"""Canvas import/export and asset package operations."""

from __future__ import annotations

import asyncio
import io
import json
import re
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException

from ..config import CANVAS_FILES_DIR, OUTPUT_DIR, UPLOAD_DIR
from ..core.paths import resolve_local_media_url
from ..core.paths import validate_zip_member
from ..core.security import sanitize_filename
from .manager import import_canvas_data

MAX_ARCHIVE_ENTRIES = 2000
MAX_ARCHIVE_FILE_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_RATIO = 200


def _validate_archive(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_ENTRIES:
        raise HTTPException(400, "ZIP 文件条目过多")
    total = 0
    result: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        path = validate_zip_member(info.filename.rstrip("/"))
        if info.is_dir():
            continue
        if info.file_size > MAX_ARCHIVE_FILE_BYTES:
            raise HTTPException(400, "ZIP 中单个文件过大")
        total += info.file_size
        if total > MAX_ARCHIVE_TOTAL_BYTES:
            raise HTTPException(400, "ZIP 解压后总大小过大")
        if info.file_size and info.file_size / max(info.compress_size, 1) > MAX_ARCHIVE_RATIO:
            raise HTTPException(400, "ZIP 压缩比异常")
        result[str(path)] = info
    return result


def _read_json_bytes(content: bytes, message: str) -> dict:
    try:
        value = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, message) from exc
    if not isinstance(value, dict):
        raise HTTPException(400, message)
    return value


def _parse_canvas_zip(raw: bytes) -> tuple[dict, list[tuple[str, str, bytes]]]:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = _validate_archive(archive)

            # Format 1: canvas.json + resources-manifest.json
            if "canvas.json" in members:
                data = _read_json_bytes(archive.read(members["canvas.json"]), "canvas.json 不是有效的画布文件")
                resources: list[tuple[str, str, bytes]] = []
                manifest_info = members.get("resources-manifest.json")
                if manifest_info:
                    manifest = _read_json_bytes(archive.read(manifest_info), "资源清单格式错误")
                    entries = manifest.get("resources", [])
                    if not isinstance(entries, list) or len(entries) > MAX_ARCHIVE_ENTRIES:
                        raise HTTPException(400, "资源清单格式错误")
                    for entry in entries:
                        if not isinstance(entry, dict):
                            raise HTTPException(400, "资源清单格式错误")
                        member = str(validate_zip_member(str(entry.get("file") or "")))
                        if member not in members:
                            raise HTTPException(400, f"ZIP 缺少资源文件: {member}")
                        resources.append((str(entry.get("url") or ""), member, archive.read(members[member])))
                return data, resources

            # Format 2: workflow.json + resources/
            if "workflow.json" in members:
                wf = _read_json_bytes(archive.read(members["workflow.json"]), "workflow.json 解析失败")
                data = {
                    "id": wf.get("id", "imported"),
                    "nodes": wf.get("nodes", []),
                    "connections": wf.get("connections", []),
                    "viewport": wf.get("viewport"),
                    "title": wf.get("title", ""),
                }
                resources = []
                for entry in wf.get("resources", []):
                    archive_path = str(entry.get("archive") or "")
                    url = str(entry.get("url") or "")
                    if archive_path and url and archive_path in members:
                        member_path = validate_zip_member(archive_path)
                        name = Path(archive_path).name
                        content = archive.read(members[str(member_path)])
                        resources.append((url, name, content))
                return data, resources

            raise HTTPException(400, "ZIP 中没有 canvas.json 或 workflow.json")
    except zipfile.BadZipFile as exc:
        raise HTTPException(400, "无效的 ZIP 文件") from exc


def _derive_title(filename: str | None, data: dict) -> str:
    title = str(data.get("title") or "").strip()
    if title:
        return title
    if filename:
        name = Path(filename).stem
        name = re.sub(r"[-_]\d{8}T\d{6}$", "", name)
        name = re.sub(r"[-_]\d{14}$", "", name)
        name = re.sub(r"[-_]\d{8}$", "", name)
        return name
    return "未命名"


async def import_canvas_file(raw: bytes, filename: str | None, project_id: str | None = None) -> dict:
    if filename and filename.lower().endswith(".zip"):
        data, resources = await asyncio.to_thread(_parse_canvas_zip, raw)
    else:
        data = _read_json_bytes(raw, "无效的 JSON 文件，支持 .json 或 .zip")
        resources = []
    if "id" not in data:
        raise HTTPException(400, "不是有效的画布文件")
    data["title"] = _derive_title(filename, data)
    if project_id:
        data["project"] = project_id
    try:
        return await import_canvas_data(data, resources)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


