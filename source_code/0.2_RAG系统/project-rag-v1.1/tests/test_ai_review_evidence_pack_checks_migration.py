"""Static contract tests for the post-013 evidence-pack repair migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path

RAG_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    RAG_ROOT
    / "alembic"
    / "versions"
    / "014_ai_review_evidence_pack_checks.py"
)


def _migration_module():
    spec = importlib.util.spec_from_file_location(
        "evidence_pack_checks_migration", MIGRATION_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATION_TEXT = MIGRATION_PATH.read_text(encoding="utf-8")


def test_migration_is_chained_after_evidence_pack_fk():
    migration = _migration_module()

    assert migration.revision == "014_ai_review_evidence_pack_checks"
    assert migration.down_revision == "013_ai_review_evidence_pack_fk"
    assert migration._TABLE == "rag_evidence_packs"
    assert set(migration._CHECKS) == {
        "ck_rag_evidence_packs_status",
        "ck_rag_evidence_packs_evidence_count",
    }


def test_migration_checks_are_fail_closed():
    assert "status NOT IN" in MIGRATION_TEXT
    assert "evidence_count < 0" in MIGRATION_TEXT
    assert "create_check_constraint" in MIGRATION_TEXT
    assert "drop_constraint" in MIGRATION_TEXT
