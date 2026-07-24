"""
画布路由 — /api/canvases, /api/projects
"""

import asyncio
import time
from fastapi import APIRouter, HTTPException, UploadFile

from .models import (
    CanvasCreateRequest, CanvasMetaUpdate, CanvasSaveRequest,
    CanvasAssetCheckRequest, CanvasAssetDownloadRequest,
    ProjectCreateRequest, ProjectUpdateRequest,
)
from .manager import (
    create_canvas, load_canvas, load_canvas_any,
    save_canvas, update_canvas_meta,
    delete_canvas, restore_canvas, purge_canvas,
    list_canvases, list_deleted_canvases,
    extract_canvas_assets, canvas_resource_dir,
    list_projects, create_project, update_project, delete_project,
    CanvasConflictError,
)
from ..core.websocket import manager as ws_manager
from .import_export import (
    build_workflow_zip, check_canvas_assets, import_canvas_file,
    pack_canvas_assets, parse_workflow_zip,
)

router = APIRouter(prefix="/api", tags=["canvas"])


# ============================================================
# 画布
# ============================================================

@router.get("/canvases")
async def api_list_canvases(trash: bool = False):
    """列出画布（默认不含回收站）"""
    if trash:
        return {"canvases": await list_deleted_canvases()}
    return {"canvases": await list_canvases()}


@router.get("/canvases/trash")
async def api_list_trash():
    """回收站列表"""
    return {"canvases": await list_deleted_canvases()}


@router.get("/canvas-assets")
async def api_canvas_assets_index():
    """全量画布资产索引"""
    all_assets = []
    canvases = await list_canvases()
    for c in canvases:
        try:
            full = await load_canvas(c["id"])
            for asset in extract_canvas_assets(full):
                asset["canvas_id"] = c["id"]
                asset["canvas_title"] = c["title"]
                all_assets.append(asset)
        except Exception:
            pass
    return {
        "categories": [
            {"id": canvas["id"], "name": canvas["title"], "type": "canvas"}
            for canvas in canvases
        ],
        "canvases": canvases,
        "items": all_assets,
    }


@router.post("/canvases")
async def api_create_canvas(req: CanvasCreateRequest):
    """创建画布"""
    canvas = await create_canvas(
        title=req.title,
        icon=req.icon,
        kind=req.kind,
        project=req.project,
        board_x=req.board_x,
        board_y=req.board_y,
    )
    return {"canvas": canvas}


@router.post("/canvases/import")
async def api_import_canvas(file: UploadFile, project_id: str = ""):
    raw = await file.read()
    pid = project_id.strip() or None
    canvas = await import_canvas_file(raw, file.filename, pid)
    return {"canvas": canvas}


@router.get("/canvases/{canvas_id}")
async def api_get_canvas(canvas_id: str):
    """获取画布完整数据"""
    from .context import set_last_opened_canvas
    set_last_opened_canvas(canvas_id)
    try:
        canvas = await load_canvas(canvas_id)
        return {"canvas": canvas}
    except FileNotFoundError:
        raise HTTPException(404, "画布不存在或已在回收站")


@router.put("/canvases/{canvas_id}")
async def api_save_canvas(canvas_id: str, req: CanvasSaveRequest):
    """保存画布内容（带乐观锁）"""
    try:
        from .context import bind_canvas_client
        bind_canvas_client(req.client_id, canvas_id)
        result = await save_canvas(
            canvas_id=canvas_id,
            nodes=req.nodes,
            connections=req.connections,
            viewport=req.viewport,
            logs=req.logs,
            settings=req.settings,
            title=req.title,
            icon=req.icon,
            base_updated_at=req.base_updated_at,
        )
        # WebSocket 广播
        await ws_manager.broadcast_canvas_updated(
            canvas_id=canvas_id,
            updated_at=result["updated_at"],
            client_id=req.client_id,
        )
        return {"canvas": result}
    except CanvasConflictError as e:
        raise HTTPException(409, {"detail": str(e), "updated_at": e.current_updated_at})
    except FileNotFoundError:
        raise HTTPException(404, "画布不存在")


@router.get("/canvases/{canvas_id}/meta")
async def api_get_meta(canvas_id: str):
    """读取画布元数据（前端轮询用）"""
    try:
        canvas = await load_canvas(canvas_id)
        return {
            "id": canvas.get("id"),
            "updated_at": canvas.get("updated_at", 0),
            "title": canvas.get("title", ""),
            "icon": canvas.get("icon", "layers"),
            "kind": canvas.get("kind", "classic"),
        }
    except FileNotFoundError:
        raise HTTPException(404, "画布不存在")


