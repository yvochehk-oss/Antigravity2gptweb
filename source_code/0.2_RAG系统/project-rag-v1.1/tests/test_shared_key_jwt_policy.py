"""Regression tests for the RAG service-key versus Tax-JWT boundary.

The RAG process has two distinct callers:

* the executive/adaptive user APIs, which authenticate a Tax user and then
  apply endpoint RBAC; and
* the Tax-to-RAG/internal bridge, which authenticates with
  ``RAG_SHARED_API_KEY``.

These tests exercise the global middleware together with the real RBAC
dependency so a configured service key cannot become an accidental second
factor for users, and a user JWT cannot become a service credential.
"""
from __future__ import annotations

import jwt
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from app import security
from app.auth import TaxPrincipal, require_exec_role, require_web_auth
from app.routers.auth import router as auth_router

JWT_SECRET = "shared-key-jwt-policy-regression-secret-32-bytes"
SHARED_KEY = "service-credential-for-rag-policy-tests"


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

    @app.get("/api/v1/executive/user")
    def user_api(
        principal: TaxPrincipal = Depends(require_exec_role("admin", "operator")),
    ):
        return {"ok": True, "role": principal.role}

    @app.get("/api/v1/facts/service")
    def service_api():
        return {"ok": True}

    @app.get("/api/v1/retrieve")
    def legacy_retrieve_api():
        # This legacy route has no JWT dependency of its own, so a Tax JWT
        # must not make the global service-key check disappear for it.
        return {"ok": True}

    @app.get("/api/v1/retrieve/adaptive")
    def adaptive_retrieve_api(
        principal: TaxPrincipal = Depends(require_exec_role("admin", "operator")),
    ):
        return {"ok": True, "role": principal.role}

    @app.get("/", name="web_root")
    def web_root(request: Request, principal: TaxPrincipal = Depends(require_web_auth)):
        return {"ok": True, "role": principal.role}

    @app.get("/projects/{project_ref}")
    def web_project(project_ref: str, principal: TaxPrincipal = Depends(require_web_auth)):
        return {"ok": True, "project_ref": project_ref, "role": principal.role}

    @app.post("/projects/{project_id}/upload")
    def web_upload_without_route_auth(project_id: int):
        # This intentionally models the current mutation handler: a broad
        # /projects prefix must not make it reachable with only a cookie.
        return {"ok": True, "project_id": project_id}

    return app


def _configure(monkeypatch, *, auth_required: bool = True, shared_key: str = SHARED_KEY):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("JWT_SECRET_KEY", JWT_SECRET)
    monkeypatch.setattr(security, "AUTH_REQUIRED", auth_required)
    monkeypatch.setattr(security, "RAG_SHARED_API_KEY", shared_key)


def test_valid_tax_jwt_is_not_blocked_by_configured_shared_key(monkeypatch):
    _configure(monkeypatch)
    client = TestClient(_app())

    response = client.get(
        "/api/v1/executive/user",
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "role": "admin"}


def test_valid_tax_jwt_cookie_reaches_explicit_web_route(monkeypatch):
    """The Web cookie satisfies only the global gate; the route still verifies it."""
    _configure(monkeypatch)
    client = TestClient(_app())

    response = client.get("/", cookies={"cdjg_rag_token": _token()})

    assert response.status_code == 200
    assert response.json() == {"ok": True, "role": "admin"}


def test_missing_or_invalid_web_cookie_remains_rejected(monkeypatch):
    _configure(monkeypatch)
    client = TestClient(_app())

    # Explicit Web GET routes must reach their route-level dependency so a
    # missing session receives the browser-friendly login redirect.
    assert client.get("/", follow_redirects=False).status_code == 302
    invalid_cookie = client.get(
        "/",
        cookies={"cdjg_rag_token": "expired-or-invalid"},
        follow_redirects=False,
    )
    assert invalid_cookie.status_code == 302

    # If the caller supplies the independent service key, the middleware may
    # reach the Web handler; the handler then owns the invalid-cookie redirect.
    route_response = client.get(
        "/",
        cookies={"cdjg_rag_token": "expired-or-invalid"},
        headers={"X-API-Key": SHARED_KEY},
        follow_redirects=False,
    )
    assert route_response.status_code == 302
    assert route_response.headers["location"] == "/login"


