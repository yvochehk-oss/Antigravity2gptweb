#!/usr/bin/env python3
"""Task18/19 canonical group penetration PLAN/APPLY builder."""
from __future__ import annotations
import argparse,json,os
from datetime import date,datetime,timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from sqlalchemy import create_engine,select,text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from app.calc.penetration.group import RULESET_VERSION,accrual_snapshot,canonical_hash,cash_snapshot,month_start,summarize_accrual,summarize_cash,summarize_tax,tax_snapshot
from app.v3_group_models import GroupPenetrationComponent,GroupPenetrationResult
from app.v3_party_models import InternalEntity
from app.v3_period_models import CalculationRun
EXPECTED_HEAD="90_v3_payment_facts"; PLAN_KIND="V3_TASK18_GROUP_PENETRATION_PLAN"; RUN_TYPE={"ACCRUAL":"GROUP_ACCRUAL","TAX":"GROUP_TAX","CASH":"GROUP_CASH"}
def _database_url():
    v=os.getenv("DATABASE_URL","").strip()
    if not v:raise SystemExit("DATABASE_URL is required")
    if make_url(v).get_backend_name() not in {"postgresql","postgres"}:raise SystemExit("Group Penetration is PostgreSQL-only")
    return v
def _head(s):return str(s.connection().exec_driver_sql("SELECT version_num FROM alembic_version_tax").scalar_one())
def _anchor(session,code):
    row=session.scalar(select(InternalEntity).where(InternalEntity.canonical_code==code))
    if row is None:raise ValueError(f"unknown internal entity: {code}")
    if not row.legal_entity:raise ValueError("group analysis anchor must be a legal internal entity")
    return row
def _result(basis,snapshot):
    if basis=="ACCRUAL": values=summarize_accrual(snapshot); return {"basis":basis,"status":"READY","cycle_detected":False,"max_depth":int(snapshot["max_depth"]),**{k:str(v) for k,v in values.items()}}
    if basis=="TAX": values=summarize_tax(snapshot); return {"basis":basis,"status":"READY","cycle_detected":False,"max_depth":0,**{k:str(v) for k,v in values.items()}}
    values=summarize_cash(snapshot); return {"basis":basis,"status":"READY","cycle_detected":False,"max_depth":0,**{k:str(v) for k,v in values.items()}}
def make_plan(session,*,anchor_entity,period,basis):
    if _head(session)!=EXPECTED_HEAD:raise ValueError(f"formal DB head must be {EXPECTED_HEAD}")
    basis=basis.strip().upper()
    if basis not in RUN_TYPE:raise ValueError("basis must be ACCRUAL, TAX or CASH")
    anchor=_anchor(session,anchor_entity); p=month_start(period); snapshot=accrual_snapshot(session,period=p) if basis=="ACCRUAL" else tax_snapshot(session,period=p) if basis=="TAX" else cash_snapshot(session,period=p); result=_result(basis,snapshot); ih=canonical_hash(snapshot); payload={"anchor_party_id":anchor.party_id,"analysis_period":str(p),"input_snapshot_sha256":ih,**result}; rh=canonical_hash(payload); core={"kind":PLAN_KIND,"version":2,"anchor_entity":anchor_entity,"anchor_party_id":anchor.party_id,"analysis_period":str(p),"basis":basis,"run_type":RUN_TYPE[basis],"ruleset_version":RULESET_VERSION,"status":"READY","input_snapshot_sha256":ih,"result_sha256":rh,"source_snapshot":snapshot,"result":result};return {**core,"plan_digest":canonical_hash(core)}
def _prior(session,anchor,period,run_type):return session.scalar(select(CalculationRun).where(CalculationRun.reporting_party_id==anchor,CalculationRun.tax_type==run_type,CalculationRun.tax_period==period,CalculationRun.run_status=="SUCCEEDED").order_by(CalculationRun.id.desc()).limit(1))
def _expected(basis,snapshot):
    if basis=="ACCRUAL":return {(r["component_type"],r["fulfillment_fact_id"],None,None) for r in snapshot["components"]}
    if basis=="TAX":return {("ENTITY_VAT_LEDGER",None,r["entity_vat_ledger_id"],None) for r in snapshot["ledgers"]}
    return {(r["component_type"],None,None,r["payment_fact_id"]) for r in snapshot["components"]}
