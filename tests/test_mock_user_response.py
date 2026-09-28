# -*- coding: utf-8 -*-

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import gui_windows
from gui_windows import MainWindow
from mock_validation_contract import MockValidationError
from mock_validation_user_response import (
    mock_action_result_message,
    mock_action_status_text,
    mock_user_message,
)


class MockUserResponseTest(unittest.TestCase):
    def test_registration_infrastructure_block_uses_non_modal_toast(self):
        window = SimpleNamespace(
            mock_validation_host=None,
            mock_validation_ui_actions=None,
        )
        target = SimpleNamespace(code="005930")
        with patch.object(gui_windows, "show_toast") as toast, patch.object(
            gui_windows.QMessageBox, "warning"
        ) as warning:
            self.assertFalse(MainWindow.begin_mock_validation(window, target))
        toast.assert_called_once_with(
            window,
            "모의검증 실행 기반을 사용할 수 없습니다.",
        )
        warning.assert_not_called()

    def test_known_operational_reasons_are_user_messages(self):
        expected = {
            "FINAL_SESSION_ENDED": "운영 가능한 거래 시간이 종료되어 운영을 시작할 수 없습니다.",
            "ALREADY_RUNNING": "이미 운영 중인 모의검증 대상입니다.",
            "MOCK_EXECUTION_NOT_ALLOWED": "이 루틴은 모의검증 실행이 비활성화되어 있습니다.",
            "MOCK_ORDERBOOK_STALE": "가상 실행에 필요한 호가 정보가 오래되어 주문을 진행할 수 없습니다.",
            "MOCK_MARKET_UNAVAILABLE": "가상 실행에 필요한 시장정보를 확인할 수 없습니다.",
            "NO_HOLDING": "보유수량이 없습니다.",
            "OUTSIDE_REGULAR_MARKET": "현재 조기마감 가능한 시간이 아닙니다.",
            "SCHEDULED_OPERATION_WINDOW_ENDED": "현재 조기마감 가능한 시간이 아닙니다.",
            "LIQUIDATION_TIME_WINDOW_ENTERED": "청산설정변경이 불가능합니다.",
            "RETURN_TO_ROUTINE_CLOSE_NOT_ALLOWED": "현재 마감방식으로 변경할 수 없습니다.",
            "MOCK_EARLY_AUTO_RETURN_BLOCKED": "현재 마감방식으로 변경할 수 없습니다.",
            "MOCK_EARLY_AUTO_RETURN_FILLED_BLOCKED": "현재 마감방식으로 변경할 수 없습니다.",
            "MOCK_INSTANCE_EARLY_CLOSE_CANCEL_BLOCKED": "현재 상태에서는 실행할 수 없습니다.",
            "MOCK_ATS_SESSION_INACTIVE": "현재 ATS 거래시간에는 청산을 진행할 수 없습니다.",
            "MOCK_INSTANCE_OPERATION_CLOSE_STATE_INVALID": "현재 운영 대상 종목이 아닙니다.",
            "MOCK_ROUTINE_INSTANCE_NOT_IN_SESSION": "현재 운영 대상 종목이 아닙니다.",
        }
        for reason, message in expected.items():
            with self.subTest(reason=reason):
                self.assertEqual(message, mock_user_message(reason))
                self.assertNotIn(reason, message)

    def test_unknown_internal_reason_uses_generic_message(self):
        message = mock_user_message("MOCK_FUTURE_INTERNAL_REASON")
        self.assertEqual("요청을 처리하지 못했습니다.", message)
        self.assertNotIn("MOCK_", message)

    def test_block_result_maps_reason_but_summary_keeps_single_surface(self):
        self.assertEqual(
            "가상 실행에 필요한 시장정보를 확인할 수 없습니다.",
            mock_action_result_message(
                {"ok": False, "status": "BLOCKED", "reason": "MOCK_MARKET_UNAVAILABLE"}
            ),
        )
        self.assertEqual(
            "",
            mock_action_result_message(
                {
                    "status": "BLOCKED",
                    "reason": "MOCK_MARKET_UNAVAILABLE",
                    "summary_toast_message": "대상종목 3  |  기운영중 1  |  운영시작 1  |  운영불가 1",
                }
            ),
        )

    def test_action_admission_block_logs_without_traceback_and_uses_non_modal_toast(self):
        status_bar = SimpleNamespace(showMessage=Mock())
        window = SimpleNamespace(statusBar=lambda: status_bar)
        error = MockValidationError("MOCK_ORDERBOOK_STALE")
        with patch.object(gui_windows, "show_toast") as toast, patch.object(
            gui_windows.QMessageBox, "warning"
        ) as warning, patch.object(
            gui_windows.LOGGER, "info"
        ) as info, patch.object(gui_windows.LOGGER, "exception") as traceback_log:
            MainWindow._mock_action_result(
                window,
                "모의 운영시작",
                Mock(side_effect=error),
            )
        toast.assert_called_once_with(
            window,
            "가상 실행에 필요한 호가 정보가 오래되어 주문을 진행할 수 없습니다.",
            duration_ms=2500,
        )
        warning.assert_not_called()
        status_bar.showMessage.assert_not_called()
        info.assert_called_once_with(
            "Mock action blocked: %s reason=%s",
            "모의 운영시작",
            error,
        )
        traceback_log.assert_not_called()
        self.assertEqual("MOCK_ORDERBOOK_STALE", str(error))

    def test_unexpected_action_exception_keeps_traceback_logging(self):
        status_bar = SimpleNamespace(showMessage=Mock())
        window = SimpleNamespace(statusBar=lambda: status_bar)
        error = RuntimeError("unexpected")
        with patch.object(gui_windows, "show_toast") as toast, patch.object(
            gui_windows.LOGGER, "info"
        ) as info, patch.object(gui_windows.LOGGER, "exception") as traceback_log:
            MainWindow._mock_action_result(
                window,
                "모의 운영시작",
                Mock(side_effect=error),
            )
        toast.assert_called_once_with(
            window,
            "요청을 처리하지 못했습니다.",
            duration_ms=2500,
        )
        info.assert_not_called()
        traceback_log.assert_called_once_with("Mock action failed: %s", "모의 운영시작")
        status_bar.showMessage.assert_not_called()

    def test_internal_action_status_is_not_shown_in_status_bar(self):
        self.assertEqual("처리 불가", mock_action_status_text({"status": "BLOCKED"}))
        self.assertEqual("오류", mock_action_status_text({"status": "ERROR"}))
        self.assertEqual("완료", mock_action_status_text({"status": "COMPLETED"}))

    def test_block_result_uses_toast_and_preserves_result_object(self):
        status_bar = SimpleNamespace(showMessage=Mock())
        window = SimpleNamespace(statusBar=lambda: status_bar)
        result = {
            "ok": False,
            "status": "BLOCKED",
            "reason": "MOCK_POSITION_REMAINS",
        }
        operation = Mock(return_value=result)
        with patch.object(gui_windows, "show_toast") as toast, patch.object(
            gui_windows.QMessageBox, "warning"
        ) as warning:
            MainWindow._mock_action_result(window, "모의 등록해제", operation)
        operation.assert_called_once_with()
        self.assertEqual("MOCK_POSITION_REMAINS", result["reason"])
        toast.assert_called_once_with(
            window,
            "보유 수량이 있어 등록을 해제할 수 없습니다.",
            duration_ms=2500,
        )
        warning.assert_not_called()
        status_bar.showMessage.assert_called_once_with(
            "모의 등록해제: 처리 불가",
            5000,
        )

    def test_multi_selection_summary_emits_one_toast_without_raw_reason(self):
        status_bar = SimpleNamespace(showMessage=Mock())
        window = SimpleNamespace(statusBar=lambda: status_bar)
        message = (
            "대상종목 3  |  기운영중 1  |  운영시작 1  |  운영불가 1\n"
            "시간운영 종료 1"
        )
        result = {
            "status": "BLOCKED",
            "reason": "FINAL_SESSION_ENDED",
            "summary_toast_message": message,
        }
        with patch.object(gui_windows, "show_toast") as toast, patch.object(
            gui_windows.QMessageBox, "warning"
        ) as warning:
            MainWindow._mock_action_result(window, "모의 Instance 운영시작", lambda: result)
        toast.assert_called_once_with(window, message, duration_ms=3200)
        self.assertNotIn("FINAL_SESSION_ENDED", toast.call_args.args[1])
        warning.assert_not_called()

    def test_mock_settings_write_failure_uses_adapter_without_modal(self):
        error = MockValidationError("MOCK_INSTANCE_SETTINGS_REVISION_CONFLICT")
        actions = SimpleNamespace(
            set_instance_effective_settings=Mock(side_effect=error)
        )
        window = SimpleNamespace(
            _mock_routine_instance_edit_target=Mock(
                return_value=("MS-1", "005930", "A", {}, {}, {})
            ),
            mock_validation_ui_actions=actions,
            _reload_main_routine_table_preserving_view=Mock(),
        )
        with patch.object(gui_windows, "show_toast") as toast, patch.object(
            gui_windows.QMessageBox, "warning"
        ) as warning, patch.object(gui_windows.LOGGER, "exception") as log:
            self.assertFalse(
                MainWindow._write_mock_routine_instance_settings(
                    window,
                    3,
                    operation_mode="CONTINUOUS",
                )
            )
        actions.set_instance_effective_settings.assert_called_once_with(
            "005930",
            "A",
            operation_mode="CONTINUOUS",
        )
        toast.assert_called_once_with(
            window,
            "모의검증 상태가 변경되었습니다. 화면을 새로 확인한 뒤 다시 시도하세요.",
            duration_ms=2500,
        )
        warning.assert_not_called()
        log.assert_called_once()
        self.assertEqual("MOCK_INSTANCE_SETTINGS_REVISION_CONFLICT", str(error))


if __name__ == "__main__":
    unittest.main()
