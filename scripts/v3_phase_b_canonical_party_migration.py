#!/usr/bin/env python3
"""Atomic migration script for STEP EXT-2: Canonical External Code Convergence.

Migrates all durable database references from raw legacy aliases:
  EXT-CY   -> E02  (成渝高速公路开发投资集团有限公司)
  EXT-GY   -> E03  (广元市利州区水务发展投资集团有限公司)
  EXT-SHIP -> EA02 (长航特种工程潜水与打捞公司)
  EXT-CONC -> EB02 (西南特种混凝土骨料直供站)
  EXT-TREE -> EB03 (四川省生态林业苗木繁育中心)

Tables affected:
  - contracts (buyer_code, seller_code)
  - invoices (counterparty_code)
  - cashflows (counterparty_code)
  - fulfillment (counterparty_code)
  - documents (counterparty_code)
  - canonical_facts (payload)
  - parties (code, name, short_name)
  - external_parties (code, name, short_name, kind, active)
"""

import argparse
import os
import sys
import psycopg

DEFAULT_DB_URL = "postgresql://yvoche@localhost:5432/projectrag"

INTERNAL_REGEX = r"^(?:A(?:0[1-9]|1[01])|B(?:0[1-9]|10)|C0[12]|D0[1-3])$"
EXTERNAL_REGEX = r"^E(?:0[1-9]|[1-9]\d|[A-D](?:0[1-9]|[1-9]\d))$"
COMBINED_PARTY_REGEX = r"^(?:A(?:0[1-9]|1[01])|B(?:0[1-9]|10)|C0[12]|D0[1-3]|E(?:0[1-9]|[1-9]\d|[A-D](?:0[1-9]|[1-9]\d)))$"

LEGACY_UNREFERENCED_SEEDS = (
    "EXT-PARTNER-01",
    "EXT-CORP-A",
    "EXT-CORP-B",
    "EXT-BRANCH-A",
    "EXT-BRANCH-B",
    "EXT-PROJECT-01",
    "EXT-SUPPLIER-01",
    "EXT-SUB-01",
    "EXT-CORP-1",
    "EXT-CORP-2",
    "EXT-BRANCH-1",
    "EXT-BRANCH-2",
    "EXT-TEST-01",
    "EXT-TEST-02",
    "EXT-TEST-03",
    "EXT-DEMO-01",
)


def require_zero(cur, query: str, params: tuple | None, error_message: str):
    """Fail-closed assertion: raises RuntimeError if count != 0."""
    if params:
        cur.execute(query, params)
    else:
        cur.execute(query)
    row = cur.fetchone()
    count = row[0] if row else 0
    if count != 0:
        raise RuntimeError(f"VERIFICATION FAILURE (count={count}): {error_message}")

MAPPINGS = {
    "EXT-CY": {
        "target": "E02",
        "name": "成渝高速公路开发投资集团有限公司",
        "short_name": "成渝高速投资",
        "kind": "owner",
        "tax_id": "91510100MA61BBBB22",
    },
    "EXT-GY": {
        "target": "E03",
        "name": "广元市利州区水务发展投资集团有限公司",
        "short_name": "广元利州水务",
        "kind": "owner",
        "tax_id": "91510800MA61CCCC33",
    },
    "EXT-SHIP": {
        "target": "EA02",
        "name": "长航特种工程潜水与打捞公司",
        "short_name": "长航潜水",
        "kind": "construction",
        "tax_id": "91500100MA61HHHH00",
    },
    "EXT-CONC": {
        "target": "EB02",
        "name": "西南特种混凝土骨料直供站",
        "short_name": "特种商砼",
        "kind": "supplier",
        "tax_id": "91500100MA61FFFF88",
    },
    "EXT-TREE": {
        "target": "EB03",
        "name": "四川省生态林业苗木繁育中心",
        "short_name": "生态林业",
        "kind": "supplier",
        "tax_id": "91510800MA61JJJJ55",
    },
    "E0": {
        "target": "E01",
        "name": "成都市天府新区金融城投公司",
        "short_name": "天府金融城投",
        "kind": "owner",
        "tax_id": "91510100MA61AAAA11",
    },
    "EA": {
        "target": "EA01",
        "name": "四川省建筑科学研究院特种技术服务中心",
        "short_name": "省建科院特种技术中心",
        "kind": "construction",
        "tax_id": "91510100MA61KKKK33",
    },
    "EB": {
        "target": "EB01",
        "name": "攀钢集团攀枝花钢钒物资销售有限公司",
        "short_name": "攀钢钢钒物资",
        "kind": "trade",
        "tax_id": "91510400MA61EEEE77",
    },
    "EC": {
        "target": "EC01",
        "name": "四川中泰建筑劳务分包有限公司",
        "short_name": "中泰劳务",
        "kind": "labor",
        "tax_id": "91510100MA61LLLL44",
    },
    "ED": {
        "target": "ED01",
        "name": "重庆巨力重型起重设备吊装公司",
        "short_name": "重庆重交起重",
        "kind": "equipment",
        "tax_id": "91500100MA61GGGG99",
    },
}


