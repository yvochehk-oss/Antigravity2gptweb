# Task14 — Entity VAT Ledger + Components

Task14 builds the first legal-entity VAT calculation projection on top of V3
Facts, explicit VAT-attribution events and Task13 CalculationRun/TaxPeriodState
controls.

## Constitutional boundaries

1. **Invoice date is not Output VAT period.** Output VAT enters a ledger only
   through `output_vat_events.output_vat_period`.
2. **Input VAT uses only `input_vat_claims.claim_period`.** Legacy invoice
   `period/deductible` flags never enter the ledger directly.
3. **Tax prepayment is not Input VAT credit.** Confirmed current VALID
   `tax_prepayment_facts` may reduce VAT payable after the Input VAT continuity
   calculation, but cannot alter `closing_input_credit`.
4. **First-period opening credit is evidence, not a default.** A ledger requires
   either the previous month's current official ledger or a reviewed
   `vat_opening_balance_seed`.
5. **Components use typed real FKs.** No fake polymorphic `source_type/source_id`
   relationship is permitted.
6. **CLOSED periods obey Task13.** A changed CLOSED month requires an explicit
   RESTATEMENT run that supersedes the prior current run.
7. **Rebuild from the earliest changed period forward.** Later opening credits
   depend on earlier closing credits, so a changed earlier VAT event invalidates
   downstream ledger snapshots.

## Revision 84

`84_v3_entity_vat_ledgers` adds:

- `output_vat_events`
- `vat_opening_balance_seeds`
- `entity_vat_ledgers`
- `entity_vat_ledger_components`

The revision is additive and does not alter legacy Invoice, CashFlow, Contract,
Project or accrual tables.

## Deterministic formula

For one reporting party/month:

```text
net_before_prepayment = output_vat - input_vat - opening_input_credit
vat_payable_before_prepayment = max(net_before_prepayment, 0)
closing_input_credit = max(-net_before_prepayment, 0)
vat_payable_after_prepayment = max(vat_payable_before_prepayment - tax_prepayment, 0)
unapplied_tax_prepayment = max(tax_prepayment - vat_payable_before_prepayment, 0)
```

`tax_prepayment` can include signed PREPAYMENT/REVERSAL/ADJUSTMENT facts. An
unapplied tax prepayment is deliberately not converted into Input VAT credit.

## Reviewed Output VAT / opening evidence

Use `docs/v3/vat_ledger_evidence.template.json` only as a template. Do not set
`reviewed=true`, invent an opening zero or promote a legacy assumption merely to
pass Gate S14.

The evidence loader validates reviewed manifest rows against current V3 data:

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/vat_ledger_evidence.py \
  --manifest reviewed_vat_ledger_evidence.json \
  --json v3_vat_ledger_evidence_plan.json
```

After reviewing the dry-run output:

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/vat_ledger_evidence.py \
  --manifest reviewed_vat_ledger_evidence.json \
  --apply \
  --confirm-database projectrag \
  --json v3_vat_ledger_evidence_result.json
```

Confirmed Output VAT evidence requires a current VALID InvoiceFact. OUTPUT and
REVERSAL rows must exactly match the invoice VAT amount; a red invoice is a
REVERSAL, not a mutated blue invoice. DOCUMENT_EVIDENCE requires a real
SourceDocument FK.

## Formal one-entity/month pilot

### 1. Upgrade and run discovery

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run alembic upgrade head

DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/entity_vat_ledger.py \
  --discover \
  --json v3_vat_ledger_discovery.json
```

If there is no `eligible=true` legal-entity/month, **stop**. Typical blockers are
unresolved Input/Output VAT evidence or absence of prior-ledger/opening-balance
evidence. Do not manufacture data to make discovery green.

### 2. Generate an exact read-only PLAN

For one discovered eligible entity/month:

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/entity_vat_ledger.py \
  --entity <ENTITY_CODE> \
  --period <YYYY-MM> \
  --json v3_vat_ledger_plan.json
```

Review the source snapshot, opening source and calculation. The saved plan binds
entity, month, database, period-state snapshot and VAT source snapshot through a
SHA-256 digest.

### 3. Apply the exact saved PLAN

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/entity_vat_ledger.py \
  --plan v3_vat_ledger_plan.json \
  --apply \
  --confirm-database projectrag \
  --created-by <REVIEWER> \
  --json v3_vat_ledger_result.json
```

If the reviewed plan targets a CLOSED period, the command additionally requires
`--restatement`; the Task13 database trigger still verifies that the new run
properly supersedes the prior current run.

If any VAT source changes between PLAN and APPLY, APPLY fails as stale rather
than silently calculating from a different evidence set.

## Gate S14

Run:

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/entity_vat_gate_14.py \
  --json v3_gate_14_entity_vat.json

DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3_schema_audit.py --strict \
  --json v3_schema_audit_after_task14.json
```

Gate S14 checks, among other things:

- Output VAT has an explicit period and valid current InvoiceFact/reporting-party semantics.
- every current official VAT ledger has a SUCCEEDED VAT CalculationRun in the same scope;
- components sum exactly to stored ledger totals;
- the deterministic formula recomputes exactly;
- CalculationRun result hash matches the ledger result;
- opening credit comes from the immediately prior official ledger or reviewed seed;
- current official ledgers cover every confirmed Output VAT event, confirmed Input VAT claim and current VALID VAT prepayment in scope;
- unresolved VAT evidence blocks an official ledger;
- at least one formal legal-entity/month pilot exists;
- strict shared-schema audit has zero warnings/drift.

## Rebuild rule

When an already-attributed VAT event changes, determine the earliest affected
month and rebuild that month forward. An OPEN month can receive a new STANDARD
run. A CLOSED month and every changed downstream CLOSED month require explicit
RESTATEMENT runs. Never patch stored ledger totals in place.
