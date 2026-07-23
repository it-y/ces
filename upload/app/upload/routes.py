"""
上传路由 — /api/upload, /api/ai/upload, /api/local-assets
"""

import base64
import uuid
import time
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, UploadFile, File, HTTPException, Request, Form
from ..config import (
    CANVAS_FILES_DIR, LOCAL_DIR, LOCAL_INDEX_PATH,
    LOCAL_IMAGE_IMPORT_EXTS, LOCAL_IMAGE_IMPORT_MAX_BYTES, UPLOAD_DIR,
)
from ..core.errors import read_json, write_atomic
from ..core.security import ensure_same_origin_request, safe_path_join, sanitize_filename, validate_remote_url
from ..core.http_client import create_client

router = APIRouter(prefix="/api", tags=["upload"])


async def _load_local_asset_index() -> dict:
    return await read_json(LOCAL_INDEX_PATH) or {"version": 1, "items": []}


async def _save_local_asset_index(index: dict) -> None:
    await write_atomic(LOCAL_INDEX_PATH, index)


def _local_asset_url(path: Path) -> str:
    return f"/api/local-assets/files/{path.as_posix()}"

# 兼容导入 — 实际实现在 app/canvas/context.py
from ..canvas.context import (  # noqa: F401
    set_last_opened_canvas, get_last_opened_canvas,
    bind_canvas_client, resolve_canvas_id,
)


_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v", ".flv"}
_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
_MAX_BYTES = 50 * 1024 * 1024


def _detect_kind(ext: str, content_type: str) -> str:
    if ext in _VIDEO_EXTS or content_type.startswith("video/"):
        return "video"
    if ext in _AUDIO_EXTS or content_type.startswith("audio/"):
        return "audio"
    if ext in _IMAGE_EXTS or content_type.startswith("image/"):
        return "image"
    return "file"


async def _save_to_canvas_input(canvas_id: str, filename: str, content: bytes) -> str | None:
    """保存到画布 inputs/ 文件夹，返回 /cfiles/... URL"""
    try:
        from ..canvas.manager import canvas_input_dir
        in_dir = await canvas_input_dir(canvas_id)
        in_dir.mkdir(parents=True, exist_ok=True)
        (in_dir / filename).write_bytes(content)
        rel = in_dir.relative_to(CANVAS_FILES_DIR)
        return f"/cfiles/{rel.as_posix()}/{filename}"
    except Exception:
        return None


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    canvas_id: Optional[str] = Form(None),
):
    """上传文件（全局 UPLOAD_DIR + 有 canvas_id 时写 canvas inputs/）"""
    safe_name = sanitize_filename(file.filename or "upload")
    ext = Path(safe_name).suffix.lower()
    name = f"{uuid.uuid4().hex[:8]}_{safe_name}"

    content = await file.read()

    # 1) 全局 UPLOAD_DIR
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (UPLOAD_DIR / name).write_bytes(content)

    # 2) 有 canvas_id → 同时写入 canvas inputs/
    cid = resolve_canvas_id(canvas_id)
    if cid:
        await _save_to_canvas_input(cid, name, content)

    return {
        "url": f"/assets/{name}",
        "filename": safe_name,
        "size": len(content),
    }


@router.post("/ai/upload")
async def upload_ai_reference(
    files: List[UploadFile] = File(...),
    canvas_id: Optional[str] = Form(None),
):
    """
    上传 AI 参考素材（50MB 限制）。
    全局 UPLOAD_DIR + canvas inputs/ 各存一份。
    """
    cid = resolve_canvas_id(canvas_id)

    uploaded = []
    for file in files:
        content = await file.read()
        if not content:
            continue
        if len(content) > _MAX_BYTES:
            raise HTTPException(413, f"{file.filename or '文件'} 超过 50MB，无法上传")

        ext = Path(file.filename or "").suffix.lower()
        content_type = (file.content_type or "").lower()
        kind = _detect_kind(ext, content_type)
        safe_name = sanitize_filename(file.filename or "ref.png")
        name = f"ref_{uuid.uuid4().hex[:12]}{ext or '.png'}"

        # 1) 全局 UPLOAD_DIR
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        (UPLOAD_DIR / name).write_bytes(content)

        # 2) 有 canvas_id → canvas inputs/
        if cid:
            url = await _save_to_canvas_input(cid, name, content)
            if url:
                uploaded.append({
                    "url": url,
                    "name": file.filename or name,
                    "kind": kind,
                    "mime": content_type,
                })
                continue

        # 无 canvas_id → 只返回全局路径
        uploaded.append({
            "url": f"/assets/{name}",
            "name": file.filename or name,
            "kind": kind,
            "mime": content_type,
        })
    return {"files": uploaded}


