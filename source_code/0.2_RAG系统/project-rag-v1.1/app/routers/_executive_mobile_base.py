"""
=============================================================================
成都建工·天府掌舵 —— 老板端移动驾驶舱专用极速 API 网关
=============================================================================
专为高管移动端 (Android Boss App) 提供高性能聚合数据：
  1. /api/v1/executive/cockpit/summary   - 集团经营与财税大盘核心指标与趋势
  2. /api/v1/executive/projects          - 全部工程项目全景列表与健康度
  3. /api/v1/executive/projects/{id}/360 - 单项目 360° 成本/税务/利润/四流穿透
  4. /api/v1/executive/entities/matrix   - 集团 26 家系统内单位 ABCD 分类全景底账
  5. /api/v1/executive/ai/chat           - 董事长专属 AI 智策助手与决策诊断
=============================================================================
"""

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from facts_provider.facts_provider import FactsProvider, FactsResponse, MetricValue

from ..auth import TaxPrincipal, require_exec_role
from ..db import SessionLocal

router = APIRouter(prefix="/api/v1/executive", tags=["Executive Mobile Cockpit"])

ALL_EXEC_ROLES = ("admin", "operator")
# Keep one explicit dependency for the entire Executive surface.  The
# dependency verifies a Tax-issued Bearer JWT and applies the allow-list at
# the route boundary; it is intentionally not a service-key or Web-cookie
# dependency.
require_executive = require_exec_role(*ALL_EXEC_ROLES)


