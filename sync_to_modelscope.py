"""
Sync release files to ModelScope dataset for China-accessible updates.
Usage: python sync_to_modelscope.py

Before first use:
  1. Add "modelscope_token": "ms-xxx" to data/settings.json
  2. Or set env var: MODELSCOPE_TOKEN=ms-xxx
"""
import os
import sys
import json
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()

# Keep consistent with updater.py ALLOWED_SOURCE_PREFIXES
ALLOWED_PREFIXES = {"app/", "static/", "upload/", "workflows/", "VERSION"}

DATASET_ID = "ytk001/ces"


def load_token() -> str:
    token = os.getenv("MODELSCOPE_TOKEN", "")
    if token:
        return token
    sp = BASE_DIR / "data" / "settings.json"
    if sp.exists():
        try:
            data = json.loads(sp.read_text(encoding="utf-8"))
            token = data.get("modelscope_token", "")
        except Exception:
            pass
    return token


def collect_files() -> list[Path]:
    """Collect files to upload (same filtering as updater._is_allowed_path)."""
    files: list[Path] = []
    for prefix in ALLOWED_PREFIXES:
        if prefix.endswith("/"):
            pdir = BASE_DIR / prefix
            if pdir.is_dir():
                for f in pdir.rglob("*"):
                    if not f.is_file():
                        continue
                    if "__pycache__" in f.parts or f.name.endswith(".pyc"):
                        continue
                    files.append(f.resolve())
        else:
            f = BASE_DIR / prefix
            if f.is_file():
                files.append(f.resolve())

    # Extra files not under ALLOWED_PREFIXES but needed
    for extra in ("CLAUDE.md", "requirements.txt", "run.bat",
                  "mac-启动服务.command", "start.sh",
                  "electron/main.js", "electron/preload.js", "electron/package.json"):
        f = BASE_DIR / extra
        if f.is_file():
            files.append(f.resolve())

    return sorted(set(files))


def main():
    token = load_token()
    if not token:
        print("[ERROR] ModelScope Token not found.")
        print("  Add to data/settings.json: \"modelscope_token\": \"ms-xxx\"")
        print("  Or set env var: MODELSCOPE_TOKEN=ms-xxx")
        sys.exit(1)

    # Read current version
    ver_file = BASE_DIR / "VERSION"
    version = ver_file.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    print(f"Version: {version}")
    print(f"Target:  {DATASET_ID}")

    files = collect_files()
    print(f"Files to upload: {len(files)}")

    try:
        from modelscope.hub.api import HubApi
    except ImportError:
        print("[ERROR] Please install modelscope: pip install modelscope")
        sys.exit(1)

    api = HubApi()
    api.login(token)

    # Build staging dir, copy all files, upload in one commit
    import tempfile, shutil
    staging = Path(tempfile.mkdtemp(prefix="ms_sync_"))
    try:
        for f in files:
            rel = f.relative_to(BASE_DIR).as_posix()
            dst = staging / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(f), str(dst))

        print(f"Uploading {len(files)} files in one commit...")
        api.upload_folder(
            repo_id=DATASET_ID,
            repo_type="dataset",
            folder_path=str(staging),
            path_in_repo="",
            commit_message=f"Release {version}",
        )
        print(f"Done. Version {version} synced to ModelScope.")
    finally:
        shutil.rmtree(str(staging), ignore_errors=True)


if __name__ == "__main__":
    main()
