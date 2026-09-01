"""Static/metadata contract tests for V3 Task 05 Party schema."""
from __future__ import annotations

from pathlib import Path

import app.models  # noqa: F401
import app.v3_party_models  # noqa: F401
from app.db import Base

ROOT = Path(__file__).resolve().parents[1]


def test_v3_party_tables_share_existing_base():
    expected = {
        "source_documents",
        "parties",
        "internal_entities",
        "party_identifiers",
        "party_tax_profiles",
    }
    assert expected <= set(Base.metadata.tables)


def test_external_party_has_nullable_party_bridge():
    table = Base.metadata.tables["external_parties"]
    assert "party_id" in table.c
    assert table.c.party_id.nullable is True
    assert "industry" in table.c


def test_party_identifier_value_is_not_globally_unique():
    table = Base.metadata.tables["party_identifiers"]
    uniques = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if getattr(constraint, "columns", None) is not None
        and constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("identifier_value",) not in uniques
    assert ("party_id", "identifier_type", "identifier_value") in uniques


def test_migration_chain_uses_real_revision_73_then_74_75():
    migration_74 = (
        ROOT / "alembic" / "versions" / "74_v3_party_source_documents.py"
    ).read_text(encoding="utf-8")
    migration_75 = (
        ROOT / "alembic" / "versions" / "75_v3_taxpayer_profiles.py"
    ).read_text(encoding="utf-8")
    assert 'down_revision = "73_v3_boundary_entity_refs"' in migration_74
    assert 'down_revision = "74_v3_party_source_documents"' in migration_75
    assert "DROP TABLE external_parties" not in migration_74.upper()
    assert "EXCLUDE USING gist" in migration_75
    assert "btree_gist" in migration_75
