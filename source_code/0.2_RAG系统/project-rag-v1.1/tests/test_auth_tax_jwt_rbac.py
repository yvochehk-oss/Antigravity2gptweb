"""Regression tests for the RAG Tax-JWT RBAC contract."""
from __future__ import annotations

import jwt
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.auth import TaxPrincipal, require_exec_role

JWT_SECRET = "tax-jwt-rbac-regression-secret-32-bytes"


def _token(role: str, *, subject: str = "7") -> str:
    return jwt.encode(
        {
            "sub": subject,
            "username": f"{role}.user",
            "role": role,
            "display_name": role,
            "type": "access",
        },
        JWT_SECRET,
        algorithm="HS256",
    )


def _app():
    app = FastAPI()

    @app.get("/protected")
    def protected(
        principal: TaxPrincipal = Depends(require_exec_role("admin", "operator")),
    ):
        return {"role": principal.role, "source": principal.source}

    return app


def test_tax_admin_and_operator_can_access_exec_rbac(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("JWT_SECRET_KEY", JWT_SECRET)
    client = TestClient(_app())

    for role in ("admin", "operator"):
        response = client.get("/protected", headers={"Authorization": f"Bearer {_token(role)}"})
        assert response.status_code == 200
        assert response.json()["role"] == role
        assert response.json()["source"] == "tax_jwt"


def test_tax_rbac_returns_401_for_missing_or_invalid_credentials(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("JWT_SECRET_KEY", JWT_SECRET)
    client = TestClient(_app())

    assert client.get("/protected").status_code == 401
    assert client.get("/protected", headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401
    assert client.get("/protected", headers={"Authorization": "Basic not-a-jwt"}).status_code == 401


def test_tax_rbac_returns_403_for_valid_but_disallowed_role(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("JWT_SECRET_KEY", JWT_SECRET)
    client = TestClient(_app())

    response = client.get("/protected", headers={"Authorization": f"Bearer {_token('viewer')}"})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "EXEC_ROLE_FORBIDDEN"


def test_legacy_exec_tokens_are_not_accepted_outside_test_mode(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", JWT_SECRET)
    monkeypatch.setenv("RAG_EXEC_TOKENS", '{"legacy-token":"admin"}')
    monkeypatch.setenv("RAG_EXEC_DEMO_TOKENS", '{"demo-token":"admin"}')
    monkeypatch.setenv("RAG_EXEC_INTERNAL_TOKEN", "internal-token")
    client = TestClient(_app())

    for token in ("legacy-token", "demo-token", "internal-token"):
        assert client.get("/protected", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_malformed_jwt_subject_is_rejected_as_401(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("JWT_SECRET_KEY", JWT_SECRET)
    malformed = jwt.encode(
        {"sub": "not-an-integer", "role": "admin", "type": "access"},
        JWT_SECRET,
        algorithm="HS256",
    )
    response = TestClient(_app()).get("/protected", headers={"Authorization": f"Bearer {malformed}"})
    assert response.status_code == 401


def test_signed_tax_jwt_cannot_use_legacy_internal_source_to_bypass_rbac(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("JWT_SECRET_KEY", JWT_SECRET)
    token = jwt.encode(
        {"sub": "7", "role": "viewer", "source": "test_internal", "type": "access"},
        JWT_SECRET,
        algorithm="HS256",
    )
    response = TestClient(_app()).get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
