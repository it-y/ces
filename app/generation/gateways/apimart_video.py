"""
APIMart 视频生成 — VEO 3.1 / Seedance body 格式 + 媒体上传 + 轮询。

从原版 main.py:6398-6570 / 11380-11563 迁移重构。
"""

import asyncio
import base64
import mimetypes
import time
from io import BytesIO
from typing import Optional


# ---- 常量 ----

VIDEO_POLL_TIMEOUT = 1800  # 30分钟

VIDEO_TASK_SUCCESS_STATUSES = {
    "SUCCESS", "SUCCEED", "SUCCEEDED", "COMPLETED", "COMPLETE",
    "DONE", "FINISHED", "FINISH", "OK", "READY",
}
VIDEO_TASK_FAILURE_STATUSES = {
    "FAILURE", "FAILED", "FAIL", "ERROR", "ERRORED",
    "CANCELED", "CANCELLED", "TIMEOUT", "TIMEDOUT", "REJECTED", "EXPIRED",
}

VIDEO_URL_KEYS = (
    "video_url", "video_urls", "url", "output_url", "output_urls",
    "download_url", "download_urls", "cdn_url", "cdn_urls",
    "result_url", "result_urls", "play_url", "play_urls",
    "preview_url", "preview_urls", "stream_url", "stream_urls",
)


# ---- VEO 3.1 模型映射 ----

def is_veo31_model(model: str) -> bool:
    return str(model or "").strip().lower().startswith("veo3.1")


def veo31_model(model: str) -> str:
    value = str(model or "").strip().lower()
    aliases = {
        "veo3.1": "veo3.1-fast",
        "veo3.1-pro": "veo3.1-quality",
        "veo3.1-preview": "veo3.1-fast",
    }
    value = aliases.get(value, value or "veo3.1-fast")
    allowed = {"veo3.1-fast", "veo3.1-quality", "veo3.1-lite"}
    return value if value in allowed else "veo3.1-fast"


def veo31_duration(duration) -> int:
    try:
        value = int(duration)
    except Exception:
        value = 8
    return max(4, min(8, value))


def veo31_aspect(aspect: str) -> str:
    value = str(aspect or "16:9").strip()
    return value if value in {"16:9", "9:16"} else "16:9"


def veo31_resolution(resolution: str) -> str:
    value = str(resolution or "").strip().lower()
    aliases = {"": "720p", "auto": "720p", "480p": "720p", "780p": "720p", "1080": "1080p", "4k": "4k"}
    value = aliases.get(value, value)
    return value if value in {"720p", "1080p", "4k"} else "720p"


# ---- Seedance 通用 ----

def video_duration(duration) -> int:
    try:
        value = int(duration)
    except Exception:
        value = 5
    return max(4, min(15, value))


