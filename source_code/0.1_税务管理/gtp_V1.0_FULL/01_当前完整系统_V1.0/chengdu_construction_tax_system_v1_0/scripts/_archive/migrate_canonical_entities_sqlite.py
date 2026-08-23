"""Migrate a V0.2 tax database to the canonical entity master.

The migration is intentionally a file-oriented SQLite migration.  It does
not touch ``data/demo.db`` unless the caller explicitly passes
``--allow-production``.  A backup is made before the transaction by default.

Recommended validation flow (the production database is never opened):

    cp data/demo.db /tmp/chengdu-tax-migration.db
    python scripts/migrate_canonical_entities.py \
        --database /tmp/chengdu-tax-migration.db
    python scripts/migrate_canonical_entities.py \
        --database /tmp/chengdu-tax-migration.db --no-backup

The second invocation is the idempotence check.  ``app.seed`` can then be run
against a separate fresh database to validate the canonical seed data.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# ``python scripts/migrate_canonical_entities.py`` puts only ``scripts/`` on
# sys.path; add the application root so the canonical master is sourced from
# the live seed module.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))



def _load_master():
    from app.seed import ENTITIES_MASTER, EXTERNAL_PARTIES

    return ENTITIES_MASTER, EXTERNAL_PARTIES


ENTITIES_MASTER, EXTERNAL_PARTIES = _load_master()


CANONICAL_CODES = frozenset(row[0] for row in ENTITIES_MASTER)
EXTERNAL_CODES = frozenset(row[0] for row in EXTERNAL_PARTIES)

# Legacy role placeholders were never legal entity codes.  These mappings are
# only used to preserve existing transaction references while the old rows
# are removed from ``entities``.
LEGACY_ENTITY_MAP = {"A": "A08", "B": "B01", "C": "C01", "D": "D01"}
LEGACY_EXTERNAL_ENTITY_CODES = frozenset({"甲", "乙", "丙", "丁"})
LEGACY_EXTERNAL_MAP = {
    "甲": "EXT-CY",
    "乙": "EXT-GX",
    "丙": "EXT-GEM",
    "丁": "EXT-GY",
    "业主": "EXT-TF",
    "业主/建设单位": "EXT-TF",
    "成都市天府新区金融城投公司": "EXT-TF",
    "成渝高速公路开发投资集团": "EXT-CY",
    "广元市利州区水务局城投平台": "EXT-GY",
    "国家电网四川省电力公司成都供电公司": "EXT-GX",
    "青海盐湖工业股份有限公司": "EXT-GEM",
}

ENTITY_COLUMNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("contracts", ("buyer_code", "seller_code")),
    ("invoices", ("entity_code",)),
    ("cashflows", ("entity_code",)),
    ("real_costs", ("entity_code",)),
    ("tax_ledgers", ("entity_code",)),
    ("entity_bank_accounts", ("entity_code",)),
    ("tax_payment_records", ("entity_code",)),
)
COUNTERPARTY_COLUMNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("contracts", ("buyer_code", "seller_code")),
    ("invoices", ("counterparty_code",)),
    ("cashflows", ("counterparty_code",)),
    ("fulfillment", ("counterparty_code",)),
    ("real_costs", ("counterparty_code",)),
)


class MigrationError(RuntimeError):
    """Raised when a migration cannot preserve references safely."""


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _add_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    if _table_exists(conn, table) and column not in _columns(conn, table):
        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')


def _remap_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    mapping: dict[str, str],
) -> None:
    if not _table_exists(conn, table) or column not in _columns(conn, table):
        return
    for old, new in mapping.items():
        conn.execute(
            f'UPDATE "{table}" SET "{column}"=? WHERE "{column}"=?',
            (new, old),
        )


def _remap_references(conn: sqlite3.Connection) -> None:
    # First map role placeholders in every field that can carry a party code.
    for table, columns in ENTITY_COLUMNS:
        for column in columns:
            _remap_column(conn, table, column, LEGACY_ENTITY_MAP)
    # Then map aliases/names only where a transaction field is a party side.
    for table, columns in COUNTERPARTY_COLUMNS:
        for column in columns:
            # A counterparty can be another internal legal entity as well as
            # an external party; normalize both namespaces before validation.
            _remap_column(conn, table, column, LEGACY_ENTITY_MAP)
            _remap_column(conn, table, column, LEGACY_EXTERNAL_MAP)


def _create_external_party_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS external_parties (
            id INTEGER PRIMARY KEY,
            code VARCHAR(16) NOT NULL UNIQUE,
            name VARCHAR(120) NOT NULL,
            short_name VARCHAR(60) NOT NULL DEFAULT '',
            kind VARCHAR(30) NOT NULL DEFAULT '',
            tax_id VARCHAR(40) NULL UNIQUE,
            active BOOLEAN NOT NULL DEFAULT 1
        )
        """
    )
    for code, name, short_name, kind in EXTERNAL_PARTIES:
        conn.execute(
            """
            INSERT INTO external_parties(code, name, short_name, kind, active)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(code) DO UPDATE SET
                name=excluded.name,
                short_name=excluded.short_name,
                kind=excluded.kind,
                active=1
            """,
            (code, name, short_name, kind),
        )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_external_parties_name ON external_parties(name)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_external_parties_active ON external_parties(active)")


