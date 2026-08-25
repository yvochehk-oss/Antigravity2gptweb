"""API contract tests for deterministic four-flow evidence completeness."""
from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient


def _login(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"username": "admin", "password": "TestPass12345!"},
        follow_redirects=False,
    )
    assert response.status_code == 302


def test_matching_response_compatibility_and_completeness_endpoint(seeded_app):
    from app.db import SessionLocal
    from app.main import app
    from app.models import Project

    client = TestClient(app)
    _login(client)

    legacy = client.get("/api/projects/1/matching")
    assert legacy.status_code == 200
    assert isinstance(legacy.json(), list)
    assert legacy.json()
    assert {
        "contract_ok",
        "fulfillment_ok",
        "invoice_ok",
        "paid_ok",
        "evidence_ok",
    }.issubset(legacy.json()[0])

    response = client.get("/api/projects/1/matching/completeness")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"AVAILABLE", "DEGRADED"}
    assert payload["source"] == "tax.deterministic.matching_rows"
    assert payload["score"] is None
    assert isinstance(payload["counts"]["by_flow"], dict)
    assert set(payload["counts"]["by_flow"]) == {
        "contract",
        "fulfillment",
        "invoice",
        "paid",
    }

    with SessionLocal() as db:
        empty_project = Project(
            code="EMPTY-MATCHING-CONTRACT",
            name="无四流匹配项目",
            city="成都",
            contract_total=Decimal("1"),
            tax_method="general",
        )
        db.add(empty_project)
        db.commit()
        empty_project_id = empty_project.id

    empty = client.get(f"/api/projects/{empty_project_id}/matching/completeness")
    assert empty.status_code == 200
    empty_payload = empty.json()
    assert empty_payload["status"] == "UNAVAILABLE"
    assert empty_payload["percentage"] is None
    assert empty_payload["score"] is None
    assert empty_payload["data_gaps"] == ["NO_MATCHING_ROWS"]

    missing = client.get("/api/projects/999999999/matching/completeness")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "项目不存在"


def test_matching_completeness_requires_auth(seeded_app):
    from app.main import app

    response = TestClient(app).get("/api/projects/1/matching/completeness")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Session"
