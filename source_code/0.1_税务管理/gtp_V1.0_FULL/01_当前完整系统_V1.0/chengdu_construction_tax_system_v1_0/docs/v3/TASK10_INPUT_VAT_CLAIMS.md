# Task 10 — Input VAT Claims + One Entity-Month Reconciliation

## Purpose

Task10 separates Input VAT attribution from Invoice date and legacy invoice
period. The only period used by the new VAT projection is
`input_vat_claims.claim_period`.

Legacy `invoices.deductible` and `invoices.period` are migration evidence only.
They never become confirmed Input VAT automatically.

## Schema — revision 80

`80_v3_input_vat_claims` adds `input_vat_claims` with:

- `invoice_fact_id` -> `invoice_facts.fact_id`
- `reporting_party_id` -> `internal_entities.party_id`
- `claim_period` (month-start date; the VAT attribution period)
- signed `claim_amount`
- `event_type`: `CLAIM / REVERSAL / ADJUSTMENT`
- `claim_status`: `CONFIRMED / NEEDS_REVIEW / REJECTED / SUPERSEDED`
- `evidence_type`: `DOCUMENT_EVIDENCE / MANUAL_REVIEW / LEGACY_ASSUMPTION`
- `confidence`: `HIGH / MEDIUM / LOW`
- optional source-document and external claim identity
- reviewer/audit metadata

Hard fail-closed rules:

- `LEGACY_ASSUMPTION` must be `LOW + NEEDS_REVIEW`.
- `DOCUMENT_EVIDENCE` requires `source_document_id`.
- `CONFIRMED` cannot be a legacy assumption and requires reviewer metadata.
- `CLAIM` amounts are positive; `REVERSAL` amounts are negative.
- no `project_id`, `invoice_date`, `deductible` or `legacy_period` is stored in
  the claim table.

## Projection rule

Only rows with `claim_status='CONFIRMED'` contribute to Input VAT, grouped by
`reporting_party_id + claim_period`.

`InvoiceFact.invoice_date` does not control Input VAT period.

## Formal execution

Run from the Tax application root.

### 1. Sync and run PostgreSQL regression

```bash
git fetch origin feature/v3-database-core
git pull --rebase origin feature/v3-database-core

TEST_DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag_test \
uv run --with pytest --with pytest-asyncio --with httpx --with psycopg pytest \
  tests/test_v3_boundary_hotfix.py \
  tests/test_v3_tax_api_split.py \
  tests/test_v3_party_reference_resolution.py \
  tests/test_v3_party_backfill.py \
  tests/test_v3_party_domain.py \
  tests/test_v3_party_schema_contract.py \
  tests/test_v3_fact_invoice_core.py \
  tests/test_v3_fact_invoice_relationships.py \
  tests/test_v3_legacy_invoice_pilot.py \
  tests/test_v3_invoice_validation.py \
  tests/test_v3_input_vat_claims.py
```

### 2. Upgrade formal DB to revision 80

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run alembic upgrade head

DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run alembic current
```

Expected head: `80_v3_input_vat_claims`.

### 3. Discover an eligible legal-entity/month

Do not guess a pilot entity or month. Discover from actual mapped legacy data:

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/input_vat_pilot.py \
  --discover \
  --json v3_input_vat_discovery.json
```

Choose one row from `eligible_entity_months`. Eligibility requires all legacy
`direction='in' AND deductible=true AND vat<>0` rows in that entity/month to be
mapped to Invoice Facts and the reviewed VAT reporting party to be stable for
the month.

### 4. Generate read-only PLAN

Replace `<ENTITY>` and `<YYYY-MM>` only with an eligible discovery row:

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/input_vat_pilot.py \
  --entity <ENTITY> \
  --period <YYYY-MM> \
  --json v3_input_vat_plan.json
```

A formal PLAN must have `blockers: []`.

Every Task10 legacy candidate must be:

```text
claim_status = NEEDS_REVIEW
evidence_type = LEGACY_ASSUMPTION
confidence = LOW
```

The legacy invoice period is used only as a review assumption for
`claim_period`; it is not proof of an actual tax filing period.

### 5. Apply exact saved PLAN

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/input_vat_pilot.py \
  --plan v3_input_vat_plan.json \
  --apply \
  --confirm-database projectrag \
  --json v3_input_vat_result.json
```

This creates review candidates only. It must not create confirmed Input VAT.

### 6. Run Gate S10

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/input_vat_gate_10.py \
  --result v3_input_vat_result.json \
  --json v3_gate_10_input_vat.json
```

The one entity-month reconciliation must satisfy:

```text
legacy candidate VAT
= selected NEEDS_REVIEW candidate VAT
+ selected CONFIRMED linked VAT
+ explained residual
```

For the legacy-assumption pilot, confirmed linked VAT must remain zero and the
explained residual must be zero.

### 7. Re-run strict schema audit

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3_schema_audit.py --strict \
  --json v3_schema_audit_after_task10.json
```

## Gate boundary

Task10 does not cut over any production reader and does not create Entity VAT
Ledgers. It only establishes the Input VAT Claim event source and proves one
entity-month migration/reconciliation path.
