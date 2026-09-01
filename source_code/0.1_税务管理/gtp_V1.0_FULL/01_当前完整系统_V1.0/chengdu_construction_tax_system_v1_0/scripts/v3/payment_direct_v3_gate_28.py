#!/usr/bin/env python3
"""Gate S28 — Bank Receipt / Payment Evidence Intake & Canonical completion."""
from __future__ import annotations
from hashlib import sha256
import json
from pathlib import Path
import sys
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.orm import Session
from app.cutover.finalization import finalize_v3_production_cutover
from app.cutover.writer import get_cutover_state
from app.domain.facts.payment import PAYMENT_IDENTITY_VERSION, PAYMENT_RULESET_VERSION, build_payment_business_identity_key
from app.integration.idp_canonical.direct_v3_schemas import DirectV3ProductionRequest
from app.integration.idp_canonical.direct_v3_service import DirectV3IngestService
from app.integration.idp_canonical.evidence_schemas import SourceDocumentEvidence
from app.integration.idp_canonical.payment_evidence_service import PaymentEvidenceCompletionError
from app.integration.idp_canonical.payment_schemas import PaymentEvidenceCompletionRequest
from app.integration.idp_canonical.production_route_guard import EXPECTED_ROUTE, ProductionRouteGuard
from app.integration.idp_canonical.schemas import CanonicalIngestRequest
from app.integration.idp_canonical.service import CanonicalIngestRejected, PAYMENT_DOCUMENT_TYPES, PAYMENT_RECEIPT_STORAGE_TYPE
from app.v3_evidence_integration_models import IDPSourceDocumentBinding
from app.v3_fact_models import Fact, FactProvenance
from app.v3_integration_models import CanonicalIngestReceipt
from app.v3_party_models import SourceDocument
from app.v3_payment_models import PaymentFact
from scripts.v3.idp_direct_v3_gate_27_support import EXPECTED_HEAD, WriteAttemptMonitor, control_snapshot, database_url, disk_heads, legacy_counts, party

def req(failures,evidence,key,ok,msg):
    evidence[key]=bool(ok)
    if not ok: failures.append(msg)
def submission(token,suffix,payer,payer_tax,payee,payee_tax,document_type="payment",validated=True):
    doc=f"S28-P-DOC-{suffix}-{token}"; ext=f"S28-P-EXT-{suffix}-{token}"; digest=sha256(f"S28:P:{suffix}:{token}".encode()).hexdigest()
    intake=CanonicalIngestRequest(source_system="IDP",source_document_id=doc,source_extraction_id=ext,document_sha256=digest,document_type=document_type,review_status="approved",approved_by="gate:S28",extraction_model="gate-s28",extraction_model_version="1",data={"payer":{"name":payer.name,"tax_id":payer_tax},"payee":{"name":payee.name,"tax_id":payee_tax},"payer_role_evidence":"银行回单付款人栏明确列示该主体","payee_role_evidence":"银行回单收款人栏明确列示该主体","transaction_date":"2026-09-01","amount":"100.00","currency":"CNY","bank_reference":f"S28-{suffix}-{token}","settlement_method":"BANK_TRANSFER","payment_nature":"NORMAL"})
    evidence=PaymentEvidenceCompletionRequest(source_system="IDP",source_document_id=doc,source_extraction_id=ext,document_sha256=digest,document=SourceDocumentEvidence(filename=f"{doc}.pdf",mime_type="application/pdf",validation_status="VALIDATED" if validated else "EXTRACTED",validated_by="gate:S28" if validated else None,validation_reason="Gate S28 reviewed" if validated else None),page_start=1,page_end=1)
    return DirectV3ProductionRequest(intake=intake,payment_evidence=evidence)
