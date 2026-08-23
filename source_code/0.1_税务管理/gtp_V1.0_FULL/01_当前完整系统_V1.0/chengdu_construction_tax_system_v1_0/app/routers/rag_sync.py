"""V0.2: RAG 税务数据同步路由。

从 ProjectRAG 知识库 AI 抽取发票、合同、付款、完税凭证数据，
经置信度分流后自动入库或存入待确认表。
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx
from fastapi import APIRouter, Form, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import Date, DateTime

from .. import config
from ..audit import current_actor
from ..db import SessionLocal
from ..models import (
    CashFlow,
    Contract,
    Entity,
    Invoice,
    Project,
    ProjectRAGMap,
    SyncLog,
    SyncPending,
)
from ..observability import get_request_id
from ..services.facts_client import (
    get_latest_snapshot,
    get_project_facts,
    invalidate_facts,
    list_snapshots,
)
from ..security import validate_rag_service_url

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
    url: str = Field(default="", max_length=300, description="RAG 服务地址；留空使用服务配置")
    api_key: str = Field(default="", max_length=512, description="RAG API 密钥（可选）")


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


def _rag_headers(api_key: str = "", *, request_id: str | None = None) -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    key = api_key or RAG_SHARED_KEY
    if key:
        h["Authorization"] = f"Bearer {key}"
    rid = (request_id or get_request_id() or "").strip()
    if rid:
        h["X-Request-ID"] = rid
    return h


def _validated_rag_url(raw_url: str) -> str:
    """Validate a configured or database-backed RAG URL before use."""
    normalized = str(raw_url or "").strip().rstrip("/")
    if not normalized:
        raise ValueError("RAG 服务地址不能为空")
    # The shipped development default is a loopback HTTP service.  It is
    # allowed only when it is exactly the configured global endpoint and the
    # process is not production/staging; arbitrary user-supplied loopback
    # addresses remain blocked.
    allow_loopback_http = normalized == RAG_URL and (
        os.getenv("APP_ENV", "development").strip().lower()
        not in {"prod", "production", "staging"}
    )
    return validate_rag_service_url(
        normalized,
        allow_loopback_http=allow_loopback_http,
    )


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
    configured_url = (mapping.rag_url if mapping and mapping.rag_url else RAG_URL).rstrip("/")
    try:
        resolved_url = _validated_rag_url(configured_url)
    except ValueError as exc:
        raise HTTPException(400, f"RAG 服务地址不安全: {exc}") from exc
    # Project mappings must not become a secret store.  Legacy values may
    # still exist in old rows, but are deliberately ignored; new credentials
    # must be injected through RAG_SHARED_API_KEY.
    resolved_key = RAG_SHARED_KEY

    return resolved_id, resolved_url, resolved_key


def _resolve_entity_code(
    db,
    code: str | None = None,
    name: str | None = None,
    tax_id: str | None = None,
) -> str:
    """Resolve a real, active legal entity and return its canonical code.

    ``A``/``B``/``C``/``D`` and ``甲``/``乙``/``丙``/``丁`` are historical demo
    labels, never valid entity identifiers.  Every supplied identifier is
    checked independently.  If two identifiers point at different rows, or
    one identifier is ambiguous/missing, the item is sent to human review;
    SQLAlchemy's ``first()`` is intentionally not used here.

    A non-legal branch is resolved through ``parent_entity_code``.  The parent
    is resolved again so an inactive/missing parent cannot silently become a
    posting target.
    """
    identifiers = {
        "code": _clean_identity(code),
        "tax_id": _clean_identity(tax_id),
        "name": _clean_identity(name),
    }
    for label, value in identifiers.items():
        if value and _is_virtual_identity(value):
            raise SyncReviewRequired(f"{label} 使用了已禁用的虚拟主体标识 {value!r}")

    found: dict[int, Any] = {}
    for label, value in identifiers.items():
        if not value:
            continue
        rows = _entity_matches(db, label, value)
        if len(rows) > 1:
            raise SyncReviewRequired(f"{label}={value!r} 匹配到多个主体，无法自动入账")
        if not rows:
            raise SyncReviewRequired(f"{label}={value!r} 未识别到唯一实际主体")
        entity = rows[0]
        found[id(entity)] = entity

    if not found:
        raise SyncReviewRequired("未提供可用于识别实际主体的 code、tax_id 或 name")

    canonical_codes = {
        _canonical_entity_code(db, entity)
        for entity in found.values()
    }
    if len(canonical_codes) != 1:
        raise SyncReviewRequired(
            "code、tax_id、name 分别指向不同主体，存在身份冲突"
        )
    resolved = next(iter(canonical_codes))
    if _is_virtual_identity(resolved):
        raise SyncReviewRequired(f"解析结果 {resolved!r} 是已禁用的虚拟主体")
    return resolved


def _clean_identity(value: Any) -> str:
    """Normalize an extracted identity without changing its business value."""
    if value is None:
        return ""
    return " ".join(str(value).strip().split())


def _is_virtual_identity(value: str | None) -> bool:
    normalized = _clean_identity(value)
    return normalized.upper() in {"A", "B", "C", "D"} or normalized in {
        "甲", "乙", "丙", "丁",
    }


def _entity_matches(db, label: str, value: str) -> list[Any]:
    """Return all active entity matches for one identity field."""
    # ``tax_id`` is the tax identifier in the extraction contract.  A few old
    # payloads called the real entity code ``*_code``; callers pass it through
    # ``code`` when that distinction is known.  We do not fuzzy-match names:
    # partial names are not a safe posting key.
    fields = {
        "code": ("code",),
        "tax_id": ("tax_id", "code"),
        "name": ("name", "short_name"),
    }[label]
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
    return list(rows.values())


def _canonical_entity_code(db, entity: Any, seen: set[str] | None = None) -> str:
    """Return a legal parent's code for a branch, rejecting bad master data."""
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
        if _is_virtual_identity(parent_code):
            raise SyncReviewRequired(f"分公司 {code} 的父主体是虚拟标识 {parent_code!r}")
        rows = _entity_matches(db, "code", parent_code)
        if len(rows) != 1:
            raise SyncReviewRequired(
                f"分公司 {code} 的父主体 {parent_code!r} 未唯一匹配"
            )
        return _canonical_entity_code(db, rows[0], seen)
    return code


