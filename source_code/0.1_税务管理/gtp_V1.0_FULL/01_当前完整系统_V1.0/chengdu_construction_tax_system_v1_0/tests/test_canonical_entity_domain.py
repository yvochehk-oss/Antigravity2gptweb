from app.domain.entities import CANONICAL_ENTITY_CODES, is_canonical_entity_code
from app.domain.entity_master import ENTITIES_MASTER

def test_canonical_internal_master_has_exactly_26_units():
    assert len(CANONICAL_ENTITY_CODES) == 26
    assert {row[0] for row in ENTITIES_MASTER} == set(CANONICAL_ENTITY_CODES)

def test_confirmed_range_boundaries_are_accepted_and_out_of_range_rejected():
    for code in ("A01", "A11", "B10", "C02", "D03"):
        assert is_canonical_entity_code(code)
    for code in ("A12", "B11", "C03", "D04", "A", "甲", "EXT-001"):
        assert not is_canonical_entity_code(code)
