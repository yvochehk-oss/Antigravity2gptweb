"""Task21 writer cutover / shadow-write orchestration.

Phase-1 invariants:
* Legacy official write commits first and remains authoritative.
* Shadow V3 projection runs only after legacy success.
* Shadow failure is isolated in a SAVEPOINT and never rolls back legacy success.
* RAG_SOURCE stays LEGACY.
* No automatic transition to DUAL_WRITE or V3_PRIMARY.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any, Callable, Mapping

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.v3_cutover_models import ReviewDiffQueue, ShadowWriteDiff, WriterCutoverState

GLOBAL_SCOPE = "GLOBAL"
SHADOW_ERROR_CODE = "V3_SHADOW_WRITE_FAILED"


class CutoverError(RuntimeError):
    pass


class WriterMode(StrEnum):
    SHADOW = "SHADOW"
    DUAL_WRITE = "DUAL_WRITE"
    V3_PRIMARY = "V3_PRIMARY"


@dataclass(frozen=True)
class ShadowProjection:
    payload: Mapping[str, Any]
    canonical_fact_id: int | None = None


@dataclass(frozen=True)
class ShadowOutcome:
    operation_key: str
    result: str
    diff_id: int
    canonical_fact_id: int | None


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _json_value(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    return value


def normalize_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return _json_value(dict(payload))


def payload_diff(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    a = normalize_payload(left)
    b = normalize_payload(right)
    return {
        key: {"legacy": a.get(key), "canonical": b.get(key)}
        for key in sorted(set(a) | set(b))
        if a.get(key) != b.get(key)
    }


def get_cutover_state(db: Session, *, scope: str = GLOBAL_SCOPE, for_update: bool = False) -> WriterCutoverState:
    stmt = select(WriterCutoverState).where(WriterCutoverState.scope == scope)
    if for_update:
        stmt = stmt.with_for_update()
    state = db.execute(stmt).scalar_one_or_none()
    if state is None:
        raise CutoverError(f"writer cutover state missing for scope={scope}")
    return state


def require_shadow_phase(db: Session) -> WriterCutoverState:
    state = get_cutover_state(db)
    if not (
        state.writer_mode == WriterMode.SHADOW.value
        and state.legacy_write_enabled is True
        and state.new_fact_write_enabled is False
        and state.rag_source == "LEGACY"
        and state.new_fact_read_mode == "SHADOW"
        and state.legacy_frozen is False
    ):
        raise CutoverError("Task21 shadow writer requires the exact Phase-1 cutover state")
    return state


def record_shadow_projection(
    db: Session,
    *,
    operation_key: str,
    object_type: str,
    operation: str,
    legacy_payload: Mapping[str, Any],
    shadow_writer: Callable[[Session], ShadowProjection],
    legacy_object_id: str | int | None = None,
    simulation_fixture: bool = False,
) -> ShadowOutcome:
    """Run one post-commit shadow projection and persist deterministic evidence.

    The caller must already have committed the official legacy write. This
    function never performs or rolls back that official write.
    """
    require_shadow_phase(db)
    existing = db.execute(
        select(ShadowWriteDiff).where(ShadowWriteDiff.operation_key == operation_key)
    ).scalar_one_or_none()
    if existing is not None:
        return ShadowOutcome(existing.operation_key, existing.result, existing.id, existing.canonical_fact_id)

    normalized_legacy = normalize_payload(legacy_payload)
    projection: ShadowProjection | None = None
    error_message: str | None = None
    try:
        with db.begin_nested():
            projection = shadow_writer(db)
    except Exception as exc:
        error_message = f"{type(exc).__name__}: {exc}"

    now = datetime.now(timezone.utc)
    if projection is None:
        row = ShadowWriteDiff(
            operation_key=operation_key,
            object_type=object_type,
            operation=operation,
            legacy_object_id=None if legacy_object_id is None else str(legacy_object_id),
            canonical_fact_id=None,
            legacy_payload=normalized_legacy,
            canonical_payload=None,
            normalized_diff={},
            result="ERROR",
            error_code=SHADOW_ERROR_CODE,
            error_message=error_message or SHADOW_ERROR_CODE,
            review_status="OPEN",
            simulation_fixture=simulation_fixture,
        )
    else:
        normalized_canonical = normalize_payload(projection.payload)
        diff = payload_diff(normalized_legacy, normalized_canonical)
        is_match = not diff
        row = ShadowWriteDiff(
            operation_key=operation_key,
            object_type=object_type,
            operation=operation,
            legacy_object_id=None if legacy_object_id is None else str(legacy_object_id),
            canonical_fact_id=projection.canonical_fact_id,
            legacy_payload=normalized_legacy,
            canonical_payload=normalized_canonical,
            normalized_diff=diff,
            result="MATCH" if is_match else "MISMATCH",
            error_code=None,
            error_message=None,
            review_status="RESOLVED" if is_match else "OPEN",
            simulation_fixture=simulation_fixture,
            resolved_by="system:shadow-compare" if is_match else None,
            resolved_at=now if is_match else None,
        )

    db.add(row)
    db.flush()
    db.commit()
    return ShadowOutcome(row.operation_key, row.result, row.id, row.canonical_fact_id)


def unresolved_production_diff_count(db: Session) -> int:
    return int(
        db.scalar(
            select(func.count(ShadowWriteDiff.id)).where(
                ShadowWriteDiff.simulation_fixture.is_(False),
                ShadowWriteDiff.result.in_(("MISMATCH", "ERROR")),
                ShadowWriteDiff.review_status == "OPEN",
            )
        ) or 0
    )


def transition_to_dual_write(db: Session, *, actor: str) -> WriterCutoverState:
    """Explicit operator transition; never invoked automatically."""
    state = get_cutover_state(db, for_update=True)
    if state.writer_mode != WriterMode.SHADOW.value:
        raise CutoverError("DUAL_WRITE transition requires current SHADOW mode")
    blockers = unresolved_production_diff_count(db)
    if blockers:
        raise CutoverError(f"DUAL_WRITE blocked by {blockers} unresolved production shadow diffs")
    state.writer_mode = WriterMode.DUAL_WRITE.value
    state.legacy_write_enabled = True
    state.new_fact_write_enabled = True
    state.rag_source = "LEGACY"
    state.new_fact_read_mode = "SHADOW"
    state.legacy_frozen = False
    state.updated_by = actor
    state.updated_at = datetime.now(timezone.utc)
    db.commit()
    return state


def transition_to_v3_primary(db: Session, *, actor: str) -> WriterCutoverState:
    """Controlled legacy-write freeze. Task21 never calls this automatically."""
    state = get_cutover_state(db, for_update=True)
    if state.writer_mode != WriterMode.DUAL_WRITE.value:
        raise CutoverError("V3_PRIMARY transition requires DUAL_WRITE mode")
    blockers = unresolved_production_diff_count(db)
    if blockers:
        raise CutoverError(f"V3_PRIMARY blocked by {blockers} unresolved production shadow diffs")
    state.writer_mode = WriterMode.V3_PRIMARY.value
    state.legacy_write_enabled = False
    state.new_fact_write_enabled = True
    state.legacy_frozen = True
    # Task22 owns reader/RAG cutover; do not silently alter those fields here.
    state.updated_by = actor
    state.updated_at = datetime.now(timezone.utc)
    db.commit()
    return state


def resolve_diff(db: Session, *, diff_id: int, actor: str, status: str = "RESOLVED") -> None:
    if status not in {"RESOLVED", "IGNORED"}:
        raise CutoverError("review resolution must be RESOLVED or IGNORED")
    diff = db.get(ShadowWriteDiff, diff_id)
    if diff is None:
        raise CutoverError(f"shadow diff {diff_id} not found")
    queue = db.execute(
        select(ReviewDiffQueue).where(ReviewDiffQueue.shadow_diff_id == diff_id)
    ).scalar_one_or_none()
    if queue is None and diff.result in {"MISMATCH", "ERROR"}:
        raise CutoverError("review queue row missing for reviewable shadow diff")
    now = datetime.now(timezone.utc)
    diff.review_status = status
    diff.resolved_by = actor
    diff.resolved_at = now
    if queue is not None:
        queue.status = status
        queue.reviewed_by = actor
        queue.reviewed_at = now
    db.commit()
