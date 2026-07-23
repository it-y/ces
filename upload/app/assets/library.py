"""
资产库管理 v4 — 目录即结构 + .meta/ 侧边文件 + .index.json 缓存。

存储结构：
  assets/
  ├── 库名/
  │   ├── .meta.json              ← 库元数据 { id, name }
  │   ├── 分类名/
  │   │   ├── .meta.json          ← 分类元数据 { id, name, type }
  │   │   ├── .meta/
  │   │   │   └── {stem}.json     ← 每个素材的侧边元数据
  │   │   ├── 原文件名.png        ← 实体文件
  │   │   └── {item_id}.url       ← URL 素材标记文件（0 字节）
  │   └── ...
  └── .index.json                  ← 缓存索引（可重建）

  workflows/
  ├── 库名/
  │   ├── 分类名/
  │   │   ├── {uuid}/
  │   │   │   └── workflow.json
  │   │   │   └── .meta.json
  │   │   └── ...
  └── .index.json

  local/
  ├── {文件夹}/
  │   └── {文件}
  └── .index.json
"""

import asyncio
import os
import re
import uuid
import time
from pathlib import Path

from ..config import (
    ASSET_DIR, ASSET_INDEX_PATH,
    TRASH_DIR, WORKFLOW_LIBRARY_DIR, WORKFLOW_LIBRARY_INDEX_PATH,
    LEGACY_PROMPT_LIBRARY_PATH,
    PROMPT_LIBRARY_INDEX_PATH,
    PROMPT_LIBRARY_ITEMS_DIR,
    PROMPT_LIBRARY_PATH,
)
from ..core.errors import read_json, write_atomic, now_ms
from ..core.security import sanitize_filename

_asset_lock = asyncio.Lock()
_prompt_lock = asyncio.Lock()


# ══════════════════════════════════════════════════════════════════
# 辅助
# ══════════════════════════════════════════════════════════════════


def _sanitize_dirname(name: str) -> str:
    name = name.strip().replace("\\", "/").split("/")[-1]
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"[\x00-\x1f]", "", name)
    return name or "untitled"


def _unique_path(dir_path: Path, name: str) -> Path:
    p = dir_path / name
    if not p.exists():
        return p
    stem = p.stem
    ext = p.suffix
    counter = 1
    while (dir_path / f"{stem}_{counter}{ext}").exists():
        counter += 1
    return dir_path / f"{stem}_{counter}{ext}"


def _guess_kind(mime: str) -> str:
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    return "file"


def _remove_directory(path: Path) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════
# 索引重建（真相源是文件系统）
# ══════════════════════════════════════════════════════════════════


