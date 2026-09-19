#!/usr/bin/env python3
"""Contract tests for the Windows orchestrator."""

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "orchestrate.py"
SPEC = importlib.util.spec_from_file_location("orchestrate_windows", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
orchestrate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = orchestrate
SPEC.loader.exec_module(orchestrate)


class OrchestratorContractTests(unittest.TestCase):
    def test_fix_attempt_contract_is_three(self):
        self.assertEqual(orchestrate.MAX_FIX_ATTEMPTS, 3)

    def test_profile_is_forwarded_to_bsk_bridge(self):
        original_resolve = orchestrate.resolve_driver
        original_run = orchestrate.run
        captured = {}
        orchestrate.resolve_driver = lambda requested: "bsk"

        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            return SimpleNamespace(returncode=0, stdout=b"ok", stderr=b"")

        orchestrate.run = fake_run
        try:
            state = {
                "driver": "auto",
                "browser_profile": "GPT专用",
                "chatgpt_url": "https://chatgpt.com/c/11111111-1111-1111-1111-111111111111",
                "cwd": str(ROOT),
            }
            orchestrate.bridge(state, "plan", "x")
        finally:
            orchestrate.resolve_driver = original_resolve
            orchestrate.run = original_run
        argv = captured["argv"]
        self.assertIn("--browser-profile", argv)
        self.assertEqual(argv[argv.index("--browser-profile") + 1], "GPT专用")

    def test_profile_refuses_cdp_fallback(self):
        original_resolve = orchestrate.resolve_driver
        orchestrate.resolve_driver = lambda requested: "cdp"
        try:
            state = {
                "driver": "auto",
                "browser_profile": "GPT专用",
                "chatgpt_url": "https://chatgpt.com/c/11111111-1111-1111-1111-111111111111",
                "cwd": str(ROOT),
            }
            with self.assertRaisesRegex(orchestrate.OrchestratorError, "拒绝静默降级"):
                orchestrate.bridge(state, "plan", "x")
        finally:
            orchestrate.resolve_driver = original_resolve

    def test_bsk_ready_requires_connected_browser(self):
        original_which = orchestrate.shutil.which
        original_run = orchestrate.run
        orchestrate.shutil.which = lambda name: "bsk.exe" if name in ("bsk", "bsk.exe") else None
        try:
            orchestrate.run = lambda *a, **k: SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"browsers": []}).encode(),
                stderr=b"",
            )
            self.assertFalse(orchestrate.bsk_ready())
            orchestrate.run = lambda *a, **k: SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"browsers": [{"instance_id": "abc"}]}).encode(),
                stderr=b"",
            )
            self.assertTrue(orchestrate.bsk_ready())
        finally:
            orchestrate.shutil.which = original_which
            orchestrate.run = original_run


if __name__ == "__main__":
    unittest.main()
