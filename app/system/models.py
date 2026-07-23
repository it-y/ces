"""系统 Pydantic 模型"""

from pydantic import BaseModel, Field
from typing import Optional


class UpdateRequest(BaseModel):
    auto_restart: bool = True
    restart_delay: int = 3
    source: str = "github"
    fallback: bool = True


class RollbackRequest(BaseModel):
    name: str           # 备份名称
    auto_restart: bool = True
    restart_delay: int = 3


class TokenRequest(BaseModel):
    token: str


class ApiProviderPayload(BaseModel):
    """供应商配置的完整 schema"""
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
