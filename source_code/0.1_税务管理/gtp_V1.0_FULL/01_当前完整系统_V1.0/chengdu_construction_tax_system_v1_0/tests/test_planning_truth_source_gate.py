"""Regression coverage for Planning dual-scope truth-source boundaries."""
from __future__ import annotations

import ast
import inspect

from fastapi.testclient import TestClient

EXPECTED_FACT_BASIS = {
    "project": "CANONICAL_FACTS",
    "entity_vat": "entity_vat_ledgers",
    "cit": "UNAVAILABLE",
}


def _login(client: TestClient) -> str:
    login_page = client.get("/login")
    assert login_page.status_code == 200
    pre_login_csrf = client.cookies.get("tax_csrf")
    assert pre_login_csrf

    response = client.post(
        "/login",
        data={
            "username": "admin",
            "password": "888888",
            "_csrf": pre_login_csrf,
        },
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert client.cookies.get("tax_session")

    post_login_csrf = client.cookies.get("tax_csrf")
    assert post_login_csrf
    return post_login_csrf


def _mock_endpoint_id(SessionLocal, AIModelEndpoint) -> int:
    with SessionLocal() as db:
        endpoint = db.query(AIModelEndpoint).filter(
            AIModelEndpoint.enabled.is_(True),
            AIModelEndpoint.adapter == "mock",
        ).first()
        assert endpoint is not None
        return endpoint.id


def test_planning_service_has_no_taxledger_dependency():
    from app.planning import service

    source = inspect.getsource(service)
    tree = ast.parse(source)
    referenced_names = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
    }
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert "TaxLedger" not in imported_names
    assert "TaxLedger" not in referenced_names


def test_truth_safe_response_protects_simulation_scope_and_fact_basis():
    from app.routers.planning import _truth_safe_planning_response

    normalized = _truth_safe_planning_response({
        "scenario_scope": "LEGAL_ENTITY_STATUTORY",
        "is_filing_basis": True,
        "fact_basis": {
            "project": "LEGACY",
            "entity_vat": "TaxLedger",
            "cit": "estimated_cit",
        },
        "planning_basis": {},
    })

    assert normalized["scenario_scope"] == "SIMULATION"
    assert normalized["is_filing_basis"] is False
    assert normalized["fact_basis"] == EXPECTED_FACT_BASIS
    assert "CIT_ESTIMATE_UNAVAILABLE" in normalized["planning_basis"]["note"]


def test_planning_api_self_reports_truth_sources(seeded_app, monkeypatch):
    from app.db import SessionLocal
    from app.main import app
    from app.models import AIModelEndpoint

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AI_ALLOW_MOCK_ENDPOINTS", "1")
    endpoint_id = _mock_endpoint_id(SessionLocal, AIModelEndpoint)

    client = TestClient(app)
    csrf_token = _login(client)
    response = client.post(
        "/api/projects/1/allocation-planning/recommend",
        json={
            "package_name": "truth-source-gate",
            "category": "劳务",
            "package_amount": 1000,
            "objective": "balanced",
            "endpoint_id": endpoint_id,
            "persist": False,
        },
        headers={
            "Origin": "http://testserver",
            "X-CSRF-Token": csrf_token,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scenario_scope"] == "SIMULATION"
    assert body["is_filing_basis"] is False
    assert body["fact_basis"] == EXPECTED_FACT_BASIS
    assert body["planning_scenario_id"] is None
