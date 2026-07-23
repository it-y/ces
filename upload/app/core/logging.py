"""
日志配置 — 过滤高频心跳接口的访问日志，避免刷屏。
"""

import logging


class QuietAccessLogFilter(logging.Filter):
    """过滤掉高频轮询接口的访问日志"""

    QUIET_PATHS = {
        "/api/queue_status",
        "/api/canvases",
        "/api/canvases/trash",
    }

    QUIET_PREFIXES = (
        "/api/canvases/",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        # 格式: "GET /api/canvases HTTP/1.1 200"
        for path in self.QUIET_PATHS:
            if f"GET {path} " in message and " 200" in message:
                return False
        for prefix in self.QUIET_PREFIXES:
            if f"GET {prefix}" in message and "/meta" in message and " 200" in message:
                return False
        return True


def setup_logging() -> None:
    """配置 uvicorn 访问日志过滤器"""
    uvicorn_logger = logging.getLogger("uvicorn.access")
    if uvicorn_logger:
        uvicorn_logger.addFilter(QuietAccessLogFilter())
