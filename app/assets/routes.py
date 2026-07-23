"""资产库路由 — /api/asset-library, /api/prompt-libraries"""

import asyncio
import time
import uuid
from pathlib import Path
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .library import (
    load_asset_library, save_asset_library,
    store_asset, resolve_asset, delete_stored_asset, move_asset,
    create_library, rename_library, delete_library,
    create_category, update_category, delete_category,
    add_url_item, update_item,
    batch_add_items, batch_delete_items, classify_items,
    store_workflow, resolve_workflow,
    rebuild_asset_index, rebuild_workflow_index,
)
from .library import load_prompt_library, save_prompt_library
from ..core.websocket import manager as ws_manager
from ..config import WORKFLOW_LIBRARY_DIR

router = APIRouter(prefix="/api", tags=["assets"])


# ══════════════════════════════════════════════════════════════════
# 内部工具
# ══════════════════════════════════════════════════════════════════

def _wrap(lib: dict) -> dict:
    return {"library": lib}


def _public_asset(item: dict) -> dict:
    asset = {key: value for key, value in item.items() if key not in ("path",)}
    asset["source"] = "library"
    asset["url"] = f"/api/asset-library/items/{asset['id']}/content"
    return asset


# ══════════════════════════════════════════════════════════════════
# Asset Library — 读取
# ══════════════════════════════════════════════════════════════════

@router.get("/asset-library")
async def get_asset_library():
    lib = await load_asset_library()
    return _wrap(lib)


# ══════════════════════════════════════════════════════════════════
# Asset Library — Libraries CRUD
# ══════════════════════════════════════════════════════════════════

@router.post("/asset-library/libraries")
async def create_library_route(req: dict):
    result = await create_library(req.get("name", "未命名库"))
    await ws_manager.broadcast_asset_library_updated(int(time.time() * 1000))
    lib = await load_asset_library()
    return {"library": lib, "asset_library": {"id": result["id"]}}


@router.patch("/asset-library/libraries/{library_id}")
async def update_library_route(library_id: str, req: dict):
    if "name" in req:
        await rename_library(library_id, req["name"])
    await ws_manager.broadcast_asset_library_updated(int(time.time() * 1000))
    lib = await load_asset_library()
    return _wrap(lib)


@router.delete("/asset-library/libraries/{library_id}")
async def delete_library_route(library_id: str):
    await delete_library(library_id)
    await ws_manager.broadcast_asset_library_updated(int(time.time() * 1000))
    lib = await load_asset_library()
    return _wrap(lib)


# ══════════════════════════════════════════════════════════════════
# Asset Library — Categories CRUD
# ══════════════════════════════════════════════════════════════════

@router.post("/asset-library/categories")
async def create_category_route(req: dict):
    result = await create_category(
        req.get("library_id", ""),
        req.get("name", "未命名分类"),
        req.get("type", "image"),
    )
    await ws_manager.broadcast_asset_library_updated(int(time.time() * 1000))
    lib = await load_asset_library()
    return {"library": lib, "category": {"id": result["id"]}}


@router.patch("/asset-library/categories/{category_id}")
async def update_category_route(category_id: str, req: dict):
    await update_category(category_id, req.get("name"), req.get("type"))
    await ws_manager.broadcast_asset_library_updated(int(time.time() * 1000))
    lib = await load_asset_library()
    return _wrap(lib)


@router.delete("/asset-library/categories/{category_id}")
async def delete_category_route(category_id: str):
    await delete_category(category_id)
    await ws_manager.broadcast_asset_library_updated(int(time.time() * 1000))
    lib = await load_asset_library()
    return _wrap(lib)


# ══════════════════════════════════════════════════════════════════
# Asset Library — Items CRUD
# ══════════════════════════════════════════════════════════════════

