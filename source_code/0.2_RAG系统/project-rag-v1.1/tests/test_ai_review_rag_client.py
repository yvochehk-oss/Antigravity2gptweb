"""AI Review ↔ RAG bridge type-boundary tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ai_review import rag_client
from ai_review.rag_client import (
    ProjectRAGClient,
    RAGBridgeProtocolError,
    RAGBridgeUnavailableError,
)


def test_format_evidence_rejects_degraded_payload_explicitly():
    """A degraded dict is not an evidence list and must never be iterated."""
    client = ProjectRAGClient("YB-DEMO-001")

    with pytest.raises(RAGBridgeUnavailableError) as caught:
        client.format_evidence({
            "status": "DEGRADED",
            "error": "rag_http_client HTTP 401",
        })

    assert caught.value.status == "DEGRADED"
    assert caught.value.needs_review is True
    assert caught.value.operation == "format_evidence"
    assert "401" in str(caught.value)


def test_format_evidence_rejects_other_mapping_shape_without_attribute_error():
    """Unexpected response shapes fail as an explicit protocol error."""
    client = ProjectRAGClient("YB-DEMO-001")

    with pytest.raises(RAGBridgeProtocolError, match="expected list"):
        client.format_evidence({"results": []})


def test_project_id_surfaces_list_projects_degraded_response(monkeypatch):
    """list_projects transport failure must not become a not-found success."""
    monkeypatch.setattr(
        rag_client,
        "rag_list_projects",
        lambda: {"status": "DEGRADED", "error": "rag_http_client HTTP 503"},
    )
    client = ProjectRAGClient("P-001")

    with pytest.raises(RAGBridgeUnavailableError) as caught:
        client.retrieve_evidence("合同")

    assert caught.value.operation == "list_projects"
    assert caught.value.status == "DEGRADED"
    assert caught.value.needs_review is True


def test_project_id_maps_real_bridge_transport_failure_to_explicit_error(monkeypatch):
    """The live bridge failure payload follows the same boundary contract."""
    from app.services import rag_http_client

    monkeypatch.setattr(rag_http_client, "DEFAULT_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setattr(rag_http_client, "DEFAULT_TIMEOUT", 0.1)
    monkeypatch.setattr(rag_client, "rag_list_projects", rag_http_client.list_projects)
    client = ProjectRAGClient("P-001")

    with pytest.raises(RAGBridgeUnavailableError) as caught:
        client.retrieve_evidence("合同")

    assert caught.value.operation == "list_projects"
    assert caught.value.status == "DEGRADED"
    assert "rag_http_client" in str(caught.value)


def test_retrieve_evidence_surfaces_degraded_response_after_project_resolution(monkeypatch):
    """retrieve's degraded dict must remain an explicit unavailable state."""
    monkeypatch.setattr(
        rag_client,
        "rag_list_projects",
        lambda: [{"id": 7, "project_code": "P-001"}],
    )
    monkeypatch.setattr(
        rag_client,
        "rag_retrieve",
        lambda **_: {"status": "DEGRADED", "error": "rag_http_client HTTP 401"},
    )
    client = ProjectRAGClient("P-001")

    with pytest.raises(RAGBridgeUnavailableError) as caught:
        client.retrieve_evidence("合同")

    assert caught.value.operation == "retrieve"
    assert caught.value.response_status == "DEGRADED"
    assert caught.value.needs_review is True


def test_review_service_records_bridge_degraded_as_manual_review_signal(monkeypatch):
    """The review boundary catches the explicit error and records its reason."""
    from ai_review.review_service import AIReviewService

    monkeypatch.setattr(
        rag_client,
        "rag_list_projects",
        lambda: {"status": "DEGRADED", "error": "rag_http_client HTTP 503"},
    )
    service = AIReviewService(
        db=None,
        facts_provider=SimpleNamespace(),
    )

    assert service._get_rag_evidence("P-001") == []
    assert len(service._last_rag_errors) == 5
    assert all("DEGRADED" in error for error in service._last_rag_errors)
    assert all("list_projects" in error for error in service._last_rag_errors)


def test_successful_list_contract_still_formats_evidence(monkeypatch):
    """Normal list responses retain the existing citation-bearing contract."""
    monkeypatch.setattr(
        rag_client,
        "rag_list_projects",
        lambda: [{"id": 7, "project_code": "P-001"}],
    )
    monkeypatch.setattr(
        rag_client,
        "rag_retrieve",
        lambda **_: [{
            "document_id": 10,
            "chunk_id": 3,
            "filename": "合同.pdf",
            "page_start": 2,
            "source": "合同.pdf",
            "content": "按月结算。",
        }],
    )
    client = ProjectRAGClient("P-001")

    evidence = client.retrieve_evidence("合同")
    formatted = client.format_evidence(evidence)

    assert formatted[0]["evidence_id"] == "doc:10:chunk:3"
    assert formatted[0]["content"] == "按月结算。"
