"""管理员（admin）专属路由：总览大屏 + 项目详情（含 AI 问答）。"""
from __future__ import annotations

import json
from decimal import Decimal

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import case, select

from ..ai.adapter import call_endpoint
from ..calc import consolidated, project_summary
from ..constants import CANONICAL_ENTITY_CODES
from ..db import SessionLocal
from ..dependencies import admin_only
from ..models import AIModelEndpoint, AuditLog, Entity, Invoice, Project, TaxLedger
from ..templates import templates

router = APIRouter(prefix="/manager", tags=["管理者"])


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _fmt(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):,.0f}"
    except Exception:
        return str(v)


def _build_project_context(db: Session, project_id: int) -> dict:
    """为 AI 问答构建项目上下文字典（供 adapter 调用）。

    说明：TaxLedger 按期间+主体（entity_code）组织，不含 project_id。
    这里只把"本项目涉及到的 entity_code"对应 ledger 行纳入，避免把别的项目主体的数据喂给 AI。
    """
    project = db.get(Project, project_id)
    if project is None:
        return {}

    s = project_summary(db, project_id)

    entity_codes = db.execute(
        select(Invoice.entity_code)
        .where(Invoice.project_id == project_id)
        .distinct()
    ).scalars().all()

    if entity_codes:
        rows = db.execute(
            select(TaxLedger)
            .where(TaxLedger.entity_code.in_(entity_codes))
            .order_by(TaxLedger.period)
        ).scalars().all()
    else:
        rows = []

    return {
        "project_code": project.code,
        "project_name": project.name,
        "contract_total": float(project.contract_total),
        "revenue": float(s.get("revenue", 0)),
        "real_cost": float(s.get("real_cost", 0)),
        "profit": float(s.get("profit", 0)),
        "margin": float(s.get("margin", 0) or 0),
        "vat": float(s.get("vat", 0)),
        "progress": float(s.get("progress", 0) or 0),
        "eac": float(s.get("eac", 0)),
        "eac_profit": float(s.get("eac_profit", 0)),
        "tax_ledgers": [
            {
                "period": r.period,
                "entity_code": r.entity_code,
                "output_vat": float(r.output_vat),
                "input_vat": float(r.input_vat),
                "vat_payable": float(r.vat_payable),
                "revenue": float(r.revenue),
                "estimated_cit": float(r.estimated_cit),
                "estimated_profit": float(r.estimated_profit),
            }
            for r in rows
        ],
    }


def _call_ai(question: str, ctx: dict, endpoint_id: int | None = None) -> str:
    """直接调用 AI，优先使用外部大模型（如 DeepSeek），本地 Mock 作为兜底。"""
    db = SessionLocal()
    try:
        if endpoint_id:
            endpoint = db.get(AIModelEndpoint, endpoint_id)
        else:
            # 优先选择外部真实模型（非 mock），按 id 升序，mock 模型作为最后兜底
            endpoint = (
                db.query(AIModelEndpoint)
                .filter(AIModelEndpoint.enabled == True)  # noqa: E712
                .order_by(case((AIModelEndpoint.adapter == "mock", 1), else_=0), AIModelEndpoint.id)
                .first()
            )

        if endpoint is None:
            return "当前没有可用的 AI 模型端点，请在「AI 模型」页面配置。"

        ctx_str = json.dumps(ctx, ensure_ascii=False, indent=2)
        system_msg = (
            "你是一个专业的建筑施工企业税务统筹AI助手。"
            "基于以下项目数据，回答管理者的问题。"
            "数据来源：税务系统的确定性计算结果，不是预测值。"
            "回答要简洁、数据驱动，直接引用系统数字，不说「根据数据」这种模糊词。"
            "如果数据不足以回答，说明哪些信息缺失。\n\n"
            f"项目数据：\n{ctx_str}"
        )
        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": question},
        ]

        try:
            result, raw, _ = call_endpoint(endpoint, messages, ctx)
        except Exception as e:
            # 若外部主模型调用失败（如网络波动/配额超限），自动尝试本地 mock 模型作为兜底
            if endpoint.adapter != "mock":
                mock_ep = (
                    db.query(AIModelEndpoint)
                    .filter(AIModelEndpoint.enabled == True, AIModelEndpoint.adapter == "mock")  # noqa: E712
                    .first()
                )
                if mock_ep:
                    result, raw, _ = call_endpoint(mock_ep, messages, ctx)
                else:
                    return f"AI 调用失败：{e}"
            else:
                return f"AI 调用失败：{e}"

        # 从结构化结果提取 summary
        summary = result.get("summary", "") if isinstance(result, dict) else ""
        return summary or raw[:2000]
    except Exception as e:
        return f"AI 调用失败：{e}"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 总览大屏
