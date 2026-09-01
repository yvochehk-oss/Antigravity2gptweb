#!/usr/bin/env python3
from pathlib import Path
import ast
import re
import textwrap

ROOT = Path(__file__).resolve().parents[1]
RAG = ROOT / 'source_code/0.2_RAG系统/project-rag-v1.1'
TAX = ROOT / 'source_code/0.1_税务管理/gtp_V1.0_FULL/01_当前完整系统_V1.0/chengdu_construction_tax_system_v1_0'
FRONT = ROOT / 'source_code/0.1_税务管理/gtp_V1.0_FULL/01_当前完整系统_V1.0/frontend_stitch'

workflow = (ROOT / '.github/workflows/phase25-ssot-hardening.yml').read_text(encoding='utf-8')
start_marker = "          python - <<'PY'\n"
end_marker = "\n          PY\n\n      - name: Commit Phase 2.5 hardening and remove one-shot workflow"
start = workflow.index(start_marker) + len(start_marker)
end = workflow.index(end_marker, start)
script = textwrap.dedent(workflow[start:end])

# The staging workflow embedded parse checks inside generated triple-quoted tests.
# Remove those checks here, apply the business patch, then write corrected tests
# below and validate every Python file ourselves.
script, count = re.subn(
    r'\n# Parse-level validation before committing remotely\..*?\n# Final static invariants\.',
    '\n# Parse-level validation is performed by scripts/phase25_apply.py.\n# Final static invariants.',
    script,
    count=1,
    flags=re.S,
)
if count != 1:
    raise RuntimeError(f'embedded validation removal count={count}')

exec(compile(script, '<phase25-business-patch>', 'exec'), {'__name__': '__main__'})

(RAG / 'tests/test_canonical_phase25_hardening.py').write_text('''from pathlib import Path
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
''', encoding='utf-8')

(TAX / 'tests/test_phase25_ssot_hardening.py').write_text('''from pathlib import Path


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
''', encoding='utf-8')

for path in (
    RAG / 'app/services/canonical_facts.py',
    RAG / 'scripts/apply_canonical_facts_ssot.py',
    RAG / 'tests/test_canonical_phase25_hardening.py',
    TAX / 'app/routers/api.py',
    TAX / 'app/routers/rag_sync.py',
    TAX / 'tests/test_phase25_ssot_hardening.py',
):
    ast.parse(path.read_text(encoding='utf-8'), filename=str(path))

assert '"canonical_facts"' in (TAX / 'app/routers/api.py').read_text(encoding='utf-8')
assert 'status_code=410' in (TAX / 'app/routers/rag_sync.py').read_text(encoding='utf-8')
assert '/api/v1/canonical-ssot/projects/${projectId}/counterparties' in (FRONT / 'src/api.ts').read_text(encoding='utf-8')
assert '确认创建交易方并导入合同' not in (FRONT / 'src/components/NewTaxRecordModal.tsx').read_text(encoding='utf-8')
