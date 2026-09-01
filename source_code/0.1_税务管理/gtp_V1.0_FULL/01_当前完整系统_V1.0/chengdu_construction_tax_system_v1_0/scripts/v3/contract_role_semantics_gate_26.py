#!/usr/bin/env python3
"""Gate S26 — Contract Role & Semantic Completion."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.integration.idp_canonical.contract_role_resolver import (
    CONTRACT_ROLE_RULESET_V1,
)
from app.integration.idp_canonical.contract_role_schemas import (
    ContractRoleCompletionRequest,
    ContractRoleEvidenceInput,
)
from app.integration.idp_canonical.contract_role_service import ContractRoleService
from app.integration.idp_canonical.schemas import CanonicalIngestRequest
from app.integration.idp_canonical.service import CanonicalIngestService
from app.v3_contract_models import ContractFact
from app.v3_contract_role_models import ContractRoleEvidence, ContractRoleResolution
from app.v3_fact_models import Fact
from app.v3_integration_models import CanonicalIngestReceipt
from app.v3_party_models import Party, PartyIdentifier


EXPECTED_HEAD = "97_v3_contract_role_semantics"
ROOT = Path(__file__).resolve().parents[2]


def _database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("Gate S26 is PostgreSQL-only")
    return value


def _disk_heads() -> list[str]:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    return sorted(ScriptDirectory.from_config(cfg).get_heads())


def _seal_snapshot(session: Session) -> list[dict]:
    rows = session.execute(
        text(
            """
            SELECT scope, finalized_by, finalized_at, state_snapshot, evidence_snapshot
            FROM v3_cutover_finalizations
            ORDER BY scope
            """
        )
    ).mappings().all()
    return [
        {
            "scope": row["scope"],
            "finalized_by": row["finalized_by"],
            "finalized_at": str(row["finalized_at"]),
            "state_snapshot": deepcopy(row["state_snapshot"]),
            "evidence_snapshot": deepcopy(row["evidence_snapshot"]),
        }
        for row in rows
    ]


def _cutover_snapshot(session: Session) -> list[dict]:
    rows = session.execute(
        text(
            """
            SELECT scope, writer_mode, legacy_write_enabled,
                   new_fact_write_enabled, legacy_frozen,
                   new_fact_read_mode, rag_source, updated_by
            FROM writer_cutover_states
            ORDER BY scope
            """
        )
    ).mappings().all()
    return [dict(row) for row in rows]


def _legacy_snapshot(session: Session) -> dict[str, int | None]:
    result: dict[str, int | None] = {}
    for table_name in ("contracts_v3", "invoices_v3", "invoices"):
        exists = session.execute(
            text("SELECT to_regclass(:name)"),
            {"name": f"public.{table_name}"},
        ).scalar_one()
        result[table_name] = (
            int(
                session.execute(
                    text(f'SELECT count(*) FROM "{table_name}"')
                ).scalar_one()
            )
            if exists is not None
            else None
        )
    return result


def _require(failures: list[str], evidence: dict, key: str, value: bool, message: str) -> None:
    evidence[key] = bool(value)
    if not value:
        failures.append(message)


def _sha(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _party(session: Session, token: str, side: str) -> tuple[Party, str]:
    tax_id = "91" + uuid4().hex[:16].upper()
    party = Party(
        code=f"S26{side}{token}",
        name=f"S26 Party {side} {token}",
        short_name=f"S26 {side} {token}",
        party_type="external",
        active=True,
    )
    session.add(party)
    session.flush()
    session.add(
        PartyIdentifier(
            party_id=int(party.id),
            identifier_type="TAX_REGISTRATION_ID",
            identifier_value=tax_id,
            source_system="GATE_S26",
            active=True,
        )
    )
    session.flush()
    return party, tax_id


def _task24_contract(
    session: Session,
    *,
    token: str,
    suffix: str,
    party_a: Party,
    tax_a: str,
    party_b: Party,
    tax_b: str,
) -> tuple[CanonicalIngestRequest, Fact, ContractFact]:
    request = CanonicalIngestRequest(
        source_system="IDP",
        source_document_id=f"S26-DOC-{suffix}-{token}",
        source_extraction_id=f"S26-EXT-{suffix}-{token}",
        document_sha256=_sha(f"S26:{suffix}:{token}"),
        document_type="contract",
        review_status="approved",
        approved_by="gate:S26",
        extraction_model="gate-fixture",
        extraction_model_version="1",
        confidence={"contract_no": 0.99},
        data={
            "contract_no": f"S26-HT-{suffix}-{token}",
            "contract_name": f"S26 Contract {suffix}",
            "party_a": {"name": party_a.name, "credit_code": None, "tax_id": tax_a, "address": None, "legal_representative": None},
            "party_b": {"name": party_b.name, "credit_code": None, "tax_id": tax_b, "address": None, "legal_representative": None},
            "project_name": None,
            "sign_date": "2026-09-01",
            "currency": "CNY",
            "amount_tax_included": "113.00",
            "amount_tax_excluded": "100.00",
            "tax_amount": "13.00",
            "tax_rate": "0.13",
            "payment_terms": [],
            "contract_start_date": None,
            "contract_end_date": None,
            "warranty_period": None,
            "bank": None,
            "bank_account": None,
            "confidence": {"contract_no": 0.99},
            "sources": {},
        },
    )
    result = CanonicalIngestService(session).ingest(request, commit=False)
    if result.status != "CREATED":
        raise AssertionError(f"Task24 fixture expected CREATED, got {result.status}")
    fact = session.get(Fact, int(result.fact_id))
    contract = session.get(ContractFact, int(result.fact_id))
    if fact is None or contract is None:
        raise AssertionError("Task24 Contract fixture missing")
    return request, fact, contract


def _request(source: CanonicalIngestRequest, evidences: list[ContractRoleEvidenceInput]) -> ContractRoleCompletionRequest:
    return ContractRoleCompletionRequest(
        source_document_id=source.source_document_id,
        source_extraction_id=source.source_extraction_id,
        document_sha256=source.document_sha256,
        submitted_by="gate:S26",
        evidences=evidences,
    )


def _a_buyer() -> ContractRoleEvidenceInput:
    return ContractRoleEvidenceInput(source_party_role="PARTY_A", legal_role_label="甲方（发包人）", evidence_text="甲方（发包人）负责工程发包。", page_no=1, confidence=0.99)


def _a_seller() -> ContractRoleEvidenceInput:
    return ContractRoleEvidenceInput(source_party_role="PARTY_A", legal_role_label="甲方（承包人）", evidence_text="甲方（承包人）负责工程施工。", page_no=1, confidence=0.99)


def _b_buyer() -> ContractRoleEvidenceInput:
    return ContractRoleEvidenceInput(source_party_role="PARTY_B", legal_role_label="乙方（发包人）", evidence_text="乙方（发包人）负责工程发包。", page_no=1, confidence=0.99)


def _b_seller() -> ContractRoleEvidenceInput:
    return ContractRoleEvidenceInput(source_party_role="PARTY_B", legal_role_label="乙方（承包人）", evidence_text="乙方（承包人）负责工程施工。", page_no=1, confidence=0.99)


def main() -> int:
    failures: list[str] = []
    evidence: dict = {
        "gate": "S26",
        "alembic_disk_heads": _disk_heads(),
        "legacy_write_attempted": False,
        "production_seal_write_attempted": False,
        "new_fact_created": False,
        "contract_supersession_present": False,
    }

    engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
    token = uuid4().hex[:10].upper()

    with Session(engine) as session:
        outer = session.begin()
        try:
            evidence["database"] = session.execute(text("SELECT current_database()" )).scalar_one()
            db_heads = sorted(row[0] for row in session.execute(text("SELECT version_num FROM alembic_version_tax")).all())
            evidence["alembic_db_heads"] = db_heads
            if db_heads != [EXPECTED_HEAD]:
                failures.append(f"DB heads={db_heads}, expected={[EXPECTED_HEAD]}")
            if evidence["alembic_disk_heads"] != [EXPECTED_HEAD]:
                failures.append("disk Alembic head is not Task26")

            _require(failures, evidence, "role_evidence_table_present", bool(session.execute(text("SELECT to_regclass('public.contract_role_evidence') IS NOT NULL")).scalar_one()), "contract_role_evidence missing")
            _require(failures, evidence, "role_resolution_table_present", bool(session.execute(text("SELECT to_regclass('public.contract_role_resolutions') IS NOT NULL")).scalar_one()), "contract_role_resolutions missing")

            seal_before = _seal_snapshot(session)
            cutover_before = _cutover_snapshot(session)
            legacy_before = _legacy_snapshot(session)
            _require(failures, evidence, "production_seal_present", any(row["scope"] == "GLOBAL" for row in seal_before), "GLOBAL Production Seal missing")

            party_a, tax_a = _party(session, token, "A")
            party_b, tax_b = _party(session, token, "B")
            service = ContractRoleService(session)

            source, f, c = _task24_contract(session, token=token, suffix="AB", party_a=party_a, tax_a=tax_a, party_b=party_b, tax_b=tax_b)
            original = (int(f.id), str(f.business_identity_key), int(f.version_no), f.supersedes_fact_id, c.contract_category)
            _require(failures, evidence, "task24_contract_role_neutral_baseline", c.buyer_party_id is None and c.seller_party_id is None and f.validation_status == "NEEDS_REVIEW", "Task24 Contract baseline not role-neutral")
            count_before = int(session.execute(select(func.count(Fact.id))).scalar_one())
            result = service.complete(_request(source, [_a_buyer(), _b_seller()]), commit=False)
            session.refresh(f); session.refresh(c)
            count_after = int(session.execute(select(func.count(Fact.id))).scalar_one())
            _require(failures, evidence, "explicit_a_buyer_b_seller_resolved", result.resolution_status == "RESOLVED" and c.buyer_party_id == party_a.id and c.seller_party_id == party_b.id, "A=BUYER/B=SELLER not resolved")
            _require(failures, evidence, "resolved_contract_remains_needs_review", f.validation_status == "NEEDS_REVIEW", "Task26 promoted Contract beyond NEEDS_REVIEW")
            evidence["new_fact_created"] = count_after != count_before
            if evidence["new_fact_created"]: failures.append("Task26 created new Fact")
            _require(failures, evidence, "task24_contract_fact_id_unchanged", int(f.id) == original[0], "Fact id changed")
            _require(failures, evidence, "task24_contract_identity_unchanged", str(f.business_identity_key) == original[1] and int(f.version_no) == original[2], "business identity/version changed")
            evidence["contract_supersession_present"] = f.supersedes_fact_id is not None
            if evidence["contract_supersession_present"]: failures.append("Fact supersession present")
            _require(failures, evidence, "buyer_seller_distinct", c.buyer_party_id != c.seller_party_id, "buyer/seller not distinct")
            _require(failures, evidence, "contract_category_unchanged", c.contract_category == original[4], "contract_category changed")

            e_before = int(session.execute(select(func.count(ContractRoleEvidence.id)).where(ContractRoleEvidence.fact_id == f.id)).scalar_one())
            r_before = int(session.execute(select(func.count(ContractRoleResolution.id)).where(ContractRoleResolution.fact_id == f.id)).scalar_one())
            retry = service.complete(_request(source, [_a_buyer(), _b_seller()]), commit=False)
            e_after = int(session.execute(select(func.count(ContractRoleEvidence.id)).where(ContractRoleEvidence.fact_id == f.id)).scalar_one())
            r_after = int(session.execute(select(func.count(ContractRoleResolution.id)).where(ContractRoleResolution.fact_id == f.id)).scalar_one())
            _require(failures, evidence, "duplicate_role_evidence_idempotent", retry.outcome == "NOOP" and retry.evidence_outcome == "NOOP" and retry.resolution_outcome == "NOOP" and e_before == e_after == 2 and r_before == r_after == 1, "Task26 retry not idempotent")

            source_ba, f_ba, c_ba = _task24_contract(session, token=token, suffix="BA", party_a=party_a, tax_a=tax_a, party_b=party_b, tax_b=tax_b)
            reverse = service.complete(_request(source_ba, [_a_seller(), _b_buyer()]), commit=False)
            session.refresh(c_ba)
            _require(failures, evidence, "explicit_a_seller_b_buyer_resolved", reverse.resolution_status == "RESOLVED" and c_ba.buyer_party_id == party_b.id and c_ba.seller_party_id == party_a.id and f_ba.validation_status == "NEEDS_REVIEW", "reverse roles not resolved")

            source_pos, _, c_pos = _task24_contract(session, token=token, suffix="POS", party_a=party_a, tax_a=tax_a, party_b=party_b, tax_b=tax_b)
            positional = service.complete(_request(source_pos, [ContractRoleEvidenceInput(source_party_role="PARTY_A", legal_role_label="甲方", evidence_text="合同列示甲方。"), ContractRoleEvidenceInput(source_party_role="PARTY_B", legal_role_label="乙方", evidence_text="合同列示乙方。")]), commit=False)
            session.refresh(c_pos)
            _require(failures, evidence, "party_a_not_assumed_buyer", positional.resolution_status == "NEEDS_REVIEW" and c_pos.buyer_party_id is None, "PARTY_A assumed BUYER")
            _require(failures, evidence, "party_b_not_assumed_seller", positional.resolution_status == "NEEDS_REVIEW" and c_pos.seller_party_id is None, "PARTY_B assumed SELLER")

            source_missing, f_missing, c_missing = _task24_contract(session, token=token, suffix="MISS", party_a=party_a, tax_a=tax_a, party_b=party_b, tax_b=tax_b)
            missing = service.complete(_request(source_missing, [_a_buyer()]), commit=False)
            session.refresh(f_missing); session.refresh(c_missing)
            _require(failures, evidence, "missing_role_evidence_needs_review", missing.resolution_status == "NEEDS_REVIEW" and missing.reason_code == "ROLE_EVIDENCE_INSUFFICIENT" and c_missing.buyer_party_id is None and c_missing.seller_party_id is None and f_missing.validation_status == "NEEDS_REVIEW", "missing evidence did not fail closed")

            source_conflict, f_conflict, c_conflict = _task24_contract(session, token=token, suffix="CONFLICT", party_a=party_a, tax_a=tax_a, party_b=party_b, tax_b=tax_b)
            service.complete(_request(source_conflict, [_a_buyer(), _b_seller()]), commit=False)
            conflict = service.complete(_request(source_conflict, [ContractRoleEvidenceInput(source_party_role="PARTY_A", legal_role_label="甲方（供应商）", evidence_text="甲方（供应商）负责供货。", page_no=2)]), commit=False)
            session.refresh(f_conflict); session.refresh(c_conflict)
            history = list(session.execute(select(ContractRoleResolution).where(ContractRoleResolution.fact_id == f_conflict.id).order_by(ContractRoleResolution.resolution_seq)).scalars().all())
            _require(failures, evidence, "conflicting_role_evidence_fail_closed", conflict.resolution_status == "NEEDS_REVIEW" and conflict.reason_code == "ROLE_EVIDENCE_CONFLICT" and c_conflict.buyer_party_id is None and c_conflict.seller_party_id is None and f_conflict.validation_status == "NEEDS_REVIEW", "conflict did not fail closed")
            _require(failures, evidence, "resolution_history_preserved", len(history) == 2 and history[0].resolution_seq == 1 and history[0].resolution_status == "RESOLVED" and not history[0].is_current and history[1].resolution_seq == 2 and history[1].resolution_status == "NEEDS_REVIEW" and history[1].is_current and history[1].supersedes_resolution_id == history[0].id, "resolution history not preserved")

            audit_rows = list(session.execute(select(ContractRoleEvidence).where(ContractRoleEvidence.fact_id == f.id)).scalars().all())
            current_resolution = session.execute(select(ContractRoleResolution).where(ContractRoleResolution.fact_id == f.id, ContractRoleResolution.is_current.is_(True))).scalar_one_or_none()
            _require(failures, evidence, "role_resolution_auditable", len(audit_rows) == 2 and current_resolution is not None and current_resolution.resolution_status == "RESOLVED", "audit trail incomplete")
            _require(failures, evidence, "role_ruleset_versioned", all(row.ruleset_version == CONTRACT_ROLE_RULESET_V1 for row in audit_rows) and current_resolution is not None and current_resolution.ruleset_version == CONTRACT_ROLE_RULESET_V1, "ruleset not versioned")

            source_atomic, f_atomic, _ = _task24_contract(session, token=token, suffix="ATOMIC", party_a=party_a, tax_a=tax_a, party_b=party_b, tax_b=tax_b)
            atomic_id = int(f_atomic.id)
            savepoint = session.begin_nested()
            atomic_result = service.complete(_request(source_atomic, [_a_buyer(), _b_seller()]), commit=False)
            if atomic_result.resolution_status != "RESOLVED": failures.append("atomic fixture did not resolve")
            savepoint.rollback(); session.expire_all()
            atomic_contract = session.get(ContractFact, atomic_id)
            atomic_e = int(session.execute(select(func.count(ContractRoleEvidence.id)).where(ContractRoleEvidence.fact_id == atomic_id)).scalar_one())
            atomic_r = int(session.execute(select(func.count(ContractRoleResolution.id)).where(ContractRoleResolution.fact_id == atomic_id)).scalar_one())
            _require(failures, evidence, "atomic_rollback_verified", atomic_e == 0 and atomic_r == 0 and atomic_contract is not None and atomic_contract.buyer_party_id is None and atomic_contract.seller_party_id is None, "Task26 writes escaped rollback")

            seal_after = _seal_snapshot(session)
            cutover_after = _cutover_snapshot(session)
            legacy_after = _legacy_snapshot(session)
            _require(failures, evidence, "production_seal_unchanged", seal_before == seal_after, "Production Seal changed")
            _require(failures, evidence, "cutover_state_unchanged", cutover_before == cutover_after, "cutover state changed")
            evidence["legacy_write_attempted"] = legacy_before != legacy_after
            if evidence["legacy_write_attempted"]: failures.append("Legacy business tables changed")
        finally:
            if outer.is_active:
                outer.rollback()

    payload = {"gate": "S26", "status": "PASS" if not failures else "FAIL", "evidence": evidence, "failures": failures}
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
