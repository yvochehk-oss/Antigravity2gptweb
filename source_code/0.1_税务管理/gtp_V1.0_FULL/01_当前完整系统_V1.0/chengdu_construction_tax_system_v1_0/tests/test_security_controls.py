"""Regression tests for session expiry, API auth responses and SSRF guards."""
from __future__ import annotations

import hmac
import time

import pytest
from fastapi.testclient import TestClient


def test_expired_signed_session_is_rejected(monkeypatch):
    from app import auth

    issued_at = int(time.time()) - auth.COOKIE_MAX_AGE - 1
    payload = f"1:{issued_at}"
    signature = hmac.new(auth._SECRET.encode(), payload.encode(), "sha256").hexdigest()[:32]
    assert auth._parse_token(f"{payload}:{signature}") is None


def test_unauthenticated_tax_api_returns_json_401(seeded_app):
    from app.main import app

    response = TestClient(app).get("/api/projects/1")
    assert response.status_code == 401
    assert response.json()["detail"]


def test_cross_origin_state_change_is_rejected(seeded_app):
    from app.main import app

    client = TestClient(app)
    login = client.post(
        "/login", data={"username": "admin", "password": "TestPass12345!"}, follow_redirects=False,
    )
    assert login.status_code == 302
    response = client.post(
        "/tasks/create",
        data={"project_id": "1", "scope": "equipment", "title": "x", "priority": "P1", "owner_role": "x"},
        headers={"Origin": "https://attacker.example"},
    )
    assert response.status_code == 403


def test_ai_endpoint_rejects_arbitrary_secret_name():
    from app.security import resolve_secret_env_name

    try:
        resolve_secret_env_name("DATABASE_URL")
    except ValueError:
        pass
    else:
        raise AssertionError("arbitrary environment variables must not be readable by AI adapters")


def test_rag_service_url_rejects_ssrf_targets_and_dns_private_addresses(monkeypatch):
    from app import security

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("TAX_RAG_ALLOWED_HOSTS", raising=False)
    with pytest.raises(ValueError):
        security.validate_rag_service_url("http://127.0.0.1:9000")
    with pytest.raises(ValueError):
        security.validate_rag_service_url("https://metadata.google.internal/v1")

    monkeypatch.setattr(
        security.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("10.0.0.7", 443))],
    )
    with pytest.raises(ValueError):
        security.validate_rag_service_url("https://rag.example.com/v1")

    monkeypatch.setattr(
        security.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("93.184.216.34", 443))],
    )
    assert security.validate_rag_service_url("https://rag.example.com/v1").endswith("/v1")
    assert security.validate_rag_service_url(
        "http://127.0.0.1:8922", allow_loopback_http=True,
    ).startswith("http://127.0.0.1")


def test_project_mapping_never_uses_legacy_stored_api_key():
    from app.routers import rag_sync

    class _Query:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return type("Mapping", (), {
                "rag_project_id": 123,
                "rag_url": rag_sync.RAG_URL,
                "rag_api_key": "legacy-secret-that-must-not-be-used",
            })()

    class _DB:
        def query(self, *args, **kwargs):
            return _Query()

    _, _, resolved_key = rag_sync._resolve_rag_project(_DB(), 1, None)
    assert resolved_key == rag_sync.RAG_API_KEY
