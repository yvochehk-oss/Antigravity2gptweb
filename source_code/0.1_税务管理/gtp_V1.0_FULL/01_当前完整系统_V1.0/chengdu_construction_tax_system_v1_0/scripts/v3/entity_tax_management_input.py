#!/usr/bin/env python3
"""Controlled reviewed-input loader for Task15 Entity Tax Ledger.

This module is the only supported entry point for loading the reviewed,
explicit monthly ``REVENUE`` and ``REAL_COST`` evidence required by Task15.
It intentionally does not read invoices, projects, legacy ``TaxLedger`` rows,
or infer a period from any transaction date.

The default operation is a PostgreSQL read-only PLAN.  The only mutating
operation is APPLY of an exact saved PLAN with an exact database confirmation.
APPLY takes a transaction-scoped advisory lock, re-reads every source, and
inserts only rows which are still absent.  It never updates an existing
reviewed input row.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.orm import Session


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.models import EntityTaxManagementInput  # noqa: E402
from app.v3_party_models import InternalEntity, Party, SourceDocument  # noqa: E402


EXPECTED_HEAD = "87_v3_entity_tax_ledgers"
EXPECTED_DOWN_REVISION = "86_v3_input_vat_claim_review_resolution"
MANIFEST_KIND = "V3_TASK15_ENTITY_TAX_MANAGEMENT_INPUT_MANIFEST"
PLAN_KIND = "V3_TASK15_ENTITY_TAX_MANAGEMENT_INPUT_PLAN"
RESULT_KIND = "V3_TASK15_ENTITY_TAX_MANAGEMENT_INPUT_RESULT"
MANIFEST_VERSION = 1
PLAN_VERSION = 1
ACCEPTED_SOURCE_DOCUMENT_STATUS = "VALIDATED"
MONEY_QUANTUM = Decimal("0.01")
NUMERIC_18_2_MAX = Decimal("9999999999999999.99")

_MONTH_RE = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")
_FORBIDDEN_KEYS = {
    "project_id",
    "entity_code",
    "invoice_date",
    "tax_ledger_id",
    "legacy_tax_ledger_id",
    "legacy_tax_ledger",
    "old_tax_ledger",
    "taxledger",
}


class ManagementInputError(ValueError):
    """Raised for invalid reviewed input evidence or stale plans."""


@dataclass(frozen=True)
class ManagementInputSpec:
    """One manifest item after strict, lossless validation."""

    input_type: str
    input_version: int
    amount: Decimal
    source: str
    source_document_id: int | None = None
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_type": self.input_type,
            "input_version": self.input_version,
            "amount": _money_text(self.amount),
            "source": self.source,
            "source_document_id": self.source_document_id,
            "note": self.note,
        }


@dataclass(frozen=True)
class ReviewedManagementManifest:
    """Strict normalized representation of the reviewed manifest."""

    canonical_entity: str
    tax_period: str
    reviewed_by: str
    reviewed_at: datetime
    inputs: tuple[ManagementInputSpec, ManagementInputSpec]

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": MANIFEST_KIND,
            "version": MANIFEST_VERSION,
            "reviewed": True,
            "canonical_entity": self.canonical_entity,
            "tax_period": self.tax_period,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at.isoformat(),
            "inputs": [item.as_dict() for item in self.inputs],
        }


def _clean_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ManagementInputError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ManagementInputError(f"{field_name} must be nonblank")
    return cleaned


def _reject_forbidden_keys(value: Any, *, path: str = "manifest") -> None:
    """Reject prohibited axes/references anywhere in a JSON object.

    A recursive check matters because putting ``project_id`` or an invoice
    date under an otherwise ignored metadata object would still make the
    manifest ambiguous and would make a future parser accidentally unsafe.
    """

    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in _FORBIDDEN_KEYS:
                raise ManagementInputError(
                    f"{path} contains prohibited field {key!r}; "
                    "Task15 requires explicit legal-entity/month evidence"
                )
            _reject_forbidden_keys(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_forbidden_keys(child, path=f"{path}[{index}]")


def _parse_month(value: Any, *, field_name: str = "tax_period") -> str:
    if not isinstance(value, str) or not _MONTH_RE.fullmatch(value):
        raise ManagementInputError(
            f"{field_name} must use strict YYYY-MM format"
        )
    # The regular expression enforces the month range; this conversion keeps
    # the canonical representation explicit and rejects implementation drift.
    try:
        date.fromisoformat(f"{value}-01")
    except ValueError as exc:  # pragma: no cover - defensive after regex
        raise ManagementInputError(f"{field_name} is invalid") from exc
    return value


def _parse_aware_datetime(value: Any, *, field_name: str = "reviewed_at") -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ManagementInputError(f"{field_name} is required")
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ManagementInputError(
            f"{field_name} must be an ISO-8601 datetime"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ManagementInputError(f"{field_name} must include a timezone offset")
    return parsed


def _parse_positive_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ManagementInputError(f"{field_name} must be a positive integer")
    return value


def _parse_source_document_id(value: Any) -> int | None:
    if value is None:
        return None
    return _parse_positive_int(value, field_name="source_document_id")


def _parse_amount(value: Any, *, field_name: str = "amount") -> Decimal:
    if value is None or isinstance(value, bool):
        raise ManagementInputError(f"{field_name} must be a nonnegative amount")
    # JSON numbers are accepted for compatibility with existing reviewed
    # manifests, but Decimal(str(value)) is used so no binary float operation
    # participates in the deterministic value.  A caller should prefer a
    # quoted decimal string in a durable manifest.
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ManagementInputError(f"{field_name} is not a valid decimal") from exc
    if not amount.is_finite() or amount < 0:
        raise ManagementInputError(f"{field_name} must be finite and nonnegative")
    if amount > NUMERIC_18_2_MAX:
        raise ManagementInputError(
            f"{field_name} exceeds PostgreSQL NUMERIC(18,2) maximum"
        )
    exponent = amount.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -2:
        raise ManagementInputError(
            f"{field_name} must have no more than two decimal places"
        )
    try:
        return amount.quantize(MONEY_QUANTUM)
    except InvalidOperation as exc:
        raise ManagementInputError(
            f"{field_name} cannot be represented as PostgreSQL NUMERIC(18,2)"
        ) from exc


def _money_text(value: Any) -> str:
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    return f"{amount.quantize(MONEY_QUANTUM):.2f}"


def _canonicalize(value: Any) -> Any:
    if isinstance(value, Decimal):
        return _money_text(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _canonicalize(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(child) for child in value]
    return value


def canonical_digest(value: Any) -> str:
    """Return the SHA-256 digest used by plans and source snapshots."""

    encoded = json.dumps(
        _canonicalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_json(path: str | Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManagementInputError("cannot read Task15 manifest/plan JSON") from exc
    if not isinstance(payload, dict):
        raise ManagementInputError("Task15 manifest/plan must be a JSON object")
    return payload


def _normalize_manifest(payload: Mapping[str, Any]) -> ReviewedManagementManifest:
    _reject_forbidden_keys(payload)
    expected_keys = {
        "kind",
        "version",
        "reviewed",
        "canonical_entity",
        "tax_period",
        "reviewed_by",
        "reviewed_at",
        "inputs",
    }
    unknown = sorted(set(payload) - expected_keys)
    if unknown:
        raise ManagementInputError(
            f"manifest has unsupported fields: {', '.join(str(item) for item in unknown)}"
        )
    if payload.get("kind") != MANIFEST_KIND or payload.get("version") != MANIFEST_VERSION:
        raise ManagementInputError(
            f"manifest must be {MANIFEST_KIND} version {MANIFEST_VERSION}"
        )
    if payload.get("reviewed") is not True:
        raise ManagementInputError("manifest must explicitly set reviewed=true")
    canonical_entity = _clean_text(
        payload.get("canonical_entity"), field_name="canonical_entity"
    )
    tax_period = _parse_month(payload.get("tax_period"))
    reviewed_by = _clean_text(payload.get("reviewed_by"), field_name="reviewed_by")
    reviewed_at = _parse_aware_datetime(payload.get("reviewed_at"))

    raw_inputs = payload.get("inputs")
    if not isinstance(raw_inputs, list) or len(raw_inputs) != 2:
        raise ManagementInputError(
            "manifest.inputs must contain exactly one REVENUE and one REAL_COST item"
        )
    expected_input_keys = {
        "input_type",
        "input_version",
        "amount",
        "source",
        "source_document_id",
        "note",
    }
    specs: list[ManagementInputSpec] = []
    seen_types: set[str] = set()
    for index, raw in enumerate(raw_inputs, start=1):
        if not isinstance(raw, dict):
            raise ManagementInputError(f"inputs[{index - 1}] must be an object")
        unknown_item_keys = sorted(set(raw) - expected_input_keys)
        if unknown_item_keys:
            raise ManagementInputError(
                f"inputs[{index - 1}] has unsupported fields: "
                + ", ".join(str(item) for item in unknown_item_keys)
            )
        input_type = raw.get("input_type")
        if input_type not in {"REVENUE", "REAL_COST"}:
            raise ManagementInputError(
                f"inputs[{index - 1}].input_type must be REVENUE or REAL_COST"
            )
        if input_type in seen_types:
            raise ManagementInputError(f"duplicate {input_type} input in manifest")
        seen_types.add(input_type)
        input_version = _parse_positive_int(
            raw.get("input_version"),
            field_name=f"inputs[{index - 1}].input_version",
        )
        amount = _parse_amount(raw.get("amount"), field_name=f"inputs[{index - 1}].amount")
        source = _clean_text(raw.get("source"), field_name=f"inputs[{index - 1}].source")
        source_document_id = _parse_source_document_id(raw.get("source_document_id"))
        note = raw.get("note")
        if note is not None and not isinstance(note, str):
            raise ManagementInputError(f"inputs[{index - 1}].note must be a string or null")
        if isinstance(note, str):
            note = note.strip() or None
        specs.append(
            ManagementInputSpec(
                input_type=input_type,
                input_version=input_version,
                amount=amount,
                source=source,
                source_document_id=source_document_id,
                note=note,
            )
        )
    if seen_types != {"REVENUE", "REAL_COST"}:
        raise ManagementInputError(
            "manifest.inputs must contain exactly one REVENUE and one REAL_COST item"
        )
    specs.sort(key=lambda item: item.input_type)
    return ReviewedManagementManifest(
        canonical_entity=canonical_entity,
        tax_period=tax_period,
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at,
        inputs=(specs[0], specs[1]),
    )


def load_manifest(path: str | Path) -> dict[str, Any]:
    """Load and strictly normalize a reviewed manifest from disk."""

    return _normalize_manifest(_load_json(path)).as_dict()


_load_manifest = load_manifest


def _require_postgresql_session(session: Session) -> None:
    try:
        dialect = session.connection().dialect.name
    except Exception as exc:  # pragma: no cover - defensive for malformed test doubles
        raise ManagementInputError("Task15 input loader requires PostgreSQL") from exc
    if dialect not in {"postgresql", "postgres"}:
        raise ManagementInputError("Task15 input loader is PostgreSQL-only")


def _database_name(session: Session) -> str:
    return str(session.execute(text("SELECT current_database()")).scalar_one())


def _db_head(session: Session) -> str:
    rows = session.execute(
        text("SELECT version_num FROM alembic_version_tax")
    ).scalars().all()
    heads = {str(value) for value in rows if value}
    if len(rows) != 1 or heads != {EXPECTED_HEAD}:
        raise ManagementInputError(
            f"Task15 loader requires exactly database head {EXPECTED_HEAD}"
        )
    return EXPECTED_HEAD


def _disk_head() -> str:
    """Resolve the migration head from the checked-out Alembic scripts."""

    try:
        config = Config(str(ROOT / "alembic.ini"))
        script_location = Path(config.get_main_option("script_location"))
        if not script_location.is_absolute():
            config.set_main_option("script_location", str(ROOT / script_location))
        script = ScriptDirectory.from_config(config)
        heads = set(script.get_heads())
        revision = script.get_revision(EXPECTED_HEAD)
    except Exception as exc:
        raise ManagementInputError("cannot resolve Task15 migration head from disk") from exc
    if heads != {EXPECTED_HEAD}:
        raise ManagementInputError(
            f"Task15 loader requires exactly disk head {EXPECTED_HEAD}"
        )
    if revision is None or revision.down_revision != EXPECTED_DOWN_REVISION:
        raise ManagementInputError(
            "Task15 loader requires revision 87 to descend directly from revision 86"
        )
    return EXPECTED_HEAD


def _ensure_heads(session: Session) -> tuple[str, str]:
    _require_postgresql_session(session)
    db_head = _db_head(session)
    disk_head = _disk_head()
    if db_head != disk_head:
        raise ManagementInputError("database and migration disk heads do not match")
    return db_head, disk_head


def _entity(session: Session, canonical_entity: str) -> tuple[InternalEntity, Party]:
    row = session.execute(
        select(InternalEntity, Party)
        .join(Party, Party.id == InternalEntity.party_id)
        .where(
            InternalEntity.canonical_code == canonical_entity,
            InternalEntity.active.is_(True),
            InternalEntity.legal_entity.is_(True),
            Party.active.is_(True),
            Party.party_type == "internal",
        )
    ).one_or_none()
    if row is None:
        # Distinguish a known but ineligible master row without exposing any
        # connection details or unrelated data.
        known = session.scalar(
            select(InternalEntity).where(
                InternalEntity.canonical_code == canonical_entity
            )
        )
        if known is None:
            raise ManagementInputError("canonical entity is not found")
        raise ManagementInputError("canonical entity is not an active legal internal entity")
    return row[0], row[1]


def _iso(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _entity_snapshot(entity: InternalEntity, party: Party) -> dict[str, Any]:
    return {
        "party_id": int(entity.party_id),
        "canonical_code": str(entity.canonical_code),
        "business_role": str(entity.business_role),
        "legal_entity": bool(entity.legal_entity),
        "entity_active": bool(entity.active),
        "party": {
            "id": int(party.id),
            "code": str(party.code),
            "name": str(party.name),
            "party_type": str(party.party_type),
            "active": bool(party.active),
        },
    }


def _source_document_snapshot(row: SourceDocument) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "source_system": str(row.source_system),
        "external_document_id": row.external_document_id,
        "filename": str(row.filename),
        "mime_type": row.mime_type,
        "file_sha256": row.file_sha256,
        "document_type": row.document_type,
        "received_at": _iso(row.received_at),
        "processed_at": _iso(row.processed_at),
        "source_uri": row.source_uri,
        "status": str(row.status),
    }


def _source_documents(
    session: Session,
    specs: tuple[ManagementInputSpec, ManagementInputSpec],
) -> dict[str, dict[str, Any]]:
    ids = sorted(
        {int(item.source_document_id) for item in specs if item.source_document_id is not None}
    )
    snapshots: dict[str, dict[str, Any]] = {}
    for source_id in ids:
        row = session.get(SourceDocument, source_id)
        if row is None:
            raise ManagementInputError(
                f"source document {source_id} is not available"
            )
        # ``status`` is the only current-state field in the real model.  Do
        # not invent an ``is_current`` column; a validated row is accepted and
        # its complete current catalog state is hash-bound below.
        if str(row.status) != ACCEPTED_SOURCE_DOCUMENT_STATUS:
            raise ManagementInputError(
                f"source document {source_id} is not current VALIDATED evidence"
            )
        snapshots[str(source_id)] = _source_document_snapshot(row)
    return snapshots


def _input_snapshot(row: EntityTaxManagementInput) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "reporting_party_id": int(row.reporting_party_id),
        "tax_period": _iso(row.tax_period),
        "input_type": str(row.input_type),
        "input_version": int(row.input_version),
        "amount": _money_text(row.amount),
        "source_document_id": (
            int(row.source_document_id) if row.source_document_id is not None else None
        ),
        "source": str(row.source),
        "reviewed": bool(row.reviewed),
        "reviewed_by": row.reviewed_by,
        "reviewed_at": _iso(row.reviewed_at),
        "note": row.note,
        "created_at": _iso(row.created_at),
    }


def _input_content(row: EntityTaxManagementInput) -> dict[str, Any]:
    """Fields controlled by the manifest, excluding generated id/timestamp."""

    return {
        "reporting_party_id": int(row.reporting_party_id),
        "tax_period": _iso(row.tax_period),
        "input_type": str(row.input_type),
        "input_version": int(row.input_version),
        "amount": _money_text(row.amount),
        "source_document_id": (
            int(row.source_document_id) if row.source_document_id is not None else None
        ),
        "source": str(row.source),
        "reviewed": bool(row.reviewed),
        "reviewed_by": str(row.reviewed_by or ""),
        "reviewed_at": _iso(row.reviewed_at),
        "note": row.note,
    }


def _spec_content(
    spec: ManagementInputSpec,
    *,
    reporting_party_id: int,
    tax_period: str,
    reviewed_by: str,
    reviewed_at: datetime,
) -> dict[str, Any]:
    return {
        "reporting_party_id": int(reporting_party_id),
        "tax_period": f"{tax_period}-01",
        "input_type": spec.input_type,
        "input_version": spec.input_version,
        "amount": _money_text(spec.amount),
        "source_document_id": spec.source_document_id,
        "source": spec.source,
        "reviewed": True,
        "reviewed_by": reviewed_by,
        "reviewed_at": reviewed_at.isoformat(),
        "note": spec.note,
    }


def _existing_rows(
    session: Session,
    *,
    reporting_party_id: int,
    tax_period: date,
    specs: tuple[ManagementInputSpec, ManagementInputSpec],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for spec in specs:
        rows = session.scalars(
            select(EntityTaxManagementInput).where(
                EntityTaxManagementInput.reporting_party_id == reporting_party_id,
                EntityTaxManagementInput.tax_period == tax_period,
                EntityTaxManagementInput.input_type == spec.input_type,
                EntityTaxManagementInput.input_version == spec.input_version,
            )
        ).all()
        if len(rows) > 1:
            raise ManagementInputError(
                f"multiple existing {spec.input_type} rows share the same version"
            )
        expected = _spec_content(
            spec,
            reporting_party_id=reporting_party_id,
            tax_period=tax_period.strftime("%Y-%m"),
            reviewed_by="__REVIEWED_BY__",
            reviewed_at=datetime.fromtimestamp(0),
        )
        # Placeholder values are replaced by the caller before comparison;
        # keeping this helper's row shape independent makes snapshots easy to
        # inspect in tests and in audit artifacts.
        existing = rows[0] if rows else None
        output.append(
            {
                "input_type": spec.input_type,
                "input_version": spec.input_version,
                "status": "MISSING" if existing is None else "EXISTS",
                "row": _input_snapshot(existing) if existing is not None else None,
                "expected_without_review_identity": expected,
            }
        )
    return output


def _compare_existing(
    existing: Mapping[str, Any] | None,
    expected: Mapping[str, Any],
    *,
    input_type: str,
    input_version: int,
) -> str:
    if existing is None:
        return "INSERT"
    actual = {
        key: existing.get(key)
        for key in expected
    }
    if _canonicalize(actual) != _canonicalize(dict(expected)):
        raise ManagementInputError(
            f"existing {input_type} version {input_version} conflicts with reviewed manifest"
        )
    return "NO_CHANGE"


def _validate_plan_action_contract(
    plan: Mapping[str, Any],
    manifest: ReviewedManagementManifest,
) -> dict[tuple[str, int], dict[str, Any]]:
    """Rebuild and validate every action from the trusted manifest fields.

    A plan digest is an accidental-integrity check, not a signature. Therefore
    action expected and existing payloads are never trusted merely because they
    are covered by a (recomputed) public digest. This helper verifies their
    exact relationship to the manifest and the original existing-row snapshot,
    and returns expected values reconstructed from the manifest for APPLY.
    """

    reporting_party_id = _parse_positive_int(
        plan.get("reporting_party_id"), field_name="plan.reporting_party_id"
    )
    existing_rows = plan.get("existing_same_version")
    source_snapshot = plan.get("source_snapshot")
    if not isinstance(existing_rows, list) or len(existing_rows) != 2:
        raise ManagementInputError(
            "Task15 plan existing_same_version must contain exactly two rows"
        )
    if not isinstance(source_snapshot, Mapping):
        raise ManagementInputError("Task15 plan source snapshot is invalid")
    snapshot_existing_rows = source_snapshot.get("existing_same_version")
    if not isinstance(snapshot_existing_rows, list) or len(snapshot_existing_rows) != 2:
        raise ManagementInputError(
            "Task15 source snapshot existing_same_version must contain exactly two rows"
        )
    if _canonicalize(existing_rows) != _canonicalize(snapshot_existing_rows):
        raise ManagementInputError(
            "Task15 plan existing-row snapshots are inconsistent"
        )

    planned_by_key: dict[tuple[str, int], dict[str, Any]] = {}
    for item in existing_rows:
        if not isinstance(item, Mapping):
            raise ManagementInputError("Task15 plan existing-row snapshot is invalid")
        input_type = item.get("input_type")
        if input_type not in {"REVENUE", "REAL_COST"}:
            raise ManagementInputError("Task15 plan existing-row type is invalid")
        input_version = _parse_positive_int(
            item.get("input_version"), field_name="plan existing input_version"
        )
        key = (str(input_type), input_version)
        if key in planned_by_key:
            raise ManagementInputError("Task15 plan existing-row snapshot is duplicated")
        row_status = item.get("status")
        row = item.get("row")
        if row_status not in {"MISSING", "EXISTS"}:
            raise ManagementInputError("Task15 plan existing-row status is invalid")
        if row_status == "MISSING" and row is not None:
            raise ManagementInputError("Task15 MISSING existing row must be null")
        if row_status == "EXISTS" and not isinstance(row, Mapping):
            raise ManagementInputError("Task15 EXISTS existing row is invalid")
        planned_by_key[key] = {"row": row, "status": row_status}

    actions = plan.get("actions")
    if not isinstance(actions, list) or len(actions) != 2:
        raise ManagementInputError("Task15 plan must contain exactly two actions")
    expected_by_type = {item.input_type: item for item in manifest.inputs}
    action_keys = {"input_type", "input_version", "action", "expected", "existing"}
    contract: dict[tuple[str, int], dict[str, Any]] = {}
    for action in actions:
        if not isinstance(action, Mapping) or set(action) != action_keys:
            raise ManagementInputError("Task15 plan action shape is invalid")
        input_type = action.get("input_type")
        if input_type not in {"REVENUE", "REAL_COST"}:
            raise ManagementInputError("Task15 plan contains an unsupported input type")
        input_version = _parse_positive_int(
            action.get("input_version"), field_name="plan action input_version"
        )
        key = (str(input_type), input_version)
        if key in contract:
            raise ManagementInputError("Task15 plan actions contain a duplicate input")
        spec = expected_by_type.get(str(input_type))
        if spec is None or spec.input_version != input_version:
            raise ManagementInputError("Task15 plan action does not match manifest input")
        expected = _spec_content(
            spec,
            reporting_party_id=reporting_party_id,
            tax_period=manifest.tax_period,
            reviewed_by=manifest.reviewed_by,
            reviewed_at=manifest.reviewed_at,
        )
        supplied_expected = action.get("expected")
        if not isinstance(supplied_expected, Mapping) or (
            _canonicalize(dict(supplied_expected)) != _canonicalize(expected)
        ):
            raise ManagementInputError(
                "Task15 plan action expected row does not match manifest input"
            )
        planned = planned_by_key.get(key)
        if planned is None:
            raise ManagementInputError(
                "Task15 plan action has no matching existing-row snapshot"
            )
        if _canonicalize(action.get("existing")) != _canonicalize(planned["row"]):
            raise ManagementInputError(
                "Task15 plan action existing row does not match source snapshot"
            )
        expected_action = "INSERT" if planned["row"] is None else "NO_CHANGE"
        if action.get("action") != expected_action:
            raise ManagementInputError("Task15 plan action status is inconsistent")
        contract[key] = {
            "expected": expected,
            "planned_row": planned["row"],
        }
    expected_keys = {
        ("REVENUE", expected_by_type["REVENUE"].input_version),
        ("REAL_COST", expected_by_type["REAL_COST"].input_version),
    }
    if set(contract) != expected_keys:
        raise ManagementInputError(
            "Task15 plan must address REVENUE and REAL_COST exactly once"
        )
    return contract


def _set_read_only(session: Session) -> None:
    """Start a PostgreSQL transaction that rejects accidental writes."""

    _require_postgresql_session(session)
    session.execute(text("SET TRANSACTION READ ONLY"))


def _source_snapshot(
    *,
    entity: InternalEntity,
    party: Party,
    tax_period: date,
    specs: tuple[ManagementInputSpec, ManagementInputSpec],
    reviewed_by: str,
    reviewed_at: datetime,
    documents: Mapping[str, Mapping[str, Any]],
    existing: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "snapshot_version": "V3_TASK15_MANAGEMENT_INPUT_SOURCE_SNAPSHOT_V1",
        "entity": _entity_snapshot(entity, party),
        "scope": {
            "reporting_party_id": int(entity.party_id),
            "tax_period": tax_period.isoformat(),
        },
        "review": {
            "reviewed_by": reviewed_by,
            "reviewed_at": reviewed_at.isoformat(),
        },
        "requested_inputs": [item.as_dict() for item in specs],
        "source_documents": dict(documents),
        "existing_same_version": existing,
    }


def _existing_for_plan(
    session: Session,
    *,
    party_id: int,
    tax_period: date,
    specs: tuple[ManagementInputSpec, ManagementInputSpec],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in specs:
        matches = session.scalars(
            select(EntityTaxManagementInput).where(
                EntityTaxManagementInput.reporting_party_id == party_id,
                EntityTaxManagementInput.tax_period == tax_period,
                EntityTaxManagementInput.input_type == spec.input_type,
                EntityTaxManagementInput.input_version == spec.input_version,
            )
        ).all()
        if len(matches) > 1:
            raise ManagementInputError(
                f"multiple existing {spec.input_type} rows share the same version"
            )
        rows.append(
            {
                "input_type": spec.input_type,
                "input_version": spec.input_version,
                "status": "MISSING" if not matches else "EXISTS",
                "row": _input_snapshot(matches[0]) if matches else None,
            }
        )
    return rows


def _plan_manifest(payload: Mapping[str, Any]) -> ReviewedManagementManifest:
    return _normalize_manifest(payload)


def make_plan(
    session: Session,
    manifest: Mapping[str, Any] | ReviewedManagementManifest,
) -> dict[str, Any]:
    """Validate a reviewed manifest and return a read-only insertion PLAN."""

    _set_read_only(session)
    if isinstance(manifest, ReviewedManagementManifest):
        normalized = manifest
    else:
        normalized = _plan_manifest(manifest)
    db_head, disk_head = _ensure_heads(session)
    current_database = _database_name(session)
    tax_period = date.fromisoformat(f"{normalized.tax_period}-01")
    entity, party = _entity(session, normalized.canonical_entity)
    documents = _source_documents(session, normalized.inputs)
    existing = _existing_for_plan(
        session,
        party_id=int(entity.party_id),
        tax_period=tax_period,
        specs=normalized.inputs,
    )
    actions: list[dict[str, Any]] = []
    for spec, existing_item in zip(normalized.inputs, existing):
        expected = _spec_content(
            spec,
            reporting_party_id=int(entity.party_id),
            tax_period=normalized.tax_period,
            reviewed_by=normalized.reviewed_by,
            reviewed_at=normalized.reviewed_at,
        )
        action = _compare_existing(
            existing_item["row"],
            expected,
            input_type=spec.input_type,
            input_version=spec.input_version,
        )
        actions.append(
            {
                "input_type": spec.input_type,
                "input_version": spec.input_version,
                "action": action,
                "expected": expected,
                "existing": existing_item["row"],
            }
        )
    snapshot = _source_snapshot(
        entity=entity,
        party=party,
        tax_period=tax_period,
        specs=normalized.inputs,
        reviewed_by=normalized.reviewed_by,
        reviewed_at=normalized.reviewed_at,
        documents=documents,
        existing=existing,
    )
    core: dict[str, Any] = {
        "kind": PLAN_KIND,
        "version": PLAN_VERSION,
        "mode": "PLAN",
        "database": current_database,
        "alembic_head": db_head,
        "disk_head": disk_head,
        "canonical_entity": normalized.canonical_entity,
        "reporting_party_id": int(entity.party_id),
        "tax_period": normalized.tax_period,
        "reviewed_by": normalized.reviewed_by,
        "reviewed_at": normalized.reviewed_at.isoformat(),
        "manifest": normalized.as_dict(),
        "manifest_sha256": canonical_digest(normalized.as_dict()),
        "source_snapshot": snapshot,
        "existing_same_version": existing,
        "actions": actions,
    }
    core["source_snapshot_sha256"] = canonical_digest(snapshot)
    core["plan_digest"] = canonical_digest(core)
    return core


def _validate_plan(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ManagementInputError("Task15 plan must be an object")
    _reject_forbidden_keys(payload)
    if payload.get("kind") != PLAN_KIND or payload.get("version") != PLAN_VERSION:
        raise ManagementInputError(f"unsupported Task15 management-input plan")
    digest = payload.get("plan_digest")
    if not isinstance(digest, str) or digest != canonical_digest(
        {key: value for key, value in payload.items() if key != "plan_digest"}
    ):
        raise ManagementInputError("Task15 plan_digest mismatch")
    required = {
        "database",
        "alembic_head",
        "disk_head",
        "canonical_entity",
        "reporting_party_id",
        "tax_period",
        "reviewed_by",
        "reviewed_at",
        "manifest",
        "manifest_sha256",
        "source_snapshot",
        "source_snapshot_sha256",
        "existing_same_version",
        "actions",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise ManagementInputError("Task15 plan is missing required fields")
    if not isinstance(payload["manifest"], Mapping):
        raise ManagementInputError("Task15 plan manifest is invalid")
    manifest = _normalize_manifest(payload["manifest"])
    if canonical_digest(manifest.as_dict()) != payload["manifest_sha256"]:
        raise ManagementInputError("Task15 manifest digest mismatch")
    if (
        manifest.canonical_entity != payload["canonical_entity"]
        or manifest.tax_period != payload["tax_period"]
        or manifest.reviewed_by != payload["reviewed_by"]
        or manifest.reviewed_at.isoformat() != payload["reviewed_at"]
    ):
        raise ManagementInputError("Task15 plan and manifest scope are inconsistent")
    _clean_text(payload["database"], field_name="plan.database")
    _parse_positive_int(payload["reporting_party_id"], field_name="plan.reporting_party_id")
    snapshot = payload["source_snapshot"]
    if not isinstance(snapshot, Mapping):
        raise ManagementInputError("Task15 source snapshot must be an object")
    if canonical_digest(snapshot) != payload["source_snapshot_sha256"]:
        raise ManagementInputError("Task15 source snapshot digest mismatch")
    if payload["alembic_head"] != EXPECTED_HEAD or payload["disk_head"] != EXPECTED_HEAD:
        raise ManagementInputError("Task15 plan is not bound to revision 87")
    if not isinstance(payload["actions"], list) or len(payload["actions"]) != 2:
        raise ManagementInputError("Task15 plan must contain exactly two actions")
    _validate_plan_action_contract(payload, manifest)
    return dict(payload)


def load_plan(path: str | Path) -> dict[str, Any]:
    return _validate_plan(_load_json(path))


_load_plan = load_plan


def _lock_scope(session: Session, party_id: int, tax_period: date) -> None:
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {
            "lock_key": (
                f"task15-management-input:{int(party_id)}:{tax_period.isoformat()}"
            )
        },
    )


def _snapshot_without_existing(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(snapshot)
    # The existing rows are allowed to transition from MISSING to the exact
    # rows inserted by an earlier retry.  Every other source field must remain
    # byte-for-byte canonical-equivalent.
    result.pop("existing_same_version", None)
    return result


def _apply_existing_matches(
    *,
    plan: Mapping[str, Any],
    current_existing: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    actions = plan.get("actions")
    if not isinstance(actions, list) or len(actions) != 2:
        raise ManagementInputError("Task15 plan actions are invalid")
    if not isinstance(plan.get("manifest"), Mapping):
        raise ManagementInputError("Task15 plan manifest is invalid")
    manifest = _normalize_manifest(plan["manifest"])
    action_contract = _validate_plan_action_contract(plan, manifest)
    by_key: dict[tuple[str, int], Mapping[str, Any]] = {}
    for item in current_existing:
        if not isinstance(item, Mapping):
            raise ManagementInputError("Task15 current existing-input snapshot is invalid")
        input_type = item.get("input_type")
        input_version = item.get("input_version")
        if input_type not in {"REVENUE", "REAL_COST"}:
            raise ManagementInputError("Task15 current existing-input type is invalid")
        normalized_version = _parse_positive_int(
            input_version, field_name="current existing input_version"
        )
        key = (str(input_type), normalized_version)
        if key in by_key:
            raise ManagementInputError("Task15 current existing-input snapshot is duplicated")
        row_status = item.get("status")
        row = item.get("row")
        if row_status not in {"MISSING", "EXISTS"}:
            raise ManagementInputError("Task15 current existing-input status is invalid")
        if row_status == "MISSING" and row is not None:
            raise ManagementInputError("Task15 current MISSING existing row must be null")
        if row_status == "EXISTS" and not isinstance(row, Mapping):
            raise ManagementInputError("Task15 current EXISTS existing row is invalid")
        by_key[key] = item
    if set(by_key) != set(action_contract):
        raise ManagementInputError("Task15 current existing-input scope is inconsistent")
    result: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, Mapping):
            raise ManagementInputError("Task15 plan action is invalid")
        input_type = action.get("input_type")
        input_version = action.get("input_version")
        if input_type not in {"REVENUE", "REAL_COST"}:
            raise ManagementInputError("Task15 plan contains an unsupported input type")
        normalized_version = _parse_positive_int(
            input_version, field_name="plan action input_version"
        )
        key = (str(input_type), normalized_version)
        current = by_key.get(key)
        if current is None:
            raise ManagementInputError("Task15 plan action has no current existing-input row")
        expected = action_contract[key]["expected"]
        planned_row = action_contract[key]["planned_row"]
        current_row = current.get("row")
        if planned_row is not None and current_row is None:
            raise ManagementInputError(
                "Task15 existing reviewed input disappeared since PLAN"
            )
        if current_row is not None and not isinstance(current_row, Mapping):
            raise ManagementInputError("Task15 current existing-input row is invalid")
        actual_status = "MISSING" if current_row is None else "EXISTS"
        if current_row is not None:
            _compare_existing(
                current_row,
                expected,
                input_type=key[0],
                input_version=key[1],
            )
        result.append(
            {
                "input_type": key[0],
                "input_version": key[1],
                "action": "NO_CHANGE" if actual_status == "EXISTS" else "INSERT",
                "existing": current_row,
                "expected": dict(expected),
            }
        )
    if {str(item["input_type"]) for item in result} != {"REVENUE", "REAL_COST"}:
        raise ManagementInputError("Task15 plan must address REVENUE and REAL_COST exactly once")
    # The manifest is used to construct the insertion values, not action
    # payloads supplied by an untrusted edited plan.
    expected_by_type = {item.input_type: item for item in manifest.inputs}
    for item in result:
        spec = expected_by_type.get(item["input_type"])
        if spec is None or spec.input_version != item["input_version"]:
            raise ManagementInputError("Task15 plan action does not match manifest input")
    return result


def apply_plan(
    session: Session,
    plan: Mapping[str, Any],
    *,
    confirm_database: str,
) -> dict[str, Any]:
    """Apply a validated plan using one atomic caller-owned transaction.

    This function does not commit the caller's outer transaction.  It uses a
    savepoint so any insertion/trigger failure rolls back all loader writes;
    the CLI commits only after this function returns successfully.
    """

    validated = _validate_plan(plan)
    _require_postgresql_session(session)
    nested = session.begin_nested()
    try:
        db_head, disk_head = _ensure_heads(session)
        actual_database = _database_name(session)
        if confirm_database != actual_database:
            raise ManagementInputError(
                "--confirm-database must exactly match current database"
            )
        if validated.get("database") != actual_database:
            raise ManagementInputError("saved Task15 plan targets a different database")
        if validated.get("alembic_head") != db_head or validated.get("disk_head") != disk_head:
            raise ManagementInputError("saved Task15 plan head is stale")
        manifest = _normalize_manifest(validated["manifest"])
        tax_period = date.fromisoformat(f"{manifest.tax_period}-01")
        entity, party = _entity(session, manifest.canonical_entity)
        if int(validated["reporting_party_id"]) != int(entity.party_id):
            raise ManagementInputError("saved Task15 plan entity scope is stale")
        _lock_scope(session, int(entity.party_id), tax_period)
        # Re-read after the lock.  The check is deliberately repeated so the
        # lock sits before the authoritative evidence read.
        db_head, disk_head = _ensure_heads(session)
        entity, party = _entity(session, manifest.canonical_entity)
        documents = _source_documents(session, manifest.inputs)
        current_existing = _existing_for_plan(
            session,
            party_id=int(entity.party_id),
            tax_period=tax_period,
            specs=manifest.inputs,
        )
        current_snapshot = _source_snapshot(
            entity=entity,
            party=party,
            tax_period=tax_period,
            specs=manifest.inputs,
            reviewed_by=manifest.reviewed_by,
            reviewed_at=manifest.reviewed_at,
            documents=documents,
            existing=current_existing,
        )
        planned_snapshot = validated["source_snapshot"]
        if not isinstance(planned_snapshot, Mapping):
            raise ManagementInputError("saved Task15 plan source snapshot is invalid")
        if canonical_digest(_snapshot_without_existing(current_snapshot)) != canonical_digest(
            _snapshot_without_existing(planned_snapshot)
        ):
            raise ManagementInputError("stale Task15 plan: reviewed source snapshot changed")
        actions = _apply_existing_matches(
            plan=validated,
            current_existing=current_existing,
        )
        inserted_ids: list[int] = []
        for action in actions:
            if action["action"] == "NO_CHANGE":
                continue
            spec = next(
                item for item in manifest.inputs if item.input_type == action["input_type"]
            )
            row = EntityTaxManagementInput(
                reporting_party_id=int(entity.party_id),
                tax_period=tax_period,
                input_type=spec.input_type,
                input_version=spec.input_version,
                amount=spec.amount,
                source_document_id=spec.source_document_id,
                source=spec.source,
                reviewed=True,
                reviewed_by=manifest.reviewed_by,
                reviewed_at=manifest.reviewed_at,
                note=spec.note,
            )
            session.add(row)
            session.flush()
            inserted_ids.append(int(row.id))
        nested.commit()
        status = "BUILT" if inserted_ids else "NO_CHANGE"
        return {
            "kind": RESULT_KIND,
            "version": 1,
            "mode": "APPLY",
            "status": status,
            "database": actual_database,
            "alembic_head": db_head,
            "disk_head": disk_head,
            "canonical_entity": manifest.canonical_entity,
            "reporting_party_id": int(entity.party_id),
            "tax_period": manifest.tax_period,
            "plan_digest": validated["plan_digest"],
            "inserted_ids": inserted_ids,
            "actions": actions,
        }
    except BaseException:
        # ``SessionTransaction`` rolls the savepoint back, including a flush
        # that inserted the first of two inputs.  The outer transaction remains
        # available to the caller for an explicit rollback/inspection.
        nested.rollback()
        raise


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise ManagementInputError("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise ManagementInputError("Task15 input loader is PostgreSQL-only")
    return value


def _write_json(path: str | None, payload: Mapping[str, Any]) -> None:
    rendered = json.dumps(
        _canonicalize(payload),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    print(rendered)
    if path:
        Path(path).write_text(rendered + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Load reviewed Task15 entity-tax management inputs through PLAN/APPLY."
    )
    parser.add_argument("--manifest", help="reviewed Task15 manifest JSON (PLAN only)")
    parser.add_argument("--plan", help="exact saved Task15 PLAN JSON (APPLY only)")
    parser.add_argument("--apply", action="store_true", help="apply an exact saved PLAN")
    parser.add_argument(
        "--confirm-database",
        help="required with APPLY; must exactly equal PostgreSQL current_database()",
    )
    parser.add_argument("--json", dest="json_path", help="write the PLAN/RESULT JSON artifact")
    args = parser.parse_args(argv)

    if args.apply:
        if not args.plan or args.manifest:
            parser.error("APPLY requires --plan and does not accept --manifest")
        if not args.confirm_database:
            parser.error("APPLY requires --confirm-database")
    else:
        if args.plan:
            parser.error("--plan is only valid with --apply")
        if not args.manifest:
            parser.error("PLAN mode requires --manifest")
        if args.confirm_database:
            parser.error("--confirm-database is only valid with --apply")

    mode_kind = RESULT_KIND if args.apply else PLAN_KIND
    engine: Engine | None = None
    try:
        database_url = _database_url()
        engine = create_engine(database_url, future=True, pool_pre_ping=True)
        if args.apply:
            plan = load_plan(args.plan)
            with Session(engine) as session:
                try:
                    result = apply_plan(
                        session,
                        plan,
                        confirm_database=args.confirm_database,
                    )
                    session.commit()
                except BaseException:
                    session.rollback()
                    raise
        else:
            manifest = load_manifest(args.manifest)
            # A transaction-level read-only flag ensures that even an
            # accidental ORM flush or future code path cannot mutate the DB in
            # PLAN mode.  The artifact file itself is the intended output.
            with Session(engine) as session:
                try:
                    _set_read_only(session)
                    result = make_plan(session, manifest)
                finally:
                    session.rollback()
        _write_json(args.json_path, result)
        return 0
    except ManagementInputError as exc:
        _write_json(
            args.json_path,
            {
                "kind": f"{mode_kind}_ERROR",
                "version": 1,
                "mode": "APPLY" if args.apply else "PLAN",
                "status": "FAIL",
                "error": str(exc),
            },
        )
        return 2
    except Exception:
        # Do not print tracebacks or connection strings in CLI evidence.
        _write_json(
            args.json_path,
            {
                "kind": f"{mode_kind}_ERROR",
                "version": 1,
                "mode": "APPLY" if args.apply else "PLAN",
                "status": "FAIL",
                "error": "Task15 loader failed without exposing database details",
            },
        )
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
