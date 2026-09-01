# Task14b — Reviewed Output VAT Period Completeness

## Why this additive correction exists

Revision 84 correctly separated `output_vat_period` from `invoice_date`, but the
formal A08/2025-09 review exposed a remaining completeness gap: an empty set of
Output VAT events could otherwise be interpreted as zero without positive review
evidence that the month was complete.

Revision 85 is therefore additive. Revision 84 remains immutable.

## New evidence object

`vat_output_period_assertions` stores exactly one reviewed assertion for each
`(reporting_party_id, tax_period)`:

- `asserted_output_vat_total` is the reviewed complete monthly Output VAT total;
- `0.00` is permitted and is the explicit representation of a reviewed zero-Output-VAT month;
- `source` is mandatory;
- reviewed rows require reviewer and reviewed timestamp;
- no `invoice_date`, `project_id`, `deductible`, CashFlow or accrual fields exist.

## Database enforcement

`trg_v3_guard_entity_vat_ledger_output_completeness` rejects an Entity VAT Ledger
INSERT/UPDATE unless all three values agree exactly:

1. reviewed `vat_output_period_assertions.asserted_output_vat_total`;
2. SUM of `CONFIRMED output_vat_events.vat_amount` for the legal entity/month;
3. `entity_vat_ledgers.output_vat`.

This rule is enforced even if application code is bypassed.

## Human-reviewed assertion loader

Use `scripts/v3/vat_output_assertion.py`.

The loader is fail-closed:

- manifest must be `reviewed=true` with reviewer and timestamp;
- the asserted total must already equal the sum of confirmed Output VAT events;
- a reviewed zero assertion is allowed only when the reviewer deliberately records 0.00;
- existing conflicting assertions are never overwritten;
- default mode is PLAN/read-only; APPLY requires exact database confirmation.

Template: `docs/v3/vat_output_assertion.template.json`.

## Revision-85 wrappers

To preserve user-local fixes in the original Task14 scripts while avoiding
rewriting them, revision 85 uses thin wrappers:

- `scripts/v3/entity_vat_ledger_85.py`
- `scripts/v3/vat_ledger_evidence_85.py`
- `scripts/v3/vat_review_packet_85.py`
- `scripts/v3/entity_vat_gate_14b.py`

The ledger wrapper injects the reviewed assertion into the source snapshot, so
PLAN digests and stale-plan detection include the completeness evidence.

## Formal A08/2025-09 state

Task14 remains blocked. Existing evidence establishes:

- `input_vat_claim_id=1` is `LEGACY_ASSUMPTION / LOW / NEEDS_REVIEW`;
- no confirmed Output VAT event currently exists;
- no prior official VAT ledger exists;
- no opening-balance seed exists;
- the repository does not contain the invoice source file identified by the
  legacy note, so no automatic evidence promotion is permitted.

The remaining formal review must separately resolve:

1. Input VAT claim 1 from real evidence;
2. opening Input VAT credit for the first ledger month;
3. monthly Output VAT completeness, including an explicit reviewed 0.00 if zero
   Output VAT is genuinely supported.

Entity Tax Ledger remains blocked until the final Task14/S14b Gate passes.
