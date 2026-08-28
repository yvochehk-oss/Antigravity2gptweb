"""Four-flow matching with database-backed business thresholds."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    CashFlow,
    Contract,
    Fulfillment,
    Invoice,
    RiskThreshold,
)
from .business_rules import decimal_rule
from .subjects import kind_to_category


def _zero(d: Decimal | float | int | None) -> Decimal:
    if d is None:
        return Decimal("0")
    return Decimal(str(d))


def _load_thresholds(db: Session) -> dict[str, tuple[Decimal, str]]:
    """Return matching thresholds with DB overrides over versioned baselines.

    ``RiskThreshold`` remains the operator-facing override table.  When an
    override is absent, the baseline comes from ``business_rule_parameters``;
    Python no longer contains hidden financial threshold values.
    """
    required = (
        "invoice_over_contract",
        "paid_over_invoice",
        "fulfilled_over_contract",
    )
    out: dict[str, tuple[Decimal, str]] = {}
    rows = db.execute(
        select(RiskThreshold).where(RiskThreshold.enabled == True)  # noqa: E712
    ).scalars().all()
    for row in rows:
        if row.code in required:
            out[row.code] = (_zero(row.ratio), row.severity)

    for code in required:
        if code not in out:
            out[code] = (decimal_rule(db, "matching", code), "YELLOW")
    return out


# Payment-note text is warning metadata only; it never establishes evidence.
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
    """Guess a category for warning text only, never for matching evidence."""
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
    """Return deterministic four-flow evidence completeness, not health."""
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
    """Match contract -> fulfillment -> invoice -> payment evidence.

    CashFlow has no deterministic category link in the current schema, so note
    text is never allowed to place a payment into a contract/invoice category.
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
    payment_note_guesses: dict[tuple[str, str], set[str]] = defaultdict(set)
    contract_evidence: set[tuple[str, str]] = set()
    invoice_evidence: set[tuple[str, str]] = set()
    payment_evidence: set[tuple[str, str]] = set()
    evidence: dict[tuple[str, str], bool] = defaultdict(lambda: False)

    for contract_row in contracts:
        key = (contract_row.seller_code, contract_row.category)
        keys.add(key)
        csum[key] += _zero(contract_row.amount)
        contract_evidence.add(key)

    for fulfillment_row in fulfill:
        cat = fulfillment_row.category or kind_to_category(fulfillment_row.kind)
        key = (fulfillment_row.counterparty_code, cat)
        keys.add(key)
        fsum[key] += _zero(fulfillment_row.amount)
        if fulfillment_row.evidence_complete:
            evidence[key] = True

    for invoice_row in invoices:
        key = (invoice_row.counterparty_code, invoice_row.category)
        keys.add(key)
        isum[key] += _zero(invoice_row.net) + _zero(invoice_row.vat)
        invoice_evidence.add(key)

    invoice_counterparties = {row.counterparty_code for row in invoices}

    for cash_row in cash:
        key = (cash_row.counterparty_code, "未分类")
        keys.add(key)
        psum[key] += _zero(cash_row.amount)
        payment_evidence.add(key)
        guessed = _note_category(cash_row.note)
        if guessed != "未分类":
            payment_note_guesses[key].add(guessed)

    rows: list[dict[str, Any]] = []
    for cp, cat in sorted(keys, key=lambda key: (key[0], key[1])):
        contract = csum[(cp, cat)]
        fulfilled = fsum[(cp, cat)]
        invoice = isum[(cp, cat)]
        paid = psum[(cp, cat)]
        flags: list[str] = []

        if cat == "未分类" and paid > 0:
            guesses = sorted(payment_note_guesses.get((cp, cat), set()))
            if guesses:
                flags.append(
                    "付款未关联确定性成本类别；备注猜测“"
                    + "、".join(guesses)
                    + "”仅供人工复核"
                )
            else:
                flags.append("付款未关联确定性成本类别")
            if cp in invoice_counterparties:
                flags.append("付款尚未关联到具体发票/成本类别")
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
