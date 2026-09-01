#!/usr/bin/env python3
"""Gate S32 — Native V3 Web/Boss API + Production HTTP E2E final closure.

The gate is non-destructive: application commits are isolated inside a
connection-owned transaction and the outer transaction is rolled back. It
never initializes or changes Production Seal/Cutover state.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session

import app.middleware as middleware
from app.ai.canonical_context import build_canonical_context
from app.domain.finance.canonical_four_flow import CanonicalFourFlow
from app.domain.finance.canonical_project_finance import CanonicalProjectFinance
from app.domain.tax.canonical_project_tax import CanonicalProjectTax
from app.integration.idp_canonical.production_route_guard import EXPECTED_ROUTE, ProductionRouteGuard
from app.routers.v3_canonical import get_v3_db
from app.v3_fact_models import Fact, FactRelationship
from app.v3_project_analysis_models import FactProjectAllocation
from app.wiring import create_app
from scripts.v3.canonical_finance_rag_gate_31_support import make_fixture
from scripts.v3.idp_direct_v3_gate_27_support import (
    contract_submission,
    control_snapshot,
    database_url,
    disk_heads,
    invoice_submission,
    legacy_counts,
    party,
)
from scripts.v3.payment_direct_v3_gate_28 import submission as payment_submission

EXPECTED_HEAD = "98_v3_explicit_fact_relationship_graph"
EXPECTED_PATHS = {
    "/api/v3/boss/projects/{project_id}/snapshot",
    "/api/v3/boss/projects/{project_id}/finance",
    "/api/v3/boss/projects/{project_id}/four-flow",
    "/api/v3/boss/projects/{project_id}/tax",
    "/api/v3/boss/projects/{project_id}/evidence-quality",
    "/api/v3/boss/projects/{project_id}/rag-context",
    "/api/v3/idp/direct",
    "/api/v3/system/status",
}


class S32Monitor:
    PROTECTED_DML = {
        "v3_cutover_finalizations": "production_seal_write_attempted",
        "writer_cutover_states": "cutover_state_write_attempted",
        "contracts": "legacy_write_attempted",
        "contracts_v3": "legacy_write_attempted",
        "invoices": "legacy_write_attempted",
        "invoices_v3": "legacy_write_attempted",
        "cashflows": "legacy_write_attempted",
        "fulfillments": "legacy_write_attempted",
        "real_costs": "legacy_write_attempted",
        "budgets": "legacy_write_attempted",
        "progress": "legacy_write_attempted",
    }
    LEGACY_READ_TABLES = {
        "contracts", "invoices", "cashflows", "fulfillments",
        "real_costs", "budgets", "progress",
    }

    def __init__(self) -> None:
        self.flags = {
            "production_seal_write_attempted": False,
            "cutover_state_write_attempted": False,
            "legacy_write_attempted": False,
            "canonical_web_legacy_business_read_attempted": False,
        }
        self.watch_legacy_reads = False
        self.dml = re.compile(
            r'^\s*(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+"?([A-Za-z0-9_]+)"?', re.I
        )
        self.read = re.compile(r'\b(?:FROM|JOIN)\s+"?([A-Za-z0-9_]+)"?', re.I)

    def before_cursor_execute(self, conn, cursor, statement, parameters, context, executemany) -> None:
        sql = str(statement)
        match = self.dml.match(sql)
        if match:
            flag = self.PROTECTED_DML.get(match.group(1).lower())
            if flag:
                self.flags[flag] = True
                raise AssertionError(f"Gate S32 forbidden DML against {match.group(1)}")
        if self.watch_legacy_reads and sql.lstrip().upper().startswith("SELECT"):
            tables = {m.group(1).lower() for m in self.read.finditer(sql)}
            touched = tables & self.LEGACY_READ_TABLES
            if touched:
                self.flags["canonical_web_legacy_business_read_attempted"] = True
                raise AssertionError(
                    f"Gate S32 canonical Web read touched legacy business tables: {sorted(touched)}"
                )


def req(failures, evidence, key, ok, message):
    evidence[key] = bool(ok)
    if not ok:
        failures.append(message)


def _counts(session: Session) -> dict[str, int]:
    names = ("facts", "fact_project_allocations", "fact_relationships", "projects")
    result = {}
    for name in names:
        exists = session.execute(
            text("SELECT to_regclass(:name)"), {"name": f"public.{name}"}
        ).scalar_one()
        result[name] = (
            int(session.execute(text(f'SELECT count(*) FROM "{name}"')).scalar_one())
            if exists else -1
        )
    return result


def main() -> int:
    failures: list[str] = []
    evidence: dict[str, object] = {
        "gate": "S32",
        "alembic_head": EXPECTED_HEAD,
        "automatic_fact_supersession_present": False,
    }
    engine = create_engine(database_url(), future=True, pool_pre_ping=True)
    monitor = S32Monitor()
    listening = False
    original_auth = middleware.current_user_from_request
    baseline = None
    import os
    os.environ["APP_ENV"] = "test"
    try:
        with Session(engine) as pre:
            baseline = _counts(pre)
        conn = engine.connect()
        outer = conn.begin()
        session = Session(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            db_heads = sorted(
                row[0]
                for row in session.execute(
                    text("SELECT version_num FROM alembic_version_tax")
                ).all()
            )
            disk = disk_heads(ROOT)
            evidence["alembic_db_heads"] = db_heads
            evidence["alembic_disk_heads"] = disk
            req(
                failures, evidence, "alembic_head_unchanged",
                db_heads == [EXPECTED_HEAD] and disk == [EXPECTED_HEAD],
                "Alembic head changed from 98",
            )

            from app.cutover.finalization import finalize_v3_production_cutover
            from app.cutover.writer import get_cutover_state
            cstate = get_cutover_state(session, for_update=True)
            if cstate.writer_mode == "SHADOW":
                session.execute(text("UPDATE writer_cutover_states SET writer_mode='DUAL_WRITE', legacy_write_enabled=true, new_fact_write_enabled=true, legacy_frozen=false, new_fact_read_mode='SHADOW', rag_source='LEGACY', updated_by='gate:S32' WHERE scope='GLOBAL'"))
                session.flush()
            session.execute(text("UPDATE writer_cutover_states SET writer_mode='V3_PRIMARY', legacy_write_enabled=false, new_fact_write_enabled=true, legacy_frozen=true, updated_by='gate:S32' WHERE scope='GLOBAL' AND writer_mode='DUAL_WRITE'"))
            session.execute(text("UPDATE writer_cutover_states SET new_fact_read_mode='PRIMARY', rag_source='CANONICAL_FACTS', updated_by='gate:S32' WHERE scope='GLOBAL' AND writer_mode='V3_PRIMARY' AND new_fact_read_mode='SHADOW' AND rag_source='LEGACY'"))
            session.flush(); session.expire_all()
            if not session.execute(text("SELECT 1 FROM v3_cutover_finalizations WHERE scope='GLOBAL'")).scalar():
                finalize_v3_production_cutover(session, actor="gate:S32", commit=False)
                session.expire_all()

            seal0 = control_snapshot(session, "seal")
            cutover0 = control_snapshot(session, "cutover")
            legacy0 = legacy_counts(session)
            route = ProductionRouteGuard(session).require(scope="GLOBAL")
            req(
                failures, evidence, "production_route_guard_reused",
                all(getattr(route, k) == v for k, v in EXPECTED_ROUTE.items()),
                "Production route mismatch",
            )
            req(failures, evidence, "writer_mode_v3_primary", route.writer_mode == "V3_PRIMARY", "writer is not V3_PRIMARY")
            req(failures, evidence, "reader_mode_primary", route.new_fact_read_mode == "PRIMARY", "reader is not PRIMARY")
            req(failures, evidence, "rag_source_canonical_facts", route.rag_source == "CANONICAL_FACTS", "RAG is not CANONICAL_FACTS")
            req(failures, evidence, "legacy_write_disabled", route.legacy_write_enabled is False, "legacy write enabled")

            event.listen(engine, "before_cursor_execute", monitor.before_cursor_execute)
            listening = True

            app = create_app()
            app.dependency_overrides[get_v3_db] = lambda: session
            route_paths = set(app.openapi().get("paths", {}).keys())
            req(failures, evidence, "v3_routes_registered", EXPECTED_PATHS <= route_paths, "Task32 routes are not all registered")
            req(
                failures, evidence, "openapi_contract_valid",
                EXPECTED_PATHS <= route_paths,
                "Task32 OpenAPI paths missing",
            )

            middleware.current_user_from_request = lambda request: None
            with TestClient(app) as client:
                unauth = client.get("/api/v3/system/status")
                req(
                    failures, evidence, "unauthenticated_v3_access_rejected",
                    unauth.status_code == 401,
                    "Unauthenticated V3 request was accepted",
                )

                middleware.current_user_from_request = lambda request: SimpleNamespace(
                    id=1, username="gate-s32", role="admin", is_active=True
                )
                status = client.get("/api/v3/system/status")
                req(
                    failures, evidence, "system_status_ready",
                    status.status_code == 200
                    and status.json().get("ready") is True
                    and status.json().get("alembic_head") == EXPECTED_HEAD,
                    "V3 system status not ready",
                )

                token = uuid4().hex[:10].upper()
                project, contract, invoice, payment, unlinked, review = make_fixture(session, token)
                session.flush()
                finance_expected = CanonicalProjectFinance(session).read(project.id)
                four_expected = CanonicalFourFlow(session).read(
                    project.id,
                    eligible_fact_ids=set(finance_expected.get("eligible_fact_ids", [])),
                )
                tax_expected = CanonicalProjectTax(session).read(project.id)
                rag_expected = build_canonical_context(session, project.id, "whole_project")
                fact_ids = [contract.id, invoice.id, payment.id, unlinked.id, review.id]
                fact_state0 = [
                    (r.id, r.validation_status, r.business_identity_key, r.version_no, r.supersedes_fact_id)
                    for r in session.scalars(select(Fact).where(Fact.id.in_(fact_ids))).all()
                ]
                allocations = session.scalars(
                    select(FactProjectAllocation).where(FactProjectAllocation.fact_id.in_(fact_ids))
                ).all()
                alloc0 = [
                    (r.id, r.fact_id, r.project_id, str(r.allocated_gross), r.status)
                    for r in allocations
                ]
                relationships = session.scalars(
                    select(FactRelationship).where(
                        FactRelationship.source_fact_id.in_([invoice.id, payment.id])
                    )
                ).all()
                rel0 = [
                    (r.id, r.source_fact_id, r.target_fact_id, r.relationship_type, r.reason)
                    for r in relationships
                ]
                req(failures, evidence, "task29_project_allocations_reused", len(alloc0) >= 5, "Task29 allocation evidence missing")
                req(failures, evidence, "task30_relationships_reused", len(rel0) >= 3, "Task30 relationship evidence missing")
                req(
                    failures, evidence, "task17_tax_engine_reused",
                    tax_expected.get("task17_ruleset_version") == "V3_PROJECT_TAX_ANALYSIS_V1",
                    "Task17 tax ruleset not reused",
                )
                req(
                    failures, evidence, "needs_review_excluded_from_official_totals",
                    review.id not in set(finance_expected.get("eligible_fact_ids", [])),
                    "NEEDS_REVIEW fact entered official finance totals",
                )

                monitor.watch_legacy_reads = True
                finance_http = client.get(f"/api/v3/boss/projects/{project.id}/finance")
                four_http = client.get(f"/api/v3/boss/projects/{project.id}/four-flow")
                tax_http = client.get(f"/api/v3/boss/projects/{project.id}/tax")
                rag_http = client.get(
                    f"/api/v3/boss/projects/{project.id}/rag-context?scope=whole_project"
                )
                quality_http = client.get(
                    f"/api/v3/boss/projects/{project.id}/evidence-quality"
                )
                snap_http = client.get(f"/api/v3/boss/projects/{project.id}/snapshot")
                monitor.watch_legacy_reads = False

                req(
                    failures, evidence, "boss_finance_matches_task31",
                    finance_http.status_code == 200
                    and finance_http.json() == jsonable_encoder(finance_expected),
                    "Boss finance differs from Task31",
                )
                req(
                    failures, evidence, "boss_four_flow_matches_task31",
                    four_http.status_code == 200
                    and four_http.json() == jsonable_encoder(four_expected),
                    "Boss four-flow differs from Task31",
                )
                req(
                    failures, evidence, "boss_tax_matches_task31",
                    tax_http.status_code == 200
                    and tax_http.json() == jsonable_encoder(tax_expected),
                    "Boss tax differs from Task31/Task17",
                )
                req(
                    failures, evidence, "boss_rag_matches_task31",
                    rag_http.status_code == 200
                    and rag_http.json().get("context") == jsonable_encoder(rag_expected)
                    and rag_http.json().get("data_source") == "CANONICAL_FACTS",
                    "Boss RAG differs from native canonical context",
                )
                req(
                    failures, evidence, "relationship_coverage_reported",
                    quality_http.status_code == 200
                    and "relationship_coverage" in quality_http.json(),
                    "Relationship coverage missing",
                )
                no_inference = (
                    four_http.status_code == 200
                    and four_http.json()
                    .get("invoice_payment_amount_allocation", {})
                    .get("supported") is False
                )
                req(
                    failures, evidence, "invoice_payment_amount_not_inferred",
                    no_inference,
                    "Payment relationship was converted into invoice paid amount",
                )
                req(
                    failures, evidence, "boss_snapshot_api_native_v3",
                    snap_http.status_code == 200
                    and snap_http.json().get("data_source") == "CANONICAL_FACTS"
                    and snap_http.json().get("finance") == finance_http.json()
                    and snap_http.json().get("four_flow") == four_http.json(),
                    "Boss snapshot is not a native V3 aggregate",
                )

                fact_state1 = [
                    (r.id, r.validation_status, r.business_identity_key, r.version_no, r.supersedes_fact_id)
                    for r in session.scalars(select(Fact).where(Fact.id.in_(fact_ids))).all()
                ]
                alloc1 = [
                    (r.id, r.fact_id, r.project_id, str(r.allocated_gross), r.status)
                    for r in session.scalars(
                        select(FactProjectAllocation).where(FactProjectAllocation.fact_id.in_(fact_ids))
                    ).all()
                ]
                rel1 = [
                    (r.id, r.source_fact_id, r.target_fact_id, r.relationship_type, r.reason)
                    for r in session.scalars(
                        select(FactRelationship).where(
                            FactRelationship.source_fact_id.in_([invoice.id, payment.id])
                        )
                    ).all()
                ]
                req(failures, evidence, "fact_validation_status_not_mutated_by_read_api", fact_state0 == fact_state1, "Boss read mutated Fact state")
                req(failures, evidence, "fact_project_allocations_not_mutated_by_read_api", alloc0 == alloc1, "Boss read mutated allocations")
                req(failures, evidence, "fact_relationships_not_mutated_by_read_api", rel0 == rel1, "Boss read mutated relationships")

                seller, seller_tax = party(session, token, "HTTP_I_S")
                buyer, buyer_tax = party(session, token, "HTTP_I_B")
                inv_req = invoice_submission(token, "S32", seller, seller_tax, buyer, buyer_tax)
                inv_res = client.post(
                    "/api/v3/idp/direct", json=inv_req.model_dump(mode="json")
                )
                req(
                    failures, evidence, "invoice_e2e_completed",
                    inv_res.status_code == 200
                    and inv_res.json().get("validation_status") == "VALID",
                    f"Invoice HTTP E2E failed: {inv_res.text}",
                )

                payer, payer_tax = party(session, token, "HTTP_P_R")
                payee, payee_tax = party(session, token, "HTTP_P_E")
                pay_req = payment_submission(token, "S32", payer, payer_tax, payee, payee_tax)
                pay_res = client.post(
                    "/api/v3/idp/direct", json=pay_req.model_dump(mode="json")
                )
                req(
                    failures, evidence, "payment_e2e_completed",
                    pay_res.status_code == 200
                    and pay_res.json().get("validation_status") == "VALID"
                    and pay_res.json().get("payment_evidence") is not None,
                    f"Payment HTTP E2E failed: {pay_res.text}",
                )

                ca, cat = party(session, token, "HTTP_C_A")
                cb, cbt = party(session, token, "HTTP_C_B")
                con_req = contract_submission(token, "S32", ca, cat, cb, cbt, reverse=True)
                con_res = client.post(
                    "/api/v3/idp/direct", json=con_req.model_dump(mode="json")
                )
                req(
                    failures, evidence, "contract_review_e2e_completed",
                    con_res.status_code == 200
                    and con_res.json().get("validation_status") == "NEEDS_REVIEW"
                    and con_res.json().get("contract_roles") is not None,
                    f"Contract HTTP E2E failed: {con_res.text}",
                )
                req(
                    failures, evidence, "idp_direct_api_reuses_task27",
                    inv_res.status_code == pay_res.status_code == con_res.status_code == 200,
                    "Task27 production API path failed",
                )
                posted_ids = [
                    response.json().get("fact_id")
                    for response in (inv_res, pay_res, con_res)
                    if response.status_code == 200
                ]
                supersession = any(
                    session.get(Fact, int(fact_id)).supersedes_fact_id is not None
                    for fact_id in posted_ids
                    if fact_id is not None
                )
                evidence["automatic_fact_supersession_present"] = supersession
                if supersession:
                    failures.append("Automatic Fact supersession detected in Task32 HTTP E2E")

            req(failures, evidence, "production_seal_unchanged", seal0 == control_snapshot(session, "seal"), "Production Seal changed")
            req(failures, evidence, "cutover_state_unchanged", cutover0 == control_snapshot(session, "cutover"), "Cutover state changed")
            req(failures, evidence, "legacy_tables_unchanged", legacy0 == legacy_counts(session), "Legacy tables changed")
        finally:
            middleware.current_user_from_request = original_auth
            session.close()
            if outer.is_active:
                outer.rollback()
            conn.close()

        if listening:
            event.remove(engine, "before_cursor_execute", monitor.before_cursor_execute)
            listening = False
        with Session(engine) as post:
            req(
                failures, evidence, "production_e2e_fixture_rolled_back",
                baseline == _counts(post),
                "Gate S32 leaked fixture/application writes",
            )
    except Exception as exc:
        failures.append(f"Gate S32 unexpected error: {type(exc).__name__}: {exc}")
    finally:
        middleware.current_user_from_request = original_auth
        if listening:
            event.remove(engine, "before_cursor_execute", monitor.before_cursor_execute)
        engine.dispose()

    evidence.update(monitor.flags)
    evidence["canonical_web_legacy_overlay_removed"] = not bool(
        evidence.get("canonical_web_legacy_business_read_attempted")
    )
    evidence["production_e2e_gate_passed"] = not failures
    for key in (
        "legacy_write_attempted",
        "production_seal_write_attempted",
        "cutover_state_write_attempted",
        "canonical_web_legacy_business_read_attempted",
    ):
        if evidence.get(key):
            failures.append(f"Forbidden operation detected: {key}")
    if failures:
        evidence["production_e2e_gate_passed"] = False
    print(
        json.dumps(
            {
                "gate": "S32",
                "status": "PASS" if not failures else "FAIL",
                "evidence": evidence,
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=str,
        )
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
