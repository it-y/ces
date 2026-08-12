"""
全局配置 — 路径、常量、默认值。

原则：没有业务逻辑，只有常量定义和纯函数。
所有路径基于 BASE_DIR 自动推断，不写死。
"""

import json
import os
from pathlib import Path

# ============================================================
# 路径 — 自动推断，适配 Windows / Mac / Linux
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent  # image/

# --- 前端 ---
STATIC_DIR = BASE_DIR / "static"
STATIC_RUNNINGHUB_DIR = STATIC_DIR / "runninghub"
STATIC_RUNNINGHUB_THUMBNAIL_DIR = STATIC_RUNNINGHUB_DIR / "thumbnails"
STATIC_RUNNINGHUB_API_PROVIDERS_FILE = STATIC_RUNNINGHUB_DIR / "api_providers.json"
STATIC_RUNNINGHUB_MODEL_REGISTRY_FILE = STATIC_RUNNINGHUB_DIR / "models_registry.json"
PROMPT_TEMPLATE_PATH = STATIC_DIR / "system-prompts" / "infinite-canvas-prompt-templates.md"

# --- 数据 ---
DATA_DIR = BASE_DIR / "data"
CANVAS_DIR = DATA_DIR / "canvases"                # 画布 JSON
CANVAS_TRASH_DIR = DATA_DIR / "canvas-trash"       # 回收站画布 JSON
CANVAS_FILES_DIR = DATA_DIR / "canvas-files"       # 画布资源
OUTPUT_DIR = DATA_DIR / "outputs"                   # 非画布生成输出
UPLOAD_DIR = DATA_DIR / "uploads"                   # 用户上传
CONVERSATION_DIR = DATA_DIR / "conversations"       # 对话
LIBRARY_DIR = DATA_DIR / "library"                  # 资产库

# --- v4 新路径（目录即结构） ---
ASSET_DIR = LIBRARY_DIR / "assets"                   # page 1: assets/{库}/{分类}/{文件}
ASSET_INDEX_PATH = ASSET_DIR / ".index.json"          # 缓存索引，可重建
ASSET_TAGS_CACHE_PATH = ASSET_DIR / ".tags.json"      # AI 分类缓存

WORKFLOW_LIBRARY_DIR = LIBRARY_DIR / "workflows"       # page 2: workflows/{库}/{分类}/{uuid}/
WORKFLOW_LIBRARY_INDEX_PATH = WORKFLOW_LIBRARY_DIR / ".index.json"

LOCAL_DIR = LIBRARY_DIR / "local"                     # page 5a: local/{文件夹}/{文件}
LOCAL_INDEX_PATH = LOCAL_DIR / ".index.json"

TRASH_DIR = LIBRARY_DIR / "trash"                     # 回收站
CLASSIFICATION_DIR = LIBRARY_DIR / "classification"
TAGS_CACHE_PATH = CLASSIFICATION_DIR / "tags_cache.json"

PROMPT_LIBRARY_DIR = LIBRARY_DIR / "prompts"          # page 3: 提示词库（JSON 格式不变）
PROMPT_LIBRARY_ITEMS_DIR = PROMPT_LIBRARY_DIR / "libraries"
PROMPT_LIBRARY_INDEX_PATH = PROMPT_LIBRARY_DIR / "index.json"
PROMPT_LIBRARY_PATH = PROMPT_LIBRARY_INDEX_PATH
LEGACY_PROMPT_LIBRARY_PATH = LIBRARY_DIR / "prompts.json"
PROJECTS_DIR = DATA_DIR / "projects"
HISTORY_DIR = DATA_DIR / "history"
CONFIG_DIR = DATA_DIR / "config"
PROJECTS_PATH = PROJECTS_DIR / "index.json"

# 配置文件
API_PROVIDERS_PATH = CONFIG_DIR / "providers.json"
SETTINGS_PATH = CONFIG_DIR / "settings.json"
SHARED_FOLDERS_PATH = CONFIG_DIR / "shared-folders.json"
RUNNINGHUB_WORKFLOWS_PATH = CONFIG_DIR / "runninghub-workflows.json"
API_ENV_FILE = CONFIG_DIR / "env"

# 历史
HISTORY_PATH = HISTORY_DIR / "history.json"

# 缓存
MEDIA_PREVIEW_DIR = DATA_DIR / "cache" / "media_previews"

