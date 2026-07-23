"""
即梦 WSL 兼容层 — Windows 路径转换、输出解码。

WSL 模式下：
  - 路径需要从 Windows 格式转 WSL 格式（C:/Users/... → /mnt/c/Users/...）
  - CLI 输出可能是 UTF-16LE 编码
  - stderr 需要过滤 WSL 代理警告
"""

import os
import re


def windows_to_wsl(path: str) -> str:
    """Windows 路径 → WSL 路径"""
    if not path or not re.match(r"^[A-Za-z]:", path):
        return path
    drive = path[0].lower()
    rest = path[2:].replace("\\", "/")
    return f"/mnt/{drive}/{rest}"


def wsl_to_windows(path: str) -> str:
    """WSL 路径 → Windows 路径"""
    m = re.match(r"^/mnt/([a-z])/(.*)", path)
    if m:
        return f"{m.group(1).upper()}:\\{m.group(2).replace('/', os.sep)}"
    return path


def clean_wsl_stderr(text: str) -> str:
    """过滤 WSL 代理警告"""
    lines = text.splitlines()
    cleaned = []
    skip_keywords = [
        "wslproxy", "WSL", "[WSL]", "WSLPROXY",
        "Windows Subsystem", "pulseaudio",
    ]
    for line in lines:
        if not any(kw in line for kw in skip_keywords):
            cleaned.append(line)
    return "\n".join(cleaned)
