from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.services import canonical_ledger, canonical_project_summary
from app.services.canonical_ssot import (
    DEDUCTIBILITY_ELIGIBLE,
    DEDUCTIBILITY_INELIGIBLE,
    DEDUCTIBILITY_NEEDS_REVIEW,
    consolidate_invoice_facts,
    invoice_deductibility_status,
)


def _fact(fid: int, **payload):
    return {
        "fact_id": fid,
        "source_document_id": fid,
        "fact_version": 1,
        "payload": payload,
    }


def test_deductibility_is_tri_state_not_nullable_bool_collapse():
    assert invoice_deductibility_status({"deductible": True}) == DEDUCTIBILITY_ELIGIBLE
    assert invoice_deductibility_status({"deductible": False}) == DEDUCTIBILITY_INELIGIBLE
    assert invoice_deductibility_status({"deductible": None}) == DEDUCTIBILITY_NEEDS_REVIEW
    assert invoice_deductibility_status({}) == DEDUCTIBILITY_NEEDS_REVIEW


def test_project_boundary_eliminates_internal_trade_and_accounts_all_input_vat():
    internal = {"A08", "B01"}
    facts = [
        _fact(1, seller_entity_code="EXT-MAT", buyer_entity_code="A08", net_amount="100.00", vat_amount="13.00", deductible=True),
        _fact(2, seller_entity_code="EXT-SVC", buyer_entity_code="A08", net_amount="50.00", vat_amount="6.50", deductible=None),
        _fact(3, seller_entity_code="A08", buyer_entity_code="EXT-CUSTOMER", net_amount="200.00", vat_amount="18.00", deductible=True),
        _fact(4, seller_entity_code="A08", buyer_entity_code="B01", net_amount="300.00", vat_amount="27.00", deductible=True),
    ]

    result = consolidate_invoice_facts(facts, internal)

    assert result["boundary_output_net"] == Decimal("200.00")
    assert result["boundary_output_vat"] == Decimal("18.00")
    assert result["boundary_input_net"] == Decimal("150.00")
    assert result["boundary_input_vat"] == Decimal("19.50")
    assert result["deductible_input_vat"] == Decimal("13.00")
    assert result["nondeductible_input_vat"] == Decimal("0")
    assert result["pending_input_vat"] == Decimal("6.50")
    assert result["internal_eliminated"] == Decimal("300.00")
    assert result["internal_eliminated_vat"] == Decimal("27.00")
    assert result["signed_vat_position"] == Decimal("5.00")
    assert result["input_vat_identity_ok"] is True
    assert result["input_vat_unaccounted"] == Decimal("0")


def test_nondeductible_vat_is_explicit_and_enters_external_cost():
    result = consolidate_invoice_facts(
        [_fact(1, seller_entity_code="EXT-MAT", buyer_entity_code="A08", net_amount="100.00", vat_amount="13.00", deductible=False)],
        {"A08"},
    )

    assert result["boundary_input_vat"] == Decimal("13.00")
    assert result["deductible_input_vat"] == Decimal("0")
    assert result["nondeductible_input_vat"] == Decimal("13.00")
    assert result["pending_input_vat"] == Decimal("0")
    assert result["external_cost"] == Decimal("113.00")
    assert result["input_vat_identity_ok"] is True


def test_project_tax_analysis_defaults_to_project_boundary(monkeypatch):
    facts = [
        _fact(1, seller_entity_code="A08", buyer_entity_code="B01", net_amount="300.00", vat_amount="27.00", deductible=True),
        _fact(2, seller_entity_code="EXT-MAT", buyer_entity_code="A08", net_amount="100.00", vat_amount="13.00", deductible=True),
        _fact(3, seller_entity_code="A08", buyer_entity_code="EXT-CUSTOMER", net_amount="200.00", vat_amount="18.00", deductible=True),
    ]

    monkeypatch.setattr(canonical_ledger, "_party_master", lambda _db: ({}, {"A08", "B01"}, {"EXT-MAT", "EXT-CUSTOMER"}))
    monkeypatch.setattr(canonical_ledger, "load_current_facts", lambda _db, _project_id, _fact_type: facts)

    result = canonical_ledger.project_tax_analysis_summary(object(), 1)

    assert result["scope"] == "PROJECT_BOUNDARY"
    assert result["is_filing_basis"] is False
    assert result["out_invoice_net"] == 200.0
    assert result["out_invoice_vat"] == 18.0
    assert result["in_invoice_net"] == 100.0
    assert result["in_invoice_vat"] == 13.0
    assert result["deductible_input_vat"] == 13.0
    assert result["internal_eliminated_net"] == 300.0
    assert result["internal_eliminated_vat"] == 27.0
    assert result["signed_vat_position"] == 5.0


