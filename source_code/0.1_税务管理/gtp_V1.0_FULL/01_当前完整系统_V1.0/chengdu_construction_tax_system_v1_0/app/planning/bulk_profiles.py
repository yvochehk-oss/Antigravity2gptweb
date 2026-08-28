"""Batched planning profile loader.

The legacy profile helpers issue several SQL statements per candidate entity.
This module preserves their scoring semantics while loading all candidate
statistics with a fixed number of GROUP BY queries.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..models import Entity, ExternalParty, Fulfillment, Invoice, RealCost, TaxLedger
from .engine import D, PartyProfile


def _dec(value: Any, default: str = "0") -> Decimal:
    return D(default) if value is None else D(str(value))


def _bounded(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, value))


def _internal_stats(db: Session, codes: list[str]) -> dict[str, dict[str, Decimal]]:
    stats: dict[str, dict[str, Decimal]] = defaultdict(dict)
    if not codes:
        return stats

    for code, amount in db.execute(
        select(Invoice.entity_code, func.coalesce(func.sum(Invoice.net), 0))
        .where(Invoice.direction == "out", Invoice.entity_code.in_(codes))
        .group_by(Invoice.entity_code)
    ):
        stats[code]["out_revenue"] = _dec(amount)

    for code, amount in db.execute(
        select(RealCost.entity_code, func.coalesce(func.sum(RealCost.amount), 0))
        .where(RealCost.external_cash.is_(True), RealCost.entity_code.in_(codes))
        .group_by(RealCost.entity_code)
    ):
        stats[code]["ext_cost"] = _dec(amount)

    for code, revenue, tax_cash in db.execute(
        select(
            TaxLedger.entity_code,
            func.coalesce(func.sum(TaxLedger.revenue), 0),
            func.coalesce(func.sum(TaxLedger.vat_payable + TaxLedger.estimated_cit), 0),
        )
        .where(TaxLedger.generated.is_(True), TaxLedger.entity_code.in_(codes))
        .group_by(TaxLedger.entity_code)
    ):
        stats[code]["revenue"] = _dec(revenue)
        stats[code]["tax_cash"] = _dec(tax_cash)

    max_project_sales: dict[str, Decimal] = defaultdict(lambda: D("0"))
    for code, _project_id, amount in db.execute(
        select(Invoice.entity_code, Invoice.project_id, func.sum(Invoice.net))
        .where(Invoice.direction == "out", Invoice.entity_code.in_(codes))
        .group_by(Invoice.entity_code, Invoice.project_id)
    ):
        max_project_sales[code] = max(max_project_sales[code], _dec(amount))
    for code, amount in max_project_sales.items():
        stats[code]["max_hist"] = amount
    return stats


def _external_stats(db: Session, codes: list[str]) -> dict[str, dict[str, Decimal]]:
    stats: dict[str, dict[str, Decimal]] = defaultdict(dict)
    if not codes:
        return stats

    deductible_vat = case((Invoice.deductible.is_(True), Invoice.vat), else_=0)
    for code, net, credit in db.execute(
        select(
            Invoice.counterparty_code,
            func.coalesce(func.sum(Invoice.net), 0),
            func.coalesce(func.sum(deductible_vat), 0),
        )
        .where(Invoice.direction == "in", Invoice.counterparty_code.in_(codes))
        .group_by(Invoice.counterparty_code)
    ):
        stats[code]["net"] = _dec(net)
        stats[code]["credit"] = _dec(credit)

    max_project_amount: dict[str, Decimal] = defaultdict(lambda: D("0"))
    for code, _project_id, amount in db.execute(
        select(Invoice.counterparty_code, Invoice.project_id, func.sum(Invoice.net))
        .where(Invoice.direction == "in", Invoice.counterparty_code.in_(codes))
        .group_by(Invoice.counterparty_code, Invoice.project_id)
    ):
        max_project_amount[code] = max(max_project_amount[code], _dec(amount))
    for code, amount in max_project_amount.items():
        stats[code]["max_hist"] = amount

    complete_expr = case((Fulfillment.evidence_complete.is_(True), 1), else_=0)
    for code, total, complete in db.execute(
        select(
            Fulfillment.counterparty_code,
            func.count(Fulfillment.id),
            func.coalesce(func.sum(complete_expr), 0),
        )
        .where(Fulfillment.counterparty_code.in_(codes))
        .group_by(Fulfillment.counterparty_code)
    ):
        stats[code]["fulfillment_total"] = _dec(total)
        stats[code]["fulfillment_complete"] = _dec(complete)
    return stats


def _internal_profile(entity: Entity, stats: dict[str, Decimal]) -> PartyProfile:
    code = entity.code
    out_revenue = stats.get("out_revenue", D("0"))
    ext_cost = stats.get("ext_cost", D("0"))
    revenue = stats.get("revenue", D("0"))
    tax_cash = stats.get("tax_cash", D("0"))
    max_hist = stats.get("max_hist", D("0"))
    gaps: list[str] = []
    evidence_parts = 0

    if out_revenue > 0:
        cost_ratio = _bounded(ext_cost / out_revenue, D("0"), D("1.20"))
        evidence_parts += 1
    else:
        cost_ratio = D("1")
        gaps.append(f"{code}缺少可用于估算穿透成本率的历史对外收入")

    if revenue > 0:
        tax_rate = _bounded(tax_cash / revenue, D("-0.20"), D("0.50"))
        evidence_parts += 1
    else:
        tax_rate = D("0")
        gaps.append(f"{code}缺少可用于估算税务现金率的历史台账")

    capacity = max_hist * D("1.25") if max_hist > 0 else None
    if capacity is None:
        gaps.append(f"{code}未配置承载能力；当前不把容量作为硬约束")
    else:
        evidence_parts += 1

    gaps.append(f"{code}尚无主体级履约/税务风险评分，使用中性风险值，仅供方案排序")
    evidence = D("0.35") + D("0.20") * evidence_parts
    return PartyProfile(
        code=code,
        scope="internal",
        role=entity.business_role,
        capacity=capacity,
        external_cost_ratio=cost_ratio,
        tax_cash_rate=tax_rate,
        risk_score=D("0.50"),
        evidence_quality=min(evidence, D("0.95")),
        rationale="系统内承接：内部交易在系统合并口径抵销，成本穿透到最终系统外支出。",
        data_gaps=tuple(gaps),
    )


def _external_profile(party: ExternalParty, stats: dict[str, Decimal]) -> PartyProfile:
    net = stats.get("net", D("0"))
    credit = stats.get("credit", D("0"))
    max_hist = stats.get("max_hist", D("0"))
    total = stats.get("fulfillment_total", D("0"))
    complete = stats.get("fulfillment_complete", D("0"))
    tax_rate = -(credit / net) if net > 0 else D("0")
    capacity = max_hist * D("1.25") if max_hist > 0 else None
    gaps: list[str] = []

    if total > 0:
        risk = D("1") - complete / total
        evidence = D("0.75") if net > 0 else D("0.60")
    else:
        risk = D("0.50")
        evidence = D("0.45") if net > 0 else D("0.30")
        gaps.append(f"{party.code}缺少履约证据历史，使用中性风险值")
    if capacity is None:
        gaps.append(f"{party.code}无历史交易容量，当前不把容量作为硬约束")
    if net <= 0:
        gaps.append(f"{party.code}无可用于估算进项税抵扣率的历史发票")

    return PartyProfile(
        code=party.code,
        scope="external",
        role=party.kind or "external",
        capacity=capacity,
        external_cost_ratio=D("1"),
        tax_cash_rate=tax_rate,
        risk_score=_bounded(risk, D("0"), D("1")),
        evidence_quality=evidence,
        rationale="系统外承接：合同净额全部作为系统边界外成本，历史可抵扣进项税用于税务现金影响估算。",
        data_gaps=tuple(gaps),
    )


def install_bulk_planning_context() -> None:
    """Install a batched build function into the legacy service module."""
    from . import service

    def build_project_planning_context(
        db: Session,
        project_id: int,
        category: str,
        package_amount: Decimal,
    ) -> dict[str, Any]:
        project = db.get(service.Project, project_id)
        if project is None:
            raise ValueError("project not found")

        role = service.CATEGORY_ROLE.get(category)
        entity_stmt = select(Entity).where(Entity.active.is_(True), Entity.internal.is_(True))
        if role:
            entity_stmt = entity_stmt.where(Entity.business_role == role)
        entities = db.execute(entity_stmt.order_by(Entity.code)).scalars().all()
        external_rows = db.execute(
            select(ExternalParty).where(ExternalParty.active.is_(True)).order_by(ExternalParty.code)
        ).scalars().all()
        matched_external = [x for x in external_rows if service._category_matches_external(x, category)]
        if not matched_external:
            matched_external = external_rows

        internal_stats = _internal_stats(db, [e.code for e in entities])
        external_stats = _external_stats(db, [x.code for x in matched_external])
        profiles = [_internal_profile(e, internal_stats.get(e.code, {})) for e in entities]
        profiles += [_external_profile(x, external_stats.get(x.code, {})) for x in matched_external]

        party_names = {e.code: e.name for e in entities}
        party_names.update({x.code: x.name for x in matched_external})
        penetration = service.system_penetration_snapshot(db, project_id)
        return {
            "project": project,
            "profiles": profiles,
            "party_names": party_names,
            "current_external_cost": D(str(penetration["system_external_real_cost"])),
            "current_tax_paid": D(str(penetration["project_tax_paid"])),
            "planning_revenue": service._dec(project.contract_total),
            "penetration": penetration,
        }

    service.build_project_planning_context = build_project_planning_context
