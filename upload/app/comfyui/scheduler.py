"""ComfyUI 多实例调度和工作流文件管理。"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import time
from pathlib import Path
from urllib.parse import urlencode

from fastapi import HTTPException

from ..config import COMFYUI_DOWNLOAD_TIMEOUT, WORKFLOW_DIR
from ..core.http_client import create_client
from ..core.io import write_text_atomic_sync
from ..core.paths import safe_path_join, validate_simple_filename


class ComfyUIUnavailableError(RuntimeError):
    """没有可用的 ComfyUI 后端。"""


class ComfyUIScheduler:
    """ComfyUI 多实例负载均衡器。"""

    def __init__(self):
        configured = os.getenv("COMFYUI_INSTANCES", "127.0.0.1:8188").split(",")
        self.instances = self._normalize_instances(configured)
        self._load_lock = asyncio.Lock()
        self._load: dict[str, int] = {address: 0 for address in self.instances}

    @staticmethod
    def _normalize_instances(instances: list[str]) -> list[str]:
        result: list[str] = []
        for value in instances:
            address = str(value or "").strip().rstrip("/")
            if address and address not in result:
                result.append(address)
        return result

    @staticmethod
    def _queue_length(value) -> int:
        if isinstance(value, int):
            return max(0, value)
        if isinstance(value, (list, tuple)):
            return len(value)
        return 0

    async def update_instances(self, instances: list[str]) -> list[str]:
        normalized = self._normalize_instances(instances)
        async with self._load_lock:
            previous = self._load
            self.instances = normalized
            self._load = {address: previous.get(address, 0) for address in normalized}
        return list(normalized)

    async def get_best_backend(self) -> str:
        """并发探测实例，在锁内只完成负载比较和预留。"""
        async with self._load_lock:
            instances = list(self.instances)
            reserved = dict(self._load)
        if not instances:
            raise ComfyUIUnavailableError("未配置 ComfyUI 实例")

        results = await asyncio.gather(
            *(self._query_queue(address) for address in instances),
            return_exceptions=True,
        )
        candidates: list[tuple[int, int, str]] = []
        for order, (address, result) in enumerate(zip(instances, results)):
            if isinstance(result, BaseException):
                continue
            running = self._queue_length(result.get("queue_running", 0))
            pending = self._queue_length(result.get("queue_pending", 0))
            candidates.append((reserved.get(address, 0) + running + pending, order, address))

        if not candidates:
            raise ComfyUIUnavailableError("所有 ComfyUI 实例均不可用")

        _, _, best = min(candidates)
        async with self._load_lock:
            if best not in self.instances:
                raise ComfyUIUnavailableError("ComfyUI 实例配置已变化，请重试")
            self._load[best] = self._load.get(best, 0) + 1
        return best

    async def release_backend(self, address: str) -> None:
        async with self._load_lock:
            if address in self._load:
                self._load[address] = max(0, self._load[address] - 1)

    async def _query_queue(self, address: str) -> dict:
        async with create_client("quick") as client:
            response = await client.get(f"http://{address}/queue")
        if response.status_code != 200:
            raise ComfyUIUnavailableError(f"ComfyUI {address} 返回 {response.status_code}")
        data = response.json()
        if not isinstance(data, dict):
            raise ComfyUIUnavailableError(f"ComfyUI {address} 队列响应无效")
        return data

    async def queue_status(self) -> dict:
        """返回所有实例的真实队列快照，不可用实例单独标记。"""
        async with self._load_lock:
            instances = list(self.instances)
            reserved = dict(self._load)
        results = await asyncio.gather(
            *(self._query_queue(address) for address in instances),
            return_exceptions=True,
        )
        backends = []
        running = pending = 0
        for address, result in zip(instances, results):
            if isinstance(result, BaseException):
                backends.append({"address": address, "available": False, "reserved": reserved.get(address, 0)})
                continue
            backend_running = self._queue_length(result.get("queue_running", 0))
            backend_pending = self._queue_length(result.get("queue_pending", 0))
            running += backend_running
            pending += backend_pending
            backends.append({
                "address": address,
                "available": True,
                "running": backend_running,
                "pending": backend_pending,
                "reserved": reserved.get(address, 0),
            })
        return {"queue": backends, "running": running, "pending": pending}

    async def submit_workflow(self, workflow: dict | str, params: dict | None = None) -> str:
        """提交工作流到最佳实例，返回 prompt_id。"""
        if isinstance(workflow, str):
            workflow = json.loads(workflow)
        if not isinstance(workflow, dict):
            raise ValueError("工作流必须是 JSON 对象")
        prompt = copy.deepcopy(workflow)
        for node_id, node_params in (params or {}).items():
            node = prompt.get(str(node_id))
            if isinstance(node, dict) and isinstance(node.get("inputs"), dict) and isinstance(node_params, dict):
                node["inputs"].update(node_params)

        address = await self.get_best_backend()
        try:
            async with create_client("long") as client:
                response = await client.post(f"http://{address}/prompt", json={"prompt": prompt})
            if response.status_code != 200:
                raise RuntimeError(f"ComfyUI 提交失败：{response.text[:200]}")
            data = response.json()
            prompt_id = str(data.get("prompt_id") or "")
            if not prompt_id:
                raise RuntimeError("ComfyUI 未返回 prompt_id")
        except Exception:
            await self.release_backend(address)
            raise

        asyncio.create_task(self._watch_and_release(address, prompt_id))
        return prompt_id

    async def _watch_and_release(self, address: str, prompt_id: str) -> None:
        try:
            deadline = time.monotonic() + 1800
            while time.monotonic() < deadline:
                try:
                    async with create_client("quick") as client:
                        response = await client.get(f"http://{address}/history/{prompt_id}")
                    if response.status_code == 200 and prompt_id in response.json():
                        break
                except Exception:
                    pass
                await asyncio.sleep(2)
        finally:
            await self.release_backend(address)

    async def download_output(
        self,
        address: str,
        filename: str,
        subfolder: str = "",
        output_type: str = "output",
    ) -> bytes:
        params = urlencode({"filename": filename, "subfolder": subfolder, "type": output_type})
        async with create_client("normal") as client:
            response = await client.get(
                f"http://{address}/view?{params}",
                timeout=COMFYUI_DOWNLOAD_TIMEOUT,
            )
        if response.status_code != 200:
            raise RuntimeError(f"下载失败：{response.status_code}")
        return response.content

    @staticmethod
    def _workflow_path(name: str) -> Path:
        filename = validate_simple_filename(name, suffix=".json")
        return safe_path_join(WORKFLOW_DIR, filename)

    def list_workflow_files(self) -> list[dict]:
        workflows: list[dict] = []
        if not WORKFLOW_DIR.exists():
            return workflows
        for path in WORKFLOW_DIR.glob("*.json"):
            if path.name.endswith(".config.json") or not path.is_file():
                continue
            stat = path.stat()
            workflows.append({
                "name": path.name,
                "path": path.name,
                "size": stat.st_size,
                "modified": int(stat.st_mtime * 1000),
            })
        workflows.sort(key=lambda item: item["name"].casefold())
        return workflows

    def load_workflow(self, name: str) -> dict:
        path = self._workflow_path(name)
        if not path.is_file():
            raise FileNotFoundError(f"工作流 {name} 不存在")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("工作流必须是 JSON 对象")
        return data

    def save_workflow_file(self, name: str, workflow_json: str) -> None:
        path = self._workflow_path(name)
        data = json.loads(workflow_json)
        if not isinstance(data, dict):
            raise ValueError("工作流必须是 JSON 对象")
        normalized = json.dumps(data, ensure_ascii=False, indent=2)
        write_text_atomic_sync(path, normalized)

    def delete_workflow_file(self, name: str) -> None:
        path = self._workflow_path(name)
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def workflow_config_path(self, name: str) -> Path:
        workflow_name = validate_simple_filename(name, suffix=".json")
        return safe_path_join(WORKFLOW_DIR, f"{workflow_name}.config.json")

    def save_workflow_config(self, name: str, config: dict) -> None:
        if not isinstance(config, dict):
            raise ValueError("工作流配置必须是 JSON 对象")
        write_text_atomic_sync(
            self.workflow_config_path(name),
            json.dumps(config, ensure_ascii=False, indent=2),
        )

    def load_workflow_config(self, name: str) -> dict | None:
        path = self.workflow_config_path(name)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("工作流配置必须是 JSON 对象")
        return data

    def delete_workflow_config(self, name: str) -> None:
        try:
            self.workflow_config_path(name).unlink()
        except FileNotFoundError:
            pass


scheduler = ComfyUIScheduler()
