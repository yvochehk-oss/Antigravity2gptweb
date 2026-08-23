"""V0.2: Prompt 模板版本库路由。"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from ..ai import SCOPES
from ..ai.timeutil import now_iso
from ..audit import audit_from_request
from ..db import SessionLocal
from ..dependencies import admin_only
from ..models import AIPromptTemplate
from ..templates import templates

router = APIRouter()


@router.get("/ai-prompts", response_class=HTMLResponse)
def ai_prompts(request: Request):
    admin_only(request)
    db = SessionLocal()
    rows = db.execute(
        select(AIPromptTemplate)
        .order_by(AIPromptTemplate.scope, AIPromptTemplate.version.desc())
    ).scalars().all()
    db.close()
    return templates.TemplateResponse(
        request,
        "ai_prompts.html",
    {
        "request": request, "rows": rows, "scopes": SCOPES
    },
    )


@router.post("/ai-prompts")
def ai_prompt_add(
    request: Request,
    name: str = Form(...),
    scope: str = Form(...),
    system_addendum: str = Form(""),
    review_focus: str = Form(""),
):
    admin_only(request)
    db = SessionLocal()
    existing = db.execute(
        select(AIPromptTemplate).where(AIPromptTemplate.scope == scope)
    ).scalars().all()
    ver = max((x.version for x in existing), default=0) + 1
    x = AIPromptTemplate(
        name=name, scope=scope, version=ver,
        system_addendum=system_addendum, review_focus=review_focus,
        enabled=True, created_at=now_iso(),
    )
    db.add(x); db.flush()
    audit_from_request(
        db, request, "CREATE", "AIPromptTemplate", x.id,
        f"{scope}/v{ver}/{name}",
    )
    db.commit(); db.close()
    return RedirectResponse("/ai-prompts", status_code=303)


@router.post("/ai-prompts/{template_id}/toggle")
def ai_prompt_toggle(request: Request, template_id: int):
    admin_only(request)
    db = SessionLocal()
    x = db.get(AIPromptTemplate, template_id)
    if x:
        x.enabled = not x.enabled
        audit_from_request(
            db, request, "UPDATE", "AIPromptTemplate", x.id,
            f"enabled={x.enabled}",
        )
        db.commit()
    db.close()
    return RedirectResponse("/ai-prompts", status_code=303)