def test_entity_projection_keeps_internal_legal_trade(monkeypatch):
    facts = [
        _fact(1, seller_entity_code="A08", buyer_entity_code="B01", net_amount="300.00", vat_amount="27.00", deductible=True),
        _fact(2, seller_entity_code="B01", buyer_entity_code="A08", net_amount="100.00", vat_amount="13.00", deductible=True),
    ]

    monkeypatch.setattr(canonical_ledger, "_party_master", lambda _db: ({}, {"A08", "B01"}, set()))
    monkeypatch.setattr(canonical_ledger, "load_current_facts", lambda _db, _project_id, _fact_type: facts)

    result = canonical_ledger.project_tax_analysis_summary(object(), 1, entity_code="A08")

    assert result["scope"] == "LEGAL_ENTITY_PROJECTION"
    assert result["is_filing_basis"] is False
    assert result["out_invoice_vat"] == 27.0
    assert result["in_invoice_vat"] == 13.0
    assert result["deductible_input_vat"] == 13.0
    assert result["internal_eliminated_vat"] == 0.0


class _ScalarResult:
    def scalars(self):
        return self

    def all(self):
        return ["A08"]


class _FakeDb:
    def get(self, _model, _project_id):
        return SimpleNamespace(id=1)

    def execute(self, _statement):
        return _ScalarResult()


def test_canonical_project_summary_preserves_negative_signed_vat(monkeypatch):
    monkeypatch.setattr(
        canonical_project_summary,
        "resolve_project_transaction_price",
        lambda _db, _pid: {
            "amount": Decimal("1450"),
            "status": "READY",
            "source_fact_id": 1,
            "fact_version": 1,
            "contract_no": "MAIN",
        },
    )
    monkeypatch.setattr(canonical_project_summary, "load_current_facts", lambda _db, _pid, _fact_type: [])
    monkeypatch.setattr(
        canonical_project_summary,
        "consolidate_invoice_facts",
        lambda _facts, _codes: {
            "external_cost": Decimal("100"),
            "internal_eliminated": Decimal("0"),
            "internal_eliminated_vat": Decimal("0"),
            "boundary_output_vat": Decimal("10"),
            "boundary_input_vat": Decimal("15"),
            "deductible_input_vat": Decimal("15"),
            "nondeductible_input_vat": Decimal("0"),
            "pending_input_vat": Decimal("0"),
            "signed_vat_position": Decimal("-5"),
            "input_vat_identity_ok": True,
        },
    )
    monkeypatch.setattr(
        canonical_project_summary,
        "payment_boundary",
        lambda _db, _pid: {
            "external_cash_in": Decimal("0"),
            "external_cash_out": Decimal("100"),
            "internal_cash_eliminated": Decimal("0"),
        },
    )

    from app.services import phase4_accounting

    monkeypatch.setattr(
        phase4_accounting,
        "build_project_accounting",
        lambda _db, _pid: {
            "recognition": {
                "recognized_revenue": Decimal("200"),
                "completion_percent": Decimal("0.5"),
                "incurred_cost": Decimal("100"),
            },
            "book_tax": {"accounting_profit": Decimal("100")},
            "engine_version": "test",
            "lineage": {},
        },
    )

    result = canonical_project_summary.canonical_project_summary(_FakeDb(), 1)

    assert result["vat"] == Decimal("-5")
    assert result["vat_scope"] == "PROJECT_BOUNDARY"
    assert result["vat_is_filing_basis"] is False
