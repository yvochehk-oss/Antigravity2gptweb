"""Unit tests for the Phase-A reviewed party-reference resolution manifest."""
from __future__ import annotations

import json

import pytest

from scripts.v3_party_reference_resolution import Decision, _classify, load_manifest


def _write_manifest(tmp_path, decisions):
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps({"version": 1, "decisions": decisions}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def test_classify_keeps_master_and_sentinels_distinct():
    internal = {"A08"}
    external = {"EXT-TF"}
    assert _classify("A08", internal, external) == "RESOLVED_INTERNAL"
    assert _classify("EXT-TF", internal, external) == "RESOLVED_EXTERNAL"
    assert _classify("UNKNOWN", internal, external) == "UNKNOWN_SENTINEL"
    assert _classify("A", internal, external) == "ROLE_PLACEHOLDER"
    assert _classify("EXT-MISSING", internal, external) == "UNRESOLVED"


def test_load_manifest_accepts_reviewed_mapping(tmp_path):
    path = _write_manifest(
        tmp_path,
        [{
            "source_code": "LEGACY-VENDOR",
            "action": "MAP_TO_EXISTING",
            "target_code": "EXT-TF",
            "reason": "人工核对原始合同与发票后确认",
            "reviewed": True,
            "reviewed_by": "admin",
        }],
    )
    assert load_manifest(path) == [
        Decision(
            source_code="LEGACY-VENDOR",
            action="MAP_TO_EXISTING",
            target_code="EXT-TF",
            reason="人工核对原始合同与发票后确认",
            reviewed_by="admin",
            external_party=None,
        )
    ]


def test_load_manifest_requires_explicit_review(tmp_path):
    path = _write_manifest(
        tmp_path,
        [{
            "source_code": "LEGACY-VENDOR",
            "action": "MAP_TO_EXISTING",
            "target_code": "EXT-TF",
            "reason": "尚未复核",
            "reviewed": False,
            "reviewed_by": "admin",
        }],
    )
    with pytest.raises(ValueError, match="reviewed=true"):
        load_manifest(path)


def test_load_manifest_rejects_duplicate_source_code(tmp_path):
    decision = {
        "source_code": "LEGACY-VENDOR",
        "action": "MAP_TO_EXISTING",
        "target_code": "EXT-TF",
        "reason": "人工复核",
        "reviewed": True,
        "reviewed_by": "admin",
    }
    path = _write_manifest(tmp_path, [decision, decision])
    with pytest.raises(ValueError, match="duplicate source_code"):
        load_manifest(path)


@pytest.mark.parametrize("source_code", ["UNKNOWN", "A", "B", "C", "D"])
def test_sentinel_or_role_cannot_be_created_as_external_party(tmp_path, source_code):
    path = _write_manifest(
        tmp_path,
        [{
            "source_code": source_code,
            "action": "ADD_EXTERNAL_MASTER",
            "reason": "人工复核",
            "reviewed": True,
            "reviewed_by": "admin",
            "external_party": {"name": "错误示例", "kind": "supplier"},
        }],
    )
    with pytest.raises(ValueError, match="cannot become ExternalParty"):
        load_manifest(path)


def test_reviewed_external_master_requires_name_and_kind(tmp_path):
    path = _write_manifest(
        tmp_path,
        [{
            "source_code": "EXT-REVIEWED-NEW",
            "action": "ADD_EXTERNAL_MASTER",
            "reason": "已核对供应商营业执照",
            "reviewed": True,
            "reviewed_by": "admin",
            "external_party": {"name": "已核验供应商", "kind": "supplier"},
        }],
    )
    decision = load_manifest(path)[0]
    assert decision.source_code == "EXT-REVIEWED-NEW"
    assert decision.external_party["name"] == "已核验供应商"