async def check_canvas_assets(
    urls: list[str],
    *,
    output_dir: Path = OUTPUT_DIR,
    upload_dir: Path = UPLOAD_DIR,
    canvas_files_dir: Path = CANVAS_FILES_DIR,
) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for raw in urls[:3000]:
        url = str(raw or "").strip()
        if not url:
            continue
        path = resolve_local_media_url(
            url,
            output_dir=output_dir,
            upload_dir=upload_dir,
            canvas_files_dir=canvas_files_dir,
        )
        result[url] = await asyncio.to_thread(path.is_file) if path else True
    return result


def _build_assets_zip(
    items: list[dict],
    output_dir: Path,
    upload_dir: Path,
    canvas_files_dir: Path,
) -> bytes:
    buffer = io.BytesIO()
    used: set[str] = set()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in items[:3000]:
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            path = resolve_local_media_url(
                url,
                output_dir=output_dir,
                upload_dir=upload_dir,
                canvas_files_dir=canvas_files_dir,
            )
            if not path or not path.is_file():
                continue
            requested = sanitize_filename(str(item.get("name") or ""))
            name = requested if requested != "untitled" else sanitize_filename(path.name)
            stem, suffix = Path(name).stem, Path(name).suffix
            candidate = name
            index = 2
            while candidate.lower() in used:
                candidate = f"{stem}_{index}{suffix}"
                index += 1
            used.add(candidate.lower())
            archive.write(path, candidate)
    return buffer.getvalue()


async def pack_canvas_assets(
    items: list[dict],
    filename: str | None = None,
    *,
    output_dir: Path = OUTPUT_DIR,
    upload_dir: Path = UPLOAD_DIR,
    canvas_files_dir: Path = CANVAS_FILES_DIR,
) -> tuple[bytes, str]:
    safe_name = sanitize_filename(filename or "canvas-assets.zip")
    if not safe_name.lower().endswith(".zip"):
        safe_name += ".zip"
    content = await asyncio.to_thread(
        _build_assets_zip, items, output_dir, upload_dir, canvas_files_dir
    )
    return content, safe_name


def _collect_resource_urls(nodes: list) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        source = node.get("data") if isinstance(node.get("data"), dict) else node
        for field in ("src", "image_url", "video_url"):
            url = str(source.get(field) or "")
            if url and url not in seen:
                seen.add(url)
                found.append(url)
    return found


def _build_workflow_zip(payload: dict, include_resources: bool) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        workflow = {key: payload[key] for key in ("nodes", "connections", "viewport") if key in payload}
        archive.writestr("workflow.json", json.dumps(workflow, ensure_ascii=False, indent=2))
        if include_resources:
            used: set[str] = set()
            for url in _collect_resource_urls(payload.get("nodes", [])):
                path = resolve_local_media_url(url)
                if not path or not path.is_file():
                    continue
                name = sanitize_filename(path.name)
                candidate = name
                index = 2
                while candidate.lower() in used:
                    candidate = f"{Path(name).stem}_{index}{Path(name).suffix}"
                    index += 1
                used.add(candidate.lower())
                archive.write(path, f"resources/{candidate}")
    return buffer.getvalue()


async def build_workflow_zip(payload: dict) -> bytes:
    return await asyncio.to_thread(
        _build_workflow_zip, payload, bool(payload.get("include_resources"))
    )


def _parse_workflow_zip(content: bytes) -> dict:
    stripped = content.lstrip(b"\xef\xbb\xbf").strip()
    if stripped and stripped[0:1] in (b"{", b"["):
        try:
            data = json.loads(stripped.decode("utf-8-sig"))
            if isinstance(data, list):
                return {"nodes": data, "connections": []}
            return {
                "nodes": data.get("nodes", []),
                "connections": data.get("connections", []),
                "viewport": data.get("viewport"),
            }
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = _validate_archive(archive)
            target = members.get("workflow.json")
            if target is None:
                target = next((info for name, info in members.items() if name.lower().endswith(".json")), None)
            if target is None:
                raise HTTPException(400, "未找到工作流 JSON")
            data = _read_json_bytes(archive.read(target), "工作流 JSON 格式错误")
            return {
                "nodes": data.get("nodes", []),
                "connections": data.get("connections", []),
                "viewport": data.get("viewport"),
            }
    except zipfile.BadZipFile as exc:
        raise HTTPException(400, "导入失败: 无效的工作流文件，支持 .json 或 .zip") from exc


async def parse_workflow_zip(content: bytes) -> dict:
    return await asyncio.to_thread(_parse_workflow_zip, content)
