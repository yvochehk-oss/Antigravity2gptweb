"""Add Input VAT Claim events; claim_period is the VAT attribution period.

Revision ID: 80_v3_input_vat_claims
Revises: 79_v3_legacy_invoice_pilot_bridge

Task 10 is additive. Legacy invoice ``period`` / ``deductible`` values are not
promoted into confirmed tax facts by this migration.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "80_v3_input_vat_claims"
down_revision = "79_v3_legacy_invoice_pilot_bridge"
branch_labels = None
depends_on = None


def _require_postgresql() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")


def upgrade() -> None:
    _require_postgresql()

    op.create_table(
        "input_vat_claims",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "invoice_fact_id",
            sa.Integer(),
            sa.ForeignKey("invoice_facts.fact_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "reporting_party_id",
            sa.Integer(),
            sa.ForeignKey("internal_entities.party_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("claim_period", sa.Date(), nullable=False),
        sa.Column("claim_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("event_type", sa.String(16), nullable=False, server_default="CLAIM"),
        sa.Column(
            "claim_status",
            sa.String(20),
            nullable=False,
            server_default="NEEDS_REVIEW",
        ),
        sa.Column("evidence_type", sa.String(24), nullable=False),
        sa.Column("confidence", sa.String(8), nullable=False),
        sa.Column(
            "source_document_id",
            sa.Integer(),
            sa.ForeignKey("source_documents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("source_system", sa.String(40), nullable=True),
        sa.Column("external_claim_id", sa.String(120), nullable=True),
        sa.Column("reviewed_by", sa.String(80), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "EXTRACT(DAY FROM claim_period) = 1",
            name="ck_input_vat_claims_period_month_start",
        ),
        sa.CheckConstraint(
            "claim_amount <> 0",
            name="ck_input_vat_claims_amount_nonzero",
        ),
        sa.CheckConstraint(
            "event_type IN ('CLAIM','REVERSAL','ADJUSTMENT')",
            name="ck_input_vat_claims_event_type",
        ),
        sa.CheckConstraint(
            "(event_type <> 'CLAIM' OR claim_amount > 0) AND "
            "(event_type <> 'REVERSAL' OR claim_amount < 0)",
            name="ck_input_vat_claims_event_sign",
        ),
        sa.CheckConstraint(
            "claim_status IN ('CONFIRMED','NEEDS_REVIEW','REJECTED','SUPERSEDED')",
            name="ck_input_vat_claims_status",
        ),
        sa.CheckConstraint(
            "evidence_type IN ('DOCUMENT_EVIDENCE','MANUAL_REVIEW','LEGACY_ASSUMPTION')",
            name="ck_input_vat_claims_evidence_type",
        ),
        sa.CheckConstraint(
            "confidence IN ('HIGH','MEDIUM','LOW')",
            name="ck_input_vat_claims_confidence",
        ),
        sa.CheckConstraint(
            "evidence_type <> 'DOCUMENT_EVIDENCE' OR source_document_id IS NOT NULL",
            name="ck_input_vat_claims_document_evidence_source",
        ),
        sa.CheckConstraint(
            "evidence_type <> 'LEGACY_ASSUMPTION' OR "
            "(confidence='LOW' AND claim_status='NEEDS_REVIEW')",
            name="ck_input_vat_claims_legacy_assumption_fail_closed",
        ),
        sa.CheckConstraint(
            "claim_status <> 'CONFIRMED' OR "
            "(evidence_type <> 'LEGACY_ASSUMPTION' AND reviewed_by IS NOT NULL "
            "AND btrim(reviewed_by) <> '' AND reviewed_at IS NOT NULL)",
            name="ck_input_vat_claims_confirmed_reviewed",
        ),
        sa.UniqueConstraint(
            "source_system",
            "external_claim_id",
            name="uq_input_vat_claims_source_identity",
        ),
    )
    op.create_index(
        "ix_input_vat_claims_invoice_fact_id",
        "input_vat_claims",
        ["invoice_fact_id"],
    )
    op.create_index(
        "ix_input_vat_claims_reporting_period_status",
        "input_vat_claims",
        ["reporting_party_id", "claim_period", "claim_status"],
    )
    op.create_index(
        "ix_input_vat_claims_source_document_id",
        "input_vat_claims",
        ["source_document_id"],
    )


def downgrade() -> None:
    _require_postgresql()
    op.drop_index("ix_input_vat_claims_source_document_id", table_name="input_vat_claims")
    op.drop_index("ix_input_vat_claims_reporting_period_status", table_name="input_vat_claims")
    op.drop_index("ix_input_vat_claims_invoice_fact_id", table_name="input_vat_claims")
    op.drop_table("input_vat_claims")
