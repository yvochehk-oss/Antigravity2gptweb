"""Task30 Explicit Canonical Fact Relationship & Four-Flow Graph Completion.

Revision ID: 98_v3_explicit_fact_relationship_graph
Revises: 97_v3_contract_role_semantics

Extends the existing FactRelationship type vocabulary and adds append-only
SourceDocument-backed relationship evidence.  It does not modify Fact identity,
Fact validation, Project attribution, Production Seal, or cutover state.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "98_v3_explicit_fact_relationship_graph"
down_revision = "97_v3_contract_role_semantics"
branch_labels = None
depends_on = None


_OLD_TYPES = ("REVERSAL_OF", "REPLACES", "VOID_RELATION", "CORRECTS")
_NEW_TYPES = _OLD_TYPES + (
    "INVOICE_FOR_CONTRACT",
    "PAYMENT_FOR_INVOICE",
    "PAYMENT_FOR_CONTRACT",
)


def _require_postgresql() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("Task30 is PostgreSQL-only")


def _relationship_type_check(types: tuple[str, ...]) -> str:
    rendered = ",".join(f"'{value}'" for value in types)
    return f"relationship_type IN ({rendered})"


def upgrade() -> None:
    _require_postgresql()

    op.drop_constraint(
        "ck_fact_relationships_type",
        "fact_relationships",
        type_="check",
    )
    op.create_check_constraint(
        "ck_fact_relationships_type",
        "fact_relationships",
        _relationship_type_check(_NEW_TYPES),
    )

    op.create_table(
        "fact_relationship_evidence",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("relationship_id", sa.Integer(), nullable=False),
        sa.Column("source_document_id", sa.Integer(), nullable=False),
        sa.Column("source_extraction_id", sa.String(length=160), nullable=False),
        sa.Column(
            "evidence_type",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'EXPLICIT_REFERENCE'"),
        ),
        sa.Column("reference_type", sa.String(length=48), nullable=False),
        sa.Column("reference_value", sa.String(length=240), nullable=False),
        sa.Column("evidence_text", sa.Text(), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Numeric(6, 5), nullable=True),
        sa.Column("ruleset_version", sa.String(length=48), nullable=False),
        sa.Column("evidence_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("submitted_by", sa.String(length=80), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["relationship_id"],
            ["fact_relationships.id"],
            name="fk_fact_relationship_evidence_relationship",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["source_documents.id"],
            name="fk_fact_relationship_evidence_source_document",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "relationship_id",
            "evidence_fingerprint",
            name="uq_fact_relationship_evidence_relationship_fingerprint",
        ),
        sa.UniqueConstraint(
            "source_document_id",
            "source_extraction_id",
            "evidence_fingerprint",
            name="uq_fact_relationship_evidence_source_event_fingerprint",
        ),
        sa.CheckConstraint(
            "evidence_type = 'EXPLICIT_REFERENCE'",
            name="ck_fact_relationship_evidence_type",
        ),
        sa.CheckConstraint(
            """
            reference_type IN (
                'TARGET_FACT_ID',
                'BUSINESS_IDENTITY_KEY',
                'INVOICE_IDENTITY_KEY',
                'CONTRACT_BUSINESS_IDENTITY_KEY'
            )
            """,
            name="ck_fact_relationship_evidence_reference_type",
        ),
        sa.CheckConstraint(
            "btrim(source_extraction_id) <> ''",
            name="ck_fact_relationship_evidence_extraction_nonempty",
        ),
        sa.CheckConstraint(
            "btrim(reference_value) <> ''",
            name="ck_fact_relationship_evidence_reference_nonempty",
        ),
        sa.CheckConstraint(
            "btrim(evidence_text) <> ''",
            name="ck_fact_relationship_evidence_text_nonempty",
        ),
        sa.CheckConstraint(
            "page_no IS NULL OR page_no >= 1",
            name="ck_fact_relationship_evidence_page_positive",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_fact_relationship_evidence_confidence",
        ),
        sa.CheckConstraint(
            "btrim(ruleset_version) <> ''",
            name="ck_fact_relationship_evidence_ruleset_nonempty",
        ),
        sa.CheckConstraint(
            "evidence_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_fact_relationship_evidence_fingerprint",
        ),
        sa.CheckConstraint(
            "btrim(submitted_by) <> ''",
            name="ck_fact_relationship_evidence_submitter_nonempty",
        ),
    )

    op.create_index(
        "ix_fact_relationship_evidence_relationship_id",
        "fact_relationship_evidence",
        ["relationship_id"],
    )
    op.create_index(
        "ix_fact_relationship_evidence_source_document_id",
        "fact_relationship_evidence",
        ["source_document_id"],
    )
    op.create_index(
        "ix_fact_relationship_evidence_source_extraction",
        "fact_relationship_evidence",
        ["source_document_id", "source_extraction_id"],
    )
    op.create_index(
        "ix_fact_relationship_evidence_ruleset",
        "fact_relationship_evidence",
        ["ruleset_version"],
    )


def downgrade() -> None:
    _require_postgresql()

    op.drop_index(
        "ix_fact_relationship_evidence_ruleset",
        table_name="fact_relationship_evidence",
    )
    op.drop_index(
        "ix_fact_relationship_evidence_source_extraction",
        table_name="fact_relationship_evidence",
    )
    op.drop_index(
        "ix_fact_relationship_evidence_source_document_id",
        table_name="fact_relationship_evidence",
    )
    op.drop_index(
        "ix_fact_relationship_evidence_relationship_id",
        table_name="fact_relationship_evidence",
    )
    op.drop_table("fact_relationship_evidence")

    op.drop_constraint(
        "ck_fact_relationships_type",
        "fact_relationships",
        type_="check",
    )
    op.create_check_constraint(
        "ck_fact_relationships_type",
        "fact_relationships",
        _relationship_type_check(_OLD_TYPES),
    )
