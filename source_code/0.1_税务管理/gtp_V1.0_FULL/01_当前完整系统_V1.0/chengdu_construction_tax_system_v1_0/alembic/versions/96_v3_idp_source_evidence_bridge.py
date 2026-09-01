"""Task25 IDP Source Evidence Bridge & Canonical Evidence Completion.

Revision ID: 96_v3_idp_source_evidence_bridge
Revises: 95_v3_idp_canonical_ingest

Task25 only adds the IDP -> SourceDocument evidence-binding layer.

It deliberately does NOT alter:

* facts / invoice_facts / invoice_lines core schema
* Task24 canonical_ingest_receipts semantics
* writer/read/RAG cutover state
* Task23 Production Seal
* invoice identity or supersession rules
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "96_v3_idp_source_evidence_bridge"
down_revision = "95_v3_idp_canonical_ingest"
branch_labels = None
depends_on = None


def _require_postgresql() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("Task25 is PostgreSQL-only")


def upgrade() -> None:
    _require_postgresql()

    op.create_table(
        "idp_source_document_bindings",
        sa.Column(
            "id",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column(
            "source_system",
            sa.String(length=40),
            nullable=False,
        ),
        sa.Column(
            "source_document_id",
            sa.String(length=160),
            nullable=False,
        ),
        sa.Column(
            "document_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "source_document_pk",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "fact_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "first_source_extraction_id",
            sa.String(length=160),
            nullable=False,
        ),
        sa.Column(
            "first_receipt_id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "binding_status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'BOUND'"),
        ),
        sa.Column(
            "line_payload_fingerprint",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "validated_by",
            sa.String(length=80),
            nullable=True,
        ),
        sa.Column(
            "validation_reason",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["source_document_pk"],
            ["source_documents.id"],
            name="fk_idp_source_document_bindings_source_document",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["fact_id"],
            ["facts.id"],
            name="fk_idp_source_document_bindings_fact",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["first_receipt_id"],
            ["canonical_ingest_receipts.id"],
            name="fk_idp_source_document_bindings_receipt",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "source_system",
            "source_document_id",
            name="uq_idp_source_document_bindings_external_document",
        ),
        sa.UniqueConstraint(
            "source_document_pk",
            name="uq_idp_source_document_bindings_source_document_pk",
        ),
        sa.CheckConstraint(
            "btrim(source_system) <> ''",
            name="ck_idp_source_document_bindings_source_system_nonempty",
        ),
        sa.CheckConstraint(
            "btrim(source_document_id) <> ''",
            name="ck_idp_source_document_bindings_document_id_nonempty",
        ),
        sa.CheckConstraint(
            "btrim(first_source_extraction_id) <> ''",
            name="ck_idp_source_document_bindings_extraction_id_nonempty",
        ),
        sa.CheckConstraint(
            "document_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_idp_source_document_bindings_sha256",
        ),
        sa.CheckConstraint(
            "binding_status IN ('BOUND','VALIDATED')",
            name="ck_idp_source_document_bindings_status",
        ),
        sa.CheckConstraint(
            """
            binding_status = 'BOUND'
            OR (
                binding_status = 'VALIDATED'
                AND validated_by IS NOT NULL
                AND btrim(validated_by) <> ''
                AND validation_reason IS NOT NULL
                AND btrim(validation_reason) <> ''
            )
            """,
            name="ck_idp_source_document_bindings_validation_evidence",
        ),
        sa.CheckConstraint(
            """
            line_payload_fingerprint IS NULL
            OR line_payload_fingerprint ~ '^[0-9a-f]{64}$'
            """,
            name="ck_idp_source_document_bindings_line_fingerprint",
        ),
    )

    op.create_index(
        "ix_idp_source_document_bindings_source_document_pk",
        "idp_source_document_bindings",
        ["source_document_pk"],
    )
    op.create_index(
        "ix_idp_source_document_bindings_fact_id",
        "idp_source_document_bindings",
        ["fact_id"],
    )
    op.create_index(
        "ix_idp_source_document_bindings_first_receipt",
        "idp_source_document_bindings",
        ["first_receipt_id"],
    )
    op.create_index(
        "ix_idp_source_document_bindings_source_sha",
        "idp_source_document_bindings",
        ["source_system", "document_sha256"],
    )


def downgrade() -> None:
    _require_postgresql()

    op.drop_index(
        "ix_idp_source_document_bindings_source_sha",
        table_name="idp_source_document_bindings",
    )
    op.drop_index(
        "ix_idp_source_document_bindings_first_receipt",
        table_name="idp_source_document_bindings",
    )
    op.drop_index(
        "ix_idp_source_document_bindings_fact_id",
        table_name="idp_source_document_bindings",
    )
    op.drop_index(
        "ix_idp_source_document_bindings_source_document_pk",
        table_name="idp_source_document_bindings",
    )
    op.drop_table("idp_source_document_bindings")
