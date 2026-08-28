"""Canonical Facts provider.

This module is the only bridge from deterministic ``analytics_*`` views to
AI Review. It deliberately has no demo/default values: if the analytics
source is missing or incomplete, callers receive an explicit degraded
response and must not make a financial conclusion from it.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from threading import RLock
from typing import Any, Optional

from app.config import (
    FACTS_CACHE_EVENT_LIMIT,
    FACTS_CACHE_MAX_ENTRIES,
    FACTS_CACHE_TTL_SECONDS,
)
from app.domain.entities import is_canonical_entity_code, normalize_entity_code

FACTS_SOURCE = "analytics_project_full"
SNAPSHOT_SOURCE = "facts_snapshots"

# A row may expose any successfully read optional metrics, but
# ``facts_available`` stays false until this core set is complete.
REQUIRED_METRICS = frozenset(
    {
        "recognized_revenue",
        "real_profit",
        "collection_rate",
        "eac_profit",
        "eac_margin",
    }
)

# Historical snapshots use the same deterministic core boundary as current
# Facts. Optional metrics (cash-gap forecast, full tax burden, health score)
# must never make an otherwise valid deterministic snapshot unusable.
SNAPSHOT_REQUIRED_METRICS = REQUIRED_METRICS


def entity_mapping_failure(mapping: Mapping[str, Any]) -> str | None:
    """Return a fail-closed reason when a project is not canonically mapped.

    ``analytics_project_summary`` is intentionally the place that joins a
    project to the canonical ``entities`` master.  The provider still checks
    the resulting status because an old view, hand-written fixture, or stale
    snapshot must not be able to mark aggregate amounts as trusted merely by
    setting ``facts_available=true``.
    """

    raw_code = mapping.get("entity_code")
    code = normalize_entity_code(raw_code if raw_code is not None else None)
    if not code:
        return "entity mapping gap: project.entity_code is missing"
    if not is_canonical_entity_code(code):
        return (
            f"entity mapping gap: invalid entity_code={raw_code!r}; "
            "business roles A/B/C/D are not legal entity identifiers"
        )

    raw_status = mapping.get("entity_mapping_status")
    status = str(raw_status or "").strip().upper()
    if not status:
        return "entity mapping gap: analytics source has no entity_mapping_status"
    if status != "VALID":
        detail = str(mapping.get("entity_mapping_reason") or "").strip()
        suffix = f": {detail}" if detail else ""
        return f"entity mapping gap: status={status}{suffix}"

    # ``entity_mapping_valid`` was added alongside the status field.  Treat a
    # present false value as a hard failure, but remain compatible with a
    # valid status-only row produced by a transitional view.
    raw_valid = mapping.get("entity_mapping_valid")
    if isinstance(raw_valid, str):
        valid = raw_valid.strip().lower() in {"true", "t", "1", "yes"}
    elif raw_valid is None:
        valid = True
    else:
        valid = bool(raw_valid)
    if not valid:
        detail = str(mapping.get("entity_mapping_reason") or "").strip()
        suffix = f": {detail}" if detail else ""
        return f"entity mapping gap: entity_mapping_valid=false{suffix}"
    return None


def _optional_bool(value: Any) -> bool | None:
    """Normalize a nullable database/JSON boolean for response metadata."""

    if value is None:
        return None
    if isinstance(value, str):
        return value.strip().lower() in {"true", "t", "1", "yes"}
    return bool(value)


def _validate_metrics_completeness(metrics: dict) -> tuple[bool, list[str]]:
    """Return (complete, missing_keys) for snapshot validation.

    Checks that all required metrics are present and have non-None values.
    A snapshot with missing required metrics is downgraded to DEGRADED.
    """
    missing = [
        k
        for k in SNAPSHOT_REQUIRED_METRICS
        if k not in metrics or metrics[k] is None
    ]
    return len(missing) == 0, missing

# ``unit`` and metric version are part of the contract, while the value must
# come from the analytics row. In particular, a NULL is not converted to 0.
METRIC_DEFINITIONS: dict[str, tuple[str, str]] = {
    "contract_amount": ("CNY", "2.0"),
    "recognized_revenue": ("CNY", "1.3"),
    "real_project_cost": ("CNY", "1.0"),
    "real_profit": ("CNY", "2.1"),
    "collected_amount": ("CNY", "1.0"),
    "unpaid_amount": ("CNY", "1.0"),
    "collection_rate": ("ratio", "2.0"),
    "eac_revenue": ("CNY", "2.0"),
    "eac_cost": ("CNY", "2.0"),
    "eac_profit": ("CNY", "2.0"),
    "eac_margin": ("ratio", "2.0"),
    "cash_inflow": ("CNY", "2.0"),
    "cash_outflow": ("CNY", "2.0"),
    "net_cashflow": ("CNY", "2.0"),
    "cash_gap_30d": ("CNY", "2.0"),
    "output_vat": ("CNY", "2.0"),
    "input_vat": ("CNY", "2.0"),
    "vat_payable": ("CNY", "2.0"),
    "total_tax_burden": ("CNY", "2.0"),
    "tax_burden_rate": ("ratio", "2.0"),
    "cost_variance": ("CNY", "1.0"),
    "health_score": ("score", "1.0"),
}


def _json_safe_value(value: Any) -> Any:
    """Convert database numeric/date values into JSON-safe primitives."""

    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _iso_value(value: Any, *, default_now: bool = True) -> str:
    """Return an ISO string without inventing a business data timestamp."""

    if value is not None:
        converted = _json_safe_value(value)
        if isinstance(converted, str):
            return converted
    if default_now:
        return datetime.now(timezone.utc).isoformat()
    return ""


@dataclass
class MetricValue:
    """One deterministic metric returned by an analytics view."""

    value: float | int | Decimal
    metric_version: str
    unit: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize a metric without relying on dataclass internals."""

        return {
            "value": _json_safe_value(self.value),
            "metric_version": self.metric_version,
            "unit": self.unit,
        }