# --- ComfyUI ---
WORKFLOW_DIR = BASE_DIR / "workflows"
WORKFLOW_PATH = WORKFLOW_DIR / "Z-Image.json"

# --- 版本 ---
VERSION_FILE = BASE_DIR / "VERSION"

# ============================================================
# 项目
DEFAULT_PROJECT_ID = "default"

# 超时（秒）
# ============================================================

AI_REQUEST_TIMEOUT = 1800.0
IMAGE_POLL_INTERVAL = 2.0
IMAGE_TASK_TIMEOUT = 1800.0
COMFYUI_HISTORY_TIMEOUT = 1800.0
COMFYUI_DOWNLOAD_TIMEOUT = 120.0
APIMART_IMAGE_TASK_TIMEOUT = 1800.0
APIMART_IMAGE_POLL_INTERVAL = 5.0
APIMART_IMAGE_INITIAL_POLL_DELAY = 10.0
VIDEO_POLL_TIMEOUT = 1800.0

# ============================================================
# 限制
# ============================================================

CANVAS_TRASH_RETENTION_MS = 30 * 24 * 60 * 60 * 1000  # 30 天
LOCAL_IMAGE_IMPORT_MAX_BYTES = 50 * 1024 * 1024        # 50 MB
LOCAL_IMAGE_IMPORT_EXTS = {"png", "jpg", "jpeg", "webp", "gif"}
RUNNINGHUB_THUMBNAIL_EXTS = (".jpg",)
ONLINE_IMAGE_PROMPT_MAX_LENGTH = 20000
VIDEO_PROMPT_MAX_LENGTH = 4000
LLM_MESSAGE_MAX_LENGTH = 20000
MAX_HISTORY_MESSAGES = 30
CHAT_ATTACHMENT_MAX = 20
ONLINE_IMAGE_REFERENCE_MAX = 20
HISTORY_MAX_ENTRIES = 5000
SHARED_SCAN_MAX_ENTRIES = 8000

# ============================================================
# 协议
# ============================================================

SUPPORTED_PROVIDER_PROTOCOLS = {"openai", "apimart", "gemini", "volcengine", "runninghub", "jimeng"}
SUPPORTED_IMAGE_REQUEST_MODES = {"openai", "openai-json"}

# ============================================================
# AI 供应商默认配置
# ============================================================

# --- Comfly（默认 OpenAI 兼容） ---
COMFLY_BASE_URL = "https://ai.comfly.chat"

# --- RunningHub ---
RUNNINGHUB_DEFAULT_BASE_URL = "https://www.runninghub.cn"
RUNNINGHUB_OPENAPI_BASE_URL = "https://www.runninghub.cn/openapi/v2"
RUNNINGHUB_MODEL_REGISTRY_URL = "https://raw.githubusercontent.com/HM-RunningHub/ComfyUI_RH_OpenAPI/main/models_registry.json"
RUNNINGHUB_LLM_BASE_URL = "https://llm.runninghub.cn/v1"
LINGJING_DEFAULT_BASE_URL = "https://apistudio.vip"
RUNNINGHUB_LLM_MODELS_URLS = [
    "https://llm.runninghub.cn/v1/models",
    "https://llm.runninghub.ai/v1/models",
]
RUNNINGHUB_FALLBACK_CHAT_MODELS = ["gemini-3.1-flash", "qwen3-235b-a22b", "gpt-5.1", "gpt-4o-mini"]

RUNNINGHUB_DEFAULT_IMAGE_MODELS = ["gpt-image-2", "gpt-image-2-v2", "nano-banana-pro", "gemini-3.1-flash-image-preview-2k"]
RUNNINGHUB_DEFAULT_VIDEO_MODELS = ["veo2", "veo2-fast", "veo3", "sora2", "seedance2.0_vip"]

# --- 即梦 ---
JIMENG_DEFAULT_POLL_SECONDS = 900
JIMENG_DEFAULT_IMAGE_MODELS = ["5.0", "4.6", "4.5", "4.1", "4.0", "3.1", "3.0"]
JIMENG_DEFAULT_VIDEO_MODELS = ["seedance2.0_vip", "seedance2.0fast_vip", "seedance2.0", "seedance2.0fast", "3.5pro", "3.0pro", "3.0", "3.0fast"]
AGNES_DEFAULT_VIDEO_MODELS = ["agnes-video-v2.0"]
JIMENG_LEGACY_IMAGE_MODELS = {"jimeng-image-2k", "jimeng-image-4k"}
JIMENG_LEGACY_VIDEO_MODELS = {"jimeng-video-720p", "jimeng-video-1080p"}