# ---------------------------------------------------------------------------
@router.get("/dashboard", response_class=HTMLResponse)
def manager_dashboard(request: Request) -> HTMLResponse:
    """管理者总览大屏：全部项目一览 + 关键经济指标。"""
    user = admin_only(request)
    db = SessionLocal()
    try:
        d = consolidated(db)
        # Entity 主数据是驾驶舱主体清单的唯一来源。台账只提供金额，不能
        # 反向决定页面显示哪些主体；这样没有交易/台账的真实单位也会显示，
        # 同时旧库中残留的 A/B/C/D 虚拟行不会被渲染出来。
        entities = db.execute(
            select(Entity)
            .where(
                Entity.active.is_(True),
                Entity.internal.is_(True),
                Entity.code.in_(CANONICAL_ENTITY_CODES),
            )
            .order_by(Entity.code)
        ).scalars().all()

        # 各实际单位税务汇总。A04 是非独立法人分支机构，税务计算已按
        # parent_entity_code 归集到 A03，因此分支卡片明确显示归集关系，
        # 不把 A03 的金额再复制到 A04 造成重复统计。
        ledgers = db.execute(
            select(TaxLedger).where(TaxLedger.generated == True)  # noqa: E712
        ).scalars().all()

        # 以主数据中的全部实际单位初始化金额，避免只展示有台账的单位。
        entity_vat = {entity.code: Decimal(0) for entity in entities}
        entity_cit = {entity.code: Decimal(0) for entity in entities}
        for row in ledgers:
            ec = row.entity_code
            if ec not in entity_vat:
                # 不让未知/历史虚拟主体进入 UI；其清理由主数据迁移负责。
                continue
            entity_vat[ec] += row.vat_payable
            entity_cit[ec] += row.estimated_cit

        entity_cards = [
            {
                "code": entity.code,
                "name": entity.name,
                "business_role": entity.business_role,
                "legal_entity": bool(entity.legal_entity),
                "parent_entity_code": entity.parent_entity_code,
                "vat": float(entity_vat[entity.code]),
                "cit": float(entity_cit[entity.code]),
            }
            for entity in entities
        ]

        # 项目风险汇总
        from ..models import RiskEvent
        risks = db.execute(
            select(RiskEvent).where(RiskEvent.resolved == False)  # noqa: E712
        ).scalars().all()
        risk_by_severity = {"RED": 0, "YELLOW": 0}
        for r in risks:
            if r.severity in risk_by_severity:
                risk_by_severity[r.severity] += 1

        return templates.TemplateResponse(
            request,
            "manager_dashboard.html",
            {
                "request": request,
                "title": "管理者总览",
                "user": user,
                "d": d,
                "risk_by_severity": risk_by_severity,
                "entity_vat": {k: float(v) for k, v in entity_vat.items()},
                "entity_cit": {k: float(v) for k, v in entity_cit.items()},
                "entity_cards": entity_cards,
                "entity_legal_count": sum(1 for entity in entities if entity.legal_entity),
                "entity_branch_count": sum(1 for entity in entities if not entity.legal_entity),
            },
        )
    finally:
        db.close()


@router.get("/project-list", response_class=HTMLResponse)
def manager_project_list(request: Request) -> HTMLResponse:
    """管理者项目列表（可点击进入详情）。"""
    user = admin_only(request)
    db = SessionLocal()
    try:
        d = consolidated(db)
        return templates.TemplateResponse(
            request,
            "manager_project_list.html",
            {
                "request": request,
                "title": "全部项目",
                "user": user,
                "d": d,
            },
        )
    finally:
        db.close()


@router.get("/project/{pid}", response_class=HTMLResponse)
def manager_project_detail(request: Request, pid: int) -> HTMLResponse:
    """管理者项目详情页：含完整经济指标 + AI 自由问答。"""
    user = admin_only(request)
    db = SessionLocal()
    try:
        s = project_summary(db, pid)
        project = db.get(Project, pid)
        if project is None:
            return RedirectResponse("/manager/project-list", 302)

        # 税务台账
        ledgers = db.execute(
            select(TaxLedger).where(TaxLedger.generated == True)  # noqa: E712
        ).scalars().all()

        # 成本明细
        from ..models import RealCost
        costs = db.execute(
            select(RealCost).where(RealCost.project_id == pid)
        ).scalars().all()

        # 可用 AI 端点（外部主力模型优先，Mock 作为兜底）
        endpoints = (
            db.query(AIModelEndpoint)
            .filter(AIModelEndpoint.enabled == True)  # noqa: E712
            .order_by(case((AIModelEndpoint.adapter == "mock", 1), else_=0), AIModelEndpoint.id)
            .all()
        )

        return templates.TemplateResponse(
            request,
            "manager_project.html",
            {
                "request": request,
                "title": f"{project.name} ({project.code})",
                "user": user,
                "project": project,
                "s": s,
                "ledgers": ledgers,
                "costs": costs,
                "endpoints": endpoints,
                "fmt": _fmt,
            },
        )
    finally:
        db.close()


@router.post("/project/{pid}/ask")
def manager_ask(
    request: Request,
    pid: int,
    question: str = Form(...),
    endpoint_id: int = Form(...),
) -> HTMLResponse:
    """处理 AI 问答请求，返回 JSON 片段（JS 接管）。"""
    user = admin_only(request)
    db = SessionLocal()
    try:
        ctx = _build_project_context(db, pid)
        answer = _call_ai(question, ctx, endpoint_id)

        # 记录审计
        audit_from_request(request, "manager_ask", "project", str(pid),
                           f"AI问答: {question[:80]}", user.username)

        from fastapi.responses import JSONResponse
        return JSONResponse({"answer": answer})
    finally:
        db.close()


def audit_from_request(request: Request, action: str, obj_type: str,
                       obj_id: str, message: str, actor: str = "system") -> None:
    """将操作写入审计日志。"""
    from ..db import SessionLocal
    db = SessionLocal()
    try:
        db.add(AuditLog(
            action=action,
            object_type=obj_type,
            object_id=obj_id,
            message=message,
            actor=actor,
            ip=request.client.host if request.client else "",
        ))
        db.commit()
    finally:
        db.close()
