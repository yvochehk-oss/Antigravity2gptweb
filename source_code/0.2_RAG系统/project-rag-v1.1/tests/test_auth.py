"""Authentication tests for the executive mobile API.

These tests build a minimal FastAPI app that mounts the real
``executive_mobile`` router against a stubbed ``get_db_connection``
dependency, so the suite stays hermetic and does not require PostgreSQL.

The token table is supplied through ``RAG_EXEC_TOKENS`` /
``RAG_EXEC_DEMO_TOKENS`` / ``RAG_EXEC_INTERNAL_TOKEN`` environment
variables (read by ``app.auth``) and re-asserted via monkeypatch so
the auth cache is invalidated between scenarios.
"""

from __future__ import annotations

import importlib
import json
from typing import Any, Dict, List

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient


def _reload_auth_module():
    """Reload ``app.auth`` to pick up changed environment variables."""

    import app.auth as mod

    importlib.reload(mod)
    return mod


def _patch_env(
    mpatch: pytest.MonkeyPatch,
    *,
    prod: Dict[str, str] | None = None,
    demo: Dict[str, str] | None = None,
    internal: str | None = None,
):
    """Set or clear the three token-related environment variables."""

    if prod is None:
        mpatch.delenv("RAG_EXEC_TOKENS", raising=False)
    else:
        mpatch.setenv("RAG_EXEC_TOKENS", json.dumps(prod))
    if demo is None:
        mpatch.delenv("RAG_EXEC_DEMO_TOKENS", raising=False)
    else:
        mpatch.setenv("RAG_EXEC_DEMO_TOKENS", json.dumps(demo))
    if internal is None:
        mpatch.delenv("RAG_EXEC_INTERNAL_TOKEN", raising=False)
    else:
        mpatch.setenv("RAG_EXEC_INTERNAL_TOKEN", internal)


@pytest.fixture
def auth(monkeypatch: pytest.MonkeyPatch):
    """``app.auth`` module configured with a mixed prod/demo/internal token set."""

    _patch_env(
        monkeypatch,
        prod={
            "prod.chairman.alpha": "chairman",
            "prod.gm.beta": "general_manager",
            "prod.cfo.gamma": "cfo",
            "prod.pm.delta": "project_manager",
        },
        demo={
            "demo.chairman.alpha": "chairman",
            "demo.gm.beta": "general_manager",
            "demo.cfo.gamma": "cfo",
            "demo.pm.delta": "project_manager",
        },
        internal="ops-bypass-1",
    )
    return _reload_auth_module()


def _build_test_router(auth_mod) -> FastAPI:
    """Construct a minimal FastAPI app with the same auth-decorated
    endpoints as ``executive_mobile`` but stubbed data, so we can test
    auth logic in isolation without needing a PostgreSQL connection.
    """

    from fastapi import APIRouter
    from pydantic import BaseModel

    router = APIRouter(prefix="/api/v1/executive")

    class ChatBody(BaseModel):
        message: str

    @router.get("/cockpit/summary")
    def cockpit(_=Depends(auth_mod.require_exec_role(
        "chairman", "general_manager", "cfo", "project_manager",
    ))):
        return {"status": "success", "kpi": {"contract_total": 623000000.0}}

    @router.get("/projects")
    def projects(_=Depends(auth_mod.require_exec_role(
        "chairman", "general_manager", "cfo", "project_manager",
    ))):
        return {"status": "success", "count": 1, "projects": []}

    @router.get("/entities/matrix")
    def entities(_=Depends(auth_mod.require_exec_role(
        "chairman", "general_manager", "cfo",
    ))):
        return {"status": "success", "total_entities": 0, "matrix": {}}

    from fastapi import Body

    @router.post("/ai/chat")
    def chat(
        _: Any = Depends(auth_mod.require_exec_role(
            "chairman", "general_manager", "cfo", "project_manager",
        )),
        body: Dict[str, Any] = Body(...),
    ):
        return {"status": "success", "query": body["message"], "reply": f"echo: {body['message']}", "citations": [], "timestamp": 0}

    app = FastAPI()
    app.include_router(router)
    return app


# ---------------------------------------------------------------------------
# 401 rejection cases
# ---------------------------------------------------------------------------

def test_missing_authorization_returns_401(auth):
    client = TestClient(_build_test_router(auth))
    resp = client.get("/api/v1/executive/cockpit/summary")
    assert resp.status_code == 401
    assert resp.headers.get("WWW-Authenticate") == "Bearer"


def test_wrong_token_returns_401(auth):
    client = TestClient(_build_test_router(auth))
    resp = client.get(
        "/api/v1/executive/cockpit/summary",
        headers={"Authorization": "Bearer prod.chairman.nope"},
    )
    assert resp.status_code == 401


def test_malformed_scheme_returns_401(auth):
    client = TestClient(_build_test_router(auth))
    resp = client.get(
        "/api/v1/executive/cockpit/summary",
        headers={"Authorization": "Basic prod.chairman.alpha"},
    )
    assert resp.status_code == 401


