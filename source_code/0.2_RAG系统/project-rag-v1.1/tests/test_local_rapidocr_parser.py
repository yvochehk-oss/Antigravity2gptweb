"""Contract tests for the offline RapidOCR compatibility CLI.

All OCR calls are mocked: these tests require neither local model files nor
project documents and are safe to run in CI.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import fitz
import pytest

from app.services import mineru_adapter

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "mineru_mock.py"
SPEC = importlib.util.spec_from_file_location("local_rapidocr_parser", SCRIPT)
assert SPEC and SPEC.loader
parser = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = parser
SPEC.loader.exec_module(parser)


def _pdf_with_text(path: Path, text: str = "A sufficiently long embedded PDF text for parser contract testing.") -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    document.save(path)
    document.close()


def test_default_models_dir_resolves_to_v2_models() -> None:
    assert parser.default_models_dir() == ROOT.parents[2] / "models" / "rapidocr"


def test_override_models_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RAPIDOCR_MODELS_DIR", str(tmp_path / "models"))
    assert parser.models_dir() == tmp_path / "models"


def test_embedded_pdf_creates_adapter_compatible_output(tmp_path: Path) -> None:
    source = tmp_path / "embedded.pdf"
    output = tmp_path / "parsed"
    _pdf_with_text(source)

    parser.parse_file(str(source), str(output))

    content = json.loads((output / "embedded_content_list.json").read_text(encoding="utf-8"))
    assert len(content) == 1
    assert content[0]["type"] == "text"
    assert content[0]["page_idx"] == 0
    assert "sufficiently long embedded" in content[0]["text"]
    assert (output / "embedded.md").read_text(encoding="utf-8") == content[0]["text"]


def test_empty_ocr_fails_without_creating_placeholder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "scan.png"
    source.write_bytes(b"not inspected because OCR is mocked")
    output = tmp_path / "parsed"
    monkeypatch.setattr(parser, "get_ocr_engine", lambda: object())
    monkeypatch.setattr(parser, "_ocr_lines", lambda *_args: ("", None))

    with pytest.raises(parser.ParserError, match="no usable text"):
        parser.parse_file(str(source), str(output))
    assert not output.exists()


def test_unsupported_type_fails_without_output(tmp_path: Path) -> None:
    source = tmp_path / "contract.docx"
    source.write_bytes(b"not a real docx")
    output = tmp_path / "parsed"

    with pytest.raises(parser.ParserError, match="does not support"):
        parser.parse_file(str(source), str(output))
    assert not output.exists()


def test_image_ocr_contract_with_mocked_engine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "invoice.jpg"
    source.write_bytes(b"mocked")
    output = tmp_path / "parsed"
    monkeypatch.setattr(parser, "get_ocr_engine", lambda: object())
    monkeypatch.setattr(parser, "_ocr_lines", lambda *_args: ("invoice content", 0.91))

    parser.parse_file(str(source), str(output))
    content = json.loads((output / "invoice_content_list.json").read_text(encoding="utf-8"))
    assert content == [{"type": "text", "text": "invoice content", "page_idx": 0, "score": 0.91}]


def test_cli_version_and_backend_rejection(tmp_path: Path) -> None:
    version = subprocess.run([sys.executable, str(SCRIPT), "--version"], text=True, capture_output=True)
    assert version.returncode == 0
    assert "local-rapidocr-parser" in version.stdout

    source = tmp_path / "input.pdf"
    _pdf_with_text(source)
    rejected = subprocess.run(
        [sys.executable, str(SCRIPT), "-p", str(source), "-o", str(tmp_path / "out"), "-b", "remote"],
        text=True,
        capture_output=True,
    )
    assert rejected.returncode == 2
    assert "unsupported" in rejected.stderr


def test_adapter_uses_service_python_for_local_python_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mineru_adapter, "MINERU_BIN", "/tmp/local_ocr.py")
    assert mineru_adapter._mineru_command("--version") == [
        sys.executable,
        "/tmp/local_ocr.py",
        "--version",
    ]


def test_adapter_keeps_native_mineru_binary_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mineru_adapter, "MINERU_BIN", "mineru")
    assert mineru_adapter._mineru_command("--version") == ["mineru", "--version"]