async def rebuild_asset_index() -> dict:
    """遍历 assets/ 目录 → 读 .meta/ 侧边文件 → 写 .index.json 缓存"""
    index = {
        "version": 4,
        "updated_at": now_ms(),
        "active_library_id": "",
        "id_map": {"libraries": {}, "categories": {}},
        "items": {},
    }
    if not ASSET_DIR.exists():
        await write_atomic(ASSET_INDEX_PATH, index)
        return index

    for lib_dir in sorted(ASSET_DIR.iterdir()):
        if not lib_dir.is_dir() or lib_dir.name.startswith("."):
            continue
        lib_meta = await read_json(lib_dir / ".meta.json")
        lib_id = (lib_meta.get("id") if lib_meta else None) or lib_dir.name
        lib_name = (lib_meta.get("name") if lib_meta else None) or lib_dir.name
        index["id_map"]["libraries"][lib_id] = lib_dir.name
        if not index["active_library_id"]:
            index["active_library_id"] = lib_id

        for cat_dir in sorted(lib_dir.iterdir()):
            if not cat_dir.is_dir() or cat_dir.name.startswith("."):
                continue
            cat_meta = await read_json(cat_dir / ".meta.json")
            cat_id = (cat_meta.get("id") if cat_meta else None) or cat_dir.name
            cat_name = (cat_meta.get("name") if cat_meta else None) or cat_dir.name
            cat_type = (cat_meta.get("type") if cat_meta else None) or "image"
            index["id_map"]["categories"][cat_id] = {
                "library_name": lib_dir.name,
                "category_name": cat_dir.name,
            }

            for file in sorted(cat_dir.iterdir()):
                if file.name.startswith(".") or file.name == ".meta":
                    continue
                is_url_item = file.suffix == ".url"
                stem = file.stem if not is_url_item else file.stem
                meta_file = cat_dir / ".meta" / f"{stem}.json"
                meta = (await read_json(meta_file)) if meta_file.exists() else {}

                item_id = meta.get("id") or stem
                size = meta.get("size", 0 if is_url_item else file.stat().st_size)
                created = meta.get("created_at", int(file.stat().st_ctime * 1000))
                updated = meta.get("updated_at", int(file.stat().st_mtime * 1000))

                entry = {
                    "id": item_id,
                    "library_id": lib_id,
                    "category_id": cat_id,
                    "library_name": lib_name,
                    "category_name": cat_name,
                    "library_dir": lib_dir.name,
                    "category_dir": cat_dir.name,
                    "name": meta.get("name", file.name),
                    "filename": "" if is_url_item else file.name,
                    "url": meta.get("url", f"/api/asset-library/items/{item_id}/content"),
                    "kind": meta.get("kind") or _guess_kind(meta.get("mime", "")),
                    "mime": meta.get("mime", ""),
                    "size": size,
                    "tags": meta.get("tags", []),
                    "classification": meta.get("classification", {}),
                    "caption": meta.get("caption", ""),
                    "caption_provider": meta.get("caption_provider", ""),
                    "created_at": created,
                    "updated_at": updated,
                }
                index["items"][item_id] = entry

    await write_atomic(ASSET_INDEX_PATH, index)
    return index


async def _load_index_unlocked() -> dict:
    index = await read_json(ASSET_INDEX_PATH)
    if index is None:
        index = await rebuild_asset_index()
    return index


# ══════════════════════════════════════════════════════════════════
# 响应拼装（兼容前端 v3 嵌套结构）
# ══════════════════════════════════════════════════════════════════


