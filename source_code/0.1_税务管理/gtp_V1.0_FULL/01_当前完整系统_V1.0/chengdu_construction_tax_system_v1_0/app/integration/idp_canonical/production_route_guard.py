"""Task27 read-only guard for the immutable V3 production route.

This module intentionally exposes no transition/finalization operation.  It
only SELECTs the GLOBAL cutover state and GLOBAL Production Seal and validates
that both represent the exact already-approved production posture.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.v3_cutover_finalization_models import V3CutoverFinalization
from app.v3_cutover_models import WriterCutoverState

from .direct_v3_schemas import ProductionRouteState


GLOBAL_SCOPE = "GLOBAL"

EXPECTED_ROUTE: dict[str, Any] = {
    "writer_mode": "V3_PRIMARY",
    "legacy_write_enabled": False,
    "new_fact_write_enabled": True,
    "legacy_frozen": True,
    "new_fact_read_mode": "PRIMARY",
    "rag_source": "CANONICAL_FACTS",
}


class ProductionRouteGuardError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class RouteValidation:
    live: dict[str, Any]
    sealed: dict[str, Any]


def _route_values(source: Any) -> dict[str, Any]:
    if isinstance(source, Mapping):
        return {
            key: source.get(key)
            for key in EXPECTED_ROUTE
        }
    return {
        key: getattr(source, key, None)
        for key in EXPECTED_ROUTE
    }


def validate_route_values(
    live: Any,
    sealed: Mapping[str, Any],
) -> RouteValidation:
    """Pure fail-closed validator used by both the runtime guard and tests."""

    live_values = _route_values(live)
    sealed_values = _route_values(sealed)

    live_mismatches = {
        key: {"expected": expected, "actual": live_values.get(key)}
        for key, expected in EXPECTED_ROUTE.items()
        if live_values.get(key) != expected
    }
    if live_mismatches:
        raise ProductionRouteGuardError(
            "PRODUCTION_ROUTE_STATE_MISMATCH",
            f"GLOBAL live cutover state is not strict V3 production state: {live_mismatches}",
        )

    seal_mismatches = {
        key: {"expected": expected, "actual": sealed_values.get(key)}
        for key, expected in EXPECTED_ROUTE.items()
        if sealed_values.get(key) != expected
    }
    if seal_mismatches:
        raise ProductionRouteGuardError(
            "PRODUCTION_SEAL_STATE_MISMATCH",
            f"GLOBAL Production Seal snapshot is not strict V3 production state: {seal_mismatches}",
        )

    drift = {
        key: {"live": live_values.get(key), "sealed": sealed_values.get(key)}
        for key in EXPECTED_ROUTE
        if live_values.get(key) != sealed_values.get(key)
    }
    if drift:
        raise ProductionRouteGuardError(
            "PRODUCTION_ROUTE_SEAL_DRIFT",
            f"GLOBAL live route differs from immutable Production Seal snapshot: {drift}",
        )

    return RouteValidation(live=live_values, sealed=sealed_values)


class ProductionRouteGuard:
    """Read-only verification of GLOBAL Production Seal + cutover routing."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def require(self, *, scope: str = GLOBAL_SCOPE) -> ProductionRouteState:
        if scope != GLOBAL_SCOPE:
            raise ProductionRouteGuardError(
                "PRODUCTION_SCOPE_UNSUPPORTED",
                "Task27 production orchestration is sealed to GLOBAL scope only",
            )

        # Deliberately no FOR UPDATE and no mutation.  Production Seal is only read.
        state = self.db.execute(
            select(WriterCutoverState).where(WriterCutoverState.scope == scope)
        ).scalar_one_or_none()
        if state is None:
            raise ProductionRouteGuardError(
                "PRODUCTION_CUTOVER_STATE_MISSING",
                "GLOBAL writer cutover state is missing",
            )

        seal = self.db.execute(
            select(V3CutoverFinalization).where(V3CutoverFinalization.scope == scope)
        ).scalar_one_or_none()
        if seal is None:
            raise ProductionRouteGuardError(
                "PRODUCTION_SEAL_MISSING",
                "GLOBAL Production Seal is missing",
            )

        if not isinstance(seal.state_snapshot, Mapping):
            raise ProductionRouteGuardError(
                "PRODUCTION_SEAL_INVALID",
                "GLOBAL Production Seal state_snapshot is not an object",
            )

        validation = validate_route_values(state, seal.state_snapshot)

        actor = str(seal.finalized_by).strip()
        if not actor:
            raise ProductionRouteGuardError(
                "PRODUCTION_SEAL_INVALID",
                "GLOBAL Production Seal finalized_by is empty",
            )
        if seal.finalized_at is None:
            raise ProductionRouteGuardError(
                "PRODUCTION_SEAL_INVALID",
                "GLOBAL Production Seal finalized_at is missing",
            )

        return ProductionRouteState(
            scope="GLOBAL",
            **validation.live,
            seal_finalized_by=actor,
            seal_finalized_at=seal.finalized_at,
        )
