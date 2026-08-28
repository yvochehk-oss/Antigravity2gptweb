"""Converge legacy ``*_at`` columns onto timezone-aware PostgreSQL timestamps.

Revision ID: 71_timezone_aware_timestamps
Revises: 70_versioned_business_rules

The V2 deployment uses one shared PostgreSQL schema.  Tax migrations run first,
so this convergence pass upgrades every already-existing legacy timestamp field
in that schema.  A later RAG migration repeats the same idempotent convergence
for RAG tables introduced after the Tax chain on a fresh installation.
"""
from __future__ import annotations

from alembic import op

revision = "71_timezone_aware_timestamps"
down_revision = "70_versioned_business_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")

    # Historical ISO strings were emitted in UTC by the application.  Setting
    # the transaction timezone makes conversion deterministic even for an old
    # naive timestamp that lacks an explicit offset.
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
            -- Empty-string timestamps mean unknown, not Unix epoch.  Make the
            -- legacy column nullable before converting those sentinels to NULL.
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
    # Deliberately irreversible: converting a real timestamp back to VARCHAR
    # would reintroduce the ordering/timezone defect this migration removes.
    pass
