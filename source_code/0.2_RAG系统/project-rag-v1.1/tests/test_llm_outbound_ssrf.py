"""Regression tests for the outbound LLM SSRF boundary.

Every LLM HTTP path must validate its fully-resolved URL before constructing
or using an ``httpx.Client``.  These tests replace the client with a sentinel,
so a rejected destination is proven not to reach the network layer.
"""

from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from ai_review.review_service import AIReviewService, AIReviewUnavailable
from app import security
from app.services import extractor, llm, llm_pool, query_rewrite

MALICIOUS_URLS = (
    "http://169.254.169.254:80/v1",
    "https://metadata.google.internal/v1",
    "https://100.64.0.7/v1",
    "https://[fd00::7]/v1",
    "https://user:pass@example.com/v1",
)


class _NoNetworkClient:
    """Fail if code constructs an HTTP client before validation."""

    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("rejected LLM URL must not construct an HTTP client")


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "choices": [{"message": {"content": "validated response"}}],
            "models": [],
        }


class _RecordingClient:
    urls: list[str] = []

    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None

    def __enter__(self):
        return self

    def __exit__(self, *args):  # noqa: ANN002
        return False

    def post(self, url, **kwargs):  # noqa: ANN001, ANN003
        self.urls.append(url)
        return _Response()

    def get(self, url, **kwargs):  # noqa: ANN001, ANN003
        self.urls.append(url)
        return _Response()


class _MissingPoolTableSession:
    """Explicitly emulate a pre-migration endpoint-table state."""

    def execute(self, _statement):
        raise RuntimeError("relation rag_llm_model_endpoints does not exist")

    def close(self):
        return None


class _EmptyPoolSession:
    """Represent a readable endpoint table with zero configured rows."""

    def execute(self, _statement):
        return self

    def scalars(self):
        return self

    def all(self):
        return []

    def close(self):
        return None


@pytest.fixture(autouse=True)
def use_missing_endpoint_table(monkeypatch):
    """Make legacy endpoint tests explicit about the compatibility state."""
    monkeypatch.setattr(llm_pool, "SessionLocal", lambda: _MissingPoolTableSession())


@pytest.mark.parametrize("url", MALICIOUS_URLS)
def test_extractor_rejects_malicious_url_before_network(monkeypatch, url):
    monkeypatch.setattr(
        security.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("169.254.169.254", 80))],
    )
    monkeypatch.setattr(extractor, "LLM_BASE_URL", url)
    monkeypatch.setattr(extractor, "LLM_MODEL", "test-model")
    monkeypatch.setattr(extractor.httpx, "Client", _NoNetworkClient)

    with pytest.raises(extractor.ExtractionError, match="LLM call failed"):
        extractor._llm_extract("extract this")


@pytest.mark.parametrize("url", MALICIOUS_URLS)
def test_query_rewrite_helper_rejects_malicious_url_before_network(monkeypatch, url):
    monkeypatch.setattr(
        security.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("169.254.169.254", 80))],
    )
    monkeypatch.setattr(query_rewrite.httpx, "Client", _NoNetworkClient)

    result = query_rewrite._call_llm(
        base_url=url,
        model="test-model",
        api_key="",
        system_prompt="system",
        user_prompt="user",
        max_tokens=16,
        temperature=0.0,
    )

    assert result is None


@pytest.mark.parametrize("setting", ["override", "fallback"])
@pytest.mark.parametrize("helper", ["rewrite", "hyde"])
@pytest.mark.parametrize("url", MALICIOUS_URLS)
def test_rewrite_and_hyde_override_and_fallback_reject_malicious_url(
    monkeypatch, setting, helper, url
):
    """Dedicated URL overrides and the shared LLM fallback share the guard."""
    monkeypatch.setattr(
        security.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("169.254.169.254", 80))],
    )
    monkeypatch.setattr(query_rewrite.httpx, "Client", _NoNetworkClient)
    monkeypatch.setattr(query_rewrite, "ENABLE_QUERY_REWRITE", True)
    monkeypatch.setattr(query_rewrite, "ENABLE_HYDE", True)
    monkeypatch.setattr(query_rewrite, "REWRITE_LLM_MODEL", "rewrite-model")
    monkeypatch.setattr(query_rewrite, "HYDE_LLM_MODEL", "hyde-model")
    monkeypatch.setattr(query_rewrite, "LLM_MODEL", "shared-model")

    if helper == "rewrite":
        monkeypatch.setattr(query_rewrite, "REWRITE_LLM_BASE_URL", url if setting == "override" else "")
        monkeypatch.setattr(query_rewrite, "LLM_BASE_URL", url if setting == "fallback" else "https://example.com/v1")
        result = query_rewrite.rewrite_query("合同结算", {})
        assert result.status == "UNAVAILABLE"
        assert result.confidence == 0.0
    else:
        monkeypatch.setattr(query_rewrite, "HYDE_LLM_BASE_URL", url if setting == "override" else "")
        monkeypatch.setattr(query_rewrite, "LLM_BASE_URL", url if setting == "fallback" else "https://example.com/v1")
        result = query_rewrite.hyde_generate("合同结算")
        assert result.status == "UNAVAILABLE"
        assert result.helper_used is False


