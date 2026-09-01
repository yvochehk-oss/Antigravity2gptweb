# Task 08 — Legacy Invoice Small-Batch Pilot

Task 08 is a **single small-batch migration pilot**, not the full invoice cutover.
Production readers continue to use legacy `invoices` throughout this task.

## Preconditions

- Gate S1 = PASS / EXPLAINED-PASS.
- Gate S07A = PASS.
- Gate S07B = PASS.
- Formal PostgreSQL database is at revision `78_v3_invoice_fact_relationships`
  before applying Task 08 schema.
- `legacy_invoice_map` is empty before the pilot.

## Revision 79

Apply the additive provenance bridge:

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run alembic upgrade head
```

Revision `79_v3_legacy_invoice_pilot_bridge` only adds nullable
`real_cost_invoice_links.invoice_fact_id -> invoice_facts.fact_id` plus a
**non-unique** index. The old `invoice_id -> invoices.id` link remains intact.

The bridge is deliberately non-unique because two legacy internal perspectives
may merge into one physical InvoiceFact while both provenance paths must remain
auditable.

## Pilot classification rules

Task 08 does not claim that a legacy row has a complete legal invoice identity.
The old table lacks invoice code, seller tax identity, invoice date, and line
facts. Every Fact created by the pilot therefore uses:

- `invoice_identity_version = LEGACY_MIGRATION_V1`
- `facts.validation_status = NEEDS_REVIEW`
- `invoice_status = NULL`
- zero synthetic `invoice_lines`

Task 09 owns legal validation and promotion to `VALID`.

### Automatic internal merge

Two legacy rows become one InvoiceFact only when all of the following hold:

1. both seller and buyer resolve to internal Party records;
2. exactly two rows exist in the candidate cluster;
3. directions are exactly one `in` and one `out`;
4. project, period, normalized invoice number, seller and buyer agree;
5. category, net, VAT and rate agree exactly after numeric normalization.

Both legacy rows receive `legacy_invoice_map.migration_status = MERGED` and point
to the same InvoiceFact.

### Single perspective

A resolved row with a nonblank invoice number and without an eligible mirror is
migrated as one InvoiceFact with:

- `MIGRATED_SINGLE_PERSPECTIVE`
- Fact status `NEEDS_REVIEW`

No project truth or deductible truth is copied into InvoiceFact.

### Review-only

The pilot creates **no Fact** when:

- invoice number is blank or missing;
- a Party cannot be resolved;
- direction is invalid;
- seller and buyer resolve to the same Party;
- multiple rows share the same coarse identity but are not one exact internal
  IN/OUT pair;
- duplicate/ambiguous external perspectives exist.

Every such legacy row still receives a `legacy_invoice_map` row with
`NEEDS_REVIEW`. There is no silent drop.

## Cluster-atomic selection and hard ceiling

`--limit` is approximate: the planner first classifies the complete unmapped
legacy population, then selects whole actions/clusters. A two-row pair is never
split just because the requested limit ends between the rows.

Explicit `--ids` is also fail-closed. If the supplied IDs select only part of a
pair/ambiguous cluster, planning stops and reports the full cluster.

Task 08 has a hard ceiling of **500 legacy rows**. PLAN and APPLY both refuse a
larger batch. If a single ambiguous cluster itself exceeds 500 rows, the pilot
stops rather than partially migrating that cluster.

## 1. Generate a read-only plan

Start with a deliberately small pilot, for example approximately 20 legacy
rows:

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/invoice_legacy_pilot.py \
  --limit 20 \
  --json v3_invoice_pilot_plan.json
```

This performs no writes. Review every action in the JSON before applying it.
Pay particular attention to:

- `MERGE_PAIR`
- `MIGRATE_SINGLE`
- `REVIEW_ONLY`
- Party IDs
- legacy row IDs
- `plan_digest`

For a hand-selected pilot, use `--ids 1,2,3,...`. The planner refuses a partial
cluster.

## 2. Apply the exact saved plan

Only after reviewing the plan:

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/invoice_legacy_pilot.py \
  --plan v3_invoice_pilot_plan.json \
  --apply \
  --confirm-database projectrag \
  --json v3_invoice_pilot_result.json
```

APPLY re-reads all currently unmapped rows and recalculates cluster membership.
If source data, Party resolution, or pair membership changed after planning, the
saved digest no longer matches and the transaction is refused.

All writes happen in one transaction:

- `facts`
- `invoice_facts`
- `legacy_invoice_map`
- `real_cost_invoice_links.invoice_fact_id`
- `audit_logs`

If anything fails, the whole batch rolls back.

## One-batch safety rule

This Task 08 runner intentionally refuses a second pilot once
`legacy_invoice_map` contains any rows. A later newly-arrived legacy row could be
the missing mirror of a previously migrated singleton; resolving that situation
requires an explicit reviewed reconciliation workflow, not another blind pilot.

## 3. Run the Task 08 Gate

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/invoice_gate_08.py \
  --result v3_invoice_pilot_result.json \
  --json v3_gate_08_invoice_pilot.json
```

Gate S08 requires:

- revision 79 is DB/disk head;
- selected batch is at most 500 rows;
- selected legacy rows have 100% `legacy_invoice_map` coverage;
- no selected row silently disappears;
- MERGED actions contain exactly two rows with IN/OUT directions and one Fact;
- single-perspective actions contain exactly one row and one Fact;
- REVIEW_ONLY actions have no invented Fact;
- every created Task 08 Fact remains `NEEDS_REVIEW`;
- every created InvoiceFact uses `LEGACY_MIGRATION_V1`;
- Task 08 creates no invoice status and no synthetic invoice lines;
- header net/VAT/gross equal the representative legacy evidence;
- `real_cost_invoice_links.invoice_fact_id` agrees with `legacy_invoice_map`;
- no migration-only InvoiceFacts exist outside the recorded result;
- invoice identity remains unique.

Task 09 must not begin until S08 is PASS (or an explicitly documented explained
pass approved under the V3 migration rules).

## PostgreSQL regression suite

Run the prior suites plus Task 08:

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
  tests/test_v3_legacy_invoice_pilot.py
```
