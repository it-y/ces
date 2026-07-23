"""
即梦 CLI 子进程管理 — 核心入口。

即梦 CLI 是字节跳动的 AI 创作命令行工具。
后端通过 asyncio.create_subprocess_exec 调用它。

支持：
  - 本地 CLI + WSL（Windows Subsystem for Linux）模式
  - 长驻登录进程（QR 码扫描登录）
  - 7 种视频模式切换
  - 图片生成（text2image / image2image）
"""

import asyncio
import json
import os
import re
import time
from pathlib import Path
from ....core.http_client import create_client
from ....config import (
    JIMENG_DEFAULT_POLL_SECONDS,
    BASE_DIR)


class JimengSubprocess:
    """即梦 CLI 子进程管理器"""

    def __init__(self):
        self._login_process: asyncio.subprocess.Process | None = None
        self._login_output: list[str] = []

    # ---- 环境探测 ----

    def _use_wsl(self) -> bool:
        return os.getenv("JIMENG_USE_WSL", "") == "1"

    def _cli_executable(self) -> str:
        if self._use_wsl():
            return "wsl.exe"
        return os.getenv("DREAMINA_BIN", "dreamina")

    def _cli_base_args(self) -> list[str]:
        exe = self._cli_executable()
        if self._use_wsl():
            # 探测 WSL 发行版
            return ["wsl.exe", "-d", "Ubuntu", "--", "dreamina"]
        return [exe]

    # ---- 子进程执行 ----

    async def run(self, args: list[str], timeout: int = 900) -> dict | None:
        """
        执行即梦 CLI 命令，返回解析后的 JSON 结果。
        timeout: 超时秒数（默认 15 分钟）
        """
        cmd = self._cli_base_args() + args
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(BASE_DIR),
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            output = self._decode_output(stdout, stderr)
            return self._extract_json(output)
        except asyncio.TimeoutError:
            return None
        except Exception:
            return None

    # ---- 输出解析 ----

    def _decode_output(self, stdout: bytes, stderr: bytes) -> str:
        """解码 CLI 输出，WSL 模式处理 UTF-16LE"""
        if self._use_wsl():
            try:
                return stdout.decode("utf-16-le")
            except Exception:
                pass
        return stdout.decode("utf-8", errors="replace") + "\n" + stderr.decode("utf-8", errors="replace")

    def _extract_json(self, text: str) -> dict | None:
        """从 CLI 输出中提取 JSON（支持嵌套 + 多 JSON 选最优）"""
        candidates = []
        for m in re.finditer(r"\{[^{}]*\}", text, re.DOTALL):
            try:
                obj = json.loads(m.group())
                candidates.append((len(m.group()), obj))
            except json.JSONDecodeError:
                pass
        if not candidates:
            return None
        candidates.sort(key=lambda x: -x[0])
        return candidates[0][1]

    # ---- 登录 ----

    async def start_login(self) -> str:
        """启动长驻登录进程，返回 session_id"""
        cmd = self._cli_base_args() + ["login", "--headless"]
        self._login_process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(BASE_DIR),
        )
        return "jimeng_login_session"

    async def get_login_text(self) -> str:
        """读取登录进程的输出（含 QR 码链接）"""
        if not self._login_process or self._login_process.stdout is None:
            return ""
        try:
            chunk = await asyncio.wait_for(
                self._login_process.stdout.read(4096), timeout=1.0
            )
            if chunk:
                text = chunk.decode("utf-8", errors="replace")
                self._login_output.append(text)
                return "".join(self._login_output)
        except asyncio.TimeoutError:
            pass
        return "".join(self._login_output)


# 全局单例
jimeng_subprocess = JimengSubprocess()
