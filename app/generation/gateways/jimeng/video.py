"""
即梦视频生成 — 7 种模式。

模式映射：
  multimodal2video   — 有 video_refs 或 audio_refs 或 multimodal=True
  frames2video       — 2+ image_refs 且 first_frame + last_frame 都有
  multiframe2video   — 2+ image_refs（无 first/last_frame）
  image2video        — 1 个 image_ref
  text2video         — 无任何 ref（默认）
"""

from .process import jimeng_subprocess, JimengPendingError
from ..openai import ImageGenerationError


class JimengVideoGateway:
    """即梦视频生成 — 支持 7 种模式"""

    async def generate(
        self, prompt: str, model: str = "seedance2.0", duration: int = 5,
        aspect_ratio: str = "16:9", resolution: str = "720p",
        images: list | None = None, videos: list | None = None,
        audios: list | None = None, multimodal: bool = False,
        **kwargs,
    ) -> list[str]:
        version = self._video_model_version(model)
        mode = self._determine_mode(images, videos, audios, multimodal)

        args = [
            "video", mode,
            "--model", version,
            "--prompt", prompt,
            "--duration", str(duration),
            "--aspect", aspect_ratio,
            "--resolution", resolution,
        ]

        if images:
            for ref in images:
                url = ref.get("url", "") if isinstance(ref, dict) else str(ref)
                if url:
                    args += ["--image", url]

        try:
            result = await jimeng_subprocess.run(args)
        except JimengPendingError as e:
            raise ImageGenerationError(
                str(e) or f"即梦视频任务（模式: {mode}）在云端排队中，请稍后查询",
                202,
            )
        except RuntimeError as e:
            raise ImageGenerationError(str(e), 502)

        return self._extract_urls(result)

    def _determine_mode(self, images, videos, audios, multimodal) -> str:
        has_images = images and len(images) > 0
        has_videos = videos and len(videos) > 0
        has_audios = audios and len(audios) > 0

        if multimodal or has_videos or has_audios:
            return "multimodal2video"

        if has_images:
            count = len(images)
            if count >= 2:
                roles = [ref.get("role", "") if isinstance(ref, dict) else "" for ref in images]
                has_first = any("first" in r for r in roles)
                has_last = any("last" in r for r in roles)
                if has_first and has_last:
                    return "frames2video"
                return "multiframe2video"
            return "image2video"

        return "text2video"

    def _video_model_version(self, model: str) -> str:
        # 别名映射
        aliases = {
            "2.0": "seedance2.0",
            "3.0": "3.0",
            "3.0pro": "3.0pro",
            "3.5": "3.5pro",
        }
        for k, v in aliases.items():
            if k in model:
                return v
        return model

    def _extract_urls(self, result: dict) -> list[str]:
        urls = []
        for item in result.get("outputs", []):
            url = item.get("url") or item.get("remote_url", "")
            if url:
                urls.append(url)
        if not urls:
            url = result.get("url") or result.get("video_url", "")
            if url:
                urls.append(url)
        return urls
