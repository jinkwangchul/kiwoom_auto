# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest

from execution_price_reset import inspect_buy_price_resets
from execution_provenance_contract import materialize_execution_intent_children


ACCOUNT = "81291234"
CODE = "005930"
SIGNAL = "BUY-RESET-SIGNAL"
PROCESS = "BUY-RESET-PROCESS"


class BuyPriceResetProductionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.queue = root / "order_queue.json"
        self.executions = root / "order_executions.json"
        self.fills = root / "fills.json"
        self.positions = root / "positions.json"
        self.holdings = root / "broker_holdings.json"
        self.signals = root / "routine_signals.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self, path: Path, field: str, values: list[dict[str, object]]) -> None:
        path.write_text(json.dumps({"version": 1, field: values}), encoding="utf-8")

    def intents(self, mode: str = "SINGLE_ORDER") -> list[dict[str, object]]:
        policy = {
            "policy": "BUY_PRICE_CHANGE_RESET", "enabled": True, "action": "RESET",
            "left_source": "ORDER_PRICE", "right_source": "CURRENT_PRICE",
            "direction": "UP", "compare": ">=", "threshold_percent": 5,
        }
        intent = {
            "side": "BUY", "execution_mode": mode, "hoga": "LIMIT",
            "price_basis": "ORDER_PRICE", "price": 100, "quantity": 10,
            "budget": 1000, "buy_round": 1, "plan_generation": 0,
            "source_signal_id": SIGNAL, "execution_process_id": PROCESS,
            "option_snapshot_hash": "OPTION", "buy_price_reset_policy": policy,
            "routine_type": "INDICATOR_FOLLOW", "routine_instance_id": "I1",
            "child_sequence_index": 1, "child_sequence_total": 1,
            "child_kind": "SINGLE_ORDER", "child_plan": {"planned_quantity": 10, "planned_price": 100},
        }
        return materialize_execution_intent_children([intent], source_signal_id=SIGNAL,
            execution_process_id=PROCESS, plan_generation_value=0)

    def fixture(self, *, status: str = "CANCELLED", remaining: int = 0,
                fills: list[dict[str, object]] | None = None,
                cancel: dict[str, object] | None = None,
                current_price: int | None = 105) -> dict[str, object]:
        intents = self.intents()
        intent = deepcopy(intents[0])
        order = {
            "id": "ORDER-1", "order_id": "ORDER-1", "status": status,
            "order_action": "NEW", "side": "BUY", "broker_order_no": "BROKER-1",
            "remaining_quantity": remaining, "quantity": 10, "budget": 1000,
            "account_no": ACCOUNT, "code": CODE, "source_signal_id": SIGNAL,
            "execution_process_id": PROCESS, "execution_id": intent["execution_id"],
            "plan_generation": 0, "option_snapshot_hash": "OPTION",
            "execution_intent": deepcopy(intent), "updated_at": "2026-09-03T10:00:00",
        }
        orders = [order] + ([cancel] if cancel else [])
        self.write(self.queue, "orders", orders)
        self.write(self.executions, "executions", [{"execution_id": intent["execution_id"],
            "execution_process_id": PROCESS, "plan_generation": 0}])
        with self.executions.open("a", encoding="utf-8") as handle:
            # Keep one JSON document containing both runtime collections.
            handle.seek(0)
        data = json.loads(self.executions.read_text(encoding="utf-8"))
        data["processes"] = [{"execution_process_id": PROCESS, "option_snapshot_hash": "OPTION"}]
        self.executions.write_text(json.dumps(data), encoding="utf-8")
        self.write(self.fills, "fills", fills or [])
        self.write(self.positions, "positions", [{"account_no": ACCOUNT, "code": CODE,
            "quantity": 4, "average_price": 95, "updated_at": "2026-09-03T10:02:00"}])
        self.write(self.holdings, "holdings", [{"account_no": ACCOUNT, "code": CODE,
            "holding_quantity": 4, "available_quantity": 4, "received_at": "2026-09-03T10:02:00",
            "reconciliation_status": "CONSISTENT"}])
        self.write(self.signals, "signals", [{"id": SIGNAL, "code": CODE, "name": "테스트",
            "signal": "BUY", "execution_intent": deepcopy(intent), "execution_intents": deepcopy(intents)}])
        return {"prices": {CODE: current_price} if current_price is not None else {}}

    def inspect(self, **kwargs: object) -> dict[str, object]:
        values = self.fixture(**kwargs)
        return inspect_buy_price_resets(
            selected_account_no=ACCOUNT, allowed_stock_codes=[CODE],
            actionable_prices_by_code=values["prices"],
            now=datetime.fromisoformat("2026-09-03T10:03:00"),
            order_queue_path=self.queue, order_executions_path=self.executions,
            fills_path=self.fills, positions_path=self.positions,
            holdings_path=self.holdings, signals_path=self.signals,
        )

    def test_threshold_not_met_and_exact_threshold(self) -> None:
        self.assertFalse(self.inspect(current_price=104)["replan_proposals"])
        self.assertEqual("BUY_PRICE_RESPONSE_THRESHOLD_NOT_MET", self.inspect(current_price=104)["waiting"][0]["reason"])
        self.assertEqual(1, len(self.inspect(current_price=105)["replan_proposals"]))

    def test_two_triggered_slots_with_different_actions_use_existing_fail_closed_contract(self) -> None:
        values = self.fixture(current_price=105)
        policies = [
            {
                "slot": "SETTING1", "enabled": True,
                "left_source": "ORDER_PRICE", "right_source": "CURRENT_PRICE",
                "direction": "UP", "compare": ">=", "threshold_percent": 1,
                "action": "RESET",
            },
            {
                "slot": "SETTING2", "enabled": True,
                "left_source": "ORDER_PRICE", "right_source": "CURRENT_PRICE",
                "direction": "UP", "compare": ">=", "threshold_percent": 3,
                "action": "CANCEL_BATCH",
            },
        ]
        queue = json.loads(self.queue.read_text(encoding="utf-8"))
        queue["orders"][0]["execution_intent"]["buy_price_response_policies"] = deepcopy(
            policies
        )
        self.queue.write_text(json.dumps(queue), encoding="utf-8")
        signals = json.loads(self.signals.read_text(encoding="utf-8"))
        signals["signals"][0]["execution_intent"]["buy_price_response_policies"] = deepcopy(
            policies
        )
        signals["signals"][0]["execution_intents"][0][
            "buy_price_response_policies"
        ] = deepcopy(policies)
        self.signals.write_text(json.dumps(signals), encoding="utf-8")

        result = inspect_buy_price_resets(
            selected_account_no=ACCOUNT,
            allowed_stock_codes=[CODE],
            actionable_prices_by_code=values["prices"],
            now=datetime.fromisoformat("2026-09-03T10:03:00"),
            order_queue_path=self.queue,
            order_executions_path=self.executions,
            fills_path=self.fills,
            positions_path=self.positions,
            holdings_path=self.holdings,
            signals_path=self.signals,
        )

        self.assertFalse(result["cancel_proposals"])
        self.assertFalse(result["replan_proposals"])
        self.assertEqual(
            ["BUY_PRICE_RESPONSE_MULTIPLE_SLOTS_TRIGGERED"],
            result["reviews"][0]["review_reasons"],
        )

    def test_open_buy_requires_cancel_first(self) -> None:
        result = self.inspect(status="PARTIALLY_FILLED", remaining=6)
        self.assertEqual(1, len(result["cancel_proposals"]))
        self.assertEqual("BUY", result["cancel_proposals"][0]["side"])
        self.assertEqual(6, result["cancel_proposals"][0]["remaining_quantity"])
        self.assertFalse(result["replan_proposals"])

    def test_cancel_effect_replans_same_round_and_next_generation(self) -> None:
        cancel = {"id": "CANCEL-1", "status": "CANCELLED", "order_action": "CANCEL",
                  "execution_process_id": PROCESS, "original_order_no": "BROKER-1",
                  "cancel_evidence": {"trigger": "BUY_PRICE_CHANGE_RESET", "source_plan_generation": 0}}
        result = self.inspect(status="CANCELLED", remaining=0, cancel=cancel,
                              fills=[{"execution_id": self.intents()[0]["execution_id"],
                                      "filled_quantity": 4, "filled_price": 100}])
        proposal = result["replan_proposals"][0]
        self.assertEqual(1, proposal["plan_generation"])
        self.assertEqual(1, proposal["buy_round"])
        self.assertEqual(SIGNAL, proposal["source_signal_id"])
        self.assertEqual(6, proposal["execution_intents"][0]["quantity"])

    def test_missing_current_price_is_fail_closed(self) -> None:
        result = self.inspect(current_price=None)
        self.assertFalse(result["replan_proposals"])
        self.assertEqual("BUY_PRICE_RESET_CURRENT_PRICE_UNAVAILABLE", result["waiting"][0]["reason"])

    def test_higher_priority_exit_block_prevents_reset_even_at_threshold(self) -> None:
        values = self.fixture(current_price=105)
        result = inspect_buy_price_resets(
            selected_account_no=ACCOUNT, allowed_stock_codes=[CODE],
            actionable_prices_by_code=values["prices"],
            blocked_execution_process_ids=[PROCESS],
            now=datetime.fromisoformat("2026-09-03T10:03:00"),
            order_queue_path=self.queue, order_executions_path=self.executions,
            fills_path=self.fills, positions_path=self.positions,
            holdings_path=self.holdings, signals_path=self.signals,
        )
        self.assertFalse(result["cancel_proposals"])
        self.assertFalse(result["replan_proposals"])
        self.assertEqual([PROCESS], result["blocked_execution_process_ids"])
        self.assertEqual(
            "BUY_PRICE_RESET_BLOCKED_BY_HIGHER_PRIORITY_POLICY",
            result["waiting"][0]["reason"],
        )

    def test_recovery_origin_reset_restarts_base_plan_without_recovery_metadata(self) -> None:
        base_intent = deepcopy(self.intents()[0])
        base_intent["base_plan_marker"] = "ORIGINAL_BUY_SETTINGS"
        recovery_intent = deepcopy(base_intent)
        recovery_intent.update({
            "execution_id": "RECOVERY-EXECUTION-1",
            "plan_generation": 1,
            "quantity": 3,
            "budget": 300,
            "recovery_cycle": True,
            "recovery_generation": 1,
            "recovery_started_at": "2026-09-03T10:01:00",
            "confirmed_residual_quantity": 3,
            "recovery_source_snapshot_hash": "RECOVERY-SNAPSHOT",
            "recovery_source_original_order_nos": ["BROKER-BASE"],
            "recovery_source_cancel_ids": ["CANCEL-BASE"],
        })
        base_order = {
            "id": "ORDER-BASE", "status": "CANCELLED", "order_action": "NEW",
            "side": "BUY", "broker_order_no": "BROKER-BASE", "remaining_quantity": 0,
            "quantity": 10, "budget": 1000, "account_no": ACCOUNT, "code": CODE,
            "source_signal_id": SIGNAL, "execution_process_id": PROCESS,
            "execution_id": base_intent["execution_id"], "plan_generation": 0,
            "option_snapshot_hash": "OPTION", "execution_intent": base_intent,
            "updated_at": "2026-09-03T10:00:30",
        }
        recovery_order = {
            "id": "ORDER-RECOVERY", "status": "CANCELLED", "order_action": "NEW",
            "side": "BUY", "broker_order_no": "BROKER-RECOVERY", "remaining_quantity": 0,
            "quantity": 3, "budget": 300, "account_no": ACCOUNT, "code": CODE,
            "source_signal_id": SIGNAL, "execution_process_id": PROCESS,
            "execution_id": recovery_intent["execution_id"], "plan_generation": 1,
            "option_snapshot_hash": "OPTION", "execution_intent": recovery_intent,
            "updated_at": "2026-09-03T10:01:30",
        }
        reset_cancel = {
            "id": "CANCEL-RECOVERY", "status": "CANCELLED", "order_action": "CANCEL",
            "execution_process_id": PROCESS, "original_order_no": "BROKER-RECOVERY",
            "original_order_effect_confirmed": True,
            "cancel_evidence": {
                "trigger": "BUY_PRICE_CHANGE_RESET", "source_plan_generation": 1,
            },
            "updated_at": "2026-09-03T10:02:00",
        }
        self.write(self.queue, "orders", [base_order, recovery_order, reset_cancel])
        self.executions.write_text(json.dumps({
            "version": 1,
            "executions": [
                {"execution_id": base_intent["execution_id"], "execution_process_id": PROCESS, "plan_generation": 0},
                {"execution_id": recovery_intent["execution_id"], "execution_process_id": PROCESS, "plan_generation": 1},
            ],
            "processes": [{"execution_process_id": PROCESS, "option_snapshot_hash": "OPTION"}],
        }), encoding="utf-8")
        self.write(self.fills, "fills", [
            {"execution_id": base_intent["execution_id"], "execution_process_id": PROCESS,
             "filled_quantity": 4, "filled_price": 100},
            {"execution_id": recovery_intent["execution_id"], "execution_process_id": PROCESS,
             "filled_quantity": 1, "filled_price": 100},
        ])
        self.write(self.positions, "positions", [{
            "account_no": ACCOUNT, "code": CODE, "quantity": 5,
            "average_price": 100, "updated_at": "2026-09-03T10:02:30",
        }])
        self.write(self.holdings, "holdings", [{
            "account_no": ACCOUNT, "code": CODE, "holding_quantity": 5,
            "available_quantity": 5, "received_at": "2026-09-03T10:02:30",
            "reconciliation_status": "CONSISTENT",
        }])
        self.write(self.signals, "signals", [{
            "id": SIGNAL, "code": CODE, "name": "테스트", "signal": "BUY",
            "execution_intent": recovery_intent, "execution_intents": [recovery_intent],
        }])

        result = inspect_buy_price_resets(
            selected_account_no=ACCOUNT, allowed_stock_codes=[CODE],
            actionable_prices_by_code={CODE: 105},
            now=datetime.fromisoformat("2026-09-03T10:03:00"),
            order_queue_path=self.queue, order_executions_path=self.executions,
            fills_path=self.fills, positions_path=self.positions,
            holdings_path=self.holdings, signals_path=self.signals,
        )

        self.assertEqual([], result["reviews"], result)
        proposal = result["replan_proposals"][0]
        restarted = proposal["execution_intents"][0]
        self.assertEqual(2, proposal["plan_generation"])
        self.assertEqual(1, proposal["buy_round"])
        self.assertEqual(SIGNAL, restarted["source_signal_id"])
        self.assertEqual(PROCESS, restarted["execution_process_id"])
        self.assertEqual("ORIGINAL_BUY_SETTINGS", restarted["base_plan_marker"])
        self.assertEqual(5, restarted["quantity"])
        for field in (
            "recovery_cycle", "recovery_generation", "recovery_started_at",
            "confirmed_residual_quantity", "recovery_source_snapshot_hash",
            "recovery_source_original_order_nos", "recovery_source_cancel_ids",
        ):
            self.assertNotIn(field, restarted)


if __name__ == "__main__":
    unittest.main()
