import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QPoint, Qt, QTimer
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog, QWidget

import gui_auto_trade_context_menu as context_menu
from gui_auto_trade_context_menu import (
    PersistentContextMenu,
    StockContextMenuCallbacks,
)
from tests.participant_owner_fixture import participant_owner


class PersistentStockContextMenuTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _visible_menu(self) -> PersistentContextMenu:
        menu = PersistentContextMenu()
        menu.show()
        self.app.processEvents()
        self.assertTrue(menu.isVisible())
        return menu

    def test_three_leaf_actions_run_once_each_without_closing(self) -> None:
        menu = self._visible_menu()
        callbacks = [Mock(), Mock(), Mock()]
        actions = [menu.addAction(label) for label in ("A", "B", "C")]
        for action, callback in zip(actions, callbacks):
            menu.register_persistent_action(action, callback)

        for action, callback in zip(actions, callbacks):
            self.assertTrue(menu._activate_registered_action(action))
            callback.assert_called_once_with()
            self.assertTrue(menu.isVisible())
        menu.close()

    def test_terminal_action_closes_after_exactly_one_callback(self) -> None:
        menu = self._visible_menu()
        callback = Mock()
        action = menu.addAction("등록해제")
        menu.register_persistent_action(action, callback, terminal=True)

        self.assertTrue(menu._activate_registered_action(action))
        callback.assert_called_once_with()
        self.assertFalse(menu.isVisible())

    def test_disabled_separator_and_submenu_parent_do_not_dispatch(self) -> None:
        menu = self._visible_menu()
        callback = Mock()
        disabled = menu.addAction("disabled")
        disabled.setEnabled(False)
        separator = menu.addSeparator()
        submenu = menu.addMenu("submenu")
        submenu_parent = submenu.menuAction()
        for action in (disabled, separator, submenu_parent):
            menu.register_persistent_action(action, callback)

        for action in (disabled, separator, submenu_parent):
            self.assertFalse(menu._activate_registered_action(action))
        callback.assert_not_called()
        self.assertTrue(menu.isVisible())
        self.assertIsInstance(submenu, PersistentContextMenu)
        menu.close()

    def test_submenu_leaf_uses_same_persistent_root(self) -> None:
        menu = self._visible_menu()
        submenu = menu.addMenu("조기마감")
        action = submenu.addAction("현재가")
        callback = Mock()
        menu.register_persistent_action(action, callback)

        self.assertTrue(submenu._activate_registered_action(action))
        callback.assert_called_once_with()
        self.assertTrue(menu.isVisible())
        menu.close()

    def test_keyboard_enter_keeps_menu_and_escape_closes(self) -> None:
        menu = self._visible_menu()
        action = menu.addAction("설정")
        callback = Mock()
        menu.register_persistent_action(action, callback)
        menu.setActiveAction(action)

        menu.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
        callback.assert_called_once_with()
        self.assertTrue(menu.isVisible())

        menu.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
        self.app.processEvents()
        self.assertFalse(menu.isVisible())

    def test_outside_mouse_release_preserves_qmenu_dismissal(self) -> None:
        host = QWidget()
        host.resize(300, 200)
        host.show()
        menu = PersistentContextMenu(host)
        menu.addAction("persistent")
        menu.popup(host.mapToGlobal(QPoint(10, 10)))
        self.app.processEvents()
        self.assertTrue(menu.isVisible())

        QTest.mousePress(menu, Qt.LeftButton, pos=QPoint(-20, -20))
        QTest.mouseRelease(menu, Qt.LeftButton, pos=QPoint(-20, -20))
        self.app.processEvents()
        self.assertFalse(menu.isVisible())
        host.close()

    def test_checkable_action_toggles_once_before_callback(self) -> None:
        menu = self._visible_menu()
        observed = []
        action = menu.addAction("모의세금")
        action.setCheckable(True)
        menu.register_persistent_action(action, lambda: observed.append(action.isChecked()))

        self.assertTrue(menu._activate_registered_action(action))
        self.assertEqual([True], observed)
        self.assertTrue(menu.isVisible())
        menu.close()

    def test_modal_dialog_action_returns_to_same_visible_menu(self) -> None:
        menu = self._visible_menu()
        callback = Mock()
        action = menu.addAction("설정")

        def open_dialog() -> None:
            callback()
            dialog = QDialog()
            QTimer.singleShot(0, dialog.accept)
            dialog.exec_()

        menu.register_persistent_action(action, open_dialog)
        self.assertTrue(menu._activate_registered_action(action))
        callback.assert_called_once_with()
        self.assertTrue(menu.isVisible())
        menu.close()

    @staticmethod
    def _callbacks(**overrides) -> StockContextMenuCallbacks:
        values = {
            "select_all": Mock(),
            "clear_selection": Mock(),
            "early_close": Mock(),
            "early_close_profit_loss": Mock(),
            "early_close_cancel": Mock(),
            "individual_liquidation": Mock(),
        }
        values.update(overrides)
        return StockContextMenuCallbacks(**values)

    def test_mock_actions_refresh_in_place_and_unregister_is_terminal(self) -> None:
        state = {"can_start": True, "current": True}
        start = Mock(side_effect=lambda: state.update(can_start=False))
        finish = Mock(side_effect=lambda: state.update(current=False))
        observed = []

        def mock_actions():
            return {
                "current": state["current"],
                "can_start": state["can_start"],
                "can_early_close": False,
                "can_immediate": False,
                "can_reset": False,
                "can_unregister": True,
                "start": start,
                "unregister": finish,
            }

        class ScriptedMenu(PersistentContextMenu):
            def exec_(self, _position):
                self.show()
                actions = self._mock_validation_actions
                self._activate_registered_action(actions["start"])
                observed.append((actions["start"].isEnabled(), self.isVisible()))
                self._activate_registered_action(actions["unregister"])
                observed.append(self.isVisible())

        callbacks = self._callbacks(mock_actions=mock_actions)
        with patch.object(
            context_menu,
            "_new_stock_context_menu",
            return_value=ScriptedMenu(),
        ):
            context_menu.show_monitor_stock_context_menu(
                None,
                QPoint(),
                has_selection=True,
                callbacks=callbacks,
            )

        start.assert_called_once_with()
        finish.assert_called_once_with()
        self.assertEqual([(False, True), False], observed)

    def test_mock_close_shell_dispatches_only_mock_lifecycle_callbacks(self) -> None:
        mock_early = Mock()
        mock_immediate = Mock()
        production_early = Mock()
        production_immediate = Mock()

        def mock_actions():
            return {
                "current": True,
                "can_start": False,
                "can_early_close": True,
                "can_immediate": True,
                "can_reset": False,
                "can_unregister": False,
                "early_close": mock_early,
                "immediate_liquidation": mock_immediate,
            }

        class ScriptedMenu(PersistentContextMenu):
            def exec_(self, _position):
                self.show()
                actions = self._mock_validation_actions
                self._activate_registered_action(actions["early_close"]["current"])
                self._activate_registered_action(
                    actions["individual_liquidation"]["current"]
                )

        callbacks = self._callbacks(
            early_close=production_early,
            individual_liquidation=production_immediate,
            mock_actions=mock_actions,
        )
        with patch.object(
            context_menu,
            "_new_stock_context_menu",
            return_value=ScriptedMenu(),
        ):
            context_menu.show_monitor_stock_context_menu(
                None,
                QPoint(),
                has_selection=True,
                callbacks=callbacks,
            )

        mock_early.assert_called_once_with("현재가즉시")
        mock_immediate.assert_called_once_with("현재가", "")
        production_early.assert_not_called()
        production_immediate.assert_not_called()

    def test_policy_blocked_production_close_entries_survive_persistent_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stock = Path(temp) / "005930_Samsung"
            stock.mkdir()
            (stock / "config.json").write_text(
                json.dumps({"operation_excluded": True}),
                encoding="utf-8",
            )
            (stock / "state.json").write_text(
                json.dumps({"status": "REVIEW_REQUIRED", "review_required": True}),
                encoding="utf-8",
            )
            early = Mock()
            individual = Mock()
            observed = []

            class ScriptedMenu(PersistentContextMenu):
                def exec_(self, _position):
                    self.show()
                    close_menu = next(
                        action.menu()
                        for action in self.actions()
                        if action.menu() is not None and action.text() == "조기마감"
                    )
                    individual_menu = next(
                        action.menu()
                        for action in self.actions()
                        if action.menu() is not None and action.text() == "개별청산"
                    )
                    close_market = next(
                        action for action in close_menu.actions() if action.text() == "시장가"
                    )
                    individual_market = next(
                        action for action in individual_menu.actions() if action.text() == "시장가"
                    )
                    observed.append(
                        (close_market.isEnabled(), individual_market.isEnabled())
                    )
                    self._activate_registered_action(close_market)
                    observed.append(
                        (close_market.isEnabled(), individual_market.isEnabled())
                    )
                    self._activate_registered_action(individual_market)

            callbacks = self._callbacks(
                early_close=early,
                individual_liquidation=individual,
            )
            owner = SimpleNamespace(
                _main_monitoring_auto_trade_operation_host=participant_owner(
                    {"005930"}
                )
            )
            with (
                patch.object(
                    context_menu,
                    "_new_stock_context_menu",
                    return_value=ScriptedMenu(),
                ),
                patch.object(context_menu, "_append_stock_context_decision"),
            ):
                context_menu.show_monitor_stock_context_menu(
                    owner,
                    QPoint(),
                    has_selection=True,
                    callbacks=callbacks,
                    operation_excluded=True,
                    selected_targets=[(stock, "005930", "Samsung")],
                )

        self.assertEqual([(True, True), (True, True)], observed)
        early.assert_called_once_with("시장가즉시")
        individual.assert_called_once_with("시장가", "5")


if __name__ == "__main__":
    unittest.main()
