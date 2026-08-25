"""V0.2: RAG V1.0 Facts Provider 客户端。

税务系统侧调用 RAG V1.0 Facts Provider 的统一入口：
- 调用 `/api/v1/facts/projects/{project_code}` 读取指标
- 调用 `/api/v1/facts/invalidate` 在业务变更时主动失效缓存
- 每次调用记录到 `facts_request_logs` 表；响应保留到 `facts_snapshots` 表

Facts Provider 是 RAG V1.0 统一事实通道，承载 L2（确定性计算）和 L3（Analytics Contract）。
本客户端仅作调用 + 快照记录，不复制指标口径。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy.exc import IntegrityError

from .. import config
from ..audit import audit
from ..cache import TTLCache
from ..db import SessionLocal
from ..models import FactsRequestLog, FactsSnapshot, Project, RagServiceEndpoint
from ..observability import get_request_id
from ..security import validate_rag_service_url
from ..structured_logging import resolve_request_id

FACTS_URL: str = config.RAG_V1_FACTS_URL
FACTS_API_KEY: str = config.RAG_V1_FACTS_API_KEY
FACTS_DEFAULT_MAX_AGE: int = config.FACTS_DEFAULT_MAX_AGE
FACTS_HTTP_TIMEOUT_SECONDS: float = float(os.getenv("TAX_FACTS_HTTP_TIMEOUT_SECONDS", "10"))
FACTS_CACHE_TTL_SECONDS: float = float(os.getenv("TAX_FACTS_CACHE_TTL_SECONDS", "30"))
FACTS_CACHE_MAX_CAPACITY: int = int(os.getenv("TAX_FACTS_CACHE_MAX_CAPACITY", "256"))


@dataclass(frozen=True)
class _FactsResult:
    payload: dict[str, Any]
    response_status: int
    remote_latency_ms: int


class _FactsProviderError(RuntimeError):
    """A remote Facts failure that must never be cached as a successful value."""

    def __init__(self, message: str, *, response_status: int = 0, latency_ms: int = 0):
        super().__init__(message)
        self.message = message
        self.response_status = response_status
        self.latency_ms = latency_ms


facts_cache: TTLCache[_FactsResult] = TTLCache(
    ttl_seconds=FACTS_CACHE_TTL_SECONDS,
    max_capacity=FACTS_CACHE_MAX_CAPACITY,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_SNAPSHOT_VOLATILE_KEYS = frozenset(
    {"cache_status", "request_id", "_snapshot_metadata"}
)


def _snapshot_payload_for_storage(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep only the remote Facts response in the canonical snapshot.

    ``cache_status`` and ``request_id`` are local/request-scoped metadata.  If
    they are stored in ``facts_data``, the first cache miss and a later cache
    hit look like two different payloads even though the remote Facts version
    is identical.  Excluding them keeps the unique
    ``(project_code, facts_version)`` snapshot contract deterministic.
    """
    return {
        key: value
        for key, value in dict(payload).items()
        if key not in _SNAPSHOT_VOLATILE_KEYS
    }


def _same_snapshot_payload(left: Any, right: Any) -> bool:
    """Compare canonical payloads while tolerating legacy volatile fields."""
    if not isinstance(left, dict) or not isinstance(right, dict):
        return left == right
    return (
        _snapshot_payload_for_storage(left)
        == _snapshot_payload_for_storage(right)
    )


