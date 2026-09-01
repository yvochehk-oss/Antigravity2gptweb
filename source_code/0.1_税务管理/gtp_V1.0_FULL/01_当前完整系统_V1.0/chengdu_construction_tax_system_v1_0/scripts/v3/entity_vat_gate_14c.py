#!/usr/bin/env python3
"""Gate S14c wrapper for revision 86 claim-resolution support."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import entity_vat_gate_14b as gate  # noqa: E402
import entity_vat_ledger_86 as ledger86  # noqa: E402

EXPECTED_HEAD = "86_v3_input_vat_claim_review_resolution"
gate.EXPECTED_HEAD = EXPECTED_HEAD
gate.base.EXPECTED_HEAD = EXPECTED_HEAD
gate.base._source_snapshot = ledger86.wrapped._source_snapshot

if __name__ == "__main__":
    raise SystemExit(gate.main())
