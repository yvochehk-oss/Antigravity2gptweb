from app.domain.entities import (
    CANONICAL_ENTITY_CODES,
    CANONICAL_ENTITY_RANGE_TEXT,
    is_canonical_entity_code,
    is_canonical_external_code,
    is_canonical_internal_code,
    is_canonical_party_code,
    validate_entity_code,
)
from app.services.metadata import is_canonical_party_code as is_canonical_party_code_meta
from app.services.metadata_confidence import is_canonical_party_code as is_canonical_party_code_conf


def test_canonical_internal_master_has_exactly_26_units():
    assert len(CANONICAL_ENTITY_CODES) == 26
    assert CANONICAL_ENTITY_RANGE_TEXT == "A01-A11, B01-B10, C01-C02, D01-D03"


def test_confirmed_range_boundaries_are_accepted_and_external_codes_are_not_internal():
    for code in ("A01", "A11", "B10", "C02", "D03"):
        assert is_canonical_entity_code(code)
        assert is_canonical_internal_code(code)
        assert validate_entity_code(code) == code
    for code in ("A12", "B11", "C03", "D04", "A", "甲", "EXT-001", "E01", "EA01"):
        assert not is_canonical_entity_code(code)
        assert not is_canonical_internal_code(code)


def test_metadata_canonical_code_accepts_canonical_and_rejects_aliases():
    # Valid canonical internal & external
    for code in ("A01", "A11", "B01", "B10", "C01", "C02", "D01", "D03", "E01", "E02", "EA01", "EA02", "EB01", "EB02", "EB03", "EC01", "ED01"):
        assert is_canonical_party_code(code), f"Expected canonical party: {code}"
        assert is_canonical_party_code_meta(code), f"Expected canonical in metadata: {code}"
        assert is_canonical_party_code_conf(code), f"Expected canonical in metadata_confidence: {code}"

    # Invalid codes & legacy aliases MUST BE REJECTED
    invalid_codes = (
        "EXT-CY", "EXT-GY", "EXT-SHIP", "EXT-CONC", "EXT-TREE", "EXT-CRANE", "EXT-PG", "EXT-EXP", "EXT-OWNER", "EXT-001",
        "E0", "EA", "EB", "EC", "ED",
        "E00", "EA00", "EB00", "EC00", "ED00",
        "E100", "EA100",
        "A00", "A12", "B00", "B11", "C00", "C03", "D00", "D04",
        "A", "B", "C", "D", "E",
    )
    for code in invalid_codes:
        assert not is_canonical_party_code(code), f"Expected non-canonical party: {code}"
        assert not is_canonical_party_code_meta(code), f"Expected non-canonical in metadata: {code}"
        assert not is_canonical_party_code_conf(code), f"Expected non-canonical in metadata_confidence: {code}"


