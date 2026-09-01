"""Task30 FactRelationship evidence audit ORM."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from . import v3_fact_models as _v3_fact_models  # noqa: F401
from . import v3_party_models as _v3_party_models  # noqa: F401
from .db import Base


class FactRelationshipEvidence(Base):
    __tablename__ = "fact_relationship_evidence"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    relationship_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("fact_relationships.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_document_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("source_documents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_extraction_id: Mapped[str] = mapped_column(String(160), nullable=False)
    evidence_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'EXPLICIT_REFERENCE'"),
    )
    reference_type: Mapped[str] = mapped_column(String(48), nullable=False)
    reference_value: Mapped[str] = mapped_column(String(240), nullable=False)
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False)
    page_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(6, 5), nullable=True)
    ruleset_version: Mapped[str] = mapped_column(String(48), nullable=False)
    evidence_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    submitted_by: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        UniqueConstraint(
            "relationship_id",
            "evidence_fingerprint",
            name="uq_fact_relationship_evidence_relationship_fingerprint",
        ),
        UniqueConstraint(
            "source_document_id",
            "source_extraction_id",
            "evidence_fingerprint",
            name="uq_fact_relationship_evidence_source_event_fingerprint",
        ),
        CheckConstraint(
            "evidence_type = 'EXPLICIT_REFERENCE'",
            name="ck_fact_relationship_evidence_type",
        ),
        CheckConstraint(
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
        CheckConstraint(
            "btrim(source_extraction_id) <> ''",
            name="ck_fact_relationship_evidence_extraction_nonempty",
        ),
        CheckConstraint(
            "btrim(reference_value) <> ''",
            name="ck_fact_relationship_evidence_reference_nonempty",
        ),
        CheckConstraint(
            "btrim(evidence_text) <> ''",
            name="ck_fact_relationship_evidence_text_nonempty",
        ),
        CheckConstraint(
            "page_no IS NULL OR page_no >= 1",
            name="ck_fact_relationship_evidence_page_positive",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_fact_relationship_evidence_confidence",
        ),
        CheckConstraint(
            "btrim(ruleset_version) <> ''",
            name="ck_fact_relationship_evidence_ruleset_nonempty",
        ),
        CheckConstraint(
            "evidence_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_fact_relationship_evidence_fingerprint",
        ),
        CheckConstraint(
            "btrim(submitted_by) <> ''",
            name="ck_fact_relationship_evidence_submitter_nonempty",
        ),
        Index("ix_fact_relationship_evidence_relationship_id", "relationship_id"),
        Index("ix_fact_relationship_evidence_source_document_id", "source_document_id"),
        Index(
            "ix_fact_relationship_evidence_source_extraction",
            "source_document_id",
            "source_extraction_id",
        ),
        Index("ix_fact_relationship_evidence_ruleset", "ruleset_version"),
    )
