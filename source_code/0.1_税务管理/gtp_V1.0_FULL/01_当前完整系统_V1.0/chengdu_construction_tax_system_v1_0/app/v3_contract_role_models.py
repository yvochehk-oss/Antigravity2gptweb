"""Task26 auditable Contract legal-role evidence and resolution history."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
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

from . import v3_contract_models as _v3_contract_models  # noqa: F401
from . import v3_fact_models as _v3_fact_models  # noqa: F401
from . import v3_integration_models as _v3_integration_models  # noqa: F401
from . import v3_party_models as _v3_party_models  # noqa: F401
from .db import Base


class ContractRoleEvidence(Base):
    """Append-only explicit legal-role evidence for one Contract Fact."""

    __tablename__ = "contract_role_evidence"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    fact_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("contract_facts.fact_id", ondelete="RESTRICT"),
        nullable=False,
    )
    receipt_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("canonical_ingest_receipts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_system: Mapped[str] = mapped_column(String(40), nullable=False)
    source_document_id: Mapped[str] = mapped_column(String(160), nullable=False)
    source_extraction_id: Mapped[str] = mapped_column(String(160), nullable=False)
    document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_party_role: Mapped[str] = mapped_column(String(16), nullable=False)
    party_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("parties.id", ondelete="RESTRICT"),
        nullable=False,
    )
    evidence_type: Mapped[str] = mapped_column(String(32), nullable=False)
    legal_role_label: Mapped[str] = mapped_column(String(160), nullable=False)
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False)
    page_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(6, 5), nullable=True)
    canonical_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    classification_status: Mapped[str] = mapped_column(String(20), nullable=False)
    classification_code: Mapped[str] = mapped_column(String(80), nullable=False)
    ruleset_version: Mapped[str] = mapped_column(String(40), nullable=False)
    evidence_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    submitted_by: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        UniqueConstraint(
            "fact_id",
            "evidence_fingerprint",
            name="uq_contract_role_evidence_fact_fingerprint",
        ),
        CheckConstraint(
            "source_party_role IN ('PARTY_A','PARTY_B')",
            name="ck_contract_role_evidence_source_role",
        ),
        CheckConstraint(
            "evidence_type IN ('LEGAL_ROLE_LABEL','CONTRACT_CLAUSE_ROLE')",
            name="ck_contract_role_evidence_type",
        ),
        CheckConstraint(
            "canonical_role IS NULL OR canonical_role IN ('BUYER','SELLER')",
            name="ck_contract_role_evidence_canonical_role",
        ),
        CheckConstraint(
            "classification_status IN ('MAPPED','UNSUPPORTED','AMBIGUOUS','INVALID')",
            name="ck_contract_role_evidence_classification",
        ),
        CheckConstraint(
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
        CheckConstraint(
            "btrim(source_system) <> ''",
            name="ck_contract_role_evidence_source_system_nonempty",
        ),
        CheckConstraint(
            "btrim(source_document_id) <> ''",
            name="ck_contract_role_evidence_document_nonempty",
        ),
        CheckConstraint(
            "btrim(source_extraction_id) <> ''",
            name="ck_contract_role_evidence_extraction_nonempty",
        ),
        CheckConstraint(
            "document_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_contract_role_evidence_sha256",
        ),
        CheckConstraint(
            "btrim(legal_role_label) <> ''",
            name="ck_contract_role_evidence_label_nonempty",
        ),
        CheckConstraint(
            "btrim(evidence_text) <> ''",
            name="ck_contract_role_evidence_text_nonempty",
        ),
        CheckConstraint(
            "btrim(classification_code) <> ''",
            name="ck_contract_role_evidence_code_nonempty",
        ),
        CheckConstraint(
            "btrim(ruleset_version) <> ''",
            name="ck_contract_role_evidence_ruleset_nonempty",
        ),
        CheckConstraint(
            "evidence_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_contract_role_evidence_fingerprint",
        ),
        CheckConstraint(
            "btrim(submitted_by) <> ''",
            name="ck_contract_role_evidence_submitter_nonempty",
        ),
        CheckConstraint(
            "page_no IS NULL OR page_no >= 1",
            name="ck_contract_role_evidence_page_positive",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_contract_role_evidence_confidence",
        ),
        Index("ix_contract_role_evidence_fact_id", "fact_id"),
        Index("ix_contract_role_evidence_receipt_id", "receipt_id"),
        Index("ix_contract_role_evidence_party_id", "party_id"),
        Index(
            "ix_contract_role_evidence_source_document",
            "source_system",
            "source_document_id",
            "source_extraction_id",
        ),
        Index("ix_contract_role_evidence_ruleset", "ruleset_version"),
    )


class ContractRoleResolution(Base):
    """Versioned Task26 role-decision history; not Fact supersession."""

    __tablename__ = "contract_role_resolutions"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    fact_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("contract_facts.fact_id", ondelete="RESTRICT"),
        nullable=False,
    )
    receipt_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("canonical_ingest_receipts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    resolution_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    is_current: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )
    supersedes_resolution_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("contract_role_resolutions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    ruleset_version: Mapped[str] = mapped_column(String(40), nullable=False)
    resolution_status: Mapped[str] = mapped_column(String(20), nullable=False)
    buyer_party_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("parties.id", ondelete="RESTRICT"),
        nullable=True,
    )
    seller_party_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("parties.id", ondelete="RESTRICT"),
        nullable=True,
    )
    evidence_set_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(80), nullable=False)
    reason_detail: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        UniqueConstraint(
            "fact_id",
            "resolution_seq",
            name="uq_contract_role_resolutions_fact_seq",
        ),
        CheckConstraint(
            "resolution_seq >= 1",
            name="ck_contract_role_resolutions_seq_positive",
        ),
        CheckConstraint(
            "resolution_status IN ('RESOLVED','NEEDS_REVIEW')",
            name="ck_contract_role_resolutions_status",
        ),
        CheckConstraint(
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
        CheckConstraint(
            "supersedes_resolution_id IS NULL OR supersedes_resolution_id <> id",
            name="ck_contract_role_resolutions_not_self_supersede",
        ),
        CheckConstraint(
            "btrim(ruleset_version) <> ''",
            name="ck_contract_role_resolutions_ruleset_nonempty",
        ),
        CheckConstraint(
            "evidence_set_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_contract_role_resolutions_fingerprint",
        ),
        CheckConstraint(
            "btrim(reason_code) <> ''",
            name="ck_contract_role_resolutions_reason_nonempty",
        ),
        CheckConstraint(
            "btrim(reason_detail) <> ''",
            name="ck_contract_role_resolutions_detail_nonempty",
        ),
        Index(
            "uq_contract_role_resolutions_current_fact",
            "fact_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
        Index("ix_contract_role_resolutions_buyer", "buyer_party_id"),
        Index("ix_contract_role_resolutions_seller", "seller_party_id"),
        Index("ix_contract_role_resolutions_status", "resolution_status"),
        Index(
            "ix_contract_role_resolutions_supersedes",
            "supersedes_resolution_id",
        ),
    )
