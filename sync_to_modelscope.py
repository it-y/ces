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
import time
import zipfile
import tempfile
import shutil
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()

# Keep consistent with updater.py ALLOWED_SOURCE_PREFIXES
ALLOWED_PREFIXES = {"app/", "static/", "workflows/", "VERSION"}

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


def build_release_zip(files: list[Path], version: str, out_dir: Path) -> Path:
    """把所有更新文件打成一个 update-{version}.zip（zip 内为相对路径，与 updater 白名单一致）。"""
    zip_path = out_dir / f"update-{version}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            rel = f.relative_to(BASE_DIR).as_posix()
            zf.write(str(f), rel)
    return zip_path


def git_clean_remote(token: str) -> None:
    """用 git 删除数据集里除 VERSION / update-*.zip / .gitattributes 外的所有文件。

    说明：ModelScope 的 SDK 同步删除（sync_remote_repo）会因仓库策略被拒（400），
    而 git 协议的删除提交是允许的。这里只删不增（zip 内容不变，LFS 对象不动），
    因此不需要安装 git-lfs。
    """
    tmp = Path(tempfile.mkdtemp(prefix="ms_clean_"))
    env = {**os.environ, "GIT_LFS_SKIP_SMUDGE": "1", "GIT_TERMINAL_PROMPT": "0"}
    try:
        url = f"https://oauth2:{token}@www.modelscope.cn/datasets/{DATASET_ID}.git"
        repo = tmp / "repo"
        subprocess.run(
            ["git", "clone", "--depth", "1", url, str(repo)],
            check=True, env=env, capture_output=True,
        )
        keep_names = {"VERSION", ".gitattributes"}
        for entry in repo.iterdir():
            if entry.name in keep_names or (entry.name.startswith("update-") and entry.name.endswith(".zip")):
                continue
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.name=release", "-c", "user.email=release@local",
             "commit", "-m", "clean: keep only VERSION + update zip"],
            cwd=repo, check=True, env=env, capture_output=True,
        )
        subprocess.run(["git", "push", "origin", "HEAD"], cwd=repo, check=True, env=env, capture_output=True)
        print("Clean done. Remote keeps only VERSION + update-*.zip.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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
    print(f"Files to package: {len(files)}")

    try:
        from modelscope.hub.api import HubApi
    except ImportError:
        print("[ERROR] Please install modelscope: pip install modelscope")
        sys.exit(1)

    api = HubApi()
    api.login(token)

    # 只上传 2 个文件：VERSION（版本探测用）+ update-{version}.zip（下载用）。
    # 上传后自动 git 清理远端散文件（SDK 的 sync_remote_repo 删除会被魔塔策略拒绝，
    # git 协议的删除提交则允许），数据集永远只有最新 2 个文件，不会越堆越多。
    # 顺序是先传后删：若上传失败，旧数据原样保留，不会把数据集搞空。
    staging = Path(tempfile.mkdtemp(prefix="ms_sync_"))
    try:
        (staging / "VERSION").write_text(version + "\n", encoding="utf-8")
        zip_path = build_release_zip(files, version, staging)
        size_kb = zip_path.stat().st_size / 1024
        print(f"Packed: {zip_path.name} ({size_kb:.1f} KB)")

        # 整体重试：网络波动时自动重跑（上传/清理都是幂等的，重跑安全）
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                print(f"[{attempt}/{max_attempts}] Uploading VERSION + zip...")
                api.upload_folder(
                    repo_id=DATASET_ID,
                    repo_type="dataset",
                    folder_path=str(staging),
                    path_in_repo="",
                    commit_message=f"Release {version}",
                )
                print(f"[{attempt}/{max_attempts}] Cleaning remote leftovers via git...")
                git_clean_remote(token)
                print(f"Done. Version {version} synced to ModelScope (zip mode, clean).")
                return
            except Exception as e:
                if attempt < max_attempts:
                    wait = 5 * attempt
                    print(f"[{attempt}/{max_attempts}] 失败: {e}，{wait}s 后重试...")
                    time.sleep(wait)
                else:
                    print(f"[ERROR] 重试 {max_attempts} 次仍失败: {e}")
                    sys.exit(1)
    finally:
        shutil.rmtree(str(staging), ignore_errors=True)


if __name__ == "__main__":
    main()
