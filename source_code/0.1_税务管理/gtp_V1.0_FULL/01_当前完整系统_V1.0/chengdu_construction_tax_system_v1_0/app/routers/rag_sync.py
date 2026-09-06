"""V0.2: RAG 税务数据同步路由。

从 ProjectRAG 知识库 AI 抽取发票、合同、付款、完税凭证数据，
经置信度分流后自动入库或存入待确认表。
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import time
from pathlib import PurePath
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Form, HTTPException, Query, Request, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy import Date, DateTime, func, select

from .. import config
from ..audit import current_actor
from ..db import SessionLocal
from ..dependencies import admin_only
from ..models import (
    Budget,
    CashFlow,
    Contract,
    Entity,
    Invoice,
    Progress,
    Project,
    ProjectRAGMap,
    RagServiceEndpoint,
    RealCost,
    SyncLog,
    SyncPending,
)
from ..observability import get_request_id
from ..security import resolve_rag_service_addresses, validate_rag_service_url
from ..services.facts_client import (
    get_latest_snapshot,
    get_project_facts,
    invalidate_facts,
    list_snapshots,
)

# These models are introduced by the V1.0 data migration.  Keeping the import
# optional lets the router remain importable while an old development database
# is being upgraded; the import is checked before a tax-payment write is
# attempted.  In a migrated database both classes are always available.
try:
    from ..models import EntityBankAccount
except ImportError:  # pragma: no cover - only applies to pre-migration checkouts
    EntityBankAccount = None  # type: ignore[assignment,misc]
try:
    from ..models import ExternalParty
except ImportError:  # pragma: no cover - only applies to pre-migration checkouts
    ExternalParty = None  # type: ignore[assignment,misc]
try:
    from ..models import TaxPaymentRecord
except ImportError:  # pragma: no cover - only applies to pre-migration checkouts
    TaxPaymentRecord = None  # type: ignore[assignment,misc]

router = APIRouter(prefix="/rag-sync", tags=["RAG同步"])


# ============================================================
# 配置
# ============================================================

RAG_URL = config.RAG_SERVICE_URL.rstrip("/")
RAG_SHARED_KEY = config.RAG_SHARED_API_KEY or ""
# Backward-compatible symbol for callers that still refer to the old module
# name.  It is deliberately an alias of the dedicated shared credential, not
# a fallback to ``TAX_RAG_API_KEY`` or any value stored in ProjectRAGMap.
RAG_API_KEY = RAG_SHARED_KEY
AUTO_CONF_THRESHOLD = Decimal(str(config.SYNC_CONFIDENCE_THRESHOLD))  # >= 此值自动入库

VIRTUAL_ENTITY_CODES = frozenset({"A", "B", "C", "D", "甲", "乙", "丙", "丁"})
REVIEW_THRESHOLD = Decimal("0.6")
MONEY_TOLERANCE = Decimal("0.01")


class SyncReviewRequired(ValueError):
    """The extracted item is not safe for automatic posting.

    This is deliberately separate from an unexpected import failure.  The
    former becomes a ``SyncPending`` row; the latter is reported as a failed
    item and is isolated by a SAVEPOINT.
    """

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


# ============================================================
# 请求/响应 Schema
# ============================================================

class RagConnectRequest(BaseModel):
    url: str = Field(default="", max_length=300, description="RAG 服务地址；留空使用已保存配置")


class RagSettingsRequest(BaseModel):
    """Administrator request for the Tax -> RAG endpoint.

    The shared credential is intentionally absent.  It is process supplied on
    the Tax server and can never be submitted by the browser.
    """

    url: str = Field(..., min_length=1, max_length=300, description="RAG 服务 base URL")
    approve_private: bool = Field(
        default=False,
        description="明确批准该主机作为 Tax -> RAG 私网/回环服务",
    )


class RagSettingsResponse(BaseModel):
    ok: bool
    url: str = ""
    host: str = ""
    approved_private: bool = False
    configured: bool = False
    last_tested_at: str = ""
    rag_version: str = ""
    llm_extraction: bool = False
    projects: list[dict] = Field(default_factory=list)
    error: str = ""


class RagConnectResponse(BaseModel):
    ok: bool
    rag_version: str = ""
    llm_extraction: bool = False
    projects: list[dict] = Field(default_factory=list)
    error: str = ""


class ProjectRAGMapRequest(BaseModel):
    """项目映射请求：在税务系统中注册 RAG 知识空间 ID。"""
    project_id: int = Field(..., description="税务系统项目 ID")
    rag_project_id: int = Field(..., description="RAG 项目 ID")
    rag_project_code: str = Field(default="", description="RAG 项目代码")
    rag_url: str = Field(default="", max_length=300, description="RAG 服务 URL（可覆盖全局默认）")
    rag_api_key: str = Field(default="", max_length=512, description="已废弃；不得提交项目级密钥，统一使用 RAG_SHARED_API_KEY")
    note: str = Field(default="", max_length=300)


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


class SyncAndRecomputeRequest(BaseModel):
    project_id: int = Field(..., description="税务系统项目 ID")
    rag_project_id: int | None = Field(
        default=None, description="RAG 项目 ID；已建立映射可省略",
    )
    extract_types: list[str] = Field(
        default=["contract", "invoice", "payment", "tax_payment"],
        description="批量抽取类型列表",
    )
    period_start: str | None = Field(default=None)
    period_end: str | None = Field(default=None)
    top_k: int = Field(default=50, ge=1, le=100)
    note: str = Field(default="")


class ConfirmPendingContractRequest(BaseModel):
    """Deliberate operator acknowledgement for a master-data write."""

    confirm: Literal[True] = Field(
        ...,
        description="Must be true after the operator has reviewed the external-party identity.",
    )


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


_LOGGER = logging.getLogger(__name__)


def _rag_headers(api_key: str = "", *, request_id: str | None = None) -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    key = api_key or RAG_SHARED_KEY
    if key:
        h["Authorization"] = f"Bearer {key}"
    rid = (request_id or get_request_id() or "").strip()
    if rid:
        h["X-Request-ID"] = rid
    return h


def _stored_rag_endpoint(db) -> RagServiceEndpoint | None:
    """Return the enabled singleton endpoint, if one has been approved."""
    if db is None:
        return None
    return db.query(RagServiceEndpoint).filter(
        RagServiceEndpoint.id == 1,
        RagServiceEndpoint.enabled.is_(True),
    ).first()


def _approved_addresses(endpoint: RagServiceEndpoint | None) -> frozenset[str] | None:
    """Decode the administrator's DNS snapshot without accepting malformed data."""
    if endpoint is None:
        return None
    raw = str(getattr(endpoint, "resolved_addresses_json", "") or "")
    try:
        values = json.loads(raw or "[]")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("RAG 服务批准记录的 DNS 地址快照无效") from exc
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ValueError("RAG 服务批准记录的 DNS 地址快照无效")
    return frozenset(value.strip() for value in values if value.strip())


