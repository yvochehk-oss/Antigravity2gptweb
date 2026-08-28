"""P1 regression tests for Invoice/RealCost anti-double-counting provenance."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import delete, select, text


def _ledger_cost(rows, entity_code: str) -> Decimal:
    return next(row.real_cost for row in rows if row.entity_code == entity_code)


def test_equal_invoice_and_real_cost_require_explicit_provenance(seeded_app):
    """Equal business attributes alone must never collapse two source records."""
    from app.calc import rebuild_tax_ledger
    from app.db import SessionLocal
    from app.models import Entity, Invoice, Progress, Project, RealCost

    db = SessionLocal()
    invoice = None
    real_cost = None
    try:
        progress = db.scalar(select(Progress).order_by(Progress.id))
        project = db.scalar(select(Project).order_by(Project.id))
        entity = db.scalar(
            select(Entity)
            .where(Entity.active.is_(True), Entity.legal_entity.is_(True))
            .order_by(Entity.code)
        )
        assert progress is not None
        assert project is not None
        assert entity is not None

        period = progress.period
        entity_code = entity.code
        amount = Decimal("12345.67")

        baseline = _ledger_cost(rebuild_tax_ledger(db, period), entity_code)

        invoice = Invoice(
            project_id=project.id,
            invoice_no="P1-PROVENANCE-INVOICE",
            period=period,
            entity_code=entity_code,
            direction="in",
            counterparty_code="P1-PROVENANCE-CP",
            category="材料",
            net=amount,
            vat=Decimal("0"),
            rate=Decimal("0"),
            deductible=True,
            note="P1 provenance regression",
        )
        real_cost = RealCost(
            project_id=project.id,
            entity_code=entity_code,
            counterparty_code="P1-PROVENANCE-CP",
            category="材料",
            subcategory="regression",
            period=period,
            amount=amount,
            external_cash=True,
            note="P1 provenance regression",
        )
        db.add_all([invoice, real_cost])
        db.commit()
        db.refresh(invoice)
        db.refresh(real_cost)

        # Same owner/counterparty/period/amount is not provenance.  Both rows
        # must remain in cost until an explicit source relationship exists.
        without_link = _ledger_cost(rebuild_tax_ledger(db, period), entity_code)
        assert without_link == baseline + amount + amount

        db.execute(
            text(
                "INSERT INTO real_cost_invoice_links "
                "(real_cost_id, invoice_id, source, source_record_id, source_fingerprint) "
                "VALUES (:real_cost_id, :invoice_id, :source, :source_record_id, :fingerprint)"
            ),
            {
                "real_cost_id": real_cost.id,
                "invoice_id": invoice.id,
                "source": "pytest",
                "source_record_id": "P1-PROVENANCE-PAIR",
                "fingerprint": "p1-explicit-provenance",
            },
        )
        db.commit()

        # Once explicitly linked, RealCost is known to be a second
        # representation of the input invoice and is excluded exactly once.
        with_link = _ledger_cost(rebuild_tax_ledger(db, period), entity_code)
        assert with_link == baseline + amount
    finally:
        if real_cost is not None and real_cost.id is not None:
            db.execute(
                text("DELETE FROM real_cost_invoice_links WHERE real_cost_id = :id"),
                {"id": real_cost.id},
            )
            db.execute(delete(RealCost).where(RealCost.id == real_cost.id))
        if invoice is not None and invoice.id is not None:
            db.execute(delete(Invoice).where(Invoice.id == invoice.id))
        db.commit()
        # Restore the period ledger so session-scoped seeded_app stays stable
        # for tests that run after this regression case.
        if "period" in locals():
            rebuild_tax_ledger(db, period)
        db.close()


def test_tax_source_no_longer_uses_composite_value_identity():
    """Guard against reintroducing the old value-equality heuristic."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "app" / "calc" / "tax.py"
    ).read_text(encoding="utf-8")
    assert "real_cost_invoice_links" in source
    assert "covered_invoice_keys" not in source
    assert "_input_invoice_key" not in source
