"""
系统路由 — /api/providers, /api/check-update, /api/update-* 等。
"""

import asyncio
import json
import uuid
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import FileResponse
from .models import UpdateRequest, RollbackRequest, TokenRequest, ApiProviderPayload
from .providers import (
    load_providers, save_providers, get_provider,
    public_provider, mask_secret, provider_api_key,
)
from .updater import check_update, download_update, apply_update, rollback_update, schedule_restart, _update_lock, is_electron
from ..config import current_app_version, GITHUB_REPO_URL, MODELSCOPE_REPO_URL, DATA_DIR
from ..core.http_client import create_client
from ..core.websocket import manager as ws_manager
from ..core.errors import read_json, write_atomic
from ..core.security import ensure_same_origin_request, safe_path_join

router = APIRouter(prefix="/api", tags=["system"])
_shared_folders_lock = asyncio.Lock()


# ---- 供应商 ----

@router.get("/providers")
async def api_list_providers():
    providers = await load_providers()
    return {"providers": [public_provider(p) for p in providers]}


@router.put("/providers")
async def api_update_providers(payload: list[dict]):
    """批量更新供应商配置"""
    providers = await load_providers()
    existing_ids = {p["id"] for p in providers}
    for item in payload:
        pid = item.get("id", "")
        if pid in existing_ids:
            for p in providers:
                if p["id"] == pid:
                    p.update({k: v for k, v in item.items() if v is not None})
                    # 处理清除密钥标记
                    if item.get("clear_key"):
                        p.pop("api_key", None)
                    if item.get("clear_wallet_key"):
                        p.pop("wallet_api_key", None)
                    if item.get("clear_volcengine_access_key"):
                        p.pop("volcengine_access_key_id", None)
                    if item.get("clear_volcengine_secret_key"):
                        p.pop("volcengine_secret_access_key", None)
                    break
        else:
            providers.append(item)
            existing_ids.add(pid)
    await save_providers(providers)
    # 重新加载以获取完整数据（含脱敏处理）
    updated = await load_providers()
    return {"ok": True, "providers": [public_provider(p) for p in updated]}


@router.post("/providers/test-connection")
async def providers_test_connection(req: dict):
    token = req.get("token", "") or req.get("api_key", "")
    base_url = req.get("base_url", "")
    provider_id = req.get("provider_id", "") or req.get("id", "")
    # 如果前端没传 key，尝试从已保存的供应商配置读取
    if not token and provider_id:
        saved = await get_provider(provider_id)
        if saved:
            token = saved.get("api_key", "") or provider_api_key(saved)
    if not token:
        return {"ok": False, "status": 400, "message": "缺少 API Key，请先在输入框填写 Key"}
    test_url = f"{base_url.rstrip('/')}/v1/models" if base_url else "https://api.openai.com/v1/models"
    try:
        async with create_client("quick") as client:
            resp = await client.get(test_url, headers={"Authorization": f"Bearer {token}"})
            if resp.status_code == 200:
                data = resp.json()
                models = [m.get("id", "") for m in data.get("data", []) if m.get("id")]
                # 按名称分类
                image_models = [m for m in models if any(k in m.lower() for k in ("image", "turbo", "flux", "banana"))]
                chat_models = [m for m in models if any(k in m.lower() for k in ("gpt", "qwen", "gemini", "claude", "llama", "chat"))]
                video_models = [m for m in models if any(k in m.lower() for k in ("veo", "sora", "video", "seedance"))]
                return {
                    "ok": True, "status": 200,
                    "model_count": len(models),
                    "image_models": image_models,
                    "chat_models": chat_models,
                    "video_models": video_models,
                    "all": models,
                    "protocol": req.get("protocol", "openai"),
                    "image_request_mode": req.get("image_request_mode", "openai"),
                }
            if resp.status_code == 401:
                return {"ok": False, "status": 401, "message": "API Key 无效"}
            return {"ok": False, "status": resp.status_code, "message": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"ok": False, "status": 0, "message": str(e)[:300]}


@router.post("/test-connection")
async def test_connection(req: dict):
    return await providers_test_connection(req)


_MODEL_IMAGE_KEYWORDS = (
    "image", "turbo", "flux", "banana", "dall", "sdxl",
    "stable", "imagen", "kolors", "midjourney",
)
_MODEL_CHAT_KEYWORDS = (
    "chatgpt", "gpt", "qwen", "gemini", "claude", "llama", "chat",
    "deepseek", "doubao", "glm", "kimi", "moonshot", "ernie",
    "mistral", "minimax", "spark", "hunyuan", "baichuan", "yi-", "phi", "abab",
)
_MODEL_VIDEO_KEYWORDS = (
    "veo", "sora", "video", "seedance", "wan", "runway",
    "kling", "pika", "hunyuan-video",
)


