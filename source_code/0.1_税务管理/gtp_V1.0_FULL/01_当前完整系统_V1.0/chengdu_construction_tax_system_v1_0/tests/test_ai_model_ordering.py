"""Isolated contracts for administrator-managed AI endpoint ordering.

These tests deliberately use in-memory objects and a tiny fake session.  They
exercise the route's ordering policy without connecting to, migrating, or
writing the formal projectrag database.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.requests import Request


def _endpoint(name: str, endpoint_id: int, priority: int, *, group: str = "default", adapter: str = "openai_compatible"):
    from app.models import AIModelEndpoint

    return AIModelEndpoint(
        id=endpoint_id,
        name=name,
        adapter=adapter,
        base_url="https://provider.example.test",
        chat_path="/v1/chat/completions",
        model=f"model-{name}",
        enabled=True,
        priority=priority,
        routing_group=group,
        timeout_seconds=10,
    )


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": "/ai-models/2/move",
            "raw_path": b"/ai-models/2/move",
            "query_string": b"",
            "headers": [],
            "server": ("testserver", 80),
            "client": ("testclient", 1234),
            "root_path": "",
            "http_version": "1.1",
        },
    )


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Session:
    def __init__(self, target):
        self.target = target
        self.rollback_called = False
        self.commit_called = False
        self.closed = False

    def execute(self, _statement):
        return _Result(self.target)

    def rollback(self):
        self.rollback_called = True

    def commit(self):
        self.commit_called = True

    def close(self):
        self.closed = True


class _InsertSession:
    """Small isolated session double for the append-only create contract."""

    def __init__(self):
        self.added = []
        self.commit_called = False
        self.rollback_called = False
        self.closed = False

    def add(self, entity):
        self.added.append(entity)

    def flush(self):
        # The route only needs an id for the audit call.  Assign one locally;
        # no SQLAlchemy engine or formal projectrag connection is involved.
        if self.added and self.added[-1].id is None:
            self.added[-1].id = 3

    def rollback(self):
        self.rollback_called = True

    def commit(self):
        self.commit_called = True

    def close(self):
        self.closed = True


@pytest.fixture
def isolated_group():
    """Return an in-memory routing group ordered as the DB query would return it."""
    first = _endpoint("first", 1, 900)
    second = _endpoint("second", 2, 10)
    return [second, first]


def test_group_renumbering_is_deterministic_and_mock_rows_are_excluded():
    from app.routers import models as model_router

    first = _endpoint("first", 1, 900)
    second = _endpoint("second", 2, 10)
    legacy_mock = _endpoint("legacy-mock", 3, 20, adapter="mock")
    rows = model_router._real_endpoint_rows([first, second, legacy_mock])

    assert rows == [first, second]
    model_router._renumber_group(rows)
    assert [(row.id, row.priority) for row in rows] == [(1, 100), (2, 200)]


def test_move_swaps_neighbors_atomically_and_redirects(monkeypatch):
    from app.routers import models as model_router

    first = _endpoint("first", 1, 100)
    second = _endpoint("second", 2, 200)
    third = _endpoint("third", 3, 300)
    session = _Session(second)

    monkeypatch.setattr(model_router, "SessionLocal", lambda: session)
    monkeypatch.setattr(model_router, "admin_only", lambda _request: SimpleNamespace(role="admin"))
    monkeypatch.setattr(model_router, "_locked_group_rows", lambda _db, _group: [first, second, third])
    monkeypatch.setattr(model_router, "audit_from_request", lambda *args, **kwargs: None)
    monkeypatch.setattr(model_router, "_commit_or_fail", lambda db: db.commit())

    response = model_router.ai_model_move(_request(), endpoint_id=2, direction="up")

    assert response.status_code == 303
    assert response.headers["location"] == "/ai-models"
    assert [(row.id, row.priority) for row in [second, first, third]] == [
        (2, 100), (1, 200), (3, 300),
    ]
    assert session.commit_called is True
    assert session.rollback_called is False
    assert session.closed is True


def test_move_boundary_and_mock_are_rejected_with_409_and_rollback(monkeypatch):
    from fastapi import HTTPException

    from app.routers import models as model_router

    first = _endpoint("first", 1, 100)
    session = _Session(first)
    monkeypatch.setattr(model_router, "SessionLocal", lambda: session)
    monkeypatch.setattr(model_router, "admin_only", lambda _request: SimpleNamespace(role="admin"))
    monkeypatch.setattr(model_router, "_locked_group_rows", lambda _db, _group: [first])

    with pytest.raises(HTTPException) as boundary:
        model_router.ai_model_move(_request(), endpoint_id=1, direction="up")
    assert boundary.value.status_code == 409
    assert session.rollback_called is True
    assert session.closed is True

    legacy_mock = _endpoint("legacy-mock", 9, 100, adapter="mock")
    mock_session = _Session(legacy_mock)
    monkeypatch.setattr(model_router, "SessionLocal", lambda: mock_session)
    with pytest.raises(HTTPException) as mock_error:
        model_router.ai_model_move(_request(), endpoint_id=9, direction="down")
    assert mock_error.value.status_code == 409
    assert mock_session.rollback_called is True
    assert mock_session.closed is True


def test_new_model_is_appended_to_group_end_and_ignores_submitted_priority(
    monkeypatch, isolated_group,
):
    """A new endpoint receives the next group priority, never user input."""
    from app.routers import models as model_router

    session = _InsertSession()
    submitted = {
        "name": "third",
        "adapter": "openai_compatible",
        "base_url": "https://provider.example.test",
        "chat_path": "/v1/chat/completions",
        "model": "model-third",
        "api_key_env": "",
        "timeout_seconds": 10,
        "note": "",
        "priority": 0,
        "routing_group": "default",
        "api_key": "",
    }

    monkeypatch.setattr(model_router, "SessionLocal", lambda: session)
    monkeypatch.setattr(model_router, "admin_only", lambda _request: SimpleNamespace(role="admin"))
    monkeypatch.setattr(model_router, "_form_values", lambda **_kwargs: dict(submitted))
    monkeypatch.setattr(
        model_router,
        "_locked_group_rows",
        lambda _db, _group: isolated_group,
    )
    monkeypatch.setattr(model_router, "audit_from_request", lambda *args, **kwargs: None)
    monkeypatch.setattr(model_router, "_commit_or_fail", lambda db: db.commit())

    response = model_router.ai_model_add(_request())

    assert response.status_code == 303
    assert response.headers["location"] == "/ai-models"
    assert len(session.added) == 1
    added = session.added[0]
    # Existing legacy priorities are repaired to 100/200 and the new row is
    # appended at 300, even though the caller submitted priority=0.
    assert [(row.id, row.priority) for row in isolated_group] == [
        (2, 100), (1, 200),
    ]
    assert (added.name, added.routing_group, added.priority) == (
        "third", "default", 300,
    )
    assert session.commit_called is True
    assert session.rollback_called is False
    assert session.closed is True


def test_model_template_exposes_order_controls_but_not_mock_adapter():
    from pathlib import Path

    template = Path(__file__).parents[1] / "app" / "templates" / "ai_models.html"
    source = template.read_text(encoding="utf-8")

    assert 'action="/ai-models/{{ r.id }}/move"' in source
    assert 'name="direction" value="up"' in source
    assert 'name="direction" value="down"' in source
    assert 'option value="mock"' not in source
    assert "新增端点自动追加到组末尾" in source
