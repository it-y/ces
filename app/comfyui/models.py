"""ComfyUI Pydantic 模型"""

from pydantic import BaseModel, Field
from typing import Optional


class ComfyInstancesPayload(BaseModel):
    instances: list[str] = Field(default_factory=list)


class WorkflowConfig(BaseModel):
    title: str = Field(max_length=200)
    description: str = ""
    workflow_json: str = ""
    fields: list = Field(default_factory=list)


class WorkflowRunRequest(BaseModel):
    workflow_id: str
    params: dict = Field(default_factory=dict)
    client_id: Optional[str] = None
