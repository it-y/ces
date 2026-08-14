"""
Gemini 网关 — 图片生成（multipart + URL 拼接）。
"""

import base64
from ...core.http_client import create_client, retry_request
from ...core.errors import friendly_image_error_detail
from .openai import ImageGenerationError


# Gemini imageConfig 分辨率映射
_GEMINI_IMAGE_SIZES = {
    "1K": "1K", "2K": "2K", "4K": "4K",
    "1024x1024": "1K", "1024x576":  "1K", "576x1024":  "1K",
    "2048x2048": "2K", "2048x1152": "2K", "1152x2048": "2K",
    "4096x4096": "4K", "4096x2304": "4K", "2304x4096": "4K",
}


def _gemini_aspect_ratio(size: str) -> str:
    """从尺寸字符串推断 Gemini aspectRatio"""
    parts = size.lower().replace("x", " ").split()
    if len(parts) != 2:
        return "1:1"
    try:
        w, h = int(parts[0]), int(parts[1])
    except ValueError:
        return "1:1"
    if w == h:
        return "1:1"
    if w > h:
        if w / h >= 2.0:
            return "21:9" if w / h > 2.0 else "16:9"
        return "4:3" if w / h < 1.5 else "16:9"
    else:
        if h / w >= 2.0:
            return "9:21" if h / w > 2.0 else "9:16"
        return "3:4" if h / w < 1.5 else "9:16"


def _gemini_image_size(size: str) -> str:
    """从尺寸字符串推断 Gemini imageSize（1K/2K/4K）"""
    if not size:
        return "2K"
    if size.upper() in ("1K", "2K", "4K"):
        return size.upper()
    if size in _GEMINI_IMAGE_SIZES:
        return _GEMINI_IMAGE_SIZES[size]
    # 按长边推断
    parts = size.lower().replace("x", " ").split()
    if len(parts) >= 2:
        try:
            long_edge = max(int(parts[0]), int(parts[1]))
            if long_edge >= 3000:
                return "4K"
            if long_edge >= 1800:
                return "2K"
        except ValueError:
            pass
    return "2K"


def gemini_image_config(size: str) -> dict:
    """构建 Gemini imageConfig"""
    return {
        "aspectRatio": _gemini_aspect_ratio(size),
        "imageSize": _gemini_image_size(size),
    }


class GeminiGateway:
    """Gemini 图片生成网关"""

    def __init__(self, provider: dict):
        self.provider = provider
        self.api_key = provider.get("api_key", "")

    @staticmethod
    def _normalize_model_name(model: str) -> str:
        """规范化模型名 — 去除 models/ 前缀再统一添加"""
        name = (model or "gemini-2.5-flash-image-preview")
        if name.startswith("models/"):
            name = name[len("models/"):]
        return f"models/{name}"

    def _endpoint(self, model: str) -> str:
        base = self.provider.get("base_url", "").rstrip("/")
        # 支持 provider 级别的 endpoint 覆盖
        endpoint_override = self.provider.get("image_generation_endpoint", "")
        if endpoint_override:
            if endpoint_override.startswith("http://") or endpoint_override.startswith("https://"):
                return f"{endpoint_override}:generateContent"
            base = endpoint_override.rstrip("/")

        # 自动补 /v1beta 如果不存在
        if "/v1beta" not in base:
            base = f"{base}/v1beta"
        model_name = self._normalize_model_name(model)
        return f"{base}/{model_name}:generateContent"

    async def generate(
        self, prompt: str, size: str = "", model: str = "",
        quality: str = "", n: int = 1,
        reference_images: list | None = None,
    ) -> list[str]:
        url = self._endpoint(model)

        parts = [{"text": prompt}]
        if reference_images:
            for ref in reference_images:
                img_data = await self._fetch_image_bytes(ref.get("url", ""))
                if img_data:
                    parts.append({
                        "inlineData": {
                            "mimeType": ref.get("mime", "image/png"),
                            "data": base64.b64encode(img_data).decode(),
                        }
                    })

        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseModalities": ["IMAGE", "TEXT"],
                "imageConfig": gemini_image_config(size),
            },
        }

        headers = {"x-goog-api-key": self.api_key} if self.api_key else {}

        resp = await retry_request("POST", url, json=body, headers=headers)

        # Gemini 图片为同步返回（candidates 内嵌 base64），只接受 200/201/202
        if resp.status_code >= 300:
            raise ImageGenerationError(friendly_image_error_detail(resp.text, size, model), resp.status_code)

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
        # 本地路径（/assets/, /output/, /cfiles/）
        if url.startswith("/"):
            from pathlib import Path
            from ...config import UPLOAD_DIR, OUTPUT_DIR, CANVAS_FILES_DIR
            path_part = url.split("?")[0].lstrip("/")
            for root in (CANVAS_FILES_DIR, UPLOAD_DIR, OUTPUT_DIR):
                try:
                    local = Path(root) / path_part.split("/", 1)[-1] if "/" in path_part else Path(root) / path_part
                    if local.exists():
                        return local.read_bytes()
                except Exception:
                    continue
            return None
        try:
            async with create_client("normal") as client:
                resp = await client.get(url)
                return resp.content if resp.status_code == 200 else None
        except Exception:
            return None
