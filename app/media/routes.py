"""
媒体工具路由 — /api/media-preview, /api/image-jpeg, /api/download-output
"""

import io
import os
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import FileResponse, StreamingResponse, Response
from ..config import MEDIA_PREVIEW_DIR, OUTPUT_DIR, UPLOAD_DIR, CANVAS_FILES_DIR
from ..core.http_client import create_client
from ..core.security import safe_path_join, sanitize_filename, validate_remote_url

router = APIRouter(prefix="/api", tags=["media"])


# ============================================================
# 媒体预览缩略图
# ============================================================

@router.get("/media-preview")
async def media_preview(path: str = Query(""), width: int = Query(256), url: str = Query("")):
    """
    生成图片/视频缩略图。
    支持本地文件路径和远程 URL。
    缓存到 data/cache/media_previews/。
    """
    target = path or url
    if not target:
        raise HTTPException(400, "缺少 path 参数")

    # 本地文件
    local = await _resolve_local_path(target)
    if local and local.exists() and local.is_file():
        return await _thumbnail_from_local(local, width)

    # 远程 URL
    if target.startswith("http"):
        validate_remote_url(target)
        return await _thumbnail_from_remote(target, width)

    raise HTTPException(404, "文件不存在")


async def _resolve_local_path(raw: str) -> Path | None:
    """解析本地路径（仅允许项目内的安全目录）"""
    candidates = []
    for base in (OUTPUT_DIR, UPLOAD_DIR, MEDIA_PREVIEW_DIR):
        try:
            p = safe_path_join(base, os.path.basename(raw))
            candidates.append(p)
        except Exception:
            continue

    # 也尝试直接文件名查找
    filename = os.path.basename(raw)
    for d in (OUTPUT_DIR, UPLOAD_DIR):
        p = d / filename
        if p.exists():
            return p
    return None


async def _thumbnail_from_local(filepath: Path, width: int) -> Response:
    """从本地文件生成缩略图"""
    ext = filepath.suffix.lower()

    # 视频 → ffmpeg 抽首帧
    if ext in (".mp4", ".webm", ".mov", ".avi", ".mkv"):
        return await _video_thumbnail(filepath, width)

    # 图片 → PIL 缩略
    return await _image_thumbnail(filepath, width)


async def _image_thumbnail(filepath: Path, width: int) -> Response:
    """PIL 图片缩略图，带磁盘缓存 — 第二次打开秒开"""
    import hashlib
    try:
        from PIL import Image
        import asyncio

        # 缓存 key = 文件完整路径 + 宽度 + 修改时间
        mtime = filepath.stat().st_mtime
        cache_key = hashlib.md5(f"{filepath.resolve()}|{width}|{mtime}".encode()).hexdigest()
        cache_file = MEDIA_PREVIEW_DIR / f"{cache_key}.jpg"

        # 命中缓存 → 直接返回（不走 PIL，零 CPU）
        if cache_file.exists():
            return FileResponse(cache_file, media_type="image/jpeg")

        # 未命中 → 生成缩略图
        img = await asyncio.to_thread(Image.open, str(filepath))
        img = img.convert("RGB")

        w, h = img.size
        if w > width:
            ratio = width / w
            new_h = int(h * ratio)
            img = await asyncio.to_thread(img.resize, (width, new_h), Image.LANCZOS)

        # 存磁盘，下次秒开
        MEDIA_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(img.save, str(cache_file), format="JPEG", quality=85)

        return FileResponse(cache_file, media_type="image/jpeg")
    except Exception:
        raise HTTPException(500, "缩略图生成失败")


async def _video_thumbnail(filepath: Path, width: int) -> Response:
    """ffmpeg 视频首帧缩略图"""
    import asyncio
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-i", str(filepath),
            "-vf", f"scale={width}:-1",
            "-vframes", "1",
            "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "3",
            "-", "-y", "-loglevel", "error",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            raise Exception("ffmpeg 失败")
        return Response(content=stdout, media_type="image/jpeg")
    except asyncio.TimeoutError:
        raise HTTPException(504, "视频缩略图超时")
    except FileNotFoundError:
        raise HTTPException(501, "ffmpeg 未安装")
    except Exception:
        raise HTTPException(500, "视频缩略图生成失败")