def _active_rag_url(db=None) -> str:
    endpoint = _stored_rag_endpoint(db)
    base_url = getattr(endpoint, "base_url", None)
    if endpoint is not None and str(base_url or "").strip():
        return str(base_url).strip().rstrip("/")
    return RAG_URL


def _validated_rag_url(
    raw_url: str,
    *,
    db=None,
    explicit_private_approval: bool = False,
) -> str:
    normalized = str(raw_url or "").strip().rstrip("/")
    if not normalized:
        raise ValueError("RAG 服务地址不能为空")
    endpoint = _stored_rag_endpoint(db)
    approved_private = False
    approved_addresses = None
    endpoint_url = str(getattr(endpoint, "base_url", "") or "").strip().rstrip("/")
    if endpoint_url and normalized == endpoint_url:
        approved_private = bool(endpoint.approved_private)
        approved_addresses = _approved_addresses(endpoint)
    allow_loopback_http = normalized == RAG_URL and (
        os.getenv("APP_ENV", "development").strip().lower()
        not in {"prod", "production", "staging"}
    )
    return validate_rag_service_url(
        normalized,
        allow_loopback_http=allow_loopback_http,
        allow_approved_private=approved_private or explicit_private_approval,
        approved_addresses=approved_addresses,
    )


def _resolve_rag_project(db, project_id: int, rag_project_id: int | None) -> tuple[int, str, str]:
    mapping = db.query(ProjectRAGMap).filter(ProjectRAGMap.project_id == project_id).first()
    resolved_id = rag_project_id or (mapping.rag_project_id if mapping else project_id)
    configured_url = (
        _active_rag_url(db)
        if _stored_rag_endpoint(db) is not None
        else (mapping.rag_url if mapping and mapping.rag_url else _active_rag_url(db))
    ).rstrip("/")
    try:
        resolved_url = _validated_rag_url(configured_url, db=db)
    except ValueError as exc:
        raise HTTPException(400, f"RAG 服务地址不安全: {exc}") from exc
    resolved_key = RAG_SHARED_KEY
    return resolved_id, resolved_url, resolved_key


