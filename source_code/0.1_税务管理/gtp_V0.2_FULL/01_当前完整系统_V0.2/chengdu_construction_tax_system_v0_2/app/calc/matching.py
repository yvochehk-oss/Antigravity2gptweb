"""V0.2: 四流匹配 + 风险阈值表驱动。"""
from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..constants import COST_CATEGORIES, DEFAULT_RISK_THRESHOLDS
from ..models import (
    Contract, Fulfillment, Invoice, CashFlow, RiskThreshold,
)
from .subjects import kind_to_category


def _zero(d: Decimal | float | int | None) -> Decimal:
    if d is None:
        return Decimal("0")
    return Decimal(str(d))


def _load_thresholds(db: Session) -> dict[str, tuple[Decimal, str]]:
    """返回 {code: (ratio, severity)}，缺省回退到代码常量。"""
    out: dict[str, tuple[Decimal, str]] = {}
    rows = db.execute(
        select(RiskThreshold).where(RiskThreshold.enabled == True)  # noqa: E712
    ).scalars().all()
    for r in rows:
        out[r.code] = (_zero(r.ratio), r.severity)
    for k, v in DEFAULT_RISK_THRESHOLDS.items():
        out.setdefault(k, (Decimal(str(v)), "YELLOW"))
    return out


def _note_category(note: str) -> str:
    """V0.2: 按 ASCII 词边界 + 优先最长匹配。

    ``re`` 的 ``\\b`` 在 Unicode 模式下会把中文字符视为 ``\\w``，
    导致 ``labor`` 与 ``labor付款`` 中之间不存在词边界。
    这里用显式 ASCII 字符否定环视，强制要求两侧为非 ASCII
    英文字母数字下划线。
    """
    if not note:
        return "未分类"
    note_l = note.lower()
    for c in sorted(COST_CATEGORIES, key=len, reverse=True):
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(c)}(?![A-Za-z0-9_])"
        if re.search(pattern, note_l):
            return c
    return "未分类"


def matching_rows(db: Session, pid: int) -> list[dict[str, Any]]:
    """合同 → 履约 → 发票 → 付款 四流匹配。"""
    contracts = db.execute(
        select(Contract).where(Contract.project_id == pid)
    ).scalars().all()
    fulfill = db.execute(
        select(Fulfillment).where(Fulfillment.project_id == pid)
    ).scalars().all()
    invoices = db.execute(
        select(Invoice).where(
            Invoice.project_id == pid, Invoice.direction == "in"
        )
    ).scalars().all()
    cash = db.execute(
        select(CashFlow).where(
            CashFlow.project_id == pid, CashFlow.direction == "out"
        )
    ).scalars().all()

    thresholds = _load_thresholds(db)
    inv_ratio, _ = thresholds["invoice_over_contract"]
    pay_ratio, _ = thresholds["paid_over_invoice"]
    ful_ratio, _ = thresholds["fulfilled_over_contract"]

    keys: set[tuple[str, str]] = set()
    csum: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))
    fsum: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))
    isum: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))
    psum: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))
    evidence: dict[tuple[str, str], bool] = defaultdict(lambda: True)

    for c in contracts:
        key = (c.seller_code, c.category)
        keys.add(key)
        csum[key] += _zero(c.amount)

    for f in fulfill:
        cat = f.category or kind_to_category(f.kind)
        key = (f.counterparty_code, cat)
        keys.add(key)
        fsum[key] += _zero(f.amount)
        evidence[key] = evidence[key] and bool(f.evidence_complete)

    for i in invoices:
        key = (i.counterparty_code, i.category)
        keys.add(key)
        isum[key] += _zero(i.net) + _zero(i.vat)

    for x in cash:
        cat = _note_category(x.note)
        key = (x.counterparty_code, cat)
        # V0.2: 不再"自动归类到唯一 category"，保留 unclassified
        keys.add(key)
        psum[key] += _zero(x.amount)

    rows: list[dict[str, Any]] = []
    for cp, cat in sorted(keys, key=lambda k: (k[0], k[1])):
        contract = csum[(cp, cat)]
        fulfilled = fsum[(cp, cat)]
        invoice = isum[(cp, cat)]
        paid = psum[(cp, cat)]
        flags: list[str] = []

        if cat == "未分类" and (paid > 0):
            flags.append(f"付款 {paid} 未携带成本类别")

        if invoice and not contract:
            flags.append("无合同发票")
        if paid and not invoice:
            flags.append("付款未见发票")
        if fulfilled and not evidence[(cp, cat)]:
            flags.append("履约证据不完整")
        if contract and invoice > contract * inv_ratio:
            flags.append("发票超过合同")
        if invoice and paid > invoice * pay_ratio:
            flags.append("付款超过发票")
        if contract and fulfilled > contract * ful_ratio:
            flags.append("履约结算超过合同")

        rows.append({
            "counterparty": cp,
            "category": cat,
            "contract": contract,
            "fulfillment": fulfilled,
            "invoice": invoice,
            "paid": paid,
            "evidence_ok": evidence[(cp, cat)],
            "status": "正常" if not flags else "；".join(flags),
        })
    return rows


__all__ = ["matching_rows", "_note_category"]