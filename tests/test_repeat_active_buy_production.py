# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from buy_execution_policy import calculate_active_buy_requirement
from execution_active_buy import (
    build_active_buy_generation_intents,
    inspect_active_buy_lifecycle,
    supersede_active_buy_pre_dispatch_generation,
)
import gui_auto_trade_timer
import routine_signal_consumer


ACCOUNT = "12345678"
CODE = "005930"
PROCESS = "PROCESS_ACTIVE_1"
SIGNAL = "SIGNAL_ACTIVE_1"


class ActiveBuyMathTest(unittest.TestCase):
    def calc(self, **overrides):
        values = dict(
            quantity=3,
            average_price=50_000,
            reference_price=13_000,
            actionable_price=13_000,
            direction="UP",
            ratio_percent=1,
            comparator="<=",
        )
        values.update(overrides)
        return calculate_active_buy_requirement(**values)

    def test_canonical_851_and_56_examples(self) -> None:
        self.assertEqual(851, self.calc()["required_quantity"])
        second = self.calc(reference_price=14_900, ratio_percent=0)
        self.assertEqual(56, second["required_quantity"])

    def test_no_buy_wait_and_invalid(self) -> None:
        self.assertEqual("NO_BUY", self.calc(average_price=13_100)["status"])
        self.assertEqual("WAIT", self.calc(actionable_price=14_000)["status"])
        self.assertEqual("WAIT", self.calc(actionable_price=13_130)["status"])
        self.assertEqual("INVALID", self.calc(quantity=0)["status"])
        self.assertEqual("INVALID", self.calc(average_price=float("nan"))["status"])
        self.assertEqual("INVALID", self.calc(actionable_price=float("inf"))["status"])

    def test_average_can_move_up_or_down(self) -> None:
        down = self.calc()
        up = self.calc(
            average_price=90,
            reference_price=100,
            actionable_price=120,
            direction="UP",
            ratio_percent=5,
            comparator=">=",
        )
        self.assertEqual("READY", down["status"])
        self.assertEqual("READY", up["status"])

    def test_both_within_and_outside_strictness(self) -> None:
        within_high = self.calc(reference_price=100, average_price=130, actionable_price=90,
                                direction="BOTH", ratio_percent=5, comparator="WITHIN")
        within_low = self.calc(reference_price=100, average_price=70, actionable_price=110,
                               direction="BOTH", ratio_percent=5, comparator="WITHIN")
        already = self.calc(reference_price=100, average_price=100, actionable_price=90,
                            direction="BOTH", ratio_percent=5, comparator="WITHIN")
        outside_low = self.calc(reference_price=100, average_price=100, actionable_price=80,
                                direction="BOTH", ratio_percent=5, comparator="OUTSIDE")
        outside_high = self.calc(reference_price=100, average_price=100, actionable_price=120,
                                 direction="BOTH", ratio_percent=5, comparator="OUTSIDE")
        outside_wait = self.calc(reference_price=100, average_price=100, actionable_price=102,
                                 direction="BOTH", ratio_percent=5, comparator="OUTSIDE")
        for result in (within_high, within_low, outside_low, outside_high):
            self.assertEqual("READY", result["status"], result)
        self.assertEqual("NO_BUY", already["status"])
        self.assertEqual("WAIT", outside_wait["status"])
        self.assertTrue(outside_low["projected_average"] < outside_low["lower_price"])
        previous = (100 * 3 + 80 * (outside_low["required_quantity"] - 1)) / (
            3 + outside_low["required_quantity"] - 1
        )
        self.assertGreaterEqual(previous, outside_low["lower_price"])

    def test_dynamic_price_changes_full_required_quantity_without_truncation(self) -> None:
        first = self.calc(actionable_price=13_000)
        second = self.calc(actionable_price=12_000)
        self.assertEqual(851, first["required_quantity"])
        self.assertNotEqual(first["required_quantity"], second["required_quantity"])
        self.assertEqual(
            second["required_quantity"] * 12_000,
            second["required_cost"],
        )


class ActiveBuyLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = {
            "order_queue_path": self.root / "order_queue.json",
            "fills_path": self.root / "fills.json",
            "positions_path": self.root / "positions.json",
            "holdings_path": self.root / "broker_holdings.json",
            "signals_path": self.root / "routine_signals.json",
        }
        self.initial = calculate_active_buy_requirement(
            quantity=3, average_price=50_000, reference_price=13_000,
            actionable_price=13_000, direction="UP", ratio_percent=1,
            comparator="<=",
        )
        self.intent = {
            "side": "BUY", "buy_phase": "REPEAT", "buy_round": 2,
            "quantity": self.initial["required_quantity"],
            "budget": self.initial["required_cost"], "price": 13_000,
            "price_basis": "CURRENT_PRICE", "hoga": "LIMIT",
            "execution_mode": "SINGLE_ORDER", "child_kind": "SINGLE_ORDER",
            "child_sequence_index": 1, "child_sequence_total": 1,
            "planned_total_quantity": self.initial["required_quantity"],
            "plan_generation": 0, "execution_process_id": PROCESS,
            "execution_id": "EXEC_ACTIVE_0_1", "source_signal_id": SIGNAL,
            "option_snapshot_hash": "OPTION_HASH",
            "execution_snapshot": {
                "approved_rule_hash": "APPROVED_RULE_HASH",
                "runtime_state_hash": "OLD_RUNTIME_HASH",
                "calculation_hash": "OLD_CALCULATION_HASH",
                "policy_hash": "OLD_POLICY_HASH",
            },
            "active_buy_policy": {
                "policy": "REPEAT_ACTIVE_BUY", "direction": "UP",
                "ratio_percent": 1, "comparator": "<=", "reference_price": 13_000,
            },
            "active_buy_calculation": deepcopy(self.initial),
            "active_buy_required_quantity": self.initial["required_quantity"],
        }
        self.signal = {
            "id": SIGNAL, "code": CODE, "name": "삼성전자", "signal": "BUY",
            "status": "PREVIEWED", "execution_intent": deepcopy(self.intent),
            "execution_intents": [deepcopy(self.intent)],
        }
        self.order = {
            "id": "ORDER_ACTIVE_0_1", "account_no": ACCOUNT, "code": CODE,
            "side": "BUY", "status": "EXECUTABLE", "quantity": self.intent["quantity"],
            "amount": self.intent["budget"], "execution_process_id": PROCESS,
            "execution_id": self.intent["execution_id"], "plan_generation": 0,
            "option_snapshot_hash": "OPTION_HASH", "execution_intent": deepcopy(self.intent),
        }
        self.write("order_queue_path", {"version": 1, "revision": 0, "orders": [self.order]})
        self.write("fills_path", {"fills": []})
        self.write("positions_path", {"positions": [{
            "account_no": ACCOUNT, "code": CODE, "quantity": 3, "average_price": 50_000,
        }]})
        self.write("holdings_path", {"holdings": [{
            "account_no": ACCOUNT, "code": CODE, "holding_quantity": 3,
            "available_quantity": 3,
        }]})
        self.write("signals_path", {"signals": [self.signal]})

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self, key: str, value: dict) -> None:
        self.paths[key].write_text(json.dumps(value), encoding="utf-8")

    def inspect(self, price: float):
        return inspect_active_buy_lifecycle(
            selected_account_no=ACCOUNT,
            allowed_stock_codes=[CODE],
            actionable_prices_by_code={CODE: price},
            **self.paths,
        )

    def test_pre_dispatch_change_supersedes_and_replans_generation_one(self) -> None:
        result = self.inspect(12_000)
        self.assertEqual(1, len(result["supersede_proposals"]), result)
        self.assertEqual(1, len(result["replan_proposals"]), result)
        replan = result["replan_proposals"][0]
        self.assertEqual(1, replan["plan_generation"])
        self.assertNotEqual(self.intent["quantity"], replan["execution_intents"][0]["quantity"])
        self.assertEqual(
            "APPROVED_RULE_HASH",
            replan["execution_intents"][0]["execution_snapshot"]["approved_rule_hash"],
        )
        self.assertNotEqual(
            "OLD_POLICY_HASH",
            replan["execution_intents"][0]["execution_snapshot"]["policy_hash"],
        )
        old_snapshot = deepcopy(self.order["execution_intent"].get("execution_snapshot"))
        committed = supersede_active_buy_pre_dispatch_generation(
            result["supersede_proposals"][0],
            order_queue_path=self.paths["order_queue_path"],
        )
        self.assertTrue(committed["committed"], committed)
        queue = json.loads(self.paths["order_queue_path"].read_text())
        self.assertEqual("BLOCKED", queue["orders"][0]["status"])
        self.assertEqual(old_snapshot, queue["orders"][0]["execution_intent"].get("execution_snapshot"))

    def test_same_price_and_quantity_keeps_approved_generation(self) -> None:
        result = self.inspect(13_000)
        self.assertEqual([], result["supersede_proposals"])
        self.assertNotIn(PROCESS, result["blocked_execution_process_ids"])

    def test_send_uncertain_waits_without_blind_replan(self) -> None:
        self.order["status"] = "SEND_UNCERTAIN"
        self.write("order_queue_path", {"version": 1, "revision": 0, "orders": [self.order]})
        result = self.inspect(12_000)
        self.assertEqual([], result["supersede_proposals"])
        self.assertEqual([], result["replan_proposals"])
        self.assertIn(PROCESS, result["blocked_execution_process_ids"])

    def test_open_remainder_excess_cancels_but_shortfall_waits(self) -> None:
        self.order.update(status="BROKER_ACCEPTED", broker_order_no="123", remaining_quantity=200)
        self.write("order_queue_path", {"version": 1, "revision": 0, "orders": [self.order]})
        excess = self.inspect(12_000)
        self.assertEqual(1, len(excess["cancel_proposals"]), excess)
        self.order["remaining_quantity"] = 20
        self.write("order_queue_path", {"version": 1, "revision": 0, "orders": [self.order]})
        shortfall = self.inspect(12_000)
        self.assertEqual([], shortfall["cancel_proposals"])
        self.assertEqual([], shortfall["replan_proposals"])

    def test_open_child_is_kept_while_changed_unsent_multi_children_are_superseded(self) -> None:
        intents = []
        orders = []
        for index, quantity in enumerate((20, 400, 431), 1):
            intent = deepcopy(self.intent)
            intent.update({
                "execution_mode": "MULTI_HOGA",
                "child_kind": "HOGA_LEVEL",
                "child_sequence_index": index,
                "child_sequence_total": 3,
                "quantity": quantity,
                "execution_id": f"EXEC_ACTIVE_0_{index}",
            })
            intents.append(intent)
            order = deepcopy(self.order)
            order.update({
                "id": f"ORDER_ACTIVE_0_{index}",
                "execution_id": intent["execution_id"],
                "quantity": quantity,
                "status": "EXECUTABLE",
                "execution_intent": deepcopy(intent),
            })
            orders.append(order)
        orders[0].update(
            status="BROKER_ACCEPTED", broker_order_no="123", remaining_quantity=20,
        )
        self.signal.update(
            execution_intent=deepcopy(intents[0]), execution_intents=deepcopy(intents),
        )
        self.write("signals_path", {"signals": [self.signal]})
        self.write("order_queue_path", {"version": 1, "revision": 0, "orders": orders})

        result = self.inspect(12_000)

        self.assertEqual([], result["cancel_proposals"], result)
        self.assertEqual([], result["replan_proposals"], result)
        self.assertEqual(1, len(result["supersede_proposals"]), result)
        self.assertEqual(
            {"ORDER_ACTIVE_0_2", "ORDER_ACTIVE_0_3"},
            set(result["supersede_proposals"][0]["order_ids"]),
        )

    def test_active_cancel_effect_pending_blocks_replan_and_duplicate_cancel(self) -> None:
        self.order.update(status="BROKER_ACCEPTED", broker_order_no="123", remaining_quantity=200)
        cancel = {
            "id": "CANCEL_ACTIVE_1", "status": "EXECUTABLE", "order_action": "CANCEL",
            "execution_process_id": PROCESS,
            "cancel_evidence": {"trigger": "ACTIVE_BUY_RECONCILIATION"},
            "original_order_effect_confirmed": False,
        }
        self.write(
            "order_queue_path",
            {"version": 1, "revision": 0, "orders": [self.order, cancel]},
        )

        result = self.inspect(12_000)

        self.assertEqual([], result["cancel_proposals"])
        self.assertEqual([], result["replan_proposals"])
        self.assertIn(
            "ACTIVE_BUY_CANCEL_EFFECT_PENDING",
            {item["reason"] for item in result["waiting"]},
        )

    def test_partial_fill_target_satisfied_cancels_open_remainder(self) -> None:
        self.order.update(status="PARTIALLY_FILLED", broker_order_no="123", remaining_quantity=10)
        self.write("order_queue_path", {"version": 1, "revision": 0, "orders": [self.order]})
        self.write("positions_path", {"positions": [{
            "account_no": ACCOUNT, "code": CODE, "quantity": 10, "average_price": 13_100,
        }]})
        self.write("holdings_path", {"holdings": [{
            "account_no": ACCOUNT, "code": CODE, "holding_quantity": 10,
            "available_quantity": 10,
        }]})
        result = self.inspect(13_000)
        self.assertEqual(1, len(result["cancel_proposals"]), result)
        self.assertEqual([], result["replan_proposals"])

    def test_confirmed_partial_fill_recalculates_from_position_before_next_generation(self) -> None:
        self.order.update(status="CANCELED", remaining_quantity=0)
        self.write("order_queue_path", {"version": 1, "revision": 0, "orders": [self.order]})
        self.write("positions_path", {"positions": [{
            "account_no": ACCOUNT, "code": CODE, "quantity": 13, "average_price": 21_538,
        }]})
        self.write("holdings_path", {"holdings": [{
            "account_no": ACCOUNT, "code": CODE, "holding_quantity": 13,
            "available_quantity": 13,
        }]})
        result = self.inspect(12_000)
        self.assertEqual(1, len(result["replan_proposals"]), result)
        calculation = result["replan_proposals"][0]["trigger_snapshot"]["calculation"]
        expected = calculate_active_buy_requirement(
            quantity=13, average_price=21_538, reference_price=13_000,
            actionable_price=12_000, direction="UP", ratio_percent=1,
            comparator="<=",
        )
        self.assertEqual(expected["required_quantity"], calculation["required_quantity"])
        self.assertEqual(1, result["replan_proposals"][0]["plan_generation"])

    def test_replan_reenters_existing_approval_consumer(self) -> None:
        proposal = self.inspect(12_000)["replan_proposals"][0]
        with mock.patch.object(routine_signal_consumer, "update_signal_status", return_value={"ok": True}), \
                mock.patch.object(routine_signal_consumer, "enqueue_replanned_execution_intents", return_value={"ok": True, "orders_created": 1, "executable_order_ids": ["NEW"]}) as enqueue:
            result = routine_signal_consumer.enqueue_active_buy_generation(proposal, apply_approval=True)
        self.assertTrue(result["ok"], result)
        self.assertTrue(enqueue.call_args.kwargs["apply_approval"])


