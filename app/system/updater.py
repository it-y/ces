"""
系统更新 — 版本检查、双源下载、备份回滚、跨平台重启。

更新源：GitHub（主）+ ModelScope（备用）。
"""

import asyncio
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from ..config import (
    BASE_DIR, DATA_DIR, GITHUB_REPO_URL,
    GITHUB_VERSION_URL, GITHUB_TREE_URL, GITHUB_RAW_ROOT,
    GITHUB_MANIFEST_URL,
    MODELSCOPE_VERSION_URL, MODELSCOPE_TREE_URL, MODELSCOPE_FILE_API_ROOT,
    MODELSCOPE_DATASET_ID,
    current_app_version, load_github_token, load_modelscope_token,
)
from ..core.http_client import create_client, request_with_fallback

_update_lock = asyncio.Lock()

STAGING_REQUIRED_FILES = {"VERSION", "app/main.py", "static/index.html"}

ALLOWED_SOURCE_PREFIXES = {"app/", "static/", "upload/", "workflows/", "VERSION"}

EXCLUDED_PREFIXES = {
    "data/", "__pycache__/", ".venv/", "venv/",
    ".git/", "node_modules/", ".pytest_cache/",
}

_UNSAFE_PATH_RE = re.compile(r"(\.\.|^/|^[A-Za-z]:[\\/]|\\\\|^\.)")

_GITHUB_AUTH_CACHE: dict = {}
def _github_auth_headers() -> dict:
    if "headers" not in _GITHUB_AUTH_CACHE:
        token = load_github_token()
        _GITHUB_AUTH_CACHE["headers"] = (
            {"Authorization": f"Bearer {token}"} if token else {}
        )
    return _GITHUB_AUTH_CACHE["headers"]


_MODELSCOPE_AUTH_CACHE: dict = {}
def _modelscope_auth_headers() -> dict:
    if "headers" not in _MODELSCOPE_AUTH_CACHE:
        token = load_modelscope_token()
        _MODELSCOPE_AUTH_CACHE["headers"] = (
            {"Authorization": f"Bearer {token}"} if token else {}
        )
    return _MODELSCOPE_AUTH_CACHE["headers"]


def _is_safe_relative_path(path: str) -> bool:
    if not path or path.startswith("/"):
        return False
    if _UNSAFE_PATH_RE.search(path):
        return False
    normalized = Path(path).as_posix()
    if normalized != path:
        return False
    if normalized.startswith(".."):
        return False
    return True


def _is_allowed_path(path: str) -> bool:
    if not _is_safe_relative_path(path):
        return False
    for prefix in EXCLUDED_PREFIXES:
        if path.startswith(prefix):
            return False
    for prefix in ALLOWED_SOURCE_PREFIXES:
        if path == prefix.rstrip("/") or path.startswith(prefix):
            return True
    return False


