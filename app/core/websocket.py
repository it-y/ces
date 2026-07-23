"""
WebSocket 连接管理器（全局单例）。

消息协议（必须保持不变，前端依赖）：
  {"type": "stats", "online_count": N}
  {"type": "new_image", "data": {...}}
  {"type": "canvas_updated", "canvas_id": "...", "updated_at": ..., "client_id": "..."}
  {"type": "asset_library_updated", "updated_at": ...}
  {"type": "pong"}
"""

import json
from fastapi import WebSocket


class ConnectionManager:
    """管理所有 WebSocket 连接，支持广播和点对点消息"""

    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.user_connections: dict[str, WebSocket] = {}       # client_id → ws
        self.connection_clients: dict[WebSocket, str] = {}     # ws → client_id

    # ---- 连接管理 ----

    async def connect(self, websocket: WebSocket, client_id: str | None = None):
        await websocket.accept()
        self.active_connections.append(websocket)
        if client_id:
            self.user_connections[client_id] = websocket
            self.connection_clients[websocket] = client_id
        await self.broadcast_count()

    async def disconnect(self, websocket: WebSocket, client_id: str | None = None):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        cid = client_id or self.connection_clients.pop(websocket, None)
        if cid and cid in self.user_connections:
            del self.user_connections[cid]
        await self.broadcast_count()

    # ---- 在线人数 ----

    def online_count(self) -> int:
        """可见用户数（过滤 canvas_ 开头的内部 client_id）"""
        return sum(
            1 for cid in self.connection_clients.values()
            if not cid.startswith("canvas_")
        )

    async def broadcast_count(self):
        await self._broadcast({"type": "stats", "online_count": self.online_count()})

    # ---- 业务广播 ----

    async def broadcast_new_image(self, data: dict):
        """新图片生成通知"""
        await self._broadcast({"type": "new_image", "data": data})

    async def broadcast_canvas_updated(self, canvas_id: str, updated_at: int, client_id: str | None = None):
        """画布变更通知（排除发起变更的客户端）"""
        msg = {
            "type": "canvas_updated",
            "canvas_id": canvas_id,
            "updated_at": updated_at,
            "client_id": client_id or "",
        }
        await self._broadcast(msg, exclude_client_id=client_id)

    async def broadcast_asset_library_updated(self, updated_at: int):
        """资产库变更通知"""
        await self._broadcast({"type": "asset_library_updated", "updated_at": updated_at})

    # ---- 内部方法 ----

    async def _broadcast(self, message: dict, exclude_client_id: str | None = None):
        payload = json.dumps(message, ensure_ascii=False)
        dead = []
        for ws in self.active_connections:
            try:
                cid = self.connection_clients.get(ws, "")
                if exclude_client_id and cid == exclude_client_id:
                    continue
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)

    async def send_personal_message(self, client_id: str, message: dict):
        """点对点消息"""
        ws = self.user_connections.get(client_id)
        if ws:
            try:
                await ws.send_text(json.dumps(message, ensure_ascii=False))
            except Exception:
                await self.disconnect(ws, client_id)


# 全局单例
manager = ConnectionManager()
