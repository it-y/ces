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

    注意：PUT /api/providers 只把本模型里声明过的字段写进 providers.json，
    前端 saveProviders() 发送的每个持久化字段都必须在这里声明，
    否则会被静默丢弃（例如 RH 余额 Key、LoRA、工作流卡片、火山 AK/SK）。
    """
    id: str = Field(max_length=40, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(max_length=120)
    base_url: str = ""
    protocol: str = "openai"
    api_key: str = ""
    enabled: bool = True
    primary: bool = False
    image_models: list = Field(default_factory=list)
    chat_models: list = Field(default_factory=list)
    video_models: list = Field(default_factory=list)
    image_request_mode: str = "openai"
    model_protocols: dict = Field(default_factory=dict)
    # ---- 端点覆盖（gemini 等网关读 image_generation_endpoint） ----
    image_edit_route: str = "general"
    image_generation_endpoint: str = ""
    image_edit_endpoint: str = ""
    # ---- RunningHub：RH币 Key / 余额 Key / 应用与工作流卡片 ----
    wallet_api_key: str = ""
    rh_apps: list = Field(default_factory=list)
    rh_workflows: list = Field(default_factory=list)
    # ---- ModelScope：LoRA 列表与默认值版本 ----
    ms_loras: list = Field(default_factory=list)
    ms_defaults_version: int = 0
    # ---- 火山素材库 AK/SK 与项目/地域（volcengine 网关读 region） ----
    volcengine_access_key_id: str = ""
    volcengine_secret_access_key: str = ""
    volcengine_project_name: str = ""
    volcengine_region: str = ""
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