@router.post("/providers/fetch-models")
async def providers_fetch_models(req: dict):
    provider_id = req.get("provider_id", "") or req.get("id", "")
    provider = await get_provider(provider_id) if provider_id else None
    base = (provider or {}).get("base_url", "").rstrip("/") if provider else ""
    api_key = (provider or {}).get("api_key", "") if provider else ""
    base_url = req.get("base_url", "").strip() or base
    api_key = req.get("api_key", "").strip() or api_key
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    if not base_url:
        return {"total": 0, "image_models": [], "chat_models": [], "video_models": [], "all": []}

    def _model_id(m):
        if not isinstance(m, dict):
            return ""
        return str(m.get("id") or m.get("name") or m.get("model_id") or "").strip()

    try:
        async with create_client("normal") as client:
            url = f"{base_url.rstrip('/')}/v1/models"
            last_error = ""
            for attempt in range(2):
                try:
                    resp = await client.get(url, headers=headers)
                except Exception as e:
                    last_error = str(e)
                    if attempt == 0:
                        await asyncio.sleep(1.0)
                        continue
                    break
                if resp.status_code in (429,) or resp.status_code >= 500:
                    last_error = f"HTTP {resp.status_code}"
                    if attempt == 0:
                        await asyncio.sleep(1.0)
                        continue
                    return {"total": 0, "image_models": [], "chat_models": [], "video_models": [], "all": [], "error": last_error, "status": resp.status_code}
                if resp.status_code != 200:
                    return {"total": 0, "image_models": [], "chat_models": [], "video_models": [], "all": [], "error": resp.text[:200], "status": resp.status_code}
                raw = resp.json()
                data = raw.get("data") if isinstance(raw, dict) else raw
                if not isinstance(data, list):
                    data = []
                models = [mid for mid in map(_model_id, data) if mid]
                # 按名称分类（尽力建议，关键词覆盖主流平台）
                image_models = [m for m in models if any(k in m.lower() for k in _MODEL_IMAGE_KEYWORDS)]
                chat_models = [m for m in models if any(k in m.lower() for k in _MODEL_CHAT_KEYWORDS)]
                video_models = [m for m in models if any(k in m.lower() for k in _MODEL_VIDEO_KEYWORDS)]
                return {
                    "total": len(models),
                    "image_models": image_models,
                    "chat_models": chat_models,
                    "video_models": video_models,
                    "all": models,
                    "image_request_mode": req.get("image_request_mode", "openai"),
                }
            return {"total": 0, "image_models": [], "chat_models": [], "video_models": [], "all": [], "error": last_error}
    except Exception as e:
        return {"total": 0, "image_models": [], "chat_models": [], "video_models": [], "all": [], "error": str(e)}


@router.get("/fetch-models")
async def fetch_models(provider_id: str = ""):
    """从供应商 API 拉取模型列表"""
    if not provider_id:
        raise HTTPException(400, "缺少 provider_id")

    provider = await get_provider(provider_id)
    if not provider:
        raise HTTPException(400, f"供应商 {provider_id} 不存在")

    base = provider.get("base_url", "").rstrip("/")
    api_key = provider.get("api_key", "")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    try:
        async with create_client("normal") as client:
            resp = await client.get(f"{base}/v1/models", headers=headers)
            if resp.status_code == 200:
                raw = resp.json()
                data = raw.get("data") if isinstance(raw, dict) else raw
                models = []
                for m in (data if isinstance(data, list) else []):
                    mid = str(m.get("id") or m.get("name") or m.get("model_id") or "").strip()
                    if mid:
                        models.append(mid)
                return {"models": sorted(models)}
            return {"models": [], "error": resp.text[:200]}
    except Exception as e:
        return {"models": [], "error": str(e)}


# ---- 更新 ----

@router.get("/check-update")
async def api_check_update():
    return await check_update()


@router.post("/update-from-github")
async def api_update(req: UpdateRequest):
    if _update_lock.locked():
        raise HTTPException(409, detail="更新或回滚操作正在进行中")
    try:
        staging = await download_update(source=req.source, fallback=req.fallback)
        result = await apply_update(staging, declared_version=req.version)
        if req.auto_restart:
            if is_electron():
                result["electron_relaunch"] = True
                result["restart_scheduled"] = False
            else:
                schedule_restart()
        return result
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        detail = f"{type(e).__name__}: {e}"
        log_path = DATA_DIR / "update_error.log"
        try:
            log_path.write_text(f"[{__import__('time').strftime('%Y-%m-%d %H:%M:%S')}] {detail}\n{tb}\n", encoding="utf-8")
        except Exception:
            pass
        raise HTTPException(500, detail=detail)