@router.post("/asset-library/items/upload", status_code=201)
async def upload_asset_item(
    library_id: str = Form(...),
    category_id: str = Form(...),
    file: UploadFile = File(...),
):
    content = await file.read()
    if not content:
        raise HTTPException(400, "上传文件不能为空")
    try:
        item = await store_asset(
            library_id=library_id,
            category_id=category_id,
            original_name=file.filename or "upload",
            content=content,
            mime=file.content_type or "application/octet-stream",
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    await ws_manager.broadcast_asset_library_updated(int(time.time() * 1000))
    return {"item": _public_asset(item)}


@router.get("/asset-library/items/{item_id}/content")
async def get_asset_content(item_id: str):
    item = await resolve_asset(item_id)
    if item is None:
        raise HTTPException(404, "素材不存在或已删除")
    path = item.get("path")
    if not path or not path.is_file():
        raise HTTPException(404, "素材文件不存在")
    return FileResponse(
        path,
        media_type=item.get("mime") or "application/octet-stream",
        filename=item.get("original_name") or item.get("name") or "asset",
    )


@router.get("/asset-library/items/{item_id}/resolve")
async def resolve_asset_item(item_id: str):
    item = await resolve_asset(item_id)
    if item is None:
        raise HTTPException(404, "素材不存在或已删除")
    return {"item": _public_asset(item)}


@router.post("/asset-library/items")
async def add_asset_item(req: dict):
    item = await add_url_item(
        req.get("category_id", ""),
        {
            "name": req.get("name", "未命名"),
            "url": req.get("url", ""),
            "kind": req.get("kind", "image"),
            "tags": req.get("tags", []),
            "mime": req.get("mime", ""),
        },
    )
    await ws_manager.broadcast_asset_library_updated(int(time.time() * 1000))
    lib = await load_asset_library()
    return {"library": lib, "item": item}


@router.patch("/asset-library/items/{item_id}")
async def update_asset_item(item_id: str, req: dict):
    allowed = ("name", "tags", "classification", "caption", "caption_provider", "url")
    updates = {k: req[k] for k in allowed if k in req}
    if updates:
        await update_item(item_id, updates)
    await ws_manager.broadcast_asset_library_updated(int(time.time() * 1000))
    lib = await load_asset_library()
    return _wrap(lib)


@router.delete("/asset-library/items/{item_id}")
async def delete_asset_item(item_id: str):
    try:
        await delete_stored_asset(item_id)
    except FileNotFoundError:
        pass
    await ws_manager.broadcast_asset_library_updated(int(time.time() * 1000))
    lib = await load_asset_library()
    return _wrap(lib)


# ══════════════════════════════════════════════════════════════════
# Asset Library — 批量操作
# ══════════════════════════════════════════════════════════════════

@router.post("/asset-library/items/batch")
async def batch_add_items_route(req: dict):
    items = req.get("items", [])
    created = []
    for item_data in items:
        item = await add_url_item(req.get("category_id", ""), item_data)
        created.append(item)
    await ws_manager.broadcast_asset_library_updated(int(time.time() * 1000))
    lib = await load_asset_library()
    return {"library": lib, "items": created}


@router.post("/asset-library/items/delete")
async def batch_delete_items_route(req: dict):
    ids = req.get("ids", [])
    removed = await batch_delete_items(ids)
    await ws_manager.broadcast_asset_library_updated(int(time.time() * 1000))
    lib = await load_asset_library()
    return {"library": lib, "removed": removed}


@router.post("/asset-library/items/move")
async def move_items(req: dict):
    ids = req.get("ids", [])
    target_category_id = req.get("target_category_id", "")
    moved = 0
    for item_id in ids:
        try:
            await move_asset(item_id, target_category_id=target_category_id)
            moved += 1
        except (FileNotFoundError, ValueError):
            continue
    await ws_manager.broadcast_asset_library_updated(int(time.time() * 1000))
    lib = await load_asset_library()
    return {"library": lib, "moved": moved}


@router.post("/asset-library/items/classify")
async def classify_items_route(req: dict):
    names = req.get("names", [])
    classifications = [{"id": name, "classification": {"flat": []}} for name in names]
    await classify_items(classifications)
    return {"library": await load_asset_library(), "items": [{"ok": True} for _ in names], "count": len(names)}


# ══════════════════════════════════════════════════════════════════
# Asset Library — 头像注册（桩）
# ══════════════════════════════════════════════════════════════════

@router.post("/asset-library/items/{item_id}/register-avatar")
async def register_avatar(item_id: str, req: dict):
    return {"library": await load_asset_library()}


@router.post("/asset-library/items/{item_id}/avatar-status")
async def avatar_status(item_id: str, req: dict):
    return {"library": await load_asset_library(), "item": {"id": item_id, "registrations": {}}}


# ══════════════════════════════════════════════════════════════════
# Asset Library — 工作流
# ══════════════════════════════════════════════════════════════════

@router.post("/asset-library/workflows/upload")
async def upload_workflows(
    library_id: str = Form(...),
    category_id: str = Form(""),
    files: list = File(...),
):
    created = []
    for f in files:
        ext = Path(f.filename).suffix.lower() if f.filename else ".bin"
        if ext not in {".json", ".zip"}:
            raise HTTPException(400, "工作流仅支持 JSON 或 ZIP 文件")
        item = await store_workflow(
            library_id=library_id,
            category_id=category_id or "",
            name=f.filename or "workflow",
            content=await f.read(),
            ext=ext,
        )
        created.append(item)

    await ws_manager.broadcast_asset_library_updated(int(time.time() * 1000))
    lib = await load_asset_library()
    return {"library": lib, "items": created}


@router.get("/asset-library/workflows/{item_id}/content")
async def get_workflow_content(item_id: str):
    item = await resolve_workflow(item_id)
    if item is None:
        raise HTTPException(404, "工作流不存在")
    path = item.get("path")
    if not path or not path.is_file():
        raise HTTPException(404, "工作流文件不存在")
    return FileResponse(path, filename=path.name)


@router.post("/asset-library/workflows/{item_id}/install")
async def install_workflow(item_id: str):
    import shutil
    from ..config import WORKFLOW_DIR

    item = await resolve_workflow(item_id)
    if item is None:
        raise HTTPException(404, "工作流不存在")
    source = item["path"]
    if source.suffix != ".json":
        raise HTTPException(400, "仅 JSON 工作流可安装")
    target = WORKFLOW_DIR / f"{item_id}.json"
    await asyncio.to_thread(shutil.copy2, source, target)
    return {"installed": True, "name": target.name}


# ══════════════════════════════════════════════════════════════════
# Prompt Library — 不变
# ══════════════════════════════════════════════════════════════════

@router.get("/prompt-libraries")
async def get_prompt_libraries():
    lib = await load_prompt_library()
    return _wrap(lib)


@router.post("/prompt-libraries")
async def create_prompt_library(req: dict):
    lib = await load_prompt_library()
    new_lib = {
        "id": uuid.uuid4().hex,
        "name": req.get("name", "未命名提示词库"),
        "system": False,
        "readonly": False,
        "categories": [],
        "items": [],
    }
    lib.setdefault("libraries", []).append(new_lib)
    if not lib.get("active_library_id"):
        lib["active_library_id"] = new_lib["id"]
    await save_prompt_library(lib)
    return _wrap(lib)


@router.patch("/prompt-libraries/{library_id}")
async def update_prompt_library(library_id: str, req: dict):
    lib = await load_prompt_library()
    for l in lib.get("libraries", []):
        if l.get("id") == library_id and "name" in req:
            l["name"] = req["name"]
            break
    await save_prompt_library(lib)
    return _wrap(lib)


@router.delete("/prompt-libraries/{library_id}")
async def delete_prompt_library(library_id: str):
    lib = await load_prompt_library()
    lib["libraries"] = [l for l in lib.get("libraries", []) if l.get("id") != library_id]
    await save_prompt_library(lib)
    return _wrap(lib)


@router.post("/prompt-libraries/categories")
async def create_prompt_category(req: dict):
    lib = await load_prompt_library()
    library_id = req.get("library_id", "")
    target = None
    for l in lib.get("libraries", []):
        if l.get("id") == library_id:
            target = l
            break
    if not target and lib.get("libraries"):
        target = lib["libraries"][0]
    if target:
        target.setdefault("categories", []).append({
            "id": uuid.uuid4().hex,
            "name": req.get("name", "未命名分类"),
        })
    await save_prompt_library(lib)
    return _wrap(lib)


@router.patch("/prompt-libraries/categories/{category_id}")
async def update_prompt_category(category_id: str, req: dict):
    lib = await load_prompt_library()
    for l in lib.get("libraries", []):
        for c in l.get("categories", []):
            if c.get("id") == category_id and "name" in req:
                c["name"] = req["name"]
                break
    await save_prompt_library(lib)
    return _wrap(lib)


@router.delete("/prompt-libraries/categories/{category_id}")
async def delete_prompt_category(category_id: str):
    lib = await load_prompt_library()
    for l in lib.get("libraries", []):
        l["categories"] = [c for c in l.get("categories", []) if c.get("id") != category_id]
    await save_prompt_library(lib)
    return _wrap(lib)


@router.post("/prompt-libraries/items")
async def add_prompt_item(req: dict):
    lib = await load_prompt_library()
    new_item = {
        "id": uuid.uuid4().hex,
        "name": req.get("name", ""),
        "positive": req.get("positive", ""),
        "negative": req.get("negative", ""),
        "scene": req.get("scene", ""),
        "category": req.get("category", ""),
        "params": req.get("params", {}),
    }
    library_id = req.get("library_id")
    for l in lib.get("libraries", []):
        if not library_id or l.get("id") == library_id:
            l.setdefault("items", []).append(new_item)
            break
    await save_prompt_library(lib)
    return {"library": lib, "item": new_item}


@router.patch("/prompt-libraries/items/{item_id}")
async def update_prompt_item(item_id: str, req: dict):
    lib = await load_prompt_library()
    for l in lib.get("libraries", []):
        for item in l.get("items", []):
            if item.get("id") == item_id:
                for key in ("name", "positive", "negative", "scene", "category"):
                    if key in req:
                        item[key] = req[key]
                if "params" in req:
                    item["params"] = req["params"]
                break
    await save_prompt_library(lib)
    return _wrap(lib)


@router.delete("/prompt-libraries/items/{item_id}")
async def delete_prompt_item(item_id: str):
    lib = await load_prompt_library()
    for l in lib.get("libraries", []):
        l["items"] = [i for i in l.get("items", []) if i.get("id") != item_id]
    await save_prompt_library(lib)
    return _wrap(lib)


@router.post("/prompt-libraries/items/delete")
async def batch_delete_prompt_items(req: dict):
    lib = await load_prompt_library()
    ids = set(req.get("ids", []))
    for l in lib.get("libraries", []):
        l["items"] = [i for i in l.get("items", []) if i.get("id") not in ids]
    await save_prompt_library(lib)
    return _wrap(lib)


@router.get("/smart-canvas/prompt-templates")
async def smart_canvas_prompt_templates():
    return {"templates": []}
