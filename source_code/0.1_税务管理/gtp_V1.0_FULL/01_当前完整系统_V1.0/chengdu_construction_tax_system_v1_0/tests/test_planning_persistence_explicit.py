"""Regression coverage for explicit planning-scenario persistence."""
from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient


def _login(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"username": "admin", "password": "TestPass12345!"},
        follow_redirects=False,
    )
    assert response.status_code == 302


def _mock_endpoint_id(SessionLocal, AIModelEndpoint) -> int:
    with SessionLocal() as db:
        endpoint = db.query(AIModelEndpoint).filter(
            AIModelEndpoint.enabled.is_(True),
            AIModelEndpoint.adapter == "mock",
        ).first()
        assert endpoint is not None
        return endpoint.id


def _scenario_count(SessionLocal, PlanningScenario, package_name: str) -> int:
    with SessionLocal() as db:
        return db.query(PlanningScenario).filter(
            PlanningScenario.created_by == "admin",
            PlanningScenario.package_name == package_name,
        ).count()


def test_allocation_planning_body_defaults_to_no_persistence():
    from app.routers.planning import AllocationPlanningBody

    body = AllocationPlanningBody(category="劳务", package_amount=1000)
    assert body.persist is False


def test_service_omitted_persist_does_not_write(seeded_app, monkeypatch):
    from app.db import SessionLocal
    from app.models import AIModelEndpoint, PlanningScenario
    from app.planning.service import recommend_project_allocation

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AI_ALLOW_MOCK_ENDPOINTS", "1")
    endpoint_id = _mock_endpoint_id(SessionLocal, AIModelEndpoint)
    package_name = f"默认不落库-{uuid4().hex}"
    payload = {
        "package_name": package_name,
        "category": "劳务",
        "package_amount": 1000,
        "objective": "balanced",
        "endpoint_id": endpoint_id,
    }

    with SessionLocal() as db:
        result = recommend_project_allocation(db, 1, payload, actor="admin")

    assert result["planning_scenario_id"] is None
    assert _scenario_count(SessionLocal, PlanningScenario, package_name) == 0


def test_planning_api_persists_only_when_explicitly_true(seeded_app, monkeypatch):
    from app.db import SessionLocal
    from app.main import app
    from app.models import AIModelEndpoint, PlanningScenario

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AI_ALLOW_MOCK_ENDPOINTS", "1")
    endpoint_id = _mock_endpoint_id(SessionLocal, AIModelEndpoint)
    client = TestClient(app)
    _login(client)

    cases = (
        ("未传persist", None, False),
        ("显式false", False, False),
        ("显式true", True, True),
    )
    for label, persist_value, should_persist in cases:
        package_name = f"{label}-{uuid4().hex}"
        body: dict[str, object] = {
            "package_name": package_name,
            "category": "劳务",
            "package_amount": 1000,
            "objective": "balanced",
            "endpoint_id": endpoint_id,
        }
        if persist_value is not None:
            body["persist"] = persist_value

        response = client.post(
            "/api/projects/1/allocation-planning/recommend",
            json=body,
        )
        assert response.status_code == 200
        scenario_id = response.json()["planning_scenario_id"]
        if should_persist:
            assert isinstance(scenario_id, int)
            assert scenario_id > 0
            assert _scenario_count(SessionLocal, PlanningScenario, package_name) == 1
        else:
            assert scenario_id is None
            assert _scenario_count(SessionLocal, PlanningScenario, package_name) == 0
