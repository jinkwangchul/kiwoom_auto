# -*- coding: utf-8 -*-

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import gui_auto_trade_close as close
import gui_auto_trade_status_ops as status_ops


class _ProceedMessageBox:
    Warning = 1
    Information = 2
    Question = 3
    AcceptRole = 4
    RejectRole = 5

    def __init__(self, *_args):
        self._proceed = None

    def setIcon(self, _value):
        return None

    def setWindowTitle(self, _value):
        return None

    def setText(self, _value):
        return None

    def addButton(self, _text, role):
        button = object()
        if role == self.AcceptRole:
            self._proceed = button
        return button

    def setDefaultButton(self, _value):
        return None

    def exec_(self):
        return 0

    def clickedButton(self):
        return self._proceed

    @staticmethod
    def critical(*_args):
        return None

    @staticmethod
    def warning(*_args):
        return None


class TransitionProductionCallerTest(unittest.TestCase):
    @staticmethod
    def _write_stock(root: Path) -> tuple[Path, str, str]:
        stock_dir = root / "005930_Samsung"
        stock_dir.mkdir()
        (stock_dir / "state.json").write_text(
            json.dumps(
                {
                    "status": "RUNNING",
                    "holding_qty": 1,
                    "trade_enabled": True,
                    "trade_started_at": "2026-07-27 09:00:00",
                }
            ),
            encoding="utf-8",
        )
        (stock_dir / "config.json").write_text(
            json.dumps(
                {
                    "operation_mode": "SCHEDULED",
                    "assigned_routine_instance_id": "routine-instance-1",
                }
            ),
            encoding="utf-8",
        )
        return stock_dir, "005930", "Samsung"

    @staticmethod
    def _window(selected):
        window = Mock()
        window.selected_stock_infos.return_value = selected
        window.current_selected_routine_name.return_value = "routine"
        window.capture_stock_table_view_state.return_value = ([], 0)
        return window

    def test_early_close_rejection_does_not_create_command_or_mutate_state(self):
        with tempfile.TemporaryDirectory() as temp:
            selected = [self._write_stock(Path(temp))]
            state_path = selected[0][0] / "state.json"
            before = state_path.read_bytes()
            window = self._window(selected)
            service = Mock()
            rejected = SimpleNamespace(
                allowed=False,
                reason_code="MARKET_DOWNGRADE_NOT_ALLOWED",
                evidence_status="COMPLETE",
            )
            with (
                patch.object(close, "QMessageBox", _ProceedMessageBox),
                patch.object(close, "OperationCommandService", return_value=service),
                patch.object(
                    close,
                    "pending_order_side_quantities",
                    return_value=(0, 0),
                ),
                patch.object(
                    close,
                    "auto_trade_setting_liquidation_phase_active",
                    return_value=False,
                ),
                patch.object(
                    close,
                    "evaluate_production_transition",
                    return_value=rejected,
                ),
                patch.object(close, "append_changelog"),
            ):
                close.auto_trade_apply_selected_early_close(
                    window,
                    "\ud604\uc7ac\uac00",
                )

            self.assertEqual(state_path.read_bytes(), before)
            service.apply_early_close.assert_not_called()
            window.statusBarMessage.assert_called_once_with(
                "\uc870\uae30\ub9c8\uac10 \uc801\uc6a9: 0\uac1c / \uc81c\uc678 1\uac1c"
            )

    def test_early_close_recovery_block_keeps_runtime_unchanged(self):
        class Parent:
            def production_recovery_gate_for_stock(
                self,
                _code,
                *,
                caller_name,
            ):
                self.caller_name = caller_name
                return SimpleNamespace(
                    allowed=False,
                    reason_code="RECOVERY_ACCOUNT_REVIEW_REQUIRED",
                )

        with tempfile.TemporaryDirectory() as temp:
            selected = [self._write_stock(Path(temp))]
            window = self._window(selected)
            parent = Parent()
            window.parent.return_value = parent
            state_path = selected[0][0] / "state.json"
            before = state_path.read_bytes()
            service = Mock()
            with (
                patch.object(close, "QMessageBox", _ProceedMessageBox),
                patch.object(close, "OperationCommandService", return_value=service),
                patch.object(
                    close,
                    "pending_order_side_quantities",
                    return_value=(0, 0),
                ),
                patch.object(
                    close,
                    "auto_trade_setting_liquidation_phase_active",
                    return_value=False,
                ),
                patch.object(close, "evaluate_production_transition") as transition,
                patch.object(close, "append_changelog"),
            ):
                close.auto_trade_apply_selected_early_close(window, "현재가")

            self.assertEqual("EARLY_CLOSE_REQUEST", parent.caller_name)
            transition.assert_not_called()
            service.apply_early_close.assert_not_called()
            window.update_stock_status.assert_not_called()
            self.assertEqual(before, state_path.read_bytes())

    def test_individual_liquidation_recovery_block_keeps_runtime_unchanged(self):
        class Parent:
            def production_recovery_gate_for_stock(
                self,
                _code,
                *,
                caller_name,
            ):
                self.caller_name = caller_name
                return SimpleNamespace(
                    allowed=False,
                    reason_code="RECOVERY_CONTEXT_MISSING",
                    evidence=("caller=INDIVIDUAL_LIQUIDATION_REQUEST",),
                )

        with tempfile.TemporaryDirectory() as temp:
            selected = [self._write_stock(Path(temp))]
            state_path = selected[0][0] / "state.json"
            before = state_path.read_bytes()
            window = self._window(selected)
            parent = Parent()
            window.parent.return_value = parent
            service = Mock()
            with (
                patch.object(close, "OperationCommandService", return_value=service),
                patch.object(close.QMessageBox, "critical"),
            ):
                close.auto_trade_apply_selected_individual_liquidation_method(
                    window,
                    "시장가",
                    "10",
                )

            self.assertEqual(
                "INDIVIDUAL_LIQUIDATION_REQUEST",
                parent.caller_name,
            )
            service.apply_individual_liquidation.assert_not_called()
            window.update_stock_status.assert_not_called()
            self.assertEqual(before, state_path.read_bytes())

    def test_individual_unknown_does_not_create_command_or_mutate_state(self):
        with tempfile.TemporaryDirectory() as temp:
            selected = [self._write_stock(Path(temp))]
            state_path = selected[0][0] / "state.json"
            before = state_path.read_bytes()
            window = self._window(selected)
            service = Mock()
            rejected = SimpleNamespace(
                allowed=False,
                reason_code="INSUFFICIENT_EVIDENCE",
                evidence_status="UNKNOWN",
            )
            with (
                patch.object(close, "OperationCommandService", return_value=service),
                patch.object(
                    close,
                    "evaluate_production_transition",
                    return_value=rejected,
                ),
                patch.object(close.QMessageBox, "critical") as critical,
            ):
                close.auto_trade_apply_selected_individual_liquidation_method(
                    window,
                    "\uc2dc\uc7a5\uac00",
                    "10",
                )

            self.assertEqual(state_path.read_bytes(), before)
            service.apply_individual_liquidation.assert_not_called()
            critical.assert_called_once()

    def test_auto_close_unknown_keeps_runtime_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            stock_dir, code, name = self._write_stock(Path(temp))
            state_path = stock_dir / "state.json"
            before = state_path.read_bytes()
            window = Mock()
            rejected = SimpleNamespace(
                allowed=False,
                reason_code="INSUFFICIENT_EVIDENCE",
                evidence_status="UNKNOWN",
            )
            with (
                patch.object(
                    status_ops,
                    "status_after_operation_mode_change",
                    return_value="AUTO_CLOSE",
                ),
                patch.object(
                    status_ops,
                    "read_operation_policy",
                    return_value={"auto_close": {"method": "\ud604\uc7ac\uac00"}},
                ),
                patch.object(
                    status_ops,
                    "now_text",
                    return_value="2026-07-27 13:30:00",
                ),
                patch.object(
                    status_ops,
                    "evaluate_production_transition",
                    return_value=rejected,
                ),
                patch.object(status_ops, "append_stock_log"),
            ):
                result = status_ops.auto_trade_recalculate_stock_status_by_operation_policy(
                    window,
                    stock_dir,
                    code,
                    name,
                    "timer",
                    silent_unchanged=True,
                )

            self.assertEqual(result, ("protected", "RUNNING", "RUNNING"))
            self.assertEqual(state_path.read_bytes(), before)
            window.update_stock_status.assert_not_called()

    def test_auto_close_recovery_block_keeps_runtime_unchanged(self):
        class Parent:
            def production_recovery_gate_for_stock(
                self,
                _code,
                *,
                caller_name,
            ):
                self.caller_name = caller_name
                return SimpleNamespace(
                    allowed=False,
                    reason_code="RECOVERY_IN_PROGRESS",
                )

        with tempfile.TemporaryDirectory() as temp:
            stock_dir, code, name = self._write_stock(Path(temp))
            state_path = stock_dir / "state.json"
            before = state_path.read_bytes()
            window = Mock()
            parent = Parent()
            window.parent.return_value = parent
            window.update_stock_status.return_value = True
            with (
                patch.object(
                    status_ops,
                    "status_after_operation_mode_change",
                    return_value="AUTO_CLOSE",
                ),
                patch.object(
                    status_ops,
                    "read_operation_policy",
                    return_value={"auto_close": {"method": "현재가"}},
                ),
                patch.object(
                    status_ops,
                    "now_text",
                    return_value="2026-07-27 13:30:00",
                ),
                patch.object(status_ops, "evaluate_production_transition") as transition,
                patch.object(status_ops.LOGGER, "warning") as warning,
            ):
                result = status_ops.auto_trade_recalculate_stock_status_by_operation_policy(
                    window,
                    stock_dir,
                    code,
                    name,
                    "timer",
                    silent_unchanged=True,
                )

            self.assertEqual(("protected", "RUNNING", "RUNNING"), result)
            self.assertEqual("AUTO_CLOSE_TIME_POLICY", parent.caller_name)
            transition.assert_not_called()
            window.update_stock_status.assert_not_called()
            self.assertEqual(before, state_path.read_bytes())
            warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
