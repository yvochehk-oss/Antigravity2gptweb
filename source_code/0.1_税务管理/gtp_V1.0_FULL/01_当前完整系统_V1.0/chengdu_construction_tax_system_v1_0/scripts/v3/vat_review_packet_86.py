#!/usr/bin/env python3
"""Revision-86 wrapper for the read-only Task14 VAT review packet."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import vat_review_packet as base  # noqa: E402

base.EXPECTED_HEAD = "86_v3_input_vat_claim_review_resolution"

if __name__ == "__main__":
    raise SystemExit(base.main())
