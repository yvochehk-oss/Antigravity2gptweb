"""V0.2: 经营驾驶舱 / 项目 / 主数据 / 导入 / 审计。"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from ..audit import audit_from_request
from ..calc import consolidated, project_summary
from ..db import SessionLocal
from ..auth import create_session, current_user_from_request
from ..models import (
    AuditLog,
    Entity,
    Fulfillment,
    Project,
    RealCost,
    RiskEvent,
    User,
)
from pathlib import Path
from ..templates import templates

STATIC_DIST_INDEX = Path(__file__).resolve().parents[1] / "static_dist" / "index.html"

router = APIRouter()

# V0.2 RAG 同步模式：发票/合同/付款数据强制从 RAG 抽取，禁止手工录入
RAG_ONLY_MSG = "请使用 RAG 同步获取数据，禁止手工录入"


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    """现代化智控大屏主页 (React SPA)。"""
    if not STATIC_DIST_INDEX.exists():
        raise HTTPException(503, "前端静态资源未找到，请先构建 React SPA Bundle")
    resp = HTMLResponse(STATIC_DIST_INDEX.read_text(encoding="utf-8"))
    if current_user_from_request(request) is None:
        db = SessionLocal()
        try:
            admin_user = db.query(User).filter(User.role == "admin", User.active == True).first()  # noqa: E712
            if admin_user:
                create_session(resp, admin_user.id)
        finally:
            db.close()
    return resp


@router.get("/demo", response_class=HTMLResponse)
def demo_home(request: Request) -> HTMLResponse:
    return home(request)


@router.get("/classic", response_class=HTMLResponse)
def classic_home(request: Request) -> HTMLResponse:
    """经典后端渲染模式 (Jinja2)。"""
    db = SessionLocal()
    d = consolidated(db)
    risks = db.execute(
        select(RiskEvent).where(RiskEvent.resolved == False)  # noqa: E712
    ).scalars().all()
    db.close()
    return templates.TemplateResponse(
        request,
        "home.html",
        {"request": request, "d": d, "risk_count": len(risks)},
    )



@router.get("/project/{pid}", response_class=HTMLResponse)
def project(request: Request, pid: int) -> HTMLResponse:
    db = SessionLocal()
    s = project_summary(db, pid)
    costs = db.execute(
        select(RealCost).where(RealCost.project_id == pid)
    ).scalars().all()
    db.close()
    return templates.TemplateResponse(
        request,
        "project.html",
        {"request": request, "s": s, "costs": costs, "tree": {}},
    )


@router.get("/manage", response_class=HTMLResponse)
def manage(request: Request) -> HTMLResponse:
    db = SessionLocal()
    ps = db.execute(select(Project)).scalars().all()
    es = db.execute(select(Entity)).scalars().all()
    db.close()
    return templates.TemplateResponse(
        request,
        "manage.html",
        {"request": request, "projects": ps, "entities": es},
    )


@router.post("/manage/cost")
def add_cost(
    request: Request,
    project_id: int = Form(...),
    entity_code: str = Form(...),
    counterparty_code: str = Form(""),
    category: str = Form(...),
    subcategory: str = Form(""),
    period: str = Form("2026-08"),
    amount: float = Form(...),
    external_cash: bool = Form(False),
    note: str = Form(""),
):
    db = SessionLocal()
    x = RealCost(
        project_id=project_id, entity_code=entity_code,
        counterparty_code=counterparty_code, category=category,
        subcategory=subcategory, period=period,
        amount=Decimal(str(amount)), external_cash=external_cash, note=note,
    )
    db.add(x); db.flush()
    audit_from_request(
        db, request, "CREATE", "RealCost", x.id,
        f"{entity_code}/{category}/{amount}",
    )
    db.commit(); db.close()
    return RedirectResponse(f"/project/{project_id}", status_code=303)


@router.post("/manage/contract")
def add_contract(
    request: Request,
    project_id: int = Form(...),
    contract_no: str = Form(""),
    buyer_code: str = Form(...),
    seller_code: str = Form(...),
    category: str = Form(...),
    amount: float = Form(...),
    note: str = Form(""),
):
    raise HTTPException(403, "禁止手工录入合同，请使用 RAG 同步功能")


@router.post("/manage/invoice")
def add_invoice(
    request: Request,
    project_id: int = Form(...),
    invoice_no: str = Form(""),
    period: str = Form(...),
    entity_code: str = Form(...),
    direction: str = Form(...),
    counterparty_code: str = Form(...),
    category: str = Form(...),
    net: float = Form(...),
    vat: float = Form(0),
    rate: float = Form(0),
    deductible: bool = Form(False),
    note: str = Form(""),
):
    raise HTTPException(403, "禁止手工录入发票，请使用 RAG 同步功能")


@router.post("/manage/cashflow")
def add_cashflow(
    request: Request,
    project_id: int = Form(...),
    entity_code: str = Form(...),
    counterparty_code: str = Form(...),
    direction: str = Form(...),
    amount: float = Form(...),
    period: str = Form(...),
    note: str = Form(""),
):
    raise HTTPException(403, "禁止手工录入付款流水，请使用 RAG 同步功能")


@router.post("/manage/fulfillment")
def add_fulfillment(
    request: Request,
    project_id: int = Form(...),
    counterparty_code: str = Form(...),
    kind: str = Form(...),
    category: str = Form(...),
    quantity: float = Form(0),
    amount: float = Form(0),
    evidence_complete: bool = Form(False),
    note: str = Form(""),
):
    db = SessionLocal()
    x = Fulfillment(
        project_id=project_id, counterparty_code=counterparty_code,
        kind=kind, category=category, quantity=Decimal(str(quantity)),
        amount=Decimal(str(amount)),
        evidence_complete=evidence_complete, note=note,
    )
    db.add(x); db.flush()
    audit_from_request(
        db, request, "CREATE", "Fulfillment", x.id,
        f"{counterparty_code}/{kind}/{amount}",
    )
    db.commit(); db.close()
    return RedirectResponse("/manage", status_code=303)


@router.get("/audit", response_class=HTMLResponse)
def audits(request: Request) -> HTMLResponse:
    db = SessionLocal()
    rows = db.execute(
        select(AuditLog).order_by(AuditLog.id.desc()).limit(200)
    ).scalars().all()
    db.close()
    return templates.TemplateResponse(
        request,
        "audit.html",
        {"request": request, "rows": rows},
    )


@router.get("/imports", response_class=HTMLResponse)
def imports(request: Request) -> HTMLResponse:
    db = SessionLocal()
    ps = db.execute(select(Project)).scalars().all()
    db.close()
    return templates.TemplateResponse(
        request,
        "imports.html",
        {"request": request, "projects": ps, "message": ""},
    )


@router.post("/imports/invoices", response_class=HTMLResponse)
async def import_invoices(
    request: Request,
    project_id: int = Form(...),
    file: UploadFile = File(...),
):
    db = SessionLocal()
    ps = db.execute(select(Project)).scalars().all()
    db.close()
    return templates.TemplateResponse(
        request,
        "imports.html",
        {
            "request": request,
            "projects": ps,
            "message": "禁止 CSV 导入发票，请使用 RAG 同步功能",
        },
    )


@router.post("/imports/cashflows", response_class=HTMLResponse)
async def import_cashflows(
    request: Request,
    project_id: int = Form(...),
    file: UploadFile = File(...),
):
    db = SessionLocal()
    ps = db.execute(select(Project)).scalars().all()
    db.close()
    return templates.TemplateResponse(
        request,
        "imports.html",
        {
            "request": request,
            "projects": ps,
            "message": "禁止 CSV 导入付款流水，请使用 RAG 同步功能",
        },
    )