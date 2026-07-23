"""
统一异常处理和友好中文错误消息。
"""

from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError


# ============================================================
# 工具 — 原子写 JSON
# ============================================================

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Optional


async def read_json(path: Path) -> Optional[dict | list]:
    """读 JSON，不存在或损坏返回 None"""
    if not path.exists():
        return None
    try:
        text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        return json.loads(text)
    except (json.JSONDecodeError, IOError):
        return None


async def write_atomic(path: Path, data) -> None:
    """原子写 JSON：临时文件 + os.replace + fsync"""
    path.parent.mkdir(parents=True, exist_ok=True)

    def _write():
        fd, tmp = tempfile.mkstemp(suffix=".json", prefix=".tmp_", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=True, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, str(path))
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    await asyncio.to_thread(_write)


def now_ms() -> int:
    import time
    return int(time.time() * 1000)


# ============================================================
# 验证错误 → 中文
# ============================================================

def friendly_validation_error(errors: list) -> str:
    """Pydantic 验证错误转中文消息"""
    if not errors:
        return "请求参数不正确。"
    parts = []
    for err in errors:
        loc = [str(item) for item in err.get("loc", []) if item != "body"]
        field = loc[-1] if loc else ""

        err_type = str(err.get("type", ""))
        msg = str(err.get("msg", ""))

        if "max_length" in err_type or "at most" in msg:
            parts.append(f"{field}过长，请拆分为多个提示词节点，或先用 LLM 节点压缩后再生成。")
        elif "min_length" in err_type:
            parts.append(f"{field}不能为空。")
        elif "required" in err_type:
            parts.append(f"缺少必填字段 {field}。")
        else:
            parts.append(f"{field}格式不正确：{msg}")
    return "\n".join(parts) or "请求参数不正确。"


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "detail": friendly_validation_error(exc.errors()),
            "errors": exc.errors(),
        },
    )


# ============================================================
# AI 错误 → 中文（保留原项目 30+ 种规则）
# ============================================================

def friendly_image_error_detail(text: str, size: str | None = None, model: str | None = None) -> str:
    """将上游 AI 服务的英文/技术错误转为中文"""
    text_lower = (text or "").lower()

    # 像素/尺寸
    if "pixel" in text_lower or "resolution" in text_lower or "size" in text_lower:
        return f"图片尺寸不符合模型要求（{size or '未知'}），请尝试调整尺寸。"
    if "content" in text_lower and ("safety" in text_lower or "policy" in text_lower or "filter" in text_lower):
        return "内容被安全策略拦截，请修改提示词后重试。"

    # 鉴权
    if "401" in text_lower or "unauthorized" in text_lower or "invalid api key" in text_lower:
        return "API Key 无效或已过期，请在设置中更新。"
    if "402" in text_lower or "429" in text_lower or "rate" in text_lower:
        return "API 额度不足或请求过于频繁，请稍后重试。"
    if "403" in text_lower or "forbidden" in text_lower:
        return "API 访问被拒绝，请检查账户权限。"

    # 模型
    if "model_not_found" in text_lower or "model not found" in text_lower:
        return f"模型 {model or ''} 不存在或已下线。"
    if "channel" in text_lower and ("not found" in text_lower or "missing" in text_lower):
        return "上游渠道不可用，请联系 API 供应商。"

    # 网络/超时
    if "timeout" in text_lower or "timed out" in text_lower:
        return "请求超时，请检查网络后重试。"
    if "connection" in text_lower or "network" in text_lower or "dns" in text_lower:
        return "网络连接失败，请检查网络后重试。"

    # 即梦 CLI
    if "jimeng" in text_lower or "dreamina" in text_lower:
        if "login" in text_lower:
            return "即梦 CLI 未登录，请在设置中执行登录。"
        if "credit" in text_lower or "quota" in text_lower:
            return "即梦积分不足，请充值或切换模型。"

    return f"生成失败：{text[:200]}"


def friendly_chat_error_detail(text: str, model: str | None = None, provider: str | None = None) -> str:
    """聊天错误中文化"""
    text_lower = (text or "").lower()
    provider_name = provider or "AI"

    if "401" in text_lower or "unauthorized" in text_lower:
        return f"{provider_name} API Key 无效，请在设置中更新。"
    if "429" in text_lower or "rate" in text_lower:
        return f"{provider_name} 请求过于频繁，请稍后重试。"
    if "timeout" in text_lower:
        return "请求超时，请检查网络后重试。"

    return f"对话失败：{text[:200]}"


# ============================================================
# 注册
# ============================================================

def register_error_handlers(app):
    """注册全局异常处理器"""
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
