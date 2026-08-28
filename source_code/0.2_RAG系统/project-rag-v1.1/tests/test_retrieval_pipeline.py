"""Regression tests for the V0.3 adaptive retrieval contracts.

These tests stub the storage search itself so they exercise the orchestration
boundary without requiring a running PostgreSQL/pgvector service or an LLM.
"""
from types import SimpleNamespace

import pytest

import app.services.retrieval as retrieval
from app.services import query_rewrite
from app.services.quality_gate import assess_quality


class _FakeDB:
    """Small QueryLog-compatible session used by orchestration tests."""

    def __init__(self):
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _candidate(entity_code="A01"):
    return {
        "score": 0.9,
        "chunk_id": 1,
        "document_id": 10,
        "document_code": "DOC-1",
        "filename": "合同.md",
        "entity_code": entity_code,
        "business_role": "construction",
        "business_category": "construction",
        "heading_path": "第七条",
        "text": "合同结算资料",
    }


def test_adaptive_pipeline_uses_actual_helper_signatures(monkeypatch):
    db = _FakeDB()
    calls = []

    def rewrite_query(query, filters, project_meta=None):
        calls.append(("rewrite", query, filters, project_meta))
        return query_rewrite.RewriteResult(
            intent="合同条款",
            rewritten_query="合同结算资料",
            filters={"business_role": "A"},
            keywords=["合同"],
            confidence=0.8,
        )

    def hyde_generate(query, context_chunks=None, max_hints=3):
        calls.append(("hyde", query, context_chunks, max_hints))
        return query_rewrite.HyDEResult(
            hypothetical_text="工程合同结算资料",
            helper_used=True,
        )

    helper = SimpleNamespace(
        rewrite_query=rewrite_query,
        hyde_generate=hyde_generate,
    )
    target_module = getattr(retrieval, "legacy", retrieval)
    monkeypatch.setattr(target_module, "ENABLE_QUERY_REWRITE", True)
    monkeypatch.setattr(target_module, "ENABLE_HYDE", True)
    monkeypatch.setattr(target_module, "_get_query_rewrite_module", lambda: helper)
    monkeypatch.setattr(
        target_module,
        "_hybrid_search",
        lambda *args, **kwargs: [{**_candidate(), "score": 0.0}],
    )

    result = retrieval.retrieve(
        db,
        project_id=1,
        query="合同怎么结算",
        filters={"entity_code": "A01"},
        top_k=3,
        use_rerank=False,
        rewrite=True,
        hyde=True,
    )

    assert result["rewrite_used"] is True
    assert result["hyde_used"] is True
    assert result["pipeline_errors"] == []
    assert calls[0] == (
        "rewrite",
        "合同怎么结算",
        {"entity_code": ["A01"]},
        None,
    )
    assert calls[1][0] == "hyde"
    assert calls[1][2][0]["chunk_id"] == 1
    assert calls[1][3] == 3
    assert result["effective_filters"]["entity_code"] == ["A01"]


def test_adaptive_helper_failure_is_explicit(monkeypatch):
    db = _FakeDB()

    def broken_rewrite(*args, **kwargs):
        raise RuntimeError("rewrite contract exploded")

    helper = SimpleNamespace(rewrite_query=broken_rewrite)
    target_module = getattr(retrieval, "legacy", retrieval)
    monkeypatch.setattr(target_module, "ENABLE_QUERY_REWRITE", True)
    monkeypatch.setattr(target_module, "ENABLE_HYDE", True)
    monkeypatch.setattr(target_module, "_get_query_rewrite_module", lambda: helper)
    monkeypatch.setattr(target_module, "_hybrid_search", lambda *args, **kwargs: [])

    result = retrieval.retrieve(
        db,
        project_id=1,
        query="缺失资料",
        filters={"entity_code": "A01"},
        top_k=3,
        use_rerank=False,
        rewrite=True,
    )

    assert result["results"] == []
    assert result["rewrite_result"]["status"] == "ERROR"
    assert any(x["step"] == "query_rewrite" for x in result["pipeline_errors"])
    assert result["quality_gate"]["status"] == "INSUFFICIENT_EVIDENCE"


def test_virtual_entity_filter_is_rejected_before_search(monkeypatch):
    monkeypatch.setattr(
        retrieval,
        "_hybrid_search",
        lambda *args, **kwargs: pytest.fail("invalid filter must not reach search"),
    )
    with pytest.raises(ValueError, match="canonical"):
        retrieval.retrieve(
            _FakeDB(),
            project_id=1,
            query="合同",
            filters={"entity_code": "A"},
            use_rerank=False,
        )


def test_quality_gate_normalizes_rrf_scores_and_role_aliases():
    gate = assess_quality(
        [
            {
                "score": 0.016,
                "document_id": 1,
                "entity_code": "A01",
                "business_role": "construction",
            },
            {
                "score": 0.008,
                "document_id": 2,
                "entity_code": "A02",
                "business_role": "construction",
            },
        ],
        {"business_role": ["A"]},
    )
    assert gate.top1_score == 1.0
    assert gate.metadata_match_count == 2
    assert gate.status == "GOOD"
