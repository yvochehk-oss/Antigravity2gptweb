"""V0.2: Pydantic 输入输出契约。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Severity = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
RiskLevel = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL", "UNKNOWN"]
Priority = Literal["P0", "P1", "P2", "P3"]
TaskStatus = Literal["open", "in_progress", "done", "rechecked", "verified", "closed"]
CollectionStatus = Literal["LOADING", "READY", "DEGRADED", "UNAVAILABLE", "DEPRECATED"]


class Finding(BaseModel):
    severity: Severity
    area: str
    issue: str
    evidence: str = ""
    impact: str = ""


class Recommendation(BaseModel):
    priority: Priority
    action: str
    reason: str = ""
    owner: str = ""


class AIReviewPayload(BaseModel):
    risk_level: RiskLevel = "UNKNOWN"
    score: float = Field(ge=0, le=100, default=0)
    summary: str = ""
    findings: list[Finding] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)


class HealthCheckRequest(BaseModel):
    project_id: int
    profile: Literal["quick", "standard", "deep"] = "standard"
    endpoint_ids: list[int]
    user_instruction: str = ""


class TaskUpdateRequest(BaseModel):
    status: TaskStatus


class CollectionEnvelope(BaseModel):
    """Stable envelope shared by the React collection endpoints.

    ``items`` is the canonical field.  ``data`` is deliberately retained as a
    same-value compatibility alias for API consumers that use the common
    ``data`` convention; neither field is populated with demo values.
    """

    status: CollectionStatus
    message: str = ""
    items: list[dict[str, Any]] = Field(default_factory=list)
    data: list[dict[str, Any]] = Field(default_factory=list)
    projects: list[dict[str, Any]] = Field(default_factory=list)
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    has_more: bool = False
    deprecated: bool | None = None
    deprecation_message: str = ""
    recommended_endpoints: dict[str, str] = Field(default_factory=dict)


class ProjectCollectionItem(BaseModel):
    id: int
    code: str
    project_code: str = ""
    name: str
    city: str = ""
    location: str = ""
    contract_total: float = 0
    contract_amount: float = 0
    entity_code: str = ""
    entity_name: str = ""
    # ``status`` is the project's data-trust status, not a workflow state.
    status: CollectionStatus = "DEGRADED"
    data_status: CollectionStatus = "DEGRADED"
    data_gaps: list[str] = Field(default_factory=list)
    trusted: bool = False


class RiskCollectionItem(BaseModel):
    id: str
    project_id: int
    project_name: str = ""
    project_code: str = ""
    entity_name: str = ""
    risk_type: str = ""
    severity: str = "未知"
    severity_code: str = "UNKNOWN"
    trigger_time: str = ""
    description: str = ""
    audit_suggestions: str = ""
    status: str = "待处置"
    handler: str = "未分配"
    resolved: bool = False
    project_entity_code: str = ""
    project_data_status: CollectionStatus = "DEGRADED"
    data_status: CollectionStatus = "DEGRADED"
    data_gaps: list[str] = Field(default_factory=list)
    trusted: bool = False


class TaxLedgerCollectionItem(BaseModel):
    id: str
    period: str
    entity_code: str
    entity_name: str = ""
    business_role: str = ""
    legal_entity: bool = True
    output_vat: float = 0
    input_vat: float = 0
    vat_payable: float = 0
    revenue: float = 0
    real_cost: float = 0
    estimated_profit: float = 0
    estimated_cit: float = 0
    cit_note: str = ""
    generated: bool = True
    # Frontend-oriented names.  They are derived from the deterministic
    # ledger row, never from a static/demo record.
    entityName: str = ""
    entityCategory: str = ""
    isInternal: bool = True
    source: str = "deterministic_tax_ledger"
    declareAmount: float = 0
    taxAmount: float = 0
    taxCategory: str = "增值税"
    filingPeriod: str = ""
    status: str = "已生成"
    data_status: CollectionStatus = "DEGRADED"
    data_gaps: list[str] = Field(default_factory=list)
    trusted: bool = False
    riskLevel: str = "未知"
    riskDescription: str = ""
    fourFlowsCheck: dict[str, bool] = Field(
        default_factory=lambda: {
            "contractMatch": False,
            "invoiceMatch": False,
            "paymentMatch": False,
            "logisticsMatch": False,
        },
    )
    updateTime: str = ""


class AuditCollectionItem(BaseModel):
    id: str
    timestamp: str = ""
    operator: str = ""
    role: str = "历史记录未存储"
    target_subject: str = ""
    action_type: str = ""
    details: str = ""
    integrity_hash: str = ""
    integrity_status: str = "NOT_RECORDED"
    object_type: str = ""
    object_id: str = ""
    ip: str = ""
    request_id: str = ""
    data_status: CollectionStatus = "DEGRADED"
    data_gaps: list[str] = Field(default_factory=list)
    trusted: bool = False
