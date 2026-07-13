from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest

from sell_method_execution_preview import build_sell_method_execution_preview


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


class SellMethodExecutionPreviewTest(unittest.TestCase):
    def _signal(self, **overrides):
        signal = {
            "signal": "SELL",
            "signal_id": "SIG_SELL_1",
            "symbol": "005930",
            "matched_groups": ["sell_pass"],
        }
        signal.update(overrides)
        return signal

    def _rules(self, *, selected_sets=None, method_overrides=None):
        method = {
            "selected_sets": ["setting_a"] if selected_sets is None else selected_sets,
            "setting_a": {
                "perform1_title_combo": "single",
                "preview_only": True,
                "execution_connected": False,
                "runtime_write": False,
                "send_order": False,
                "queue_write": False,
            },
            "setting_b": {
                "perform1_title_combo": "multi",
                "preview_only": True,
                "execution_connected": False,
                "runtime_write": False,
                "send_order": False,
                "queue_write": False,
            },
            "setting_c": {
                "perform2_title_combo": "none",
                "preview_only": True,
                "execution_connected": False,
                "runtime_write": False,
                "send_order": False,
                "queue_write": False,
            },
        }
        if method_overrides:
            method.update(method_overrides)
        return {"sell": {"method": method}}

    def _market(self, **overrides):
        market = {
            "symbol": "005930",
            "current_price": 70000,
            "average_price": 69000,
            "holding_qty": 10,
        }
        market.update(overrides)
        return market

    def _build(self, **overrides):
        kwargs = {
            "sell_signal_preview": self._signal(),
            "approved_rules": self._rules(),
            "market_context": self._market(),
        }
        kwargs.update(overrides)
        return build_sell_method_execution_preview(**kwargs)

    def test_sell_setting_a_ready(self):
        result = self._build()

        self.assertEqual("READY", result["status"])
        self.assertTrue(result["ready"])
        self.assertEqual("SELL", result["side"])
        self.assertEqual("005930", result["symbol"])
        self.assertEqual(["setting_a"], result["selected_sets"])
        self.assertEqual("setting_a", result["method_previews"][0]["method_set"])
        self.assertEqual("READY", result["method_previews"][0]["status"])

    def test_buy_signal_is_blocked(self):
        result = self._build(sell_signal_preview=self._signal(signal="BUY"))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("SELL signal is required", result["reasons"])

    def test_missing_signal_is_blocked(self):
        result = self._build(sell_signal_preview={})

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("SELL signal is required", result["reasons"])

    def test_selected_sets_missing_is_blocked(self):
        rules = self._rules(method_overrides={"selected_sets": None})

        result = self._build(approved_rules=rules)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("selected_sets is required", result["reasons"])

    def test_selected_sets_empty_is_blocked(self):
        result = self._build(approved_rules=self._rules(selected_sets=[]))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("selected_sets is empty", result["reasons"])

    def test_abc_order_is_preserved(self):
        result = self._build(approved_rules=self._rules(selected_sets=["setting_c", "setting_a", "setting_b"]))

        self.assertEqual(["setting_a", "setting_b", "setting_c"], result["selected_sets"])
        self.assertEqual(
            ["setting_a", "setting_b", "setting_c"],
            [preview["method_set"] for preview in result["method_previews"]],
        )

    def test_missing_setting_is_blocked(self):
        rules = self._rules(selected_sets=["setting_b"], method_overrides={"setting_b": None})

        result = self._build(approved_rules=rules)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("method setting is required: setting_b", result["reasons"])

    def test_unsupported_set_is_blocked(self):
        result = self._build(approved_rules=self._rules(selected_sets=["setting_x"]))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("unsupported method set: setting_x", result["reasons"])

    def test_setting_type_error_is_invalid(self):
        rules = self._rules(method_overrides={"setting_a": ["not", "dict"]})

        result = self._build(approved_rules=rules)

        self.assertEqual("INVALID", result["status"])
        self.assertIn("method setting must be a dict: setting_a", result["reasons"])

    def test_holding_qty_zero_is_blocked(self):
        result = self._build(market_context=self._market(holding_qty=0))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("holding_qty must be greater than 0", result["reasons"])

    def test_current_price_missing_is_blocked(self):
        result = self._build(market_context=self._market(current_price=None))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("current_price is required", result["reasons"])

    def test_symbol_missing_is_blocked(self):
        result = self._build(
            sell_signal_preview=self._signal(symbol=""),
            market_context=self._market(symbol=""),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("symbol is required", result["reasons"])

    def test_safety_flag_true_is_invalid(self):
        rules = self._rules(method_overrides={
            "setting_a": {
                "perform1_title_combo": "single",
                "execution_connected": True,
            }
        })

        result = self._build(approved_rules=rules)

        self.assertEqual("INVALID", result["status"])
        self.assertIn("method setting safety flag must be false: setting_a.execution_connected", result["reasons"])

    def test_order_execution_fields_stay_none(self):
        result = self._build()
        preview = result["method_previews"][0]

        for container in (result, preview):
            self.assertIsNone(container["quantity"])
            self.assertIsNone(container["price"])
            self.assertIsNone(container["hoga"])
            self.assertIsNone(container["order_type"])

    def test_method_snapshot_is_deepcopy(self):
        rules = self._rules(method_overrides={
            "setting_a": {
                "nested": {"value": 1},
                "execution_connected": False,
                "runtime_write": False,
                "send_order": False,
                "queue_write": False,
            }
        })
        result = self._build(approved_rules=rules)

        result["method_previews"][0]["method_snapshot"]["nested"]["value"] = 2

        self.assertEqual(1, rules["sell"]["method"]["setting_a"]["nested"]["value"])

    def test_inputs_are_not_mutated(self):
        signal = self._signal()
        rules = self._rules()
        market = self._market()
        before = (deepcopy(signal), deepcopy(rules), deepcopy(market))

        self._build(sell_signal_preview=signal, approved_rules=rules, market_context=market)

        self.assertEqual(before, (signal, rules, market))

    def test_queue_runtime_and_send_order_are_not_called_or_written(self):
        queue_path = ROOT / "runtime" / "order_queue.json"
        queue_before = _sha256(queue_path)

        result = self._build()

        self.assertFalse(result["queue_write"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["send_order"])
        self.assertFalse(result["execution_connected"])
        self.assertEqual(queue_before, _sha256(queue_path))


if __name__ == "__main__":
    unittest.main()
