"""Task32 Native V3 Web/Boss API.

The router contains no finance/tax/relationship calculations and no legacy
business queries.  All semantics are delegated to approved V3 services.
"""
from __future__ import annotations

from datetime import date
from typing import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.integration.idp_canonical.direct_v3_schemas import (
    DirectV3ProductionRequest,
    DirectV3ProductionResult,
)
from app.integration.idp_canonical.direct_v3_service import (
    DirectV3IngestService,
    DirectV3ProductionError,
)
from app.integration.idp_canonical.production_route_guard import ProductionRouteGuardError
from app.services.v3_boss_service import V3BossReadError, V3BossService

router = APIRouter(prefix="/api/v3", tags=["v3-canonical"])


def get_v3_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _read_call(callable_):
    try:
        return callable_()
    except V3BossReadError as exc:
        status = 404 if exc.code == "PROJECT_NOT_FOUND" else 409
        raise HTTPException(status, {"code": exc.code, "detail": exc.detail}) from exc
    except ProductionRouteGuardError as exc:
        raise HTTPException(503, {"code": exc.code, "detail": exc.detail}) from exc
    except ValueError as exc:
        raise HTTPException(409, {"code": "CANONICAL_READ_REJECTED", "detail": str(exc)}) from exc


def _coded_domain_http_error(exc: Exception) -> HTTPException | None:
    """Preserve a Task27 child-domain fail-closed error at the HTTP boundary.

    Task27 deliberately lets Task24/25/26/28 domain errors propagate. Those
    errors expose ``code``/``detail``. The router does not import or call those
    child services; it only preserves their rejection contract as HTTP 409.
    Unknown programming/runtime errors still propagate as 500.
    """
    code = getattr(exc, "code", None)
    detail = getattr(exc, "detail", None)
    if code is None or detail is None:
        return None
    return HTTPException(409, {"code": str(code), "detail": str(detail)})


@router.get("/boss/projects/{project_id}/snapshot")
def boss_snapshot(
    project_id: int,
    reporting_party_id: int | None = Query(default=None),
    period: date | None = Query(default=None),
    rag_scope: str = Query(default="whole_project"),
    db: Session = Depends(get_v3_db),
):
    return _read_call(lambda: V3BossService(db).snapshot(
        project_id,
        reporting_party_id=reporting_party_id,
        tax_period=period,
        rag_scope=rag_scope,
    ))


@router.get("/boss/projects/{project_id}/finance")
def boss_finance(project_id: int, db: Session = Depends(get_v3_db)):
    return _read_call(lambda: V3BossService(db).finance(project_id))


@router.get("/boss/projects/{project_id}/four-flow")
def boss_four_flow(project_id: int, db: Session = Depends(get_v3_db)):
    return _read_call(lambda: V3BossService(db).four_flow(project_id))


@router.get("/boss/projects/{project_id}/tax")
def boss_tax(
    project_id: int,
    reporting_party_id: int | None = Query(default=None),
    period: date | None = Query(default=None),
    db: Session = Depends(get_v3_db),
):
    return _read_call(lambda: V3BossService(db).tax(
        project_id, reporting_party_id=reporting_party_id, tax_period=period
    ))


@router.get("/boss/projects/{project_id}/evidence-quality")
def boss_evidence_quality(project_id: int, db: Session = Depends(get_v3_db)):
    return _read_call(lambda: V3BossService(db).evidence_quality(project_id))


@router.get("/boss/projects/{project_id}/rag-context")
def boss_rag_context(
    project_id: int,
    scope: str = Query(default="whole_project"),
    db: Session = Depends(get_v3_db),
):
    return _read_call(lambda: V3BossService(db).rag_context(project_id, scope=scope))


@router.post("/idp/direct", response_model=DirectV3ProductionResult)
def idp_direct(request: DirectV3ProductionRequest, db: Session = Depends(get_v3_db)):
    try:
        return DirectV3IngestService(db).process(request, commit=True)
    except ProductionRouteGuardError as exc:
        raise HTTPException(503, {"code": exc.code, "detail": exc.detail}) from exc
    except DirectV3ProductionError as exc:
        raise HTTPException(409, {"code": exc.code, "detail": exc.detail}) from exc
    except (ValueError, RuntimeError) as exc:
        # Task24/25/26/28 child errors are intentionally not reimplemented here.
        # Preserve their fail-closed code/detail at the HTTP boundary.
        mapped = _coded_domain_http_error(exc)
        if mapped is None:
            raise
        raise mapped from exc


@router.get("/system/status")
def v3_system_status(db: Session = Depends(get_v3_db)):
    return _read_call(lambda: V3BossService(db).system_status())
