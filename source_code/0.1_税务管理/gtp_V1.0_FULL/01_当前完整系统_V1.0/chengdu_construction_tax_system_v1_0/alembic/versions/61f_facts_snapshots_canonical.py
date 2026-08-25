"""Converge Tax/RAG ``facts_snapshots`` to one PostgreSQL contract.

Tax is migrated first in the shared database.  Older Tax revisions created an
integer-keyed table with two text JSON columns, while ProjectRAG uses UUID text
keys and one JSON payload.  This revision upgrades the old table in place,
preserving every historical response in ``facts_data`` and retaining a small
legacy metadata object inside that payload.  No row is deleted.

The downgrade is intentionally reversible only for an empty disposable
database.  A populated canonical table is refused rather than silently
inventing integer IDs or dropping the historical JSON contract.
"""
from __future__ import annotations

import re

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision = "61f_facts_snapshots_canonical"
down_revision = "60a1_project_allocation_planning"
branch_labels = None
depends_on = None


_FACTS_TABLE = "facts_snapshots"
_LOG_TABLE = "facts_request_logs"
_AI_REVIEW_TABLE = "ai_review_runs"


def _columns(table: str) -> dict[str, dict]:
    return {item["name"]: item for item in sa_inspect(op.get_bind()).get_columns(table)}


def _tables() -> set[str]:
    return set(sa_inspect(op.get_bind()).get_table_names())


def _has_constraint(table: str, name: str) -> bool:
    inspector = sa_inspect(op.get_bind())
    if any(item.get("name") == name for item in inspector.get_unique_constraints(table)):
        return True
    if any(item.get("name") == name for item in inspector.get_foreign_keys(table)):
        return True
    return any(item.get("name") == name for item in inspector.get_check_constraints(table))


def _drop_fks_referencing(table: str) -> None:
    """Drop only FKs that point to ``table`` so its key type can be changed."""
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT conrelid::regclass::text AS table_name, conname
            FROM pg_constraint
            WHERE contype = 'f'
              AND confrelid = to_regclass(:target)
            """
        ),
        {"target": f"public.{table}"},
    ).mappings()
    for row in rows:
        child = str(row["table_name"]).split(".")[-1].strip('"')
        name = str(row["conname"])
        child = _safe_identifier(child)
        name = _safe_identifier(name)
        op.execute(
            sa.text(f'ALTER TABLE "{child}" DROP CONSTRAINT IF EXISTS "{name}"')
        )


def _safe_identifier(value: str) -> str:
    """Allow only PostgreSQL identifiers returned by the inspector."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise RuntimeError(f"unexpected PostgreSQL identifier: {value!r}")
    return value


def _drop_fk_if_present(table: str, name: str) -> None:
    table = _safe_identifier(table)
    name = _safe_identifier(name)
    op.execute(sa.text(f'ALTER TABLE "{table}" DROP CONSTRAINT IF EXISTS "{name}"'))


def _is_integer_type(value: object) -> bool:
    return "INT" in str(value).upper() and "INTERVAL" not in str(value).upper()


def _uuid_expression(column: str) -> str:
    """Stable UUID text derived from a legacy integer id, without extensions."""
    return (
        "substr(md5('tax-facts-snapshot:' || " + column + "::text), 1, 8) || '-' || "
        "substr(md5('tax-facts-snapshot:' || " + column + "::text), 9, 4) || '-' || "
        "substr(md5('tax-facts-snapshot:' || " + column + "::text), 13, 4) || '-' || "
        "substr(md5('tax-facts-snapshot:' || " + column + "::text), 17, 4) || '-' || "
        "substr(md5('tax-facts-snapshot:' || " + column + "::text), 21, 12)"
    )


