"""Task12 Project Tax Treatment / Tax Prepayment contracts."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import engine
from app.domain.project_tax import (
    ProjectTaxRuleError,
    ProjectTaxTreatmentView,
    TaxPrepaymentView,
    resolve_project_tax_treatment,
    validated_tax_prepayment_total,
)
from app.models import Project
from app.v3_fact_models import Fact
from app.v3_party_models import InternalEntity
from app.v3_project_tax_models import ProjectTaxTreatment, TaxPrepaymentFact

ROOT = Path(__file__).resolve().parents[1]


def test_revision_82_is_additive_and_has_no_rate_columns():
    migration = (
        ROOT / "alembic" / "versions" / "82_v3_project_tax_prepayment.py"
    ).read_text(encoding="utf-8")
    upper = migration.upper()
    assert 'down_revision = "81_v3_contract_fulfillment_facts"' in migration
    assert '"project_tax_treatments"' in migration
    assert '"tax_prepayment_facts"' in migration
    assert '"prepayment_rate"' not in migration
    assert 'sa.Column("rate"' not in migration
    assert "ALTER TABLE PROJECTS" not in upper
    assert "ALTER TABLE CASHFLOWS" not in upper
    assert "DROP TABLE PROJECTS" not in upper


def test_project_tax_schema_keeps_tax_cash_accrual_axes_separate():
    treatment = ProjectTaxTreatment.__table__
    prepayment = TaxPrepaymentFact.__table__

    assert {"rate", "prepayment_rate", "cashflow_id", "bank_transaction_id"}.isdisjoint(
        treatment.c.keys()
    )
    assert {
        "rate",
        "prepayment_rate",
        "cashflow_id",
        "bank_transaction_id",
        "invoice_fact_id",
        "recognized_revenue",
        "cost_amount",
    }.isdisjoint(prepayment.c.keys())

    treatment_targets = {
        element.target_fullname
        for constraint in treatment.foreign_key_constraints
        for element in constraint.elements
    }
    assert "projects.id" in treatment_targets
    assert "internal_entities.party_id" in treatment_targets

    prepayment_targets = {
        element.target_fullname
        for constraint in prepayment.foreign_key_constraints
        for element in constraint.elements
    }
    assert "facts.id" in prepayment_targets
    assert "projects.id" in prepayment_targets
    assert "internal_entities.party_id" in prepayment_targets
    assert "project_tax_treatments.id" in prepayment_targets


def test_project_tax_treatment_resolution_is_reviewed_and_deterministic():
    rows = (
        ProjectTaxTreatmentView(
            id=1,
            project_id=10,
            tax_type="VAT_PREPAYMENT",
            treatment_code="RULE-A",
            reporting_party_id=7,
            effective_from=date(2026, 1, 1),
            effective_to=None,
            reviewed=True,
        ),
        ProjectTaxTreatmentView(
            id=2,
            project_id=11,
            tax_type="VAT_PREPAYMENT",
            treatment_code="RULE-B",
            reporting_party_id=8,
            effective_from=date(2026, 1, 1),
            effective_to=None,
            reviewed=False,
        ),
    )
    resolved = resolve_project_tax_treatment(
        rows,
        project_id=10,
        tax_type="VAT_PREPAYMENT",
        as_of_date=date(2026, 8, 1),
    )
    assert resolved is not None and resolved.id == 1
    assert (
        resolve_project_tax_treatment(
            rows,
            project_id=11,
            tax_type="VAT_PREPAYMENT",
            as_of_date=date(2026, 8, 1),
        )
        is None
    )

    ambiguous = rows + (
        ProjectTaxTreatmentView(
            id=3,
            project_id=10,
            tax_type="VAT_PREPAYMENT",
            treatment_code="RULE-C",
            reporting_party_id=7,
            effective_from=date(2026, 6, 1),
            effective_to=None,
            reviewed=True,
        ),
    )
    with pytest.raises(ProjectTaxRuleError):
        resolve_project_tax_treatment(
            ambiguous,
            project_id=10,
            tax_type="VAT_PREPAYMENT",
            as_of_date=date(2026, 8, 1),
        )


def test_tax_prepayment_projection_uses_only_current_valid_tax_facts():
    facts = (
        TaxPrepaymentView(1, 7, "VAT_PREPAYMENT", date(2026, 8, 1), Decimal("100.00"), "PREPAYMENT", "VALID"),
        TaxPrepaymentView(1, 7, "VAT_PREPAYMENT", date(2026, 8, 1), Decimal("-20.00"), "REVERSAL", "VALID"),
        TaxPrepaymentView(1, 7, "VAT_PREPAYMENT", date(2026, 8, 1), Decimal("50.00"), "PREPAYMENT", "NEEDS_REVIEW"),
        TaxPrepaymentView(2, 7, "VAT_PREPAYMENT", date(2026, 8, 1), Decimal("30.00"), "PREPAYMENT", "VALID"),
        TaxPrepaymentView(1, 7, "VAT_PREPAYMENT", date(2026, 8, 1), Decimal("10.00"), "PREPAYMENT", "VALID", False),
    )
    assert validated_tax_prepayment_total(
        facts,
        reporting_party_id=7,
        tax_type="VAT_PREPAYMENT",
        tax_period=date(2026, 8, 31),
        project_id=1,
    ) == Decimal("80.00")


def test_postgresql_treatment_windows_cannot_overlap(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            project = session.query(Project).order_by(Project.id).first()
            entity = session.query(InternalEntity).order_by(InternalEntity.party_id).first()
            assert project is not None and entity is not None
            session.add(
                ProjectTaxTreatment(
                    project_id=project.id,
                    tax_type="VAT_PREPAYMENT",
                    treatment_code="T12-A",
                    reporting_party_id=entity.party_id,
                    effective_from=date(2026, 1, 1),
                    effective_to=date(2026, 12, 31),
                    rule_version="T12-R1",
                    source="pytest",
                    reviewed=True,
                )
            )
            session.flush()

            nested = session.begin_nested()
            session.add(
                ProjectTaxTreatment(
                    project_id=project.id,
                    tax_type="VAT_PREPAYMENT",
                    treatment_code="T12-B",
                    reporting_party_id=entity.party_id,
                    effective_from=date(2026, 6, 1),
                    effective_to=None,
                    rule_version="T12-R2",
                    source="pytest",
                    reviewed=True,
                )
            )
            with pytest.raises(IntegrityError):
                session.flush()
            nested.rollback()
        finally:
            session.close()
            tx.rollback()


def test_postgresql_prepayment_event_sign_is_enforced(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            project = session.query(Project).order_by(Project.id).first()
            entity = session.query(InternalEntity).order_by(InternalEntity.party_id).first()
            assert project is not None and entity is not None
            fact = Fact(
                fact_type="TAX_PREPAYMENT",
                business_identity_key="T12|SIGN|1",
                validation_status="NEEDS_REVIEW",
            )
            session.add(fact)
            session.flush()

            nested = session.begin_nested()
            session.add(
                TaxPrepaymentFact(
                    fact_id=fact.id,
                    project_id=project.id,
                    reporting_party_id=entity.party_id,
                    tax_type="VAT_PREPAYMENT",
                    tax_period=date(2026, 8, 1),
                    tax_event_date=date(2026, 8, 20),
                    taxable_base=Decimal("1000.00"),
                    tax_amount=Decimal("-20.00"),
                    event_type="PREPAYMENT",
                    currency="CNY",
                    source_system="pytest",
                    external_reference="T12-SIGN-BAD",
                )
            )
            with pytest.raises(IntegrityError):
                session.flush()
            nested.rollback()
        finally:
            session.close()
            tx.rollback()
