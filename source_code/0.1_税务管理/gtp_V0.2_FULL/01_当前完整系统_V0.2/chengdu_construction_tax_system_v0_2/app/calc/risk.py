"""V0.2: 风险扫描（确定性规则）。"""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..constants import CATEGORY_LABELS
from ..models import Invoice, RiskEvent
from .matching import matching_rows


def scan_risks(db: Session, pid: int) -> list[RiskEvent]:
    db.execute(delete(RiskEvent).where(RiskEvent.project_id == pid))

    rows = matching_rows(db, pid)
    for r in rows:
        if r["status"] == "正常":
            continue
        sev = "RED" if (
            "无合同发票" in r["status"] or "付款未见发票" in r["status"]
        ) else "YELLOW"
        db.add(RiskEvent(
            project_id=pid, severity=sev, code="四流不匹配",
            message=f'{r["counterparty"]}/{CATEGORY_LABELS.get(r["category"], r["category"])}: {r["status"]}',
        ))

    invs = db.execute(
        select(Invoice).where(Invoice.project_id == pid)
    ).scalars().all()
    for i in invs:
        if i.direction == "in" and float(i.vat) > 0 and float(i.rate) <= 0:
            db.add(RiskEvent(
                project_id=pid, severity="YELLOW", code="发票缺失税率",
                message=f"发票{i.invoice_no or i.id}存在VAT但未填写税率",
            ))
        if i.category == "设备" and float(i.rate) not in (0.09, 0.13, 0.03, 0):
            db.add(RiskEvent(
                project_id=pid, severity="YELLOW",
                code="设备税率需复核",
                message=f"设备业务发票{i.invoice_no or i.id}税率需复核",
            ))

    db.commit()
    return db.execute(
        select(RiskEvent).where(RiskEvent.project_id == pid)
        .order_by(RiskEvent.id.desc())
    ).scalars().all()


__all__ = ["scan_risks"]