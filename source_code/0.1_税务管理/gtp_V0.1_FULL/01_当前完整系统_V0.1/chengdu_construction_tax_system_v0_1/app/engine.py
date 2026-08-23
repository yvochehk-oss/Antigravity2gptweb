from collections import defaultdict
from sqlalchemy import select, func, delete
from .models import *

INTERNAL={"A","B","C","D"}
EXTERNAL={"甲","乙","丙","丁"}

def project_summary(db,pid):
    p=db.get(Project,pid)
    revenue=db.scalar(select(func.coalesce(func.sum(Progress.recognized_revenue),0)).where(Progress.project_id==pid)) or 0
    costs=db.execute(select(RealCost).where(RealCost.project_id==pid)).scalars().all()
    real=sum(x.amount for x in costs)
    external=sum(x.amount for x in costs if x.external_cash)
    inv=db.execute(select(Invoice).where(Invoice.project_id==pid)).scalars().all()
    outvat=sum(x.vat for x in inv if x.direction=="out")
    invat=sum(x.vat for x in inv if x.direction=="in" and x.deductible)
    vat=max(outvat-invat,0)
    profit=revenue-real
    progress=(revenue/p.contract_total) if p and p.contract_total else 0
    eac=(real/progress) if progress>0.05 else real
    eac=max(eac,real)
    return {"project":p,"revenue":revenue,"real_cost":real,"external_cash_cost":external,
            "profit":profit,"margin":profit/revenue if revenue else 0,"vat":vat,
            "progress":progress,"eac":eac,"eac_profit":(p.contract_total-eac if p else 0)}

def consolidated(db):
    ps=db.execute(select(Project)).scalars().all()
    rows=[project_summary(db,p.id) for p in ps]
    return {"rows":rows,"revenue":sum(x["revenue"] for x in rows),
            "cost":sum(x["real_cost"] for x in rows),
            "profit":sum(x["profit"] for x in rows),
            "vat":sum(x["vat"] for x in rows)}

def matching_rows(db,pid):
    contracts=db.execute(select(Contract).where(Contract.project_id==pid)).scalars().all()
    fulfill=db.execute(select(Fulfillment).where(Fulfillment.project_id==pid)).scalars().all()
    invoices=db.execute(select(Invoice).where(Invoice.project_id==pid, Invoice.direction=="in")).scalars().all()
    cash=db.execute(select(CashFlow).where(CashFlow.project_id==pid, CashFlow.direction=="out")).scalars().all()
    keys=set()
    csum=defaultdict(float); fsum=defaultdict(float); isum=defaultdict(float); psum=defaultdict(float)
    evidence=defaultdict(lambda: True)
    for c in contracts:
        cp=c.seller_code; key=(cp,c.category); keys.add(key); csum[key]+=c.amount
    for f in fulfill:
        key=(f.counterparty_code,f.category or _kind_to_cat(f.kind)); keys.add(key); fsum[key]+=f.amount
        evidence[key]=evidence[key] and bool(f.evidence_complete)
    for i in invoices:
        key=(i.counterparty_code,i.category); keys.add(key); isum[key]+=i.net+i.vat
    for x in cash:
        key=(x.counterparty_code,_note_category(x.note)); 
        # if note has no category, assign to any single existing cp category, else "unclassified"
        candidates=[k for k in keys if k[0]==x.counterparty_code]
        if key[1]=="unclassified" and len(candidates)==1: key=candidates[0]
        keys.add(key); psum[key]+=x.amount
    rows=[]
    for cp,cat in sorted(keys,key=lambda x:(x[0],x[1])):
        contract=csum[(cp,cat)]; fulfilled=fsum[(cp,cat)]; invoice=isum[(cp,cat)]; paid=psum[(cp,cat)]
        flags=[]
        if invoice and not contract: flags.append("无合同发票")
        if paid and not invoice: flags.append("付款未见发票")
        if fulfilled and not evidence[(cp,cat)]: flags.append("履约证据不完整")
        if contract and invoice>contract*1.05: flags.append("发票超过合同")
        if invoice and paid>invoice*1.05: flags.append("付款超过发票")
        if contract and fulfilled>contract*1.10: flags.append("履约结算超过合同")
        rows.append({"counterparty":cp,"category":cat,"contract":contract,"fulfillment":fulfilled,
                     "invoice":invoice,"paid":paid,"evidence_ok":evidence[(cp,cat)],
                     "status":"正常" if not flags else "；".join(flags)})
    return rows

