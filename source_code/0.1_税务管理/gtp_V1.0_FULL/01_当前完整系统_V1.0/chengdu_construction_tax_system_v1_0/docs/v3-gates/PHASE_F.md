# Phase F — CLOSED

Status: **PASS / CLOSED**

Authoritative local PostgreSQL evidence supplied on 2026-08-31:

- Task16 Three Basis Separation: PASS.
- Task17 Project Tax Analysis / cross-project isolation: PASS.
- Task18 Group Penetration: PASS.
- Alembic DB/Disk head at Phase-F close: `89_v3_group_penetration`.
- Task18 tests: `8 passed`.
- Alembic transaction guard tests: `4 passed`.
- Gate S18: PASS.
- Chain1: external leaf cost `255.00`, external revenue `350.00`, internal eliminated `300.00`.
- Chain2: external leaf cost `200.00`, external revenue `330.00`, internal eliminated `490.00`.
- Cycle fixture: cycle detected.
- TAX internal elimination rows: `0`.
- Legacy cashflow fallback allowed: `false`.

Phase F GO conditions are closed: TAX/ACCRUAL/CASH separation, project isolation, and canonical group internal-trade elimination/cycle protection. Task19 opens Phase G without rewriting Phase-F evidence.
