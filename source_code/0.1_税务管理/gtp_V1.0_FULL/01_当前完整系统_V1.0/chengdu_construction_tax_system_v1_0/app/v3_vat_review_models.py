"""Reviewed VAT-period completeness evidence models."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class VatOutputPeriodAssertion(Base):
    __tablename__ = "vat_output_period_assertions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reporting_party_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("internal_entities.party_id", ondelete="RESTRICT"),
        nullable=False,
    )
    tax_period: Mapped[date] = mapped_column(Date, nullable=False)
    asserted_output_vat_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    source_document_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("source_documents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    source: Mapped[str] = mapped_column(String(160), nullable=False)
    reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    reviewed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        CheckConstraint(
            "EXTRACT(DAY FROM tax_period) = 1",
            name="ck_vat_output_assertions_period_month_start",
        ),
        CheckConstraint(
            "btrim(source) <> ''",
            name="ck_vat_output_assertions_source_nonblank",
        ),
        CheckConstraint(
            "NOT reviewed OR (reviewed_by IS NOT NULL AND btrim(reviewed_by)<>'' AND reviewed_at IS NOT NULL)",
            name="ck_vat_output_assertions_reviewed_evidence",
        ),
        UniqueConstraint(
            "reporting_party_id",
            "tax_period",
            name="uq_vat_output_assertions_scope",
        ),
        Index("ix_vat_output_assertions_source_document", "source_document_id"),
    )


class VatInputPeriodAssertion(Base):
    __tablename__ = "vat_input_period_assertions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reporting_party_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("internal_entities.party_id", ondelete="RESTRICT"),
        nullable=False,
    )
    tax_period: Mapped[date] = mapped_column(Date, nullable=False)
    asserted_input_vat_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    source_document_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("source_documents.id", ondelete="RESTRICT"),
        nullable=True,
    )
    source: Mapped[str] = mapped_column(String(160), nullable=False)
    reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    reviewed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        CheckConstraint(
            "EXTRACT(DAY FROM tax_period) = 1",
            name="ck_vat_input_assertions_period_month_start",
        ),
        CheckConstraint(
            "btrim(source) <> ''",
            name="ck_vat_input_assertions_source_nonblank",
        ),
        CheckConstraint(
            "NOT reviewed OR (reviewed_by IS NOT NULL AND btrim(reviewed_by)<>'' AND reviewed_at IS NOT NULL)",
            name="ck_vat_input_assertions_reviewed_evidence",
        ),
        UniqueConstraint(
            "reporting_party_id",
            "tax_period",
            name="uq_vat_input_assertions_scope",
        ),
        Index("ix_vat_input_assertions_source_document", "source_document_id"),
    )
