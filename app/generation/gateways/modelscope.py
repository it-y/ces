"""
ModelScope 网关 — 图片生成（异步提交 + 轮询）。
"""

import asyncio
import time
from ...core.http_client import create_client, retry_request
from ...core.errors import friendly_image_error_detail
from ...config import IMAGE_POLL_INTERVAL, IMAGE_TASK_TIMEOUT, MODELSCOPE_CHAT_BASE_URL
from .openai import ImageGenerationError


class ModelScopeGateway:
    """ModelScope 图片生成网关"""

    def __init__(self, provider: dict):
        self.provider = provider
        self.api_key = provider.get("api_key", "")

    def _api_root(self) -> str:
        """聊天/通用 API 根路径（含 /v1）"""
        return self.provider.get("base_url", MODELSCOPE_CHAT_BASE_URL).rstrip("/")

    def _image_api_root(self) -> str:
        """图片 API 根路径（不含 /v1）"""
        base = self.provider.get("base_url", MODELSCOPE_CHAT_BASE_URL).rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        return base

    async def generate(
        self, prompt: str, size: str = "1024x1024", model: str = "",
        quality: str = "", n: int = 1,
        reference_images: list | None = None,
    ) -> list[str]:
        model = model or self.provider.get("image_models", ["Tongyi-MAI/Z-Image-Turbo"])[0]

        # 提交任务（需要 X-ModelScope-Async-Mode 头强制异步模式）
        url = f"{self._image_api_root()}/images/generations"
        body = {
            "model": model,
            "prompt": prompt,
            "n": n,
            "size": size,
        }
        if reference_images:
            body["image_urls"] = [ref.get("url") for ref in reference_images if ref.get("url")]

        headers = self._submit_headers()

        resp = await retry_request("POST", url, json=body, headers=headers)

        data = resp.json()

        # 200 可能返回图片 URL（同步模式）也可能是 task_id（异步模式）
        if resp.status_code == 200:
            urls = self._parse_urls(data)
            if urls:
                return urls
            # 200 但无图片 URL → 可能走异步，检查是否有 task_id
            task_id = data.get("task_id", "")
            if task_id:
                return await self._poll_task(task_id)

        # 非 200 → 检查是否走异步轮询
        task_id = data.get("task_id", "")
        if task_id:
            return await self._poll_task(task_id)

        raise ImageGenerationError(friendly_image_error_detail(resp.text, size, model), resp.status_code)

    def _submit_headers(self) -> dict:
        """提交请求头（含 Async-Mode）"""
        headers = {"X-ModelScope-Async-Mode": "true"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _poll_headers(self) -> dict:
        """轮询请求头（含 Task-Type）"""
        headers = {"X-ModelScope-Task-Type": "image_generation"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def _poll_task(self, task_id: str) -> list[str]:
        """轮询直到完成"""
        url = f"{self._image_api_root()}/tasks/{task_id}"
        deadline = time.monotonic() + IMAGE_TASK_TIMEOUT
        headers = self._poll_headers()

        while time.monotonic() < deadline:
            async with create_client("normal") as client:
                resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                await asyncio.sleep(IMAGE_POLL_INTERVAL)
                continue
            data = resp.json()
            status = str(data.get("status", "")).upper()

            if status in ("SUCCEED", "SUCCESS", "SUCCESSFUL", "COMPLETED", "COMPLETE", "DONE", "FINISHED", "READY"):
                return self._parse_output(data)
            if status in ("FAILED", "FAIL", "ERROR", "CANCELED", "CANCELLED", "TIMEOUT", "REVOKED"):
                raise ImageGenerationError(
                    data.get("error", {}).get("message", data.get("message", "生成失败")), 502,
                )
            await asyncio.sleep(IMAGE_POLL_INTERVAL)

        raise ImageGenerationError("图片生成超时，请稍后重试", 504)

    def _parse_urls(self, data: dict) -> list[str]:
        """从同步响应中解析图片 URL"""
        urls = []
        for item in data.get("data", []):
            if "url" in item:
                urls.append(item["url"])
        return urls

    def _parse_output(self, data: dict) -> list[str]:
        """从轮询后的响应中解析图片（支持 output_images 字段）"""
        # ModelScope 异步任务完成后图像在 output_images 字段
        output_images = data.get("output_images", [])
        if output_images:
            return [img.get("url", "") for img in output_images if img.get("url")]

        # fallback: 检查 data 数组
        return self._parse_urls(data)
