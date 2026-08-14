"""
生成路由 — /api/online-image, /api/canvas-image-tasks, /api/canvas-video, /api/canvas-llm, /api/generate 等。
"""

import asyncio
import time
import uuid
from fastapi import APIRouter, HTTPException

from .models import (
    OnlineImageRequest, ImageTaskQueryRequest,
    CanvasVideoRequest, CanvasLLMRequest,
    ComfyGenerateRequest,
)
from .orchestrator import generate_image, generate_video
from ..comfyui.scheduler import scheduler
from .gateways.openai import ImageGenerationError
router = APIRouter(prefix="/api", tags=["generation"])

# ============================================================
# 画布任务系统（文件持久化，服务重启不丢任务）
# ============================================================
import json
import os
from pathlib import Path

_TASKS_FILE = Path("data/canvas_tasks.json")

CANVAS_TASKS: dict = {}
_task_lock = asyncio.Lock()
TASK_TTL_MS = 7 * 24 * 60 * 60 * 1000  # 7 天


def _load_tasks():
    """启动时从文件加载持久化任务，恢复中断任务并清理过期任务"""
    global CANVAS_TASKS
    try:
        if _TASKS_FILE.exists():
            raw = _TASKS_FILE.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict):
                CANVAS_TASKS = data
    except Exception:
        CANVAS_TASKS = {}
    now_s = time.time()
    ttl_s = TASK_TTL_MS / 1000
    changed = False
    for tid in list(CANVAS_TASKS):
        task = CANVAS_TASKS[tid]
        created = task.get("created_at", 0)
        if now_s - created > ttl_s:
            del CANVAS_TASKS[tid]
            changed = True
        elif task.get("status") in ("queued", "running"):
            task["status"] = "interrupted"
            task["updated_at"] = now_s
            changed = True
    if changed:
        _save_tasks()


def _cleanup_old_tasks():
    """移除过期任务（超过 TASK_TTL_MS）"""
    now_s = time.time()
    ttl_s = TASK_TTL_MS / 1000
    for tid in list(CANVAS_TASKS):
        created = CANVAS_TASKS[tid].get("created_at", 0)
        if now_s - created > ttl_s:
            del CANVAS_TASKS[tid]
    _save_tasks()


def _save_tasks():
    """异步任务状态变更后写回文件"""
    try:
        _TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _TASKS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(CANVAS_TASKS, ensure_ascii=True, default=str), encoding="utf-8")
        tmp.replace(_TASKS_FILE)
    except Exception:
        pass


# 模块加载时自动恢复任务状态
_load_tasks()


async def _run_canvas_image_task(task_id: str, payload: OnlineImageRequest):
    """后台执行图片生成，完成后更新任务状态"""
    import logging
    log = logging.getLogger("routes")
    log.info("_run_canvas_image_task canvas_id=%s", payload.canvas_id)
    async with _task_lock:
        if task_id in CANVAS_TASKS:
            CANVAS_TASKS[task_id]["status"] = "running"
            CANVAS_TASKS[task_id]["updated_at"] = time.time()
            _save_tasks()
    # 看门狗超时按协议区分：异步轮询型（apimart/runninghub/modelscope 等）任务
    # 常需要排队等待，给足与轮询预算一致的时间，避免慢任务被总超时误砍。
    timeout = await _task_timeout_for(payload.provider_id)
    try:
        # 任务级看门狗：上游接口挂起（不返回响应）时，超时后标记失败，
        # 否则前端会无限等待、计时器一直累加
        result = await asyncio.wait_for(
            generate_image(
                prompt=payload.prompt, size=payload.size, model=payload.model,
                quality=payload.quality, n=payload.n, provider_id=payload.provider_id,
                reference_images=[ref.model_dump() for ref in payload.reference_images] if payload.reference_images else None,
                canvas_id=payload.canvas_id, client_id=payload.client_id,
            ),
            timeout=timeout,
        )
        async with _task_lock:
            CANVAS_TASKS[task_id].update({
                "status": "succeeded",
                "result": result,
                "error": "",
                "updated_at": time.time(),
            })
            _save_tasks()
    except asyncio.TimeoutError:
        detail = f"图片生成超时（{int(timeout)} 秒内未完成）。可能是上游接口无响应，请重试；若持续失败请更换模型或供应商。"
        log.warning("canvas image task %s timed out", task_id)
        async with _task_lock:
            CANVAS_TASKS[task_id].update({
                "status": "failed",
                "error": detail,
                "updated_at": time.time(),
            })
            _save_tasks()
    except Exception as exc:
        detail = str(exc)
        async with _task_lock:
            CANVAS_TASKS[task_id].update({
                "status": "failed",
                "error": detail,
                "updated_at": time.time(),
            })
            _save_tasks()