@router.get("/update-backups")
async def list_backups():
    from pathlib import Path
    backups_dir = DATA_DIR / "update" / "backups"
    if backups_dir.exists():
        return [d.name for d in backups_dir.iterdir() if d.is_dir()]
    return []


@router.post("/update-rollback")
async def api_rollback(req: RollbackRequest):
    if _update_lock.locked():
        raise HTTPException(409, detail="更新或回滚操作正在进行中")
    result = await rollback_update(req.name)
    if req.auto_restart:
        if is_electron():
            result["electron_relaunch"] = True
            result["restart_scheduled"] = False
        else:
            schedule_restart()
    return result


# ---- 综合 ----

@router.get("/config")
async def api_config():
    """全局配置（前端 settings 页使用）"""
    from ..config import (
        APP_VERSION, CHAT_MODEL, IMAGE_MODEL, COMFYUI_INSTANCES,
        AI_BASE_URL, AI_API_KEY, CHAT_MODELS, IMAGE_MODELS, VIDEO_MODELS,
        MODELSCOPE_CHAT_MODELS, MODELSCOPE_API_KEY,
    )
    providers = await load_providers()
    chat_models = CHAT_MODELS or ["gpt-4o-mini"]
    return {
        "base_url": AI_BASE_URL,
        "chat_model": chat_models[0],
        "image_model": IMAGE_MODEL,
        "chat_models": chat_models,
        "image_models": IMAGE_MODELS,
        "video_models": VIDEO_MODELS,
        "comfy_instances": COMFYUI_INSTANCES,
        "api_providers": [public_provider(p) for p in providers],
        "has_api_key": bool(AI_API_KEY),
        "ms_chat_models": MODELSCOPE_CHAT_MODELS,
        "has_ms_key": bool(MODELSCOPE_API_KEY),
    }


@router.get("/config/token")
async def api_config_token():
    """API Key 状态"""
    from ..config import AI_API_KEY, MODELSCOPE_API_KEY
    return {
        "has_api_key": bool(AI_API_KEY),
        "has_ms_key": bool(MODELSCOPE_API_KEY),
    }


@router.get("/settings/github-token")
async def api_get_github_token():
    """返回 GitHub Token 是否存在"""
    from ..config import SETTINGS_PATH
    token = ""
    try:
        if SETTINGS_PATH.exists():
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            token = data.get("github_token", "")
    except Exception:
        pass
    return {"has_token": bool(token), "token": token}


@router.post("/settings/github-token")
async def api_set_github_token(req: TokenRequest):
    """保存 GitHub Token 到 settings.json"""
    from ..config import SETTINGS_PATH
    data = {}
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SETTINGS_PATH.exists():
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    data["github_token"] = req.token
    import tempfile, os
    tmp = SETTINGS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(str(tmp), str(SETTINGS_PATH))
    # 清除缓存
    from .updater import _GITHUB_AUTH_CACHE
    _GITHUB_AUTH_CACHE.pop("headers", None)
    return {"ok": True}


@router.get("/queue_status")
async def api_queue_status(client_id: str = ""):
    """生成队列状态（前端高频轮询）"""
    from ..comfyui.scheduler import scheduler
    status = await scheduler.queue_status()
    return {"total": status["pending"], "position": 0}


@router.get("/history")
async def api_history(type: str = ""):
    """生成历史"""
    from ..config import HISTORY_PATH
    import json
    try:
        if HISTORY_PATH.exists():
            data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            if type:
                data = [h for h in data if h.get("type") == type]
            return data[:100]
    except Exception:
        pass
    return []


@router.get("/shared-folders")
async def api_shared_folders():
    """共享文件夹列表"""
    from ..config import SHARED_FOLDERS_PATH
    data = await read_json(SHARED_FOLDERS_PATH)
    if isinstance(data, dict) and isinstance(data.get("folders"), list):
        return data
    if isinstance(data, dict):
        folders = [
            {"id": uuid.uuid5(uuid.NAMESPACE_URL, str(path)).hex, "name": name, "path": str(path)}
            for name, path in data.items()
        ]
        return {"folders": folders}
    return {"folders": []}


