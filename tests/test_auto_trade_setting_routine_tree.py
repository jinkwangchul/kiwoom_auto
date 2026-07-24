import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MethodType
from unittest.mock import patch

from PyQt5.QtCore import QObject
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QStyleOptionGroupBox,
    QTableWidget,
)

from routine_instance_registry import RoutineDefinitionRecord, RoutineInstanceRecord

import gui_auto_trade_setting_window as setting_window
import gui_auto_trade_table_loader as table_loader
from gui_auto_trade_setting_window import AutoTradeSettingWindow


class AutoTradeSettingRoutineTreeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _definition(self) -> RoutineDefinitionRecord:
        return RoutineDefinitionRecord(
            definition_id="indicator_follow",
            display_name="지표추종매매",
            package_dir=Path("routines") / "indicator_follow",
            schema_version="1.0",
            version="1.0",
            routine_type="auto_trade",
            entry_file="routine.py",
            module_name="indicator_follow_routine",
            settings_ui="indicator_follow",
            default_rules_file="rules.json",
            package_enabled=True,
            source_name="routine.json",
        )

    def _instance(self, instance_id: str, name: str) -> RoutineInstanceRecord:
        return RoutineInstanceRecord(
            instance_id=instance_id,
            definition_id="indicator_follow",
            display_name=name,
            source_routine_name="지표추종매매",
            persisted=True,
            source="PERSISTED",
            enabled=False,
            real_trade_allowed=False,
            rules_path=Path("routine_instances") / instance_id / "rules.json",
            schema_version="1.0",
        )

    def _window_harness(self):
        class Harness(QObject):
            pass

        harness = Harness()
        harness.routine_table = QTableWidget(0, 1)
        harness.stock_table = QTableWidget(0, 1)
        harness.routine_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        harness.routine_table.setSelectionMode(QAbstractItemView.SingleSelection)
        harness._collapsed_auto_trade_definition_ids = set()
        harness._default_operation_instance_by_definition = {}
        harness._routine_operation_status_by_instance = {}
        harness._stock_status_filter = "all"
        harness._collapsed_auto_trade_instance_ids = set()
        harness._routine_tree_display_level = "category"
        harness._routine_tree_display_scope = ""
        harness._routine_tree_last_stock_scope = "all"
        harness._routine_tree_display_criterion = "profit"
        for name in (
            "_setup_routine_table",
            "_routine_instance_stock_counts",
            "_current_stock_entries_by_instance",
            "_current_stocks_by_instance",
            "_routine_instance_operation_counts",
            "_is_default_operation_instance",
            "_routine_status_text_for_metadata",
            "set_default_operation_instance_from_metadata",
            "_refresh_default_operation_stamps",
            "_routine_tree_stock_performance_source",
            "_routine_tree_performance_texts",
            "_routine_tree_metric_text_parts",
            "_routine_tree_metric_values",
            "_routine_tree_row_widget",
            "_set_routine_tree_parent_summary_visible",
            "_setup_routine_tree_display_level_badges",
            "_position_routine_tree_display_level_badges",
            "_update_routine_tree_display_level_badges",
            "_set_routine_tree_display_scope",
            "_refresh_routine_tree_display_state",
            "_set_routine_tree_display_criterion",
            "_apply_routine_tree_display_level_command",
            "_set_routine_tree_display_level",
            "_toggle_routine_definition_collapsed",
            "_toggle_routine_instance_collapsed",
            "_routine_tree_toggle_enabled",
            "_apply_routine_tree_collapse_visibility",
            "eventFilter",
            "load_routine_table",
            "current_selected_routine_row_metadata",
            "current_selected_definition_id",
            "current_selected_instance_id",
            "current_selected_instance_dir",
            "current_selected_target_instance_ids",
            "current_selected_routine_name",
            "current_selected_routine_dir",
            "restore_routine_selection",
            "restore_routine_selection_metadata",
            "on_routine_table_item_clicked",
            "on_routine_table_item_double_clicked",
            "on_routine_selection_changed",
            "auto_trade_runtime_state_for_order",
            "update_selection_summary_panel",
            "_setup_selected_routine_status_bar",
            "set_stock_status_filter",
            "update_selected_routine_status_bar",
            "load_selected_routine_stocks",
        ):
            setattr(harness, name, MethodType(getattr(AutoTradeSettingWindow, name), harness))
        return harness

    def test_top_table_uses_definition_and_instance_rows_without_stock_scope_rows(self) -> None:
        instances = [self._instance("inst-a", "A 인스턴스"), self._instance("inst-b", "B 인스턴스")]
        counts = {
            "inst-a": {"registered": 1, "running": 1, "stopped": 0, "error": 0},
            "inst-b": {"registered": 2, "running": 0, "stopped": 2, "error": 1},
        }
        window = self._window_harness()
        window._routine_instance_operation_counts = lambda: counts
        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=[]):
            window.load_routine_table()
            window._set_routine_tree_display_level("routine")

        self.assertEqual(3, window.routine_table.rowCount())
        self.assertEqual(1, window.routine_table.columnCount())
        parent_meta = window.routine_table.item(0, 0).data(setting_window.Qt.UserRole)
        child_a_meta = window.routine_table.item(1, 0).data(setting_window.Qt.UserRole)
        child_b_meta = window.routine_table.item(2, 0).data(setting_window.Qt.UserRole)
        self.assertEqual("definition", parent_meta["row_kind"])
        self.assertEqual("instance", child_a_meta["row_kind"])
        self.assertEqual("instance", child_b_meta["row_kind"])
        self.assertNotIn(
            "stock_scope",
            [
                window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)["row_kind"]
                for row in range(window.routine_table.rowCount())
            ],
        )
        self.assertEqual("", window.routine_table.item(0, 0).text())
        self.assertEqual("", window.routine_table.item(1, 0).text())
        self.assertEqual("", window.routine_table.item(0, 0).data(setting_window.Qt.DisplayRole))
        self.assertEqual("", window.routine_table.item(0, 0).data(setting_window.Qt.ToolTipRole))
        self.assertEqual("", window.routine_table.item(1, 0).data(setting_window.Qt.ToolTipRole))
        self.assertNotIn("005930", window.routine_table.item(0, 0).text())
        self.assertIsNotNone(window.routine_table.cellWidget(0, 0))
        self.assertIsNotNone(window.routine_table.cellWidget(1, 0))
        self.assertGreaterEqual(window.routine_table.rowHeight(0), 30)
        parent_widget = window.routine_table.cellWidget(0, 0)
        parent_title = parent_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeTitle")
        parent_icon = parent_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeIcon")
        parent_stamp = parent_widget.findChild(setting_window.QPushButton, "autoTradeSettingDefaultOperationStamp")
        parent_instance_count = parent_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeInstanceCount")
        parent_meta_group = parent_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreeMetaGroup")
        parent_status_group = parent_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreeStatusGroup")
        parent_period = parent_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformancePeriod")
        parent_period_spacer = parent_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformancePeriodSpacer")
        parent_profit_column_spacer = parent_widget.findChild(
            setting_window.QWidget,
            "autoTradeSettingRoutineTreeParentProfitColumnSpacer",
        )
        parent_profit = parent_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformanceProfit")
        parent_average = parent_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformanceAverage")
        parent_efficiency = parent_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformanceEfficiency")
        parent_widget.resize(900, parent_widget.sizeHint().height())
        parent_widget.show()
        self._app.processEvents()
        self.assertIsNotNone(parent_title)
        self.assertIsNotNone(parent_icon)
        self.assertIsNone(parent_stamp)
        self.assertIsNotNone(parent_instance_count)
        self.assertIsNotNone(parent_meta_group)
        self.assertIsNone(parent_status_group)
        self.assertIsNotNone(parent_profit)
        self.assertIsNone(parent_period)
        self.assertIsNone(parent_period_spacer)
        self.assertIsNotNone(parent_profit_column_spacer)
        self.assertIsNotNone(parent_average)
        self.assertIsNotNone(parent_efficiency)
        self.assertEqual(28, parent_icon.width())
        self.assertEqual(
            setting_window.routine_tree_title_width(parent_title.fontMetrics()),
            parent_title.width(),
        )
        self.assertEqual(setting_window.Qt.AlignCenter | setting_window.Qt.AlignVCenter, parent_title.alignment())
        self.assertEqual(
            parent_title.width()
            + 4
            + parent_instance_count.width(),
            parent_meta_group.width(),
        )
        self.assertEqual("루틴2", parent_instance_count.text())
        self.assertEqual(64, parent_instance_count.width())
        self.assertEqual(setting_window.Qt.AlignCenter, parent_instance_count.alignment())
        self.assertIn("background-color: transparent", parent_instance_count.styleSheet())
        self.assertIn("#A855F7", parent_instance_count.styleSheet())
        self.assertIn("padding: 0 6px", parent_instance_count.styleSheet())
        self.assertGreater(parent_icon.font().pointSize(), parent_title.font().pointSize())
        self.assertTrue(parent_title.font().bold())
        self.assertTrue(parent_profit.isHidden())
        self.assertTrue(parent_average.isHidden())
        self.assertTrue(parent_efficiency.isHidden())
        self.assertEqual("+0", parent_profit.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreePerformanceProfitLeftValue").text())
        self.assertEqual("0.0%", parent_profit.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreePerformanceProfitRightValue").text())
        self.assertEqual("+0", parent_average.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreePerformanceAverageLeftValue").text())
        self.assertEqual("0.0%", parent_average.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreePerformanceAverageRightValue").text())
        self.assertEqual("0.0", parent_efficiency.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreePerformanceEfficiencyLeftValue").text())
        self.assertEqual("", parent_widget.toolTip())
        self.assertFalse(parent_instance_count.isHidden())
        window._set_routine_tree_parent_summary_visible(parent_widget, True)
        parent_widget.layout().activate()
        self._app.processEvents()
        self.assertFalse(parent_profit.isHidden())
        self.assertFalse(parent_average.isHidden())
        self.assertFalse(parent_efficiency.isHidden())
        parent_layout_widgets = [
            parent_widget.layout().itemAt(index).widget()
            for index in range(parent_widget.layout().count())
            if parent_widget.layout().itemAt(index).widget() is not None
        ]
        self.assertEqual(
            parent_profit,
            parent_layout_widgets[parent_layout_widgets.index(parent_profit_column_spacer) + 1],
        )
        self.assertGreaterEqual(window.routine_table.item(0, 0).sizeHint().height(), parent_widget.sizeHint().height())
        child_widget = window.routine_table.cellWidget(1, 0)
        child_title = child_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeTitle")
        child_indent = child_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreeIndent")
        stamp = child_widget.findChild(setting_window.QPushButton, "autoTradeSettingDefaultOperationStamp")
        child_default_slot = child_widget.findChild(setting_window.QWidget, "autoTradeSettingDefaultOperationSlot")
        child_instance_count = child_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeInstanceCount")
        child_period = child_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformancePeriod")
        child_period_spacer = child_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformancePeriodSpacer")
        child_profit = child_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformanceProfit")
        child_average = child_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformanceAverage")
        child_efficiency = child_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformanceEfficiency")
        self.assertIsNotNone(child_title)
        self.assertIsNotNone(child_indent)
        self.assertIsNone(stamp)
        self.assertIsNone(child_default_slot)
        self.assertIsNone(child_instance_count)
        self.assertIsNotNone(child_profit)
        self.assertIsNotNone(child_period)
        self.assertIsNone(child_period_spacer)
        self.assertIsNotNone(child_average)
        self.assertIsNotNone(child_efficiency)
        self.assertEqual(28, child_indent.width())
        self.assertEqual(setting_window.routine_tree_title_width(child_title.fontMetrics()), child_title.width())
        self.assertEqual(child_title.width(), child_title.minimumWidth())
        self.assertEqual(child_title.width(), child_title.maximumWidth())
        self.assertEqual(setting_window.QSizePolicy.Fixed, child_title.sizePolicy().horizontalPolicy())
        self.assertEqual(setting_window.Qt.AlignCenter | setting_window.Qt.AlignVCenter, child_title.alignment())
        self.assertIsNone(child_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeRegistered"))
        self.assertIsNone(child_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeRunning"))
        self.assertIsNone(child_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeStopped"))
        self.assertIsNone(child_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeError"))
        self.assertFalse(child_title.font().bold())
        self.assertGreaterEqual(
            child_title.mapTo(child_widget, child_title.rect().topLeft()).x()
            - parent_title.mapTo(parent_widget, parent_title.rect().topLeft()).x(),
            20,
        )
        self.assertGreater(parent_title.font().pointSize(), child_title.font().pointSize())
        self.assertEqual(setting_window.QFont.DemiBold, parent_title.font().weight())
        self.assertLess(parent_title.font().weight(), setting_window.QFont.Bold)
        self.assertEqual("0", child_period.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreePerformancePeriodLeftValue").text())
        self.assertEqual("+0", child_profit.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreePerformanceProfitLeftValue").text())
        self.assertEqual("0.0%", child_profit.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreePerformanceProfitRightValue").text())
        self.assertEqual("+0", child_average.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreePerformanceAverageLeftValue").text())
        self.assertEqual("0.0%", child_average.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreePerformanceAverageRightValue").text())
        self.assertEqual("0.0", child_efficiency.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreePerformanceEfficiencyLeftValue").text())
        self.assertFalse(child_period.isHidden())
        self.assertFalse(child_profit.isHidden())
        self.assertFalse(child_average.isHidden())
        self.assertFalse(child_efficiency.isHidden())
        self.assertEqual("", child_widget.toolTip())
        self.assertEqual("A 인스턴스", child_title.text())
        self.assertNotIn("기본운영", child_title.text())
        self.assertFalse(child_title.text().startswith(" "))
        self.assertEqual(2, window.routine_table.rowHeight(0) - window.routine_table.rowHeight(1))
        self.assertLessEqual(window.routine_table.rowHeight(0), 40)

    def test_tree_display_level_badges_reserve_control_area_without_changing_table_width(self) -> None:
        window = self._window_harness()
        window.eventFilter = MethodType(lambda _self, _obj, _event: False, window)
        window.routine_box = setting_window.QGroupBox("자동매매 루틴")
        window.routine_box.setAlignment(setting_window.Qt.AlignLeft)
        window.routine_box.setFlat(False)
        window.routine_box.setStyleSheet(
            setting_window.AUTO_TRADE_SETTING_WORKSPACE_GROUP_BOX_STYLE
        )
        layout = setting_window.QVBoxLayout(window.routine_box)
        layout.addWidget(window.routine_table)
        window.routine_box.resize(1000, 320)
        window.routine_box.show()
        self._app.processEvents()
        table_geometry_before = window.routine_table.geometry()
        group_geometry_before = window.routine_box.geometry()

        window._setup_routine_tree_display_level_badges()
        self._app.processEvents()

        badges = window._routine_tree_display_level_buttons
        self.assertEqual({"category", "routine", "stock"}, set(badges))
        self.assertEqual(["그룹", "루틴", "종목"], [badges[level].text() for level in ("category", "routine", "stock")])
        for badge in badges.values():
            self.assertEqual((64, 22), (badge.width(), badge.height()))
            self.assertIn("border-radius: 4px", badge.styleSheet())
            self.assertIn("padding: 0 6px", badge.styleSheet())
        self.assertIn("color: #16A34A", badges["category"].styleSheet())
        self.assertIn("color: #111827", badges["routine"].styleSheet())
        scopes = window._routine_tree_display_scope_buttons
        self.assertEqual(
            ["전체", "현재"],
            [scopes[key].text() for key in ("all", "current")],
        )
        self.assertTrue(all(not button.isEnabled() for button in scopes.values()))
        self.assertIn("color: #9CA3AF", scopes["all"].styleSheet())
        self.assertIn("color: #9CA3AF", scopes["current"].styleSheet())
        scopes["current"].click()
        self.assertEqual("", window._routine_tree_display_scope)
        criteria = window._routine_tree_display_criterion_buttons
        self.assertEqual(
            ["기간", "수익", "평균", "효율"],
            [criteria[key].text() for key in ("period", "profit", "average", "efficiency")],
        )
        self.assertFalse(criteria["period"].isEnabled())
        self.assertTrue(criteria["profit"].isEnabled())
        self.assertTrue(criteria["average"].isEnabled())
        self.assertTrue(criteria["efficiency"].isEnabled())
        self.assertIn("color: #16A34A", criteria["profit"].styleSheet())
        self.assertIn("color: #9CA3AF", criteria["period"].styleSheet())
        criteria["period"].click()
        self.assertEqual("profit", window._routine_tree_display_criterion)
        badges["routine"].click()
        self.assertTrue(all(not button.isEnabled() for button in scopes.values()))
        self.assertTrue(criteria["period"].isEnabled())
        self.assertTrue(criteria["average"].isEnabled())
        self.assertTrue(criteria["efficiency"].isEnabled())
        criteria["period"].click()
        self.assertEqual("period", window._routine_tree_display_criterion)
        badges["category"].click()
        self.assertEqual("profit", window._routine_tree_display_criterion)
        badges["stock"].click()
        self.assertTrue(all(button.isEnabled() for button in scopes.values()))
        self.assertIn("color: #16A34A", scopes["all"].styleSheet())
        self.assertTrue(all(button.isEnabled() for button in criteria.values()))
        self.assertEqual("all", window._routine_tree_display_scope)

        badge_group = window._routine_tree_display_level_badges
        expected_x = window.routine_box.width() - layout.contentsMargins().right() - badge_group.width()
        self.assertEqual(expected_x, badge_group.x())
        self.assertEqual(window.routine_box.contentsRect().top(), badge_group.y())
        self.assertGreater(
            badge_group.y(),
            setting_window.AUTO_TRADE_SETTING_WORKSPACE_GROUP_BOX_FRAME_TOP,
        )
        self.assertFalse(badge_group.geometry().intersects(window.routine_table.geometry()))
        self.assertEqual(group_geometry_before, window.routine_box.geometry())
        self.assertEqual(table_geometry_before.x(), window.routine_table.geometry().x())
        self.assertEqual(table_geometry_before.width(), window.routine_table.geometry().width())
        self.assertEqual(table_geometry_before.bottom(), window.routine_table.geometry().bottom())

    def test_actual_window_badges_change_visible_hierarchy(self) -> None:
        instances = [self._instance("inst-a", "A 인스턴스")]
        stocks = [
            {
                "stock_path": "stocks/005930_A",
                "assigned_routine_instance_id": "inst-a",
                "code": "005930",
                "name": "삼성전자",
            },
        ]
        with patch.object(AutoTradeSettingWindow, "refresh_all", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "update_startup_recovery_controls", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "current_runtime_file_signature", lambda _self: tuple()):
            window = AutoTradeSettingWindow()
        self.addCleanup(window.close)
        window._routine_instance_operation_counts = lambda: {
            "inst-a": {"registered": 1, "running": 0, "stopped": 1, "error": 0},
        }

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=stocks), \
                patch.object(setting_window, "read_orders_data", return_value=[]):
            window.load_routine_table()
        window.show()
        self._app.processEvents()

        def _visible_counts() -> dict[str, int]:
            counts = {"definition": 0, "instance": 0, "stock": 0}
            for row in range(window.routine_table.rowCount()):
                metadata = window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)
                if not window.routine_table.isRowHidden(row):
                    counts[str(metadata["row_kind"])] += 1
            return counts

        level_buttons = window._routine_tree_display_level_buttons
        scope_buttons = window._routine_tree_display_scope_buttons
        criteria = window._routine_tree_display_criterion_buttons

        level_buttons["category"].click()
        self._app.processEvents()
        self.assertEqual({"definition": 1, "instance": 0, "stock": 0}, _visible_counts())
        self.assertTrue(all(not button.isEnabled() for button in scope_buttons.values()))
        self.assertFalse(criteria["period"].isEnabled())
        self.assertTrue(criteria["average"].isEnabled())
        self.assertTrue(criteria["efficiency"].isEnabled())

        level_buttons["routine"].click()
        self._app.processEvents()
        self.assertEqual({"definition": 1, "instance": 1, "stock": 0}, _visible_counts())
        self.assertTrue(all(not button.isEnabled() for button in scope_buttons.values()))
        self.assertTrue(criteria["period"].isEnabled())
        self.assertTrue(criteria["average"].isEnabled())
        self.assertTrue(criteria["efficiency"].isEnabled())

        window.routine_table.selectRow(1)
        level_buttons["stock"].click()
        self._app.processEvents()
        self.assertEqual({"definition": 1, "instance": 1, "stock": 1}, _visible_counts())
        self.assertTrue(all(button.isEnabled() for button in scope_buttons.values()))
        self.assertTrue(all(button.isEnabled() for button in criteria.values()))
        self.assertEqual(1, window.routine_table.currentRow())

        window._toggle_routine_instance_collapsed("inst-a")
        level_buttons["category"].click()
        level_buttons["stock"].click()
        self._app.processEvents()
        self.assertEqual({"definition": 1, "instance": 1, "stock": 1}, _visible_counts())
        self.assertEqual(set(), window._collapsed_auto_trade_instance_ids)

    def test_tree_display_level_changes_visible_hierarchy_and_preserves_collapse(self) -> None:
        instances = [self._instance("inst-a", "A 인스턴스"), self._instance("inst-empty", "빈 인스턴스")]
        stocks = [
            {
                "stock_path": "stocks/005930_A",
                "assigned_routine_instance_id": "inst-a",
                "code": "005930",
                "name": "삼성전자",
            },
        ]
        window = self._window_harness()
        window._routine_instance_operation_counts = lambda: {
            "inst-a": {"registered": 1, "running": 0, "stopped": 1, "error": 0},
            "inst-empty": {"registered": 0, "running": 0, "stopped": 0, "error": 0},
        }
        window._routine_tree_display_level_buttons = {}

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=stocks):
            window.load_routine_table()
            window._set_routine_tree_display_level("category")

            def _visible_counts() -> dict[str, int]:
                counts = {"definition": 0, "instance": 0, "stock": 0}
                for row in range(window.routine_table.rowCount()):
                    metadata = window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)
                    if not window.routine_table.isRowHidden(row):
                        counts[str(metadata["row_kind"])] += 1
                return counts

            self.assertEqual(
                {"definition": 1, "instance": 0, "stock": 0},
                _visible_counts(),
            )
            window._set_routine_tree_display_level("routine")
            self.assertEqual(
                {"definition": 1, "instance": 2, "stock": 0},
                _visible_counts(),
            )
            window._set_routine_tree_display_level("stock")
            self.assertEqual(
                {"definition": 1, "instance": 2, "stock": 1},
                _visible_counts(),
            )

            window._toggle_routine_instance_collapsed("inst-a")
            self.assertEqual(
                {"definition": 1, "instance": 2, "stock": 0},
                _visible_counts(),
            )
            window._set_routine_tree_display_level("category")
            window._set_routine_tree_display_level("stock")
            self.assertEqual(
                {"definition": 1, "instance": 2, "stock": 1},
                _visible_counts(),
            )

        self.assertEqual("stock", window._routine_tree_display_level)
        self.assertEqual(set(), window._collapsed_auto_trade_instance_ids)
        self.assertFalse(window.routine_table.isRowHidden(1))
        self.assertFalse(window.routine_table.isRowHidden(2))

    def test_level_badges_apply_once_and_arrows_remain_authoritative(self) -> None:
        instances = [self._instance("inst-a", "A 인스턴스")]
        stocks = [
            {
                "stock_path": "stocks/005930_A",
                "assigned_routine_instance_id": "inst-a",
                "code": "005930",
                "name": "삼성전자",
            },
        ]
        window = self._window_harness()
        window._routine_tree_display_level_buttons = {}
        window._routine_tree_display_scope_buttons = {}
        window._routine_tree_display_criterion_buttons = {}
        window._routine_instance_operation_counts = lambda: {
            "inst-a": {"registered": 1, "running": 0, "stopped": 1, "error": 0},
        }

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=stocks):
            window.load_routine_table()

        definition_icon = window.routine_table.cellWidget(0, 0).findChild(
            setting_window.QLabel,
            "autoTradeSettingRoutineTreeIcon",
        )
        instance_icon = window.routine_table.cellWidget(1, 0).findChild(
            setting_window.QLabel,
            "autoTradeSettingRoutineTreeIcon",
        )

        window._set_routine_tree_display_level("category")
        self.assertEqual({"indicator_follow"}, window._collapsed_auto_trade_definition_ids)
        self.assertEqual("▶", definition_icon.text())
        self.assertTrue(window.routine_table.isRowHidden(1))
        self.assertTrue(window.routine_table.isRowHidden(2))

        window._toggle_routine_definition_collapsed("indicator_follow")
        self.assertEqual(set(), window._collapsed_auto_trade_definition_ids)
        self.assertEqual("▼", definition_icon.text())
        self.assertFalse(window.routine_table.isRowHidden(1))
        window._refresh_routine_tree_display_state()
        window.routine_table.viewport().update()
        self.assertEqual("▼", definition_icon.text())
        self.assertFalse(window.routine_table.isRowHidden(1))

        window._set_routine_tree_display_level("routine")
        self.assertEqual(set(), window._collapsed_auto_trade_definition_ids)
        self.assertEqual({"inst-a"}, window._collapsed_auto_trade_instance_ids)
        self.assertEqual("▼", definition_icon.text())
        self.assertEqual("▶", instance_icon.text())
        self.assertFalse(window.routine_table.isRowHidden(1))
        self.assertTrue(window.routine_table.isRowHidden(2))

        window._toggle_routine_instance_collapsed("inst-a")
        self.assertEqual(set(), window._collapsed_auto_trade_instance_ids)
        self.assertEqual("▼", instance_icon.text())
        self.assertFalse(window.routine_table.isRowHidden(2))
        window._refresh_routine_tree_display_state()
        window.routine_table.viewport().update()
        self.assertEqual("▼", instance_icon.text())
        self.assertFalse(window.routine_table.isRowHidden(2))

        window._toggle_routine_instance_collapsed("inst-a")
        self.assertEqual({"inst-a"}, window._collapsed_auto_trade_instance_ids)
        self.assertEqual("▶", instance_icon.text())
        self.assertTrue(window.routine_table.isRowHidden(2))

        window._set_routine_tree_display_level("stock")
        self.assertEqual(set(), window._collapsed_auto_trade_definition_ids)
        self.assertEqual(set(), window._collapsed_auto_trade_instance_ids)
        self.assertEqual("▼", definition_icon.text())
        self.assertEqual("▼", instance_icon.text())
        self.assertFalse(window.routine_table.isRowHidden(2))

    def test_empty_definition_keeps_default_performance_visible(self) -> None:
        empty_definition = RoutineDefinitionRecord(
            definition_id="review",
            display_name="등록확인루틴",
            package_dir=Path("routines") / "review",
            schema_version="1.0",
            version="1.0",
            routine_type="auto_trade",
            entry_file="routine.py",
            module_name="review_routine",
            settings_ui="",
            default_rules_file="rules.json",
            package_enabled=True,
            source_name="routine.json",
        )
        with patch.object(AutoTradeSettingWindow, "refresh_all", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "update_startup_recovery_controls", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "current_runtime_file_signature", lambda _self: tuple()):
            window = AutoTradeSettingWindow()
        self.addCleanup(window.close)
        window._routine_instance_operation_counts = lambda: {}

        with patch.object(setting_window, "load_routine_definitions", return_value=[empty_definition]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=[]), \
                patch.object(setting_window, "read_base_stocks", return_value=[]):
            window.load_routine_table()
            window._set_routine_tree_display_level("category")
        window.show()
        self._app.processEvents()

        widget = window.routine_table.cellWidget(0, 0)
        icon = widget.findChild(
            setting_window.QLabel,
            "autoTradeSettingRoutineTreeIcon",
        )
        self.assertEqual("▶", icon.text())
        count_badge = widget.findChild(
            setting_window.QLabel,
            "autoTradeSettingRoutineTreeInstanceCount",
        )
        self.assertEqual("루틴0", count_badge.text())

        expected_labels = (
            ("autoTradeSettingRoutineTreePerformanceProfitLeftValue", "+0"),
            ("autoTradeSettingRoutineTreePerformanceProfitRightValue", "0.0%"),
            ("autoTradeSettingRoutineTreePerformanceAverageLeftValue", "+0"),
            ("autoTradeSettingRoutineTreePerformanceAverageRightValue", "0.0%"),
            ("autoTradeSettingRoutineTreePerformanceEfficiencyLeftValue", "0.0"),
        )

        def _assert_default_summary_visible() -> None:
            self.assertTrue(
                bool(widget.property("autoTradeSettingRoutineTreeSummaryPinned"))
            )
            for object_name, expected in expected_labels:
                label = widget.findChild(setting_window.QLabel, object_name)
                self.assertIsNotNone(label)
                self.assertFalse(label.isHidden())
                self.assertTrue(label.isVisible())
                self.assertGreater(label.width(), 0)
                self.assertEqual(expected, label.text())

        _assert_default_summary_visible()
        self._app.sendEvent(widget, setting_window.QEvent(setting_window.QEvent.Leave))
        self._app.processEvents()
        _assert_default_summary_visible()

        for level in ("routine", "stock", "category"):
            window._set_routine_tree_display_level(level)
            self._app.processEvents()
            _assert_default_summary_visible()

        window._refresh_routine_tree_display_state()
        window.routine_table.viewport().update()
        self._app.processEvents()
        _assert_default_summary_visible()

    def test_empty_definition_summary_follows_visible_tree_depth(self) -> None:
        empty_definition = RoutineDefinitionRecord(
            definition_id="review",
            display_name="등록확인루틴",
            package_dir=Path("routines") / "review",
            schema_version="1.0",
            version="1.0",
            routine_type="auto_trade",
            entry_file="routine.py",
            module_name="review_routine",
            settings_ui="",
            default_rules_file="rules.json",
            package_enabled=True,
            source_name="routine.json",
        )
        instances = [self._instance("inst-a", "A 인스턴스")]
        with patch.object(AutoTradeSettingWindow, "refresh_all", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "update_startup_recovery_controls", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "current_runtime_file_signature", lambda _self: tuple()):
            window = AutoTradeSettingWindow()
        self.addCleanup(window.close)
        window._routine_instance_operation_counts = lambda: {}

        with patch.object(
            setting_window,
            "load_routine_definitions",
            return_value=[self._definition(), empty_definition],
        ), patch.object(
            setting_window,
            "load_persisted_routine_instances",
            return_value=instances,
        ), patch.object(setting_window, "read_base_stocks", return_value=[]):
            window.load_routine_table()
        window.show()
        self._app.processEvents()

        def _definition_widget(definition_id: str):
            for row in range(window.routine_table.rowCount()):
                item = window.routine_table.item(row, 0)
                metadata = item.data(setting_window.Qt.UserRole)
                if (
                    metadata["row_kind"] == "definition"
                    and metadata["definition_id"] == definition_id
                ):
                    return window.routine_table.cellWidget(row, 0)
            self.fail(f"definition row not found: {definition_id}")

        def _summary_widgets(widget):
            return [
                child
                for child in widget.findChildren(setting_window.QWidget)
                if child.property("autoTradeSettingParentSummaryMetric")
            ]

        indicator_widget = _definition_widget("indicator_follow")
        review_widget = _definition_widget("review")
        review_title = review_widget.findChild(
            setting_window.QLabel,
            "autoTradeSettingRoutineTreeTitle",
        )

        window._set_routine_tree_display_level("category")
        self._app.processEvents()
        self.assertTrue(
            bool(review_widget.property("autoTradeSettingRoutineTreeSummaryPinned"))
        )
        self.assertTrue(all(not child.isHidden() for child in _summary_widgets(review_widget)))

        window._set_routine_tree_display_level("routine")
        self._app.processEvents()
        self.assertFalse(
            bool(review_widget.property("autoTradeSettingRoutineTreeSummaryPinned"))
        )
        self.assertTrue(all(child.isHidden() for child in _summary_widgets(review_widget)))
        self.assertFalse(
            bool(indicator_widget.property("autoTradeSettingRoutineTreeSummaryPinned"))
        )

        hover_position = review_title.mapTo(review_widget, review_title.rect().center())
        self._app.sendEvent(
            review_widget,
            QMouseEvent(
                setting_window.QEvent.MouseMove,
                hover_position,
                setting_window.Qt.NoButton,
                setting_window.Qt.NoButton,
                setting_window.Qt.NoModifier,
            ),
        )
        self._app.processEvents()
        self.assertTrue(all(not child.isHidden() for child in _summary_widgets(review_widget)))

        self._app.sendEvent(
            review_widget,
            setting_window.QEvent(setting_window.QEvent.Leave),
        )
        self._app.processEvents()
        self.assertTrue(all(child.isHidden() for child in _summary_widgets(review_widget)))

        window._toggle_routine_definition_collapsed("indicator_follow")
        self._app.processEvents()
        self.assertTrue(
            bool(review_widget.property("autoTradeSettingRoutineTreeSummaryPinned"))
        )
        self.assertTrue(all(not child.isHidden() for child in _summary_widgets(review_widget)))

        window._toggle_routine_definition_collapsed("indicator_follow")
        window._refresh_routine_tree_display_state()
        window.routine_table.viewport().update()
        self._app.processEvents()
        self.assertFalse(
            bool(review_widget.property("autoTradeSettingRoutineTreeSummaryPinned"))
        )
        self.assertTrue(all(child.isHidden() for child in _summary_widgets(review_widget)))

    def test_tree_display_scope_and_metric_are_independent_and_preserve_collapse(self) -> None:
        instances = [self._instance("inst-a", "A 인스턴스")]
        stocks = [
            {
                "stock_path": "stocks/005930_A",
                "assigned_routine_instance_id": "inst-a",
                "code": "005930",
                "name": "삼성전자",
            },
        ]
        window = self._window_harness()
        window._routine_tree_display_level_buttons = {}
        window._routine_tree_display_scope_buttons = {}
        window._routine_tree_display_criterion_buttons = {}
        window._routine_instance_operation_counts = lambda: {
            "inst-a": {"registered": 1, "running": 0, "stopped": 1, "error": 0},
        }

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=stocks):
            window.load_routine_table()
            window._set_routine_tree_display_criterion("period")
            self.assertEqual("profit", window._routine_tree_display_criterion)

            window._set_routine_tree_display_level("routine")
            window._set_routine_tree_display_criterion("period")
            self.assertEqual("period", window._routine_tree_display_criterion)
            instance_metadata = window.routine_table.item(1, 0).data(setting_window.Qt.UserRole)
            self.assertEqual("routine", instance_metadata["display_level"])
            self.assertEqual("", instance_metadata["display_scope"])
            self.assertEqual("period", instance_metadata["display_metric"])

            window._set_routine_tree_display_level("category")
            self.assertEqual("profit", window._routine_tree_display_criterion)

            window._set_routine_tree_display_level("stock")
            self.assertEqual("all", window._routine_tree_display_scope)
            window._toggle_routine_instance_collapsed("inst-a")
            collapsed_before = set(window._collapsed_auto_trade_instance_ids)
            window._set_routine_tree_display_scope("all")
            self.assertEqual("all", window._routine_tree_display_scope)
            self.assertEqual("profit", window._routine_tree_display_criterion)
            self.assertEqual(collapsed_before, window._collapsed_auto_trade_instance_ids)
            self.assertEqual(
                ["definition", "instance", "stock"],
                [
                    window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)["row_kind"]
                    for row in range(window.routine_table.rowCount())
                ],
            )
            stock_metadata = window.routine_table.item(2, 0).data(setting_window.Qt.UserRole)
            self.assertEqual("all", stock_metadata["display_scope"])
            self.assertEqual("stock", stock_metadata["display_level"])
            self.assertEqual("profit", stock_metadata["display_metric"])
            self.assertTrue(window.routine_table.isRowHidden(2))

            window._set_routine_tree_display_scope("current")
            self.assertEqual("current", window._routine_tree_display_scope)
            self.assertEqual(collapsed_before, window._collapsed_auto_trade_instance_ids)
            window._set_routine_tree_display_level("routine")
            self.assertEqual("", window._routine_tree_display_scope)
            window._set_routine_tree_display_level("stock")
            self.assertEqual("current", window._routine_tree_display_scope)

    def test_routine_period_uses_unique_filled_trade_days_and_excludes_zero_day_stocks(self) -> None:
        window = self._window_harness()
        orders_by_name = {
            "a": [
                {"side": "BUY", "filled_qty": 1, "filled_price": 100, "order_time": "2026-07-01 09:00:00"},
                {"side": "SELL", "filled_qty": 1, "filled_price": 110, "order_time": "2026-07-01 10:00:00"},
                {"side": "BUY", "filled_qty": 1, "filled_price": 120, "order_time": "2026-07-02 09:00:00"},
            ],
            "b": [
                {"side": "BUY", "filled_qty": 1, "filled_price": 200, "order_time": "2026-07-03 09:00:00"},
            ],
            "c": [],
        }
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stocks = []
            for name, orders in orders_by_name.items():
                stock_dir = root / name
                stock_dir.mkdir()
                (stock_dir / "orders.json").write_text(
                    json.dumps({"orders": orders}, ensure_ascii=False),
                    encoding="utf-8",
                )
                stocks.append({"stock_path": str(stock_dir), "is_current": name != "c"})
            texts = window._routine_tree_performance_texts(stocks)
            empty_texts = window._routine_tree_performance_texts([stocks[-1]])
            source = window._routine_tree_stock_performance_source(stocks[0])

        self.assertEqual("기간(1)", texts["performance_period_text"])
        self.assertEqual("수익(+10 / 0.0%)", texts["performance_profit_text"])
        self.assertEqual("평균(+0 / 0.0%)", texts["performance_average_text"])
        self.assertEqual("효율(0.0)", texts["performance_efficiency_text"])
        self.assertEqual("기간(0)", empty_texts["performance_period_text"])
        self.assertEqual("+10", texts["performance_profit_amount"])
        self.assertEqual("0.0%", texts["performance_profit_rate"])
        self.assertEqual("+0", texts["performance_average_amount"])
        self.assertEqual("0.0%", texts["performance_average_rate"])
        self.assertEqual("0.0", texts["performance_efficiency_value"])
        self.assertEqual(
            {
                "trade_days",
                "realized_profit",
                "profit_rate",
                "average",
                "efficiency",
                "is_current",
            },
            set(source),
        )
        self.assertEqual(2, source["trade_days"])
        self.assertEqual(10.0, source["realized_profit"])
        self.assertTrue(source["is_current"])

    def test_level_and_metric_badges_update_actual_row_values_without_expanding_tree(self) -> None:
        instances = [self._instance("inst-a", "A 인스턴스")]
        stocks = [
            {
                "stock_path": "stocks/005930_A",
                "assigned_routine_instance_id": "inst-a",
                "code": "005930",
                "name": "삼성전자",
            },
        ]
        orders = [
            {"side": "BUY", "filled_qty": 1, "filled_price": 100, "order_time": "2026-07-01 09:00:00"},
            {"side": "SELL", "filled_qty": 1, "filled_price": 110, "order_time": "2026-07-02 09:00:00"},
        ]
        window = self._window_harness()
        window._routine_tree_display_level_buttons = {}
        window._routine_tree_display_scope_buttons = {}
        window._routine_tree_display_criterion_buttons = {}
        window._routine_instance_operation_counts = lambda: {
            "inst-a": {"registered": 1, "running": 0, "stopped": 1, "error": 0},
        }

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=stocks), \
                patch.object(setting_window, "read_orders_data", return_value=orders):
            window.load_routine_table()
            window._toggle_routine_instance_collapsed("inst-a")
            collapsed_before = set(window._collapsed_auto_trade_instance_ids)

            window._set_routine_tree_display_level("routine")
            window._set_routine_tree_display_criterion("period")
            instance_widget = window.routine_table.cellWidget(1, 0)
            instance_period = instance_widget.findChild(
                setting_window.QLabel,
                "autoTradeSettingRoutineTreePerformancePeriodLeftValue",
            )
            instance_profit = instance_widget.findChild(
                setting_window.QLabel,
                "autoTradeSettingRoutineTreePerformanceProfitLeftValue",
            )
            self.assertEqual("2", instance_period.text())
            self.assertEqual("+10", instance_profit.text())
            instance_average = instance_widget.findChild(
                setting_window.QLabel,
                "autoTradeSettingRoutineTreePerformanceAverageLeftValue",
            )
            instance_efficiency = instance_widget.findChild(
                setting_window.QLabel,
                "autoTradeSettingRoutineTreePerformanceEfficiencyLeftValue",
            )
            self.assertEqual("+0", instance_average.text())
            self.assertEqual("0.0", instance_efficiency.text())
            self.assertEqual(collapsed_before, window._collapsed_auto_trade_instance_ids)
            self.assertTrue(window.routine_table.isRowHidden(2))

            window._set_routine_tree_display_level("stock")
            stock_widget = window.routine_table.cellWidget(2, 0)
            stock_period = stock_widget.findChild(
                setting_window.QLabel,
                "autoTradeSettingRoutineTreePerformancePeriodLeftValue",
            )
            self.assertEqual("2", stock_period.text())
            window._set_routine_tree_display_criterion("profit")
            stock_profit = stock_widget.findChild(
                setting_window.QLabel,
                "autoTradeSettingRoutineTreePerformanceProfitLeftValue",
            )
            self.assertEqual("+10", stock_profit.text())
            self.assertEqual("2", stock_period.text())
            self.assertEqual("+0", instance_average.text())
            self.assertEqual("0.0", instance_efficiency.text())
            self.assertEqual(set(), window._collapsed_auto_trade_instance_ids)
            self.assertFalse(window.routine_table.isRowHidden(2))

    def test_routine_tree_performance_formatter_contract(self) -> None:
        window = self._window_harness()
        cases = (
            (
                "positive",
                {
                    "trade_days": 125,
                    "realized_profit": 123456.0,
                    "profit_rate": 12.3,
                    "average": None,
                    "efficiency": 123.4,
                    "is_current": True,
                },
                {
                    "performance_period_value": "125",
                    "performance_profit_amount": "+123,456",
                    "performance_profit_rate": "+12.3%",
                    "performance_average_amount": "+0",
                    "performance_average_rate": "0.0%",
                    "performance_efficiency_value": "123.4",
                },
            ),
            (
                "negative",
                {
                    "trade_days": 1,
                    "realized_profit": -2500.0,
                    "profit_rate": -4.2,
                    "average": None,
                    "efficiency": None,
                    "is_current": True,
                },
                {
                    "performance_period_value": "1",
                    "performance_profit_amount": "-2,500",
                    "performance_profit_rate": "-4.2%",
                    "performance_average_amount": "+0",
                    "performance_average_rate": "0.0%",
                    "performance_efficiency_value": "0.0",
                },
            ),
            (
                "empty",
                {
                    "trade_days": None,
                    "realized_profit": None,
                    "profit_rate": None,
                    "average": None,
                    "efficiency": None,
                    "is_current": True,
                },
                {
                    "performance_period_value": "0",
                    "performance_profit_amount": "+0",
                    "performance_profit_rate": "0.0%",
                    "performance_average_amount": "+0",
                    "performance_average_rate": "0.0%",
                    "performance_efficiency_value": "0.0",
                },
            ),
        )

        for stock_path, source, expected in cases:
            texts = window._routine_tree_performance_texts(
                [{"stock_path": stock_path}],
                {stock_path: source},
            )
            for key, expected_value in expected.items():
                self.assertEqual(expected_value, texts[key])

    def test_performance_fixture_changes_group_routine_and_stock_tree_text(self) -> None:
        instances = [
            self._instance("inst-a", "A 인스턴스"),
            self._instance("inst-b", "B 인스턴스"),
        ]
        orders_by_code = {
            "000001": [
                {"side": "BUY", "filled_qty": 1, "filled_price": 100, "order_time": "2026-07-01 09:00:00"},
                {"side": "SELL", "filled_qty": 1, "filled_price": 120, "order_time": "2026-07-02 09:00:00"},
            ],
            "000002": [],
            "000003": [
                {"side": "BUY", "filled_qty": 1, "filled_price": 200, "order_time": "2026-07-03 09:00:00"},
                {"side": "SELL", "filled_qty": 1, "filled_price": 205, "order_time": "2026-07-03 10:00:00"},
            ],
        }
        assignments = {
            "000001": "inst-a",
            "000002": "inst-a",
            "000003": "inst-b",
        }
        window = self._window_harness()
        window._routine_tree_display_level_buttons = {}
        window._routine_tree_display_scope_buttons = {}
        window._routine_tree_display_criterion_buttons = {}
        window._routine_instance_operation_counts = lambda: {
            "inst-a": {"registered": 2, "running": 0, "stopped": 2, "error": 0},
            "inst-b": {"registered": 1, "running": 0, "stopped": 1, "error": 0},
        }

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stocks = []
            for code, orders in orders_by_code.items():
                stock_dir = root / code
                stock_dir.mkdir()
                (stock_dir / "orders.json").write_text(
                    json.dumps({"orders": orders}, ensure_ascii=False),
                    encoding="utf-8",
                )
                stocks.append(
                    {
                        "stock_path": str(stock_dir),
                        "assigned_routine_instance_id": assignments[code],
                        "code": code,
                        "name": f"종목{code[-1]}",
                    }
                )

            with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                    patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                    patch.object(setting_window, "read_base_stocks", return_value=stocks):
                window.load_routine_table()

                rows = {}
                for row in range(window.routine_table.rowCount()):
                    metadata = window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)
                    key = (
                        str(metadata.get("row_kind", "")),
                        str(metadata.get("instance_id", "")),
                        str(metadata.get("stock_code", "")),
                    )
                    rows[key] = row

                def _left_value(row: int, metric: str) -> str:
                    widget = window.routine_table.cellWidget(row, 0)
                    label = widget.findChild(
                        setting_window.QLabel,
                        f"autoTradeSettingRoutineTreePerformance{metric.title()}LeftValue",
                    )
                    return label.text()

                group_row = rows[("definition", "", "")]
                instance_a_row = rows[("instance", "inst-a", "")]
                instance_b_row = rows[("instance", "inst-b", "")]
                stock_a_row = rows[("stock", "inst-a", "000001")]
                stock_empty_row = rows[("stock", "inst-a", "000002")]
                stock_b_row = rows[("stock", "inst-b", "000003")]

                text_changes = 0
                before = _left_value(group_row, "profit")
                self.assertEqual("+25", before)

                window._set_routine_tree_display_level("routine")
                after = _left_value(instance_a_row, "profit")
                text_changes += int(after != before)
                self.assertEqual("+20", after)
                self.assertEqual("+5", _left_value(instance_b_row, "profit"))

                before = _left_value(instance_a_row, "profit")
                window._set_routine_tree_display_criterion("period")
                after = _left_value(instance_a_row, "period")
                text_changes += int(after != before)
                self.assertEqual("2", after)
                self.assertEqual("1", _left_value(instance_b_row, "period"))

                window._set_routine_tree_display_level("stock")
                self.assertEqual("2", _left_value(stock_a_row, "period"))
                self.assertEqual("0", _left_value(stock_empty_row, "period"))
                self.assertEqual("1", _left_value(stock_b_row, "period"))

                before = _left_value(stock_a_row, "period")
                window._set_routine_tree_display_criterion("profit")
                after = _left_value(stock_a_row, "profit")
                text_changes += int(after != before)
                self.assertEqual("+20", after)
                self.assertEqual("+0", _left_value(stock_empty_row, "profit"))
                self.assertEqual("+5", _left_value(stock_b_row, "profit"))
                self.assertGreaterEqual(text_changes, 3)

    def test_parent_arrow_click_only_collapses_definition_rows(self) -> None:
        instances = [self._instance("inst-a", "A 인스턴스")]
        window = self._window_harness()
        window._routine_instance_operation_counts = lambda: {
            "inst-a": {"registered": 1, "running": 0, "stopped": 1, "error": 0}
        }

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=[]):
            window.load_routine_table()
            window._set_routine_tree_display_level("routine")

            parent_icon = window.routine_table.cellWidget(0, 0).findChild(
                setting_window.QLabel,
                "autoTradeSettingRoutineTreeIcon",
            )
            self.assertEqual("▼", parent_icon.text())
            self.assertEqual(setting_window.Qt.PointingHandCursor, parent_icon.cursor().shape())
            self.assertFalse(parent_icon.testAttribute(setting_window.Qt.WA_TransparentForMouseEvents))

            window.on_routine_table_item_clicked(window.routine_table.item(0, 0))
            self.assertEqual(set(), window._collapsed_auto_trade_definition_ids)
            window.on_routine_table_item_double_clicked(window.routine_table.item(0, 0))
            self.assertEqual(set(), window._collapsed_auto_trade_definition_ids)
            window.load_routine_table = lambda: self.fail("definition collapse must not rebuild the routine table")
            window._toggle_routine_definition_collapsed("indicator_follow")

        self.assertEqual({"indicator_follow"}, window._collapsed_auto_trade_definition_ids)
        self.assertEqual(
            ["definition", "instance"],
            [
                window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)["row_kind"]
                for row in range(window.routine_table.rowCount())
            ],
        )
        self.assertFalse(window.routine_table.isRowHidden(0))
        self.assertTrue(window.routine_table.isRowHidden(1))

    def test_parent_arrow_stays_locked_for_empty_definition(self) -> None:
        review_definition = RoutineDefinitionRecord(
            definition_id="review",
            display_name="등록확인루틴",
            package_dir=Path("routines") / "review",
            schema_version="1.0",
            version="1.0",
            routine_type="auto_trade",
            entry_file="routine.py",
            module_name="review_routine",
            settings_ui="",
            default_rules_file="rules.json",
            package_enabled=True,
            source_name="routine.json",
        )
        window = self._window_harness()
        window._routine_instance_operation_counts = lambda: {}

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition(), review_definition]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=[]), \
                patch.object(setting_window, "read_base_stocks", return_value=[]):
            window.load_routine_table()
            self.assertEqual(["definition", "definition"], [
                window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)["row_kind"]
                for row in range(window.routine_table.rowCount())
            ])

            for _index in range(20):
                review_row = next(
                    row
                    for row in range(window.routine_table.rowCount())
                    if window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)["definition_id"] == "review"
                )
                icon = window.routine_table.cellWidget(review_row, 0).findChild(
                    setting_window.QLabel,
                    "autoTradeSettingRoutineTreeIcon",
                )
                metadata = window.routine_table.item(review_row, 0).data(setting_window.Qt.UserRole)
                self.assertEqual("▶", icon.text())
                self.assertFalse(bool(metadata["has_toggle_children"]))
                self.assertFalse(bool(icon.property("autoTradeSettingRoutineTreeToggleEnabled")))
                window._apply_routine_tree_collapse_visibility = lambda: self.fail("locked parent arrow must not apply collapse")
                window._toggle_routine_definition_collapsed("review")

            self.assertEqual(set(), window._collapsed_auto_trade_definition_ids)
            self.assertFalse(window.routine_table.isRowHidden(review_row))

    def test_instance_arrow_stays_locked_when_no_stock_rows_exist(self) -> None:
        instances = [self._instance("inst-empty", "빈 인스턴스")]
        window = self._window_harness()
        window._routine_instance_operation_counts = lambda: {
            "inst-empty": {"registered": 0, "running": 0, "stopped": 0, "error": 0}
        }

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=[]):
            window.load_routine_table()
            window._set_routine_tree_display_level("routine")

            self.assertEqual(
                ["definition", "instance"],
                [
                    window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)["row_kind"]
                    for row in range(window.routine_table.rowCount())
                ],
            )
            instance_icon = window.routine_table.cellWidget(1, 0).findChild(
                setting_window.QLabel,
                "autoTradeSettingRoutineTreeIcon",
            )
            instance_metadata = window.routine_table.item(1, 0).data(setting_window.Qt.UserRole)
            self.assertEqual("▶", instance_icon.text())
            self.assertFalse(bool(instance_metadata["has_toggle_children"]))
            self.assertFalse(bool(instance_icon.property("autoTradeSettingRoutineTreeToggleEnabled")))
            self.assertFalse(window.routine_table.isRowHidden(1))

            original_apply_visibility = window._apply_routine_tree_collapse_visibility
            window._apply_routine_tree_collapse_visibility = lambda: self.fail("locked instance arrow must not apply collapse")
            window._toggle_routine_instance_collapsed("inst-empty")
            window._apply_routine_tree_collapse_visibility = original_apply_visibility

        self.assertEqual(set(), window._collapsed_auto_trade_instance_ids)
        self.assertFalse(window.routine_table.isRowHidden(1))

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=[]):
            window.load_routine_table()
            window._toggle_routine_definition_collapsed("indicator_follow")
            self.assertTrue(window.routine_table.isRowHidden(1))
            window._toggle_routine_definition_collapsed("indicator_follow")
            self.assertFalse(window.routine_table.isRowHidden(1))

    def test_routine_tree_hides_table_header_and_grid(self) -> None:
        window = self._window_harness()
        window._setup_routine_table()

        self.assertEqual(1, window.routine_table.columnCount())
        self.assertTrue(window.routine_table.horizontalHeader().isHidden())
        self.assertTrue(window.routine_table.verticalHeader().isHidden())
        self.assertFalse(window.routine_table.showGrid())
        self.assertEqual(setting_window.Qt.ScrollBarAlwaysOn, window.routine_table.verticalScrollBarPolicy())
        self.assertIn("selection-background-color: #dbeafe", window.routine_table.styleSheet())
        self.assertIn("selection-color: #111827", window.routine_table.styleSheet())

    def test_window_uses_standard_minimize_maximize_close_title_buttons(self) -> None:
        window = setting_window.AutoTradeSettingWindow()
        try:
            flags = window.windowFlags()
            self.assertFalse(bool(flags & setting_window.Qt.WindowContextHelpButtonHint))
            self.assertTrue(bool(flags & setting_window.Qt.WindowMinimizeButtonHint))
            self.assertTrue(bool(flags & setting_window.Qt.WindowMaximizeButtonHint))
            self.assertTrue(bool(flags & setting_window.Qt.WindowCloseButtonHint))
            expected_minimum_width = (
                window.routine_box.minimumWidth()
                + window._right_workspace_initial_width()
                + window.strategy_workspace_splitter.handleWidth()
                + window.layout().contentsMargins().left()
                + window.layout().contentsMargins().right()
            )
            self.assertEqual(expected_minimum_width, window.minimumWidth())
            self.assertEqual(650, window.minimumHeight())
            self.assertGreater(window.maximumWidth(), window.minimumWidth())
            self.assertGreater(window.maximumHeight(), window.minimumHeight())
        finally:
            window.close()

    def test_parent_summary_counts_are_removed_from_tree_tooltip(self) -> None:
        window = self._window_harness()
        row_data = {
            "row_kind": "definition",
            "definition_id": "indicator_follow",
            "instance_id": "",
            "display_name": "지표추종매매",
            "tree_icon": "▼",
            "registered": 12,
            "running": 12,
            "stopped": 8,
            "error": 0,
        }

        widget = window._routine_tree_row_widget(row_data, "")
        window._set_routine_tree_parent_summary_visible(widget, True)
        widget.show()
        self._app.processEvents()
        for object_name in (
            "autoTradeSettingRoutineTreeRegistered",
            "autoTradeSettingRoutineTreeRunning",
            "autoTradeSettingRoutineTreeStopped",
            "autoTradeSettingRoutineTreeError",
        ):
            self.assertIsNone(widget.findChild(setting_window.QLabel, object_name))
        self.assertIsNone(widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreeStatusGroup"))
        self.assertEqual("", widget.toolTip())

    def test_parent_title_uses_fixed_six_character_slot_and_fixed_columns(self) -> None:
        window = self._window_harness()
        samples = [
            ("단기", "단기"),
            ("단기매매", "단기매매"),
            ("지표추종매매", "지표추종매매"),
            ("지표추종매매A", "지표추종매매..."),
            ("지표추종매매BC", "지표추종매매..."),
            ("아주긴자동매매루틴", "아주긴자동매..."),
            ("123456", "123456"),
            ("1234567", "123456..."),
            ("12345678", "123456..."),
        ]
        badge_x_values = set()
        title_widths = set()

        for display_name, expected_title in samples:
            row_data = {
                "row_kind": "definition",
                "definition_id": "indicator_follow",
                "instance_id": "",
                "display_name": display_name,
                "tree_icon": "▼",
                "instance_count": 3,
                "registered": 12,
                "running": 4,
                "stopped": 8,
                "error": 0,
            }
            widget = window._routine_tree_row_widget(row_data, "")
            window._set_routine_tree_parent_summary_visible(widget, True)
            widget.show()
            self._app.processEvents()

            title = widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeTitle")
            badge = widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeInstanceCount")

            self.assertEqual(expected_title, title.text())
            self.assertEqual(
                setting_window.routine_tree_title_width(title.fontMetrics()),
                title.width(),
            )
            required_width = max(
                max(title.fontMetrics().horizontalAdvance(sample), title.fontMetrics().boundingRect(sample).width())
                for sample in ("가" * 6, "가" * 6 + "...", "123456", "123456...")
            )
            self.assertGreaterEqual(title.contentsRect().width(), required_width)
            self.assertEqual(title.width(), title.minimumWidth())
            self.assertEqual(title.width(), title.maximumWidth())
            self.assertEqual(setting_window.QSizePolicy.Fixed, title.sizePolicy().horizontalPolicy())
            self.assertEqual(setting_window.Qt.AlignCenter | setting_window.Qt.AlignVCenter, title.alignment())
            text_width = title.fontMetrics().horizontalAdvance(title.text())
            if len(display_name) <= 6:
                left_padding = (title.width() - text_width) // 2
                right_padding = title.width() - text_width - left_padding
                self.assertLessEqual(abs(left_padding - right_padding), 1)
            title_widths.add(title.width())
            badge_x = badge.mapTo(widget, badge.rect().topLeft()).x()
            badge_x_values.add(badge_x)
            self.assertEqual("", widget.toolTip())
            for object_name in (
                "autoTradeSettingRoutineTreeRegistered",
                "autoTradeSettingRoutineTreeRunning",
                "autoTradeSettingRoutineTreeStopped",
                "autoTradeSettingRoutineTreeError",
            ):
                self.assertIsNone(widget.findChild(setting_window.QLabel, object_name))

        self.assertEqual(1, len(title_widths))
        self.assertEqual(1, len(badge_x_values))

    def test_child_title_uses_fixed_name_slot_without_status_tooltip(self) -> None:
        window = self._window_harness()
        samples = [
            ("두자", "두자"),
            ("동전주", "동전주"),
            ("네글자명", "네글자명"),
            ("지표추종매매", "지표추종매매"),
            ("지표추종매매B", "지표추종매매..."),
            ("지표추종매매BC", "지표추종매매..."),
            ("아주긴자동매매루틴", "아주긴자동매..."),
            ("123456", "123456"),
            ("1234567", "123456..."),
            ("12345678", "123456..."),
        ]
        title_x_values = set()
        title_widths = set()

        for display_name, expected_title in samples:
            row_data = {
                "row_kind": "instance",
                "definition_id": "indicator_follow",
                "instance_id": "inst-a",
                "display_name": display_name,
                "tree_icon": "●",
                "instance_count": 0,
                "registered": 4,
                "running": 4,
                "stopped": 0,
                "error": 0,
            }
            widget = window._routine_tree_row_widget(row_data, "")
            widget.resize(widget.sizeHint().width(), widget.sizeHint().height())
            widget.show()
            self._app.processEvents()

            title = widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeTitle")

            self.assertEqual(expected_title, title.text())
            self.assertEqual(
                setting_window.routine_tree_title_width(title.fontMetrics()),
                title.width(),
            )
            required_width = max(
                max(title.fontMetrics().horizontalAdvance(sample), title.fontMetrics().boundingRect(sample).width())
                for sample in ("가" * 6, "가" * 6 + "...", "123456", "123456...")
            )
            self.assertGreaterEqual(title.contentsRect().width(), required_width)
            self.assertEqual(title.width(), title.minimumWidth())
            self.assertEqual(title.width(), title.maximumWidth())
            self.assertEqual(setting_window.QSizePolicy.Fixed, title.sizePolicy().horizontalPolicy())
            self.assertEqual(setting_window.Qt.AlignCenter | setting_window.Qt.AlignVCenter, title.alignment())
            text_width = title.fontMetrics().horizontalAdvance(title.text())
            if len(display_name) <= 6:
                left_padding = (title.width() - text_width) // 2
                right_padding = title.width() - text_width - left_padding
                self.assertLessEqual(abs(left_padding - right_padding), 1)

            title_x_values.add(title.mapTo(widget, title.rect().topLeft()).x())
            title_widths.add(title.width())
            self.assertEqual("", widget.toolTip())
            for object_name in (
                "autoTradeSettingRoutineTreeRegistered",
                "autoTradeSettingRoutineTreeRunning",
                "autoTradeSettingRoutineTreeStopped",
                "autoTradeSettingRoutineTreeError",
            ):
                self.assertIsNone(widget.findChild(setting_window.QLabel, object_name))

        self.assertEqual(1, len(title_x_values))
        self.assertEqual(1, len(title_widths))

    def test_routine_tree_performance_columns_keep_fixed_x_axis_by_row_kind(self) -> None:
        window = self._window_harness()
        rows = [
            {
                "row_kind": "definition",
                "definition_id": "indicator_follow",
                "instance_id": "",
                "display_name": "지표추종매매",
                "tree_icon": "▼",
                "instance_count": 3,
                "performance_period_text": "기간(0123)",
                "performance_profit_text": "수익(12,345,678 / 18.42%)",
                "performance_average_text": "평균(102,345 / 0.83%)",
                "performance_efficiency_text": "효율(1.86)",
            },
            {
                "row_kind": "definition",
                "definition_id": "review",
                "instance_id": "",
                "display_name": "등록확인루틴",
                "tree_icon": "▶",
                "instance_count": 0,
                "performance_period_text": "기간(0000)",
                "performance_profit_text": "수익(0 / 0.00%)",
                "performance_average_text": "평균(0 / 0.00%)",
                "performance_efficiency_text": "효율(0.00)",
            },
            {
                "row_kind": "instance",
                "definition_id": "indicator_follow",
                "instance_id": "inst-a",
                "display_name": "지표추종매매B",
                "tree_icon": "●",
                "performance_period_text": "기간(0045)",
                "performance_profit_text": "수익(1,200 / 1.20%)",
                "performance_average_text": "평균(27 / 0.03%)",
                "performance_efficiency_text": "효율(1.20)",
            },
            {
                "row_kind": "instance",
                "definition_id": "indicator_follow",
                "instance_id": "inst-b",
                "display_name": "매우긴인스턴스이름",
                "tree_icon": "●",
                "performance_period_text": "기간(9999)",
                "performance_profit_text": "수익(99,999,999 / 000.0%)",
                "performance_average_text": "평균(99,999,999 / 00.0%)",
                "performance_efficiency_text": "효율(000.0)",
            },
            {
                "row_kind": "instance",
                "definition_id": "indicator_follow",
                "instance_id": "inst-c",
                "display_name": "단기매매",
                "tree_icon": "●",
                "performance_period_text": "기간(0001)",
                "performance_profit_text": "수익(125,000 / 8.40%)",
                "performance_average_text": "평균(125,000 / 8.40%)",
                "performance_efficiency_text": "효율(2.45)",
            },
            {
                "row_kind": "stock",
                "definition_id": "indicator_follow",
                "instance_id": "inst-c",
                "display_name": "삼성전자",
                "tree_icon": "",
                "performance_period_text": "기간(0000)",
                "performance_profit_text": "수익(0 / 0.0%)",
                "performance_average_text": "평균(0 / 0.0%)",
                "performance_efficiency_text": "효율(0.0)",
            },
        ]
        x_values = {
            "definition": {"profit": set(), "average": set(), "efficiency": set()},
            "instance": {"period": set(), "profit": set(), "average": set(), "efficiency": set()},
            "stock": {"period": set(), "profit": set(), "average": set(), "efficiency": set()},
        }
        widths = {"period": set(), "profit": set(), "average": set(), "efficiency": set()}

        for row_data in rows:
            widget = window._routine_tree_row_widget(row_data, "")
            if row_data["row_kind"] == "definition":
                window._set_routine_tree_parent_summary_visible(widget, True)
            widget.resize(widget.sizeHint().width(), widget.sizeHint().height())
            widget.show()
            self._app.processEvents()

            title = widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeTitle")
            period = widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformancePeriod")
            period_spacer = widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformancePeriodSpacer")
            parent_profit_column_spacer = widget.findChild(
                setting_window.QWidget,
                "autoTradeSettingRoutineTreeParentProfitColumnSpacer",
            )
            profit = widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformanceProfit")
            average = widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformanceAverage")
            efficiency = widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformanceEfficiency")

            self.assertIsNotNone(title)
            if row_data["row_kind"] == "definition":
                self.assertIsNone(period)
                self.assertIsNone(period_spacer)
                self.assertIsNotNone(parent_profit_column_spacer)
            else:
                self.assertIsNotNone(period)
                self.assertIsNone(period_spacer)
                self.assertIsNone(parent_profit_column_spacer)
            self.assertIsNotNone(profit)
            self.assertIsNotNone(average)
            self.assertIsNotNone(efficiency)
            if row_data["row_kind"] in {"instance", "stock"}:
                self.assertGreater(
                    period.mapTo(widget, period.rect().topLeft()).x(),
                    title.mapTo(widget, title.rect().topLeft()).x() + title.width(),
                )
            labels = {"profit": profit, "average": average, "efficiency": efficiency}
            if row_data["row_kind"] in {"instance", "stock"}:
                labels["period"] = period
            for key, label in labels.items():
                x_values[str(row_data["row_kind"])][key].add(label.mapTo(widget, label.rect().topLeft()).x())
                widths[key].add(label.width())
                left_value = label.findChild(
                    setting_window.QLabel,
                    f"autoTradeSettingRoutineTreePerformance{key.title()}LeftValue",
                )
                if key == "period":
                    left_value = label.findChild(
                        setting_window.QLabel,
                        "autoTradeSettingRoutineTreePerformancePeriodLeftValue",
                    )
                if key == "profit":
                    left_value = label.findChild(
                        setting_window.QLabel,
                        "autoTradeSettingRoutineTreePerformanceProfitLeftValue",
                    )
                if key == "average":
                    left_value = label.findChild(
                        setting_window.QLabel,
                        "autoTradeSettingRoutineTreePerformanceAverageLeftValue",
                    )
                if key == "efficiency":
                    left_value = label.findChild(
                        setting_window.QLabel,
                        "autoTradeSettingRoutineTreePerformanceEfficiencyLeftValue",
                    )
                self.assertIsNotNone(left_value)
                self.assertEqual(setting_window.Qt.AlignRight | setting_window.Qt.AlignVCenter, left_value.alignment())
                self.assertGreaterEqual(left_value.width(), left_value.fontMetrics().horizontalAdvance(left_value.text()))
                if key in {"profit", "average"}:
                    right_value = label.findChild(
                        setting_window.QLabel,
                        f"autoTradeSettingRoutineTreePerformance{key.title()}RightValue",
                    )
                    self.assertIsNotNone(right_value)
                    self.assertEqual(setting_window.Qt.AlignRight | setting_window.Qt.AlignVCenter, right_value.alignment())
                    self.assertGreaterEqual(right_value.width(), right_value.fontMetrics().horizontalAdvance(right_value.text()))
            self.assertEqual("", widget.toolTip())

        for row_kind, keys in (
            ("definition", ("profit", "average", "efficiency")),
            ("instance", ("period", "profit", "average", "efficiency")),
            ("stock", ("period", "profit", "average", "efficiency")),
        ):
            for key in keys:
                self.assertEqual(1, len(x_values[row_kind][key]))
                self.assertEqual(1, len(widths[key]))
        self.assertEqual(x_values["definition"]["profit"], x_values["instance"]["profit"])
        self.assertEqual(x_values["instance"]["profit"], x_values["stock"]["profit"])

    def test_routine_tree_numeric_slots_keep_fixed_right_edge_for_value_lengths(self) -> None:
        window = self._window_harness()
        widget = window._routine_tree_row_widget(
            {
                "row_kind": "instance",
                "definition_id": "indicator_follow",
                "instance_id": "inst-a",
                "display_name": "인스턴스",
                "tree_icon": "▼",
            },
            "",
        )
        widget.resize(widget.sizeHint().width(), widget.sizeHint().height())
        widget.show()
        widget.layout().activate()
        self._app.processEvents()

        values_by_object_name = {
            "autoTradeSettingRoutineTreePerformancePeriodLeftValue": (
                "0",
                "5",
                "25",
                "999",
                "1234",
            ),
            "autoTradeSettingRoutineTreePerformanceProfitLeftValue": (
                "0",
                "25",
                "1,234",
                "12,345",
                "123,456",
                "-2,500",
                "+12,345",
            ),
            "autoTradeSettingRoutineTreePerformanceProfitRightValue": (
                "0.0%",
                "+3.5%",
                "-4.2%",
                "123.4%",
            ),
            "autoTradeSettingRoutineTreePerformanceAverageLeftValue": (
                "0",
                "25",
                "1,234",
                "12,345",
                "123,456",
                "-2,500",
                "+12,345",
            ),
            "autoTradeSettingRoutineTreePerformanceAverageRightValue": (
                "0.0%",
                "+3.5%",
                "-4.2%",
                "12.3%",
            ),
            "autoTradeSettingRoutineTreePerformanceEfficiencyLeftValue": (
                "0.0",
                "5.0",
                "25.0",
                "999.0",
            ),
        }

        for object_name, values in values_by_object_name.items():
            label = widget.findChild(setting_window.QLabel, object_name)
            self.assertIsNotNone(label)
            initial_geometry = label.geometry()
            initial_width = label.width()
            right_edges = set()
            for value in values:
                label.setText(value)
                widget.layout().activate()
                self._app.processEvents()
                self.assertEqual(initial_geometry, label.geometry())
                self.assertEqual(initial_width, label.width())
                self.assertTrue(label.alignment() & setting_window.Qt.AlignRight)
                self.assertLessEqual(
                    label.fontMetrics().horizontalAdvance(value),
                    label.contentsRect().width(),
                )
                right_edges.add(
                    label.mapTo(widget, label.contentsRect().topRight()).x()
                )
            self.assertEqual(1, len(right_edges))

    def test_routine_tree_title_text_contract(self) -> None:
        samples = [
            ("가", "가"),
            ("동전주", "동전주"),
            ("단기매매", "단기매매"),
            ("지표추종매매", "지표추종매매"),
            ("지표추종매매B", "지표추종매매..."),
            ("지표추종매매BC", "지표추종매매..."),
            ("ABCDEFGHI", "ABCDEF..."),
            ("123456", "123456"),
            ("1234567", "123456..."),
            ("12345678", "123456..."),
        ]

        for display_name, expected in samples:
            with self.subTest(display_name=display_name):
                self.assertEqual(expected, setting_window.routine_tree_title_text(display_name))

    def test_collapsed_parent_shows_summary_without_hover(self) -> None:
        window = self._window_harness()
        row_data = {
            "row_kind": "definition",
            "definition_id": "indicator_follow",
            "instance_id": "",
            "display_name": "지표추종매매",
            "tree_icon": "▶",
            "instance_count": 3,
            "registered": 12,
            "running": 4,
            "stopped": 8,
            "error": 0,
        }

        widget = window._routine_tree_row_widget(row_data, "지표추종매매")

        count_badge = widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeInstanceCount")
        self.assertEqual("루틴3", count_badge.text())
        self.assertFalse(count_badge.isHidden())
        self.assertEqual("", widget.toolTip())
        for object_name in (
            "autoTradeSettingRoutineTreeRegistered",
            "autoTradeSettingRoutineTreeRunning",
            "autoTradeSettingRoutineTreeStopped",
            "autoTradeSettingRoutineTreeError",
        ):
            self.assertIsNone(widget.findChild(setting_window.QLabel, object_name))

    def test_parent_selection_is_view_scope_and_not_routine_dir(self) -> None:
        instances = [self._instance("inst-a", "A 인스턴스"), self._instance("inst-b", "B 인스턴스")]
        window = self._window_harness()
        window._routine_instance_operation_counts = lambda: {}
        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances):
            window.load_routine_table()
            window.routine_table.selectRow(0)

            self.assertEqual("indicator_follow", window.current_selected_definition_id())
            self.assertEqual("", window.current_selected_instance_id())
            self.assertIsNone(window.current_selected_routine_dir())
            self.assertEqual(("inst-a", "inst-b"), window.current_selected_target_instance_ids())

    def test_instance_selection_returns_single_instance_scope(self) -> None:
        instances = [self._instance("inst-a", "A 인스턴스")]
        window = self._window_harness()
        window._routine_instance_operation_counts = lambda: {}
        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(Path, "exists", return_value=True):
            window.load_routine_table()
            window.routine_table.selectRow(1)

            self.assertEqual("inst-a", window.current_selected_instance_id())
            self.assertEqual(("inst-a",), window.current_selected_target_instance_ids())
            self.assertEqual(Path("routines") / "indicator_follow", window.current_selected_routine_dir())

    def test_stock_dirs_follow_selected_instance_ids(self) -> None:
        class Window:
            def current_selected_target_instance_ids(self):
                return ("inst-a", "inst-b")

        stocks = [
            {"stock_path": "stocks/005930_A", "assigned_routine_instance_id": "inst-a"},
            {"stock_path": "stocks/000660_B", "assigned_routine_instance_id": "other"},
            {"stock_path": "stocks/035420_C", "assigned_routine_instance_id": "inst-b"},
        ]
        with patch.object(table_loader, "read_base_stocks", return_value=stocks):
            dirs = table_loader._selected_instance_stock_dirs(Window())

        self.assertEqual(
            [table_loader.PROJECT_ROOT / "stocks" / "005930_A", table_loader.PROJECT_ROOT / "stocks" / "035420_C"],
            dirs,
        )

    def test_runtime_state_for_order_uses_selected_assignment_scope(self) -> None:
        window = self._window_harness()
        window.current_selected_target_instance_ids = lambda: ("inst-a",)
        window.current_selected_routine_row_metadata = lambda: {
            "row_kind": "definition",
            "definition_id": "indicator_follow",
        }
        stocks = [
            {"stock_path": "stocks/005930_A", "assigned_routine_instance_id": "other"},
            {"stock_path": "stocks/005930_B", "assigned_routine_instance_id": "inst-a"},
        ]

        def fake_read_json(path: Path):
            if str(path).endswith("config.json"):
                if "005930_B" in str(path):
                    return {"assigned_routine_instance_id": "inst-a", "real_trade_enabled": True}
                return {"assigned_routine_instance_id": "other"}
            return {"status": "RUNNING", "trade_enabled": True}

        with patch.object(setting_window, "read_base_stocks", return_value=stocks), \
                patch.object(setting_window, "read_json_dict", side_effect=fake_read_json):
            result = window.auto_trade_runtime_state_for_order({"code": "005930"})

        self.assertTrue(result["found"])
        self.assertIn("005930_B", result["stock_dir"])
        self.assertEqual("inst-a", result["config"]["assigned_routine_instance_id"])

    def test_runtime_state_for_order_blocks_parent_without_instance_scope(self) -> None:
        window = self._window_harness()
        window.current_selected_target_instance_ids = lambda: ()
        window.current_selected_routine_row_metadata = lambda: {
            "row_kind": "definition",
            "definition_id": "indicator_follow",
        }

        result = window.auto_trade_runtime_state_for_order({"code": "005930"})

        self.assertFalse(result["found"])
        self.assertEqual(
            [setting_window.ROUTINE_INSTANCE_REQUIRED_MESSAGE],
            result["issues"],
        )

    def test_selection_summary_area_is_removed_from_workspace(self) -> None:
        with patch.object(AutoTradeSettingWindow, "refresh_all", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "update_startup_recovery_controls", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "current_runtime_file_signature", lambda _self: tuple()):
            window = AutoTradeSettingWindow()
        self.addCleanup(window.close)

        group_titles = [group.title() for group in window.findChildren(setting_window.QGroupBox)]
        self.assertNotIn("Selection Summary", group_titles)
        self.assertFalse(hasattr(window, "selection_summary_box"))
        self.assertFalse(hasattr(window, "summary_routine_value"))

        workspace_layout = window.strategy_workspace_widget.layout()
        self.assertEqual(window.stock_box, workspace_layout.itemAt(0).widget())
        self.assertEqual(1, workspace_layout.count())
        self.assertEqual("자동매매운영실적", window.routine_box.title())
        self.assertEqual("등록종목상태", window.stock_box.title())
        self.assertEqual(window.routine_box.font(), window.stock_box.font())
        self.assertEqual(window.routine_box.alignment(), window.stock_box.alignment())
        self.assertEqual(setting_window.Qt.AlignLeft, window.routine_box.alignment())
        self.assertEqual(window.routine_box.isFlat(), window.stock_box.isFlat())
        self.assertFalse(window.routine_box.isFlat())
        window._position_routine_tree_display_level_badges()
        routine_margins = window.routine_box.layout().contentsMargins()
        stock_margins = window.stock_box.layout().contentsMargins()
        self.assertEqual(
            (
                routine_margins.left(),
                routine_margins.right(),
                routine_margins.bottom(),
            ),
            (
                stock_margins.left(),
                stock_margins.right(),
                stock_margins.bottom(),
            ),
        )
        self.assertGreaterEqual(
            routine_margins.top(),
            window._routine_tree_display_level_badges.height(),
        )
        self.assertGreater(routine_margins.top(), stock_margins.top())
        self.assertEqual(window.routine_box.styleSheet(), window.stock_box.styleSheet())
        self.assertEqual(
            setting_window.AUTO_TRADE_SETTING_WORKSPACE_GROUP_BOX_STYLE,
            window.routine_box.styleSheet(),
        )

        def _group_box_rects(group_box):
            option = QStyleOptionGroupBox()
            option.initFrom(group_box)
            option.text = group_box.title()
            option.lineWidth = 1
            option.subControls = (
                setting_window.QStyle.SC_GroupBoxFrame
                | setting_window.QStyle.SC_GroupBoxLabel
            )
            style = group_box.style()
            return (
                style.subControlRect(
                    setting_window.QStyle.CC_GroupBox,
                    option,
                    setting_window.QStyle.SC_GroupBoxLabel,
                    group_box,
                ),
                style.subControlRect(
                    setting_window.QStyle.CC_GroupBox,
                    option,
                    setting_window.QStyle.SC_GroupBoxFrame,
                    group_box,
                ),
                style.subControlRect(
                    setting_window.QStyle.CC_GroupBox,
                    option,
                    setting_window.QStyle.SC_GroupBoxContents,
                    group_box,
                ),
            )

        routine_label_rect, routine_frame_rect, routine_contents_rect = _group_box_rects(
            window.routine_box
        )
        stock_label_rect, stock_frame_rect, stock_contents_rect = _group_box_rects(
            window.stock_box
        )
        self.assertEqual(
            (routine_label_rect.x(), routine_label_rect.y(), routine_label_rect.height()),
            (stock_label_rect.x(), stock_label_rect.y(), stock_label_rect.height()),
        )
        self.assertEqual(
            (routine_frame_rect.y(), routine_frame_rect.height()),
            (stock_frame_rect.y(), stock_frame_rect.height()),
        )
        self.assertEqual(
            setting_window.AUTO_TRADE_SETTING_WORKSPACE_GROUP_BOX_FRAME_TOP,
            routine_frame_rect.y(),
        )
        self.assertEqual(
            (routine_contents_rect.y(), routine_contents_rect.height()),
            (stock_contents_rect.y(), stock_contents_rect.height()),
        )
        window._position_routine_tree_display_level_badges()
        badge_rect = window._routine_tree_display_level_badges.geometry()
        self.assertGreater(badge_rect.y(), routine_frame_rect.y())
        self.assertGreaterEqual(
            badge_rect.y(),
            routine_label_rect.bottom() + 1,
        )
        self.assertFalse(badge_rect.intersects(window.routine_table.geometry()))
        self.assertGreaterEqual(window.routine_table.geometry().y(), badge_rect.bottom() + 1)
        window.selected_routine_instance_count_badge.setText("루틴3")
        window.selected_routine_instance_count_badge.setVisible(True)
        window.show()
        self._app.processEvents()
        window._position_routine_tree_display_level_badges()
        self._app.processEvents()
        badge_center_y = window._routine_tree_display_level_badges.mapTo(
            window,
            window._routine_tree_display_level_badges.rect().center(),
        ).y()
        status_center_y = window.selected_routine_status_bar.mapTo(
            window,
            window.selected_routine_status_bar.rect().center(),
        ).y()
        self.assertLessEqual(abs(badge_center_y - status_center_y), 1)
        self.assertEqual(
            window._routine_tree_display_level_badges.height(),
            window.selected_routine_status_bar.height(),
        )
        aligned_controls = (
            *window._routine_tree_display_level_buttons.values(),
            *window._routine_tree_display_scope_buttons.values(),
            *window._routine_tree_display_criterion_buttons.values(),
            window.selected_routine_signal_label,
            window.selected_routine_name_button,
            window.selected_routine_instance_count_badge,
            *window.selected_routine_status_buttons.values(),
            window.btn_early_close,
            window.btn_stop,
        )
        for control in aligned_controls:
            with self.subTest(control=control.objectName()):
                control_center_y = control.mapTo(window, control.rect().center()).y()
                self.assertLessEqual(abs(control_center_y - status_center_y), 1)

        window.routine_table.setRowCount(1)
        first_item = setting_window.QTableWidgetItem("첫 번째 루틴")
        window.routine_table.setItem(0, 0, first_item)
        self._app.processEvents()
        first_row_rect = window.routine_table.visualItemRect(first_item)
        first_row_y = window.routine_table.viewport().mapTo(
            window,
            first_row_rect.topLeft(),
        ).y()
        stock_header = window.stock_table.horizontalHeader()
        stock_header_y = stock_header.mapTo(
            window,
            stock_header.rect().topLeft(),
        ).y()
        self.assertLessEqual(abs(first_row_y - stock_header_y), 1)
        routine_label_rect, _, _ = _group_box_rects(window.routine_box)
        stock_label_rect, _, _ = _group_box_rects(window.stock_box)
        badge_rect = window._routine_tree_display_level_badges.geometry()
        status_rect = window.selected_routine_status_bar.geometry()
        routine_title_gap = badge_rect.top() - routine_label_rect.bottom() - 1
        stock_title_gap = status_rect.top() - stock_label_rect.bottom() - 1
        self.assertEqual(routine_title_gap, stock_title_gap)
        self.assertIn(routine_title_gap, range(7, 10))
        self.assertEqual(
            setting_window.AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT,
            badge_rect.height(),
        )
        self.assertEqual(badge_rect.height(), status_rect.height())
        badge_bottom_y = window._routine_tree_display_level_badges.mapTo(
            window,
            window._routine_tree_display_level_badges.rect().bottomLeft(),
        ).y()
        status_bottom_y = window.selected_routine_status_bar.mapTo(
            window,
            window.selected_routine_status_bar.rect().bottomLeft(),
        ).y()
        self.assertIn(first_row_y - badge_bottom_y - 1, range(8, 11))
        self.assertIn(stock_header_y - status_bottom_y - 1, range(8, 11))
        self.assertEqual(
            window.routine_table.geometry().bottom(),
            window.stock_table.geometry().bottom(),
        )

    def test_selected_routine_status_bar_reflects_parent_and_instance_counts(self) -> None:
        instances = [self._instance("inst-a", "A 인스턴스")]
        window = self._window_harness()
        window._setup_selected_routine_status_bar()
        window.load_selected_routine_stocks = lambda: None
        counts = {"inst-a": {"registered": 7, "running": 3, "stopped": 4, "error": 1}}
        window._routine_instance_operation_counts = lambda: counts

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances):
            window.load_routine_table()
            window.routine_table.selectRow(0)
            window.update_selected_routine_status_bar()

            self.assertEqual("●", window.selected_routine_signal_label.text())
            self.assertEqual("지표추종매매", window.selected_routine_name_button.text())
            self.assertEqual("루틴1", window.selected_routine_instance_count_badge.text())
            self.assertFalse(window.selected_routine_instance_count_badge.isHidden())
            self.assertEqual("종목(7)", window.selected_routine_status_buttons["all"].text())
            self.assertEqual("실행(3)", window.selected_routine_status_buttons["running"].text())
            self.assertEqual("정지(4)", window.selected_routine_status_buttons["stopped"].text())
            self.assertEqual("검토(1)", window.selected_routine_status_buttons["error"].text())

            calls = []
            window.load_selected_routine_stocks = lambda: calls.append(window._stock_status_filter)
            window.selected_routine_status_buttons["running"].click()
            self.assertEqual("running", window._stock_status_filter)
            window.selected_routine_name_button.click()
            self.assertEqual("all", window._stock_status_filter)
            self.assertEqual(["running", "all"], calls)

            window.routine_table.selectRow(1)
            window.update_selected_routine_status_bar()
            self.assertEqual("A 인스턴스", window.selected_routine_name_button.text())
            self.assertTrue(window.selected_routine_instance_count_badge.isHidden())
            self.assertEqual("종목(7)", window.selected_routine_status_buttons["all"].text())
            self.assertEqual("실행(3)", window.selected_routine_status_buttons["running"].text())
            self.assertEqual("정지(4)", window.selected_routine_status_buttons["stopped"].text())
            self.assertEqual("검토(1)", window.selected_routine_status_buttons["error"].text())

    def test_instance_renders_current_stock_rows_without_internal_scope_badges(self) -> None:
        instances = [self._instance("inst-a", "A 인스턴스")]
        stocks = [
            {
                "stock_path": "stocks/005930_A",
                "assigned_routine_instance_id": "inst-a",
                "code": "005930",
                "name": "삼성전자",
            },
            {
                "stock_path": "stocks/005380_B",
                "assigned_routine_instance_id": "inst-a",
                "code": "005380",
                "name": "현대차",
            },
        ]
        window = self._window_harness()
        window._routine_instance_operation_counts = lambda: {
            "inst-a": {"registered": 2, "running": 0, "stopped": 2, "error": 0}
        }

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=stocks):
            window.load_routine_table()
            window._set_routine_tree_display_level("stock")

        row_kinds = [
            window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)["row_kind"]
            for row in range(window.routine_table.rowCount())
        ]
        self.assertEqual(
            ["definition", "instance", "stock", "stock"],
            row_kinds,
        )
        self.assertNotIn("현재 종목", [window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)["display_name"] for row in range(window.routine_table.rowCount())])
        self.assertNotIn("과거 종목", [window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)["display_name"] for row in range(window.routine_table.rowCount())])

        stock_widget = window.routine_table.cellWidget(2, 0)
        second_stock_widget = window.routine_table.cellWidget(3, 0)
        instance_widget = window.routine_table.cellWidget(1, 0)
        stock_layout_margins = stock_widget.layout().contentsMargins()
        second_stock_layout_margins = second_stock_widget.layout().contentsMargins()
        self.assertEqual(
            (
                setting_window.AUTO_TRADE_SETTING_STOCK_ROW_MARGIN_X,
                setting_window.AUTO_TRADE_SETTING_INSTANCE_GROUP_TOP_GAP,
                setting_window.AUTO_TRADE_SETTING_STOCK_ROW_MARGIN_X,
                0,
            ),
            (
                stock_layout_margins.left(),
                stock_layout_margins.top(),
                stock_layout_margins.right(),
                stock_layout_margins.bottom(),
            ),
        )
        self.assertEqual(0, second_stock_layout_margins.top())
        self.assertEqual(setting_window.AUTO_TRADE_SETTING_STOCK_ROW_SPACING, stock_widget.layout().spacing())
        instance_icon = instance_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeIcon")
        stock_icon = stock_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeIcon")
        instance_title = instance_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeTitle")
        stock_title = stock_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeTitle")
        self.assertEqual("▼", instance_icon.text())
        self.assertEqual(setting_window.Qt.PointingHandCursor, instance_icon.cursor().shape())
        self.assertFalse(instance_icon.testAttribute(setting_window.Qt.WA_TransparentForMouseEvents))
        self.assertEqual("▪", stock_icon.text())
        self.assertIn("color: #7E22CE", stock_icon.styleSheet())
        self.assertEqual("삼성전자", stock_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeTitle").text())
        self.assertEqual("삼성전자", stock_title.toolTip())
        self.assertEqual("삼성전자", window.routine_table.item(2, 0).data(setting_window.Qt.ToolTipRole))
        self.assertEqual(setting_window.Qt.AlignCenter | setting_window.Qt.AlignVCenter, instance_title.alignment())
        self.assertEqual(setting_window.Qt.AlignLeft | setting_window.Qt.AlignVCenter, stock_title.alignment())
        self.assertIn("color: #7E22CE", stock_title.styleSheet())
        self.assertEqual(
            instance_title.mapTo(instance_widget, instance_title.rect().topLeft()).x(),
            stock_title.mapTo(stock_widget, stock_title.rect().topLeft()).x(),
        )
        stock_title_spacer = stock_widget.findChild(
            setting_window.QWidget,
            "autoTradeSettingRoutineTreeStockTitleXCompensation",
        )
        self.assertEqual(
            setting_window.AUTO_TRADE_SETTING_STOCK_TITLE_X_COMPENSATION,
            stock_title_spacer.width(),
        )
        stock_performance_spacer = stock_widget.findChild(
            setting_window.QWidget,
            "autoTradeSettingRoutineTreeStockPerformanceXCompensation",
        )
        self.assertEqual(
            setting_window.AUTO_TRADE_SETTING_STOCK_PERFORMANCE_X_COMPENSATION,
            stock_performance_spacer.width(),
        )
        self.assertIsNotNone(stock_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformancePeriod"))
        self.assertIsNotNone(stock_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformanceProfit"))
        self.assertIsNotNone(stock_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformanceAverage"))
        self.assertIsNotNone(stock_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreePerformanceEfficiency"))
        for label in stock_widget.findChildren(setting_window.QLabel):
            if label.objectName().startswith("autoTradeSettingRoutineTreePerformance"):
                self.assertIn("color: #7E22CE", label.styleSheet())
        self.assertEqual(
            "0",
            stock_widget.findChild(
                setting_window.QLabel,
                "autoTradeSettingRoutineTreePerformancePeriodLeftValue",
            ).text(),
        )
        self.assertEqual(
            setting_window.AUTO_TRADE_SETTING_STOCK_ROW_HEIGHT
            + setting_window.AUTO_TRADE_SETTING_INSTANCE_GROUP_TOP_GAP,
            window.routine_table.rowHeight(2),
        )
        self.assertEqual(
            setting_window.AUTO_TRADE_SETTING_STOCK_ROW_HEIGHT,
            window.routine_table.rowHeight(3),
        )
        self.assertLessEqual(window.routine_table.rowHeight(3), window.routine_table.rowHeight(1) - 2)
        stock_widget.resize(max(stock_widget.sizeHint().width(), 960), stock_widget.sizeHint().height())
        stock_widget.layout().activate()
        previous_x = -1
        for object_name in (
            "autoTradeSettingRoutineTreeTitle",
            "autoTradeSettingRoutineTreePerformancePeriod",
            "autoTradeSettingRoutineTreePerformanceProfit",
            "autoTradeSettingRoutineTreePerformanceAverage",
            "autoTradeSettingRoutineTreePerformanceEfficiency",
        ):
            child = stock_widget.findChild(setting_window.QWidget, object_name)
            self.assertIsNotNone(child)
            child_x = child.mapTo(stock_widget, child.rect().topLeft()).x()
            self.assertGreaterEqual(child_x, previous_x)
            previous_x = child_x
        window.routine_table.selectRow(2)
        self.assertEqual(("inst-a",), window.current_selected_target_instance_ids())
        window.routine_table.selectRow(3)
        self.assertEqual(("inst-a",), window.current_selected_target_instance_ids())

    def test_instance_arrow_click_collapses_stock_rows_independently(self) -> None:
        instances = [self._instance("inst-a", "A 인스턴스"), self._instance("inst-b", "B 인스턴스")]
        stocks = [
            {
                "stock_path": "stocks/005930_A",
                "assigned_routine_instance_id": "inst-a",
                "code": "005930",
                "name": "삼성전자",
            },
            {
                "stock_path": "stocks/005380_B",
                "assigned_routine_instance_id": "inst-b",
                "code": "005380",
                "name": "현대차",
            },
        ]
        window = self._window_harness()
        window._routine_instance_operation_counts = lambda: {
            "inst-a": {"registered": 1, "running": 0, "stopped": 1, "error": 0},
            "inst-b": {"registered": 1, "running": 1, "stopped": 0, "error": 0},
        }

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=stocks):
            window.load_routine_table()
            window._set_routine_tree_display_level("stock")

            self.assertEqual(
                ["definition", "instance", "stock", "instance", "stock"],
                [
                    window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)["row_kind"]
                    for row in range(window.routine_table.rowCount())
                ],
            )
            first_icon = window.routine_table.cellWidget(1, 0).findChild(
                setting_window.QLabel,
                "autoTradeSettingRoutineTreeIcon",
            )
            self.assertEqual("▼", first_icon.text())
            first_instance_meta = window.routine_table.item(1, 0).data(setting_window.Qt.UserRole)
            second_instance_meta = window.routine_table.item(3, 0).data(setting_window.Qt.UserRole)
            self.assertFalse(bool(first_instance_meta.get("instance_group_top_gap")))
            self.assertTrue(bool(second_instance_meta.get("instance_group_top_gap")))
            self.assertEqual(
                setting_window.AUTO_TRADE_SETTING_INSTANCE_GROUP_TOP_GAP,
                window.routine_table.rowHeight(3) - window.routine_table.rowHeight(1),
            )
            self.assertEqual(window.routine_table.rowHeight(2), window.routine_table.rowHeight(4))
            self.assertEqual(
                setting_window.AUTO_TRADE_SETTING_INSTANCE_GROUP_TOP_GAP,
                window.routine_table.rowHeight(2)
                - setting_window.AUTO_TRADE_SETTING_STOCK_ROW_HEIGHT,
            )

            window.routine_table.selectRow(1)
            selected_before_toggle = []
            window.load_selected_routine_stocks = lambda: selected_before_toggle.append(window._stock_status_filter)
            original_load_routine_table = window.load_routine_table
            window.load_routine_table = lambda: self.fail("instance collapse must not rebuild the routine table")
            window._toggle_routine_instance_collapsed("inst-a")
            window.load_routine_table = original_load_routine_table

        self.assertEqual({"inst-a"}, window._collapsed_auto_trade_instance_ids)
        self.assertEqual([], selected_before_toggle)
        row_metadata = [
            window.routine_table.item(row, 0).data(setting_window.Qt.UserRole)
            for row in range(window.routine_table.rowCount())
        ]
        self.assertEqual(
            [
                ("definition", ""),
                ("instance", "inst-a"),
                ("stock", "inst-a"),
                ("instance", "inst-b"),
                ("stock", "inst-b"),
            ],
            [(str(meta["row_kind"]), str(meta.get("instance_id", ""))) for meta in row_metadata],
        )
        self.assertFalse(window.routine_table.isRowHidden(1))
        self.assertTrue(window.routine_table.isRowHidden(2))
        self.assertFalse(window.routine_table.isRowHidden(3))
        self.assertFalse(window.routine_table.isRowHidden(4))
        collapsed_icon = window.routine_table.cellWidget(1, 0).findChild(
            setting_window.QLabel,
            "autoTradeSettingRoutineTreeIcon",
        )
        self.assertEqual("▶", collapsed_icon.text())
        self.assertEqual("inst-a", window.current_selected_instance_id())
        self.assertEqual(("inst-a",), window.current_selected_target_instance_ids())
        window.on_routine_table_item_double_clicked(window.routine_table.item(1, 0))
        self.assertEqual({"inst-a"}, window._collapsed_auto_trade_instance_ids)

        window._toggle_routine_definition_collapsed("indicator_follow")
        self.assertEqual({"indicator_follow"}, window._collapsed_auto_trade_definition_ids)
        self.assertTrue(window.routine_table.isRowHidden(1))
        self.assertTrue(window.routine_table.isRowHidden(2))
        self.assertTrue(window.routine_table.isRowHidden(3))
        self.assertTrue(window.routine_table.isRowHidden(4))
        window._toggle_routine_definition_collapsed("indicator_follow")
        self.assertEqual(set(), window._collapsed_auto_trade_definition_ids)
        self.assertFalse(window.routine_table.isRowHidden(1))
        self.assertTrue(window.routine_table.isRowHidden(2))
        self.assertFalse(window.routine_table.isRowHidden(3))
        self.assertFalse(window.routine_table.isRowHidden(4))

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=stocks):
            window._set_routine_tree_display_level("stock")
            window._set_routine_tree_display_scope("all")

        self.assertEqual(set(), window._collapsed_auto_trade_instance_ids)
        stock_row = window.routine_table.item(2, 0).data(setting_window.Qt.UserRole)
        self.assertEqual("all", stock_row["display_scope"])
        self.assertFalse(window.routine_table.isRowHidden(1))
        self.assertFalse(window.routine_table.isRowHidden(2))

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances), \
                patch.object(setting_window, "read_base_stocks", return_value=stocks):
            window._toggle_routine_instance_collapsed("inst-a")

        self.assertEqual({"inst-a"}, window._collapsed_auto_trade_instance_ids)
        self.assertEqual(5, window.routine_table.rowCount())

    def test_stock_status_filter_limits_loaded_stock_rows_by_existing_status_rules(self) -> None:
        class Window:
            pass

        window = Window()
        window.stock_table = QTableWidget(0, 11)
        window.current_selected_target_instance_ids = lambda: ("inst-a",)
        window.current_selected_routine_dir = lambda: Path("routines") / "indicator_follow"
        window.current_selected_routine_name = lambda: "지표추종매매"
        window.capture_stock_table_view_state = lambda: (set(), 0)
        window.restore_stock_table_view_state = lambda _paths, _scroll: None
        window.update_selected_routine_status_bar = lambda: None
        window.update_action_buttons = lambda: None
        window._stock_visual_order = []

        stocks = [
            {"stock_path": "stocks/111111_RUN", "assigned_routine_instance_id": "inst-a", "code": "111111", "name": "실행"},
            {"stock_path": "stocks/222222_STOP", "assigned_routine_instance_id": "inst-a", "code": "222222", "name": "정지"},
            {"stock_path": "stocks/333333_ERR", "assigned_routine_instance_id": "inst-a", "code": "333333", "name": "검토"},
        ]

        def fake_read_json(path: Path):
            text = str(path)
            if text.endswith("config.json"):
                return {"assigned_routine_instance_id": "inst-a", "operation_mode": "SCHEDULED"}
            if "111111_RUN" in text:
                return {"status": "RUNNING", "trade_enabled": True}
            if "333333_ERR" in text:
                return {"status": "ERROR", "trade_enabled": False}
            return {"status": "STOPPED", "trade_enabled": False}

        with patch.object(table_loader, "read_base_stocks", return_value=stocks), \
                patch.object(table_loader, "read_json_dict", side_effect=fake_read_json):
            window._stock_status_filter = "all"
            table_loader.auto_trade_load_selected_routine_stocks(window)
            self.assertEqual(3, window.stock_table.rowCount())

            window._stock_status_filter = "running"
            table_loader.auto_trade_load_selected_routine_stocks(window)
            self.assertEqual(["111111"], [window.stock_table.item(row, 0).text() for row in range(window.stock_table.rowCount())])

            window._stock_status_filter = "stopped"
            table_loader.auto_trade_load_selected_routine_stocks(window)
            self.assertEqual(
                ["222222", "333333"],
                [window.stock_table.item(row, 0).text() for row in range(window.stock_table.rowCount())],
            )

            window._stock_status_filter = "error"
            table_loader.auto_trade_load_selected_routine_stocks(window)
            self.assertEqual(["333333"], [window.stock_table.item(row, 0).text() for row in range(window.stock_table.rowCount())])

    def test_maximized_workspace_reserves_stock_table_required_width(self) -> None:
        with patch.object(AutoTradeSettingWindow, "refresh_all", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "update_startup_recovery_controls", lambda _self: None), \
                patch.object(AutoTradeSettingWindow, "current_runtime_file_signature", lambda _self: tuple()):
            window = AutoTradeSettingWindow()
        self.addCleanup(window.close)
        window.show()
        self._app.processEvents()

        header = window.stock_table.horizontalHeader()
        column_width_sum = sum(header.sectionSize(col) for col in range(window.stock_table.columnCount()))
        initial_column_width_sum = sum(
            header.sectionSize(col)
            for col in range(setting_window.AUTO_TRADE_SETTING_INITIAL_STOCK_LAST_COLUMN + 1)
        )
        initial_stock_width = window._stock_table_required_width(
            setting_window.AUTO_TRADE_SETTING_INITIAL_STOCK_LAST_COLUMN
        )
        initial_right_width = window._right_workspace_initial_width()
        stock_required_width = window._stock_table_required_width()
        right_required_width = window._right_workspace_required_width()
        self.assertGreaterEqual(initial_stock_width, initial_column_width_sum)
        self.assertLess(initial_stock_width, stock_required_width)
        self.assertGreaterEqual(initial_right_width, initial_stock_width)
        self.assertLess(initial_right_width, right_required_width)
        self.assertGreaterEqual(stock_required_width, column_width_sum)
        self.assertGreaterEqual(right_required_width, stock_required_width)
        initial_left_width, initial_splitter_right_width = window.strategy_workspace_splitter.sizes()
        self.assertGreaterEqual(initial_left_width, window.routine_box.minimumWidth())
        self.assertGreaterEqual(initial_splitter_right_width, initial_right_width)
        self.assertLess(initial_splitter_right_width, right_required_width)

        handle_width = window.strategy_workspace_splitter.handleWidth()
        available_width = window.routine_box.minimumWidth() + right_required_width + handle_width + 120
        window.strategy_workspace_splitter.resize(available_width, 420)
        window._rebalance_strategy_workspace_splitter()
        self._app.processEvents()

        left_width, right_width = window.strategy_workspace_splitter.sizes()
        self.assertGreaterEqual(left_width, window.routine_box.minimumWidth())
        self.assertGreaterEqual(right_width, right_required_width)
        self.assertEqual(setting_window.Qt.ScrollBarAlwaysOff, window.stock_table.horizontalScrollBarPolicy())
        self.assertEqual(setting_window.QHeaderView.Fixed, header.sectionResizeMode(0))

    def test_routine_tree_does_not_render_default_operation_stamp_buttons(self) -> None:
        instances = [self._instance("inst-a", "A 인스턴스"), self._instance("inst-b", "B 인스턴스")]
        window = self._window_harness()
        counts = {
            "inst-a": {"registered": 1, "running": 0, "error": 0},
            "inst-b": {"registered": 1, "running": 0, "error": 0},
        }
        window._routine_instance_operation_counts = lambda: counts
        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances):
            window.load_routine_table()
            window.routine_table.selectRow(0)
            parent_stamp = window.routine_table.cellWidget(0, 0).findChild(
                setting_window.QPushButton,
                "autoTradeSettingDefaultOperationStamp",
            )
            first_stamp = window.routine_table.cellWidget(1, 0).findChild(
                setting_window.QPushButton,
                "autoTradeSettingDefaultOperationStamp",
            )
            second_stamp = window.routine_table.cellWidget(2, 0).findChild(
                setting_window.QPushButton,
                "autoTradeSettingDefaultOperationStamp",
            )

            self.assertIsNone(parent_stamp)
            self.assertIsNone(first_stamp)
            self.assertIsNone(second_stamp)
            self.assertEqual(0, window.routine_table.selectionModel().selectedRows()[0].row())


if __name__ == "__main__":
    unittest.main()
