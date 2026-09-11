"""Canonical structured facts promoted from RAG documents.

RAG owns this write boundary. Tax is a read-only consumer of accepted/current
facts and must never treat raw OCR/LLM output as accounting truth.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from sqlalchemy import select, text

from ..domain.entities import get_external_preset, is_canonical_entity_code
from ..models import Chunk, Document
from .extractor import (
    extract_contract_fields_from_text,
    extract_invoice_fields_from_text,
    extract_payment_fields_from_text,
)
from .tax_extraction import EXTRACT_DOC_TYPE_FILTERS

_MAX_FACT_TEXT_CHARS = 300_000
_FACT_SCHEMA_VERSION = "v1"
_VALIDATION_POLICY_VERSION = "v1"


@dataclass(frozen=True)
class CanonicalFactCandidate:
    fact_type: str
    business_key: str
    payload: dict[str, Any]
    evidence: dict[str, Any]
    validation_errors: list[str]
    confidence: Decimal
    status: str
    source_hash: str


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _money(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value).replace(",", "").replace("，", ""))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _canonical_party(value: Any) -> str:
    raw = _clean(value).upper()
    if not raw:
        return ""
    if is_canonical_entity_code(raw):
        return raw
    preset = get_external_preset(raw)
    return str(preset["code"]).upper() if preset else ""


def _known_party(code: Any) -> bool:
    raw = _clean(code).upper()
    return bool(raw and (is_canonical_entity_code(raw) or get_external_preset(raw)))


def infer_fact_type(document_type: str | None) -> str | None:
    """Map a RAG document type to the deterministic fact extractor."""
    value = _clean(document_type).lower()
    for fact_type in ("contract", "invoice", "payment"):
        if value in EXTRACT_DOC_TYPE_FILTERS[fact_type]:
            return fact_type
    return None


def _canonical_name_party(value: Any) -> str:
    preset = get_external_preset(_clean(value))
    return str(preset["code"]).upper() if preset else ""


def _merge_party_identity(
    fields: dict[str, Any],
    prefix: str,
    document_code: Any = "",
) -> None:
    """Merge document/code/name identity while recording every contradiction.

    Document metadata is authoritative only when it agrees with any explicit
    extracted canonical code and any known legal-name alias.  Contradictions
    are preserved as validation errors so the candidate becomes needs_review.
    """
    code_key = f"{prefix}_entity_code"
    generic_key = f"{prefix}_code"
    name_key = f"{prefix}_name"

    raw_extracted = _clean(fields.get(code_key) or fields.get(generic_key))
    extracted_code = _canonical_party(raw_extracted)
    document_canonical = _canonical_party(document_code)
    legal_name = _clean(fields.get(name_key))
    legal_name_code = _canonical_name_party(legal_name)

    conflicts = fields.setdefault("_identity_conflicts", [])
    if raw_extracted and not extracted_code:
        conflicts.append(
            f"{prefix} extracted code {raw_extracted!r} is not a canonical identity"
        )

    observations = {
        "document": document_canonical,
        "extracted": extracted_code,
        "legal_name": legal_name_code,
    }
    distinct = {code for code in observations.values() if code}
    if len(distinct) > 1:
        detail = "; ".join(
            f"{label}={code or '-'}" for label, code in observations.items()
        )
        conflicts.append(f"{prefix} identity conflict: {detail}")

    chosen = document_canonical or extracted_code or legal_name_code
    if chosen:
        fields[code_key] = chosen
        fields[generic_key] = chosen
    if not conflicts:
        fields.pop("_identity_conflicts", None)


def _apply_document_identity(doc: Document, fact_type: str, fields: dict[str, Any]) -> dict[str, Any]:
    out = dict(fields or {})
    entity_code = _canonical_party(getattr(doc, "entity_code", ""))
    counterparty_code = _canonical_party(getattr(doc, "counterparty_code", ""))

    if fact_type == "contract":
        if getattr(doc, "contract_no", ""):
            out.setdefault("contract_no", doc.contract_no)
        _merge_party_identity(out, "party_a", entity_code)
        _merge_party_identity(out, "party_b", counterparty_code)

    elif fact_type == "invoice":
        direction = _clean(out.get("direction")).lower()
        if not direction:
            if float(getattr(doc, "tax_vat_input", 0) or 0) > 0:
                direction = "in"
            elif float(getattr(doc, "tax_vat_output", 0) or 0) > 0:
                direction = "out"
            if direction:
                out["direction"] = direction
        if getattr(doc, "invoice_no", ""):
            out.setdefault("invoice_no", doc.invoice_no)
        if getattr(doc, "invoice_date", ""):
            out.setdefault("invoice_date", doc.invoice_date)
        if direction == "in":
            _merge_party_identity(out, "buyer", entity_code)
            _merge_party_identity(out, "seller", counterparty_code)
        elif direction == "out":
            _merge_party_identity(out, "seller", entity_code)
            _merge_party_identity(out, "buyer", counterparty_code)
        else:
            _merge_party_identity(out, "seller")
            _merge_party_identity(out, "buyer")

    elif fact_type == "payment":
        direction = _clean(out.get("direction")).lower()
        if direction == "out":
            _merge_party_identity(out, "payer", entity_code)
            _merge_party_identity(out, "payee", counterparty_code)
        elif direction == "in":
            _merge_party_identity(out, "payee", entity_code)
            _merge_party_identity(out, "payer", counterparty_code)
        else:
            _merge_party_identity(out, "payer")
            _merge_party_identity(out, "payee")
        if getattr(doc, "contract_no", ""):
            out.setdefault("contract_no", doc.contract_no)

    return out


def _business_key(doc: Document, fact_type: str, fields: dict[str, Any]) -> str:
    if fact_type == "contract":
        return _clean(fields.get("contract_no")) or f"document:{doc.id}:contract"
    if fact_type == "invoice":
        invoice_no = _clean(fields.get("invoice_no"))
        seller = _canonical_party(fields.get("seller_entity_code") or fields.get("seller_code"))
        invoice_code = _clean(fields.get("invoice_code")) or "-"
        if invoice_no and seller:
            return f"invoice:{seller}:{invoice_code}:{invoice_no}"
        return f"document:{doc.id}:invoice"

    # Two legitimate transfers can have the same date, parties and amount.
    # Only a bank reference is strong enough for cross-document dedupe; without
    # one, keep document identity so the SSOT never silently merges cash facts.
    reference = _clean(fields.get("payer_bank_reference") or fields.get("payee_bank_reference"))
    if not reference:
        return f"document:{doc.id}:payment"
    pieces = [
        _clean(fields.get("payment_date")),
        _clean(fields.get("payer_entity_code")),
        _clean(fields.get("payee_entity_code")),
        _clean(fields.get("amount")),
        reference,
    ]
    material = "|".join(pieces)
    return "payment:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _validate(fact_type: str, fields: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if fields.get("_extraction_error"):
        errors.append(str(fields["_extraction_error"]))
    for item in fields.get("_identity_conflicts") or []:
        errors.append(str(item))

    if fact_type == "contract":
        if not _clean(fields.get("contract_no")):
            errors.append("contract_no missing")
        for key in ("party_a_entity_code", "party_b_entity_code"):
            if not _known_party(fields.get(key) or fields.get(key.replace("_entity", ""))):
                errors.append(f"{key} unresolved")
        amount = _money(fields.get("total_amount"))
        if amount is None or amount <= 0:
            errors.append("total_amount must be positive")

    elif fact_type == "invoice":
        if not _clean(fields.get("invoice_no")):
            errors.append("invoice_no missing")
        for key in ("seller_entity_code", "buyer_entity_code"):
            if not _known_party(fields.get(key) or fields.get(key.replace("_entity", ""))):
                errors.append(f"{key} unresolved")
        net = _money(fields.get("net_amount"))
        total = _money(fields.get("total_amount"))
        if net is None and total is None:
            errors.append("invoice amount missing")
        validation_status = _clean(fields.get("validation_status")).upper()
        if validation_status in {"PENDING_REVIEW", "UNVALIDATED"}:
            errors.append(f"invoice validation_status={validation_status}")
        for item in fields.get("validation_errors") or []:
            errors.append(str(item))

    elif fact_type == "payment":
        for key in ("payer_entity_code", "payee_entity_code"):
            if not _known_party(fields.get(key)):
                errors.append(f"{key} unresolved")
        amount = _money(fields.get("amount"))
        if amount is None or amount <= 0:
            errors.append("payment amount must be positive")
        if not _clean(fields.get("payment_date")):
            errors.append("payment_date missing")

    return list(dict.fromkeys(errors))


def build_candidate(doc: Document, fact_type: str, fields: dict[str, Any]) -> CanonicalFactCandidate:
    payload = _apply_document_identity(doc, fact_type, fields)
    errors = _validate(fact_type, payload)
    status = "accepted" if not errors else "needs_review"
    confidence = Decimal("1") if status == "accepted" else Decimal(str(getattr(doc, "metadata_confidence", 0) or 0))
    confidence = min(Decimal("1"), max(Decimal("0"), confidence))
    stable = {
        "document_file_hash": _clean(getattr(doc, "file_hash", "")),
        "fact_type": fact_type,
        "schema_version": _FACT_SCHEMA_VERSION,
        "validation_policy_version": _VALIDATION_POLICY_VERSION,
        "payload": payload,
    }
    source_hash = hashlib.sha256(
        json.dumps(stable, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    return CanonicalFactCandidate(
        fact_type=fact_type,
        business_key=_business_key(doc, fact_type, payload),
        payload=payload,
        evidence=evidence,
        validation_errors=errors,
        confidence=confidence,
        status=status,
        source_hash=source_hash,
    )


def _acquire_business_key_lock(db, project_id: int, fact_type: str, business_key: str) -> None:
    """Serialize one business identity before version/current transitions."""
    try:
        bind = db.get_bind()
    except Exception:
        bind = getattr(db, "bind", None)
    dialect = getattr(getattr(bind, "dialect", None), "name", "")
    if dialect == "postgresql":
        lock_key = f"canonical_fact|{project_id}|{fact_type}|{business_key}"
        db.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtextextended(CAST(:lock_key AS text), 0))"
            ),
            {"lock_key": lock_key},
        )


def _stale_replay_candidate(doc: Document, candidate: CanonicalFactCandidate) -> CanonicalFactCandidate:
    reason = "stale source document replay blocked; current higher version retained"
    confidence = Decimal(str(getattr(doc, "metadata_confidence", 0) or 0))
    confidence = min(Decimal("0.99999"), max(Decimal("0"), confidence))
    return replace(
        candidate,
        status="needs_review",
        validation_errors=list(dict.fromkeys([*candidate.validation_errors, reason])),
        confidence=confidence,
    )


def _persist_candidate(db, doc: Document, candidate: CanonicalFactCandidate) -> int:
    _acquire_business_key_lock(
        db,
        int(doc.project_id),
        candidate.fact_type,
        candidate.business_key,
    )

    existing = db.execute(
        text(
            "SELECT id, fact_version, status, is_current FROM canonical_facts "
            "WHERE source_document_id=:document_id AND fact_type=:fact_type AND source_hash=:source_hash "
            "FOR UPDATE"
        ),
        {"document_id": doc.id, "fact_type": candidate.fact_type, "source_hash": candidate.source_hash},
    ).mappings().first()

    if existing and existing["status"] == candidate.status:
        return int(existing["id"])

    # A source that has already been superseded is historical evidence.
    # Exact replay must never reactivate it as current.
    if existing and existing["status"] == "superseded":
        return int(existing["id"])

    current = db.execute(
        text(
            "SELECT id, fact_version, source_document_id, source_hash FROM canonical_facts "
            "WHERE project_id=:project_id AND fact_type=:fact_type AND business_key=:business_key "
            "AND status='accepted' AND is_current=TRUE FOR UPDATE"
        ),
        {
            "project_id": doc.project_id,
            "fact_type": candidate.fact_type,
            "business_key": candidate.business_key,
        },
    ).mappings().first()

    prior_superseded_version = int(
        db.execute(
            text(
                "SELECT COALESCE(MAX(fact_version), 0) FROM canonical_facts "
                "WHERE source_document_id=:document_id AND project_id=:project_id "
                "AND fact_type=:fact_type AND business_key=:business_key "
                "AND status='superseded'"
            ),
            {
                "document_id": doc.id,
                "project_id": doc.project_id,
                "fact_type": candidate.fact_type,
                "business_key": candidate.business_key,
            },
        ).scalar_one()
    )

    if (
        candidate.status == "accepted"
        and current
        and int(current["source_document_id"]) != int(doc.id)
        and prior_superseded_version > 0
    ):
        candidate = _stale_replay_candidate(doc, candidate)

    if existing:
        fact_version = int(existing["fact_version"])
    else:
        fact_version = int(
            db.execute(
                text(
                    "SELECT COALESCE(MAX(fact_version), 0) FROM canonical_facts "
                    "WHERE project_id=:project_id AND fact_type=:fact_type AND business_key=:business_key"
                ),
                {"project_id": doc.project_id, "fact_type": candidate.fact_type, "business_key": candidate.business_key},
            ).scalar_one()
        ) + 1

    if candidate.status == "accepted":
        db.execute(
            text(
                "UPDATE canonical_facts SET status='superseded', is_current=FALSE, updated_at=now() "
                "WHERE project_id=:project_id AND fact_type=:fact_type AND business_key=:business_key "
                "AND status='accepted' AND is_current=TRUE "
                "AND NOT (source_document_id=:document_id AND source_hash=:source_hash)"
            ),
            {
                "project_id": doc.project_id,
                "fact_type": candidate.fact_type,
                "business_key": candidate.business_key,
                "document_id": doc.id,
                "source_hash": candidate.source_hash,
            },
        )

    row_id = db.execute(
        text(
            "INSERT INTO canonical_facts ("
            "source_document_id, project_id, fact_type, business_key, schema_version, fact_version, source_hash, "
            "payload, evidence, validation_errors, confidence, status, is_current, producer, accepted_at"
            ") VALUES ("
            ":document_id, :project_id, :fact_type, :business_key, 'v1', :fact_version, :source_hash, "
            "CAST(:payload AS jsonb), CAST(:evidence AS jsonb), CAST(:errors AS jsonb), :confidence, :status, :is_current, "
            "'rag_worker:deterministic-v1', CASE WHEN CAST(:status AS varchar)='accepted' THEN now() ELSE NULL END"
            ") ON CONFLICT (source_document_id, fact_type, source_hash) DO UPDATE SET "
            "payload=EXCLUDED.payload, evidence=EXCLUDED.evidence, validation_errors=EXCLUDED.validation_errors, "
            "confidence=EXCLUDED.confidence, status=EXCLUDED.status, is_current=EXCLUDED.is_current, "
            "accepted_at=CASE WHEN EXCLUDED.status='accepted' THEN COALESCE(canonical_facts.accepted_at, now()) ELSE NULL END, "
            "updated_at=now() RETURNING id"
        ),
        {
            "document_id": doc.id,
            "project_id": doc.project_id,
            "fact_type": candidate.fact_type,
            "business_key": candidate.business_key,
            "fact_version": fact_version,
            "source_hash": candidate.source_hash,
            "payload": json.dumps(candidate.payload, ensure_ascii=False, default=str),
            "evidence": json.dumps(candidate.evidence, ensure_ascii=False, default=str),
            "errors": json.dumps(candidate.validation_errors, ensure_ascii=False),
            "confidence": candidate.confidence,
            "status": candidate.status,
            "is_current": candidate.status == "accepted",
        },
    ).scalar_one()

    if candidate.status == "accepted":
        db.execute(
            text(
                "INSERT INTO canonical_fact_outbox (fact_id, event_type, payload) "
                "VALUES (:fact_id, 'canonical_fact.accepted', CAST(:payload AS jsonb)) "
                "ON CONFLICT (fact_id, event_type) DO NOTHING"
            ),
            {
                "fact_id": row_id,
                "payload": json.dumps(
                    {"project_id": doc.project_id, "fact_type": candidate.fact_type, "business_key": candidate.business_key},
                    ensure_ascii=False,
                ),
            },
        )
    return int(row_id)


def promote_document_to_canonical_facts(db, doc: Document) -> list[int]:
    """Promote one indexed document into the shared canonical fact layer."""
    fact_type = infer_fact_type(getattr(doc, "document_type", ""))
    if not fact_type:
        return []

    chunks = db.scalars(
        select(Chunk.content)
        .where(Chunk.document_id == doc.id)
        .order_by(Chunk.chunk_index.asc())
    ).all()
    content = "\n\n".join(str(item or "") for item in chunks)[:_MAX_FACT_TEXT_CHARS]

    extractors: dict[str, Callable[[str], dict[str, Any]]] = {
        "contract": extract_contract_fields_from_text,
        "invoice": extract_invoice_fields_from_text,
        "payment": extract_payment_fields_from_text,
    }
    try:
        fields = extractors[fact_type](content)
    except Exception as exc:  # extraction failure becomes review, not silent data loss
        fields = {"_extraction_error": f"deterministic extraction failed: {exc}"}

    candidate = build_candidate(doc, fact_type, fields)
    return [_persist_candidate(db, doc, candidate)]


# ============================================================
# 项目编号日期查询
# ============================================================

def get_project_contract_and_payment_dates(db, project_id: int) -> tuple[str | None, str | None]:
    """从 canonical_facts 中查询项目的最早合同日期和第一笔付款日期。

    合同日期：fact_type='contract' 且 payload.contract_date 有值，取最早日期。
    付款日期：fact_type='payment' 且 payload.payment_date 有值，取最早日期。

    Returns (contract_date: str | None, first_payment_date: str | None)，日期格式均为 YYYY-MM-DD。
    """
    # Use raw SQL via text() to safely query JSONB payload
    def min_date_for_type(fact_type: str, date_key: str) -> str | None:
        stmt = text(
            f"""
            SELECT MIN((payload ->> :key)::text)
            FROM canonical_facts
            WHERE project_id = :pid
              AND fact_type = :ft
              AND status = 'accepted'
              AND payload ? :key
              AND (payload ->> :key) IS NOT NULL
              AND (payload ->> :key) ~ :date_regex
            """
        )
        row = db.execute(stmt, {"pid": project_id, "ft": fact_type, "key": date_key, "date_regex": r"^\d{4}-\d{2}-\d{2}$"}).scalar()
        return row if row else None

    contract_date = min_date_for_type("contract", "contract_date")
    payment_date = min_date_for_type("payment", "payment_date")
    return contract_date, payment_date


def update_project_code_and_dates(db, project_id: int) -> str | None:
    """根据 canonical_facts 中的合同/付款日期更新项目编号和日期字段。

    编号规则：地点首字母 + YYYYMMDD（优先合同日期，找不到则用第一笔付款日期）。
    若无日期信息则跳过更新，返回 None。
    若日期已存在则不覆盖（避免文档重扫导致编号变化）。
    """
    from ..models import Project

    project: Project | None = db.get(Project, project_id)
    if not project:
        return None

    contract_date, payment_date = get_project_contract_and_payment_dates(db, project_id)

    # 日期来源：合同日期优先，找不到则用付款日期
    effective_date = contract_date or payment_date

    # 若日期字段已有值，不覆盖
    if effective_date and not project.contract_date and not project.first_payment_date:
        date_str = effective_date.replace("-", "")  # YYYYMMDD
        location_initial = project.location[0] if project.location else "待定"
        new_code = f"{location_initial}{date_str}"
        project.contract_date = contract_date
        project.first_payment_date = payment_date

        # 编号去重：若新编号被占用则追加 -1 -2 ...
        base_code = new_code
        suffix = 1
        while True:
            conflict = db.execute(
                text("SELECT id FROM projects WHERE project_code = :code AND id != :pid"),
                {"code": new_code, "pid": project_id}
            ).scalar()
            if not conflict:
                break
            new_code = f"{base_code}-{suffix}"
            suffix += 1

        project.project_code = new_code
        db.commit()
        return new_code

    return None