async def _task_timeout_for(provider_id: str) -> float:
    """按协议返回看门狗超时（秒）。异步轮询型给长超时，同步型给默认 300s。"""
    try:
        from ..system.providers import get_provider
        from ..config import CANVAS_IMAGE_TASK_TIMEOUT, APIMART_IMAGE_TASK_TIMEOUT, IMAGE_TASK_TIMEOUT
        if not provider_id:
            return CANVAS_IMAGE_TASK_TIMEOUT
        provider = _provider_cache.get(provider_id)
        if provider is None:
            provider = await get_provider(provider_id)
            if provider:
                _provider_cache[provider_id] = provider
        if not provider:
            return CANVAS_IMAGE_TASK_TIMEOUT
        from ..system.providers import (
            is_apimart_provider, is_runninghub_provider, is_modelscope_provider,
            is_volcengine_provider, is_jimeng_provider,
        )
        if any(fn(provider) for fn in (
            is_apimart_provider, is_runninghub_provider,
            is_modelscope_provider, is_jimeng_provider,
        )):
            return max(CANVAS_IMAGE_TASK_TIMEOUT, APIMART_IMAGE_TASK_TIMEOUT)
        if is_volcengine_provider(provider):
            return max(CANVAS_IMAGE_TASK_TIMEOUT, IMAGE_TASK_TIMEOUT)
        return CANVAS_IMAGE_TASK_TIMEOUT
    except Exception:
        return CANVAS_IMAGE_TASK_TIMEOUT


_provider_cache: dict = {}


@router.post("/online-image")
async def api_generate_online_image(req: OnlineImageRequest):
    """在线图片生成 — 支持 6 种 AI 协议"""
    try:
        from ..canvas.context import resolve_canvas_id
        resolved_canvas_id = resolve_canvas_id(req.canvas_id, req.client_id)
        result = await generate_image(
            prompt=req.prompt, size=req.size, model=req.model,
            quality=req.quality, n=req.n, provider_id=req.provider_id,
            reference_images=[ref.model_dump() for ref in req.reference_images] if req.reference_images else None,
            canvas_id=resolved_canvas_id, client_id=req.client_id,
        )
        return result
    except ImageGenerationError as e:
        raise HTTPException(e.status_code, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/canvas-image-tasks")
async def api_create_canvas_image_task(req: OnlineImageRequest):
    """创建画布图片生成任务（异步，后台执行）"""
    import logging
    from ..canvas.context import resolve_canvas_id
    req.canvas_id = resolve_canvas_id(req.canvas_id, req.client_id)
    logging.getLogger("routes").info("canvas-image-tasks body: prompt=%s canvas_id=%s", req.prompt[:50], req.canvas_id)
    task_id = f"canvas_img_{uuid.uuid4().hex}"
    async with _task_lock:
        CANVAS_TASKS[task_id] = {
            "id": task_id,
            "type": "online-image",
            "status": "queued",
            "created_at": time.time(),
            "updated_at": time.time(),
            "result": None,
            "error": "",
            "provider_id": req.provider_id,
            "model": req.model,
        }
        _save_tasks()
    asyncio.create_task(_run_canvas_image_task(task_id, req))
    return {"task_id": task_id, "status": "queued"}


@router.get("/canvas-image-tasks/{task_id}")
async def api_get_canvas_image_task(task_id: str):
    """查询画布图片生成任务状态"""
    async with _task_lock:
        task = dict(CANVAS_TASKS.get(task_id) or {})
    if not task:
        raise HTTPException(status_code=404, detail="画布任务不存在，可能服务已重启或任务已过期")
    return task


@router.post("/ai/generate")
async def api_ai_generate(req: OnlineImageRequest):
    return await api_generate_online_image(req)


@router.post("/image-task-query")
async def api_query_image_task(req: ImageTaskQueryRequest):
    """查询异步图片任务（Apimart 等异步协议的状态查询）"""
    from ..system.providers import get_provider
    provider = await get_provider(req.provider_id)
    if not provider:
        raise HTTPException(400, f"供应商 {req.provider_id} 不存在")

    base = provider.get("base_url", "").rstrip("/")
    from ..core.http_client import create_client
    async with create_client("normal") as client:
        resp = await client.get(f"{base}/v1/images/tasks/{req.task_id}")
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, f"查询失败: {resp.text[:200]}")
    return resp.json()


