"""
供应商配置管理 — 加载、保存、协议判断、热重载。

供应商配置存储在 data/config/providers.json。
每个供应商包含：id, name, protocol, base_url, api_key, image_models, chat_models, video_models 等。
"""

import asyncio
import os
from pathlib import Path
from typing import Optional

from ..config import (
    API_PROVIDERS_PATH, CONFIG_DIR, API_ENV_FILE,
    SUPPORTED_PROVIDER_PROTOCOLS,
    COMFLY_BASE_URL, MODELSCOPE_CHAT_BASE_URL,
    RUNNINGHUB_DEFAULT_BASE_URL, RUNNINGHUB_OPENAPI_BASE_URL,
    VOLCENGINE_DEFAULT_BASE_URL, LINGJING_DEFAULT_BASE_URL,
    DEFAULT_PROJECT_ID,
)
from ..core.errors import read_json, write_atomic

_providers_lock = asyncio.Lock()


# ============================================================
# 默认供应商
# ============================================================

def _default_providers() -> list[dict]:
    """内置的 4 个默认供应商"""
    return [
        {
            "id": "comfly",
            "name": "Comfly Chat",
            "protocol": "openai",
            "base_url": COMFLY_BASE_URL,
            "image_request_mode": "openai",
            "enabled": True,
            "primary": True,
            "image_models": ["gpt-image-2", "gemini-3.1-flash-image-preview-2k", "nano-banana-pro"],
            "chat_models": ["gpt-4o-mini", "gemini-3.1-flash-image-preview-2k", "gpt-4.1"],
            "video_models": ["veo2", "veo2-fast", "veo3", "veo3-fast"],
            "model_protocols": {},
            "ms_loras": [],
            "ms_defaults_version": 3,
        },
        {
            "id": "modelscope",
            "name": "ModelScope",
            "protocol": "openai",
            "base_url": MODELSCOPE_CHAT_BASE_URL,
            "image_request_mode": "openai",
            "enabled": True,
            "primary": False,
            "image_models": ["Tongyi-MAI/Z-Image-Turbo", "Qwen/Qwen-Image-2512"],
            "chat_models": ["Qwen/Qwen3-235B-A22B", "Qwen/Qwen3-VL-235B-A22B-Instruct"],
            "video_models": [],
            "model_protocols": {},
            "ms_loras": [],
            "ms_defaults_version": 3,
        },
        {
            "id": "runninghub",
            "name": "RunningHub",
            "protocol": "runninghub",
            "base_url": RUNNINGHUB_DEFAULT_BASE_URL,
            "image_request_mode": "openai",
            "enabled": True,
            "primary": False,
            "image_models": [],
            "chat_models": [],
            "video_models": [],
            "model_protocols": {},
            "rh_apps": [],
            "rh_workflows": [],
        },
        {
            "id": "volcengine",
            "name": "火山引擎",
            "protocol": "volcengine",
            "base_url": VOLCENGINE_DEFAULT_BASE_URL,
            "image_request_mode": "openai",
            "enabled": True,
            "primary": False,
            "image_models": [],
            "chat_models": [],
            "video_models": [],
            "model_protocols": {},
            "volcengine_project_name": "default",
            "volcengine_region": "cn-beijing",
        },
    ]


# ============================================================
# 加载 / 保存
# ============================================================

async def load_providers() -> list[dict]:
    """加载供应商列表，合并默认值"""
    async with _providers_lock:
        providers = await read_json(API_PROVIDERS_PATH)
        if providers is None:
            providers = _default_providers()
            await write_atomic(API_PROVIDERS_PATH, providers)
            return providers

    # 合并默认供应商（确保 4 个内置供应商始终存在）
    defaults = {p["id"]: p for p in _default_providers()}
    for p in providers:
        pid = p.get("id", "")
        if pid in defaults:
            # 保留用户配置，补充默认字段
            for k, v in defaults[pid].items():
                if k not in p:
                    p[k] = v
            del defaults[pid]
    # 添加用户删掉的默认供应商
    for p in defaults.values():
        providers.append(p)

    return providers


async def save_providers(providers: list[dict]) -> None:
    async with _providers_lock:
        await write_atomic(API_PROVIDERS_PATH, providers)


async def get_provider(provider_id: str) -> Optional[dict]:
    """获取单个供应商配置"""
    providers = await load_providers()
    # 兼容 comfly 别名
    if provider_id == "comfly":
        provider_id = _resolve_comfly(providers)
    for p in providers:
        if p.get("id") == provider_id:
            return p
    return None


def _resolve_comfly(providers: list[dict]) -> str:
    """找到 primary=True 的供应商作为默认"""
    for p in providers:
        if p.get("primary") and p.get("enabled"):
            return p["id"]
    return "comfly"


# ============================================================
# 协议判断
# ============================================================

def provider_protocol(provider: dict) -> str:
    return provider.get("protocol", "openai")


