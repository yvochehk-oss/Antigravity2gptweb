"""Persist AI Review evidence packs and close the review foreign-key gap.

Before this revision ``ai_review_runs.rag_evidence_pack_id`` was a nullable
string with no referenced relation.  The runtime already produced an
EvidencePack-shaped retrieval response, but it was never durable.  This
revision adds the smallest canonical persistence table and a nullable FK.

The column intentionally remains nullable: old review rows and the explicit
Facts-unavailable path must remain readable.  New AI Review runs persist an
empty or degraded pack as well as a populated pack, so the service no longer
creates new dangling identifiers.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

from alembic import op

revision = "013_ai_review_evidence_pack_fk"
down_revision = "012_document_storage_paths"
branch_labels = None
depends_on = None

_PACK_TABLE = "rag_evidence_packs"
_RUN_TABLE = "ai_review_runs"
_FK_NAME = "fk_ai_review_runs_rag_evidence_pack_id"
_PACK_INDEX = "ix_rag_evidence_packs_project_id"
_RUN_INDEX = "ix_ai_review_runs_rag_evidence_pack_id"

_PACK_COLUMNS = {
    "id",
    "project_id",
    "project_code",
    "query",
    "evidence_data",
    "evidence_count",
    "status",
    "extra_metadata",
    "created_at",
    "created_by",
}


def _require_postgresql() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("AI Review evidence pack migration is PostgreSQL-only")


def _matching_fks(table: str) -> list[dict]:
    inspector = sa_inspect(op.get_bind())
    return [
        item
        for item in inspector.get_foreign_keys(table)
        if item.get("constrained_columns") == ["rag_evidence_pack_id"]
    ]


def _project_fks() -> list[dict]:
    inspector = sa_inspect(op.get_bind())
    return [
        item
        for item in inspector.get_foreign_keys(_PACK_TABLE)
        if item.get("constrained_columns") == ["project_id"]
    ]


def _ensure_pack_table() -> None:
    inspector = sa_inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if _PACK_TABLE not in tables:
        op.create_table(
            _PACK_TABLE,
            sa.Column("id", sa.String(36), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("project_code", sa.String(64), nullable=False),
            sa.Column("query", sa.Text(), nullable=False),
            sa.Column("evidence_data", sa.JSON(), nullable=False),
            sa.Column("evidence_count", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("extra_metadata", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.String(40), nullable=False),
            sa.Column("created_by", sa.String(80), nullable=False),
            sa.CheckConstraint(
                "status IN ('AVAILABLE', 'EMPTY', 'DEGRADED')",
                name="ck_rag_evidence_packs_status",
            ),
            sa.CheckConstraint(
                "evidence_count >= 0",
                name="ck_rag_evidence_packs_evidence_count",
            ),
            sa.ForeignKeyConstraint(
                ["project_id"], ["projects.id"],
                name="fk_rag_evidence_packs_project_id",
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
    else:
        columns = {item["name"] for item in inspector.get_columns(_PACK_TABLE)}
        missing = sorted(_PACK_COLUMNS - columns)
        if missing:
            raise RuntimeError(
                f"existing {_PACK_TABLE} is not compatible; missing columns: {', '.join(missing)}"
            )
        primary_key = inspector.get_pk_constraint(_PACK_TABLE).get("constrained_columns") or []
        if primary_key != ["id"]:
            raise RuntimeError(f"{_PACK_TABLE}.id must be the sole primary key")

        checks = {
            item.get("name") for item in inspector.get_check_constraints(_PACK_TABLE)
        }
        if "ck_rag_evidence_packs_status" not in checks:
            op.create_check_constraint(
                "ck_rag_evidence_packs_status",
                _PACK_TABLE,
                "status IN ('AVAILABLE', 'EMPTY', 'DEGRADED')",
            )
        if "ck_rag_evidence_packs_evidence_count" not in checks:
            op.create_check_constraint(
                "ck_rag_evidence_packs_evidence_count",
                _PACK_TABLE,
                "evidence_count >= 0",
            )

    project_fks = _project_fks()
    compatible = [
        item for item in project_fks
        if item.get("referred_table") == "projects"
        and item.get("referred_columns") == ["id"]
    ]
    if any(item not in compatible for item in project_fks):
        raise RuntimeError(
            f"{_PACK_TABLE}.project_id already references an incompatible table"
        )
    if not compatible:
        op.create_foreign_key(
            "fk_rag_evidence_packs_project_id",
            _PACK_TABLE,
            "projects",
            ["project_id"],
            ["id"],
            ondelete="CASCADE",
        )


def upgrade() -> None:
    _require_postgresql()
    inspector = sa_inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "projects" not in tables or _RUN_TABLE not in tables:
        raise RuntimeError("projects and ai_review_runs must exist before evidence pack migration")
    run_columns = {item["name"] for item in inspector.get_columns(_RUN_TABLE)}
    if "rag_evidence_pack_id" not in run_columns:
        raise RuntimeError("ai_review_runs.rag_evidence_pack_id is missing")

    _ensure_pack_table()

    # A pre-existing FK to another target is a schema conflict, not something
    # to silently stack another constraint on top of.
    fks = _matching_fks(_RUN_TABLE)
    compatible = [
        item for item in fks
        if item.get("referred_table") == _PACK_TABLE
        and item.get("referred_columns") == ["id"]
    ]
    incompatible = [item for item in fks if item not in compatible]
    if incompatible:
        raise RuntimeError(
            "ai_review_runs.rag_evidence_pack_id already references an incompatible table"
        )
    if not compatible:
        op.create_foreign_key(
            _FK_NAME,
            _RUN_TABLE,
            _PACK_TABLE,
            ["rag_evidence_pack_id"],
            ["id"],
            ondelete="SET NULL",
        )

    inspector = sa_inspect(op.get_bind())
    run_indexes = {item["name"] for item in inspector.get_indexes(_RUN_TABLE)}
    if _RUN_INDEX not in run_indexes:
        op.create_index(_RUN_INDEX, _RUN_TABLE, ["rag_evidence_pack_id"])
    pack_indexes = {item["name"] for item in inspector.get_indexes(_PACK_TABLE)}
    if _PACK_INDEX not in pack_indexes:
        op.create_index(_PACK_INDEX, _PACK_TABLE, ["project_id"])


def downgrade() -> None:
    _require_postgresql()
    inspector = sa_inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if _PACK_TABLE not in tables:
        return

    # A downgrade must not silently destroy documentary audit history.  The
    # empty-table case is the normal migration test path; operators must
    # export/delete packs deliberately before asking to remove this revision.
    pack_count = op.get_bind().execute(
        text(f"SELECT COUNT(*) FROM {_PACK_TABLE}")
    ).scalar_one()
    if pack_count:
        raise RuntimeError(
            f"refusing downgrade: {_PACK_TABLE} contains {pack_count} evidence pack(s)"
        )

    for item in _matching_fks(_RUN_TABLE):
        if (
            item.get("referred_table") == _PACK_TABLE
            and item.get("referred_columns") == ["id"]
        ):
            op.drop_constraint(item["name"], _RUN_TABLE, type_="foreignkey")

    inspector = sa_inspect(op.get_bind())
    if _RUN_INDEX in {item["name"] for item in inspector.get_indexes(_RUN_TABLE)}:
        op.drop_index(_RUN_INDEX, table_name=_RUN_TABLE)
    if _PACK_INDEX in {item["name"] for item in inspector.get_indexes(_PACK_TABLE)}:
        op.drop_index(_PACK_INDEX, table_name=_PACK_TABLE)
    op.drop_table(_PACK_TABLE)
