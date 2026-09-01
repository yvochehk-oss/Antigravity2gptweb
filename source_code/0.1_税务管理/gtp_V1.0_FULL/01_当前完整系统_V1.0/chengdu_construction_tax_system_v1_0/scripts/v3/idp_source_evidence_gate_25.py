#!/usr/bin/env python3
"""Task25 Gate S25: IDP Source Evidence Bridge."""
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
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.cutover.finalization import finalize_v3_production_cutover
from app.cutover.writer import get_cutover_state
from app.integration.idp_canonical.evidence_schemas import (
    InvoiceEvidenceCompletionRequest,
    InvoiceLineEvidence,
    ReviewedTaxRateRules,
    SourceDocumentEvidence,
)
from app.integration.idp_canonical.evidence_service import (
    EvidenceCompletionError,
    InvoiceEvidenceCompletionService,
)
from app.integration.idp_canonical.schemas import (
    CanonicalIngestRequest,
)
from app.integration.idp_canonical.service import (
    CanonicalIngestService,
)
from app.v3_evidence_integration_models import (
    IDPSourceDocumentBinding,
)
from app.v3_fact_models import (
    Fact,
    FactProvenance,
    InvoiceLine,
)
from app.v3_party_models import (
    Party,
    PartyIdentifier,
    SourceDocument,
)


EXPECTED_HEAD = "96_v3_idp_source_evidence_bridge"


def _database_url() -> str:
    value = os.getenv(
        "DATABASE_URL",
        "",
    ).strip()

    if not value:
        raise SystemExit(
            "DATABASE_URL is required"
        )

    backend = make_url(value).get_backend_name()
    if backend not in {
        "postgresql",
        "postgres",
    }:
        raise SystemExit(
            "Gate S25 is PostgreSQL-only"
        )

    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value[len("postgresql://"):]
    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value[len("postgres://"):]
    return value


def _disk_heads() -> list[str]:
    cfg = Config(
        str(ROOT / "alembic.ini")
    )
    cfg.set_main_option(
        "script_location",
        str(ROOT / "alembic"),
    )

    return sorted(
        ScriptDirectory.from_config(
            cfg
        ).get_heads()
    )


def _seal_snapshot(
    session: Session,
) -> list[dict]:
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
            "finalized_at": str(
                row["finalized_at"]
            ),
            "state_snapshot": deepcopy(
                row["state_snapshot"]
            ),
            "evidence_snapshot": deepcopy(
                row["evidence_snapshot"]
            ),
        }
        for row in rows
    ]


def _cutover_snapshot(
    session: Session,
) -> list[dict]:
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

    return [
        dict(row)
        for row in rows
    ]


def _table_count(
    session: Session,
    table: str,
) -> int | None:
    allowed = {
        "invoices",
        "invoices_v3",
        "contracts_v3",
    }
    if table not in allowed:
        raise ValueError(table)

    exists = session.execute(
        text(
            "SELECT to_regclass(:name)"
        ),
        {
            "name": f"public.{table}"
        },
    ).scalar_one()

    if exists is None:
        return None

    return int(
        session.execute(
            text(
                f'SELECT count(*) FROM "{table}"'
            )
        ).scalar_one()
    )


def _legacy_counts(
    session: Session,
) -> dict[str, int | None]:
    return {
        table: _table_count(
            session,
            table,
        )
        for table in (
            "invoices",
            "invoices_v3",
            "contracts_v3",
        )
    }


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


def _fixture_party(
    session: Session,
    *,
    code: str,
    name: str,
    tax_id: str,
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
            identifier_type=(
                "TAX_REGISTRATION_ID"
            ),
            identifier_value=tax_id,
            source_system="GATE_S25",
            active=True,
        )
    )
    session.flush()

    return party


def _sha(
    value: str,
) -> str:
    return sha256(
        value.encode("utf-8")
    ).hexdigest()