def _get_or_create_snapshot(
    db,
    *,
    project_id: int,
    project_code: str,
    facts_version: str,
    as_of: str,
    payload: dict[str, Any],
    actor: str,
) -> FactsSnapshot:
    """Return the canonical snapshot for one project/version.

    The unique key makes repeated cache hits idempotent.  The nested
    transaction also handles two concurrent requests racing to insert the
    same version without rolling back the already-created request log.
    A same-version/different-payload response is a data-integrity conflict,
    never something to resolve by choosing ``.first()`` or overwriting data.
    """
    canonical_payload = _snapshot_payload_for_storage(payload)
    existing = (
        db.query(FactsSnapshot)
        .filter(
            FactsSnapshot.project_code == project_code,
            FactsSnapshot.facts_version == facts_version,
        )
        .one_or_none()
    )
    if existing is not None:
        if not _same_snapshot_payload(existing.facts_data, canonical_payload):
            raise ValueError(
                "Facts snapshot version already exists with a different payload"
            )
        return existing

    snapshot = FactsSnapshot(
        project_id=project_id,
        project_code=project_code,
        as_of=as_of,
        facts_version=facts_version,
        facts_data=canonical_payload,
        analytics_contract_version=str(
            payload.get("analytics_contract_version") or "1.0"
        ),
        created_at=_now(),
        created_by=actor,
    )
    try:
        with db.begin_nested():
            db.add(snapshot)
            db.flush()
    except IntegrityError as exc:
        # Another transaction may have committed this version after the
        # pre-check.  The savepoint keeps the request log transaction alive.
        existing = (
            db.query(FactsSnapshot)
            .filter(
                FactsSnapshot.project_code == project_code,
                FactsSnapshot.facts_version == facts_version,
            )
            .one_or_none()
        )
        if existing is None:
            raise
        if not _same_snapshot_payload(existing.facts_data, canonical_payload):
            raise ValueError(
                "Facts snapshot version already exists with a different payload"
            ) from exc
        return existing
    return snapshot


def _headers(*, request_id: str | None = None) -> dict[str, str]:
    """Return outbound HTTP headers, including ``X-Request-ID`` for cross-system tracing.

    The ``request_id`` argument is forwarded as the outbound header so the
    RAG service can echo the same identifier back in its own logs and
    response headers.  When no id is supplied the active observability
    scope is consulted as a fallback.
    """
    h = {"Content-Type": "application/json"}
    if FACTS_API_KEY:
        h["Authorization"] = f"Bearer {FACTS_API_KEY}"
    h["X-Request-ID"] = resolve_request_id(request_id or get_request_id())
    return h


def _configured_facts_base_url(db) -> str:
    """Resolve the active RAG base URL and revalidate approved DNS state.

    The administrator-managed endpoint is shared by Tax -> RAG sync and the
    Facts Provider.  The process environment remains the compatibility
    fallback until an endpoint is saved through ``/rag-sync/settings``.
    """
    endpoint = db.query(RagServiceEndpoint).filter(
        RagServiceEndpoint.id == 1,
        RagServiceEndpoint.enabled.is_(True),
    ).first()
    if endpoint is None:
        return FACTS_URL.rstrip("/")

    raw_snapshot = str(endpoint.resolved_addresses_json or "")
    try:
        snapshot = json.loads(raw_snapshot or "[]")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("RAG 服务批准记录的 DNS 地址快照无效") from exc
    if not isinstance(snapshot, list) or not all(
        isinstance(value, str) and value.strip() for value in snapshot
    ):
        raise ValueError("RAG 服务批准记录的 DNS 地址快照无效")

    return validate_rag_service_url(
        str(endpoint.base_url or "").strip().rstrip("/"),
        allow_approved_private=bool(endpoint.approved_private),
        approved_addresses=frozenset(snapshot),
    )


def _cache_key(
    project_code: str,
    *,
    base_url: str | None = None,
    require_fresh: bool,
    max_age: int,
    as_of: str | None,
) -> str:
    """Build a non-secret cache key for one Facts query shape."""
    key_fingerprint = hashlib.sha256(FACTS_API_KEY.encode("utf-8")).hexdigest()[:12]
    return "::".join(
        (
            (base_url or FACTS_URL).rstrip("/"),
            project_code,
            str(bool(require_fresh)),
            str(max_age),
            as_of or "",
            key_fingerprint,
        ),
    )


