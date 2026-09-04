"""V0.2: Application startup and lifecycle helpers.

Contains health-check aggregation, request-id middleware, and any
lifespan-scoped initialization that should not live in the wiring layer.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable

import httpx
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from . import config
from .observability import (
    REQUEST_ID_HEADER,
    get_request_id,
    new_request_id,
    reset_request_id,
    set_request_id,
)
from .security import validate_ai_endpoint_url
from .structured_logging import (
    bind_request_context,
    get_logger,
    reset_request_context,
    resolve_request_id,
)

logger = get_logger("app.startup")

# ``/healthz`` is polled by the desktop controller, whose request budget is
# intentionally short.  A health probe should fail fast and report a
# dependency as degraded rather than hold the public liveness endpoint open
# for the normal (longer) business-request timeout.
_MAX_HEALTH_PROBE_TIMEOUT_SECONDS = 1.5
_MAX_AI_HEALTH_DEADLINE_SECONDS = 2.0

_HEALTH_TIMEOUT_SECONDS = min(
    max(float(os.getenv("HEALTH_CHECK_TIMEOUT_SECONDS", "1.5")), 0.1),
    _MAX_HEALTH_PROBE_TIMEOUT_SECONDS,
)


def _ai_health_deadline_seconds() -> float:
    """Return a bounded total budget for one synchronous AI health pass."""
    raw = os.getenv(
        "AI_HEALTH_ENDPOINT_DEADLINE_SECONDS",
        str(_MAX_AI_HEALTH_DEADLINE_SECONDS),
    )
    try:
        configured = float(raw)
    except (TypeError, ValueError):
        configured = _MAX_AI_HEALTH_DEADLINE_SECONDS
    return min(max(configured, 0.1), _MAX_AI_HEALTH_DEADLINE_SECONDS)


def _health_headers(request_id: str | None = None, *, facts: bool = False) -> dict[str, str]:
    """Build probe headers without exposing credentials in health responses."""
    rid = request_id or get_request_id() or new_request_id()
    key_name = "RAG_V1_FACTS_API_KEY" if facts else "RAG_SHARED_API_KEY"
    key = getattr(config, key_name, "")
    headers = {"X-Request-ID": rid}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


# ---------------------------------------------------------------------------
# Health-check helpers
# ---------------------------------------------------------------------------

def _check_db() -> dict[str, object]:
    """Run a cheap ``SELECT 1`` and report status + latency."""
    started = time.monotonic()
    try:
        from .db import engine

        with engine.connect() as conn:
            from sqlalchemy import text
            conn.execute(text("SELECT 1"))
        latency_ms = int((time.monotonic() - started) * 1000)
        return {"status": "ok", "latency_ms": latency_ms}
    except Exception:
        latency_ms = int((time.monotonic() - started) * 1000)
        # Health is public; do not disclose DSNs, local paths or driver text.
        logger.warning("healthz db check failed")
        return {
            "status": "down",
            "latency_ms": latency_ms,
            "error": "database_unavailable",
        }


def _check_rag() -> dict[str, object]:
    """Probe the configured RAG endpoint.  Down status must never be reported as ``ok``."""
    started = time.monotonic()
    rag_url = (getattr(config, "RAG_SERVICE_URL", "") or "").strip().rstrip("/")
    if not rag_url:
        return {"status": "down", "latency_ms": 0, "error": "RAG_SERVICE_URL 未配置"}
    try:
        with httpx.Client(timeout=_HEALTH_TIMEOUT_SECONDS, follow_redirects=False) as client:
            response = client.get(
                f"{rag_url}/api/v1/health",
                headers=_health_headers(),
            )
        latency_ms = int((time.monotonic() - started) * 1000)
        if response.status_code >= 500:
            return {"status": "down", "latency_ms": latency_ms, "error": "rag_server_error"}
        if response.status_code >= 400:
            return {"status": "degraded", "latency_ms": latency_ms, "error": "rag_probe_rejected"}
        return {"status": "ok", "latency_ms": latency_ms}
    except httpx.TimeoutException:
        latency_ms = int((time.monotonic() - started) * 1000)
        return {"status": "down", "latency_ms": latency_ms, "error": "rag_probe_timeout"}
    except Exception:
        latency_ms = int((time.monotonic() - started) * 1000)
        return {"status": "down", "latency_ms": latency_ms, "error": "rag_unreachable"}


def _check_facts() -> dict[str, object]:
    """Verify the Facts provider surface without writing to the database."""
    started = time.monotonic()
    rag_url = (getattr(config, "RAG_SERVICE_URL", "") or "").strip().rstrip("/")
    if not rag_url:
        return {"status": "down", "latency_ms": 0, "error": "RAG_SERVICE_URL 未配置"}
    try:
        with httpx.Client(timeout=_HEALTH_TIMEOUT_SECONDS, follow_redirects=False) as client:
            response = client.get(
                f"{rag_url}/api/v1/facts/projects/__healthcheck__",
                headers=_health_headers(facts=True),
            )
        latency_ms = int((time.monotonic() - started) * 1000)
        if response.status_code == 404:
            return {"status": "ok", "latency_ms": latency_ms}
        if response.status_code >= 500:
            return {"status": "down", "latency_ms": latency_ms, "error": "facts_server_error"}
        if response.status_code >= 400:
            return {"status": "degraded", "latency_ms": latency_ms, "error": "facts_probe_rejected"}
        return {"status": "ok", "latency_ms": latency_ms}
    except httpx.TimeoutException:
        latency_ms = int((time.monotonic() - started) * 1000)
        return {"status": "down", "latency_ms": latency_ms, "error": "facts_probe_timeout"}
    except Exception:
        latency_ms = int((time.monotonic() - started) * 1000)
        return {"status": "down", "latency_ms": latency_ms, "error": "facts_unreachable"}


def _check_ai() -> dict[str, object]:
    """Probe each enabled AI chat endpoint with a bounded minimal request.

    ``HEAD(base_url)`` is not an OpenAI-compatible contract: many providers
    reject HEAD or expose no useful root route.  A one-token, non-streaming
    chat request against the configured ``chat_path`` verifies the endpoint
    that the adapter actually uses, while the short total deadline prevents a
    health request from becoming a model-generation job.
    """
    started = time.monotonic()
    endpoints: list[dict[str, object]] = []
    overall = "ok"
    deadline = started + _ai_health_deadline_seconds()
    try:
        from .ai.adapter import _resolve_endpoint_key, endpoint_is_allowed
        from .db import SessionLocal
        from .models import AIModelEndpoint

        with SessionLocal() as db:
            rows = (
                db.query(AIModelEndpoint)
                .filter(AIModelEndpoint.enabled.is_(True))
                .all()
            )
        rows = [row for row in rows if endpoint_is_allowed(row)]
        if not rows:
            return {
                "status": "degraded",
                "latency_ms": int((time.monotonic() - started) * 1000),
                "error": "no_enabled_ai_endpoints",
                "endpoints": [],
            }
        for row in rows:
            base_url = (row.base_url or "").strip()
            # The URL can contain credentials or reveal private topology; the
            # endpoint name is enough for a health response.
            entry: dict[str, object] = {"name": row.name}
            if not base_url:
                entry["status"] = "down"
                entry["error"] = "base_url_unconfigured"
                overall = "degraded"
                endpoints.append(entry)
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                entry["status"] = "down"
                entry["error"] = "ai_health_deadline_exceeded"
                overall = "degraded"
                endpoints.append(entry)
                continue
            try:
                chat_path = str(row.chat_path or "")
                validated_base_url = validate_ai_endpoint_url(
                    base_url,
                    chat_path,
                )
                url = validated_base_url + "/" + chat_path.lstrip("/")
                headers = {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }
                try:
                    # Resolve only this endpoint's credential.  A generic
                    # provider-key fallback can send provider A's secret to
                    # provider B and is therefore forbidden.
                    key = _resolve_endpoint_key(row)
                except RuntimeError:
                    entry["status"] = "degraded"
                    entry["error"] = "ai_credential_unavailable"
                    overall = "degraded"
                    endpoints.append(entry)
                    continue
                if key:
                    headers["Authorization"] = f"Bearer {key}"
                payload = {
                    "model": row.model,
                    "messages": [{"role": "user", "content": "health check"}],
                    "temperature": 0,
                    "max_tokens": 1,
                    "stream": False,
                }
                with httpx.Client(
                    timeout=min(_HEALTH_TIMEOUT_SECONDS, remaining),
                    follow_redirects=False,
                ) as client:
                    probe = client.post(url, headers=headers, json=payload)
                entry["status"] = (
                    "ok" if 200 <= probe.status_code < 300 else "degraded"
                )
                if not 200 <= probe.status_code < 300:
                    overall = "degraded"
                    entry["error"] = "ai_probe_rejected"
            except ValueError:
                entry["status"] = "degraded"
                entry["error"] = "ai_endpoint_configuration_invalid"
                overall = "degraded"
            except httpx.TimeoutException:
                entry["status"] = "down"
                entry["error"] = "ai_probe_timeout"
                overall = "degraded"
            except Exception:
                entry["status"] = "down"
                entry["error"] = "ai_unreachable"
                overall = "degraded"
            endpoints.append(entry)
    except Exception:
        latency_ms = int((time.monotonic() - started) * 1000)
        return {
            "status": "down",
            "latency_ms": latency_ms,
            "error": "ai_dependency_check_failed",
            "endpoints": endpoints,
        }
    latency_ms = int((time.monotonic() - started) * 1000)
    statuses = {str(endpoint.get("status") or "") for endpoint in endpoints}
    # The configured endpoints form a failover pool.  One healthy endpoint
    # keeps the AI capability available; individual failures remain visible
    # in ``endpoints`` for diagnosis.  Only a pool with no healthy endpoint
    # is unavailable as a whole.
    overall = "ok" if "ok" in statuses else "down"
    return {"status": overall, "latency_ms": latency_ms, "endpoints": endpoints}


# ---------------------------------------------------------------------------
# Request-ID passthrough middleware
# ---------------------------------------------------------------------------

class RequestIdMiddleware(BaseHTTPMiddleware):
    """Ensure every response carries the request-id header.

    The :class:`AuthMiddleware` already injects the header, but the health
    endpoint lives in the public-prefix allow list so the middleware skips it;
    this layer guarantees the header is present everywhere.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Response]
    ) -> Response:
        resolved_id = resolve_request_id(request.headers.get(REQUEST_ID_HEADER))
        token = set_request_id(resolved_id)
        structlog_tokens = bind_request_context(resolved_id)
        request.state.request_id = resolved_id
        try:
            response = await call_next(request)
        finally:
            reset_request_context(structlog_tokens)
            reset_request_id(token)
        response.headers[REQUEST_ID_HEADER] = resolved_id
        return response
