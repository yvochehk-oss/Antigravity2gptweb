"""Static regression guards for P0 truth semantics.

These tests intentionally require no database. Runtime/API tests cover behavior elsewhere;
these guards prevent the specific fabricated-value and collection-conversion patterns from
being reintroduced during refactors.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _executive_source() -> str:
    base_file = ROOT / "app" / "routers" / "_executive_mobile_base.py"
    mobile_file = ROOT / "app" / "routers" / "executive_mobile.py"
    return (base_file.read_text(encoding="utf-8") if base_file.exists() else "") + mobile_file.read_text(encoding="utf-8")


def test_executive_penetration_does_not_reintroduce_ratio_fabrication():
    source = _executive_source()

    assert 'amt * Decimal("1.2")' not in source
    assert 'amt * Decimal("1.09")' not in source
    assert '"contract": float(camt) if camt > 0 else None' in source
    assert '"nominal": None' in source


def test_executive_after_tax_profit_uses_the_ui_formula_not_real_profit_alias():
    source = _executive_source()

    assert "rec_revenue - system_external_real_cost - tax_paid_val" in source
    assert '_metric_value(metrics, "real_profit") if facts_available else None' not in source
    assert "management_profit_after_tax_unavailable" in source


def test_project_audit_tax_categories_are_a_stable_list():
    source = (ROOT / "app" / "legacy_routes.py").read_text(encoding="utf-8")

    assert 'coverage["tax_detail"] = sorted(tax_cats)' in source
    assert "dict(tax_cats)" not in source
