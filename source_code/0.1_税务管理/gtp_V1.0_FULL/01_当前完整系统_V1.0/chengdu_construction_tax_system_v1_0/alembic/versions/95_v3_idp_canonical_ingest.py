"""Task24 IDP -> Canonical intake receipt boundary.

Revision ID: 95_v3_idp_canonical_ingest
Revises: 94_v3_production_seal

This migration deliberately does not alter Tasks 1-23 core/cutover tables.
It only adds the application-integration receipt ledger used by Task24.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "95_v3_idp_canonical_ingest"
down_revision = "94_v3_production_seal"
branch_labels = None
depends_on = None


def _require_postgresql() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("Task24 is PostgreSQL-only")


def upgrade() -> None:
    _require_postgresql()

    op.create_table(
        "canonical_ingest_receipts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source_system", sa.String(length=40), nullable=False),
        sa.Column("source_document_id", sa.String(length=160), nullable=False),
        sa.Column("source_extraction_id", sa.String(length=160), nullable=False),
        sa.Column("document_sha256", sa.String(length=64), nullable=False),
        sa.Column("document_type", sa.String(length=40), nullable=False),
        sa.Column("business_identity_key", sa.String(length=240), nullable=False),
        sa.Column("identity_version", sa.String(length=40), nullable=False),
        sa.Column("normalization_version", sa.String(length=40), nullable=False),
        sa.Column("fact_id", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("source_payload_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("payload_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "canonical_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["fact_id"],
            ["facts.id"],
            name="fk_canonical_ingest_receipts_fact_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "source_system",
            "source_extraction_id",
            name="uq_canonical_ingest_receipts_source_extraction",
        ),
        sa.CheckConstraint(
            "source_system <> ''",
            name="ck_canonical_ingest_receipts_source_system_nonempty",
        ),
        sa.CheckConstraint(
            "source_document_id <> ''",
            name="ck_canonical_ingest_receipts_document_id_nonempty",
        ),
        sa.CheckConstraint(
            "source_extraction_id <> ''",
            name="ck_canonical_ingest_receipts_extraction_id_nonempty",
        ),
        sa.CheckConstraint(
            "char_length(document_sha256) = 64",
            name="ck_canonical_ingest_receipts_sha256_length",
        ),
        sa.CheckConstraint(
            "document_type IN ('invoice','contract')",
            name="ck_canonical_ingest_receipts_document_type",
        ),
        sa.CheckConstraint(
            "outcome IN ('CREATED','NOOP','REJECTED')",
            name="ck_canonical_ingest_receipts_outcome",
        ),
        sa.CheckConstraint(
            """
            (
                outcome = 'REJECTED'
                AND error_code IS NOT NULL
            )
            OR
            (
                outcome IN ('CREATED','NOOP')
                AND error_code IS NULL
            )
            """,
            name="ck_canonical_ingest_receipts_error_contract",
        ),
    )

    op.create_index(
        "ix_canonical_ingest_receipts_document_history",
        "canonical_ingest_receipts",
        ["source_system", "source_document_id", "source_extraction_id"],
    )
    op.create_index(
        "ix_canonical_ingest_receipts_business_identity",
        "canonical_ingest_receipts",
        ["business_identity_key"],
    )
    op.create_index(
        "ix_canonical_ingest_receipts_fact_id",
        "canonical_ingest_receipts",
        ["fact_id"],
    )


def downgrade() -> None:
    _require_postgresql()

    op.drop_index(
        "ix_canonical_ingest_receipts_fact_id",
        table_name="canonical_ingest_receipts",
    )
    op.drop_index(
        "ix_canonical_ingest_receipts_business_identity",
        table_name="canonical_ingest_receipts",
    )
    op.drop_index(
        "ix_canonical_ingest_receipts_document_history",
        table_name="canonical_ingest_receipts",
    )
    op.drop_table("canonical_ingest_receipts")
