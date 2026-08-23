import os, json, hashlib, re
from datetime import datetime, timezone
from typing import Any
import httpx
from sqlalchemy import select
from .models import *
from .engine import project_summary, matching_rows, scan_risks, cost_tree_summary, rebuild_tax_ledger

INTERNAL = {"A","B","C","D"}
EXTERNAL = {"甲","乙","丙","丁"}

SCOPES = {
    "overview": "项目总体经营",
    "budget": "预算与成本偏差",
    "contract": "合同",
    "fulfillment": "履约证据",
    "invoice": "发票",
    "cashflow": "资金与付款",
    "cost": "真实成本穿透",
    "tax": "税务管理测算",
    "eac": "EAC预计完工",
    "risk": "风险事件",
    "material": "材料",
    "labor": "劳务",
    "equipment": "设备",
    "subcontract": "专业分包",
    "whole_project": "整个项目",
}

CATEGORY_SCOPE = {
    "material": "material",
    "labor": "labor",
    "equipment": "equipment",
    "subcontract": "subcontract",
}

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

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def _serialize(rows, fields):
    out=[]
    for x in rows:
        out.append({f:getattr(x,f,None) for f in fields})
    return out

def _tax_periods(db, pid):
    periods=set()
    for x in db.execute(select(Invoice).where(Invoice.project_id==pid)).scalars().all():
        if x.period: periods.add(x.period)
    for x in db.execute(select(Progress).where(Progress.project_id==pid)).scalars().all():
        if x.period: periods.add(x.period)
    return sorted(periods)

def build_context(db, pid:int, scope:str) -> dict[str,Any]:
    p=db.get(Project,pid)
    if not p:
        raise ValueError("项目不存在")
    summary=project_summary(db,pid)
    base={
        "project":{"id":p.id,"code":p.code,"name":p.name,"city":p.city,"contract_total":p.contract_total,"tax_method":p.tax_method},
        "system_calculation":{
            "recognized_revenue":summary["revenue"],
            "real_cost":summary["real_cost"],
            "profit":summary["profit"],
            "margin":summary["margin"],
            "vat_management_estimate":summary["vat"],
            "progress_ratio":summary["progress"],
            "eac_cost":summary["eac"],
            "eac_profit":summary["eac_profit"],
        },
        "scope": scope,
        "scope_name": SCOPES.get(scope, scope)
    }

    budgets=db.execute(select(Budget).where(Budget.project_id==pid)).scalars().all()
    contracts=db.execute(select(Contract).where(Contract.project_id==pid)).scalars().all()
    fulfills=db.execute(select(Fulfillment).where(Fulfillment.project_id==pid)).scalars().all()
    invoices=db.execute(select(Invoice).where(Invoice.project_id==pid)).scalars().all()
    cash=db.execute(select(CashFlow).where(CashFlow.project_id==pid)).scalars().all()
    costs=db.execute(select(RealCost).where(RealCost.project_id==pid)).scalars().all()
    progress=db.execute(select(Progress).where(Progress.project_id==pid)).scalars().all()

    cat=CATEGORY_SCOPE.get(scope)
    if cat:
        contracts=[x for x in contracts if x.category==cat]
        fulfills=[x for x in fulfills if x.category==cat]
        invoices=[x for x in invoices if x.category==cat]
        costs=[x for x in costs if x.category==cat]
        base["contracts"]=_serialize(contracts,["contract_no","buyer_code","seller_code","category","amount","internal_trade","note"])
        base["fulfillment"]=_serialize(fulfills,["counterparty_code","kind","category","quantity","amount","evidence_complete","note"])
        base["invoices"]=_serialize(invoices,["invoice_no","period","entity_code","direction","counterparty_code","category","net","vat","rate","deductible","note"])
        base["real_costs"]=_serialize(costs,["entity_code","counterparty_code","category","subcategory","period","amount","external_cash","note"])
        base["four_stream_matching"]=[x for x in matching_rows(db,pid) if x["category"]==cat]
        return base

    if scope in ("overview","whole_project","budget","eac"):
        base["budget"]=_serialize(budgets,["category","amount"])
        base["progress"]=_serialize(progress,["period","output_value","settlement","recognized_revenue","collection"])
    if scope in ("contract","whole_project"):
        base["contracts"]=_serialize(contracts,["contract_no","buyer_code","seller_code","category","amount","internal_trade","note"])
        base["four_stream_matching"]=matching_rows(db,pid)
    if scope in ("fulfillment","whole_project"):
        base["fulfillment"]=_serialize(fulfills,["counterparty_code","kind","category","quantity","amount","evidence_complete","note"])
        base["four_stream_matching"]=matching_rows(db,pid)
    if scope in ("invoice","whole_project"):
        base["invoices"]=_serialize(invoices,["invoice_no","period","entity_code","direction","counterparty_code","category","net","vat","rate","deductible","note"])
        base["four_stream_matching"]=matching_rows(db,pid)
    if scope in ("cashflow","whole_project"):
        base["cashflows"]=_serialize(cash,["entity_code","counterparty_code","direction","amount","period","note"])
        base["four_stream_matching"]=matching_rows(db,pid)
    if scope in ("cost","whole_project"):
        base["real_costs"]=_serialize(costs,["entity_code","counterparty_code","category","subcategory","period","amount","external_cash","note"])
        tree=cost_tree_summary(db,pid)
        base["cost_tree"]={k:dict(v) for k,v in tree.items()}
    if scope in ("tax","whole_project"):
        ledgers=[]
        for period in _tax_periods(db,pid):
            # 当前Demo税务台账按法人+月份全系统汇总；标明这是管理口径
            rows=rebuild_tax_ledger(db,period)
            ledgers += _serialize(rows,["period","entity_code","output_vat","input_vat","vat_payable","revenue","real_cost","estimated_profit","estimated_cit"])
        rules=db.execute(select(TaxRule)).scalars().all()
        base["tax_ledgers"]=ledgers
        base["tax_rule_review_status"]=_serialize(rules,["code","rate","effective_from","effective_to","reviewed","note"])
    if scope in ("risk","whole_project"):
        risks=scan_risks(db,pid)
        base["deterministic_risks"]=_serialize(risks,["severity","code","message","resolved"])
    if scope=="eac":
        base["eac_note"]="EAC为系统确定性管理预测；模型只能解释和提出需要复核的假设，不得覆盖系统计算。"

    # 整项目限制体积：Demo按最近/前若干条，生产版应分段并生成摘要
    if scope=="whole_project":
        for key in ("contracts","fulfillment","invoices","cashflows","real_costs"):
            if key in base and len(base[key])>80:
                base[key]=base[key][:80]
                base[f"{key}_truncated"]=True
    return base