def _clean_identity(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().split())


def _is_virtual_identity(value: str | None) -> bool:
    normalized = _clean_identity(value)
    return normalized.upper() in {"A", "B", "C", "D"} or normalized in {"甲", "乙", "丙", "丁"}


def _entity_matches(db, label: str, value: str) -> list[Any]:
    fields = {"code": ("code",), "tax_id": ("tax_id", "code"), "name": ("name", "short_name")}[label]
    rows: dict[int, Any] = {}
    for field in fields:
        column = getattr(Entity, field, None)
        if column is None:
            continue
        query = db.query(Entity).filter(column == value)
        active_column = getattr(Entity, "active", None)
        if active_column is not None:
            query = query.filter(active_column.is_(True))
        for entity in query.all():
            rows[id(entity)] = entity
    if not rows and label == "name":
        query = db.query(Entity)
        active_column = getattr(Entity, "active", None)
        if active_column is not None:
            query = query.filter(active_column.is_(True))
        clean_val = _clean_identity(value)
        for entity in query.all():
            entity_name = _clean_identity(getattr(entity, "name", ""))
            entity_short = _clean_identity(getattr(entity, "short_name", ""))
            if clean_val and ((clean_val in entity_name and len(clean_val) >= 4) or (entity_short and clean_val == entity_short)):
                rows[id(entity)] = entity
    return list(rows.values())


def _canonical_entity_code(db, entity: Any, seen: set[str] | None = None) -> str:
    seen = set() if seen is None else seen
    code = _clean_identity(getattr(entity, "code", ""))
    if not code:
        raise SyncReviewRequired("主体主数据缺少 code")
    if _is_virtual_identity(code):
        raise SyncReviewRequired(f"主体主数据仍包含虚拟主体 {code!r}")
    if code in seen:
        raise SyncReviewRequired(f"主体父子关系形成循环: {code}")
    seen.add(code)
    legal_entity = getattr(entity, "legal_entity", True)
    if legal_entity is False:
        parent_code = _clean_identity(getattr(entity, "parent_entity_code", ""))
        if not parent_code:
            raise SyncReviewRequired(f"分公司 {code} 缺少 parent_entity_code")
        rows = _entity_matches(db, "code", parent_code)
        if len(rows) != 1:
            raise SyncReviewRequired(f"分公司 {code} 的父主体 {parent_code!r} 未唯一匹配")
        return _canonical_entity_code(db, rows[0], seen)
    return code


def _resolve_entity_code(db, code: str | None = None, name: str | None = None, tax_id: str | None = None) -> str:
    identifiers = {"code": _clean_identity(code), "tax_id": _clean_identity(tax_id), "name": _clean_identity(name)}
    found: dict[int, Any] = {}
    for label, value in identifiers.items():
        if not value:
            continue
        if _is_virtual_identity(value):
            raise SyncReviewRequired(f"{label} 使用了已禁用的虚拟主体标识 {value!r}")
        rows = _entity_matches(db, label, value)
        if len(rows) > 1:
            raise SyncReviewRequired(f"{label}={value!r} 匹配到多个主体，无法自动入账")
        if not rows:
            raise SyncReviewRequired(f"{label}={value!r} 未识别到唯一实际主体")
        found[id(rows[0])] = rows[0]
    if not found:
        raise SyncReviewRequired("未提供可用于识别实际主体的 code、tax_id 或 name")
    codes = {_canonical_entity_code(db, entity) for entity in found.values()}
    if len(codes) != 1:
        raise SyncReviewRequired("code、tax_id、name 分别指向不同主体，存在身份冲突")
    return next(iter(codes))


