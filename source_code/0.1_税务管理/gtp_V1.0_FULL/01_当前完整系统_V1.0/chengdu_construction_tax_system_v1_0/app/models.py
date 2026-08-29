"""V0.2: SQLAlchemy ORM 模型。金额统一使用 Numeric(18, 2)。"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from sqlalchemy.types import TypeDecorator


class IsoDateTime(TypeDecorator):
    """Transparently bridges Python strings / datetime objects with PostgreSQL TIMESTAMPTZ."""
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value
        if isinstance(value, str):
            val = value.strip()
            if not val:
                return None
            try:
                dt = datetime.fromisoformat(val)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                return None
        return value

    def process_result_value(self, value, dialect):
        if value is None:
            return ""
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)


from .constants import DEFAULT_RISK_THRESHOLDS
from .db import Base


class Entity(Base):
    """Canonical entity master.

    ``code`` is a real-company master-data code (A01-A11/B01-B10/C01-C02/D01-D03), never a
    business-role placeholder.  ``kind`` and ``internal`` remain as
    deprecated compatibility columns for older readers; new code must use
    ``business_role``, ``legal_entity`` and ``active``.
    """
    __tablename__ = "entities"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    # Deprecated V0.2 aliases.  They are kept so an old reader can open a
    # migrated database while the canonical fields are adopted everywhere.
    kind: Mapped[str] = mapped_column(String(30), default="", index=True)
    internal: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    tax_id: Mapped[str | None] = mapped_column(
        String(40), nullable=True, unique=True, index=True,
    )  # 纳税人识别号（统一社会信用代码）；未知值必须为 NULL
    short_name: Mapped[str] = mapped_column(String(80), default="", index=True)
    business_role: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    legal_entity: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    parent_entity_code: Mapped[str | None] = mapped_column(
        String(16), ForeignKey("entities.code"), nullable=True, index=True,
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    # Shared RAG aliases; the PostgreSQL trigger keeps these synchronized with
    # ``code/kind/active`` in the same row.
    entity_code: Mapped[str | None] = mapped_column(String(16), nullable=True, unique=True, index=True)
    entity_kind: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    status: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)

    __table_args__ = (
        CheckConstraint(
            "business_role IN ('A', 'B', 'C', 'D')",
            name="ck_entities_business_role",
        ),
        # The canonical master is intentionally closed: an A/B/C/D value on
        # its own is a role, not an entity.  External parties use their own
        # table and never enter this table.
        CheckConstraint(
            "code IN ("
            "'A01','A02','A03','A04','A05','A06','A07','A08','A09','A10','A11',"
            "'B01','B02','B03','B04','B05','B06','B07','B08','B09','B10',"
            "'C01','C02',"
            "'D01','D02','D03'"
            ")",
            name="ck_entities_canonical_code",
        ),
        UniqueConstraint("code", name="uq_entities_code"),
        UniqueConstraint("tax_id", name="uq_entities_tax_id"),
    )


class ExternalParty(Base):
    """Named external counterparty master.

    External parties are deliberately separate from :class:`Entity`.  The
    transaction tables retain a compact ``*_code`` value, which resolves to
    this table for external parties and to ``entities`` for internal legal
    entities.
    """

    __tablename__ = "external_parties"
    id: Mapped[int] = mapped_column(primary_key=True)
    # External-party codes are shared with RAG and include descriptive codes
    # such as ``EXT-CQ-HEAVY-CRANE``.  Keep the full canonical value instead
    # of inheriting the 16-character limit used by internal entity codes.
    code: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    short_name: Mapped[str] = mapped_column(String(60), default="", index=True)
    kind: Mapped[str] = mapped_column(String(30), default="", index=True)
    tax_id: Mapped[str | None] = mapped_column(String(40), nullable=True, unique=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    city: Mapped[str] = mapped_column(String(40))
    contract_total: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    tax_method: Mapped[str] = mapped_column(String(20), default="general")
    entity_code: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    # Shared RAG aliases; kept in the same row by PostgreSQL trigger.
    project_code: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)
    contract_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)


class Contract(Base):
    __tablename__ = "contracts"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    contract_no: Mapped[str] = mapped_column(String(50), default="")
    buyer_code: Mapped[str] = mapped_column(String(16), index=True)
    seller_code: Mapped[str] = mapped_column(String(16), index=True)
    category: Mapped[str] = mapped_column(String(30), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    internal_trade: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    note: Mapped[str] = mapped_column(String(200), default="")


class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    invoice_no: Mapped[str] = mapped_column(String(60), default="", index=True)
    period: Mapped[str] = mapped_column(String(7), index=True)
    entity_code: Mapped[str] = mapped_column(String(16), index=True)
    direction: Mapped[str] = mapped_column(String(10), index=True)  # in/out
    counterparty_code: Mapped[str] = mapped_column(String(16), index=True)
    category: Mapped[str] = mapped_column(String(30), index=True)
    net: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    vat: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), default=Decimal("0"))
    deductible: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(String(200), default="")


class CashFlow(Base):
    __tablename__ = "cashflows"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    entity_code: Mapped[str] = mapped_column(String(16), index=True)
    counterparty_code: Mapped[str] = mapped_column(String(16), index=True)
    direction: Mapped[str] = mapped_column(String(10), index=True)  # in/out
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    period: Mapped[str] = mapped_column(String(7), index=True)
    transaction_date: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    bank_reference: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    source_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    note: Mapped[str] = mapped_column(String(200), default="")


class Fulfillment(Base):
    __tablename__ = "fulfillment"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    counterparty_code: Mapped[str] = mapped_column(String(16), index=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    category: Mapped[str] = mapped_column(String(30), default="", index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    evidence_complete: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    note: Mapped[str] = mapped_column(String(200), default="")


class RealCost(Base):
    __tablename__ = "real_costs"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    entity_code: Mapped[str] = mapped_column(String(16), index=True)
    counterparty_code: Mapped[str] = mapped_column(String(16), default="", index=True)
    category: Mapped[str] = mapped_column(String(30), index=True)
    subcategory: Mapped[str] = mapped_column(String(50), default="", index=True)
    period: Mapped[str] = mapped_column(String(7), default="", index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    external_cash: Mapped[bool] = mapped_column(Boolean)
    note: Mapped[str] = mapped_column(String(200), default="")


class Progress(Base):
    __tablename__ = "progress"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    period: Mapped[str] = mapped_column(String(7), index=True)
    output_value: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    settlement: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    recognized_revenue: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    collection: Mapped[Decimal] = mapped_column(Numeric(18, 2))


class Budget(Base):
    __tablename__ = "budgets"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    category: Mapped[str] = mapped_column(String(30), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))


class CostAccount(Base):
    __tablename__ = "cost_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    parent_code: Mapped[str] = mapped_column(String(20), default="")
    name: Mapped[str] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(30), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class TaxRule(Base):
    __tablename__ = "tax_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    rate: Mapped[Decimal] = mapped_column(Numeric(6, 4))
    effective_from: Mapped[str] = mapped_column(String(10))
    effective_to: Mapped[str] = mapped_column(String(10), default="")
    source: Mapped[str] = mapped_column(String(300), default="")
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(String(300), default="")


class TaxLedger(Base):
    __tablename__ = "tax_ledgers"
    id: Mapped[int] = mapped_column(primary_key=True)
    period: Mapped[str] = mapped_column(String(7), index=True)
    entity_code: Mapped[str] = mapped_column(String(16), index=True)
    output_vat: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    input_vat: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    vat_payable: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    revenue: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    real_cost: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    estimated_profit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    estimated_cit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    cit_note: Mapped[str] = mapped_column(String(500), default="")
    generated: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        UniqueConstraint("period", "entity_code", name="uq_tax_ledger_period_entity"),
    )


class EntityBankAccount(Base):
    """Bank account master for a real internal entity."""

    __tablename__ = "entity_bank_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    entity_code: Mapped[str] = mapped_column(String(16), index=True)
    bank_name: Mapped[str] = mapped_column(String(120), default="")
    account_name: Mapped[str] = mapped_column(String(120), default="")
    account_no: Mapped[str] = mapped_column(String(80), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[str] = mapped_column(IsoDateTime, default="")

    __table_args__ = (
        UniqueConstraint("entity_code", "account_no", name="uq_entity_bank_account"),
    )


class TaxPaymentRecord(Base):
    """Documentary tax-payment record imported from RAG or a source ledger."""

    __tablename__ = "tax_payment_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True,
    )
    entity_code: Mapped[str] = mapped_column(String(16), index=True)
    tax_type: Mapped[str] = mapped_column(String(40), default="", index=True)
    period: Mapped[str] = mapped_column(String(7), default="", index=True)
    # ``tax_period`` and ``transaction_date`` retain the source-document
    # vocabulary used by RAG while ``period`` remains the normalized ledger
    # period used for reporting.
    tax_period: Mapped[str] = mapped_column(String(7), default="", index=True)
    receipt_no: Mapped[str] = mapped_column(String(100), default="", index=True)
    payment_date: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    transaction_date: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    principal_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    penalty_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    bank_reference: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    source_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(IsoDateTime, default="")

    __table_args__ = (
        UniqueConstraint("entity_code", "receipt_no", name="uq_tax_payment_receipt"),
    )


class RiskEvent(Base):
    __tablename__ = "risk_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    severity: Mapped[str] = mapped_column(String(10), index=True)
    code: Mapped[str] = mapped_column(String(50), index=True)
    message: Mapped[str] = mapped_column(String(300))
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    action: Mapped[str] = mapped_column(String(30), index=True)
    object_type: Mapped[str] = mapped_column(String(30), index=True)
    object_id: Mapped[str] = mapped_column(String(50), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(80), default="anonymous", index=True)
    ip: Mapped[str] = mapped_column(String(45), default="")
    request_id: Mapped[str] = mapped_column(String(64), default="", index=True)


class RiskThreshold(Base):
    """V0.2: 风险阈值表化（可由业务侧配置）。"""
    __tablename__ = "risk_thresholds"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(200), default="")
    ratio: Mapped[Decimal] = mapped_column(Numeric(6, 4))
    severity: Mapped[str] = mapped_column(String(10), default="YELLOW")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    @classmethod
    def defaults(cls) -> list[RiskThreshold]:
        return [
            cls(code="invoice_over_contract",
                description="发票净额超过合同金额的比率阈值",
                ratio=Decimal(str(DEFAULT_RISK_THRESHOLDS["invoice_over_contract"])),
                severity="YELLOW"),
            cls(code="paid_over_invoice",
                description="付款超过发票金额的比率阈值",
                ratio=Decimal(str(DEFAULT_RISK_THRESHOLDS["paid_over_invoice"])),
                severity="YELLOW"),
            cls(code="fulfilled_over_contract",
                description="履约金额超过合同金额的比率阈值",
                ratio=Decimal(str(DEFAULT_RISK_THRESHOLDS["fulfilled_over_contract"])),
                severity="YELLOW"),
        ]


class AIModelEndpoint(Base):
    __tablename__ = "ai_model_endpoints"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    adapter: Mapped[str] = mapped_column(String(30), default="openai_compatible")
    base_url: Mapped[str] = mapped_column(String(300), default="")
    chat_path: Mapped[str] = mapped_column(String(200), default="/v1/chat/completions")
    model: Mapped[str] = mapped_column(String(120), default="")
    # ``api_key_env`` remains for backwards-compatible deployments that resolve
    # an operator-provided environment variable.  New UI-managed credentials
    # are represented by an opaque reference only; the secret itself is never
    # persisted in this table.
    api_key_env: Mapped[str] = mapped_column(String(120), default="")
    credential_ref: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=90)
    note: Mapped[str] = mapped_column(String(300), default="")
    # Lower priority values are attempted first.  ``id`` is the deterministic
    # tie-breaker used by the routing service, so no separate default flag is
    # needed in the schema.
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default=text("100"),
    )
    routing_group: Mapped[str] = mapped_column(
        String(40), nullable=False, default="default",
        server_default=text("'default'"),
    )

    __table_args__ = (
        CheckConstraint(
            "priority >= 0",
            name="ck_ai_model_endpoints_priority_nonnegative",
        ),
        CheckConstraint(
            "routing_group ~ '^[a-z0-9][a-z0-9_/-]{0,39}$'",
            name="ck_ai_model_endpoints_routing_group_format",
        ),
    )


class AIReviewJob(Base):
    __tablename__ = "ai_review_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    scope: Mapped[str] = mapped_column(String(40), index=True)
    endpoint_id: Mapped[int] = mapped_column(ForeignKey("ai_model_endpoints.id"), index=True)
    batch_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    prompt_template_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    parent_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    user_instruction: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    parse_failed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[str] = mapped_column(IsoDateTime, default="")
    started_at: Mapped[str] = mapped_column(IsoDateTime, default="")
    finished_at: Mapped[str] = mapped_column(IsoDateTime, default="")
    input_digest: Mapped[str] = mapped_column(String(64), default="")
    input_preview: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(80), default="anonymous", index=True)


class AIReviewResult(Base):
    __tablename__ = "ai_review_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("ai_review_jobs.id"), unique=True, index=True,
    )
    provider_name: Mapped[str] = mapped_column(String(100), default="")
    model_name: Mapped[str] = mapped_column(String(120), default="")
    risk_level: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    score: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0"))
    summary: Mapped[str] = mapped_column(Text, default="")
    findings_json: Mapped[str] = mapped_column(Text, default="[]")
    recommendations_json: Mapped[str] = mapped_column(Text, default="[]")
    data_gaps_json: Mapped[str] = mapped_column(Text, default="[]")
    raw_response: Mapped[str] = mapped_column(Text, default="")


class AIPromptTemplate(Base):
    __tablename__ = "ai_prompt_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), index=True)
    scope: Mapped[str] = mapped_column(String(40), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    system_addendum: Mapped[str] = mapped_column(Text, default="")
    review_focus: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[str] = mapped_column(IsoDateTime, default="")

    __table_args__ = (
        UniqueConstraint("scope", "version", name="uq_prompt_scope_version"),
    )


class AIReviewBatch(Base):
    __tablename__ = "ai_review_batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    profile: Mapped[str] = mapped_column(String(30), default="standard", index=True)
    scopes_json: Mapped[str] = mapped_column(Text, default="[]")
    endpoint_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    user_instruction: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    created_at: Mapped[str] = mapped_column(IsoDateTime, default="")
    finished_at: Mapped[str] = mapped_column(IsoDateTime, default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(80), default="anonymous", index=True)


class AIConsensusReport(Base):
    __tablename__ = "ai_consensus_reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("ai_review_batches.id"), unique=True, index=True,
    )
    overall_risk: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    score: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0"))
    summary: Mapped[str] = mapped_column(Text, default="")
    common_findings_json: Mapped[str] = mapped_column(Text, default="[]")
    differences_json: Mapped[str] = mapped_column(Text, default="[]")
    recommendations_json: Mapped[str] = mapped_column(Text, default="[]")
    data_gaps_json: Mapped[str] = mapped_column(Text, default="[]")


class RemediationTask(Base):
    __tablename__ = "remediation_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    scope: Mapped[str] = mapped_column(String(40), default="whole_project", index=True)
    source_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    source_batch_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    recheck_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str] = mapped_column(String(10), default="P2", index=True)
    owner_role: Mapped[str] = mapped_column(String(80), default="项目财务/商务")
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    created_at: Mapped[str] = mapped_column(IsoDateTime, default="")
    updated_at: Mapped[str] = mapped_column(IsoDateTime, default="")
    closed_at: Mapped[str] = mapped_column(IsoDateTime, default="")
    actor: Mapped[str] = mapped_column(String(80), default="anonymous", index=True)


class ProjectRAGMap(Base):
    """税务系统项目 ↔ RAG 项目映射表。"""
    __tablename__ = "project_rag_map"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), unique=True, index=True)
    rag_project_id: Mapped[int] = mapped_column(Integer, index=True)
    rag_project_code: Mapped[str] = mapped_column(String(64), default="")
    rag_url: Mapped[str] = mapped_column(String(300), default="")
    rag_api_key: Mapped[str] = mapped_column(String(200), default="")
    note: Mapped[str] = mapped_column(String(300), default="")
    synced_at: Mapped[str] = mapped_column(IsoDateTime, default="")
    created_at: Mapped[str] = mapped_column(IsoDateTime, default="")


class RagServiceEndpoint(Base):
    """Administrator-approved Tax -> RAG service endpoint.

    There is deliberately one row (``id=1``).  ``resolved_addresses_json``
    stores the DNS result observed at approval time, not a credential.  The
    outbound validator compares a fresh resolution with this exact set before
    every request so a hostname cannot silently rebind to another network.
    """

    __tablename__ = "rag_service_endpoints"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    base_url: Mapped[str] = mapped_column(String(300), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    scheme: Mapped[str] = mapped_column(String(8), nullable=False)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_private: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolved_addresses_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    approved_at: Mapped[str] = mapped_column(IsoDateTime, nullable=False, default="")
    approved_by: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    last_tested_at: Mapped[str] = mapped_column(IsoDateTime, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(IsoDateTime, nullable=False, default="")
    updated_at: Mapped[str] = mapped_column(IsoDateTime, nullable=False, default="")


class SyncLog(Base):
    """RAG 同步记录，跟踪从知识库抽取数据的每次同步操作。"""
    __tablename__ = "sync_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    sync_type: Mapped[str] = mapped_column(String(30), index=True)  # invoice/contract/payment/tax_payment
    rag_project_id: Mapped[int] = mapped_column(Integer, default=0)  # 来源 RAG 项目 ID
    rag_chunk_ids_json: Mapped[str] = mapped_column(Text, default="[]")  # 关联 RAG chunk_id 列表
    rag_document_ids_json: Mapped[str] = mapped_column(Text, default="[]")  # 关联 RAG document_id 列表
    tax_record_ids_json: Mapped[str] = mapped_column(Text, default="[]")  # 入库后的税务系统记录 ID 列表
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending/confirmed/rejected/error
    total_chunks: Mapped[int] = mapped_column(Integer, default=0)
    total_extracted: Mapped[int] = mapped_column(Integer, default=0)
    total_imported: Mapped[int] = mapped_column(Integer, default=0)
    total_pending: Mapped[int] = mapped_column(Integer, default=0)
    errors_json: Mapped[str] = mapped_column(Text, default="[]")
    synced_at: Mapped[str] = mapped_column(IsoDateTime, default="")
    synced_by: Mapped[str] = mapped_column(String(80), default="rag_ai")
    note: Mapped[str] = mapped_column(String(300), default="")

    @property
    def errors(self) -> list[str]:
        if not self.errors_json:
            return []
        try:
            val = json.loads(self.errors_json)
            return val if isinstance(val, list) else []
        except Exception:
            return []


class SyncPending(Base):
    """RAG 抽取待确认项，置信度中等（0.6-0.9）的抽取结果需人工确认后入库。"""
    __tablename__ = "sync_pending"
    id: Mapped[int] = mapped_column(primary_key=True)
    sync_log_id: Mapped[int] = mapped_column(ForeignKey("sync_logs.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    sync_type: Mapped[str] = mapped_column(String(30), index=True)
    source_chunk_id: Mapped[int] = mapped_column(Integer, index=True)
    source_document_id: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(300), default="")
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 3))  # 0.000-1.000
    fields_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending/confirmed/rejected
    confirmed_record_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 确认后对应的税务记录 ID
    confirmed_at: Mapped[str] = mapped_column(IsoDateTime, default="")
    confirmed_by: Mapped[str] = mapped_column(String(80), default="")
    note: Mapped[str] = mapped_column(String(200), default="")


class FactsSnapshot(Base):
    """Canonical Facts snapshot shared by Tax and ProjectRAG.

    ``facts_snapshots`` is owned by the Tax migration chain because Tax is
    migrated first, but the physical contract is shared with ProjectRAG:
    UUID text identifiers, one JSON payload, explicit analytics-contract
    versioning, and creation metadata.  The old Tax facts client used
    ``metrics_json``/``raw_response_json`` constructor arguments.  Those
    arguments remain accepted as a compatibility adapter, while they are
    normalised into the canonical ``facts_data`` JSON field and are not
    persisted as a second source of truth.
    """
    __tablename__ = "facts_snapshots"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    project_code: Mapped[str] = mapped_column(String(64), index=True)
    facts_data: Mapped[dict] = mapped_column(JSON, nullable=False)
    as_of: Mapped[str] = mapped_column(String(40), index=True)
    facts_version: Mapped[str] = mapped_column(String(64), index=True)
    analytics_contract_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="1.0"
    )
    created_at: Mapped[str] = mapped_column(
        IsoDateTime, nullable=False,
        default=lambda: datetime.now(timezone.utc).isoformat(),
        index=True,
    )
    created_by: Mapped[str | None] = mapped_column(String(80), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "project_code", "facts_version",
            name="uq_facts_snapshots_project_facts_version",
        ),
    )

    @staticmethod
    def _decode_legacy_json(value, default):
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
            except (TypeError, ValueError, json.JSONDecodeError):
                return default
            return parsed if isinstance(parsed, dict) else default
        return default

    def __init__(self, **kwargs):
        """Accept the pre-canonical Tax client shape without dual columns.

        This adapter is intentionally kept at the ORM boundary so the
        existing Tax service can be upgraded atomically with the database
        migration.  New code should pass ``facts_data`` directly.
        """
        legacy_metrics = kwargs.pop("metrics_json", None)
        legacy_raw = kwargs.pop("raw_response_json", None)
        legacy_meta = {
            "source": kwargs.pop("source", "rag_v1"),
            "require_fresh": bool(kwargs.pop("require_fresh", False)),
            "max_age": int(kwargs.pop("max_age", 60) or 60),
            "requested_at": kwargs.pop("requested_at", ""),
            "requested_by": kwargs.pop("requested_by", "system"),
            "note": kwargs.pop("note", ""),
        }

        facts_data = kwargs.get("facts_data")
        if facts_data is None:
            raw_payload = self._decode_legacy_json(legacy_raw, {})
            metrics = self._decode_legacy_json(legacy_metrics, {})
            facts_data = dict(raw_payload)
            facts_data.setdefault("metrics", metrics)
            facts_data.setdefault("facts_available", True)
            facts_data.setdefault("project_code", kwargs.get("project_code", ""))
            facts_data.setdefault("as_of", kwargs.get("as_of", ""))
            facts_data.setdefault("facts_version", kwargs.get("facts_version", ""))
            # Retain non-canonical Tax request metadata inside the immutable
            # payload instead of maintaining a second set of table columns.
            facts_data.setdefault("_snapshot_metadata", legacy_meta)
            kwargs["facts_data"] = facts_data

        kwargs.setdefault(
            "created_at",
            legacy_meta["requested_at"] or datetime.now(timezone.utc).isoformat(),
        )
        kwargs.setdefault("created_by", legacy_meta["requested_by"] or "system")
        super().__init__(**kwargs)

    def _snapshot_metadata(self) -> dict:
        data = self.facts_data if isinstance(self.facts_data, dict) else {}
        metadata = data.get("_snapshot_metadata")
        return metadata if isinstance(metadata, dict) else {}

    # Compatibility read properties for existing Tax routes/services.  They
    # deliberately derive from canonical facts_data rather than database
    # columns, preventing metrics/raw response drift.
    @property
    def metrics_json(self) -> str:
        data = self.facts_data if isinstance(self.facts_data, dict) else {}
        return json.dumps(data.get("metrics", {}), ensure_ascii=False)

    @property
    def raw_response_json(self) -> str:
        return json.dumps(self.facts_data or {}, ensure_ascii=False)

    @property
    def source(self) -> str:
        return str(self._snapshot_metadata().get("source") or self.facts_data.get("source", "rag_v1"))

    @property
    def require_fresh(self) -> bool:
        return bool(self._snapshot_metadata().get("require_fresh", False))

    @property
    def max_age(self) -> int:
        try:
            return int(self._snapshot_metadata().get("max_age", 60))
        except (TypeError, ValueError):
            return 60

    @property
    def requested_at(self) -> str:
        return str(self._snapshot_metadata().get("requested_at") or self.created_at or "")

    @property
    def requested_by(self) -> str:
        return str(self._snapshot_metadata().get("requested_by") or self.created_by or "system")

    @property
    def note(self) -> str:
        return str(self._snapshot_metadata().get("note") or "")


class FactsRequestLog(Base):
    """Facts Provider 调用记录（审计 + 缓存管理）。"""
    __tablename__ = "facts_request_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_code: Mapped[str] = mapped_column(String(30), index=True)
    endpoint: Mapped[str] = mapped_column(String(200), default="/api/v1/facts/projects/{project_code}")
    require_fresh: Mapped[bool] = mapped_column(Boolean, default=False)
    max_age: Mapped[int] = mapped_column(Integer, default=60)
    as_of_param: Mapped[str] = mapped_column(String(40), default="")
    response_status: Mapped[int] = mapped_column(Integer, default=0)
    facts_snapshot_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("facts_snapshots.id"), nullable=True, index=True
    )
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(80), default="system", index=True)
    ip: Mapped[str] = mapped_column(String(45), default="")
    request_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    created_at: Mapped[str] = mapped_column(IsoDateTime, default="", index=True)


class PlanningScenario(Base):
    """Auditable project allocation planning scenario selected from validated candidates."""
    __tablename__ = "planning_scenarios"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    package_name: Mapped[str] = mapped_column(String(120), default="")
    category: Mapped[str] = mapped_column(String(30), index=True)
    package_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    objective: Mapped[str] = mapped_column(String(20), default="balanced", index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    score: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("0"))
    internal_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    external_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    projected_external_cost: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    projected_tax_cash: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    projected_profit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    risk_score: Mapped[Decimal] = mapped_column(Numeric(6, 4), default=Decimal("0"))
    evidence_quality: Mapped[Decimal] = mapped_column(Numeric(6, 4), default=Decimal("0"))
    request_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    ai_summary: Mapped[str] = mapped_column(Text, default="")
    ai_json: Mapped[str] = mapped_column(Text, default="{}")
    created_by: Mapped[str] = mapped_column(String(80), default="system", index=True)
    created_at: Mapped[str] = mapped_column(IsoDateTime, default="", index=True)


class PlanningAllocation(Base):
    """Per-party allocation belonging to a planning scenario."""
    __tablename__ = "planning_allocations"
    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("planning_scenarios.id"), index=True)
    party_scope: Mapped[str] = mapped_column(String(10), index=True)
    party_code: Mapped[str] = mapped_column(String(16), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    share: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    estimated_external_cost: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    estimated_tax_cash: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    risk_score: Mapped[Decimal] = mapped_column(Numeric(6, 4), default=Decimal("0"))
    evidence_quality: Mapped[Decimal] = mapped_column(Numeric(6, 4), default=Decimal("0"))
    rationale: Mapped[str] = mapped_column(Text, default="")

    __table_args__ = (
        CheckConstraint("party_scope IN ('internal','external')", name="ck_planning_allocation_scope"),
    )


class User(Base):
    """系统用户：管理员（admin）或操作员（operator）。

    admin: 系统管理员，拥有用户管理权限，可查看/修改所有数据。
    operator: 业务操作员，拥有业务数据读写权限，可录入发票/合同/成本等。
    """
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20), default="operator", index=True)  # admin / operator
    display_name: Mapped[str] = mapped_column(String(80), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[str] = mapped_column(IsoDateTime, default="")
    last_login: Mapped[str] = mapped_column(String(30), default="")

    __table_args__ = (
        UniqueConstraint("username", name="uq_user_username"),
    )


__all__ = [
    "Entity", "ExternalParty", "Project", "Contract", "Invoice", "CashFlow",
    "Fulfillment", "RealCost", "Progress", "Budget", "CostAccount",
    "TaxRule", "TaxLedger", "EntityBankAccount", "TaxPaymentRecord",
    "RiskEvent", "AuditLog", "RiskThreshold",
    "AIModelEndpoint", "AIReviewJob", "AIReviewResult",
    "AIPromptTemplate", "AIReviewBatch", "AIConsensusReport",
    "RemediationTask", "SyncLog", "SyncPending",
    "FactsSnapshot", "FactsRequestLog",
    "PlanningScenario", "PlanningAllocation",
    "User",
]
