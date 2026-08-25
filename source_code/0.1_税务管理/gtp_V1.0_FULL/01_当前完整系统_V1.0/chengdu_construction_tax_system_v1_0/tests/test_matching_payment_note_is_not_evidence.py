"""Regression tests: payment-note text must never become deterministic evidence."""
from __future__ import annotations

from decimal import Decimal


def test_payment_note_category_guess_does_not_close_invoice_category(seeded_app):
    from app.calc.matching import matching_rows
    from app.db import SessionLocal
    from app.models import CashFlow, Contract, Invoice, Project

    with SessionLocal() as db:
        project = Project(
            code="P0-NOTE-NOT-EVIDENCE",
            name="付款备注不可作为证据",
            city="成都",
            contract_total=Decimal("1000"),
            tax_method="general",
        )
        db.add(project)
        db.flush()

        db.add(
            Contract(
                project_id=project.id,
                contract_no="P0-C-001",
                buyer_code="A01",
                seller_code="EXT-P0",
                category="材料",
                amount=Decimal("100"),
                internal_trade=False,
            )
        )
        db.add(
            Invoice(
                project_id=project.id,
                invoice_no="P0-I-001",
                period="2026-08",
                entity_code="A01",
                direction="in",
                counterparty_code="EXT-P0",
                category="材料",
                net=Decimal("100"),
                vat=Decimal("0"),
                rate=Decimal("0"),
                deductible=True,
            )
        )
        db.add(
            CashFlow(
                project_id=project.id,
                entity_code="A01",
                counterparty_code="EXT-P0",
                direction="out",
                amount=Decimal("100"),
                period="2026-08",
                note="材料款",
            )
        )
        db.commit()
        project_id = project.id

    with SessionLocal() as db:
        rows = matching_rows(db, project_id)

    material = next(row for row in rows if row["category"] == "材料")
    unclassified = next(row for row in rows if row["category"] == "未分类")

    # A free-text note saying “材料” must not make the deterministic 材料 row paid.
    assert material["contract"] == Decimal("100")
    assert material["invoice"] == Decimal("100")
    assert material["paid"] == Decimal("0")
    assert material["paid_ok"] is False

    # The payment remains visible, but explicitly unclassified and review-only.
    assert unclassified["paid"] == Decimal("100")
    assert unclassified["paid_ok"] is True
    assert "备注猜测“材料”仅供人工复核" in unclassified["status"]
    assert "尚未关联到具体发票/成本类别" in unclassified["status"]