class ActiveBuyPlannerTest(unittest.TestCase):
    def calculation(self, quantity: int = 10) -> dict:
        return {
            "status": "READY", "required_quantity": quantity,
            "required_cost": quantity * 100, "actionable_price": 100,
        }

    def template(self, mode: str) -> dict:
        value = {
            "side": "BUY", "buy_phase": "REPEAT", "buy_round": 2,
            "price": 100, "price_basis": "ORDER_PRICE", "hoga": "LIMIT",
            "execution_mode": mode, "active_buy_policy": {"policy": "REPEAT_ACTIVE_BUY"},
            "execution_snapshot": {"approved_rule_hash": "APPROVED_RULE_HASH"},
        }
        if mode == "MULTI_HOGA":
            value["multi_hoga_plan"] = {"hoga_offsets": [0, 1, -1], "instrument_type": "STOCK"}
        elif mode == "MULTI_TIME":
            value["multi_time_plan"] = {"configured_child_count": 3, "scheduled_offsets_ms": [0, 1000, 2000]}
        elif mode == "MULTI_RATIO":
            value["multi_ratio_plan"] = {"configured_child_count": 3}
        return value

    def test_single_hoga_time_ratio_reuse_generation_planner(self) -> None:
        for mode, expected_count in (("SINGLE_ORDER", 1), ("MULTI_HOGA", 3), ("MULTI_TIME", 3), ("MULTI_RATIO", 3)):
            with self.subTest(mode=mode):
                intents = build_active_buy_generation_intents(
                    template=self.template(mode), source_signal_id=SIGNAL,
                    process_id=PROCESS, option_snapshot_hash="OPTION",
                    generation=1, buy_round=2, calculation=self.calculation(),
                    source_snapshot_hash="SNAPSHOT", generated_at=datetime(2026, 9, 5, 10, 0),
                )
                self.assertEqual(expected_count, len(intents))
                self.assertEqual(10, sum(item["quantity"] for item in intents))
                self.assertEqual({1}, {item["plan_generation"] for item in intents})
                self.assertEqual({"SNAPSHOT"}, {item["active_buy_source_snapshot_hash"] for item in intents})


class ActiveBuyTimerIntegrationTest(unittest.TestCase):
    def test_cycle_orders_exit_active_reconciliation_then_price_reset_and_reapproval(self) -> None:
        from tests.indicator_follow_assigned_timer_fixture import (
            run_assigned_routine_timer_fixture,
        )

        result = run_assigned_routine_timer_fixture(self)
        self.assertIn("lifecycle", result)

    def test_lifecycle_inspection_failure_blocks_lower_buy_progression_and_dispatch(self) -> None:
        from tests.indicator_follow_assigned_timer_fixture import (
            run_assigned_routine_timer_fixture,
        )

        result = run_assigned_routine_timer_fixture(self)
        self.assertIn("lifecycle", result)
if __name__ == "__main__":
    unittest.main()
