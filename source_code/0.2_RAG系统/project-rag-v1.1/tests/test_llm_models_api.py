"""Focused checks for the admin RAG model settings boundary."""
from __future__ import annotations

from types import SimpleNamespace

import jwt
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from app import security
from app.routers import llm_models
from app.schemas import LLMModelEndpointCreate, LLMModelEndpointMove, LLMModelEndpointPatch
from app.validation_errors import request_validation_exception_handler


class _ScalarResult:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)


class _Db:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.added = []

    def execute(self, _statement):
        return _ScalarResult(sorted(self.rows, key=lambda item: (item.routing_group, item.priority, item.id)))

    def get(self, _model, model_id):
        return next((item for item in self.rows + self.added if item.id == model_id), None)

    def add(self, item):
        item.id = len(self.rows) + len(self.added) + 1
        self.added.append(item)

    def delete(self, item):
        self.rows.remove(item)

    def commit(self):
        self.rows.extend(self.added)
        self.added = []

    def rollback(self):
        return None

    def refresh(self, _item):
        return None


def _endpoint(name, model_id, priority, *, key="secret"):
    return SimpleNamespace(
        id=model_id,
        name=name,
        base_url=f"http://127.0.0.1:{9000 + model_id}/v1",
        chat_path="/v1/chat/completions",
        model=name,
        api_key=key,
        enabled=True,
        timeout_seconds=30,
        priority=priority,
        routing_group="default",
        note="",
        last_status="UNKNOWN",
        last_error_class="",
        last_latency_ms=0,
        last_checked_at=None,
    )


def test_safe_view_never_contains_api_key():
    view = llm_models._view(_endpoint("primary", 1, 0))
    assert "api_key" not in view
    assert view["has_api_key"] is True


def test_create_rejects_unsafe_url(monkeypatch):
    monkeypatch.setattr(llm_models, "validate_llm_outbound_url", lambda _url: (_ for _ in ()).throw(ValueError("unsafe")))
    payload = LLMModelEndpointCreate(name="bad", base_url="http://169.254.169.254", model="x")
    with pytest.raises(Exception) as caught:
        llm_models.create_llm_model(payload, principal=object(), db=_Db())
    assert getattr(caught.value, "status_code", None) == 422


def test_patch_blank_api_key_preserves_existing_secret(monkeypatch):
    monkeypatch.setattr(llm_models, "validate_llm_outbound_url", lambda _url: _url)
    endpoint = _endpoint("primary", 1, 0)
    db = _Db([endpoint])
    updated = llm_models.update_llm_model(
        1,
        LLMModelEndpointPatch(note="changed", api_key=""),
        principal=object(),
        db=db,
    )
    assert endpoint.api_key == "secret"
    assert updated["note"] == "changed"
    assert "api_key" not in updated


def test_move_reorders_rows_with_transactional_priority_adjustment():
    first = _endpoint("first", 1, 0)
    second = _endpoint("second", 2, 10)
    db = _Db([first, second])
    result = llm_models.move_llm_model(
        2,
        LLMModelEndpointMove(direction="up"),
        principal=object(),
        db=db,
    )
    assert [item["id"] for item in result["items"]] == [2, 1]
    assert second.priority < first.priority


def test_model_api_anonymous_denied_admin_cookie_allowed(monkeypatch):
    """The middleware reaches the route, where admin auth still gates it."""
    secret = "llm-model-api-test-secret-32-bytes"
    monkeypatch.setenv("JWT_SECRET_KEY", secret)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setattr(security, "AUTH_REQUIRED", True)
    monkeypatch.setattr(security, "RAG_SHARED_API_KEY", "internal-test-key")
    db = _Db([_endpoint("primary", 1, 0)])
    test_app = FastAPI()
    test_app.add_middleware(security.RAGSecurityMiddleware)
    test_app.include_router(llm_models.router)
    test_app.dependency_overrides[llm_models.get_db] = lambda: db
    client = TestClient(test_app)

    anonymous = client.get("/api/v1/llm-models")
    assert anonymous.status_code == 401

    token = jwt.encode(
        {"sub": "7", "username": "admin", "role": "admin", "type": "access"},
        secret,
        algorithm="HS256",
    )
    authenticated = client.get("/api/v1/llm-models", cookies={"cdjg_rag_token": token})
    assert authenticated.status_code == 200
    assert authenticated.json()["items"][0]["name"] == "primary"


def test_validation_error_never_echoes_submitted_api_key():
    test_app = FastAPI()
    test_app.add_exception_handler(RequestValidationError, request_validation_exception_handler)

    @test_app.post("/model")
    def accept_model(payload: LLMModelEndpointCreate):
        return payload

    submitted_key = "PRIVATE_MODEL_KEY_FOR_ERROR_TEST"
    response = TestClient(test_app).post(
        "/model",
        json={
            "name": "primary",
            "base_url": "http://127.0.0.1:8930/v1",
            "model": "local-model",
            "api_key": submitted_key + "\n",
        },
    )

    assert response.status_code == 422
    assert submitted_key not in response.text
    assert "input" not in response.json()["detail"][0]
    assert response.json()["detail"][0]["msg"] == "api_key 参数无效"
