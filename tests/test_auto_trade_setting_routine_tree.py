import unittest
from pathlib import Path
from types import MethodType
from unittest.mock import patch

from PyQt5.QtCore import QObject
from PyQt5.QtWidgets import QApplication, QAbstractItemView, QTableWidget

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
        harness.routine_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        harness.routine_table.setSelectionMode(QAbstractItemView.SingleSelection)
        harness._collapsed_auto_trade_definition_ids = set()
        harness._default_operation_instance_by_definition = {}
        harness._routine_operation_status_by_instance = {}
        for name in (
            "_setup_routine_table",
            "_routine_instance_stock_counts",
            "_routine_instance_operation_counts",
            "_is_default_operation_instance",
            "_routine_status_text_for_metadata",
            "set_default_operation_instance_from_metadata",
            "_refresh_default_operation_stamps",
            "_routine_tree_row_widget",
            "_set_routine_tree_parent_summary_visible",
            "eventFilter",
            "load_routine_table",
            "current_selected_routine_row_metadata",
            "current_selected_definition_id",
            "current_selected_instance_id",
            "current_selected_instance_dir",
            "current_selected_target_instance_ids",
            "current_selected_routine_label_text",
            "current_selected_routine_name",
            "current_selected_routine_dir",
            "restore_routine_selection",
            "restore_routine_selection_metadata",
            "on_routine_table_item_clicked",
            "auto_trade_runtime_state_for_order",
            "update_selection_summary_panel",
        ):
            setattr(harness, name, MethodType(getattr(AutoTradeSettingWindow, name), harness))
        return harness

    def test_top_table_uses_definition_instance_rows_without_stock_nodes(self) -> None:
        instances = [self._instance("inst-a", "A 인스턴스"), self._instance("inst-b", "B 인스턴스")]
        counts = {
            "inst-a": {"registered": 1, "running": 1, "stopped": 0, "error": 0},
            "inst-b": {"registered": 2, "running": 0, "stopped": 2, "error": 1},
        }
        window = self._window_harness()
        window._routine_instance_operation_counts = lambda: counts
        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances):
            window.load_routine_table()

        self.assertEqual(3, window.routine_table.rowCount())
        self.assertEqual(1, window.routine_table.columnCount())
        parent_meta = window.routine_table.item(0, 0).data(setting_window.Qt.UserRole)
        child_a_meta = window.routine_table.item(1, 0).data(setting_window.Qt.UserRole)
        child_b_meta = window.routine_table.item(2, 0).data(setting_window.Qt.UserRole)
        self.assertEqual("definition", parent_meta["row_kind"])
        self.assertEqual("instance", child_a_meta["row_kind"])
        self.assertEqual("instance", child_b_meta["row_kind"])
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
        parent_registered = parent_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeRegistered")
        parent_running = parent_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeRunning")
        parent_stopped = parent_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeStopped")
        parent_error = parent_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeError")
        parent_stamp = parent_widget.findChild(setting_window.QPushButton, "autoTradeSettingDefaultOperationStamp")
        parent_instance_count = parent_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeInstanceCount")
        parent_meta_group = parent_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreeMetaGroup")
        parent_status_group = parent_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreeStatusGroup")
        parent_widget.resize(900, parent_widget.sizeHint().height())
        parent_widget.show()
        self._app.processEvents()
        self.assertIsNotNone(parent_title)
        self.assertIsNotNone(parent_icon)
        self.assertIsNone(parent_stamp)
        self.assertIsNotNone(parent_instance_count)
        self.assertIsNotNone(parent_meta_group)
        self.assertIsNotNone(parent_status_group)
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
        self.assertGreater(parent_icon.font().pointSize(), parent_title.font().pointSize())
        self.assertTrue(parent_title.font().bold())
        self.assertEqual("종목(3)", parent_registered.text())
        self.assertEqual("실행(1)", parent_running.text())
        self.assertEqual("정지(2)", parent_stopped.text())
        self.assertEqual("검토(1)", parent_error.text())
        self.assertFalse(parent_instance_count.isHidden())
        self.assertTrue(parent_registered.isHidden())
        self.assertTrue(parent_running.isHidden())
        self.assertTrue(parent_stopped.isHidden())
        self.assertTrue(parent_error.isHidden())
        status_slot_width = max(
            parent_registered.fontMetrics().horizontalAdvance(text)
            for text in ("종목(999)", "실행(999)", "정지(999)", "검토(999)")
        ) + 8
        parent_status_slot_width = int(status_slot_width * 0.80)
        self.assertEqual(parent_status_slot_width, parent_registered.width())
        self.assertEqual(parent_status_slot_width, parent_running.width())
        self.assertEqual(parent_status_slot_width, parent_stopped.width())
        self.assertEqual(parent_status_slot_width, parent_error.width())
        self.assertEqual(
            parent_status_group.mapTo(parent_widget, parent_status_group.rect().topLeft()).x(),
            parent_registered.mapTo(parent_widget, parent_registered.rect().topLeft()).x(),
        )
        parent_columns = [
            parent_registered,
            parent_running,
            parent_stopped,
            parent_error,
        ]
        self.assertEqual(1, len({label.width() for label in parent_columns}))
        window._set_routine_tree_parent_summary_visible(parent_widget, True)
        self.assertFalse(parent_registered.isHidden())
        self.assertFalse(parent_running.isHidden())
        self.assertFalse(parent_stopped.isHidden())
        self.assertFalse(parent_error.isHidden())
        self.assertGreaterEqual(
            parent_registered.width(),
            parent_registered.fontMetrics().horizontalAdvance(parent_registered.text()),
        )
        self.assertGreaterEqual(
            parent_running.width(),
            parent_running.fontMetrics().horizontalAdvance(parent_running.text()),
        )
        self.assertGreaterEqual(
            parent_stopped.width(),
            parent_stopped.fontMetrics().horizontalAdvance(parent_stopped.text()),
        )
        self.assertGreaterEqual(
            parent_error.width(),
            parent_error.fontMetrics().horizontalAdvance(parent_error.text()),
        )
        self.assertGreaterEqual(window.routine_table.item(0, 0).sizeHint().height(), parent_widget.sizeHint().height())
        child_widget = window.routine_table.cellWidget(1, 0)
        child_title = child_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeTitle")
        child_indent = child_widget.findChild(setting_window.QWidget, "autoTradeSettingRoutineTreeIndent")
        stamp = child_widget.findChild(setting_window.QPushButton, "autoTradeSettingDefaultOperationStamp")
        child_registered = child_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeRegistered")
        child_default_slot = child_widget.findChild(setting_window.QWidget, "autoTradeSettingDefaultOperationSlot")
        child_instance_count = child_widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeInstanceCount")
        self.assertIsNotNone(child_title)
        self.assertIsNotNone(child_indent)
        self.assertIsNone(stamp)
        self.assertIsNone(child_default_slot)
        self.assertIsNone(child_instance_count)
        self.assertEqual(28, child_indent.width())
        self.assertEqual(setting_window.routine_tree_title_width(child_title.fontMetrics()), child_title.width())
        self.assertEqual(child_title.width(), child_title.minimumWidth())
        self.assertEqual(child_title.width(), child_title.maximumWidth())
        self.assertEqual(setting_window.QSizePolicy.Fixed, child_title.sizePolicy().horizontalPolicy())
        self.assertEqual(setting_window.Qt.AlignCenter | setting_window.Qt.AlignVCenter, child_title.alignment())
        self.assertEqual(parent_status_slot_width, child_registered.width())
        self.assertFalse(child_title.font().bold())
        self.assertGreaterEqual(
            child_title.mapTo(child_widget, child_title.rect().topLeft()).x()
            - parent_title.mapTo(parent_widget, parent_title.rect().topLeft()).x(),
            20,
        )
        self.assertGreater(parent_title.font().pointSize(), child_title.font().pointSize())
        self.assertEqual(setting_window.QFont.DemiBold, parent_title.font().weight())
        self.assertLess(parent_title.font().weight(), setting_window.QFont.Bold)
        self.assertGreaterEqual(
            child_registered.width(),
            child_registered.fontMetrics().horizontalAdvance(child_registered.text()),
        )
        self.assertEqual("A 인스턴스", child_title.text())
        self.assertNotIn("기본운영", child_title.text())
        self.assertFalse(child_title.text().startswith(" "))
        self.assertEqual(2, window.routine_table.rowHeight(0) - window.routine_table.rowHeight(1))
        self.assertLessEqual(window.routine_table.rowHeight(0), 40)

    def test_routine_tree_hides_table_header_and_grid(self) -> None:
        window = self._window_harness()
        window._setup_routine_table()

        self.assertEqual(1, window.routine_table.columnCount())
        self.assertTrue(window.routine_table.horizontalHeader().isHidden())
        self.assertTrue(window.routine_table.verticalHeader().isHidden())
        self.assertFalse(window.routine_table.showGrid())
        self.assertIn("selection-background-color: #dbeafe", window.routine_table.styleSheet())
        self.assertIn("selection-color: #111827", window.routine_table.styleSheet())

    def test_parent_summary_labels_use_compressed_equal_slots(self) -> None:
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
        labels = []
        for object_name, expected in (
            ("autoTradeSettingRoutineTreeRegistered", "종목(12)"),
            ("autoTradeSettingRoutineTreeRunning", "실행(12)"),
            ("autoTradeSettingRoutineTreeStopped", "정지(8)"),
            ("autoTradeSettingRoutineTreeError", "검토(0)"),
        ):
            label = widget.findChild(setting_window.QLabel, object_name)
            labels.append(label)
            self.assertEqual(expected, label.text())
            self.assertGreaterEqual(
                label.width(),
                label.fontMetrics().horizontalAdvance(label.text()),
            )
            self.assertNotIn("(00", label.text())
        self.assertEqual(1, len({label.width() for label in labels}))
        self.assertLess(labels[0].x(), labels[1].x())
        self.assertLess(labels[1].x(), labels[2].x())
        self.assertLess(labels[2].x(), labels[3].x())

    def test_parent_title_uses_fixed_six_character_slot_and_fixed_columns(self) -> None:
        window = self._window_harness()
        samples = [
            ("단기", "단기"),
            ("단기매매", "단기매매"),
            ("지표추종매매", "지표추종매매"),
            ("지표추종매매A", "지표추종매매A"),
            ("지표추종매매BC", "지표추종매매…"),
            ("아주긴자동매매루틴", "아주긴자동매…"),
            ("123456", "123456"),
            ("1234567", "1234567"),
            ("12345678", "123456…"),
        ]
        badge_x_values = set()
        registered_x_values = set()
        running_x_values = set()
        stopped_x_values = set()
        error_x_values = set()
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
            registered = widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeRegistered")
            running = widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeRunning")
            stopped = widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeStopped")
            error = widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeError")

            self.assertEqual(expected_title, title.text())
            self.assertEqual(
                setting_window.routine_tree_title_width(title.fontMetrics()),
                title.width(),
            )
            required_width = max(
                max(title.fontMetrics().horizontalAdvance(sample), title.fontMetrics().boundingRect(sample).width())
                for sample in ("가" * 7, "가" * 6 + "…", "1234567", "123456…")
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
            registered_x = registered.mapTo(widget, registered.rect().topLeft()).x()
            badge_x_values.add(badge_x)
            registered_x_values.add(registered_x)
            running_x_values.add(running.mapTo(widget, running.rect().topLeft()).x())
            stopped_x_values.add(stopped.mapTo(widget, stopped.rect().topLeft()).x())
            error_x_values.add(error.mapTo(widget, error.rect().topLeft()).x())

        self.assertEqual(1, len(title_widths))
        self.assertEqual(1, len(badge_x_values))
        self.assertEqual(1, len(registered_x_values))
        self.assertEqual(1, len(running_x_values))
        self.assertEqual(1, len(stopped_x_values))
        self.assertEqual(1, len(error_x_values))

    def test_child_title_uses_fixed_six_character_slot_and_fixed_status_columns(self) -> None:
        window = self._window_harness()
        samples = [
            ("두자", "두자"),
            ("동전주", "동전주"),
            ("네글자명", "네글자명"),
            ("지표추종매매", "지표추종매매"),
            ("지표추종매매B", "지표추종매매B"),
            ("지표추종매매BC", "지표추종매매…"),
            ("아주긴자동매매루틴", "아주긴자동매…"),
            ("123456", "123456"),
            ("1234567", "1234567"),
            ("12345678", "123456…"),
        ]
        title_x_values = set()
        title_widths = set()
        registered_x_values = set()
        running_x_values = set()
        stopped_x_values = set()
        error_x_values = set()

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
            widget.resize(900, widget.sizeHint().height())
            widget.show()
            self._app.processEvents()

            title = widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeTitle")
            registered = widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeRegistered")
            running = widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeRunning")
            stopped = widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeStopped")
            error = widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeError")

            self.assertEqual(expected_title, title.text())
            self.assertEqual(
                setting_window.routine_tree_title_width(title.fontMetrics()),
                title.width(),
            )
            required_width = max(
                max(title.fontMetrics().horizontalAdvance(sample), title.fontMetrics().boundingRect(sample).width())
                for sample in ("가" * 7, "가" * 6 + "…", "1234567", "123456…")
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
            title_x = title.mapTo(widget, title.rect().topLeft()).x()
            registered_x = registered.mapTo(widget, registered.rect().topLeft()).x()
            registered_x_values.add(registered_x)
            running_x_values.add(running.mapTo(widget, running.rect().topLeft()).x())
            stopped_x_values.add(stopped.mapTo(widget, stopped.rect().topLeft()).x())
            error_x_values.add(error.mapTo(widget, error.rect().topLeft()).x())

        self.assertEqual(1, len(title_x_values))
        self.assertEqual(1, len(title_widths))
        self.assertEqual(1, len(registered_x_values))
        self.assertEqual(1, len(running_x_values))
        self.assertEqual(1, len(stopped_x_values))
        self.assertEqual(1, len(error_x_values))

    def test_routine_tree_title_text_contract(self) -> None:
        samples = [
            ("가", "가"),
            ("동전주", "동전주"),
            ("단기매매", "단기매매"),
            ("지표추종매매", "지표추종매매"),
            ("지표추종매매B", "지표추종매매B"),
            ("지표추종매매BC", "지표추종매매…"),
            ("ABCDEFGHI", "ABCDEF…"),
            ("123456", "123456"),
            ("1234567", "1234567"),
            ("12345678", "123456…"),
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
        labels = [
            widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeRegistered"),
            widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeRunning"),
            widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeStopped"),
            widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeError"),
        ]

        count_badge = widget.findChild(setting_window.QLabel, "autoTradeSettingRoutineTreeInstanceCount")
        self.assertEqual("루틴3", count_badge.text())
        self.assertFalse(count_badge.isHidden())
        self.assertEqual(["종목(12)", "실행(4)", "정지(8)", "검토(0)"], [label.text() for label in labels])
        self.assertTrue(all(not label.isHidden() for label in labels))

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

    def test_selection_summary_panel_reflects_definition_and_instance_scope(self) -> None:
        instances = [self._instance("inst-a", "A 인스턴스")]
        window = self._window_harness()
        window.summary_routine_value = setting_window.QLabel("-")
        window.summary_instance_value = setting_window.QLabel("-")
        window.summary_registered_value = setting_window.QLabel("-")
        window.summary_running_value = setting_window.QLabel("-")
        window.summary_error_value = setting_window.QLabel("-")
        window.summary_default_operation_value = setting_window.QLabel("-")
        counts = {"inst-a": {"registered": 7, "running": 3, "error": 1}}
        window._routine_instance_operation_counts = lambda: counts

        with patch.object(setting_window, "load_routine_definitions", return_value=[self._definition()]), \
                patch.object(setting_window, "load_persisted_routine_instances", return_value=instances):
            window.load_routine_table()
            window.routine_table.selectRow(0)
            window.update_selection_summary_panel()
            self.assertEqual("지표추종매매", window.summary_routine_value.text())
            self.assertEqual("전체 인스턴스", window.summary_instance_value.text())
            self.assertEqual("7", window.summary_registered_value.text())
            self.assertEqual("3", window.summary_running_value.text())
            self.assertEqual("1", window.summary_error_value.text())
            self.assertEqual("OFF", window.summary_default_operation_value.text())

            window.routine_table.selectRow(1)
            window.update_selection_summary_panel()
            self.assertEqual("A 인스턴스", window.summary_instance_value.text())
            self.assertEqual("7", window.summary_registered_value.text())

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
