from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from realized_pnl_ledger import (
    COST_BASIS_METHOD,
    project_daily_realized_pnl,
    read_realized_pnl_ledger,
    record_realized_pnl,
)


class RealizedPnlLedgerTests(unittest.TestCase):
    def _fill(self, **overrides: object) -> dict[str, object]:
        fill = {
            "fill_id": "FILL-SELL-1",
            "execution_identity_source": "execution_no",
            "execution_identity": "EXEC-1",
            "side": "SELL",
            "code": "005930",
            "filled_quantity": 10,
            "filled_price": 1_200,
            "broker_order_no": "BROKER-1",
            "order_id": "ORDER-1",
            "received_at": "2026-08-10T09:45:00+09:00",
            "normalized_event": {"name": "삼성전자"},
        }
        fill.update(overrides)
        return fill

    def _position(self, **overrides: object) -> dict[str, object]:
        result = {
            "position_updated": True,
            "position_stage": "position_updated_from_fill",
            "position_id": "POSITION-1",
            "positions_path": "runtime/positions.json",
            "fill_delta_applied": 10,
            "previous_average_price": 1_000,
        }
        result.update(overrides)
        return result

    @staticmethod
    def _order(**overrides: object) -> dict[str, object]:
        order = {
            "source_signal_id": "SIGNAL-SELL-1",
            "routine_provenance": {"routine_instance_id": "INSTANCE-A"},
        }
        order.update(overrides)
        return order

    @staticmethod
    def _context() -> dict[str, object]:
        return {
            "manual_realized_pnl_confirmed": True,
            "fills_path": "runtime/fills.json",
        }

    def _record(
        self,
        path: Path,
        *,
        fill: dict[str, object] | None = None,
        position: dict[str, object] | None = None,
        order: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return record_realized_pnl(
            fill or self._fill(),
            position or self._position(),
            order or self._order(),
            path,
            context=self._context(),
        )

    def test_single_full_sell_records_weighted_average_gross_and_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "realized_pnl.json"
            result = self._record(path)
            records = read_realized_pnl_ledger(path)["records"]

        self.assertTrue(result["realized_pnl_recorded"])
        self.assertEqual(1, len(records))
        record = records[0]
        self.assertEqual(10, record["sell_quantity"])
        self.assertEqual(10_000, record["matched_cost_basis"])
        self.assertEqual(2_000, record["gross_realized_profit"])
        self.assertEqual(2_000, record["cumulative_daily_gross_realized_profit"])
        self.assertEqual(COST_BASIS_METHOD, record["cost_basis_method"])
        self.assertEqual("INSTANCE-A", record["routine_instance_id"])
        self.assertTrue(record["realization_id"])

    def test_partial_sell_records_only_fill_delta(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "realized_pnl.json"
            result = self._record(
                path,
                fill=self._fill(filled_quantity=30),
                position=self._position(fill_delta_applied=30, previous_average_price=1_000),
            )

        record = result["realization_record"]
        self.assertEqual(30, record["sell_quantity"])
        self.assertEqual(30_000, record["matched_cost_basis"])
        self.assertEqual(6_000, record["gross_realized_profit"])

    def test_multiple_buy_average_cost_and_multiple_sells_accumulate_by_day(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "realized_pnl.json"
            first = self._record(
                path,
                fill=self._fill(fill_id="SELL-1", execution_identity="E1", filled_price=1_100),
                position=self._position(fill_delta_applied=15, previous_average_price=1_000),
            )
            second = self._record(
                path,
                fill=self._fill(fill_id="SELL-2", execution_identity="E2", filled_price=900, received_at="2026-08-10T11:10:00+09:00"),
                position=self._position(fill_delta_applied=5, previous_average_price=1_000),
            )
            projected = project_daily_realized_pnl("005930", "2026-08-10", ledger_path=path)

        self.assertEqual(1_500, first["realization_record"]["gross_realized_profit"])
        self.assertEqual(1_000, second["realization_record"]["cumulative_daily_gross_realized_profit"])
        self.assertEqual(1_000, projected["cumulative_daily_gross_realized_profit"])
        self.assertEqual(2, len(projected["records"]))

    def test_duplicate_and_restart_replay_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "realized_pnl.json"
            first = self._record(path)
            replay = record_realized_pnl(
                self._fill(),
                {"position_stage": "duplicate_fill"},
                self._order(),
                path,
                context=self._context(),
            )
            records = read_realized_pnl_ledger(path)["records"]

        self.assertTrue(first["changed"])
        self.assertTrue(replay["idempotent"])
        self.assertFalse(replay["changed"])
        self.assertEqual(1, len(records))

    def test_previous_day_carry_cost_is_used_and_missing_cost_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "realized_pnl.json"
            carry = self._record(
                path,
                position=self._position(previous_average_price=800, fill_delta_applied=10),
            )
            missing = self._record(
                path,
                fill=self._fill(fill_id="SELL-MISSING", execution_identity="E-MISSING"),
                position=self._position(previous_average_price=0),
            )

        self.assertEqual(8_000, carry["realization_record"]["matched_cost_basis"])
        self.assertFalse(missing["realized_pnl_recorded"])
        self.assertEqual("cost_basis", missing["realized_pnl_stage"])

    def test_actual_fee_and_tax_produce_net_but_missing_costs_keep_net_unconfirmed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with_cost_path = Path(temp) / "with_cost.json"
            missing_path = Path(temp) / "missing_cost.json"
            with_cost = self._record(
                with_cost_path,
                fill=self._fill(commission=100, tax=50),
            )["realization_record"]
            missing = self._record(missing_path)["realization_record"]

        self.assertTrue(with_cost["costs_available"])
        self.assertEqual(1_850, with_cost["net_realized_profit"])
        self.assertEqual(1_850, with_cost["cumulative_daily_realized_profit"])
        self.assertFalse(missing["costs_available"])
        self.assertIsNone(missing["net_realized_profit"])
        self.assertIsNone(missing["cumulative_daily_realized_profit"])

    def test_stock_and_trade_date_cumulative_values_are_independent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "realized_pnl.json"
            self._record(path)
            self._record(
                path,
                fill=self._fill(fill_id="OTHER", execution_identity="OTHER", code="000660", received_at="2026-08-10T10:00:00+09:00"),
            )
            self._record(
                path,
                fill=self._fill(fill_id="NEXT-DAY", execution_identity="NEXT", received_at="2026-08-11T10:00:00+09:00"),
            )
            samsung = project_daily_realized_pnl("005930", "2026-08-10", ledger_path=path)
            hynix = project_daily_realized_pnl("000660", "2026-08-10", ledger_path=path)
            next_day = project_daily_realized_pnl("005930", "2026-08-11", ledger_path=path)

        self.assertEqual(1, len(samsung["records"]))
        self.assertEqual(1, len(hynix["records"]))
        self.assertEqual(1, len(next_day["records"]))

    def test_buy_or_uncommitted_position_does_not_record_realization(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "realized_pnl.json"
            buy = self._record(path, fill=self._fill(side="BUY"))
            uncommitted = self._record(
                path,
                fill=self._fill(fill_id="UNCOMMITTED"),
                position=self._position(position_updated=False),
            )

        self.assertTrue(buy["not_applicable"])
        self.assertFalse(uncommitted["realized_pnl_recorded"])
        self.assertFalse(path.exists())

    def test_existing_runtime_inputs_are_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ledger = root / "realized_pnl.json"
            fills = root / "fills.json"
            positions = root / "positions.json"
            fills.write_text(json.dumps({"fills": [self._fill()]}), encoding="utf-8")
            positions.write_text(json.dumps({"positions": []}), encoding="utf-8")
            before = (fills.read_bytes(), positions.read_bytes())

            self._record(ledger)

            self.assertEqual(before, (fills.read_bytes(), positions.read_bytes()))

    def test_chejan_pipeline_records_each_sell_fill_delta(self) -> None:
        import gui_auto_trade_setting_window as gui

        def write_order(path: Path, *, side: str, broker_order_no: str) -> None:
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "updated_at": "2026-08-10 09:00:00",
                        "orders": [
                            {
                                "id": f"QUEUE-{side}",
                                "status": "SEND_CALL_ACCEPTED",
                                "order_id": f"ORDER-{side}",
                                "request_hash": f"HASH-{side}",
                                "lock_id": f"LOCK-{side}",
                                "execution_id": f"EXECUTION-{side}",
                                "broker_order_no": broker_order_no,
                                "account_no": "12345678",
                                "code": "005930",
                                "side": side,
                                "quantity": 10,
                                "send_order_called": True,
                                "send_order_result_status": "SEND_ORDER_CALLED",
                                "routine_provenance": {"routine_instance_id": "INSTANCE-A"},
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        def event(
            *,
            side: str,
            broker_order_no: str,
            execution_no: str,
            cumulative_quantity: int,
            remaining_quantity: int,
            price: int,
            received_at: str,
        ) -> dict[str, object]:
            return {
                "source": "kiwoom_chejan",
                "gubun": "0",
                "fid_values": {
                    "9201": "12345678",
                    "9203": broker_order_no,
                    "9001": "A005930",
                    "302": "삼성전자",
                    "907": "2" if side == "BUY" else "1",
                    "913": "FILLED",
                    "900": "10",
                    "911": str(cumulative_quantity),
                    "902": str(remaining_quantity),
                    "910": str(price),
                    "901": str(price),
                    "909": execution_no,
                },
                "received_at": received_at,
            }

        context = {
            "kiwoom_api_live_event": True,
            "live_event_source": "KiwoomApi.raw_chejan_received",
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            queue = root / "order_queue.json"
            fills = root / "fills.json"
            positions = root / "positions.json"
            ledger = root / "realized_pnl.json"
            with (
                mock.patch.object(gui, "ORDER_QUEUE_PATH", queue),
                mock.patch.object(gui, "FILLS_PATH", fills),
                mock.patch.object(gui, "POSITIONS_PATH", positions),
                mock.patch.object(gui, "REALIZED_PNL_LEDGER_PATH", ledger),
            ):
                write_order(queue, side="BUY", broker_order_no="BROKER-BUY")
                buy = gui.handle_kiwoom_raw_chejan_event(
                    event(
                        side="BUY",
                        broker_order_no="BROKER-BUY",
                        execution_no="BUY-EXEC-1",
                        cumulative_quantity=10,
                        remaining_quantity=0,
                        price=1_000,
                        received_at="2026-08-10T09:10:00+09:00",
                    ),
                    context,
                )
                write_order(queue, side="SELL", broker_order_no="BROKER-SELL")
                first_sell = gui.handle_kiwoom_raw_chejan_event(
                    event(
                        side="SELL",
                        broker_order_no="BROKER-SELL",
                        execution_no="SELL-EXEC-1",
                        cumulative_quantity=4,
                        remaining_quantity=6,
                        price=1_200,
                        received_at="2026-08-10T10:00:00+09:00",
                    ),
                    context,
                )
                second_sell = gui.handle_kiwoom_raw_chejan_event(
                    event(
                        side="SELL",
                        broker_order_no="BROKER-SELL",
                        execution_no="SELL-EXEC-2",
                        cumulative_quantity=7,
                        remaining_quantity=3,
                        price=1_200,
                        received_at="2026-08-10T10:05:00+09:00",
                    ),
                    context,
                )

            records = read_realized_pnl_ledger(ledger)["records"]
            position_data = json.loads(positions.read_text(encoding="utf-8"))

        self.assertTrue(buy["position_result"]["position_updated"], buy)
        self.assertNotIn("realized_pnl_result", buy)
        self.assertTrue(first_sell["realized_pnl_result"]["realized_pnl_recorded"], first_sell)
        self.assertTrue(second_sell["realized_pnl_result"]["realized_pnl_recorded"], second_sell)
        self.assertEqual([4, 3], [item["sell_quantity"] for item in records])
        self.assertEqual([800, 1_400], [item["cumulative_daily_gross_realized_profit"] for item in records])
        self.assertEqual("INSTANCE-A", records[-1]["routine_instance_id"])
        self.assertEqual(3, position_data["positions"][0]["quantity"])


if __name__ == "__main__":
    unittest.main()
