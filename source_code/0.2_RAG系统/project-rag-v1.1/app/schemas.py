"""Pydantic schemas for API request/response validation."""

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Canonical entity validation is shared by every RAG entry point.
from .domain.entities import (
    CANONICAL_ENTITY_RANGE_TEXT,
    is_canonical_entity_code,
    normalize_entity_code,
)

# These role labels are not legal entities and must never be accepted as a
# document counterparty.  Keep the schema-level set in one place so request
# validation cannot fail at runtime with an undefined name.
_VIRTUAL_ENTITY_CODES = frozenset({"A", "B", "C", "D", "甲", "乙", "丙", "丁"})


class ProjectCreate(BaseModel):
    """Create a business project in the shared Tax/RAG PostgreSQL master."""

    project_code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=160)
    contract_amount: Decimal = Field(gt=0, description="Contract amount in CNY; must be an explicit business fact")
    location: str = Field(min_length=1, max_length=200)
    entity_code: Optional[str] = Field(
        default=None, description="Optional lead entity; multi-entity participation lives in transaction facts"
    )
    external_system: str = ""
    external_project_id: str = ""
    status: str = "ACTIVE"
    start_date: str = ""
    expected_end_date: str = ""
    project_type: str = ""
    note: str = ""

    @field_validator("entity_code")
    @classmethod
    def validate_project_entity_code(cls, value: str | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        code = normalize_entity_code(value)
        if not is_canonical_entity_code(code):
            raise ValueError(f"entity_code must be one canonical code: {CANONICAL_ENTITY_RANGE_TEXT}")
        return code


class ProjectSync(ProjectCreate):
    """Idempotently sync a project from an external business system."""

    pass


class ProjectResponse(BaseModel):
    """Schema for project response."""

    id: int
    project_code: str
    name: str
    status: str
    entity_code: Optional[str] = None
    external_system: str = ""
    external_project_id: str = ""


class DocumentMetadataPatch(BaseModel):
    """Schema for updating document metadata."""

    document_type: Optional[str] = None
    entity_code: Optional[str] = None
    counterparty_code: Optional[str] = None
    business_category: Optional[str] = None
    tax_category: Optional[str] = None  # 税务分类

    # 增值税
    tax_vat_rate: Optional[float] = None  # 税率（%）
    tax_vat_input: Optional[float] = None  # 进项税额
    tax_vat_output: Optional[float] = None  # 销项税额
    tax_vat_paid: Optional[float] = None  # 已交增值税

    # 企业所得税
    tax_income_rate: Optional[float] = None  # 税率（%）
    tax_income_amount: Optional[float] = None  # 应纳所得税额
    tax_income_paid: Optional[float] = None  # 已预缴所得税

    # 个人所得税
    tax_individual_rate: Optional[float] = None  # 税率（%）
    tax_individual_amount: Optional[float] = None  # 代扣代缴个税
    tax_individual_paid: Optional[float] = None  # 已缴个税

    # 附加税
    tax_surtax_urban: Optional[float] = None  # 城建税
    tax_surtax_edu: Optional[float] = None  # 教育费附加
    tax_surtax_local_edu: Optional[float] = None  # 地方教育附加

    # 其他税种
    tax_stamp_duty: Optional[float] = None  # 印花税
    tax_land: Optional[float] = None  # 土地增值税
    tax_environmental: Optional[float] = None  # 环境保护税

    # 发票信息
    invoice_no: Optional[str] = None  # 发票号码
    invoice_code: Optional[str] = None  # 发票代码
    invoice_type: Optional[str] = None  # 发票类型
    invoice_date: Optional[str] = None  # 开票日期
    invoice_deductible: Optional[bool] = None  # 是否已勾选抵扣

    # 合计
    tax_total: Optional[float] = None  # 税金合计

    contract_no: Optional[str] = None
    period: Optional[str] = None
    document_date: Optional[str] = None
    confidentiality: Optional[str] = None
    version_label: Optional[str] = None
    version_status: Optional[str] = None

    @field_validator("entity_code")
    @classmethod
    def validate_document_entity_code(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return value
        code = normalize_entity_code(value)
        if not is_canonical_entity_code(code):
            raise ValueError("document entity_code must be a canonical real-company code, not a role label")
        return code

    @field_validator("counterparty_code")
    @classmethod
    def reject_virtual_counterparty(cls, value: str | None) -> str | None:
        if value is not None and value.strip() in _VIRTUAL_ENTITY_CODES:
            raise ValueError("甲乙丙丁 are placeholders, not counterparties")
        return value


class RetrieveRequest(BaseModel):
    """Schema for retrieval request."""

    project_id: Optional[int] = None
    project_code: Optional[str] = None
    query: str
    filters: dict = Field(default_factory=dict)
    top_k: int = Field(default=10, ge=1, le=50)
    rerank: bool = True


class QueryRequest(RetrieveRequest):
    """Schema for query request with optional LLM answer."""

    answer: bool = True


# ============================================
# V0.3 Retrieval Schemas
# ============================================


class QueryRequestV3(QueryRequest):
    """V3 query request with rewrite / hyde / deep mode."""

    rewrite: Optional[bool] = None  # None = use config default
    hyde: Optional[bool] = None
    deep: bool = False
    stream: bool = False
    history: list[dict] = Field(default_factory=list)

    @field_validator("query")
    @classmethod
    def validate_adaptive_query(cls, value: str) -> str:
        """Reject blank queries before they reach the retrieval service."""
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("query must not be blank")
        return normalized


class RewriteResult(BaseModel):
    """Output of query rewrite step."""

    model_config = ConfigDict(extra="allow")

    intent: str = ""
    rewritten_query: str = ""
    filters: dict = Field(default_factory=dict)
    keywords: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class HyDEResult(BaseModel):
    """Output of HyDE step."""

    model_config = ConfigDict(extra="allow")

    hypothetical_text: str = ""
    helper_used: bool = False


class QualityGate(BaseModel):
    """Retrieval quality gate result."""

    model_config = ConfigDict(extra="allow")

    status: str = "UNKNOWN"  # GOOD | PARTIAL | LOW_CONFIDENCE | INSUFFICIENT_EVIDENCE
    score: float = 0.0
    top1_score: float = 0.0
    avg_score: float = 0.0
    result_count: int = 0
    doc_source_count: int = 0
    metadata_match_count: int = 0
    reasons: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    """Single evidence unit returned to LLM."""

    chunk_id: int
    document_id: int
    document_code: str = ""
    filename: str = ""
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    heading_path: str = ""
    title_chain: str = ""
    semantic_type: str = "plain"
    text: str
    rerank_score: float = 0.0
    vector_score: float = 0.0
    bm25_score: float = 0.0
    metadata: dict = Field(default_factory=dict)


class EvidencePack(BaseModel):
    """Complete evidence package for an answer."""

    query: str
    project_id: int
    rewrite_used: bool = False
    hyde_used: bool = False
    rewrite_result: Optional[RewriteResult] = None
    hyde_result: Optional[HyDEResult] = None
    quality_gate: Optional[QualityGate] = None
    evidence: list[Evidence] = Field(default_factory=list)
    answer: str = ""
    citations_used: list[int] = Field(default_factory=list)
    faithful: bool = False
    no_answer_confidence: float = 0.0


class DeepRetrievalRequest(QueryRequestV3):
    """Deep retrieval request — executes full V0.3 pipeline."""

    answer: bool = True
    generate_answer: bool = True


class RetrievalExplainItem(BaseModel):
    """Per-result explanation."""

    chunk_id: int
    document_id: int
    filename: str = ""
    heading_path: str = ""
    title_chain: str = ""
    rerank_score: float = 0.0
    vector_score: float = 0.0
    bm25_score: float = 0.0
    matched_keywords: list[str] = Field(default_factory=list)
    metadata_matched: dict = Field(default_factory=dict)


class RetrievalExplainResponse(BaseModel):
    """Explain response."""

    model_config = ConfigDict(extra="allow")

    query: str
    project_id: int
    items: list[RetrievalExplainItem] = Field(default_factory=list)
    quality_gate: Optional[QualityGate] = None
    status: str = "UNKNOWN"
    retrieval_status: str = "UNKNOWN"
    pipeline_errors: list[dict] = Field(default_factory=list)
    latency_ms: int = 0


class AdaptiveRetrievalResponse(BaseModel):
    """Stable HTTP response for the V0.3 adaptive retrieval pipeline.

    ``retrieval_status`` and ``pipeline_errors`` are deliberately returned in
    the response rather than hidden behind an empty result list.  ``extra`` is
    allowed so diagnostics added by the service remain observable without
    breaking older clients.
    """

    model_config = ConfigDict(extra="allow")

    project_id: int
    query: str
    results: list[dict] = Field(default_factory=list)
    rewrite_used: bool = False
    hyde_used: bool = False
    deep_mode: bool = False
    rewrite_result: Optional[dict] = None
    hyde_result: Optional[dict] = None
    quality_gate: Optional[dict] = None
    pipeline_errors: list[dict] = Field(default_factory=list)
    reranker_status: str = "UNKNOWN"
    retrieval_status: str = "UNKNOWN"
    status: str = "UNKNOWN"
    search_diagnostics: dict = Field(default_factory=dict)
    effective_filters: dict = Field(default_factory=dict)
    latency_ms: int = 0
    bm25_candidates: list[int] = Field(default_factory=list)
    vector_candidates: list[int] = Field(default_factory=list)
    answer: Optional[str] = None
    citations: list[dict] = Field(default_factory=list)


# ============================================
# V0.3 Document / Chunk schemas
# ============================================


class RechunkRequest(BaseModel):
    """Trigger re-chunk of a single document."""

    chunk_version: str = "v0.3_structured"
    semantic_type: Optional[str] = None  # override classification


class RechunkResponse(BaseModel):
    """Rechunk operation result."""

    document_id: int
    chunk_version: str
    semantic_type: str = "plain"
    old_chunk_count: int = 0
    new_chunk_count: int = 0
    status: str = "ok"
    message: str = ""


# ============================================
# V0.3 Feedback / Benchmark / Backup schemas
# ============================================


class FeedbackRequest(BaseModel):
    """User feedback for a query."""

    query_log_id: int
    helpful: bool = True
    reason: str = ""
    correction: str = ""
    comment: str = ""


class FeedbackResponse(BaseModel):
    id: int
    query_log_id: int
    helpful: bool
    reason: str = ""
    created_at: str = ""


class BenchmarkRunRequest(BaseModel):
    """Run a benchmark against current config."""

    project_id: Optional[int] = None
    question_ids: Optional[list[int]] = None  # None = all
    category: Optional[str] = None
    difficulty: Optional[str] = None
    config: dict = Field(default_factory=dict)
    note: str = ""


class BenchmarkMetrics(BaseModel):
    """Computed metrics for a benchmark run."""

    recall_at_5: float = 0.0
    recall_at_10: float = 0.0
    mrr: float = 0.0
    hit_rate: float = 0.0
    citation_recall: float = 0.0
    citation_precision: float = 0.0
    faithfulness: float = 0.0
    no_answer_accuracy: float = 0.0
    avg_latency_ms: float = 0.0
    total_questions: int = 0


class BenchmarkRunResponse(BaseModel):
    id: int
    status: str
    config: dict
    metrics: BenchmarkMetrics
    started_at: str = ""
    finished_at: str = ""
    note: str = ""


class BackupRequest(BaseModel):
    """Backup creation request."""

    backup_types: list[str] = Field(default_factory=lambda: ["postgres", "files"])
    note: str = ""


class BackupResponse(BaseModel):
    id: int
    backup_type: str
    tier: str
    path: str
    size_bytes: int
    status: str
    created_at: str = ""


class KnowledgeAuditResult(BaseModel):
    """Knowledge audit 2.0 result."""

    project_id: int
    counts: dict
    coverage: dict
    completeness: dict
    recency: dict
    conflicts: list[dict]
    parse_failures: list[dict]
    metadata_gaps: list[dict]
    issues: list[dict]
    recommendations: list[str]


class FolderImportRequest(BaseModel):
    """Schema for folder import request."""

    project_id: Optional[int] = None
    project_code: Optional[str] = None
    path: str
    recursive: bool = True
    auto_parse: bool = True


# ============================================
# NEW: Pagination schemas
# ============================================


class PaginationParams(BaseModel):
    """Common pagination parameters."""

    page: int = Field(default=1, ge=1, description="Page number")
    page_size: int = Field(default=50, ge=1, le=200, description="Items per page")


class PaginatedResponse(BaseModel):
    """Base paginated response wrapper."""

    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_next: bool
    has_prev: bool


class DocumentListResponse(PaginatedResponse):
    """Paginated document list response."""

    items: list[dict]


class JobListResponse(PaginatedResponse):
    """Paginated job list response."""

    items: list[dict]


# ============================================
# NEW: Audit and health schemas
# ============================================


class HealthResponse(BaseModel):
    """Extended health check response."""

    status: str
    service: str
    version: str
    mineru_available: bool
    database: dict
    embedding: dict
    reranker: dict
    worker_auto_start: bool
    validation_errors: list[str] = []


class AuditResponse(BaseModel):
    """Project audit response with coverage analysis."""

    project_id: int
    project_code: str
    counts: dict
    coverage: dict
    issues: list[dict]
    recommendations: list[str] = []


# ============================================
# NEW: Error response schemas
# ============================================


class ErrorResponse(BaseModel):
    """Standard error response."""

    error: str
    detail: Optional[str] = None
    code: Optional[str] = None


class ValidationErrorResponse(BaseModel):
    """Validation error response."""

    error: str
    field: str
    message: str


# ============================================
# Entity (往来单位) schemas
# ============================================


class EntityCreate(BaseModel):
    """Schema for creating an entity."""

    entity_code: str = Field(description="Canonical internal master code; external counterparties use external_parties")
    name: str
    short_name: str = ""
    entity_type: str = ""
    industry: str = ""
    business_role: str = ""
    entity_kind: str = "company"
    legal_entity: bool = True
    parent_entity_code: Optional[str] = None
    status: str = "active"
    legal_representative: str = ""
    legal_rep_id: str = ""
    legal_rep_phone: str = ""
    shareholders: str = ""
    supervisor: str = ""
    finance_officer: str = ""
    registered_capital: str = ""
    establishment_date: str = ""
    acquisition_date: str = ""
    registration_authority: str = ""
    registration_number: str = ""
    unified_social_credit_code: str = ""
    tax_id: Optional[str] = None
    business_scope: str = ""
    contributed_legal: float = 0.0
    contributed_shareholder: float = 0.0
    note: str = ""
    source: str = "manual"
    data_as_of: str = ""

    @field_validator("entity_code", "parent_entity_code")
    @classmethod
    def validate_canonical_code(cls, value: str | None, info) -> str | None:
        if value is None or not str(value).strip():
            if info.field_name == "entity_code":
                raise ValueError("entity_code is required for an internal entity")
            return None
        code = normalize_entity_code(value)
        if not is_canonical_entity_code(code):
            raise ValueError(f"entity references must use a canonical code: {CANONICAL_ENTITY_RANGE_TEXT}")
        return code

    @model_validator(mode="after")
    def validate_master_semantics(self):
        code = self.entity_code
        parent = self.parent_entity_code
        expected_role = code[0]
        if self.business_role and self.business_role != expected_role:
            raise ValueError(f"business_role must match entity_code prefix {expected_role}")
        self.business_role = expected_role
        if code == "A04":
            if self.legal_entity or parent != "A03":
                raise ValueError("A04 is the non-legal branch and must have parent_entity_code=A03")
            if self.entity_kind == "company":
                self.entity_kind = "branch"
        elif parent is not None and code is None:
            raise ValueError("parent_entity_code requires an entity_code")

        # The old column is retained for compatibility; the canonical tax id
        # must be explicit and must not silently disagree with it.
        legacy_tax_id = (self.unified_social_credit_code or "").strip() or None
        if self.tax_id and legacy_tax_id and self.tax_id.strip() != legacy_tax_id:
            raise ValueError("tax_id and unified_social_credit_code conflict")
        if self.tax_id is None and legacy_tax_id:
            self.tax_id = legacy_tax_id
        return self


class EntityPatch(BaseModel):
    """Schema for updating an entity."""

    entity_code: Optional[str] = None
    short_name: Optional[str] = None
    entity_type: Optional[str] = None
    industry: Optional[str] = None
    business_role: Optional[str] = None
    entity_kind: Optional[str] = None
    legal_entity: Optional[bool] = None
    parent_entity_code: Optional[str] = None
    status: Optional[str] = None
    legal_representative: Optional[str] = None
    legal_rep_id: Optional[str] = None
    legal_rep_phone: Optional[str] = None
    shareholders: Optional[str] = None
    supervisor: Optional[str] = None
    finance_officer: Optional[str] = None
    registered_capital: Optional[str] = None
    establishment_date: Optional[str] = None
    acquisition_date: Optional[str] = None
    registration_authority: Optional[str] = None
    registration_number: Optional[str] = None
    unified_social_credit_code: Optional[str] = None
    tax_id: Optional[str] = None
    business_scope: Optional[str] = None
    contributed_legal: Optional[float] = None
    contributed_shareholder: Optional[float] = None
    note: Optional[str] = None
    source: Optional[str] = None
    data_as_of: Optional[str] = None

    @field_validator("entity_code", "parent_entity_code")
    @classmethod
    def validate_patch_codes(cls, value: str | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        code = normalize_entity_code(value)
        if not is_canonical_entity_code(code):
            raise ValueError(f"entity references must use a canonical code: {CANONICAL_ENTITY_RANGE_TEXT}")
        return code

    @model_validator(mode="after")
    def validate_patch_semantics(self):
        if self.entity_code == "A04":
            if self.legal_entity is not False or self.parent_entity_code != "A03":
                raise ValueError("A04 is the non-legal branch and must have parent_entity_code=A03")
        if self.tax_id and self.unified_social_credit_code and self.tax_id != self.unified_social_credit_code:
            raise ValueError("tax_id and unified_social_credit_code conflict")
        return self


class ExternalPartyCreate(BaseModel):
    code: str = Field(min_length=1, max_length=16)
    name: str = Field(min_length=1, max_length=120)
    short_name: str = Field(default="", max_length=60)
    kind: str = Field(default="", max_length=30)
    tax_id: Optional[str] = Field(default=None, max_length=40)
    active: bool = True

    @field_validator("code")
    @classmethod
    def reject_internal_codes(cls, value: str) -> str:
        code = value.strip().upper()
        if is_canonical_entity_code(code):
            raise ValueError("canonical internal codes belong in entities, not external_parties")
        return code


class ExternalPartyPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    short_name: Optional[str] = Field(default=None, max_length=60)
    kind: Optional[str] = Field(default=None, max_length=30)
    tax_id: Optional[str] = Field(default=None, max_length=40)
    active: Optional[bool] = None


class EntityResponse(BaseModel):
    """Schema for entity response."""

    id: int
    entity_code: Optional[str] = None
    name: str
    short_name: str
    entity_type: str
    industry: str
    business_role: str = ""
    entity_kind: str = "company"
    legal_entity: bool = True
    parent_entity_code: Optional[str] = None
    status: str = "active"
    legal_representative: str
    legal_rep_id: str
    legal_rep_phone: str
    shareholders: str
    supervisor: str
    finance_officer: str
    registered_capital: str
    establishment_date: str
    acquisition_date: str
    registration_authority: str
    registration_number: str
    unified_social_credit_code: str
    tax_id: Optional[str] = None
    business_scope: str
    contributed_legal: float
    contributed_shareholder: float
    note: str
    source: str
    data_as_of: str
    created_at: str
    updated_at: str


class EntityListResponse(PaginatedResponse):
    """Paginated entity list response."""

    items: list[dict]


# === V1.1: Regulation schemas (ported from v0.2 base) =======================


class RegulationCreate(BaseModel):
    title: str
    document_no: str
    issuer: str = ""
    legal_level: str = "部门规章"
    jurisdiction: str = "全国"
    tax_type: str = ""
    industry: str = "建筑业"
    publish_date: str = ""
    effective_date: str = ""
    expiry_date: str = ""
    status: str = "effective"
    supersedes_id: int | None = None
    full_text: str = ""
    source: str = ""
    metadata_json: dict = Field(default_factory=dict)


class RegulationUpdate(BaseModel):
    title: str | None = None
    document_no: str | None = None
    issuer: str | None = None
    legal_level: str | None = None
    jurisdiction: str | None = None
    tax_type: str | None = None
    industry: str | None = None
    publish_date: str | None = None
    effective_date: str | None = None
    expiry_date: str | None = None
    status: str | None = None
    superseded_by_id: int | None = None
    full_text: str | None = None
    source: str | None = None


class RegulationArticleCreate(BaseModel):
    regulation_id: int
    chapter: str = ""
    article_no: str
    paragraph_no: str = ""
    heading: str = ""
    text: str
    full_chapter_text: str = ""
    sort_order: int = 0


class RegulationRetrieveRequest(BaseModel):
    query: str
    jurisdiction: str | None = None
    tax_type: str | None = None
    industry: str | None = None
    status: str = "effective"
    legal_level: str | None = None
    effective_date: str | None = None
    top_k: int = Field(default=10, ge=1, le=50)
    include_expired: bool = False
    rerank: bool = True


class RegulationQueryRequest(RegulationRetrieveRequest):
    answer: bool = True
