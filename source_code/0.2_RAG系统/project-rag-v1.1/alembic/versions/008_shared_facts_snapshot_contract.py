"""Verify the Tax-owned canonical Facts snapshot contract in RAG.

The physical ``facts_snapshots`` table is migrated by Tax first.  RAG must not
silently stamp over an old integer/text-column table; this revision is a
fail-closed boundary that verifies the shared contract and completes missing
foreign keys when an existing PostgreSQL database was upgraded in stages.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "008_shared_facts_snapshot_contract"
down_revision = "007_mount_configs"
branch_labels = None
depends_on = None


_REQUIRED = {
    "id",
    "project_id",
    "project_code",
    "facts_data",
    "as_of",
    "facts_version",
    "analytics_contract_version",
    "created_at",
    "created_by",
}
_LEGACY = {
    "metrics_json",
    "raw_response_json",
    "source",
    "require_fresh",
    "max_age",
    "requested_at",
    "requested_by",
    "note",
}


def _has_fk(table: str, column: str, target: str) -> bool:
    for item in sa_inspect(op.get_bind()).get_foreign_keys(table):
        if item.get("referred_table") == target and item.get("constrained_columns") == [column]:
            return True
    return False


def _ensure_fk(table: str, name: str, column: str) -> None:
    if _has_fk(table, column, "facts_snapshots"):
        return
    op.execute(
        sa.text(
            f'ALTER TABLE "{table}" ADD CONSTRAINT "{name}" '
            f'FOREIGN KEY ("{column}") REFERENCES "facts_snapshots" ("id")'
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("shared facts snapshot contract is PostgreSQL-only")
    inspector = sa_inspect(bind)
    tables = set(inspector.get_table_names())
    if "facts_snapshots" not in tables:
        raise RuntimeError("Tax migrations must run before RAG migrations")

    columns = {item["name"] for item in inspector.get_columns("facts_snapshots")}
    missing = sorted(_REQUIRED - columns)
    legacy = sorted(_LEGACY & columns)
    if missing or legacy:
        detail = []
        if missing:
            detail.append("missing=" + ",".join(missing))
        if legacy:
            detail.append("legacy=" + ",".join(legacy))
        raise RuntimeError(
            "facts_snapshots is not canonical; run Tax migration "
            "61f_facts_snapshots_canonical first (" + "; ".join(detail) + ")"
        )

    pk = inspector.get_pk_constraint("facts_snapshots").get("constrained_columns") or []
    if pk != ["id"]:
        raise RuntimeError("facts_snapshots.id must be the sole primary key")
    if not _has_fk("facts_snapshots", "project_id", "projects"):
        raise RuntimeError("facts_snapshots.project_id must reference projects.id")

    unique_pairs = {
        tuple(item.get("column_names") or ()) for item in inspector.get_unique_constraints("facts_snapshots")
    }
    unique_pairs.update(
        tuple(item.get("column_names") or ()) for item in inspector.get_indexes("facts_snapshots") if item.get("unique")
    )
    if ("project_code", "facts_version") not in unique_pairs:
        raise RuntimeError("facts_snapshots must enforce unique (project_code, facts_version)")

    if "ai_review_runs" in tables and "facts_snapshot_id" in {
        item["name"] for item in inspector.get_columns("ai_review_runs")
    }:
        _ensure_fk(
            "ai_review_runs",
            "ai_review_runs_facts_snapshot_id_fkey",
            "facts_snapshot_id",
        )
    if "facts_request_logs" in tables and "facts_snapshot_id" in {
        item["name"] for item in inspector.get_columns("facts_request_logs")
    }:
        _ensure_fk(
            "facts_request_logs",
            "facts_request_logs_facts_snapshot_id_fkey",
            "facts_snapshot_id",
        )


def downgrade() -> None:
    # The table is Tax-owned.  RAG rollback must not drop or mutate shared
    # history; Tax migration 61 controls any explicit schema rollback.
    pass
