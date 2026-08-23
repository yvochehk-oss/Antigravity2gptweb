"""Database models for ProjectRAG V0.2 Optimized."""
from sqlalchemy import String, Integer, Float, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base
from .config import IS_POSTGRES, EMBEDDING_DIM

if IS_POSTGRES:
    from pgvector.sqlalchemy import Vector


class Project(Base):
    """Project entity representing a construction project knowledge space."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    external_system: Mapped[str] = mapped_column(String(80), default="")
    external_project_id: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(24), default="ACTIVE", index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(40), default="")
    updated_at: Mapped[str] = mapped_column(String(40), default="")


class Document(Base):
    """Document entity with metadata and parsing status tracking."""

    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_project_status", "project_id", "parse_status"),
        Index("ix_documents_hash", "file_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    document_code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    filename: Mapped[str] = mapped_column(String(300))
    file_type: Mapped[str] = mapped_column(String(24), index=True)
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    original_path: Mapped[str] = mapped_column(String(600))
    parsed_dir: Mapped[str] = mapped_column(String(600), default="")
    markdown_path: Mapped[str] = mapped_column(String(600), default="")
    content_list_path: Mapped[str] = mapped_column(String(600), default="")
    parse_status: Mapped[str] = mapped_column(String(32), default="UPLOADED", index=True)
    parse_message: Mapped[str] = mapped_column(Text, default="")
    parse_attempts: Mapped[int] = mapped_column(Integer, default=0)  # NEW: track attempts
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), nullable=True)

    # Metadata
    document_type: Mapped[str] = mapped_column(String(60), default="other", index=True)
    entity_code: Mapped[str] = mapped_column(String(16), default="", index=True)
    counterparty_code: Mapped[str] = mapped_column(String(16), default="", index=True)
    business_category: Mapped[str] = mapped_column(String(40), default="", index=True)
    tax_category: Mapped[str] = mapped_column(String(32), default="", index=True)  # 税务分类

    # ========== 税务金额字段（按中国税务系统要求）==========
    # 增值税
    tax_vat_rate: Mapped[float] = mapped_column(Float, default=0.0)  # 增值税税率（%）
    tax_vat_input: Mapped[float] = mapped_column(Float, default=0.0)  # 进项税额（可抵扣）
    tax_vat_output: Mapped[float] = mapped_column(Float, default=0.0)  # 销项税额
    tax_vat_paid: Mapped[float] = mapped_column(Float, default=0.0)  # 已交增值税

    # 企业所得税
    tax_income_rate: Mapped[float] = mapped_column(Float, default=0.0)  # 所得税税率（%）
    tax_income_amount: Mapped[float] = mapped_column(Float, default=0.0)  # 应纳所得税额
    tax_income_paid: Mapped[float] = mapped_column(Float, default=0.0)  # 已预缴所得税

    # 个人所得税（代扣代缴）
    tax_individual_rate: Mapped[float] = mapped_column(Float, default=0.0)  # 个税税率（%）
    tax_individual_amount: Mapped[float] = mapped_column(Float, default=0.0)  # 代扣代缴个税
    tax_individual_paid: Mapped[float] = mapped_column(Float, default=0.0)  # 已缴个税

    # 附加税
    tax_surtax_urban: Mapped[float] = mapped_column(Float, default=0.0)  # 城建税
    tax_surtax_edu: Mapped[float] = mapped_column(Float, default=0.0)  # 教育费附加
    tax_surtax_local_edu: Mapped[float] = mapped_column(Float, default=0.0)  # 地方教育附加

    # 其他税种
    tax_stamp_duty: Mapped[float] = mapped_column(Float, default=0.0)  # 印花税
    tax_land: Mapped[float] = mapped_column(Float, default=0.0)  # 土地增值税
    tax_environmental: Mapped[float] = mapped_column(Float, default=0.0)  # 环境保护税

    # 发票信息
    invoice_no: Mapped[str] = mapped_column(String(50), default="")  # 发票号码
    invoice_code: Mapped[str] = mapped_column(String(20), default="")  # 发票代码
    invoice_type: Mapped[str] = mapped_column(String(20), default="")  # 发票类型（增值税专用发票/普通发票）
    invoice_date: Mapped[str] = mapped_column(String(20), default="")  # 开票日期
    invoice_deductible: Mapped[bool] = mapped_column(default=False)  # 是否已勾选抵扣

    # 合计字段
    tax_total: Mapped[float] = mapped_column(Float, default=0.0)  # 税金合计

    contract_no: Mapped[str] = mapped_column(String(100), default="", index=True)
    period: Mapped[str] = mapped_column(String(20), default="", index=True)
    document_date: Mapped[str] = mapped_column(String(20), default="")
    confidentiality: Mapped[str] = mapped_column(String(32), default="PROJECT", index=True)
    version_label: Mapped[str] = mapped_column(String(40), default="V1")
    version_status: Mapped[str] = mapped_column(String(24), default="effective", index=True)
    metadata_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    metadata_source: Mapped[str] = mapped_column(String(32), default="filename")

    # Timestamps
    created_at: Mapped[str] = mapped_column(String(40), default="")
    updated_at: Mapped[str] = mapped_column(String(40), default="")


class Chunk(Base):
    """Document chunk with vector embedding for semantic search."""

    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_doc_chunk_index"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    heading_path: Mapped[str] = mapped_column(String(500), default="")
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_type: Mapped[str] = mapped_column(String(30), default="text")
    content: Mapped[str] = mapped_column(Text)
    token_estimate: Mapped[int] = mapped_column(Integer, default=0)
    embedding_json: Mapped[str] = mapped_column(Text, default="[]")

    if IS_POSTGRES:
        embedding: Mapped[list | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    else:
        embedding: Mapped[str | None] = mapped_column(Text, nullable=True)

    search_text: Mapped[str] = mapped_column(Text, default="", index=True)

    # === V0.3 Chunk metadata ===
    chunk_strategy_version: Mapped[str] = mapped_column(String(32), default="v0.3_structured", index=True)
    title_chain: Mapped[str] = mapped_column(Text, default="")
    semantic_type: Mapped[str] = mapped_column(String(32), default="plain", index=True)


class QueryLog(Base):
    """Query log for audit trail and analytics."""

    __tablename__ = "query_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    query: Mapped[str] = mapped_column(Text)
    filters_json: Mapped[str] = mapped_column(Text, default="{}")
    top_k: Mapped[int] = mapped_column(Integer, default=10)
    result_chunk_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    mode: Mapped[str] = mapped_column(String(24), default="retrieve")
    embedding_backend: Mapped[str] = mapped_column(String(40), default="")
    reranker_backend: Mapped[str] = mapped_column(String(40), default="")
    response_time_ms: Mapped[int] = mapped_column(Integer, default=0)  # NEW: track latency
    created_at: Mapped[str] = mapped_column(String(40), default="")

    # === V0.3 Query log fields ===
    rewrite_result_json: Mapped[str] = mapped_column(Text, default="")
    hyde_used: Mapped[bool] = mapped_column(default=False)
    quality_gate_json: Mapped[str] = mapped_column(Text, default="")
    bm25_candidates_json: Mapped[str] = mapped_column(Text, default="[]")
    vector_candidates_json: Mapped[str] = mapped_column(Text, default="[]")
    reranked_json: Mapped[str] = mapped_column(Text, default="[]")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    chunk_version: Mapped[str] = mapped_column(String(32), default="")
    embedding_version: Mapped[str] = mapped_column(String(32), default="")
    reranker_version: Mapped[str] = mapped_column(String(32), default="")
    retrieval_status: Mapped[str] = mapped_column(String(32), default="")
    deep_mode: Mapped[bool] = mapped_column(default=False)
    answer_faithful: Mapped[bool] = mapped_column(default=False)
    no_answer_confidence: Mapped[float] = mapped_column(Float, default=0.0)


class IngestJob(Base):
    """Ingest job queue with retry support."""

    __tablename__ = "ingest_jobs"
    __table_args__ = (
        Index("ix_jobs_status_priority", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    job_type: Mapped[str] = mapped_column(String(24), default="PARSE_INDEX", index=True)
    status: Mapped[str] = mapped_column(String(24), default="QUEUED", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)  # NEW: configurable retry limit
    message: Mapped[str] = mapped_column(Text, default="")
    last_error: Mapped[str] = mapped_column(Text, default="")  # NEW: store last error
    created_at: Mapped[str] = mapped_column(String(40), default="")
    started_at: Mapped[str] = mapped_column(String(40), default="")
    finished_at: Mapped[str] = mapped_column(String(40), default="")
    next_retry_at: Mapped[str] = mapped_column(String(40), default="")  # NEW: for exponential backoff

    # === V0.3 Parse quality fields ===
    parse_quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    is_encrypted: Mapped[bool] = mapped_column(default=False)
    parse_quality_flags_json: Mapped[str] = mapped_column(Text, default="[]")
    chunk_strategy_version: Mapped[str] = mapped_column(String(32), default="")


class Entity(Base):
    """Entity (往来单位) basic information table.

    Stores basic information about companies and counterparties:
    - Legal representative, shareholders, supervisors
    - Registration details (capital, date, authority)
    - Business scope, contact info
    """

    __tablename__ = "entities"
    __table_args__ = (
        Index("ix_entities_name", "name"),
        Index("ix_entities_short_name", "short_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Basic identification
    name: Mapped[str] = mapped_column(String(200), index=True, default="")
    short_name: Mapped[str] = mapped_column(String(80), default="", index=True)
    entity_type: Mapped[str] = mapped_column(String(32), default="", index=True)  # 公司/个体工商户/分公司
    industry: Mapped[str] = mapped_column(String(32), default="", index=True)     # 工程/劳务/贸易/机械/个体/其他

    # Legal representative
    legal_representative: Mapped[str] = mapped_column(String(80), default="")
    legal_rep_id: Mapped[str] = mapped_column(String(32), default="")  # 身份证号
    legal_rep_phone: Mapped[str] = mapped_column(String(32), default="")

    # Shareholders
    shareholders: Mapped[str] = mapped_column(Text, default="")  # JSON-like text for multiple shareholders
    supervisor: Mapped[str] = mapped_column(String(80), default="")
    finance_officer: Mapped[str] = mapped_column(String(80), default="")

    # Registration details
    registered_capital: Mapped[str] = mapped_column(String(40), default="")  # e.g. "5000万"
    establishment_date: Mapped[str] = mapped_column(String(20), default="")
    acquisition_date: Mapped[str] = mapped_column(String(20), default="")      # 收购时间（子公司）
    registration_authority: Mapped[str] = mapped_column(String(120), default="")
    registration_number: Mapped[str] = mapped_column(String(40), default="")
    unified_social_credit_code: Mapped[str] = mapped_column(String(40), default="")
    business_scope: Mapped[str] = mapped_column(Text, default="")

    # Contributed capital
    contributed_legal: Mapped[float] = mapped_column(Float, default=0.0)   # 法人已出资
    contributed_shareholder: Mapped[float] = mapped_column(Float, default=0.0)  # 股东已出资

    # Notes and source
    note: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(32), default="excel")  # excel / manual
    data_as_of: Mapped[str] = mapped_column(String(20), default="")  # 数据截止日期

    # Timestamps
    created_at: Mapped[str] = mapped_column(String(40), default="")
    updated_at: Mapped[str] = mapped_column(String(40), default="")


# ============================================
# V0.3 NEW MODELS
# ============================================

class BenchmarkQuestion(Base):
    """Standard benchmark question for RAG evaluation."""

    __tablename__ = "benchmark_questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    question: Mapped[str] = mapped_column(Text)
    gold_answer: Mapped[str] = mapped_column(Text, default="")
    answerable: Mapped[bool] = mapped_column(default=True)
    gold_document_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    gold_keywords_json: Mapped[str] = mapped_column(Text, default="[]")
    difficulty: Mapped[str] = mapped_column(String(16), default="medium", index=True)
    category: Mapped[str] = mapped_column(String(40), default="general", index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(40), default="")


class BenchmarkRun(Base):
    """One benchmark execution."""

    __tablename__ = "benchmark_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    per_question_json: Mapped[str] = mapped_column(Text, default="[]")
    total_questions: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="completed", index=True)
    started_at: Mapped[str] = mapped_column(String(40), default="")
    finished_at: Mapped[str] = mapped_column(String(40), default="")
    note: Mapped[str] = mapped_column(Text, default="")


class BackupRecord(Base):
    """Backup history."""

    __tablename__ = "backup_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    backup_type: Mapped[str] = mapped_column(String(24), index=True)  # postgres / files
    tier: Mapped[str] = mapped_column(String(16), default="local")  # local / s3 / remote
    path: Mapped[str] = mapped_column(String(600))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="created", index=True)
    tested_at: Mapped[str] = mapped_column(String(40), default="")
    test_status: Mapped[str] = mapped_column(String(16), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(40), default="")


class QueryFeedback(Base):
    """User feedback for individual queries."""

    __tablename__ = "query_feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    query_log_id: Mapped[int] = mapped_column(ForeignKey("query_logs.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    helpful: Mapped[bool] = mapped_column(default=True, index=True)
    reason: Mapped[str] = mapped_column(String(64), default="")
    correction: Mapped[str] = mapped_column(Text, default="")
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(40), default="")


class KnowledgeConflict(Base):
    """Detected knowledge conflict between documents."""

    __tablename__ = "knowledge_conflicts"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    field_name: Mapped[str] = mapped_column(String(60), index=True)
    conflict_type: Mapped[str] = mapped_column(String(32), default="value_mismatch", index=True)
    document_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    detected_at: Mapped[str] = mapped_column(String(40), default="")
    resolved_at: Mapped[str] = mapped_column(String(40), default="")