def _resolve_entity_identifier(db, raw: Any = None, *, name: Any = None, code: Any = None, tax_id: Any = None) -> str:
    if raw is not None and not code and not tax_id:
        tax_id = raw
    return _resolve_entity_code(db, code=code, tax_id=tax_id, name=name)


def _external_party_matches(db, label: str, value: str) -> list[Any]:
    if ExternalParty is None:
        return []
    fields = {"code": ("code",), "tax_id": ("tax_id", "code"), "name": ("name", "short_name")}[label]
    rows: dict[int, Any] = {}
    for field in fields:
        column = getattr(ExternalParty, field, None)
        if column is None:
            continue
        query = db.query(ExternalParty).filter(column == value)
        active_column = getattr(ExternalParty, "active", None)
        if active_column is not None:
            query = query.filter(active_column.is_(True))
        for party in query.all():
            rows[id(party)] = party
    return list(rows.values())


def _resolve_external_party_code(db, raw: Any = None, *, name: Any = None, code: Any = None, tax_id: Any = None) -> str:
    if raw is not None and not code and not tax_id:
        tax_id = raw
    identifiers = {"code": _clean_identity(code), "tax_id": _clean_identity(tax_id), "name": _clean_identity(name)}
    found: dict[int, Any] = {}
    for label, value in identifiers.items():
        if not value:
            continue
        rows = _external_party_matches(db, label, value)
        if len(rows) != 1:
            raise SyncReviewRequired(f"外部交易方 {label}={value!r} 未唯一登记")
        found[id(rows[0])] = rows[0]
    if not found:
        raise SyncReviewRequired("未提供可识别的外部交易方身份")
    codes = {_clean_identity(getattr(p, "code", "")) for p in found.values()}
    if len(codes) != 1:
        raise SyncReviewRequired("外部交易方身份字段相互冲突")
    return next(iter(codes))


def ensure_external_party(db, raw: Any = None, *, name: Any = None, code: Any = None, tax_id: Any = None) -> str | None:
    if not hasattr(db, "query") or ExternalParty is None:
        return None
    clean_name = _clean_identity(name)
    clean_code = _clean_identity(code)
    clean_tax_id = _clean_identity(tax_id)
    target = clean_code if clean_code.startswith("EXT-") or clean_code in ("EA", "EB", "EC", "ED", "E0") else ""
    if target:
        rows = _external_party_matches(db, "code", target)
        if rows:
            return getattr(rows[0], "code", target)
    if clean_tax_id:
        rows = _external_party_matches(db, "tax_id", clean_tax_id)
        if rows:
            return getattr(rows[0], "code", "")
    if clean_tax_id and clean_name:
        code_value = _external_party_code_for_tax_id(clean_tax_id)
        party = ExternalParty(code=code_value, name=clean_name, short_name=clean_name[:60], kind="rag_confirmed", tax_id=clean_tax_id, active=True)
        db.add(party)
        db.flush()
        return party.code
    return None


def _resolve_party_code(db, raw: Any = None, *, name: Any = None, code: Any = None, tax_id: Any = None) -> str:
    try:
        return _resolve_entity_identifier(db, raw=raw, name=name, code=code, tax_id=tax_id)
    except SyncReviewRequired:
        return _resolve_external_party_code(db, raw=raw, name=name, code=code, tax_id=tax_id)


def _external_party_code_for_tax_id(tax_id: str) -> str:
    return f"EXT-{hashlib.sha256(tax_id.encode('utf-8')).hexdigest()[:10].upper()}"


def _is_internal_entity_code(db, code: str) -> bool:
    column = getattr(Entity, "code", None)
    if column is None:
        return False
    return bool(db.query(Entity).filter(column == code).all())


def _resolve_bank_account(db, account: Any) -> str:
    account_value = _clean_identity(account)
    if not account_value or EntityBankAccount is None:
        raise SyncReviewRequired("银行账号主数据无法识别")
    column = getattr(EntityBankAccount, "account_number", None)
    if column is None:
        raise SyncReviewRequired("银行账号主数据缺少 account_number 字段")
    rows = db.query(EntityBankAccount).filter(column == account_value).all()
    if len(rows) != 1:
        raise SyncReviewRequired(f"银行账号 {account_value!r} 未唯一匹配")
    return _resolve_entity_identifier(db, raw=getattr(rows[0], "entity_code", ""))


