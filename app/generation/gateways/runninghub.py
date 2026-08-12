"""
RunningHub 网关 — 工作流/AI应用/模型调用。

RunningHub 是项目最复杂的网关：
  - 支持 app:ID 和 workflow:ID 两种模型引用
  - 7 种字段类型自动推断（IMAGE/VIDEO/AUDIO/SLIDER/NUMBER/BOOLEAN/TEXT）
  - seed 字段自动随机化
  - OpenAPI v2 任务提交 + 轮询
  - LLM 网关（模型列表、fallback）
  - 模型注册表（3 来源：OpenAPI/GitHub/local）
"""

import asyncio
import json
import random
import time
import uuid
from ...core.http_client import create_client
from ...config import (
    RUNNINGHUB_OPENAPI_BASE_URL, RUNNINGHUB_LLM_BASE_URL,
    RUNNINGHUB_DEFAULT_BASE_URL, IMAGE_TASK_TIMEOUT, IMAGE_POLL_INTERVAL,
)


class RunningHubGateway:
    """RunningHub 生成网关"""

    def __init__(self, provider: dict):
        self.provider = provider

    # ---- 主入口 ----

    async def generate(
        self, prompt: str, size: str = "", model: str = "",
        quality: str = "", n: int = 1,
        reference_images: list | None = None,
    ) -> list[str]:
        """通过 RH workflow/app 生成图片"""
        entry = self._parse_model_entry(model)
        if not entry:
            raise ValueError(f"RunningHub 模型格式错误：{model}，请使用 app:ID 或 workflow:ID")

        if entry["kind"] == "app":
            return await self._run_app(entry, prompt, reference_images)
        else:
            return await self._run_workflow(entry, prompt, reference_images)

    # ---- 模型解析 ----

    def _parse_model_entry(self, model: str) -> dict | None:
        """
        解析模型字符串：
          "app:2511"        → { kind: "app", id: "2511" }
          "workflow:abc123"  → { kind: "workflow", id: "abc123" }
        """
        if model.startswith("app:"):
            return {"kind": "app", "id": model[4:]}
        if model.startswith("workflow:"):
            return {"kind": "workflow", "id": model[9:]}
        # 从 provider 配置里匹配
        for app in self.provider.get("rh_apps", []):
            if model in (app.get("id"), app.get("name")):
                return {"kind": "app", "id": app["id"], "config": app}
        for wf in self.provider.get("rh_workflows", []):
            if model in (wf.get("workflowId"), wf.get("title")):
                return {"kind": "workflow", "id": wf["workflowId"], "config": wf}
        return None

    # ---- App 模式 ----

    async def _run_app(self, entry: dict, prompt: str, refs: list | None) -> list[str]:
        url = f"{RUNNINGHUB_OPENAPI_BASE_URL}/webapp/submit"
        body = {
            "webappId": entry["id"],
            "nodeInfoList": self._build_node_info(prompt, entry.get("config"), refs),
            "instanceType": "public",
            "useWallet": self.provider.get("use_wallet", False),
        }
        return await self._submit_and_poll(url, body, "image")

    # ---- Workflow 模式 ----

    async def _run_workflow(self, entry: dict, prompt: str, refs: list | None) -> list[str]:
        url = f"{RUNNINGHUB_OPENAPI_BASE_URL}/workflow/submit"
        body = {
            "workflowId": entry["id"],
            "nodeInfoList": self._build_node_info(prompt, entry.get("config"), refs),
            "useWallet": self.provider.get("use_wallet", False),
        }
        return await self._submit_and_poll(url, body, "image")

    # ---- 提交 + 轮询 ----

    async def _submit_and_poll(self, url: str, body: dict, output_kind: str = "image") -> list[str]:
        api_key = self._api_key()
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        } if api_key else {"Content-Type": "application/json"}

        from ...core.http_client import request_with_fallback
        resp = await request_with_fallback("POST", url, timeout_preset="long", json=body, headers=headers)

        if resp.status_code != 200:
            raise Exception(f"RunningHub 提交失败：{resp.text[:200]}")

        data = resp.json()
        task_id = data.get("data", {}).get("taskId") or data.get("taskId", "")

        if not task_id:
            raise Exception(f"RunningHub 未返回 taskId")

        # 轮询
        poll_url = f"{RUNNINGHUB_OPENAPI_BASE_URL}/task/query"
        deadline = time.time() + IMAGE_TASK_TIMEOUT

        while time.time() < deadline:
            async with create_client("normal") as client:
                poll_resp = await client.post(poll_url, json={"taskId": task_id}, headers=headers)
            if poll_resp.status_code != 200:
                await asyncio.sleep(IMAGE_POLL_INTERVAL)
                continue
            poll_data = poll_resp.json()
            status = poll_data.get("data", {}).get("status") or poll_data.get("status", "")
            if status in ("done", "succeeded", "success", "completed"):
                return self._extract_outputs(poll_data)
            if status in ("failed", "error", "cancelled"):
                raise Exception(poll_data.get("data", {}).get("failReason", "RunningHub 生成失败"))
            await asyncio.sleep(IMAGE_POLL_INTERVAL)

        raise Exception("RunningHub 任务超时")

    # ---- 节点参数构造 ----

    def _build_node_info(self, prompt: str, config: dict | None, refs: list | None) -> list:
        """根据 workflow/app 的字段定义构造 nodeInfoList"""
        fields = (config or {}).get("fields", [])
        nodes = []
        for field in fields:
            value = self._field_default(field)
            # 自动填充 prompt 字段
            if self._is_prompt_field(field):
                value = prompt
            # 自动填充 seed 字段
            if self._is_seed_field(field):
                value = random.randint(0, 4294967295)
            # 自动填充参考图
            if self._is_image_field(field) and refs:
                value = refs[0].get("url", "") if refs else ""
            nodes.append({field.get("fieldName", "unknown"): value})
        return nodes

    def _field_default(self, field: dict) -> any:
        kind = field.get("kind", "").upper()
        if kind == "NUMBER":
            return field.get("default", 0)
        if kind == "SLIDER":
            return field.get("default", field.get("min", 0))
        if kind == "BOOLEAN":
            return field.get("default", False)
        return field.get("default", "")

    def _is_prompt_field(self, field: dict) -> bool:
        name = (field.get("fieldName", "") + field.get("label", "")).lower()
        return "prompt" in name or "text" in name

    def _is_seed_field(self, field: dict) -> bool:
        name = (field.get("fieldName", "") + field.get("label", "")).lower()
        return "seed" in name or "noise" in name

    def _is_image_field(self, field: dict) -> bool:
        kind = (field.get("kind", "") + field.get("type", "")).upper()
        return kind in ("IMAGE", "PICTURE", "UPLOADIMAGE")

    # ---- 输出提取 ----

    def _extract_outputs(self, data: dict) -> list[str]:
        outputs = data.get("data", {}).get("outputs", [])
        urls = []
        for item in outputs:
            if isinstance(item, dict):
                for v in item.values():
                    if isinstance(v, str) and v.startswith("http"):
                        urls.append(v)
        if not urls:
            # 深度遍历
            urls = self._deep_find_urls(data)
        return urls

    def _deep_find_urls(self, data, depth=0) -> list[str]:
        if depth > 8:
            return []
        urls = []
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, str) and v.startswith("http") and any(
                    ext in v.lower() for ext in (".png", ".jpg", ".mp4", ".webp", ".gif")
                ):
                    urls.append(v)
                urls.extend(self._deep_find_urls(v, depth + 1))
        elif isinstance(data, list):
            for item in data:
                urls.extend(self._deep_find_urls(item, depth + 1))
        return urls

    def _api_key(self) -> str:
        return self.provider.get("api_key", "")
