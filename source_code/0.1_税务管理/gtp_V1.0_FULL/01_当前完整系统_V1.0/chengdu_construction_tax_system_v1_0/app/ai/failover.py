"""Deterministic routing for the Tax AI endpoint pool.

The adapter module owns one OpenAI-compatible request.  This module owns the
policy around that request: which real endpoints are candidates, in which
order they are attempted, how one failure is recorded, and when the caller
must receive an explicit unavailable/degraded result.  Keeping these concerns
separate makes it possible for Assistant, Review, Planning, and Health to use
the same pool without each route growing its own subtly different fallback.

No credential or provider response is included in routing metadata.  The
metadata is intentionally safe to pass to an API envelope or an audit event.
"""
from __future__ import annotations

import os
import time
from collections.abc import Iterable, Sequence
from typing import Any, NamedTuple

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AIModelEndpoint
from .adapter import (
    AIEndpointTimeout,
    AIEndpointUnavailable,
    AIResponseContractError,
    call_endpoint,
    call_text_endpoint,
    endpoint_is_allowed,
    is_mock_endpoint,
    mock_endpoint_allowed,
)

DEFAULT_FAILOVER_DEADLINE_SECONDS = 120.0


def _default_deadline() -> float:
    raw = os.getenv(
        "AI_FAILOVER_DEADLINE_SECONDS",
        str(DEFAULT_FAILOVER_DEADLINE_SECONDS),
    ).strip()
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        seconds = DEFAULT_FAILOVER_DEADLINE_SECONDS
    return time.monotonic() + max(0.1, min(seconds, 600.0))


class FailoverResult(NamedTuple):
    """The normalized response and safe routing metadata.

    ``raw`` is the successful provider's response text for the existing AI
    Review persistence contract.  It is not copied into ``metadata`` or error
    messages.  ``metadata`` contains only endpoint identity, status and
    bounded attempt summaries.
    """

    result: dict[str, Any]
    raw: str
    parse_failed: bool
    metadata: dict[str, Any]


class TextFailoverResult(NamedTuple):
    """A successful natural-language answer and safe routing metadata."""

    text: str
    metadata: dict[str, Any]


def _endpoint_enabled(endpoint: AIModelEndpoint | None) -> bool:
    # SQLAlchemy applies the column default on insert.  In-memory unit fakes
    # often leave it as None, which should retain the model's historical
    # "enabled unless explicitly disabled" behaviour.
    return endpoint is not None and getattr(endpoint, "enabled", True) is not False


def _routing_group(endpoint: AIModelEndpoint | None) -> str:
    value = str(getattr(endpoint, "routing_group", "default") or "default").strip()
    return value or "default"


def _priority(endpoint: AIModelEndpoint | None) -> int:
    raw = getattr(endpoint, "priority", None)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return 100
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 100
    # The schema permits zero and rejects negative values.  Preserve zero as
    # the highest priority; treat an invalid negative value as legacy/missing
    # data instead of promoting it ahead of every configured endpoint.
    return value if value >= 0 else 100


