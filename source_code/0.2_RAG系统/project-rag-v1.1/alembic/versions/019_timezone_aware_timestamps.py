"""Converge RAG ``*_at`` columns onto timezone-aware timestamps.

Revision ID: 019_timezone_aware_timestamps
Revises: 018_lexical_trigram_index

Tax migration 71 performs the first shared-schema convergence pass.  This RAG
revision is intentionally idempotent and catches RAG-owned tables introduced
later in a fresh deployment, including legacy timestamp-without-time-zone
columns.
"""
from __future__ import annotations

from alembic import op

revision = "019_timezone_aware_timestamps"
down_revision = "018_lexical_trigram_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")
    op.execute("SET LOCAL TIME ZONE 'UTC'")
    op.execute(r"""
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND column_name LIKE '%\_at' ESCAPE '\'
          AND data_type IN (
              'character varying',
              'text',
              'timestamp without time zone'
          )
        ORDER BY table_name, ordinal_position
    LOOP
        EXECUTE format(
            'ALTER TABLE %I ALTER COLUMN %I DROP DEFAULT',
            r.table_name, r.column_name
        );
        IF r.data_type IN ('character varying', 'text') THEN
            EXECUTE format(
                'ALTER TABLE %I ALTER COLUMN %I DROP NOT NULL',
                r.table_name, r.column_name
            );
            EXECUTE format(
                'ALTER TABLE %I ALTER COLUMN %I TYPE timestamptz '
                'USING CASE WHEN btrim(%I::text) = '''' THEN NULL '
                'ELSE %I::timestamptz END',
                r.table_name, r.column_name, r.column_name, r.column_name
            );
        ELSE
            EXECUTE format(
                'ALTER TABLE %I ALTER COLUMN %I TYPE timestamptz '
                'USING %I AT TIME ZONE ''UTC''',
                r.table_name, r.column_name, r.column_name
            );
        END IF;
    END LOOP;
END $$;
""")


def downgrade() -> None:
    pass
