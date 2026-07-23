"""
图片/视频生成网关接口（轻量 Protocol，不继承 ABC）。
"""

from typing import Protocol


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
