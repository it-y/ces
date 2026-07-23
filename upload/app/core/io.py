"""本地文件的原子读写工具。"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path


def write_bytes_atomic_sync(path: Path, content: bytes) -> None:
    """在目标目录写临时文件并原子替换目标。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".tmp_", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def write_text_atomic_sync(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    write_bytes_atomic_sync(path, content.encode(encoding))


async def write_bytes_atomic(path: Path, content: bytes) -> None:
    await asyncio.to_thread(write_bytes_atomic_sync, path, content)


async def write_text_atomic(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    await asyncio.to_thread(write_text_atomic_sync, path, content, encoding=encoding)
