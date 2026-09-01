"""Pure deterministic rules for Task17 Project Tax Analysis.

Project attribution and tax attribution remain orthogonal: Project Allocation
chooses the project; explicit Tax Events choose the tax month.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

from app.calc.basis.contracts import BasisSource, CalculationBasis, SourceKind, SourceRole, assert_source_allowed

RULESET_VERSION = "V3_PROJECT_TAX_ANALYSIS_V1"
PROJECT_TAX_TYPE = "PROJECT_TAX"
MONEY_QUANTUM = Decimal("0.01")
TOLERANCE = Decimal("0.01")


class ProjectTaxAnalysisError(ValueError):
    pass


class ProjectAllocationError(ProjectTaxAnalysisError):
    pass


@dataclass(frozen=True)
class AllocationView:
    id: int | None
    fact_id: int
    project_id: int
    allocated_net: Decimal
    allocated_vat: Decimal
    allocated_gross: Decimal
    status: str = "CONFIRMED"
    is_current: bool = True


@dataclass(frozen=True)
class AllocationCoverage:
    status: str
    allocated_net: Decimal
    allocated_vat: Decimal
    allocated_gross: Decimal
    remaining_net: Decimal
    remaining_vat: Decimal
    remaining_gross: Decimal

    def as_dict(self) -> dict[str, str]:
        return {name: str(value) if name != "status" else value for name, value in self.__dict__.items()}


@dataclass(frozen=True)
class EventShare:
    allocation_id: int | None
    project_id: int
    tax_amount: Decimal
    taxable_amount: Decimal | None = None


def _money(value: Any, *, field_name: str) -> Decimal:
    if isinstance(value, bool) or value is None or value == "":
        raise ProjectAllocationError(f"{field_name} must be a Decimal-compatible value")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ProjectAllocationError(f"{field_name} is not a valid Decimal") from exc
    if not result.is_finite():
        raise ProjectAllocationError(f"{field_name} must be finite")
    return result.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def canonical_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def coerce_allocation(row: Any) -> AllocationView:
    def get(name: str, default: Any = None) -> Any:
        return row.get(name, default) if isinstance(row, Mapping) else getattr(row, name, default)
    return AllocationView(
        id=int(get("id")) if get("id") is not None else None,
        fact_id=int(get("fact_id")),
        project_id=int(get("project_id")),
        allocated_net=_money(get("allocated_net"), field_name="allocated_net"),
        allocated_vat=_money(get("allocated_vat"), field_name="allocated_vat"),
        allocated_gross=_money(get("allocated_gross"), field_name="allocated_gross"),
        status=str(get("status", "")).strip().upper(),
        is_current=get("is_current", False) is True,
    )


def _sign_compatible(source: Decimal, allocated: Decimal) -> bool:
    if source > 0:
        return allocated >= 0
    if source < 0:
        return allocated <= 0
    return abs(allocated) <= TOLERANCE


def validate_allocation_row(allocation: AllocationView, *, source_net: Decimal, source_vat: Decimal, source_gross: Decimal) -> None:
    if allocation.status != "CONFIRMED" or not allocation.is_current:
        raise ProjectAllocationError("only current CONFIRMED allocations are canonical")
    if abs((allocation.allocated_net + allocation.allocated_vat) - allocation.allocated_gross) > TOLERANCE:
        raise ProjectAllocationError(f"allocation {allocation.id} does not balance net + VAT = gross")
    for field_name, source, allocated in (("net", source_net, allocation.allocated_net), ("vat", source_vat, allocation.allocated_vat), ("gross", source_gross, allocation.allocated_gross)):
        if not _sign_compatible(source, allocated):
            raise ProjectAllocationError(f"allocation {allocation.id} {field_name} sign conflicts with source Fact")


def allocation_coverage(*, source_net: Any, source_vat: Any, source_gross: Any, allocations: Iterable[Any]) -> AllocationCoverage:
    source_net_d = _money(source_net, field_name="source_net")
    source_vat_d = _money(source_vat, field_name="source_vat")
    source_gross_d = _money(source_gross, field_name="source_gross")
    rows = tuple(coerce_allocation(row) for row in allocations)
    for row in rows:
        validate_allocation_row(row, source_net=source_net_d, source_vat=source_vat_d, source_gross=source_gross_d)
    sums = {
        "net": sum((row.allocated_net for row in rows), Decimal("0.00")),
        "vat": sum((row.allocated_vat for row in rows), Decimal("0.00")),
        "gross": sum((row.allocated_gross for row in rows), Decimal("0.00")),
    }
    sources = {"net": source_net_d, "vat": source_vat_d, "gross": source_gross_d}
    over = any((abs(sums[name]) > TOLERANCE if abs(sources[name]) <= TOLERANCE else abs(sums[name]) > abs(sources[name]) + TOLERANCE) for name in sources)
    if over:
        status = "OVER"
    elif all(abs(sums[name] - sources[name]) <= TOLERANCE for name in sources):
        status = "FULL"
    elif not rows or all(abs(value) <= TOLERANCE for value in sums.values()):
        status = "NONE"
    else:
        status = "PARTIAL"
    return AllocationCoverage(
        status=status,
        allocated_net=sums["net"].quantize(MONEY_QUANTUM),
        allocated_vat=sums["vat"].quantize(MONEY_QUANTUM),
        allocated_gross=sums["gross"].quantize(MONEY_QUANTUM),
        remaining_net=(source_net_d - sums["net"]).quantize(MONEY_QUANTUM),
        remaining_vat=(source_vat_d - sums["vat"]).quantize(MONEY_QUANTUM),
        remaining_gross=(source_gross_d - sums["gross"]).quantize(MONEY_QUANTUM),
    )


def allocate_vat_event(*, event_amount: Any, source_vat: Any, allocations: Sequence[Any], source_net: Any | None = None) -> tuple[EventShare, ...]:
    assert_source_allowed(CalculationBasis.TAX, BasisSource(SourceKind.INVOICE_FACT, SourceRole.PRIMARY_AMOUNT))
    event = _money(event_amount, field_name="event_amount")
    source_vat_d = _money(source_vat, field_name="source_vat")
    if abs(source_vat_d) <= TOLERANCE:
        if abs(event) <= TOLERANCE:
            return ()
        raise ProjectAllocationError("non-zero VAT event cannot use a zero-VAT Fact denominator")
    source_net_d = _money(source_net, field_name="source_net") if source_net is not None else None
    rows = sorted((coerce_allocation(row) for row in allocations), key=lambda row: (row.project_id, row.id or 0))
    for row in rows:
        if row.status != "CONFIRMED" or not row.is_current:
            raise ProjectAllocationError("Tax Events may only consume current CONFIRMED allocations")
    shares: list[EventShare] = []
    for row in rows:
        ratio = abs(row.allocated_vat) / abs(source_vat_d)
        tax_amount = (event * ratio).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
        taxable_amount = None
        if source_net_d is not None:
            taxable_amount = (row.allocated_net * (event / source_vat_d)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
        shares.append(EventShare(row.id, row.project_id, tax_amount, taxable_amount))
    full = abs(sum((abs(row.allocated_vat) for row in rows), Decimal("0.00")) - abs(source_vat_d)) <= TOLERANCE
    if full and shares:
        residual = event - sum((share.tax_amount for share in shares), Decimal("0.00"))
        taxable_residual = Decimal("0.00")
        if source_net_d is not None:
            expected_taxable = (source_net_d * (event / source_vat_d)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
            taxable_residual = expected_taxable - sum((share.taxable_amount for share in shares if share.taxable_amount is not None), Decimal("0.00"))
        if residual or taxable_residual:
            last = shares[-1]
            shares[-1] = EventShare(last.allocation_id, last.project_id, (last.tax_amount + residual).quantize(MONEY_QUANTUM), ((last.taxable_amount + taxable_residual).quantize(MONEY_QUANTUM) if last.taxable_amount is not None else None))
    return tuple(shares)


def summarize_project_components(components: Iterable[Mapping[str, Any]]) -> dict[str, Decimal]:
    output_taxable_net = Decimal("0.00")
    output_vat = Decimal("0.00")
    claimed_input_vat = Decimal("0.00")
    tax_prepayment = Decimal("0.00")
    for component in components:
        kind = str(component["component_type"]).upper()
        tax_amount = _money(component["tax_amount"], field_name="component tax_amount")
        taxable = component.get("taxable_amount")
        if kind == "OUTPUT_VAT":
            output_vat += tax_amount
            if taxable is not None:
                output_taxable_net += _money(taxable, field_name="component taxable_amount")
        elif kind == "INPUT_VAT":
            claimed_input_vat += tax_amount
        elif kind == "TAX_PREPAYMENT":
            tax_prepayment += tax_amount
        else:
            raise ProjectTaxAnalysisError(f"unknown component_type: {kind}")
    before = (output_vat - claimed_input_vat).quantize(MONEY_QUANTUM)
    after = (before - tax_prepayment).quantize(MONEY_QUANTUM)
    return {
        "output_taxable_net": output_taxable_net.quantize(MONEY_QUANTUM),
        "output_vat": output_vat.quantize(MONEY_QUANTUM),
        "claimed_input_vat": claimed_input_vat.quantize(MONEY_QUANTUM),
        "tax_prepayment": tax_prepayment.quantize(MONEY_QUANTUM),
        "net_vat_before_entity_credit": before,
        "net_vat_after_project_prepayment": after,
    }
