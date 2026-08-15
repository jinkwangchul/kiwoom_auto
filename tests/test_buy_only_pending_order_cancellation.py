# -*- coding: utf-8 -*-

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from auto_trade_order_execution_boundary import (
    CANCEL_SIDE_SCOPE_BUY_ONLY,
    AutoTradeOrderExecutionBoundary,
    AutoTradeOrderExecutionContext,
    project_buy_only_cancel_readiness,
)
from operation_close_completion_evaluator import resolve_liquidation_holding_quantity


ACCOUNT = "81291234"
OTHER_ACCOUNT = "99999999"
CODE = "005930"
OTHER_CODE = "000660"
ROUTINE = "routine-instance-1"
DAY = "2026-08-15"
STARTED_AT = "2026-08-15 09:00:00"


def _order(
    identity: str,
    *,
    side: str = "BUY",
    status: str = "BROKER_ACCEPTED",
    remaining: int = 10,
    quantity: int = 10,
    account_no: str = ACCOUNT,
    code: str = CODE,
    routine: str = ROUTINE,
    action: str = "NEW",
) -> dict[str, object]:
    return {
        "id": f"ORDER_QUEUED_{identity}",
        "order_id": identity,
        "source_signal_id": f"SIGNAL_{identity}",
        "status": status,
        "broker_order_no": f"BROKER_{identity}",
        "remaining_quantity": remaining,
        "quantity": quantity,
        "account_no": account_no,
        "code": code,
        "side": side,
        "routine": routine,
        "created_at": "2026-08-15 09:30:00",
        "order_action": action,
    }


def _cancel_request(source: dict[str, object]) -> dict[str, object]:
    original_order_no = str(source["broker_order_no"])
    return {
        "id": f"ORDER_QUEUED_CANCEL_{source['order_id']}",
        "order_id": f"CANCEL_{source['order_id']}",
        "status": "ORDER_QUEUED",
        "account_no": source["account_no"],
        "code": source["code"],
        "side": source["side"],
        "order_action": "CANCEL",
        "original_order_effect_confirmed": False,
        "execution_request": {
            "request_preview": {
                "account_no": source["account_no"],
                "code": source["code"],
                "side": source["side"],
                "order_action": "CANCEL",
                "original_order_no": original_order_no,
            }
        },
    }


class BuyOnlyPendingOrderCancellationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.queue_path = self.root / "order_queue.json"
        self.executions_path = self.root / "order_executions.json"
        self.locks_path = self.root / "order_locks.json"
        context = AutoTradeOrderExecutionContext(
            kiwoom_connected=lambda: False,
            account_numbers=lambda: [ACCOUNT],
            selected_account_no=lambda: ACCOUNT,
            send_order_callable=lambda: None,
            selected_stock_info=lambda: None,
            selected_routine_metadata=lambda: None,
            selected_target_instance_ids=lambda: (),
            selected_routine_dir=lambda: None,
            routine_dirs=lambda: [],
            stock_dirs_in_routine=lambda _path: [],
            base_stocks=lambda: [],
            order_queue_path=lambda: self.queue_path,
            order_executions_path=lambda: self.executions_path,
            order_locks_path=lambda: self.locks_path,
        )
        self.boundary = AutoTradeOrderExecutionBoundary(context)
        self.boundary.send_order_for_order_queued_automatically = mock.Mock(
            return_value={
                "queue_result_recorded": True,
                "send_order_called": False,
            }
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_orders(self, orders: list[dict[str, object]]) -> None:
        self.queue_path.write_text(
            json.dumps(
                {"version": 1, "revision": 0, "updated_at": "", "orders": orders},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def _read_orders(self) -> list[dict[str, object]]:
        return json.loads(self.queue_path.read_text(encoding="utf-8"))["orders"]

    def _cancel_buy_only(self) -> dict[str, object]:
        return self.boundary.queue_pending_order_cancellations_for_stock_automatically(
            CODE,
            ROUTINE,
            trading_day=DAY,
            started_at=STARTED_AT,
            side_scope=CANCEL_SIDE_SCOPE_BUY_ONLY,
            account_no=ACCOUNT,
        )

    def test_buy_and_sell_together_queues_only_buy_and_preserves_sell_record(self) -> None:
        buy = _order("BUY_1")
        sell = _order("SELL_1", side="SELL")
        sell_before = deepcopy(sell)
        self._write_orders([buy, sell])

        result = self._cancel_buy_only()

        self.assertTrue(result["ok"], result)
        self.assertEqual(1, result["target_buy_order_count"])
        self.assertEqual(1, result["cancel_requested"])
        self.assertTrue(result["sell_untouched"])
        self.assertEqual(1, self.boundary.send_order_for_order_queued_automatically.call_count)
        saved = self._read_orders()
        self.assertEqual(sell_before, next(item for item in saved if item["order_id"] == "SELL_1"))
        cancel = next(item for item in saved if item.get("order_action") == "CANCEL")
        self.assertEqual("BUY", cancel["side"])
        self.assertEqual("BROKER_BUY_1", cancel["execution_request"]["request_preview"]["original_order_no"])

    def test_multiple_buy_orders_and_partial_fill_use_only_positive_remaining(self) -> None:
        partial = _order(
            "BUY_PARTIAL",
            status="PARTIALLY_FILLED",
            quantity=100,
            remaining=60,
        )
        second = _order("BUY_2", remaining=7, quantity=7)
        self._write_orders([partial, second])

        result = self._cancel_buy_only()

        self.assertTrue(result["ok"], result)
        self.assertEqual(2, result["target_buy_order_count"])
        self.assertEqual(2, result["cancel_requested"])
        self.assertEqual(67, result["remaining_buy_pending_quantity"])
        cancel_quantities = sorted(
            int(item["quantity"])
            for item in self._read_orders()
            if item.get("order_action") == "CANCEL"
        )
        self.assertEqual([7, 60], cancel_quantities)

    def test_zero_terminal_other_stock_and_other_account_are_not_targets(self) -> None:
        orders = [
            _order("ZERO", remaining=0),
            _order("FILLED", status="FILLED", remaining=0),
            _order("CANCELLED", status="CANCELLED", remaining=0),
            _order("REJECTED", status="BROKER_REJECTED", remaining=0),
            _order("OTHER_CODE", code=OTHER_CODE),
            _order("OTHER_ACCOUNT", account_no=OTHER_ACCOUNT),
            _order("SELL", side="SELL"),
        ]
        self._write_orders(orders)

        result = self._cancel_buy_only()

        self.assertTrue(result["ok"], result)
        self.assertEqual(0, result["target_buy_order_count"])
        self.assertEqual(0, result["cancel_requested"])
        self.boundary.send_order_for_order_queued_automatically.assert_not_called()

    def test_repeated_call_and_existing_cancel_request_are_idempotent(self) -> None:
        source = _order("BUY_1")
        self._write_orders([source])

        first = self._cancel_buy_only()
        second = self._cancel_buy_only()

        self.assertTrue(first["ok"], first)
        self.assertEqual(1, first["cancel_requested"])
        self.assertTrue(second["ok"], second)
        self.assertEqual(0, second["cancel_requested"])
        self.assertEqual(1, second["cancel_pending"])
        self.assertEqual(1, self.boundary.send_order_for_order_queued_automatically.call_count)

        self._write_orders([source, _cancel_request(source)])
        self.boundary.send_order_for_order_queued_automatically.reset_mock()
        pending = self._cancel_buy_only()
        self.assertTrue(pending["ok"], pending)
        self.assertEqual(1, pending["cancel_pending"])
        self.assertEqual(0, pending["cancel_requested"])
        self.boundary.send_order_for_order_queued_automatically.assert_not_called()

    def test_default_scope_preserves_legacy_buy_and_sell_behavior(self) -> None:
        self._write_orders([_order("BUY_1"), _order("SELL_1", side="SELL")])

        result = self.boundary.queue_pending_order_cancellations_for_stock_automatically(
            CODE,
            ROUTINE,
            trading_day=DAY,
            started_at=STARTED_AT,
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual("ALL", result["side_scope"])
        self.assertEqual(2, result["cancel_requested"])
        self.assertEqual(2, self.boundary.send_order_for_order_queued_automatically.call_count)

    def test_readiness_waits_for_chejan_then_accepts_zero_pending_quantity(self) -> None:
        source = _order(
            "BUY_PARTIAL",
            status="PARTIALLY_FILLED",
            quantity=100,
            remaining=60,
        )
        cancel = _cancel_request(source)
        waiting = project_buy_only_cancel_readiness(
            {"orders": [source, cancel]},
            account_no=ACCOUNT,
            stock_code=CODE,
        )
        self.assertTrue(waiting["available"], waiting)
        self.assertFalse(waiting["ready"])
        self.assertEqual("WAITING_BUY_CANCEL", waiting["state"])
        self.assertEqual(60, waiting["pending_buy_quantity"])
        self.assertEqual(1, waiting["cancel_request_in_progress_count"])

        source.update({"status": "PARTIAL_CANCELLED", "remaining_quantity": 0})
        cancel.update(
            {
                "status": "CANCELLED",
                "original_order_effect_confirmed": True,
            }
        )
        completed = project_buy_only_cancel_readiness(
            {"orders": [source, cancel]},
            account_no=ACCOUNT,
            stock_code=CODE,
        )
        self.assertTrue(completed["available"], completed)
        self.assertTrue(completed["ready"])
        self.assertEqual("READY_FOR_LIQUIDATION", completed["state"])
        self.assertEqual(0, completed["pending_buy_quantity"])

    def test_readiness_is_fail_closed_for_conflicting_or_unconfirmed_evidence(self) -> None:
        terminal_conflict = _order("BAD", status="FILLED", remaining=3)
        conflict = project_buy_only_cancel_readiness(
            {"orders": [terminal_conflict]},
            account_no=ACCOUNT,
            stock_code=CODE,
        )
        self.assertFalse(conflict["available"])
        self.assertEqual("BLOCKED_UNCERTAIN", conflict["state"])

        unconfirmed = _order("UNCONFIRMED", status="SEND_UNCERTAIN", remaining=3)
        uncertain = project_buy_only_cancel_readiness(
            {"orders": [unconfirmed]},
            account_no=ACCOUNT,
            stock_code=CODE,
        )
        self.assertFalse(uncertain["available"])
        self.assertEqual("BLOCKED_UNCERTAIN", uncertain["state"])

        orphan_cancel = project_buy_only_cancel_readiness(
            {"orders": [_cancel_request(_order("ORPHAN"))]},
            account_no=ACCOUNT,
            stock_code=CODE,
        )
        self.assertFalse(orphan_cancel["available"])
        self.assertEqual("BLOCKED_UNCERTAIN", orphan_cancel["state"])
        self.assertIn("linked original", orphan_cancel["reason"])

    def test_partial_fill_cancel_keeps_filled_holding_for_existing_resolver(self) -> None:
        source = _order(
            "BUY_PARTIAL",
            status="PARTIAL_CANCELLED",
            quantity=100,
            remaining=0,
        )
        readiness = project_buy_only_cancel_readiness(
            {"orders": [source]},
            account_no=ACCOUNT,
            stock_code=CODE,
        )
        self.assertTrue(readiness["ready"], readiness)

        positions = self.root / "positions.json"
        broker = self.root / "broker_holdings.json"
        positions.write_text(
            json.dumps({"positions": [{"code": CODE, "quantity": 40}]}),
            encoding="utf-8",
        )
        broker.write_text(
            json.dumps({"holdings": [{"code": CODE, "holding_quantity": 40}]}),
            encoding="utf-8",
        )
        holding = resolve_liquidation_holding_quantity(
            CODE,
            positions_path=positions,
            broker_holdings_path=broker,
        )
        self.assertTrue(holding["ok"], holding)
        self.assertEqual(40, holding["resolved_liquidation_qty"])


if __name__ == "__main__":
    unittest.main()