def _create_canonical_entities(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "entities"):
        raise MigrationError("entities table is missing; initialize the database schema first")

    old_columns = _columns(conn, "entities")
    required_old = {"id", "code", "name", "tax_id"}
    if not required_old.issubset(old_columns):
        raise MigrationError(f"entities table is missing required columns: {required_old - old_columns}")

    rows = conn.execute("SELECT * FROM entities ORDER BY id").fetchall()
    names = [description[0] for description in conn.execute("SELECT * FROM entities LIMIT 0").description]
    records = [dict(zip(names, row, strict=True)) for row in rows]
    by_code = {record["code"]: record for record in records}
    unknown = sorted(
        set(by_code) - CANONICAL_CODES - set(LEGACY_ENTITY_MAP) - LEGACY_EXTERNAL_ENTITY_CODES
    )
    if unknown:
        raise MigrationError(
            "unrecognized Entity codes would be dropped; resolve them explicitly first: "
            + ", ".join(unknown)
        )

    # Once the table is canonical, avoid a needless SQLite table rebuild on
    # every invocation.  This keeps a second ``up`` run byte/DDL stable and
    # makes idempotence observable while still refreshing master metadata.
    canonical_columns = {
        "business_role", "legal_entity", "parent_entity_code", "active"
    }
    if set(by_code) == CANONICAL_CODES and canonical_columns.issubset(old_columns):
        for code, name, short_name, role, tax_id, legal_entity, parent_code, active in ENTITIES_MASTER:
            conn.execute(
                """
                UPDATE entities
                SET name=?, short_name=?, kind=?, internal=1, tax_id=?,
                    business_role=?, legal_entity=?, parent_entity_code=?, active=?
                WHERE code=?
                """,
                (
                    name, short_name, role, tax_id or None, role,
                    int(bool(legal_entity)), parent_code, int(bool(active)), code,
                ),
            )
        return

    # Preserve IDs where a canonical row already exists.  If an old role row
    # is the only source for a target, reuse its ID; otherwise allocate above
    # the current maximum.  Transaction tables use codes, but this keeps
    # audit links stable for installations that retain Entity IDs externally.
    used_ids: set[int] = set()
    next_id = max((int(record["id"]) for record in records), default=0) + 1
    canonical_rows: list[dict[str, object]] = []
    for code, name, short_name, role, tax_id, legal_entity, parent_code, active in ENTITIES_MASTER:
        record = by_code.get(code)
        if record is None:
            legacy_code = next((legacy for legacy, target in LEGACY_ENTITY_MAP.items() if target == code), None)
            record = by_code.get(legacy_code) if legacy_code else None
        if record is not None and int(record["id"]) not in used_ids:
            row_id = int(record["id"])
            used_ids.add(row_id)
        else:
            while next_id in used_ids:
                next_id += 1
            row_id = next_id
            used_ids.add(row_id)
            next_id += 1
        canonical_rows.append({
            "id": row_id,
            "code": code,
            "name": name,
            "kind": role,
            "internal": 1,
            "tax_id": tax_id or None,
            "short_name": short_name,
            "business_role": role,
            "legal_entity": int(bool(legal_entity)),
            "parent_entity_code": parent_code,
            "active": int(bool(active)),
        })

    tax_ids = [str(row["tax_id"]) for row in canonical_rows if row["tax_id"]]
    if len(tax_ids) != len(set(tax_ids)):
        raise MigrationError("canonical master contains duplicate tax IDs")

    conn.execute("DROP TABLE IF EXISTS entities__canonical")
    conn.execute(
        """
        CREATE TABLE entities__canonical (
            id INTEGER NOT NULL PRIMARY KEY,
            code VARCHAR(8) NOT NULL UNIQUE,
            name VARCHAR(100) NOT NULL,
            kind VARCHAR(30) NOT NULL DEFAULT '',
            internal BOOLEAN NOT NULL DEFAULT 1,
            tax_id VARCHAR(40) NULL UNIQUE,
            short_name VARCHAR(60) NOT NULL DEFAULT '',
            business_role VARCHAR(1) NOT NULL,
            legal_entity BOOLEAN NOT NULL DEFAULT 1,
            parent_entity_code VARCHAR(8) NULL,
            active BOOLEAN NOT NULL DEFAULT 1,
            CONSTRAINT ck_entities_business_role
                CHECK (business_role IN ('A', 'B', 'C', 'D')),
            CONSTRAINT ck_entities_canonical_code
                CHECK (code IN (
                    'A01','A02','A03','A04','A05','A06','A07','A08','A09','A10','A11',
                    'B01','B02','B03','B04','B05','B06','B07','B08','B09','B10',
                    'C01','C02','D01','D02','D03'
                )),
            CONSTRAINT uq_entities_tax_id UNIQUE (tax_id),
            FOREIGN KEY(parent_entity_code) REFERENCES entities__canonical(code)
        )
        """
    )
    # Parents first so the self-reference is valid when FK checking is
    # enabled by a caller after the transaction.
    canonical_rows.sort(key=lambda row: (row["parent_entity_code"] is not None, str(row["code"])))
    conn.executemany(
        """
        INSERT INTO entities__canonical
            (id, code, name, kind, internal, tax_id, short_name,
             business_role, legal_entity, parent_entity_code, active)
        VALUES (:id, :code, :name, :kind, :internal, :tax_id, :short_name,
                :business_role, :legal_entity, :parent_entity_code, :active)
        """,
        canonical_rows,
    )
    conn.execute("DROP TABLE entities")
    conn.execute("ALTER TABLE entities__canonical RENAME TO entities")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_entities_code ON entities(code)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_entities_business_role ON entities(business_role)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_entities_legal_entity ON entities(legal_entity)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_entities_parent_entity_code ON entities(parent_entity_code)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_entities_active ON entities(active)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_entities_tax_id ON entities(tax_id)")