def _meta_block(
    principal: TaxPrincipal,
    facts_available: Optional[bool] = None,
    facts_reason: Optional[str] = None,
    as_of: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the response ``_meta`` block shared by every endpoint.

    ``data_source`` indicates the data freshness:
    - ``"live"``: data sourced from real analytics computation
    - ``"unavailable"``: Canonical Facts not yet connected, no demo numbers emitted

    ``timestamp`` is an ISO-8601 UTC string so front-end can render data freshness.
    ``facts_available`` mirrors the underlying FactsResponse.facts_available so the
    front-end can render data-source badges without digging into ``kpi`` payload.
    ``facts_reason`` and ``as_of`` are populated only when ``facts_available=False``.
    """

    data_source = "live" if facts_available else "unavailable"
    meta: Dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_source": data_source,
        "issuer_role": principal.role,
        "issuer_source": principal.source,
        "facts_available": bool(facts_available) if facts_available is not None else False,
    }
    if as_of:
        meta["as_of"] = as_of
    elif facts_available is False:
        meta["as_of"] = None
    if facts_available is False and facts_reason:
        meta["facts_reason"] = facts_reason
    return meta


# =============================================================================
# FactsProvider helpers
# =============================================================================
def _safe_get_facts(db, project_code: str) -> FactsResponse:
    """Wrap :meth:`FactsProvider.get_facts` so the endpoint never crashes on outage.

    Any exception from the provider or DB layer is converted into an explicit
    DEGRADED FactsResponse. The endpoint code therefore only has to inspect
    ``response.facts_available`` rather than catching exceptions inline.
    """

    try:
        provider = FactsProvider(db)
        return provider.get_facts(project_code)
    except Exception as exc:  # any failure is treated as a source outage
        return FactsResponse(
            project_code=project_code,
            as_of=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            facts_version="",
            metrics={},
            status="DEGRADED",
            facts_available=False,
            reason=f"facts_provider_error: {exc.__class__.__name__}",
            source="analytics_project_full",
        )


def _metric_value(metrics: Dict[str, MetricValue], key: str) -> Optional[float]:
    """Extract a metric value as a JSON-safe float, preserving ``None`` for missing."""

    mv = metrics.get(key)
    if mv is None:
        return None
    try:
        return float(mv.value)
    except (TypeError, ValueError):
        return None


def _aggregate_facts(
    project_codes: List[str],
    db: Session,
) -> Dict[str, Any]:
    """Aggregate deterministic project Facts without averaging ratios blindly.

    Group rates are recomputed from their additive numerators/denominators.
    The result is marked available only when every project has the required
    deterministic core; partial project sets never surface partial group sums.
    """
    if not project_codes:
        return {"facts_available": False, "facts_reason": "no projects in scope", "as_of": None, "kpi": {}, "available_count": 0, "total_count": 0}

    additive = (
        "contract_amount", "recognized_revenue", "real_project_cost",
        "real_profit", "collected_amount", "unpaid_amount",
        "eac_revenue", "eac_cost", "eac_profit",
        "cash_inflow", "cash_outflow", "net_cashflow", "cash_gap_30d",
        "cost_variance", "output_vat", "input_vat", "vat_payable",
        "total_tax_burden",
    )
    sums={key:0.0 for key in additive}
    seen={key:False for key in additive}
    health_scores: List[float]=[]
    available_count=0
    latest_as_of: Optional[str]=None

    for code in project_codes:
        response=_safe_get_facts(db,code)
        if not response.facts_available:
            continue
        available_count += 1
        if response.as_of and (latest_as_of is None or response.as_of > latest_as_of):
            latest_as_of=response.as_of
        for key in additive:
            value=_metric_value(response.metrics,key)
            if value is not None:
                sums[key] += value
                seen[key] = True
        health=_metric_value(response.metrics,"health_score")
        if health is not None:
            health_scores.append(health)

    if available_count != len(project_codes):
        reason=(f"facts available for {available_count}/{len(project_codes)} projects"
                if available_count else "no canonical facts available for any project")
        return {"facts_available":False,"facts_reason":reason,"as_of":None,"kpi":{},"available_count":available_count,"total_count":len(project_codes)}

    kpi={key:(sums[key] if seen[key] else None) for key in additive}
    revenue=kpi.get("recognized_revenue")
    collected=kpi.get("collected_amount")
    eac_revenue=kpi.get("eac_revenue")
    eac_profit=kpi.get("eac_profit")
    total_tax=kpi.get("total_tax_burden")
    kpi["collection_rate"]=(collected/revenue if collected is not None and revenue and revenue>0 else None)
    kpi["eac_margin"]=(eac_profit/eac_revenue if eac_profit is not None and eac_revenue and eac_revenue>0 else None)
    kpi["tax_burden_rate"]=(total_tax/revenue if total_tax is not None and revenue and revenue>0 else None)
    kpi["health_score_avg"]=(sum(health_scores)/len(health_scores) if health_scores else None)
    # Compatibility alias used by the current mobile response contract.
    kpi["contract_total"]=kpi.get("contract_amount")
    return {"facts_available":True,"facts_reason":None,"as_of":latest_as_of,"kpi":kpi,"available_count":available_count,"total_count":len(project_codes)}


# =============================================================================
# 1. 集团经营与财税大盘 (Executive Cockpit Summary)
# =============================================================================
@router.get("/cockpit/summary")
def get_cockpit_summary(
    principal: TaxPrincipal = Depends(require_executive),
):
    """获取集团经营大盘总览指标、资金缺口与月度趋势"""
    db = SessionLocal()
    try:
        # 1. 统计项目数与基础聚合
        result = db.execute(text("SELECT count(*) as total_projects FROM projects"))
        p_row = result.fetchone()
        total_projects = p_row[0] if p_row else 0

        # 2. 统计文档总数
        result = db.execute(text("SELECT count(*) as total_docs FROM documents"))
        d_row = result.fetchone()
        total_docs = d_row[0] if d_row else 0

        # 3. 统计法人实体分类
        result = db.execute(
            text("SELECT business_role, count(*) as count FROM entities GROUP BY business_role")
        )
        role_counts = {r[0]: r[1] for r in result.fetchall()}

        # 4. 汇总 Canonical Facts across every project
        proj_codes_result = db.execute(text("SELECT project_code FROM projects"))
        project_codes = [row[0] for row in proj_codes_result.fetchall() if row[0]]
        aggregate = _aggregate_facts(project_codes, db)
        facts_available: bool = aggregate["facts_available"]
        facts_reason: Optional[str] = aggregate["facts_reason"]
        facts_as_of: Optional[str] = aggregate["as_of"]
        facts_kpi: Dict[str, Optional[float]] = aggregate["kpi"]

        # Build KPI block: when facts available, surface real numbers; otherwise
        # emit None for every metric, matching the no-demo-numbers contract.
        if facts_available:
            recognized = facts_kpi.get("recognized_revenue")
            profit = facts_kpi.get("real_profit")
            cost = facts_kpi.get("real_project_cost")
            tax_burden = facts_kpi.get("tax_burden_rate")
            kpi = {
                "contract_total": facts_kpi.get("contract_total"),
                "revenue_recognized": recognized,
                "actual_cost": cost,
                "real_profit": profit,
                "gross_margin": (
                    profit / recognized if (profit is not None and recognized) else None
                ),
                "output_vat": facts_kpi.get("output_vat"),
                "input_vat": facts_kpi.get("input_vat"),
                "net_tax_liability": facts_kpi.get("vat_payable"),
                "prepaid_tax": facts_kpi.get("total_tax_burden"),
                "tax_burden_rate": tax_burden,
                "cash_inflow": facts_kpi.get("cash_inflow"),
                "cash_outflow": facts_kpi.get("cash_outflow"),
                "net_cashflow": facts_kpi.get("net_cashflow"),
                "collection_rate": facts_kpi.get("collection_rate"),
                "total_projects": total_projects,
                "total_docs": total_docs,
                "total_entities": sum(role_counts.values()),
                "eac_revenue": facts_kpi.get("eac_revenue"),
                "eac_cost": facts_kpi.get("eac_cost"),
                "eac_profit": facts_kpi.get("eac_profit"),
                "eac_margin": facts_kpi.get("eac_margin"),
                "health_score_avg": facts_kpi.get("health_score_avg"),
            }
        else:
            kpi = {
                "contract_total": None,
                "revenue_recognized": None,
                "actual_cost": None,
                "real_profit": None,
                "gross_margin": None,
                "output_vat": None,
                "input_vat": None,
                "net_tax_liability": None,
                "prepaid_tax": None,
                "tax_burden_rate": None,
                "cash_inflow": None,
                "cash_outflow": None,
                "net_cashflow": None,
                "collection_rate": None,
                "total_projects": total_projects,
                "total_docs": total_docs,
                "total_entities": sum(role_counts.values()),
                "eac_revenue": None,
                "eac_cost": None,
                "eac_profit": None,
                "eac_margin": None,
                "health_score_avg": None,
            }

        return {
            "status": "success",
            "_meta": _meta_block(
                principal,
                facts_available=facts_available,
                facts_reason=facts_reason,
                as_of=facts_as_of,
            ),
            "kpi": kpi,
            "entities_breakdown": {
                "A_general_contractors": role_counts.get("A", 0),
                "B_material_traders": role_counts.get("B", 0),
                "C_labor_subcontractors": role_counts.get("C", 0),
                "D_equipment_leasing": role_counts.get("D", 0),
            },
            "trends": {
                "months": [],
                "revenue": [],
                "cost": [],
                "profit": [],
                "tax": [],
            },
            "urgent_risks": [],
            "facts_summary": {
                "available_count": aggregate["available_count"],
                "total_count": aggregate["total_count"],
            },
        }
    finally:
        db.close()


# =============================================================================
# 2. 项目全景列表 (Executive Projects Overview)
# =============================================================================
@router.get("/projects")
def get_executive_projects(
    principal: TaxPrincipal = Depends(require_executive),
):
    """获取所有工程项目的高管穿透指标卡片"""
    db = SessionLocal()
    try:
        result = db.execute(text("""
            SELECT p.id, p.project_code, p.name, p.status,
                   count(d.id) as doc_count
            FROM projects p
            LEFT JOIN documents d ON d.project_id = p.id
            GROUP BY p.id, p.project_code, p.name, p.status
            ORDER BY p.id;
        """))
        rows = result.fetchall()

        # First pass: identify whether any project yields available facts so we
        # can flip data_source to "live" without N+1 queries per project.
        project_codes = [r[1] for r in rows if r[1]]
        any_available = False
        latest_as_of: Optional[str] = None
        first_reason: Optional[str] = None
        per_project_facts: Dict[str, FactsResponse] = {}
        for code in project_codes:
            response = _safe_get_facts(db, code)
            per_project_facts[code] = response
            if response.facts_available:
                any_available = True
                if response.as_of and (latest_as_of is None or response.as_of > latest_as_of):
                    latest_as_of = response.as_of
            elif first_reason is None:
                first_reason = response.reason

        projects = []
        for r in rows:
            pid = r[0]
            pcode = r[1]
            pname = r[2]
            pstatus = r[3]
            doc_count = r[4]

            facts = per_project_facts.get(pcode)
            if facts and facts.facts_available:
                m = facts.metrics
                projects.append({
                    "id": pid,
                    "project_code": pcode,
                    "name": pname,
                    "status": pstatus,
                    "doc_count": doc_count,
                    "contract_amount": _metric_value(m, "contract_amount"),
                    "revenue": _metric_value(m, "recognized_revenue"),
                    "cost": _metric_value(m, "real_project_cost"),
                    "real_profit": _metric_value(m, "real_profit"),
                    "gross_margin": (
                        _metric_value(m, "real_profit")
                        / _metric_value(m, "recognized_revenue")
                        if _metric_value(m, "real_profit") is not None
                        and _metric_value(m, "recognized_revenue")
                        else None
                    ),
                    "output_vat": _metric_value(m, "output_vat"),
                    "input_vat": _metric_value(m, "input_vat"),
                    "net_vat": _metric_value(m, "vat_payable"),
                    "progress_pct": None,
                    "collection_rate": _metric_value(m, "collection_rate"),
                    "tax_method": None,
                    "risk_status": (
                        None if _metric_value(m, "health_score") is None
                        else ("需关注" if _metric_value(m, "health_score") < 60 else "健康")
                    ),
                    "entities_involved": [],
                    "manager": None,
                    "location": None,
                    "facts_available": True,
                    "facts_as_of": facts.as_of,
                    "health_score": _metric_value(m, "health_score"),
                })
            else:
                projects.append({
                    "id": pid,
                    "project_code": pcode,
                    "name": pname,
                    "status": pstatus,
                    "doc_count": doc_count,
                    "contract_amount": None,
                    "revenue": None,
                    "cost": None,
                    "real_profit": None,
                    "gross_margin": None,
                    "output_vat": None,
                    "input_vat": None,
                    "net_vat": None,
                    "progress_pct": None,
                    "collection_rate": None,
                    "tax_method": None,
                    "risk_status": None,
                    "entities_involved": [],
                    "manager": None,
                    "location": None,
                    "facts_available": False,
                    "facts_reason": facts.reason if facts else "facts_provider_error",
                    "facts_as_of": None,
                    "health_score": None,
                })

        return {
            "status": "success",
            "count": len(projects),
            "projects": projects,
            "_meta": _meta_block(
                principal,
                facts_available=any_available,
                facts_reason=None if any_available else first_reason,
                as_of=latest_as_of,
            ),
        }
    finally:
        db.close()


# =============================================================================
# 3. 单项目 360° 穿透分析 (Project 360 Deep-Dive)
# =============================================================================
@router.get("/projects/{project_id}/360")
def get_project_360_detail(
    project_id: int,
    principal: TaxPrincipal = Depends(require_executive),
):
    """获取单个项目的 360 度成本、税务、进展与四流证据链"""
    db = SessionLocal()
    try:
        result = db.execute(
            text("SELECT id, project_code, name, status FROM projects WHERE id = :id"),
            {"id": project_id}
        )
        proj = result.fetchone()
        if not proj:
            raise HTTPException(status_code=404, detail="未找到指定项目")

        pcode = proj[1]
        pname = proj[2]
        proj_status = proj[3]

        # 查询项目关联的所有凭据文档
        result = db.execute(
            text("""
                SELECT id, document_code, filename, document_type, entity_code, counterparty_code, original_path
                FROM documents
                WHERE project_id = :project_id
                ORDER BY document_type, id;
            """),
            {"project_id": project_id}
        )
        docs = result.fetchall()

        # Build document list from real DB data
        document_list = [
            {
                "id": d[0],
                "document_code": d[1],
                "filename": d[2],
                "document_type": d[3],
                "entity_code": d[4],
                "counterparty_code": d[5],
                "download_url": f"/api/v1/documents/{d[0]}/original"
            }
            for d in docs
        ]

        # Canonical Facts lookup for this project
        facts = _safe_get_facts(db, pcode)
        facts_available: bool = bool(facts.facts_available)
        metrics = facts.metrics if facts_available else {}
        data_gaps: List[str] = []
        if not facts_available:
            # Canonical Facts is the gate for every financial value in this
            # endpoint.  A project row, invoice row, or payment row is not a
            # substitute for the analytics_* / Facts contract.
            data_gaps.append("canonical_facts_unavailable")
            facts_reason = (facts.reason or "").lower()
            if "entity mapping" in facts_reason or "entity_mapping" in facts_reason:
                data_gaps.append("entity_mapping_gap")

        if facts_available:
            recognized = _metric_value(metrics, "recognized_revenue")
            profit = _metric_value(metrics, "real_profit")
            cost = _metric_value(metrics, "real_project_cost")
            eac_cost = _metric_value(metrics, "eac_cost")
            eac_margin = _metric_value(metrics, "eac_margin")
            financial_penetration = {
                "contract_total": _metric_value(metrics, "contract_amount"),
                "recognized_revenue": recognized,
                "actual_cost": cost,
                "real_profit": profit,
                "gross_margin_pct": (
                    profit / recognized if (profit is not None and recognized) else None
                ),
                "eac_forecast_cost": eac_cost,
                "eac_forecast_margin": eac_margin,
            }
            tax_burden = _metric_value(metrics, "tax_burden_rate")
            tax_details = {
                "output_vat": _metric_value(metrics, "output_vat"),
                "input_vat": _metric_value(metrics, "input_vat"),
                "net_vat": _metric_value(metrics, "vat_payable"),
                "prepaid_tax": _metric_value(metrics, "total_tax_burden"),
                "tax_burden_pct": tax_burden,
                "invoice_deduction_rate": None,
            }
        else:
            financial_penetration = {
                "contract_total": None,
                "recognized_revenue": None,
                "actual_cost": None,
                "real_profit": None,
                "gross_margin_pct": None,
                "eac_forecast_cost": None,
                "eac_forecast_margin": None,
            }
            tax_details = {
                "output_vat": None,
                "input_vat": None,
                "net_vat": None,
                "prepaid_tax": None,
                "tax_burden_pct": None,
                "invoice_deduction_rate": None,
            }

        cost_breakdown = {
            "materials": {"amount": None, "pct": None, "desc": None},
            "labor": {"amount": None, "pct": None, "desc": None},
            "equipment": {"amount": None, "pct": None, "desc": None},
            "other": {"amount": None, "pct": None, "desc": None},
        }

        # =========================================================================
        # 26 家系统内单位穿透与合并抵销数据计算 (System Penetration Calculation)
        # =========================================================================
        from collections import defaultdict
        from decimal import Decimal

        def _d(v): return Decimal(str(v)) if v is not None else Decimal("0")

        # 1. 内部与外部主体名单
        ent_rows = (
            db.execute(
                text("SELECT entity_code, name, business_role FROM entities")
            ).fetchall()
            if facts_available
            else []
        )
        internal_map = {e[0]: e[1] for e in ent_rows}
        internal_codes = set(internal_map.keys())

        ext_rows = (
            db.execute(text("SELECT code, name, kind FROM external_parties")).fetchall()
            if facts_available
            else []
        )
        external_map = {x[0]: {"name": x[1], "kind": x[2] or "外部单位"} for x in ext_rows}
        external_codes = set(external_map.keys())

        # 2. Recognized revenue is Canonical Facts, never a project-table
        # fallback.  It remains None if the available Facts row omits it.
        canonical_recognized = _metric_value(metrics, "recognized_revenue")
        rec_revenue = (
            _d(canonical_recognized) if canonical_recognized is not None else None
        )

        # 3. 发票流向分析 (区分系统内流转 vs 对外开票)
        inv_rows = (
            db.execute(
                text(
                    "SELECT direction, entity_code, counterparty_code, net, vat, category "
                    "FROM invoices WHERE project_id = :id"
                ),
                {"id": project_id},
            ).fetchall()
            if facts_available
            else []
        )

        internal_trade_volume: Optional[Decimal] = None
        external_invoice_revenue: Optional[Decimal] = None
        int_details = defaultdict(lambda: Decimal("0"))
        rev_details = defaultdict(lambda: Decimal("0"))

        for inv in inv_rows:
            direction, ecode, ccode, net, _vat, cat = inv[0], inv[1], inv[2], inv[3], inv[4], inv[5]
            if direction != "out" or ecode not in internal_codes:
                continue
            if ccode in internal_codes:
                internal_trade_volume = (internal_trade_volume or Decimal("0")) + _d(net)
                int_details[(ecode, ccode, cat)] += _d(net)
            elif ccode in external_codes:
                external_invoice_revenue = (
                    external_invoice_revenue or Decimal("0")
                ) + _d(net)
                rev_details[ccode] += _d(net)

        # 4. 穿透后系统外真实成本 (real_costs external_cash = true)
        cost_rows = (
            db.execute(
                text(
                    "SELECT entity_code, counterparty_code, category, amount "
                    "FROM real_costs WHERE project_id = :id AND external_cash = true"
                ),
                {"id": project_id},
            ).fetchall()
            if facts_available
            else []
        )

        system_external_real_cost: Optional[Decimal] = None
        ext_details = defaultdict(lambda: Decimal("0"))
        for rc in cost_rows:
            amt = _d(rc[3])
            system_external_real_cost = (
                system_external_real_cost or Decimal("0")
            ) + amt
            ext_details[(rc[0], rc[1], rc[2])] += amt

        # 5. 实缴税款
        tax_paid_raw = (
            db.scalar(
                text(
                    "SELECT sum(tax_amount) FROM tax_payment_records "
                    "WHERE project_id = :id"
                ),
                {"id": project_id},
            )
            if facts_available
            else None
        )
        tax_paid_val = _d(tax_paid_raw) if tax_paid_raw is not None else None

        # This management KPI has one explicit formula across backend and UI.
        # It is not the same metric as analytics real_profit.  Fail closed when
        # any operand is unavailable rather than substituting or inventing 0.
        if (
            facts_available
            and rec_revenue is not None
            and system_external_real_cost is not None
            and tax_paid_val is not None
        ):
            management_profit_after_tax = float(
                rec_revenue - system_external_real_cost - tax_paid_val
            )
        else:
            management_profit_after_tax = None
            if facts_available:
                data_gaps.append(
                    "management_profit_after_tax_unavailable: "
                    "requires recognized_revenue, system_external_real_cost and project_tax_paid"
                )

        # 构建前端易读明细列表
        contract_rows = (
            db.execute(
                text("SELECT buyer_code, amount FROM contracts WHERE project_id = :id"),
                {"id": project_id},
            ).fetchall()
            if facts_available
            else []
        )
        contract_map = {}
        for c in contract_rows:
            if c[0] in external_codes:
                contract_map[c[0]] = contract_map.get(c[0], Decimal("0")) + _d(c[1])

        revenue_details = []
        if facts_available and not rev_details and contract_map:
            for bcode, camt in contract_map.items():
                revenue_details.append({
                    "type": external_map.get(bcode, {}).get("kind", "发包方"),
                    "name": f"{external_map.get(bcode, {}).get('name', bcode)} ({bcode})",
                    "contract": float(camt),
                    "recognized": float(0)
                })
        elif facts_available:
            for bcode, amt in rev_details.items():
                camt = contract_map.get(bcode, Decimal("0"))
                if camt <= 0:
                    data_gaps.append(
                        f"external_contract_amount_missing:{bcode}; no ratio-based estimate emitted"
                    )
                revenue_details.append({
                    "type": external_map.get(bcode, {}).get("kind", "发包方"),
                    "name": f"{external_map.get(bcode, {}).get('name', bcode)} ({bcode})",
                    "contract": float(camt) if camt > 0 else None,
                    "recognized": float(amt)
                })

        internal_details = []
        for (ecode, ccode, cat), amt in int_details.items():
            ename = internal_map.get(ecode, ecode)
            cname = internal_map.get(ccode, ccode)
            internal_details.append({
                "node": f"{ename[:2]} → {cname[:2]}",
                "unit": f"{ecode} {ename}",
                "category": cat or "内部流转",
                "amount": float(amt)
            })

        external_details = []
        if ext_details:
            data_gaps.append(
                "external_nominal_amount_unavailable: real_costs has no independent nominal amount; no tax-rate reverse estimate emitted"
            )
        for (ecode, ccode, cat), amt in ext_details.items():
            if ccode and ccode in external_map:
                cname = external_map[ccode]["name"]
                supplier_name = f"{cname} ({ccode})"
            elif ecode and ecode in internal_map:
                supplier_name = f"{internal_map[ecode]} ({ecode}) · 自营真实支出"
            else:
                supplier_name = "散户 / 零星分供商"

            external_details.append({
                "category": cat or "外部支出",
                "entity": ecode,
                "supplier": supplier_name,
                "nominal": None,
                "real": float(amt)
            })

        system_penetration = {
            "project_id": project_id,
            "project_code": pcode,
            "recognized_revenue": float(rec_revenue) if rec_revenue is not None else None,
            "external_invoice_revenue": (
                float(external_invoice_revenue)
                if external_invoice_revenue is not None
                else None
            ),
            "internal_trade_volume_eliminated": (
                float(internal_trade_volume)
                if internal_trade_volume is not None
                else None
            ),
            "system_external_real_cost": (
                float(system_external_real_cost)
                if system_external_real_cost is not None
                else None
            ),
            "project_tax_paid": float(tax_paid_val) if tax_paid_val is not None else None,
            "management_profit_after_tax": management_profit_after_tax,
            "internal_unit_count": len(internal_codes) if facts_available else None,
            "revenueDetails": revenue_details,
            "internalDetails": internal_details,
            "externalDetails": external_details,
            "definitions": {
                "management_profit_after_tax": "确认收入-系统外真实成本-项目实缴税款；三个操作数任一缺失则不可用",
                "contract": "仅展示实际合同金额，缺失时为 null，不按发票或收入比例反推",
                "nominal": "当前 real_costs 无独立名义金额来源，缺失时为 null，不按税率反推",
            },
        }

        return {
            "status": "success" if facts_available else "DEGRADED",
            "_meta": _meta_block(
                principal,
                facts_available=facts_available,
                facts_reason=facts.reason if not facts_available else None,
                as_of=facts.as_of if facts_available else None,
            ),
            "project": {
                "id": proj[0],
                "project_code": pcode,
                "name": pname,
                "status": proj_status,
            },
            "financial_penetration": financial_penetration,
            "system_penetration": system_penetration,
            "cost_breakdown": cost_breakdown,
            "tax_details": tax_details,
            "evidence_chain": {
                "contract_stream": {"title": "合同流 (Contract Stream)", "status": "unavailable", "details": None, "docs": []},
                "invoice_stream": {"title": "发票流 (Invoice Stream)", "status": "unavailable", "details": None, "docs": []},
                "cash_stream": {"title": "资金流 (Cashflow Stream)", "status": "unavailable", "details": None, "docs": []},
                "goods_stream": {"title": "物资与实物业务流 (Business/Goods Stream)", "status": "unavailable", "details": None, "docs": []},
            },
            "document_list": document_list,
            "facts_summary": {
                "available": facts_available,
                "reason": None if facts_available else facts.reason,
                "as_of": facts.as_of if facts_available else None,
                "facts_version": facts.facts_version if facts_available else None,
                "metrics_count": len(metrics),
            },
            "data_gaps": data_gaps,
        }
    finally:
        db.close()


# =============================================================================
# 4. 集团 26 家系统内单位经营全景 (26 Entities ABCD Matrix)
# =============================================================================
@router.get("/entities/matrix")
def get_entities_matrix(
    principal: TaxPrincipal = Depends(require_executive),
):
    """获取集团法人公司的 ABCD 业务分类经营与税务全景底账"""
    db = SessionLocal()
    try:
        result = db.execute(text("""
            SELECT id, entity_code, name, business_role, legal_entity, legal_representative,
                   registered_capital, unified_social_credit_code, industry, business_scope, note
            FROM entities
            ORDER BY business_role, entity_code;
        """))
        rows = result.fetchall()

        matrix = {
            "A": {"title": "A 类：施工总承包与专业分包", "desc": "集团主力施工平台，负责施工资质输出、产值申报与工程总包结算", "entities": []},
            "B": {"title": "B 类：物资商贸与建材集采", "desc": "建材与大宗物资采购平台，负责钢筋水泥进项发票池管理与供应链抵扣", "entities": []},
            "C": {"title": "C 类：建筑劳务分包公司", "desc": "专业劳务分包公司，负责班组用工管理、个税合规与劳务资金闭环", "entities": []},
            "D": {"title": "D 类：机械设备租赁公司", "desc": "重型施工机械与设备租赁平台，负责塔吊挖掘机进项税额抵扣与资产运营", "entities": []}
        }

        for r in rows:
            role = r[3] or "A"
            if role not in matrix:
                continue

            matrix[role]["entities"].append({
                "id": r[0],
                "entity_code": r[1],
                "name": r[2],
                "business_role": role,
                "legal_entity": r[4],
                "legal_representative": r[5],
                "registered_capital": r[6],
                "uscc": r[7],
                "industry": r[8],
                "note": r[10] or "",
                "risk_status": "健康" if not r[10] or "往来" in r[10] else "需关注"
            })

        # The entities endpoint exposes real entity master data, but financial
        # Facts are scoped per-project. Without an entity-level Facts surface,
        # we conservatively mark ``facts_available=False`` for the matrix view
        # rather than fabricating numbers.
        return {
            "status": "success",
            "total_entities": len(rows),
            "matrix": matrix,
            "_meta": _meta_block(
                principal,
                facts_available=False,
                facts_reason="entity-level canonical facts not exposed in this view",
                as_of=None,
            ),
        }
    finally:
        db.close()


# =============================================================================
# 5. 董事长 AI 智策助手 (Executive AI Copilot)
# =============================================================================
class AIChatRequest(BaseModel):
    message: str
    project_id: Optional[int] = None


def _facts_summary_block(facts: FactsResponse) -> str:
    """Render a deterministic facts summary for the AI reply.

    The summary uses only metrics already proven available by FactsProvider;
    it never invents numbers. When Facts are unavailable, the reply
    explicitly tells the LLM (and the user) that no Canonical Facts are
    connected, so the model cannot fabricate figures.
    """

    if not facts.facts_available:
        return (
            "**Canonical Facts 状态：未接入 / 数据不完整**\n"
            f"- 原因：{facts.reason or 'unknown'}\n"
            "- 结论：无法基于真实数据回答与金额/比率/项目状态相关的问题。\n"
        )

    metrics = facts.metrics
    lines = ["**Canonical Facts 摘要（仅来自 analytics_project_full 真实指标）**"]
    lines.append(f"- 项目：{facts.project_code}")
    if facts.as_of:
        lines.append(f"- 数据截至 (as_of)：{facts.as_of}")
    if facts.facts_version:
        lines.append(f"- 事实版本：{facts.facts_version}")

    def _fmt(key: str, unit_hint: Optional[str] = None) -> Optional[str]:
        mv = metrics.get(key)
        if mv is None:
            return None
        unit = unit_hint or mv.unit or ""
        try:
            value = float(mv.value)
        except (TypeError, ValueError):
            return None
        return f"  - {key} = {value:g} {unit}".strip()

    for line in (
        _fmt("recognized_revenue", "CNY"),
        _fmt("real_project_cost", "CNY"),
        _fmt("real_profit", "CNY"),
        _fmt("collected_amount", "CNY"),
        _fmt("unpaid_amount", "CNY"),
        _fmt("collection_rate", "percent"),
        _fmt("eac_revenue", "CNY"),
        _fmt("eac_cost", "CNY"),
        _fmt("eac_profit", "CNY"),
        _fmt("eac_margin", "percent"),
        _fmt("cash_gap_30d", "CNY"),
        _fmt("tax_burden_rate", "percent"),
        _fmt("cost_variance", "CNY"),
        _fmt("health_score", "score"),
    ):
        if line:
            lines.append(line)
    if len(lines) == 4:
        lines.append("- （该项目无可用指标详情）")
    lines.append("")
    lines.append(
        "**AI 约束**：本回答仅可基于上述 Canonical Facts 描述事实，不得编造"
        "任何未在事实中出现的数据。如事实不足，请明确告知用户。\n"
    )
    return "\n".join(lines)


def _build_chat_payload(query: str, facts: Optional[FactsResponse]) -> Dict[str, Any]:
    """Compose the AI reply for a given query, anchored on Canonical Facts.

    Both ``/ai/chat`` (JSON) and ``/ai/chat/stream`` (SSE) consume this helper
    so the two endpoints stay in lock-step.

    The reply ALWAYS starts with the deterministic facts summary. When facts
    are unavailable the summary explicitly says so; the model is instructed
    not to fabricate numbers.
    """

    q = query.strip()
    citations: List[Dict[str, str]] = []

    facts_summary = _facts_summary_block(facts) if facts is not None else (
        "**Canonical Facts 状态：未提供项目范围**\n"
        "当前未指定 project_id，无法获取任何项目的 Canonical Facts。\n"
    )

    if "利润" in q or "毛利" in q or "赚" in q:
        response_body = """**【董事长经营内参 · 集团利润分析】**

回答必须严格基于上文 Canonical Facts 摘要。请直接引用其中金额/比率，并
明确指出事实范围以外的信息无法提供。"""
        citations = [
            {"title": "Canonical Facts 计算引擎文档", "url": "/docs/canonical-facts"},
            {"title": "集团经营大盘汇总", "url": "/api/v1/executive/cockpit/summary"}
        ]

    elif "税" in q or "发票" in q or "预缴" in q or "抵扣" in q:
        response_body = """**【董事长财税风控内参 · 税务分析】**

回答必须严格基于上文 Canonical Facts 摘要。如需补充进销项发票 / 预缴税款
等指标，请确认该指标已经在 analytics_project_full 中可用。"""
        citations = [
            {"title": "Canonical Facts 税务模块文档", "url": "/docs/canonical-facts/tax"},
            {"title": "集团经营大盘汇总", "url": "/api/v1/executive/cockpit/summary"}
        ]

    elif "风险" in q or "合规" in q or "四流" in q or "三流" in q or "预警" in q:
        response_body = """**【董事长合规风控内参 · 风险预警】**

风险结论请基于上文 health_score / collection_rate / cash_gap_30d 等指标
给出。事实之外的风险等级不得推断。"""
        citations = [
            {"title": "Canonical Facts 风险模块文档", "url": "/docs/canonical-facts/risk"},
            {"title": "法人全景矩阵", "url": "/api/v1/executive/entities/matrix"}
        ]

    elif "公司" in q or "法人" in q or "abcd" in q:
        response_body = """**【董事长组织穿透内参 · 法人公司经营矩阵】**

请通过 /api/v1/executive/entities/matrix 接口获取法人基础信息。
若需经营/税务指标，请先在 analytics_* 视图中落地后再返回本系统。"""
        citations = [
            {"title": "集团法人全景矩阵", "url": "/api/v1/executive/entities/matrix"}
        ]

    else:
        response_body = """**【董事长 AI 智策助手 · 综合经营分析】**

请基于上文 Canonical Facts 摘要回答用户问题，并明确指出哪些信息不在事实
范围内。如事实不足，请直接告知并提示通过 cockpit/summary 接口确认。"""
        citations = [
            {"title": "Canonical Facts 计算引擎文档", "url": "/docs/canonical-facts"},
            {"title": "集团经营大盘汇总", "url": "/api/v1/executive/cockpit/summary"}
        ]

    response_text = f"{facts_summary}\n---\n{response_body}"
    return {"reply": response_text, "citations": citations}


@router.post("/ai/chat")
def executive_ai_chat(
    req: AIChatRequest,
    principal: TaxPrincipal = Depends(require_executive),
):
    """董事长移动端专属 AI 智策问答与经营决策诊断接口 (JSON, backward-compatible)"""
    q = req.message.strip()
    if not q:
        raise HTTPException(status_code=400, detail="提问内容不能为空")

    db = SessionLocal()
    try:
        facts: Optional[FactsResponse] = None
        facts_available = False
        facts_reason: Optional[str] = None
        facts_as_of: Optional[str] = None
        if req.project_id is not None:
            proj_row = db.execute(
                text("SELECT project_code FROM projects WHERE id = :id"),
                {"id": req.project_id},
            ).fetchone()
            if not proj_row:
                raise HTTPException(status_code=404, detail="未找到指定项目")
            pcode = proj_row[0]
            facts = _safe_get_facts(db, pcode)
            facts_available = bool(facts.facts_available)
            facts_reason = None if facts_available else facts.reason
            facts_as_of = facts.as_of if facts_available else None
        payload = _build_chat_payload(q, facts)
        return {
            "status": "success",
            "query": q,
            "reply": payload["reply"],
            "citations": payload["citations"],
            "timestamp": int(time.time()),
            "_meta": _meta_block(
                principal,
                facts_available=facts_available,
                facts_reason=facts_reason,
                as_of=facts_as_of,
            ),
        }
    finally:
        db.close()


@router.post("/ai/chat/stream")
async def executive_ai_chat_stream(
    req: AIChatRequest,
    principal: TaxPrincipal = Depends(require_executive),
):
    """SSE streaming variant. Emits four event types:

    - ``event: delta\\ndata: <chunk>``   text fragments as they are produced
    - ``event: citations\\ndata: [...]``  final structured references
    - ``event: meta\\ndata: {...}``        data_source / facts_available envelope
    - ``event: done\\ndata: {"timestamp":...}``  terminator

    Front-end reads ``ReadableStream`` and pushes deltas into the bubble.
    """
    q = req.message.strip()
    if not q:
        raise HTTPException(status_code=400, detail="提问内容不能为空")

    db = SessionLocal()
    try:
        facts: Optional[FactsResponse] = None
        facts_available = False
        facts_reason: Optional[str] = None
        facts_as_of: Optional[str] = None
        if req.project_id is not None:
            proj_row = db.execute(
                text("SELECT project_code FROM projects WHERE id = :id"),
                {"id": req.project_id},
            ).fetchone()
            if not proj_row:
                raise HTTPException(status_code=404, detail="未找到指定项目")
            pcode = proj_row[0]
            facts = _safe_get_facts(db, pcode)
            facts_available = bool(facts.facts_available)
            facts_reason = None if facts_available else facts.reason
            facts_as_of = facts.as_of if facts_available else None
        payload = _build_chat_payload(q, facts)
        reply_text = payload["reply"]
        citations = payload["citations"]
        meta_block = _meta_block(
            principal,
            facts_available=facts_available,
            facts_reason=facts_reason,
            as_of=facts_as_of,
        )

        async def event_source():
            # Chunk by paragraph so the UI can render discrete sections.
            # The chunk delay is small but non-zero, giving a perceptible
            # "typing" cadence for ~200-400 chars per tick.
            chunk_size = 24
            for start in range(0, len(reply_text), chunk_size):
                chunk = reply_text[start:start + chunk_size]
                yield f"event: delta\ndata: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0.04)

            yield f"event: citations\ndata: {json.dumps(citations, ensure_ascii=False)}\n\n"
            # 同时把 _meta 透传给客户端，便于前端展示数据来源与时戳。
            yield f"event: meta\ndata: {json.dumps(meta_block, ensure_ascii=False)}\n\n"
            yield f"event: done\ndata: {json.dumps({'timestamp': int(time.time())}, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive"
            }
        )
    finally:
        db.close()
