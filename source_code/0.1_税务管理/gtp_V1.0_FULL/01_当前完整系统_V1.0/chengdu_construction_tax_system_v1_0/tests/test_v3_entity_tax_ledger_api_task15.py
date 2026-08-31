"""Task15 API projection contracts for the current Entity Tax Ledger."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import json
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import (
    EntityTaxLedger,
    EntityTaxLedgerComponent,
    EntityTaxManagementInput,
    TaxLedger,
)
from app.routers.collections import (
    _build_v3_entity_tax_ledger_envelope,
    entity_tax_ledger_collection,
)
from app.v3_fact_models import Fact, InvoiceFact
from app.v3_party_models import InternalEntity, Party
from app.v3_period_models import CalculationRun, TaxPeriodState
from app.v3_vat_ledger_models import EntityVatLedger, OutputVatEvent
from app.v3_vat_review_models import VatOutputPeriodAssertion


PERIOD = date(2026, 8, 1)
REVIEWED_AT = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)


def _new_v3_scope(session: Session) -> tuple[Party, EntityTaxLedger]:
    suffix = uuid.uuid4().hex[:12].upper()
    party = Party(
        code=f"T15-{suffix}",
        name=f"Task15 Entity {suffix}",
        short_name="T15",
        party_type="internal",
        active=True,
    )
    session.add(party)
    session.flush()
    session.add(
        InternalEntity(
            party_id=party.id,
            canonical_code=f"T15{suffix[:8]}",
            business_role="A",
            legal_entity=True,
            active=True,
        )
    )
    session.flush()

    vat_run = CalculationRun(
        reporting_party_id=party.id,
        tax_type="VAT",
        tax_period=PERIOD,
        run_kind="STANDARD",
        run_status="SUCCEEDED",
        ruleset_version="TASK15_VAT_TEST_V1",
        input_snapshot_sha256="1" * 64,
        result_sha256="2" * 64,
        created_by="pytest",
        completed_at=REVIEWED_AT,
    )
    entity_run = CalculationRun(
        reporting_party_id=party.id,
        tax_type="ENTITY_TAX",
        tax_period=PERIOD,
        run_kind="STANDARD",
        run_status="SUCCEEDED",
        ruleset_version="TASK15_ENTITY_TEST_V1",
        input_snapshot_sha256="3" * 64,
        result_sha256="4" * 64,
        created_by="pytest",
        completed_at=REVIEWED_AT,
    )
    session.add_all([vat_run, entity_run])
    session.flush()
    invoice_fact_root = Fact(
        fact_type="INVOICE",
        business_identity_key=f"INVOICE|TASK15_API|{suffix}",
        validation_status="VALID",
    )
    session.add(invoice_fact_root)
    session.flush()
    invoice_fact = InvoiceFact(
        fact_id=invoice_fact_root.id,
        seller_party_id=party.id,
        invoice_identity_key=f"TASK15_API|{suffix}",
        invoice_identity_version="TASK15_API_V1",
        invoice_number=f"TASK15-API-{suffix}",
        invoice_date=PERIOD,
        invoice_status="VALID",
        gross_amount=Decimal("112.50"),
        net_amount=Decimal("100.00"),
        vat_amount=Decimal("12.50"),
        currency="CNY",
    )
    session.add(invoice_fact)
    session.flush()
    session.add(
        OutputVatEvent(
            invoice_fact_id=invoice_fact.fact_id,
            reporting_party_id=party.id,
            output_vat_period=PERIOD,
            vat_amount=Decimal("12.50"),
            event_type="OUTPUT",
            event_status="CONFIRMED",
            evidence_type="MANUAL_REVIEW",
            confidence="HIGH",
            reviewed_by="pytest",
            reviewed_at=REVIEWED_AT,
            source_system="task15-api-test",
            external_event_id=f"TASK15-API-{suffix}",
        )
    )
    session.flush()
    session.add(
        TaxPeriodState(
            reporting_party_id=party.id,
            tax_type="ENTITY_TAX",
            tax_period=PERIOD,
            state="OPEN",
            current_run_id=entity_run.id,
        )
    )
    session.add(
        VatOutputPeriodAssertion(
            reporting_party_id=party.id,
            tax_period=PERIOD,
            asserted_output_vat_total=Decimal("12.50"),
            source="Task15 reviewed output VAT fixture",
            reviewed=True,
            reviewed_by="pytest",
            reviewed_at=REVIEWED_AT,
        )
    )
    session.flush()
    vat_ledger = EntityVatLedger(
        calculation_run_id=vat_run.id,
        reporting_party_id=party.id,
        tax_period=PERIOD,
        opening_input_credit=Decimal("5.00"),
        output_vat=Decimal("12.50"),
        input_vat=Decimal("2.50"),
        tax_prepayment=Decimal("1.00"),
        vat_payable_before_prepayment=Decimal("10.00"),
        closing_input_credit=Decimal("0.00"),
        vat_payable_after_prepayment=Decimal("9.00"),
        unapplied_tax_prepayment=Decimal("0.00"),
    )
    session.add(vat_ledger)
    session.flush()

    revenue_input = EntityTaxManagementInput(
        reporting_party_id=party.id,
        tax_period=PERIOD,
        input_type="REVENUE",
        input_version=1,
        amount=Decimal("100.00"),
        source="Task15 reviewed revenue fixture",
        reviewed=True,
        reviewed_by="pytest",
        reviewed_at=REVIEWED_AT,
    )
    cost_input = EntityTaxManagementInput(
        reporting_party_id=party.id,
        tax_period=PERIOD,
        input_type="REAL_COST",
        input_version=1,
        amount=Decimal("40.00"),
        source="Task15 reviewed cost fixture",
        reviewed=True,
        reviewed_by="pytest",
        reviewed_at=REVIEWED_AT,
    )
    session.add_all([revenue_input, cost_input])
    session.flush()
    ledger = EntityTaxLedger(
        calculation_run_id=entity_run.id,
        reporting_party_id=party.id,
        tax_period=PERIOD,
        entity_vat_ledger_id=vat_ledger.id,
        revenue=Decimal("100.00"),
        real_cost=Decimal("40.00"),
        estimated_profit=Decimal("60.00"),
        estimated_cit=Decimal("15.00"),
        rule_version=entity_run.ruleset_version,
        input_snapshot_sha256=entity_run.input_snapshot_sha256,
        result_sha256=entity_run.result_sha256,
    )
    session.add(ledger)
    session.flush()
    session.add_all(
        [
            EntityTaxLedgerComponent(
                ledger_id=ledger.id,
                component_type="REVENUE",
                amount=Decimal("100.00"),
                management_input_id=revenue_input.id,
            ),
            EntityTaxLedgerComponent(
                ledger_id=ledger.id,
                component_type="REAL_COST",
                amount=Decimal("40.00"),
                management_input_id=cost_input.id,
            ),
            EntityTaxLedgerComponent(
                ledger_id=ledger.id,
                component_type="ESTIMATED_CIT",
                amount=Decimal("15.00"),
            ),
        ]
    )
    session.flush()
    return party, ledger


def test_entity_tax_api_projects_current_v3_ledger_and_official_vat(seeded_app):
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        party, ledger = _new_v3_scope(db)
        historical_run = CalculationRun(
            reporting_party_id=party.id,
            tax_type="ENTITY_TAX",
            tax_period=PERIOD,
            run_kind="RESTATEMENT",
            run_status="SUCCEEDED",
            ruleset_version="TASK15_ENTITY_TEST_RESTATEMENT_V1",
            input_snapshot_sha256="5" * 64,
            result_sha256="6" * 64,
            supersedes_run_id=ledger.calculation_run_id,
            created_by="pytest",
            completed_at=REVIEWED_AT,
        )
        db.add(historical_run)
        db.flush()
        db.add(
            EntityTaxLedger(
                calculation_run_id=historical_run.id,
                reporting_party_id=party.id,
                tax_period=PERIOD,
                entity_vat_ledger_id=ledger.entity_vat_ledger_id,
                revenue=Decimal("999.00"),
                real_cost=Decimal("1.00"),
                estimated_profit=Decimal("998.00"),
                estimated_cit=Decimal("249.50"),
                rule_version=historical_run.ruleset_version,
                input_snapshot_sha256=historical_run.input_snapshot_sha256,
                result_sha256=historical_run.result_sha256,
            )
        )
        db.flush()
        db.add(
            TaxLedger(
                period="2026-08",
                entity_code=party.code,
                output_vat=Decimal("999.00"),
                input_vat=Decimal("888.00"),
                vat_payable=Decimal("111.00"),
                revenue=Decimal("9999.00"),
                real_cost=Decimal("9998.00"),
                estimated_profit=Decimal("1.00"),
                estimated_cit=Decimal("0.25"),
                cit_note="legacy row must not be projected",
                generated=True,
            )
        )
        db.flush()

        payload = _build_v3_entity_tax_ledger_envelope(
            db,
            period="2026-08",
            entity=party.code,
            page=1,
            page_size=50,
        )
        assert payload["status"] == "READY"
        assert len(payload["items"]) == 1
        item = payload["items"][0]
        assert item["ledger_id"] == ledger.id
        assert item["calculation_run_id"] == ledger.calculation_run_id
        assert item["run_kind"] == "STANDARD"
        assert item["reporting_party_id"] == party.id
        assert item["entity_code"] == party.code
        assert item["tax_period"] == "2026-08"
        assert item["revenue"] == 100.0
        assert item["real_cost"] == 40.0
        assert item["profit"] == 60.0
        assert item["cit"] == 15.0
        assert item["output_vat"] == 12.5
        assert item["input_vat"] == 2.5
        assert item["vat_payable_after_prepayment"] == 9.0
        assert item["official_vat_ledger_id"] == item["entity_vat_ledger_id"]
        assert item["source"] == "entity_tax_ledgers"
        assert "project_id" not in item
    finally:
        db.rollback()
        db.close()


@pytest.mark.parametrize("failure_mode", ["missing_state", "mismatched_vat_run"])
def test_entity_tax_api_fails_closed_for_missing_or_inconsistent_scope(
    seeded_app, failure_mode
):
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        party, ledger = _new_v3_scope(db)
        if failure_mode == "missing_state":
            state = (
                db.query(TaxPeriodState)
                .filter_by(
                    reporting_party_id=party.id,
                    tax_type="ENTITY_TAX",
                    tax_period=PERIOD,
                )
                .one()
            )
            state.current_run_id = None
        else:
            vat_ledger = db.get(EntityVatLedger, ledger.entity_vat_ledger_id)
            assert vat_ledger is not None
            vat_ledger.calculation_run_id = ledger.calculation_run_id
        db.flush()

        response = _build_v3_entity_tax_ledger_envelope(
            db,
            period="2026-08",
            entity=party.code,
            page=1,
            page_size=50,
        )
        assert response.status_code == 503
        body = json.loads(response.body)
        assert body["status"] == "UNAVAILABLE"
        assert body["items"] == []
    finally:
        db.rollback()
        db.close()


def test_entity_tax_api_rejects_project_scope_before_database_access():
    with pytest.raises(HTTPException) as exc_info:
        entity_tax_ledger_collection(
            project_id=1,
            period=None,
            entity=None,
            entity_code=None,
            page=1,
            page_size=50,
            _user=None,
        )
    assert exc_info.value.status_code == 422
