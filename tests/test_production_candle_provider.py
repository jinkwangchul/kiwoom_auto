# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from candle_timeframe_aggregation import (
    SEOUL_TIMEZONE,
    aggregate_minute_candles,
    project_candle_supply,
    required_minute_candles,
)
from gui_market_data_host import MarketDataHost
from mock_validation_host import MockValidationHost
from routines.지표추종매매.routine import market_bar_projection_request
from stock_instance_day_projection import chart_candle_projection_request


def _minutes(count: int, *, start: datetime | None = None) -> list[dict[str, object]]:
    anchor = start or datetime(2026, 9, 8, 9, 0, tzinfo=SEOUL_TIMEZONE)
    return [
        {
            "time": (anchor + timedelta(minutes=index)).strftime("%Y%m%d%H%M%S"),
            "open": 100 + index,
            "high": 101 + index,
            "low": 99 + index,
            "close": 100 + index,
            "volume": 10,
        }
        for index in range(count)
    ]


class ProductionCandleProviderTest(unittest.TestCase):
    def test_chart_display_does_not_hide_completed_prices_for_missing_forming_or_warmup(self) -> None:
        raw = _minutes(390)
        host = MarketDataHost.__new__(MarketDataHost)
        host.kiwoom_api = SimpleNamespace(current_realtime_shadow_bar=lambda _code: None)
        rules = {"bar": {"bar_minutes": 5}}
        routine_request = {"projection": "FORMING_BASE_BAR", "warmup_bars": 60}
        sessions = (
            {"name": "regular", "start_time": "09:00:00", "end_time": "15:30:00"},
        )
        with patch("gui_market_data_host.load_candles", return_value=raw), patch(
            "gui_market_data_host.StockRepository"
        ) as repository:
            repository.return_value.resolve_stock_dir.return_value = Path("unused")
            evaluation = host.project_routine_candles(
                stock_code="005930",
                rules=rules,
                projection_request=routine_request,
                as_of=datetime(2026, 9, 8, 15, 30, tzinfo=SEOUL_TIMEZONE),
                session_windows=sessions,
            )
            chart_request = chart_candle_projection_request(routine_request)
            production_chart = host.project_routine_candles(
                stock_code="005930",
                rules=rules,
                projection_request=chart_request,
                as_of=datetime(2026, 9, 8, 15, 30, tzinfo=SEOUL_TIMEZONE),
                session_windows=sessions,
                consumer_scope="PRODUCTION",
            )
            mock_chart = host.project_routine_candles(
                stock_code="005930",
                rules=rules,
                projection_request=chart_request,
                as_of=datetime(2026, 9, 8, 15, 30, tzinfo=SEOUL_TIMEZONE),
                session_windows=sessions,
                consumer_scope="MOCK",
            )

        self.assertEqual("FORMING_SOURCE_UNAVAILABLE", evaluation["availability_state"])
        self.assertFalse(evaluation["available"])
        self.assertNotIn("warmup_bars", chart_request)
        self.assertTrue(production_chart["available"])
        self.assertGreater(len(production_chart["candles"]), 0)
        self.assertEqual(production_chart["candles"], mock_chart["candles"])
        self.assertEqual(
            production_chart["source_identity"],
            mock_chart["source_identity"],
        )

    def test_warmup_margin_and_limit_contract(self) -> None:
        self.assertEqual(69_120, required_minute_candles(240, 240)["required_minute_candles"])
        exceeded = required_minute_candles(240, 700)
        self.assertFalse(exceeded["within_limit"])
        self.assertEqual("CANDLE_WARMUP_LIMIT_EXCEEDED", exceeded["reason"])
        self.assertEqual(201_600, exceeded["required_minute_candles"])
        self.assertEqual(200_000, exceeded["maximum_minute_candles"])

    def test_regular_and_selected_ats_sessions_have_independent_buckets(self) -> None:
        regular = _minutes(5)
        ats = _minutes(5, start=datetime(2026, 9, 8, 9, 10, tzinfo=SEOUL_TIMEZONE))
        projected = aggregate_minute_candles(
            regular + ats,
            5,
            now=datetime(2026, 9, 8, 9, 20, tzinfo=SEOUL_TIMEZONE),
            session_windows=(
                {"name": "regular", "start_time": "09:00:00", "end_time": "09:05:00"},
                {"name": "extra1", "start_time": "09:10:00", "end_time": "09:15:00"},
            ),
        )
        self.assertEqual(["regular", "extra1"], [item["session"] for item in projected])
        self.assertTrue(all(item["is_complete"] for item in projected))

    def test_invalid_session_window_fails_closed_without_projection(self) -> None:
        result = project_candle_supply(
            _minutes(2),
            {"bar": {"bar_minutes": 1}},
            {"projection": "COMPLETED_TIMEFRAME", "warmup_bars": 1},
            now=datetime(2026, 9, 8, 9, 2, tzinfo=SEOUL_TIMEZONE),
            session_windows=({"name": "invalid", "start_time": "15:20:00", "end_time": "09:00:00"},),
        )
        self.assertFalse(result["available"])
        self.assertEqual("SESSION_INVALID", result["availability_state"])

    def test_provider_adds_forming_shadow_without_committing_it(self) -> None:
        raw = _minutes(2)
        forming = _minutes(1, start=datetime(2026, 9, 8, 9, 2, tzinfo=SEOUL_TIMEZONE))[0]
        api = SimpleNamespace(current_realtime_shadow_bar=lambda _code: forming)
        host = MarketDataHost.__new__(MarketDataHost)
        host.kiwoom_api = api
        request = {"projection": "FORMING_BASE_BAR", "warmup_bars": 1}
        with patch("gui_market_data_host.load_candles", return_value=raw), patch(
            "gui_market_data_host.StockRepository"
        ) as repository:
            repository.return_value.resolve_stock_dir.return_value = Path("unused")
            result = host.project_routine_candles(
                stock_code="005930",
                rules={"bar": {"bar_minutes": 1}},
                projection_request=request,
                as_of=datetime(2026, 9, 8, 9, 2, 30, tzinfo=SEOUL_TIMEZONE),
                session_windows=({"name": "regular", "start_time": "09:00:00", "end_time": "15:20:00"},),
            )
        self.assertTrue(result["available"])
        self.assertTrue(result["forming_included"])
        self.assertFalse(result["candles"][-1]["is_complete"])

    def test_same_stock_requests_are_projected_per_applied_interval(self) -> None:
        raw = _minutes(288)
        host = MarketDataHost.__new__(MarketDataHost)
        host.kiwoom_api = SimpleNamespace(current_realtime_shadow_bar=lambda _code: None)
        counts = {}
        with patch("gui_market_data_host.load_candles", return_value=raw), patch(
            "gui_market_data_host.StockRepository"
        ) as repository:
            repository.return_value.resolve_stock_dir.return_value = Path("unused")
            for interval in (1, 5, 120, 240):
                result = host.project_routine_candles(
                    stock_code="005930",
                    rules={"bar": {"bar_minutes": interval}},
                    projection_request={"projection": "COMPLETED_TIMEFRAME", "warmup_bars": 1},
                    as_of=datetime(2026, 9, 8, 14, 0, tzinfo=SEOUL_TIMEZONE),
                    session_windows=({"name": "regular", "start_time": "09:00:00", "end_time": "15:20:00"},),
                )
                counts[interval] = len(result["candles"])
                self.assertTrue(all(item["timeframe_minutes"] == interval for item in result["candles"]))
        self.assertEqual({1: 288, 5: 57, 120: 2, 240: 1}, counts)

    def test_mock_has_no_direct_canonical_file_reader(self) -> None:
        self.assertFalse(hasattr(MockValidationHost, "_read_candles"))

    def test_indicator_follow_declares_warmup_without_main_interpretation(self) -> None:
        request = market_bar_projection_request(
            {"bar": {"bar_minutes": 240}, "indicators": {"moving_averages": [5, 20, 240]}}
        )
        self.assertEqual(240, request["warmup_bars"])

    def test_waiting_mock_requirement_joins_and_leaves_observation_universe(self) -> None:
        synced: list[tuple[str, ...]] = []
        monitored: list[tuple[str, ...]] = []
        host = SimpleNamespace(
            kiwoom_api=SimpleNamespace(
                sync_realtime_shadow_targets=lambda codes: synced.append(tuple(codes)) or {"ok": True}
            ),
            _execution_shadow_stock_codes=("005930",),
            _production_monitoring_stock_codes=("005930",),
            _candle_observation_stock_codes=(),
            _candle_observation_required_by_stock={},
            sync_monitoring_targets=lambda codes: monitored.append(tuple(codes)) or {"ok": True},
        )
        requirement = {
            "stock_code": "000080",
            "rules": {"bar": {"bar_minutes": 240}},
            "projection_request": {"projection": "COMPLETED_TIMEFRAME", "warmup_bars": 240},
        }
        with patch("gui_market_data_host.QTimer.singleShot"):
            MarketDataHost.sync_candle_observation_targets(host, [requirement])
            self.assertEqual(("005930",), monitored[-1])
            self.assertEqual(("000080", "005930"), synced[-1])
            self.assertEqual(69_120, host._candle_observation_required_by_stock["000080"])
            MarketDataHost.sync_candle_observation_targets(host, [])
        self.assertEqual(("005930",), monitored[-1])
        self.assertEqual(("005930",), synced[-1])
        self.assertEqual({}, host._candle_observation_required_by_stock)

    def test_mock_requests_production_projection_for_each_instance_rules(self) -> None:
        calls: list[dict[str, object]] = []

        def provider(**kwargs):
            calls.append(dict(kwargs))
            return {"available": True, "candles": [{"timeframe_minutes": kwargs["rules"]["bar"]["bar_minutes"]}]}

        host = SimpleNamespace(
            _candles_provider=provider,
            routine_adapter=SimpleNamespace(
                market_bar_projection_request=lambda rules: {
                    "projection": "COMPLETED_TIMEFRAME",
                    "warmup_bars": 1,
                    "interval": rules["bar"]["bar_minutes"],
                }
            ),
            _operation_policy_provider=lambda: {
                "regular_market": {"start_time": "09:00:00", "end_time": "15:20:00"}
            },
        )
        host._selected_candle_sessions = lambda settings: MockValidationHost._selected_candle_sessions(host, settings)
        document = {"session": {"stock_code": "005930"}}
        for instance_id, interval in (("A", 1), ("B", 120)):
            result = MockValidationHost._instance_candle_projection(
                host,
                document,
                instance_id=instance_id,
                rules={"bar": {"bar_minutes": interval}},
                settings={},
                now=datetime(2026, 9, 8, 10, 0, tzinfo=SEOUL_TIMEZONE),
            )
            self.assertEqual(interval, result["candles"][0]["timeframe_minutes"])

        self.assertEqual([1, 120], [call["rules"]["bar"]["bar_minutes"] for call in calls])
        self.assertTrue(all(call["consumer_scope"] == "MOCK" for call in calls))


if __name__ == "__main__":
    unittest.main()