# --- 火山引擎 ---
VOLCENGINE_DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
VOLCENGINE_DEFAULT_PROJECT_NAME = "default"
VOLCENGINE_DEFAULT_REGION = "cn-beijing"

# --- ModelScope ---
MODELSCOPE_CHAT_BASE_URL = "https://api-inference.modelscope.cn/v1"
MODELSCOPE_DEFAULT_IMAGE_MODELS = ["Tongyi-MAI/Z-Image-Turbo", "Qwen/Qwen-Image-2512", "Qwen/Qwen-Image-Edit-2511"]
MODELSCOPE_DEFAULT_CHAT_MODELS = ["Qwen/Qwen3-235B-A22B", "Qwen/Qwen3-VL-235B-A22B-Instruct", "MiniMax/MiniMax-M2.7"]
MODELSCOPE_DEFAULT_IMAGE_MODEL = "Tongyi-MAI/Z-Image-Turbo"
MODELSCOPE_DEFAULT_CHAT_MODEL = "Qwen/Qwen3-235B-A22B"
MODELSCOPE_DEFAULTS_VERSION = 3

# --- LLM 默认值 ---
CHAT_MODEL = "gpt-4o-mini"
IMAGE_MODEL = "gpt-image-2"
SYSTEM_PROMPT = "You are a helpful assistant."

# --- ComfyUI ---
COMFYUI_INSTANCES = os.getenv("COMFYUI_INSTANCES", "127.0.0.1:8188").split(",")
COMFYUI_ADDRESS = COMFYUI_INSTANCES[0] if COMFYUI_INSTANCES else "127.0.0.1:8188"

# --- 环境变量（可选覆盖） ---
AI_BASE_URL = os.getenv("COMFLY_BASE_URL", COMFLY_BASE_URL)
AI_API_KEY = os.getenv("COMFLY_API_KEY", "")
MODELSCOPE_API_KEY = os.getenv("MODELSCOPE_API_KEY", "")

# --- 全局模型列表 ---
CHAT_MODELS = os.getenv("MODELSCOPE_CHAT_MODELS", "Qwen/Qwen3-235B-A22B,Qwen/Qwen3-VL-235B-A22B-Instruct").split(",")
IMAGE_MODELS = ["gpt-image-2", "gemini-3.1-flash-image-preview-2k", "nano-banana-pro"]
VIDEO_MODELS = ["veo2", "veo2-fast", "veo3", "veo3-fast"]
MODELSCOPE_CHAT_MODELS = MODELSCOPE_DEFAULT_CHAT_MODELS

# ============================================================
# GitHub / ModelScope 更新 URL
# ============================================================

GITHUB_REPO_URL = "https://github.com/it-y/ces"
GITHUB_RAW_ROOT = "https://raw.githubusercontent.com/it-y/ces/main"
GITHUB_VERSION_URL = f"{GITHUB_RAW_ROOT}/VERSION"
GITHUB_TREE_URL = "https://api.github.com/repos/it-y/ces/git/trees/main?recursive=1"
GITHUB_UPDATE_NOTES_URL = f"{GITHUB_RAW_ROOT}/static/update-notes.json"
GITHUB_MANIFEST_URL = f"{GITHUB_RAW_ROOT}/MANIFEST"

GITHUB_TOKEN = ""

def load_github_token() -> str:
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        try:
            sp = SETTINGS_PATH
            if sp.exists():
                data = json.loads(sp.read_text(encoding="utf-8"))
                token = data.get("github_token", "")
        except Exception:
            pass
    global GITHUB_TOKEN
    GITHUB_TOKEN = token
    return token

MODELSCOPE_DATASET_ID = "ytk001/ces"
MODELSCOPE_REPO_URL = f"https://www.modelscope.cn/datasets/{MODELSCOPE_DATASET_ID}"
MODELSCOPE_VERSION_URL = f"https://www.modelscope.cn/api/v1/datasets/{MODELSCOPE_DATASET_ID}/repo?Revision=master&FilePath=VERSION"
MODELSCOPE_TREE_URL = f"https://www.modelscope.cn/api/v1/datasets/{MODELSCOPE_DATASET_ID}/repo/tree?Revision=master&Recursive=true"
MODELSCOPE_FILE_API_ROOT = f"https://www.modelscope.cn/api/v1/datasets/{MODELSCOPE_DATASET_ID}/repo?Revision=master&FilePath="
MODELSCOPE_UPDATE_NOTES_URL = ""
MODELSCOPE_TOKEN = ""

