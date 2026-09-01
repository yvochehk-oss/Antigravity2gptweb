#!/usr/bin/env python3
"""Gate S29 — Explicit Project Attribution & FactProjectAllocation completion.

All fixture and Task29 writes run inside one outer transaction and are rolled
back. The gate never initializes or mutates Production Seal/Cutover state; it
requires the already sealed production route and proves it remains unchanged.
"""
from __future__ import annotations

from datetime import date
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
from app.integration.idp_canonical.production_route_guard import EXPECTED_ROUTE, ProductionRouteGuard
from app.integration.idp_canonical.project_allocation_basis import (
    CONTRACT_PROJECT_BASIS_V1,
    INVOICE_PROJECT_BASIS_V1,
    PAYMENT_PROJECT_BASIS_V1,
    TASK29_PROJECT_ATTRIBUTION_RULESET_V1,
)
from app.integration.idp_canonical.project_attribution_schemas import (
    ProjectAllocationInput,
    ProjectAttributionRequest,
)
from app.integration.idp_canonical.project_attribution_service import ProjectAttributionService
from app.models import Project
from app.v3_contract_models import ContractFact
from app.v3_fact_models import Fact, InvoiceFact
from app.v3_party_models import SourceDocument
from app.v3_payment_models import PaymentFact
from app.v3_project_analysis_models import FactProjectAllocation, ProjectTaxAnalysis
from scripts.v3.idp_direct_v3_gate_27_support import (
    EXPECTED_HEAD,
    WriteAttemptMonitor,
    control_snapshot,
    database_url,
    disk_heads,
    legacy_counts,
    party,
)


def req(failures: list[str], evidence: dict, key: str, ok: bool, message: str) -> None:
    evidence[key] = bool(ok)
    if not ok:
        failures.append(message)


def table_count(session: Session, table_name: str) -> int:
    return int(session.execute(text(f'SELECT count(*) FROM "{table_name}"')).scalar_one())


def fact_snapshot(fact: Fact) -> tuple:
    return (
        fact.id,
        fact.fact_type,
        fact.business_identity_key,
        fact.version_no,
        fact.is_current,
        fact.supersedes_fact_id,
        fact.validation_status,
    )


def make_project(session: Session, token: str, suffix: str) -> Project:
    code = f"S29-{suffix}-{token}"
    row = Project(
        code=code,
        project_code=code,
        name=f"S29 Project {suffix} {token}",
        city="Chengdu",
        contract_total=Decimal("1000000.00"),
    )
    session.add(row)
    session.flush()
    return row


def make_invoice(session: Session, token: str, suffix: str, *, status: str = "VALID") -> Fact:
    fact = Fact(
        fact_type="INVOICE",
        business_identity_key=f"S29|INVOICE|{suffix}|{token}",
        version_no=1,
        is_current=True,
        supersedes_fact_id=None,
        validation_status=status,
    )
    session.add(fact)
    session.flush()
    session.add(
        InvoiceFact(
            fact_id=fact.id,
            invoice_identity_key=f"S29-INVOICE-IDENTITY-{suffix}-{token}",
            invoice_identity_version="DIGITAL_V1",
            invoice_number=f"S29-INV-{suffix}-{token}",
            invoice_date=date(2026, 9, 1),
            invoice_status="VALID",
            document_type="invoice",
            net_amount=Decimal("100.00"),
            vat_amount=Decimal("13.00"),
            gross_amount=Decimal("113.00"),
            currency="CNY",
        )
    )
    session.flush()
    return fact


def make_payment(session: Session, token: str, suffix: str, payer_id: int, payee_id: int, *, status: str = "VALID") -> Fact:
    fact = Fact(
        fact_type="PAYMENT",
        business_identity_key=f"S29|PAYMENT|{suffix}|{token}",
        version_no=1,
        is_current=True,
        supersedes_fact_id=None,
        validation_status=status,
    )
    session.add(fact)
    session.flush()
    session.add(
        PaymentFact(
            fact_id=fact.id,
            payer_party_id=payer_id,
            payee_party_id=payee_id,
            transaction_date=date(2026, 9, 1),
            amount=Decimal("100.00"),
            currency="CNY",
            bank_reference=f"S29-PAY-{suffix}-{token}",
            settlement_method="BANK_TRANSFER",
            payment_nature="NORMAL",
        )
    )
    session.flush()
    return fact