@router.post("/canvas-video")
async def api_canvas_video(req: CanvasVideoRequest):
    """Canvas 视频生成 — 支持 OpenAI/火山/RunningHub/即梦等"""
    try:
        from ..canvas.context import resolve_canvas_id
        resolved_canvas_id = resolve_canvas_id(req.canvas_id, req.client_id)
        result = await generate_video(
            prompt=req.prompt, model=req.model, provider_id=req.provider_id,
            duration=req.duration, aspect_ratio=req.aspect_ratio,
            resolution=req.resolution, size=req.size,
            images=[ref.model_dump() for ref in req.images] if req.images else None,
            videos=[ref.model_dump() for ref in req.videos] if req.videos else None,
            audios=[ref.model_dump() for ref in req.audios] if req.audios else None,
            enhance_prompt=req.enhance_prompt, enable_upsample=req.enable_upsample,
            watermark=req.watermark, seed=req.seed, camerafixed=req.camerafixed,
            return_last_frame=req.return_last_frame, generate_audio=req.generate_audio,
            multimodal=req.multimodal, trusted_asset=req.trusted_asset,
            canvas_id=resolved_canvas_id, client_id=req.client_id,
        )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except ImageGenerationError as e:
        raise HTTPException(e.status_code, str(e))
    except NotImplementedError as e:
        raise HTTPException(501, str(e))


