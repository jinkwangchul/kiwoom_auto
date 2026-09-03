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
        self.assertEqual("BUY_PRICE_RESET_THRESHOLD_NOT_MET", self.inspect(current_price=104)["waiting"][0]["reason"])
        self.assertEqual(1, len(self.inspect(current_price=105)["replan_proposals"]))

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


if __name__ == "__main__":
    unittest.main()