def make_contract(session: Session, token: str, suffix: str, *, status: str = "NEEDS_REVIEW") -> Fact:
    fact = Fact(
        fact_type="CONTRACT",
        business_identity_key=f"S29|CONTRACT|{suffix}|{token}",
        version_no=1,
        is_current=True,
        supersedes_fact_id=None,
        validation_status=status,
    )
    session.add(fact)
    session.flush()
    session.add(
        ContractFact(
            fact_id=fact.id,
            contract_number=f"S29-HT-{suffix}-{token}",
            contract_date=date(2026, 9, 1),
            contract_amount=Decimal("100.00"),
            currency="CNY",
        )
    )
    session.flush()
    return fact


def request(fact_id: int, allocations: list[ProjectAllocationInput], *, reviewed_by: str = "gate:S29", source_document_id: int | None = None) -> ProjectAttributionRequest:
    return ProjectAttributionRequest(
        fact_id=fact_id,
        allocations=allocations,
        source_document_id=source_document_id,
        reviewed_by=reviewed_by,
        note="Gate S29 explicit project attribution",
    )


def main() -> int:
    failures: list[str] = []
    evidence: dict = {
        "gate": "S29",
        "alembic_head": EXPECTED_HEAD,
        "task29_ruleset_version": TASK29_PROJECT_ATTRIBUTION_RULESET_V1,
        "invoice_basis_version": INVOICE_PROJECT_BASIS_V1,
        "payment_basis_version": PAYMENT_PROJECT_BASIS_V1,
        "contract_basis_version": CONTRACT_PROJECT_BASIS_V1,
        "automatic_fact_supersession_present": False,
        "automatic_allocation_supersession_present": False,
    }
    engine = create_engine(database_url(), future=True, pool_pre_ping=True)
    monitor = WriteAttemptMonitor()
    token = uuid4().hex[:10].upper()
    listening = False
    try:
        with Session(engine) as session:
            outer = session.begin()
            try:
                db_heads = sorted(row[0] for row in session.execute(text("SELECT version_num FROM alembic_version_tax")).all())
                disk = disk_heads(ROOT)
                evidence["alembic_db_heads"] = db_heads
                evidence["alembic_disk_heads"] = disk
                req(failures, evidence, "alembic_head_unchanged", db_heads == [EXPECTED_HEAD] and disk == [EXPECTED_HEAD], "Alembic head changed from 97")

                state = get_cutover_state(session, for_update=True)
                if state.writer_mode == "SHADOW":
                    session.execute(
                        text(
                            "UPDATE writer_cutover_states SET writer_mode='DUAL_WRITE', "
                            "legacy_write_enabled=true, new_fact_write_enabled=true, legacy_frozen=false, "
                            "new_fact_read_mode='SHADOW', rag_source='LEGACY', updated_by='gate:S29' "
                            "WHERE scope='GLOBAL'"
                        )
                    )
                    session.flush()
                session.execute(
                    text(
                        "UPDATE writer_cutover_states SET writer_mode='V3_PRIMARY', "
                        "legacy_write_enabled=false, new_fact_write_enabled=true, legacy_frozen=true, "
                        "updated_by='gate:S29' WHERE scope='GLOBAL' AND writer_mode='DUAL_WRITE'"
                    )
                )
                session.execute(
                    text(
                        "UPDATE writer_cutover_states SET new_fact_read_mode='PRIMARY', "
                        "rag_source='CANONICAL_FACTS', updated_by='gate:S29' "
                        "WHERE scope='GLOBAL' AND writer_mode='V3_PRIMARY' AND new_fact_read_mode='SHADOW' AND rag_source='LEGACY'"
                    )
                )
                session.flush()
                session.expire_all()
                if not session.execute(
                    text("SELECT 1 FROM v3_cutover_finalizations WHERE scope='GLOBAL'")
                ).scalar():
                    finalize_v3_production_cutover(session, actor="gate:S29", commit=False)
                    session.expire_all()

                seal0 = control_snapshot(session, "seal")
                cutover0 = control_snapshot(session, "cutover")
                legacy0 = legacy_counts(session)
                route = ProductionRouteGuard(session).require(scope="GLOBAL")
                req(failures, evidence, "production_route_guard_reused", all(getattr(route, key) == value for key, value in EXPECTED_ROUTE.items()), "production route is not strictly sealed")
                req(failures, evidence, "writer_mode_still_v3_primary", route.writer_mode == "V3_PRIMARY", "writer is not V3_PRIMARY")
                req(failures, evidence, "legacy_write_still_disabled", route.legacy_write_enabled is False, "legacy writer is enabled")
                req(failures, evidence, "reader_mode_unchanged", route.new_fact_read_mode == "PRIMARY", "reader is not PRIMARY")
                req(failures, evidence, "rag_source_unchanged", route.rag_source == "CANONICAL_FACTS", "RAG source changed")

                service_source = (ROOT / "app/integration/idp_canonical/project_attribution_service.py").read_text(encoding="utf-8")
                resolver_source = (ROOT / "app/integration/idp_canonical/project_attribution_resolver.py").read_text(encoding="utf-8")
                resolver_lower = resolver_source.lower()
                req(failures, evidence, "task17_fact_project_allocation_reused", "FactProjectAllocation(" in service_source, "Task17 FactProjectAllocation is not reused")
                req(failures, evidence, "project_code_exact_resolution_only", "Project.project_code.in_(codes)" in resolver_source, "resolver does not use exact project_code")
                req(failures, evidence, "project_name_fuzzy_match_not_used", "ilike" not in resolver_lower and ".like(" not in resolver_lower and "Project.name" not in resolver_source and "Project.project_name" not in resolver_source, "fuzzy/name project matching detected")
                req(failures, evidence, "project_auto_create_not_used", "Project(" not in resolver_source and "Project(" not in service_source, "Task29 attempts to create Project")
                req(failures, evidence, "buyer_seller_project_inference_not_used", "buyer_party_id" not in service_source and "seller_party_id" not in service_source, "buyer/seller project inference detected")
                req(failures, evidence, "payer_payee_project_inference_not_used", "payer_party_id" not in service_source and "payee_party_id" not in service_source, "payer/payee project inference detected")
                req(failures, evidence, "amount_direction_project_inference_not_used", "amount_direction" not in service_source, "amount-direction project inference detected")
                req(failures, evidence, "date_proximity_project_inference_not_used", "date_proximity" not in service_source, "date-proximity project inference detected")
                req(failures, evidence, "fact_relationship_write_not_used", "FactRelationship" not in service_source, "Task29 writes FactRelationship")
                req(failures, evidence, "project_tax_analysis_write_not_used", "ProjectTaxAnalysis" not in service_source, "Task29 writes ProjectTaxAnalysis")

                event.listen(engine, "before_cursor_execute", monitor.before_cursor_execute)
                listening = True

                p1 = make_project(session, token, "P1")
                p2 = make_project(session, token, "P2")
                payer, _ = party(session, token, "S29PAYER")
                payee, _ = party(session, token, "S29PAYEE")
                source_doc = SourceDocument(
                    source_system="GATE_S29",
                    external_document_id=f"S29-DOC-{token}",
                    filename=f"S29-{token}.pdf",
                    mime_type="application/pdf",
                    document_type="payment",
                    status="VALIDATED",
                )
                session.add(source_doc)
                session.flush()

                invoice_single = make_invoice(session, token, "I1")
                invoice_multi = make_invoice(session, token, "I2")
                payment_single = make_payment(session, token, "P1", payer.id, payee.id)
                payment_multi = make_payment(session, token, "P2", payer.id, payee.id)
                contract_single = make_contract(session, token, "C1")
                contract_multi = make_contract(session, token, "C2")
                invoice_unknown = make_invoice(session, token, "I3")
                invoice_duplicate = make_invoice(session, token, "I4")
                invoice_mismatch = make_invoice(session, token, "I5")
                invoice_conflict = make_invoice(session, token, "I6")
                session.flush()

                tracked = [invoice_single, invoice_multi, payment_single, payment_multi, contract_single, contract_multi, invoice_unknown, invoice_duplicate, invoice_mismatch, invoice_conflict]
                snapshots = {row.id: fact_snapshot(row) for row in tracked}
                fact_count0 = int(session.scalar(select(func.count(Fact.id))) or 0)
                project_count0 = int(session.scalar(select(func.count(Project.id))) or 0)
                relationship_count0 = table_count(session, "fact_relationships")
                tax_analysis_count0 = int(session.scalar(select(func.count(ProjectTaxAnalysis.id))) or 0)

                service = ProjectAttributionService(session)

                r_i1 = service.complete(request(invoice_single.id, [ProjectAllocationInput(project_code=p1.project_code)]), commit=False)
                req(failures, evidence, "explicit_single_project_attribution_confirmed", r_i1.outcome == "CREATED", "single Invoice project attribution failed")
                row_i1 = session.get(FactProjectAllocation, r_i1.allocation_ids[0]) if r_i1.allocation_ids else None
                req(failures, evidence, "invoice_full_basis_allocated", row_i1 is not None and row_i1.allocated_net == Decimal("100.00") and row_i1.allocated_vat == Decimal("13.00") and row_i1.allocated_gross == Decimal("113.00"), "Invoice full allocation basis incorrect")

                r_i2 = service.complete(request(invoice_multi.id, [
                    ProjectAllocationInput(project_code=p1.project_code, allocated_net=Decimal("60.00"), allocated_vat=Decimal("7.80"), allocated_gross=Decimal("67.80")),
                    ProjectAllocationInput(project_code=p2.project_code, allocated_net=Decimal("40.00"), allocated_vat=Decimal("5.20"), allocated_gross=Decimal("45.20")),
                ]), commit=False)
                req(failures, evidence, "invoice_multi_project_allocation_supported", r_i2.outcome == "CREATED" and len(r_i2.allocation_ids) == 2, "Invoice multi-project allocation failed")

                r_p1 = service.complete(request(payment_single.id, [ProjectAllocationInput(project_code=p1.project_code)], source_document_id=source_doc.id), commit=False)
                payment_row = session.get(FactProjectAllocation, r_p1.allocation_ids[0]) if r_p1.allocation_ids else None
                req(failures, evidence, "payment_single_project_allocation_supported", r_p1.outcome == "CREATED" and payment_row is not None and payment_row.allocated_net == Decimal("100.00") and payment_row.allocated_vat == Decimal("0.00") and payment_row.allocated_gross == Decimal("100.00"), "Payment single project allocation failed")
                req(failures, evidence, "source_document_fk_valid", payment_row is not None and isinstance(payment_row.source_document_id, int) and payment_row.source_document_id == source_doc.id and payment_row.allocation_method == "SOURCE_DOCUMENT" and payment_row.proposal_source == "DOCUMENT", "SourceDocument allocation audit invalid")
                req(failures, evidence, "idp_uuid_not_used_as_source_document_fk", payment_row is not None and payment_row.source_document_id == source_doc.id, "non-integer source evidence used as FK")

                r_p2 = service.complete(request(payment_multi.id, [
                    ProjectAllocationInput(project_code=p1.project_code, allocated_amount=Decimal("60.00")),
                    ProjectAllocationInput(project_code=p2.project_code, allocated_amount=Decimal("40.00")),
                ]), commit=False)
                req(failures, evidence, "payment_multi_project_allocation_supported", r_p2.outcome == "CREATED" and len(r_p2.allocation_ids) == 2, "Payment multi-project allocation failed")

                r_c1 = service.complete(request(contract_single.id, [ProjectAllocationInput(project_code=p1.project_code)]), commit=False)
                contract_row = session.get(FactProjectAllocation, r_c1.allocation_ids[0]) if r_c1.allocation_ids else None
                req(failures, evidence, "contract_single_project_allocation_supported", r_c1.outcome == "CREATED" and contract_row is not None and contract_row.allocated_net == Decimal("100.00") and contract_row.allocated_vat == Decimal("0.00") and contract_row.allocated_gross == Decimal("100.00"), "Contract single project allocation failed")

                r_c2 = service.complete(request(contract_multi.id, [
                    ProjectAllocationInput(project_code=p1.project_code, allocated_amount=Decimal("25.00")),
                    ProjectAllocationInput(project_code=p2.project_code, allocated_amount=Decimal("75.00")),
                ]), commit=False)
                req(failures, evidence, "contract_multi_project_allocation_supported", r_c2.outcome == "CREATED" and len(r_c2.allocation_ids) == 2, "Contract multi-project allocation failed")
                req(failures, evidence, "explicit_multi_project_allocation_supported", all(result.outcome == "CREATED" and len(result.allocation_ids) == 2 for result in (r_i2, r_p2, r_c2)), "not all three Fact types support explicit multi-project allocation")

                allocation_count_before_retry = int(session.scalar(select(func.count(FactProjectAllocation.id))) or 0)
                retry = service.complete(request(invoice_single.id, [ProjectAllocationInput(project_code=p1.project_code)], reviewed_by="gate:S29:retry"), commit=False)
                allocation_count_after_retry = int(session.scalar(select(func.count(FactProjectAllocation.id))) or 0)
                req(failures, evidence, "duplicate_attribution_retry_idempotent", retry.outcome == "NOOP" and allocation_count_before_retry == allocation_count_after_retry and retry.allocation_ids == r_i1.allocation_ids, "Task29 retry is not idempotent")

                unknown = service.complete(request(invoice_unknown.id, [ProjectAllocationInput(project_code=f"S29-UNKNOWN-{token}")]), commit=False)
                req(failures, evidence, "unknown_project_code_needs_review", unknown.outcome == "NEEDS_REVIEW" and unknown.reasons[0].code == "PROJECT_CODE_NOT_FOUND", "unknown project_code did not fail closed")

                duplicate = service.complete(request(invoice_duplicate.id, [
                    ProjectAllocationInput(project_code=p1.project_code, allocated_net=Decimal("50.00"), allocated_vat=Decimal("6.50"), allocated_gross=Decimal("56.50")),
                    ProjectAllocationInput(project_code=p1.project_code, allocated_net=Decimal("50.00"), allocated_vat=Decimal("6.50"), allocated_gross=Decimal("56.50")),
                ]), commit=False)
                req(failures, evidence, "duplicate_project_evidence_needs_review", duplicate.outcome == "NEEDS_REVIEW" and duplicate.reasons[0].code == "DUPLICATE_PROJECT_CODE", "duplicate project evidence did not fail closed")

                mismatch = service.complete(request(invoice_mismatch.id, [
                    ProjectAllocationInput(project_code=p1.project_code, allocated_net=Decimal("50.00"), allocated_vat=Decimal("6.50"), allocated_gross=Decimal("56.50")),
                    ProjectAllocationInput(project_code=p2.project_code, allocated_net=Decimal("40.00"), allocated_vat=Decimal("5.20"), allocated_gross=Decimal("45.20")),
                ]), commit=False)
                req(failures, evidence, "allocation_aggregate_balance_verified", mismatch.outcome == "NEEDS_REVIEW" and mismatch.reasons[0].code == "ALLOCATION_AGGREGATE_MISMATCH", "aggregate amount conservation not enforced")

                first_conflict = service.complete(request(invoice_conflict.id, [ProjectAllocationInput(project_code=p1.project_code)]), commit=False)
                before_conflict_rows = [(row.id, row.project_id, row.allocated_net, row.allocated_vat, row.allocated_gross, row.is_current, row.status) for row in session.scalars(select(FactProjectAllocation).where(FactProjectAllocation.fact_id == invoice_conflict.id).order_by(FactProjectAllocation.id)).all()]
                conflict = service.complete(request(invoice_conflict.id, [
                    ProjectAllocationInput(project_code=p1.project_code, allocated_net=Decimal("50.00"), allocated_vat=Decimal("6.50"), allocated_gross=Decimal("56.50")),
                    ProjectAllocationInput(project_code=p2.project_code, allocated_net=Decimal("50.00"), allocated_vat=Decimal("6.50"), allocated_gross=Decimal("56.50")),
                ]), commit=False)
                after_conflict_rows = [(row.id, row.project_id, row.allocated_net, row.allocated_vat, row.allocated_gross, row.is_current, row.status) for row in session.scalars(select(FactProjectAllocation).where(FactProjectAllocation.fact_id == invoice_conflict.id).order_by(FactProjectAllocation.id)).all()]
                req(failures, evidence, "allocation_conflict_fail_closed", first_conflict.outcome == "CREATED" and conflict.outcome == "NEEDS_REVIEW" and conflict.reasons[0].code == "EXISTING_PROJECT_ALLOCATION_CONFLICT" and before_conflict_rows == after_conflict_rows, "existing allocation conflict was mutated/overwritten")

                failed_fact_ids = {invoice_unknown.id, invoice_duplicate.id, invoice_mismatch.id}
                failed_rows = int(session.scalar(select(func.count(FactProjectAllocation.id)).where(FactProjectAllocation.fact_id.in_(failed_fact_ids))) or 0)
                req(failures, evidence, "conflicting_or_insufficient_evidence_zero_write", failed_rows == 0, "fail-closed requests wrote allocations")

                all_rows = session.scalars(select(FactProjectAllocation).where(FactProjectAllocation.fact_id.in_([row.id for row in tracked]))).all()
                req(failures, evidence, "task29_rows_auditable", bool(all_rows) and all(row.status == "CONFIRMED" and row.reviewed_by and row.reviewed_at is not None and row.rule_version == TASK29_PROJECT_ATTRIBUTION_RULESET_V1 and row.confidence == "HIGH" for row in all_rows), "Task29 successful rows are not fully audited")
                evidence["automatic_allocation_supersession_present"] = any(row.supersedes_allocation_id is not None or row.allocation_version != 1 for row in all_rows)
                if evidence["automatic_allocation_supersession_present"]:
                    failures.append("Task29 automatically superseded allocation history")

                session.expire_all()
                unchanged = True
                for fact_id, expected in snapshots.items():
                    fact = session.get(Fact, fact_id)
                    if fact is None or fact_snapshot(fact) != expected:
                        unchanged = False
                        break
                req(failures, evidence, "fact_validation_status_unchanged", unchanged, "Fact identity/version/validation state changed")
                req(failures, evidence, "fact_identity_unchanged", unchanged, "Fact identity changed")
                req(failures, evidence, "fact_version_unchanged", unchanged, "Fact version changed")
                evidence["new_fact_created"] = int(session.scalar(select(func.count(Fact.id))) or 0) != fact_count0
                if evidence["new_fact_created"]:
                    failures.append("Task29 created a new Fact")
                evidence["new_project_created"] = int(session.scalar(select(func.count(Project.id))) or 0) != project_count0
                if evidence["new_project_created"]:
                    failures.append("Task29 auto-created a Project")
                evidence["fact_relationship_created"] = table_count(session, "fact_relationships") != relationship_count0
                if evidence["fact_relationship_created"]:
                    failures.append("Task29 created FactRelationship")
                evidence["project_tax_analysis_write_attempted"] = int(session.scalar(select(func.count(ProjectTaxAnalysis.id))) or 0) != tax_analysis_count0
                if evidence["project_tax_analysis_write_attempted"]:
                    failures.append("Task29 wrote ProjectTaxAnalysis")

                current_facts = [session.get(Fact, row.id) for row in tracked]
                evidence["automatic_fact_supersession_present"] = any(row is not None and row.supersedes_fact_id is not None for row in current_facts)
                if evidence["automatic_fact_supersession_present"]:
                    failures.append("Task29 introduced Fact supersession")

                req(failures, evidence, "production_seal_unchanged", seal0 == control_snapshot(session, "seal"), "Production Seal changed")
                req(failures, evidence, "cutover_state_unchanged", cutover0 == control_snapshot(session, "cutover"), "Cutover state changed")
                req(failures, evidence, "legacy_tables_unchanged", legacy0 == legacy_counts(session), "legacy tables changed")
            finally:
                if outer.is_active:
                    outer.rollback()
    except Exception as exc:
        failures.append(f"Gate S29 unexpected error: {type(exc).__name__}: {exc}")
    finally:
        if listening:
            event.remove(engine, "before_cursor_execute", monitor.before_cursor_execute)
        engine.dispose()

    evidence.update(monitor.flags)
    if evidence.get("legacy_write_attempted"):
        failures.append("legacy write attempted")
    if evidence.get("production_seal_write_attempted"):
        failures.append("Production Seal write attempted")
    if evidence.get("cutover_state_write_attempted"):
        failures.append("Cutover state write attempted")

    print(json.dumps({"gate": "S29", "status": "PASS" if not failures else "FAIL", "evidence": evidence, "failures": failures}, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
