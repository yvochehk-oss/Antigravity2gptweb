"""Database-backed planning profiles, deterministic simulation and AI recommendation."""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..ai.failover import call_with_failover
from ..models import (
    AIModelEndpoint,
    Contract,
    Entity,
    ExternalParty,
    Fulfillment,
    Invoice,
    PlanningAllocation,
    PlanningScenario,
    Progress,
    Project,
    RealCost,
    TaxPaymentRecord,
)
from .engine import D, PartyProfile, PlanningRequest, Scenario, build_scenarios

CATEGORY_ROLE = {"材料": "B", "material": "B", "劳务": "C", "labor": "C", "设备": "D", "equipment": "D", "专业分包": "A", "subcontract": "A"}
CATEGORY_EXTERNAL_KEYWORDS = {
    "材料": ("材料", "商贸", "贸易", "供应", "建材"),
    "material": ("材料", "商贸", "贸易", "供应", "建材"),
    "劳务": ("劳务", "人工", "班组"),
    "labor": ("劳务", "人工", "班组"),
    "设备": ("设备", "机械", "租赁"),
    "equipment": ("设备", "机械", "租赁"),
    "专业分包": ("分包", "工程", "施工"),
    "subcontract": ("分包", "工程", "施工"),
}

# Planning recommendations are persisted as one scenario plus its allocation
# rows. Serialize the small critical section so two browser retries in the
# same process cannot both pass the idempotency lookup before either commits.
# The database transaction remains the source of truth for atomicity.
_PERSIST_LOCK = threading.RLock()
_AUTO_IDEMPOTENCY_WINDOW = timedelta(minutes=5)

PLANNING_SCENARIO_SCOPE = "SIMULATION"
PLANNING_FACT_BASIS = {
    "project": "CANONICAL_FACTS",
    "entity_vat": "entity_vat_ledgers",
    "cit": "UNAVAILABLE",
}


def _dec(value: Any, default: str = "0") -> Decimal:
    if value is None:
        return D(default)
    return D(str(value))


def _bounded(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, value))


def _internal_profile(db: Session, code: str, role: str, package_amount: Decimal) -> PartyProfile:
    """Build an internal-party profile without crossing statutory truth scopes.

    Operating evidence may come from real invoice and external-cash facts. The
    planning engine must not derive a legal-entity VAT liability or CIT estimate
    from the retired mixed-scope TaxLedger. Until a dedicated scenario-safe tax
    feature is available, tax_cash_rate remains neutral and the data gap is
    explicit rather than fabricated.
    """
    out_revenue = _dec(db.scalar(
        select(func.coalesce(func.sum(Invoice.net), 0)).where(
            Invoice.entity_code == code,
            Invoice.direction == "out",
        )
    ))
    ext_cost = _dec(db.scalar(
        select(func.coalesce(func.sum(RealCost.amount), 0)).where(
            RealCost.entity_code == code,
            RealCost.external_cash.is_(True),
        )
    ))
    gaps: list[str] = []
    evidence_parts = 0
    if out_revenue > 0:
        cost_ratio = _bounded(ext_cost / out_revenue, D("0"), D("1.20"))
        evidence_parts += 1
    else:
        # Neutral: without history, do not fabricate an internal saving.
        cost_ratio = D("1")
        gaps.append(f"{code}缺少可用于估算穿透成本率的真实对外开票收入")

    # Legal-entity VAT belongs exclusively to entity_vat_ledgers. Invoice facts
    # can describe operating history, but Planning must not turn them into a
    # filing-looking VAT liability. CIT has no canonical estimator in this step.
    tax_rate = D("0")
    gaps.append(f"{code}法人正式VAT仅以entity_vat_ledgers为权威；Planning不从开票事实反推法定税负")
    gaps.append(f"{code} CIT_ESTIMATE_UNAVAILABLE；不回退旧estimated_cit")

    # Historical project sales are a capacity proxy, not a legal/operational guarantee.
    project_sales = db.execute(
        select(Invoice.project_id, func.sum(Invoice.net))
        .where(Invoice.entity_code == code, Invoice.direction == "out")
        .group_by(Invoice.project_id)
    ).all()
    max_hist = max((_dec(v) for _, v in project_sales), default=D("0"))
    capacity = max_hist * D("1.25") if max_hist > 0 else None
    if capacity is None:
        gaps.append(f"{code}未配置承载能力；当前不把容量作为硬约束")
    else:
        evidence_parts += 1

    # Entity-specific risk evidence is not yet modeled. Keep neutral and surface the gap.
    risk = D("0.50")
    gaps.append(f"{code}尚无主体级履约/税务风险评分，使用中性风险值，仅供方案排序")
    evidence = D("0.35") + D("0.20") * evidence_parts
    return PartyProfile(
        code=code, scope="internal", role=role, capacity=capacity,
        external_cost_ratio=cost_ratio, tax_cash_rate=tax_rate,
        risk_score=risk, evidence_quality=min(evidence, D("0.95")),
        rationale="系统内承接：内部交易在系统合并口径抵销，成本穿透到最终系统外支出；法人法定VAT不在Planning内重算。",
        data_gaps=tuple(gaps),
    )


