"""Deterministic monthly tax ledger calculation.

Entity scope is deliberately sourced from the canonical ``Entity`` table.  A
business role is metadata only; it must never be used as a legal-entity code
or as a fallback when an owner cannot be resolved.
"""
from __future__ import annotations

import re
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..cache import tax_ledger_cache
from ..constants import CANONICAL_ENTITY_CODES
from ..models import Entity, Invoice, RealCost, TaxLedger, TaxRule

_PERIOD_RE = re.compile(r"^\d{4}-\d{2}$")


class EntityScopeError(ValueError):
    """Raised when a tax input cannot be mapped to an active legal entity."""


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
    if r is None:
        return _zero(default)
    if not r.reviewed:
        raise ValueError(
            f"TaxRule '{code}' exists but is not reviewed; "
            "a reviewed rule is required for deterministic calculation"
        )
    return _zero(r.rate)


def _get_effective_tax_rule(
    db: Session, code: str, period: str, default: float | Decimal | None = None
) -> Decimal:
    """Select a tax rule by code, period validity, and reviewed status.

    Returns the rate from the single matching rule, or raises if none or
    multiple rules are active for the given period.
    """
    rules = db.execute(
        select(TaxRule).where(TaxRule.code == code)
    ).scalars().all()

    effective_rules = [
        r for r in rules
        if r.reviewed
        and r.effective_from <= period
        and (r.effective_to is None or r.effective_to == "" or r.effective_to >= period)
    ]

    if len(effective_rules) == 0:
        msg = f"No active reviewed tax rule for '{code}' in period {period}"
        if default is not None:
            raise ValueError(
                f"{msg}; no fallback allowed in deterministic mode (got default={default})"
            )
        raise ValueError(msg)

    if len(effective_rules) > 1:
        rule_summaries = [
            f"{r.effective_from}-{r.effective_to}" for r in effective_rules
        ]
        raise ValueError(
            f"Multiple active tax rules for '{code}' in period {period}; "
            f"candidates: {rule_summaries}"
        )

    return _zero(effective_rules[0].rate)


def _entity_scope(db: Session) -> tuple[tuple[str, ...], dict[str, Entity]]:
    """Load the legal-entity scope and all master rows used for validation.

    The first query is intentionally the source of the ledger scope.  The
    second query is only a validation map so an inactive, external, or
    unknown owner can never disappear through a permissive ``if`` filter.
    """
    legal_entities = db.execute(
        select(Entity)
        .where(
            # ``internal`` is a deprecated V0.2 compatibility column.  It is
            # intentionally not part of the calculation boundary: the
            # canonical Entity master is the source of truth, and an Entity
            # row is an internal owner by virtue of being in this table.  The
            # legal/active flags are the only current scope controls.
            Entity.active.is_(True),
            Entity.legal_entity.is_(True),
        )
        .order_by(Entity.code)
    ).scalars().all()
    all_entities = db.execute(select(Entity).order_by(Entity.code)).scalars().all()

    by_code: dict[str, Entity] = {}
    for entity in all_entities:
        code = (entity.code or "").strip()
        if not code:
            raise EntityScopeError("实体主数据存在空 code，无法建立税务主体范围")
        if code not in CANONICAL_ENTITY_CODES:
            raise EntityScopeError(
                f"实体主数据包含非 canonical code：{code}；请先完成主体迁移"
            )
        if code in by_code:
            raise EntityScopeError(f"实体主数据 code 重复：{code}")
        by_code[code] = entity

    # Use the result of the canonical scope query, rather than reconstructing
    # it from constants or business-role labels.
    legal_codes = tuple((entity.code or "").strip() for entity in legal_entities)
    if not legal_codes:
        raise EntityScopeError("实体主数据没有 active legal entity，拒绝生成空税务台账")
    return legal_codes, by_code


