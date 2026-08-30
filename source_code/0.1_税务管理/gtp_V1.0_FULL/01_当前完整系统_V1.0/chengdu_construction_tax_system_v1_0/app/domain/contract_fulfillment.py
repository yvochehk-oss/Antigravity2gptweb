"""Pure Contract/Fulfillment rules for the V3 Fact layer."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable


@dataclass(frozen=True)
class ContractParties:
    buyer_party_id: int | None
    seller_party_id: int | None


@dataclass(frozen=True)
class FulfillmentParties:
    performing_party_id: int | None
    receiving_party_id: int | None


def derive_internal_trade(
    parties: ContractParties,
    internal_party_ids: Iterable[int],
) -> bool | None:
    """Derive internal-trade status from Party master membership only.

    Missing Party evidence remains unknown rather than being coerced to False.
    Legacy ``contracts.internal_trade`` must never be treated as authoritative.
    """
    if parties.buyer_party_id is None or parties.seller_party_id is None:
        return None
    internal = {int(value) for value in internal_party_ids}
    return (
        int(parties.buyer_party_id) in internal
        and int(parties.seller_party_id) in internal
    )


def validate_distinct_contract_parties(parties: ContractParties) -> None:
    if (
        parties.buyer_party_id is not None
        and parties.seller_party_id is not None
        and int(parties.buyer_party_id) == int(parties.seller_party_id)
    ):
        raise ValueError("contract buyer and seller must be distinct Parties")


def validate_distinct_fulfillment_parties(parties: FulfillmentParties) -> None:
    if (
        parties.performing_party_id is not None
        and parties.receiving_party_id is not None
        and int(parties.performing_party_id) == int(parties.receiving_party_id)
    ):
        raise ValueError("fulfillment performing and receiving Parties must be distinct")


def validate_nonnegative(value: Decimal | None, *, field_name: str) -> None:
    if value is not None and Decimal(value) < 0:
        raise ValueError(f"{field_name} must be nonnegative")


def fulfillment_may_be_independent(contract_fact_id: int | None) -> bool:
    """Return True deliberately: a fulfillment Fact may precede contract linking."""
    _ = contract_fact_id
    return True
