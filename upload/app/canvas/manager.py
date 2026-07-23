"""
画布管理器 — 创建、保存、列表、乐观锁、回收站、项目。

存储格式（见架构文档 §2.1）：
  data/canvases/{title}_{date}_{uuid8}.json      ← 画布数据
  data/canvas-files/{title}_{date}_{uuid8}/       ← 资源文件夹（并行）
  data/canvases/.index.json                       ← 可重建元数据索引（列表热路径）
"""

import asyncio
import re
import time
import uuid
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from ..config import (
    CANVAS_DIR, CANVAS_TRASH_DIR, CANVAS_FILES_DIR, PROJECTS_PATH,
    CANVAS_TRASH_RETENTION_MS,
    DEFAULT_PROJECT_ID,
)
from ..core.errors import read_json, write_atomic, now_ms
from ..core.security import sanitize_filename


# ============================================================
# 锁
# ============================================================

_canvas_locks: dict[str, asyncio.Lock] = {}
_projects_lock = asyncio.Lock()

CANVAS_COLORS = {"red", "orange", "amber", "green", "teal", "blue", "violet", "pink", "slate", ""}

INDEX_LOCK = asyncio.Lock()


def _get_lock(canvas_id: str) -> asyncio.Lock:
    if canvas_id not in _canvas_locks:
        _canvas_locks[canvas_id] = asyncio.Lock()
    return _canvas_locks[canvas_id]


# ============================================================
# 工具
# ============================================================

def _make_filename(title: str, date_str: str, canvas_id: str) -> str:
    safe = sanitize_filename(title) or "untitled"
    short_id = canvas_id[:8]
    return f"{safe}_{date_str}_{short_id}.json"


def _filename_to_dirname(filename: str) -> str:
    return filename.rsplit(".", 1)[0]


def _rename_filename(path: Path, title: str, canvas_id: str) -> str:
    parts = path.stem.rsplit("_", 2)
    date_str = parts[-2] if len(parts) == 3 else time.strftime("%Y%m%d")
    return _make_filename(title, date_str, canvas_id)


async def _find_canvas_file(canvas_id: str, *, include_trash: bool = False) -> Optional[Path]:
    """通过 canvas_id 找到对应的 JSON 文件（用 id 前 8 位快速定位）"""
    short = canvas_id[:8]
    pattern = f"*_{short}.json"
    directories = (CANVAS_DIR, CANVAS_TRASH_DIR) if include_trash else (CANVAS_DIR,)
    for directory in directories:
        for f in directory.glob(pattern):
            data = await read_json(f)
            if data and data.get("id") == canvas_id:
                return f
    return None


# ============================================================
# 元数据索引 (.index.json)
# ============================================================

def _canvas_record(canvas: dict) -> dict:
    """从画布 dict 提取列表视图所需的元数据（不含 nodes/connections）"""
    nodes = canvas.get("nodes") or []
    return {
        "id": canvas.get("id"),
        "title": canvas.get("title"),
        "icon": canvas.get("icon"),
        "kind": canvas.get("kind", "classic"),
        "owner": canvas.get("owner"),
        "color": canvas.get("color"),
        "pinned": canvas.get("pinned", False),
        "project": canvas.get("project", DEFAULT_PROJECT_ID),
        "board_x": canvas.get("board_x", 0),
        "board_y": canvas.get("board_y", 0),
        "node_count": len(nodes),
        "created_at": canvas.get("created_at"),
        "updated_at": canvas.get("updated_at"),
        "deleted_at": canvas.get("deleted_at"),
    }


async def _read_index() -> dict:
    idx = await read_json(CANVAS_DIR / ".index.json")
    if isinstance(idx, dict) and "canvases" in idx:
        return idx
    return {"canvases": [], "trash": []}


async def _write_index(idx: dict) -> None:
    await write_atomic(CANVAS_DIR / ".index.json", idx)


async def _rebuild_index() -> dict:
    """从所有画布 JSON 重建索引。"""
    canvases = []
    trash = []
    for f in CANVAS_DIR.glob("*.json"):
        if f.name == ".index.json":
            continue
        data = await read_json(f)
        if data is None:
            continue
        if data.get("deleted_at"):
            trash.append(_canvas_record(data))
        else:
            canvases.append(_canvas_record(data))
    for f in CANVAS_TRASH_DIR.glob("*.json"):
        data = await read_json(f)
        if data is not None:
            trash.append(_canvas_record(data))
    idx = {"canvases": canvases, "trash": trash}
    await _write_index(idx)
    return idx


