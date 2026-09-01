"""Task31 deterministic Canonical project-finance read model.

Only current VALID Facts with current CONFIRMED Task29 allocations contribute to
financial totals.  Facts or allocations requiring review are surfaced only as
quality gaps and never leak into the official totals.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.v3_contract_models import ContractFact
from app.v3_fact_models import Fact, InvoiceFact
from app.v3_payment_models import PaymentFact
from app.v3_project_analysis_models import FactProjectAllocation


CANONICAL_PROJECT_FINANCE_V1 = "CANONICAL_PROJECT_FINANCE_V1"
MONEY = Decimal("0.01")


def _money(value: Any) -> Decimal:
    if value is None:
        return Decimal("0.00")
    return Decimal(value).quantize(MONEY)


def _text_money(value: Decimal) -> str:
    return str(value.quantize(MONEY))


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


class CanonicalProjectFinance:
    """Read Task29 allocations as the only project-level monetary attribution."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def _rows(self, project_id: int) -> list[tuple[FactProjectAllocation, Fact]]:
        return list(
            self.session.execute(
                select(FactProjectAllocation, Fact)
                .join(Fact, Fact.id == FactProjectAllocation.fact_id)
                .where(
                    FactProjectAllocation.project_id == project_id,
                    FactProjectAllocation.is_current.is_(True),
                )
                .order_by(FactProjectAllocation.fact_id, FactProjectAllocation.id)
            ).all()
        )

    def read(self, project_id: int) -> dict[str, Any]:
        rows = self._rows(project_id)
        contracts: list[dict[str, Any]] = []
        invoices: list[dict[str, Any]] = []
        payments: list[dict[str, Any]] = []
        quality: list[dict[str, Any]] = []

        contract_total = Decimal("0.00")
        invoice_net = Decimal("0.00")
        invoice_vat = Decimal("0.00")
        invoice_gross = Decimal("0.00")
        payment_total = Decimal("0.00")

        for allocation, fact in rows:
            gap: str | None = None
            if not fact.is_current:
                gap = "FACT_NOT_CURRENT"
            elif fact.validation_status == "NEEDS_REVIEW":
                gap = "FACT_NEEDS_REVIEW"
            elif fact.validation_status != "VALID":
                gap = "FACT_NOT_VALID"
            elif allocation.status != "CONFIRMED":
                gap = "ALLOCATION_NOT_CONFIRMED"

            if gap is not None:
                quality.append(
                    {
                        "code": gap,
                        "fact_id": int(fact.id),
                        "fact_type": str(fact.fact_type),
                        "validation_status": str(fact.validation_status),
                        "allocation_id": int(allocation.id),
                        "allocation_status": str(allocation.status),
                    }
                )
                continue

            common = {
                "fact_id": int(fact.id),
                "business_identity_key": str(fact.business_identity_key),
                "version_no": int(fact.version_no),
                "allocation_id": int(allocation.id),
                "allocation_method": str(allocation.allocation_method),
                "allocation_confidence": str(allocation.confidence),
                "allocated_net": _text_money(_money(allocation.allocated_net)),
                "allocated_vat": _text_money(_money(allocation.allocated_vat)),
                "allocated_gross": _text_money(_money(allocation.allocated_gross)),
            }

            if fact.fact_type == "CONTRACT":
                row = self.session.get(ContractFact, fact.id)
                if row is None:
                    quality.append({"code": "SPECIALIZED_FACT_MISSING", "fact_id": int(fact.id), "fact_type": "CONTRACT"})
                    continue
                amount = _money(allocation.allocated_gross)
                contract_total += amount
                contracts.append(
                    {
                        **common,
                        "contract_number": row.contract_number,
                        "contract_date": _iso(row.contract_date),
                        "contract_category": row.contract_category,
                        "buyer_party_id": row.buyer_party_id,
                        "seller_party_id": row.seller_party_id,
                        "allocated_contract_amount": _text_money(amount),
                        "currency": row.currency,
                    }
                )
                continue

            if fact.fact_type == "INVOICE":
                row = self.session.get(InvoiceFact, fact.id)
                if row is None:
                    quality.append({"code": "SPECIALIZED_FACT_MISSING", "fact_id": int(fact.id), "fact_type": "INVOICE"})
                    continue
                if row.invoice_status == "VOIDED":
                    quality.append({"code": "INVOICE_VOIDED_EXCLUDED", "fact_id": int(fact.id), "fact_type": "INVOICE"})
                    continue
                net = _money(allocation.allocated_net)
                vat = _money(allocation.allocated_vat)
                gross = _money(allocation.allocated_gross)
                invoice_net += net
                invoice_vat += vat
                invoice_gross += gross
                invoices.append(
                    {
                        **common,
                        "invoice_identity_key": row.invoice_identity_key,
                        "invoice_number": row.invoice_number,
                        "invoice_date": _iso(row.invoice_date),
                        "invoice_status": row.invoice_status,
                        "seller_party_id": row.seller_party_id,
                        "buyer_party_id": row.buyer_party_id,
                        "currency": row.currency,
                    }
                )
                continue

            if fact.fact_type == "PAYMENT":
                row = self.session.get(PaymentFact, fact.id)
                if row is None:
                    quality.append({"code": "SPECIALIZED_FACT_MISSING", "fact_id": int(fact.id), "fact_type": "PAYMENT"})
                    continue
                amount = _money(allocation.allocated_gross)
                payment_total += amount
                payments.append(
                    {
                        **common,
                        "payer_party_id": row.payer_party_id,
                        "payee_party_id": row.payee_party_id,
                        "transaction_date": _iso(row.transaction_date),
                        "bank_reference": row.bank_reference,
                        "settlement_method": row.settlement_method,
                        "payment_nature": row.payment_nature,
                        "allocated_payment_amount": _text_money(amount),
                        "currency": row.currency,
                    }
                )
                continue

            quality.append(
                {
                    "code": "UNSUPPORTED_FINANCE_FACT_TYPE",
                    "fact_id": int(fact.id),
                    "fact_type": str(fact.fact_type),
                }
            )

        return {
            "ruleset_version": CANONICAL_PROJECT_FINANCE_V1,
            "project_id": int(project_id),
            "summary": {
                "allocated_contract_amount": _text_money(contract_total),
                "allocated_invoice_net": _text_money(invoice_net),
                "allocated_invoice_vat": _text_money(invoice_vat),
                "allocated_invoice_gross": _text_money(invoice_gross),
                "allocated_payment_amount": _text_money(payment_total),
                "contract_count": len({row["fact_id"] for row in contracts}),
                "invoice_count": len({row["fact_id"] for row in invoices}),
                "payment_count": len({row["fact_id"] for row in payments}),
            },
            "contracts": contracts,
            "invoices": invoices,
            "payments": payments,
            "eligible_fact_ids": sorted(
                {row["fact_id"] for row in contracts}
                | {row["fact_id"] for row in invoices}
                | {row["fact_id"] for row in payments}
            ),
            "evidence_quality": quality,
        }
