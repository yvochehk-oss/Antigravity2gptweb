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

import sys
import psycopg

DB_URL = "postgresql://yvoche@localhost:5432/projectrag"

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
    "ED": {
        "target": "ED01",
        "name": "重庆巨力重型起重设备吊装公司",
        "short_name": "重庆重交起重",
        "kind": "equipment",
        "tax_id": "91500100MA61GGGG99",
    },
}


def run_migration(apply: bool = False):
    print(f"Connecting to database: {DB_URL}")
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            print("Beginning atomic migration transaction...")

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

                # 6. canonical_facts payload
                cur.execute(
                    """
                    UPDATE canonical_facts
                    SET payload = replace(payload::text, %s, %s)::jsonb
                    WHERE payload::text LIKE %s;
                    """,
                    (f'"{source}"', f'"{target}"', f'%"{source}"%')
                )
                print(f"  canonical_facts payload: {cur.rowcount} rows updated")

                # 7. parties
                cur.execute(
                    """
                    UPDATE parties 
                    SET code = %s, name = %s, short_name = %s, active = TRUE
                    WHERE code = %s;
                    """,
                    (target, meta["name"], meta["short_name"], source)
                )
                print(f"  parties.code: {cur.rowcount} rows updated")

                # 8. external_parties
                cur.execute(
                    """
                    UPDATE external_parties 
                    SET code = %s, name = %s, short_name = %s, kind = %s, active = TRUE,
                        tax_id = COALESCE(tax_id, %s)
                    WHERE code = %s;
                    """,
                    (target, meta["name"], meta["short_name"], meta["kind"], meta["tax_id"], source)
                )
                print(f"  external_parties.code: {cur.rowcount} rows updated")

            # Verification
            print("\n=== VERIFICATION CHECKS ===")
            for source, meta in MAPPINGS.items():
                target = meta["target"]
                cur.execute("SELECT count(*) FROM documents WHERE counterparty_code = %s;", (source,))
                assert cur.fetchone()[0] == 0, f"Leaked document counterparty_code: {source}"
                cur.execute("SELECT count(*) FROM invoices WHERE counterparty_code = %s;", (source,))
                assert cur.fetchone()[0] == 0, f"Leaked invoice counterparty_code: {source}"
                cur.execute("SELECT count(*) FROM cashflows WHERE counterparty_code = %s;", (source,))
                assert cur.fetchone()[0] == 0, f"Leaked cashflow counterparty_code: {source}"
                cur.execute("SELECT count(*) FROM external_parties WHERE code = %s;", (source,))
                assert cur.fetchone()[0] == 0, f"Leaked external_parties code: {source}"
                cur.execute("SELECT count(*) FROM parties WHERE code = %s;", (source,))
                assert cur.fetchone()[0] == 0, f"Leaked parties code: {source}"
                print(f"  Verified 0 occurrences of old code {source}")

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
    apply_flag = "--apply" in sys.argv
    run_migration(apply=apply_flag)