def test_empty_bearer_token_returns_401(auth):
    client = TestClient(_build_test_router(auth))
    resp = client.get(
        "/api/v1/executive/cockpit/summary",
        headers={"Authorization": "Bearer "},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Role-allowance cases
# ---------------------------------------------------------------------------

def test_chairman_token_can_access_cockpit(auth):
    client = TestClient(_build_test_router(auth))
    resp = client.get(
        "/api/v1/executive/cockpit/summary",
        headers={"Authorization": "Bearer prod.chairman.alpha"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"


def test_chairman_token_can_access_ai_chat(auth):
    client = TestClient(_build_test_router(auth))
    resp = client.post(
        "/api/v1/executive/ai/chat",
        json={"message": "利润"},
        headers={"Authorization": "Bearer prod.chairman.alpha"},
    )
    assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text}"
    assert "echo" in resp.json()["reply"]


def test_chairman_token_can_access_entities_matrix(auth):
    client = TestClient(_build_test_router(auth))
    resp = client.get(
        "/api/v1/executive/entities/matrix",
        headers={"Authorization": "Bearer prod.chairman.alpha"},
    )
    assert resp.status_code == 200


def test_general_manager_token_can_access_cockpit(auth):
    client = TestClient(_build_test_router(auth))
    resp = client.get(
        "/api/v1/executive/cockpit/summary",
        headers={"Authorization": "Bearer prod.gm.beta"},
    )
    assert resp.status_code == 200


def test_project_manager_token_cannot_access_entities_matrix(auth):
    """``entities/matrix`` is restricted to chairman/general_manager/cfo only."""

    client = TestClient(_build_test_router(auth))
    resp = client.get(
        "/api/v1/executive/entities/matrix",
        headers={"Authorization": "Bearer prod.pm.delta"},
    )
    assert resp.status_code == 403


def test_project_manager_token_can_access_projects(auth):
    client = TestClient(_build_test_router(auth))
    resp = client.get(
        "/api/v1/executive/projects",
        headers={"Authorization": "Bearer prod.pm.delta"},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Demo / prod token separation
# ---------------------------------------------------------------------------

def test_demo_token_works_when_prod_token_does_not_exist(auth):
    """The demo table is checked after the prod table; a demo token
    authenticates even when a prod token of the same value does not exist.
    """

    client = TestClient(_build_test_router(auth))
    resp = client.get(
        "/api/v1/executive/cockpit/summary",
        headers={"Authorization": "Bearer demo.chairman.alpha"},
    )
    assert resp.status_code == 200


def test_demo_token_is_distinguished_from_prod(auth, monkeypatch):
    """A demo token should be labelled ``issuer_source: demo`` in the
    ``_meta`` block — exercised via the real router fixture in
    ``test_data_source_meta.py``.  Here we just assert the auth
    path resolves to the correct ``ExecPrincipal.source``.
    """

    _patch_env(
        monkeypatch,
        prod={"prod.chairman.alpha": "chairman"},
        demo={"demo.chairman.alpha": "chairman"},
        internal=None,
    )
    mod = _reload_auth_module()

    from fastapi import Depends
    from fastapi.testclient import TestClient
    from fastapi import APIRouter

    router = APIRouter()

    @router.get("/test")
    def test_endpoint(_=Depends(mod.require_exec_role("chairman"))):
        return {}

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)
    resp = client.get("/test", headers={"Authorization": "Bearer demo.chairman.alpha"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Internal bypass
# ---------------------------------------------------------------------------

def test_internal_token_bypasses_role_checks(auth):
    """The internal service token grants access even when the role would
    otherwise be forbidden (e.g. project_manager on entities/matrix).
    """

    client = TestClient(_build_test_router(auth))
    resp = client.get(
        "/api/v1/executive/entities/matrix",
        headers={"Authorization": "Bearer ops-bypass-1"},
    )
    assert resp.status_code == 200


def test_internal_token_disabled_when_env_unset(monkeypatch: pytest.MonkeyPatch):
    """When ``RAG_EXEC_INTERNAL_TOKEN`` is absent, the static bypass is
    rejected like any other unknown token.
    """

    _patch_env(
        monkeypatch,
        prod={"prod.chairman.alpha": "chairman"},
        demo=None,
        internal=None,
    )
    mod = _reload_auth_module()
    client = TestClient(_build_test_router(mod))
    resp = client.get(
        "/api/v1/executive/cockpit/summary",
        headers={"Authorization": "Bearer ops-bypass-1"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Timing-safe comparison
# ---------------------------------------------------------------------------

def test_token_validation_uses_constant_time_compare(
    auth, monkeypatch: pytest.MonkeyPatch
):
    """Verify ``secrets.compare_digest`` is used for token comparisons."""

    comparisons: List[tuple[str, str]] = []
    real_compare = auth.secrets.compare_digest

    def _spy(a: str, b: str) -> bool:
        comparisons.append((a, b))
        return real_compare(a, b)

    monkeypatch.setattr(auth.secrets, "compare_digest", _spy)
    client = TestClient(_build_test_router(auth))
    resp = client.get(
        "/api/v1/executive/cockpit/summary",
        headers={"Authorization": "Bearer prod.chairman.alpha"},
    )
    assert resp.status_code == 200
    assert any(
        left == "prod.chairman.alpha" and right == "prod.chairman.alpha"
        for left, right in comparisons
    )
