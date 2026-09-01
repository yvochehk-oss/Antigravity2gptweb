"""Regression tests for canonical RAG contract counterparty identities."""

import pytest

from app.routers import rag_sync


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("四川省建筑科学研究院特种技术服务中心", "EA"),
        ("攀钢集团攀枝花钢铁钒物资销售有限公司", "EB"),
        ("重庆重交大件起重吊装工程有限公司", "ED"),
    ],
)
def test_contract_seal_name_normalizes_to_canonical_code(name, expected) -> None:
    fields = {"party_b_name": name}
    code, tax_id, normalized_name, alias_code = (
        rag_sync._normalize_contract_party_identity(fields, "party_b")
    )
    assert code == expected
    assert alias_code == expected
    assert tax_id == ""
    assert normalized_name == name
    assert fields["party_b_code"] == expected
    assert fields["party_b_entity_code"] == expected


def test_contract_identity_prefers_rag_entity_code() -> None:
    fields = {
        "party_b_entity_code": "EB",
        "party_b_code": "legacy-or-tax-id",
        "party_b_name": "攀钢集团攀枝花钢铁钒物资销售有限公司",
    }
    code, _tax_id, _name, alias_code = rag_sync._normalize_contract_party_identity(
        fields, "party_b"
    )
    assert code == "EB"
    assert alias_code == "EB"
    assert fields["party_b_code"] == "EB"


def test_map_contract_fields_uses_canonical_code_for_known_seal_alias(monkeypatch) -> None:
    calls = []

    def _fake_resolve(db, raw=None, *, name=None, code=None, tax_id=None):
        calls.append({"raw": raw, "name": name, "code": code, "tax_id": tax_id})
        return raw or code

    monkeypatch.setattr(rag_sync, "_resolve_party_code", _fake_resolve)
    monkeypatch.setattr(rag_sync, "_is_internal_entity_code", lambda db, code: code == "A08")

    fields = {
        "party_a_entity_code": "A08",
        "party_a_name": "内部施工主体",
        "party_b_entity_code": "EB",
        "party_b_name": "攀钢集团攀枝花钢铁钒物资销售有限公司",
        "contract_no": "TF-A08-EXT-PG",
        "total_amount": "100.00",
    }
    mapped = rag_sync._map_contract_fields(object(), fields, 1)

    assert mapped["buyer_code"] == "A08"
    assert mapped["seller_code"] == "EB"
    assert calls[1]["raw"] == "EB"
    assert calls[1]["name"] == ""


def test_confirm_existing_alias_party_does_not_create_duplicate(monkeypatch) -> None:
    monkeypatch.setattr(
        rag_sync,
        "_resolve_party_code",
        lambda db, raw=None, *, name=None, code=None, tax_id=None: raw or code,
    )
    fields = {
        "party_b_entity_code": "ED",
        "party_b_name": "重庆重交大件起重吊装工程有限公司",
    }

    created = rag_sync._create_confirmed_external_parties(object(), fields)

    assert created == []
    assert fields["party_b_code"] == "ED"
    assert fields["party_b_entity_code"] == "ED"


def test_contract_alias_conflict_fails_closed() -> None:
    fields = {
        "party_b_entity_code": "EA",
        "party_b_name": "攀钢集团攀枝花钢铁钒物资销售有限公司",
    }
    with pytest.raises(rag_sync.SyncReviewRequired, match="冲突"):
        rag_sync._normalize_contract_party_identity(fields, "party_b")