@router.post("/shared-folders")
async def api_register_shared_folder(request: Request, req: dict):
    ensure_same_origin_request(request)
    from ..config import BASE_DIR, SHARED_FOLDERS_PATH
    raw_path = str(req.get("path") or "").strip()
    if not raw_path:
        raise HTTPException(400, "缺少共享文件夹路径")
    path = Path(raw_path)
    if not path.is_absolute():
        path = BASE_DIR / path
    path = path.resolve()
    if not path.is_dir():
        raise HTTPException(404, "共享文件夹不存在")
    async with _shared_folders_lock:
        data = await api_shared_folders()
        existing = next((item for item in data["folders"] if Path(item["path"]).resolve() == path), None)
        if existing:
            return {"folder": existing}
        folder = {"id": uuid.uuid4().hex, "name": path.name or str(path), "path": str(path)}
        data["folders"].append(folder)
        await write_atomic(SHARED_FOLDERS_PATH, data)
    return {"folder": folder}


@router.delete("/shared-folders/{folder_id}")
async def api_unregister_shared_folder(folder_id: str, request: Request):
    ensure_same_origin_request(request)
    from ..config import SHARED_FOLDERS_PATH
    async with _shared_folders_lock:
        data = await api_shared_folders()
        before = len(data["folders"])
        data["folders"] = [item for item in data["folders"] if item.get("id") != folder_id]
        if len(data["folders"]) == before:
            raise HTTPException(404, "共享文件夹不存在")
        await write_atomic(SHARED_FOLDERS_PATH, data)
    return {"ok": True}


