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
        self.assertEqual(63_360, required_minute_candles(240, 240)["required_minute_candles"])
        within = required_minute_candles(240, 700)
        self.assertTrue(within["within_limit"])
        self.assertEqual("", within["reason"])
        self.assertEqual(184_800, within["required_minute_candles"])
        exceeded = required_minute_candles(240, 800)
        self.assertFalse(exceeded["within_limit"])
        self.assertEqual("CANDLE_WARMUP_LIMIT_EXCEEDED", exceeded["reason"])
        self.assertEqual(211_200, exceeded["required_minute_candles"])
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

    def test_warmup_requires_enough_usable_projected_bars_not_only_raw_minutes(self) -> None:
        regular = _minutes(
            5,
            start=datetime(2026, 9, 8, 9, 0, tzinfo=SEOUL_TIMEZONE),
        )
        off_session = _minutes(
            12,
            start=datetime(2026, 9, 8, 8, 0, tzinfo=SEOUL_TIMEZONE),
        )

        result = project_candle_supply(
            regular + off_session,
            {"bar": {"bar_minutes": 5}},
            {"projection": "COMPLETED_TIMEFRAME", "warmup_bars": 3},
            now=datetime(2026, 9, 8, 10, 0, tzinfo=SEOUL_TIMEZONE),
            session_windows=(
                {"name": "regular", "start_time": "09:00:00", "end_time": "15:20:00"},
            ),
        )

        self.assertFalse(result["available"])
        self.assertEqual("HISTORY_INSUFFICIENT", result["availability_state"])
        self.assertEqual(17, result["available_minute_candles"])
        self.assertEqual(1, result["available_timeframe_bars"])

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

    def test_forming_projection_excludes_historical_incomplete_buckets(self) -> None:
        raw = _minutes(
            1,
            start=datetime(2026, 9, 8, 9, 0, tzinfo=SEOUL_TIMEZONE),
        ) + _minutes(
            5,
            start=datetime(2026, 9, 8, 9, 5, tzinfo=SEOUL_TIMEZONE),
        )
        forming = _minutes(
            1,
            start=datetime(2026, 9, 8, 9, 10, tzinfo=SEOUL_TIMEZONE),
        )[0]

        result = project_candle_supply(
            raw,
            {"bar": {"bar_minutes": 5}},
            {"projection": "FORMING_BASE_BAR", "warmup_bars": 1},
            now=datetime(2026, 9, 8, 9, 10, 30, tzinfo=SEOUL_TIMEZONE),
            session_windows=(
                {"name": "regular", "start_time": "09:00:00", "end_time": "15:20:00"},
            ),
            forming_minute=forming,
        )

        self.assertTrue(result["available"])
        self.assertTrue(result["forming_included"])
        self.assertEqual(
            ["09:05:00", "09:10:00"],
            [item["bar_time"][11:19] for item in result["candles"]],
        )
        self.assertTrue(result["candles"][0]["is_complete"])
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
            {
                "indicators": {
                    "moving_averages": [5, 20, 240],
                    "macd": {"slow": 26, "signal": 9},
                },
                "buy": {
                    "delay_bar": 1,
                    "groups": [{
                        "enabled": True,
                        "conditions": [{
                            "enabled": True,
                            "target": "MA",
                            "period": 20,
                            "operator": "CROSS_UP",
                            "compare_target": "CLOSE",
                        }],
                    }],
                },
                "sell": {"enabled": False, "signals": {}},
            }
        )
        self.assertEqual("FORMING_BASE_BAR", request["projection"])
        self.assertEqual(22, request["warmup_bars"])

    def test_indicator_follow_warmup_uses_only_active_evaluator_paths(self) -> None:
        request = market_bar_projection_request({
            "indicators": {
                "moving_averages": [5, 20, 240],
                "rsi": {"period": 14},
                "macd": {"slow": 26, "signal": 9},
            },
            "buy": {
                "delay_bar": 2,
                "groups": [
                    {
                        "enabled": True,
                        "conditions": [{
                            "enabled": True,
                            "target": "RSI",
                            "period": 14,
                            "operator": "TURN_UP",
                            "bar_offset": 3,
                        }, {
                            "enabled": False,
                            "target": "MA",
                            "period": 240,
                            "operator": "TURN_UP",
                        }],
                    },
                    {
                        "enabled": False,
                        "conditions": [{
                            "enabled": True,
                            "target": "MA",
                            "period": 240,
                            "operator": "TURN_UP",
                        }],
                    },
                ],
                "filters": {
                    "moving_average": {
                        "enabled": False,
                        "conditions": [{"period": 240}],
                    },
                    "ocr": {"enabled": False, "order_delay_bars": 8},
                },
            },
            "sell": {"enabled": False, "signals": {}},
        })

        # RSI 14 needs 15 bars, TURN_UP needs two older bars, bar_offset is
        # three, and the engine-owned OCR delay override is eight.
        self.assertEqual(28, request["warmup_bars"])

    def test_indicator_follow_filter_defaults_match_the_current_evaluator(self) -> None:
        request = market_bar_projection_request({
            "indicators": {
                "rsi": {"period": 14},
                "bollinger": {"period": 20},
            },
            "buy": {
                "delay_bar": 2,
                "groups": [{
                    "enabled": True,
                    "conditions": [{
                        "target": "CLOSE",
                        "operator": ">=",
                        "value": 0,
                    }],
                }],
                "filters": {
                    "rsi": {
                        "enabled": True,
                        "conditions": [
                            {"period": 14, "operator": "<=", "value": 40},
                            {"period": 240, "operator": "<=", "value": 40},
                        ],
                    },
                    "moving_average": {
                        "enabled": True,
                        "conditions": [
                            {"period": 30, "operator": "CROSS_UP"},
                            {"period": 240, "operator": "CROSS_UP"},
                        ],
                    },
                    "bollinger": {
                        "enabled": True,
                        # The current evaluator does not merge this filter-level
                        # value into its first condition.
                        "period": 240,
                        "conditions": [
                            {
                                "period": 240,
                                "operator": "<=",
                                "compare_target": "BOLLINGER_LOWER",
                            },
                            {"period": 240, "operator": "<="},
                        ],
                    },
                },
            },
            "sell": {"enabled": False, "signals": {}},
        })

        # The evaluator uses only the first condition for these three filters.
        # MA30 CROSS_UP plus the two-bar BUY delay is the largest active need.
        self.assertEqual(33, request["warmup_bars"])

    def test_indicator_follow_legacy_filters_are_ignored_without_reachable_buy_group(self) -> None:
        request = market_bar_projection_request({
            "indicators": {"rsi": {"period": 240}},
            "buy": {
                "delay_bar": 7,
                "groups": [],
                "filters": {
                    "rsi": {
                        "enabled": True,
                        "conditions": [{"period": 240, "operator": "<=", "value": 40}],
                    },
                },
            },
            "sell": {"enabled": False, "signals": {}},
        })

        self.assertEqual(3, request["warmup_bars"])

    def test_indicator_follow_expression_includes_unreferenced_active_filters(self) -> None:
        request = market_bar_projection_request({
            "indicators": {
                "moving_averages": [240],
                "rsi": {"period": 14},
            },
            "buy": {
                "delay_bar": 0,
                "groups": [{
                    "enabled": True,
                    "conditions": [{
                        "target": "MA",
                        "period": 240,
                        "operator": "TURN_UP",
                    }],
                }],
                "filters": {
                    "rsi": {
                        "enabled": True,
                        "conditions": [{
                            "enabled": True,
                            "target": "RSI",
                            "period": 14,
                            "operator": "<=",
                            "value": 40,
                        }],
                    },
                    "moving_average": {
                        "enabled": True,
                        "conditions": [{
                            "target": "MA",
                            "period": 240,
                            "operator": "TURN_UP",
                        }],
                    },
                    "composite": {
                        "enabled": True,
                        "expression": {
                            "identifiers": ["A"],
                            "identifier_map": {"A": "rsi"},
                            "ast": {"type": "IDENTIFIER", "name": "A"},
                        },
                    },
                },
            },
            "sell": {"enabled": False, "signals": {}},
        })

        # The current composite evaluator also requires every configured and
        # enabled filter that is not referenced by the expression.
        self.assertEqual(242, request["warmup_bars"])

    def test_indicator_follow_price_box_24_requires_24_bars(self) -> None:
        request = market_bar_projection_request({
            "indicators": {"price_box": {"period": 24}},
            "buy": {
                "delay_bar": 0,
                "groups": [{
                    "enabled": True,
                    "conditions": [{
                        "enabled": True,
                        "target": "PRICE_BOX_LOWER",
                        "operator": ">=",
                        "value": 0,
                    }],
                }],
            },
            "sell": {"enabled": False, "signals": {}},
        })

        self.assertEqual(24, request["warmup_bars"])

    def test_indicator_follow_sell_signal_uses_signal_delay_and_compared_indicator(self) -> None:
        request = market_bar_projection_request({
            "indicators": {
                "macd": {"slow": 26, "signal": 9},
                "bollinger": {"period": 20},
            },
            "buy": {"enabled": False, "groups": []},
            "sell": {
                "enabled": True,
                "delay_bar": 1,
                "signals": {
                    "active": {
                        "enabled": True,
                        "order_delay_bars": 4,
                        "groups": [{
                            "enabled": True,
                            "conditions": [{
                                "enabled": True,
                                "target": "CLOSE",
                                "operator": "CROSS_UP",
                                "compare_target": "BOLLINGER_UPPER",
                            }],
                        }],
                    },
                    "dormant": {
                        "enabled": False,
                        "groups": [{
                            "enabled": True,
                            "conditions": [{
                                "target": "MA",
                                "period": 240,
                                "operator": "TURN_DOWN",
                            }],
                        }],
                    },
                },
            },
        })

        self.assertEqual(25, request["warmup_bars"])

    def test_indicator_follow_profit_rate_sell_warmup_uses_actual_sell_delay(self) -> None:
        request = market_bar_projection_request({
            "buy": {"enabled": False, "groups": []},
            "sell": {
                "enabled": True,
                "delay_bar": 1,
                "signals": {
                    "macd_sell": {
                        "enabled": False,
                        "delay_bar": 7,
                        "groups": [],
                    },
                    "profit_rate_sell": {
                        "enabled": True,
                        "order_delay_bars": 1,
                    },
                },
            },
        })

        self.assertEqual(8, request["warmup_bars"])

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
            self.assertEqual(63_360, host._candle_observation_required_by_stock["000080"])
            MarketDataHost.sync_candle_observation_targets(host, [])
        self.assertEqual(("005930",), monitored[-1])
        self.assertEqual(("005930",), synced[-1])
        self.assertEqual({}, host._candle_observation_required_by_stock)

    def test_observation_history_target_caps_without_dropping_target(self) -> None:
        synced: list[tuple[str, ...]] = []
        host = SimpleNamespace(
            kiwoom_api=SimpleNamespace(
                sync_realtime_shadow_targets=lambda codes: synced.append(tuple(codes)) or {"ok": True}
            ),
            _execution_shadow_stock_codes=(),
            _production_monitoring_stock_codes=(),
            _candle_observation_stock_codes=(),
            _candle_observation_required_by_stock={},
            sync_monitoring_targets=lambda _codes: {"ok": True},
        )
        requirement = {
            "stock_code": "005930",
            "rules": {"bar": {"bar_minutes": 240}},
            "projection_request": {
                "projection": "FORMING_BASE_BAR",
                "warmup_bars": 35,
                "history_target_bars": 900,
            },
        }
        with patch("gui_market_data_host.QTimer.singleShot"):
            result = MarketDataHost.sync_candle_observation_targets(
                host,
                [requirement],
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(
            200_000,
            host._candle_observation_required_by_stock["005930"],
        )
        self.assertEqual(("005930",), host._candle_observation_stock_codes)
        self.assertEqual(("005930",), synced[-1])

    def test_production_standby_requirements_are_separate_and_feed_refresh_contract(self) -> None:
        host = SimpleNamespace(
            _candle_execution_required_by_stock={"005930": 7},
            _candle_observation_required_by_stock={"000660": 9},
            _candle_standby_required_by_stock={},
            _candle_standby_stock_codes=(),
            _execution_shadow_stock_codes=("005930",),
            _candle_observation_stock_codes=("000660",),
        )
        result = MarketDataHost.sync_candle_standby_requirements(host, [{
            "stock_code": "035420",
            "rules": {"bar": {"bar_minutes": 5}},
            "projection_request": {
                "projection": "FORMING_BASE_BAR",
                "warmup_bars": 35,
            },
        }])

        self.assertTrue(result["ok"])
        self.assertEqual({"005930": 7}, host._candle_execution_required_by_stock)
        self.assertEqual({"000660": 9}, host._candle_observation_required_by_stock)
        self.assertEqual(("035420",), host._candle_standby_stock_codes)
        self.assertEqual(
            193,
            MarketDataHost.candle_history_required_count(host, "035420"),
        )
        with patch("gui_market_data_host.StockRepository") as repository:
            repository.return_value.resolve_stock_dir.side_effect = (
                lambda code: Path("stocks") / code
            )
            targets = MarketDataHost.registered_operation_targets(host)
        self.assertEqual(
            ("000660", "005930", "035420"),
            tuple(code for _path, code, _name in targets),
        )

    def test_standby_sync_preserves_only_explicit_failed_current_codes(self) -> None:
        host = SimpleNamespace(
            _candle_standby_required_by_stock={"035420": 999, "000660": 777},
            _candle_standby_stock_codes=("000660", "035420"),
        )

        result = MarketDataHost.sync_candle_standby_requirements(
            host,
            [{
                "stock_code": "005930",
                "rules": {"bar": {"bar_minutes": 5}},
                "projection_request": {
                    "projection": "FORMING_BASE_BAR",
                    "warmup_bars": 35,
                },
            }],
            preserve_stock_codes=("035420",),
        )

        self.assertTrue(result["ok"])
        self.assertEqual({"005930": 193, "035420": 999}, host._candle_standby_required_by_stock)
        self.assertEqual(("035420",), result["preserved_stock_codes"])
        self.assertNotIn("000660", host._candle_standby_required_by_stock)

    def test_standby_sync_preserves_previous_requirement_when_current_item_is_invalid(self) -> None:
        host = SimpleNamespace(
            _candle_standby_required_by_stock={"035420": 999},
            _candle_standby_stock_codes=("035420",),
        )

        result = MarketDataHost.sync_candle_standby_requirements(
            host,
            [{
                "stock_code": "035420",
                "rules": {"bar": {"bar_minutes": 0}},
                "projection_request": {
                    "projection": "FORMING_BASE_BAR",
                    "warmup_bars": 35,
                },
            }],
        )

        self.assertFalse(result["ok"])
        self.assertEqual({"035420": 999}, host._candle_standby_required_by_stock)
        self.assertEqual(("035420",), result["preserved_stock_codes"])
        self.assertEqual("035420", result["errors"][0]["stock_code"])

    def test_execution_target_without_candle_requirement_is_not_a_refresh_target(self) -> None:
        host = SimpleNamespace(
            _candle_execution_required_by_stock={},
            _candle_observation_stock_codes=(),
            _candle_standby_stock_codes=(),
            _execution_shadow_stock_codes=("005930",),
        )
        with patch("gui_market_data_host.StockRepository") as repository:
            targets = MarketDataHost.registered_operation_targets(host)
        repository.assert_not_called()
        self.assertEqual((), targets)

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