def video_size(size) -> str:
    value = str(size or "16:9").strip()
    if value == "keep_ratio":
        return "adaptive"
    allowed = {"16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "adaptive"}
    return value if value in allowed else "16:9"


# ---- 媒体上传 ----

def valid_apimart_media_url(url: str) -> bool:
    url = str(url or "").strip()
    return bool(url and (
        url.startswith("http://") or url.startswith("https://") or url.startswith("asset://")
    ))


def extract_apimart_asset_url(payload) -> str:
    """从上传响应递归提取可用的 asset URL。"""
    if isinstance(payload, list):
        for item in payload:
            found = extract_apimart_asset_url(item)
            if found:
                return found
        return ""
    if not isinstance(payload, dict):
        return ""
    for key in ("url", "asset_url", "assetUrl", "uri", "file_url", "fileUrl"):
        value = str(payload.get(key) or "").strip()
        if valid_apimart_media_url(value):
            return value
    for key in ("asset_id", "assetId", "file_id", "fileId", "id"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value if value.startswith("asset://") else f"asset://{value}"
    for key in ("data", "file", "asset", "result"):
        found = extract_apimart_asset_url(payload.get(key))
        if found:
            return found
    return ""


def upload_file_payload(path: str):
    """读取本地文件并返回 (filename, bytes, content_type)，超 10MB 自动压缩。"""
    from PIL import Image
    max_bytes = 9_500_000
    size = path.stat().st_size if hasattr(path, 'stat') else len(open(path, 'rb').read())
    if size <= max_bytes:
        with open(path, "rb") as fh:
            return path.name if hasattr(path, 'name') else "file", fh.read(), _content_type(path)
    with Image.open(path) as img:
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        quality = 92
        while quality >= 62:
            buf = BytesIO()
            bg.save(buf, format="JPEG", quality=quality, optimize=True)
            data = buf.getvalue()
            if len(data) <= max_bytes:
                name = str(path).rsplit(".", 1)[0].split("/")[-1].split("\\")[-1]
                return f"{name}.jpg", data, "image/jpeg"
            quality -= 8
    raise ValueError("图片超过 10MB，且压缩后仍无法满足 APIMart 限制")


def upload_payload_from_bytes(data: bytes, mime: str, name_hint: str = "image"):
    """把内存中的图片字节压缩到 10MB 以内。"""
    from PIL import Image
    ext = mimetypes.guess_extension(mime or "image/png") or ".png"
    if len(data) <= 9_500_000 and (mime or "").lower() in ("image/png", "image/jpeg", "image/webp"):
        return f"{name_hint}{ext}", data, (mime or "image/png")
    with Image.open(BytesIO(data)) as img:
        has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
        if has_alpha:
            base = img.convert("RGBA")
            bg = Image.new("RGB", base.size, (255, 255, 255))
            bg.paste(base, mask=base.split()[-1])
            target = bg
        else:
            target = img.convert("RGB")
        quality = 92
        while quality >= 62:
            buf = BytesIO()
            target.save(buf, format="JPEG", quality=quality, optimize=True)
            payload_data = buf.getvalue()
            if len(payload_data) <= 9_500_000:
                return f"{name_hint}.jpg", payload_data, "image/jpeg"
            quality -= 8
    raise ValueError("data URL 图片超过 10MB，且压缩后仍无法满足 APIMart 限制")


async def upload_image_for_apimart(client, provider: dict, ref_url: str) -> str:
    """上传本地/Data URL 图片到 APIMart，返回网络可用 URL。"""
    from ...system.providers import is_apimart_provider
    from pathlib import Path

    ref_url = str(ref_url or "").strip()
    if not ref_url:
        return "ERR:空地址"

    if ref_url.startswith("http://") or ref_url.startswith("https://") or ref_url.startswith("asset://"):
        return ref_url

    base_url = (provider.get("base_url") or "").rstrip("/")
    upload_url = f"{base_url}/v1/uploads/images"

    if ref_url.startswith("data:"):
        try:
            if ";base64," not in ref_url:
                return "ERR:不支持的 data URL"
            header, encoded = ref_url.split(";base64,", 1)
            mime = header.split(":", 1)[1].split(";", 1)[0] if ":" in header else "image/png"
            raw = base64.b64decode(encoded)
            filename, content, ct = upload_payload_from_bytes(raw, mime, name_hint="canvas_image")
            resp = await _upload_post(client, upload_url, provider, (filename, content, ct))
            if resp.status_code in (200, 201):
                rj = resp.json()
                url = extract_apimart_asset_url(rj)
                if valid_apimart_media_url(url):
                    return url
                return "ERR:APIMart 上传响应未包含可用 URL"
            return f"ERR:APIMart 上传失败({resp.status_code})"
        except ValueError as e:
            return f"ERR:{e}"
        except Exception as e:
            return f"ERR:上传异常 {e}"

    if ref_url.startswith("/output/") or ref_url.startswith("/assets/"):
        # 解析本地路径
        from ...config import UPLOAD_DIR, OUTPUT_DIR
        clean = ref_url.split("?")[0]
        if clean.startswith("/assets/"):
            local = Path(UPLOAD_DIR) / clean[len("/assets/"):]
        else:
            local = Path(OUTPUT_DIR) / clean[len("/output/"):]
        if not local.exists():
            return "ERR:本地文件不存在或已被删除"
        try:
            filename, content, ct = upload_file_payload(str(local))
            resp = await _upload_post(client, upload_url, provider, (filename, content, ct))
            if resp.status_code in (200, 201):
                rj = resp.json()
                url = extract_apimart_asset_url(rj)
                if valid_apimart_media_url(url):
                    return url
                return "ERR:APIMart 上传响应未包含可用 URL"
            return f"ERR:APIMart 上传失败({resp.status_code})"
        except ValueError as e:
            return f"ERR:{e}"
        except Exception as e:
            return f"ERR:上传异常 {e}"

    return "ERR:不支持的图片来源"


async def _upload_post(client, upload_url, provider, file_tuple, timeout=60):
    """带 TLS 重试的上传请求。"""
    from ...core.http_client import create_client

    headers = {}
    key = provider.get("api_key", "")
    if key and not key.startswith("Bearer "):
        key = f"Bearer {key}"
    if key:
        headers["Authorization"] = key

    files = {"file": file_tuple}
    for attempt in range(3):
        try:
            if attempt == 0:
                return await client.post(upload_url, headers=headers, files=files, timeout=timeout)
            # 重试用新连接，避免复用坏掉的 TLS
            async with create_client("fast") as fresh:
                return await fresh.post(upload_url, headers=headers, files=files, timeout=timeout)
        except Exception:
            if attempt == 2:
                raise
            await asyncio.sleep(0.6 * (attempt + 1))


# ---- 提交端点 ----

def video_submit_url(provider: dict) -> str:
    """返回 apimart 视频提交 URL。"""
    from ...system.providers import is_apimart_provider
    base_url = (provider.get("base_url") or "").rstrip("/")
    if is_apimart_provider(provider):
        if base_url.endswith("/v1"):
            return f"{base_url}/videos/generations"
        return f"{base_url}/v1/videos/generations"
    return f"{base_url}/v1/videos/generations"


# ---- VEO 3.1 body 构造 ----

def build_veo31_body(
    prompt: str, model: str, duration: int,
    aspect_ratio: str, resolution: str,
    image_urls: list | None = None,
) -> dict:
    """构造 APIMart VEO 3.1 请求体。"""
    mapped_model = veo31_model(model)
    body = {
        "prompt": prompt,
        "model": mapped_model,
        "duration": veo31_duration(duration),
        "aspect_ratio": veo31_aspect(aspect_ratio),
        "resolution": veo31_resolution(resolution),
    }
    if image_urls and mapped_model != "veo3.1-lite":
        video_images = image_urls[:3]
        if mapped_model == "veo3.1-quality" and len(video_images) > 2:
            video_images = video_images[:2]
        body["image_urls"] = video_images
        if len(video_images) == 2:
            body["generation_type"] = "frame"
        elif len(video_images) >= 3 and mapped_model != "veo3.1-quality":
            body["generation_type"] = "reference"
    if mapped_model != "veo3.1-lite":
        body["official_fallback"] = False
    return body


# ---- Seedance body 构造 ----

def build_seedance_body(
    prompt: str, model: str, duration: int,
    aspect_ratio: str, resolution: str, size: str,
    image_urls: list | None = None,
    image_with_roles: list | None = None,
    video_urls: list | None = None,
    audio_urls: list | None = None,
    seed: int | None = None,
    return_last_frame: bool = False,
    generate_audio: bool = False,
) -> dict:
    """构造 APIMart Seedance 请求体。"""
    body = {
        "prompt": prompt,
        "model": model or "doubao-seedance-2.0",
        "duration": video_duration(duration),
        "size": video_size(aspect_ratio or size),
        "resolution": resolution or "480p",
    }
    if image_with_roles:
        body["image_with_roles"] = image_with_roles
    elif image_urls:
        body["image_urls"] = image_urls[:9]
    if video_urls:
        body["video_urls"] = video_urls
    if audio_urls:
        body["audio_urls"] = audio_urls
    if seed is not None:
        body["seed"] = seed
    if return_last_frame:
        body["return_last_frame"] = True
    if generate_audio:
        body["generate_audio"] = True
    return body


# ---- 结果提取 ----

def _collect_video_url(value, out: list):
    """递归收集视频 URL。"""
    if isinstance(value, str) and value:
        out.append(value)
    elif isinstance(value, list):
        for item in value:
            _collect_video_url(item, out)
    elif isinstance(value, dict):
        for key in ("url", "output_url", "download_url"):
            if key in value:
                v = value[key]
                if isinstance(v, str) and v:
                    out.append(v)


def video_output_urls(raw) -> list[str]:
    """从 API 响应中提取视频 URL 列表。"""
    urls = []
    if not isinstance(raw, dict):
        return urls
    candidates = [raw]
    data = raw.get("data")
    content = raw.get("content")
    if isinstance(data, dict):
        candidates.append(data)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                candidates.append(item)
    if isinstance(content, dict):
        candidates.append(content)
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                candidates.append(item)
    for node in list(candidates):
        result = node.get("result") if isinstance(node, dict) else None
        if isinstance(result, dict):
            candidates.append(result)
        elif isinstance(result, list):
            for item in result:
                if isinstance(item, dict):
                    candidates.append(item)
    for node in candidates:
        if not isinstance(node, dict):
            continue
        for key in ("videos", "outputs", "content"):
            value = node.get(key)
            if value:
                _collect_video_url(value, urls)
        for key in VIDEO_URL_KEYS:
            if key in node:
                _collect_video_url(node.get(key), urls)
    deduped = []
    for url in urls:
        if isinstance(url, str) and url and url not in deduped:
            deduped.append(url)
    return deduped


# ---- 视频轮询 ----

async def poll_video_task(client, provider: dict, task_id: str, submit_url: str = "") -> dict:
    """轮询视频生成任务直到完成/失败/超时。"""
    from ...system.providers import is_apimart_provider
    from ...core.http_client import create_client

    base_url = (provider.get("base_url") or "").rstrip("/")

    # 构造候选查询 URL
    if is_apimart_provider(provider):
        task_urls = [f"{base_url}/tasks/{task_id}?language=zh" if base_url.endswith("/v1") else f"{base_url}/v1/tasks/{task_id}?language=zh"]
    else:
        task_urls = [
            f"{base_url}/v1/videos/generations/{task_id}",
            f"{base_url}/v1/tasks/{task_id}",
            f"{base_url}/v2/videos/generations/{task_id}",
        ]

    deadline = time.monotonic() + VIDEO_POLL_TIMEOUT
    delay = 2.0
    last_payload = {}

    while time.monotonic() < deadline:
        await asyncio.sleep(delay)
        raw = None
        last_error = None

        for task_url in task_urls:
            try:
                headers = {}
                key = provider.get("api_key", "")
                if key and not key.startswith("Bearer "):
                    key = f"Bearer {key}"
                if key:
                    headers["Authorization"] = key
                from ...core.http_client import request_with_fallback
                response = await request_with_fallback(
                    "GET", task_url, timeout_preset="fast", headers=headers,
                )
                response.raise_for_status()
                raw = response.json()
                break
            except Exception as exc:
                last_error = exc
                continue

        if raw is None:
            if last_error:
                raise last_error
            raise RuntimeError(f"视频任务查询失败：{task_id}")

        last_payload = raw
        task_data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        status = str(
            task_data.get("status") or task_data.get("task_status")
            or raw.get("status") or raw.get("task_status") or ""
        ).upper()

        if status in VIDEO_TASK_SUCCESS_STATUSES:
            return raw
        if status not in VIDEO_TASK_FAILURE_STATUSES and video_output_urls(raw):
            return raw
        if status in VIDEO_TASK_FAILURE_STATUSES:
            error = task_data.get("error") if isinstance(task_data.get("error"), dict) else {}
            reason = (
                task_data.get("fail_reason") or task_data.get("message")
                or error.get("message") or raw.get("error") or raw.get("message") or str(raw)
            )
            raise RuntimeError(_humanize_video_failure(reason))
        delay = min(delay * 1.6, 12)

    raise TimeoutError(f"视频生成任务超时：{last_payload or task_id}")


def _humanize_video_failure(reason) -> str:
    text = str(reason or "").strip()
    upper = text.upper()
    if "PROMINENT_PEOPLE_FILTER" in upper or "PROMINENT_PEOPLE" in upper:
        return (
            f"视频生成被上游内容安全策略拦截：检测到提示词或参考图里包含知名人物/真人面孔（错误码：{text}）。\n\n"
            "这不是代码错误，而是上游的内容审核规则。建议：\n"
            "  1. 去掉提示词里的人名、明星等指向具体真人的描述；\n"
            "  2. 换用非真人参考图（插画、AI 头像、商品图等）；\n"
            "  3. 如用了真人照片做参考图，先做模糊/遮挡处理。"
        )
    if "SAFETY" in upper or "CONTENT_FILTER" in upper or "POLICY" in upper:
        return f"视频生成被上游内容安全策略拦截（错误码：{text}）。\n请调整提示词/参考图后重试。"
    return f"视频生成任务失败：{text}"


def _content_type(path) -> str:
    """根据文件扩展名推断 content type。"""
    ext = str(path).rsplit(".", 1)[-1].lower() if "." in str(path) else ""
    return {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "webp": "image/webp", "gif": "image/gif", "mp4": "video/mp4",
        "webm": "video/webm", "mov": "video/quicktime", "avi": "video/x-msvideo",
    }.get(ext, "application/octet-stream")
