"""Task21 writer-cutover ORM models.

These rows are control/evidence infrastructure; they never become business truth.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

JsonType = JSON().with_variant(JSONB(), "postgresql")


class WriterCutoverState(Base):
    __tablename__ = "writer_cutover_states"

    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    writer_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    legacy_write_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    new_fact_write_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rag_source: Mapped[str] = mapped_column(String(24), nullable=False)
    new_fact_read_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    legacy_frozen: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    updated_by: Mapped[str] = mapped_column(String(80), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        CheckConstraint("writer_mode IN ('SHADOW','DUAL_WRITE','V3_PRIMARY')", name="ck_writer_cutover_states_mode"),
        CheckConstraint("rag_source IN ('LEGACY','CANONICAL_FACTS')", name="ck_writer_cutover_states_rag_source"),
        CheckConstraint("new_fact_read_mode IN ('OFF','SHADOW','PRIMARY')", name="ck_writer_cutover_states_read_mode"),
        CheckConstraint(
            "(writer_mode='SHADOW' AND legacy_write_enabled IS TRUE AND new_fact_write_enabled IS FALSE AND rag_source='LEGACY' AND new_fact_read_mode='SHADOW' AND legacy_frozen IS FALSE) OR "
            "(writer_mode='DUAL_WRITE' AND legacy_write_enabled IS TRUE AND new_fact_write_enabled IS TRUE AND rag_source='LEGACY' AND new_fact_read_mode='SHADOW' AND legacy_frozen IS FALSE) OR "
            "(writer_mode='V3_PRIMARY' AND legacy_write_enabled IS FALSE AND new_fact_write_enabled IS TRUE AND legacy_frozen IS TRUE)",
            name="ck_writer_cutover_states_mode_flags",
        ),
    )


class ShadowWriteDiff(Base):
    __tablename__ = "shadow_write_diffs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    operation_key: Mapped[str] = mapped_column(String(240), nullable=False, unique=True)
    object_type: Mapped[str] = mapped_column(String(40), nullable=False)
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    legacy_object_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    canonical_fact_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("facts.id", ondelete="RESTRICT"), nullable=True)
    legacy_payload: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    canonical_payload: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)
    normalized_diff: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False, server_default=text("'{}'::jsonb"))
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_status: Mapped[str] = mapped_column(String(16), nullable=False)
    simulation_fixture: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    resolved_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        CheckConstraint("operation IN ('INSERT','UPDATE','DELETE')", name="ck_shadow_write_diffs_operation"),
        CheckConstraint("result IN ('MATCH','MISMATCH','ERROR')", name="ck_shadow_write_diffs_result"),
        CheckConstraint("review_status IN ('OPEN','RESOLVED','IGNORED')", name="ck_shadow_write_diffs_review_status"),
        CheckConstraint(
            "(result='MATCH' AND review_status='RESOLVED' AND error_code IS NULL AND error_message IS NULL) OR "
            "(result='MISMATCH' AND error_code IS NULL) OR (result='ERROR' AND error_code IS NOT NULL)",
            name="ck_shadow_write_diffs_result_semantics",
        ),
        CheckConstraint(
            "(review_status='OPEN' AND resolved_by IS NULL AND resolved_at IS NULL) OR "
            "(review_status IN ('RESOLVED','IGNORED') AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL)",
            name="ck_shadow_write_diffs_resolution_semantics",
        ),
        Index("ix_shadow_write_diffs_result", "result"),
        Index("ix_shadow_write_diffs_review_status", "review_status"),
        Index("ix_shadow_write_diffs_canonical_fact", "canonical_fact_id"),
    )


class ReviewDiffQueue(Base):
    __tablename__ = "review_diff_queue"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    shadow_diff_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("shadow_write_diffs.id", ondelete="CASCADE"), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'OPEN'"))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        CheckConstraint("status IN ('OPEN','RESOLVED','IGNORED')", name="ck_review_diff_queue_status"),
        CheckConstraint(
            "(status='OPEN' AND reviewed_by IS NULL AND reviewed_at IS NULL) OR "
            "(status IN ('RESOLVED','IGNORED') AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="ck_review_diff_queue_resolution",
        ),
        Index("ix_review_diff_queue_status", "status"),
    )
