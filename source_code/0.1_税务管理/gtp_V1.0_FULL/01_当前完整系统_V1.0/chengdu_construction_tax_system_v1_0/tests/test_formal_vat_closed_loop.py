from __future__ import annotations

from copy import deepcopy

from app.services.formal_vat_closed_loop import (
    STATUS_COMPLETE,
    STATUS_INCOMPLETE,
    STATUS_NO_ACTIVITY,
    evaluate_formal_vat_closed_loop,
)


def _snapshot():
    return {
        "opening": {"kind": "OPENING_SEED", "seed_id": 9, "amount": "20.00"},
        "output_events": [{"id": 11, "amount": "100.00"}],
        "input_claims": [{"id": 12, "amount": "30.00"}],
        "tax_prepayments": [{"fact_id": 13, "amount": "10.00"}],
    }


def _components():
    return [
        {
            "component_type": "OPENING_INPUT_CREDIT",
            "amount": "20.00",
            "prior_ledger_id": None,
            "opening_balance_seed_id": 9,
        },
        {"component_type": "OUTPUT_VAT", "amount": "100.00", "output_vat_event_id": 11},
        {"component_type": "INPUT_VAT", "amount": "30.00", "input_vat_claim_id": 12},
        {"component_type": "TAX_PREPAYMENT", "amount": "10.00", "tax_prepayment_fact_id": 13},
    ]


def test_exact_source_to_component_mapping_is_complete():
    result = evaluate_formal_vat_closed_loop(_snapshot(), _components())

    assert result["status"] == STATUS_COMPLETE
    assert result["source_resolved"] is True
    assert result["expected_component_count"] == 4
    assert result["persisted_component_count"] == 4
    assert result["transaction_source_count"] == 3
    assert result["missing_source_keys"] == []
    assert result["unexpected_component_keys"] == []
    assert result["amount_mismatches"] == []


def test_opening_only_exact_mapping_is_no_activity_not_fake_zero():
    snapshot = _snapshot()
    snapshot["output_events"] = []
    snapshot["input_claims"] = []
    snapshot["tax_prepayments"] = []

    result = evaluate_formal_vat_closed_loop(snapshot, _components()[:1])

    assert result["status"] == STATUS_NO_ACTIVITY
    assert result["transaction_source_count"] == 0


def test_missing_component_is_incomplete():
    result = evaluate_formal_vat_closed_loop(_snapshot(), _components()[:-1])

    assert result["status"] == STATUS_INCOMPLETE
    assert result["missing_source_keys"] == ["TAX_PREPAYMENT_FACT:13"]


def test_unexpected_component_is_incomplete():
    components = _components() + [
        {"component_type": "OUTPUT_VAT", "amount": "1.00", "output_vat_event_id": 99}
    ]

    result = evaluate_formal_vat_closed_loop(_snapshot(), components)

    assert result["status"] == STATUS_INCOMPLETE
    assert result["unexpected_component_keys"] == ["OUTPUT_VAT_EVENT:99"]


def test_duplicate_source_identity_is_incomplete():
    snapshot = deepcopy(_snapshot())
    snapshot["output_events"].append({"id": 11, "amount": "100.00"})

    result = evaluate_formal_vat_closed_loop(snapshot, _components())

    assert result["status"] == STATUS_INCOMPLETE
    assert result["duplicate_source_keys"] == ["OUTPUT_VAT_EVENT:11"]


def test_duplicate_persisted_component_identity_is_incomplete():
    components = _components() + [
        {"component_type": "INPUT_VAT", "amount": "30.00", "input_vat_claim_id": 12}
    ]

    result = evaluate_formal_vat_closed_loop(_snapshot(), components)

    assert result["status"] == STATUS_INCOMPLETE
    assert result["duplicate_component_keys"] == ["INPUT_VAT_CLAIM:12"]


def test_amount_mismatch_is_incomplete():
    components = deepcopy(_components())
    components[1]["amount"] = "99.99"

    result = evaluate_formal_vat_closed_loop(_snapshot(), components)

    assert result["status"] == STATUS_INCOMPLETE
    assert result["amount_mismatches"] == [
        {"source_key": "OUTPUT_VAT_EVENT:11", "expected": "100.00", "persisted": "99.99"}
    ]


def test_unresolved_opening_source_is_incomplete():
    snapshot = _snapshot()
    snapshot["opening"] = {"kind": "UNKNOWN", "amount": "20.00"}

    result = evaluate_formal_vat_closed_loop(snapshot, _components()[1:])

    assert result["status"] == STATUS_INCOMPLETE
    assert result["source_resolved"] is False
    assert result["source_errors"] == ["OPENING_SOURCE_UNRESOLVED"]
