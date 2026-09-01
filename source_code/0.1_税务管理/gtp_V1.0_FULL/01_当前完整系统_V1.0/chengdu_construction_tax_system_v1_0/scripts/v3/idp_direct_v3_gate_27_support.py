"""Support helpers for Gate S27. No production control writes live here."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
import os
from pathlib import Path
import re
from uuid import uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.integration.idp_canonical.contract_role_schemas import ContractRoleCompletionRequest, ContractRoleEvidenceInput
from app.integration.idp_canonical.direct_v3_schemas import DirectV3ProductionRequest
from app.integration.idp_canonical.evidence_schemas import InvoiceEvidenceCompletionRequest, InvoiceLineEvidence, ReviewedTaxRateRules, SourceDocumentEvidence
from app.integration.idp_canonical.schemas import CanonicalIngestRequest
from app.v3_party_models import Party, PartyIdentifier

EXPECTED_HEAD = "97_v3_contract_role_semantics"


class WriteAttemptMonitor:
    PROTECTED = {
        "v3_cutover_finalizations": "production_seal_write_attempted",
        "writer_cutover_states": "cutover_state_write_attempted",
        "invoices": "legacy_write_attempted",
        "invoices_v3": "legacy_write_attempted",
        "contracts_v3": "legacy_write_attempted",
    }

    def __init__(self) -> None:
        self.flags = {value: False for value in set(self.PROTECTED.values())}
        self.pattern = re.compile(r'^\s*(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+"?([A-Za-z0-9_]+)"?', re.I)

    def before_cursor_execute(self, conn, cursor, statement, parameters, context, executemany) -> None:
        match = self.pattern.match(str(statement))
        if not match:
            return
        flag = self.PROTECTED.get(match.group(1).lower())
        if flag:
            self.flags[flag] = True
            raise AssertionError(f"Gate S27 detected forbidden DML against {match.group(1)}")


def database_url() -> str:
    value = os.getenv("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    if make_url(value).get_backend_name() not in {"postgresql", "postgres"}:
        raise SystemExit("Gate S27 is PostgreSQL-only")
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value[len("postgresql://"):]
    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value[len("postgres://"):]
    return value


def disk_heads(root: Path) -> list[str]:
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    return sorted(ScriptDirectory.from_config(cfg).get_heads())


def control_snapshot(session: Session, which: str) -> list[dict]:
    if which == "seal":
        rows = session.execute(text("SELECT scope,finalized_by,finalized_at,state_snapshot,evidence_snapshot FROM v3_cutover_finalizations ORDER BY scope")).mappings().all()
        return [{**dict(row), "finalized_at": str(row["finalized_at"]), "state_snapshot": deepcopy(row["state_snapshot"]), "evidence_snapshot": deepcopy(row["evidence_snapshot"])} for row in rows]
    rows = session.execute(text("SELECT scope,writer_mode,legacy_write_enabled,new_fact_write_enabled,legacy_frozen,new_fact_read_mode,rag_source,updated_by,updated_at FROM writer_cutover_states ORDER BY scope")).mappings().all()
    return [{**dict(row), "updated_at": str(row["updated_at"])} for row in rows]


def legacy_counts(session: Session) -> dict[str, int | None]:
    result = {}
    for name in ("contracts_v3", "invoices_v3", "invoices"):
        exists = session.execute(text("SELECT to_regclass(:n)"), {"n": f"public.{name}"}).scalar_one()
        result[name] = int(session.execute(text(f'SELECT count(*) FROM "{name}"')).scalar_one()) if exists else None
    return result


def party(session: Session, token: str, side: str) -> tuple[Party, str]:
    tax_id = "91" + uuid4().hex[:16].upper()
    row = Party(code=f"S27{side}{token}", name=f"S27 Party {side} {token}", short_name=f"S27 {side}", party_type="external", active=True)
    session.add(row); session.flush()
    session.add(PartyIdentifier(party_id=row.id, identifier_type="TAX_REGISTRATION_ID", identifier_value=tax_id, source_system="GATE_S27", active=True)); session.flush()
    return row, tax_id


def _sha(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def invoice_submission(token: str, suffix: str, seller: Party, seller_tax: str, buyer: Party, buyer_tax: str, *, review_status: str = "approved", invoice_status: str = "VALID") -> DirectV3ProductionRequest:
    doc, ext, digest = f"S27-I-DOC-{suffix}-{token}", f"S27-I-EXT-{suffix}-{token}", _sha(f"S27:I:{suffix}:{token}")
    intake = CanonicalIngestRequest(
        source_system="IDP", source_document_id=doc, source_extraction_id=ext, document_sha256=digest,
        document_type="invoice", review_status=review_status, approved_by="gate:S27", extraction_model="gate-s27", extraction_model_version="1",
        data={"invoice_type": "增值税电子专用发票", "invoice_no": f"S27-INV-{suffix}-{token}", "invoice_date": "2026-09-01", "seller": {"name": seller.name, "tax_id": seller_tax}, "buyer": {"name": buyer.name, "tax_id": buyer_tax}, "amount_excluding_tax": "100.00", "tax_amount": "13.00", "amount_including_tax": "113.00", "tax_rate": "0.13", "currency": "CNY"},
    )
    completion = InvoiceEvidenceCompletionRequest(
        source_system="IDP", source_document_id=doc, source_extraction_id=ext, document_sha256=digest,
        document=SourceDocumentEvidence(filename=f"{doc}.pdf", mime_type="application/pdf", validation_status="VALIDATED", validated_by="gate:S27", validation_reason="Gate S27 reviewed"), invoice_status=invoice_status,
        lines=[InvoiceLineEvidence(line_no=1, item_name="Gate S27 service", quantity=Decimal("1"), unit_price=Decimal("100.00"), net_amount=Decimal("100.00"), vat_amount=Decimal("13.00"), tax_rate=Decimal("0.13"), confidence=0.99)],
        tax_rules=ReviewedTaxRateRules(rule_version="GATE_S27_TAX_V1", reviewed_by="gate:S27", allowed_tax_rates=[Decimal("0.13")]), extraction_model="gate-s27", extraction_model_version="1", confidence=0.99, page_start=1, page_end=1,
    )
    return DirectV3ProductionRequest(intake=intake, invoice_evidence=completion)


def contract_submission(token: str, suffix: str, a: Party, tax_a: str, b: Party, tax_b: str, *, reverse: bool = False) -> DirectV3ProductionRequest:
    doc, ext, digest = f"S27-C-DOC-{suffix}-{token}", f"S27-C-EXT-{suffix}-{token}", _sha(f"S27:C:{suffix}:{token}")
    intake = CanonicalIngestRequest(source_system="IDP", source_document_id=doc, source_extraction_id=ext, document_sha256=digest, document_type="contract", review_status="approved", approved_by="gate:S27", extraction_model="gate-s27", extraction_model_version="1", data={"contract_no": f"S27-HT-{suffix}-{token}", "party_a": {"name": a.name, "tax_id": tax_a}, "party_b": {"name": b.name, "tax_id": tax_b}, "sign_date": "2026-09-01", "currency": "CNY", "amount_tax_included": "113.00"})
    labels = ("承包人", "发包人") if reverse else ("发包人", "承包人")
    roles = ContractRoleCompletionRequest(source_system="IDP", source_document_id=doc, source_extraction_id=ext, document_sha256=digest, submitted_by="gate:S27", evidences=[
        ContractRoleEvidenceInput(source_party_role="PARTY_A", legal_role_label=f"甲方（{labels[0]}）", evidence_text=f"本合同明确甲方（{labels[0]}）承担相应法律角色。", page_no=1, confidence=0.99),
        ContractRoleEvidenceInput(source_party_role="PARTY_B", legal_role_label=f"乙方（{labels[1]}）", evidence_text=f"本合同明确乙方（{labels[1]}）承担相应法律角色。", page_no=1, confidence=0.99),
    ])
    return DirectV3ProductionRequest(intake=intake, contract_roles=roles)
