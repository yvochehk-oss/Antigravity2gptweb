"""Phase 3 read-only reconciliation between Canonical SSOT and legacy ledgers.

Legacy tables are inspected only for migration/audit comparison.  Their values
never feed accounting, tax, Boss, or Canonical outputs.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text

from ..models import CashFlow, Contract, Invoice, Project
from .canonical_ssot import load_current_facts


def _d(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("payload") or {}
    return value if isinstance(value, dict) else {}


def canonical_metrics(facts: list[dict[str, Any]]) -> dict[str, Decimal | int]:
    metrics: dict[str, Decimal | int] = {
        "contract_count": 0,
        "contract_amount": Decimal("0"),
        "invoice_count": 0,
        "invoice_net": Decimal("0"),
        "invoice_vat": Decimal("0"),
        "payment_count": 0,
        "payment_amount": Decimal("0"),
    }
    for fact in facts:
        fact_type = str(fact.get("fact_type") or "")
        item = _payload(fact)
        if fact_type == "contract":
            metrics["contract_count"] = int(metrics["contract_count"]) + 1
            metrics["contract_amount"] = _d(metrics["contract_amount"]) + _d(
                item.get("contract_amount", item.get("amount"))
            )
        elif fact_type == "invoice":
            metrics["invoice_count"] = int(metrics["invoice_count"]) + 1
            metrics["invoice_net"] = _d(metrics["invoice_net"]) + _d(
                item.get("net_amount", item.get("net"))
            )
            metrics["invoice_vat"] = _d(metrics["invoice_vat"]) + _d(
                item.get("vat_amount", item.get("vat"))
            )
        elif fact_type == "payment":
            metrics["payment_count"] = int(metrics["payment_count"]) + 1
            metrics["payment_amount"] = _d(metrics["payment_amount"]) + _d(item.get("amount"))
    return metrics


def compare_metric_sets(
    canonical: dict[str, Decimal | int],
    legacy: dict[str, Decimal | int],
) -> dict[str, Any]:
    diff: dict[str, Decimal | int] = {}
    zero = True
    for key in canonical:
        left = canonical[key]
        right = legacy.get(key, 0)
        if key.endswith("_count"):
            value: Decimal | int = int(left) - int(right)
            zero = zero and value == 0
        else:
            value = _d(left) - _d(right)
            zero = zero and value == Decimal("0")
        diff[key] = value
    return {"canonical": canonical, "legacy": legacy, "diff": diff, "zero_diff": zero}


def legacy_metrics(db, project_id: int) -> dict[str, Decimal | int]:
    contract_count, contract_amount = db.execute(
        select(func.count(Contract.id), func.coalesce(func.sum(Contract.amount), 0)).where(
            Contract.project_id == project_id
        )
    ).one()
    invoice_count, invoice_net, invoice_vat = db.execute(
        select(
            func.count(Invoice.id),
            func.coalesce(func.sum(Invoice.net), 0),
            func.coalesce(func.sum(Invoice.vat), 0),
        ).where(Invoice.project_id == project_id)
    ).one()
    payment_count, payment_amount = db.execute(
        select(func.count(CashFlow.id), func.coalesce(func.sum(CashFlow.amount), 0)).where(
            CashFlow.project_id == project_id
        )
    ).one()
    return {
        "contract_count": int(contract_count or 0),
        "contract_amount": _d(contract_amount),
        "invoice_count": int(invoice_count or 0),
        "invoice_net": _d(invoice_net),
        "invoice_vat": _d(invoice_vat),
        "payment_count": int(payment_count or 0),
        "payment_amount": _d(payment_amount),
    }


def reconcile_project(db, project_id: int) -> dict[str, Any]:
    project = db.get(Project, int(project_id))
    if project is None:
        raise LookupError(f"project not found: {project_id}")
    canonical = canonical_metrics(load_current_facts(db, int(project_id)))
    legacy = legacy_metrics(db, int(project_id))
    result = compare_metric_sets(canonical, legacy)
    return {
        "project_id": int(project_id),
        "project_code": str(project.code),
        "source_of_truth": "canonical_facts",
        "legacy_mode": "read_only_audit",
        "legacy_used_for_business_output": False,
        **result,
    }


def reconciliation_summary(db) -> dict[str, Any]:
    project_ids = [int(value) for value in db.scalars(select(Project.id).order_by(Project.id)).all()]
    items = [reconcile_project(db, project_id) for project_id in project_ids]
    return {
        "source_of_truth": "canonical_facts",
        "legacy_mode": "read_only_audit",
        "projects": items,
        "project_count": len(items),
        "zero_diff_projects": sum(1 for item in items if item["zero_diff"]),
        "all_zero_diff": all(item["zero_diff"] for item in items),
        "legacy_v3_tables": {
            "facts": bool(db.execute(text("SELECT to_regclass('public.facts') IS NOT NULL")).scalar_one()),
            "invoice_facts": bool(
                db.execute(text("SELECT to_regclass('public.invoice_facts') IS NOT NULL")).scalar_one()
            ),
            "contract_facts": bool(
                db.execute(text("SELECT to_regclass('public.contract_facts') IS NOT NULL")).scalar_one()
            ),
            "payment_facts": bool(
                db.execute(text("SELECT to_regclass('public.payment_facts') IS NOT NULL")).scalar_one()
            ),
            "mode": "read_only_audit",
        },
    }


__all__ = [
    "canonical_metrics",
    "compare_metric_sets",
    "legacy_metrics",
    "reconcile_project",
    "reconciliation_summary",
]
