"""Task25 IDP SourceDocument evidence-binding ORM models."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

# Register referenced tables in the shared Base metadata.
from . import v3_fact_models as _v3_fact_models  # noqa: F401
from . import v3_integration_models as _v3_integration_models  # noqa: F401
from . import v3_party_models as _v3_party_models  # noqa: F401
from .db import Base


class IDPSourceDocumentBinding(Base):
    """Stable mapping from an IDP document to a Canonical SourceDocument.

    Task25 uses this record to prove that:

    * the external IDP document was registered exactly once;
    * FactProvenance.document_id points to a real source_documents.id;
    * the same IDP document cannot silently move to another Fact;
    * once line evidence is established, a different line payload fails closed.
    """

    __tablename__ = "idp_source_document_bindings"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    source_system: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )
    source_document_id: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
    )
    document_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    source_document_pk: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "source_documents.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    fact_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "facts.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    first_source_extraction_id: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
    )
    first_receipt_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "canonical_ingest_receipts.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    binding_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'BOUND'"),
    )

    line_payload_fingerprint: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    validated_by: Mapped[str | None] = mapped_column(
        String(80),
        nullable=True,
    )
    validation_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        UniqueConstraint(
            "source_system",
            "source_document_id",
            name="uq_idp_source_document_bindings_external_document",
        ),
        UniqueConstraint(
            "source_document_pk",
            name="uq_idp_source_document_bindings_source_document_pk",
        ),
        CheckConstraint(
            "btrim(source_system) <> ''",
            name="ck_idp_source_document_bindings_source_system_nonempty",
        ),
        CheckConstraint(
            "btrim(source_document_id) <> ''",
            name="ck_idp_source_document_bindings_document_id_nonempty",
        ),
        CheckConstraint(
            "btrim(first_source_extraction_id) <> ''",
            name="ck_idp_source_document_bindings_extraction_id_nonempty",
        ),
        CheckConstraint(
            "document_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_idp_source_document_bindings_sha256",
        ),
        CheckConstraint(
            "binding_status IN ('BOUND','VALIDATED')",
            name="ck_idp_source_document_bindings_status",
        ),
        CheckConstraint(
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
        CheckConstraint(
            """
            line_payload_fingerprint IS NULL
            OR line_payload_fingerprint ~ '^[0-9a-f]{64}$'
            """,
            name="ck_idp_source_document_bindings_line_fingerprint",
        ),
        Index(
            "ix_idp_source_document_bindings_source_document_pk",
            "source_document_pk",
        ),
        Index(
            "ix_idp_source_document_bindings_fact_id",
            "fact_id",
        ),
        Index(
            "ix_idp_source_document_bindings_first_receipt",
            "first_receipt_id",
        ),
        Index(
            "ix_idp_source_document_bindings_source_sha",
            "source_system",
            "document_sha256",
        ),
    )
