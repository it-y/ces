"""
画布上下文 — last_opened、client binding。

消除其他模块对 upload.routes 的反向导入依赖。
"""

_last_opened_canvas: str | None = None
_client_canvases: dict[str, str] = {}


def set_last_opened_canvas(canvas_id: str | None):
    global _last_opened_canvas
    _last_opened_canvas = canvas_id


def get_last_opened_canvas() -> str | None:
    return _last_opened_canvas


def bind_canvas_client(client_id: str | None, canvas_id: str | None) -> None:
    if client_id and canvas_id:
        _client_canvases[client_id] = canvas_id


def resolve_canvas_id(canvas_id: str | None = None, client_id: str | None = None) -> str | None:
    if canvas_id:
        return canvas_id
    if client_id and client_id in _client_canvases:
        return _client_canvases[client_id]
    return get_last_opened_canvas()
