"""Database adapter for the pure V3 taxpayer resolver."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.v3_party_models import PartyTaxProfile

from .resolver import TaxProfileWindow, resolve_reporting_party


def load_tax_profile_windows(
    session: Session,
    *,
    tax_type: str,
) -> tuple[TaxProfileWindow, ...]:
    """Load tax-profile evidence without embedding resolution logic in SQL."""
    rows = session.execute(
        select(PartyTaxProfile).where(PartyTaxProfile.tax_type == tax_type)
    ).scalars().all()
    return tuple(
        TaxProfileWindow(
            party_id=row.party_id,
            tax_type=row.tax_type,
            reporting_party_id=row.reporting_party_id,
            effective_from=row.effective_from,
            effective_to=row.effective_to,
            reviewed=bool(row.reviewed),
        )
        for row in rows
    )


def resolve_reporting_party_from_session(
    session: Session,
    party_id: int,
    as_of_date: date,
    *,
    tax_type: str = "VAT",
    require_reviewed: bool = True,
) -> int:
    """Load profile rows, then delegate to the deterministic pure resolver."""
    profiles = load_tax_profile_windows(session, tax_type=tax_type)
    return resolve_reporting_party(
        party_id,
        as_of_date,
        profiles,
        tax_type=tax_type,
        require_reviewed=require_reviewed,
    )
