"""HTTP routes for the canonical Facts provider."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from app.config import FACTS_CACHE_TTL_SECONDS

from .facts_provider import FactsResponse as DomainFactsResponse
from .facts_provider import get_facts_provider

router = APIRouter(prefix="/api/v1/facts", tags=["facts"])


class FactsRequest(BaseModel):
    """Facts request model kept for clients that use the POST contract."""

    project_code: str


class InvalidateRequest(BaseModel):
    """Cache invalidation request."""

    project_code: Optional[str] = None
    entity_code: Optional[str] = None


class MetricValueResponse(BaseModel):
    """Public representation of one deterministic metric."""

    value: float | int
    metric_version: str
    unit: str = ""


class FactsResponse(BaseModel):
    """Public canonical Facts response.

    ``DEGRADED`` is a valid response status. It means the source data is not
    sufficient for deterministic financial conclusions, not that demo values
    should be substituted.
    """

    model_config = ConfigDict(from_attributes=True)

    project_code: str
    as_of: str
    facts_version: str
    status: str = "AVAILABLE"
    facts_available: bool = True
    reason: Optional[str] = None
    source: str = "analytics_project_full"
    entity_code: Optional[str] = None
    entity_mapping_status: Optional[str] = None
    entity_mapping_reason: Optional[str] = None
    entity_mapping_valid: Optional[bool] = None
    metrics: dict[str, MetricValueResponse] = Field(default_factory=dict)

    @classmethod
    def from_domain(cls, facts: DomainFactsResponse) -> "FactsResponse":
        """Serialize a dataclass response without calling a missing method."""

        payload = facts.to_dict()
        return cls.model_validate(payload)


@router.get("/projects/{project_code}", response_model=FactsResponse)
async def get_project_facts(
    project_code: str,
    require_fresh: bool = Query(
        False,
        description="是否强制获取最新数据（用于 AI 深度体检）",
    ),
    max_age: int = Query(
        FACTS_CACHE_TTL_SECONDS,
        ge=0,
        le=3600,
        description="缓存最大有效期（秒），默认 60",
    ),
    as_of: Optional[str] = Query(
        None,
        description="查询历史 Snapshot 的时间点（ISO 8601 格式）",
    ),
):
    """Return current or historical canonical facts.

    The endpoint intentionally returns HTTP 200 with ``status=DEGRADED`` when
    the deterministic source is unavailable. This keeps the unavailable state
    machine-readable for AI Review and clients, while preventing a 500 or a
    fabricated financial answer.
    """

    from app.db import SessionLocal

    db = SessionLocal()
    try:
        provider = get_facts_provider(db)
        facts = provider.get_facts(
            project_code=project_code,
            require_fresh=require_fresh,
            max_age=max_age,
            as_of=as_of,
        )
        return FactsResponse.from_domain(facts)
    finally:
        db.close()


@router.post("/invalidate")
async def invalidate_facts(request: InvalidateRequest):
    """Invalidate project/entity Facts cache entries."""

    from app.db import SessionLocal

    db = SessionLocal()
    try:
        provider = get_facts_provider(db)
        invalidated = 0
        if request.project_code:
            invalidated += int(provider.invalidate(request.project_code))
        if request.entity_code:
            invalidated += provider.invalidate_entity(request.entity_code)

        return {
            "status": "ok",
            "invalidated": invalidated,
            "message": "Facts 缓存已失效",
        }
    finally:
        db.close()


@router.get("/projects/{project_code}/history")
async def get_facts_history(
    project_code: str,
    limit: int = Query(10, ge=1, le=100),
):
    """Return persisted Facts snapshot metadata.

    Missing snapshot storage is represented as an explicit degraded empty
    history; the route never returns a fabricated snapshot identifier.
    """

    from app.db import SessionLocal

    db = SessionLocal()
    try:
        provider = get_facts_provider(db)
        return provider.get_history(project_code, limit)
    finally:
        db.close()


__all__ = [
    "FactsRequest",
    "FactsResponse",
    "MetricValueResponse",
    "InvalidateRequest",
    "router",
]
