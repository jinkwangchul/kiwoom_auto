from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest

from sell_exit_policy_preview import build_sell_exit_policy_preview


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


class SellExitPolicyPreviewTest(unittest.TestCase):
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
        }
        snapshot.update(overrides)
        return {
            "method_set": "setting_a",
            "method_snapshot": snapshot,
        }

    def _price_method(self, **overrides):
        data = {
            "exit_price_check": True,
            "exit_price_left": "현재가",
            "exit_price_right": "평단가",
            "exit_price_direction": "상향",
            "exit_price_value": "1",
            "exit_price_compare": "이상",
        }
        data.update(overrides)
        return self._method(**data)

    def _count_method(self, **overrides):
        data = {
            "exit_count_check": True,
            "exit_count_line": "3",
        }
        data.update(overrides)
        return self._method(**data)

    def _time_method(self, **overrides):
        data = {
            "exit_time_check": True,
            "exit_time_line": "2",
            "exit_time_unit": "분",
        }
        data.update(overrides)
        return self._method(**data)

    def _market(self, **overrides):
        market = {
            "current_price": 102,
            "average_price": 100,
            "order_price": 101,
        }
        market.update(overrides)
        return market

    def test_all_exit_disabled_is_not_applicable(self):
        result = build_sell_exit_policy_preview(self._method(), self._market(), {})

        self.assertEqual("NOT_APPLICABLE", result["status"])
        self.assertEqual([], result["conditions"])
        self.assertEqual([], result["matched_conditions"])

    def test_current_average_price_condition_matched(self):
        result = build_sell_exit_policy_preview(self._price_method(), self._market(), {})

        self.assertEqual("READY", result["status"])
        self.assertEqual(1, len(result["matched_conditions"]))
        self.assertEqual("PRICE", result["matched_conditions"][0]["condition_type"])

    def test_current_average_price_condition_not_matched(self):
        result = build_sell_exit_policy_preview(
            self._price_method(exit_price_compare="이상"),
            self._market(current_price=100),
            {},
        )

        self.assertEqual("READY", result["status"])
        self.assertEqual([], result["matched_conditions"])
        self.assertEqual("not_matched", result["conditions"][0]["reason"])

    def test_order_price_required_without_order_price_is_blocked(self):
        result = build_sell_exit_policy_preview(
            self._price_method(exit_price_left="주문가"),
            self._market(order_price=None),
            {},
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("ORDER_PRICE is required", result["reasons"])

    def test_range_price_compare_is_blocked(self):
        result = build_sell_exit_policy_preview(
            self._price_method(exit_price_direction="상하", exit_price_compare="이내"),
            self._market(),
            {},
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("range exit price compare is unsupported in preview", result["reasons"])

    def test_count_without_execution_count_is_blocked(self):
        result = build_sell_exit_policy_preview(self._count_method(), self._market(), {})

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("execution_count is required", result["reasons"])

    def test_count_matched(self):
        result = build_sell_exit_policy_preview(self._count_method(), self._market(), {"execution_count": 3})

        self.assertEqual("READY", result["status"])
        self.assertEqual(1, len(result["matched_conditions"]))
        self.assertEqual("COUNT", result["matched_conditions"][0]["condition_type"])

    def test_count_not_matched(self):
        result = build_sell_exit_policy_preview(self._count_method(), self._market(), {"execution_count": 2})

        self.assertEqual("READY", result["status"])
        self.assertEqual([], result["matched_conditions"])

    def test_time_without_context_is_blocked(self):
        result = build_sell_exit_policy_preview(self._time_method(), self._market(), {})

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("elapsed_time is required", result["reasons"])

    def test_elapsed_time_matched(self):
        result = build_sell_exit_policy_preview(self._time_method(), self._market(), {"elapsed_time": 120})

        self.assertEqual("READY", result["status"])
        self.assertEqual(1, len(result["matched_conditions"]))
        self.assertEqual("TIME", result["matched_conditions"][0]["condition_type"])

    def test_elapsed_time_not_matched(self):
        result = build_sell_exit_policy_preview(self._time_method(), self._market(), {"elapsed_time": 119})

        self.assertEqual("READY", result["status"])
        self.assertEqual([], result["matched_conditions"])

    def test_invalid_count_and_time_values_are_invalid(self):
        count = build_sell_exit_policy_preview(self._count_method(exit_count_line="bad"), self._market(), {"execution_count": 3})
        time = build_sell_exit_policy_preview(self._time_method(exit_time_line="bad"), self._market(), {"elapsed_time": 120})

        self.assertEqual("INVALID", count["status"])
        self.assertIn("exit_count_line is invalid", count["reasons"])
        self.assertEqual("INVALID", time["status"])
        self.assertIn("exit_time_line is invalid", time["reasons"])

    def test_setting_type_error_is_invalid(self):
        result = build_sell_exit_policy_preview({"method_set": "setting_a", "method_snapshot": ["bad"]}, self._market(), {})

        self.assertEqual("INVALID", result["status"])
        self.assertIn("method_snapshot must be a dict", result["reasons"])

    def test_safety_flag_true_is_invalid(self):
        result = build_sell_exit_policy_preview(self._method(runtime_write=True), self._market(), {})

        self.assertEqual("INVALID", result["status"])
        self.assertIn("safety flag must be false: runtime_write", result["reasons"])

    def test_or_conditions_one_matched_is_ready(self):
        method = self._price_method(exit_count_check=True, exit_count_line="3")

        result = build_sell_exit_policy_preview(method, self._market(), {"execution_count": 1})

        self.assertEqual("READY", result["status"])
        self.assertEqual(["PRICE"], [item["condition_type"] for item in result["matched_conditions"]])

    def test_method_set_is_independent(self):
        method = self._price_method()
        method["method_set"] = "setting_b"

        result = build_sell_exit_policy_preview(method, self._market(), {})

        self.assertEqual("setting_b", result["method_set"])
        self.assertEqual("READY", result["status"])

    def test_snapshots_are_deepcopy(self):
        method = self._method(nested={"value": 1})
        market = self._market(nested={"price": 1})
        runtime = {"nested": {"count": 1}}

        result = build_sell_exit_policy_preview(method, market, runtime)
        result["method_snapshot"]["nested"]["value"] = 2
        result["market_context_snapshot"]["nested"]["price"] = 2
        result["runtime_context_snapshot"]["nested"]["count"] = 2

        self.assertEqual(1, method["method_snapshot"]["nested"]["value"])
        self.assertEqual(1, market["nested"]["price"])
        self.assertEqual(1, runtime["nested"]["count"])

    def test_inputs_are_not_mutated(self):
        method = self._price_method()
        market = self._market()
        runtime = {"execution_count": 1}
        before = (deepcopy(method), deepcopy(market), deepcopy(runtime))

        build_sell_exit_policy_preview(method, market, runtime)

        self.assertEqual(before, (method, market, runtime))

    def test_runtime_queue_and_send_order_are_not_called_or_written(self):
        queue_path = ROOT / "runtime" / "order_queue.json"
        before = _sha256(queue_path)

        result = build_sell_exit_policy_preview(self._price_method(), self._market(), {})

        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["send_order"])
        self.assertFalse(result["execution_connected"])
        self.assertIsNone(result["quantity"])
        self.assertIsNone(result["price"])
        self.assertIsNone(result["hoga"])
        self.assertIsNone(result["order_type"])
        self.assertEqual(before, _sha256(queue_path))


if __name__ == "__main__":
    unittest.main()
