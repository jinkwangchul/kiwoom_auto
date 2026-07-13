from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest

from sell_completion_policy_preview import build_sell_completion_policy_preview


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


class SellCompletionPolicyPreviewTest(unittest.TestCase):
    def _method(self, **overrides):
        snapshot = {
            "preview_only": True,
            "execution_connected": False,
            "runtime_write": False,
            "send_order": False,
            "queue_write": False,
            "exit_price_check": False,
            "exit_count_check": False,
            "exit_time_check": False,
            "complete_policy_carry_check": False,
            "complete_policy_market_check": True,
        }
        snapshot.update(overrides)
        return {
            "method_set": "setting_a",
            "method_snapshot": snapshot,
        }

    def _exit(self, **overrides):
        data = {
            "preview_type": "SELL_EXIT_POLICY_PREVIEW",
            "status": "READY",
            "matched_conditions": [{"condition_type": "PRICE", "matched": True}],
            "execution_connected": False,
            "runtime_write": False,
            "send_order": False,
            "queue_write": False,
        }
        data.update(overrides)
        return data

    def _market(self, **overrides):
        data = {
            "symbol": "005930",
            "current_price": 70000,
            "average_price": 69000,
            "holding_qty": 10,
        }
        data.update(overrides)
        return data

    def test_no_exit_condition_carries_to_next_signal_and_is_not_applicable(self):
        result = build_sell_completion_policy_preview(self._method(), None, self._market(), {})

        self.assertEqual("NOT_APPLICABLE", result["status"])
        self.assertEqual("CARRY_TO_NEXT_SIGNAL", result["policy"])
        self.assertIsNone(result["action_preview"])
        self.assertIn("exit conditions are not configured", result["reasons"])

    def test_exit_condition_not_triggered_is_not_applicable(self):
        method = self._method(exit_price_check=True)
        exit_preview = self._exit(status="READY", matched_conditions=[])

        result = build_sell_completion_policy_preview(method, exit_preview, self._market(), {})

        self.assertEqual("NOT_APPLICABLE", result["status"])
        self.assertEqual("MARKET_SELL_REMAINING", result["policy"])
        self.assertIsNone(result["action_preview"])
        self.assertIn("exit conditions are not triggered", result["reasons"])

    def test_runtime_exit_triggered_without_remaining_qty_is_blocked(self):
        method = self._method(exit_price_check=True)

        result = build_sell_completion_policy_preview(
            method,
            self._exit(status="READY", matched_conditions=[]),
            self._market(),
            {"exit_triggered": True},
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("remaining_qty is required", result["reasons"])

    def test_matched_conditions_without_remaining_qty_is_blocked(self):
        method = self._method(exit_price_check=True)

        result = build_sell_completion_policy_preview(method, self._exit(), self._market(), {})

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("remaining_qty is required", result["reasons"])

    def test_exit_triggered_with_remaining_qty_is_ready(self):
        method = self._method(exit_price_check=True)

        result = build_sell_completion_policy_preview(
            method,
            self._exit(),
            self._market(),
            {"remaining_qty": 3},
        )

        self.assertEqual("READY", result["status"])
        self.assertEqual(3, result["remaining_qty"])

    def test_market_sell_remaining_policy(self):
        method = self._method(exit_count_check=True)

        result = build_sell_completion_policy_preview(
            method,
            self._exit(),
            self._market(),
            {"remaining_qty": 2},
        )

        self.assertEqual("MARKET_SELL_REMAINING", result["policy"])

    def test_action_preview_is_created_for_market_sell_remaining(self):
        method = self._method(exit_time_check=True)

        result = build_sell_completion_policy_preview(
            method,
            self._exit(),
            self._market(),
            {"remaining_qty": "4"},
        )

        self.assertEqual({
            "action": "MARKET_SELL_REMAINING",
            "quantity": 4.0,
            "order_request_created": False,
            "execution_connected": False,
        }, result["action_preview"])

    def test_action_preview_does_not_create_order_request(self):
        method = self._method(exit_time_check=True)

        result = build_sell_completion_policy_preview(
            method,
            self._exit(),
            self._market(),
            {"remaining_qty": 1},
        )

        self.assertFalse(result["action_preview"]["order_request_created"])
        self.assertFalse(result["action_preview"]["execution_connected"])
        self.assertFalse(result["execution_connected"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["send_order"])

    def test_remaining_qty_zero_is_not_applicable(self):
        method = self._method(exit_price_check=True)

        result = build_sell_completion_policy_preview(method, self._exit(), self._market(), {"remaining_qty": 0})

        self.assertEqual("NOT_APPLICABLE", result["status"])
        self.assertIsNone(result["action_preview"])

    def test_remaining_qty_negative_is_not_applicable(self):
        method = self._method(exit_price_check=True)

        result = build_sell_completion_policy_preview(method, self._exit(), self._market(), {"remaining_qty": -1})

        self.assertEqual("NOT_APPLICABLE", result["status"])
        self.assertIsNone(result["action_preview"])

    def test_remaining_qty_invalid_is_invalid(self):
        method = self._method(exit_price_check=True)

        result = build_sell_completion_policy_preview(method, self._exit(), self._market(), {"remaining_qty": "bad"})

        self.assertEqual("INVALID", result["status"])
        self.assertIn("remaining_qty is invalid", result["reasons"])

    def test_method_structure_error_is_invalid(self):
        result = build_sell_completion_policy_preview({"method_set": "setting_a", "method_snapshot": ["bad"]})

        self.assertEqual("INVALID", result["status"])
        self.assertIn("method_snapshot must be a dict", result["reasons"])

    def test_exit_preview_type_error_is_invalid(self):
        result = build_sell_completion_policy_preview(self._method(exit_price_check=True), ["bad"], self._market(), {})

        self.assertEqual("INVALID", result["status"])
        self.assertIn("exit_preview must be a dict", result["reasons"])

    def test_safety_flag_true_is_invalid(self):
        result = build_sell_completion_policy_preview(self._method(send_order=True), self._exit(), self._market(), {})

        self.assertEqual("INVALID", result["status"])
        self.assertIn("safety flag must be false: send_order", result["reasons"])

    def test_snapshots_are_deepcopy(self):
        method = self._method(nested={"value": 1})
        exit_preview = self._exit(nested={"value": 1})
        market = self._market(nested={"value": 1})
        runtime = {"remaining_qty": 1, "nested": {"value": 1}}

        result = build_sell_completion_policy_preview(method, exit_preview, market, runtime)
        result["method_snapshot"]["nested"]["value"] = 2
        result["exit_snapshot"]["nested"]["value"] = 2
        result["market_context_snapshot"]["nested"]["value"] = 2
        result["runtime_context_snapshot"]["nested"]["value"] = 2

        self.assertEqual(1, method["method_snapshot"]["nested"]["value"])
        self.assertEqual(1, exit_preview["nested"]["value"])
        self.assertEqual(1, market["nested"]["value"])
        self.assertEqual(1, runtime["nested"]["value"])

    def test_inputs_are_not_mutated(self):
        method = self._method(exit_price_check=True)
        exit_preview = self._exit()
        market = self._market()
        runtime = {"remaining_qty": 1}
        before = (deepcopy(method), deepcopy(exit_preview), deepcopy(market), deepcopy(runtime))

        build_sell_completion_policy_preview(method, exit_preview, market, runtime)

        self.assertEqual(before, (method, exit_preview, market, runtime))

    def test_runtime_queue_and_send_order_are_not_called_or_written(self):
        queue_path = ROOT / "runtime" / "order_queue.json"
        before = _sha256(queue_path)

        result = build_sell_completion_policy_preview(
            self._method(exit_price_check=True),
            self._exit(),
            self._market(),
            {"remaining_qty": 1},
        )

        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["send_order"])
        self.assertFalse(result["execution_connected"])
        self.assertFalse(result["action_preview"]["order_request_created"])
        self.assertEqual(before, _sha256(queue_path))

    def test_complete_policy_display_widgets_are_not_source_of_truth(self):
        method = self._method(
            exit_price_check=False,
            complete_policy_carry_check=False,
            complete_policy_market_check=True,
        )

        result = build_sell_completion_policy_preview(method, self._exit(), self._market(), {"remaining_qty": 1})

        self.assertEqual("NOT_APPLICABLE", result["status"])
        self.assertEqual("CARRY_TO_NEXT_SIGNAL", result["policy"])
        self.assertIsNone(result["action_preview"])


if __name__ == "__main__":
    unittest.main()
