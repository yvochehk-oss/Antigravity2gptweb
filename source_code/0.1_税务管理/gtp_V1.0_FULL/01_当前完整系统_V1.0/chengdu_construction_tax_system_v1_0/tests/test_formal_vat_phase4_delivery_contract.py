"""Phase 4 delivery contracts for the Formal VAT closed-loop system."""
from __future__ import annotations

from pathlib import Path
import runpy

from app.services import formal_vat_rebuild
from app.v3_vat_review_models import VatInputPeriodAssertion, VatOutputPeriodAssertion

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic" / "versions" / "101_v3_vat_input_period_assertions.py"

EXPECTED_EXPORTS = {
    "FormalVatRebuildBlockedError",
    "PLAN_KIND",
    "RESOURCE_TYPE",
    "RULESET_VERSION",
    "make_formal_vat_rebuild_plan",
    "rebuild_formal_vat_statutory_resource",
}

CRITICAL_REGRESSION_FILES = (
    "tests/test_formal_vat_closed_loop.py",
    "tests/test_formal_vat_rebuild.py",
    "tests/test_formal_vat_rebuild_closed_loop_integration.py",
    "tests/test_tax_data_policy_contract.py",
    "tests/test_formal_vat_phase4_delivery_contract.py",
)


def test_phase4_public_rebuild_exports_are_stable():
    assert set(formal_vat_rebuild.__all__) == EXPECTED_EXPORTS


def test_phase4_output_and_input_completeness_models_remain_distinct():
    assert VatOutputPeriodAssertion.__tablename__ == "vat_output_period_assertions"
    assert VatInputPeriodAssertion.__tablename__ == "vat_input_period_assertions"
    assert hasattr(VatOutputPeriodAssertion, "asserted_output_vat_total")
    assert hasattr(VatInputPeriodAssertion, "asserted_input_vat_total")


def test_phase4_input_assertion_migration_is_the_expected_successor():
    namespace = runpy.run_path(str(MIGRATION))
    assert namespace["revision"] == "101_v3_vat_input_period_assertions"
    assert namespace["down_revision"] == "100_project_master_alignment"


def test_phase4_database_guard_is_present_in_input_assertion_migration():
    migration = MIGRATION.read_text(encoding="utf-8")
    assert "v3_guard_entity_vat_ledger_input_completeness" in migration
    assert "trg_v3_guard_entity_vat_ledger_input_completeness" in migration
    assert "reviewed Input VAT completeness assertion required" in migration
    assert "confirmed Input VAT claims total" in migration
    assert "Entity VAT Ledger input_vat" in migration


def test_phase4_makefile_keeps_one_reproducible_closed_loop_regression_entrypoint():
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "test-vat-phase4:" in makefile
    assert "verify-vat-phase4:" in makefile
    for path in CRITICAL_REGRESSION_FILES:
        assert path in makefile