def _endpoint_id(endpoint: AIModelEndpoint | None) -> int | None:
    value = getattr(endpoint, "id", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _endpoint_label(endpoint: AIModelEndpoint | None) -> dict[str, Any]:
    """Return identity safe for user/API output (never URL or credentials)."""
    return {
        "endpoint_id": _endpoint_id(endpoint),
        "endpoint_name": str(getattr(endpoint, "name", "") or "")[:100],
        "model": str(getattr(endpoint, "model", "") or "")[:120],
    }


def _unique_endpoints(endpoints: Iterable[AIModelEndpoint]) -> list[AIModelEndpoint]:
    seen: set[int] = set()
    result: list[AIModelEndpoint] = []
    for endpoint in endpoints:
        identity = _endpoint_id(endpoint)
        # Persisted endpoint ids are the normal identity.  Unsaved fakes use
        # object identity so two independent fixtures are not collapsed.
        marker = identity if identity is not None else id(endpoint)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(endpoint)
    return result


def _load_endpoints(
    db: Session | None,
    endpoints: Sequence[AIModelEndpoint] | None,
) -> list[AIModelEndpoint]:
    if endpoints is not None:
        return _unique_endpoints(endpoints)
    if db is None:
        return []
    return _unique_endpoints(
        db.execute(
            select(AIModelEndpoint).order_by(
                AIModelEndpoint.routing_group.asc(),
                AIModelEndpoint.priority.asc(),
                AIModelEndpoint.id.asc(),
            )
        ).scalars().all(),
    )


def select_failover_endpoints(
    db: Session | None,
    *,
    endpoint_id: int | None = None,
    routing_group: str | None = None,
    endpoints: Sequence[AIModelEndpoint] | None = None,
    allow_test_mock: bool = False,
) -> list[AIModelEndpoint]:
    """Select enabled real endpoints in deterministic failover order.

    An explicitly selected endpoint is attempted first when it is enabled and
    real.  Its routing group then constrains the fallback candidates.  Without
    an explicit endpoint, ``routing_group`` (or ``default``) constrains the
    pool.  Mock endpoints are never candidates in a normal process.  The only
    exception is an explicit test-only opt-in used by legacy deterministic
    unit fixtures; it is not reachable from a production route.
    """
    rows = _load_endpoints(db, endpoints)
    explicit: AIModelEndpoint | None = None
    if endpoint_id is not None:
        explicit = next(
            (row for row in rows if _endpoint_id(row) == int(endpoint_id)),
            None,
        )
        if explicit is None and db is not None:
            explicit = db.get(AIModelEndpoint, int(endpoint_id))
    group = _routing_group(explicit) if explicit is not None else (
        str(routing_group or "default").strip() or "default"
    )

    real = [
        row
        for row in rows
        if _endpoint_enabled(row)
        and not is_mock_endpoint(row)
        and _routing_group(row) == group
        and endpoint_is_allowed(row)
    ]
    real.sort(key=lambda row: (_priority(row), _endpoint_id(row) or 0))

    if explicit is not None and _endpoint_enabled(explicit) and not is_mock_endpoint(explicit):
        # A caller can pass an unsaved endpoint in tests.  For persisted rows,
        # identity is already represented in ``real``; for fakes, add it if it
        # passed the same policy checks.
        if endpoint_is_allowed(explicit) and _routing_group(explicit) == group:
            real = [explicit] + [row for row in real if row is not explicit and _endpoint_id(row) != _endpoint_id(explicit)]
    elif (
        allow_test_mock
        and explicit is not None
        and is_mock_endpoint(explicit)
        and mock_endpoint_allowed()
        and _endpoint_enabled(explicit)
    ):
        # This branch exists solely for the current test suite's explicit
        # mock opt-in.  No production caller passes allow_test_mock=True.
        return [explicit]

    return real


def _status_code(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _error_class(exc: BaseException) -> str:
    if isinstance(exc, (AIEndpointTimeout, TimeoutError, httpx.TimeoutException)):
        return "timeout"
    if isinstance(exc, (httpx.TransportError, ConnectionError, OSError)):
        return "network"
    if isinstance(exc, AIResponseContractError):
        return "response_contract"
    status = _status_code(exc)
    if status is not None:
        if 400 <= status < 500:
            return "http_4xx"
        if status >= 500:
            return "http_5xx"
        return "http_error"
    if isinstance(exc, (ValueError, TypeError)):
        return "configuration"
    return "provider_error"


def _safe_attempt(endpoint: AIModelEndpoint, exc: BaseException, started: float) -> dict[str, Any]:
    return {
        "endpoint_id": _endpoint_id(endpoint),
        "error_class": _error_class(exc),
        "status": _status_code(exc),
        "latency_ms": max(0, int((time.monotonic() - started) * 1000)),
    }


def _safe_failure_message(attempts: list[dict[str, Any]], *, deadline: bool = False) -> str:
    if deadline:
        return "真实 AI 端点调用超过总时间预算，未生成 AI 结论"
    if not attempts:
        return "未配置可用的真实 AI 端点"
    classes = sorted({str(item.get("error_class") or "provider_error") for item in attempts})
    return f"所有真实 AI 端点均不可用（{', '.join(classes)}），未生成 AI 结论"


def call_with_failover(
    db: Session | None,
    messages: list[dict[str, Any]],
    ctx: dict[str, Any],
    *,
    endpoint_id: int | None = None,
    routing_group: str | None = None,
    endpoints: Sequence[AIModelEndpoint] | None = None,
    deadline: float | None = None,
    allow_test_mock: bool = False,
) -> FailoverResult:
    """Call the selected endpoint and then same-group real fallbacks.

    The deadline is an absolute ``time.monotonic`` timestamp shared by all
    candidate calls and their adapter retry/backoff loops.  A successful
    fallback is explicitly marked ``DEGRADED`` with ``fallback_used=True``;
    complete exhaustion raises :class:`AIEndpointUnavailable` with a safe
    summary and safe attempts only.
    """
    candidates = select_failover_endpoints(
        db,
        endpoint_id=endpoint_id,
        routing_group=routing_group,
        endpoints=endpoints,
        allow_test_mock=allow_test_mock,
    )
    attempts: list[dict[str, Any]] = []
    if not candidates:
        raise AIEndpointUnavailable(_safe_failure_message(attempts))

    # All four production callers receive a bounded total budget, even when
    # they are not running through the asynchronous Health worker.  Callers
    # that already own a stricter absolute deadline retain it unchanged.
    deadline = _default_deadline() if deadline is None else deadline

    first_endpoint = candidates[0]
    deadline_exceeded = False
    for index, endpoint in enumerate(candidates):
        if deadline is not None and deadline - time.monotonic() <= 0:
            deadline_exceeded = True
            break
        started = time.monotonic()
        try:
            result, raw, parse_failed = call_endpoint(
                endpoint,
                messages,
                ctx,
                deadline=deadline,
            )
            # A response that violates the declared Tax review contract is a
            # failed candidate, not a successful response with guessed fields.
            if parse_failed:
                raise AIResponseContractError("模型响应未通过结构化结果校验")
            metadata = {
                **_endpoint_label(endpoint),
                "actual_endpoint_id": _endpoint_id(endpoint),
                "actual_endpoint_name": str(getattr(endpoint, "name", "") or "")[:100],
                "actual_model": str(getattr(endpoint, "model", "") or "")[:120],
                "status": "DEGRADED" if index else "READY",
                "fallback_used": bool(index),
                "attempts": attempts + [{
                    "endpoint_id": _endpoint_id(endpoint),
                    "error_class": None,
                    "status": "ok",
                    "latency_ms": max(0, int((time.monotonic() - started) * 1000)),
                }],
            }
            return FailoverResult(result, raw, False, metadata)
        except BaseException as exc:
            # BaseException is intentional for cancellation propagation only
            # after we have not swallowed it.  asyncio cancellation must not
            # turn into a fake AI result; preserve it as a bounded failure.
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            attempts.append(_safe_attempt(endpoint, exc, started))
            if isinstance(exc, AIEndpointTimeout):
                deadline_exceeded = True
                break
            if deadline is not None and deadline - time.monotonic() <= 0:
                deadline_exceeded = True
                break

    error = AIEndpointUnavailable(
        _safe_failure_message(attempts, deadline=deadline_exceeded),
        status="UNAVAILABLE",
        attempts=attempts,
        endpoint=first_endpoint,
    )
    error.data_gaps.extend(
        [
            "AI failover 已耗尽所有真实端点",
            "必须人工复核；系统未使用模拟结论",
        ],
    )
    raise error


def call_text_with_failover(
    db: Session | None,
    messages: list[dict[str, Any]],
    ctx: dict[str, Any],
    *,
    endpoint_id: int | None = None,
    routing_group: str | None = None,
    endpoints: Sequence[AIModelEndpoint] | None = None,
    deadline: float | None = None,
) -> TextFailoverResult:
    """Call real endpoints for natural-language manager questions.

    Unlike :func:`call_with_failover`, this route treats a non-empty assistant
    text as the complete response contract.  It never interprets that text as
    Review/Planning JSON, while retaining the same endpoint selection, SSRF,
    credential, timeout and real-endpoint-only failover policy.
    """
    candidates = select_failover_endpoints(
        db,
        endpoint_id=endpoint_id,
        routing_group=routing_group,
        endpoints=endpoints,
    )
    attempts: list[dict[str, Any]] = []
    if not candidates:
        raise AIEndpointUnavailable("未配置可用的真实 AI 端点")

    deadline = _default_deadline() if deadline is None else deadline
    first_endpoint = candidates[0]
    deadline_exceeded = False
    for index, endpoint in enumerate(candidates):
        if deadline - time.monotonic() <= 0:
            deadline_exceeded = True
            break
        started = time.monotonic()
        try:
            text = call_text_endpoint(
                endpoint,
                messages,
                ctx,
                deadline=deadline,
            )
            if not isinstance(text, str) or not text.strip():
                raise AIResponseContractError(
                    "模型端点未返回非空 assistant 文本",
                )
            text = text.strip()
            metadata = {
                **_endpoint_label(endpoint),
                "actual_endpoint_id": _endpoint_id(endpoint),
                "actual_endpoint_name": str(getattr(endpoint, "name", "") or "")[:100],
                "actual_model": str(getattr(endpoint, "model", "") or "")[:120],
                "status": "DEGRADED" if index else "READY",
                "fallback_used": bool(index),
                "attempts": attempts + [{
                    "endpoint_id": _endpoint_id(endpoint),
                    "error_class": None,
                    "status": "ok",
                    "latency_ms": max(0, int((time.monotonic() - started) * 1000)),
                }],
            }
            return TextFailoverResult(text, metadata)
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            attempts.append(_safe_attempt(endpoint, exc, started))
            if isinstance(exc, AIEndpointTimeout):
                deadline_exceeded = True
                break
            if deadline - time.monotonic() <= 0:
                deadline_exceeded = True
                break

    message = (
        "真实 AI 端点调用超过总时间预算，未生成 AI 问答"
        if deadline_exceeded
        else (
            "所有真实 AI 端点均不可用，未生成 AI 问答"
            if attempts
            else "未配置可用的真实 AI 端点"
        )
    )
    error = AIEndpointUnavailable(
        message,
        status="UNAVAILABLE",
        attempts=attempts,
        endpoint=first_endpoint,
    )
    error.data_gaps.extend([
        "AI 文本问答 failover 已耗尽所有真实端点",
        "必须人工复核；系统未使用模拟结论",
    ])
    raise error


__all__ = [
    "FailoverResult",
    "TextFailoverResult",
    "call_with_failover",
    "call_text_with_failover",
    "select_failover_endpoints",
]
