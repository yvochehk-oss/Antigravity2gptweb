# V3 Task 06 — Party Backfill / Gate S1

Source of truth: 成都建工 V3.0 Database Core v1.2.

Task 06 remains additive. It does **not** migrate invoices or VAT claims and it
does not infer Party identity from fuzzy names or tax IDs.

## Preconditions

1. Phase A evidence is archived.
2. Reviewed legacy party-reference manifest has been applied and strict boundary
   preflight returns no unresolved party codes.
3. Revisions 74 and 75 are applied to the target PostgreSQL database.
4. Revision 76 creates only `party_migration_conflicts`.

If Phase A is still FAIL, this Task 06 code may be reviewed/tested but must not
be applied to the formal database.

## Dry-run plan

```bash
DATABASE_URL=postgresql://... uv run python scripts/v3/party_backfill.py \
  --json v3_party_backfill_plan.json
```

The plan inventories all canonical internal masters and every legacy
`external_parties` row. Exact code is the only automatic identity key.

Conflict types include:

- `CODE_COLLISION`
- `EXISTING_PARTY_TYPE_MISMATCH`
- `NAME_CHANGED`
- `SHARED_TAX_ID`
- `NAME_MULTIPLE_TAX_IDS`
- `EMPTY_TAX_ID`
- `INACTIVE_ENTITY`
- `EXTERNAL_BRIDGE_MISMATCH`
- `PARENT_TARGET_MISSING`
- `PARENT_CYCLE`
- `TAX_PROFILE_REVIEW_REQUIRED`
- `REPORTING_PARTY_CYCLE`

Each conflict records legacy-table usage plus earliest/latest available
period/date evidence. No conflict is resolved automatically.

## Tax reporting profile manifest

Optional reviewed overrides use:

```json
{
  "profiles": [
    {
      "party_code": "A04",
      "tax_type": "VAT",
      "reporting_party_code": "A03",
      "effective_from": "2026-01-01",
      "effective_to": null,
      "rule_version": "manual-20260830",
      "source": "人工复核表",
      "reviewed": true
    }
  ]
}
```

A non-self reporting relationship without `reviewed=true` is a blocking Gate S1
conflict. When no reviewed override exists, Task 06 writes a default unreviewed
VAT self-report profile; this preserves deterministic behavior without guessing.

## Apply

Only after reviewing the dry-run:

```bash
DATABASE_URL=postgresql://... uv run python scripts/v3/party_backfill.py \
  --tax-profile-manifest reviewed_tax_profiles.json \
  --apply \
  --confirm-database projectrag \
  --actor admin \
  --json v3_party_backfill_result.json
```

Apply mode is idempotent. Safe Party identities can be backfilled while unrelated
review conflicts remain. A conflict that blocks identity mapping causes that
code to be skipped; it is never merged by tax ID or name.

## Gate S1 verification

```bash
DATABASE_URL=postgresql://... uv run python scripts/v3/party_gate_s1.py \
  --as-of 2026-08-30 \
  --json v3_gate_s1.json
```

Gate S1 requires:

- active canonical internal entities have 100% Party coverage;
- all legacy `ExternalParty` rows have a valid `party_id` bridge;
- parent chain is acyclic;
- every mapped internal Party has a VAT profile;
- reporting-party resolution is acyclic and deterministic;
- no OPEN blocking Task 06 conflict remains.

Open `EMPTY_TAX_ID`, `INACTIVE_ENTITY`, or `SHARED_TAX_ID` rows are review
evidence and may produce `EXPLAINED`, but they do not authorize identity merging.

Do not start Fact/Invoice Task 07 until Gate S1 is `PASS` or an explicitly
approved `EXPLAINED` state under the project migration discipline.
