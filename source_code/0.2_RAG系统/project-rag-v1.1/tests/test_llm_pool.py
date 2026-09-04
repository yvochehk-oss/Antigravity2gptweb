"""Unit coverage for the shared RAG LLM endpoint pool."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from ai_review.review_service import AIReviewService, AIReviewUnavailable
from app.services import ai_regulation_extractor, extractor, llm, llm_pool, query_rewrite


class FakeScalarResult:
    def __init__(self, rows):
        self.rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)


class FakeSession:
    def __init__(self, rows):
        self.rows = list(rows)
        self.executed = 0

    def execute(self, _statement):
        self.executed += 1
        return FakeScalarResult(self.rows)


def endpoint(
    name: str,
    *,
    id: int,
    priority: int = 100,
    enabled: bool = True,
    routing_group: str = "default",
    base_url: str | None = None,
    chat_path: str = "/v1/chat/completions",
    model: str | None = None,
    api_key: str = "secret-key",
):
    return SimpleNamespace(
        id=id,
        name=name,
        base_url=base_url or f"http://127.0.0.1:{9000 + id}",
        chat_path=chat_path,
        model=model or name,
        api_key=api_key,
        enabled=enabled,
        timeout_seconds=15,
        priority=priority,
        routing_group=routing_group,
    )


class FakeResponse:
    def __init__(self, payload=None, status_code: int = 200):
        self.payload = payload if payload is not None else {
            "choices": [{"message": {"content": "ok"}}],
        }
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://fake.test/v1/chat/completions")
            raise httpx.HTTPStatusError("provider failure", request=request, response=self)

    def json(self):
        return self.payload


class FakeClient:
    responses: list[FakeResponse] = []
    calls: list[dict] = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, **kwargs):
        self.calls.append({"method": "POST", "url": url, **kwargs})
        return self.responses.pop(0)

    def get(self, url, **kwargs):
        self.calls.append({"method": "GET", "url": url, **kwargs})
        return self.responses.pop(0)


def reset_client():
    FakeClient.responses = []
    FakeClient.calls = []


def test_catalog_orders_enabled_default_rows_and_ignores_other_groups():
    session = FakeSession(
        [
            endpoint("late", id=9, priority=20),
            endpoint("early", id=4, priority=5),
            endpoint("tie-later", id=8, priority=5),
            endpoint("disabled", id=1, priority=0, enabled=False),
            endpoint("other-group", id=2, priority=0, routing_group="review"),
        ]
    )

    catalog = llm_pool.get_endpoint_catalog(
        session=session,
        legacy_base_url="https://legacy.invalid/v1",
        legacy_model="legacy-model",
        legacy_api_key="legacy-secret",
    )

    assert [item.name for item in catalog.endpoints] == ["early", "tie-later", "late"]
    assert catalog.row_count == 5
    assert catalog.source == "database"
    assert session.executed == 1


def test_empty_table_is_authoritative_and_does_not_use_legacy_environment_configuration():
    catalog = llm_pool.get_endpoint_catalog(
        session=FakeSession([]),
        legacy_base_url="https://legacy.example/v1",
        legacy_model="legacy-model",
        legacy_api_key="legacy-secret",
        legacy_timeout_seconds=60,
    )

    assert catalog.source == "database_configured_empty"
    assert catalog.table_state == "available"
    assert catalog.row_count == 0
    assert catalog.endpoints == ()


def test_call_chat_with_empty_table_fails_before_constructing_http_client():
    constructed = []

    class NoClient:
        def __init__(self, *args, **kwargs):
            constructed.append((args, kwargs))
            raise AssertionError("empty endpoint table reached HTTP client")

    with pytest.raises(llm_pool.LLMPoolError) as caught:
        llm_pool.call_chat(
            [{"role": "user", "content": "question"}],
            session=FakeSession([]),
            legacy_base_url="https://legacy.example/v1",
            legacy_model="legacy-model",
            legacy_api_key="legacy-secret",
            http_client_factory=NoClient,
        )

    assert constructed == []
    assert caught.value.catalog is not None
    assert caught.value.catalog.source == "database_configured_empty"
    assert caught.value.catalog.endpoints == ()


def test_disabled_row_suppresses_legacy_fallback():
    catalog = llm_pool.get_endpoint_catalog(
        session=FakeSession([endpoint("disabled", id=1, enabled=False)]),
        legacy_base_url="https://legacy.example/v1",
        legacy_model="legacy-model",
        legacy_api_key="legacy-secret",
    )

    assert catalog.endpoints == ()
    assert catalog.source == "database_configured_empty"


def test_failover_returns_attempts_and_effective_key_free_endpoint():
    reset_client()
    FakeClient.responses = [
        FakeResponse(status_code=503),
        FakeResponse(payload={"choices": [{"message": {"content": "second answer"}}]}),
    ]
    rows = [endpoint("first", id=1, priority=1), endpoint("second", id=2, priority=2)]

    result = llm_pool.call_chat(
        [{"role": "user", "content": "question"}],
        session=FakeSession(rows),
        http_client_factory=FakeClient,
    )

    assert result.text == "second answer"
    assert [attempt["name"] for attempt in result.attempts] == ["first", "second"]
    assert [attempt["status"] for attempt in result.attempts] == ["failed", "success"]
    assert result.effective_endpoint["name"] == "second"
    assert "api_key" not in result.effective_endpoint
    assert all("api_key" not in attempt for attempt in result.attempts)
    assert FakeClient.calls[0]["url"].endswith("/v1/chat/completions")
    assert FakeClient.calls[0]["headers"]["Authorization"] == "Bearer secret-key"


def test_managed_local_fallback_is_last_after_persisted_user_endpoints():
    catalog = llm_pool.get_endpoint_catalog(
        session=FakeSession([
            endpoint("user-primary", id=1, priority=1),
            endpoint("user-backup", id=2, priority=100),
        ]),
        local_base_url="http://127.0.0.1:8930/v1",
        local_model="ling-3.0-tiny",
    )

    assert [item.name for item in catalog.endpoints] == [
        "user-primary",
        "user-backup",
        "local-llama.cpp",
    ]
    assert catalog.endpoints[-1].source == "local_fallback"
    assert catalog.endpoints[-1].api_key == ""
    assert catalog.source == "database_with_local_fallback"


def test_managed_local_fallback_is_used_when_migrated_table_is_empty():
    catalog = llm_pool.get_endpoint_catalog(
        session=FakeSession([]),
        legacy_base_url="https://legacy.example/v1",
        legacy_model="legacy-model",
        local_base_url="http://127.0.0.1:8930/v1",
        local_model="ling-3.0-tiny",
    )

    assert [item.source for item in catalog.endpoints] == ["local_fallback"]
    assert catalog.source == "database_with_local_fallback"


def test_persisted_disabled_local_row_suppresses_launcher_duplicate():
    catalog = llm_pool.get_endpoint_catalog(
        session=FakeSession([
            endpoint(
                "local-disabled",
                id=1,
                enabled=False,
                base_url="http://127.0.0.1:8930",
                model="ling-3.0-tiny",
            ),
        ]),
        local_base_url="http://127.0.0.1:8930/v1",
        local_model="ling-3.0-tiny",
    )

    assert catalog.endpoints == ()
    assert catalog.source == "database_configured_empty"


def test_managed_local_fallback_participates_in_failover_without_a_key():
    reset_client()
    FakeClient.responses = [
        FakeResponse(status_code=503),
        FakeResponse(payload={"choices": [{"message": {"content": "local answer"}}]}),
    ]

    result = llm_pool.call_chat(
        [{"role": "user", "content": "question"}],
        session=FakeSession([endpoint("user-primary", id=1, priority=1)]),
        local_base_url="http://127.0.0.1:8930/v1",
        local_model="ling-3.0-tiny",
        http_client_factory=FakeClient,
    )

    assert result.text == "local answer"
    assert [attempt["name"] for attempt in result.attempts] == [
        "user-primary",
        "local-llama.cpp",
    ]
    assert result.effective_endpoint["source"] == "local_fallback"
    assert "Authorization" not in FakeClient.calls[1]["headers"]


def test_connection_probe_uses_get_models_for_local_llama_endpoint():
    reset_client()
    FakeClient.responses = [FakeResponse(payload={"data": [{"id": "local-qwen"}]})]

    result = llm_pool.check_connection(
        session=FakeSession([
            endpoint(
                "local-llama",
                id=1,
                base_url="http://127.0.0.1:8930/v1",
            ),
        ]),
        http_client_factory=FakeClient,
    )

    assert result["ok"] is True
    assert result["models"] == {"data": [{"id": "local-qwen"}]}
    assert FakeClient.calls == [
        {
            "method": "GET",
            "url": "http://127.0.0.1:8930/v1/models",
            "headers": {"Content-Type": "application/json", "Authorization": "Bearer secret-key"},
        },
    ]


def test_connection_probe_rejects_unsafe_url_before_client_construction():
    class NoClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("unsafe URL reached HTTP client")

    result = llm_pool.check_connection(
        session=FakeSession([
            endpoint(
                "metadata",
                id=1,
                base_url="http://169.254.169.254/v1",
            ),
        ]),
        http_client_factory=NoClient,
    )

    assert result["ok"] is False
    assert result["attempts"][0]["status"] == "rejected"


def test_ai_review_uses_pool_priority_and_failover(monkeypatch):
    reset_client()
    FakeClient.responses = [
        FakeResponse(status_code=503),
        FakeResponse(payload={"choices": [{"message": {"content": "backup review"}}]}),
    ]
    monkeypatch.setattr(llm_pool.httpx, "Client", FakeClient)
    session = FakeSession([
        endpoint("primary", id=1, priority=1),
        endpoint("backup", id=2, priority=2),
    ])

    service = AIReviewService(db=session, facts_provider=SimpleNamespace())

    assert service._call_llm("review prompt", "request-model") == "backup review"
    assert [call["method"] for call in FakeClient.calls] == ["POST", "POST"]
    assert FakeClient.calls[0]["json"]["model"] == "primary"
    assert FakeClient.calls[1]["json"]["model"] == "backup"


def test_ai_review_disabled_pool_does_not_reactivate_legacy_or_leak_key(monkeypatch):
    reset_client()
    monkeypatch.setattr(llm_pool.httpx, "Client", FakeClient)
    session = FakeSession([endpoint("disabled", id=1, enabled=False)])
    service = AIReviewService(db=session, facts_provider=SimpleNamespace())

    with pytest.raises(AIReviewUnavailable) as caught:
        service._call_llm("review prompt", "request-model")

    assert "secret-key" not in str(caught.value)
    assert FakeClient.calls == []


def test_ai_review_database_outage_fails_closed_without_legacy_or_key_leak():
    class BrokenSession:
        def execute(self, _statement):
            raise RuntimeError("database password=secret-key connection refused")

    service = AIReviewService(db=BrokenSession(), facts_provider=SimpleNamespace())

    with pytest.raises(AIReviewUnavailable) as caught:
        service._call_llm("review prompt", "request-model")

    assert "secret-key" not in str(caught.value)


def test_empty_completion_continues_to_next_endpoint():
    reset_client()
    FakeClient.responses = [
        FakeResponse(payload={"choices": [{"message": {"content": "  "}}]}),
        FakeResponse(payload={"choices": [{"message": {"content": "usable"}}]}),
    ]

    result = llm_pool.call_chat(
        [{"role": "user", "content": "question"}],
        session=FakeSession([endpoint("empty", id=1), endpoint("usable", id=2)]),
        http_client_factory=FakeClient,
    )

    assert result.text == "usable"
    assert [attempt["status"] for attempt in result.attempts] == ["empty", "success"]


def test_all_failures_raise_controlled_error_with_safe_attempts():
    reset_client()
    FakeClient.responses = [FakeResponse(status_code=500), FakeResponse(payload={})]
    with pytest.raises(llm_pool.LLMPoolError) as caught:
        llm_pool.call_chat(
            [{"role": "user", "content": "question"}],
            session=FakeSession([endpoint("bad-a", id=1), endpoint("bad-b", id=2)]),
            http_client_factory=FakeClient,
        )

    assert "all 2 configured" in str(caught.value)
    assert [attempt["status"] for attempt in caught.value.attempts] == ["failed", "empty"]
    assert all("secret-key" not in str(attempt) for attempt in caught.value.attempts)


def test_rejected_url_does_not_construct_http_client():
    class NoClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("unsafe URL reached HTTP client")

    row = endpoint("unsafe", id=1, base_url="http://169.254.169.254/v1")
    with pytest.raises(llm_pool.LLMPoolError) as caught:
        llm_pool.call_chat(
            [{"role": "user", "content": "question"}],
            session=FakeSession([row]),
            http_client_factory=NoClient,
        )

    assert caught.value.attempts[0]["status"] == "rejected"


def test_safe_endpoint_view_has_no_api_key():
    view = llm_pool.safe_endpoint_view(endpoint("safe", id=7, api_key="do-not-leak"))
    assert "api_key" not in view
    assert "do-not-leak" not in str(view)


def test_table_unavailable_is_explicit_legacy_compat(monkeypatch):
    class BrokenSession:
        def execute(self, _statement):
            raise RuntimeError("relation rag_llm_model_endpoints does not exist")

    catalog = llm_pool.get_endpoint_catalog(
        session=BrokenSession(),
        legacy_base_url="https://legacy.example/v1",
        legacy_model="legacy-model",
        legacy_api_key="legacy-secret",
    )
    assert catalog.table_state == "unavailable"
    assert catalog.source == "legacy_env_compat"
    assert catalog.endpoints[0].fallback_reason == "endpoint_table_unavailable"


def test_generic_db_error_fails_closed_without_legacy_fallback():
    class BrokenSession:
        def execute(self, _statement):
            raise RuntimeError("database connection refused")

    catalog = llm_pool.get_endpoint_catalog(
        session=BrokenSession(),
        legacy_base_url="https://legacy.example/v1",
        legacy_model="legacy-model",
        legacy_api_key="legacy-secret",
    )

    assert catalog.table_state == "error"
    assert catalog.source == "database_error"
    assert catalog.endpoints == ()
    assert catalog.error_class == "RuntimeError"


def test_disabled_row_then_db_outage_never_reactivates_legacy_endpoint():
    class DisabledSession(FakeSession):
        pass

    class BrokenSession:
        def execute(self, _statement):
            raise RuntimeError("database connection refused")

    legacy = {
        "legacy_base_url": "https://legacy.example/v1",
        "legacy_model": "legacy-model",
        "legacy_api_key": "legacy-secret",
    }
    disabled = llm_pool.get_endpoint_catalog(
        session=DisabledSession([endpoint("disabled", id=1, enabled=False)]),
        **legacy,
    )
    outage = llm_pool.get_endpoint_catalog(session=BrokenSession(), **legacy)

    assert disabled.source == "database_configured_empty"
    assert disabled.endpoints == ()
    assert outage.source == "database_error"
    assert outage.endpoints == ()


def test_generic_table_error_is_not_misclassified_as_missing_table():
    class BrokenSession:
        def execute(self, _statement):
            raise RuntimeError("permission denied for relation rag_llm_model_endpoints")

    catalog = llm_pool.get_endpoint_catalog(
        session=BrokenSession(),
        legacy_base_url="https://legacy.example/v1",
        legacy_model="legacy-model",
        legacy_api_key="legacy-secret",
    )

    assert catalog.table_state == "error"
    assert catalog.source == "database_error"
    assert catalog.endpoints == ()


def test_extractor_uses_shared_pool_adapter(monkeypatch):
    seen = {}

    def fake_call(messages, **kwargs):
        seen.update(kwargs)
        return llm_pool.LLMCallResult(text='{"invoice_no": "I-1", "total_amount": 1, "vat_amount": 0}')

    monkeypatch.setattr(extractor.llm_pool, "call_chat", fake_call)
    assert extractor._llm_extract("extract", session=object()).startswith("{")
    assert seen["routing_group"] == "default"


def test_extractor_passes_managed_local_fallback_to_pool(monkeypatch):
    seen = {}

    def fake_call(_messages, **kwargs):
        seen.update(kwargs)
        return llm_pool.LLMCallResult(text='{"invoice_no": "I-1"}')

    monkeypatch.setattr(extractor, "LLM_LOCAL_BASE_URL", "http://127.0.0.1:8931/v1")
    monkeypatch.setattr(extractor, "LLM_LOCAL_MODEL", "Spark-X2.5-4B")
    monkeypatch.setattr(extractor, "LLM_LOCAL_TIMEOUT_SECONDS", 7)
    monkeypatch.setattr(extractor.llm_pool, "call_chat", fake_call)

    assert extractor._llm_extract("extract", session=object()).startswith("{")
    assert seen["local_base_url"] == "http://127.0.0.1:8931/v1"
    assert seen["local_model"] == "Spark-X2.5-4B"
    assert seen["local_timeout_seconds"] == 7


def test_extraction_health_probes_managed_local_fallback(monkeypatch):
    reset_client()
    FakeClient.responses = [FakeResponse(payload={"data": [{"id": "Spark-X2.5-4B"}]})]
    monkeypatch.setattr(extractor, "LLM_LOCAL_BASE_URL", "http://127.0.0.1:8931/v1")
    monkeypatch.setattr(extractor, "LLM_LOCAL_MODEL", "Spark-X2.5-4B")
    monkeypatch.setattr(extractor, "LLM_LOCAL_TIMEOUT_SECONDS", 60)
    monkeypatch.setattr(extractor.llm_pool, "SessionLocal", lambda: FakeSession([]))
    monkeypatch.setattr(extractor.httpx, "Client", FakeClient)

    assert extractor.llm_extraction_available() is True
    assert FakeClient.calls[0]["method"] == "GET"
    assert FakeClient.calls[0]["url"] == "http://127.0.0.1:8931/v1/models"
    assert FakeClient.calls[0]["headers"] == {"Content-Type": "application/json"}


def test_extraction_health_is_false_when_all_probes_fail(monkeypatch):
    reset_client()
    FakeClient.responses = [FakeResponse(status_code=503)]
    monkeypatch.setattr(extractor, "LLM_LOCAL_BASE_URL", "http://127.0.0.1:8931/v1")
    monkeypatch.setattr(extractor, "LLM_LOCAL_MODEL", "Spark-X2.5-4B")
    monkeypatch.setattr(extractor.llm_pool, "SessionLocal", lambda: FakeSession([]))
    monkeypatch.setattr(extractor.httpx, "Client", FakeClient)

    assert extractor.llm_extraction_available() is False


def test_regulation_extractor_uses_shared_pool_and_marks_ai_success(monkeypatch):
    seen = {}

    def fake_call(messages, **kwargs):
        seen.update(kwargs)
        return llm_pool.LLMCallResult(
            text='{"title":"增值税法","legal_level":"法律"}',
        )

    monkeypatch.setattr(ai_regulation_extractor.llm_pool, "call_chat", fake_call)
    meta = ai_regulation_extractor.ai_extract_regulation_metadata("增值税法\n第一条", session=object())

    assert meta["raw_extracted"] is True
    assert meta["ai_status"] == "OK"
    assert meta["ai_degraded"] is False
    assert seen["routing_group"] == "default"
    assert seen["legacy_timeout_seconds"] == 15


def test_regulation_extractor_preserves_pool_failover_metadata_on_success(monkeypatch):
    def failover_call(_messages, **_kwargs):
        return llm_pool.LLMCallResult(
            text='{"title":"增值税法"}',
            attempts=[
                {"name": "primary", "status": "failed"},
                {"name": "backup", "status": "success"},
            ],
            effective_endpoint={"name": "backup", "model": "backup-model"},
        )

    monkeypatch.setattr(ai_regulation_extractor.llm_pool, "call_chat", failover_call)
    meta = ai_regulation_extractor.ai_extract_regulation_metadata("增值税法\n第一条")

    assert meta["raw_extracted"] is True
    assert meta["ai_status"] == "OK"


def test_regulation_extractor_marks_disabled_pool_as_non_ai_degraded(monkeypatch):
    disabled = llm_pool.EndpointCatalog(
        endpoints=(),
        table_state="available",
        row_count=1,
        source="database_configured_empty",
    )

    def disabled_call(_messages, **_kwargs):
        raise llm_pool.LLMPoolError(
            "RAG LLM endpoint pool is not configured or all configured endpoints are disabled",
            catalog=disabled,
        )

    monkeypatch.setattr(ai_regulation_extractor.llm_pool, "call_chat", disabled_call)
    meta = ai_regulation_extractor.ai_extract_regulation_metadata("增值税法\n第一条")

    assert meta["ai_status"] == "DEGRADED"
    assert meta["ai_degraded"] is True
    assert "规则启发式" in meta["summary"]


def test_regulation_extractor_marks_pool_failure_as_explicit_degradation(monkeypatch):
    secret = "regulation-test-secret"

    def fail_call(_messages, **_kwargs):
        raise llm_pool.LLMPoolError("all configured endpoints failed")

    monkeypatch.setattr(ai_regulation_extractor.llm_pool, "call_chat", fail_call)
    meta = ai_regulation_extractor.ai_extract_regulation_metadata("中华人民共和国增值税法\n第一条")

    assert meta["raw_extracted"] is False
    assert meta["ai_status"] == "DEGRADED"
    assert meta["ai_degraded"] is True
    assert meta["ai_degradation_reason"]
    assert secret not in str(meta)


def test_answer_service_uses_shared_pool_adapter(monkeypatch):
    seen = {}

    def fake_call(messages, **kwargs):
        seen["messages"] = messages
        return llm_pool.LLMCallResult(text="grounded answer")

    monkeypatch.setattr(llm.llm_pool, "call_chat", fake_call)
    assert llm.answer_with_llm(
        "question",
        [{"filename": "evidence.md", "text": "evidence", "page_start": 1}],
        session=object(),
    ) == "grounded answer"
    assert seen["messages"][0]["role"] == "user"


def test_query_rewrite_uses_shared_pool_adapter(monkeypatch):
    seen = {}

    def fake_call(messages, **kwargs):
        seen["messages"] = messages
        return llm_pool.LLMCallResult(text='{"intent":"金额","rewritten_query":"合同金额","filters":{},"keywords":[]}')

    monkeypatch.setattr(query_rewrite.llm_pool, "call_chat", fake_call)
    text = query_rewrite._call_llm(
        "https://ignored.example/v1",
        "ignored-model",
        "ignored-key",
        "system",
        "user",
        32,
        0.1,
        session=object(),
    )
    assert text and text.startswith("{")
    assert seen["messages"][0]["role"] == "system"
