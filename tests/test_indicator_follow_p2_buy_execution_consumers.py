# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import unittest

from tests.test_indicator_follow_buy_execution_connection import (
    IndicatorFollowBuyExecutionConnectionTest as _BuyHelper,
    bridge,
)
from tests.test_indicator_follow_cycle_projection import (
    IndicatorFollowCycleProjectionTest as _ProjectionHelper,
)


def _additional(*, skip: bool = False, last: bool = False, method: str = "MARKET") -> dict:
    return {
        "previous_round_price_skip": {
            "enabled": skip,
            "reference_source": "PREVIOUS_CONFIRMED_BUY_ORDER_PRICE",
            "current_source": "ACTIONABLE_ORDER_PRICE",
            "direction": "UP",
            "ratio_percent": 5.0,
            "comparator": ">=",
            "action": "SKIP_CURRENT_GENERATION",
            "skipped_round_increment": False,
        },
        "last_plus_one": {
            "enabled": last,
            "generation_kind": "LAST_PLUS_ONE",
            "trigger": "AFTER_NORMAL_MAX_ROUND_COMPLETED",
            "max_occurrences": 1,
            "method": method,
            "active_condition": {
                "lhs_source": "ACTIONABLE_ORDER_PRICE",
                "rhs_source": "AVERAGE_PRICE",
                "direction": "DOWN",
                "ratio_percent": 5.0,
                "comparator": ">=",
            },
            "budget_basis": "LAST_NORMAL_ROUND_APPROVED_BUDGET",
            "terminal_after_completed_fill": True,
        },
        "execution_connected": True,
        "execution_lock_reason": "",
    }


def _cycle_policy(*, hoga: str = "SINGLE", point: str = "NONE", situation: str = "UNFILLED") -> dict:
    point_policy: dict = {"mode": point}
    if point == "MULTI_TIME":
        point_policy.update(value=1, unit="SECOND", range="INTERVAL", count=3, order_price_basis="CURRENT_PRICE")
    elif point == "MULTI_RATIO":
        point_policy.update(
            left_source="ORDER_PRICE", right_source="CURRENT_PRICE", direction="UP",
            ratio_percent=0.5, comparator=">=", count=3,
        )
    response = {
        "mode": "UNFILLED", "action": "CANCEL", "scope": "EACH",
        "configured_value": 5, "configured_unit": "SECOND", "anchor": "BROKER_ACCEPTED_AT",
    }
    if situation == "RESET":
        response = {
            "mode": "PRICE_COMPARE", "left_source": "ORDER_PRICE", "right_source": "CURRENT_PRICE",
            "direction": "UP", "ratio_percent": 1.0, "comparator": ">=", "action": "RESET",
        }
    elif situation == "CANCEL_BATCH":
        response = {
            "mode": "PRICE_COMPARE", "left_source": "ORDER_PRICE", "right_source": "CURRENT_PRICE",
            "direction": "UP", "ratio_percent": 1.0, "comparator": ">=", "action": "CANCEL_BATCH",
        }
    return {
        "scope": "SIGNAL_SCOPED_BUY_CYCLE",
        "requires_source_signal": True,
        "autonomous_scheduler": False,
        "after_cycle_completion": "REQUIRE_NEW_BUY_SIGNAL",
        "order_policy": {
            "hoga_mode": hoga, "order_price_basis": "ORDER_PRICE" if hoga == "MULTI" else "CURRENT_PRICE",
            "hoga_up": 1 if hoga == "MULTI" else 0, "hoga_down": 1 if hoga == "MULTI" else 0,
        },
        "point_policy": point_policy,
        "situation_response": response,
        "execution_connected": situation != "CANCEL_BATCH",
        "execution_lock_reason": "" if situation != "CANCEL_BATCH" else "CYCLE_OPTION_EXECUTION_NOT_CONNECTED",
    }


class PreviousRoundPriceSkipConsumerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.helper = _BuyHelper()

    def rules(self, enabled: bool = True) -> dict:
        rules = self.helper._rules()
        rules["buy"]["execution"]["additional"] = _additional(skip=enabled)
        return rules

    def test_first_buy_is_not_applicable(self) -> None:
        self.assertEqual("READY", self.helper._build(rules=self.rules(), price=110)["status"])

    def test_match_skips_only_current_generation_without_round_effect(self) -> None:
        cycle = self.helper._cycle(1, last_confirmed_buy_order_price=100)
        result = self.helper._build(rules=self.rules(), cycle=cycle, price=110)
        self.assertEqual("BUY_GENERATION_SKIPPED_BY_PREVIOUS_ROUND_PRICE", result["reason"])
        self.assertEqual([], result["execution_intents"])
        self.assertFalse(result["decision"]["round_increment"])

    def test_non_match_and_new_price_re_evaluate(self) -> None:
        cycle = self.helper._cycle(1, last_confirmed_buy_order_price=100)
        self.assertEqual("READY", self.helper._build(rules=self.rules(), cycle=cycle, price=102)["status"])
        self.assertEqual(
            "BUY_GENERATION_SKIPPED_BY_PREVIOUS_ROUND_PRICE",
            self.helper._build(rules=self.rules(), cycle=cycle, price=110)["reason"],
        )

    def test_missing_previous_or_market_actionable_price_fails_closed(self) -> None:
        self.assertEqual(
            "PREVIOUS_ROUND_PRICE_UNAVAILABLE",
            self.helper._build(rules=self.rules(), cycle=self.helper._cycle(1), price=110)["reason"],
        )
        market = self.helper._rules(price_basis="MARKET")
        market["buy"]["execution"]["additional"] = _additional(skip=True)
        self.assertEqual(
            "PRICE_EVIDENCE_STALE",
            self.helper._build(rules=market, cycle=self.helper._cycle(1, last_confirmed_buy_order_price=100),
                               price=110, actionable_price=None)["reason"],
        )


class LastPlusOneConsumerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.helper = _BuyHelper()

    def rules(self, method: str) -> dict:
        rules = self.helper._rules(max_rounds=2)
        rules["buy"]["execution"]["additional"] = _additional(last=True, method=method)
        return rules

    def cycle(self, **overrides) -> dict:
        value = self.helper._cycle(
            2,
            last_normal_round_approved_budget=300,
            last_confirmed_buy_order_price=100,
            avg_price=90,
        )
        value.update(overrides)
        return value

    def test_requires_completed_normal_max_round_and_new_buy_signal(self) -> None:
        before = self.helper._build(rules=self.rules("MARKET"), cycle=self.helper._cycle(1), price=100)
        no_signal = bridge.build_indicator_follow_buy_intent(
            buy_signal_result={"signal": "HOLD"},
            context={"cycle": self.cycle(), "stock_config": {"trade_amount_type": "QUANTITY", "buy_qty": 1},
                     "rules": self.rules("MARKET"), "reference_price": 100, "actionable_current_price": 100},
        )
        self.assertEqual("READY", before["status"])
        self.assertEqual("BUY_SOURCE_SIGNAL_REQUIRED", no_signal["reason"])

    def test_market_and_current_price_reuse_budget_boundary(self) -> None:
        market = self.helper._build(rules=self.rules("MARKET"), cycle=self.cycle(), price=100)
        current = self.helper._build(rules=self.rules("CURRENT_PRICE"), cycle=self.cycle(), price=100)
        self.assertEqual("MARKET", market["execution_intent"]["hoga"])
        self.assertEqual("LIMIT", current["execution_intent"]["hoga"])
        for result in (market, current):
            intent = result["execution_intent"]
            self.assertEqual("LAST_PLUS_ONE", intent["generation_kind"])
            self.assertEqual(300, intent["budget"])
            self.assertEqual(3, intent["quantity"])
            self.assertEqual("LAST_NORMAL_ROUND_APPROVED_BUDGET", intent["budget_reference"])

    def test_active_condition_does_not_consume_occurrence_when_false(self) -> None:
        passed = self.helper._build(rules=self.rules("ACTIVE"), cycle=self.cycle(), price=100)
        failed = self.helper._build(rules=self.rules("ACTIVE"), cycle=self.cycle(avg_price=110), price=100)
        self.assertEqual("READY", passed["status"])
        self.assertEqual("LAST_PLUS_ONE_ACTIVE_CONDITION_NOT_MET", failed["reason"])
        self.assertFalse(failed["decision"]["occurrence_consumed"])

    def test_pending_and_completed_are_duplicate_safe(self) -> None:
        pending = self.helper._build(rules=self.rules("MARKET"), cycle=self.cycle(last_plus_one_pending=True), price=100)
        completed = self.helper._build(rules=self.rules("MARKET"), cycle=self.cycle(last_plus_one_completed=True), price=100)
        self.assertEqual("LAST_PLUS_ONE_ALREADY_PENDING", pending["reason"])
        self.assertEqual("BUY_ADDITIONAL_PROGRESS_COMPLETED", completed["reason"])

    def test_missing_budget_and_remaining_limit_fail_closed(self) -> None:
        missing = self.helper._build(rules=self.rules("MARKET"), cycle=self.helper._cycle(2), price=100)
        limited = self.helper._build(
            rules=self.rules("MARKET"), cycle=self.cycle(), price=100,
            config={"trade_amount_type": "QUANTITY", "buy_qty": 1, "buy_limit_enabled": True, "buy_limit_amount": 250},
        )
        self.assertEqual("LAST_NORMAL_ROUND_APPROVED_BUDGET_UNAVAILABLE", missing["reason"])
        self.assertEqual("ROUND_BUDGET_EXCEEDS_REMAINING_BUDGET", limited["reason"])


class LastRoundActiveConsumerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.helper = _BuyHelper()

    def rules(self, mode: str) -> dict:
        rules = self.helper._rules()
        base = rules["buy"]["execution"]["base"]
        base["last_round_active_buy"] = {
            "enabled": True, "applies_to": "LAST_MULTI_POINT_CHILD", "budget_policy_override": "NONE",
            "purpose": "BUY_METHOD_SPECIAL_ACTION", "subject": "AVERAGE_PRICE",
            "reference": "MULTI_POINT_SET_PRICE", "direction": "UP", "ratio_percent": 5, "comparator": ">=",
        }
        if mode == "MULTI_TIME":
            base.update(point_mode=mode, point_count=3, point_value=1, point_unit="SECOND",
                        point_range="INTERVAL", time_order_price_basis="CURRENT_PRICE")
        else:
            base.update(point_mode=mode, ratio_count=3, ratio_left="ORDER_PRICE", ratio_right="CURRENT_PRICE",
                        ratio_direction="UP", ratio_value=0.5, ratio_compare=">=")
        return rules

    def build(self, mode: str, avg: float) -> dict:
        return self.helper._build(
            rules=self.rules(mode), cycle=self.helper._cycle(
                1, avg_price=avg, base_filled_buy_amount=300,
                last_filled_buy_amount=300, cumulative_filled_buy_amount=300,
            ),
            config={"trade_amount_type": "QUANTITY", "buy_qty": 6}, price=100,
        )

    def test_time_and_ratio_gate_only_last_child(self) -> None:
        for mode in ("MULTI_TIME", "MULTI_RATIO"):
            with self.subTest(mode=mode):
                passed = self.build(mode, 110)
                skipped = self.build(mode, 90)
                self.assertEqual(3, len(passed["execution_intents"]))
                self.assertEqual(2, len(skipped["execution_intents"]))
                self.assertTrue(passed["execution_intents"][-1]["last_round_active_decision"]["matched"])
                self.assertFalse(skipped["execution_intents"][-1]["last_round_active_decision"]["matched"])
                self.assertEqual(
                    sum(item["budget"] for item in skipped["execution_intents"]),
                    skipped["execution_intents"][0]["multi_time_plan" if mode == "MULTI_TIME" else "multi_ratio_plan"]["planned_total_budget"],
                )

    def test_missing_average_and_non_multi_fail_closed(self) -> None:
        self.assertEqual(
            "BUY_LAST_ROUND_ACTIVE_AVERAGE_PRICE_UNAVAILABLE",
            self.build("MULTI_TIME", 0)["reason"],
        )
        rules = self.helper._rules()
        rules["buy"]["execution"]["base"]["last_round_active_buy"] = self.rules("MULTI_TIME")["buy"]["execution"]["base"]["last_round_active_buy"]
        self.assertEqual("BUY_LAST_ROUND_ACTIVE_REQUIRES_MULTI_POINT", self.helper._build(rules=rules)["reason"])


class SignalScopedCycleConsumerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.helper = _BuyHelper()

    def rules(self, **kwargs) -> dict:
        rules = self.helper._rules(price_basis="CURRENT_PRICE")
        rules["buy"]["execution"]["cycle"] = _cycle_policy(**kwargs)
        return rules

    def test_single_hoga_time_ratio_reuse_existing_planners(self) -> None:
        cases = (({}, "SINGLE", 1), ({"hoga": "MULTI"}, "MULTI_HOGA", 3),
                 ({"point": "MULTI_TIME"}, "MULTI_TIME", 3), ({"point": "MULTI_RATIO"}, "MULTI_RATIO", 3))
        for options, mode, count in cases:
            with self.subTest(mode=mode):
                result = self.helper._build(
                    rules=self.rules(**options), config={"trade_amount_type": "QUANTITY", "buy_qty": 3}, price=100,
                )
                self.assertEqual("READY", result["status"], result)
                self.assertEqual(count, len(result["execution_intents"]))
                self.assertEqual({mode}, {item.get("execution_mode", "SINGLE") for item in result["execution_intents"]})
                self.assertTrue(all(item["cycle_scope"] == "SIGNAL_SCOPED_BUY_CYCLE" for item in result["execution_intents"]))
                self.assertTrue(all(item["signal_scoped_cycle"]["autonomous_scheduler"] is False for item in result["execution_intents"]))

    def test_plan_is_deterministic_and_children_do_not_increment_round(self) -> None:
        first = self.helper._build(rules=self.rules(point="MULTI_TIME"),
                                   config={"trade_amount_type": "QUANTITY", "buy_qty": 3}, price=100)
        second = self.helper._build(rules=self.rules(point="MULTI_TIME"),
                                    config={"trade_amount_type": "QUANTITY", "buy_qty": 3}, price=100)
        self.assertEqual(first["execution_intents"], second["execution_intents"])
        self.assertEqual({1}, {item["buy_round"] for item in first["execution_intents"]})

    def test_cycle_situation_reuses_timeout_and_reset_and_blocks_unsupported(self) -> None:
        timeout = self.helper._build(rules=self.rules(), price=100)
        reset = self.helper._build(rules=self.rules(situation="RESET"), price=100)
        unsupported = self.helper._build(rules=self.rules(situation="CANCEL_BATCH"), price=100)
        self.assertEqual(5000, timeout["execution_intent"]["unfilled_timeout_policy"]["timeout_ms"])
        self.assertEqual("BUY_PRICE_CHANGE_RESET", reset["execution_intent"]["buy_price_reset_policy"]["policy"])
        self.assertEqual("CYCLE_OPTION_EXECUTION_NOT_CONNECTED", unsupported["reason"])

    def test_no_source_signal_means_no_plan(self) -> None:
        result = bridge.build_indicator_follow_buy_intent(
            buy_signal_result={"signal": "NONE"},
            context={"cycle": self.helper._cycle(), "stock_config": {"trade_amount_type": "QUANTITY", "buy_qty": 3},
                     "rules": self.rules(), "reference_price": 100, "actionable_current_price": 100},
        )
        self.assertEqual("BUY_SOURCE_SIGNAL_REQUIRED", result["reason"])


class ProjectionEvidenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.helper = _ProjectionHelper()

    def test_projection_exposes_confirmed_price_budget_and_last_plus_one_lifecycle(self) -> None:
        normal = self.helper._order("Q1")
        normal["execution_intent"].update(budget=200, price=100, cycle_identity="Q1")
        pending = self.helper._order("LP1", phase="REPEAT", round_value=2, status="PENDING")
        pending["execution_intent"].update(generation_kind="LAST_PLUS_ONE", cycle_identity="Q1", budget=200, price=100)
        projected = self.helper._project([normal, pending], [self.helper._fill("Q1", cumulative=2, price=100)], 2)
        self.assertEqual(100, projected["last_confirmed_buy_order_price"])
        self.assertEqual(200, projected["last_normal_round_approved_budget"])
        self.assertTrue(projected["last_plus_one_pending"])
        self.assertFalse(projected["last_plus_one_completed"])

        completed = deepcopy(pending)
        completed["status"] = "FILLED"
        projected = self.helper._project(
            [normal, completed],
            [self.helper._fill("Q1", cumulative=2, price=100),
             self.helper._fill("LP1", cumulative=1, price=105, timestamp="2026-08-07 10:00:00")],
            3,
        )
        self.assertFalse(projected["last_plus_one_pending"])
        self.assertTrue(projected["last_plus_one_completed"])
        self.assertEqual(1, projected["confirmed_buy_round"])

    def test_rejected_last_plus_one_is_not_completed_and_new_signal_can_replan(self) -> None:
        normal = self.helper._order("Q1")
        normal["execution_intent"].update(budget=200, price=100, cycle_identity="Q1")
        rejected = self.helper._order("LP1", phase="REPEAT", round_value=2, status="BROKER_REJECTED")
        rejected["execution_intent"].update(generation_kind="LAST_PLUS_ONE", cycle_identity="Q1", budget=200, price=100)
        projected = self.helper._project([normal, rejected], [self.helper._fill("Q1", cumulative=2, price=100)], 2)
        self.assertFalse(projected["last_plus_one_pending"])
        self.assertFalse(projected["last_plus_one_completed"])

        helper = _BuyHelper()
        rules = helper._rules(max_rounds=1)
        rules["buy"]["execution"]["additional"] = _additional(last=True, method="CURRENT_PRICE")
        projected["last_normal_round_approved_budget"] = 200
        projected["last_confirmed_buy_order_price"] = 100
        result = helper._build(rules=rules, cycle=projected, price=100)
        self.assertEqual("READY", result["status"])
        self.assertEqual("LAST_PLUS_ONE", result["execution_intent"]["generation_kind"])


if __name__ == "__main__":
    unittest.main()