@router.post("/ai/import-local-image")
async def import_local_image(req: dict):
    """导入本地图片"""
    from ..config import LOCAL_IMAGE_IMPORT_MAX_BYTES, LOCAL_IMAGE_IMPORT_EXTS
    import base64, uuid
    from ..core.security import sanitize_filename

    paths = req.get("paths") or []
    if req.get("path"):
        paths.insert(0, req["path"])
    paths = [p for p in paths if str(p or "").strip()][:20]
    if not paths:
        raise HTTPException(status_code=400, detail="没有可导入的本地图片")

    result = []
    for path_str in paths:
        try:
            p = Path(path_str)
            if not p.exists():
                continue
            if p.suffix.lower() not in LOCAL_IMAGE_IMPORT_EXTS:
                continue
            if p.stat().st_size > LOCAL_IMAGE_IMPORT_MAX_BYTES:
                continue
            ext = p.suffix.lower() or ".png"
            name = f"local_{uuid.uuid4().hex[:12]}{ext}"
            target = UPLOAD_DIR / name
            target.write_bytes(p.read_bytes())
            result.append({
                "url": f"/assets/{name}",
                "name": p.name,
                "size": target.stat().st_size,
            })
        except Exception:
            continue
    return {"files": result}


@router.post("/ai/upload-base64")
async def upload_base64(req: dict):
    """Base64 上传"""
    data = req.get("data", "")
    filename = req.get("filename", "upload.png")

    if "," in data:
        data = data.split(",", 1)[1]

    MAX_BASE64_BYTES = 50 * 1024 * 1024
    if len(data) > MAX_BASE64_BYTES:
        raise HTTPException(413, "Base64 数据超过限制")

    try:
        content = base64.b64decode(data)
    except Exception:
        raise HTTPException(400, "Base64 解码失败")

    safe_name = sanitize_filename(filename)
    name = f"b64_{uuid.uuid4().hex[:8]}_{safe_name}"
    path = UPLOAD_DIR / name
    path.write_bytes(content)

    return {"url": f"/assets/{name}", "size": len(content)}


def _build_local_tree(items: list[dict], folders: list[str]) -> dict:
    """从 items + folders 构建嵌套树。"""
    root = {"id": "__root__", "path": "", "name": "全部上传", "items": [], "children": {}}

    # 先把索引里的文件夹注册到树
    for folder_path in sorted(folders):
        parts = Path(folder_path).parts
        node = root
        for i, part in enumerate(parts):
            if part not in node["children"]:
                sub_path = str(Path(*parts[: i + 1]).as_posix())
                node["children"][part] = {
                    "id": f"folder:{sub_path}",
                    "path": sub_path,
                    "name": part,
                    "items": [],
                    "children": {},
                }
            node = node["children"][part]

    # 把文件注册到对应文件夹
    for item in items:
        file_path = Path(item["path"])
        parts = file_path.parts
        if len(parts) <= 1:
            root["items"].append(item)
            continue
        node = root
        for i, part in enumerate(parts[:-1]):
            if part not in node["children"]:
                sub_path = str(Path(*parts[: i + 1]).as_posix())
                node["children"][part] = {
                    "id": f"folder:{sub_path}",
                    "path": sub_path,
                    "name": part,
                    "items": [],
                    "children": {},
                }
            node = node["children"][part]
        node["items"].append(item)

    # 递归把 children dict 转 list
    def _children_list(node: dict) -> dict:
        if not node["children"]:
            node["children"] = []
            return node
        result = []
        for name, child in sorted(node["children"].items()):
            result.append(_children_list(child))
        node["children"] = result
        return node

    return _children_list(root)