def _resolve_entity_identifier(
    db,
    raw: Any = None,
    *,
    name: Any = None,
    code: Any = None,
    tax_id: Any = None,
) -> str:
    """Resolve fields whose upstream schema calls a tax id a ``*_code``.

    ``raw`` is tried as both code and tax id, with conflicts detected by the
    common resolver.  This preserves compatibility with existing RAG payloads
    while never treating a bank account as a tax id.
    """
    if raw is not None and not code and not tax_id:
        # ``*_code`` in the RAG extraction schema means taxpayer identifier,
        # while a few integrations send the canonical entity code there.  The
        # tax_id matcher deliberately checks both tax_id and code, so this
        # single selector supports both forms without treating a missing code
        # match as an error.
        tax_id = raw
    return _resolve_entity_code(db, code=code, tax_id=tax_id, name=name)


def _external_party_matches(db, label: str, value: str) -> list[Any]:
    if ExternalParty is None:
        return []
    fields = {
        "code": ("code",),
        "tax_id": ("tax_id", "code"),
        "name": ("name", "short_name"),
    }[label]
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


def _resolve_external_party_code(
    db,
    raw: Any = None,
    *,
    name: Any = None,
    code: Any = None,
    tax_id: Any = None,
) -> str:
    """Resolve an external counterparty from its own master, never as a demo entity."""
    if raw is not None and not code and not tax_id:
        tax_id = raw
    identifiers = {
        "code": _clean_identity(code),
        "tax_id": _clean_identity(tax_id),
        "name": _clean_identity(name),
    }
    for label, value in identifiers.items():
        if value and _is_virtual_identity(value):
            raise SyncReviewRequired(f"{label} 使用了已禁用的虚拟交易方标识 {value!r}")
    found: dict[int, Any] = {}
    for label, value in identifiers.items():
        if not value:
            continue
        rows = _external_party_matches(db, label, value)
        if len(rows) > 1:
            raise SyncReviewRequired(f"外部交易方 {label}={value!r} 匹配不唯一")
        if not rows:
            raise SyncReviewRequired(f"外部交易方 {label}={value!r} 未登记")
        found[id(rows[0])] = rows[0]
    if not found:
        raise SyncReviewRequired("未提供可识别的外部交易方身份")
    codes = {_clean_identity(getattr(party, "code", "")) for party in found.values()}
    if len(codes) != 1 or not next(iter(codes), ""):
        raise SyncReviewRequired("外部交易方身份字段相互冲突")
    resolved = next(iter(codes))
    if _is_virtual_identity(resolved):
        raise SyncReviewRequired(f"外部交易方解析为虚拟标识 {resolved!r}")
    return resolved


