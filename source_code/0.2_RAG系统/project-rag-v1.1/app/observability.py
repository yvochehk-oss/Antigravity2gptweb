"""Observability primitives for ProjectRAG V1.1.

This is the RAG-side analogue of the Tax V1.0 ``observability`` module: it
attaches a stable ``request_id`` to every incoming HTTP request, makes
the value available through :func:`get_request_id`, and writes it back
into the response via :class:`RequestIdMiddleware`.  The value also
flows into :func:`logging.Logger` records so structured logs share the
same identifier from a single client request to the response.
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import threading
import uuid
from typing import Any, Callable, Iterator

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_MDC_KEY = "request_id"

_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "projectrag_request_id", default="",
)

_LOCK = threading.Lock()
_INSTALLED = False
_RECORD_FACTORY_MARKER = "_projectrag_request_id_factory"


def new_request_id() -> str:
    """Generate a fresh UUID4 hex string for one request."""
    return uuid.uuid4().hex


def get_request_id() -> str:
    """Return the active request id for the current context."""
    return _request_id_var.get() or ""


def set_request_id(value: str) -> contextvars.Token:
    """Bind ``value`` as the active request id for the current context."""
    cleaned = (value or "").strip()
    if not cleaned:
        cleaned = new_request_id()
    return _request_id_var.set(cleaned)


def reset_request_id(token: contextvars.Token | None) -> None:
    if token is not None:
        try:
            _request_id_var.reset(token)
        except (ValueError, LookupError):
            pass


@contextlib.contextmanager
def request_scope(request_id: str | None) -> Iterator[str]:
    """Bind a request id for the lifetime of a block."""
    final_id = (request_id or "").strip() or new_request_id()
    token = set_request_id(final_id)
    try:
        yield final_id
    finally:
        reset_request_id(token)


class RequestIdFilter(logging.Filter):
    """Inject the active request id into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:  # pragma: no cover - trivial
        # Normal records are enriched by the factory at creation time.  Keep
        # that value when a queue or deferred handler processes the record in
        # a different context; replacing it here could cross-contaminate
        # request ids between worker/request contexts.  The fallback is for
        # manually-created LogRecords that did not pass through the factory.
        if not hasattr(record, REQUEST_ID_MDC_KEY):
            record.request_id = _request_id_var.get() or ""
        return True


def _make_request_id_record_factory(
    previous_factory: Callable[..., logging.LogRecord],
) -> Callable[..., logging.LogRecord]:
    """Wrap a stdlib record factory with the active request id.

    Logger filters are only evaluated by the logger that *creates* a record;
    filters on the root logger do not run before a record is delivered to a
    handler attached to a child logger.  A record factory runs at creation
    time, so it also covers handlers and child loggers that are registered
    after :func:`install_logging_filter` has run.

    The wrapper delegates to the previously configured factory so an
    application/library record factory remains in effect.  The marker on the
    returned callable prevents duplicate wrapping if this module is imported
    more than once or logging setup is invoked repeatedly.
    """

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous_factory(*args, **kwargs)
        # ``request_id`` is not an ``extra`` value here: setting it while the
        # record is created makes it available to every handler, including a
        # handler added to a child logger after installation.  The project
        # emits request ids through context, so callers must not pass a second
        # ``request_id`` via logging ``extra`` (which would collide with this
        # structured field in stdlib logging).
        record.request_id = get_request_id()
        return record

    setattr(factory, _RECORD_FACTORY_MARKER, True)
    return factory


def install_logging_filter() -> None:
    """Install request-id enrichment for all current and future log records.

    The root filter remains in place for records created manually by a caller,
    while the record-factory wrapper is the primary path for normal stdlib
    logging.  This distinction matters for child-logger handlers because
    propagated records bypass filters attached to ancestor loggers.
    """
    global _INSTALLED
    with _LOCK:
        if _INSTALLED:
            return
        filter_obj = RequestIdFilter()
        root = logging.getLogger()
        for handler in root.handlers:
            handler.addFilter(filter_obj)
        root.addFilter(filter_obj)
        current_factory = logging.getLogRecordFactory()
        if not getattr(current_factory, _RECORD_FACTORY_MARKER, False):
            logging.setLogRecordFactory(_make_request_id_record_factory(current_factory))
        _INSTALLED = True


def bind_extra(logger: Any, **values: Any) -> Any:
    """Best-effort contextual binding that works with both stdlib and structlog."""
    bind = getattr(logger, "bind", None)
    if callable(bind):
        return bind(**values)
    merged = dict(values)
    merged.setdefault("request_id", get_request_id())
    return logger


__all__ = [
    "REQUEST_ID_HEADER",
    "REQUEST_ID_MDC_KEY",
    "RequestIdFilter",
    "bind_extra",
    "get_request_id",
    "install_logging_filter",
    "new_request_id",
    "request_scope",
    "reset_request_id",
    "set_request_id",
]
