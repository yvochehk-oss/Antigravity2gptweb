"""V3 Entity VAT Ledger ORM models."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class OutputVatEvent(Base):
    __tablename__ = "output_vat_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_fact_id: Mapped[int] = mapped_column(Integer, ForeignKey("invoice_facts.fact_id", ondelete="RESTRICT"), nullable=False)
    reporting_party_id: Mapped[int] = mapped_column(Integer, ForeignKey("internal_entities.party_id", ondelete="RESTRICT"), nullable=False)
    output_vat_period: Mapped[date] = mapped_column(Date, nullable=False)
    vat_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'OUTPUT'"))
    event_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'NEEDS_REVIEW'"))
    evidence_type: Mapped[str] = mapped_column(String(24), nullable=False)
    confidence: Mapped[str] = mapped_column(String(8), nullable=False)
    source_document_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("source_documents.id", ondelete="RESTRICT"), nullable=True)
    source_system: Mapped[str | None] = mapped_column(String(40), nullable=True)
    external_event_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        CheckConstraint("EXTRACT(DAY FROM output_vat_period) = 1", name="ck_output_vat_events_period_month_start"),
        CheckConstraint("vat_amount <> 0", name="ck_output_vat_events_amount_nonzero"),
        CheckConstraint("event_type IN ('OUTPUT','REVERSAL','ADJUSTMENT')", name="ck_output_vat_events_type"),
        CheckConstraint("(event_type<>'OUTPUT' OR vat_amount>0) AND (event_type<>'REVERSAL' OR vat_amount<0) AND (event_type<>'ADJUSTMENT' OR vat_amount<>0)", name="ck_output_vat_events_sign"),
        CheckConstraint("event_status IN ('CONFIRMED','NEEDS_REVIEW','REJECTED','SUPERSEDED')", name="ck_output_vat_events_status"),
        CheckConstraint("evidence_type IN ('DOCUMENT_EVIDENCE','MANUAL_REVIEW','LEGACY_ASSUMPTION')", name="ck_output_vat_events_evidence_type"),
        CheckConstraint("confidence IN ('HIGH','MEDIUM','LOW')", name="ck_output_vat_events_confidence"),
        CheckConstraint("evidence_type<>'LEGACY_ASSUMPTION' OR (confidence='LOW' AND event_status='NEEDS_REVIEW')", name="ck_output_vat_events_legacy_fail_closed"),
        CheckConstraint("evidence_type<>'DOCUMENT_EVIDENCE' OR source_document_id IS NOT NULL", name="ck_output_vat_events_document_source"),
        CheckConstraint("event_status<>'CONFIRMED' OR (evidence_type<>'LEGACY_ASSUMPTION' AND reviewed_by IS NOT NULL AND btrim(reviewed_by)<>'' AND reviewed_at IS NOT NULL)", name="ck_output_vat_events_confirmed_reviewed"),
        UniqueConstraint("source_system", "external_event_id", name="uq_output_vat_events_source_identity"),
        Index("ix_output_vat_events_invoice_fact_id", "invoice_fact_id"),
        Index("ix_output_vat_events_reporting_period_status", "reporting_party_id", "output_vat_period", "event_status"),
        Index("ix_output_vat_events_source_document_id", "source_document_id"),
    )


class VatOpeningBalanceSeed(Base):
    __tablename__ = "vat_opening_balance_seeds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reporting_party_id: Mapped[int] = mapped_column(Integer, ForeignKey("internal_entities.party_id", ondelete="RESTRICT"), nullable=False)
    tax_period: Mapped[date] = mapped_column(Date, nullable=False)
    opening_input_credit: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    source_document_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("source_documents.id", ondelete="RESTRICT"), nullable=True)
    source: Mapped[str] = mapped_column(String(160), nullable=False)
    reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    reviewed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        CheckConstraint("EXTRACT(DAY FROM tax_period) = 1", name="ck_vat_opening_seeds_period_month_start"),
        CheckConstraint("opening_input_credit >= 0", name="ck_vat_opening_seeds_nonnegative"),
        CheckConstraint("NOT reviewed OR (reviewed_by IS NOT NULL AND btrim(reviewed_by)<>'' AND reviewed_at IS NOT NULL)", name="ck_vat_opening_seeds_reviewed_evidence"),
        UniqueConstraint("reporting_party_id", "tax_period", name="uq_vat_opening_seeds_scope"),
        Index("ix_vat_opening_seeds_source_document", "source_document_id"),
    )


class EntityVatLedger(Base):
    __tablename__ = "entity_vat_ledgers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    calculation_run_id: Mapped[int] = mapped_column(Integer, ForeignKey("calculation_runs.id", ondelete="RESTRICT"), nullable=False, unique=True)
    reporting_party_id: Mapped[int] = mapped_column(Integer, ForeignKey("internal_entities.party_id", ondelete="RESTRICT"), nullable=False)
    tax_period: Mapped[date] = mapped_column(Date, nullable=False)
    opening_input_credit: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    output_vat: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    input_vat: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    tax_prepayment: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    vat_payable_before_prepayment: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    closing_input_credit: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    vat_payable_after_prepayment: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    unapplied_tax_prepayment: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        CheckConstraint("EXTRACT(DAY FROM tax_period) = 1", name="ck_entity_vat_ledgers_period_month_start"),
        CheckConstraint("opening_input_credit >= 0 AND closing_input_credit >= 0", name="ck_entity_vat_ledgers_credit_nonnegative"),
        CheckConstraint("vat_payable_before_prepayment >= 0 AND vat_payable_after_prepayment >= 0 AND unapplied_tax_prepayment >= 0", name="ck_entity_vat_ledgers_payable_nonnegative"),
        UniqueConstraint("reporting_party_id", "tax_period", "calculation_run_id", name="uq_entity_vat_ledgers_scope_run"),
        Index("ix_entity_vat_ledgers_scope", "reporting_party_id", "tax_period"),
    )


class EntityVatLedgerComponent(Base):
    __tablename__ = "entity_vat_ledger_components"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ledger_id: Mapped[int] = mapped_column(Integer, ForeignKey("entity_vat_ledgers.id", ondelete="CASCADE"), nullable=False)
    component_type: Mapped[str] = mapped_column(String(28), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    output_vat_event_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("output_vat_events.id", ondelete="RESTRICT"), nullable=True)
    input_vat_claim_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("input_vat_claims.id", ondelete="RESTRICT"), nullable=True)
    tax_prepayment_fact_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("tax_prepayment_facts.fact_id", ondelete="RESTRICT"), nullable=True)
    prior_ledger_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("entity_vat_ledgers.id", ondelete="RESTRICT"), nullable=True)
    opening_balance_seed_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("vat_opening_balance_seeds.id", ondelete="RESTRICT"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        CheckConstraint("component_type IN ('OPENING_INPUT_CREDIT','OUTPUT_VAT','INPUT_VAT','TAX_PREPAYMENT')", name="ck_entity_vat_ledger_components_type"),
        CheckConstraint("(component_type='OUTPUT_VAT' AND output_vat_event_id IS NOT NULL AND input_vat_claim_id IS NULL AND tax_prepayment_fact_id IS NULL AND prior_ledger_id IS NULL AND opening_balance_seed_id IS NULL) OR (component_type='INPUT_VAT' AND output_vat_event_id IS NULL AND input_vat_claim_id IS NOT NULL AND tax_prepayment_fact_id IS NULL AND prior_ledger_id IS NULL AND opening_balance_seed_id IS NULL) OR (component_type='TAX_PREPAYMENT' AND output_vat_event_id IS NULL AND input_vat_claim_id IS NULL AND tax_prepayment_fact_id IS NOT NULL AND prior_ledger_id IS NULL AND opening_balance_seed_id IS NULL) OR (component_type='OPENING_INPUT_CREDIT' AND output_vat_event_id IS NULL AND input_vat_claim_id IS NULL AND tax_prepayment_fact_id IS NULL AND ((prior_ledger_id IS NOT NULL AND opening_balance_seed_id IS NULL) OR (prior_ledger_id IS NULL AND opening_balance_seed_id IS NOT NULL)))", name="ck_entity_vat_ledger_components_typed_source"),
        UniqueConstraint("ledger_id", "output_vat_event_id", name="uq_entity_vat_component_output_event"),
        UniqueConstraint("ledger_id", "input_vat_claim_id", name="uq_entity_vat_component_input_claim"),
        UniqueConstraint("ledger_id", "tax_prepayment_fact_id", name="uq_entity_vat_component_prepayment_fact"),
        Index("ix_entity_vat_components_ledger", "ledger_id"),
        Index("ix_entity_vat_components_prior_ledger", "prior_ledger_id"),
    )
