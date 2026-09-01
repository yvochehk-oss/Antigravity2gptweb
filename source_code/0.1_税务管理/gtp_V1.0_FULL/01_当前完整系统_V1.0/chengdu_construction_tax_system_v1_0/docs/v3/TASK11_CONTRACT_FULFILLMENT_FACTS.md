# Task 11 — ContractFact + FulfillmentFact

Task11 introduces Contract and Fulfillment as V3 Fact subtypes. It is additive
and does not migrate legacy `contracts` or `fulfillment` rows yet.

## Invariants

1. Revision chain is `80_v3_input_vat_claims -> 81_v3_contract_fulfillment_facts`.
2. `contract_facts.fact_id` and `fulfillment_facts.fact_id` are real FKs to
   `facts.id`.
3. Contract buyer/seller and Fulfillment performing/receiving parties are Party
   FKs; legacy string party codes are not copied into the V3 Fact tables.
4. `project_id` is absent. Project attribution belongs to the later allocation
   layer, not to the business Fact itself.
5. `internal_trade` is absent. It is derived deterministically: both resolved
   Contract parties must exist in `internal_entities`.
6. Missing buyer/seller Party means internal-trade state is unknown, not False.
7. A FulfillmentFact may have `contract_fact_id = NULL`. RAG/document extraction
   can create a Fulfillment Fact before a deterministic Contract link is known.
8. Evidence completeness is represented through Fact validation/provenance, not
   a duplicated `evidence_complete` boolean.
9. No legacy Contract/Fulfillment rows are deleted, rewritten or backfilled by
   revision 81.

## PostgreSQL verification

From the Tax application root:

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
  tests/test_v3_invoice_validation.py \
  tests/test_v3_input_vat_claims.py \
  tests/test_v3_contract_fulfillment_facts.py
```

Then upgrade the formal database:

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run alembic upgrade head

DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run alembic current
```

Run Gate S11:

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3/contract_fulfillment_gate_11.py \
  --json v3_gate_11_contract_fulfillment.json
```

Finally re-run strict shared-schema audit:

```bash
DATABASE_URL=postgresql://yvoche@localhost:5432/projectrag \
uv run python scripts/v3_schema_audit.py --strict \
  --json v3_schema_audit_after_task11.json
```

## Gate interpretation

At initial rollout, both new tables may legitimately contain zero rows. S11 is a
schema/semantic boundary Gate, not a legacy migration Gate. PASS requires the
correct head, real FKs, no prohibited duplicate axes, no wrong Fact subtype rows,
no same-party transactions and a clean strict schema audit.

Legacy Contract/Fulfillment migration is a later explicit task and must use
reviewed mappings/provenance rather than silently copying `internal_trade`,
`project_id` or party-code strings.
