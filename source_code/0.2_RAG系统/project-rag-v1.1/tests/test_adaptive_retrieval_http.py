"""HTTP contract tests for the authenticated adaptive retrieval routes."""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import TaxPrincipal, require_auth
from app.routers import adaptive_retrieval


class _FakeDB:
    def __init__(self):
        self.project = SimpleNamespace(id=7, project_code="P-007")

    def get(self, model, project_id):
        return self.project if project_id == self.project.id else None

    def scalar(self, stmt):
        return self.project


def _client(monkeypatch, retrieve):
    app = FastAPI()
    app.include_router(adaptive_retrieval.router)
    app.dependency_overrides[require_auth] = lambda: TaxPrincipal(
        {"sub": "1", "username": "operator", "role": "operator"}
    )
    db = _FakeDB()

    @contextmanager
    def fake_get_db():
        yield db

    monkeypatch.setattr(adaptive_retrieval, "get_db", fake_get_db)
    monkeypatch.setattr(adaptive_retrieval.retrieval_service, "retrieve", retrieve)
    monkeypatch.setattr(
        adaptive_retrieval,
        "answer_with_evidence",
        lambda query, evidence: SimpleNamespace(
            answer="依据证据回答",
            citations_used=[1] if evidence else [],
            faithful=bool(evidence),
            no_answer_confidence=0.0 if evidence else 0.95,
        ),
    )
    return app, db


def _result():
    return {
        "results": [
            {
                "chunk_id": 11,
                "document_id": 21,
                "filename": "合同.md",
                "text": "合同结算条款",
                "score": 0.91,
                "entity_code": "A01",
                "business_role": "construction",
            }
        ],
        "rewrite_used": True,
        "hyde_used": False,
        "deep_mode": False,
        "rewrite_result": {"status": "OK"},
        "hyde_result": None,
        "quality_gate": {"status": "PARTIAL", "score": 0.7},
        "pipeline_errors": [],
        "reranker_status": "OK",
        "retrieval_status": "PARTIAL",
        "effective_filters": {"entity_code": ["A01"]},
        "search_diagnostics": {},
        "latency_ms": 4,
        "bm25_candidates": [11],
        "vector_candidates": [11],
    }


def test_adaptive_route_requires_tax_jwt(monkeypatch):
    app = FastAPI()
    app.include_router(adaptive_retrieval.router)
    response = TestClient(app).post(
        "/api/v1/retrieve/adaptive",
        json={"project_id": 7, "query": "合同结算", "answer": False},
    )
    assert response.status_code == 401


def test_adaptive_route_rejects_unknown_tax_role(monkeypatch):
    app = FastAPI()
    app.include_router(adaptive_retrieval.router)
    app.dependency_overrides[require_auth] = lambda: TaxPrincipal(
        {"sub": "1", "username": "viewer", "role": "viewer"}
    )
    response = TestClient(app).post(
        "/api/v1/retrieve/adaptive",
        json={"project_id": 7, "query": "合同结算", "answer": False},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "RETRIEVAL_ROLE_FORBIDDEN"


def test_adaptive_route_passes_named_pipeline_options_and_status(monkeypatch):
    calls = []

    def fake_retrieve(**kwargs):
        calls.append(kwargs)
        return _result()

    app, _ = _client(monkeypatch, fake_retrieve)
    response = TestClient(app).post(
        "/api/v1/retrieve/adaptive",
        json={
            "project_code": "P-007",
            "query": "合同结算",
            "filters": {"entity_code": "A01"},
            "top_k": 3,
            "rerank": False,
            "rewrite": True,
            "hyde": False,
            "answer": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["project_id"] == 7
    assert body["retrieval_status"] == "PARTIAL"
    assert body["status"] == "PARTIAL"
    assert calls == [
        {
            "db": calls[0]["db"],
            "project_id": 7,
            "query": "合同结算",
            "filters": {"entity_code": "A01"},
            "top_k": 3,
            "use_rerank": False,
            "rewrite": True,
            "hyde": False,
            "deep": False,
        }
    ]


def test_deep_route_exposes_pipeline_errors_and_answer(monkeypatch):
    def fake_retrieve(**kwargs):
        assert kwargs["rewrite"] is True
        assert kwargs["hyde"] is True
        assert kwargs["deep"] is True
        result = _result()
        result["pipeline_errors"] = [
            {"step": "hyde", "code": "UNAVAILABLE", "message": "LLM unavailable"}
        ]
        result["retrieval_status"] = "DEGRADED"
        return result

    app, _ = _client(monkeypatch, fake_retrieve)
    response = TestClient(app).post(
        "/api/v1/retrieve/deep",
        json={"project_id": 7, "query": "合同结算", "generate_answer": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "DEGRADED"
    assert body["pipeline_errors"][0]["step"] == "hyde"
    assert body["answer"] == "依据证据回答"


def test_explain_route_is_available_and_keeps_pipeline_status(monkeypatch):
    app, _ = _client(monkeypatch, lambda **kwargs: _result())
    response = TestClient(app).post(
        "/api/v1/retrieve/explain",
        json={"project_id": 7, "query": "合同结算", "answer": False},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["project_id"] == 7
    assert body["items"][0]["chunk_id"] == 11
    assert body["retrieval_status"] == "PARTIAL"


def test_adaptive_route_rejects_unsupported_stream_and_history(monkeypatch):
    app, _ = _client(monkeypatch, lambda **kwargs: _result())
    client = TestClient(app)
    for field in ({"stream": True}, {"history": [{"role": "user", "content": "前文"}]}):
        response = client.post(
            "/api/v1/retrieve/adaptive",
            json={"project_id": 7, "query": "合同结算", "answer": False, **field},
        )
        assert response.status_code == 422
