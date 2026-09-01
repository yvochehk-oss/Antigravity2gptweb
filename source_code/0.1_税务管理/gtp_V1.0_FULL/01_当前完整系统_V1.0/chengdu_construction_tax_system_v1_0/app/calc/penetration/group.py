"""Task18/19 canonical group penetration.

ACCRUAL eliminates internal operating transfers, TAX preserves legal-entity VAT,
and CASH uses canonical PaymentFact only. Legacy cashflows are never a fallback.
"""
from __future__ import annotations
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import hashlib, json
from typing import Any
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.calc.basis.accrual_basis import validate_accrual_source
from app.calc.basis.cash_basis import require_canonical_payment_fact, validate_cash_source
from app.calc.basis.contracts import SourceKind, SourceRole
from app.v3_period_models import CalculationRun, TaxPeriodState
from app.v3_vat_ledger_models import EntityVatLedger

RULESET_VERSION="V3_GROUP_PENETRATION_V2"; MONEY=Decimal("0.01"); MAX_DEPTH=32
class GroupPenetrationError(ValueError): pass
class GroupCycleDetected(GroupPenetrationError):
    def __init__(self, paths:list[str]): super().__init__("CYCLE_DETECTED: "+"; ".join(paths)); self.paths=tuple(paths)
def money(value:Any)->Decimal: return Decimal(str(value or 0)).quantize(MONEY,rounding=ROUND_HALF_UP)
def canonical_hash(payload:Any)->str:
    rendered=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str); return hashlib.sha256(rendered.encode()).hexdigest()
def month_start(value:date|str)->date:
    if isinstance(value,date): return date(value.year,value.month,1)
    rendered=str(value); parsed=date.fromisoformat(f"{rendered}-01" if len(rendered)==7 else rendered); return date(parsed.year,parsed.month,1)
def next_month(period:date)->date: return date(period.year+1,1,1) if period.month==12 else date(period.year,period.month+1,1)

ACCRUAL_RECURSIVE_SQL=text("""
WITH RECURSIVE roots AS (
 SELECT ff.fact_id root_fact_id,ff.fact_id edge_fact_id,ff.performing_party_id current_party_id,ff.receiving_party_id receiving_party_id,ff.amount,0::integer depth,ARRAY[ff.performing_party_id]::integer[] visited,ARRAY[ff.receiving_party_id,ff.performing_party_id]::integer[] path,false cycle_detected,'EXTERNAL_REVENUE'::text edge_kind
 FROM fulfillment_facts ff JOIN facts f ON f.id=ff.fact_id JOIN parties performer ON performer.id=ff.performing_party_id JOIN parties receiver ON receiver.id=ff.receiving_party_id
 WHERE f.is_current IS TRUE AND f.validation_status='VALID' AND ff.fulfillment_date>=:period_start AND ff.fulfillment_date<:period_end AND ff.amount IS NOT NULL AND performer.party_type='internal' AND receiver.party_type='external'
), walk AS (
 SELECT * FROM roots UNION ALL
 SELECT w.root_fact_id,up.fact_id,up.performing_party_id,up.receiving_party_id,up.amount,w.depth+1,w.visited||up.performing_party_id,w.path||up.performing_party_id,(up.performing_party_id=ANY(w.visited)),CASE WHEN upstream_party.party_type='external' THEN 'EXTERNAL_LEAF_COST' ELSE 'INTERNAL_ELIMINATION' END::text
 FROM walk w JOIN fulfillment_facts up ON up.receiving_party_id=w.current_party_id JOIN facts uf ON uf.id=up.fact_id JOIN parties upstream_party ON upstream_party.id=up.performing_party_id
 WHERE w.edge_kind<>'EXTERNAL_LEAF_COST' AND w.cycle_detected IS FALSE AND w.depth<:max_depth AND uf.is_current IS TRUE AND uf.validation_status='VALID' AND up.fulfillment_date>=:period_start AND up.fulfillment_date<:period_end AND up.amount IS NOT NULL
) SELECT root_fact_id,edge_fact_id,current_party_id,receiving_party_id,amount,depth,path,cycle_detected,edge_kind FROM walk ORDER BY root_fact_id,depth,edge_fact_id
""")

def accrual_snapshot(session:Session,*,period:date|str,max_depth:int=MAX_DEPTH)->dict[str,Any]:
    validate_accrual_source(SourceKind.FULFILLMENT_FACT,SourceRole.PRIMARY_AMOUNT); analysis_period=month_start(period)
    if max_depth<1: raise GroupPenetrationError("max_depth must be >= 1")
    rows=session.execute(ACCRUAL_RECURSIVE_SQL,{"period_start":analysis_period,"period_end":next_month(analysis_period),"max_depth":max_depth}).mappings().all(); cycles=[]; components_by_key={}; max_seen=0
    for row in rows:
        edge_kind=str(row["edge_kind"]); edge_fact_id=int(row["edge_fact_id"]); path="->".join(str(x) for x in row["path"]); depth=int(row["depth"]); max_seen=max(max_seen,depth)
        if row["cycle_detected"]: cycles.append(path); continue
        candidate={"component_type":edge_kind,"fulfillment_fact_id":edge_fact_id,"amount":str(money(row["amount"])),"depth":depth,"path":path,"root_fact_id":int(row["root_fact_id"])}; key=(edge_kind,edge_fact_id); previous=components_by_key.get(key)
        if previous is None or depth<int(previous["depth"]): components_by_key[key]=candidate
    if cycles: raise GroupCycleDetected(sorted(set(cycles)))
    return {"basis":"ACCRUAL","analysis_period":str(analysis_period),"max_depth":max_seen,"components":sorted(components_by_key.values(),key=lambda r:(r["component_type"],r["fulfillment_fact_id"],r["depth"]))}

