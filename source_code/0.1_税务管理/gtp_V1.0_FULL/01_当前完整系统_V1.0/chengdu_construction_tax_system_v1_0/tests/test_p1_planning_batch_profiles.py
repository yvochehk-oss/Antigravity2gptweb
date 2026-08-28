"""Regression guards for planning candidate batch loading."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_planning_profiles_use_grouped_batch_queries():
    source = (ROOT / "app" / "planning" / "bulk_profiles.py").read_text(encoding="utf-8")
    init = (ROOT / "app" / "planning" / "__init__.py").read_text(encoding="utf-8")

    assert "group_by(Invoice.entity_code)" in source
    assert "group_by(Invoice.entity_code, Invoice.project_id)" in source
    assert "group_by(Invoice.counterparty_code)" in source
    assert "group_by(Fulfillment.counterparty_code)" in source
    assert "install_bulk_planning_context()" in init
    assert "for e in entities" in source
    assert "_internal_profile(e, internal_stats.get(e.code, {}))" in source
