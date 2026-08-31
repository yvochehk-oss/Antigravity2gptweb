"""Pure deterministic rules for the V3 Entity Tax Ledger.

Task 15 deliberately separates three concerns:

* VAT is represented only by a reference to an official ``EntityVatLedger``
  result and its successful VAT ``CalculationRun``;
* revenue and real cost come only from reviewed, versioned, explicit-period
  management inputs; and
* profit and estimated CIT are calculated here from Decimal values and a
  caller-selected reviewed/effective ``TaxRule``.

No invoice date, project id, legacy ``TaxLedger``, or default tax rate is
consulted by this module.  It is safe to use in a builder transaction because
it has no database or I/O dependency.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from typing import Any


RULESET_VERSION = "V3_ENTITY_TAX_LEDGER_V1"
ENTITY_TAX_TYPE = "ENTITY_TAX"
MONEY_QUANTUM = Decimal("0.01")
RATE_QUANTUM = Decimal("0.0001")
SHA256_HEX_LENGTH = 64


class EntityTaxLedgerError(ValueError):
    """Base error for an unavailable or invalid entity-tax calculation."""


class EntityTaxEvidenceError(EntityTaxLedgerError):
    """Raised when required reviewed evidence is missing or ambiguous."""


class EntityTaxRuleError(EntityTaxLedgerError):
    """Raised when a CIT rule is missing, ineffective, or ambiguous."""


@dataclass(frozen=True)
class EntityTaxManagementInputView:
    """Small immutable view of a revision-87 management-input row."""

    id: int | None
    reporting_party_id: int
    tax_period: date
    input_type: str
    input_version: int
    amount: Decimal
    reviewed: bool
    reviewed_by: str | None = None
    reviewed_at: datetime | str | None = None
    source: str = ""
    source_document_id: int | None = None
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "reporting_party_id": self.reporting_party_id,
            "tax_period": self.tax_period.isoformat(),
            "input_type": self.input_type,
            "input_version": self.input_version,
            "amount": str(self.amount),
            "reviewed": self.reviewed,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": _canonical_datetime(self.reviewed_at),
            "source": self.source,
            "source_document_id": self.source_document_id,
            "note": self.note,
        }


# Both names are useful to callers and keep the domain vocabulary aligned with
# the physical table without maintaining two separate view implementations.
EntityTaxInputView = EntityTaxManagementInputView


@dataclass(frozen=True)
class EntityTaxRuleView:
    """Explicit reviewed/effective CIT rule evidence."""

    code: str
    rate: Decimal
    effective_from: date | str
    effective_to: date | str | None = None
    reviewed: bool = False
    source: str | None = None
    id: int | None = None
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "code": self.code,
            "rate": str(self.rate),
            "effective_from": _canonical_date(self.effective_from),
            "effective_to": _canonical_date(self.effective_to),
            "reviewed": self.reviewed,
            "source": self.source,
            "note": self.note,
        }


@dataclass(frozen=True)
class OfficialVatLedgerView:
    """Portable evidence view for the official VAT ledger dependency."""

    id: int
    calculation_run_id: int
    reporting_party_id: int
    tax_period: date
    tax_type: str = "VAT"
    run_status: str = "SUCCEEDED"
    result_sha256: str | None = None
    official: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "calculation_run_id": self.calculation_run_id,
            "reporting_party_id": self.reporting_party_id,
            "tax_period": self.tax_period.isoformat(),
            "tax_type": self.tax_type,
            "run_status": self.run_status,
            "result_sha256": self.result_sha256,
            "official": self.official,
        }


@dataclass(frozen=True)
class EntityTaxLedgerResult:
    """Immutable calculation result ready for a revision-87 ledger row."""

    reporting_party_id: int
    tax_period: date
    entity_vat_ledger_id: int
    revenue: Decimal
    real_cost: Decimal
    estimated_profit: Decimal
    estimated_cit: Decimal
    rule_version: str
    input_snapshot_sha256: str
    result_sha256: str
    revenue_input_id: int | None
    real_cost_input_id: int | None
    revenue_input_version: int
    real_cost_input_version: int
    cit_rule_code: str
    cit_rate: Decimal
    selected_inputs: tuple[EntityTaxManagementInputView, ...] = field(
        default_factory=tuple,
        repr=False,
    )
    input_snapshot: Mapping[str, Any] = field(default_factory=dict, repr=False)
    result_payload: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @property
    def profit(self) -> Decimal:
        """Short alias used by calculation/reporting callers."""

        return self.estimated_profit

    @property
    def cit(self) -> Decimal:
        """Short alias for the estimated CIT result."""

        return self.estimated_cit

    def as_dict(self) -> dict[str, Any]:
        """Return only serializable result fields, excluding internal views."""

        return {
            "reporting_party_id": self.reporting_party_id,
            "tax_period": self.tax_period.isoformat(),
            "entity_vat_ledger_id": self.entity_vat_ledger_id,
            "revenue": str(self.revenue),
            "real_cost": str(self.real_cost),
            "estimated_profit": str(self.estimated_profit),
            "estimated_cit": str(self.estimated_cit),
            "rule_version": self.rule_version,
            "input_snapshot_sha256": self.input_snapshot_sha256,
            "result_sha256": self.result_sha256,
            "revenue_input_id": self.revenue_input_id,
            "real_cost_input_id": self.real_cost_input_id,
            "revenue_input_version": self.revenue_input_version,
            "real_cost_input_version": self.real_cost_input_version,
            "cit_rule_code": self.cit_rule_code,
            "cit_rate": str(self.cit_rate),
        }

    def to_dict(self) -> dict[str, Any]:
        """Compatibility alias for callers that use ``to_dict`` elsewhere."""

        return self.as_dict()


# The longer name makes integration code self-documenting; the result remains
# one concrete type so hashes and fields cannot drift between aliases.
EntityTaxLedgerCalculation = EntityTaxLedgerResult


def _get(row: Any, name: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(name, default)
    return getattr(row, name, default)


def _canonical_datetime(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _canonical_date(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()


def month_start(value: date | str) -> date:
    """Parse a monthly scope without using any transaction date.

    ``YYYY-MM`` and ISO dates are accepted for boundary/API convenience.  A
    date containing a day is normalized to the first day only for the *scope
    argument*; management-input and VAT evidence rows are separately required
    to carry an explicit first-of-month date.
    """

    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return date(value.year, value.month, 1)
    text = str(value or "").strip()
    if len(text) == 7:
        try:
            parsed = date.fromisoformat(f"{text}-01")
        except ValueError as exc:
            raise EntityTaxEvidenceError(f"invalid tax period: {value!r}") from exc
        return parsed
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise EntityTaxEvidenceError(f"invalid tax period: {value!r}") from exc
    return date(parsed.year, parsed.month, 1)


def _explicit_period(value: Any, *, field_name: str) -> date:
    # Evidence rows must already carry the database's Date value.  Parsing a
    # string here would blur the boundary between an external scope selector
    # (which may use ``month_start``) and documentary evidence (which must be
    # explicit and auditable).  ``datetime`` is rejected separately because it
    # is a subclass of ``date`` but is not a tax-period Date value.
    if isinstance(value, datetime) or not isinstance(value, date):
        raise EntityTaxEvidenceError(
            f"{field_name} must be an explicit month-start date value"
        )
    if value.day != 1:
        raise EntityTaxEvidenceError(
            f"{field_name} must be the first day of its month (month-start); "
            "no date inference is allowed"
        )
    return value


def _int(value: Any, *, field_name: str, required: bool = True) -> int | None:
    if value is None or value == "":
        if required:
            raise EntityTaxEvidenceError(f"{field_name} is required")
        return None
    if isinstance(value, bool):
        raise EntityTaxEvidenceError(f"{field_name} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise EntityTaxEvidenceError(f"{field_name} must be an integer") from exc
    return result


def _money(value: Any, *, field_name: str, nonnegative: bool = True) -> Decimal:
    if value is None or value == "":
        raise EntityTaxEvidenceError(f"{field_name} is required")
    if isinstance(value, bool):
        raise EntityTaxEvidenceError(f"{field_name} must be a Decimal-compatible number")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise EntityTaxEvidenceError(f"{field_name} is not a valid Decimal") from exc
    if not result.is_finite():
        raise EntityTaxEvidenceError(f"{field_name} must be finite")
    if nonnegative and result < 0:
        raise EntityTaxEvidenceError(f"{field_name} must be nonnegative")
    return result.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _rate(value: Any, *, field_name: str = "tax rule rate") -> Decimal:
    if value is None or value == "":
        raise EntityTaxRuleError(f"{field_name} is required")
    if isinstance(value, bool):
        raise EntityTaxRuleError(f"{field_name} must be a Decimal-compatible number")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise EntityTaxRuleError(f"{field_name} is not a valid Decimal") from exc
    if not result.is_finite() or result < 0 or result > 1:
        raise EntityTaxRuleError(f"{field_name} must be finite and between 0 and 1")
    return result.quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)


def _coerce_input(row: Any) -> EntityTaxManagementInputView:
    if isinstance(row, EntityTaxManagementInputView):
        return row
    return EntityTaxManagementInputView(
        id=_int(_get(row, "id"), field_name="management input id", required=False),
        reporting_party_id=_int(
            _get(row, "reporting_party_id"),
            field_name="management input reporting_party_id",
        ),
        tax_period=_get(row, "tax_period"),  # validated by selector
        input_type=str(_get(row, "input_type", "") or "").strip().upper(),
        input_version=_int(
            _get(row, "input_version"),
            field_name="management input input_version",
        ),
        amount=_get(row, "amount"),  # normalized by selector
        reviewed=_get(row, "reviewed", False) is True,
        reviewed_by=_get(row, "reviewed_by"),
        reviewed_at=_get(row, "reviewed_at"),
        source=str(_get(row, "source", "") or ""),
        source_document_id=_int(
            _get(row, "source_document_id"),
            field_name="management input source_document_id",
            required=False,
        ),
        note=_get(row, "note"),
    )


def _coerce_rule(row: Any) -> EntityTaxRuleView:
    if isinstance(row, EntityTaxRuleView):
        return row
    return EntityTaxRuleView(
        code=str(_get(row, "code", "") or "").strip(),
        rate=_get(row, "rate"),
        effective_from=_get(row, "effective_from"),
        effective_to=_get(row, "effective_to"),
        reviewed=_get(row, "reviewed", False) is True,
        source=_get(row, "source"),
        id=_int(_get(row, "id"), field_name="tax rule id", required=False),
        note=_get(row, "note"),
    )


def _coerce_rules(rules: Iterable[Any] | Any) -> tuple[Any, ...]:
    if isinstance(rules, Mapping) or isinstance(rules, EntityTaxRuleView):
        return (rules,)
    if isinstance(rules, (str, bytes)):
        return (rules,)
    try:
        return tuple(rules)
    except TypeError:
        return (rules,)


def _validate_reviewed_input(row: EntityTaxManagementInputView) -> EntityTaxManagementInputView:
    if row.reviewed is not True:
        raise EntityTaxEvidenceError(
            f"{row.input_type} input version {row.input_version} is not reviewed"
        )
    if not str(row.reviewed_by or "").strip() or row.reviewed_at in (None, ""):
        raise EntityTaxEvidenceError(
            f"{row.input_type} input version {row.input_version} lacks review evidence"
        )
    if not row.source.strip():
        raise EntityTaxEvidenceError(
            f"{row.input_type} input version {row.input_version} lacks a source"
        )
    return EntityTaxManagementInputView(
        id=row.id,
        reporting_party_id=_int(
            row.reporting_party_id,
            field_name="management input reporting_party_id",
        ),
        tax_period=_explicit_period(row.tax_period, field_name="management input tax_period"),
        input_type=row.input_type,
        input_version=_int(row.input_version, field_name="management input input_version"),
        amount=_money(row.amount, field_name=f"{row.input_type} amount"),
        reviewed=True,
        reviewed_by=str(row.reviewed_by).strip(),
        reviewed_at=row.reviewed_at,
        source=row.source.strip(),
        source_document_id=row.source_document_id,
        note=row.note,
    )


def select_latest_reviewed_management_inputs(
    inputs: Iterable[Any],
    *,
    reporting_party_id: int,
    tax_period: date | str,
    input_versions: Mapping[str, int] | None = None,
    required_types: tuple[str, ...] = ("REVENUE", "REAL_COST"),
) -> tuple[EntityTaxManagementInputView, ...]:
    """Select one reviewed version of every required explicit-period input.

    The highest version is selected per type.  Therefore a newer unreviewed
    version cannot be silently bypassed in favor of an older reviewed value.
    Duplicate versions and missing types fail closed.
    """

    party = _int(reporting_party_id, field_name="reporting_party_id")
    period = month_start(tax_period)
    normalized_types = tuple(str(item).strip().upper() for item in required_types)
    if not normalized_types or any(item not in {"REVENUE", "REAL_COST"} for item in normalized_types):
        raise EntityTaxEvidenceError("required management input types are invalid")

    requested: dict[str, int] = {}
    for key, value in (input_versions or {}).items():
        normalized_key = str(key).strip().upper()
        version = _int(value, field_name=f"{normalized_key} input version")
        if version is None or version < 1:
            raise EntityTaxEvidenceError(f"{normalized_key} input version must be positive")
        requested[normalized_key] = version

    candidates: dict[str, list[EntityTaxManagementInputView]] = {
        item: [] for item in normalized_types
    }
    seen_versions: dict[str, set[int]] = {
        item: set() for item in normalized_types
    }
    for raw in inputs:
        row = _coerce_input(raw)
        if row.reporting_party_id != party:
            continue
        row_period = _explicit_period(row.tax_period, field_name="management input tax_period")
        if row_period != period:
            continue
        if row.input_type not in candidates:
            if row.input_type:
                raise EntityTaxEvidenceError(
                    f"unsupported entity-tax management input type: {row.input_type}"
                )
            continue
        if row.input_version < 1:
            raise EntityTaxEvidenceError("management input input_version must be positive")
        if row.input_version in seen_versions[row.input_type]:
            raise EntityTaxEvidenceError(
                f"duplicate {row.input_type} input version {row.input_version} for "
                f"party={party}, period={period.isoformat()}"
            )
        seen_versions[row.input_type].add(row.input_version)
        # Validate malformed amounts even when the row is not the latest; an
        # invalid evidence row must not be hidden by a later selection.
        normalized = EntityTaxManagementInputView(
            id=row.id,
            reporting_party_id=row.reporting_party_id,
            tax_period=row_period,
            input_type=row.input_type,
            input_version=row.input_version,
            amount=_money(row.amount, field_name=f"{row.input_type} amount"),
            reviewed=row.reviewed,
            reviewed_by=row.reviewed_by,
            reviewed_at=row.reviewed_at,
            source=row.source,
            source_document_id=row.source_document_id,
            note=row.note,
        )
        candidates[row.input_type].append(normalized)

    selected: list[EntityTaxManagementInputView] = []
    for input_type in normalized_types:
        rows = candidates[input_type]
        if not rows:
            raise EntityTaxEvidenceError(
                f"missing reviewed explicit-period {input_type} input for "
                f"party={party}, period={period.isoformat()}"
            )
        wanted = requested.get(input_type)
        if wanted is not None:
            rows = [row for row in rows if row.input_version == wanted]
            if not rows:
                raise EntityTaxEvidenceError(
                    f"requested {input_type} input version {wanted} is unavailable"
                )
        highest = max(row.input_version for row in rows)
        highest_rows = [row for row in rows if row.input_version == highest]
        if len(highest_rows) != 1:
            raise EntityTaxEvidenceError(
                f"ambiguous {input_type} input version {highest} for "
                f"party={party}, period={period.isoformat()}"
            )
        selected.append(_validate_reviewed_input(highest_rows[0]))
    return tuple(selected)


resolve_management_inputs = select_latest_reviewed_management_inputs


def resolve_effective_tax_rule(
    rules: Iterable[Any] | Any,
    *,
    tax_period: date | str,
    rule_code: str = "CIT_GENERAL",
) -> EntityTaxRuleView:
    """Resolve exactly one reviewed rule effective on the requested date."""

    code = str(rule_code or "").strip()
    if not code:
        raise EntityTaxRuleError("an explicit CIT rule code is required")
    try:
        period = month_start(tax_period)
    except (EntityTaxEvidenceError, ValueError) as exc:
        raise EntityTaxRuleError(f"invalid rule evaluation date: {tax_period!r}") from exc

    matching: list[EntityTaxRuleView] = []
    unreviewed_effective: list[EntityTaxRuleView] = []
    for raw in _coerce_rules(rules):
        row = _coerce_rule(raw)
        if row.code != code:
            continue
        if not row.code:
            raise EntityTaxRuleError("tax rule code cannot be blank")
        try:
            effective_from = date.fromisoformat(_canonical_date(row.effective_from) or "")
        except ValueError as exc:
            raise EntityTaxRuleError(
                f"tax rule {code!r} has an invalid effective_from"
            ) from exc
        effective_to: date | None
        try:
            effective_to = (
                date.fromisoformat(_canonical_date(row.effective_to))
                if _canonical_date(row.effective_to)
                else None
            )
        except ValueError as exc:
            raise EntityTaxRuleError(
                f"tax rule {code!r} has an invalid effective_to"
            ) from exc
        if effective_to is not None and effective_to < effective_from:
            raise EntityTaxRuleError(f"tax rule {code!r} has an inverted effective range")
        if effective_from <= period and (effective_to is None or period <= effective_to):
            if row.reviewed is not True:
                unreviewed_effective.append(row)
                continue
            matching.append(
                EntityTaxRuleView(
                    code=row.code,
                    rate=_rate(row.rate),
                    effective_from=effective_from,
                    effective_to=effective_to,
                    reviewed=True,
                    source=row.source,
                    id=row.id,
                    note=row.note,
                )
            )

    if unreviewed_effective:
        raise EntityTaxRuleError(
            f"no reviewed effective tax rule {code!r} is available; an unreviewed "
            "candidate blocks deterministic calculation"
        )
    if len(matching) == 0:
        raise EntityTaxRuleError(
            f"no reviewed effective tax rule {code!r} for {period.isoformat()}"
        )
    if len(matching) != 1:
        raise EntityTaxRuleError(
            f"multiple reviewed effective tax rules {code!r} for {period.isoformat()}"
        )
    return matching[0]


resolve_effective_cit_rule = resolve_effective_tax_rule


def validate_official_vat_ledger(
    vat_ledger: Any,
    *,
    reporting_party_id: int,
    tax_period: date | str,
    vat_calculation_run: Any | None = None,
) -> int:
    """Validate the official VAT result dependency and return its id.

    A bare integer or an unmarked ledger is not enough.  The caller must
    provide either a successful VAT run view or an explicit ``official`` flag
    on the ledger view; DB builders should pass the linked CalculationRun.
    """

    if vat_ledger is None:
        raise EntityTaxEvidenceError("official EntityVatLedger result is required")
    expected_party = _int(reporting_party_id, field_name="reporting_party_id")
    expected_period = month_start(tax_period)
    ledger_id = _int(_get(vat_ledger, "id"), field_name="EntityVatLedger id")
    if ledger_id is None or ledger_id < 1:
        raise EntityTaxEvidenceError("official EntityVatLedger id must be positive")
    actual_party = _int(
        _get(vat_ledger, "reporting_party_id"),
        field_name="EntityVatLedger reporting_party_id",
    )
    if actual_party != expected_party:
        raise EntityTaxEvidenceError("EntityVatLedger reporting party does not match scope")
    actual_period = _explicit_period(
        _get(vat_ledger, "tax_period"),
        field_name="EntityVatLedger tax_period",
    )
    if actual_period != expected_period:
        raise EntityTaxEvidenceError("EntityVatLedger tax period does not match scope")

    linked_run = vat_calculation_run or _get(vat_ledger, "calculation_run")
    tax_type = _get(linked_run, "tax_type") if linked_run is not None else _get(vat_ledger, "tax_type")
    run_status = _get(linked_run, "run_status") if linked_run is not None else _get(vat_ledger, "run_status")
    official_marker = _get(vat_ledger, "official", _get(vat_ledger, "is_official"))
    if official_marker is False:
        # An explicit negative marker is authoritative.  A successful linked
        # CalculationRun cannot upgrade a ledger that was marked unofficial.
        raise EntityTaxEvidenceError(
            "EntityVatLedger is explicitly marked official=False"
        )
    if tax_type != "VAT" or run_status != "SUCCEEDED":
        if official_marker is not True:
            raise EntityTaxEvidenceError(
                "EntityVatLedger must be linked to a SUCCEEDED VAT CalculationRun"
            )
    if tax_type is not None and tax_type != "VAT":
        raise EntityTaxEvidenceError(
            "EntityVatLedger must be linked to a SUCCEEDED VAT CalculationRun; "
            "dependency has a non-VAT tax type"
        )
    if run_status is not None and run_status != "SUCCEEDED":
        raise EntityTaxEvidenceError(
            "EntityVatLedger must be linked to a SUCCEEDED VAT CalculationRun; "
            "dependency is not a SUCCEEDED result"
        )

    calculation_run_id = _int(
        _get(vat_ledger, "calculation_run_id"),
        field_name="EntityVatLedger calculation_run_id",
    )
    if calculation_run_id is None or calculation_run_id < 1:
        raise EntityTaxEvidenceError("EntityVatLedger calculation_run_id must be positive")
    if linked_run is not None:
        linked_id = _get(linked_run, "id")
        if linked_id is not None and _int(linked_id, field_name="VAT CalculationRun id") != calculation_run_id:
            raise EntityTaxEvidenceError("EntityVatLedger run identity does not match its CalculationRun")
        linked_party = _get(linked_run, "reporting_party_id")
        if linked_party is not None and _int(linked_party, field_name="VAT run reporting_party_id") != expected_party:
            raise EntityTaxEvidenceError("VAT CalculationRun reporting party does not match scope")
        linked_period = _get(linked_run, "tax_period")
        if linked_period is not None and _explicit_period(linked_period, field_name="VAT run tax_period") != expected_period:
            raise EntityTaxEvidenceError("VAT CalculationRun tax period does not match scope")
    return ledger_id


def _vat_snapshot(vat_ledger: Any, vat_calculation_run: Any | None) -> dict[str, Any]:
    linked_run = vat_calculation_run or _get(vat_ledger, "calculation_run")
    return {
        "id": _get(vat_ledger, "id"),
        "calculation_run_id": _get(vat_ledger, "calculation_run_id"),
        "reporting_party_id": _get(vat_ledger, "reporting_party_id"),
        "tax_period": _canonical_date(_get(vat_ledger, "tax_period")),
        "tax_type": _get(linked_run, "tax_type", _get(vat_ledger, "tax_type")),
        "run_status": _get(linked_run, "run_status", _get(vat_ledger, "run_status")),
        "run_result_sha256": _get(linked_run, "result_sha256", _get(vat_ledger, "result_sha256")),
    }


def build_entity_tax_input_snapshot(
    *,
    reporting_party_id: int,
    tax_period: date | str,
    vat_ledger: Any,
    selected_inputs: Iterable[EntityTaxManagementInputView],
    cit_rule: EntityTaxRuleView,
    vat_calculation_run: Any | None = None,
    ruleset_version: str = RULESET_VERSION,
) -> dict[str, Any]:
    """Build the canonical input payload hashed into ``CalculationRun``."""

    period = month_start(tax_period)
    rows = tuple(selected_inputs)
    return {
        "snapshot_version": "V3_ENTITY_TAX_LEDGER_INPUT_SNAPSHOT_V1",
        "ruleset_version": ruleset_version,
        "scope": {
            "reporting_party_id": int(reporting_party_id),
            "tax_period": period.isoformat(),
        },
        "entity_vat_ledger": _vat_snapshot(vat_ledger, vat_calculation_run),
        "management_inputs": [row.as_dict() for row in rows],
        "cit_rule": cit_rule.as_dict(),
    }


def calculate_profit(revenue: Any, real_cost: Any) -> Decimal:
    """Compute ``revenue - real_cost`` using the repository's cent rule."""

    revenue_amount = _money(revenue, field_name="revenue")
    cost_amount = _money(real_cost, field_name="real_cost")
    return (revenue_amount - cost_amount).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def calculate_estimated_cit(profit: Any, rate: Any) -> Decimal:
    """Compute non-negative CIT from an explicit rate; never supply a default."""

    try:
        profit_amount = profit if isinstance(profit, Decimal) else Decimal(str(profit))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise EntityTaxLedgerError("profit is not a valid Decimal") from exc
    if not profit_amount.is_finite():
        raise EntityTaxLedgerError("profit must be finite")
    tax_rate = _rate(rate)
    taxable_profit = max(profit_amount, Decimal("0"))
    return (taxable_profit * tax_rate).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _result_payload(
    *,
    reporting_party_id: int,
    tax_period: date,
    entity_vat_ledger_id: int,
    revenue: Decimal,
    real_cost: Decimal,
    estimated_profit: Decimal,
    estimated_cit: Decimal,
    cit_rule: EntityTaxRuleView,
    ruleset_version: str,
    input_snapshot_sha256: str,
) -> dict[str, Any]:
    return {
        "result_version": "V3_ENTITY_TAX_LEDGER_RESULT_V1",
        "ruleset_version": ruleset_version,
        "scope": {
            "reporting_party_id": reporting_party_id,
            "tax_period": tax_period.isoformat(),
        },
        "entity_vat_ledger_id": entity_vat_ledger_id,
        "revenue": str(revenue),
        "real_cost": str(real_cost),
        "estimated_profit": str(estimated_profit),
        "estimated_cit": str(estimated_cit),
        "cit_rule": cit_rule.as_dict(),
        "input_snapshot_sha256": input_snapshot_sha256,
    }