@router.post("/canvases/{canvas_id}/meta")
async def api_update_meta(canvas_id: str, req: CanvasMetaUpdate):
    """更新画布元数据（不修改 updated_at）"""
    try:
        result = await update_canvas_meta(
            canvas_id=canvas_id,
            title=req.title,
            icon=req.icon,
            owner=req.owner,
            color=req.color,
            pinned=req.pinned,
            project=req.project,
            board_x=req.board_x,
            board_y=req.board_y,
        )
        return {"canvas": result}
    except FileNotFoundError:
        raise HTTPException(404, "画布不存在")


@router.delete("/canvases/{canvas_id}")
async def api_delete_canvas(canvas_id: str):
    """软删除（移到回收站）"""
    try:
        return await delete_canvas(canvas_id)
    except FileNotFoundError:
        raise HTTPException(404, "画布不存在")


@router.post("/canvases/{canvas_id}/restore")
async def api_restore_canvas(canvas_id: str):
    """从回收站恢复"""
    try:
        return await restore_canvas(canvas_id)
    except FileNotFoundError:
        raise HTTPException(404, "画布不存在")


@router.post("/canvases/{canvas_id}/touch")
async def api_touch_canvas(canvas_id: str):
    """刷新画布 updated_at（前端定时 touch）"""
    from .context import set_last_opened_canvas
    set_last_opened_canvas(canvas_id)
    try:
        canvas = await load_canvas(canvas_id)
        result = await save_canvas(canvas_id, nodes=canvas.get("nodes"), base_updated_at=canvas.get("updated_at"))
        return {"ok": True, "updated_at": result["updated_at"]}
    except CanvasConflictError:
        return {"ok": False}
    except FileNotFoundError:
        raise HTTPException(404, "画布不存在")


@router.delete("/canvases/{canvas_id}/purge")
async def api_purge_canvas(canvas_id: str):
    """永久删除"""
    try:
        await purge_canvas(canvas_id)
        return {"ok": True}
    except FileNotFoundError:
        raise HTTPException(404, "画布不存在")


@router.get("/canvases/{canvas_id}/assets")
async def api_list_canvas_assets(canvas_id: str):
    """列出画布中的所有媒体资产"""
    try:
        canvas = await load_canvas(canvas_id)
        return extract_canvas_assets(canvas)
    except FileNotFoundError:
        raise HTTPException(404, "画布不存在")


@router.post("/canvas-assets/check")
async def api_check_canvas_assets(req: CanvasAssetCheckRequest):
    return {"exists": await check_canvas_assets(req.urls)}


@router.post("/canvas-assets/download")
async def api_download_canvas_assets(req: CanvasAssetDownloadRequest):
    from fastapi.responses import Response
    from urllib.parse import quote

    items = [item.model_dump() for item in req.items] if req.items else [
        {"url": url} for url in req.urls
    ]
    content, filename = await pack_canvas_assets(items, req.filename)
    return Response(
        content,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/projects")
async def api_list_projects():
    return {"projects": await list_projects()}


@router.post("/projects")
async def api_create_project(req: ProjectCreateRequest):
    proj = await create_project(req.name)
    return {"project": proj}


@router.post("/projects/{project_id}")
async def api_update_project(project_id: str, req: ProjectUpdateRequest):
    try:
        proj = await update_project(project_id, req.name, req.order)
        return {"project": proj}
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")


@router.delete("/projects/{project_id}")
async def api_delete_project(project_id: str):
    try:
        await delete_project(project_id)
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")


# ============================================================
# 画布工作流导入导出
# ============================================================


@router.post("/canvas-workflows/export")
async def export_canvas_workflow(req: dict):
    from fastapi.responses import Response
    from urllib.parse import quote

    content = await build_workflow_zip(req)
    filename = str(req.get("filename") or "canvas-workflow.zip")
    return Response(
        content,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@router.post("/canvas-workflows/import")
async def import_canvas_workflow(file: UploadFile):
    return await parse_workflow_zip(await file.read())


@router.post("/canvas-workflows/export-to-library")
async def export_workflow_to_library(req: dict):
    from ..assets.library import load_asset_library, store_workflow

    content = await build_workflow_zip(req)
    item = await store_workflow(
        library_id=req.get("library_id", ""),
        category_id=req.get("category_id", ""),
        name=req.get("name", "canvas workflow"),
        content=content,
        ext=".zip",
    )
    await ws_manager.broadcast_asset_library_updated(int(time.time() * 1000))
    return {"library": await load_asset_library(), "item": item}