def _allow_public_dns(monkeypatch):
    monkeypatch.setattr(
        security.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (None, None, None, None, ("93.184.216.34", 443)),
        ],
    )


def _allow_dns(monkeypatch, *addresses):
    """Return deterministic DNS answers for local/internal policy tests."""
    monkeypatch.setattr(
        security.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (None, None, None, None, (address, args[1] if len(args) > 1 else 80))
            for address in addresses
        ],
    )


@pytest.mark.parametrize(
    ("url", "addresses"),
    [
        ("http://localhost:11434/v1", ("127.0.0.1", "::1")),
        ("http://127.0.0.1:11434/v1", ("127.0.0.1",)),
        ("https://[::1]:1234/v1", ("::1",)),
        ("http://10.0.0.7:8000/v1", ("10.0.0.7",)),
        ("https://172.16.12.4:8000/v1", ("172.16.12.4",)),
        ("http://192.168.1.20:1234/v1", ("192.168.1.20",)),
        ("http://llm.internal:8000/v1", ("10.20.30.40",)),
        ("http://[::ffff:127.0.0.1]:11434/v1", ("::ffff:127.0.0.1",)),
        ("http://[::ffff:10.0.0.7]:8000/v1", ("::ffff:10.0.0.7",)),
    ],
)
def test_llm_policy_allows_local_and_rfc1918_endpoints(monkeypatch, url, addresses):
    _allow_dns(monkeypatch, *addresses)
    assert security.validate_llm_outbound_url(url) == url


def test_llm_policy_allows_public_https_but_generic_policy_stays_strict(monkeypatch):
    _allow_public_dns(monkeypatch)
    assert security.validate_llm_outbound_url("https://example.com/v1") == "https://example.com/v1"
    _allow_dns(monkeypatch, "127.0.0.1")
    with pytest.raises(ValueError, match="private|reserved"):
        security.validate_outbound_url("https://127.0.0.1/v1")


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/v1",  # public HTTP must not be opened
        "http://169.254.169.254/v1",  # link-local metadata IP
        "https://100.64.0.7/v1",  # CGNAT
        "https://[fe80::1]/v1",  # IPv6 link-local
        "https://[fd00::1]/v1",  # IPv6 ULA is not opened this release
        "https://[::]/v1",  # unspecified
        "https://[ff02::1]/v1",  # multicast
        "https://192.0.2.10/v1",  # documentation
        "https://198.18.0.10/v1",  # benchmarking
        "https://user:pass@example.com/v1",  # userinfo
        "file:///tmp/model",  # dangerous scheme
    ],
)
def test_llm_policy_rejects_unsafe_destinations(monkeypatch, url):
    host = urlsplit(url).hostname
    _allow_dns(monkeypatch, host or "93.184.216.34")
    with pytest.raises(ValueError):
        security.validate_llm_outbound_url(url)


def test_llm_policy_rejects_mixed_private_public_dns(monkeypatch):
    _allow_dns(monkeypatch, "10.0.0.7", "93.184.216.34")
    with pytest.raises(ValueError, match="mixed"):
        security.validate_llm_outbound_url("https://split.internal/v1")


def test_llm_policy_uses_scheme_default_port_and_rejects_resolution_failure(monkeypatch):
    seen = {}

    def fake_getaddrinfo(host, port, **kwargs):
        seen["host"] = host
        seen["port"] = port
        return [(None, None, None, None, ("127.0.0.1", port))]

    monkeypatch.setattr(security.socket, "getaddrinfo", fake_getaddrinfo)
    security.validate_llm_outbound_url("http://llm.internal/v1")
    assert seen == {"host": "llm.internal", "port": 80}

    def unresolved(*args, **kwargs):
        raise OSError("NXDOMAIN")

    monkeypatch.setattr(security.socket, "getaddrinfo", unresolved)
    with pytest.raises(ValueError, match="resolved"):
        security.validate_llm_outbound_url("http://missing.internal/v1")