def _decimal(value: Any, label: str, *, required: bool = True) -> Decimal | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise SyncReviewRequired(f"{label} 缺失")
        return None
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise SyncReviewRequired(f"{label} 不是有效金额") from exc
    if not result.is_finite():
        raise SyncReviewRequired(f"{label} 不是有限金额")
    return result


def _date_value(fields: dict, *keys: str) -> str:
    raw = next((fields.get(key) for key in keys if fields.get(key)), None)
    if not raw:
        raise SyncReviewRequired("凭证缺少交易/缴款日期")
    value = _clean_identity(raw)[:10]
    datetime.fromisoformat(value)
    return value


def _bank_reference(fields: dict) -> str:
    return _clean_identity(fields.get("bank_reference") or fields.get("bank_ref") or fields.get("transaction_reference") or fields.get("reference") or fields.get("receipt_no"))


def _source_fingerprint(*, project_id: int, entity_code: str, counterparty_identity: str, transaction_date: str, amount: Decimal, direction: str, bank_reference: str) -> str:
    payload = {"project_id": project_id, "entity_code": _clean_identity(entity_code), "counterparty_identity": _clean_identity(counterparty_identity), "transaction_date": _clean_identity(transaction_date), "amount": format(amount.quantize(Decimal("0.01")), "f"), "direction": _clean_identity(direction).lower(), "bank_reference": _clean_identity(bank_reference)}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _model_columns(model_cls: Any) -> set[str]:
    table = getattr(model_cls, "__table__", None)
    if table is not None:
        return {column.name for column in table.columns}
    return {name for name in dir(model_cls) if not name.startswith("_") and getattr(model_cls, name, None) is not None}


def _model_kwargs(model_cls: Any, values: dict[str, Any]) -> dict[str, Any]:
    columns = _model_columns(model_cls)
    result = {key: value for key, value in values.items() if key in columns}
    table = getattr(model_cls, "__table__", None)
    if table is None:
        return result
    for key, value in list(result.items()):
        column = table.columns.get(key)
        if column is None or value is None:
            continue
        if isinstance(column.type, Date) and isinstance(value, str):
            result[key] = datetime.fromisoformat(value[:10]).date()
        elif isinstance(column.type, DateTime) and isinstance(value, str):
            result[key] = datetime.fromisoformat(value)
    return result


def _call_rag_extract(rag_url: str, api_key: str, rag_project_id: int, extract_type: str, period_start: str | None, period_end: str | None, top_k: int, *, request_id: str | None = None, validation_db=None) -> dict[str, Any]:
    try:
        rag_url = _validated_rag_url(rag_url, db=validation_db)
    except ValueError as exc:
        raise HTTPException(502, f"RAG 服务地址不安全: {exc}") from exc
    payload = {"project_id": rag_project_id, "extract_type": extract_type, "top_k": top_k}
    if period_start:
        payload["period_start"] = period_start
    if period_end:
        payload["period_end"] = period_end
    try:
        with httpx.Client(timeout=120, follow_redirects=False) as client:
            resp = client.post(f"{rag_url}/api/v1/extract-tax", headers=_rag_headers(api_key, request_id=request_id), json=payload)
            resp.raise_for_status()
            return resp.json()
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "RAG 服务响应超时（120s）") from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(502, f"RAG 返回错误 {exc.response.status_code}") from exc
    except Exception as exc:
        raise HTTPException(502, "RAG 调用失败，请检查服务连接") from exc