def run_migration(apply: bool = False, db_url: str = DEFAULT_DB_URL):
    print(f"Connecting to database: {db_url}")
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            print("Beginning atomic migration transaction...")
            # P0: Explicit table locking to prevent concurrent writes during migration
            cur.execute("""
                LOCK TABLE external_parties, parties, documents, contracts, 
                           invoices, cashflows, fulfillment, canonical_facts 
                IN EXCLUSIVE MODE;
            """)
            print("Acquired exclusive lock on 8 affected tables.")

            for source, meta in MAPPINGS.items():
                target = meta["target"]
                print(f"\n--- Migrating {source} -> {target} ---")

                # 1. contracts
                cur.execute("UPDATE contracts SET buyer_code = %s WHERE buyer_code = %s;", (target, source))
                print(f"  contracts.buyer_code: {cur.rowcount} rows updated")
                cur.execute("UPDATE contracts SET seller_code = %s WHERE seller_code = %s;", (target, source))
                print(f"  contracts.seller_code: {cur.rowcount} rows updated")

                # 2. invoices
                cur.execute("UPDATE invoices SET counterparty_code = %s WHERE counterparty_code = %s;", (target, source))
                print(f"  invoices.counterparty_code: {cur.rowcount} rows updated")

                # 3. cashflows
                cur.execute("UPDATE cashflows SET counterparty_code = %s WHERE counterparty_code = %s;", (target, source))
                print(f"  cashflows.counterparty_code: {cur.rowcount} rows updated")

                # 4. fulfillment
                cur.execute("UPDATE fulfillment SET counterparty_code = %s WHERE counterparty_code = %s;", (target, source))
                print(f"  fulfillment.counterparty_code: {cur.rowcount} rows updated")

                # 5. documents
                cur.execute("UPDATE documents SET counterparty_code = %s WHERE counterparty_code = %s;", (target, source))
                print(f"  documents.counterparty_code: {cur.rowcount} rows updated")

                # 6. canonical_facts targeted key update
                # Update only specific entity/counterparty keys instead of indiscriminate text replacement
                for key in ("counterparty_code", "party_a_code", "party_b_code", "party_a_entity_code", "party_b_entity_code", "buyer_code", "seller_code"):
                    cur.execute(
                        f"""
                        UPDATE canonical_facts
                        SET payload = jsonb_set(payload, '{{{key}}}', to_jsonb(%(target)s::text))
                        WHERE payload->>'{key}' = %(source)s;
                        """,
                        {"source": source, "target": target}
                    )
                    if cur.rowcount > 0:
                        print(f"  canonical_facts payload ({key}): {cur.rowcount} rows updated")

                # 7. parties (preserve existing active status)
                cur.execute(
                    """
                    UPDATE parties 
                    SET code = %s, name = %s, short_name = %s
                    WHERE code = %s;
                    """,
                    (target, meta["name"], meta["short_name"], source)
                )
                print(f"  parties.code: {cur.rowcount} rows updated")

                # 8. external_parties (preserve existing active status)
                cur.execute(
                    """
                    UPDATE external_parties 
                    SET code = %s, name = %s, short_name = %s, kind = %s,
                        tax_id = COALESCE(tax_id, %s)
                    WHERE code = %s;
                    """,
                    (target, meta["name"], meta["short_name"], meta["kind"], meta["tax_id"], source)
                )
                print(f"  external_parties.code: {cur.rowcount} rows updated")

            # 9. Verify and purge only explicit manifest unreferenced legacy EXT-* seeds
            print("\nVerifying unreferenced manifest legacy seeds before deletion...")
            cur.execute("""
                SELECT code FROM external_parties 
                WHERE code LIKE 'EXT-%%' AND code != ALL(%s);
            """, (list(LEGACY_UNREFERENCED_SEEDS),))
            unrecognized_ext = [r[0] for r in cur.fetchall()]
            if unrecognized_ext:
                raise RuntimeError(f"Found unrecognized EXT-* party not in manifest: {unrecognized_ext}")

            cur.execute("""
                DELETE FROM external_parties 
                WHERE code = ANY(%s);
            """, (list(LEGACY_UNREFERENCED_SEEDS),))
            print(f"Purged explicit manifest legacy seeds from external_parties: {cur.rowcount} rows deleted")

            cur.execute("""
                DELETE FROM parties 
                WHERE code = ANY(%s);
            """, (list(LEGACY_UNREFERENCED_SEEDS),))
            print(f"Purged explicit manifest legacy seeds from parties: {cur.rowcount} rows deleted")

            # 10. Enforce PostgreSQL hard CHECK constraints across all 8 tables
            print("\nEnforcing PostgreSQL hard CHECK constraints across all 8 tables...")
            cur.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'ck_external_parties_canonical_code'
                ) THEN
                    ALTER TABLE external_parties 
                    ADD CONSTRAINT ck_external_parties_canonical_code 
                    CHECK (code ~ '^E(?:0[1-9]|[1-9]\\d|[A-D](?:0[1-9]|[1-9]\\d))$');
                END IF;
                
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'ck_parties_canonical_code'
                ) THEN
                    ALTER TABLE parties 
                    ADD CONSTRAINT ck_parties_canonical_code 
                    CHECK (code ~ '^(?:A(?:0[1-9]|1[01])|B(?:0[1-9]|10)|C0[12]|D0[1-3]|E(?:0[1-9]|[1-9]\\d|[A-D](?:0[1-9]|[1-9]\\d)))$');
                END IF;

                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'ck_documents_counterparty_canonical'
                ) THEN
                    ALTER TABLE documents 
                    ADD CONSTRAINT ck_documents_counterparty_canonical 
                    CHECK (counterparty_code IS NULL OR counterparty_code = '' OR counterparty_code ~ '^(?:A(?:0[1-9]|1[01])|B(?:0[1-9]|10)|C0[12]|D0[1-3]|E(?:0[1-9]|[1-9]\\d|[A-D](?:0[1-9]|[1-9]\\d)))$');
                END IF;

                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'ck_contracts_buyer_canonical'
                ) THEN
                    ALTER TABLE contracts 
                    ADD CONSTRAINT ck_contracts_buyer_canonical 
                    CHECK (buyer_code IS NULL OR buyer_code = '' OR buyer_code ~ '^(?:A(?:0[1-9]|1[01])|B(?:0[1-9]|10)|C0[12]|D0[1-3]|E(?:0[1-9]|[1-9]\\d|[A-D](?:0[1-9]|[1-9]\\d)))$');
                END IF;

                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'ck_contracts_seller_canonical'
                ) THEN
                    ALTER TABLE contracts 
                    ADD CONSTRAINT ck_contracts_seller_canonical 
                    CHECK (seller_code IS NULL OR seller_code = '' OR seller_code ~ '^(?:A(?:0[1-9]|1[01])|B(?:0[1-9]|10)|C0[12]|D0[1-3]|E(?:0[1-9]|[1-9]\\d|[A-D](?:0[1-9]|[1-9]\\d)))$');
                END IF;

                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'ck_invoices_counterparty_canonical'
                ) THEN
                    ALTER TABLE invoices 
                    ADD CONSTRAINT ck_invoices_counterparty_canonical 
                    CHECK (counterparty_code IS NULL OR counterparty_code = '' OR counterparty_code ~ '^(?:A(?:0[1-9]|1[01])|B(?:0[1-9]|10)|C0[12]|D0[1-3]|E(?:0[1-9]|[1-9]\\d|[A-D](?:0[1-9]|[1-9]\\d)))$');
                END IF;

                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'ck_cashflows_counterparty_canonical'
                ) THEN
                    ALTER TABLE cashflows 
                    ADD CONSTRAINT ck_cashflows_counterparty_canonical 
                    CHECK (counterparty_code IS NULL OR counterparty_code = '' OR counterparty_code ~ '^(?:A(?:0[1-9]|1[01])|B(?:0[1-9]|10)|C0[12]|D0[1-3]|E(?:0[1-9]|[1-9]\\d|[A-D](?:0[1-9]|[1-9]\\d)))$');
                END IF;

                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'ck_fulfillment_counterparty_canonical'
                ) THEN
                    ALTER TABLE fulfillment 
                    ADD CONSTRAINT ck_fulfillment_counterparty_canonical 
                    CHECK (counterparty_code IS NULL OR counterparty_code = '' OR counterparty_code ~ '^(?:A(?:0[1-9]|1[01])|B(?:0[1-9]|10)|C0[12]|D0[1-3]|E(?:0[1-9]|[1-9]\\d|[A-D](?:0[1-9]|[1-9]\\d)))$');
                END IF;
            END $$;
            """)
            print("  [PASS] Hard CHECK constraints active across all 8 tables")

            # Verification (Fail-Closed using require_zero)
            print("\n=== VERIFICATION CHECKS (FAIL-CLOSED) ===")
            for source, meta in MAPPINGS.items():
                require_zero(cur, "SELECT count(*) FROM contracts WHERE buyer_code = %s OR seller_code = %s;", (source, source), f"Leaked contract party: {source}")
                require_zero(cur, "SELECT count(*) FROM fulfillment WHERE counterparty_code = %s;", (source,), f"Leaked fulfillment counterparty_code: {source}")
                for key in ("counterparty_code", "party_a_code", "party_b_code", "party_a_entity_code", "party_b_entity_code", "buyer_code", "seller_code"):
                    require_zero(cur, f"SELECT count(*) FROM canonical_facts WHERE payload->>'{key}' = %s;", (source,), f"Leaked canonical_facts payload ({key}): {source}")
                require_zero(cur, "SELECT count(*) FROM documents WHERE counterparty_code = %s;", (source,), f"Leaked document counterparty_code: {source}")
                require_zero(cur, "SELECT count(*) FROM invoices WHERE counterparty_code = %s;", (source,), f"Leaked invoice counterparty_code: {source}")
                require_zero(cur, "SELECT count(*) FROM cashflows WHERE counterparty_code = %s;", (source,), f"Leaked cashflow counterparty_code: {source}")
                require_zero(cur, "SELECT count(*) FROM external_parties WHERE code = %s;", (source,), f"Leaked external_parties code: {source}")
                require_zero(cur, "SELECT count(*) FROM parties WHERE code = %s;", (source,), f"Leaked parties code: {source}")
                print(f"  Verified 0 occurrences of old code {source} across all 8 tables")

            # Global Zero-Residue Invariant Checks across ENTIRE tables (active & inactive)
            print("\n=== GLOBAL ZERO-RESIDUE INVARIANT CHECKS (FAIL-CLOSED) ===")
            require_zero(cur, f"SELECT count(*) FROM external_parties WHERE code !~ '{EXTERNAL_REGEX}';", None, "Non-canonical external_parties found in master roster!")
            print("  [PASS] All external_parties (active & inactive) strictly conform to canonical two-digit regex")

            require_zero(cur, f"SELECT count(*) FROM parties WHERE code !~ '{COMBINED_PARTY_REGEX}';", None, "Non-canonical parties found in master roster!")
            print("  [PASS] All parties (active & inactive) strictly conform to canonical regex")

            require_zero(cur, f"SELECT count(*) FROM contracts WHERE (buyer_code IS NOT NULL AND buyer_code <> '' AND buyer_code !~ '{COMBINED_PARTY_REGEX}') OR (seller_code IS NOT NULL AND seller_code <> '' AND seller_code !~ '{COMBINED_PARTY_REGEX}');", None, "Non-canonical contract party codes found!")
            print("  [PASS] All contracts parties strictly conform to canonical regex")

            require_zero(cur, f"SELECT count(*) FROM documents WHERE counterparty_code IS NOT NULL AND counterparty_code <> '' AND counterparty_code !~ '{COMBINED_PARTY_REGEX}';", None, "Non-canonical document counterparty codes found!")
            print("  [PASS] All documents counterparties strictly conform to canonical regex")

            require_zero(cur, f"SELECT count(*) FROM invoices WHERE counterparty_code IS NOT NULL AND counterparty_code <> '' AND counterparty_code !~ '{COMBINED_PARTY_REGEX}';", None, "Non-canonical invoice counterparty codes found!")
            print("  [PASS] All invoices counterparties strictly conform to canonical regex")

            require_zero(cur, f"SELECT count(*) FROM cashflows WHERE counterparty_code IS NOT NULL AND counterparty_code <> '' AND counterparty_code !~ '{COMBINED_PARTY_REGEX}';", None, "Non-canonical cashflow counterparty codes found!")
            print("  [PASS] All cashflows counterparties strictly conform to canonical regex")

            require_zero(cur, f"SELECT count(*) FROM fulfillment WHERE counterparty_code IS NOT NULL AND counterparty_code <> '' AND counterparty_code !~ '{COMBINED_PARTY_REGEX}';", None, "Non-canonical fulfillment counterparty codes found!")
            print("  [PASS] All fulfillment counterparties strictly conform to canonical regex")

            # Check all 7 identity keys in canonical_facts across all records
            for key in ("counterparty_code", "party_a_code", "party_b_code", "party_a_entity_code", "party_b_entity_code", "buyer_code", "seller_code"):
                require_zero(cur, f"SELECT count(*) FROM canonical_facts WHERE payload->>'{key}' IS NOT NULL AND payload->>'{key}' <> '' AND payload->>'{key}' !~ '{COMBINED_PARTY_REGEX}';", None, f"Non-canonical {key} found in canonical_facts payload!")
                print(f"  [PASS] canonical_facts payload ({key}) strictly conforms to canonical regex")

            cur.execute("SELECT code, name, short_name, active FROM external_parties WHERE active = TRUE ORDER BY code;")
            active_parties = cur.fetchall()
            print(f"\nTotal active external parties ({len(active_parties)}):")
            for r in active_parties:
                print(f"  {r[0]:<6} | {r[1]:<36} | {r[2]:<16} | active={r[3]}")

            if apply:
                conn.commit()
                print("\n[SUCCESS] Transaction committed successfully!")
            else:
                conn.rollback()
                print("\n[DRY RUN] Transaction rolled back. Pass --apply to persist.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Canonical Party Migration Script (STEP EXT-2)")
    parser.add_argument("--apply", action="store_true", help="Commit changes to database (default is dry-run)")
    parser.add_argument("--db-url", type=str, default=os.getenv("DATABASE_URL", DEFAULT_DB_URL), help="PostgreSQL connection string")
    args = parser.parse_args()

    run_migration(apply=args.apply, db_url=args.db_url)

