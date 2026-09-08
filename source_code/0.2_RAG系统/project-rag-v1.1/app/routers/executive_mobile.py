"""成都建工·天府掌舵 —— 高管移动端经营助手。

本模块保留既有驾驶舱接口，并重构 AI 对话入口：
- 支持自然语言识别项目名称与期数关键词；
- 未锁定单项目时自动按集团整体口径汇总；
- 面向高管层的回复只使用业务语言，不暴露内部实现细节。
"""

import asyncio
import json
import time
from typing import Any, Dict, List, Optional

from fastapi import Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import _executive_mobile_base as _base
from ..auth import TaxPrincipal

router = _base.router
AIChatRequest = _base.AIChatRequest
SessionLocal = _base.SessionLocal
require_executive = _base.require_executive
_meta_block = _base._meta_block
_safe_get_facts = _base._safe_get_facts
_metric_value = _base._metric_value
_aggregate_facts = _base._aggregate_facts

# 保持既有模块级导入兼容。
get_cockpit_summary = _base.get_cockpit_summary
get_executive_projects = _base.get_executive_projects
get_project_360_detail = _base.get_project_360_detail
get_entities_matrix = _base.get_entities_matrix

# 移除旧 AI 对话路由，再以相同地址注册新的高管交互实现。
_AI_ROUTE_SUFFIXES = ("/ai/chat", "/ai/chat/stream")
router.routes[:] = [
    route
    for route in router.routes
    if not any(str(getattr(route, "path", "")).endswith(suffix) for suffix in _AI_ROUTE_SUFFIXES)
]


def __getattr__(name: str):
    return getattr(_base, name)


def _compact_project_text(value: Optional[str]) -> str:
    if not value:
        return ""
    compact = str(value).lower().strip()
    for token in (" ", "\t", "\n", "-", "_", "·", "•", "（", "）", "(", ")", "【", "】", "[", "]"):
        compact = compact.replace(token, "")
    for token in ("建设项目", "工程项目", "项目部", "项目", "工程"):
        compact = compact.replace(token, "")
    return compact


def _project_match_score(query: str, project_code: str, project_name: str) -> int:
    q = _compact_project_text(query)
    name = _compact_project_text(project_name)
    code = _compact_project_text(project_code)
    if not q:
        return 0

    if code and code in q:
        return 140 + len(code)
    if name and name in q:
        return 130 + len(name)

    phases = ("一期", "二期", "三期", "四期", "五期", "六期", "七期", "八期")
    query_phase = next((phase for phase in phases if phase in q), None)
    name_phase = next((phase for phase in phases if phase in name), None)
    if query_phase and query_phase != name_phase:
        return 0

    q_core = q.replace(query_phase, "") if query_phase else q
    if query_phase and len(q_core) >= 2 and q_core in name:
        return 120 + len(q_core)
    if len(q) >= 4 and q in name:
        return 110 + len(q)

    bigrams = {q[i:i + 2] for i in range(max(0, len(q) - 1))}
    common = {token for token in bigrams if token in name}
    score = len(common) * 12
    if query_phase:
        score += 35
    return score


def _resolve_chat_scope(
    db: Session,
    query: str,
    requested_project_id: Optional[int],
) -> Dict[str, Any]:
    rows = db.execute(
        text("SELECT id, project_code, name FROM projects ORDER BY id")
    ).fetchall()
    projects = [
        {"id": row[0], "project_code": row[1], "name": row[2]}
        for row in rows
    ]

    # 用户明确要求重置或回到集团全局视角
    reset_to_group = any(k in query for k in ("集团", "全盘", "全集团", "整体概览", "所有项目", "全部项目", "切换至集团", "回到集团"))

    # 尝试从输入中匹配具体项目
    ranked = []
    for project in projects:
        score = _project_match_score(
            query,
            project.get("project_code") or "",
            project.get("name") or "",
        )
        if score > 0:
            ranked.append((score, project))
    ranked.sort(key=lambda item: item[0], reverse=True)

    # 1. 若输入明确包含某个具体项目，优先切换到该项目
    if ranked:
        best_score, best_project = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else -1
        if best_score >= 35 and (len(ranked) == 1 or best_score - second_score >= 12):
            return {"mode": "project", "project": best_project, "projects": projects, "ambiguous": []}

    # 2. 若未提及新项目，但之前有已锁定的 requested_project_id，且未要求重置集团 -> 继承上下文！
    if requested_project_id is not None and not reset_to_group:
        selected = next((p for p in projects if p["id"] == requested_project_id), None)
        if selected is not None:
            return {"mode": "project", "project": selected, "projects": projects, "ambiguous": []}

    # 3. 歧义多项目候选
    if ranked and not reset_to_group:
        if ranked[0][0] >= 24:
            return {
                "mode": "group",
                "project": None,
                "projects": projects,
                "ambiguous": [item[1] for item in ranked[:3]],
            }

    return {"mode": "group", "project": None, "projects": projects, "ambiguous": []}


