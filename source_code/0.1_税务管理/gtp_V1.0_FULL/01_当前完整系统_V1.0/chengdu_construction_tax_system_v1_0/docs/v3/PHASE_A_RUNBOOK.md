# V3 Phase A / Step 0 Runbook

Source of truth: **成都建工 V3.0 Database Core v1.2 实施指导方案**。

Phase A is a blocking accuracy stage. Do not start Party/Fact cutover until the
four gates below have evidence.

## Task 01 — Schema Drift Audit

```bash
DATABASE_URL=postgresql+psycopg://... python scripts/v3_schema_audit.py \
  --json v3_schema_audit.json
```

Gate S0-01 requires no unexplained schema drift. `FAIL` blocks migration.
`WARNING` items (DB-only tables/triggers/functions, constraint/index/type drift)
must be reviewed and explained or converted to migrations. Use `--strict` for
the final GO/NO-GO evidence.

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

- `/api/entity-tax-ledger`: accepts `period` + optional `entity`/`entity_code`;
  rejects `project_id` with 422.
- `/api/project-tax-analysis`: requires `project_id`; accepts `period` + optional
  `entity`/`entity_code`; derives only from rows carrying that exact project id.
- `/api/tax-ledger?project_id=`: first-stage deprecated compatibility response;
  returns no mixed-scope ledger rows and points callers to the project endpoint.
- Regression gate: `test_project_tax_does_not_leak_other_projects`.
- JSON collection routes are owned by `app/routers/collections.py`; `tax.py`
  remains rebuild/HTML only to avoid duplicate route registration.

## Task 04 — Legacy party-code length hotfix (Gate S0-03)

The deployed migration history is intentionally immutable:

- revision `72_v3_boundary_hotfix` widens the six buyer/seller/counterparty
  references that were already deployed;
- revision `73_v3_boundary_entity_refs` follows 72 and widens the three local
  legacy `entity_code` transaction references required by Database Core v1.2.

Together they make all nine migration-period legacy references `VARCHAR(64)`:

- `contracts.buyer_code`, `contracts.seller_code`
- `invoices.entity_code`, `invoices.counterparty_code`
- `cashflows.entity_code`, `cashflows.counterparty_code`
- `fulfillment.counterparty_code`
- `real_costs.entity_code`, `real_costs.counterparty_code`

Canonical `entities.code` and `entities.entity_code` stay `VARCHAR(16)`.
Never rewrite revision 72 after it has been applied to a deployed database.

Run the data preflight before and after migration:

```bash
DATABASE_URL=postgresql+psycopg://... python scripts/v3_boundary_preflight.py \
  --json v3_boundary_preflight.json
```

The report includes maximum lengths, blanks, `UNKNOWN`, virtual `A/B/C/D` role
codes and values that resolve to neither `entities` nor `external_parties`.
These review items are never auto-corrected.

### Closing unresolved party references

If preflight reports unresolved/sentinel party codes, inventory them with the
review-only resolver:

```bash
DATABASE_URL=postgresql+psycopg://... python scripts/v3_party_reference_resolution.py \
  --json v3_party_reference_conflicts.json
```

No write is performed. Review the original contract/invoice/source evidence and
create a manifest based on `docs/v3/party_reference_resolution_manifest.example.json`.
Allowed reviewed actions are deliberately narrow:

1. `MAP_TO_EXISTING`: rewrite the exact legacy source code to an already-existing
   `entities` or `external_parties` code.
2. `ADD_EXTERNAL_MASTER`: add a verified external-party master row using the same
   source code. `UNKNOWN` and role placeholders `A/B/C/D` can never use this action.

Validate the manifest against the production database without writing:

```bash
DATABASE_URL=postgresql+psycopg://... python scripts/v3_party_reference_resolution.py \
  --manifest reviewed_party_resolution.json \
  --json reviewed_party_resolution_validation.json
```

Only after manual approval, apply it with an exact database-name confirmation:

```bash
DATABASE_URL=postgresql+psycopg://... python scripts/v3_party_reference_resolution.py \
  --manifest reviewed_party_resolution.json \
  --apply \
  --confirm-database <exact_current_database_name> \
  --json reviewed_party_resolution_result.json
```

All decisions are validated before mutation, all writes run in one transaction,
and every decision creates an `audit_logs` record. If any unresolved/sentinel
code remains after the batch, the whole transaction rolls back. Blank legacy
references remain visible in the report but are not automatically rewritten,
because some legacy fields intentionally use blank to mean “no counterparty”.

After a successful resolution, rerun `v3_boundary_preflight.py --strict` and
archive both the before/after reports.

## PostgreSQL integration gate

The test harness uses the fixed test login `admin / 888888` and requires a
disposable PostgreSQL database whose database name contains `test`.

```bash
TEST_DATABASE_URL=postgresql+psycopg://... pytest -q \
  tests/test_v3_boundary_hotfix.py \
  tests/test_v3_tax_api_split.py \
  tests/test_v3_party_reference_resolution.py
```

If the execution sandbox blocks localhost, run the same command in an execution
context with the required network permissions. CI absence is not evidence of a
passing database gate; retain local test output with the migration records.

## Phase A GO evidence

All four are required before Party/Fact work begins:

1. S0-01 schema audit reviewed and strict PASS.
2. Baseline JSON saved and archived.
3. S0-02 project-scope regression tests pass on PostgreSQL.
4. S0-03 revisions 72+73, boundary tests and strict preflight pass with no
   unexplained blocking values.

Alembic revision numbers are historical identifiers, not task names. If a
corrective migration has already consumed the revision originally suggested by
v1.2, never rewrite it; start Task 05 from the next free revision after the real
current head.
