"""Unit contracts for the shared real-model routing pool."""
from __future__ import annotations

import httpx
import pytest


def _endpoint(
    name: str,
    *,
    endpoint_id: int,
    priority: int,
    group: str = "default",
    adapter: str = "openai_compatible",
):
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
        timeout_seconds=1,
    )


def test_pool_uses_explicit_endpoint_then_same_group_priority_order():
    from app.ai.failover import select_failover_endpoints

    explicit = _endpoint("explicit", endpoint_id=3, priority=90)
    earlier = _endpoint("earlier", endpoint_id=1, priority=1)
    later = _endpoint("later", endpoint_id=2, priority=100)
    other_group = _endpoint("other", endpoint_id=4, priority=0, group="other")
    mock = _endpoint("mock", endpoint_id=5, priority=0, adapter="mock")

    selected = select_failover_endpoints(
        None,
        endpoint_id=explicit.id,
        endpoints=[explicit, earlier, later, other_group, mock],
    )

    assert [row.name for row in selected] == ["explicit", "earlier", "later"]


def test_failover_marks_fallback_and_returns_actual_endpoint(monkeypatch):
    from app.ai import failover

    first = _endpoint("first", endpoint_id=1, priority=1)
    second = _endpoint("second", endpoint_id=2, priority=2)
    calls: list[str] = []

    def fake_call(endpoint, messages, ctx, *, deadline=None):
        calls.append(endpoint.name)
        if endpoint is first:
            request = httpx.Request("POST", endpoint.base_url)
            response = httpx.Response(503, request=request)
            raise httpx.HTTPStatusError("provider failure", request=request, response=response)
        return ({
            "risk_level": "LOW",
            "score": 10,
            "summary": "ok",
            "findings": [],
            "recommendations": [],
            "data_gaps": [],
        }, '{"risk_level":"LOW"}', False)

    monkeypatch.setattr(failover, "call_endpoint", fake_call)
    result, raw, parse_failed, metadata = failover.call_with_failover(
        None,
        [],
        {},
        endpoint_id=first.id,
        endpoints=[first, second],
    )

    assert calls == ["first", "second"]
    assert result["risk_level"] == "LOW"
    assert raw.startswith("{")
    assert parse_failed is False
    assert metadata["endpoint_id"] == second.id
    assert metadata["endpoint_name"] == "second"
    assert metadata["model"] == "model-second"
    assert metadata["status"] == "DEGRADED"
    assert metadata["fallback_used"] is True
    assert metadata["attempts"][0]["status"] == 503
    assert set(metadata["attempts"][0]) == {
        "endpoint_id", "error_class", "status", "latency_ms",
    }


def test_response_contract_failure_switches_to_next_endpoint(monkeypatch):
    from app.ai import failover
    from app.ai.adapter import AIResponseContractError

    first = _endpoint("bad-contract", endpoint_id=11, priority=1)
    second = _endpoint("good-contract", endpoint_id=12, priority=2)

    def fake_call(endpoint, messages, ctx, *, deadline=None):
        if endpoint is first:
            raise AIResponseContractError("provider response must not be persisted")
        return ({"risk_level": "LOW", "score": 1}, "{}", False)

    monkeypatch.setattr(failover, "call_endpoint", fake_call)
    result = failover.call_with_failover(
        None, [], {}, endpoint_id=first.id, endpoints=[first, second],
    )

    assert result.metadata["fallback_used"] is True
    assert result.metadata["attempts"][0]["error_class"] == "response_contract"
    assert "provider response" not in str(result.metadata)


