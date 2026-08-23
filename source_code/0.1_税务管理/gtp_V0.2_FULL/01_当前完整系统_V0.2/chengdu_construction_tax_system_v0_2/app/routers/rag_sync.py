"""V0.2: RAG 税务数据同步路由。

从 ProjectRAG 知识库 AI 抽取发票、合同、付款、完税凭证数据，
经置信度分流后自动入库或存入待确认表。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import config
from ..db import SessionLocal
from ..models import (
    Contract, Invoice, CashFlow, Project, TaxLedger, Entity,
    SyncLog, SyncPending, ProjectRAGMap,
)

router = APIRouter(prefix="/rag-sync", tags=["RAG同步"])


# ============================================================
# 配置
# ============================================================

RAG_URL = config.RAG_SERVICE_URL.rstrip("/")
RAG_API_KEY = config.RAG_API_KEY or ""
AUTO_CONF_THRESHOLD = Decimal(str(config.SYNC_CONFIDENCE_THRESHOLD))  # >= 此值自动入库


# ============================================================
# 请求/响应 Schema
# ============================================================

class RagConnectRequest(BaseModel):
    url: str = Field(default="http://127.0.0.1:8800", description="RAG 服务地址")
    api_key: str = Field(default="", description="RAG API 密钥（可选）")


class RagConnectResponse(BaseModel):
    ok: bool
    rag_version: str = ""
    llm_extraction: bool = False
    projects: list[dict] = []
    error: str = ""


class ProjectRAGMapRequest(BaseModel):
    """项目映射请求：在税务系统中注册 RAG 知识空间 ID。"""
    project_id: int = Field(..., description="税务系统项目 ID")
    rag_project_id: int = Field(..., description="RAG 项目 ID")
    rag_project_code: str = Field(default="", description="RAG 项目代码")
    rag_url: str = Field(default="", description="RAG 服务 URL（可覆盖全局默认）")
    rag_api_key: str = Field(default="", description="RAG API Key（可覆盖全局默认）")
    note: str = Field(default="")


class SyncRequest(BaseModel):
    project_id: int = Field(..., description="税务系统项目 ID")
    rag_project_id: int | None = Field(
        default=None,
        description="RAG 项目 ID；若已建立映射可省略",
    )
    extract_type: str = Field(
        ...,
        description="抽取类型: invoice | contract | payment | tax_payment",
    )
    period_start: str | None = Field(default=None, description="期间筛选 YYYY-MM")
    period_end: str | None = Field(default=None)
    top_k: int = Field(default=30, ge=1, le=100)
    note: str = Field(default="")


class SyncBatchRequest(BaseModel):
    project_id: int = Field(..., description="税务系统项目 ID")
    rag_project_id: int | None = Field(
        default=None, description="RAG 项目 ID；已建立映射可省略",
    )
    extract_types: list[str] = Field(
        default=["invoice", "contract", "payment"],
        description="批量抽取类型列表",
    )
    period_start: str | None = Field(default=None)
    period_end: str | None = Field(default=None)
    note: str = Field(default="")


class SyncResponse(BaseModel):
    sync_log_id: int
    sync_type: str
    status: str
    total_extracted: int
    total_imported: int
    total_pending: int
    imported_ids: list[int]
    pending_ids: list[int]
    errors: list[str]


# ============================================================
# 工具函数
# ============================================================

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _rag_headers(api_key: str = "") -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    key = api_key or RAG_API_KEY
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def _resolve_rag_project(db, project_id: int, rag_project_id: int | None) -> tuple[int, str, str]:
    """解析税务项目对应的 RAG 项目参数。

    Returns:
        (rag_project_id, rag_url, rag_api_key)
    """
    mapping = db.query(ProjectRAGMap).filter(ProjectRAGMap.project_id == project_id).first()
    if not mapping and not rag_project_id:
        raise HTTPException(
            400,
            f"项目 {project_id} 未配置 RAG 映射，且未传入 rag_project_id；"
            "请先调用 /rag-sync/project-map 注册映射",
        )

    resolved_id = rag_project_id or (mapping.rag_project_id if mapping else 0)
    resolved_url = (mapping.rag_url if mapping and mapping.rag_url else RAG_URL).rstrip("/")
    resolved_key = mapping.rag_api_key if mapping and mapping.rag_api_key else RAG_API_KEY

    return resolved_id, resolved_url, resolved_key


def _resolve_entity_code(
    db,
    code: str | None = None,
    name: str | None = None,
    tax_id: str | None = None,
) -> str:
    """根据纳税人识别号 / 名称 解析税务系统 entity_code。

    优先匹配 tax_id，再按 short_name / name 模糊匹配；找不到返回空字符串。
    """
    if code:
        return code

    if tax_id:
        ent = db.query(Entity).filter(Entity.tax_id == tax_id).first()
        if ent:
            return ent.code

    candidates = [name, tax_id]
    for q in candidates:
        if not q:
            continue
        ent = (
            db.query(Entity)
            .filter(Entity.name == q)
            .first()
        )
        if ent:
            return ent.code
        ent = (
            db.query(Entity)
            .filter(Entity.short_name == q)
            .first()
        )
        if ent:
            return ent.code
        ent = (
            db.query(Entity)
            .filter(Entity.name.like(f"%{q}%"))
            .first()
        )
        if ent:
            return ent.code

    return ""


def _call_rag_extract(
    rag_url: str,
    api_key: str,
    rag_project_id: int,
    extract_type: str,
    period_start: str | None,
    period_end: str | None,
    top_k: int,
) -> dict[str, Any]:
    """调用 RAG /api/v1/extract-tax 接口。"""
    url = f"{rag_url}/api/v1/extract-tax"
    payload = {
        "project_id": rag_project_id,
        "extract_type": extract_type,
        "top_k": top_k,
    }
    if period_start:
        payload["period_start"] = period_start
    if period_end:
        payload["period_end"] = period_end

    try:
        with httpx.Client(timeout=120) as client:
            resp = client.post(url, headers=_rag_headers(api_key), json=payload)
            resp.raise_for_status()
            return resp.json()
    except httpx.TimeoutException:
        raise HTTPException(504, "RAG 服务响应超时（120s）")
    except httpx.HTTPStatusError as e:
        raise HTTPException(502, f"RAG 返回错误 {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        raise HTTPException(502, f"RAG 调用失败: {e}")


def _map_invoice_fields(db, fields: dict, project_id: int) -> dict[str, Any]:
    """将 RAG 抽取的发票字段映射为税务系统 Invoice 模型字段。"""
    buyer_code = _resolve_entity_code(
        db,
        tax_id=fields.get("buyer_code"),
        name=fields.get("buyer_name"),
    )
    seller_code = _resolve_entity_code(
        db,
        tax_id=fields.get("seller_code"),
        name=fields.get("seller_name"),
    )

    total = fields.get("total_amount")
    vat = fields.get("vat_amount")
    net = Decimal("0")
    if total is not None and vat is not None:
        net = Decimal(str(total)) - Decimal(str(vat))

    period = fields.get("period") or (fields.get("invoice_date", "")[:7] if fields.get("invoice_date") else "")

    return {
        "project_id": project_id,
        "invoice_no": fields.get("invoice_no") or "",
        "period": period,
        "entity_code": buyer_code,
        "direction": fields.get("direction") or "",
        "counterparty_code": seller_code,
        "category": fields.get("category") or "",
        "net": net,
        "vat": Decimal(str(vat or 0)),
        "rate": Decimal(str(fields.get("vat_rate") or 0)),
        "deductible": bool(fields.get("deductible", True)),
        "note": fields.get("note") or "",
    }


def _map_contract_fields(db, fields: dict, project_id: int) -> dict[str, Any]:
    """将 RAG 抽取的合同字段映射为税务系统 Contract 模型字段。"""
    party_a = _resolve_entity_code(
        db,
        tax_id=fields.get("party_a_code"),
        name=fields.get("party_a_name"),
    )
    party_b = _resolve_entity_code(
        db,
        tax_id=fields.get("party_b_code"),
        name=fields.get("party_b_name"),
    )
    internal_codes = {e.code for e in db.query(Entity).filter(Entity.internal == True).all()}
    internal_trade = party_a in internal_codes and party_b in internal_codes

    return {
        "project_id": project_id,
        "contract_no": fields.get("contract_no") or "",
        "buyer_code": party_a,
        "seller_code": party_b,
        "category": fields.get("category") or fields.get("contract_type") or "",
        "amount": Decimal(str(fields.get("total_amount") or 0)),
        "internal_trade": internal_trade,
        "note": fields.get("note") or "",
    }


def _map_cashflow_fields(db, fields: dict, project_id: int) -> dict[str, Any]:
    """将 RAG 抽取的付款字段映射为税务系统 CashFlow 模型字段。"""
    payer_code = _resolve_entity_code(
        db,
        tax_id=fields.get("payer_account"),
        name=fields.get("payer_name"),
    )
    counterparty_code = _resolve_entity_code(
        db,
        tax_id=fields.get("counterparty_code"),
        name=fields.get("payee_name"),
    )

    period = fields.get("period") or (fields.get("payment_date", "")[:7] if fields.get("payment_date") else "")
    note_parts = []
    if fields.get("contract_no"):
        note_parts.append(f"合同 {fields['contract_no']}")
    if fields.get("payment_method"):
        note_parts.append(f"方式 {fields['payment_method']}")
    if fields.get("note"):
        note_parts.append(fields["note"])

    return {
        "project_id": project_id,
        "entity_code": payer_code,
        "counterparty_code": counterparty_code,
        "direction": fields.get("direction") or "",
        "amount": Decimal(str(fields.get("amount") or 0)),
        "period": period,
        "note": " | ".join(note_parts),
    }


def _map_tax_payment_fields(db, fields: dict, project_id: int) -> dict[str, Any]:
    """将 RAG 抽取的完税凭证字段映射为税务系统 TaxLedger 模型字段。

    TaxLedger 是按期间/月汇总的税务台账。
    """
    entity_code = _resolve_entity_code(
        db,
        tax_id=fields.get("taxpayer_code"),
        name=fields.get("taxpayer_name"),
    )
    tax_type = (fields.get("tax_type") or "").lower()
    tax_period = fields.get("tax_period") or (fields.get("payment_date", "")[:7] if fields.get("payment_date") else "")
    tax_amount = Decimal(str(fields.get("tax_amount") or 0))
    note_parts = [
        f"完税凭证 {fields.get('receipt_no', '')}".strip(),
        f"税种 {tax_type or 'unknown'}".strip(),
        f"本金 {fields.get('principal_amount', '')}".strip(),
        f"滞纳金 {fields.get('penalty_amount', '')}".strip(),
    ]
    note_parts = [p for p in note_parts if not p.endswith(" ") or p.strip().count(" ") > 1]
    note = " | ".join(p for p in note_parts if p.strip() and not p.strip().endswith(" "))

    ledger_kwargs: dict[str, Any] = {
        "period": tax_period,
        "entity_code": entity_code or "A",
        "generated": False,
    }

    if tax_type == "vat":
        ledger_kwargs["vat_payable"] = tax_amount
    elif tax_type in {"income", "cit", "enterprise_income"}:
        ledger_kwargs["estimated_cit"] = tax_amount
        ledger_kwargs["cit_note"] = note
    else:
        ledger_kwargs["cit_note"] = note or tax_type

    return ledger_kwargs, note


def _dedup_check(db, model_cls, project_id: int, extract_type: str, fields: dict) -> bool:
    """根据 extract_type 检查是否已存在相同记录，避免重复入库。"""
    if extract_type == "invoice":
        invoice_no = fields.get("invoice_no")
        if invoice_no:
            exist = db.query(Invoice).filter(
                Invoice.project_id == project_id,
                Invoice.invoice_no == invoice_no
            ).first()
            return exist is not None

    elif extract_type == "contract":
        contract_no = fields.get("contract_no")
        if contract_no:
            exist = db.query(Contract).filter(
                Contract.project_id == project_id,
                Contract.contract_no == contract_no
            ).first()
            return exist is not None

    elif extract_type == "payment":
        # 去重逻辑：同日期+同金额+同方向视为同一笔
        payment_date = fields.get("payment_date")
        amount = fields.get("amount")
        direction = fields.get("direction")
        if payment_date and amount:
            exist = db.query(CashFlow).filter(
                CashFlow.project_id == project_id,
                CashFlow.direction == (direction or ""),
            ).first()
            return exist is not None

    return False


# ============================================================
# 核心同步逻辑
# ============================================================

def _do_sync(
    db,
    project_id: int,
    rag_project_id: int,
    rag_url: str,
    rag_api_key: str,
    extract_type: str,
    period_start: str | None,
    period_end: str | None,
    top_k: int,
    note: str,
    actor: str,
) -> SyncResponse:
    """执行一次同步，返回同步结果。"""
    # 1. 调用 RAG 抽取
    rag_data = _call_rag_extract(
        rag_url, rag_api_key, rag_project_id, extract_type,
        period_start, period_end, top_k,
    )

    extracted_items = rag_data.get("extracted_items") or []
    errors = rag_data.get("errors") or []

    # 2. 创建同步记录
    sync_log = SyncLog(
        project_id=project_id,
        sync_type=extract_type,
        rag_project_id=rag_project_id,
        rag_chunk_ids_json=json.dumps([i["source_chunk_id"] for i in extracted_items]),
        rag_document_ids_json=json.dumps(list({i["source_document_id"] for i in extracted_items})),
        tax_record_ids_json="[]",
        status="pending",
        total_chunks=rag_data.get("total_chunks", 0),
        total_extracted=len(extracted_items),
        total_imported=0,
        total_pending=0,
        errors_json=json.dumps(errors),
        synced_at=_now(),
        synced_by=actor,
        note=note,
    )
    db.add(sync_log)
    db.flush()  # 获取 sync_log.id

    imported_ids: list[int] = []
    pending_ids: list[int] = []

    # 3. 按置信度分流
    for item in extracted_items:
        fields = item.get("fields") or {}
        confidence = Decimal(str(item.get("confidence", 0)))

        # 去重检查
        if _dedup_check(db, None, project_id, extract_type, fields):
            errors.append(f"去重跳过: {item.get('filename')}")
            continue

        if confidence >= AUTO_CONF_THRESHOLD:
            # 自动入库
            try:
                record = _import_record(db, project_id, extract_type, fields)
                db.flush()
                imported_ids.append(record.id)
            except Exception as e:
                errors.append(f"入库失败 chunk={item['source_chunk_id']}: {e}")
        elif confidence >= Decimal("0.6"):
            # 存入待确认表
            pending = SyncPending(
                sync_log_id=sync_log.id,
                project_id=project_id,
                sync_type=extract_type,
                source_chunk_id=item["source_chunk_id"],
                source_document_id=item["source_document_id"],
                filename=item.get("filename") or "",
                page_start=item.get("page_start"),
                confidence=confidence,
                fields_json=json.dumps(fields, ensure_ascii=False),
                status="pending",
            )
            db.add(pending)
            db.flush()
            pending_ids.append(pending.id)
        else:
            errors.append(f"置信度过低跳过 chunk={item['source_chunk_id']} confidence={confidence}")

    # 4. 更新同步记录
    sync_log.status = "confirmed" if imported_ids or pending_ids else "error"
    sync_log.tax_record_ids_json = json.dumps(imported_ids)
    sync_log.total_imported = len(imported_ids)
    sync_log.total_pending = len(pending_ids)
    sync_log.errors_json = json.dumps(errors)
    db.commit()

    return SyncResponse(
        sync_log_id=sync_log.id,
        sync_type=extract_type,
        status=sync_log.status,
        total_extracted=len(extracted_items),
        total_imported=len(imported_ids),
        total_pending=len(pending_ids),
        imported_ids=imported_ids,
        pending_ids=pending_ids,
        errors=errors,
    )


def _import_record(db, project_id: int, extract_type: str, fields: dict):
    """根据 extract_type 将字段字典入库到对应税务模型。"""
    if extract_type == "invoice":
        mapped = _map_invoice_fields(db, fields, project_id)
        record = Invoice(**mapped)
        db.add(record)
        return record

    elif extract_type == "contract":
        mapped = _map_contract_fields(db, fields, project_id)
        record = Contract(**mapped)
        db.add(record)
        return record

    elif extract_type == "payment":
        mapped = _map_cashflow_fields(db, fields, project_id)
        record = CashFlow(**mapped)
        db.add(record)
        return record

    elif extract_type == "tax_payment":
        ledger_kwargs, note = _map_tax_payment_fields(db, fields, project_id)
        record = TaxLedger(**ledger_kwargs)
        db.add(record)
        return record

    raise ValueError(f"Unsupported extract_type: {extract_type}")


# ============================================================
# API 路由
# ============================================================

@router.post("/connect", response_model=RagConnectResponse)
def rag_connect(body: RagConnectRequest):
    """配置并测试 RAG 服务连接。"""
    url = body.url.rstrip("/")
    headers = _rag_headers()
    if body.api_key:
        headers["Authorization"] = f"Bearer {body.api_key}"

    try:
        with httpx.Client(timeout=10) as client:
            health = client.get(f"{url}/api/v1/health", headers=headers)
            health.raise_for_status()
            hdata = health.json()

            projects_resp = client.get(f"{url}/api/v1/projects", headers=headers)
            projects_resp.raise_for_status()
            projects = projects_resp.json()

        return RagConnectResponse(
            ok=True,
            rag_version=hdata.get("version", ""),
            llm_extraction=hdata.get("llm_extraction", False),
            projects=projects,
        )
    except httpx.HTTPStatusError as e:
        return RagConnectResponse(ok=False, error=f"HTTP {e.response.status_code}")
    except Exception as e:
        return RagConnectResponse(ok=False, error=str(e))


@router.get("/status", response_model=RagConnectResponse)
def rag_status():
    """查看 RAG 连接状态。"""
    return rag_connect(RagConnectRequest())


@router.post("/sync", response_model=SyncResponse)
def sync_single(body: SyncRequest, request: Request):
    """触发单类型同步：从 RAG 抽取指定类型数据并入库。"""
    actor = "demo-user"

    db = SessionLocal()
    try:
        # 验证项目存在
        proj = db.get(Project, body.project_id)
        if not proj:
            raise HTTPException(404, f"税务系统项目 {body.project_id} 不存在")

        rag_project_id, rag_url, rag_api_key = _resolve_rag_project(
            db, body.project_id, body.rag_project_id,
        )

        return _do_sync(
            db=db,
            project_id=body.project_id,
            rag_project_id=rag_project_id,
            rag_url=rag_url,
            rag_api_key=rag_api_key,
            extract_type=body.extract_type,
            period_start=body.period_start,
            period_end=body.period_end,
            top_k=body.top_k,
            note=body.note,
            actor=actor,
        )
    finally:
        db.close()


@router.post("/sync-batch")
def sync_batch(body: SyncBatchRequest, request: Request):
    """批量同步：按类型列表逐一同步。"""
    actor = "demo-user"
    results: list[SyncResponse] = []

    db = SessionLocal()
    try:
        proj = db.get(Project, body.project_id)
        if not proj:
            raise HTTPException(404, f"税务系统项目 {body.project_id} 不存在")

        rag_project_id, rag_url, rag_api_key = _resolve_rag_project(
            db, body.project_id, body.rag_project_id,
        )

        for extract_type in body.extract_types:
            try:
                result = _do_sync(
                    db=db,
                    project_id=body.project_id,
                    rag_project_id=rag_project_id,
                    rag_url=rag_url,
                    rag_api_key=rag_api_key,
                    extract_type=extract_type,
                    period_start=body.period_start,
                    period_end=body.period_end,
                    top_k=30,
                    note=body.note,
                    actor=actor,
                )
                results.append(result)
            except Exception as e:
                results.append(SyncResponse(
                    sync_log_id=0,
                    sync_type=extract_type,
                    status="error",
                    total_extracted=0,
                    total_imported=0,
                    total_pending=0,
                    imported_ids=[],
                    pending_ids=[],
                    errors=[str(e)],
                ))

        return {"project_id": body.project_id, "results": [r.model_dump() for r in results]}
    finally:
        db.close()


@router.get("/history")
def sync_history(
    project_id: int | None = None,
    sync_type: str | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """查看同步历史记录。"""
    db = SessionLocal()
    try:
        q = db.query(SyncLog)
        if project_id:
            q = q.where(SyncLog.project_id == project_id)
        if sync_type:
            q = q.where(SyncLog.sync_type == sync_type)
        if status:
            q = q.where(SyncLog.status == status)

        total = q.count()
        offset = (page - 1) * page_size
        logs = q.order_by(SyncLog.id.desc()).offset(offset).limit(page_size).all()

        return {
            "page": page,
            "page_size": page_size,
            "total": total,
            "items": [
                {
                    "id": log.id,
                    "project_id": log.project_id,
                    "sync_type": log.sync_type,
                    "status": log.status,
                    "total_chunks": log.total_chunks,
                    "total_extracted": log.total_extracted,
                    "total_imported": log.total_imported,
                    "total_pending": log.total_pending,
                    "synced_at": log.synced_at,
                    "synced_by": log.synced_by,
                    "note": log.note,
                    "errors": json.loads(log.errors_json or "[]"),
                }
                for log in logs
            ],
        }
    finally:
        db.close()


@router.get("/pending")
def sync_pending_list(
    project_id: int | None = None,
    sync_type: str | None = None,
    status: str = Query("pending"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """查看待确认的抽取结果。"""
    db = SessionLocal()
    try:
        q = db.query(SyncPending)
        if project_id:
            q = q.where(SyncPending.project_id == project_id)
        if sync_type:
            q = q.where(SyncPending.sync_type == sync_type)
        if status:
            q = q.where(SyncPending.status == status)

        total = q.count()
        offset = (page - 1) * page_size
        items = q.order_by(SyncPending.id.desc()).offset(offset).limit(page_size).all()

        return {
            "page": page,
            "page_size": page_size,
            "total": total,
            "items": [
                {
                    "id": p.id,
                    "sync_log_id": p.sync_log_id,
                    "project_id": p.project_id,
                    "sync_type": p.sync_type,
                    "source_chunk_id": p.source_chunk_id,
                    "filename": p.filename,
                    "page_start": p.page_start,
                    "confidence": float(p.confidence),
                    "fields": json.loads(p.fields_json or "{}"),
                    "status": p.status,
                    "confirmed_record_id": p.confirmed_record_id,
                    "confirmed_at": p.confirmed_at,
                    "note": p.note,
                }
                for p in items
            ],
        }
    finally:
        db.close()


@router.post("/pending/{pending_id}/confirm")
def confirm_pending(pending_id: int, request: Request):
    """确认一条待确认记录，触发入库。"""
    actor = "demo-user"

    db = SessionLocal()
    try:
        pending = db.get(SyncPending, pending_id)
        if not pending:
            raise HTTPException(404, "记录不存在")
        if pending.status != "pending":
            raise HTTPException(409, f"该记录状态为 {pending.status}，无法确认")

        fields = json.loads(pending.fields_json or "{}")

        record = _import_record(db, pending.project_id, pending.sync_type, fields)
        db.flush()

        pending.status = "confirmed"
        pending.confirmed_record_id = record.id
        pending.confirmed_at = _now()
        pending.confirmed_by = actor
        db.commit()

        return {
            "ok": True,
            "pending_id": pending_id,
            "record_id": record.id,
            "record_type": pending.sync_type,
        }
    finally:
        db.close()


@router.post("/pending/{pending_id}/reject")
def reject_pending(pending_id: int, note: str = "", request: Request = None):
    """拒绝一条待确认记录。"""
    db = SessionLocal()
    try:
        pending = db.get(SyncPending, pending_id)
        if not pending:
            raise HTTPException(404, "记录不存在")
        if pending.status != "pending":
            raise HTTPException(409, f"该记录状态为 {pending.status}，无法拒绝")

        pending.status = "rejected"
        pending.note = note
        db.commit()

        return {"ok": True, "pending_id": pending_id}
    finally:
        db.close()


# ============================================================
# RAG V1.0 Facts Provider 接入
# ============================================================
# 税务系统侧调用 RAG V1.0 统一事实通道 (Analytics Contract)：
# - GET /rag-sync/facts/{project_id}  拉取项目事实（默认 60s TTL）
# - POST /rag-sync/facts/{project_id}/invalidate  通知 RAG 失效缓存
# - GET /rag-sync/facts/{project_id}/snapshots  查询本地快照历史
# 设计原则：
# 1. 税务系统不复制指标口径（V1.0 的核心约束）
# 2. 每次调用留痕至 audit_log + facts_request_logs + facts_snapshots
# 3. AI Review 在调用 Facts 时强制 require_fresh=true

from ..services.facts_client import (
    get_project_facts,
    invalidate_facts,
    get_latest_snapshot,
    list_snapshots,
)


@router.get("/facts/{project_id}")
def facts_get(
    project_id: int,
    request: Request,
    require_fresh: bool = Query(False, description="强制获取最新数据（AI 深度体检场景）"),
    max_age: int = Query(60, ge=0, le=3600, description="缓存最大有效期(秒)"),
    as_of: str | None = Query(None, description="查询历史 Snapshot 时间点 ISO 8601"),
):
    """从 RAG V1.0 Facts Provider 拉取项目指标。"""
    actor = "demo-user"
    ip = request.client.host if request.client else ""
    payload = get_project_facts(
        project_id=project_id,
        require_fresh=require_fresh,
        max_age=max_age,
        as_of=as_of,
        actor=actor,
        ip=ip,
    )
    if payload.get("error"):
        raise HTTPException(502, payload)
    return payload


@router.post("/facts/{project_id}/invalidate")
def facts_invalidate(project_id: int, request: Request):
    """业务数据变更后通知 RAG V1.0 失效项目 Facts 缓存。"""
    actor = "demo-user"
    ip = request.client.host if request.client else ""
    payload = invalidate_facts(project_id=project_id, actor=actor, ip=ip)
    if payload.get("error"):
        raise HTTPException(502, payload)
    return payload


@router.get("/facts/{project_id}/snapshots")
def facts_snapshots_list(project_id: int, limit: int = Query(20, ge=1, le=100)):
    """查询本系统保存的 Facts 历史快照（无需调用 RAG）。"""
    return {"project_id": project_id, "items": list_snapshots(project_id, limit)}


@router.get("/facts/{project_id}/latest")
def facts_latest(project_id: int):
    """读取最近一次 Facts 快照（不调 RAG）。"""
    snapshot = get_latest_snapshot(project_id)
    if not snapshot:
        raise HTTPException(404, "无快照")
    return snapshot


# ============================================================
# 项目映射管理：税务项目 ↔ RAG 知识空间
# ============================================================

@router.post("/project-map")
def upsert_project_map(body: ProjectRAGMapRequest):
    """注册或更新税务项目 ↔ RAG 项目映射。

    首次调用为注册，重复调用为更新。可覆盖 RAG URL / API Key。
    """
    db = SessionLocal()
    try:
        proj = db.get(Project, body.project_id)
        if not proj:
            raise HTTPException(404, f"税务系统项目 {body.project_id} 不存在")

        mapping = db.query(ProjectRAGMap).filter(
            ProjectRAGMap.project_id == body.project_id,
        ).first()

        if mapping:
            mapping.rag_project_id = body.rag_project_id
            mapping.rag_project_code = body.rag_project_code or mapping.rag_project_code
            mapping.rag_url = body.rag_url or mapping.rag_url
            mapping.rag_api_key = body.rag_api_key or mapping.rag_api_key
            mapping.note = body.note or mapping.note
            mapping.synced_at = _now()
        else:
            mapping = ProjectRAGMap(
                project_id=body.project_id,
                rag_project_id=body.rag_project_id,
                rag_project_code=body.rag_project_code,
                rag_url=body.rag_url,
                rag_api_key=body.rag_api_key,
                note=body.note,
                synced_at=_now(),
                created_at=_now(),
            )
            db.add(mapping)

        db.commit()
        return {
            "ok": True,
            "project_id": body.project_id,
            "rag_project_id": body.rag_project_id,
        }
    finally:
        db.close()


@router.get("/project-map/{project_id}")
def get_project_map(project_id: int):
    """查看项目映射。"""
    db = SessionLocal()
    try:
        mapping = db.query(ProjectRAGMap).filter(
            ProjectRAGMap.project_id == project_id,
        ).first()
        if not mapping:
            raise HTTPException(404, "项目未配置 RAG 映射")
        return {
            "project_id": mapping.project_id,
            "rag_project_id": mapping.rag_project_id,
            "rag_project_code": mapping.rag_project_code,
            "rag_url": mapping.rag_url,
            "has_api_key": bool(mapping.rag_api_key),
            "note": mapping.note,
            "synced_at": mapping.synced_at,
        }
    finally:
        db.close()


@router.post("/sync-project/{project_id}")
def sync_project_from_rag(project_id: int, rag_project_id: int, name: str = ""):
    """从税务系统项目信息反向同步到 RAG 项目（确保 RAG 知识空间存在）。"""
    db = SessionLocal()
    try:
        proj = db.get(Project, project_id)
        if not proj:
            raise HTTPException(404, f"税务系统项目 {project_id} 不存在")

        rag_project_id_resolved, rag_url, rag_api_key = _resolve_rag_project(
            db, project_id, rag_project_id,
        )

        payload = {
            "project_code": proj.code,
            "name": name or proj.name,
            "external_system": "construction-tax",
            "external_project_id": str(project_id),
        }
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    f"{rag_url}/api/v1/projects/sync",
                    headers=_rag_headers(rag_api_key),
                    json=payload,
                )
                resp.raise_for_status()
                rag_resp = resp.json()
        except Exception as e:
            raise HTTPException(502, f"同步到 RAG 失败: {e}")

        mapping = db.query(ProjectRAGMap).filter(
            ProjectRAGMap.project_id == project_id,
        ).first()
        if not mapping:
            mapping = ProjectRAGMap(
                project_id=project_id,
                rag_project_id=rag_project_id_resolved,
                rag_project_code=rag_resp.get("project_code", proj.code),
                rag_url=rag_url,
                rag_api_key=rag_api_key,
                synced_at=_now(),
                created_at=_now(),
            )
            db.add(mapping)
        else:
            mapping.rag_project_id = rag_project_id_resolved
            mapping.synced_at = _now()
        db.commit()

        return {
            "ok": True,
            "project_id": project_id,
            "rag_project_id": rag_project_id_resolved,
            "rag_project": rag_resp,
        }
    finally:
        db.close()
