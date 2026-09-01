"""Task 06 Party migration conflict ORM model."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class PartyMigrationConflict(Base):
    __tablename__ = "party_migration_conflicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conflict_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    conflict_type: Mapped[str] = mapped_column(String(48), nullable=False)
    subject_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    related_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    legacy_source: Mapped[str] = mapped_column(String(120), nullable=False)
    detector: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default=text("'TASK06_BACKFILL'")
    )
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    context: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'OPEN'")
    )
    reviewed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('OPEN','RESOLVED','IGNORED','CLEARED')",
            name="ck_party_migration_conflicts_status",
        ),
        Index("ix_party_migration_conflicts_status", "status"),
        Index("ix_party_migration_conflicts_type", "conflict_type"),
        Index("ix_party_migration_conflicts_subject", "subject_code"),
    )
