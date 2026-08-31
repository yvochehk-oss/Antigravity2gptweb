# Task15 — Entity Tax Ledger

## Scope and status

Task15 adds a legal-entity/month Entity Tax Ledger projection.  Its scope is
`(reporting_party_id, tax_period)`, where `reporting_party_id` resolves to
`internal_entities.party_id` and `legal_entity=true`.  It deliberately has no
`project_id`, `entity_code`, or invoice-date axis.

The three revision-87 tables are:

- `entity_tax_management_inputs`: reviewed, versioned, explicit monthly
  `REVENUE` and `REAL_COST` evidence;
- `entity_tax_ledgers`: immutable deterministic result rows;
- `entity_tax_ledger_components`: exactly one typed `REVENUE`, `REAL_COST`, and
  `ESTIMATED_CIT` component per result.

The ledger references the official current `entity_vat_ledgers` result for the
same legal entity/month.  VAT is a dependency and is never added to revenue,
real cost, profit, or CIT by this task.

This project is explicitly a user-authorized simulation test project.  The
formal review material is
`V3.0/项目存档资料/01_天府国际金融中心二期_CD-TF-001/10_模拟测试资料/TASK15_A08_2025-09_法人月度收入成本审核资料.md`.
It is not a real business financial statement, accounting record, tax return,
or external proof.

The formal database `projectrag` is now at Tax
`87_v3_entity_tax_ledgers` and RAG `019_timezone_aware_timestamps`.  It contains
two reviewed management inputs, one Entity Tax Ledger, three typed components,
and one official VAT Ledger for A08 / 2025-09.  Gate artifact
`docs/v3/simulation/task15_formal_gate.json` is `PASS` with exit code `0` and
`failures = []`; the strict schema audit artifact is also PASS with empty
failures and warnings.  The formal Gate PASS is limited to this authorized
simulation pilot.  Independent `final_reviewer` acceptance remains pending.

The formal deterministic result for A08 / 2025-09 is:

```text
revenue          = 0.00
real_cost        = 11,320,754.72
estimated_profit = -11,320,754.72
estimated_cit    = 0.00
```

## Deterministic calculation contract

The calculation engine is `app/domain/tax/entity_tax_ledger.py` and the fixed
ruleset is `V3_ENTITY_TAX_LEDGER_V1`:

```text
revenue        = selected reviewed REVENUE input amount
real_cost      = selected reviewed REAL_COST input amount
estimated_profit = revenue - real_cost
estimated_cit  = max(estimated_profit, 0) * reviewed/effective CIT rate
```

Money is normalized to two decimal places using `ROUND_HALF_UP`; the CIT rate is
an explicit reviewed/effective `tax_rules` row selected by rule code.  There is
no default or inferred tax rate.  A missing, unreviewed, ineffective,
ambiguous, malformed, or newer-unreviewed rule/input fails closed.

For each type, the highest `input_version` in the exact party/month scope is
selected.  The input row must already carry the first day of the month as its
`tax_period`; the engine never derives a tax period from an invoice date.  The
input and result payloads are canonical JSON and are bound by SHA-256 hashes in
both `calculation_runs` and `entity_tax_ledgers`.

## Reviewed evidence prerequisites

Before generating a plan, the reviewer must have:

1. an active `internal_entities` row with `legal_entity=true`;
2. one official current VAT ledger for the same party/month, whose
   `TaxPeriodState(tax_type='VAT').current_run_id` points to a SUCCEEDED VAT
   `CalculationRun` in the same scope;
3. one reviewed explicit-period `REVENUE` input and one reviewed explicit-period
   `REAL_COST` input.  Each input is versioned, has a nonblank source and review
   identity/time, and is not a project or invoice-date inference;
4. exactly one reviewed `CIT_GENERAL` (or another explicitly requested code)
   `tax_rules` row effective on the month.  Its rate is part of the hashed
   snapshot and result; changing it requires a new plan and, after close, a
   RESTATEMENT.

An empty management-input or ledger table is not a zero result.  It is
unavailable evidence and Gate S15 fails closed.

## PLAN (read-only)

### Reviewed management-input manifest

