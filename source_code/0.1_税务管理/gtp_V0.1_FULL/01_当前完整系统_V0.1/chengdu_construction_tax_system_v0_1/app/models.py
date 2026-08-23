from sqlalchemy import String, Float, ForeignKey, Boolean, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base

class Entity(Base):
    __tablename__="entities"
    id:Mapped[int]=mapped_column(primary_key=True)
    code:Mapped[str]=mapped_column(String(8),unique=True,index=True)
    name:Mapped[str]=mapped_column(String(100))
    kind:Mapped[str]=mapped_column(String(30))
    internal:Mapped[bool]=mapped_column(Boolean,index=True)

class Project(Base):
    __tablename__="projects"
    id:Mapped[int]=mapped_column(primary_key=True)
    code:Mapped[str]=mapped_column(String(30),unique=True,index=True)
    name:Mapped[str]=mapped_column(String(120))
    city:Mapped[str]=mapped_column(String(40))
    contract_total:Mapped[float]=mapped_column(Float)
    tax_method:Mapped[str]=mapped_column(String(20),default="general")

class Contract(Base):
    __tablename__="contracts"
    id:Mapped[int]=mapped_column(primary_key=True)
    project_id:Mapped[int]=mapped_column(ForeignKey("projects.id"),index=True)
    contract_no:Mapped[str]=mapped_column(String(50),default="")
    buyer_code:Mapped[str]=mapped_column(String(8),index=True)
    seller_code:Mapped[str]=mapped_column(String(8),index=True)
    category:Mapped[str]=mapped_column(String(30),index=True)
    amount:Mapped[float]=mapped_column(Float)
    internal_trade:Mapped[bool]=mapped_column(Boolean,default=False,index=True)
    note:Mapped[str]=mapped_column(String(200),default="")

class Invoice(Base):
    __tablename__="invoices"
    id:Mapped[int]=mapped_column(primary_key=True)
    project_id:Mapped[int]=mapped_column(ForeignKey("projects.id"),index=True)
    invoice_no:Mapped[str]=mapped_column(String(60),default="",index=True)
    period:Mapped[str]=mapped_column(String(7),index=True)
    entity_code:Mapped[str]=mapped_column(String(8),index=True)
    direction:Mapped[str]=mapped_column(String(10),index=True)  # in/out
    counterparty_code:Mapped[str]=mapped_column(String(8),index=True)
    category:Mapped[str]=mapped_column(String(30),index=True)
    net:Mapped[float]=mapped_column(Float)
    vat:Mapped[float]=mapped_column(Float)
    rate:Mapped[float]=mapped_column(Float,default=0)
    deductible:Mapped[bool]=mapped_column(Boolean,default=True)
    note:Mapped[str]=mapped_column(String(200),default="")

class CashFlow(Base):
    __tablename__="cashflows"
    id:Mapped[int]=mapped_column(primary_key=True)
    project_id:Mapped[int]=mapped_column(ForeignKey("projects.id"),index=True)
    entity_code:Mapped[str]=mapped_column(String(8),index=True)
    counterparty_code:Mapped[str]=mapped_column(String(8),index=True)
    direction:Mapped[str]=mapped_column(String(10),index=True) # in/out
    amount:Mapped[float]=mapped_column(Float)
    period:Mapped[str]=mapped_column(String(7),index=True)
    note:Mapped[str]=mapped_column(String(200),default="")

class Fulfillment(Base):
    __tablename__="fulfillment"
    id:Mapped[int]=mapped_column(primary_key=True)
    project_id:Mapped[int]=mapped_column(ForeignKey("projects.id"),index=True)
    counterparty_code:Mapped[str]=mapped_column(String(8),index=True)
    kind:Mapped[str]=mapped_column(String(30),index=True)
    category:Mapped[str]=mapped_column(String(30),default="",index=True)
    quantity:Mapped[float]=mapped_column(Float,default=0)
    amount:Mapped[float]=mapped_column(Float,default=0)
    evidence_complete:Mapped[bool]=mapped_column(Boolean,default=False,index=True)
    note:Mapped[str]=mapped_column(String(200),default="")

class RealCost(Base):
    __tablename__="real_costs"
    id:Mapped[int]=mapped_column(primary_key=True)
    project_id:Mapped[int]=mapped_column(ForeignKey("projects.id"),index=True)
    entity_code:Mapped[str]=mapped_column(String(8),index=True)  # 成本承担法人 A/B/C/D
    counterparty_code:Mapped[str]=mapped_column(String(8),default="",index=True)  # 成本来源/外部交易对手
    category:Mapped[str]=mapped_column(String(30),index=True)
    subcategory:Mapped[str]=mapped_column(String(50),default="",index=True)
    period:Mapped[str]=mapped_column(String(7),default="",index=True)
    amount:Mapped[float]=mapped_column(Float)
    external_cash:Mapped[bool]=mapped_column(Boolean)
    note:Mapped[str]=mapped_column(String(200),default="")

