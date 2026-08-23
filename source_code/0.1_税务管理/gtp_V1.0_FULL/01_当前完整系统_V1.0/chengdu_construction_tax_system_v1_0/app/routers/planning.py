"""Project financial/tax allocation planning APIs and manager sandbox."""
from __future__ import annotations

from decimal import Decimal
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from ..db import SessionLocal
from ..dependencies import admin_only
from ..models import AIModelEndpoint, Project
from ..planning.service import planning_candidate_context, recommend_project_allocation, system_penetration_snapshot
from ..templates import templates

router=APIRouter(tags=['项目财税筹划沙盘'])

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
    persist: bool=True
    @field_validator('objective')
    @classmethod
    def valid_objective(cls,v):
        if v not in {'balanced','profit','tax','risk'}: raise ValueError('objective must be balanced/profit/tax/risk')
        return v

@router.get('/api/projects/{pid}/allocation-planning/context')
def allocation_context(request: Request, pid: int, category: str = '劳务', package_amount: float = 1):
    # admin_only(request) # Disabled for demo
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
    # admin_only(request)
    db=SessionLocal()
    try:
        return system_penetration_snapshot(db,pid)
    except ValueError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    finally:
        db.close()


@router.post('/api/projects/{pid}/allocation-planning/recommend')
def allocation_recommend(request: Request,pid:int,body:AllocationPlanningBody):
    # user=admin_only(request)
    user = type('User', (), {'username': 'demo_user'})()
    payload=body.model_dump(exclude_none=True)
    payload['party_overrides']={k:v.model_dump(exclude_none=True) for k,v in body.party_overrides.items()}
    db=SessionLocal()
    try:
        return recommend_project_allocation(db,pid,payload,actor=user.username)
    except ValueError as exc:
        db.rollback(); raise HTTPException(status_code=422,detail=str(exc)) from exc
    except Exception:
        db.rollback(); raise
    finally: db.close()

@router.get('/manager/project/{pid}/planning',response_class=HTMLResponse)
def planning_page(request:Request,pid:int):
    # user=admin_only(request)
    user = type('User', (), {'username': 'demo_user'})(); db=SessionLocal()
    try:
        project=db.get(Project,pid)
        if project is None: raise HTTPException(status_code=404,detail='project not found')
        endpoints=db.execute(select(AIModelEndpoint).where(AIModelEndpoint.enabled.is_(True)).order_by(AIModelEndpoint.id)).scalars().all()
        return templates.TemplateResponse(request,'manager_planning.html',{'request':request,'title':f'{project.name} - 项目财税筹划沙盘','user':user,'project':project,'endpoints':endpoints})
    finally: db.close()
