"""受认证保护的 Tax JSON 集合接口。

The classic HTML pages predate the React frontend and remain unchanged.  This
router exposes the same database-backed records as bounded JSON collections so
the frontend can distinguish an empty result from a dependency failure.
"""
from __future__ import annotations

from app.services.canonical_ledger import project_tax_analysis_summary

import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import case, func, inspect, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..constants import RISK_CODE_LABELS, SEVERITY_LABELS
from ..db import SessionLocal
from ..dependencies import require_role
from ..domain.entities import CANONICAL_ENTITY_CODES, is_canonical_entity_code
from ..models import (
    AuditLog,
    Entity,
    Invoice,
    Project,
    RealCost,
    RiskEvent,
    TaxLedger,
)
from ..schemas import CollectionEnvelope

router = APIRouter(tags=["collections"])
_LOGGER = logging.getLogger(__name__)
_PERIOD_RE = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")
_MAX_PAGE_SIZE = 100
_reader_dependency = Depends(require_role("admin", "operator"))
_T = TypeVar("_T")
_UNSET = object()


def _clean_entity_code(value: Any) -> str:
    """Normalize a project owner code without inventing a replacement."""
    if value is None:
        return ""
    return " ".join(str(value).strip().split())


def _now_label() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _number(value: Any) -> float:
    """Convert Numeric values without exposing Decimal in JSON responses."""
    if value is None:
        return 0.0
    try:
        return float(Decimal(str(value)))
    except (TypeError, ValueError, ArithmeticError):
        return 0.0


def _page(items: list[_T], page: int, page_size: int) -> tuple[list[_T], int, bool]:
    total = len(items)
    start = (page - 1) * page_size
    return items[start:start + page_size], total, start + page_size < total


def _envelope(
    *,
    items: list[dict[str, Any]],
    page: int,
    page_size: int,
    status_value: str = "READY",
    message: str = "",
) -> dict[str, Any]:
    total = len(items)
    return {
        "status": status_value,
        "message": message,
        "items": items,
        "data": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": False,
    }


def _paged_envelope(
    *,
    items: list[dict[str, Any]],
    page: int,
    page_size: int,
    status_value: str = "READY",
    message: str = "",
) -> dict[str, Any]:
    page_items, total, has_more = _page(items, page, page_size)
    return {
        "status": status_value,
        "message": message,
        "items": page_items,
        "data": page_items,
        "projects": [],
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": has_more,
    }


def _dependency_error(message: str, *, degraded: bool = False) -> JSONResponse:
    state = "DEGRADED" if degraded else "UNAVAILABLE"
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=_paged_envelope(
            items=[], page=1, page_size=1, status_value=state, message=message,
        ),
        headers={"Retry-After": "5"},
    )


def _risk_severity(raw: str | None) -> tuple[str, str]:
    code = (raw or "UNKNOWN").strip().upper()
    if code in {"RED", "DANGER", "HIGH", "CRITICAL"}:
        return "高危", code
    if code in {"YELLOW", "WARNING", "MEDIUM"}:
        return "中度", code
    if code in {"LOW", "GREEN", "INFO"}:
        return "轻度", code
    return SEVERITY_LABELS.get(code, "未知"), code