def _create_supporting_schema(conn: sqlite3.Connection) -> None:
    _add_column(conn, "cashflows", "transaction_date", "VARCHAR(10) NULL")
    _add_column(conn, "cashflows", "bank_reference", "VARCHAR(120) NULL")
    _add_column(conn, "cashflows", "source_fingerprint", "VARCHAR(64) NULL")
    if _table_exists(conn, "cashflows"):
        conn.execute("CREATE INDEX IF NOT EXISTS ix_cashflows_transaction_date ON cashflows(transaction_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_cashflows_bank_reference ON cashflows(bank_reference)")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_cashflows_source_fingerprint ON cashflows(source_fingerprint)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entity_bank_accounts (
            id INTEGER PRIMARY KEY,
            entity_code VARCHAR(8) NOT NULL,
            bank_name VARCHAR(120) NOT NULL DEFAULT '',
            account_name VARCHAR(120) NOT NULL DEFAULT '',
            account_no VARCHAR(80) NOT NULL DEFAULT '',
            active BOOLEAN NOT NULL DEFAULT 1,
            created_at VARCHAR(30) NOT NULL DEFAULT '',
            CONSTRAINT uq_entity_bank_account UNIQUE(entity_code, account_no)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_entity_bank_accounts_entity_code ON entity_bank_accounts(entity_code)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_entity_bank_accounts_active ON entity_bank_accounts(active)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tax_payment_records (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NULL,
            entity_code VARCHAR(8) NOT NULL,
            tax_type VARCHAR(40) NOT NULL DEFAULT '',
            period VARCHAR(7) NOT NULL DEFAULT '',
            tax_period VARCHAR(7) NOT NULL DEFAULT '',
            receipt_no VARCHAR(100) NOT NULL DEFAULT '',
            payment_date VARCHAR(10) NULL,
            transaction_date VARCHAR(10) NULL,
            tax_amount NUMERIC(18, 2) NOT NULL DEFAULT 0,
            principal_amount NUMERIC(18, 2) NOT NULL DEFAULT 0,
            penalty_amount NUMERIC(18, 2) NOT NULL DEFAULT 0,
            bank_reference VARCHAR(120) NULL,
            source_fingerprint VARCHAR(64) NULL,
            note TEXT NOT NULL DEFAULT '',
            created_at VARCHAR(30) NOT NULL DEFAULT '',
            CONSTRAINT uq_tax_payment_receipt UNIQUE(entity_code, receipt_no),
            FOREIGN KEY(project_id) REFERENCES projects(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_tax_payment_records_entity_code ON tax_payment_records(entity_code)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_tax_payment_records_period ON tax_payment_records(period)")
    _add_column(conn, "tax_payment_records", "tax_period", "VARCHAR(7) NOT NULL DEFAULT ''")
    _add_column(conn, "tax_payment_records", "transaction_date", "VARCHAR(10) NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_tax_payment_records_tax_period ON tax_payment_records(tax_period)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_tax_payment_records_transaction_date ON tax_payment_records(transaction_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_tax_payment_records_source_fingerprint ON tax_payment_records(source_fingerprint)")


def _merge_duplicate_ledgers(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "tax_ledgers"):
        return
    groups = conn.execute(
        """
        SELECT period, entity_code, GROUP_CONCAT(id), COUNT(*)
        FROM tax_ledgers
        GROUP BY period, entity_code
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    amount_columns = (
        "output_vat", "input_vat", "vat_payable", "revenue",
        "real_cost", "estimated_profit", "estimated_cit",
    )
    for _period, _entity_code, id_csv, _count in groups:
        ids = [int(value) for value in str(id_csv).split(",")]
        keep_id, drop_ids = min(ids), [value for value in ids if value != min(ids)]
        for column in amount_columns:
            conn.execute(
                f"UPDATE tax_ledgers SET {column}=COALESCE({column},0)+COALESCE((SELECT SUM(COALESCE({column},0)) FROM tax_ledgers WHERE id IN ({','.join('?' for _ in drop_ids)})),0) WHERE id=?",
                (*drop_ids, keep_id),
            )
        notes = [
            row[0] for row in conn.execute(
                f"SELECT cit_note FROM tax_ledgers WHERE id IN ({','.join('?' for _ in ids)}) ORDER BY id",
                ids,
            ).fetchall() if row[0]
        ]
        conn.execute(
            "UPDATE tax_ledgers SET cit_note=?, generated=MAX(generated, 0) WHERE id=?",
            (" | ".join(dict.fromkeys(notes)), keep_id),
        )
        conn.execute(
            f"DELETE FROM tax_ledgers WHERE id IN ({','.join('?' for _ in drop_ids)})",
            drop_ids,
        )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_tax_ledger_period_entity ON tax_ledgers(period, entity_code)"
    )


def _validate_references(conn: sqlite3.Connection) -> None:
    invalid: list[str] = []
    for table, columns in ENTITY_COLUMNS:
        for column in columns:
            if not _table_exists(conn, table) or column not in _columns(conn, table):
                continue
            values = conn.execute(
                f'SELECT DISTINCT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL AND TRIM("{column}") <> ""'
            ).fetchall()
            for (value,) in values:
                if value not in CANONICAL_CODES and not (
                    table == "contracts" and value in EXTERNAL_CODES
                ):
                    invalid.append(f"{table}.{column}={value!r}")
    for table, columns in COUNTERPARTY_COLUMNS:
        for column in columns:
            if not _table_exists(conn, table) or column not in _columns(conn, table):
                continue
            values = conn.execute(
                f'SELECT DISTINCT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL AND TRIM("{column}") <> ""'
            ).fetchall()
            for (value,) in values:
                if value not in CANONICAL_CODES and value not in EXTERNAL_CODES:
                    invalid.append(f"{table}.{column}={value!r}")
    if invalid:
        raise MigrationError("unresolved entity/counterparty references: " + ", ".join(invalid[:30]))

    rows = conn.execute(
        "SELECT code, COUNT(*) FROM entities GROUP BY code HAVING COUNT(*) <> 1"
    ).fetchall()
    if rows:
        raise MigrationError(f"entity code uniqueness check failed: {rows}")
    rows = conn.execute(
        "SELECT tax_id, COUNT(*) FROM entities WHERE tax_id IS NOT NULL GROUP BY tax_id HAVING COUNT(*) > 1"
    ).fetchall()
    if rows:
        raise MigrationError(f"duplicate canonical tax IDs: {rows}")
    count = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    if count != len(CANONICAL_CODES):
        raise MigrationError(f"canonical entity count is {count}, expected {len(CANONICAL_CODES)}")


def _backup_path(database: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return database.with_name(f"{database.name}.pre-canonical-{stamp}.bak")


def migrate_database(
    database: str | Path,
    *,
    backup: bool = True,
    allow_production: bool = False,
) -> Path | None:
    """Run the canonical migration atomically and return the backup path."""
    path = Path(database).expanduser().resolve()
    if not path.exists():
        raise MigrationError(f"database does not exist: {path}")
    if path.name == "demo.db" and not allow_production:
        raise MigrationError(
            "refusing to modify data/demo.db directly; copy it to a temporary path "
            "and pass --database there (use --allow-production only after review)"
        )
    backup_path: Path | None = None
    if backup:
        backup_path = _backup_path(path)
        shutil.copy2(path, backup_path)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN IMMEDIATE")
        _remap_references(conn)
        _create_external_party_table(conn)
        _create_canonical_entities(conn)
        _create_supporting_schema(conn)
        _merge_duplicate_ledgers(conn)
        _validate_references(conn)
        # Validate the complete transactional state before COMMIT.  A
        # post-commit foreign-key failure cannot be rolled back by SQLite and
        # would leave a partially accepted migration on disk.  SQLite's
        # foreign_key_check works while enforcement is temporarily disabled,
        # which is required above for the entities table rebuild.
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise MigrationError(f"foreign key check failed before commit: {fk_errors[:5]}")
        conn.commit()
        conn.execute("PRAGMA foreign_keys=ON")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return backup_path


# Short alias for tests and callers that prefer a migration verb.
migrate = migrate_database


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path, help="temporary SQLite database path")
    parser.add_argument("--no-backup", action="store_true", help="skip the pre-migration backup")
    parser.add_argument(
        "--allow-production",
        action="store_true",
        help="explicitly permit a file named demo.db (review backup and downtime first)",
    )
    args = parser.parse_args()
    try:
        backup_path = migrate_database(
            args.database,
            backup=not args.no_backup,
            allow_production=args.allow_production,
        )
    except Exception as exc:
        print(f"MIGRATION_FAILED: {exc}")
        return 1
    if backup_path:
        print(f"backup: {backup_path}")
    print(f"MIGRATION_OK: {Path(args.database).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
