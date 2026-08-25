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


def test_approved_private_rag_ip_and_hostname_require_exact_dns_snapshot(monkeypatch):
    from app import security

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("TAX_RAG_ALLOWED_HOSTS", raising=False)

    # A private IP is rejected until the administrator approval flag and the
    # address snapshot are both present.
    with pytest.raises(ValueError):
        security.validate_rag_service_url("http://192.168.1.20:8922")
    assert security.validate_rag_service_url(
        "http://192.168.1.20:8922",
        allow_approved_private=True,
        approved_addresses={"192.168.1.20"},
    ) == "http://192.168.1.20:8922"

    resolved = ["192.168.1.20"]

    def fake_getaddrinfo(*args, **kwargs):
        return [(None, None, None, None, (resolved[0], 8922))]

    monkeypatch.setattr(security.socket, "getaddrinfo", fake_getaddrinfo)
    assert security.validate_rag_service_url(
        "http://rag.internal.test:8922",
        allow_approved_private=True,
        approved_addresses={"192.168.1.20"},
    ).startswith("http://rag.internal.test")

    # A later DNS answer must not silently change the approved destination.
    resolved[0] = "192.168.1.21"
    with pytest.raises(ValueError, match="DNS 解析已变化"):
        security.validate_rag_service_url(
            "http://rag.internal.test:8922",
            allow_approved_private=True,
            approved_addresses={"192.168.1.20"},
        )


def test_rag_service_url_rejects_special_ranges_and_url_obfuscation(monkeypatch):
    from app import security

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("TAX_RAG_ALLOWED_HOSTS", raising=False)
    for url in (
        "http://169.254.169.254",
        "https://224.0.0.1",
        "https://0.0.0.0",
        "https://192.0.2.1",
        "https://user:password@rag.example.test",
        "https://@rag.example.test",
        "https://rag.example.test?next=http://127.0.0.1",
        "https://rag.example.test/#fragment",
    ):
        with pytest.raises(ValueError):
            security.validate_rag_service_url(url)


def test_ai_private_llm_policy_is_narrow_and_keeps_public_allowlist(monkeypatch):
    from app import security

    monkeypatch.setenv("AI_ALLOW_PRIVATE_LLM", "1")
    monkeypatch.setenv("AI_ALLOWED_HOSTS", "provider.example.test")

    # Internal company LLMs may use either scheme when the explicit policy is
    # enabled, and the public allow-list must not accidentally block them.
    for base_url in (
        "http://127.0.0.1:9000",
        "https://localhost:9443/v1",
        "http://10.20.30.40:8000",
        "https://172.16.10.20:8443",
        "http://192.168.10.20:8080",
        "https://[fc00::20]:8443",
    ):
        assert security.validate_ai_endpoint_url(
            base_url, "/v1/chat/completions",
        ).startswith(base_url.rstrip("/"))

    # Public HTTP is never enabled by the internal exception; public HTTPS
    # remains bounded by the configured host allow-list.
    with pytest.raises(ValueError):
        security.validate_ai_endpoint_url(
            "http://provider.example.test", "/v1/chat/completions",
        )
    assert security.validate_ai_endpoint_url(
        "https://provider.example.test/v1", "/chat/completions",
    ).startswith("https://provider.example.test")
    with pytest.raises(ValueError):
        security.validate_ai_endpoint_url(
            "https://other.example.test", "/v1/chat/completions",
        )

    # These destinations remain forbidden even in an internal deployment.
    for base_url in (
        "http://169.254.169.254",
        "https://metadata.google.internal",
        "http://224.0.0.1",
        "https://0.0.0.0",
        "https://[::]",
        "https://[fe80::1%25en0]",
        "http://user:password@127.0.0.1:9000",
        "http://@127.0.0.1:9000",
    ):
        with pytest.raises(ValueError):
            security.validate_ai_endpoint_url(
                base_url, "/v1/chat/completions",
            )

    for chat_path in (
        "v1/chat/completions",
        "//other-host/chat",
        "/v1/../admin",
        "/v1/%2e%2e/admin",
        "/v1/chat?next=https://other.example.test",
        "/v1/chat#fragment",
        "/v1/https://other.example.test",
    ):
        with pytest.raises(ValueError):
            security.validate_ai_endpoint_url(
                "http://127.0.0.1:9000", chat_path,
            )

    monkeypatch.setenv("AI_ALLOW_PRIVATE_LLM", "0")
    with pytest.raises(ValueError):
        security.validate_ai_endpoint_url(
            "http://127.0.0.1:9000", "/v1/chat/completions",
        )


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
