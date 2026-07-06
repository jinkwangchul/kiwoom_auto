from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest

import signal_queue_candidate_service


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ACTUAL_RULES_PATH = next((PROJECT_ROOT / "routines").glob("*/rules.json"))
RUNTIME_QUEUE_PATH = PROJECT_ROOT / "runtime" / "routine_signals.json"


def _file_sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _orchestrator_preview(result: str = "PASS") -> dict:
    decision = "IGNORE" if result == "IGNORE" else "ACCEPT" if result == "PASS" else "REJECT"
    signal = None if result == "IGNORE" else "BUY"
    return {
        "ok": result != "REJECT",
        "stage": "SIGNAL_POLICY_PREVIEW",
        "decision": decision,
        "policy_result": "PASS" if result != "REJECT" else "REJECT",
        "policy_orchestrator": "SIGNAL_POLICY_ORCHESTRATOR",
        "policy_orchestrator_result": result,
        "policy_orchestrator_reason": "unit policy result",
        "signal": signal,
        "rule_source": "unit_rules",
        "matched_rule_paths": ["buy.groups"] if signal else [],
        "condition_summary": ["summary"],
        "applied_policies": ["TIME_POLICY", "DELAY_POLICY"],
        "blocked_policy": "DELAY_POLICY" if result == "REJECT" else None,
        "signal_index": 2,
        "delay_bar": 0,
        "queue_connected": False,
        "runtime_write": False,
        "execution_connected": False,
        "send_order_connected": False,
    }


class SignalQueueCandidateServiceTest(unittest.TestCase):
    def test_pass_policy_preview_builds_ready_candidate_without_mutating_input(self):
        preview = _orchestrator_preview("PASS")
        before = deepcopy(preview)

        result = signal_queue_candidate_service.build_signal_queue_candidate(preview)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["stage"], "SIGNAL_QUEUE_CANDIDATE")
        self.assertEqual(result["candidate_type"], "QUEUE_SIGNAL")
        self.assertEqual(result["candidate_result"], "READY")
        self.assertEqual(result["signal"], "BUY")
        self.assertEqual(result["decision"], "ACCEPT")
        self.assertEqual(result["policy_result"], "PASS")
        self.assertEqual(result["rule_source"], "unit_rules")
        self.assertEqual(result["matched_rule_paths"], ["buy.groups"])
        self.assertEqual(result["condition_summary"], ["summary"])
        self.assertEqual(result["applied_policies"], ["TIME_POLICY", "DELAY_POLICY"])
        self.assertIsNone(result["blocked_policy"])
        self.assertEqual(result["signal_index"], 2)
        self.assertEqual(result["delay_bar"], 0)
        self.assertFalse(result["queue_connected"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["execution_connected"])
        self.assertFalse(result["send_order_connected"])
        self.assertEqual(preview, before)

    def test_reject_policy_preview_builds_blocked_candidate(self):
        result = signal_queue_candidate_service.create_signal_queue_candidate(
            _orchestrator_preview("REJECT")
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["candidate_result"], "BLOCKED")
        self.assertEqual(result["decision"], "REJECT")
        self.assertEqual(result["policy_result"], "REJECT")
        self.assertEqual(result["blocked_policy"], "DELAY_POLICY")

    def test_ignore_policy_preview_builds_ignore_candidate(self):
        result = signal_queue_candidate_service.build_signal_queue_candidate(
            _orchestrator_preview("IGNORE")
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["candidate_result"], "IGNORE")
        self.assertEqual(result["decision"], "IGNORE")
        self.assertIsNone(result["signal"])
        self.assertEqual(result["policy_result"], "IGNORE")

    def test_invalid_preview_builds_blocked_candidate(self):
        result = signal_queue_candidate_service.build_signal_queue_candidate(
            {"stage": "SIGNAL_POLICY_PREVIEW"}
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["candidate_result"], "BLOCKED")
        self.assertEqual(
            result["blocked_reasons"],
            [
                "missing required policy_orchestrator_preview fields: "
                "decision, policy_result, policy_orchestrator, policy_orchestrator_result, "
                "signal, rule_source, matched_rule_paths, condition_summary, applied_policies, blocked_policy"
            ],
        )

    def test_candidate_service_does_not_touch_rules_or_runtime_queue(self):
        before_rules_hash = _file_sha256(ACTUAL_RULES_PATH)
        before_queue_hash = _file_sha256(RUNTIME_QUEUE_PATH)

        result = signal_queue_candidate_service.build_signal_queue_candidate(
            _orchestrator_preview("PASS")
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(_file_sha256(ACTUAL_RULES_PATH), before_rules_hash)
        self.assertEqual(_file_sha256(RUNTIME_QUEUE_PATH), before_queue_hash)

    def test_candidate_service_has_no_queue_runtime_execution_or_send_order_imports(self):
        module_text = Path(signal_queue_candidate_service.__file__).read_text(encoding="utf-8")

        self.assertNotIn("routine_signal_queue", module_text)
        self.assertNotIn("runtime_io", module_text)
        self.assertNotIn("import execution", module_text)
        self.assertNotIn("from execution", module_text)
        self.assertNotIn("SendOrder", module_text)
        self.assertNotIn("import send_order", module_text)
        self.assertNotIn("from send_order", module_text)
        self.assertNotIn("order_queue", module_text)


if __name__ == "__main__":
    unittest.main()
