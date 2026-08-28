"""Keep the shared external-party identifiers wide enough for canonical seeds.

The original shared table used ``VARCHAR(16)`` for both the external-party
code and kind in some deployed databases.  The Tax model and current seed
contract use descriptive values (for example ``EXT-CQ-HEAVY-CRANE``), so this
revision widens both columns to the established 30-character contract.  The
operation is data-preserving and applies equally to databases whose columns
were already widened by an out-of-band schema bootstrap.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "65_ext_party_code_kind_len"
down_revision = "64_rag_service_endpoint"
branch_labels = None
depends_on = None


_TABLE = "external_parties"
_TARGET_TYPE = sa.String(30)
_LEGACY_TYPE = sa.String(16)


def upgrade() -> None:
    """Widen shared external-party code and kind without changing values."""
    op.alter_column(
        _TABLE,
        "code",
        type_=_TARGET_TYPE,
        existing_type=_LEGACY_TYPE,
    )
    op.alter_column(
        _TABLE,
        "kind",
        type_=_TARGET_TYPE,
        existing_type=_LEGACY_TYPE,
    )


def downgrade() -> None:
    """Restore the legacy width only when no value would be truncated.

    PostgreSQL would reject a narrowing ALTER when oversized values exist, but
    the explicit check makes the reason actionable and guarantees that a
    downgrade never relies on implicit truncation semantics.
    """
    bind = op.get_bind()
    oversized = bind.execute(
        sa.text(
            """
            SELECT code, kind
            FROM external_parties
            WHERE length(code) > 16 OR length(kind) > 16
            ORDER BY id
            LIMIT 1
            """
        )
    ).first()
    if oversized is not None:
        raise RuntimeError(
            "cannot downgrade external_parties code/kind to VARCHAR(16): "
            f"value(s) exceed 16 characters (code={oversized.code!r}, "
            f"kind={oversized.kind!r}); remove or migrate them explicitly first"
        )

    op.alter_column(
        _TABLE,
        "kind",
        type_=_LEGACY_TYPE,
        existing_type=_TARGET_TYPE,
    )
    op.alter_column(
        _TABLE,
        "code",
        type_=_LEGACY_TYPE,
        existing_type=_TARGET_TYPE,
    )