def _build_library_response(index: dict) -> dict:
    """将 index['items'] + id_map + workflow items 拼成 libraries→categories→items 嵌套"""
    id_map = index.get("id_map", {})
    libs_map = {}

    # 1. 从 id_map 注册所有库/分类（含空库）
    for lid in id_map.get("libraries", {}):
        libs_map[lid] = {"id": lid, "name": None, "categories": {}}

    # 2. 用 asset items 填充
    for item in index.get("items", {}).values():
        lid = item.get("library_id", "")
        cid = item.get("category_id", "")

        if lid not in libs_map:
            libs_map[lid] = {"id": lid, "name": item.get("library_name", lid), "categories": {}}
        lib = libs_map[lid]
        if lib["name"] is None:
            lib["name"] = item.get("library_name", lid)

        if cid not in lib["categories"]:
            lib["categories"][cid] = {
                "id": cid,
                "name": item.get("category_name", cid),
                "type": item.get("kind", "image"),
                "items": [],
            }
        cat = lib["categories"][cid]

        cat["items"].append({
            "id": item["id"],
            "name": item["name"],
            "kind": item["kind"],
            "url": item.get("url") or f"/api/asset-library/items/{item['id']}/content",
            "tags": item.get("tags", []),
            "classification": item.get("classification", {}),
            "caption": item.get("caption", ""),
            "created_at": item.get("created_at", 0),
        })

    # 3. 从 id_map 补充空分类 + 完善显示名
    lib_cat_registry = {}  # cid → lid
    for cid, entry in id_map.get("categories", {}).items():
        lib_dir_name = entry.get("library_name", "")
        found_lid = None
        for lid, d in id_map.get("libraries", {}).items():
            if d == lib_dir_name:
                found_lid = lid
                break
        if found_lid is None:
            continue
        lib_cat_registry[cid] = found_lid
        if found_lid not in libs_map:
            libs_map[found_lid] = {"id": found_lid, "name": None, "categories": {}}
        if cid not in libs_map[found_lid]["categories"]:
            cat_dir = ASSET_DIR / entry["library_name"] / entry["category_name"]
            cat_meta = read_json_sync(cat_dir / ".meta.json")
            libs_map[found_lid]["categories"][cid] = {
                "id": cid,
                "name": (cat_meta.get("name") if cat_meta else None) or entry["category_name"],
                "type": (cat_meta.get("type") if cat_meta else None) or "image",
                "items": [],
            }

    # 4. 补充库的显示名
    for lid, lib_dir_name in id_map.get("libraries", {}).items():
        if lid in libs_map and libs_map[lid]["name"] is None:
            lib_dir = ASSET_DIR / lib_dir_name
            lib_meta = read_json_sync(lib_dir / ".meta.json")
            libs_map[lid]["name"] = (lib_meta.get("name") if lib_meta else None) or lib_dir_name

    # 5. 合并 workflow items
    wf_index = read_json_sync(WORKFLOW_LIBRARY_INDEX_PATH)
    if wf_index:
        # 构建 (lib_name, cat_name) → category_id 反向映射
        name_to_cid = {}
        for cid, entry in id_map.get("categories", {}).items():
            key = (entry["library_name"], entry["category_name"])
            name_to_cid[key] = cid
        for wf_item in wf_index.get("items", {}).values():
            lib_name = wf_item.get("library_name", "")
            cat_name = wf_item.get("category_name", "")
            cid = name_to_cid.get((lib_name, cat_name))
            if cid is None:
                continue
            # 找到 lid → 获取 lib_id 来匹配 libs_map
            lid = lib_cat_registry.get(cid, "")
            if lid not in libs_map:
                continue
            cat = libs_map[lid]["categories"].get(cid)
            if cat is None:
                continue
            cat["items"].append({
                "id": wf_item["id"],
                "name": wf_item["name"],
                "kind": "workflow",
                "url": f"/api/asset-library/workflows/{wf_item['id']}/content",
                "tags": [],
                "classification": {},
                "caption": "",
                "created_at": wf_item.get("created_at", 0),
            })

    # 6. 排序输出
    libraries = []
    for lid in libs_map:
        lib = libs_map[lid]
        libraries.append({
            "id": lib["id"],
            "name": lib["name"] or lid,
            "categories": sorted(
                lib["categories"].values(),
                key=lambda c: c["name"],
            ),
        })

    return {
        "version": 4,
        "updated_at": index.get("updated_at", now_ms()),
        "active_library_id": index.get("active_library_id", ""),
        "libraries": libraries,
    }


def read_json_sync(path: Path) -> dict | None:
    """同步读 JSON（用于无法 await 的场合）"""
    if not path.exists():
        return None
    try:
        import json
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════
# 外部接口 — 加载 / 保存
# ══════════════════════════════════════════════════════════════════


async def load_asset_library() -> dict:
    async with _asset_lock:
        index = await _load_index_unlocked()
        return _build_library_response(index)


async def save_asset_library(lib: dict) -> None:
    async with _asset_lock:
        index = await rebuild_asset_index()
        if lib.get("active_library_id"):
            index["active_library_id"] = lib["active_library_id"]


# ══════════════════════════════════════════════════════════════════
# 库 CRUD
# ══════════════════════════════════════════════════════════════════


async def create_library(name: str) -> dict:
    async with _asset_lock:
        lib_id = uuid.uuid4().hex
        dir_name = _sanitize_dirname(name)
        lib_dir = ASSET_DIR / dir_name
        if lib_dir.exists():
            dir_name = f"{dir_name}_{uuid.uuid4().hex[:6]}"
            lib_dir = ASSET_DIR / dir_name
        lib_dir.mkdir(parents=True, exist_ok=True)
        await write_atomic(lib_dir / ".meta.json", {
            "id": lib_id, "name": name, "updated_at": now_ms(),
        })
        # 创建默认分类
        cat_id = uuid.uuid4().hex
        cat_dir = lib_dir / "默认"
        cat_dir.mkdir(parents=True, exist_ok=True)
        await write_atomic(cat_dir / ".meta.json", {
            "id": cat_id, "name": "默认", "type": "image", "updated_at": now_ms(),
        })
        await rebuild_asset_index()
        return {"id": lib_id, "name": name, "default_category_id": cat_id}


