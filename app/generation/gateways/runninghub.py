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
import base64
import json
import random
import time
import uuid
from ...core.http_client import create_client, retry_request
from .openai import ImageGenerationError
from ...config import (
    RUNNINGHUB_OPENAPI_BASE_URL, IMAGE_TASK_TIMEOUT, IMAGE_POLL_INTERVAL,
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

        # 上传参考图（本地路径/data URL → RunningHub 内部 fileName）
        uploaded_refs = await self._upload_references(reference_images or [])

        if entry["kind"] == "app":
            return await self._run_app(entry, prompt, uploaded_refs)
        else:
            return await self._run_workflow(entry, prompt, uploaded_refs)

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

    # ---- 参考图上传 ----

    async def _upload_references(self, refs: list) -> list[dict]:
        """
        上传参考图到 RunningHub。本地路径/data URL 需要先上传得到 fileName，
        远程 URL 直接传递。
        返回格式与输入一致: [{"url": "fileName_or_url", ...}, ...]
        """
        if not refs:
            return []
        uploaded = []
        for ref in refs:
            url = ref.get("url", "") if isinstance(ref, dict) else str(ref)
            if not url:
                uploaded.append(ref)
                continue

            # 远程 URL 直接传递
            if url.startswith("http://") or url.startswith("https://"):
                uploaded.append(ref)
                continue

            # 本地路径 / data URL → 上传
            try:
                img_bytes = await self._fetch_ref_bytes(url)
                if not img_bytes:
                    uploaded.append(ref)
                    continue

                b64 = base64.b64encode(img_bytes).decode("ascii")
                mime = ref.get("mime", "image/png") if isinstance(ref, dict) else "image/png"
                ext = mime.split("/")[-1] if "/" in mime else "png"

                upload_url = f"{RUNNINGHUB_OPENAPI_BASE_URL}/workflow/upload"
                upload_body = {
                    "imageBase64": f"data:{mime};base64,{b64}",
                    "fileName": f"ref_{uuid.uuid4().hex[:8]}.{ext}",
                }
                headers = {"Content-Type": "application/json"}
                if self._api_key():
                    headers["Authorization"] = f"Bearer {self._api_key()}"

                resp = await retry_request("POST", upload_url, json=upload_body, headers=headers)
                if resp.status_code == 200:
                    result = resp.json()
                    # RH 上传返回 fileName 或 url
                    filename = (
                        result.get("data", {}).get("fileName")
                        or result.get("fileName")
                        or result.get("data", {}).get("url")
                    )
                    if filename:
                        uploaded.append({"url": filename, **{k: v for k, v in (ref if isinstance(ref, dict) else {}).items() if k != "url"}})
                        continue
            except Exception:
                pass
            uploaded.append(ref)
        return uploaded

    @staticmethod
    async def _fetch_ref_bytes(url: str) -> bytes | None:
        """获取参考图字节（支持 data URL、本地路径、远程 URL）"""
        if not url:
            return None
        if url.startswith("data:"):
            try:
                _, encoded = url.split(",", 1)
                return base64.b64decode(encoded)
            except Exception:
                return None
        if url.startswith("/"):
            from pathlib import Path
            from ...config import UPLOAD_DIR, OUTPUT_DIR, CANVAS_FILES_DIR
            path_part = url.split("?")[0]
            for root in (CANVAS_FILES_DIR, UPLOAD_DIR, OUTPUT_DIR):
                local = Path(root) / path_part.lstrip("/").split("/", 1)[-1] if "/" in path_part.lstrip("/") else Path(root) / path_part.lstrip("/")
                try:
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

        resp = await retry_request("POST", url, json=body, headers=headers)

        if resp.status_code != 200:
            raise ImageGenerationError(f"RunningHub 提交失败：{resp.text[:200]}", resp.status_code)

        data = resp.json()
        task_id = data.get("data", {}).get("taskId") or data.get("taskId", "")

        if not task_id:
            raise ImageGenerationError(f"RunningHub 未返回 taskId", 502)

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
                raise ImageGenerationError(poll_data.get("data", {}).get("failReason", "RunningHub 生成失败"), 502)
            await asyncio.sleep(IMAGE_POLL_INTERVAL)

        raise ImageGenerationError("RunningHub 任务超时", 504)

    # ---- 节点参数构造 ----

    def _build_node_info(self, prompt: str, config: dict | None, refs: list | None) -> list:
        """根据 workflow/app 的字段定义构造 nodeInfoList，自动填充 schema 默认值"""
        fields = (config or {}).get("fields", [])
        if not fields:
            return [{"prompt": prompt}]

        nodes = []
        ref_idx = 0
        for field in fields:
            field_name = field.get("fieldName", "unknown")
            kind = (field.get("kind", "") + field.get("type", "")).upper()

            # 自动填充 prompt
            if self._is_prompt_field(field):
                value = prompt
            # 自动填充 seed
            elif self._is_seed_field(field):
                value = random.randint(0, 4294967295)
            # 自动填充参考图
            elif kind in ("IMAGE", "PICTURE", "UPLOADIMAGE"):
                if refs and ref_idx < len(refs):
                    value = refs[ref_idx].get("url", "") if isinstance(refs[ref_idx], dict) else str(refs[ref_idx])
                    ref_idx += 1
                else:
                    value = field.get("default", "")
            # 自动填充视频参考
            elif kind in ("VIDEO", "UPLOADVIDEO"):
                value = field.get("default", "")
            # 自动填充音频参考
            elif kind in ("AUDIO", "UPLOADAUDIO"):
                value = field.get("default", "")
            # 按 kind 应用默认值
            elif kind == "NUMBER":
                value = field.get("default", 0)
                if value is None:
                    value = 0
            elif kind == "SLIDER":
                value = field.get("default", field.get("min", 0))
                if value is None:
                    value = field.get("min", 0)
            elif kind == "BOOLEAN":
                default = field.get("default", False)
                if isinstance(default, str):
                    default = default.lower() in ("true", "1", "yes")
                value = default
            elif kind == "CHECKBOX":
                default = field.get("default", False)
                if isinstance(default, str):
                    default = default.lower() in ("true", "1", "yes")
                value = default
            elif kind == "TEXT":
                value = field.get("default", "")
                if value is None:
                    value = ""
            else:
                value = field.get("default", "")

            nodes.append({field_name: value})

        return nodes

    def _is_prompt_field(self, field: dict) -> bool:
        name = (field.get("fieldName", "") + field.get("label", "")).lower()
        # 精确匹配 prompt/text，避免误匹配 texture/context
        if name in ("prompt", "text", "positive_prompt"):
            return True
        return name.startswith("prompt") or name.endswith("_prompt")

    def _is_seed_field(self, field: dict) -> bool:
        name = (field.get("fieldName", "") + field.get("label", "")).lower()
        # 精确匹配 seed，避免误匹配 denoise
        if name in ("seed", "noise_seed", "random_seed"):
            return True
        return name.startswith("seed_") or name.endswith("_seed")

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
                    ext in v.lower() for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff",
                                                 ".mp4", ".webm", ".mov", ".avi", ".mkv")
                ):
                    urls.append(v)
                urls.extend(self._deep_find_urls(v, depth + 1))
        elif isinstance(data, list):
            for item in data:
                urls.extend(self._deep_find_urls(item, depth + 1))
        return urls

    def _api_key(self) -> str:
        return self.provider.get("api_key", "")
