"""
图片/视频生成网关接口（轻量 Protocol，不继承 ABC）。
"""

from pathlib import Path
from typing import Optional, Protocol

from ...config import UPLOAD_DIR, OUTPUT_DIR, CANVAS_FILES_DIR


def resolve_local_media_path(url: str) -> Optional[Path]:
    """把本地媒体 URL（/assets/xx、/output/xx、/cfiles/xx）解析为文件系统路径。

    带 include 检查：解析后的真实路径必须仍位于对应资源根目录内，
    防 `/assets/../xxx` 这类穿越读取任意本地文件后外传给第三方 API。
    不匹配或越界返回 None。
    """
    if not url or not url.startswith("/"):
        return None
    clean = url.split("?", 1)[0]
    roots = {
        "/cfiles/": CANVAS_FILES_DIR,
        "/assets/": UPLOAD_DIR,
        "/output/": OUTPUT_DIR,
    }
    for prefix, root in roots.items():
        if clean.startswith(prefix):
            rel = clean[len(prefix):].lstrip("/")
            if not rel:
                return None
            path = Path(root) / rel
            try:
                path.resolve().relative_to(Path(root).resolve())
            except ValueError:
                return None
            return path
    return None


class ImageGateway(Protocol):
    """图片生成网关"""

    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        model: str = "",
        quality: str = "auto",
        n: int = 1,
        reference_images: list | None = None,
    ) -> list[str]:
        """返回生成的图片 URL 列表"""
        ...


class VideoGateway(Protocol):
    """视频生成网关"""

    async def generate(
        self,
        prompt: str,
        model: str = "",
        duration: int = 5,
        aspect_ratio: str = "16:9",
        resolution: str = "720p",
        reference_images: list | None = None,
        reference_videos: list | None = None,
        reference_audios: list | None = None,
        **kwargs,
    ) -> list[str]:
        """返回生成的视频 URL 列表"""
        ...
