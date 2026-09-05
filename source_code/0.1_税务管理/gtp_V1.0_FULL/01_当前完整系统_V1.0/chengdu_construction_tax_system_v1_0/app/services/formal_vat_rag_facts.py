"""Canonical RAG invoice VAT observation and deterministic Formal VAT mirrors.

``analytics_canonical_facts_current`` remains the business-fact source of truth.
The local Fact/InvoiceFact + OutputVatEvent/InputVatClaim rows created here are
only statutory-working mirrors so the already-audited Formal VAT rebuild and
closed-loop component model can keep its typed foreign keys and lineage.

Reads never materialize. Materialization happens only after an authenticated
operator explicitly confirms a completeness preview.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any

from sqlalchemy import func, select, text

from app.v3_fact_models import Fact, InvoiceFact
from app.v3_party_models import InternalEntity
from app.v3_tax_models import InputVatClaim
from app.v3_vat_ledger_models import OutputVatEvent

MONEY = Decimal("0.01")
SOURCE_SYSTEM = "RAG_CANONICAL_FACT"
MIRROR_PREFIX = "rag-canonical-invoice:"


def _period(value: str | date) -> date:
    if isinstance(value, date):
        parsed = value
    else:
        normalized = str(value or "").strip()
        if len(normalized) == 7:
            normalized = f"{normalized}-01"
        parsed = date.fromisoformat(normalized)
    if parsed.day != 1:
        raise ValueError("period must be YYYY-MM")
    return parsed


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _money(value: Any) -> Decimal:
    try:
        result = Decimal(str(value if value is not None else 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0.00")
    if not result.is_finite():
        return Decimal("0.00")
    return result.quantize(MONEY)


def _code(payload: dict[str, Any], side: str) -> str:
    return str(
        payload.get(f"{side}_entity_code")
        or payload.get(f"{side}_code")
        or ""
    ).strip().upper()


def _invoice_period(payload: dict[str, Any]) -> str:
    raw = str(
        payload.get("invoice_date")
        or payload.get("transaction_date")
        or payload.get("date")
        or payload.get("period")
        or ""
    ).strip()
    return raw[:7] if len(raw) >= 7 else ""


def _vat_amount(payload: dict[str, Any]) -> Decimal:
    for key in ("vat_amount", "tax_amount", "tax"):
        if payload.get(key) is not None:
            return _money(payload.get(key))
    return Decimal("0.00")


def summarize_rag_invoice_rows(
    rows: list[dict[str, Any]],
    *,
    entity_code: str,
    period: str | date,
) -> dict[str, Any]:
    """Summarize one legal-entity/month directly from current Canonical Facts."""
    wanted = str(entity_code or "").strip().upper()
    tax_period = _period(period)
    period_label = tax_period.strftime("%Y-%m")
    normalized_rows: list[dict[str, Any]] = []
    output_total = Decimal("0.00")
    input_total = Decimal("0.00")
    output_count = 0
    input_count = 0

    for raw in rows:
        payload = _payload(raw.get("payload"))
        if _invoice_period(payload) != period_label:
            continue
        seller = _code(payload, "seller")
        buyer = _code(payload, "buyer")
        if seller != wanted and buyer != wanted:
            continue
        vat = _vat_amount(payload)
        item = {
            "fact_id": raw.get("fact_id"),
            "business_key": str(raw.get("business_key") or ""),
            "fact_version": int(raw.get("fact_version") or 0),
            "source_hash": str(raw.get("source_hash") or ""),
            "payload": payload,
            "seller_code": seller,
            "buyer_code": buyer,
            "vat_amount": vat,
        }
        normalized_rows.append(item)
        if seller == wanted and vat != 0:
            output_total += vat
            output_count += 1
        if buyer == wanted and vat != 0:
            input_total += vat
            input_count += 1

    digest_payload = [
        {
            "fact_id": row["fact_id"],
            "business_key": row["business_key"],
            "fact_version": row["fact_version"],
            "source_hash": row["source_hash"],
            "seller_code": row["seller_code"],
            "buyer_code": row["buyer_code"],
            "vat_amount": f"{row['vat_amount']:.2f}",
        }
        for row in normalized_rows
    ]
    rendered = json.dumps(
        digest_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return {
        "source": "analytics_canonical_facts_current",
        "entity_code": wanted,
        "period": period_label,
        "invoice_fact_count": len(normalized_rows),
        "output_fact_count": output_count,
        "input_fact_count": input_count,
        "output_vat_total": output_total.quantize(MONEY),
        "input_vat_total": input_total.quantize(MONEY),
        "snapshot_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        "rows": normalized_rows,
    }


def load_rag_vat_observation(db, entity_code: str, period: str | date) -> dict[str, Any]:
    tax_period = _period(period)
    period_label = tax_period.strftime("%Y-%m")
    wanted = str(entity_code or "").strip().upper()
    rows = db.execute(
        text(
            "SELECT fact_id, business_key, fact_version, source_hash, payload "
            "FROM analytics_canonical_facts_current "
            "WHERE lower(fact_type) = 'invoice' "
            "AND left(btrim(COALESCE(NULLIF(payload::jsonb ->> 'invoice_date',''), "
            "NULLIF(payload::jsonb ->> 'transaction_date',''), NULLIF(payload::jsonb ->> 'date',''), "
            "payload::jsonb ->> 'period', '')), 7) = :period "
            "AND ("
            "UPPER(btrim(COALESCE(NULLIF(payload::jsonb ->> 'seller_entity_code',''), payload::jsonb ->> 'seller_code',''))) = :entity_code "
            "OR UPPER(btrim(COALESCE(NULLIF(payload::jsonb ->> 'buyer_entity_code',''), payload::jsonb ->> 'buyer_code',''))) = :entity_code"
            ") ORDER BY business_key, fact_version, fact_id"
        ),
        {"period": period_label, "entity_code": wanted},
    ).mappings().all()
    return summarize_rag_invoice_rows(
        [dict(row) for row in rows],
        entity_code=wanted,
        period=tax_period,
    )


def list_rag_vat_scopes(db, entity_code: str | None = None) -> list[dict[str, str]]:
    """Return legal-entity/month scopes which have current Canonical invoice facts."""
    wanted = str(entity_code or "").strip().upper()
    legal_codes = {
        str(code).strip().upper()
        for code in db.scalars(
            select(InternalEntity.canonical_code).where(
                InternalEntity.active.is_(True),
                InternalEntity.legal_entity.is_(True),
            )
        ).all()
        if code
    }
    if wanted and wanted != "ALL":
        legal_codes &= {wanted}
    if not legal_codes:
        return []

    rows = db.execute(
        text(
            "SELECT payload FROM analytics_canonical_facts_current "
            "WHERE lower(fact_type)='invoice' ORDER BY business_key, fact_version, fact_id"
        )
    ).scalars().all()
    scopes: set[tuple[str, str]] = set()
    for value in rows:
        payload = _payload(value)
        period_label = _invoice_period(payload)
        if len(period_label) != 7:
            continue
        seller = _code(payload, "seller")
        buyer = _code(payload, "buyer")
        if seller in legal_codes:
            scopes.add((seller, period_label))
        if buyer in legal_codes:
            scopes.add((buyer, period_label))
    return [
        {"entity_code": code, "period": period_label}
        for code, period_label in sorted(scopes, key=lambda item: (item[0], item[1]))
    ]


def _internal_party_id(db, canonical_code: str) -> int | None:
    return db.scalar(
        select(InternalEntity.party_id).where(
            func.upper(InternalEntity.canonical_code) == str(canonical_code or "").strip().upper(),
            InternalEntity.active.is_(True),
        )
    )


def _mirror_digest(row: dict[str, Any]) -> str:
    identity = "|".join(
        [
            str(row.get("fact_id") or ""),
            str(row.get("business_key") or ""),
            str(row.get("fact_version") or ""),
            str(row.get("source_hash") or ""),
            f"{_money(row.get('vat_amount')):.2f}",
        ]
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _ensure_invoice_mirror(db, row: dict[str, Any], tax_period: date) -> InvoiceFact:
    digest = _mirror_digest(row)
    identity = f"{MIRROR_PREFIX}{digest}"
    fact = db.scalar(
        select(Fact).where(
            Fact.business_identity_key == identity,
            Fact.version_no == 1,
        )
    )
    payload = row["payload"]
    if fact is None:
        fact = Fact(
            fact_type="INVOICE",
            business_identity_key=identity,
            version_no=1,
            is_current=True,
            validation_status="VALID",
        )
        db.add(fact)
        db.flush()

    invoice = db.get(InvoiceFact, fact.id)
    if invoice is None:
        invoice_date_raw = str(payload.get("invoice_date") or "").strip()
        try:
            invoice_date = date.fromisoformat(invoice_date_raw[:10]) if len(invoice_date_raw) >= 10 else tax_period
        except ValueError:
            invoice_date = tax_period
        invoice_no = str(
            payload.get("invoice_no")
            or payload.get("invoice_number")
            or row.get("business_key")
            or digest[:20]
        ).strip()[:100]
        seller_party_id = _internal_party_id(db, row.get("seller_code") or "")
        buyer_party_id = _internal_party_id(db, row.get("buyer_code") or "")
        vat = _money(row.get("vat_amount"))
        net = _money(payload.get("net_amount")) if payload.get("net_amount") is not None else None
        gross_value = payload.get("gross_amount")
        if gross_value is None:
            gross_value = payload.get("amount_with_tax")
        if gross_value is None:
            gross_value = payload.get("total_amount")
        gross = _money(gross_value) if gross_value is not None else None
        invoice = InvoiceFact(
            fact_id=fact.id,
            seller_party_id=seller_party_id,
            buyer_party_id=buyer_party_id,
            invoice_identity_key=identity,
            invoice_identity_version="RAGCF_V1",
            invoice_number=invoice_no or digest[:20],
            invoice_date=invoice_date,
            invoice_status="VALID",
            gross_amount=gross,
            net_amount=net,
            vat_amount=vat,
            currency=str(payload.get("currency") or "CNY")[:3].upper(),
        )
        db.add(invoice)
        db.flush()
    return invoice


def materialize_rag_vat_evidence(
    db,
    *,
    reporting_party_id: int,
    entity_code: str,
    period: str | date,
    reviewed_by: str,
    observation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create/update statutory working mirrors for one reviewed RAG scope."""
    tax_period = _period(period)
    actor = str(reviewed_by or "").strip()[:80]
    if not actor:
        raise ValueError("reviewed_by is required")
    observed = observation or load_rag_vat_observation(db, entity_code, tax_period)
    now = datetime.now(timezone.utc)
    expected_output_ids: set[str] = set()
    expected_input_ids: set[str] = set()

    for row in observed["rows"]:
        vat = _money(row.get("vat_amount"))
        if vat == 0:
            continue
        invoice = _ensure_invoice_mirror(db, row, tax_period)
        digest = _mirror_digest(row)
        if row.get("seller_code") == observed["entity_code"]:
            external_id = f"OUT:{digest}"
            expected_output_ids.add(external_id)
            event = db.scalar(
                select(OutputVatEvent).where(
                    OutputVatEvent.source_system == SOURCE_SYSTEM,
                    OutputVatEvent.external_event_id == external_id,
                )
            )
            if event is None:
                event = OutputVatEvent(
                    invoice_fact_id=invoice.fact_id,
                    reporting_party_id=reporting_party_id,
                    output_vat_period=tax_period,
                    vat_amount=vat,
                    event_type="OUTPUT" if vat > 0 else "REVERSAL",
                    event_status="CONFIRMED",
                    evidence_type="MANUAL_REVIEW",
                    confidence="HIGH",
                    source_system=SOURCE_SYSTEM,
                    external_event_id=external_id,
                    reviewed_by=actor,
                    reviewed_at=now,
                    note="Derived statutory mirror of operator-confirmed current RAG Canonical invoice fact.",
                )
                db.add(event)
            else:
                event.event_status = "CONFIRMED"
                event.reviewed_by = actor
                event.reviewed_at = now

        if row.get("buyer_code") == observed["entity_code"]:
            external_id = f"IN:{digest}"
            expected_input_ids.add(external_id)
            claim = db.scalar(
                select(InputVatClaim).where(
                    InputVatClaim.source_system == SOURCE_SYSTEM,
                    InputVatClaim.external_claim_id == external_id,
                )
            )
            if claim is None:
                claim = InputVatClaim(
                    invoice_fact_id=invoice.fact_id,
                    reporting_party_id=reporting_party_id,
                    claim_period=tax_period,
                    claim_amount=vat,
                    event_type="CLAIM" if vat > 0 else "REVERSAL",
                    claim_status="CONFIRMED",
                    evidence_type="MANUAL_REVIEW",
                    confidence="HIGH",
                    source_system=SOURCE_SYSTEM,
                    external_claim_id=external_id,
                    reviewed_by=actor,
                    reviewed_at=now,
                    note="Derived statutory mirror of operator-confirmed current RAG Canonical invoice fact.",
                )
                db.add(claim)
            else:
                claim.claim_status = "CONFIRMED"
                claim.reviewed_by = actor
                claim.reviewed_at = now

    stale_outputs = db.scalars(
        select(OutputVatEvent).where(
            OutputVatEvent.reporting_party_id == reporting_party_id,
            OutputVatEvent.output_vat_period == tax_period,
            OutputVatEvent.source_system == SOURCE_SYSTEM,
            OutputVatEvent.event_status == "CONFIRMED",
        )
    ).all()
    for event in stale_outputs:
        if event.external_event_id not in expected_output_ids:
            event.event_status = "SUPERSEDED"
            event.reviewed_by = actor
            event.reviewed_at = now
            event.note = "Superseded because this RAG Canonical fact is no longer current for the reviewed scope."

    stale_inputs = db.scalars(
        select(InputVatClaim).where(
            InputVatClaim.reporting_party_id == reporting_party_id,
            InputVatClaim.claim_period == tax_period,
            InputVatClaim.source_system == SOURCE_SYSTEM,
            InputVatClaim.claim_status == "CONFIRMED",
        )
    ).all()
    for claim in stale_inputs:
        if claim.external_claim_id not in expected_input_ids:
            claim.claim_status = "SUPERSEDED"
            claim.reviewed_by = actor
            claim.reviewed_at = now
            claim.note = "Superseded because this RAG Canonical fact is no longer current for the reviewed scope."

    db.flush()
    output_total = db.scalar(
        select(func.coalesce(func.sum(OutputVatEvent.vat_amount), 0)).where(
            OutputVatEvent.reporting_party_id == reporting_party_id,
            OutputVatEvent.output_vat_period == tax_period,
            OutputVatEvent.event_status == "CONFIRMED",
        )
    )
    input_total = db.scalar(
        select(func.coalesce(func.sum(InputVatClaim.claim_amount), 0)).where(
            InputVatClaim.reporting_party_id == reporting_party_id,
            InputVatClaim.claim_period == tax_period,
            InputVatClaim.claim_status == "CONFIRMED",
        )
    )
    return {
        "output_vat_total": _money(output_total),
        "input_vat_total": _money(input_total),
        "output_event_count": len(expected_output_ids),
        "input_claim_count": len(expected_input_ids),
    }


__all__ = [
    "SOURCE_SYSTEM",
    "list_rag_vat_scopes",
    "load_rag_vat_observation",
    "materialize_rag_vat_evidence",
    "summarize_rag_invoice_rows",
]
