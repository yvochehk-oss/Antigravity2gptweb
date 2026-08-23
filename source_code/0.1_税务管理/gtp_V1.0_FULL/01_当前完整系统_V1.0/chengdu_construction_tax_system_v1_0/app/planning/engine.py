"""Deterministic project allocation optimizer.

AI never performs authoritative arithmetic here. This module receives explicit
party profiles and produces feasible, auditable scenarios using Decimal math.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

D = Decimal
CENT = D("0.01")


@dataclass(frozen=True)
class PartyProfile:
    code: str
    scope: str  # internal | external
    role: str = ""
    capacity: Decimal | None = None
    external_cost_ratio: Decimal = D("1")
    tax_cash_rate: Decimal = D("0")
    risk_score: Decimal = D("0.5")
    evidence_quality: Decimal = D("0.5")
    eligible: bool = True
    rationale: str = ""
    data_gaps: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.scope not in {"internal", "external"}:
            raise ValueError("scope must be internal or external")
        for name in ("external_cost_ratio", "risk_score", "evidence_quality"):
            value = getattr(self, name)
            if value < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.capacity is not None and self.capacity < 0:
            raise ValueError("capacity cannot be negative")


@dataclass(frozen=True)
class PlanningRequest:
    package_amount: Decimal
    category: str
    objective: str = "balanced"
    internal_min_ratio: Decimal = D("0")
    internal_max_ratio: Decimal = D("1")
    preferred_internal_ratio: Decimal | None = None

    def __post_init__(self) -> None:
        if self.package_amount <= 0:
            raise ValueError("package_amount must be positive")
        if not D("0") <= self.internal_min_ratio <= D("1"):
            raise ValueError("internal_min_ratio must be between 0 and 1")
        if not D("0") <= self.internal_max_ratio <= D("1"):
            raise ValueError("internal_max_ratio must be between 0 and 1")
        if self.internal_min_ratio > self.internal_max_ratio:
            raise ValueError("internal_min_ratio cannot exceed internal_max_ratio")
        if self.preferred_internal_ratio is not None and not D("0") <= self.preferred_internal_ratio <= D("1"):
            raise ValueError("preferred_internal_ratio must be between 0 and 1")
        if self.objective not in {"balanced", "profit", "tax", "risk"}:
            raise ValueError("unsupported objective")


@dataclass
class AllocationLine:
    scope: str
    party_code: str
    amount: Decimal
    share: Decimal
    estimated_external_cost: Decimal
    estimated_tax_cash: Decimal
    risk_score: Decimal
    evidence_quality: Decimal
    rationale: str = ""


@dataclass
class Scenario:
    scenario_id: str
    internal_ratio: Decimal
    internal_amount: Decimal
    external_amount: Decimal
    allocations: list[AllocationLine]
    system_external_cost: Decimal
    incremental_tax_cash: Decimal
    weighted_risk: Decimal
    evidence_quality: Decimal
    concentration: Decimal
    savings_vs_all_external: Decimal
    score: Decimal
    data_gaps: list[str] = field(default_factory=list)


def _q(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, value))


def _weight(profile: PartyProfile) -> Decimal:
    # Higher evidence and lower risk/cost get more allocation. The floor avoids
    # a zero-weight dead-end while still letting hard capacity/eligibility win.
    cost_factor = _clamp(D("1.25") - profile.external_cost_ratio, D("0.05"), D("1.25"))
    risk_factor = _clamp(D("1") - profile.risk_score, D("0.05"), D("1"))
    evidence = _clamp(profile.evidence_quality, D("0.05"), D("1"))
    return max(D("0.0001"), cost_factor * risk_factor * evidence)


def _allocate(total: Decimal, profiles: list[PartyProfile]) -> list[tuple[PartyProfile, Decimal]]:
    if total <= 0:
        return []
    active = [p for p in profiles if p.eligible and (p.capacity is None or p.capacity > 0)]
    if not active:
        raise ValueError("no eligible party for required allocation")
    known_capacity = sum((p.capacity or D("0") for p in active), D("0"))
    unlimited = any(p.capacity is None for p in active)
    if not unlimited and known_capacity + CENT < total:
        raise ValueError(f"eligible capacity {known_capacity} is below required allocation {total}")

    remaining = total
    amounts = {p.code: D("0") for p in active}
    open_profiles = list(active)
    for _ in range(len(active) + 2):
        if remaining <= CENT / 2 or not open_profiles:
            break
        weight_sum = sum((_weight(p) for p in open_profiles), D("0"))
        distributed = D("0")
        next_open: list[PartyProfile] = []
        for p in open_profiles:
            proposed = remaining * _weight(p) / weight_sum
            room = None if p.capacity is None else max(D("0"), p.capacity - amounts[p.code])
            give = proposed if room is None else min(proposed, room)
            amounts[p.code] += give
            distributed += give
            if room is None or room - give > CENT:
                next_open.append(p)
        if distributed <= D("0.000001"):
            break
        remaining -= distributed
        open_profiles = next_open

    if remaining > CENT:
        # Final cent-level fill to the first party with room.
        for p in active:
            room = None if p.capacity is None else p.capacity - amounts[p.code]
            if room is None or room + CENT >= remaining:
                amounts[p.code] += remaining
                remaining = D("0")
                break
    if remaining > CENT:
        raise ValueError(f"unable to allocate remaining amount {remaining}")

    rounded: list[tuple[PartyProfile, Decimal]] = []
    running = D("0")
    for i, p in enumerate(active):
        amount = _q(amounts[p.code])
        if i == len(active) - 1:
            amount = _q(total - running)
        if amount > 0:
            rounded.append((p, amount))
            running += amount
    return rounded


def _candidate_ratios(req: PlanningRequest, has_internal: bool, has_external: bool) -> list[Decimal]:
    if not has_internal:
        return [D("0")]
    if not has_external:
        return [D("1")]
    raw = [req.internal_min_ratio, D("0.40"), D("0.55"), D("0.65"), D("0.75"), D("0.85"), req.internal_max_ratio]
    if req.preferred_internal_ratio is not None:
        raw.append(req.preferred_internal_ratio)
    out = sorted({_clamp(x, req.internal_min_ratio, req.internal_max_ratio).quantize(D("0.01")) for x in raw})
    return out


def _score(s: Scenario, req: PlanningRequest) -> Decimal:
    amount = req.package_amount
    saving_rate = _clamp(s.savings_vs_all_external / amount, D("-1"), D("1"))
    tax_rate = _clamp(s.incremental_tax_cash / amount, D("-0.5"), D("0.5"))
    risk = _clamp(s.weighted_risk, D("0"), D("1"))
    evidence = _clamp(s.evidence_quality, D("0"), D("1"))
    concentration = _clamp(s.concentration, D("0"), D("1"))
    weights = {
        "balanced": (D("0.35"), D("0.15"), D("0.25"), D("0.15"), D("0.10")),
        "profit": (D("0.55"), D("0.10"), D("0.15"), D("0.15"), D("0.05")),
        "tax": (D("0.20"), D("0.35"), D("0.20"), D("0.15"), D("0.10")),
        "risk": (D("0.15"), D("0.10"), D("0.45"), D("0.20"), D("0.10")),
    }[req.objective]
    w_save, w_tax, w_risk, w_evidence, w_conc = weights
    # Score 0..100-ish. Negative tax cash means an estimated cash-tax reduction.
    raw = D("50") + D("50") * (
        w_save * saving_rate
        - w_tax * tax_rate
        + w_risk * (D("0.5") - risk)
        + w_evidence * (evidence - D("0.5"))
        + w_conc * (D("0.5") - concentration)
    )
    return _clamp(raw, D("0"), D("100")).quantize(D("0.01"))


def _build_one(req: PlanningRequest, ratio: Decimal, internals: list[PartyProfile], externals: list[PartyProfile], idx: int) -> Scenario:
    internal_amount = _q(req.package_amount * ratio)
    external_amount = _q(req.package_amount - internal_amount)
    pairs = _allocate(internal_amount, internals) + _allocate(external_amount, externals)
    lines: list[AllocationLine] = []
    ext_cost = tax_cash = risk_total = evidence_total = D("0")
    max_amount = D("0")
    gaps: list[str] = []
    for p, amount in pairs:
        share = amount / req.package_amount
        line_ext = _q(amount * p.external_cost_ratio)
        line_tax = _q(amount * p.tax_cash_rate)
        lines.append(AllocationLine(
            scope=p.scope, party_code=p.code, amount=amount, share=share,
            estimated_external_cost=line_ext, estimated_tax_cash=line_tax,
            risk_score=p.risk_score, evidence_quality=p.evidence_quality,
            rationale=p.rationale,
        ))
        ext_cost += line_ext
        tax_cash += line_tax
        risk_total += amount * p.risk_score
        evidence_total += amount * p.evidence_quality
        max_amount = max(max_amount, amount)
        gaps.extend(g for g in p.data_gaps if g not in gaps)
    scenario = Scenario(
        scenario_id=f"S{idx:02d}", internal_ratio=ratio,
        internal_amount=internal_amount, external_amount=external_amount,
        allocations=lines, system_external_cost=_q(ext_cost), incremental_tax_cash=_q(tax_cash),
        weighted_risk=(risk_total / req.package_amount).quantize(D("0.0001")),
        evidence_quality=(evidence_total / req.package_amount).quantize(D("0.0001")),
        concentration=(max_amount / req.package_amount).quantize(D("0.0001")),
        savings_vs_all_external=_q(req.package_amount - ext_cost), score=D("0"), data_gaps=gaps,
    )
    scenario.score = _score(scenario, req)
    return scenario


def build_scenarios(req: PlanningRequest, profiles: Iterable[PartyProfile]) -> list[Scenario]:
    profiles = [p for p in profiles if p.eligible]
    internals = [p for p in profiles if p.scope == "internal"]
    externals = [p for p in profiles if p.scope == "external"]
    if not internals and req.internal_min_ratio > 0:
        raise ValueError("internal allocation required but no eligible internal entity")
    if not externals and req.internal_max_ratio < 1:
        raise ValueError("external allocation required but no eligible external counterparty")
    scenarios: list[Scenario] = []
    errors: list[str] = []
    for idx, ratio in enumerate(_candidate_ratios(req, bool(internals), bool(externals)), 1):
        try:
            scenarios.append(_build_one(req, ratio, internals, externals, idx))
        except ValueError as exc:
            errors.append(f"ratio={ratio}: {exc}")
    if not scenarios:
        raise ValueError("no feasible allocation scenario; " + "; ".join(errors))
    return sorted(scenarios, key=lambda x: (x.score, x.evidence_quality, -x.weighted_risk), reverse=True)