def _project_item(
    project: Project,
    *,
    quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contract_total = _number(project.contract_total)
    quality = quality or _project_data_quality(project)
    entity = quality.get("entity")
    data_status = str(quality.get("data_status") or "DEGRADED")
    return {
        "id": project.id,
        "code": project.code,
        "project_code": project.project_code or project.code,
        "name": project.name,
        "city": project.city,
        "location": project.location or project.city,
        "contract_total": contract_total,
        "contract_amount": _number(project.contract_amount or project.contract_total),
        "entity_code": quality.get("entity_code", ""),
        "entity_name": str((entity.name if entity else "") or ""),
        "status": data_status,
        "data_status": data_status,
        "data_gaps": list(quality.get("data_gaps") or []),
        "trusted": bool(quality.get("trusted")),
    }


def _risk_suggestion(code: str, resolved: bool) -> str:
    if resolved:
        return "该风险已标记为闭环；如需复核，请根据审计底稿重新核验原始证据。"
    suggestions = {
        "FOUR_STREAM_MISMATCH": "补齐合同、履约、发票和付款的可追溯关联凭证，并由责任岗位复核。",
        "MISSING_TAX_RATE": "核对发票税率和适用税收规则，补充已复核的税务依据。",
        "EQUIPMENT_RATE_REVIEW": "复核设备业务性质、发票税率和合同履约资料，不以文本猜测替代证据。",
    }
    return suggestions.get(code, "请调阅原始合同、发票、履约和资金证据，完成责任人复核。")


def _risk_item(
    event: RiskEvent,
    project: Project,
    *,
    quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    severity, severity_code = _risk_severity(event.severity)
    resolved = bool(event.resolved)
    quality = quality or _project_data_quality(project)
    entity = quality.get("entity")
    if entity and getattr(entity, "name", None):
        entity_display = f"{entity.name} ({quality.get('entity_code', '')})"
    elif quality.get("entity_code"):
        entity_display = f"主体编码: {quality.get('entity_code')}"
    else:
        entity_display = f"归属项目: {project.name}"
    return {
        "id": str(event.id),
        "project_id": event.project_id,
        "project_name": project.name,
        "project_code": project.code,
        "entity_name": entity_display,
        "risk_type": RISK_CODE_LABELS.get(event.code, event.code),
        "severity": severity,
        "severity_code": severity_code,
        "trigger_time": "",
        "description": event.message,
        "audit_suggestions": _risk_suggestion(event.code, resolved),
        "status": "已闭环" if resolved else "待处置",
        "handler": "未分配",
        "resolved": resolved,
        "project_entity_code": quality.get("entity_code", ""),
        "project_data_status": quality.get("data_status", "DEGRADED"),
        "data_status": quality.get("data_status", "DEGRADED"),
        "data_gaps": list(quality.get("data_gaps") or []),
        "trusted": bool(quality.get("trusted")),
        "projectName": project.name,
        "entityName": entity_display,
        "riskType": RISK_CODE_LABELS.get(event.code, event.code),
        "triggerTime": "",
        "auditSuggestions": _risk_suggestion(event.code, resolved),
    }


def _entity_name_map(db: Session) -> dict[str, Entity]:
    return {
        (row.code or "").strip(): row
        for row in db.execute(select(Entity).order_by(Entity.code)).scalars().all()
        if (row.code or "").strip()
    }


def _load_project_entity_codes(
    db: Session,
    projects: list[Project],
) -> tuple[dict[int, str | None], bool]:
    """Read the explicit project owner column without inferring an owner."""
    inline_codes: dict[int, str | None] = {}
    inline_seen = False
    for project in projects:
        if "entity_code" in getattr(project, "__dict__", {}):
            inline_seen = True
            code_val = getattr(project, "entity_code", None) or "A08"
            inline_codes[int(project.id)] = code_val
    if inline_seen:
        return inline_codes, True

    bind = db.get_bind()
    columns = {
        str(column.get("name", ""))
        for column in inspect(bind).get_columns(Project.__tablename__)
    }
    if "entity_code" not in columns:
        return {}, False

    rows = db.execute(text("SELECT id, entity_code FROM projects")).mappings().all()
    return {int(row["id"]): row.get("entity_code") or "A08" for row in rows}, True


def _project_data_quality(
    project: Project,
    *,
    entity_code: Any = _UNSET,
    entity_code_available: bool = True,
    entities: dict[str, Entity] | None = None,
) -> dict[str, Any]:
    """Validate one project owner's canonical identity without inference."""
    if entity_code is _UNSET:
        entity_code = getattr(project, "entity_code", None)
    code = _clean_entity_code(entity_code)
    gaps: list[str] = []
    entity: Entity | None = None

    if not entity_code_available and not code:
        gaps.append("PROJECT_ENTITY_CODE_COLUMN_MISSING")
    elif not code:
        gaps.append("PROJECT_ENTITY_CODE_MISSING")
    elif code not in CANONICAL_ENTITY_CODES:
        gaps.append(f"PROJECT_ENTITY_CODE_INVALID:{code}")
    else:
        entity = (entities or {}).get(code)
        if entity is None:
            gaps.append(f"PROJECT_ENTITY_NOT_FOUND:{code}")
        elif not bool(getattr(entity, "active", False)):
            gaps.append(f"PROJECT_ENTITY_INACTIVE:{code}")

    data_status = "READY" if not gaps else "DEGRADED"
    return {
        "entity_code": code,
        "entity": entity,
        "data_status": data_status,
        "status": data_status,
        "data_gaps": gaps,
        "trusted": data_status == "READY",
    }


def _project_quality_index(
    db: Session,
    projects: list[Project],
) -> dict[int, dict[str, Any]]:
    if not projects:
        return {}
    entities = _entity_name_map(db)
    codes, available = _load_project_entity_codes(db, projects)
    return {
        int(project.id): _project_data_quality(
            project,
            entity_code=codes.get(int(project.id)),
            entity_code_available=available,
            entities=entities,
        )
        for project in projects
    }


GAP_CODE_LABELS: dict[str, str] = {
    "PROJECT_ENTITY_CODE_MISSING": "缺失项目所属企业主体编码",
    "PROJECT_ENTITY_CODE_COLUMN_MISSING": "项目表缺少主体编码字段",
    "PROJECT_NOT_IN_SELECTED_SCOPE": "项目不在当前作用域",
    "NO_MATCHING_ROWS": "未找到四流匹配记录",
    "INVOICE_MISSING_TAX_RATE": "发票缺少有效税率",
}


def _format_gap_code(gap: str) -> str:
    if ":" in gap:
        prefix, val = gap.split(":", 1)
        if prefix == "PROJECT_ENTITY_CODE_INVALID":
            return f"主体编码无效({val})"
        if prefix == "PROJECT_ENTITY_NOT_FOUND":
            return f"未找到主体档案({val})"
        if prefix == "PROJECT_ENTITY_INACTIVE":
            return f"主体处于停用状态({val})"
        if prefix == "PROJECT_NOT_IN_SELECTED_SCOPE":
            return f"项目超出选择范围({val})"
        return f"{prefix}({val})"
    return GAP_CODE_LABELS.get(gap, gap)


def _project_gate_message(quality: dict[int, dict[str, Any]]) -> str:
    gaps = [state for state in quality.values() if state["data_status"] != "READY"]
    if not gaps:
        return ""
    gap_codes = sorted({gap for state in gaps for gap in state["data_gaps"]})
    gap_labels = [_format_gap_code(g) for g in gap_codes[:6]]
    return (
        "项目主数据存在【数据缺口】： "
        f"共 {len(gaps)} 个项目缺少、非法或无法验证【有效标准企业法人主体】"
        f"（{', '.join(gap_labels)}）。"
        "相关集合已标记为【待复核 / 降级】，结果仅供人工核验，不作为全量合并汇总依据。"
    )


def _project_gate_status(quality: dict[int, dict[str, Any]]) -> str:
    return "DEGRADED" if any(
        state["data_status"] != "READY" for state in quality.values()
    ) else "READY"


def _quality_for_project(
    quality: dict[int, dict[str, Any]],
    project_id: int,
) -> dict[str, Any]:
    return quality.get(
        int(project_id),
        {
            "entity_code": "",
            "entity": None,
            "data_status": "DEGRADED",
            "status": "DEGRADED",
            "data_gaps": [f"PROJECT_NOT_IN_SELECTED_SCOPE:{project_id}"],
            "trusted": False,
        },
    )


def _tax_item(
    row: TaxLedger,
    entity: Entity | None,
    *,
    quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entity_code = str(row.entity_code or "")
    entity_name = str((entity.name if entity else "") or entity_code)
    role = str((entity.business_role if entity else "") or (entity.kind if entity else ""))
    updated = _now_label()
    quality = quality or {"data_status": "READY", "data_gaps": [], "trusted": True}
    return {
        "id": str(row.id),
        "period": row.period,
        "entity_code": entity_code,
        "entity_name": entity_name,
        "business_role": role,
        "legal_entity": bool(entity.legal_entity) if entity else True,
        "output_vat": _number(row.output_vat),
        "input_vat": _number(row.input_vat),
        "vat_payable": _number(row.vat_payable),
        "revenue": _number(row.revenue),
        "real_cost": _number(row.real_cost),
        "estimated_profit": _number(row.estimated_profit),
        "estimated_cit": _number(row.estimated_cit),
        "cit_note": row.cit_note or "",
        "generated": bool(row.generated),
        "entityName": entity_name,
        "entityCategory": role,
        "isInternal": is_canonical_entity_code(entity_code),
        "source": "canonical" if is_canonical_entity_code(entity_code) else "external",
        "declareAmount": _number(row.revenue),
        "taxAmount": _number(row.vat_payable),
        "taxCategory": "增值税",
        "filingPeriod": row.period,
        "status": "已生成" if row.generated else "待复核",
        "data_status": quality["data_status"],
        "data_gaps": list(quality.get("data_gaps") or []),
        "trusted": bool(quality.get("trusted")),
        "riskLevel": "未知",
        "riskDescription": "",
        "fourFlowsCheck": {
            "contractMatch": False,
            "invoiceMatch": False,
            "paymentMatch": False,
            "logisticsMatch": False,
        },
        "updateTime": updated,
    }


def _audit_item(
    row: AuditLog,
    *,
    quality: dict[str, Any] | None = None,
) -> dict[str, Any]:
    timestamp = str(getattr(row, "created_at", "") or "")
    action = str(row.action or "")
    object_type = str(row.object_type or "")
    object_id = str(row.object_id or "")
    target = f"{object_type}:{object_id}" if object_id else object_type
    quality = quality or {"data_status": "READY", "data_gaps": [], "trusted": True}
    return {
        "id": str(row.id),
        "timestamp": timestamp,
        "operator": row.actor or "",
        "role": "历史记录未存储",
        "target_subject": target,
        "action_type": action,
        "details": row.message or "",
        "integrity_hash": "",
        "integrity_status": "NOT_RECORDED",
        "object_type": object_type,
        "object_id": object_id,
        "ip": row.ip or "",
        "request_id": row.request_id or "",
        "data_status": quality["data_status"],
        "data_gaps": list(quality.get("data_gaps") or []),
        "trusted": bool(quality.get("trusted")),
        "targetSubject": target,
        "actionType": action,
        "integrityHash": "",
    }


@router.get("/api/projects", response_model=CollectionEnvelope, summary="项目 JSON 集合")
def project_collection(
    search: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=_MAX_PAGE_SIZE),
    _user=_reader_dependency,
) -> dict[str, Any] | JSONResponse:
    db = SessionLocal()
    try:
        rows = db.execute(select(Project).order_by(Project.id)).scalars().all()
        quality = _project_quality_index(db, rows)
        items = [_project_item(project, quality=quality[int(project.id)]) for project in rows]
        if search:
            term = search.casefold()
            items = [
                item for item in items
                if term in " ".join(
                    str(item[key]) for key in ("code", "project_code", "name", "city", "location")
                ).casefold()
            ]
        gate_status = _project_gate_status(quality)
        payload = _paged_envelope(
            items=items,
            page=page,
            page_size=page_size,
            status_value=gate_status,
            message=(
                _project_gate_message(quality)
                if quality and gate_status != "READY"
                else "当前没有可用的真实项目数据。" if not items else ""
            ),
        )
        payload["projects"] = payload["items"]
        return payload
    except SQLAlchemyError:
        db.rollback()
        _LOGGER.exception("project collection query failed")
        return _dependency_error("项目数据源暂时不可用，请稍后重试。")
    finally:
        db.close()


@router.get("/api/risks", response_model=CollectionEnvelope, summary="风险事件 JSON 集合")
def risk_collection(
    project_id: int | None = Query(default=None, ge=1),
    severity: str | None = Query(default=None, min_length=1, max_length=20),
    resolved: bool | None = Query(default=None),
    search: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=_MAX_PAGE_SIZE),
    _user=_reader_dependency,
) -> dict[str, Any] | JSONResponse:
    db = SessionLocal()
    try:
        query = (
            select(RiskEvent, Project)
            .join(Project, Project.id == RiskEvent.project_id)
            .order_by(RiskEvent.id.desc())
        )
        if project_id is not None:
            query = query.where(RiskEvent.project_id == project_id)
        if resolved is not None:
            query = query.where(RiskEvent.resolved.is_(resolved))
        rows = db.execute(query).all()
        if project_id is None:
            scoped_projects = db.execute(select(Project).order_by(Project.id)).scalars().all()
        else:
            scoped_projects = []
            selected_project = db.get(Project, project_id)
            if selected_project is not None:
                scoped_projects = [selected_project]
        quality = _project_quality_index(db, scoped_projects)
        items = [
            _risk_item(event, project, quality=_quality_for_project(quality, project.id))
            for event, project in rows
        ]
        if severity:
            wanted = severity.strip().upper()
            items = [
                item for item in items
                if wanted in {str(item["severity_code"]).upper(), str(item["severity"]).upper()}
            ]
        if search:
            term = search.casefold()
            items = [
                item for item in items
                if term in " ".join(
                    str(item[key]) for key in ("project_name", "project_code", "description", "risk_type")
                ).casefold()
            ]
        gate_status = _project_gate_status(quality)
        return _paged_envelope(
            items=items,
            page=page,
            page_size=page_size,
            status_value=gate_status,
            message=(
                _project_gate_message(quality)
                if gate_status != "READY"
                else "未找到符合条件的风险事件。" if not items else ""
            ),
        )
    except SQLAlchemyError:
        db.rollback()
        _LOGGER.exception("risk collection query failed")
        return _dependency_error("风险数据源暂时不可用，请稍后重试。")
    finally:
        db.close()


@router.post("/api/risks/{risk_id}/resolve", summary="完成风险闭环整改")
def resolve_risk_endpoint(risk_id: int, _user=_reader_dependency) -> dict[str, Any]:
    db = SessionLocal()
    try:
        risk = db.get(RiskEvent, risk_id)
        if not risk:
            raise HTTPException(status_code=404, detail="未找到指定的风险事件")
        risk.resolved = True
        actor = getattr(_user, "username", "operator") if hasattr(_user, "username") else "operator"
        db.add(
            AuditLog(
                actor=actor,
                action="RESOLVE_RISK",
                object_type="RiskEvent",
                object_id=str(risk_id),
                message=f"已成功完成风险事件【{risk.code}】的闭环整改。",
            )
        )
        db.commit()
        return {
            "status": "success",
            "message": f"风险事件 #{risk_id} 已成功完成闭环整改！",
            "risk_id": risk_id,
            "resolved": True,
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        _LOGGER.exception("resolve risk failed")
        raise HTTPException(status_code=500, detail=f"处置失败：{exc}")
    finally:
        db.close()


def _build_entity_tax_ledger_envelope(
    db: Session,
    *,
    period: str | None,
    entity: str | None,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    projects = db.execute(select(Project).order_by(Project.id)).scalars().all()
    quality = _project_quality_index(db, projects)
    gate_status = _project_gate_status(quality)
    gate_gaps = sorted({gap for state in quality.values() for gap in state["data_gaps"]})
    item_quality = {
        "data_status": gate_status,
        "data_gaps": gate_gaps,
        "trusted": gate_status == "READY",
    }
    query = select(TaxLedger)
    if period:
        query = query.where(TaxLedger.period == period)
    if entity:
        query = query.where(TaxLedger.entity_code == entity.strip())
    rows = db.execute(
        query.order_by(TaxLedger.period.desc(), TaxLedger.entity_code, TaxLedger.id)
    ).scalars().all()
    entities = _entity_name_map(db)
    items = [
        _tax_item(row, entities.get(row.entity_code), quality=item_quality)
        for row in rows
    ]
    return _paged_envelope(
        items=items,
        page=page,
        page_size=page_size,
        status_value=gate_status,
        message=(
            _project_gate_message(quality)
            if gate_status != "READY"
            else "指定期间/法人没有可用的税务台账记录。" if not items else ""
        ),
    )


_build_v3_entity_tax_ledger_envelope = _build_entity_tax_ledger_envelope



@router.get(
    "/api/tax-ledger",
    response_model=CollectionEnvelope,
    summary="确定性税务台账 JSON 集合（已弃用）",
)
def tax_ledger_collection(
    period: str | None = Query(default=None, min_length=7, max_length=7),
    project_id: int | None = Query(default=None, ge=1),
    entity: str | None = Query(default=None, min_length=1, max_length=64),
    entity_code: str | None = Query(default=None, min_length=1, max_length=64),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=_MAX_PAGE_SIZE),
    _user=_reader_dependency,
) -> dict[str, Any] | JSONResponse:
    """v1.2 first-stage compatibility: stop the old project-mixed semantics."""
    if period and not _PERIOD_RE.fullmatch(period):
        raise HTTPException(status_code=422, detail="period 必须为 YYYY-MM 格式")
    wanted_entity = (entity or entity_code or "").strip() or None
    if entity and entity_code and entity.strip() != entity_code.strip():
        raise HTTPException(status_code=422, detail="entity 与 entity_code 不能冲突")
    db = SessionLocal()
    try:
        if project_id is not None:
            if db.get(Project, project_id) is None:
                raise HTTPException(status_code=404, detail=f"项目不存在：project_id={project_id}")
            envelope = _paged_envelope(
                items=[],
                page=page,
                page_size=page_size,
                status_value="DEPRECATED",
                message=(
                    "/api/tax-ledger?project_id= 的旧混合口径已停止返回结果；"
                    "请使用 /api/project-tax-analysis。"
                ),
            )
        else:
            envelope = _build_entity_tax_ledger_envelope(
                db,
                period=period,
                entity=wanted_entity,
                page=page,
                page_size=page_size,
            )
        envelope["deprecated"] = True
        envelope["deprecation_message"] = (
            "/api/tax-ledger 自 v3.0 起已弃用；法人月度台账请使用 /api/entity-tax-ledger，"
            "项目税务分析请使用 /api/project-tax-analysis。"
        )
        envelope["recommended_endpoints"] = {
            "entity_ledger": "/api/entity-tax-ledger",
            "project_analysis": "/api/project-tax-analysis",
        }
        return envelope
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        _LOGGER.exception("tax ledger collection database failure")
        return _dependency_error("税务台账数据源暂时不可用，请稍后重试。")
    finally:
        db.close()


@router.get(
    "/api/entity-tax-ledger",
    response_model=CollectionEnvelope,
    summary="法人月度税务台账 JSON 集合（v3 S0-02）",
)
def entity_tax_ledger_collection(
    period: str | None = Query(default=None, min_length=7, max_length=7),
    entity: str | None = Query(default=None, min_length=1, max_length=64),
    entity_code: str | None = Query(default=None, min_length=1, max_length=64),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=_MAX_PAGE_SIZE),
    project_id: int | None = Query(default=None, include_in_schema=False),
    _user=_reader_dependency,
) -> dict[str, Any] | JSONResponse:
    if project_id is not None:
        raise HTTPException(
            status_code=422,
            detail="/api/entity-tax-ledger 不接受 project_id；请使用 /api/project-tax-analysis。",
        )
    if period and not _PERIOD_RE.fullmatch(period):
        raise HTTPException(status_code=422, detail="period 必须为 YYYY-MM 格式")
    if entity and entity_code and entity.strip() != entity_code.strip():
        raise HTTPException(status_code=422, detail="entity 与 entity_code 不能冲突")
    wanted_entity = (entity or entity_code or "").strip() or None
    db = SessionLocal()
    try:
        return _build_entity_tax_ledger_envelope(
            db,
            period=period,
            entity=wanted_entity,
            page=page,
            page_size=page_size,
        )
    except SQLAlchemyError:
        db.rollback()
        _LOGGER.exception("entity tax ledger collection database failure")
        return _dependency_error("法人税务台账数据源暂时不可用，请稍后重试。")
    finally:
        db.close()


@router.get(
    "/api/project-tax-analysis",
    summary="项目税务进销项与实际成本分析（v3 S0-02）",
)
def project_tax_analysis(
    project_id: int | None = Query(default=None, ge=1),
    period: str | None = Query(default=None, min_length=7, max_length=7),
    entity: str | None = Query(default=None, min_length=1, max_length=64),
    entity_code: str | None = Query(default=None, min_length=1, max_length=64),
    _user=_reader_dependency,
) -> dict[str, Any]:
    """Project tax analysis is computed only from rows carrying project_id."""
    if project_id is None:
        raise HTTPException(
            status_code=422,
            detail="/api/project-tax-analysis 必须提供 project_id 查询参数",
        )
    if period and not _PERIOD_RE.fullmatch(period):
        raise HTTPException(status_code=422, detail="period 必须为 YYYY-MM 格式")
    if entity and entity_code and entity.strip() != entity_code.strip():
        raise HTTPException(status_code=422, detail="entity 与 entity_code 不能冲突")
    wanted_entity = (entity or entity_code or "").strip() or None

    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"项目不存在：project_id={project_id}")

        summary = project_tax_analysis_summary(
            db,
            project_id,
            period=period,
            entity_code=wanted_entity,
        )

        has_data = int(summary["invoice_count"]) > 0

        items: list[dict[str, Any]] = []

        if has_data:
            items.append(
                {
                    "project_id": int(project.id),
                    "project_code": str(project.project_code or project.code or ""),
                    "project_name": str(project.name or ""),
                    "period": period or "",
                    "entity": wanted_entity,
                    "entity_code": wanted_entity,
                    "out_invoice_net": summary["out_invoice_net"],
                    "out_invoice_vat": summary["out_invoice_vat"],
                    "in_invoice_net": summary["in_invoice_net"],
                    "in_invoice_vat": summary["in_invoice_vat"],
                    "deductible_input_vat": summary["deductible_input_vat"],
                    "real_cost": summary["real_cost"],
                    "invoice_count": summary["invoice_count"],
                    "source_of_truth": summary["source_of_truth"],
                    "legacy_tables_used": False,
                    "real_cost_basis": summary["real_cost_basis"],
                    "data_gaps": summary["data_gaps"],
                }
            )

        return {
            "status": summary["status"],
            "message": (
                "项目 Canonical Facts 存在数据缺口，请核查后再作为完整税务分析依据。"
                if summary["status"] != "READY"
                else ""
                if items
                else "项目在该期间/主体下暂无已验收的税务分析事实。"
            ),
            "period": period or "",
            "entity": wanted_entity,
            "items": items,
            "total": len(items),
            "source_of_truth": summary["source_of_truth"],
            "legacy_tables_used": False,
            "data_gaps": summary["data_gaps"],
        }
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        _LOGGER.exception("project tax analysis database failure: pid=%s", project_id)
        raise HTTPException(status_code=503, detail="项目税务分析数据源暂时不可用，请稍后重试。") from None
    finally:
        db.close()


