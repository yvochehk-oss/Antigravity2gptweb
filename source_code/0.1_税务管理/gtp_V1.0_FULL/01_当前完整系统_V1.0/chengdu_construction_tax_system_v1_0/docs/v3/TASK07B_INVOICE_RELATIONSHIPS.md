# Task 07b — Invoice Red / Void / Correction Relationships

Task 07b implements the relationship semantics required by V3 Database Core v1.2 after Gate S07A has passed.

## Scope

Revision: `78_v3_invoice_fact_relationships`

Additive changes only:

- add `fact_relationships`
- add `invoice_medium`
- add `invoice_category`
- constrain `invoice_status` to `VALID / VOIDED / RED`
- constrain RED invoice monetary fields to non-positive values
- keep legacy `invoice_type` only as a compatibility field
- keep production readers on legacy `invoices`

No legacy Invoice backfill occurs in this task.

## Relationship direction

`source_fact_id -> target_fact_id`

- `REVERSAL_OF`: red Invoice Fact -> original Invoice Fact
- `REPLACES`: replacement Invoice Fact -> replaced Invoice Fact
- `CORRECTS`: corrected Invoice Fact -> prior Invoice Fact
- `VOID_RELATION`: auditable `INVOICE_STATUS_EVENT` Fact -> voided Invoice Fact

Targets are explicit. Fuzzy invoice-number/amount matching is never allowed to create a relationship automatically.

## Single-fact semantics

A red invoice is not an UPDATE of the blue invoice and is not an amount-zeroing operation.

Example:

- blue Fact net `+100`
- red Fact net `-100`
- both Facts remain present
- red has `REVERSAL_OF -> blue`
- effective projection net = `0`

A void also does not rewrite stored amounts to zero. The invoice retains its original amounts, receives `invoice_status=VOIDED`, and is excluded by projection. The `VOID_RELATION` preserves the audit event.

## Invoice classification axes

Do not add `red_blue_flag` or `original_invoice_id`.

Canonical axes are:

- `invoice_medium`: `DIGITAL / PAPER / OTHER`
- `invoice_category`: `SPECIAL / ORDINARY / OTHER`
- `invoice_status`: `VALID / VOIDED / RED`

The old nullable `invoice_type` remains only because this phase is additive.

## PostgreSQL test command

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
  tests/test_v3_fact_invoice_relationships.py
```

Required cases include:

- blue +100 plus red -100 projects to zero while two Facts remain
- red source has explicit `REVERSAL_OF`
- positive RED amount rejected by PostgreSQL
- void event preserves original amount but projects to zero
- self and duplicate relationships rejected by PostgreSQL

## Formal database migration

Only after the test database is green:

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run alembic upgrade head
```

Then run the read-only Gate:

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/fact_invoice_gate_07b.py \
  --json v3_gate_07b_invoice_relationships.json
```

## Gate S07B

Gate must be `PASS` before Task 08 starts.

Required zero counts:

- positive RED amounts
- self relationships
- orphan relationships
- invalid relationship types
- RED invoices without `REVERSAL_OF`
- VOIDED invoices without `VOID_RELATION`
- reversal semantic mismatches
- void semantic mismatches
- relationship cycles

Task 08 remains prohibited until this gate is executed against PostgreSQL and archived as PASS or explicitly approved EXPLAINED evidence.
