# -*- coding: utf-8 -*-

from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock
from unittest.mock import MagicMock, Mock

from PyQt5.QtWidgets import QMessageBox

import gui_auto_trade_ats_ops as ats_ops
import gui_windows
from gui_operation_ui_context import refresh_auto_trade_views


class D4c1InternalRefreshGapTest(TestCase):
    def _run_chejan(self, result: dict[str, object]):
        main = SimpleNamespace(auto_trade_setting_window=None)
        with (
            mock.patch.object(
                gui_windows,
                "handle_kiwoom_raw_chejan_event",
                return_value=result,
            ),
            mock.patch.object(gui_windows, "observe_owner_failure_transition"),
            mock.patch.object(
                gui_windows,
                "main_window_buffer_response_integration_ready",
                return_value=False,
            ),
            mock.patch.object(gui_windows, "refresh_auto_trade_views") as refresh,
        ):
            gui_windows.MainWindow.on_kiwoom_raw_chejan_received(
                main,
                {"gubun": "0"},
            )
        return main, refresh

    def test_chejan_final_sell_marker_refreshes_shared_views_once(self) -> None:
        result = {
            "recorded": True,
            "stage": "chejan_record",
            "close_routine_final_sell_marker": {
                "attempted": True,
                "marked": True,
                "changed": True,
                "read_back_verified": True,
            },
        }

        main, refresh = self._run_chejan(result)

        refresh.assert_called_once_with(main)

    def test_chejan_partial_duplicate_and_write_failure_do_not_refresh(self) -> None:
        cases = (
            {
                "recorded": True,
                "stage": "chejan_record",
                "close_routine_final_sell_marker": {
                    "attempted": False,
                    "marked": False,
                    "changed": False,
                },
            },
            {
                "recorded": False,
                "stage": "chejan_record",
                "duplicate_noop": True,
            },
            {
                "recorded": True,
                "stage": "chejan_record",
                "close_routine_final_sell_marker": {
                    "attempted": True,
                    "marked": False,
                    "changed": False,
                    "read_back_verified": False,
                },
            },
        )
        for result in cases:
            with self.subTest(result=result):
                _main, refresh = self._run_chejan(result)
                refresh.assert_not_called()

    def test_shared_refresh_does_not_create_closed_settings_window(self) -> None:
        main = SimpleNamespace(
            auto_trade_setting_window=None,
            refresh_all=Mock(),
        )

        gui_windows.MainWindow.refresh_auto_trade_assignment_views(main)

        main.refresh_all.assert_called_once_with()
        self.assertIsNone(main.auto_trade_setting_window)

    @staticmethod
    def _ats_window(stock_dir: Path) -> MagicMock:
        window = MagicMock()
        window.selected_stock_infos.return_value = [
            (stock_dir, "005930", "Samsung")
        ]
        return window

    def _run_selected_ats(self, result: dict[str, object]):
        stock_dir = Path("C:/temp/005930_Samsung")
        window = self._ats_window(stock_dir)
        preview = {
            "ok": True,
            "code": "005930",
            "name": "Samsung",
            "stock_dir": str(stock_dir),
            "command_id": "ats-refresh-1",
            "selected_ats_sessions": ["extra1"],
            "sell_method": "MARKET",
        }
        with (
            mock.patch.object(
                ats_ops,
                "_manual_ats_liquidation_target_eligibility",
                return_value={
                    "eligible": True,
                    "selected_sessions": ("extra1",),
                    "blocked_reasons": [],
                },
            ),
            mock.patch.object(
                ats_ops,
                "build_manual_ats_liquidation_preview",
                return_value=preview,
            ),
            mock.patch.object(
                ats_ops.QMessageBox,
                "question",
                return_value=QMessageBox.Yes,
            ),
            mock.patch.object(ats_ops.QMessageBox, "warning"),
            mock.patch.object(
                ats_ops,
                "_start_manual_ats_liquidation_with_cancel_boundary",
                return_value=result,
            ),
            mock.patch.object(ats_ops, "append_production_event"),
            mock.patch.object(ats_ops, "append_stock_log"),
            mock.patch.object(ats_ops, "refresh_auto_trade_views") as refresh,
        ):
            ats_ops.auto_trade_execute_selected_manual_ats_liquidation(
                window,
                "MARKET",
                {"extra1": True},
            )
        return window, refresh

    def test_ats_intermediate_and_failure_state_refresh_without_poll(self) -> None:
        for result in (
            {
                "ok": True,
                "projection_changed": True,
                "stage": "awaiting_cancel_confirmation",
            },
            {
                "ok": False,
                "projection_changed": True,
                "stage": "holding_reconciliation",
                "blocked_reasons": ["holding quantity conflict"],
            },
        ):
            with self.subTest(result=result):
                window, refresh = self._run_selected_ats(result)
                refresh.assert_called_once_with(window)

    def test_ats_writer_failure_does_not_project_success(self) -> None:
        window, refresh = self._run_selected_ats(
            {
                "ok": False,
                "projection_changed": False,
                "stage": "runtime_status_readback",
                "blocked_reasons": ["state write failed"],
            }
        )

        refresh.assert_not_called()

    def test_ats_resume_final_state_refreshes_once_without_poll(self) -> None:
        stock_dir = Path("C:/temp/005930_Samsung")
        request = {
            "status": "READY_TO_RESUME",
            "command_id": "ats-resume-refresh",
            "selected_ats_sessions": ["extra1"],
            "sell_method": "MARKET",
        }
        window = MagicMock()
        with (
            mock.patch(
                "gui_auto_trade_runtime.all_registered_stock_dirs",
                return_value=[stock_dir],
            ),
            mock.patch.object(
                ats_ops,
                "read_execution_queue_records",
                return_value={"ok": True, "records": ()},
            ),
            mock.patch.object(
                ats_ops,
                "read_json_dict",
                return_value={
                    ats_ops.MANUAL_ATS_LIQUIDATION_REQUEST_KEY: request,
                },
            ),
            mock.patch.object(
                ats_ops,
                "_finalize_manual_ats_liquidation_with_latest_holding",
                return_value={
                    "ok": True,
                    "projection_changed": True,
                    "stage": "completed_no_holding",
                },
            ),
            mock.patch.object(ats_ops, "refresh_auto_trade_views") as refresh,
        ):
            result = ats_ops.auto_trade_continue_pending_manual_ats_liquidations(
                window,
                limit=5,
            )

        self.assertEqual(1, result["processed"])
        self.assertTrue(result["projection_changed"])
        refresh.assert_called_once_with(window)
