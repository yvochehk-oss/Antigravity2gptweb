"""RAG context builder with Task22 Reader/RAG cutover routing."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..calc.matching import matching_rows
from ..calc.project import cost_tree_summary, project_summary
from ..calc.risk import scan_risks
from ..calc.tax import rebuild_tax_ledger
from ..constants import CATEGORY_SCOPE, SCOPES
from ..cutover.reader import get_reader_route, load_canonical_project_entities
from ..models import Budget, CashFlow, Contract, Fulfillment, Invoice, Progress, Project, RealCost, TaxRule


def _serialize(rows: list[Any], fields: list[str]) -> list[dict[str, Any]]:
    out = []
    for x in rows:
        d: dict[str, Any] = {}
        for f in fields:
            v = getattr(x, f, None)
            if isinstance(v, Decimal):
                v = float(v)
            d[f] = v
        out.append(d)
    return out


def _tax_periods(db: Session, pid: int) -> list[str]:
    periods: set[str] = set()
    for x in db.execute(select(Invoice).where(Invoice.project_id == pid)).scalars().all():
        if x.period:
            periods.add(x.period)
    for x in db.execute(select(Progress).where(Progress.project_id == pid)).scalars().all():
        if x.period:
            periods.add(x.period)
    return sorted(periods)


def _build_legacy_context(db: Session, pid: int, scope: str) -> dict[str, Any]:
    p = db.get(Project, pid)
    if not p:
        raise ValueError("项目不存在")
    summary = project_summary(db, pid)
    base: dict[str, Any] = {
        "project": {"id": p.id, "code": p.code, "name": p.name, "city": p.city, "contract_total": float(p.contract_total), "tax_method": p.tax_method},
        "system_calculation": {
            "recognized_revenue": float(summary["revenue"]),
            "real_cost": float(summary["real_cost"]),
            "profit": float(summary["profit"]),
            "margin": float(summary["margin"]),
            "vat_management_estimate": float(summary["vat"]),
            "progress_ratio": float(summary["progress"]),
            "eac_cost": float(summary["eac"]),
            "eac_profit": float(summary["eac_profit"]),
        },
        "scope": scope,
        "scope_name": SCOPES.get(scope, scope),
    }

    budgets = db.execute(select(Budget).where(Budget.project_id == pid)).scalars().all()
    contracts = db.execute(select(Contract).where(Contract.project_id == pid)).scalars().all()
    fulfills = db.execute(select(Fulfillment).where(Fulfillment.project_id == pid)).scalars().all()
    invoices = db.execute(select(Invoice).where(Invoice.project_id == pid)).scalars().all()
    cash = db.execute(select(CashFlow).where(CashFlow.project_id == pid)).scalars().all()
    costs = db.execute(select(RealCost).where(RealCost.project_id == pid)).scalars().all()
    progress = db.execute(select(Progress).where(Progress.project_id == pid)).scalars().all()

    cat = CATEGORY_SCOPE.get(scope)
    if cat:
        contracts = [x for x in contracts if x.category == cat]
        fulfills = [x for x in fulfills if x.category == cat]
        invoices = [x for x in invoices if x.category == cat]
        costs = [x for x in costs if x.category == cat]
        base["contracts"] = _serialize(contracts, ["contract_no", "buyer_code", "seller_code", "category", "amount", "internal_trade", "note"])
        base["fulfillment"] = _serialize(fulfills, ["counterparty_code", "kind", "category", "quantity", "amount", "evidence_complete", "note"])
        base["invoices"] = _serialize(invoices, ["invoice_no", "period", "entity_code", "direction", "counterparty_code", "category", "net", "vat", "rate", "deductible", "note"])
        base["real_costs"] = _serialize(costs, ["entity_code", "counterparty_code", "category", "subcategory", "period", "amount", "external_cash", "note"])
        base["four_stream_matching"] = [x for x in matching_rows(db, pid) if x["category"] == cat]
        return base

    if scope in ("overview", "whole_project", "budget", "eac"):
        base["budget"] = _serialize(budgets, ["category", "amount"])
        base["progress"] = _serialize(progress, ["period", "output_value", "settlement", "recognized_revenue", "collection"])
    if scope in ("contract", "whole_project"):
        base["contracts"] = _serialize(contracts, ["contract_no", "buyer_code", "seller_code", "category", "amount", "internal_trade", "note"])
        base["four_stream_matching"] = matching_rows(db, pid)
    if scope in ("fulfillment", "whole_project"):
        base["fulfillment"] = _serialize(fulfills, ["counterparty_code", "kind", "category", "quantity", "amount", "evidence_complete", "note"])
        base["four_stream_matching"] = matching_rows(db, pid)
    if scope in ("invoice", "whole_project"):
        base["invoices"] = _serialize(invoices, ["invoice_no", "period", "entity_code", "direction", "counterparty_code", "category", "net", "vat", "rate", "deductible", "note"])
        base["four_stream_matching"] = matching_rows(db, pid)
    if scope in ("cashflow", "whole_project"):
        base["cashflows"] = _serialize(cash, ["entity_code", "counterparty_code", "direction", "amount", "period", "note"])
        base["four_stream_matching"] = matching_rows(db, pid)
    if scope in ("cost", "whole_project"):
        base["real_costs"] = _serialize(costs, ["entity_code", "counterparty_code", "category", "subcategory", "period", "amount", "external_cash", "note"])
        tree = cost_tree_summary(db, pid)
        base["cost_tree"] = {k: {kk: float(vv) for kk, vv in v.items()} for k, v in tree.items()}
    if scope in ("tax", "whole_project"):
        ledgers: list[dict[str, Any]] = []
        for period in _tax_periods(db, pid):
            rows = rebuild_tax_ledger(db, period)
            serialized = _serialize(rows, ["period", "entity_code", "output_vat", "input_vat", "vat_payable", "revenue", "real_cost", "estimated_profit", "estimated_cit"])
            ledgers += [r for r in serialized if r.get("vat_payable") or r.get("revenue") or r.get("real_cost") or r.get("output_vat") or r.get("input_vat") or r.get("estimated_cit")]
        rules = db.execute(select(TaxRule)).scalars().all()
        base["tax_ledgers"] = ledgers
        base["tax_rule_review_status"] = _serialize(rules, ["code", "rate", "effective_from", "effective_to", "reviewed", "note"])
    if scope in ("risk", "whole_project"):
        risks = scan_risks(db, pid)
        base["deterministic_risks"] = _serialize(risks, ["severity", "code", "message", "resolved"])
    if scope == "eac":
        base["eac_note"] = "EAC为系统确定性管理预测；模型只能解释和提出需要复核的假设，不得覆盖系统计算。"

    if scope == "whole_project":
        for key in ("contracts", "fulfillment", "invoices", "cashflows", "real_costs"):
            if key in base and len(base[key]) > 80:
                base[key] = base[key][:80]
                base[f"{key}_truncated_summary"] = f"已截断为前 80 条；本字段原本 {key} 数据应进一步分段或聚合后再发模型。"
    return base


def _apply_canonical_entities(db: Session, pid: int, scope: str, base: dict[str, Any]) -> dict[str, Any]:
    category = CATEGORY_SCOPE.get(scope)
    entities = load_canonical_project_entities(db, pid, category=category)
    for key in ("contracts", "fulfillment", "invoices", "cashflows", "four_stream_matching"):
        base.pop(key, None)
    if category:
        base["contracts"] = entities["contracts"]
        base["fulfillment"] = entities["fulfillment"]
        base["invoices"] = entities["invoices"]
    else:
        if scope in ("contract", "whole_project"):
            base["contracts"] = entities["contracts"]
        if scope in ("fulfillment", "whole_project"):
            base["fulfillment"] = entities["fulfillment"]
        if scope in ("invoice", "whole_project"):
            base["invoices"] = entities["invoices"]
        if scope in ("cashflow", "whole_project"):
            base["cashflows"] = entities["cashflows"]
    if scope in ("overview", "whole_project"):
        base["canonical_facts"] = entities["canonical_facts"][:120]
    return base


def build_context(db: Session, pid: int, scope: str) -> dict[str, Any]:
    """Build RAG context from the source selected by the Task22 control plane.

    PRIMARY/CANONICAL_FACTS has no automatic fallback. Any canonical read
    failure propagates to the caller and requires an explicit operator rollback.
    """
    route = get_reader_route(db)
    base = _build_legacy_context(db, pid, scope)
    base["read_control"] = {"new_fact_read_mode": route.mode, "rag_source": route.rag_source}
    if not route.is_canonical:
        base["data_source"] = "LEGACY"
        return base
    base = _apply_canonical_entities(db, pid, scope, base)
    base["data_source"] = "CANONICAL_FACTS"
    return base