async def _update_index(canvas: dict, target: str = "canvases") -> None:
    """增量更新索引：插入或替换一条记录。"""
    async with INDEX_LOCK:
        idx = await _read_index()
        records = idx.setdefault(target, [])
        # 替换已有的或追加
        for i, r in enumerate(records):
            if r.get("id") == canvas.get("id"):
                records[i] = _canvas_record(canvas)
                break
        else:
            records.append(_canvas_record(canvas))
        idx[target] = records
        await _write_index(idx)


async def _remove_from_index(canvas_id: str, source: str = "canvases", target: str = None) -> None:
    """从索引移除一条记录，可选追加到另一分区。"""
    async with INDEX_LOCK:
        idx = await _read_index()
        src_list = idx.get(source, [])
        kept = []
        removed = None
        for r in src_list:
            if r.get("id") == canvas_id:
                removed = r
            else:
                kept.append(r)
        idx[source] = kept
        if target and removed:
            idx.setdefault(target, []).append(removed)
        await _write_index(idx)


# ============================================================
# 初始画布结构
# ============================================================

def _new_canvas_dict(title: str, canvas_id: str, **kwargs) -> dict:
    now = now_ms()
    return {
        "id": canvas_id,
        "title": title,
        "icon": kwargs.get("icon", ""),
        "kind": kwargs.get("kind", "classic"),
        "owner": kwargs.get("owner", ""),
        "color": kwargs.get("color", ""),
        "pinned": kwargs.get("pinned", False),
        "project": kwargs.get("project", DEFAULT_PROJECT_ID),
        "board_x": kwargs.get("board_x", 0),
        "board_y": kwargs.get("board_y", 0),
        "created_at": now,
        "updated_at": now,
        "deleted_at": None,
        "nodes": [],
        "connections": [],
        "viewport": {"x": 0, "y": 0, "scale": 1.0},
        "logs": [],
        "settings": {},
    }


# ============================================================
# 画布 CRUD
# ============================================================

async def create_canvas(title: str = "未命名画布", **kwargs) -> dict:
    """创建画布：生成 JSON 文件 + 资源文件夹"""
    canvas_id = uuid.uuid4().hex
    date_str = time.strftime("%Y%m%d")
    filename = _make_filename(title, date_str, canvas_id)
    dirname = _filename_to_dirname(filename)

    canvas = _new_canvas_dict(title, canvas_id, **kwargs)

    lock = _get_lock(canvas_id)
    async with lock:
        await write_atomic(CANVAS_DIR / filename, canvas)

    # 创建资源文件夹
    (CANVAS_FILES_DIR / dirname / "inputs").mkdir(parents=True, exist_ok=True)
    (CANVAS_FILES_DIR / dirname / "outputs").mkdir(parents=True, exist_ok=True)

    # 更新索引
    await _update_index(canvas)

    # 确保默认项目存在
    await ensure_default_project()

    return canvas


def _rewrite_values(obj, mapping: dict[str, str]) -> None:
    if isinstance(obj, dict):
        for key, value in list(obj.items()):
            if isinstance(value, str):
                for old_prefix, new_prefix in mapping.items():
                    if value.startswith(old_prefix):
                        obj[key] = new_prefix + value[len(old_prefix):]
                        break
                else:
                    _rewrite_values(value, mapping)
            else:
                _rewrite_values(value, mapping)
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            if isinstance(value, str):
                for old_prefix, new_prefix in mapping.items():
                    if value.startswith(old_prefix):
                        obj[index] = new_prefix + value[len(old_prefix):]
                        break
                else:
                    _rewrite_values(value, mapping)
            else:
                _rewrite_values(value, mapping)