def _ensure_safe_json_helper() -> None:
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION _v2_safe_jsonb(value text)
            RETURNS jsonb
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF value IS NULL OR btrim(value) = '' THEN
                    RETURN '{}'::jsonb;
                END IF;
                RETURN value::jsonb;
            EXCEPTION WHEN others THEN
                RETURN '{}'::jsonb;
            END;
            $$
            """
        )
    )


def _drop_safe_json_helper() -> None:
    op.execute(sa.text("DROP FUNCTION IF EXISTS _v2_safe_jsonb(text)"))


def _populate_canonical_payload(columns: set[str]) -> None:
    """Build one replayable JSON object from old Tax columns."""
    bind = op.get_bind()
    if "facts_data" not in columns:
        return

    # These expressions are only used for columns that exist in the legacy
    # table.  A canonical table already contains facts_data and is left intact.
    raw_expr = "_v2_safe_jsonb(raw_response_json)" if "raw_response_json" in columns else "'{}'::jsonb"
    metrics_expr = "_v2_safe_jsonb(metrics_json)" if "metrics_json" in columns else "'{}'::jsonb"
    source_expr = "source" if "source" in columns else "'rag_v1'"
    fresh_expr = "require_fresh" if "require_fresh" in columns else "false"
    max_age_expr = "max_age" if "max_age" in columns else "60"
    requested_at_expr = "requested_at" if "requested_at" in columns else "created_at"
    requested_by_expr = "requested_by" if "requested_by" in columns else "created_by"
    note_expr = "note" if "note" in columns else "''"

    # Keep all raw response keys, but force the canonical identity and metrics
    # keys to deterministic values.  ``_snapshot_metadata`` is retained only
    # for the old Tax client adapter and is never used as Facts truth.
    statement = sa.text(
        f"""
        UPDATE {_FACTS_TABLE}
        SET facts_data = (
            CASE
                WHEN jsonb_typeof({raw_expr}) = 'object' THEN {raw_expr}
                ELSE '{{}}'::jsonb
            END
            || jsonb_build_object(
                'project_code', project_code,
                'as_of', as_of,
                'facts_version', facts_version,
                'facts_available', true,
                'metrics', CASE
                    WHEN jsonb_typeof({raw_expr}->'metrics') IN ('object', 'array')
                        THEN {raw_expr}->'metrics'
                    WHEN jsonb_typeof({metrics_expr}) = 'object'
                        THEN {metrics_expr}
                    ELSE '{{}}'::jsonb
                END,
                '_snapshot_metadata', jsonb_build_object(
                    'source', {source_expr},
                    'require_fresh', {fresh_expr},
                    'max_age', {max_age_expr},
                    'requested_at', {requested_at_expr},
                    'requested_by', {requested_by_expr},
                    'note', {note_expr}
                )
            )
        )::json
        WHERE facts_data IS NULL
        """
    )
    bind.execute(statement)


def _normalise_duplicate_versions() -> None:
    """Keep all history while satisfying the canonical unique version key."""
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY project_code, facts_version
                           ORDER BY created_at NULLS LAST, id
                       ) AS ordinal
                FROM facts_snapshots
            )
            UPDATE facts_snapshots AS snapshot
            SET facts_version = left(snapshot.facts_version, 48)
                               || '-legacy-' || left(snapshot.id, 8),
                facts_data = jsonb_set(
                    snapshot.facts_data::jsonb,
                    '{original_facts_version}',
                    to_jsonb(snapshot.facts_version),
                    true
                )::json
            FROM ranked
            WHERE ranked.id = snapshot.id
              AND ranked.ordinal > 1
            """
        )
    )


def _ensure_unique_constraint() -> None:
    if _has_constraint(_FACTS_TABLE, "uq_facts_snapshots_project_facts_version"):
        return
    op.execute(
        sa.text(
            "ALTER TABLE facts_snapshots ADD CONSTRAINT "
            "uq_facts_snapshots_project_facts_version "
            "UNIQUE (project_code, facts_version)"
        )
    )


