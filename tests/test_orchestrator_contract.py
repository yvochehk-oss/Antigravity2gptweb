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

    def test_github_flow_is_removed(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("_sync_remote_branch", source)
        self.assertNotIn("_ensure_branch", source)
        self.assertNotIn("pull --ff-only", source)
        self.assertNotIn('"--repo"', source)
        self.assertNotIn("commit_sha_head", source.replace(
            '("repo_url", "branch", "commit_sha_init", "commit_sha_head")', ""))

    def test_tunnel_verification_machinery_exists(self):
        self.assertTrue(callable(orchestrate._workspace_manifest))
        self.assertTrue(callable(orchestrate._manifest_diff))
        self.assertTrue(callable(orchestrate._make_snapshot))
        self.assertTrue(callable(orchestrate.cmd_restore))

    def test_manifest_diff_detects_changes(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td) / "ws"
            ws.mkdir()
            (ws / "a.txt").write_text("one", encoding="utf-8")
            before = orchestrate._workspace_manifest(str(ws))
            (ws / "a.txt").write_text("two", encoding="utf-8")
            (ws / "b.txt").write_text("new", encoding="utf-8")
            after = orchestrate._workspace_manifest(str(ws))
            added, modified, deleted = orchestrate._manifest_diff(before, after)
            self.assertEqual(added, ["b.txt"])
            self.assertEqual(modified, ["a.txt"])
            self.assertEqual(deleted, [])

    def test_help_does_not_reference_removed_run_all_command(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("orchestrate.py run-all", source)


if __name__ == "__main__":
    unittest.main()
