"""V0.2: 外部模型 API 端点路由。"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from ..audit import audit_from_request
from ..db import SessionLocal
from ..models import AIModelEndpoint
from ..templates import templates

router = APIRouter()


@router.get("/ai-models", response_class=HTMLResponse)
def ai_models(request: Request):
    db = SessionLocal()
    rows = db.execute(
        select(AIModelEndpoint).order_by(AIModelEndpoint.id)
    ).scalars().all()
    db.close()
    return templates.TemplateResponse(
        request,
        "ai_models.html",
        {"request": request, "rows": rows},
    )


@router.post("/ai-models")
def ai_model_add(
    request: Request,
    name: str = Form(...),
    adapter: str = Form("openai_compatible"),
    base_url: str = Form(""),
    chat_path: str = Form("/v1/chat/completions"),
    model: str = Form(""),
    api_key_env: str = Form(""),
    timeout_seconds: int = Form(90),
    note: str = Form(""),
):
    db = SessionLocal()
    x = AIModelEndpoint(
        name=name, adapter=adapter, base_url=base_url,
        chat_path=chat_path, model=model, api_key_env=api_key_env,
        enabled=True, timeout_seconds=timeout_seconds, note=note,
    )
    db.add(x); db.flush()
    audit_from_request(
        db, request, "CREATE", "AIModelEndpoint", x.id,
        f"{name}/{adapter}/{model}",
    )
    db.commit(); db.close()
    return RedirectResponse("/ai-models", status_code=303)


@router.post("/ai-models/{endpoint_id}/toggle")
def ai_model_toggle(request: Request, endpoint_id: int):
    db = SessionLocal()
    x = db.get(AIModelEndpoint, endpoint_id)
    if x:
        x.enabled = not x.enabled
        audit_from_request(
            db, request, "UPDATE", "AIModelEndpoint", x.id,
            f"enabled={x.enabled}",
        )
        db.commit()
    db.close()
    return RedirectResponse("/ai-models", status_code=303)