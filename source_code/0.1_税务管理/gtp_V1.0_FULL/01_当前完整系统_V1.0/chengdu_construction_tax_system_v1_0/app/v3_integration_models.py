"""Task24 application-integration ORM models."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


JsonType = JSON().with_variant(JSONB(), "postgresql")


class CanonicalIngestReceipt(Base):
    __tablename__ = "canonical_ingest_receipts"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    source_system: Mapped[str] = mapped_column(String(40), nullable=False)
    source_document_id: Mapped[str] = mapped_column(String(160), nullable=False)
    source_extraction_id: Mapped[str] = mapped_column(String(160), nullable=False)
    document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    document_type: Mapped[str] = mapped_column(String(40), nullable=False)

    business_identity_key: Mapped[str] = mapped_column(
        String(240),
        nullable=False,
    )
    identity_version: Mapped[str] = mapped_column(String(40), nullable=False)
    normalization_version: Mapped[str] = mapped_column(String(40), nullable=False)

    fact_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("facts.id", ondelete="RESTRICT"),
        nullable=False,
    )

    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    source_payload_fingerprint: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    payload_fingerprint: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )

    canonical_payload: Mapped[dict[str, Any]] = mapped_column(
        JsonType,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        UniqueConstraint(
            "source_system",
            "source_extraction_id",
            name="uq_canonical_ingest_receipts_source_extraction",
        ),
        CheckConstraint(
            "source_system <> ''",
            name="ck_canonical_ingest_receipts_source_system_nonempty",
        ),
        CheckConstraint(
            "source_document_id <> ''",
            name="ck_canonical_ingest_receipts_document_id_nonempty",
        ),
        CheckConstraint(
            "source_extraction_id <> ''",
            name="ck_canonical_ingest_receipts_extraction_id_nonempty",
        ),
        CheckConstraint(
            "char_length(document_sha256) = 64",
            name="ck_canonical_ingest_receipts_sha256_length",
        ),
        CheckConstraint(
            "document_type IN ('invoice','contract')",
            name="ck_canonical_ingest_receipts_document_type",
        ),
        CheckConstraint(
            "outcome IN ('CREATED','NOOP','REJECTED')",
            name="ck_canonical_ingest_receipts_outcome",
        ),
        CheckConstraint(
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
        Index(
            "ix_canonical_ingest_receipts_document_history",
            "source_system",
            "source_document_id",
            "source_extraction_id",
        ),
        Index(
            "ix_canonical_ingest_receipts_business_identity",
            "business_identity_key",
        ),
        Index(
            "ix_canonical_ingest_receipts_fact_id",
            "fact_id",
        ),
    )
