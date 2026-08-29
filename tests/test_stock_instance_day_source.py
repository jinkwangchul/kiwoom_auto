from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import auto_candle_refresh
from candle_manager import load_candles, save_candles
from candle_timeframe_aggregation import SEOUL_TIMEZONE, completed_timeframe_candles
import gui_auto_trade_timer
import kiwoom_candle_adapter
from market_evidence_store import market_window_hash
import routine_signal_order_bridge
import routine_signal_probe
import routine_signal_queue
import stock_instance_day_projection


def _raw_candles(count: int, *, day: str = "2026-08-10") -> list[dict[str, object]]:
    start = datetime.fromisoformat(f"{day}T09:00:00+09:00")
    return [
        {
            "time": (start + timedelta(minutes=index)).strftime("%Y%m%d%H%M%S"),
            "open": 100 + index,
            "high": 102 + index,
            "low": 99 + index,
            "close": 101 + index,
            "volume": 1000 + index,
        }
        for index in range(count)
    ]


def _opt10080_rows(candles: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "체결시간": candle["time"],
            "시가": candle["open"],
            "고가": candle["high"],
            "저가": candle["low"],
            "현재가": candle["close"],
            "거래량": candle["volume"],
        }
        for candle in reversed(candles)
    ]


class CandleDayPersistenceTests(unittest.TestCase):
    def test_current_day_merge_preserves_over_300_and_deduplicates_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_삼성전자"
            existing = _raw_candles(378)
            save_candles(stock_dir, existing, max_count=600)
            incoming = _raw_candles(381)[-4:]
            incoming[-1]["close"] = 999999
            repository = SimpleNamespace(resolve_stock_dir=lambda _code, _name="": stock_dir)

            with patch.object(kiwoom_candle_adapter, "StockRepository", return_value=repository):
                saved = kiwoom_candle_adapter.save_minute_candles_for_stock(
                    "005930",
                    "삼성전자",
                    _opt10080_rows(incoming),
                )

            self.assertEqual(len(saved), 381)
            self.assertEqual(len({item["time"] for item in saved}), 381)
            self.assertEqual(saved[-1]["close"], 999999)
            self.assertEqual(len(load_candles(stock_dir)), 381)
            projected = completed_timeframe_candles(
                saved,
                {"bar": {"bar_minutes": 5}},
                now=datetime(2026, 8, 10, 15, 21, tzinfo=SEOUL_TIMEZONE),
            )
            self.assertEqual(len(projected), 76)
            self.assertEqual(projected[-1]["bar_time"], "2026-08-10T15:15:00+09:00")
            self.assertEqual(projected[-1]["close"], 480)

    def test_new_trade_day_drops_previous_day_without_new_file_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_삼성전자"
            save_candles(stock_dir, _raw_candles(10, day="2026-08-09"), max_count=600)
            repository = SimpleNamespace(resolve_stock_dir=lambda _code, _name="": stock_dir)

            with patch.object(kiwoom_candle_adapter, "StockRepository", return_value=repository):
                saved = kiwoom_candle_adapter.save_minute_candles_for_stock(
                    "005930",
                    "삼성전자",
                    _opt10080_rows(_raw_candles(3)),
                )

            self.assertEqual(len(saved), 3)
            self.assertTrue(all(str(item["time"]).startswith("20260810") for item in saved))


