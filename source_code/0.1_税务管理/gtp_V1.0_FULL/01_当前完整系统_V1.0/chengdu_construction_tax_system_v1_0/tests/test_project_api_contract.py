from __future__ import annotations

from decimal import Decimal

from app.routers import api as api_router


def test_api_project_serializes_canonical_summary(monkeypatch):
    class Project:
        id = 15
        code = "P15"
        project_code = "P15"
        name = "Test"
        city = "成都"
        location = "成都"

    class DB:
        def rollback(self):
            pass
        def close(self):
            pass

    monkeypatch.setattr(api_router, "SessionLocal", lambda: DB())
    monkeypatch.setattr(
        api_router,
        "canonical_project_summary",
        lambda _db, _pid: {
            "project": Project(),
            "contract_total": Decimal("1450000000.00"),
            "contract_amount": Decimal("1450000000.00"),
            "real_cost": Decimal("208482217.42"),
            "external_cash_cost": Decimal("100000000.00"),
            "profit": Decimal("1241517782.58"),
            "margin": Decimal("0.5"),
            "vat": Decimal("0"),
            "progress": Decimal("0.2"),
            "source_of_truth": "analytics_canonical_facts_current",
            "legacy_tables_used": False,
        },
    )
    out = api_router.api_project(15, _user={"role": "admin"})
    assert out["project"]["contract_total"] == 1450000000.0
    assert out["real_cost"] == Decimal("208482217.42")
    assert out["source_of_truth"] == "analytics_canonical_facts_current"
    assert out["legacy_tables_used"] is False
