from __future__ import annotations

import os
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PyQt5.QtWidgets import QApplication

import kiwoom_api
import gui_windows


class _Signal:
    def __init__(self) -> None:
        self.callbacks = []

    def connect(self, callback) -> None:
        self.callbacks.append(callback)


class _Control:
    def __init__(self, *, comm_connect_result: int = 0) -> None:
        self.OnEventConnect = _Signal()
        self.OnReceiveTrData = _Signal()
        self.OnReceiveChejanData = _Signal()
        self.comm_connect_result = comm_connect_result
        self.connect_state = 0
        self.calls: list[tuple[object, ...]] = []

    def setControl(self, name: str) -> bool:
        self.control_name = name
        return True

    def dynamicCall(self, *args):
        self.calls.append(args)
        signature = str(args[0])
        if signature == "CommConnect()":
            return self.comm_connect_result
        if signature == "GetConnectState()":
            return self.connect_state
        if signature == "GetLoginInfo(QString)":
            return ""
        return 0


class KiwoomLoginUacRejectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _create_api(self, control: _Control):
        processes: dict[int, str] = {}
        windows = {101}
        process_patch = patch.object(
            kiwoom_api,
            "_windows_process_names_by_pid",
            side_effect=lambda: dict(processes),
        )
        window_patch = patch.object(
            kiwoom_api,
            "_visible_top_level_window_handles_for_pid",
            side_effect=lambda _pid: frozenset(windows),
        )
        open_api_window_patch = patch.object(
            kiwoom_api,
            "_visible_open_api_login_window_handles",
            side_effect=lambda process_snapshot: (
                frozenset({202})
                if any(
                    str(name or "").lower() == "opstarter.exe"
                    for name in process_snapshot.values()
                )
                else frozenset()
            ),
        )
        qax_patch = patch.object(kiwoom_api, "QAxWidget", return_value=control)
        process_patch.start()
        window_patch.start()
        open_api_window_patch.start()
        qax_patch.start()
        self.addCleanup(process_patch.stop)
        self.addCleanup(window_patch.stop)
        self.addCleanup(open_api_window_patch.stop)
        self.addCleanup(qax_patch.stop)
        api = kiwoom_api.KiwoomApi()
        self.addCleanup(api._login_bootstrap_timer.stop)
        self.addCleanup(api._connection_observation_timer.stop)
        return api, processes, windows

    def _create_main_window(self, api: kiwoom_api.KiwoomApi):
        with (
            patch.object(gui_windows, "KiwoomApi", return_value=api),
            patch.object(gui_windows, "normalize_base_stock_single_routine_file"),
            patch.object(
                gui_windows.MainWindow,
                "refresh_startup_recovery_status",
                return_value={},
            ),
            patch.object(gui_windows.MainWindow, "refresh_all"),
            patch.object(gui_windows, "append_owner_event_once"),
        ):
            return gui_windows.MainWindow()

    def test_uac_rejection_resets_request_only_after_consent_lifecycle(self) -> None:
        control = _Control()
        api, processes, _windows = self._create_api(control)
        events = []
        api.login_state_changed.connect(events.append)

        result = api.login()
        api._login_bootstrap_timer.stop()
        self.assertEqual(0, result["result"])
        self.assertTrue(api._login_requested)

        processes[500] = "consent.exe"
        api._observe_login_bootstrap()
        self.assertTrue(api._login_requested)
        self.assertTrue(api._login_bootstrap_consent_observed)

        processes.clear()
        api._observe_login_bootstrap()
        api._observe_login_bootstrap()
        self.assertTrue(api._login_requested)
        api._observe_login_bootstrap()

        self.assertFalse(api._login_requested)
        self.assertFalse(api.is_connected())
        self.assertEqual(1, len(events))
        self.assertEqual("login_bootstrap_rejected", events[0]["status"])
        self.assertIsNone(events[0]["err_code"])
        self.assertNotIn("CommTerminate()", [call[0] for call in control.calls])

    def test_consent_already_present_before_commconnect_is_still_observed(self) -> None:
        control = _Control()
        api, processes, _windows = self._create_api(control)
        events = []
        api.login_state_changed.connect(events.append)
        processes[500] = "consent.exe"

        api.login()
        api._login_bootstrap_timer.stop()
        self.assertIn(500, api._login_bootstrap_baseline_consent_pids)
        api._observe_login_bootstrap()
        self.assertTrue(api._login_bootstrap_consent_observed)
        self.assertIn(500, api._login_bootstrap_observed_consent_pids)

        processes.clear()
        for _ in range(3):
            api._observe_login_bootstrap()

        self.assertFalse(api._login_requested)
        self.assertEqual(1, len(events))
        self.assertEqual("미연결 상태", events[0]["message"])

    def test_secure_desktop_return_detects_rejection_with_lingering_consent(self) -> None:
        control = _Control()
        api, processes, _windows = self._create_api(control)
        events = []
        api.login_state_changed.connect(events.append)
        processes[500] = "consent.exe"

        api.login()
        api._login_bootstrap_timer.stop()
        with patch.object(
            kiwoom_api,
            "_windows_input_desktop_name",
            return_value="Winlogon",
        ):
            api._observe_login_bootstrap()
        self.assertTrue(api._login_bootstrap_secure_desktop_observed)
        self.assertTrue(api._login_requested)

        with patch.object(
            kiwoom_api,
            "_windows_input_desktop_name",
            return_value="Default",
        ):
            for _ in range(3):
                api._observe_login_bootstrap()

        self.assertFalse(api._login_requested)
        self.assertEqual(1, len(events))
        self.assertEqual("login_bootstrap_rejected", events[0]["status"])

    def test_blocking_commconnect_probe_observes_secure_desktop(self) -> None:
        control = _Control()
        api, processes, _windows = self._create_api(control)
        events = []
        api.login_state_changed.connect(events.append)
        processes[500] = "consent.exe"
        secure_probe_called = threading.Event()
        original_dynamic_call = control.dynamicCall

        def dynamic_call(*args):
            if str(args[0]) == "CommConnect()":
                self.assertTrue(secure_probe_called.wait(timeout=1.0))
                return 0
            return original_dynamic_call(*args)

        def input_desktop_name():
            secure_probe_called.set()
            return "Winlogon"

        control.dynamicCall = dynamic_call
        with patch.object(
            kiwoom_api,
            "_windows_input_desktop_name",
            side_effect=input_desktop_name,
        ):
            result = api.login()
        api._login_bootstrap_timer.stop()

        self.assertEqual(0, result["result"])
        self.assertTrue(api._login_bootstrap_secure_desktop_observed)

        with patch.object(
            kiwoom_api,
            "_windows_input_desktop_name",
            return_value="Default",
        ):
            for _ in range(4):
                api._observe_login_bootstrap()

        self.assertFalse(api._login_requested)
        self.assertEqual(1, len(events))
        self.assertEqual("login_bootstrap_rejected", events[0]["status"])

    def test_secure_desktop_evidence_survives_consent_exit_before_first_timer_tick(self) -> None:
        control = _Control()
        api, processes, _windows = self._create_api(control)
        events = []
        api.login_state_changed.connect(events.append)
        secure_probe_called = threading.Event()
        original_dynamic_call = control.dynamicCall

        def dynamic_call(*args):
            if str(args[0]) == "CommConnect()":
                self.assertTrue(secure_probe_called.wait(timeout=1.0))
                processes.clear()
                return 0
            return original_dynamic_call(*args)

        def input_desktop_name():
            secure_probe_called.set()
            return "Winlogon"

        control.dynamicCall = dynamic_call
        processes[500] = "consent.exe"
        with patch.object(
            kiwoom_api,
            "_windows_input_desktop_name",
            side_effect=input_desktop_name,
        ):
            result = api.login()
        api._login_bootstrap_timer.stop()

        self.assertEqual(0, result["result"])
        self.assertTrue(api._login_bootstrap_secure_desktop_observed)
        self.assertFalse(api._login_bootstrap_consent_observed)

        with patch.object(
            kiwoom_api,
            "_windows_input_desktop_name",
            return_value="Default",
        ):
            for _ in range(3):
                api._observe_login_bootstrap()

        self.assertFalse(api._login_requested)
        self.assertEqual(1, len(events))
        self.assertEqual("login_bootstrap_rejected", events[0]["status"])

    def test_rejection_signal_restores_main_button_status_and_allows_retry(self) -> None:
        control = _Control()
        api, processes, _windows = self._create_api(control)
        window = self._create_main_window(api)
        signal_spy = Mock()
        api.login_state_changed.connect(signal_spy)
        try:
            processes[500] = "consent.exe"
            window.btn_kiwoom_login.click()
            api._login_bootstrap_timer.stop()
            self.assertEqual("로그인\n중...", window.btn_kiwoom_login.text())
            self.assertEqual("로그인 요청됨", window.statusBar().currentMessage())

            api._observe_login_bootstrap()
            processes.clear()
            with (
                patch.object(window, "_stop_production_recovery_timers") as stop_timers,
                patch.object(window, "_production_recovery_status_result"),
                patch.object(gui_windows.production_recovery_registry, "invalidate"),
            ):
                for _ in range(3):
                    api._observe_login_bootstrap()

            signal_spy.assert_called_once()
            self.assertFalse(api._login_requested)
            self.assertEqual("키움\n로그인", window.btn_kiwoom_login.text())
            self.assertTrue(window.btn_kiwoom_login.isEnabled())
            self.assertEqual("미연결 상태", window.statusBar().currentMessage())
            self.assertFalse(window.account_combo.isEnabled())
            self.assertEqual(0, window.account_combo.count())
            self.assertEqual(
                gui_windows.ACCOUNT_FUNDS_DISCONNECTED,
                window._account_funds_projection.snapshot.status,
            )
            stop_timers.assert_called_once_with()

            comm_connect_count = sum(
                1 for call in control.calls if call[0] == "CommConnect()"
            )
            window.btn_kiwoom_login.click()
            api._login_bootstrap_timer.stop()
            self.assertEqual(
                comm_connect_count + 1,
                sum(1 for call in control.calls if call[0] == "CommConnect()"),
            )
            self.assertFalse(
                any("SendOrder" in str(call[0]) for call in control.calls)
            )
            self.assertFalse(
                any("CommTerminate" in str(call[0]) for call in control.calls)
            )
        finally:
            with patch.object(
                window,
                "_confirm_main_window_exit_if_required",
                return_value=True,
            ):
                window.close()

    def test_uac_prompt_still_open_never_uses_elapsed_time_as_failure(self) -> None:
        control = _Control()
        api, processes, _windows = self._create_api(control)
        events = []
        api.login_state_changed.connect(events.append)
        api.login()
        api._login_bootstrap_timer.stop()

        processes[500] = "consent.exe"
        for _ in range(100):
            api._observe_login_bootstrap()

        self.assertTrue(api._login_requested)
        self.assertEqual([], events)

    def test_uac_approval_handoff_to_nkstarter_keeps_login_pending(self) -> None:
        control = _Control()
        api, processes, _windows = self._create_api(control)
        events = []
        api.login_state_changed.connect(events.append)
        api.login()
        api._login_bootstrap_timer.stop()

        processes[500] = "consent.exe"
        api._observe_login_bootstrap()
        processes.clear()
        processes[600] = "NKStarter.exe"
        api._observe_login_bootstrap()

        self.assertTrue(api._login_requested)
        self.assertEqual([], events)

        control.connect_state = 1
        api._on_event_connect(0)
        self.assertFalse(api._login_requested)
        self.assertTrue(api.is_connected())
        self.assertTrue(events[-1]["connected"])

    def test_uac_approval_handoff_to_opstarter_keeps_login_pending(self) -> None:
        control = _Control()
        api, processes, _windows = self._create_api(control)
        events = []
        api.login_state_changed.connect(events.append)
        api.login()
        api._login_bootstrap_timer.stop()

        processes[500] = "consent.exe"
        api._observe_login_bootstrap()
        processes.clear()
        api._observe_login_bootstrap()
        self.assertTrue(api._login_requested)

        processes[600] = "opstarter.exe"
        api._observe_login_bootstrap()

        self.assertTrue(api._login_requested)
        self.assertFalse(api._login_bootstrap_timer.isActive())
        self.assertEqual([], events)

        for _ in range(100):
            api._observe_login_bootstrap()
        self.assertTrue(api._login_requested)
        self.assertEqual([], events)

    def test_visible_login_window_prevents_uac_rejection_fallback(self) -> None:
        control = _Control()
        api, processes, windows = self._create_api(control)
        events = []
        api.login_state_changed.connect(events.append)
        api.login()
        api._login_bootstrap_timer.stop()

        processes[500] = "consent.exe"
        api._observe_login_bootstrap()
        processes.clear()
        windows.add(202)
        for _ in range(10):
            api._observe_login_bootstrap()

        self.assertTrue(api._login_requested)
        self.assertEqual([], events)

    def test_failed_event_and_immediate_commconnect_failure_clear_request(self) -> None:
        control = _Control()
        api, _processes, _windows = self._create_api(control)
        events = []
        api.login_state_changed.connect(events.append)
        api.login()
        api._login_bootstrap_timer.stop()
        api._on_event_connect(-101)
        self.assertFalse(api._login_requested)
        self.assertFalse(api.is_connected())
        self.assertEqual(-101, events[-1]["err_code"])

        failed_control = _Control(comm_connect_result=-1)
        failed_api, _processes2, _windows2 = self._create_api(failed_control)
        result = failed_api.login()
        self.assertFalse(result["ok"])
        self.assertFalse(failed_api._login_requested)

    def test_broker_session_initial_unavailable_disconnected_readiness(self) -> None:
        control = _Control()
        api, _processes, _windows = self._create_api(control)

        session = api.broker_session_snapshot()
        readiness = api.broker_readiness_snapshot()

        self.assertTrue(session.api_available)
        self.assertFalse(session.connected)
        self.assertFalse(session.login_requested)
        self.assertEqual("", session.login_session_id)
        self.assertEqual(0, session.connection_epoch)
        self.assertFalse(readiness.broker_request_ready)
        self.assertIn("DISCONNECTED", readiness.blockers)
        self.assertIn("LOGIN_SESSION_MISSING", readiness.blockers)

    def test_login_success_creates_session_epoch_and_broker_readiness(self) -> None:
        control = _Control()
        api, _processes, _windows = self._create_api(control)
        events = []
        api.login_state_changed.connect(events.append)

        control.connect_state = 1
        api._on_event_connect(0)
        session = api.broker_session_snapshot()
        readiness = api.broker_readiness_snapshot()

        self.assertTrue(session.connected)
        self.assertTrue(session.login_session_id)
        self.assertEqual(1, session.connection_epoch)
        self.assertTrue(readiness.api_available)
        self.assertTrue(readiness.connection_ready)
        self.assertTrue(readiness.login_session_ready)
        self.assertTrue(readiness.broker_request_ready)
        self.assertEqual((), readiness.blockers)
        self.assertEqual(1, events[-1]["connection_epoch"])
        self.assertEqual(session.login_session_id, events[-1]["login_session_id"])

    def test_disconnect_observation_invalidates_session_once(self) -> None:
        control = _Control()
        api, _processes, _windows = self._create_api(control)
        events = []
        api.login_state_changed.connect(events.append)

        control.connect_state = 1
        api._on_event_connect(0)
        first_session = api.login_session_id()
        self.assertEqual(1, api.broker_session_snapshot().connection_epoch)

        control.connect_state = 0
        self.assertFalse(api.is_connected())
        disconnected = api.broker_session_snapshot()
        self.assertEqual("", disconnected.login_session_id)
        self.assertEqual(2, disconnected.connection_epoch)
        self.assertFalse(api.broker_readiness_snapshot().broker_request_ready)

        api.is_connected()
        api._observe_broker_connection()
        self.assertEqual(2, api.broker_session_snapshot().connection_epoch)
        disconnect_events = [event for event in events if not event.get("connected")]
        self.assertEqual(1, len(disconnect_events))
        self.assertEqual(2, disconnect_events[0]["connection_epoch"])
        self.assertNotEqual("", first_session)

    def test_reconnect_creates_new_session_and_advances_epoch(self) -> None:
        control = _Control()
        api, _processes, _windows = self._create_api(control)

        control.connect_state = 1
        api._on_event_connect(0)
        first_session = api.login_session_id()
        control.connect_state = 0
        api.is_connected()
        control.connect_state = 1
        api._on_event_connect(0)

        session = api.broker_session_snapshot()
        self.assertEqual(3, session.connection_epoch)
        self.assertTrue(session.login_session_id)
        self.assertNotEqual(first_session, session.login_session_id)
        self.assertTrue(api.broker_readiness_snapshot().broker_request_ready)

    def test_login_failure_without_established_session_does_not_create_epoch(self) -> None:
        control = _Control()
        api, _processes, _windows = self._create_api(control)
        events = []
        api.login_state_changed.connect(events.append)

        api._on_event_connect(-101)
        session = api.broker_session_snapshot()

        self.assertFalse(session.connected)
        self.assertEqual("", session.login_session_id)
        self.assertEqual(0, session.connection_epoch)
        self.assertFalse(api.broker_readiness_snapshot().broker_request_ready)
        self.assertEqual(0, events[-1]["connection_epoch"])

    def test_get_connect_state_exception_does_not_create_disconnect_transition(self) -> None:
        class RaisingControl(_Control):
            def dynamicCall(self, *args):
                if str(args[0]) == "GetConnectState()":
                    raise RuntimeError("connect state unavailable")
                return super().dynamicCall(*args)

        control = RaisingControl()
        api, _processes, _windows = self._create_api(control)
        events = []
        api.login_state_changed.connect(events.append)
        api._connected = True
        api._login_session_id = "SESSION-A"
        api._connection_epoch = 7

        self.assertTrue(api.is_connected())
        session = api.broker_session_snapshot()
        self.assertTrue(session.connected)
        self.assertEqual("SESSION-A", session.login_session_id)
        self.assertEqual(7, session.connection_epoch)
        self.assertEqual([], events)

    def test_readiness_is_not_trading_or_order_permission(self) -> None:
        control = _Control()
        api, _processes, _windows = self._create_api(control)
        control.connect_state = 1
        api._on_event_connect(0)
        readiness = api.broker_readiness_snapshot()

        self.assertTrue(readiness.broker_request_ready)
        self.assertFalse(hasattr(readiness, "trading_ready"))
        self.assertFalse(hasattr(readiness, "order_permission"))

    def test_login_state_changed_keeps_compatible_payload_keys(self) -> None:
        control = _Control()
        api, _processes, _windows = self._create_api(control)
        events = []
        api.login_state_changed.connect(events.append)

        control.connect_state = 1
        api._on_event_connect(0)
        control.connect_state = 0
        api.is_connected()

        for event in events:
            self.assertIn("connected", event)
            self.assertIn("err_code", event)
            self.assertIn("message", event)

        self.assertIn("connection_epoch", events[-1])
        self.assertIn("login_session_id", events[-1])


if __name__ == "__main__":
    unittest.main()
