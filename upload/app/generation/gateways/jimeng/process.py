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
from json import JSONDecodeError
from pathlib import Path
from ....core.http_client import create_client
from ....config import (
    JIMENG_DEFAULT_POLL_SECONDS,
    BASE_DIR)

# 最低 CLI 版本要求（低于此版本 submit_id 格式不兼容）
JIMENG_MIN_CLI_VERSION = (1, 4, 2)


class JimengPendingError(Exception):
    """
    即梦任务在云端排队中，CLI 超时但任务已提交。
    前端应展示"排队中"卡片并轮询。
    """
    def __init__(self, message: str = "任务已提交，在云端排队中",
                 submit_id: str = "", queue_info: dict | None = None):
        super().__init__(message)
        self.submit_id = submit_id
        self.queue_info = queue_info or {}


class JimengSubprocess:
    """即梦 CLI 子进程管理器"""

    def __init__(self):
        self._login_process: asyncio.subprocess.Process | None = None
        self._login_output: list[str] = []
        self._cli_version_cache: tuple | None = None

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
            result = self._extract_json(output)
            if result is None:
                # 尝试查找 submit_id（CLI 可能在输出中包含了提交 ID）
                submit_id = self._find_submit_id(output)
                if submit_id:
                    raise JimengPendingError(
                        f"任务已提交 (id={submit_id[:16]}...)，在云端排队中",
                        submit_id=submit_id,
                    )
                raise RuntimeError(f"即梦 CLI 无有效输出: {output[:200]}")
            return result
        except asyncio.TimeoutError:
            # CLI 超时，检查是否有部分输出含 submit_id
            raise JimengPendingError(
                "即梦任务在云端排队中，请稍后查询",
                queue_info={"note": "CLI 超时，任务可能仍在云端执行"},
            )
        except JimengPendingError:
            raise
        except RuntimeError:
            raise
        except Exception as e:
            # 网络连接失败等 → 不静默吞掉
            raise RuntimeError(f"即梦 CLI 执行异常: {e}") from e

    # ---- submit_id 提取 ----

    def _find_submit_id(self, text: str) -> str | None:
        """从 CLI 输出中提取 submit_id"""
        # 匹配 "submit_id": "xxx" 或 submit_id: xxx
        for pattern in (r'"submit_id"\s*:\s*"([^"]+)"', r'submit_id[\s:=]+([a-zA-Z0-9_-]+)'):
            m = re.search(pattern, text)
            if m:
                return m.group(1)
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
        """
        从 CLI 输出中提取最相关的 JSON 对象。
        优先用 json.JSONDecoder.raw_decode（支持嵌套），
        回退 regex（不支持嵌套但覆盖简单场景）。
        """
        candidates = []

        # 方法 1: raw_decode（支持嵌套 JSON）
        idx = 0
        while idx < len(text):
            brace = text.find("{", idx)
            if brace == -1:
                break
            try:
                decoder = json.JSONDecoder()
                obj, end = decoder.raw_decode(text, brace)
                candidates.append((end - brace, obj))
                idx = brace + max(end - brace, 1)
            except JSONDecodeError:
                idx = brace + 1

        # 方法 2: regex fallback（简单场景更快）
        if not candidates:
            for m in re.finditer(r"\{[^{}]*\}", text):
                try:
                    obj = json.loads(m.group())
                    candidates.append((len(m.group()), obj))
                except JSONDecodeError:
                    pass

        if not candidates:
            return None

        # 评分：优先选择含关键字段的对象
        priority_keys = ("submit_id", "gen_status", "result_json", "images", "videos",
                         "outputs", "status", "url", "remote_url", "image_url")
        def _score(candidate: tuple) -> int:
            size, obj = candidate
            if not isinstance(obj, dict):
                return size
            bonus = 0
            for key in priority_keys:
                if key in obj:
                    bonus += 100
            return size + bonus

        candidates.sort(key=_score, reverse=True)
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
