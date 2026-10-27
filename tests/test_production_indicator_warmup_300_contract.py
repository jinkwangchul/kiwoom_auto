"""Production Indicator Follow history target is bounded independently of V5 display."""
from __future__ import annotations

import unittest
from datetime import date, datetime
from types import SimpleNamespace

import pytest

import gui_market_data_host as host_module
from candle_timeframe_aggregation import SEOUL_TIMEZONE
from gui_market_data_host import MarketDataHost
from stock_market_source_plan import initialize_stock_market_source_plan
from routines.지표추종매매.routine import market_bar_projection_request


@pytest.fixture(autouse=True)
def native_source_plans(tmp_path, monkeypatch):
    monkeypatch.setattr(host_module, "PROJECT_ROOT", tmp_path)
    for code, nxt_available in (("005930", False), ("012210", False),
                                ("000660", True)):
        initialize_stock_market_source_plan(
            tmp_path, code, code, trading_day=date(2026, 10, 27),
            decided_at=datetime(2026, 10, 27, 8, tzinfo=SEOUL_TIMEZONE),
            nxt_available=nxt_available,
        )
    return tmp_path


class ProductionIndicatorWarmup300ContractTests(unittest.TestCase):
    def test_native_same_day_gap_requests_overlap_without_changing_cache(self) -> None:
        bars = [{"timestamp": "2026-10-27 09:00"}] * 299 + [
            {"timestamp": "2026-10-27 10:00"}
        ]
        host = SimpleNamespace(
            _candle_standby_timeframe_requirements_by_stock={"005930": {3: 300}},
            _direct_timeframe_candles={("005930", 3): bars},
        )
        before = list(bars)
        specs = MarketDataHost.direct_timeframe_refresh_specs(
            host, "005930", "2026-10-27 10:18"
        )
        self.assertEqual(7, specs[0]["request_count"])
        self.assertEqual(before, bars)
        self.assertIs(before[-1], bars[-1])
        self.assertEqual(
            (), MarketDataHost.direct_timeframe_refresh_specs(
                host, "005930", "2026-10-27 10:03"
            ),
        )

    def test_native_next_day_backfill_and_same_day_cap(self) -> None:
        bars = [{"timestamp": "2026-10-26 15:27"} for _ in range(300)]
        host = SimpleNamespace(
            _candle_standby_timeframe_requirements_by_stock={"005930": {3: 300}},
            _direct_timeframe_candles={("005930", 3): bars},
        )
        self.assertEqual(
            300, MarketDataHost.direct_timeframe_refresh_specs(
                host, "005930", "2026-10-27 09:06"
            )[0]["request_count"],
        )
        bars[-1] = {"timestamp": "2026-10-27 09:00"}
        host._candle_standby_timeframe_requirements_by_stock["005930"][3] = 100
        self.assertEqual(
            101, MarketDataHost.direct_timeframe_refresh_specs(
                host, "005930", "2026-10-27 15:30"
            )[0]["request_count"],
        )

    def test_verified_native_standby_history_does_not_repeat_full_fetch(self) -> None:
        host = SimpleNamespace(
            _candle_standby_timeframe_requirements_by_stock={"005930": {3: 300}},
            _direct_timeframe_candles={},
        )
        minute_key = "2026-10-27 08:00"
        first = MarketDataHost.direct_timeframe_refresh_specs(host, "005930", minute_key)
        self.assertEqual(300, first[0]["required_count"])
        self.assertEqual(301, first[0]["request_count"])
        host._direct_timeframe_candles[("005930", 3)] = [{} for _ in range(300)]
        self.assertEqual(
            (), MarketDataHost.direct_timeframe_refresh_specs(host, "005930", minute_key)
        )

    def test_sor_m3_uses_al_display_history_without_krx_direct(self) -> None:
        host = SimpleNamespace(
            _candle_standby_timeframe_requirements_by_stock={"000660": {3: 300}},
            _direct_timeframe_candles={},
        )
        for minute_key in ("2026-10-27 10:00", "2026-10-27 20:01"):
            self.assertEqual(
                (), MarketDataHost.direct_timeframe_refresh_specs(host, "000660", minute_key)
            )
            specs = MarketDataHost.display_timeframe_refresh_specs(
                host, "000660", minute_key
            )
            self.assertEqual(1, len(specs))
            self.assertEqual((3, 300, 301, True), (
                specs[0]["timeframe_minutes"], specs[0]["required_count"],
                specs[0]["request_count"], specs[0]["nxt_available"],
            ))

    def test_krx_m3_uses_only_bare_direct_history_after_hours(self) -> None:
        host = SimpleNamespace(
            _candle_standby_timeframe_requirements_by_stock={"012210": {3: 300}},
            _direct_timeframe_candles={},
        )
        for minute_key in ("2026-10-27 10:00", "2026-10-27 20:01"):
            specs = MarketDataHost.direct_timeframe_refresh_specs(
                host, "012210", minute_key
            )
            self.assertEqual((3, 300, 301), (
                specs[0]["timeframe_minutes"], specs[0]["required_count"],
                specs[0]["request_count"],
            ))
            self.assertEqual(
                (), MarketDataHost.display_timeframe_refresh_specs(
                    host, "012210", minute_key
                )
            )

    def test_missing_source_plan_fails_closed(self) -> None:
        host = SimpleNamespace(
            _candle_standby_timeframe_requirements_by_stock={"122350": {3: 300}},
        )
        self.assertEqual(
            (), MarketDataHost.direct_timeframe_refresh_specs(
                host, "122350", "2026-10-27 20:01"
            )
        )
        self.assertEqual(
            (), MarketDataHost.display_timeframe_refresh_specs(
                host, "122350", "2026-10-27 20:01"
            )
        )

    def test_confirmed_al_history_does_not_repeat_full_request(self) -> None:
        key = ("000660", 3)
        host = SimpleNamespace(
            kiwoom_api=SimpleNamespace(broker_session_snapshot=lambda: SimpleNamespace(
                connected=True, connection_epoch=7, login_session_id="offline"
            )),
            _candle_standby_timeframe_requirements_by_stock={"000660": {3: 300}},
            _display_timeframe_session_identity={key: (7, "offline")},
            _display_timeframe_candles={key: [
                {"timestamp": "2026-10-27 19:58"} for _ in range(300)
            ]},
        )
        self.assertEqual(
            (), MarketDataHost.display_timeframe_refresh_specs(
                host, "000660", "2026-10-27 20:01"
            )
        )
        self.assertEqual(
            (), MarketDataHost.direct_timeframe_refresh_specs(
                host, "000660", "2026-10-27 20:01"
            )
        )

    def test_live_rsi_declares_300_timeframe_history_without_v5_preload(self) -> None:
        rules = {
            "bar": {"bar_minutes": 5},
            "indicators": {"rsi": {"period": 14}},
            "buy": {"enabled": False},
            "sell": {
                "enabled": True,
                "signals": {
                    "rsi": {
                        "enabled": True,
                        "order_delay_bars": 0,
                        "groups": [{
                            "enabled": True,
                            "conditions": [{
                                "target": "RSI", "period": 14,
                                "operator": "<=", "value": 30,
                            }],
                        }],
                    },
                },
            },
        }
        request = market_bar_projection_request(rules)
        self.assertEqual(15, request["warmup_bars"])
        self.assertEqual(300, request["history_target_bars"])
        interval, direct_bars, minute_source = MarketDataHost._direct_timeframe_requirement(
            rules, request,
        )
        self.assertEqual((5, 300), (interval, direct_bars))
        self.assertLess(minute_source, 5000)

    def test_active_filter_longer_than_300_remains_sufficient(self) -> None:
        rules = {
            "bar": {"bar_minutes": 3},
            "buy": {
                "enabled": True,
                "delay_bar": 0,
                "groups": [{
                    "enabled": True,
                    "conditions": [{
                        "enabled": True, "target": "MA",
                        "period": 420, "operator": "TURN_UP",
                    }],
                }],
            },
            "sell": {"enabled": False, "signals": {}},
        }
        request = market_bar_projection_request(rules)
        self.assertGreaterEqual(request["warmup_bars"], 420)
        self.assertEqual(request["warmup_bars"], request["history_target_bars"])


if __name__ == "__main__":
    unittest.main()