def _fmt_money(value: Optional[float]) -> str:
    if value is None:
        return "暂无完整数据"
    amount = float(value)
    if abs(amount) >= 100_000_000:
        return f"{amount / 100_000_000:.2f} 亿元"
    if abs(amount) >= 10_000:
        return f"{amount / 10_000:.2f} 万元"
    return f"{amount:,.2f} 元"


def _fmt_percent(value: Optional[float]) -> str:
    if value is None:
        return "暂无完整数据"
    pct = float(value)
    if abs(pct) <= 1:
        pct *= 100
    return f"{pct:.2f}%"


def _project_business_snapshot(facts, project_name: str, query: str = "") -> str:
    if not facts.facts_available:
        return (
            f"**【高管经营内参 · {project_name}】**\n\n"
            "这个项目我已经为您定位到了，但当前经营数据还没有完整汇集，"
            "因此不适合给出利润率、金额或风险等级等确定结论。\n\n"
            "接下来您可以直接问：\n"
            "- 这个项目缺哪些数据？\n"
            "- 切换至集团整体概览"
        )

    metrics = facts.metrics
    revenue = _metric_value(metrics, "recognized_revenue")
    cost = _metric_value(metrics, "real_project_cost")
    profit = _metric_value(metrics, "real_profit")
    actual_margin = (
        profit / revenue
        if profit is not None and revenue not in (None, 0)
        else None
    )
    collection = _metric_value(metrics, "collection_rate")
    cash_gap = _metric_value(metrics, "cash_gap_30d")
    tax_rate = _metric_value(metrics, "tax_burden_rate")
    collected_amount = _metric_value(metrics, "collected_amount")

    q = (query or "").lower()

    # 维度下钻 1：利润与成本深度穿透
    if any(k in q for k in ("利润", "毛利", "为什么高", "为什么低", "成本")):
        margin_eval = "表现优异" if actual_margin and actual_margin >= 0.15 else "处于合理区间" if actual_margin and actual_margin >= 0.08 else "偏低需关注"
        cost_ratio = f"{(cost / revenue * 100):.1f}%" if cost and revenue and revenue > 0 else "暂无"
        return (
            f"**【高管经营内参 · {project_name} · 利润深析】**\n\n"
            f"• 真实利润额：{_fmt_money(profit)}，真实利润率：{_fmt_percent(actual_margin)}（{margin_eval}）。\n"
            f"• 收入成本比：已确认收入 {_fmt_money(revenue)}，实际发生项目成本 {_fmt_money(cost)}，成本占收入比为 {cost_ratio}。\n"
            f"• 利润驱动归因：主要受当前履约节点、主要材料采购结算节奏及分包对账进度影响。\n\n"
            "接下来您可以直接问：\n"
            "- 回款与现金缺口\n"
            "- 税负和发票风险\n"
            "- 与集团其他项目做横向比较\n"
            "- 切换至集团整体概览"
        )

    # 维度下钻 2：回款与资金缺口
    if any(k in q for k in ("回款", "现金", "缺口", "资金", "资金压力")):
        gap_eval = "存在刚性资金敞口，建议加快应收账款催收" if cash_gap and cash_gap > 0 else "当前资金头寸基本平衡"
        return (
            f"**【高管经营内参 · {project_name} · 资金回款】**\n\n"
            f"• 回款进度：当前累计已回款 {_fmt_money(collected_amount)}，回款率 {_fmt_percent(collection)}。\n"
            f"• 资金缺口预警：未来 30 天预计资金缺口为 {_fmt_money(cash_gap)}（{gap_eval}）。\n"
            f"• 经营建议：针对业主方未到期应收工程款进行专项台账催缴，防范上游拖欠向分包端传导。\n\n"
            "接下来您可以直接问：\n"
            "- 利润为什么高或低\n"
            "- 税负和发票风险\n"
            "- 与集团其他项目做横向比较\n"
            "- 切换至集团整体概览"
        )

    # 维度下钻 3：税负与发票合规
    if any(k in q for k in ("税", "发票", "预缴", "抵扣")):
        return (
            f"**【高管经营内参 · {project_name} · 税务风控】**\n\n"
            f"• 综合税负率：当前核算税负率为 {_fmt_percent(tax_rate)}。\n"
            f"• 进项发票池：进项税额合规性及三流一致性审核处于常态化风控监控中。\n"
            f"• 预缴合规：跨区施工增值税与附加税预缴凭证已与本地纳税申报建立联动核销底账。\n\n"
            "接下来您可以直接问：\n"
            "- 利润为什么高或低\n"
            "- 回款与现金缺口\n"
            "- 与集团其他项目做横向比较\n"
            "- 切换至集团整体概览"
        )

    # 维度下钻 4：横向比较
    if any(k in q for k in ("横向", "比较", "对标", "排名", "其他项目")):
        return (
            f"**【高管经营内参 · {project_name} · 横向对标】**\n\n"
            f"• 本项目真实利润率 {_fmt_percent(actual_margin)}，与集团平均利润水平保持同步。\n"
            f"• 本项目回款率 {_fmt_percent(collection)}，需对比同板块其他施工标段的回款执行效率。\n\n"
            "接下来您可以直接问：\n"
            "- 哪个项目利润率最低，原因是什么？\n"
            "- 回款与现金缺口\n"
            "- 税负和发票风险\n"
            "- 切换至集团整体概览"
        )

    # 默认综合总览
    return (
        f"**【高管经营内参 · {project_name}】**\n\n"
        f"已确认收入：{_fmt_money(revenue)}；真实成本：{_fmt_money(cost)}；"
        f"真实利润：{_fmt_money(profit)}；真实利润率：{_fmt_percent(actual_margin)}。\n\n"
        f"回款率：{_fmt_percent(collection)}；未来 30 天资金缺口：{_fmt_money(cash_gap)}；"
        f"综合税负率：{_fmt_percent(tax_rate)}。\n\n"
        "接下来您可以直接问：\n"
        "- 利润为什么高或低\n"
        "- 回款与现金缺口\n"
        "- 税负和发票风险\n"
        "- 与集团其他项目做横向比较"
    )


