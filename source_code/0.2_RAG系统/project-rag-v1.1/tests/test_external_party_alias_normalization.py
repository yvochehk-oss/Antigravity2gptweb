"""Regression tests for canonical external-party alias handling."""

from app.domain.entities import (
    get_external_preset,
    map_to_standard_external_code,
)
from app.services.metadata import (
    infer_from_filename,
    refine_from_content,
    resolve_entity_reference,
)


def test_ext_cq_maps_to_ed() -> None:
    assert map_to_standard_external_code("EXT-CQ") == "ED"
    assert map_to_standard_external_code(" ext-cq ") == "ED"
    assert get_external_preset("EXT-CQ")["code"] == "ED"


def test_preset_alias_wins_over_stale_runtime_row() -> None:
    stale_cache = [
        {
            "entity_code": "EXT-CQ",
            "name": "重庆巨力重型起重设备吊装公司",
            "short_name": "重庆巨力吊装",
            "business_role": "owner",
            "entity_kind": "external",
            "legal_entity": True,
            "status": "active",
        },
        {
            "entity_code": "ED",
            "name": "重庆巨力重型起重设备吊装公司",
            "short_name": "重庆巨力吊装",
            "business_role": "equipment",
            "entity_kind": "external",
            "legal_entity": True,
            "status": "active",
        },
    ]

    resolved = resolve_entity_reference("EXT-CQ", canonical_cache=stale_cache)

    assert resolved["status"] == "RESOLVED"
    assert resolved["entity_code"] == "ED"
    assert resolved["business_role"] == "equipment"


def test_filename_alias_is_emitted_as_canonical_counterparty() -> None:
    inferred = infer_from_filename(
        "TAX_CERT_EXT-CQ_2024Q3_完税证明.pdf",
        canonical_cache=[],
    )

    assert inferred["counterparty_code"] == "ED"
    assert inferred["counterparty_resolution_status"] == "RESOLVED"


def test_refine_normalizes_explicit_alias_even_without_content() -> None:
    refined = refine_from_content(
        {"counterparty_code": "EXT-CQ", "document_type": "other"},
        "",
        canonical_cache=[],
    )

    assert refined["counterparty_code"] == "ED"