def _stable_facts_version(
    project_code: str,
    *,
    metrics: Optional[dict[str, MetricValue]] = None,
    mapping: Optional[Mapping[str, Any]] = None,
    facts_available: bool,
    reason: Optional[str] = None,
    source: str = FACTS_SOURCE,
) -> str:
    """Hash only stable, externally meaningful Facts content.

    Analytics views expose ``calculated_at=CURRENT_TIMESTAMP``. Including that
    timestamp would make a read look like a new fact version even when no
    source value changed. The version therefore covers the public metrics,
    metric contract versions, entity-mapping gate and EAC method, but excludes
    volatile query timestamps.
    """

    row = dict(mapping or {})
    metric_payload = {
        key: value.to_dict()
        for key, value in sorted((metrics or {}).items())
    }
    payload = {
        "project_code": project_code,
        "source": source,
        "facts_available": bool(facts_available),
        "reason": reason or None,
        "entity_code": normalize_entity_code(row.get("entity_code")),
        "entity_mapping_status": (
            str(row.get("entity_mapping_status")).strip().upper()
            if row.get("entity_mapping_status") is not None
            else None
        ),
        "entity_mapping_reason": (
            str(row.get("entity_mapping_reason")).strip()
            if row.get("entity_mapping_reason") is not None
            else None
        ),
        "entity_mapping_valid": _optional_bool(row.get("entity_mapping_valid")),
        "eac_method": str(row.get("eac_method") or ""),
        "metrics": metric_payload,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "f_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


@dataclass
class FactsResponse:
    """Canonical Facts response.

    ``status=DEGRADED`` and ``facts_available=False`` are intentional
    outcomes. They mean that the response cannot support a deterministic
    financial conclusion; ``metrics`` is empty for source-level failures and
    never exposes partial metrics to downstream consumers.
    """

    project_code: str
    as_of: str
    facts_version: str
    metrics: dict[str, MetricValue]
    status: str = "AVAILABLE"
    facts_available: bool = True
    reason: Optional[str] = None
    source: str = FACTS_SOURCE
    entity_code: Optional[str] = None
    entity_mapping_status: Optional[str] = None
    entity_mapping_reason: Optional[str] = None
    entity_mapping_valid: Optional[bool] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert the response to the public JSON contract."""

        return {
            "project_code": self.project_code,
            "as_of": self.as_of,
            "facts_version": self.facts_version,
            "status": self.status,
            "facts_available": self.facts_available,
            "reason": self.reason,
            "source": self.source,
            "entity_code": self.entity_code,
            "entity_mapping_status": self.entity_mapping_status,
            "entity_mapping_reason": self.entity_mapping_reason,
            "entity_mapping_valid": self.entity_mapping_valid,
            "metrics": {key: value.to_dict() for key, value in self.metrics.items()},
        }

    def to_json(self) -> str:
        """Convert the response to a UTF-8 JSON string."""

        return json.dumps(self.to_dict(), ensure_ascii=False)


class FactsCache:
    """Small in-memory TTL cache for complete Facts responses.

    The cache publishes four observable events through :attr:`events`:

    * ``facts_cache.hit``     – a non-expired entry was returned to the caller
    * ``facts_cache.miss``    – no entry was present for the key
    * ``facts_cache.expired`` – an entry existed but exceeded ``max_age_seconds``
    * ``facts_cache.invalidated`` – one or more entries were removed by an
      explicit ``invalidate`` call

    Consumers (log handlers, metrics emitters, audit sinks) can subscribe
    to these events without coupling to the cache implementation.  A
    :class:`FactsCacheSubscriber` helper is provided for the common case
    of collecting event counters in tests.
    """

    EVENT_HIT = "facts_cache.hit"
    EVENT_MISS = "facts_cache.miss"
    EVENT_EXPIRED = "facts_cache.expired"
    EVENT_INVALIDATED = "facts_cache.invalidated"
    EVENT_EVICTED = "facts_cache.evicted"

    def __init__(
        self,
        *,
        max_entries: int = FACTS_CACHE_MAX_ENTRIES,
        event_limit: int = FACTS_CACHE_EVENT_LIMIT,
    ):
        if max_entries <= 0:
            raise ValueError("max_entries must be > 0")
        if event_limit <= 0:
            raise ValueError("event_limit must be > 0")
        self.max_entries = max_entries
        self.event_limit = event_limit
        # OrderedDict gives deterministic oldest-entry eviction without an
        # unbounded in-memory cache.  The lock protects both data and event
        # history because Facts is read by HTTP workers and invalidated by
        # separate admin requests.
        self._cache: OrderedDict[str, tuple[FactsResponse, datetime]] = OrderedDict()
        self.events: list[tuple[str, str]] = []
        self._lock = RLock()

    def _record_event(self, event: str, key: str) -> None:
        self.events.append((event, key))
        if len(self.events) > self.event_limit:
            del self.events[: len(self.events) - self.event_limit]

    def get(
        self,
        key: str,
        max_age_seconds: int = FACTS_CACHE_TTL_SECONDS,
    ) -> Optional[FactsResponse]:
        """Get a non-expired response, otherwise return ``None``."""

        if max_age_seconds < 0:
            raise ValueError("max_age_seconds must be >= 0")
        with self._lock:
            if key not in self._cache:
                self._record_event(self.EVENT_MISS, key)
                return None

            response, cached_at = self._cache[key]
            age = (datetime.now(timezone.utc) - cached_at).total_seconds()
            if age >= max_age_seconds:
                del self._cache[key]
                self._record_event(self.EVENT_EXPIRED, key)
                return None
            self._cache.move_to_end(key)
            self._record_event(self.EVENT_HIT, key)
            return response

    def set(self, key: str, response: FactsResponse) -> None:
        """Cache only an actually available response."""

        if not response.facts_available:
            return
        with self._lock:
            self._cache[key] = (response, datetime.now(timezone.utc))
            self._cache.move_to_end(key)
            while len(self._cache) > self.max_entries:
                evicted_key, _ = self._cache.popitem(last=False)
                self._record_event(self.EVENT_EVICTED, evicted_key)

    def invalidate(self, key: str) -> bool:
        """Invalidate one response."""

        with self._lock:
            if self._cache.pop(key, None) is not None:
                self._record_event(self.EVENT_INVALIDATED, key)
                return True
        return False

    def invalidate_prefix(self, prefix: str) -> None:
        """Invalidate all responses whose keys have ``prefix``."""

        with self._lock:
            for key in [key for key in self._cache if key.startswith(prefix)]:
                self._cache.pop(key, None)
                self._record_event(self.EVENT_INVALIDATED, key)

    def clear_events(self) -> None:
        """Discard the recorded event list (mostly for tests)."""
        with self._lock:
            self.events.clear()

    def clear(self) -> None:
        """Clear entries and event history, useful during controlled reloads."""
        with self._lock:
            self._cache.clear()
            self.events.clear()

    @property
    def size(self) -> int:
        """Current number of cached responses."""
        with self._lock:
            return len(self._cache)


class FactsCacheSubscriber:
    """Tiny subscriber that tallies events from a :class:`FactsCache`.

    The dataclass-style accessor surfaces the canonical counters used by
    production health endpoints and tests alike.
    """

    def __init__(self, cache: FactsCache):
        self._cache = cache

    @property
    def hit(self) -> int:
        return sum(1 for event, _ in self._cache.events if event == FactsCache.EVENT_HIT)

    @property
    def miss(self) -> int:
        return sum(1 for event, _ in self._cache.events if event == FactsCache.EVENT_MISS)

    @property
    def expired(self) -> int:
        return sum(1 for event, _ in self._cache.events if event == FactsCache.EVENT_EXPIRED)

    @property
    def invalidated(self) -> int:
        return sum(
            1 for event, _ in self._cache.events if event == FactsCache.EVENT_INVALIDATED
        )

    @property
    def evicted(self) -> int:
        return sum(1 for event, _ in self._cache.events if event == FactsCache.EVENT_EVICTED)

    def summary(self) -> dict[str, int]:
        return {
            "hit": self.hit,
            "miss": self.miss,
            "expired": self.expired,
            "invalidated": self.invalidated,
            "evicted": self.evicted,
            "size": self._cache.size,
        }


def _degraded_response(
    project_code: str,
    reason: str,
    *,
    as_of: Optional[str] = None,
    source: str = FACTS_SOURCE,
    metrics: Optional[dict[str, MetricValue]] = None,
    facts_version: Optional[str] = None,
    entity_code: Optional[str] = None,
    entity_mapping_status: Optional[str] = None,
    entity_mapping_reason: Optional[str] = None,
    entity_mapping_valid: Optional[bool] = None,
) -> FactsResponse:
    """Create an explicit unavailable response with no fabricated values."""

    stable_mapping = {
        "entity_code": entity_code,
        "entity_mapping_status": entity_mapping_status,
        "entity_mapping_reason": entity_mapping_reason,
        "entity_mapping_valid": entity_mapping_valid,
    }
    return FactsResponse(
        project_code=project_code,
        as_of=as_of or datetime.now(timezone.utc).isoformat(),
        facts_version=facts_version or _stable_facts_version(
            project_code,
            metrics=metrics,
            mapping=stable_mapping,
            facts_available=False,
            reason=reason,
            source=source,
        ),
        metrics=metrics or {},
        status="DEGRADED",
        facts_available=False,
        reason=reason,
        source=source,
        entity_code=entity_code,
        entity_mapping_status=entity_mapping_status,
        entity_mapping_reason=entity_mapping_reason,
        entity_mapping_valid=entity_mapping_valid,
    )


_DEFAULT_FACTS_CACHE = FactsCache()


class FactsProvider:
    """Read canonical facts from ``analytics_project_full`` and snapshots."""

    def __init__(self, db_session, cache: FactsCache | None = None):
        self._db = db_session
        # The cache contains only immutable/short-lived response data, never
        # a SQLAlchemy session.  Sharing it across request-scoped providers
        # makes the invalidate endpoint effective without retaining a closed
        # DB session.
        self._cache = cache or _DEFAULT_FACTS_CACHE
        # A process may serve more than one database during tests, migrations,
        # or a controlled tenant switch. Namespace keys by the bound engine
        # so a response from one database cannot leak into another one.
        bind = getattr(db_session, "bind", None)
        self._cache_namespace = f"{id(bind)}:" if bind is not None else "default:"
        self._metric_versions = self._load_metric_versions()

    def _cache_key(self, project_code: str) -> str:
        return f"{self._cache_namespace}facts:{project_code}"

    def _load_metric_versions(self) -> dict[str, str]:
        """Use the metric contract itself as the single version source."""
        return {key: version for key, (_unit, version) in METRIC_DEFINITIONS.items()}


    def get_facts(
        self,
        project_code: str,
        require_fresh: bool = False,
        max_age: int = FACTS_CACHE_TTL_SECONDS,
        as_of: Optional[str] = None,
    ) -> FactsResponse:
        """Get current or historical canonical Facts.

        Unavailable data is returned explicitly. Degraded responses are not
        cached, so a temporary source outage cannot mask a later recovery.
        """

        cache_key = self._cache_key(project_code)
        if as_of:
            return self._get_historical_facts(project_code, as_of)

        if not require_fresh:
            cached = self._cache.get(cache_key, max_age)
            if cached:
                return cached

        facts = self._compute_facts(project_code)
        self._cache.set(cache_key, facts)
        return facts

    @staticmethod
    def _row_mapping(row: Any) -> dict[str, Any]:
        """Get a mapping from SQLAlchemy rows and simple test doubles."""

        mapping = getattr(row, "_mapping", row)
        return dict(mapping)

    def _metrics_from_row(
        self,
        row: Any,
    ) -> tuple[dict[str, MetricValue], set[str], set[str]]:
        """Read only columns actually present and non-NULL in an analytics row."""

        mapping = self._row_mapping(row)
        metrics: dict[str, MetricValue] = {}
        invalid: set[str] = set()

        for key, (unit, default_version) in METRIC_DEFINITIONS.items():
            if key not in mapping or mapping[key] is None:
                continue
            raw = mapping[key]
            try:
                # Do not coerce booleans or arbitrary text into a financial
                # value. Decimal/int/float are the expected DB results.
                if isinstance(raw, bool):
                    raise TypeError("boolean is not a metric value")
                value = float(raw)
                if not math.isfinite(value):
                    raise ValueError("metric is not finite")
            except (TypeError, ValueError, OverflowError):
                invalid.add(key)
                continue
            metrics[key] = MetricValue(
                value=value,
                metric_version=self._metric_versions.get(key, default_version),
                unit=unit,
            )

        missing = set(REQUIRED_METRICS) - set(metrics)
        return metrics, missing, invalid

    def _compute_facts(self, project_code: str) -> FactsResponse:
        """Read one project row from the deterministic analytics view.

        No exception path supplies a default amount. A missing view, missing
        project, NULL metric or invalid metric is represented as degraded.
        """

        try:
            from sqlalchemy import text

            row = self._db.execute(
                text(
                    "SELECT * FROM analytics_project_full "
                    "WHERE project_code = :pc LIMIT 1"
                ),
                {"pc": project_code},
            ).first()
        except Exception as exc:  # database driver errors are a source outage
            return _degraded_response(
                project_code,
                f"{FACTS_SOURCE} unavailable: {exc.__class__.__name__}",
            )

        if row is None:
            return _degraded_response(
                project_code,
                f"no canonical facts for project_code={project_code}",
            )

        mapping = self._row_mapping(row)
        mapping_failure = entity_mapping_failure(mapping)
        metrics, missing, invalid = self._metrics_from_row(row)
        reasons: list[str] = []
        if mapping_failure:
            reasons.append(mapping_failure)
        source_flag = mapping.get("facts_available")
        source_incomplete = source_flag is False or source_flag == 0 or (
            isinstance(source_flag, str) and source_flag.strip().lower() in {"false", "0", "no"}
        )
        if source_incomplete:
            reasons.append("analytics source marked facts_available=false")
        if missing:
            reasons.append("missing metrics: " + ", ".join(sorted(missing)))
        if invalid:
            reasons.append("invalid metrics: " + ", ".join(sorted(invalid)))
        available = not mapping_failure and not source_incomplete and not missing and not invalid
        reason = "; ".join(reasons) if reasons else None
        as_of = _iso_value(mapping.get("calculated_at") or mapping.get("data_date"))
        facts_version = _stable_facts_version(
            project_code,
            metrics=metrics,
            mapping=mapping,
            facts_available=available,
            reason=reason,
            source=FACTS_SOURCE,
        )

        return FactsResponse(
            project_code=project_code,
            as_of=as_of,
            facts_version=facts_version,
            metrics=metrics if available else {},
            status="AVAILABLE" if available else "DEGRADED",
            facts_available=available,
            reason=reason,
            source=FACTS_SOURCE,
            entity_code=normalize_entity_code(mapping.get("entity_code")),
            entity_mapping_status=(
                str(mapping.get("entity_mapping_status")).strip().upper()
                if mapping.get("entity_mapping_status") is not None
                else None
            ),
            entity_mapping_reason=(
                str(mapping.get("entity_mapping_reason")).strip()
                if mapping.get("entity_mapping_reason") is not None
                else None
            ),
            entity_mapping_valid=_optional_bool(mapping.get("entity_mapping_valid")),
        )

    @staticmethod
    def _decode_snapshot_data(raw: Any) -> dict[str, Any]:
        """Decode JSON/JSONB snapshot payloads without trusting arbitrary data."""

        if isinstance(raw, str):
            raw = json.loads(raw)
        if not isinstance(raw, dict):
            raise ValueError("facts_data is not an object")
        return raw

    def _get_historical_facts(self, project_code: str, as_of: str) -> FactsResponse:
        """Read the latest stored snapshot at or before ``as_of``."""

        try:
            from sqlalchemy import text

            row = self._db.execute(
                text(
                    "SELECT facts_data, as_of, facts_version "
                    "FROM facts_snapshots "
                    "WHERE project_code = :pc AND as_of <= :as_of "
                    "ORDER BY as_of DESC LIMIT 1"
                ),
                {"pc": project_code, "as_of": as_of},
            ).first()
        except Exception as exc:
            return _degraded_response(
                project_code,
                f"{SNAPSHOT_SOURCE} unavailable: {exc.__class__.__name__}",
                as_of=as_of,
                source=SNAPSHOT_SOURCE,
            )

        if row is None:
            return _degraded_response(
                project_code,
                f"no Facts snapshot at or before as_of={as_of}",
                as_of=as_of,
                source=SNAPSHOT_SOURCE,
            )

        mapping = self._row_mapping(row)
        try:
            payload = self._decode_snapshot_data(mapping.get("facts_data"))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return _degraded_response(
                project_code,
                f"invalid Facts snapshot: {exc}",
                as_of=_iso_value(mapping.get("as_of"), default_now=False) or as_of,
                source=SNAPSHOT_SOURCE,
                facts_version=str(mapping.get("facts_version") or ""),
            )

        mapping_failure = entity_mapping_failure(payload)
        if mapping_failure:
            return _degraded_response(
                project_code,
                mapping_failure,
                as_of=_iso_value(mapping.get("as_of"), default_now=False) or as_of,
                source=SNAPSHOT_SOURCE,
                facts_version=str(mapping.get("facts_version") or payload.get("facts_version") or ""),
                entity_code=normalize_entity_code(payload.get("entity_code")),
                entity_mapping_status=(
                    str(payload.get("entity_mapping_status")).strip().upper()
                    if payload.get("entity_mapping_status") is not None
                    else None
                ),
                entity_mapping_reason=(
                    str(payload.get("entity_mapping_reason")).strip()
                    if payload.get("entity_mapping_reason") is not None
                    else None
                ),
                entity_mapping_valid=_optional_bool(payload.get("entity_mapping_valid")),
            )

        raw_metrics = payload.get("metrics", {})
        if not isinstance(raw_metrics, dict):
            return _degraded_response(
                project_code,
                "invalid Facts snapshot: metrics is not an object",
                as_of=_iso_value(mapping.get("as_of"), default_now=False) or as_of,
                source=SNAPSHOT_SOURCE,
                facts_version=str(mapping.get("facts_version") or ""),
            )

        metrics: dict[str, MetricValue] = {}
        for key, raw in raw_metrics.items():
            if not isinstance(raw, dict) or raw.get("value") is None:
                continue
            try:
                value = float(raw["value"])
                if not math.isfinite(value):
                    raise ValueError("metric is not finite")
            except (TypeError, ValueError, OverflowError):
                continue
            unit, default_version = METRIC_DEFINITIONS.get(key, ("", ""))
            metrics[key] = MetricValue(
                value=value,
                metric_version=str(
                    raw.get("metric_version")
                    or self._metric_versions.get(key, default_version)
                ),
                unit=str(raw.get("unit") or unit),
            )

        # Validate snapshot completeness before marking as AVAILABLE
        snapshot_available = payload.get("facts_available", bool(metrics))
        metrics_complete, missing_metrics = _validate_metrics_completeness(metrics)

        available = bool(snapshot_available) and bool(metrics) and metrics_complete
        reason = payload.get("reason") if not available else None
        if not available:
            if missing_metrics:
                reason = f"incomplete Facts snapshot: missing metrics: {', '.join(sorted(missing_metrics))}"
            elif not bool(metrics):
                reason = "stored Facts snapshot has no metrics"
            elif not snapshot_available:
                reason = "stored Facts snapshot marked unavailable"
            else:
                reason = "stored Facts snapshot is unavailable or incomplete"

        return FactsResponse(
            project_code=project_code,
            as_of=_iso_value(mapping.get("as_of"), default_now=False) or as_of,
            facts_version=str(mapping.get("facts_version") or payload.get("facts_version") or ""),
            metrics=metrics if available else {},
            status="AVAILABLE" if available else "DEGRADED",
            facts_available=available,
            reason=reason,
            source=SNAPSHOT_SOURCE,
            entity_code=normalize_entity_code(payload.get("entity_code")),
            entity_mapping_status=(
                str(payload.get("entity_mapping_status")).strip().upper()
                if payload.get("entity_mapping_status") is not None
                else None
            ),
            entity_mapping_reason=(
                str(payload.get("entity_mapping_reason")).strip()
                if payload.get("entity_mapping_reason") is not None
                else None
            ),
            entity_mapping_valid=_optional_bool(payload.get("entity_mapping_valid")),
        )

    def get_history(self, project_code: str, limit: int = 10) -> dict[str, Any]:
        """Return stored snapshot metadata, or an explicit empty history."""

        try:
            from sqlalchemy import text

            rows = self._db.execute(
                text(
                    "SELECT facts_data, as_of, facts_version, created_at "
                    "FROM facts_snapshots "
                    "WHERE project_code = :pc "
                    "ORDER BY created_at DESC LIMIT :limit"
                ),
                {"pc": project_code, "limit": limit},
            ).mappings().all()
        except Exception as exc:
            return {
                "project_code": project_code,
                "status": "DEGRADED",
                "facts_available": False,
                "reason": f"{SNAPSHOT_SOURCE} unavailable: {exc.__class__.__name__}",
                "history": [],
            }

        history: list[dict[str, Any]] = []
        for row in rows:
            available = False
            reason = None
            try:
                payload = self._decode_snapshot_data(row.get("facts_data"))
                mapping_failure = entity_mapping_failure(payload)
                raw_metrics = payload.get("metrics", {})
                metrics_complete, missing = _validate_metrics_completeness(raw_metrics)
                base_available = bool(payload.get("facts_available", bool(raw_metrics)))
                available = not mapping_failure and base_available and metrics_complete
                if mapping_failure:
                    reason = mapping_failure
                elif not available and missing:
                    reason = f"missing metrics: {', '.join(sorted(missing))}"
            except (TypeError, ValueError, json.JSONDecodeError):
                reason = "invalid snapshot data"
            history.append(
                {
                    "facts_version": row.get("facts_version"),
                    "as_of": _iso_value(row.get("as_of"), default_now=False),
                    "created_at": _iso_value(row.get("created_at"), default_now=False),
                    "facts_available": available,
                    "reason": reason,
                }
            )

        has_available = any(item["facts_available"] for item in history)
        return {
            "project_code": project_code,
            "status": "AVAILABLE" if has_available else "DEGRADED",
            "facts_available": has_available,
            "reason": None if has_available else (
                "no available Facts snapshots found" if history else "no Facts snapshots found"
            ),
            "history": history,
        }

    def invalidate(self, project_code: str) -> bool:
        """Invalidate one project Facts cache entry."""

        return self._cache.invalidate(self._cache_key(project_code))

    def invalidate_entity(self, entity_code: str) -> int:
        """Invalidate cached projects belonging to a canonical entity."""

        try:
            from sqlalchemy import text

            rows = self._db.execute(
                text(
                    "SELECT project_code FROM analytics_project_summary "
                    "WHERE entity_code = :entity_code"
                ),
                {"entity_code": entity_code},
            ).scalars().all()
        except Exception:
            # Cache invalidation cannot fabricate project identifiers. If the
            # analytics dimension is unavailable, report zero invalidations.
            return 0

        for project_code in rows:
            self.invalidate(project_code)
        return len(rows)


def get_facts_provider(db_session) -> FactsProvider:
    """Build a provider bound to the caller's live SQLAlchemy session.

    A module-global provider used to retain a closed request session. A
    request-scoped provider is safe and still retains the short-lived cache
    for callers that reuse the instance directly.
    """

    return FactsProvider(db_session)
