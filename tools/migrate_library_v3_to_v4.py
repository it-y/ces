"""
迁移 v3 → v4 资产库存储结构。

用法：python -m tools.migrate_library_v3_to_v4

迁移内容：
  assets/     ← 从 library/index.json + library/files/ 迁移
  workflows/  ← 从 library/workflows/{uuid}/ 迁移
  local/      ← 从 data/local-assets/ 迁移
"""

import asyncio
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

# 加项目根到 path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.config import (
    DATA_DIR, LIBRARY_DIR,
    ASSET_DIR, ASSET_INDEX_PATH,
    WORKFLOW_LIBRARY_DIR, WORKFLOW_LIBRARY_INDEX_PATH,
    LOCAL_DIR, LOCAL_INDEX_PATH,
    TRASH_DIR,
)
from app.core.errors import read_json, write_atomic, now_ms
from app.core.security import sanitize_filename

# 旧路径（迁移脚本硬编码，迁移完成后不再需要）
_OLD_ASSET_FILES_DIR = LIBRARY_DIR / "files"
_OLD_LOCAL_ASSETS_DIR = DATA_DIR / "local-assets"
_OLD_LOCAL_ASSET_FILES_DIR = _OLD_LOCAL_ASSETS_DIR / "files"
_OLD_LOCAL_ASSET_INDEX_PATH = _OLD_LOCAL_ASSETS_DIR / "index.json"

_report = {"migrated": 0, "skipped": 0, "errors": []}


def log(msg: str):
    print(f"  {msg}")


def _sanitize_dirname(name: str) -> str:
    import re
    name = name.strip().replace("\\", "/").split("/")[-1]
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"[\x00-\x1f]", "", name)
    return name or "untitled"


async def migrate_assets():
    """从旧的 library/index.json(v3) + library/files/ → assets/{库}/{分类}/{文件}"""
    log("\n=== 迁移 assets ===")

    old_lib = await read_json(LIBRARY_DIR / "index.json")
    if not old_lib:
        log("  没有找到旧的 library/index.json，跳过")
        return

    for lib_entry in old_lib.get("libraries", []):
        lib_name = _sanitize_dirname(lib_entry.get("name", "未命名库"))
        lib_id = lib_entry.get("id", uuid.uuid4().hex)
        lib_dir = ASSET_DIR / lib_name
        lib_dir.mkdir(parents=True, exist_ok=True)
        await write_atomic(lib_dir / ".meta.json", {"id": lib_id, "name": lib_entry.get("name", lib_name), "updated_at": now_ms()})

        for cat in lib_entry.get("categories", []):
            cat_name = _sanitize_dirname(cat.get("name", "未命名分类"))
            cat_id = cat.get("id", uuid.uuid4().hex)
            cat_dir = lib_dir / cat_name
            cat_dir.mkdir(parents=True, exist_ok=True)
            await write_atomic(cat_dir / ".meta.json", {
                "id": cat_id,
                "name": cat.get("name", cat_name),
                "type": cat.get("type", "image"),
                "updated_at": now_ms(),
            })

            for item in cat.get("items", []):
                item_id = item.get("id") or uuid.uuid4().hex
                storage_key = item.get("storage_key", "")
                if storage_key:
                    # 文件型素材
                    src = _OLD_ASSET_FILES_DIR / storage_key
                    if src.is_file():
                        dest_name = sanitize_filename(item.get("original_name", item.get("name", src.name))) or src.name
                        dest = cat_dir / dest_name
                        counter = 1
                        while dest.exists():
                            p = Path(dest_name)
                            dest = cat_dir / f"{p.stem}_{counter}{p.suffix}"
                            counter += 1
                        shutil.copy2(str(src), str(dest))
                        # 写 .meta 侧边
                        meta_dir = cat_dir / ".meta"
                        meta_dir.mkdir(parents=True, exist_ok=True)
                        await write_atomic(meta_dir / f"{dest.stem}.json", {
                            "id": item_id,
                            "name": item.get("name", dest_name),
                            "kind": item.get("kind", "file"),
                            "mime": item.get("mime", ""),
                            "size": item.get("size", dest.stat().st_size),
                            "tags": item.get("tags", []),
                            "classification": item.get("classification", {}),
                            "caption": item.get("caption", ""),
                            "caption_provider": item.get("caption_provider", ""),
                            "created_at": item.get("created_at", int(src.stat().st_ctime * 1000)),
                            "updated_at": item.get("updated_at", now_ms()),
                        })
                        _report["migrated"] += 1
                        continue
                # URL 型素材
                marker = cat_dir / f"{item_id}.url"
                if not marker.exists():
                    marker.write_text("")
                meta_dir = cat_dir / ".meta"
                meta_dir.mkdir(parents=True, exist_ok=True)
                await write_atomic(meta_dir / f"{item_id}.json", {
                    "id": item_id,
                    "name": item.get("name", "未命名"),
                    "url": item.get("url", ""),
                    "kind": item.get("kind", "image"),
                    "tags": item.get("tags", []),
                    "classification": item.get("classification", {}),
                    "caption": item.get("caption", ""),
                    "caption_provider": item.get("caption_provider", ""),
                    "created_at": item.get("created_at", now_ms()),
                    "updated_at": item.get("updated_at", now_ms()),
                })
                _report["migrated"] += 1

    # 重建索引
    from app.assets.library import rebuild_asset_index
    await rebuild_asset_index()
    log(f"  assets 迁移完成：{_report['migrated']} 个素材")


