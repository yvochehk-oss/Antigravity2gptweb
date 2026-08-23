"""Add request correlation IDs to Tax audit/Facts request logs.

This is deliberately a separate revision from ``001_initial``.  The initial
revision is already stamped in existing Tax databases, so changing its table
definition would not upgrade those databases and would recreate the incident
where a migration was stamped despite an incomplete schema.
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "002_request_id"
down_revision: str | None = "001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLES = ("audit_logs", "facts_request_logs")
_INDEXES = {
    "audit_logs": "ix_audit_logs_request_id",
    "facts_request_logs": "ix_facts_request_logs_request_id",
}


def _request_id_type(bind) -> str:
    return sa.String(64).compile(dialect=bind.dialect).upper().replace(" ", "")


def _validate_request_column(table_name: str, column) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    expected_type = _request_id_type(bind)
    actual_type = column["type"].compile(dialect=bind.dialect).upper().replace(" ", "")
    if actual_type != expected_type:
        raise RuntimeError(
            f"incompatible {table_name}.request_id type: {actual_type!r} != {expected_type!r}"
        )
    if bool(column["nullable"]):
        raise RuntimeError(f"incompatible {table_name}.request_id: nullable column is not allowed")

    index_name = _INDEXES[table_name]
    indexes = {item["name"]: item for item in inspector.get_indexes(table_name)}
    index = indexes.get(index_name)
    if index is not None:
        if tuple(index.get("column_names") or ()) != ("request_id",) or bool(index.get("unique")):
            raise RuntimeError(f"incompatible index {index_name!r} on {table_name!r}: {index!r}")
        return
    equivalent = any(
        tuple(item.get("column_names") or ()) == ("request_id",) and not bool(item.get("unique"))
        for item in indexes.values()
    )
    if not equivalent:
        raise RuntimeError(f"missing required index {index_name!r} on {table_name!r}")


def _ensure_request_id(table_name: str) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        raise RuntimeError(f"missing required table {table_name!r}")

    columns = {item["name"]: item for item in inspector.get_columns(table_name)}
    column = columns.get("request_id")
    if column is None:
        # Existing rows need a deterministic, non-null value.  The application
        # default is an empty string; the server default is retained for
        # SQLite compatibility because SQLite cannot add a NOT NULL column to a
        # populated table without one in the same ALTER TABLE operation.
        op.add_column(
            table_name,
            sa.Column(
                "request_id",
                sa.String(64),
                nullable=False,
                server_default=sa.text("''"),
            ),
        )
        columns = {item["name"]: item for item in sa.inspect(bind).get_columns(table_name)}
        column = columns.get("request_id")
        if column is None:
            raise RuntimeError(f"failed to add {table_name}.request_id")

    # The index may have been created by a partial previous attempt.  Validate
    # it before accepting the column; do not silently recreate a conflicting
    # object under a different meaning.
    index_name = _INDEXES[table_name]
    indexes = {item["name"]: item for item in sa.inspect(bind).get_indexes(table_name)}
    if index_name not in indexes:
        equivalent = any(
            tuple(item.get("column_names") or ()) == ("request_id",)
            and not bool(item.get("unique"))
            for item in indexes.values()
        )
        if not equivalent:
            op.create_index(index_name, table_name, ["request_id"], unique=False)

    _validate_request_column(table_name, column)


def upgrade() -> None:
    for table_name in _TABLES:
        _ensure_request_id(table_name)


def _refuse_data_loss_on_downgrade() -> None:
    """Refuse to drop populated request IDs without an explicit override."""
    if os.getenv("ALLOW_DESTRUCTIVE_TAX_DOWNGRADE") == "1":
        return
    bind = op.get_bind()
    populated = []
    for table_name in _TABLES:
        quoted = bind.dialect.identifier_preparer.quote(table_name)
        if bind.execute(sa.text(f"SELECT 1 FROM {quoted} LIMIT 1")).first() is not None:
            populated.append(table_name)
    if populated:
        raise RuntimeError(
            "refusing to drop request_id from populated Tax log tables; "
            "verify a backup, then set ALLOW_DESTRUCTIVE_TAX_DOWNGRADE=1 "
            f"explicitly. Populated tables: {', '.join(populated)}"
        )


def downgrade() -> None:
    _refuse_data_loss_on_downgrade()
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table_name in reversed(_TABLES):
        columns = {item["name"]: item for item in inspector.get_columns(table_name)}
        if "request_id" not in columns:
            raise RuntimeError(f"missing {table_name}.request_id during downgrade")
        index_name = _INDEXES[table_name]
        indexes = {item["name"]: item for item in inspector.get_indexes(table_name)}
        if index_name in indexes:
            op.drop_index(index_name, table_name=table_name)
        else:
            equivalent = next(
                (
                    item["name"]
                    for item in indexes.values()
                    if tuple(item.get("column_names") or ()) == ("request_id",)
                    and not bool(item.get("unique"))
                ),
                None,
            )
            if equivalent is None:
                raise RuntimeError(f"missing request_id index on {table_name!r} during downgrade")
            op.drop_index(equivalent, table_name=table_name)
        op.drop_column(table_name, "request_id")
