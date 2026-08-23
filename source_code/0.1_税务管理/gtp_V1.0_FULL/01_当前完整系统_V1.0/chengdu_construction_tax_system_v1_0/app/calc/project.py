"""V0.2: 项目经营口径计算。"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Invoice, Progress, Project, RealCost


def _zero(d: Decimal | float | int | None) -> Decimal:
    if d is None:
        return Decimal("0")
    return Decimal(str(d))


def project_summary(db: Session, pid: int) -> dict[str, Any]:
    p = db.get(Project, pid)
    revenue = _zero(
        db.scalar(
            select(func.coalesce(func.sum(Progress.recognized_revenue), 0))
            .where(Progress.project_id == pid)
        )
    )
    costs = db.execute(
        select(RealCost).where(RealCost.project_id == pid)
    ).scalars().all()
    real = sum((_zero(x.amount) for x in costs), Decimal("0"))
    external = sum(
        (_zero(x.amount) for x in costs if x.external_cash), Decimal("0")
    )
    inv = db.execute(
        select(Invoice).where(Invoice.project_id == pid)
    ).scalars().all()
    outvat = sum(
        (_zero(x.vat) for x in inv if x.direction == "out"), Decimal("0")
    )
    invat = sum(
        (_zero(x.vat) for x in inv if x.direction == "in" and x.deductible),
        Decimal("0"),
    )
    vat = max(outvat - invat, Decimal("0"))
    profit = revenue - real
    progress = (revenue / p.contract_total) if p and p.contract_total else Decimal("0")
    # V0.2 EAC：保留三档（预算 / 当前效率 / 实际下限）
    eac_efficiency = (real / progress) if progress > Decimal("0.05") else real
    eac = max(eac_efficiency, real)
    eac_profit = (
        (p.contract_total - eac) if p else Decimal("0")
    )
    margin = (profit / revenue) if revenue else Decimal("0")
    return {
        "project": p,
        "revenue": revenue,
        "real_cost": real,
        "external_cash_cost": external,
        "profit": profit,
        "margin": margin,
        "vat": vat,
        "progress": progress,
        "eac": eac,
        "eac_profit": eac_profit,
        "eac_efficiency": eac_efficiency,
    }


def consolidated(db: Session) -> dict[str, Any]:
    projects = db.execute(select(Project)).scalars().all()
    rows = [project_summary(db, p.id) for p in projects]
    return {
        "rows": rows,
        "revenue": sum((x["revenue"] for x in rows), Decimal("0")),
        "cost": sum((x["real_cost"] for x in rows), Decimal("0")),
        "profit": sum((x["profit"] for x in rows), Decimal("0")),
        "vat": sum((x["vat"] for x in rows), Decimal("0")),
    }


def cost_tree_summary(db: Session, pid: int) -> dict[str, dict[str, Decimal]]:
    rows = db.execute(
        select(RealCost).where(RealCost.project_id == pid)
    ).scalars().all()
    tree: dict[str, dict[str, Decimal]] = {}
    for x in rows:
        bucket = tree.setdefault(x.category, {})
        bucket[x.subcategory or "未细分"] = bucket.get(
            x.subcategory or "未细分", Decimal("0")
        ) + _zero(x.amount)
    return tree


__all__ = ["project_summary", "consolidated", "cost_tree_summary"]