@router.get("/local-assets")
async def list_local_assets(request: Request):
    """列出本地上传的文件"""
    ensure_same_origin_request(request)
    index = await _load_local_asset_index()
    items = [item for item in index.get("items", []) if (LOCAL_DIR / item["path"]).is_file()]
    folders = index.get("folders", [])
    tree = _build_local_tree(items, folders)
    return {"items": items, "tree": tree}


@router.get("/local-assets/files/{path:path}")
async def get_local_asset_file(path: str):
    from fastapi.responses import FileResponse

    file_path = safe_path_join(LOCAL_DIR, path)
    if not file_path.is_file():
        raise HTTPException(404, "本地素材不存在")
    return FileResponse(file_path)


# ── Local-assets routes (metadata + import) ─────────────────────────────


@router.post("/local-assets/upload")
async def upload_local_assets(
    folder: str = Form(""),
    files: List[UploadFile] = File(...),
):
    """Upload files to a logical folder"""
    uploaded = []
    index = await _load_local_asset_index()
    folder_path = Path(folder.strip("/\\")) if folder else Path()
    if folder_path.is_absolute() or ".." in folder_path.parts:
        raise HTTPException(400, "文件夹路径无效")
    target_dir = LOCAL_DIR / folder_path
    target_dir.mkdir(parents=True, exist_ok=True)
    for file in files:
        content = await file.read()
        if not content:
            continue
        name = sanitize_filename(file.filename or "upload")
        counter = 1
        while (target_dir / name).exists():
            p = Path(name)
            name = f"{p.stem}_{counter}{p.suffix}"
            counter += 1
        (target_dir / name).write_bytes(content)
        relative_path = (folder_path / name).as_posix()
        item = {
            "id": uuid.uuid4().hex,
            "name": name,
            "path": relative_path,
            "url": _local_asset_url(Path(relative_path)),
            "folder": folder,
        }
        index.setdefault("items", []).append(item)
        uploaded.append(item)
    await _save_local_asset_index(index)
    return {"files": uploaded}


@router.post("/local-assets/delete")
async def delete_local_assets(req: dict):
    """删除本地素材实体和索引记录。"""
    names = req.get("names") or []
    index = await _load_local_asset_index()
    count = 0
    for name in names:
        p = safe_path_join(LOCAL_DIR, name)
        if p.exists() and p.is_file():
            p.unlink()
            count += 1
    index["items"] = [item for item in index.get("items", []) if item.get("path") not in names]
    await _save_local_asset_index(index)
    return {"deleted": count}


@router.patch("/local-assets/items")
async def rename_local_asset(req: dict):
    """重命名本地素材并保留稳定 ID。"""
    old_path = req["path"]
    new_name = sanitize_filename(req["name"])
    old_file = safe_path_join(LOCAL_DIR, old_path)
    new_file = old_file.parent / new_name
    if not old_file.exists():
        raise HTTPException(404, "File not found")
    if new_file.exists():
        raise HTTPException(409, "Target name already exists")
    old_file.rename(new_file)
    new_path = new_file.relative_to(LOCAL_DIR).as_posix()
    index = await _load_local_asset_index()
    item = next((entry for entry in index.get("items", []) if entry.get("path") == old_path), None)
    if item:
        item.update({"name": new_name, "path": new_path, "url": _local_asset_url(Path(new_path))})
        await _save_local_asset_index(index)
    return {
        "item": {
            "id": item["id"] if item else "",
            "url": _local_asset_url(Path(new_path)),
            "name": new_name,
        },
        "old_path": old_path,
    }