The controlled input-entry helper is
scripts/v3/entity_tax_management_input.py. Start from the deliberately
invalid placeholder at
docs/v3/entity_tax_management_input.template.json, replace every
REPLACE_WITH_* / YYYY-* value only with reviewed evidence, and set
reviewed to true. The helper accepts exactly one explicit monthly
REVENUE and one REAL_COST item. Amounts must be nonnegative decimal
strings with at most two places; source_document_id, when present, must
resolve to a current SourceDocument whose status is VALIDATED.

The manifest must not contain project_id, entity_code, invoice_date, or any
legacy TaxLedger reference. It is not valid evidence until the helper accepts
it. A template with placeholders, reviewed=false, or zero values used merely
to satisfy a gate must fail closed and must not be applied.

From the Tax application root, generate a read-only PLAN and review the saved
entity, month, amounts, source documents, existing same-version rows, heads,
and SHA-256 fields:

    DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag uv run python scripts/v3/entity_tax_management_input.py --manifest docs/v3/entity_tax_management_input.template.json --json v3_task15_management_input_plan.json

The output kind is
V3_TASK15_ENTITY_TAX_MANAGEMENT_INPUT_PLAN. PLAN mode opens a PostgreSQL
read-only transaction and does not insert inputs. It requires both the
database and checked-out migration heads to be exactly
87_v3_entity_tax_ledgers.

After an authorized reviewer has checked the exact saved PLAN, APPLY it only
with the matching database name. APPLY re-reads the entity, source documents,
and existing versions under a scope advisory lock, inserts missing rows
atomically, and returns NO_CHANGE for an exact retry:

    DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag uv run python scripts/v3/entity_tax_management_input.py --plan v3_task15_management_input_plan.json --apply --confirm-database projectrag --json v3_task15_management_input_result.json

The output kind is
V3_TASK15_ENTITY_TAX_MANAGEMENT_INPUT_RESULT. This input APPLY is only the
reviewed evidence-entry step; it does not build an Entity Tax Ledger or make
Gate S15 pass. The ledger PLAN/APPLY below remains responsible for the
deterministic calculation and official VAT dependency.

The entity-tax ledger discovery/calculation PLAN remains read-only:

Run from the Tax application root.  `--discover` and ordinary entity/period
invocation only read PostgreSQL:

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/entity_tax_ledger.py \
  --discover \
  --json v3_task15_entity_tax_discovery.json

DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/entity_tax_ledger.py \
  --entity <CANONICAL_ENTITY_CODE> \
  --period <YYYY-MM> \
  --cit-rule-code CIT_GENERAL \
  --json v3_task15_entity_tax_plan.json
```

Review the saved PLAN before applying it.  Confirm the entity, month,
selected input IDs/versions, reviewed sources, effective CIT rule/rate, official
VAT ledger/run, period-state snapshot, and both hashes.  A PLAN is bound to the
database head and source snapshot.  Any evidence or period-state change makes
APPLY stale instead of silently recalculating different data.

## APPLY (explicit write)

APPLY is the only Task15 builder operation that writes the formal calculation
run, period state, immutable ledger, and three components.  It re-reads all
evidence inside a PostgreSQL transaction and verifies the saved PLAN first:

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/entity_tax_ledger.py \
  --plan v3_task15_entity_tax_plan.json \
  --apply \
  --confirm-database projectrag \
  --created-by <REVIEWER> \
  --json v3_task15_entity_tax_result.json
```

For an already `CLOSED` entity-tax month, APPLY additionally requires
`--restatement`.  The new SUCCEEDED `RESTATEMENT` must directly supersede the
previous current run.  The original STANDARD close and every prior ledger
remain historical rows; no stored result is patched in place.

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/entity_tax_ledger.py \
  --plan v3_task15_entity_tax_restatement_plan.json \
  --apply \
  --restatement \
  --confirm-database projectrag \
  --created-by <REVIEWER> \
  --json v3_task15_entity_tax_restatement_result.json