class Progress(Base):
    __tablename__="progress"
    id:Mapped[int]=mapped_column(primary_key=True)
    project_id:Mapped[int]=mapped_column(ForeignKey("projects.id"),index=True)
    period:Mapped[str]=mapped_column(String(7),index=True)
    output_value:Mapped[float]=mapped_column(Float)
    settlement:Mapped[float]=mapped_column(Float)
    recognized_revenue:Mapped[float]=mapped_column(Float)
    collection:Mapped[float]=mapped_column(Float)

class Budget(Base):
    __tablename__="budgets"
    id:Mapped[int]=mapped_column(primary_key=True)
    project_id:Mapped[int]=mapped_column(ForeignKey("projects.id"),index=True)
    category:Mapped[str]=mapped_column(String(30),index=True)
    amount:Mapped[float]=mapped_column(Float)

class CostAccount(Base):
    __tablename__="cost_accounts"
    id:Mapped[int]=mapped_column(primary_key=True)
    code:Mapped[str]=mapped_column(String(20),unique=True,index=True)
    parent_code:Mapped[str]=mapped_column(String(20),default="")
    name:Mapped[str]=mapped_column(String(100))
    category:Mapped[str]=mapped_column(String(30),index=True)
    active:Mapped[bool]=mapped_column(Boolean,default=True)

class TaxRule(Base):
    __tablename__="tax_rules"
    id:Mapped[int]=mapped_column(primary_key=True)
    code:Mapped[str]=mapped_column(String(80),unique=True,index=True)
    rate:Mapped[float]=mapped_column(Float)
    effective_from:Mapped[str]=mapped_column(String(10))
    effective_to:Mapped[str]=mapped_column(String(10),default="")
    source:Mapped[str]=mapped_column(String(300),default="")
    reviewed:Mapped[bool]=mapped_column(Boolean,default=False)
    note:Mapped[str]=mapped_column(String(300),default="")

class TaxLedger(Base):
    __tablename__="tax_ledgers"
    id:Mapped[int]=mapped_column(primary_key=True)
    period:Mapped[str]=mapped_column(String(7),index=True)
    entity_code:Mapped[str]=mapped_column(String(8),index=True)
    output_vat:Mapped[float]=mapped_column(Float,default=0)
    input_vat:Mapped[float]=mapped_column(Float,default=0)
    vat_payable:Mapped[float]=mapped_column(Float,default=0)
    revenue:Mapped[float]=mapped_column(Float,default=0)
    real_cost:Mapped[float]=mapped_column(Float,default=0)
    estimated_profit:Mapped[float]=mapped_column(Float,default=0)
    estimated_cit:Mapped[float]=mapped_column(Float,default=0)
    generated:Mapped[bool]=mapped_column(Boolean,default=True)

class RiskEvent(Base):
    __tablename__="risk_events"
    id:Mapped[int]=mapped_column(primary_key=True)
    project_id:Mapped[int]=mapped_column(ForeignKey("projects.id"),index=True)
    severity:Mapped[str]=mapped_column(String(10),index=True)
    code:Mapped[str]=mapped_column(String(50),index=True)
    message:Mapped[str]=mapped_column(String(300))
    resolved:Mapped[bool]=mapped_column(Boolean,default=False,index=True)

class AuditLog(Base):
    __tablename__="audit_logs"
    id:Mapped[int]=mapped_column(primary_key=True)
    action:Mapped[str]=mapped_column(String(30),index=True)
    object_type:Mapped[str]=mapped_column(String(30),index=True)
    object_id:Mapped[str]=mapped_column(String(50),default="")
    message:Mapped[str]=mapped_column(Text,default="")


class AIModelEndpoint(Base):
    __tablename__="ai_model_endpoints"
    id:Mapped[int]=mapped_column(primary_key=True)
    name:Mapped[str]=mapped_column(String(100),unique=True,index=True)
    adapter:Mapped[str]=mapped_column(String(30),default="openai_compatible")  # mock/openai_compatible
    base_url:Mapped[str]=mapped_column(String(300),default="")
    chat_path:Mapped[str]=mapped_column(String(200),default="/v1/chat/completions")
    model:Mapped[str]=mapped_column(String(120),default="")
    api_key_env:Mapped[str]=mapped_column(String(120),default="")  # 只保存环境变量名，不保存密钥
    enabled:Mapped[bool]=mapped_column(Boolean,default=True,index=True)
    timeout_seconds:Mapped[int]=mapped_column(Integer,default=90)
    note:Mapped[str]=mapped_column(String(300),default="")