def _resolve_party_code(
    db,
    raw: Any = None,
    *,
    name: Any = None,
    code: Any = None,
    tax_id: Any = None,
) -> str:
    """Resolve either a real entity or a registered external party."""
    try:
        return _resolve_entity_identifier(
            db, raw=raw, name=name, code=code, tax_id=tax_id,
        )
    except SyncReviewRequired as entity_error:
        message = str(entity_error)
        # Ambiguous/conflicting entity identity must not be hidden by an
        # unrelated external match.  Only an absent entity may fall through.
        if any(marker in message for marker in ("多个", "冲突", "虚拟", "父主体")):
            raise
        return _resolve_external_party_code(
            db, raw=raw, name=name, code=code, tax_id=tax_id,
        )


def _is_internal_entity_code(db, code: str) -> bool:
    column = getattr(Entity, "code", None)
    if column is None:
        return False
    query = db.query(Entity).filter(column == code)
    active_column = getattr(Entity, "active", None)
    if active_column is not None:
        query = query.filter(active_column.is_(True))
    return bool(query.all())


def _resolve_bank_account(db, account: Any) -> str:
    """Resolve a payer account only through the bank-account master mapping."""
    account_value = _clean_identity(account)
    if not account_value:
        raise SyncReviewRequired("付款凭证缺少付款方银行账号")
    if _is_virtual_identity(account_value):
        raise SyncReviewRequired("付款方银行账号不能使用虚拟主体标识")
    if EntityBankAccount is None:
        raise SyncReviewRequired("银行账号主数据尚未迁移，无法安全识别付款主体")

    account_column = next(
        (
            getattr(EntityBankAccount, field, None)
            for field in ("account_number", "account_no", "bank_account", "account")
            if getattr(EntityBankAccount, field, None) is not None
        ),
        None,
    )
    if account_column is None:
        raise SyncReviewRequired("银行账号主数据缺少 account_number 字段")
    query = db.query(EntityBankAccount).filter(account_column == account_value)
    active_column = getattr(EntityBankAccount, "active", None)
    if active_column is not None:
        query = query.filter(active_column.is_(True))
    rows = query.all()
    if not rows:
        raise SyncReviewRequired(f"付款方银行账号 {account_value!r} 未在账号主数据中登记")

    entity_values: list[str] = []
    for row in rows:
        entity_code = _clean_identity(
            getattr(row, "entity_code", None)
            or getattr(row, "owner_entity_code", None)
            or getattr(row, "entity", None)
        )
        if not entity_code and getattr(row, "entity_id", None) is not None:
            entity = db.get(Entity, row.entity_id)
            entity_code = _clean_identity(getattr(entity, "code", ""))
        if entity_code:
            entity_values.append(entity_code)
    if not entity_values:
        raise SyncReviewRequired(f"银行账号 {account_value!r} 缺少归属主体")
    if len(set(entity_values)) != 1:
        raise SyncReviewRequired(f"银行账号 {account_value!r} 对应多个主体")
    return _resolve_entity_identifier(db, raw=entity_values[0])


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
    value = _clean_identity(raw)
    # RAG normally emits YYYY-MM-DD.  Accept an ISO datetime while preserving
    # a canonical date for fingerprints and Date columns.
    value = value[:10]
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise SyncReviewRequired(f"日期 {value!r} 格式无效") from exc
    return value


def _bank_reference(fields: dict) -> str:
    return _clean_identity(
        fields.get("bank_reference")
        or fields.get("bank_ref")
        or fields.get("transaction_reference")
        or fields.get("reference")
        or fields.get("receipt_no")
    )