def test_extractor_accepts_public_https_after_validation(monkeypatch):
    _allow_public_dns(monkeypatch)
    _RecordingClient.urls = []
    monkeypatch.setattr(extractor, "LLM_BASE_URL", "https://example.com/v1")
    monkeypatch.setattr(extractor, "LLM_MODEL", "test-model")
    monkeypatch.setattr(extractor.httpx, "Client", _RecordingClient)

    assert extractor._llm_extract("extract this") == "validated response"
    assert _RecordingClient.urls == ["https://example.com/v1/chat/completions"]


def test_readable_empty_endpoint_table_does_not_construct_http_client():
    """A migrated empty table is authoritative and must fail closed."""
    constructed = []

    class NoClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            constructed.append((args, kwargs))
            raise AssertionError("empty endpoint table reached HTTP client")

    with pytest.raises(llm_pool.LLMPoolError) as caught:
        llm_pool.call_chat(
            [{"role": "user", "content": "question"}],
            session=_EmptyPoolSession(),
            legacy_base_url="https://legacy.example/v1",
            legacy_model="legacy-model",
            legacy_api_key="legacy-secret",
            http_client_factory=NoClient,
        )

    assert constructed == []
    assert caught.value.catalog is not None
    assert caught.value.catalog.table_state == "available"
    assert caught.value.catalog.source == "database_configured_empty"
    assert caught.value.catalog.endpoints == ()


@pytest.mark.parametrize("helper", ["rewrite", "hyde"])
@pytest.mark.parametrize("setting", ["override", "fallback"])
def test_rewrite_and_hyde_accept_public_https_override_and_fallback(
    monkeypatch, helper, setting
):
    _allow_public_dns(monkeypatch)
    _RecordingClient.urls = []
    monkeypatch.setattr(query_rewrite.httpx, "Client", _RecordingClient)
    monkeypatch.setattr(query_rewrite, "ENABLE_QUERY_REWRITE", True)
    monkeypatch.setattr(query_rewrite, "ENABLE_HYDE", True)
    monkeypatch.setattr(query_rewrite, "REWRITE_LLM_MODEL", "rewrite-model")
    monkeypatch.setattr(query_rewrite, "HYDE_LLM_MODEL", "hyde-model")
    monkeypatch.setattr(query_rewrite, "LLM_MODEL", "shared-model")

    public_url = "https://example.com/v1"
    if helper == "rewrite":
        monkeypatch.setattr(query_rewrite, "REWRITE_LLM_BASE_URL", public_url if setting == "override" else "")
        monkeypatch.setattr(query_rewrite, "LLM_BASE_URL", public_url)
        result = query_rewrite.rewrite_query("合同结算", {})
        assert result.status == "INVALID_RESPONSE"
    else:
        monkeypatch.setattr(query_rewrite, "HYDE_LLM_BASE_URL", public_url if setting == "override" else "")
        monkeypatch.setattr(query_rewrite, "LLM_BASE_URL", public_url)
        result = query_rewrite.hyde_generate("合同结算")
        assert result.status == "OK"
        assert result.helper_used is True
    assert _RecordingClient.urls == ["https://example.com/v1/chat/completions"]


@pytest.mark.parametrize("helper", ["rewrite", "hyde"])
@pytest.mark.parametrize("setting", ["override", "fallback"])
def test_rewrite_and_hyde_accept_local_http_override_and_fallback(
    monkeypatch, helper, setting
):
    """Local Ollama/LM Studio/vLLM endpoints are allowed by the LLM policy."""
    _allow_dns(monkeypatch, "10.0.0.7")
    _RecordingClient.urls = []
    monkeypatch.setattr(query_rewrite.httpx, "Client", _RecordingClient)
    monkeypatch.setattr(query_rewrite, "ENABLE_QUERY_REWRITE", True)
    monkeypatch.setattr(query_rewrite, "ENABLE_HYDE", True)
    monkeypatch.setattr(query_rewrite, "REWRITE_LLM_MODEL", "rewrite-model")
    monkeypatch.setattr(query_rewrite, "HYDE_LLM_MODEL", "hyde-model")
    monkeypatch.setattr(query_rewrite, "LLM_MODEL", "shared-model")

    local_url = "http://llm.internal:8000/v1"
    if helper == "rewrite":
        monkeypatch.setattr(query_rewrite, "REWRITE_LLM_BASE_URL", local_url if setting == "override" else "")
        monkeypatch.setattr(query_rewrite, "LLM_BASE_URL", local_url)
        result = query_rewrite.rewrite_query("合同结算", {})
        assert result.status == "INVALID_RESPONSE"
    else:
        monkeypatch.setattr(query_rewrite, "HYDE_LLM_BASE_URL", local_url if setting == "override" else "")
        monkeypatch.setattr(query_rewrite, "LLM_BASE_URL", local_url)
        result = query_rewrite.hyde_generate("合同结算")
        assert result.status == "OK"
        assert result.helper_used is True
    assert _RecordingClient.urls == ["http://llm.internal:8000/v1/chat/completions"]


