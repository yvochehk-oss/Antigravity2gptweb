"""V0.2: JSON API 路由。"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text

from ..auth import login as auth_login, verify_password
from ..calc import four_flow_evidence_completeness, matching_rows, project_summary
from ..db import SessionLocal
from ..dependencies import require_role
from ..domain.entities import CANONICAL_ENTITY_CODES, is_canonical_entity_code
from ..services.canonical_ledger import project_counterparties as load_canonical_counterparties
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
        s = project_summary(db, pid)
        project = s.get("project")
        if project is None:
            raise HTTPException(status_code=404, detail="项目不存在")
        out = {k: v for k, v in s.items() if k != "project"}
        out["project"] = {
            "id": project.id,
            "code": project.code,
            "project_code": project.project_code or project.code,
            "name": project.name,
            "city": project.city,
            "location": project.location or project.city,
            "contract_total": float(project.contract_total or 0),
            "contract_amount": float(project.contract_amount or project.contract_total or 0),
        }
        return out
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        _LOGGER.exception("project summary query failed: pid=%s", pid)
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
        # 序列化 Decimal → float
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
    """Return deterministic four-flow evidence completeness for one project.

    The legacy ``/matching`` response remains a list for compatibility.  This
    aggregate endpoint is intentionally separate because a JSON list cannot
    carry aggregate metadata without breaking existing callers.  ``score`` is
    null: the only published metric is ``percentage``, which is explicitly
    evidence completeness and is not a project-health score.
    """
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
    """Resolve a counterparty code to a (name, kind, is_internal) tuple.

    ``kind`` is one of ``"entity"`` (canonical 26-unit master), ``"external"``
    (named master) or ``"unknown"`` (no master row).  ``is_internal`` mirrors the
    canonical-set check so a missing master row never silently re-classifies a
    system-internal unit as external.
    """
    code = (code or "").strip()
    if not code:
        return "", "unknown", False
    if is_canonical_entity_code(code):
        row = db.execute(
            select(Entity).where(Entity.code == code)
        ).scalars().first()
        name = (row.name if row else code) or code
        return str(name), "entity", True
    row = db.execute(
        select(ExternalParty).where(ExternalParty.code == code)
    ).scalars().first()
    if row is not None:
        return str(row.name or row.code), "external", False
    return code, "unknown", False


def _aggregate_party(db, pid: int, code: str) -> dict[str, Any]:
    """Aggregate one counterparty across the four-flow evidence tables.

    Every numeric field is sourced from the same RAG-shared PostgreSQL row sets
    (contracts / invoices / cashflows / real_costs / fulfillment).  Empty
    collections produce zeros rather than fabricated placeholders.
    """
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
        select(
            func.coalesce(func.sum(Invoice.net), 0),
            func.coalesce(func.sum(Invoice.vat), 0),
            func.count(Invoice.id),
        )
        .where(Invoice.project_id == pid, Invoice.direction == "in")
        .where((Invoice.entity_code == code) | (Invoice.counterparty_code == code))
    ).one()
    inv_out = db.execute(
        select(
            func.coalesce(func.sum(Invoice.net), 0),
            func.coalesce(func.sum(Invoice.vat), 0),
            func.count(Invoice.id),
        )
        .where(Invoice.project_id == pid, Invoice.direction == "out")
        .where((Invoice.entity_code == code) | (Invoice.counterparty_code == code))
    ).one()

    cash_in = db.execute(
        select(
            func.coalesce(func.sum(CashFlow.amount), 0),
            func.count(CashFlow.id),
        )
        .where(CashFlow.project_id == pid, CashFlow.direction == "in")
        .where((CashFlow.entity_code == code) | (CashFlow.counterparty_code == code))
    ).one()
    cash_out = db.execute(
        select(
            func.coalesce(func.sum(CashFlow.amount), 0),
            func.count(CashFlow.id),
        )
        .where(CashFlow.project_id == pid, CashFlow.direction == "out")
        .where((CashFlow.entity_code == code) | (CashFlow.counterparty_code == code))
    ).one()

    real_cost = db.execute(
        select(
            func.coalesce(func.sum(RealCost.amount), 0),
            func.count(RealCost.id),
        )
        .where(RealCost.project_id == pid)
        .where((RealCost.entity_code == code) | (RealCost.counterparty_code == code))
    ).one()

    fulfillment = db.execute(
        select(
            func.coalesce(func.sum(Fulfillment.amount), 0),
            func.count(Fulfillment.id),
        )
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
    """Return every distinct party code that the project actually references.

    Each evidence table contributes its own ``*_code`` columns so a unit that
    only shows up in one channel (e.g. only on a RealCost row) is still listed.
    Order is deterministic: canonical codes first, then alphabetic.
    """
    codes: set[str] = set()

    inv_codes = db.execute(
        select(Invoice.entity_code, Invoice.counterparty_code)
        .where(Invoice.project_id == pid)
    ).all()
    for entity_code, counterparty_code in inv_codes:
        for raw in (entity_code, counterparty_code):
            value = (raw or "").strip()
            if value:
                codes.add(value)

    cf_codes = db.execute(
        select(CashFlow.entity_code, CashFlow.counterparty_code)
        .where(CashFlow.project_id == pid)
    ).all()
    for entity_code, counterparty_code in cf_codes:
        for raw in (entity_code, counterparty_code):
            value = (raw or "").strip()
            if value:
                codes.add(value)

    rc_codes = db.execute(
        select(RealCost.entity_code, RealCost.counterparty_code)
        .where(RealCost.project_id == pid)
    ).all()
    for entity_code, counterparty_code in rc_codes:
        for raw in (entity_code, counterparty_code):
            value = (raw or "").strip()
            if value:
                codes.add(value)

    ct_codes = db.execute(
        select(Contract.buyer_code, Contract.seller_code)
        .where(Contract.project_id == pid)
    ).all()
    for buyer_code, seller_code in ct_codes:
        for raw in (buyer_code, seller_code):
            value = (raw or "").strip()
            if value:
                codes.add(value)

    fu_codes = db.execute(
        select(Fulfillment.counterparty_code)
        .where(Fulfillment.project_id == pid)
    ).all()
    for (counterparty_code,) in fu_codes:
        value = (counterparty_code or "").strip()
        if value:
            codes.add(value)

    def sort_key(code: str) -> tuple[int, str]:
        return (0 if code in CANONICAL_ENTITY_CODES else 1, code)

    return sorted(codes, key=sort_key)


@router.get("/api/projects/{pid}/counterparties")
def api_project_counterparties(pid: int, _user=_reader_dependency):
    """Compatibility endpoint backed only by accepted/current Canonical Facts."""
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
        _LOGGER.exception("canonical counterparty aggregation failed: pid=%s", pid)
        raise
    finally:
        db.close()


@router.post("/api/projects/{pid}/delete-data")
@router.delete("/api/projects/{pid}/data")
def api_delete_project_data(
    pid: int,
    body: DeleteProjectDataRequest,
    _user: Any = _reader_dependency,
):
    """彻底删除该项目及关联的所有数据（项目主数据、合同、发票、流水、台账、四流记录、AI快照等，需密码确认）。"""
    username = getattr(_user, "username", "admin")
    authenticated = auth_login(username, body.password)
    if authenticated is None:
        raise HTTPException(status_code=400, detail="密码错误，安全验证未通过")

    db = SessionLocal()
    try:
        project = db.get(Project, pid)
        if project is None:
            raise HTTPException(status_code=404, detail="项目不存在")

        project_name = project.name
        project_code = project.code or project.project_code or str(pid)
        deleted_counts = {}

        # 1. 严格限定需要保护的 RAG 核心知识库与文档凭证表（绝对不删除）
        rag_preserve_tables = {
            "documents",
            "chunks",
            "ingest_jobs",
            "canonical_facts",
            "canonical_fact_outbox",
            "document_path_migrations_012",
            "query_logs",
            "query_feedback",
            "knowledge_conflicts",
            "benchmark_runs",
            "benchmark_questions",
            "rag_evidence_packs",
            "ai_review_runs",
            "projects",  # 保留项目主空间定义，以维持底层文档外键完整性
        }

        # 2. 先级联清理没有直接 project_id 字段但外键依赖 Tax 父表的子台账记录
        secondary_cleanups = [
            "DELETE FROM planning_allocations WHERE scenario_id IN (SELECT id FROM planning_scenarios WHERE project_id = :pid)",
            "DELETE FROM ai_consensus_reports WHERE batch_id IN (SELECT id FROM ai_review_batches WHERE project_id = :pid)",
            "DELETE FROM ai_review_results WHERE job_id IN (SELECT id FROM ai_review_jobs WHERE project_id = :pid)",
            "DELETE FROM facts_request_logs WHERE facts_snapshot_id IN (SELECT id FROM facts_snapshots WHERE project_id = :pid)",
            "DELETE FROM real_cost_invoice_links WHERE real_cost_id IN (SELECT id FROM real_costs WHERE project_id = :pid) OR invoice_id IN (SELECT id FROM invoices WHERE project_id = :pid)",
            "DELETE FROM sync_pending WHERE project_id = :pid OR sync_log_id IN (SELECT id FROM sync_logs WHERE project_id = :pid)",
        ]
        for sql in secondary_cleanups:
            try:
                db.execute(text(sql), {"pid": pid})
            except Exception as sec_err:
                _LOGGER.debug("secondary cleanup skipped or table missing: %s", sec_err)

        # 3. 动态获取当前数据库中所有具有 project_id 字段的基础数据表（过滤掉 VIEW 视图）
        table_rows = db.execute(text("""
            SELECT c.table_name 
            FROM information_schema.columns c
            JOIN information_schema.tables t 
              ON c.table_name = t.table_name AND c.table_schema = t.table_schema
            WHERE c.column_name = 'project_id' 
              AND c.table_schema = 'public'
              AND t.table_type = 'BASE TABLE'
        """)).all()
        tables_with_project_id = {row[0] for row in table_rows}

        # 4. 仅清空 Tax 系统所属的全部财务台账表
        tax_tables_to_wipe = [t for t in tables_with_project_id if t not in rag_preserve_tables]

        for t in tax_tables_to_wipe:
            r = db.execute(text(f"DELETE FROM {t} WHERE project_id = :pid"), {"pid": pid})
            deleted_counts[t] = r.rowcount or 0

        db.commit()
        _LOGGER.info("tax project data wiped successfully: pid=%s code=%s details=%s", pid, project_code, deleted_counts)
        return {
            "success": True,
            "project_id": pid,
            "project_code": project_code,
            "project_name": project_name,
            "message": f"项目【{project_code} · {project_name}】在 Tax 系统中的全部财税数据已彻底删除清空（RAG 凭证知识库已安全保留）！",
            "deleted_counts": deleted_counts,
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        _LOGGER.exception("tax project data deletion failed: pid=%s", pid)
        raise HTTPException(status_code=500, detail=f"删除项目数据失败: {exc}")
    finally:
        db.close()