def _external_profile(db: Session, party: ExternalParty, package_amount: Decimal) -> PartyProfile:
    inbound = db.execute(select(Invoice).where(Invoice.counterparty_code == party.code, Invoice.direction == "in")).scalars().all()
    net = sum((_dec(x.net) for x in inbound), D("0"))
    credit = sum((_dec(x.vat) for x in inbound if x.deductible), D("0"))
    # External allocation itself leaves the system boundary; deductible input VAT
    # is represented as a negative incremental tax-cash estimate.
    tax_rate = -(credit / net) if net > 0 else D("0")
    project_amounts: dict[int, Decimal] = {}
    for row in inbound:
        project_amounts[row.project_id] = project_amounts.get(row.project_id, D("0")) + _dec(row.net)
    max_hist = max(project_amounts.values(), default=D("0"))
    capacity = max_hist * D("1.25") if max_hist > 0 else None
    fulfills = db.execute(select(Fulfillment).where(Fulfillment.counterparty_code == party.code)).scalars().all()
    if fulfills:
        complete = sum(1 for x in fulfills if x.evidence_complete)
        risk = D("1") - D(complete) / D(len(fulfills))
        evidence = D("0.75") if inbound else D("0.60")
        gaps: list[str] = []
    else:
        risk = D("0.50")
        evidence = D("0.45") if inbound else D("0.30")
        gaps = [f"{party.code}缺少履约证据历史，使用中性风险值"]
    if capacity is None:
        gaps.append(f"{party.code}无历史交易容量，当前不把容量作为硬约束")
    if net <= 0:
        gaps.append(f"{party.code}无可用于估算进项税抵扣率的历史发票")
    return PartyProfile(
        code=party.code, scope="external", role=party.kind or "external",
        capacity=capacity, external_cost_ratio=D("1"), tax_cash_rate=tax_rate,
        risk_score=_bounded(risk, D("0"), D("1")), evidence_quality=evidence,
        rationale="系统外承接：合同净额全部作为系统边界外成本，历史可抵扣进项税用于税务现金影响估算。",
        data_gaps=tuple(gaps),
    )


def _category_matches_external(party: ExternalParty, category: str) -> bool:
    keywords = CATEGORY_EXTERNAL_KEYWORDS.get(category, ())
    if not keywords:
        return True
    haystack = f"{party.kind} {party.name} {party.short_name}"
    return any(k in haystack for k in keywords)



