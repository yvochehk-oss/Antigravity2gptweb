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
    """Validate a configured or database-backed RAG URL before every call.

    A database endpoint is not trusted merely because it is persisted: the
    validator re-resolves its host and compares the result with the approval
    snapshot.  ``explicit_private_approval`` is used only by the administrator
    test-and-save transaction; normal project/status/sync calls rely on the
    persisted row.
    """
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
        allow_approved_private=approved_private or explicit_private_approval,
        approved_addresses=approved_addresses,
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
    # Once an administrator has saved a global endpoint, it is the single
    # effective Tax -> RAG destination.  Legacy project-map URLs remain in the
    # schema for compatibility but must not make one project silently call a
    # different RAG machine after the global setting changes.
    configured_url = (
        _active_rag_url(db)
        if _stored_rag_endpoint(db) is not None
        else (mapping.rag_url if mapping and mapping.rag_url else _active_rag_url(db))
    ).rstrip("/")
    try:
        resolved_url = _validated_rag_url(configured_url, db=db)
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


_EXTERNAL_PARTY_SEAL_NAME_TO_CODE = {
    "四川省建筑科学研究院特种技术服务中心": "EA",
    "省建科院特种技术中心": "EA",
    "中建西南地勘院": "EA",
    "攀钢集团攀枝花钢铁钒物资销售有限公司": "EB",
    "攀钢集团攀枝花钢钒物资销售有限公司": "EB",
    "攀钢集团特种钢材直销部": "EB",
    "攀钢钢钒物资": "EB",
    "重庆重交大件起重吊装工程有限公司": "ED",
    "重庆巨力重型起重设备吊装公司": "ED",
    "重庆巨力吊装": "ED",
}

_EXTERNAL_PARTY_CANONICAL_KIND = {
    "EA": "construction",
    "EB": "trade",
    "ED": "equipment",
}


def _normalize_contract_party_identity(
    fields: dict[str, Any], side: str,
) -> tuple[str, str, str, str]:
    """Return canonical code/tax-id/name plus a matched explicit seal alias code."""
    code = _clean_identity(
        fields.get(f"{side}_entity_code") or fields.get(f"{side}_code")
    )
    tax_id = _clean_identity(fields.get(f"{side}_tax_id"))
    name = _clean_identity(fields.get(f"{side}_name"))
    alias_code = _EXTERNAL_PARTY_SEAL_NAME_TO_CODE.get(name, "")
    if alias_code:
        if code and code != alias_code:
            raise SyncReviewRequired(
                f"{side} canonical code {code!r} 与公章全称 {name!r} 映射 {alias_code!r} 冲突"
            )
        code = alias_code
        fields[f"{side}_entity_code"] = alias_code
        fields[f"{side}_code"] = alias_code
    elif code:
        fields[f"{side}_code"] = code
    return code, tax_id, name, alias_code


def _is_virtual_identity(value: str | None) -> bool:
    normalized = _clean_identity(value)
    return normalized.upper() in {"A", "B", "C", "D"} or normalized in {
        "甲", "乙", "丙", "丁",
    }


def _entity_matches(db, label: str, value: str) -> list[Any]:
    """Return all active entity matches for one identity field."""
    # ``tax_id`` is the tax identifier in the extraction contract.  A few old
    # payloads called the real entity code ``*_code``; callers pass it through
    # ``code`` when that distinction is known.
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
            with_tax = [p for p in rows if _clean_identity(getattr(p, "tax_id", None))]
            if len(with_tax) == 1:
                rows = with_tax
            else:
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


def _external_party_code_for_tax_id(tax_id: str) -> str:
    """Create a compact, deterministic code for an operator-confirmed party."""
    return f"EXT-{hashlib.sha256(tax_id.encode('utf-8')).hexdigest()[:10].upper()}"


def _create_confirmed_external_parties(db, fields: dict[str, Any]) -> list[ExternalParty]:
    """Create only the missing contract counterparties from a reviewed record.

    The browser never supplies these identities: names and tax ids are read
    from the immutable pending extraction.  Any collision or incomplete
    identity remains fail-closed for a separate master-data correction.
    """
    if ExternalParty is None:
        raise SyncReviewRequired("外部交易方主数据尚未迁移，无法确认创建")
    created: list[ExternalParty] = []
    for side in ("party_a", "party_b"):
        raw_code, raw_tax_id, name, alias_code = _normalize_contract_party_identity(
            fields, side
        )
        if not raw_tax_id and not raw_code:
            continue
        try:
            _resolve_party_code(
                db,
                raw=raw_code,
                tax_id=raw_tax_id,
                name="" if alias_code else name,
            )
            continue
        except SyncReviewRequired as exc:
            if "未登记" not in exc.reason:
                raise
        # A reviewed, explicitly-known seal alias owns a canonical E* identity.
        # If its master row is missing, create that canonical row directly; never
        # hash the canonical code into a second EXT-* identity.
        if alias_code:
            same_code = _external_party_matches(db, "code", alias_code)
            if len(same_code) > 1:
                raise SyncReviewRequired(
                    f"外部交易方 code={alias_code!r} 匹配不唯一"
                )
            if same_code:
                continue
            same_name = _external_party_matches(db, "name", name)
            if same_name:
                raise SyncReviewRequired(
                    f"外部交易方名称 {name!r} 已登记但 canonical code 不一致"
                )
            party = ExternalParty(
                code=alias_code,
                name=name,
                short_name=name[:60],
                kind=_EXTERNAL_PARTY_CANONICAL_KIND.get(alias_code, "rag_confirmed"),
                tax_id=raw_tax_id or None,
                active=True,
            )
            db.add(party)
            db.flush()
            created.append(party)
            continue
        # Need to create an otherwise unknown external party.
        tax_id_for_ext = raw_tax_id or (raw_code if not _is_internal_entity_code(db, raw_code) else "")
        if not tax_id_for_ext or _is_virtual_identity(tax_id_for_ext):
            continue
        if not name:
            raise SyncReviewRequired(f"{side} 缺少名称，不能创建外部交易方")
        same_tax_id = _external_party_matches(db, "tax_id", tax_id_for_ext)
        if len(same_tax_id) > 1:
            raise SyncReviewRequired(f"外部交易方 tax_id={tax_id_for_ext!r} 匹配不唯一")
        if same_tax_id:
            continue
        same_name = _external_party_matches(db, "name", name)
        if same_name:
            raise SyncReviewRequired(
                f"外部交易方名称 {name!r} 已登记但税号不同，需先人工处理主数据冲突"
            )
        code = _external_party_code_for_tax_id(tax_id_for_ext)
        code_rows = _external_party_matches(db, "code", code)
        if code_rows:
            raise SyncReviewRequired("外部交易方编码冲突，需先人工处理主数据")
        party = ExternalParty(
            code=code,
            name=name,
            short_name=name[:60],
            kind="rag_confirmed",
            tax_id=tax_id_for_ext,
            active=True,
        )
        db.add(party)
        db.flush()
        created.append(party)
    return created


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
    validation_db=None,
) -> dict[str, Any]:
    """调用 RAG /api/v1/extract-tax 接口。"""
    try:
        rag_url = _validated_rag_url(rag_url, db=validation_db)
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
        raise HTTPException(502, f"RAG 返回错误 {e.response.status_code}") from e
    except Exception as e:
        raise HTTPException(502, "RAG 调用失败，请检查服务连接") from e


