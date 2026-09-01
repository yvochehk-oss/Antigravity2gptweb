from decimal import Decimal

from app.services.canonical_ssot import consolidate_invoice_facts


def _fact(fid, seller, buyer, net, vat=0, deductible=True):
    return {
        "fact_id": fid,
        "payload": {
            "seller_entity_code": seller,
            "buyer_entity_code": buyer,
            "net_amount": net,
            "vat_amount": vat,
            "deductible": deductible,
        },
    }


def test_internal_chain_is_eliminated_and_external_boundary_counted_once() -> None:
    facts = [
        _fact(1, "EB", "A08", 1000, 130, True),
        _fact(2, "A08", "B03", 1100, 99, True),
        _fact(3, "B03", "C01", 1200, 108, True),
        _fact(4, "C01", "E0", 1600, 144, True),
    ]
    result = consolidate_invoice_facts(facts, {"A08", "B03", "C01"})
    assert result["external_cost"] == Decimal("1000")
    assert result["external_revenue"] == Decimal("1600")
    assert result["internal_eliminated"] == Decimal("2300")
    assert result["boundary_margin"] == Decimal("600")


def test_non_deductible_input_vat_enters_real_cost() -> None:
    result = consolidate_invoice_facts(
        [_fact(1, "EA", "A08", 100, 6, False)],
        {"A08"},
    )
    assert result["external_cost"] == Decimal("106")
    assert result["boundary_margin"] == Decimal("-106")


def test_external_to_external_is_outside_system() -> None:
    result = consolidate_invoice_facts([_fact(1, "EA", "EB", 500)], {"A08"})
    assert result["external_cost"] == 0
    assert result["external_revenue"] == 0
    assert result["edges"][0]["classification"] == "outside_system"
