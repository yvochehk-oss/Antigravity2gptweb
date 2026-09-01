#!/usr/bin/env python3
"""Gate S30 — Explicit Canonical FactRelationship & Four-Flow graph."""
from __future__ import annotations

import inspect
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.orm import Session

from app.cutover.finalization import finalize_v3_production_cutover
from app.cutover.writer import get_cutover_state
from app.integration.idp_canonical.fact_relationship_resolver import FactRelationshipResolver
from app.integration.idp_canonical.fact_relationship_rules import (
    INVOICE_CONTRACT_RELATION_V1,
    PAYMENT_CONTRACT_RELATION_V1,
    PAYMENT_INVOICE_RELATION_V1,
    TASK30_FACT_RELATIONSHIP_RULESET_V1,
)
from app.integration.idp_canonical.fact_relationship_schemas import FactRelationshipCompletionRequest, FactRelationshipEvidenceInput
from app.integration.idp_canonical.fact_relationship_service import FactRelationshipService
from app.integration.idp_canonical.production_route_guard import EXPECTED_ROUTE, ProductionRouteGuard
from app.v3_fact_models import Fact, FactRelationship, InvoiceFact
from app.v3_fact_relationship_evidence_models import FactRelationshipEvidence
from app.v3_party_models import SourceDocument
from scripts.v3.idp_direct_v3_gate_27_support import WriteAttemptMonitor, control_snapshot, database_url, disk_heads, legacy_counts

EXPECTED_HEAD = "98_v3_explicit_fact_relationship_graph"


def req(failures, evidence, key, ok, message):
    evidence[key] = bool(ok)
    if not ok:
        failures.append(message)


def table_count(session, table_name):
    exists = session.execute(text("SELECT to_regclass(:name)"), {"name": f"public.{table_name}"}).scalar_one()
    return int(session.execute(text(f'SELECT count(*) FROM "{table_name}"')).scalar_one()) if exists else None


def make_fact(session, token, suffix, fact_type):
    row = Fact(fact_type=fact_type, business_identity_key=f"S30|{fact_type}|{suffix}|{token}", version_no=1, is_current=True, supersedes_fact_id=None, validation_status="VALID" if fact_type != "CONTRACT" else "NEEDS_REVIEW")
    session.add(row); session.flush(); return row


def make_invoice(session, token, suffix):
    fact = make_fact(session, token, suffix, "INVOICE")
    identity = f"S30-INV-ID|{suffix}|{token}"
    session.add(InvoiceFact(fact_id=fact.id, invoice_identity_key=identity, invoice_identity_version="S30_V1", invoice_number=f"S30-INV-{suffix}-{token}", invoice_status="VALID", currency="CNY"))
    session.flush(); return fact, identity


def make_document(session, token, suffix):
    row = SourceDocument(source_system="GATE_S30", external_document_id=f"S30-DOC-{suffix}-{token}", filename=f"S30-{suffix}-{token}.pdf", mime_type="application/pdf", file_sha256=(suffix.lower().encode().hex()+token.lower()*8)[:64].ljust(64,"0"), document_type="contract", status="VALIDATED")
    session.add(row); session.flush(); return row


def snapshot(fact):
    return (fact.id, fact.fact_type, fact.business_identity_key, fact.version_no, fact.is_current, fact.supersedes_fact_id, fact.validation_status)


def relationship_request(*, source, relationship_type, document, extraction, reference, evidence_text="Gate S30 explicit reference evidence"):
    return FactRelationshipCompletionRequest(source_fact_id=source.id, relationship_type=relationship_type, source_document_id=document.id, source_extraction_id=extraction, submitted_by="gate:S30", evidences=[FactRelationshipEvidenceInput(evidence_text=evidence_text, page_no=1, confidence=0.99, **reference)])


