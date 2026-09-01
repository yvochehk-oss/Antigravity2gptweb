"""Task26 Contract Role & Semantic Completion.

Revision ID: 97_v3_contract_role_semantics
Revises: 96_v3_idp_source_evidence_bridge

Adds append-only Contract legal-role evidence and versioned role-resolution
history without changing Task24 Fact identity, Fact versioning/supersession,
Production Seal, or cutover state.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "97_v3_contract_role_semantics"
down_revision = "96_v3_idp_source_evidence_bridge"
branch_labels = None
depends_on = None


def _require_postgresql() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("Task26 is PostgreSQL-only")


def upgrade() -> None:
    _require_postgresql()

    op.create_table(
        "contract_role_evidence",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("fact_id", sa.Integer(), nullable=False),
        sa.Column("receipt_id", sa.BigInteger(), nullable=False),
        sa.Column("source_system", sa.String(length=40), nullable=False),
        sa.Column("source_document_id", sa.String(length=160), nullable=False),
        sa.Column("source_extraction_id", sa.String(length=160), nullable=False),
        sa.Column("document_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_party_role", sa.String(length=16), nullable=False),
        sa.Column("party_id", sa.Integer(), nullable=False),
        sa.Column("evidence_type", sa.String(length=32), nullable=False),
        sa.Column("legal_role_label", sa.String(length=160), nullable=False),
        sa.Column("evidence_text", sa.Text(), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Numeric(6, 5), nullable=True),
        sa.Column("canonical_role", sa.String(length=16), nullable=True),
        sa.Column("classification_status", sa.String(length=20), nullable=False),
        sa.Column("classification_code", sa.String(length=80), nullable=False),
        sa.Column("ruleset_version", sa.String(length=40), nullable=False),
        sa.Column("evidence_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("submitted_by", sa.String(length=80), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["fact_id"],
            ["contract_facts.fact_id"],
            name="fk_contract_role_evidence_fact",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["receipt_id"],
            ["canonical_ingest_receipts.id"],
            name="fk_contract_role_evidence_receipt",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["party_id"],
            ["parties.id"],
            name="fk_contract_role_evidence_party",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "fact_id",
            "evidence_fingerprint",
            name="uq_contract_role_evidence_fact_fingerprint",
        ),
        sa.CheckConstraint(
            "source_party_role IN ('PARTY_A','PARTY_B')",
            name="ck_contract_role_evidence_source_role",
        ),
        sa.CheckConstraint(
            "evidence_type IN ('LEGAL_ROLE_LABEL','CONTRACT_CLAUSE_ROLE')",
            name="ck_contract_role_evidence_type",
        ),
        sa.CheckConstraint(
            "canonical_role IS NULL OR canonical_role IN ('BUYER','SELLER')",
            name="ck_contract_role_evidence_canonical_role",
        ),
        sa.CheckConstraint(
            "classification_status IN ('MAPPED','UNSUPPORTED','AMBIGUOUS','INVALID')",
            name="ck_contract_role_evidence_classification",
        ),
        sa.CheckConstraint(
            """
            (
                classification_status = 'MAPPED'
                AND canonical_role IN ('BUYER','SELLER')
            )
            OR
            (
                classification_status <> 'MAPPED'
                AND canonical_role IS NULL
            )
            """,
            name="ck_contract_role_evidence_mapping_shape",
        ),
        sa.CheckConstraint(
            "btrim(source_system) <> ''",
            name="ck_contract_role_evidence_source_system_nonempty",
        ),
        sa.CheckConstraint(
            "btrim(source_document_id) <> ''",
            name="ck_contract_role_evidence_document_nonempty",
        ),
        sa.CheckConstraint(
            "btrim(source_extraction_id) <> ''",
            name="ck_contract_role_evidence_extraction_nonempty",
        ),
        sa.CheckConstraint(
            "document_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_contract_role_evidence_sha256",
        ),
        sa.CheckConstraint(
            "btrim(legal_role_label) <> ''",
            name="ck_contract_role_evidence_label_nonempty",
        ),
        sa.CheckConstraint(
            "btrim(evidence_text) <> ''",
            name="ck_contract_role_evidence_text_nonempty",
        ),
        sa.CheckConstraint(
            "btrim(classification_code) <> ''",
            name="ck_contract_role_evidence_code_nonempty",
        ),
        sa.CheckConstraint(
            "btrim(ruleset_version) <> ''",
            name="ck_contract_role_evidence_ruleset_nonempty",
        ),
        sa.CheckConstraint(
            "evidence_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_contract_role_evidence_fingerprint",
        ),
        sa.CheckConstraint(
            "btrim(submitted_by) <> ''",
            name="ck_contract_role_evidence_submitter_nonempty",
        ),
        sa.CheckConstraint(
            "page_no IS NULL OR page_no >= 1",
            name="ck_contract_role_evidence_page_positive",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_contract_role_evidence_confidence",
        ),
    )

    op.create_index(
        "ix_contract_role_evidence_fact_id",
        "contract_role_evidence",
        ["fact_id"],
    )
    op.create_index(
        "ix_contract_role_evidence_receipt_id",
        "contract_role_evidence",
        ["receipt_id"],
    )
    op.create_index(
        "ix_contract_role_evidence_party_id",
        "contract_role_evidence",
        ["party_id"],
    )
    op.create_index(
        "ix_contract_role_evidence_source_document",
        "contract_role_evidence",
        ["source_system", "source_document_id", "source_extraction_id"],
    )
    op.create_index(
        "ix_contract_role_evidence_ruleset",
        "contract_role_evidence",
        ["ruleset_version"],
    )

    op.create_table(
        "contract_role_resolutions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("fact_id", sa.Integer(), nullable=False),
        sa.Column("receipt_id", sa.BigInteger(), nullable=False),
        sa.Column("resolution_seq", sa.Integer(), nullable=False),
        sa.Column(
            "is_current",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("supersedes_resolution_id", sa.BigInteger(), nullable=True),
        sa.Column("ruleset_version", sa.String(length=40), nullable=False),
        sa.Column("resolution_status", sa.String(length=20), nullable=False),
        sa.Column("buyer_party_id", sa.Integer(), nullable=True),
        sa.Column("seller_party_id", sa.Integer(), nullable=True),
        sa.Column("evidence_set_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("reason_code", sa.String(length=80), nullable=False),
        sa.Column("reason_detail", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["fact_id"],
            ["contract_facts.fact_id"],
            name="fk_contract_role_resolutions_fact",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["receipt_id"],
            ["canonical_ingest_receipts.id"],
            name="fk_contract_role_resolutions_receipt",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["buyer_party_id"],
            ["parties.id"],
            name="fk_contract_role_resolutions_buyer",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["seller_party_id"],
            ["parties.id"],
            name="fk_contract_role_resolutions_seller",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_resolution_id"],
            ["contract_role_resolutions.id"],
            name="fk_contract_role_resolutions_supersedes",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "fact_id",
            "resolution_seq",
            name="uq_contract_role_resolutions_fact_seq",
        ),
        sa.CheckConstraint(
            "resolution_seq >= 1",
            name="ck_contract_role_resolutions_seq_positive",
        ),
        sa.CheckConstraint(
            "resolution_status IN ('RESOLVED','NEEDS_REVIEW')",
            name="ck_contract_role_resolutions_status",
        ),
        sa.CheckConstraint(
            """
            (
                resolution_status = 'RESOLVED'
                AND buyer_party_id IS NOT NULL
                AND seller_party_id IS NOT NULL
                AND buyer_party_id <> seller_party_id
            )
            OR
            (
                resolution_status = 'NEEDS_REVIEW'
                AND buyer_party_id IS NULL
                AND seller_party_id IS NULL
            )
            """,
            name="ck_contract_role_resolutions_shape",
        ),
        sa.CheckConstraint(
            "supersedes_resolution_id IS NULL OR supersedes_resolution_id <> id",
            name="ck_contract_role_resolutions_not_self_supersede",
        ),
        sa.CheckConstraint(
            "btrim(ruleset_version) <> ''",
            name="ck_contract_role_resolutions_ruleset_nonempty",
        ),
        sa.CheckConstraint(
            "evidence_set_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_contract_role_resolutions_fingerprint",
        ),
        sa.CheckConstraint(
            "btrim(reason_code) <> ''",
            name="ck_contract_role_resolutions_reason_nonempty",
        ),
        sa.CheckConstraint(
            "btrim(reason_detail) <> ''",
            name="ck_contract_role_resolutions_detail_nonempty",
        ),
    )

    op.create_index(
        "uq_contract_role_resolutions_current_fact",
        "contract_role_resolutions",
        ["fact_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )
    op.create_index(
        "ix_contract_role_resolutions_buyer",
        "contract_role_resolutions",
        ["buyer_party_id"],
    )
    op.create_index(
        "ix_contract_role_resolutions_seller",
        "contract_role_resolutions",
        ["seller_party_id"],
    )
    op.create_index(
        "ix_contract_role_resolutions_status",
        "contract_role_resolutions",
        ["resolution_status"],
    )
    op.create_index(
        "ix_contract_role_resolutions_supersedes",
        "contract_role_resolutions",
        ["supersedes_resolution_id"],
    )


def downgrade() -> None:
    _require_postgresql()

    op.drop_index(
        "ix_contract_role_resolutions_supersedes",
        table_name="contract_role_resolutions",
    )
    op.drop_index(
        "ix_contract_role_resolutions_status",
        table_name="contract_role_resolutions",
    )
    op.drop_index(
        "ix_contract_role_resolutions_seller",
        table_name="contract_role_resolutions",
    )
    op.drop_index(
        "ix_contract_role_resolutions_buyer",
        table_name="contract_role_resolutions",
    )
    op.drop_index(
        "uq_contract_role_resolutions_current_fact",
        table_name="contract_role_resolutions",
    )
    op.drop_table("contract_role_resolutions")

    op.drop_index(
        "ix_contract_role_evidence_ruleset",
        table_name="contract_role_evidence",
    )
    op.drop_index(
        "ix_contract_role_evidence_source_document",
        table_name="contract_role_evidence",
    )
    op.drop_index(
        "ix_contract_role_evidence_party_id",
        table_name="contract_role_evidence",
    )
    op.drop_index(
        "ix_contract_role_evidence_receipt_id",
        table_name="contract_role_evidence",
    )
    op.drop_index(
        "ix_contract_role_evidence_fact_id",
        table_name="contract_role_evidence",
    )
    op.drop_table("contract_role_evidence")
