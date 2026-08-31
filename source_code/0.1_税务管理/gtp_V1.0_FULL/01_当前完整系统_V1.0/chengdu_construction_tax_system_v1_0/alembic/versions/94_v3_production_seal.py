"""Task23 V3 production seal: make completed cutover immutable.

Revision ID: 94_v3_production_seal
Revises: 93_v3_reader_rag_cutover
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "94_v3_production_seal"
down_revision = "93_v3_reader_rag_cutover"
branch_labels = None
depends_on = None


def _require_postgresql() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")


def _install_transition_guard(*, sealed: bool) -> None:
    seal_clause = """
            IF EXISTS (SELECT 1 FROM v3_cutover_finalizations WHERE scope=OLD.scope)
               AND (
                    NEW.writer_mode IS DISTINCT FROM OLD.writer_mode
                    OR NEW.legacy_write_enabled IS DISTINCT FROM OLD.legacy_write_enabled
                    OR NEW.new_fact_write_enabled IS DISTINCT FROM OLD.new_fact_write_enabled
                    OR NEW.legacy_frozen IS DISTINCT FROM OLD.legacy_frozen
                    OR NEW.new_fact_read_mode IS DISTINCT FROM OLD.new_fact_read_mode
                    OR NEW.rag_source IS DISTINCT FROM OLD.rag_source
               ) THEN
                RAISE EXCEPTION 'V3 cutover is finalized for scope %, routing state is immutable', OLD.scope;
            END IF;
    """ if sealed else ""
    op.execute("DROP TRIGGER IF EXISTS trg_v3_guard_writer_cutover_transition ON writer_cutover_states")
    op.execute("DROP FUNCTION IF EXISTS v3_guard_writer_cutover_transition()")
    op.execute(f"""
        CREATE OR REPLACE FUNCTION v3_guard_writer_cutover_transition()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE blocker_count bigint;
        BEGIN
            IF NEW.scope <> OLD.scope THEN
                RAISE EXCEPTION 'writer cutover scope is immutable';
            END IF;
            {seal_clause}
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
                SELECT count(*) INTO blocker_count FROM shadow_write_diffs
                WHERE simulation_fixture IS FALSE AND result IN ('MISMATCH','ERROR') AND review_status='OPEN';
                IF blocker_count > 0 THEN
                    RAISE EXCEPTION 'writer cutover blocked by % unresolved production shadow diffs', blocker_count;
                END IF;
            END IF;
            IF (OLD.new_fact_read_mode, OLD.rag_source) IS DISTINCT FROM
               (NEW.new_fact_read_mode, NEW.rag_source) AND NEW.new_fact_read_mode='PRIMARY' THEN
                IF OLD.writer_mode <> 'V3_PRIMARY'
                   OR OLD.legacy_write_enabled IS NOT FALSE
                   OR OLD.new_fact_write_enabled IS NOT TRUE
                   OR OLD.legacy_frozen IS NOT TRUE THEN
                    RAISE EXCEPTION 'Reader/RAG PRIMARY requires writer already in strict V3_PRIMARY state';
                END IF;
                SELECT count(*) INTO blocker_count FROM shadow_write_diffs
                WHERE simulation_fixture IS FALSE AND result IN ('MISMATCH','ERROR') AND review_status='OPEN';
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


