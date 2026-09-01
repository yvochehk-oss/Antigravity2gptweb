"""Deterministic PaymentFact rules for Task19."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import hashlib, json
from typing import Any

MONEY = Decimal("0.01")
PAYMENT_NATURES = frozenset({"NORMAL","ADVANCE","DEPOSIT","GUARANTEE","REFUND","TAX","PAYROLL","OTHER"})
SETTLEMENT_METHODS = frozenset({"BANK_TRANSFER","CASH","BILL","OFFSET","OTHER","UNKNOWN"})
PAYMENT_IDENTITY_VERSION = "TASK19_SOURCE_ROW_V1"
PAYMENT_RULESET_VERSION = "V3_PAYMENT_VALIDATION_V1"

class PaymentFactError(ValueError): pass

@dataclass(frozen=True)
class PaymentCandidate:
    payer_party_id: int
    payee_party_id: int
    transaction_date: date
    amount: Decimal
    currency: str = "CNY"
    payer_account_id: int | None = None
    payee_account_id: int | None = None
    bank_reference: str | None = None
    settlement_method: str = "UNKNOWN"
    payment_nature: str = "OTHER"
    def as_dict(self) -> dict[str, Any]:
        return {"payer_party_id":self.payer_party_id,"payee_party_id":self.payee_party_id,"transaction_date":str(self.transaction_date),"amount":str(self.amount),"currency":self.currency,"payer_account_id":self.payer_account_id,"payee_account_id":self.payee_account_id,"bank_reference":self.bank_reference,"settlement_method":self.settlement_method,"payment_nature":self.payment_nature}

def money(value: Any) -> Decimal: return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)
def canonical_hash(payload: Any) -> str:
    rendered=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()

def build_payment_business_identity_key(*, source_domain: str, source_row_id: Any) -> str:
    """Build the stable Task19 source-row Payment business identity.

    Task19 legacy CashFlow used PAYMENT|LEGACY_CASHFLOW|ROW|<id>. Task28
    reuses that exact source-domain/source-row identity shape for IDP documents.
    No amount/date/account/direction field participates in identity.
    """
    domain = str(source_domain).strip().upper()
    row_id = str(source_row_id).strip()
    if not domain:
        raise PaymentFactError("payment source_domain is required")
    if not row_id:
        raise PaymentFactError("payment source_row_id is required")
    if "|" in domain or "|" in row_id:
        raise PaymentFactError("payment identity components may not contain '|'")
    key = f"PAYMENT|{domain}|ROW|{row_id}"
    if len(key) > 240:
        raise PaymentFactError("payment business_identity_key exceeds 240 characters")
    return key

def validate_candidate(candidate: PaymentCandidate) -> PaymentCandidate:
    if candidate.payer_party_id == candidate.payee_party_id: raise PaymentFactError("payer and payee must be different parties")
    if candidate.amount <= 0: raise PaymentFactError("payment amount must be positive; direction belongs to payer/payee")
    if len(candidate.currency)!=3 or candidate.currency.upper()!=candidate.currency: raise PaymentFactError("currency must be a 3-letter uppercase code")
    if candidate.payment_nature not in PAYMENT_NATURES: raise PaymentFactError(f"unsupported payment_nature: {candidate.payment_nature}")
    if candidate.settlement_method not in SETTLEMENT_METHODS: raise PaymentFactError(f"unsupported settlement_method: {candidate.settlement_method}")
    return candidate

def legacy_direction_parties(*, direction: str, entity_party_id: int, counterparty_party_id: int) -> tuple[int,int]:
    normalized=direction.strip().lower()
    if normalized=="out": return entity_party_id,counterparty_party_id
    if normalized=="in": return counterparty_party_id,entity_party_id
    raise PaymentFactError(f"unsupported legacy CashFlow direction: {direction!r}")
