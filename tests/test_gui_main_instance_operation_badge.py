import json
import os
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QItemSelectionModel, QRect, Qt
from PyQt5.QtGui import QFontMetrics
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

import gui_auto_trade_selection
import gui_auto_trade_run_control as run_control
import gui_auto_trade_setting_window as setting_window
import gui_main_stock_context_menu as context_menu
import gui_main_table_loader as table_loader
import gui_windows


class MainInstanceOperationBadgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_review_required_button_count_uses_review_window_collector(self) -> None:
        rows = [
            {"code": "000660", "review_reason": "긴급정지 해제 시 보유잔량 존재"},
            {"code": "323410", "review_reason": "운영 데이터 불일치"},
            {"code": "035420", "review_reason": "운영 데이터 없음"},
            {"code": "051910", "review_reason": "운영 데이터 읽기 오류"},
        ]
        window = SimpleNamespace(btn_review_required=QLabel())
        window.review_required_stock_count = (
            lambda: gui_windows.MainWindow.review_required_stock_count(window)
        )

        with patch.object(
            gui_windows,
            "collect_global_review_required_rows",
            return_value=rows,
        ) as collector:
            count = gui_windows.MainWindow.review_required_stock_count(window)
            gui_windows.MainWindow.update_review_required_button_text(window)

        collector.assert_called()
        self.assertEqual(4, count)
        self.assertEqual("검토관리(4)", window.btn_review_required.text())

    def test_review_required_button_shows_zero_when_collector_empty(self) -> None:
        window = SimpleNamespace(btn_review_required=QLabel())
        window.review_required_stock_count = (
            lambda: gui_windows.MainWindow.review_required_stock_count(window)
        )

        with patch.object(
            gui_windows,
            "collect_global_review_required_rows",
            return_value=[],
        ):
            gui_windows.MainWindow.update_review_required_button_text(window)

        self.assertEqual("검토관리(0)", window.btn_review_required.text())

    def test_monitoring_bottom_button_order_starts_with_global_start(self) -> None:
        window = SimpleNamespace(
            btn_start=QPushButton("▶ 운영시작"),
            btn_auto_trade_setting=QPushButton("자동매매설정"),
            btn_close_all_windows=QPushButton("모든창닫기"),
            btn_log_view=QPushButton("이벤트기록"),
            btn_review_required=QPushButton("검토관리(0)"),
            btn_exit=QPushButton("종료"),
        )

        layout = gui_windows.MainWindow._create_button_area(window)

        self.assertEqual(
            [
                "▶ 운영시작",
                "자동매매설정",
                "이벤트기록",
                "검토관리(0)",
                "모든창닫기",
                "종료",
            ],
            [layout.itemAt(index).widget().text() for index in range(layout.count())],
        )

    def test_badge_click_selects_and_double_click_requests_action(self) -> None:
        on_click = MagicMock()
        on_double_click = MagicMock()
        widget = table_loader.create_routine_instance_status_widget(
            table_loader.ROUTINE_STATUS_RUNNING,
            registered=1,
            excluded=0,
            operation_or_stopped=1,
            review=0,
            on_status_click=on_click,
            on_status_double_click=on_double_click,
        )
        widget.show()
        self.app.processEvents()
        stamp = widget.findChild(QWidget, "routineInstanceStatusStamp")
        text = widget.findChild(QLabel, "routineInstanceStatusText")

        QTest.mouseClick(stamp, Qt.LeftButton)
        self.assertEqual(1, on_click.call_count)
        on_double_click.assert_not_called()

        QTest.mouseDClick(stamp, Qt.LeftButton)
        on_double_click.assert_called_once()
        self.assertEqual("운  영", text.text())
        self.assertEqual(table_loader.ROUTINE_STATUS_STAMP_WIDTH, stamp.width())
        self.assertEqual(table_loader.ROUTINE_STATUS_STAMP_HEIGHT, stamp.height())
        self.assertTrue(stamp.testAttribute(Qt.WA_StyledBackground))
        widget.close()

    def test_stopped_badge_has_start_tooltip_and_reuses_stock_badge_height(self) -> None:
        on_double_click = MagicMock()
        stopped_widget = table_loader.create_routine_instance_status_widget(
            table_loader.ROUTINE_STATUS_STOPPED,
            registered=1,
            excluded=0,
            operation_or_stopped=1,
            review=0,
            on_status_double_click=on_double_click,
        )
        running_widget = table_loader.create_routine_instance_status_widget(
            table_loader.ROUTINE_STATUS_RUNNING,
            registered=1,
            excluded=0,
            operation_or_stopped=1,
            review=0,
        )
        stopped_widget.show()
        running_widget.show()
        self.app.processEvents()
        stopped_stamp = stopped_widget.findChild(
            QWidget,
            "routineInstanceStatusStamp",
        )
        running_stamp = running_widget.findChild(
            QWidget,
            "routineInstanceStatusStamp",
        )

        self.assertEqual("더블클릭 | 운영시작", stopped_stamp.toolTip())
        self.assertEqual("", running_stamp.toolTip())
        stock_badge_rect = gui_windows._initial_buy_component_rects(
            QRect(0, 0, 176, table_loader.ROUTINE_STOCK_ROW_HEIGHT)
        )["badge"]
        stock_badge_outer_rect = stock_badge_rect.adjusted(0, -1, 0, 1)
        self.assertEqual(stock_badge_outer_rect.height(), stopped_stamp.height())
        self.assertEqual(stopped_stamp.height(), running_stamp.height())
        self.assertEqual(table_loader.ROUTINE_STATUS_STAMP_WIDTH, stopped_stamp.width())
        stock_badge_left = (
            table_loader.ROUTINE_STOCK_TEXT_OFFSET
            + table_loader.routine_stock_column_widths(
                table_loader.main_monitoring_table_font()
            )[0]
        )
        instance_column_left = table_loader.routine_instance_status_column_left(
            table_loader.main_monitoring_table_font()
        )
        instance_badge_left = instance_column_left + stopped_stamp.geometry().left()
        self.assertEqual(stock_badge_left, instance_badge_left)

        registered = stopped_widget.findChild(QWidget, "routineInstanceRegistered")
        self.assertEqual(
            table_loader.ROUTINE_INSTANCE_NAME_WIDTH
            + table_loader.ROUTINE_STATUS_STAMP_GRID_INSET
            + table_loader.ROUTINE_STATUS_STAMP_WIDTH
            + table_loader.ROUTINE_AGGREGATE_LEADING_GAP,
            instance_column_left + registered.geometry().left(),
        )

        QTest.mouseDClick(stopped_stamp, Qt.LeftButton)
        on_double_click.assert_called_once()
        stopped_widget.close()
        running_widget.close()

    def test_missing_instance_uses_one_actionable_failure_dialog(self) -> None:
        status_bar = MagicMock()
        window = SimpleNamespace(statusBar=lambda: status_bar)

        with (
            patch.object(gui_windows, "routine_instance_by_id", return_value=None),
            patch.object(
                gui_windows,
                "show_auto_trade_operation_failure_dialog",
            ) as show_failure,
        ):
            gui_windows.MainWindow.toggle_routine_instance_operation(
                window,
                "missing-instance",
            )

        show_failure.assert_called_once()
        result = show_failure.call_args.args[2]
        self.assertIn("화면을 새로고침", result["user_message"])
        self.assertNotIn("INSTANCE_NOT_FOUND", result["user_message"])

    def test_instance_without_registered_stocks_has_actionable_message(self) -> None:
        status_bar = MagicMock()
        window = SimpleNamespace(
            _routine_instance_stock_dirs=Mock(return_value=[]),
            statusBar=lambda: status_bar,
        )
        instance = SimpleNamespace(
            instance_id="instance-a",
            display_name="지표추종매매",
        )

        with (
            patch.object(gui_windows, "routine_instance_by_id", return_value=instance),
            patch.object(
                gui_windows,
                "show_auto_trade_operation_failure_dialog",
            ) as show_failure,
        ):
            gui_windows.MainWindow.toggle_routine_instance_operation(
                window,
                "instance-a",
            )

        show_failure.assert_called_once()
        result = show_failure.call_args.args[2]
        self.assertIn("등록된 종목이 없습니다.", result["user_message"])
        self.assertIn("자동매매 설정", result["user_message"])

    def test_badge_availability_does_not_depend_on_removed_checkbox_state(self) -> None:
        self.assertTrue(
            table_loader.routine_instance_operation_badge_enabled(
                definition_enabled=True,
                registered_count=1,
            )
        )
        self.assertFalse(
            table_loader.routine_instance_operation_badge_enabled(
                definition_enabled=True,
                registered_count=0,
            )
        )

    def test_run_control_adapter_reuses_setting_start_orchestration(self) -> None:
        parent = QWidget()
        parent.routine_table = MagicMock()
        target = context_menu.MainMonitoringStockTarget(
            stock_dir=Path("stocks/005930_삼성전자"),
            code="005930",
            name="삼성전자",
            routine_instance_id="instance-a",
        )
        adapter = context_menu.MainMonitoringStockOperationAdapter(parent, [target])

        with (
            patch.object(
                context_menu,
                "auto_trade_start_selected_rows_auto_trades",
                return_value={"ok": True, "reason": "STARTED"},
            ) as start_orchestration,
        ):
            start_result = adapter.start_selected_auto_trades()

        start_orchestration.assert_called_once_with(adapter)
        self.assertEqual("STARTED", start_result["reason"])
        adapter.close()
        parent.close()

    def test_monitor_start_before_running_uses_setting_selective_start_contract(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            targets = []
            for index in range(4):
                code = f"{index + 1:06d}"
                name = f"테스트{index + 1}"
                stock_dir = root / f"{code}_{name}"
                stock_dir.mkdir()
                (stock_dir / "config.json").write_text(
                    json.dumps(
                        {
                            "assigned_routine_instance_id": "instance-a",
                            "routine_instance_name": "테스트 루틴",
                            "operation_excluded": index == 0,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                (stock_dir / "state.json").write_text(
                    json.dumps({"status": "STOPPED", "trade_enabled": False}),
                    encoding="utf-8",
                )
                targets.append(
                    context_menu.MainMonitoringStockTarget(
                        stock_dir=stock_dir,
                        code=code,
                        name=name,
                        routine_instance_id="instance-a",
                    )
                )

            parent = QWidget()
            parent.routine_table = QTableWidget()
            parent.statusBar = Mock(return_value=Mock())
            adapter = context_menu.MainMonitoringStockOperationAdapter(
                parent,
                [targets[0], targets[2]],
            )
            registered = [
                (target.stock_dir, target.code, target.name) for target in targets
            ]
            adapter.registered_operation_targets = lambda: registered
            adapter.running_registered_operation_targets = lambda: []
            adapter.refresh_auto_trade_assignment_views = Mock()
            adapter.update_global_operation_button_state = Mock()

            with (
                patch.object(run_control, "read_operation_state", return_value={}),
                patch.object(
                    run_control,
                    "auto_trade_start_selected_auto_trades",
                    return_value={"ok": True, "reason": "STARTED"},
                ) as start_backend,
            ):
                result = adapter.start_selected_auto_trades()

            self.assertTrue(result["ok"])
            self.assertEqual(
                [targets[0].code, targets[2].code],
                [item[1] for item in start_backend.call_args.kwargs["selected_targets"]],
            )
            configs = [
                json.loads((target.stock_dir / "config.json").read_text(encoding="utf-8"))
                for target in targets
            ]
            self.assertFalse(configs[0]["operation_excluded"])
            self.assertTrue(configs[1]["operation_excluded"])
            self.assertFalse(configs[2]["operation_excluded"])
            self.assertTrue(configs[3]["operation_excluded"])
            parent.close()

    def test_monitor_start_while_running_adds_only_selected_excluded_target(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            targets = []
            for index, excluded in enumerate((False, True, True)):
                code = f"{index + 1:06d}"
                name = f"테스트{index + 1}"
                stock_dir = root / f"{code}_{name}"
                stock_dir.mkdir()
                (stock_dir / "config.json").write_text(
                    json.dumps(
                        {
                            "assigned_routine_instance_id": "instance-a",
                            "routine_instance_name": "테스트 루틴",
                            "operation_excluded": excluded,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                (stock_dir / "state.json").write_text(
                    json.dumps({"status": "STOPPED", "trade_enabled": False}),
                    encoding="utf-8",
                )
                targets.append(
                    context_menu.MainMonitoringStockTarget(
                        stock_dir=stock_dir,
                        code=code,
                        name=name,
                        routine_instance_id="instance-a",
                    )
                )

            parent = QWidget()
            parent.routine_table = QTableWidget()
            parent.statusBar = Mock(return_value=Mock())
            adapter = context_menu.MainMonitoringStockOperationAdapter(parent, [targets[1]])
            adapter.registered_operation_targets = lambda: [
                (target.stock_dir, target.code, target.name) for target in targets
            ]
            adapter.running_registered_operation_targets = lambda: [
                (targets[0].stock_dir, targets[0].code, targets[0].name)
            ]
            adapter.refresh_auto_trade_assignment_views = Mock()
            adapter.update_global_operation_button_state = Mock()

            with (
                patch.object(run_control, "read_operation_state", return_value={}),
                patch.object(
                    run_control,
                    "auto_trade_start_selected_auto_trades",
                    return_value={"ok": True, "reason": "STARTED"},
                ) as start_backend,
            ):
                result = adapter.start_selected_auto_trades()

            self.assertTrue(result["ok"])
            start_backend.assert_called_once()
            self.assertFalse(
                json.loads(
                    (targets[1].stock_dir / "config.json").read_text(encoding="utf-8")
                )["operation_excluded"]
            )
            self.assertTrue(
                json.loads(
                    (targets[2].stock_dir / "config.json").read_text(encoding="utf-8")
                )["operation_excluded"]
            )
            parent.close()

    def test_monitor_start_mixed_running_selection_uses_setting_block_contract(self) -> None:
        parent = QWidget()
        parent.routine_table = QTableWidget()
        parent.statusBar = Mock(return_value=Mock())
        running = context_menu.MainMonitoringStockTarget(
            stock_dir=Path("stocks/000001_운영중"),
            code="000001",
            name="운영중",
            routine_instance_id="instance-a",
        )
        inactive = context_menu.MainMonitoringStockTarget(
            stock_dir=Path("stocks/000002_비운영"),
            code="000002",
            name="비운영",
            routine_instance_id="instance-a",
        )
        adapter = context_menu.MainMonitoringStockOperationAdapter(
            parent,
            [running, inactive],
        )
        adapter.running_registered_operation_targets = lambda: [
            (running.stock_dir, running.code, running.name)
        ]

        with (
            patch.object(run_control, "read_operation_state", return_value={}),
            patch.object(run_control, "auto_trade_start_selected_auto_trades") as backend,
        ):
            result = adapter.start_selected_auto_trades()

        self.assertFalse(result["ok"])
        self.assertEqual("MIXED_RUNNING_SELECTION", result["reason"])
        backend.assert_not_called()
        parent.close()

    def test_monitor_operation_adapter_requests_owner_view_synchronization(self) -> None:
        parent = QWidget()
        parent.routine_table = QTableWidget()
        parent.refresh_all = Mock()
        parent.refresh_auto_trade_assignment_views = Mock()
        adapter = context_menu.MainMonitoringStockOperationAdapter(parent, [])

        adapter.refresh_all()

        parent.refresh_auto_trade_assignment_views.assert_called_once_with()
        parent.refresh_all.assert_not_called()
        parent.close()

    def test_owner_synchronization_refreshes_each_open_view_once(self) -> None:
        setting_window_widget = QWidget()
        setting_window_widget.refresh_all = Mock()
        owner = SimpleNamespace(
            refresh_all=Mock(),
            auto_trade_setting_window=setting_window_widget,
        )

        with patch.object(gui_windows.sip, "isdeleted", return_value=False):
            gui_windows.MainWindow.refresh_auto_trade_assignment_views(owner)

        owner.refresh_all.assert_called_once_with()
        setting_window_widget.refresh_all.assert_called_once_with()
        setting_window_widget.close()

    def test_owner_synchronization_drops_deleted_settings_window(self) -> None:
        setting_window_widget = QWidget()
        setting_window_widget.refresh_all = Mock()
        owner = SimpleNamespace(
            refresh_all=Mock(),
            auto_trade_setting_window=setting_window_widget,
        )

        with patch.object(gui_windows.sip, "isdeleted", return_value=True):
            gui_windows.MainWindow.refresh_auto_trade_assignment_views(owner)

        owner.refresh_all.assert_called_once_with()
        setting_window_widget.refresh_all.assert_not_called()
        self.assertIsNone(owner.auto_trade_setting_window)
        setting_window_widget.close()

    def test_monitoring_bottom_start_reuses_setting_global_orchestration(self) -> None:
        window = SimpleNamespace()
        adapter = Mock()

        with (
            patch.object(
                gui_windows,
                "MainMonitoringStockOperationAdapter",
                return_value=adapter,
            ) as adapter_factory,
            patch.object(
                gui_windows.AutoTradeSettingWindow,
                "start_selected_auto_trades",
            ) as orchestration,
        ):
            gui_windows.MainWindow.start_global_auto_trades(window)

        adapter_factory.assert_called_once_with(window, [])
        orchestration.assert_called_once_with(adapter)

    def test_monitoring_global_start_uses_global_target_scope_and_source(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            included_dir = root / "005930_Samsung"
            excluded_dir = root / "000660_SKHynix"
            included_dir.mkdir()
            excluded_dir.mkdir()
            (included_dir / "config.json").write_text(
                json.dumps({"operation_excluded": False}),
                encoding="utf-8",
            )
            (excluded_dir / "config.json").write_text(
                json.dumps({"operation_excluded": True}),
                encoding="utf-8",
            )
            parent = QWidget()
            parent.routine_table = QTableWidget()
            parent.btn_start = QPushButton("▶ 운영시작")
            parent.auto_trade_setting_window = None
            adapter = context_menu.MainMonitoringStockOperationAdapter(parent, [])
            adapter.update_global_operation_button_state = Mock()

            with (
                patch.object(
                    run_control,
                    "all_registered_stock_dirs",
                    return_value=[included_dir, excluded_dir],
                ),
                patch.object(setting_window, "read_operation_state", return_value={}),
                patch.object(
                    setting_window,
                    "auto_trade_start_selected_auto_trades",
                    return_value={"ok": True},
                ) as backend,
            ):
                setting_window.AutoTradeSettingWindow.start_selected_auto_trades(
                    adapter
                )

            backend.assert_called_once_with(
                adapter,
                request_scope="multiple",
                selected_targets=[(included_dir, "005930", "Samsung")],
                source="auto_trade_global_start_button",
            )
            adapter.update_global_operation_button_state.assert_called_once_with()
            adapter.close()
            parent.close()

    def test_monitoring_global_start_button_uses_setting_button_state_contract(self) -> None:
        parent = QWidget()
        parent.routine_table = QTableWidget()
        parent.btn_start = QPushButton("▶ 운영시작")
        parent.auto_trade_setting_window = None
        adapter = context_menu.MainMonitoringStockOperationAdapter(parent, [])
        adapter.registered_operation_targets = Mock(
            return_value=[(Path("stocks/005930_삼성전자"), "005930", "삼성전자")]
        )
        adapter.running_registered_operation_targets = Mock(return_value=[])
        setting_button = QPushButton("▶ 운영시작")
        setting_host = SimpleNamespace(
            btn_start=setting_button,
            registered_operation_targets=adapter.registered_operation_targets,
            running_registered_operation_targets=adapter.running_registered_operation_targets,
        )

        with patch.object(run_control, "read_operation_state", return_value={}):
            setting_window.AutoTradeSettingWindow.update_global_operation_button_state(
                setting_host
            )
            adapter.update_global_operation_button_state()

        self.assertEqual(setting_button.text(), parent.btn_start.text())
        self.assertEqual(setting_button.isEnabled(), parent.btn_start.isEnabled())
        self.assertEqual(setting_button.styleSheet(), parent.btn_start.styleSheet())
        parent.close()

    def test_setting_global_start_refreshes_open_monitoring_parent(self) -> None:
        selected = [(Path("stocks/005930_삼성전자"), "005930", "삼성전자")]
        parent = SimpleNamespace(refresh_all=Mock())
        window = SimpleNamespace(
            parent=Mock(return_value=parent),
            running_registered_operation_targets=Mock(return_value=[]),
            registered_operation_start_targets=Mock(return_value=selected),
            update_global_operation_button_state=Mock(),
        )

        with (
            patch.object(setting_window, "read_operation_state", return_value={}),
            patch.object(
                setting_window,
                "auto_trade_start_selected_auto_trades",
                return_value={"ok": True},
            ) as backend,
        ):
            setting_window.AutoTradeSettingWindow.start_selected_auto_trades(window)

        backend.assert_called_once_with(
            window,
            request_scope="multiple",
            selected_targets=selected,
            source="auto_trade_global_start_button",
        )
        parent.refresh_all.assert_called_once_with()
        window.update_global_operation_button_state.assert_not_called()

    def test_auto_trade_setting_bottom_start_uses_global_multiple_scope(self) -> None:
        window = SimpleNamespace(
            selected_stock_infos=Mock(),
            update_global_operation_button_state=Mock(),
        )
        window.registered_operation_targets = lambda: (
            gui_windows.AutoTradeSettingWindow.registered_operation_targets(window)
        )
        window.registered_operation_start_targets = lambda: (
            gui_windows.AutoTradeSettingWindow.registered_operation_start_targets(window)
        )
        window.running_registered_operation_targets = lambda: (
            gui_windows.AutoTradeSettingWindow.running_registered_operation_targets(window)
        )
        stock_dirs = [
            Path("stocks/005930_삼성전자"),
            Path("stocks/000660_SK하이닉스"),
        ]

        with (
            patch(
                "gui_auto_trade_run_control.all_registered_stock_dirs",
                return_value=stock_dirs,
            ),
            patch(
                "gui_auto_trade_setting_window.auto_trade_start_selected_auto_trades"
            ) as start_backend,
            patch(
                "gui_auto_trade_run_control.auto_trade_stock_operation_excluded",
                return_value=False,
            ),
        ):
            gui_windows.AutoTradeSettingWindow.start_selected_auto_trades(window)

        window.selected_stock_infos.assert_not_called()
        call = start_backend.call_args
        self.assertEqual("multiple", call.kwargs["request_scope"])
        self.assertEqual(
            "auto_trade_global_start_button",
            call.kwargs["source"],
        )
        self.assertEqual(
            ["005930", "000660"],
            [target[1] for target in call.kwargs["selected_targets"]],
        )
        window.update_global_operation_button_state.assert_called_once_with()

    def test_auto_trade_setting_bottom_start_filters_operation_excluded_targets(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            included_dir = root / "005930_Samsung"
            excluded_dir = root / "000660_SKHynix"
            included_dir.mkdir()
            excluded_dir.mkdir()
            (included_dir / "config.json").write_text(
                json.dumps({"operation_excluded": False}),
                encoding="utf-8",
            )
            (excluded_dir / "config.json").write_text(
                json.dumps({"operation_excluded": True}),
                encoding="utf-8",
            )
            window = SimpleNamespace(
                selected_stock_infos=Mock(),
                update_global_operation_button_state=Mock(),
                statusBarMessage=Mock(),
            )
            window.registered_operation_targets = lambda: (
                gui_windows.AutoTradeSettingWindow.registered_operation_targets(window)
            )
            window.registered_operation_start_targets = lambda: (
                gui_windows.AutoTradeSettingWindow.registered_operation_start_targets(window)
            )
            window.running_registered_operation_targets = lambda: (
                gui_windows.AutoTradeSettingWindow.running_registered_operation_targets(window)
            )

            with (
                patch(
                    "gui_auto_trade_run_control.all_registered_stock_dirs",
                    return_value=[included_dir, excluded_dir],
                ),
                patch(
                    "gui_auto_trade_setting_window.auto_trade_start_selected_auto_trades"
                ) as start_backend,
            ):
                gui_windows.AutoTradeSettingWindow.start_selected_auto_trades(window)

            start_backend.assert_called_once()
            self.assertEqual(
                ["005930"],
                [
                    target[1]
                    for target in start_backend.call_args.kwargs["selected_targets"]
                ],
            )
            window.selected_stock_infos.assert_not_called()
            window.statusBarMessage.assert_not_called()
            window.update_global_operation_button_state.assert_called_once_with()

    def test_auto_trade_setting_bottom_button_does_not_stop_running_targets(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            running_dir = root / "005930_삼성전자"
            stopped_dir = root / "000660_SK하이닉스"
            running_dir.mkdir()
            stopped_dir.mkdir()
            (running_dir / "state.json").write_text(
                json.dumps({"status": "RUNNING", "trade_enabled": True}),
                encoding="utf-8",
            )
            (stopped_dir / "state.json").write_text(
                json.dumps({"status": "STOPPED", "trade_enabled": False}),
                encoding="utf-8",
            )
            window = SimpleNamespace(
                update_global_operation_button_state=Mock(),
                startup_recovery_session_ready=Mock(return_value=True),
                _current_session_operation_participant_stock_codes={"005930"},
            )
            window.registered_operation_targets = lambda: (
                gui_windows.AutoTradeSettingWindow.registered_operation_targets(window)
            )
            window.registered_operation_start_targets = lambda: (
                gui_windows.AutoTradeSettingWindow.registered_operation_start_targets(window)
            )
            window.running_registered_operation_targets = lambda: (
                gui_windows.AutoTradeSettingWindow.running_registered_operation_targets(window)
            )

            with (
                patch(
                    "gui_auto_trade_run_control.all_registered_stock_dirs",
                    return_value=[running_dir, stopped_dir],
                ),
                patch(
                    "gui_auto_trade_setting_window.auto_trade_start_selected_auto_trades"
                ) as start_backend,
            ):
                gui_windows.AutoTradeSettingWindow.start_selected_auto_trades(window)

            start_backend.assert_not_called()
            window.update_global_operation_button_state.assert_called_once_with()

    def test_global_start_button_stays_enabled_before_recovery_when_targets_exist(
        self,
    ) -> None:
        execution_buttons = [MagicMock() for _index in range(6)]
        window = SimpleNamespace(
            startup_recovery_session_ready=Mock(return_value=False),
            btn_execution_enable=execution_buttons[0],
            btn_real_ready_preflight=execution_buttons[1],
            btn_execution_preview=execution_buttons[2],
            btn_manual_send_order=execution_buttons[3],
            btn_manual_cancel_pending_order=execution_buttons[4],
            btn_manual_modify_pending_order=execution_buttons[5],
            btn_manual_queue_commit=MagicMock(),
            btn_start=MagicMock(),
            update_global_operation_button_state=MagicMock(),
        )

        with patch(
            "gui_auto_trade_run_control.all_registered_stock_dirs",
            return_value=[Path("stocks/005930_삼성전자")],
        ):
            gui_windows.AutoTradeSettingWindow.update_startup_recovery_controls(window)

        window.update_global_operation_button_state.assert_called_once_with()
        for button in execution_buttons:
            button.setEnabled.assert_called_once_with(False)
        window.btn_manual_queue_commit.setEnabled.assert_called_once_with(False)

    def test_global_operation_button_uses_official_running_state(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            running_dir = root / "005930_삼성전자"
            stopped_dir = root / "000660_SK하이닉스"
            running_dir.mkdir()
            stopped_dir.mkdir()
            (running_dir / "state.json").write_text(
                json.dumps({"status": "RUNNING", "trade_enabled": True}),
                encoding="utf-8",
            )
            (stopped_dir / "state.json").write_text(
                json.dumps({"status": "STOPPED", "trade_enabled": False}),
                encoding="utf-8",
            )
            window = SimpleNamespace(
                btn_start=MagicMock(),
                startup_recovery_session_ready=Mock(return_value=True),
                _current_session_operation_participant_stock_codes={"005930"},
            )
            window.registered_operation_targets = lambda: (
                gui_windows.AutoTradeSettingWindow.registered_operation_targets(window)
            )
            window.running_registered_operation_targets = lambda: (
                gui_windows.AutoTradeSettingWindow.running_registered_operation_targets(window)
            )

            with (
                patch(
                    "gui_auto_trade_run_control.all_registered_stock_dirs",
                    return_value=[running_dir, stopped_dir],
                ),
                patch(
                    "gui_auto_trade_run_control.read_operation_state",
                    return_value={"emergency_stop": False, "operation_status": ""},
                ),
            ):
                gui_windows.AutoTradeSettingWindow.update_global_operation_button_state(
                    window
                )

            window.btn_start.setText.assert_called_once_with("운영중")
            window.btn_start.setEnabled.assert_called_once_with(False)

            (running_dir / "state.json").write_text(
                json.dumps({"status": "STOPPED", "trade_enabled": False}),
                encoding="utf-8",
            )
            window.btn_start.reset_mock()
            with (
                patch(
                    "gui_auto_trade_run_control.all_registered_stock_dirs",
                    return_value=[running_dir, stopped_dir],
                ),
                patch(
                    "gui_auto_trade_run_control.read_operation_state",
                    return_value={"emergency_stop": False, "operation_status": ""},
                ),
            ):
                gui_windows.AutoTradeSettingWindow.update_global_operation_button_state(
                    window
                )

            window.btn_start.setText.assert_called_once_with("▶ 운영시작")
            window.btn_start.setEnabled.assert_called_once_with(True)

    def test_stock_name_double_click_toggles_operation_exclusion(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "000660_SK하이닉스"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                json.dumps({"status": "STOPPED", "trade_enabled": False}),
                encoding="utf-8",
            )
            window = SimpleNamespace(
                stock_info_from_row=Mock(
                    return_value=(stock_dir, "000660", "SK하이닉스")
                ),
                statusBarMessage=Mock(),
            )
            target = (stock_dir, "000660", "Test")
            window.stock_info_from_row.return_value = target
            window.toggle_stock_operation_exclusion = Mock(return_value=True)
            item = SimpleNamespace(column=lambda: 1, row=lambda: 2)

            with patch(
                "gui_auto_trade_setting_window.auto_trade_start_status_indicator"
            ) as adapter:
                gui_windows.AutoTradeSettingWindow.on_stock_table_name_item_double_clicked(
                    window,
                    item,
                )

        window.toggle_stock_operation_exclusion.assert_called_once_with(
            target,
            refresh=False,
        )
        adapter.assert_not_called()

    def test_running_status_indicator_does_not_call_start_backend(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "000660_SK하이닉스"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                json.dumps({"status": "RUNNING", "trade_enabled": True}),
                encoding="utf-8",
            )
            window = SimpleNamespace(
                statusBarMessage=Mock(),
                startup_recovery_session_ready=Mock(return_value=True),
                _current_session_operation_participant_stock_codes={"000660"},
            )
            target = (stock_dir, "000660", "SK하이닉스")

            with (
                patch(
                    "gui_auto_trade_run_control.auto_trade_start_selected_auto_trades"
                ) as backend,
                patch(
                    "gui_auto_trade_run_control.show_auto_trade_operation_failure_dialog"
                ) as dialog,
            ):
                result = run_control.auto_trade_start_status_indicator(
                    window,
                    target,
                )

        backend.assert_not_called()
        dialog.assert_called_once()
        self.assertEqual("ALREADY_RUNNING", result["reason"])
        self.assertIn("이미 운영 중입니다", result["user_message"])

    def test_non_name_cells_do_not_start_stock(self) -> None:
        for column in (0, 2, 3, 4):
            with self.subTest(column=column):
                window = SimpleNamespace(
                    stock_info_from_row=Mock(),
                    toggle_stock_operation_exclusion=Mock(),
                )
                item = SimpleNamespace(column=lambda: column, row=lambda: 2)

                with patch(
                    "gui_auto_trade_setting_window.auto_trade_start_status_indicator"
                ) as adapter:
                    gui_windows.AutoTradeSettingWindow.on_stock_table_name_item_double_clicked(
                        window,
                        item,
                    )

                adapter.assert_not_called()
                window.stock_info_from_row.assert_not_called()
                window.toggle_stock_operation_exclusion.assert_not_called()

    def test_status_indicator_ignores_reentry_while_start_is_inflight(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "000660_SK하이닉스"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                json.dumps({"status": "STOPPED", "trade_enabled": False}),
                encoding="utf-8",
            )
            window = SimpleNamespace(
                _operation_start_inflight_stock_codes={"000660"},
            )
            target = (stock_dir, "000660", "SK하이닉스")

            with patch(
                "gui_auto_trade_run_control.auto_trade_start_selected_auto_trades"
            ) as backend:
                result = run_control.auto_trade_start_status_indicator(
                    window,
                    target,
                )

        backend.assert_not_called()
        self.assertEqual("REQUEST_IN_PROGRESS", result["reason"])

    def test_status_indicator_reports_official_readback_mismatch(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "000660_SK하이닉스"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                json.dumps({"status": "STOPPED", "trade_enabled": False}),
                encoding="utf-8",
            )
            window = SimpleNamespace(
                statusBarMessage=Mock(),
            )
            target = (stock_dir, "000660", "SK하이닉스")

            with (
                patch(
                    "gui_auto_trade_run_control.auto_trade_start_selected_auto_trades",
                    return_value={"ok": True, "reason": "STARTED"},
                ),
                patch(
                    "gui_auto_trade_run_control.show_auto_trade_operation_failure_dialog"
                ) as dialog,
            ):
                failure = run_control.auto_trade_start_status_indicator(
                    window,
                    target,
                )

        dialog.assert_called_once()
        self.assertEqual("STATE_READBACK_FAILED", failure["reason"])
        self.assertEqual("single", failure["request_scope"])
        self.assertEqual(
            "auto_trade_status_indicator",
            failure["source"],
        )
        self.assertEqual(set(), window._operation_start_inflight_stock_codes)

    def test_selected_rows_context_start_is_always_multiple(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "stocks" / "005930_삼성전자"
            stock_dir.mkdir(parents=True)
            (stock_dir / "config.json").write_text(
                json.dumps({"operation_excluded": False}, ensure_ascii=False),
                encoding="utf-8",
            )
            selected = [(stock_dir, "005930", "삼성전자")]
            window = SimpleNamespace(
                selected_stock_infos=Mock(return_value=selected),
                running_registered_operation_targets=Mock(return_value=[]),
                registered_operation_targets=Mock(return_value=selected),
                refresh_all=Mock(),
                update_global_operation_button_state=Mock(),
            )

            with (
                patch(
                    "gui_auto_trade_run_control.auto_trade_start_selected_auto_trades",
                    return_value={"ok": False},
                ) as backend,
                patch(
                    "gui_auto_trade_run_control.read_operation_state",
                    return_value={},
                ),
            ):
                gui_windows.AutoTradeSettingWindow.start_selected_rows_auto_trades(window)

        call = backend.call_args
        self.assertEqual("multiple", call.kwargs["request_scope"])
        self.assertEqual(selected, call.kwargs["selected_targets"])
        self.assertEqual(
            "auto_trade_context_menu",
            call.kwargs["source"],
        )

    def test_selected_rows_context_start_requires_running_targets_protocol(self) -> None:
        selected = [(Path("stocks/005930_삼성전자"), "005930", "삼성전자")]
        window = SimpleNamespace(selected_stock_infos=Mock(return_value=selected))

        with patch(
            "gui_auto_trade_run_control.auto_trade_start_selected_auto_trades"
        ) as backend:
            with self.assertRaises(AttributeError):
                gui_windows.AutoTradeSettingWindow.start_selected_rows_auto_trades(window)

        backend.assert_not_called()

    def test_selected_stock_infos_excludes_hidden_selected_rows(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            visible_dir = root / "005930_삼성전자"
            hidden_dir = root / "000660_SK하이닉스"
            visible_dir.mkdir()
            hidden_dir.mkdir()
            table = QTableWidget(2, 2)
            table.setSelectionBehavior(QAbstractItemView.SelectRows)
            table.setSelectionMode(QAbstractItemView.ExtendedSelection)
            for row, (stock_dir, code, name) in enumerate(
                (
                    (visible_dir, "005930", "삼성전자"),
                    (hidden_dir, "000660", "SK하이닉스"),
                )
            ):
                code_item = QTableWidgetItem(code)
                code_item.setData(Qt.UserRole, str(stock_dir))
                table.setItem(row, 0, code_item)
                table.setItem(row, 1, QTableWidgetItem(name))
                table.selectionModel().select(
                    table.model().index(row, 0),
                    QItemSelectionModel.Select | QItemSelectionModel.Rows,
                )
            table.setRowHidden(1, True)

            infos = gui_auto_trade_selection.selected_stock_infos(
                SimpleNamespace(stock_table=table)
            )

        self.assertEqual(["005930"], [info[1] for info in infos])
        table.close()

    def test_selected_stock_infos_falls_back_to_selected_indexes(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dir = root / "005930_Samsung"
            stock_dir.mkdir()
            table = QTableWidget(1, 2)
            table.setSelectionBehavior(QAbstractItemView.SelectItems)
            table.setSelectionMode(QAbstractItemView.ExtendedSelection)
            code_item = QTableWidgetItem("005930")
            code_item.setData(Qt.UserRole, str(stock_dir))
            table.setItem(0, 0, code_item)
            table.setItem(0, 1, QTableWidgetItem("Samsung"))
            table.selectionModel().select(
                table.model().index(0, 1),
                QItemSelectionModel.Select,
            )

            infos = gui_auto_trade_selection.selected_stock_infos(
                SimpleNamespace(stock_table=table)
            )

        self.assertEqual([(stock_dir, "005930", "Samsung")], infos)
        table.close()

    def test_context_row_preserves_or_replaces_extended_row_selection(self) -> None:
        table = QTableWidget(3, 1)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        for row in range(3):
            table.setItem(row, 0, QTableWidgetItem(str(row)))
        selection_model = table.selectionModel()
        for row in (0, 2):
            selection_model.select(
                table.model().index(row, 0),
                QItemSelectionModel.Select | QItemSelectionModel.Rows,
            )
        window = SimpleNamespace(stock_table=table)

        gui_auto_trade_selection.ensure_context_row_selected(window, 2)
        self.assertEqual(
            [0, 2],
            sorted(index.row() for index in selection_model.selectedRows()),
        )

        gui_auto_trade_selection.ensure_context_row_selected(window, 1)
        self.assertEqual(
            [1],
            sorted(index.row() for index in selection_model.selectedRows()),
        )
        table.close()

    def test_recovery_block_message_is_reported_on_monitoring_window(self) -> None:
        parent = QWidget()
        parent.routine_table = MagicMock()
        parent.startup_recovery_session_ready = MagicMock(return_value=False)
        parent.startup_recovery_block_reason = MagicMock(return_value="INVALID_RUNTIME")
        parent.statusBar = MagicMock()
        status_bar = parent.statusBar.return_value
        target = context_menu.MainMonitoringStockTarget(
            stock_dir=Path("stocks/005930_삼성전자"),
            code="005930",
            name="삼성전자",
            routine_instance_id="instance-a",
        )
        adapter = context_menu.MainMonitoringStockOperationAdapter(parent, [target])

        self.assertFalse(adapter.require_startup_recovery_session("운영시작"))
        status_bar.showMessage.assert_called_once()
        message = status_bar.showMessage.call_args.args[0]
        self.assertIn("Recovery 완료 상태를 확인", message)
        self.assertNotIn("INVALID_RUNTIME", message)
        adapter.close()
        parent.close()

    def test_stopped_instance_badge_starts_instance(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            stopped_dir = root / "005930_삼성전자"
            stopped_dir.mkdir()
            (stopped_dir / "state.json").write_text(
                json.dumps({"status": "STOPPED", "trade_enabled": False}),
                encoding="utf-8",
            )
            adapter = MagicMock()
            status_bar = MagicMock()
            window = SimpleNamespace(
                _routine_instance_stock_dirs=lambda _instance_id: [stopped_dir],
                _reload_main_routine_table_preserving_view=MagicMock(),
                statusBar=lambda: status_bar,
            )
            instance = SimpleNamespace(
                instance_id="instance-a",
                display_name="지표추종매매",
            )
            with (
                patch.object(gui_windows, "routine_instance_by_id", return_value=instance),
                patch.object(
                    gui_windows,
                    "auto_trade_running_registered_operation_targets",
                    side_effect=[
                        [],
                        [(stopped_dir, "005930", "삼성전자")],
                    ],
                ),
                patch.object(
                    gui_windows,
                    "MainMonitoringStockOperationAdapter",
                    return_value=adapter,
                ) as adapter_factory,
            ):
                def start_instance() -> dict[str, object]:
                    (stopped_dir / "state.json").write_text(
                        json.dumps({"status": "MONITORING", "trade_enabled": True}),
                        encoding="utf-8",
                    )
                    return {"ok": True, "reason": "STARTED"}

                adapter.start_selected_auto_trades.side_effect = start_instance
                gui_windows.MainWindow.toggle_routine_instance_operation(
                    window,
                    "instance-a",
                )

            adapter.start_selected_auto_trades.assert_called_once()
            targets = adapter_factory.call_args.args[1]
            self.assertEqual(["005930"], [target.code for target in targets])
            self.assertEqual(
                "multiple",
                adapter_factory.call_args.kwargs["request_scope"],
            )
            self.assertTrue(
                table_loader.auto_trade_setting_trade_started(
                    json.loads((stopped_dir / "state.json").read_text(encoding="utf-8"))
                )
            )
            self.assertIn("운영시작 완료", status_bar.showMessage.call_args.args[0])

    def test_running_instance_badge_does_not_mutate_or_open_stop_dialog(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            stock_specs = (
                ("000001", "현재운영1", True),
                ("000002", "현재운영2", True),
                ("000003", "과거운영", True),
                ("000004", "정지", False),
            )
            stock_dirs = []
            for code, name, persisted_running in stock_specs:
                stock_dir = root / f"{code}_{name}"
                stock_dir.mkdir()
                (stock_dir / "state.json").write_text(
                    json.dumps(
                        {
                            "status": "RUNNING" if persisted_running else "STOPPED",
                            "trade_enabled": persisted_running,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                stock_dirs.append(stock_dir)

            before_states = {
                stock_dir: (stock_dir / "state.json").read_text(encoding="utf-8")
                for stock_dir in stock_dirs
            }
            status_bar = MagicMock()
            window = SimpleNamespace(
                _routine_instance_stock_dirs=lambda _instance_id: stock_dirs,
                _reload_main_routine_table_preserving_view=MagicMock(),
                statusBar=lambda: status_bar,
            )
            instance = SimpleNamespace(
                instance_id="instance-a",
                display_name="테스트 루틴",
            )
            current_running = [
                (stock_dirs[0], "000001", "현재운영1"),
                (stock_dirs[1], "000002", "현재운영2"),
            ]
            with (
                patch.object(gui_windows, "routine_instance_by_id", return_value=instance),
                patch.object(
                    gui_windows,
                    "auto_trade_running_registered_operation_targets",
                    return_value=current_running,
                ),
                patch.object(
                    gui_windows,
                    "MainMonitoringStockOperationAdapter",
                ) as adapter_factory,
            ):
                gui_windows.MainWindow.toggle_routine_instance_operation(
                    window,
                    "instance-a",
                )

            adapter_factory.assert_not_called()
            self.assertEqual(
                before_states,
                {
                    stock_dir: (stock_dir / "state.json").read_text(encoding="utf-8")
                    for stock_dir in stock_dirs
                },
            )
            self.assertIn(
                "긴급정지",
                status_bar.showMessage.call_args.args[0],
            )

    def test_stale_running_instance_uses_start_direction(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "068270_셀트리온"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                json.dumps({"status": "RUNNING", "trade_enabled": True}),
                encoding="utf-8",
            )
            adapter = MagicMock()
            adapter.start_selected_auto_trades.return_value = {
                "ok": False,
                "reason": "BLOCKED_RECOVERY",
            }
            status_bar = MagicMock()
            window = SimpleNamespace(
                _routine_instance_stock_dirs=lambda _instance_id: [stock_dir],
                _reload_main_routine_table_preserving_view=MagicMock(),
                statusBar=lambda: status_bar,
            )
            instance = SimpleNamespace(
                instance_id="instance-a",
                display_name="테스트 루틴",
            )
            with (
                patch.object(gui_windows, "routine_instance_by_id", return_value=instance),
                patch.object(
                    gui_windows,
                    "auto_trade_running_registered_operation_targets",
                    return_value=[],
                ),
                patch.object(
                    gui_windows,
                    "MainMonitoringStockOperationAdapter",
                    return_value=adapter,
                ),
            ):
                gui_windows.MainWindow.toggle_routine_instance_operation(
                    window,
                    "instance-a",
                )

            adapter.start_selected_auto_trades.assert_called_once()

    def test_blocked_start_keeps_official_state_and_reports_reason(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_삼성전자"
            stock_dir.mkdir()
            state_path = stock_dir / "state.json"
            state_path.write_text(
                json.dumps({"status": "STOPPED", "trade_enabled": False}),
                encoding="utf-8",
            )
            status_bar = MagicMock()
            adapter = MagicMock()
            adapter._last_operation_user_message = (
                "운영 시작 전에 Recovery가 완료되지 않았습니다."
            )
            adapter.start_selected_auto_trades.return_value = {
                "ok": False,
                "reason": "BLOCKED_RECOVERY",
            }
            window = SimpleNamespace(
                _routine_instance_stock_dirs=lambda _instance_id: [stock_dir],
                _reload_main_routine_table_preserving_view=MagicMock(),
                statusBar=lambda: status_bar,
            )
            instance = SimpleNamespace(
                instance_id="instance-a",
                display_name="지표추종매매",
            )
            with (
                patch.object(gui_windows, "routine_instance_by_id", return_value=instance),
                patch.object(
                    gui_windows,
                    "MainMonitoringStockOperationAdapter",
                    return_value=adapter,
                ),
            ):
                gui_windows.MainWindow.toggle_routine_instance_operation(
                    window,
                    "instance-a",
                )

            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertFalse(table_loader.auto_trade_setting_trade_started(state))
            status_message = status_bar.showMessage.call_args.args[0]
            self.assertIn("Recovery가 완료되지 않았습니다.", status_message)
            self.assertNotIn("BLOCKED_RECOVERY", status_message)
            adapter.show_operation_failure_dialog.assert_called_once_with(
                "운영시작",
                {
                    "ok": False,
                    "reason": "BLOCKED_RECOVERY",
                },
            )
            window._reload_main_routine_table_preserving_view.assert_called_once()

    def test_recovery_failure_uses_shared_dialog_message(self) -> None:
        parent = QWidget()
        parent.routine_table = MagicMock()
        target = context_menu.MainMonitoringStockTarget(
            stock_dir=Path("stocks/000660_SK하이닉스"),
            code="000660",
            name="SK하이닉스",
            routine_instance_id="instance-a",
        )
        adapter = context_menu.MainMonitoringStockOperationAdapter(parent, [target])
        adapter._last_operation_block_reason = (
            "INVALID_RUNTIME: 보유수량 있음 + 현재가 확인 불가"
        )

        with (
            patch("gui_auto_trade_run_control.show_toast") as toast,
            patch("gui_auto_trade_run_control.QMessageBox.warning") as warning,
        ):
            shown = adapter.show_operation_failure_dialog(
                "운영시작",
                {
                    "ok": False,
                    "reason": adapter._last_operation_block_reason,
                },
            )

        self.assertTrue(shown)
        toast.assert_called_once_with(
            parent=parent,
            message=(
                "운영시작할 수 없습니다. "
                "로그인, 계좌 선택 및 Recovery 완료 상태를 확인하십시오."
                "\n\n원인: 보유수량 있음 + 현재가 확인 불가"
            ),
            duration_ms=2500,
            position="center",
        )
        warning.assert_not_called()
        adapter.close()
        parent.close()

    def test_recovery_context_missing_displays_actual_login_cause(self) -> None:
        api = SimpleNamespace(
            is_connected=Mock(return_value=False),
            login_session_id=Mock(return_value=""),
        )
        window = SimpleNamespace(
            kiwoom_api=api,
            selected_account_no=Mock(return_value=""),
        )
        decision = SimpleNamespace(
            reason_code=gui_windows.RECOVERY_CONTEXT_MISSING,
            evidence=(),
        )

        message = gui_windows.MainWindow.production_recovery_block_user_message(
            window,
            decision,
        )

        self.assertEqual("키움 서버에 로그인되어 있지 않습니다.", message)
        self.assertNotIn("RECOVERY_", message)

    def test_recovery_context_missing_displays_account_selection_cause(self) -> None:
        api = SimpleNamespace(
            is_connected=Mock(return_value=True),
            login_session_id=Mock(return_value="login-a"),
        )
        window = SimpleNamespace(
            kiwoom_api=api,
            selected_account_no=Mock(return_value=""),
        )
        decision = SimpleNamespace(
            reason_code=gui_windows.RECOVERY_CONTEXT_MISSING,
            evidence=(),
        )

        message = gui_windows.MainWindow.production_recovery_block_user_message(
            window,
            decision,
        )

        self.assertEqual("운영할 계좌를 선택하십시오.", message)
        self.assertNotIn("RECOVERY_", message)

    def test_routine_recovery_block_message_uses_operator_language(self) -> None:
        cases = (
            (
                SimpleNamespace(is_connected=Mock(return_value=False)),
                "",
                None,
                "키움 서버에 로그인되어 있지 않습니다.",
            ),
            (
                SimpleNamespace(is_connected=Mock(return_value=True)),
                "",
                None,
                "사용할 계좌 정보가 아직 확인되지 않았습니다.",
            ),
            (
                SimpleNamespace(is_connected=Mock(return_value=True)),
                "12345678",
                SimpleNamespace(account_status="COLLECTING"),
                "기존 운영 상태를 확인하고 있습니다.",
            ),
            (
                SimpleNamespace(is_connected=Mock(return_value=True)),
                "12345678",
                SimpleNamespace(account_status="FAILED"),
                "이전 운영 상태를 확인하지 못했습니다.",
            ),
            (
                SimpleNamespace(is_connected=Mock(return_value=True)),
                "12345678",
                None,
                "운영 상태 확인이 아직 완료되지 않았습니다.",
            ),
        )
        for api, account_no, context, expected in cases:
            with self.subTest(expected=expected), patch.object(
                gui_windows.production_recovery_registry,
                "snapshot",
                return_value=context,
            ):
                window = SimpleNamespace(
                    kiwoom_api=api,
                    selected_account_no=Mock(return_value=account_no),
                )
                message = gui_windows.MainWindow.routine_recovery_block_message(
                    window,
                    "루틴 조기마감",
                )
                if expected == "키움 서버에 로그인되어 있지 않습니다.":
                    self.assertEqual(expected, message)
                else:
                    self.assertIn("루틴 조기마감 불가", message)
                self.assertIn(expected, message)
                self.assertNotIn("Recovery", message)
                self.assertNotIn("RECOVERY_", message)

    def test_routine_restart_login_block_is_single_line(self) -> None:
        window = SimpleNamespace(
            kiwoom_api=SimpleNamespace(is_connected=Mock(return_value=False)),
            selected_account_no=Mock(return_value=""),
        )

        message = gui_windows.MainWindow.routine_recovery_block_message(
            window,
            "루틴 재시작",
        )

        self.assertEqual("키움 서버에 로그인되어 있지 않습니다.", message)
        self.assertNotIn("\n", message)

    def _routine_close_target(self, code="005930", instance_id="instance-a"):
        return gui_windows.MainMonitoringStockTarget(
            stock_dir=Path(f"{code}_stock"),
            code=code,
            name=f"stock-{code}",
            routine_instance_id=instance_id,
        )

    def _append_monitoring_stock_row(
        self,
        table: QTableWidget,
        stock_dir: Path,
        *,
        code: str,
        name: str,
        instance_id: str = "instance-a",
    ) -> int:
        row = table.rowCount()
        table.insertRow(row)
        item = QTableWidgetItem(code)
        item.setData(table_loader.ROUTINE_ROW_KIND_ROLE, table_loader.ROUTINE_ROW_STOCK)
        item.setData(table_loader.ROUTINE_STOCK_PATH_ROLE, str(stock_dir))
        item.setData(table_loader.ROUTINE_STOCK_CODE_ROLE, code)
        item.setData(table_loader.ROUTINE_STOCK_NAME_ROLE, name)
        item.setData(table_loader.ROUTINE_INSTANCE_ID_ROLE, instance_id)
        table.setItem(row, 0, item)
        return row

    def test_visible_early_close_button_reuses_top_summary_badge_font(self) -> None:
        window = SimpleNamespace()
        window.btn_group_pack_register = QPushButton("그룹등록")
        window.btn_main_visible_early_close = QPushButton("조기마감")
        vertical_badge = QPushButton("보유")
        top_badge = QPushButton("유효")
        badge_font = top_badge.font()
        badge_font.setPointSize(12)
        top_badge.setFont(badge_font)

        def setup_routine_table():
            window.routine_table = QTableWidget()

        def setup_running_stock_table():
            window.running_stock_table = QTableWidget()

        def create_filter_badge_area():
            window._main_routine_metric_buttons = {"holding": vertical_badge}
            return QWidget()

        def create_main_routine_summary():
            window._main_routine_valid_button = top_badge
            return QWidget()

        window._setup_routine_table = setup_routine_table
        window._setup_running_stock_table = setup_running_stock_table
        window._create_routine_filter_badge_area = create_filter_badge_area
        window._create_main_routine_summary = create_main_routine_summary

        table_area = gui_windows.MainWindow._create_table_area(window)
        routine_box = table_area.itemAt(0).widget()
        header_layout = routine_box.layout().itemAt(0).layout()

        self.assertEqual(
            top_badge.font().pointSize(),
            window.btn_main_visible_early_close.font().pointSize(),
        )
        self.assertEqual(
            top_badge.font().pointSizeF(),
            window.btn_main_visible_early_close.font().pointSizeF(),
        )
        expected_font_size = f"font-size: {top_badge.font().pointSizeF():g}pt"
        self.assertIn(expected_font_size, window.btn_main_visible_early_close.styleSheet())
        self.assertIn(
            "QPushButton#mainVisibleEarlyCloseButton",
            window.btn_main_visible_early_close.styleSheet(),
        )
        left_metrics = QFontMetrics(top_badge.font())
        early_close_metrics = QFontMetrics(window.btn_main_visible_early_close.font())
        self.assertEqual(left_metrics.height(), early_close_metrics.height())
        self.assertEqual(left_metrics.ascent(), early_close_metrics.ascent())
        self.assertEqual(
            left_metrics.boundingRect("조기마감").height(),
            early_close_metrics.boundingRect("조기마감").height(),
        )
        self.assertEqual(28, window.btn_main_visible_early_close.minimumHeight())
        self.assertEqual(28, window.btn_group_pack_register.minimumHeight())
        self.assertEqual(
            window.btn_main_visible_early_close.font(),
            window.btn_group_pack_register.font(),
        )
        self.assertIn(
            "QPushButton#mainGroupPackRegisterButton",
            window.btn_group_pack_register.styleSheet(),
        )
        self.assertIs(window.btn_group_pack_register, header_layout.itemAt(2).widget())
        self.assertIs(window.btn_main_visible_early_close, header_layout.itemAt(3).widget())
        self.assertIn(
            "color: #2563eb; font-weight: bold;",
            window.btn_main_visible_early_close.styleSheet(),
        )

    def test_group_pack_registration_cancel_has_no_side_effect(self) -> None:
        window = SimpleNamespace(refresh_auto_trade_assignment_views=Mock())
        with (
            patch.object(gui_windows.QFileDialog, "getOpenFileName", return_value=("", "")),
            patch.object(gui_windows, "register_group_pack") as register,
            patch.object(gui_windows, "show_toast") as toast,
        ):
            gui_windows.MainWindow.register_group_pack_from_file(window)

        register.assert_not_called()
        window.refresh_auto_trade_assignment_views.assert_not_called()
        toast.assert_not_called()

    def test_group_pack_registration_success_refreshes_and_toasts_once(self) -> None:
        window = SimpleNamespace(refresh_auto_trade_assignment_views=Mock())
        result = SimpleNamespace(
            success=True,
            group=SimpleNamespace(
                group_id="11111111-1111-4111-8111-111111111111",
                display_name="지표추종매매_1",
            ),
        )
        with (
            patch.object(
                gui_windows.QFileDialog,
                "getOpenFileName",
                return_value=("sample.group.zip", "Group Pack (*.group.zip)"),
            ),
            patch.object(gui_windows, "register_group_pack", return_value=result) as register,
            patch.object(gui_windows, "show_toast") as toast,
            patch.object(gui_windows.QMessageBox, "warning") as warning,
        ):
            gui_windows.MainWindow.register_group_pack_from_file(window)

        register.assert_called_once_with(
            "sample.group.zip",
            project_root=gui_windows.PROJECT_ROOT,
        )
        window.refresh_auto_trade_assignment_views.assert_called_once_with()
        warning.assert_not_called()
        toast.assert_called_once_with(
            window,
            "지표추종매매_1 그룹을 등록했습니다.",
            duration_ms=2500,
        )

    def test_routine_close_candidates_intersect_current_session_running_targets(self) -> None:
        running_dir = Path("005930_running")
        waiting_dir = Path("000660_waiting")
        other_dir = Path("035720_other")
        window = SimpleNamespace(
            _routine_instance_stock_dirs=Mock(
                return_value=[running_dir, waiting_dir]
            )
        )
        with patch.object(
            gui_windows,
            "auto_trade_running_registered_operation_targets",
            return_value=[
                (running_dir, "005930", "삼성전자"),
                (other_dir, "035720", "카카오"),
            ],
        ):
            targets = gui_windows.MainWindow._running_routine_operation_targets(
                window,
                ("instance-a",),
            )

        self.assertEqual(["005930"], [target.code for target in targets])
        self.assertEqual("instance-a", targets[0].routine_instance_id)

    def test_visible_early_close_targets_use_visible_running_stock_rows_only(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            running_dir = root / "005930_삼성전자"
            waiting_dir = root / "000660_SK하이닉스"
            hidden_dir = root / "035720_카카오"
            review_dir = root / "051910_LG화학"
            for stock_dir in (running_dir, waiting_dir, hidden_dir, review_dir):
                stock_dir.mkdir()
            (running_dir / "state.json").write_text("{}", encoding="utf-8")
            (waiting_dir / "state.json").write_text("{}", encoding="utf-8")
            (hidden_dir / "state.json").write_text("{}", encoding="utf-8")
            (review_dir / "state.json").write_text(
                json.dumps({"review_required": True}),
                encoding="utf-8",
            )
            table = QTableWidget()
            table.setColumnCount(1)
            self._append_monitoring_stock_row(
                table,
                running_dir,
                code="005930",
                name="삼성전자",
            )
            self._append_monitoring_stock_row(
                table,
                waiting_dir,
                code="000660",
                name="SK하이닉스",
            )
            hidden_row = self._append_monitoring_stock_row(
                table,
                hidden_dir,
                code="035720",
                name="카카오",
            )
            self._append_monitoring_stock_row(
                table,
                review_dir,
                code="051910",
                name="LG화학",
            )
            table.hideRow(hidden_row)
            window = SimpleNamespace(routine_table=table)

            with patch.object(
                gui_windows,
                "auto_trade_running_registered_operation_targets",
                return_value=[
                    (running_dir, "005930", "삼성전자"),
                    (hidden_dir, "035720", "카카오"),
                    (review_dir, "051910", "LG화학"),
                ],
            ):
                targets = gui_windows.MainWindow._visible_monitoring_early_close_targets(
                    window
                )

        self.assertEqual(["005930"], [target.code for target in targets])

    def test_visible_early_close_button_uses_policy_method_and_actual_success_count(self) -> None:
        confirmation = MagicMock()
        confirmation.exec_.return_value = gui_windows.QMessageBox.Yes
        target = self._routine_close_target()
        adapter = MagicMock()
        adapter.apply_selected_early_close.return_value = {
            "ok": True,
            "completed_count": 3,
            "failed_count": 0,
            "message": "",
        }
        window = SimpleNamespace(
            _visible_monitoring_early_close_targets=Mock(return_value=[target]),
            statusBar=Mock(return_value=SimpleNamespace(showMessage=Mock())),
            update_review_required_button_text=Mock(),
        )

        with (
            patch.object(
                gui_windows,
                "_create_routine_operation_confirmation",
                return_value=confirmation,
            ),
            patch.object(
                gui_windows,
                "operation_policy_section",
                return_value={"method": "현재가"},
            ),
            patch.object(gui_windows, "append_production_event"),
            patch.object(
                gui_windows,
                "MainMonitoringStockOperationAdapter",
                return_value=adapter,
            ) as adapter_factory,
            patch.object(gui_windows, "show_toast") as toast,
        ):
            gui_windows.MainWindow.request_visible_monitoring_early_close(window)

        adapter_factory.assert_called_once_with(
            window,
            [target],
            request_scope="multiple",
        )
        adapter.apply_selected_early_close.assert_called_once_with(
            "현재가",
            source="main_visible_early_close_button",
            show_error_dialog=False,
            show_result_toast=False,
            show_confirmation=False,
        )
        toast.assert_called_once_with(
            window,
            "조기마감 3종목 적용 합니다.",
            duration_ms=2500,
        )

    def test_visible_early_close_button_zero_targets_does_not_enter_backend(self) -> None:
        window = SimpleNamespace(
            _visible_monitoring_early_close_targets=Mock(return_value=[]),
            statusBar=Mock(return_value=SimpleNamespace(showMessage=Mock())),
        )

        with (
            patch.object(gui_windows, "MainMonitoringStockOperationAdapter") as adapter_factory,
            patch.object(gui_windows, "_create_routine_operation_confirmation") as confirmation,
            patch.object(gui_windows, "show_toast") as toast,
        ):
            gui_windows.MainWindow.request_visible_monitoring_early_close(window)

        adapter_factory.assert_not_called()
        confirmation.assert_not_called()
        toast.assert_called_once_with(
            window,
            "조기마감 대상이 없습니다.",
            duration_ms=2500,
        )

    def test_instance_close_actions_use_stock_canonical_boundary(self) -> None:
        for method, label in (
            ("루틴", gui_windows.ROUTINE_STATUS_EARLY_CLOSE),
            (gui_windows.POLICY_MARKET, gui_windows.ROUTINE_STATUS_IMMEDIATE_LIQUIDATION),
        ):
            with self.subTest(method=method):
                confirmation = MagicMock()
                confirmation.exec_.return_value = gui_windows.QMessageBox.Yes
                target = self._routine_close_target()
                adapter = MagicMock()
                adapter.apply_selected_early_close.return_value = {
                    "ok": True,
                    "completed_count": 1,
                    "failed_count": 0,
                    "message": "",
                }
                window = SimpleNamespace(
                    statusBar=Mock(return_value=SimpleNamespace(showMessage=Mock())),
                    update_review_required_button_text=Mock(),
                )
                with (
                    patch.object(
                        gui_windows.MainWindow,
                        "_running_routine_operation_targets",
                        return_value=[target],
                    ) as collect_targets,
                    patch.object(
                        gui_windows,
                        "_create_routine_operation_confirmation",
                        return_value=confirmation,
                    ),
                    patch.object(
                        gui_windows,
                        "MainMonitoringStockOperationAdapter",
                        return_value=adapter,
                    ) as adapter_factory,
                    patch.object(gui_windows, "show_toast") as toast,
                ):
                    gui_windows.MainWindow.request_routine_operation(
                        window,
                        "instance-a",
                        "지표추종매매",
                        method,
                        label,
                    )

                collect_targets.assert_called_once_with(
                    window,
                    ("instance-a",),
                    stock_paths=None,
                )
                adapter_factory.assert_called_once_with(
                    window,
                    [target],
                    request_scope="multiple",
                )
                adapter.apply_selected_early_close.assert_called_once_with(
                    method,
                    source="main_routine_context_menu",
                    show_error_dialog=False,
                    show_result_toast=False,
                    show_confirmation=False,
                )
                expected_message = (
                    "조기마감 1종목 적용 합니다."
                    if label == gui_windows.ROUTINE_STATUS_EARLY_CLOSE
                    else f"지표추종매매 {label} 요청이 접수되었습니다."
                )
                toast.assert_called_once_with(
                    window,
                    expected_message,
                    duration_ms=2500,
                )

    def test_category_close_actions_use_one_stock_canonical_result(self) -> None:
        confirmation = MagicMock()
        confirmation.exec_.return_value = gui_windows.QMessageBox.Yes
        targets = [
            self._routine_close_target("005930", "instance-a"),
            self._routine_close_target("000660", "instance-b"),
        ]
        adapter = MagicMock()
        adapter.apply_selected_early_close.return_value = {
            "ok": True,
            "completed_count": 2,
            "failed_count": 0,
            "message": "",
        }
        window = SimpleNamespace(
            _routine_instance_ids_by_definition={
                "definition-a": ("instance-a", "instance-b")
            },
            _routine_instance_has_assigned_stocks=Mock(return_value=True),
            statusBar=Mock(return_value=SimpleNamespace(showMessage=Mock())),
            update_review_required_button_text=Mock(),
        )
        with (
            patch.object(gui_windows, "routine_instance_checked", return_value=True),
            patch.object(
                gui_windows.MainWindow,
                "_running_routine_operation_targets",
                return_value=targets,
            ) as collect_targets,
            patch.object(
                gui_windows,
                "_create_routine_operation_confirmation",
                return_value=confirmation,
            ),
            patch.object(
                gui_windows,
                "MainMonitoringStockOperationAdapter",
                return_value=adapter,
            ) as adapter_factory,
            patch.object(gui_windows, "show_toast") as toast,
        ):
            gui_windows.MainWindow.request_routine_definition_operation(
                window,
                "definition-a",
                "지표추종매매",
                "루틴",
                gui_windows.ROUTINE_STATUS_EARLY_CLOSE,
            )

        collect_targets.assert_called_once_with(
            window,
            ("instance-a", "instance-b"),
            stock_paths=None,
        )
        adapter_factory.assert_called_once_with(
            window,
            targets,
            request_scope="multiple",
        )
        adapter.apply_selected_early_close.assert_called_once()
        toast.assert_called_once_with(
            window,
            "조기마감 2종목 적용 합니다.",
            duration_ms=2500,
        )

    def test_group_and_instance_zero_running_targets_do_not_enter_backend(self) -> None:
        requests = (
            ("instance", "루틴", gui_windows.ROUTINE_STATUS_EARLY_CLOSE),
            ("instance", gui_windows.POLICY_MARKET, gui_windows.ROUTINE_STATUS_IMMEDIATE_LIQUIDATION),
            ("group", "루틴", gui_windows.ROUTINE_STATUS_EARLY_CLOSE),
            ("group", gui_windows.POLICY_MARKET, gui_windows.ROUTINE_STATUS_IMMEDIATE_LIQUIDATION),
        )
        for scope, method, label in requests:
            with self.subTest(scope=scope, method=method):
                window = SimpleNamespace(
                    _routine_instance_ids_by_definition={"definition-a": ("instance-a",)},
                    _routine_instance_has_assigned_stocks=Mock(return_value=True),
                    statusBar=Mock(return_value=SimpleNamespace(showMessage=Mock())),
                    update_review_required_button_text=Mock(),
                )
                with (
                    patch.object(gui_windows, "routine_instance_checked", return_value=True),
                    patch.object(
                        gui_windows.MainWindow,
                        "_running_routine_operation_targets",
                        return_value=[],
                    ),
                    patch.object(
                        gui_windows,
                        "_create_routine_operation_confirmation",
                    ) as confirmation,
                    patch.object(
                        gui_windows,
                        "MainMonitoringStockOperationAdapter",
                    ) as adapter_factory,
                    patch.object(gui_windows, "show_toast") as toast,
                ):
                    if scope == "group":
                        gui_windows.MainWindow.request_routine_definition_operation(
                            window, "definition-a", "그룹", method, label
                        )
                    else:
                        gui_windows.MainWindow.request_routine_operation(
                            window, "instance-a", "루틴", method, label
                        )

                confirmation.assert_not_called()
                adapter_factory.assert_not_called()
                self.assertIn("대상이 없습니다", toast.call_args.args[1])

    def test_instance_partial_success_uses_actual_result_counts(self) -> None:
        confirmation = MagicMock()
        confirmation.exec_.return_value = gui_windows.QMessageBox.Yes
        adapter = MagicMock()
        adapter.apply_selected_early_close.return_value = {
            "ok": False,
            "completed_count": 2,
            "failed_count": 1,
            "message": "차단 원인",
        }
        window = SimpleNamespace(
            statusBar=Mock(return_value=SimpleNamespace(showMessage=Mock())),
            update_review_required_button_text=Mock(),
        )
        with (
            patch.object(
                gui_windows.MainWindow,
                "_running_routine_operation_targets",
                return_value=[self._routine_close_target()],
            ),
            patch.object(
                gui_windows,
                "_create_routine_operation_confirmation",
                return_value=confirmation,
            ),
            patch.object(
                gui_windows,
                "MainMonitoringStockOperationAdapter",
                return_value=adapter,
            ),
            patch.object(gui_windows.QMessageBox, "warning") as warning,
            patch.object(gui_windows, "show_toast") as toast,
        ):
            gui_windows.MainWindow.request_routine_operation(
                window,
                "instance-a",
                "루틴",
                "루틴",
                gui_windows.ROUTINE_STATUS_EARLY_CLOSE,
            )

        self.assertIn("2건 접수 / 1건 차단", warning.call_args.args[2])
        toast.assert_not_called()

    def test_routine_recovery_global_block_does_not_write_any_stock(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dirs = []
            before = {}
            for index in range(8):
                stock_dir = root / f"{index:06d}_stock-{index}"
                stock_dir.mkdir()
                state_path = stock_dir / "state.json"
                state_path.write_text(
                    json.dumps(
                        {
                            "status": "MONITORING",
                            "review_required": False,
                        }
                    ),
                    encoding="utf-8",
                )
                stock_dirs.append(stock_dir)
                before[state_path] = state_path.read_bytes()

            decision = SimpleNamespace(
                allowed=False,
                reason_code=gui_windows.RECOVERY_CONTEXT_MISSING,
                evidence=("caller=EARLY_CLOSE_ROUTINE_INSTANCE",),
            )
            window = SimpleNamespace(
                _routine_instance_stock_dirs=Mock(return_value=stock_dirs),
                production_recovery_gate_for_stock=Mock(return_value=decision),
                kiwoom_api=SimpleNamespace(
                    login_session_id=Mock(return_value="")
                ),
                selected_account_no=Mock(return_value=""),
                update_runtime_stock_status=Mock(),
            )

            stdout = StringIO()
            stderr = StringIO()
            with (
                patch.object(gui_windows.LOGGER, "warning") as warning,
                patch.object(gui_windows.LOGGER, "exception") as exception,
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                allowed = (
                    gui_windows.MainWindow._production_recovery_allows_routine_operation(
                        window,
                        "instance-a",
                        command=gui_windows.MODE_EARLY_CLOSE,
                        caller_name="EARLY_CLOSE_ROUTINE_INSTANCE",
                    )
                )

            self.assertFalse(allowed)
            window.production_recovery_gate_for_stock.assert_called_once_with(
                "000000",
                caller_name="EARLY_CLOSE_ROUTINE_INSTANCE",
            )
            window.update_runtime_stock_status.assert_not_called()
            warning.assert_not_called()
            exception.assert_not_called()
            self.assertEqual("", stdout.getvalue())
            self.assertEqual("", stderr.getvalue())
            for state_path, content in before.items():
                self.assertEqual(content, state_path.read_bytes())

    def test_repeated_routine_recovery_blocks_never_write_review_state(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dirs = []
            before = {}
            for index in range(8):
                stock_dir = root / f"{index:06d}_stock-{index}"
                stock_dir.mkdir()
                state_path = stock_dir / "state.json"
                state_path.write_text(
                    json.dumps(
                        {
                            "status": "MONITORING",
                            "review_required": False,
                        }
                    ),
                    encoding="utf-8",
                )
                stock_dirs.append(stock_dir)
                before[state_path] = state_path.read_bytes()

            decision = SimpleNamespace(
                allowed=False,
                reason_code=gui_windows.RECOVERY_CONTEXT_MISSING,
                evidence=(),
            )
            window = SimpleNamespace(
                _routine_instance_stock_dirs=Mock(return_value=stock_dirs),
                production_recovery_gate_for_stock=Mock(return_value=decision),
                kiwoom_api=SimpleNamespace(
                    login_session_id=Mock(return_value="")
                ),
                selected_account_no=Mock(return_value=""),
                update_runtime_stock_status=Mock(),
            )
            caller_names = (
                "EARLY_CLOSE_ROUTINE_INSTANCE",
                "MARKET_EARLY_CLOSE_ROUTINE_INSTANCE",
                "EARLY_CLOSE_ROUTINE_INSTANCE",
            )

            with patch.object(gui_windows.LOGGER, "warning") as warning:
                for caller_name in caller_names:
                    self.assertFalse(
                        gui_windows.MainWindow._production_recovery_allows_routine_operation(
                            window,
                            "instance-a",
                            command=gui_windows.MODE_EARLY_CLOSE,
                            caller_name=caller_name,
                        )
                    )

            warning.assert_not_called()
            self.assertEqual(
                len(caller_names),
                window.production_recovery_gate_for_stock.call_count,
            )
            window.update_runtime_stock_status.assert_not_called()
            for state_path, content in before.items():
                self.assertEqual(content, state_path.read_bytes())

    def test_unknown_recovery_reason_keeps_diagnostic_log(self) -> None:
        stock_dir = Path("000000_stock")
        decision = SimpleNamespace(
            allowed=False,
            reason_code="RECOVERY_UNKNOWN_CONTRACT_STATE",
            evidence=(),
        )
        window = SimpleNamespace(
            _routine_instance_stock_dirs=Mock(return_value=[stock_dir]),
            production_recovery_gate_for_stock=Mock(return_value=decision),
            kiwoom_api=SimpleNamespace(
                login_session_id=Mock(return_value="login-a")
            ),
            selected_account_no=Mock(return_value="12345678"),
            update_runtime_stock_status=Mock(),
        )

        with patch.object(gui_windows.LOGGER, "warning") as warning:
            allowed = (
                gui_windows.MainWindow._production_recovery_allows_routine_operation(
                    window,
                    "instance-a",
                    command=gui_windows.MODE_EARLY_CLOSE,
                    caller_name="EARLY_CLOSE_ROUTINE_INSTANCE",
                )
            )

        self.assertFalse(allowed)
        warning.assert_called_once()
        window.update_runtime_stock_status.assert_not_called()

    def test_registry_error_evidence_keeps_diagnostic_log(self) -> None:
        stock_dir = Path("000000_stock")
        decision = SimpleNamespace(
            allowed=False,
            reason_code=gui_windows.RECOVERY_CONTEXT_MISSING,
            evidence=("registry_error=RuntimeError",),
        )
        window = SimpleNamespace(
            _routine_instance_stock_dirs=Mock(return_value=[stock_dir]),
            production_recovery_gate_for_stock=Mock(return_value=decision),
            kiwoom_api=SimpleNamespace(
                login_session_id=Mock(return_value="login-a")
            ),
            selected_account_no=Mock(return_value="12345678"),
            update_runtime_stock_status=Mock(),
        )

        with patch.object(gui_windows.LOGGER, "warning") as warning:
            allowed = (
                gui_windows.MainWindow._production_recovery_allows_routine_operation(
                    window,
                    "instance-a",
                    command=gui_windows.MODE_EARLY_CLOSE,
                    caller_name="MARKET_EARLY_CLOSE_ROUTINE_INSTANCE",
                )
            )

        self.assertFalse(allowed)
        warning.assert_called_once()
        window.update_runtime_stock_status.assert_not_called()

    def test_recovery_gate_exception_is_fail_closed_and_logged(self) -> None:
        window = SimpleNamespace(
            _routine_instance_stock_dirs=Mock(
                return_value=[Path("000000_stock")]
            ),
            production_recovery_gate_for_stock=Mock(
                side_effect=RuntimeError("registry failed")
            ),
            update_runtime_stock_status=Mock(),
        )

        with patch.object(gui_windows.LOGGER, "exception") as exception:
            allowed = (
                gui_windows.MainWindow._production_recovery_allows_routine_operation(
                    window,
                    "instance-a",
                    command=gui_windows.MODE_EARLY_CLOSE,
                    caller_name="EARLY_CLOSE_ROUTINE_INSTANCE",
                )
            )

        self.assertFalse(allowed)
        exception.assert_called_once()
        window.update_runtime_stock_status.assert_not_called()

    def test_routine_definition_recovery_block_uses_canonical_failure_result(self) -> None:
        confirmation = MagicMock()
        confirmation.exec_.return_value = gui_windows.QMessageBox.Yes
        target = self._routine_close_target()
        adapter = MagicMock()
        adapter.apply_selected_early_close.return_value = {
            "ok": False,
            "completed_count": 0,
            "failed_count": 1,
            "message": "서버 연결 및 계좌 상태를 확인하십시오.",
        }
        window = SimpleNamespace(
            _routine_instance_ids_by_definition={
                "indicator-follow": ("instance-a", "instance-b")
            },
            _routine_instance_has_assigned_stocks=Mock(return_value=True),
            update_review_required_button_text=Mock(),
            statusBar=Mock(
                return_value=SimpleNamespace(showMessage=Mock())
            ),
        )

        with (
            patch.object(
                gui_windows,
                "routine_instance_checked",
                return_value=True,
            ),
            patch.object(
                gui_windows,
                "_create_routine_operation_confirmation",
                return_value=confirmation,
            ),
            patch.object(
                gui_windows.MainWindow,
                "_running_routine_operation_targets",
                return_value=[target],
            ),
            patch.object(
                gui_windows,
                "MainMonitoringStockOperationAdapter",
                return_value=adapter,
            ),
            patch.object(gui_windows.QMessageBox, "warning") as warning,
            patch.object(gui_windows, "show_toast") as toast,
        ):
            gui_windows.MainWindow.request_routine_definition_operation(
                window,
                "indicator-follow",
                "지표추종매매",
                "루틴",
                gui_windows.ROUTINE_STATUS_EARLY_CLOSE,
            )

        adapter.apply_selected_early_close.assert_called_once()
        self.assertIn("계좌 상태", warning.call_args.args[2])
        toast.assert_not_called()

    def test_instance_restart_adapter_uses_shared_routine_block_formatter(self) -> None:
        expected = (
            "루틴 재시작 불가\n\n"
            "프로그램 시작 후 운영 상태 확인이 아직 완료되지 않았습니다.\n"
            "잠시 후 다시 시도해 주세요."
        )
        host = SimpleNamespace(
            filter_start_targets_by_recovery=Mock(
                return_value={
                    "allowed": False,
                    "reason": "RECOVERY_NOT_STARTED",
                    "user_message": "",
                }
            )
        )
        window = SimpleNamespace(
            routine_recovery_block_message=Mock(return_value=expected),
        )
        adapter = SimpleNamespace(
            _window=window,
            _recovery_action_label="루틴 재시작",
            _execution_host=Mock(return_value=host),
        )

        result = (
            context_menu.MainMonitoringStockOperationAdapter.filter_start_targets_by_recovery(
                adapter,
                [],
                action="운영시작",
            )
        )

        self.assertEqual(expected, result["user_message"])
        window.routine_recovery_block_message.assert_called_once_with("루틴 재시작")

    def test_recovery_states_have_actionable_messages_without_codes(self) -> None:
        api = SimpleNamespace(
            is_connected=Mock(return_value=True),
            login_session_id=Mock(return_value="login-a"),
        )
        window = SimpleNamespace(
            kiwoom_api=api,
            selected_account_no=Mock(return_value="12345678"),
        )
        cases = {
            gui_windows.RECOVERY_NOT_STARTED: "Recovery가 완료되지 않았습니다.",
            gui_windows.RECOVERY_IN_PROGRESS: "Recovery가 진행 중입니다.",
            gui_windows.RECOVERY_ACCOUNT_FAILED: "계좌 Recovery에 실패했습니다.",
            gui_windows.RECOVERY_STALE_SESSION: "현재 세션에서 사용할 수 없습니다.",
            gui_windows.RECOVERY_STOCK_PENDING: "종목의 Recovery가 아직 완료되지 않았습니다.",
            gui_windows.RECOVERY_STOCK_FAILED: "종목의 Recovery에 실패했습니다.",
        }

        for reason_code, expected in cases.items():
            with self.subTest(reason_code=reason_code):
                decision = SimpleNamespace(reason_code=reason_code, evidence=())
                message = (
                    gui_windows.MainWindow.production_recovery_block_user_message(
                        window,
                        decision,
                    )
                )
                self.assertIn(expected, message)
                self.assertNotIn(reason_code, message)

    def test_recovery_registry_read_failure_has_recovery_action(self) -> None:
        api = SimpleNamespace(
            is_connected=Mock(return_value=True),
            login_session_id=Mock(return_value="login-a"),
        )
        window = SimpleNamespace(
            kiwoom_api=api,
            selected_account_no=Mock(return_value="12345678"),
        )
        decision = SimpleNamespace(
            reason_code=gui_windows.RECOVERY_CONTEXT_MISSING,
            evidence=("registry_error=broken registry",),
        )

        message = gui_windows.MainWindow.production_recovery_block_user_message(
            window,
            decision,
        )

        self.assertIn("Recovery 데이터를 읽을 수 없습니다.", message)
        self.assertIn("복구를 다시 실행", message)
        self.assertNotIn("registry_error", message)

    def test_recovery_account_failure_uses_preserved_runtime_cause(self) -> None:
        api = SimpleNamespace(
            is_connected=Mock(return_value=True),
            login_session_id=Mock(return_value="login-a"),
        )
        window = SimpleNamespace(
            kiwoom_api=api,
            selected_account_no=Mock(return_value="12345678"),
            _production_recovery_failure_reason_code="DAMAGED_RUNTIME",
        )
        decision = SimpleNamespace(
            reason_code=gui_windows.RECOVERY_ACCOUNT_FAILED,
            evidence=(),
        )

        message = gui_windows.MainWindow.production_recovery_block_user_message(
            window,
            decision,
        )

        self.assertIn("Runtime 데이터를 읽을 수 없어", message)
        self.assertIn("검토관리", message)
        self.assertNotIn("DAMAGED_RUNTIME", message)

    def test_recovery_account_failure_uses_preserved_timer_cause(self) -> None:
        api = SimpleNamespace(
            is_connected=Mock(return_value=True),
            login_session_id=Mock(return_value="login-a"),
        )
        window = SimpleNamespace(
            kiwoom_api=api,
            selected_account_no=Mock(return_value="12345678"),
            _production_recovery_failure_reason_code="RECOVERY_TIMER_START_FAILED",
        )
        decision = SimpleNamespace(
            reason_code=gui_windows.RECOVERY_ACCOUNT_FAILED,
            evidence=(),
        )

        message = gui_windows.MainWindow.production_recovery_block_user_message(
            window,
            decision,
        )

        self.assertIn("운영 주기 실행을 시작하지 못했습니다.", message)
        self.assertIn("Recovery를 다시 실행", message)
        self.assertNotIn("RECOVERY_TIMER_START_FAILED", message)

    def test_runtime_timer_exception_hides_internal_exception(self) -> None:
        window = SimpleNamespace(
            _runtime_file_snapshot=(("state.json", 1, 1),),
            statusBarMessage=Mock(),
        )
        with (
            patch(
                "gui_auto_trade_setting_window.auto_trade_on_runtime_file_timer_tick",
                side_effect=RuntimeError("secret runtime error"),
            ),
            patch("gui_auto_trade_setting_window.LOGGER.exception"),
        ):
            gui_windows.AutoTradeSettingWindow.on_runtime_file_timer_tick(window)

        message = window.statusBarMessage.call_args.args[0]
        self.assertIn("Runtime 상태를 갱신하지 못했습니다.", message)
        self.assertNotIn("secret runtime error", message)

    def test_time_policy_timer_exception_hides_internal_exception(self) -> None:
        window = SimpleNamespace(
            _last_time_policy_gui_minute_key="10:00",
            statusBarMessage=Mock(),
        )
        with (
            patch(
                "gui_auto_trade_setting_window.auto_trade_on_time_policy_gui_timer_tick",
                side_effect=RuntimeError("secret timer error"),
            ),
            patch("gui_auto_trade_setting_window.LOGGER.exception"),
        ):
            gui_windows.AutoTradeSettingWindow.on_time_policy_timer_tick(window)

        message = window.statusBarMessage.call_args.args[0]
        self.assertIn("시간정책 상태를 갱신하지 못했습니다.", message)
        self.assertNotIn("secret timer error", message)

    def test_internal_reason_code_is_not_exposed_by_dialog_fallback(self) -> None:
        parent = QWidget()
        parent.routine_table = MagicMock()
        target = context_menu.MainMonitoringStockTarget(
            stock_dir=Path("stocks/005930_삼성전자"),
            code="005930",
            name="삼성전자",
            routine_instance_id="instance-a",
        )
        adapter = context_menu.MainMonitoringStockOperationAdapter(parent, [target])

        with (
            patch("gui_auto_trade_run_control.show_toast") as toast,
            patch("gui_auto_trade_run_control.QMessageBox.warning") as warning,
        ):
            shown = adapter.show_operation_failure_dialog(
                "운영시작",
                {"ok": False, "reason": "RECOVERY_CONTEXT_MISSING"},
            )

        self.assertTrue(shown)
        message = toast.call_args.kwargs["message"]
        self.assertNotIn("RECOVERY_CONTEXT_MISSING", message)
        self.assertIn("로그인, 계좌 및 운영 상태", message)
        warning.assert_not_called()
        adapter.close()
        parent.close()

    def test_review_required_failure_displays_stock_and_official_reason(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "000660_SK하이닉스"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text(
                json.dumps(
                    {
                        "status": "REVIEW_REQUIRED",
                        "review_reason": "보유수량 있음 + 현재가 확인 불가",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            parent = QWidget()
            parent.routine_table = MagicMock()
            target = context_menu.MainMonitoringStockTarget(
                stock_dir=stock_dir,
                code="000660",
                name="SK하이닉스",
                routine_instance_id="instance-a",
            )
            adapter = context_menu.MainMonitoringStockOperationAdapter(parent, [target])

            with (
                patch("gui_auto_trade_run_control.show_toast") as toast,
                patch("gui_auto_trade_run_control.QMessageBox.warning") as warning,
            ):
                shown = adapter.show_operation_failure_dialog(
                    "운영시작",
                    {"ok": False, "reason": "REVIEW_REQUIRED"},
                )

            self.assertTrue(shown)
            message = toast.call_args.kwargs["message"]
            self.assertIn("000660 SK하이닉스", message)
            self.assertIn("보유수량 있음 + 현재가 확인 불가", message)
            self.assertIn("검토관리", message)
            warning.assert_not_called()
            adapter.close()
            parent.close()

    def test_backend_warning_is_not_duplicated_by_monitoring_presenter(self) -> None:
        parent = QWidget()
        parent.routine_table = MagicMock()
        target = context_menu.MainMonitoringStockTarget(
            stock_dir=Path("stocks/000660_SK하이닉스"),
            code="000660",
            name="SK하이닉스",
            routine_instance_id="instance-a",
        )
        adapter = context_menu.MainMonitoringStockOperationAdapter(parent, [target])
        adapter._last_operation_failure_dialog_shown = True

        with (
            patch("gui_auto_trade_run_control.show_toast") as toast,
            patch("gui_auto_trade_run_control.QMessageBox.warning") as warning,
        ):
            shown = adapter.show_operation_failure_dialog(
                "운영시작",
                {"ok": False, "reason": "START_FAILED"},
            )

        self.assertFalse(shown)
        toast.assert_not_called()
        warning.assert_not_called()
        adapter.close()
        parent.close()

    def test_backend_exception_uses_critical_dialog_and_keeps_state(self) -> None:
        with TemporaryDirectory() as temp:
            stock_dir = Path(temp) / "005930_삼성전자"
            stock_dir.mkdir()
            state_path = stock_dir / "state.json"
            state_path.write_text(
                json.dumps({"status": "STOPPED", "trade_enabled": False}),
                encoding="utf-8",
            )
            status_bar = MagicMock()
            adapter = MagicMock()
            adapter.start_selected_auto_trades.side_effect = RuntimeError("backend failed")
            window = SimpleNamespace(
                _routine_instance_stock_dirs=lambda _instance_id: [stock_dir],
                _reload_main_routine_table_preserving_view=MagicMock(),
                statusBar=lambda: status_bar,
            )
            instance = SimpleNamespace(
                instance_id="instance-a",
                display_name="지표추종매매",
            )
            with (
                patch.object(gui_windows, "routine_instance_by_id", return_value=instance),
                patch.object(
                    gui_windows,
                    "MainMonitoringStockOperationAdapter",
                    return_value=adapter,
                ),
                patch.object(gui_windows.QMessageBox, "critical") as critical,
            ):
                gui_windows.MainWindow.toggle_routine_instance_operation(
                    window,
                    "instance-a",
                )

            critical.assert_called_once()
            self.assertNotIn("backend failed", critical.call_args.args[2])
            self.assertIn("로그를 확인한 뒤 다시 시도", critical.call_args.args[2])
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertFalse(table_loader.auto_trade_setting_trade_started(state))

    def test_review_stock_stale_trade_flag_does_not_choose_stop_backend(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            normal_dir = root / "005930_삼성전자"
            review_dir = root / "000660_SK하이닉스"
            normal_dir.mkdir()
            review_dir.mkdir()
            (normal_dir / "state.json").write_text(
                json.dumps({"status": "STOPPED", "trade_enabled": False}),
                encoding="utf-8",
            )
            review_before = {
                "status": "REVIEW_REQUIRED",
                "review_required": True,
                "trade_enabled": True,
            }
            (review_dir / "state.json").write_text(
                json.dumps(review_before, ensure_ascii=False),
                encoding="utf-8",
            )
            adapter = MagicMock()
            status_bar = MagicMock()
            window = SimpleNamespace(
                _routine_instance_stock_dirs=lambda _instance_id: [
                    review_dir,
                    normal_dir,
                ],
                _reload_main_routine_table_preserving_view=MagicMock(),
                statusBar=lambda: status_bar,
            )
            instance = SimpleNamespace(
                instance_id="instance-a",
                display_name="지표추종매매",
            )

            def start_instance() -> dict[str, object]:
                (normal_dir / "state.json").write_text(
                    json.dumps({"status": "MONITORING", "trade_enabled": True}),
                    encoding="utf-8",
                )
                return {
                    "ok": True,
                    "reason": "STARTED",
                    "excluded_review": ("000660 SK하이닉스",),
                }

            adapter.start_selected_auto_trades.side_effect = start_instance
            with (
                patch.object(gui_windows, "routine_instance_by_id", return_value=instance),
                patch.object(
                    gui_windows,
                    "MainMonitoringStockOperationAdapter",
                    return_value=adapter,
                ) as adapter_factory,
            ):
                gui_windows.MainWindow.toggle_routine_instance_operation(
                    window,
                    "instance-a",
                )

            adapter.start_selected_auto_trades.assert_called_once()
            targets = adapter_factory.call_args.args[1]
            self.assertEqual(
                {"005930"},
                {target.code for target in targets},
            )
            review_after = json.loads(
                (review_dir / "state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(review_before, review_after)

    def test_all_review_stocks_do_not_enter_start_backend_or_failure_toast(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            review_dirs = [
                root / "000660_SK하이닉스",
                root / "068270_셀트리온",
            ]
            for review_dir in review_dirs:
                review_dir.mkdir()
                (review_dir / "state.json").write_text(
                    json.dumps(
                        {
                            "status": "REVIEW_REQUIRED",
                            "review_required": True,
                            "trade_enabled": False,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

            reload_table = MagicMock()
            status_bar = MagicMock()
            window = SimpleNamespace(
                _routine_instance_stock_dirs=lambda _instance_id: review_dirs,
                _reload_main_routine_table_preserving_view=reload_table,
                statusBar=lambda: status_bar,
            )
            instance = SimpleNamespace(
                instance_id="instance-a",
                display_name="지표추종매매",
            )

            with (
                patch.object(gui_windows, "routine_instance_by_id", return_value=instance),
                patch.object(
                    gui_windows,
                    "MainMonitoringStockOperationAdapter",
                ) as adapter_factory,
                patch.object(
                    gui_windows,
                    "show_auto_trade_operation_failure_dialog",
                ) as show_failure,
            ):
                gui_windows.MainWindow.toggle_routine_instance_operation(
                    window,
                    "instance-a",
                )

            adapter_factory.assert_not_called()
            show_failure.assert_not_called()
            reload_table.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
