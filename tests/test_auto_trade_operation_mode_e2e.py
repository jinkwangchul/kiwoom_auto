from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gui_auto_trade_status_ops as status_ops


class AutoTradeOperationModeE2ETest(unittest.TestCase):
    def _stock(self, root: Path) -> Path:
        stock_dir = root / "stocks" / "111111_테스트종목"
        stock_dir.mkdir(parents=True)
        (stock_dir / "config.json").write_text(
            json.dumps({"operation_mode": "SCHEDULED"}, ensure_ascii=False),
            encoding="utf-8",
        )
        (stock_dir / "state.json").write_text(
            json.dumps({"status": "STOPPED", "trade_enabled": False}, ensure_ascii=False),
            encoding="utf-8",
        )
        return stock_dir

    def _window(self, stock_dir: Path):
        parent = SimpleNamespace(refresh_all=Mock())
        window = SimpleNamespace(
            selected_stock_infos=lambda: [(stock_dir, "111111", "테스트종목")],
            current_selected_routine_name=lambda: "테스트루틴",
            refresh_all=Mock(),
            statusBarMessage=Mock(),
            parent=lambda: parent,
            recalculate_stock_status_by_operation_policy=Mock(
                return_value=("unchanged", "STOPPED", "STOPPED")
            ),
        )
        window.update_stock_operation_mode = (
            lambda target_dir, code, name, mode, updates=None:
            status_ops.auto_trade_update_stock_operation_mode(
                window,
                target_dir,
                code,
                name,
                mode,
                updates,
            )
        )
        return window, parent

    def test_success_persists_read_back_restores_and_refreshes_both_views_once(self) -> None:
        from gui_auto_trade_setting_window import AutoTradeSettingWindow

        with tempfile.TemporaryDirectory() as temp:
            stock_dir = self._stock(Path(temp))
            window, parent = self._window(stock_dir)

            with (
                patch.object(status_ops, "append_stock_log"),
                patch.object(status_ops, "append_changelog"),
            ):
                status_ops.auto_trade_set_selected_operation_mode(window, "CONTINUOUS")

            saved = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))
            restored_modes = AutoTradeSettingWindow.selected_operation_mode_set(
                SimpleNamespace(),
                [(stock_dir, "111111", "테스트종목")],
            )

        self.assertEqual("CONTINUOUS", saved["operation_mode"])
        self.assertEqual({"CONTINUOUS"}, restored_modes)
        window.refresh_all.assert_called_once_with()
        parent.refresh_all.assert_called_once_with()
        window.statusBarMessage.assert_called_once()
        self.assertIn("변경 완료", window.statusBarMessage.call_args.args[0])

    def test_write_failure_does_not_report_success_and_reloads_runtime_views(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = self._stock(Path(temp))
            window, parent = self._window(stock_dir)
            window.update_stock_operation_mode = Mock(return_value=False)

            with patch.object(status_ops, "append_changelog") as append_changelog:
                status_ops.auto_trade_set_selected_operation_mode(window, "CONTINUOUS")

        append_changelog.assert_not_called()
        window.refresh_all.assert_called_once_with()
        parent.refresh_all.assert_called_once_with()
        status_message = window.statusBarMessage.call_args.args[0]
        self.assertIn("운영방식 변경 실패:", status_message)
        self.assertIn("실패 1개", status_message)

    def test_read_back_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = self._stock(Path(temp))
            window, _parent = self._window(stock_dir)
            original = {"operation_mode": "SCHEDULED"}

            with (
                patch.object(
                    status_ops,
                    "read_json_dict",
                    side_effect=[dict(original), dict(original)],
                ),
                patch.object(status_ops, "append_stock_log") as append_stock_log,
                patch.object(status_ops.QMessageBox, "critical") as critical,
            ):
                result = status_ops.auto_trade_update_stock_operation_mode(
                    window,
                    stock_dir,
                    "111111",
                    "테스트종목",
                    "CONTINUOUS",
                )

        self.assertFalse(result)
        critical.assert_called_once()
        self.assertIn("read-back 실패", append_stock_log.call_args.args[2])

    def test_status_recalculation_failure_is_reported_after_mode_save(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock_dir = self._stock(Path(temp))
            window, parent = self._window(stock_dir)
            window.recalculate_stock_status_by_operation_policy.return_value = (
                "failed",
                "STOPPED",
                "RUNNING",
            )

            with (
                patch.object(status_ops, "append_stock_log"),
                patch.object(status_ops, "append_changelog"),
            ):
                status_ops.auto_trade_set_selected_operation_mode(window, "CONTINUOUS")

            saved = json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))

        self.assertEqual("CONTINUOUS", saved["operation_mode"])
        window.refresh_all.assert_called_once_with()
        parent.refresh_all.assert_called_once_with()
        self.assertIn("상태재판정 실패 1개", window.statusBarMessage.call_args.args[0])

    def test_cancelled_schedule_dialog_does_not_write_or_refresh(self) -> None:
        from PyQt5.QtWidgets import QDialog
        from gui_auto_trade_setting_window import AutoTradeSettingWindow

        stock_dir = Path("stocks/111111_테스트종목")
        window = SimpleNamespace(
            selected_stock_infos=lambda: [(stock_dir, "111111", "테스트종목")],
            set_selected_operation_mode=Mock(),
        )
        dialog = Mock()
        dialog.exec_.return_value = QDialog.Rejected

        with (
            patch("gui_auto_trade_setting_window.read_json_dict", return_value={"operation_mode": "SCHEDULED"}),
            patch("gui_auto_trade_setting_window.ScheduleOperationDialog", return_value=dialog),
        ):
            AutoTradeSettingWindow.set_selected_individual_schedule_time(window)

        window.set_selected_operation_mode.assert_not_called()


if __name__ == "__main__":
    unittest.main()