def _source_fingerprint(
    *,
    project_id: int,
    entity_code: str,
    counterparty_identity: str,
    transaction_date: str,
    amount: Decimal,
    direction: str,
    bank_reference: str,
) -> str:
    """Build a stable, content-addressed source key for one financial event."""
    payload = {
        "project_id": project_id,
        "entity_code": _clean_identity(entity_code),
        "counterparty_identity": _clean_identity(counterparty_identity),
        "transaction_date": _clean_identity(transaction_date),
        "amount": format(amount.quantize(Decimal("0.01")), "f"),
        "direction": _clean_identity(direction).lower(),
        "bank_reference": _clean_identity(bank_reference),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _model_columns(model_cls: Any) -> set[str]:
    table = getattr(model_cls, "__table__", None)
    if table is not None:
        return {column.name for column in table.columns}
    return {
        name
        for name in dir(model_cls)
        if not name.startswith("_") and getattr(model_cls, name, None) is not None
    }


def _model_kwargs(model_cls: Any, values: dict[str, Any]) -> dict[str, Any]:
    """Filter aliases to fields present in the current migration's model."""
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


def _call_rag_extract(
    rag_url: str,
    api_key: str,
    rag_project_id: int,
    extract_type: str,
    period_start: str | None,
    period_end: str | None,
    top_k: int,
    *,
    request_id: str | None = None,
) -> dict[str, Any]:
    """调用 RAG /api/v1/extract-tax 接口。"""
    try:
        rag_url = _validated_rag_url(rag_url)
    except ValueError as exc:
        raise HTTPException(502, f"RAG 服务地址不安全: {exc}") from exc
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
        with httpx.Client(timeout=120, follow_redirects=False) as client:
            resp = client.post(
                url,
                headers=_rag_headers(api_key, request_id=request_id),
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "RAG 服务响应超时（120s）") from exc
    except httpx.HTTPStatusError as e:
        raise HTTPException(502, f"RAG 返回错误 {e.response.status_code}: {e.response.text[:200]}") from e
    except Exception as e:
        raise HTTPException(502, f"RAG 调用失败: {e}") from e


def _map_invoice_fields(db, fields: dict, project_id: int) -> dict[str, Any]:
    """将 RAG 抽取的发票字段映射为税务系统 Invoice 模型字段。"""
    direction = _clean_identity(fields.get("direction")).lower()
    if direction not in {"", "in", "out"}:
        raise SyncReviewRequired(f"发票 direction={direction!r} 无效")
    # The entity owning the tax event is the buyer for input invoices and the
    # seller for output invoices.  Counterparties are retained separately.
    if direction == "out":
        seller_code = _resolve_entity_identifier(
            db, raw=fields.get("seller_code"), name=fields.get("seller_name"),
        )
        buyer_code = _resolve_party_code(
            db, raw=fields.get("buyer_code"), name=fields.get("buyer_name"),
        )
        entity_code, counterparty_code = seller_code, buyer_code
    else:
        buyer_code = _resolve_entity_identifier(
            db, raw=fields.get("buyer_code"), name=fields.get("buyer_name"),
        )
        seller_code = _resolve_party_code(
            db, raw=fields.get("seller_code"), name=fields.get("seller_name"),
        )
        entity_code, counterparty_code = buyer_code, seller_code

    explicit_net = _decimal(fields.get("net_amount"), "发票不含税金额", required=False)
    total = _decimal(fields.get("total_amount"), "发票价税合计", required=False)
    vat = _decimal(fields.get("vat_amount"), "发票税额", required=False)
    if explicit_net is not None:
        net = explicit_net
        if total is not None and vat is not None:
            calculated = total - vat
            if abs(net - calculated) >= MONEY_TOLERANCE:
                raise SyncReviewRequired(
                    f"发票不含税金额与价税合计-税额不一致: {net} != {calculated}"
                )
    else:
        if total is None or vat is None:
            raise SyncReviewRequired("发票缺少不含税金额，且无法由价税合计-税额计算")
        net = total - vat
    if net < 0:
        raise SyncReviewRequired("发票不含税金额不能为负数")
    vat = vat or Decimal("0")

    invoice_date = fields.get("invoice_date")
    period = fields.get("period") or (str(invoice_date)[:7] if invoice_date else "")
    if not period:
        raise SyncReviewRequired("发票缺少所属期/开票日期")

    return {
        "project_id": project_id,
        "invoice_no": fields.get("invoice_no") or "",
        "period": period,
        "entity_code": entity_code,
        "direction": direction,
        "counterparty_code": counterparty_code,
        "category": fields.get("category") or "",
        "net": net,
        "vat": vat,
        "rate": Decimal(str(fields.get("vat_rate") or 0)),
        "deductible": bool(fields.get("deductible", True)),
        "note": fields.get("note") or "",
    }


def _map_contract_fields(db, fields: dict, project_id: int) -> dict[str, Any]:
    """将 RAG 抽取的合同字段映射为税务系统 Contract 模型字段。"""
    party_a = _resolve_party_code(
        db,
        raw=fields.get("party_a_code"),
        name=fields.get("party_a_name"),
    )
    party_b = _resolve_party_code(
        db,
        raw=fields.get("party_b_code"),
        name=fields.get("party_b_name"),
    )
    internal_trade = _is_internal_entity_code(db, party_a) and _is_internal_entity_code(db, party_b)

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
    # A payer account is a bank-account key only.  It is never passed to the
    # tax-id resolver (the previous implementation did exactly that).
    explicit_entity = ""
    payer_code = fields.get("payer_code") or fields.get("entity_code")
    payer_tax_id = fields.get("payer_tax_id")
    payer_name = fields.get("payer_name")
    if payer_code or payer_tax_id or payer_name:
        if payer_tax_id:
            explicit_entity = _resolve_entity_identifier(
                db, code=payer_code, tax_id=payer_tax_id, name=payer_name,
            )
        else:
            explicit_entity = _resolve_entity_identifier(
                db, raw=payer_code, name=payer_name,
            )
    account_entity = ""
    if fields.get("payer_account"):
        account_entity = _resolve_bank_account(db, fields.get("payer_account"))
    elif not explicit_entity:
        raise SyncReviewRequired("付款凭证缺少可识别的付款主体")
    if account_entity and explicit_entity and explicit_entity != account_entity:
        raise SyncReviewRequired("付款方名称/code 与付款方银行账号归属主体冲突")
    payer_code = explicit_entity or account_entity

    counterparty_code = _resolve_party_code(
        db,
        raw=fields.get("counterparty_code"),
        name=fields.get("payee_name"),
    )

    transaction_date = _date_value(fields, "transaction_date", "payment_date")
    direction = _clean_identity(fields.get("direction")).lower()
    if direction not in {"in", "out"}:
        raise SyncReviewRequired("付款 direction 必须是 in 或 out")
    amount = _decimal(fields.get("amount"), "付款金额")
    if amount is None or amount <= 0:
        raise SyncReviewRequired("付款金额必须大于 0")
    bank_reference = _bank_reference(fields)
    fingerprint = _source_fingerprint(
        project_id=project_id,
        entity_code=payer_code,
        counterparty_identity=counterparty_code,
        transaction_date=transaction_date,
        amount=amount,
        direction=direction,
        bank_reference=bank_reference,
    )
    period = fields.get("period") or transaction_date[:7]
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
        "direction": direction,
        "amount": amount,
        "period": period,
        "transaction_date": transaction_date,
        "bank_reference": bank_reference,
        "source_fingerprint": fingerprint,
        "note": " | ".join(note_parts),
    }