def _task24_invoice(
    session: Session,
    *,
    token: str,
    suffix: str,
    seller: Party,
    seller_tax: str,
    buyer: Party,
    buyer_tax: str,
) -> CanonicalIngestRequest:
    request = CanonicalIngestRequest(
        source_system="IDP",
        source_document_id=(
            f"S25-DOC-{suffix}-{token}"
        ),
        source_extraction_id=(
            f"S25-EXT-{suffix}-{token}"
        ),
        document_sha256=_sha(
            f"S25:{suffix}:{token}"
        ),
        document_type="invoice",
        review_status="approved",
        approved_by="gate:S25",
        extraction_model="gate-fixture",
        extraction_model_version="1",
        confidence={
            "invoice_no": 0.99,
        },
        data={
            "invoice_type": (
                "增值税电子专用发票"
            ),
            "invoice_code": None,
            "invoice_no": (
                f"S25INV{suffix}{token}"
            ),
            "invoice_date": "2026-08-31",
            "buyer": {
                "name": buyer.name,
                "credit_code": buyer_tax,
                "tax_id": buyer_tax,
                "address": None,
                "legal_representative": None,
            },
            "seller": {
                "name": seller.name,
                "credit_code": seller_tax,
                "tax_id": seller_tax,
                "address": None,
                "legal_representative": None,
            },
            "amount_excluding_tax": "100.00",
            "tax_amount": "13.00",
            "amount_including_tax": "113.00",
            "tax_rate": "0.13",
            "currency": "CNY",
            "check_code": None,
            "confidence": {
                "invoice_no": 0.99,
            },
            "sources": {},
        },
    )

    result = CanonicalIngestService(
        session
    ).ingest(
        request,
        commit=False,
    )

    assert result.status == "CREATED"
    return request


def _evidence_request(
    task24_request: CanonicalIngestRequest,
    *,
    validated: bool,
    lines: list[InvoiceLineEvidence],
    allowed_rates: list[str] | None,
) -> InvoiceEvidenceCompletionRequest:
    return InvoiceEvidenceCompletionRequest(
        source_system="IDP",
        source_document_id=(
            task24_request.source_document_id
        ),
        source_extraction_id=(
            task24_request.source_extraction_id
        ),
        document_sha256=(
            task24_request.document_sha256
        ),
        document=SourceDocumentEvidence(
            filename=(
                f"{task24_request.source_document_id}.pdf"
            ),
            mime_type="application/pdf",
            source_uri=(
                f"idp://{task24_request.source_document_id}"
            ),
            validation_status=(
                "VALIDATED"
                if validated
                else "EXTRACTED"
            ),
            validated_by=(
                "gate:S25"
                if validated
                else None
            ),
            validation_reason=(
                "Gate S25 validated source evidence"
                if validated
                else None
            ),
        ),
        invoice_status="VALID",
        lines=lines,
        tax_rules=(
            ReviewedTaxRateRules(
                rule_version="GATE_S25_RATES_V1",
                reviewed_by="gate:S25",
                allowed_tax_rates=allowed_rates,
            )
            if allowed_rates is not None
            else None
        ),
        extraction_model="gate-fixture",
        extraction_model_version="1",
        confidence=0.99,
        page_start=1,
        page_end=1,
    )


def _valid_line() -> InvoiceLineEvidence:
    return InvoiceLineEvidence(
        line_no=1,
        item_name="施工服务",
        category="SERVICE",
        quantity="1",
        unit_price="100",
        net_amount="100.00",
        vat_amount="13.00",
        tax_rate="0.13",
        tax_classification_code=None,
        confidence=0.99,
    )


