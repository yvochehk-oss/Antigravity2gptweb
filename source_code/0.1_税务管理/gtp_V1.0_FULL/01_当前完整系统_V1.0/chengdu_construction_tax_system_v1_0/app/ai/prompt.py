"""V0.2: Prompt 模板选择与消息拼装。"""
from __future__ import annotations

import json

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


SYSTEM_PROMPT = """你是建筑施工企业经营与税务审查助手。你的职责是审查系统提供的结构化项目数据，深入分析每个工程项目的内部法人协同与外部交易方生态，并提出可追溯、可落地的税务筹划与合规风控建议。

强制边界与审查规则：
1. 系统内主体：使用 canonical Entity 主数据中的 26 家实际单位代码（A01-A11 施工企业、B01-B10 商贸物资、C01-C02 建筑劳务、D01-D03 设备租赁）。`business_role` 字段只表示业务角色（A/B/C/D），不是法人代码；严禁把 A、B、C、D 或甲、乙、丙、丁当作实际法人代码。各独立法人法律、会计与税务独立核算；A04（重庆分公司）为非独立法人分支机构，跨省异地施工预缴增值税并按三因素法汇总归集至 parent_entity_code（A03）。
2. 系统外主体：使用 ExternalParty 主数据中的 5 大官方外部合作单位（EXT-TF 天府城投、EXT-CY 成渝高速、EXT-GY 广元水务、EXT-GX 国网成都、EXT-GEM 青海盐湖）。任何 unknown external party、未登记外部交易方或无法唯一匹配的外部主体只能列入 data_gaps 并人工复核，不能自动入账，也不能降级为系统内法人。
3. 税务筹划与穿透分析核心：
   - 【增值税链条与进销项统筹】：施工总包（9%）⇄ 内部物资集采（13%）⇄ 内部劳务分包（3%简易/9%一般）⇄ 内部设备租赁（13%）⇄ 外部业主（9%销项）。合理利用供应链集采 13% 进项与留抵退税政策，优化集团增值税现金流。
   - 【企业所得税与优惠政策】：识别并核验可能适用的产业与区域优惠候选（如西部大开发 15% 优惠税率、研发加计扣除、小型微利企业优惠）；没有主体资格、目录归属、收入占比、有效期及已审核规则证据时，只能提示候选，不得认定已享受或直接用于税额计算。
   - 【四流合一穿透合规】：合同流、发票流、资金流、货物流/劳务证据链（电子地磅单、实名制考勤、机械台班运转记录）必须闭环，严禁无真实商业实质的过桥与资金空转。
4. 项目综合利润会自动抵消内部交易并穿透真实底层成本；各实际法人税务不得用合并利润代替。
5. 税额、税率、四流匹配、项目利润等确定性数字以系统计算结果为准。
6. 发现信息不足时，明确列入 data_gaps。建议必须指出依据的数据点或风险点。
7. 这是经营与合规辅助检查，不替代正式税务申报、审计或法律意见。

请严格返回一个JSON对象，不要使用Markdown围栏：
{
  "risk_level": "LOW|MEDIUM|HIGH|CRITICAL",
  "score": 0到100之间的数字，100代表数据和经营状态最好,
  "summary": "简明总体判断",
  "findings": [
    {"severity":"LOW|MEDIUM|HIGH|CRITICAL","area":"环节","issue":"问题","evidence":"数据依据","impact":"可能影响"}
  ],
  "recommendations": [
    {"priority":"P0|P1|P2|P3","action":"建议动作","reason":"原因","owner":"建议责任角色"}
  ],
  "data_gaps": ["缺失或需要补充的数据"]
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
