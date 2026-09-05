from __future__ import annotations

from copy import deepcopy
import unittest

from buy_execution_policy import STATUS_BLOCKED, STATUS_READY, evaluate_buy_execution_policy


class BuyExecutionPolicyTest(unittest.TestCase):
    def _rules(self, *, repeat=None):
        return {
            "buy": {
                "execution": {
                    "base": {
                        "buy_phase": "BASE",
                        "buy_round": 1,
                        "budget_reference": "STARTING_BUDGET",
                        "hoga_mode": "SINGLE",
                        "order_price_basis": "ORDER_PRICE",
                        "hoga_up": 1,
                        "hoga_down": 0,
                        # These are order-placement counts, never max BUY rounds.
                        "point_count": 1,
                        "ratio_count": 1,
                    },
                    "repeat": repeat or {
                        "buy_phase": "REPEAT",
                        "starts_from_round": 2,
                        "apply_all": True,
                        "detail_mode": "ROUND",
                        "round_operator": "ADD",
                        "round_budget_value": 0.5,
                        "budget_ratio": 0.5,
                    },
                }
            }
        }

    def _signal(self, **overrides):
        value = {
            "signal_type": "BUY",
            "order_price": 10000,
            "current_price": 10000,
        }
        value.update(overrides)
        return value

    def _runtime(self, **overrides):
        value = {
            "confirmed_current_buy_round": 0,
            "confirmed_cumulative_buy_budget": 0,
        }
        value.update(overrides)
        return value

    def _budget(self, **overrides):
        value = {
            "starting_budget_type": "QUANTITY",
            "starting_quantity": 10,
            "base_buy_budget": 100000,
            "previous_buy_budget": 100000,
            "total_budget": 1000000,
            "remaining_budget": 1000000,
            "max_buy_rounds": 4,
        }
        value.update(overrides)
        return value

    def _evaluate(self, **overrides):
        values = {
            "signal_context": self._signal(),
            "approved_rules": self._rules(),
            "runtime_state_snapshot": self._runtime(),
            "budget_context": self._budget(),
        }
        values.update(overrides)
        return evaluate_buy_execution_policy(**values)

    def test_base_buy_is_first_round_from_starting_quantity(self):
        result = self._evaluate()

        self.assertEqual(STATUS_READY, result["status"])
        self.assertEqual("BASE", result["buy_phase"])
        self.assertEqual(1, result["buy_round"])
        self.assertEqual(1, result["next_buy_round"])
        self.assertEqual(10, result["quantity"])
        self.assertEqual(100000, result["round_budget"])
        self.assertEqual("STARTING_QUANTITY", result["budget_reference"])
        self.assertFalse(result["runtime_write"])

    def test_amount_base_uses_integer_shares_and_ignores_remainder(self):
        result = self._evaluate(
            signal_context=self._signal(current_price=80000, order_price=80000),
            budget_context=self._budget(
                starting_budget_type="AMOUNT",
                starting_quantity=None,
                starting_amount=120000,
            ),
        )

        self.assertEqual(STATUS_READY, result["status"], result)
        self.assertEqual(1, result["quantity"])
        self.assertEqual(80000, result["round_budget"])
        self.assertEqual(40000, result["evidence"]["budget_calculation"]["ignored_remainder"])

    def test_repeat_add_uses_confirmed_round_plus_value_times_starting_budget(self):
        result = self._evaluate(
            runtime_state_snapshot=self._runtime(
                confirmed_current_buy_round=1,
                confirmed_cumulative_buy_budget=100000,
            )
        )

        self.assertEqual(STATUS_READY, result["status"], result)
        self.assertEqual("REPEAT", result["buy_phase"])
        self.assertEqual(2, result["buy_round"])
        self.assertEqual(15, result["quantity"])
        self.assertEqual(150000, result["round_budget"])
        self.assertEqual("CONFIRMED_ROUND_PLUS_VALUE_X_STARTING_BUDGET", result["budget_reference"])

    def test_repeat_multiply_uses_confirmed_round_times_value_times_starting_budget(self):
        repeat = self._rules()["buy"]["execution"]["repeat"]
        repeat.update(round_operator="MULTIPLY", round_budget_value=1.5)
        result = self._evaluate(
            approved_rules=self._rules(repeat=repeat),
            runtime_state_snapshot=self._runtime(
                confirmed_current_buy_round=1,
                confirmed_cumulative_buy_budget=100000,
            ),
        )

        self.assertEqual(STATUS_READY, result["status"], result)
        self.assertEqual(150000, result["round_budget"])
        self.assertEqual("CONFIRMED_ROUND_X_VALUE_X_STARTING_BUDGET", result["budget_reference"])

    def test_budget_mode_uses_previous_budget_not_total_budget(self):
        repeat = self._rules()["buy"]["execution"]["repeat"]
        repeat.update(detail_mode="BUDGET", budget_ratio=0.5)
        result = self._evaluate(
            approved_rules=self._rules(repeat=repeat),
            runtime_state_snapshot=self._runtime(
                confirmed_current_buy_round=1,
                confirmed_cumulative_buy_budget=200000,
            ),
            budget_context=self._budget(previous_buy_budget=200000),
        )

        self.assertEqual(STATUS_READY, result["status"], result)
        self.assertEqual(100000, result["round_budget"])
        self.assertEqual("PREVIOUS_BUDGET", result["budget_reference"])

    def test_active_buy_remains_explicitly_unimplemented(self):
        repeat = self._rules()["buy"]["execution"]["repeat"]
        repeat.update(
            detail_mode="ACTIVE_BUY",
            active_direction="DOWN",
            active_ratio=0.7,
            active_compare="<=",
        )
        result = self._evaluate(
            approved_rules=self._rules(repeat=repeat),
            runtime_state_snapshot=self._runtime(confirmed_current_buy_round=1),
        )

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertIn("ACTIVE_BUY_NOT_IMPLEMENTED", result["issues"])
        self.assertFalse(result["evidence"]["budget_calculation"]["active_buy"]["implemented"])

    def test_only_explicit_max_rounds_limits_buy_round(self):
        result = self._evaluate(
            runtime_state_snapshot=self._runtime(confirmed_current_buy_round=2),
            budget_context=self._budget(max_buy_rounds=2),
        )
        no_explicit_limit = self._evaluate(
            runtime_state_snapshot=self._runtime(confirmed_current_buy_round=2),
            budget_context=self._budget(max_buy_rounds=None),
        )

        self.assertIn("BUY_ROUND_COUNT_EXCEEDED", result["issues"])
        self.assertNotIn("BUY_ROUND_COUNT_EXCEEDED", no_explicit_limit["issues"])

    def test_remaining_and_total_budget_guards_are_reused(self):
        runtime = self._runtime(confirmed_current_buy_round=1, confirmed_cumulative_buy_budget=900000)
        remaining = self._evaluate(
            runtime_state_snapshot=runtime,
            budget_context=self._budget(remaining_budget=50000),
        )
        total = self._evaluate(
            runtime_state_snapshot=runtime,
            budget_context=self._budget(total_budget=950000, remaining_budget=500000),
        )

        self.assertIn("ROUND_BUDGET_EXCEEDS_REMAINING_BUDGET", remaining["issues"])
        self.assertIn("TOTAL_BUDGET_EXCEEDED", total["issues"])

    def test_planning_does_not_change_confirmed_runtime_state(self):
        runtime = self._runtime(confirmed_current_buy_round=1, confirmed_cumulative_buy_budget=100000)
        original = deepcopy(runtime)

        result = self._evaluate(runtime_state_snapshot=runtime)

        self.assertEqual(original, runtime)
        self.assertEqual(1, runtime["confirmed_current_buy_round"])
        self.assertEqual(2, result["next_buy_round"])
        self.assertEqual("confirmed_fills_only", result["evidence"]["round_confirmation_source"])

    def test_loose_legacy_round_aliases_are_not_read(self):
        result = self._evaluate(
            runtime_state_snapshot={"current_buy_round": 4, "used_budget": 400000}
        )

        self.assertIn("CONFIRMED_BUY_ROUND_MISSING", result["issues"])
        self.assertIn("CONFIRMED_CUMULATIVE_BUY_BUDGET_MISSING", result["issues"])

    def test_pending_namespace_is_ignored_and_hash_is_deterministic(self):
        rules = self._rules()
        rules["indicator_follow_rule_pending"] = {"buy": {"execution": {"broken": True}}}
        first = self._evaluate(approved_rules=rules)
        second = self._evaluate(approved_rules=rules)

        self.assertEqual(STATUS_READY, first["status"])
        self.assertFalse(first["evidence"]["pending_namespace_read"])
        self.assertEqual(
            first["execution_snapshot"]["policy_hash"],
            second["execution_snapshot"]["policy_hash"],
        )

    def test_sell_signal_does_not_end_cycle_or_create_buy(self):
        result = self._evaluate(signal_context=self._signal(signal_type="SELL"))

        self.assertIn("NOT_BUY_SIGNAL", result["issues"])
        self.assertFalse(result["evidence"]["cycle_contract"]["sell_signal_alone_ends_cycle"])


if __name__ == "__main__":
    unittest.main()