def _fetch_remote_facts(
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, Any],
) -> _FactsResult:
    """Fetch one Facts response and raise on every non-success condition."""
    started = time.monotonic()
    response_status = 0
    try:
        with httpx.Client(
            timeout=FACTS_HTTP_TIMEOUT_SECONDS,
            follow_redirects=False,
        ) as client:
            response = client.get(url, headers=headers, params=params)
        response_status = response.status_code
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not payload:
            raise ValueError("Facts Provider returned an empty response")
        if payload.get("status") in {"DEGRADED", "ERROR"} or payload.get("facts_available") is False:
            raise ValueError("Facts Provider returned unavailable facts")
        return _FactsResult(
            payload=dict(payload),
            response_status=response_status,
            remote_latency_ms=int((time.monotonic() - started) * 1000),
        )
    except httpx.HTTPStatusError as exc:
        response = exc.response
        body = str(getattr(response, "text", ""))[:200].replace("\n", " ")
        message = f"Facts Provider HTTP {response.status_code}"
        if body:
            message = f"{message}: {body}"
        raise _FactsProviderError(
            message,
            response_status=response.status_code,
            latency_ms=int((time.monotonic() - started) * 1000),
        ) from exc
    except httpx.TimeoutException as exc:
        raise _FactsProviderError(
            f"Facts Provider timeout ({FACTS_HTTP_TIMEOUT_SECONDS:g}s)",
            response_status=response_status,
            latency_ms=int((time.monotonic() - started) * 1000),
        ) from exc
    except _FactsProviderError:
        raise
    except Exception as exc:
        # Keep a bounded diagnostic for the caller/audit while preventing
        # newlines and huge driver tracebacks from entering the DB.
        message = str(exc).replace("\r", " ").replace("\n", " ")[:300]
        raise _FactsProviderError(
            f"Facts call failed: {message}",
            response_status=response_status,
            latency_ms=int((time.monotonic() - started) * 1000),
        ) from exc


def _project_code_from_id(db, project_id: int) -> str | None:
    """根据税务系统 project_id 查找对应的 project_code。"""
    if not project_id:
        return None
    row = db.query(Project).filter(Project.id == project_id).first()
    return row.code if row else None


