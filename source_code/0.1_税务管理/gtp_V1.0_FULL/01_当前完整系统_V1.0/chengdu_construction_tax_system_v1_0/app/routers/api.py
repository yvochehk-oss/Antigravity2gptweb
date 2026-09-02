"""V0.2: JSON API 路由。"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text

from ..auth import login as auth_login, verify_password
from ..calc import four_flow_evidence_completeness, matching_rows
from ..db import SessionLocal
from ..dependencies import require_role
from ..domain.entities import CANONICAL_ENTITY_CODES, is_canonical_entity_code
from ..services.canonical_ledger import project_counterparties as load_canonical_counterparties
from ..services.canonical_project_summary import canonical_project_summary
from ..models import (
    CashFlow,
    Contract,
    Entity,
    ExternalParty,
    Fulfillment,
    Invoice,
    Project,
    RealCost,
    User,
)

_LOGGER = logging.getLogger(__name__)
_reader_dependency = Depends(require_role("admin", "operator"))

router = APIRouter()


class DeleteProjectDataRequest(BaseModel):
    password: str = Field(..., min_length=1, description="操作者确认密码")


@router.get("/api/projects/{pid}")
def api_project(pid: int, _user=_reader_dependency):
    db = SessionLocal()
    try:
        s = canonical_project_summary(db, pid)
        project = s.get("project")
        if project is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        out = {k: v for k, v in s.items() if k != "project"}
        contract_total = float(s.get("contract_total") or 0)
        out["project"] = {
            "id": project.id,
            "code": project.code,
            "project_code": project.project_code or project.code,
            "name": project.name,
            "city": project.city,
            "location": project.location or project.city,
            "contract_total": contract_total,
            "contract_amount": float(s.get("contract_amount") or contract_total),
        }
        return out
    except LookupError:
        db.rollback()
        raise HTTPException(status_code=404, detail="项目不存在")
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        _LOGGER.exception("canonical project summary query failed: pid=%s", pid)
        raise
    finally:
        db.close()


@router.get("/api/projects/{pid}/matching")
def api_matching(pid: int, _user=_reader_dependency):
    db = SessionLocal()
    try:
        if db.get(Project, pid) is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        rows = matching_rows(db, pid)
        for r in rows:
            for k in ("contract", "fulfillment", "invoice", "paid"):
                r[k] = float(r[k])
        return rows
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        _LOGGER.exception("matching query failed: pid=%s", pid)
        raise
    finally:
        db.close()


@router.get("/api/projects/{pid}/matching/completeness")
def api_matching_completeness(pid: int, _user=_reader_dependency):
    db = SessionLocal()
    try:
        if db.get(Project, pid) is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        rows = matching_rows(db, pid)
        return four_flow_evidence_completeness(rows)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        _LOGGER.exception("matching completeness query failed: pid=%s", pid)
        raise
    finally:
        db.close()


def _party_label(db, code: str) -> tuple[str, str, bool]:
    code = (code or "").strip()
    if not code:
        return "", "unknown", False
    if is_canonical_entity_code(code):
        row = db.execute(select(Entity).where(Entity.code == code)).scalars().first()
        name = (row.name if row else code) or code
        return str(name), "entity", True
    row = db.execute(select(ExternalParty).where(ExternalParty.code == code)).scalars().first()
    if row is not None:
        return str(row.name or row.code), "external", False
    return code, "unknown", False


def _aggregate_party(db, pid: int, code: str) -> dict[str, Any]:
    name, kind, is_internal = _party_label(db, code)
    contract_amount_total = db.scalar(
        select(func.coalesce(func.sum(Contract.amount), 0))
        .where(Contract.project_id == pid)
        .where((Contract.buyer_code == code) | (Contract.seller_code == code))
    ) or 0
    contract_count = db.scalar(
        select(func.count(Contract.id))
        .where(Contract.project_id == pid)
        .where((Contract.buyer_code == code) | (Contract.seller_code == code))
    ) or 0
    inv_in = db.execute(
        select(func.coalesce(func.sum(Invoice.net), 0), func.coalesce(func.sum(Invoice.vat), 0), func.count(Invoice.id))
        .where(Invoice.project_id == pid, Invoice.direction == "in")
        .where((Invoice.entity_code == code) | (Invoice.counterparty_code == code))
    ).one()
    inv_out = db.execute(
        select(func.coalesce(func.sum(Invoice.net), 0), func.coalesce(func.sum(Invoice.vat), 0), func.count(Invoice.id))
        .where(Invoice.project_id == pid, Invoice.direction == "out")
        .where((Invoice.entity_code == code) | (Invoice.counterparty_code == code))
    ).one()
    cash_in = db.execute(
        select(func.coalesce(func.sum(CashFlow.amount), 0), func.count(CashFlow.id))
        .where(CashFlow.project_id == pid, CashFlow.direction == "in")
        .where((CashFlow.entity_code == code) | (CashFlow.counterparty_code == code))
    ).one()
    cash_out = db.execute(
        select(func.coalesce(func.sum(CashFlow.amount), 0), func.count(CashFlow.id))
        .where(CashFlow.project_id == pid, CashFlow.direction == "out")
        .where((CashFlow.entity_code == code) | (CashFlow.counterparty_code == code))
    ).one()
    real_cost = db.execute(
        select(func.coalesce(func.sum(RealCost.amount), 0), func.count(RealCost.id))
        .where(RealCost.project_id == pid)
        .where((RealCost.entity_code == code) | (RealCost.counterparty_code == code))
    ).one()
    fulfillment = db.execute(
        select(func.coalesce(func.sum(Fulfillment.amount), 0), func.count(Fulfillment.id))
        .where(Fulfillment.project_id == pid)
        .where(Fulfillment.counterparty_code == code)
    ).one()
    return {
        "party_code": code,
        "party_name": name,
        "kind": kind,
        "isInternal": is_internal,
        "source": "entities" if kind == "entity" else "external_parties" if kind == "external" else "unknown",
        "contract_count": int(contract_count),
        "contract_amount": float(contract_amount_total),
        "invoice_in_count": int(inv_in[2]),
        "invoice_in_net": float(inv_in[0]),
        "invoice_in_vat": float(inv_in[1]),
        "invoice_out_count": int(inv_out[2]),
        "invoice_out_net": float(inv_out[0]),
        "invoice_out_vat": float(inv_out[1]),
        "cashflow_in_count": int(cash_in[1]),
        "cashflow_in_amount": float(cash_in[0]),
        "cashflow_out_count": int(cash_out[1]),
        "cashflow_out_amount": float(cash_out[0]),
        "real_cost_count": int(real_cost[1]),
        "real_cost_amount": float(real_cost[0]),
        "fulfillment_count": int(fulfillment[1]),
        "fulfillment_amount": float(fulfillment[0]),
    }


def _discover_party_codes(db, pid: int) -> list[str]:
    codes: set[str] = set()
    inv_codes = db.execute(select(Invoice.entity_code, Invoice.counterparty_code).where(Invoice.project_id == pid)).all()
    for entity_code, counterparty_code in inv_codes:
        for raw in (entity_code, counterparty_code):
            value = (raw or "").strip()
            if value:
                codes.add(value)
    contract_codes = db.execute(select(Contract.buyer_code, Contract.seller_code).where(Contract.project_id == pid)).all()
    for buyer_code, seller_code in contract_codes:
        for raw in (buyer_code, seller_code):
            value = (raw or "").strip()
            if value:
                codes.add(value)
    cash_codes = db.execute(select(CashFlow.entity_code, CashFlow.counterparty_code).where(CashFlow.project_id == pid)).all()
    for entity_code, counterparty_code in cash_codes:
        for raw in (entity_code, counterparty_code):
            value = (raw or "").strip()
            if value:
                codes.add(value)
    real_codes = db.execute(select(RealCost.entity_code, RealCost.counterparty_code).where(RealCost.project_id == pid)).all()
    for entity_code, counterparty_code in real_codes:
        for raw in (entity_code, counterparty_code):
            value = (raw or "").strip()
            if value:
                codes.add(value)
    fulfillment_codes = db.execute(select(Fulfillment.counterparty_code).where(Fulfillment.project_id == pid)).scalars().all()
    for raw in fulfillment_codes:
        value = (raw or "").strip()
        if value:
            codes.add(value)
    canonical = set(CANONICAL_ENTITY_CODES)
    return sorted(codes, key=lambda value: (0 if value in canonical else 1, value))


@router.get("/api/projects/{pid}/counterparties")
def api_project_counterparties(pid: int, _user=_reader_dependency):
    db = SessionLocal()
    try:
        if db.get(Project, pid) is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        return load_canonical_counterparties(db, pid)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        _LOGGER.exception("canonical counterparties query failed: pid=%s", pid)
        raise
    finally:
        db.close()


@router.post("/api/login")
def api_login(payload: dict[str, Any]):
    username = str(payload.get("username") or "")
    password = str(payload.get("password") or "")
    try:
        return auth_login(username, password)
    except Exception as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post("/api/projects/{pid}/delete-data")
def api_delete_project_data(pid: int, payload: DeleteProjectDataRequest, user=Depends(require_role("admin"))):
    db = SessionLocal()
    try:
        project = db.get(Project, pid)
        if project is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        operator = db.execute(select(User).where(User.username == user.get("username"))).scalars().first()
        if operator is None or not verify_password(payload.password, operator.password_hash):
            raise HTTPException(status_code=403, detail="密码确认失败")
        rag_preserve_tables = {
            "projects",
            "documents",
            "chunks",
            "ingest_jobs",
            "canonical_facts",
            "canonical_fact_outbox",
            "accounting_report_snapshots",
        }
        tables_with_project_id = [
            str(name)
            for name in db.execute(
                text("SELECT DISTINCT table_name FROM information_schema.columns WHERE table_schema='public' AND column_name='project_id'")
            ).scalars().all()
        ]
        tax_tables_to_wipe = [name for name in tables_with_project_id if name not in rag_preserve_tables]
        deleted: dict[str, int] = {}
        for table_name in tax_tables_to_wipe:
            result = db.execute(text(f'DELETE FROM "{table_name}" WHERE project_id=:pid'), {"pid": pid})
            deleted[table_name] = int(result.rowcount or 0)
        db.commit()
        return {"status": "ok", "project_id": pid, "deleted": deleted, "rag_preserved": True}
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        _LOGGER.exception("project data deletion failed: pid=%s", pid)
        raise
    finally:
        db.close()