def _ensure_foreign_key(table: str, name: str, column: str) -> None:
    table = _safe_identifier(table)
    name = _safe_identifier(name)
    column = _safe_identifier(column)
    if _has_constraint(table, name):
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
        raise RuntimeError("facts_snapshots canonical migration is PostgreSQL-only")
    if _FACTS_TABLE not in _tables():
        raise RuntimeError("facts_snapshots must be created by Tax 001_initial first")

    current = _columns(_FACTS_TABLE)
    old_id_is_integer = _is_integer_type(current["id"]["type"])

    # Add canonical columns nullable first so existing rows remain untouched
    # until their payload has been transformed transactionally.
    additions = (
        ("facts_data", sa.Column("facts_data", sa.JSON(), nullable=True)),
        ("analytics_contract_version", sa.Column("analytics_contract_version", sa.String(32), nullable=True)),
        ("created_at", sa.Column("created_at", sa.String(40), nullable=True)),
        ("created_by", sa.Column("created_by", sa.String(80), nullable=True)),
    )
    for name, column in additions:
        if name not in current:
            op.add_column(_FACTS_TABLE, column)

    current = _columns(_FACTS_TABLE)
    columns = set(current)
    _ensure_safe_json_helper()
    try:
        _populate_canonical_payload(columns)
        op.execute(
            sa.text(
                "UPDATE facts_snapshots "
                "SET analytics_contract_version = COALESCE(NULLIF(analytics_contract_version, ''), '1.0'), "
                "created_at = COALESCE(NULLIF(created_at, ''), NULLIF(requested_at, ''), as_of), "
                "created_by = COALESCE(NULLIF(created_by, ''), NULLIF(requested_by, ''), 'system')"
            )
        )
    finally:
        _drop_safe_json_helper()

    # The original table used integer IDs.  Save a mapping column while the
    # old value is still available; dependent rows are restored after the key
    # conversion and before the mapping is dropped.
    dependent_tables = _tables()
    if old_id_is_integer:
        op.add_column(_FACTS_TABLE, sa.Column("_legacy_facts_snapshot_id", sa.Integer(), nullable=True))
        op.execute(sa.text("UPDATE facts_snapshots SET _legacy_facts_snapshot_id = id"))

        if _LOG_TABLE in dependent_tables and "facts_snapshot_id" in _columns(_LOG_TABLE):
            log_type = _columns(_LOG_TABLE)["facts_snapshot_id"]["type"]
            if _is_integer_type(log_type):
                op.add_column(_LOG_TABLE, sa.Column("_legacy_facts_snapshot_id", sa.Integer(), nullable=True))
                op.execute(sa.text("UPDATE facts_request_logs SET _legacy_facts_snapshot_id = facts_snapshot_id"))

        _drop_fks_referencing(_FACTS_TABLE)
        op.alter_column(
            _FACTS_TABLE,
            "id",
            type_=sa.String(36),
            existing_type=sa.Integer(),
            postgresql_using=f"({_uuid_expression('id')})",
        )

        if (
            _LOG_TABLE in dependent_tables
            and "facts_snapshot_id" in _columns(_LOG_TABLE)
            and _is_integer_type(_columns(_LOG_TABLE)["facts_snapshot_id"]["type"])
        ):
                op.alter_column(
                    _LOG_TABLE,
                    "facts_snapshot_id",
                    type_=sa.String(36),
                    existing_type=sa.Integer(),
                    nullable=True,
                    postgresql_using="NULL::varchar",
                )
                op.execute(
                    sa.text(
                        "UPDATE facts_request_logs AS log "
                        "SET facts_snapshot_id = snapshot.id "
                        "FROM facts_snapshots AS snapshot "
                        "WHERE log._legacy_facts_snapshot_id = snapshot._legacy_facts_snapshot_id"
                    )
                )
                op.drop_column(_LOG_TABLE, "_legacy_facts_snapshot_id")

        if _AI_REVIEW_TABLE in dependent_tables and "facts_snapshot_id" in _columns(_AI_REVIEW_TABLE):
            op.execute(
                sa.text(
                    "UPDATE ai_review_runs AS run "
                    "SET facts_snapshot_id = snapshot.id "
                    "FROM facts_snapshots AS snapshot "
                    "WHERE run.facts_snapshot_id = snapshot._legacy_facts_snapshot_id::text"
                )
            )
        op.drop_column(_FACTS_TABLE, "_legacy_facts_snapshot_id")

    # A partially converged shared database may already have UUID snapshot
    # rows while the old Tax request log still has integer references.  Handle
    # that drift independently of the snapshot primary-key conversion.
    if _LOG_TABLE in _tables() and "facts_snapshot_id" in _columns(_LOG_TABLE):
        log_columns = _columns(_LOG_TABLE)
        if _is_integer_type(log_columns["facts_snapshot_id"]["type"]):
            if "_legacy_facts_snapshot_id" not in log_columns:
                op.add_column(_LOG_TABLE, sa.Column("_legacy_facts_snapshot_id", sa.Integer(), nullable=True))
                op.execute(sa.text("UPDATE facts_request_logs SET _legacy_facts_snapshot_id = facts_snapshot_id"))
            _drop_fk_if_present(_LOG_TABLE, "facts_request_logs_facts_snapshot_id_fkey")
            op.alter_column(
                _LOG_TABLE,
                "facts_snapshot_id",
                type_=sa.String(36),
                existing_type=sa.Integer(),
                nullable=True,
                postgresql_using="NULL::varchar",
            )
            op.execute(
                sa.text(
                    "UPDATE facts_request_logs AS log "
                    f"SET facts_snapshot_id = {_uuid_expression('log._legacy_facts_snapshot_id')} "
                    "WHERE EXISTS (SELECT 1 FROM facts_snapshots snapshot "
                    f"WHERE snapshot.id = {_uuid_expression('log._legacy_facts_snapshot_id')})"
                )
            )
            op.drop_column(_LOG_TABLE, "_legacy_facts_snapshot_id")

    # Canonical column widths are deliberately shared with the RAG model.
    op.alter_column(_FACTS_TABLE, "project_code", type_=sa.String(64), existing_type=sa.String(30))
    op.alter_column(_FACTS_TABLE, "as_of", type_=sa.String(40), existing_type=sa.String(40))
    op.alter_column(_FACTS_TABLE, "facts_version", type_=sa.String(64), existing_type=sa.String(40))
    op.alter_column(_FACTS_TABLE, "facts_data", nullable=False, existing_type=sa.JSON())
    op.alter_column(_FACTS_TABLE, "analytics_contract_version", nullable=False, existing_type=sa.String(32))
    op.alter_column(_FACTS_TABLE, "created_at", nullable=False, existing_type=sa.String(40))
    op.alter_column(_FACTS_TABLE, "created_by", nullable=True, existing_type=sa.String(80))

    # Remove the old dual-truth columns only after facts_data is populated.
    # This also makes the post-migration schema pass the RAG baseline's exact
    # compatibility check.
    for name in (
        "metrics_json", "raw_response_json", "source", "require_fresh",
        "max_age", "requested_at", "requested_by", "note",
    ):
        if name in _columns(_FACTS_TABLE):
            op.drop_column(_FACTS_TABLE, name)

    bind.execute(sa.text("DROP INDEX IF EXISTS ix_facts_snapshots_requested_at"))
    bind.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_facts_snapshots_created_at ON facts_snapshots (created_at)"))
    _normalise_duplicate_versions()
    _ensure_unique_constraint()

    if _LOG_TABLE in _tables() and "facts_snapshot_id" in _columns(_LOG_TABLE):
        _ensure_foreign_key(_LOG_TABLE, "facts_request_logs_facts_snapshot_id_fkey", "facts_snapshot_id")
    if _AI_REVIEW_TABLE in _tables() and "facts_snapshot_id" in _columns(_AI_REVIEW_TABLE):
        # Existing RAG installations may have orphan references from the old
        # integer table.  Keep valid references and null only broken ones
        # before enforcing the shared FK.
        bind.execute(
            sa.text(
                "UPDATE ai_review_runs AS run SET facts_snapshot_id = NULL "
                "WHERE run.facts_snapshot_id IS NOT NULL "
                "AND NOT EXISTS (SELECT 1 FROM facts_snapshots s WHERE s.id = run.facts_snapshot_id)"
            )
        )
        _ensure_foreign_key(_AI_REVIEW_TABLE, "ai_review_runs_facts_snapshot_id_fkey", "facts_snapshot_id")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("facts_snapshots canonical migration is PostgreSQL-only")
    if _FACTS_TABLE not in _tables():
        return
    if bind.execute(sa.text("SELECT 1 FROM facts_snapshots LIMIT 1")).first() is not None:
        raise RuntimeError(
            "refusing to downgrade populated canonical facts_snapshots; "
            "take a PostgreSQL backup and perform an explicit data migration"
        )

    for table, constraint in (
        (_LOG_TABLE, "facts_request_logs_facts_snapshot_id_fkey"),
        (_AI_REVIEW_TABLE, "ai_review_runs_facts_snapshot_id_fkey"),
    ):
        if table in _tables():
            _drop_fk_if_present(table, constraint)

    current = _columns(_FACTS_TABLE)
    if not _is_integer_type(current["id"]["type"]):
        _drop_fks_referencing(_FACTS_TABLE)
        op.alter_column(
            _FACTS_TABLE, "id", type_=sa.Integer(), existing_type=sa.String(36),
            postgresql_using="NULL::integer",
        )
    if (
        _LOG_TABLE in _tables()
        and "facts_snapshot_id" in _columns(_LOG_TABLE)
        and not _is_integer_type(_columns(_LOG_TABLE)["facts_snapshot_id"]["type"])
    ):
            op.alter_column(
                _LOG_TABLE, "facts_snapshot_id", type_=sa.Integer(),
                existing_type=sa.String(36), nullable=True,
                postgresql_using="NULL::integer",
            )

    op.execute(sa.text("ALTER TABLE facts_snapshots DROP CONSTRAINT IF EXISTS uq_facts_snapshots_project_facts_version"))
    bind.execute(sa.text("DROP INDEX IF EXISTS ix_facts_snapshots_created_at"))
    for name in ("facts_data", "analytics_contract_version", "created_at", "created_by"):
        if name in _columns(_FACTS_TABLE):
            op.drop_column(_FACTS_TABLE, name)

    legacy_columns = (
        ("metrics_json", sa.Text(), "{}"),
        ("raw_response_json", sa.Text(), "{}"),
        ("source", sa.String(20), "rag_v1"),
        ("require_fresh", sa.Boolean(), "false"),
        ("max_age", sa.Integer(), "60"),
        ("requested_at", sa.String(30), ""),
        ("requested_by", sa.String(80), "system"),
        ("note", sa.String(300), ""),
    )
    for name, type_, default in legacy_columns:
        if name not in _columns(_FACTS_TABLE):
            op.add_column(
                _FACTS_TABLE,
                sa.Column(name, type_, nullable=False, server_default=sa.text(f"'{default}'" if default not in {"false", "true"} else default)),
            )
    op.execute(sa.text("DROP INDEX IF EXISTS ix_facts_snapshots_created_at"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_facts_snapshots_requested_at ON facts_snapshots (requested_at)"))
