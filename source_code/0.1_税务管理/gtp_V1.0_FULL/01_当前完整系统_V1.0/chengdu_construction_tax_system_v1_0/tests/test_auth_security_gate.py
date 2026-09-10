from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import app.middleware as middleware_module
import app.routers.auth as auth_routes
from app.auth import COOKIE_NAME, CSRF_COOKIE_NAME
from app.middleware import AuthMiddleware


def _anonymous_gate_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/")
    def root():
        return {"ok": True}

    @app.get("/api/projects/15")
    def api_project():
        return {"ok": True}

    @app.post("/manager/project/15/ask")
    def manager_ask():
        return {"ok": True}

    @app.get("/login")
    def login_get():
        return {"ok": True}

    @app.post("/api/v1/auth/token")
    def api_token():
        return {"ok": True}

    @app.post("/api/protected")
    def api_protected():
        return {"ok": True}

    return app


def test_anonymous_root_redirects_to_login(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr(
        middleware_module,
        "current_user_from_request",
        lambda request: None,
    )

    client = TestClient(_anonymous_gate_app())

    response = client.get("/", follow_redirects=False)

    assert response.status_code in {302, 303}
    assert response.headers["location"] == "/login"


def test_anonymous_api_returns_strict_401_even_in_development(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr(
        middleware_module,
        "current_user_from_request",
        lambda request: None,
    )

    client = TestClient(_anonymous_gate_app())

    response = client.get(
        "/api/projects/15",
        follow_redirects=False,
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "请先登录"}


def test_anonymous_non_api_ajax_write_also_returns_401(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr(
        middleware_module,
        "current_user_from_request",
        lambda request: None,
    )

    client = TestClient(_anonymous_gate_app())

    response = client.post(
        "/manager/project/15/ask",
        follow_redirects=False,
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "请先登录"}


def test_login_page_remains_public(monkeypatch):
    monkeypatch.setattr(
        middleware_module,
        "current_user_from_request",
        lambda request: None,
    )

    client = TestClient(_anonymous_gate_app())

    response = client.get("/login")

    assert response.status_code == 200


def test_mobile_token_bootstrap_remains_public(monkeypatch):
    monkeypatch.setattr(
        middleware_module,
        "current_user_from_request",
        lambda request: None,
    )

    client = TestClient(_anonymous_gate_app())

    response = client.post(
        "/api/v1/auth/token",
        follow_redirects=False,
    )

    assert response.status_code == 200


def test_bearer_only_api_request_does_not_require_browser_csrf(monkeypatch):
    def fake_current_user(request):
        if request.headers.get("Authorization") == "Bearer valid-test-token":
            return object()
        return None

    monkeypatch.setattr(
        middleware_module,
        "current_user_from_request",
        fake_current_user,
    )

    client = TestClient(_anonymous_gate_app())

    response = client.post(
        "/api/protected",
        headers={"Authorization": "Bearer valid-test-token"},
        follow_redirects=False,
    )

    assert response.status_code == 200


def _real_login_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(auth_routes.router)
    return app


def test_login_get_issues_csrf_cookie_and_form_token():
    client = TestClient(_real_login_app())

    response = client.get(
        "/login",
        follow_redirects=False,
    )

    assert response.status_code == 200

    csrf_token = response.cookies.get(CSRF_COOKIE_NAME)

    assert csrf_token
    assert 'name="_csrf"' in response.text
    assert f'value="{csrf_token}"' in response.text


def test_login_post_without_csrf_is_rejected():
    client = TestClient(_real_login_app())

    # 建立 CSRF cookie，但故意不提交表单 token。
    response = client.get("/login")
    assert response.status_code == 200

    response = client.post(
        "/login",
        data={
            "username": "admin",
            "password": "invalid",
        },
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF 校验失败"}


def test_login_valid_csrf_creates_session_and_redirects_root(monkeypatch):
    class FakeUser:
        id = 1
        username = "admin"
        role = "admin"
        display_name = "系统管理员"
        active = True

    monkeypatch.setattr(
        auth_routes,
        "auth_login",
        lambda username, password: FakeUser(),
    )

    client = TestClient(_real_login_app())

    login_page = client.get("/login")
    assert login_page.status_code == 200

    csrf_token = login_page.cookies.get(CSRF_COOKIE_NAME)
    assert csrf_token

    response = client.post(
        "/login",
        data={
            "username": "admin",
            "password": "test-password",
            "_csrf": csrf_token,
        },
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/"
    assert response.cookies.get(COOKIE_NAME)


def test_cross_origin_login_post_is_rejected():
    client = TestClient(_real_login_app())

    login_page = client.get("/login")
    csrf_token = login_page.cookies.get(CSRF_COOKIE_NAME)
    assert csrf_token

    response = client.post(
        "/login",
        data={
            "username": "admin",
            "password": "irrelevant",
            "_csrf": csrf_token,
        },
        headers={"Origin": "https://evil.example"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "跨站请求被拒绝"}


def test_authenticated_user_context_compatibility_and_actor(monkeypatch):
    class FakeUser:
        id = 1
        username = "admin_test"
        role = "admin"
        display_name = "管理员测试"
        active = True

    monkeypatch.setattr(
        middleware_module,
        "current_user_from_request",
        lambda request: FakeUser(),
    )

    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    captured = {}

    @app.get("/api/test-context")
    def test_ctx(request: Request):
        from app.observability import current_actor
        captured["user"] = getattr(request.state, "user", None)
        captured["current_user"] = getattr(request.state, "current_user", None)
        captured["actor"] = current_actor()
        return {"ok": True}

    client = TestClient(app)
    resp = client.get("/api/test-context", headers={"Authorization": "Bearer valid"})
    assert resp.status_code == 200
    assert captured["user"].username == "admin_test"
    assert captured["current_user"].username == "admin_test"
    assert captured["actor"] == "admin_test"


def test_security_headers_retention():
    client = TestClient(_anonymous_gate_app())

    resp1 = client.get("/login")
    assert resp1.headers.get("x-content-type-options") == "nosniff"
    assert resp1.headers.get("x-frame-options") == "DENY"
    assert resp1.headers.get("referrer-policy") == "same-origin"

    resp2 = client.get("/api/projects/15")
    assert resp2.headers.get("x-content-type-options") == "nosniff"
    assert resp2.headers.get("x-frame-options") == "DENY"
    assert resp2.headers.get("referrer-policy") == "same-origin"


def test_form_post_with_csrf_in_body_succeeds(monkeypatch):
    class FakeUser:
        id = 1
        username = "admin"
        role = "admin"
        display_name = "管理员"
        active = True

    monkeypatch.setattr(
        middleware_module,
        "current_user_from_request",
        lambda request: FakeUser(),
    )

    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.post("/test-form")
    async def test_form_endpoint(request: Request):
        form = await request.form()
        return {"name": form.get("name"), "csrf": form.get("_csrf")}

    client = TestClient(app, cookies={CSRF_COOKIE_NAME: "test-token-123", COOKIE_NAME: "session-123"})
    resp = client.post(
        "/test-form",
        data={"name": "test-model", "_csrf": "test-token-123"},
        headers={"Origin": "http://testserver"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"name": "test-model", "csrf": "test-token-123"}
