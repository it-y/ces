"""
生成编排器 — 协议分发 → gateway → 下载 → 画布更新 → 历史 → 广播。
"""

import asyncio
import json
import time
import uuid

from ..system.providers import (
    get_provider, effective_protocol,
    is_jimeng_provider, is_runninghub_provider, is_volcengine_provider,
)
from ..core.websocket import manager as ws_manager
from ..core.http_client import create_client
from ..canvas.manager import load_canvas, save_canvas, canvas_output_dir, CanvasConflictError
from ..config import CANVAS_FILES_DIR, OUTPUT_DIR, HISTORY_PATH, HISTORY_MAX_ENTRIES
from .gateways.openai import OpenAIGateway, ImageGenerationError
from .gateways.gemini import GeminiGateway
from .gateways.volcengine import VolcengineGateway
from .gateways.modelscope import ModelScopeGateway
from .gateways.runninghub import RunningHubGateway
from .gateways.jimeng.image import JimengImageGateway

_history_lock = asyncio.Lock()


# ============================================================
# 主入口
# ============================================================

async def generate_image(
    prompt: str,
    size: str = "1024x1024",
    model: str = "",
    quality: str = "auto",
    n: int = 1,
    provider_id: str = "",
    reference_images: list | None = None,
    canvas_id: str | None = None,
    client_id: str | None = None,
) -> dict:
    if not provider_id:
        raise ValueError("请选择供应商")
    provider = await get_provider(provider_id)
    if not provider:
        raise ValueError(f"供应商 {provider_id} 不存在，请在 API 设置中配置")

    proto = effective_protocol(provider, model)
    if not model:
        model = _default_model(provider)

    # 协议分发
    urls = await _dispatch(provider, proto, prompt, size, model, quality, n, reference_images)

    # 下载到本地（失败用原 URL）
    local_urls = [await _download_or_keep(url, canvas_id) for url in urls]

    # 更新画布
    if canvas_id and local_urls:
        await _add_image_nodes_to_canvas(canvas_id, local_urls, prompt, model, provider_id)

    # 历史
    await _save_history({
        "type": "image",
        "prompt": prompt,
        "model": model,
        "provider": provider_id,
        "urls": local_urls,
        "canvas_id": canvas_id,
    })

    # WebSocket 广播
    await ws_manager.broadcast_new_image({
        "images": local_urls,
        "prompt": prompt,
        "model": model,
        "canvas_id": canvas_id,
    })

    return {
        "images": local_urls,
        "prompt": prompt,
        "model": model,
        "provider_id": provider_id,
        "timestamp": time.time(),
        "type": "online",
    }


# ============================================================
# 协议分发
# ============================================================

async def _dispatch(provider, proto, prompt, size, model, quality, n, refs) -> list[str]:
    if proto == "modelscope":
        gw = ModelScopeGateway(provider)
    elif is_jimeng_provider(provider):
        gw = JimengImageGateway()
    elif is_runninghub_provider(provider):
        gw = RunningHubGateway(provider)
    elif proto == "gemini":
        gw = GeminiGateway(provider)
    elif is_volcengine_provider(provider):
        gw = VolcengineGateway(provider)
    else:
        gw = OpenAIGateway(provider)

    return await gw.generate(prompt, size, model, quality, n, refs)


# ============================================================
# 下载 & 存储
# ============================================================

