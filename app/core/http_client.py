"""
httpx 异步 HTTP 客户端工厂 — 直连优先 + 代理兜底 + 策略缓存。

设计原则：
  - 所有 API 请求默认直连（trust_env=False），国内中转站无需代理
  - 直连失败（连接错误/DNS 失败/超时）后自动尝试代理（trust_env=True）
  - 每个 host 缓存成功的连接策略（直连/代理），5 分钟后过期
  - 4xx 业务错误不触发代理 fallback（不是网络问题）

客户端按 preset 缓存复用，避免每次调用新建连接。

⚠ 注意：create_client / create_upload_client 是 @asynccontextmanager，
   退出时不会关闭共享客户端。关闭只发生在 close_clients()（应用退出时）。
"""

import asyncio
import time
from contextlib import asynccontextmanager
from urllib.parse import urlparse
from httpx import AsyncClient, Timeout, Limits

TIMEOUT_PRESETS = {
    "quick":   Timeout(connect=10, read=15,  write=10, pool=10),
    "fast":    Timeout(connect=10, read=30,  write=20, pool=10),
    "normal":  Timeout(connect=20, read=120, write=30, pool=20),
    "long":    Timeout(connect=20, read=1800, write=120, pool=20),
    "xlong":   Timeout(connect=20, read=600, write=600, pool=20),
}

LIMITS = Limits(max_connections=50, max_keepalive_connections=20)

# 直连客户端池：trust_env=False，忽略环境变量中的代理
_direct_clients: dict[str, AsyncClient] = {}
# 代理客户端池：trust_env=True，使用 HTTP_PROXY/HTTPS_PROXY（如果设置了的话）
_proxy_clients: dict[str, AsyncClient] = {}
_upload_client: AsyncClient | None = None

# 策略缓存：host → ("direct"|"proxy", timestamp)
_strategy_cache: dict[str, tuple[str, float]] = {}
STRATEGY_TTL = 300  # 5 分钟


