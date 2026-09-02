"""Phase 2 read-only business ledgers backed only by Canonical Facts.

The Tax application owns calculations, not documentary fact persistence.  This
module is therefore deliberately free of ORM references to legacy contracts,
invoices and cashflows tables.  Every source row comes from
``analytics_canonical_facts_current`` and carries its RAG lineage into the API.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import text

from .canonical_ssot import _decimal, _payload, consolidate_invoice_facts, load_current_facts

_CANONICAL_SOURCE = "analytics_canonical_facts_current"
_SUPPORTED_LEDGER_TYPES = frozenset({"contract", "invoice", "payment"})


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _code(value: Any) -> str:
    return _clean(value).upper()


def _number(value: Any) -> float:
    return float(_decimal(value))


def _fact_meta(fact: dict[str, Any]) -> dict[str, Any]:
    return {
        "fact_id": int(fact.get("fact_id") or 0),
        "source_document_id": int(fact.get("source_document_id") or 0),
        "business_key": _clean(fact.get("business_key")),
        "fact_version": int(fact.get("fact_version") or 0),
        "accepted_at": fact.get("accepted_at"),
        "source": _CANONICAL_SOURCE,
        "read_only": True,
    }


def _party_master(db) -> tuple[dict[str, str], set[str], set[str]]:
    names: dict[str, str] = {}
    internal: set[str] = set()
    external: set[str] = set()

    for row in db.execute(
        text("SELECT code, name FROM entities WHERE active = TRUE")
    ).mappings().all():
        code = _code(row.get("code"))
        if code:
            internal.add(code)
            names[code] = _clean(row.get("name")) or code

    for row in db.execute(
        text("SELECT code, name FROM external_parties WHERE active = TRUE")
    ).mappings().all():
        code = _code(row.get("code"))
        if code:
            external.add(code)
            names[code] = _clean(row.get("name")) or code

    return names, internal, external


def _party_name(names: dict[str, str], code: str, fallback: Any = "") -> str:
    return names.get(code) or _clean(fallback) or code


def serialize_contract_fact(fact: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
    payload = _payload(fact.get("payload"))
    party_a = _code(payload.get("party_a_entity_code") or payload.get("party_a_code"))
    party_b = _code(payload.get("party_b_entity_code") or payload.get("party_b_code"))
    return {
        **_fact_meta(fact),
        "contract_no": _clean(payload.get("contract_no")) or _clean(fact.get("business_key")),
        "contract_date": _clean(payload.get("contract_date")),
        "party_a_code": party_a,
        "party_a_name": _party_name(names, party_a, payload.get("party_a_name")),
        "party_b_code": party_b,
        "party_b_name": _party_name(names, party_b, payload.get("party_b_name")),
        "amount": _number(payload.get("total_amount")),
        "tax_included": bool(payload.get("tax_included", True)),
        "category": _clean(payload.get("category")),
        "note": _clean(payload.get("note")),
    }


def serialize_invoice_fact(fact: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
    payload = _payload(fact.get("payload"))
    seller = _code(payload.get("seller_entity_code") or payload.get("seller_code"))
    buyer = _code(payload.get("buyer_entity_code") or payload.get("buyer_code"))
    vat = _decimal(payload.get("vat_amount"))
    net = _decimal(payload.get("net_amount"))
    total = _decimal(payload.get("total_amount"))
    if net == 0 and payload.get("total_amount") is not None:
        net = total - vat
    if total == 0 and (net != 0 or vat != 0):
        total = net + vat
    return {
        **_fact_meta(fact),
        "invoice_no": _clean(payload.get("invoice_no")) or _clean(fact.get("business_key")),
        "invoice_code": _clean(payload.get("invoice_code")),
        "invoice_date": _clean(payload.get("invoice_date")),
        "direction": _clean(payload.get("direction")).lower(),
        "seller_code": seller,
        "seller_name": _party_name(names, seller, payload.get("seller_name")),
        "buyer_code": buyer,
        "buyer_name": _party_name(names, buyer, payload.get("buyer_name")),
        "net_amount": float(net),
        "vat_amount": float(vat),
        "total_amount": float(total),
        "deductible": bool(payload.get("deductible", True)),
        "category": _clean(payload.get("category")),
        "note": _clean(payload.get("note")),
    }


def serialize_payment_fact(fact: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
    payload = _payload(fact.get("payload"))
    payer = _code(payload.get("payer_entity_code"))
    payee = _code(payload.get("payee_entity_code"))
    return {
        **_fact_meta(fact),
        "payment_date": _clean(payload.get("payment_date")),
        "direction": _clean(payload.get("direction")).lower(),
        "payer_code": payer,
        "payer_name": _party_name(names, payer, payload.get("payer_name")),
        "payee_code": payee,
        "payee_name": _party_name(names, payee, payload.get("payee_name")),
        "amount": _number(payload.get("amount")),
        "payment_method": _clean(payload.get("payment_method")),
        "contract_no": _clean(payload.get("contract_no")),
        "bank_reference": _clean(
            payload.get("payer_bank_reference") or payload.get("payee_bank_reference")
        ),
        "note": _clean(payload.get("note")),
    }


def project_ledger(db, project_id: int, fact_type: str) -> list[dict[str, Any]]:
    if fact_type not in _SUPPORTED_LEDGER_TYPES:
        raise ValueError(f"unsupported canonical ledger type: {fact_type}")
    names, _internal, _external = _party_master(db)
    facts = load_current_facts(db, project_id, fact_type)
    serializers = {
        "contract": serialize_contract_fact,
        "invoice": serialize_invoice_fact,
        "payment": serialize_payment_fact,
    }
    serializer = serializers[fact_type]
    return [serializer(fact, names) for fact in facts]


def _new_party_row(
    code: str,
    names: dict[str, str],
    internal_codes: set[str],
    external_codes: set[str],
) -> dict[str, Any]:
    kind = "entity" if code in internal_codes else "external" if code in external_codes else "unknown"
    source = "entities" if kind == "entity" else "external_parties" if kind == "external" else "unknown"
    return {
        "party_code": code,
        "party_name": names.get(code, code),
        "kind": kind,
        "isInternal": code in internal_codes,
        "source": source,
        "contract_count": 0,
        "contract_amount": 0.0,
        "invoice_in_count": 0,
        "invoice_in_net": 0.0,
        "invoice_in_vat": 0.0,
        "invoice_out_count": 0,
        "invoice_out_net": 0.0,
        "invoice_out_vat": 0.0,
        "cashflow_in_count": 0,
        "cashflow_in_amount": 0.0,
        "cashflow_out_count": 0,
        "cashflow_out_amount": 0.0,
        "real_cost_count": 0,
        "real_cost_amount": 0.0,
        # Fulfilment is not yet a Canonical Fact type.  Phase 2 refuses to
        # backfill this number from a legacy table merely to make the UI look complete.
        "fulfillment_count": 0,
        "fulfillment_amount": 0.0,
    }


def aggregate_counterparties_from_facts(
    contracts: Iterable[dict[str, Any]],
    invoices: Iterable[dict[str, Any]],
    payments: Iterable[dict[str, Any]],
    *,
    names: dict[str, str],
    internal_codes: set[str],
    external_codes: set[str],
) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}

    def row(code: Any, fallback_name: Any = "") -> dict[str, Any] | None:
        canonical = _code(code)
        if not canonical:
            return None
        if canonical not in names and fallback_name:
            names[canonical] = _clean(fallback_name) or canonical
        return rows.setdefault(
            canonical,
            _new_party_row(canonical, names, internal_codes, external_codes),
        )

    for fact in contracts:
        payload = _payload(fact.get("payload"))
        amount = _number(payload.get("total_amount"))
        parties = (
            (payload.get("party_a_entity_code") or payload.get("party_a_code"), payload.get("party_a_name")),
            (payload.get("party_b_entity_code") or payload.get("party_b_code"), payload.get("party_b_name")),
        )
        seen: set[str] = set()
        for code, fallback_name in parties:
            canonical = _code(code)
            if not canonical or canonical in seen:
                continue
            seen.add(canonical)
            item = row(canonical, fallback_name)
            if item is not None:
                item["contract_count"] += 1
                item["contract_amount"] += amount

    for fact in invoices:
        payload = _payload(fact.get("payload"))
        seller = _code(payload.get("seller_entity_code") or payload.get("seller_code"))
        buyer = _code(payload.get("buyer_entity_code") or payload.get("buyer_code"))
        net = _decimal(payload.get("net_amount"))
        vat = _decimal(payload.get("vat_amount"))
        if net == 0 and payload.get("total_amount") is not None:
            net = _decimal(payload.get("total_amount")) - vat
        direction = _clean(payload.get("direction")).lower()
        if direction not in {"in", "out"}:
            if seller not in internal_codes and buyer in internal_codes:
                direction = "in"
            elif seller in internal_codes and buyer not in internal_codes:
                direction = "out"

        parties = (
            (seller, payload.get("seller_name")),
            (buyer, payload.get("buyer_name")),
        )
        seen: set[str] = set()
        for code, fallback_name in parties:
            canonical = _code(code)
            if not canonical or canonical in seen:
                continue
            seen.add(canonical)
            item = row(canonical, fallback_name)
            if item is None or direction not in {"in", "out"}:
                continue
            item[f"invoice_{direction}_count"] += 1
            item[f"invoice_{direction}_net"] += float(net)
            item[f"invoice_{direction}_vat"] += float(vat)

        # Real cost is an economic-boundary metric, not a copy of invoice volume.
        # Only external -> internal input enters the project-wide true resource cost.
        if seller and seller not in internal_codes and buyer in internal_codes:
            supplier = row(seller, payload.get("seller_name"))
            if supplier is not None:
                nondeductible_vat = Decimal("0") if bool(payload.get("deductible", True)) else vat
                supplier["real_cost_count"] += 1
                supplier["real_cost_amount"] += float(net + nondeductible_vat)

    for fact in payments:
        payload = _payload(fact.get("payload"))
        payer = _code(payload.get("payer_entity_code"))
        payee = _code(payload.get("payee_entity_code"))
        amount = _number(payload.get("amount"))
        direction = _clean(payload.get("direction")).lower()
        if direction not in {"in", "out"}:
            if payer in internal_codes and payee not in internal_codes:
                direction = "out"
            elif payer not in internal_codes and payee in internal_codes:
                direction = "in"

        parties = (
            (payer, payload.get("payer_name")),
            (payee, payload.get("payee_name")),
        )
        seen: set[str] = set()
        for code, fallback_name in parties:
            canonical = _code(code)
            if not canonical or canonical in seen:
                continue
            seen.add(canonical)
            item = row(canonical, fallback_name)
            if item is None or direction not in {"in", "out"}:
                continue
            item[f"cashflow_{direction}_count"] += 1
            item[f"cashflow_{direction}_amount"] += amount

    return sorted(
        rows.values(),
        key=lambda item: (0 if item["isInternal"] else 1, item["party_code"]),
    )


def project_counterparties(db, project_id: int) -> dict[str, Any]:
    names, internal_codes, external_codes = _party_master(db)
    contracts = load_current_facts(db, project_id, "contract")
    invoices = load_current_facts(db, project_id, "invoice")
    payments = load_current_facts(db, project_id, "payment")
    items = aggregate_counterparties_from_facts(
        contracts,
        invoices,
        payments,
        names=names,
        internal_codes=internal_codes,
        external_codes=external_codes,
    )
    return {
        "status": "READY" if items else "EMPTY",
        "message": "" if items else "该项目当前没有已验收的 Canonical 合同/发票/收付款事实。",
        "items": items,
        "total": len(items),
        "source_of_truth": _CANONICAL_SOURCE,
        "legacy_tables_used": False,
        "manual_party_creation": False,
    }


def project_ledger_bundle(db, project_id: int) -> dict[str, Any]:
    names, internal_codes, external_codes = _party_master(db)
    contract_facts = load_current_facts(db, project_id, "contract")
    invoice_facts = load_current_facts(db, project_id, "invoice")
    payment_facts = load_current_facts(db, project_id, "payment")
    review_count = int(
        db.execute(
            text(
                "SELECT count(*) FROM analytics_canonical_fact_review_queue "
                "WHERE project_id=:project_id AND fact_type IN ('contract','invoice','payment')"
            ),
            {"project_id": project_id},
        ).scalar_one()
    )
    counterparties = aggregate_counterparties_from_facts(
        contract_facts,
        invoice_facts,
        payment_facts,
        names=names,
        internal_codes=internal_codes,
        external_codes=external_codes,
    )
    boundary = consolidate_invoice_facts(invoice_facts, internal_codes)
    return {
        "project_id": project_id,
        "source_of_truth": _CANONICAL_SOURCE,
        "read_only": True,
        "legacy_tables_used": False,
        "manual_party_creation": False,
        "contracts": [serialize_contract_fact(fact, names) for fact in contract_facts],
        "invoices": [serialize_invoice_fact(fact, names) for fact in invoice_facts],
        "cash_flows": [serialize_payment_fact(fact, names) for fact in payment_facts],
        "counterparties": counterparties,
        "counts": {
            "contracts": len(contract_facts),
            "invoices": len(invoice_facts),
            "cash_flows": len(payment_facts),
            "needs_review": review_count,
        },
        "boundary": boundary,
    }


def _canonical_invoice_period(payload: dict[str, Any]) -> str:
    explicit = _clean(payload.get("period"))
    if len(explicit) == 7 and explicit[4] == "-":
        return explicit

    invoice_date = _clean(payload.get("invoice_date"))
    if len(invoice_date) >= 7 and invoice_date[4] == "-":
        return invoice_date[:7]

    return ""


def project_tax_analysis_summary(
    db,
    project_id: int,
    *,
    period: str | None = None,
    entity_code: str | None = None,
) -> dict[str, Any]:
    """Project tax projection backed only by accepted/current Canonical Facts."""

    _names, internal_codes, _external_codes = _party_master(db)
    facts = load_current_facts(db, int(project_id), "invoice")

    wanted_entity = _code(entity_code) if entity_code else ""

    out_invoice_net = Decimal("0")
    out_invoice_vat = Decimal("0")
    in_invoice_net = Decimal("0")
    in_invoice_vat = Decimal("0")
    deductible_input_vat = Decimal("0")
    real_cost = Decimal("0")

    invoice_count = 0
    included_fact_ids: list[int] = []
    data_gaps: set[str] = set()

    for fact in facts:
        payload = _payload(fact.get("payload"))

        seller = _code(
            payload.get("seller_entity_code")
            or payload.get("seller_code")
        )
        buyer = _code(
            payload.get("buyer_entity_code")
            or payload.get("buyer_code")
        )

        # Canonical invoice Facts without both parties cannot safely
        # participate in a deterministic project tax projection.
        if not seller or not buyer:
            data_gaps.add("CANONICAL_INVOICE_PARTY_MISSING")
            continue

        if wanted_entity and wanted_entity not in {seller, buyer}:
            continue

        if period:
            fact_period = _canonical_invoice_period(payload)
            if not fact_period:
                data_gaps.add("CANONICAL_INVOICE_PERIOD_MISSING")
                continue
            if fact_period != period:
                continue

        net = _decimal(payload.get("net_amount"))
        vat = _decimal(payload.get("vat_amount"))

        if net == 0 and payload.get("total_amount") is not None:
            net = _decimal(payload.get("total_amount")) - vat

        deductible = payload.get("deductible") is True

        seller_in_scope = (
            seller in internal_codes
            and (not wanted_entity or seller == wanted_entity)
        )
        buyer_in_scope = (
            buyer in internal_codes
            and (not wanted_entity or buyer == wanted_entity)
        )

        # Internal -> internal contributes once to each legal-side
        # project projection: output for seller and input for buyer.
        if seller_in_scope:
            out_invoice_net += net
            out_invoice_vat += vat

        if buyer_in_scope:
            in_invoice_net += net
            in_invoice_vat += vat
            if deductible:
                deductible_input_vat += vat

        # Real cost follows the canonical economic boundary rule:
        # external seller -> internal buyer only.
        if buyer_in_scope and seller not in internal_codes:
            real_cost += net
            if not deductible:
                real_cost += vat

        if seller_in_scope or buyer_in_scope:
            invoice_count += 1
            included_fact_ids.append(int(fact.get("fact_id") or 0))

    return {
        "status": "DEGRADED" if data_gaps else "READY",
        "project_id": int(project_id),
        "period": period or "",
        "entity_code": wanted_entity or None,
        "out_invoice_net": _number(out_invoice_net),
        "out_invoice_vat": _number(out_invoice_vat),
        "in_invoice_net": _number(in_invoice_net),
        "in_invoice_vat": _number(in_invoice_vat),
        "deductible_input_vat": _number(deductible_input_vat),
        "real_cost": _number(real_cost),
        "invoice_count": invoice_count,
        "fact_ids": included_fact_ids,
        "data_gaps": sorted(data_gaps),
        "source_of_truth": _CANONICAL_SOURCE,
        "legacy_tables_used": False,
        "real_cost_basis": "external_boundary_invoice_cost",
        "read_only": True,
    }
