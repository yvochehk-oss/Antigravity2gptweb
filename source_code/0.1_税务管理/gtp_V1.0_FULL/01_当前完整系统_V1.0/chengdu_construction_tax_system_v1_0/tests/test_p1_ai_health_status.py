"""Regression coverage for aggregate AI health semantics."""
from __future__ import annotations

from types import SimpleNamespace


def test_partial_ai_endpoint_failure_reports_degraded(monkeypatch):
    """One healthy endpoint must not hide another enabled endpoint failure."""
    import app.ai.adapter as adapter
    import app.db as db_module
    import app.models as models
    import app.startup as startup

    class EnabledField:
        @staticmethod
        def is_(_value):
            return object()

    class FakeAIModelEndpoint:
        enabled = EnabledField()

    healthy = SimpleNamespace(
        name="healthy-endpoint",
        base_url="http://127.0.0.1:19001",
        chat_path="/v1/chat/completions",
        model="health-model",
    )
    broken = SimpleNamespace(
        name="broken-endpoint",
        base_url="",
        chat_path="/v1/chat/completions",
        model="health-model",
    )

    class FakeQuery:
        def filter(self, *_args):
            return self

        @staticmethod
        def all():
            return [healthy, broken]

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb):
            return False

        @staticmethod
        def query(_model):
            return FakeQuery()

    class FakeResponse:
        status_code = 200

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb):
            return False

        @staticmethod
        def post(_url, *, headers, json):
            assert headers["Content-Type"] == "application/json"
            assert json["max_tokens"] == 1
            return FakeResponse()

    monkeypatch.setattr(models, "AIModelEndpoint", FakeAIModelEndpoint)
    monkeypatch.setattr(db_module, "SessionLocal", FakeSession)
    monkeypatch.setattr(adapter, "endpoint_is_allowed", lambda _row: True)
    monkeypatch.setattr(adapter, "_resolve_endpoint_key", lambda _row: "")
    monkeypatch.setattr(
        startup,
        "validate_ai_endpoint_url",
        lambda base_url, _chat_path: base_url.rstrip("/"),
    )
    monkeypatch.setattr(startup.httpx, "Client", FakeClient)

    result = startup._check_ai()

    assert result["status"] == "degraded"
    assert [endpoint["status"] for endpoint in result["endpoints"]] == [
        "ok",
        "down",
    ]
