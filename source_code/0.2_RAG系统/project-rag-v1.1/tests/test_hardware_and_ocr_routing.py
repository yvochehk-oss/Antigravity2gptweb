"""Comprehensive unit and integration tests for hardware probe matrix, fail-closed Schema validation, OCR routing, RAG chunker filtering, and profile cache rebuilding."""

import os
import sys
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.services.native_parser import (
    detect_image_mime_type,
    validate_canonical_schema,
    parse_image_with_paddleocr_vl,
    parse_with_native_idp,
)
from app.services.chunker import chunks_from_content_list

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts" / "runtime"))
import detect_hardware
from detect_hardware import (
    detect_cpu_cores,
    detect_total_ram_gb,
    detect_avx2_support,
    detect_gpu_capability,
    run_hardware_probe,
    validate_cached_profile,
    compute_hardware_fingerprint,
)


def test_detect_image_mime_type():
    assert detect_image_mime_type("sample.png") == "image/png"
    assert detect_image_mime_type("sample.PNG") == "image/png"
    assert detect_image_mime_type("sample.jpg") == "image/jpeg"
    assert detect_image_mime_type("sample.jpeg") == "image/jpeg"
    assert detect_image_mime_type("sample.webp") == "image/webp"
    assert detect_image_mime_type("sample.bmp") == "image/bmp"
    assert detect_image_mime_type("sample.gif") == "image/gif"


def test_validate_canonical_schema_strict():
    valid = {
        "document_type": "发票",
        "title": "测试发票",
        "text_content": "销项发票明细",
        "fields": {"发票代码": "12345"},
        "confidence": 0.99
    }
    assert validate_canonical_schema(valid) is True

    # Missing required keys
    assert validate_canonical_schema({"document_type": "发票", "text_content": "abc", "fields": {}, "confidence": 0.9}) is False  # missing title
    assert validate_canonical_schema({"document_type": "发票", "title": "t", "fields": {}, "confidence": 0.9}) is False  # missing text_content
    assert validate_canonical_schema({"title": "t", "text_content": "c", "fields": {}, "confidence": 0.9}) is False  # missing document_type
    assert validate_canonical_schema({"document_type": "发票", "title": "t", "text_content": "c", "fields": {}}) is False  # missing confidence

    # Invalid confidence range / type
    assert validate_canonical_schema({"document_type": "发票", "title": "t", "text_content": "c", "fields": {}, "confidence": 1.5}) is False  # >1.0
    assert validate_canonical_schema({"document_type": "发票", "title": "t", "text_content": "c", "fields": {}, "confidence": -0.1}) is False  # <0.0
    assert validate_canonical_schema({"document_type": "发票", "title": "t", "text_content": "c", "fields": {}, "confidence": "high"}) is False  # non-numeric

    # Invalid field types
    assert validate_canonical_schema({"document_type": "", "title": "t", "text_content": "c", "fields": {}, "confidence": 0.9}) is False
    assert validate_canonical_schema({"document_type": "发票", "title": 123, "text_content": "c", "fields": {}, "confidence": 0.9}) is False
    assert validate_canonical_schema({"document_type": "发票", "title": "t", "text_content": "", "fields": {}, "confidence": 0.9}) is False
    assert validate_canonical_schema({"document_type": "发票", "title": "t", "text_content": "c", "fields": "not a dict", "confidence": 0.9}) is False
    assert validate_canonical_schema(None) is False


def test_parse_image_paddleocr_vl_fail_closed(tmp_path):
    dummy_img = tmp_path / "test.png"
    dummy_img.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89")

    # 1. Invalid JSON output -> returns None
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"choices": [{"message": {"content": "Not a valid JSON payload"}}]}
    with patch("requests.post", return_value=mock_resp):
        assert parse_image_with_paddleocr_vl(str(dummy_img)) is None

    # 2. Invalid Schema (missing confidence/title) -> returns None (fail-closed)
    invalid_schema_json = json.dumps({"document_type": "unknown", "text_content": "some text"})
    mock_resp.json.return_value = {"choices": [{"message": {"content": f"```json\n{invalid_schema_json}\n```"}}]}
    with patch("requests.post", return_value=mock_resp):
        assert parse_image_with_paddleocr_vl(str(dummy_img)) is None

    # 3. HTTP 500 Error -> returns None
    mock_resp.status_code = 500
    with patch("requests.post", return_value=mock_resp):
        assert parse_image_with_paddleocr_vl(str(dummy_img)) is None

    # 4. Valid JSON & Schema -> returns dict with text & structured_json
    valid_schema = {
        "document_type": "增值税发票",
        "title": "建工材料采购发票",
        "text_content": "采购水泥10吨",
        "fields": {"金额": "5000元"},
        "confidence": 0.98
    }
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"choices": [{"message": {"content": json.dumps(valid_schema)}}]}
    with patch("requests.post", return_value=mock_resp):
        res = parse_image_with_paddleocr_vl(str(dummy_img))
        assert res is not None
        assert res["text"] == "采购水泥10吨"
        assert res["structured_json"]["document_type"] == "增值税发票"