def system_penetration_snapshot(db: Session, project_id: int) -> dict[str, Any]:
    """Return the 26-unit consolidated management truth for one project.

    Internal invoices are visible as transaction volume but eliminated from
    system revenue/cost. External real cost comes from ``real_costs.external_cash``.
    Project tax cash uses project-specific tax-payment records only; no tax is
    guessed from unrelated entity ledgers. Missing contract/nominal values stay
    missing; this snapshot never derives fact-looking values from arbitrary ratios.
    """
    from collections import defaultdict
    from decimal import Decimal as D

    from sqlalchemy import func, select

    project = db.get(Project, project_id)
    if project is None:
        raise ValueError("project not found")

    internal_entities = db.execute(
        select(Entity).where(Entity.active.is_(True), Entity.internal.is_(True))
    ).scalars().all()
    internal_map = {e.code: e.name for e in internal_entities}
    internal_codes = set(internal_map.keys())

    external_entities = db.execute(
        select(ExternalParty).where(ExternalParty.active.is_(True))
    ).scalars().all()
    external_map = {x.code: {"name": x.name, "kind": x.kind or '外部单位'} for x in external_entities}
    external_codes = set(external_map.keys())

    def _dec(v): return D(str(v)) if v else D("0")

    recognized_revenue = _dec(db.scalar(
        select(func.coalesce(func.sum(Progress.recognized_revenue), 0)).where(Progress.project_id == project_id)
    ))

    rows = db.execute(select(Invoice).where(Invoice.project_id == project_id)).scalars().all()

    internal_trade = D("0")
    external_invoice_revenue = D("0")
    unknown_counterparties: set[str] = set()

    # details dictionaries
    # revenue: buyer_code -> {'type', 'name', 'recognized'}
    rev_details = defaultdict(lambda: D("0"))
    # internal: (entity_code, counterparty_code, category) -> amount
    int_details = defaultdict(lambda: D("0"))

    for row in rows:
        if row.direction != "out" or row.entity_code not in internal_codes:
            continue
        if row.counterparty_code in internal_codes:
            internal_trade += _dec(row.net)
            int_details[(row.entity_code, row.counterparty_code, row.category)] += _dec(row.net)
        elif row.counterparty_code in external_codes:
            external_invoice_revenue += _dec(row.net)
            rev_details[row.counterparty_code] += _dec(row.net)
        elif row.counterparty_code:
            unknown_counterparties.add(row.counterparty_code)

    # external details
    ext_cost_rows = db.execute(
        select(RealCost).where(RealCost.project_id == project_id, RealCost.external_cash.is_(True))
    ).scalars().all()
    # Build entity_code → name map for real cost attribution
    entity_name_map = {e.code: e.name for e in db.execute(select(Entity)).scalars().all()}

    external_cost = D("0")
    ext_details = defaultdict(lambda: D("0"))
    for r in ext_cost_rows:
        amt = _dec(r.amount)
        external_cost += amt
        ext_details[(r.entity_code, r.counterparty_code, r.category)] += amt

    tax_paid = _dec(db.scalar(
        select(func.coalesce(func.sum(TaxPaymentRecord.tax_amount), 0)).where(TaxPaymentRecord.project_id == project_id)
    ))

    management_profit_after_tax = recognized_revenue - external_cost - tax_paid
    data_gaps: list[str] = []
    if not external_codes:
        data_gaps.append("external_parties 为空，无法对外部开票收入做主数据交叉核验")
    if unknown_counterparties:
        data_gaps.append("存在未归入系统内/系统外主数据的对手方: " + ", ".join(sorted(unknown_counterparties)))
    if recognized_revenue == 0 and external_invoice_revenue > 0:
        data_gaps.append("存在对外开票但项目确认收入为0，请复核收入确认/进度数据")
    if tax_paid == 0:
        data_gaps.append("当前项目没有项目级实缴税款记录；实际税务现金为0不代表无纳税义务")

    # Build frontend friendly lists
    revenueDetails = []
    # get contracts to match
    contracts = db.execute(select(Contract).where(Contract.project_id == project_id)).scalars().all()
    contract_map = {}
    for c in contracts:
        if c.buyer_code in external_codes:
            contract_map[c.buyer_code] = contract_map.get(c.buyer_code, D("0")) + _dec(c.amount)

    if not rev_details and contract_map:
        for bcode, camt in contract_map.items():
            revenueDetails.append({
                "type": external_map[bcode]["kind"],
                "name": f"{external_map[bcode]['name']} ({bcode})",
                "contract": float(camt),
                "recognized": float(0)
            })
    else:
        for bcode, amt in rev_details.items():
            camt = contract_map.get(bcode, D("0"))
            if camt <= 0:
                data_gaps.append(f"外部对手方 {bcode} 缺少合同金额；不按已开票金额比例反推合同额")
            revenueDetails.append({
                "type": external_map[bcode]["kind"],
                "name": f"{external_map[bcode]['name']} ({bcode})",
                "contract": float(camt) if camt > 0 else None,
                "recognized": float(amt)
            })

    internalDetails = []
    for (ecode, ccode, cat), amt in int_details.items():
        ename = internal_map.get(ecode, ecode)
        cname = internal_map.get(ccode, ccode)
        internalDetails.append({
            "node": f"{ename[:2]} → {cname[:2]}",
            "unit": f"{ecode} {ename}",
            "category": cat or '内部流转',
            "amount": float(amt)
        })

    externalDetails = []
    if ext_details:
        data_gaps.append("外部真实成本明细没有独立名义金额来源；nominal 保持为空，不按税率反推")
    for (ecode, ccode, cat), amt in ext_details.items():
        if ccode:
            # 有明确外部对手方
            cname = external_map.get(ccode, {}).get("name", ccode)
            supplier_name = f"{cname} ({ccode})"
        elif ecode and ecode in entity_name_map:
            # 归属到系统内单位自身发生的实际外部支出（如工资、设备折旧）
            supplier_name = f"{entity_name_map[ecode]} ({ecode}) · 系统内单位自营支出"
        else:
            supplier_name = "散户 / 未登记零星供应商 (分散支付)"

        externalDetails.append({
            "category": cat or '外部支出',
            "entity": ecode,
            "supplier": supplier_name,
            "nominal": None,
            "real": float(amt)
        })

    return {
        "project_id": project.id,
        "project_code": project.code,
        "recognized_revenue": float(recognized_revenue),
        "external_invoice_revenue": float(external_invoice_revenue),
        "internal_trade_volume_eliminated": float(internal_trade),
        "system_external_real_cost": float(external_cost),
        "project_tax_paid": float(tax_paid),
        "management_profit_after_tax": float(management_profit_after_tax),
        "internal_unit_count": len(internal_codes),
        "data_gaps": data_gaps,
        "revenueDetails": revenueDetails,
        "internalDetails": internalDetails,
        "externalDetails": externalDetails,
        "definitions": {
            "recognized_revenue": "项目进度表确认收入，不叠加系统内开票收入",
            "internal_trade_volume_eliminated": "26家系统内单位之间开票净额，仅展示交易规模，系统合并利润中抵销",
            "system_external_real_cost": "real_costs 中 external_cash=true 的最终系统边界外支出",
            "project_tax_paid": "项目级 TaxPaymentRecord 实际已缴税款",
            "management_profit_after_tax": "确认收入-系统外真实成本-项目实际已缴税；管理口径，不替代法定会计利润",
        },
    }


