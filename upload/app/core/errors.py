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
import re
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
    """将上游 AI 服务的英文/技术错误转为中文。覆盖 20+ 种错误模式。"""
    raw_text = str(text or "")
    text_lower = raw_text.lower()

    # ---- GPT-Image-2 尺寸专属 ----
    if _is_gpt_image_2_model_name(model):
        if _gpt_size_exceeds(size):
            return (
                f"GPT-Image-2 不支持当前尺寸 {size or '未指定'}：它有最大像素限制"
                "（长边最大 3840、总像素约 829 万）。请改用更小的尺寸，"
                "或切换到 nano-banana 生成更高分辨率。"
            )
        m = re.search(r"longest edge must be less than or equal to (\d+)", raw_text)
        if m:
            return (
                f"GPT-Image-2 不支持当前尺寸 {size or '未指定'}：最长边超过 {m.group(1)}px。"
                "如果需要更高分辨率，请切换到 nano-banana；继续使用 GPT 时请调低分辨率。"
            )

    # ---- 尺寸/像素 ----
    m = re.search(r"longest edge must be less than or equal to (\d+)", raw_text)
    if m:
        return f"该模型不支持当前分辨率：最长边超过 {m.group(1)}px。请把图片分辨率调低（例如换到 2K 或更小），或更换支持高分辨率的模型。"
    if "image size must be at least" in text_lower:
        pixel_match = re.search(r"at least (\d+) pixels", text_lower)
        pixels = pixel_match.group(1) if pixel_match else "3686400"
        return f"该模型要求更高分辨率，当前尺寸 {size or '过小'} 不满足最低像素要求（至少 {pixels} 像素）。建议从 2K 起步。"
    if "invalid size" in text_lower or "invalid_value" in text_lower:
        return f"该模型不支持当前尺寸：{size or '未指定'}。请尝试更换分辨率或模型。"

    # ---- 内容安全 ----
    if any(kw in text_lower for kw in (
        "inputtextsensitivecontentdetected", "policyviolation", "copyright restrictions",
    )):
        return (
            "上游内容安全拦截了这段提示词，原因偏向版权/敏感内容限制。"
            "请改写提示词，避免直接出现具体 IP、角色名、品牌名、影视/动漫作品名，改成风格特征描述再试。"
        )
    if any(kw in text_lower for kw in (
        "rejected by the safety system", "image_generation_user_error",
        "safety system", "content_policy_violation", "content policy",
    )):
        return (
            "上游（Azure/OpenAI 系）内容安全系统拒绝了本次生图请求。"
            "可能是提示词或参考图触发了内容审核。请改写提示词、"
            "避免敏感/暴力/成人/名人/版权角色等描述；若使用了人物参考图，可换一张图再试。"
        )
    if "content" in text_lower and ("safety" in text_lower or "policy" in text_lower or "filter" in text_lower):
        return "内容被安全策略拦截，请修改提示词后重试。"

    # ---- 鉴权 ----
    if "401" in text_lower or "unauthorized" in text_lower or "invalid api key" in text_lower:
        return "API Key 无效或已过期，请在设置中更新。"
    if "402" in text_lower or "429" in text_lower or "rate limit" in text_lower:
        return "API 额度不足或请求过于频繁，请稍后重试。"
    if "403" in text_lower or "forbidden" in text_lower:
        return "API 访问被拒绝，请检查账户权限。"

    # ---- 模型/渠道 ----
    if "model_not_found" in text_lower or "model not found" in text_lower:
        return f"模型 {model or ''} 不存在或已下线。"
    if "channel not found" in text_lower or ("channel" in text_lower and ("not found" in text_lower or "missing" in text_lower)):
        return f"上游平台找不到模型「{model or ''}」可用通道。可能该模型未在此账号开通，请换一个已开通的模型。"

    # ---- 网络/超时 ----
    if "timeout" in text_lower or "timed out" in text_lower:
        return "请求超时，请检查网络后重试。"
    if "connection" in text_lower or "network" in text_lower or "dns" in text_lower:
        return "网络连接失败，请检查网络后重试。"

    # ---- 即梦 CLI ----
    if "jimeng" in text_lower or "dreamina" in text_lower:
        if "login" in text_lower:
            return "即梦 CLI 未登录，请在设置中执行登录。"
        if "credit" in text_lower or "quota" in text_lower:
            return "即梦积分不足，请充值或切换模型。"

    return f"生成失败：{raw_text[:200]}"


