# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFontMetrics, QValidator
from PyQt5.QtWidgets import QApplication, QLineEdit

import gui_windows
from gui_windows import RunningBudgetAdjustmentDialog
from gui_auto_trade_setting_window import StockPolicyOverrideDialog


class StartBudgetActiveParticipantGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _owner(*participants: str, global_running: bool = False):
        return SimpleNamespace(
            _current_session_operation_participant_stock_codes=set(participants),
            operation_status="RUNNING" if global_running else "STOPPED",
            startup_recovery_session_ready=lambda refresh=False: True,
            parent=lambda: None,
        )

    @staticmethod
    def _config_path(root: Path) -> Path:
        stock_dir = root / "005930_Test"
        stock_dir.mkdir(parents=True, exist_ok=True)
        return stock_dir / "config.json"

    @staticmethod
    def _write(path: Path, payload: dict[str, object]) -> None:
        path.write_text(json.dumps(payload), encoding="utf-8")

    @staticmethod
    def _write_state(
        config_path: Path,
        *,
        status: str = "MONITORING",
        trade_enabled: bool = True,
    ) -> None:
        (config_path.parent / "state.json").write_text(
            json.dumps(
                {
                    "status": status,
                    "trade_enabled": trade_enabled,
                    "trade_started_at": "2026-08-28 05:09:14",
                }
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _read(path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))

    def test_pre_operation_mode_and_values_remain_editable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._config_path(Path(temp_dir))
            owner = self._owner()

            self._write(path, {"trade_amount_type": "QUANTITY", "buy_qty": 7, "buy_amount": 100000})
            result = gui_windows.MainWindow._write_stock_initial_buy_config(
                owner, path, mode="AMOUNT", value=100000
            )
            self.assertTrue(result["allowed"])
            self.assertEqual("AMOUNT", self._read(path)["trade_amount_type"])

            result = gui_windows.MainWindow._write_stock_initial_buy_config(
                owner, path, mode="AMOUNT", value=200000
            )
            self.assertTrue(result["allowed"])
            self.assertEqual(200000, self._read(path)["buy_amount"])

            result = gui_windows.MainWindow._write_stock_initial_buy_config(
                owner, path, mode="QUANTITY", value=7
            )
            self.assertTrue(result["allowed"])
            result = gui_windows.MainWindow._write_stock_initial_buy_config(
                owner, path, mode="QUANTITY", value=10
            )
            self.assertTrue(result["allowed"])
            self.assertEqual(10, self._read(path)["buy_qty"])

    def test_active_participant_blocks_mode_quantity_and_amount_mutation(self) -> None:
        cases = (
            ({"trade_amount_type": "QUANTITY", "buy_qty": 7, "buy_amount": 100000}, "AMOUNT", 100000),
            ({"trade_amount_type": "AMOUNT", "buy_qty": 7, "buy_amount": 100000}, "QUANTITY", 7),
            ({"trade_amount_type": "QUANTITY", "buy_qty": 7, "buy_amount": 100000}, "QUANTITY", 10),
            ({"trade_amount_type": "AMOUNT", "buy_qty": 7, "buy_amount": 100000}, "AMOUNT", 200000),
        )
        for initial, mode, value in cases:
            with self.subTest(mode=mode, value=value), tempfile.TemporaryDirectory() as temp_dir:
                path = self._config_path(Path(temp_dir))
                self._write(path, initial)
                self._write_state(path)
                before = path.read_bytes()
                result = gui_windows.MainWindow._write_stock_initial_buy_config(
                    self._owner("005930"), path, mode=mode, value=value
                )
                self.assertFalse(result["allowed"])
                self.assertEqual("START_BUDGET_MUTATION_BLOCKED", result["reason"])
                self.assertEqual(before, path.read_bytes())

    def test_global_running_does_not_block_nonparticipant(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._config_path(Path(temp_dir))
            self._write(path, {"trade_amount_type": "QUANTITY", "buy_qty": 7})
            result = gui_windows.MainWindow._write_stock_initial_buy_config(
                self._owner(global_running=True), path, mode="QUANTITY", value=10
            )
            self.assertTrue(result["allowed"])
            self.assertEqual(10, self._read(path)["buy_qty"])

    def test_pre_session_participant_is_locked_without_time_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._config_path(Path(temp_dir))
            self._write(path, {"trade_amount_type": "QUANTITY", "buy_qty": 7})
            self._write_state(path)
            owner = self._owner("005930")
            owner.order_time_allowed = False
            result = gui_windows.MainWindow._write_stock_initial_buy_config(
                owner, path, mode="QUANTITY", value=10
            )
            self.assertFalse(result["allowed"])
            self.assertEqual(7, self._read(path)["buy_qty"])

    def test_current_running_statuses_remain_locked(self) -> None:
        for status in ("MONITORING", "RUNNING", "CLOSING"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temp_dir:
                path = self._config_path(Path(temp_dir))
                self._write(path, {"trade_amount_type": "QUANTITY", "buy_qty": 7})
                self._write_state(path, status=status, trade_enabled=True)

                result = gui_windows.MainWindow._write_stock_initial_buy_config(
                    self._owner("005930"), path, mode="QUANTITY", value=10
                )

                self.assertFalse(result["allowed"])
                self.assertTrue(result["current_running"])
                self.assertEqual(7, self._read(path)["buy_qty"])

    def test_active_ui_blocks_mode_toggle_and_value_editor_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._config_path(Path(temp_dir))
            self._write(path, {"trade_amount_type": "QUANTITY", "buy_qty": 7})
            self._write_state(path)
            hosts = []
            for action in (
                gui_windows.MainWindow.toggle_routine_stock_initial_buy_mode,
                gui_windows.MainWindow.start_routine_stock_initial_buy_edit,
            ):
                host = self._owner("005930")
                host._main_routine_display_level = "stock"
                host._main_routine_initial_buy_badge_enabled = lambda: True
                host._stock_config_path_for_routine_row = lambda _row: path
                host._stock_start_budget_locked = MethodType(
                    gui_windows.MainWindow._stock_start_budget_locked, host
                )
                host._open_running_budget_adjustment_dialog = MagicMock()
                host.finish_routine_stock_initial_buy_edit = MagicMock()
                host._write_stock_initial_buy_config = MagicMock()
                host.load_routine_table = MagicMock()
                host.routine_table = SimpleNamespace(item=lambda _row, _column: None)
                action(host, 0)
                hosts.append(host)

            for host in hosts:
                host._write_stock_initial_buy_config.assert_not_called()
                host.finish_routine_stock_initial_buy_edit.assert_not_called()
                host._open_running_budget_adjustment_dialog.assert_called_once()
            self.assertEqual(7, self._read(path)["buy_qty"])

    def test_editor_open_before_start_is_blocked_at_enter_or_focusout_commit(self) -> None:
        for commit_source in ("ENTER", "FOCUS_OUT"):
            with self.subTest(commit_source=commit_source), tempfile.TemporaryDirectory() as temp_dir:
                path = self._config_path(Path(temp_dir))
                self._write(path, {"trade_amount_type": "QUANTITY", "buy_qty": 7})
                self._write_state(path)
                editor = QLineEdit("10")
                host = self._owner("005930")
                host._routine_stock_initial_buy_editor = editor
                host._routine_stock_initial_buy_edit_finishing = False
                host._routine_stock_initial_buy_editor_config_path = str(path)
                host._routine_stock_initial_buy_editor_mode = "QUANTITY"
                host.routine_table = SimpleNamespace(
                    _editing_stock_initial_buy_path="stock",
                    viewport=lambda: SimpleNamespace(update=MagicMock()),
                )
                host._write_stock_initial_buy_config = MethodType(
                    gui_windows.MainWindow._write_stock_initial_buy_config, host
                )
                host._show_start_budget_mutation_blocked = MagicMock()
                host.load_routine_table = MagicMock()

                gui_windows.MainWindow.finish_routine_stock_initial_buy_edit(host, save=True)

                self.assertEqual(7, self._read(path)["buy_qty"])
                host._show_start_budget_mutation_blocked.assert_called_once_with(host)
                host.load_routine_table.assert_called_once_with()
                editor.deleteLater()

    def test_active_noop_does_not_write_or_report_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._config_path(Path(temp_dir))
            self._write(path, {"trade_amount_type": "QUANTITY", "buy_qty": 7, "updated_at": "fixed"})
            self._write_state(path)
            before = path.read_bytes()
            result = gui_windows.MainWindow._write_stock_initial_buy_config(
                self._owner("005930"), path, mode="QUANTITY", value=7
            )
            self.assertTrue(result["allowed"])
            self.assertFalse(result["changed"])
            self.assertEqual(before, path.read_bytes())

    def test_stale_full_config_preserves_all_protected_fields(self) -> None:
        cases = (
            (
                {"trade_amount_type": "AMOUNT", "buy_qty": 7, "buy_amount": 100000},
                {"trade_amount_type": "QUANTITY", "buy_qty": 7, "buy_amount": 100000},
            ),
            (
                {"trade_amount_type": "QUANTITY", "buy_qty": 10, "buy_amount": 100000},
                {"trade_amount_type": "QUANTITY", "buy_qty": 7, "buy_amount": 100000},
            ),
            (
                {"trade_amount_type": "AMOUNT", "buy_qty": 7, "buy_amount": 200000},
                {"trade_amount_type": "AMOUNT", "buy_qty": 7, "buy_amount": 100000},
            ),
        )
        for current, stale in cases:
            with self.subTest(current=current, stale=stale), tempfile.TemporaryDirectory() as temp_dir:
                path = self._config_path(Path(temp_dir))
                self._write(path, {**current, "policy_override_memo": "old"})
                self._write_state(path)
                dialog = self._owner("005930")
                dialog.stock_dir = path.parent
                dialog.config_path = path
                dialog.code = "005930"
                dialog.config = {**stale, "policy_override_memo": "new"}

                StockPolicyOverrideDialog.write_config(dialog)

                saved = self._read(path)
                for field in ("trade_amount_type", "buy_qty", "buy_amount"):
                    self.assertEqual(current[field], saved[field])
                self.assertEqual("new", saved["policy_override_memo"])
                self.assertEqual(
                    "START_BUDGET_MUTATION_BLOCKED",
                    dialog._start_budget_preservation_result["reason"],
                )

    def test_retained_participant_unlocks_after_emergency_or_normal_stop(self) -> None:
        for status in ("EMERGENCY_STOPPED", "STOPPED"):
            for mode, value, field, expected in (
                ("AMOUNT", 100000, "trade_amount_type", "AMOUNT"),
                ("QUANTITY", 10, "buy_qty", 10),
                ("AMOUNT", 200000, "buy_amount", 200000),
            ):
                with self.subTest(status=status, field=field), tempfile.TemporaryDirectory() as temp_dir:
                    path = self._config_path(Path(temp_dir))
                    self._write(
                        path,
                        {
                            "trade_amount_type": "QUANTITY" if field != "buy_amount" else "AMOUNT",
                            "buy_qty": 7,
                            "buy_amount": 100000,
                        },
                    )
                    self._write_state(path, status=status, trade_enabled=False)

                    result = gui_windows.MainWindow._write_stock_initial_buy_config(
                        self._owner("005930"), path, mode=mode, value=value
                    )

                    self.assertTrue(result["allowed"])
                    self.assertFalse(result["current_running"])
                    self.assertEqual(expected, self._read(path)[field])

    def test_stale_full_config_is_not_preserved_after_operation_stop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._config_path(Path(temp_dir))
            self._write(
                path,
                {"trade_amount_type": "QUANTITY", "buy_qty": 7, "buy_amount": 100000},
            )
            self._write_state(path, status="STOPPED", trade_enabled=False)
            dialog = self._owner("005930")
            dialog.stock_dir = path.parent
            dialog.config_path = path
            dialog.code = "005930"
            dialog.config = {
                "trade_amount_type": "AMOUNT",
                "buy_qty": 10,
                "buy_amount": 200000,
            }

            StockPolicyOverrideDialog.write_config(dialog)

            saved = self._read(path)
            self.assertEqual("AMOUNT", saved["trade_amount_type"])
            self.assertEqual(10, saved["buy_qty"])
            self.assertEqual(200000, saved["buy_amount"])
            self.assertTrue(dialog._start_budget_preservation_result["allowed"])
            self.assertFalse(dialog._start_budget_preservation_result["current_running"])

    def test_running_budget_dialog_previews_amount_and_keeps_fraction_light(self) -> None:
        dialog = RunningBudgetAdjustmentDialog(
            self.app.activeWindow(),
            stock_code="005930",
            stock_name="005930 삼성전자 | 금액 15,000원 | 0.5주",
            current_price=30000,
            config={"trade_amount_type": "AMOUNT", "buy_amount": 15000},
        )
        self.addCleanup(dialog.deleteLater)

        self.assertEqual("AMOUNT", dialog.mode)
        label_texts = [label.text() for label in dialog.findChildren(gui_windows.QLabel)]
        self.assertNotIn("005930 삼성전자", label_texts)
        self.assertEqual("현재가 30,000원", dialog.current_price_label.text())
        self.assertEqual(Qt.AlignCenter, dialog.current_price_label.alignment())
        self.assertGreaterEqual(
            dialog.current_price_label.font().pointSizeF(),
            self.app.font().pointSizeF() * 2,
        )
        self.assertEqual(
            QFontMetrics(dialog.value_edit.font()).horizontalAdvance("00,000,000") + 24,
            dialog.value_edit.width(),
        )
        dialog.value_edit.setText("100000000")
        self.assertEqual("15,000", dialog.value_edit.text())
        dialog.value_edit.setText("99999999")
        self.assertEqual("99,999,999", dialog.value_edit.text())
        self.assertNotEqual(
            QValidator.Acceptable,
            dialog.value_edit.validator().validate("100000000", 9)[0],
        )
        self.assertTrue(dialog.immediate_checkbox.isChecked())
        self.assertFalse(dialog.next_cycle_checkbox.isChecked())
        self.assertEqual("기본예산변경 | 005930 삼성전자", dialog.windowTitle())
        self.assertEqual("0.5주", dialog.current_reference_label.text())
        self.assertEqual(
            (gui_windows.INITIAL_BUY_BADGE_WIDTH, gui_windows.INITIAL_BUY_BADGE_HEIGHT),
            (dialog.current_badge.width(), dialog.current_badge.height()),
        )
        self.assertIn(gui_windows.INITIAL_BUY_AMOUNT_COLOR, dialog.current_badge.styleSheet())
        dialog.value_edit.setText("50000")
        self.assertIn("1.7주", dialog.changed_reference_label.text())
        self.assertIn(".7", dialog.changed_reference_label.text())
        self.assertNotIn("span", dialog.changed_reference_label.text().lower())

        dialog.next_cycle_checkbox.setChecked(True)
        self.assertFalse(dialog.immediate_checkbox.isChecked())
        self.assertTrue(dialog.next_cycle_checkbox.isChecked())
        dialog._validate_and_accept()
        self.assertEqual(
            {
                "mode": "AMOUNT",
                "value": 50000,
                "apply_timing": "NEXT_CYCLE",
                "apply_limit": False,
            },
            dialog.result,
        )

    def test_running_budget_dialog_previews_quantity_without_mode_switch(self) -> None:
        dialog = RunningBudgetAdjustmentDialog(
            self.app.activeWindow(),
            stock_code="005930",
            stock_name="삼성전자",
            current_price=3000,
            config={"trade_amount_type": "QUANTITY", "buy_qty": 21},
        )
        self.addCleanup(dialog.deleteLater)

        self.assertEqual("QUANTITY", dialog.mode)
        self.assertIn("63,000원", dialog.current_reference_label.text())
        dialog.value_edit.setText("52")
        self.assertIn("156,000원", dialog.changed_reference_label.text())
        self.assertFalse(hasattr(dialog, "mode_combo"))

        label_texts = [label.text() for label in dialog.findChildren(gui_windows.QLabel)]
        self.assertNotIn("005930 삼성전자", label_texts)
        self.assertEqual("현재가 3,000원", dialog.current_price_label.text())
        self.assertEqual(Qt.AlignCenter, dialog.current_price_label.alignment())
        self.assertNotEqual(
            QValidator.Acceptable,
            dialog.value_edit.validator().validate("100000000", 9)[0],
        )
        self.assertEqual("기본예산변경 | 005930 삼성전자", dialog.windowTitle())
        self.assertFalse(any("현재주가" in text for text in label_texts))
        self.assertFalse(any(text in {"변경", "현재 설정", "변경값 입력", "변경 후 참고"} for text in label_texts))

    def test_running_budget_dialog_rehydrates_pending_policy_and_limit_choice(self) -> None:
        dialog = RunningBudgetAdjustmentDialog(
            self.app.activeWindow(),
            stock_code="005930",
            stock_name="삼성전자",
            current_price=30000,
            config={"trade_amount_type": "AMOUNT", "buy_amount": 60000},
            pending_adjustment={
                "requested_value": 60000,
                "apply_policy": "NEXT_CYCLE",
                "apply_limit": True,
                "state": "WAIT_SELL",
            },
        )
        self.addCleanup(dialog.deleteLater)

        self.assertEqual("60,000", dialog.value_edit.text())
        self.assertFalse(dialog.immediate_checkbox.isChecked())
        self.assertTrue(dialog.next_cycle_checkbox.isChecked())
        self.assertTrue(dialog.apply_limit_checkbox.isChecked())

    def test_running_budget_dialog_spacing_and_checkbox_indent_are_aligned(self) -> None:
        dialog = RunningBudgetAdjustmentDialog(
            self.app.activeWindow(),
            stock_code="005930",
            stock_name="삼성전자",
            current_price=30000,
            config={"trade_amount_type": "AMOUNT", "buy_amount": 15000},
        )
        self.addCleanup(dialog.deleteLater)

        root = dialog.layout()
        self.assertEqual(0, root.spacing())
        self.assertEqual(Qt.AlignTop, root.alignment())
        self.assertEqual(
            [9, 10, 9, 9],
            [root.itemAt(index).spacerItem().sizeHint().height() for index in (1, 3, 5, 7)],
        )

        budget_row = root.itemAt(2).layout()
        self.assertIsNotNone(budget_row.itemAt(0).spacerItem())
        self.assertIsNotNone(budget_row.itemAt(budget_row.count() - 1).spacerItem())

        expected_indent = 24 + QFontMetrics(dialog.font()).horizontalAdvance("한")
        timing_row = root.itemAt(4).layout()
        limit_row = root.itemAt(6).layout()
        self.assertEqual(expected_indent, timing_row.contentsMargins().left())
        self.assertEqual(expected_indent, limit_row.contentsMargins().left())
        self.assertEqual(36, timing_row.spacing())
        self.assertTrue(dialog.immediate_checkbox.isChecked())
        self.assertFalse(dialog.next_cycle_checkbox.isChecked())
        self.assertTrue(dialog.validation_label.isHidden())

        dialog.show()
        self.app.processEvents()
        buttons = dialog.findChild(gui_windows.QDialogButtonBox)
        self.assertEqual(
            dialog.immediate_checkbox.geometry().x(),
            dialog.apply_limit_checkbox.geometry().x(),
        )
        self.assertEqual(
            9,
            buttons.geometry().y()
            - (
                dialog.apply_limit_checkbox.geometry().y()
                + dialog.apply_limit_checkbox.geometry().height()
            ),
        )

        dialog.value_edit.clear()
        dialog._validate_and_accept()
        self.assertFalse(dialog.validation_label.isHidden())
        self.assertEqual("변경값을 입력하세요.", dialog.validation_label.text())

    def test_running_budget_entry_routes_both_start_budget_regions_to_dialog(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._config_path(Path(temp_dir))
            self._write(path, {"trade_amount_type": "QUANTITY", "buy_qty": 7})
            host = self._owner("005930")
            host._main_routine_display_level = "stock"
            host._main_routine_initial_buy_badge_enabled = lambda: True
            host._stock_config_path_for_routine_row = lambda _row: path
            host._stock_start_budget_locked = lambda _path: True
            host._open_running_budget_adjustment_dialog = MagicMock()
            host.finish_routine_stock_initial_buy_edit = MagicMock()
            host.load_routine_table = MagicMock()

            table_item = MagicMock()
            host.routine_table = SimpleNamespace(item=lambda _row, _column: table_item)
            for action in (
                gui_windows.MainWindow.toggle_routine_stock_initial_buy_mode,
                gui_windows.MainWindow.start_routine_stock_initial_buy_edit,
            ):
                action(host, 0)

            self.assertEqual(2, host._open_running_budget_adjustment_dialog.call_count)
            host.finish_routine_stock_initial_buy_edit.assert_not_called()
            host.load_routine_table.assert_not_called()

    def test_ui_lock_releases_for_retained_stopped_participant(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = self._config_path(Path(temp_dir))
            self._write(path, {"trade_amount_type": "QUANTITY", "buy_qty": 7})
            owner = self._owner("005930")

            self._write_state(path, status="MONITORING", trade_enabled=True)
            self.assertTrue(gui_windows.MainWindow._stock_start_budget_locked(owner, path))

            self._write_state(path, status="STOPPED", trade_enabled=False)
            self.assertFalse(gui_windows.MainWindow._stock_start_budget_locked(owner, path))


if __name__ == "__main__":
    unittest.main()