async def create_default_assets():
    """如果没有 assets，创建一个默认库"""
    if ASSET_DIR.exists() and any(ASSET_DIR.iterdir()):
        return
    log("\n=== 创建默认资产库 ===")
    lib_dir = ASSET_DIR / "我的资产"
    lib_dir.mkdir(parents=True, exist_ok=True)
    await write_atomic(lib_dir / ".meta.json", {
        "id": "default", "name": "我的资产", "updated_at": now_ms(),
    })
    cat_dir = lib_dir / "默认"
    cat_dir.mkdir(parents=True, exist_ok=True)
    await write_atomic(cat_dir / ".meta.json", {
        "id": "default", "name": "默认", "type": "image", "updated_at": now_ms(),
    })
    from app.assets.library import rebuild_asset_index
    await rebuild_asset_index()
    log("  默认资产库已创建")


async def migrate_workflows():
    """从旧的 library/workflows/{uuid}/ → workflows/{库}/{分类}/{uuid}/"""
    log("\n=== 迁移 workflows ===")

    old_wf_dir = LIBRARY_DIR / "workflows"
    if not old_wf_dir.exists():
        log("  没有找到 workflows 目录，跳过")
        return

    # 放到一个临时的 "默认" 库/分类下
    lib_dir = WORKFLOW_LIBRARY_DIR / "我的资产"
    cat_dir = lib_dir / "默认"
    cat_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for item_dir in sorted(old_wf_dir.iterdir()):
        if not item_dir.is_dir() or item_dir.name.startswith("."):
            continue
        meta = await read_json(item_dir / ".meta.json")
        if not meta:
            continue
        item_id = meta.get("id") or item_dir.name
        dst_dir = cat_dir / item_id
        dst_dir.mkdir(parents=True, exist_ok=True)
        # 拷贝文件
        for f in item_dir.iterdir():
            if f.is_file():
                shutil.copy2(str(f), str(dst_dir / f.name))
        await write_atomic(dst_dir / ".meta.json", meta)
        count += 1

    from app.assets.library import rebuild_workflow_index
    await rebuild_workflow_index()
    log(f"  workflows 迁移完成：{count} 个工作流")


async def migrate_local_assets():
    """从 data/local-assets/ → library/local/"""
    log("\n=== 迁移 local-assets ===")

    if not _OLD_LOCAL_ASSETS_DIR.exists():
        log("  没有找到 local-assets 目录，跳过")
        return

    old_index = await read_json(_OLD_LOCAL_ASSET_INDEX_PATH) or {}
    old_files_dir = _OLD_LOCAL_ASSET_FILES_DIR

    # 拷贝所有文件
    if old_files_dir.exists():
        for file_path in old_files_dir.rglob("*"):
            if file_path.is_file():
                rel = file_path.relative_to(old_files_dir)
                dst = LOCAL_DIR / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(file_path), str(dst))

    # 写新索引
    new_index = {"version": 1, "updated_at": now_ms(), "items": []}
    for item in old_index.get("items", []):
        path = item.get("path", "")
        src_file = old_files_dir / path
        if not src_file.is_file():
            continue
        new_index["items"].append(item)
    await write_atomic(LOCAL_INDEX_PATH, new_index)
    log(f"  local-assets 迁移完成：{len(new_index['items'])} 个文件")


async def main():
    print("=" * 50)
    print("  资产库 v3 → v4 迁移工具")
    print("=" * 50)

    # 确保目标目录存在
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    WORKFLOW_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    TRASH_DIR.mkdir(parents=True, exist_ok=True)

    await migrate_assets()
    await create_default_assets()
    await migrate_workflows()
    await migrate_local_assets()

    # 旧文件备份
    backup_dir = DATA_DIR / "backup-v3-migration"
    backup_dir.mkdir(parents=True, exist_ok=True)
    old_files = [
        (LIBRARY_DIR / "index.json", backup_dir / "library-index.json"),
        (LIBRARY_DIR / "files", backup_dir / "library-files"),
        (_OLD_LOCAL_ASSETS_DIR, backup_dir / "local-assets"),
    ]
    for src, dst in old_files:
        if src.exists():
            dst = Path(str(dst))
            if src.is_dir() and not dst.exists():
                shutil.copytree(str(src), str(dst))
                log(f"  备份 {src} → {dst}")
            elif src.is_file():
                shutil.copy2(str(src), str(dst))
                log(f"  备份 {src} → {dst}")

    print("\n" + "=" * 50)
    print(f"  迁移完成！")
    print(f"  已迁移：{_report['migrated']} 个素材")
    print(f"  跳过：{_report['skipped']} 个")
    print(f"  错误：{len(_report['errors'])} 个")
    if _report['errors']:
        for err in _report['errors'][:10]:
            print(f"    - {err}")
    print(f"  备份位置：{backup_dir}")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
