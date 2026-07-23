"""
httpx 异步 HTTP 客户端工厂 — 带连接池复用。

提供 4 个 timeout 预设：
  quick   — 健康检查、队列查询（短超时）
  normal  — 常规 API 调用
  long    — 图片/视频生成（长超时）
  xlong   — 超长任务（30 分钟）

客户端按 preset 缓存复用，避免每次调用新建连接。

⚠ 注意：create_client / create_upload_client 是 @asynccontextmanager，
   退出时不会关闭共享客户端。关闭只发生在 close_clients()（应用退出时）。
"""

from contextlib import asynccontextmanager
from httpx import AsyncClient, Timeout, Limits

TIMEOUT_PRESETS = {
    "quick":   Timeout(connect=10, read=15,  write=10, pool=10),
    "normal":  Timeout(connect=20, read=120, write=30, pool=20),
    "long":    Timeout(connect=20, read=1800, write=120, pool=20),
    "xlong":   Timeout(connect=20, read=600, write=600, pool=20),
}

LIMITS = Limits(max_connections=50, max_keepalive_connections=20)

_clients: dict[str, AsyncClient] = {}
_upload_client: AsyncClient | None = None


@asynccontextmanager
async def create_client(preset: str = "normal"):
    """获取带连接池复用的 httpx AsyncClient（上下文管理器，退出时不关闭）"""
    if preset not in _clients:
        timeout = TIMEOUT_PRESETS.get(preset, TIMEOUT_PRESETS["normal"])
        _clients[preset] = AsyncClient(timeout=timeout, limits=LIMITS)
    yield _clients[preset]


@asynccontextmanager
async def create_upload_client():
    """上传专用客户端：更长的写入超时（上下文管理器，退出时不关闭）"""
    global _upload_client
    if _upload_client is None:
        _upload_client = AsyncClient(
            timeout=Timeout(connect=20, read=120, write=300, pool=20),
            limits=LIMITS,
        )
    yield _upload_client


async def close_clients():
    """关闭所有缓存客户端（应用关闭时调用）"""
    for preset, client in _clients.items():
        await client.aclose()
    _clients.clear()
    global _upload_client
    if _upload_client:
        await _upload_client.aclose()
        _upload_client = None
