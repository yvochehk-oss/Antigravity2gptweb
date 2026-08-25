"""AI Review evidence-pack persistence and schema-boundary tests."""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_review.evidence_pack_service import RAGEvidencePackService
from ai_review.models import AIReviewRun, RAGEvidencePack
from ai_review.review_service import AIReviewService
from facts_provider.facts_provider import MetricValue

RAG_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = RAG_ROOT / "alembic" / "versions" / "013_ai_review_evidence_pack_fk.py"


class _FlushOnlyDb:
    def __init__(self):
        self.added = []

    def add(self, value):
        self.added.append(value)

    def flush(self):
        return None


def test_model_keeps_legacy_nullable_column_but_adds_canonical_fk():
    column = AIReviewRun.__table__.c.rag_evidence_pack_id
    assert column.nullable is True
    foreign_keys = list(column.foreign_keys)
    assert len(foreign_keys) == 1
    assert foreign_keys[0].target_fullname == "rag_evidence_packs.id"
    assert foreign_keys[0].ondelete == "SET NULL"
    assert RAGEvidencePack.__table__.c.evidence_data.nullable is False


def test_evidence_pack_service_persists_empty_state_without_fake_evidence():
    db = _FlushOnlyDb()
    pack = RAGEvidencePackService(db).create_pack(
        project_id=7,
        project_code="YB-DEMO-001",
        query="AI Review documentary evidence",
        evidence=[],
        status="DEGRADED",
        extra_metadata={"retrieval_errors": ["HTTP 503"]},
    )

    assert db.added == [pack]
    assert pack.status == "DEGRADED"
    assert pack.evidence_count == 0
    assert pack.evidence_data == []
    assert pack.extra_metadata == {"retrieval_errors": ["HTTP 503"]}


def test_evidence_pack_service_rejects_unknown_status():
    with pytest.raises(ValueError, match="unsupported evidence pack status"):
        RAGEvidencePackService(_FlushOnlyDb()).create_pack(
            project_id=7,
            project_code="YB-DEMO-001",
            query="query",
            status="UNKNOWN",
        )


@dataclass
class _Facts:
    project_code: str = "YB-DEMO-001"
    as_of: str = "2026-08-20T00:00:00+00:00"
    facts_version: str = "facts-test-v1"
    metrics: dict = None
    entity_code: str = "A01"
    entity_mapping_status: str = "VALID"
    entity_mapping_reason: str | None = None
    entity_mapping_valid: bool = True

    def __post_init__(self):
        if self.metrics is None:
            self.metrics = {"real_profit": MetricValue(123.0, "2.1", "CNY")}


def test_review_passes_persisted_pack_id_to_run(monkeypatch):
    service = AIReviewService(
        db=None,
        facts_provider=SimpleNamespace(get_facts=lambda **_: _Facts()),
    )
    monkeypatch.setattr(service, "_resolve_project_id", lambda _: 7)
    monkeypatch.setattr(
        service._snapshot_service,
        "create_snapshot",
        lambda **_: SimpleNamespace(id="snapshot-1"),
    )
    monkeypatch.setattr(
        service,
        "_get_rag_evidence",
        lambda _: [{"evidence_id": "doc:1:chunk:1", "content": "真实证据", "filename": "x.pdf"}],
    )
    monkeypatch.setattr(
        service,
        "_call_llm",
        lambda *_: json.dumps({
            "summary": "证据支持人工复核。",
            "risk_level": "LOW",
            "evidence_refs": ["doc:1:chunk:1"],
            "findings": [],
            "metric_explanations": {},
        }),
    )
    created_pack = SimpleNamespace(id="pack-1")
    monkeypatch.setattr(service._evidence_pack_service, "create_pack", lambda **_: created_pack)
    run_kwargs = {}

    def create_run(**kwargs):
        run_kwargs.update(kwargs)
        return SimpleNamespace(id="run-1")

    monkeypatch.setattr(service._run_service, "create_run", create_run)
    monkeypatch.setattr(service._run_service, "complete_run", lambda **_: None)
    monkeypatch.setattr(service._run_service, "fail_run", lambda *args, **kwargs: pytest.fail("unexpected failure"))

    response = service.run_review("YB-DEMO-001")

    assert response["run_id"] == "run-1"
    assert run_kwargs["rag_evidence_pack_id"] == "pack-1"


def test_migration_is_next_head_and_keeps_nullable_fk_contract():
    spec = importlib.util.spec_from_file_location("evidence_pack_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "013_ai_review_evidence_pack_fk"
    assert module.down_revision == "012_document_storage_paths"
    assert "nullable" in MIGRATION_PATH.read_text(encoding="utf-8")
    assert "ondelete=\"SET NULL\"" in MIGRATION_PATH.read_text(encoding="utf-8")