def _kind_to_cat(kind):
    return {"material_acceptance":"material","labor_settlement":"labor","equipment_shift":"equipment"}.get(kind,"other")
def _note_category(note):
    for c in ("material","labor","equipment","subcontract","project_management"):
        if c in (note or ""): return c
    return "unclassified"

def rebuild_tax_ledger(db,period):
    """法人月度管理税务台账。
    项目综合利润与法人账面利润严格分开：
    - 项目综合利润：使用 RealCost 的真实底层成本；
    - 法人管理利润：使用本法人销项收入 - 本法人对外/对内采购发票净额 - 未被发票覆盖的直接成本。
    本函数仍是管理预测，不替代正式申报。
    """
    db.execute(delete(TaxLedger).where(TaxLedger.period==period))
    cit_rate=_rule_rate(db,"CIT_GENERAL",0.25)
    for code in sorted(INTERNAL):
        inv=db.execute(select(Invoice).where(Invoice.period==period,Invoice.entity_code==code)).scalars().all()
        outvat=sum(x.vat for x in inv if x.direction=="out")
        inputvat=sum(x.vat for x in inv if x.direction=="in" and x.deductible)
        revenue=sum(x.net for x in inv if x.direction=="out")
        invoice_cost=sum(x.net for x in inv if x.direction=="in")
        costs=db.execute(select(RealCost).where(RealCost.period==period,RealCost.entity_code==code)).scalars().all()
        # 有明确counterparty的A真实外部成本，通常已由A进项发票反映，避免重复；
        # 无counterparty的项目部/工资等直接成本，以及B/C/D底层成本作为本法人直接成本进入管理利润。
        direct_real=sum(x.amount for x in costs if (code!="A" or not x.counterparty_code))
        legal_cost=invoice_cost+direct_real
        profit=revenue-legal_cost
        ledger=TaxLedger(period=period,entity_code=code,output_vat=outvat,input_vat=inputvat,
                         vat_payable=max(outvat-inputvat,0),revenue=revenue,real_cost=legal_cost,
                         estimated_profit=profit,estimated_cit=max(profit,0)*cit_rate,generated=True)
        db.add(ledger)
    db.commit()
    return db.execute(select(TaxLedger).where(TaxLedger.period==period).order_by(TaxLedger.entity_code)).scalars().all()

def _rule_rate(db,code,default):
    r=db.scalar(select(TaxRule).where(TaxRule.code==code))
    return r.rate if r else default

def scan_risks(db,pid):
    db.execute(delete(RiskEvent).where(RiskEvent.project_id==pid))
    rows=matching_rows(db,pid)
    for r in rows:
        if r["status"]!="正常":
            sev="RED" if ("无合同发票" in r["status"] or "付款未见发票" in r["status"]) else "YELLOW"
            db.add(RiskEvent(project_id=pid,severity=sev,code="FOUR_STREAM_MISMATCH",
                             message=f'{r["counterparty"]}/{r["category"]}: {r["status"]}'))
    # generic tax/fulfillment risks
    invs=db.execute(select(Invoice).where(Invoice.project_id==pid)).scalars().all()
    for i in invs:
        if i.direction=="in" and i.vat>0 and i.rate<=0:
            db.add(RiskEvent(project_id=pid,severity="YELLOW",code="MISSING_TAX_RATE",
                             message=f"发票{i.invoice_no or i.id}存在VAT但未填写税率"))
        if i.category=="equipment" and i.rate not in (0.09,0.13,0.03,0):
            db.add(RiskEvent(project_id=pid,severity="YELLOW",code="EQUIPMENT_RATE_REVIEW",
                             message=f"设备业务发票{i.invoice_no or i.id}税率需复核"))
    db.commit()
    return db.execute(select(RiskEvent).where(RiskEvent.project_id==pid).order_by(RiskEvent.id.desc())).scalars().all()

def cost_tree_summary(db,pid):
    rows=db.execute(select(RealCost).where(RealCost.project_id==pid)).scalars().all()
    out=defaultdict(lambda: defaultdict(float))
    for x in rows: out[x.category][x.subcategory or "未细分"] += x.amount
    return out
