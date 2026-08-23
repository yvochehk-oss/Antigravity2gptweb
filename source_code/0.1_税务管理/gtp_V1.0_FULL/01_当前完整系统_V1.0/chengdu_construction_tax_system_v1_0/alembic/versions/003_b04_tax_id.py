"""Correct the canonical tax identifier for entity ``B04``.

The canonical master has one authoritative identifier for 四川矗佳商贸有限公司:
``91511526MA67UN7C2G``.  Some pre-canonical Tax databases contain the known
transposition ``91510115MA67UN7C2G``.  This is a data migration rather than a
schema migration, so it deliberately validates the row before changing it:

* exactly one ``B04`` row must exist in a populated entity master;
* its name must be the canonical company name; and
* its current tax id must be either the known old value or the canonical value.

An empty freshly-created schema is allowed to pass through; the current seed
inserts the canonical value.  A partially populated or conflicting master
fails closed and the Alembic environment restores its SQLite snapshot.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "003_b04_tax_id"
down_revision: str | None = "002_request_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

B04_CODE = "B04"
B04_NAME = "四川矗佳商贸有限公司"
KNOWN_OLD_TAX_ID = "91510115MA67UN7C2G"
CANONICAL_TAX_ID = "91511526MA67UN7C2G"
_ALLOW_DOWNGRADE = "ALLOW_B04_TAX_ID_DOWNGRADE"


def _bind():
    return op.get_bind()


def _table_state() -> tuple[bool, int, list[dict[str, object]]]:
    """Return whether the entity table exists, its row count and B04 rows."""

    bind = _bind()
    inspector = sa.inspect(bind)
    if "entities" not in inspector.get_table_names():
        return False, 0, []
    required = {item["name"] for item in inspector.get_columns("entities")}
    missing = {"id", "code", "name", "tax_id"} - required
    if missing:
        raise RuntimeError(
            f"incompatible entities table for B04 tax-id migration; missing columns: "
            f"{sorted(missing)}"
        )
    rows = bind.execute(
        sa.text(
            "SELECT id, code, name, tax_id FROM entities "
            "WHERE code = :code ORDER BY id"
        ),
        {"code": B04_CODE},
    ).mappings().all()
    count = int(
        bind.execute(sa.text("SELECT COUNT(*) FROM entities")).scalar_one()
    )
    return True, count, [dict(row) for row in rows]


def _validate_b04() -> tuple[bool, dict[str, object] | None]:
    """Validate the current master and return ``(skip, row)``.

    ``skip`` is true only for a fresh empty schema or for an already canonical
    row.  Any populated-but-incomplete or otherwise conflicting master raises.
    """

    exists, total_rows, rows = _table_state()
    if not exists:
        raise RuntimeError(
            "missing required entities table for B04 tax-id migration"
        )
    if total_rows == 0:
        # 001_initial can be upgraded before seed runs.  There is no data to
        # repair in this state, and seed.py is already canonical.
        return True, None
    if len(rows) != 1:
        raise RuntimeError(
            f"B04 tax-id migration requires exactly one {B04_CODE!r} row in a "
            f"populated entities master; found {len(rows)}"
        )
    row = rows[0]
    if row["name"] != B04_NAME:
        raise RuntimeError(
            f"B04 tax-id migration name conflict: {row['name']!r} != {B04_NAME!r}"
        )
    if row["tax_id"] not in {KNOWN_OLD_TAX_ID, CANONICAL_TAX_ID}:
        raise RuntimeError(
            "B04 tax-id migration refuses unexpected current value: "
            f"{row['tax_id']!r}; expected the known old value or canonical value"
        )

    # Protect the unique tax-id constraint explicitly so a legacy database
    # with a conflicting canonical identifier fails before UPDATE.
    conflict_count = int(
        _bind()
        .execute(
            sa.text(
                "SELECT COUNT(*) FROM entities "
                "WHERE tax_id = :tax_id AND id <> :id"
            ),
            {"tax_id": CANONICAL_TAX_ID, "id": row["id"]},
        )
        .scalar_one()
    )
    if conflict_count:
        raise RuntimeError(
            "B04 tax-id migration found another entity using the canonical tax id; "
            "refusing to create a duplicate"
        )
    return row["tax_id"] == CANONICAL_TAX_ID, row


def upgrade() -> None:
    already_canonical, row = _validate_b04()
    if already_canonical or row is None:
        return

    result = _bind().execute(
        sa.text(
            "UPDATE entities SET tax_id = :canonical "
            "WHERE id = :id AND code = :code AND name = :name "
            "AND tax_id = :known_old"
        ),
        {
            "canonical": CANONICAL_TAX_ID,
            "id": row["id"],
            "code": B04_CODE,
            "name": B04_NAME,
            "known_old": KNOWN_OLD_TAX_ID,
        },
    )
    if result.rowcount != 1:
        raise RuntimeError(
            "B04 tax-id migration update lost its precondition; expected exactly one row"
        )

    current = _bind().execute(
        sa.text("SELECT tax_id FROM entities WHERE id = :id"), {"id": row["id"]}
    ).scalar_one_or_none()
    if current != CANONICAL_TAX_ID:
        raise RuntimeError(
            f"B04 tax-id migration verification failed: {current!r} != {CANONICAL_TAX_ID!r}"
        )


def downgrade() -> None:
    """Reverse only with an explicit operator opt-in.

    Reverting the data correction restores a known historical typo.  It is
    therefore never implicit.  The default refusal also prevents an ordinary
    ``downgrade base`` from silently reintroducing cross-system master drift.
    """

    already_canonical, row = _validate_b04()
    if row is None:
        return
    if os.getenv(_ALLOW_DOWNGRADE) != "1":
        raise RuntimeError(
            "refusing destructive Tax downgrade of B04 tax_id; "
            f"set {_ALLOW_DOWNGRADE}=1 only after verifying a backup"
        )
    if not already_canonical:
        raise RuntimeError(
            "B04 tax-id downgrade expected the canonical value; "
            "refusing to overwrite an unexpected current value"
        )

    result = _bind().execute(
        sa.text(
            "UPDATE entities SET tax_id = :known_old "
            "WHERE id = :id AND code = :code AND name = :name "
            "AND tax_id = :canonical"
        ),
        {
            "known_old": KNOWN_OLD_TAX_ID,
            "id": row["id"],
            "code": B04_CODE,
            "name": B04_NAME,
            "canonical": CANONICAL_TAX_ID,
        },
    )
    if result.rowcount != 1:
        raise RuntimeError(
            "B04 tax-id downgrade update lost its precondition; expected exactly one row"
        )
