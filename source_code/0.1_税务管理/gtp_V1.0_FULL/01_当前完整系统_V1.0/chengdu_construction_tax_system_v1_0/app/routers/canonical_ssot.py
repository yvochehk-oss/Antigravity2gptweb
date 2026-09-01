"""Read-only Canonical Facts endpoints for the Tax deterministic engine."""
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException

from ..db import SessionLocal
from ..services.canonical_ledger import (
    project_counterparties,
    project_ledger,
    project_ledger_bundle,
)
from ..services.canonical_ssot import build_consolidated_project_pnl, ssot_status

router = APIRouter(prefix="/api/v1/canonical-ssot", tags=["canonical-ssot"])


def _read_project(project_id: int, label: str, loader: Callable[[Any, int], Any]) -> Any:
    """Execute one read-only Canonical Facts projection with a stable 503 boundary."""
    try:
        with SessionLocal() as db:
            return loader(db, project_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"canonical {label} unavailable: {exc}",
        ) from exc


@router.get("/status")
def canonical_ssot_status() -> dict:
    try:
        with SessionLocal() as db:
            return {
                "mode": "direct-read",
                "source_of_truth": "RAG canonical_facts",
                "phase": 2,
                "manual_party_creation": False,
                **ssot_status(db),
            }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"canonical facts unavailable: {exc}") from exc


@router.get("/projects/{project_id}/contracts")
def canonical_contracts(project_id: int) -> list[dict[str, Any]]:
    return _read_project(
        project_id,
        "contracts",
        lambda db, pid: project_ledger(db, pid, "contract"),
    )


@router.get("/projects/{project_id}/invoices")
def canonical_invoices(project_id: int) -> list[dict[str, Any]]:
    return _read_project(
        project_id,
        "invoices",
        lambda db, pid: project_ledger(db, pid, "invoice"),
    )


@router.get("/projects/{project_id}/cash-flows")
def canonical_cash_flows(project_id: int) -> list[dict[str, Any]]:
    return _read_project(
        project_id,
        "cash flows",
        lambda db, pid: project_ledger(db, pid, "payment"),
    )


@router.get("/projects/{project_id}/ledger")
def canonical_project_ledger(project_id: int) -> dict[str, Any]:
    return _read_project(project_id, "project ledger", project_ledger_bundle)


@router.get("/projects/{project_id}/counterparties")
def canonical_project_counterparties(project_id: int) -> dict[str, Any]:
    return _read_project(project_id, "counterparties", project_counterparties)


@router.get("/projects/{project_id}/consolidated-pnl")
def canonical_consolidated_pnl(project_id: int) -> dict:
    return _read_project(project_id, "P&L", build_consolidated_project_pnl)