async def _thumbnail_from_remote(url: str, width: int) -> Response:
    """从远程 URL 下载并生成缩略图"""
    try:
        async with create_client("normal") as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                raise HTTPException(404, "远程文件下载失败")

            from PIL import Image
            import asyncio
            img = await asyncio.to_thread(Image.open, io.BytesIO(resp.content))
            img = img.convert("RGB")
            w, h = img.size
            if w > width:
                ratio = width / w
                img = await asyncio.to_thread(img.resize, (width, int(h * ratio)), Image.LANCZOS)
            buf = io.BytesIO()
            await asyncio.to_thread(img.save, buf, format="JPEG", quality=85)
            return Response(content=buf.getvalue(), media_type="image/jpeg")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(500, "远程缩略图生成失败")


# ============================================================
# JPEG 转换（给不支持 WebP 的客户端）
# ============================================================

@router.get("/image-jpeg")
async def convert_to_jpeg(url: str = Query(""), quality: int = Query(85, ge=10, le=100)):
    """将任意图片转为 JPEG"""
    if not url:
        raise HTTPException(400, "缺少 url 参数")

    try:
        import asyncio
        # 本地文件
        if not url.startswith("http"):
            for d in (OUTPUT_DIR, UPLOAD_DIR):
                p = d / os.path.basename(url)
                if p.exists():
                    img = await asyncio.to_thread(__import__("PIL.Image").open, str(p))
                    img = img.convert("RGB")
                    buf = io.BytesIO()
                    await asyncio.to_thread(img.save, buf, format="JPEG", quality=quality)
                    return Response(content=buf.getvalue(), media_type="image/jpeg")

        # 远程
        validate_remote_url(url)
        async with create_client("normal") as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                raise HTTPException(404, "图片下载失败")
            img = await asyncio.to_thread(__import__("PIL.Image").open, io.BytesIO(resp.content))
            img = img.convert("RGB")
            buf = io.BytesIO()
            await asyncio.to_thread(img.save, buf, format="JPEG", quality=quality)
            return Response(content=buf.getvalue(), media_type="image/jpeg")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(500, "图片转换失败")


# ============================================================
# 流式代理下载（支持 Range — 视频 seek 必需）
# ============================================================

@router.get("/download-output")
async def download_output(request: Request, url: str = Query("")):
    """流式代理远程文件下载，透传 Range header"""
    if not url:
        raise HTTPException(400, "缺少 url 参数")

    local_prefixes = {
        "/output/": OUTPUT_DIR,
        "/assets/": UPLOAD_DIR,
        "/cfiles/": CANVAS_FILES_DIR,
    }
    for prefix, base in local_prefixes.items():
        if url.startswith(prefix):
            path = safe_path_join(base, url[len(prefix):])
            if not path.is_file():
                raise HTTPException(404, "文件不存在")
            return FileResponse(path)

    validate_remote_url(url)

    req_headers = {}
    if request.headers.get("range"):
        req_headers["Range"] = request.headers["range"]

    async with create_client("normal") as client:
        resp = await client.get(url, headers=req_headers)
        if resp.status_code not in (200, 206):
            raise HTTPException(resp.status_code, "下载失败")

        async def stream():
            async for chunk in resp.aiter_bytes(65536):
                yield chunk

        resp_headers = {"Content-Type": resp.headers.get("content-type", "application/octet-stream")}
        for h in ("content-length", "content-range", "accept-ranges"):
            if h in resp.headers:
                resp_headers[h.replace("-", " ").title().replace(" ", "-")] = resp.headers[h]

        return StreamingResponse(stream(), status_code=resp.status_code, headers=resp_headers)


_MEDIA_TYPE_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".pdf": "application/pdf",
    ".json": "application/json",
}


@router.get("/view")
async def view_file(path: str = Query("")):
    if not path:
        raise HTTPException(400, "缺少 path 参数")
    filename = os.path.basename(path)
    for base in (OUTPUT_DIR, UPLOAD_DIR):
        resolved = safe_path_join(base, filename)
        if resolved.exists():
            ext = resolved.suffix.lower()
            media_type = _MEDIA_TYPE_MAP.get(ext, "application/octet-stream")
            return FileResponse(str(resolved), media_type=media_type)
    raise HTTPException(404, "文件不存在")
