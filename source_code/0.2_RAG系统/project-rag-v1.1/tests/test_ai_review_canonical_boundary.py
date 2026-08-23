"""AI Review contract tests.

These tests exercise the downstream boundary without requiring a live LLM or
RAG service. A missing dependency must result in an explicit needs-review
response, never a demo score.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from ai_review.rag_client import ProjectRAGClient
from ai_review.review_service import AIReviewOutputError, AIReviewService, _serialize_facts
from facts_provider.facts_provider import MetricValue


@dataclass
class _Facts:
    project_code: str
    as_of: str
    facts_version: str
    metrics: dict


def _facts() -> _Facts:
    return _Facts(
        project_code="YB-DEMO-001",
        as_of="2026-08-20T00:00:00+00:00",
        facts_version="facts-test-v1",
        metrics={
            "real_profit": MetricValue(123.0, "2.1", "CNY"),
            "health_score": MetricValue(88.0, "1.0", "score"),
        },
    )


def _service(monkeypatch, facts: _Facts | None = None):
    service = AIReviewService(
        db=None,
        facts_provider=SimpleNamespace(get_facts=lambda **_: facts or _facts()),
    )
    monkeypatch.setattr(service, "_resolve_project_id", lambda _: 7)
    monkeypatch.setattr(
        service._snapshot_service,
        "create_snapshot",
        lambda **_: SimpleNamespace(id="snapshot-1"),
    )
    monkeypatch.setattr(
        service._run_service,
        "create_run",
        lambda **_: SimpleNamespace(id="run-1"),
    )
    completed = {}

    def complete_run(**kwargs):
        completed.update(kwargs)

    monkeypatch.setattr(service._run_service, "complete_run", complete_run)
    monkeypatch.setattr(
        service._run_service,
        "fail_run",
        lambda *args, **kwargs: pytest.fail("unexpected failure"),
    )
    return service, completed


def test_serialize_facts_does_not_call_broken_to_dict():
    facts = _facts()
    facts.to_dict = lambda: (_ for _ in ()).throw(AssertionError("must not use legacy serializer"))
    payload = _serialize_facts(facts)
    assert payload["metrics"]["real_profit"] == {
        "value": 123.0,
        "metric_version": "2.1",
        "unit": "CNY",
    }


def test_no_documentary_evidence_is_explicit_needs_review(monkeypatch):
    service, completed = _service(monkeypatch)
    called = {"llm": False}
    monkeypatch.setattr(service, "_get_rag_evidence", lambda _: [])
    monkeypatch.setattr(service, "_call_llm", lambda *_: called.__setitem__("llm", True))

    response = service.run_review("YB-DEMO-001")

    assert response["status"] == "NEEDS_REVIEW"
    assert response["result"]["risk_level"] == "UNKNOWN"
    assert response["result"]["health_score"] == 88.0
    assert response["result"]["health_score_source"] == "canonical_facts"
    assert response["result"]["needs_review"] is True
    assert called["llm"] is False
    assert completed["run_status"] == "NEEDS_REVIEW"


def test_llm_cannot_override_canonical_health_score(monkeypatch):
    service, completed = _service(monkeypatch)
    evidence = [{
        "evidence_id": "doc:1:chunk:2",
        "filename": "合同.pdf",
        "page": 3,
        "source": "合同.pdf",
        "version": "V1",
        "effective_from": "2026-01-01",
        "effective_to": None,
        "entity_code": "A01",
        "business_role": "construction",
        "score": 0.9,
        "content": "合同约定按月结算。",
    }]
    monkeypatch.setattr(service, "_get_rag_evidence", lambda _: evidence)
    monkeypatch.setattr(
        service,
        "_call_llm",
        lambda *_: json.dumps({
            "summary": "合同证据支持按月结算。",
            "health_score": 1,
            "risk_level": "LOW",
            "evidence_refs": ["doc:1:chunk:2"],
            "findings": [{
                "category": "合同",
                "description": "需要核对结算资料。",
                "evidence_refs": ["doc:1:chunk:2"],
                "suggestion": "人工复核。",
            }],
            "metric_explanations": {},
        }),
    )

    response = service.run_review("YB-DEMO-001")

    assert response["status"] == "COMPLETED"
    assert response["result"]["health_score"] == 88.0
    assert response["result"]["health_score_source"] == "canonical_facts"
    assert "health_score" not in response["result"].get("metric_explanations", {})
    assert completed["run_status"] == "COMPLETED"


def test_parser_requires_existing_evidence_reference():
    service = AIReviewService(db=None, facts_provider=SimpleNamespace())
    output = json.dumps({
        "summary": "结论",
        "risk_level": "LOW",
        "evidence_refs": ["missing"],
        "findings": [],
        "metric_explanations": {},
    })
    with pytest.raises(AIReviewOutputError, match="不存在"):
        service._parse_llm_result(output, valid_evidence_ids={"doc:1:chunk:1"})


def test_rag_evidence_rejects_role_as_entity_code():
    client = ProjectRAGClient("YB-DEMO-001")
    result = client.format_evidence([
        {"entity_code": "A", "filename": "bad.pdf", "text": "不应使用角色作为主体"},
        {"entity_code": "A01", "filename": "good.pdf", "text": "真实主体证据"},
    ])
    assert len(result) == 1
    assert result[0]["entity_code"] == "A01"