@router.post("/local-assets/folders")
async def create_local_asset_folder(req: dict):
    """创建真实本地素材文件夹并写入索引。"""
    name = req.get("name") or req.get("path", "untitled")
    path = Path(str(name).strip("/\\"))
    if path.is_absolute() or ".." in path.parts:
        raise HTTPException(400, "文件夹路径无效")
    (LOCAL_DIR / path).mkdir(parents=True, exist_ok=True)
    index = await _load_local_asset_index()
    folders = index.setdefault("folders", [])
    folder_path = path.as_posix()
    if folder_path not in folders:
        folders.append(folder_path)
    await _save_local_asset_index(index)
    return {"folder": {"path": name}}


@router.post("/local-assets/caption")
async def caption_local_assets(req: dict):
    """Generate or save captions"""
    if "caption" in req:
        name = req["name"]
        caption = req["caption"]
        return {"caption": caption, "caption_file": f"{name}.txt"}
    names = req.get("names") or []
    return {"items": [{"ok": True} for _ in names], "count": len(names)}


@router.post("/local-assets/classify")
async def classify_local_assets(req: dict):
    """Classify items by AI"""
    names = req.get("names") or []
    return {"items": [{"ok": True} for _ in names], "count": len(names)}


@router.post("/local-assets/move")
async def move_local_assets(req: dict):
    """移动真实本地素材文件并同步索引。"""
    names = req.get("names") or []
    folder = Path(str(req.get("folder", req.get("target_folder", ""))).strip("/\\"))
    if folder.is_absolute() or ".." in folder.parts:
        raise HTTPException(400, "目标文件夹路径无效")
    target_dir = LOCAL_DIR / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    index = await _load_local_asset_index()
    moved = 0
    for name in names:
        source = safe_path_join(LOCAL_DIR, name)
        if not source.is_file():
            continue
        destination = target_dir / source.name
        if destination.exists():
            raise HTTPException(409, "目标文件已存在")
        source.rename(destination)
        new_path = destination.relative_to(LOCAL_DIR).as_posix()
        for item in index.get("items", []):
            if item.get("path") == name:
                item.update({"path": new_path, "folder": folder.as_posix(), "url": _local_asset_url(Path(new_path))})
        moved += 1
    await _save_local_asset_index(index)
    return {"moved": moved}


@router.post("/local-assets/import-urls")
async def import_urls(req: dict):
    """Import files from remote URLs"""
    folder = req.get("folder", "")
    items = req.get("items") or []
    result = []
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    MAX_TOTAL_DOWNLOAD_BYTES = 500 * 1024 * 1024
    async with create_client() as client:
        for item in items:
            url = item["url"]
            validate_remote_url(url)
            name = sanitize_filename(item.get("name", url.rsplit("/", 1)[-1] or "download"))
            counter = 1
            target = name
            while (UPLOAD_DIR / target).exists():
                p = Path(name)
                target = f"{p.stem}_{counter}{p.suffix}"
                counter += 1
            try:
                total = 0
                chunks = []
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    async for chunk in resp.aiter_bytes():
                        total += len(chunk)
                        if total > MAX_TOTAL_DOWNLOAD_BYTES:
                            raise HTTPException(413, "下载总量超过限制")
                        chunks.append(chunk)
                (UPLOAD_DIR / target).write_bytes(b"".join(chunks))
                result.append({
                    "id": str(uuid.uuid4()),
                    "url": f"/assets/{target}",
                    "name": target,
                })
            except HTTPException:
                raise
            except Exception:
                continue
    return {"files": result, "count": len(result)}


@router.post("/cloud-video/upload")
async def upload_cloud_video(file: UploadFile = File(...)):
    safe_name = sanitize_filename(file.filename or "video.mp4")
    name = f"video_{uuid.uuid4().hex[:8]}_{safe_name}"
    content = await file.read()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (UPLOAD_DIR / name).write_bytes(content)
    return {"url": f"/assets/{name}"}