async def import_canvas_data(data: dict, resources: list[tuple[str, str, bytes]] | None = None) -> dict:
    if not isinstance(data, dict) or "id" not in data:
        raise ValueError("invalid canvas data")

    canvas = dict(data)
    canvas_id = uuid.uuid4().hex
    timestamp = now_ms()
    title = str(canvas.get("title") or "untitled")
    filename = _make_filename(title, time.strftime("%Y%m%d"), canvas_id)
    dirname = _filename_to_dirname(filename)
    canvas.update(id=canvas_id, created_at=timestamp, updated_at=timestamp, deleted_at=None)

    resource_items = resources or []
    replacements = {
        source_url: f"/cfiles/{dirname}/inputs/{relative_name}"
        for source_url, relative_name, _content in resource_items
        if source_url
    }
    _rewrite_values(canvas, replacements)

    lock = _get_lock(canvas_id)
    async with lock:
        CANVAS_DIR.mkdir(parents=True, exist_ok=True)
        CANVAS_FILES_DIR.mkdir(parents=True, exist_ok=True)
        final_resources = CANVAS_FILES_DIR / dirname

        def prepare_resources() -> None:
            temp_dir = Path(tempfile.mkdtemp(prefix=".import_", dir=str(CANVAS_FILES_DIR)))
            try:
                from ..core.paths import safe_path_join
                (temp_dir / "inputs").mkdir(parents=True, exist_ok=True)
                (temp_dir / "outputs").mkdir(parents=True, exist_ok=True)
                for _source_url, relative_name, content in resource_items:
                    target = safe_path_join(temp_dir / "inputs", relative_name)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                os.replace(temp_dir, final_resources)
            except Exception:
                shutil.rmtree(temp_dir, ignore_errors=True)
                raise

        await asyncio.to_thread(prepare_resources)
        try:
            await write_atomic(CANVAS_DIR / filename, canvas)
        except Exception:
            await asyncio.to_thread(shutil.rmtree, final_resources, True)
            raise

    await _update_index(canvas)
    await ensure_default_project()
    return canvas


async def load_canvas(canvas_id: str) -> dict:
    """加载画布，不存在或已删除抛异常"""
    path = await _find_canvas_file(canvas_id)
    if path is None:
        raise FileNotFoundError(f"画布 {canvas_id} 不存在")
    canvas = await read_json(path)
    if canvas is None or canvas.get("deleted_at"):
        raise FileNotFoundError(f"画布 {canvas_id} 不存在或已在回收站")
    return canvas


async def load_canvas_any(canvas_id: str) -> dict:
    """加载画布（包括已删除的）"""
    path = await _find_canvas_file(canvas_id, include_trash=True)
    if path is None:
        raise FileNotFoundError(f"画布 {canvas_id} 不存在")
    canvas = await read_json(path)
    if canvas is None:
        raise FileNotFoundError(f"画布 {canvas_id} 无法读取")
    return canvas


async def save_canvas(
    canvas_id: str,
    nodes: Optional[list] = None,
    connections: Optional[list] = None,
    viewport: Optional[dict] = None,
    logs: Optional[list] = None,
    settings: Optional[dict] = None,
    title: Optional[str] = None,
    icon: Optional[str] = None,
    base_updated_at: Optional[int] = None,
) -> dict:
    """
    保存画布内容。自动更新 updated_at。
    如果 base_updated_at 不匹配，抛出 ConflictError（乐观锁）。
    """
    path = await _find_canvas_file(canvas_id)
    if path is None:
        raise FileNotFoundError(f"画布 {canvas_id} 不存在")

    lock = _get_lock(canvas_id)
    async with lock:
        current = await read_json(path)
        if current is None:
            raise FileNotFoundError(f"画布 {canvas_id} 无法读取")

        # 乐观锁检查
        if base_updated_at is not None:
            if current.get("updated_at") != base_updated_at:
                raise CanvasConflictError(
                    f"画布已被修改（当前: {current.get('updated_at')}, 你的: {base_updated_at}），请刷新后重试",
                    current_updated_at=current.get("updated_at"),
                )

        # 更新字段
        if nodes is not None:
            current["nodes"] = nodes
        if connections is not None:
            current["connections"] = connections
        if viewport is not None:
            current["viewport"] = viewport
        if logs is not None:
            current["logs"] = logs
        if settings is not None:
            current["settings"] = settings
        if title is not None:
            current["title"] = title
        if icon is not None:
            current["icon"] = icon

        current["updated_at"] = now_ms()

        # 如果 title 变了，需要重命名文件并重写 URL
        if title is not None:
            new_filename = _rename_filename(path, title, canvas_id)
            if new_filename != path.name:
                old_stem = path.stem
                new_stem = _filename_to_dirname(new_filename)
                # 重写 /cfiles/ URL
                old_prefix = f"/cfiles/{old_stem}/"
                new_prefix = f"/cfiles/{new_stem}/"
                _rewrite_values(current, {old_prefix: new_prefix})
                await write_atomic(CANVAS_DIR / new_filename, current)
                path.unlink(missing_ok=True)
                old_dir = CANVAS_FILES_DIR / old_stem
                new_dir = CANVAS_FILES_DIR / new_stem
                if old_dir.exists() and not new_dir.exists():
                    await asyncio.to_thread(old_dir.rename, new_dir)
                await _update_index(current)
                return current

        await write_atomic(path, current)

    await _update_index(current)
    return current


