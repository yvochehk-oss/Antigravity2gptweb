"""Fail-closed Task24 Party resolution."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.v3_party_models import Party, PartyIdentifier

from .normalizer import normalize_identity_component
from .schemas import IDPPartyData


SUPPORTED_IDENTIFIER_TYPES = (
    "TAX_REGISTRATION_ID",
    "UNIFIED_SOCIAL_CREDIT_CODE",
    "SOCIAL_CREDIT_CODE",
    "CREDIT_CODE",
    "TAX_ID",
)


class PartyResolutionError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class PartyResolution:
    party_id: int
    party_code: str
    party_name: str
    canonical_tax_identity: str | None
    resolution_method: str
    credit_code: str | None
    tax_id: str | None


class PartyResolver:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _identifier_party_ids(self, value: str) -> set[int]:
        normalized = normalize_identity_component(value)
        if not normalized:
            return set()

        normalized_db_value = func.upper(
            func.regexp_replace(
                PartyIdentifier.identifier_value,
                r"\s+",
                "",
                "g",
            )
        )

        rows = self.db.execute(
            select(PartyIdentifier.party_id)
            .join(Party, Party.id == PartyIdentifier.party_id)
            .where(
                PartyIdentifier.active.is_(True),
                Party.active.is_(True),
                PartyIdentifier.identifier_type.in_(
                    SUPPORTED_IDENTIFIER_TYPES
                ),
                normalized_db_value == normalized,
            )
        ).all()

        return {int(row[0]) for row in rows}

    def _name_party_ids(self, value: str) -> set[int]:
        normalized = normalize_identity_component(value)
        if not normalized:
            return set()

        normalized_name = func.upper(
            func.regexp_replace(
                Party.name,
                r"\s+",
                "",
                "g",
            )
        )

        rows = self.db.execute(
            select(Party.id).where(
                Party.active.is_(True),
                normalized_name == normalized,
            )
        ).all()

        return {int(row[0]) for row in rows}

    def _party_by_id(self, party_id: int) -> Party:
        party = self.db.get(Party, int(party_id))
        if party is None or not party.active:
            raise PartyResolutionError(
                "PARTY_UNRESOLVED",
                f"Party {party_id} is missing or inactive",
            )
        return party

    def _canonical_tax_identity(self, party_id: int) -> str | None:
        rows = self.db.execute(
            select(PartyIdentifier.identifier_value).where(
                PartyIdentifier.party_id == int(party_id),
                PartyIdentifier.active.is_(True),
                PartyIdentifier.identifier_type == "TAX_REGISTRATION_ID",
            )
        ).all()

        values = {
            normalize_identity_component(row[0])
            for row in rows
            if normalize_identity_component(row[0])
        }

        if len(values) != 1:
            return None

        return next(iter(values))

    @staticmethod
    def _single_candidate(
        candidates: set[int],
        *,
        field: str,
    ) -> int | None:
        if len(candidates) > 1:
            raise PartyResolutionError(
                "PARTY_AMBIGUOUS",
                f"{field} resolves to multiple active Parties: "
                f"{sorted(candidates)}",
            )

        if not candidates:
            return None

        return next(iter(candidates))

    def resolve(self, source: IDPPartyData) -> PartyResolution:
        credit = normalize_identity_component(source.credit_code)
        tax_id = normalize_identity_component(source.tax_id)
        name = normalize_identity_component(source.name)

        if not credit and not tax_id and not name:
            raise PartyResolutionError(
                "PARTY_UNRESOLVED",
                "party contains no usable identity evidence",
            )

        credit_candidates = (
            self._identifier_party_ids(credit)
            if credit
            else set()
        )
        tax_candidates = (
            self._identifier_party_ids(tax_id)
            if tax_id
            else set()
        )

        credit_party = self._single_candidate(
            credit_candidates,
            field="credit_code",
        )
        tax_party = self._single_candidate(
            tax_candidates,
            field="tax_id",
        )

        if (
            credit_party is not None
            and tax_party is not None
            and credit_party != tax_party
        ):
            raise PartyResolutionError(
                "PARTY_IDENTIFIER_CONFLICT",
                "credit_code and tax_id resolve to different Parties",
            )

        strong_credit_supplied = bool(credit and len(credit) == 18)

        if strong_credit_supplied and credit_party is None and tax_party is not None:
            raise PartyResolutionError(
                "PARTY_IDENTIFIER_CONFLICT",
                "18-character credit_code is unresolved while tax_id "
                "resolves to another master-data identity",
            )

        selected_party_id: int | None = None
        method: str | None = None

        if credit_party is not None:
            selected_party_id = credit_party
            method = (
                "CREDIT_CODE_EXACT"
                if strong_credit_supplied
                else "IDENTIFIER_EXACT"
            )
        elif tax_party is not None:
            selected_party_id = tax_party
            method = "TAX_ID_EXACT"

        if selected_party_id is None:
            if credit or tax_id:
                raise PartyResolutionError(
                    "PARTY_UNRESOLVED",
                    "provided party identifier does not resolve to an "
                    "active canonical Party",
                )

            name_candidates = self._name_party_ids(name)
            name_party = self._single_candidate(
                name_candidates,
                field="name",
            )

            if name_party is None:
                raise PartyResolutionError(
                    "PARTY_UNRESOLVED",
                    f"party name {source.name!r} does not resolve",
                )

            selected_party_id = name_party
            method = "NAME_EXACT"

        party = self._party_by_id(selected_party_id)

        return PartyResolution(
            party_id=int(party.id),
            party_code=str(party.code),
            party_name=str(party.name),
            canonical_tax_identity=self._canonical_tax_identity(
                int(party.id)
            ),
            resolution_method=method or "UNKNOWN",
            credit_code=credit or None,
            tax_id=tax_id or None,
        )
