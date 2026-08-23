"""V0.2: 结构化日志（structlog）。失败时降级为标准 logging，避免硬依赖。"""
from __future__ import annotations

import logging
import os
import re
import sys
from collections.abc import Mapping
from typing import Any

from .observability import get_request_id, install_logging_filter, new_request_id

try:
    import structlog

    _HAS_STRUCTLOG = True
except Exception:  # pragma: no cover - structlog 缺装时降级
    _HAS_STRUCTLOG = False


# Audit/request-log columns are VARCHAR(64); keep the transport value within
# that bound as well as excluding control characters.
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")


def resolve_request_id(value: str | None) -> str:
    """Return a safe request id suitable for headers, logs and audit rows.

    Request IDs are correlation metadata, not caller-controlled log text.  A
    bounded allow-list prevents CR/LF injection and unbounded values from
    reaching response headers, while preserving valid IDs supplied by trusted
    reverse proxies.  Invalid or missing input receives a fresh UUID4 hex id.
    """
    candidate = str(value or "").strip()
    if _REQUEST_ID_RE.fullmatch(candidate):
        return candidate
    return new_request_id()


def _install_record_factory() -> None:
    """Inject request_id into every stdlib LogRecord, including child handlers.

    A filter on the root logger does not run for a record handled directly by
    a child logger.  The LogRecord factory is process-wide and therefore keeps
    the request identifier available to both application and third-party
    handlers without changing their logging calls.
    """
    previous = logging.getLogRecordFactory()
    if getattr(previous, "_codex_request_id_factory", False):
        return

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        if not getattr(record, "request_id", ""):
            record.request_id = get_request_id() or ""
        return record

    factory._codex_request_id_factory = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(factory)


# Install during import so records emitted before FastAPI startup are also
# traceable.  ``configure_logging`` remains idempotent for application setup.
_install_record_factory()


def bind_request_context(request_id: str) -> Mapping[str, Any] | None:
    """Bind the request id to structlog when that optional backend is present."""
    if not _HAS_STRUCTLOG:
        return None
    return structlog.contextvars.bind_contextvars(request_id=request_id)


def reset_request_context(tokens: Mapping[str, Any] | None) -> None:
    """Restore the previous structlog context after a request finishes."""
    if not _HAS_STRUCTLOG or not tokens:
        return
    structlog.contextvars.reset_contextvars(**tokens)


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
        h.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s [request_id=%(request_id)s]",
            ),
        )
        root.addHandler(h)
    root.setLevel(level)
    install_logging_filter()
    _install_record_factory()


def get_logger(name: str = "app"):
    if _HAS_STRUCTLOG:
        return structlog.get_logger(name)
    return logging.getLogger(name)
