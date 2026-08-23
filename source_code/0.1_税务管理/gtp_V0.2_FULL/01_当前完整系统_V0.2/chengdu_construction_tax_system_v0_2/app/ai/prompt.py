"""V0.2: Prompt 模板选择与消息拼装。"""
from __future__ import annotations

import json
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..constants import SCOPES
from ..models import AIPromptTemplate


def get_prompt_template(
    db: Session,
    scope: str,
    template_id: Optional[int] = None,
) -> Optional[AIPromptTemplate]:
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


SYSTEM_PROMPT = """你是建筑施工企业经营与税务审查助手。你的职责是审查系统提供的结构化项目数据，并提出可追溯、可执行的建议。

强制边界：
1. 系统内：A施工、B劳务、C商贸、D设备租赁。四个法人法律、会计、税务独立。
2. 系统外：甲第三方劳务、乙第三方设备、丙外部材料、丁外部专业分包/其他，只作为外部交易对手。
3. 项目综合利润会抵消ABCD内部交易并穿透真实底层成本；法人税务不得用合并利润代替。
4. 税额、税率、四流匹配、项目利润等确定性数字以系统计算结果为准。不得自行修改税率、创造数字或假设不存在的业务。
5. 不得建议虚构劳务、虚构采购、虚假设备租赁、购买发票、资金空转、倒签合同或其他无真实业务安排。
6. 发现信息不足时，明确列入 data_gaps，不要猜。
7. 建议必须指出依据的数据点或风险点。
8. 这是经营与合规辅助检查，不替代正式税务申报、审计或法律意见。

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
    ctx: dict, user_instruction: str, template: Optional[AIPromptTemplate],
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