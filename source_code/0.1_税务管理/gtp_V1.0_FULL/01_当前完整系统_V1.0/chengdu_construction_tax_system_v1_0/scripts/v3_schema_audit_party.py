#!/usr/bin/env python3
"""Run the shared V3 schema audit with Task-05 Party metadata preloaded."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app.models  # noqa: F401,E402
import app.v3_party_models  # noqa: F401,E402
from scripts.v3_schema_audit import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
