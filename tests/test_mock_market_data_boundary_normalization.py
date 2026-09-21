# -*- coding: utf-8 -*-

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from mock_validation_candle_provider import project_mock_routine_candles
from mock_validation_host import MockValidationHost


SEOUL = timezone(timedelta(hours=9))


class _Signal:
    def connect(self, _callback) -> None:
        pass

    def disconnect(self, _callback) -> None:
        pass


class _Api:
    def __init__(self) -> None:
        self.mock_orderbook_received = _Signal()
        self.realtime_shadow_tick_received = _Signal()
        self.login_state_changed = _Signal()

    def sync_mock_orderbook_registration(self, targets):
        return {
            "ok": True,
            "snapshot": {
                "active": False,
                "connection_epoch": 0,
                "login_session_id": "",
                "target_stock_codes": tuple(targets),
            },
        }

    def clear_mock_orderbook_registration(self, *, reason: str):
        return {"ok": True, "reason": reason}

    def current_realtime_shadow_bar(self, _stock_code):
        return None


def _production_projection(self, **_kwargs):
    self.projection_calls += 1
    return {"available": True, "candles": []}


def _production_updater(self, _targets):
    self.update_calls += 1
    self.targets = ("MUTATED",)


ProductionMarketDataHost = type(
    "MarketDataHost",
    (),
    {
        "__module__": "gui_market_data_host",
        "project_routine_candles": _production_projection,
        "sync_candle_observation_targets": _production_updater,
    },
)


class MockMarketDataBoundaryNormalizationTest(unittest.TestCase):
    def test_production_market_data_callbacks_are_not_used_by_mock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            production = ProductionMarketDataHost()
            production.projection_calls = 0
            production.update_calls = 0
            production.targets = ("PRODUCTION",)
            host = MockValidationHost(
                _Api(),
                project_root=root,
                candles_provider=production.project_routine_candles,
                candle_observation_updater=production.sync_candle_observation_targets,
                now_factory=lambda: datetime(2026, 9, 12, 9, 10, tzinfo=SEOUL),
            )
            try:
                host.sync_registration()
                self.assertIsNone(host._candle_observation_updater)
                self.assertIsNot(
                    getattr(host._candles_provider, "__self__", None),
                    production,
                )
            finally:
                host.dispose()
            self.assertEqual(("PRODUCTION",), production.targets)
            self.assertEqual(0, production.update_calls)
            self.assertEqual(0, production.projection_calls)

    def test_mock_owned_candle_projection_reads_without_production_host(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stock_dir = root / "stocks" / "005930_TEST"
            stock_dir.mkdir(parents=True)
            (stock_dir / "candles.json").write_text(
                json.dumps(
                    [
                        {
                            "timestamp": "2026-09-12T09:00:00+09:00",
                            "open": 100,
                            "high": 101,
                            "low": 99,
                            "close": 100,
                            "volume": 10,
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = project_mock_routine_candles(
                project_root=root,
                api=_Api(),
                stock_code="005930",
                rules={"bar": {"bar_minutes": 1}},
                projection_request={"projection": "COMPLETED_TIMEFRAME"},
                as_of=datetime(2026, 9, 12, 9, 2, tzinfo=SEOUL),
                session_windows=(
                    {"name": "regular", "start_time": "09:00:00", "end_time": "15:20:00"},
                ),
            )
            self.assertTrue(result["available"])
            self.assertEqual(1, len(result["candles"]))
            self.assertEqual(100, result["candles"][0]["close"])


if __name__ == "__main__":
    unittest.main()
