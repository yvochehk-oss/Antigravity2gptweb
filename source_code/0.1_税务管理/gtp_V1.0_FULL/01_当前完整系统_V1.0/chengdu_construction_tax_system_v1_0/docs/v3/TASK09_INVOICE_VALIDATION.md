# Task 09 — Evidence-Aware Invoice Validation

Task 09 validates V3 Invoice Facts without changing invoice business values and
without consuming a new Alembic revision. The Tax database/disk head remains:

`79_v3_legacy_invoice_pilot_bridge`

Revision 80 is intentionally left free for Task 10 Input VAT Claims.

## Preconditions

- Gate S1 = PASS / EXPLAINED-PASS.
- Gate S07A = PASS.
- Gate S07B = PASS.
- Gate S08 = PASS.
- Formal PostgreSQL database is at revision 79.
- Already-applied revisions 78 and 79 are immutable.

## Validation semantics

Task 09 has three outcomes:

- `VALID`: complete stored evidence satisfies every deterministic rule.
- `INVALID`: stored facts contain a deterministic contradiction.
- `NEEDS_REVIEW`: evidence or reviewed rule context is incomplete/ambiguous.

Missing evidence is never converted into a guessed fact.

### Deterministic INVALID examples

- seller and buyer are the same Party;
- `facts.business_identity_key` disagrees with the Invoice identity;
- a supported `DIGITAL_V1` / `LEGACY_V1` identity key disagrees with the
  Task07a deterministic identity builder;
- gross amount differs from net + VAT beyond the existing 0.01 integrity
  tolerance;
- complete line sums disagree with header net or VAT;
- a line tax rate is outside an explicitly reviewed/versioned allowed-rate set;
- a red invoice has positive amounts or lacks `REVERSAL_OF`;
- a voided invoice lacks the Task07b `VOID_RELATION` event relationship.

### NEEDS_REVIEW examples

- Task08 `LEGACY_MIGRATION_V1` identity;
- missing/unresolved seller or buyer Party;
- unsupported/uncanonical identity version;
- missing invoice number/date/document status;
- seller Party lacks exactly one active `TAX_REGISTRATION_ID` identifier;
- missing source-document provenance or linked document is not `VALIDATED`;
- missing/incomplete invoice lines;
- missing line tax rate;
- invoice lines exist but no reviewed/versioned tax-rate rules were supplied.

Task09 never hard-codes invoice number/code lengths or statutory tax-rate
numbers. Those rules can change and must not be silently embedded as timeless
truth.

## Identity contracts reused from Task07a

Task09 reuses the immutable Task07a identity contracts:

- `DIGITAL_V1`: normalized invoice number.
- `LEGACY_V1`: normalized seller tax identity + invoice code + invoice number.

The seller tax identity comes from the Party layer's active
`TAX_REGISTRATION_ID`, which Task06 backfilled from the legacy masters.

`LEGACY_MIGRATION_V1` is a Task08 migration namespace only and can never be
promoted to `VALID` by Task09.

## Versioned tax-rate manifest

The Task07a validator already requires an explicit reviewed/versioned allowed
rate set before promotion to VALID. Task09 preserves that contract.

A manifest has this shape:

```json
{
  "kind": "V3_INVOICE_TAX_RATE_RULES",
  "version": 1,
  "rule_version": "<reviewed rule version>",
  "reviewed": true,
  "reviewed_by": "<reviewer>",
  "source": "<approved rule source>",
  "allowed_tax_rates": ["<decimal rate>"]
}
```

Do not copy example tax percentages from code or memory. Populate the set only
from the project's approved/versioned tax-rule evidence.

The manifest is normalized and embedded in the PLAN JSON. APPLY never reloads
the external manifest, so changing a file after review cannot silently change an
approved plan.

For the current Task08 formal pilot Facts, a rate manifest is not required to
run Task09: those Facts have no invoice-line evidence and must remain
`NEEDS_REVIEW` regardless.

## 1. PostgreSQL regression suite

Run all prior V3 suites plus Task09:

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
  tests/test_v3_invoice_validation.py
```

Task09 tests include pure rule tests plus PostgreSQL transactional tests for
promotion and stale-plan rejection.

## 2. Confirm schema head

Task09 has no new business migration. Running upgrade is safe and should remain
at revision 79:

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run alembic upgrade head
```

`alembic/env.py` owns only the technical Alembic version-column capacity
bootstrap. It creates/widens `alembic_version_tax.version_num` to support long
revision IDs. Applied revision files are not rewritten for this purpose.

## 3. Generate a read-only validation plan

For the current Task08 formal pilot:

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/invoice_validation.py \
  --all-review \
  --json v3_invoice_validation_plan.json
```

This is read-only. Review every `desired_status` and finding code. The 13
Task08 migration Facts are expected to stay `NEEDS_REVIEW`; Task09 must not
manufacture missing invoice date, source-document, line, status, or legal
identity evidence.

For a future complete set of document-backed invoices, bind reviewed rate rules:

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/invoice_validation.py \
  --ids <fact_id,...> \
  --rate-rules reviewed_invoice_tax_rates.json \
  --json v3_invoice_validation_plan.json
```

The maximum selection is 1000 current Invoice Facts per plan.

## 4. Apply the exact saved plan

Only after reviewing the PLAN JSON:

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/invoice_validation.py \
  --plan v3_invoice_validation_plan.json \
  --apply \
  --confirm-database projectrag \
  --json v3_invoice_validation_result.json
```

APPLY re-reads the exact selected Facts, source-document status, Party tax
identity, line facts and Fact relationships. If any evidence or prior validation
status changed, the saved digest is stale and the transaction is rejected.

The only business-table mutation is `facts.validation_status`; every selected
Fact receives a `V3_INVOICE_VALIDATION` audit record. Invoice header/line values,
Parties, provenance and relationships are never edited by Task09.

## 5. Gate S09

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/invoice_gate_09.py \
  --result v3_invoice_validation_result.json \
  --json v3_gate_09_invoice_validation.json
```

Gate S09 requires:

- DB and disk heads are still exactly revision 79;
- every selected stored status equals a fresh deterministic recalculation;
- result finding codes still match current evidence;
- no `LEGACY_MIGRATION_V1` Fact is `VALID`;
- all current globally `VALID` Invoice Facts pass the same Task09 rules and the
  same rate-rule context bound to the result;
- Task08 migration Facts are not promoted to VALID.

Task10 must not begin until S09 is PASS (or an explicitly approved explained
pass under the V3 migration rules).
