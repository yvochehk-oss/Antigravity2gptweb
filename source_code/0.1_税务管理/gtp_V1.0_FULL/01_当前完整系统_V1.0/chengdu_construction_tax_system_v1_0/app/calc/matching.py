"""V0.2: 四流匹配 + 风险阈值表驱动。"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..constants import DEFAULT_RISK_THRESHOLDS
from ..models import (
    CashFlow,
    Contract,
    Fulfillment,
    Invoice,
    RiskThreshold,
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


# 付款备注文本仅用于警告提示，不可作为履约证据来源。
# 文本猜测可靠性低，分类必须依赖显式关联记录。
_CATEGORY_KEYWORDS: list[tuple[str, str]] = [
    ("project_management", "项目管理"),
    ("subcontract", "专业分包"),
    ("equipment", "设备"),
    ("material", "材料"),
    ("labor", "劳务"),
    ("tax", "税金"),
    ("项目管理", "项目管理"),
    ("专业分包", "专业分包"),
    ("分包", "专业分包"),
    ("设备", "设备"),
    ("材料", "材料"),
    ("劳务", "劳务"),
    ("税金", "税金"),
    ("税费", "税金"),
]


def _note_category(note: str) -> str:
    """V0.2: 按 ASCII 词边界 + 优先最长匹配（仅警告用途，不作为证据）。

    ``re`` 的 ``\\b`` 在 Unicode 模式下会把中文字符视为 ``\\w``，
    导致 ``labor`` 与 ``labor付款`` 中之间不存在词边界。
    这里用显式 ASCII 字符否定环视，强制要求两侧为非 ASCII
    英文字母数字下划线。
    """
    if not note:
        return "未分类"
    note_l = note.lower()
    for kw, cat in _CATEGORY_KEYWORDS:
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(kw)}(?![A-Za-z0-9_])"
        if re.search(pattern, note_l):
            return cat
    return "未分类"


_FOUR_FLOW_NAMES = ("contract", "fulfillment", "invoice", "paid")


def four_flow_evidence_completeness(
    rows: list[dict[str, Any]],
    *,
    evaluated_at: str | None = None,
) -> dict[str, Any]:
    """Return the deterministic completeness contract for matching rows.

    This is deliberately an *evidence completeness* measure, not a project
    health score.  Every matching row has four expected evidence items:
    contract, fulfillment, invoice and payment.  The numerator is the number
    of explicit evidence flags present across those rows and the denominator
    is ``len(rows) * 4``.  A project with no matching rows cannot be treated as
    a measured zero; it is ``UNAVAILABLE`` and its score/percentage are null.

    ``matching_rows`` emits the explicit ``*_ok`` flags.  The amount-based
    fallback keeps this helper safe for old persisted/test payloads while the
    fulfillment flow still requires the existing ``evidence_ok`` flag.
    """
    row_count = len(rows)
    expected_per_flow = row_count
    expected = expected_per_flow * len(_FOUR_FLOW_NAMES)
    by_flow: dict[str, dict[str, int]] = {}

    for flow in _FOUR_FLOW_NAMES:
        available = 0
        for row in rows:
            explicit_key = f"{flow}_ok"
            if explicit_key in row:
                present = bool(row[explicit_key])
            elif flow == "fulfillment":
                # ``evidence_ok`` is the established flag and cannot be
                # inferred from the fulfillment amount alone.
                present = bool(row.get("evidence_ok", False))
            else:
                present = _zero(row.get(flow)) > Decimal("0")
            available += int(present)
        by_flow[flow] = {
            "expected": expected_per_flow,
            "available": available,
            "missing": expected_per_flow - available,
        }

    available_total = sum(item["available"] for item in by_flow.values())
    missing_total = expected - available_total
    if row_count == 0:
        status = "UNAVAILABLE"
        percentage: float | None = None
        data_gaps = ["NO_MATCHING_ROWS"]
    else:
        status = "AVAILABLE" if missing_total == 0 else "DEGRADED"
        percentage = round(available_total / expected * 100, 2)
        data_gaps = [
            f"MISSING_{flow.upper()}_EVIDENCE"
            for flow in _FOUR_FLOW_NAMES
            if by_flow[flow]["missing"]
        ]

    # ``score`` intentionally remains null.  No approved deterministic
    # project-health formula exists; callers must use ``percentage`` only as
    # four-flow evidence completeness and must not label it project health.
    return {
        "status": status,
        "score": None,
        "percentage": percentage,
        "counts": {
            "rows": row_count,
            "expected_evidence": expected,
            "available_evidence": available_total,
            "missing_evidence": missing_total,
            "by_flow": by_flow,
        },
        "data_gaps": data_gaps,
        "updated": evaluated_at or datetime.now(timezone.utc).isoformat(),
        "source": "tax.deterministic.matching_rows",
    }



def matching_rows(db: Session, pid: int) -> list[dict[str, Any]]:
    """合同 → 履约 → 发票 → 付款 四流匹配。

    Evidence is only True when explicit fulfillment records exist with
    ``evidence_complete=True``. Text-based category guessing from payment
    notes is used only for flagging unclassified payments, not as evidence.
    """
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
    payment_note_guesses: set[tuple[str, str]] = set()
    contract_evidence: set[tuple[str, str]] = set()
    invoice_evidence: set[tuple[str, str]] = set()
    payment_evidence: set[tuple[str, str]] = set()
    # P1-08: evidence defaults to False; only explicit evidence_complete=True
    # from a Fulfillment record can make it True.
    evidence: dict[tuple[str, str], bool] = defaultdict(lambda: False)

    for c in contracts:
        key = (c.seller_code, c.category)
        keys.add(key)
        csum[key] += _zero(c.amount)
        contract_evidence.add(key)

    for f in fulfill:
        cat = f.category or kind_to_category(f.kind)
        key = (f.counterparty_code, cat)
        keys.add(key)
        fsum[key] += _zero(f.amount)
        # Only mark evidence as True when there is an explicit fulfillment record
        # with evidence_complete set to True.
        if f.evidence_complete:
            evidence[key] = True

    for i in invoices:
        key = (i.counterparty_code, i.category)
        keys.add(key)
        isum[key] += _zero(i.net) + _zero(i.vat)
        invoice_evidence.add(key)

    # Build a map from (counterparty_code, category) to True for invoice
    # categories so payments can be validated against actual invoice records
    # rather than relying on unreliable text-based note guessing.
    invoiced_categories: set[tuple[str, str]] = set()
    for i in invoices:
        invoiced_categories.add((i.counterparty_code, i.category))

    for x in cash:
        # Text-based category is a warning signal only; the payment category
        # defaults to "未分类" when the note contains no recognized keyword.
        guessed = _note_category(x.note)
        key = (x.counterparty_code, guessed)
        keys.add(key)
        psum[key] += _zero(x.amount)
        payment_evidence.add(key)
        if guessed != "未分类":
            payment_note_guesses.add(key)

    rows: list[dict[str, Any]] = []
    for cp, cat in sorted(keys, key=lambda k: (k[0], k[1])):
        contract = csum[(cp, cat)]
        fulfilled = fsum[(cp, cat)]
        invoice = isum[(cp, cat)]
        paid = psum[(cp, cat)]
        flags: list[str] = []

        if cat == "未分类" and (paid > 0):
            flags.append(f"付款 {paid} 未携带成本类别（文本猜测不可作为证据）")

        # Use the actual invoice category set rather than text-based guessing
        # to validate whether a payment is backed by an invoice record.
        if paid > 0 and (cp, cat) not in invoiced_categories:
            # If the payment note did contain a recognized category keyword,
            # still warn because the note text is unreliable evidence.
            if (cp, cat) in payment_note_guesses:
                flags.append(
                    "付款类别来自文本猜测，可靠性不足，请关联发票或合同"
                )
            else:
                flags.append("付款未见发票记录")

        if invoice and not contract:
            flags.append("无合同发票")
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
            "contract_ok": (cp, cat) in contract_evidence,
            "fulfillment_ok": evidence[(cp, cat)],
            "invoice_ok": (cp, cat) in invoice_evidence,
            "paid_ok": (cp, cat) in payment_evidence,
            "evidence_ok": evidence[(cp, cat)],
            "status": "正常" if not flags else "；".join(flags),
        })
    return rows


__all__ = [
    "matching_rows",
    "four_flow_evidence_completeness",
    "_note_category",
]
