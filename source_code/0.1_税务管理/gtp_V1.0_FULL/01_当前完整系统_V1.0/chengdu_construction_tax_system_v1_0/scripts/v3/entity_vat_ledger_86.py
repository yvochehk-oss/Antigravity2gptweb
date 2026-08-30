#!/usr/bin/env python3
"""Task14c wrapper for Entity VAT Ledger under revision 86."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import entity_vat_ledger_85 as wrapped  # noqa: E402

wrapped.base.EXPECTED_HEAD = "86_v3_input_vat_claim_review_resolution"

if __name__ == "__main__":
    raise SystemExit(wrapped.base.main())
