import tempfile
from pathlib import Path
import pytest
from app.services.scanner import _is_category_folder, _resolve_project_targets


def test_is_category_folder():
    assert _is_category_folder("01_项目立项与招投标文件") is True
    assert _is_category_folder("02_专业分包与施工合同") is True
    assert _is_category_folder("06_增值税发票与税务完税凭证") is True
    assert _is_category_folder("DOC-A1B2C3") is True
    assert _is_category_folder("1") is True
    assert _is_category_folder("现场材料进场磅单与签收单") is True


def test_resolve_project_targets_single_project_with_category_subfolders():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir) / "01_天府国际金融中心二期_CD-TF-001"
        root.mkdir()
        (root / "01_项目立项与招投标文件").mkdir()
        (root / "02_专业分包与施工合同").mkdir()
        (root / "03_施工进度与工程计量单").mkdir()
        (root / "04_现场材料进场磅单与签收单").mkdir()

        (root / "01_项目立项与招投标文件" / "doc1.pdf").write_bytes(b"%PDF-dummy")

        targets = _resolve_project_targets(root)
        assert len(targets) == 1
        name, path = targets[0]
        assert name == "01_天府国际金融中心二期_CD-TF-001"
        assert path == root


def test_resolve_project_targets_multi_project_container():
    with tempfile.TemporaryDirectory() as tmpdir:
        container = Path(tmpdir) / "项目存档资料"
        container.mkdir()
        
        proj1 = container / "01_天府国际金融中心二期_CD-TF-001"
        proj1.mkdir()
        (proj1 / "01_立项资料").mkdir()
        (proj1 / "01_立项资料" / "a.pdf").write_bytes(b"%PDF-a")

        proj2 = container / "02_成渝中线高铁配套_CY-CQ-002"
        proj2.mkdir()
        (proj2 / "02_合同资料").mkdir()
        (proj2 / "02_合同资料" / "b.pdf").write_bytes(b"%PDF-b")

        targets = _resolve_project_targets(container)
        target_names = {t[0] for t in targets}
        assert "01_天府国际金融中心二期_CD-TF-001" in target_names
        assert "02_成渝中线高铁配套_CY-CQ-002" in target_names
        assert len(targets) == 2
