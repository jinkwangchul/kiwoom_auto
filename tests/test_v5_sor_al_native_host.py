"""Host admission stays behind a broker-TR parity receipt."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from unittest.mock import Mock

from PyQt5.QtCore import QObject

import gui_market_data_host as module
from tests.qt_test_support import ensure_qapplication


KST = timezone(timedelta(hours=9))


class Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)


class Api:
    def __init__(self):
        self.bar_committed = Signal()
        self.realtime_shadow_bar_completed = Signal()
        self.realtime_shadow_tick_received = Signal()
        self.nxt_display_tick_received = Signal()
        self.v5_al_tick_received = Signal()
        self.sync_v5_al_realtime_targets = Mock(return_value={"ok": True})

    def broker_session_snapshot(self):
        return SimpleNamespace(connected=True, connection_epoch=7,
                               login_session_id="session-7")


class Clock(datetime):
    observed = datetime(2026, 10, 23, 9, 3, 15, tzinfo=KST)

    @classmethod
    def now(cls, tz=None):
        value = cls.observed
        return value if tz is None else value.astimezone(tz)


class NativeHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = ensure_qapplication()

    def setUp(self):
        self.host = module.MarketDataHost(QObject(), Api(), None)
        self.host._v5_observation_leases[1] = "005930"
        self.events = []
        self.host.market_data_observed.connect(self.events.append)

    def tick(self, minute, second, price, sequence, *, code="005930", epoch=7):
        stamp = datetime(2026, 10, 23, 9, minute, second, tzinfo=KST)
        Clock.observed = stamp
        self.host._on_v5_al_tick_received({
            "stock_code": code, "broker_market_identity": f"{code}_AL",
            "market_source": "SOR", "v5_only": True,
            "connection_epoch": epoch, "login_session_id": "session-7",
            "market_datetime": stamp.isoformat(), "received_at": stamp.isoformat(),
            "current_price": price, "receive_sequence": sequence,
        })

    def test_fallback_until_closed_tr_matches_then_native_forming_only(self):
        with patch.object(module, "datetime", Clock):
            self.tick(1, 1, 100, 1)
            self.tick(2, 1, 100, 2)
            self.tick(2, 2, 102, 3)
            self.tick(2, 3, 99, 4)
            self.tick(2, 4, 101, 5)
            self.tick(3, 1, 101, 6)
            self.host._sor_forming_m1_by_stock = {"005930": {
                "session_identity": (7, "session-7"),
                "candle": {
                    "bar_time": "2026-10-23T09:03:00+09:00",
                    "open": 101, "high": 101, "low": 101, "close": 101,
                    "volume": 4, "snapshot_source": "BROKER_AL_TR_FORMING_M1",
                    "market_data_identity": "005930_AL", "market_source": "INTEGRATED",
                    "display_only": True, "is_complete": False, "timeframe_minutes": 1,
                    "snapshot_received_at": "2026-10-23T09:03:05+09:00",
                },
            }}
            self.tick(3, 4, 102, 7)
            self.assertEqual(self.host._sor_forming_m1_by_stock["005930"]["candle"]["snapshot_source"],
                             "BROKER_AL_TR_FORMING_M1")
            broker = {"bar_time": "2026-10-23T09:02:00+09:00",
                      "open": 100, "high": 102, "low": 99, "close": 101,
                      "market_data_identity": "005930_AL", "market_source": "INTEGRATED",
                      "is_complete": True}
            self.host._v5_al_consider_parity("005930", [{**broker, "high": 103}])
            self.assertNotIn("005930", self.host._v5_al_parity)
            self.host._v5_al_consider_parity("005930", [broker])
            self.assertEqual(self.host._v5_al_parity["005930"], (7, "session-7"))
            self.tick(3, 6, 103, 8)
            candle = self.host._sor_forming_m1_by_stock["005930"]["candle"]
            self.assertEqual(candle["snapshot_source"], "BROKER_AL_PARITY_NATIVE_FORMING_M1")
            self.assertEqual((candle["open"], candle["high"], candle["close"], candle["volume"]),
                             (101, 103, 103, 4))
            self.assertEqual(candle["volume_provenance"], "LAST_BROKER_AL_TR_SNAPSHOT")
            self.assertEqual([event["source"] for event in self.events],
                             ["SOR_AL_NATIVE_M1_PARITY_ACCEPTED"])
            self.tick(3, 7, 104, 9, epoch=8)
            self.assertEqual(candle["close"], 103)

    def test_two_window_leases_keep_one_broker_target_until_last_release(self):
        self.host._v5_observation_leases.clear()
        with patch.object(module.MarketDataHost, "sync_monitoring_targets",
                          return_value={"ok": True}), patch.object(
                          module.MarketDataHost, "_nxt_eligible_targets",
                          side_effect=lambda codes: tuple(sorted(codes))), patch.object(
                          module.MarketDataHost, "_schedule_v5_observation_watch"):
            self.host.set_v5_observation_lease(1, "005930")
            self.host.set_v5_observation_lease(2, "005930")
            self.host.set_v5_observation_lease(1, None)
            self.host.set_v5_observation_lease(2, None)
        calls = [call.args[0] for call in
                 self.host.kiwoom_api.sync_v5_al_realtime_targets.call_args_list]
        self.assertEqual(calls, [("005930",), ("005930",), ("005930",), ()])

    def test_periodic_watch_rearms_native_lease_after_reconnect(self):
        self.host.kiwoom_api.broker_session_snapshot = lambda: SimpleNamespace(
            connected=True, connection_epoch=8, login_session_id="session-8")
        Clock.observed = datetime(2026, 10, 23, 9, 3, 15, tzinfo=KST)
        with patch.object(module, "datetime", Clock), patch.object(
            module.MarketDataHost, "_nxt_eligible_targets", return_value=("005930",)
        ), patch.object(module.MarketDataHost, "display_timeframe_refresh_specs",
                        return_value=()):
            self.host._refresh_v5_observation()
        self.host.kiwoom_api.sync_v5_al_realtime_targets.assert_called_once_with(("005930",))


if __name__ == "__main__":
    unittest.main()