def summarize_accrual(snapshot):
    c=snapshot["components"]; revenue=sum((money(r["amount"]) for r in c if r["component_type"]=="EXTERNAL_REVENUE"),Decimal("0")); cost=sum((money(r["amount"]) for r in c if r["component_type"]=="EXTERNAL_LEAF_COST"),Decimal("0")); eliminated=sum((money(r["amount"]) for r in c if r["component_type"]=="INTERNAL_ELIMINATION"),Decimal("0")); return {"external_revenue":money(revenue),"external_leaf_cost":money(cost),"internal_eliminated":money(eliminated),"group_gross_margin":money(revenue-cost)}

def tax_snapshot(session:Session,*,period:date|str)->dict[str,Any]:
    analysis_period=month_start(period)
    rows=session.execute(select(EntityVatLedger,CalculationRun).join(CalculationRun,CalculationRun.id==EntityVatLedger.calculation_run_id).join(TaxPeriodState,(TaxPeriodState.reporting_party_id==EntityVatLedger.reporting_party_id)&(TaxPeriodState.tax_type=="VAT")&(TaxPeriodState.tax_period==EntityVatLedger.tax_period)&(TaxPeriodState.current_run_id==EntityVatLedger.calculation_run_id)).where(EntityVatLedger.tax_period==analysis_period,CalculationRun.run_status=="SUCCEEDED",CalculationRun.tax_type=="VAT").order_by(EntityVatLedger.reporting_party_id,EntityVatLedger.id)).all()
    if not rows: raise GroupPenetrationError("no current official Entity VAT Ledgers exist for TAX group penetration")
    ledgers=[{"entity_vat_ledger_id":l.id,"reporting_party_id":l.reporting_party_id,"output_vat":str(money(l.output_vat)),"input_vat":str(money(l.input_vat)),"tax_prepayment":str(money(l.tax_prepayment)),"vat_payable_after_prepayment":str(money(l.vat_payable_after_prepayment))} for l,_ in rows]
    return {"basis":"TAX","analysis_period":str(analysis_period),"internal_elimination_applied":False,"ledgers":ledgers}
def summarize_tax(snapshot):
    l=snapshot["ledgers"]; return {"tax_output_vat":money(sum((money(r["output_vat"]) for r in l),Decimal("0"))),"tax_input_vat":money(sum((money(r["input_vat"]) for r in l),Decimal("0"))),"tax_prepayment":money(sum((money(r["tax_prepayment"]) for r in l),Decimal("0"))),"tax_payable_after_prepayment":money(sum((money(r["vat_payable_after_prepayment"]) for r in l),Decimal("0")))}

def cash_snapshot(session:Session,*,period:date|str)->dict[str,Any]:
    require_canonical_payment_fact(payment_fact_available=True); validate_cash_source(SourceKind.PAYMENT_FACT,SourceRole.PRIMARY_AMOUNT); analysis_period=month_start(period)
    rows=session.execute(text("""SELECT pf.fact_id,pf.amount,pf.transaction_date,payer.party_type payer_type,payee.party_type payee_type,pf.payer_party_id,pf.payee_party_id FROM payment_facts pf JOIN facts f ON f.id=pf.fact_id JOIN parties payer ON payer.id=pf.payer_party_id JOIN parties payee ON payee.id=pf.payee_party_id WHERE f.is_current IS TRUE AND f.validation_status='VALID' AND f.fact_type='PAYMENT' AND pf.transaction_date>=:period_start AND pf.transaction_date<:period_end ORDER BY pf.transaction_date,pf.fact_id"""),{"period_start":analysis_period,"period_end":next_month(analysis_period)}).mappings().all()
    components=[]
    for row in rows:
        if row["payer_type"]=="external" and row["payee_type"]=="internal": kind="CASH_INFLOW"
        elif row["payer_type"]=="internal" and row["payee_type"]=="external": kind="CASH_OUTFLOW"
        elif row["payer_type"]=="internal" and row["payee_type"]=="internal": kind="INTERNAL_CASH_ELIMINATION"
        else: continue
        components.append({"component_type":kind,"payment_fact_id":int(row["fact_id"]),"amount":str(money(row["amount"])),"depth":0,"path":f"{row['payer_party_id']}->{row['payee_party_id']}"})
    return {"basis":"CASH","analysis_period":str(analysis_period),"components":components}
def summarize_cash(snapshot):
    c=snapshot["components"]; inflow=sum((money(r["amount"]) for r in c if r["component_type"]=="CASH_INFLOW"),Decimal("0")); outflow=sum((money(r["amount"]) for r in c if r["component_type"]=="CASH_OUTFLOW"),Decimal("0")); return {"cash_inflow":money(inflow),"cash_outflow":money(outflow),"net_cash":money(inflow-outflow)}
def require_cash_penetration(): return require_canonical_payment_fact(payment_fact_available=True)
