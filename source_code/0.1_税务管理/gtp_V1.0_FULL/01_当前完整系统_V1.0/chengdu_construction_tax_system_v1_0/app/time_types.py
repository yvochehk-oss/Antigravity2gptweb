"""Timezone-aware SQLAlchemy types for legacy ISO timestamp fields.

The database is migrated to TIMESTAMPTZ while existing Tax call sites may keep
passing/receiving ISO strings during the gradual application-layer migration.
This preserves compatibility without storing timestamps as VARCHAR.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.types import TypeDecorator

from .db import Base


class UTCDateTimeString(TypeDecorator):
    """TIMESTAMPTZ storage with an ISO-string compatibility boundary."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value in (None, ""):
            return None
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not isinstance(value, datetime):
            raise TypeError(f"timestamp must be datetime/ISO string, got {type(value).__name__}")
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return ""
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def apply_timezone_types() -> tuple[str, ...]:
    """Upgrade mapped legacy ``*_at`` VARCHAR columns to TIMESTAMPTZ semantics."""
    changed: list[str] = []
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if not column.name.endswith("_at"):
                continue
            if isinstance(column.type, String):
                column.type = UTCDateTimeString()
                changed.append(f"{table.name}.{column.name}")
    return tuple(changed)


__all__ = ["UTCDateTimeString", "apply_timezone_types"]
