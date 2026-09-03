"""FVAT-4 end-to-end statutory VAT invariant contracts."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.models import TaxLedger
from app.v3_fact_models import Fact, InvoiceFact
from app.v3_party_models import InternalEntity, Party
from app.v3_period_models import CalculationRun, TaxPeriodState
from app.v3_vat_ledger_models import (
    EntityVatLedger,
    EntityVatLedgerComponent,
    OutputVatEvent,
    VatOpeningBalanceSeed,
)
from app.v3_vat_review_models import VatOutputPeriodAssertion


def _login(client: TestClient) -> None:
    assert client.get("/login").status_code == 200
    csrf_token = client.cookies.get("tax_csrf")
    assert csrf_token
    response = client.post(
        "/login",
        data={"username": "admin", "password": "888888", "_csrf": csrf_token},
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert response.status_code == 302


def _csrf_headers(client: TestClient) -> dict[str, str]:
    token = client.cookies.get("tax_csrf")
    assert token
    return {
        "Origin": "http://testserver",
        "X-CSRF-Token": token,
    }


@contextmanager
def _formal_vat_source(
    *,
    period: date,
    output_vat: Decimal = Decimal("130.00"),
    opening_credit: Decimal = Decimal("10.00"),
):
    from app.db import SessionLocal

    suffix = uuid4().hex[:10].upper()
    canonical_code = f"F4{suffix}"
    identity = f"pytest:fvat4:{canonical_code}:{period.isoformat()}"
    reviewed_at = datetime.now(timezone.utc)

    with SessionLocal() as db:
        party = Party(
            code=f"FVAT4-PARTY-{suffix}",
            name=f"FVAT-4 E2E {suffix}",
            short_name="FVAT4",
            party_type="internal",
            active=True,
        )
        db.add(party)
        db.flush()
        party_id = int(party.id)

        db.add(
            InternalEntity(
                party_id=party_id,
                canonical_code=canonical_code,
                business_role="A",
                legal_entity=True,
                active=True,
            )
        )
        db.flush()

        fact = Fact(
            fact_type="INVOICE",
            business_identity_key=identity,
            version_no=1,
            is_current=True,
            validation_status="VALID",
        )
        db.add(fact)
        db.flush()
        fact_id = int(fact.id)

        db.add(
            InvoiceFact(
                fact_id=fact_id,
                seller_party_id=party_id,
                buyer_party_id=None,
                invoice_identity_key=identity,
                invoice_identity_version="FVAT4_V1",
                invoice_number=f"FVAT4-{canonical_code}-{period:%Y%m}",
                invoice_date=period,
                invoice_status="VALID",
                vat_amount=output_vat,
            )
        )
        db.flush()

        db.add_all(
            [
                OutputVatEvent(
                    invoice_fact_id=fact_id,
                    reporting_party_id=party_id,
                    output_vat_period=period,
                    vat_amount=output_vat,
                    event_type="OUTPUT",
                    event_status="CONFIRMED",
                    evidence_type="MANUAL_REVIEW",
                    confidence="HIGH",
                    source_system="pytest-fvat4",
                    external_event_id=f"{identity}:output",
                    reviewed_by="pytest",
                    reviewed_at=reviewed_at,
                ),
                VatOutputPeriodAssertion(
                    reporting_party_id=party_id,
                    tax_period=period,
                    asserted_output_vat_total=output_vat,
                    source="FVAT-4 reviewed Output VAT completeness",
                    reviewed=True,
                    reviewed_by="pytest",
                    reviewed_at=reviewed_at,
                ),
                VatOpeningBalanceSeed(
                    reporting_party_id=party_id,
                    tax_period=period,
                    opening_input_credit=opening_credit,
                    source="FVAT-4 reviewed opening balance",
                    reviewed=True,
                    reviewed_by="pytest",
                    reviewed_at=reviewed_at,
                ),
            ]
        )
        db.commit()

    scope = {
        "party_id": party_id,
        "fact_id": fact_id,
        "entity_code": canonical_code,
        "period": period.strftime("%Y-%m"),
        "tax_period": period,
    }

    try:
        yield scope
    finally:
        with SessionLocal() as db:
            ledger_ids = list(
                db.scalars(
                    select(EntityVatLedger.id).where(
                        EntityVatLedger.reporting_party_id == party_id
                    )
                ).all()
            )
            if ledger_ids:
                db.execute(
                    delete(EntityVatLedgerComponent).where(
                        EntityVatLedgerComponent.ledger_id.in_(ledger_ids)
                    )
                )
            db.execute(
                delete(TaxPeriodState).where(
                    TaxPeriodState.reporting_party_id == party_id
                )
            )
            db.execute(
                delete(EntityVatLedger).where(
                    EntityVatLedger.reporting_party_id == party_id
                )
            )
            db.execute(
                delete(CalculationRun).where(
                    CalculationRun.reporting_party_id == party_id
                )
            )
            db.execute(
                delete(VatOpeningBalanceSeed).where(
                    VatOpeningBalanceSeed.reporting_party_id == party_id
                )
            )
            db.execute(
                delete(VatOutputPeriodAssertion).where(
                    VatOutputPeriodAssertion.reporting_party_id == party_id
                )
            )
            db.execute(
                delete(OutputVatEvent).where(
                    OutputVatEvent.reporting_party_id == party_id
                )
            )
            db.execute(delete(InvoiceFact).where(InvoiceFact.fact_id == fact_id))
            db.execute(delete(Fact).where(Fact.id == fact_id))
            db.execute(
                delete(InternalEntity).where(InternalEntity.party_id == party_id)
            )
            db.execute(delete(Party).where(Party.id == party_id))
            db.commit()


def _statutory_url(scope: dict[str, object]) -> str:
    return (
        f"/api/v3/legal-entities/{scope['entity_code']}/statutory-vat"
        f"?period={scope['period']}"
    )


def _rebuild_url(scope: dict[str, object]) -> str:
    return (
        f"/api/v3/legal-entities/{scope['entity_code']}/statutory-vat/rebuild"
        f"?period={scope['period']}"
    )


def _assert_single_canonical_materialization(scope: dict[str, object]) -> None:
    from app.db import SessionLocal

    party_id = int(scope["party_id"])
    tax_period = scope["tax_period"]
    entity_code = str(scope["entity_code"])
    period = str(scope["period"])

    with SessionLocal() as db:
        runs = db.scalars(
            select(CalculationRun).where(
                CalculationRun.reporting_party_id == party_id,
                CalculationRun.tax_type == "VAT",
                CalculationRun.tax_period == tax_period,
            )
        ).all()
        ledgers = db.scalars(
            select(EntityVatLedger).where(
                EntityVatLedger.reporting_party_id == party_id,
                EntityVatLedger.tax_period == tax_period,
            )
        ).all()
        states = db.scalars(
            select(TaxPeriodState).where(
                TaxPeriodState.reporting_party_id == party_id,
                TaxPeriodState.tax_type == "VAT",
                TaxPeriodState.tax_period == tax_period,
            )
        ).all()
        legacy_rows = db.scalars(
            select(TaxLedger).where(
                TaxLedger.entity_code == entity_code,
                TaxLedger.period == period,
            )
        ).all()

        assert len(runs) == 1
        assert len(ledgers) == 1
        assert len(states) == 1
        assert states[0].current_run_id == runs[0].id
        assert ledgers[0].calculation_run_id == runs[0].id
        assert legacy_rows == []


def test_fvat4_missing_rebuild_read_converges_on_one_canonical_resource(seeded_app):
    from app.db import SessionLocal
    from app.main import app

    with _formal_vat_source(period=date(2097, 1, 1)) as scope:
        client = TestClient(app)
        _login(client)

        missing = client.get(_statutory_url(scope))
        assert missing.status_code == 404
        assert missing.json()["detail"]["code"] == "FORMAL_VAT_STATUTORY_RESOURCE_NOT_FOUND"

        with SessionLocal() as db:
            party_id = int(scope["party_id"])
            tax_period = scope["tax_period"]
            assert db.scalars(
                select(TaxPeriodState).where(
                    TaxPeriodState.reporting_party_id == party_id,
                    TaxPeriodState.tax_type == "VAT",
                    TaxPeriodState.tax_period == tax_period,
                )
            ).all() == []
            assert db.scalars(
                select(CalculationRun).where(
                    CalculationRun.reporting_party_id == party_id,
                    CalculationRun.tax_type == "VAT",
                    CalculationRun.tax_period == tax_period,
                )
            ).all() == []
            assert db.scalars(
                select(EntityVatLedger).where(
                    EntityVatLedger.reporting_party_id == party_id,
                    EntityVatLedger.tax_period == tax_period,
                )
            ).all() == []

        built_response = client.post(
            _rebuild_url(scope),
            headers=_csrf_headers(client),
        )
        assert built_response.status_code == 200, built_response.text
        built = built_response.json()
        assert built["status"] == "BUILT"
        assert built["resource_type"] == "FORMAL_VAT_STATUTORY_V1"

        read_response = client.get(_statutory_url(scope))
        assert read_response.status_code == 200, read_response.text
        read = read_response.json()

        assert built["resource"] == read
        assert built["calculation_run_id"] == read["calculation_run"]["id"]
        assert built["ledger_id"] == read["vat_ledger"]["id"]
        assert (
            built["input_snapshot_sha256"]
            == read["calculation_run"]["input_snapshot_sha256"]
        )
        assert built["result_sha256"] == read["calculation_run"]["result_sha256"]
        assert read["source_of_truth"] == (
            "tax_period_states.current_run_id->calculation_runs->entity_vat_ledgers"
        )
        assert read["vat_ledger"]["output_vat"] == "130.00"
        assert read["vat_ledger"]["opening_input_credit"] == "10.00"
        assert read["vat_ledger"]["vat_payable_after_prepayment"] == "120.00"

        _assert_single_canonical_materialization(scope)


def test_fvat4_rebuild_read_rebuild_read_is_idempotent_and_stable(seeded_app):
    from app.main import app

    with _formal_vat_source(
        period=date(2097, 2, 1),
        output_vat=Decimal("88.00"),
        opening_credit=Decimal("3.00"),
    ) as scope:
        client = TestClient(app)
        _login(client)

        first_rebuild_response = client.post(
            _rebuild_url(scope),
            headers=_csrf_headers(client),
        )
        assert first_rebuild_response.status_code == 200, first_rebuild_response.text
        first_rebuild = first_rebuild_response.json()
        assert first_rebuild["status"] == "BUILT"

        first_read_response = client.get(_statutory_url(scope))
        assert first_read_response.status_code == 200, first_read_response.text
        first_read = first_read_response.json()
        assert first_rebuild["resource"] == first_read

        second_rebuild_response = client.post(
            _rebuild_url(scope),
            headers=_csrf_headers(client),
        )
        assert second_rebuild_response.status_code == 200, second_rebuild_response.text
        second_rebuild = second_rebuild_response.json()
        assert second_rebuild["status"] == "NO_CHANGE"

        second_read_response = client.get(_statutory_url(scope))
        assert second_read_response.status_code == 200, second_read_response.text
        second_read = second_read_response.json()

        assert second_rebuild["resource"] == second_read
        assert first_read == second_read
        assert (
            first_rebuild["calculation_run_id"]
            == second_rebuild["calculation_run_id"]
            == second_read["calculation_run"]["id"]
        )
        assert (
            first_rebuild["ledger_id"]
            == second_rebuild["ledger_id"]
            == second_read["vat_ledger"]["id"]
        )
        assert (
            first_rebuild["input_snapshot_sha256"]
            == second_rebuild["input_snapshot_sha256"]
            == second_read["calculation_run"]["input_snapshot_sha256"]
        )
        assert (
            first_rebuild["result_sha256"]
            == second_rebuild["result_sha256"]
            == second_read["calculation_run"]["result_sha256"]
        )
        assert (
            first_read["state_version"]
            == second_rebuild["resource"]["state_version"]
            == second_read["state_version"]
        )
        assert second_read["vat_ledger"]["output_vat"] == "88.00"
        assert second_read["vat_ledger"]["opening_input_credit"] == "3.00"
        assert second_read["vat_ledger"]["vat_payable_after_prepayment"] == "85.00"

        _assert_single_canonical_materialization(scope)
