"""
ModelScope 网关 — 图片生成（异步提交 + 轮询）。
"""

import asyncio
import time
from ...core.http_client import create_client, retry_request
from ...core.errors import friendly_image_error_detail
from ...config import IMAGE_POLL_INTERVAL, IMAGE_TASK_TIMEOUT, MODELSCOPE_CHAT_BASE_URL


class ModelScopeGateway:
    """ModelScope 图片生成网关"""

    def __init__(self, provider: dict):
        self.provider = provider
        self.api_key = provider.get("api_key", "")

    def _api_root(self) -> str:
        return self.provider.get("base_url", MODELSCOPE_CHAT_BASE_URL).rstrip("/")

    async def generate(
        self, prompt: str, size: str = "1024x1024", model: str = "",
        quality: str = "", n: int = 1,
        reference_images: list | None = None,
    ) -> list[str]:
        model = model or self.provider.get("image_models", ["Tongyi-MAI/Z-Image-Turbo"])[0]

        # 提交任务
        url = f"{self._api_root()}/images/generations"
        body = {
            "model": model,
            "prompt": prompt,
            "n": n,
            "size": size,
        }
        if reference_images:
            body["image_urls"] = [ref.get("url") for ref in reference_images if ref.get("url")]

        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

        resp = await retry_request("POST", url, json=body, headers=headers)

        if resp.status_code == 200:
            data = resp.json()
            return self._parse_urls(data)

        # 可能需要轮询
        data = resp.json()
        task_id = data.get("task_id", "")
        if task_id:
            return await self._poll_task(task_id, headers)

        raise Exception(friendly_image_error_detail(resp.text, size, model))

    async def _poll_task(self, task_id: str, headers: dict) -> list[str]:
        """轮询直到完成"""
        url = f"{self._api_root()}/images/tasks/{task_id}"
        deadline = time.time() + IMAGE_TASK_TIMEOUT

        while time.time() < deadline:
            async with create_client("normal") as client:
                resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                await asyncio.sleep(IMAGE_POLL_INTERVAL)
                continue
            data = resp.json()
            status = data.get("status", "")
            if status in ("done", "succeeded", "completed", "success"):
                return self._parse_urls(data)
            if status in ("failed", "error", "cancelled"):
                raise Exception(data.get("error", {}).get("message", "生成失败"))
            await asyncio.sleep(IMAGE_POLL_INTERVAL)

        raise Exception("图片生成超时，请稍后重试")

    def _parse_urls(self, data: dict) -> list[str]:
        urls = []
        for item in data.get("data", []):
            if "url" in item:
                urls.append(item["url"])
        return urls