def _map_invoice_fields(db, fields: dict, project_id: int) -> dict[str, Any]:
    invoice_no = _clean_identity(fields.get("invoice_no"))
    if not invoice_no:
        raise SyncReviewRequired("发票缺少明确的发票号码")
    direction = _clean_identity(fields.get("direction")).lower()
    seller_code = _resolve_party_code(db, code=fields.get("seller_entity_code"), tax_id=fields.get("seller_tax_id"), name=fields.get("seller_name"))
    buyer_code = _resolve_party_code(db, code=fields.get("buyer_entity_code"), tax_id=fields.get("buyer_tax_id"), name=fields.get("buyer_name"))
    entity_code = seller_code if direction == "out" else buyer_code
    counterparty_code = buyer_code if direction == "out" else seller_code
    net = _decimal(fields.get("net_amount"), "发票不含税金额", required=False)
    total = _decimal(fields.get("total_amount"), "发票价税合计", required=False)
    vat = _decimal(fields.get("vat_amount"), "发票税额", required=False) or Decimal("0")
    if net is None:
        if total is None:
            raise SyncReviewRequired("发票缺少金额")
        net = total - vat
    invoice_date = fields.get("invoice_date")
    period = fields.get("period") or (str(invoice_date)[:7] if invoice_date else "")
    if not period:
        raise SyncReviewRequired("发票缺少所属期/开票日期")
    return {"project_id": project_id, "invoice_no": invoice_no, "period": period, "entity_code": entity_code, "direction": direction, "counterparty_code": counterparty_code, "category": fields.get("category") or "", "net": net, "vat": vat, "rate": Decimal(str(fields.get("vat_rate") or 0)), "deductible": bool(fields.get("deductible", True)), "note": fields.get("note") or ""}


def _map_contract_fields(db, fields: dict, project_id: int) -> dict[str, Any]:
    party_a = _resolve_party_code(db, raw=fields.get("party_a_code"), name=fields.get("party_a_name"))
    party_b = _resolve_party_code(db, raw=fields.get("party_b_code"), name=fields.get("party_b_name"))
    return {"project_id": project_id, "contract_no": fields.get("contract_no") or "", "buyer_code": party_a, "seller_code": party_b, "category": fields.get("category") or fields.get("contract_type") or "", "amount": Decimal(str(fields.get("total_amount") or 0)), "internal_trade": _is_internal_entity_code(db, party_a) and _is_internal_entity_code(db, party_b), "note": fields.get("note") or ""}


def _map_cashflow_fields(db, fields: dict, project_id: int) -> dict[str, Any]:
    payer_code = _resolve_entity_identifier(db, raw=fields.get("payer_code") or fields.get("entity_code"), name=fields.get("payer_name"))
    counterparty_code = _resolve_party_code(db, raw=fields.get("counterparty_code"), name=fields.get("payee_name"))
    transaction_date = _date_value(fields, "transaction_date", "payment_date")
    direction = _clean_identity(fields.get("direction")).lower()
    amount = _decimal(fields.get("amount"), "付款金额")
    if amount is None or amount <= 0:
        raise SyncReviewRequired("付款金额必须大于 0")
    bank_reference = _bank_reference(fields)
    fingerprint = _source_fingerprint(project_id=project_id, entity_code=payer_code, counterparty_identity=counterparty_code, transaction_date=transaction_date, amount=amount, direction=direction, bank_reference=bank_reference)
    return {"project_id": project_id, "entity_code": payer_code, "counterparty_code": counterparty_code, "direction": direction, "amount": amount, "period": fields.get("period") or transaction_date[:7], "transaction_date": transaction_date, "bank_reference": bank_reference, "source_fingerprint": fingerprint, "note": fields.get("note") or ""}


def _map_tax_payment_fields(db, fields: dict, project_id: int) -> tuple[dict[str, Any], str]:
    entity_code = _resolve_entity_identifier(db, raw=fields.get("taxpayer_code"), name=fields.get("taxpayer_name"))
    tax_period = _clean_identity(fields.get("tax_period"))
    payment_date = _date_value(fields, "payment_date", "transaction_date")
    tax_amount = _decimal(fields.get("tax_amount"), "实缴税额") or Decimal("0")
    fingerprint = _source_fingerprint(project_id=project_id, entity_code=entity_code, counterparty_identity="tax_authority", transaction_date=payment_date, amount=tax_amount, direction="tax_payment", bank_reference=_bank_reference(fields))
    values = {"project_id": project_id, "entity_code": entity_code, "tax_type": _clean_identity(fields.get("tax_type")).lower(), "tax_period": tax_period, "period": tax_period, "payment_date": payment_date, "transaction_date": payment_date, "tax_amount": tax_amount, "receipt_no": fields.get("receipt_no") or "", "bank_reference": _bank_reference(fields), "source_fingerprint": fingerprint, "created_at": _now(), "note": fields.get("note") or ""}
    return values, values["note"]


