from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest

from sell_pending_policy_preview import build_sell_pending_policy_preview


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


class SellPendingPolicyPreviewTest(unittest.TestCase):
    def _method(self, **overrides):
        snapshot = {
            "preview_only": True,
            "execution_connected": False,
            "runtime_write": False,
            "send_order": False,
            "queue_write": False,
            "perform3_title_combo": "미체결",
            "perform3_pending_scope": "매회",
            "perform3_pending_value": "20",
            "perform3_pending_unit": "초",
            "perform3_price_left": "주문가",
            "perform3_price_right": "현재가",
        }
        snapshot.update(overrides)
        return {
            "method_set": "setting_a",
            "method_snapshot": snapshot,
        }

    def _order(self, **overrides):
        order = {
            "order_id": "ORD-1",
            "ordered_qty": 10,
            "filled_qty": 0,
            "remaining_qty": 10,
            "order_price": 70000,
            "order_status": "OPEN",
            "elapsed_time": 10,
        }
        order.update(overrides)
        return order

    def _market(self, **overrides):
        market = {
            "symbol": "005930",
            "current_price": 70000,
            "average_price": 69000,
        }
        market.update(overrides)
        return market

    def test_non_pending_mode_is_not_applicable(self):
        result = build_sell_pending_policy_preview(
            self._method(perform3_title_combo="가격비교"),
            self._order(),
            self._market(),
            {},
        )

        self.assertEqual("NOT_APPLICABLE", result["status"])
        self.assertIsNone(result["policy"])
        self.assertIsNone(result["action_preview"])

    def test_pending_without_order_id_is_blocked(self):
        result = build_sell_pending_policy_preview(self._method(), self._order(order_id=""), self._market(), {})

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("order_id is required", result["reasons"])

    def test_pending_without_remaining_qty_is_blocked(self):
        order = self._order()
        del order["remaining_qty"]

        result = build_sell_pending_policy_preview(self._method(), order, self._market(), {})

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("remaining_qty is required", result["reasons"])

    def test_remaining_qty_zero_is_not_applicable(self):
        result = build_sell_pending_policy_preview(self._method(), self._order(remaining_qty=0), self._market(), {})

        self.assertEqual("NOT_APPLICABLE", result["status"])
        self.assertIsNone(result["action_preview"])

    def test_filled_status_is_not_applicable(self):
        result = build_sell_pending_policy_preview(self._method(), self._order(order_status="FILLED"), self._market(), {})

        self.assertEqual("NOT_APPLICABLE", result["status"])
        self.assertIsNone(result["action_preview"])

    def test_cancelled_status_is_not_applicable(self):
        result = build_sell_pending_policy_preview(self._method(), self._order(order_status="CANCELLED"), self._market(), {})

        self.assertEqual("NOT_APPLICABLE", result["status"])
        self.assertIsNone(result["action_preview"])

    def test_seconds_not_reached_is_ready_without_action(self):
        result = build_sell_pending_policy_preview(
            self._method(perform3_pending_value="20", perform3_pending_unit="초"),
            self._order(elapsed_time=19),
            self._market(),
            {},
        )

        self.assertEqual("READY", result["status"])
        self.assertEqual(20, result["threshold_seconds"])
        self.assertIsNone(result["action_preview"])

    def test_seconds_reached_is_ready_with_action(self):
        result = build_sell_pending_policy_preview(
            self._method(perform3_pending_value="20", perform3_pending_unit="초"),
            self._order(elapsed_time=20),
            self._market(),
            {},
        )

        self.assertEqual("READY", result["status"])
        self.assertEqual("CANCEL_PENDING_ORDER", result["action_preview"]["action"])
        self.assertFalse(result["action_preview"]["cancel_order_called"])

    def test_minutes_convert_to_seconds(self):
        result = build_sell_pending_policy_preview(
            self._method(perform3_pending_value="2", perform3_pending_unit="분"),
            self._order(elapsed_time=119),
            self._market(),
            {},
        )

        self.assertEqual("READY", result["status"])
        self.assertEqual(120, result["threshold_seconds"])
        self.assertIsNone(result["action_preview"])

    def test_bars_without_elapsed_bars_is_blocked(self):
        result = build_sell_pending_policy_preview(
            self._method(perform3_pending_value="3", perform3_pending_unit="봉"),
            self._order(elapsed_time=None),
            self._market(),
            {},
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("elapsed_bars is required", result["reasons"])

    def test_bars_not_reached_is_ready_without_action(self):
        result = build_sell_pending_policy_preview(
            self._method(perform3_pending_value="3", perform3_pending_unit="봉"),
            self._order(elapsed_bars=2),
            self._market(),
            {},
        )

        self.assertEqual("READY", result["status"])
        self.assertEqual(3, result["threshold_bars"])
        self.assertIsNone(result["action_preview"])

    def test_bars_reached_is_ready_with_action(self):
        result = build_sell_pending_policy_preview(
            self._method(perform3_pending_value="3", perform3_pending_unit="봉"),
            self._order(elapsed_bars=3),
            self._market(),
            {},
        )

        self.assertEqual("READY", result["status"])
        self.assertEqual("CANCEL_PENDING_ORDER", result["action_preview"]["action"])

    def test_each_scope_maps_to_each(self):
        result = build_sell_pending_policy_preview(
            self._method(perform3_pending_scope="매회"),
            self._order(elapsed_time=20),
            self._market(),
            {},
        )

        self.assertEqual("EACH", result["scope"])
        self.assertEqual("EACH", result["action_preview"]["scope"])

    def test_batch_scope_maps_to_batch(self):
        result = build_sell_pending_policy_preview(
            self._method(perform3_pending_scope="일괄"),
            self._order(elapsed_time=20),
            self._market(),
            {},
        )

        self.assertEqual("BATCH", result["scope"])
        self.assertEqual("BATCH", result["action_preview"]["scope"])

    def test_invalid_scope_value_and_unit_are_invalid(self):
        invalid_scope = build_sell_pending_policy_preview(
            self._method(perform3_pending_scope="broken"),
            self._order(),
            self._market(),
            {},
        )
        invalid_value = build_sell_pending_policy_preview(
            self._method(perform3_pending_value="bad"),
            self._order(),
            self._market(),
            {},
        )
        invalid_unit = build_sell_pending_policy_preview(
            self._method(perform3_pending_unit="broken"),
            self._order(),
            self._market(),
            {},
        )

        self.assertEqual("INVALID", invalid_scope["status"])
        self.assertIn("perform3_pending_scope is invalid", invalid_scope["reasons"])
        self.assertEqual("INVALID", invalid_value["status"])
        self.assertIn("perform3_pending_value is invalid", invalid_value["reasons"])
        self.assertEqual("INVALID", invalid_unit["status"])
        self.assertIn("perform3_pending_unit is invalid", invalid_unit["reasons"])

    def test_safety_flag_true_is_invalid(self):
        result = build_sell_pending_policy_preview(self._method(runtime_write=True), self._order(), self._market(), {})

        self.assertEqual("INVALID", result["status"])
        self.assertIn("safety flag must be false: runtime_write", result["reasons"])

    def test_snapshots_are_deepcopy(self):
        method = self._method(nested={"value": 1})
        order = self._order(nested={"value": 1})
        market = self._market(nested={"value": 1})
        runtime = {"nested": {"value": 1}}

        result = build_sell_pending_policy_preview(method, order, market, runtime)
        result["method_snapshot"]["nested"]["value"] = 2
        result["order_context_snapshot"]["nested"]["value"] = 2
        result["market_context_snapshot"]["nested"]["value"] = 2
        result["runtime_context_snapshot"]["nested"]["value"] = 2

        self.assertEqual(1, method["method_snapshot"]["nested"]["value"])
        self.assertEqual(1, order["nested"]["value"])
        self.assertEqual(1, market["nested"]["value"])
        self.assertEqual(1, runtime["nested"]["value"])

    def test_inputs_are_not_mutated(self):
        method = self._method()
        order = self._order()
        market = self._market()
        runtime = {"marker": {"value": 1}}
        before = (deepcopy(method), deepcopy(order), deepcopy(market), deepcopy(runtime))

        build_sell_pending_policy_preview(method, order, market, runtime)

        self.assertEqual(before, (method, order, market, runtime))

    def test_cancel_runtime_queue_and_send_order_are_not_called_or_written(self):
        queue_path = ROOT / "runtime" / "order_queue.json"
        before = _sha256(queue_path)

        result = build_sell_pending_policy_preview(
            self._method(),
            self._order(elapsed_time=20),
            self._market(),
            {},
        )

        self.assertFalse(result["cancel_order_called"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["send_order"])
        self.assertFalse(result["execution_connected"])
        self.assertFalse(result["action_preview"]["cancel_order_called"])
        self.assertEqual(before, _sha256(queue_path))

    def test_context_type_errors_are_invalid(self):
        method_error = build_sell_pending_policy_preview(["bad"], self._order(), self._market(), {})
        order_error = build_sell_pending_policy_preview(self._method(), ["bad"], self._market(), {})
        qty_error = build_sell_pending_policy_preview(
            self._method(),
            self._order(remaining_qty="bad"),
            self._market(),
            {},
        )
        elapsed_error = build_sell_pending_policy_preview(
            self._method(),
            self._order(elapsed_time="bad"),
            self._market(),
            {},
        )

        self.assertEqual("INVALID", method_error["status"])
        self.assertIn("method_preview must be a dict", method_error["reasons"])
        self.assertEqual("INVALID", order_error["status"])
        self.assertIn("order_context must be a dict", order_error["reasons"])
        self.assertEqual("INVALID", qty_error["status"])
        self.assertIn("remaining_qty is invalid", qty_error["reasons"])
        self.assertEqual("INVALID", elapsed_error["status"])
        self.assertIn("elapsed_time is invalid", elapsed_error["reasons"])


if __name__ == "__main__":
    unittest.main()
