#!/usr/bin/env python3
"""Task24 Gate S24 for the IDP -> Canonical intake boundary."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.cutover.finalization import finalize_v3_production_cutover
from app.cutover.writer import get_cutover_state
from app.domain.invoice.identity import (
    build_invoice_business_identity_key,
    build_invoice_identity_key,
)
from app.integration.idp_canonical import (
    CanonicalIngestRequest,
    CanonicalIngestRejected,
    CanonicalIngestService,
)
from app.v3_contract_models import ContractFact
from app.v3_fact_models import (
    Fact,
    FactProvenance,
    InvoiceFact,
    InvoiceLine,
)
from app.v3_integration_models import CanonicalIngestReceipt
from app.v3_party_models import Party, PartyIdentifier


EXPECTED_HEAD = "95_v3_idp_canonical_ingest"
ROOT = Path(__file__).resolve().parents[2]


def _url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")

    backend = make_url(value).get_backend_name()
    if backend not in {
        "postgresql",
        "postgres",
    }:
        raise SystemExit("S24 is PostgreSQL-only")

    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value[len("postgresql://"):]
    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value[len("postgres://"):]
    return value


def _disk_heads() -> list[str]:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option(
        "script_location",
        str(ROOT / "alembic"),
    )
    return sorted(
        ScriptDirectory.from_config(cfg).get_heads()
    )


def _seal_snapshot(session: Session) -> list[dict]:
    rows = session.execute(
        text(
            """
            SELECT
                scope,
                finalized_by,
                finalized_at,
                state_snapshot,
                evidence_snapshot
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
            "state_snapshot": deepcopy(
                row["state_snapshot"]
            ),
            "evidence_snapshot": deepcopy(
                row["evidence_snapshot"]
            ),
        }
        for row in rows
    ]


def _cutover_snapshot(session: Session) -> list[dict]:
    rows = session.execute(
        text(
            """
            SELECT
                scope,
                writer_mode,
                legacy_write_enabled,
                new_fact_write_enabled,
                legacy_frozen,
                new_fact_read_mode,
                rag_source,
                updated_by
            FROM writer_cutover_states
            ORDER BY scope
            """
        )
    ).mappings().all()

    return [dict(row) for row in rows]


def _table_count(
    session: Session,
    table_name: str,
) -> int | None:
    exists = session.execute(
        text("SELECT to_regclass(:name)"),
        {"name": f"public.{table_name}"},
    ).scalar_one()

    if exists is None:
        return None

    if table_name not in {
        "invoices",
        "invoices_v3",
        "contracts_v3",
    }:
        raise ValueError("unexpected table count request")

    return int(
        session.execute(
            text(f'SELECT count(*) FROM "{table_name}"')
        ).scalar_one()
    )


def _legacy_counts(session: Session) -> dict[str, int | None]:
    return {
        table: _table_count(session, table)
        for table in (
            "invoices",
            "invoices_v3",
            "contracts_v3",
        )
    }


def _party(
    session: Session,
    *,
    code: str,
    name: str,
    tax_identity: str,
) -> Party:
    party = Party(
        code=code,
        name=name,
        short_name=name,
        party_type="external",
        active=True,
    )
    session.add(party)
    session.flush()

    session.add(
        PartyIdentifier(
            party_id=int(party.id),
            identifier_type="TAX_REGISTRATION_ID",
            identifier_value=tax_identity,
            source_system="GATE_S24",
            active=True,
        )
    )
    session.flush()

    return party


