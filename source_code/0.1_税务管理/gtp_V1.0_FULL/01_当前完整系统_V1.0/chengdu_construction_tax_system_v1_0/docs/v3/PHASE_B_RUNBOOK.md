# V3 Phase B — Party / Taxpayer Runbook

Source of truth: **成都建工 V3.0 Database Core v1.2**.

This branch stages Task 05 code after the real corrective revision 73. Production
migration remains blocked until Phase A Gate S0 is approved; staging code is not
permission to apply revisions 74/75 to the formal database.

## Revision chain

```text
72_v3_boundary_hotfix              (historical; never rewrite)
73_v3_boundary_entity_refs         (corrective boundary completion)
74_v3_party_source_documents       (Task 05 source + Party foundation)
75_v3_taxpayer_profiles            (Task 05 reporting-taxpayer profiles)
```

## Revision 74

Creates `source_documents`, `parties`, `internal_entities`, and
`party_identifiers`.

The existing legacy `external_parties` table is **not dropped or recreated**.
Revision 74 only adds nullable `party_id -> parties.id`, a unique bridge
constraint, and `industry`. Legacy `id/code/name/tax_id/kind/active` remain
readable until Task 06 backfill and later cutover.

Document status is restricted to:

`RECEIVED / PARSED / EXTRACTED / VALIDATED / FAILED`.

`UNIQUE(source_system, external_document_id)` is authoritative when an upstream
ID exists. For sources without such an ID, `(source_system, file_sha256)` is an
import-idempotency key only; it must never become a Fact business identity.

`party_identifiers.identifier_value` is deliberately **not globally unique**.
A tax registration number may legitimately appear on multiple business parties.

## Revision 75

Creates `party_tax_profiles` with:

- `party_id`
- `tax_type`
- `reporting_party_id`
- `taxpayer_category`
- `tax_registration_id`
- `effective_from / effective_to`
- `rule_version`
- `source`
- `reviewed`

PostgreSQL `btree_gist` plus an `EXCLUDE USING gist` constraint prevents two
profiles for the same `(party_id, tax_type)` from overlapping in effective dates.
The extension is not dropped on downgrade because the shared database may use it
elsewhere.

## Deterministic resolver

`app/domain/party/resolver.py` contains the pure resolver. It performs no DB I/O
and no fuzzy/AI matching. It fails closed on overlapping active profiles and on
reporting-party cycles. Unreviewed mappings are ignored by default.

Example business meaning:

```text
business party A04
  -> reviewed VAT profile
  -> reporting party A03
```

Project/business attribution may remain A04 while the VAT ledger belongs to A03.

## Tests before any approved DB apply

```bash
pytest -q \
  tests/test_v3_party_domain.py \
  tests/test_v3_party_schema_contract.py
```

These tests do not require production data.

## After applying 74/75 to an approved disposable PostgreSQL target

First verify Task 05 directly:

```bash
DATABASE_URL=... python3 scripts/v3_party_schema_verify.py \
  --json v3_party_schema_verify.json
```

Then run the full shared Tax + RAG audit with Party metadata preloaded:

```bash
DATABASE_URL=... python3 scripts/v3_schema_audit_party.py \
  --strict --json v3_schema_audit_party.json
```

Expected Task 05 result:

- schema exists and Tax Alembic head is `75_v3_taxpayer_profiles`;
- daterange exclusion constraint exists;
- legacy external parties are still present;
- `external_parties.party_id` may still be NULL because Task 06 owns backfill.

## Explicitly deferred to Task 06

- 26 canonical internal-party backfill;
- ExternalParty -> Party 100% linkage;
- `party_migration_conflicts` population and human review;
- parent/reporting cycle audit on real data;
- A04 -> A03 and similar real reporting mappings;
- resolving the six Phase-A formal-database party-reference conflicts.

No Invoice Fact migration starts until Gate S1 is satisfied.