def sanitize_context(ctx:dict) -> dict:
    """当前模型表中不存身份证/银行账号等字段；仍保留统一脱敏入口，正式版在这里做字段级策略。"""
    blocked_tokens=("身份证","银行卡号","银行账号","手机号","联系电话","联系人电话")
    def clean(v):
        if isinstance(v,dict):
            return {k:("[REDACTED]" if any(t in k for t in blocked_tokens) else clean(val)) for k,val in v.items()}
        if isinstance(v,list): return [clean(x) for x in v]
        return v
    return clean(ctx)

def get_prompt_template(db, scope:str, template_id:int|None=None):
    if template_id:
        x=db.get(AIPromptTemplate,template_id)
        if x and x.enabled: return x
    x=db.scalar(select(AIPromptTemplate).where(AIPromptTemplate.scope==scope,AIPromptTemplate.enabled==True).order_by(AIPromptTemplate.version.desc(),AIPromptTemplate.id.desc()))
    if x: return x
    return db.scalar(select(AIPromptTemplate).where(AIPromptTemplate.scope=="default",AIPromptTemplate.enabled==True).order_by(AIPromptTemplate.version.desc(),AIPromptTemplate.id.desc()))

def build_messages(ctx:dict,user_instruction:str, template=None):
    payload=json.dumps(sanitize_context(ctx),ensure_ascii=False,indent=2)
    addendum=(template.system_addendum if template else "")
    focus=(template.review_focus if template else "")
    system=SYSTEM_PROMPT + ("\n\n本次审查模板补充要求：\n"+addendum if addendum else "")
    user=f"""检查范围：{ctx.get("scope_name")}
用户特别要求：{user_instruction or "无额外要求"}
模板重点：{focus or "按通用规则全面检查"}

以下是系统生成的结构化检查包。确定性数字以此为准：
{payload}

请按系统要求返回严格JSON。"""
    return [{"role":"system","content":system},{"role":"user","content":user}]