async def check_update() -> dict:
    """并发检查 GitHub + ModelScope，返回最高版本信息"""
    async def probe(label: str, url: str) -> dict | None:
        try:
            headers = _github_auth_headers() if label == "github" else _modelscope_auth_headers()
            async with create_client("quick") as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    version = resp.text.strip().splitlines()[0].strip()
                    return {"source": label, "version": version}
        except Exception:
            pass
        return None

    results = await asyncio.gather(
        probe("github", GITHUB_VERSION_URL),
        probe("modelscope", MODELSCOPE_VERSION_URL),
    )

    current = current_app_version()
    latest = current
    source = None

    for r in results:
        if r and _version_newer(r["version"], latest):
            latest = r["version"]
            source = r["source"]

    # 兜底：raw CDN 失败时切 GitHub API
    if source is None:
        try:
            headers = _github_auth_headers()
            api_url = GITHUB_REPO_URL.replace("https://github.com/", "https://api.github.com/repos/")
            async with create_client("quick") as client:
                resp = await client.get(f"{api_url}/contents/VERSION", headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    import base64
                    version = base64.b64decode(data.get("content", "")).decode().strip().splitlines()[0].strip()
                    if version and _version_newer(version, current):
                        latest = version
                        source = "github"
        except Exception:
            pass

    return {
        "current": current,
        "has_update": latest != current and source is not None,
        "latest": {"version": latest, "source": source} if source else None,
    }


def _version_newer(a: str, b: str) -> bool:
    """比较两个版本号字符串"""
    def parse(v: str) -> list[int]:
        return [int(x) for x in re.findall(r"\d+", v)]
    try:
        na = parse(a)
        nb = parse(b)
        min_len = min(len(na), len(nb))
        for i in range(min_len):
            if na[i] != nb[i]:
                return na[i] > nb[i]
        return len(na) > len(nb)
    except Exception:
        return a > b


async def download_update(source: str = "github", fallback: bool = True) -> Path:
    """下载更新文件到临时目录。fallback=True 时主源失败自动切换备用源。"""
    if source not in ("github", "modelscope"):
        raise ValueError(f"Unknown update source: {source}")

    sources_to_try = [source]
    if fallback:
        alt = "modelscope" if source == "github" else "github"
        sources_to_try.append(alt)

    last_error = None
    for src in sources_to_try:
        try:
            return await _do_download(src)
        except Exception as e:
            last_error = e

    raise RuntimeError(
        f"Download failed from all sources: {', '.join(sources_to_try)}"
    ) from last_error


async def _do_download(source: str) -> Path:
    staging = DATA_DIR / "update" / "staging" / f"{int(time.time())}"
    staging.mkdir(parents=True, exist_ok=True)

    if source == "github":
        file_list = await _fetch_github_file_list()
    else:
        file_list = await _fetch_modelscope_file_list()

    allowed_files = [p for p in file_list if _is_allowed_path(p)]
    if not allowed_files:
        raise RuntimeError(f"No allowed files found from {source}")

    await _download_files(source, allowed_files, staging)

    _validate_staging_has_required(staging)

    return staging


async def _fetch_github_file_list() -> list[str]:
    headers = _github_auth_headers()
    # 直连优先，失败自动走系统代理（GitHub 国内直连常被墙）；跟随重定向（raw 会返回 302）
    tree_code = None
    resp = await request_with_fallback(
        "GET", GITHUB_TREE_URL, timeout_preset="normal",
        follow_redirects=True, headers=headers,
    )
    tree_code = resp.status_code
    if resp.status_code == 200:
        data = resp.json()
        tree = data.get("tree", [])
        return [item["path"] for item in tree if item.get("type") == "blob"]
    resp = await request_with_fallback(
        "GET", GITHUB_MANIFEST_URL, timeout_preset="normal",
        follow_redirects=True, headers=headers,
    )
    manifest_code = resp.status_code
    if resp.status_code == 200:
        lines = resp.text.strip().splitlines()
        return [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]
    raise RuntimeError(
        f"GitHub file list unavailable: tree API={tree_code}, manifest={manifest_code}"
    )


async def _fetch_modelscope_file_list() -> list[str]:
    headers = _modelscope_auth_headers()
    all_files = []
    page = 1
    while True:
        url = f"{MODELSCOPE_TREE_URL}&PageNumber={page}&PageSize=100"
        resp = await request_with_fallback(
            "GET", url, timeout_preset="normal", follow_redirects=True, headers=headers,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"ModelScope tree returned {resp.status_code}")
        data = resp.json()
        files = data.get("Data", {}).get("Files", [])
        if not files:
            break
        for item in files:
            if isinstance(item, dict) and item.get("Type") == "blob" and item.get("Path"):
                all_files.append(item["Path"])
        total = data.get("Data", {}).get("TotalCount") or data.get("TotalCount", 0)
        if len(all_files) >= total:
            break
        page += 1
    return all_files


async def _download_files(source: str, files: list[str], staging: Path):
    gh_headers = _github_auth_headers() if source == "github" else {}
    ms_headers = _modelscope_auth_headers() if source == "modelscope" else {}

    # 并发上限：一次性并发全部文件会把 httpx 连接池打满，
    # 上游响应慢时排队请求命中 pool=20s 的 PoolTimeout，导致整个更新中止。
    # 限流后连接池始终有富余，逐个波次下载。
    CONCURRENCY = 40
    semaphore = asyncio.Semaphore(CONCURRENCY)
    failures: list[str] = []

    async def download_one(path: str):
        url = f"{GITHUB_RAW_ROOT}/{path}" if source == "github" else f"{MODELSCOPE_FILE_API_ROOT}{path}"
        headers = gh_headers if source == "github" else ms_headers
        # request_with_fallback：直连优先，失败自动走系统代理（VPN），并跟随重定向
        last_err = ""
        # 单文件重试：瞬时错误（限流 429 / 5xx / 连接异常）重试最多 3 次
        for attempt in range(3):
            try:
                async with semaphore:
                    resp = await request_with_fallback(
                        "GET", url, timeout_preset="normal",
                        follow_redirects=True, headers=headers,
                    )
                if resp.status_code != 200:
                    if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                        last_err = f"HTTP {resp.status_code}"
                        await asyncio.sleep(1 + attempt)
                        continue
                    failures.append(f"{path}: HTTP {resp.status_code}")
                    return
                dest = staging / path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(resp.content)
                return
            except Exception as exc:
                last_err = str(exc)
                if attempt < 2:
                    await asyncio.sleep(1 + attempt)
                    continue
        failures.append(f"{path}: {last_err or 'unknown'}")

    await asyncio.gather(*[download_one(f) for f in files])

    if failures:
        # 关键文件缺失才判定失败；少量文件失败且不影响关键文件时容忍
        missing_required = [r for r in STAGING_REQUIRED_FILES if not (staging / r).exists()]
        if missing_required or len(failures) > max(3, len(files) * 0.05):
            raise RuntimeError(
                f"部分文件下载失败 ({len(failures)}/{len(files)})："
                + "; ".join(failures[:5])
            )


def _validate_staging_has_required(staging: Path):
    missing = [r for r in STAGING_REQUIRED_FILES if not (staging / r).exists()]
    if missing:
        raise RuntimeError(f"Staging missing required files: {', '.join(missing)}")


def _get_staging_version(staging: Path) -> str:
    version_file = staging / "VERSION"
    if version_file.exists():
        return version_file.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    return "unknown"


def _list_relative_files(base: Path) -> list[str]:
    return sorted(
        entry.relative_to(base).as_posix()
        for entry in base.rglob("*") if entry.is_file()
    )


def _write_json(path: Path, data: dict):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


async def apply_update(staging_path: Path, declared_version: str | None = None) -> dict:
    """安装 staging 目录的文件到 BASE_DIR，含备份和自动回滚。"""
    async with _update_lock:
        rel_files = _list_relative_files(staging_path)
        _validate_staging_has_required(staging_path)

        version = declared_version or _get_staging_version(staging_path)
        backup_id = f"{int(time.time())}_{version}"
        backup_dir = DATA_DIR / "update" / "backups" / backup_id
        backup_files_dir = backup_dir / "files"

        backup_dir.mkdir(parents=True, exist_ok=True)

        manifest = {
            "backup_id": backup_id,
            "version": version,
            "created_at": time.time(),
            "files": [],
        }

        installed = []

        try:
            for rel_path in rel_files:
                src = staging_path / rel_path
                dst = BASE_DIR / rel_path
                dst.parent.mkdir(parents=True, exist_ok=True)

                was_new = not dst.exists()
                if dst.exists():
                    backup_dst = backup_files_dir / rel_path
                    backup_dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(dst), str(backup_dst))

                manifest["files"].append({"path": rel_path, "was_new": was_new})

                tmp = dst.parent / f".{dst.name}.tmp"
                try:
                    shutil.copy2(str(src), str(tmp))
                    os.replace(str(tmp), str(dst))
                    installed.append(rel_path)
                finally:
                    tp = Path(tmp)
                    if tp.exists():
                        tp.unlink()

            _write_json(backup_dir / "manifest.json", manifest)

            marker = DATA_DIR / ".applied_version"
            try:
                marker.write_text(version, encoding="utf-8")
            except Exception:
                pass

            return {
                "ok": True,
                "backup_id": backup_id,
                "files_installed": len(installed),
                "version": version,
                "count": len(installed),
                "restart_scheduled": True,
            }

        except BaseException:
            for rel_path in reversed(installed):
                dst = BASE_DIR / rel_path
                backup_src = backup_files_dir / rel_path
                if backup_src.exists():
                    tmp = dst.parent / f".{dst.name}.tmp"
                    try:
                        shutil.copy2(str(backup_src), str(tmp))
                        os.replace(str(tmp), str(dst))
                    finally:
                        tp = Path(tmp)
                        if tp.exists():
                            tp.unlink()
                elif dst.exists():
                    dst.unlink()

            if backup_dir.exists():
                shutil.rmtree(str(backup_dir))
            raise


