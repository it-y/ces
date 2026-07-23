"""上传 Pydantic 模型"""

from pydantic import BaseModel, Field
from fastapi import UploadFile


class Base64UploadRequest(BaseModel):
    data: str           # data:image/png;base64,xxx 或纯 base64
    filename: str = "upload.png"
