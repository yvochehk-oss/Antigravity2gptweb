"""Task24 deterministic IDP -> Canonical intake service.

Important Task24 boundaries:

* only approved/auto-approved IDP extractions cross this boundary;
* IDP approval does not mean Canonical VALID;
* Invoice identity reuses Task07a DIGITAL_V1 / LEGACY_V1 contracts;
* Fact.version_no is independent from invoice_identity_version;
* same source extraction is idempotent;
* same business identity + same payload is NOOP;
* same business identity + different payload is fail-closed REJECTED;
* Task24 never performs automatic Invoice supersession;
* Task24 never writes Legacy tables;
* Task24 never changes Production Seal/cutover state;
* IDP UUIDs are not written into FactProvenance.document_id;
* Contract party_a / party_b are not assumed to mean buyer / seller.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.domain.invoice.identity import (
    InvoiceIdentityError,
    build_invoice_business_identity_key,
    build_invoice_identity_key,
    normalize_identity_component as normalize_invoice_identity_component,
)
from app.v3_contract_models import ContractFact
from app.v3_fact_models import Fact, FactProvenance, InvoiceFact
from app.v3_integration_models import CanonicalIngestReceipt

from .normalizer import (
    CANONICAL_INTAKE_VERSION,
    CONTRACT_IDENTITY_VERSION,
    NormalizationError,
    average_confidence,
    build_contract_business_identity_key,
    canonical_json,
    canonical_payload_fingerprint,
    ensure_contract_amount,
    ensure_invoice_amount_equation,
    money_value,
    normalize_currency,
    normalize_identity_component,
    normalize_invoice_type,
    source_payload_fingerprint,
)
from .party_resolver import (
    PartyResolution,
    PartyResolutionError,
    PartyResolver,
)
from .schemas import (
    CanonicalIngestRequest,
    CanonicalIngestResult,
    IDPContractData,
    IDPInvoiceData,
)


APPROVED_REVIEW_STATES = frozenset(
    {
        "approved",
        "auto_approved",
    }
)

INITIAL_VALIDATION_STATUS = "NEEDS_REVIEW"


class CanonicalIngestRejected(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class PreparedIngest:
    document_type: str
    fact_type: str

    business_identity_key: str
    identity_version: str

    canonical_payload: dict[str, Any]
    payload_fingerprint: str

    invoice_values: dict[str, Any] | None = None
    contract_values: dict[str, Any] | None = None

    provenance_confidence: Decimal | None = None


def _decide_existing(
    existing_payload_fingerprint: str | None,
    incoming_payload_fingerprint: str,
) -> tuple[str, str | None]:
    if (
        existing_payload_fingerprint is not None
        and existing_payload_fingerprint
        == incoming_payload_fingerprint
    ):
        return "NOOP", None

    if existing_payload_fingerprint is None:
        return "REJECTED", "EXISTING_FACT_UNMANAGED"

    return "REJECTED", "SOURCE_DATA_CONFLICT"


class CanonicalIngestService:
    def __init__(
        self,
        db: Session,
        *,
        party_resolver: PartyResolver | None = None,
    ) -> None:
        self.db = db
        self.party_resolver = party_resolver or PartyResolver(db)

    @staticmethod
    def _raise_normalization(exc: NormalizationError) -> None:
        raise CanonicalIngestRejected(
            exc.code,
            exc.detail,
        ) from exc

    @staticmethod
    def _raise_party(exc: PartyResolutionError) -> None:
        raise CanonicalIngestRejected(
            exc.code,
            exc.detail,
        ) from exc

    def _resolve_party(self, value: Any) -> PartyResolution:
        try:
            return self.party_resolver.resolve(value)
        except PartyResolutionError as exc:
            self._raise_party(exc)
        raise AssertionError("unreachable")

    def prepare(self, request: CanonicalIngestRequest) -> PreparedIngest:
        if request.document_type == "invoice":
            return self._prepare_invoice(request)

        if request.document_type == "contract":
            return self._prepare_contract(request)

        raise CanonicalIngestRejected(
            "UNSUPPORTED_DOCUMENT_TYPE",
            f"Task24 does not support {request.document_type!r}",
        )

    def _prepare_invoice(
        self,
        request: CanonicalIngestRequest,
    ) -> PreparedIngest:
        try:
            source = IDPInvoiceData.model_validate(request.data)
        except Exception as exc:
            raise CanonicalIngestRejected(
                "SCHEMA_INVALID",
                f"invoice payload does not match Task24 schema: {exc}",
            ) from exc

        seller = self._resolve_party(source.seller)
        buyer = self._resolve_party(source.buyer)

        if seller.party_id == buyer.party_id:
            raise CanonicalIngestRejected(
                "PARTY_IDENTIFIER_CONFLICT",
                "invoice seller and buyer resolve to the same Party",
            )

        invoice_number = normalize_invoice_identity_component(
            source.invoice_no
        )
        invoice_code = normalize_invoice_identity_component(
            source.invoice_code
        )

        if not invoice_number:
            raise CanonicalIngestRejected(
                "BUSINESS_IDENTITY_INCOMPLETE",
                "invoice number is required",
            )

        type_mapping = normalize_invoice_type(source.invoice_type)

        identity_version: str

        if type_mapping.medium == "DIGITAL" and type_mapping.recognized:
            identity_version = "DIGITAL_V1"
        elif (
            invoice_code
            and seller.canonical_tax_identity
        ):
            identity_version = "LEGACY_V1"
        else:
            raise CanonicalIngestRejected(
                "BUSINESS_IDENTITY_INCOMPLETE",
                "invoice cannot receive DIGITAL_V1 or LEGACY_V1 "
                "identity from available evidence",
            )

        try:
            invoice_identity_key = build_invoice_identity_key(
                identity_version,
                invoice_number=invoice_number,
                invoice_code=invoice_code or None,
                seller_tax_identity=seller.canonical_tax_identity,
            )
            business_identity_key = (
                build_invoice_business_identity_key(
                    invoice_identity_key
                )
            )
        except InvoiceIdentityError as exc:
            raise CanonicalIngestRejected(
                "BUSINESS_IDENTITY_INCOMPLETE",
                str(exc),
            ) from exc

        gross = money_value(source.amount_including_tax)
        net = money_value(source.amount_excluding_tax)
        vat = money_value(source.tax_amount)

        try:
            ensure_invoice_amount_equation(
                gross=gross,
                net=net,
                vat=vat,
            )
            currency = normalize_currency(source.currency)
        except NormalizationError as exc:
            self._raise_normalization(exc)
            raise AssertionError("unreachable")

        canonical_payload: dict[str, Any] = {
            "fact_type": "INVOICE",
            "identity_version": identity_version,
            "invoice_identity_key": invoice_identity_key,
            "business_identity_key": business_identity_key,
            "invoice_number": invoice_number,
            "invoice_code": invoice_code or None,
            "invoice_date": (
                source.invoice_date.isoformat()
                if source.invoice_date
                else None
            ),
            "seller_party_id": seller.party_id,
            "buyer_party_id": buyer.party_id,
            "seller_party_code": seller.party_code,
            "buyer_party_code": buyer.party_code,
            "gross_amount": (
                format(gross, ".2f")
                if gross is not None
                else None
            ),
            "net_amount": (
                format(net, ".2f")
                if net is not None
                else None
            ),
            "vat_amount": (
                format(vat, ".2f")
                if vat is not None
                else None
            ),
            "currency": currency,
            "invoice_medium": type_mapping.medium,
            "invoice_category": type_mapping.category,
            "invoice_type_mapping_version": (
                type_mapping.mapping_version
            ),
            "invoice_type_recognized": type_mapping.recognized,
            "invoice_lines_present": False,
            "source_document_registered": False,
        }

        confidence = average_confidence(
            request.confidence,
            source.confidence,
        )

        return PreparedIngest(
            document_type="invoice",
            fact_type="INVOICE",
            business_identity_key=business_identity_key,
            identity_version=identity_version,
            canonical_payload=canonical_payload,
            payload_fingerprint=canonical_payload_fingerprint(
                canonical_payload
            ),
            invoice_values={
                "seller_party_id": seller.party_id,
                "buyer_party_id": buyer.party_id,
                "invoice_identity_key": invoice_identity_key,
                "invoice_identity_version": identity_version,
                "invoice_number": invoice_number,
                "invoice_code": invoice_code or None,
                # Task07a compatibility field is deliberately unused.
                "invoice_type": None,
                "invoice_medium": type_mapping.medium,
                "invoice_category": type_mapping.category,
                "invoice_date": source.invoice_date,
                # IDP v3 schema does not provide legal document status.
                "invoice_status": None,
                "document_type": "IDP_INVOICE",
                "gross_amount": gross,
                "net_amount": net,
                "vat_amount": vat,
                "currency": currency,
            },
            contract_values=None,
            provenance_confidence=confidence,
        )

    def _prepare_contract(
        self,
        request: CanonicalIngestRequest,
    ) -> PreparedIngest:
        try:
            source = IDPContractData.model_validate(request.data)
        except Exception as exc:
            raise CanonicalIngestRejected(
                "SCHEMA_INVALID",
                f"contract payload does not match Task24 schema: {exc}",
            ) from exc

        party_a = self._resolve_party(source.party_a)
        party_b = self._resolve_party(source.party_b)

        if party_a.party_id == party_b.party_id:
            raise CanonicalIngestRejected(
                "PARTY_IDENTIFIER_CONFLICT",
                "contract party_a and party_b resolve to the same Party",
            )

        contract_number = normalize_identity_component(
            source.contract_no
        )

        try:
            business_identity_key = (
                build_contract_business_identity_key(
                    contract_number,
                    [
                        party_a.party_code,
                        party_b.party_code,
                    ],
                )
            )
            currency = normalize_currency(source.currency)
            contract_amount = money_value(
                source.amount_tax_included
            )
            ensure_contract_amount(contract_amount)
        except NormalizationError as exc:
            self._raise_normalization(exc)
            raise AssertionError("unreachable")

        participants = sorted(
            [
                {
                    "party_id": party_a.party_id,
                    "party_code": party_a.party_code,
                    "source_role": "party_a",
                },
                {
                    "party_id": party_b.party_id,
                    "party_code": party_b.party_code,
                    "source_role": "party_b",
                },
            ],
            key=lambda item: (
                str(item["party_code"]),
                int(item["party_id"]),
            ),
        )

        canonical_payload: dict[str, Any] = {
            "fact_type": "CONTRACT",
            "identity_version": CONTRACT_IDENTITY_VERSION,
            "business_identity_key": business_identity_key,
            "contract_number": contract_number,
            "contract_date": (
                source.sign_date.isoformat()
                if source.sign_date
                else None
            ),
            # Task24 deliberately does not infer category.
            "contract_category": None,
            "contract_amount": (
                format(contract_amount, ".2f")
                if contract_amount is not None
                else None
            ),
            "currency": currency,
            # Role-neutral participants are retained in the receipt.
            "participants": participants,
            "buyer_party_id": None,
            "seller_party_id": None,
            "source_document_registered": False,
        }

        confidence = average_confidence(
            request.confidence,
            source.confidence,
        )

        return PreparedIngest(
            document_type="contract",
            fact_type="CONTRACT",
            business_identity_key=business_identity_key,
            identity_version=CONTRACT_IDENTITY_VERSION,
            canonical_payload=canonical_payload,
            payload_fingerprint=canonical_payload_fingerprint(
                canonical_payload
            ),
            invoice_values=None,
            contract_values={
                # party_a / party_b are NOT assumed buyer / seller.
                "buyer_party_id": None,
                "seller_party_id": None,
                "contract_number": contract_number,
                "contract_date": source.sign_date,
                "contract_category": None,
                "contract_amount": contract_amount,
                "currency": currency,
            },
            provenance_confidence=confidence,
        )

    def _lock(self, namespace: str, value: str) -> None:
        lock_key = f"{namespace}|{value}"
        self.db.execute(
            text(
                """
                SELECT pg_advisory_xact_lock(
                    hashtextextended(:lock_key, 0)
                )
                """
            ),
            {"lock_key": lock_key},
        )

    def _source_receipt(
        self,
        request: CanonicalIngestRequest,
    ) -> CanonicalIngestReceipt | None:
        return self.db.execute(
            select(CanonicalIngestReceipt).where(
                CanonicalIngestReceipt.source_system
                == request.source_system,
                CanonicalIngestReceipt.source_extraction_id
                == request.source_extraction_id,
            )
        ).scalar_one_or_none()

    def _current_fact(
        self,
        business_identity_key: str,
    ) -> Fact | None:
        return self.db.execute(
            select(Fact)
            .where(
                Fact.business_identity_key
                == business_identity_key,
                Fact.is_current.is_(True),
            )
            .with_for_update()
        ).scalar_one_or_none()

    def _managed_payload_fingerprint(
        self,
        *,
        fact_id: int,
        business_identity_key: str,
    ) -> str | None:
        receipt = self.db.execute(
            select(CanonicalIngestReceipt)
            .where(
                CanonicalIngestReceipt.fact_id == int(fact_id),
                CanonicalIngestReceipt.business_identity_key
                == business_identity_key,
                CanonicalIngestReceipt.outcome.in_(
                    ("CREATED", "NOOP")
                ),
            )
            .order_by(CanonicalIngestReceipt.id.asc())
        ).scalars().first()

        return (
            str(receipt.payload_fingerprint)
            if receipt is not None
            else None
        )

    def _build_new_fact(
        self,
        request: CanonicalIngestRequest,
        prepared: PreparedIngest,
    ) -> tuple[Fact, InvoiceFact | ContractFact, FactProvenance]:
        fact = Fact(
            fact_type=prepared.fact_type,
            business_identity_key=prepared.business_identity_key,
            version_no=1,
            is_current=True,
            supersedes_fact_id=None,
            validation_status=INITIAL_VALIDATION_STATUS,
        )

        if prepared.invoice_values is not None:
            domain: InvoiceFact | ContractFact = InvoiceFact(
                **prepared.invoice_values
            )
        elif prepared.contract_values is not None:
            domain = ContractFact(
                **prepared.contract_values
            )
        else:
            raise AssertionError(
                "prepared ingest contains no domain projection"
            )

        provenance = FactProvenance(
            document_id=None,
            chunk_id=(
                f"IDP:{request.source_extraction_id}"[:120]
            ),
            confidence=prepared.provenance_confidence,
            extraction_model=request.extraction_model,
            extraction_model_version=(
                request.extraction_model_version
            ),
            original_extracted_value=canonical_json(request.data),
            # Canonical VALID verification is deliberately not claimed.
            verified_value=None,
            verifier_user_id=request.approved_by,
            verification_reason=(
                "TASK24_IDP_APPROVED_INTAKE;"
                "CANONICAL_VALIDATION_PENDING"
            ),
        )

        return fact, domain, provenance

    def _create_receipt(
        self,
        *,
        request: CanonicalIngestRequest,
        prepared: PreparedIngest,
        source_fingerprint: str,
        fact_id: int,
        outcome: str,
        error_code: str | None = None,
        error_detail: str | None = None,
    ) -> CanonicalIngestReceipt:
        receipt = CanonicalIngestReceipt(
            source_system=request.source_system,
            source_document_id=request.source_document_id,
            source_extraction_id=request.source_extraction_id,
            document_sha256=request.document_sha256.lower(),
            document_type=request.document_type,
            business_identity_key=prepared.business_identity_key,
            identity_version=prepared.identity_version,
            normalization_version=CANONICAL_INTAKE_VERSION,
            fact_id=int(fact_id),
            outcome=outcome,
            error_code=error_code,
            error_detail=error_detail,
            source_payload_fingerprint=source_fingerprint,
            payload_fingerprint=prepared.payload_fingerprint,
            canonical_payload=prepared.canonical_payload,
        )
        self.db.add(receipt)
        self.db.flush()
        return receipt

    def _result_from_receipt(
        self,
        receipt: CanonicalIngestReceipt,
    ) -> CanonicalIngestResult:
        fact = self.db.get(Fact, int(receipt.fact_id))
        if fact is None:
            raise RuntimeError(
                f"receipt {receipt.id} references missing Fact "
                f"{receipt.fact_id}"
            )

        return CanonicalIngestResult(
            status=receipt.outcome,
            receipt_id=int(receipt.id),
            fact_id=int(fact.id),
            fact_type=str(fact.fact_type),
            business_identity_key=str(
                fact.business_identity_key
            ),
            identity_version=str(receipt.identity_version),
            version_no=int(fact.version_no),
            validation_status=str(fact.validation_status),
            error_code=receipt.error_code,
            error_detail=receipt.error_detail,
        )

    def _ingest(
        self,
        request: CanonicalIngestRequest,
    ) -> CanonicalIngestResult:
        source_fingerprint = source_payload_fingerprint(request)

        self._lock(
            "TASK24_SOURCE",
            f"{request.source_system}|"
            f"{request.source_extraction_id}",
        )

        existing_source_receipt = self._source_receipt(request)

        if existing_source_receipt is not None:
            if (
                existing_source_receipt.source_payload_fingerprint
                != source_fingerprint
            ):
                raise CanonicalIngestRejected(
                    "SOURCE_EVENT_MUTATED",
                    "same source_system/source_extraction_id was "
                    "reused with a different payload",
                )

            return self._result_from_receipt(
                existing_source_receipt
            )

        prepared = self.prepare(request)

        self._lock(
            "TASK24_BUSINESS",
            prepared.business_identity_key,
        )

        existing_fact = self._current_fact(
            prepared.business_identity_key
        )

        if existing_fact is not None:
            managed_fingerprint = (
                self._managed_payload_fingerprint(
                    fact_id=int(existing_fact.id),
                    business_identity_key=(
                        prepared.business_identity_key
                    ),
                )
            )

            outcome, error_code = _decide_existing(
                managed_fingerprint,
                prepared.payload_fingerprint,
            )

            if outcome == "NOOP":
                receipt = self._create_receipt(
                    request=request,
                    prepared=prepared,
                    source_fingerprint=source_fingerprint,
                    fact_id=int(existing_fact.id),
                    outcome="NOOP",
                )
                return self._result_from_receipt(receipt)

            detail = (
                "existing current Fact has no Task24-managed "
                "canonical payload"
                if error_code == "EXISTING_FACT_UNMANAGED"
                else (
                    "same physical business identity was submitted "
                    "with a different canonical payload"
                )
            )

            receipt = self._create_receipt(
                request=request,
                prepared=prepared,
                source_fingerprint=source_fingerprint,
                fact_id=int(existing_fact.id),
                outcome="REJECTED",
                error_code=error_code,
                error_detail=detail,
            )
            return self._result_from_receipt(receipt)

        fact, domain, provenance = self._build_new_fact(
            request,
            prepared,
        )

        self.db.add(fact)
        self.db.flush()

        domain.fact_id = int(fact.id)
        provenance.fact_id = int(fact.id)

        self.db.add(domain)
        self.db.add(provenance)
        self.db.flush()

        receipt = self._create_receipt(
            request=request,
            prepared=prepared,
            source_fingerprint=source_fingerprint,
            fact_id=int(fact.id),
            outcome="CREATED",
        )

        return self._result_from_receipt(receipt)

    def ingest(
        self,
        request: CanonicalIngestRequest,
        *,
        commit: bool = True,
    ) -> CanonicalIngestResult:
        if request.review_status not in APPROVED_REVIEW_STATES:
            raise CanonicalIngestRejected(
                "SOURCE_NOT_APPROVED",
                "only approved or auto_approved IDP extractions may "
                "cross the Canonical intake boundary",
            )

        try:
            with self.db.begin_nested():
                result = self._ingest(request)

            if commit:
                self.db.commit()

            return result

        except (
            CanonicalIngestRejected,
            PartyResolutionError,
            NormalizationError,
        ):
            if commit:
                self.db.rollback()
            raise

        except Exception:
            if commit:
                self.db.rollback()
            raise