class AutomaticCandleRefreshTests(unittest.TestCase):
    def _stock_dir(self, root: Path, index: int = 0) -> Path:
        stock_dir = root / f"{index + 1:06d}_Stock{index}"
        stock_dir.mkdir()
        (stock_dir / "state.json").write_text(
            json.dumps({"status": "RUNNING", "trade_enabled": True}),
            encoding="utf-8",
        )
        return stock_dir

    def test_bootstrap_then_incremental_refresh_reuses_existing_request_helper(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = self._stock_dir(Path(temp))
            calls: list[dict[str, object]] = []

            class Api:
                @staticmethod
                def is_available():
                    return True

                @staticmethod
                def is_connected():
                    return True

                @staticmethod
                def request_minute_candles(code, name, **kwargs):
                    calls.append({"code": code, "name": name, **kwargs})
                    save_candles(stock_dir, _raw_candles(1), max_count=600)
                    kwargs["callback"]({"ok": True, "rows_count": kwargs["count"]})
                    return {"ok": True}

            window = SimpleNamespace(kiwoom_api=Api())
            completions: list[dict[str, object]] = []
            with patch.object(auto_candle_refresh, "all_registered_stock_dirs", return_value=[stock_dir]), patch.object(
                auto_candle_refresh.QTimer,
                "singleShot",
                side_effect=lambda _delay, callback: callback(),
            ):
                first = auto_candle_refresh.refresh_operation_candles(
                    window,
                    "2026-08-10 10:00",
                    on_complete=completions.append,
                )
                second = auto_candle_refresh.refresh_operation_candles(
                    window,
                    "2026-08-10 10:01",
                    on_complete=completions.append,
                )

            self.assertTrue(first["accepted"])
            self.assertTrue(second["accepted"])
            self.assertEqual([call["count"] for call in calls], [600, 3])
            self.assertTrue(all(call["interval"] == 1 for call in calls))
            self.assertTrue(all(call["max_count"] == 600 for call in calls))
            self.assertEqual(len(completions), 2)

    def test_request_batch_is_bounded_and_round_robin_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dirs = [self._stock_dir(Path(temp), index) for index in range(20)]
            requested: list[str] = []

            class Api:
                is_available = staticmethod(lambda: True)
                is_connected = staticmethod(lambda: True)

                @staticmethod
                def request_minute_candles(code, _name, **kwargs):
                    requested.append(code)
                    kwargs["callback"]({"ok": True, "rows_count": 1})
                    return {"ok": True}

            window = SimpleNamespace(kiwoom_api=Api())
            with patch.object(auto_candle_refresh, "all_registered_stock_dirs", return_value=stock_dirs), patch.object(
                auto_candle_refresh.QTimer,
                "singleShot",
                side_effect=lambda _delay, callback: callback(),
            ):
                result = auto_candle_refresh.refresh_operation_candles(window, "2026-08-10 10:00")

            self.assertEqual(len(requested), 15)
            self.assertEqual(result["skipped_by_limit"], 5)
            self.assertEqual(result["request_spacing_ms"], 1000)

    def test_refresh_targets_prefers_registered_operation_targets_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            assigned = self._stock_dir(Path(temp), 1)
            unassigned = self._stock_dir(Path(temp), 2)
            calls: list[dict[str, object]] = []

            class Api:
                is_available = staticmethod(lambda: True)
                is_connected = staticmethod(lambda: True)

                @staticmethod
                def request_minute_candles(code, _name, **kwargs):
                    calls.append({"code": code, "name": _name, **kwargs})
                    kwargs["callback"]({"ok": True, "rows_count": 1})
                    return {"ok": True}

            window = SimpleNamespace(
                kiwoom_api=Api(),
                registered_operation_targets=lambda: [
                    (assigned, "000001", "Assigned"),
                ],
            )
            with patch.object(
                auto_candle_refresh,
                "all_registered_stock_dirs",
                return_value=[assigned, unassigned],
            ), patch.object(
                auto_candle_refresh.QTimer,
                "singleShot",
                side_effect=lambda _delay, callback: callback(),
            ):
                result = auto_candle_refresh.refresh_operation_candles(window, "2026-08-10 10:00")

            self.assertTrue(result["accepted"])
            self.assertEqual(["000001"], [call["code"] for call in calls])

    def test_operation_cycle_tail_does_not_repeat_batch_probe(self) -> None:
        host = Mock()
        host.startup_recovery_session_ready.return_value = True
        host._last_time_policy_minute_key = ""
        host.recalculate_all_status_by_operation_policy.return_value = {"changed": 0, "failed": 0}
        callbacks: list[object] = []
        probe = Mock(return_value={"logged": 0, "error": 0})
        pipeline = Mock(return_value={})

        def begin_refresh(_minute_key, *, on_complete):
            callbacks.append(on_complete)
            return {"accepted": True, "completed": False, "reason_code": "CANDLE_REFRESH_STARTED"}

        market_data = SimpleNamespace(
            sync_targets=Mock(return_value={}),
            prepare_operation_cycle=Mock(return_value={}),
            refresh_operation_candles=Mock(side_effect=begin_refresh),
        )
        host.market_data_host.return_value = market_data

        with patch.object(gui_auto_trade_timer, "auto_trade_current_time_policy_minute_key", return_value="2026-08-10 10:00"), patch.object(
            gui_auto_trade_timer,
            "auto_trade_continue_pending_close_liquidations",
            return_value={"processed": 0, "blocked": 0},
        ), patch.object(
            gui_auto_trade_timer,
            "auto_trade_continue_pending_manual_ats_liquidations",
            return_value={"processed": 0, "failed": 0},
        ), patch.object(gui_auto_trade_timer,
            "probe_all_enabled_routine_stocks_once",
            probe,
        ), patch.object(
            gui_auto_trade_timer,
            "_process_pending_signal_pipeline",
            pipeline,
        ), patch.object(gui_auto_trade_timer, "consume_pending_routine_signals_dry_run", None):
            result = gui_auto_trade_timer.auto_trade_run_operation_cycle(host)
            probe.assert_not_called()
            callbacks[0]({"completed": True})

        probe.assert_not_called()
        pipeline.assert_called_once_with(host)
        self.assertTrue(result["signal_result"]["deferred_for_candle_refresh"])


class SignalMarkerAndDayProjectionTests(unittest.TestCase):
    def test_probe_queue_payload_uses_exact_evaluated_bar_and_trace_hash(self) -> None:
        candles = completed_timeframe_candles(
            _raw_candles(15),
            {"bar": {"bar_minutes": 5}},
            now=datetime(2026, 8, 10, 9, 15, tzinfo=SEOUL_TIMEZONE),
        )
        captured: list[dict[str, object]] = []

        def enqueue(payload, **_kwargs):
            captured.append(payload)
            return {"status": "queued", "id": "SIGNAL-1"}

        with patch.object(routine_signal_probe, "enqueue_routine_signal", side_effect=enqueue):
            routine_signal_probe._maybe_enqueue_signal(
                {"signal": "BUY", "signal_index": 1, "delay_bar": 1},
                routine_name="test",
                code="005930",
                name="삼성전자",
                tick_key="2026-08-10 09:15",
                routine_instance_id="instance-1",
                candles=candles,
            )

        payload = captured[0]
        self.assertEqual(payload["signal_bar_time"], "2026-08-10T09:05:00+09:00")
        self.assertEqual(payload["signal_bar_close"], 110)
        self.assertEqual(payload["signal_timeframe_minutes"], 5)
        self.assertEqual(payload["signal_trade_date"], "2026-08-10")
        self.assertEqual(payload["signal_input_hash"], market_window_hash(candles))

    def test_queue_writer_adds_optional_marker_fields_and_old_reader_remains_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            queue_path = Path(temp) / "routine_signals.json"
            with patch.object(routine_signal_queue, "QUEUE_PATH", queue_path), patch.object(
                routine_signal_queue,
                "RUNTIME_DIR",
                queue_path.parent,
            ):
                queued = routine_signal_queue.enqueue_routine_signal(
                    {
                        "signal": "BUY",
                        "signal_index": 1,
                        "signal_bar_time": "2026-08-10T09:05:00+09:00",
                        "signal_bar_close": 110,
                        "signal_timeframe_minutes": 5,
                        "signal_trade_date": "2026-08-10",
                        "signal_input_hash": "HASH",
                    },
                    routine="test",
                    code="005930",
                    name="삼성전자",
                    tick_key="T1",
                )
            record = json.loads(queue_path.read_text(encoding="utf-8"))["signals"][0]
            self.assertEqual(record["signal_bar_close"], 110)

            old_path = Path(temp) / "old_routine_signals.json"
            old_path.write_text(
                json.dumps(
                    {
                        "signals": [
                            {
                                "id": "OLD",
                                "routine": "test",
                                "code": "005930",
                                "signal": "BUY",
                                "status": "PENDING",
                                "execution_enabled": False,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(routine_signal_order_bridge, "SIGNAL_QUEUE_PATH", old_path):
                old_records = routine_signal_order_bridge.load_pending_routine_signals()

            self.assertEqual([record["id"] for record in old_records], ["OLD"])
            self.assertNotIn("signal_bar_time", old_records[0])
            self.assertEqual(queued["status"], "queued")

    def test_day_projection_returns_markers_and_actual_order_linkage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dir = root / "stocks" / "005930_삼성전자"
            stock_dir.mkdir(parents=True)
            (stock_dir / "config.json").write_text(
                json.dumps({"assigned_routine_instance_id": "instance-1", "name": "삼성전자"}),
                encoding="utf-8",
            )
            save_candles(stock_dir, _raw_candles(10), max_count=600)
            rules_path = root / "rules.json"
            rules_path.write_text(json.dumps({"bar": {"bar_minutes": 5}}), encoding="utf-8")
            candles = completed_timeframe_candles(
                _raw_candles(10),
                {"bar": {"bar_minutes": 5}},
                now=datetime(2026, 8, 10, 9, 10, tzinfo=SEOUL_TIMEZONE),
            )
            runtime = root / "runtime"
            runtime.mkdir()
            (runtime / "routine_signals.json").write_text(
                json.dumps(
                    {
                        "signals": [
                            {
                                "id": "SIGNAL-1",
                                "routine_instance_id": "instance-1",
                                "code": "005930",
                                "signal": "BUY",
                                "signal_bar_time": candles[1]["bar_time"],
                                "signal_bar_close": candles[1]["close"],
                                "signal_timeframe_minutes": 5,
                                "signal_trade_date": "2026-08-10",
                                "signal_input_hash": market_window_hash(candles),
                                "signal_index": 1,
                                "delay_bar": 0,
                            },
                            {
                                "id": "OTHER-INSTANCE",
                                "routine_instance_id": "instance-2",
                                "code": "005930",
                                "signal": "SELL",
                                "signal_bar_time": candles[1]["bar_time"],
                                "signal_bar_close": candles[1]["close"],
                                "signal_timeframe_minutes": 5,
                                "signal_trade_date": "2026-08-10",
                            },
                            {"id": "OLD", "code": "005930", "signal": "SELL"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (runtime / "order_queue.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "revision": 0,
                        "orders": [
                            {"source_signal_id": "SIGNAL-1", "send_order_called": True},
                            {"source_signal_id": "SIGNAL-1", "send_order_called": False},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(
                stock_instance_day_projection,
                "routine_instance_by_id",
                return_value=SimpleNamespace(rules_path=rules_path),
            ):
                projected = stock_instance_day_projection.project_stock_instance_day(
                    "005930",
                    "2026-08-10",
                    project_root=root,
                    now=datetime(2026, 8, 10, 9, 10, tzinfo=SEOUL_TIMEZONE),
                )

        self.assertEqual(projected["instance_id"], "instance-1")
        self.assertEqual(projected["instance_name"], "instance-1")
        self.assertEqual(projected["bar_minutes"], 5)
        self.assertEqual(projected["operation_mode"], "SCHEDULED")
        self.assertEqual(projected["operation_mode_display"], "시간")
        self.assertEqual(projected["operation_start_time"], "09:00:00")
        self.assertEqual(projected["operation_end_buy_time"], "13:30:00")
        self.assertEqual(projected["operation_time"], "09:00~13:30")
        self.assertEqual(projected["ats_session_ranges"], [])
        self.assertEqual(projected["current_status"], "STOPPED")
        self.assertEqual(projected["current_status_display"], "감시/대기")
        self.assertEqual(len(projected["candles"]), 2)
        self.assertEqual(len(projected["buy_signal_markers"]), 1)
        self.assertEqual(len(projected["sell_signal_markers"]), 0)
        self.assertEqual(projected["buy_signal_count"], 1)
        self.assertEqual(projected["sell_signal_count"], 0)
        self.assertEqual(projected["buy_signal_markers"][0]["actual_order_count"], 1)
        self.assertEqual(projected["actual_order_count"], 1)
        self.assertEqual(projected["diagnostics"]["legacy_signal_marker_unavailable_count"], 1)

    def test_projection_ats_ranges_use_runtime_selection_and_operation_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "operation_policy.json").write_text(
                json.dumps(
                    {
                        "extra_sessions": [
                            {
                                "enabled": True,
                                "start_time": "08:00:00",
                                "end_time": "08:50:00",
                            },
                            {
                                "enabled": True,
                                "start_time": "15:40:00",
                                "end_time": "19:50:00",
                            },
                            {
                                "enabled": False,
                                "start_time": "20:00:00",
                                "end_time": "21:00:00",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            state = {
                "manual_ats_selection": {
                    "selected_sessions": ["extra1", "extra2", "extra3"]
                }
            }
            continuous = stock_instance_day_projection._selected_ats_session_ranges(
                {"operation_mode": "CONTINUOUS"},
                state,
                root,
            )
            scheduled = stock_instance_day_projection._selected_ats_session_ranges(
                {"operation_mode": "SCHEDULED"},
                state,
                root,
            )

        self.assertEqual(["extra1", "extra2"], [item["key"] for item in continuous])
        self.assertEqual("08:00:00", continuous[0]["start_time"])
        self.assertEqual("19:50:00", continuous[1]["end_time"])
        self.assertEqual([], scheduled)

    def test_operation_title_display_uses_mode_and_canonical_ats_selection(self) -> None:
        self.assertEqual(
            "시간운영",
            stock_instance_day_projection._operation_title_display("SCHEDULED", {}),
        )
        self.assertEqual(
            "수동운영",
            stock_instance_day_projection._operation_title_display("CONTINUOUS", {}),
        )
        self.assertEqual(
            "수동+ATS",
            stock_instance_day_projection._operation_title_display(
                "CONTINUOUS",
                {"manual_ats_selection": {"selected_sessions": ["extra1"]}},
            ),
        )

    def test_open_position_projection_reuses_reconciled_position_cost(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = root / "runtime"
            runtime.mkdir()
            (runtime / "positions.json").write_text(
                json.dumps(
                    {
                        "positions": [
                            {
                                "account_no": "12345678",
                                "code": "005930",
                                "quantity": 10,
                                "average_price": 70_000,
                                "cost_basis": 700_000,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (runtime / "broker_holdings.json").write_text(
                json.dumps(
                    {
                        "holdings": [
                            {
                                "account_no": "12345678",
                                "code": "005930",
                                "holding_quantity": 10,
                                "average_price": 70_000,
                                "current_price": 71_000,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            projected = stock_instance_day_projection._open_position_projection(
                root,
                "005930",
            )

        self.assertTrue(projected["open_position_available"])
        self.assertEqual(10, projected["holding_quantity"])
        self.assertEqual(70_000, projected["average_price"])
        self.assertEqual(700_000, projected["open_position_cost"])

    def test_open_position_projection_blocks_mismatch_and_accepts_zero_position(self) -> None:
        cases = (
            (9, 10, False, "HOLDING_RECONCILIATION_REQUIRED"),
            (0, 0, True, ""),
        )
        for broker_quantity, internal_quantity, available, reason in cases:
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                runtime = root / "runtime"
                runtime.mkdir()
                (runtime / "positions.json").write_text(
                    json.dumps(
                        {
                            "positions": (
                                []
                                if internal_quantity == 0
                                else [
                                    {
                                        "account_no": "12345678",
                                        "code": "005930",
                                        "quantity": internal_quantity,
                                        "average_price": 70_000,
                                        "cost_basis": internal_quantity * 70_000,
                                    }
                                ]
                            )
                        }
                    ),
                    encoding="utf-8",
                )
                (runtime / "broker_holdings.json").write_text(
                    json.dumps(
                        {
                            "holdings": [
                                {
                                    "account_no": "12345678",
                                    "code": "005930",
                                    "holding_quantity": broker_quantity,
                                    "average_price": 70_000,
                                    "current_price": 71_000,
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )

                projected = stock_instance_day_projection._open_position_projection(
                    root,
                    "005930",
                )

            self.assertEqual(available, projected["open_position_available"])
            self.assertEqual(reason, projected["open_position_unavailable_reason"])

    def test_open_position_projection_does_not_ignore_broker_only_holding(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = root / "runtime"
            runtime.mkdir()
            (runtime / "broker_holdings.json").write_text(
                json.dumps(
                    {
                        "holdings": [
                            {
                                "account_no": "12345678",
                                "code": "005930",
                                "holding_quantity": 3,
                                "average_price": 70_000,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            projected = stock_instance_day_projection._open_position_projection(
                root,
                "005930",
            )

        self.assertFalse(projected["open_position_available"])
        self.assertEqual(
            "HOLDING_RECONCILIATION_REQUIRED",
            projected["open_position_unavailable_reason"],
        )

    def test_day_projection_reports_malformed_candle_data_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dir = root / "stocks" / "005930_삼성전자"
            stock_dir.mkdir(parents=True)
            (stock_dir / "config.json").write_text(
                json.dumps({"assigned_routine_instance_id": "instance-1"}),
                encoding="utf-8",
            )
            (stock_dir / "candles.json").write_text("{broken", encoding="utf-8")
            rules_path = root / "rules.json"
            rules_path.write_text(
                json.dumps({"bar": {"bar_minutes": 5}}),
                encoding="utf-8",
            )
            with patch.object(
                stock_instance_day_projection,
                "routine_instance_by_id",
                return_value=SimpleNamespace(rules_path=rules_path),
            ):
                projected = stock_instance_day_projection.project_stock_instance_day(
                    "005930",
                    "2026-08-10",
                    project_root=root,
                    now=datetime(2026, 8, 10, 9, 10, tzinfo=SEOUL_TIMEZONE),
                )

        self.assertEqual([], projected["candles"])
        self.assertIn("CANDLE_DATA_MALFORMED", projected["diagnostics"]["issues"])


if __name__ == "__main__":
    unittest.main()