async def rename_library(library_id: str, new_name: str) -> None:
    async with _asset_lock:
        index = await _load_index_unlocked()
        id_map = index.get("id_map", {})
        dir_name = id_map.get("libraries", {}).get(library_id)
        if not dir_name:
            raise ValueError("资产库不存在")
        lib_dir = ASSET_DIR / dir_name
        await write_atomic(lib_dir / ".meta.json", {
            "id": library_id, "name": new_name, "updated_at": now_ms(),
        })
        id_map["libraries"][library_id] = dir_name
        await rebuild_asset_index()


async def delete_library(library_id: str) -> None:
    async with _asset_lock:
        index = await _load_index_unlocked()
        id_map = index.get("id_map", {})
        dir_name = id_map.get("libraries", {}).get(library_id)
        if not dir_name:
            raise ValueError("资产库不存在")
        lib_dir = ASSET_DIR / dir_name
        trash = TRASH_DIR / f"lib_{library_id}"
        trash.parent.mkdir(parents=True, exist_ok=True)
        os.replace(str(lib_dir), str(trash))
        await rebuild_asset_index()


# ══════════════════════════════════════════════════════════════════
# 分类 CRUD
# ══════════════════════════════════════════════════════════════════


async def create_category(library_id: str, name: str, type_: str = "image") -> dict:
    async with _asset_lock:
        index = await _load_index_unlocked()
        id_map = index.get("id_map", {})
        lib_dir_name = id_map.get("libraries", {}).get(library_id)
        if not lib_dir_name:
            raise ValueError("资产库不存在")
        base_dir = WORKFLOW_LIBRARY_DIR if type_ == "workflow" else ASSET_DIR
        lib_dir = base_dir / lib_dir_name
        cat_id = uuid.uuid4().hex
        dir_name = _sanitize_dirname(name)
        cat_dir = lib_dir / dir_name
        if cat_dir.exists():
            dir_name = f"{dir_name}_{uuid.uuid4().hex[:6]}"
            cat_dir = lib_dir / dir_name
        cat_dir.mkdir(parents=True, exist_ok=True)
        await write_atomic(cat_dir / ".meta.json", {
            "id": cat_id, "name": name, "type": type_, "updated_at": now_ms(),
        })
        # 如果 type_ == "workflow"，也在 ASSET_DIR 创建一份元数据记录，
        # 确保 _build_library_response 能通过 id_map 找到分类。
        if type_ == "workflow":
            asset_cat_dir = ASSET_DIR / lib_dir_name / dir_name
            asset_cat_dir.mkdir(parents=True, exist_ok=True)
            await write_atomic(asset_cat_dir / ".meta.json", {
                "id": cat_id, "name": name, "type": type_, "updated_at": now_ms(),
            })
        await rebuild_asset_index()
        return {"id": cat_id, "name": name}


async def update_category(category_id: str, req_name: str | None = None, req_type: str | None = None) -> None:
    async with _asset_lock:
        index = await _load_index_unlocked()
        id_map = index.get("id_map", {})
        entry = id_map.get("categories", {}).get(category_id)
        if not entry:
            raise ValueError("资产分类不存在")
        cat_dir = ASSET_DIR / entry["library_name"] / entry["category_name"]
        meta = (await read_json(cat_dir / ".meta.json")) or {}
        if req_name is not None:
            meta["name"] = req_name
        if req_type is not None:
            meta["type"] = req_type
        meta["updated_at"] = now_ms()
        await write_atomic(cat_dir / ".meta.json", meta)
        await rebuild_asset_index()


