from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from app.services.phase4_accounting import (
    ENGINE_VERSION,
    calculate_phase4_model,
    calculation_parameters_hash,
    fact_snapshot_hash,
)
from app.services.ssot_reconciliation import compare_metric_sets

ROOT = Path(__file__).resolve().parents[1]


def _fact(fact_id: int, fact_type: str, version: int, payload: dict) -> dict:
    return {
        "fact_id": fact_id,
        "fact_type": fact_type,
        "business_key": f"{fact_type}:{fact_id}",
        "fact_version": version,
        "source_hash": f"hash-{fact_id}-{version}",
        "source_document_id": fact_id + 100,
        "payload": payload,
    }


def test_cost_to_cost_accrual_and_cit_book_tax_difference() -> None:
    result = calculate_phase4_model(
        transaction_price=Decimal("10000000"),
        external_revenue_documentary=Decimal("3000000"),
        external_invoiced_cost=Decimal("2000000"),
        invoice_tax_addback=Decimal("200000"),
        accrual_facts=[
            _fact(1, "accrual", 1, {"amount": "500000", "reversal_amount": "0", "tax_deductible": False})
        ],
        progress_facts=[_fact(2, "progress", 1, {"estimated_total_cost": "5000000"})],
        tax_adjustment_facts=[
            _fact(3, "tax_adjustment", 1, {"direction": "ADD", "amount": "100000"}),
            _fact(4, "tax_adjustment", 1, {"direction": "DEDUCT", "amount": "50000"}),
        ],
        cit_rate=Decimal("0.25"),
    )
    assert result["recognition"]["basis"] == "cost_to_cost"
    assert result["recognition"]["completion_percent"] == Decimal("0.5")
    assert result["recognition"]["recognized_revenue"] == Decimal("5000000.00")
    assert result["recognition"]["recognized_cost"] == Decimal("2500000.00")
    assert result["accruals"]["unbilled_cost"] == Decimal("500000.00")
    assert result["book_tax"]["accounting_profit"] == Decimal("2500000.00")
    assert result["book_tax"]["taxable_income_current"] == Decimal("3250000.00")
    assert result["book_tax"]["current_cit"] == Decimal("812500.00")


def test_certified_completion_overrides_cost_to_cost() -> None:
    result = calculate_phase4_model(
        transaction_price=Decimal("10000000"),
        external_revenue_documentary=Decimal("0"),
        external_invoiced_cost=Decimal("2000000"),
        invoice_tax_addback=Decimal("0"),
        accrual_facts=[],
        progress_facts=[_fact(2, "progress", 2, {"completion_percent": "0.60", "estimated_total_cost": "5000000"})],
        tax_adjustment_facts=[],
    )
    assert result["recognition"]["basis"] == "certified_completion_percent"
    assert result["recognition"]["completion_percent"] == Decimal("0.60")
    assert result["recognition"]["recognized_revenue"] == Decimal("6000000.00")


def test_accrual_reversal_and_capitalization_do_not_double_expense() -> None:
    result = calculate_phase4_model(
        transaction_price=Decimal("0"),
        external_revenue_documentary=Decimal("0"),
        external_invoiced_cost=Decimal("100"),
        invoice_tax_addback=Decimal("0"),
        accrual_facts=[
            _fact(1, "accrual", 1, {"amount": "100", "reversal_amount": "100", "tax_deductible": True}),
            _fact(2, "accrual", 1, {"amount": "50", "capitalized": True}),
        ],
        progress_facts=[],
        tax_adjustment_facts=[],
    )
    assert result["accruals"]["unbilled_cost"] == Decimal("0.00")
    assert result["accruals"]["capitalized_not_expensed"] == Decimal("50.00")
    assert result["recognition"]["recognized_cost"] == Decimal("100.00")


def test_fact_engine_report_lineage_is_version_sensitive() -> None:
    facts_v1 = [_fact(10, "invoice", 1, {"net_amount": "100"})]
    facts_v2 = [_fact(10, "invoice", 2, {"net_amount": "100"})]
    hash_v1, rows_v1 = fact_snapshot_hash(facts_v1)
    hash_v2, rows_v2 = fact_snapshot_hash(facts_v2)
    assert hash_v1 != hash_v2
    assert rows_v1[0]["fact_version"] == 1
    assert rows_v2[0]["fact_version"] == 2
    assert ENGINE_VERSION == "canonical-accounting-phase4-v2"


