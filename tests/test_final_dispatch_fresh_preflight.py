# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from datetime import date
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from auto_trade_order_execution_boundary import (
    AutoTradeOrderExecutionBoundary,
    AutoTradeOrderExecutionContext,
)
from production_recovery_contract import ACCOUNT_COMPLETED, STOCK_RESTORED


ACCOUNT = "12345678"
CODE = "003550"


class RecordingSendOrder:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def __call__(self, *args: object) -> int:
        self.calls.append(tuple(args))
        return 0


class FinalDispatchFreshPreflightTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.queue_path = self.root / "order_queue.json"
        self.executions_path = self.root / "order_executions.json"
        self.locks_path = self.root / "order_locks.json"
        self.positions_path = self.root / "positions.json"
        self.broker_holdings_path = self.root / "broker_holdings.json"
        self.stock_dir = self.root / f"{CODE}_LG"
        self.stock_dir.mkdir()
        (self.stock_dir / "config.json").write_text(
            json.dumps({"real_trade_enabled": True}, ensure_ascii=False),
            encoding="utf-8",
        )
        (self.stock_dir / "state.json").write_text(
            json.dumps(
                {
                    "status": "RUNNING",
                    "trade_enabled": True,
                    "real_trade_enabled": True,
                    "signal_probe_only": False,
                    "review_required": False,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.positions_path.write_text(
            json.dumps({"version": 1, "positions": []}, ensure_ascii=False),
            encoding="utf-8",
        )
        self.send_order = RecordingSendOrder()
        self.orderable_cash = 1_000_000
        self.boundary = AutoTradeOrderExecutionBoundary(
            AutoTradeOrderExecutionContext(
                kiwoom_connected=lambda: True,
                account_numbers=lambda: [ACCOUNT],
                selected_account_no=lambda: ACCOUNT,
                send_order_callable=lambda: self.send_order,
                selected_stock_info=lambda: (self.stock_dir, CODE, "LG"),
                selected_routine_metadata=lambda: None,
                selected_target_instance_ids=lambda: (),
                selected_routine_dir=lambda: None,
                routine_dirs=lambda: [],
                stock_dirs_in_routine=lambda _path: [],
                base_stocks=lambda: [],
                order_queue_path=lambda: self.queue_path,
                order_executions_path=lambda: self.executions_path,
                order_locks_path=lambda: self.locks_path,
                current_orderable_cash=lambda: self.orderable_cash,
                broker_holdings_path=lambda: self.broker_holdings_path,
                all_group_stock_dirs=lambda: [self.stock_dir],
            )
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _record(self, *, side: str, quantity: int = 10, amount: int = 10_000) -> dict[str, object]:
        return {
            "id": "ORDER_QUEUED_ORDER_1",
            "status": "ORDER_QUEUED",
            "source": "execution_queue_pending",
            "source_signal_id": "SIG_1",
            "order_id": "ORDER_1",
            "candidate_id": "CANDIDATE_1",
            "queue_pending_id": "PENDING_1",
            "execution_id": "EXEC_1",
            "request_hash": "a" * 64,
            "lock_id": "LOCK_1",
            "queue_contract_version": "preview-1",
            "send_order_called": False,
            "execution_enabled": False,
            "blocked_reasons": [],
            "account_no": ACCOUNT,
            "code": CODE,
            "side": side,
            "quantity": quantity,
            "amount": amount,
            "price": 1000,
            "order_type": "LIMIT",
            "execution_intent": {"budget": amount},
            "execution_request": {
                "execution_id": "EXEC_1",
                "order_id": "ORDER_1",
                "source_signal_id": "SIG_1",
                "lock_id": "LOCK_1",
                "request_hash": "a" * 64,
                "guard_snapshot": {"account_no": ACCOUNT},
                "request_preview": {
                    "account_no": ACCOUNT,
                    "screen_no": "0101",
                    "side": side,
                    "order_action": "NEW",
                    "code": CODE,
                    "quantity": quantity,
                    "price": 1000,
                    "hoga": "LIMIT",
                    "original_order_no": "",
                },
            },
        }

    def _write_queue(self, record: dict[str, object]) -> None:
        self.queue_path.write_text(
            json.dumps(
                {"version": 1, "revision": 0, "updated_at": "before", "orders": [record]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _write_broker_holding(self, *, holding: int, available: int) -> None:
        self.broker_holdings_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "updated_at": "now",
                    "holdings": [
                        {
                            "account_no": ACCOUNT,
                            "stock_code": CODE,
                            "holding_quantity": holding,
                            "available_quantity": available,
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _patch_recovery(self):
        identity = SimpleNamespace(account_no=ACCOUNT, trading_day=date.today().isoformat())
        snapshot = SimpleNamespace(
            account_status=ACCOUNT_COMPLETED,
            identity=identity,
            stocks=(
                SimpleNamespace(
                    stock_code=CODE,
                    stock_status=STOCK_RESTORED,
                    review_required=False,
                ),
            ),
        )
        return mock.patch(
            "auto_trade_order_execution_boundary.production_recovery_registry.snapshot",
            return_value=snapshot,
        )

    def test_stale_sell_quantity_blocks_before_send_order_without_rewrite(self) -> None:
        record = self._record(side="SELL", quantity=10)
        self._write_queue(record)
        self._write_broker_holding(holding=5, available=5)

        result = self.boundary.send_order_for_order_queued_automatically(record["id"])

        self.assertEqual("fresh_dispatch_preflight", result["stage"])
        self.assertIn("requested SELL exceeds current sellable quantity", result["blocked_reasons"])
        self.assertEqual([], self.send_order.calls)
        stored = json.loads(self.queue_path.read_text(encoding="utf-8"))["orders"][0]
        self.assertEqual(10, stored["quantity"])
        self.assertEqual("ORDER_QUEUED", stored["status"])

    def test_normal_sell_quantity_passes_to_fake_send_order_once(self) -> None:
        record = self._record(side="SELL", quantity=10)
        self._write_queue(record)
        self._write_broker_holding(holding=12, available=10)

        result = self.boundary.send_order_for_order_queued_automatically(record["id"])

        self.assertEqual("send_call_result_recorded", result["executor_stage"])
        self.assertEqual(1, len(self.send_order.calls))
        self.assertTrue(result["fresh_dispatch_preflight_result"]["fresh_dispatch_preflight"])

    def test_stale_buy_budget_blocks_before_send_order_without_rewrite(self) -> None:
        record = self._record(side="BUY", quantity=10, amount=10_000)
        self._write_queue(record)
        self.orderable_cash = 9_999
        with self._patch_recovery():
            result = self.boundary.send_order_for_order_queued_automatically(record["id"])

        self.assertEqual("fresh_dispatch_preflight", result["stage"])
        self.assertIn("requested BUY exposure exceeds current orderable amount", result["blocked_reasons"])
        self.assertEqual([], self.send_order.calls)
        stored = json.loads(self.queue_path.read_text(encoding="utf-8"))["orders"][0]
        self.assertEqual(10, stored["quantity"])
        self.assertEqual(10_000, stored["amount"])
        self.assertEqual("ORDER_QUEUED", stored["status"])

    def test_normal_buy_budget_passes_to_fake_send_order_once(self) -> None:
        record = self._record(side="BUY", quantity=10, amount=10_000)
        self._write_queue(record)
        self.orderable_cash = 20_000
        with self._patch_recovery(), mock.patch(
            "auto_trade_order_execution_boundary.read_system_total_budget_for_recalculation",
            return_value=100_000,
        ):
            result = self.boundary.send_order_for_order_queued_automatically(record["id"])

        self.assertEqual("send_call_result_recorded", result["executor_stage"])
        self.assertEqual(1, len(self.send_order.calls))
        self.assertTrue(result["fresh_dispatch_preflight_result"]["fresh_dispatch_preflight"])


if __name__ == "__main__":
    unittest.main()