def _shared_media_kind(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        return "image"
    if suffix in {".mp4", ".webm", ".mov", ".m4v"}:
        return "video"
    if suffix in {".mp3", ".wav", ".m4a", ".flac", ".ogg"}:
        return "audio"
    return "file"


def _scan_shared_tree(root: Path, folder_id: str, current: Path | None = None, count: list[int] | None = None) -> dict:
    current = current or root
    count = count or [0]
    relative = current.relative_to(root).as_posix() if current != root else ""
    node = {"id": f"{folder_id}:{relative}", "name": current.name, "path": relative, "items": [], "children": []}
    for entry in sorted(current.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        if count[0] >= 5000:
            break
        if entry.is_dir():
            node["children"].append(_scan_shared_tree(root, folder_id, entry, count))
            continue
        count[0] += 1
        item_relative = entry.relative_to(root).as_posix()
        stat = entry.stat()
        node["items"].append({
            "id": f"{folder_id}:{item_relative}",
            "name": entry.name,
            "url": f"/api/shared-folders/{folder_id}/file?path={item_relative}",
            "kind": _shared_media_kind(entry.name),
            "size": stat.st_size,
            "lastModified": int(stat.st_mtime * 1000),
            "relativePath": item_relative,
            "folderId": folder_id,
        })
    return node


async def _get_shared_folder(folder_id: str) -> dict:
    data = await api_shared_folders()
    folder = next((item for item in data["folders"] if item.get("id") == folder_id), None)
    if not folder:
        raise HTTPException(404, "共享文件夹不存在")
    return folder


@router.get("/shared-folders/{folder_id}/tree")
async def api_shared_folder_tree(folder_id: str):
    folder = await _get_shared_folder(folder_id)
    root = Path(folder["path"]).resolve()
    if not root.is_dir():
        raise HTTPException(404, "共享文件夹不存在")
    tree = await asyncio.to_thread(_scan_shared_tree, root, folder_id)
    return {"folder": folder, "tree": tree}


@router.get("/shared-folders/{folder_id}/file")
async def api_shared_folder_file(folder_id: str, path: str):
    folder = await _get_shared_folder(folder_id)
    resolved = safe_path_join(Path(folder["path"]), path)
    if not resolved.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(resolved)


@router.post("/history/delete")
async def api_delete_history(req: dict):
    """删除历史记录"""
    from ..config import HISTORY_PATH

    path = HISTORY_PATH
    if not path.exists():
        return {"success": False, "message": "History file not found"}
    try:
        history = json.loads(path.read_text(encoding="utf-8"))
        target_timestamp = req.get("timestamp")
        target_record = None
        new_history = []
        for item in history:
            is_match = False
            item_ts = item.get("timestamp", 0)
            if isinstance(target_timestamp, (int, float)) and isinstance(item_ts, (int, float)):
                if abs(float(item_ts) - float(target_timestamp)) < 0.001:
                    is_match = True
            elif str(item_ts) == str(target_timestamp):
                is_match = True
            if is_match:
                target_record = item
            else:
                new_history.append(item)
        if target_record:
            path.write_text(json.dumps(new_history, ensure_ascii=True, indent=4), encoding="utf-8")
            # 尝试删除关联的图片文件
            for img_url in target_record.get("images", []):
                try:
                    from ..config import OUTPUT_DIR
                    import os
                    fname = os.path.basename(img_url.split("?")[0])
                    output_file = OUTPUT_DIR / fname
                    if output_file.exists():
                        output_file.unlink()
                except Exception:
                    pass
        return {"success": bool(target_record)}
    except Exception as e:
        return {"success": False, "message": str(e)}


@router.post("/providers/probe-async")
async def api_probe_async(req: dict):
    raise HTTPException(501, "该功能尚未实现")


@router.post("/update-connectivity")
async def api_update_connectivity(req: dict):
    from ..config import GITHUB_VERSION_URL, MODELSCOPE_VERSION_URL
    from .updater import _github_auth_headers

    async def _probe(label: str, url: str) -> bool:
        try:
            headers = _github_auth_headers() if label == "github" else {}
            async with create_client("quick") as client:
                resp = await client.head(url, headers=headers, timeout=5)
                return resp.status_code < 500
        except Exception:
            return False

    github, modelscope = await asyncio.gather(
        _probe("github", GITHUB_VERSION_URL), _probe("modelscope", MODELSCOPE_VERSION_URL),
    )
    return {"github": github, "modelscope": modelscope}


@router.get("/update-connectivity/probe")
async def api_probe_connectivity():
    raise HTTPException(501, "该功能尚未实现")


FOLDER_MAP = {}


def _build_folder_map():
    """延迟构建文件夹映射，避免循环导入"""
    if FOLDER_MAP:
        return
    from ..config import (
        CANVAS_DIR, CANVAS_FILES_DIR, CANVAS_TRASH_DIR,
        OUTPUT_DIR, UPLOAD_DIR,
        ASSET_DIR, WORKFLOW_LIBRARY_DIR, LOCAL_DIR,
        PROMPT_LIBRARY_DIR, TRASH_DIR,
        PROJECTS_DIR, HISTORY_DIR, CONFIG_DIR,
        WORKFLOW_DIR, DATA_DIR,
    )
    FOLDER_MAP.update({
        "canvases": CANVAS_DIR,
        "canvas-files": CANVAS_FILES_DIR,
        "canvas-trash": CANVAS_TRASH_DIR,
        "outputs": OUTPUT_DIR,
        "uploads": UPLOAD_DIR,
        "assets": ASSET_DIR,
        "workflows": WORKFLOW_LIBRARY_DIR,
        "local": LOCAL_DIR,
        "prompts": PROMPT_LIBRARY_DIR,
        "library-trash": TRASH_DIR,
        "projects": PROJECTS_DIR,
        "history": HISTORY_DIR,
        "config": CONFIG_DIR,
        "comfyui": WORKFLOW_DIR,
        "data-root": DATA_DIR,
    })


@router.post("/open-folder")
async def api_open_folder(
    key: Optional[str] = Body(None),
    canvas_id: Optional[str] = Body(None),
    path: Optional[str] = Body(None),
):
    """在操作系统中打开数据文件夹或画布文件"""
    import ctypes
    def _open_folder(path):
        ctypes.windll.shell32.ShellExecuteW(None, "open", "explorer", f'"{path}"', None, 1)
    # 按路径直接打开
    if path:
        target = Path(path).resolve()
        if not target.exists():
            raise HTTPException(404, f"路径不存在: {path}")
        _open_folder(target)
        return {"ok": True, "path": str(target)}

    # 按画布 ID 打开对应的 JSON 文件
    if canvas_id:
        from ..canvas.manager import canvas_resource_dir
        try:
            target = await canvas_resource_dir(canvas_id)
        except FileNotFoundError:
            raise HTTPException(404, f"画布不存在: {canvas_id}")
        _open_folder(target)
        return {"ok": True, "path": str(target)}

    # 按 key 打开预设文件夹
    _build_folder_map()
    if not key:
        raise HTTPException(400, "需要 key、path 或 canvas_id")
    folder = FOLDER_MAP.get(key)
    if not folder:
        raise HTTPException(400, f"Unknown folder key: {key}")
    target = folder.resolve()
    if not target.is_dir():
        raise HTTPException(404, f"Folder not found: {target}")
    _open_folder(target)
    return {"ok": True, "path": str(target)}


@router.post("/shared-folders/import")
async def api_import_shared_folders(req: dict):
    from ..config import SHARED_FOLDERS_PATH
    import json
    path = req.get("path", "")
    name = req.get("name", "")
    current = {}
    if SHARED_FOLDERS_PATH.exists():
        current = json.loads(SHARED_FOLDERS_PATH.read_text(encoding="utf-8"))
    current[name] = path
    SHARED_FOLDERS_PATH.write_text(json.dumps(current, ensure_ascii=True, indent=4), encoding="utf-8")
    return {"ok": True}
