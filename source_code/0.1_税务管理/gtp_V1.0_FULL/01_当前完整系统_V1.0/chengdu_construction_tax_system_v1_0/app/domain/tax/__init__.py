"""Deterministic entity-tax domain rules.

The public API is intentionally small.  Database orchestration belongs to the
Task 15 builder; this package validates its evidence and computes the entity
monthly result without consulting an invoice date or guessing a tax rate.
"""

from .entity_tax_ledger import (
    ENTITY_TAX_TYPE,
    MONEY_QUANTUM,
    RULESET_VERSION,
    EntityTaxEvidenceError,
    EntityTaxInputView,
    EntityTaxLedgerCalculation,
    EntityTaxLedgerError,
    EntityTaxLedgerResult,
    EntityTaxManagementInputView,
    EntityTaxRuleError,
    EntityTaxRuleView,
    OfficialVatLedgerView,
    build_entity_tax_input_snapshot,
    calculate_entity_tax,
    calculate_entity_tax_ledger,
    calculate_estimated_cit,
    calculate_profit,
    canonical_hash,
    canonical_json,
    canonical_sha256,
    month_start,
    resolve_effective_cit_rule,
    resolve_effective_tax_rule,
    resolve_management_inputs,
    select_latest_reviewed_management_inputs,
    validate_official_vat_ledger,
)

__all__ = [
    "ENTITY_TAX_TYPE",
    "MONEY_QUANTUM",
    "RULESET_VERSION",
    "EntityTaxEvidenceError",
    "EntityTaxInputView",
    "EntityTaxLedgerCalculation",
    "EntityTaxLedgerError",
    "EntityTaxLedgerResult",
    "EntityTaxManagementInputView",
    "EntityTaxRuleError",
    "EntityTaxRuleView",
    "OfficialVatLedgerView",
    "build_entity_tax_input_snapshot",
    "calculate_entity_tax",
    "calculate_entity_tax_ledger",
    "calculate_estimated_cit",
    "calculate_profit",
    "canonical_hash",
    "canonical_json",
    "canonical_sha256",
    "month_start",
    "resolve_effective_cit_rule",
    "resolve_effective_tax_rule",
    "resolve_management_inputs",
    "select_latest_reviewed_management_inputs",
    "validate_official_vat_ledger",
]
