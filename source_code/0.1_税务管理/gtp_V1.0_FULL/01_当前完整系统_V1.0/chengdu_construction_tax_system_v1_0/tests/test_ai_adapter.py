"""Focused contract tests for the Tax V1.0 AI adapter."""
from __future__ import annotations

import json

import pytest


def _endpoint(adapter: str = "mock"):
    from app.models import AIModelEndpoint

    return AIModelEndpoint(
        name="adapter-test",
        adapter=adapter,
        base_url="https://ai.example.test",
        chat_path="/v1/chat/completions",
        model="test-model",
        timeout_seconds=1,
    )


def test_public_ai_import_uses_the_current_adapter_module():
    """The public AI package must expose the V1 adapter implementation."""
    from app.ai import call_endpoint as public_call_endpoint
    from app.ai.adapter import call_endpoint

    assert public_call_endpoint is call_endpoint


def test_invalid_json_is_unknown_and_requires_manual_review():
    from app.ai.adapter import _parse_json

    result = _parse_json("model output without a JSON object")

    assert result["risk_level"] == "UNKNOWN"
    assert result["findings"][0]["severity"] == "UNKNOWN"
    assert result["findings"][0]["requires_manual_review"] is True


def test_missing_risk_level_is_unknown_and_marks_data_gap():
    from app.ai.adapter import _normalize_result

    result, contract_failed = _normalize_result({
        "summary": "没有足够证据",
        "findings": [],
    })

    assert contract_failed is True
    assert result["risk_level"] == "UNKNOWN"
    assert result["needs_review"] is True
    assert result["data_gaps"] == ["模型未返回可用风险等级，需人工复核"]


def test_mock_endpoint_requires_test_environment_and_explicit_opt_in(monkeypatch):
    from app.ai.adapter import AIEndpointUnavailable, call_endpoint

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("AI_ALLOW_MOCK_ENDPOINTS", raising=False)
    with pytest.raises(AIEndpointUnavailable):
        call_endpoint(_endpoint(), messages=[], ctx={})

    monkeypatch.setenv("APP_ENV", "test")
    with pytest.raises(AIEndpointUnavailable):
        call_endpoint(_endpoint(), messages=[], ctx={})


def test_mock_result_uses_contextual_deterministic_risk(monkeypatch):
    from app.ai.adapter import call_endpoint

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AI_ALLOW_MOCK_ENDPOINTS", "1")
    result, raw, parse_failed = call_endpoint(
        _endpoint(),
        messages=[],
        ctx={
            "system_calculation": {"margin": -0.2},
            "scope_name": "项目",
        },
    )

    assert result["risk_level"] == "HIGH"
    assert json.loads(raw)["risk_level"] == "HIGH"
    assert parse_failed is False


def test_valid_external_risk_level_is_normalized_without_default(monkeypatch):
    from app.ai import adapter

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{
                    "message": {
                        "content": '{"risk_level":"high","score":42,"summary":"有证据"}',
                    },
                }],
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AI_ALLOWED_HOSTS", "ai.example.test")
    monkeypatch.setattr(adapter.httpx, "Client", FakeClient)

    result, _raw, parse_failed = adapter.call_endpoint(
        _endpoint("openai_compatible"),
        messages=[],
        ctx={"system_calculation": {"margin": 0.1}},
    )

    assert result["risk_level"] == "HIGH"
    assert result["score"] == 42
    assert parse_failed is False


def test_invalid_external_risk_level_is_unknown_and_surfaces_contract_failure(monkeypatch):
    from app.ai import adapter

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{
                    "message": {
                        "content": '{"risk_level":"P0","score":99,"summary":"未知"}',
                    },
                }],
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("AI_ALLOWED_HOSTS", "ai.example.test")
    monkeypatch.setattr(adapter.httpx, "Client", FakeClient)

    result, _raw, parse_failed = adapter.call_endpoint(
        _endpoint("openai_compatible"),
        messages=[],
        ctx={},
    )

    assert result["risk_level"] == "UNKNOWN"
    assert result["needs_review"] is True
    assert "模型未返回可用风险等级，需人工复核" in result["data_gaps"]
    assert parse_failed is True


def test_missing_endpoint_configuration_fails_explicitly():
    from app.ai.adapter import call_endpoint

    endpoint = _endpoint("openai_compatible")
    endpoint.base_url = ""

    try:
        call_endpoint(endpoint, messages=[], ctx={})
    except RuntimeError as exc:
        assert "未配置 base_url" in str(exc)
    else:  # pragma: no cover - assertion documents the explicit failure contract
        raise AssertionError("missing endpoint configuration must not look successful")


def _patch_text_response(monkeypatch, content: str):
    from app.ai import adapter

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": content}}]}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setenv("AI_ALLOWED_HOSTS", "ai.example.test")
    monkeypatch.setattr(adapter.httpx, "Client", FakeClient)


def test_text_endpoint_accepts_plain_assistant_text_without_json_contract(
    monkeypatch,
):
    from app.ai.adapter import call_text_endpoint

    _patch_text_response(monkeypatch, "项目毛利率为 12%，请结合合同资料复核。")

    answer = call_text_endpoint(
        _endpoint("openai_compatible"),
        messages=[{"role": "user", "content": "项目毛利率？"}],
        ctx={},
    )

    assert answer == "项目毛利率为 12%，请结合合同资料复核。"


def test_text_endpoint_rejects_empty_assistant_text(monkeypatch):
    from app.ai.adapter import AIResponseContractError, call_text_endpoint

    _patch_text_response(monkeypatch, " \n\t")

    with pytest.raises(AIResponseContractError, match="非空 assistant 文本"):
        call_text_endpoint(_endpoint("openai_compatible"), messages=[], ctx={})


def test_structured_endpoint_keeps_rejecting_plain_text(monkeypatch):
    from app.ai.adapter import call_endpoint

    _patch_text_response(monkeypatch, "这是一段自然语言，而不是 Review JSON。")

    result, raw, parse_failed = call_endpoint(
        _endpoint("openai_compatible"),
        messages=[],
        ctx={},
    )

    assert raw == "这是一段自然语言，而不是 Review JSON。"
    assert result["risk_level"] == "UNKNOWN"
    assert parse_failed is True
