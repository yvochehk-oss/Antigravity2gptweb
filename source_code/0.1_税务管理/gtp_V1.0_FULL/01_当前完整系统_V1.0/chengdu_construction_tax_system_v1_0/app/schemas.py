"""V0.2: Pydantic 输入输出契约。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
RiskLevel = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL", "UNKNOWN"]
Priority = Literal["P0", "P1", "P2", "P3"]
TaskStatus = Literal["open", "in_progress", "done", "rechecked", "verified", "closed"]


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