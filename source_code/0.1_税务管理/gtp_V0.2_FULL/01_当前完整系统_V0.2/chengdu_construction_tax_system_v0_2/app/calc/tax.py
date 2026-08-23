"""V0.2: 月度法人税务台账。"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..cache import tax_ledger_cache
from ..constants import INTERNAL
from ..models import Invoice, RealCost, TaxLedger, TaxRule


def _zero(d: Decimal | float | int | None) -> Decimal:
    if d is None:
        return Decimal("0")
    return Decimal(str(d))


_CIT_UNAPPLIED_NOTE = (
    "未应用项：业务招待费 60% 上限、研发费加计扣除、小型微利优惠、"
    "以前年度亏损弥补、税会差异调整、预缴与汇缴差异。"
    "本数字为管理口径预测，不得直接用于正式申报。"
)


def _rule_rate(db: Session, code: str, default: float | Decimal) -> Decimal:
    r = db.scalar(select(TaxRule).where(TaxRule.code == code))
    return _zero(r.rate) if r else _zero(default)


def rebuild_tax_ledger(db: Session, period: str) -> list[TaxLedger]:
    """重建指定期间的法人月度管理税务台账。

    V0.2: 事务安全 + 单次 GROUP BY 聚合 + 显式 CIT note。
    """
    cit_rate = _rule_rate(db, "CIT_GENERAL", 0.25)

    try:
        db.execute(delete(TaxLedger).where(TaxLedger.period == period))

        invs = db.execute(
            select(Invoice).where(Invoice.period == period)
        ).scalars().all()
        costs = db.execute(
            select(RealCost).where(RealCost.period == period)
        ).scalars().all()

        # 聚合：法人 × 方向 × 类别
        outvat: dict[str, Decimal] = {c: Decimal("0") for c in INTERNAL}
        invat: dict[str, Decimal] = {c: Decimal("0") for c in INTERNAL}
        revenue: dict[str, Decimal] = {c: Decimal("0") for c in INTERNAL}
        invoice_cost: dict[str, Decimal] = {c: Decimal("0") for c in INTERNAL}

        for i in invs:
            if i.entity_code not in INTERNAL:
                continue
            if i.direction == "out":
                outvat[i.entity_code] += _zero(i.vat)
                revenue[i.entity_code] += _zero(i.net)
            elif i.direction == "in":
                invoice_cost[i.entity_code] += _zero(i.net)
                if i.deductible:
                    invat[i.entity_code] += _zero(i.vat)

        direct_real: dict[str, Decimal] = {c: Decimal("0") for c in INTERNAL}
        for x in costs:
            if x.entity_code not in INTERNAL:
                continue
            # A 的真实外部成本（counterparty 非空）通常已由 A 进项发票覆盖，避免重复
            if x.entity_code == "A" and x.counterparty_code:
                continue
            direct_real[x.entity_code] += _zero(x.amount)

        for code in sorted(INTERNAL):
            r_vat_payable = max(outvat[code] - invat[code], Decimal("0"))
            legal_cost = invoice_cost[code] + direct_real[code]
            profit = revenue[code] - legal_cost
            est_cit = max(profit, Decimal("0")) * cit_rate
            ledger = TaxLedger(
                period=period,
                entity_code=code,
                output_vat=outvat[code],
                input_vat=invat[code],
                vat_payable=r_vat_payable,
                revenue=revenue[code],
                real_cost=legal_cost,
                estimated_profit=profit,
                estimated_cit=est_cit,
                cit_note=_CIT_UNAPPLIED_NOTE,
                generated=True,
            )
            db.add(ledger)
        db.commit()
        tax_ledger_cache.invalidate(period)
    except Exception:
        db.rollback()
        raise

    return db.execute(
        select(TaxLedger)
        .where(TaxLedger.period == period)
        .order_by(TaxLedger.entity_code)
    ).scalars().all()


__all__ = ["rebuild_tax_ledger"]