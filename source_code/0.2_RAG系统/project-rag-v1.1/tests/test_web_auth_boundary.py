"""Focused Web session/RBAC/CSRF boundary regressions.

These tests use a dependency-only harness, so no request can write the
projectrag database while the cookie and service-key policy is exercised.
"""
from __future__ import annotations

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app import security
from app.auth import TaxPrincipal, require_web_auth, require_web_role
from app.routers.auth import router as auth_router

JWT_SECRET = "web-boundary-regression-secret-32-bytes"
SHARED_KEY = "web-boundary-service-key"
ORIGIN = "http://127.0.0.1:8922"


def _token(role: str = "admin") -> str:
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


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(security.RAGSecurityMiddleware)
    app.include_router(auth_router)

    for path in ("/", "/search", "/regulations", "/mounts", "/health"):
        app.add_api_route(path, lambda principal=Depends(require_web_auth): {"ok": True}, methods=["GET"])

    @app.get("/api/v1/mounts/fs")
    def mount_fs(principal: TaxPrincipal = Depends(require_web_auth)):
        return {"ok": True}

    def write_readable(
        principal: TaxPrincipal = Depends(require_web_role("admin", "operator")),
        _csrf=Depends(security.require_same_origin),
    ):
        return {"ok": True, "role": principal.role}

    def write_admin(
        principal: TaxPrincipal = Depends(require_web_role("admin")),
        _csrf=Depends(security.require_same_origin),
    ):
        return {"ok": True, "role": principal.role}

    for path in (
        "/projects",
        "/projects/7/upload",
        "/projects/7/import-folder",
        "/documents/7/parse",
        "/api/v1/mounts/scan",
        "/api/v1/regulations/verify",
        "/api/v1/regulations/save-custom",
        "/api/v1/regulations/ai-parse-url",
        "/api/v1/regulations/ai-parse-file",
        "/api/v1/regulations/batch-import-dir",
        "/api/v1/regulations/pkulaw-sync",
    ):
        app.add_api_route(path, write_readable, methods=["POST"])
    for path in ("/api/v1/mounts", "/api/v1/mounts/7/delete"):
        app.add_api_route(path, write_admin, methods=["POST"])

    @app.get("/api/v1/facts/service")
    def service_only():
        return {"ok": True}

    @app.get("/api/v1/executive/user")
    def executive_without_cookie_auth():
        return {"ok": True}

    return app


@pytest.fixture(autouse=True)
def _security_config(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("JWT_SECRET_KEY", JWT_SECRET)
    monkeypatch.setattr(security, "AUTH_REQUIRED", True)
    monkeypatch.setattr(security, "RAG_SHARED_API_KEY", SHARED_KEY)


@pytest.mark.parametrize("path", ["/", "/search", "/regulations", "/mounts", "/health", "/api/v1/mounts/fs"])
def test_web_navigation_missing_or_expired_cookie_redirects(path):
    client = TestClient(_app())
    for cookies in ({}, {"cdjg_rag_token": "expired"}):
        response = client.get(path, cookies=cookies, follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/login"


@pytest.mark.parametrize("path", ["/", "/search", "/regulations", "/mounts", "/health", "/api/v1/mounts/fs"])
def test_web_navigation_accepts_valid_cookie(path):
    response = TestClient(_app()).get(path, cookies={"cdjg_rag_token": _token()})
    assert response.status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/projects",
        "/projects/7/upload",
        "/projects/7/import-folder",
        "/documents/7/parse",
        "/api/v1/mounts/scan",
        "/api/v1/regulations/verify",
        "/api/v1/regulations/save-custom",
        "/api/v1/regulations/ai-parse-url",
        "/api/v1/regulations/ai-parse-file",
        "/api/v1/regulations/batch-import-dir",
        "/api/v1/regulations/pkulaw-sync",
    ],
)
def test_web_mutations_require_cookie_same_origin_and_write_role(path):
    client = TestClient(_app())
    headers = {"Origin": ORIGIN}
    valid = client.post(path, headers=headers, cookies={"cdjg_rag_token": _token("operator")})
    assert valid.status_code == 200
    assert client.post(path, headers=headers, cookies={"cdjg_rag_token": _token("viewer")}).status_code == 403
    assert client.post(path, headers=headers, cookies={}).status_code == 401
    assert client.post(path, headers={"Origin": "https://evil.example"}, cookies={"cdjg_rag_token": _token("operator")}).status_code == 403


@pytest.mark.parametrize("path", ["/api/v1/mounts", "/api/v1/mounts/7/delete"])
def test_mount_configuration_is_admin_only(path):
    client = TestClient(_app())
    assert client.post(path, headers={"Origin": ORIGIN}, cookies={"cdjg_rag_token": _token("operator")}).status_code == 403
    assert client.post(path, headers={"Origin": ORIGIN}, cookies={"cdjg_rag_token": _token("admin")}).status_code == 200


@pytest.mark.parametrize("path", ["/api/v1/facts/service", "/api/v1/executive/user"])
def test_cookie_cannot_replace_service_or_api_auth(path):
    client = TestClient(_app())
    assert client.get(path, cookies={"cdjg_rag_token": _token("admin")}).status_code == 401
    assert client.get(path, headers={"X-API-Key": SHARED_KEY}).status_code == 200
