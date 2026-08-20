from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from PyQt5.QtCore import QCoreApplication

from kiwoom_api import KiwoomApi, RealtimeShadowRegistrationSnapshot
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
        self.real_values = {20: "101501", 10: "-1234", 13: "9876"}
        self.raise_on_registration = False

    def dynamicCall(self, signature, *args):
        self.calls.append((signature, args))
        if signature.startswith("SetRealReg") and self.raise_on_registration:
            raise RuntimeError("registration failed")
        if signature.startswith("GetCommRealData"):
            return self.real_values[args[1]]
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
    api.realtime_shadow_tick_received = _Signal()
    api.realtime_shadow_bar_completed = _Signal()
    api.login_state_changed = _Signal()
    return api


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

    def test_registered_stock_reads_exact_three_fids_and_emits_tick(self) -> None:
        self.api._on_receive_real_data("005930", "주식체결", "unused")
        reads = [call for call in self.api._control.calls if call[0].startswith("GetCommRealData")]
        self.assertEqual(list(REALTIME_SHADOW_FIDS), [call[1][1] for call in reads])
        self.assertEqual(1, len(self.api.realtime_shadow_tick_received.values))
        self.assertEqual(1234, self.api.realtime_shadow_tick_received.values[0]["current_price"])

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


if __name__ == "__main__":
    unittest.main()
