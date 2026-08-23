from pathlib import Path

from app.services import regulations_md_ingest as ingest


PROJECT_REGULATIONS = Path(__file__).resolve().parents[1] / "regulations_data"


def test_current_regulation_tree_uses_business_roles_only():
    role_dirs = {
        "construction": 8,
        "trade": 6,
        "labor": 5,
        "equipment": 5,
    }

    assert not list(PROJECT_REGULATIONS.glob("entity_*"))
    for role, expected_count in role_dirs.items():
        files = sorted((PROJECT_REGULATIONS / "business_roles" / role).glob("*.md"))
        assert len(files) == expected_count
        for path in files:
            metadata, _ = ingest.parse_frontmatter(path.read_text(encoding="utf-8"))
            assert metadata["business_role"] == role
            assert "Entity A" not in path.read_text(encoding="utf-8")
            assert "Entity B" not in path.read_text(encoding="utf-8")
            assert "Entity C" not in path.read_text(encoding="utf-8")
            assert "Entity D" not in path.read_text(encoding="utf-8")


def test_scan_emits_business_role_and_canonical_category():
    original = ingest.REG_DATA_DIR
    try:
        ingest.REG_DATA_DIR = PROJECT_REGULATIONS
        files = ingest.scan_md_files()
    finally:
        ingest.REG_DATA_DIR = original

    role_rows = [row for row in files if row[3].startswith("business_roles/")]
    assert len(role_rows) == 24
    assert {row[1]["business_role"] for row in role_rows} == {
        "construction",
        "trade",
        "labor",
        "equipment",
    }
    assert all(row[3] == f"business_roles/{row[1]['business_role']}" for row in role_rows)


def test_legacy_entity_directory_is_normalized_without_virtual_entity_code(tmp_path):
    legacy_dir = tmp_path / "entity_a"
    legacy_dir.mkdir()
    (legacy_dir / "legacy.md").write_text(
        "---\n"
        "title: legacy\n"
        "document_no: legacy\n"
        "status: 现行有效\n"
        "---\n\n"
        "适用业务角色：construction。\n",
        encoding="utf-8",
    )

    original = ingest.REG_DATA_DIR
    try:
        ingest.REG_DATA_DIR = tmp_path
        rows = ingest.scan_md_files()
    finally:
        ingest.REG_DATA_DIR = original

    assert len(rows) == 1
    _, metadata, _, category = rows[0]
    assert category == "business_roles/construction"
    assert metadata["business_role"] == "construction"
    assert metadata["legacy_category"] == "entity_a"
    assert "entity_code" not in metadata
