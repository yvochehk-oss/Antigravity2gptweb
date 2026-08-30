# V3 Phase A / Step 0 Runbook

Source of truth: **成都建工 V3.0 Database Core v1.2 实施指导方案**。

Phase A is a blocking accuracy stage. Do not start Party/Fact cutover until the
four gates below have evidence.

## Task 01 — Schema Drift Audit

```bash
DATABASE_URL=postgresql+psycopg://... python scripts/v3_schema_audit.py --json v3_schema_audit.json
```

Gate S0-01 requires no unexplained schema drift. `FAIL` blocks migration.
`WARNING` items (DB-only tables/triggers/functions) must be reviewed and either
explained or converted to migrations before strict GO.

## Task 02 — Baseline Snapshot

Run against the pre-cutover production copy/read-only target:

```bash
DATABASE_URL=postgresql+psycopg://... python scripts/v3_baseline_snapshot.py \
  --output migration_baseline_YYYYMMDD.json
```

Keep the generated JSON as migration evidence. It records invoice count/net/VAT,
contract/cashflow/real-cost/fulfillment counts, entity-period measures,
project-period measures and `real_cost_invoice_links` count when present.

## Task 03 — Tax API scope correction (Gate S0-02)

- `/api/entity-tax-ledger`: accepts `period` + optional `entity`; rejects
  `project_id` with 422.
- `/api/project-tax-analysis`: requires `project_id`; accepts `period` + optional
  `entity`; derives only from rows carrying that exact project id.
- `/api/tax-ledger?project_id=`: first-stage deprecated compatibility response;
  returns no mixed-scope ledger rows and points callers to the project endpoint.
- Regression gate: `test_project_tax_does_not_leak_other_projects`.

## Task 04 — Legacy party-code length hotfix (Gate S0-03)

Revision `72_v3_boundary_hotfix` temporarily widens these legacy references to
`VARCHAR(64)` until Party FKs replace strings:

- `contracts.buyer_code`, `contracts.seller_code`
- `invoices.entity_code`, `invoices.counterparty_code`
- `cashflows.entity_code`, `cashflows.counterparty_code`
- `fulfillment.counterparty_code`
- `real_costs.entity_code`, `real_costs.counterparty_code`

Canonical `entities.code` and `entities.entity_code` stay `VARCHAR(16)`.

## PostgreSQL integration gate

The test harness uses the fixed test login `admin / 888888` and requires a
disposable PostgreSQL database whose database name contains `test`.

```bash
TEST_DATABASE_URL=postgresql+psycopg://... pytest -q \
  tests/test_v3_boundary_hotfix.py tests/test_v3_tax_api_split.py
```

If the execution sandbox blocks localhost, run the same command in an execution
context with the required network permissions. CI absence is not evidence of a
passing database gate; retain the local test output with the migration records.
