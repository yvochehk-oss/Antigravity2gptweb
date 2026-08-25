"""Regression tests for the V2.0 document path migration preflight."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest

RAG_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = RAG_ROOT / "alembic" / "versions" / "012_document_storage_paths.py"


def _migration_module():
    spec = importlib.util.spec_from_file_location("document_path_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _Connection:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, statement):
        del statement
        return _Result(self.rows)


def test_plan_preflights_a_real_bundle_document_without_string_replacement():
    """The target paths are rebuilt from project/document identifiers."""

    migration = _migration_module()
    materials = migration._v2_root() / "project_materials"
    source = materials / "CD-TF-001" / "DOC-CF61DD37BED3" / "发票_OUT-IN-TF-B01_2.pdf"
    assert source.is_file()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    row = {
        "id": 1,
        "project_code": "CD-TF-001",
        "document_code": "DOC-CF61DD37BED3",
        "filename": source.name,
        "original_path": "/legacy/project-rag/data/originals/old.pdf",
        "parsed_dir": "/legacy/project-rag/data/parsed/DOC-CF61DD37BED3",
        "markdown_path": "/legacy/project-rag/data/parsed/DOC-CF61DD37BED3/old.md",
        "content_list_path": "/legacy/project-rag/data/parsed/DOC-CF61DD37BED3/old_content_list.json",
        "file_hash": digest,
        "size_bytes": source.stat().st_size,
    }

    plan = migration._plan_paths(_Connection([row]))
    assert len(plan) == 1
    item = plan[0]
    assert item["new_original_path"] == str(source.resolve())
    assert item["new_parsed_dir"].startswith(str(migration._v2_root()))
    assert item["new_markdown_path"].endswith("发票_OUT-IN-TF-B01_2.md")
    assert item["new_content_list_path"].endswith(
        "发票_OUT-IN-TF-B01_2_content_list.json"
    )


def test_plan_rejects_a_target_outside_the_approved_v2_root(monkeypatch, tmp_path):
    migration = _migration_module()
    monkeypatch.setenv("PROJECT_RAG_PROJECT_MATERIALS_ROOT", str(tmp_path))
    with pytest.raises(RuntimeError, match="approved V2.0 root"):
        migration._plan_paths(_Connection([]))


def test_safe_component_rejects_path_traversal_metadata():
    migration = _migration_module()
    with pytest.raises(RuntimeError, match="unsafe filename"):
        migration._safe_component("../outside.pdf", "filename")