async def rollback_update(backup_id: str) -> dict:
    """从备份恢复文件。"""
    async with _update_lock:
        backup_dir = DATA_DIR / "update" / "backups" / backup_id
        if not backup_dir.exists():
            raise FileNotFoundError(f"Backup not found: {backup_id}")

        manifest_path = backup_dir / "manifest.json"
        if not manifest_path.exists():
            raise RuntimeError(f"Manifest not found in backup: {backup_id}")

        manifest = _read_json(manifest_path)
        backup_files_dir = backup_dir / "files"

        restored = []

        try:
            for entry in reversed(manifest.get("files", [])):
                rel_path = entry["path"]
                dst = BASE_DIR / rel_path
                backup_src = backup_files_dir / rel_path

                if backup_src.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    tmp = dst.parent / f".{dst.name}.tmp"
                    try:
                        shutil.copy2(str(backup_src), str(tmp))
                        os.replace(str(tmp), str(dst))
                        restored.append(rel_path)
                    finally:
                        tp = Path(tmp)
                        if tp.exists():
                            tp.unlink()
                elif entry.get("was_new"):
                    if dst.exists():
                        dst.unlink()
                        restored.append(rel_path)

            return {
                "ok": True,
                "backup_id": backup_id,
                "files_restored": len(restored),
            }
        except BaseException:
            raise


