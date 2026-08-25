"""Real-app HTTP regression matrix for Executive JWT/RBAC.

This module imports ``app.main.app`` (the production route assembly) instead
of mounting ``executive_mobile.router`` in isolation. The endpoint database
session is replaced with a read-only in-memory result adapter so admin and
operator requests can traverse the actual handlers without requiring a live
database; viewer requests must stop at the real RBAC dependency before any DB
call is attempted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient

from app import security
from app.routers import executive_mobile
from facts_provider.facts_provider import FactsResponse

JWT_SECRET = "real-app-executive-rbac-regression-secret-32-bytes"


def _token(role: str) -> str:
    return jwt.encode(
        {
            "sub": "7",
            "username": f"{role}.user",
            "role": role,
            "display_name": role,
            "type": "access",
        },
        JWT_SECRET,
        algorithm="HS256",
    )


@dataclass
class _Result:
    rows: list[tuple[Any, ...]]

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class _ReadOnlySession:
    """Minimal SELECT-only adapter for the real Executive handlers."""

    def execute(self, statement, params=None):
        del params
        sql = str(statement).lower()
        if "select id, project_code, name, status from projects where id" in sql:
            return _Result([(1, "P-RBAC-001", "RBAC test project", "ACTIVE")])
        if "select count(*) as total_projects" in sql:
            return _Result([(0,)])
        if "select count(*) as total_docs" in sql:
            return _Result([(0,)])
        if "group by business_role" in sql:
            return _Result([])
        if "select project_code from projects" in sql:
            return _Result([])
        if "from projects p" in sql and "left join documents" in sql:
            return _Result([])
        if "from documents" in sql:
            return _Result([])
        if "from entities" in sql:
            return _Result([])
        return _Result([])

    def close(self):
        return None


def _degraded_facts(project_code: str) -> FactsResponse:
    return FactsResponse(
        project_code=project_code,
        as_of="2026-08-23T00:00:00+00:00",
        facts_version="",
        metrics={},
        status="DEGRADED",
        facts_available=False,
        reason="test analytics source unavailable",
    )


@pytest.fixture
def real_app_client(monkeypatch):
    """Return the production app with only its read-only DB boundary stubbed."""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("JWT_SECRET_KEY", JWT_SECRET)
    # The JWT is the user credential under test; no service key is needed for
    # this matrix. RAGSecurityMiddleware remains installed and active.
    monkeypatch.setattr(security, "AUTH_REQUIRED", False)
    monkeypatch.setattr(executive_mobile, "SessionLocal", _ReadOnlySession)
    monkeypatch.setattr(executive_mobile, "_safe_get_facts", _degraded_facts)

    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


EXECUTIVE_CASES = (
    ("GET", "/api/v1/executive/cockpit/summary", None),
    ("GET", "/api/v1/executive/projects", None),
    ("GET", "/api/v1/executive/projects/1/360", None),
    ("GET", "/api/v1/executive/entities/matrix", None),
    ("POST", "/api/v1/executive/ai/chat", {"message": "风险"}),
    ("POST", "/api/v1/executive/ai/chat/stream", {"message": "风险"}),
)


@pytest.mark.parametrize("method,path,body", EXECUTIVE_CASES)
def test_real_app_viewer_is_forbidden_on_every_executive_route(
    real_app_client, method, path, body
):
    response = real_app_client.request(
        method,
        path,
        json=body,
        headers={"Authorization": f"Bearer {_token('viewer')}"},
    )

    assert response.status_code == 403, (method, path, response.text)
    assert response.json()["detail"]["code"] == "EXEC_ROLE_FORBIDDEN"


@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "Bearer not-a-jwt"},
    {"Authorization": "Basic not-a-jwt"},
])
@pytest.mark.parametrize("method,path,body", EXECUTIVE_CASES)
def test_real_app_missing_or_invalid_tax_jwt_is_unauthorized(
    real_app_client, headers, method, path, body
):
    """Every Executive route must authenticate before business processing."""
    response = real_app_client.request(method, path, json=body, headers=headers)

    assert response.status_code == 401, (method, path, headers, response.text)
    assert response.headers.get("www-authenticate") == "Bearer"


@pytest.mark.parametrize("role", ["admin", "operator"])
@pytest.mark.parametrize("method,path,body", EXECUTIVE_CASES)
def test_real_app_admin_and_operator_pass_executive_rbac(
    real_app_client, role, method, path, body
):
    response = real_app_client.request(
        method,
        path,
        json=body,
        headers={"Authorization": f"Bearer {_token(role)}"},
    )

    assert response.status_code not in {401, 403}, (
        role,
        method,
        path,
        response.status_code,
        response.text,
    )
