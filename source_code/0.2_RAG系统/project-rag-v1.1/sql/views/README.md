# Analytics SQL references (PostgreSQL only)

These files document the deterministic SQL contract used by Facts Provider. **Alembic migrations are the schema source of truth**; do not run these files as an independent migration chain.

Business truth comes from the shared PostgreSQL tables owned by the Tax system: `projects`, `progress`, `real_costs`, `budgets`, `invoices`, and `cashflows`. Missing forward-looking or allocation data is represented as `NULL`, never as a fabricated zero or estimate. Ratios are expressed on a `0..1` scale.