def test_cookie_does_not_bypass_unprotected_web_mutation_or_legacy_route(monkeypatch):
    _configure(monkeypatch)
    client = TestClient(_app())
    cookies = {"cdjg_rag_token": _token()}

    # The mutation is deliberately not in the cookie bypass set until it has
    # its own require_web_auth dependency.
    assert client.post("/projects/7/upload", cookies=cookies).status_code == 401
    assert client.get("/api/v1/retrieve", cookies=cookies).status_code == 401
    assert client.get("/api/v1/executive/user", cookies=cookies).status_code == 401
    assert client.get("/api/v1/facts/service", cookies=cookies).status_code == 401
    assert client.get("/api/v1/ai-review/review", cookies=cookies).status_code == 401


def test_login_and_same_origin_session_are_not_blocked_by_service_key(monkeypatch):
    """The login flow must be able to establish the cookie without a key."""
    _configure(monkeypatch)
    app = FastAPI()
    app.add_middleware(security.RAGSecurityMiddleware)

    @app.get("/login")
    def login_page():
        return {"page": "login"}

    @app.post("/api/v1/auth/session")
    def session_bridge():
        return {"authenticated": False}

    client = TestClient(app)
    assert client.get("/login").status_code == 200
    assert client.post("/api/v1/auth/session").status_code == 200


def test_auth_verify_reads_tax_jwt_from_authorization_header(monkeypatch):
    _configure(monkeypatch)
    client = TestClient(_app())

    response = client.get(
        "/api/v1/auth/verify",
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert response.status_code == 200
    assert response.json()["valid"] is True


def test_valid_tax_jwt_works_when_shared_key_is_not_required(monkeypatch):
    _configure(monkeypatch, auth_required=False, shared_key="")
    client = TestClient(_app())

    response = client.get(
        "/api/v1/executive/user",
        headers={"Authorization": f"Bearer {_token('operator')}"},
    )

    assert response.status_code == 200
    assert response.json()["role"] == "operator"


def test_valid_tax_jwt_is_not_blocked_on_adaptive_user_route(monkeypatch):
    _configure(monkeypatch)
    client = TestClient(_app())

    response = client.get(
        "/api/v1/retrieve/adaptive",
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert response.status_code == 200


def test_user_route_still_enforces_jwt_and_rbac(monkeypatch):
    _configure(monkeypatch)
    client = TestClient(_app())

    # The service key can pass the global middleware but cannot satisfy the
    # user route's JWT dependency.
    service_only = client.get(
        "/api/v1/executive/user",
        headers={"Authorization": f"Bearer {SHARED_KEY}"},
    )
    assert service_only.status_code == 401

    viewer = client.get(
        "/api/v1/executive/user",
        headers={"Authorization": f"Bearer {_token('viewer')}"},
    )
    assert viewer.status_code == 403

    wrong_both = client.get(
        "/api/v1/executive/user",
        headers={"Authorization": "Bearer not-a-valid-token"},
    )
    assert wrong_both.status_code == 401


def test_service_route_requires_shared_key_even_with_valid_tax_jwt(monkeypatch):
    _configure(monkeypatch)
    client = TestClient(_app())

    jwt_only = client.get(
        "/api/v1/facts/service",
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert jwt_only.status_code == 401

    wrong_key = client.get(
        "/api/v1/facts/service",
        headers={"Authorization": "Bearer wrong-service-key"},
    )
    assert wrong_key.status_code == 401

    valid_key = client.get(
        "/api/v1/facts/service",
        headers={"Authorization": f"Bearer {SHARED_KEY}"},
    )
    assert valid_key.status_code == 200


def test_service_key_header_is_supported_without_becoming_user_auth(monkeypatch):
    _configure(monkeypatch)
    client = TestClient(_app())

    service = client.get("/api/v1/facts/service", headers={"X-API-Key": SHARED_KEY})
    assert service.status_code == 200

    user = client.get("/api/v1/executive/user", headers={"X-API-Key": SHARED_KEY})
    assert user.status_code == 401


def test_valid_tax_jwt_does_not_bypass_legacy_route_without_user_dependency(monkeypatch):
    _configure(monkeypatch)
    client = TestClient(_app())

    response = client.get(
        "/api/v1/retrieve",
        headers={"Authorization": f"Bearer {_token()}"},
    )

    assert response.status_code == 401
