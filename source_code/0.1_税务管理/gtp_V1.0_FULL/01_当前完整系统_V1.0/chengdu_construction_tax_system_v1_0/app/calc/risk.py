"""Deterministic risk scanning backed by versioned business rules."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..constants import (
    CATEGORY_LABELS,
    RISK_CODE_EQUIPMENT_RATE_REVIEW,
    RISK_CODE_FOUR_STREAM_MISMATCH,
    RISK_CODE_MISSING_TAX_RATE,
)
from ..models import Invoice, RiskEvent
from .business_rules import BUSINESS_RULE_VERSION, decimal_list_rule
from .matching import matching_rows

RISK_SOURCE = "deterministic_risk_scan"
RISK_RULE_VERSION = BUSINESS_RULE_VERSION


def _zero(value) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def scan_risks(db: Session, pid: int) -> list[RiskEvent]:
    """Replace only rows owned by this deterministic scanner/rule version."""
    db.execute(
        text(
            "DELETE FROM risk_events "
            "WHERE project_id = :pid AND source = :source AND rule_version = :rule_version"
        ),
        {"pid": pid, "source": RISK_SOURCE, "rule_version": RISK_RULE_VERSION},
    )

    equipment_allowed_rates = set(
        decimal_list_rule(db, "risk", "equipment_allowed_invoice_rates")
    )

    new_events: list[dict[str, object]] = []
    rows = matching_rows(db, pid)
    for r in rows:
        if r["status"] == "正常":
            continue
        sev = "RED" if (
            "无合同发票" in r["status"] or "付款未见发票" in r["status"]
        ) else "YELLOW"
        new_events.append({
            "project_id": pid,
            "severity": sev,
            "code": RISK_CODE_FOUR_STREAM_MISMATCH,
            "message": (
                f'{r["counterparty"]}/'
                f'{CATEGORY_LABELS.get(r["category"], r["category"])}: '
                f'{r["status"]}'
            ),
        })

    invs = db.execute(
        select(Invoice).where(Invoice.project_id == pid)
    ).scalars().all()
    for i in invs:
        rate = _zero(i.rate)
        if i.direction == "in" and _zero(i.vat) > 0 and rate <= 0:
            new_events.append({
                "project_id": pid,
                "severity": "YELLOW",
                "code": RISK_CODE_MISSING_TAX_RATE,
                "message": f"发票{i.invoice_no or i.id}存在VAT但未填写税率",
            })
        if i.category == "设备" and rate not in equipment_allowed_rates:
            new_events.append({
                "project_id": pid,
                "severity": "YELLOW",
                "code": RISK_CODE_EQUIPMENT_RATE_REVIEW,
                "message": f"设备业务发票{i.invoice_no or i.id}税率需复核",
            })

    if new_events:
        db.execute(
            text(
                "INSERT INTO risk_events "
                "(project_id, severity, code, message, resolved, source, rule_version) "
                "VALUES (:project_id, :severity, :code, :message, false, :source, :rule_version)"
            ),
            [
                {
                    **event,
                    "source": RISK_SOURCE,
                    "rule_version": RISK_RULE_VERSION,
                }
                for event in new_events
            ],
        )

    db.commit()
    return db.execute(
        select(RiskEvent).where(RiskEvent.project_id == pid)
        .order_by(RiskEvent.id.desc())
    ).scalars().all()


__all__ = ["scan_risks"]