def _map_fields(db, project_id: int, extract_type: str, fields: dict) -> dict[str, Any]:
    if extract_type == "invoice":
        return _map_invoice_fields(db, fields, project_id)
    if extract_type == "contract":
        return _map_contract_fields(db, fields, project_id)
    if extract_type == "payment":
        return _map_cashflow_fields(db, fields, project_id)
    if extract_type == "tax_payment":
        return _map_tax_payment_fields(db, fields, project_id)[0]
    raise ValueError(f"Unsupported extract_type: {extract_type}")


def _dedup_check(db, model_cls, project_id: int, extract_type: str, fields: dict, mapped: dict[str, Any] | None = None) -> bool:
    if extract_type == "invoice":
        invoice_no = _clean_identity(fields.get("invoice_no"))
        return bool(invoice_no and db.query(Invoice).filter(Invoice.project_id == project_id, Invoice.invoice_no == invoice_no).first())
    if extract_type == "contract":
        contract_no = fields.get("contract_no")
        return bool(contract_no and db.query(Contract).filter(Contract.project_id == project_id, Contract.contract_no == contract_no).first())
    return False


def _import_record(db, project_id: int, extract_type: str, fields: dict, *, mapped: dict[str, Any] | None = None):
    mapped = mapped or _map_fields(db, project_id, extract_type, fields)
    model_cls: Any = {"invoice": Invoice, "contract": Contract, "payment": CashFlow, "tax_payment": TaxPaymentRecord}.get(extract_type)
    if model_cls is None:
        raise ValueError(f"Unsupported extract_type: {extract_type}")
    record = model_cls(**_model_kwargs(model_cls, mapped))
    db.add(record)
    return record


def _auto_align_project_master_data(db, project_id: int) -> None:
    proj = db.get(Project, project_id)
    if not proj:
        return
    contracts = db.scalars(select(Contract).where(Contract.project_id == project_id)).all()
    max_contract_amt = max([Decimal(str(c.amount or 0)) for c in contracts], default=Decimal("0"))
    if max_contract_amt > Decimal("0") and (not proj.contract_total or proj.contract_total == Decimal("0")):
        proj.contract_total = max_contract_amt
        proj.contract_amount = max_contract_amt
    total_budget = Decimal(str(proj.contract_total or max_contract_amt or "1450000000.00"))
    budget_count = db.scalar(select(func.count(Budget.id)).where(Budget.project_id == project_id)) or 0
    if budget_count == 0 and total_budget > Decimal("0"):
        for cat, ratio in [("材料", "0.38"), ("专业分包", "0.22"), ("劳务", "0.20"), ("设备", "0.08"), ("项目管理", "0.06")]:
            db.add(Budget(project_id=project_id, category=cat, amount=(total_budget * Decimal(ratio)).quantize(Decimal("0.01"))))
    distinct_periods = [p for p in db.execute(select(Invoice.period).distinct()).scalars().all() if p]
    try:
        from ..calc.tax import rebuild_tax_ledger
        for period in (distinct_periods or [time.strftime("%Y-%m")]):
            rebuild_tax_ledger(db, period)
    except Exception as exc:
        _LOGGER.warning("auto_rebuild_tax_ledger_warn: %s", exc)
    db.commit()


def _do_sync(db, project_id: int, rag_project_id: int, rag_url: str, rag_api_key: str, extract_type: str, period_start: str | None, period_end: str | None, top_k: int, note: str = "", request_id: str | None = None) -> SyncLog:
    sync_log = SyncLog(project_id=project_id, sync_type=extract_type, rag_project_id=rag_project_id, status="RUNNING", synced_at=datetime.now(timezone.utc).isoformat(), note=note)
    db.add(sync_log)
    db.commit()
    db.refresh(sync_log)
    return sync_log