def get_project_facts(
    project_id: int,
    *,
    require_fresh: bool = False,
    max_age: int = FACTS_DEFAULT_MAX_AGE,
    as_of: str | None = None,
    actor: str = "system",
    ip: str = "",
    request_id: str | None = None,
) -> dict[str, Any]:
    """调用 RAG V1.0 Facts Provider 读取项目指标。

    行为：
    1. 由 project_id 查出 project_code；
    2. 调用 `/api/v1/facts/projects/{project_code}`；
    3. 将响应写入 facts_snapshots（每次响应都落库，便于追溯）；
    4. 写一条 facts_request_logs（含 status / latency_ms / actor / ip）。

    Returns ``{"error": ..., "status": "DEGRADED"}`` on failure.  The
    function never collapses a failed call into an empty success payload:
    callers downstream of this module MUST inspect ``status`` before
    promoting the response to a financial statement.
    """
    started = time.monotonic()
    resolved_request_id = resolve_request_id(request_id or get_request_id())
    db = SessionLocal()
    try:
        project_code = _project_code_from_id(db, project_id)
        if not project_code:
            return {
                "error": f"project_id={project_id} 在税务系统内不存在",
                "status": "DEGRADED",
                "facts_available": False,
                "cache_status": "bypassed",
                "request_id": resolved_request_id,
            }

        try:
            facts_base_url = _configured_facts_base_url(db)
        except ValueError as exc:
            return {
                "error": f"RAG Facts 服务地址不安全: {exc}",
                "status": "DEGRADED",
                "facts_available": False,
                "cache_status": "bypassed",
                "project_code": project_code,
                "request_id": resolved_request_id,
            }

        endpoint = f"/api/v1/facts/projects/{project_code}"
        url = f"{facts_base_url}{endpoint}"
        params: dict[str, Any] = {}
        if require_fresh:
            params["require_fresh"] = "true"
        if max_age and max_age != FACTS_DEFAULT_MAX_AGE:
            params["max_age"] = max_age
        if as_of:
            params["as_of"] = as_of

        log = FactsRequestLog(
            project_code=project_code,
            endpoint=endpoint,
            require_fresh=require_fresh,
            max_age=max_age,
            as_of_param=as_of or "",
            response_status=0,
            latency_ms=0,
            actor=actor,
            ip=ip,
            request_id=resolved_request_id,
            created_at=_now(),
        )
        db.add(log)
        db.flush()

        key = _cache_key(
            project_code,
            base_url=facts_base_url,
            require_fresh=False,
            max_age=max_age,
            as_of=as_of,
        )
        cache_status = "bypassed" if require_fresh else "miss"
        response_payload: dict[str, Any] = {}
        response_status = 0
        error_message = ""
        try:
            loader = lambda: _fetch_remote_facts(  # noqa: E731
                url,
                headers=_headers(request_id=resolved_request_id),
                params=params,
            )
            if require_fresh:
                result = loader()
                # Fresh requests update the local copy after the remote call,
                # but never serve a stale local value.
                facts_cache.set(key, result)
            else:
                result, cache_status = facts_cache.get_with_status(key, loader)
            response_payload = dict(result.payload)
            response_status = result.response_status
        except _FactsProviderError as exc:
            error_message = exc.message
            response_status = exc.response_status

        latency_ms = int((time.monotonic() - started) * 1000)
        log.response_status = response_status or 500
        log.latency_ms = latency_ms
        log.error_message = error_message

        if not error_message and response_payload:
            snapshot_payload = _snapshot_payload_for_storage(response_payload)
            response_payload["cache_status"] = cache_status
            as_of_ts = response_payload.get("as_of") or _now()
            facts_version = response_payload.get("facts_version") or ""
            try:
                snapshot = _get_or_create_snapshot(
                    db,
                    project_id=project_id,
                    project_code=project_code,
                    as_of=as_of_ts,
                    facts_version=facts_version,
                    payload=snapshot_payload,
                    actor=actor,
                )
            except ValueError as exc:
                # A reused Facts version with a changed payload is a remote
                # contract violation.  Keep the request/audit trail but do
                # not promote the response to an available financial fact.
                error_message = f"Facts snapshot conflict: {exc}"
                response_status = 500
                log.response_status = response_status
                log.error_message = error_message
                audit(
                    db,
                    action="QUERY_FAILED",
                    obj_type="Facts",
                    obj_id="conflict",
                    message=f"{project_code} {error_message}",
                    actor=actor,
                    ip=ip,
                    request_id=resolved_request_id,
                )
            else:
                log.facts_snapshot_id = snapshot.id
                audit(
                    db,
                    action="QUERY",
                    obj_type="Facts",
                    obj_id=snapshot.id,
                    message=(
                        f"{project_code} as_of={as_of_ts} version={facts_version} "
                        f"cache={cache_status} latency={latency_ms}ms"
                    ),
                    actor=actor,
                    ip=ip,
                    request_id=resolved_request_id,
                )
        else:
            audit(
                db,
                action="QUERY",
                obj_type="Facts",
                obj_id="error",
                message=f"{project_code} cache={cache_status} {error_message}",
                actor=actor,
                ip=ip,
                request_id=resolved_request_id,
            )

        db.commit()
        if error_message:
            return {
                "error": error_message,
                "status": "DEGRADED",
                "facts_available": False,
                "project_code": project_code,
                "cache_status": cache_status,
                "request_id": resolved_request_id,
            }
        response_payload.setdefault("status", "AVAILABLE")
        response_payload.setdefault("facts_available", True)
        response_payload.setdefault("request_id", resolved_request_id)
        return response_payload
    finally:
        db.close()


