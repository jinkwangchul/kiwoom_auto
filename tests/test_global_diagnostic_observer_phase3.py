from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import event_journal_production as production
from event_journal_reader import EventJournalReader
from event_journal_writer import EventJournalWriter
import gui_auto_trade_operation_host as operation_host
import gui_auto_trade_timer as operation_timer
import gui_main
import gui_windows
import kiwoom_api
import routine_signal_probe


class GlobalDiagnosticObserverPhase3Test(unittest.TestCase):
    def test_exception_projection_is_sanitized_and_path_free(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = EventJournalWriter(Path(temp_dir) / "journal")
            try:
                raise RuntimeError(
                    "password=hunter2 account=8129123456 at "
                    "C:\\Users\\JIN KWANG CHUL\\secret.py"
                )
            except RuntimeError:
                exc_type, exc_value, exc_traceback = sys.exc_info()
            with patch.object(production, "_WRITER", writer):
                result = production.observe_production_exception(
                    exc_type,
                    exc_value,
                    exc_traceback,
                    component="test_component",
                    operation="run",
                    source="tests.phase3",
                    target_name="테스트 경계",
                )

            self.assertTrue(result["appended"])
            event = EventJournalReader(writer.journal_dir).read_events()["events"][0]
            serialized = str(event)
            self.assertNotIn("hunter2", serialized)
            self.assertNotIn("8129123456", serialized)
            self.assertNotIn("JIN KWANG CHUL", serialized)
            self.assertNotIn("Traceback (most recent call last)", serialized)
            self.assertEqual(24, len(event["stack_fingerprint"]))

    def test_exception_projection_writer_failure_is_fail_open(self) -> None:
        writer = Mock()
        writer.append_event.side_effect = RuntimeError("journal unavailable")
        with patch.object(production, "_WRITER", writer):
            result = production.observe_production_exception(
                RuntimeError,
                RuntimeError("failure"),
                None,
                component="test",
                operation="write",
                source="tests.phase3",
            )
        self.assertFalse(result["appended"])
        self.assertTrue(result["write_failed"])

    def test_global_hooks_preserve_existing_hooks_and_install_once(self) -> None:
        original_sys = sys.excepthook
        original_thread = threading.excepthook
        previous_sys = Mock()
        previous_thread = Mock()
        try:
            sys.excepthook = previous_sys
            threading.excepthook = previous_thread
            production._GLOBAL_EXCEPTION_HOOKS_INSTALLED = False
            production._PREVIOUS_SYS_EXCEPTHOOK = None
            production._PREVIOUS_THREADING_EXCEPTHOOK = None
            with patch.object(production, "observe_production_exception") as observer:
                first = production.install_global_exception_observers()
                sys_wrapper = sys.excepthook
                thread_wrapper = threading.excepthook
                second = production.install_global_exception_observers()
                error = RuntimeError("boom")
                sys_wrapper(RuntimeError, error, None)
                args = SimpleNamespace(
                    exc_type=RuntimeError,
                    exc_value=error,
                    exc_traceback=None,
                    thread=SimpleNamespace(name="worker"),
                )
                thread_wrapper(args)
            self.assertTrue(first["installed"])
            self.assertTrue(second["duplicate"])
            self.assertIs(sys_wrapper, sys.excepthook)
            self.assertIs(thread_wrapper, threading.excepthook)
            self.assertEqual(2, observer.call_count)
            previous_sys.assert_called_once_with(RuntimeError, error, None)
            previous_thread.assert_called_once_with(args)
        finally:
            sys.excepthook = original_sys
            threading.excepthook = original_thread
            production._GLOBAL_EXCEPTION_HOOKS_INSTALLED = False
            production._PREVIOUS_SYS_EXCEPTHOOK = None
            production._PREVIOUS_THREADING_EXCEPTHOOK = None

    def test_hook_observer_failure_does_not_replace_existing_hook(self) -> None:
        original_sys = sys.excepthook
        previous_sys = Mock()
        try:
            sys.excepthook = previous_sys
            production._GLOBAL_EXCEPTION_HOOKS_INSTALLED = False
            with patch.object(
                production,
                "observe_production_exception",
                side_effect=RuntimeError("observer failed"),
            ):
                production.install_global_exception_observers()
                sys.excepthook(RuntimeError, RuntimeError("production"), None)
            previous_sys.assert_called_once()
        finally:
            sys.excepthook = original_sys
            threading.excepthook = production._PREVIOUS_THREADING_EXCEPTHOOK or threading.excepthook
            production._GLOBAL_EXCEPTION_HOOKS_INSTALLED = False

    def test_gui_main_keeps_existing_error_result_and_modal(self) -> None:
        app = Mock()
        with patch.object(gui_main, "install_global_exception_observers"), patch.object(
            gui_main, "QApplication", return_value=app
        ), patch.object(gui_main, "MainWindow", side_effect=RuntimeError("main failed")), patch.object(
            gui_main, "observe_production_exception"
        ) as observer, patch.object(gui_main.QMessageBox, "critical") as critical:
            result = gui_main.main()
        self.assertEqual(1, result)
        observer.assert_called_once()
        critical.assert_called_once()
        app.exec_.assert_not_called()

    def test_operation_cycle_final_exception_keeps_result(self) -> None:
        fake = SimpleNamespace(
            _shutting_down=False,
            _operation_cycle_running=False,
            operation_cycle_completed=SimpleNamespace(emit=Mock()),
            statusBarMessage=Mock(),
        )
        with patch.object(
            operation_timer,
            "auto_trade_run_operation_cycle",
            side_effect=RuntimeError("cycle failed"),
        ), patch.object(operation_host, "observe_production_exception") as observer:
            result = operation_host.AutoTradeOperationHost.run_operation_cycle(fake)
        self.assertFalse(result["processed"])
        self.assertEqual("OPERATION_CYCLE_FAILED", result["reason_code"])
        observer.assert_called_once()

    def test_signal_cycle_failure_transition_dedupes_and_resets(self) -> None:
        owner = SimpleNamespace(statusBarMessage=Mock())

        def fail_probe(*_args, **_kwargs):
            raise RuntimeError("probe failed")

        with patch.object(operation_timer, "probe_all_enabled_routine_stocks_once", fail_probe), patch.object(
            production, "append_production_event", return_value={"appended": True}
        ) as append:
            operation_timer._auto_trade_run_signal_cycle(owner, "2026-08-16 09:00")
            operation_timer._auto_trade_run_signal_cycle(owner, "2026-08-16 09:01")
            self.assertEqual(1, append.call_count)
            with patch.object(
                operation_timer,
                "probe_all_enabled_routine_stocks_once",
                return_value={"logged": 0, "error": 0},
            ), patch.object(operation_timer, "consume_pending_routine_signals_dry_run", None):
                operation_timer._auto_trade_run_signal_cycle(owner, "2026-08-16 09:02")
            operation_timer._auto_trade_run_signal_cycle(owner, "2026-08-16 09:03")
            self.assertEqual(2, append.call_count)

    def test_final_consumer_and_policy_failures_use_transition_boundaries(self) -> None:
        owner = SimpleNamespace(statusBarMessage=Mock())
        with patch.object(
            operation_timer,
            "probe_all_enabled_routine_stocks_once",
            return_value={"logged": 0, "error": 0},
        ), patch.object(
            operation_timer,
            "auto_trade_signal_probe_only_active",
            return_value=True,
        ), patch.object(
            operation_timer,
            "consume_pending_routine_signals_dry_run",
            return_value={
                "summary": {
                    "signals_checked": 1,
                    "blocked": 0,
                    "allowed": 0,
                    "errors": 1,
                    "orders_created": 0,
                    "approval_checked": 0,
                    "approved": 0,
                }
            },
        ), patch.object(operation_timer, "observe_owner_failure_transition") as transition:
            operation_timer._auto_trade_run_signal_cycle(owner, "2026-08-16 09:00")
        consumer_calls = [
            call for call in transition.call_args_list
            if call.args[1] == "routine_signal_consumer_result"
        ]
        self.assertEqual(1, len(consumer_calls))
        self.assertTrue(consumer_calls[0].kwargs["active"])

        cycle_owner = SimpleNamespace(
            _last_time_policy_minute_key="",
            startup_recovery_session_ready=Mock(return_value=True),
            recalculate_all_status_by_operation_policy=Mock(
                return_value={"changed": 0, "failed": 1}
            ),
            rebind_startup_recovery_after_trusted_runtime_update=Mock(),
            statusBarMessage=Mock(),
        )
        with patch.object(
            operation_timer,
            "auto_trade_continue_pending_close_liquidations",
            return_value={"processed": 0, "blocked": 0},
        ), patch.object(
            operation_timer,
            "auto_trade_continue_pending_manual_ats_liquidations",
            return_value={"processed": 0, "failed": 0},
        ), patch.object(
            operation_timer,
            "refresh_operation_candles",
            None,
        ), patch.object(
            operation_timer,
            "_CANDLE_REFRESH_IMPORT_ERROR",
            None,
        ), patch.object(
            operation_timer,
            "_auto_trade_run_signal_cycle",
            return_value={},
        ), patch.object(operation_timer, "observe_owner_failure_transition") as transition:
            operation_timer.auto_trade_run_operation_cycle(cycle_owner)
        policy_calls = [
            call for call in transition.call_args_list
            if call.args[1] == "operation_policy_recalculation"
        ]
        self.assertEqual(1, len(policy_calls))
        self.assertTrue(policy_calls[0].kwargs["active"])

    def test_candle_request_and_deferred_callback_failures_are_observed(self) -> None:
        owner = SimpleNamespace(
            _last_time_policy_minute_key="",
            startup_recovery_session_ready=Mock(return_value=True),
            recalculate_all_status_by_operation_policy=Mock(
                return_value={"changed": 0, "failed": 0}
            ),
            rebind_startup_recovery_after_trusted_runtime_update=Mock(),
            statusBarMessage=Mock(),
            complete_deferred_operation_cycle=Mock(),
        )
        with patch.object(
            operation_timer,
            "auto_trade_continue_pending_close_liquidations",
            return_value={"processed": 0, "blocked": 0},
        ), patch.object(
            operation_timer,
            "auto_trade_continue_pending_manual_ats_liquidations",
            return_value={"processed": 0, "failed": 0},
        ), patch.object(
            operation_timer,
            "_auto_trade_run_signal_cycle",
            return_value={},
        ), patch.object(
            operation_timer,
            "refresh_operation_candles",
            side_effect=RuntimeError("refresh failed"),
        ), patch.object(operation_timer, "observe_production_exception") as observer:
            result = operation_timer.auto_trade_run_operation_cycle(owner)
        self.assertTrue(result["processed"])
        self.assertEqual("CANDLE_REFRESH_FAILED", result["candle_refresh_result"]["reason_code"])
        observer.assert_called_once()

        owner._last_time_policy_minute_key = ""
        callback_box = {}

        def defer_refresh(_window, _minute_key, *, on_complete):
            callback_box["callback"] = on_complete
            return {"accepted": True, "completed": False}

        owner.complete_deferred_operation_cycle.side_effect = RuntimeError("notify failed")
        with patch.object(
            operation_timer,
            "auto_trade_continue_pending_close_liquidations",
            return_value={"processed": 0, "blocked": 0},
        ), patch.object(
            operation_timer,
            "auto_trade_continue_pending_manual_ats_liquidations",
            return_value={"processed": 0, "failed": 0},
        ), patch.object(
            operation_timer,
            "_auto_trade_run_signal_cycle",
            return_value={},
        ), patch.object(
            operation_timer,
            "refresh_operation_candles",
            side_effect=defer_refresh,
        ), patch.object(operation_timer, "observe_production_exception") as observer:
            pending = operation_timer.auto_trade_run_operation_cycle(owner)
            callback_box["callback"](
                {"accepted": True, "completed": True, "failed": 0}
            )
        self.assertTrue(pending["signal_result"]["deferred_for_candle_refresh"])
        self.assertEqual("DEFERRED_OPERATION_CALLBACK_FAILED", observer.call_args.kwargs["reason_code"])

    def test_candle_final_failure_uses_transition_not_per_request_noise(self) -> None:
        owner = SimpleNamespace(
            _last_time_policy_minute_key="",
            startup_recovery_session_ready=Mock(return_value=True),
            recalculate_all_status_by_operation_policy=Mock(
                return_value={"changed": 0, "failed": 0}
            ),
            rebind_startup_recovery_after_trusted_runtime_update=Mock(),
            statusBarMessage=Mock(),
            complete_deferred_operation_cycle=Mock(),
        )
        callback_box = {}

        def defer_refresh(_window, _minute_key, *, on_complete):
            callback_box["callback"] = on_complete
            return {"accepted": True, "completed": False}

        with patch.object(
            operation_timer,
            "auto_trade_continue_pending_close_liquidations",
            return_value={"processed": 0, "blocked": 0},
        ), patch.object(
            operation_timer,
            "auto_trade_continue_pending_manual_ats_liquidations",
            return_value={"processed": 0, "failed": 0},
        ), patch.object(
            operation_timer,
            "_auto_trade_run_signal_cycle",
            return_value={},
        ), patch.object(
            operation_timer,
            "refresh_operation_candles",
            side_effect=defer_refresh,
        ), patch.object(operation_timer, "observe_owner_failure_transition") as transition:
            operation_timer.auto_trade_run_operation_cycle(owner)
            callback_box["callback"](
                {"accepted": True, "completed": True, "failed": 2}
            )
        matching = [
            call
            for call in transition.call_args_list
            if call.args[1] == "candle_refresh_result"
        ]
        self.assertEqual(1, len(matching))
        self.assertTrue(matching[0].kwargs["active"])
        self.assertEqual(2, matching[0].kwargs["details"]["failed_count"])

    def test_routine_evaluate_exception_and_malformed_result_are_observed(self) -> None:
        state = {"trade_enabled": True, "status": "RUNNING"}

        class FailingRoutine:
            ROUTINE_TYPE = "test"

            @staticmethod
            def evaluate(_context):
                raise RuntimeError("evaluate failed")

        class MalformedRoutine:
            ROUTINE_TYPE = "test"

            @staticmethod
            def evaluate(_context):
                return "bad"

        class NoneRoutine:
            ROUTINE_TYPE = "test"

            @staticmethod
            def evaluate(_context):
                return {"signal": "NONE", "reason": "no signal"}

        def read_dict(path):
            return state if Path(path).name == "state.json" else {}

        common_patches = (
            patch.object(routine_signal_probe, "_read_json_dict", side_effect=read_dict),
            patch.object(routine_signal_probe, "_load_candles_from_stock_dir", return_value=[]),
            patch.object(routine_signal_probe, "_load_instance_rules", return_value={}),
            patch.object(routine_signal_probe, "completed_timeframe_candles", return_value=[]),
            patch.object(routine_signal_probe, "read_latest_price", return_value=None),
            patch.object(routine_signal_probe, "_default_decision_trace_observer", return_value=None),
            patch.object(routine_signal_probe, "_append_log"),
        )
        with common_patches[0], common_patches[1], common_patches[2], common_patches[3], common_patches[4], common_patches[5], common_patches[6], patch.object(
            production, "append_production_event", return_value={"appended": True}
        ) as append:
            stock = Path("005930_삼성전자")
            failed = routine_signal_probe.probe_routine_for_stock(
                FailingRoutine, "테스트", stock, "2026-08-16 09:00"
            )
            malformed = routine_signal_probe.probe_routine_for_stock(
                MalformedRoutine, "테스트", stock, "2026-08-16 09:01"
            )
            before_none = append.call_count
            none_result = routine_signal_probe.probe_routine_for_stock(
                NoneRoutine, "테스트", stock, "2026-08-16 09:02"
            )
        self.assertEqual("ERROR", failed["signal"])
        self.assertEqual("ERROR", malformed["signal"])
        self.assertEqual("NONE", none_result["signal"])
        self.assertEqual(before_none, append.call_count)

    def test_routine_import_exception_is_observed_once_per_failure_transition(self) -> None:
        window = SimpleNamespace(
            current_selected_routine_dir=Mock(return_value=Path("routine-package")),
            current_selected_routine_name=Mock(return_value="테스트 루틴"),
        )
        with patch.object(
            routine_signal_probe,
            "_load_routine_module",
            side_effect=RuntimeError("import failed"),
        ), patch.object(routine_signal_probe, "_append_log"), patch.object(
            production,
            "append_production_event",
            return_value={"appended": True},
        ) as append:
            first = routine_signal_probe.probe_selected_routine_once(window)
            second = routine_signal_probe.probe_selected_routine_once(window)
        self.assertEqual(1, first["error"])
        self.assertEqual(1, second["error"])
        self.assertEqual(1, append.call_count)

    def test_kiwoom_callback_exception_is_observed_without_changing_contract(self) -> None:
        def callback(_result):
            raise RuntimeError("callback failed")

        with patch.object(kiwoom_api, "observe_production_exception") as observer:
            result = kiwoom_api.KiwoomApi._finish_callback(callback, {"ok": True})
        self.assertTrue(result["ok"])
        self.assertEqual("callback failed", result["callback_error"])
        observer.assert_called_once()

    def test_login_connection_and_account_events_use_confirmed_transitions(self) -> None:
        status_bar = SimpleNamespace(showMessage=Mock())
        fake = SimpleNamespace(
            login_status_label=SimpleNamespace(setText=Mock()),
            _apply_connected_kiwoom_login_button_state=Mock(),
            _apply_kiwoom_login_button_state=Mock(),
            refresh_kiwoom_accounts=Mock(),
            sync_account_funds_selection=Mock(),
            request_account_funds=Mock(),
            start_production_recovery=Mock(),
            start_stock_library_sync_for_current_session=Mock(),
            _account_authentication_states={},
            _account_query_states={},
            _stop_production_recovery_timers=Mock(),
            _production_recovery_identity=None,
            _production_recovery_parts={},
            _production_recovery_status_result=Mock(),
            statusBar=lambda: status_bar,
        )
        with patch.object(gui_windows, "append_production_event") as append, patch.object(
            gui_windows.production_recovery_registry, "invalidate"
        ), patch.object(gui_windows.QTimer, "singleShot") as single_shot:
            gui_windows.MainWindow.on_kiwoom_login_state_changed(fake, {"connected": True})
            gui_windows.MainWindow.on_kiwoom_login_state_changed(fake, {"connected": True})
            gui_windows.MainWindow.on_kiwoom_login_state_changed(fake, {"connected": False})
        self.assertEqual(
            ["LOGIN_SUCCEEDED", "CONNECTION_LOST"],
            [call.args[0] for call in append.call_args_list],
        )
        self.assertEqual(2, single_shot.call_count)

        class Combo:
            def currentData(self, role):
                return True if role == gui_windows.ACCOUNT_ACTIVE_ROLE else "8129123456"

            def currentIndex(self):
                return 0

        account_fake = SimpleNamespace(
            _selected_kiwoom_account_no="",
            account_combo=Combo(),
            save_current_account_memo_input=Mock(),
            load_current_account_memo_input=Mock(),
            sync_account_funds_selection=Mock(),
            refresh_account_authentication_ui=Mock(),
            refresh_account_query_status_ui=Mock(),
            request_account_funds=Mock(),
            _production_recovery_required=Mock(return_value=False),
            _stop_production_recovery_timers=Mock(),
            _production_recovery_identity=None,
            _production_recovery_parts={},
        )
        with patch.object(gui_windows, "append_production_event") as append, patch.object(
            gui_windows.production_recovery_registry, "invalidate"
        ):
            gui_windows.MainWindow.on_kiwoom_account_changed(account_fake)
        kwargs = append.call_args.kwargs
        self.assertEqual("8129****", kwargs["target_id"])
        self.assertNotIn("8129123456", str(kwargs))

    def test_chejan_final_failure_boundary_has_no_raw_payload(self) -> None:
        fake = SimpleNamespace(auto_trade_setting_window=None)
        with patch.object(
            gui_windows,
            "handle_kiwoom_raw_chejan_event",
            return_value={"recorded": False, "stage": "normalize"},
        ), patch.object(gui_windows, "observe_owner_failure_transition") as observer:
            gui_windows.MainWindow.on_kiwoom_raw_chejan_received(
                fake,
                {"fid_raw_values": {"9201": "8129123456"}},
            )
        kwargs = observer.call_args.kwargs
        self.assertTrue(kwargs["active"])
        self.assertNotIn("fid_raw_values", str(kwargs))
        self.assertNotIn("8129123456", str(kwargs))


if __name__ == "__main__":
    unittest.main()