def _is_gpt_image_2_model_name(model: str | None) -> bool:
    """检测模型名是否为 GPT-Image-2 系列。"""
    if not model:
        return False
    raw = str(model).strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    compact = re.sub(r"[^a-z0-9]+", "", raw)
    return (
        normalized == "gpt-image-2"
        or normalized.startswith("gpt-image-2-")
        or normalized.endswith("-gpt-image-2")
        or "-gpt-image-2-" in normalized
        or compact == "gptimage2"
        or compact.startswith("gptimage2")
        or compact.endswith("gptimage2")
    )


def _gpt_size_exceeds(size: str | None) -> bool:
    """检查尺寸是否超过 GPT-Image-2 上限（长边 3840 / 总像素 829 万）。"""
    if not size:
        return False
    import re as _re
    m = _re.fullmatch(r"\s*(\d+)\s*[xX*×]\s*(\d+)\s*", str(size).strip())
    if not m:
        return False
    w, h = int(m.group(1)), int(m.group(2))
    return max(w, h) > 3840 or w * h > 8_294_400


def friendly_chat_error_detail(text: str, model: str | None = None, provider: str | None = None) -> str:
    """聊天错误中文化，覆盖 15+ 种错误模式。"""
    raw_text = str(text or "")
    text_lower = raw_text.lower()
    provider_name = str(provider or "AI")

    # 先尝试解析 JSON error body
    try:
        payload = json.loads(raw_text)
    except Exception:
        payload = {}
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    code = str(error.get("code") or payload.get("code") or "").lower()
    message = str(error.get("message") or payload.get("message") or "").lower()

    # 上下文超长
    if any(kw in text_lower or kw in message for kw in (
        "context_length_exceeded", "maximum context length", "max_tokens",
        "reduce the length", "too long", "token limit",
    )):
        return (
            f"{provider_name} 输入内容过长，超过了模型上下文限制。"
            "请删减对话历史或缩短提示词后重试。如果正在使用长文档，建议先让 LLM 节点提取摘要。"
        )

    # 内容过滤
    if any(kw in text_lower or kw in message for kw in (
        "content_filter", "content filter", "content_policy_violation",
        "safety", "inappropriate", "harmful",
    )):
        return f"{provider_name} 内容安全策略拦截了本次请求，请修改对话内容后重试。"

    # 鉴权
    if "401" in text_lower or "unauthorized" in text_lower or "invalid_api_key" in text_lower.replace(" ", "_"):
        return f"{provider_name} API Key 无效或已过期，请在设置中更新。"
    if "429" in text_lower or "rate" in text_lower:
        return f"{provider_name} 请求过于频繁，请稍后重试。"
    if "402" in text_lower or "quota" in text_lower or "billing" in text_lower:
        return f"{provider_name} 额度不足，请充值或检查账户余额。"

    # 模型
    if "model_not_found" in text_lower or "model not found" in text_lower:
        return f"{provider_name} 找不到模型「{model or ''}」，可能已下线或未开通。"

    # 网络/超时
    if "timeout" in text_lower:
        return "请求超时，请检查网络后重试。"
    if "connection" in text_lower or "network" in text_lower or "dns" in text_lower:
        return "网络连接失败，请检查网络后重试。"

    # 通用兜底
    return f"对话失败：{raw_text[:200]}"


# ============================================================
# 注册
# ============================================================

def register_error_handlers(app):
    """注册全局异常处理器"""
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
