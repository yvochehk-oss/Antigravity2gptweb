"""Regression tests for the RAG same-origin Tax authentication bridge."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import auth as auth_router_module


class _FakeAsyncClient:
    response: httpx.Response | None = None
    error: Exception | None = None
    calls: list[dict] = []

    def __init__(self, **kwargs):
        self.options = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


@pytest.fixture(autouse=True)
def _reset_fake_client(monkeypatch):
    _FakeAsyncClient.response = None
    _FakeAsyncClient.error = None
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(auth_router_module.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.delenv("TAX_AUTH_SERVICE_URL", raising=False)
    monkeypatch.delenv("RAG_COOKIE_SECURE", raising=False)


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(auth_router_module.router)
    return app


def _client() -> TestClient:
    """Use the default local RAG origin for normal browser-like requests."""
    return TestClient(_app(), headers={"Origin": "http://127.0.0.1:8922"})


def _upstream(status_code: int, payload: dict) -> httpx.Response:
    return httpx.Response(status_code, json=payload, request=httpx.Request("POST", "http://tax.test"))


def test_127_loopback_origin_sets_http_only_cookie_and_does_not_return_tax_token():
    token = "tax-access-token-that-must-not-reach-javascript"
    _FakeAsyncClient.response = _upstream(
        200,
        {"access_token": token, "refresh_token": "refresh-token", "expires_in": 3600},
    )

    response = _client().post(
        "/api/v1/auth/session",
        json={"username": "admin", "password": "correct-password"},
    )

    assert response.status_code == 200
    assert response.json() == {"authenticated": True}
    assert token not in response.text
    set_cookie = response.headers["set-cookie"]
    assert "cdjg_rag_token=" + token in set_cookie
    assert "HttpOnly" in set_cookie
    assert "samesite=lax" in set_cookie.lower()
    assert "Path=/" in set_cookie
    assert "Max-Age=3600" in set_cookie
    assert _FakeAsyncClient.calls[0]["url"] == "http://127.0.0.1:8921/api/v1/auth/token"
    assert _FakeAsyncClient.calls[0]["data"] == {
        "username": "admin",
        "password": "correct-password",
    }
    assert "?" not in _FakeAsyncClient.calls[0]["url"]


def test_success_accepts_form_body_and_caps_untrusted_expiry():
    token = "token-with-bounded-lifetime"
    _FakeAsyncClient.response = _upstream(200, {"access_token": token, "expires_in": "999999999"})

    response = _client().post(
        "/api/v1/auth/session",
        data={"username": "operator", "password": "password"},
    )

    assert response.status_code == 200
    assert "Max-Age=604800" in response.headers["set-cookie"]


def test_upstream_unauthorized_is_safe_and_does_not_echo_upstream_body():
    secret_detail = "internal auth detail with token=do-not-leak"
    _FakeAsyncClient.response = _upstream(401, {"detail": secret_detail})

    response = _client().post(
        "/api/v1/auth/session",
        json={"username": "admin", "password": "wrong"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "用户名或密码错误"}
    assert secret_detail not in response.text
    assert "set-cookie" not in response.headers


def test_upstream_unavailable_returns_503_without_sensitive_details():
    _FakeAsyncClient.error = httpx.ConnectError("password=secret and token=secret")

    response = _client().post(
        "/api/v1/auth/session",
        json={"username": "admin", "password": "password"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "认证服务暂时不可用，请稍后重试"}
    assert "secret" not in response.text


def test_credentials_in_query_string_are_rejected_and_not_forwarded():
    _FakeAsyncClient.response = _upstream(200, {"access_token": "must-not-be-used"})

    response = _client().post(
        "/api/v1/auth/session?username=admin&password=secret",
        json={"username": "admin", "password": "secret"},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "用户名和密码必须通过 POST 请求体提交"}
    assert _FakeAsyncClient.calls == []


def test_invalid_server_url_is_rejected_without_outbound_request(monkeypatch):
    monkeypatch.setenv("TAX_AUTH_SERVICE_URL", "http://169.254.169.254/latest/meta-data?token=secret")
    _FakeAsyncClient.response = _upstream(200, {"access_token": "must-not-be-used"})

    response = _client().post(
        "/api/v1/auth/session",
        json={"username": "admin", "password": "password"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "认证服务配置不可用"}
    assert _FakeAsyncClient.calls == []


def test_login_template_uses_same_origin_post_and_does_not_expose_credentials_or_token():
    template = (Path(__file__).parents[1] / "app" / "templates" / "login.html").read_text(encoding="utf-8")
    base_template = (Path(__file__).parents[1] / "app" / "templates" / "base.html").read_text(encoding="utf-8")

    assert "fetch('/api/v1/auth/session'" in template
    assert "JSON.stringify({ username: u, password: p })" in template
    assert "8921/api/v1/auth/token" not in template
    assert "?username=" not in template
    assert "document.cookie" not in template
    assert "access_token" not in template
    assert "888888" not in template
    assert 'method="post"' in base_template
    assert 'action="/api/v1/auth/logout"' in base_template
    assert "document.cookie" not in base_template


def test_cross_origin_session_is_rejected_before_forwarding_credentials():
    _FakeAsyncClient.response = _upstream(200, {"access_token": "must-not-be-used"})

    response = _client().post(
        "/api/v1/auth/session",
        json={"username": "admin", "password": "password"},
        headers={"Origin": "https://evil.example"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "请求来源未通过同源校验"}
    assert _FakeAsyncClient.calls == []
    assert "must-not-be-used" not in response.text


@pytest.mark.parametrize("origin", ["http://192.168.50.20:8922", "https://evil.example"])
def test_non_loopback_or_unknown_origin_is_rejected_before_forwarding_credentials(origin):
    """The local browser contract must not trust LAN or attacker origins."""
    _FakeAsyncClient.response = _upstream(200, {"access_token": "must-not-be-used"})

    response = _client().post(
        "/api/v1/auth/session",
        json={"username": "admin", "password": "password"},
        headers={"Origin": origin},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "请求来源未通过同源校验"}
    assert _FakeAsyncClient.calls == []


def test_missing_origin_and_referer_are_rejected_closed():
    _FakeAsyncClient.response = _upstream(200, {"access_token": "must-not-be-used"})

    response = TestClient(_app()).post(
        "/api/v1/auth/session",
        json={"username": "admin", "password": "password"},
    )

    assert response.status_code == 403
    assert _FakeAsyncClient.calls == []


def test_same_origin_referer_is_accepted_when_origin_header_is_absent():
    token = "referer-session-token"
    _FakeAsyncClient.response = _upstream(200, {"access_token": token, "expires_in": 300})

    response = TestClient(_app()).post(
        "/api/v1/auth/session",
        json={"username": "admin", "password": "password"},
        headers={"Referer": "http://127.0.0.1:8922/login"},
    )

    assert response.status_code == 200
    assert token not in response.text


def test_origin_takes_precedence_over_a_same_origin_referer():
    _FakeAsyncClient.response = _upstream(200, {"access_token": "must-not-be-used"})

    response = _client().post(
        "/api/v1/auth/session",
        json={"username": "admin", "password": "password"},
        headers={
            "Origin": "https://evil.example",
            "Referer": "http://127.0.0.1:8922/login",
        },
    )

    assert response.status_code == 403
    assert _FakeAsyncClient.calls == []


def test_configured_reverse_proxy_origin_is_explicitly_allowed(monkeypatch):
    monkeypatch.setenv("RAG_CSRF_ALLOWED_ORIGINS", "https://rag.example.com")
    _FakeAsyncClient.response = _upstream(200, {"access_token": "proxy-session-token"})

    response = TestClient(_app()).post(
        "/api/v1/auth/session",
        json={"username": "admin", "password": "password"},
        headers={"Origin": "https://rag.example.com"},
    )

    assert response.status_code == 200


def test_forwarded_headers_cannot_create_a_trusted_origin():
    _FakeAsyncClient.response = _upstream(200, {"access_token": "must-not-be-used"})

    response = TestClient(_app()).post(
        "/api/v1/auth/session",
        json={"username": "admin", "password": "password"},
        headers={
            "Origin": "https://evil.example",
            "X-Forwarded-Host": "rag.example.com",
            "X-Forwarded-Proto": "https",
        },
    )

    assert response.status_code == 403
    assert _FakeAsyncClient.calls == []


def test_logout_expires_http_only_cookie_and_redirects_to_login():
    client = _client()
    client.cookies.set("cdjg_rag_token", "server-side-token")

    response = client.post("/api/v1/auth/logout", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    set_cookie = response.headers["set-cookie"]
    assert "cdjg_rag_token=" in set_cookie
    assert "max-age=0" in set_cookie.lower()
    assert "expires=" in set_cookie.lower()
    assert "HttpOnly" in set_cookie
    assert "server-side-token" not in response.text
    assert "server-side-token" not in set_cookie


def test_logout_is_post_only():
    response = _client().get("/api/v1/auth/logout")

    assert response.status_code == 405
    assert "set-cookie" not in response.headers