async def update_canvas_meta(canvas_id: str, **fields) -> dict:
    """
    更新画布元数据。不会修改 updated_at。
    用于修改标题、图标、颜色、置顶等（不打标签）。
    """
    path = await _find_canvas_file(canvas_id)
    if path is None:
        raise FileNotFoundError(f"画布 {canvas_id} 不存在")

    lock = _get_lock(canvas_id)
    async with lock:
        current = await read_json(path)
        if current is None or current.get("deleted_at"):
            raise FileNotFoundError(f"画布 {canvas_id} 不存在")

        for key in ("title", "icon", "owner", "color", "pinned", "project", "board_x", "board_y"):
            if key in fields and fields[key] is not None:
                if key == "color" and fields[key] not in CANVAS_COLORS:
                    continue
                current[key] = fields[key]

        # 不更新 updated_at — 打标签不应把画布顶到最前
        if fields.get("title") is not None:
            new_filename = _rename_filename(path, current["title"], canvas_id)
            if new_filename != path.name:
                old_stem = path.stem
                new_stem = _filename_to_dirname(new_filename)
                # 重写 /cfiles/ URL
                old_prefix = f"/cfiles/{old_stem}/"
                new_prefix = f"/cfiles/{new_stem}/"
                _rewrite_values(current, {old_prefix: new_prefix})
                # 先写新 JSON
                await write_atomic(CANVAS_DIR / new_filename, current)
                try:
                    # 再移动资源目录
                    old_resource = CANVAS_FILES_DIR / old_stem
                    new_resource = CANVAS_FILES_DIR / new_stem
                    if old_resource.exists() and not new_resource.exists():
                        await asyncio.to_thread(old_resource.rename, new_resource)
                    # 最后删旧 JSON
                    await asyncio.to_thread(path.unlink, missing_ok=True)
                except Exception:
                    # 资源移动或删旧文件失败 — 新 JSON 已写，但回滚为时已晚
                    # 保留状态一致：索引走新文件名
                    await _update_index(current)
                    raise
                await _update_index(current)
                return current

        await write_atomic(path, current)

    await _update_index(current)
    return current


async def delete_canvas(canvas_id: str) -> dict:
    """软删除：移到回收站"""
    path = await _find_canvas_file(canvas_id)
    if path is None:
        raise FileNotFoundError(f"画布 {canvas_id} 不存在")

    lock = _get_lock(canvas_id)
    async with lock:
        current = await read_json(path)
        if current is None:
            raise FileNotFoundError(f"画布 {canvas_id} 无法读取")
        current["deleted_at"] = now_ms()
        current["updated_at"] = now_ms()
        await write_atomic(path, current)
        CANVAS_TRASH_DIR.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.replace, CANVAS_TRASH_DIR / path.name)
    await _remove_from_index(canvas_id, source="canvases", target="trash")
    return current


async def restore_canvas(canvas_id: str) -> dict:
    """从回收站恢复"""
    path = await _find_canvas_file(canvas_id, include_trash=True)
    if path is None:
        raise FileNotFoundError(f"画布 {canvas_id} 不存在")

    lock = _get_lock(canvas_id)
    async with lock:
        current = await read_json(path)
        if current is None:
            raise FileNotFoundError(f"画布 {canvas_id} 无法读取")
        current["deleted_at"] = None
        current["updated_at"] = now_ms()
        await write_atomic(path, current)
        CANVAS_DIR.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.replace, CANVAS_DIR / path.name)
    await _remove_from_index(canvas_id, source="trash", target="canvases")
    return current


async def purge_canvas(canvas_id: str) -> None:
    """永久删除回收站中的画布 JSON，资源目录永久保留。"""
    path = await _find_canvas_file(canvas_id, include_trash=True)
    if path is None:
        # 可能已经在回收站被清理了
        return
    if path.parent.resolve() != CANVAS_TRASH_DIR.resolve():
        return

    lock = _get_lock(canvas_id)
    async with lock:
        await asyncio.to_thread(path.unlink, missing_ok=True)
    await _remove_from_index(canvas_id, source="trash")
    # 清理锁
    _canvas_locks.pop(canvas_id, None)


