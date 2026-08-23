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

    # 2. 深入税务筹划与内部/外部关联主体分析
    scope = ctx.get("scope", "default")
    proj = ctx.get("project", {}) or {}
    p_code = proj.get("code", "")
    p_name = proj.get("name", "")

    if scope in ("tax", "whole_project", "default"):
        # 增值税进销项留抵退税筹划
        findings.append({
            "severity": "LOW",
            "area": "增值税进销项统筹",
            "issue": f"【{p_name}】全产业链税率级差与进销项留抵优化空间",
            "evidence": "施工总包(9%) ⇄ 内部物资集采(13%) ⇄ 劳务分包(3%简易) ⇄ 设备租赁(13%) 架构已建立",
            "impact": "存在进销项时点与留抵管理的核验空间；资金效果必须由确定性现金流与发票数据测算后确认",
        })
        recs.append({
            "priority": "P1",
            "action": "实施物资供应链集中开票与留抵退税专项申报",
            "reason": "利用物资商贸(B类)13%高进项税率与施工总包(9%)税率差，按期申请留抵退税加速资金周转",
            "owner": "集团税务总监 / 供应链财务部",
        })

        # 区域所得税优惠政策筹划
        if "GEM" in p_code or "GY" in p_code:
            findings.append({
                "severity": "LOW",
                "area": "所得税优惠筹划",
                "issue": f"【{p_name}】存在西部大开发等区域优惠资格核验候选",
                "evidence": "仅由项目地域触发候选；尚缺主体资格、鼓励类目录、主营收入占比、有效期及审核规则证据",
                "impact": "不得自动套用 15% 税率；资格证据完整并由税务规则引擎审核后才能测算影响",
            })
            recs.append({
                "priority": "P0",
                "action": "核验西部大开发优惠适用资格与证据链，通过规则审核后再决定是否申报",
                "reason": "锁定区域政策红利，合规合法降低跨区域分支机构与独立法人的所得税税负",
                "owner": "异地税务主管 / 财税合规部",
            })

        # 跨区域预缴与总分机构所得税三因素分摊
        if "CY" in p_code or "A04" in str(ctx):
            findings.append({
                "severity": "MEDIUM",
                "area": "跨区域异地施工预缴",
                "issue": "跨省跨区建筑服务 2% 就地预缴与企业所得税三因素法分摊核销",
                "evidence": "涉及跨省分支机构(A04)与总公司(A03)两地涉税报验",
                "impact": "需按期取得异地预缴完税凭证，避免川渝两地重复征税或纳税信用评级降级",
            })
            recs.append({
                "priority": "P1",
                "action": "通过川渝电子税务局跨省涉税协同通道办理就地预缴抵免确认",
                "reason": "严格落实跨区域涉税事项预缴反馈表，完成总分机构年度汇算清缴税额抵减",
                "owner": "主任会计师 / 异地工程财务组",
            })

    # 3. 外部合作主体穿透与四流一致性合规
    recs.append({
        "priority": "P1",
        "action": "对外部业主(EXT-*)与系统内26家系统内单位建立穿透式四流合一电子台账档案",
        "reason": "确保大宗材料过磅单、实名考勤、银行对公打款与专票四流严格吻合，筑牢税务合规底线",
        "owner": "风控审计部",
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