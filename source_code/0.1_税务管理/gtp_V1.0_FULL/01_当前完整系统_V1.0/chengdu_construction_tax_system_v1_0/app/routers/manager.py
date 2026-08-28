"""管理员（admin）专属路由：总览大屏 + 项目详情（含 AI 问答）。"""
from __future__ import annotations

import json
from decimal import Decimal

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai.adapter import (
    AIEndpointUnavailable,
    AIResponseContractError,
    endpoint_is_allowed,
    is_mock_endpoint,
)
from ..ai.adapter import call_endpoint as _ADAPTER_CALL_ENDPOINT
from ..ai.adapter import call_text_endpoint as _ADAPTER_CALL_TEXT_ENDPOINT
from ..ai.failover import call_text_with_failover, call_with_failover
from ..calc import consolidated, project_summary
from ..constants import CANONICAL_ENTITY_CODES
from ..db import SessionLocal
from ..dependencies import admin_only
from ..models import AIModelEndpoint, AuditLog, Entity, Invoice, Project, TaxLedger
from ..templates import templates

router = APIRouter(prefix="/manager", tags=["管理者"])

# Kept as a narrow monkeypatch seam for existing adapter tests.  Normal
# execution below detects the untouched alias and always uses the shared
# routing pool; a patched alias is only used by an isolated unit test.
call_endpoint = _ADAPTER_CALL_ENDPOINT
call_text_endpoint = _ADAPTER_CALL_TEXT_ENDPOINT

# Existing isolated tests monkeypatch the old structured seams.  Production
# execution uses the dedicated text failover path below; these identity marks
# let old tests remain narrow without making the natural-language route share
# the structured JSON contract.
_DEFAULT_STRUCTURED_FAILOVER = call_with_failover
_DEFAULT_TEXT_FAILOVER = call_text_with_failover


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


def _ai_failure_payload(exc: Exception, *, status: str = "DEGRADED") -> dict:
    reason = str(exc).replace("\x00", " ").replace("\n", " ").strip()[:2000]
    if not reason:
        reason = exc.__class__.__name__
    payload = {
        "answer": "真实 AI 端点当前不可用，请人工复核；系统未使用模拟结论。",
        "status": status,
        "requires_manual_review": True,
        "data_gaps": [f"AI状态={status}", f"真实 AI 端点不可用：{reason}"],
    }
    attempts = getattr(exc, "attempts", None)
    if isinstance(attempts, list) and attempts:
        payload["attempts"] = attempts
    return payload


def _normalise_endpoint_id(endpoint_id: int | str | None) -> int | None:
    """Normalize browser form input without turning blank values into 422."""
    if endpoint_id is None:
        return None
    if isinstance(endpoint_id, int):
        if endpoint_id <= 0:
            raise AIEndpointUnavailable(
                "指定的 AI 端点编号无效",
                status="UNAVAILABLE",
            )
        return endpoint_id
    value = str(endpoint_id).strip()
    if not value:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise AIEndpointUnavailable(
            "指定的 AI 端点编号无效",
            status="UNAVAILABLE",
        ) from exc
    if parsed <= 0:
        raise AIEndpointUnavailable(
            "指定的 AI 端点编号无效",
            status="UNAVAILABLE",
        )
    return parsed


def _manager_endpoint_order_key(endpoint: AIModelEndpoint) -> tuple[str, int, int]:
    """Return the same stable order used by the shared AI failover pool."""
    routing_group = str(getattr(endpoint, "routing_group", "default") or "default").strip()
    if not routing_group:
        routing_group = "default"
    raw_priority = getattr(endpoint, "priority", None)
    if raw_priority is None or (
        isinstance(raw_priority, str) and not raw_priority.strip()
    ):
        priority = 100
    else:
        try:
            parsed_priority = int(raw_priority)
        except (TypeError, ValueError):
            priority = 100
        else:
            # Keep the valid schema value 0 as the first position.  Negative
            # values are invalid and therefore use the safe legacy default.
            priority = parsed_priority if parsed_priority >= 0 else 100
    try:
        endpoint_id = int(getattr(endpoint, "id", 0) or 0)
    except (TypeError, ValueError):
        endpoint_id = 0
    return routing_group, priority, endpoint_id


def _manager_ai_endpoints(db: Session) -> list[AIModelEndpoint]:
    """Load selectable real endpoints in the configured failover order.

    The project question page deliberately has an explicit automatic mode.
    Its endpoint list is only a display/override list, so historical mock
    rows are excluded even in a test process that has opted into mock calls.
    Disabled real endpoints remain managed from the model settings page but
    are not offered as selectable callers here.
    """
    rows = (
        db.query(AIModelEndpoint)
        .filter(AIModelEndpoint.enabled == True)  # noqa: E712
        .order_by(
            AIModelEndpoint.routing_group.asc(),
            AIModelEndpoint.priority.asc(),
            AIModelEndpoint.id.asc(),
        )
        .all()
    )
    rows = [
        endpoint
        for endpoint in rows
        if not is_mock_endpoint(endpoint) and endpoint_is_allowed(endpoint)
    ]
    # Keep the contract deterministic for isolated session doubles as well as
    # ORM-backed results, whose database ordering is already the same.
    rows.sort(key=_manager_endpoint_order_key)
    return rows