@router.post("/canvas-llm")
async def api_canvas_llm(req: CanvasLLMRequest):
    """Canvas LLM — 画布上的 LLM 节点"""
    from ..system.providers import get_provider, effective_protocol, chat_api_url, preferred_chat_model
    from ..core.http_client import create_client
    from ..config import MODELSCOPE_CHAT_BASE_URL, OUTPUT_DIR, UPLOAD_DIR

    from ..canvas.context import resolve_canvas_id
    req.canvas_id = resolve_canvas_id(req.canvas_id, None)
    if not req.provider:
        raise HTTPException(400, "请选择 LLM 供应商")
    provider = await get_provider(req.provider)
    if not provider:
        raise HTTPException(400, f"供应商 {req.provider} 不存在，请在 API 设置中配置")

    chat_models = provider.get("chat_models") or []
    model = preferred_chat_model(provider, req.model) or (chat_models[0] if chat_models else "gpt-4o-mini")
    messages = req.messages or [{"role": "user", "content": req.message}]

    # 将相对路径图片转为 base64（外部 LLM 访问不了 localhost）
    import base64, urllib.parse
    from io import BytesIO
    from ..config import OUTPUT_DIR, UPLOAD_DIR, CANVAS_FILES_DIR
    async def _resolve_image(url: str, max_size: int = 1024) -> str:
        if url.startswith("data:") or url.startswith("http://") or url.startswith("https://"):
            return url
        clean = urllib.parse.unquote(url.split("?", 1)[0]).replace("\\", "/")
        if clean.startswith("/cfiles/"):
            root = CANVAS_FILES_DIR; rel = clean[len("/cfiles/"):]
        elif clean.startswith("/assets/"):
            root = UPLOAD_DIR; rel = clean[len("/assets/"):]
        elif clean.startswith("/output/"):
            root = OUTPUT_DIR; rel = clean[len("/output/"):]
        else:
            return url
        rel = rel.lstrip("/")
        if not rel: return url
        path = (root / rel).resolve()
        try:
            root_abs = root.resolve(); path.relative_to(root_abs)
        except ValueError:
            return url
        if not path.is_file(): return url
        raw = await asyncio.to_thread(path.read_bytes)
        try:
            from PIL import Image
            img = Image.open(BytesIO(raw))
            img.load()
            w, h = img.size
            if max(w, h) > max_size:
                img.thumbnail((max_size, max_size), Image.LANCZOS)
            has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
            fmt = "PNG" if has_alpha else "JPEG"
            mime = "image/png" if fmt == "PNG" else "image/jpeg"
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA" if has_alpha else "RGB")
            buf = BytesIO()
            img.save(buf, format=fmt, quality=88 if fmt == "JPEG" else None)
            encoded = base64.b64encode(buf.getvalue()).decode("ascii")
            return f"data:{mime};base64,{encoded}"
        except Exception:
            mime = "image/png"
            return f"data:{mime};base64,{base64.b64encode(raw).decode()}"

    if req.images or req.videos:
        content_parts = [{"type": "text", "text": req.message}]
        for url in (req.images or []):
            resolved = await _resolve_image(url)
            content_parts.append({"type": "image_url", "image_url": {"url": resolved}})
        for url in (req.videos or []):
            resolved = await _resolve_image(url)
            content_parts.append({"type": "image_url", "image_url": {"url": resolved}})
        messages = [{"role": "user", "content": content_parts}]

    # ModelScope 特殊路由
    if provider.get("id") == "modelscope" and req.ms_model:
        model = req.ms_model

    api_key = provider.get("api_key", "")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    # 使用协议感知的 URL 构建
    chat_url = chat_api_url(provider)
    if not chat_url:
        raise HTTPException(400, "LLM 供应商未配置 base_url，请在 API 设置中填写")

    last_error = None
    data = None
    async with create_client("long") as client:
        for attempt in range(3):
            try:
                resp = await client.post(
                    chat_url,
                    json={"model": model, "messages": messages},
                    headers=headers,
                )
                if resp.status_code >= 500:
                    last_error = f"LLM 服务端错误 ({resp.status_code}): {resp.text[:200]}"
                    if attempt < 2:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    raise HTTPException(resp.status_code, last_error)
                if resp.status_code != 200:
                    raise HTTPException(resp.status_code, f"LLM 调用失败: {resp.text[:200]}")
                data = resp.json()
                break
            except HTTPException:
                raise
            except Exception as e:
                last_error = str(e)
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue
                import traceback
                detail = f"LLM 请求异常（重试 3 次后仍失败）: {e}"
                try:
                    detail += f"\nURL: {chat_url}"
                    detail += f"\nModel: {model}"
                except Exception:
                    pass
                print(f"[LLM ERROR] {detail}\n{traceback.format_exc()}")
                raise HTTPException(502, detail)

    if data is None:
        raise HTTPException(502, f"LLM 请求失败（重试 3 次后仍无响应）: {last_error}")

    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")

    # 如果有 canvas_id，在画布上添加文本节点
    if req.canvas_id and content:
        import uuid
        from ..canvas.manager import load_canvas, save_canvas
        try:
            canvas = await load_canvas(req.canvas_id)
            nodes = canvas.get("nodes", [])
            nodes.append({
                "id": f"node_{uuid.uuid4().hex[:8]}",
                "type": "text",
                "x": 200, "y": 200,
                "width": 400, "height": 200,
                "data": {"text": content, "model": model},
            })
            await save_canvas(req.canvas_id, nodes=nodes)
        except Exception:
            pass

    return {"content": content, "model": model}


# ---- SSE 流式 LLM 端点 ----

def _sse_event(data: dict) -> str:
    """构造 SSE 事件字符串。"""
    import json as _json
    return f"data: {_json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/canvas-llm-stream")