async def delete_category(category_id: str) -> None:
    async with _asset_lock:
        index = await _load_index_unlocked()
        id_map = index.get("id_map", {})
        entry = id_map.get("categories", {}).get(category_id)
        if not entry:
            raise ValueError("资产分类不存在")
        cat_dir = ASSET_DIR / entry["library_name"] / entry["category_name"]
        meta = await read_json(cat_dir / ".meta.json")
        cat_type = (meta.get("type") if meta else None) or "image"
        trash = TRASH_DIR / f"cat_{category_id}"
        trash.parent.mkdir(parents=True, exist_ok=True)
        os.replace(str(cat_dir), str(trash))
        if cat_type == "workflow":
            wf_cat_dir = WORKFLOW_LIBRARY_DIR / entry["library_name"] / entry["category_name"]
            wf_trash = TRASH_DIR / f"wf_cat_{category_id}"
            wf_trash.parent.mkdir(parents=True, exist_ok=True)
            if wf_cat_dir.exists():
                os.replace(str(wf_cat_dir), str(wf_trash))
        await rebuild_asset_index()


# ══════════════════════════════════════════════════════════════════
# 素材 CRUD
# ══════════════════════════════════════════════════════════════════


async def store_asset(
    *,
    library_id: str,
    category_id: str,
    original_name: str,
    content: bytes,
    mime: str,
) -> dict:
    """上传实体文件到 assets/{库}/{分类}/"""
    async with _asset_lock:
        index = await _load_index_unlocked()
        id_map = index.get("id_map", {})
        entry = id_map.get("categories", {}).get(category_id)
        if not entry:
            raise ValueError("资产分类不存在")
        cat_dir = ASSET_DIR / entry["library_name"] / entry["category_name"]
        cat_dir.mkdir(parents=True, exist_ok=True)

        safe_name = sanitize_filename(original_name) or "upload"
        dest = _unique_path(cat_dir, safe_name)

        # 原子写实体文件
        tmp = cat_dir / f".{dest.name}.tmp"
        tmp.write_bytes(content)
        os.replace(str(tmp), str(dest))

        # 写侧边元数据
        item = {
            "id": uuid.uuid4().hex,
            "name": original_name,
            "kind": _guess_kind(mime),
            "mime": mime,
            "size": len(content),
            "tags": [],
            "classification": {},
            "caption": "",
            "created_at": now_ms(),
            "updated_at": now_ms(),
        }
        meta_dir = cat_dir / ".meta"
        meta_dir.mkdir(parents=True, exist_ok=True)
        await write_atomic(meta_dir / f"{dest.stem}.json", item)

        await rebuild_asset_index()
        return item


async def add_url_item(category_id: str, item_data: dict) -> dict:
    """添加 URL 素材（无实体文件）"""
    async with _asset_lock:
        index = await _load_index_unlocked()
        id_map = index.get("id_map", {})
        entry = id_map.get("categories", {}).get(category_id)
        if not entry:
            raise ValueError("资产分类不存在")
        cat_dir = ASSET_DIR / entry["library_name"] / entry["category_name"]

        item_id = uuid.uuid4().hex
        item = {
            "id": item_id,
            "name": item_data.get("name", "未命名"),
            "url": item_data.get("url", ""),
            "kind": item_data.get("kind", "image"),
            "tags": item_data.get("tags", []),
            "mime": item_data.get("mime", ""),
            "classification": item_data.get("classification", {}),
            "caption": item_data.get("caption", ""),
            "created_at": now_ms(),
            "updated_at": now_ms(),
        }
        # .url 标记文件
        (cat_dir / f"{item_id}.url").write_text("")
        meta_dir = cat_dir / ".meta"
        meta_dir.mkdir(parents=True, exist_ok=True)
        await write_atomic(meta_dir / f"{item_id}.json", item)

        await rebuild_asset_index()
        return item


async def update_item(item_id: str, updates: dict) -> None:
    async with _asset_lock:
        index = await _load_index_unlocked()
        old = index.get("items", {}).get(item_id)
        if not old:
            raise FileNotFoundError("素材不存在")

        lib_dir_name = old.get("library_dir") or old["library_name"]
        cat_dir_name = old.get("category_dir") or old["category_name"]
        cat_dir = ASSET_DIR / lib_dir_name / cat_dir_name
        filename = old.get("filename", "")
        stem = Path(filename).stem if filename else item_id
        meta_path = cat_dir / ".meta" / f"{stem}.json"
        meta = (await read_json(meta_path)) if meta_path.exists() else {}

        for key in ("name", "tags", "classification", "caption", "caption_provider", "url"):
            if key in updates:
                meta[key] = updates[key]
        meta["updated_at"] = now_ms()

        meta_dir = cat_dir / ".meta"
        meta_dir.mkdir(parents=True, exist_ok=True)
        await write_atomic(meta_path, meta)
        await rebuild_asset_index()


