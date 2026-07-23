"""
安全工具 — CSRF 校验、路径穿越防护、文件名清洗。
"""

import os
import re
import ipaddress
import socket
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import Request, HTTPException


def ensure_same_origin_request(request: Request) -> None:
    """
    CSRF 防护：校验请求的 Origin / Referer 与 Host 一致。

    只有浏览器发起的跨域请求才会有 Origin header，
    直接 HTTP 请求（curl、后端调用）不会带 Origin，放行。
    """
    origin = request.headers.get("origin", "")
    referer = request.headers.get("referer", "")
    host = request.headers.get("host", "")

    check = origin or referer
    if not check:
        return  # 非浏览器请求，放行

    # 从 Origin/Referer 提取 host:port
    origin_host = _extract_host(check)
    if origin_host and origin_host != host:
        raise HTTPException(status_code=403, detail="跨域请求被拒绝")


def _extract_host(url: str) -> str:
    """从 URL 提取 host:port"""
    m = re.search(r"://([^/]+)", url)
    return m.group(1) if m else ""


def safe_path_join(base: Path, relative: str) -> Path:
    """??????????? core.paths?"""
    from .paths import safe_path_join as _safe_path_join
    return _safe_path_join(base, relative)


def sanitize_filename(name: str) -> str:
    """清洗文件名，移除不安全字符"""
    name = name.strip().replace("\\", "/").split("/")[-1]  # 去掉路径
    name = re.sub(r'[<>:"|?*]', "_", name)                 # Windows 非法字符
    name = re.sub(r"[\x00-\x1f]", "", name)                 # 控制字符
    return name or "untitled"


def validate_remote_url(url: str) -> str:
    """只允许指向公网地址的 HTTP(S) URL。"""
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError
        if parsed.username or parsed.password:
            raise ValueError
    except ValueError:
        raise HTTPException(status_code=400, detail="远程 URL 不合法")

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise HTTPException(status_code=400, detail="不允许访问本机或内网地址")

    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, parsed.port)}
    except socket.gaierror:
        raise HTTPException(status_code=400, detail="远程地址无法解析")

    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise HTTPException(status_code=400, detail="不允许访问本机或内网地址")
    return url
