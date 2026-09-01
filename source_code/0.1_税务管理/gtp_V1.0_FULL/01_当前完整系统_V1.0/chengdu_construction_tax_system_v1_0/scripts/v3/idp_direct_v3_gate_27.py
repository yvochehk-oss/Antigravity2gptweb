#!/usr/bin/env python3
"""Gate S27 — IDP Direct-to-V3 Production Orchestrator & Legacy Bypass."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
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
from app.integration.idp_canonical.contract_role_resolver import CONTRACT_ROLE_RULESET_V1
from app.integration.idp_canonical.contract_role_schemas import ContractRoleCompletionRequest, ContractRoleEvidenceInput
from app.integration.idp_canonical.direct_v3_schemas import DirectV3ProductionRequest
from app.integration.idp_canonical.direct_v3_service import DirectV3IngestService
from app.integration.idp_canonical.evidence_schemas import InvoiceLineEvidence
from app.integration.idp_canonical.evidence_service import EvidenceCompletionError
from app.integration.idp_canonical.production_route_guard import EXPECTED_ROUTE, ProductionRouteGuard, ProductionRouteGuardError, validate_route_values
from app.integration.idp_canonical.service import CanonicalIngestRejected
from app.v3_contract_models import ContractFact
from app.v3_contract_role_models import ContractRoleEvidence, ContractRoleResolution
from app.v3_evidence_integration_models import IDPSourceDocumentBinding
from app.v3_fact_models import Fact, FactProvenance, InvoiceLine
from app.v3_integration_models import CanonicalIngestReceipt
from app.v3_party_models import SourceDocument
from scripts.v3.idp_direct_v3_gate_27_support import EXPECTED_HEAD, WriteAttemptMonitor, contract_submission, control_snapshot, database_url, disk_heads, invoice_submission, legacy_counts, party


def _req(failures: list[str], evidence: dict, key: str, ok: bool, message: str) -> None:
    evidence[key] = bool(ok)
    if not ok:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    evidence = {"gate": "S27", "alembic_head": EXPECTED_HEAD, "alembic_disk_heads": disk_heads(ROOT), "automatic_fact_supersession_present": False}
    engine = create_engine(database_url(), future=True, pool_pre_ping=True)
    monitor = WriteAttemptMonitor()
    token = uuid4().hex[:10].upper()
    try:
        with Session(engine) as session:
            outer = session.begin()
            try:
                db_heads = sorted(r[0] for r in session.execute(text("SELECT version_num FROM alembic_version_tax")).all())
                evidence["alembic_db_heads"] = db_heads
                _req(failures, evidence, "alembic_head_unchanged", db_heads == [EXPECTED_HEAD] and evidence["alembic_disk_heads"] == [EXPECTED_HEAD], "Alembic head changed")

                state = get_cutover_state(session, for_update=True)
                if state.writer_mode == "SHADOW":
                    session.execute(
                        text(
                            "UPDATE writer_cutover_states SET writer_mode='DUAL_WRITE', "
                            "legacy_write_enabled=true, new_fact_write_enabled=true, legacy_frozen=false, "
                            "new_fact_read_mode='SHADOW', rag_source='LEGACY', updated_by='gate:S27' "
                            "WHERE scope='GLOBAL'"
                        )
                    )
                    session.flush()
                session.execute(
                    text(
                        "UPDATE writer_cutover_states SET writer_mode='V3_PRIMARY', "
                        "legacy_write_enabled=false, new_fact_write_enabled=true, legacy_frozen=true, "
                        "updated_by='gate:S27' WHERE scope='GLOBAL' AND writer_mode='DUAL_WRITE'"
                    )
                )
                session.execute(
                    text(
                        "UPDATE writer_cutover_states SET new_fact_read_mode='PRIMARY', "
                        "rag_source='CANONICAL_FACTS', updated_by='gate:S27' "
                        "WHERE scope='GLOBAL' AND writer_mode='V3_PRIMARY' AND new_fact_read_mode='SHADOW' AND rag_source='LEGACY'"
                    )
                )
                session.flush()
                session.expire_all()
                if not session.execute(
                    text("SELECT 1 FROM v3_cutover_finalizations WHERE scope='GLOBAL'")
                ).scalar():
                    finalize_v3_production_cutover(session, actor="gate:S27", commit=False)
                    session.expire_all()

                event.listen(engine, "before_cursor_execute", monitor.before_cursor_execute)
                seal0, cutover0, legacy0 = control_snapshot(session, "seal"), control_snapshot(session, "cutover"), legacy_counts(session)

                route = ProductionRouteGuard(session).require(scope="GLOBAL")
                _req(failures, evidence, "production_route_guard_passed", all(getattr(route, k) == v for k, v in EXPECTED_ROUTE.items()), "production route not strict")
                _req(failures, evidence, "writer_mode_still_v3_primary", route.writer_mode == "V3_PRIMARY", "writer not V3_PRIMARY")
                _req(failures, evidence, "legacy_write_still_disabled", route.legacy_write_enabled is False, "legacy writer enabled")
                _req(failures, evidence, "reader_mode_unchanged", route.new_fact_read_mode == "PRIMARY", "reader not PRIMARY")
                _req(failures, evidence, "rag_source_unchanged", route.rag_source == "CANONICAL_FACTS", "RAG source changed")
                bad = deepcopy(EXPECTED_ROUTE); bad["legacy_write_enabled"] = True
                try:
                    validate_route_values(bad, EXPECTED_ROUTE); closed = False
                except ProductionRouteGuardError:
                    closed = True
                _req(failures, evidence, "production_route_guard_fail_closed_unit_verified", closed, "route guard did not fail closed")

                seller, seller_tax = party(session, token, "SELLER"); buyer, buyer_tax = party(session, token, "BUYER")
                service = DirectV3IngestService(session)
                inv = invoice_submission(token, "VALID", seller, seller_tax, buyer, buyer_tax)
                n0 = int(session.scalar(select(func.count(Fact.id))) or 0)
                r1 = service.process(inv, commit=False); session.flush(); n1 = int(session.scalar(select(func.count(Fact.id))) or 0)
                f1 = session.get(Fact, r1.fact_id); identity1 = (r1.fact_id, r1.business_identity_key, r1.version_no, f1.supersedes_fact_id)
                _req(failures, evidence, "approved_invoice_direct_path_completed", r1.outcome == "COMPLETED", "invoice direct path failed")
                _req(failures, evidence, "invoice_task24_reused", r1.task24.fact_id == r1.fact_id, "Task24 not reused")
                _req(failures, evidence, "invoice_task25_reused", r1.invoice_evidence is not None, "Task25 not reused")
                _req(failures, evidence, "invoice_task09_decision_reused", r1.invoice_evidence and r1.invoice_evidence.task09_ruleset_version == "V3_INVOICE_VALIDATION_V1", "Task09 not reused")
                _req(failures, evidence, "complete_invoice_promoted_valid_by_task09", r1.validation_status == "VALID" and r1.invoice_evidence.task09_desired_status == "VALID", "invoice not VALID by Task09")
                _req(failures, evidence, "invoice_created_exactly_one_fact", n1 == n0 + 1, "invoice did not create exactly one Fact")
                counts1 = (int(session.scalar(select(func.count(InvoiceLine.id)).where(InvoiceLine.invoice_fact_id == r1.fact_id)) or 0), int(session.scalar(select(func.count(FactProvenance.id)).where(FactProvenance.fact_id == r1.fact_id)) or 0), int(session.scalar(select(func.count(IDPSourceDocumentBinding.id)).where(IDPSourceDocumentBinding.fact_id == r1.fact_id)) or 0))
                r2 = service.process(inv, commit=False); n2 = int(session.scalar(select(func.count(Fact.id))) or 0)
                counts2 = (int(session.scalar(select(func.count(InvoiceLine.id)).where(InvoiceLine.invoice_fact_id == r1.fact_id)) or 0), int(session.scalar(select(func.count(FactProvenance.id)).where(FactProvenance.fact_id == r1.fact_id)) or 0), int(session.scalar(select(func.count(IDPSourceDocumentBinding.id)).where(IDPSourceDocumentBinding.fact_id == r1.fact_id)) or 0))
                f2 = session.get(Fact, r1.fact_id); identity2 = (r2.fact_id, r2.business_identity_key, r2.version_no, f2.supersedes_fact_id)
                _req(failures, evidence, "direct_path_retry_idempotent", r2.outcome == "NOOP" and counts1 == counts2, "invoice retry not idempotent")
                evidence["duplicate_fact_created_on_retry"] = n2 != n1
                if evidence["duplicate_fact_created_on_retry"]: failures.append("retry created duplicate Fact")
                _req(failures, evidence, "fact_identity_unchanged_on_completion", identity1[0] == identity2[0], "Fact id changed")
                _req(failures, evidence, "business_identity_unchanged_on_completion", identity1[1] == identity2[1], "business identity changed")
                evidence["automatic_fact_supersession_present"] = identity1[3] != identity2[3]
                if evidence["automatic_fact_supersession_present"]: failures.append("automatic Fact supersession detected")

                changed = inv.invoice_evidence.model_copy(update={"lines": [InvoiceLineEvidence(line_no=1, item_name="changed", net_amount=Decimal("99"), vat_amount=Decimal("13"), tax_rate=Decimal("0.13"))]})
                try:
                    service.process(DirectV3ProductionRequest(intake=inv.intake, invoice_evidence=changed), commit=False); invoice_conflict = False
                except EvidenceCompletionError:
                    invoice_conflict = True
                _req(failures, evidence, "invoice_conflict_fail_closed", invoice_conflict, "invoice conflict not fail-closed")

                a, tax_a = party(session, token, "A"); b, tax_b = party(session, token, "B")
                con = contract_submission(token, "RESOLVED", a, tax_a, b, tax_b, reverse=True)
                c1 = service.process(con, commit=False); domain = session.get(ContractFact, c1.fact_id)
                _req(failures, evidence, "approved_contract_direct_path_completed", c1.outcome == "COMPLETED", "contract direct path failed")
                _req(failures, evidence, "contract_task24_reused", c1.task24.fact_id == c1.fact_id, "contract Task24 not reused")
                _req(failures, evidence, "contract_task26_reused", c1.contract_roles and c1.contract_roles.ruleset_version == CONTRACT_ROLE_RULESET_V1, "Task26 not reused")
                _req(failures, evidence, "contract_roles_resolved", c1.contract_roles.resolution_status == "RESOLVED" and domain.buyer_party_id == b.id and domain.seller_party_id == a.id, "contract roles not resolved")
                _req(failures, evidence, "contract_remains_needs_review", c1.validation_status == "NEEDS_REVIEW", "contract promoted beyond NEEDS_REVIEW")
                _req(failures, evidence, "contract_direct_retry_idempotent", service.process(con, commit=False).outcome == "NOOP", "contract retry not idempotent")

                base = contract_submission(token, "CONFLICT", a, tax_a, b, tax_b); cb = service.process(base, commit=False)
                roles = ContractRoleCompletionRequest(source_system="IDP", source_document_id=base.intake.source_document_id, source_extraction_id=base.intake.source_extraction_id, document_sha256=base.intake.document_sha256, submitted_by="gate:S27", evidences=[ContractRoleEvidenceInput(source_party_role="PARTY_A", legal_role_label="甲方（承包人）", evidence_text="另一条款明确甲方（承包人）承担相应法律角色。", page_no=2)])
                cr = service.process(DirectV3ProductionRequest(intake=base.intake, contract_roles=roles), commit=False); cd = session.get(ContractFact, cb.fact_id)
                _req(failures, evidence, "contract_role_conflict_fail_closed", cr.contract_roles.resolution_status == "NEEDS_REVIEW" and cd.buyer_party_id is None and cd.seller_party_id is None, "contract conflict not fail-closed")

                unapproved = invoice_submission(token, "UNAPPROVED", seller, seller_tax, buyer, buyer_tax, review_status="extracted")
                before = int(session.scalar(select(func.count(Fact.id))) or 0)
                try:
                    service.process(unapproved, commit=False); rejected = False
                except CanonicalIngestRejected as exc:
                    rejected = exc.code == "SOURCE_NOT_APPROVED"
                after = int(session.scalar(select(func.count(Fact.id))) or 0)
                _req(failures, evidence, "unapproved_idp_submission_rejected", rejected, "unapproved submission accepted")
                evidence["unapproved_submission_created_fact"] = after != before
                if evidence["unapproved_submission_created_fact"]: failures.append("unapproved submission created Fact")

                atomic = invoice_submission(token, "ATOMIC", seller, seller_tax, buyer, buyer_tax, invoice_status="RED")
                before = int(session.scalar(select(func.count(Fact.id))) or 0)
                try:
                    service.process(atomic, commit=False); failed = False
                except Exception:
                    failed = True
                after = int(session.scalar(select(func.count(Fact.id))) or 0)
                receipts = int(session.scalar(select(func.count(CanonicalIngestReceipt.id)).where(CanonicalIngestReceipt.source_extraction_id == atomic.intake.source_extraction_id)) or 0)
                _req(failures, evidence, "atomic_rollback_verified", failed and before == after and receipts == 0, "atomic rollback failed")
                _req(failures, evidence, "invoice_source_document_bound", int(session.scalar(select(func.count(SourceDocument.id)).where(SourceDocument.external_document_id == inv.intake.source_document_id)) or 0) == 1, "SourceDocument missing")
                _req(failures, evidence, "contract_role_audit_preserved", int(session.scalar(select(func.count(ContractRoleEvidence.id)).where(ContractRoleEvidence.fact_id == c1.fact_id)) or 0) >= 2 and int(session.scalar(select(func.count(ContractRoleResolution.id)).where(ContractRoleResolution.fact_id == c1.fact_id)) or 0) >= 1, "contract audit missing")
                _req(failures, evidence, "production_seal_unchanged", seal0 == control_snapshot(session, "seal"), "Production Seal changed")
                _req(failures, evidence, "cutover_state_unchanged", cutover0 == control_snapshot(session, "cutover"), "cutover changed")
                _req(failures, evidence, "legacy_tables_unchanged", legacy0 == legacy_counts(session), "legacy tables changed")
            finally:
                if outer.is_active: outer.rollback()
    except Exception as exc:
        failures.append(f"Gate S27 unexpected error: {type(exc).__name__}: {exc}")
    finally:
        try:
            event.remove(engine, "before_cursor_execute", monitor.before_cursor_execute)
        except Exception:
            pass
        engine.dispose()

    evidence.update(monitor.flags)
    if evidence.get("production_seal_write_attempted"): failures.append("Production Seal write attempted")
    if evidence.get("cutover_state_write_attempted"): failures.append("cutover write attempted")
    if evidence.get("legacy_write_attempted"): failures.append("legacy write attempted")
    print(json.dumps({"gate": "S27", "status": "PASS" if not failures else "FAIL", "evidence": evidence, "failures": failures}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
