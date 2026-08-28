"""Regression guards for PostgreSQL indexed lexical retrieval."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_postgres_lexical_path_is_indexed_and_never_scans_corpus_in_python():
    source = (ROOT / "app" / "services" / "indexed_retrieval.py").read_text(encoding="utf-8")
    migration = (ROOT / "alembic" / "versions" / "018_lexical_trigram_index.py").read_text(encoding="utf-8")
    package = (ROOT / "app" / "services" / "__init__.py").read_text(encoding="utf-8")

    assert "gin_trgm_ops" in migration
    assert "CREATE EXTENSION IF NOT EXISTS pg_trgm" in migration
    assert "Chunk.search_text.ilike" in source
    assert 'diagnostics["lexical_backend"] = "postgres_trigram"' in source
    assert "db.execute(base).all()" not in source
    assert "python_bm25" not in source
    assert "vector_only" in source
    assert "indexed_retrieval as retrieval" in package


def test_chinese_terms_use_trigram_shingles():
    source = (ROOT / "app" / "services" / "indexed_retrieval.py").read_text(encoding="utf-8")
    assert "run[i : i + 3]" in source
    assert "limit: int = 8" in source