```

The PostgreSQL triggers enforce legal-entity scope, run/hash identity, official
VAT linkage, period-state semantics, reviewed typed input sources,
three-component reconciliation, and terminal immutability.  The builder must
not be used to bypass review or to seed a zero merely to satisfy a gate.

## Gate S15 and strict schema audit

Gate S15 is read-only.  It runs in a repeatable-read read-only transaction and
prints JSON.  Exit code `0` means PASS, `1` means a revision-87 database has a
data/invariant failure (including no pilot), and `2` means the gate is not yet
runnable (for example the formal database is still at revision 86):

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/entity_tax_gate_15.py \
  --json v3_gate_15_entity_tax.json

DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3_schema_audit.py \
  --strict \
  --json v3_schema_audit_after_task15.json
```

Gate S15 checks:

- database and disk head `87_v3_entity_tax_ledgers`;
- all three tables, revision-87 indexes, foreign keys, checks, uniques, and
  protection triggers;
- absence of `project_id`, `entity_code`, and `invoice_date` in all three
  tables;
- legal-entity and same-scope ENTITY_TAX `CalculationRun` /
  `TaxPeriodState.current_run_id`;
- the official same-scope VAT ledger and its SUCCEEDED VAT run;
- deterministic formula, canonical input/result hashes, exact reviewed/effective
  CIT rule and rate, and all three typed component reconciliations;
- latest reviewed explicit-month revenue/cost selection, including blocking a
  higher unreviewed or ambiguous version;
- CLOSED-period direct RESTATEMENT supersession and retained historical ledgers;
- at least one valid formal legal-entity/month pilot.  An empty ledger cannot
  pass.

The existing shared-schema audit imports the Task15 ORM metadata from
`app.models` and sees revision-87 table names in the migration text.  It should
therefore report no Task15 tables as missing or unexplained; do not weaken the
audit to hide a real drift.

## Formal PostgreSQL change procedure

The formal database must remain untouched until the reviewed source package and
PLAN are approved.

1. Confirm the target and current head without writing:

   ```bash
   psql postgresql://yvoche@localhost:5432/projectrag -X -Atc \
     "select current_database(), version_num from alembic_version_tax"
   ```

2. Take a recoverable custom-format backup and record its checksum outside the
   application result directory:

   ```bash
   pg_dump --format=custom \
     --file=/secure/backup/projectrag_before_task15_$(date +%Y%m%d%H%M%S).dump \
     postgresql://yvoche@localhost:5432/projectrag
   shasum -a 256 /secure/backup/projectrag_before_task15_*.dump
   ```

3. Apply only the reviewed migration chain.  Revision 87 is additive and must
   finish at exactly `87_v3_entity_tax_ledgers`:

   ```bash
   DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
   uv run alembic upgrade 87_v3_entity_tax_ledgers
   ```

4. Load only reviewed management inputs and CIT-rule evidence through the
   approved controlled data-entry process, generate and review the exact PLAN,
   then run APPLY.  Never copy a simulation fixture into production without
   matching reviewed source documents.

5. Run Gate S15 and strict schema audit.  A missing pilot, stale hash, scope
   mismatch, unresolved input/rule, or audit warning is a non-PASS result and
   must stop release.

## Rollback and recovery

Before any pilot has been applied, the additive migration can be rolled back on
the disposable validation database with:

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag_test \
uv run alembic downgrade 86_v3_input_vat_claim_review_resolution
```

Do not downgrade the formal database as an experiment after Task15 data exists.
Revision 87 uses restrictive foreign keys and immutable terminal triggers; a
rollback with ledgers would require an approved outage/recovery procedure and
the verified pre-Task15 backup.  Restore into an isolated database first, rerun
the migration and both gates, then obtain a separate change approval before any
formal recovery action.

## Current Gate S15 disposition

The formal A08 / 2025-09 simulation pilot has been applied to `projectrag` at
Tax head `87_v3_entity_tax_ledgers` with RAG head
`019_timezone_aware_timestamps`.  The formal Gate S15 artifact and strict schema
audit are PASS, with the required reviewed inputs, immutable ledger, three
components, official VAT dependency, and no recorded failures or warnings.

**PASS — FORMAL USER-AUTHORIZED SIMULATION PILOT / INDEPENDENT FINAL REVIEWER
PENDING**

This PASS is limited to the explicitly labelled simulation test project.  It is
not a real business financial statement, tax filing, external proof, or an
independent `final_reviewer` PASS.  Do not update this disposition to final
release acceptance until the independent read-only final review is complete.
