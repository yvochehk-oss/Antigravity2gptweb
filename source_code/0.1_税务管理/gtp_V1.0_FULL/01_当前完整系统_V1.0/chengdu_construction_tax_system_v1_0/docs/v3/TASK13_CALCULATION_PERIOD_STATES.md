# Task13 — Calculation Runs / Tax Period States

## Goal

Introduce an append-style calculation-run chain and explicit tax-period state machine before Entity VAT Ledger construction.

The core rule is non-negotiable:

> A CLOSED tax period is never silently overwritten. Any later official result must be a RESTATEMENT run that explicitly supersedes the currently effective run.

## Revision

`83_v3_calculation_runs_period_states`

Revises immutable `82_v3_project_tax_prepayment`.

## Tables

### calculation_runs

Scope is exactly:

- `reporting_party_id`
- `tax_type`
- `tax_period`

A run stores deterministic calculation identity/evidence:

- `run_kind = STANDARD | RESTATEMENT`
- `run_status = DRAFT | SUCCEEDED | FAILED`
- `ruleset_version`
- `input_snapshot_sha256`
- `result_sha256`
- `supersedes_run_id`
- creator and timestamps

No project/cashflow/bank/invoice/accrual axis is stored in this table.

RESTATEMENT requires `supersedes_run_id`; STANDARD forbids it.

A PostgreSQL trigger makes run identity/scope immutable after insert. DRAFT may transition once to SUCCEEDED or FAILED; a terminal run cannot be edited later.

### tax_period_states

Exactly one row per `(reporting_party_id, tax_type, tax_period)`.

States:

- `OPEN`
- `CLOSED`

`current_run_id` is the currently effective official calculation result.

`closed_run_id` is the immutable STANDARD run that first closed the period. It remains unchanged even after one or more restatements.

`state_version` is advanced by the database trigger on each update.

## Database guard semantics

The trigger `trg_v3_guard_tax_period_state` enforces:

1. A current run must be SUCCEEDED and match the same reporting party, tax type and tax period.
2. OPEN period current runs must be STANDARD.
3. First close requires a SUCCEEDED STANDARD run and anchors `closed_run_id=current_run_id`.
4. CLOSED -> OPEN is forbidden.
5. Closed anchor metadata (`closed_run_id`, `closed_by`, `closed_at`) is immutable.
6. Changing `current_run_id` while CLOSED requires a SUCCEEDED RESTATEMENT whose `supersedes_run_id` equals the old current run exactly.

Thus a second restatement supersedes the first restatement, not the original close run.

## Gate S13

Run from Tax application root after the full PostgreSQL suite:

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run alembic upgrade head

DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/calculation_period_gate_13.py \
  --json v3_gate_13_calculation_periods.json

DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3_schema_audit.py --strict \
  --json v3_schema_audit_after_task13.json
```

The initial formal database may contain zero CalculationRun and zero TaxPeriodState rows. Task13 is additive infrastructure and does not close a real tax period merely to make the Gate green.

## What Task13 does not do

- no Entity VAT Ledger yet;
- no tax amount recomputation;
- no project allocation;
- no reader cutover;
- no automatic period close;
- no reopening of closed periods.
