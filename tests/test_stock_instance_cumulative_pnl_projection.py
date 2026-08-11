from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import stock_instance_day_projection as projection


class StockInstanceCumulativePnlProjectionTests(unittest.TestCase):
    @staticmethod
    def _write(path: Path, data: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _fill(
        fill_id: str,
        *,
        side: str,
        quantity: int,
        price: int,
        order_no: str,
        received_at: str,
    ) -> dict[str, object]:
        return {
            "fill_id": fill_id,
            "execution_identity_source": "execution_no",
            "execution_identity": f"EXEC-{fill_id}",
            "broker_order_no": order_no,
            "account_no": "12345678",
            "code": "005930",
            "side": side,
            "filled_quantity": quantity,
            "filled_price": price,
            "received_at": received_at,
        }

    @staticmethod
    def _candle(close: int, bar_time: str = "2026-08-10T10:05:00+09:00") -> list[dict[str, object]]:
        return [{"bar_time": bar_time, "close": close}]

    def _write_open_position(self, root: Path, *, quantity: int, average: int) -> None:
        runtime = root / "runtime"
        self._write(
            runtime / "pnl_cycle_boundaries.json",
            {"version": 1, "boundaries": [{"stock_code": "005930", "boundary_id": "TEST-BOUNDARY", "boundary_at": "2026-08-10T09:00:00+09:00"}]},
        )
        self._write(
            runtime / "positions.json",
            {
                "positions": [
                    {
                        "account_no": "12345678",
                        "code": "005930",
                        "quantity": quantity,
                        "average_price": average if quantity else 0,
                        "cost_basis": quantity * average,
                    }
                ]
            },
        )
        if quantity:
            self._write(
                runtime / "broker_holdings.json",
                {
                    "holdings": [
                        {
                            "account_no": "12345678",
                            "code": "005930",
                            "holding_quantity": quantity,
                            "average_price": average,
                            "current_price": 999_999,
                        }
                    ]
                },
            )

    def test_partial_sell_splits_completed_and_open_cost_without_duplication(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = root / "runtime"
            buy = self._fill(
                "BUY-1",
                side="BUY",
                quantity=30,
                price=50_000,
                order_no="ORDER-BUY",
                received_at="2026-08-10T09:10:00+09:00",
            )
            sell = self._fill(
                "SELL-1",
                side="SELL",
                quantity=20,
                price=55_000,
                order_no="ORDER-SELL",
                received_at="2026-08-10T10:00:00+09:00",
            )
            self._write(runtime / "fills.json", {"fills": [buy, buy, sell]})
            self._write(
                runtime / "realized_pnl.json",
                {
                    "version": 1,
                    "realizations": [
                        {
                            "realization_id": "R1",
                            "trade_date": "2026-08-10",
                            "stock_code": "005930",
                            "routine_instance_id": "INSTANCE-A",
                            "gross_realized_profit": 100_000,
                        }
                    ],
                },
            )
            self._write_open_position(root, quantity=10, average=50_000)

            result = projection._cumulative_pnl_projection(
                root,
                "005930",
                "2026-08-10",
                self._candle(52_000),
                "INSTANCE-A",
            )
            ledger_data = json.loads((runtime / "realized_pnl.json").read_text(encoding="utf-8"))
            ledger_data["realizations"][0]["gross_realized_profit"] = 55_000
            self._write(runtime / "realized_pnl.json", ledger_data)
            five_percent = projection._cumulative_pnl_projection(
                root,
                "005930",
                "2026-08-10",
                self._candle(52_000),
                "INSTANCE-A",
            )

        self.assertTrue(result["pnl_available"])
        self.assertEqual(100_000, result["daily_realized_gross"])
        self.assertEqual(1_000_000, result["completed_buy_cost"])
        self.assertEqual(500_000, result["open_position_cost"])
        self.assertEqual(20_000, result["unrealized_pnl_at_bar_close"])
        self.assertEqual(120_000, result["cumulative_pnl"])
        self.assertAlmostEqual(8.0, result["cumulative_return_rate"])
        self.assertEqual(52_000, result["pnl_bar_close"])
        self.assertEqual(75_000, five_percent["cumulative_pnl"])
        self.assertAlmostEqual(5.0, five_percent["cumulative_return_rate"])

    def test_latest_completed_bar_close_not_broker_tick_drives_unrealized_loss(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = root / "runtime"
            self._write(
                runtime / "fills.json",
                {
                    "fills": [
                        self._fill(
                            "BUY-1",
                            side="BUY",
                            quantity=30,
                            price=50_000,
                            order_no="ORDER-BUY",
                            received_at="2026-08-10T09:10:00+09:00",
                        ),
                        self._fill(
                            "SELL-1",
                            side="SELL",
                            quantity=20,
                            price=55_000,
                            order_no="ORDER-SELL",
                            received_at="2026-08-10T10:00:00+09:00",
                        ),
                    ]
                },
            )
            self._write(
                runtime / "realized_pnl.json",
                {
                    "version": 1,
                    "realizations": [
                        {
                            "realization_id": "R1",
                            "trade_date": "2026-08-10",
                            "stock_code": "005930",
                            "gross_realized_profit": 100_000,
                        }
                    ],
                },
            )
            self._write_open_position(root, quantity=10, average=50_000)

            result = projection._cumulative_pnl_projection(
                root,
                "005930",
                "2026-08-10",
                self._candle(47_000),
                "INSTANCE-A",
            )

        self.assertEqual(-30_000, result["unrealized_pnl_at_bar_close"])
        self.assertEqual(70_000, result["cumulative_pnl"])
        self.assertEqual(47_000, result["pnl_bar_close"])

    def test_zero_cost_denominator_keeps_return_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write(root / "runtime" / "fills.json", {"fills": []})
            self._write_open_position(root, quantity=0, average=0)
            result = projection._cumulative_pnl_projection(
                root,
                "005930",
                "2026-08-10",
                self._candle(50_000),
                "INSTANCE-A",
            )

        self.assertTrue(result["pnl_available"])
        self.assertEqual(0, result["cumulative_pnl"])
        self.assertFalse(result["cumulative_return_available"])
        self.assertIsNone(result["cumulative_return_rate"])
        self.assertEqual("ZERO_COST_DENOMINATOR", result["pnl_unavailable_reason"])

    def test_full_exit_then_new_buy_moves_only_current_cost_back_to_open_position(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = root / "runtime"
            fills = [
                self._fill(
                    "BUY-1",
                    side="BUY",
                    quantity=30,
                    price=50_000,
                    order_no="ORDER-BUY-1",
                    received_at="2026-08-10T09:10:00+09:00",
                ),
                self._fill(
                    "SELL-1",
                    side="SELL",
                    quantity=30,
                    price=55_000,
                    order_no="ORDER-SELL-1",
                    received_at="2026-08-10T10:00:00+09:00",
                ),
            ]
            self._write(runtime / "fills.json", {"fills": fills})
            self._write(
                runtime / "realized_pnl.json",
                {
                    "version": 1,
                    "realizations": [
                        {
                            "realization_id": "R1",
                            "trade_date": "2026-08-10",
                            "stock_code": "005930",
                            "gross_realized_profit": 150_000,
                        }
                    ],
                },
            )
            self._write_open_position(root, quantity=0, average=0)
            exited = projection._cumulative_pnl_projection(
                root,
                "005930",
                "2026-08-10",
                self._candle(55_000),
                "INSTANCE-A",
            )

            fills.append(
                self._fill(
                    "BUY-2",
                    side="BUY",
                    quantity=10,
                    price=60_000,
                    order_no="ORDER-BUY-2",
                    received_at="2026-08-10T10:10:00+09:00",
                )
            )
            self._write(runtime / "fills.json", {"fills": fills})
            self._write_open_position(root, quantity=10, average=60_000)
            reopened = projection._cumulative_pnl_projection(
                root,
                "005930",
                "2026-08-10",
                self._candle(61_000, "2026-08-10T10:15:00+09:00"),
                "INSTANCE-A",
            )

        self.assertEqual(0, exited["open_position_cost"])
        self.assertEqual(0, exited["unrealized_pnl_at_bar_close"])
        self.assertEqual(150_000, exited["cumulative_pnl"])
        self.assertEqual(1_500_000, exited["completed_buy_cost"])
        self.assertEqual(600_000, reopened["open_position_cost"])
        self.assertEqual(10_000, reopened["unrealized_pnl_at_bar_close"])
        self.assertEqual(160_000, reopened["cumulative_pnl"])
        self.assertEqual(1_500_000, reopened["completed_buy_cost"])

    def test_bar_scoped_snapshot_skips_recalculation_until_new_completed_bar(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = {
                "pnl_available": True,
                "pnl_bar_time": "2026-08-10T10:00:00+09:00",
                "cumulative_pnl": 1_000,
            }
            second = {
                "pnl_available": True,
                "pnl_bar_time": "2026-08-10T10:05:00+09:00",
                "cumulative_pnl": 2_000,
            }
            with mock.patch.object(
                projection,
                "_cumulative_pnl_projection",
                side_effect=[first, second],
            ) as calculate:
                initial = projection._bar_scoped_cumulative_pnl_projection(
                    root,
                    "005930",
                    "2026-08-10",
                    self._candle(50_000, "2026-08-10T10:00:00+09:00"),
                    "INSTANCE-CACHE",
                )
                same_bar = projection._bar_scoped_cumulative_pnl_projection(
                    root,
                    "005930",
                    "2026-08-10",
                    self._candle(99_999, "2026-08-10T10:00:00+09:00"),
                    "INSTANCE-CACHE",
                )
                new_bar = projection._bar_scoped_cumulative_pnl_projection(
                    root,
                    "005930",
                    "2026-08-10",
                    self._candle(51_000, "2026-08-10T10:05:00+09:00"),
                    "INSTANCE-CACHE",
                )

        self.assertEqual(1_000, initial["cumulative_pnl"])
        self.assertEqual(1_000, same_bar["cumulative_pnl"])
        self.assertTrue(same_bar["pnl_snapshot_reused"])
        self.assertEqual(2_000, new_bar["cumulative_pnl"])
        self.assertEqual(2, calculate.call_count)


if __name__ == "__main__":
    unittest.main()