async def api_canvas_llm_stream(req: CanvasLLMRequest):
    """
    Canvas LLM 流式端点 — 返回 text/event-stream。
    SSE 事件类型：meta / delta / done / error
    """
    from fastapi.responses import StreamingResponse
    from ..system.providers import get_provider, is_apimart_provider, chat_api_url, preferred_chat_model
    from ..core.http_client import create_client
    from ..config import MODELSCOPE_CHAT_BASE_URL, OUTPUT_DIR, UPLOAD_DIR

    from ..canvas.context import resolve_canvas_id
    req.canvas_id = resolve_canvas_id(req.canvas_id, None)

    if not req.provider:
        raise HTTPException(400, "请选择 LLM 供应商")
    provider = await get_provider(req.provider)
    if not provider:
        raise HTTPException(400, f"供应商 {req.provider} 不存在，请在 API 设置中配置")

    chat_models = provider.get("chat_models") or []
    model = preferred_chat_model(provider, req.model) or (chat_models[0] if chat_models else "gpt-4o-mini")
    messages = req.messages or [{"role": "user", "content": req.message}]

    # Apimart 聊天不支持 stream
    if is_apimart_provider(provider):
        # 降级为非流式并复用现有端点
        result = await api_canvas_llm(req)
        return result

    # ModelScope 特殊路由
    if provider.get("id") == "modelscope" and req.ms_model:
        model = req.ms_model

    api_key = provider.get("api_key", "")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    chat_url = chat_api_url(provider)
    if not chat_url:
        raise HTTPException(400, "LLM 供应商未配置 base_url，请在 API 设置中填写")

    # 将本地图片转为 base64
    resolved_messages = await _resolve_images_for_llm(messages, req)

    async def stream():
        content_parts = []
        yield _sse_event({"type": "meta", "model": model, "provider": req.provider})
        try:
            async with create_client("long") as client:
                payload = {"model": model, "messages": resolved_messages, "stream": True}
                async with client.stream("POST", chat_url, headers=headers, json=payload) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        text = body.decode("utf-8", errors="ignore")
                        from ..core.errors import friendly_chat_error_detail
                        friendly = friendly_chat_error_detail(text, model, req.provider)
                        yield _sse_event({"type": "error", "detail": friendly or f"上游接口错误：{text[:300]}"})
                        return
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        if line.startswith("data:"):
                            line = line[5:].strip()
                        if line == "[DONE]":
                            break
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        text_delta = delta.get("content", "")
                        if text_delta:
                            content_parts.append(text_delta)
                            yield _sse_event({"type": "delta", "delta": text_delta})
        except Exception as exc:
            yield _sse_event({"type": "error", "detail": f"请求上游接口失败：{exc}"})
            return

        full_text = "".join(content_parts).strip()
        if not full_text:
            full_text = "接口返回了空回复。"

        # 添加画布文本节点
        if req.canvas_id and full_text:
            try:
                from ..canvas.manager import load_canvas, save_canvas
                canvas = await load_canvas(req.canvas_id)
                nodes = canvas.get("nodes", [])
                nodes.append({
                    "id": f"node_{uuid.uuid4().hex[:8]}",
                    "type": "text",
                    "x": 200, "y": 200,
                    "width": 400, "height": 200,
                    "data": {"text": full_text, "model": model},
                })
                await save_canvas(req.canvas_id, nodes=nodes)
            except Exception:
                pass

        yield _sse_event({"type": "done", "content": full_text, "model": model})

    return StreamingResponse(stream(), media_type="text/event-stream")


async def _resolve_images_for_llm(messages: list, req) -> list:
    """将消息中的本地图片路径转为 base64 data URL。"""
    import base64 as _b64
    from io import BytesIO
    from ..config import OUTPUT_DIR, UPLOAD_DIR, CANVAS_FILES_DIR

    resolved = []
    for msg in messages:
        if isinstance(msg.get("content"), list):
            parts = []
            for part in msg["content"]:
                if part.get("type") == "image_url":
                    url = part.get("image_url", {}).get("url", "")
                    resolved_url = url
                    if url and not url.startswith(("data:", "http://", "https://")):
                        resolved_url = await _local_image_to_data(url, UPLOAD_DIR, OUTPUT_DIR, CANVAS_FILES_DIR)
                    parts.append({"type": "image_url", "image_url": {"url": resolved_url}})
                else:
                    parts.append(part)
            resolved.append({"role": msg.get("role", "user"), "content": parts})
        else:
            resolved.append(msg)
    return resolved


