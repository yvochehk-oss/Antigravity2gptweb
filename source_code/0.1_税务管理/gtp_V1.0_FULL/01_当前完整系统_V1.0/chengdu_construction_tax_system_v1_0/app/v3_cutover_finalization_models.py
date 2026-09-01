"""Task23 immutable production-cutover seal ORM model."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, JSON, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

JsonType = JSON().with_variant(JSONB(), "postgresql")


class V3CutoverFinalization(Base):
    __tablename__ = "v3_cutover_finalizations"

    scope: Mapped[str] = mapped_column(String(64), ForeignKey("writer_cutover_states.scope", ondelete="RESTRICT"), primary_key=True)
    finalized_by: Mapped[str] = mapped_column(String(80), nullable=False)
    finalized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    state_snapshot: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    evidence_snapshot: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
