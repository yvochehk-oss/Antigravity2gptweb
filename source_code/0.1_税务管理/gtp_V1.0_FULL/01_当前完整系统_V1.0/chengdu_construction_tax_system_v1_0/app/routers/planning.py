"""Project financial/tax allocation planning APIs and manager sandbox."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from ..ai.adapter import endpoint_is_allowed
from ..db import SessionLocal
from ..dependencies import admin_only
from ..models import AIModelEndpoint, Project
from ..planning.service import (
    planning_candidate_context,
    recommend_project_allocation,
    system_penetration_snapshot,
)
from ..templates import templates

router=APIRouter(tags=['项目财税筹划沙盘'])

PLANNING_MARGIN_SCOPE = "known_cost_margin_not_project_eac"
PLANNING_MARGIN_DEFINITION = (
    "合同收入上限 - 当前已知系统外真实成本 - 本方案新增系统外成本 - "
    "当前项目实缴税款 - 本方案增量税务现金；不含未来未规划成本/税款，"
    "不是项目最终利润，也不是 EAC 利润。"
)


def _truth_safe_planning_response(payload: Any) -> Any:
    """Normalize legacy planning-profit naming at the public API boundary.

    The deterministic planning engine and persisted PlanningScenario table keep
    the historical ``projected_profit`` storage column for compatibility. That
    value is a local what-if margin under known costs, not a whole-project EAC
    forecast. Public JSON therefore never exposes the misleading legacy key
    ``projected_management_profit``.
    """

    def normalize(value: Any) -> Any:
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if not isinstance(value, dict):
            return value

        result: dict[str, Any] = {}
        for key, item in value.items():
            public_key = (
                "scenario_known_cost_margin"
                if key == "projected_management_profit"
                else key
            )
            result[public_key] = normalize(item)

        if "scenario_known_cost_margin" in result:
            result.setdefault("profit_scope", PLANNING_MARGIN_SCOPE)
            result.setdefault("profit_definition", PLANNING_MARGIN_DEFINITION)
        return result

    normalized = normalize(payload)
    if not isinstance(normalized, dict):
        return normalized

    basis = normalized.get("planning_basis")
    if isinstance(basis, dict):
        basis["profit_scope"] = PLANNING_MARGIN_SCOPE
        basis["profit_definition"] = PLANNING_MARGIN_DEFINITION
        basis["note"] = (
            "package_amount应为尚未计入real_costs的待规划净额；"
            "方案余量仅用于当前已知成本条件下的情景比较，不是项目最终/EAC利润；"
            "结果为规划估算，不替代法定申报税额。"
        )

    normalized["metric_semantics"] = {
        "scenario_known_cost_margin": {
            "scope": PLANNING_MARGIN_SCOPE,
            "definition": PLANNING_MARGIN_DEFINITION,
        }
    }

    ai = normalized.get("ai_recommendation")
    if isinstance(ai, dict):
        boundary = (
            "AI只能把 scenario_known_cost_margin 解释为已知成本口径的情景余量，"
            "不得表述为项目最终利润或EAC利润。"
        )
        ai["fact_boundary"] = boundary
        summary = ai.get("summary")
        if isinstance(summary, str) and summary.strip():
            ai["summary"] = f"【口径提示：{boundary}】\n{summary}"

    return normalized


class PartyOverride(BaseModel):
    capacity: float|None=None
    external_cost_ratio: float|None=None
    tax_cash_rate: float|None=None
    risk_score: float|None=None
    evidence_quality: float|None=None
    eligible: bool|None=None


class AllocationPlanningBody(BaseModel):
    package_name: str=''
    category: str
    package_amount: float=Field(gt=0)
    objective: str='balanced'
    internal_min_ratio: float=Field(default=0,ge=0,le=1)
    internal_max_ratio: float=Field(default=1,ge=0,le=1)
    preferred_internal_ratio: float|None=Field(default=None,ge=0,le=1)
    internal_candidates: list[str]=Field(default_factory=list)
    external_candidates: list[str]=Field(default_factory=list)
    party_overrides: dict[str,PartyOverride]=Field(default_factory=dict)
    endpoint_id: int|None=None
    persist: bool=False

    @field_validator('objective')
    @classmethod
    def valid_objective(cls,v):
        if v not in {'balanced','profit','tax','risk'}:
            raise ValueError('objective must be balanced/profit/tax/risk')
        return v


@router.get('/api/projects/{pid}/allocation-planning/context')
def allocation_context(request: Request, pid: int, category: str = '劳务', package_amount: float = 1):
    admin_only(request)
    if package_amount <= 0:
        raise HTTPException(status_code=422,detail='package_amount must be positive')
    db=SessionLocal()
    try:
        return planning_candidate_context(db,pid,category,Decimal(str(package_amount)))
    except ValueError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    finally:
        db.close()


@router.get('/api/projects/{pid}/system-penetration')
def penetration_api(request: Request, pid: int):
    admin_only(request)
    db=SessionLocal()
    try:
        return system_penetration_snapshot(db,pid)
    except ValueError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    finally:
        db.close()


@router.post('/api/projects/{pid}/allocation-planning/recommend')
def allocation_recommend(
    request: Request,
    pid: int,
    body: AllocationPlanningBody,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    user = admin_only(request)
    payload=body.model_dump(exclude_none=True)
    payload['party_overrides']={k:v.model_dump(exclude_none=True) for k,v in body.party_overrides.items()}
    if idempotency_key is not None:
        idempotency_key = idempotency_key.strip()
        if not idempotency_key or len(idempotency_key) > 128:
            raise HTTPException(status_code=422, detail='Idempotency-Key must be 1-128 non-whitespace characters')
    db=SessionLocal()
    try:
        result = recommend_project_allocation(
            db, pid, payload, actor=user.username, idempotency_key=idempotency_key,
        )
        return _truth_safe_planning_response(result)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422,detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.get('/manager/project/{pid}/planning',response_class=HTMLResponse)
def planning_page(request:Request,pid:int):
    user = admin_only(request)
    db=SessionLocal()
    try:
        project=db.get(Project,pid)
        if project is None:
            raise HTTPException(status_code=404,detail='project not found')
        endpoints = [
            endpoint for endpoint in db.execute(
                select(AIModelEndpoint)
                .where(AIModelEndpoint.enabled.is_(True))
                .order_by(AIModelEndpoint.id)
            ).scalars().all()
            if endpoint_is_allowed(endpoint)
        ]
        return templates.TemplateResponse(request,'manager_planning.html',{'request':request,'title':f'{project.name} - 项目财税筹划沙盘','user':user,'project':project,'endpoints':endpoints})
    finally:
        db.close()
