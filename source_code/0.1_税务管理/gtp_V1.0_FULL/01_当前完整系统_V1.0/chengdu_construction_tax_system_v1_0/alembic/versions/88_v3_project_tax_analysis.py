"""Add Task17 Fact Project Allocation and Project Tax Analysis.

Revision ID: 88_v3_project_tax_analysis
Revises: 87_v3_entity_tax_ledgers
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "88_v3_project_tax_analysis"
down_revision = "87_v3_entity_tax_ledgers"
branch_labels = None
depends_on = None


def _require_postgresql() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")


def upgrade() -> None:
    _require_postgresql()
    op.create_table(
        "fact_project_allocations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fact_id", sa.Integer(), sa.ForeignKey("facts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("allocation_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("supersedes_allocation_id", sa.Integer(), sa.ForeignKey("fact_project_allocations.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("allocated_net", sa.Numeric(18, 2), nullable=False),
        sa.Column("allocated_vat", sa.Numeric(18, 2), nullable=False),
        sa.Column("allocated_gross", sa.Numeric(18, 2), nullable=False),
        sa.Column("allocation_method", sa.String(24), nullable=False),
        sa.Column("confidence", sa.String(8), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="CANDIDATE"),
        sa.Column("proposal_source", sa.String(16), nullable=False, server_default="HUMAN"),
        sa.Column("source_document_id", sa.Integer(), sa.ForeignKey("source_documents.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("rule_version", sa.String(64), nullable=True),
        sa.Column("reviewed_by", sa.String(80), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("allocation_version >= 1", name="ck_fact_project_allocations_version_positive"),
        sa.CheckConstraint("supersedes_allocation_id IS NULL OR supersedes_allocation_id <> id", name="ck_fact_project_allocations_not_self_supersede"),
        sa.CheckConstraint("abs((allocated_net + allocated_vat) - allocated_gross) <= 0.01", name="ck_fact_project_allocations_amount_balance"),
        sa.CheckConstraint("allocation_method IN ('EXPLICIT','SOURCE_DOCUMENT','MANUAL','PROPORTIONAL','RULE_BASED')", name="ck_fact_project_allocations_method"),
        sa.CheckConstraint("confidence IN ('HIGH','MEDIUM','LOW')", name="ck_fact_project_allocations_confidence"),
        sa.CheckConstraint("status IN ('CANDIDATE','NEEDS_REVIEW','CONFIRMED','REJECTED','SUPERSEDED')", name="ck_fact_project_allocations_status"),
        sa.CheckConstraint("proposal_source IN ('HUMAN','DOCUMENT','RULE_ENGINE','AI','LEGACY')", name="ck_fact_project_allocations_proposal_source"),
        sa.CheckConstraint("allocation_method <> 'SOURCE_DOCUMENT' OR source_document_id IS NOT NULL", name="ck_fact_project_allocations_source_document"),
        sa.CheckConstraint("allocation_method <> 'RULE_BASED' OR (rule_version IS NOT NULL AND btrim(rule_version) <> '')", name="ck_fact_project_allocations_rule_version"),
        sa.CheckConstraint("status NOT IN ('CONFIRMED','REJECTED','SUPERSEDED') OR (reviewed_by IS NOT NULL AND btrim(reviewed_by) <> '' AND reviewed_at IS NOT NULL)", name="ck_fact_project_allocations_reviewed_evidence"),
        sa.CheckConstraint("status NOT IN ('REJECTED','SUPERSEDED') OR NOT is_current", name="ck_fact_project_allocations_resolved_not_current"),
        sa.UniqueConstraint("fact_id", "project_id", "allocation_version", name="uq_fact_project_allocations_fact_project_version"),
    )
    op.create_index("ix_fact_project_allocations_fact_status", "fact_project_allocations", ["fact_id", "status", "is_current"])
    op.create_index("ix_fact_project_allocations_project_status", "fact_project_allocations", ["project_id", "status", "is_current"])
    op.create_index("uq_fact_project_allocations_current", "fact_project_allocations", ["fact_id", "project_id"], unique=True, postgresql_where=sa.text("is_current"))

    op.create_table(
        "project_tax_analysis",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("calculation_run_id", sa.Integer(), sa.ForeignKey("calculation_runs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reporting_party_id", sa.Integer(), sa.ForeignKey("internal_entities.party_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("tax_period", sa.Date(), nullable=False),
        sa.Column("tax_type", sa.String(32), nullable=False, server_default="VAT"),
        sa.Column("basis", sa.String(12), nullable=False, server_default="TAX"),
        sa.Column("output_taxable_net", sa.Numeric(18, 2), nullable=False),
        sa.Column("output_vat", sa.Numeric(18, 2), nullable=False),
        sa.Column("claimed_input_vat", sa.Numeric(18, 2), nullable=False),
        sa.Column("tax_prepayment", sa.Numeric(18, 2), nullable=False),
        sa.Column("net_vat_before_entity_credit", sa.Numeric(18, 2), nullable=False),
        sa.Column("net_vat_after_project_prepayment", sa.Numeric(18, 2), nullable=False),
        sa.Column("allocation_coverage_status", sa.String(12), nullable=False),
        sa.Column("input_snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("result_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("EXTRACT(DAY FROM tax_period) = 1", name="ck_project_tax_analysis_period_month_start"),
        sa.CheckConstraint("basis = 'TAX'", name="ck_project_tax_analysis_tax_basis"),
        sa.CheckConstraint("allocation_coverage_status IN ('FULL','PARTIAL','NONE')", name="ck_project_tax_analysis_coverage_status"),
        sa.CheckConstraint("net_vat_before_entity_credit = output_vat - claimed_input_vat", name="ck_project_tax_analysis_net_before_credit_formula"),
        sa.CheckConstraint("net_vat_after_project_prepayment = net_vat_before_entity_credit - tax_prepayment", name="ck_project_tax_analysis_after_prepayment_formula"),
        sa.CheckConstraint("length(input_snapshot_sha256) = 64", name="ck_project_tax_analysis_input_hash_length"),
        sa.CheckConstraint("length(result_sha256) = 64", name="ck_project_tax_analysis_result_hash_length"),
        sa.UniqueConstraint("calculation_run_id", "project_id", "tax_type", name="uq_project_tax_analysis_run_project_tax_type"),
    )
    op.create_index("ix_project_tax_analysis_scope", "project_tax_analysis", ["reporting_party_id", "tax_period", "project_id", "tax_type"])

    op.create_table(
        "project_tax_analysis_components",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("analysis_id", sa.Integer(), sa.ForeignKey("project_tax_analysis.id", ondelete="CASCADE"), nullable=False),
        sa.Column("component_type", sa.String(20), nullable=False),
        sa.Column("taxable_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("tax_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("fact_project_allocation_id", sa.Integer(), sa.ForeignKey("fact_project_allocations.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("output_vat_event_id", sa.Integer(), sa.ForeignKey("output_vat_events.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("input_vat_claim_id", sa.Integer(), sa.ForeignKey("input_vat_claims.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("tax_prepayment_fact_id", sa.Integer(), sa.ForeignKey("tax_prepayment_facts.fact_id", ondelete="RESTRICT"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("component_type IN ('OUTPUT_VAT','INPUT_VAT','TAX_PREPAYMENT')", name="ck_project_tax_analysis_components_type"),
        sa.CheckConstraint("(component_type='OUTPUT_VAT' AND fact_project_allocation_id IS NOT NULL AND output_vat_event_id IS NOT NULL AND input_vat_claim_id IS NULL AND tax_prepayment_fact_id IS NULL) OR (component_type='INPUT_VAT' AND fact_project_allocation_id IS NOT NULL AND output_vat_event_id IS NULL AND input_vat_claim_id IS NOT NULL AND tax_prepayment_fact_id IS NULL) OR (component_type='TAX_PREPAYMENT' AND fact_project_allocation_id IS NULL AND output_vat_event_id IS NULL AND input_vat_claim_id IS NULL AND tax_prepayment_fact_id IS NOT NULL)", name="ck_project_tax_analysis_components_typed_source"),
        sa.UniqueConstraint("analysis_id", "fact_project_allocation_id", "output_vat_event_id", name="uq_project_tax_analysis_component_output"),
        sa.UniqueConstraint("analysis_id", "fact_project_allocation_id", "input_vat_claim_id", name="uq_project_tax_analysis_component_input"),
        sa.UniqueConstraint("analysis_id", "tax_prepayment_fact_id", name="uq_project_tax_analysis_component_prepayment"),
    )
    op.create_index("ix_project_tax_analysis_components_analysis", "project_tax_analysis_components", ["analysis_id"])

    op.execute("""
        CREATE FUNCTION v3_guard_ai_project_allocation_insert()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.proposal_source = 'AI' AND NEW.status <> 'CANDIDATE' THEN
                RAISE EXCEPTION 'AI project-allocation proposals must be inserted as CANDIDATE';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_v3_guard_ai_project_allocation_insert
        BEFORE INSERT ON fact_project_allocations
        FOR EACH ROW EXECUTE FUNCTION v3_guard_ai_project_allocation_insert()
    """)
    op.execute("""
        CREATE FUNCTION v3_guard_project_tax_analysis_scope()
        RETURNS trigger AS $$
        DECLARE r calculation_runs%ROWTYPE; e internal_entities%ROWTYPE;
        BEGIN
            SELECT * INTO e FROM internal_entities WHERE party_id = NEW.reporting_party_id;
            IF NOT FOUND OR e.legal_entity IS NOT TRUE THEN
                RAISE EXCEPTION 'project tax analysis requires a legal reporting party';
            END IF;
            SELECT * INTO r FROM calculation_runs WHERE id = NEW.calculation_run_id;
            IF NOT FOUND OR r.run_status <> 'SUCCEEDED' OR r.tax_type <> 'PROJECT_TAX' OR r.reporting_party_id <> NEW.reporting_party_id OR r.tax_period <> NEW.tax_period THEN
                RAISE EXCEPTION 'project tax analysis scope does not match a SUCCEEDED PROJECT_TAX run';
            END IF;
            IF NEW.basis <> 'TAX' THEN
                RAISE EXCEPTION 'project tax analysis must use TAX basis';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_v3_guard_project_tax_analysis_scope
        BEFORE INSERT OR UPDATE OF calculation_run_id, reporting_party_id, tax_period, basis
        ON project_tax_analysis
        FOR EACH ROW EXECUTE FUNCTION v3_guard_project_tax_analysis_scope()
    """)


def downgrade() -> None:
    _require_postgresql()
    op.execute("DROP TRIGGER IF EXISTS trg_v3_guard_project_tax_analysis_scope ON project_tax_analysis")
    op.execute("DROP FUNCTION IF EXISTS v3_guard_project_tax_analysis_scope()")
    op.execute("DROP TRIGGER IF EXISTS trg_v3_guard_ai_project_allocation_insert ON fact_project_allocations")
    op.execute("DROP FUNCTION IF EXISTS v3_guard_ai_project_allocation_insert()")
    op.drop_index("ix_project_tax_analysis_components_analysis", table_name="project_tax_analysis_components")
    op.drop_table("project_tax_analysis_components")
    op.drop_index("ix_project_tax_analysis_scope", table_name="project_tax_analysis")
    op.drop_table("project_tax_analysis")
    op.drop_index("uq_fact_project_allocations_current", table_name="fact_project_allocations")
    op.drop_index("ix_fact_project_allocations_project_status", table_name="fact_project_allocations")
    op.drop_index("ix_fact_project_allocations_fact_status", table_name="fact_project_allocations")
    op.drop_table("fact_project_allocations")
