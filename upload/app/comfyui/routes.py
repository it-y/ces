"""ComfyUI 路由"""

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .models import ComfyInstancesPayload, WorkflowRunRequest
from .scheduler import ComfyUIUnavailableError, scheduler


class WorkflowSaveRequest(BaseModel):
    name: str
    workflow: str


router = APIRouter(prefix="/api", tags=["comfyui"])


@router.get("/workflows")
async def list_workflows():
    return {"workflows": await asyncio.to_thread(scheduler.list_workflow_files)}


@router.post("/workflows")
async def save_workflow(req: WorkflowSaveRequest):
    try:
        await asyncio.to_thread(scheduler.save_workflow_file, req.name, req.workflow)
        return {"ok": True, "name": req.name}
    except HTTPException:
        raise
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, f"保存工作流失败: {exc}") from exc


@router.get("/workflows/{name}")
async def get_workflow(name: str):
    try:
        return await asyncio.to_thread(scheduler.load_workflow, name)
    except HTTPException:
        raise
    except FileNotFoundError as exc:
        raise HTTPException(404, "工作流未找到") from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(400, f"加载工作流失败: {exc}") from exc


@router.put("/workflows/{name}/config")
async def save_workflow_config(name: str, req: dict):
    try:
        await asyncio.to_thread(scheduler.save_workflow_config, name, req)
        return {"config": req}
    except HTTPException:
        raise
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, f"保存配置失败: {exc}") from exc


@router.get("/workflows/{name}/config")
async def get_workflow_config(name: str):
    try:
        config = await asyncio.to_thread(scheduler.load_workflow_config, name)
    except HTTPException:
        raise
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(400, f"加载配置失败: {exc}") from exc
    if config is None:
        raise HTTPException(404, "配置未找到")
    return config


@router.delete("/workflows/{name}")
async def delete_workflow(name: str):
    try:
        await asyncio.to_thread(scheduler.delete_workflow_file, name)
        await asyncio.to_thread(scheduler.delete_workflow_config, name)
        return {"ok": True}
    except HTTPException:
        raise
    except OSError as exc:
        raise HTTPException(500, f"删除工作流失败: {exc}") from exc


async def _submit(workflow: dict | str, params: dict) -> dict:
    try:
        prompt_id = await scheduler.submit_workflow(workflow, params)
        return {"prompt_id": prompt_id, "status": "submitted"}
    except ComfyUIUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(400, f"提交工作流失败: {exc}") from exc
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/workflows/run")
async def run_workflow(req: WorkflowRunRequest):
    return await _submit(req.workflow_id, req.params)


@router.post("/workflows/{name}/run")
async def run_named_workflow(name: str, payload: dict):
    try:
        workflow = await asyncio.to_thread(scheduler.load_workflow, name)
    except HTTPException:
        raise
    except FileNotFoundError as exc:
        raise HTTPException(404, "工作流未找到") from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(400, f"加载工作流失败: {exc}") from exc
    params = payload.get("params") or payload.get("fields") or {}
    return await _submit(workflow, params)


@router.get("/comfyui/instances")
async def get_instances():
    return {"instances": list(scheduler.instances)}


@router.put("/comfyui/instances")
async def update_instances(payload: ComfyInstancesPayload):
    instances = await scheduler.update_instances(payload.instances)
    return {"instances": instances}
