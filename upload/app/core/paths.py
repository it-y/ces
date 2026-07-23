"""本地路径与媒体 URL 的安全解析工具。"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import unquote, urlsplit

from fastapi import HTTPException

_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _bad_path(message: str = "非法路径") -> HTTPException:
    return HTTPException(status_code=400, detail=message)


def safe_path_join(base: Path, relative: str | Path) -> Path:
    """将不受信任的相对路径限制在 ``base`` 内。"""
    text = str(relative or "").strip()
    if not text or "\x00" in text:
        raise _bad_path()
    normalized = unquote(text).replace("\\", "/")
    if normalized.startswith("/") or _DRIVE_RE.match(normalized):
        raise _bad_path()
    parts = PurePosixPath(normalized).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise _bad_path()

    root = base.resolve()
    candidate = root.joinpath(*parts).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise _bad_path() from exc
    return candidate


def validate_zip_member(name: str) -> PurePosixPath:
    """校验 ZIP 中的成员名，只允许普通 POSIX 相对路径。"""
    text = str(name or "").strip()
    if not text or "\x00" in text or "\\" in text:
        raise _bad_path("ZIP 中包含非法路径")
    if PureWindowsPath(text).drive:
        raise _bad_path("ZIP 中包含非法路径")
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise _bad_path("ZIP 中包含非法路径")
    return path


def validate_simple_filename(name: str, *, suffix: str | None = None) -> str:
    """只接受不含目录部分的文件名，可选强制后缀。"""
    text = str(name or "").strip()
    if (
        not text
        or "\x00" in text
        or "/" in text
        or "\\" in text
        or text in {".", ".."}
        or PureWindowsPath(text).drive
    ):
        raise _bad_path("非法文件名")
    if suffix and not text.lower().endswith(suffix.lower()):
        raise _bad_path(f"文件名必须以 {suffix} 结尾")
    return text


def resolve_local_media_url(
    url: str,
    *,
    output_dir: Path | None = None,
    upload_dir: Path | None = None,
    canvas_files_dir: Path | None = None,
) -> Path | None:
    """解析本地媒体 URL；远程 URL 和未知前缀返回 ``None``。"""
    if output_dir is None or upload_dir is None or canvas_files_dir is None:
        from ..config import CANVAS_FILES_DIR, OUTPUT_DIR, UPLOAD_DIR

        output_dir = output_dir or OUTPUT_DIR
        upload_dir = upload_dir or UPLOAD_DIR
        canvas_files_dir = canvas_files_dir or CANVAS_FILES_DIR

    path = urlsplit(str(url or "")).path
    mappings = (
        ("/output/", output_dir),
        ("/assets/", upload_dir),
        ("/cfiles/", canvas_files_dir),
    )
    for prefix, root in mappings:
        if path.startswith(prefix):
            return safe_path_join(root, path[len(prefix):])
    return None
