"""
test_rag_http_client.py
=======================
测试 app.services.rag_http_client 的容错行为。
"""

import logging

import httpx
import pytest


def _install_mock_transport(monkeypatch, rag_http_client, handler, captured):
    """Route the real shared httpx client through an in-process transport."""
    real_client = rag_http_client.httpx.Client

    def client_factory(*args, **kwargs):
        captured.append(dict(kwargs))
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(rag_http_client.httpx, "Client", client_factory)


def _success_payload(request: httpx.Request) -> httpx.Response:
    payloads = {
        "/api/v1/projects": [],
        "/api/v1/retrieve": {"results": []},
        "/api/v1/query": {"answer": "", "results": [], "citations": []},
        "/api/v1/regulations/retrieve": {"results": []},
        "/api/v1/regulations/query": {"answer": "", "results": [], "citations": []},
        "/api/v1/health": {"status": "ok"},
    }
    return httpx.Response(200, json=payloads[request.url.path], request=request)


def test_module_imports_cleanly():
    """模块能正常 import，不抛异常"""
    from app.services import rag_http_client  # noqa: F401


def test_health_returns_unreachable_when_no_server(monkeypatch):
    """无 server 时 health 返回 unreachable，不抛异常"""
    from app.services import rag_http_client

    monkeypatch.setattr(rag_http_client, "DEFAULT_BASE_URL", "http://127.0.0.1:1")  # 端口 1 不可用
    monkeypatch.setattr(rag_http_client, "DEFAULT_TIMEOUT", "0.5")
    rag_http_client.DEFAULT_BASE_URL = "http://127.0.0.1:1"
    rag_http_client.DEFAULT_TIMEOUT = 0.5

    result = rag_http_client.health()
    assert isinstance(result, dict)
    assert result.get("status") in {"unreachable", "ok"}


def test_retrieve_returns_degraded_dict_on_failure(monkeypatch):
    """retrieve 失败时返回 degraded dict（含 status=DEGRADED），不抛异常也不伪装成功空列表"""
    from app.services import rag_http_client

    rag_http_client.DEFAULT_BASE_URL = "http://127.0.0.1:1"
    rag_http_client.DEFAULT_TIMEOUT = 0.5

    result = rag_http_client.retrieve(
        project_id=1, query="合同", top_k=5,
    )
    assert isinstance(result, dict)
    assert result.get("status") == "DEGRADED"
    assert result.get("error")


def test_query_returns_degraded_dict_on_failure(monkeypatch):
    """query 失败时返回 degraded 结构（不再是空结果）"""
    from app.services import rag_http_client

    rag_http_client.DEFAULT_BASE_URL = "http://127.0.0.1:1"
    rag_http_client.DEFAULT_TIMEOUT = 0.5

    result = rag_http_client.query(project_id=1, query="合同")
    assert isinstance(result, dict)
    assert result.get("status") == "DEGRADED"
    assert result.get("error")


def test_retrieve_regulations_returns_degraded_dict_on_failure(monkeypatch):
    """法规检索失败时返回 degraded dict，不再返回空列表伪装成功"""
    from app.services import rag_http_client

    rag_http_client.DEFAULT_BASE_URL = "http://127.0.0.1:1"
    rag_http_client.DEFAULT_TIMEOUT = 0.5

    result = rag_http_client.retrieve_regulations(query="增值税")
    assert isinstance(result, dict)
    assert result.get("status") == "DEGRADED"
    assert result.get("error")


def test_list_projects_returns_degraded_dict_on_failure(monkeypatch):
    """list_projects 失败时返回 degraded dict，不再返回空列表"""
    from app.services import rag_http_client

    rag_http_client.DEFAULT_BASE_URL = "http://127.0.0.1:1"
    rag_http_client.DEFAULT_TIMEOUT = 0.5

    result = rag_http_client.list_projects()
    assert isinstance(result, dict)
    assert result.get("status") == "DEGRADED"
    assert result.get("error")


def test_resolve_project_id_returns_none_when_not_found(monkeypatch):
    """resolve_project_id 找不到时返回 None"""
    from app.services import rag_http_client

    rag_http_client.DEFAULT_BASE_URL = "http://127.0.0.1:1"
    rag_http_client.DEFAULT_TIMEOUT = 0.5

    assert rag_http_client.resolve_project_id("NONEXISTENT") is None


def test_env_override(monkeypatch):
    """环境变量能覆盖 base_url"""
    monkeypatch.setenv("RAG_HTTP_BASE_URL", "http://example.com:9999")

    # 重新 import 让模块重新读取常量
    import importlib
    from app.services import rag_http_client
    importlib.reload(rag_http_client)
    assert rag_http_client.DEFAULT_BASE_URL == "http://example.com:9999"


