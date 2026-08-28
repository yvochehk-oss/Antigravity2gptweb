"""Regression coverage for project entity fallback during ingestion."""

from app.models import Document, Project
from app.services import ingest


class _FakeDB:
    def __init__(self, project):
        self.project = project
        self.added_chunks = []

    def get(self, model, object_id):
        assert model is Project
        return self.project if object_id == self.project.id else None

    def execute(self, _statement):
        return type("DeleteResult", (), {"rowcount": 0})()

    def add_all(self, chunks):
        self.added_chunks.extend(chunks)

    def scalar(self, _statement):
        return None

    def commit(self):
        return None

    def rollback(self):
        return None

    def refresh(self, _document):
        return None


def test_mineru_ingest_falls_back_to_project_entity_code(monkeypatch, tmp_path):
    """A document without an entity inherits its project's canonical code."""
    source = tmp_path / "contract.pdf"
    source.write_bytes(b"%PDF-1.7")
    project = Project(
        id=7,
        project_code="P-INGEST",
        name="Ingest regression project",
        contract_amount=0,
        location="成都",
        entity_code="A01",
    )
    document = Document(
        id=11,
        project_id=project.id,
        document_code="DOC-INGEST",
        filename=source.name,
        file_type="pdf",
        file_hash="a" * 64,
        original_path=str(source),
        document_type="contract",
        entity_code="",
    )

    monkeypatch.setattr(ingest, "detect_encrypted_pdf", lambda _path: False)
    monkeypatch.setattr(
        ingest,
        "parse_with_mineru",
        lambda _document_code, _path: {
            "output_dir": str(tmp_path / "mineru"),
            "markdown_path": "",
            "content_list_path": str(tmp_path / "content.json"),
        },
    )
    monkeypatch.setattr(
        ingest,
        "chunks_from_content_list",
        lambda _path: [{"content": "合同正文", "heading_path": "", "token_estimate": 2}],
    )
    monkeypatch.setattr(ingest, "assess_parse_quality", lambda *_args: {"score": 100, "flags": []})
    monkeypatch.setattr(ingest, "refine_from_content", lambda *_args: {})
    monkeypatch.setattr(ingest, "embed_many", lambda _texts: [[0.1]])

    result = ingest.parse_and_index(_FakeDB(project), document)

    assert result.parse_status == "INDEXED"
    assert result.entity_code == "A01"