def calculate_entity_tax_ledger(
    *,
    reporting_party_id: int,
    tax_period: date | str,
    management_inputs: Iterable[Any],
    tax_rules: Iterable[Any] | Any,
    vat_ledger: Any | None = None,
    official_vat_ledger: Any | None = None,
    vat_calculation_run: Any | None = None,
    entity: Any | None = None,
    entity_is_legal: bool | None = None,
    cit_rule_code: str = "CIT_GENERAL",
    input_versions: Mapping[str, int] | None = None,
    ruleset_version: str = RULESET_VERSION,
) -> EntityTaxLedgerResult:
    """Calculate one legal-entity/month result from complete explicit evidence.

    ``management_inputs``, ``tax_rules`` and ``vat_ledger`` are intentionally
    required parameters.  A caller may pass ORM rows or the immutable view
    dataclasses.  The VAT dependency is validated but never folded into
    revenue, cost, profit, or CIT.
    """

    if ruleset_version != RULESET_VERSION:
        raise EntityTaxLedgerError(
            f"unsupported entity-tax ruleset version: {ruleset_version!r}"
        )
    party = _int(reporting_party_id, field_name="reporting_party_id")
    if party is None or party < 1:
        raise EntityTaxEvidenceError("reporting_party_id must be positive")
    period = month_start(tax_period)

    if entity is not None:
        entity_party = _get(entity, "party_id", _get(entity, "reporting_party_id"))
        if entity_party is not None and _int(entity_party, field_name="entity party_id") != party:
            raise EntityTaxEvidenceError("legal entity does not match reporting_party_id")
        legal_marker = _get(entity, "legal_entity")
        if legal_marker is not True:
            raise EntityTaxEvidenceError("entity-tax ledger requires a legal entity")
    elif entity_is_legal is not True:
        raise EntityTaxEvidenceError(
            "legal entity evidence is required for an entity-tax ledger"
        )

    selected_vat = official_vat_ledger if official_vat_ledger is not None else vat_ledger
    vat_id = validate_official_vat_ledger(
        selected_vat,
        reporting_party_id=party,
        tax_period=period,
        vat_calculation_run=vat_calculation_run,
    )
    selected_inputs = select_latest_reviewed_management_inputs(
        management_inputs,
        reporting_party_id=party,
        tax_period=period,
        input_versions=input_versions,
    )
    rule = resolve_effective_tax_rule(
        tax_rules,
        tax_period=period,
        rule_code=cit_rule_code,
    )
    revenue_row = next(row for row in selected_inputs if row.input_type == "REVENUE")
    cost_row = next(row for row in selected_inputs if row.input_type == "REAL_COST")
    revenue = revenue_row.amount
    real_cost = cost_row.amount
    profit = calculate_profit(revenue, real_cost)
    estimated_cit = calculate_estimated_cit(profit, rule.rate)

    snapshot = build_entity_tax_input_snapshot(
        reporting_party_id=party,
        tax_period=period,
        vat_ledger=selected_vat,
        selected_inputs=selected_inputs,
        cit_rule=rule,
        vat_calculation_run=vat_calculation_run,
        ruleset_version=ruleset_version,
    )
    input_hash = canonical_sha256(snapshot)
    payload = _result_payload(
        reporting_party_id=party,
        tax_period=period,
        entity_vat_ledger_id=vat_id,
        revenue=revenue,
        real_cost=real_cost,
        estimated_profit=profit,
        estimated_cit=estimated_cit,
        cit_rule=rule,
        ruleset_version=ruleset_version,
        input_snapshot_sha256=input_hash,
    )
    result_hash = canonical_sha256(payload)
    return EntityTaxLedgerResult(
        reporting_party_id=party,
        tax_period=period,
        entity_vat_ledger_id=vat_id,
        revenue=revenue,
        real_cost=real_cost,
        estimated_profit=profit,
        estimated_cit=estimated_cit,
        rule_version=ruleset_version,
        input_snapshot_sha256=input_hash,
        result_sha256=result_hash,
        revenue_input_id=revenue_row.id,
        real_cost_input_id=cost_row.id,
        revenue_input_version=revenue_row.input_version,
        real_cost_input_version=cost_row.input_version,
        cit_rule_code=rule.code,
        cit_rate=rule.rate,
        selected_inputs=selected_inputs,
        input_snapshot=snapshot,
        result_payload=payload,
    )


