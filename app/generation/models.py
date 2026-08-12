"""
AI 生成 Pydantic 模型。
"""

from pydantic import BaseModel, Field
from typing import Optional, List


# ============================================================
# 参考素材
# ============================================================

class AIReference(BaseModel):
    url: str
    name: str = ""
    role: str = ""          # 图片的角色：first_frame, last_frame, reference 等
    kind: str = "image"     # image / video / audio
    mime: str = ""


# ============================================================
# 在线图片生成
# ============================================================

class OnlineImageRequest(BaseModel):
    prompt: str = Field(max_length=20000)
    provider_id: str = ""
    model: str = ""
    size: str = "1024x1024"
    quality: str = "auto"
    n: int = Field(default=1, ge=1, le=10)
    reference_images: List[AIReference] = Field(default_factory=list)
    client_id: Optional[str] = None
    canvas_id: Optional[str] = None


class ImageTaskQueryRequest(BaseModel):
    provider_id: str
    task_id: str = Field(max_length=240)


# ============================================================
# Canvas 视频 / LLM
# ============================================================

class CanvasVideoRequest(BaseModel):
    prompt: str = Field(max_length=4000)
    provider_id: str = ""
    model: str = ""
    duration: int = Field(default=5, ge=1, le=60)
    aspect_ratio: str = "16:9"
    resolution: str = "720p"
    size: str = ""
    images: List[AIReference] = Field(default_factory=list)
    videos: List[AIReference] = Field(default_factory=list)
    audios: List[AIReference] = Field(default_factory=list)
    enhance_prompt: bool = False
    enable_upsample: bool = False
    watermark: bool = False
    seed: Optional[int] = None
    camerafixed: bool = False
    return_last_frame: bool = False
    generate_audio: bool = True
    multimodal: bool = False
    trusted_asset: bool = False
    client_id: Optional[str] = None
    canvas_id: Optional[str] = None


class CanvasLLMRequest(BaseModel):
    message: str = Field(max_length=20000)
    system_prompt: str = ""
    model: str = ""
    messages: list = Field(default_factory=list)
    provider: str = ""
    ms_model: str = ""
    images: list = Field(default_factory=list)
    videos: list = Field(default_factory=list)
    canvas_id: Optional[str] = None


# ============================================================
# ComfyUI 本地生成
# ============================================================

class ComfyGenerateRequest(BaseModel):
    prompt: str
    width: int = 1024
    height: int = 1024
    workflow_json: str = "Z-Image.json"
    params: dict = Field(default_factory=dict)
    type: str = "image"
    client_id: Optional[str] = None
    convert_to_jpg: bool = True


# ============================================================
# Cloud (ModelScope) 生成
# ============================================================

class CloudGenRequest(BaseModel):
    prompt: str
    api_key: str = ""
    model: str = ""
    resolution: str = "1024x1024"
    type: str = "image"
    image_urls: list = Field(default_factory=list)
    loras: list = Field(default_factory=list)
    client_id: Optional[str] = None


class CloudPollRequest(BaseModel):
    task_id: str
    api_key: str = ""
    client_id: Optional[str] = None
