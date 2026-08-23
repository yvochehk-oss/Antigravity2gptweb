"""V0.2: 结构化日志（structlog）。失败时降级为标准 logging，避免硬依赖。"""
from __future__ import annotations

import logging
import os
import sys

try:
    import structlog

    _HAS_STRUCTLOG = True
except Exception:  # pragma: no cover - structlog 缺装时降级
    _HAS_STRUCTLOG = False


def configure_logging() -> None:
    """在 app 启动时调用一次。"""
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    if _HAS_STRUCTLOG:
        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso", utc=True),
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(ensure_ascii=False),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level)),
            cache_logger_on_first_use=True,
        )
    root = logging.getLogger()
    if not root.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root.addHandler(h)
    root.setLevel(level)


def get_logger(name: str = "app"):
    if _HAS_STRUCTLOG:
        return structlog.get_logger(name)
    return logging.getLogger(name)