def build_project_planning_context(db: Session, project_id: int, category: str, package_amount: Decimal) -> dict[str, Any]:
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError("project not found")
    role = CATEGORY_ROLE.get(category)
    entity_stmt = select(Entity).where(Entity.active.is_(True), Entity.internal.is_(True))
    if role:
        entity_stmt = entity_stmt.where(Entity.business_role == role)
    entities = db.execute(entity_stmt.order_by(Entity.code)).scalars().all()
    external_rows = db.execute(select(ExternalParty).where(ExternalParty.active.is_(True)).order_by(ExternalParty.code)).scalars().all()
    matched_external = [x for x in external_rows if _category_matches_external(x, category)]
    if not matched_external:
        matched_external = external_rows
    profiles = [_internal_profile(db, e.code, e.business_role, package_amount) for e in entities]
    profiles += [_external_profile(db, x, package_amount) for x in matched_external]
    party_names = {e.code: e.name for e in entities}
    party_names.update({x.code: x.name for x in matched_external})
    penetration = system_penetration_snapshot(db, project_id)
    return {
        "project": project,
        "profiles": profiles,
        "party_names": party_names,
        "current_external_cost": D(str(penetration["system_external_real_cost"])),
        "current_tax_paid": D(str(penetration["project_tax_paid"])),
        "planning_revenue": _dec(project.contract_total),
        "penetration": penetration,
    }