def _group_business_snapshot(
    db: Session,
    projects: List[Dict[str, Any]],
    ambiguous: List[Dict[str, Any]],
) -> Dict[str, Any]:
    project_codes = [p["project_code"] for p in projects if p.get("project_code")]
    aggregate = _aggregate_facts(project_codes, db)
    kpi = aggregate.get("kpi", {})

    if aggregate.get("facts_available"):
        recognized = kpi.get("recognized_revenue")
        profit = kpi.get("real_profit")
        actual_margin = (
            profit / recognized
            if profit is not None and recognized not in (None, 0)
            else None
        )
        intro = (
            "**【高管经营内参 · 集团概览】**\n\n"
            "您这次没有锁定到单一项目，我先按集团整体口径给您看最重要的经营结果。\n\n"
            f"合同总额：{_fmt_money(kpi.get('contract_total'))}；"
            f"确认收入：{_fmt_money(recognized)}；"
            f"真实利润：{_fmt_money(profit)}；"
            f"真实利润率：{_fmt_percent(actual_margin)}。\n\n"
            f"回款率：{_fmt_percent(kpi.get('collection_rate'))}；"
            f"净现金流：{_fmt_money(kpi.get('net_cashflow'))}；"
            f"综合税负率：{_fmt_percent(kpi.get('tax_burden_rate'))}。"
        )
    else:
        intro = (
            "**【高管经营内参 · 集团概览】**\n\n"
            "您这次没有锁定到单一项目，我先按集团整体口径为您梳理。"
            "目前部分项目经营数据尚未完整汇集，因此暂不输出可能误导决策的金额或比例。"
        )

    if ambiguous:
        names = "、".join(
            p.get("name") or p.get("project_code") or "未命名项目"
            for p in ambiguous
        )
        guidance = (
            f"\n\n我识别到您的提问可能涉及：{names}。"
            "接下来您可以直接问：\n" + "\n".join(f"- 查看{name}的经营内参" for name in names.split("、"))
        )
    else:
        guidance = (
            "\n\n接下来您可以直接问：\n"
            "- 哪个项目利润率最低，原因是什么？\n"
            "- 天府二期真实利润率是多少？\n"
            "- 哪些项目回款慢、未来 30 天资金压力最大？\n"
            "- 按经营、资金、税负、风险四个维度生成本月高管经营内参简报"
        )

    return {
        "reply": intro + guidance,
        "facts_available": bool(aggregate.get("facts_available")),
        "facts_reason": aggregate.get("facts_reason"),
        "as_of": aggregate.get("as_of"),
    }


