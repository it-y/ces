"""
Infinite Canvas — 后端入口。

启动：uvicorn app.main:app --host 0.0.0.0 --port 3000
"""

import json
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from .config import (
    APP_VERSION, PROJECT_NAME,
    STATIC_DIR, OUTPUT_DIR, UPLOAD_DIR, LIBRARY_DIR,
    CANVAS_DIR, CANVAS_FILES_DIR, CONVERSATION_DIR,
    HISTORY_DIR, CONFIG_DIR, MEDIA_PREVIEW_DIR,
    current_app_version,
)
from .config import ensure_directories
from .core.websocket import manager
from .core.logging import setup_logging
from .core.errors import register_error_handlers


# ============================================================
# 生命周期
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    print(f"[OK] {PROJECT_NAME} started")
    print(f"Version: {current_app_version()}")
    print(f"Data dir: {CANVAS_DIR.parent}")
    yield
    print("[STOP] Infinite Canvas shutting down...")
    from .core.http_client import close_clients
    await close_clients()
    for ws in manager.active_connections[:]:
        try:
            await ws.close()
        except Exception:
            pass
    manager.active_connections.clear()


# ============================================================
# FastAPI 实例
# ============================================================

app = FastAPI(
    title=PROJECT_NAME,
    description="AI-powered infinite canvas",
    version=current_app_version(),
    lifespan=lifespan,
)


# ============================================================
# 纯 ASGI 中间件（零 overhead）
# ============================================================

class StaticCacheMiddleware:
    """为带 ?v= 的静态资源设置 1 年不可变缓存。
    纯 ASGI 实现，避免 BaseHTTPMiddleware 的协程开销。"""

    STATIC_PREFIXES = frozenset({"/static/", "/output/", "/assets/", "/cfiles/"})
    HTML_CACHE = (b"cache-control", b"no-cache")
    ASSET_CACHE = (b"cache-control", b"public, max-age=31536000, immutable")

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not any(path.startswith(p) for p in self.STATIC_PREFIXES):
            await self.app(scope, receive, send)
            return

        qs = scope.get("query_string", b"").decode()
        has_v = "v=" in qs
        is_html = path.endswith(".html")
        header = self.ASSET_CACHE if has_v and not is_html else self.HTML_CACHE

        original_send = send

        async def send_with_cache(message):
            if message["type"] == "http.response.start":
                headers = message.get("headers", [])
                headers = [h for h in headers if h[0].lower() != b"cache-control"]
                headers.append(header)
                message["headers"] = headers
            await original_send(message)

        await self.app(scope, receive, send_with_cache)


class SameOriginMiddleware:
    """纯 ASGI 同源校验中间件 — 比 BaseHTTPMiddleware 快 3-5x。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET")
        if path.startswith("/api/") and method not in {"GET", "HEAD", "OPTIONS"}:
            headers = dict(scope.get("headers", []))
            origin = headers.get(b"origin", b"").decode()
            referer = headers.get(b"referer", b"").decode()
            host = headers.get(b"host", b"").decode()

            check = origin or referer
            if check:
                m = re.search(r"://([^/]+)", check)
                origin_host = m.group(1) if m else ""
                if origin_host and origin_host != host:
                    body = json.dumps({"detail": "跨域请求被拒绝"}).encode()
                    await send({
                        "type": "http.response.start",
                        "status": 403,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                        ],
                    })
                    await send({"type": "http.response.body", "body": body})
                    return

        await self.app(scope, receive, send)


# ============================================================
# 注册中间件（顺序：后加的先执行）
# ============================================================

# 确保数据目录存在（必须在 StaticFiles mount 之前执行）
ensure_directories()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3000", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)
# 静态资源已带 ?v= 缓存破坏参数，允许浏览器永久缓存
app.add_middleware(StaticCacheMiddleware)
# 同源校验替换为纯 ASGI 实现
app.add_middleware(SameOriginMiddleware)

# 异常处理
register_error_handlers(app)

# 静态文件挂载
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/output", StaticFiles(directory=str(OUTPUT_DIR)), name="output")
app.mount("/assets", StaticFiles(directory=str(UPLOAD_DIR)), name="assets")
app.mount("/cfiles", StaticFiles(directory=str(CANVAS_FILES_DIR)), name="canvas-files")


# ============================================================
# WebSocket
# ============================================================

@app.websocket("/ws/stats")
async def websocket_endpoint(websocket: WebSocket, client_id: str = None):
    await manager.connect(websocket, client_id)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except Exception:
        pass
    finally:
        await manager.disconnect(websocket, client_id)


from fastapi.responses import FileResponse

# ============================================================
# 基础路由
# ============================================================

@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))

@app.get("/api/app-info")
async def app_info():
    from .config import (
        GITHUB_VERSION_URL, GITHUB_TREE_URL, GITHUB_RAW_ROOT,
        MODELSCOPE_VERSION_URL, MODELSCOPE_REPO_URL,
    )
    return {
        "app": PROJECT_NAME,
        "version": current_app_version(),
        "status": "running",
        "features": ["canvas", "generation", "assets", "comfyui"],
        "data_dir": str(CANVAS_DIR.parent),
        "sources": {
            "github": {
                "version_url": GITHUB_VERSION_URL,
                "tree_url": GITHUB_TREE_URL,
            },
            "modelscope": {
                "version_url": MODELSCOPE_VERSION_URL,
                "repo_url": MODELSCOPE_REPO_URL,
            },
        },
    }


@app.get("/health")
async def health_check():
    return {"status": "healthy", "version": current_app_version()}


@app.get("/api/version-debug")
async def version_debug():
    from .config import _get_version_debug
    return _get_version_debug()


# 注册功能模块路由
from .canvas.routes import router as canvas_router
app.include_router(canvas_router)

from .generation.routes import router as gen_router
from .generation.routes_runninghub import router as rh_router
from .generation.routes_jimeng import router as jimeng_router
app.include_router(gen_router)
app.include_router(rh_router)
app.include_router(jimeng_router)


from .assets.routes import router as assets_router
app.include_router(assets_router)

from .upload.routes import router as upload_router
app.include_router(upload_router)

from .comfyui.routes import router as comfyui_router
app.include_router(comfyui_router)

from .media.routes import router as media_router
app.include_router(media_router)

from .system.routes import router as system_router
app.include_router(system_router)

from .modelscope.routes import router as ms_router
app.include_router(ms_router)