def _scenario_dict(s: Scenario, baseline: dict[str, Any]) -> dict[str, Any]:
    projected_external_cost = baseline["current_external_cost"] + s.system_external_cost
    projected_tax_cash = baseline["current_tax_paid"] + s.incremental_tax_cash
    projected_profit = baseline["planning_revenue"] - projected_external_cost - projected_tax_cash
    return {
        "scenario_id": s.scenario_id,
        "score": float(s.score),
        "internal_ratio": float(s.internal_ratio),
        "internal_amount": float(s.internal_amount),
        "external_amount": float(s.external_amount),
        "system_external_cost": float(s.system_external_cost),
        "incremental_tax_cash": float(s.incremental_tax_cash),
        "weighted_risk": float(s.weighted_risk),
        "evidence_quality": float(s.evidence_quality),
        "concentration": float(s.concentration),
        "savings_vs_all_external": float(s.savings_vs_all_external),
        "projected_system_external_cost": float(projected_external_cost),
        "projected_system_tax_cash": float(projected_tax_cash),
        "projected_management_profit": float(projected_profit),
        "data_gaps": s.data_gaps,
        "allocations": [{
            "scope": x.scope, "party_code": x.party_code, "party_name": baseline.get("party_names", {}).get(x.party_code, x.party_code), "amount": float(x.amount),
            "share": float(x.share), "estimated_external_cost": float(x.estimated_external_cost),
            "estimated_tax_cash": float(x.estimated_tax_cash), "risk_score": float(x.risk_score),
            "evidence_quality": float(x.evidence_quality), "rationale": x.rationale,
        } for x in s.allocations],
    }