@pytest.mark.parametrize(
    ("operation", "kwargs"),
    [
        ("list_projects", {}),
        ("retrieve", {"project_id": 1, "query": "合同"}),
        ("query", {"project_id": 1, "query": "合同"}),
        ("retrieve_regulations", {"query": "增值税"}),
        ("query_regulations", {"query": "增值税"}),
        ("health", {}),
    ],
    ids=[
        "projects",
        "retrieve",
        "query",
        "regulations-retrieve",
        "regulations-query",
        "health",
    ],
)
def test_all_bridge_operations_share_trimmed_bearer_header(
    monkeypatch, operation, kwargs,
):
    """Every bridge operation must use one trimmed shared-key client."""
    from app.services import rag_http_client

    monkeypatch.setenv("RAG_SHARED_API_KEY", "  bridge-secret \t")
    captured = []
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _success_payload(request)

    _install_mock_transport(monkeypatch, rag_http_client, handler, captured)
    result = getattr(rag_http_client, operation)(**kwargs)

    assert requests
    assert requests[0].headers["Authorization"] == "Bearer bridge-secret"
    assert captured[0]["headers"] == {"Authorization": "Bearer bridge-secret"}
    assert not (isinstance(result, dict) and result.get("status") == "DEGRADED")


def test_shared_key_is_optional_for_local_bridge_and_health_stays_public(monkeypatch):
    """No key means no Authorization header; public health remains callable."""
    from app.services import rag_http_client

    monkeypatch.delenv("RAG_SHARED_API_KEY", raising=False)
    requests = []
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _success_payload(request)

    _install_mock_transport(monkeypatch, rag_http_client, handler, captured)
    result = rag_http_client.health()

    assert result == {"status": "ok"}
    assert requests
    assert "authorization" not in requests[0].headers
    assert captured[0].get("headers") is None

    # A whitespace-only value is equivalent to an unset key after trimming.
    monkeypatch.setenv("RAG_SHARED_API_KEY", " \t\n")
    rag_http_client.health()
    assert "authorization" not in requests[-1].headers
    assert captured[-1].get("headers") is None


def test_auth_required_business_endpoint_accepts_correct_key_and_degrades_on_401(monkeypatch):
    """AUTH_REQUIRED middleware accepts good bearer and degrades on 401."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import security
    from app.services import rag_http_client

    expected = "auth-required-bridge-key"
    monkeypatch.setenv("PROJECT_RAG_AUTH_REQUIRED", "1")
    monkeypatch.setenv("RAG_SHARED_API_KEY", expected)

    # Use the real middleware and a business-shaped route.  The bridge client
    # is adapted only at the transport boundary, so no socket or service
    # process is needed for this deterministic integration test.
    monkeypatch.setattr(security, "AUTH_REQUIRED", True)
    monkeypatch.setattr(security, "RAG_SHARED_API_KEY", expected)
    app = FastAPI()
    app.add_middleware(security.RAGSecurityMiddleware)

    @app.post("/api/v1/retrieve")
    def retrieve_endpoint():
        return {"results": [{"document_id": 1, "content": "证据"}]}

    server = TestClient(app)

    class InProcessBridgeClient:
        def __init__(self, *args, **kwargs):
            self.headers = kwargs.get("headers") or {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def get(self, url, **kwargs):
            return server.get(url, headers=self.headers, **kwargs)

        def post(self, url, **kwargs):
            return server.post(url, headers=self.headers, **kwargs)

    monkeypatch.setattr(rag_http_client.httpx, "Client", InProcessBridgeClient)

    success = rag_http_client.retrieve(project_id=1, query="合同")
    assert success == [{"document_id": 1, "content": "证据"}]

    monkeypatch.setenv("RAG_SHARED_API_KEY", "wrong-key")
    wrong = rag_http_client.retrieve(project_id=1, query="合同")
    assert wrong["status"] == "DEGRADED"
    assert "401" in wrong["error"]

    monkeypatch.delenv("RAG_SHARED_API_KEY", raising=False)
    missing = rag_http_client.query(project_id=1, query="合同")
    assert missing["status"] == "DEGRADED"
    assert "401" in missing["error"]


def test_http_error_does_not_echo_shared_key(monkeypatch, caplog):
    """HTTP error payloads and logs must not persist bridge credentials."""
    from app.services import rag_http_client

    secret = "do-not-log-bridge-secret"
    monkeypatch.setenv("RAG_SHARED_API_KEY", secret)
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"detail": f"invalid token {secret}"},
            request=request,
        )

    _install_mock_transport(monkeypatch, rag_http_client, handler, captured)
    with caplog.at_level(logging.WARNING, logger=rag_http_client.__name__):
        result = rag_http_client.retrieve(project_id=1, query="合同")

    assert result["status"] == "DEGRADED"
    assert secret not in result["error"]
    assert secret not in caplog.text
