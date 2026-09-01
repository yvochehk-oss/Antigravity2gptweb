"""Task22 Reader/RAG cutover guard and reversible read rollback.

Revision ID: 93_v3_reader_rag_cutover
Revises: 92_v3_writer_shadow_cutover
"""
from __future__ import annotations

from alembic import op

revision = "93_v3_reader_rag_cutover"
down_revision = "92_v3_writer_shadow_cutover"
branch_labels = None
depends_on = None


def _require_postgresql() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")


def upgrade() -> None:
    _require_postgresql()
    op.create_check_constraint(
        "ck_writer_cutover_states_reader_rag_pair",
        "writer_cutover_states",
        "(new_fact_read_mode='SHADOW' AND rag_source='LEGACY') OR "
        "(new_fact_read_mode='PRIMARY' AND rag_source='CANONICAL_FACTS')",
    )
    op.execute("DROP TRIGGER IF EXISTS trg_v3_guard_writer_cutover_transition ON writer_cutover_states")
    op.execute("DROP FUNCTION IF EXISTS v3_guard_writer_cutover_transition()")
    op.execute("""
        CREATE OR REPLACE FUNCTION v3_guard_writer_cutover_transition()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE blocker_count bigint;
        BEGIN
            IF NEW.scope <> OLD.scope THEN
                RAISE EXCEPTION 'writer cutover scope is immutable';
            END IF;

            IF NOT (
                (NEW.new_fact_read_mode='SHADOW' AND NEW.rag_source='LEGACY') OR
                (NEW.new_fact_read_mode='PRIMARY' AND NEW.rag_source='CANONICAL_FACTS')
            ) THEN
                RAISE EXCEPTION 'Reader/RAG fields must switch together';
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

            IF (OLD.new_fact_read_mode, OLD.rag_source) IS DISTINCT FROM
               (NEW.new_fact_read_mode, NEW.rag_source)
               AND NEW.new_fact_read_mode='PRIMARY' THEN
                IF OLD.writer_mode <> 'V3_PRIMARY'
                   OR OLD.legacy_write_enabled IS NOT FALSE
                   OR OLD.new_fact_write_enabled IS NOT TRUE
                   OR OLD.legacy_frozen IS NOT TRUE THEN
                    RAISE EXCEPTION 'Reader/RAG PRIMARY requires writer already in strict V3_PRIMARY state';
                END IF;
                SELECT count(*) INTO blocker_count
                FROM shadow_write_diffs
                WHERE simulation_fixture IS FALSE
                  AND result IN ('MISMATCH','ERROR')
                  AND review_status='OPEN';
                IF blocker_count > 0 THEN
                    RAISE EXCEPTION 'Reader/RAG cutover blocked by % unresolved production shadow diffs', blocker_count;
                END IF;
            END IF;

            IF OLD.new_fact_read_mode='PRIMARY' AND NEW.new_fact_read_mode='SHADOW' THEN
                IF NEW.writer_mode <> 'V3_PRIMARY'
                   OR NEW.legacy_write_enabled IS NOT FALSE
                   OR NEW.new_fact_write_enabled IS NOT TRUE
                   OR NEW.legacy_frozen IS NOT TRUE THEN
                    RAISE EXCEPTION 'Reader rollback must not unfreeze or roll back the V3_PRIMARY writer';
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


def downgrade() -> None:
    _require_postgresql()
    op.execute("DROP TRIGGER IF EXISTS trg_v3_guard_writer_cutover_transition ON writer_cutover_states")
    op.execute("DROP FUNCTION IF EXISTS v3_guard_writer_cutover_transition()")
    op.drop_constraint("ck_writer_cutover_states_reader_rag_pair", "writer_cutover_states", type_="check")
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