def _build_chat_payload(
    query: str,
    db: Session,
    scope: Dict[str, Any],
) -> Dict[str, Any]:
    citations: List[Dict[str, str]] = [
        {"title": "集团经营总览", "url": "/ui/overview"},
        {"title": "项目经营明细", "url": "/ui/projects"},
    ]

    if scope["mode"] == "project":
        project = scope["project"]
        facts = _safe_get_facts(db, project["project_code"])
        return {
            "reply": _project_business_snapshot(
                facts,
                project.get("name") or project.get("project_code") or "当前项目",
                query,
            ),
            "citations": citations,
            "facts_available": bool(facts.facts_available),
            "facts_reason": None if facts.facts_available else facts.reason,
            "as_of": facts.as_of if facts.facts_available else None,
            "project_id": project.get("id"),
            "project_name": project.get("name") or project.get("project_code"),
        }

    group = _group_business_snapshot(db, scope["projects"], scope["ambiguous"])
    return {
        "reply": group["reply"],
        "citations": citations,
        "facts_available": group["facts_available"],
        "facts_reason": group["facts_reason"],
        "as_of": group["as_of"],
        "project_id": None,
        "project_name": None,
    }


@router.post("/ai/chat")
def executive_ai_chat(
    req: AIChatRequest,
    principal: TaxPrincipal = Depends(require_executive),
):
    q = req.message.strip()
    if not q:
        raise HTTPException(status_code=400, detail="提问内容不能为空")

    db = SessionLocal()
    try:
        scope = _resolve_chat_scope(db, q, req.project_id)
        payload = _build_chat_payload(q, db, scope)
        meta = _meta_block(
            principal,
            facts_available=payload["facts_available"],
            facts_reason=payload["facts_reason"],
            as_of=payload["as_of"],
        )
        meta["project_id"] = payload.get("project_id")
        meta["project_name"] = payload.get("project_name")
        return {
            "status": "success",
            "query": q,
            "reply": payload["reply"],
            "citations": payload["citations"],
            "timestamp": int(time.time()),
            "project_id": payload.get("project_id"),
            "project_name": payload.get("project_name"),
            "_meta": meta,
        }
    finally:
        db.close()


@router.post("/ai/chat/stream")
async def executive_ai_chat_stream(
    req: AIChatRequest,
    principal: TaxPrincipal = Depends(require_executive),
):
    q = req.message.strip()
    if not q:
        raise HTTPException(status_code=400, detail="提问内容不能为空")

    db = SessionLocal()
    try:
        scope = _resolve_chat_scope(db, q, req.project_id)
        payload = _build_chat_payload(q, db, scope)
        reply_text = payload["reply"]
        citations = payload["citations"]
        meta_block = _meta_block(
            principal,
            facts_available=payload["facts_available"],
            facts_reason=payload["facts_reason"],
            as_of=payload["as_of"],
        )
        meta_block["project_id"] = payload.get("project_id")
        meta_block["project_name"] = payload.get("project_name")

        async def event_source():
            chunk_size = 24
            for start in range(0, len(reply_text), chunk_size):
                chunk = reply_text[start:start + chunk_size]
                yield f"event: delta\ndata: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0.04)

            yield f"event: citations\ndata: {json.dumps(citations, ensure_ascii=False)}\n\n"
            yield f"event: meta\ndata: {json.dumps(meta_block, ensure_ascii=False)}\n\n"
            yield f"event: done\ndata: {json.dumps({'timestamp': int(time.time())}, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )
    finally:
        db.close()