def _ai_recommend(db: Session, endpoint_id: int | None, project: Project, request_payload: dict[str, Any], scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    endpoint = db.get(AIModelEndpoint, endpoint_id) if endpoint_id else None
    if endpoint_id is not None and endpoint is None:
        return {
            "recommended_scenario_id": scenarios[0]["scenario_id"],
            "summary": "指定 AI 模型不存在，采用确定性综合评分最高方案。",
            "source": "deterministic",
            "status": "UNAVAILABLE",
            "requires_manual_review": True,
            "data_gaps": ["指定 AI 端点不存在，未生成 AI 筹划建议"],
        }
    compact = [{k:v for k,v in s.items() if k not in {"data_gaps"}} for s in scenarios]
    messages = [
        {"role":"system","content": (
            "你是建筑企业项目财税筹划顾问。确定性引擎已经生成并校验候选分配方案。"
            "你只能从给定 scenario_id 中推荐一个，不得发明新的金额、比例、税率或主体。"
            "系统内交易最终还会做合并抵销和成本穿透；系统外单位只参与交易链与税务影响，不进入内部利润合并。"
            "法人法定VAT仅以entity_vat_ledgers为权威，CIT在当前Planning事实基准中不可用；不得自行补算。"
            "请综合真实外部成本、税务现金影响、履约风险、证据质量、集中度提出建议。"
            "返回JSON，必须包含 risk_level, score, summary, findings, recommendations, data_gaps, recommended_scenario_id。"
        )},
        {"role":"user","content": json.dumps({"project":{"code":project.code,"name":project.name},"planning_request":request_payload,"validated_scenarios":compact},ensure_ascii=False)}
    ]
    try:
        parsed, raw, parse_failed, route_meta = call_with_failover(
            db,
            messages,
            {"scope": "allocation_planning", "project": {
                "code": project.code, "name": project.name,
            }},
            endpoint_id=endpoint_id,
        )
        picked = str(parsed.get("recommended_scenario_id") or "")
        valid={s["scenario_id"] for s in scenarios}
        if picked not in valid:
            picked=scenarios[0]["scenario_id"]
            gaps=list(parsed.get("data_gaps") or [])
            gaps.append("AI未返回有效候选方案ID，已回退到确定性最高分方案")
            parsed["data_gaps"]=gaps
        data_gaps = list(parsed.get("data_gaps", []) or [])
        fallback_used = bool(route_meta.get("fallback_used"))
        if fallback_used:
            data_gaps.append(
                "首选 AI 端点失败，已切换同路由组后备端点；本结果状态为 DEGRADED",
            )
        return {
            "recommended_scenario_id": picked,
            "summary": parsed.get("summary") or "",
            "risk_level": parsed.get("risk_level", "UNKNOWN"),
            "score": parsed.get("score", 0),
            "findings": parsed.get("findings", []),
            "recommendations": parsed.get("recommendations", []),
            "data_gaps": data_gaps,
            "source": (
                "ai_with_fallback" if fallback_used
                else ("ai" if not parse_failed else "ai_with_parse_warning")
            ),
            "status": "DEGRADED" if parse_failed or fallback_used else "READY",
            "requires_manual_review": bool(parse_failed or fallback_used),
            "provider": route_meta.get("endpoint_name", ""),
            "endpoint_id": route_meta.get("endpoint_id"),
            "model": route_meta.get("model", ""),
            "fallback_used": fallback_used,
            "raw_response": raw[:4000],
        }
    except Exception as exc:
        ai_status = str(getattr(exc, "status", "DEGRADED") or "DEGRADED")
        return {
            "recommended_scenario_id": scenarios[0]["scenario_id"],
            "summary": "AI建议不可用，采用确定性综合评分最高方案。",
            "source": "deterministic_fallback",
            "status": ai_status if ai_status in {"DEGRADED", "UNAVAILABLE"} else "DEGRADED",
            "requires_manual_review": True,
            "data_gaps": [f"AI调用失败: {exc}"],
        }


def _request_fingerprint(request_payload: dict[str, Any]) -> str:
    """Hash the user request, excluding transport/idempotency metadata."""
    comparable = {
        key: value
        for key, value in request_payload.items()
        if not key.startswith("_")
    }
    canonical = json.dumps(
        comparable,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _find_existing_persisted(
    db: Session,
    *,
    project_id: int,
    actor: str,
    request_fingerprint: str,
    idempotency_key: str | None,
    now: datetime,
) -> PlanningScenario | None:
    """Find a replay without relying on a schema change or a fake actor.

    Explicit ``Idempotency-Key`` values are durable. Requests without that
    header use a short fingerprint window to absorb accidental double-clicks
    while still allowing a later, intentional re-run after the source data has
    changed.
    """
    rows = db.execute(
        select(PlanningScenario)
        .where(
            PlanningScenario.project_id == project_id,
            PlanningScenario.created_by == actor,
        )
        .order_by(PlanningScenario.id.desc())
    ).scalars()
    cutoff = now - _AUTO_IDEMPOTENCY_WINDOW
    for row in rows:
        try:
            stored = json.loads(row.request_json or "{}")
        except (TypeError, ValueError):
            continue
        stored_key = str(stored.get("_idempotency_key") or "").strip()
        if idempotency_key:
            if stored_key == idempotency_key:
                if stored.get("_request_fingerprint") != request_fingerprint:
                    raise ValueError("Idempotency-Key was already used with a different request")
                return row
            continue
        if stored.get("_request_fingerprint") != request_fingerprint:
            continue
        if stored_key:
            continue
        try:
            created_at = datetime.fromisoformat(str(row.created_at))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if created_at >= cutoff:
            return row
    return None


def _persist(
    db: Session,
    project: Project,
    req: PlanningRequest,
    request_payload: dict[str, Any],
    scenario: dict[str, Any],
    ai: dict[str, Any],
    actor: str,
    *,
    idempotency_key: str | None = None,
) -> int:
    """Persist one planning scenario atomically and safely replay retries."""
    idempotency_key = idempotency_key.strip() if idempotency_key else None
    now = datetime.now(timezone.utc)
    fingerprint = _request_fingerprint(request_payload)
    stored_request = dict(request_payload)
    stored_request["_request_fingerprint"] = fingerprint
    if idempotency_key:
        stored_request["_idempotency_key"] = idempotency_key

    with _PERSIST_LOCK:
        try:
            existing = _find_existing_persisted(
                db,
                project_id=project.id,
                actor=actor,
                request_fingerprint=fingerprint,
                idempotency_key=idempotency_key,
                now=now,
            )
            if existing is not None:
                return existing.id

            row = PlanningScenario(
                project_id=project.id, package_name=str(request_payload.get("package_name") or req.category),
                category=req.category, package_amount=req.package_amount, objective=req.objective,
                status="recommended", score=D(str(scenario["score"])),
                internal_amount=D(str(scenario["internal_amount"])), external_amount=D(str(scenario["external_amount"])),
                projected_external_cost=D(str(scenario["projected_system_external_cost"])),
                projected_tax_cash=D(str(scenario["projected_system_tax_cash"])),
                projected_profit=D(str(scenario["projected_management_profit"])),
                risk_score=D(str(scenario["weighted_risk"])), evidence_quality=D(str(scenario["evidence_quality"])),
                request_json=json.dumps(stored_request, ensure_ascii=False, sort_keys=True),
                result_json=json.dumps(scenario, ensure_ascii=False),
                ai_summary=str(ai.get("summary") or ""), ai_json=json.dumps(ai, ensure_ascii=False),
                created_by=actor, created_at=now.isoformat(),
            )
            db.add(row)
            db.flush()
            for line in scenario["allocations"]:
                db.add(PlanningAllocation(
                    scenario_id=row.id, party_scope=line["scope"], party_code=line["party_code"],
                    amount=D(str(line["amount"])), share=D(str(line["share"])),
                    estimated_external_cost=D(str(line["estimated_external_cost"])),
                    estimated_tax_cash=D(str(line["estimated_tax_cash"])),
                    risk_score=D(str(line["risk_score"])), evidence_quality=D(str(line["evidence_quality"])),
                    rationale=line.get("rationale", ""),
                ))
            db.commit()
            return row.id
        except Exception:
            # Keep direct service callers safe as well as the HTTP route. A
            # failed allocation row must never leave the Session poisoned or
            # a parent scenario visible without its children.
            db.rollback()
            raise



def planning_candidate_context(db: Session, project_id: int, category: str, package_amount: Decimal) -> dict[str, Any]:
    ctx = build_project_planning_context(db, project_id, category, package_amount)
    return {
        "project": {"id": ctx["project"].id, "code": ctx["project"].code, "name": ctx["project"].name},
        "category": category,
        "package_amount": float(package_amount),
        "system_penetration": ctx["penetration"],
        "candidates": [{
            "code": p.code, "name": ctx["party_names"].get(p.code, p.code), "scope": p.scope,
            "role": p.role, "capacity": (float(p.capacity) if p.capacity is not None else None),
            "external_cost_ratio": float(p.external_cost_ratio), "tax_cash_rate": float(p.tax_cash_rate),
            "risk_score": float(p.risk_score), "evidence_quality": float(p.evidence_quality),
            "eligible": p.eligible, "rationale": p.rationale, "data_gaps": list(p.data_gaps),
        } for p in ctx["profiles"]],
    }


def recommend_project_allocation(
    db: Session,
    project_id: int,
    request_payload: dict[str, Any],
    actor: str = "system",
    *,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    amount=D(str(request_payload["package_amount"]))
    req=PlanningRequest(
        package_amount=amount, category=str(request_payload["category"]), objective=str(request_payload.get("objective") or "balanced"),
        internal_min_ratio=D(str(request_payload.get("internal_min_ratio", 0))),
        internal_max_ratio=D(str(request_payload.get("internal_max_ratio", 1))),
        preferred_internal_ratio=(D(str(request_payload["preferred_internal_ratio"])) if request_payload.get("preferred_internal_ratio") is not None else None),
    )
    ctx=build_project_planning_context(db,project_id,req.category,amount)
    profiles=list(ctx["profiles"])
    overrides=request_payload.get("party_overrides") or {}
    include_internal=set(request_payload.get("internal_candidates") or [])
    include_external=set(request_payload.get("external_candidates") or [])
    adjusted=[]
    for p in profiles:
        if p.scope=="internal" and include_internal and p.code not in include_internal:
            continue
        if p.scope=="external" and include_external and p.code not in include_external:
            continue
        ov=overrides.get(p.code) or {}
        adjusted.append(PartyProfile(
            code=p.code,scope=p.scope,role=p.role,
            capacity=(D(str(ov["capacity"])) if ov.get("capacity") is not None else p.capacity),
            external_cost_ratio=(D(str(ov["external_cost_ratio"])) if ov.get("external_cost_ratio") is not None else p.external_cost_ratio),
            tax_cash_rate=(D(str(ov["tax_cash_rate"])) if ov.get("tax_cash_rate") is not None else p.tax_cash_rate),
            risk_score=(D(str(ov["risk_score"])) if ov.get("risk_score") is not None else p.risk_score),
            evidence_quality=(D(str(ov["evidence_quality"])) if ov.get("evidence_quality") is not None else p.evidence_quality),
            eligible=bool(ov.get("eligible",p.eligible)), rationale=p.rationale, data_gaps=p.data_gaps,
        ))
    scenarios=build_scenarios(req,adjusted)
    scenario_dicts=[_scenario_dict(x,ctx) for x in scenarios]
    ai=_ai_recommend(db,request_payload.get("endpoint_id"),ctx["project"],request_payload,scenario_dicts)
    recommended=next((x for x in scenario_dicts if x["scenario_id"]==ai["recommended_scenario_id"]),scenario_dicts[0])
    persisted_id=None
    if request_payload.get("persist",False):
        persisted_id=_persist(
            db, ctx["project"], req, request_payload, recommended, ai, actor,
            idempotency_key=idempotency_key,
        )
    return {
        "scenario_scope": PLANNING_SCENARIO_SCOPE,
        "is_filing_basis": False,
        "fact_basis": dict(PLANNING_FACT_BASIS),
        "project":{"id":ctx["project"].id,"code":ctx["project"].code,"name":ctx["project"].name,"contract_total":float(ctx["project"].contract_total)},
        "planning_basis":{"package_amount":float(amount),"category":req.category,"objective":req.objective,"current_external_cost":float(ctx["current_external_cost"]),"current_tax_paid":float(ctx["current_tax_paid"]),"planning_revenue":float(ctx["planning_revenue"]),"note":"package_amount应为尚未计入real_costs的待规划净额；结果为规划估算，不替代法定申报税额；CIT_ESTIMATE_UNAVAILABLE。"},
        "candidate_count":{"internal":sum(1 for x in adjusted if x.scope=="internal"),"external":sum(1 for x in adjusted if x.scope=="external")},
        "system_penetration": ctx["penetration"],
        "scenarios":scenario_dicts,"ai_recommendation":ai,"recommended":recommended,"planning_scenario_id":persisted_id,
    }
