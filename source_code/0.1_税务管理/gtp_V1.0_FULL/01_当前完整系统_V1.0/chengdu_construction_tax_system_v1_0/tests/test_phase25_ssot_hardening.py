from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONT = ROOT.parent / "frontend_stitch" / "src"


def test_tax_delete_preserves_rag_canonical_facts_and_outbox() -> None:
    source = (ROOT / "app" / "routers" / "api.py").read_text(encoding="utf-8")
    assert '"canonical_facts"' in source
    assert '"canonical_fact_outbox"' in source


def test_compat_counterparty_api_delegates_to_canonical_reader() -> None:
    source = (ROOT / "app" / "routers" / "api.py").read_text(encoding="utf-8")
    marker = '@router.get("/api/projects/{pid}/counterparties")'
    start = source.index(marker)
    end = source.index('@router.post("/api/projects/{pid}/delete-data")', start)
    body = source[start:end]
    assert "load_canonical_counterparties" in body
    assert "select(Contract" not in body
    assert "select(Invoice" not in body
    assert "select(CashFlow" not in body


def test_manual_contract_party_creation_endpoint_is_gone() -> None:
    source = (ROOT / "app" / "routers" / "rag_sync.py").read_text(encoding="utf-8")
    marker = '@router.post("/pending/{pending_id}/confirm-contract-and-create-parties")'
    start = source.index(marker)
    next_route = source.find("@router.", start + len(marker))
    body = source[start:] if next_route < 0 else source[start:next_route]
    assert "status_code=410" in body
    assert "_create_confirmed_external_parties" not in body
    assert "_import_record" not in body


def test_frontend_reads_canonical_counterparties_and_hides_manual_create_button() -> None:
    api = (FRONT / "api.ts").read_text(encoding="utf-8")
    modal = (FRONT / "components" / "NewTaxRecordModal.tsx").read_text(encoding="utf-8")
    assert "/api/v1/canonical-ssot/projects/${projectId}/counterparties" in api
    assert "`/api/projects/${projectId}/counterparties`" not in api
    assert "确认创建交易方并导入合同" not in modal
    assert "确认创建并导入" not in modal
    assert "Canonical Facts 自动直通，无需手工创建交易方" in modal