def test_calculation_parameters_are_part_of_snapshot_identity() -> None:
    hash_25, params_25 = calculation_parameters_hash(
        Decimal("0.25"),
        transaction_price=Decimal("1450000000.00"),
        transaction_price_fact_id=101,
        transaction_price_fact_version=3,
    )
    hash_20, params_20 = calculation_parameters_hash(
        Decimal("0.20"),
        transaction_price=Decimal("1450000000.00"),
        transaction_price_fact_id=101,
        transaction_price_fact_version=3,
    )
    assert hash_25 != hash_20
    assert params_25 == {
        "cit_rate": "0.25",
        "transaction_price": "1450000000.00",
        "transaction_price_fact_id": "101",
        "transaction_price_fact_version": "3",
    }
    assert params_20 == {
        "cit_rate": "0.20",
        "transaction_price": "1450000000.00",
        "transaction_price_fact_id": "101",
        "transaction_price_fact_version": "3",
    }


def test_reconciliation_zero_diff_and_detected_diff() -> None:
    canonical = {
        "contract_count": 2,
        "contract_amount": Decimal("100"),
        "invoice_count": 3,
        "invoice_net": Decimal("80"),
        "invoice_vat": Decimal("10"),
        "payment_count": 4,
        "payment_amount": Decimal("70"),
    }
    assert compare_metric_sets(canonical, dict(canonical))["zero_diff"] is True
    changed = dict(canonical)
    changed["invoice_net"] = Decimal("79")
    result = compare_metric_sets(canonical, changed)
    assert result["zero_diff"] is False
    assert result["diff"]["invoice_net"] == Decimal("1")


def test_phase3_retirement_blocks_all_tax_sync_writers() -> None:
    source = (ROOT / "app" / "services" / "phase3_retirement.py").read_text(encoding="utf-8")
    for path in (
        "/rag-sync/sync",
        "/rag-sync/sync-batch",
        "/rag-sync/pending/{pending_id}/confirm",
        "/rag-sync/pending/{pending_id}/confirm-contract-and-create-parties",
        "/rag-sync/pending/{pending_id}/reject",
    ):
        assert path in source
    assert "status_code=410" in source


def test_v3_production_reads_no_longer_import_old_fact_domain() -> None:
    service = (ROOT / "app" / "services" / "v3_boss_service.py").read_text(encoding="utf-8")
    router = (ROOT / "app" / "routers" / "v3_canonical.py").read_text(encoding="utf-8")
    bridge = (ROOT / "app" / "services" / "canonical_v3_bridge.py").read_text(encoding="utf-8")
    assert "CanonicalV3Bridge" in service
    assert "CanonicalProjectFinance" not in service
    assert "CanonicalFourFlow" not in service
    assert "CanonicalProjectTax" not in service
    assert "DirectV3IngestService" not in router
    assert "LEGACY_V3_WRITER_RETIRED" in router
    assert "status_code=410" in router
    assert "canonical_facts" in bridge
    assert "legacy_v3_facts_used\": False" in bridge or '"legacy_v3_facts_used": False' in bridge


def test_phase4_service_has_no_legacy_transaction_table_reads() -> None:
    source = (ROOT / "app" / "services" / "phase4_accounting.py").read_text(encoding="utf-8").lower()
    assert "load_current_facts" in source
    assert "from contracts" not in source
    assert "from invoices" not in source
    assert "from cashflows" not in source
    assert "select(contract" not in source
    assert "select(invoice" not in source
    assert "select(cashflow" not in source
    assert "payment_in_pnl\": false" in source or '"payment_in_pnl": false' in source


def test_phase4_migration_extends_v3_head_and_triple_lineage_snapshot() -> None:
    source = (ROOT / "alembic" / "versions" / "99_phase4_accounting_snapshots.py").read_text(encoding="utf-8")
    assert 'down_revision = "98_v3_explicit_fact_relationship_graph"' in source
    assert "fact_snapshot_hash" in source
    assert "calculation_parameters_hash" in source
    assert "engine_version" in source
    assert "report_version" in source
    assert "fact_versions_json" in source
