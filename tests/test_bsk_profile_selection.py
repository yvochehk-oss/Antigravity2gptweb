#!/usr/bin/env python3
"""Regression tests for BrowserSkill profile selection on the macOS bridge."""

import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "bsk_chatgpt.py"
SPEC = importlib.util.spec_from_file_location("bsk_chatgpt", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
bsk_chatgpt = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bsk_chatgpt
SPEC.loader.exec_module(bsk_chatgpt)


def browser(instance_id: str, label: str, name: str = "chrome"):
    return {
        "instance_id": instance_id,
        "label": label,
        "browser_name": name,
        "browser_version": "153.0",
        "extension_version": "1.0.0",
    }


class BrowserProfileSelectionTests(unittest.TestCase):
    def test_single_profile_auto_selects_instance_id(self):
        client = bsk_chatgpt.BSKClient()
        client.list_browser_profiles = lambda: [browser("aaa11111", "GPT专用")]
        self.assertEqual(client.resolve_browser_profile(), "aaa11111")

    def test_multiple_profiles_without_selector_fail_closed(self):
        client = bsk_chatgpt.BSKClient()
        client.list_browser_profiles = lambda: [
            browser("aaa11111", "工作"),
            browser("bbb22222", "GPT专用"),
        ]
        with self.assertRaisesRegex(bsk_chatgpt.BSKError, "多个 BrowserSkill"):
            client.resolve_browser_profile()

    def test_unique_label_resolves_to_stable_instance_id(self):
        client = bsk_chatgpt.BSKClient(browser_profile="GPT专用")
        client.list_browser_profiles = lambda: [
            browser("aaa11111", "工作"),
            browser("bbb22222", "GPT专用"),
        ]
        self.assertEqual(client.resolve_browser_profile(), "bbb22222")
        self.assertEqual(client.selected_browser_instance_id, "bbb22222")

    def test_duplicate_label_is_rejected(self):
        client = bsk_chatgpt.BSKClient(browser_profile="GPT专用")
        client.list_browser_profiles = lambda: [
            browser("aaa11111", "GPT专用"),
            browser("bbb22222", "GPT专用", "edge"),
        ]
        with self.assertRaisesRegex(bsk_chatgpt.BSKError, "匹配多个"):
            client.resolve_browser_profile()

    def test_explicit_instance_id_wins(self):
        client = bsk_chatgpt.BSKClient(browser_profile="bbb22222")
        client.list_browser_profiles = lambda: [
            browser("aaa11111", "工作"),
            browser("bbb22222", "GPT专用"),
        ]
        self.assertEqual(client.resolve_browser_profile(), "bbb22222")

    def test_start_session_pins_browser_and_uses_no_focus(self):
        calls = []
        client = bsk_chatgpt.BSKClient(browser_profile="GPT专用")
        client.list_browser_profiles = lambda: [browser("bbb22222", "GPT专用")]

        def fake_exec(args, timeout=30):
            calls.append((args, timeout))
            return 0, json.dumps({
                "session_id": "sess",
                "agent_window_id": 42,
                "browser_instance_id": "bbb22222",
            }), ""

        client._exec_bsk = fake_exec
        self.assertEqual(client.start_session(), "sess")
        args, timeout = calls[-1]
        self.assertIn("--no-focus", args)
        self.assertEqual(args[args.index("--browser") + 1], "bbb22222")
        self.assertEqual(timeout, 45)

    def test_agent_window_is_default_and_borrow_is_opt_in(self):
        args = bsk_chatgpt._build_parser().parse_args([])
        self.assertFalse(args.borrow)
        self.assertFalse(args.no_borrow)

    def test_empty_feedback_evidence_auto_collects_git_diagnostics(self):
        original = bsk_chatgpt._collect_local_diagnostics
        bsk_chatgpt._collect_local_diagnostics = lambda cwd: (
            "[auto diagnostics] git status --short --untracked-files=all:\n M local.txt"
        )
        try:
            payload = bsk_chatgpt.format_evidence_payload(
                "feedback",
                "端到端闭环测试",
                "",
                level="L1",
                cwd="/tmp/example",
            )
        finally:
            bsk_chatgpt._collect_local_diagnostics = original
        self.assertIn("[Evidence (L1)]:", payload)
        self.assertIn("[auto diagnostics]", payload)
        self.assertIn("M local.txt", payload)

    def test_explicit_feedback_evidence_is_not_replaced(self):
        original = bsk_chatgpt._collect_local_diagnostics
        bsk_chatgpt._collect_local_diagnostics = lambda cwd: "SHOULD_NOT_APPEAR"
        try:
            payload = bsk_chatgpt.format_evidence_payload(
                "feedback",
                "端到端闭环测试",
                "EXIT_CODE: 1\nSTDERR: boom",
                level="L1",
                cwd="/tmp/example",
            )
        finally:
            bsk_chatgpt._collect_local_diagnostics = original
        self.assertIn("EXIT_CODE: 1", payload)
        self.assertNotIn("SHOULD_NOT_APPEAR", payload)


if __name__ == "__main__":
    unittest.main()