def _map_tax_payment_fields(db, fields: dict, project_id: int) -> tuple[dict[str, Any], str]:
    """Map a receipt to a TaxPaymentRecord, never to the monthly TaxLedger."""
    entity_code = _resolve_entity_identifier(
        db,
        raw=fields.get("taxpayer_code"),
        name=fields.get("taxpayer_name"),
    )
    tax_type = _clean_identity(fields.get("tax_type")).lower()
    if not tax_type:
        raise SyncReviewRequired("完税凭证缺少税种")
    tax_period = _clean_identity(fields.get("tax_period"))
    if not tax_period:
        raise SyncReviewRequired("完税凭证缺少税款所属期")
    payment_date = _date_value(fields, "payment_date", "transaction_date")
    tax_amount = _decimal(fields.get("tax_amount"), "实缴税额")
    if tax_amount is None or tax_amount < 0:
        raise SyncReviewRequired("实缴税额不能为负数")
    principal_amount = _decimal(fields.get("principal_amount"), "税款本金", required=False)
    penalty_amount = _decimal(fields.get("penalty_amount"), "滞纳金", required=False)
    note_parts = [
        f"完税凭证 {fields.get('receipt_no', '')}".strip(),
        f"税种 {tax_type or 'unknown'}".strip(),
        f"本金 {fields.get('principal_amount', '')}".strip(),
        f"滞纳金 {fields.get('penalty_amount', '')}".strip(),
    ]
    note_parts = [p for p in note_parts if not p.endswith(" ") or p.strip().count(" ") > 1]
    note = " | ".join(p for p in note_parts if p.strip() and not p.strip().endswith(" "))

    fingerprint = _source_fingerprint(
        project_id=project_id,
        entity_code=entity_code,
        counterparty_identity="tax_authority",
        transaction_date=payment_date,
        amount=tax_amount,
        direction="tax_payment",
        bank_reference=_bank_reference(fields),
    )
    record_kwargs: dict[str, Any] = {
        "project_id": project_id,
        "entity_code": entity_code,
        "tax_type": tax_type,
        "tax_period": tax_period,
        "period": tax_period,
        "payment_date": payment_date,
        "transaction_date": payment_date,
        "tax_amount": tax_amount,
        "principal_amount": principal_amount or Decimal("0"),
        "penalty_amount": penalty_amount or Decimal("0"),
        "receipt_no": fields.get("receipt_no") or "",
        "bank_reference": _bank_reference(fields),
        "source_fingerprint": fingerprint,
        "created_at": _now(),
        "note": note,
    }
    return record_kwargs, note


