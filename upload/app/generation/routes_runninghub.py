"""
RunningHub 路由 — /api/runninghub/*
"""

import time
from fastapi import APIRouter, HTTPException
from ..system.providers import get_provider
from ..core.http_client import create_client
from ..config import RUNNINGHUB_OPENAPI_BASE_URL, RUNNINGHUB_DEFAULT_BASE_URL

router = APIRouter(prefix="/api/runninghub", tags=["runninghub"])


async def _rh_provider():
    p = await get_provider("runninghub")
    if not p:
        raise HTTPException(400, "RunningHub 供应商未配置")
    return p


async def _rh_headers(provider: dict) -> dict:
    key = provider.get("api_key", "")
    return {"Authorization": f"Bearer {key}"} if key else {}


# ---- 工作流 ----

@router.get("/workflows")
async def list_workflows():
    provider = await _rh_provider()
    url = f"{RUNNINGHUB_OPENAPI_BASE_URL}/workflow/list"
    async with create_client("normal") as client:
        resp = await client.get(url, headers=await _rh_headers(provider))
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, f"获取失败: {resp.text[:200]}")
    return resp.json()


@router.get("/workflows/{workflow_id}")
async def get_workflow(workflow_id: str):
    provider = await _rh_provider()
    url = f"{RUNNINGHUB_OPENAPI_BASE_URL}/workflow/detail"
    async with create_client("normal") as client:
        resp = await client.post(url, json={"workflowId": workflow_id}, headers=await _rh_headers(provider))
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, f"获取失败: {resp.text[:200]}")
    return resp.json()


@router.post("/workflows/submit")
async def submit_workflow(req: dict):
    provider = await _rh_provider()
    url = f"{RUNNINGHUB_OPENAPI_BASE_URL}/workflow/submit"
    async with create_client("long") as client:
        resp = await client.post(url, json=req, headers=await _rh_headers(provider))
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, f"提交失败: {resp.text[:200]}")
    return resp.json()


# ---- AI 应用 ----

@router.get("/apps")
async def list_apps():
    provider = await _rh_provider()
    url = f"{RUNNINGHUB_OPENAPI_BASE_URL}/webapp/list"
    async with create_client("normal") as client:
        resp = await client.get(url, headers=await _rh_headers(provider))
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, f"获取失败: {resp.text[:200]}")
    return resp.json()


@router.post("/apps/submit")
async def submit_app(req: dict):
    provider = await _rh_provider()
    url = f"{RUNNINGHUB_OPENAPI_BASE_URL}/webapp/submit"
    async with create_client("long") as client:
        resp = await client.post(url, json=req, headers=await _rh_headers(provider))
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, f"提交失败: {resp.text[:200]}")
    return resp.json()


# ---- 任务查询 ----

@router.post("/task/query")
async def query_task(req: dict):
    provider = await _rh_provider()
    url = f"{RUNNINGHUB_OPENAPI_BASE_URL}/task/query"
    async with create_client("normal") as client:
        resp = await client.post(url, json=req, headers=await _rh_headers(provider))
    if resp.status_code != 200:
        raise HTTPException(502, f"查询失败: {resp.text[:200]}")
    return resp.json()


# ---- 模型注册表 ----

@router.get("/models")
async def list_models():
    provider = await _rh_provider()
    from ..config import RUNNINGHUB_LLM_BASE_URL, RUNNINGHUB_MODEL_REGISTRY_URL

    # 优先从 LLM 网关获取
    try:
        async with create_client("quick") as client:
            resp = await client.get(
                f"{RUNNINGHUB_LLM_BASE_URL}/models",
                headers=await _rh_headers(provider),
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        pass

    # 回退 GitHub 注册表
    try:
        async with create_client("normal") as client:
            resp = await client.get(RUNNINGHUB_MODEL_REGISTRY_URL)
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        pass

    raise HTTPException(502, "无法获取模型列表")


# ---- 别名 ----

@router.post("/workflow-submit")
async def submit_workflow_alias(req: dict):
    provider = await _rh_provider()
    url = f"{RUNNINGHUB_OPENAPI_BASE_URL}/workflow/submit"
    async with create_client("long") as client:
        resp = await client.post(url, json=req, headers=await _rh_headers(provider))
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, f"提交失败: {resp.text[:200]}")
    return resp.json()


@router.post("/submit")
async def submit_app_alias(req: dict):
    provider = await _rh_provider()
    url = f"{RUNNINGHUB_OPENAPI_BASE_URL}/webapp/submit"
    async with create_client("long") as client:
        resp = await client.post(url, json=req, headers=await _rh_headers(provider))
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, f"提交失败: {resp.text[:200]}")
    return resp.json()


# ---- 上传 ----

@router.post("/upload-asset")
async def upload_asset(req: dict):
    provider = await _rh_provider()
    url = f"{RUNNINGHUB_OPENAPI_BASE_URL}/workflow/upload"
    async with create_client("normal") as client:
        resp = await client.post(url, json=req, headers=await _rh_headers(provider))
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, f"上传失败: {resp.text[:200]}")
    return resp.json()


# ---- 工作流获取 ----

@router.post("/workflows/fetch")
async def fetch_workflow(req: dict):
    provider = await _rh_provider()
    url = f"{RUNNINGHUB_OPENAPI_BASE_URL}/workflow/fetch"
    async with create_client("normal") as client:
        resp = await client.post(url, json=req, headers=await _rh_headers(provider))
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, f"获取失败: {resp.text[:200]}")
    return resp.json()


# ---- 应用详情 ----

@router.get("/app-info")
async def get_app_info(webappId: str):
    provider = await _rh_provider()
    url = f"{RUNNINGHUB_OPENAPI_BASE_URL}/webapp/detail"
    async with create_client("normal") as client:
        resp = await client.post(url, json={"webappId": webappId}, headers=await _rh_headers(provider))
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, f"获取失败: {resp.text[:200]}")
    return resp.json()


@router.get("/workflow-info")
async def get_workflow_info(workflowId: str):
    """返回画布节点读取工作流字段所需的兼容结构。"""
    raw = await get_workflow(workflowId)
    data = raw.get("data", raw) if isinstance(raw, dict) else {}
    if not isinstance(data, dict):
        data = {"raw": raw}
    data.setdefault("workflowId", workflowId)
    return {"success": True, "data": data}


# ---- 任务快捷查询 ----

@router.get("/query")
async def query_task_by_id(taskId: str):
    provider = await _rh_provider()
    url = f"{RUNNINGHUB_OPENAPI_BASE_URL}/task/query"
    async with create_client("normal") as client:
        resp = await client.post(url, json={"taskId": taskId}, headers=await _rh_headers(provider))
    if resp.status_code != 200:
        raise HTTPException(502, f"查询失败: {resp.text[:200]}")
    return resp.json()
