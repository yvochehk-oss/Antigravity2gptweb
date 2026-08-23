"""Regression tests for canonical legal-entity tax scope and branch roll-up."""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


@pytest.fixture
def tax_db():
    from app.db import Base
    from app.models import Entity, Project, TaxRule

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all(
            [
                _entity(Entity, "A03"),
                _entity(Entity, "A04", legal_entity=False, parent_entity_code="A03"),
                # ``internal`` is a deprecated compatibility field and must
                # not decide the canonical legal-entity tax scope.
                _entity(Entity, "A08", internal=False),
                _entity(Entity, "A09"),
                _entity(Entity, "B01", active=False),
                _entity(Entity, "A10", internal=False, legal_entity=False),
            ]
        )
        db.add(
            TaxRule(
                code="CIT_GENERAL",
                rate=Decimal("0.25"),
                effective_from="2026-01-01",
                reviewed=True,
            )
        )
        db.add(
            Project(
                code="TAX-REGRESSION",
                name="税务台账回归项目",
                city="成都",
                contract_total=Decimal("1000000000.00"),
                tax_method="general",
            )
        )
        db.commit()
        yield db
    engine.dispose()


def _entity(
    entity_type,
    code: str,
    *,
    internal: bool = True,
    legal_entity: bool = True,
    parent_entity_code: str = "",
    active: bool = True,
):
    return entity_type(
        code=code,
        name=f"测试企业-{code}",
        short_name=code,
        kind="construction",
        business_role="A",
        internal=internal,
        legal_entity=legal_entity,
        parent_entity_code=parent_entity_code,
        active=active,
        tax_id=f"TAX-{code}",
    )


def _project_id(db: Session) -> int:
    from app.models import Project

    return db.scalar(select(Project.id).where(Project.code == "TAX-REGRESSION"))


def test_rebuild_rolls_branch_into_parent_and_keeps_zero_rows(tax_db):
    from app.calc.tax import rebuild_tax_ledger
    from app.models import Invoice, RealCost

    project_id = _project_id(tax_db)
    tax_db.add_all(
        [
            Invoice(
                project_id=project_id,
                invoice_no="OUT-A03",
                period="2026-08",
                entity_code="A03",
                direction="out",
                counterparty_code="CUSTOMER-1",
                category="construction",
                net=Decimal("100.00"),
                vat=Decimal("9.00"),
                rate=Decimal("0.09"),
                deductible=False,
            ),
            Invoice(
                project_id=project_id,
                invoice_no="IN-A04",
                period="2026-08",
                entity_code="A04",
                direction="in",
                counterparty_code="SUPPLIER-1",
                category="material",
                net=Decimal("20.00"),
                vat=Decimal("2.60"),
                rate=Decimal("0.13"),
                deductible=True,
            ),
            Invoice(
                project_id=project_id,
                invoice_no="OUT-A08-LARGE",
                period="2026-08",
                entity_code="A08",
                direction="out",
                counterparty_code="CUSTOMER-2",
                category="construction",
                net=Decimal("999999999.99"),
                vat=Decimal("89999999.99"),
                rate=Decimal("0.09"),
                deductible=False,
            ),
            RealCost(
                project_id=project_id,
                entity_code="A04",
                counterparty_code="",
                category="material",
                subcategory="branch-cost",
                period="2026-08",
                amount=Decimal("5.00"),
                external_cash=True,
            ),
        ]
    )
    tax_db.commit()

    rows = rebuild_tax_ledger(tax_db, "2026-08")
    by_code = {row.entity_code: row for row in rows}

    assert set(by_code) == {"A03", "A08", "A09"}
    assert by_code["A03"].output_vat == Decimal("9.00")
    assert by_code["A03"].input_vat == Decimal("2.60")
    assert by_code["A03"].revenue == Decimal("100.00")
    assert by_code["A03"].real_cost == Decimal("25.00")
    assert by_code["A03"].estimated_profit == Decimal("75.00")
    assert by_code["A03"].estimated_cit == Decimal("18.75")
    assert by_code["A08"].revenue == Decimal("999999999.99")
    assert by_code["A09"].revenue == Decimal("0.00")
    assert by_code["A09"].real_cost == Decimal("0.00")
    assert by_code["A09"].estimated_cit == Decimal("0.00")


@pytest.mark.parametrize("invalid_code", ["B01", "A10", "UNKNOWN", ""])
def test_rebuild_rejects_invalid_owner_without_silent_mix(tax_db, invalid_code):
    from app.calc.tax import EntityScopeError, rebuild_tax_ledger
    from app.models import Invoice

    tax_db.add(
        Invoice(
            project_id=_project_id(tax_db),
            invoice_no=f"INVALID-{invalid_code or 'MISSING'}",
            period="2026-08",
            entity_code=invalid_code,
            direction="out",
            counterparty_code="CUSTOMER-INVALID",
            category="construction",
            net=Decimal("1.00"),
            vat=Decimal("0.09"),
            rate=Decimal("0.09"),
            deductible=False,
        )
    )
    tax_db.commit()

    with pytest.raises(EntityScopeError):
        rebuild_tax_ledger(tax_db, "2026-08")