def invalidate_facts(
    project_id: int,
    *,
    actor: str = "system",
    ip: str = "",
    request_id: str | None = None,
) -> dict[str, Any]:
    """业务数据变更后调用：通知 Facts Provider 失效该项目缓存。"""
    resolved_request_id = resolve_request_id(request_id or get_request_id())
    db = SessionLocal()
    try:
        project_code = _project_code_from_id(db, project_id)
        if not project_code:
            return {
                "error": f"project_id={project_id} 不存在",
                "status": "DEGRADED",
                "facts_available": False,
                "cache_status": "bypassed",
                "request_id": resolved_request_id,
            }

        # Clear local entries first.  If the remote invalidation fails, a
        # subsequent request must not silently reuse a value known to be stale.
        removed = facts_cache.invalidate_where(
            lambda key: f"::{project_code}::" in key,
        )
        try:
            facts_base_url = _configured_facts_base_url(db)
        except ValueError as exc:
            return {
                "error": f"RAG Facts 服务地址不安全: {exc}",
                "status": "DEGRADED",
                "facts_available": False,
                "cache_status": "invalidated_local_only",
                "local_cache_entries_removed": removed,
                "project_code": project_code,
                "request_id": resolved_request_id,
            }

        url = f"{facts_base_url}/api/v1/facts/invalidate"
        body = {"project_code": project_code}
        try:
            with httpx.Client(
                timeout=FACTS_HTTP_TIMEOUT_SECONDS,
                follow_redirects=False,
            ) as client:
                resp = client.post(
                    url,
                    headers=_headers(request_id=resolved_request_id),
                    json=body,
                )
                resp.raise_for_status()
                payload = resp.json()
            if not isinstance(payload, dict):
                payload = {"remote_response": payload}
            payload.setdefault("status", "AVAILABLE")
            payload["cache_status"] = "invalidated"
            payload["local_cache_entries_removed"] = removed
            payload["request_id"] = resolved_request_id
            audit(
                db,
                action="INVALIDATE",
                obj_type="Facts",
                obj_id=project_code,
                message=f"已失效 Facts 缓存: {project_code} local={removed}",
                actor=actor,
                ip=ip,
                request_id=resolved_request_id,
            )
            db.commit()
            return payload
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            error_message = f"invalidate HTTP {status}"
        except httpx.TimeoutException:
            error_message = f"invalidate timeout ({FACTS_HTTP_TIMEOUT_SECONDS:g}s)"
        except Exception as exc:
            error_message = f"invalidate 失败: {str(exc).replace(chr(10), ' ')[:300]}"

        audit(
            db,
            action="INVALIDATE_FAILED",
            obj_type="Facts",
            obj_id=project_code,
            message=f"{project_code} local cache cleared={removed}; {error_message}",
            actor=actor,
            ip=ip,
            request_id=resolved_request_id,
        )
        db.commit()
        return {
            "error": error_message,
            "status": "DEGRADED",
            "facts_available": False,
            "project_code": project_code,
            "cache_status": "invalidated_local_only",
            "local_cache_entries_removed": removed,
            "request_id": resolved_request_id,
        }
    finally:
        db.close()


def get_latest_snapshot(project_id: int) -> dict[str, Any] | None:
    """读取最近一次的 Facts 快照（无需调用 RAG）。"""
    db = SessionLocal()
    row = (
        db.query(FactsSnapshot)
        .filter(FactsSnapshot.project_id == project_id)
        .order_by(FactsSnapshot.id.desc())
        .first()
    )
    db.close()
    if not row:
        return None
    return {
        "id": row.id,
        "project_id": row.project_id,
        "project_code": row.project_code,
        "as_of": row.as_of,
        "facts_version": row.facts_version,
        "metrics": json.loads(row.metrics_json or "{}"),
        "requested_at": row.requested_at,
        "requested_by": row.requested_by,
    }


def list_snapshots(project_id: int, limit: int = 20) -> list[dict[str, Any]]:
    """读取项目最近的 N 个 Facts 快照列表。"""
    db = SessionLocal()
    rows = (
        db.query(FactsSnapshot)
        .filter(FactsSnapshot.project_id == project_id)
        .order_by(FactsSnapshot.id.desc())
        .limit(limit)
        .all()
    )
    db.close()
    return [
        {
            "id": r.id,
            "project_code": r.project_code,
            "as_of": r.as_of,
            "facts_version": r.facts_version,
            "requested_at": r.requested_at,
            "requested_by": r.requested_by,
            "require_fresh": r.require_fresh,
        }
        for r in rows
    ]


def to_decimal_safe(value: Any) -> Decimal:
    """把 Facts 指标值安全转换为 Decimal（用于系统内计算）。"""
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


__all__ = [
    "facts_cache",
    "get_project_facts",
    "invalidate_facts",
    "get_latest_snapshot",
    "list_snapshots",
    "to_decimal_safe",
]