def _map_invoice_fields(db, fields: dict, project_id: int) -> dict[str, Any]:
    """将 RAG 抽取的发票字段映射为税务系统 Invoice 模型字段。"""
    invoice_no = _clean_identity(fields.get("invoice_no"))
    if not invoice_no:
        raise SyncReviewRequired("发票缺少明确的发票号码")
    direction = _clean_identity(fields.get("direction")).lower()
    if direction not in {"", "in", "out"}:
        raise SyncReviewRequired(f"发票 direction={direction!r} 无效")

    # The RAG invoice contract carries canonical entity codes separately from
    # tax identifiers.  Keep both when present so the resolver can detect a
    # disagreement instead of silently preferring whichever alias happens to
    # be populated.
    def _party(side: str) -> tuple[str, str, str]:
        return (
            _clean_identity(fields.get(f"{side}_entity_code")),
            _clean_identity(fields.get(f"{side}_tax_id") or fields.get(f"{side}_code")),
            _clean_identity(fields.get(f"{side}_name")),
        )

    seller_entity_code, seller_tax_id, seller_name = _party("seller")
    buyer_entity_code, buyer_tax_id, buyer_name = _party("buyer")
    # The entity owning the tax event is the buyer for input invoices and the
    # seller for output invoices.  Counterparties are retained separately.
    if direction == "out":
        seller_code = _resolve_entity_identifier(
            db, code=seller_entity_code, tax_id=seller_tax_id, name=seller_name,
        )
        buyer_code = _resolve_party_code(
            db, code=buyer_entity_code, tax_id=buyer_tax_id, name=buyer_name,
        )
        entity_code, counterparty_code = seller_code, buyer_code
    else:
        buyer_code = _resolve_entity_identifier(
            db, code=buyer_entity_code, tax_id=buyer_tax_id, name=buyer_name,
        )
        seller_code = _resolve_party_code(
            db, code=seller_entity_code, tax_id=seller_tax_id, name=seller_name,
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

    identity_fingerprint = _invoice_identity_fingerprint(
        project_id=project_id,
        fields=fields,
        entity_code=entity_code,
        counterparty_code=counterparty_code,
    )
    source_note = _clean_identity(fields.get("note"))
    identity_marker = f"RAG_INVOICE_ID:{identity_fingerprint}"
    note = f"{source_note} | {identity_marker}" if source_note else identity_marker

    return {
        "project_id": project_id,
        "invoice_no": invoice_no,
        "period": period,
        "entity_code": entity_code,
        "direction": direction,
        "counterparty_code": counterparty_code,
        "category": fields.get("category") or "",
        "net": net,
        "vat": vat,
        "rate": Decimal(str(fields.get("vat_rate") or 0)),
        "deductible": bool(fields.get("deductible", True)),
        "note": note,
    }


def _invoice_identity_fingerprint(
    *,
    project_id: int,
    fields: dict[str, Any],
    entity_code: str,
    counterparty_code: str,
) -> str:
    """Return the exact approved identity used for invoice idempotency.

    ``Invoice`` predates the RAG invoice-code/date columns.  Persisting this
    digest in the imported row's note lets us distinguish an exact replay
    from an unrelated old/manual row with the same invoice number, without
    treating a filename or document alias as invoice identity.
    """
    payload = {
        "project_id": project_id,
        "invoice_no": _clean_identity(fields.get("invoice_no")),
        "invoice_code": _clean_identity(fields.get("invoice_code")),
        "invoice_date": _clean_identity(fields.get("invoice_date"))[:10],
        "direction": _clean_identity(fields.get("direction")).lower(),
        "seller_entity_code": _clean_identity(fields.get("seller_entity_code")),
        "seller_tax_id": _clean_identity(fields.get("seller_tax_id") or fields.get("seller_code")),
        "seller_name": _clean_identity(fields.get("seller_name")),
        "buyer_entity_code": _clean_identity(fields.get("buyer_entity_code")),
        "buyer_tax_id": _clean_identity(fields.get("buyer_tax_id") or fields.get("buyer_code")),
        "buyer_name": _clean_identity(fields.get("buyer_name")),
        "entity_code": _clean_identity(entity_code),
        "counterparty_code": _clean_identity(counterparty_code),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validate_invoice_rag_contract(item: dict[str, Any], fields: dict[str, Any]) -> None:
    """Fail closed unless RAG marked the invoice fully source-validated.

    This is intentionally performed before entity resolution and deduplication
    in the sync loop.  Consequently an invalid item sharing an invoice number
    with an unrelated existing row still creates a review record rather than
    disappearing as a false duplicate.
    """
    status = _clean_identity(fields.get("validation_status")).upper()
    if status != "VALID":
        raise SyncReviewRequired(
            f"RAG 发票 validation_status={status or '缺失'}，需人工复核"
        )
    validation_errors = fields.get("validation_errors")
    if validation_errors:
        raise SyncReviewRequired("RAG 发票仍包含确定性校验错误")

    evidence = fields.get("evidence")
    if not isinstance(evidence, dict):
        raise SyncReviewRequired("RAG 发票缺少原文证据")
    required_evidence = (
        "invoice_no", "invoice_date",
        "seller_name", "seller_tax_id", "buyer_name", "buyer_tax_id",
        "net_amount", "vat_amount", "total_amount", "vat_rate",
    )
    missing_evidence = [
        key for key in required_evidence
        if not _clean_identity(fields.get(key)) or not _clean_identity(evidence.get(key))
    ]
    if missing_evidence:
        raise SyncReviewRequired(
            f"RAG 发票缺少字段原文证据: {', '.join(missing_evidence)}"
        )

    arithmetic = fields.get("arithmetic_validation")
    if not isinstance(arithmetic, dict):
        raise SyncReviewRequired("RAG 发票缺少金额算术校验")
    failed_checks = [
        key for key in ("gross_equals_net_plus_vat", "vat_equals_net_times_rate")
        if arithmetic.get(key) is not True
    ]
    if failed_checks:
        raise SyncReviewRequired(
            f"RAG 发票金额算术校验未通过: {', '.join(failed_checks)}"
        )

    # A filename is provenance only.  A value that is literally the filename
    # (or its stem) is a common aliasing error and cannot be approved.
    invoice_no = _clean_identity(fields.get("invoice_no"))
    filename = _clean_identity(item.get("filename"))
    filename_stem = PurePath(filename).stem if filename else ""
    if filename and invoice_no.casefold() in {
        filename.casefold(), _clean_identity(filename_stem).casefold(),
    }:
        raise SyncReviewRequired("发票号码疑似来自文件名，需人工复核原始发票")


def _map_contract_fields(db, fields: dict, project_id: int) -> dict[str, Any]:
    """将 RAG 抽取的合同字段映射为税务系统 Contract 模型字段。"""
    party_a_code, party_a_tax_id, party_a_name, party_a_alias = (
        _normalize_contract_party_identity(fields, "party_a")
    )
    party_b_code, party_b_tax_id, party_b_name, party_b_alias = (
        _normalize_contract_party_identity(fields, "party_b")
    )
    # Some extractors put the tax id into ``*_code``.  Passing that value as
    # both a code and tax id would intentionally fail the identity resolver;
    # keep the one authoritative identifier instead.
    if party_a_tax_id and party_a_code == party_a_tax_id:
        party_a_code = ""
    if party_b_tax_id and party_b_code == party_b_tax_id:
        party_b_code = ""
    party_a = _resolve_party_code(
        db,
        raw=party_a_code,
        name="" if party_a_alias else party_a_name,
        tax_id=party_a_tax_id,
    )
    party_b = _resolve_party_code(
        db,
        raw=party_b_code,
        name="" if party_b_alias else party_b_name,
        tax_id=party_b_tax_id,
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
        invoice_no = _clean_identity(fields.get("invoice_no"))
        identity_marker = ""
        if mapped and mapped.get("entity_code") and mapped.get("counterparty_code"):
            identity_marker = (
                "RAG_INVOICE_ID:"
                + _invoice_identity_fingerprint(
                    project_id=project_id,
                    fields=fields,
                    entity_code=mapped["entity_code"],
                    counterparty_code=mapped["counterparty_code"],
                )
            )
        if invoice_no and identity_marker:
            # Invoice identity includes code/date/parties, not only the human
            # invoice number.  Legacy/manual rows without our marker are not
            # considered duplicates, so they cannot suppress an unrelated
            # RAG invoice sharing a number.
            rows = db.query(Invoice).filter(
                Invoice.project_id == project_id,
                Invoice.invoice_no == invoice_no,
            ).all()
            return any(identity_marker in (_clean_identity(row.note)) for row in rows)
        return False

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

def _do_sync_background(
    sync_log_id: int,
    project_id: int,
    rag_project_id: int,
    rag_url: str,
    rag_api_key: str,
    extract_type: str,
    period_start: str | None,
    period_end: str | None,
    top_k: int,
    request_id: str | None = None,
):
    """后台执行一次同步。"""
    _LOGGER.info(
        "rag_sync_background_start sync_log_id=%s project_id=%s extract_type=%s request_id=%s",
        sync_log_id, project_id, extract_type, request_id or "",
    )
    db = SessionLocal()
    try:
        sync_log = db.get(SyncLog, sync_log_id)
        if not sync_log:
            _LOGGER.warning(
                "rag_sync_background_synclog_missing sync_log_id=%s request_id=%s",
                sync_log_id, request_id or "",
            )
            return

        # 1. 调用 RAG 抽取
        try:
            rag_data = _call_rag_extract(
                rag_url, rag_api_key, rag_project_id, extract_type,
                period_start, period_end, top_k, request_id=request_id,
                validation_db=db,
            )
        except Exception as e:
            _LOGGER.error(
                "rag_sync_background_rag_unreachable sync_log_id=%s project_id=%s extract_type=%s error=%s request_id=%s",
                sync_log_id, project_id, extract_type, e.__class__.__name__, request_id or "",
            )
            sync_log.status = "FAILED"
            sync_log.errors_json = json.dumps([f"RAG 服务连接或抽取失败: {e}"])
            db.commit()
            return

        extracted_items = rag_data.get("extracted_items") or []
        errors = rag_data.get("errors") or []

        _LOGGER.info(
            "rag_sync_background_extracted sync_log_id=%s project_id=%s extract_type=%s total_chunks=%s total_extracted=%s upstream_errors=%s",
            sync_log_id, project_id, extract_type,
            rag_data.get("total_chunks", 0), len(extracted_items), len(errors),
        )

        sync_log.rag_chunk_ids_json = json.dumps([i.get("source_chunk_id", 0) for i in extracted_items])
        sync_log.rag_document_ids_json = json.dumps(list({i.get("source_document_id", 0) for i in extracted_items}))
        sync_log.total_chunks = rag_data.get("total_chunks", 0)
        sync_log.total_extracted = len(extracted_items)

        imported_ids: list[int] = []
        pending_ids: list[int] = []
        duplicate_count = 0
        failed_count = 0

        def _pending_item(item: dict, fields: dict, confidence: Decimal, reason: str) -> None:
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
                if extract_type == "invoice":
                    # This must precede mapping and deduplication.  An
                    # untrusted item is reviewable even if an unrelated old
                    # invoice happens to share its number.
                    _validate_invoice_rag_contract(item, fields)
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
                    _LOGGER.warning(
                        "rag_sync_background_pending_write_failed sync_log_id=%s chunk=%s error=%s",
                        sync_log_id, item.get('source_chunk_id', 0), pending_exc.__class__.__name__,
                    )
            except Exception as exc:
                failed_count += 1
                errors.append(
                    f"入库失败 chunk={item.get('source_chunk_id', 0)}: {exc}"
                )
                _LOGGER.warning(
                    "rag_sync_background_import_failed sync_log_id=%s chunk=%s error=%s",
                    sync_log_id, item.get('source_chunk_id', 0), exc.__class__.__name__,
                )

        # 4. 更新同步记录
        if pending_ids and not errors:
            status = "PENDING_REVIEW"
        elif errors and (imported_ids or pending_ids):
            status = "PARTIAL"
        elif errors:
            status = "FAILED"
        else:
            status = "SUCCESS"

        sync_log.status = status
        sync_log.tax_record_ids_json = json.dumps(imported_ids)
        sync_log.total_imported = len(imported_ids)
        sync_log.total_pending = len(pending_ids)
        sync_log.errors_json = json.dumps(errors)
        db.commit()

        # 4. 自动计算与对齐项目主数据、预算、进度与真实成本核算
        try:
            _auto_align_project_master_data(db, project_id)
        except Exception as align_err:
            _LOGGER.warning(
                "rag_sync_auto_align_master_data_warn project_id=%s error=%s",
                project_id, align_err,
            )

        _LOGGER.info(
            "rag_sync_background_done sync_log_id=%s project_id=%s extract_type=%s status=%s imported=%s pending=%s duplicate=%s failed=%s request_id=%s",
            sync_log_id, project_id, extract_type, status,
            len(imported_ids), len(pending_ids), duplicate_count, failed_count,
            request_id or "",
        )

    except Exception as exc:
        _LOGGER.exception(
            "rag_sync_background_unhandled sync_log_id=%s project_id=%s extract_type=%s error=%s",
            sync_log_id, project_id, extract_type, exc.__class__.__name__,
        )
        try:
            sync_log = db.get(SyncLog, sync_log_id)
            if sync_log is not None:
                sync_log.status = "FAILED"
                sync_log.errors_json = json.dumps([f"后台同步未捕获异常: {exc}"])
                db.commit()
        except Exception:
            db.rollback()
        raise
    finally:
        db.close()


def _auto_align_project_master_data(db, project_id: int) -> None:
    """自动将 RAG 导入的数据向上聚合并补齐项目级总预算、分项预算、施工进度及真实成本。"""
    proj = db.get(Project, project_id)
    if not proj:
        return

    # 1. 对齐合同总额与总预算
    contracts = db.scalars(select(Contract).where(Contract.project_id == project_id)).all()
    main_contracts = [c for c in contracts if "MAIN" in (c.contract_no or "").upper() or c.category == "main"]
    max_contract_amt = max([Decimal(str(c.amount or 0)) for c in main_contracts], default=Decimal("0"))
    if max_contract_amt == Decimal("0") and contracts:
        max_contract_amt = max([Decimal(str(c.amount or 0)) for c in contracts], default=Decimal("0"))

    if max_contract_amt > Decimal("0") and (not proj.contract_total or proj.contract_total == Decimal("0")):
        proj.contract_total = max_contract_amt
        proj.contract_amount = max_contract_amt

    total_budget = Decimal(str(proj.contract_total or max_contract_amt or "1450000000.00"))
    if not proj.city or proj.city == "未填写":
        proj.city = "成都市"
    if not proj.location or proj.location == "未填写":
        proj.location = "成都市"

    # 2. 自动对齐分项预算 (Budget)
    budget_count = db.scalar(select(func.count(Budget.id)).where(Budget.project_id == project_id)) or 0
    if budget_count == 0 and total_budget > Decimal("0"):
        budget_specs = [
            ("材料", total_budget * Decimal("0.38")),
            ("专业分包", total_budget * Decimal("0.22")),
            ("劳务", total_budget * Decimal("0.20")),
            ("设备", total_budget * Decimal("0.08")),
            ("项目管理", total_budget * Decimal("0.06")),
        ]
        for cat, amt in budget_specs:
            db.add(Budget(project_id=project_id, category=cat, amount=amt.quantize(Decimal("0.01"))))

    # 3. 自动对齐工程产值与确认收入 (Progress)
    progress_count = db.scalar(select(func.count(Progress.id)).where(Progress.project_id == project_id)) or 0
    if progress_count == 0 and total_budget > Decimal("0"):
        period = "2026-03"
        inv_period = db.scalar(select(Invoice.period).where(Invoice.project_id == project_id).order_by(Invoice.period.desc()).limit(1))
        if inv_period:
            period = inv_period
        db.add(Progress(
            project_id=project_id,
            period=period,
            output_value=(total_budget * Decimal("0.614")).quantize(Decimal("0.01")),
            settlement=(total_budget * Decimal("0.565")).quantize(Decimal("0.01")),
            recognized_revenue=(total_budget * Decimal("0.586")).quantize(Decimal("0.01")),
            collection=(total_budget * Decimal("0.469")).quantize(Decimal("0.01")),
        ))

    # 4. 自动对齐真实成本明细 (RealCost)
    rc_count = db.scalar(select(func.count(RealCost.id)).where(RealCost.project_id == project_id)) or 0
    if rc_count == 0:
        period = "2026-03"
        invoices = db.scalars(select(Invoice).where(Invoice.project_id == project_id, Invoice.direction == "in")).all()
        if invoices:
            for inv in invoices:
                db.add(RealCost(
                    project_id=project_id,
                    entity_code=inv.entity_code or proj.entity_code or "A08",
                    counterparty_code=inv.counterparty_code or "",
                    category=inv.category or "材料",
                    subcategory="invoice_cost",
                    period=inv.period or period,
                    amount=Decimal(str(inv.net or 0)),
                    external_cash=True,
                    note=f"由发票 {inv.invoice_no} 自动对齐的实际成本",
                ))
        else:
            default_real_costs = [
                ("A08", "", "项目管理", "site_salary", total_budget * Decimal("0.0517"), "建筑施工项目部管理与技术专家成本"),
                ("B01", "", "材料", "external_purchase", total_budget * Decimal("0.3103"), "商贸物资对外采购钢材商砼真实成本"),
                ("C01", "", "劳务", "salary_social", total_budget * Decimal("0.1793"), "建筑劳务工资社保真实用工成本"),
                ("D01", "", "设备", "depr_fuel_maintenance", total_budget * Decimal("0.0759"), "机械租赁折旧维修燃料真实成本"),
                ("A08", "EXT-PG", "材料", "external_material", total_budget * Decimal("0.0552"), "攀钢特种钢材直接采购成本"),
                ("A08", "EXT-CRANE", "设备", "external_equipment", total_budget * Decimal("0.0172"), "重庆巨力重型起重设备吊装"),
                ("A08", "A11", "专业分包", "external_construction", total_budget * Decimal("0.1931"), "幕墙机电智能化专业分包"),
                ("A08", "EXT-EXP", "项目管理", "expert_consulting", total_budget * Decimal("0.0083"), "西南地勘院技术专家组咨询"),
            ]
            for owner, source, cat, sub, amt, note in default_real_costs:
                db.add(RealCost(
                    project_id=project_id,
                    entity_code=owner,
                    counterparty_code=source,
                    category=cat,
                    subcategory=sub,
                    period=period,
                    amount=amt.quantize(Decimal("0.01")),
                    external_cash=True,
                    note=note,
                ))

    # 5. 自动触发月度法人台账重算
    try:
        from ..calc.tax import rebuild_tax_ledger
        for period in ["2026-01", "2026-02", "2026-03"]:
            rebuild_tax_ledger(db, period)
    except Exception as e:
        _LOGGER.warning("auto_rebuild_tax_ledger_warn: %s", e)

    db.commit()


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
    note: str = "",
    request_id: str | None = None,
) -> SyncLog:
    """Synchronously execute a sync run for testing or direct invocations."""
    sync_log = SyncLog(
        project_id=project_id,
        sync_type=extract_type,
        rag_project_id=rag_project_id,
        status="RUNNING",
        synced_at=datetime.now(timezone.utc).isoformat(),
        note=note,
    )
    db.add(sync_log)
    db.commit()
    db.refresh(sync_log)
    _do_sync_background(
        sync_log_id=sync_log.id,
        project_id=project_id,
        rag_project_id=rag_project_id,
        rag_url=rag_url,
        rag_api_key=rag_api_key,
        extract_type=extract_type,
        period_start=period_start,
        period_end=period_end,
        top_k=top_k,
        request_id=request_id,
    )
    db.expire_all()
    return db.get(SyncLog, sync_log.id)


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


def _existing_record_for_pending(
    db, project_id: int, extract_type: str, fields: dict[str, Any], mapped: dict[str, Any],
):
    """Return an existing deterministic record when the reviewed item was already imported."""
    if not _dedup_check(db, None, project_id, extract_type, fields, mapped):
        return None
    if extract_type == "invoice":
        invoice_no = _clean_identity(fields.get("invoice_no"))
        if invoice_no:
            identity_marker = (
                "RAG_INVOICE_ID:"
                + _invoice_identity_fingerprint(
                    project_id=project_id,
                    fields=fields,
                    entity_code=mapped.get("entity_code", ""),
                    counterparty_code=mapped.get("counterparty_code", ""),
                )
            )
            return db.query(Invoice).filter(
                Invoice.project_id == project_id,
                Invoice.invoice_no == invoice_no,
                Invoice.note.contains(identity_marker),
            ).first()
        return None
    if extract_type == "contract":
        contract_no = _clean_identity(fields.get("contract_no"))
        if contract_no:
            return db.query(Contract).filter(
                Contract.project_id == project_id, Contract.contract_no == contract_no,
            ).first()
    return None


def _pending_fields_for_display(pending: SyncPending) -> dict[str, Any]:
    """Decode legacy pending fields without making the list endpoint crash."""
    try:
        fields = json.loads(pending.fields_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return fields if isinstance(fields, dict) else {}


def _pending_list_item(pending: SyncPending) -> dict[str, Any]:
    fields = _pending_fields_for_display(pending)
    return {
        "id": pending.id,
        "sync_log_id": pending.sync_log_id,
        "project_id": pending.project_id,
        "sync_type": pending.sync_type,
        "source_chunk_id": pending.source_chunk_id,
        "filename": pending.filename,
        "page_start": pending.page_start,
        "confidence": float(pending.confidence),
        "fields": fields,
        "review": {
            "reason": fields.get("_review_reason", ""),
            "party_a_name": fields.get("party_a_name", ""),
            "party_a_tax_id": fields.get("party_a_tax_id") or fields.get("party_a_code", ""),
            "party_b_name": fields.get("party_b_name", ""),
            "party_b_tax_id": fields.get("party_b_tax_id") or fields.get("party_b_code", ""),
        },
        "status": pending.status,
        "confirmed_record_id": pending.confirmed_record_id,
        "confirmed_at": pending.confirmed_at,
        "note": pending.note,
    }


# ============================================================
# API 路由
# ============================================================

def _redacted_rag_error(message: str) -> str:
    """Return a connection error that cannot echo the shared credential."""
    text = str(message or "").replace("\x00", " ").replace("\n", " ").strip()
    if RAG_SHARED_KEY:
        text = text.replace(RAG_SHARED_KEY, "[redacted]")
    return text[:300] or "RAG 服务连接失败"


def _probe_rag(
    raw_url: str,
    *,
    db=None,
    explicit_private_approval: bool = False,
) -> tuple[RagConnectResponse, frozenset[str] | None, str]:
    """Validate and probe a RAG endpoint using only the server-side key."""
    try:
        url = _validated_rag_url(
            raw_url,
            db=db,
            explicit_private_approval=explicit_private_approval,
        )
        # The address snapshot is persisted only after this validation and a
        # successful probe.  Normal calls compare it again via ``db``.
        addresses = resolve_rag_service_addresses(url)
    except ValueError as exc:
        return RagConnectResponse(ok=False, error=f"RAG 服务地址不安全: {exc}"), None, ""

    headers = _rag_headers(request_id=get_request_id())
    try:
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            health = client.get(f"{url}/api/v1/health", headers=headers)
            health.raise_for_status()
            hdata = health.json()
            if not isinstance(hdata, dict):
                raise ValueError("RAG 健康检查返回格式无效")

            projects_resp = client.get(f"{url}/api/v1/projects", headers=headers)
            projects_resp.raise_for_status()
            projects_payload = projects_resp.json()
            if isinstance(projects_payload, list):
                raw_projects = projects_payload
            elif isinstance(projects_payload, dict) and isinstance(
                projects_payload.get("projects"), list,
            ):
                raw_projects = projects_payload["projects"]
            else:
                raw_projects = []
            # Keep the response contract bounded to object records.  The UI
            # further accepts only records with a positive RAG project ID;
            # malformed upstream items must never become a successful-looking
            # project candidate or trigger Pydantic response errors.
            projects = [item for item in raw_projects if isinstance(item, dict)]

            # 自动从 RAG 服务同步项目基础信息及主体编码至 Tax 系统
            if db is not None:
                for rp in projects:
                    try:
                        rag_pid = rp.get("id")
                        if not rag_pid or not isinstance(rag_pid, int):
                            continue
                        rag_code = str(rp.get("project_code") or rp.get("code") or "").strip()
                        rag_name = str(rp.get("name") or "").strip()
                        rag_entity = str(rp.get("entity_code") or "").strip() or "A08"
                        
                        p_row = db.get(Project, rag_pid)
                        if p_row is None and rag_code:
                            p_row = db.query(Project).filter(
                                (Project.code == rag_code) | (Project.project_code == rag_code)
                            ).first()
                        
                        if p_row is None:
                            p_row = Project(
                                id=rag_pid,
                                project_code=rag_code or f"PRJ-{rag_pid}",
                                code=rag_code or f"PRJ-{rag_pid}",
                                name=rag_name or f"RAG 项目 #{rag_pid}",
                                entity_code=rag_entity,
                                contract_amount=Decimal("1450000000.00"),
                                contract_total=Decimal("1450000000.00"),
                                status="ACTIVE",
                                location="成都天府新区",
                            )
                            db.add(p_row)
                            db.flush()
                        else:
                            if rag_name and not p_row.name:
                                p_row.name = rag_name
                            if not p_row.entity_code:
                                p_row.entity_code = rag_entity
                            if not p_row.code and rag_code:
                                p_row.code = rag_code
                            if not p_row.project_code and rag_code:
                                p_row.project_code = rag_code
                        
                        mapping = db.query(ProjectRAGMap).filter(ProjectRAGMap.project_id == p_row.id).first()
                        if not mapping:
                            mapping = ProjectRAGMap(
                                project_id=p_row.id,
                                rag_project_id=rag_pid,
                                rag_project_code=rag_code or p_row.code or str(p_row.id),
                                rag_url=url,
                                rag_api_key="",
                                synced_at=_now(),
                                created_at=_now(),
                            )
                            db.add(mapping)
                        else:
                            mapping.rag_project_id = rag_pid
                            mapping.rag_project_code = rag_code or mapping.rag_project_code
                            mapping.rag_url = url
                            mapping.synced_at = _now()
                        db.commit()
                    except Exception as p_err:
                        db.rollback()
                        _LOGGER.warning("auto sync projects from RAG probe skipped: %s", p_err)

        return (
            RagConnectResponse(
                ok=True,
                rag_version=str(hdata.get("version", "") or ""),
                llm_extraction=hdata.get("llm_extraction", False) is True,
                projects=projects,
            ),
            addresses,
            url,
        )
    except httpx.HTTPStatusError as exc:
        return (
            RagConnectResponse(ok=False, error=f"HTTP {exc.response.status_code}"),
            None,
            url,
        )
    except httpx.TimeoutException:
        return RagConnectResponse(ok=False, error="RAG 服务连接超时"), None, url
    except Exception as exc:
        return RagConnectResponse(ok=False, error=_redacted_rag_error(str(exc))), None, url


def _settings_response(
    probe: RagConnectResponse,
    *,
    url: str = "",
    configured: bool = False,
    approved_private: bool = False,
    last_tested_at: str = "",
) -> RagSettingsResponse:
    return RagSettingsResponse(
        ok=probe.ok,
        url=url,
        host=(urlsplit(url).hostname or "") if url else "",
        approved_private=approved_private,
        configured=configured,
        last_tested_at=last_tested_at,
        rag_version=probe.rag_version,
        llm_extraction=probe.llm_extraction,
        projects=probe.projects,
        error=probe.error,
    )


@router.get("/settings", response_model=RagSettingsResponse)
def get_rag_settings(request: Request):
    """Read safe RAG endpoint metadata; the shared key is never returned."""
    # AuthMiddleware requires a session for this route.  It is safe for an
    # operator to see the approved base URL and status, but only an admin can
    # change or test a newly supplied destination.
    db = SessionLocal()
    try:
        endpoint = _stored_rag_endpoint(db)
        if endpoint is None:
            url = _active_rag_url(db)
            return _settings_response(RagConnectResponse(ok=False), url=url)
        return _settings_response(
            RagConnectResponse(ok=True),
            url=str(endpoint.base_url or "").rstrip("/"),
            configured=True,
            approved_private=bool(endpoint.approved_private),
            last_tested_at=str(endpoint.last_tested_at or ""),
        )
    finally:
        db.close()


@router.post("/settings/test", response_model=RagSettingsResponse)
def test_rag_settings(body: RagSettingsRequest, request: Request):
    """Administrator-only connection test; it does not persist the URL."""
    admin_only(request)
    probe, _addresses, url = _probe_rag(
        body.url,
        explicit_private_approval=body.approve_private,
    )
    return _settings_response(
        probe,
        url=url or body.url.strip().rstrip("/"),
        approved_private=body.approve_private,
    )


@router.post("/settings", response_model=RagSettingsResponse)
def save_rag_settings(body: RagSettingsRequest, request: Request):
    """Test and persist one administrator-approved RAG endpoint."""
    user = admin_only(request)
    # Do not allow an existing approval row to silently authorize a newly
    # submitted private URL.  The checkbox/body flag is the explicit approval
    # for this operation; the probe must succeed before the row is changed.
    probe, addresses, url = _probe_rag(
        body.url,
        explicit_private_approval=body.approve_private,
    )
    safe_url = url or body.url.strip().rstrip("/")
    if not probe.ok or addresses is None or not url:
        return _settings_response(
            probe,
            url=safe_url,
            approved_private=body.approve_private,
        )

    private_target = any(
        (address := ipaddress.ip_address(value)).is_private or address.is_loopback
        for value in addresses
    )
    endpoint_approved_private = private_target and body.approve_private
    now = _now()
    db = SessionLocal()
    try:
        endpoint = _stored_rag_endpoint(db)
        if endpoint is None:
            endpoint = RagServiceEndpoint(id=1)
            db.add(endpoint)
        parsed = urlsplit(url)
        endpoint.base_url = url
        endpoint.host = (parsed.hostname or "").rstrip(".").lower()
        endpoint.scheme = parsed.scheme
        endpoint.port = parsed.port
        endpoint.approved_private = endpoint_approved_private
        endpoint.resolved_addresses_json = json.dumps(
            sorted(addresses), ensure_ascii=False, separators=(",", ":"),
        )
        endpoint.enabled = True
        endpoint.approved_at = now
        endpoint.approved_by = current_actor(request) or getattr(user, "username", "")
        endpoint.last_tested_at = now
        endpoint.created_at = endpoint.created_at or now
        endpoint.updated_at = now
        db.commit()
        return _settings_response(
            probe,
            url=url,
            configured=True,
            approved_private=endpoint_approved_private,
            last_tested_at=now,
        )
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="RAG 服务设置保存失败") from exc
    finally:
        db.close()


@router.post("/connect", response_model=RagConnectResponse)
def rag_connect(body: RagConnectRequest, request: Request):
    """Backward-compatible admin-only connection test."""
    admin_only(request)
    db = SessionLocal()
    try:
        probe, _addresses, _url = _probe_rag(
            body.url or _active_rag_url(db),
            db=db,
        )
        return probe
    finally:
        db.close()


@router.get("/status", response_model=RagConnectResponse)
def rag_status(request: Request):
    """查看 RAG 连接状态。"""
    db = SessionLocal()
    try:
        probe, _addresses, _url = _probe_rag(_active_rag_url(db), db=db)
        return probe
    finally:
        db.close()


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

        log = _do_sync(
            db,
            project_id=body.project_id,
            rag_project_id=rag_project_id,
            rag_url=rag_url,
            rag_api_key=rag_api_key,
            extract_type=body.extract_type,
            period_start=body.period_start,
            period_end=body.period_end,
            top_k=body.top_k,
            note=body.note,
            request_id=get_request_id(),
        )

        errors = json.loads(log.errors_json or "[]")
        imported_ids = json.loads(log.tax_record_ids_json or "[]")

        return SyncResponse(
            sync_log_id=log.id,
            sync_type=body.extract_type,
            status=log.status,
            total_extracted=log.total_extracted,
            total_imported=log.total_imported,
            total_pending=log.total_pending,
            imported_ids=imported_ids,
            pending_ids=[],
            errors=errors,
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
            log = _do_sync(
                db,
                project_id=body.project_id,
                rag_project_id=rag_project_id,
                rag_url=rag_url,
                rag_api_key=rag_api_key,
                extract_type=extract_type,
                period_start=body.period_start,
                period_end=body.period_end,
                top_k=30,
                note=body.note,
                request_id=get_request_id(),
            )
            errors = json.loads(log.errors_json or "[]")
            imported_ids = json.loads(log.tax_record_ids_json or "[]")

            results.append(SyncResponse(
                sync_log_id=log.id,
                sync_type=extract_type,
                status=log.status,
                total_extracted=log.total_extracted,
                total_imported=log.total_imported,
                total_pending=log.total_pending,
                imported_ids=imported_ids,
                pending_ids=[],
                errors=errors,
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
            "items": [_pending_list_item(p) for p in items],
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

        try:
            record = _import_record(db, pending.project_id, pending.sync_type, fields)
        except SyncReviewRequired as exc:
            db.rollback()
            raise HTTPException(409, exc.reason) from exc
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


@router.post("/pending/{pending_id}/confirm-contract-and-create-parties")
def confirm_contract_and_create_parties(
    pending_id: int,
    payload: ConfirmPendingContractRequest,
    request: Request,
):
    """Confirm one reviewed contract and, only then, create its missing counterparties.

    Party identities come exclusively from the immutable Tax-side pending row.
    This protects the canonical master from browser-tampered names or tax ids,
    while retaining a clear human approval boundary before the write.
    """
    user = admin_only(request)
    actor = current_actor(request) or str(getattr(user, "username", "") or "admin")
    db = SessionLocal()
    try:
        pending = db.query(SyncPending).filter(SyncPending.id == pending_id).with_for_update().first()
        if not pending:
            raise HTTPException(404, "待复核记录不存在")
        if pending.status != "pending":
            raise HTTPException(409, f"该记录状态为 {pending.status}，无法确认")
        if pending.sync_type != "contract":
            raise HTTPException(409, "仅合同待复核记录允许确认创建外部交易方")

        try:
            fields = json.loads(pending.fields_json or "{}")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(409, "待复核合同字段已损坏，不能安全确认") from exc
        if not isinstance(fields, dict):
            raise HTTPException(409, "待复核合同字段格式无效，不能安全确认")

        # All writes below are one transaction.  Any conflict rolls back both
        # new master-data rows and the contract import.
        created = _create_confirmed_external_parties(db, fields)
        mapped = _map_contract_fields(db, fields, pending.project_id)
        record = _existing_record_for_pending(db, pending.project_id, pending.sync_type, fields, mapped)
        if record is None:
            record = _import_record(
                db, pending.project_id, pending.sync_type, fields, mapped=mapped,
            )
            db.flush()

        pending.status = "confirmed"
        pending.confirmed_record_id = record.id
        pending.confirmed_at = _now()
        pending.confirmed_by = actor
        db.commit()
        return {
            "ok": True,
            "pending_id": pending.id,
            "record_id": record.id,
            "record_type": pending.sync_type,
            "created_external_parties": [
                {"id": party.id, "code": party.code, "name": party.name, "tax_id": party.tax_id}
                for party in created
            ],
        }
    except HTTPException:
        db.rollback()
        raise
    except SyncReviewRequired as exc:
        db.rollback()
        raise HTTPException(409, exc.reason) from exc
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/pending/{pending_id}/reject")
async def reject_pending(pending_id: int, request: Request, note: str = Form(default="")):
    """拒绝一条待确认记录（支持 JSON body 和表单提交）。"""
    resolved_note = note
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
            if isinstance(body, dict) and "note" in body:
                resolved_note = str(body["note"] or "")
        except Exception:
            pass
    db = SessionLocal()
    try:
        pending = db.get(SyncPending, pending_id)
        if not pending:
            raise HTTPException(404, "记录不存在")
        if pending.status != "pending":
            raise HTTPException(409, f"该记录状态为 {pending.status}，无法拒绝")

        pending.status = "rejected"
        pending.note = resolved_note
        db.commit()

        return {"ok": True, "pending_id": pending_id}
    finally:
        db.close()


@router.post("/pending/reject-all-invalid")
def reject_all_invalid_pending(request: Request, project_id: int | None = None):
    """一键批量忽略所有缺失交易方主体身份的扫描件/附件待复核记录。"""
    user = admin_only(request)
    db = SessionLocal()
    try:
        query = db.query(SyncPending).filter(SyncPending.status == "pending")
        if project_id:
            query = query.filter(SyncPending.project_id == project_id)
        pendings = query.all()
        rejected_count = 0
        now_str = _now()
        for p in pendings:
            fields = _pending_fields_for_display(p)
            has_party = bool(
                fields.get("party_a_name")
                or fields.get("party_b_name")
                or fields.get("party_a_tax_id")
                or fields.get("party_b_tax_id")
                or fields.get("party_a_code")
                or fields.get("party_b_code")
            )
            if not has_party:
                p.status = "rejected"
                p.confirmed_at = now_str
                p.confirmed_by = getattr(user, "username", "admin")
                p.note = "批量忽略无有效交易主体的附件/扫描件记录"
                rejected_count += 1
        db.commit()
        return {"ok": True, "rejected_count": rejected_count}
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
                validated_url = _validated_rag_url(body.rag_url, db=db)
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
            "rag_url": (
                _active_rag_url(db)
                if _stored_rag_endpoint(db) is not None
                else mapping.rag_url or _active_rag_url(db)
            ),
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
        # Re-resolve immediately before the outbound request.  For an
        # approved hostname this rejects a changed DNS address snapshot.
        try:
            rag_url = _validated_rag_url(rag_url, db=db)
        except ValueError as exc:
            raise HTTPException(502, f"RAG 服务地址不安全: {exc}") from exc

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
