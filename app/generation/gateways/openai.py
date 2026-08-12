"""
OpenAI 兼容协议网关 — 支持 OpenAI/Apimart/Gemini-compatible 等。

修改记录：
  - 加 retry（指数退避 3 次）
  - _parse_image_urls 加深度递归 fallback
  - HTTP/1.1 兼容（中转站常见不支持 HTTP/2）
"""

import asyncio
import base64
import json
import time
from typing import Optional
from pathlib import Path

from ...core.http_client import create_client, retry_request
from ...core.errors import friendly_image_error_detail
from ...config import UPLOAD_DIR, OUTPUT_DIR, CANVAS_FILES_DIR


class OpenAIGateway:
    """OpenAI 兼容图片生成网关"""

    def __init__(self, provider: dict):
        self.provider = provider
        self.base_url = provider.get("base_url", "").rstrip("/")
        self.api_key = provider.get("api_key", "")
        self.protocol = provider.get("protocol", "openai")
        self.image_request_mode = provider.get("image_request_mode", "openai")

    # ---- 主入口 ----

    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        model: str = "",
        quality: str = "auto",
        n: int = 1,
        reference_images: list | None = None,
    ) -> list[str]:
        """
        生成图片。返回 URL 列表。
        如果有参考图，走 edit 端点；否则走 generations 端点。
        """
        if reference_images:
            return await self._generate_with_refs(prompt, size, model, reference_images)
        return await self._generate_simple(prompt, size, model, quality, n)

    # ---- retry 请求包装 ----

    async def _post_with_retry(self, url: str, **kwargs) -> dict:
        """直连 POST + 重试，返回 JSON 响应体"""
        resp = await retry_request("POST", url, **kwargs)
        if resp.status_code != 200:
            msg = friendly_image_error_detail(resp.text, url, "")
            raise ImageGenerationError(msg, resp.status_code)
        return resp.json()

    # ---- 简单生成（无参考图） ----

    async def _generate_simple(
        self, prompt: str, size: str, model: str,
        quality: str, n: int,
    ) -> list[str]:
        url = f"{self.base_url}/v1/images/generations"
        body = {
            "prompt": prompt,
            "model": model or self.provider.get("image_models", [""])[0],
            "n": n,
            "size": size,
            "quality": quality,
        }
        if self.image_request_mode == "openai-json":
            body = {"extra_body": body}  # Agnes 等特殊模式

        data = await self._post_with_retry(
            url,
            json=body,
            headers=self._auth_headers(),
        )
        return self._parse_image_urls(data)

    # ---- 带参考图生成 ----

    async def _generate_with_refs(
        self, prompt: str, size: str, model: str, refs: list,
    ) -> list[str]:
        """带参考图的生成。
        - openai-json 模式：走 /v1/images/generations（JSON body，参考图转为 data URL）
        - 其他模式：走 /v1/images/edits（multipart）
        如果无法获取任何参考图文件，降级为无参考图生成。
        """
        # openai-json 模式：JSON body 传 data URL
        if self.image_request_mode == "openai-json":
            extra_body = {"response_format": "url"}
            image_urls = []
            for ref in refs:
                data_url = await self._ref_to_data_url(ref)
                if data_url:
                    image_urls.append(data_url)
            if image_urls:
                extra_body["image"] = image_urls
            body = {"model": model, "prompt": prompt, "size": size, "extra_body": extra_body}
            url = f"{self.base_url}/v1/images/generations"
            raw = await self._post_with_retry(url, json=body, headers=self._auth_headers())
            return self._parse_image_urls(raw)

        # multipart 模式：走 /v1/images/edits
        files = []
        for ref in refs:
            img_data = await self._fetch_image_bytes(ref.get("url", ""))
            if img_data:
                files.append(("image", (ref.get("name", "ref.png"), img_data, ref.get("mime", "image/png"))))

        # 没有成功获取任何参考图 → 降级为无参考图生成
        if not files:
            return await self._generate_simple(prompt, size, model, "auto", 1)

        url = f"{self.base_url}/v1/images/edits"
        data = {"prompt": prompt, "model": model, "size": size}
        raw = await self._post_with_retry(url, files=files, data=data, headers=self._auth_headers())
        return self._parse_image_urls(raw)

    async def _ref_to_data_url(self, ref: dict, max_size: int = 1536) -> str | None:
        """将参考图转为 data URL（用于 openai-json 模式）"""
        data = await self._fetch_image_bytes(ref.get("url", ""))
        if not data:
            return None
        import base64
        mime = ref.get("mime", "") or "image/png"
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"

    # ---- 结果解析（带深度 fallback） ----

    def _parse_image_urls(self, data: dict) -> list[str]:
        """
        从 OpenAI 响应中提取图片 URL。
        优先标准格式 data[].url / data[].b64_json，
        失败后用深度递归扫描整个响应体。
        """
        urls = []
        for item in data.get("data", []):
            if isinstance(item, dict):
                if "url" in item and isinstance(item["url"], str):
                    urls.append(item["url"])
                elif "b64_json" in item and isinstance(item["b64_json"], str):
                    urls.append(f"data:image/png;base64,{item['b64_json']}")

        if urls:
            return urls

        # fallback: 递归扫描整个响应，找到所有合法图片 URL 或 base64
        urls = self._deep_scan_urls(data)
        return urls

    @staticmethod
    def _deep_scan_urls(obj, max_depth: int = 8) -> list[str]:
        """递归扫描对象，找出所有以 http 或 data:image 开头的图片 URL"""
        found = set()

        def _scan(val, depth=0):
            if depth > max_depth:
                return
            if isinstance(val, dict):
                for v in val.values():
                    if isinstance(v, str) and (v.startswith("http") or v.startswith("data:image")):
                        found.add(v)
                    else:
                        _scan(v, depth + 1)
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, str) and (item.startswith("http") or item.startswith("data:image")):
                        found.add(item)
                    else:
                        _scan(item, depth + 1)

        _scan(obj)
        return list(found)[:20]

    # ---- 工具 ----

    def _auth_headers(self) -> dict:
        key = self.api_key
        if key and not key.startswith("Bearer "):
            key = f"Bearer {key}"
        headers = {}
        if key:
            headers["Authorization"] = key
        return headers

    def _local_path_from_url(self, url: str) -> Optional[Path]:
        """将本地 URL（/assets/xxx, /output/xxx, /cfiles/xxx）转为文件系统路径"""
        from pathlib import Path as _Path
        path_part = url.split("?")[0]
        if path_part.startswith("/assets/"):
            return _Path(UPLOAD_DIR) / path_part[len("/assets/"):]
        if path_part.startswith("/output/"):
            return _Path(OUTPUT_DIR) / path_part[len("/output/"):]
        if path_part.startswith("/cfiles/"):
            return _Path(CANVAS_FILES_DIR) / path_part[len("/cfiles/"):]

    async def _fetch_image_bytes(self, url: str) -> Optional[bytes]:
        """下载远程图片或从本地文件读取"""
        if not url:
            return None
        if url.startswith("data:"):
            try:
                _, encoded = url.split(",", 1)
                return base64.b64decode(encoded)
            except Exception:
                return None
        if url.startswith("/"):
            local = self._local_path_from_url(url)
            if local and local.exists():
                return local.read_bytes()
            return None
        try:
            async with create_client("normal") as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.content
        except Exception:
            pass
        return None


class ImageGenerationError(Exception):
    """图片生成失败"""
    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code
