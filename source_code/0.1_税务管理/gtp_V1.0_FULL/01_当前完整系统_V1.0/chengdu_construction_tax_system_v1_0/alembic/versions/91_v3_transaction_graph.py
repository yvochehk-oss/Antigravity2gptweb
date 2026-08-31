"""Add Task20 canonical business transaction graph and review queue.

Revision ID: 91_v3_transaction_graph
Revises: 90_v3_payment_facts
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "91_v3_transaction_graph"
down_revision = "90_v3_payment_facts"
branch_labels = None
depends_on = None


def _require_postgresql() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")


def upgrade() -> None:
    _require_postgresql()
    op.create_table(
        "business_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("business_identity_key", sa.String(240), nullable=False, unique=True),
        sa.Column("created_by", sa.String(80), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_table(
        "transaction_fact_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("transaction_id", sa.Integer(), sa.ForeignKey("business_transactions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fact_id", sa.Integer(), sa.ForeignKey("facts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("relation_type", sa.String(20), nullable=False),
        sa.Column("allocated_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("allocation_method", sa.String(24), nullable=False, server_default="EXPLICIT"),
        sa.Column("link_source", sa.String(40), nullable=False),
        sa.Column("confidence", sa.Numeric(6, 5), nullable=True),
        sa.Column("match_score_breakdown", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(20), nullable=False, server_default="CANDIDATE"),
        sa.Column("confirmed_by", sa.String(80), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("relation_type IN ('CONTRACT','FULFILLMENT','INVOICE','PAYMENT')", name="ck_transaction_fact_links_relation_type"),
        sa.CheckConstraint("allocation_method IN ('EXPLICIT','SOURCE_DOCUMENT','MANUAL','PROPORTIONAL','RULE_BASED')", name="ck_transaction_fact_links_allocation_method"),
        sa.CheckConstraint("status IN ('CANDIDATE','NEEDS_REVIEW','CONFIRMED','REJECTED')", name="ck_transaction_fact_links_status"),
        sa.CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_transaction_fact_links_confidence"),
        sa.CheckConstraint("(status='CONFIRMED' AND confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL) OR (status<>'CONFIRMED' AND confirmed_by IS NULL AND confirmed_at IS NULL)", name="ck_transaction_fact_links_confirmation_semantics"),
        sa.UniqueConstraint("transaction_id", "fact_id", "relation_type", name="uq_transaction_fact_link"),
    )
    op.create_index("ix_transaction_fact_links_transaction", "transaction_fact_links", ["transaction_id"])
    op.create_index("ix_transaction_fact_links_fact", "transaction_fact_links", ["fact_id"])
    op.create_index("ix_transaction_fact_links_status", "transaction_fact_links", ["status"])
    op.create_table(
        "transaction_participants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("transaction_id", sa.Integer(), sa.ForeignKey("business_transactions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("party_id", sa.Integer(), sa.ForeignKey("parties.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_fact_id", sa.Integer(), sa.ForeignKey("facts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("participant_role", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("participant_role IN ('CONTRACT_BUYER','CONTRACT_SELLER','FULFILLMENT_PROVIDER','FULFILLMENT_RECEIVER','INVOICE_SELLER','INVOICE_BUYER','PAYMENT_PAYER','PAYMENT_PAYEE')", name="ck_transaction_participants_role"),
        sa.UniqueConstraint("transaction_id", "source_fact_id", "participant_role", name="uq_transaction_participant_source_role"),
    )
    op.create_index("ix_transaction_participants_transaction", "transaction_participants", ["transaction_id"])
    op.create_index("ix_transaction_participants_party", "transaction_participants", ["party_id"])
    op.create_table(
        "review_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("object_type", sa.String(32), nullable=False),
        sa.Column("object_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(8), nullable=False, server_default="P2"),
        sa.Column("assigned_to", sa.String(80), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="OPEN"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.CheckConstraint("object_type='TRANSACTION_FACT_LINK'", name="ck_review_tasks_object_type"),
        sa.CheckConstraint("priority IN ('P0','P1','P2','P3')", name="ck_review_tasks_priority"),
        sa.CheckConstraint("status IN ('OPEN','IN_REVIEW','RESOLVED','CANCELLED')", name="ck_review_tasks_status"),
        sa.CheckConstraint("(status IN ('OPEN','IN_REVIEW') AND resolved_at IS NULL) OR (status IN ('RESOLVED','CANCELLED') AND resolved_at IS NOT NULL)", name="ck_review_tasks_resolution_semantics"),
    )
    op.create_index("ix_review_tasks_object", "review_tasks", ["object_type", "object_id"])
    op.create_index("ix_review_tasks_status", "review_tasks", ["status"])
    op.create_index("uq_review_tasks_open_object", "review_tasks", ["object_type", "object_id"], unique=True, postgresql_where=sa.text("status IN ('OPEN','IN_REVIEW')"))

    op.execute("""
        CREATE FUNCTION v3_guard_transaction_fact_link()
        RETURNS trigger AS $$
        DECLARE f facts%ROWTYPE;
        DECLARE expected_type text;
        BEGIN
            SELECT * INTO f FROM facts WHERE id = NEW.fact_id;
            IF NOT FOUND THEN RAISE EXCEPTION 'transaction fact % not found', NEW.fact_id; END IF;
            expected_type := CASE NEW.relation_type WHEN 'CONTRACT' THEN 'CONTRACT' WHEN 'FULFILLMENT' THEN 'FULFILLMENT' WHEN 'INVOICE' THEN 'INVOICE' WHEN 'PAYMENT' THEN 'PAYMENT' END;
            IF f.fact_type <> expected_type THEN RAISE EXCEPTION 'relation_type % requires fact_type %, got %', NEW.relation_type, expected_type, f.fact_type; END IF;
            IF f.is_current IS NOT TRUE OR f.validation_status <> 'VALID' THEN RAISE EXCEPTION 'transaction links require current VALID facts'; END IF;
            IF NEW.relation_type='CONTRACT' AND NOT EXISTS (SELECT 1 FROM contract_facts WHERE fact_id=NEW.fact_id) THEN RAISE EXCEPTION 'CONTRACT fact % missing contract_facts row', NEW.fact_id; END IF;
            IF NEW.relation_type='FULFILLMENT' AND NOT EXISTS (SELECT 1 FROM fulfillment_facts WHERE fact_id=NEW.fact_id) THEN RAISE EXCEPTION 'FULFILLMENT fact % missing fulfillment_facts row', NEW.fact_id; END IF;
            IF NEW.relation_type='INVOICE' AND NOT EXISTS (SELECT 1 FROM invoice_facts WHERE fact_id=NEW.fact_id) THEN RAISE EXCEPTION 'INVOICE fact % missing invoice_facts row', NEW.fact_id; END IF;
            IF NEW.relation_type='PAYMENT' AND NOT EXISTS (SELECT 1 FROM payment_facts WHERE fact_id=NEW.fact_id) THEN RAISE EXCEPTION 'PAYMENT fact % missing payment_facts row', NEW.fact_id; END IF;
            IF TG_OP='INSERT' AND NEW.status='CONFIRMED' THEN RAISE EXCEPTION 'AUTO_CONFIRM is OFF: links must be inserted as CANDIDATE/NEEDS_REVIEW'; END IF;
            IF TG_OP='UPDATE' AND NEW.status='CONFIRMED' AND OLD.status<>'CONFIRMED' THEN
                IF OLD.status NOT IN ('CANDIDATE','NEEDS_REVIEW') THEN RAISE EXCEPTION 'only CANDIDATE/NEEDS_REVIEW links may be confirmed'; END IF;
                IF NEW.confirmed_by IS NULL OR NEW.confirmed_at IS NULL THEN RAISE EXCEPTION 'manual confirmation requires confirmed_by and confirmed_at'; END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("CREATE TRIGGER trg_v3_guard_transaction_fact_link BEFORE INSERT OR UPDATE OF fact_id, relation_type, status, confirmed_by, confirmed_at ON transaction_fact_links FOR EACH ROW EXECUTE FUNCTION v3_guard_transaction_fact_link()")
    op.execute("""
        CREATE FUNCTION v3_guard_transaction_participant()
        RETURNS trigger AS $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM transaction_fact_links l WHERE l.transaction_id=NEW.transaction_id AND l.fact_id=NEW.source_fact_id) THEN
                RAISE EXCEPTION 'participant source_fact % is not linked to transaction %', NEW.source_fact_id, NEW.transaction_id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("CREATE TRIGGER trg_v3_guard_transaction_participant BEFORE INSERT OR UPDATE OF transaction_id, source_fact_id ON transaction_participants FOR EACH ROW EXECUTE FUNCTION v3_guard_transaction_participant()")
    op.execute("""
        CREATE FUNCTION v3_guard_review_task_object()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.object_type='TRANSACTION_FACT_LINK' AND NOT EXISTS (SELECT 1 FROM transaction_fact_links WHERE id=NEW.object_id) THEN
                RAISE EXCEPTION 'review task link % not found', NEW.object_id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("CREATE TRIGGER trg_v3_guard_review_task_object BEFORE INSERT OR UPDATE OF object_type, object_id ON review_tasks FOR EACH ROW EXECUTE FUNCTION v3_guard_review_task_object()")


def downgrade() -> None:
    _require_postgresql()
    op.execute("DROP TRIGGER IF EXISTS trg_v3_guard_review_task_object ON review_tasks")
    op.execute("DROP FUNCTION IF EXISTS v3_guard_review_task_object()")
    op.execute("DROP TRIGGER IF EXISTS trg_v3_guard_transaction_participant ON transaction_participants")
    op.execute("DROP FUNCTION IF EXISTS v3_guard_transaction_participant()")
    op.execute("DROP TRIGGER IF EXISTS trg_v3_guard_transaction_fact_link ON transaction_fact_links")
    op.execute("DROP FUNCTION IF EXISTS v3_guard_transaction_fact_link()")
    op.drop_index("uq_review_tasks_open_object", table_name="review_tasks")
    op.drop_index("ix_review_tasks_status", table_name="review_tasks")
    op.drop_index("ix_review_tasks_object", table_name="review_tasks")
    op.drop_table("review_tasks")
    op.drop_index("ix_transaction_participants_party", table_name="transaction_participants")
    op.drop_index("ix_transaction_participants_transaction", table_name="transaction_participants")
    op.drop_table("transaction_participants")
    op.drop_index("ix_transaction_fact_links_status", table_name="transaction_fact_links")
    op.drop_index("ix_transaction_fact_links_fact", table_name="transaction_fact_links")
    op.drop_index("ix_transaction_fact_links_transaction", table_name="transaction_fact_links")
    op.drop_table("transaction_fact_links")
    op.drop_table("business_transactions")