def _host_from_url(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""


def _get_direct_client(preset: str) -> AsyncClient:
    if preset not in _direct_clients:
        timeout = TIMEOUT_PRESETS.get(preset, TIMEOUT_PRESETS["normal"])
        _direct_clients[preset] = AsyncClient(timeout=timeout, limits=LIMITS, trust_env=False)
    return _direct_clients[preset]


def _get_proxy_client(preset: str) -> AsyncClient:
    if preset not in _proxy_clients:
        timeout = TIMEOUT_PRESETS.get(preset, TIMEOUT_PRESETS["normal"])
        _proxy_clients[preset] = AsyncClient(timeout=timeout, limits=LIMITS, trust_env=True)
    return _proxy_clients[preset]


def _from_cache(host: str) -> str | None:
    """返回缓存的策略（'direct' 或 'proxy'），过期返回 None"""
    entry = _strategy_cache.get(host)
    if entry and (time.time() - entry[1]) < STRATEGY_TTL:
        return entry[0]
    return None


def _cache_strategy(host: str, strategy: str) -> None:
    _strategy_cache[host] = (strategy, time.time())


@asynccontextmanager
async def create_client(preset: str = "normal", trust_env: bool | None = None, follow_redirects: bool = False):
    """
    返回 HTTP 客户端。
    - 默认：共享直连客户端（trust_env=False，国内中转站无需代理）
    - 传入 trust_env/follow_redirects 时：创建一次性专用客户端（用完即关）
      （用于更新下载等需要走系统代理/跟随重定向的场景）
    """
    if trust_env is None and not follow_redirects:
        yield _get_direct_client(preset)
        return
    timeout = TIMEOUT_PRESETS.get(preset, TIMEOUT_PRESETS["normal"])
    client = AsyncClient(
        timeout=timeout, limits=LIMITS,
        trust_env=trust_env if trust_env is not None else False,
        follow_redirects=follow_redirects,
    )
    yield client
    await client.aclose()


@asynccontextmanager
async def create_upload_client():
    """上传专用客户端（上下文管理器，退出时不关闭）"""
    global _upload_client
    if _upload_client is None:
        _upload_client = AsyncClient(
            timeout=Timeout(connect=20, read=120, write=300, pool=20),
            limits=LIMITS,
            trust_env=False,
        )
    yield _upload_client


async def request_with_fallback(
    method: str,
    url: str,
    timeout_preset: str = "normal",
    max_retries: int = 3,
    **kwargs,
):
    """
    直连优先 + 代理兜底。

    流程：
      1. 查策略缓存，命中则直接用缓存策略
      2. 未命中 → 直连尝试（最多 2 次）
      3. 直连全失败（连接错误/超时/5xx）→ 切换代理尝试（最多 2 次）
      4. 全失败 → 抛出最后一个异常
      5. 成功 → 缓存策略，5 分钟后过期

    4xx 响应不重试（业务错误，重试没用）。
    """
    host = _host_from_url(url)

    # —— 命中缓存：直接用上次成功的策略 ——
    cached = _from_cache(host)
    if cached:
        client = _get_proxy_client(timeout_preset) if cached == "proxy" else _get_direct_client(timeout_preset)
        try:
            resp = await client.request(method, url, **kwargs)
            if 400 <= resp.status_code < 500:
                return resp
            if resp.status_code < 600:
                _cache_strategy(host, cached)
                return resp
        except Exception:
            _strategy_cache.pop(host, None)  # 缓存失效，重新探测
            # fall through 到完整探测

    last_error = None

    # —— 阶段 1：直连 ——
    direct = _get_direct_client(timeout_preset)
    for attempt in range(min(max_retries, 2)):
        try:
            resp = await direct.request(method, url, **kwargs)
            if 400 <= resp.status_code < 500:
                _cache_strategy(host, "direct")
                return resp
            if resp.status_code < 600:
                _cache_strategy(host, "direct")
                return resp
            # 5xx：可能是临时故障，重试
            if attempt < 1:
                await asyncio.sleep(2 ** attempt)
                continue
            last_error = Exception(f"HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            last_error = e
            if _is_transient_error(e) and attempt < 1:
                await asyncio.sleep(2 ** attempt)
                continue
            # 连接错误 → 不再重试直连，直接跳到代理
            break

    # —— 阶段 2：代理兜底 ——
    proxy = _get_proxy_client(timeout_preset)
    for attempt in range(min(max_retries, 2)):
        try:
            resp = await proxy.request(method, url, **kwargs)
            if 400 <= resp.status_code < 500:
                _cache_strategy(host, "proxy")
                return resp
            if resp.status_code < 600:
                _cache_strategy(host, "proxy")
                return resp
            if attempt < 1:
                await asyncio.sleep(2 ** attempt)
                continue
            last_error = Exception(f"HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            last_error = e
            if _is_transient_error(e) and attempt < 1:
                await asyncio.sleep(2 ** attempt)
                continue
            break

    raise last_error or Exception(f"请求失败（已尝试直连+代理）: {url}")


def _is_transient_error(e: Exception) -> bool:
    """判断是否为瞬时错误（值得重试）"""
    msg = str(e).lower()
    if any(kw in msg for kw in ("timeout", "connection", "reset", "refused", "dns", "eof", "broken pipe")):
        return True
    # httpx 的连接相关异常
    cls_name = type(e).__name__.lower()
    if any(kw in cls_name for kw in ("timeout", "connect", "read", "write", "network", "remote", "proxy", "pool")):
        return True
    return False


async def retry_request(method: str, url: str, **kwargs):
    """
    直连 HTTP 请求 + 指数退避重试（不经过代理，与 LLM 路由行为一致）。

    5xx 和连接异常自动重试（最多 3 次），4xx 立即返回。
    """
    async with create_client("long") as client:
        for attempt in range(3):
            try:
                resp = await client.request(method, url, **kwargs)
                if resp.status_code >= 500 and attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return resp
            except Exception:
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue
                raise


async def close_clients():
    """关闭所有缓存客户端（应用关闭时调用）"""
    for preset, client in _direct_clients.items():
        await client.aclose()
    _direct_clients.clear()
    for preset, client in _proxy_clients.items():
        await client.aclose()
    _proxy_clients.clear()
    global _upload_client
    if _upload_client:
        await _upload_client.aclose()
        _upload_client = None
