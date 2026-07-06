from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest

from engines.signal_result import RoutineSignal
import routine_signal_preview_service
import signal_decision_service


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ACTUAL_RULES_PATH = next((PROJECT_ROOT / "routines").glob("*/rules.json"))
RUNTIME_QUEUE_PATH = PROJECT_ROOT / "runtime" / "routine_signals.json"


def _file_sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _preview(signal: str | None) -> dict:
    routine_signal = RoutineSignal(
        signal,
        "decision source reason",
        ["matched_group"] if signal else [],
        ["PASS detail"] if signal else ["FAIL detail"],
        2,
        0,
    )
    return routine_signal_preview_service.build_routine_signal_preview(
        routine_signal,
        {
            "rule_source": "unit_rules",
            "matched_rule_paths": ["buy.groups"] if signal == "BUY" else ["sell.signals"] if signal == "SELL" else [],
            "condition_summary": ["summary"],
            "preview_time": "2026-07-05T12:00:00+09:00",
        },
    )


class SignalDecisionServiceTest(unittest.TestCase):
    def test_buy_preview_is_accepted_without_mutating_input(self):
        preview = _preview("BUY")
        before = deepcopy(preview)

        result = signal_decision_service.build_signal_decision_preview(preview)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["stage"], "SIGNAL_DECISION_PREVIEW")
        self.assertEqual(result["decision"], "ACCEPT")
        self.assertEqual(result["signal"], "BUY")
        self.assertEqual(result["reason"], "decision source reason")
        self.assertEqual(result["rule_source"], "unit_rules")
        self.assertEqual(result["matched_rule_paths"], ["buy.groups"])
        self.assertEqual(result["condition_summary"], ["summary"])
        self.assertFalse(result["queue_connected"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["execution_connected"])
        self.assertFalse(result["send_order_connected"])
        self.assertEqual(preview, before)

    def test_sell_preview_is_accepted(self):
        result = signal_decision_service.decide_signal(_preview("SELL"))

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["decision"], "ACCEPT")
        self.assertEqual(result["signal"], "SELL")
        self.assertEqual(result["matched_rule_paths"], ["sell.signals"])

    def test_none_preview_is_ignored(self):
        result = signal_decision_service.build_signal_decision_preview(_preview(None))

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["decision"], "IGNORE")
        self.assertIsNone(result["signal"])
        self.assertEqual(result["decision_reason"], "no executable signal in routine signal preview")

    def test_invalid_preview_is_rejected(self):
        result = signal_decision_service.build_signal_decision_preview(
            {
                "ok": True,
                "preview_type": "routine_signal_preview",
                "signal": "HOLD",
                "reason": "invalid signal",
                "rule_source": "unit_rules",
                "matched_rule_paths": [],
                "condition_summary": [],
            }
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["decision"], "REJECT")
        self.assertIsNone(result["signal"])
        self.assertEqual(result["blocked_reasons"], ["routine_signal_preview.signal is invalid"])

    def test_missing_required_preview_field_is_rejected(self):
        preview = _preview("BUY")
        del preview["rule_source"]

        result = signal_decision_service.build_signal_decision_preview(preview)

        self.assertFalse(result["ok"])
        self.assertEqual(result["decision"], "REJECT")
        self.assertEqual(
            result["blocked_reasons"],
            ["missing required routine_signal_preview fields: rule_source"],
        )

    def test_decision_service_does_not_touch_rules_or_runtime_queue(self):
        before_rules_hash = _file_sha256(ACTUAL_RULES_PATH)
        before_queue_hash = _file_sha256(RUNTIME_QUEUE_PATH)

        result = signal_decision_service.build_signal_decision_preview(_preview("BUY"))

        self.assertTrue(result["ok"], result)
        self.assertEqual(_file_sha256(ACTUAL_RULES_PATH), before_rules_hash)
        self.assertEqual(_file_sha256(RUNTIME_QUEUE_PATH), before_queue_hash)

    def test_decision_service_has_no_queue_runtime_execution_or_send_order_imports(self):
        module_text = Path(signal_decision_service.__file__).read_text(encoding="utf-8")

        self.assertNotIn("routine_signal_queue", module_text)
        self.assertNotIn("runtime_io", module_text)
        self.assertNotIn("import execution", module_text)
        self.assertNotIn("from execution", module_text)
        self.assertNotIn("SendOrder", module_text)
        self.assertNotIn("import send_order", module_text)
        self.assertNotIn("from send_order", module_text)


if __name__ == "__main__":
    unittest.main()
