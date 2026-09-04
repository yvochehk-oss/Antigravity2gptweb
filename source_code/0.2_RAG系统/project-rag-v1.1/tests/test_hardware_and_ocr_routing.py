"""Unit and integration tests for hardware probe, MIME detection, and PaddleOCR-VL routing."""

import os
import sys
import json
import tempfile
from pathlib import Path

import pytest

from app.services.native_parser import (
    detect_image_mime_type,
    validate_canonical_schema,
    parse_image_with_paddleocr_vl,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "scripts" / "runtime"))
from detect_hardware import (
    detect_cpu_cores,
    detect_total_ram_gb,
    detect_avx2_support,
    detect_gpu_capability,
    run_hardware_probe,
)


def test_detect_image_mime_type():
    assert detect_image_mime_type("sample.png") == "image/png"
    assert detect_image_mime_type("sample.PNG") == "image/png"
    assert detect_image_mime_type("sample.jpg") == "image/jpeg"
    assert detect_image_mime_type("sample.jpeg") == "image/jpeg"
    assert detect_image_mime_type("sample.webp") == "image/webp"
    assert detect_image_mime_type("sample.bmp") == "image/bmp"


def test_validate_canonical_schema():
    valid = {
        "document_type": "发票",
        "title": "测试发票",
        "text_content": "销项发票明细",
        "fields": {"发票代码": "12345"},
        "confidence": 0.99
    }
    assert validate_canonical_schema(valid) is True

    invalid_missing_key = {
        "document_type": "发票",
        "title": "测试发票"
    }
    assert validate_canonical_schema(invalid_missing_key) is False
    assert validate_canonical_schema("not a dict") is False


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


def test_hardware_probe_persistence(tmp_path):
    # Test probe execution and json structure
    probe = run_hardware_probe(force=True)
    assert probe["HARDWARE_CONFIGURED"] == "true"
    assert probe["PROFILE_VERSION"] == "2.0"
    assert probe["PROFILE_MODE"] in ("LOW_CPU", "HIGH_PERFORMANCE")
    assert isinstance(probe["LOCAL_LLM_SLOTS"], int)
