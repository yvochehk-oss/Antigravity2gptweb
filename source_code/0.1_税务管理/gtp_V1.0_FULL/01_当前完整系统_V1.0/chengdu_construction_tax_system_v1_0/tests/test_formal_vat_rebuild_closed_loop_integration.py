"""Integration contracts for the Formal VAT rebuild closed-loop promotion gate."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.db import engine
from app.services import formal_vat_rebuild as rebuild_module
from app.services.formal_vat_closed_loop import (
    STATUS_NO_ACTIVITY,
    evaluate_formal_vat_closed_loop,
)
from app.services.formal_vat_rebuild import FormalVatRebuildBlockedError
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


def _seed_rebuild_source(
    session: Session,
    *,
    code: str,
    period: date,
    output_vat: Decimal | None = Decimal("130.00"),
    opening_credit: Decimal = Decimal("10.00"),
):
    party = Party(
        code=f"FVAT-CLOSED-LOOP-{code}",
        name=f"Formal VAT Closed Loop {code}",
        short_name=code,
        party_type="internal",
        active=True,
    )
    session.add(party)
    session.flush()
    session.add(
        InternalEntity(
            party_id=party.id,
            canonical_code=code,
            business_role="A",
            legal_entity=True,
            active=True,
        )
    )
    session.flush()

    reviewed_at = datetime.now(timezone.utc)
    asserted_output = Decimal("0.00") if output_vat is None else output_vat

    if output_vat is not None:
        identity = f"pytest:fvat-closed-loop:{code}:{period.isoformat()}"
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
                invoice_number=f"FVAT-CL-{code}-{period:%Y%m}",
                invoice_date=period,
                invoice_status="VALID",
                vat_amount=output_vat,
            )
        )
        session.flush()
        session.add(
            OutputVatEvent(
                invoice_fact_id=fact.id,
                reporting_party_id=party.id,
                output_vat_period=period,
                vat_amount=output_vat,
                event_type="OUTPUT",
                event_status="CONFIRMED",
                evidence_type="MANUAL_REVIEW",
                confidence="HIGH",
                source_system="pytest-fvat-closed-loop",
                external_event_id=f"{identity}:output",
                reviewed_by="pytest",
                reviewed_at=reviewed_at,
            )
        )

    session.add_all(
        [
            VatOutputPeriodAssertion(
                reporting_party_id=party.id,
                tax_period=period,
                asserted_output_vat_total=asserted_output,
                source="pytest reviewed Output VAT completeness",
                reviewed=True,
                reviewed_by="pytest",
                reviewed_at=reviewed_at,
            ),
            VatOpeningBalanceSeed(
                reporting_party_id=party.id,
                tax_period=period,
                opening_input_credit=opening_credit,
                source="pytest reviewed opening credit",
                reviewed=True,
                reviewed_by="pytest",
                reviewed_at=reviewed_at,
            ),
        ]
    )
    session.flush()
    return party


def _component_payload(row: EntityVatLedgerComponent) -> dict[str, object]:
    return {
        "component_type": row.component_type,
        "amount": str(row.amount),
        "prior_ledger_id": row.prior_ledger_id,
        "opening_balance_seed_id": row.opening_balance_seed_id,
        "output_vat_event_id": row.output_vat_event_id,
        "input_vat_claim_id": row.input_vat_claim_id,
        "tax_prepayment_fact_id": row.tax_prepayment_fact_id,
    }


def _faulting_evaluator(kind: str):
    def _evaluate(snapshot, components):
        persisted = [_component_payload(row) for row in components]
        if kind == "missing":
            persisted = persisted[:-1]
        elif kind == "unexpected":
            persisted.append(
                {
                    "component_type": "OUTPUT_VAT",
                    "amount": "1.00",
                    "output_vat_event_id": 999999999,
                }
            )
        elif kind == "duplicate":
            persisted.append(dict(persisted[-1]))
        elif kind == "amount_mismatch":
            persisted[0] = dict(persisted[0])
            persisted[0]["amount"] = str(Decimal(str(persisted[0]["amount"])) + Decimal("0.01"))
        else:  # pragma: no cover - parameter contract protects this branch
            raise AssertionError(f"unknown closed-loop fault: {kind}")
        return evaluate_formal_vat_closed_loop(snapshot, persisted)

    return _evaluate


@pytest.mark.parametrize("fault", ["missing", "unexpected", "duplicate", "amount_mismatch"])
def test_incomplete_closed_loop_never_promotes_and_rolls_back_cleanly(
    seeded_app,
    monkeypatch,
    fault,
):
    period = date(2026, 5, 1)
    code = f"FVC{fault[:2].upper()}"

    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            party = _seed_rebuild_source(session, code=code, period=period)
            monkeypatch.setattr(
                rebuild_module,
                "evaluate_formal_vat_closed_loop",
                _faulting_evaluator(fault),
            )

            savepoint = session.begin_nested()
            with pytest.raises(FormalVatRebuildBlockedError, match="closed-loop completeness assertion failed"):
                rebuild_module.rebuild_formal_vat_statutory_resource(
                    session,
                    entity_code=code,
                    period="2026-05",
                    created_by="pytest",
                )

            runs = session.query(CalculationRun).filter_by(
                reporting_party_id=party.id,
                tax_type="VAT",
                tax_period=period,
            ).all()
            assert len(runs) == 1
            assert runs[0].run_status == "DRAFT"

            state = session.query(TaxPeriodState).filter_by(
                reporting_party_id=party.id,
                tax_type="VAT",
                tax_period=period,
            ).one_or_none()
            assert state is None

            ledgers = session.query(EntityVatLedger).filter_by(
                reporting_party_id=party.id,
                tax_period=period,
            ).all()
            assert len(ledgers) == 1
            candidate_ledger_id = int(ledgers[0].id)
            assert session.query(EntityVatLedgerComponent).filter_by(
                ledger_id=candidate_ledger_id
            ).count() > 0

            savepoint.rollback()
            session.expire_all()

            assert session.query(CalculationRun).filter_by(
                reporting_party_id=party.id,
                tax_type="VAT",
                tax_period=period,
            ).count() == 0
            assert session.query(TaxPeriodState).filter_by(
                reporting_party_id=party.id,
                tax_type="VAT",
                tax_period=period,
            ).count() == 0
            assert session.query(EntityVatLedger).filter_by(
                reporting_party_id=party.id,
                tax_period=period,
            ).count() == 0
            assert session.query(EntityVatLedgerComponent).filter_by(
                ledger_id=candidate_ledger_id
            ).count() == 0
        finally:
            session.close()
            tx.rollback()


def test_no_activity_closed_loop_is_valid_and_can_promote(seeded_app, monkeypatch):
    period = date(2026, 6, 1)
    observed: dict[str, str] = {}

    def _recording_evaluator(snapshot, components):
        result = evaluate_formal_vat_closed_loop(snapshot, components)
        observed["status"] = result["status"]
        return result

    with engine.connect() as conn:
        tx = conn.begin()
        session = Session(bind=conn, expire_on_commit=False)
        try:
            party = _seed_rebuild_source(
                session,
                code="FVCNA",
                period=period,
                output_vat=None,
            )
            monkeypatch.setattr(
                rebuild_module,
                "evaluate_formal_vat_closed_loop",
                _recording_evaluator,
            )

            built = rebuild_module.rebuild_formal_vat_statutory_resource(
                session,
                entity_code="FVCNA",
                period="2026-06",
                created_by="pytest",
            )

            state = session.query(TaxPeriodState).filter_by(
                reporting_party_id=party.id,
                tax_type="VAT",
                tax_period=period,
            ).one()
            run = session.get(CalculationRun, built["calculation_run_id"])

            assert observed["status"] == STATUS_NO_ACTIVITY
            assert built["status"] == "BUILT"
            assert run is not None
            assert run.run_status == "SUCCEEDED"
            assert state.current_run_id == built["calculation_run_id"]
        finally:
            session.close()
            tx.rollback()
