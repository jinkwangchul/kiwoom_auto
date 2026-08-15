from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from account_auto_trade_budget_consumption import (
    project_account_auto_trade_budget_consumption,
)


class AccountAutoTradeBudgetConsumptionTests(unittest.TestCase):
    account_no = "12345678"

    @staticmethod
    def _write(path: Path, field: str, records: list[dict]) -> None:
        path.write_text(
            json.dumps({"version": 1, field: records}, ensure_ascii=False),
            encoding="utf-8",
        )

    def _position(self, *, code: str = "005930", cost_basis: int = 100_000_000) -> dict:
        return {
            "position_id": f"POSITION_{code}",
            "account_no": self.account_no,
            "code": code,
            "quantity": 100,
            "average_price": cost_basis // 100,
            "cost_basis": cost_basis,
            "position_status": "OPEN",
        }

    def _order(
        self,
        *,
        code: str = "005930",
        side: str = "BUY",
        status: str = "BROKER_ACCEPTED",
        quantity: int = 10,
        price: int = 5_000_000,
        remaining_quantity: int | None = None,
        broker_order_no: str = "BROKER-1",
    ) -> dict:
        record = {
            "id": f"ORDER_QUEUED_{broker_order_no}",
            "status": status,
            "source_signal_id": f"SIGNAL_{broker_order_no}",
            "broker_order_no": broker_order_no,
            "execution_request": {
                "guard_snapshot": {"account_no": self.account_no},
                "request_preview": {
                    "account_no": self.account_no,
                    "side": side,
                    "code": code,
                    "quantity": quantity,
                    "price": price,
                    "hoga": "LIMIT",
                },
            },
        }
        if remaining_quantity is not None:
            record["remaining_quantity"] = remaining_quantity
            record["original_order_quantity"] = quantity
        return record

    def _project(self, positions: list[dict], orders: list[dict], *, codes=("005930",)) -> dict:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            positions_path = root / "positions.json"
            queue_path = root / "order_queue.json"
            self._write(positions_path, "positions", positions)
            self._write(queue_path, "orders", orders)
            return project_account_auto_trade_budget_consumption(
                account_no=self.account_no,
                positions_path=positions_path,
                order_queue_path=queue_path,
                recovery_complete=True,
                reconciled_stock_codes=codes,
            )

    def test_holdings_only_uses_position_cost_basis(self) -> None:
        result = self._project([self._position()], [])
        self.assertTrue(result["available"])
        self.assertEqual(100_000_000, result["holding_cost"])
        self.assertEqual(0, result["open_buy_reservation"])
        self.assertEqual(100_000_000, result["consumed_amount"])

    def test_valid_empty_sources_are_available_zero_consumption(self) -> None:
        result = self._project([], [], codes=())

        self.assertTrue(result["available"])
        self.assertEqual(0, result["holding_cost"])
        self.assertEqual(0, result["open_buy_reservation"])
        self.assertEqual(0, result["consumed_amount"])
        self.assertEqual(0, result["position_count"])
        self.assertEqual(0, result["open_buy_order_count"])
        self.assertEqual("", result["reason"])

    def test_missing_or_malformed_source_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            positions_path = root / "positions.json"
            queue_path = root / "order_queue.json"
            self._write(positions_path, "positions", [])
            missing = project_account_auto_trade_budget_consumption(
                account_no=self.account_no,
                positions_path=positions_path,
                order_queue_path=queue_path,
                recovery_complete=True,
                reconciled_stock_codes=(),
            )
            queue_path.write_text("{not-json", encoding="utf-8")
            malformed = project_account_auto_trade_budget_consumption(
                account_no=self.account_no,
                positions_path=positions_path,
                order_queue_path=queue_path,
                recovery_complete=True,
                reconciled_stock_codes=(),
            )

        self.assertFalse(missing["available"])
        self.assertFalse(malformed["available"])

    def test_open_position_without_cost_basis_fails_closed(self) -> None:
        position = self._position()
        position.pop("cost_basis")

        result = self._project([position], [])

        self.assertFalse(result["available"])
        self.assertIn("cost_basis", result["reason"])

    def test_open_buy_only_uses_unfilled_quantity_times_order_price(self) -> None:
        result = self._project([], [self._order(quantity=10, price=5_000_000)])
        self.assertTrue(result["available"])
        self.assertEqual(50_000_000, result["open_buy_reservation"])
        self.assertEqual(50_000_000, result["consumed_amount"])

    def test_partial_fill_counts_holding_and_only_remaining_buy_once(self) -> None:
        result = self._project(
            [self._position(cost_basis=25_000_000)],
            [
                self._order(
                    status="PARTIALLY_FILLED",
                    quantity=10,
                    remaining_quantity=5,
                    price=5_000_000,
                )
            ],
        )
        self.assertTrue(result["available"])
        self.assertEqual(25_000_000, result["holding_cost"])
        self.assertEqual(25_000_000, result["open_buy_reservation"])
        self.assertEqual(50_000_000, result["consumed_amount"])

    def test_sell_and_terminal_buy_orders_do_not_reserve_budget(self) -> None:
        orders = [
            self._order(side="SELL", broker_order_no="SELL-1"),
            self._order(status="FILLED", broker_order_no="FILLED-1"),
            self._order(status="CANCELLED", broker_order_no="CANCELLED-1"),
            self._order(status="BROKER_REJECTED", broker_order_no="REJECTED-1"),
        ]
        result = self._project([], orders)
        self.assertTrue(result["available"])
        self.assertEqual(0, result["consumed_amount"])

    def test_account_scope_excludes_other_account(self) -> None:
        other = self._position(cost_basis=99_000_000)
        other["account_no"] = "87654321"
        result = self._project([self._position(), other], [])
        self.assertTrue(result["available"])
        self.assertEqual(100_000_000, result["consumed_amount"])

    def test_unresolved_send_market_buy_and_unreconciled_stock_fail_closed(self) -> None:
        unresolved = self._project(
            [],
            [self._order(status="SEND_CALL_ACCEPTED")],
        )
        self.assertFalse(unresolved["available"])

        market = self._project([], [self._order(price=0)])
        self.assertFalse(market["available"])

        outside = self._project(
            [self._position(code="006400")],
            [],
            codes=("005930",),
        )
        self.assertFalse(outside["available"])

    def test_recovery_must_be_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            positions_path = root / "positions.json"
            queue_path = root / "order_queue.json"
            self._write(positions_path, "positions", [])
            self._write(queue_path, "orders", [])
            result = project_account_auto_trade_budget_consumption(
                account_no=self.account_no,
                positions_path=positions_path,
                order_queue_path=queue_path,
                recovery_complete=False,
                reconciled_stock_codes=(),
            )
        self.assertFalse(result["available"])


if __name__ == "__main__":
    unittest.main()
