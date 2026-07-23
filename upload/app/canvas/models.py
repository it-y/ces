"""
画布 Pydantic 模型 — 请求/响应结构定义。
"""

from pydantic import BaseModel, Field
from typing import Optional, List


# ============================================================
# 画布
# ============================================================

class CanvasCreateRequest(BaseModel):
    title: str = Field(default="未命名画布", max_length=80)
    icon: str = Field(default="")
    kind: str = Field(default="classic", pattern=r"^(classic|smart)$")
    project: str = Field(default="default")
    board_x: float = Field(default=0)
    board_y: float = Field(default=0)


class CanvasMetaUpdate(BaseModel):
    """更新元数据 — 不会修改 updated_at"""
    title: Optional[str] = Field(default=None, max_length=80)
    icon: Optional[str] = None
    owner: Optional[str] = None
    color: Optional[str] = None
    pinned: Optional[bool] = None
    project: Optional[str] = None
    board_x: Optional[float] = None
    board_y: Optional[float] = None


class CanvasSaveRequest(BaseModel):
    """保存画布内容 — 会修改 updated_at + 触发乐观锁"""
    title: Optional[str] = Field(default=None, max_length=80)
    icon: Optional[str] = None
    nodes: Optional[list] = None
    connections: Optional[list] = None
    viewport: Optional[dict] = None
    logs: Optional[list] = None
    settings: Optional[dict] = None
    client_id: Optional[str] = None
    base_updated_at: Optional[int] = None   # 乐观锁：必须匹配当前 updated_at


# ============================================================
# 项目
# ============================================================

class ProjectCreateRequest(BaseModel):
    name: str = Field(max_length=80)


class ProjectUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=80)
    order: Optional[int] = None


# ============================================================
# 画布资产
# ============================================================

class CanvasAssetCheckRequest(BaseModel):
    urls: List[str] = Field(default_factory=list)


class CanvasAssetCheckItem(BaseModel):
    url: str = ""
    name: Optional[str] = None


class CanvasAssetDownloadRequest(BaseModel):
    urls: List[str] = Field(default_factory=list)
    items: List[CanvasAssetCheckItem] = Field(default_factory=list)
    filename: Optional[str] = "canvas-assets.zip"
