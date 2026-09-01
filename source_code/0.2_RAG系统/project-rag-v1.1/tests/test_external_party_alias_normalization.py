"""Regression tests for canonical external-party alias handling."""

from pathlib import Path
from contextlib import contextmanager
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.domain.entities import (
    get_external_preset,
    map_to_standard_external_code,
)
from app.services.metadata import (
    infer_from_filename,
    refine_from_content,
    resolve_entity_reference,
)
from app.services.documents import register_bytes
from app.services.ingest import _auto_register_external_party
from app.models import ExternalParty, Document, Project
from app.legacy_routes import app, api_patch_metadata
from app.schemas import DocumentMetadataPatch


def test_ext_cq_maps_to_ed() -> None:
    assert map_to_standard_external_code("EXT-CQ") == "ED"
    assert map_to_standard_external_code(" ext-cq ") == "ED"
    assert map_to_standard_external_code("EXT-CQ-HEAVY-CRANE") == "ED"
    assert map_to_standard_external_code("EXT-CRANE") == "ED"
    assert get_external_preset("EXT-CQ")["code"] == "ED"


def test_preset_alias_wins_over_stale_runtime_row() -> None:
    stale_cache = [
        {
            "entity_code": "EXT-CQ",
            "name": "重庆巨力重型起重设备吊装公司",
            "short_name": "重庆巨力吊装",
            "business_role": "owner",
            "entity_kind": "external",
            "legal_entity": True,
            "status": "active",
        },
        {
            "entity_code": "ED",
            "name": "重庆巨力重型起重设备吊装公司",
            "short_name": "重庆巨力吊装",
            "business_role": "equipment",
            "entity_kind": "external",
            "legal_entity": True,
            "status": "active",
        },
    ]

    resolved = resolve_entity_reference("EXT-CQ", canonical_cache=stale_cache)

    assert resolved["status"] == "RESOLVED"
    assert resolved["entity_code"] == "ED"
    assert resolved["business_role"] == "equipment"


def test_filename_alias_is_emitted_as_canonical_counterparty() -> None:
    inferred = infer_from_filename(
        "TAX_CERT_EXT-CQ_2024Q3_完税证明.pdf",
        canonical_cache=[],
    )

    assert inferred["counterparty_code"] == "ED"
    assert inferred["counterparty_resolution_status"] == "RESOLVED"


def test_refine_normalizes_explicit_alias_even_without_content() -> None:
    refined = refine_from_content(
        {"counterparty_code": "EXT-CQ", "document_type": "other"},
        "",
        canonical_cache=[],
    )

    assert refined["counterparty_code"] == "ED"


def test_api_create_external_party_rejects_alias() -> None:
    client = TestClient(app)
    resp = client.post(
        "/api/v1/external-parties",
        json={"code": "EXT-CQ", "name": "重庆巨力", "kind": "equipment"},
    )
    assert resp.status_code == 409
    assert "alias of canonical external party ED" in resp.json()["detail"]


def test_auto_register_does_not_create_ext_cq() -> None:
    created_parties = []

    class _MockDB:
        def scalar(self, stmt):
            return None
        def add(self, entity):
            created_parties.append(entity)
        def flush(self):
            pass

    mock_db = _MockDB()
    _auto_register_external_party(
        mock_db,
        counterparty_code="EXT-CQ",
        counterparty_name="重庆巨力重型起重设备吊装公司",
        tax_id=None,
        kind="equipment",
    )

    assert len(created_parties) == 1
    assert created_parties[0].code == "ED"
    assert created_parties[0].code != "EXT-CQ"


def test_api_patch_metadata_normalizes_alias(monkeypatch) -> None:
    doc = Document(id=99, project_id=1, counterparty_code="OLD")

    class _MockDB:
        def get(self, model, doc_id):
            return doc if doc_id == 99 else None
        def commit(self):
            pass
        def refresh(self, obj):
            pass

    @contextmanager
    def _mock_get_db():
        yield _MockDB()

    monkeypatch.setattr("app.legacy_routes.get_db", _mock_get_db)

    patch = DocumentMetadataPatch(counterparty_code="EXT-CQ")
    res = api_patch_metadata(99, patch)
    assert res["counterparty_code"] == "ED"
    assert doc.counterparty_code == "ED"


def test_api_patch_metadata_missing_document_returns_404(monkeypatch) -> None:
    class _MockDB:
        def get(self, model, doc_id):
            return None

    @contextmanager
    def _mock_get_db():
        yield _MockDB()

    monkeypatch.setattr("app.legacy_routes.get_db", _mock_get_db)

    patch = DocumentMetadataPatch(counterparty_code="EXT-CQ")
    with pytest.raises(HTTPException) as exc_info:
        api_patch_metadata(404404, patch)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "document not found"


def test_register_bytes_initial_commit_normalizes_to_ed(monkeypatch) -> None:
    project = Project(id=10, project_code="TEST-PROJ-10", name="测试项目")
    saved_docs = []

    class _MockDB:
        def scalar(self, stmt):
            return None
        def add(self, entity):
            if isinstance(entity, Document):
                saved_docs.append(entity)
        def commit(self):
            pass
        def flush(self):
            pass
        def refresh(self, obj):
            obj.id = 101

    mock_db = _MockDB()
    monkeypatch.setattr("app.services.documents.validate_safe_directory", lambda p: None)
    monkeypatch.setattr("app.services.documents.save_original", lambda pcode, dcode, fn, data: Path(f"/tmp/{fn}"))
    monkeypatch.setattr("app.services.documents.enqueue_parse", lambda doc_id: None)

    content = b"%PDF-1.4\n%TEST-CONTENT-FOR-EXT-CQ\n%%EOF"
    filename = "TAX_CERT_EXT-CQ_2024Q3_完税证明.pdf"

    doc, _ = register_bytes(
        db=mock_db,
        project=project,
        data=content,
        filename=filename,
        metadata={"counterparty_code": "EXT-CQ"},
    )

    assert doc.counterparty_code == "ED"
    assert len(saved_docs) >= 1
    assert saved_docs[0].counterparty_code == "ED"
