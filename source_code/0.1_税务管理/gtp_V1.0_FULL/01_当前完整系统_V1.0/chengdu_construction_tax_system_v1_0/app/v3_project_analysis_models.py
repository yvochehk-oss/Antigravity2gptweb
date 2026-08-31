"""V3 Task17 project-tax allocation and analysis ORM models."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class FactProjectAllocation(Base):
    __tablename__ = "fact_project_allocations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fact_id: Mapped[int] = mapped_column(Integer, ForeignKey("facts.id", ondelete="RESTRICT"), nullable=False)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False)
    allocation_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    supersedes_allocation_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("fact_project_allocations.id", ondelete="RESTRICT"), nullable=True)
    allocated_net: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    allocated_vat: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    allocated_gross: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    allocation_method: Mapped[str] = mapped_column(String(24), nullable=False)
    confidence: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'CANDIDATE'"))
    proposal_source: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'HUMAN'"))
    source_document_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("source_documents.id", ondelete="RESTRICT"), nullable=True)
    rule_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        CheckConstraint("allocation_version >= 1", name="ck_fact_project_allocations_version_positive"),
        CheckConstraint("supersedes_allocation_id IS NULL OR supersedes_allocation_id <> id", name="ck_fact_project_allocations_not_self_supersede"),
        CheckConstraint("abs((allocated_net + allocated_vat) - allocated_gross) <= 0.01", name="ck_fact_project_allocations_amount_balance"),
        CheckConstraint("allocation_method IN ('EXPLICIT','SOURCE_DOCUMENT','MANUAL','PROPORTIONAL','RULE_BASED')", name="ck_fact_project_allocations_method"),
        CheckConstraint("confidence IN ('HIGH','MEDIUM','LOW')", name="ck_fact_project_allocations_confidence"),
        CheckConstraint("status IN ('CANDIDATE','NEEDS_REVIEW','CONFIRMED','REJECTED','SUPERSEDED')", name="ck_fact_project_allocations_status"),
        CheckConstraint("proposal_source IN ('HUMAN','DOCUMENT','RULE_ENGINE','AI','LEGACY')", name="ck_fact_project_allocations_proposal_source"),
        CheckConstraint("allocation_method <> 'SOURCE_DOCUMENT' OR source_document_id IS NOT NULL", name="ck_fact_project_allocations_source_document"),
        CheckConstraint("allocation_method <> 'RULE_BASED' OR (rule_version IS NOT NULL AND btrim(rule_version) <> '')", name="ck_fact_project_allocations_rule_version"),
        CheckConstraint("status NOT IN ('CONFIRMED','REJECTED','SUPERSEDED') OR (reviewed_by IS NOT NULL AND btrim(reviewed_by) <> '' AND reviewed_at IS NOT NULL)", name="ck_fact_project_allocations_reviewed_evidence"),
        CheckConstraint("status NOT IN ('REJECTED','SUPERSEDED') OR NOT is_current", name="ck_fact_project_allocations_resolved_not_current"),
        UniqueConstraint("fact_id", "project_id", "allocation_version", name="uq_fact_project_allocations_fact_project_version"),
        Index("ix_fact_project_allocations_fact_status", "fact_id", "status", "is_current"),
        Index("ix_fact_project_allocations_project_status", "project_id", "status", "is_current"),
        Index("uq_fact_project_allocations_current", "fact_id", "project_id", unique=True, postgresql_where=text("is_current")),
    )


class ProjectTaxAnalysis(Base):
    __tablename__ = "project_tax_analysis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    calculation_run_id: Mapped[int] = mapped_column(Integer, ForeignKey("calculation_runs.id", ondelete="RESTRICT"), nullable=False)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False)
    reporting_party_id: Mapped[int] = mapped_column(Integer, ForeignKey("internal_entities.party_id", ondelete="RESTRICT"), nullable=False)
    tax_period: Mapped[date] = mapped_column(Date, nullable=False)
    tax_type: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'VAT'"))
    basis: Mapped[str] = mapped_column(String(12), nullable=False, server_default=text("'TAX'"))
    output_taxable_net: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    output_vat: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    claimed_input_vat: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    tax_prepayment: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    net_vat_before_entity_credit: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    net_vat_after_project_prepayment: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    allocation_coverage_status: Mapped[str] = mapped_column(String(12), nullable=False)
    input_snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    result_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        CheckConstraint("EXTRACT(DAY FROM tax_period) = 1", name="ck_project_tax_analysis_period_month_start"),
        CheckConstraint("basis = 'TAX'", name="ck_project_tax_analysis_tax_basis"),
        CheckConstraint("allocation_coverage_status IN ('FULL','PARTIAL','NONE')", name="ck_project_tax_analysis_coverage_status"),
        CheckConstraint("net_vat_before_entity_credit = output_vat - claimed_input_vat", name="ck_project_tax_analysis_net_before_credit_formula"),
        CheckConstraint("net_vat_after_project_prepayment = net_vat_before_entity_credit - tax_prepayment", name="ck_project_tax_analysis_after_prepayment_formula"),
        CheckConstraint("length(input_snapshot_sha256) = 64", name="ck_project_tax_analysis_input_hash_length"),
        CheckConstraint("length(result_sha256) = 64", name="ck_project_tax_analysis_result_hash_length"),
        UniqueConstraint("calculation_run_id", "project_id", "tax_type", name="uq_project_tax_analysis_run_project_tax_type"),
        Index("ix_project_tax_analysis_scope", "reporting_party_id", "tax_period", "project_id", "tax_type"),
    )


class ProjectTaxAnalysisComponent(Base):
    __tablename__ = "project_tax_analysis_components"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_id: Mapped[int] = mapped_column(Integer, ForeignKey("project_tax_analysis.id", ondelete="CASCADE"), nullable=False)
    component_type: Mapped[str] = mapped_column(String(20), nullable=False)
    taxable_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    fact_project_allocation_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("fact_project_allocations.id", ondelete="RESTRICT"), nullable=True)
    output_vat_event_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("output_vat_events.id", ondelete="RESTRICT"), nullable=True)
    input_vat_claim_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("input_vat_claims.id", ondelete="RESTRICT"), nullable=True)
    tax_prepayment_fact_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("tax_prepayment_facts.fact_id", ondelete="RESTRICT"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        CheckConstraint("component_type IN ('OUTPUT_VAT','INPUT_VAT','TAX_PREPAYMENT')", name="ck_project_tax_analysis_components_type"),
        CheckConstraint("(component_type='OUTPUT_VAT' AND fact_project_allocation_id IS NOT NULL AND output_vat_event_id IS NOT NULL AND input_vat_claim_id IS NULL AND tax_prepayment_fact_id IS NULL) OR (component_type='INPUT_VAT' AND fact_project_allocation_id IS NOT NULL AND output_vat_event_id IS NULL AND input_vat_claim_id IS NOT NULL AND tax_prepayment_fact_id IS NULL) OR (component_type='TAX_PREPAYMENT' AND fact_project_allocation_id IS NULL AND output_vat_event_id IS NULL AND input_vat_claim_id IS NULL AND tax_prepayment_fact_id IS NOT NULL)", name="ck_project_tax_analysis_components_typed_source"),
        UniqueConstraint("analysis_id", "fact_project_allocation_id", "output_vat_event_id", name="uq_project_tax_analysis_component_output"),
        UniqueConstraint("analysis_id", "fact_project_allocation_id", "input_vat_claim_id", name="uq_project_tax_analysis_component_input"),
        UniqueConstraint("analysis_id", "tax_prepayment_fact_id", name="uq_project_tax_analysis_component_prepayment"),
        Index("ix_project_tax_analysis_components_analysis", "analysis_id"),
    )