def load_modelscope_token() -> str:
    """加载 ModelScope API token（优先环境变量，其次 settings.json）"""
    global MODELSCOPE_TOKEN
    token = os.getenv("MODELSCOPE_TOKEN", "")
    if not token:
        try:
            sp = SETTINGS_PATH
            if sp.exists():
                data = json.loads(sp.read_text(encoding="utf-8"))
                token = data.get("modelscope_token", "")
        except Exception:
            pass
    MODELSCOPE_TOKEN = token
    return token

# ============================================================
# 应用信息
# ============================================================

PROJECT_NAME = "Infinite Canvas (AI Studio)"
APP_VERSION = "2026.07.23.11"


# ============================================================
# 辅助函数
# ============================================================

def _parse_version(v: str) -> list[int]:
    import re
    return [int(x) for x in re.findall(r"\d+", v)]


def current_app_version() -> str:
    """从 VERSION 文件读取版本号，失败则返回 APP_VERSION"""
    def newer(a: str, b: str) -> bool:
        try:
            na = _parse_version(a)
            nb = _parse_version(b)
            for i in range(min(len(na), len(nb))):
                if na[i] != nb[i]:
                    return na[i] > nb[i]
            return len(na) > len(nb)
        except Exception:
            return a > b

    candidates = []
    try:
        if VERSION_FILE.exists():
            lines = VERSION_FILE.read_text(encoding="utf-8").strip().splitlines()
            if lines and lines[0].strip():
                candidates.append(lines[0].strip())
    except Exception:
        pass
    try:
        marker = DATA_DIR / ".applied_version"
        if marker.exists():
            v = marker.read_text(encoding="utf-8").strip()
            if v:
                candidates.append(v)
    except Exception:
        pass
    best = APP_VERSION
    for v in candidates:
        if newer(v, best):
            best = v
    return best


def _get_version_debug() -> dict:
    """返回版本调试信息"""
    import os
    info = {
        "returned": current_app_version(),
        "version_file": str(VERSION_FILE),
        "version_file_exists": VERSION_FILE.exists(),
        "app_version_fallback": APP_VERSION,
        "base_dir": str(BASE_DIR),
    }
    if VERSION_FILE.exists():
        try:
            info["version_file_content"] = VERSION_FILE.read_text(encoding="utf-8").strip()
        except Exception as e:
            info["version_file_error"] = str(e)
    try:
        info["cwd"] = os.getcwd()
    except Exception:
        pass
    return info


def ensure_directories() -> None:
    """启动时创建所有需要的目录"""
    dirs = [
        CANVAS_DIR, CANVAS_TRASH_DIR, CANVAS_FILES_DIR, OUTPUT_DIR, UPLOAD_DIR,
        CONVERSATION_DIR, LIBRARY_DIR,
        PROMPT_LIBRARY_DIR, PROMPT_LIBRARY_ITEMS_DIR,
        PROJECTS_DIR, HISTORY_DIR, CONFIG_DIR, MEDIA_PREVIEW_DIR,
        WORKFLOW_DIR, STATIC_DIR, STATIC_RUNNINGHUB_THUMBNAIL_DIR,
        # v4 目录
        ASSET_DIR, WORKFLOW_LIBRARY_DIR, LOCAL_DIR, CLASSIFICATION_DIR,
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    if not PROMPT_LIBRARY_INDEX_PATH.exists():
        PROMPT_LIBRARY_INDEX_PATH.write_text(
            json.dumps({"version": 1, "libraries": []}, ensure_ascii=True),
            encoding="utf-8",
        )
    if not LOCAL_INDEX_PATH.exists():
        LOCAL_INDEX_PATH.write_text(
            json.dumps({"version": 1, "items": []}, ensure_ascii=True),
            encoding="utf-8",
        )
    projects_path = PROJECTS_DIR / "index.json"
    legacy_projects = LIBRARY_DIR / "projects.json"
    if not projects_path.exists() and legacy_projects.exists():
        os.replace(legacy_projects, projects_path)
