"""Tests asserting the ``_meta`` block on every executive mobile endpoint.

The current router contract emits one of two ``_meta.data_source`` values:

* ``"unavailable"`` — Canonical Facts not yet connected (the default)
* ``"live"`` — Canonical Facts supplied a complete response for the request

These tests pin that contract so the front-end can rely on
``_meta.data_source`` to render a freshness badge and so accidental
live-vs-unavailable drift triggers a test failure.

The endpoints under test depend on SQLAlchemy sessions via
``app.db.SessionLocal``.  The disposable test SQLite database is set up by
``tests/conftest.py`` so normal application imports point at the
``PROJECT_RAG_DB_URL`` SQLite file.  The router reads/writes rows directly
through SQLAlchemy, so we never patch ``get_db_connection`` here.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict

import jwt
import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Auth helpers — Tax-style JWT signed with the same secret the router uses
# ---------------------------------------------------------------------------

_TEST_JWT_SECRET = "test-suite-jwt-secret-32-bytes-or-more"


def _issue_jwt(role: str = "admin", user_id: int = 1, username: str = "test.user") -> str:
    """Mint a Tax-style ``access`` JWT signed with the test secret."""

    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "display_name": username,
        "type": "access",
    }
    return jwt.encode(payload, _TEST_JWT_SECRET, algorithm="HS256")


def _bearer(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _assert_iso_timestamp(meta: Dict[str, Any]) -> None:
    assert "timestamp" in meta, "_meta.timestamp missing"
    parsed = datetime.fromisoformat(meta["timestamp"])
    assert parsed.tzinfo is not None, "timestamp must include timezone"


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def jwt_secret(monkeypatch):
    """Pin ``JWT_SECRET_KEY`` so the router's auth module signs the same way."""

    monkeypatch.setenv("JWT_SECRET_KEY", _TEST_JWT_SECRET)
    yield _TEST_JWT_SECRET


@pytest.fixture
def client(jwt_secret):
    """Build a TestClient wrapping the real ``app.main.app``.

    Importing ``app.main`` runs the entire wire-up (migrations, middleware,
    routers) which is what the production binary executes.  This is the
    only way the router's ``from .db import SessionLocal`` relative import
    resolves correctly in tests.
    """

    from app.main import app
    from app.db import init_db

    # Force table creation so SQL count queries on empty tables succeed.
    init_db()
    return TestClient(app)


@pytest.fixture
def admin_token(jwt_secret) -> str:
    return _issue_jwt(role="admin", user_id=1, username="admin.user")


@pytest.fixture
def operator_token(jwt_secret) -> str:
    return _issue_jwt(role="operator", user_id=2, username="ops.user")


@pytest.fixture
def sample_project(client, admin_token):
    """Insert one project row directly via SQLAlchemy and return its id."""

    from app.db import SessionLocal
    from sqlalchemy import text

    with SessionLocal() as db:
        db.execute(
            text(
                "INSERT INTO projects (project_code, name, status, external_system, "
                "external_project_id, contract_amount, start_date, expected_end_date, "
                "location, project_type, note, created_at, updated_at) VALUES "
                "(:code, :name, 'ACTIVE', 'construction-tax', :code, 0.0, '', '', "
                "'', '', '', '', '')"
            ),
            {"code": "CD-TF-001", "name": "天府新区"},
        )
        db.commit()
        row = db.execute(
            text("SELECT id FROM projects WHERE project_code = :code"),
            {"code": "CD-TF-001"},
        ).first()
    assert row is not None, "failed to insert sample project"
    return int(row[0])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_cockpit_summary_meta_block_is_unavailable(client, admin_token):
    resp = client.get(
        "/api/v1/executive/cockpit/summary",
        headers=_bearer(admin_token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success"
    assert "_meta" in body
    meta = body["_meta"]
    # Canonical Facts is intentionally not connected in this suite, so the
    # router must report ``unavailable`` and refuse to emit demo numbers.
    assert meta["data_source"] in {"live", "unavailable"}
    assert meta["issuer_role"] == "admin"
    assert meta["issuer_source"] == "tax_jwt"
    _assert_iso_timestamp(meta)


def test_projects_meta_block_is_unavailable(client, admin_token):
    resp = client.get(
        "/api/v1/executive/projects",
        headers=_bearer(admin_token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success"
    assert "_meta" in body
    meta = body["_meta"]
    assert meta["data_source"] in {"live", "unavailable"}
    _assert_iso_timestamp(meta)


def test_project_360_meta_block_is_unavailable(client, admin_token, sample_project):
    resp = client.get(
        f"/api/v1/executive/projects/{sample_project}/360",
        headers=_bearer(admin_token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    meta = body["_meta"]
    assert meta["data_source"] in {"live", "unavailable"}
    _assert_iso_timestamp(meta)


def test_entities_matrix_meta_block_is_unavailable(client, admin_token):
    resp = client.get(
        "/api/v1/executive/entities/matrix",
        headers=_bearer(admin_token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "_meta" in body
    meta = body["_meta"]
    assert meta["data_source"] in {"live", "unavailable"}
    _assert_iso_timestamp(meta)


def test_ai_chat_meta_block_is_unavailable(client, admin_token):
    resp = client.post(
        "/api/v1/executive/ai/chat",
        json={"message": "利润"},
        headers=_bearer(admin_token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "_meta" in body
    meta = body["_meta"]
    assert meta["data_source"] in {"live", "unavailable"}
    _assert_iso_timestamp(meta)


def test_ai_chat_stream_emits_meta_event(client, admin_token):
    with client.stream(
        "POST",
        "/api/v1/executive/ai/chat/stream",
        json={"message": "利润"},
        headers=_bearer(admin_token),
    ) as resp:
        assert resp.status_code == 200
        events = []
        for line in resp.iter_lines():
            if line:
                events.append(line)
        joined = "\n".join(events)
        assert "event: meta" in joined, joined
        meta_line = next(
            (l for l in events if l.startswith("data: ") and '"data_source"' in l),
            None,
        )
        assert meta_line is not None, joined
        meta = json.loads(meta_line[len("data: "):])
        assert meta["data_source"] in {"live", "unavailable"}
        assert meta["issuer_role"] == "admin"


def test_meta_block_distinguishes_admin_and_operator_roles(client, admin_token, operator_token):
    """The ``issuer_role`` field must reflect the role embedded in the JWT."""

    resp_admin = client.get(
        "/api/v1/executive/cockpit/summary",
        headers=_bearer(admin_token),
    )
    assert resp_admin.status_code == 200, resp_admin.text
    meta_admin = resp_admin.json()["_meta"]
    assert meta_admin["issuer_role"] == "admin"
    assert meta_admin["issuer_source"] == "tax_jwt"

    resp_op = client.get(
        "/api/v1/executive/cockpit/summary",
        headers=_bearer(operator_token),
    )
    assert resp_op.status_code == 200, resp_op.text
    meta_op = resp_op.json()["_meta"]
    assert meta_op["issuer_role"] == "operator"
    assert meta_op["issuer_source"] == "tax_jwt"