async def _local_image_to_data(url: str, upload_dir, output_dir, canvas_files_dir) -> str:
    """将本地路径转为 base64 data URL。"""
    import base64 as _b64, urllib.parse
    from io import BytesIO
    from pathlib import Path as _Path

    clean = urllib.parse.unquote(url.split("?", 1)[0]).replace("\\", "/")
    if clean.startswith("/cfiles/"):
        root, rel = canvas_files_dir, clean[len("/cfiles/"):]
    elif clean.startswith("/assets/"):
        root, rel = upload_dir, clean[len("/assets/"):]
    elif clean.startswith("/output/"):
        root, rel = output_dir, clean[len("/output/"):]
    else:
        return url
    rel = rel.lstrip("/")
    if not rel:
        return url
    path = _Path(root) / rel
    try:
        path.relative_to(_Path(root).resolve())
    except ValueError:
        return url
    if not path.is_file():
        return url

    raw = path.read_bytes()
    try:
        from PIL import Image
        img = Image.open(BytesIO(raw))
        img.load()
        w, h = img.size
        max_size = 1024
        if max(w, h) > max_size:
            img.thumbnail((max_size, max_size), Image.LANCZOS)
        has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
        fmt = "PNG" if has_alpha else "JPEG"
        mime = "image/png" if fmt == "PNG" else "image/jpeg"
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if has_alpha else "RGB")
        buf = BytesIO()
        img.save(buf, format=fmt, quality=88 if fmt == "JPEG" else None)
        encoded = _b64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:{mime};base64,{encoded}"
    except Exception:
        return f"data:image/png;base64,{_b64.b64encode(raw).decode()}"
async def api_create_canvas_comfy_task(req: ComfyGenerateRequest):
    """创建 ComfyUI 画布任务（异步）"""
    task_id = f"canvas_comfy_{uuid.uuid4().hex}"
    async with _task_lock:
        CANVAS_TASKS[task_id] = {
            "id": task_id,
            "type": "comfy",
            "status": "queued",
            "created_at": time.time(),
            "updated_at": time.time(),
            "result": None,
            "error": "",
            "workflow_json": req.workflow_json,
        }
        _save_tasks()
    asyncio.create_task(_run_canvas_comfy_task(task_id, req))
    return {"task_id": task_id, "status": "queued"}


@router.get("/canvas-comfy-tasks/{task_id}")
async def api_get_canvas_comfy_task(task_id: str):
    """查询 ComfyUI 画布任务状态"""
    async with _task_lock:
        task = dict(CANVAS_TASKS.get(task_id) or {})
    if not task:
        raise HTTPException(status_code=404, detail="ComfyUI 任务不存在，可能服务已重启或任务已过期")
    return task


async def _run_canvas_comfy_task(task_id: str, req: ComfyGenerateRequest):
    """后台执行 ComfyUI 生成"""
    async with _task_lock:
        if task_id in CANVAS_TASKS:
            CANVAS_TASKS[task_id]["status"] = "running"
            CANVAS_TASKS[task_id]["updated_at"] = time.time()
            _save_tasks()
    try:
        result = await scheduler.submit_workflow(req.workflow_json, req.params)
        async with _task_lock:
            CANVAS_TASKS[task_id].update({
                "status": "succeeded",
                "result": result,
                "error": "",
                "updated_at": time.time(),
            })
            _save_tasks()
    except Exception as exc:
        detail = str(exc)
        async with _task_lock:
            CANVAS_TASKS[task_id].update({
                "status": "failed",
                "error": detail,
                "updated_at": time.time(),
            })
            _save_tasks()


@router.post("/generate")
async def api_comfy_generate(req: ComfyGenerateRequest):
    """ComfyUI 本地生成（同步）"""
    prompt_id = await scheduler.submit_workflow(req.workflow_json, req.params)
    return {"prompt_id": prompt_id, "status": "submitted"}