def test_native_parser_image_fail_closed_routing(tmp_path):
    img_path = tmp_path / "invoice.jpg"
    img_path.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xd9")

    # When VLM OCR fails, native_parser tags status REVIEW without crash or fake schema
    with patch("app.services.native_parser.parse_image_with_paddleocr_vl", return_value=None):
        doc_code = "DOC_IMG_FAIL_TEST"
        result = parse_with_native_idp(doc_code, str(img_path))
        cl_file = Path(result["content_list_path"])
        assert cl_file.exists()
        items = json.loads(cl_file.read_text(encoding="utf-8"))
        assert len(items) == 1
        assert items[0]["status"] == "REVIEW"
        assert items[0]["warning"] == "OCR_VL_FAIL_CLOSED"


def test_review_status_skipped_by_chunker(tmp_path):
    # Verify that items with status REVIEW/FAILED are 100% skipped by chunker to prevent vector index pollution
    content_list_data = [
        {
            "type": "text",
            "text": "第一章 工程合同正文条款",
            "text_level": 1,
            "page_idx": 0,
            "status": "PROCESSED"
        },
        {
            "type": "text",
            "text": "本合同由甲乙双方共同签署，遵守相关财税法规。",
            "text_level": 0,
            "page_idx": 0,
            "status": "PROCESSED"
        },
        {
            "type": "text",
            "text": "【图像原文件识别失败/待审核】: invoice.jpg",
            "text_level": 0,
            "page_idx": 1,
            "status": "REVIEW",
            "warning": "OCR_VL_FAIL_CLOSED"
        }
    ]
    cl_path = tmp_path / "content_list.json"
    cl_path.write_text(json.dumps(content_list_data, ensure_ascii=False), encoding="utf-8")

    chunks = chunks_from_content_list(str(cl_path))
    assert len(chunks) == 1
    assert "本合同由甲乙双方共同签署" in chunks[0]["content"]
    assert "图像原文件识别失败" not in chunks[0]["content"]


def test_hardware_probe_detection():
    cores = detect_cpu_cores()
    assert isinstance(cores, int)
    assert cores > 0

    ram = detect_total_ram_gb()
    assert isinstance(ram, float)
    assert ram > 0.0

    avx2 = detect_avx2_support()
    assert avx2 in ("true", "false", "not_applicable")

    gpu_info = detect_gpu_capability()
    assert isinstance(gpu_info, dict)
    assert "is_apple_silicon" in gpu_info
    assert "metal_supported" in gpu_info


def test_hardware_fingerprint_validation():
    curr_fp = compute_hardware_fingerprint()
    assert isinstance(curr_fp, str)
    assert len(curr_fp) == 12

    # Cached profile validation
    assert validate_cached_profile() is True


def test_hardware_probe_matrix_and_cache():
    # 1. Low spec simulation
    with patch("detect_hardware.detect_cpu_cores", return_value=2), \
         patch("detect_hardware.detect_total_ram_gb", return_value=8.0):
        probe = run_hardware_probe(force=True)
        assert probe["PROFILE_MODE"] == "LOW_CPU"
        assert probe["LOCAL_LLM_SLOTS"] == 2
        assert probe["PADDLE_OCR_BACKEND"] == "cpu"

    # 2. High performance Apple Silicon simulation
    with patch("detect_hardware.detect_cpu_cores", return_value=10), \
         patch("detect_hardware.detect_total_ram_gb", return_value=32.0), \
         patch("detect_hardware.detect_gpu_capability", return_value={"is_apple_silicon": True, "metal_supported": True, "cuda_supported": False, "vram_mb": 0}):
        probe = run_hardware_probe(force=True)
        assert probe["PROFILE_MODE"] == "HIGH_PERFORMANCE"
        assert probe["LOCAL_LLM_SLOTS"] == 4
        assert probe["PADDLE_OCR_BACKEND"] == "metal"
        assert probe["PROFILE_VERSION"] == "2.0"
        assert "HARDWARE_FINGERPRINT" in probe
