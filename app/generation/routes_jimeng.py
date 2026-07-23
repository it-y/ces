"""
即梦路由 — /api/jimeng/*
"""

import asyncio
from fastapi import APIRouter, HTTPException
from ..core.http_client import create_client
from .gateways.jimeng.process import jimeng_subprocess

router = APIRouter(prefix="/api/jimeng", tags=["jimeng"])


# ---- 状态 ----

@router.get("/status")
async def jimeng_status():
    """检查即梦 CLI 是否可用"""
    result = await jimeng_subprocess.run(["--version" if jimeng_subprocess._use_wsl() else "version"], timeout=10)
    if result:
        return {"status": "ok", "version": result}
    return {"status": "unavailable", "hint": "请安装即梦 CLI 并登录"}


@router.get("/credit")
async def jimeng_credit():
    """查询即梦积分余额"""
    result = await jimeng_subprocess.run(["credit"], timeout=30)
    if result:
        return result
    raise HTTPException(502, "即梦积分查询失败，请检查 CLI 登录状态")


# ---- 登录 ----

@router.post("/login")
async def jimeng_login():
    """启动即梦登录（返回 QR 码链接）"""
    session_id = await jimeng_subprocess.start_login()
    return {"session_id": session_id, "message": "登录进程已启动，请通过 QR 码完成认证"}


@router.get("/login/text")
async def jimeng_login_text():
    """获取登录输出（含 QR 码/登录链接）"""
    text = await jimeng_subprocess.get_login_text()
    return {"text": text, "has_qr": "qrcode" in text.lower() or "qr" in text.lower()}


# ---- 版本 ----

@router.get("/version")
async def jimeng_version():
    """获取即梦 CLI 版本"""
    result = await jimeng_subprocess.run(["version"], timeout=10)
    if result:
        return {"version": result}
    raise HTTPException(502, "无法获取即梦版本")


# ---- 查询媒体 ----

@router.post("/query-media")
async def jimeng_query_media(req: dict):
    """查询即梦生成任务的状态和结果"""
    submit_id = req.get("submit_id", "")
    kind = req.get("kind", "image")
    if not submit_id:
        raise HTTPException(400, "缺少 submit_id")

    args = ["query", "--id", submit_id]
    if kind == "video":
        args = ["video", "query", "--id", submit_id]

    result = await jimeng_subprocess.run(args, timeout=30)
    if result:
        return result
    raise HTTPException(502, "查询失败，请检查 CLI 状态")


# ---- 帮助 ----

@router.post("/help")
async def jimeng_help(req: dict):
    """执行即梦 CLI 命令（调试用）"""
    command = req.get("command", "help")
    args = command.split()
    result = await jimeng_subprocess.run(args, timeout=60)
    if result:
        return result
    return {"output": "命令执行完成（无 JSON 输出）"}


# ---- 登录别名 ----

@router.post("/login/start")
async def jimeng_login_start():
    """启动即梦登录（POST /login 的别名）"""
    try:
        session_id = await jimeng_subprocess.start_login()
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="即梦 CLI 未安装或命令不可用")
    return {"session_id": session_id, "message": "登录进程已启动，请通过 QR 码完成认证"}


@router.get("/login/status")
async def jimeng_login_status():
    """查询即梦登录状态"""
    text = await jimeng_subprocess.get_login_text()
    text_lower = text.lower()
    if "logged in" in text_lower or "登录成功" in text or "login success" in text_lower:
        return {"logged_in": True, "running": False}
    if "qrcode" in text_lower or "qr" in text_lower or "login" in text_lower:
        return {"logged_in": False, "running": True, "text": text}
    return {"logged_in": False, "running": False, "text": text}


@router.post("/logout")
async def jimeng_logout():
    """退出即梦登录"""
    result = await jimeng_subprocess.run(["logout"], timeout=10)
    if result:
        return result
    return {"message": "注销成功"}
