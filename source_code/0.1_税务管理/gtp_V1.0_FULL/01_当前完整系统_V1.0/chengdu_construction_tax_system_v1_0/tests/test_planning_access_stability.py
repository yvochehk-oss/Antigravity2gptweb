"""Access and persistence regression tests for project allocation planning."""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient


def _login(client: TestClient, username: str = "admin") -> None:
    response = client.post(
        "/login",
        data={"username": username, "password": "TestPass12345!"},
        follow_redirects=False,
    )
    assert response.status_code == 302


def _planning_body(*, package_name: str = "稳定性回归") -> dict[str, object]:
    return {
        "package_name": package_name,
        "category": "劳务",
        "package_amount": 1000,
        "objective": "balanced",
        "persist": True,
    }


def test_project_and_planning_api_are_not_public():
    """The old /api/projects public prefix must not bypass AuthMiddleware."""
    from app.main import app
    from app.middleware import _is_public_path

    assert not _is_public_path("/api/projects")
    assert not _is_public_path("/api/projects/1")
    assert not _is_public_path("/api/projects/1/allocation-planning/context")
    assert _is_public_path("/docs")
    assert _is_public_path("/docs/oauth2-redirect")

    client = TestClient(app)
    for path in (
        "/api/projects/1",
        "/api/projects/1/matching",
        "/api/projects/1/allocation-planning/context",
        "/api/projects/1/system-penetration",
    ):
        response = client.get(path)
        assert response.status_code == 401, path
        assert response.headers["WWW-Authenticate"] == "Session"
        assert response.json()["detail"]


def test_planning_page_keeps_html_login_redirect(seeded_app):
    from app.main import app

    response = TestClient(app).get("/manager/project/1/planning", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith("/login?next=/manager/project/1/planning")


def test_planning_role_and_csrf_boundaries(seeded_app):
    from app.main import app

    operator = TestClient(app)
    _login(operator, "operator")
    assert operator.get("/api/projects/1/allocation-planning/context").status_code == 403
    assert operator.get("/manager/project/1/planning").status_code == 403
    assert operator.post(
        "/api/projects/1/allocation-planning/recommend",
        json=_planning_body(),
    ).status_code == 403

    admin = TestClient(app)
    _login(admin)
    response = admin.post(
        "/api/projects/1/allocation-planning/recommend",
        json=_planning_body(),
        headers={"Origin": "https://attacker.example"},
    )
    assert response.status_code == 403


def test_valid_csrf_token_does_not_override_cross_origin(seeded_app):
    from app.main import app

    client = TestClient(app)
    _login(client)
    csrf_token = client.cookies.get("tax_csrf")
    assert csrf_token
    payload = {
        "project_id": "1",
        "scope": "equipment",
        "title": "跨源 CSRF 回归",
        "priority": "P1",
        "owner_role": "测试",
    }
    cross_origin = client.post(
        "/tasks/create",
        data=payload,
        headers={
            "Origin": "https://attacker.example",
            "X-CSRF-Token": csrf_token,
        },
    )
    assert cross_origin.status_code == 403

    same_origin = client.post(
        "/tasks/create",
        data=payload,
        headers={
            "Origin": "http://testserver",
            "X-CSRF-Token": csrf_token,
        },
        follow_redirects=False,
    )
    assert same_origin.status_code == 303


def test_planning_replay_is_idempotent_for_same_key(seeded_app, monkeypatch):
    from app.db import SessionLocal
    from app.main import app
    from app.models import AIModelEndpoint, PlanningScenario

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AI_ALLOW_MOCK_ENDPOINTS", "1")
    client = TestClient(app)
    _login(client)
    with SessionLocal() as db:
        endpoint = db.query(AIModelEndpoint).filter(
            AIModelEndpoint.enabled.is_(True), AIModelEndpoint.adapter == "mock",
        ).first()
        assert endpoint is not None
        endpoint_id = endpoint.id

    key = f"planning-{uuid4().hex}"
    body = _planning_body(package_name=f"幂等回归-{key}")
    body["endpoint_id"] = endpoint_id
    headers = {"Idempotency-Key": key}
    first = client.post("/api/projects/1/allocation-planning/recommend", json=body, headers=headers)
    second = client.post("/api/projects/1/allocation-planning/recommend", json=body, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["planning_scenario_id"] == second.json()["planning_scenario_id"]

    with SessionLocal() as db:
        count = db.query(PlanningScenario).filter(
            PlanningScenario.created_by == "admin",
            PlanningScenario.package_name == body["package_name"],
        ).count()
    assert count == 1


def test_planning_browser_retry_without_key_is_deduplicated(seeded_app, monkeypatch):
    """The browser page does not set a header, so its double-click is covered too."""
    from app.db import SessionLocal
    from app.main import app
    from app.models import AIModelEndpoint, PlanningScenario

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AI_ALLOW_MOCK_ENDPOINTS", "1")
    client = TestClient(app)
    _login(client)
    with SessionLocal() as db:
        endpoint = db.query(AIModelEndpoint).filter(
            AIModelEndpoint.enabled.is_(True), AIModelEndpoint.adapter == "mock",
        ).first()
        assert endpoint is not None
        endpoint_id = endpoint.id

    package_name = f"浏览器重试-{uuid4().hex}"
    body = _planning_body(package_name=package_name)
    body["endpoint_id"] = endpoint_id
    first = client.post("/api/projects/1/allocation-planning/recommend", json=body)
    second = client.post("/api/projects/1/allocation-planning/recommend", json=body)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["planning_scenario_id"] == second.json()["planning_scenario_id"]

    with SessionLocal() as db:
        count = db.query(PlanningScenario).filter(
            PlanningScenario.created_by == "admin",
            PlanningScenario.package_name == package_name,
        ).count()
    assert count == 1


def test_planning_persist_failure_rolls_back_parent_and_children(seeded_app, monkeypatch):
    """A child-row failure must not leave a visible parent scenario behind."""
    from app.db import SessionLocal
    from app.models import AIModelEndpoint, PlanningScenario
    from app.planning import service

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AI_ALLOW_MOCK_ENDPOINTS", "1")
    with SessionLocal() as db:
        endpoint = db.query(AIModelEndpoint).filter(
            AIModelEndpoint.enabled.is_(True), AIModelEndpoint.adapter == "mock",
        ).first()
        assert endpoint is not None
        endpoint_id = endpoint.id

    class BrokenAllocation:
        def __init__(self, **kwargs):
            raise RuntimeError("allocation insert sentinel")

    monkeypatch.setattr(service, "PlanningAllocation", BrokenAllocation)
    package_name = f"失败回滚-{uuid4().hex}"
    payload = _planning_body(package_name=package_name)
    payload["endpoint_id"] = endpoint_id
    with SessionLocal() as db:
        with pytest.raises(RuntimeError, match="allocation insert sentinel"):
            service.recommend_project_allocation(db, 1, payload, actor="admin")
        assert db.query(PlanningScenario).filter(
            PlanningScenario.created_by == "admin",
            PlanningScenario.package_name == package_name,
        ).count() == 0