async def _download_or_keep(url: str, canvas_id: str | None = None) -> str:
    """
    下载远程/数据 URL。
    如果 canvas_id 已知则写入 per-canvas outputs/，否则写全局 OUTPUT_DIR。
    """
    if not url:
        return url

    # 已在本地的路径，直接返回
    if url.startswith("/assets/") or url.startswith("/output/") or url.startswith("/cfiles/"):
        return url

    content = None
    ext = ".png"

    # data: URL → 解码
    if url.startswith("data:"):
        try:
            import base64
            meta, encoded = url.split(",", 1)
            content = base64.b64decode(encoded)
            if "jpeg" in meta or "jpg" in meta:
                ext = ".jpg"
            elif "webp" in meta:
                ext = ".webp"
            elif "gif" in meta:
                ext = ".gif"
        except Exception:
            return url

    # 远程 HTTP URL → 下载（带重试）
    elif url.startswith("http"):
        async with create_client("normal") as client:
            for attempt in range(3):
                try:
                    ext = _guess_ext(url)
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        if attempt < 2:
                            await asyncio.sleep(2 ** attempt)
                            continue
                        return url
                    content = resp.content
                    break
                except Exception:
                    if attempt < 2:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    return url

    if content is None:
        return url

    filename = f"gen_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}{ext}"

    # 有 canvas_id → 写入 per-canvas outputs/，不写全局
    if canvas_id:
        out_dir = await canvas_output_dir(canvas_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread((out_dir / filename).write_bytes, content)
        rel = out_dir.relative_to(CANVAS_FILES_DIR)
        return f"/cfiles/{rel.as_posix()}/{filename}"

    # 无 canvas_id 或 per-canvas 写入失败 → 全局 OUTPUT_DIR
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread((OUTPUT_DIR / filename).write_bytes, content)
    return f"/output/{filename}"


def _guess_ext(url: str) -> str:
    url_lower = url.split("?")[0]
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm"):
        if ext in url_lower:
            return ext
    return ".png"


# ============================================================
# 画布节点更新（带乐观锁重试）
# ============================================================

async def _add_image_nodes_to_canvas(
    canvas_id: str, urls: list[str], prompt: str, model: str, provider_id: str,
):
    """在画布上添加生成图片节点，乐观锁冲突自动重试"""
    for attempt in range(3):
        try:
            canvas = await load_canvas(canvas_id)
        except FileNotFoundError:
            return

        nodes = list(canvas.get("nodes", []))
        for url in urls:
            nodes.append({
                "id": f"node_{uuid.uuid4().hex[:8]}",
                "type": "image",
                "x": 100 + len(nodes) * 50,
                "y": 100 + len(nodes) * 50,
                "width": 512,
                "height": 512,
                "data": {
                    "url": url,
                    "prompt": prompt,
                    "model": model,
                    "provider": provider_id,
                },
            })

        try:
            await save_canvas(
                canvas_id, nodes=nodes,
                base_updated_at=canvas.get("updated_at"),
            )
            return
        except CanvasConflictError:
            if attempt >= 2:
                return  # 重试耗尽，放弃
            await asyncio.sleep(0.1 * (attempt + 1))  # 指数退避，避免并发冲突风暴


# ============================================================
# 历史
# ============================================================

async def _save_history(record: dict):
    """追加生成记录（并发安全 + 原子写）"""
    try:
        import tempfile, os, json
        async with _history_lock:
            HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
            history = []

            def _read():
                if HISTORY_PATH.exists():
                    try:
                        return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
                    except Exception:
                        return []
                return []

            history = await asyncio.to_thread(_read)
            record.setdefault("timestamp", int(time.time() * 1000))
            history.insert(0, record)
            history = history[:HISTORY_MAX_ENTRIES]

            def _write():
                dirname = str(HISTORY_PATH.parent)
                fd, tmp = tempfile.mkstemp(suffix=".json", prefix=".tmp_", dir=dirname)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump(history, f, ensure_ascii=True, indent=2)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp, str(HISTORY_PATH))
                except Exception:
                    if os.path.exists(tmp):
                        os.remove(tmp)

            await asyncio.to_thread(_write)
    except Exception:
        pass


# ============================================================
# 工具
# ============================================================

def _default_model(provider: dict) -> str:
    models = provider.get("image_models", [])
    return models[0] if models else "gpt-image-2"


# ============================================================
# 视频生成
# ============================================================

async def generate_video(
    prompt: str,
    provider_id: str = "",
    model: str = "",
    duration: int = 5,
    aspect_ratio: str = "16:9",
    resolution: str = "720p",
    size: str = "",
    images: list | None = None,
    videos: list | None = None,
    audios: list | None = None,
    canvas_id: str | None = None,
    client_id: str | None = None,
    **kwargs,
) -> dict:
    if not provider_id:
        raise ValueError("请选择供应商")
    provider = await get_provider(provider_id)
    if not provider:
        raise ValueError(f"供应商 {provider_id} 不存在，请在 API 设置中配置")

    proto = effective_protocol(provider, model)
    if not model:
        vmodels = provider.get("video_models", [])
        model = vmodels[0] if vmodels else "veo2"

    # Jimeng 走独立视频网关
    if is_jimeng_provider(provider):
        from .gateways.jimeng.video import JimengVideoGateway
        gw = JimengVideoGateway()
        urls = await gw.generate(
            prompt, model=model, duration=duration,
            aspect_ratio=aspect_ratio, resolution=resolution,
            images=images, videos=videos, audios=audios,
            **kwargs,
        )
    # RunningHub 视频
    elif is_runninghub_provider(provider):
        gw = RunningHubGateway(provider)
        urls = await gw.generate(prompt, model=model, reference_images=images)
    # 火山方舟视频
    elif is_volcengine_provider(provider):
        gw = VolcengineGateway(provider)
        urls = await gw.generate(prompt, model=model)
    else:
        # OpenAI 兼容视频
        gw = OpenAIGateway(provider)
        urls = await gw.generate(prompt, model=model)

    # 下载到本地
    local_urls = []
    for url in urls:
        local = await _download_or_keep(url, canvas_id)
        local_urls.append(local)

    # 记录历史 & 广播
    await _save_history({
        "type": "video", "prompt": prompt, "model": model,
        "provider": provider_id, "urls": local_urls, "canvas_id": canvas_id,
    })
    await ws_manager.broadcast_new_image({
        "images": local_urls, "prompt": prompt, "model": model, "canvas_id": canvas_id,
    })

    return {
        "images": local_urls,
        "prompt": prompt,
        "model": model,
        "provider_id": provider_id,
        "timestamp": time.time(),
        "type": "video",
    }
