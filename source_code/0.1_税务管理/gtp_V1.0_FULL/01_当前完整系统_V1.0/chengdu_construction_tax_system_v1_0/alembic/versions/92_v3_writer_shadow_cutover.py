"""Add Task21 writer shadow-cutover state and diff ledger.

Revision ID: 92_v3_writer_shadow_cutover
Revises: 91_v3_transaction_graph
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "92_v3_writer_shadow_cutover"
down_revision = "91_v3_transaction_graph"
branch_labels = None
depends_on = None


def _require_postgresql() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")


def upgrade() -> None:
    _require_postgresql()
    op.create_table(
        "writer_cutover_states",
        sa.Column("scope", sa.String(64), primary_key=True),
        sa.Column("writer_mode", sa.String(20), nullable=False),
        sa.Column("legacy_write_enabled", sa.Boolean(), nullable=False),
        sa.Column("new_fact_write_enabled", sa.Boolean(), nullable=False),
        sa.Column("rag_source", sa.String(24), nullable=False),
        sa.Column("new_fact_read_mode", sa.String(20), nullable=False),
        sa.Column("legacy_frozen", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("updated_by", sa.String(80), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("writer_mode IN ('SHADOW','DUAL_WRITE','V3_PRIMARY')", name="ck_writer_cutover_states_mode"),
        sa.CheckConstraint("rag_source IN ('LEGACY','CANONICAL_FACTS')", name="ck_writer_cutover_states_rag_source"),
        sa.CheckConstraint("new_fact_read_mode IN ('OFF','SHADOW','PRIMARY')", name="ck_writer_cutover_states_read_mode"),
        sa.CheckConstraint(
            "(writer_mode='SHADOW' AND legacy_write_enabled IS TRUE AND new_fact_write_enabled IS FALSE AND rag_source='LEGACY' AND new_fact_read_mode='SHADOW' AND legacy_frozen IS FALSE) OR "
            "(writer_mode='DUAL_WRITE' AND legacy_write_enabled IS TRUE AND new_fact_write_enabled IS TRUE AND rag_source='LEGACY' AND new_fact_read_mode='SHADOW' AND legacy_frozen IS FALSE) OR "
            "(writer_mode='V3_PRIMARY' AND legacy_write_enabled IS FALSE AND new_fact_write_enabled IS TRUE AND legacy_frozen IS TRUE)",
            name="ck_writer_cutover_states_mode_flags",
        ),
    )

    op.create_table(
        "shadow_write_diffs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("operation_key", sa.String(240), nullable=False, unique=True),
        sa.Column("object_type", sa.String(40), nullable=False),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("legacy_object_id", sa.String(120), nullable=True),
        sa.Column("canonical_fact_id", sa.Integer(), sa.ForeignKey("facts.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("legacy_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("canonical_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("normalized_diff", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("review_status", sa.String(16), nullable=False),
        sa.Column("simulation_fixture", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("resolved_by", sa.String(80), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("operation IN ('INSERT','UPDATE','DELETE')", name="ck_shadow_write_diffs_operation"),
        sa.CheckConstraint("result IN ('MATCH','MISMATCH','ERROR')", name="ck_shadow_write_diffs_result"),
        sa.CheckConstraint("review_status IN ('OPEN','RESOLVED','IGNORED')", name="ck_shadow_write_diffs_review_status"),
        sa.CheckConstraint(
            "(result='MATCH' AND review_status='RESOLVED' AND error_code IS NULL AND error_message IS NULL) OR "
            "(result='MISMATCH' AND error_code IS NULL) OR (result='ERROR' AND error_code IS NOT NULL)",
            name="ck_shadow_write_diffs_result_semantics",
        ),
        sa.CheckConstraint(
            "(review_status='OPEN' AND resolved_by IS NULL AND resolved_at IS NULL) OR "
            "(review_status IN ('RESOLVED','IGNORED') AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL)",
            name="ck_shadow_write_diffs_resolution_semantics",
        ),
    )
    op.create_index("ix_shadow_write_diffs_result", "shadow_write_diffs", ["result"])
    op.create_index("ix_shadow_write_diffs_review_status", "shadow_write_diffs", ["review_status"])
    op.create_index("ix_shadow_write_diffs_canonical_fact", "shadow_write_diffs", ["canonical_fact_id"])

    op.create_table(
        "review_diff_queue",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("shadow_diff_id", sa.BigInteger(), sa.ForeignKey("shadow_write_diffs.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'OPEN'")),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reviewed_by", sa.String(80), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("status IN ('OPEN','RESOLVED','IGNORED')", name="ck_review_diff_queue_status"),
        sa.CheckConstraint(
            "(status='OPEN' AND reviewed_by IS NULL AND reviewed_at IS NULL) OR "
            "(status IN ('RESOLVED','IGNORED') AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="ck_review_diff_queue_resolution",
        ),
    )
    op.create_index("ix_review_diff_queue_status", "review_diff_queue", ["status"])

    op.execute("""
        CREATE OR REPLACE FUNCTION v3_enqueue_shadow_diff_review()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.result IN ('MISMATCH','ERROR') THEN
                INSERT INTO review_diff_queue(shadow_diff_id, status, reason)
                VALUES (
                    NEW.id, 'OPEN',
                    CASE WHEN NEW.result='ERROR'
                         THEN COALESCE(NEW.error_code, 'V3_SHADOW_WRITE_FAILED')
                         ELSE 'SHADOW_WRITE_MISMATCH' END
                ) ON CONFLICT (shadow_diff_id) DO NOTHING;
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_v3_enqueue_shadow_diff_review
        AFTER INSERT ON shadow_write_diffs
        FOR EACH ROW EXECUTE FUNCTION v3_enqueue_shadow_diff_review();
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION v3_guard_writer_cutover_transition()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE blocker_count bigint;
        BEGIN
            IF NEW.scope <> OLD.scope THEN
                RAISE EXCEPTION 'writer cutover scope is immutable';
            END IF;
            IF OLD.writer_mode='SHADOW' AND NEW.writer_mode NOT IN ('SHADOW','DUAL_WRITE') THEN
                RAISE EXCEPTION 'illegal writer cutover transition: SHADOW -> %', NEW.writer_mode;
            END IF;
            IF OLD.writer_mode='V3_PRIMARY' AND NEW.writer_mode <> 'V3_PRIMARY' THEN
                RAISE EXCEPTION 'V3_PRIMARY writer mode is fail-closed; rollback requires a new reviewed migration';
            END IF;
            IF NEW.writer_mode <> OLD.writer_mode AND NEW.writer_mode IN ('DUAL_WRITE','V3_PRIMARY') THEN
                SELECT count(*) INTO blocker_count
                FROM shadow_write_diffs
                WHERE simulation_fixture IS FALSE
                  AND result IN ('MISMATCH','ERROR')
                  AND review_status='OPEN';
                IF blocker_count > 0 THEN
                    RAISE EXCEPTION 'writer cutover blocked by % unresolved production shadow diffs', blocker_count;
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_v3_guard_writer_cutover_transition
        BEFORE UPDATE ON writer_cutover_states
        FOR EACH ROW EXECUTE FUNCTION v3_guard_writer_cutover_transition();
    """)

    op.execute("""
        INSERT INTO writer_cutover_states(
            scope, writer_mode, legacy_write_enabled, new_fact_write_enabled,
            rag_source, new_fact_read_mode, legacy_frozen, updated_by
        ) VALUES (
            'GLOBAL', 'SHADOW', true, false, 'LEGACY', 'SHADOW', false,
            'migration:92_v3_writer_shadow_cutover'
        );
    """)


def downgrade() -> None:
    _require_postgresql()
    op.execute("DROP TRIGGER IF EXISTS trg_v3_guard_writer_cutover_transition ON writer_cutover_states")
    op.execute("DROP FUNCTION IF EXISTS v3_guard_writer_cutover_transition()")
    op.execute("DROP TRIGGER IF EXISTS trg_v3_enqueue_shadow_diff_review ON shadow_write_diffs")
    op.execute("DROP FUNCTION IF EXISTS v3_enqueue_shadow_diff_review()")
    op.drop_index("ix_review_diff_queue_status", table_name="review_diff_queue")
    op.drop_table("review_diff_queue")
    op.drop_index("ix_shadow_write_diffs_canonical_fact", table_name="shadow_write_diffs")
    op.drop_index("ix_shadow_write_diffs_review_status", table_name="shadow_write_diffs")
    op.drop_index("ix_shadow_write_diffs_result", table_name="shadow_write_diffs")
    op.drop_table("shadow_write_diffs")
    op.drop_table("writer_cutover_states")
