#!/usr/bin/env python3
"""Gate S31 — Canonical finance/tax + Native V3 RAG read path."""
from __future__ import annotations
from decimal import Decimal
import inspect, json
from pathlib import Path
import sys
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from sqlalchemy import create_engine,event,select,text
from sqlalchemy.orm import Session
import app.ai.context as rag_context
from app.cutover.reader import ReaderRoute,get_reader_route
from app.domain.finance.canonical_four_flow import CANONICAL_FOUR_FLOW_V1,CanonicalFourFlow
from app.domain.finance.canonical_project_finance import CANONICAL_PROJECT_FINANCE_V1,CanonicalProjectFinance
from app.domain.tax.canonical_project_tax import TASK31_CANONICAL_TAX_READ_V1,CanonicalProjectTax
from app.domain.tax.project_tax_analysis import RULESET_VERSION as TASK17_RULESET_VERSION
from app.integration.idp_canonical.production_route_guard import EXPECTED_ROUTE,ProductionRouteGuard
from app.v3_fact_models import Fact
from app.v3_period_models import CalculationRun,TaxPeriodState
from app.v3_project_analysis_models import ProjectTaxAnalysis
from scripts.v3.canonical_finance_rag_gate_31_support import ReadOnlyMonitor,fact_snapshot,make_fixture,table_count
from scripts.v3.idp_direct_v3_gate_27_support import control_snapshot,database_url,disk_heads,legacy_counts
EXPECTED_HEAD="98_v3_explicit_fact_relationship_graph"

def req(failures,evidence,key,ok,message):
    evidence[key]=bool(ok)
    if not ok: failures.append(message)

