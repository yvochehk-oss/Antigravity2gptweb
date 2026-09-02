# ADR-V3: Dual-Scope Financial & Tax Architecture

Status: Accepted

## Decision

V3 uses one documentary source of truth and two deterministic calculation scopes. The two scopes answer different business questions and must never be merged implicitly.

### 1. Canonical Facts are the single documentary source

Accepted/current Canonical Facts are the only documentary input for V3 analytical calculations. Legacy mixed ORM tables must not be reintroduced as silent fallback sources merely to fill missing values.

### 2. LEGAL_ENTITY_STATUTORY preserves intercompany transactions

A legal entity is an independent accounting and tax reporting subject. Therefore an internal transaction such as `A08 -> B01` remains:

- output/revenue evidence for A08; and
- input/cost evidence for B01.

A legal-entity calculation may aggregate the entity across every project in which it participates, plus explicitly scoped non-project facts. Intercompany transactions are never eliminated from this scope.

The authoritative VAT filing result remains the V3 legal-entity VAT ledger (`entity_vat_ledgers`) with period carry-forward. A Canonical-Facts entity projection must not present itself as a filing amount.

### 3. PROJECT_BOUNDARY_MANAGEMENT eliminates intercompany transactions

A project is an economic management boundary, not an independent taxpayer. Internal-to-internal trades inside the controlled entity set contribute zero to project consolidated revenue, cost and VAT position while remaining fully traceable in lineage.

Only boundary-crossing flows contribute to project economics:

- internal -> external: project external revenue/output;
- external -> internal: project external resource cost/input;
- internal -> internal: 100% eliminated;
- external -> external: outside project group scope.

Changing an internal transfer price, or inserting additional internal legal-entity layers, must not change the project's external true cost or consolidated true margin.

### 4. Filing semantics are explicit

- Legal-entity statutory ledgers are filing-oriented.
- Project-boundary results are management analytics only.
- Project APIs must expose `is_filing_basis = false`.
- Project signed VAT position may be positive, zero or negative and must never be relabelled as a legal-entity VAT payable/credit balance.

### 5. Input-VAT deductibility is tri-state

Document extraction uncertainty is not a legal conclusion. Deductibility must be classified as:

- `ELIGIBLE`;
- `INELIGIBLE`; or
- `NEEDS_REVIEW`.

Missing/unknown evidence must resolve to `NEEDS_REVIEW`, never silently to `False` and never silently to `True`.

The following invariant is mandatory in every scope that reports input VAT:

`input_vat = deductible_input_vat + nondeductible_input_vat + pending_input_vat`

Any non-zero unaccounted amount degrades the result and blocks trusted presentation.

### 6. No synthetic financial facts

UI or API layers must not manufacture budgets, WBS costs, tax amounts, counterparties, project owners or other financial facts when source data is missing. Missing data must remain explicit (`DEGRADED`, `NEEDS_REVIEW`, or empty state).

## Consequences

1. One fact may participate in both scopes, but with different deterministic treatment.
2. Internal trades remain fully auditable even when eliminated from project consolidated metrics.
3. Legal-entity cross-project aggregation and project-boundary consolidation are separate services/read models.
4. Frontend components must label the active scope clearly and must not infer filing semantics from project metrics.
5. Regression tests must lock the invariants above before changes are accepted.