def _request_sha(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _invoice_request(
    *,
    token: str,
    extraction_id: str,
    document_id: str,
    seller_name: str,
    seller_tax: str,
    buyer_name: str,
    buyer_tax: str,
    invoice_no: str,
    net: str = "100.00",
    vat: str = "13.00",
    gross: str = "113.00",
    review_status: str = "approved",
    seller_credit: str | None = None,
    seller_tax_field: str | None = None,
) -> CanonicalIngestRequest:
    return CanonicalIngestRequest(
        source_system="IDP",
        source_document_id=document_id,
        source_extraction_id=extraction_id,
        document_sha256=_request_sha(
            f"S24:{token}:{document_id}"
        ),
        document_type="invoice",
        review_status=review_status,
        approved_by="gate:S24",
        extraction_model="gate-fixture",
        extraction_model_version="1",
        confidence={
            "invoice_no": 0.99,
            "amount_including_tax": 0.99,
        },
        data={
            "invoice_type": "增值税电子专用发票",
            "invoice_code": None,
            "invoice_no": invoice_no,
            "invoice_date": "2026-08-31",
            "buyer": {
                "name": buyer_name,
                "credit_code": buyer_tax,
                "tax_id": buyer_tax,
                "address": None,
                "legal_representative": None,
            },
            "seller": {
                "name": seller_name,
                "credit_code": (
                    seller_credit
                    if seller_credit is not None
                    else seller_tax
                ),
                "tax_id": (
                    seller_tax_field
                    if seller_tax_field is not None
                    else seller_tax
                ),
                "address": None,
                "legal_representative": None,
            },
            "amount_excluding_tax": net,
            "tax_amount": vat,
            "amount_including_tax": gross,
            "tax_rate": "0.13",
            "currency": "CNY",
            "check_code": None,
            "confidence": {
                "invoice_no": 0.99,
            },
            "sources": {},
        },
    )


def _contract_request(
    *,
    token: str,
    party_a_name: str,
    party_a_tax: str,
    party_b_name: str,
    party_b_tax: str,
) -> CanonicalIngestRequest:
    return CanonicalIngestRequest(
        source_system="IDP",
        source_document_id=f"S24-CONTRACT-DOC-{token}",
        source_extraction_id=(
            f"S24-CONTRACT-EXT-{token}"
        ),
        document_sha256=_request_sha(
            f"S24-CONTRACT:{token}"
        ),
        document_type="contract",
        review_status="approved",
        approved_by="gate:S24",
        extraction_model="gate-fixture",
        extraction_model_version="1",
        confidence={"contract_no": 0.99},
        data={
            "contract_no": f"S24-CONTRACT-{token}",
            "contract_name": "S24 Gate Contract",
            "party_a": {
                "name": party_a_name,
                "credit_code": party_a_tax,
                "tax_id": party_a_tax,
                "address": None,
                "legal_representative": None,
            },
            "party_b": {
                "name": party_b_name,
                "credit_code": party_b_tax,
                "tax_id": party_b_tax,
                "address": None,
                "legal_representative": None,
            },
            "project_name": "S24",
            "sign_date": "2026-08-31",
            "currency": "CNY",
            "amount_tax_included": "1000.00",
            "amount_tax_excluded": "917.43",
            "tax_amount": "82.57",
            "tax_rate": "0.09",
            "payment_terms": [],
            "contract_start_date": None,
            "contract_end_date": None,
            "warranty_period": None,
            "bank": None,
            "bank_account": None,
            "confidence": {
                "contract_no": 0.99,
            },
            "sources": {},
        },
    )


def _require(
    failures: list[str],
    evidence: dict,
    key: str,
    value: bool,
    message: str,
) -> None:
    evidence[key] = bool(value)
    if not value:
        failures.append(message)


def main() -> int:
    engine = create_engine(
        _url(),
        future=True,
        pool_pre_ping=True,
    )

    failures: list[str] = []
    evidence: dict = {
        "database": None,
        "alembic_db_heads": [],
        "alembic_disk_heads": _disk_heads(),
    }

    token = uuid4().hex[:12].upper()

    with Session(engine) as session:
        outer = session.begin()

        try:
            evidence["database"] = session.execute(
                text("SELECT current_database()")
            ).scalar_one()

            db_heads = sorted(
                row[0]
                for row in session.execute(
                    text(
                        "SELECT version_num "
                        "FROM alembic_version_tax"
                    )
                ).all()
            )
            evidence["alembic_db_heads"] = db_heads

            if db_heads != [EXPECTED_HEAD]:
                failures.append(
                    f"DB heads={db_heads}, "
                    f"expected={[EXPECTED_HEAD]}"
                )

            if evidence["alembic_disk_heads"] != [
                EXPECTED_HEAD
            ]:
                failures.append(
                    "disk Alembic head does not match Task24"
                )

            receipt_table_present = bool(
                session.execute(
                    text(
                        """
                        SELECT
                            to_regclass(
                                'public.canonical_ingest_receipts'
                            ) IS NOT NULL
                        """
                    )
                ).scalar_one()
            )
            _require(
                failures,
                evidence,
                "receipt_table_present",
                receipt_table_present,
                "canonical_ingest_receipts missing",
            )

            source_unique_present = bool(
                session.execute(
                    text(
                        """
                        SELECT EXISTS (
                            SELECT 1
                            FROM pg_constraint
                            WHERE conname =
                              'uq_canonical_ingest_receipts_source_extraction'
                        )
                        """
                    )
                ).scalar_one()
            )
            _require(
                failures,
                evidence,
                "source_extraction_unique_constraint_present",
                source_unique_present,
                "source extraction unique constraint missing",
            )

            document_index_present = bool(
                session.execute(
                    text(
                        """
                        SELECT EXISTS (
                            SELECT 1
                            FROM pg_indexes
                            WHERE schemaname='public'
                              AND indexname =
                              'ix_canonical_ingest_receipts_document_history'
                        )
                        """
                    )
                ).scalar_one()
            )
            _require(
                failures,
                evidence,
                "document_history_index_present",
                document_index_present,
                "document history index missing",
            )

            state = get_cutover_state(session, for_update=True)
            if state.writer_mode == "SHADOW":
                session.execute(
                    text(
                        "UPDATE writer_cutover_states SET writer_mode='DUAL_WRITE', "
                        "legacy_write_enabled=true, new_fact_write_enabled=true, legacy_frozen=false, "
                        "new_fact_read_mode='SHADOW', rag_source='LEGACY', updated_by='gate:S24' "
                        "WHERE scope='GLOBAL'"
                    )
                )
                session.flush()
            session.execute(
                text(
                    "UPDATE writer_cutover_states SET writer_mode='V3_PRIMARY', "
                    "legacy_write_enabled=false, new_fact_write_enabled=true, legacy_frozen=true, "
                    "updated_by='gate:S24' WHERE scope='GLOBAL' AND writer_mode='DUAL_WRITE'"
                )
            )
            session.execute(
                text(
                    "UPDATE writer_cutover_states SET new_fact_read_mode='PRIMARY', "
                    "rag_source='CANONICAL_FACTS', updated_by='gate:S24' "
                    "WHERE scope='GLOBAL' AND writer_mode='V3_PRIMARY' AND new_fact_read_mode='SHADOW' AND rag_source='LEGACY'"
                )
            )
            session.flush()
            session.expire_all()
            if not session.execute(
                text("SELECT 1 FROM v3_cutover_finalizations WHERE scope='GLOBAL'")
            ).scalar():
                finalize_v3_production_cutover(session, actor="gate:S24", commit=False)
                session.expire_all()

            seal_before = _seal_snapshot(session)
            cutover_before = _cutover_snapshot(session)
            legacy_before = _legacy_counts(session)

            _require(
                failures,
                evidence,
                "production_seal_present",
                any(
                    item["scope"] == "GLOBAL"
                    for item in seal_before
                ),
                "GLOBAL Production Seal is not present",
            )

            seller_tax = (
                "91"
                + uuid4().hex[:16].upper()
            )
            buyer_tax = (
                "91"
                + uuid4().hex[:16].upper()
            )

            seller = _party(
                session,
                code=f"S24SELL{token}",
                name=f"S24 Seller {token}",
                tax_identity=seller_tax,
            )
            buyer = _party(
                session,
                code=f"S24BUY{token}",
                name=f"S24 Buyer {token}",
                tax_identity=buyer_tax,
            )

            service = CanonicalIngestService(session)

            invoice_no = f"S24INV{token}"
            base_request = _invoice_request(
                token=token,
                extraction_id=f"S24-EXT-1-{token}",
                document_id=f"S24-DOC-{token}",
                seller_name=str(seller.name),
                seller_tax=seller_tax,
                buyer_name=str(buyer.name),
                buyer_tax=buyer_tax,
                invoice_no=invoice_no,
            )

            created = service.ingest(
                base_request,
                commit=False,
            )

            _require(
                failures,
                evidence,
                "approved_invoice_ingested",
                created.status == "CREATED",
                "approved Invoice was not CREATED",
            )

            fact = session.get(Fact, created.fact_id)
            invoice = session.get(
                InvoiceFact,
                created.fact_id,
            )

            expected_invoice_identity = (
                build_invoice_identity_key(
                    "DIGITAL_V1",
                    invoice_number=invoice_no,
                )
            )
            expected_business_identity = (
                build_invoice_business_identity_key(
                    expected_invoice_identity
                )
            )

            _require(
                failures,
                evidence,
                "invoice_identity_uses_existing_contract",
                bool(
                    invoice
                    and invoice.invoice_identity_key
                    == expected_invoice_identity
                    and fact
                    and fact.business_identity_key
                    == expected_business_identity
                ),
                "Invoice identity does not use Task07a contract",
            )

            _require(
                failures,
                evidence,
                "identity_version_is_not_fact_version",
                bool(
                    invoice
                    and invoice.invoice_identity_version
                    == "DIGITAL_V1"
                    and fact
                    and fact.version_no == 1
                ),
                "identity version was confused with Fact version",
            )

            _require(
                failures,
                evidence,
                "invoice_type_split_deterministic",
                bool(
                    invoice
                    and invoice.invoice_type is None
                    and invoice.invoice_medium == "DIGITAL"
                    and invoice.invoice_category == "SPECIAL"
                ),
                "invoice_type was not split deterministically",
            )

            provenance = session.execute(
                select(FactProvenance).where(
                    FactProvenance.fact_id
                    == created.fact_id
                )
            ).scalar_one()

            linked_document_count = int(
                session.execute(
                    select(FactProvenance)
                    .where(
                        FactProvenance.fact_id
                        == created.fact_id,
                        FactProvenance.document_id.is_not(
                            None
                        ),
                    )
                ).scalars().all().__len__()
            )

            line_count = int(
                session.execute(
                    select(InvoiceLine).where(
                        InvoiceLine.invoice_fact_id
                        == created.fact_id
                    )
                ).scalars().all().__len__()
            )

            _require(
                failures,
                evidence,
                "fact_provenance_does_not_reference_idp_uuid",
                provenance.document_id is None,
                "IDP identifier was written as SourceDocument FK",
            )

            _require(
                failures,
                evidence,
                "invoice_without_source_document_not_promoted_valid",
                bool(
                    fact
                    and fact.validation_status
                    == "NEEDS_REVIEW"
                    and linked_document_count == 0
                ),
                "Invoice without SourceDocument was promoted VALID",
            )

            _require(
                failures,
                evidence,
                "invoice_without_lines_not_promoted_valid",
                bool(
                    fact
                    and fact.validation_status
                    == "NEEDS_REVIEW"
                    and line_count == 0
                ),
                "Invoice without lines was promoted VALID",
            )

            repeated = service.ingest(
                base_request,
                commit=False,
            )

            same_source_receipt_count = int(
                session.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM canonical_ingest_receipts
                        WHERE source_system='IDP'
                          AND source_extraction_id=:extraction_id
                        """
                    ),
                    {
                        "extraction_id":
                            base_request.source_extraction_id
                    },
                ).scalar_one()
            )

            _require(
                failures,
                evidence,
                "same_source_retry_is_idempotent",
                bool(
                    repeated.fact_id == created.fact_id
                    and repeated.receipt_id
                    == created.receipt_id
                    and same_source_receipt_count == 1
                ),
                "same source extraction was not idempotent",
            )

            second_request = base_request.model_copy(
                update={
                    "source_extraction_id":
                        f"S24-EXT-2-{token}"
                }
            )

            noop = service.ingest(
                second_request,
                commit=False,
            )

            _require(
                failures,
                evidence,
                "same_business_same_payload_is_noop",
                bool(
                    noop.status == "NOOP"
                    and noop.fact_id == created.fact_id
                ),
                "same business/same payload was not NOOP",
            )

            conflicting_request = _invoice_request(
                token=token,
                extraction_id=f"S24-EXT-3-{token}",
                document_id=f"S24-DOC-{token}",
                seller_name=str(seller.name),
                seller_tax=seller_tax,
                buyer_name=str(buyer.name),
                buyer_tax=buyer_tax,
                invoice_no=invoice_no,
                net="200.00",
                vat="26.00",
                gross="226.00",
            )

            conflict = service.ingest(
                conflicting_request,
                commit=False,
            )

            _require(
                failures,
                evidence,
                "same_business_conflicting_payload_rejected",
                bool(
                    conflict.status == "REJECTED"
                    and conflict.error_code
                    == "SOURCE_DATA_CONFLICT"
                    and conflict.fact_id
                    == created.fact_id
                ),
                "same identity/conflicting payload was not rejected",
            )

            fact_count_for_identity = int(
                session.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM facts
                        WHERE business_identity_key=:key
                        """
                    ),
                    {
                        "key":
                            expected_business_identity
                    },
                ).scalar_one()
            )

            supersede_count = int(
                session.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM facts
                        WHERE business_identity_key=:key
                          AND supersedes_fact_id IS NOT NULL
                        """
                    ),
                    {
                        "key":
                            expected_business_identity
                    },
                ).scalar_one()
            )

            evidence[
                "automatic_invoice_supersede_present"
            ] = supersede_count > 0

            if (
                fact_count_for_identity != 1
                or supersede_count != 0
            ):
                failures.append(
                    "Task24 performed automatic Invoice supersession"
                )

            conflict_party_request = _invoice_request(
                token=token,
                extraction_id=f"S24-EXT-CONFLICT-{token}",
                document_id=f"S24-DOC-CONFLICT-{token}",
                seller_name=str(seller.name),
                seller_tax=seller_tax,
                buyer_name=str(buyer.name),
                buyer_tax=buyer_tax,
                invoice_no=f"S24CONFLICT{token}",
                seller_credit=seller_tax,
                seller_tax_field=buyer_tax,
            )

            party_conflict_rejected = False
            try:
                service.ingest(
                    conflict_party_request,
                    commit=False,
                )
            except CanonicalIngestRejected as exc:
                party_conflict_rejected = (
                    exc.code
                    == "PARTY_IDENTIFIER_CONFLICT"
                )

            _require(
                failures,
                evidence,
                "party_identifier_conflict_rejected",
                party_conflict_rejected,
                "Party identifier conflict did not fail closed",
            )

            unapproved = base_request.model_copy(
                update={
                    "source_extraction_id":
                        f"S24-UNAPPROVED-{token}",
                    "review_status": "needs_review",
                }
            )

            unapproved_rejected = False
            try:
                service.ingest(
                    unapproved,
                    commit=False,
                )
            except CanonicalIngestRejected as exc:
                unapproved_rejected = (
                    exc.code == "SOURCE_NOT_APPROVED"
                )

            _require(
                failures,
                evidence,
                "unapproved_source_rejected",
                unapproved_rejected,
                "unapproved IDP source crossed Canonical boundary",
            )

            contract_request = _contract_request(
                token=token,
                party_a_name=str(seller.name),
                party_a_tax=seller_tax,
                party_b_name=str(buyer.name),
                party_b_tax=buyer_tax,
            )

            contract_result = service.ingest(
                contract_request,
                commit=False,
            )
            contract = session.get(
                ContractFact,
                contract_result.fact_id,
            )
            contract_fact = session.get(
                Fact,
                contract_result.fact_id,
            )

            _require(
                failures,
                evidence,
                "approved_contract_ingested",
                contract_result.status == "CREATED",
                "approved Contract was not CREATED",
            )

            _require(
                failures,
                evidence,
                "contract_ab_roles_not_assumed",
                bool(
                    contract
                    and contract.buyer_party_id is None
                    and contract.seller_party_id is None
                    and contract_fact
                    and contract_fact.validation_status
                    == "NEEDS_REVIEW"
                ),
                "party_a/party_b was incorrectly mapped to buyer/seller",
            )

            atomic_request = _invoice_request(
                token=token,
                extraction_id=f"S24-ATOMIC-{token}",
                document_id=f"S24-ATOMIC-DOC-{token}",
                seller_name=str(seller.name),
                seller_tax=seller_tax,
                buyer_name=str(buyer.name),
                buyer_tax=buyer_tax,
                invoice_no=f"S24ATOMIC{token}",
            )

            savepoint = session.begin_nested()
            atomic_result = service.ingest(
                atomic_request,
                commit=False,
            )
            atomic_fact_id = atomic_result.fact_id
            atomic_receipt_id = atomic_result.receipt_id
            savepoint.rollback()
            session.expire_all()

            atomic_fact_count = int(
                session.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM facts
                        WHERE id=:fact_id
                        """
                    ),
                    {"fact_id": atomic_fact_id},
                ).scalar_one()
            )

            atomic_receipt_count = int(
                session.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM canonical_ingest_receipts
                        WHERE id=:receipt_id
                        """
                    ),
                    {"receipt_id": atomic_receipt_id},
                ).scalar_one()
            )

            _require(
                failures,
                evidence,
                "atomic_rollback_verified",
                (
                    atomic_fact_count == 0
                    and atomic_receipt_count == 0
                ),
                "Task24 writes escaped caller transaction rollback",
            )

            seal_after = _seal_snapshot(session)
            cutover_after = _cutover_snapshot(session)
            legacy_after = _legacy_counts(session)

            _require(
                failures,
                evidence,
                "production_seal_unchanged",
                seal_before == seal_after,
                "Production Seal changed during Task24 Gate",
            )

            _require(
                failures,
                evidence,
                "cutover_state_unchanged",
                cutover_before == cutover_after,
                "cutover routing state changed during Task24 Gate",
            )

            _require(
                failures,
                evidence,
                "legacy_write_attempted",
                legacy_before == legacy_after,
                "legacy table row counts changed during Task24 Gate",
            )

        finally:
            if outer.is_active:
                outer.rollback()

    payload = {
        "gate": "S24",
        "status": (
            "PASS"
            if not failures
            else "FAIL"
        ),
        "evidence": evidence,
        "failures": failures,
    }

    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )

    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
