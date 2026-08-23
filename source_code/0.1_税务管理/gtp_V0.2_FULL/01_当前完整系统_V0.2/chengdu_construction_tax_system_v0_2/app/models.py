"""V0.2: SQLAlchemy ORM 模型。金额统一使用 Numeric(18, 2)。"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .constants import DEFAULT_RISK_THRESHOLDS
from .db import Base


class Entity(Base):
    __tablename__ = "entities"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(8), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(30))
    internal: Mapped[bool] = mapped_column(Boolean, index=True)
    tax_id: Mapped[str] = mapped_column(String(40), default="", index=True)  # 纳税人识别号（统一社会信用代码）
    short_name: Mapped[str] = mapped_column(String(60), default="", index=True)  # RAG 抽取的名称模糊匹配用


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    city: Mapped[str] = mapped_column(String(40))
    contract_total: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    tax_method: Mapped[str] = mapped_column(String(20), default="general")


class Contract(Base):
    __tablename__ = "contracts"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    contract_no: Mapped[str] = mapped_column(String(50), default="")
    buyer_code: Mapped[str] = mapped_column(String(8), index=True)
    seller_code: Mapped[str] = mapped_column(String(8), index=True)
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
    entity_code: Mapped[str] = mapped_column(String(8), index=True)
    direction: Mapped[str] = mapped_column(String(10), index=True)  # in/out
    counterparty_code: Mapped[str] = mapped_column(String(8), index=True)
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
    entity_code: Mapped[str] = mapped_column(String(8), index=True)
    counterparty_code: Mapped[str] = mapped_column(String(8), index=True)
    direction: Mapped[str] = mapped_column(String(10), index=True)  # in/out
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    period: Mapped[str] = mapped_column(String(7), index=True)
    note: Mapped[str] = mapped_column(String(200), default="")


class Fulfillment(Base):
    __tablename__ = "fulfillment"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    counterparty_code: Mapped[str] = mapped_column(String(8), index=True)
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
    entity_code: Mapped[str] = mapped_column(String(8), index=True)
    counterparty_code: Mapped[str] = mapped_column(String(8), default="", index=True)
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
    entity_code: Mapped[str] = mapped_column(String(8), index=True)
    output_vat: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    input_vat: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    vat_payable: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    revenue: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    real_cost: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    estimated_profit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    estimated_cit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    cit_note: Mapped[str] = mapped_column(String(500), default="")
    generated: Mapped[bool] = mapped_column(Boolean, default=True)


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
    def defaults(cls) -> list["RiskThreshold"]:
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
    api_key_env: Mapped[str] = mapped_column(String(120), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=90)
    note: Mapped[str] = mapped_column(String(300), default="")


class AIReviewJob(Base):
    __tablename__ = "ai_review_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    scope: Mapped[str] = mapped_column(String(40), index=True)
    endpoint_id: Mapped[int] = mapped_column(ForeignKey("ai_model_endpoints.id"), index=True)
    batch_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    prompt_template_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    parent_job_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    user_instruction: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    parse_failed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[str] = mapped_column(String(30), default="")
    started_at: Mapped[str] = mapped_column(String(30), default="")
    finished_at: Mapped[str] = mapped_column(String(30), default="")
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
    created_at: Mapped[str] = mapped_column(String(30), default="")

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
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[str] = mapped_column(String(30), default="")
    finished_at: Mapped[str] = mapped_column(String(30), default="")
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
    source_job_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    source_batch_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    recheck_job_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str] = mapped_column(String(10), default="P2", index=True)
    owner_role: Mapped[str] = mapped_column(String(80), default="项目财务/商务")
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    created_at: Mapped[str] = mapped_column(String(30), default="")
    updated_at: Mapped[str] = mapped_column(String(30), default="")
    closed_at: Mapped[str] = mapped_column(String(30), default="")
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
    synced_at: Mapped[str] = mapped_column(String(30), default="")
    created_at: Mapped[str] = mapped_column(String(30), default="")


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
    synced_at: Mapped[str] = mapped_column(String(30), default="")
    synced_by: Mapped[str] = mapped_column(String(80), default="rag_ai")
    note: Mapped[str] = mapped_column(String(300), default="")


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
    page_start: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 3))  # 0.000-1.000
    fields_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending/confirmed/rejected
    confirmed_record_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 确认后对应的税务记录 ID
    confirmed_at: Mapped[str] = mapped_column(String(30), default="")
    confirmed_by: Mapped[str] = mapped_column(String(80), default="")
    note: Mapped[str] = mapped_column(String(200), default="")


class FactsSnapshot(Base):
    """RAG V1.0 Facts Provider 响应快照。

    每次税务系统向 Facts Provider 拉取指标时保存一份快照，
    保证后续审计/复核能还原当时的口径与版本（as_of + facts_version）。
    """
    __tablename__ = "facts_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    project_code: Mapped[str] = mapped_column(String(30), index=True)
    as_of: Mapped[str] = mapped_column(String(40), index=True)  # 数据截止时间
    facts_version: Mapped[str] = mapped_column(String(40), index=True)  # RAG 返回的事实版本
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")  # 全部指标快照
    raw_response_json: Mapped[str] = mapped_column(Text, default="{}")  # 原始响应
    source: Mapped[str] = mapped_column(String(20), default="rag_v1")  # rag_v1 / manual
    require_fresh: Mapped[bool] = mapped_column(Boolean, default=False)
    max_age: Mapped[int] = mapped_column(Integer, default=60)
    requested_at: Mapped[str] = mapped_column(String(30), default="", index=True)
    requested_by: Mapped[str] = mapped_column(String(80), default="system")
    note: Mapped[str] = mapped_column(String(300), default="")


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
    facts_snapshot_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(80), default="system", index=True)
    ip: Mapped[str] = mapped_column(String(45), default="")
    created_at: Mapped[str] = mapped_column(String(30), default="", index=True)


__all__ = [
    "Entity", "Project", "Contract", "Invoice", "CashFlow",
    "Fulfillment", "RealCost", "Progress", "Budget", "CostAccount",
    "TaxRule", "TaxLedger", "RiskEvent", "AuditLog", "RiskThreshold",
    "AIModelEndpoint", "AIReviewJob", "AIReviewResult",
    "AIPromptTemplate", "AIReviewBatch", "AIConsensusReport",
    "RemediationTask", "SyncLog", "SyncPending",
    "FactsSnapshot", "FactsRequestLog",
]