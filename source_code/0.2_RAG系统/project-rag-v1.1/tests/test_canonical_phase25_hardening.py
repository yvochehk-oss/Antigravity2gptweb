from pathlib import Path
from types import SimpleNamespace

from app.services.canonical_facts import build_candidate


def _doc(**overrides):
    values = {
        "id": 301,
        "project_id": 15,
        "file_hash": "b" * 64,
        "document_type": "contract",
        "entity_code": "A08",
        "counterparty_code": "ED",
        "contract_no": "TF-A08-EXT-CRANE",
        "invoice_no": "",
        "invoice_date": "",
        "tax_vat_input": 0,
        "tax_vat_output": 0,
        "metadata_confidence": 0.95,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_document_code_vs_extracted_code_conflict_is_review_only() -> None:
    candidate = build_candidate(
        _doc(),
        "contract",
        {
            "party_b_entity_code": "EB",
            "party_b_name": "重庆重交大件起重吊装工程有限公司",
            "total_amount": 1000,
        },
    )
    assert candidate.status == "needs_review"
    assert any("party_b identity conflict" in item for item in candidate.validation_errors)


def test_extracted_code_vs_legal_name_alias_conflict_is_review_only() -> None:
    candidate = build_candidate(
        _doc(counterparty_code=""),
        "contract",
        {
            "party_b_entity_code": "EA",
            "party_b_name": "攀钢集团攀枝花钢钒物资销售有限公司",
            "total_amount": 1000,
        },
    )
    assert candidate.status == "needs_review"
    assert any("party_b identity conflict" in item for item in candidate.validation_errors)


def test_unknown_explicit_extracted_code_is_review_only() -> None:
    candidate = build_candidate(
        _doc(counterparty_code=""),
        "contract",
        {
            "party_b_entity_code": "EXT-UNKNOWN-999",
            "party_b_name": "未知供应商",
            "total_amount": 1000,
        },
    )
    assert candidate.status == "needs_review"
    assert any("not a canonical identity" in item for item in candidate.validation_errors)


def test_phase25_migration_has_current_timestamp_and_version_constraints() -> None:
    sql = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "20260902_canonical_facts_phase25_hardening.sql"
    ).read_text(encoding="utf-8")
    assert "NOT is_current OR status = 'accepted'" in sql
    assert "status <> 'accepted' OR accepted_at IS NOT NULL" in sql
    assert "uq_canonical_fact_business_version" in sql


def test_persist_path_serializes_business_key_and_blocks_superseded_replay() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "services"
        / "canonical_facts.py"
    ).read_text(encoding="utf-8")
    assert "pg_advisory_xact_lock" in source
    assert 'existing["status"] == "superseded"' in source
    assert "stale source document replay blocked" in source
