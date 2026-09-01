# Task14 Simulation Pilot — CD-TF-001 / A08 / 2025-09

This runbook is only for the Chengdu Construction synthetic/demo dataset. It uses
normal V3 Fact, Input VAT, CalculationRun, PeriodState and VAT Ledger contracts;
the only special part is that the reviewed business evidence is explicitly marked
as a simulation fixture.

## Fixture

- fixture id: `CD-TF-001-A08-2025-09`
- entity: `A08` / reporting Party `8`
- period: `2025-09`
- legacy InvoiceFact: `6`
- legacy Input VAT claim: `1`
- invoice: `255182174019`
- reviewed Input VAT claim amount: `679245.28`
- simulation opening Input VAT credit: `0.00`
- simulation complete monthly Output VAT: `0.00`
- expected closing Input VAT credit: `679245.28`

Reviewed fixture manifest:

`docs/v3/simulation/task14_cd_tf_001_a08_2025_09.json`

The manifest SHA-binds this project source file:

`项目存档资料/01_天府国际金融中心二期_CD-TF-001/06_增值税发票与税务完税凭证/INVOICE_TF-A08-EXT-EXP_255182174019_增值税专票.pdf`

Expected SHA256:

`81cb120530406d3029a070756eeb10206e98f5494e212dde2542d421d8b98883`

## 1. Preserve local edits and sync

```bash
git fetch origin feature/v3-database-core
git stash push -u
git pull --rebase origin feature/v3-database-core
git stash pop || true
```

Do not reset/discard local `input_vat_pilot.py` fixes.

## 2. Targeted Task14 regression

```bash
TEST_DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag_test \
uv run --with pytest --with pytest-asyncio --with httpx --with psycopg pytest \
  tests/test_v3_entity_vat_ledgers.py \
  tests/test_v3_vat_review_packet.py \
  tests/test_v3_vat_output_period_assertions.py \
  tests/test_v3_input_vat_claim_review_resolution.py \
  tests/test_v3_task14_simulation_pilot.py
```

## 3. Full 19-module V3 regression

Run the existing 18-module suite plus:

`tests/test_v3_task14_simulation_pilot.py`

No formal DB mutation is allowed until the regression is green.

## 4. Verify formal head

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run alembic current
```

Expected:

`86_v3_input_vat_claim_review_resolution (head)`

No new Alembic revision is required.

## 5. Build reviewed simulation PLAN

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/task14_simulation_pilot.py \
  --manifest docs/v3/simulation/task14_cd_tf_001_a08_2025_09.json \
  --source-file "/Users/yvoche/AI开发/073_成都建工/V3.0/项目存档资料/01_天府国际金融中心二期_CD-TF-001/06_增值税发票与税务完税凭证/INVOICE_TF-A08-EXT-EXP_255182174019_增值税专票.pdf" \
  --json v3_task14_simulation_plan.json
```

PLAN must be read-only. It must fail if the local PDF SHA256 differs from the
reviewed fixture manifest or if the legacy Fact/claim state has changed.

## 6. APPLY exact reviewed simulation PLAN

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/task14_simulation_pilot.py \
  --plan v3_task14_simulation_plan.json \
  --apply \
  --confirm-database projectrag \
  --confirm-simulation-fixture CD-TF-001-A08-2025-09 \
  --json v3_task14_simulation_result.json
```

Expected effects in one transaction:

1. VALIDATED SourceDocument for the simulated PDF;
2. deterministic current VALID `DIGITAL_V1` Invoice Fact;
3. one invoice line and FactProvenance;
4. new Fact `REPLACES` legacy InvoiceFact 6;
5. legacy Fact 6 becomes non-current `SUPERSEDED`;
6. new `DOCUMENT_EVIDENCE/HIGH/CONFIRMED` Input VAT claim `679245.28`;
7. legacy claim 1 becomes reviewed `SUPERSEDED`;
8. reviewed opening seed `0.00` for A08 / 2025-09;
9. reviewed explicit Output VAT completeness assertion `0.00` for A08 / 2025-09.

No Output VAT event is created because the reviewed simulation fixture explicitly
states a zero-Output-VAT month.

## 7. Re-discover VAT Ledger eligibility

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/entity_vat_ledger_86.py \
  --discover \
  --json v3_vat_ledger_discovery_after_simulation.json
```

Expected A08 / 2025-09:

- `eligible=true`
- opening source = reviewed seed `0.00`
- Output event count = `0`
- Input claim count = `1`

## 8. Build VAT Ledger PLAN

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/entity_vat_ledger_86.py \
  --entity A08 \
  --period 2025-09 \
  --json v3_vat_ledger_plan_A08_2025-09.json
```

Expected calculation:

- opening Input VAT credit = `0.00`
- Output VAT = `0.00`
- Input VAT = `679245.28`
- VAT prepayment = `0.00`
- payable before prepayment = `0.00`
- closing Input VAT credit = `679245.28`
- payable after prepayment = `0.00`

## 9. APPLY exact VAT Ledger PLAN

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/entity_vat_ledger_86.py \
  --plan v3_vat_ledger_plan_A08_2025-09.json \
  --apply \
  --confirm-database projectrag \
  --created-by admin \
  --json v3_vat_ledger_result_A08_2025-09.json
```

The builder must abort if the source snapshot changed after PLAN review.

## 10. Final S14 Gate and strict audit

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/entity_vat_gate_14c.py \
  --json v3_gate_14c_entity_vat_after_pilot.json

DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3_schema_audit.py --strict \
  --json v3_schema_audit_after_task14_pilot.json
```

Final acceptance requires:

- Gate S14c `status=PASS`, `failures=[]`;
- at least one `official_vat_ledger_id`;
- no unresolved evidence error;
- no source coverage/hash/continuity/formula errors;
- strict schema audit `PASS`, no warnings.

Only after these results are posted may S14 be changed to PASS and Task15 begin.

## Production boundary

This fixture is deliberately labelled synthetic. Do not copy its opening `0.00`,
Output VAT `0.00`, claim period or evidence status into a real company onboarding.
Production business facts must be supported by the actual reviewed records.