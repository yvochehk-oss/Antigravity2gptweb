"""成都建工·天府掌舵 —— 高管移动端经营助手。

AI 对话采用严格的三层证据降级：
1. 权威经营底账（PostgreSQL 经营/税务事实）；
2. 已解析业务资料的混合检索证据；
3. 原始资料库元数据与附件线索。

任何层都不会为缺失事实“补数字”；三层均无相关记录时明确返回
“暂无相关记录与凭证”。
"""

import asyncio
import json
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import httpx
from fastapi import Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import _executive_mobile_base as _base
from ..auth import TaxPrincipal
from ..services.retrieval import retrieve

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
    rows = db.execute(text("SELECT id, project_code, name FROM projects ORDER BY id")).fetchall()
    projects = [
        {"id": row[0], "project_code": row[1], "name": row[2]}
        for row in rows
    ]

    reset_to_group = any(
        k in query
        for k in ("集团", "全盘", "全集团", "整体概览", "所有项目", "全部项目", "切换至集团", "回到集团")
    )

    ranked: List[Tuple[int, Dict[str, Any]]] = []
    for project in projects:
        score = _project_match_score(
            query,
            project.get("project_code") or "",
            project.get("name") or "",
        )
        if score > 0:
            ranked.append((score, project))
    ranked.sort(key=lambda item: item[0], reverse=True)

    if ranked:
        best_score, best_project = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else -1
        if best_score >= 35 and (len(ranked) == 1 or best_score - second_score >= 12):
            return {"mode": "project", "project": best_project, "projects": projects, "ambiguous": []}

    if requested_project_id is not None and not reset_to_group:
        selected = next((p for p in projects if p["id"] == requested_project_id), None)
        if selected is not None:
            return {"mode": "project", "project": selected, "projects": projects, "ambiguous": []}

    if ranked and not reset_to_group and ranked[0][0] >= 24:
        return {
            "mode": "group",
            "project": None,
            "projects": projects,
            "ambiguous": [item[1] for item in ranked[:3]],
        }

    return {"mode": "group", "project": None, "projects": projects, "ambiguous": []}


def _fmt_money(value: Optional[float]) -> str:
    if value is None:
        return "暂无记录"
    amount = float(value)
    if abs(amount) >= 100_000_000:
        return f"{amount / 100_000_000:.2f} 亿元"
    if abs(amount) >= 10_000:
        return f"{amount / 10_000:.2f} 万元"
    return f"{amount:,.2f} 元"


def _fmt_percent(value: Optional[float]) -> str:
    if value is None:
        return "暂无记录"
    pct = float(value)
    if abs(pct) <= 1:
        pct *= 100
    return f"{pct:.2f}%"


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_present(columns: Sequence[str], aliases: Sequence[str]) -> Optional[str]:
    column_set = set(columns)
    return next((name for name in aliases if name in column_set), None)


def _qident(value: str) -> str:
    # Identifiers passed here originate only from information_schema or fixed relation names.
    return '"' + value.replace('"', '""') + '"'


def _relation_schema_and_columns(db: Session, relation: str) -> Tuple[Optional[str], List[str]]:
    try:
        rows = db.execute(
            text(
                """
                SELECT table_schema, column_name
                FROM information_schema.columns
                WHERE table_name = :relation
                  AND table_schema NOT IN ('pg_catalog', 'information_schema')
                ORDER BY
                  CASE
                    WHEN table_schema = current_schema() THEN 0
                    WHEN table_schema = 'public' THEN 1
                    ELSE 2
                  END,
                  ordinal_position
                """
            ),
            {"relation": relation},
        ).fetchall()
    except Exception:
        db.rollback()
        return None, []

    if not rows:
        return None, []
    schema = rows[0][0]
    return schema, [row[1] for row in rows if row[0] == schema]