# ============================================================
# 列表 & 回收站
# ============================================================

def _canvas_record(canvas: dict) -> dict:
    """提取画布元数据（不含 nodes、connections）"""
    nodes = canvas.get("nodes") or []
    return {
        "id": canvas.get("id"),
        "title": canvas.get("title"),
        "icon": canvas.get("icon"),
        "kind": canvas.get("kind", "classic"),
        "owner": canvas.get("owner"),
        "color": canvas.get("color"),
        "pinned": canvas.get("pinned", False),
        "project": canvas.get("project", DEFAULT_PROJECT_ID),
        "board_x": canvas.get("board_x", 0),
        "board_y": canvas.get("board_y", 0),
        "node_count": len(nodes),
        "created_at": canvas.get("created_at"),
        "updated_at": canvas.get("updated_at"),
        "deleted_at": canvas.get("deleted_at"),
    }


async def _cleanup_trash() -> int:
    """清理超过 30 天的回收站画布。返回清理数量。"""
    cutoff = now_ms() - CANVAS_TRASH_RETENTION_MS
    removed = 0
    for f in CANVAS_TRASH_DIR.glob("*.json"):
        data = await read_json(f)
        if data is None:
            continue
        deleted_at = data.get("deleted_at")
        if deleted_at and deleted_at < cutoff:
            await purge_canvas(data.get("id"))
            removed += 1
    return removed


async def list_canvases(include_deleted: bool = False) -> list[dict]:
    """列出所有画布 — 优先读 .index.json，损坏时从完整 JSON 重建。"""
    await _cleanup_trash()
    idx = await _read_index()
    if idx.get("canvases"):
        records = list(idx["canvases"])
    else:
        idx = await _rebuild_index()
        records = list(idx.get("canvases", []))
    if include_deleted:
        records.extend(idx.get("trash", []))
    records.sort(key=lambda r: (not r["pinned"], -(r["updated_at"] or 0)))
    return records


async def list_deleted_canvases() -> list[dict]:
    """列出回收站 — 优先读索引。"""
    idx = await _read_index()
    if idx.get("trash"):
        records = list(idx["trash"])
    else:
        idx = await _rebuild_index()
        records = list(idx.get("trash", []))
    records.sort(key=lambda r: -(r["deleted_at"] or 0))
    return records


# ============================================================
# 原子追加节点
# ============================================================

async def append_canvas_nodes(canvas_id: str, nodes: list[dict]) -> dict:
    """在画布末尾原子追加节点。"""
    path = await _find_canvas_file(canvas_id)
    if path is None:
        raise FileNotFoundError(f"画布 {canvas_id} 不存在")

    lock = _get_lock(canvas_id)
    async with lock:
        current = await read_json(path)
        if current is None:
            raise FileNotFoundError(f"画布 {canvas_id} 无法读取")

        existing = current.setdefault("nodes", [])
        existing.extend(nodes)
        current["updated_at"] = now_ms()

        await write_atomic(path, current)

    await _update_index(current)
    return current


# ============================================================
# 画布资源路径
# ============================================================

async def canvas_resource_dir(canvas_id: str) -> Path:
    """获取画布的资源文件夹路径"""
    path = await _find_canvas_file(canvas_id)
    if path is None:
        raise FileNotFoundError(f"画布 {canvas_id} 不存在")
    dirname = _filename_to_dirname(path.name)
    return CANVAS_FILES_DIR / dirname


async def canvas_output_dir(canvas_id: str) -> Path:
    """画布的输出目录"""
    d = await canvas_resource_dir(canvas_id)
    return d / "outputs"


async def canvas_input_dir(canvas_id: str) -> Path:
    """画布的输入目录"""
    d = await canvas_resource_dir(canvas_id)
    return d / "inputs"


# ============================================================
# 画布资产提取
# ============================================================

def extract_canvas_assets(canvas: dict) -> list[dict]:
    """深度遍历画布节点，提取所有可下载的媒体资产"""
    assets = []
    for node in canvas.get("nodes", []):
        for value in _iter_asset_values(node.get("data", {})):
            url = _asset_url(value)
            if url:
                assets.append({
                    "node_id": node.get("id"),
                    "node_title": node.get("title", ""),
                    "url": url,
                    "kind": _asset_kind(value, url),
                    "name": _asset_name(value, url),
                })
    return assets


