"""V0.2: Mock 审查器。"""
from __future__ import annotations

from typing import Any


def mock_review(ctx: dict[str, Any], reviewer_name: str = "") -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    recs: list[dict[str, Any]] = []
    gaps: list[str] = []

    matching = ctx.get("four_stream_matching", []) or []
    for row in matching:
        if row.get("status") != "正常":
            findings.append({
                "severity": "HIGH" if (
                    "无合同" in row["status"] or "付款未见发票" in row["status"]
                ) else "MEDIUM",
                "area": "四流匹配",
                "issue": row["status"],
                "evidence": (
                    f'{row["counterparty"]}/{row["category"]} 合同{row["contract"]} '
                    f'履约{row["fulfillment"]} 发票{row["invoice"]} 付款{row["paid"]}'
                ),
                "impact": "可能造成成本真实性、税务抵扣或付款控制风险",
            })

    for r in ctx.get("deterministic_risks", []) or []:
        findings.append({
            "severity": "HIGH" if r.get("severity") == "RED" else "MEDIUM",
            "area": "确定性风险",
            "issue": r.get("message", ""),
            "evidence": r.get("code", ""),
            "impact": "需要业务或财税复核",
        })

    calc = ctx.get("system_calculation", {}) or {}
    if float(calc.get("margin", 0) or 0) < 0:
        findings.append({
            "severity": "HIGH",
            "area": "项目盈亏",
            "issue": "项目当前经营利润为负",
            "evidence": f'利润率 {float(calc.get("margin", 0)):.2%}',
            "impact": "项目存在亏损风险",
        })

    if not findings:
        findings.append({
            "severity": "LOW",
            "area": ctx.get("scope_name", "项目"),
            "issue": "未发现明显规则异常",
            "evidence": "基于当前结构化数据和确定性检查",
            "impact": "仍需结合完整原始凭证复核",
        })

    recs.append({
        "priority": "P1",
        "action": "优先处理高等级四流和履约异常，并补齐证据链",
        "reason": "这是当前数据中最直接的可验证风险",
        "owner": "项目财务/商务",
    })

    if ctx.get("scope") in ("tax", "whole_project"):
        rules = ctx.get("tax_rule_review_status", []) or []
        if any(not x.get("reviewed") for x in rules):
            gaps.append("存在尚未专业复核的税务规则，AI不得据此给出正式申报结论")
            if "合规" in reviewer_name:
                findings.append({
                    "severity": "MEDIUM",
                    "area": "税务规则治理",
                    "issue": "存在尚未专业复核的税务规则",
                    "evidence": "tax_rule_review_status 中 reviewed=false",
                    "impact": "正式申报前必须由财税专业人员复核规则有效性",
                })
                recs.append({
                    "priority": "P1",
                    "action": "完成当前税务规则的专业复核和来源固化",
                    "reason": "避免经营测算规则被误用于正式申报",
                    "owner": "财务/税务负责人",
                })

    score = max(
        0,
        100
        - 20 * sum(1 for x in findings if x["severity"] == "HIGH")
        - 8 * sum(1 for x in findings if x["severity"] == "MEDIUM"),
    )
    level = (
        "HIGH" if any(x["severity"] == "HIGH" for x in findings)
        else "MEDIUM" if any(x["severity"] == "MEDIUM" for x in findings)
        else "LOW"
    )
    return {
        "risk_level": level,
        "score": score,
        "summary": (
            f'{ctx.get("scope_name", "项目")}检查完成，'
            f'共发现{len(findings)}项观察点。'
        ),
        "findings": findings,
        "recommendations": recs,
        "data_gaps": gaps,
    }