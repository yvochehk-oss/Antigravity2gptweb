"""Test that all models are registered to a single shared Base.metadata.

Verifies that ai_review models use the same Base as app.models,
preventing dual-Base issues and orphaned foreign keys.
"""
import os
import tempfile

import pytest

# Use a temp DB for this test
tmp = tempfile.mkdtemp(prefix="rag-model-reg-test-")
os.environ["PROJECT_RAG_DATA_DIR"] = tmp
os.environ["PROJECT_RAG_DB_URL"] = f"sqlite:///{tmp}/test_models.db"
os.environ["PROJECT_RAG_EMBEDDING_BACKEND"] = "hash_v1"
os.environ["PROJECT_RAG_AUTO_START_WORKER"] = "0"

from app.db import Base, engine, init_db


def test_all_models_share_same_base():
    """All models must register to a single shared Base.metadata."""
    # Trigger model imports by calling init_db
    init_db()

    # Import models to register them
    from app import models as app_models  # noqa: F401
    from ai_review import models as ai_review_models  # noqa: F401

    app_table_names = set(Base.metadata.tables.keys())

    # Verify app.models tables are present
    expected_app_tables = {
        "projects", "documents", "chunks", "ingest_jobs", "entities",
        "query_logs", "benchmark_questions", "benchmark_runs",
        "backup_records", "query_feedback", "knowledge_conflicts",
        "regulations", "regulation_articles", "regulation_chunks",
    }
    assert expected_app_tables.issubset(app_table_names), (
        f"Missing app tables: {expected_app_tables - app_table_names}"
    )

    # Verify ai_review tables are in the SAME Base.metadata
    expected_ai_review_tables = {
        "facts_snapshots", "ai_review_runs", "metric_versions",
    }
    assert expected_ai_review_tables.issubset(app_table_names), (
        f"Missing ai_review tables: {expected_ai_review_tables - app_table_names}"
    )

    # Verify no duplicate tables across different metadata
    all_tables = set(Base.metadata.tables.values())
    assert len(all_tables) == len(app_table_names), (
        "Duplicate table entries detected in Base.metadata"
    )


def test_ai_review_base_is_shared():
    """ai_review.db.Base must be the same object as app.db.Base."""
    from app.db import Base as app_Base
    from ai_review.db import Base as ai_review_Base

    assert app_Base is ai_review_Base, (
        "ai_review.db.Base is not the same object as app.db.Base. "
        "This causes dual-Base issues where foreign keys cannot resolve."
    )


def test_ai_review_tables_have_foreign_keys_resolved():
    """ai_review tables with FK to projects must have the dependency registered."""
    init_db()

    from ai_review import models  # noqa: F401

    # Get the ai_review models with FK references
    ai_review_fk_tables = {
        "facts_snapshots": "projects",
        "ai_review_runs": "projects",
    }

    for table_name, referenced_table in ai_review_fk_tables.items():
        assert table_name in Base.metadata.tables, (
            f"ai_review table '{table_name}' not found in Base.metadata"
        )
        table = Base.metadata.tables[table_name]
        fk_columns = {
            fk.column.name for fk in table.foreign_keys
        }
        assert referenced_table in Base.metadata.tables, (
            f"Referenced table '{referenced_table}' not in Base.metadata. "
            f"FK from '{table_name}' cannot be resolved."
        )


def test_fresh_db_has_all_tables():
    """A fresh database created via Base.metadata.create_all has all expected tables."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "fresh_test.db"
        from sqlalchemy import create_engine

        fresh_engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(fresh_engine)

        # Verify tables exist
        from sqlalchemy import inspect
        inspector = inspect(fresh_engine)
        actual_tables = set(inspector.get_table_names())

        expected_tables = {
            # app models
            "projects", "documents", "chunks", "ingest_jobs", "entities",
            "query_logs", "benchmark_questions", "benchmark_runs",
            "backup_records", "query_feedback", "knowledge_conflicts",
            "regulations", "regulation_articles", "regulation_chunks",
            # ai_review models
            "facts_snapshots", "ai_review_runs", "metric_versions",
        }

        missing = expected_tables - actual_tables
        assert not missing, f"Missing tables after fresh create_all: {missing}"

        # Verify no orphaned tables
        unexpected = actual_tables - expected_tables
        if unexpected:
            # alembic_version is expected if migrations were run
            unexpected.discard("alembic_version")
        assert not unexpected, f"Unexpected tables: {unexpected}"
