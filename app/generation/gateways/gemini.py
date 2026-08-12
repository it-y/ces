"""
Gemini 网关 — 图片生成（multipart + URL 拼接）。
"""

import base64
from ...core.http_client import create_client
from ...core.errors import friendly_image_error_detail


class GeminiGateway:
    """Gemini 图片生成网关"""

    def __init__(self, provider: dict):
        self.provider = provider
        self.api_key = provider.get("api_key", "")

    def _endpoint(self, model: str) -> str:
        base = self.provider.get("base_url", "").rstrip("/")
        # 自动补 /v1beta 如果不存在
        if "/v1beta" not in base:
            base = f"{base}/v1beta"
        model_name = model or "gemini-2.5-flash-image-preview"
        if not model_name.startswith("models/"):
            model_name = f"models/{model_name}"
        return f"{base}/{model_name}:generateContent"

    async def generate(
        self, prompt: str, size: str = "", model: str = "",
        quality: str = "", n: int = 1,
        reference_images: list | None = None,
    ) -> list[str]:
        url = self._endpoint(model)
        url = f"{url}?key={self.api_key}"

        parts = [{"text": prompt}]
        if reference_images:
            for ref in reference_images:
                img_data = await self._fetch_image_bytes(ref.get("url", ""))
                if img_data:
                    parts.append({
                        "inline_data": {
                            "mime_type": ref.get("mime", "image/png"),
                            "data": base64.b64encode(img_data).decode(),
                        }
                    })

        body = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "responseModalities": ["IMAGE", "TEXT"],
            },
        }

        from ...core.http_client import request_with_fallback
        resp = await request_with_fallback("POST", url, timeout_preset="long", json=body)

        if resp.status_code != 200:
            raise Exception(friendly_image_error_detail(resp.text, size, model))

        return self._parse_image_urls(resp.json())

    def _parse_image_urls(self, data: dict) -> list[str]:
        urls = []
        for candidate in data.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                if "inlineData" in part:
                    d = part["inlineData"]
                    mime = d.get("mimeType", "image/png")
                    urls.append(f"data:{mime};base64,{d.get('data', '')}")
                if "fileData" in part:
                    urls.append(part["fileData"].get("fileUri", ""))
        return urls

    async def _fetch_image_bytes(self, url: str) -> bytes | None:
        if not url:
            return None
        if url.startswith("data:"):
            try:
                _, encoded = url.split(",", 1)
                return base64.b64decode(encoded)
            except Exception:
                return None
        try:
            async with create_client("normal") as client:
                resp = await client.get(url)
                return resp.content if resp.status_code == 200 else None
        except Exception:
            return None
