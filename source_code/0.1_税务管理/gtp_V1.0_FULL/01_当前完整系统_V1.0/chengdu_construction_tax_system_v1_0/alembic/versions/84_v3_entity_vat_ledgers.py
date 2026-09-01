"""Add Output VAT attribution events and Entity VAT Ledger projections.

Revision ID: 84_v3_entity_vat_ledgers
Revises: 83_v3_calculation_runs_period_states

Input VAT remains sourced only from input_vat_claims.claim_period. Output VAT is
attributed by explicit output_vat_events.output_vat_period; invoice_date is not a
tax-period substitute. First-period opening credit requires reviewed evidence or
continuity from a prior Entity VAT Ledger.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "84_v3_entity_vat_ledgers"
down_revision = "83_v3_calculation_runs_period_states"
branch_labels = None
depends_on = None


def _require_postgresql() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")


def upgrade() -> None:
    _require_postgresql()

    op.create_table(
        "output_vat_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("invoice_fact_id", sa.Integer(), sa.ForeignKey("invoice_facts.fact_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reporting_party_id", sa.Integer(), sa.ForeignKey("internal_entities.party_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("output_vat_period", sa.Date(), nullable=False),
        sa.Column("vat_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("event_type", sa.String(16), nullable=False, server_default="OUTPUT"),
        sa.Column("event_status", sa.String(20), nullable=False, server_default="NEEDS_REVIEW"),
        sa.Column("evidence_type", sa.String(24), nullable=False),
        sa.Column("confidence", sa.String(8), nullable=False),
        sa.Column("source_document_id", sa.Integer(), sa.ForeignKey("source_documents.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("source_system", sa.String(40), nullable=True),
        sa.Column("external_event_id", sa.String(120), nullable=True),
        sa.Column("reviewed_by", sa.String(80), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("EXTRACT(DAY FROM output_vat_period) = 1", name="ck_output_vat_events_period_month_start"),
        sa.CheckConstraint("vat_amount <> 0", name="ck_output_vat_events_amount_nonzero"),
        sa.CheckConstraint("event_type IN ('OUTPUT','REVERSAL','ADJUSTMENT')", name="ck_output_vat_events_type"),
        sa.CheckConstraint("(event_type<>'OUTPUT' OR vat_amount>0) AND (event_type<>'REVERSAL' OR vat_amount<0) AND (event_type<>'ADJUSTMENT' OR vat_amount<>0)", name="ck_output_vat_events_sign"),
        sa.CheckConstraint("event_status IN ('CONFIRMED','NEEDS_REVIEW','REJECTED','SUPERSEDED')", name="ck_output_vat_events_status"),
        sa.CheckConstraint("evidence_type IN ('DOCUMENT_EVIDENCE','MANUAL_REVIEW','LEGACY_ASSUMPTION')", name="ck_output_vat_events_evidence_type"),
        sa.CheckConstraint("confidence IN ('HIGH','MEDIUM','LOW')", name="ck_output_vat_events_confidence"),
        sa.CheckConstraint("evidence_type<>'LEGACY_ASSUMPTION' OR (confidence='LOW' AND event_status='NEEDS_REVIEW')", name="ck_output_vat_events_legacy_fail_closed"),
        sa.CheckConstraint("evidence_type<>'DOCUMENT_EVIDENCE' OR source_document_id IS NOT NULL", name="ck_output_vat_events_document_source"),
        sa.CheckConstraint("event_status<>'CONFIRMED' OR (evidence_type<>'LEGACY_ASSUMPTION' AND reviewed_by IS NOT NULL AND btrim(reviewed_by)<>'' AND reviewed_at IS NOT NULL)", name="ck_output_vat_events_confirmed_reviewed"),
        sa.UniqueConstraint("source_system", "external_event_id", name="uq_output_vat_events_source_identity"),
    )
    op.create_index("ix_output_vat_events_invoice_fact_id", "output_vat_events", ["invoice_fact_id"])
    op.create_index("ix_output_vat_events_reporting_period_status", "output_vat_events", ["reporting_party_id", "output_vat_period", "event_status"])
    op.create_index("ix_output_vat_events_source_document_id", "output_vat_events", ["source_document_id"])

    op.create_table(
        "vat_opening_balance_seeds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("reporting_party_id", sa.Integer(), sa.ForeignKey("internal_entities.party_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("tax_period", sa.Date(), nullable=False),
        sa.Column("opening_input_credit", sa.Numeric(18, 2), nullable=False),
        sa.Column("source_document_id", sa.Integer(), sa.ForeignKey("source_documents.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("source", sa.String(160), nullable=False),
        sa.Column("reviewed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reviewed_by", sa.String(80), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("EXTRACT(DAY FROM tax_period) = 1", name="ck_vat_opening_seeds_period_month_start"),
        sa.CheckConstraint("opening_input_credit >= 0", name="ck_vat_opening_seeds_nonnegative"),
        sa.CheckConstraint("NOT reviewed OR (reviewed_by IS NOT NULL AND btrim(reviewed_by)<>'' AND reviewed_at IS NOT NULL)", name="ck_vat_opening_seeds_reviewed_evidence"),
        sa.UniqueConstraint("reporting_party_id", "tax_period", name="uq_vat_opening_seeds_scope"),
    )
    op.create_index("ix_vat_opening_seeds_source_document", "vat_opening_balance_seeds", ["source_document_id"])

    op.create_table(
        "entity_vat_ledgers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("calculation_run_id", sa.Integer(), sa.ForeignKey("calculation_runs.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("reporting_party_id", sa.Integer(), sa.ForeignKey("internal_entities.party_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("tax_period", sa.Date(), nullable=False),
        sa.Column("opening_input_credit", sa.Numeric(18, 2), nullable=False),
        sa.Column("output_vat", sa.Numeric(18, 2), nullable=False),
        sa.Column("input_vat", sa.Numeric(18, 2), nullable=False),
        sa.Column("tax_prepayment", sa.Numeric(18, 2), nullable=False),
        sa.Column("vat_payable_before_prepayment", sa.Numeric(18, 2), nullable=False),
        sa.Column("closing_input_credit", sa.Numeric(18, 2), nullable=False),
        sa.Column("vat_payable_after_prepayment", sa.Numeric(18, 2), nullable=False),
        sa.Column("unapplied_tax_prepayment", sa.Numeric(18, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("EXTRACT(DAY FROM tax_period) = 1", name="ck_entity_vat_ledgers_period_month_start"),
        sa.CheckConstraint("opening_input_credit >= 0 AND closing_input_credit >= 0", name="ck_entity_vat_ledgers_credit_nonnegative"),
        sa.CheckConstraint("vat_payable_before_prepayment >= 0 AND vat_payable_after_prepayment >= 0 AND unapplied_tax_prepayment >= 0", name="ck_entity_vat_ledgers_payable_nonnegative"),
        sa.UniqueConstraint("reporting_party_id", "tax_period", "calculation_run_id", name="uq_entity_vat_ledgers_scope_run"),
    )
    op.create_index("ix_entity_vat_ledgers_scope", "entity_vat_ledgers", ["reporting_party_id", "tax_period"])

    op.create_table(
        "entity_vat_ledger_components",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ledger_id", sa.Integer(), sa.ForeignKey("entity_vat_ledgers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("component_type", sa.String(28), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("output_vat_event_id", sa.Integer(), sa.ForeignKey("output_vat_events.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("input_vat_claim_id", sa.Integer(), sa.ForeignKey("input_vat_claims.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("tax_prepayment_fact_id", sa.Integer(), sa.ForeignKey("tax_prepayment_facts.fact_id", ondelete="RESTRICT"), nullable=True),
        sa.Column("prior_ledger_id", sa.Integer(), sa.ForeignKey("entity_vat_ledgers.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("opening_balance_seed_id", sa.Integer(), sa.ForeignKey("vat_opening_balance_seeds.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("component_type IN ('OPENING_INPUT_CREDIT','OUTPUT_VAT','INPUT_VAT','TAX_PREPAYMENT')", name="ck_entity_vat_ledger_components_type"),
        sa.CheckConstraint(
            "(component_type='OUTPUT_VAT' AND output_vat_event_id IS NOT NULL AND input_vat_claim_id IS NULL AND tax_prepayment_fact_id IS NULL AND prior_ledger_id IS NULL AND opening_balance_seed_id IS NULL) OR "
            "(component_type='INPUT_VAT' AND output_vat_event_id IS NULL AND input_vat_claim_id IS NOT NULL AND tax_prepayment_fact_id IS NULL AND prior_ledger_id IS NULL AND opening_balance_seed_id IS NULL) OR "
            "(component_type='TAX_PREPAYMENT' AND output_vat_event_id IS NULL AND input_vat_claim_id IS NULL AND tax_prepayment_fact_id IS NOT NULL AND prior_ledger_id IS NULL AND opening_balance_seed_id IS NULL) OR "
            "(component_type='OPENING_INPUT_CREDIT' AND output_vat_event_id IS NULL AND input_vat_claim_id IS NULL AND tax_prepayment_fact_id IS NULL AND ((prior_ledger_id IS NOT NULL AND opening_balance_seed_id IS NULL) OR (prior_ledger_id IS NULL AND opening_balance_seed_id IS NOT NULL)))",
            name="ck_entity_vat_ledger_components_typed_source",
        ),
        sa.UniqueConstraint("ledger_id", "output_vat_event_id", name="uq_entity_vat_component_output_event"),
        sa.UniqueConstraint("ledger_id", "input_vat_claim_id", name="uq_entity_vat_component_input_claim"),
        sa.UniqueConstraint("ledger_id", "tax_prepayment_fact_id", name="uq_entity_vat_component_prepayment_fact"),
    )
    op.create_index("ix_entity_vat_components_ledger", "entity_vat_ledger_components", ["ledger_id"])
    op.create_index("ix_entity_vat_components_prior_ledger", "entity_vat_ledger_components", ["prior_ledger_id"])


def downgrade() -> None:
    _require_postgresql()
    op.drop_index("ix_entity_vat_components_prior_ledger", table_name="entity_vat_ledger_components")
    op.drop_index("ix_entity_vat_components_ledger", table_name="entity_vat_ledger_components")
    op.drop_table("entity_vat_ledger_components")
    op.drop_index("ix_entity_vat_ledgers_scope", table_name="entity_vat_ledgers")
    op.drop_table("entity_vat_ledgers")
    op.drop_index("ix_vat_opening_seeds_source_document", table_name="vat_opening_balance_seeds")
    op.drop_table("vat_opening_balance_seeds")
    op.drop_index("ix_output_vat_events_source_document_id", table_name="output_vat_events")
    op.drop_index("ix_output_vat_events_reporting_period_status", table_name="output_vat_events")
    op.drop_index("ix_output_vat_events_invoice_fact_id", table_name="output_vat_events")
    op.drop_table("output_vat_events")
