"""Deterministic planning rules for the Task 08 legacy invoice pilot.

The legacy table lacks the legal identity fields required by ``LEGACY_V1``
(invoice code + seller tax identity).  Task 08 therefore uses a migration-only
identity namespace and keeps every created Fact in ``NEEDS_REVIEW``.  Task 09,
not this module, owns legal invoice validation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
import hashlib
import json
import unicodedata
from typing import Iterable, Mapping


MIGRATION_IDENTITY_VERSION = "LEGACY_MIGRATION_V1"


@dataclass(frozen=True)
class LegacyInvoiceSnapshot:
    id: int
    project_id: int
    invoice_no: str
    period: str
    entity_code: str
    direction: str
    counterparty_code: str
    category: str
    net: Decimal
    vat: Decimal
    rate: Decimal
    deductible: bool
    note: str = ""


@dataclass(frozen=True)
class PartyRef:
    party_id: int
    code: str
    party_type: str

    @property
    def internal(self) -> bool:
        return self.party_type == "internal"


@dataclass(frozen=True)
class ResolvedLegacyInvoice:
    row: LegacyInvoiceSnapshot
    seller: PartyRef
    buyer: PartyRef


@dataclass(frozen=True)
class PilotAction:
    action: str
    legacy_ids: tuple[int, ...]
    migration_status: str
    reason: str
    seller_party_id: int | None = None
    buyer_party_id: int | None = None
    identity_key: str | None = None

    @property
    def creates_fact(self) -> bool:
        return self.action in {"MERGE_PAIR", "MIGRATE_SINGLE"}


@dataclass(frozen=True)
class PilotPlan:
    selected_ids: tuple[int, ...]
    actions: tuple[PilotAction, ...]

    @property
    def covered_ids(self) -> tuple[int, ...]:
        return tuple(sorted(i for action in self.actions for i in action.legacy_ids))


def normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", "" if value is None else str(value))
    return "".join(text.split()).upper()


def normalize_direction(value: object) -> str:
    value = normalize_text(value).lower()
    if value in {"in", "out"}:
        return value
    return ""


def _money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"))


def _rate(value: Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.000001"))


def source_fingerprint(row: LegacyInvoiceSnapshot) -> str:
    payload = {
        "id": row.id,
        "project_id": row.project_id,
        "invoice_no": row.invoice_no,
        "period": row.period,
        "entity_code": row.entity_code,
        "direction": row.direction,
        "counterparty_code": row.counterparty_code,
        "category": row.category,
        "net": str(_money(row.net)),
        "vat": str(_money(row.vat)),
        "rate": str(_rate(row.rate)),
        "deductible": bool(row.deductible),
        "note": row.note,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def migration_identity_key(legacy_ids: Iterable[int]) -> str:
    ids = tuple(sorted(int(value) for value in legacy_ids))
    if not ids:
        raise ValueError("legacy_ids must not be empty")
    if len(ids) == 1:
        suffix = f"ROW|{ids[0]}"
    else:
        suffix = "PAIR|" + "-".join(str(value) for value in ids)
    return f"{MIGRATION_IDENTITY_VERSION}|{suffix}"


def resolve_perspective(
    row: LegacyInvoiceSnapshot,
    party_lookup: Mapping[str, PartyRef],
) -> tuple[ResolvedLegacyInvoice | None, str | None]:
    direction = normalize_direction(row.direction)
    if not direction:
        return None, "INVALID_DIRECTION"

    entity = party_lookup.get(normalize_text(row.entity_code))
    counterparty = party_lookup.get(normalize_text(row.counterparty_code))
    if entity is None or counterparty is None:
        missing = []
        if entity is None:
            missing.append(normalize_text(row.entity_code) or "<BLANK_ENTITY>")
        if counterparty is None:
            missing.append(normalize_text(row.counterparty_code) or "<BLANK_COUNTERPARTY>")
        return None, "UNRESOLVED_PARTY:" + ",".join(missing)

    if entity.party_id == counterparty.party_id:
        return None, "SELLER_BUYER_SAME_PARTY"

    if direction == "out":
        seller, buyer = entity, counterparty
    else:
        seller, buyer = counterparty, entity
    return ResolvedLegacyInvoice(row=row, seller=seller, buyer=buyer), None


def _coarse_key(row: ResolvedLegacyInvoice) -> tuple[object, ...] | None:
    invoice_no = normalize_text(row.row.invoice_no)
    if not invoice_no:
        return None
    return (
        int(row.row.project_id),
        normalize_text(row.row.period),
        invoice_no,
        row.seller.party_id,
        row.buyer.party_id,
    )


def _exact_key(row: ResolvedLegacyInvoice) -> tuple[object, ...]:
    return (
        _coarse_key(row),
        normalize_text(row.row.category),
        _money(row.row.net),
        _money(row.row.vat),
        _rate(row.row.rate),
    )


def _single_action(row: ResolvedLegacyInvoice) -> PilotAction:
    both_internal = row.seller.internal and row.buyer.internal
    reason = (
        "INTERNAL_SINGLE_PERSPECTIVE_REQUIRES_REVIEW"
        if both_internal
        else "SINGLE_PERSPECTIVE_REQUIRES_REVIEW"
    )
    return PilotAction(
        action="MIGRATE_SINGLE",
        legacy_ids=(row.row.id,),
        migration_status="MIGRATED_SINGLE_PERSPECTIVE",
        reason=reason,
        seller_party_id=row.seller.party_id,
        buyer_party_id=row.buyer.party_id,
        identity_key=migration_identity_key((row.row.id,)),
    )


def plan_pilot(
    rows: Iterable[LegacyInvoiceSnapshot],
    party_lookup: Mapping[str, PartyRef],
) -> PilotPlan:
    selected = sorted(rows, key=lambda item: item.id)
    selected_ids = tuple(row.id for row in selected)
    if len(selected_ids) != len(set(selected_ids)):
        raise ValueError("duplicate legacy invoice id in pilot selection")

    review_actions: list[PilotAction] = []
    resolved: list[ResolvedLegacyInvoice] = []
    for row in selected:
        item, error = resolve_perspective(row, party_lookup)
        if item is None:
            review_actions.append(
                PilotAction(
                    action="REVIEW_ONLY",
                    legacy_ids=(row.id,),
                    migration_status="NEEDS_REVIEW",
                    reason=error or "UNRESOLVED",
                )
            )
        elif not normalize_text(row.invoice_no):
            review_actions.append(
                PilotAction(
                    action="REVIEW_ONLY",
                    legacy_ids=(row.id,),
                    migration_status="NEEDS_REVIEW",
                    reason="MISSING_INVOICE_NUMBER",
                )
            )
        else:
            resolved.append(item)

    groups: dict[tuple[object, ...], list[ResolvedLegacyInvoice]] = {}
    for item in resolved:
        key = _coarse_key(item)
        if key is None:
            raise AssertionError("resolved rows with invoice number must have a coarse key")
        groups.setdefault(key, []).append(item)

    actions: list[PilotAction] = list(review_actions)

    for _, members in sorted(groups.items(), key=lambda pair: repr(pair[0])):
        members = sorted(members, key=lambda item: item.row.id)
        if len(members) == 1:
            actions.append(_single_action(members[0]))
            continue

        all_internal = all(item.seller.internal and item.buyer.internal for item in members)
        directions = {normalize_direction(item.row.direction) for item in members}
        exact_keys = {_exact_key(item) for item in members}
        if (
            len(members) == 2
            and all_internal
            and directions == {"in", "out"}
            and len(exact_keys) == 1
        ):
            ids = tuple(item.row.id for item in members)
            actions.append(
                PilotAction(
                    action="MERGE_PAIR",
                    legacy_ids=ids,
                    migration_status="MERGED",
                    reason="DETERMINISTIC_INTERNAL_IN_OUT_PAIR",
                    seller_party_id=members[0].seller.party_id,
                    buyer_party_id=members[0].buyer.party_id,
                    identity_key=migration_identity_key(ids),
                )
            )
            continue

        reason = (
            "AMBIGUOUS_OR_CONFLICTING_INTERNAL_PERSPECTIVES"
            if all_internal
            else "DUPLICATE_OR_AMBIGUOUS_SINGLE_PERSPECTIVE"
        )
        actions.append(
            PilotAction(
                action="REVIEW_ONLY",
                legacy_ids=tuple(item.row.id for item in members),
                migration_status="NEEDS_REVIEW",
                reason=reason,
            )
        )

    actions.sort(key=lambda item: item.legacy_ids)
    plan = PilotPlan(selected_ids=selected_ids, actions=tuple(actions))
    if plan.covered_ids != tuple(sorted(selected_ids)):
        raise AssertionError(
            f"pilot planner lost or duplicated rows: selected={selected_ids} covered={plan.covered_ids}"
        )
    return plan


def canonical_plan_payload(
    plan: PilotPlan,
    rows: Iterable[LegacyInvoiceSnapshot],
) -> dict[str, object]:
    by_id = {row.id: row for row in rows}
    return {
        "version": 1,
        "identity_version": MIGRATION_IDENTITY_VERSION,
        "selected_ids": list(plan.selected_ids),
        "source_fingerprints": {
            str(row_id): source_fingerprint(by_id[row_id]) for row_id in plan.selected_ids
        },
        "actions": [
            {
                **asdict(action),
                "legacy_ids": list(action.legacy_ids),
            }
            for action in plan.actions
        ],
    }


def plan_digest(payload: Mapping[str, object]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
