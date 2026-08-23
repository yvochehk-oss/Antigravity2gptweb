"""Observability utilities for cross-system tracing.

Every incoming HTTP request is assigned a ``request_id`` so that logs, audit
entries, and downstream outbound calls (notably ``Tax -> RAG``) can be
correlated from a single client request through to the response header.

The module deliberately avoids any external dependencies so it can be
imported by both the FastAPI application and the test suite.
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import threading
import uuid
from typing import Any, Iterator

# Bound to the current request via :func:`set_request_id`.  ``contextvars``
# is used so the value travels correctly across threads (the FastAPI sync
# tests instantiate multiple workers concurrently) and through ``Background``
# tasks relying on the same context.
_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="",
)
_actor_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "actor", default="anonymous",
)

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_MDC_KEY = "request_id"

_LOCK = threading.Lock()
_CACHED_LOGGER_FILTERS: dict[int, logging.Filter] = {}


class RequestIdFilter(logging.Filter):
    """Inject the active ``request_id`` into every stdlib log record.

    The filter is attached to the root logger so both the application logger
    and any third-party loggers end up with the same field.  The value is
    read from a :class:`contextvars.ContextVar` so it is safe even when
    ``logging`` is called from a thread that started before the request.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # pragma: no cover - trivial
        record.request_id = _request_id_var.get() or ""
        return True


def new_request_id() -> str:
    """Return a stable, URL-safe UUID4 hex string for one request."""
    return uuid.uuid4().hex


def get_request_id() -> str:
    """Return the request id active for the current context."""
    return _request_id_var.get() or ""


def set_request_id(value: str) -> contextvars.Token:
    """Bind ``value`` as the active request id for the current context.

    Returns the :class:`contextvars.Token` so the caller can restore the
    previous value when the request finishes.
    """
    cleaned = (value or "").strip()
    if not cleaned:
        cleaned = new_request_id()
    return _request_id_var.set(cleaned)


def reset_request_id(token: contextvars.Token | None) -> None:
    """Reset the active request id using a previously stored token."""
    if token is not None:
        try:
            _request_id_var.reset(token)
        except (ValueError, LookupError):
            pass


def current_actor() -> str:
    """Return the actor (user) associated with the current request context."""
    return _actor_var.get() or "anonymous"


def set_actor(value: str) -> contextvars.Token:
    actor = (value or "").strip() or "anonymous"
    return _actor_var.set(actor)


def reset_actor(token: contextvars.Token | None) -> None:
    if token is not None:
        try:
            _actor_var.reset(token)
        except (ValueError, LookupError):
            pass


@contextlib.contextmanager
def request_scope(request_id: str | None, actor: str | None = None) -> Iterator[str]:
    """Context manager that pins request id and actor for the block.

    Returns the resolved ``request_id`` so callers can echo it back to the
    client.  If ``request_id`` is empty a fresh UUID4 is generated.
    """
    final_id = (request_id or "").strip() or new_request_id()
    id_token = set_request_id(final_id)
    actor_token = set_actor(actor or "anonymous")
    try:
        yield final_id
    finally:
        reset_actor(actor_token)
        reset_request_id(id_token)


def install_logging_filter() -> None:
    """Attach a :class:`RequestIdFilter` to the root logger exactly once.

    Tests re-run this helper; the lock guarantees that the filter is added
    only once per process even when :func:`configure_logging` is called
    multiple times.
    """
    root = logging.getLogger()
    with _LOCK:
        existing = _CACHED_LOGGER_FILTERS.get(id(root))
        if existing is not None:
            return
        filter_obj = RequestIdFilter()
        for handler in root.handlers:
            handler.addFilter(filter_obj)
        root.addFilter(filter_obj)
        _CACHED_LOGGER_FILTERS[id(root)] = filter_obj


def bind_extra(logger: Any, **values: Any) -> Any:
    """Best-effort attachment of context fields to a structlog-style logger.

    ``structlog`` has ``bind``; :class:`logging.Logger` does not.  This
    helper keeps callers agnostic to which backend is configured so the
    application code can call ``logger.info("event", **payload)``
    interchangeably.
    """
    bind = getattr(logger, "bind", None)
    if callable(bind):
        return bind(**values)
    merged = dict(values)
    merged.setdefault("request_id", get_request_id())
    merged.setdefault("actor", current_actor())
    return logger


__all__ = [
    "REQUEST_ID_HEADER",
    "REQUEST_ID_MDC_KEY",
    "RequestIdFilter",
    "bind_extra",
    "current_actor",
    "get_request_id",
    "install_logging_filter",
    "new_request_id",
    "request_scope",
    "reset_actor",
    "reset_request_id",
    "set_actor",
    "set_request_id",
]
