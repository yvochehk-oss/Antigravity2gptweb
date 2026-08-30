"""V3 Party/Taxpayer ORM models sharing the existing Tax ``Base`` metadata.

The legacy ``ExternalParty`` mapping is extended in-place with the nullable
``party_id`` bridge introduced by revision 74. No second SQLAlchemy Base is
created.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

# Ensure legacy tables are registered before extending external_parties.
from . import models as legacy_models
from .db import Base


class SourceDocument(Base):
    __tablename__ = "source_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_system: Mapped[str] = mapped_column(String(40), nullable=False)
    external_document_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    file_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    document_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_uri: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'RECEIVED'")
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('RECEIVED','PARSED','EXTRACTED','VALIDATED','FAILED')",
            name="ck_source_documents_status",
        ),
        UniqueConstraint(
            "source_system",
            "external_document_id",
            name="uq_source_documents_external_identity",
        ),
        Index("ix_source_documents_status", "status"),
        Index("ix_source_documents_source_sha", "source_system", "file_sha256"),
        Index(
            "uq_source_documents_source_sha_without_external_id",
            "source_system",
            "file_sha256",
            unique=True,
            postgresql_where=text(
                "external_document_id IS NULL AND file_sha256 IS NOT NULL"
            ),
        ),
    )


class Party(Base):
    __tablename__ = "parties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    short_name: Mapped[str] = mapped_column(
        String(100), nullable=False, server_default=text("''")
    )
    party_type: Mapped[str] = mapped_column(String(16), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    __table_args__ = (
        CheckConstraint(
            "party_type IN ('internal','external')",
            name="ck_parties_party_type",
        ),
        UniqueConstraint("code", name="uq_parties_code"),
        Index("ix_parties_party_type", "party_type"),
        Index("ix_parties_active", "active"),
    )


class InternalEntity(Base):
    __tablename__ = "internal_entities"

    party_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("parties.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    canonical_code: Mapped[str] = mapped_column(String(16), nullable=False)
    business_role: Mapped[str] = mapped_column(String(32), nullable=False)
    legal_entity: Mapped[bool] = mapped_column(Boolean, nullable=False)
    parent_party_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("parties.id", ondelete="RESTRICT"),
        nullable=True,
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    __table_args__ = (
        CheckConstraint(
            "parent_party_id IS NULL OR parent_party_id <> party_id",
            name="ck_internal_entities_not_own_parent",
        ),
        UniqueConstraint("canonical_code", name="uq_internal_entities_canonical_code"),
        Index("ix_internal_entities_parent_party_id", "parent_party_id"),
    )


class PartyIdentifier(Base):
    __tablename__ = "party_identifiers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    party_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("parties.id", ondelete="CASCADE"),
        nullable=False,
    )
    identifier_type: Mapped[str] = mapped_column(String(32), nullable=False)
    identifier_value: Mapped[str] = mapped_column(String(160), nullable=False)
    source_system: Mapped[str | None] = mapped_column(String(40), nullable=True)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    __table_args__ = (
        UniqueConstraint(
            "party_id",
            "identifier_type",
            "identifier_value",
            name="uq_party_identifiers_party_type_value",
        ),
        Index("ix_party_identifiers_party_id", "party_id"),
        Index("ix_party_identifiers_lookup", "identifier_type", "identifier_value"),
    )


class PartyTaxProfile(Base):
    __tablename__ = "party_tax_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    party_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("parties.id", ondelete="CASCADE"),
        nullable=False,
    )
    tax_type: Mapped[str] = mapped_column(String(32), nullable=False)
    reporting_party_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("parties.id", ondelete="RESTRICT"),
        nullable=False,
    )
    taxpayer_category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    tax_registration_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    rule_version: Mapped[str] = mapped_column(String(40), nullable=False)
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    reviewed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    __table_args__ = (
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_party_tax_profiles_effective_range",
        ),
        Index("ix_party_tax_profiles_party_id", "party_id"),
        Index("ix_party_tax_profiles_reporting_party_id", "reporting_party_id"),
        Index(
            "ix_party_tax_profiles_lookup",
            "party_id",
            "tax_type",
            "effective_from",
        ),
    )


# Extend the existing legacy mapper after ``parties`` has been registered.
_external_table = legacy_models.ExternalParty.__table__
if "party_id" not in _external_table.c:
    _external_table.append_column(
        Column(
            "party_id",
            Integer,
            ForeignKey("parties.id", ondelete="RESTRICT"),
            nullable=True,
        )
    )
if "industry" not in _external_table.c:
    _external_table.append_column(Column("industry", String(80), nullable=True))

if not any(
    isinstance(constraint, UniqueConstraint)
    and tuple(column.name for column in constraint.columns) == ("party_id",)
    for constraint in _external_table.constraints
):
    _external_table.append_constraint(
        UniqueConstraint(
            _external_table.c.party_id,
            name="uq_external_parties_party_id",
        )
    )

if "party_id" not in legacy_models.ExternalParty.__mapper__.attrs:
    legacy_models.ExternalParty.__mapper__.add_property("party_id", _external_table.c.party_id)
if "industry" not in legacy_models.ExternalParty.__mapper__.attrs:
    legacy_models.ExternalParty.__mapper__.add_property("industry", _external_table.c.industry)
