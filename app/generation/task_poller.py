"""
异步任务轮询器 — 提交 apimart 等异步生图任务后，轮询直到完成或超时。

从原版 main.py:4625-4683 迁移重构。
"""

import asyncio
import json
import os
import time
from typing import Optional

from ..core.http_client import request_with_fallback

# ---- 超时 & 间隔 ----

AI_REQUEST_TIMEOUT = float(os.getenv("AI_REQUEST_TIMEOUT", "600"))
IMAGE_POLL_INTERVAL = float(os.getenv("IMAGE_POLL_INTERVAL", "2"))
IMAGE_TASK_TIMEOUT = float(os.getenv("IMAGE_TASK_TIMEOUT", str(AI_REQUEST_TIMEOUT)))
APIMART_IMAGE_TASK_TIMEOUT = float(os.getenv("APIMART_IMAGE_TASK_TIMEOUT", "1800"))
APIMART_IMAGE_POLL_INTERVAL = float(os.getenv("APIMART_IMAGE_POLL_INTERVAL", "5"))
APIMART_IMAGE_INITIAL_POLL_DELAY = float(os.getenv("APIMART_IMAGE_INITIAL_POLL_DELAY", "10"))

IMAGE_TASK_SUCCESS_STATUSES = {
    "SUCCESS", "SUCCESSFUL", "SUCCEED", "SUCCEEDED",
    "COMPLETED", "COMPLETE", "DONE", "FINISHED", "OK", "READY",
}
IMAGE_TASK_FAILED_STATUSES = {
    "FAILURE", "FAILED", "FAIL", "ERROR", "ERRORED",
    "CANCELED", "CANCELLED", "TIMEOUT", "REJECTED", "EXPIRED",
}


# ---- task_id 提取 ----

def extract_task_id(data) -> Optional[str]:
    """
    从 API 响应中递归提取 task_id。
    支持顶层 task_id / id / data[0].task_id / data.task_id 等嵌套格式。

    示例：
      {"task_id": "img_abc"} → "img_abc"
      {"code":200, "data":[{"status":"submitted", "task_id":"img_xyz"}]} → "img_xyz"
      {"id": "task_123"} → "task_123"
    """
    if isinstance(data, dict):
        if data.get("task_id"):
            return str(data["task_id"])
        if data.get("id") and str(data["id"]).startswith("task"):
            return str(data["id"])
        nested = data.get("data")
        if isinstance(nested, list) and nested:
            first = nested[0]
            if isinstance(first, dict):
                return extract_task_id(first)
        if isinstance(nested, dict):
            return extract_task_id(nested)
    return None


# ---- 任务端点 ----

def image_task_url_for_provider(provider: dict, task_id: str) -> str:
    """
    根据 provider 类型返回轮询 URL。
    apimart: /v1/tasks/{task_id}
    标准:    /v1/images/tasks/{task_id}
    """
    from ..system.providers import is_apimart_provider

    base_url = (provider.get("base_url") or "").rstrip("/")
    if is_apimart_provider(provider):
        if base_url.endswith("/v1"):
            return f"{base_url}/tasks/{task_id}"
        return f"{base_url}/v1/tasks/{task_id}"
    if base_url.endswith("/v1"):
        return f"{base_url}/images/tasks/{task_id}"
    return f"{base_url}/v1/images/tasks/{task_id}"


# ---- 状态解析 ----

def _image_task_data(payload) -> dict:
    """从 payload 中提取 task data 子对象。"""
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload if isinstance(payload, dict) else {}


def image_task_status(payload) -> str:
    """提取任务状态字符串（大写）。"""
    task_data = _image_task_data(payload)
    return str(task_data.get("status") or task_data.get("task_status") or "").upper()


def image_task_fail_reason(payload) -> str:
    """提取任务失败原因。"""
    task_data = _image_task_data(payload)
    error = task_data.get("error") if isinstance(task_data.get("error"), dict) else {}
    return (
        task_data.get("fail_reason")
        or task_data.get("message")
        or error.get("message")
        or (payload.get("message") if isinstance(payload, dict) else "")
        or "生图任务失败"
    )


# ---- 轮询器 ----

async def poll_image_task(task_id: str, provider: dict) -> dict:
    """
    轮询异步图片生成任务，直到完成、失败或超时。
    返回包含图片 URL 的响应体。

    异常：
      - TimeoutError: 轮询超时
      - RuntimeError: 任务失败
    """
    from ..system.providers import is_apimart_provider

    is_apimart = is_apimart_provider(provider)
    timeout = APIMART_IMAGE_TASK_TIMEOUT if is_apimart else IMAGE_TASK_TIMEOUT
    interval = APIMART_IMAGE_POLL_INTERVAL if is_apimart else IMAGE_POLL_INTERVAL
    initial_delay = APIMART_IMAGE_INITIAL_POLL_DELAY if is_apimart else 0

    deadline = time.monotonic() + timeout
    last_payload = {}

    while time.monotonic() < deadline:
        # 首次延迟（apimart 多等一会让服务端处理）
        if initial_delay:
            await asyncio.sleep(min(initial_delay, max(0.0, deadline - time.monotonic())))
            initial_delay = 0
            if time.monotonic() >= deadline:
                break

        # 查询任务状态
        task_url = image_task_url_for_provider(provider, task_id)
        try:
            resp = await request_with_fallback(
                "GET", task_url, timeout_preset="fast",
                headers=_poll_headers(provider),
            )
        except Exception:
            await asyncio.sleep(min(interval, max(0.0, deadline - time.monotonic())))
            continue
        if resp.status_code != 200:
            last_payload = {}
            await asyncio.sleep(min(interval, max(0.0, deadline - time.monotonic())))
            continue

        last_payload = resp.json()

        # 检查状态
        status = image_task_status(last_payload)

        if not status:
            # 无 status 字段 — 可能已经是结果（含 images），尝试发现 URL
            if _payload_has_image_url(last_payload):
                return last_payload

        if status in IMAGE_TASK_SUCCESS_STATUSES:
            return last_payload
        if status in IMAGE_TASK_FAILED_STATUSES:
            raise RuntimeError(f"生图任务失败: {image_task_fail_reason(last_payload)}")

        await asyncio.sleep(min(interval, max(0.0, deadline - time.monotonic())))

    raw_text = json.dumps(last_payload, ensure_ascii=False)[:800] if last_payload else ""
    extra = f"，最后响应：{raw_text}" if raw_text else ""
    raise TimeoutError(
        f"生图任务超时（已等待 {int(timeout)} 秒），task_id={task_id}{extra}"
    )


def _payload_has_image_url(payload: dict) -> bool:
    """检查 payload 是否包含图片 URL（用于判断无 status 的响应是否为结果）。"""
    import json as _json
    text = _json.dumps(payload) if isinstance(payload, dict) else str(payload)
    return any(text.startswith(prefix) or (prefix in text) for prefix in ("https://", "http://", "data:image/"))


def _poll_headers(provider: dict) -> dict:
    """轮询请求头（带 Authorization if present）。"""
    key = provider.get("api_key", "")
    if key and not key.startswith("Bearer "):
        key = f"Bearer {key}"
    headers = {}
    if key:
        headers["Authorization"] = key
    return headers