def test_all_answer_paths_validate_public_https_before_request(monkeypatch):
    _allow_public_dns(monkeypatch)
    _RecordingClient.urls = []
    monkeypatch.setattr(llm, "LLM_BASE_URL", "https://example.com/v1")
    monkeypatch.setattr(llm, "LLM_MODEL", "test-model")
    monkeypatch.setattr(llm.httpx, "Client", _RecordingClient)

    evidence = [{"filename": "x.md", "text": "evidence", "page_start": 1}]
    assert llm.answer_with_llm("question", evidence) == "validated response"
    assert llm.test_llm_connection()["ok"] is True
    assert llm._call_llm_chat("system", "user") == "validated response"
    assert _RecordingClient.urls == [
        "https://example.com/v1/chat/completions",
        "https://example.com/v1/models",
        "https://example.com/v1/chat/completions",
    ]


def test_all_answer_paths_allow_local_http_llm(monkeypatch):
    _allow_dns(monkeypatch, "127.0.0.1")
    _RecordingClient.urls = []
    monkeypatch.setattr(llm, "LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setattr(llm, "LLM_MODEL", "test-model")
    monkeypatch.setattr(llm.httpx, "Client", _RecordingClient)

    evidence = [{"filename": "x.md", "text": "evidence", "page_start": 1}]
    assert llm.answer_with_llm("question", evidence) == "validated response"
    assert llm.test_llm_connection()["ok"] is True
    assert llm._call_llm_chat("system", "user") == "validated response"
    assert _RecordingClient.urls == [
        "http://127.0.0.1:11434/v1/chat/completions",
        "http://127.0.0.1:11434/v1/models",
        "http://127.0.0.1:11434/v1/chat/completions",
    ]


@pytest.mark.parametrize("url", MALICIOUS_URLS)
def test_all_answer_paths_reject_malicious_url_before_network(monkeypatch, url):
    monkeypatch.setattr(
        security.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("169.254.169.254", 80))],
    )
    monkeypatch.setattr(llm, "LLM_BASE_URL", url)
    monkeypatch.setattr(llm, "LLM_MODEL", "test-model")
    monkeypatch.setattr(llm.httpx, "Client", _NoNetworkClient)
    evidence = [{"filename": "x.md", "text": "evidence", "page_start": 1}]

    with pytest.raises(llm.LLMError, match="LLM call failed"):
        llm.answer_with_llm("question", evidence)
    assert llm.test_llm_connection()["ok"] is False
    assert llm._call_llm_chat("system", "user") is None


def test_ai_review_allows_local_http_llm_before_client(monkeypatch):
    _allow_dns(monkeypatch, "127.0.0.1")
    _RecordingClient.urls = []
    monkeypatch.setattr(llm_pool, "LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setattr(llm_pool, "LLM_MODEL", "configured-model")
    monkeypatch.setattr(llm_pool, "LLM_API_KEY", "")
    monkeypatch.setattr(llm_pool.httpx, "Client", _RecordingClient)

    service = AIReviewService(db=None, facts_provider=SimpleNamespace())
    assert service._call_llm("review prompt", "review-model") == "validated response"
    assert _RecordingClient.urls == [
        "http://127.0.0.1:11434/v1/chat/completions",
    ]


def test_ai_review_rejects_unsafe_llm_url_before_client(monkeypatch):
    monkeypatch.setattr(
        security.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("169.254.169.254", 80))],
    )
    monkeypatch.setattr(llm_pool, "LLM_BASE_URL", "http://169.254.169.254/v1")
    monkeypatch.setattr(llm_pool, "LLM_MODEL", "configured-model")
    monkeypatch.setattr(llm_pool, "LLM_API_KEY", "")
    monkeypatch.setattr(llm_pool.httpx, "Client", _NoNetworkClient)

    service = AIReviewService(db=None, facts_provider=SimpleNamespace())
    with pytest.raises(AIReviewUnavailable, match="URL"):
        service._call_llm("review prompt", "review-model")
