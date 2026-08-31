"""Task23 V3 production-seal orchestration.

The seal is explicit, one-way, and evidence-backed. It never deletes legacy
business data. Once present, database guards make routing immutable.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.cutover.writer import CutoverError, GLOBAL_SCOPE, ShadowWriteDiff, get_cutover_state
from app.v3_cutover_finalization_models import V3CutoverFinalization


@dataclass(frozen=True)
class ProductionSealEvidence:
    unresolved_production_shadow_diffs: int
    review_queue_orphan_count: int
    canonical_fact_orphan_count: int
    invalid_confirmed_allocation_fact_count: int

    @property
    def clean(self) -> bool:
        return all(value == 0 for value in self.as_dict().values())

    def as_dict(self) -> dict[str, int]:
        return {
            "unresolved_production_shadow_diffs": self.unresolved_production_shadow_diffs,
            "review_queue_orphan_count": self.review_queue_orphan_count,
            "canonical_fact_orphan_count": self.canonical_fact_orphan_count,
            "invalid_confirmed_allocation_fact_count": self.invalid_confirmed_allocation_fact_count,
        }


def is_cutover_finalized(db: Session, *, scope: str = GLOBAL_SCOPE) -> bool:
    return db.get(V3CutoverFinalization, scope) is not None


def collect_production_seal_evidence(db: Session) -> ProductionSealEvidence:
    unresolved = int(db.scalar(select(func.count(ShadowWriteDiff.id)).where(
        ShadowWriteDiff.simulation_fixture.is_(False),
        ShadowWriteDiff.result.in_(("MISMATCH", "ERROR")),
        ShadowWriteDiff.review_status == "OPEN",
    )) or 0)
    review_orphans = int(db.execute(text("""
        SELECT count(*) FROM review_diff_queue q
        LEFT JOIN shadow_write_diffs d ON d.id=q.shadow_diff_id WHERE d.id IS NULL
    """)).scalar_one())
    fact_orphans = int(db.execute(text("""
        SELECT count(*) FROM shadow_write_diffs d LEFT JOIN facts f ON f.id=d.canonical_fact_id
        WHERE d.canonical_fact_id IS NOT NULL AND f.id IS NULL
    """)).scalar_one())
    invalid_allocations = int(db.execute(text("""
        SELECT count(*) FROM fact_project_allocations a LEFT JOIN facts f ON f.id=a.fact_id
        WHERE a.is_current IS TRUE AND a.status='CONFIRMED'
          AND (f.id IS NULL OR f.is_current IS NOT TRUE OR f.validation_status<>'VALID')
    """)).scalar_one())
    return ProductionSealEvidence(unresolved, review_orphans, fact_orphans, invalid_allocations)


def _strict_final_state(state: Any) -> bool:
    return (
        state.writer_mode == "V3_PRIMARY"
        and state.legacy_write_enabled is False
        and state.new_fact_write_enabled is True
        and state.legacy_frozen is True
        and state.new_fact_read_mode == "PRIMARY"
        and state.rag_source == "CANONICAL_FACTS"
    )


def finalize_v3_production_cutover(
    db: Session,
    *,
    actor: str,
    scope: str = GLOBAL_SCOPE,
    commit: bool = True,
) -> V3CutoverFinalization:
    """Write the one-way production seal after all Task21/22 safety gates pass."""
    if not actor.strip():
        raise CutoverError("production seal requires a non-empty actor")
    existing = db.get(V3CutoverFinalization, scope)
    if existing is not None:
        return existing
    state = get_cutover_state(db, scope=scope, for_update=True)
    if not _strict_final_state(state):
        raise CutoverError("production seal requires strict V3_PRIMARY + PRIMARY + CANONICAL_FACTS state")
    evidence = collect_production_seal_evidence(db)
    if not evidence.clean:
        raise CutoverError(f"production seal blocked by integrity evidence: {evidence.as_dict()}")
    snapshot = {
        "writer_mode": state.writer_mode,
        "legacy_write_enabled": state.legacy_write_enabled,
        "new_fact_write_enabled": state.new_fact_write_enabled,
        "legacy_frozen": state.legacy_frozen,
        "new_fact_read_mode": state.new_fact_read_mode,
        "rag_source": state.rag_source,
        "state_updated_by": state.updated_by,
        "state_updated_at": state.updated_at.isoformat() if state.updated_at else None,
    }
    seal = V3CutoverFinalization(
        scope=scope,
        finalized_by=actor.strip(),
        finalized_at=datetime.now(timezone.utc),
        state_snapshot=snapshot,
        evidence_snapshot=evidence.as_dict(),
    )
    db.add(seal)
    if commit:
        db.commit()
    else:
        db.flush()
    return seal
