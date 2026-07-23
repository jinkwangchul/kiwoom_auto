from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class _TableItem:
    def __init__(self, text: str) -> None:
        self._text = text

    def text(self) -> str:
        return self._text


class _SelectedIndex:
    def row(self) -> int:
        return 0


class _RegistrationTable:
    def selectionModel(self):
        return SimpleNamespace(selectedRows=lambda: [_SelectedIndex()])

    def item(self, row: int, column: int):
        values = ("111111", "테스트종목")
        return _TableItem(values[column]) if row == 0 and column < len(values) else None


class AutoTradeGuiE2ESyncTest(unittest.TestCase):
    def _registration_dialog(self, parent):
        return SimpleNamespace(
            result_table=_RegistrationTable(),
            parent=lambda: parent,
            search_stocks=Mock(),
        )

    def test_registration_backend_failure_is_not_reported_or_refreshed_as_success(self) -> None:
        import gui_search_stock_register_dialog as registration

        main = SimpleNamespace(refresh_all=Mock())
        parent = SimpleNamespace(
            refresh_stock_table=Mock(),
            parent=lambda: main,
        )
        dialog = self._registration_dialog(parent)

        with (
            patch.object(
                registration,
                "load_stock_library",
                return_value=[{"code": "111111", "name": "테스트종목", "market": "KOSPI"}],
            ),
            patch.object(registration, "read_base_stocks", return_value=[]),
            patch.object(registration, "append_base_stock", return_value=False),
            patch.object(registration, "append_changelog") as append_changelog,
            patch.object(registration.QMessageBox, "information"),
        ):
            registration.SearchStockRegisterDialog.register_selected_stocks(dialog)

        append_changelog.assert_not_called()
        parent.refresh_stock_table.assert_not_called()
        main.refresh_all.assert_not_called()
        dialog.search_stocks.assert_called_once_with()

    def test_registration_success_refreshes_register_and_monitoring_once(self) -> None:
        import gui_search_stock_register_dialog as registration

        main = SimpleNamespace(refresh_all=Mock())
        parent = SimpleNamespace(
            refresh_stock_table=Mock(),
            parent=lambda: main,
        )
        dialog = self._registration_dialog(parent)

        with (
            patch.object(
                registration,
                "load_stock_library",
                return_value=[{"code": "111111", "name": "테스트종목", "market": "KOSPI"}],
            ),
            patch.object(registration, "read_base_stocks", return_value=[]),
            patch.object(registration, "append_base_stock", return_value=True),
            patch.object(registration, "append_changelog"),
            patch.object(registration.QMessageBox, "information"),
        ):
            registration.SearchStockRegisterDialog.register_selected_stocks(dialog)

        parent.refresh_stock_table.assert_called_once_with()
        main.refresh_all.assert_called_once_with()
        dialog.search_stocks.assert_called_once_with()

    def test_assignment_refreshes_monitoring_and_open_settings_once(self) -> None:
        import gui_routine_assign_window as assignment

        auto_trade_setting = SimpleNamespace(refresh_all=Mock())
        main = SimpleNamespace(
            auto_trade_setting_window=auto_trade_setting,
            refresh_all=Mock(),
        )
        stock_register = SimpleNamespace(
            refresh_stock_table=Mock(),
            parent=lambda: main,
        )
        instance = SimpleNamespace(instance_id="instance-1", display_name="인스턴스1")
        definition = SimpleNamespace(definition_id="definition-1", display_name="루틴1")
        window = SimpleNamespace(
            checked_stocks=lambda: [("111111", "테스트종목", [])],
            checked_routines=lambda: [(instance, definition)],
            load_stock_table=Mock(),
            load_selected_routine_stocks=Mock(),
            clear_routine_checks=Mock(),
            show_status=Mock(),
            parent=lambda: stock_register,
        )
        repository = Mock()
        repository.ensure_stock_folder.return_value = assignment.PROJECT_ROOT / "stocks" / "111111_테스트종목"

        with (
            patch.object(assignment, "routine_action_reasons_for_stock", return_value=(True, {})),
            patch.object(assignment, "is_valid_stock_code", return_value=True),
            patch.object(
                assignment,
                "find_library_stock_by_code",
                return_value={"code": "111111", "name": "테스트종목"},
            ),
            patch.object(assignment, "update_base_stock_routine_instance", return_value=True),
            patch.object(assignment, "stock_repository_factory", return_value=repository),
            patch.object(assignment, "ensure_single_real_trade_routine_for_stock"),
            patch.object(assignment, "write_blocked_action_report", return_value=None),
            patch.object(assignment, "append_changelog"),
            patch.object(assignment.QMessageBox, "information"),
            patch("gui_windows.sip.isdeleted", return_value=False),
        ):
            from gui_windows import MainWindow

            main.refresh_auto_trade_assignment_views = lambda: MainWindow.refresh_auto_trade_assignment_views(main)
            assignment.RoutineAssignWindow.apply_routines_to_checked_stocks(window)

        stock_register.refresh_stock_table.assert_called_once_with()
        main.refresh_all.assert_called_once_with()
        auto_trade_setting.refresh_all.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
