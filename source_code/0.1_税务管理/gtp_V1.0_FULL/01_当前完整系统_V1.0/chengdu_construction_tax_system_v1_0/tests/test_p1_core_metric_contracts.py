"""P1 regression guards for the shared management EAC contract."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]


def test_tax_project_summary_has_no_private_eac_threshold():
    source = (ROOT / "app" / "calc" / "project.py").read_text(encoding="utf-8")
    assert 'Decimal("0.05")' not in source
    assert "canonical_management_eac_cost(" in source
    assert "canonical_management_revenue_progress_v1" in source


def test_shared_eac_function_extrapolates_early_stage_without_second_formula(seeded_app):
    from app.db import SessionLocal

    with SessionLocal() as db:
        value = db.scalar(
            text(
                "SELECT canonical_management_eac_cost(" 
                "CAST(:contract AS numeric), CAST(:revenue AS numeric), CAST(:cost AS numeric))"
            ),
            {"contract": "1000", "revenue": "10", "cost": "5"},
        )
    assert Decimal(str(value)) == Decimal("500")


def test_shared_eac_never_drops_below_actual_cost(seeded_app):
    from app.db import SessionLocal

    with SessionLocal() as db:
        value = db.scalar(
            text(
                "SELECT canonical_management_eac_cost(" 
                "CAST(:contract AS numeric), CAST(:revenue AS numeric), CAST(:cost AS numeric))"
            ),
            {"contract": "1000", "revenue": "1200", "cost": "900"},
        )
    assert Decimal(str(value)) == Decimal("900")
