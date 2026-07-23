"""资产库 Pydantic 模型"""

from pydantic import BaseModel, Field
from typing import Optional


class AssetLibraryItem(BaseModel):
    name: str = Field(max_length=200)
    kind: str = "image"          # image / video / audio / file
    tags: list = Field(default_factory=list)
    category_id: str = ""


class PromptLibraryItem(BaseModel):
    title: str = Field(max_length=200)
    content: str = ""
    category: str = ""
    tags: list = Field(default_factory=list)