def upgrade() -> None:
    _require_postgresql()
    op.create_table(
        "v3_cutover_finalizations",
        sa.Column("scope", sa.String(length=64), sa.ForeignKey("writer_cutover_states.scope", ondelete="RESTRICT"), primary_key=True),
        sa.Column("finalized_by", sa.String(length=80), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("state_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )
    op.execute("""
        CREATE OR REPLACE FUNCTION v3_guard_cutover_finalization()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE s writer_cutover_states%ROWTYPE; blocker_count bigint; review_orphans bigint; fact_orphans bigint; invalid_allocations bigint;
        BEGIN
            IF btrim(NEW.finalized_by) = '' THEN
                RAISE EXCEPTION 'production seal requires a non-empty actor';
            END IF;
            SELECT * INTO s FROM writer_cutover_states WHERE scope=NEW.scope FOR UPDATE;
            IF NOT FOUND THEN RAISE EXCEPTION 'cutover state missing for scope %', NEW.scope; END IF;
            IF s.writer_mode <> 'V3_PRIMARY'
               OR s.legacy_write_enabled IS NOT FALSE
               OR s.new_fact_write_enabled IS NOT TRUE
               OR s.legacy_frozen IS NOT TRUE
               OR s.new_fact_read_mode <> 'PRIMARY'
               OR s.rag_source <> 'CANONICAL_FACTS' THEN
                RAISE EXCEPTION 'production seal requires strict V3_PRIMARY + PRIMARY + CANONICAL_FACTS state';
            END IF;
            SELECT count(*) INTO blocker_count FROM shadow_write_diffs
            WHERE simulation_fixture IS FALSE AND result IN ('MISMATCH','ERROR') AND review_status='OPEN';
            IF blocker_count > 0 THEN
                RAISE EXCEPTION 'production seal blocked by % unresolved production shadow diffs', blocker_count;
            END IF;
            SELECT count(*) INTO review_orphans FROM review_diff_queue q
            LEFT JOIN shadow_write_diffs d ON d.id=q.shadow_diff_id WHERE d.id IS NULL;
            IF review_orphans > 0 THEN RAISE EXCEPTION 'production seal blocked by % review queue orphans', review_orphans; END IF;
            SELECT count(*) INTO fact_orphans FROM shadow_write_diffs d
            LEFT JOIN facts f ON f.id=d.canonical_fact_id
            WHERE d.canonical_fact_id IS NOT NULL AND f.id IS NULL;
            IF fact_orphans > 0 THEN RAISE EXCEPTION 'production seal blocked by % canonical fact orphans', fact_orphans; END IF;
            SELECT count(*) INTO invalid_allocations FROM fact_project_allocations a
            LEFT JOIN facts f ON f.id=a.fact_id
            WHERE a.is_current IS TRUE AND a.status='CONFIRMED'
              AND (f.id IS NULL OR f.is_current IS NOT TRUE OR f.validation_status<>'VALID');
            IF invalid_allocations > 0 THEN RAISE EXCEPTION 'production seal blocked by % invalid confirmed allocations', invalid_allocations; END IF;

            NEW.state_snapshot := jsonb_build_object(
                'writer_mode', s.writer_mode,
                'legacy_write_enabled', s.legacy_write_enabled,
                'new_fact_write_enabled', s.new_fact_write_enabled,
                'legacy_frozen', s.legacy_frozen,
                'new_fact_read_mode', s.new_fact_read_mode,
                'rag_source', s.rag_source,
                'state_updated_by', s.updated_by,
                'state_updated_at', s.updated_at
            );
            NEW.evidence_snapshot := jsonb_build_object(
                'unresolved_production_shadow_diffs', blocker_count,
                'review_queue_orphan_count', review_orphans,
                'canonical_fact_orphan_count', fact_orphans,
                'invalid_confirmed_allocation_fact_count', invalid_allocations
            );
            RETURN NEW;
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_v3_guard_cutover_finalization
        BEFORE INSERT ON v3_cutover_finalizations
        FOR EACH ROW EXECUTE FUNCTION v3_guard_cutover_finalization();
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION v3_reject_cutover_finalization_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'V3 production seal is immutable';
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_v3_immutable_cutover_finalization
        BEFORE UPDATE OR DELETE ON v3_cutover_finalizations
        FOR EACH ROW EXECUTE FUNCTION v3_reject_cutover_finalization_mutation();
    """)
    _install_transition_guard(sealed=True)


def downgrade() -> None:
    _require_postgresql()
    _install_transition_guard(sealed=False)
    op.execute("DROP TRIGGER IF EXISTS trg_v3_immutable_cutover_finalization ON v3_cutover_finalizations")
    op.execute("DROP FUNCTION IF EXISTS v3_reject_cutover_finalization_mutation()")
    op.execute("DROP TRIGGER IF EXISTS trg_v3_guard_cutover_finalization ON v3_cutover_finalizations")
    op.execute("DROP FUNCTION IF EXISTS v3_guard_cutover_finalization()")
    op.drop_table("v3_cutover_finalizations")