async def resolve_asset(item_id: str) -> dict | None:
    index = await read_json(ASSET_INDEX_PATH)
    if not index:
        return None
    entry = index.get("items", {}).get(item_id)
    if not entry:
        return None
    if entry.get("filename"):
        lib_d = entry.get("library_dir") or entry["library_name"]
        cat_d = entry.get("category_dir") or entry["category_name"]
        cat_dir = ASSET_DIR / lib_d / cat_d
        file_path = cat_dir / entry["filename"]
        if not file_path.is_file():
            return None
        return {**entry, "path": file_path}
    return {**entry}


async def delete_stored_asset(item_id: str) -> None:
    async with _asset_lock:
        index = await _load_index_unlocked()
        entry = index.get("items", {}).get(item_id)
        if not entry:
            raise FileNotFoundError("素材不存在")

        trash = TRASH_DIR / f"item_{item_id}"
        trash.parent.mkdir(parents=True, exist_ok=True)

        lib_d = entry.get("library_dir") or entry["library_name"]
        cat_d = entry.get("category_dir") or entry["category_name"]
        cat_dir = ASSET_DIR / lib_d / cat_d
        filename = entry.get("filename", "")
        if filename:
            file_path = cat_dir / filename
            if file_path.is_file():
                os.replace(str(file_path), str(trash))
        else:
            marker = cat_dir / f"{item_id}.url"
            if marker.is_file():
                os.replace(str(marker), str(trash))

        # 删除侧边元数据
        stem = Path(filename).stem if filename else item_id
        meta_path = cat_dir / ".meta" / f"{stem}.json"
        if meta_path.exists():
            meta_path.unlink()

        await rebuild_asset_index()


async def move_asset(item_id: str, *, target_category_id: str) -> dict:
    async with _asset_lock:
        index = await _load_index_unlocked()
        id_map = index.get("id_map", {})
        entry = index.get("items", {}).get(item_id)
        if not entry:
            raise FileNotFoundError("素材不存在")

        tgt = id_map.get("categories", {}).get(target_category_id)
        if not tgt:
            raise ValueError("目标分类不存在")
        target_dir = ASSET_DIR / tgt["library_name"] / tgt["category_name"]
        target_dir.mkdir(parents=True, exist_ok=True)

        src_lib_d = entry.get("library_dir") or entry["library_name"]
        src_cat_d = entry.get("category_dir") or entry["category_name"]
        source_dir = ASSET_DIR / src_lib_d / src_cat_d
        entry_filename = entry.get("filename", "")
        stem = Path(entry_filename).stem if entry_filename else item_id

        if entry.get("filename"):
            src_file = source_dir / entry["filename"]
            src_meta = source_dir / ".meta" / f"{stem}.json"
            dst_file = _unique_path(target_dir, entry["filename"])
            if src_file.is_file():
                os.replace(str(src_file), str(dst_file))
            if src_meta.exists():
                meta_data = await read_json(src_meta)
                if meta_data:
                    target_meta_dir = target_dir / ".meta"
                    target_meta_dir.mkdir(parents=True, exist_ok=True)
                    await write_atomic(target_meta_dir / f"{dst_file.stem}.json", meta_data)
                src_meta.unlink()
        else:
            src_marker = source_dir / f"{item_id}.url"
            src_meta = source_dir / ".meta" / f"{item_id}.json"
            if src_marker.is_file():
                os.replace(str(src_marker), str(target_dir / f"{item_id}.url"))
            if src_meta.exists():
                meta_data = await read_json(src_meta)
                if meta_data:
                    target_meta_dir = target_dir / ".meta"
                    target_meta_dir.mkdir(parents=True, exist_ok=True)
                    await write_atomic(target_meta_dir / f"{item_id}.json", meta_data)
                src_meta.unlink()

        await rebuild_asset_index()
        return await resolve_asset(item_id) or entry