def _actual(session,result_id):return {(r.component_type,r.fulfillment_fact_id,r.entity_vat_ledger_id,r.payment_fact_id) for r in session.scalars(select(GroupPenetrationComponent).where(GroupPenetrationComponent.result_id==result_id)).all()}
def build_one(session,*,anchor_entity,period,basis,created_by,expected_input_snapshot_sha256=None):
    if _head(session)!=EXPECTED_HEAD:raise ValueError(f"formal DB head must be {EXPECTED_HEAD}")
    basis=basis.strip().upper();anchor=_anchor(session,anchor_entity);p=month_start(period);session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:scope))"),{"scope":f"GROUP_PENETRATION:{basis}:{anchor.party_id}:{p.isoformat()}"});plan=make_plan(session,anchor_entity=anchor_entity,period=p,basis=basis);ih=plan["input_snapshot_sha256"]
    if expected_input_snapshot_sha256 is not None and expected_input_snapshot_sha256!=ih:raise ValueError("stale group penetration plan: source snapshot changed after PLAN review")
    rh=plan["result_sha256"];run_type=plan["run_type"];prior=_prior(session,anchor.party_id,p,run_type)
    if prior and prior.ruleset_version==RULESET_VERSION and prior.input_snapshot_sha256==ih and prior.result_sha256==rh:
        existing=session.scalar(select(GroupPenetrationResult).where(GroupPenetrationResult.calculation_run_id==prior.id))
        if existing and existing.result_sha256==rh and _actual(session,existing.id)==_expected(basis,plan["source_snapshot"]):return {"status":"NO_CHANGE","calculation_run_id":prior.id,"result_id":existing.id,"basis":basis,"analysis_period":str(p),"input_snapshot_sha256":ih}
    run=CalculationRun(reporting_party_id=anchor.party_id,tax_type=run_type,tax_period=p,run_kind="RESTATEMENT" if prior else "STANDARD",run_status="SUCCEEDED",ruleset_version=RULESET_VERSION,input_snapshot_sha256=ih,result_sha256=rh,supersedes_run_id=prior.id if prior else None,created_by=created_by,note=f"Group Penetration {basis}",completed_at=datetime.now(timezone.utc));session.add(run);session.flush();v=plan["result"];kw={"calculation_run_id":run.id,"anchor_party_id":anchor.party_id,"analysis_period":p,"basis":basis,"status":"READY","cycle_detected":False,"max_depth":int(v["max_depth"]),"input_snapshot_sha256":ih,"result_sha256":rh}
    if basis=="ACCRUAL":kw.update(external_revenue=Decimal(v["external_revenue"]),external_leaf_cost=Decimal(v["external_leaf_cost"]),internal_eliminated=Decimal(v["internal_eliminated"]),group_gross_margin=Decimal(v["group_gross_margin"]))
    elif basis=="TAX":kw.update(tax_output_vat=Decimal(v["tax_output_vat"]),tax_input_vat=Decimal(v["tax_input_vat"]),tax_prepayment=Decimal(v["tax_prepayment"]),tax_payable_after_prepayment=Decimal(v["tax_payable_after_prepayment"]))
    else:kw.update(cash_inflow=Decimal(v["cash_inflow"]),cash_outflow=Decimal(v["cash_outflow"]),net_cash=Decimal(v["net_cash"]))
    result=GroupPenetrationResult(**kw);session.add(result);session.flush();snap=plan["source_snapshot"]
    if basis=="ACCRUAL":
        for r in snap["components"]:session.add(GroupPenetrationComponent(result_id=result.id,component_type=r["component_type"],amount=Decimal(r["amount"]),fulfillment_fact_id=r["fulfillment_fact_id"],depth=int(r["depth"]),path=r["path"]))
    elif basis=="TAX":
        for r in snap["ledgers"]:session.add(GroupPenetrationComponent(result_id=result.id,component_type="ENTITY_VAT_LEDGER",amount=Decimal(r["vat_payable_after_prepayment"]),entity_vat_ledger_id=r["entity_vat_ledger_id"],depth=0,path=f"reporting_party:{r['reporting_party_id']}"))
    else:
        for r in snap["components"]:session.add(GroupPenetrationComponent(result_id=result.id,component_type=r["component_type"],amount=Decimal(r["amount"]),payment_fact_id=r["payment_fact_id"],depth=0,path=r["path"]))
    session.flush();return {"status":"BUILT","calculation_run_id":run.id,"result_id":result.id,"basis":basis,"analysis_period":str(p),"input_snapshot_sha256":ih,"result_sha256":rh}
def _write(path,payload):
    rendered=json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True);Path(path).write_text(rendered+"\n",encoding="utf-8") if path else print(rendered)
def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest="command",required=True);p=sub.add_parser("plan");p.add_argument("--anchor-entity",required=True);p.add_argument("--period",required=True);p.add_argument("--basis",required=True,choices=["ACCRUAL","TAX","CASH"]);p.add_argument("--json");a=sub.add_parser("apply");a.add_argument("--anchor-entity",required=True);a.add_argument("--period",required=True);a.add_argument("--basis",required=True,choices=["ACCRUAL","TAX","CASH"]);a.add_argument("--created-by",required=True);a.add_argument("--expected-input-sha256");a.add_argument("--confirm-database",required=True);a.add_argument("--json");args=parser.parse_args();engine=create_engine(_database_url(),future=True,pool_pre_ping=True)
    with Session(engine) as session:
        if args.command=="plan":payload=make_plan(session,anchor_entity=args.anchor_entity,period=args.period,basis=args.basis)
        else:
            db=session.connection().exec_driver_sql("SELECT current_database()").scalar_one()
            if db!=args.confirm_database:raise SystemExit(f"--confirm-database mismatch: expected {db!r}")
            payload=build_one(session,anchor_entity=args.anchor_entity,period=args.period,basis=args.basis,created_by=args.created_by,expected_input_snapshot_sha256=args.expected_input_sha256);session.commit()
        _write(args.json,payload)
    return 0
if __name__=="__main__":raise SystemExit(main())