def _iter_asset_values(data, depth=0):
    """递归遍历，找出所有包含媒体 URL 的 dict"""
    if depth > 10 or data is None:
        return
    if isinstance(data, dict):
        if _asset_url(data):
            yield data
        for v in data.values():
            yield from _iter_asset_values(v, depth + 1)
    elif isinstance(data, list):
        for item in data:
            yield from _iter_asset_values(item, depth + 1)


def _asset_url(value: dict) -> str:
    """从 dict 提取 URL（8 个候选 key）"""
    if not isinstance(value, dict):
        return ""
    for k in ("url", "image_url", "video_url", "audio_url", "file_url", "src", "href", "data"):
        v = value.get(k)
        if isinstance(v, str) and v:
            return v
    return ""


def _asset_kind(value: dict, url: str) -> str:
    ext = url.rsplit(".", 1)[-1].lower().split("?")[0] if "." in url else ""
    if ext in ("mp4", "webm", "mov", "avi", "mkv"):
        return "video"
    if ext in ("mp3", "wav", "ogg", "aac", "flac"):
        return "audio"
    return "image"


def _asset_name(value: dict, url: str) -> str:
    name = value.get("name", "") or value.get("filename", "")
    if name:
        return name
    return url.rsplit("/", 1)[-1].split("?")[0] if "/" in url else url


# ============================================================
# 项目管理
# ============================================================

async def _ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


async def ensure_default_project():
    """确保 default 项目存在"""
    async with _projects_lock:
        projects = await read_json(PROJECTS_PATH) or []
        if not any(p.get("id") == DEFAULT_PROJECT_ID for p in projects):
            projects.insert(0, {
                "id": DEFAULT_PROJECT_ID,
                "name": "默认项目",
                "order": 0,
                "created_at": now_ms(),
            })
            await write_atomic(PROJECTS_PATH, projects)


async def list_projects() -> list[dict]:
    """列出所有项目，含每个项目的画布数量"""
    await ensure_default_project()
    async with _projects_lock:
        projects = await read_json(PROJECTS_PATH) or []

    # 统计每个项目的画布数
    canvas_counts = {}
    for f in CANVAS_DIR.glob("*.json"):
        canvas = await read_json(f)
        if canvas and not canvas.get("deleted_at"):
            pid = canvas.get("project", DEFAULT_PROJECT_ID)
            canvas_counts[pid] = canvas_counts.get(pid, 0) + 1

    result = []
    for p in projects:
        rec = dict(p)
        rec["canvas_count"] = canvas_counts.get(p["id"], 0)
        result.append(rec)
    result.sort(key=lambda p: (p.get("order", 0), p.get("created_at", 0)))
    return result


async def create_project(name: str) -> dict:
    await ensure_default_project()
    async with _projects_lock:
        projects = await read_json(PROJECTS_PATH) or []
        max_order = max((p.get("order", 0) for p in projects), default=0)
        project = {
            "id": uuid.uuid4().hex,
            "name": name,
            "order": max_order + 1,
            "created_at": now_ms(),
        }
        projects.append(project)
        await write_atomic(PROJECTS_PATH, projects)
    return project


async def update_project(project_id: str, name: str = None, order: int = None) -> dict:
    async with _projects_lock:
        projects = await read_json(PROJECTS_PATH) or []
        for p in projects:
            if p["id"] == project_id:
                if name is not None:
                    p["name"] = name
                if order is not None:
                    p["order"] = order
                await write_atomic(PROJECTS_PATH, projects)
                return dict(p)
    raise FileNotFoundError(f"项目 {project_id} 不存在")


async def delete_project(project_id: str) -> None:
    if project_id == DEFAULT_PROJECT_ID:
        raise ValueError("不能删除默认项目")
    async with _projects_lock:
        projects = await read_json(PROJECTS_PATH) or []
        projects = [p for p in projects if p["id"] != project_id]
        await write_atomic(PROJECTS_PATH, projects)


# ============================================================
# 异常
# ============================================================

class CanvasConflictError(Exception):
    """乐观锁冲突 — 前端收到应提示用户刷新"""
    def __init__(self, message: str, current_updated_at: int | None = None):
        super().__init__(message)
        self.current_updated_at = current_updated_at
