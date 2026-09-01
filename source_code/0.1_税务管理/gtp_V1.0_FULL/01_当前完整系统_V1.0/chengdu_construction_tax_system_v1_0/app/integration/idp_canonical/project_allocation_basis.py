"""Pure Task29 project-allocation basis and conservation rules.

Task29 deliberately keeps tax semantics out of Payment/Contract allocation.
For those Fact types, ``allocated_net=amount, allocated_vat=0,
allocated_gross=amount`` is only a Project monetary-allocation representation,
not a VAT/tax decomposition.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable
import unicodedata

from .project_attribution_schemas import ProjectAllocationInput


TASK29_PROJECT_ATTRIBUTION_RULESET_V1 = "TASK29_PROJECT_ATTRIBUTION_V1"
INVOICE_PROJECT_BASIS_V1 = "INVOICE_PROJECT_BASIS_V1"
PAYMENT_PROJECT_BASIS_V1 = "PAYMENT_PROJECT_BASIS_V1"
CONTRACT_PROJECT_BASIS_V1 = "CONTRACT_PROJECT_BASIS_V1"
MONEY_TOLERANCE = Decimal("0.01")
CENT = Decimal("0.01")


class ProjectAllocationValidationError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ProjectAllocationBasis:
    fact_type: str
    net: Decimal
    vat: Decimal
    gross: Decimal
    basis_version: str
    audit_note: str


@dataclass(frozen=True)
class ProjectAllocationPlan:
    project_code: str
    net: Decimal
    vat: Decimal
    gross: Decimal
    note: str | None


def normalize_project_code(value: str) -> str:
    """NFKC + trim only. No case-folding, whitespace deletion, or fuzzy logic."""

    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized:
        raise ProjectAllocationValidationError("PROJECT_CODE_BLANK", "project_code must not be blank")
    return normalized


def _decimal(value: object, *, field: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ProjectAllocationValidationError("INVALID_MONEY", f"{field} is not a valid decimal") from exc
    if not result.is_finite():
        raise ProjectAllocationValidationError("INVALID_MONEY", f"{field} must be finite")
    return result


def _money(value: object, *, field: str) -> Decimal:
    result = _decimal(value, field=field)
    rounded = result.quantize(CENT)
    if result != rounded:
        raise ProjectAllocationValidationError(
            "MONEY_PRECISION_EXCEEDED",
            f"{field} must not contain more than two significant decimal places",
        )
    return rounded


def _balanced(net: Decimal, vat: Decimal, gross: Decimal, *, scope: str) -> None:
    if abs((net + vat) - gross) > MONEY_TOLERANCE:
        raise ProjectAllocationValidationError(
            "ALLOCATION_COMPONENT_IMBALANCE",
            f"{scope}: net + vat must equal gross within {MONEY_TOLERANCE}",
        )


def _same_direction(value: Decimal, basis: Decimal, *, field: str, project_code: str) -> None:
    if basis > 0 and value < 0:
        raise ProjectAllocationValidationError(
            "ALLOCATION_SIGN_CONFLICT",
            f"{project_code}: {field} must not reverse the positive Fact basis",
        )
    if basis < 0 and value > 0:
        raise ProjectAllocationValidationError(
            "ALLOCATION_SIGN_CONFLICT",
            f"{project_code}: {field} must not reverse the negative Fact basis",
        )
    if basis == 0 and value != 0:
        raise ProjectAllocationValidationError(
            "ALLOCATION_SIGN_CONFLICT",
            f"{project_code}: {field} must be zero because the Fact basis is zero",
        )


def build_project_allocation_basis(*, fact_type: str, specialized_fact: object) -> ProjectAllocationBasis:
    fact_type = str(fact_type or "").strip().upper()
    if fact_type == "INVOICE":
        raw_net = getattr(specialized_fact, "net_amount", None)
        raw_vat = getattr(specialized_fact, "vat_amount", None)
        raw_gross = getattr(specialized_fact, "gross_amount", None)
        if raw_net is None or raw_vat is None or raw_gross is None:
            raise ProjectAllocationValidationError(
                "INVOICE_BASIS_INCOMPLETE",
                "Invoice project attribution requires net_amount, vat_amount, and gross_amount",
            )
        net = _money(raw_net, field="invoice.net_amount")
        vat = _money(raw_vat, field="invoice.vat_amount")
        gross = _money(raw_gross, field="invoice.gross_amount")
        _balanced(net, vat, gross, scope="invoice basis")
        return ProjectAllocationBasis(
            fact_type="INVOICE",
            net=net,
            vat=vat,
            gross=gross,
            basis_version=INVOICE_PROJECT_BASIS_V1,
            audit_note="INVOICE_COMPONENT_BASIS;NET_VAT_GROSS_FROM_CANONICAL_INVOICE_FACT",
        )

    if fact_type == "PAYMENT":
        raw_amount = getattr(specialized_fact, "amount", None)
        if raw_amount is None:
            raise ProjectAllocationValidationError("PAYMENT_BASIS_INCOMPLETE", "Payment amount is required")
        amount = _money(raw_amount, field="payment.amount")
        if amount <= 0:
            raise ProjectAllocationValidationError("PAYMENT_BASIS_INVALID", "Payment amount must be positive")
        return ProjectAllocationBasis(
            fact_type="PAYMENT",
            net=amount,
            vat=Decimal("0.00"),
            gross=amount,
            basis_version=PAYMENT_PROJECT_BASIS_V1,
            audit_note="PROJECT_MONETARY_BASIS;PAYMENT_AMOUNT;VAT_ZERO_IS_NOT_TAX_DECOMPOSITION",
        )

    if fact_type == "CONTRACT":
        raw_amount = getattr(specialized_fact, "contract_amount", None)
        if raw_amount is None:
            raise ProjectAllocationValidationError(
                "CONTRACT_BASIS_INCOMPLETE",
                "Contract contract_amount is required for project attribution",
            )
        amount = _money(raw_amount, field="contract.contract_amount")
        if amount < 0:
            raise ProjectAllocationValidationError("CONTRACT_BASIS_INVALID", "Contract amount must not be negative")
        return ProjectAllocationBasis(
            fact_type="CONTRACT",
            net=amount,
            vat=Decimal("0.00"),
            gross=amount,
            basis_version=CONTRACT_PROJECT_BASIS_V1,
            audit_note="PROJECT_MONETARY_BASIS;CONTRACT_AMOUNT;VAT_ZERO_IS_NOT_TAX_DECOMPOSITION",
        )

    raise ProjectAllocationValidationError(
        "UNSUPPORTED_FACT_TYPE",
        f"Task29 supports only INVOICE, PAYMENT, and CONTRACT facts; got {fact_type or '<blank>'}",
    )


def _has_invoice_components(item: ProjectAllocationInput) -> bool:
    return any(value is not None for value in (item.allocated_net, item.allocated_vat, item.allocated_gross))


def _has_any_amount(item: ProjectAllocationInput) -> bool:
    return _has_invoice_components(item) or item.allocated_amount is not None


def build_project_allocation_plans(
    *,
    basis: ProjectAllocationBasis,
    allocations: Iterable[ProjectAllocationInput],
) -> list[ProjectAllocationPlan]:
    items = list(allocations)
    if not items:
        raise ProjectAllocationValidationError("PROJECT_EVIDENCE_INSUFFICIENT", "at least one project_code is required")

    normalized_codes = [normalize_project_code(item.project_code) for item in items]
    if len(set(normalized_codes)) != len(normalized_codes):
        raise ProjectAllocationValidationError(
            "DUPLICATE_PROJECT_CODE",
            "the same exact project_code must not appear more than once in one attribution request",
        )

    # A single explicit project may omit amounts: the full canonical Fact basis
    # is then deterministic and does not require proportional inference.
    if len(items) == 1 and not _has_any_amount(items[0]):
        return [
            ProjectAllocationPlan(
                project_code=normalized_codes[0],
                net=basis.net,
                vat=basis.vat,
                gross=basis.gross,
                note=items[0].note,
            )
        ]

    plans: list[ProjectAllocationPlan] = []
    for item, project_code in zip(items, normalized_codes, strict=True):
        if basis.fact_type == "INVOICE":
            if item.allocated_amount is not None:
                raise ProjectAllocationValidationError(
                    "INVOICE_ALLOCATION_SHAPE_INVALID",
                    f"{project_code}: Invoice allocations must use allocated_net/allocated_vat/allocated_gross",
                )
            if item.allocated_net is None or item.allocated_vat is None or item.allocated_gross is None:
                raise ProjectAllocationValidationError(
                    "INVOICE_ALLOCATION_INCOMPLETE",
                    f"{project_code}: Invoice allocation requires net, vat, and gross components",
                )
            net = _money(item.allocated_net, field=f"{project_code}.allocated_net")
            vat = _money(item.allocated_vat, field=f"{project_code}.allocated_vat")
            gross = _money(item.allocated_gross, field=f"{project_code}.allocated_gross")
            _balanced(net, vat, gross, scope=project_code)
        else:
            if _has_invoice_components(item):
                raise ProjectAllocationValidationError(
                    "MONETARY_ALLOCATION_SHAPE_INVALID",
                    f"{project_code}: {basis.fact_type} allocation accepts allocated_amount only; VAT decomposition is out of scope",
                )
            if item.allocated_amount is None:
                raise ProjectAllocationValidationError(
                    "MONETARY_ALLOCATION_INCOMPLETE",
                    f"{project_code}: allocated_amount is required for multi-project {basis.fact_type} allocation",
                )
            amount = _money(item.allocated_amount, field=f"{project_code}.allocated_amount")
            net, vat, gross = amount, Decimal("0.00"), amount

        _same_direction(net, basis.net, field="allocated_net", project_code=project_code)
        _same_direction(vat, basis.vat, field="allocated_vat", project_code=project_code)
        _same_direction(gross, basis.gross, field="allocated_gross", project_code=project_code)
        plans.append(ProjectAllocationPlan(project_code=project_code, net=net, vat=vat, gross=gross, note=item.note))

    net_total = sum((plan.net for plan in plans), Decimal("0.00"))
    vat_total = sum((plan.vat for plan in plans), Decimal("0.00"))
    gross_total = sum((plan.gross for plan in plans), Decimal("0.00"))
    mismatches: list[str] = []
    if abs(net_total - basis.net) > MONEY_TOLERANCE:
        mismatches.append(f"net {net_total} != {basis.net}")
    if abs(vat_total - basis.vat) > MONEY_TOLERANCE:
        mismatches.append(f"vat {vat_total} != {basis.vat}")
    if abs(gross_total - basis.gross) > MONEY_TOLERANCE:
        mismatches.append(f"gross {gross_total} != {basis.gross}")
    if mismatches:
        raise ProjectAllocationValidationError(
            "ALLOCATION_AGGREGATE_MISMATCH",
            "explicit project allocations do not conserve the canonical Fact basis: " + "; ".join(mismatches),
        )
    return plans
