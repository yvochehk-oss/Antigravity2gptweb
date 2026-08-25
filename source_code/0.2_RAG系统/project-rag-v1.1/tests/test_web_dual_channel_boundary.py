"""Regression coverage for the precise Web/service compatibility boundary."""
from __future__ import annotations

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app import security
from app.auth import require_web_or_service_read, require_web_or_service_role

JWT_SECRET = "web-dual-channel-regression-secret-32-bytes"
SHARED_KEY = "dual-channel-service-key"
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

    def read(principal=Depends(require_web_or_service_read)):
        return {"ok": True}

    def write(
        principal=Depends(require_web_or_service_role("admin", "operator")),
    ):
        return {"ok": True}

    def admin_write(
        principal=Depends(require_web_or_service_role("admin")),
    ):
        return {"ok": True}

    # Exact compatibility GET routes.
    app.add_api_route("/api/v1/documents/7/original", read, methods=["GET"])
    app.add_api_route("/api/v1/regulations/7", read, methods=["GET"])
    app.add_api_route("/api/v1/mounts/fs", read, methods=["GET"])

    # User-visible dual-channel mutations.
    for path in (
        "/api/v1/regulations/verify",
        "/api/v1/regulations/save-custom",
        "/api/v1/regulations/ai-parse-url",
        "/api/v1/regulations/ai-parse-file",
        "/api/v1/mounts/scan",
    ):
        app.add_api_route(path, write, methods=["POST"])
    for path in ("/api/v1/mounts", "/api/v1/mounts/7/delete"):
        app.add_api_route(path, admin_write, methods=["POST"])

    # Similar-looking APIs remain service-only.
    for path in (
        "/api/v1/documents/7",
        "/api/v1/regulations/7/articles",
        "/api/v1/facts/service",
        "/api/v1/ai-review/review",
        "/api/v1/executive/user",
    ):
        app.add_api_route(path, lambda: {"ok": True}, methods=["GET"])
    return app


@pytest.fixture(autouse=True)
def _security_config(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("JWT_SECRET_KEY", JWT_SECRET)
    monkeypatch.setattr(security, "AUTH_REQUIRED", True)
    monkeypatch.setattr(security, "RAG_SHARED_API_KEY", SHARED_KEY)


@pytest.mark.parametrize(
    "path",
    ["/api/v1/documents/7/original", "/api/v1/regulations/7", "/api/v1/mounts/fs"],
)
def test_compatibility_get_supports_cookie_or_service_key(path):
    client = TestClient(_app())
    assert client.get(path, cookies={"cdjg_rag_token": _token()}).status_code == 200
    assert client.get(path, headers={"X-API-Key": SHARED_KEY}).status_code == 200
    assert client.get(path).status_code == 401
    assert client.get(path, cookies={"cdjg_rag_token": "invalid"}).status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/regulations/verify",
        "/api/v1/regulations/save-custom",
        "/api/v1/regulations/ai-parse-url",
        "/api/v1/regulations/ai-parse-file",
        "/api/v1/mounts/scan",
    ],
)
def test_dual_mutation_accepts_service_key_or_same_origin_cookie(path):
    client = TestClient(_app())
    assert client.post(path, headers={"X-API-Key": SHARED_KEY}).status_code == 200
    assert client.post(path, headers={"Origin": ORIGIN}, cookies={"cdjg_rag_token": _token("operator")}).status_code == 200
    assert client.post(path, headers={"Origin": "https://evil.example"}, cookies={"cdjg_rag_token": _token("operator")}).status_code == 403
    assert client.post(path, headers={"Origin": ORIGIN}, cookies={"cdjg_rag_token": _token("viewer")}).status_code == 403
    assert client.post(path, cookies={}).status_code == 401


@pytest.mark.parametrize("path", ["/api/v1/mounts", "/api/v1/mounts/7/delete"])
def test_mount_configuration_dual_mutation_remains_admin_only(path):
    client = TestClient(_app())
    assert client.post(path, headers={"X-API-Key": SHARED_KEY}).status_code == 200
    assert client.post(path, headers={"Origin": ORIGIN}, cookies={"cdjg_rag_token": _token("admin")}).status_code == 200
    assert client.post(path, headers={"Origin": ORIGIN}, cookies={"cdjg_rag_token": _token("operator")}).status_code == 403


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/documents/7",
        "/api/v1/regulations/7/articles",
        "/api/v1/facts/service",
        "/api/v1/ai-review/review",
        "/api/v1/executive/user",
    ],
)
def test_cookie_does_not_expand_into_other_api_boundaries(path):
    client = TestClient(_app())
    assert client.get(path, cookies={"cdjg_rag_token": _token()}).status_code == 401
    assert client.get(path, headers={"X-API-Key": SHARED_KEY}).status_code == 200
