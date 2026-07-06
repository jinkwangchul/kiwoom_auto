from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest

import signal_queue_gate_service


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ACTUAL_RULES_PATH = next((PROJECT_ROOT / "routines").glob("*/rules.json"))
RUNTIME_QUEUE_PATH = PROJECT_ROOT / "runtime" / "routine_signals.json"


def _file_sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _candidate(candidate_result: str = "READY") -> dict:
    signal = None if candidate_result == "IGNORE" else "BUY"
    decision = "IGNORE" if candidate_result == "IGNORE" else "REJECT" if candidate_result == "BLOCKED" else "ACCEPT"
    return {
        "ok": True,
        "stage": "SIGNAL_QUEUE_CANDIDATE",
        "candidate_type": "QUEUE_SIGNAL",
        "candidate_result": candidate_result,
        "signal": signal,
        "decision": decision,
        "policy_result": "IGNORE" if candidate_result == "IGNORE" else "REJECT" if candidate_result == "BLOCKED" else "PASS",
        "candidate_reason": "unit candidate",
        "rule_source": "unit_rules",
        "matched_rule_paths": ["buy.groups"] if signal else [],
        "condition_summary": ["summary"],
        "applied_policies": ["TIME_POLICY", "DELAY_POLICY"],
        "blocked_policy": "DELAY_POLICY" if candidate_result == "BLOCKED" else None,
        "signal_index": 2,
        "delay_bar": 0,
        "queue_connected": False,
        "runtime_write": False,
        "execution_connected": False,
        "send_order_connected": False,
    }


class SignalQueueGateServiceTest(unittest.TestCase):
    def test_ready_candidate_opens_gate_without_mutating_input(self):
        candidate = _candidate("READY")
        before = deepcopy(candidate)

        result = signal_queue_gate_service.build_signal_queue_gate(candidate)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["stage"], "SIGNAL_QUEUE_GATE")
        self.assertEqual(result["gate_result"], "OPEN")
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
        self.assertEqual(candidate, before)

    def test_blocked_candidate_blocks_gate(self):
        result = signal_queue_gate_service.preview_signal_queue_gate(_candidate("BLOCKED"))

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["gate_result"], "BLOCKED")
        self.assertEqual(result["candidate_result"], "BLOCKED")
        self.assertEqual(result["decision"], "REJECT")
        self.assertEqual(result["blocked_policy"], "DELAY_POLICY")

    def test_ignore_candidate_ignores_gate(self):
        result = signal_queue_gate_service.build_signal_queue_gate(_candidate("IGNORE"))

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["gate_result"], "IGNORE")
        self.assertEqual(result["candidate_result"], "IGNORE")
        self.assertEqual(result["decision"], "IGNORE")
        self.assertIsNone(result["signal"])

    def test_invalid_candidate_blocks_gate(self):
        result = signal_queue_gate_service.build_signal_queue_gate(
            {"stage": "SIGNAL_QUEUE_CANDIDATE", "candidate_result": "UNKNOWN", "signal": "BUY"}
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["gate_result"], "BLOCKED")
        self.assertEqual(result["blocked_reasons"], ["signal_queue_candidate.candidate_result is invalid"])

    def test_missing_required_candidate_fields_blocks_gate(self):
        result = signal_queue_gate_service.build_signal_queue_gate(
            {"stage": "SIGNAL_QUEUE_CANDIDATE"}
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["gate_result"], "BLOCKED")
        self.assertEqual(
            result["blocked_reasons"],
            ["missing required signal_queue_candidate fields: candidate_result, signal"],
        )

    def test_gate_service_does_not_touch_rules_or_runtime_queue(self):
        before_rules_hash = _file_sha256(ACTUAL_RULES_PATH)
        before_queue_hash = _file_sha256(RUNTIME_QUEUE_PATH)

        result = signal_queue_gate_service.build_signal_queue_gate(_candidate("READY"))

        self.assertTrue(result["ok"], result)
        self.assertEqual(_file_sha256(ACTUAL_RULES_PATH), before_rules_hash)
        self.assertEqual(_file_sha256(RUNTIME_QUEUE_PATH), before_queue_hash)

    def test_gate_service_has_no_queue_runtime_execution_or_send_order_imports(self):
        module_text = Path(signal_queue_gate_service.__file__).read_text(encoding="utf-8")

        self.assertNotIn("routine_signal_queue", module_text)
        self.assertNotIn("runtime_io", module_text)
        self.assertNotIn("import execution", module_text)
        self.assertNotIn("from execution", module_text)
        self.assertNotIn("SendOrder", module_text)
        self.assertNotIn("import send_order", module_text)
        self.assertNotIn("from send_order", module_text)
        self.assertNotIn("order_queue", module_text)
        self.assertNotIn("enqueue", module_text)


if __name__ == "__main__":
    unittest.main()
