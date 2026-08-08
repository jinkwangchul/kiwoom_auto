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
import gui_auto_trade_setting_window as setting_window_module
import gui_main_emergency_ops as emergency_module


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
        class Host:
            def statusBarMessage(self, *_args):
                pass

            def refresh_all(self):
                pass

        stock_dir = Path(self.temp.name) / "005930_삼성전자"
        stock_dir.mkdir(parents=True)
        (stock_dir / "config.json").write_text(
            '{"operation_excluded": false}\n', encoding="utf-8"
        )
        target = (stock_dir, "005930", "삼성전자")
        with (
            patch.object(setting_window_module, "append_stock_log"),
            patch.object(setting_window_module, "append_changelog"),
            patch.object(setting_window_module, "show_toast"),
            patch.object(setting_window_module, "append_production_event") as append,
        ):
            self.assertTrue(
                setting_window_module.AutoTradeSettingWindow.set_stock_operation_exclusion(
                    Host(), target, True
                )
            )
            self.assertTrue(
                setting_window_module.AutoTradeSettingWindow.set_stock_operation_exclusion(
                    Host(), target, True
                )
            )
        append.assert_called_once()
        self.assertEqual("OPERATION_EXCLUDED", append.call_args.args[0])

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
