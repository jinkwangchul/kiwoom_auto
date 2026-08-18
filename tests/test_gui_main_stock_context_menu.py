# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QEvent, QItemSelectionModel, QPoint, QPointF, Qt
from PyQt5.QtGui import QMouseEvent, QPainter, QPixmap
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QMenu,
    QStyle,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
    QWidgetAction,
)

import gui_auto_trade_context_menu as common_menu
import gui_auto_trade_close as close_ops
import gui_auto_trade_run_control as run_control
import gui_auto_trade_status_ops as status_ops
import gui_auto_trade_unregister as unregister_ops
import gui_main_emergency_ops as emergency_ops
import gui_main_stock_context_menu as monitoring_menu
import gui_windows
from gui_main_table_loader import (
    ROUTINE_DEFINITION_ID_ROLE,
    ROUTINE_INSTANCE_ID_ROLE,
    ROUTINE_ROW_KIND_ROLE,
    ROUTINE_ROW_PARENT,
    ROUTINE_ROW_STOCK,
    ROUTINE_STOCK_CODE_ROLE,
    ROUTINE_STOCK_DISPLAY_ROLE,
    ROUTINE_STOCK_INITIAL_BUY_ROLE,
    ROUTINE_STOCK_METRICS_ROLE,
    ROUTINE_STOCK_PROFIT_LED_ROLE,
    ROUTINE_STOCK_NAME_ROLE,
    ROUTINE_STOCK_PATH_ROLE,
    ROUTINE_STOCK_VALUES_ROLE,
)


class _FakeAction:
    def __init__(self, text: str, *, separator: bool = False) -> None:
        self.text = text
        self.separator = separator
        self.enabled = True
        self.icon = None
        self.properties: dict[str, object] = {}

    def setEnabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)

    def setText(self, text: str) -> None:
        self.text = text

    def setIcon(self, icon) -> None:
        self.icon = icon

    def setProperty(self, name: str, value: object) -> None:
        self.properties[name] = value

    def property(self, name: str):
        return self.properties.get(name)


class _FakeMenu:
    root = None
    chosen_text = None
    chosen_menu_title = None

    def __init__(self, _parent=None, *, title: str = "") -> None:
        self.title = title
        self.actions: list[_FakeAction] = []
        self.submenus: list[_FakeMenu] = []
        self.enabled = True
        if not title:
            _FakeMenu.root = self

    def setToolTipsVisible(self, _visible: bool) -> None:
        return

    def addAction(self, text: str) -> _FakeAction:
        action = _FakeAction(text)
        self.actions.append(action)
        return action

    def addMenu(self, title: str):
        submenu = _FakeMenu(title=title)
        self.submenus.append(submenu)
        return submenu

    def addSeparator(self):
        action = _FakeAction("<separator>", separator=True)
        self.actions.append(action)
        return action

    def setEnabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)

    def exec_(self, _position):
        if _FakeMenu.chosen_text is None:
            return None
        menus = [self, *self.submenus]
        for submenu in self.submenus:
            menus.extend(submenu.submenus)
        for menu in menus:
            if (
                _FakeMenu.chosen_menu_title is not None
                and menu.title != _FakeMenu.chosen_menu_title
            ):
                continue
            for action in menu.actions:
                if action.text == _FakeMenu.chosen_text:
                    return action
        return None