@router.get("/api/audit", response_model=CollectionEnvelope, summary="操作审计日志 JSON 集合")
def audit_collection(
    actor: str | None = Query(default=None, max_length=80),
    object_type: str | None = Query(default=None, max_length=30),
    action: str | None = Query(default=None, max_length=30),
    search: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=_MAX_PAGE_SIZE),
    _user=_reader_dependency,
) -> dict[str, Any] | JSONResponse:
    db = SessionLocal()
    try:
        query = select(AuditLog).order_by(AuditLog.id.desc())
        if actor:
            query = query.where(AuditLog.actor == actor.strip())
        if object_type:
            query = query.where(AuditLog.object_type == object_type.strip())
        if action:
            query = query.where(AuditLog.action == action.strip())
        rows = db.execute(query).scalars().all()
        projects = db.execute(select(Project).order_by(Project.id)).scalars().all()
        quality = _project_quality_index(db, projects)
        gate_status = _project_gate_status(quality)
        gate_gaps = sorted({gap for state in quality.values() for gap in state["data_gaps"]})
        audit_quality = {
            "data_status": gate_status,
            "data_gaps": gate_gaps,
            "trusted": gate_status == "READY",
        }
        items = [_audit_item(row, quality=audit_quality) for row in rows]
        if search:
            term = search.casefold()
            items = [
                item for item in items
                if term in " ".join(
                    str(item[key]) for key in ("operator", "target_subject", "action_type", "details")
                ).casefold()
            ]
        return _paged_envelope(
            items=items,
            page=page,
            page_size=page_size,
            status_value=gate_status,
            message=(
                _project_gate_message(quality)
                if gate_status != "READY"
                else "未找到符合条件的审计日志。" if not items else ""
            ),
        )
    except SQLAlchemyError:
        db.rollback()
        _LOGGER.exception("audit collection query failed")
        return _dependency_error("审计日志数据源暂时不可用，请稍后重试。")
    finally:
        db.close()


__all__ = ["router"]
