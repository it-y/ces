"""
同步发布文件到 ModelScope 数据集（国内更新源）。
用法: python sync_to_modelscope.py

首次使用前：
  1. 在 data/settings.json 中添加 "modelscope_token": "ms-xxx"
  2. 或设置环境变量 MODELSCOPE_TOKEN=ms-xxx
"""
import os
import sys
import json
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()

# ---- 与 updater.py 保持一致的过滤规则 ----
ALLOWED_PREFIXES = {"app/", "static/", "upload/", "workflows/", "VERSION"}
EXCLUDED_PREFIXES = {
    "data/", "__pycache__/", ".venv/", "venv/",
    ".git/", "node_modules/", ".pytest_cache/",
}

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
    """收集需要上传的文件（与 updater _is_allowed_path 规则一致）"""
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
            # Single file like VERSION
            f = BASE_DIR / prefix
            if f.is_file():
                files.append(f.resolve())

    # Also include CLAUDE.md and requirements
    for extra in ("CLAUDE.md", "requirements.txt", "run.bat", "启动服务.bat",
                  "mac-启动服务.command", "start.sh", "electron/main.js",
                  "electron/preload.js", "electron/package.json"):
        f = BASE_DIR / extra
        if f.is_file():
            files.append(f.resolve())

    return sorted(set(files))


def main():
    token = load_token()
    if not token:
        print("❌ 未找到 ModelScope Token。")
        print("   请在 data/settings.json 中添加:  \"modelscope_token\": \"ms-xxx\"")
        print("   或设置环境变量:  set MODELSCOPE_TOKEN=ms-xxx")
        sys.exit(1)

    # 读取当前版本号
    ver_file = BASE_DIR / "VERSION"
    version = ver_file.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    print(f"📦 当前版本: {version}")
    print(f"📂 目标仓库: {DATASET_ID}")

    files = collect_files()
    print(f"📋 待上传文件: {len(files)} 个")

    try:
        from modelscope.hub.api import HubApi
    except ImportError:
        print("❌ 请先安装 modelscope: pip install modelscope")
        sys.exit(1)

    api = HubApi()
    api.login(token)

    # 使用 file_download / file_upload API 逐个上传
    # ModelScope SDK 的 upload 方法签名：
    #   api.upload_file(repo_id, local_path, path_in_repo, ...)
    success = 0
    failed = []

    for f in files:
        rel = f.relative_to(BASE_DIR).as_posix()
        try:
            # HubApi.upload_file 上传单个文件
            result = api.upload_file(
                repo_id=DATASET_ID,
                local_path=str(f),
                path_in_repo=rel,
                repo_type="dataset",
                commit_message=f"Release {version}",
            )
            success += 1
            print(f"  ✅ {rel}")
        except Exception as e:
            failed.append((rel, str(e)))
            print(f"  ❌ {rel}: {e}")

    print(f"\n{'='*50}")
    print(f"✅ 成功: {success}   ❌ 失败: {len(failed)}")
    if failed:
        print("失败文件:")
        for path, err in failed:
            print(f"  - {path}: {err}")
    else:
        print(f"🎉 版本 {version} 已同步到魔搭！国内用户可直连更新。")


if __name__ == "__main__":
    main()
