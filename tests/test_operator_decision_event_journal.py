# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QDialog, QMessageBox, QWidget

import event_journal_contract as contract
from event_journal_writer import EventJournalWriter
import gui_auto_trade_ats_ops as ats_ops
import gui_auto_trade_close as close_ops
import gui_auto_trade_context_menu as context_menu
import gui_auto_trade_setting_window as setting_window
import gui_operation_environment as operation_environment
import gui_stock_register_window as stock_register
import gui_windows
from tests.test_gui_main_stock_context_menu import _FakeMenu


class _FactoryConfirmation:
    result = QDialog.Rejected
    text = ""
    CONFIRMATION_TEXT = "전체초기화"

    def __init__(self, _parent=None) -> None:
        self.confirmation_input = MagicMock()
        self.confirmation_input.text.return_value = self.text

    def exec_(self) -> int:
        return self.result


class _ClickedMessageBox:
    Warning = QMessageBox.Warning
    Question = QMessageBox.Question
    AcceptRole = QMessageBox.AcceptRole
    RejectRole = QMessageBox.RejectRole
    accepted = False

    def __init__(self, _parent=None) -> None:
        self._buttons: list[object] = []

    def setIcon(self, _value) -> None:
        pass

    def setWindowTitle(self, _value) -> None:
        pass

    def setText(self, _value) -> None:
        pass

    def addButton(self, _text, _role):
        button = object()
        self._buttons.append(button)
        return button

    def setDefaultButton(self, _button) -> None:
        pass

    def setEscapeButton(self, _button) -> None:
        pass

    def exec_(self) -> int:
        return 0

    def clickedButton(self):
        return self._buttons[0] if self.accepted else self._buttons[-1]


class OperatorDecisionEventJournalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_contract_has_only_the_four_operator_decision_types(self) -> None:
        expected = {
            "OPERATOR_SYSTEM_DECISION": ("SYSTEM", "사용자 시스템 선택"),
            "OPERATOR_OPERATION_DECISION": ("OPERATION", "사용자 운영 선택"),
            "OPERATOR_SETTING_DECISION": ("SETTING", "사용자 설정 선택"),
            "OPERATOR_ORDER_DECISION": ("ORDER", "사용자 주문 선택"),
        }
        for event_type, (category, label) in expected.items():
            self.assertEqual(category, contract.EVENT_TYPE_CATEGORIES[event_type])
            self.assertEqual(label, contract.EVENT_TYPE_LABELS[event_type])

        with tempfile.TemporaryDirectory() as temp:
            writer = EventJournalWriter(Path(temp))
            result = writer.append_event(
                event_type="OPERATOR_ORDER_DECISION",
                occurred_at="2026-08-16T12:00:00+09:00",
                category="ORDER",
                severity="INFO",
                result="ACCEPTED",
                source="focused_test",
                details={
                    "interaction_type": "CONFIRM",
                    "prompt_key": "SAFE_PROMPT",
                    "prompt_title": "확인",
                    "prompt_summary": "정규화된 선택",
                    "offered_options": ["진행", "취소"],
                    "selected_option": "진행",
                },
            )
            self.assertTrue(result["appended"])

    def test_profit_loss_decision_records_only_numeric_input_and_cancel_is_mutation_free(self) -> None:
        window = MagicMock()
        accepted_dialog = MagicMock()
        accepted_dialog.exec_.return_value = QDialog.Accepted
        accepted_dialog.values.return_value = ("1.5", "2")
        cancelled_dialog = MagicMock()
        cancelled_dialog.exec_.return_value = QDialog.Rejected
        cancelled_dialog.values.return_value = ("raw-not-recorded", "")
        with (
            patch.object(close_ops, "ProfitLossEarlyCloseDialog", side_effect=[accepted_dialog, cancelled_dialog]),
            patch.object(close_ops, "append_production_event") as journal,
        ):
            close_ops.auto_trade_apply_selected_early_close_profit_loss(window)
            close_ops.auto_trade_apply_selected_early_close_profit_loss(window)

        self.assertEqual(1, window.apply_selected_early_close.call_count)
        accepted = journal.call_args_list[0].kwargs
        cancelled = journal.call_args_list[1].kwargs
        self.assertEqual("ACCEPTED", accepted["result"])
        self.assertEqual({"profit_percent": 1.5, "loss_percent": 2.0}, accepted["details"]["input_value"])
        self.assertEqual("CANCELLED", cancelled["result"])
        self.assertNotIn("input_value", cancelled["details"])

    def test_direct_early_close_records_proceed_and_cancel_before_backend_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = Path(temp)
            (stock_dir / "state.json").write_text(
                json.dumps({"status": "RUNNING", "holding_qty": 10}), encoding="utf-8"
            )
            (stock_dir / "config.json").write_text(json.dumps({}), encoding="utf-8")
            (stock_dir / "orders.json").write_text(json.dumps({"orders": []}), encoding="utf-8")
            window = MagicMock()
            window.current_selected_routine_name.return_value = "테스트 루틴"
            window.stock_table.viewport.return_value = MagicMock()
            recovery = MagicMock(allowed=False)
            with (
                patch.object(close_ops, "QMessageBox", _ClickedMessageBox),
                patch.object(close_ops, "append_production_event") as journal,
                patch.object(close_ops, "_kiwoom_server_login_block_message", return_value=""),
                patch.object(close_ops, "pending_order_side_quantities", return_value=(0, 0)),
                patch.object(close_ops, "auto_trade_setting_liquidation_phase_active", return_value=False),
                patch.object(close_ops, "auto_trade_setting_has_close_progress_quantity", return_value=True),
                patch.object(close_ops, "_production_recovery_gate", return_value=recovery),
                patch.object(close_ops, "_log_recovery_block"),
                patch.object(close_ops, "_recovery_block_user_message", return_value="Recovery 차단"),
                patch.object(close_ops, "append_changelog"),
                patch.object(close_ops, "refresh_auto_trade_views"),
                patch.object(close_ops, "show_toast"),
            ):
                _ClickedMessageBox.accepted = False
                result = close_ops.auto_trade_apply_selected_early_close(
                    window, "시장가즉시", selected=[(stock_dir, "005930", "삼성전자")]
                )
                self.assertTrue(result["cancelled"])
                _ClickedMessageBox.accepted = True
                close_ops.auto_trade_apply_selected_early_close(
                    window, "시장가즉시", selected=[(stock_dir, "005930", "삼성전자")]
                )

        self.assertEqual(["CANCELLED", "ACCEPTED"], [call.kwargs["result"] for call in journal.call_args_list])
        self.assertEqual("시장가", journal.call_args_list[1].kwargs["details"]["method"])

    def test_ats_yes_and_no_are_separate_operator_results(self) -> None:
        selected = [(Path("C:/fixture/stock"), "005930", "삼성전자")]
        preview = {
            "ok": True,
            "stock_dir": "C:/fixture/stock",
            "code": "005930",
            "name": "삼성전자",
            "command_id": "cmd-1",
            "selected_ats_sessions": ["extra1"],
        }
        eligibility = {"eligible": True, "selected_sessions": ("extra1",), "blocked_reasons": []}
        window = MagicMock()
        window.capture_stock_table_view_state.return_value = (set(), 0)
        window.current_runtime_file_signature.return_value = (0, 0)
        with (
            patch.object(ats_ops, "_manual_ats_liquidation_target_eligibility", return_value=eligibility),
            patch.object(ats_ops, "build_manual_ats_liquidation_preview", return_value=preview),
            patch.object(ats_ops, "_start_manual_ats_liquidation_with_cancel_boundary", return_value={"ok": True, "stage": "completed_no_holding"}),
            patch.object(ats_ops.QMessageBox, "question", side_effect=[QMessageBox.No, QMessageBox.Yes]),
            patch.object(ats_ops.QMessageBox, "warning"),
            patch.object(ats_ops, "append_production_event") as journal,
        ):
            ats_ops.auto_trade_execute_selected_manual_ats_liquidation(window, "시장가", {}, selected)
            ats_ops.auto_trade_execute_selected_manual_ats_liquidation(window, "시장가", {}, selected)

        self.assertEqual(["REJECTED", "ACCEPTED"], [call.kwargs["result"] for call in journal.call_args_list])
        self.assertTrue(all(call.kwargs["details"]["method"] == "MARKET" for call in journal.call_args_list))

    def test_stock_and_factory_reset_record_cancel_and_accept_without_raw_confirmation(self) -> None:
        with patch.object(stock_register, "QMessageBox", _ClickedMessageBox), patch.object(
            stock_register, "append_production_event"
        ) as stock_journal:
            _ClickedMessageBox.accepted = False
            self.assertFalse(stock_register.confirm_stock_reset(QWidget(), "005930", "삼성전자"))
            _ClickedMessageBox.accepted = True
            self.assertTrue(stock_register.confirm_stock_reset(QWidget(), "005930", "삼성전자"))
        self.assertEqual(["CANCELLED", "ACCEPTED"], [call.kwargs["result"] for call in stock_journal.call_args_list])
        self.assertEqual("005930", stock_journal.call_args_list[1].kwargs["stock_code"])

        owner = MagicMock()
        owner._main_window_kiwoom_api.return_value = object()
        with (
            patch.object(operation_environment, "ProgramFactoryResetConfirmDialog", _FactoryConfirmation),
            patch.object(operation_environment, "append_production_event") as factory_journal,
            patch.object(operation_environment.QMessageBox, "warning"),
        ):
            _FactoryConfirmation.result = QDialog.Rejected
            _FactoryConfirmation.text = "비밀 원문"
            operation_environment.OperationEnvironmentSettingsDialog._request_program_factory_reset(owner)
            _FactoryConfirmation.result = QDialog.Accepted
            _FactoryConfirmation.text = "전체초기화"
            operation_environment.OperationEnvironmentSettingsDialog._request_program_factory_reset(owner)
        event_text = repr([call.kwargs for call in factory_journal.call_args_list])
        self.assertNotIn("비밀 원문", event_text)
        self.assertEqual(False, factory_journal.call_args_list[0].kwargs["details"]["confirmation_matched"])
        self.assertEqual(True, factory_journal.call_args_list[1].kwargs["details"]["confirmation_matched"])

    def test_manual_cancel_modify_and_execution_approvals_are_correlated(self) -> None:
        host = QWidget()
        host.execution_enable_confirmation_text = lambda *_args: "safe"
        host.real_preflight_confirmation_text = lambda *_args: "safe"
        host.execution_runtime_commit_confirmation_text = lambda *_args, **_kwargs: "safe"
        host.runtime_file_init_confirmation_text = lambda **_kwargs: "safe"
        host.manual_queue_commit_confirmation_text = lambda *_args: "safe"
        host.manual_send_order_confirmation_text = lambda *_args: "safe"
        order = {"id": "order-1", "source_signal_id": "signal-1", "code": "005930", "execution_id": "exec-1"}
        with (
            patch.object(setting_window.QDialog, "exec_", side_effect=[QDialog.Accepted, QDialog.Rejected] * 6),
            patch.object(setting_window, "append_production_event") as journal,
        ):
            methods = [
                lambda: setting_window.AutoTradeSettingWindow.confirm_execution_enable_commit(host, order, {"order_id": "order-1"}, Path("x"), {}),
                lambda: setting_window.AutoTradeSettingWindow.confirm_real_preflight_commit(host, order, {}, {"order_id": "order-1"}, Path("x"), {}),
                lambda: setting_window.AutoTradeSettingWindow.confirm_execution_runtime_commit(host, order, {"execution_id": "exec-1"}, order_executions_path=Path("secret-a"), order_locks_path=Path("secret-b"), queue_path=Path("secret-c")),
                lambda: setting_window.AutoTradeSettingWindow.confirm_execution_runtime_file_init(host, order_executions_path=Path("secret-a"), order_locks_path=Path("secret-b")),
                lambda: setting_window.AutoTradeSettingWindow.confirm_manual_queue_commit(host, {"order_queued_record_preview": order}, Path("secret-c"), {}),
                lambda: setting_window.AutoTradeSettingWindow.confirm_manual_send_order(host, order, {}, Path("secret-c"), {}),
            ]
            for method in methods:
                self.assertTrue(method())
                self.assertFalse(method())

        self.assertEqual(12, journal.call_count)
        self.assertEqual(["ACCEPTED", "CANCELLED"] * 6, [call.kwargs["result"] for call in journal.call_args_list])
        serialized = repr([call.kwargs for call in journal.call_args_list])
        self.assertNotIn("secret-a", serialized)
        self.assertNotIn("secret-b", serialized)
        self.assertNotIn("secret-c", serialized)
        self.assertNotIn("account_no", serialized)

        source_order = {"id": "source-order", "broker_order_no": "broker-1", "code": "005930", "account_no": "1234567890"}
        modify_preview = {"order_queued_record_preview": {"execution_request": {"request_preview": {"quantity": 3, "price": 71000}}}}
        with (
            patch.object(setting_window.QMessageBox, "question", side_effect=[QMessageBox.Yes, QMessageBox.No, QMessageBox.Yes, QMessageBox.No]),
            patch.object(setting_window, "append_production_event") as manual_journal,
        ):
            setting_window.AutoTradeSettingWindow.confirm_manual_cancel_pending_order(host, source_order, {})
            setting_window.AutoTradeSettingWindow.confirm_manual_cancel_pending_order(host, source_order, {})
            setting_window.AutoTradeSettingWindow.confirm_manual_modify_pending_order(host, source_order, modify_preview)
            setting_window.AutoTradeSettingWindow.confirm_manual_modify_pending_order(host, source_order, modify_preview)
        self.assertEqual(["ACCEPTED", "REJECTED", "ACCEPTED", "REJECTED"], [call.kwargs["result"] for call in manual_journal.call_args_list])
        self.assertEqual({"quantity": 3, "price": 71000}, manual_journal.call_args_list[2].kwargs["details"]["input_value"])
        self.assertNotIn("1234567890", repr([call.kwargs for call in manual_journal.call_args_list]))

    def test_context_menu_records_action_once_and_no_selection_zero(self) -> None:
        callbacks = context_menu.StockContextMenuCallbacks(
            select_all=MagicMock(),
            clear_selection=MagicMock(),
            early_close=MagicMock(),
            early_close_profit_loss=MagicMock(),
            early_close_cancel=MagicMock(),
            individual_liquidation=MagicMock(),
        )
        with patch.object(context_menu, "_new_stock_context_menu", side_effect=lambda parent: _FakeMenu(parent)), patch.object(
            context_menu, "append_production_event"
        ) as journal:
            _FakeMenu.chosen_menu_title = "조기마감"
            _FakeMenu.chosen_text = "시장가"
            context_menu.show_monitor_stock_context_menu(
                QWidget(),
                None,
                has_selection=True,
                callbacks=callbacks,
                selected_targets=[(Path("fixture"), "005930", "삼성전자")],
            )
            self.assertEqual(1, journal.call_count)
            self.assertEqual("EARLY_CLOSE_MARKET", journal.call_args.kwargs["details"]["selected_option"])
            _FakeMenu.chosen_text = None
            context_menu.show_monitor_stock_context_menu(
                QWidget(), None, has_selection=True, callbacks=callbacks
            )
            self.assertEqual(1, journal.call_count)

    def test_recovery_exit_and_market_routine_decisions_are_separate_from_backend(self) -> None:
        recovery = MagicMock()
        recovery.refresh_startup_recovery_status.return_value = {
            "status": "READY",
            "operator_approval_allowed": True,
            "snapshot_hash": "must-not-be-stored",
        }
        recovery._startup_recovery_detail_text.return_value = "runtime evidence full text"
        recovery.auto_trade_setting_window = None
        with patch.object(gui_windows.QMessageBox, "question", side_effect=[QMessageBox.No, QMessageBox.Yes]), patch.object(
            gui_windows, "append_production_event"
        ) as recovery_journal:
            gui_windows.MainWindow.review_startup_recovery(recovery)
            gui_windows.MainWindow.review_startup_recovery(recovery)
        self.assertEqual(["REJECTED", "ACCEPTED"], [call.kwargs["result"] for call in recovery_journal.call_args_list])
        self.assertNotIn("must-not-be-stored", repr([call.kwargs for call in recovery_journal.call_args_list]))
        self.assertNotIn("runtime evidence full text", repr([call.kwargs for call in recovery_journal.call_args_list]))

        with patch.object(gui_windows, "QMessageBox", _ClickedMessageBox), patch.object(
            gui_windows, "append_production_event"
        ) as exit_journal:
            owner = MagicMock()
            owner._main_exit_warning_required.return_value = True
            _ClickedMessageBox.accepted = False
            self.assertFalse(gui_windows.MainWindow._confirm_main_window_exit_if_required(owner))
            _ClickedMessageBox.accepted = True
            self.assertTrue(gui_windows.MainWindow._confirm_main_window_exit_if_required(owner))
        self.assertEqual(["CANCELLED", "ACCEPTED"], [call.kwargs["result"] for call in exit_journal.call_args_list])

        routine_owner = MagicMock()
        routine_owner._production_recovery_allows_routine_operation.return_value = False
        with patch.object(gui_windows, "_create_routine_operation_confirmation") as confirmation, patch.object(
            gui_windows, "append_production_event"
        ) as routine_journal:
            confirmation.return_value.exec_.return_value = QMessageBox.Yes
            gui_windows.MainWindow.request_routine_operation(
                routine_owner, "instance-1", "테스트 루틴", gui_windows.POLICY_MARKET, gui_windows.ROUTINE_STATUS_IMMEDIATE_LIQUIDATION
            )
        self.assertEqual("ACCEPTED", routine_journal.call_args.kwargs["result"])
        self.assertEqual("EARLY_CLOSE", routine_journal.call_args.kwargs["details"]["operation"])
        self.assertEqual("market", routine_journal.call_args.kwargs["details"]["method"])


if __name__ == "__main__":
    unittest.main()