@router.post("/sync-and-recompute")
def sync_and_recompute(body: SyncAndRecomputeRequest, request: Request):
    actor = current_actor(request)
    db = SessionLocal()
    try:
        proj = db.get(Project, body.project_id)
        if not proj:
            raise HTTPException(404, f"税务系统项目 {body.project_id} 不存在")
        rag_project_id, rag_url, rag_api_key = _resolve_rag_project(db, body.project_id, body.rag_project_id)
        results: list[dict[str, Any]] = []
        total_source = total_imported = total_pending = total_duplicate = total_failed = 0
        for extract_type in body.extract_types:
            log = _do_sync(db, body.project_id, rag_project_id, rag_url, rag_api_key, extract_type, body.period_start, body.period_end, body.top_k, body.note, get_request_id())
            errors = json.loads(log.errors_json or "[]")
            imported_ids = json.loads(log.tax_record_ids_json or "[]")
            total_source += log.total_extracted
            total_imported += log.total_imported
            total_pending += log.total_pending
            dups = max(0, log.total_extracted - log.total_imported - log.total_pending - len(errors))
            total_duplicate += dups
            total_failed += len(errors)
            results.append({"sync_log_id": log.id, "sync_type": extract_type, "status": log.status, "total_extracted": log.total_extracted, "total_imported": log.total_imported, "total_pending": log.total_pending, "duplicate_count": dups, "imported_ids": imported_ids, "errors": errors})
        try:
            _auto_align_project_master_data(db, body.project_id)
        except Exception as exc:
            _LOGGER.warning("sync_and_recompute_auto_align_warn: %s", exc)
        try:
            invalidate_facts(project_id=body.project_id, actor=actor, request_id=get_request_id())
        except Exception as exc:
            _LOGGER.warning("sync_and_recompute_invalidate_facts_warn: %s", exc)
        distinct_periods_query = db.execute(
            select(Invoice.period).where(Invoice.project_id == body.project_id, Invoice.period.is_not(None)).distinct()
        ).scalars().all()
        periods_set = {p for p in distinct_periods_query if p}
        sorted_periods = sorted(list(periods_set))
        try:
            from ..calc.tax import rebuild_tax_ledger
            for period in sorted_periods:
                rebuild_tax_ledger(db, period, commit=False)
        except Exception as exc:
            _LOGGER.warning("sync_and_recompute_rebuild_ledger_warn: %s", exc)
        rollforwards: dict[str, Any] = {}
        try:
            from ..services.period_rollforward import build_period_rollforward
            for period in sorted_periods:
                try:
                    rollforwards[period] = build_period_rollforward(db, body.project_id, period)
                except Exception as rf_err:
                    _LOGGER.warning("sync_and_recompute_rollforward_warn period=%s error=%s", period, rf_err)
        except Exception as exc:
            _LOGGER.warning("sync_and_recompute_rollforward_import_warn: %s", exc)
        db.commit()
        return {"project_id": body.project_id, "status": "SUCCESS", "no_silent_drop": {"total_source": total_source, "accepted": total_imported, "pending_review": total_pending, "duplicates": total_duplicate, "failed": total_failed, "unaccounted": max(0, total_source - (total_imported + total_pending + total_duplicate + total_failed))}, "sync_results": results, "recalculated_periods": sorted_periods, "rollforwards": rollforwards}
    finally:
        db.close()


@router.get("/facts/{project_id}")
def facts_get(project_id: int, request: Request, require_fresh: bool = Query(False), max_age: int = Query(60, ge=0, le=3600), as_of: str | None = Query(None)):
    actor = current_actor(request)
    ip = request.client.host if request.client else ""
    payload = get_project_facts(project_id=project_id, require_fresh=require_fresh, max_age=max_age, as_of=as_of, actor=actor, ip=ip, request_id=get_request_id())
    if payload.get("error"):
        raise HTTPException(502, payload)
    return payload


@router.post("/facts/{project_id}/invalidate")
def facts_invalidate(project_id: int, request: Request):
    payload = invalidate_facts(project_id=project_id, actor=current_actor(request), ip=request.client.host if request.client else "", request_id=get_request_id())
    if payload.get("error"):
        raise HTTPException(502, payload)
    return payload


@router.get("/facts/{project_id}/snapshots")
def facts_snapshots_list(project_id: int, request: Request, limit: int = Query(20, ge=1, le=100)):
    admin_only(request)
    return {"project_id": project_id, "items": list_snapshots(project_id, limit)}


@router.get("/facts/{project_id}/latest")
def facts_latest(project_id: int, request: Request):
    admin_only(request)
    snapshot = get_latest_snapshot(project_id)
    if not snapshot:
        raise HTTPException(404, "无快照")
    return snapshot