def effective_protocol(provider: dict, model: str = "") -> str:
    """
    确定实际使用的协议。
    优先看 model_protocols 覆盖，再看 provider 的 protocol 字段。
    """
    if model:
        overrides = provider.get("model_protocols", {})
        if model in overrides:
            proto = overrides[model]
            if proto in SUPPORTED_PROVIDER_PROTOCOLS:
                return proto
    proto = provider_protocol(provider)
    if proto == "openai" and provider.get("id") == "modelscope":
        return "modelscope"
    return proto


def is_apimart_provider(provider: dict) -> bool:
    return provider_protocol(provider) == "apimart"


def is_gemini_provider(provider: dict) -> bool:
    return provider_protocol(provider) == "gemini"


def is_volcengine_provider(provider: dict) -> bool:
    return provider_protocol(provider) == "volcengine"


def is_runninghub_provider(provider: dict) -> bool:
    return provider_protocol(provider) == "runninghub"


def is_jimeng_provider(provider: dict) -> bool:
    return provider_protocol(provider) == "jimeng"


def is_modelscope_provider(provider: dict) -> bool:
    return provider.get("id") == "modelscope" or provider_protocol(provider) == "modelscope"


# ============================================================
# API Key 辅助
# ============================================================

def mask_secret(value: str) -> str:
    """脱敏：只显示前 4 后 4 位"""
    if not value or len(value) <= 8:
        return "****" if value else ""
    return value[:4] + "****" + value[-4:]


def provider_api_key(provider: dict, explicit: str = "") -> str:
    """获取供应商的 API Key（优先级：显式传入 > 环境变量 > 配置）"""
    if explicit:
        return explicit
    # 从环境变量读取
    env_key = f"{provider['id'].upper()}_API_KEY"
    from_env = os.getenv(env_key, "")
    if from_env:
        return from_env
    # 从 .env 文件读取
    return _read_env_value(provider["id"])


def _read_env_value(provider_id: str) -> str:
    """从 API/.env 文件读取"""
    env_file = CONFIG_DIR / "env"
    if not env_file.exists():
        return ""
    try:
        text = env_file.read_text(encoding="utf-8-sig")
        key_upper = f"{provider_id.upper()}_API_KEY="
        for line in text.splitlines():
            if line.startswith(key_upper):
                return line[len(key_upper):].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def provider_key_env(provider_id: str) -> str:
    """provider_id → 环境变量名"""
    mapping = {
        "comfly": "COMFLY_API_KEY",
        "modelscope": "MODELSCOPE_API_KEY",
        "runninghub": "RUNNINGHUB_API_KEY",
        "volcengine": "VOLCENGINE_API_KEY",
        "jimeng": "JIMENG_API_KEY",
        "lingjing": "LINGJING_API_KEY",
    }
    return mapping.get(provider_id, f"{provider_id.upper()}_API_KEY")


def runninghub_wallet_key_env() -> str:
    return "RUNNINGHUB_WALLET_API_KEY"


def volcengine_access_key_env() -> str:
    return "VOLCENGINE_ACCESS_KEY_ID"


def volcengine_secret_key_env() -> str:
    return "VOLCENGINE_SECRET_ACCESS_KEY"


def public_provider(provider: dict) -> dict:
    """返回脱敏后的供应商信息（隐藏 API Key）+ 密钥状态元数据"""
    pub = dict(provider)
    api_key = pub.pop("api_key", None) or os.getenv(provider_key_env(provider.get("id", "")), "")
    wallet_key = pub.pop("wallet_api_key", None) or os.getenv(runninghub_wallet_key_env(), "")
    # 火山引擎 AK/SK
    ak = pub.pop("volcengine_access_key_id", None) or ""
    sk = pub.pop("volcengine_secret_access_key", None) or ""
    if ak:
        pub["has_volcengine_access_key"] = True
        pub["volcengine_access_key_preview"] = mask_secret(ak)
        pub["volcengine_access_key_env"] = volcengine_access_key_env()
    else:
        pub["has_volcengine_access_key"] = False
        pub["volcengine_access_key_preview"] = ""
        pub["volcengine_access_key_env"] = volcengine_access_key_env()
    if sk:
        pub["has_volcengine_secret_key"] = True
        pub["volcengine_secret_key_preview"] = mask_secret(sk)
        pub["volcengine_secret_key_env"] = volcengine_secret_key_env()
    else:
        pub["has_volcengine_secret_key"] = False
        pub["volcengine_secret_key_preview"] = ""
        pub["volcengine_secret_key_env"] = volcengine_secret_key_env()
    pub["has_key"] = bool(api_key)
    pub["key_preview"] = mask_secret(api_key) if api_key else ""
    pub["key_env"] = provider_key_env(provider.get("id", ""))
    pub["has_wallet_key"] = bool(wallet_key)
    pub["wallet_key_preview"] = mask_secret(wallet_key) if wallet_key else ""
    pub["wallet_key_env"] = runninghub_wallet_key_env()
    return pub
