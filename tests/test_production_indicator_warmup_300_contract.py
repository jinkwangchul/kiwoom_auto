"""Production Indicator Follow history target is bounded independently of V5 display."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from gui_market_data_host import MarketDataHost
from routines.지표추종매매.routine import market_bar_projection_request


class ProductionIndicatorWarmup300ContractTests(unittest.TestCase):
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