def test_all_failures_are_unavailable_and_do_not_leak_secret_or_response(monkeypatch):
    from app.ai import failover
    from app.ai.adapter import AIEndpointUnavailable

    first = _endpoint("first", endpoint_id=21, priority=1)
    second = _endpoint("second", endpoint_id=22, priority=2)

    def fake_call(endpoint, messages, ctx, *, deadline=None):
        raise RuntimeError("Authorization: Bearer sk-secret provider-body")

    monkeypatch.setattr(failover, "call_endpoint", fake_call)
    with pytest.raises(AIEndpointUnavailable) as caught:
        failover.call_with_failover(
            None, [], {}, endpoint_id=first.id, endpoints=[first, second],
        )

    error = caught.value
    assert error.status == "UNAVAILABLE"
    assert "sk-secret" not in str(error)
    assert "provider-body" not in str(error)
    assert all("sk-secret" not in str(item) for item in error.attempts)
    assert [item["endpoint_id"] for item in error.attempts] == [21, 22]


def test_credential_ref_is_resolved_dynamically_without_generic_key_fallback(monkeypatch):
    from app.ai import adapter

    endpoint = _endpoint("credential-ref", endpoint_id=31, priority=1)
    endpoint.credential_ref = "123e4567-e89b-42d3-a456-426614174000"

    class Store:
        def resolve(self, ref):
            assert ref == endpoint.credential_ref
            return "endpoint-specific-secret"

    monkeypatch.setattr(adapter, "default_secret_store", lambda: Store())
    assert adapter._resolve_endpoint_key(endpoint) == "endpoint-specific-secret"

    # A row with no own credential must not silently borrow OPENAI_API_KEY.
    endpoint.credential_ref = ""
    endpoint.api_key_env = ""
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-sent")

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '{"risk_level":"LOW"}'}}]}

    captured = {}

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, *args, **kwargs):
            captured.update(kwargs)
            return Response()

    monkeypatch.setattr(adapter.httpx, "Client", Client)
    monkeypatch.setenv("AI_ALLOWED_HOSTS", "provider.example.test")
    adapter.call_endpoint(endpoint, [], {})
    assert "Authorization" not in captured["headers"]


def test_text_failover_accepts_plain_text_and_reports_fallback_metadata(monkeypatch):
    from app.ai import failover

    first = _endpoint("first-text", endpoint_id=41, priority=1)
    second = _endpoint("second-text", endpoint_id=42, priority=2)
    calls: list[str] = []

    def fake_text_call(endpoint, messages, ctx, *, deadline=None):
        calls.append(endpoint.name)
        if endpoint is first:
            return "   "
        return "自然语言项目问答"

    monkeypatch.setattr(failover, "call_text_endpoint", fake_text_call)
    result = failover.call_text_with_failover(
        None,
        [{"role": "user", "content": "项目利润？"}],
        {},
        endpoint_id=first.id,
        endpoints=[first, second],
    )

    assert calls == ["first-text", "second-text"]
    assert result.text == "自然语言项目问答"
    assert result.metadata["endpoint_id"] == second.id
    assert result.metadata["status"] == "DEGRADED"
    assert result.metadata["fallback_used"] is True
    assert result.metadata["attempts"][0]["error_class"] == "response_contract"


def test_text_failover_empty_text_from_all_real_endpoints_is_unavailable(monkeypatch):
    from app.ai import failover
    from app.ai.adapter import AIEndpointUnavailable, AIResponseContractError

    first = _endpoint("empty-first", endpoint_id=51, priority=1)
    second = _endpoint("empty-second", endpoint_id=52, priority=2)

    def fake_empty_call(endpoint, messages, ctx, *, deadline=None):
        raise AIResponseContractError("模型端点未返回非空 assistant 文本")

    monkeypatch.setattr(failover, "call_text_endpoint", fake_empty_call)
    with pytest.raises(AIEndpointUnavailable) as caught:
        failover.call_text_with_failover(
            None,
            [],
            {},
            endpoint_id=first.id,
            endpoints=[first, second],
        )

    error = caught.value
    assert error.status == "UNAVAILABLE"
    assert [item["endpoint_id"] for item in error.attempts] == [51, 52]
    assert all(item["error_class"] == "response_contract" for item in error.attempts)
