from __future__ import annotations

from copy import deepcopy
import unittest

from buy_execution_policy import (
    POLICY_TYPE,
    STATUS_BLOCKED,
    STATUS_READY,
    evaluate_buy_execution_policy,
)


class BuyExecutionPolicyTest(unittest.TestCase):
    def _rules(self, *, base=None, repeat=None, pending=None):
        rules = {
            "buy": {
                "execution": {
                    "base": {
                        "hoga_mode": "SINGLE",
                        "order_price_basis": "ORDER_PRICE",
                        "hoga_up": 1,
                        "hoga_down": 0,
                        "point_mode": "MULTI_TIME",
                        "point_value": 3,
                        "point_unit": "MINUTE",
                        "point_range": "WITHIN",
                        "point_count": 3,
                        "ratio_left": "ORDER_PRICE",
                        "ratio_right": "AVG_PRICE",
                        "ratio_direction": "UP",
                        "ratio_value": 1.5,
                        "ratio_compare": ">=",
                        "ratio_count": 3,
                    },
                    "repeat": {
                        "apply_all": True,
                        "detail_mode": "ROUND",
                        "round_operator": "ADD",
                        "round_budget_value": 100000,
                        "budget_ratio": 50000,
                        "active_direction": "DOWN",
                        "active_ratio": 0.7,
                        "active_compare": "<=",
                    },
                }
            }
        }
        if base is not None:
            rules["buy"]["execution"]["base"] = base
        if repeat is not None:
            rules["buy"]["execution"]["repeat"] = repeat
        if pending is not None:
            rules["indicator_follow_rule_pending"] = pending
        return rules

    def _signal(self, **overrides):
        signal = {
            "signal_type": "BUY",
            "order_price": 12000,
            "current_price": 12100,
            "market_price": 12200,
        }
        signal.update(overrides)
        return signal

    def _runtime(self, **overrides):
        runtime = {
            "current_buy_round": 0,
            "used_budget": 0,
        }
        runtime.update(overrides)
        return runtime

    def _budget(self, **overrides):
        budget = {
            "total_budget": 500000,
            "remaining_budget": 500000,
            "base_round_budget": 100000,
            "max_buy_rounds": 3,
        }
        budget.update(overrides)
        return budget

    def _evaluate(self, **overrides):
        kwargs = {
            "signal_context": self._signal(),
            "approved_rules": self._rules(),
            "runtime_state_snapshot": self._runtime(),
            "budget_context": self._budget(),
        }
        kwargs.update(overrides)
        return evaluate_buy_execution_policy(**kwargs)

    def test_approved_base_repeat_ready(self):
        result = self._evaluate()

        self.assertEqual(POLICY_TYPE, result["policy_type"])
        self.assertEqual(STATUS_READY, result["status"])
        self.assertTrue(result["ready"])
        self.assertTrue(result["order_candidate_draft"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["order_write"])
        self.assertFalse(result["send_order"])
        self.assertEqual(result["next_buy_round"], 1)
        self.assertEqual(result["order_price_basis"], "ORDER_PRICE")
        self.assertEqual(result["order_price"], 12000.0)
        self.assertEqual(result["hoga_mode"], "SINGLE")
        self.assertEqual(result["hoga_up"], 1)
        self.assertEqual(result["hoga_down"], 0)
        self.assertEqual(result["round_budget"], 100000.0)
        self.assertFalse(result["is_last_round"])
        self.assertEqual(result["remaining_budget_after_candidate"], 400000.0)
        self.assertIn("approved_rule_hash", result["execution_snapshot"])

    def test_pending_namespace_is_ignored(self):
        pending = {
            "candidates": {
                "execution": {
                    "base": {
                        "value": {
                            "hoga_mode": "BROKEN",
                            "order_price_basis": "BROKEN",
                        }
                    }
                }
            }
        }

        result = self._evaluate(approved_rules=self._rules(pending=pending))

        self.assertEqual(STATUS_READY, result["status"], result)
        self.assertFalse(result["evidence"]["pending_namespace_read"])
        self.assertEqual(result["hoga_mode"], "SINGLE")

    def test_not_buy_signal_blocked(self):
        result = self._evaluate(signal_context=self._signal(signal_type="SELL"))

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertIn("NOT_BUY_SIGNAL", result["issues"])

    def test_missing_approved_rule_blocked(self):
        result = self._evaluate(approved_rules={"buy": {}})

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertIn("APPROVED_EXECUTION_RULE_MISSING", result["issues"])

    def test_single_and_multi_hoga_supported(self):
        single = self._evaluate()
        base = deepcopy(self._rules()["buy"]["execution"]["base"])
        base["hoga_mode"] = "MULTI"
        multi = self._evaluate(approved_rules=self._rules(base=base))

        self.assertEqual(STATUS_READY, single["status"])
        self.assertEqual(STATUS_READY, multi["status"])
        self.assertEqual("MULTI", multi["hoga_mode"])

    def test_order_current_and_market_price_basis(self):
        order_result = self._evaluate()
        base = deepcopy(self._rules()["buy"]["execution"]["base"])
        base["order_price_basis"] = "CURRENT_PRICE"
        current_result = self._evaluate(approved_rules=self._rules(base=base))
        base["order_price_basis"] = "MARKET"
        market_result = self._evaluate(approved_rules=self._rules(base=base))

        self.assertEqual(12000.0, order_result["order_price"])
        self.assertEqual(12100.0, current_result["order_price"])
        self.assertEqual(12200.0, market_result["order_price"])

    def test_missing_price_blocks_non_market_basis(self):
        signal = self._signal()
        signal.pop("order_price")

        result = self._evaluate(signal_context=signal)

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertIn("ORDER_PRICE_VALUE_MISSING", result["issues"])

    def test_next_buy_round_uses_runtime_state(self):
        result = self._evaluate(runtime_state_snapshot=self._runtime(current_buy_round=1))

        self.assertEqual(2, result["next_buy_round"])
        self.assertEqual(150000.0, result["round_budget"])

    def test_round_detail_add_budget(self):
        result = self._evaluate(runtime_state_snapshot=self._runtime(current_buy_round=2))

        self.assertEqual(3, result["next_buy_round"])
        self.assertEqual(200000.0, result["round_budget"])

    def test_round_detail_multiply_budget(self):
        repeat = deepcopy(self._rules()["buy"]["execution"]["repeat"])
        repeat["round_operator"] = "MULTIPLY"
        repeat["round_budget_value"] = 100000
        repeat["budget_ratio"] = 2

        result = self._evaluate(
            approved_rules=self._rules(repeat=repeat),
            runtime_state_snapshot=self._runtime(current_buy_round=2),
        )

        self.assertEqual(400000.0, result["round_budget"])

    def test_budget_detail_mode(self):
        repeat = deepcopy(self._rules()["buy"]["execution"]["repeat"])
        repeat["detail_mode"] = "BUDGET"
        repeat["budget_ratio"] = 20

        result = self._evaluate(approved_rules=self._rules(repeat=repeat))

        self.assertEqual(STATUS_READY, result["status"], result)
        self.assertEqual(100000.0, result["round_budget"])

    def test_active_buy_records_evidence_without_price_adjustment(self):
        repeat = deepcopy(self._rules()["buy"]["execution"]["repeat"])
        repeat["detail_mode"] = "ACTIVE_BUY"

        result = self._evaluate(approved_rules=self._rules(repeat=repeat))

        self.assertEqual(STATUS_READY, result["status"], result)
        self.assertFalse(result["evidence"]["round_budget_calculation"]["active_buy"]["price_adjusted"])
        self.assertEqual(12000.0, result["order_price"])

    def test_last_round(self):
        result = self._evaluate(runtime_state_snapshot=self._runtime(current_buy_round=2))

        self.assertTrue(result["is_last_round"])

    def test_round_count_exceeded_blocks(self):
        result = self._evaluate(runtime_state_snapshot=self._runtime(current_buy_round=3))

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertIn("BUY_ROUND_COUNT_EXCEEDED", result["issues"])

    def test_budget_exceeded_blocks(self):
        result = self._evaluate(
            runtime_state_snapshot=self._runtime(used_budget=450000),
            budget_context=self._budget(total_budget=500000, remaining_budget=50000),
        )

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertIn("ROUND_BUDGET_EXCEEDS_REMAINING_BUDGET", result["issues"])
        self.assertIn("TOTAL_BUDGET_EXCEEDED", result["issues"])

    def test_remaining_budget_zero_blocks(self):
        result = self._evaluate(budget_context=self._budget(remaining_budget=0))

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertIn("REMAINING_BUDGET_NOT_POSITIVE", result["issues"])

    def test_invalid_hoga_blocks(self):
        base = deepcopy(self._rules()["buy"]["execution"]["base"])
        base["hoga_up"] = -1

        result = self._evaluate(approved_rules=self._rules(base=base))

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertIn("INVALID_HOGA_VALUE", result["issues"])

    def test_invalid_mode_blocks(self):
        base = deepcopy(self._rules()["buy"]["execution"]["base"])
        base["hoga_mode"] = "UNKNOWN"

        result = self._evaluate(approved_rules=self._rules(base=base))

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertIn("INVALID_HOGA_MODE", result["issues"])

    def test_invalid_price_basis_blocks(self):
        base = deepcopy(self._rules()["buy"]["execution"]["base"])
        base["order_price_basis"] = "UNKNOWN"

        result = self._evaluate(approved_rules=self._rules(base=base))

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertIn("INVALID_ORDER_PRICE_BASIS", result["issues"])

    def test_policy_hash_mismatch_blocks(self):
        result = self._evaluate(expected_policy_hash="mismatch")

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertIn("POLICY_HASH_MISMATCH", result["issues"])

    def test_input_immutability(self):
        signal = self._signal()
        rules = self._rules()
        runtime = self._runtime()
        budget = self._budget()
        original = (deepcopy(signal), deepcopy(rules), deepcopy(runtime), deepcopy(budget))

        self._evaluate(
            signal_context=signal,
            approved_rules=rules,
            runtime_state_snapshot=runtime,
            budget_context=budget,
        )

        self.assertEqual((signal, rules, runtime, budget), original)

    def test_deterministic(self):
        first = self._evaluate()
        second = self._evaluate()

        self.assertEqual(first, second)

    def test_execution_snapshot_hash_stability(self):
        first = self._evaluate()["execution_snapshot"]
        second = self._evaluate()["execution_snapshot"]

        self.assertEqual(first, second)
        self.assertEqual(
            set(first),
            {"approved_rule_hash", "runtime_state_hash", "calculation_hash", "policy_hash"},
        )

    def test_canonical_execution_dict_input_supported(self):
        rules = self._rules()["buy"]["execution"]

        result = self._evaluate(approved_rules=rules)

        self.assertEqual(STATUS_READY, result["status"], result)

    def test_ui_and_legacy_fields_are_not_used(self):
        rules = self._rules()
        rules["buy_ui"] = {
            "base": {"hoga_combo": "BROKEN"},
            "repeat": {"apply_all_check": True},
        }
        rules["buy_method_hoga_combo"] = "BROKEN"

        result = self._evaluate(approved_rules=rules)

        self.assertEqual(STATUS_READY, result["status"], result)
        self.assertEqual("SINGLE", result["hoga_mode"])


if __name__ == "__main__":
    unittest.main()
