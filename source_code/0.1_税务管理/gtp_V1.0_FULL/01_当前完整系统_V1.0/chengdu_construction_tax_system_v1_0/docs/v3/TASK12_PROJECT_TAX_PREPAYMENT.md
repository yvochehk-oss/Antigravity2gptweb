# Task12 — Project Tax Treatment + Tax Prepayment Facts

## Purpose

Task12 introduces the project-specific TAX layer without mixing it with CASH or
ACCRUAL.

- `project_tax_treatments` stores reviewed/versioned project tax-treatment
  evidence by effective period.
- `tax_prepayment_facts` is a real Fact subtype for project tax-prepayment tax
  events.
- No prepayment rate is hard-coded or stored as a schema truth.
- No legacy `projects`, `cashflows`, `progress`, invoices or contracts are
  migrated by revision 82.

## Constitutional boundary

```text
PROJECT TAX TREATMENT (rule/master evidence)
        ↓
TAX PREPAYMENT FACT (tax axis)

≠ bank/cash payment
≠ revenue/cost accrual
```

`TaxPrepaymentFact` may contain `project_id` because this fact is explicitly a
project-tax event. That does not authorize project columns on InvoiceFact,
ContractFact or FulfillmentFact.

## Rule evidence

`ProjectTaxTreatment` contains:

- `project_id`
- `tax_type`
- `treatment_code`
- `reporting_party_id`
- effective date range
- `rule_version`
- `source`
- `reviewed`

There is deliberately no `rate` or `prepayment_rate` column. If a future
calculation derives a prepayment amount from a rate, that rate must come from a
reviewed/versioned rule source and the resulting tax event is then recorded as
a Fact. The historical Fact does not change merely because a later rule version
changes.

Effective windows for the same `(project_id, tax_type)` may not overlap.

## TaxPrepaymentFact

The subtype records only tax-event facts:

- `fact_id`
- `project_id`
- `reporting_party_id`
- `tax_type`
- `tax_period` (month-start)
- `tax_event_date`
- optional `taxable_base`
- `tax_amount`
- `event_type = PREPAYMENT | REVERSAL | ADJUSTMENT`
- `currency`
- optional `treatment_id`
- external source reference / note

It contains no:

- rate / prepayment rate
- cashflow / bank transaction link
- InvoiceFact link
- recognized revenue
- cost amount

A DRAFT/NEEDS_REVIEW prepayment may exist before treatment review is complete.
A current `VALID` TaxPrepaymentFact must have a reviewed treatment that matches
project, tax type, reporting party and is effective on `tax_event_date`.

## Deterministic projection

`app/domain/project_tax.py::validated_tax_prepayment_total()` includes only:

- `Fact.is_current = true`
- `Fact.validation_status = VALID`
- exact reporting party
- exact tax type
- exact tax period
- optional exact project

It never reads `cashflows` or accrual tables.

## Formal execution

Run from the Tax application root.

### 1. Sync

```bash
git fetch origin feature/v3-database-core
git stash
git pull --rebase origin feature/v3-database-core
git stash pop || true
```

### 2. PostgreSQL regression

```bash
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
  tests/test_v3_input_vat_claims.py \
  tests/test_v3_contract_fulfillment_facts.py \
  tests/test_v3_project_tax_prepayment.py
```

### 3. Formal schema migration

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run alembic upgrade head

DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run alembic current
```

Expected Tax head:

```text
82_v3_project_tax_prepayment (head)
```

### 4. Read-only Gate S12

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/project_tax_gate_12.py \
  --json v3_gate_12_project_tax.json
```

Zero ProjectTaxTreatment / TaxPrepaymentFact rows are acceptable at this gate;
Task12 is additive schema/domain construction and performs no legacy backfill.

### 5. Strict shared-schema audit

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3_schema_audit.py --strict \
  --json v3_schema_audit_after_task12.json
```

Do not advance unless both S12 and strict audit are PASS.