def main()->int:
    failures=[]; evidence={"gate":"S31","alembic_head":EXPECTED_HEAD,"task17_ruleset_version":TASK17_RULESET_VERSION,"task31_finance_version":CANONICAL_PROJECT_FINANCE_V1,"task31_four_flow_version":CANONICAL_FOUR_FLOW_V1,"task31_tax_adapter_version":TASK31_CANONICAL_TAX_READ_V1}
    engine=create_engine(database_url(),future=True,pool_pre_ping=True); monitor=ReadOnlyMonitor(); listening=False
    try:
        with Session(engine) as session:
            outer=session.begin()
            try:
                db_heads=sorted(r[0] for r in session.execute(text("SELECT version_num FROM alembic_version_tax")).all()); disk=disk_heads(ROOT); evidence["alembic_db_heads"]=db_heads; evidence["alembic_disk_heads"]=disk
                req(failures,evidence,"alembic_head_unchanged",db_heads==[EXPECTED_HEAD] and disk==[EXPECTED_HEAD],"Alembic head changed from 98")
                guard=ProductionRouteGuard(session).require(scope="GLOBAL"); route=get_reader_route(session)
                req(failures,evidence,"production_route_guard_reused",all(getattr(guard,k)==v for k,v in EXPECTED_ROUTE.items()),"Production route mismatch")
                req(failures,evidence,"writer_mode_still_v3_primary",guard.writer_mode=="V3_PRIMARY","writer mode changed"); req(failures,evidence,"legacy_write_still_disabled",guard.legacy_write_enabled is False,"legacy writes enabled"); req(failures,evidence,"reader_mode_primary",route.mode=="PRIMARY","reader not PRIMARY"); req(failures,evidence,"rag_source_canonical_facts",route.rag_source=="CANONICAL_FACTS","RAG not CANONICAL_FACTS")
                seal0,cutover0,legacy0=control_snapshot(session,"seal"),control_snapshot(session,"cutover"),legacy_counts(session)
                project,contract,invoice,payment,unlinked,review=make_fixture(session,uuid4().hex[:10].upper()); tracked=[contract.id,invoice.id,payment.id,unlinked.id,review.id]
                fact0=fact_snapshot(session,tracked); allocation0=table_count(session,"fact_project_allocations"); relationship0=table_count(session,"fact_relationships"); rel_ev0=table_count(session,"fact_relationship_evidence"); tax0=table_count(session,"project_tax_analysis"); project0=table_count(session,"projects"); facts0=table_count(session,"facts")
                event.listen(engine,"before_cursor_execute",monitor.before_cursor_execute); listening=True
                finance=CanonicalProjectFinance(session).read(project.id); expected={"allocated_contract_amount":"1000.00","allocated_invoice_net":"100.00","allocated_invoice_vat":"13.00","allocated_invoice_gross":"113.00","allocated_payment_amount":"100.00","contract_count":1,"invoice_count":1,"payment_count":2}
                req(failures,evidence,"canonical_finance_snapshot_completed",finance["summary"]==expected,"Canonical finance totals mismatch"); req(failures,evidence,"invoice_finance_uses_project_allocations",finance["summary"]["allocated_invoice_gross"]=="113.00","Invoice allocation not used"); req(failures,evidence,"contract_finance_uses_project_allocations",finance["summary"]["allocated_contract_amount"]=="1000.00","Contract allocation not used"); req(failures,evidence,"payment_finance_uses_project_allocations",finance["summary"]["allocated_payment_amount"]=="100.00","Payment allocation not used")
                req(failures,evidence,"valid_facts_only_in_financial_totals",finance["summary"]["invoice_count"]==1,"NEEDS_REVIEW leaked into totals"); req(failures,evidence,"confirmed_allocations_only_in_financial_totals",all(r["allocation_confidence"]=="HIGH" for r in [*finance["contracts"],*finance["invoices"],*finance["payments"]]),"Noncanonical allocation consumed"); req(failures,evidence,"needs_review_facts_excluded_from_totals",review.id not in finance["eligible_fact_ids"],"NEEDS_REVIEW included"); req(failures,evidence,"needs_review_facts_reported_as_quality_gap",any(r.get("code")=="FACT_NEEDS_REVIEW" and r.get("fact_id")==review.id for r in finance["evidence_quality"]),"Quality gap missing")
                graph=CanonicalFourFlow(session).read(project.id,eligible_fact_ids=finance["eligible_fact_ids"]); coverage=graph["coverage"]
                req(failures,evidence,"task29_project_allocations_reused",finance["eligible_fact_ids"]==sorted([contract.id,invoice.id,payment.id,unlinked.id]),"Task29 allocations not reused"); req(failures,evidence,"task30_fact_relationships_reused",len(graph["relationships"])==3,"Task30 relationships not reused"); req(failures,evidence,"invoice_contract_edges_consumed",coverage["invoice_contract_edge_count"]==1,"Invoice->Contract missing"); req(failures,evidence,"payment_invoice_edges_consumed",coverage["payment_invoice_edge_count"]==1,"Payment->Invoice missing"); req(failures,evidence,"payment_contract_edges_consumed",coverage["payment_contract_edge_count"]==1,"Payment->Contract missing"); req(failures,evidence,"relationship_coverage_reported",coverage["unlinked_invoice_fact_ids"]==[] and coverage["unlinked_payment_fact_ids"]==[unlinked.id],"Coverage incorrect"); req(failures,evidence,"unlinked_facts_reported_without_guessing",any(r.get("code")=="PAYMENT_WITHOUT_EXPLICIT_EDGE" and r.get("fact_id")==unlinked.id for r in graph["evidence_quality"]),"Unlinked fact gap missing"); req(failures,evidence,"invoice_payment_amount_not_inferred",graph["invoice_payment_amount_allocation"]["supported"] is False,"Payment amount inferred")
                payload=rag_context.build_context(session,project.id,"whole_project"); budget=rag_context.build_context(session,project.id,"budget")
                req(failures,evidence,"canonical_rag_context_native",payload.get("data_source")=="CANONICAL_FACTS" and payload.get("finance_summary",{}).get("allocated_invoice_gross")=="113.00","Native context failed"); req(failures,evidence,"canonical_rag_legacy_overlay_removed",budget.get("availability",{}).get("status")=="NOT_AVAILABLE_IN_CANONICAL_V3" and "budget" not in budget,"Legacy overlay remains")
                graph_src=inspect.getsource(CanonicalFourFlow).lower(); finance_src=inspect.getsource(CanonicalProjectFinance).lower()
                req(failures,evidence,"project_relationship_not_inferred","factprojectallocation" not in graph_src,"Project inferred relationship"); req(failures,evidence,"party_relationship_not_inferred",all(x not in graph_src for x in ("buyer_party","seller_party","payer_party","payee_party")),"Party inference detected"); req(failures,evidence,"amount_similarity_not_used",all(x not in graph_src for x in ("similarity","allocated_payment_amount","invoice_paid","outstanding")),"Amount inference detected"); req(failures,evidence,"date_proximity_not_used",all(x not in graph_src for x in ("invoice_date","transaction_date","date_proximity")),"Date inference detected"); req(failures,evidence,"llm_fact_inference_not_used",all(x not in finance_src+graph_src for x in ("llm","embedding","prompt")),"LLM inference detected")
                official=session.scalar(select(ProjectTaxAnalysis).join(CalculationRun,CalculationRun.id==ProjectTaxAnalysis.calculation_run_id).join(TaxPeriodState,TaxPeriodState.current_run_id==CalculationRun.id).where(CalculationRun.run_status=="SUCCEEDED",CalculationRun.tax_type=="PROJECT_TAX",CalculationRun.ruleset_version==TASK17_RULESET_VERSION).order_by(ProjectTaxAnalysis.id).limit(1)); req(failures,evidence,"task17_project_tax_rules_reused",TASK17_RULESET_VERSION=="V3_PROJECT_TAX_ANALYSIS_V1","Task17 ruleset not reused")
                if official is None: failures.append("Gate S31 requires one official Task17 ProjectTaxAnalysis pilot"); evidence["tax_output_matches_task17"]=False; evidence["tax_analysis_hash_stable"]=False
                else:
                    reader=CanonicalProjectTax(session); a=reader.read(official.project_id,reporting_party_id=official.reporting_party_id,tax_period=official.tax_period); b=reader.read(official.project_id,reporting_party_id=official.reporting_party_id,tax_period=official.tax_period); row=a["analyses"][0] if a["analyses"] else None
                    req(failures,evidence,"tax_output_matches_task17",row is not None and row["analysis_id"]==official.id and row["output_vat"]==str(Decimal(official.output_vat).quantize(Decimal("0.01"))) and row["result_hash_verified"] is True,"Tax read differs from Task17"); req(failures,evidence,"tax_analysis_hash_stable",a==b and row is not None and row["result_sha256"]==official.result_sha256,"Tax hash unstable")
                old_route,old_native,old_legacy=rag_context.get_reader_route,rag_context.build_canonical_context_native,rag_context._build_legacy_context; called={"legacy":False}
                try:
                    rag_context.get_reader_route=lambda db:ReaderRoute("PRIMARY","CANONICAL_FACTS"); rag_context.build_canonical_context_native=lambda *a,**k:(_ for _ in ()).throw(RuntimeError("S31 fail closed")); rag_context._build_legacy_context=lambda *a,**k:called.update(legacy=True)
                    try: rag_context.build_context(object(),1,"invoice")
                    except RuntimeError: pass
                finally: rag_context.get_reader_route,rag_context.build_canonical_context_native,rag_context._build_legacy_context=old_route,old_native,old_legacy
                req(failures,evidence,"canonical_read_failure_does_not_fallback_legacy",called["legacy"] is False,"Canonical failure fell back")
                req(failures,evidence,"fact_validation_status_unchanged",fact_snapshot(session,tracked)==fact0,"Fact state changed"); req(failures,evidence,"fact_identity_unchanged",fact_snapshot(session,tracked)==fact0,"Fact identity changed"); req(failures,evidence,"fact_version_unchanged",fact_snapshot(session,tracked)==fact0,"Fact version changed"); req(failures,evidence,"fact_project_allocations_unchanged",table_count(session,"fact_project_allocations")==allocation0,"Allocations changed"); req(failures,evidence,"fact_relationships_unchanged",table_count(session,"fact_relationships")==relationship0,"Relationships changed"); req(failures,evidence,"fact_relationship_evidence_unchanged",table_count(session,"fact_relationship_evidence")==rel_ev0,"Relationship evidence changed"); req(failures,evidence,"project_tax_analysis_unchanged",table_count(session,"project_tax_analysis")==tax0,"Tax analysis changed")
                evidence["new_fact_created"]=table_count(session,"facts")!=facts0; evidence["new_project_created"]=table_count(session,"projects")!=project0; evidence["automatic_fact_supersession_present"]=any(session.get(Fact,fid).supersedes_fact_id is not None for fid in tracked)
                if evidence["new_fact_created"]: failures.append("Task31 created Fact")
                if evidence["new_project_created"]: failures.append("Task31 created Project")
                if evidence["automatic_fact_supersession_present"]: failures.append("Task31 superseded Fact")
                if listening: event.remove(engine,"before_cursor_execute",monitor.before_cursor_execute); listening=False
                evidence["canonical_rag_legacy_business_read_attempted"]=monitor.legacy_business_read_attempted; evidence["legacy_write_attempted"]=monitor.legacy_write_attempted; evidence["production_seal_write_attempted"]=monitor.production_seal_write_attempted; evidence["cutover_state_write_attempted"]=monitor.cutover_state_write_attempted; evidence["task31_core_write_attempted"]=monitor.core_write_attempted
                for key,msg in (("canonical_rag_legacy_business_read_attempted","Canonical RAG read legacy"),("legacy_write_attempted","Legacy write attempted"),("production_seal_write_attempted","Seal write attempted"),("cutover_state_write_attempted","Cutover write attempted"),("task31_core_write_attempted","Core write attempted")):
                    if evidence[key]: failures.append(msg)
                req(failures,evidence,"production_seal_unchanged",seal0==control_snapshot(session,"seal"),"Seal changed"); req(failures,evidence,"cutover_state_unchanged",cutover0==control_snapshot(session,"cutover"),"Cutover changed"); req(failures,evidence,"legacy_tables_unchanged",legacy0==legacy_counts(session),"Legacy changed")
            finally:
                if listening: event.remove(engine,"before_cursor_execute",monitor.before_cursor_execute)
                outer.rollback()
    except Exception as exc: failures.append(f"Gate S31 exception: {type(exc).__name__}: {exc}")
    finally: engine.dispose()
    result={"gate":"S31","status":"PASS" if not failures else "FAIL","evidence":evidence,"failures":failures}; print(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True,default=str)); return 0 if not failures else 1
if __name__=="__main__": raise SystemExit(main())
