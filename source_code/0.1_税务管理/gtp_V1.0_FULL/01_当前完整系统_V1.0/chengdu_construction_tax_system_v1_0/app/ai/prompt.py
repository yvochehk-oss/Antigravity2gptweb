"""V0.2: Prompt 模板选择与消息拼装。"""
from __future__ import annotations

import json
import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AIPromptTemplate


def get_prompt_template(
    db: Session,
    scope: str,
    template_id: int | None = None,
) -> AIPromptTemplate | None:
    if template_id:
        x = db.get(AIPromptTemplate, template_id)
        if x and x.enabled:
            return x
    x = db.scalar(
        select(AIPromptTemplate)
        .where(AIPromptTemplate.scope == scope, AIPromptTemplate.enabled == True)  # noqa: E712
        .order_by(AIPromptTemplate.version.desc(), AIPromptTemplate.id.desc())
    )
    if x:
        return x
    return db.scalar(
        select(AIPromptTemplate)
        .where(AIPromptTemplate.scope == "default", AIPromptTemplate.enabled == True)  # noqa: E712
        .order_by(AIPromptTemplate.version.desc(), AIPromptTemplate.id.desc())
    )


def validate_response_score(payload: dict[str, Any]) -> bool:
    """Return whether an AI review score satisfies the strict 0-100 contract."""
    raw_score = payload.get("score")
    if isinstance(raw_score, bool):
        return False
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        return False
    return math.isfinite(score) and 0 <= score <= 100


SYSTEM_PROMPT = """你是建筑施工企业经营与税务审查资深专家。你的职责是审查系统提供的结构化工程项目数据，深入分析项目内部法人协同、外部交易方生态与财税合规性，并提出可追溯、可落地的税务筹划与风控建议。

强制业务理解与审查边界：
1. 【建筑施工行业跨期建造会计准则】：
   - 在建工程采用建造合同完工百分比法按施工进度（如当前进度 58.6%）阶段性确认收入（recognized_revenue）；
   - 主材、设备及专业分包前置采购支出（real_cost）在项目建设前中期先期集中归集，后续随工程进度结算与业主回款逐步结转释放；
   - 严禁将工程在建阶段的阶段性资金垫付或收入与成本的进度差额简单粗暴判定为企业实际破产或不可逆巨额亏损，应结合已批复总造价（contract_total）与完工预测（EAC）进行理性经营研判。
2. 【增值税供应链进销项统筹与留抵机制】：
   - 施工总包（9%）⇄ 内部物资集采（13%）⇄ 内部劳务分包（3%简易/9%一般）⇄ 内部设备租赁（13%）⇄ 外部业主（9%销项）；
   - 建筑项目在主体施工前中期因集中采购大宗建材（钢材/商砼 13%）及机械租赁形成阶段性进项税额留抵，是集团增值税资金池运作的正常行业特征，应指导合理合规申请留抵退税或留待后续销项抵扣。
3. 【四流合一穿透合规与证据链审查】：
   - 审查合同流、发票流、资金流与履约业务流（地磅单、实名制考勤、台班记录）的一致性，指出证据缺口；
   - 识别西部大开发（15%优惠税率）等税收优惠适用条件与证明材料要求。
4. 【法人主体与外部客商边界】：
   - A/B/C/D 仅代表业务角色（business_role），不是法人代码；
   - 系统内 26 家法人独立核算（A01-A11 施工总包、B01-B10 物资商贸、C01-C02 建筑劳务、D01-D03 设备租赁）；
   - 外部交易方独立记录于 ExternalParty 主数据中，外部未建档单位标识为 unknown external party；
   - A08 为总包法人，A04 为分公司分支机构，异地施工按规定预缴增值税并汇总归集。
5. 【健康评分标准】：
   - 评分满分 100 分。对于总预算批复明确、四流凭证基本齐备但处于在建阶段的项目，给出合理的健康度评分（如 70~85 分），并重点聚焦于具体可落地的风控管理动作；
   - 确定性数字以系统计算结果为准。

请严格返回一个JSON对象，不要使用Markdown围栏：
{
  "risk_level": "LOW|MEDIUM|HIGH|CRITICAL",
  "score": 0到100之间的整数或浮点数,
  "summary": "专业、客观、兼具行业特性的综合审查结论",
  "findings": [
    {"severity":"LOW|MEDIUM|HIGH|CRITICAL","area":"环节","issue":"核心问题","evidence":"数据依据","impact":"潜在影响"}
  ],
  "recommendations": [
    {"priority":"P0|P1|P2|P3","action":"管理行动","reason":"业务依据","owner":"责任部门"}
  ],
  "data_gaps": ["建议补充的支撑材料或证据链"]
}
"""


def build_messages(
    ctx: dict, user_instruction: str, template: AIPromptTemplate | None,
) -> list[dict[str, str]]:
    payload = json.dumps(ctx, ensure_ascii=False, indent=2, default=str)
    addendum = (template.system_addendum if template else "")
    focus = (template.review_focus if template else "")
    system = SYSTEM_PROMPT + (
        "\n\n本次审查模板补充要求：\n" + addendum if addendum else ""
    )
    user = (
        f"检查范围：{ctx.get('scope_name')}\n"
        f"用户特别要求：{user_instruction or '无额外要求'}\n"
        f"模板重点：{focus or '按通用规则全面检查'}\n\n"
        f"以下是系统生成的结构化检查包。确定性数字以此为准：\n{payload}\n\n"
        "请按系统要求返回严格JSON。"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