def _call_ai(question: str, ctx: dict, endpoint_id: int | str | None = None) -> dict:
    """调用真实 AI；端点失败时返回显式降级信封，不回退到 mock。"""
    db = SessionLocal()
    try:
        endpoint_id = _normalise_endpoint_id(endpoint_id)
        endpoint = db.get(AIModelEndpoint, endpoint_id) if endpoint_id else None
        if endpoint_id and endpoint is None:
            return _ai_failure_payload(
                AIEndpointUnavailable("指定的 AI 端点不存在"),
                status="UNAVAILABLE",
            )

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
            # Natural-language manager answers have their own adapter and
            # failover contract: any non-empty assistant text is usable.  The
            # default branch never invokes the structured Review failover.
            if call_text_with_failover is not _DEFAULT_TEXT_FAILOVER:
                text_result = call_text_with_failover(
                    db,
                    messages,
                    ctx,
                    endpoint_id=endpoint_id,
                )
                answer = text_result.text
                route_meta = text_result.metadata
            elif call_text_endpoint is not _ADAPTER_CALL_TEXT_ENDPOINT:
                # Narrow seam for adapter-level unit tests.
                if endpoint is None:
                    raise AIEndpointUnavailable("未指定可直接调用的 AI 端点")
                answer = call_text_endpoint(endpoint, messages, ctx)
                route_meta = {
                    "endpoint_id": getattr(endpoint, "id", None),
                    "endpoint_name": getattr(endpoint, "name", ""),
                    "model": getattr(endpoint, "model", ""),
                    "status": "READY",
                    "fallback_used": False,
                    "attempts": [],
                }
            elif call_endpoint is not _ADAPTER_CALL_ENDPOINT:
                # Compatibility seam for older adapter tests.  The raw text
                # is authoritative for this question route even if the old
                # adapter also reports a JSON parse warning.
                result, raw, _parse_failed = call_endpoint(endpoint, messages, ctx)
                answer = str(raw or "").strip()
                if not answer and isinstance(result, dict):
                    answer = str(result.get("summary") or "").strip()
                if not answer:
                    raise AIResponseContractError(
                        "模型端点未返回非空 assistant 文本",
                    )
                route_meta = {
                    "endpoint_id": getattr(endpoint, "id", None),
                    "endpoint_name": getattr(endpoint, "name", ""),
                    "model": getattr(endpoint, "model", ""),
                    "status": "READY",
                    "fallback_used": False,
                    "attempts": [],
                }
            elif call_with_failover is not _DEFAULT_STRUCTURED_FAILOVER:
                # Compatibility seam for tests written against the old pool
                # function.  Do not expose its structured fields as a Review
                # result; only retain a non-empty answer and safe route meta.
                result, raw, _parse_failed, route_meta = call_with_failover(
                    db,
                    messages,
                    ctx,
                    endpoint_id=endpoint_id,
                )
                answer = str(raw or "").strip()
                if not answer and isinstance(result, dict):
                    answer = str(result.get("summary") or "").strip()
                if not answer:
                    raise AIResponseContractError(
                        "模型端点未返回非空 assistant 文本",
                    )
            else:
                text_result = call_text_with_failover(
                    db,
                    messages,
                    ctx,
                    endpoint_id=endpoint_id,
                )
                answer = text_result.text
                route_meta = text_result.metadata
        except AIEndpointUnavailable as exc:
            return _ai_failure_payload(exc, status=exc.status)
        except Exception as exc:
            return _ai_failure_payload(exc, status="DEGRADED")

        answer = str(answer or "").strip()
        if not answer:
            raise AIResponseContractError(
                "模型端点未返回非空 assistant 文本",
            )
        fallback_used = bool(route_meta.get("fallback_used"))
        route_degraded = route_meta.get("status") == "DEGRADED"
        data_gaps = ["AI解释仅供参考，不覆盖确定性计算结果"]
        requires_manual_review = False
        if fallback_used:
            data_gaps.append(
                "首选 AI 端点失败，已切换同路由组后备端点；本结果状态为 DEGRADED",
            )
            requires_manual_review = True
        elif route_degraded:
            data_gaps.append("AI文本问答状态为 DEGRADED，请人工复核")
            requires_manual_review = True
        return {
            "answer": answer[:2000],
            "status": (
                "DEGRADED"
                if fallback_used or route_degraded
                else "READY"
            ),
            "requires_manual_review": requires_manual_review,
            "data_gaps": data_gaps,
            "endpoint_id": route_meta.get("endpoint_id"),
            "endpoint_name": route_meta.get("endpoint_name", ""),
            "model": route_meta.get("model", ""),
            "fallback_used": fallback_used,
            "attempts": route_meta.get("attempts", []),
        }
    except Exception as exc:
        return _ai_failure_payload(exc, status="DEGRADED")
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

        # 仅展示可调用的真实端点；模型设置中的 priority/routing_group
        # 决定自动模式和共享 failover 池的顺序，历史 mock 记录永不显示。
        endpoints = _manager_ai_endpoints(db)

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
    endpoint_id: str | None = Form(None),
) -> HTMLResponse:
    """处理 AI 问答请求，返回 JSON 片段（JS 接管）。

    ``endpoint_id`` is optional because the browser assistant intentionally
    lets the server choose the configured model pool.  ``_call_ai`` keeps an
    explicitly supplied id on the same failover path, while a missing/blank
    value selects the default routing group.  With no eligible real endpoint,
    the call returns an explicit ``UNAVAILABLE``/``DEGRADED`` envelope rather
    than FastAPI rejecting the form as HTTP 422.
    """
    user = admin_only(request)
    db = SessionLocal()
    try:
        ctx = _build_project_context(db, pid)
        try:
            normalized_endpoint_id = _normalise_endpoint_id(endpoint_id)
        except AIEndpointUnavailable as exc:
            payload = _ai_failure_payload(exc, status=exc.status)
        else:
            payload = _call_ai(question, ctx, normalized_endpoint_id)

        # 记录审计
        audit_from_request(request, "manager_ask", "project", str(pid),
                           f"AI问答: {question[:80]}", user.username)

        from fastapi.responses import JSONResponse
        return JSONResponse(payload)
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