def _dedup_check(
    db,
    model_cls,
    project_id: int,
    extract_type: str,
    fields: dict,
    mapped: dict[str, Any] | None = None,
) -> bool:
    """Check a business/source key without dropping legitimate same-direction payments.

    ``model_cls`` is kept in the signature for compatibility with callers from
    the V0.2 route.  For payments the complete fingerprint is preferred.  The
    fallback compares every component of that fingerprint, so old rows without
    the new columns cannot cause all later ``out`` rows to be discarded.
    """
    if extract_type == "invoice":
        invoice_no = fields.get("invoice_no")
        if invoice_no:
            return db.query(Invoice).filter(
                Invoice.project_id == project_id,
                Invoice.invoice_no == invoice_no,
            ).first() is not None

    elif extract_type == "contract":
        contract_no = fields.get("contract_no")
        if contract_no:
            return db.query(Contract).filter(
                Contract.project_id == project_id,
                Contract.contract_no == contract_no,
            ).first() is not None

    elif extract_type == "payment":
        mapped = mapped or {}
        fingerprint = mapped.get("source_fingerprint")
        fingerprint_column = getattr(CashFlow, "source_fingerprint", None)
        if fingerprint and fingerprint_column is not None:
            return db.query(CashFlow).filter(
                CashFlow.project_id == project_id,
                fingerprint_column == fingerprint,
            ).first() is not None

        # Compatibility path for records created before source_fingerprint was
        # added.  All key dimensions are required; direction alone is never a
        # duplicate key.
        entity_code = mapped.get("entity_code")
        counterparty_code = mapped.get("counterparty_code")
        direction = mapped.get("direction")
        amount = mapped.get("amount")
        transaction_date = mapped.get("transaction_date")
        bank_reference = mapped.get("bank_reference")
        if not all((entity_code, counterparty_code, direction, amount, transaction_date)):
            return False
        query = db.query(CashFlow).filter(
            CashFlow.project_id == project_id,
            CashFlow.entity_code == entity_code,
            CashFlow.counterparty_code == counterparty_code,
            CashFlow.direction == direction,
            CashFlow.amount == amount,
        )
        transaction_column = getattr(CashFlow, "transaction_date", None)
        reference_column = getattr(CashFlow, "bank_reference", None)
        if transaction_column is not None:
            query = query.filter(transaction_column == transaction_date)
        if reference_column is not None:
            query = query.filter(reference_column == bank_reference)
        return query.first() is not None

    elif extract_type == "tax_payment" and TaxPaymentRecord is not None:
        mapped = mapped or {}
        fingerprint = mapped.get("source_fingerprint")
        fingerprint_column = getattr(TaxPaymentRecord, "source_fingerprint", None)
        if fingerprint and fingerprint_column is not None:
            return db.query(TaxPaymentRecord).filter(
                fingerprint_column == fingerprint,
            ).first() is not None
        receipt_no = mapped.get("receipt_no")
        entity_code = mapped.get("entity_code")
        receipt_column = getattr(TaxPaymentRecord, "receipt_no", None)
        entity_column = getattr(TaxPaymentRecord, "entity_code", None)
        if receipt_no and receipt_column is not None:
            query = db.query(TaxPaymentRecord).filter(receipt_column == receipt_no)
            if entity_code and entity_column is not None:
                query = query.filter(entity_column == entity_code)
            return query.first() is not None

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
    request_id: str | None = None,
) -> SyncResponse:
    """执行一次同步，返回同步结果。"""
    # 1. 调用 RAG 抽取
    rag_data = _call_rag_extract(
        rag_url, rag_api_key, rag_project_id, extract_type,
        period_start, period_end, top_k, request_id=request_id,
    )

    extracted_items = rag_data.get("extracted_items") or []
    errors = rag_data.get("errors") or []

    # 2. 创建同步记录
    sync_log = SyncLog(
        project_id=project_id,
        sync_type=extract_type,
        rag_project_id=rag_project_id,
        rag_chunk_ids_json=json.dumps([i.get("source_chunk_id", 0) for i in extracted_items]),
        rag_document_ids_json=json.dumps(list({i.get("source_document_id", 0) for i in extracted_items})),
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
    duplicate_count = 0
    failed_count = 0

    def _pending_item(item: dict, fields: dict, confidence: Decimal, reason: str) -> None:
        """Persist a review item in its own SAVEPOINT."""
        pending_fields = dict(fields)
        pending_fields["_review_reason"] = reason
        with db.begin_nested():
            pending = SyncPending(
                sync_log_id=sync_log.id,
                project_id=project_id,
                sync_type=extract_type,
                source_chunk_id=item.get("source_chunk_id", 0),
                source_document_id=item.get("source_document_id", 0),
                filename=item.get("filename") or "",
                page_start=item.get("page_start"),
                confidence=confidence,
                fields_json=json.dumps(pending_fields, ensure_ascii=False),
                status="pending",
                note=reason,
            )
            db.add(pending)
            db.flush()
            pending_ids.append(pending.id)

    # 3. 按置信度分流
    for item in extracted_items:
        fields = item.get("fields") or {}
        try:
            confidence = Decimal(str(item.get("confidence", 0)))
        except Exception:
            confidence = Decimal("0")
        if confidence < 0 or confidence > 1:
            confidence = Decimal("0")

        try:
            # Mapping is read-only.  Do it before the SAVEPOINT so a review
            # decision can preserve a useful reason, then put the write itself
            # (including its flush) in a per-item nested transaction.
            mapped = _map_fields(db, project_id, extract_type, fields)
            if _dedup_check(db, None, project_id, extract_type, fields, mapped):
                duplicate_count += 1
                continue
            if confidence < AUTO_CONF_THRESHOLD:
                _pending_item(item, fields, confidence, "抽取置信度不足，需人工确认")
                continue
            with db.begin_nested():
                record = _import_record(
                    db, project_id, extract_type, fields, mapped=mapped,
                )
                db.flush()
                imported_ids.append(record.id)
        except SyncReviewRequired as exc:
            try:
                _pending_item(item, fields, confidence, exc.reason)
            except Exception as pending_exc:
                failed_count += 1
                errors.append(
                    f"待确认记录写入失败 chunk={item.get('source_chunk_id', 0)}: {pending_exc}"
                )
        except Exception as exc:
            # ``begin_nested`` rolls back only this item.  The outer sync log
            # and successful items remain valid for the next iterations.
            failed_count += 1
            errors.append(
                f"入库失败 chunk={item.get('source_chunk_id', 0)}: {exc}"
            )

    # 4. 更新同步记录
    if pending_ids and not errors:
        status = "PENDING_REVIEW"
    elif errors and (imported_ids or pending_ids):
        status = "PARTIAL"
    elif errors:
        status = "FAILED"
    else:
        # A batch consisting entirely of idempotent duplicates is a successful
        # no-op, not an error.
        status = "SUCCESS"
    sync_log.status = status
    sync_log.tax_record_ids_json = json.dumps(imported_ids)
    sync_log.total_imported = len(imported_ids)
    sync_log.total_pending = len(pending_ids)
    sync_log.errors_json = json.dumps(errors)
    db.commit()

    return SyncResponse(
        sync_log_id=sync_log.id,
        sync_type=extract_type,
        status=status,
        total_extracted=len(extracted_items),
        total_imported=len(imported_ids),
        total_pending=len(pending_ids),
        imported_ids=imported_ids,
        pending_ids=pending_ids,
        errors=errors,
    )


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


def _import_record(
    db,
    project_id: int,
    extract_type: str,
    fields: dict,
    *,
    mapped: dict[str, Any] | None = None,
):
    """Create one record using only fields supported by the active migration."""
    mapped = mapped or _map_fields(db, project_id, extract_type, fields)
    model_cls: Any
    if extract_type == "invoice":
        model_cls = Invoice
    elif extract_type == "contract":
        model_cls = Contract
    elif extract_type == "payment":
        model_cls = CashFlow
    elif extract_type == "tax_payment":
        model_cls = TaxPaymentRecord
        if model_cls is None:
            raise RuntimeError("TaxPaymentRecord 模型尚未迁移，拒绝写入 TaxLedger")
    else:
        raise ValueError(f"Unsupported extract_type: {extract_type}")
    record = model_cls(**_model_kwargs(model_cls, mapped))
    db.add(record)
    return record


# ============================================================
# API 路由
# ============================================================

@router.post("/connect", response_model=RagConnectResponse)
def rag_connect(body: RagConnectRequest):
    """配置并测试 RAG 服务连接。"""
    try:
        url = _validated_rag_url(body.url or RAG_URL)
    except ValueError as exc:
        return RagConnectResponse(ok=False, error=f"RAG 服务地址不安全: {exc}")
    headers = _rag_headers(request_id=get_request_id())
    if body.api_key:
        headers["Authorization"] = f"Bearer {body.api_key}"

    try:
        with httpx.Client(timeout=10, follow_redirects=False) as client:
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
    actor = current_actor(request)

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
            request_id=get_request_id(),
        )
    finally:
        db.close()