class AIReviewJob(Base):
    __tablename__="ai_review_jobs"
    id:Mapped[int]=mapped_column(primary_key=True)
    project_id:Mapped[int]=mapped_column(ForeignKey("projects.id"),index=True)
    scope:Mapped[str]=mapped_column(String(40),index=True)
    endpoint_id:Mapped[int]=mapped_column(ForeignKey("ai_model_endpoints.id"),index=True)
    batch_id:Mapped[int|None]=mapped_column(Integer,nullable=True,index=True)
    prompt_template_id:Mapped[int|None]=mapped_column(Integer,nullable=True,index=True)
    parent_job_id:Mapped[int|None]=mapped_column(Integer,nullable=True,index=True)
    user_instruction:Mapped[str]=mapped_column(Text,default="")
    status:Mapped[str]=mapped_column(String(20),default="pending",index=True)
    created_at:Mapped[str]=mapped_column(String(30),default="")
    started_at:Mapped[str]=mapped_column(String(30),default="")
    finished_at:Mapped[str]=mapped_column(String(30),default="")
    input_digest:Mapped[str]=mapped_column(String(64),default="")
    input_preview:Mapped[str]=mapped_column(Text,default="")
    error_message:Mapped[str]=mapped_column(Text,default="")

class AIReviewResult(Base):
    __tablename__="ai_review_results"
    id:Mapped[int]=mapped_column(primary_key=True)
    job_id:Mapped[int]=mapped_column(ForeignKey("ai_review_jobs.id"),unique=True,index=True)
    provider_name:Mapped[str]=mapped_column(String(100),default="")
    model_name:Mapped[str]=mapped_column(String(120),default="")
    risk_level:Mapped[str]=mapped_column(String(20),default="UNKNOWN")
    score:Mapped[float]=mapped_column(Float,default=0)
    summary:Mapped[str]=mapped_column(Text,default="")
    findings_json:Mapped[str]=mapped_column(Text,default="[]")
    recommendations_json:Mapped[str]=mapped_column(Text,default="[]")
    data_gaps_json:Mapped[str]=mapped_column(Text,default="[]")
    raw_response:Mapped[str]=mapped_column(Text,default="")


class AIPromptTemplate(Base):
    __tablename__="ai_prompt_templates"
    id:Mapped[int]=mapped_column(primary_key=True)
    name:Mapped[str]=mapped_column(String(100),index=True)
    scope:Mapped[str]=mapped_column(String(40),index=True)
    version:Mapped[int]=mapped_column(Integer,default=1)
    system_addendum:Mapped[str]=mapped_column(Text,default="")
    review_focus:Mapped[str]=mapped_column(Text,default="")
    enabled:Mapped[bool]=mapped_column(Boolean,default=True,index=True)
    created_at:Mapped[str]=mapped_column(String(30),default="")

class AIReviewBatch(Base):
    __tablename__="ai_review_batches"
    id:Mapped[int]=mapped_column(primary_key=True)
    project_id:Mapped[int]=mapped_column(ForeignKey("projects.id"),index=True)
    profile:Mapped[str]=mapped_column(String(30),default="standard",index=True)
    scopes_json:Mapped[str]=mapped_column(Text,default="[]")
    endpoint_ids_json:Mapped[str]=mapped_column(Text,default="[]")
    user_instruction:Mapped[str]=mapped_column(Text,default="")
    status:Mapped[str]=mapped_column(String(20),default="pending",index=True)
    created_at:Mapped[str]=mapped_column(String(30),default="")
    finished_at:Mapped[str]=mapped_column(String(30),default="")
    error_message:Mapped[str]=mapped_column(Text,default="")

class AIConsensusReport(Base):
    __tablename__="ai_consensus_reports"
    id:Mapped[int]=mapped_column(primary_key=True)
    batch_id:Mapped[int]=mapped_column(ForeignKey("ai_review_batches.id"),unique=True,index=True)
    overall_risk:Mapped[str]=mapped_column(String(20),default="UNKNOWN")
    score:Mapped[float]=mapped_column(Float,default=0)
    summary:Mapped[str]=mapped_column(Text,default="")
    common_findings_json:Mapped[str]=mapped_column(Text,default="[]")
    differences_json:Mapped[str]=mapped_column(Text,default="[]")
    recommendations_json:Mapped[str]=mapped_column(Text,default="[]")
    data_gaps_json:Mapped[str]=mapped_column(Text,default="[]")

class RemediationTask(Base):
    __tablename__="remediation_tasks"
    id:Mapped[int]=mapped_column(primary_key=True)
    project_id:Mapped[int]=mapped_column(ForeignKey("projects.id"),index=True)
    scope:Mapped[str]=mapped_column(String(40),default="whole_project",index=True)
    source_job_id:Mapped[int|None]=mapped_column(Integer,nullable=True,index=True)
    source_batch_id:Mapped[int|None]=mapped_column(Integer,nullable=True,index=True)
    recheck_job_id:Mapped[int|None]=mapped_column(Integer,nullable=True,index=True)
    title:Mapped[str]=mapped_column(String(200))
    description:Mapped[str]=mapped_column(Text,default="")
    priority:Mapped[str]=mapped_column(String(10),default="P2",index=True)
    owner_role:Mapped[str]=mapped_column(String(80),default="项目财务/商务")
    status:Mapped[str]=mapped_column(String(20),default="open",index=True)
    created_at:Mapped[str]=mapped_column(String(30),default="")
    updated_at:Mapped[str]=mapped_column(String(30),default="")
    closed_at:Mapped[str]=mapped_column(String(30),default="")
