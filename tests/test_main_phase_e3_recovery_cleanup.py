from __future__ import annotations

import inspect
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import assignment_authorization_service
import gui_auto_trade_setting_window
import gui_main_emergency_ops
import gui_windows


class MainPhaseE3RecoveryCleanupTest(unittest.TestCase):
    def test_duplicate_recovery_wrappers_are_removed(self) -> None:
        removed = (
            "startup_recovery_stock_state_paths",
            "production_recovery_stock_is_review_required",
            "show_routine_recovery_block_toast",
            "_production_recovery_allows_routine_operation",
        )

        for method_name in removed:
            with self.subTest(method_name=method_name):
                self.assertFalse(hasattr(gui_windows.MainWindow, method_name))

    def test_recovery_orchestration_guard_and_writer_contracts_are_preserved(self) -> None:
        contracts = (
            "_production_recovery_required",
            "_stop_production_recovery_timers",
            "_clear_completed_recovery_handoff",
            "_publish_completed_recovery_handoff",
            "_record_production_recovery_review",
            "_fail_production_recovery",
            "_finish_production_recovery",
            "_resume_limit_responses_after_recovery",
            "_request_account_funds_after_recovery",
            "_on_production_recovery_snapshot",
            "_request_production_recovery_snapshot",
            "start_production_recovery",
            "_restart_failed_production_recovery_after_account_funds_success",
            "production_recovery_gate_for_stock",
            "filter_start_targets_by_production_recovery",
            "refresh_startup_recovery_status",
            "startup_recovery_session_ready",
            "rebind_startup_recovery_after_trusted_runtime_update",
            "review_startup_recovery",
        )

        for method_name in contracts:
            with self.subTest(method_name=method_name):
                self.assertTrue(callable(getattr(gui_windows.MainWindow, method_name, None)))

    def test_recovery_projection_and_process_local_helpers_remain(self) -> None:
        retained = (
            "latest_completed_recovery_handoff",
            "_production_recovery_status_result",
            "_read_recovery_runtime_list",
            "_registered_recovery_stock_runtime",
            "routine_recovery_block_message",
            "production_recovery_block_user_message",
            "startup_recovery_block_reason",
            "_startup_recovery_detail_text",
        )

        for method_name in retained:
            with self.subTest(method_name=method_name):
                self.assertTrue(callable(getattr(gui_windows.MainWindow, method_name, None)))

    def test_assignment_fallback_reads_the_canonical_recovery_registry(self) -> None:
        with patch.object(
            assignment_authorization_service,
            "recovery_stock_is_review_required",
            return_value=True,
        ) as inspector:
            blocked, reason, evidence = assignment_authorization_service._recovery_block(
                None,
                "005930",
            )

        self.assertTrue(blocked)
        self.assertEqual("RECOVERY_BLOCKED", reason)
        self.assertEqual(("recovery_stock_review_required",), evidence)
        inspector.assert_called_once_with("005930")

    def test_settings_review_isolation_reads_the_canonical_recovery_registry(self) -> None:
        with (
            patch.object(
                gui_auto_trade_setting_window,
                "is_review_required_stock_dir",
                return_value=False,
            ),
            patch.object(
                gui_auto_trade_setting_window,
                "recovery_stock_is_review_required",
                return_value=True,
            ) as inspector,
        ):
            isolated = (
                gui_auto_trade_setting_window.AutoTradeSettingWindow.start_target_is_review_isolated(
                    SimpleNamespace(),
                    Path("005930_test"),
                    "005930",
                )
            )

        self.assertTrue(isolated)
        inspector.assert_called_once_with("005930")

    def test_production_callers_do_not_borrow_the_removed_mainwindow_inspector(self) -> None:
        assignment_source = inspect.getsource(
            assignment_authorization_service._recovery_block
        )
        settings_source = inspect.getsource(
            gui_auto_trade_setting_window.AutoTradeSettingWindow.start_target_is_review_isolated
        )
        emergency_source = inspect.getsource(
            gui_main_emergency_ops.release_emergency_stop_target
        )
        main_source = inspect.getsource(
            gui_windows.MainWindow.toggle_routine_instance_operation
        )

        for source in (
            assignment_source,
            settings_source,
            emergency_source,
            main_source,
        ):
            with self.subTest(source=source.splitlines()[0]):
                self.assertIn("recovery_stock_is_review_required", source)
                self.assertNotIn("production_recovery_stock_is_review_required", source)

    def test_startup_recovery_builds_state_paths_without_a_mainwindow_read_wrapper(self) -> None:
        source = inspect.getsource(gui_windows.MainWindow.refresh_startup_recovery_status)

        self.assertIn('stock_dir / "state.json"', source)
        self.assertNotIn("startup_recovery_stock_state_paths", source)


if __name__ == "__main__":
    unittest.main()
