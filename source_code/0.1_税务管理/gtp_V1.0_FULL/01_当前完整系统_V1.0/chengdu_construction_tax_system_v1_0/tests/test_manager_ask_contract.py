"""Contract tests for the manager AI question endpoint.

These tests use a tiny isolated FastAPI app and fake session so they do not
need a PostgreSQL test database or write any project data.  The production
route remains protected by ``admin_only``; the isolated app replaces that
dependency only to exercise form binding and routing behavior.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _Session:
    def close(self) -> None:
        pass


def _isolated_manager_app(monkeypatch, *, ask_handler):
    from app.routers import manager

    monkeypatch.setattr(
        manager,
        "admin_only",
        lambda _request: SimpleNamespace(username="test-admin"),
    )
    monkeypatch.setattr(manager, "SessionLocal", lambda: _Session())
    monkeypatch.setattr(manager, "_build_project_context", lambda _db, _pid: {})
    monkeypatch.setattr(manager, "audit_from_request", lambda *args, **kwargs: None)
    monkeypatch.setattr(manager, "_call_ai", ask_handler)

    app = FastAPI()
    app.include_router(manager.router)
    return app


def test_manager_ask_omits_endpoint_id_for_server_side_pool_selection(monkeypatch):
    calls: list[int | None] = []

    def ask_handler(_question, _ctx, endpoint_id=None):
        calls.append(endpoint_id)
        return {
            "answer": "来自真实模型池",
            "status": "READY",
            "requires_manual_review": False,
            "data_gaps": [],
        }

    app = _isolated_manager_app(monkeypatch, ask_handler=ask_handler)
    response = TestClient(app).post(
        "/manager/project/1/ask",
        data={"question": "项目利润是多少？"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "READY"
    assert calls == [None]


def test_manager_ask_preserves_explicit_endpoint_id(monkeypatch):
    calls: list[int | None] = []

    def ask_handler(_question, _ctx, endpoint_id=None):
        calls.append(endpoint_id)
        return {
            "answer": "指定端点回答",
            "status": "DEGRADED",
            "requires_manual_review": True,
            "data_gaps": ["fallback"],
        }

    app = _isolated_manager_app(monkeypatch, ask_handler=ask_handler)
    response = TestClient(app).post(
        "/manager/project/1/ask",
        data={"question": "项目利润是多少？", "endpoint_id": "45"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "DEGRADED"
    assert calls == [45]


def test_call_ai_without_endpoint_reports_unavailable_instead_of_validation_error(
    monkeypatch,
):
    from app.ai.adapter import AIEndpointUnavailable
    from app.routers import manager

    monkeypatch.setattr(manager, "SessionLocal", lambda: _Session())

    def no_endpoint(*args, **kwargs):
        raise AIEndpointUnavailable("未配置可用的真实 AI 端点")

    monkeypatch.setattr(manager, "call_with_failover", no_endpoint)
    payload = manager._call_ai("请检查", {}, None)

    assert payload["status"] == "UNAVAILABLE"
    assert payload["requires_manual_review"] is True
    assert any("真实 AI 端点不可用" in item for item in payload["data_gaps"])


def test_call_ai_without_endpoint_delegates_to_default_model_pool(monkeypatch):
    from app.routers import manager

    captured: list[int | None] = []
    monkeypatch.setattr(manager, "SessionLocal", lambda: _Session())

    def pool_call(_db, _messages, _ctx, *, endpoint_id=None):
        captured.append(endpoint_id)
        return (
            {"summary": "模型池回答", "risk_level": "LOW", "data_gaps": []},
            "模型池回答",
            False,
            {
                "endpoint_id": 45,
                "endpoint_name": "本地模型",
                "model": "local-qwen3.5-2b",
                "fallback_used": False,
            },
        )

    monkeypatch.setattr(manager, "call_with_failover", pool_call)
    payload = manager._call_ai("请检查", {}, None)

    assert captured == [None]
    assert payload["status"] == "READY"
    assert payload["endpoint_id"] == 45


@pytest.mark.parametrize("blank_value", ["", "   "])
def test_manager_ask_blank_endpoint_id_is_treated_as_auto_selection(
    monkeypatch, blank_value
):
    calls: list[int | None] = []

    def ask_handler(_question, _ctx, endpoint_id=None):
        calls.append(endpoint_id)
        return {"answer": "自动选择", "status": "UNAVAILABLE"}

    app = _isolated_manager_app(monkeypatch, ask_handler=ask_handler)
    response = TestClient(app).post(
        "/manager/project/1/ask",
        data={"question": "检查", "endpoint_id": blank_value},
    )

    assert response.status_code == 200
    assert calls == [None]


def test_manager_ask_invalid_endpoint_id_is_unavailable_not_422(monkeypatch):
    def forbidden_handler(*_args, **_kwargs):
        raise AssertionError("invalid endpoint must not reach the model call")

    app = _isolated_manager_app(monkeypatch, ask_handler=forbidden_handler)
    response = TestClient(app).post(
        "/manager/project/1/ask",
        data={"question": "检查", "endpoint_id": "not-an-id"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "UNAVAILABLE"
    assert payload["requires_manual_review"] is True


def test_call_ai_uses_text_failover_for_natural_language_answer(monkeypatch):
    from app.ai.failover import TextFailoverResult
    from app.routers import manager

    monkeypatch.setattr(manager, "SessionLocal", lambda: _Session())
    captured: list[int | None] = []

    def text_pool(_db, _messages, _ctx, *, endpoint_id=None):
        captured.append(endpoint_id)
        return TextFailoverResult(
            "项目毛利率为 12%，请结合合同资料复核。",
            {
                "endpoint_id": 8930,
                "endpoint_name": "本地 Qwen",
                "model": "local-qwen3.5-2b",
                "status": "READY",
                "fallback_used": False,
                "attempts": [],
            },
        )

    monkeypatch.setattr(manager, "call_text_with_failover", text_pool)
    payload = manager._call_ai("项目毛利率是多少？", {}, None)

    assert captured == [None]
    assert payload["answer"] == "项目毛利率为 12%，请结合合同资料复核。"
    assert payload["status"] == "READY"
    assert payload["requires_manual_review"] is False
    assert "AI解释仅供参考，不覆盖确定性计算结果" in payload["data_gaps"]
    assert payload["endpoint_id"] == 8930


def test_call_ai_surfaces_text_failover_metadata_as_degraded(monkeypatch):
    from app.ai.failover import TextFailoverResult
    from app.routers import manager

    monkeypatch.setattr(manager, "SessionLocal", lambda: _Session())
    monkeypatch.setattr(
        manager,
        "call_text_with_failover",
        lambda *_args, **_kwargs: TextFailoverResult(
            "后备端点回答",
            {
                "endpoint_id": 8940,
                "endpoint_name": "后备模型",
                "model": "fallback-model",
                "status": "DEGRADED",
                "fallback_used": True,
                "attempts": [],
            },
        ),
    )

    payload = manager._call_ai("项目税负如何？", {}, None)

    assert payload["answer"] == "后备端点回答"
    assert payload["status"] == "DEGRADED"
    assert payload["requires_manual_review"] is True
    assert payload["fallback_used"] is True
    assert payload["endpoint_id"] == 8940