@router.post("/sync-batch")
def sync_batch(body: SyncBatchRequest, request: Request):
    """批量同步：按类型列表逐一同步。"""
    actor = current_actor(request)
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
                    request_id=get_request_id(),
                )
                results.append(result)
            except Exception as e:
                results.append(SyncResponse(
                    sync_log_id=0,
                    sync_type=extract_type,
                    status="FAILED",
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
    actor = current_actor(request)

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
def reject_pending(pending_id: int, request: Request, note: str = Form(default="")):
    """拒绝一条待确认记录（表单提交）。"""
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

@router.get("/facts/{project_id}")
def facts_get(
    project_id: int,
    request: Request,
    require_fresh: bool = Query(False, description="强制获取最新数据（AI 深度体检场景）"),
    max_age: int = Query(60, ge=0, le=3600, description="缓存最大有效期(秒)"),
    as_of: str | None = Query(None, description="查询历史 Snapshot 时间点 ISO 8601"),
):
    """从 RAG V1.0 Facts Provider 拉取项目指标。"""
    actor = current_actor(request)
    ip = request.client.host if request.client else ""
    payload = get_project_facts(
        project_id=project_id,
        require_fresh=require_fresh,
        max_age=max_age,
        as_of=as_of,
        actor=actor,
        ip=ip,
        request_id=get_request_id(),
    )
    if payload.get("error"):
        # ``status=DEGRADED`` is the explicit signal callers must check.
        # Returning HTTP 502 keeps the failure visible to API consumers.
        raise HTTPException(502, payload)
    return payload


@router.post("/facts/{project_id}/invalidate")
def facts_invalidate(project_id: int, request: Request):
    """业务数据变更后通知 RAG V1.0 失效项目 Facts 缓存。"""
    actor = current_actor(request)
    ip = request.client.host if request.client else ""
    payload = invalidate_facts(
        project_id=project_id,
        actor=actor,
        ip=ip,
        request_id=get_request_id(),
    )
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

    首次调用为注册，重复调用为更新。可覆盖经过校验的 RAG URL；
    API Key 统一由 RAG_SHARED_API_KEY 注入，不在项目映射中落库。
    """
    db = SessionLocal()
    try:
        proj = db.get(Project, body.project_id)
        if not proj:
            raise HTTPException(404, f"税务系统项目 {body.project_id} 不存在")

        if body.rag_api_key:
            raise HTTPException(
                400,
                "项目级 RAG API Key 不接受落库，请通过 RAG_SHARED_API_KEY 注入",
            )
        validated_url = ""
        if body.rag_url:
            try:
                validated_url = _validated_rag_url(body.rag_url)
            except ValueError as exc:
                raise HTTPException(400, f"RAG 服务地址不安全: {exc}") from exc

        mapping = db.query(ProjectRAGMap).filter(
            ProjectRAGMap.project_id == body.project_id,
        ).first()

        if mapping:
            mapping.rag_project_id = body.rag_project_id
            mapping.rag_project_code = body.rag_project_code or mapping.rag_project_code
            mapping.rag_url = validated_url or mapping.rag_url
            # Never persist a project-scoped credential.  Existing legacy
            # values remain unreadable to the resolver and are cleared on the
            # next explicit mapping update.
            mapping.rag_api_key = ""
            mapping.note = body.note or mapping.note
            mapping.synced_at = _now()
        else:
            mapping = ProjectRAGMap(
                project_id=body.project_id,
                rag_project_id=body.rag_project_id,
                rag_project_code=body.rag_project_code,
                rag_url=validated_url,
                rag_api_key="",
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
            "has_api_key": bool(RAG_SHARED_KEY),
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
            with httpx.Client(timeout=30, follow_redirects=False) as client:
                resp = client.post(
                    f"{rag_url}/api/v1/projects/sync",
                    headers=_rag_headers(rag_api_key, request_id=get_request_id()),
                    json=payload,
                )
                resp.raise_for_status()
                rag_resp = resp.json()
        except Exception as e:
            raise HTTPException(502, f"同步到 RAG 失败: {e}") from e

        mapping = db.query(ProjectRAGMap).filter(
            ProjectRAGMap.project_id == project_id,
        ).first()
        if not mapping:
            mapping = ProjectRAGMap(
                project_id=project_id,
                rag_project_id=rag_project_id_resolved,
                rag_project_code=rag_resp.get("project_code", proj.code),
                rag_url=rag_url,
                rag_api_key="",
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
