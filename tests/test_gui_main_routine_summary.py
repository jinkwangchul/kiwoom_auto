# -*- coding: utf-8 -*-
from __future__ import annotations

import inspect
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFontMetrics
from PyQt5.QtWidgets import QApplication, QHBoxLayout, QStyle, QTableWidget, QWidget

import gui_main_table_loader as table_loader
import gui_windows
from gui_auto_trade_display import profit_loss_value_color
from gui_windows import MainWindow


class MainRoutineSummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_summary_uses_unfiltered_registered_counts_and_current_running_mode(self) -> None:
        projection = table_loader._main_routine_summary_projection(
            [SimpleNamespace(), SimpleNamespace()],
            [SimpleNamespace(), SimpleNamespace(), SimpleNamespace()],
            {
                "instance-a": {
                    "registered": 4,
                    "operation_running": 2,
                    "operation_or_stopped": 2,
                    "excluded": 1,
                    "review": 1,
                    "profit_amount": 100,
                    "profit_cost_basis": 1000,
                },
                "instance-b": {
                    "registered": 3,
                    "operation_running": 0,
                    "operation_or_stopped": 1,
                    "excluded": 1,
                    "review": 1,
                    "profit_amount": -40,
                    "profit_cost_basis": 100,
                },
            },
        )

        self.assertEqual(
            "그룹(2)  루틴(3)  종목(7)  운영(2)  제외(2)  검토(2)",
            projection["counts_text"],
        )
        self.assertEqual("수익(+60 / +5.45%)", projection["profit_text"])
        self.assertEqual(
            (
                ("group", "그룹", 2),
                ("routine", "루틴", 3),
                ("stock", "종목", 7),
                ("operation", "운영", 2),
                ("excluded", "제외", 2),
                ("review", "검토", 2),
            ),
            projection["count_badges"],
        )
        self.assertEqual("+60 / +5.45%", projection["profit_value_text"])
        self.assertEqual(profit_loss_value_color(60), projection["profit_color"])

    def test_summary_uses_stopped_count_when_no_current_running_target_exists(self) -> None:
        projection = table_loader._main_routine_summary_projection(
            [SimpleNamespace()],
            [SimpleNamespace()],
            {
                "instance-a": {
                    "registered": 5,
                    "operation_running": 0,
                    "operation_or_stopped": 3,
                    "excluded": 1,
                    "review": 1,
                }
            },
        )
        self.assertIn("정지(3)", projection["counts_text"])
        self.assertEqual("수익(0 / 0.00%)", projection["profit_text"])

    def test_instance_counts_reuse_common_current_running_targets(self) -> None:
        instance = SimpleNamespace(instance_id="instance-a")
        root = Path(table_loader.__file__).resolve().parent
        running_dir = root / "stocks" / "000001_Running"

        def read_json(path):
            if Path(path).name == "config.json":
                return {"assigned_routine_instance_id": instance.instance_id}
            return {"status": "RUNNING", "trade_enabled": True}

        with (
            patch.object(table_loader, "load_persisted_routine_instances", return_value=[instance]),
            patch.object(
                table_loader,
                "read_base_stocks",
                return_value=[
                    {
                        "code": "000001",
                        "name": "Running",
                        "stock_path": "stocks/000001_Running",
                    },
                    {
                        "code": "000002",
                        "name": "Stale",
                        "stock_path": "stocks/000002_Stale",
                    },
                ],
            ),
            patch.object(table_loader, "read_json_dict", side_effect=read_json),
            patch.object(
                table_loader,
                "auto_trade_running_registered_operation_targets",
                return_value=[(running_dir, "000001", "Running")],
            ) as current_running,
        ):
            counts = table_loader._instance_stock_counts(
                window=SimpleNamespace(),
            )

        current_running.assert_called_once()
        self.assertEqual(1, counts[instance.instance_id]["operation_running"])
        self.assertEqual(2, counts[instance.instance_id]["operation_or_stopped"])

    def test_batch_snapshot_aggregates_profit_and_cost_once(self) -> None:
        counts = {
            "instance-a": {
                "pnl_stock_codes": ["000001", "000002", "000003"],
                "profit_amount": 999,
                "profit_cost_basis": 999,
                "profit_unknown": False,
            }
        }
        results = {
            "000001": {
                "available": True,
                "cumulative_profit": 100,
                "completed_buy_cost": 600,
                "open_cost": 400,
            },
            "000002": {
                "available": True,
                "cumulative_profit": -40,
                "completed_buy_cost": 100,
                "open_cost": 0,
            },
            "000003": {"available": False, "reason": "EVALUATION_PRICE_UNAVAILABLE"},
        }
        with patch.object(
            table_loader,
            "project_current_stock_pnl_snapshot",
            return_value=results,
        ) as batch:
            returned = table_loader._refresh_instance_pnl_from_batch(counts)

        batch.assert_called_once()
        self.assertEqual(["000001", "000002", "000003"], batch.call_args.args[0])
        self.assertIs(results, returned)
        self.assertEqual(60, counts["instance-a"]["profit_amount"])
        self.assertEqual(1100, counts["instance-a"]["profit_cost_basis"])
        self.assertTrue(counts["instance-a"]["profit_unknown"])
        projection = table_loader._main_routine_summary_projection(
            [SimpleNamespace()],
            [SimpleNamespace()],
            counts,
        )
        self.assertEqual("수익(+60 / +5.45%)", projection["profit_text"])

    def test_one_second_refresh_updates_operation_summary_without_table_rebuild(self) -> None:
        class EmptyTable:
            @staticmethod
            def rowCount():
                return 0

        updater = MagicMock()
        window = SimpleNamespace(
            routine_table=EmptyTable(),
            _update_main_routine_summary=updater,
        )
        stopped = {
            "instance-a": {
                "registered": 2,
                "operation_running": 0,
                "operation_or_stopped": 2,
                "excluded": 0,
                "review": 0,
                "pnl_stock_codes": [],
            }
        }
        running = {
            "instance-a": {
                "registered": 2,
                "operation_running": 1,
                "operation_or_stopped": 2,
                "excluded": 0,
                "review": 0,
                "pnl_stock_codes": [],
            }
        }
        with (
            patch.object(table_loader, "_instance_stock_counts", side_effect=[stopped, running]),
            patch.object(table_loader, "_refresh_instance_pnl_from_batch", return_value={}),
            patch.object(table_loader, "load_routine_definitions", return_value=[SimpleNamespace()]),
            patch.object(table_loader, "load_persisted_routine_instances", return_value=[SimpleNamespace()]),
        ):
            table_loader.main_refresh_pnl_only(window)
            table_loader.main_refresh_pnl_only(window)

        first, second = (call.args[0] for call in updater.call_args_list)
        self.assertIn("정지(2)", first["counts_text"])
        self.assertIn("운영(1)", second["counts_text"])

    def test_visible_summary_updates_without_right_excluded_badge(self) -> None:
        routine_table = QTableWidget()
        host = SimpleNamespace(
            _set_main_routine_excluded_only=lambda: None,
            _set_main_routine_valid_only=lambda _enabled: None,
            _main_routine_valid_only=True,
            routine_table=routine_table,
        )
        base_font = QWidget().font()
        summary = MainWindow._create_main_routine_summary(host)
        container = QWidget()
        header = QHBoxLayout(container)
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(summary)
        header.addStretch(1)
        try:
            first = {
                "counts_text": "그룹(1)  루틴(2)  종목(3)  정지(2)  제외(1)  검토(0)",
                "profit_text": "수익(0 / 0.00%)",
                "count_badges": (
                    ("group", "그룹", 1),
                    ("routine", "루틴", 2),
                    ("stock", "종목", 16),
                    ("operation", "정지", 2),
                    ("excluded", "제외", 1),
                    ("review", "검토", 0),
                ),
                "profit_value_text": "0 / 0.00%",
                "profit_color": profit_loss_value_color(0),
            }
            second = {
                "counts_text": "그룹(1)  루틴(2)  종목(3)  운영(1)  제외(1)  검토(0)",
                "profit_text": "수익(+12,500 / +1.25%)",
                "count_badges": (
                    ("group", "그룹", 125),
                    ("routine", "루틴", 2),
                    ("stock", "종목", 16),
                    ("operation", "운영", 1),
                    ("excluded", "제외", 0),
                    ("review", "검토", 4),
                ),
                "profit_value_text": "+12,500 / +1.25%",
                "profit_color": profit_loss_value_color(12500),
            }
            MainWindow._update_main_routine_summary(host, first)
            MainWindow._update_main_routine_summary(host, second)

            count_labels = host._main_routine_summary_count_labels
            self.assertEqual("125", count_labels["group"][1].text())
            self.assertEqual("2", count_labels["routine"][1].text())
            self.assertEqual("16", count_labels["stock"][1].text())
            self.assertEqual("운영", count_labels["operation"][0].text())
            self.assertEqual("1", count_labels["operation"][1].text())
            self.assertEqual("0", count_labels["excluded"][1].text())
            self.assertNotEqual("002", count_labels["routine"][1].text())
            self.assertFalse(hasattr(host, "_main_routine_summary_profit_label"))
            self.assertIsNone(
                summary.findChild(QWidget, "mainRoutineSummaryProfit")
            )
            self.assertIsNone(
                summary.findChild(QWidget, "mainRoutineSummaryProfitSeparator")
            )
            all_labels = []
            for label, value in count_labels.values():
                all_labels.extend((label, value))
            for label in all_labels:
                self.assertTrue(label.font().bold())
                if base_font.pointSizeF() > 0:
                    self.assertAlmostEqual(
                        base_font.pointSizeF() * 1.3,
                        label.font().pointSizeF(),
                        places=3,
                    )
                else:
                    self.assertEqual(
                        round(base_font.pixelSize() * 1.3),
                        label.font().pixelSize(),
                    )
            expected_text_margin = (
                routine_table.viewport().geometry().x()
                +
                routine_table.style().pixelMetric(
                    QStyle.PM_FocusFrameHMargin,
                    None,
                    routine_table,
                )
                + 1
            )
            expected_valid_left_inset = (
                gui_windows.MAIN_ROUTINE_FILTER_BADGE_AREA_WIDTH
                - gui_windows.MAIN_ROUTINE_FILTER_BADGE_WIDTH
            ) // 2
            self.assertEqual(
                expected_valid_left_inset,
                summary.layout().contentsMargins().left(),
            )
            container.resize(860, summary.sizeHint().height())
            container.show()
            self.app.processEvents()
            count_badges = [label.parentWidget() for label, _value in count_labels.values()]
            self.assertEqual(1, len({badge.width() for badge in count_badges}))
            expected_border = gui_windows.AUTO_TRADE_SETTING_BADGE_INACTIVE_COLOR.lower()
            self.assertTrue(
                all(expected_border in badge.styleSheet().lower() for badge in count_badges)
            )
            self.assertEqual(
                host._main_routine_summary_count_badge_width,
                count_badges[0].width(),
            )
            self.assertEqual(
                {host._main_routine_summary_number_slot_width},
                {value.width() for _label, value in count_labels.values()},
            )
            self.assertTrue(all(badge.isCheckable() for badge in count_badges))
            self.assertTrue(all(badge.focusPolicy() == Qt.NoFocus for badge in count_badges))
            group_label = count_labels["group"][0]
            valid_badge = host._main_routine_valid_button
            valid_separator = host._main_routine_summary_valid_separator
            review_badge = count_badges[-1]
            valid_x = valid_badge.mapTo(summary, valid_badge.rect().topLeft()).x()
            separator_x = valid_separator.mapTo(
                summary,
                valid_separator.rect().topLeft(),
            ).x()
            badge_x = count_badges[0].mapTo(summary, count_badges[0].rect().topLeft()).x()
            review_x = review_badge.mapTo(summary, review_badge.rect().topLeft()).x()
            group_text_x = group_label.mapTo(summary, group_label.rect().topLeft()).x()
            self.assertEqual(expected_valid_left_inset, valid_x)
            self.assertEqual(
                valid_x + valid_badge.width() + summary.layout().spacing(),
                separator_x,
            )
            self.assertEqual(
                separator_x + valid_separator.width() + summary.layout().spacing(),
                badge_x,
            )
            self.assertEqual(badge_x + expected_text_margin, group_text_x)
            self.assertEqual("|", valid_separator.text())
            self.assertFalse(valid_separator.font().bold())
            self.assertEqual(valid_badge.height(), valid_separator.height())
            self.assertTrue(
                valid_separator.testAttribute(Qt.WA_TransparentForMouseEvents)
            )
            self.assertEqual(
                summary.layout().contentsRect().right(),
                review_x + review_badge.width() - 1,
            )
            self.assertEqual(gui_windows.MAIN_ROUTINE_SUMMARY_VALID_BADGE_WIDTH, valid_badge.width())
            self.assertEqual(count_badges[0].height(), valid_badge.height())
            self.assertTrue(valid_badge.font().bold())
            source = inspect.getsource(MainWindow._create_table_area)
            self.assertLess(
                source.index("addWidget(self._create_main_routine_summary())"),
                source.index("addStretch(1)"),
            )
            self.assertNotIn("mainRoutineHeaderAlignment", source)
            self.assertIn(
                "routine_header_layout.setSpacing(routine_content_layout.spacing())",
                source,
            )
            self.assertNotIn("_create_main_routine_excluded_badge", source)
        finally:
            container.close()
            summary.close()
            routine_table.close()

    def test_actual_main_header_summary_aligns_with_visible_routine_table(self) -> None:
        api = SimpleNamespace(
            unavailable_reason=lambda: "test double",
            login_state_changed=None,
            raw_chejan_received=None,
        )
        with (
            patch.object(gui_windows, "KiwoomApi", return_value=api),
            patch.object(gui_windows, "normalize_base_stock_single_routine_file"),
            patch.object(gui_windows.MainWindow, "refresh_startup_recovery_status", return_value={}),
            patch.object(gui_windows.MainWindow, "refresh_all"),
        ):
            window = gui_windows.MainWindow()
        try:
            window.resize(1280, 720)
            window.show()
            self.app.processEvents()

            summary = window.findChild(QWidget, "mainRoutineSummary")
            filter_area = window.findChild(QWidget, "mainRoutineFilterBadgeArea")
            valid_badge = window._main_routine_valid_button
            valid_separator = window._main_routine_summary_valid_separator
            group_label = window._main_routine_summary_count_labels["group"][0]
            group_badge = group_label.parentWidget()
            table = window.routine_table
            summary_x = summary.mapTo(window, summary.rect().topLeft()).x()
            filter_area_x = filter_area.mapTo(window, filter_area.rect().topLeft()).x()
            valid_x = valid_badge.mapTo(window, valid_badge.rect().topLeft()).x()
            separator_x = valid_separator.mapTo(
                window,
                valid_separator.rect().topLeft(),
            ).x()
            table_x = table.mapTo(window, table.rect().topLeft()).x()
            badge_x = group_badge.mapTo(window, group_badge.rect().topLeft()).x()
            group_text_x = group_label.mapTo(window, group_label.rect().topLeft()).x()
            expected_text_x = (
                table_x
                + table.viewport().geometry().x()
                + table.style().pixelMetric(
                    QStyle.PM_FocusFrameHMargin,
                    None,
                    table,
                )
                + 1
            )

            self.assertEqual(filter_area_x, summary_x)
            self.assertIs(
                group_badge,
                window._main_routine_level_buttons["group"],
            )
            self.assertEqual(
                valid_x + valid_badge.width() + summary.layout().spacing(),
                separator_x,
            )
            self.assertEqual(
                separator_x + valid_separator.width() + summary.layout().spacing(),
                badge_x,
            )
            self.assertIs(summary, valid_badge.parentWidget())
            left_separator = window.findChild(QWidget, "mainRoutineValidSeparator")
            self.assertIsNotNone(left_separator)
            self.assertIs(filter_area, left_separator.parentWidget())
            self.assertIsNone(window.findChild(QWidget, "mainRoutineMetricSeparator"))
            self.assertGreater(group_text_x, expected_text_x)
            self.assertEqual("|", valid_separator.text())
            self.assertFalse(valid_separator.font().bold())
            self.assertEqual(valid_badge.height(), valid_separator.height())
            self.assertIsNone(
                window.findChild(QWidget, "mainRoutineSummaryProfit")
            )
            self.assertIsNone(
                window.findChild(QWidget, "mainRoutineSummaryProfitSeparator")
            )
            self.assertEqual(
                valid_badge.mapTo(window, valid_badge.rect().center()).y(),
                valid_separator.mapTo(window, valid_separator.rect().center()).y(),
            )
            self.assertAlmostEqual(
                QApplication.font().pointSizeF() * 1.3,
                group_label.font().pointSizeF(),
                places=3,
            )
            self.assertTrue(group_label.font().bold())
            self.assertEqual(group_label.font(), valid_badge.font())
            badges = [valid_badge, *window._main_routine_summary_count_buttons.values()]
            content_font_height = QFontMetrics(group_label.font()).height()
            expected_badge_height = max(
                gui_windows.AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT,
                content_font_height + 3 * 2 + 1 * 2,
            )
            self.assertEqual(expected_badge_height, badges[0].height())
            self.assertEqual(badges[0].height() + 4, summary.height())
            for badge in badges:
                with self.subTest(object_name=badge.objectName()):
                    self.assertEqual(expected_badge_height, badge.height())
                    self.assertEqual(badge.rect(), badge.visibleRegion().boundingRect())
                    badge_top = badge.mapTo(summary, badge.rect().topLeft()).y()
                    badge_bottom = badge.mapTo(summary, badge.rect().bottomRight()).y()
                    self.assertGreaterEqual(badge_top, summary.rect().top())
                    self.assertLessEqual(badge_bottom, summary.rect().bottom())

            for label, value in window._main_routine_summary_count_labels.values():
                with self.subTest(label=label.objectName()):
                    self.assertEqual(
                        label.parentWidget().rect().center().y(),
                        label.mapTo(label.parentWidget(), label.rect().center()).y(),
                    )
                    self.assertEqual(
                        value.parentWidget().rect().center().y(),
                        value.mapTo(value.parentWidget(), value.rect().center()).y(),
                    )

            initial_valid_only = window._main_routine_valid_only
            with patch.object(window, "_reload_main_routine_table_preserving_view") as reload_table:
                valid_badge.click()
                self.assertEqual(not initial_valid_only, window._main_routine_valid_only)
                self.assertEqual(not initial_valid_only, valid_badge.isChecked())
                valid_badge.click()
                self.assertEqual(initial_valid_only, window._main_routine_valid_only)
                self.assertEqual(initial_valid_only, valid_badge.isChecked())
                self.assertEqual(2, reload_table.call_count)
        finally:
            window.close()

    def test_stock_scopes_project_all_current_stopped_excluded_and_review_rows(self) -> None:
        records = [
            {"code": "000001", "name": "Running", "stock_path": "stocks/000001_Running"},
            {"code": "000002", "name": "Stopped", "stock_path": "stocks/000002_Stopped"},
            {"code": "000003", "name": "Excluded", "stock_path": "stocks/000003_Excluded"},
            {"code": "000004", "name": "Review", "stock_path": "stocks/000004_Review"},
        ]
        configs = {
            "000001_Running": {"assigned_routine_instance_id": "instance-a"},
            "000002_Stopped": {"assigned_routine_instance_id": "instance-a"},
            "000003_Excluded": {
                "assigned_routine_instance_id": "instance-a",
                "operation_excluded": True,
            },
            "000004_Review": {
                "assigned_routine_instance_id": "instance-a",
                "operation_excluded": True,
            },
        }
        states = {
            "000001_Running": {},
            "000002_Stopped": {},
            "000003_Excluded": {},
            "000004_Review": {"review_required": True, "review_status": "PENDING"},
        }

        def read_json(path):
            values = configs if Path(path).name == "config.json" else states
            return dict(values.get(Path(path).parent.name, {}))

        running_dir = PROJECT_ROOT / records[0]["stock_path"]
        with (
            patch.object(table_loader, "read_base_stocks", return_value=records),
            patch.object(table_loader, "read_json_dict", side_effect=read_json),
            patch.object(
                table_loader,
                "load_persisted_routine_instances",
                return_value=[SimpleNamespace(instance_id="instance-a")],
            ),
            patch.object(
                table_loader,
                "auto_trade_running_registered_operation_targets",
                return_value=[(running_dir, "000001", "Running")],
            ) as running_targets,
        ):
            projected = {
                scope: table_loader._instance_stock_counts(
                    window=SimpleNamespace(),
                    stock_scope=scope,
                )["instance-a"]
                for scope in ("normal", "all", "operation", "excluded", "review")
            }

        def codes(count):
            return [stock["code"] for stock in count["stocks"]]

        self.assertEqual(["000001", "000002"], codes(projected["normal"]))
        self.assertEqual(
            ["000001", "000002", "000003", "000004"],
            codes(projected["all"]),
        )
        self.assertEqual(["000001"], codes(projected["operation"]))
        self.assertEqual(["000003"], codes(projected["excluded"]))
        self.assertEqual(["000004"], codes(projected["review"]))
        self.assertEqual(4, projected["all"]["registered"])
        self.assertEqual(1, projected["all"]["excluded"])
        self.assertEqual(1, projected["all"]["review"])
        running_targets.assert_called()

        with (
            patch.object(table_loader, "read_base_stocks", return_value=records),
            patch.object(table_loader, "read_json_dict", side_effect=read_json),
            patch.object(
                table_loader,
                "load_persisted_routine_instances",
                return_value=[SimpleNamespace(instance_id="instance-a")],
            ),
            patch.object(
                table_loader,
                "auto_trade_running_registered_operation_targets",
                return_value=[],
            ),
        ):
            stopped = table_loader._instance_stock_counts(
                window=SimpleNamespace(),
                stock_scope="operation",
            )["instance-a"]
        self.assertEqual(["000001", "000002"], codes(stopped))

    def test_review_scope_row_reuses_existing_review_protection_style(self) -> None:
        stock = {
            "code": "000004",
            "name": "Review",
            "state": {"review_required": True, "review_status": "PENDING"},
            "config": {"operation_excluded": True},
        }
        tokens = table_loader._routine_tree_stock_display_snapshots(
            SimpleNamespace(),
            stock,
            ["000004 Review", "-", "수동", "-", "검토종목", "루틴", "-"],
        )
        self.assertTrue(tokens)
        self.assertTrue(all(token["foreground"] == "#ff8c00" for token in tokens))
        self.assertTrue(all("검토관리" in token["tooltip"] for token in tokens))

    def test_summary_badges_keep_existing_display_and_scope_state_without_profit_badge(self) -> None:
        api = SimpleNamespace(
            unavailable_reason=lambda: "test double",
            login_state_changed=None,
            raw_chejan_received=None,
        )
        with (
            patch.object(gui_windows, "KiwoomApi", return_value=api),
            patch.object(gui_windows, "normalize_base_stock_single_routine_file"),
            patch.object(gui_windows.MainWindow, "refresh_startup_recovery_status", return_value={}),
            patch.object(gui_windows.MainWindow, "refresh_all"),
        ):
            window = gui_windows.MainWindow()
        try:
            reload_view = MagicMock()
            window._reload_main_routine_table_preserving_view = reload_view
            top = window._main_routine_summary_count_buttons

            top["group"].click()
            self.assertEqual("group", window._main_routine_display_level)
            self.assertTrue(top["group"].isChecked())
            self.assertIn(
                gui_windows.AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR,
                window._main_routine_level_buttons["group"].styleSheet(),
            )

            window._main_routine_level_buttons["routine"].click()
            self.assertEqual("routine", window._main_routine_display_level)
            self.assertTrue(top["routine"].isChecked())

            top["stock"].click()
            self.assertEqual("stock", window._main_routine_display_level)
            self.assertEqual("all", window._main_routine_stock_scope)
            self.assertTrue(top["stock"].isChecked())

            top["operation"].click()
            self.assertEqual("operation", window._main_routine_stock_scope)
            self.assertTrue(top["operation"].isChecked())
            self.assertFalse(top["stock"].isChecked())
            self.assertIn(
                gui_windows.AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR,
                top["operation"].styleSheet(),
            )

            top["excluded"].click()
            self.assertEqual("excluded", window._main_routine_stock_scope)
            self.assertTrue(window._main_routine_excluded_only)
            self.assertFalse(hasattr(window, "_main_routine_excluded_button"))
            self.assertTrue(top["excluded"].isChecked())
            self.assertEqual(
                ["excluded"],
                [
                    key
                    for key in ("stock", "operation", "excluded", "review")
                    if top[key].isChecked()
                ],
            )

            top["excluded"].click()
            self.assertEqual("normal", window._main_routine_stock_scope)
            self.assertFalse(window._main_routine_excluded_only)
            self.assertFalse(top["excluded"].isChecked())

            top["review"].click()
            self.assertEqual("review", window._main_routine_stock_scope)
            self.assertTrue(top["review"].isChecked())
            top["review"].click()
            self.assertEqual("normal", window._main_routine_stock_scope)

            window._main_routine_level_buttons["routine"].click()
            self.assertFalse(hasattr(window, "_main_routine_summary_profit_label"))
            self.assertIsNone(
                window.findChild(QWidget, "mainRoutineSummaryProfit")
            )
            self.assertIsNone(
                window.findChild(QWidget, "mainRoutineSummaryProfitSeparator")
            )

            self.assertGreaterEqual(reload_view.call_count, 7)
        finally:
            window.close()

    def test_main_loader_passes_the_single_scope_to_existing_stock_collector(self) -> None:
        table = QTableWidget()
        table.setColumnCount(len(table_loader.ROUTINE_MONITORING_HEADERS))
        host = SimpleNamespace(
            routine_table=table,
            _main_routine_stock_scope="review",
            _main_routine_excluded_only=False,
            _main_routine_display_level="stock",
            _main_routine_display_level_applied=True,
            _main_routine_valid_only=True,
            _main_routine_metric_sort_key="",
            _main_routine_metric_sort_active=False,
            _main_routine_initial_buy_sort_mode="",
            _main_routine_column_sort_key="",
            _main_routine_sort_column=-1,
            _main_routine_sort_order=Qt.AscendingOrder,
            _collapsed_routine_definition_ids=set(),
            _collapsed_routine_instance_ids=set(),
            _routine_assigned_stock_count_by_instance={},
            _update_main_routine_summary=MagicMock(),
        )
        collector = MagicMock(return_value={})
        try:
            with (
                patch.object(table_loader, "_instance_stock_counts", collector),
                patch.object(table_loader, "_refresh_instance_pnl_from_batch", return_value={}),
                patch.object(table_loader, "load_routine_definitions", return_value=[]),
                patch.object(table_loader, "load_persisted_routine_instances", return_value=[]),
                patch.object(table_loader, "sync_routine_selection_state"),
                patch.object(table_loader, "current_stock_trade_counts_by_code", return_value={}),
            ):
                table_loader.main_load_routine_table(host)
            collector.assert_called_once_with(window=host, stock_scope="review")
        finally:
            table.close()


if __name__ == "__main__":
    unittest.main()
