# V3 Phase A Gate Status — 2026-08-30

Status: **NO-GO pending reviewed manifest apply and post-apply verification**.

This record captures the local PostgreSQL evidence reported from the formal
`projectrag` database and disposable `projectrag_test` test database. It does
not claim that a production write or migration has been executed by the remote
agent.

## PostgreSQL test evidence

Reported command:

```bash
TEST_DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag_test \
uv run --with pytest --with pytest-asyncio --with httpx --with psycopg pytest \
  tests/test_v3_boundary_hotfix.py \
  tests/test_v3_tax_api_split.py \
  tests/test_v3_party_reference_resolution.py
```

Reported result: **26 passed in 2.58s**.

This is current PostgreSQL evidence for the Phase-A boundary/API/reference
resolver tests. It does not by itself replace strict schema audit, baseline
archive, approved migration application evidence, or post-write preflight.

## Formal database party-reference scan

Reported command:

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3_party_reference_resolution.py \
  --json v3_party_reference_conflicts.json
```

Reported result:

- status: `FAIL` because unresolved references remain;
- resolved distinct legal party codes: 17;
- unresolved distinct codes: 3;
- blank legacy rows: 4 in `real_costs.counterparty_code`;
- blank rows remain historical no-counterparty evidence and are not auto-rewritten.

### Reviewed mappings

1. `EXT-TF -> E0`
   - `contracts.id`: 138, 141
   - `invoices.id`: 287
   - source evidence identifies the Tianfu International Financial Center Phase II owner and existing `external_parties` master `E0`.

2. `EXT-001 -> EXT-PG`
   - `cashflows.id`: 192, 193
   - bank-receipt/source note references `EXT-PG`; existing master is the Pangang special-steel supplier.

3. `EXT-CRANE -> EXT-CQ-HEAVY-CRANE`
   - `real_costs.id`: 14
   - source note identifies the Chongqing heavy-crane lifting supplier matching the existing canonical external-party record.

The executable reviewed manifest is archived at:

`docs/v3-gates/phase_a_party_resolution_20260830.json`

All three decisions are `MAP_TO_EXISTING`; no new master party is invented.

## Required evidence before Phase A GO

1. Apply the reviewed manifest in one transaction against the explicitly confirmed formal database.
2. Rerun `v3_party_reference_resolution.py`; expected unresolved/sentinel count is zero.
3. Rerun `v3_boundary_preflight.py --strict` and archive the post-apply report.
4. Confirm the shared Tax + RAG schema audit is strict PASS.
5. Confirm the baseline snapshot is archived.
6. Apply approved boundary revision 73 only when formally authorized, then repeat schema/preflight verification.

Until those items are complete, Task 05 schema code may exist on the feature branch,
but revisions 74/75 must not be applied to the formal database and Task 06 backfill
must not be treated as started.
