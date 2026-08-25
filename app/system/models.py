"""系统 Pydantic 模型"""

from pydantic import BaseModel, Field
from typing import Optional


class UpdateRequest(BaseModel):
    auto_restart: bool = True
    restart_delay: int = 3
    source: str = "github"
    fallback: bool = True
    version: str | None = None


class RollbackRequest(BaseModel):
    name: str           # 备份名称
    auto_restart: bool = True
    restart_delay: int = 3


class TokenRequest(BaseModel):
    token: str


class ApiProviderPayload(BaseModel):
    """供应商配置的完整 schema

    clear_* 是请求级控制标志（要求服务端删除已存密钥），不属于持久化数据，
    路由层落盘前会用 CLEAR_FLAG_FIELDS 排除它们。
    """
    id: str = Field(max_length=40, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(max_length=60)
    base_url: str
    protocol: str = "openai"
    api_key: str = ""
    enabled: bool = True
    primary: bool = False
    image_models: list = Field(default_factory=list)
    chat_models: list = Field(default_factory=list)
    video_models: list = Field(default_factory=list)
    image_request_mode: str = "openai"
    model_protocols: dict = Field(default_factory=dict)
    # ---- 清除密钥控制标志 ----
    clear_key: bool = False
    clear_wallet_key: bool = False
    # 火山素材库 AK/SK：兼容前后端两套历史命名
    clear_volcengine_access_key: bool = False
    clear_volcengine_access_key_id: bool = False
    clear_volcengine_secret_key: bool = False
    clear_volcengine_secret_access_key: bool = False


# 控制标志集合：这些字段只用于单次请求，绝不写入 providers.json
CLEAR_FLAG_FIELDS = {
    "clear_key", "clear_wallet_key",
    "clear_volcengine_access_key", "clear_volcengine_access_key_id",
    "clear_volcengine_secret_key", "clear_volcengine_secret_access_key",
}