def main():
    failures=[]
    evidence={"gate":"S30","alembic_head":EXPECTED_HEAD,"task30_ruleset_version":TASK30_FACT_RELATIONSHIP_RULESET_V1,"invoice_contract_semantic_version":INVOICE_CONTRACT_RELATION_V1,"payment_invoice_semantic_version":PAYMENT_INVOICE_RELATION_V1,"payment_contract_semantic_version":PAYMENT_CONTRACT_RELATION_V1,"automatic_fact_supersession_present":False,"automatic_relationship_replacement_present":False}
    engine=create_engine(database_url(),future=True,pool_pre_ping=True); monitor=WriteAttemptMonitor(); token=uuid4().hex[:10].upper(); listening=False
    try:
        with Session(engine) as session:
            outer=session.begin()
            try:
                db_heads=sorted(r[0] for r in session.execute(text("SELECT version_num FROM alembic_version_tax")).all()); disk=disk_heads(ROOT); evidence["alembic_db_heads"]=db_heads; evidence["alembic_disk_heads"]=disk
                req(failures,evidence,"alembic_head_is_98",db_heads==[EXPECTED_HEAD] and disk==[EXPECTED_HEAD],"Alembic head is not Task30 migration 98")
                state=get_cutover_state(session,for_update=True)
                if state.writer_mode=="SHADOW":
                    session.execute(text("UPDATE writer_cutover_states SET writer_mode='DUAL_WRITE', legacy_write_enabled=true, new_fact_write_enabled=true, legacy_frozen=false, new_fact_read_mode='SHADOW', rag_source='LEGACY', updated_by='gate:S30' WHERE scope='GLOBAL'")); session.flush()
                session.execute(text("UPDATE writer_cutover_states SET writer_mode='V3_PRIMARY', legacy_write_enabled=false, new_fact_write_enabled=true, legacy_frozen=true, updated_by='gate:S30' WHERE scope='GLOBAL' AND writer_mode='DUAL_WRITE'"))
                session.execute(text("UPDATE writer_cutover_states SET new_fact_read_mode='PRIMARY', rag_source='CANONICAL_FACTS', updated_by='gate:S30' WHERE scope='GLOBAL' AND writer_mode='V3_PRIMARY' AND new_fact_read_mode='SHADOW' AND rag_source='LEGACY'")); session.flush(); session.expire_all()
                if not session.execute(text("SELECT 1 FROM v3_cutover_finalizations WHERE scope='GLOBAL'")).scalar(): finalize_v3_production_cutover(session,actor="gate:S30",commit=False); session.expire_all()
                seal0,cutover0,legacy0=control_snapshot(session,"seal"),control_snapshot(session,"cutover"),legacy_counts(session)
                route=ProductionRouteGuard(session).require(scope="GLOBAL")
                req(failures,evidence,"production_route_guard_reused",all(getattr(route,k)==v for k,v in EXPECTED_ROUTE.items()),"Production route mismatch"); req(failures,evidence,"writer_mode_still_v3_primary",route.writer_mode=="V3_PRIMARY","writer not V3_PRIMARY"); req(failures,evidence,"legacy_write_still_disabled",route.legacy_write_enabled is False,"legacy enabled"); req(failures,evidence,"reader_mode_unchanged",route.new_fact_read_mode=="PRIMARY","reader changed"); req(failures,evidence,"rag_source_unchanged",route.rag_source=="CANONICAL_FACTS","RAG changed")
                constraint=session.execute(text("SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid WHERE t.relname='fact_relationships' AND c.conname='ck_fact_relationships_type'" )).scalar_one()
                req(failures,evidence,"relationship_type_check_extended",all(v in constraint for v in ("INVOICE_FOR_CONTRACT","PAYMENT_FOR_INVOICE","PAYMENT_FOR_CONTRACT")),"relationship CHECK not extended"); req(failures,evidence,"relationship_evidence_table_present",session.execute(text("SELECT to_regclass('public.fact_relationship_evidence')")).scalar_one() is not None,"evidence table missing")
                invoice1,invoice_id1=make_invoice(session,token,"I1"); invoice2,invoice_id2=make_invoice(session,token,"I2"); invoice3,_=make_invoice(session,token,"I3"); contract1=make_fact(session,token,"C1","CONTRACT"); contract2=make_fact(session,token,"C2","CONTRACT"); payment1=make_fact(session,token,"P1","PAYMENT"); payment2=make_fact(session,token,"P2","PAYMENT"); payment3=make_fact(session,token,"P3","PAYMENT"); payment4=make_fact(session,token,"P4","PAYMENT")
                tracked=[invoice1,invoice2,invoice3,contract1,contract2,payment1,payment2,payment3,payment4]; snapshots0={r.id:snapshot(r) for r in tracked}; docs={s:make_document(session,token,s) for s in ("A","B","C","D","E","F","G","H","J")}; session.flush()
                fact_count0=int(session.scalar(select(func.count(Fact.id))) or 0); project_count0=table_count(session,"projects"); allocation_count0=table_count(session,"fact_project_allocations"); replace_count0=int(session.scalar(select(func.count(FactRelationship.id)).where(FactRelationship.relationship_type.in_(["REPLACES","CORRECTS"]))) or 0)
                event.listen(engine,"before_cursor_execute",monitor.before_cursor_execute); listening=True; service=FactRelationshipService(session)
                r_ic=service.complete(relationship_request(source=invoice1,relationship_type="INVOICE_FOR_CONTRACT",document=docs["A"],extraction=f"S30-EX-A-{token}",reference={"target_fact_id":contract1.id}),commit=False); req(failures,evidence,"invoice_contract_explicit_relationship_supported",r_ic.outcome.value=="CREATED" and r_ic.target_fact_id==contract1.id,"Invoice->Contract failed"); req(failures,evidence,"canonical_fact_id_exact_resolution_supported",r_ic.target_fact_id==contract1.id,"target_fact_id exact failed")
                r_bi=service.complete(relationship_request(source=invoice2,relationship_type="INVOICE_FOR_CONTRACT",document=docs["B"],extraction=f"S30-EX-B-{token}",reference={"business_identity_key":contract2.business_identity_key}),commit=False); req(failures,evidence,"business_identity_exact_resolution_supported",r_bi.target_fact_id==contract2.id,"business identity exact failed")
                r_pi=service.complete(relationship_request(source=payment1,relationship_type="PAYMENT_FOR_INVOICE",document=docs["C"],extraction=f"S30-EX-C-{token}",reference={"invoice_identity_key":invoice_id1}),commit=False); req(failures,evidence,"payment_invoice_explicit_relationship_supported",r_pi.outcome.value=="CREATED" and r_pi.target_fact_id==invoice1.id,"Payment->Invoice failed"); req(failures,evidence,"invoice_identity_exact_resolution_supported",r_pi.target_fact_id==invoice1.id,"invoice identity exact failed")
                r_pc=service.complete(relationship_request(source=payment2,relationship_type="PAYMENT_FOR_CONTRACT",document=docs["D"],extraction=f"S30-EX-D-{token}",reference={"contract_business_identity_key":contract1.business_identity_key}),commit=False); req(failures,evidence,"payment_contract_explicit_relationship_supported",r_pc.outcome.value=="CREATED" and r_pc.target_fact_id==contract1.id,"Payment->Contract failed"); req(failures,evidence,"contract_business_identity_exact_resolution_supported",r_pc.target_fact_id==contract1.id,"contract identity exact failed")
                rel_before=int(session.scalar(select(func.count(FactRelationship.id))) or 0); ev_before=int(session.scalar(select(func.count(FactRelationshipEvidence.id))) or 0); retry=service.complete(relationship_request(source=invoice1,relationship_type="INVOICE_FOR_CONTRACT",document=docs["A"],extraction=f"S30-EX-A-{token}",reference={"target_fact_id":contract1.id}),commit=False); req(failures,evidence,"duplicate_relationship_retry_idempotent",retry.outcome.value=="NOOP" and int(session.scalar(select(func.count(FactRelationship.id))) or 0)==rel_before,"relationship retry not NOOP"); req(failures,evidence,"duplicate_evidence_retry_idempotent",int(session.scalar(select(func.count(FactRelationshipEvidence.id))) or 0)==ev_before,"evidence duplicated")
                rel0=int(session.scalar(select(func.count(FactRelationship.id))) or 0); ev0=int(session.scalar(select(func.count(FactRelationshipEvidence.id))) or 0); unknown=service.complete(relationship_request(source=invoice3,relationship_type="INVOICE_FOR_CONTRACT",document=docs["E"],extraction=f"S30-EX-E-{token}",reference={"target_fact_id":2147483000}),commit=False); req(failures,evidence,"unknown_target_needs_review",unknown.outcome.value=="NEEDS_REVIEW","unknown target not closed"); req(failures,evidence,"unknown_target_zero_write",rel0==int(session.scalar(select(func.count(FactRelationship.id))) or 0) and ev0==int(session.scalar(select(func.count(FactRelationshipEvidence.id))) or 0),"unknown target wrote")
                conflict_req=FactRelationshipCompletionRequest(source_fact_id=invoice3.id,relationship_type="INVOICE_FOR_CONTRACT",source_document_id=docs["F"].id,source_extraction_id=f"S30-EX-F-{token}",submitted_by="gate:S30",evidences=[FactRelationshipEvidenceInput(target_fact_id=contract1.id,evidence_text="明确引用合同一",page_no=1),FactRelationshipEvidenceInput(target_fact_id=contract2.id,evidence_text="同一提取事件引用另一合同",page_no=2)]); rel0=int(session.scalar(select(func.count(FactRelationship.id))) or 0); ev0=int(session.scalar(select(func.count(FactRelationshipEvidence.id))) or 0); conflict=service.complete(conflict_req,commit=False); req(failures,evidence,"conflicting_relationship_evidence_fail_closed",conflict.outcome.value=="NEEDS_REVIEW","conflict not closed"); req(failures,evidence,"conflicting_evidence_zero_write",rel0==int(session.scalar(select(func.count(FactRelationship.id))) or 0) and ev0==int(session.scalar(select(func.count(FactRelationshipEvidence.id))) or 0),"conflict wrote")
                wrong=service.complete(relationship_request(source=payment4,relationship_type="PAYMENT_FOR_INVOICE",document=docs["G"],extraction=f"S30-EX-G-{token}",reference={"target_fact_id":contract1.id}),commit=False); req(failures,evidence,"relationship_direction_enforced",wrong.outcome.value=="NEEDS_REVIEW","wrong direction accepted"); self_result=service.complete(relationship_request(source=payment4,relationship_type="PAYMENT_FOR_INVOICE",document=docs["H"],extraction=f"S30-EX-H-{token}",reference={"target_fact_id":payment4.id}),commit=False); req(failures,evidence,"self_relationship_rejected",self_result.outcome.value=="NEEDS_REVIEW","self loop accepted")
                event_doc=docs["J"]; event_ex=f"S30-EX-J-{token}"; first=service.complete(relationship_request(source=payment3,relationship_type="PAYMENT_FOR_INVOICE",document=event_doc,extraction=event_ex,reference={"invoice_identity_key":invoice_id1}),commit=False); rel1=int(session.scalar(select(func.count(FactRelationship.id))) or 0); ev1=int(session.scalar(select(func.count(FactRelationshipEvidence.id))) or 0); mutated=service.complete(relationship_request(source=payment3,relationship_type="PAYMENT_FOR_INVOICE",document=event_doc,extraction=event_ex,reference={"invoice_identity_key":invoice_id2}),commit=False); req(failures,evidence,"source_event_target_conflict_fail_closed",first.outcome.value=="CREATED" and mutated.outcome.value=="NEEDS_REVIEW","source event mutation not blocked"); req(failures,evidence,"source_event_conflict_zero_write",rel1==int(session.scalar(select(func.count(FactRelationship.id))) or 0) and ev1==int(session.scalar(select(func.count(FactRelationshipEvidence.id))) or 0),"source event conflict wrote")
                audit=session.scalar(select(FactRelationshipEvidence).where(FactRelationshipEvidence.relationship_id==r_ic.relationship_id)); req(failures,evidence,"relationship_evidence_auditable",audit is not None and audit.ruleset_version==TASK30_FACT_RELATIONSHIP_RULESET_V1,"audit missing"); req(failures,evidence,"source_document_fk_valid",audit is not None and isinstance(audit.source_document_id,int) and audit.source_document_id==docs["A"].id,"source doc FK invalid"); req(failures,evidence,"idp_uuid_not_used_as_source_document_fk",audit is not None and str(audit.source_document_id)!=audit.source_extraction_id,"IDP UUID used as FK")
                resolver_source=inspect.getsource(FactRelationshipResolver).lower(); schema_fields=set(FactRelationshipEvidenceInput.model_fields); req(failures,evidence,"project_commonality_not_used_for_fact_matching","project" not in schema_fields and "project_id" not in resolver_source,"project inference exposed"); req(failures,evidence,"party_commonality_not_used_for_fact_matching","party_id" not in resolver_source and "buyer" not in resolver_source and "payer" not in resolver_source,"party inference exposed"); req(failures,evidence,"amount_similarity_not_used","amount" not in resolver_source,"amount matching used"); req(failures,evidence,"date_proximity_not_used","date_proximity" not in resolver_source and "invoice_date" not in resolver_source,"date matching used"); req(failures,evidence,"fuzzy_reference_match_not_used",all(w not in resolver_source for w in ("ilike","similarity(","levenshtein","embedding","fuzzy")),"fuzzy matching used"); req(failures,evidence,"llm_relationship_inference_not_used","llm" not in resolver_source and "prompt" not in resolver_source,"LLM inference used")
                unchanged={r.id:snapshot(session.get(Fact,r.id)) for r in tracked}==snapshots0; req(failures,evidence,"fact_validation_status_unchanged",unchanged,"Fact status changed"); req(failures,evidence,"fact_identity_unchanged",unchanged,"Fact identity changed"); req(failures,evidence,"fact_version_unchanged",unchanged,"Fact version changed"); evidence["new_fact_created"]=int(session.scalar(select(func.count(Fact.id))) or 0)!=fact_count0; evidence["new_project_created"]=table_count(session,"projects")!=project_count0; req(failures,evidence,"task29_allocations_unchanged",table_count(session,"fact_project_allocations")==allocation_count0,"Task29 allocations changed"); evidence["automatic_fact_supersession_present"]=any(session.get(Fact,r.id).supersedes_fact_id is not None for r in tracked); replace_count1=int(session.scalar(select(func.count(FactRelationship.id)).where(FactRelationship.relationship_type.in_(["REPLACES","CORRECTS"]))) or 0); evidence["automatic_relationship_replacement_present"]=replace_count1!=replace_count0
                req(failures,evidence,"production_seal_unchanged",seal0==control_snapshot(session,"seal"),"seal changed"); req(failures,evidence,"cutover_state_unchanged",cutover0==control_snapshot(session,"cutover"),"cutover changed"); req(failures,evidence,"legacy_tables_unchanged",legacy0==legacy_counts(session),"legacy changed")
            finally:
                if outer.is_active: outer.rollback()
    except Exception as exc: failures.append(f"Gate S30 unexpected error: {type(exc).__name__}: {exc}")
    finally:
        if listening: event.remove(engine,"before_cursor_execute",monitor.before_cursor_execute)
        engine.dispose()
    evidence.update(monitor.flags)
    if evidence.get("new_fact_created"): failures.append("Task30 created Fact")
    if evidence.get("new_project_created"): failures.append("Task30 created Project")
    if evidence.get("automatic_fact_supersession_present"): failures.append("automatic Fact supersession detected")
    if evidence.get("automatic_relationship_replacement_present"): failures.append("automatic relationship replacement detected")
    if evidence.get("legacy_write_attempted"): failures.append("legacy write attempted")
    if evidence.get("production_seal_write_attempted"): failures.append("Production Seal write attempted")
    if evidence.get("cutover_state_write_attempted"): failures.append("cutover write attempted")
    print(json.dumps({"gate":"S30","status":"PASS" if not failures else "FAIL","evidence":evidence,"failures":failures},ensure_ascii=False,indent=2,sort_keys=True)); return 0 if not failures else 1

if __name__=="__main__": raise SystemExit(main())