def _scope_filter(
    columns: Sequence[str],
    scope: Dict[str, Any],
) -> Tuple[Optional[str], Dict[str, Any]]:
    if scope.get("mode") != "project" or not scope.get("project"):
        return None, {}

    project = scope["project"]
    candidates = (
        ("project_id", project.get("id")),
        ("project_code", project.get("project_code")),
        ("project_name", project.get("name")),
        ("name", project.get("name")),
    )
    for column, value in candidates:
        if column in columns and value not in (None, ""):
            return f"{_qident(column)} = :scope_value", {"scope_value": value}
    return None, {}


def _fetch_relation_rows(
    db: Session,
    relation: str,
    scope: Dict[str, Any],
    *,
    limit: int,
) -> List[Dict[str, Any]]:
    schema, columns = _relation_schema_and_columns(db, relation)
    if not schema or not columns:
        return []

    where_sql, params = _scope_filter(columns, scope)
    sql = f"SELECT * FROM {_qident(schema)}.{_qident(relation)}"
    if where_sql:
        sql += f" WHERE {where_sql}"
    sql += " LIMIT :row_limit"
    params["row_limit"] = limit

    try:
        return [dict(row) for row in db.execute(text(sql), params).mappings().all()]
    except Exception:
        db.rollback()
        return []


_DIRECTION_ALIASES = (
    "direction", "invoice_direction", "business_direction", "biz_direction",
    "io_type", "purchase_sales_type", "invoice_nature",
)
_INVOICE_NO_ALIASES = ("invoice_no", "invoice_number", "number", "invoice_code")
_NET_AMOUNT_ALIASES = (
    "net", "amount_without_tax", "net_amount", "tax_exclusive_amount",
    "amount_excluding_tax", "untaxed_amount", "amount",
)
_GROSS_AMOUNT_ALIASES = (
    "amount_with_tax", "gross_amount", "total_amount",
    "amount_tax_included", "tax_inclusive_amount", "invoice_amount",
)
_TAX_AMOUNT_ALIASES = ("vat", "tax_amount", "vat_amount", "vat_tax", "tax")
_DATE_ALIASES = ("invoice_date", "issue_date", "issued_at", "billing_date", "date")
_COUNTERPARTY_ALIASES = (
    "counterparty_name", "buyer_name", "seller_name", "vendor_name",
    "supplier_name", "customer_name",
)