class _Window(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.routine_table = QTableWidget(0, 1)
        self.routine_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.routine_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.routine_table.setColumnWidth(0, 2400)
        self.routine_table.resize(2600, 400)
        self.routine_table.show()
        self._main_monitoring_stock_operation_adapter = None

    def refresh_auto_trade_assignment_views(self) -> None:
        return

    def handle_routine_group_name_double_click(self, _row: int) -> bool:
        return False

    def handle_routine_instance_name_double_click(self, _row: int) -> bool:
        return False


class MainMonitoringStockContextMenuTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.window = _Window()
        _FakeMenu.root = None
        _FakeMenu.chosen_text = None
        _FakeMenu.chosen_menu_title = None

    def tearDown(self) -> None:
        self.window.routine_table.close()
        self.window.close()
        self.temp.cleanup()

    def _add_row(
        self,
        *,
        kind: str,
        code: str = "",
        name: str = "",
        instance_id: str = "",
        definition_id: str = "",
        hidden: bool = False,
    ) -> int:
        row = self.window.routine_table.rowCount()
        self.window.routine_table.insertRow(row)
        item = QTableWidgetItem(name or kind)
        item.setData(ROUTINE_ROW_KIND_ROLE, kind)
        item.setData(ROUTINE_STOCK_CODE_ROLE, code)
        item.setData(ROUTINE_STOCK_NAME_ROLE, name)
        item.setData(ROUTINE_INSTANCE_ID_ROLE, instance_id)
        if definition_id:
            item.setData(ROUTINE_DEFINITION_ID_ROLE, definition_id)
        if kind == ROUTINE_ROW_STOCK:
            stock_dir = self.root / f"{code}_{name}"
            stock_dir.mkdir()
            item.setData(ROUTINE_STOCK_PATH_ROLE, str(stock_dir))
            item.setData(
                ROUTINE_STOCK_VALUES_ROLE,
                [
                    f"{code} {name}",
                    "금액 0",
                    "수동",
                    "●",
                    "감시/대기",
                    "루틴",
                    "10분/시장가",
                    "보유(0)",
                    "가격(0)",
                    "수익(0 / 0.00%)",
                    "매매(0 / 0)",
                    "한도(0)",
                    "소모(0 / 0.0%)",
                ],
            )
            item.setData(ROUTINE_STOCK_METRICS_ROLE, ())
            item.setData(ROUTINE_STOCK_PROFIT_LED_ROLE, "gray")
            item.setData(
                ROUTINE_STOCK_INITIAL_BUY_ROLE,
                {"mode": "AMOUNT", "value_text": "0"},
            )
        self.window.routine_table.setItem(row, 0, item)
        self.window.routine_table.setRowHidden(row, hidden)
        return row

    def _write_stock_config(self, row: int, *, mode: str) -> Path:
        item = self.window.routine_table.item(row, 0)
        stock_dir = Path(str(item.data(ROUTINE_STOCK_PATH_ROLE)))
        (stock_dir / "config.json").write_text(
            json.dumps({"operation_mode": mode}, ensure_ascii=False),
            encoding="utf-8",
        )
        (stock_dir / "state.json").write_text("{}", encoding="utf-8")
        return stock_dir

    def _operation_double_click_event(self, row: int) -> QMouseEvent:
        index = self.window.routine_table.model().index(row, 0)
        rect = gui_windows._routine_stock_token_rect(
            self.window.routine_table,
            index,
            gui_windows.ROUTINE_STOCK_OPERATION_TOKEN_INDEX,
        )
        return QMouseEvent(
            QEvent.MouseButtonDblClick,
            QPointF(rect.center()),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )

    def _name_double_click_event(self, row: int) -> QMouseEvent:
        index = self.window.routine_table.model().index(row, 0)
        rect = gui_windows._routine_stock_name_rect(
            self.window.routine_table,
            index,
        )
        return QMouseEvent(
            QEvent.MouseButtonDblClick,
            QPointF(rect.center()),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )

    def test_operation_token_rect_uses_same_layout_as_stock_token_rect(self) -> None:
        row = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005930",
            name="삼성전자",
            instance_id="instance-a",
        )
        self.app.processEvents()
        index = self.window.routine_table.model().index(row, 0)
        controller = gui_windows._RoutineTreeInteractionController(self.window)

        self.assertEqual(
            controller._stock_legacy_metric_rect(index, 2),
            gui_windows._routine_stock_token_rect(
                self.window.routine_table,
                index,
                gui_windows.ROUTINE_STOCK_OPERATION_TOKEN_INDEX,
            ),
        )

    def test_operation_token_double_click_routes_only_operation_rect(self) -> None:
        row = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005930",
            name="삼성전자",
            instance_id="instance-a",
        )
        self.window._main_routine_initial_buy_badge_enabled = Mock(return_value=False)
        self.window.handle_routine_stock_operation_double_click = Mock(return_value=True)
        self.window.handle_routine_stock_name_double_click = Mock(return_value=True)
        self.window.handle_routine_stock_code_double_click = Mock(return_value=True)
        controller = gui_windows._RoutineTreeInteractionController(self.window)
        self.app.processEvents()

        handled = controller.eventFilter(
            self.window.routine_table.viewport(),
            self._operation_double_click_event(row),
        )

        self.assertTrue(handled)
        self.window.handle_routine_stock_operation_double_click.assert_called_once_with(row)

        self.window.handle_routine_stock_operation_double_click.reset_mock()
        index = self.window.routine_table.model().index(row, 0)
        handled = controller.eventFilter(
            self.window.routine_table.viewport(),
            self._name_double_click_event(row),
        )

        self.assertTrue(handled)
        self.window.handle_routine_stock_operation_double_click.assert_not_called()
        self.window.handle_routine_stock_name_double_click.assert_called_once_with(row)

        self.window.handle_routine_stock_name_double_click.reset_mock()
        code_rect = gui_windows._routine_stock_code_rect(
            self.window.routine_table,
            index,
        )
        code_event = QMouseEvent(
            QEvent.MouseButtonDblClick,
            QPointF(code_rect.center()),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
        handled = controller.eventFilter(
            self.window.routine_table.viewport(),
            code_event,
        )

        self.assertTrue(handled)
        self.window.handle_routine_stock_name_double_click.assert_not_called()
        self.window.handle_routine_stock_code_double_click.assert_called_once_with(row)

    def test_stock_name_double_click_uses_common_operation_exclusion_contract(self) -> None:
        row = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005930",
            name="삼성전자",
            instance_id="instance-a",
        )
        target = monitoring_menu._stock_target_for_row(self.window, row)
        self.assertIsNotNone(target)

        with patch.object(
            gui_windows,
            "handle_stock_name_operation_exclusion_double_click",
            return_value=True,
        ) as common_handler:
            handled = gui_windows.MainWindow.handle_routine_stock_name_double_click(
                self.window,
                row,
            )

        self.assertTrue(handled)
        adapter = self.window._main_monitoring_stock_operation_adapter
        common_handler.assert_called_once_with(
            adapter,
            (target.stock_dir, "005930", "삼성전자"),
        )

    def test_routine_instance_name_double_click_toggles_set_exclusion_true_if_any_allowed(
        self,
    ) -> None:
        row = self._add_row(
            kind=gui_windows.ROUTINE_ROW_CHILD,
            code="",
            name="인스턴스명",
            instance_id="instance-a",
        )
        instance_stock_dirs = [
            self.root / "005380_현대차",
            self.root / "005930_삼성전자",
        ]
        for stock_dir in instance_stock_dirs:
            stock_dir.mkdir(parents=True, exist_ok=True)

        with (
            patch.object(
                gui_windows,
                "MainMonitoringStockOperationAdapter",
            ) as adapter_class,
            patch.object(self.window, "refresh_auto_trade_assignment_views") as refresh,
        ):
            adapter = Mock()
            adapter.set_stock_operation_exclusion = Mock(return_value=True)
            self.window._routine_instance_stock_dirs = Mock(  # type: ignore[assignment]
                return_value=instance_stock_dirs
            )
            adapter_class.return_value = adapter
            refresh.return_value = None
            handled = gui_windows.MainWindow.handle_routine_instance_name_double_click(
                self.window,
                row,
            )

        self.assertTrue(handled)
        self.assertEqual(1, refresh.call_count)
        self.assertEqual(
            [
                ((instance_stock_dirs[0], "005380", "현대차"), True, False),
                ((instance_stock_dirs[1], "005930", "삼성전자"), True, False),
            ],
            [
                ((call.args[0][0], call.args[0][1], call.args[0][2]), call.args[1], call.kwargs["refresh"])
                for call in adapter.set_stock_operation_exclusion.call_args_list
            ],
        )
        self.assertEqual(0, len(adapter.start_selected_auto_trades.call_args_list))

    def test_routine_instance_name_double_click_toggles_set_exclusion_false_if_all_excluded(
        self,
    ) -> None:
        row = self._add_row(
            kind=gui_windows.ROUTINE_ROW_CHILD,
            code="",
            name="인스턴스명",
            instance_id="instance-a",
        )
        instance_stock_dirs = [
            self.root / "005380_현대차",
            self.root / "005930_삼성전자",
        ]
        for stock_dir in instance_stock_dirs:
            stock_dir.mkdir(parents=True, exist_ok=True)
            (stock_dir / "config.json").write_text(
                json.dumps({"operation_excluded": True}, ensure_ascii=False),
                encoding="utf-8",
            )

        with (
            patch.object(
                gui_windows,
                "MainMonitoringStockOperationAdapter",
            ) as adapter_class,
            patch.object(self.window, "refresh_auto_trade_assignment_views") as refresh,
        ):
            adapter = Mock()
            adapter.set_stock_operation_exclusion = Mock(return_value=True)
            self.window._routine_instance_stock_dirs = Mock(  # type: ignore[assignment]
                return_value=instance_stock_dirs
            )
            adapter_class.return_value = adapter
            refresh.return_value = None
            handled = gui_windows.MainWindow.handle_routine_instance_name_double_click(
                self.window,
                row,
            )

        self.assertTrue(handled)
        self.assertEqual(1, refresh.call_count)
        self.assertEqual(
            [
                ((instance_stock_dirs[0], "005380", "현대차"), False, False),
                ((instance_stock_dirs[1], "005930", "삼성전자"), False, False),
            ],
            [
                ((call.args[0][0], call.args[0][1], call.args[0][2]), call.args[1], call.kwargs["refresh"])
                for call in adapter.set_stock_operation_exclusion.call_args_list
            ],
        )
        self.assertEqual(0, len(adapter.start_selected_auto_trades.call_args_list))

    def test_routine_instance_name_double_click_toggles_set_exclusion_true_if_mixed_states(
        self,
    ) -> None:
        row = self._add_row(
            kind=gui_windows.ROUTINE_ROW_CHILD,
            code="",
            name="인스턴스명",
            instance_id="instance-a",
        )
        instance_stock_dirs = [
            self.root / "005380_현대차",
            self.root / "005930_삼성전자",
        ]
        for stock_dir in instance_stock_dirs:
            stock_dir.mkdir(parents=True, exist_ok=True)
        (instance_stock_dirs[0] / "config.json").write_text(
            json.dumps({"operation_excluded": True}, ensure_ascii=False),
            encoding="utf-8",
        )

        with (
            patch.object(
                gui_windows,
                "MainMonitoringStockOperationAdapter",
            ) as adapter_class,
            patch.object(self.window, "refresh_auto_trade_assignment_views") as refresh,
        ):
            adapter = Mock()
            adapter.set_stock_operation_exclusion = Mock(return_value=True)
            self.window._routine_instance_stock_dirs = Mock(  # type: ignore[assignment]
                return_value=instance_stock_dirs
            )
            adapter_class.return_value = adapter
            refresh.return_value = None
            handled = gui_windows.MainWindow.handle_routine_instance_name_double_click(
                self.window,
                row,
            )

        self.assertTrue(handled)
        self.assertEqual(1, refresh.call_count)
        self.assertEqual(
            [
                ((instance_stock_dirs[0], "005380", "현대차"), True, False),
                ((instance_stock_dirs[1], "005930", "삼성전자"), True, False),
            ],
            [
                ((call.args[0][0], call.args[0][1], call.args[0][2]), call.args[1], call.kwargs["refresh"])
                for call in adapter.set_stock_operation_exclusion.call_args_list
            ],
        )
        self.assertEqual(0, len(adapter.start_selected_auto_trades.call_args_list))

    def test_routine_group_name_double_click_toggles_set_exclusion_true_if_any_allowed(
        self,
    ) -> None:
        row = self._add_row(
            kind=ROUTINE_ROW_PARENT,
            name="그룹명",
            definition_id="definition-a",
        )
        group_stock_dirs = {
            "instance-a": [
                self.root / "005380_현대차",
                self.root / "005930_삼성전자",
            ],
            "instance-b": [self.root / "086520_에코프로"],
        }
        for stock_dirs in group_stock_dirs.values():
            for stock_dir in stock_dirs:
                stock_dir.mkdir(parents=True, exist_ok=True)

        with (
            patch.object(
                gui_windows,
                "MainMonitoringStockOperationAdapter",
            ) as adapter_class,
            patch.object(self.window, "refresh_auto_trade_assignment_views") as refresh,
        ):
            adapter = Mock()
            adapter.set_stock_operation_exclusion = Mock(return_value=True)
            self.window._routine_instance_stock_dirs = Mock(  # type: ignore[assignment]
                side_effect=lambda instance_id: group_stock_dirs[instance_id]
            )
            adapter_class.return_value = adapter
            self.window._routine_instance_ids_by_definition = {
                "definition-a": ("instance-a", "instance-b"),
            }
            refresh.return_value = None
            handled = gui_windows.MainWindow.handle_routine_group_name_double_click(
                self.window,
                row,
            )

        self.assertTrue(handled)
        self.assertEqual(1, refresh.call_count)
        self.assertEqual(
            {
                (str(self.root / "005380_현대차"), "005380", "현대차"),
                (str(self.root / "005930_삼성전자"), "005930", "삼성전자"),
                (str(self.root / "086520_에코프로"), "086520", "에코프로"),
            },
            {
                (str(target.args[0][0]), target.args[0][1], target.args[0][2])
                for target in adapter.set_stock_operation_exclusion.call_args_list
            },
        )
        self.assertEqual(
            {call.args[1] for call in adapter.set_stock_operation_exclusion.call_args_list},
            {True},
        )
        self.assertEqual(0, len(adapter.start_selected_auto_trades.call_args_list))

    def test_routine_group_name_double_click_toggles_set_exclusion_false_if_all_excluded(
        self,
    ) -> None:
        row = self._add_row(
            kind=ROUTINE_ROW_PARENT,
            name="그룹명",
            definition_id="definition-a",
        )
        group_stock_dirs = {
            "instance-a": [
                self.root / "005380_현대차",
                self.root / "005930_삼성전자",
            ],
            "instance-b": [self.root / "086520_에코프로"],
        }
        for stock_dirs in group_stock_dirs.values():
            for stock_dir in stock_dirs:
                stock_dir.mkdir(parents=True, exist_ok=True)
                (stock_dir / "config.json").write_text(
                    json.dumps({"operation_excluded": True}, ensure_ascii=False),
                    encoding="utf-8",
                )

        with (
            patch.object(
                gui_windows,
                "MainMonitoringStockOperationAdapter",
            ) as adapter_class,
            patch.object(self.window, "refresh_auto_trade_assignment_views") as refresh,
        ):
            adapter = Mock()
            adapter.set_stock_operation_exclusion = Mock(return_value=True)
            self.window._routine_instance_stock_dirs = Mock(  # type: ignore[assignment]
                side_effect=lambda instance_id: group_stock_dirs[instance_id]
            )
            adapter_class.return_value = adapter
            self.window._routine_instance_ids_by_definition = {
                "definition-a": ("instance-a", "instance-b"),
            }
            refresh.return_value = None
            handled = gui_windows.MainWindow.handle_routine_group_name_double_click(
                self.window,
                row,
            )

        self.assertTrue(handled)
        self.assertEqual(1, refresh.call_count)
        self.assertEqual(
            {call.args[1] for call in adapter.set_stock_operation_exclusion.call_args_list},
            {False},
        )
        self.assertEqual(0, len(adapter.start_selected_auto_trades.call_args_list))
        self.assertEqual(0, len(adapter.start_selected_auto_trades.call_args_list))

    def test_routine_group_name_double_click_toggles_set_exclusion_true_if_mixed_states(
        self,
    ) -> None:
        row = self._add_row(
            kind=ROUTINE_ROW_PARENT,
            name="그룹명",
            definition_id="definition-a",
        )
        group_stock_dirs = {
            "instance-a": [
                self.root / "005380_현대차",
                self.root / "005930_삼성전자",
            ],
            "instance-b": [self.root / "086520_에코프로"],
        }
        for stock_dirs in group_stock_dirs.values():
            for idx, stock_dir in enumerate(stock_dirs):
                stock_dir.mkdir(parents=True, exist_ok=True)
                if idx == 0:
                    (stock_dir / "config.json").write_text(
                        json.dumps({"operation_excluded": True}, ensure_ascii=False),
                        encoding="utf-8",
                    )

        with (
            patch.object(
                gui_windows,
                "MainMonitoringStockOperationAdapter",
            ) as adapter_class,
            patch.object(self.window, "refresh_auto_trade_assignment_views") as refresh,
        ):
            adapter = Mock()
            adapter.set_stock_operation_exclusion = Mock(return_value=True)
            self.window._routine_instance_stock_dirs = Mock(  # type: ignore[assignment]
                side_effect=lambda instance_id: group_stock_dirs[instance_id]
            )
            adapter_class.return_value = adapter
            self.window._routine_instance_ids_by_definition = {
                "definition-a": ("instance-a", "instance-b"),
            }
            refresh.return_value = None
            handled = gui_windows.MainWindow.handle_routine_group_name_double_click(
                self.window,
                row,
            )

        self.assertTrue(handled)
        self.assertEqual(1, refresh.call_count)
        self.assertEqual(
            {
                (str(self.root / "005380_현대차"), "005380", "현대차"),
                (str(self.root / "005930_삼성전자"), "005930", "삼성전자"),
                (str(self.root / "086520_에코프로"), "086520", "에코프로"),
            },
            {
                (str(target.args[0][0]), target.args[0][1], target.args[0][2])
                for target in adapter.set_stock_operation_exclusion.call_args_list
            },
        )
        self.assertEqual({True}, {call.args[1] for call in adapter.set_stock_operation_exclusion.call_args_list})
        self.assertEqual(0, len(adapter.start_selected_auto_trades.call_args_list))

    def test_routine_group_name_double_click_routes_to_controller(self) -> None:
        row = self._add_row(
            kind=ROUTINE_ROW_PARENT,
            name="그룹명",
            definition_id="definition-a",
        )
        controller = gui_windows._RoutineTreeInteractionController(self.window)
        name_rect = Mock()
        name_rect.contains.return_value = True

        with (
            patch.object(self.window, "handle_routine_group_name_double_click") as handler,
            patch.object(
                controller,
                "_parent_name_rect",
                return_value=name_rect,
            ),
        ):
            handler.return_value = True
            event = QMouseEvent(
                QEvent.MouseButtonDblClick,
                QPointF(0, 0),
                Qt.LeftButton,
                Qt.LeftButton,
                Qt.NoModifier,
            )
            handled = controller.eventFilter(
                self.window.routine_table.viewport(),
                event,
            )

        self.assertTrue(handled)
        handler.assert_called_once_with(row)

    def test_visible_code_mouse_double_click_opens_then_clears_selection(self) -> None:
        row = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005930",
            name="삼성전자",
            instance_id="instance-a",
        )
        self.window.handle_routine_stock_code_double_click = (
            lambda target_row: gui_windows.MainWindow.handle_routine_stock_code_double_click(
                self.window,
                target_row,
            )
        )
        controller = gui_windows._RoutineTreeInteractionController(self.window)
        self.window.routine_table.viewport().installEventFilter(controller)
        self._select_rows(row)
        self.window.routine_table.setCurrentCell(row, 0)
        index = self.window.routine_table.model().index(row, 0)
        code_rect = gui_windows._routine_stock_code_rect(
            self.window.routine_table,
            index,
        )

        with patch.object(
            gui_windows,
            "open_stock_instance_chart",
            return_value=object(),
        ) as opener:
            QTest.mouseDClick(
                self.window.routine_table.viewport(),
                Qt.LeftButton,
                Qt.NoModifier,
                code_rect.center(),
            )
            self.app.processEvents()

        opener.assert_called_once_with(
            "005930",
            trade_date=None,
            parent=self.window,
        )
        self.assertEqual([], self.window.routine_table.selectionModel().selectedRows())
        self.assertFalse(self.window.routine_table.currentIndex().isValid())

    def test_visible_code_double_click_clears_selection_only_after_chart_open(self) -> None:
        row = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005930",
            name="삼성전자",
            instance_id="instance-a",
        )
        self._select_rows(row)
        self.window.routine_table.setCurrentCell(row, 0)
        chart = object()

        with patch.object(
            gui_windows,
            "open_stock_instance_chart",
            return_value=chart,
        ) as opener:
            handled = gui_windows.MainWindow.handle_routine_stock_code_double_click(
                self.window,
                row,
            )

        self.assertTrue(handled)
        opener.assert_called_once_with(
            "005930",
            trade_date=None,
            parent=self.window,
        )
        self.assertEqual([], self.window.routine_table.selectionModel().selectedRows())
        self.assertFalse(self.window.routine_table.currentIndex().isValid())

        self._select_rows(row)
        self.window.routine_table.setCurrentCell(row, 0)
        with patch.object(
            gui_windows,
            "open_stock_instance_chart",
            return_value=chart,
        ):
            handled = gui_windows.MainWindow.handle_routine_stock_code_double_click(
                self.window,
                row,
            )
        self.assertTrue(handled)
        self.assertEqual([], self.window.routine_table.selectionModel().selectedRows())
        self.assertFalse(self.window.routine_table.currentIndex().isValid())

        self._select_rows(row)
        self.window.routine_table.setCurrentCell(row, 0)
        with patch.object(
            gui_windows,
            "open_stock_instance_chart",
            return_value=None,
        ):
            handled = gui_windows.MainWindow.handle_routine_stock_code_double_click(
                self.window,
                row,
            )
        self.assertFalse(handled)
        self.assertEqual(
            [row],
            [
                index.row()
                for index in self.window.routine_table.selectionModel().selectedRows()
            ],
        )
        self.assertTrue(self.window.routine_table.currentIndex().isValid())

    def test_stock_name_double_click_toggles_shared_config_through_setting_backend(self) -> None:
        row = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005930",
            name="삼성전자",
            instance_id="instance-a",
        )
        stock_dir = self._write_stock_config(row, mode="SCHEDULED")
        self.window.refresh_auto_trade_assignment_views = Mock()
        self.window.statusBar = Mock(return_value=Mock())

        import gui_auto_trade_setting_window as setting_window

        with (
            patch.object(monitoring_menu, "auto_trade_running_registered_operation_targets", return_value=[]),
            patch.object(status_ops, "append_stock_log"),
            patch.object(status_ops, "append_changelog"),
            patch.object(status_ops, "show_toast"),
        ):
            self.assertTrue(
                gui_windows.MainWindow.handle_routine_stock_name_double_click(
                    self.window,
                    row,
                )
            )
            self.assertTrue(
                json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))[
                    "operation_excluded"
                ]
            )
            self.app.processEvents()

            self.assertTrue(
                gui_windows.MainWindow.handle_routine_stock_name_double_click(
                    self.window,
                    row,
                )
            )
            self.assertFalse(
                json.loads((stock_dir / "config.json").read_text(encoding="utf-8"))[
                    "operation_excluded"
                ]
            )
            self.app.processEvents()

        self.assertEqual(
            2,
            self.window.refresh_auto_trade_assignment_views.call_count,
        )

    def test_stock_name_double_click_keeps_setting_running_block(self) -> None:
        row = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005930",
            name="삼성전자",
            instance_id="instance-a",
        )
        stock_dir = self._write_stock_config(row, mode="SCHEDULED")
        before = (stock_dir / "config.json").read_bytes()
        self.window.refresh_auto_trade_assignment_views = Mock()
        status_bar = Mock()
        self.window.statusBar = Mock(return_value=status_bar)

        with (
            patch.object(
                monitoring_menu,
                "auto_trade_running_registered_operation_targets",
                return_value=[(stock_dir, "005930", "삼성전자")],
            ),
            patch.object(status_ops, "append_stock_log"),
            patch.object(status_ops, "append_changelog"),
            patch.object(status_ops, "show_toast"),
        ):
            self.assertTrue(
                gui_windows.MainWindow.handle_routine_stock_name_double_click(
                    self.window,
                    row,
                )
            )

        self.assertEqual(before, (stock_dir / "config.json").read_bytes())
        self.window.refresh_auto_trade_assignment_views.assert_not_called()
        status_bar.showMessage.assert_called_once()

    def test_stock_delegate_paint_smoke_with_token_and_legacy_paths(self) -> None:
        row = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005930",
            name="삼성전자",
            instance_id="instance-a",
        )
        self.app.processEvents()
        item = self.window.routine_table.item(row, 0)
        values = item.data(ROUTINE_STOCK_VALUES_ROLE)
        item.setData(
            ROUTINE_STOCK_DISPLAY_ROLE,
            [
                {"text": str(value), "foreground": "#111827", "alignment": int(Qt.AlignCenter)}
                for value in values
            ],
        )
        delegate = gui_windows._RoutineTreeItemDelegate(self.window.routine_table)
        index = self.window.routine_table.model().index(row, 0)
        pixmap = QPixmap(2600, 48)

        for selected, with_tokens in ((False, True), (True, True), (False, False)):
            if not with_tokens:
                item.setData(ROUTINE_STOCK_DISPLAY_ROLE, [])
            option = QStyleOptionViewItem()
            option.widget = self.window.routine_table
            option.rect = self.window.routine_table.visualRect(index)
            option.font = self.window.routine_table.font()
            option.palette = self.window.routine_table.palette()
            option.state = QStyle.State_Enabled
            if selected:
                option.state |= QStyle.State_Selected
            pixmap.fill()
            painter = QPainter(pixmap)
            try:
                delegate.paint(painter, option, index)
            finally:
                painter.end()

    def test_operation_token_manual_mode_uses_common_double_click_handler(self) -> None:
        row = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005930",
            name="삼성전자",
            instance_id="instance-a",
        )
        stock_dir = self._write_stock_config(row, mode="CONTINUOUS")
        self._select_rows(row)

        with (
            patch.object(
                monitoring_menu,
                "handle_auto_trade_operation_mode_double_click",
                return_value={"requested": 1, "succeeded": 1, "failed": 0, "results": []},
            ) as common_handler,
            patch.object(monitoring_menu, "ScheduleOperationDialog") as dialog,
        ):
            handled = gui_windows.MainWindow.handle_routine_stock_operation_double_click(
                self.window,
                row,
            )

        self.assertTrue(handled)
        adapter = self.window._main_monitoring_stock_operation_adapter
        common_handler.assert_called_once_with(
            adapter,
            (stock_dir, "005930", "삼성전자"),
        )
        dialog.assert_not_called()
        self.assertEqual([(stock_dir, "005930", "삼성전자")], adapter.target_snapshot())

    def test_operation_token_scheduled_mode_has_no_confirmation_dialog(self) -> None:
        row = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005930",
            name="삼성전자",
            instance_id="instance-a",
        )
        stock_dir = self._write_stock_config(row, mode="SCHEDULED")

        with (
            patch.object(
                monitoring_menu,
                "handle_auto_trade_operation_mode_double_click",
                return_value={"requested": 1, "succeeded": 1, "failed": 0, "results": []},
            ) as common_handler,
            patch.object(gui_windows.QMessageBox, "question") as question,
        ):
            handled = gui_windows.MainWindow.handle_routine_stock_operation_double_click(
                self.window,
                row,
            )

        self.assertTrue(handled)
        question.assert_not_called()
        adapter = self.window._main_monitoring_stock_operation_adapter
        common_handler.assert_called_once_with(
            adapter,
            (stock_dir, "005930", "삼성전자"),
        )

    def test_common_handler_scheduled_mode_uses_single_target_continuous_backend(self) -> None:
        row = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005930",
            name="삼성전자",
            instance_id="instance-a",
        )
        stock_dir = self._write_stock_config(row, mode="SCHEDULED")
        target = monitoring_menu._stock_target_for_row(self.window, row)
        adapter = monitoring_menu.MainMonitoringStockOperationAdapter(
            self.window,
            [target],
        )

        with (
            patch.object(gui_windows.QMessageBox, "question") as question,
            patch.object(
                status_ops,
                "auto_trade_set_operation_mode_for_targets",
                return_value={"requested": 1, "succeeded": 1, "failed": 0, "results": []},
            ) as backend,
        ):
            result = adapter.handle_operation_mode_double_click()

        self.assertEqual(1, result["requested"])
        question.assert_not_called()
        backend.assert_called_once_with(
            adapter,
            [(stock_dir, "005930", "삼성전자")],
            "CONTINUOUS",
        )

    def test_common_handler_manual_mode_applies_global_schedule_without_dialog(self) -> None:
        row = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005930",
            name="삼성전자",
            instance_id="instance-a",
        )
        stock_dir = self._write_stock_config(row, mode="CONTINUOUS")
        target = monitoring_menu._stock_target_for_row(self.window, row)
        adapter = monitoring_menu.MainMonitoringStockOperationAdapter(
            self.window,
            [target],
        )

        with (
            patch.object(monitoring_menu, "ScheduleOperationDialog") as dialog,
            patch.object(
                status_ops,
                "read_global_schedule",
                return_value={"start_time": "09:10:00", "end_buy_time": "14:20:00"},
            ) as read_schedule,
            patch.object(
                status_ops,
                "auto_trade_set_operation_mode_for_targets",
                return_value={"requested": 1, "succeeded": 1, "failed": 0, "results": []},
            ) as backend,
        ):
            result = adapter.handle_operation_mode_double_click()

        self.assertEqual(1, result["requested"])
        dialog.assert_not_called()
        read_schedule.assert_called_once_with()
        backend.assert_called_once()
        self.assertEqual(adapter, backend.call_args.args[0])
        self.assertEqual([(stock_dir, "005930", "삼성전자")], backend.call_args.args[1])
        self.assertEqual("SCHEDULED", backend.call_args.args[2])
        self.assertEqual(
            {
                "start_time": "09:10:00",
                "end_buy_time": "14:20:00",
            },
            backend.call_args.args[3],
        )

    def _select_rows(self, *rows: int) -> None:
        selection_model = self.window.routine_table.selectionModel()
        for row in rows:
            selection_model.select(
                self.window.routine_table.model().index(row, 0),
                QItemSelectionModel.Select | QItemSelectionModel.Rows,
            )

    def test_selection_adapter_keeps_multiple_instance_identity(self) -> None:
        first = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005930",
            name="삼성전자",
            instance_id="instance-a",
        )
        second = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005380",
            name="현대차",
            instance_id="instance-b",
        )
        self._select_rows(first, second)

        targets = monitoring_menu.selected_main_monitoring_stock_targets(
            self.window
        )

        self.assertEqual(["005930", "005380"], [item.code for item in targets])
        self.assertEqual(
            ["instance-a", "instance-b"],
            [item.routine_instance_id for item in targets],
        )

    def test_context_row_outside_selection_becomes_only_target(self) -> None:
        first = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005930",
            name="삼성전자",
            instance_id="instance-a",
        )
        second = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005380",
            name="현대차",
            instance_id="instance-b",
        )
        self._select_rows(first)

        monitoring_menu.ensure_main_monitoring_context_stock_selected(
            self.window,
            second,
        )

        targets = monitoring_menu.selected_main_monitoring_stock_targets(
            self.window
        )
        self.assertEqual(["005380"], [item.code for item in targets])

    def test_operation_adapter_forwards_existing_backends(self) -> None:
        row = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005930",
            name="삼성전자",
            instance_id="instance-a",
        )
        self._select_rows(row)
        targets = monitoring_menu.selected_main_monitoring_stock_targets(
            self.window
        )
        adapter = monitoring_menu.MainMonitoringStockOperationAdapter(
            self.window,
            targets,
        )

        with (
            patch.object(
                monitoring_menu,
                "auto_trade_apply_selected_early_close",
            ) as early_close,
            patch.object(
                monitoring_menu,
                "auto_trade_apply_selected_early_close_profit_loss",
            ) as profit_loss,
            patch.object(
                monitoring_menu,
                "auto_trade_cancel_selected_early_close",
            ) as cancel,
            patch.object(
                monitoring_menu,
                "auto_trade_apply_selected_individual_liquidation_method",
            ) as individual,
        ):
            adapter.apply_selected_early_close("시장가즉시", source="우클릭")
            adapter.apply_selected_early_close_profit_loss()
            adapter.cancel_selected_early_close()
            adapter.apply_selected_individual_liquidation_method("현재가", "15")

        early_close.assert_called_once_with(
            adapter,
            "시장가즉시",
            source="우클릭",
            extra_policy=None,
            show_error_dialog=True,
            show_result_toast=True,
        )
        profit_loss.assert_called_once_with(adapter)
        cancel.assert_called_once_with(adapter)
        individual.assert_called_once_with(
            adapter,
            "현재가",
            "15",
            show_error_dialog=True,
        )
        adapter.close()

    def test_select_all_uses_only_visible_stock_rows(self) -> None:
        parent = self._add_row(kind=ROUTINE_ROW_PARENT)
        visible = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005930",
            name="삼성전자",
            instance_id="instance-a",
        )
        hidden = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005380",
            name="현대차",
            instance_id="instance-b",
            hidden=True,
        )

        monitoring_menu.select_all_visible_main_monitoring_stocks(self.window)

        selected_rows = {
            index.row()
            for index in self.window.routine_table.selectionModel().selectedRows()
        }
        self.assertEqual({visible}, selected_rows)
        self.assertNotIn(parent, selected_rows)
        self.assertNotIn(hidden, selected_rows)

    def test_clear_selection_removes_only_stock_rows(self) -> None:
        parent = self._add_row(kind=ROUTINE_ROW_PARENT)
        stock = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005930",
            name="삼성전자",
            instance_id="instance-a",
        )
        self._select_rows(parent, stock)

        monitoring_menu.clear_main_monitoring_stock_selection(self.window)

        selected_rows = {
            index.row()
            for index in self.window.routine_table.selectionModel().selectedRows()
        }
        self.assertEqual({parent}, selected_rows)

    def test_non_stock_row_does_not_open_stock_menu(self) -> None:
        row = self._add_row(kind=ROUTINE_ROW_PARENT)
        self.app.processEvents()
        position = self.window.routine_table.visualItemRect(
            self.window.routine_table.item(row, 0)
        ).center()

        with patch.object(
            monitoring_menu,
            "show_monitor_stock_context_menu",
        ) as show_menu:
            opened = monitoring_menu.show_main_monitoring_stock_context_menu(
                self.window,
                position,
            )

        self.assertFalse(opened)
        show_menu.assert_not_called()

    def test_ats_session_clicks_stay_open_and_refresh_filled_dots(self) -> None:
        runtime_state = {
            "extra1": False,
            "extra2": False,
            "extra3": False,
        }
        toggle_calls = []

        def toggle(key: str, enabled: bool, label: str) -> None:
            toggle_calls.append((key, enabled, label))
            runtime_state[key] = enabled

        root = QMenu()
        with (
            patch.object(
                common_menu,
                "manual_ats_visible_session_keys",
                return_value=("extra1", "extra2"),
            ),
            patch.object(
                common_menu,
                "manual_ats_session_labels",
                return_value={"extra1": "장전프리", "extra2": "장마감NXT"},
            ),
        ):
            ats = common_menu._add_ats_settings_menu(
                root,
                has_selection=True,
                state_getter=lambda: dict(runtime_state),
                toggle=toggle,
                liquidation_available_getter=lambda: any(runtime_state.values()),
            )
        menu = ats["menu"]
        menu.show()
        self.app.processEvents()
        toggle_filter = menu._ats_toggle_filter

        def has_visible_icon(action) -> bool:
            image = action.icon().pixmap(16, 16).toImage()
            return any(
                image.pixelColor(x, y).alpha() > 0
                for x in range(image.width())
                for y in range(image.height())
            )

        def click(action) -> bool:
            position = menu.actionGeometry(action).center()
            event = QMouseEvent(
                QEvent.MouseButtonRelease,
                QPointF(position),
                Qt.LeftButton,
                Qt.LeftButton,
                Qt.NoModifier,
            )
            return toggle_filter.eventFilter(menu, event)

        first = ats["session_actions"][0][2]
        second = ats["session_actions"][1][2]
        self.assertFalse(has_visible_icon(first))
        self.assertFalse(has_visible_icon(second))
        self.assertFalse(ats["market"].isEnabled())
        self.assertFalse(ats["current"].isEnabled())

        self.assertTrue(click(first))
        self.assertTrue(menu.isVisible())
        self.assertTrue(has_visible_icon(first))
        self.assertFalse(has_visible_icon(second))
        self.assertTrue(ats["market"].isEnabled())
        self.assertTrue(ats["current"].isEnabled())

        self.assertTrue(click(second))
        self.assertTrue(menu.isVisible())
        self.assertTrue(has_visible_icon(first))
        self.assertTrue(has_visible_icon(second))

        self.assertTrue(click(first))
        self.assertTrue(menu.isVisible())
        self.assertFalse(has_visible_icon(first))
        self.assertTrue(has_visible_icon(second))
        self.assertTrue(ats["market"].isEnabled())
        self.assertTrue(click(second))
        self.assertFalse(has_visible_icon(second))
        self.assertFalse(ats["market"].isEnabled())
        self.assertFalse(ats["current"].isEnabled())
        self.assertEqual(
            [
                ("extra1", True, "장전프리"),
                ("extra2", True, "장마감NXT"),
                ("extra1", False, "장전프리"),
                ("extra2", False, "장마감NXT"),
            ],
            toggle_calls,
        )
        self.assertFalse(click(ats["market"]))
        menu.close()
        root.close()

    def test_stock_early_close_menu_uses_deep_green_text_only(self) -> None:
        root = QMenu()
        actions = common_menu._add_early_close_menu(
            root,
            has_selection=True,
            operation_policy={"early_close": {"method": "시장가"}},
        )

        self.assertEqual(
            common_menu.CONTEXT_MENU_EARLY_CLOSE_TEXT_COLOR,
            actions["menu"].menuAction().property("menuTextColor"),
        )
        for key in ("routine", "market", "current", "profit_loss", "carry"):
            self.assertEqual(
                common_menu.CONTEXT_MENU_EARLY_CLOSE_TEXT_COLOR,
                actions[key].property("menuTextColor"),
            )
        self.assertIsNone(actions["cancel"].property("menuTextColor"))
        self.assertIsInstance(root.style(), common_menu._MenuActionColorProxyStyle)
        self.assertIsInstance(
            actions["menu"].style(),
            common_menu._MenuActionColorProxyStyle,
        )
        root.close()

    def test_group_context_early_close_action_uses_danger_text_only(self) -> None:
        row_item = Mock(row=Mock(return_value=0))
        first_item = Mock()
        first_item.data.side_effect = lambda role: {
            gui_windows.ROUTINE_ROW_KIND_ROLE: gui_windows.ROUTINE_ROW_PARENT,
            gui_windows.ROUTINE_DEFINITION_ID_ROLE: "definition-a",
        }.get(role)
        table = Mock()
        table.itemAt.return_value = row_item
        table.item.return_value = first_item
        name_rect = Mock()
        name_rect.contains.return_value = True
        new_routine_action = Mock()
        early_action = Mock()
        immediate_action = Mock()
        menu = Mock()
        menu.addAction.side_effect = (
            new_routine_action,
            early_action,
            immediate_action,
        )
        menu.exec_.return_value = None
        window = SimpleNamespace(
            routine_table=table,
            _routine_tree_interaction_controller=SimpleNamespace(
                _parent_name_rect=Mock(return_value=name_rect),
            ),
            _routine_instance_ids_by_definition={"definition-a": ()},
            _routine_instance_has_assigned_stocks=Mock(return_value=False),
            _set_routine_operation_actions_enabled=lambda actions, enabled: None,
            open_routine_settings_from_main_table=Mock(),
            request_routine_definition_operation=Mock(),
        )
        definition = SimpleNamespace(display_name="성장주")

        with (
            patch.object(gui_windows, "QMenu", return_value=menu),
            patch.object(gui_windows, "routine_definition_by_id", return_value=definition),
        ):
            gui_windows.MainWindow.open_routine_context_menu(window, QPoint())

        early_action.setProperty.assert_called_once_with(
            "menuTextColor",
            common_menu.CONTEXT_MENU_DANGER_TEXT_COLOR,
        )
        immediate_action.setProperty.assert_not_called()

    def test_routine_context_early_close_action_uses_danger_text_only(self) -> None:
        row_item = Mock(row=Mock(return_value=0))
        first_item = Mock()
        first_item.data.side_effect = lambda role: {
            gui_windows.ROUTINE_ROW_KIND_ROLE: gui_windows.ROUTINE_ROW_CHILD,
            gui_windows.ROUTINE_INSTANCE_ID_ROLE: "instance-a",
        }.get(role)
        table = Mock()
        table.itemAt.return_value = row_item
        table.item.return_value = first_item
        actions = tuple(Mock() for _ in range(5))
        menu = Mock()
        menu.addAction.side_effect = actions
        menu.exec_.return_value = None
        window = SimpleNamespace(
            routine_table=table,
            open_routine_settings_from_main_table=Mock(),
            start_routine_instance_name_edit=Mock(),
            open_routine_instance_stock_register_from_main_table=Mock(),
            _routine_instance_has_assigned_stocks=Mock(return_value=True),
            _set_routine_operation_actions_enabled=lambda action_set, enabled: None,
            request_routine_operation=Mock(),
        )
        instance = SimpleNamespace(display_name="오전루틴")

        with (
            patch.object(gui_windows, "QMenu", return_value=menu),
            patch.object(gui_windows, "routine_instance_by_id", return_value=instance),
        ):
            gui_windows.MainWindow.open_routine_context_menu(window, QPoint())

        actions[3].setProperty.assert_called_once_with(
            "menuTextColor",
            common_menu.CONTEXT_MENU_DANGER_TEXT_COLOR,
        )
        actions[4].setProperty.assert_not_called()

    def test_monitor_continuous_ats_submenu_uses_shared_adapter_backends(self) -> None:
        row = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005930",
            name="삼성전자",
            instance_id="instance-a",
        )
        self._write_stock_config(row, mode="CONTINUOUS")
        self._select_rows(row)
        self.app.processEvents()
        position = self.window.routine_table.visualItemRect(
            self.window.routine_table.item(row, 0)
        ).center()

        for chosen_text in ("ATS 주간", "시장가"):
            with self.subTest(chosen_text=chosen_text):
                _FakeMenu.root = None
                _FakeMenu.chosen_menu_title = "ATS설정"
                _FakeMenu.chosen_text = chosen_text
                with (
                    patch.object(common_menu, "QMenu", _FakeMenu),
                    patch.object(
                        common_menu,
                        "manual_ats_visible_session_keys",
                        return_value=("extra1", "extra2"),
                    ),
                    patch.object(
                        common_menu,
                        "manual_ats_session_labels",
                        return_value={"extra1": "ATS 장전", "extra2": "ATS 주간"},
                    ),
                    patch.object(
                        monitoring_menu.MainMonitoringStockOperationAdapter,
                        "selected_manual_ats_state",
                        return_value={"extra1": True, "extra2": False, "extra3": False},
                    ),
                    patch.object(
                        monitoring_menu,
                        "auto_trade_set_selected_manual_ats_flag",
                    ) as save_backend,
                    patch.object(
                        monitoring_menu,
                        "auto_trade_execute_selected_manual_ats_liquidation",
                    ) as liquidation_backend,
                ):
                    opened = monitoring_menu.show_main_monitoring_stock_context_menu(
                        self.window,
                        position,
                    )

                self.assertTrue(opened)
                adapter = self.window._main_monitoring_stock_operation_adapter
                if chosen_text == "ATS 주간":
                    save_backend.assert_called_once_with(
                        adapter,
                        "extra2",
                        True,
                        "ATS 주간",
                    )
                    liquidation_backend.assert_not_called()
                else:
                    liquidation_backend.assert_called_once_with(
                        adapter,
                        "시장가",
                        {"extra1": True, "extra2": False, "extra3": False},
                        adapter.target_snapshot(),
                        ("extra1", "extra2"),
                        ("extra1",),
                    )
                    save_backend.assert_not_called()

    def test_stock_context_menu_adds_stock_register_entry_for_row_instance(self) -> None:
        row = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005930",
            name="삼성전자",
            instance_id="instance-a",
        )
        self.window.open_routine_instance_stock_register_from_main_table = Mock()
        self.app.processEvents()
        position = self.window.routine_table.visualItemRect(
            self.window.routine_table.item(row, 0)
        ).center()
        _FakeMenu.chosen_text = "종목등록"

        with patch.object(common_menu, "QMenu", _FakeMenu):
            opened = monitoring_menu.show_main_monitoring_stock_context_menu(
                self.window,
                position,
        )

        self.assertTrue(opened)
        self.assertEqual(
            ["종목등록", "등록해제", "간이차트"],
            [
                action.text
                for action in _FakeMenu.root.actions
                if not action.separator
            ][-3:],
        )
        self.window.open_routine_instance_stock_register_from_main_table.assert_called_once_with(
            "instance-a"
        )

    def test_stock_register_context_uses_right_clicked_stock_instance(self) -> None:
        first = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005930",
            name="삼성전자",
            instance_id="instance-a",
        )
        second = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005380",
            name="현대차",
            instance_id="instance-b",
        )
        self._select_rows(first)
        self.window.open_routine_instance_stock_register_from_main_table = Mock()
        self.app.processEvents()
        position = self.window.routine_table.visualItemRect(
            self.window.routine_table.item(second, 0)
        ).center()
        _FakeMenu.chosen_text = "종목등록"

        with patch.object(common_menu, "QMenu", _FakeMenu):
            opened = monitoring_menu.show_main_monitoring_stock_context_menu(
                self.window,
                position,
            )

        self.assertTrue(opened)
        self.window.open_routine_instance_stock_register_from_main_table.assert_called_once_with(
            "instance-b"
        )

    def test_monitor_operation_exclusion_uses_setting_backend_and_shared_config(self) -> None:
        row = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005930",
            name="삼성전자",
            instance_id="instance-a",
        )
        stock_dir = self._write_stock_config(row, mode="SCHEDULED")
        (stock_dir / "state.json").write_text(
            json.dumps({"status": "STOPPED"}),
            encoding="utf-8",
        )
        self.window.refresh_auto_trade_assignment_views = Mock()
        self._select_rows(row)
        self.app.processEvents()
        position = self.window.routine_table.visualItemRect(
            self.window.routine_table.item(row, 0)
        ).center()

        import gui_auto_trade_setting_window as setting_window

        with (
            patch.object(common_menu, "QMenu", _FakeMenu),
            patch.object(status_ops, "append_stock_log"),
            patch.object(status_ops, "append_changelog"),
            patch.object(status_ops, "show_toast") as toast,
        ):
            _FakeMenu.chosen_text = "운영제외"
            self.assertTrue(
                monitoring_menu.show_main_monitoring_stock_context_menu(
                    self.window,
                    position,
                )
            )
            config = json.loads(
                (stock_dir / "config.json").read_text(encoding="utf-8")
            )
            self.assertTrue(config["operation_excluded"])
            self.assertTrue(
                setting_window.auto_trade_stock_operation_excluded(stock_dir)
            )
            self.assertTrue(
                self.window._main_monitoring_stock_operation_adapter
                .selected_stocks_are_operation_excluded()
            )
            self.assertEqual("SCHEDULED", config["operation_mode"])

            _FakeMenu.root = None
            _FakeMenu.chosen_text = "제외해제"
            self.assertTrue(
                monitoring_menu.show_main_monitoring_stock_context_menu(
                    self.window,
                    position,
                )
            )
            config = json.loads(
                (stock_dir / "config.json").read_text(encoding="utf-8")
            )
            self.assertFalse(config["operation_excluded"])
            self.assertFalse(
                setting_window.auto_trade_stock_operation_excluded(stock_dir)
            )
            self.assertFalse(
                self.window._main_monitoring_stock_operation_adapter
                .selected_stocks_are_operation_excluded()
            )
            self.assertEqual("SCHEDULED", config["operation_mode"])

        self.assertTrue(toast.call_args_list)
        self.assertTrue(
            all(call.args[0] is self.window for call in toast.call_args_list)
        )
        self.assertTrue(stock_dir.exists())
        self.assertTrue((stock_dir / "state.json").exists())
        self.assertEqual(
            2,
            self.window.refresh_auto_trade_assignment_views.call_count,
        )

    def test_monitor_emergency_stop_uses_window_as_toast_parent(self) -> None:
        row = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005930",
            name="삼성전자",
            instance_id="instance-a",
        )
        stock_dir = self._write_stock_config(row, mode="SCHEDULED")
        target = monitoring_menu.MainMonitoringStockTarget(
            stock_dir=stock_dir,
            code="005930",
            name="삼성전자",
            routine_instance_id="instance-a",
        )
        adapter = monitoring_menu.MainMonitoringStockOperationAdapter(
            self.window,
            [target],
        )
        adapter.refresh_auto_trade_assignment_views = Mock()
        adapter.statusBarMessage = Mock()

        with (
            patch.object(
                emergency_ops,
                "_evaluate_emergency_preflight",
                return_value=(True, ""),
            ),
            patch.object(emergency_ops, "update_runtime_stock_status", return_value=True) as update,
            patch.object(emergency_ops, "append_changelog"),
            patch.object(emergency_ops, "show_toast") as toast,
        ):
            result = adapter.emergency_stop_selected_auto_trade_stocks()

        self.assertEqual(1, result["changed_count"])
        self.assertIs(update.call_args.args[0], adapter)
        self.assertIs(toast.call_args.kwargs["parent"], self.window)
        self.assertIsInstance(toast.call_args.kwargs["parent"], QWidget)

    def test_monitor_unregister_uses_window_as_toast_parent(self) -> None:
        row = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005930",
            name="삼성전자",
            instance_id="instance-a",
        )
        stock_dir = self._write_stock_config(row, mode="SCHEDULED")
        target = monitoring_menu.MainMonitoringStockTarget(
            stock_dir=stock_dir,
            code="005930",
            name="삼성전자",
            routine_instance_id="instance-a",
        )
        adapter = monitoring_menu.MainMonitoringStockOperationAdapter(
            self.window,
            [target],
        )
        adapter.refresh_auto_trade_assignment_views = Mock()
        adapter.statusBar_message = Mock()
        self.window.refresh_auto_trade_assignment_views = Mock()

        with (
            patch.object(
                unregister_ops,
                "auto_trade_unregister_category",
                return_value={
                    "category": "immediate",
                    "code": "005930",
                    "name": "삼성전자",
                },
            ),
            patch.object(unregister_ops, "update_base_stock_routines", return_value=True),
            patch.object(unregister_ops, "append_changelog"),
            patch.object(unregister_ops, "show_toast") as toast,
        ):
            adapter.unregister_selected_auto_trade_stocks()

        self.assertIs(toast.call_args.args[0], self.window)
        self.assertIsInstance(toast.call_args.args[0], QWidget)

    def test_monitor_has_no_selected_emergency_release_entrypoint(self) -> None:
        target = monitoring_menu.MainMonitoringStockTarget(
            stock_dir=self.root,
            code="005930",
            name="삼성전자",
            routine_instance_id="instance-a",
        )
        adapter = monitoring_menu.MainMonitoringStockOperationAdapter(
            self.window,
            [target],
        )
        self.assertFalse(
            hasattr(adapter, "release_selected_emergency_stopped_auto_trade_stocks")
        )

    def test_monitor_early_close_uses_window_as_toast_parent(self) -> None:
        target = monitoring_menu.MainMonitoringStockTarget(
            stock_dir=self.root,
            code="005930",
            name="삼성전자",
            routine_instance_id="instance-a",
        )
        adapter = monitoring_menu.MainMonitoringStockOperationAdapter(
            self.window,
            [target],
        )

        with (
            patch.object(
                close_ops,
                "_kiwoom_server_login_block_message",
                return_value="로그인 필요",
            ),
            patch.object(close_ops, "show_toast") as toast,
        ):
            adapter.apply_selected_early_close("시장가")

        self.assertIs(toast.call_args.args[0], self.window)
        self.assertIsInstance(toast.call_args.args[0], QWidget)

    def test_main_context_stock_row_keeps_stock_register_action(self) -> None:
        row = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005930",
            name="삼성전자",
            instance_id="instance-a",
        )
        self.window.open_routine_instance_stock_register_from_main_table = Mock()
        self.app.processEvents()
        position = self.window.routine_table.visualItemRect(
            self.window.routine_table.item(row, 0)
        ).center()
        _FakeMenu.chosen_text = "종목등록"

        with patch.object(common_menu, "QMenu", _FakeMenu):
            gui_windows.MainWindow.open_routine_context_menu(
                self.window,
                position,
            )

        self.assertEqual(
            ["종목등록", "등록해제", "간이차트"],
            [
                action.text
                for action in _FakeMenu.root.actions
                if not action.separator
            ][-3:],
        )
        self.window.open_routine_instance_stock_register_from_main_table.assert_called_once_with(
            "instance-a"
        )

    def test_stock_row_without_instance_does_not_open_stock_register_menu(self) -> None:
        row = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005930",
            name="삼성전자",
            instance_id="",
        )
        self.window.open_routine_instance_stock_register_from_main_table = Mock()
        self.app.processEvents()
        position = self.window.routine_table.visualItemRect(
            self.window.routine_table.item(row, 0)
        ).center()
        _FakeMenu.chosen_text = "종목등록"

        opened = monitoring_menu.show_main_monitoring_stock_context_menu(
            self.window,
            position,
        )

        self.assertFalse(opened)
        self.assertIsNone(_FakeMenu.root)
        self.window.open_routine_instance_stock_register_from_main_table.assert_not_called()

    def test_main_window_routes_only_stock_row_to_stock_profile(self) -> None:
        table = Mock()
        item = Mock()
        item.row.return_value = 3
        first_item = Mock()
        first_item.data.return_value = ROUTINE_ROW_STOCK
        table.itemAt.return_value = item
        table.item.return_value = first_item
        owner = Mock(routine_table=table)

        with patch.object(
            gui_windows,
            "show_main_monitoring_stock_context_menu",
        ) as show_menu:
            gui_windows.MainWindow.open_routine_context_menu(
                owner,
                QPoint(10, 10),
            )

        show_menu.assert_called_once_with(owner, QPoint(10, 10))

    def test_monitor_profile_menu_and_callbacks(self) -> None:
        callbacks = common_menu.StockContextMenuCallbacks(
            select_all=Mock(),
            clear_selection=Mock(),
            early_close=Mock(),
            early_close_profit_loss=Mock(),
            early_close_cancel=Mock(),
            individual_liquidation=Mock(),
        )
        _FakeMenu.chosen_menu_title = "조기마감"
        _FakeMenu.chosen_text = "시장가"
        with (
            patch.object(common_menu, "QMenu", _FakeMenu),
            patch.object(
                common_menu,
                "_context_menu_operation_policy",
                return_value={
                    "liquidation": {
                        "method": "시장가",
                        "minutes_before_regular_close": "7",
                    }
                },
            ),
        ):
            common_menu.show_monitor_stock_context_menu(
                self.window.routine_table,
                QPoint(),
                has_selection=True,
                callbacks=callbacks,
            )

        root = _FakeMenu.root
        self.assertEqual(
            ["전체선택", "선택해제"],
            [action.text for action in root.actions if not action.separator],
        )
        self.assertEqual(
            ["조기마감", "개별청산"],
            [menu.title for menu in root.submenus],
        )
        early_menu = root.submenus[0]
        self.assertEqual(
            ["루틴마감", "시장가", "현재가", "손/익절", "이월", "취소"],
            [action.text for action in early_menu.actions if not action.separator],
        )
        self.assertEqual("<separator>", early_menu.actions[5].text)
        root_labels = [action.text for action in root.actions]
        for excluded in (
            "등록해제",
            "시간변경",
            "변경리셋",
            "ATS설정",
        ):
            self.assertNotIn(excluded, root_labels)
        callbacks.early_close.assert_called_once_with("시장가즉시")

    def test_monitor_profile_switches_operation_exclusion_label_and_callback(self) -> None:
        for excluded, label, callback_name in (
            (False, "운영제외", "set_operation_exclusion"),
            (True, "제외해제", "clear_operation_exclusion"),
        ):
            with self.subTest(excluded=excluded):
                callbacks = common_menu.StockContextMenuCallbacks(
                    select_all=Mock(),
                    clear_selection=Mock(),
                    early_close=Mock(),
                    early_close_profit_loss=Mock(),
                    early_close_cancel=Mock(),
                    individual_liquidation=Mock(),
                    set_operation_exclusion=Mock(),
                    clear_operation_exclusion=Mock(),
                )
                _FakeMenu.root = None
                _FakeMenu.chosen_text = label
                _FakeMenu.chosen_menu_title = None
                with patch.object(common_menu, "QMenu", _FakeMenu):
                    common_menu.show_monitor_stock_context_menu(
                        self.window.routine_table,
                        QPoint(),
                        has_selection=True,
                        callbacks=callbacks,
                        operation_excluded=excluded,
                    )

                getattr(callbacks, callback_name).assert_called_once_with()
                other_name = (
                    "clear_operation_exclusion"
                    if callback_name == "set_operation_exclusion"
                    else "set_operation_exclusion"
                )
                getattr(callbacks, other_name).assert_not_called()

    def test_settings_chart_action_opens_all_selected_targets(self) -> None:
        selected = [
            (self.root / "005930_삼성전자", "005930", "삼성전자"),
            (self.root / "012330_현대모비스", "012330", "현대모비스"),
            (self.root / "086520_에코프로", "086520", "에코프로"),
        ]
        window = Mock()
        window._all_stocks_scope_active = True
        window._stock_status_filter = "running"
        window.stock_table.itemAt.return_value = None
        window.stock_table.viewport.return_value.mapToGlobal.return_value = QPoint()
        window.selected_stock_infos.return_value = selected
        window.selected_operation_mode_set.return_value = set()
        _FakeMenu.chosen_text = "간이차트"

        with (
            patch.object(common_menu, "QMenu", _FakeMenu),
            patch.object(
                common_menu,
                "open_selected_stock_instance_charts",
            ) as batch_open,
        ):
            common_menu.show_auto_trade_stock_context_menu(window, QPoint())

        batch_open.assert_called_once_with(window, selected)
        chart_action = _FakeMenu.root.actions[-1]
        self.assertEqual("간이차트", chart_action.text)
        self.assertFalse(chart_action.separator)
        self.assertTrue(_FakeMenu.root.actions[-2].separator)
        self.assertIsNone(chart_action.property("stockChartActionColor"))

    def test_real_chart_action_uses_native_qaction_appearance(self) -> None:
        menu = common_menu._new_stock_context_menu(self.window)
        normal_action = menu.addAction("등록해제")
        separator = menu.addSeparator()
        chart_action = menu.addAction("간이차트")

        self.assertEqual([normal_action, separator, chart_action], menu.actions())
        self.assertTrue(separator.isSeparator())
        self.assertNotIsInstance(chart_action, QWidgetAction)
        self.assertEqual(normal_action.font(), chart_action.font())
        self.assertTrue(chart_action.icon().isNull())
        self.assertIsNone(chart_action.property("stockChartActionColor"))
        menu.close()

    def test_real_chart_action_preserves_native_row_geometry_and_click_target(self) -> None:
        menu = common_menu._new_stock_context_menu(self.window)
        menu.addAction("등록해제")
        menu.addSeparator()
        chart_action = menu.addAction("간이차트")
        triggered = Mock()
        chart_action.triggered.connect(triggered)

        menu.popup(QPoint(20, 20))
        self.app.processEvents()
        normal_rect = menu.actionGeometry(menu.actions()[0])
        row_rect = menu.actionGeometry(chart_action)
        self.assertTrue(row_rect.isValid())
        self.assertEqual(normal_rect.height(), row_rect.height())
        QTest.mouseClick(
            menu,
            Qt.LeftButton,
            pos=QPoint(row_rect.right() - 2, row_rect.center().y()),
        )
        self.app.processEvents()

        triggered.assert_called_once_with(False)
        menu.close()

    def test_main_visible_selection_chart_action_uses_canonical_roles(self) -> None:
        rows = [
            self._add_row(
                kind=ROUTINE_ROW_STOCK,
                code=code,
                name=name,
                instance_id="instance-a",
            )
            for code, name in (
                ("005930", "삼성전자"),
                ("012330", "현대모비스"),
                ("086520", "에코프로"),
            )
        ]
        selection_model = self.window.routine_table.selectionModel()
        for row in rows:
            selection_model.select(
                self.window.routine_table.model().index(row, 0),
                QItemSelectionModel.Select | QItemSelectionModel.Rows,
            )
        self.app.processEvents()
        position = self.window.routine_table.visualItemRect(
            self.window.routine_table.item(rows[0], 0)
        ).center()
        _FakeMenu.chosen_text = "간이차트"

        with (
            patch.object(common_menu, "QMenu", _FakeMenu),
            patch.object(
                monitoring_menu,
                "open_selected_stock_instance_charts",
                return_value=[object(), object(), object()],
            ) as batch_open,
        ):
            opened = monitoring_menu.show_main_monitoring_stock_context_menu(
                self.window,
                position,
            )

        self.assertTrue(opened)
        parent, selected = batch_open.call_args.args
        self.assertIs(self.window, parent)
        self.assertEqual(
            ["005930", "012330", "086520"],
            [code for _stock_dir, code, _name in selected],
        )
        self.assertEqual([], selection_model.selectedRows())
        self.assertFalse(self.window.routine_table.currentIndex().isValid())
        chart_action = _FakeMenu.root.actions[-1]
        self.assertEqual("간이차트", chart_action.text)
        self.assertFalse(chart_action.separator)
        self.assertTrue(_FakeMenu.root.actions[-2].separator)
        self.assertIsNone(chart_action.property("stockChartActionColor"))

    def test_main_batch_chart_failure_preserves_selection(self) -> None:
        rows = [
            self._add_row(
                kind=ROUTINE_ROW_STOCK,
                code=code,
                name=name,
                instance_id="instance-a",
            )
            for code, name in (
                ("005930", "삼성전자"),
                ("012330", "현대모비스"),
            )
        ]
        self._select_rows(*rows)
        self.window.routine_table.selectionModel().setCurrentIndex(
            self.window.routine_table.model().index(rows[0], 0),
            QItemSelectionModel.NoUpdate,
        )
        selected = [
            (
                Path(str(self.window.routine_table.item(row, 0).data(ROUTINE_STOCK_PATH_ROLE))),
                str(self.window.routine_table.item(row, 0).data(ROUTINE_STOCK_CODE_ROLE)),
                str(self.window.routine_table.item(row, 0).data(ROUTINE_STOCK_NAME_ROLE)),
            )
            for row in rows
        ]

        with patch.object(
            monitoring_menu,
            "open_selected_stock_instance_charts",
            return_value=[],
        ):
            opened = monitoring_menu.open_selected_main_monitoring_stock_instance_charts(
                self.window,
                selected,
            )

        self.assertEqual([], opened)
        self.assertEqual(
            rows,
            [
                index.row()
                for index in self.window.routine_table.selectionModel().selectedRows()
            ],
        )
        self.assertTrue(self.window.routine_table.currentIndex().isValid())

    def test_batch_chart_open_deduplicates_and_isolates_invalid_target(self) -> None:
        selected = [
            (Path("first"), "005930", "삼성전자"),
            (Path("duplicate"), "005930", "삼성전자 중복"),
            (Path("invalid"), "", "코드없음"),
            (Path("bad"), "BAD", "잘못된코드"),
            (Path("failure"), "012330", "현대모비스"),
            (Path("last"), "086520", "에코프로"),
        ]
        opened_window = object()

        def open_chart(code, trade_date=None, parent=None):
            if code == "012330":
                raise RuntimeError("damaged target")
            return opened_window

        with patch(
            "gui_stock_instance_chart_window.open_stock_instance_chart",
            side_effect=open_chart,
        ) as opener:
            opened = common_menu.open_selected_stock_instance_charts(
                self.window,
                selected,
            )

        self.assertEqual([opened_window, opened_window], opened)
        self.assertEqual(
            ["005930", "012330", "086520"],
            [call.args[0] for call in opener.call_args_list],
        )
        self.assertTrue(
            all(call.kwargs["parent"] is self.window for call in opener.call_args_list)
        )

    def test_batch_chart_open_accepts_thirty_distinct_stocks(self) -> None:
        selected = [
            (Path(str(index)), f"{index + 1:06d}", f"종목{index + 1}")
            for index in range(30)
        ]
        with patch(
            "gui_stock_instance_chart_window.open_stock_instance_chart",
            side_effect=lambda code, **_kwargs: code,
        ) as opener:
            opened = common_menu.open_selected_stock_instance_charts(
                self.window,
                selected,
            )

        self.assertEqual(30, len(opened))
        self.assertEqual(30, opener.call_count)

    def test_monitor_excluded_selection_disables_only_early_close_menu(self) -> None:
        callbacks = common_menu.StockContextMenuCallbacks(
            select_all=Mock(),
            clear_selection=Mock(),
            early_close=Mock(),
            early_close_profit_loss=Mock(),
            early_close_cancel=Mock(),
            individual_liquidation=Mock(),
            start=Mock(),
            emergency_stop=Mock(),
            clear_operation_exclusion=Mock(),
        )
        with patch.object(common_menu, "QMenu", _FakeMenu):
            common_menu.show_monitor_stock_context_menu(
                self.window.routine_table,
                QPoint(),
                has_selection=True,
                callbacks=callbacks,
                operation_excluded=True,
            )

        menus = {menu.title: menu for menu in _FakeMenu.root.submenus}
        self.assertFalse(menus["조기마감"].enabled)
        self.assertTrue(menus["개별청산"].enabled)
        actions = {
            action.text: action
            for action in _FakeMenu.root.actions
            if not action.separator
        }
        self.assertTrue(actions["운영시작"].enabled)
        self.assertTrue(actions["검토정지"].enabled)
        self.assertTrue(actions["제외해제"].enabled)

    def test_settings_excluded_selection_uses_config_and_disables_early_close(self) -> None:
        stock_dir = self.root / "005930_삼성전자"
        stock_dir.mkdir()
        (stock_dir / "config.json").write_text(
            json.dumps({"operation_excluded": True}, ensure_ascii=False),
            encoding="utf-8",
        )
        (stock_dir / "state.json").write_text("{}", encoding="utf-8")
        window = Mock()
        window._stock_status_filter = "excluded"
        window.stock_table.itemAt.return_value = None
        window.stock_table.viewport.return_value.mapToGlobal.return_value = QPoint()
        window.selected_stock_infos.return_value = [
            (stock_dir, "005930", "삼성전자")
        ]
        window.selected_operation_mode_set.return_value = set()

        with patch.object(common_menu, "QMenu", _FakeMenu):
            common_menu.show_auto_trade_stock_context_menu(window, QPoint())

        menus = {menu.title: menu for menu in _FakeMenu.root.submenus}
        self.assertFalse(menus["조기마감"].enabled)
        self.assertTrue(menus["개별청산"].enabled)

    def test_normal_selection_keeps_early_close_enabled(self) -> None:
        callbacks = common_menu.StockContextMenuCallbacks(
            select_all=Mock(),
            clear_selection=Mock(),
            early_close=Mock(),
            early_close_profit_loss=Mock(),
            early_close_cancel=Mock(),
            individual_liquidation=Mock(),
        )
        with patch.object(common_menu, "QMenu", _FakeMenu):
            common_menu.show_monitor_stock_context_menu(
                self.window.routine_table,
                QPoint(),
                has_selection=True,
                callbacks=callbacks,
                operation_excluded=False,
            )
        menus = {menu.title: menu for menu in _FakeMenu.root.submenus}
        self.assertTrue(menus["조기마감"].enabled)

    def test_settings_hides_selected_release_while_global_latch_is_active(self) -> None:
        stock_dir = self.root / "005930_삼성전자"
        stock_dir.mkdir()
        (stock_dir / "config.json").write_text("{}", encoding="utf-8")
        (stock_dir / "state.json").write_text(
            json.dumps(
                {"status": "EMERGENCY_STOPPED", "emergency_scope": "SELECTED"},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        window = Mock()
        window._stock_status_filter = "stopped"
        window.stock_table.itemAt.return_value = None
        window.stock_table.viewport.return_value.mapToGlobal.return_value = QPoint()
        window.selected_stock_infos.return_value = [(stock_dir, "005930", "삼성전자")]
        window.selected_operation_mode_set.return_value = set()

        with patch.object(common_menu, "QMenu", _FakeMenu):
            common_menu.show_auto_trade_stock_context_menu(window, QPoint())

        action_texts = {
            action.text for action in _FakeMenu.root.actions if not action.separator
        }
        self.assertNotIn("정지해제", action_texts)

    def test_settings_selected_review_exposes_release_when_global_latch_is_off(
        self,
    ) -> None:
        stock_dir = self.root / "005930_삼성전자"
        stock_dir.mkdir()
        (stock_dir / "config.json").write_text("{}", encoding="utf-8")
        (stock_dir / "state.json").write_text(
            json.dumps(
                {
                    "status": "EMERGENCY_STOPPED",
                    "emergency_scope": "SELECTED",
                    "review_required": True,
                    "review_status": "PENDING",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        window = Mock()
        window._stock_status_filter = "stopped"
        window.stock_table.itemAt.return_value = None
        window.stock_table.viewport.return_value.mapToGlobal.return_value = QPoint()
        window.selected_stock_infos.return_value = [(stock_dir, "005930", "삼성전자")]
        window.selected_operation_mode_set.return_value = set()
        _FakeMenu.chosen_text = "검토정지 해제"

        with patch.object(common_menu, "QMenu", _FakeMenu):
            common_menu.show_auto_trade_stock_context_menu(window, QPoint())

        action_texts = {
            action.text for action in _FakeMenu.root.actions if not action.separator
        }
        self.assertNotIn("검토정지 해제", action_texts)
        self.assertNotIn("정지해제", action_texts)
        window.release_selected_emergency_stopped_auto_trade_stocks.assert_not_called()

    def test_settings_global_emergency_never_exposes_selected_release(self) -> None:
        stock_dir = self.root / "005930_삼성전자"
        stock_dir.mkdir()
        (stock_dir / "config.json").write_text("{}", encoding="utf-8")
        (stock_dir / "state.json").write_text(
            json.dumps(
                {"status": "EMERGENCY_STOPPED", "emergency_scope": "GLOBAL"},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        window = Mock()
        window._stock_status_filter = "stopped"
        window.stock_table.itemAt.return_value = None
        window.stock_table.viewport.return_value.mapToGlobal.return_value = QPoint()
        window.selected_stock_infos.return_value = [(stock_dir, "005930", "삼성전자")]
        window.selected_operation_mode_set.return_value = set()

        with patch.object(common_menu, "QMenu", _FakeMenu):
            common_menu.show_auto_trade_stock_context_menu(window, QPoint())

        action_texts = {
            action.text for action in _FakeMenu.root.actions if not action.separator
        }
        self.assertNotIn("정지해제", action_texts)

    def test_main_hides_selected_release_while_global_latch_is_active(self) -> None:
        row = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005930",
            name="삼성전자",
            instance_id="instance-a",
        )
        stock_dir = self._write_stock_config(row, mode="SCHEDULED")
        (stock_dir / "state.json").write_text(
            json.dumps(
                {"status": "EMERGENCY_STOPPED", "emergency_scope": "SELECTED"},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.window.routine_table.selectRow(row)
        position = self.window.routine_table.visualItemRect(
            self.window.routine_table.item(row, 0)
        ).center()

        with patch.object(common_menu, "QMenu", _FakeMenu):
            opened = monitoring_menu.show_main_monitoring_stock_context_menu(
                self.window, position
            )

        self.assertTrue(opened)
        action_texts = {
            action.text for action in _FakeMenu.root.actions if not action.separator
        }
        self.assertNotIn("정지해제", action_texts)

    def test_main_selected_review_exposes_release_when_global_latch_is_off(self) -> None:
        row = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005930",
            name="삼성전자",
            instance_id="instance-a",
        )
        stock_dir = self._write_stock_config(row, mode="SCHEDULED")
        (stock_dir / "state.json").write_text(
            json.dumps(
                {
                    "status": "EMERGENCY_STOPPED",
                    "emergency_scope": "SELECTED",
                    "review_required": True,
                    "review_status": "PENDING",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.window.routine_table.selectRow(row)
        position = self.window.routine_table.visualItemRect(
            self.window.routine_table.item(row, 0)
        ).center()
        _FakeMenu.chosen_text = "검토정지 해제"

        with patch.object(common_menu, "QMenu", _FakeMenu):
            opened = monitoring_menu.show_main_monitoring_stock_context_menu(
                self.window, position
            )

        self.assertTrue(opened)
        action_texts = {
            action.text for action in _FakeMenu.root.actions if not action.separator
        }
        self.assertNotIn("검토정지 해제", action_texts)
        self.assertNotIn("정지해제", action_texts)

    def test_main_review_only_state_does_not_expose_emergency_release(self) -> None:
        row = self._add_row(
            kind=ROUTINE_ROW_STOCK,
            code="005930",
            name="삼성전자",
            instance_id="instance-a",
        )
        stock_dir = self._write_stock_config(row, mode="SCHEDULED")
        (stock_dir / "state.json").write_text(
            json.dumps(
                {
                    "status": "REVIEW_REQUIRED",
                    "review_required": True,
                    "review_status": "PENDING",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.window.routine_table.selectRow(row)
        position = self.window.routine_table.visualItemRect(
            self.window.routine_table.item(row, 0)
        ).center()

        with patch.object(common_menu, "QMenu", _FakeMenu):
            opened = monitoring_menu.show_main_monitoring_stock_context_menu(
                self.window, position
            )

        self.assertTrue(opened)
        action_texts = {
            action.text for action in _FakeMenu.root.actions if not action.separator
        }
        self.assertNotIn("정지해제", action_texts)

    def test_carry_disables_shared_individual_time_menu(self) -> None:
        callbacks = common_menu.StockContextMenuCallbacks(
            select_all=Mock(),
            clear_selection=Mock(),
            early_close=Mock(),
            early_close_profit_loss=Mock(),
            early_close_cancel=Mock(),
            individual_liquidation=Mock(),
        )
        _FakeMenu.chosen_menu_title = None
        _FakeMenu.chosen_text = None
        with (
            patch.object(common_menu, "QMenu", _FakeMenu),
            patch.object(
                common_menu,
                "_context_menu_operation_policy",
                return_value={
                    "liquidation": {
                        "method": "이월",
                        "minutes_before_regular_close": "10",
                    }
                },
            ),
        ):
            common_menu.show_monitor_stock_context_menu(
                self.window.routine_table,
                QPoint(),
                has_selection=True,
                callbacks=callbacks,
            )

        individual_menu = _FakeMenu.root.submenus[1]
        self.assertFalse(individual_menu.submenus[0].enabled)

    def test_settings_and_monitor_profiles_share_early_close_definition(
        self,
    ) -> None:
        callbacks = common_menu.StockContextMenuCallbacks(
            select_all=Mock(),
            clear_selection=Mock(),
            early_close=Mock(),
            early_close_profit_loss=Mock(),
            early_close_cancel=Mock(),
            individual_liquidation=Mock(),
        )
        policy = {"early_close": {"method": "시장가"}}
        _FakeMenu.chosen_menu_title = None
        _FakeMenu.chosen_text = None
        with (
            patch.object(common_menu, "QMenu", _FakeMenu),
            patch.object(
                common_menu,
                "_context_menu_operation_policy",
                return_value=policy,
            ),
        ):
            common_menu.show_monitor_stock_context_menu(
                self.window.routine_table,
                QPoint(),
                has_selection=True,
                callbacks=callbacks,
            )
            monitor_actions = [
                (
                    action.text,
                    bool(action.property("earlyCloseCurrent")),
                    action.separator,
                )
                for action in _FakeMenu.root.submenus[0].actions
            ]

            settings_window = Mock()
            settings_window.stock_table.itemAt.return_value = None
            settings_window.stock_table.viewport.return_value.mapToGlobal.return_value = (
                QPoint()
            )
            settings_window.selected_stock_infos.return_value = [
                (self.root / "005930_삼성전자", "005930", "삼성전자")
            ]
            settings_window.selected_operation_mode_set.return_value = set()
            common_menu.show_auto_trade_stock_context_menu(
                settings_window,
                QPoint(),
            )
            settings_actions = [
                (
                    action.text,
                    bool(action.property("earlyCloseCurrent")),
                    action.separator,
                )
                for action in _FakeMenu.root.submenus[0].actions
            ]

        self.assertEqual(settings_actions, monitor_actions)


if __name__ == "__main__":
    unittest.main()
