"""
ModelScope 路由 — /api/ms/*, /api/angle
"""

import time
import uuid
from fastapi import APIRouter, HTTPException
from ..system.providers import get_provider
from ..core.http_client import create_client
from ..core.websocket import manager as ws_manager
from ..canvas.manager import canvas_output_dir
from ..config import (
    MODELSCOPE_CHAT_BASE_URL,
    IMAGE_TASK_TIMEOUT, IMAGE_POLL_INTERVAL, OUTPUT_DIR,
)

router = APIRouter(prefix="/api", tags=["modelscope"])


@router.post("/ms/generate")
async def ms_generate(req: dict):
    """ModelScope 图片/360 生成"""
    provider = await get_provider("modelscope")
    api_key = req.get("api_key", "") or (provider.get("api_key", "") if provider else "")
    model = req.get("model", "")
    prompt = req.get("prompt", "")
    loras = req.get("loras", [])
    image_urls = req.get("image_urls", [])
    width = req.get("width", 1024)
    height = req.get("height", 1024)
    from ..canvas.context import resolve_canvas_id
    canvas_id = resolve_canvas_id(req.get("canvas_id"), req.get("client_id"))

    api_root = MODELSCOPE_CHAT_BASE_URL.rstrip("/")
    if provider:
        api_root = provider.get("base_url", api_root).rstrip("/")

    body = {
        "model": model or "Tongyi-MAI/Z-Image-Turbo",
        "prompt": prompt,
        "n": 1,
        "size": f"{width}x{height}",
    }
    if image_urls:
        body["image_urls"] = image_urls
    if loras:
        body["loras"] = loras

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async with create_client("long") as client:
        resp = await client.post(f"{api_root}/images/generations", json=body, headers=headers)

    if resp.status_code == 200:
        return await _save_ms_result(resp.json(), canvas_id)

    # 可能返回 task_id 需要轮询
    try:
        data = resp.json()
        task_id = data.get("task_id", "")
        if not task_id:
            raise HTTPException(resp.status_code, f"ModelScope 错误: {resp.text[:300]}")
        return await _poll_ms_task(api_root, task_id, headers, canvas_id)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(resp.status_code, f"ModelScope 错误: {resp.text[:300]}")


@router.post("/angle/generate")
async def angle_generate(req: dict):
    """兼容当前画布的 Qwen Image Edit 入口。"""
    req = dict(req)
    req.setdefault("model", "Qwen/Qwen-Image-Edit-2511")
    return await ms_generate(req)


# ---- 内部 ----

async def _save_ms_result(data: dict, canvas_id: str | None) -> dict:
    urls = []
    for item in data.get("data", []):
        url = item.get("url", "")
        if url:
            local = await _download_ms_output(url, canvas_id)
            urls.append(local)

    await ws_manager.broadcast_new_image({"images": urls, "canvas_id": canvas_id})
    return {"images": urls, "type": "online", "timestamp": time.time()}


async def _download_ms_output(url: str, canvas_id: str | None) -> str:
    if not url or not url.startswith("http"):
        return url
    try:
        async with create_client("normal") as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return url
            content = resp.content

        ext = ".png"
        for e in (".png", ".jpg", ".webp"):
            if e in url.lower().split("?")[0]:
                ext = e
                break
        filename = f"ms_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}{ext}"

        if canvas_id:
            out = await canvas_output_dir(canvas_id)
            out.mkdir(parents=True, exist_ok=True)
            (out / filename).write_bytes(content)
            return f"outputs/{filename}"
        else:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            (OUTPUT_DIR / filename).write_bytes(content)
            return f"/output/{filename}"
    except Exception:
        return url


async def _poll_ms_task(api_root: str, task_id: str, headers: dict, canvas_id: str | None) -> dict:
    import asyncio
    url = f"{api_root}/images/tasks/{task_id}"
    deadline = time.time() + IMAGE_TASK_TIMEOUT

    while time.time() < deadline:
        async with create_client("normal") as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            await asyncio.sleep(IMAGE_POLL_INTERVAL)
            continue

        data = resp.json()
        status = data.get("status", "")
        if status in ("done", "succeeded", "completed", "success"):
            return await _save_ms_result(data, canvas_id)
        if status in ("failed", "error", "cancelled"):
            raise HTTPException(500, data.get("error", {}).get("message", "ModelScope 生成失败"))
        await asyncio.sleep(IMAGE_POLL_INTERVAL)

    raise HTTPException(504, "ModelScope 任务超时")
