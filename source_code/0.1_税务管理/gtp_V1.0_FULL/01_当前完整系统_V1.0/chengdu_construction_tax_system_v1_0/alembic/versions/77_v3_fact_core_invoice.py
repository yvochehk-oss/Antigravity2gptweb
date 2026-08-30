"""Add Task 07a V3 Fact supertype and minimal Invoice Fact schema.

Revision ID: 77_v3_fact_core_invoice
Revises: 76_v3_party_migration_conflicts

This is schema-only. It does not migrate legacy invoices, switch readers, create
VAT claims, or add red/void relationships. Those remain later gated steps.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "77_v3_fact_core_invoice"
down_revision = "76_v3_party_migration_conflicts"
branch_labels = None
depends_on = None


def _require_postgresql() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL-only migration")


def upgrade() -> None:
    _require_postgresql()

    op.create_table(
        "facts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fact_type", sa.String(40), nullable=False),
        sa.Column("business_identity_key", sa.String(240), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "supersedes_fact_id",
            sa.Integer(),
            sa.ForeignKey("facts.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "validation_status",
            sa.String(20),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "validation_status IN ('DRAFT','VALID','INVALID','SUPERSEDED','NEEDS_REVIEW')",
            name="ck_facts_validation_status",
        ),
        sa.CheckConstraint("version_no >= 1", name="ck_facts_version_positive"),
        sa.CheckConstraint(
            "supersedes_fact_id IS NULL OR supersedes_fact_id <> id",
            name="ck_facts_not_self_supersede",
        ),
        sa.UniqueConstraint(
            "business_identity_key",
            "version_no",
            name="uq_facts_business_identity_version",
        ),
    )
    op.create_index("ix_facts_fact_type", "facts", ["fact_type"])
    op.create_index("ix_facts_validation_status", "facts", ["validation_status"])
    op.create_index("ix_facts_supersedes_fact_id", "facts", ["supersedes_fact_id"])
    op.create_index(
        "uq_facts_current_business_identity",
        "facts",
        ["business_identity_key"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )

    op.create_table(
        "invoice_facts",
        sa.Column(
            "fact_id",
            sa.Integer(),
            sa.ForeignKey("facts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "seller_party_id",
            sa.Integer(),
            sa.ForeignKey("parties.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "buyer_party_id",
            sa.Integer(),
            sa.ForeignKey("parties.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("invoice_identity_key", sa.String(240), nullable=False),
        sa.Column("invoice_identity_version", sa.String(24), nullable=False),
        sa.Column("invoice_number", sa.String(100), nullable=False),
        sa.Column("invoice_code", sa.String(60), nullable=True),
        sa.Column("invoice_type", sa.String(40), nullable=True),
        sa.Column("invoice_date", sa.Date(), nullable=True),
        sa.Column("invoice_status", sa.String(24), nullable=True),
        sa.Column("document_type", sa.String(40), nullable=True),
        sa.Column("gross_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("net_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("vat_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="CNY"),
        sa.UniqueConstraint("invoice_identity_key", name="uq_invoice_facts_identity_key"),
    )
    op.create_index("ix_invoice_facts_seller_party_id", "invoice_facts", ["seller_party_id"])
    op.create_index("ix_invoice_facts_buyer_party_id", "invoice_facts", ["buyer_party_id"])
    op.create_index("ix_invoice_facts_invoice_date", "invoice_facts", ["invoice_date"])

    op.create_table(
        "invoice_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "invoice_fact_id",
            sa.Integer(),
            sa.ForeignKey("invoice_facts.fact_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("item_name", sa.String(300), nullable=True),
        sa.Column("category", sa.String(80), nullable=True),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=True),
        sa.Column("unit_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("net_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("vat_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("tax_rate", sa.Numeric(8, 6), nullable=True),
        sa.Column("tax_classification_code", sa.String(80), nullable=True),
        sa.CheckConstraint("line_no >= 1", name="ck_invoice_lines_line_no_positive"),
        sa.UniqueConstraint(
            "invoice_fact_id",
            "line_no",
            name="uq_invoice_lines_fact_line_no",
        ),
    )
    op.create_index("ix_invoice_lines_invoice_fact_id", "invoice_lines", ["invoice_fact_id"])

    op.create_table(
        "fact_provenance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "fact_id",
            sa.Integer(),
            sa.ForeignKey("facts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("source_documents.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("chunk_id", sa.String(120), nullable=True),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Numeric(6, 5), nullable=True),
        sa.Column("extraction_model", sa.String(120), nullable=True),
        sa.Column("extraction_model_version", sa.String(80), nullable=True),
        sa.Column("original_extracted_value", sa.Text(), nullable=True),
        sa.Column("verified_value", sa.Text(), nullable=True),
        sa.Column("verifier_user_id", sa.String(80), nullable=True),
        sa.Column("verification_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_fact_provenance_confidence",
        ),
        sa.CheckConstraint(
            "page_start IS NULL OR page_end IS NULL OR page_end >= page_start",
            name="ck_fact_provenance_page_range",
        ),
    )
    op.create_index("ix_fact_provenance_fact_id", "fact_provenance", ["fact_id"])
    op.create_index("ix_fact_provenance_document_id", "fact_provenance", ["document_id"])

    op.create_table(
        "legacy_invoice_map",
        sa.Column(
            "legacy_invoice_id",
            sa.Integer(),
            sa.ForeignKey("invoices.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "invoice_fact_id",
            sa.Integer(),
            sa.ForeignKey("invoice_facts.fact_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("legacy_direction", sa.String(10), nullable=True),
        sa.Column("migration_status", sa.String(40), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "mapped_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "migration_status IN ('MIGRATED','MERGED','MIGRATED_SINGLE_PERSPECTIVE','NEEDS_REVIEW','REJECTED')",
            name="ck_legacy_invoice_map_status",
        ),
    )
    op.create_index(
        "ix_legacy_invoice_map_invoice_fact_id",
        "legacy_invoice_map",
        ["invoice_fact_id"],
    )
    op.create_index(
        "ix_legacy_invoice_map_status",
        "legacy_invoice_map",
        ["migration_status"],
    )


def downgrade() -> None:
    _require_postgresql()

    op.drop_index("ix_legacy_invoice_map_status", table_name="legacy_invoice_map")
    op.drop_index("ix_legacy_invoice_map_invoice_fact_id", table_name="legacy_invoice_map")
    op.drop_table("legacy_invoice_map")

    op.drop_index("ix_fact_provenance_document_id", table_name="fact_provenance")
    op.drop_index("ix_fact_provenance_fact_id", table_name="fact_provenance")
    op.drop_table("fact_provenance")

    op.drop_index("ix_invoice_lines_invoice_fact_id", table_name="invoice_lines")
    op.drop_table("invoice_lines")

    op.drop_index("ix_invoice_facts_invoice_date", table_name="invoice_facts")
    op.drop_index("ix_invoice_facts_buyer_party_id", table_name="invoice_facts")
    op.drop_index("ix_invoice_facts_seller_party_id", table_name="invoice_facts")
    op.drop_table("invoice_facts")

    op.drop_index("uq_facts_current_business_identity", table_name="facts")
    op.drop_index("ix_facts_supersedes_fact_id", table_name="facts")
    op.drop_index("ix_facts_validation_status", table_name="facts")
    op.drop_index("ix_facts_fact_type", table_name="facts")
    op.drop_table("facts")