# ══════════════════════════════════════════════════════════════════
# 批量操作
# ══════════════════════════════════════════════════════════════════


async def batch_add_items(category_id: str, items_data: list[dict]) -> list[dict]:
    created = []
    for data in items_data:
        item = await add_url_item(category_id, data)
        created.append(item)
    return created


async def batch_delete_items(ids: list[str]) -> int:
    count = 0
    for item_id in ids:
        try:
            await delete_stored_asset(item_id)
            count += 1
        except FileNotFoundError:
            continue
    return count


async def classify_items(classifications: list[dict]) -> None:
    for entry in classifications:
        item_id = entry.get("id")
        if not item_id:
            continue
        try:
            await update_item(item_id, {"classification": entry.get("classification", {})})
        except FileNotFoundError:
            continue


# ══════════════════════════════════════════════════════════════════
# 工作流
# ══════════════════════════════════════════════════════════════════


async def store_workflow(
    library_id: str,
    category_id: str,
    name: str,
    content: bytes,
    ext: str = ".json",
) -> dict:
    """存储工作流到 workflows/{库}/{分类}/{uuid}/workflow.json"""
    async with _asset_lock:
        index = await _load_index_unlocked()
        id_map = index.get("id_map", {})
        entry = id_map.get("categories", {}).get(category_id)
        if not entry:
            raise ValueError("资产分类不存在")

        wf_dir = WORKFLOW_LIBRARY_DIR / entry["library_name"] / entry["category_name"]
        item_id = uuid.uuid4().hex
        item_dir = wf_dir / item_id
        item_dir.mkdir(parents=True, exist_ok=True)

        file_name = "workflow.json" if ext == ".json" else "package.zip"
        (item_dir / file_name).write_bytes(content)

        item = {
            "id": uuid.uuid4().hex,
            "name": name,
            "library_id": library_id,
            "category_id": category_id,
            "storage_key": f"{item_id}/{file_name}",
            "kind": "workflow",
            "created_at": now_ms(),
        }
        await write_atomic(item_dir / ".meta.json", item)

        # 刷新 worklfow index
        await rebuild_workflow_index()
        return item


async def resolve_workflow(item_id: str) -> dict | None:
    """从 workflows/.index.json 查工作流路径"""
    index = await read_json(WORKFLOW_LIBRARY_INDEX_PATH)
    if not index:
        return None
    entry = index.get("items", {}).get(item_id)
    if not entry:
        return None
    wf_dir = WORKFLOW_LIBRARY_DIR / entry["library_name"] / entry["category_name"] / item_id
    path = wf_dir / "workflow.json"
    if not path.exists():
        path = wf_dir / "package.zip"
    if not path.is_file():
        return None
    return {**entry, "path": path, "item_dir": wf_dir}


async def rebuild_workflow_index() -> dict:
    """遍历 workflows/ 重建 .index.json"""
    index = {"version": 1, "updated_at": now_ms(), "items": {}}
    if not WORKFLOW_LIBRARY_DIR.exists():
        await write_atomic(WORKFLOW_LIBRARY_INDEX_PATH, index)
        return index

    for lib_dir in sorted(WORKFLOW_LIBRARY_DIR.iterdir()):
        if not lib_dir.is_dir() or lib_dir.name.startswith("."):
            continue
        for cat_dir in sorted(lib_dir.iterdir()):
            if not cat_dir.is_dir() or cat_dir.name.startswith("."):
                continue
            for item_dir in sorted(cat_dir.iterdir()):
                if not item_dir.is_dir() or item_dir.name.startswith("."):
                    continue
                meta = await read_json(item_dir / ".meta.json")
                if not meta:
                    continue
                item_id = meta.get("id", "")
                if not item_id:
                    continue
                index["items"][item_id] = {
                    "id": item_id,
                    "name": meta.get("name", ""),
                    "library_name": lib_dir.name,
                    "category_name": cat_dir.name,
                    "storage_key": meta.get("storage_key", ""),
                    "kind": "workflow",
                    "created_at": meta.get("created_at", 0),
                }

    await write_atomic(WORKFLOW_LIBRARY_INDEX_PATH, index)
    return index


