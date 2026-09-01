"""Task15 Entity Tax Ledger deterministic and schema contracts."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.tax.entity_tax_ledger import (
    RULESET_VERSION,
    EntityTaxEvidenceError,
    EntityTaxManagementInputView,
    EntityTaxRuleError,
    EntityTaxRuleView,
    OfficialVatLedgerView,
    calculate_entity_tax_ledger,
    calculate_estimated_cit,
    calculate_profit,
    canonical_json,
    canonical_sha256,
    resolve_effective_tax_rule,
)
from app.models import EntityTaxLedger, EntityTaxLedgerComponent, EntityTaxManagementInput


ROOT = Path(__file__).resolve().parents[1]
PERIOD = date(2026, 8, 1)
REVIEWED_AT = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)


def _input(
    input_type: str,
    amount: str,
    *,
    version: int = 1,
    reviewed: bool = True,
    period: date = PERIOD,
    row_id: int | None = None,
) -> EntityTaxManagementInputView:
    return EntityTaxManagementInputView(
        id=row_id,
        reporting_party_id=101,
        tax_period=period,
        input_type=input_type,
        input_version=version,
        amount=Decimal(amount),
        reviewed=reviewed,
        reviewed_by="pytest" if reviewed else None,
        reviewed_at=REVIEWED_AT if reviewed else None,
        source="reviewed monthly management schedule" if reviewed else "",
    )


def _vat(
    *,
    run_status: str = "SUCCEEDED",
    tax_type: str = "VAT",
    period: date | str = PERIOD,
    official: bool = True,
) -> OfficialVatLedgerView:
    return OfficialVatLedgerView(
        id=501,
        calculation_run_id=601,
        reporting_party_id=101,
        tax_period=period,
        tax_type=tax_type,
        run_status=run_status,
        official=official,
    )


def _rule(
    *,
    code: str = "CIT_GENERAL",
    rate: str = "0.2500",
    reviewed: bool = True,
    effective_from: date = date(2026, 1, 1),
    effective_to: date | None = None,
) -> EntityTaxRuleView:
    return EntityTaxRuleView(
        code=code,
        rate=Decimal(rate),
        effective_from=effective_from,
        effective_to=effective_to,
        reviewed=reviewed,
        source="reviewed tax rule register" if reviewed else None,
    )


def _calculate(
    inputs: tuple[EntityTaxManagementInputView, ...] = (
        _input("REVENUE", "100.00", row_id=1),
        _input("REAL_COST", "40.00", row_id=2),
    ),
    *,
    vat: OfficialVatLedgerView | None = None,
    vat_calculation_run: object | None = None,
    rules: tuple[EntityTaxRuleView, ...] = (_rule(),),
    entity_is_legal: bool | None = True,
    scope_period: date | str = PERIOD,
):
    return calculate_entity_tax_ledger(
        reporting_party_id=101,
        tax_period=scope_period,
        management_inputs=inputs,
        tax_rules=rules,
        vat_ledger=_vat() if vat is None else vat,
        vat_calculation_run=vat_calculation_run,
        entity_is_legal=entity_is_legal,
    )


def test_entity_tax_formula_and_explicit_vat_reference():
    result = _calculate()

    assert result.rule_version == RULESET_VERSION
    assert result.entity_vat_ledger_id == 501
    assert result.revenue == Decimal("100.00")
    assert result.real_cost == Decimal("40.00")
    assert result.estimated_profit == Decimal("60.00")
    assert result.estimated_cit == Decimal("15.00")
    assert result.revenue_input_id == 1
    assert result.real_cost_input_id == 2
    assert len(result.input_snapshot_sha256) == 64
    assert len(result.result_sha256) == 64
    assert result.input_snapshot["entity_vat_ledger"]["id"] == 501
    assert result.result_payload["estimated_profit"] == "60.00"


def test_decimal_two_place_rounding_and_zero_loss_boundary():
    assert calculate_profit(Decimal("1.005"), Decimal("0.00")) == Decimal("1.01")
    assert calculate_profit(Decimal("0.00"), Decimal("0.00")) == Decimal("0.00")
    assert calculate_profit(Decimal("10.00"), Decimal("10.01")) == Decimal("-0.01")
    assert calculate_estimated_cit(Decimal("1.00"), Decimal("0.1250")) == Decimal("0.13")
    assert calculate_estimated_cit(Decimal("-1.00"), Decimal("0.2500")) == Decimal("0.00")

    result = _calculate(
        (
            _input("REVENUE", "10.005", row_id=11),
            _input("REAL_COST", "10.005", row_id=12),
        )
    )
    assert result.revenue == Decimal("10.01")
    assert result.real_cost == Decimal("10.01")
    assert result.estimated_profit == Decimal("0.00")
    assert result.estimated_cit == Decimal("0.00")


def test_hash_is_canonical_and_changes_when_evidence_changes():
    left = {"b": Decimal("2.00"), "a": ["x", 1]}
    right = {"a": ["x", 1], "b": Decimal("2.00")}
    assert canonical_json(left) == canonical_json(right)
    assert canonical_sha256(left) == canonical_sha256(right)
    assert canonical_sha256(left) != canonical_sha256({**left, "b": Decimal("2.01")})

    first = _calculate()
    changed = _calculate(
        (
            _input("REVENUE", "101.00", row_id=1),
            _input("REAL_COST", "40.00", row_id=2),
        )
    )
    assert first.input_snapshot_sha256 != changed.input_snapshot_sha256
    assert first.result_sha256 != changed.result_sha256


@pytest.mark.parametrize(
    "inputs, message",
    [
        ((_input("REVENUE", "100.00", row_id=3),), "missing"),
        (
            (
                _input("REVENUE", "100.00", row_id=4),
                _input("REAL_COST", "40.00", reviewed=False, row_id=5),
            ),
            "not reviewed",
        ),
        (
            (
                _input("REVENUE", "100.00", period=date(2026, 8, 15), row_id=6),
                _input("REAL_COST", "40.00", row_id=7),
            ),
            "month-start",
        ),
    ],
)
def test_missing_or_unreviewed_or_implicit_period_input_fails_closed(inputs, message):
    with pytest.raises(EntityTaxEvidenceError, match=message):
        _calculate(inputs)


def test_missing_vat_or_legal_entity_fails_closed():
    with pytest.raises(EntityTaxEvidenceError, match="official EntityVatLedger"):
        calculate_entity_tax_ledger(
            reporting_party_id=101,
            tax_period=PERIOD,
            management_inputs=(_input("REVENUE", "100.00"), _input("REAL_COST", "40.00")),
            tax_rules=(_rule(),),
            entity_is_legal=True,
        )
    with pytest.raises(EntityTaxEvidenceError, match="legal entity"):
        _calculate(entity_is_legal=False)
    with pytest.raises(EntityTaxEvidenceError, match="SUCCEEDED VAT"):
        _calculate(vat=_vat(run_status="DRAFT"))
    with pytest.raises(EntityTaxEvidenceError, match="SUCCEEDED VAT"):
        _calculate(vat=_vat(tax_type="ENTITY_TAX"))


def test_tax_rule_must_be_reviewed_and_effective_without_a_default_rate():
    with pytest.raises(EntityTaxRuleError, match="no reviewed effective"):
        _calculate(rules=(_rule(reviewed=False),))
    with pytest.raises(EntityTaxRuleError, match="no reviewed effective"):
        _calculate(rules=(_rule(effective_from=date(2027, 1, 1)),))
    with pytest.raises(EntityTaxRuleError, match="explicit CIT rule code"):
        resolve_effective_tax_rule((_rule(),), tax_period=PERIOD, rule_code="")
    with pytest.raises(EntityTaxRuleError, match="between 0 and 1"):
        _calculate(rules=(_rule(rate="25"),))


def test_latest_input_version_is_selected_and_newer_unreviewed_version_blocks():
    result = _calculate(
        (
            _input("REVENUE", "100.00", version=1, row_id=21),
            _input("REVENUE", "120.00", version=2, row_id=22),
            _input("REAL_COST", "20.00", row_id=23),
        )
    )
    assert result.revenue == Decimal("120.00")
    assert result.revenue_input_id == 22
    assert result.revenue_input_version == 2

    with pytest.raises(EntityTaxEvidenceError, match="not reviewed"):
        _calculate(
            (
                _input("REVENUE", "100.00", version=1, row_id=24),
                _input("REVENUE", "120.00", version=2, reviewed=False, row_id=25),
                _input("REAL_COST", "20.00", row_id=26),
            )
        )


@pytest.mark.parametrize("duplicate_type", ["REVENUE", "REAL_COST"])
def test_any_duplicate_input_version_fails_closed_even_when_not_latest(duplicate_type):
    inputs = [
        _input("REVENUE", "100.00", version=1, row_id=31),
        _input("REAL_COST", "20.00", version=1, row_id=32),
    ]
    inputs.append(_input(duplicate_type, "110.00", version=1, row_id=33))
    inputs.append(_input(duplicate_type, "120.00", version=2, row_id=34))

    with pytest.raises(EntityTaxEvidenceError, match="duplicate"):
        _calculate(tuple(inputs))


@pytest.mark.parametrize("raw_period", ["2026-08", "2026-08-01"])
def test_evidence_period_strings_are_not_implicitly_accepted(raw_period):
    with pytest.raises(EntityTaxEvidenceError, match="explicit month-start date"):
        _calculate(
            (
                _input("REVENUE", "100.00", period=raw_period, row_id=35),
                _input("REAL_COST", "20.00", row_id=36),
            )
        )

    with pytest.raises(EntityTaxEvidenceError, match="explicit month-start date"):
        _calculate(vat=_vat(period=raw_period))

    linked_run = {
        "id": 601,
        "reporting_party_id": 101,
        "tax_type": "VAT",
        "run_status": "SUCCEEDED",
        "tax_period": raw_period,
    }
    with pytest.raises(EntityTaxEvidenceError, match="explicit month-start date"):
        _calculate(vat_calculation_run=linked_run)


def test_external_scope_month_string_is_normalized_but_evidence_remains_explicit():
    result = _calculate(scope_period="2026-08")
    assert result.tax_period == PERIOD


def test_explicit_false_official_marker_cannot_be_upgraded_by_successful_linked_run():
    linked_run = {
        "id": 601,
        "reporting_party_id": 101,
        "tax_type": "VAT",
        "run_status": "SUCCEEDED",
        "tax_period": PERIOD,
    }
    with pytest.raises(EntityTaxEvidenceError, match="official=False"):
        _calculate(
            vat=_vat(official=False),
            vat_calculation_run=linked_run,
        )


def test_revision_87_orm_schema_has_no_project_or_invoice_date_axis():
    expected = {
        "entity_tax_management_inputs",
        "entity_tax_ledgers",
        "entity_tax_ledger_components",
    }
    assert {EntityTaxManagementInput.__tablename__, EntityTaxLedger.__tablename__, EntityTaxLedgerComponent.__tablename__} == expected
    for model in (EntityTaxManagementInput, EntityTaxLedger, EntityTaxLedgerComponent):
        assert {"project_id", "invoice_date"}.isdisjoint(model.__table__.c.keys())

    ledger_targets = {
        element.target_fullname
        for constraint in EntityTaxLedger.__table__.foreign_key_constraints
        for element in constraint.elements
    }
    assert "internal_entities.party_id" in ledger_targets
    assert "calculation_runs.id" in ledger_targets
    assert "entity_vat_ledgers.id" in ledger_targets

    migration = (ROOT / "alembic" / "versions" / "87_v3_entity_tax_ledgers.py").read_text(encoding="utf-8")
    assert 'down_revision = "86_v3_input_vat_claim_review_resolution"' in migration
    assert "ENTITY_TAX" in migration
    assert "entity_vat_ledgers.id" in migration
