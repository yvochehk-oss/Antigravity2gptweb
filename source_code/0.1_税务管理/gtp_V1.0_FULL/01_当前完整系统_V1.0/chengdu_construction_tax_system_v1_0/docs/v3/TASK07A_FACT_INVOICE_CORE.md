# Task 07a — Minimal Fact + Invoice Core

Source: V3.0 Database Core v1.2 Step 2 and the staged construction plan C1.

Task 07 is deliberately split. This file covers 07a only. Do not start 07b
red/void relationships or Task 08 legacy invoice migration until the 07a gate
is green on PostgreSQL.

## Scope

Revision `77_v3_fact_core_invoice` adds only:

- `facts`
- `invoice_facts`
- `invoice_lines`
- `fact_provenance`
- `legacy_invoice_map`

The legacy `invoices` table remains readable and writable by the current
production path. No reader is switched by this task.

## Single-fact rules

A physical invoice is represented once in `invoice_facts`.

Do **not** add these columns to `invoice_facts`:

- `direction`
- `internal_trade`
- `deductible`
- `project_id`

Internal trade is derived from seller/buyer Party types. VAT deductibility is a
later claim/treatment fact. Project allocation is a later allocation layer.

## Versioned invoice identity

Supported Task 07a rules:

- `DIGITAL_V1`: normalized invoice number
- `LEGACY_V1`: normalized seller tax identity + invoice code + invoice number

`invoice_identity_key` is UNIQUE. Do not replace it with a three-column UNIQUE;
NULL behavior on traditional/digital invoice variants would make that contract
ambiguous.

Normalization is part of the identity rule version. `DIGITAL_V1` and
`LEGACY_V1` currently use Unicode NFKC, uppercase, and whitespace removal only.
Changing that behavior in-place is forbidden; introduce a new identity version
instead.

## DRAFT -> VALID validation

`app/domain/invoice/service.py::validate_invoice_fact` performs the one-time
validation before promotion. It does not use per-line aggregate triggers.

Required before VALID:

- seller and buyer Party resolved and different
- invoice date present
- header net/VAT/gross present
- `abs(net + vat - gross) <= 0.01`
- at least one line
- line net/VAT sums match header within 0.01
- line gross sum matches header within 0.01
- each line tax rate belongs to a caller-supplied reviewed/versioned rule set

There is intentionally no numeric tax-rate fallback in the service.

## PostgreSQL test command

After pulling revision 77 into the disposable test database:

```bash
TEST_DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag_test \
uv run --with pytest --with pytest-asyncio --with httpx --with psycopg pytest \
  tests/test_v3_boundary_hotfix.py \
  tests/test_v3_tax_api_split.py \
  tests/test_v3_party_reference_resolution.py \
  tests/test_v3_party_backfill.py \
  tests/test_v3_party_domain.py \
  tests/test_v3_party_schema_contract.py \
  tests/test_v3_fact_invoice_core.py
```

## Formal database schema verification

Only after revision 77 is approved and applied:

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/fact_invoice_gate_07a.py \
  --json v3_gate_07a_fact_invoice.json
```

The verifier is read-only. It checks the Alembic head, required schema,
forbidden duplicate-semantics columns, true FKs, duplicate identities, and any
rows already marked VALID for header/line integrity.

## 07a Gate

Required before 07b:

1. revision 77 applies successfully to disposable PostgreSQL;
2. all Task 07a tests pass on PostgreSQL;
3. `fact_invoice_gate_07a.py` returns PASS on the approved target after migration;
4. a DRAFT invoice can be inserted;
5. duplicate digital identity is rejected by PostgreSQL;
6. an unbalanced invoice cannot be promoted to VALID;
7. no production reader has been switched to the new tables.

Task 08 must not migrate legacy invoice rows yet. `legacy_invoice_map` exists now
only so Task 08 can guarantee zero silent drop later.
