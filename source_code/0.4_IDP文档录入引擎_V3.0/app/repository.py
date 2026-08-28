from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from pydantic import ValidationError

from .schemas import ContractData, InvoiceData
from .validators import validate_contract, validate_invoice


class DuplicateBusinessRecordError(RuntimeError):
    pass


class ReviewDataValidationError(ValueError):
    """Raised when human-approved data is not a supported valid data model."""


def validate_review_data(document_type: Optional[str], raw_data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and normalize data immediately before a business-table write."""
    models = {"contract": ContractData, "invoice": InvoiceData}
    model = models.get(document_type)
    if model is None:
        raise ReviewDataValidationError("unsupported_document_type")

    candidate = dict(raw_data)
    # Extraction metadata is internal transport state, not a business field.
    candidate.pop("_meta", None)
    try:
        normalized = model.model_validate(candidate).model_dump(mode="json")
    except ValidationError as exc:
        raise ReviewDataValidationError(
            "review_data_schema_invalid: " + "; ".join(
                error.get("loc", ()) and ".".join(map(str, error["loc"])) or "data"
                for error in exc.errors()
            )
        ) from exc

    confidence = normalized.get("confidence") or {}
    for field, value in confidence.items():
        try:
            confidence_value = float(value)
        except (TypeError, ValueError):
            raise ReviewDataValidationError(f"confidence_invalid: {field}") from None
        if not 0 <= confidence_value <= 1:
            raise ReviewDataValidationError(f"confidence_out_of_range: {field}")

    validation = validate_contract(normalized) if document_type == "contract" else validate_invoice(normalized)
    if document_type == "contract":
        for index, term in enumerate(normalized.get("payment_terms") or []):
            amount = term.get("amount")
            if amount is None:
                continue
            try:
                if float(amount) < 0:
                    raise ReviewDataValidationError(f"payment_terms[{index}].amount_negative")
            except (TypeError, ValueError):
                raise ReviewDataValidationError(f"payment_terms[{index}].amount_invalid_number") from None
    if validation.get("errors"):
        raise ReviewDataValidationError(
            "review_data_business_invalid: " + ",".join(validation["errors"])
        )
    return normalized


class IDPRepository:
    """PostgreSQL persistence for V3 extraction, review and confirmed data.

    Persistence is optional in development: when DATABASE_URL is empty, the
    extraction API remains usable and simply returns persistence.enabled=false.
    """

    def __init__(self, database_url: Optional[str] = None) -> None:
        self.database_url = (database_url or os.getenv("DATABASE_URL", "")).strip()

    @property
    def enabled(self) -> bool:
        return bool(self.database_url)

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        if not self.enabled:
            raise RuntimeError("DATABASE_URL is not configured")
        with psycopg.connect(self.database_url, row_factory=dict_row) as conn:
            yield conn

    @contextmanager
    def processing_lock(self, sha256: str) -> Iterator[None]:
        """Serialize processing for one SHA across workers and processes."""
        if not self.enabled:
            yield
            return
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (sha256,))
            yield

    def health(self) -> bool:
        if not self.enabled:
            return False
        try:
            with self.connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT 1")
                return cur.fetchone() is not None
        except Exception:
            return False

    def get_document_by_sha(self, sha256: str) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, sha256, filename, document_type, status, created_at, updated_at
                FROM idp_documents WHERE sha256 = %s
                """,
                (sha256,),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def persist_result(
        self,
        *,
        filename: str,
        file_type: str,
        file_path: str,
        raw_text: str,
        result: Dict[str, Any],
        model_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "stored": False, "duplicate": False}

        data = dict(result.get("data") or {})
        validation = dict(result.get("validation") or {})
        audit = dict(result.get("audit") or {})
        initial_status = str(result.get("status") or "needs_review")
        confidence = data.get("confidence") or {}

        with self.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM idp_documents WHERE sha256 = %s", (result["sha256"],))
            existing = cur.fetchone()
            duplicate_file = existing is not None

            cur.execute(
                """
                INSERT INTO idp_documents (
                    sha256, filename, file_type, document_type, file_path,
                    page_count, parser, raw_text, ocr_confidence, status
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (sha256) DO UPDATE SET
                    filename = EXCLUDED.filename,
                    file_type = EXCLUDED.file_type,
                    document_type = EXCLUDED.document_type,
                    file_path = EXCLUDED.file_path,
                    page_count = EXCLUDED.page_count,
                    parser = EXCLUDED.parser,
                    raw_text = EXCLUDED.raw_text,
                    ocr_confidence = EXCLUDED.ocr_confidence,
                    status = EXCLUDED.status,
                    updated_at = NOW()
                RETURNING id
                """,
                (
                    result["sha256"], filename, file_type, result.get("document_type"), file_path,
                    result.get("page_count") or 0, result.get("parser"), raw_text,
                    result.get("ocr_confidence"), initial_status,
                ),
            )
            document_id = cur.fetchone()["id"]

            effective_status = initial_status
            duplicate_business = self._find_business_duplicate(cur, result.get("document_type"), data, document_id)
            if duplicate_business:
                effective_status = "needs_review"

            cur.execute(
                """
                INSERT INTO document_extractions (
                    document_id, extractor_version, model_name, schema_version,
                    extracted_data, confidence, validation_result, status
                ) VALUES (%s,'v3.0',%s,'3.0',%s,%s,%s,%s)
                RETURNING id
                """,
                (
                    document_id,
                    model_name,
                    Jsonb(data),
                    Jsonb(confidence),
                    Jsonb({"validation": validation, "audit": audit}),
                    effective_status,
                ),
            )
            extraction_id = cur.fetchone()["id"]

            review_id = None
            committed = False
            if effective_status == "approved":
                self._commit_business_entity(cur, result.get("document_type"), data, document_id, extraction_id)
                cur.execute(
                    "UPDATE idp_documents SET status='committed', updated_at=NOW() WHERE id=%s",
                    (document_id,),
                )
                cur.execute(
                    "UPDATE document_extractions SET status='committed' WHERE id=%s",
                    (extraction_id,),
                )
                effective_status = "committed"
                committed = True
            else:
                reasons = self._review_reasons(data, validation, audit, duplicate_business)
                cur.execute(
                    """
                    INSERT INTO document_reviews (
                        document_id, extraction_id, review_data, reason, status
                    ) VALUES (%s,%s,%s,%s,'pending')
                    RETURNING id
                    """,
                    (document_id, extraction_id, Jsonb(data), Jsonb(reasons)),
                )
                review_id = cur.fetchone()["id"]
                cur.execute(
                    "UPDATE idp_documents SET status=%s, updated_at=NOW() WHERE id=%s",
                    (effective_status, document_id),
                )

            conn.commit()
            return {
                "enabled": True,
                "stored": True,
                "duplicate": duplicate_file,
                "duplicate_business": duplicate_business,
                "document_id": str(document_id),
                "extraction_id": str(extraction_id),
                "review_id": str(review_id) if review_id else None,
                "status": effective_status,
                "committed": committed,
            }

    def list_reviews(self, status: str = "pending", limit: int = 100) -> list[Dict[str, Any]]:
        if not self.enabled:
            return []
        limit = max(1, min(int(limit), 500))
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.id, r.document_id, r.extraction_id, r.review_data, r.reason,
                       r.status, r.reviewer, r.reviewed_at, r.created_at,
                       d.filename, d.document_type, d.parser, d.ocr_confidence
                FROM document_reviews r
                JOIN idp_documents d ON d.id = r.document_id
                WHERE r.status = %s
                ORDER BY r.created_at ASC
                LIMIT %s
                """,
                (status, limit),
            )
            return [self._jsonable_row(row) for row in cur.fetchall()]

    def get_review(self, review_id: str | UUID) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.*, d.filename, d.document_type, d.raw_text, d.parser,
                       e.validation_result, e.extracted_data
                FROM document_reviews r
                JOIN idp_documents d ON d.id = r.document_id
                JOIN document_extractions e ON e.id = r.extraction_id
                WHERE r.id = %s
                """,
                (review_id,),
            )
            row = cur.fetchone()
            return self._jsonable_row(row) if row else None

    def complete_review(
        self,
        review_id: str | UUID,
        *,
        action: str,
        reviewer: str,
        review_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("DATABASE_URL is not configured")
        if action not in {"approve", "reject"}:
            raise ValueError("action must be approve or reject")

        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.*, d.document_type, e.extracted_data
                FROM document_reviews r
                JOIN idp_documents d ON d.id = r.document_id
                JOIN document_extractions e ON e.id = r.extraction_id
                WHERE r.id = %s FOR UPDATE
                """,
                (review_id,),
            )
            review = cur.fetchone()
            if not review:
                raise KeyError("review_not_found")
            if review["status"] != "pending":
                raise ValueError("review_already_completed")

            raw_final_data = review_data if review_data is not None else dict(review["extracted_data"] or {})
            if action == "approve" or review_data is not None:
                final_data = validate_review_data(review["document_type"], raw_final_data)
            else:
                final_data = raw_final_data
            if action == "approve":
                duplicate_business = self._find_business_duplicate(
                    cur, review["document_type"], final_data, review["document_id"]
                )
                if duplicate_business:
                    raise DuplicateBusinessRecordError(duplicate_business)
                self._commit_business_entity(
                    cur, review["document_type"], final_data,
                    review["document_id"], review["extraction_id"],
                )
                review_status = "approved"
                document_status = "committed"
                extraction_status = "committed"
            else:
                review_status = "rejected"
                document_status = "correction"
                extraction_status = "needs_review"

            cur.execute(
                """
                UPDATE document_reviews
                SET review_data=%s, status=%s, reviewer=%s, reviewed_at=NOW()
                WHERE id=%s
                """,
                (Jsonb(final_data), review_status, reviewer, review_id),
            )
            cur.execute(
                "UPDATE document_extractions SET extracted_data=%s, status=%s WHERE id=%s",
                (Jsonb(final_data), extraction_status, review["extraction_id"]),
            )
            cur.execute(
                "UPDATE idp_documents SET status=%s, updated_at=NOW() WHERE id=%s",
                (document_status, review["document_id"]),
            )
            conn.commit()
            return {
                "review_id": str(review_id),
                "action": action,
                "status": review_status,
                "document_status": document_status,
            }

    @staticmethod
    def _review_reasons(
        data: Dict[str, Any],
        validation: Dict[str, Any],
        audit: Dict[str, Any],
        duplicate_business: Optional[str],
    ) -> list[Dict[str, Any]]:
        reasons: list[Dict[str, Any]] = []
        for code in validation.get("errors") or []:
            reasons.append({"source": "validation", "level": "error", "code": code})
        for code in validation.get("warnings") or []:
            reasons.append({"source": "validation", "level": "warning", "code": code})
        if duplicate_business:
            reasons.append({"source": "dedupe", "level": "error", "code": duplicate_business})
        meta = data.get("_meta") if isinstance(data, dict) else None
        if isinstance(meta, dict) and meta.get("semantic_error"):
            reasons.append({"source": "ling", "level": "warning", "code": "semantic_model_failed"})
        for risk in audit.get("risks") or []:
            reasons.append({"source": "granite", "level": risk.get("level", "warning"), "risk": risk})
        if not reasons:
            reasons.append({"source": "confidence", "level": "warning", "code": "manual_review_required"})
        return reasons

    @staticmethod
    def _find_business_duplicate(cur: Any, document_type: Optional[str], data: Dict[str, Any], document_id: Any) -> Optional[str]:
        if document_type != "invoice":
            return None
        invoice_no = data.get("invoice_no")
        seller = data.get("seller") or {}
        seller_tax_id = seller.get("tax_id") or seller.get("credit_code")
        if not invoice_no or not seller_tax_id:
            return None
        cur.execute(
            """
            SELECT document_id FROM invoices_v3
            WHERE invoice_no=%s AND seller_tax_id=%s AND document_id<>%s
            LIMIT 1
            """,
            (invoice_no, seller_tax_id, document_id),
        )
        return "duplicate_invoice" if cur.fetchone() else None

    def _commit_business_entity(
        self,
        cur: Any,
        document_type: Optional[str],
        data: Dict[str, Any],
        document_id: Any,
        extraction_id: Any,
    ) -> None:
        if document_type == "contract":
            self._upsert_contract(cur, data, document_id, extraction_id)
        elif document_type == "invoice":
            self._upsert_invoice(cur, data, document_id, extraction_id)

    @staticmethod
    def _upsert_contract(cur: Any, data: Dict[str, Any], document_id: Any, extraction_id: Any) -> None:
        party_a = data.get("party_a") or {}
        party_b = data.get("party_b") or {}
        cur.execute("SELECT id FROM contracts_v3 WHERE document_id=%s ORDER BY created_at DESC LIMIT 1", (document_id,))
        row = cur.fetchone()
        values = (
            extraction_id, data.get("contract_no"), data.get("contract_name"),
            party_a.get("name"), party_a.get("credit_code") or party_a.get("tax_id"),
            party_b.get("name"), party_b.get("credit_code") or party_b.get("tax_id"),
            data.get("project_name"), data.get("sign_date"), data.get("currency") or "CNY",
            data.get("amount_tax_included"), data.get("amount_tax_excluded"), data.get("tax_amount"), data.get("tax_rate"),
            Jsonb(data.get("payment_terms") or []), data.get("contract_start_date"), data.get("contract_end_date"),
            data.get("warranty_period"), data.get("bank"), data.get("bank_account"),
        )
        if row:
            cur.execute(
                """
                UPDATE contracts_v3 SET extraction_id=%s, contract_no=%s, contract_name=%s,
                    party_a_name=%s, party_a_credit_code=%s, party_b_name=%s, party_b_credit_code=%s,
                    project_name=%s, sign_date=%s, currency=%s, amount_tax_included=%s,
                    amount_tax_excluded=%s, tax_amount=%s, tax_rate=%s, payment_terms=%s,
                    contract_start_date=%s, contract_end_date=%s, warranty_period=%s,
                    bank=%s, bank_account=%s, status='confirmed', updated_at=NOW()
                WHERE id=%s
                """,
                values + (row["id"],),
            )
        else:
            cur.execute(
                """
                INSERT INTO contracts_v3 (
                    document_id, extraction_id, contract_no, contract_name,
                    party_a_name, party_a_credit_code, party_b_name, party_b_credit_code,
                    project_name, sign_date, currency, amount_tax_included, amount_tax_excluded,
                    tax_amount, tax_rate, payment_terms, contract_start_date, contract_end_date,
                    warranty_period, bank, bank_account, status
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'confirmed')
                """,
                (document_id,) + values,
            )

    @staticmethod
    def _upsert_invoice(cur: Any, data: Dict[str, Any], document_id: Any, extraction_id: Any) -> None:
        buyer = data.get("buyer") or {}
        seller = data.get("seller") or {}
        buyer_tax_id = buyer.get("tax_id") or buyer.get("credit_code")
        seller_tax_id = seller.get("tax_id") or seller.get("credit_code")
        cur.execute("SELECT id FROM invoices_v3 WHERE document_id=%s ORDER BY created_at DESC LIMIT 1", (document_id,))
        row = cur.fetchone()
        values = (
            extraction_id, data.get("invoice_type"), data.get("invoice_code"), data.get("invoice_no"), data.get("invoice_date"),
            buyer.get("name"), buyer_tax_id, seller.get("name"), seller_tax_id,
            data.get("amount_excluding_tax"), data.get("tax_amount"), data.get("amount_including_tax"),
            data.get("tax_rate"), data.get("currency") or "CNY", data.get("check_code"),
        )
        if row:
            cur.execute(
                """
                UPDATE invoices_v3 SET extraction_id=%s, invoice_type=%s, invoice_code=%s,
                    invoice_no=%s, invoice_date=%s, buyer_name=%s, buyer_tax_id=%s,
                    seller_name=%s, seller_tax_id=%s, amount_excluding_tax=%s,
                    tax_amount=%s, amount_including_tax=%s, tax_rate=%s, currency=%s,
                    check_code=%s, status='confirmed', updated_at=NOW()
                WHERE id=%s
                """,
                values + (row["id"],),
            )
        else:
            cur.execute(
                """
                INSERT INTO invoices_v3 (
                    document_id, extraction_id, invoice_type, invoice_code, invoice_no,
                    invoice_date, buyer_name, buyer_tax_id, seller_name, seller_tax_id,
                    amount_excluding_tax, tax_amount, amount_including_tax, tax_rate,
                    currency, check_code, status
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'confirmed')
                """,
                (document_id,) + values,
            )

    @staticmethod
    def _jsonable_row(row: Any) -> Dict[str, Any]:
        result = dict(row)
        for key, value in list(result.items()):
            if isinstance(value, UUID):
                result[key] = str(value)
        return result