# ══════════════════════════════════════════════════════════════════
# Prompt Library（不变）
# ══════════════════════════════════════════════════════════════════


def _default_prompt_library() -> dict:
    return {
        "version": 2,
        "updated_at": now_ms(),
        "active_library_id": "default",
        "libraries": [
            {
                "id": "default",
                "name": "默认提示词库",
                "system": False,
                "readonly": False,
                "categories": [],
                "items": [],
            }
        ],
    }


def _migrate_prompt_v1_to_v2(old: dict) -> dict:
    """v1 (categories→items) → v2 (libraries→[categories,items])"""
    lib = {
        "id": "default",
        "name": "提示词库",
        "system": False,
        "readonly": False,
        "categories": [],
        "items": [],
    }
    for cat in old.get("categories", []):
        cid = cat.get("id", uuid.uuid4().hex)
        lib["categories"].append({"id": cid, "name": cat.get("name", "未命名")})
        for item in cat.get("items", []):
            item["category"] = cid
            item.setdefault("params", {})
            lib["items"].append(item)
    return {
        "version": 2,
        "updated_at": old.get("updated_at", now_ms()),
        "active_library_id": "default",
        "libraries": [lib],
    }


async def load_prompt_library() -> dict:
    async with _prompt_lock:
        lib = await read_json(PROMPT_LIBRARY_INDEX_PATH)
        legacy = await read_json(LEGACY_PROMPT_LIBRARY_PATH) if LEGACY_PROMPT_LIBRARY_PATH.exists() else None
        if legacy is not None and (lib is None or not lib.get("libraries")):
            lib = legacy
            await _write_prompt_library_unlocked(lib)
            await asyncio.to_thread(LEGACY_PROMPT_LIBRARY_PATH.unlink)
        if lib is None:
            lib = _default_prompt_library()
            await _write_prompt_library_unlocked(lib)
            return lib
        expanded = []
        for entry in lib.get("libraries", []):
            if "items" not in entry:
                file_name = entry.get("file") or f"{entry['id']}.json"
                stored = await read_json(PROMPT_LIBRARY_ITEMS_DIR / file_name)
                entry = stored or {**entry, "items": []}
            expanded.append(entry)
        lib["libraries"] = expanded
        ver = lib.get("version", 1)
        if ver < 2:
            lib = _migrate_prompt_v1_to_v2(lib)
            await _write_prompt_library_unlocked(lib)
        return lib


async def save_prompt_library(lib: dict) -> None:
    lib["updated_at"] = now_ms()
    async with _prompt_lock:
        await _write_prompt_library_unlocked(lib)


async def _write_prompt_library_unlocked(lib: dict) -> None:
    PROMPT_LIBRARY_ITEMS_DIR.mkdir(parents=True, exist_ok=True)
    existing = (await read_json(PROMPT_LIBRARY_INDEX_PATH)) or {}
    old_map = {e["id"]: e for e in existing.get("libraries", []) if e.get("id")}

    index = {key: value for key, value in lib.items() if key != "libraries"}
    index["libraries"] = []
    for entry in lib.get("libraries", []):
        item = dict(entry)
        file_name = _sanitize_dirname(item["name"]) + ".json"
        item["file"] = file_name
        # 写入完整条目
        await write_atomic(PROMPT_LIBRARY_ITEMS_DIR / file_name, item)
        # 索引条目（不含 items）
        idx_entry = {k: v for k, v in item.items() if k != "items"}
        idx_entry["file"] = file_name
        index["libraries"].append(idx_entry)
        # 清理旧文件（改名后）
        old_entry = old_map.get(item["id"])
        old_paths = set()
        if old_entry and old_entry.get("file") and old_entry["file"] != file_name:
            old_paths.add(PROMPT_LIBRARY_ITEMS_DIR / old_entry["file"])
        old_paths.add(PROMPT_LIBRARY_ITEMS_DIR / f"{item['id']}.json")
        for p in old_paths:
            if p.name != file_name and p.exists():
                p.unlink(missing_ok=True)
    await write_atomic(PROMPT_LIBRARY_INDEX_PATH, index)