def _resolve_owner(code: str | None, by_code: dict[str, Entity]) -> str:
    """Resolve an input owner to an active legal entity code.

    Active non-legal internal entities (for example a branch) are rolled up
    through ``parent_entity_code``.  External, inactive, missing, and cyclic
    references are rejected explicitly so they cannot contaminate a ledger.
    """
    original = (code or "").strip()
    if not original:
        raise EntityScopeError("税务输入缺少 entity_code")

    seen: set[str] = set()
    current = original
    while True:
        if current in seen:
            chain = " -> ".join((*seen, current))
            raise EntityScopeError(f"实体 parent_entity_code 存在循环：{chain}")
        seen.add(current)

        entity = by_code.get(current)
        if entity is None:
            raise EntityScopeError(f"税务输入 entity_code 未在实体主数据中找到：{original}")
        if not entity.active:
            raise EntityScopeError(f"税务输入 entity_code 已停用：{original}")
        if entity.legal_entity:
            return current

        parent = (entity.parent_entity_code or "").strip()
        if not parent:
            raise EntityScopeError(
                f"非独立内部主体缺少 parent_entity_code，无法归集：{original}"
            )
        current = parent


def _input_invoice_key(
    entity_code: str,
    counterparty_code: str | None,
    period: str | None = None,
    amount: Decimal | float | int | None = None,
) -> tuple[str, str, str, str]:
    """Return a stable composite key for matching covered external real costs.

    The key includes entity, counterparty, period, and amount to avoid
    false positives when the same counterparty has multiple transactions
    in different periods or amounts.
    """
    return (
        entity_code,
        (counterparty_code or "").strip(),
        (period or "").strip(),
        str(_zero(amount)),
    )


def rebuild_tax_ledger(db: Session, period: str) -> list[TaxLedger]:
    """重建指定期间的法人月度管理税务台账。

    The ledger always contains every active internal legal entity, including
    entities with no transactions in ``period``.  Non-independent branches
    are mapped to their active legal parent before aggregation.
    """
    if not isinstance(period, str) or not period.strip():
        raise ValueError("税务台账期间不能为空")
    if not _PERIOD_RE.match(period):
        raise ValueError(
            f"Invalid period format: {period!r} (expected YYYY-MM, e.g. 2024-03)"
        )

    cit_rate = _get_effective_tax_rule(db, "CIT_GENERAL", period, 0.25)
    legal_codes, by_code = _entity_scope(db)

    try:
        invs = db.execute(
            select(Invoice).where(Invoice.period == period)
        ).scalars().all()
        costs = db.execute(
            select(RealCost).where(RealCost.period == period)
        ).scalars().all()

        # Resolve all owners before deleting the previous period.  An invalid
        # source therefore leaves an existing ledger intact and is visible to
        # the caller rather than being silently skipped.
        resolved_invoices: list[tuple[Invoice, str]] = [
            (invoice, _resolve_owner(invoice.entity_code, by_code))
            for invoice in invs
        ]
        resolved_costs: list[tuple[RealCost, str]] = [
            (cost, _resolve_owner(cost.entity_code, by_code))
            for cost in costs
        ]

        db.execute(delete(TaxLedger).where(TaxLedger.period == period))

        # Aggregate by canonical legal entity.  Decimal arithmetic is retained
        # so the Numeric(18, 2) model controls the final persistence scale.
        outvat: dict[str, Decimal] = {code: Decimal("0") for code in legal_codes}
        invat: dict[str, Decimal] = {code: Decimal("0") for code in legal_codes}
        revenue: dict[str, Decimal] = {code: Decimal("0") for code in legal_codes}
        invoice_cost: dict[str, Decimal] = {
            code: Decimal("0") for code in legal_codes
        }

        # A direct external cost can be a second representation of an input
        # invoice.  Track the normalized invoice owners so this legacy
        # anti-double-counting rule applies to every real entity uniformly,
        # without an entity-code special case.
        covered_invoice_keys: set[tuple[str, str, str, str]] = set()

        for i, code in resolved_invoices:
            if i.direction == "out":
                outvat[code] += _zero(i.vat)
                revenue[code] += _zero(i.net)
            elif i.direction == "in":
                invoice_cost[code] += _zero(i.net)
                covered_invoice_keys.add(
                    _input_invoice_key(code, i.counterparty_code, i.period, i.net)
                )
                if i.deductible:
                    invat[code] += _zero(i.vat)
            else:
                raise ValueError(f"发票方向非法：{i.direction!r}")

        direct_real: dict[str, Decimal] = {
            code: Decimal("0") for code in legal_codes
        }
        for x, code in resolved_costs:
            if x.counterparty_code and _input_invoice_key(
                code, x.counterparty_code, x.period, x.amount
            ) in covered_invoice_keys:
                continue
            direct_real[code] += _zero(x.amount)

        for code in legal_codes:
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