def _classify_direction(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in ("out", "output", "sale", "sales") or any(
        token in raw for token in ("销项", "销", "output", "sale", "sales")
    ):
        return "销项"
    if raw in ("in", "input", "purchase", "buy") or any(
        token in raw for token in ("进项", "进", "input", "purchase", "buy")
    ):
        return "进项"
    return "未标明方向"


def _sum_field(rows: Iterable[Dict[str, Any]], field: Optional[str]) -> Optional[float]:
    if not field:
        return None
    values = [_to_float(row.get(field)) for row in rows]
    actual = [value for value in values if value is not None]
    return sum(actual) if actual else None


def _invoice_ledger_block(rows: List[Dict[str, Any]]) -> Tuple[str, bool]:
    if not rows:
        return "发票底账：未检索到与当前范围匹配的记录。", False

    columns = list(rows[0].keys())
    direction_col = _first_present(columns, _DIRECTION_ALIASES)
    number_col = _first_present(columns, _INVOICE_NO_ALIASES)
    net_col = _first_present(columns, _NET_AMOUNT_ALIASES)
    gross_col = _first_present(columns, _GROSS_AMOUNT_ALIASES)
    tax_col = _first_present(columns, _TAX_AMOUNT_ALIASES)
    date_col = _first_present(columns, _DATE_ALIASES)
    counterparty_col = _first_present(columns, _COUNTERPARTY_ALIASES)

    buckets = {"销项": [], "进项": [], "未标明方向": []}
    for row in rows:
        buckets[_classify_direction(row.get(direction_col) if direction_col else None)].append(row)

    lines = [f"发票底账记录：{len(rows)} 条。"]
    for label in ("销项", "进项", "未标明方向"):
        bucket = buckets[label]
        if not bucket:
            continue
        parts = [f"{label} {len(bucket)} 笔"]
        net_total = _sum_field(bucket, net_col)
        gross_total = _sum_field(bucket, gross_col)
        tax_total = _sum_field(bucket, tax_col)
        if gross_total is None and (net_total is not None or tax_total is not None):
            gross_total = (net_total or 0.0) + (tax_total or 0.0)
        if net_total is not None:
            parts.append(f"不含税金额 {_fmt_money(net_total)}")
        if gross_total is not None and (net_total is None or abs(gross_total - net_total) > 0.005):
            parts.append(f"价税合计 {_fmt_money(gross_total)}")
        if tax_total is not None:
            parts.append(f"税额 {_fmt_money(tax_total)}")
        lines.append("；".join(parts) + "。")

    if number_col:
        representatives = []
        for row in rows:
            number = str(row.get(number_col) or "").strip()
            if not number:
                continue
            details = [f"发票号 {number}"]
            if direction_col:
                details.append(_classify_direction(row.get(direction_col)))
            if date_col and row.get(date_col):
                details.append(f"日期 {row.get(date_col)}")
            amount = _to_float(row.get(gross_col)) if gross_col else None
            tax_value = _to_float(row.get(tax_col)) if tax_col else None
            if amount is None:
                net_value = _to_float(row.get(net_col)) if net_col else None
                if net_value is not None or tax_value is not None:
                    amount = (net_value or 0.0) + (tax_value or 0.0)
            if amount is not None:
                details.append(f"金额 {_fmt_money(amount)}")
            if tax_value is not None:
                details.append(f"税额 {_fmt_money(tax_value)}")
            if counterparty_col and row.get(counterparty_col):
                details.append(f"往来方 {row.get(counterparty_col)}")
            representatives.append("，".join(details))
            if len(representatives) >= 5:
                break
        if representatives:
            lines.append("代表性发票：" + "；".join(representatives) + "。")

    return "\n".join(lines), True


_TAX_METRICS = {
    "销项发票笔数": ("output_invoice_count", "sales_invoice_count", "output_count"),
    "进项发票笔数": ("input_invoice_count", "purchase_invoice_count", "input_count"),
    "销项金额": ("output_amount", "sales_amount", "output_invoice_amount"),
    "进项金额": ("input_amount", "purchase_amount", "input_invoice_amount"),
    "销项税额": ("output_tax", "output_tax_amount", "sales_tax_amount", "vat_output"),
    "进项税额": ("input_tax", "input_tax_amount", "purchase_tax_amount", "vat_input"),
    "应交增值税": ("vat_payable", "vat_payable_amount", "payable_vat", "vat_due"),
    "综合税负率": ("tax_burden_rate", "vat_burden_rate", "effective_tax_rate"),
}


def _analytics_tax_block(rows: List[Dict[str, Any]]) -> Tuple[str, bool]:
    if not rows:
        return "税务分析底账：未检索到与当前范围匹配的记录。", False

    columns = list(rows[0].keys())
    lines = [f"税务分析底账记录：{len(rows)} 条。"]
    recognized = 0
    for label, aliases in _TAX_METRICS.items():
        column = _first_present(columns, aliases)
        if not column:
            continue
        values = [_to_float(row.get(column)) for row in rows]
        actual = [value for value in values if value is not None]
        if not actual:
            continue
        value = sum(actual) if len(actual) > 1 and "率" not in label else actual[-1]
        if "率" in label:
            lines.append(f"{label}：{_fmt_percent(value)}。")
        elif "笔数" in label:
            lines.append(f"{label}：{int(value)} 笔。")
        else:
            lines.append(f"{label}：{_fmt_money(value)}。")
        recognized += 1

    if not recognized:
        lines.append("已确认该范围存在税务分析记录，但当前视图未暴露可安全映射的标准指标字段。")
    return "\n".join(lines), True


def _macro_facts_block(
    db: Session,
    scope: Dict[str, Any],
) -> Tuple[str, Dict[str, Any], bool]:
    if scope["mode"] == "project":
        project = scope["project"]
        facts = _safe_get_facts(db, project["project_code"])
        meta = {
            "facts_available": bool(facts.facts_available),
            "facts_reason": None if facts.facts_available else facts.reason,
            "as_of": facts.as_of if facts.facts_available else None,
            "project_id": project.get("id"),
            "project_name": project.get("name") or project.get("project_code"),
        }
        if not facts.facts_available:
            return (
                f"项目：{meta['project_name']}。宏观经营指标当前暂无完整记录。",
                meta,
                False,
            )

        metrics = facts.metrics
        revenue = _metric_value(metrics, "recognized_revenue")
        cost = _metric_value(metrics, "real_project_cost")
        profit = _metric_value(metrics, "real_profit")
        margin = profit / revenue if profit is not None and revenue not in (None, 0) else None
        lines = [
            f"项目：{meta['project_name']}（{project.get('project_code')}）。",
            f"已确认收入：{_fmt_money(revenue)}。",
            f"实际项目成本：{_fmt_money(cost)}。",
            f"真实利润额：{_fmt_money(profit)}。",
            f"真实利润率：{_fmt_percent(margin)}。",
            f"累计已回款：{_fmt_money(_metric_value(metrics, 'collected_amount'))}。",
            f"回款率：{_fmt_percent(_metric_value(metrics, 'collection_rate'))}。",
            f"未来30天预计资金缺口：{_fmt_money(_metric_value(metrics, 'cash_gap_30d'))}。",
            f"销项增值税：{_fmt_money(_metric_value(metrics, 'output_vat'))}。",
            f"进项增值税：{_fmt_money(_metric_value(metrics, 'input_vat'))}。",
            f"应交增值税：{_fmt_money(_metric_value(metrics, 'vat_payable'))}。",
            f"综合税负率：{_fmt_percent(_metric_value(metrics, 'tax_burden_rate'))}。",
        ]
        return "\n".join(lines), meta, True

    projects = scope["projects"]
    project_codes = [p["project_code"] for p in projects if p.get("project_code")]
    aggregate = _aggregate_facts(project_codes, db)
    kpi = aggregate.get("kpi", {})
    meta = {
        "facts_available": bool(aggregate.get("facts_available")),
        "facts_reason": aggregate.get("facts_reason"),
        "as_of": aggregate.get("as_of"),
        "project_id": None,
        "project_name": None,
    }
    if not aggregate.get("facts_available"):
        return "集团宏观经营指标当前暂无完整记录。", meta, False

    recognized = kpi.get("recognized_revenue")
    profit = kpi.get("real_profit")
    margin = profit / recognized if profit is not None and recognized not in (None, 0) else None
    lines = [
        f"集团在建重点项目数量：{len(projects)} 个。",
        f"合同总额：{_fmt_money(kpi.get('contract_total'))}。",
        f"累计确认收入：{_fmt_money(recognized)}。",
        f"累计真实利润：{_fmt_money(profit)}。",
        f"真实利润率：{_fmt_percent(margin)}。",
        f"累计已回款：{_fmt_money(kpi.get('collected_amount'))}。",
        f"整体回款率：{_fmt_percent(kpi.get('collection_rate'))}。",
        f"净现金流：{_fmt_money(kpi.get('net_cashflow'))}。",
        f"销项增值税：{_fmt_money(kpi.get('output_vat'))}。",
        f"进项增值税：{_fmt_money(kpi.get('input_vat'))}。",
        f"应交增值税：{_fmt_money(kpi.get('vat_payable'))}。",
        f"综合税负率：{_fmt_percent(kpi.get('tax_burden_rate'))}。",
    ]
    return "\n".join(lines), meta, True


def _ledger_evidence(
    db: Session,
    scope: Dict[str, Any],
) -> Tuple[str, Dict[str, Any], bool]:
    macro_text, meta, macro_has = _macro_facts_block(db, scope)
    invoice_rows = _fetch_relation_rows(db, "invoices", scope, limit=300)
    analytics_rows = _fetch_relation_rows(db, "analytics_tax", scope, limit=100)
    invoice_text, invoice_has = _invoice_ledger_block(invoice_rows)
    tax_text, tax_has = _analytics_tax_block(analytics_rows)

    return (
        "【第一层：权威经营底账】\n"
        + macro_text
        + "\n"
        + invoice_text
        + "\n"
        + tax_text,
        meta,
        bool(macro_has or invoice_has or tax_has),
    )


def _retrieval_project_ids(scope: Dict[str, Any]) -> List[int]:
    if scope["mode"] == "project" and scope.get("project"):
        value = scope["project"].get("id")
        return [int(value)] if value is not None else []
    return [int(p["id"]) for p in scope.get("projects", []) if p.get("id") is not None]


def _rag_evidence(
    db: Session,
    scope: Dict[str, Any],
    query: str,
) -> Tuple[str, List[Dict[str, Any]], bool]:
    merged: Dict[Any, Dict[str, Any]] = {}
    for project_id in _retrieval_project_ids(scope):
        try:
            result = retrieve(
                db=db,
                project_id=project_id,
                query=query,
                filters={},
                top_k=5,
                use_rerank=True,
            )
        except Exception:
            db.rollback()
            continue

        results = result.get("results", []) if isinstance(result, dict) else result
        for item in results or []:
            chunk_id = item.get("chunk_id")
            key = chunk_id if chunk_id is not None else (
                item.get("document_id"),
                item.get("heading_path"),
                item.get("text"),
            )
            existing = merged.get(key)
            current_score = _to_float(item.get("rerank_score"))
            if current_score is None:
                current_score = _to_float(item.get("score")) or 0.0
            existing_score = 0.0
            if existing:
                existing_score = _to_float(existing.get("rerank_score"))
                if existing_score is None:
                    existing_score = _to_float(existing.get("score")) or 0.0
            if existing is None or current_score > existing_score:
                merged[key] = dict(item)

    ranked = sorted(
        merged.values(),
        key=lambda item: (
            _to_float(item.get("rerank_score"))
            if _to_float(item.get("rerank_score")) is not None
            else (_to_float(item.get("score")) or 0.0)
        ),
        reverse=True,
    )[:5]

    if not ranked:
        return "【第二层：业务资料证据】\n未检索到与当前问题匹配的已解析资料片段。", [], False

    lines = ["【第二层：业务资料证据】"]
    for idx, item in enumerate(ranked, 1):
        text_value = str(item.get("text") or "").strip()
        if len(text_value) > 1200:
            text_value = text_value[:1200] + "…"
        title = item.get("filename") or item.get("document_code") or f"资料{idx}"
        heading = item.get("heading_path") or item.get("title_chain") or ""
        page = ""
        if item.get("page_start") is not None:
            page = f"，页码 {item.get('page_start')}"
            if item.get("page_end") not in (None, item.get("page_start")):
                page += f"-{item.get('page_end')}"
        lines.append(
            f"{idx}. 来源《{title}》"
            + (f"，位置 {heading}" if heading else "")
            + page
            + f"\n{text_value}"
        )
    return "\n".join(lines), ranked, True


def _raw_documents_evidence(
    db: Session,
    scope: Dict[str, Any],
    rag_results: List[Dict[str, Any]],
) -> Tuple[str, bool]:
    schema, columns = _relation_schema_and_columns(db, "documents")
    if not schema or not columns:
        return "【第三层：原始资料线索】\n原始资料库当前未检索到记录。", False

    document_ids = []
    for item in rag_results:
        doc_id = item.get("document_id")
        if doc_id is not None and doc_id not in document_ids:
            document_ids.append(doc_id)

    where_parts = []
    params: Dict[str, Any] = {"row_limit": 5}
    scope_where, scope_params = _scope_filter(columns, scope)
    if scope_where:
        where_parts.append(scope_where)
        params.update(scope_params)
    if document_ids and "id" in columns:
        bind_names = []
        for idx, value in enumerate(document_ids[:10]):
            key = f"doc_id_{idx}"
            params[key] = value
            bind_names.append(f":{key}")
        where_parts.append(f"{_qident('id')} IN ({', '.join(bind_names)})")

    select_fields = [
        field
        for field in (
            "id", "document_code", "filename", "document_type", "document_date",
            "contract_no", "invoice_no", "original_path", "parse_status",
            "version_status", "period",
        )
        if field in columns
    ]
    if not select_fields:
        return "【第三层：原始资料线索】\n原始资料库存在，但没有可安全展示的资料字段。", False

    sql = (
        "SELECT "
        + ", ".join(_qident(field) for field in select_fields)
        + f" FROM {_qident(schema)}.{_qident('documents')}"
    )
    if where_parts:
        sql += " WHERE " + " AND ".join(where_parts)
    if "updated_at" in columns:
        sql += f" ORDER BY {_qident('updated_at')} DESC"
    elif "id" in columns:
        sql += f" ORDER BY {_qident('id')} DESC"
    sql += " LIMIT :row_limit"

    try:
        rows = [dict(row) for row in db.execute(text(sql), params).mappings().all()]
    except Exception:
        db.rollback()
        rows = []

    # RAG 没命中具体文档时，仍按当前项目范围检查原始资料登记记录。
    if not rows and not document_ids:
        rows = _fetch_relation_rows(db, "documents", scope, limit=5)

    if not rows:
        return "【第三层：原始资料线索】\n未检索到与当前范围匹配的原始资料记录。", False

    lines = ["【第三层：原始资料线索】"]
    for idx, row in enumerate(rows[:5], 1):
        title = row.get("filename") or row.get("document_code") or f"原始资料{idx}"
        details = []
        for key, label in (
            ("document_type", "类型"),
            ("document_date", "日期"),
            ("period", "期间"),
            ("contract_no", "合同号"),
            ("invoice_no", "发票号"),
            ("parse_status", "解析状态"),
        ):
            if row.get(key):
                details.append(f"{label} {row.get(key)}")
        lines.append(f"{idx}. 《{title}》" + (f"（{'；'.join(details)}）" if details else ""))
    return "\n".join(lines), True


def _extract_facts_context(
    db: Session,
    scope: Dict[str, Any],
    query: str,
) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    ledger_text, meta, ledger_has = _ledger_evidence(db, scope)
    rag_text, rag_results, rag_has = _rag_evidence(db, scope, query)
    raw_text, raw_has = _raw_documents_evidence(db, scope, rag_results)

    context = "\n\n".join((ledger_text, rag_text, raw_text))
    evidence_state = {
        "ledger_has": ledger_has,
        "rag_has": rag_has,
        "raw_has": raw_has,
        "has_any": bool(ledger_has or rag_has or raw_has),
        "rag_results": rag_results,
    }
    meta["facts_available"] = bool(meta.get("facts_available") or ledger_has)
    return context, meta, evidence_state


PRIMARY_LLM_CONFIG = {
    "api_key": "sk-38lLhF00gYlbe2pfjlug9KE7DInnLNNMIOvcTye8JwRAhLkm",
    "base_url": "https://tokenhub.tencentmaas.com/v1",
    "model": "deepseek-v4-flash-202605",
    "timeout": 30.0,
}

FALLBACK_LLM_CONFIG = {
    "api_key": "none",
    "base_url": "http://127.0.0.1:8931/v1",
    "model": "Spark-X2.5-4B",
    "timeout": 15.0,
}

EXECUTIVE_SYSTEM_PROMPT = """你是“高管经营掌舵”经营智策助手，面向公司管理层提供【高管经营内参】。你的结论必须可追溯到系统提供的证据，不得凭经验补事实。

【证据优先级——必须严格执行】
1. 第一优先级“权威经营底账”：金额、税额、发票笔数、发票号码、销项/进项、应交增值税、收入、成本、利润、回款等具体经营数字，只能以该层已提供的真实记录为准。
2. 第二优先级“业务资料证据”：用于回答技术方案、合同条款、现场签证、合规依据、业务过程等；不得用它反推或覆盖第一层已有的财务底账数字。
3. 第三优先级“原始资料线索”：仅在前两层不足时用于确认资料是否存在、资料名称与原始凭证线索。
4. 发生冲突时严格按 第一层 > 第二层 > 第三层 处理。

【零幻觉与零推诿铁律】
- 证据没有给出的金额、税额、比例、发票号、日期、合同条款，不得估算、臆测、补写或按行业惯例生成。
- 如果三层都没有与问题相关的记录或凭证，明确回答：“暂无相关记录与凭证”。
- 严禁把系统未查到的事实推给任何部门或人员。不得出现“需财务提供”“让财务核算”“请联系财务”“向财务确认”“由财务进一步提供”等推诿表达。
- 可以指出“当前可核验资料中未找到某项记录”，但不得暗示系统外另有人应当补数据。
- 回答中不得暴露实现细节或内部技术名词，包括数据库表名、视图名、检索管线、向量、切片、Canonical Facts、project_id、analytics_* 等。

【输出风格】
- 先给结论，再列关键数字/证据，再给风险或管理建议。
- 保持通用高管经营内参视角，不硬编码具体职务。
- 控制在350~500字左右；若证据很少，宁可简短，也不要为了凑篇幅编造内容。
- 结尾给出3个紧贴当前证据的后续追问建议。
"""


_FORBIDDEN_DEFLECTIONS = (
    "需财务提供",
    "需要财务提供",
    "需财务核算",
    "需要财务核算",
    "请联系财务",
    "联系财务部门",
    "向财务确认",
    "财务进一步提供",
    "财务部门进一步提供",
)


def _ensure_followups(reply: str) -> str:
    if "接下来您可以直接问" in reply:
        return reply
    return (
        reply.rstrip()
        + "\n\n接下来您可以直接问：\n"
        "- 当前底账里有哪些可核验的关键数字？\n"
        "- 这项结论对应哪些业务资料证据？\n"
        "- 当前范围还缺哪些系统内记录？"
    )


def _sanitize_reply(reply: str, evidence_state: Dict[str, Any]) -> str:
    cleaned = (reply or "").strip()
    if not cleaned:
        cleaned = "暂无相关记录与凭证"

    for phrase in _FORBIDDEN_DEFLECTIONS:
        cleaned = cleaned.replace(phrase, "当前可核验资料中未找到对应记录")

    if not evidence_state.get("has_any"):
        cleaned = "暂无相关记录与凭证"

    return _ensure_followups(cleaned)


def _call_llm(config: Dict[str, Any], context: str, query: str) -> str:
    url = f"{config['base_url'].rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if config.get("api_key") and config["api_key"] != "none":
        headers["Authorization"] = f"Bearer {config['api_key']}"
    payload = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": EXECUTIVE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"{context}\n\n"
                    f"当前提问：{query}\n\n"
                    "请只依据以上三层证据回答；缺什么就明确说当前可核验资料中没有什么，绝不补写。"
                ),
            },
        ],
        "temperature": 0.1,
        "max_tokens": 2200,
    }
    response = httpx.post(url, headers=headers, json=payload, timeout=config["timeout"])
    if response.status_code != 200:
        return ""
    data = response.json()
    return str(data.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()


def _deterministic_fallback(
    context: str,
    evidence_state: Dict[str, Any],
) -> str:
    if not evidence_state.get("has_any"):
        return "暂无相关记录与凭证"

    # LLM 全部不可用时仍只返回已核验事实，不做任何推断。
    safe_context = context.replace("【第一层：权威经营底账】", "【已核验经营事实】")
    safe_context = safe_context.replace("【第二层：业务资料证据】", "【已核验业务资料】")
    safe_context = safe_context.replace("【第三层：原始资料线索】", "【原始资料线索】")
    return (
        "**【高管经营内参】**\n\n"
        "当前智能分析模型暂未返回结果，以下仅列示系统已核验的事实与资料，不作额外推断：\n\n"
        + safe_context
    )


def _generate_reply(
    query: str,
    context: str,
    evidence_state: Dict[str, Any],
) -> Tuple[str, str]:
    if not evidence_state.get("has_any"):
        return _sanitize_reply("暂无相关记录与凭证", evidence_state), "evidence_guard"

    try:
        reply = _call_llm(PRIMARY_LLM_CONFIG, context, query)
        if reply:
            return _sanitize_reply(reply, evidence_state), "deepseek-v4-flash"
    except Exception:
        pass

    try:
        reply = _call_llm(FALLBACK_LLM_CONFIG, context, query)
        if reply:
            return _sanitize_reply(reply, evidence_state), "spark-x2.5-4b"
    except Exception:
        pass

    return _sanitize_reply(_deterministic_fallback(context, evidence_state), evidence_state), "evidence_fallback"


def _citations_from_evidence(evidence_state: Dict[str, Any]) -> List[Dict[str, str]]:
    citations: List[Dict[str, str]] = [
        {"title": "集团经营总览", "url": "/ui/overview"},
        {"title": "项目经营明细", "url": "/ui/projects"},
    ]
    seen = set()
    for item in evidence_state.get("rag_results", []):
        title = str(item.get("filename") or "").strip()
        doc_id = item.get("document_id")
        if not title or title in seen:
            continue
        seen.add(title)
        url = f"/ui/documents?document_id={doc_id}" if doc_id is not None else "/ui/documents"
        citations.append({"title": title, "url": url})
        if len(citations) >= 7:
            break
    return citations


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
        evidence_text, meta_info, evidence_state = _extract_facts_context(db, scope, q)
        citations = _citations_from_evidence(evidence_state)

        meta = _meta_block(
            principal,
            facts_available=meta_info["facts_available"],
            facts_reason=meta_info["facts_reason"],
            as_of=meta_info["as_of"],
        )
        meta["project_id"] = meta_info.get("project_id")
        meta["project_name"] = meta_info.get("project_name")
        meta["evidence_layers"] = {
            "ledger": evidence_state["ledger_has"],
            "business_materials": evidence_state["rag_has"],
            "original_materials": evidence_state["raw_has"],
        }

        reply_text, data_source = _generate_reply(q, evidence_text, evidence_state)
        meta["data_source"] = data_source

        return {
            "status": "success",
            "query": q,
            "reply": reply_text,
            "citations": citations,
            "timestamp": int(time.time()),
            "project_id": meta_info.get("project_id"),
            "project_name": meta_info.get("project_name"),
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

    # 所有数据库读取在返回 StreamingResponse 前完成，避免生成器运行时 Session 已关闭。
    db = SessionLocal()
    try:
        scope = _resolve_chat_scope(db, q, req.project_id)
        evidence_text, meta_info, evidence_state = _extract_facts_context(db, scope, q)
        citations = _citations_from_evidence(evidence_state)
        meta_block = _meta_block(
            principal,
            facts_available=meta_info["facts_available"],
            facts_reason=meta_info["facts_reason"],
            as_of=meta_info["as_of"],
        )
        meta_block["project_id"] = meta_info.get("project_id")
        meta_block["project_name"] = meta_info.get("project_name")
        meta_block["evidence_layers"] = {
            "ledger": evidence_state["ledger_has"],
            "business_materials": evidence_state["rag_has"],
            "original_materials": evidence_state["raw_has"],
        }
    finally:
        db.close()

    # 先完成生成与确定性防推诿检查，再分块 SSE 输出；这样流式接口也遵守零幻觉门禁。
    reply_text, data_source = await asyncio.to_thread(
        _generate_reply,
        q,
        evidence_text,
        evidence_state,
    )
    meta_block["data_source"] = data_source

    async def event_source():
        chunk_size = 32
        for start in range(0, len(reply_text), chunk_size):
            chunk = reply_text[start:start + chunk_size]
            yield f"event: delta\ndata: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.02)
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
