#!/usr/bin/env python3
"""Contract tests for the macOS orchestrator."""

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "orchestrate.py"
SPEC = importlib.util.spec_from_file_location("orchestrate_macos", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
orchestrate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = orchestrate
SPEC.loader.exec_module(orchestrate)


class OrchestratorContractTests(unittest.TestCase):
    def test_fix_attempt_contract_is_three(self):
        self.assertEqual(orchestrate.MAX_FIX_ATTEMPTS, 3)

    def test_bsk_driver_is_removed(self):
        self.assertNotIn("bsk", orchestrate.BRIDGE_BY_BROWSER)
        with self.assertRaises(ValueError):
            orchestrate._resolve_bridge("bsk")

    def test_local_orchestrator_does_not_commit_or_push(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn('["git", "commit"', source)
        self.assertNotIn('["git", "push"', source)

    def test_help_does_not_reference_removed_run_all_command(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("orchestrate.py run-all", source)


if __name__ == "__main__":
    unittest.main()