def main() -> int:
    failures: list[str] = []
    evidence: dict = {
        "database": None,
        "alembic_db_heads": [],
        "alembic_disk_heads": (
            _disk_heads()
        ),
        "legacy_write_attempted": False,
        "production_seal_write_attempted": False,
    }

    engine = create_engine(
        _database_url(),
        future=True,
        pool_pre_ping=True,
    )

    token = uuid4().hex[:10].upper()

    with Session(engine) as session:
        outer = session.begin()

        try:
            evidence["database"] = (
                session.execute(
                    text(
                        "SELECT current_database()"
                    )
                ).scalar_one()
            )

            db_heads = sorted(
                row[0]
                for row in session.execute(
                    text(
                        """
                        SELECT version_num
                        FROM alembic_version_tax
                        """
                    )
                ).all()
            )
            evidence[
                "alembic_db_heads"
            ] = db_heads

            if db_heads != [EXPECTED_HEAD]:
                failures.append(
                    f"DB heads={db_heads}, "
                    f"expected={[EXPECTED_HEAD]}"
                )

            if evidence[
                "alembic_disk_heads"
            ] != [EXPECTED_HEAD]:
                failures.append(
                    "disk Alembic head is not Task25"
                )

            binding_table = bool(
                session.execute(
                    text(
                        """
                        SELECT
                          to_regclass(
                            'public.idp_source_document_bindings'
                          ) IS NOT NULL
                        """
                    )
                ).scalar_one()
            )

            _require(
                failures,
                evidence,
                "binding_table_present",
                binding_table,
                "idp_source_document_bindings missing",
            )

            state = get_cutover_state(session, for_update=True)
            if state.writer_mode == "SHADOW":
                session.execute(
                    text(
                        "UPDATE writer_cutover_states SET writer_mode='DUAL_WRITE', "
                        "legacy_write_enabled=true, new_fact_write_enabled=true, legacy_frozen=false, "
                        "new_fact_read_mode='SHADOW', rag_source='LEGACY', updated_by='gate:S25' "
                        "WHERE scope='GLOBAL'"
                    )
                )
                session.flush()
            session.execute(
                text(
                    "UPDATE writer_cutover_states SET writer_mode='V3_PRIMARY', "
                    "legacy_write_enabled=false, new_fact_write_enabled=true, legacy_frozen=true, "
                    "updated_by='gate:S25' WHERE scope='GLOBAL' AND writer_mode='DUAL_WRITE'"
                )
            )
            session.execute(
                text(
                    "UPDATE writer_cutover_states SET new_fact_read_mode='PRIMARY', "
                    "rag_source='CANONICAL_FACTS', updated_by='gate:S25' "
                    "WHERE scope='GLOBAL' AND writer_mode='V3_PRIMARY' AND new_fact_read_mode='SHADOW' AND rag_source='LEGACY'"
                )
            )
            session.flush()
            session.expire_all()
            if not session.execute(
                text("SELECT 1 FROM v3_cutover_finalizations WHERE scope='GLOBAL'")
            ).scalar():
                finalize_v3_production_cutover(session, actor="gate:S25", commit=False)
                session.expire_all()

            seal_before = (
                _seal_snapshot(session)
            )
            cutover_before = (
                _cutover_snapshot(session)
            )
            legacy_before = (
                _legacy_counts(session)
            )

            _require(
                failures,
                evidence,
                "production_seal_present",
                any(
                    row["scope"] == "GLOBAL"
                    for row in seal_before
                ),
                "GLOBAL Production Seal missing",
            )

            seller_tax = (
                "91"
                + uuid4().hex[:16].upper()
            )
            buyer_tax = (
                "91"
                + uuid4().hex[:16].upper()
            )

            seller = _fixture_party(
                session,
                code=f"S25SELL{token}",
                name=f"S25 Seller {token}",
                tax_id=seller_tax,
            )
            buyer = _fixture_party(
                session,
                code=f"S25BUY{token}",
                name=f"S25 Buyer {token}",
                tax_id=buyer_tax,
            )

            evidence_service = (
                InvoiceEvidenceCompletionService(
                    session
                )
            )

            # --------------------------------------------------------------
            # Complete evidence -> VALID
            # --------------------------------------------------------------
            valid_task24 = _task24_invoice(
                session,
                token=token,
                suffix="VALID",
                seller=seller,
                seller_tax=seller_tax,
                buyer=buyer,
                buyer_tax=buyer_tax,
            )

            valid_receipt = session.execute(
                select(
                    func.max(
                        text("id")
                    )
                ).select_from(
                    text(
                        "canonical_ingest_receipts"
                    )
                )
            ).scalar_one()

            valid_fact_id = int(
                session.execute(
                    text(
                        """
                        SELECT fact_id
                        FROM canonical_ingest_receipts
                        WHERE source_system='IDP'
                          AND source_extraction_id=:eid
                        """
                    ),
                    {
                        "eid":
                            valid_task24
                            .source_extraction_id
                    },
                ).scalar_one()
            )

            fact_count_before = int(
                session.execute(
                    select(
                        func.count(Fact.id)
                    )
                ).scalar_one()
            )

            valid_request = _evidence_request(
                valid_task24,
                validated=True,
                lines=[_valid_line()],
                allowed_rates=["0.13"],
            )

            valid_result = (
                evidence_service.complete(
                    valid_request,
                    commit=False,
                )
            )

            fact_count_after = int(
                session.execute(
                    select(
                        func.count(Fact.id)
                    )
                ).scalar_one()
            )

            _require(
                failures,
                evidence,
                "complete_valid_evidence_promoted_by_task09",
                (
                    valid_result.validation_status
                    == "VALID"
                    and valid_result.task09_desired_status
                    == "VALID"
                ),
                "complete evidence was not promoted to VALID by Task09",
            )

            _require(
                failures,
                evidence,
                "new_fact_created_during_evidence_completion",
                fact_count_after == fact_count_before,
                "Task25 created a new Fact",
            )
            evidence[
                "new_fact_created_during_evidence_completion"
            ] = (
                fact_count_after
                != fact_count_before
            )

            binding = session.get(
                IDPSourceDocumentBinding,
                valid_result.binding_id,
            )
            source_document = session.get(
                SourceDocument,
                valid_result.source_document_pk,
            )

            _require(
                failures,
                evidence,
                "source_document_registered",
                (
                    binding is not None
                    and source_document is not None
                    and source_document.status
                    == "VALIDATED"
                ),
                "SourceDocument was not registered/validated",
            )

            provenance = session.execute(
                select(FactProvenance)
                .where(
                    FactProvenance.fact_id
                    == valid_fact_id,
                    FactProvenance.document_id
                    == valid_result.source_document_pk,
                )
            ).scalars().first()

            _require(
                failures,
                evidence,
                "fact_provenance_document_fk_valid",
                (
                    provenance is not None
                    and isinstance(
                        provenance.document_id,
                        int,
                    )
                ),
                "FactProvenance does not use real SourceDocument integer FK",
            )

            _require(
                failures,
                evidence,
                "idp_uuid_not_used_as_provenance_fk",
                (
                    provenance is not None
                    and provenance.document_id
                    == source_document.id
                ),
                "IDP external document id leaked into provenance FK",
            )

            line_count = int(
                session.execute(
                    select(
                        func.count(
                            InvoiceLine.id
                        )
                    ).where(
                        InvoiceLine.invoice_fact_id
                        == valid_fact_id
                    )
                ).scalar_one()
            )

            _require(
                failures,
                evidence,
                "invoice_lines_ingested",
                line_count == 1,
                "InvoiceLine evidence was not created",
            )

            # Retry exact evidence.
            retry = evidence_service.complete(
                valid_request,
                commit=False,
            )

            binding_count = int(
                session.execute(
                    select(
                        func.count(
                            IDPSourceDocumentBinding.id
                        )
                    ).where(
                        IDPSourceDocumentBinding.source_system
                        == "IDP",
                        IDPSourceDocumentBinding.source_document_id
                        == valid_task24.source_document_id,
                    )
                ).scalar_one()
            )

            source_document_count = int(
                session.execute(
                    select(
                        func.count(
                            SourceDocument.id
                        )
                    ).where(
                        SourceDocument.source_system
                        == "IDP",
                        SourceDocument.external_document_id
                        == valid_task24.source_document_id,
                    )
                ).scalar_one()
            )

            _require(
                failures,
                evidence,
                "source_document_retry_idempotent",
                (
                    retry.source_document_pk
                    == valid_result.source_document_pk
                    and retry.binding_id
                    == valid_result.binding_id
                    and binding_count == 1
                    and source_document_count == 1
                ),
                "SourceDocument retry was not idempotent",
            )

            _require(
                failures,
                evidence,
                "invoice_lines_retry_idempotent",
                (
                    retry.line_outcome == "NOOP"
                    and int(
                        session.execute(
                            select(
                                func.count(
                                    InvoiceLine.id
                                )
                            ).where(
                                InvoiceLine.invoice_fact_id
                                == valid_fact_id
                            )
                        ).scalar_one()
                    )
                    == 1
                ),
                "InvoiceLine retry was not idempotent",
            )

            # Changed lines must fail closed.
            conflicting = valid_request.model_copy(
                update={
                    "lines": [
                        InvoiceLineEvidence(
                            line_no=1,
                            item_name="施工服务",
                            category="SERVICE",
                            quantity="1",
                            unit_price="99",
                            net_amount="99.00",
                            vat_amount="14.00",
                            tax_rate="0.13",
                        )
                    ]
                }
            )

            line_conflict = False
            try:
                evidence_service.complete(
                    conflicting,
                    commit=False,
                )
            except EvidenceCompletionError as exc:
                line_conflict = (
                    exc.code
                    == "INVOICE_LINE_EVIDENCE_CONFLICT"
                )

            _require(
                failures,
                evidence,
                "invoice_line_conflict_fail_closed",
                line_conflict,
                "changed InvoiceLine evidence did not fail closed",
            )

            receipt_fact_after = int(
                session.execute(
                    text(
                        """
                        SELECT fact_id
                        FROM canonical_ingest_receipts
                        WHERE source_system='IDP'
                          AND source_extraction_id=:eid
                        """
                    ),
                    {
                        "eid":
                            valid_task24
                            .source_extraction_id
                    },
                ).scalar_one()
            )

            _require(
                failures,
                evidence,
                "task24_receipt_fact_unchanged",
                receipt_fact_after
                == valid_fact_id,
                "Task25 changed Task24 receipt Fact identity",
            )

            # --------------------------------------------------------------
            # Missing lines -> NEEDS_REVIEW
            # --------------------------------------------------------------
            missing_lines_task24 = (
                _task24_invoice(
                    session,
                    token=token,
                    suffix="NOLINES",
                    seller=seller,
                    seller_tax=seller_tax,
                    buyer=buyer,
                    buyer_tax=buyer_tax,
                )
            )

            missing_lines_result = (
                evidence_service.complete(
                    _evidence_request(
                        missing_lines_task24,
                        validated=True,
                        lines=[],
                        allowed_rates=["0.13"],
                    ),
                    commit=False,
                )
            )

            _require(
                failures,
                evidence,
                "invoice_missing_lines_not_valid",
                (
                    missing_lines_result
                    .validation_status
                    == "NEEDS_REVIEW"
                    and any(
                        item["code"]
                        == "INVOICE_LINES_MISSING"
                        for item
                        in missing_lines_result.findings
                    )
                ),
                "Invoice without lines became VALID",
            )

            # --------------------------------------------------------------
            # SourceDocument not VALIDATED -> NEEDS_REVIEW
            # --------------------------------------------------------------
            unvalidated_task24 = (
                _task24_invoice(
                    session,
                    token=token,
                    suffix="UNVALIDDOC",
                    seller=seller,
                    seller_tax=seller_tax,
                    buyer=buyer,
                    buyer_tax=buyer_tax,
                )
            )

            unvalidated_result = (
                evidence_service.complete(
                    _evidence_request(
                        unvalidated_task24,
                        validated=False,
                        lines=[_valid_line()],
                        allowed_rates=["0.13"],
                    ),
                    commit=False,
                )
            )

            _require(
                failures,
                evidence,
                "invoice_missing_validated_document_not_valid",
                (
                    unvalidated_result
                    .validation_status
                    == "NEEDS_REVIEW"
                    and any(
                        item["code"]
                        == "SOURCE_DOCUMENT_NOT_VALIDATED"
                        for item
                        in unvalidated_result.findings
                    )
                ),
                "Invoice without validated SourceDocument became VALID",
            )

            # --------------------------------------------------------------
            # Header/line contradiction -> INVALID through Task09
            # --------------------------------------------------------------
            mismatch_task24 = (
                _task24_invoice(
                    session,
                    token=token,
                    suffix="MISMATCH",
                    seller=seller,
                    seller_tax=seller_tax,
                    buyer=buyer,
                    buyer_tax=buyer_tax,
                )
            )

            mismatch_result = (
                evidence_service.complete(
                    _evidence_request(
                        mismatch_task24,
                        validated=True,
                        lines=[
                            InvoiceLineEvidence(
                                line_no=1,
                                item_name="施工服务",
                                net_amount="99.00",
                                vat_amount="14.00",
                                tax_rate="0.13",
                            )
                        ],
                        allowed_rates=["0.13"],
                    ),
                    commit=False,
                )
            )

            _require(
                failures,
                evidence,
                "invalid_header_line_totals_rejected",
                (
                    mismatch_result
                    .validation_status
                    == "INVALID"
                    and any(
                        item["code"]
                        in {
                            "LINE_NET_SUM_MISMATCH",
                            "LINE_VAT_SUM_MISMATCH",
                        }
                        for item
                        in mismatch_result.findings
                    )
                ),
                "header/line contradiction was not INVALID",
            )

            # --------------------------------------------------------------
            # Unsupported reviewed tax rate -> INVALID
            # --------------------------------------------------------------
            rate_task24 = _task24_invoice(
                session,
                token=token,
                suffix="RATE",
                seller=seller,
                seller_tax=seller_tax,
                buyer=buyer,
                buyer_tax=buyer_tax,
            )

            rate_result = (
                evidence_service.complete(
                    _evidence_request(
                        rate_task24,
                        validated=True,
                        lines=[_valid_line()],
                        allowed_rates=["0.09"],
                    ),
                    commit=False,
                )
            )

            _require(
                failures,
                evidence,
                "unsupported_tax_rate_not_promoted_valid",
                (
                    rate_result.validation_status
                    == "INVALID"
                    and any(
                        item["code"]
                        == "LINE_TAX_RATE_NOT_ALLOWED"
                        for item
                        in rate_result.findings
                    )
                ),
                "unsupported line tax rate was promoted VALID",
            )

            # --------------------------------------------------------------
            # Atomic rollback
            # --------------------------------------------------------------
            atomic_task24 = _task24_invoice(
                session,
                token=token,
                suffix="ATOMIC",
                seller=seller,
                seller_tax=seller_tax,
                buyer=buyer,
                buyer_tax=buyer_tax,
            )

            atomic_fact_id = int(
                session.execute(
                    text(
                        """
                        SELECT fact_id
                        FROM canonical_ingest_receipts
                        WHERE source_system='IDP'
                          AND source_extraction_id=:eid
                        """
                    ),
                    {
                        "eid":
                            atomic_task24
                            .source_extraction_id
                    },
                ).scalar_one()
            )

            savepoint = session.begin_nested()

            atomic_result = (
                evidence_service.complete(
                    _evidence_request(
                        atomic_task24,
                        validated=True,
                        lines=[_valid_line()],
                        allowed_rates=["0.13"],
                    ),
                    commit=False,
                )
            )

            atomic_source_pk = (
                atomic_result.source_document_pk
            )
            savepoint.rollback()
            session.expire_all()

            atomic_binding_count = int(
                session.execute(
                    select(
                        func.count(
                            IDPSourceDocumentBinding.id
                        )
                    ).where(
                        IDPSourceDocumentBinding.source_system
                        == "IDP",
                        IDPSourceDocumentBinding.source_document_id
                        == atomic_task24.source_document_id,
                    )
                ).scalar_one()
            )

            atomic_source_count = int(
                session.execute(
                    select(
                        func.count(
                            SourceDocument.id
                        )
                    ).where(
                        SourceDocument.id
                        == atomic_source_pk
                    )
                ).scalar_one()
            )

            atomic_line_count = int(
                session.execute(
                    select(
                        func.count(
                            InvoiceLine.id
                        )
                    ).where(
                        InvoiceLine.invoice_fact_id
                        == atomic_fact_id
                    )
                ).scalar_one()
            )

            atomic_document_provenance = int(
                session.execute(
                    select(
                        func.count(
                            FactProvenance.id
                        )
                    ).where(
                        FactProvenance.fact_id
                        == atomic_fact_id,
                        FactProvenance.document_id
                        .is_not(None),
                    )
                ).scalar_one()
            )

            _require(
                failures,
                evidence,
                "atomic_rollback_verified",
                (
                    atomic_binding_count == 0
                    and atomic_source_count == 0
                    and atomic_line_count == 0
                    and atomic_document_provenance
                    == 0
                ),
                "Task25 writes escaped transaction rollback",
            )

            seal_after = _seal_snapshot(
                session
            )
            cutover_after = (
                _cutover_snapshot(session)
            )
            legacy_after = (
                _legacy_counts(session)
            )

            _require(
                failures,
                evidence,
                "production_seal_unchanged",
                seal_before == seal_after,
                "Production Seal changed during Gate S25",
            )

            _require(
                failures,
                evidence,
                "cutover_state_unchanged",
                cutover_before
                == cutover_after,
                "writer/read/RAG cutover state changed",
            )

            evidence[
                "legacy_write_attempted"
            ] = (
                legacy_before
                != legacy_after
            )

            if evidence[
                "legacy_write_attempted"
            ]:
                failures.append(
                    "legacy business tables changed during Task25"
                )

        finally:
            if outer.is_active:
                outer.rollback()

    payload = {
        "gate": "S25",
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

    return (
        0
        if not failures
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