def is_electron() -> bool:
    """检测是否运行在 Electron 子进程中"""
    return os.environ.get("ELECTRON_RUN") == "1"


def schedule_restart() -> None:
    """跨平台重启脚本"""
    if is_electron():
        return
    if os.name == "nt":
        _schedule_windows_restart()
    else:
        _schedule_unix_restart()


def _schedule_windows_restart():
    bat = BASE_DIR / "_self_restart.bat"

    launcher = BASE_DIR / "run.bat"
    if not launcher.exists():
        launcher = BASE_DIR / "启动服务.bat"
    if not launcher.exists():
        launcher = BASE_DIR / "start.bat"

    if launcher.exists():
        cmd = f'start "" cmd /k call "{launcher}"'
    else:
        cmd = (
            'start "" cmd /k python -m uvicorn '
            "app.main:app --host 127.0.0.1 --port 3000"
        )

    script = f"""@echo off
timeout /t 2 /nobreak >nul
taskkill /F /PID {os.getpid()} >nul 2>&1
timeout /t 1 /nobreak >nul
cd /d "{BASE_DIR}"
{cmd}
"""
    bat.write_text(script, encoding="utf-8")
    subprocess.Popen(
        f'cmd /c start "" /min "{bat}"',
        shell=True, creationflags=0x00000008,
    )


def _schedule_unix_restart():
    sh = BASE_DIR / "_self_restart.sh"
    launcher = BASE_DIR / "mac-启动服务.command"
    if not launcher.exists():
        launcher = BASE_DIR / "start.sh"

    if launcher.exists():
        cmd = f'open "{launcher}"'
    else:
        cmd = "python -m uvicorn app.main:app --host 127.0.0.1 --port 3000 &"

    script = f"""#!/bin/bash
sleep 2
kill -9 {os.getpid()} 2>/dev/null
sleep 1
cd "{BASE_DIR}"
{cmd}
"""
    sh.write_text(script)
    os.chmod(str(sh), 0o755)
    subprocess.Popen(["bash", str(sh)])
