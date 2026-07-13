from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest

from sell_order_candidate_preview import build_sell_order_candidate_preview


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


class SellOrderCandidatePreviewTest(unittest.TestCase):
    def _signal(self, **overrides):
        signal = {
            "signal": "SELL",
            "signal_id": "SIG_SELL_1",
            "symbol": "005930",
        }
        signal.update(overrides)
        return signal

    def _method(self, **overrides):
        snapshot = {
            "preview_only": True,
            "execution_connected": False,
            "runtime_write": False,
            "send_order": False,
            "queue_write": False,
            "perform1_title_combo": "단일호가",
            "perform1_single_combo": "시장가",
            "perform2_title_combo": "선택없음",
        }
        snapshot.update(overrides)
        return {
            "status": "READY",
            "method_set": "setting_a",
            "method_snapshot": snapshot,
            "reasons": [],
        }

    def _market(self, **overrides):
        market = {
            "symbol": "005930",
            "current_price": 70000,
            "average_price": 69000,
            "holding_qty": 10,
            "order_price": 69900,
        }
        market.update(overrides)
        return market

    def _completion(self, **overrides):
        completion = {
            "preview_type": "SELL_COMPLETION_POLICY_PREVIEW",
            "status": "READY",
            "method_set": "setting_a",
            "policy": "MARKET_SELL_REMAINING",
            "remaining_qty": 3,
            "action_preview": {
                "action": "MARKET_SELL_REMAINING",
                "quantity": 3,
                "order_request_created": False,
                "execution_connected": False,
            },
            "execution_connected": False,
            "runtime_write": False,
            "send_order": False,
            "queue_write": False,
        }
        completion.update(overrides)
        return completion

    def _completion_candidate(self, result):
        for candidate in result["candidates"]:
            if candidate["action_source"] == "COMPLETION":
                return candidate
        return None

    def _build(self, **overrides):
        kwargs = {
            "sell_signal_preview": self._signal(),
            "method_preview": self._method(),
            "market_context": self._market(),
            "order_context": {},
            "runtime_context": {},
        }
        kwargs.update(overrides)
        return build_sell_order_candidate_preview(**kwargs)

    def test_sell_single_market_is_ready(self):
        result = self._build()

        self.assertEqual("READY", result["status"])
        self.assertEqual("SELL", result["side"])
        self.assertEqual("METHOD", result["action_source"])
        self.assertEqual("MARKET", result["hoga"])
        self.assertEqual("SELL", result["order_type"])

    def test_market_price_is_none(self):
        result = self._build()

        self.assertIsNone(result["price"])

    def test_sell_single_order_price_limit_is_ready(self):
        result = self._build(method_preview=self._method(perform1_single_combo="주문가"))

        self.assertEqual("READY", result["status"])
        self.assertEqual("LIMIT", result["hoga"])
        self.assertEqual(69900, result["price"])
        self.assertEqual("SELL", result["order_type"])

    def test_missing_order_price_is_blocked(self):
        result = self._build(
            method_preview=self._method(perform1_single_combo="주문가"),
            market_context=self._market(order_price=None),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("order_price is required", result["reasons"])

    def test_sell_single_current_price_limit_is_ready(self):
        result = self._build(method_preview=self._method(perform1_single_combo="현재가"))

        self.assertEqual("READY", result["status"])
        self.assertEqual("LIMIT", result["hoga"])
        self.assertEqual(70000, result["price"])

    def test_buy_signal_is_blocked(self):
        result = self._build(sell_signal_preview=self._signal(signal="BUY"))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("SELL signal is required", result["reasons"])

    def test_method_status_blocked_is_propagated(self):
        method = self._method()
        method["status"] = "BLOCKED"
        method["reasons"] = ["method blocked"]

        result = self._build(method_preview=method)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("method blocked", result["reasons"])

    def test_method_status_invalid_is_propagated(self):
        method = self._method()
        method["status"] = "INVALID"
        method["reasons"] = ["method invalid"]

        result = self._build(method_preview=method)

        self.assertEqual("INVALID", result["status"])
        self.assertIn("method invalid", result["reasons"])

    def test_missing_symbol_is_blocked(self):
        result = self._build(
            sell_signal_preview=self._signal(symbol=""),
            market_context=self._market(symbol=""),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("symbol is required", result["reasons"])

    def test_missing_current_price_is_blocked(self):
        result = self._build(market_context=self._market(current_price=None))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("current_price is required", result["reasons"])

    def test_holding_qty_zero_is_blocked(self):
        result = self._build(market_context=self._market(holding_qty=0))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("holding_qty must be greater than 0", result["reasons"])

    def test_method_snapshot_type_error_is_invalid(self):
        result = self._build(method_preview={"status": "READY", "method_set": "setting_a", "method_snapshot": ["bad"]})

        self.assertEqual("INVALID", result["status"])
        self.assertIn("method_snapshot must be a dict", result["reasons"])

    def test_safety_flag_true_is_invalid(self):
        result = self._build(method_preview=self._method(runtime_write=True))

        self.assertEqual("INVALID", result["status"])
        self.assertIn("safety flag must be false: runtime_write", result["reasons"])

    def test_multi_hoga_is_blocked(self):
        result = self._build(method_preview=self._method(perform1_title_combo="다중호가"))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("multi-hoga sell method is unsupported", result["reasons"])

    def test_multi_time_is_blocked(self):
        result = self._build(method_preview=self._method(perform2_title_combo="다중시간"))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("multi-time sell method is unsupported", result["reasons"])

    def test_multi_ratio_is_blocked(self):
        result = self._build(method_preview=self._method(perform2_title_combo="다중비율"))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("multi-ratio sell method is unsupported", result["reasons"])

    def test_repeat_enabled_is_blocked(self):
        result = self._build(method_preview=self._method(repeat_enabled=True))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("repeat sell method is unsupported", result["reasons"])

    def test_quantity_uses_holding_qty(self):
        result = self._build(market_context=self._market(holding_qty=7))

        self.assertEqual("READY", result["status"])
        self.assertEqual(7, result["quantity"])

    def test_order_request_is_not_created(self):
        result = self._build()

        self.assertFalse(result["order_request_created"])

    def test_candidate_is_not_created(self):
        result = self._build()

        self.assertFalse(result["candidate_created"])

    def test_runtime_queue_and_send_order_are_not_called_or_written(self):
        queue_path = ROOT / "runtime" / "order_queue.json"
        before = _sha256(queue_path)

        result = self._build()

        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["send_order"])
        self.assertFalse(result["execution_connected"])
        self.assertFalse(result["order_request_created"])
        self.assertEqual(before, _sha256(queue_path))

    def test_snapshots_are_deepcopy(self):
        method = self._method(nested={"value": 1})
        market = self._market(nested={"value": 1})
        order = {"nested": {"value": 1}}
        runtime = {"nested": {"value": 1}}

        result = self._build(method_preview=method, market_context=market, order_context=order, runtime_context=runtime)
        result["method_snapshot"]["nested"]["value"] = 2
        result["market_context_snapshot"]["nested"]["value"] = 2
        result["order_context_snapshot"]["nested"]["value"] = 2
        result["runtime_context_snapshot"]["nested"]["value"] = 2

        self.assertEqual(1, method["method_snapshot"]["nested"]["value"])
        self.assertEqual(1, market["nested"]["value"])
        self.assertEqual(1, order["nested"]["value"])
        self.assertEqual(1, runtime["nested"]["value"])

    def test_inputs_are_not_mutated(self):
        signal = self._signal()
        method = self._method()
        market = self._market()
        order = {"order_id": "ORD-1"}
        runtime = {"marker": {"value": 1}}
        before = (deepcopy(signal), deepcopy(method), deepcopy(market), deepcopy(order), deepcopy(runtime))

        build_sell_order_candidate_preview(signal, method, market, order, runtime)

        self.assertEqual(before, (signal, method, market, order, runtime))

    def test_source_preview_contains_method_preview_snapshot(self):
        method = self._method()

        result = self._build(method_preview=method)

        self.assertEqual(method, result["source_previews"]["method_preview"])

    def test_completion_ready_creates_separate_completion_candidate(self):
        result = self._build(completion_preview=self._completion())

        completion = self._completion_candidate(result)
        self.assertIsNotNone(completion)
        self.assertEqual("READY", completion["status"])
        self.assertEqual("COMPLETION", completion["action_source"])

    def test_completion_candidate_uses_remaining_qty(self):
        result = self._build(completion_preview=self._completion(remaining_qty=4))

        self.assertEqual(4, self._completion_candidate(result)["quantity"])

    def test_completion_candidate_is_market_sell_with_no_price(self):
        result = self._build(completion_preview=self._completion())
        completion = self._completion_candidate(result)

        self.assertEqual("MARKET", completion["hoga"])
        self.assertEqual("SELL", completion["order_type"])
        self.assertIsNone(completion["price"])

    def test_completion_candidate_does_not_create_order_or_candidate(self):
        result = self._build(completion_preview=self._completion())
        completion = self._completion_candidate(result)

        self.assertFalse(completion["order_request_created"])
        self.assertFalse(completion["candidate_created"])

    def test_completion_not_applicable_creates_no_completion_candidate(self):
        result = self._build(completion_preview=self._completion(status="NOT_APPLICABLE"))

        self.assertIsNone(self._completion_candidate(result))
        self.assertEqual(["METHOD"], [candidate["action_source"] for candidate in result["candidates"]])

    def test_completion_blocked_is_propagated_to_completion_candidate(self):
        result = self._build(completion_preview=self._completion(status="BLOCKED", reasons=["completion blocked"]))
        completion = self._completion_candidate(result)

        self.assertEqual("BLOCKED", completion["status"])
        self.assertIn("completion blocked", completion["reasons"])

    def test_completion_invalid_is_propagated_to_completion_candidate(self):
        result = self._build(completion_preview=self._completion(status="INVALID", reasons=["completion invalid"]))
        completion = self._completion_candidate(result)

        self.assertEqual("INVALID", completion["status"])
        self.assertIn("completion invalid", completion["reasons"])

    def test_completion_missing_remaining_qty_is_blocked(self):
        completion = self._completion()
        completion.pop("remaining_qty")

        result = self._build(completion_preview=completion)

        self.assertEqual("BLOCKED", self._completion_candidate(result)["status"])
        self.assertIn("completion remaining_qty is required", self._completion_candidate(result)["reasons"])

    def test_completion_remaining_qty_zero_or_negative_is_not_applicable(self):
        zero = self._build(completion_preview=self._completion(remaining_qty=0))
        negative = self._build(completion_preview=self._completion(remaining_qty=-1))

        self.assertEqual("NOT_APPLICABLE", self._completion_candidate(zero)["status"])
        self.assertEqual("NOT_APPLICABLE", self._completion_candidate(negative)["status"])

    def test_completion_wrong_policy_or_action_is_blocked(self):
        wrong_policy = self._build(completion_preview=self._completion(policy="CARRY_TO_NEXT_SIGNAL"))
        wrong_action = self._build(completion_preview=self._completion(action_preview={"action": "OTHER"}))

        self.assertEqual("BLOCKED", self._completion_candidate(wrong_policy)["status"])
        self.assertIn("completion policy must be MARKET_SELL_REMAINING", self._completion_candidate(wrong_policy)["reasons"])
        self.assertEqual("BLOCKED", self._completion_candidate(wrong_action)["status"])
        self.assertIn("completion action must be MARKET_SELL_REMAINING", self._completion_candidate(wrong_action)["reasons"])

    def test_completion_safety_flag_true_is_invalid(self):
        result = self._build(completion_preview=self._completion(runtime_write=True))

        self.assertEqual("INVALID", self._completion_candidate(result)["status"])
        self.assertIn("safety flag must be false: runtime_write", self._completion_candidate(result)["reasons"])

    def test_method_and_completion_ready_keep_two_candidates(self):
        result = self._build(completion_preview=self._completion())

        self.assertEqual(["METHOD", "COMPLETION"], [candidate["action_source"] for candidate in result["candidates"]])
        self.assertEqual(["READY", "READY"], [candidate["status"] for candidate in result["candidates"]])

    def test_multiple_ready_action_sources_warning(self):
        result = self._build(completion_preview=self._completion())

        self.assertIn("multiple_ready_action_sources", result["warnings"])
        for candidate in result["candidates"]:
            self.assertIn("multiple_ready_action_sources", candidate["warnings"])

    def test_pending_ready_is_not_converted_in_phase_2(self):
        pending = {
            "preview_type": "SELL_PENDING_POLICY_PREVIEW",
            "status": "READY",
            "action_preview": {"action": "CANCEL_PENDING_ORDER"},
        }

        result = self._build(pending_preview=pending)

        self.assertEqual(["METHOD"], [candidate["action_source"] for candidate in result["candidates"]])
        self.assertIn("pending_action_source_not_supported_in_phase_2", result["warnings"])

    def test_completion_snapshot_is_deepcopy(self):
        completion = self._completion(nested={"value": 1})

        result = self._build(completion_preview=completion)
        self._completion_candidate(result)["source_previews"]["completion"]["nested"]["value"] = 2

        self.assertEqual(1, completion["nested"]["value"])

    def test_completion_input_is_not_mutated(self):
        signal = self._signal()
        method = self._method()
        completion = self._completion()
        market = self._market()
        before = (deepcopy(signal), deepcopy(method), deepcopy(completion), deepcopy(market))

        build_sell_order_candidate_preview(
            signal,
            method,
            completion_preview=completion,
            market_context=market,
        )

        self.assertEqual(before, (signal, method, completion, market))

    def test_old_positional_call_compatibility(self):
        signal = self._signal()
        method = self._method()
        market = self._market()
        order = {"legacy": True}
        runtime = {"legacy_runtime": True}

        result = build_sell_order_candidate_preview(signal, method, market, order, runtime)

        self.assertEqual("READY", result["status"])
        self.assertEqual(market, result["market_context_snapshot"])
        self.assertEqual(order, result["order_context_snapshot"])
        self.assertEqual(runtime, result["runtime_context_snapshot"])


if __name__ == "__main__":
    unittest.main()