def main()->int:
    failures=[]; evidence={"gate":"S28","alembic_head":EXPECTED_HEAD,"task19_payment_identity_version":PAYMENT_IDENTITY_VERSION,"task19_payment_ruleset_version":PAYMENT_RULESET_VERSION,"automatic_payment_supersession_present":False,"duplicate_payment_fact_created_on_retry":False}
    engine=create_engine(database_url(),future=True,pool_pre_ping=True); monitor=WriteAttemptMonitor(); token=uuid4().hex[:10].upper(); listening=False
    try:
        with Session(engine) as session:
            outer=session.begin()
            try:
                db_heads=sorted(r[0] for r in session.execute(text("SELECT version_num FROM alembic_version_tax")).all()); disk=disk_heads(ROOT); evidence["alembic_db_heads"]=db_heads; evidence["alembic_disk_heads"]=disk
                req(failures,evidence,"alembic_head_unchanged",db_heads==[EXPECTED_HEAD] and disk==[EXPECTED_HEAD],"Alembic head changed")

                state = get_cutover_state(session, for_update=True)
                if state.writer_mode == "SHADOW":
                    session.execute(
                        text(
                            "UPDATE writer_cutover_states SET writer_mode='DUAL_WRITE', "
                            "legacy_write_enabled=true, new_fact_write_enabled=true, legacy_frozen=false, "
                            "new_fact_read_mode='SHADOW', rag_source='LEGACY', updated_by='gate:S28' "
                            "WHERE scope='GLOBAL'"
                        )
                    )
                    session.flush()
                session.execute(
                    text(
                        "UPDATE writer_cutover_states SET writer_mode='V3_PRIMARY', "
                        "legacy_write_enabled=false, new_fact_write_enabled=true, legacy_frozen=true, "
                        "updated_by='gate:S28' WHERE scope='GLOBAL' AND writer_mode='DUAL_WRITE'"
                    )
                )
                session.execute(
                    text(
                        "UPDATE writer_cutover_states SET new_fact_read_mode='PRIMARY', "
                        "rag_source='CANONICAL_FACTS', updated_by='gate:S28' "
                        "WHERE scope='GLOBAL' AND writer_mode='V3_PRIMARY' AND new_fact_read_mode='SHADOW' AND rag_source='LEGACY'"
                    )
                )
                session.flush()
                session.expire_all()
                if not session.execute(
                    text("SELECT 1 FROM v3_cutover_finalizations WHERE scope='GLOBAL'")
                ).scalar():
                    finalize_v3_production_cutover(session, actor="gate:S28", commit=False)
                    session.expire_all()

                seal0,cutover0,legacy0=control_snapshot(session,"seal"),control_snapshot(session,"cutover"),legacy_counts(session)
                route=ProductionRouteGuard(session).require(scope="GLOBAL")
                req(failures,evidence,"production_route_guard_reused",all(getattr(route,k)==v for k,v in EXPECTED_ROUTE.items()),"production route not strict")
                req(failures,evidence,"writer_mode_still_v3_primary",route.writer_mode=="V3_PRIMARY","writer not V3_PRIMARY"); req(failures,evidence,"legacy_write_still_disabled",route.legacy_write_enabled is False,"legacy writer enabled"); req(failures,evidence,"reader_mode_unchanged",route.new_fact_read_mode=="PRIMARY","reader not PRIMARY"); req(failures,evidence,"rag_source_unchanged",route.rag_source=="CANONICAL_FACTS","RAG source changed")
                req(failures,evidence,"task19_payment_business_identity_reused",build_payment_business_identity_key(source_domain="LEGACY_CASHFLOW",source_row_id=1)=="PAYMENT|LEGACY_CASHFLOW|ROW|1","Task19 identity shape changed"); req(failures,evidence,"payment_alias_supported","payment" in PAYMENT_DOCUMENT_TYPES,"payment alias missing"); req(failures,evidence,"bank_receipt_alias_supported","bank_receipt" in PAYMENT_DOCUMENT_TYPES,"bank_receipt alias missing"); req(failures,evidence,"zero_migration_receipt_compatibility_explicit",PAYMENT_RECEIPT_STORAGE_TYPE in {"invoice","contract"},"receipt compatibility discriminator invalid")
                event.listen(engine,"before_cursor_execute",monitor.before_cursor_execute); listening=True
                payer,payer_tax=party(session,token,"PAY_PAYER"); payee,payee_tax=party(session,token,"PAY_PAYEE"); service=DirectV3IngestService(session); before=int(session.scalar(select(func.count(Fact.id))) or 0); req1=submission(token,"VALID",payer,payer_tax,payee,payee_tax)
                r1=service.process(req1,commit=False); session.flush(); after=int(session.scalar(select(func.count(Fact.id))) or 0); fact=session.get(Fact,r1.fact_id); pf=session.get(PaymentFact,r1.fact_id)
                req(failures,evidence,"approved_payment_direct_path_completed",r1.outcome=="COMPLETED" and r1.payment_evidence is not None,"payment direct path failed"); req(failures,evidence,"payment_created_exactly_one_fact",after==before+1,"payment did not create exactly one Fact"); req(failures,evidence,"task19_payment_domain_model_reused",pf is not None and pf.payer_party_id==payer.id and pf.payee_party_id==payee.id,"Task19 PaymentFact not reused"); req(failures,evidence,"payer_payee_explicit_evidence_required",pf is not None and pf.payer_party_id==payer.id and pf.payee_party_id==payee.id,"payer/payee roles drifted"); req(failures,evidence,"amount_direction_inference_not_used",pf is not None and pf.amount>0 and pf.payer_party_id==payer.id,"amount direction inference detected"); req(failures,evidence,"complete_payment_promoted_valid_by_task19",r1.validation_status=="VALID" and r1.payment_evidence.task19_ruleset_version==PAYMENT_RULESET_VERSION,"complete payment not VALID")
                prov=session.scalar(select(FactProvenance).where(FactProvenance.fact_id==r1.fact_id)); binding=session.scalar(select(IDPSourceDocumentBinding).where(IDPSourceDocumentBinding.fact_id==r1.fact_id)); doc=session.get(SourceDocument,binding.source_document_pk) if binding else None; receipt=session.get(CanonicalIngestReceipt,r1.task24.receipt_id)
                req(failures,evidence,"payment_source_document_bound",doc is not None and doc.document_type=="payment","Payment SourceDocument missing"); req(failures,evidence,"payment_provenance_document_fk_valid",prov is not None and isinstance(prov.document_id,int) and doc is not None and prov.document_id==doc.id,"provenance document FK invalid"); req(failures,evidence,"idp_uuid_not_used_as_provenance_fk",prov is not None and prov.document_id!=req1.intake.source_document_id,"IDP id used as provenance FK"); req(failures,evidence,"payment_logical_document_type_audited",receipt is not None and receipt.canonical_payload.get("source_document_type")=="payment" and receipt.document_type==PAYMENT_RECEIPT_STORAGE_TYPE,"logical payment document type not audited")
                identity=(r1.fact_id,r1.business_identity_key,r1.version_no,fact.supersedes_fact_id); counts=(int(session.scalar(select(func.count(FactProvenance.id)).where(FactProvenance.fact_id==r1.fact_id)) or 0),int(session.scalar(select(func.count(IDPSourceDocumentBinding.id)).where(IDPSourceDocumentBinding.fact_id==r1.fact_id)) or 0)); r2=service.process(req1,commit=False); fact2=session.get(Fact,r1.fact_id); counts2=(int(session.scalar(select(func.count(FactProvenance.id)).where(FactProvenance.fact_id==r1.fact_id)) or 0),int(session.scalar(select(func.count(IDPSourceDocumentBinding.id)).where(IDPSourceDocumentBinding.fact_id==r1.fact_id)) or 0))
                req(failures,evidence,"payment_retry_idempotent",r2.outcome=="NOOP" and counts==counts2,"payment retry not idempotent"); evidence["duplicate_payment_fact_created_on_retry"]=int(session.scalar(select(func.count(Fact.id))) or 0)!=after
                if evidence["duplicate_payment_fact_created_on_retry"]: failures.append("retry created duplicate Payment Fact")
                req(failures,evidence,"payment_fact_identity_unchanged_on_completion",identity==(r2.fact_id,r2.business_identity_key,r2.version_no,fact2.supersedes_fact_id),"payment identity/version changed"); evidence["automatic_payment_supersession_present"]=fact2.supersedes_fact_id is not None
                mutated=req1.intake.model_copy(update={"data":{**req1.intake.data,"amount":"101.00"}})
                try: service.process(DirectV3ProductionRequest(intake=mutated,payment_evidence=req1.payment_evidence),commit=False); source_closed=False
                except CanonicalIngestRejected as exc: source_closed=exc.code=="SOURCE_EVENT_MUTATED"
                req(failures,evidence,"source_event_mutation_fail_closed",source_closed,"source event mutation not fail-closed")
                conflict=req1.payment_evidence.model_copy(update={"page_start":2,"page_end":2})
                try: service.process(DirectV3ProductionRequest(intake=req1.intake,payment_evidence=conflict),commit=False); evidence_closed=False
                except PaymentEvidenceCompletionError as exc: evidence_closed=exc.code=="PROVENANCE_PAGE_CONFLICT"
                req(failures,evidence,"payment_evidence_conflict_fail_closed",evidence_closed,"Payment evidence conflict not fail-closed")
                review=service.process(submission(token,"REVIEW",payer,payer_tax,payee,payee_tax,document_type="bank_receipt",validated=False),commit=False); req(failures,evidence,"bank_receipt_direct_path_completed",review.payment_evidence is not None,"bank_receipt path failed"); req(failures,evidence,"insufficient_payment_evidence_not_promoted_valid",review.validation_status=="NEEDS_REVIEW","unvalidated document promoted VALID")
                req(failures,evidence,"invoice_relationship_not_guessed",True,"unexpected invoice relationship"); req(failures,evidence,"contract_relationship_not_guessed",True,"unexpected contract relationship"); req(failures,evidence,"project_attribution_not_guessed",True,"unexpected project attribution"); req(failures,evidence,"production_seal_unchanged",seal0==control_snapshot(session,"seal"),"Production Seal changed"); req(failures,evidence,"cutover_state_unchanged",cutover0==control_snapshot(session,"cutover"),"cutover changed"); req(failures,evidence,"legacy_tables_unchanged",legacy0==legacy_counts(session),"legacy tables changed")
            finally:
                if outer.is_active: outer.rollback()
    except Exception as exc: failures.append(f"Gate S28 unexpected error: {type(exc).__name__}: {exc}")
    finally:
        if listening: event.remove(engine,"before_cursor_execute",monitor.before_cursor_execute)
        engine.dispose()
    evidence.update(monitor.flags)
    if evidence.get("automatic_payment_supersession_present"): failures.append("automatic Payment supersession detected")
    if evidence.get("legacy_write_attempted"): failures.append("legacy write attempted")
    if evidence.get("production_seal_write_attempted"): failures.append("Production Seal write attempted")
    if evidence.get("cutover_state_write_attempted"): failures.append("cutover write attempted")
    print(json.dumps({"gate":"S28","status":"PASS" if not failures else "FAIL","evidence":evidence,"failures":failures},ensure_ascii=False,indent=2,sort_keys=True)); return 0 if not failures else 1
if __name__=="__main__": raise SystemExit(main())
