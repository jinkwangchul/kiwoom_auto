from __future__ import annotations

from collections import deque
import unittest
from unittest.mock import Mock, patch

from PyQt5.QtCore import QCoreApplication

from kiwoom_api import KiwoomApi, RealtimeShadowRegistrationSnapshot
from kiwoom_initial_market_snapshot import normalize_optkwfid_market_row
from kiwoom_realtime_fids import REALTIME_SHADOW_FIDS
from kiwoom_realtime_shadow import RealtimeShadowBarBuilder
from kiwoom_screen_allocator import (
    SCREEN_POOL_EXHAUSTED,
    KiwoomScreenAllocator,
    ScreenAllocationError,
)


class _Signal:
    def __init__(self) -> None:
        self.values = []

    def emit(self, value) -> None:
        self.values.append(value)

    def connect(self, callback) -> None:
        self.values.append(callback)


class _Control:
    def __init__(self) -> None:
        self.calls = []
        self.real_values = {
            20: "101501",
            10: "-1234",
            13: "9876",
            15: "+123",
            12: "+1.25",
            16: "-1200",
            17: "+1300",
            18: "-1100",
            30: "-12.43",
            228: "117.2",
        }
        self.raise_on_registration = False
        self.tr_rows = []

    def dynamicCall(self, signature, *args):
        self.calls.append((signature, args))
        if signature.startswith("GetConnectState"):
            return 1
        if signature.startswith("SetRealReg") and self.raise_on_registration:
            raise RuntimeError("registration failed")
        if signature.startswith("GetCommRealData"):
            return self.real_values.get(args[1], "")
        if signature.startswith("GetRepeatCnt"):
            return len(self.tr_rows)
        if signature.startswith("GetCommData"):
            return self.tr_rows[int(args[2])].get(str(args[3]), "")
        return 0


class _QAxControl(_Control):
    instances = []

    def __init__(self, _parent=None) -> None:
        super().__init__()
        self.OnEventConnect = _Signal()
        self.OnReceiveTrData = _Signal()
        self.OnReceiveRealData = _Signal()
        self.OnReceiveMsg = _Signal()
        self.OnReceiveChejanData = _Signal()
        self.instances.append(self)

    @staticmethod
    def setControl(_name):
        return True


def _api(*, connected: bool = True):
    api = KiwoomApi.__new__(KiwoomApi)
    api._control = _Control()
    api._available = True
    api._connected = connected
    api._login_requested = False
    api._login_session_id = "SESSION-1" if connected else ""
    api._connection_epoch = 1
    api.last_login_error = 0
    api.last_login_message = "connected" if connected else "disconnected"
    api._unavailable_reason = ""
    api._screen_allocator = KiwoomScreenAllocator()
    api._realtime_shadow_builder = RealtimeShadowBarBuilder()
    api._realtime_shadow_registration = api._empty_realtime_shadow_snapshot()
    api._realtime_receive_sequence = 0
    api._pending_tr = {}
    api._tr_request_queue = deque()
    api._tr_last_dispatch_monotonic_ms = None
    api._tr_governor_timer_scheduled = False
    api._tr_governor_dispatching = False
    api._tr_governor_total_enqueued = 0
    api._tr_governor_total_dispatched = 0
    api._tr_governor_last_rqname = ""
    api._tr_governor_last_trcode = ""
    api._tr_governor_dispatch_history = deque(maxlen=4096)
    api._tr_governor_last_queue_wait_ms = 0.0
    api._tr_governor_max_queue_wait_ms = 0.0
    api._tr_governor_timeout_count = 0
    api._tr_governor_stale_count = 0
    api._tr_governor_error_count = 0
    api._tr_governor_last_error_reason = ""
    api.realtime_shadow_tick_received = _Signal()
    api.realtime_shadow_bar_completed = _Signal()
    api.login_state_changed = _Signal()
    return api


class InitialMarketSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    @staticmethod
    def kw_calls(api):
        return [
            call
            for call in api._control.calls
            if call[0].startswith("CommKwRqData")
        ]

    def test_official_optkwfid_fields_are_normalized_without_fake_tick(self) -> None:
        api = _api()
        api._control.tr_rows = [{
            "종목코드": "A005930",
            "현재가": "-70,000",
            "등락율": "+1.25",
            "거래량": "123,456",
            "거래대금": "9,876,543",
            "시가총액": "4,321,000",
            "체결강도": "117.2",
            "전일거래량대비": "-12.43",
            "시가": "-69,000",
            "고가": "+71,000",
            "저가": "-68,000",
        }]
        callbacks = []
        with patch("kiwoom_api.QTimer.singleShot"):
            result = api.request_initial_market_snapshot(
                ["005930"], callback=callbacks.append
            )
        rqname = result["batches"][0]["rqname"]
        api._on_receive_tr_data("4000", rqname, "OPTKWFID", "", "0")

        self.assertEqual(1, len(self.kw_calls(api)))
        call = self.kw_calls(api)[0]
        self.assertEqual(
            ("005930", False, 1, 0),
            call[1][:4],
        )
        self.assertEqual(1, len(callbacks))
        row = callbacks[0]["rows"][0]
        self.assertEqual("005930", row["stock_code"])
        self.assertEqual((70000, 69000, 71000, 68000), (
            row["current_price"], row["open_price"],
            row["high_price"], row["low_price"],
        ))
        self.assertEqual((1.25, -12.43, 117.2, 123456), (
            row["change_rate"], row["previous_day_volume_rate"],
            row["execution_strength"], row["cumulative_volume"],
        ))
        self.assertEqual(
            (9876543, 4321000),
            (row["cumulative_trading_value"], row["market_capitalization"]),
        )
        self.assertEqual([], api.realtime_shadow_tick_received.values)

    def test_blank_invalid_values_are_unavailable_not_zero(self) -> None:
        normalized = normalize_optkwfid_market_row({
            "종목코드": "005930",
            "현재가": "",
            "등락율": "bad",
            "거래량": "nan",
            "거래대금": "bad",
            "시가총액": "",
            "체결강도": "",
            "전일거래량대비": "bad",
            "시가": "",
            "고가": "bad",
            "저가": "",
        })
        self.assertIsNotNone(normalized)
        self.assertTrue(all(
            getattr(normalized, field) is None
            for field in (
                "current_price", "open_price", "high_price", "low_price",
                "change_rate", "previous_day_volume_rate",
                "execution_strength", "cumulative_volume",
                "cumulative_trading_value", "market_capitalization",
            )
        ))

    def test_snapshot_batch_cost_is_bounded_for_contract_sizes(self) -> None:
        for stock_count, expected_batches in ((20, 1), (100, 1), (101, 2), (250, 3)):
            with self.subTest(stock_count=stock_count):
                api = _api()
                codes = [f"{index:06d}" for index in range(1, stock_count + 1)]
                with patch("kiwoom_api.QTimer.singleShot"):
                    result = api.request_initial_market_snapshot(codes)
                    while api._tr_request_queue:
                        api._tr_last_dispatch_monotonic_ms = None
                        api._tr_governor_timer_scheduled = False
                        api._drain_tr_governor()

                calls = self.kw_calls(api)
                self.assertEqual(expected_batches, result["batch_count"])
                self.assertEqual(expected_batches, len(calls))
                self.assertEqual(
                    [
                        min(100, stock_count - offset)
                        for offset in range(0, stock_count, 100)
                    ],
                    [call[1][2] for call in calls],
                )
                signatures = [call[0] for call in api._control.calls]
                self.assertFalse(any(name.startswith("SetRealReg") for name in signatures))
                self.assertFalse(any(name.startswith("SendOrder") for name in signatures))

    def test_mixed_numeric_and_alphanumeric_codes_share_one_snapshot_batch(self) -> None:
        api = _api()
        codes = ("005930", "0134X0", "0164H0", "0165X0")
        with patch("kiwoom_api.QTimer.singleShot"):
            result = api.request_initial_market_snapshot(codes)

        calls = self.kw_calls(api)
        self.assertEqual(1, result["batch_count"])
        self.assertEqual(1, len(calls))
        self.assertEqual((";".join(codes), False, 4, 0), calls[0][1][:4])

    def test_snapshot_response_preserves_alphanumeric_identity_and_wire_prefix_rule(self) -> None:
        for raw_code, expected in (
            ("A005930", "005930"),
            ("0134X0", "0134X0"),
            ("0164H0", "0164H0"),
            ("0165X0", "0165X0"),
            ("A0134X0", "0134X0"),
        ):
            with self.subTest(raw_code=raw_code):
                normalized = normalize_optkwfid_market_row(
                    {
                        "종목코드": raw_code,
                        "현재가": "12,345",
                        "등락율": "1.25",
                        "거래량": "100",
                        "거래대금": "200",
                        "시가총액": "300",
                        "체결강도": "110.5",
                        "전일거래량대비": "90.1",
                    }
                )
                self.assertIsNotNone(normalized)
                self.assertEqual(expected, normalized.stock_code)

    def test_one_hundred_and_one_codes_use_governed_batches_without_flood(self) -> None:
        api = _api()
        codes = [f"{index:06d}" for index in range(1, 102)]
        with patch("kiwoom_api.QTimer.singleShot"):
            result = api.request_initial_market_snapshot(codes)
            self.assertEqual(2, result["batch_count"])
            self.assertEqual(1, len(self.kw_calls(api)))
            self.assertEqual(1, api.tr_governor_metrics_snapshot().current_queue_depth)
            api._tr_last_dispatch_monotonic_ms = None
            api._tr_governor_timer_scheduled = False
            api._drain_tr_governor()

        calls = self.kw_calls(api)
        self.assertEqual(2, len(calls))
        self.assertEqual([100, 1], [call[1][2] for call in calls])
        metrics = api.tr_governor_metrics_snapshot()
        self.assertEqual((2, 2), (metrics.total_enqueued, metrics.total_dispatched))
        self.assertEqual("OPTKWFID", metrics.last_trcode)

    def test_old_session_response_is_rejected_and_counted_stale(self) -> None:
        api = _api()
        callbacks = []
        with patch("kiwoom_api.QTimer.singleShot"):
            result = api.request_initial_market_snapshot(
                ["005930"], callback=callbacks.append
            )
        rqname = result["batches"][0]["rqname"]
        api._connection_epoch = 2
        api._login_session_id = "SESSION-2"
        api._on_receive_tr_data("4000", rqname, "OPTKWFID", "", "0")

        self.assertEqual("STALE_BROKER_SESSION", callbacks[0]["error_kind"])
        self.assertEqual(1, api.tr_governor_metrics_snapshot().stale_count)
        self.assertNotIn(rqname, api._pending_tr)

    def test_timeout_releases_request_and_updates_governor_metrics(self) -> None:
        api = _api()
        callbacks = []
        with patch("kiwoom_api.QTimer.singleShot"):
            result = api.request_initial_market_snapshot(
                ["005930"], callback=callbacks.append
            )
        rqname = result["batches"][0]["rqname"]
        api._expire_initial_market_snapshot(rqname)

        self.assertEqual("TIMEOUT", callbacks[0]["error_kind"])
        self.assertEqual(1, api.tr_governor_metrics_snapshot().timeout_count)
        self.assertNotIn(rqname, api._pending_tr)


class RealtimeRegistrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def registration_calls(self, api):
        return [call for call in api._control.calls if call[0].startswith("SetRealReg")]

    def remove_calls(self, api):
        return [call for call in api._control.calls if call[0].startswith("SetRealRemove")]

    def test_disconnected_is_inactive_without_registration(self) -> None:
        api = _api(connected=False)
        result = api.sync_realtime_shadow_registration(["005930"])
        self.assertFalse(result["active"])
        self.assertEqual([], self.registration_calls(api))

    def test_qax_realtime_event_is_connected_once_during_api_setup(self) -> None:
        _QAxControl.instances.clear()
        with patch("kiwoom_api.QAxWidget", _QAxControl), patch(
            "kiwoom_api.QApplication.instance", return_value=object()
        ):
            api = KiwoomApi()
        control = _QAxControl.instances[0]
        self.assertEqual(1, len(control.OnReceiveRealData.values))
        self.assertIs(control.OnReceiveRealData.values[0].__self__, api)

    def test_one_hundred_and_one_stock_batching_and_official_fids(self) -> None:
        self.assertEqual((20, 10, 13, 15, 12, 16, 17, 18, 30, 228), REALTIME_SHADOW_FIDS)
        api = _api()
        first_hundred = [f"{index:06d}" for index in range(1, 101)]
        api.sync_realtime_shadow_registration(first_hundred)
        self.assertEqual(1, len(self.registration_calls(api)))
        self.assertEqual(100, len(self.registration_calls(api)[0][1][1].split(";")))

        api = _api()
        codes = [f"{index:06d}" for index in range(1, 102)]
        result = api.sync_realtime_shadow_registration(codes)
        calls = self.registration_calls(api)

        self.assertTrue(result["active"])
        self.assertEqual(2, len(calls))
        self.assertEqual([100, 1], [len(call[1][1].split(";")) for call in calls])
        self.assertEqual(
            ";".join(str(fid) for fid in REALTIME_SHADOW_FIDS),
            calls[0][1][2],
        )
        self.assertTrue(all(call[1][3] == "0" for call in calls))
        self.assertTrue(all(call[1][0].startswith("4") for call in calls))

    def test_same_target_is_idempotent_and_change_replaces_owned_screen(self) -> None:
        api = _api()
        first = api.sync_realtime_shadow_registration(["005930"])
        second = api.sync_realtime_shadow_registration(["005930"])
        third = api.sync_realtime_shadow_registration(["006400"])

        self.assertTrue(first["changed"])
        self.assertFalse(second["changed"])
        self.assertTrue(third["changed"])
        self.assertEqual(2, len(self.registration_calls(api)))
        self.assertEqual(1, len(self.remove_calls(api)))
        self.assertEqual("ALL", self.remove_calls(api)[0][1][1])

    def test_monitoring_registration_isolates_unsupported_code(self) -> None:
        api = _api()
        result = api.sync_realtime_monitoring_registration(["005930", "ABC123"])

        self.assertTrue(result["ok"])
        self.assertEqual(("ABC123",), result["unsupported_stock_codes"])
        self.assertEqual(("005930",), result["snapshot"].target_stock_codes)
        self.assertEqual(1, len(self.registration_calls(api)))

    def test_shadow_target_update_performs_no_broker_registration(self) -> None:
        api = _api()
        api.sync_realtime_monitoring_registration(["005930", "006400"])
        api._control.calls.clear()

        result = api.sync_realtime_shadow_targets(["005930"])

        self.assertTrue(result["changed"])
        self.assertEqual(("005930",), result["snapshot"].shadow_target_stock_codes)
        self.assertEqual([], self.registration_calls(api))
        self.assertEqual([], self.remove_calls(api))

    def test_empty_target_clears_without_empty_registration(self) -> None:
        api = _api()
        api.sync_realtime_shadow_registration(["005930"])
        result = api.sync_realtime_shadow_registration([])
        self.assertFalse(result["active"])
        self.assertEqual(1, len(self.registration_calls(api)))
        self.assertEqual(1, len(self.remove_calls(api)))

    def test_registration_or_screen_failure_rolls_back_inactive(self) -> None:
        api = _api()
        api._control.raise_on_registration = True
        failed = api.sync_realtime_shadow_registration(["005930"])
        self.assertFalse(failed["active"])
        self.assertFalse(api.realtime_shadow_registration_snapshot().active)

        api = _api()
        api._screen_allocator = Mock()
        api._screen_allocator.claim.side_effect = ScreenAllocationError(
            SCREEN_POOL_EXHAUSTED,
            "no screen",
        )
        api._screen_allocator.release = Mock()
        failed = api.sync_realtime_shadow_registration(["005930"])
        self.assertEqual("REALTIME_SHADOW_REGISTRATION_FAILED", failed["reason_code"])
        self.assertEqual([], self.registration_calls(api))

    def test_disconnect_invalidates_local_state_without_remove_assumption(self) -> None:
        api = _api()
        api.sync_realtime_shadow_registration(["005930"])
        api._invalidate_login_session(
            reason="test",
            emit=True,
            increment_epoch=True,
        )
        self.assertFalse(api.realtime_shadow_registration_snapshot().active)
        self.assertEqual([], self.remove_calls(api))
        self.assertEqual(2, api._connection_epoch)

        api._connected = True
        api._login_session_id = "SESSION-2"
        api.sync_realtime_shadow_registration(["005930"])
        self.assertEqual(2, len(self.registration_calls(api)))


class RealtimeHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api = _api()
        self.api.sync_realtime_shadow_registration(["005930"])
        self.api._control.calls.clear()

    def test_registered_stock_reads_exact_ten_fids_and_emits_tick(self) -> None:
        self.api._on_receive_real_data("005930", "주식체결", "unused")
        reads = [call for call in self.api._control.calls if call[0].startswith("GetCommRealData")]
        self.assertEqual(list(REALTIME_SHADOW_FIDS), [call[1][1] for call in reads])
        self.assertEqual(1, len(self.api.realtime_shadow_tick_received.values))
        tick = self.api.realtime_shadow_tick_received.values[0]
        self.assertEqual(1234, tick["current_price"])
        self.assertEqual(123, tick["trade_volume_raw"])
        self.assertEqual(123, tick["trade_volume_abs"])
        self.assertEqual(1, tick["receive_sequence"])
        self.assertEqual((1200, 1300, 1100), (
            tick["open_price"], tick["high_price"], tick["low_price"]
        ))
        self.assertEqual((1.25, -12.43, 117.2), (
            tick["change_rate"],
            tick["previous_day_volume_rate"],
            tick["execution_strength"],
        ))
        self.assertGreaterEqual(tick["received_monotonic"], 0)
        self.assertFalse(
            any(call[0].startswith("CommRqData") for call in self.api._control.calls)
        )

    def test_trade_volume_preserves_sell_sign_and_invalid_is_unavailable(self) -> None:
        self.api._control.real_values[15] = "-123"
        self.api._on_receive_real_data("005930", "주식체결", "")
        sell_tick = self.api.realtime_shadow_tick_received.values[-1]
        self.assertEqual(-123, sell_tick["trade_volume_raw"])
        self.assertEqual(123, sell_tick["trade_volume_abs"])

        self.api._control.real_values[15] = "bad"
        self.api._on_receive_real_data("005930", "주식체결", "")
        invalid_tick = self.api.realtime_shadow_tick_received.values[-1]
        self.assertIsNone(invalid_tick["trade_volume_raw"])
        self.assertIsNone(invalid_tick["trade_volume_abs"])

        self.api._control.real_values[15] = ""
        self.api._on_receive_real_data("005930", "주식체결", "")
        blank_tick = self.api.realtime_shadow_tick_received.values[-1]
        self.assertIsNone(blank_tick["trade_volume_raw"])
        self.assertIsNone(blank_tick["trade_volume_abs"])

    def test_receive_sequence_preserves_same_second_callback_order(self) -> None:
        for _ in range(3):
            self.api._on_receive_real_data("005930", "주식체결", "")
        self.assertEqual(
            [1, 2, 3],
            [
                tick["receive_sequence"]
                for tick in self.api.realtime_shadow_tick_received.values
            ],
        )

    def test_unregistered_wrong_type_malformed_and_stale_events_are_ignored(self) -> None:
        self.api._on_receive_real_data("006400", "주식체결", "")
        self.api._on_receive_real_data("005930", "주식호가잔량", "")
        self.api._control.real_values[20] = "bad"
        self.api._on_receive_real_data("005930", "주식체결", "")
        self.api._control.real_values[20] = "101501"
        self.api._login_session_id = "SESSION-2"
        self.api._on_receive_real_data("005930", "주식체결", "")
        self.assertEqual([], self.api.realtime_shadow_tick_received.values)

    def test_malformed_cumulative_keeps_price_tick_but_loses_volume(self) -> None:
        self.api._control.real_values[13] = "bad"
        self.api._on_receive_real_data("005930", "주식체결", "")
        tick = self.api.realtime_shadow_tick_received.values[0]
        self.assertEqual(1234, tick["current_price"])
        self.assertIsNone(tick["cumulative_volume"])

    def test_monitoring_tick_updates_observation_without_shadow_builder(self) -> None:
        api = _api()
        api._realtime_shadow_builder = Mock()
        api.sync_realtime_monitoring_registration(["005930"])
        api._on_receive_real_data("005930", "주식체결", "")
        self.assertEqual(1, len(api.realtime_shadow_tick_received.values))
        api._realtime_shadow_builder.accept_tick.assert_not_called()

        api.sync_realtime_shadow_targets(["005930"])
        api._realtime_shadow_builder.accept_tick.return_value = ("UPDATED", None)
        api._on_receive_real_data("005930", "주식체결", "")
        api._realtime_shadow_builder.accept_tick.assert_called_once()

    def test_optional_market_fields_blank_or_invalid_are_none(self) -> None:
        for fid in (12, 16, 17, 18, 30, 228):
            self.api._control.real_values[fid] = "" if fid % 2 else "bad"
        self.api._on_receive_real_data("005930", "주식체결", "")
        tick = self.api.realtime_shadow_tick_received.values[-1]
        for field in (
            "open_price",
            "high_price",
            "low_price",
            "change_rate",
            "previous_day_volume_rate",
            "execution_strength",
        ):
            self.assertIsNone(tick[field])


if __name__ == "__main__":
    unittest.main()
