"""Phase 4 accrual auto matching and natural-period rollforward tests."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.accrual_matching import match_accrual_reversals
from app.services.period_rollforward import accounting_period_bounds, build_period_rollforward
from app.services.phase4_accounting import (
    _facts_as_of,
    _parse_business_date,
    calculate_phase4_model,
)


def _accrual(
    fact_id: int,
    *,
    amount: str = "100.00",
    reversal_amount: str = "0",
    contract_no: str = "C-001",
    supplier_code: str = "SUP-01",
):
    return {
        "fact_id": fact_id,
        "fact_type": "accrual",
        "payload": {
            "amount": amount,
            "reversal_amount": reversal_amount,
            "contract_no": contract_no,
            "supplier_code": supplier_code,
            "tax_deductible": False,
        },
    }


def _invoice(
    fact_id: int,
    *,
    net_amount: str = "100.00",
    contract_no: str = "C-001",
    supplier_code: str = "SUP-01",
):
    return {
        "fact_id": fact_id,
        "fact_type": "invoice",
        "payload": {
            "contract_no": contract_no,
            "seller_entity_code": supplier_code,
            "buyer_entity_code": "ENT-01",
            "net_amount": net_amount,
            "vat_amount": "13.00",
            "deductible": True,
        },
    }


def test_unique_invoice_match_clears_accrual_duplicate_cost():
    accrual = _accrual(10)
    invoice = _invoice(20)

    matching = match_accrual_reversals([accrual], [invoice], {"ENT-01"})

    assert matching["status"] == "READY"
    assert matching["auto_reversal_by_accrual"] == {10: Decimal("100.00")}
    assert matching["matches"][0]["match_basis"] == "contract_supplier_accounting_cost"

    model = calculate_phase4_model(
        transaction_price=Decimal("0"),
        external_revenue_documentary=Decimal("0"),
        external_invoiced_cost=Decimal("100"),
        invoice_tax_addback=Decimal("0"),
        accrual_facts=[accrual],
        progress_facts=[],
        tax_adjustment_facts=[],
        auto_reversal_by_accrual=matching["auto_reversal_by_accrual"],
        accrual_match_audit=matching,
    )
    assert model["recognition"]["incurred_cost"] == Decimal("100.00")
    assert model["accruals"]["unbilled_cost"] == Decimal("0.00")
    assert model["accruals"]["auto_reversal_total"] == Decimal("100.00")


def test_explicit_reversal_is_authoritative_and_auto_reverses_only_remainder():
    accrual = _accrual(10, reversal_amount="40.00")
    invoice = _invoice(20)

    matching = match_accrual_reversals([accrual], [invoice], {"ENT-01"})

    assert matching["explicit_reversal_total"] == Decimal("40.00")
    assert matching["auto_reversal_by_accrual"] == {10: Decimal("60.00")}
    assert matching["auto_reversal_total"] == Decimal("60.00")
    assert matching["matches"][0]["invoice_accounting_cost"] == Decimal("100.00")
    assert matching["matches"][0]["matched_amount"] == Decimal("60.00")


def test_ambiguous_accrual_candidates_fail_closed():
    matching = match_accrual_reversals(
        [_accrual(10), _accrual(11)],
        [_invoice(20)],
        {"ENT-01"},
    )

    assert matching["status"] == "DEGRADED"
    assert matching["auto_reversal_by_accrual"] == {}
    assert matching["matches"] == []
    assert matching["data_gaps"][0]["code"] == "ACCRUAL_AUTO_MATCH_AMBIGUOUS"
    assert matching["data_gaps"][0]["candidate_accrual_fact_ids"] == [10, 11]


def test_contract_supplier_or_amount_mismatch_does_not_auto_match():
    matching = match_accrual_reversals(
        [_accrual(10)],
        [
            _invoice(20, contract_no="C-OTHER"),
            _invoice(21, supplier_code="SUP-OTHER"),
            _invoice(22, net_amount="99.00"),
        ],
        {"ENT-01"},
    )
    assert matching["status"] == "READY"
    assert matching["auto_reversal_by_accrual"] == {}
    assert matching["matches"] == []


def test_accounting_cutoff_uses_business_dates_and_fails_closed_when_missing():
    assert _parse_business_date("2026-Q3").isoformat() == "2026-09-30"
    facts = [
        {
            "fact_id": 1,
            "fact_type": "invoice",
            "payload": {"invoice_date": "2026-08-31"},
        },
        {
            "fact_id": 2,
            "fact_type": "contract",
            "payload": {"effective_date": "2026-09-01"},
        },
        {
            "fact_id": 3,
            "fact_type": "accrual",
            "payload": {},
        },
    ]

    included, gaps = _facts_as_of(facts, _parse_business_date("2026-08-31"))

    assert [fact["fact_id"] for fact in included] == [1]
    assert gaps == [
        {
            "code": "ACCOUNTING_FACT_DATE_MISSING",
            "fact_id": 3,
            "fact_type": "accrual",
        }
    ]


def test_accounting_cutoff_does_not_leak_future_explicit_reversal_backward():
    fact = {
        "fact_id": 10,
        "fact_type": "accrual",
        "accepted_at": "2026-08-15T09:00:00",
        "payload": {
            "accrual_date": "2026-07-31",
            "amount": "100.00",
            "reversal_amount": "100.00",
        },
    }

    july, july_gaps = _facts_as_of([fact], _parse_business_date("2026-07-31"))
    august, august_gaps = _facts_as_of([fact], _parse_business_date("2026-08-31"))

    assert july_gaps == []
    assert july[0]["payload"]["reversal_amount"] == 0
    assert august_gaps == []
    assert august[0]["payload"]["reversal_amount"] == "100.00"


@pytest.mark.parametrize(
    ("period", "grain", "start", "end"),
    [
        ("2026-08", "MONTH", "2026-08-01", "2026-08-31"),
        ("2026-Q3", "QUARTER", "2026-07-01", "2026-09-30"),
    ],
)
def test_accounting_period_bounds(period, grain, start, end):
    actual_grain, actual_start, actual_end = accounting_period_bounds(period)
    assert actual_grain == grain
    assert actual_start.isoformat() == start
    assert actual_end.isoformat() == end


def test_period_rollforward_is_exact_difference_of_cumulative_snapshots(monkeypatch):
    def fake_build(_db, _project_id, *, as_of):
        if str(as_of) == "2026-07-31":
            revenue, cost, profit, cit, accrual = 100, 70, 30, 7.5, 20
        elif str(as_of) == "2026-08-31":
            revenue, cost, profit, cit, accrual = 180, 125, 55, 13.75, 5
        else:
            raise AssertionError(f"unexpected cutoff: {as_of}")
        return {
            "status": "READY",
            "engine_version": "test-v1",
            "data_gaps": [],
            "recognition": {
                "recognized_revenue": Decimal(str(revenue)),
                "recognized_cost": Decimal(str(cost)),
                "incurred_cost": Decimal(str(cost)),
            },
            "book_tax": {
                "accounting_profit": Decimal(str(profit)),
                "taxable_income_before_loss_offset": Decimal(str(profit)),
                "taxable_income_current": Decimal(str(profit)),
                "current_cit": Decimal(str(cit)),
            },
            "accruals": {
                "unbilled_cost": Decimal(str(accrual)),
                "capitalized_not_expensed": Decimal("0"),
            },
        }

    monkeypatch.setattr(
        "app.services.period_rollforward.build_project_accounting",
        fake_build,
    )

    result = build_period_rollforward(object(), 15, "2026-08")

    assert result["status"] == "READY"
    assert result["opening_cutoff"] == "2026-07-31"
    assert result["closing_cutoff"] == "2026-08-31"
    assert result["movement"]["recognized_revenue"] == Decimal("80.00")
    assert result["movement"]["recognized_cost"] == Decimal("55.00")
    assert result["movement"]["accounting_profit"] == Decimal("25.00")
    assert result["movement"]["current_cit"] == Decimal("6.25")
    assert result["movement"]["accrued_unbilled"] == Decimal("-15.00")
    assert result["identity_ok"] is True


def test_period_rollforward_propagates_degraded_data_gaps(monkeypatch):
    def fake_build(_db, _project_id, *, as_of):
        return {
            "status": "DEGRADED" if str(as_of) == "2026-08-31" else "READY",
            "engine_version": "test-v1",
            "data_gaps": (
                [{"code": "ACCRUAL_AUTO_MATCH_AMBIGUOUS", "invoice_fact_id": 20}]
                if str(as_of) == "2026-08-31"
                else []
            ),
            "recognition": {
                "recognized_revenue": Decimal("0"),
                "recognized_cost": Decimal("0"),
                "incurred_cost": Decimal("0"),
            },
            "book_tax": {
                "accounting_profit": Decimal("0"),
                "taxable_income_before_loss_offset": Decimal("0"),
                "taxable_income_current": Decimal("0"),
                "current_cit": Decimal("0"),
            },
            "accruals": {
                "unbilled_cost": Decimal("0"),
                "capitalized_not_expensed": Decimal("0"),
            },
        }

    monkeypatch.setattr(
        "app.services.period_rollforward.build_project_accounting",
        fake_build,
    )

    result = build_period_rollforward(object(), 15, "2026-08")

    assert result["status"] == "DEGRADED"
    assert result["data_gaps"][0]["snapshot"] == "CLOSING"
    assert result["data_gaps"][0]["code"] == "ACCRUAL_AUTO_MATCH_AMBIGUOUS"
    assert result["identity_ok"] is True
