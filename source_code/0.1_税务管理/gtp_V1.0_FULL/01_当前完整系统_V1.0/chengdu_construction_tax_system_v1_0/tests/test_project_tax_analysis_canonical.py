"""Unit test and HTTP route test for Canonical Facts backed project tax analysis."""
from __future__ import annotations
from decimal import Decimal
from unittest.mock import MagicMock, patch

from app.services.canonical_ledger import project_tax_analysis_summary


def test_project_tax_analysis_summary_aggregates_canonical_invoice_facts():
    mock_db = MagicMock()

    with patch("app.services.canonical_ledger._party_master", return_value=({}, {"ENT01", "ENT02"}, {"EXT01"})):
        sample_facts = [
            {
                "fact_id": 101,
                "payload": {
                    "seller_entity_code": "ENT01",
                    "buyer_entity_code": "EXT01",
                    "period": "2026-08",
                    "net_amount": "1000.00",
                    "vat_amount": "90.00",
                },
            },
            {
                "fact_id": 102,
                "payload": {
                    "seller_entity_code": "EXT01",
                    "buyer_entity_code": "ENT01",
                    "period": "2026-08",
                    "net_amount": "600.00",
                    "vat_amount": "54.00",
                    "deductible": True,
                },
            },
        ]
        with patch("app.services.canonical_ledger.load_current_facts", return_value=sample_facts):
            summary = project_tax_analysis_summary(mock_db, project_id=15, period="2026-08")

            assert summary["status"] == "READY"
            assert summary["project_id"] == 15
            assert summary["out_invoice_net"] == 1000.0
            assert summary["out_invoice_vat"] == 90.0
            assert summary["in_invoice_net"] == 600.0
            assert summary["in_invoice_vat"] == 54.0
            assert summary["deductible_input_vat"] == 54.0
            assert summary["real_cost"] == 600.0
            assert summary["invoice_count"] == 2
            assert summary["legacy_tables_used"] is False
            assert summary["source_of_truth"] == "analytics_canonical_facts_current"


def test_project_tax_analysis_http_route_returns_canonical_envelope(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    import app.middleware as middleware_module
    import app.dependencies as dependencies_module

    client = TestClient(app)
    mock_summary = {
        "status": "READY",
        "project_id": 15,
        "reporting_period": "2026-08",
        "out_invoice_net": 1000.0,
        "out_invoice_vat": 90.0,
        "in_invoice_net": 600.0,
        "in_invoice_vat": 54.0,
        "deductible_input_vat": 54.0,
        "real_cost": 600.0,
        "invoice_count": 2,
        "source_of_truth": "analytics_canonical_facts_current",
        "legacy_tables_used": False,
        "data_gaps": [],
    }
    dummy_user = type("User", (), {"id": 1, "username": "admin", "role": "admin", "active": True})()
    monkeypatch.setattr(middleware_module, "current_user_from_request", lambda req: dummy_user)
    monkeypatch.setattr(dependencies_module, "current_user_from_request", lambda req: dummy_user)

    mock_session = MagicMock()
    mock_session.get.return_value = type("Project", (), {"id": 15, "name": "Test Project"})()

    with patch("app.routers.collections.SessionLocal", return_value=mock_session):
        with patch("app.routers.collections.project_tax_analysis_summary", return_value=mock_summary):
            resp = client.get("/api/project-tax-analysis?project_id=15")
            assert resp.status_code == 200
            payload = resp.json()
            assert payload["status"] == "READY"
            assert payload["source_of_truth"] == "analytics_canonical_facts_current"
            assert payload["legacy_tables_used"] is False
            assert len(payload["items"]) == 1
            assert payload["items"][0]["out_invoice_net"] == 1000.0
