from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from candle_timeframe_aggregation import (
    SEOUL_TIMEZONE,
    aggregate_minute_candles,
    completed_timeframe_candles,
    read_canonical_bar_minutes,
)
from market_evidence_store import market_window_hash
import routine_signal_probe


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_indicator_engine():
    engine_path = next((PROJECT_ROOT / "routines").glob("*/routine_macd_engine.py"))
    spec = spec_from_file_location("routine_macd_engine_for_timeframe_test", engine_path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _raw_minutes(
    count: int,
    *,
    start: datetime | None = None,
    timestamp_format: str = "iso",
) -> list[dict[str, int | float | str]]:
    first = start or datetime(2026, 8, 10, 9, 0, tzinfo=SEOUL_TIMEZONE)
    candles: list[dict[str, int | float | str]] = []
    for index in range(count):
        bar_time = first + timedelta(minutes=index)
        timestamp = (
            bar_time.strftime("%Y%m%d%H%M%S")
            if timestamp_format == "kiwoom"
            else bar_time.isoformat(timespec="seconds")
        )
        base = 100 + index
        candles.append(
            {
                "time": timestamp,
                "open": base,
                "high": base + 3,
                "low": base - 2,
                "close": base + 1,
                "volume": 10 + index,
            }
        )
    return candles


class CandleTimeframeAggregationTests(unittest.TestCase):
    def test_canonical_source_uses_applied_bar_not_ui_or_pending(self) -> None:
        rules = {
            "bar": {"bar_minutes": 1},
            "indicator_follow_ui_state": {
                "state": {"basic": {"basic_signal_interval_combo": "5"}}
            },
            "indicator_follow_rule_pending": {
                "candidates": {"bar": {"path": "bar.bar_minutes", "value": 5}}
            },
        }

        self.assertEqual(read_canonical_bar_minutes(rules), 1)

    def test_one_minute_projection_preserves_raw_sequence_meaning(self) -> None:
        raw = _raw_minutes(3, timestamp_format="kiwoom")
        projected = completed_timeframe_candles(
            raw,
            {"bar": {"bar_minutes": 1}},
            now=datetime(2026, 8, 10, 9, 4, tzinfo=SEOUL_TIMEZONE),
        )

        self.assertEqual(len(projected), len(raw))
        self.assertEqual(
            [(item["open"], item["high"], item["low"], item["close"], item["volume"]) for item in projected],
            [(item["open"], item["high"], item["low"], item["close"], item["volume"]) for item in raw],
        )
        self.assertEqual(
            [item["bar_time"] for item in projected],
            [
                "2026-08-10T09:00:00+09:00",
                "2026-08-10T09:01:00+09:00",
                "2026-08-10T09:02:00+09:00",
            ],
        )

    def test_five_minute_ohlcv_and_normalized_schema(self) -> None:
        projected = aggregate_minute_candles(
            _raw_minutes(5),
            5,
            now=datetime(2026, 8, 10, 9, 5, tzinfo=SEOUL_TIMEZONE),
        )

        self.assertEqual(len(projected), 1)
        candle = projected[0]
        self.assertEqual(candle["bar_time"], "2026-08-10T09:00:00+09:00")
        self.assertEqual(candle["open"], 100)
        self.assertEqual(candle["high"], 107)
        self.assertEqual(candle["low"], 98)
        self.assertEqual(candle["close"], 105)
        self.assertEqual(candle["volume"], 60)
        self.assertEqual(candle["timeframe_minutes"], 5)
        self.assertIs(candle["is_complete"], True)
        self.assertEqual(candle["trade_date"], "2026-08-10")

    def test_all_supported_multi_minute_timeframes_use_market_clock_boundaries(self) -> None:
        raw = _raw_minutes(240)
        as_of = datetime(2026, 8, 10, 13, 0, tzinfo=SEOUL_TIMEZONE)

        for timeframe in (3, 5, 10, 15, 30, 60, 120, 240):
            with self.subTest(timeframe=timeframe):
                projected = completed_timeframe_candles(
                    raw,
                    {"bar": {"bar_minutes": timeframe}},
                    now=as_of,
                )
                self.assertEqual(len(projected), 240 // timeframe)
                self.assertEqual(projected[0]["bar_time"], "2026-08-10T09:00:00+09:00")
                self.assertEqual(projected[-1]["timeframe_minutes"], timeframe)

    def test_incomplete_first_and_progressing_last_buckets_are_not_completed(self) -> None:
        first_partial = _raw_minutes(
            3,
            start=datetime(2026, 8, 10, 9, 2, tzinfo=SEOUL_TIMEZONE),
        )
        progressing = _raw_minutes(
            4,
            start=datetime(2026, 8, 10, 10, 10, tzinfo=SEOUL_TIMEZONE),
        )
        first_projection = aggregate_minute_candles(
            first_partial,
            5,
            now=datetime(2026, 8, 10, 9, 10, tzinfo=SEOUL_TIMEZONE),
        )
        last_projection = aggregate_minute_candles(
            progressing,
            5,
            now=datetime(2026, 8, 10, 10, 13, tzinfo=SEOUL_TIMEZONE),
        )

        self.assertIs(first_projection[0]["is_complete"], False)
        self.assertIs(last_projection[0]["is_complete"], False)
        self.assertEqual(
            completed_timeframe_candles(
                progressing,
                {"bar": {"bar_minutes": 5}},
                now=datetime(2026, 8, 10, 10, 13, tzinfo=SEOUL_TIMEZONE),
            ),
            [],
        )

    def test_last_bucket_becomes_complete_only_at_its_end_boundary(self) -> None:
        raw = _raw_minutes(
            5,
            start=datetime(2026, 8, 10, 10, 10, tzinfo=SEOUL_TIMEZONE),
        )

        before_end = aggregate_minute_candles(
            raw,
            5,
            now=datetime(2026, 8, 10, 10, 14, tzinfo=SEOUL_TIMEZONE),
        )
        at_end = completed_timeframe_candles(
            raw,
            {"bar": {"bar_minutes": 5}},
            now=datetime(2026, 8, 10, 10, 15, tzinfo=SEOUL_TIMEZONE),
        )

        self.assertIs(before_end[0]["is_complete"], False)
        self.assertEqual(len(at_end), 1)
        self.assertIs(at_end[0]["is_complete"], True)

    def test_aggregated_window_reuses_stable_decision_trace_hash_contract(self) -> None:
        projected = completed_timeframe_candles(
            _raw_minutes(10),
            {"bar": {"bar_minutes": 5}},
            now=datetime(2026, 8, 10, 9, 10, tzinfo=SEOUL_TIMEZONE),
        )

        self.assertEqual(market_window_hash(projected), market_window_hash(deepcopy(projected)))
        self.assertEqual(len(market_window_hash(projected)), 64)

    def test_indicator_engine_delay_bar_indexes_aggregated_sequence(self) -> None:
        engine = _load_indicator_engine()
        candles = completed_timeframe_candles(
            _raw_minutes(15),
            {"bar": {"bar_minutes": 5}},
            now=datetime(2026, 8, 10, 9, 15, tzinfo=SEOUL_TIMEZONE),
        )
        config = deepcopy(engine.DEFAULT_INDICATOR_FOLLOW_CONFIG)
        config["moving_averages"] = []
        config["buy"] = {
            "delay_bar": 1,
            "groups": [
                {
                    "enabled": True,
                    "name": "aggregated-close",
                    "conditions": [
                        {"enabled": True, "target": "CLOSE", "operator": ">=", "value": 110}
                    ],
                }
            ],
        }
        config["sell"] = {
            "delay_bar": 0,
            "signals": {"macd_sell": {"enabled": False, "groups": []}},
        }

        signal = engine.evaluate_indicator_follow_routine(candles, config, {})

        self.assertEqual(signal.signal, "BUY")
        self.assertEqual(signal.signal_index, 1)
        self.assertEqual(signal.delay_bar, 1)
        self.assertEqual(candles[signal.signal_index]["bar_time"], "2026-08-10T09:05:00+09:00")


class RoutineSignalProbeTimeframeIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.stock_dir = Path(self.temp.name) / "005930_삼성전자"
        self.stock_dir.mkdir()
        (self.stock_dir / "state.json").write_text(
            json.dumps({"trade_enabled": True, "status": "WATCHING"}),
            encoding="utf-8",
        )
        (self.stock_dir / "config.json").write_text(
            json.dumps({"assigned_routine_instance_id": "instance-5m"}),
            encoding="utf-8",
        )
        self.raw = _raw_minutes(15)
        self.raw_path = self.stock_dir / "candles.json"
        self.raw_path.write_text(json.dumps(self.raw), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_probe_passes_only_completed_applied_timeframe_bars_and_keeps_raw_file(self) -> None:
        class CapturingRoutine:
            ROUTINE_TYPE = "TEST"
            context = None

            @classmethod
            def evaluate(cls, context):
                cls.context = context
                return {
                    "signal": None,
                    "reason": "조건 미충족",
                    "details": [],
                    "signal_index": len(context["candles"]) - 2,
                    "delay_bar": 1,
                }

        before = self.raw_path.read_bytes()
        rules = {"bar": {"bar_minutes": 5}}
        with patch.object(routine_signal_probe, "_load_instance_rules", return_value=rules), patch.object(
            routine_signal_probe, "_append_log"
        ), patch.object(routine_signal_probe, "read_latest_price", return_value=None):
            result = routine_signal_probe.probe_routine_for_stock(
                CapturingRoutine,
                "test",
                self.stock_dir,
                "2026-08-10 09:15",
                decision_trace_observer=None,
            )

        candles = CapturingRoutine.context["candles"]
        self.assertEqual(len(candles), 3)
        self.assertEqual([item["close"] for item in candles], [105, 110, 115])
        self.assertTrue(all(item["timeframe_minutes"] == 5 for item in candles))
        self.assertTrue(all(item["is_complete"] is True for item in candles))
        self.assertEqual(result["candles"], 3)
        self.assertEqual(result["signal_index"], 1)
        self.assertEqual(result["delay_bar"], 1)
        self.assertEqual(candles[result["signal_index"]]["bar_time"], "2026-08-10T09:05:00+09:00")
        self.assertEqual(self.raw_path.read_bytes(), before)

    def test_invalid_applied_timeframe_fails_without_evaluating_raw_candles(self) -> None:
        class MustNotRunRoutine:
            @staticmethod
            def evaluate(_context):
                raise AssertionError("evaluate must not run")

        with patch.object(
            routine_signal_probe,
            "_load_instance_rules",
            return_value={"bar": {"bar_minutes": 7}},
        ), patch.object(routine_signal_probe, "_append_log"):
            result = routine_signal_probe.probe_routine_for_stock(
                MustNotRunRoutine,
                "test",
                self.stock_dir,
                "2026-08-10 09:15",
                decision_trace_observer=None,
            )

        self.assertEqual(result["signal"], "ERROR")
        self.assertIn("unsupported bar.bar_minutes", result["reason"])


if __name__ == "__main__":
    unittest.main()
