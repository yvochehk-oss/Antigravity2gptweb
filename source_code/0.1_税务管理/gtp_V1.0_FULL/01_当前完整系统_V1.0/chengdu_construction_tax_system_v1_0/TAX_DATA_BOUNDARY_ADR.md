# ADR — Tax Data Boundary: RAG Facts, Tax Advisory, Formal Statutory

Status: **ACCEPTED / REQUIRED**

## 1. Authoritative fact source

RAG PostgreSQL is the only business and tax fact source consumed by the Tax system.

Tax must not establish business truth by directly reading PDF, DOCX, contracts, invoice images, scans, local folders, OCR output files, or any other source document. Source-file ingestion, parsing, OCR/VL and canonicalization belong exclusively to the RAG system.

Tax may consume only structured facts already persisted through the RAG canonical pipeline.

## 2. Occurred tax-payment facts

Every current and VALID canonical tax-payment/prepayment fact in RAG PostgreSQL is an occurred fact for Tax purposes, including but not limited to:

- VAT payments / prepayments;
- Corporate / enterprise income tax (CIT) payments;
- other supported tax types represented by canonical tax facts.

A Formal VAT/CIT gate may block formation of a formal statutory ledger, but it must never erase, hide, downgrade, or reinterpret an already confirmed payment fact as unknown.

`FACT` contract:

- `data_class = FACT`
- `source = RAG_POSTGRESQL`
- `actual_occurred = true`
- advisory calculations must not overwrite the fact.

## 3. Tax deterministic advisory outputs

Tax engines calculate decision-support values from RAG facts, including revenue, cost, profit, VAT, CIT, tax gaps and related metrics.

These values are advisory calculations, not assertions that an event has already occurred and not automatic filing bases.

`ADVISORY` contract:

- `data_class = ADVISORY`
- `source = TAX_ENGINE`
- `actual_occurred = false`
- `is_filing_basis = false`

Actual paid tax and advisory tax liability must remain separate fields. Differences may be calculated, but neither side may overwrite the other.

## 4. Formal statutory boundary

Fail-Closed gates protect only the promotion of facts/calculations into a formal statutory resource. They do not protect ordinary FACT reads and do not suppress Tax advisory calculations.

Absence of a Formal statutory resource must be rendered as unavailable / not formed, never as a formal zero.

## 5. Scope identity

A comparison is valid only when FACT and ADVISORY use the same scope and cutoff.

Current project advisory is project-scoped. It must not compare a legal-entity-filtered actual payment total against a project-wide advisory result. Legal-entity advisory comparison may be exposed only after an equivalent deterministic legal-entity calculation engine exists.

When `period=YYYY-MM` is used by the project advisory API, both actual payments and advisory calculations are project-to-date through the end of that month.

## 6. Prohibited fallbacks

Tax must not silently substitute dynamic monetary facts from local master/legacy tables when the corresponding RAG canonical fact is missing.

In particular:

- project master contract amounts are not a fallback for canonical contract-price facts;
- legacy tax ledgers are not a fallback for formal statutory resources;
- source documents are never reparsed by Tax.

Missing RAG facts must produce an explicit EMPTY/DEGRADED/data-gap condition rather than an invented or legacy-backed fact.

## 7. Implementation checkpoints

- `app/domain/tax_data_policy.py` — shared FACT/ADVISORY source contract.
- `app/services/canonical_project_summary.py` — RAG-only financial fact resolution.
- `app/services/tax_advisory.py` — occurred tax facts + deterministic advisory + differences.
- `app/routers/tax_advisory.py` — read-only project advisory API.
- `app/services/formal_vat_readiness.py` — confirmed VAT observations remain RAG FACT even when Formal is blocked.
- `frontend_stitch/.../TaxLedgerView.tsx` — Formal unavailable is `——`; RAG confirmed tax facts remain visible.
- `tests/test_tax_data_policy_contract.py` — regression contract.

## 8. Non-negotiable invariants

1. **RAG manages facts; Tax manages calculations.**
2. **A current/VALID RAG tax payment is an occurred fact.**
3. **Tax calculated revenue/cost/profit/VAT/CIT are advisory unless explicitly promoted through a separate formal statutory contract.**
4. **FACT and ADVISORY never overwrite each other.**
5. **Formal gates never erase confirmed FACT data.**