def _parse_json(text:str) -> dict:
    text=(text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        m=re.search(r'\{.*\}',text,re.S)
        if m:
            try: return json.loads(m.group(0))
            except Exception: pass
    return {
        "risk_level":"UNKNOWN","score":0,
        "summary":"模型返回内容不是有效JSON，已保留原始响应供人工查看。",
        "findings":[{"severity":"MEDIUM","area":"AI响应","issue":"非结构化返回","evidence":"模型未按约定返回JSON","impact":"自动解析失败"}],
        "recommendations":[{"priority":"P2","action":"调整模型提示或更换兼容模型后重新检查","reason":"需要结构化结果","owner":"系统管理员"}],
        "data_gaps":[]
    }

def call_endpoint(endpoint:AIModelEndpoint,messages:list[dict],ctx:dict) -> tuple[dict,str]:
    if endpoint.adapter=="mock":
        result=mock_review(ctx, endpoint.name)
        return result, json.dumps(result,ensure_ascii=False)
    if endpoint.adapter!="openai_compatible":
        raise ValueError(f"暂不支持适配器: {endpoint.adapter}")
    key=os.getenv(endpoint.api_key_env,"") if endpoint.api_key_env else ""
    if endpoint.api_key_env and not key:
        raise RuntimeError(f"环境变量 {endpoint.api_key_env} 未设置")
    if not endpoint.base_url:
        raise RuntimeError("未配置 base_url")
    url=endpoint.base_url.rstrip("/") + "/" + endpoint.chat_path.lstrip("/")
    headers={"Content-Type":"application/json"}
    if key: headers["Authorization"]=f"Bearer {key}"
    body={"model":endpoint.model,"messages":messages,"temperature":0.1}
    with httpx.Client(timeout=endpoint.timeout_seconds) as client:
        resp=client.post(url,headers=headers,json=body)
        resp.raise_for_status()
        data=resp.json()
    text=data["choices"][0]["message"]["content"]
    return _parse_json(text), text

def mock_review(ctx:dict, reviewer_name:str="") -> dict:
    findings=[]; recs=[]; gaps=[]
    matching=ctx.get("four_stream_matching",[])
    for row in matching:
        if row.get("status")!="正常":
            findings.append({"severity":"HIGH" if "无合同" in row["status"] or "付款未见发票" in row["status"] else "MEDIUM",
                "area":"四流匹配","issue":row["status"],
                "evidence":f'{row["counterparty"]}/{row["category"]} 合同{row["contract"]} 履约{row["fulfillment"]} 发票{row["invoice"]} 付款{row["paid"]}',
                "impact":"可能造成成本真实性、税务抵扣或付款控制风险"})
    for r in ctx.get("deterministic_risks",[]):
        findings.append({"severity":"HIGH" if r["severity"]=="RED" else "MEDIUM","area":"确定性风险","issue":r["message"],"evidence":r["code"],"impact":"需要业务或财税复核"})
    calc=ctx.get("system_calculation",{})
    if calc.get("margin",0)<0:
        findings.append({"severity":"HIGH","area":"项目盈亏","issue":"项目当前经营利润为负","evidence":f'利润率 {calc.get("margin",0):.2%}',"impact":"项目存在亏损风险"})
    if not findings:
        findings.append({"severity":"LOW","area":ctx.get("scope_name","项目"),"issue":"未发现明显规则异常","evidence":"基于当前结构化数据和确定性检查","impact":"仍需结合完整原始凭证复核"})
    recs.append({"priority":"P1","action":"优先处理高等级四流和履约异常，并补齐证据链","reason":"这是当前数据中最直接的可验证风险","owner":"项目财务/商务"})
    if ctx.get("scope") in ("tax","whole_project"):
        rules=ctx.get("tax_rule_review_status",[])
        if any(not x.get("reviewed") for x in rules):
            gaps.append("存在尚未专业复核的税务规则，AI不得据此给出正式申报结论")
            if "合规" in reviewer_name:
                findings.append({"severity":"MEDIUM","area":"税务规则治理","issue":"存在尚未专业复核的税务规则","evidence":"tax_rule_review_status 中 reviewed=false","impact":"正式申报前必须由财税专业人员复核规则有效性"})
                recs.append({"priority":"P1","action":"完成当前税务规则的专业复核和来源固化","reason":"避免经营测算规则被误用于正式申报","owner":"财务/税务负责人"})
    score=max(0,100-20*sum(1 for x in findings if x["severity"]=="HIGH")-8*sum(1 for x in findings if x["severity"]=="MEDIUM"))
    level="HIGH" if any(x["severity"]=="HIGH" for x in findings) else ("MEDIUM" if any(x["severity"]=="MEDIUM" for x in findings) else "LOW")
    return {"risk_level":level,"score":score,"summary":f'{ctx.get("scope_name","项目")}检查完成，共发现{len(findings)}项观察点。',"findings":findings,"recommendations":recs,"data_gaps":gaps}

def run_review(db, job:AIReviewJob) -> AIReviewResult:
    endpoint=db.get(AIModelEndpoint,job.endpoint_id)
    if not endpoint or not endpoint.enabled:
        raise RuntimeError("模型端点不存在或已禁用")
    job.status="running"; job.started_at=now_iso(); db.commit()
    ctx=build_context(db,job.project_id,job.scope)
    safe_ctx=sanitize_context(ctx)
    serialized=json.dumps(safe_ctx,ensure_ascii=False,sort_keys=True)
    job.input_digest=hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    job.input_preview=serialized[:12000]
    template=get_prompt_template(db,job.scope,job.prompt_template_id)
    if template and not job.prompt_template_id:
        job.prompt_template_id=template.id
    messages=build_messages(safe_ctx,job.user_instruction,template)
    try:
        parsed,raw=call_endpoint(endpoint,messages,safe_ctx)
        result=AIReviewResult(
            job_id=job.id,provider_name=endpoint.name,model_name=endpoint.model or endpoint.adapter,
            risk_level=str(parsed.get("risk_level","UNKNOWN")),
            score=float(parsed.get("score",0) or 0),
            summary=str(parsed.get("summary","")),
            findings_json=json.dumps(parsed.get("findings",[]),ensure_ascii=False),
            recommendations_json=json.dumps(parsed.get("recommendations",[]),ensure_ascii=False),
            data_gaps_json=json.dumps(parsed.get("data_gaps",[]),ensure_ascii=False),
            raw_response=raw[:100000],
        )
        db.add(result)
        job.status="completed"; job.finished_at=now_iso(); db.commit()
        return result
    except Exception as e:
        job.status="failed"; job.error_message=str(e); job.finished_at=now_iso(); db.commit()
        raise
