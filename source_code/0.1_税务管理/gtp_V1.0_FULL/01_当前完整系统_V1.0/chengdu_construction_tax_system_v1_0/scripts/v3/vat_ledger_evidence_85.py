#!/usr/bin/env python3
"""Revision-85 wrapper for the reviewed Task14 VAT evidence loader."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import vat_ledger_evidence as base  # noqa: E402

base.EXPECTED_HEAD = "85_v3_vat_output_period_assertions"


if __name__ == "__main__":
    raise SystemExit(base.main())
