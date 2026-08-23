from app.domain.entities import CANONICAL_ENTITY_CODES, CANONICAL_ENTITY_RANGE_TEXT, is_canonical_entity_code, validate_entity_code

def test_canonical_internal_master_has_exactly_26_units():
    assert len(CANONICAL_ENTITY_CODES) == 26
    assert CANONICAL_ENTITY_RANGE_TEXT == "A01-A11, B01-B10, C01-C02, D01-D03"

def test_confirmed_range_boundaries_are_accepted_and_external_codes_are_not_internal():
    for code in ("A01", "A11", "B10", "C02", "D03"):
        assert is_canonical_entity_code(code)
        assert validate_entity_code(code) == code
    for code in ("A12", "B11", "C03", "D04", "A", "甲", "EXT-001"):
        assert not is_canonical_entity_code(code)