calculate_entity_tax = calculate_entity_tax_ledger


def canonical_json(payload: Any) -> str:
    """Render canonical UTF-8 JSON for audit hashes."""

    def default(value: Any) -> Any:
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if is_dataclass(value):
            return asdict(value)
        if isinstance(value, Mapping):
            return dict(value)
        raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")

    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=default,
    )


def canonical_sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


canonical_hash = canonical_sha256


__all__ = [
    "ENTITY_TAX_TYPE",
    "MONEY_QUANTUM",
    "RULESET_VERSION",
    "EntityTaxEvidenceError",
    "EntityTaxInputView",
    "EntityTaxLedgerCalculation",
    "EntityTaxLedgerError",
    "EntityTaxLedgerResult",
    "EntityTaxManagementInputView",
    "EntityTaxRuleError",
    "EntityTaxRuleView",
    "OfficialVatLedgerView",
    "build_entity_tax_input_snapshot",
    "calculate_entity_tax",
    "calculate_entity_tax_ledger",
    "calculate_estimated_cit",
    "calculate_profit",
    "canonical_hash",
    "canonical_json",
    "canonical_sha256",
    "month_start",
    "resolve_effective_cit_rule",
    "resolve_effective_tax_rule",
    "resolve_management_inputs",
    "select_latest_reviewed_management_inputs",
    "validate_official_vat_ledger",
]
