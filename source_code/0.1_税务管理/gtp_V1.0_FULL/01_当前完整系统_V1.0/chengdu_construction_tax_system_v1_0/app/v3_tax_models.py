"""V3 tax-event ORM models.

Task 10 introduces Input VAT Claim events. ``claim_period`` is the sole VAT
attribution period; Invoice date/legacy period remain source evidence only.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
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

from .db import Base


class InputVatClaim(Base):
    __tablename__ = "input_vat_claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_fact_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("invoice_facts.fact_id", ondelete="RESTRICT"),
        nullable=False,
    )
    reporting_party_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("internal_entities.party_id", ondelete="RESTRICT"),
        nullable=False,
    )
    claim_period: Mapped[date] = mapped_column(Date, nullable=False)
    claim_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    event_type: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'CLAIM'")
    )
    claim_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'NEEDS_REVIEW'")
    )
    evidence_type: Mapped[str] = mapped_column(String(24), nullable=False)
    confidence: Mapped[str] = mapped_column(String(8), nullable=False)
    source_document_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("source_documents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    source_system: Mapped[str | None] = mapped_column(String(40), nullable=True)
    external_claim_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    __table_args__ = (
        CheckConstraint(
            "claim_period = date_trunc('month', claim_period)::date",
            name="ck_input_vat_claims_period_month_start",
        ),
        CheckConstraint(
            "claim_amount <> 0",
            name="ck_input_vat_claims_amount_nonzero",
        ),
        CheckConstraint(
            "event_type IN ('CLAIM','REVERSAL','ADJUSTMENT')",
            name="ck_input_vat_claims_event_type",
        ),
        CheckConstraint(
            "(event_type <> 'CLAIM' OR claim_amount > 0) AND "
            "(event_type <> 'REVERSAL' OR claim_amount < 0)",
            name="ck_input_vat_claims_event_sign",
        ),
        CheckConstraint(
            "claim_status IN ('CONFIRMED','NEEDS_REVIEW','REJECTED','SUPERSEDED')",
            name="ck_input_vat_claims_status",
        ),
        CheckConstraint(
            "evidence_type IN ('DOCUMENT_EVIDENCE','MANUAL_REVIEW','LEGACY_ASSUMPTION')",
            name="ck_input_vat_claims_evidence_type",
        ),
        CheckConstraint(
            "confidence IN ('HIGH','MEDIUM','LOW')",
            name="ck_input_vat_claims_confidence",
        ),
        CheckConstraint(
            "evidence_type <> 'LEGACY_ASSUMPTION' OR "
            "(confidence='LOW' AND claim_status='NEEDS_REVIEW')",
            name="ck_input_vat_claims_legacy_assumption_fail_closed",
        ),
        CheckConstraint(
            "claim_status <> 'CONFIRMED' OR "
            "(evidence_type <> 'LEGACY_ASSUMPTION' AND reviewed_by IS NOT NULL "
            "AND btrim(reviewed_by) <> '' AND reviewed_at IS NOT NULL)",
            name="ck_input_vat_claims_confirmed_reviewed",
        ),
        UniqueConstraint(
            "source_system",
            "external_claim_id",
            name="uq_input_vat_claims_source_identity",
        ),
        Index("ix_input_vat_claims_invoice_fact_id", "invoice_fact_id"),
        Index(
            "ix_input_vat_claims_reporting_period_status",
            "reporting_party_id",
            "claim_period",
            "claim_status",
        ),
        Index("ix_input_vat_claims_source_document_id", "source_document_id"),
    )
