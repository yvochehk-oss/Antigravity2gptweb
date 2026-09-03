"""Formal VAT statutory resource read contracts."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import engine
from app.services.formal_vat_statutory import (
    FormalVatStatutoryResourceIntegrityError,
    FormalVatStatutoryResourceNotFoundError,
    get_formal_vat_statutory_resource,
)
from app.v3_fact_models import Fact, InvoiceFact
from app.v3_party_models import InternalEntity, Party
from app.v3_period_models import CalculationRun, TaxPeriodState
from app.v3_vat_ledger_models import EntityVatLedger, OutputVatEvent
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


def _create_official_vat_resource(session: Session, *, code: str, period: date):
    party = Party(
        code=f"FVAT-{code}",
        name=f"Formal VAT {code}",
        short_name=code,
        party_type="internal",
        active=True,
    )
    session.add(party)
    session.flush()
    entity = InternalEntity(
        party_id=party.id,
        canonical_code=code,
        business_role="A",
        legal_entity=True,
        active=True,
    )
    session.add(entity)
    session.flush()

    completed = datetime.now(timezone.utc)
    run = CalculationRun(
        reporting_party_id=party.id,
        tax_type="VAT",
        tax_period=period,
        run_kind="STANDARD",
        run_status="SUCCEEDED",
        ruleset_version="V3_VAT_LEDGER_TEST",
        input_snapshot_sha256="a" * 64,
        result_sha256="b" * 64,
        created_by="pytest",
        completed_at=completed,
    )
    session.add(run)
    session.flush()

    identity = f"pytest:{code}:{period.isoformat()}"
    fact = Fact(
        fact_type="INVOICE",
        business_identity_key=identity,
        version_no=1,
        is_current=True,
        validation_status="VALID",
    )
    session.add(fact)
    session.flush()
    session.add(
        InvoiceFact(
            fact_id=fact.id,
            seller_party_id=party.id,
            buyer_party_id=None,
            invoice_identity_key=identity,
            invoice_identity_version="V1",
            invoice_number=f"PYTEST-{code}-{period:%Y%m}",
            invoice_date=period,
            invoice_status="VALID",
            vat_amount=Decimal("130.00"),
        )
    )
    session.flush()
    session.add(
        OutputVatEvent(
            invoice_fact_id=fact.id,
            reporting_party_id=party.id,
            output_vat_period=period,
            vat_amount=Decimal("130.00"),
            event_type="OUTPUT",
            event_status="CONFIRMED",
            evidence_type="MANUAL_REVIEW",
            confidence="HIGH",
            source_system="pytest",
            external_event_id=f"{identity}:output-vat",
            reviewed_by="pytest",
            reviewed_at=completed,
        )
    )
    session.flush()

    session.add(
        VatOutputPeriodAssertion(
            reporting_party_id=party.id,
            tax_period=period,
            asserted_output_vat_total=Decimal("130.00"),
            source="pytest reviewed complete output VAT total",
            reviewed=True,
            reviewed_by="pytest",
            reviewed_at=completed,
        )
    )
    session.flush()

    ledger = EntityVatLedger(
        calculation_run_id=run.id,
        reporting_party_id=party.id,
        tax_period=period,
        opening_input_credit=Decimal("10.00"),
        output_vat=Decimal("130.00"),
        input_vat=Decimal("40.00"),
        tax_prepayment=Decimal("20.00"),
        vat_payable_before_prepayment=Decimal("80.00"),
        closing_input_credit=Decimal("0.00"),
        vat_payable_after_prepayment=Decimal("60.00"),
        unapplied_tax_prepayment=Decimal("0.00"),
    )
    state = TaxPeriodState(
        reporting_party_id=party.id,
        tax_type="VAT",
        tax_period=period,
        state="OPEN",
        current_run_id=run.id,
        state_version=1,
    )
    session.add_all([ledger, state])
    session.flush()
    return party, run, ledger


def test_formal_vat_reads_only_current_succeeded_materialization(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            party, run, ledger = _create_official_vat_resource(
                session,
                code="FVA1",
                period=date(2026, 4, 1),
            )

            result = get_formal_vat_statutory_resource(session, "fva1", "2026-04")

            assert result["status"] == "READY"
            assert result["resource_type"] == "FORMAL_VAT_STATUTORY_V1"
            assert result["source_of_truth"] == (
                "tax_period_states.current_run_id->calculation_runs->entity_vat_ledgers"
            )
            assert result["entity_code"] == "FVA1"
            assert result["reporting_party_id"] == party.id
            assert result["period"] == "2026-04"
            assert result["tax_period"] == "2026-04-01"
            assert result["calculation_run"]["id"] == run.id
            assert result["calculation_run"]["run_status"] == "SUCCEEDED"
            assert result["calculation_run"]["result_sha256"] == "b" * 64
            assert result["vat_ledger"]["id"] == ledger.id
            assert result["vat_ledger"]["output_vat"] == "130.00"
            assert result["vat_ledger"]["input_vat"] == "40.00"
            assert result["vat_ledger"]["vat_payable_after_prepayment"] == "60.00"
        finally:
            session.close()
            tx.rollback()


def test_formal_vat_missing_period_fails_closed_without_rebuild(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            _create_official_vat_resource(
                session,
                code="FVA2",
                period=date(2026, 4, 1),
            )

            with pytest.raises(FormalVatStatutoryResourceNotFoundError):
                get_formal_vat_statutory_resource(session, "FVA2", "2026-05")

            states = session.query(TaxPeriodState).filter_by(
                tax_type="VAT",
                tax_period=date(2026, 5, 1),
            ).all()
            assert states == []
        finally:
            session.close()
            tx.rollback()


def test_formal_vat_existing_state_without_current_run_is_integrity_error(seeded_app):
    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            party = Party(
                code="FVAT-FVA3",
                name="Formal VAT FVA3",
                short_name="FVA3",
                party_type="internal",
                active=True,
            )
            session.add(party)
            session.flush()
            session.add(
                InternalEntity(
                    party_id=party.id,
                    canonical_code="FVA3",
                    business_role="A",
                    legal_entity=True,
                    active=True,
                )
            )
            session.add(
                TaxPeriodState(
                    reporting_party_id=party.id,
                    tax_type="VAT",
                    tax_period=date(2026, 4, 1),
                    state="OPEN",
                    current_run_id=None,
                    state_version=1,
                )
            )
            session.flush()

            with pytest.raises(
                FormalVatStatutoryResourceIntegrityError,
                match="no current calculation run",
            ):
                get_formal_vat_statutory_resource(session, "FVA3", "2026-04")
        finally:
            session.close()
            tx.rollback()


def test_formal_vat_api_exposes_single_entity_single_period_contract(seeded_app, monkeypatch):
    from app.main import app
    import app.routers.legal_entity_operating_projection as router_module

    calls: list[tuple[str, str]] = []

    def fake_read(db, entity_code: str, period: str):
        calls.append((entity_code, period))
        return {
            "status": "READY",
            "resource_type": "FORMAL_VAT_STATUTORY_V1",
            "source_of_truth": (
                "tax_period_states.current_run_id->calculation_runs->entity_vat_ledgers"
            ),
            "entity_code": entity_code.upper(),
            "reporting_party_id": 88,
            "period": period,
            "tax_period": f"{period}-01",
            "period_state": "OPEN",
            "state_version": 3,
            "calculation_run": {
                "id": 901,
                "run_kind": "STANDARD",
                "run_status": "SUCCEEDED",
                "ruleset_version": "V3_VAT_LEDGER_V1",
                "input_snapshot_sha256": "a" * 64,
                "result_sha256": "b" * 64,
                "completed_at": "2026-04-30T10:00:00+00:00",
            },
            "vat_ledger": {
                "id": 902,
                "opening_input_credit": "10.00",
                "output_vat": "130.00",
                "input_vat": "40.00",
                "tax_prepayment": "20.00",
                "vat_payable_before_prepayment": "80.00",
                "closing_input_credit": "0.00",
                "vat_payable_after_prepayment": "60.00",
                "unapplied_tax_prepayment": "0.00",
                "created_at": "2026-04-30T10:00:01+00:00",
            },
        }

    monkeypatch.setattr(router_module, "get_formal_vat_statutory_resource", fake_read)
    client = TestClient(app)
    _login(client)

    response = client.get(
        "/api/v3/legal-entities/A08/statutory-vat",
        params={"period": "2026-04"},
    )

    assert response.status_code == 200, response.text
    assert calls == [("A08", "2026-04")]
    payload = response.json()
    assert payload["resource_type"] == "FORMAL_VAT_STATUTORY_V1"
    assert payload["entity_code"] == "A08"
    assert payload["period"] == "2026-04"
    assert payload["calculation_run"]["run_status"] == "SUCCEEDED"
    assert payload["vat_ledger"]["vat_payable_after_prepayment"] == "60.00"


def test_formal_vat_api_maps_missing_resource_to_explicit_404(seeded_app, monkeypatch):
    from app.main import app
    import app.routers.legal_entity_operating_projection as router_module

    def fake_missing(db, entity_code: str, period: str):
        raise FormalVatStatutoryResourceNotFoundError(f"{entity_code} {period}")

    monkeypatch.setattr(router_module, "get_formal_vat_statutory_resource", fake_missing)
    client = TestClient(app)
    _login(client)

    response = client.get(
        "/api/v3/legal-entities/A08/statutory-vat",
        params={"period": "2026-04"},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "FORMAL_VAT_STATUTORY_RESOURCE_NOT_FOUND"


def test_formal_vat_api_maps_broken_current_pointer_to_explicit_409(seeded_app, monkeypatch):
    from app.main import app
    import app.routers.legal_entity_operating_projection as router_module

    def fake_invalid(db, entity_code: str, period: str):
        raise FormalVatStatutoryResourceIntegrityError(
            "official VAT period state has no current calculation run"
        )

    monkeypatch.setattr(router_module, "get_formal_vat_statutory_resource", fake_invalid)
    client = TestClient(app)
    _login(client)

    response = client.get(
        "/api/v3/legal-entities/A08/statutory-vat",
        params={"period": "2026-04"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "FORMAL_VAT_STATUTORY_RESOURCE_INVALID",
        "detail": "official VAT period state has no current calculation run",
    }
