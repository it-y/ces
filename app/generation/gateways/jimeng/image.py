"""
即梦图片生成 — text2image / image2image。

调用 CLI:
  dreamina image generate --model 5.0 --prompt "..." --size 1024x1024
"""

from .process import jimeng_subprocess


class JimengImageGateway:
    """即梦图片生成"""

    async def generate(
        self, prompt: str, size: str = "1024x1024", model: str = "5.0",
        quality: str = "", n: int = 1,
        reference_images: list | None = None,
    ) -> list[str]:
        version = self._normalize_model(model)
        if not version:
            version = "5.0"

        args = [
            "image", "generate",
            "--model", version,
            "--prompt", prompt,
            "--size", size,
        ]

        if reference_images:
            ref_url = reference_images[0].get("url", "") if reference_images else ""
            if ref_url:
                args += ["--image", ref_url]

        result = await jimeng_subprocess.run(args)
        if result is None:
            raise Exception("即梦图片生成失败，请检查 CLI 是否已安装和登录")

        return self._extract_urls(result)

    def _normalize_model(self, model: str) -> str:
        """提取模型版本号，如 '5.0' '4.6'"""
        import re
        m = re.search(r"(\d+\.\d+)", model)
        return m.group(1) if m else ""

    def _extract_urls(self, result: dict) -> list[str]:
        urls = []
        for item in result.get("outputs", []):
            url = item.get("url") or item.get("remote_url", "")
            if url:
                urls.append(url)
        if not urls:
            url = result.get("url") or result.get("image_url", "")
            if url:
                urls.append(url)
        return urls
