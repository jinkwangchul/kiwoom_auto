# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from event_journal_reader import EventJournalReader
from event_journal_writer import EventJournalWriter
import event_journal_production as production
import gui_auto_trade_operation_host as operation_host_module
from gui_auto_trade_integrity import inspect_stock_review_state
import gui_auto_trade_setting_window as setting_window_module
import gui_auto_trade_status_ops as status_ops_module
import gui_main_emergency_ops as emergency_module
from runtime_io import read_json_dict
from tests.filesystem_test_support import TemporaryProjectRoot, create_stock_fixture


class _OperationExclusionOwner(operation_host_module.QObject):
    def __init__(self) -> None:
        super().__init__()
        self._main_monitoring_auto_trade_operation_host = (
            operation_host_module.AutoTradeOperationHost(self)
        )
        self.status_messages: list[str] = []
        self.refresh_count = 0

    def main_monitoring_auto_trade_operation_host(
        self,
    ) -> operation_host_module.AutoTradeOperationHost:
        return self._main_monitoring_auto_trade_operation_host

    def statusBarMessage(self, message: str) -> None:
        self.status_messages.append(str(message))

    def refresh_all(self) -> None:
        self.refresh_count += 1


class EventJournalProductionConnectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.journal_dir = Path(self.temp.name) / "event_journal"
        self.writer = EventJournalWriter(self.journal_dir)
        self.writer_patch = patch.object(production, "_WRITER", self.writer)
        self.writer_patch.start()

    def tearDown(self) -> None:
        self.writer_patch.stop()
        self.temp.cleanup()

    def test_app_session_id_is_process_stable_and_present_on_every_event(self) -> None:
        first = production.append_production_event(
            "APP_STARTED", result="SUCCESS", source="test",
            occurred_at="2026-08-08T09:00:00+09:00",
        )
        second = production.append_production_event(
            "APP_STOPPED", result="COMPLETED", source="test",
            occurred_at="2026-08-08T16:00:00+09:00",
        )
        self.assertEqual(production.app_session_id(), first["event"]["app_session_id"])
        self.assertEqual(first["event"]["app_session_id"], second["event"]["app_session_id"])

    def test_owner_once_suppresses_duplicate_lifecycle_without_persistence(self) -> None:
        owner = type("Owner", (), {})()
        first = production.append_owner_event_once(
            owner, "host-start", "OPERATION_HOST_STARTED",
            result="SUCCESS", source="test",
            occurred_at="2026-08-08T09:00:00+09:00",
        )
        second = production.append_owner_event_once(
            owner, "host-start", "OPERATION_HOST_STARTED",
            result="SUCCESS", source="test",
            occurred_at="2026-08-08T09:00:01+09:00",
        )
        self.assertTrue(first["appended"])
        self.assertTrue(second["duplicate"])
        events = EventJournalReader(self.journal_dir).read_events()["events"]
        self.assertEqual(1, len(events))

    def test_recovery_events_use_system_category_and_severity_for_importance(self) -> None:
        contracts = (
            ("RECOVERY_COMPLETED", "INFO"),
            ("RECOVERY_WARNING", "WARNING"),
            ("RECOVERY_FAILED", "ERROR"),
        )
        for index, (event_type, severity) in enumerate(contracts):
            result = production.append_production_event(
                event_type,
                severity=severity,
                source="test",
                occurred_at=f"2026-08-08T09:00:0{index}+09:00",
            )
            self.assertTrue(result["appended"], result)
            self.assertEqual("SYSTEM", result["event"]["category"])
            self.assertEqual(severity, result["event"]["severity"])

    def test_account_query_events_use_existing_writer_and_masked_identity(self) -> None:
        event_types = (
            "ACCOUNT_QUERY_REQUESTED",
            "ACCOUNT_QUERY_SUCCEEDED",
            "ACCOUNT_QUERY_FAILED",
            "ACCOUNT_AUTH_REQUIRED",
            "ACCOUNT_REQUERY_REQUESTED",
            "ACCOUNT_REQUERY_SUCCEEDED",
            "ACCOUNT_REQUERY_FAILED",
        )
        for index, event_type in enumerate(event_types):
            failed = event_type.endswith("FAILED") or event_type == "ACCOUNT_AUTH_REQUIRED"
            result = production.append_production_event(
                event_type,
                severity="WARNING" if failed else "INFO",
                result="FAILED" if failed else "SUCCESS",
                source="MainWindow.request_account_funds",
                template_args={"account_display": "8129****"},
                occurred_at=f"2026-08-13T09:00:0{index}+09:00",
                target_type="ACCOUNT",
                target_id="8129****",
                target_name="8129****",
                details={"query_scope": "ACCOUNT_FUNDS"},
            )
            self.assertTrue(result["appended"], result)
            self.assertEqual("SYSTEM", result["event"]["category"])
            self.assertNotIn("8129123456", str(result["event"]))

    def test_writer_failure_is_fail_open(self) -> None:
        class BrokenWriter:
            def append_event(self, **_kwargs):
                raise OSError("disk unavailable")

        with patch.object(production, "_WRITER", BrokenWriter()):
            result = production.append_production_event(
                "APP_STARTED", result="SUCCESS", source="test",
            )
        self.assertTrue(result["write_failed"])
        self.assertIn("disk unavailable", result["error"])

    def test_only_requested_first_stage_event_types_are_writable_here(self) -> None:
        requested = {
            "APP_STARTED", "APP_STOPPED", "OPERATION_HOST_STARTED", "OPERATION_HOST_STOPPED",
            "RECOVERY_COMPLETED", "RECOVERY_WARNING", "RECOVERY_FAILED",
            "OPERATION_STARTED", "OPERATION_STOPPED", "OPERATION_EXCLUDED",
            "OPERATION_EXCLUSION_RELEASED", "EMERGENCY_STOPPED", "EMERGENCY_RELEASED",
        }
        source = Path(production.__file__).read_text(encoding="utf-8")
        self.assertNotIn("ORDER_QUEUED", source)
        self.assertNotIn("SEND_ORDER", source)
        self.assertTrue(requested)

    def test_operation_host_records_only_actual_timer_transitions(self) -> None:
        host = operation_host_module.AutoTradeOperationHost(None)
        with (
            patch(
                "production_recovery_timer_lifecycle.start_recovery_bound_timers",
                return_value={"started": True, "started_count": 1, "reason_code": "RECOVERY_TIMER_STARTED"},
            ),
            patch.object(operation_host_module, "append_production_event") as append,
        ):
            result = host.start_after_recovery(object())
        self.assertTrue(result["started"])
        append.assert_called_once_with(
            "OPERATION_HOST_STARTED",
            result="SUCCESS",
            source="AutoTradeOperationHost.start_after_recovery",
            target_type="OPERATION_HOST",
            target_id="main_operation_host",
            reason_code="RECOVERY_TIMER_STARTED",
        )

        with (
            patch(
                "production_recovery_timer_lifecycle.start_recovery_bound_timers",
                return_value={"started": True, "started_count": 0, "reason_code": "RECOVERY_TIMER_ALREADY_ACTIVE"},
            ),
            patch.object(operation_host_module, "append_production_event") as append,
        ):
            host.start_after_recovery(object())
        append.assert_not_called()

    def test_operation_exclusion_records_one_event_only_after_state_change(self) -> None:
        layout = TemporaryProjectRoot(prefix="event_journal_exclusion_")
        self.addCleanup(layout.cleanup)
        stock_dir = create_stock_fixture(
            layout,
            code="005930",
            name="Samsung",
            config={"operation_mode": "SCHEDULED"},
            state={},
            orders=[],
        )
        config_path = stock_dir / "config.json"
        inspection = inspect_stock_review_state(stock_dir)
        self.assertFalse(inspection.review_required)
        self.assertTrue(inspection.state_valid)
        self.assertEqual("CLEAR", inspection.reason_code)
        self.assertFalse(read_json_dict(config_path)["operation_excluded"])

        owner = _OperationExclusionOwner()
        operation_host = owner.main_monitoring_auto_trade_operation_host()
        self.addCleanup(operation_host.shutdown)
        self.assertEqual(
            (),
            operation_host.current_session_operation_participant_stock_codes(),
        )

        target = (stock_dir, "005930", "Samsung")
        write_results = []
        event_config_snapshots: list[dict[str, object]] = []
        canonical_patch = status_ops_module._patch_auto_trade_stock_operation_excluded

        def capture_write(*args, **kwargs):
            result = canonical_patch(*args, **kwargs)
            write_results.append(result)
            return result

        def capture_event(*_args, **_kwargs):
            event_config_snapshots.append(read_json_dict(config_path))
            return {"appended": True}

        with (
            patch.object(status_ops_module, "append_stock_log"),
            patch.object(status_ops_module, "append_changelog"),
            patch.object(status_ops_module, "show_toast"),
            patch.object(
                status_ops_module,
                "_patch_auto_trade_stock_operation_excluded",
                side_effect=capture_write,
            ) as patch_exclusion,
            patch.object(
                status_ops_module,
                "append_production_event",
                side_effect=capture_event,
            ) as append,
        ):
            self.assertTrue(
                setting_window_module.AutoTradeSettingWindow.set_stock_operation_exclusion(
                    owner, target, True
                )
            )
            self.assertTrue(read_json_dict(config_path)["operation_excluded"])
            self.assertTrue(
                setting_window_module.AutoTradeSettingWindow.set_stock_operation_exclusion(
                    owner, target, True
                )
            )
        patch_exclusion.assert_called_once()
        self.assertEqual(1, len(write_results))
        self.assertTrue(write_results[0].ok)
        self.assertTrue(write_results[0].changed)
        self.assertTrue(write_results[0].read_back_verified)
        append.assert_called_once()
        self.assertEqual("OPERATION_EXCLUDED", append.call_args.args[0])
        self.assertEqual("COMPLETED", append.call_args.kwargs["result"])
        self.assertEqual("005930", append.call_args.kwargs["stock_code"])
        self.assertEqual(
            "AutoTradeSettingWindow.set_stock_operation_exclusion",
            append.call_args.kwargs["source"],
        )
        self.assertEqual(1, len(event_config_snapshots))
        self.assertTrue(event_config_snapshots[0]["operation_excluded"])
        self.assertTrue(read_json_dict(config_path)["operation_excluded"])
        self.assertEqual(1, owner.refresh_count)

    def test_global_emergency_event_is_after_writer_success_and_skips_noop(self) -> None:
        class StatusBar:
            def showMessage(self, *_args):
                pass

        class Window:
            def all_runtime_stock_dirs(self):
                return []

            def statusBar(self):
                return StatusBar()

            def refresh_all(self):
                pass

        window = Window()
        common = (
            patch.object(emergency_module, "append_changelog"),
            patch.object(emergency_module, "show_toast"),
            patch.object(emergency_module, "update_emergency_button_state"),
        )
        with (
            common[0], common[1], common[2],
            patch.object(emergency_module, "read_operation_state", return_value={"emergency_stop": False}),
            patch.object(emergency_module, "write_global_emergency_stop_state", return_value={"ok": True}),
            patch.object(emergency_module, "append_production_event") as append,
        ):
            emergency_module.execute_emergency_stop(window)
        append.assert_called_once()
        self.assertEqual("EMERGENCY_STOPPED", append.call_args.args[0])

        with (
            patch.object(emergency_module, "append_changelog"),
            patch.object(emergency_module, "show_toast"),
            patch.object(emergency_module, "update_emergency_button_state"),
            patch.object(emergency_module, "read_operation_state", return_value={"emergency_stop": True}),
            patch.object(emergency_module, "write_global_emergency_stop_state", return_value={"ok": True}),
            patch.object(emergency_module, "append_production_event") as append,
        ):
            emergency_module.execute_emergency_stop(window)
        append.assert_not_called()


if __name__ == "__main__":
    unittest.main()
