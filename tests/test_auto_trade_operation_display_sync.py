# -*- coding: utf-8 -*-

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QTableWidget

import gui_auto_trade_ats_ops
import gui_auto_trade_table_loader
import gui_main_table_loader
from gui_auto_trade_display import create_auto_trade_setting_activity_status_item
from gui_auto_trade_policy import auto_trade_operation_display
from manual_ats_runtime import (
    clear_manual_ats_runtime_selection,
    write_manual_ats_runtime_selection,
)
from runtime_io import read_json_dict


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _Header:
    def setSortIndicator(self, *_args) -> None:
        return None


class _Table:
    def __init__(self) -> None:
        self.row_count = 0
        self.items: dict[tuple[int, int], object] = {}

    def columnCount(self) -> int:
        return 10

    def setRowCount(self, count: int) -> None:
        self.row_count = count

    def setItem(self, row: int, column: int, item: object) -> None:
        self.items[(row, column)] = item

    def sortItems(self, *_args) -> None:
        return None

    def horizontalHeader(self) -> _Header:
        return _Header()


class _Item:
    def __init__(self, text: str = "") -> None:
        self._text = str(text)
        self.foreground = None
        self.background = None
        self.tooltip = ""

    def text(self) -> str:
        return self._text

    def setData(self, *_args) -> None:
        return None

    def setTextAlignment(self, *_args) -> None:
        return None

    def setForeground(self, color, *_args) -> None:
        self.foreground = color

    def setBackground(self, color, *_args) -> None:
        self.background = color

    def setToolTip(self, tooltip: str) -> None:
        self.tooltip = str(tooltip)


def _qcolor_name(color: object) -> str:
    if hasattr(color, "name"):
        return str(color.name()).lower()
    return str(color).lower()


def _item_foreground_name(item: object) -> str:
    foreground = getattr(item, "foreground", None)
    if callable(foreground):
        return _qcolor_name(foreground().color())
    return _qcolor_name(foreground)


def _item_background_name(item: object) -> str:
    background = getattr(item, "background", None)
    if callable(background):
        return _qcolor_name(background().color())
    return _qcolor_name(background)


def _item_tooltip(item: object) -> str:
    tooltip = getattr(item, "toolTip", None)
    if callable(tooltip):
        return str(tooltip())
    return str(getattr(item, "tooltip", ""))


def _main_table_operation_item(window: object) -> _Item:
    return window.running_stock_table.items[(0, 3)]


def _main_table_status_item(window: object) -> _Item:
    return window.running_stock_table.items[(0, 5)]


def _state_from_file(stock_dir: Path) -> dict[str, object]:
    state = read_json_dict(stock_dir / "state.json")
    if isinstance(state, dict):
        return state
    return {}


def _config_from_file(stock_dir: Path) -> dict[str, object]:
    config = read_json_dict(stock_dir / "config.json")
    if isinstance(config, dict):
        return config
    return {}


def _assert_operation_cell_matches_formatter(
    test_case: unittest.TestCase,
    stock_dir: Path,
    item: _Item,
) -> None:
    config = _config_from_file(stock_dir)
    state = _state_from_file(stock_dir)
    expected_text, expected_color, expected_tooltip, _labels = (
        auto_trade_operation_display(config, state)
    )
    test_case.assertEqual(expected_text, item.text())
    test_case.assertEqual(expected_color.lower(), _item_foreground_name(item))
    if expected_tooltip:
        test_case.assertEqual(expected_tooltip, _item_tooltip(item))


def _assert_activity_style_applied(test_case: unittest.TestCase, item: _Item) -> None:
    test_case.assertIsNotNone(getattr(item, "background", None))
    test_case.assertIn(
        _item_background_name(item),
        {"#ffffff", "#f4f5f7"},
    )
    if _item_background_name(item) == "#f4f5f7":
        test_case.assertEqual("#afb2b9", _item_foreground_name(item))


def _fake_activity_status_item(value: object, active: bool) -> _Item:
    item = _Item(str(value))
    item.setBackground("#ffffff" if active else "#f4f5f7")
    if not active:
        item.setForeground("#afb2b9")
    return item


def _item_style_tuple(item: object) -> tuple[str, str, str, bool, bool, int, int, str, int]:
    font = item.font()
    return (
        item.text(),
        _qcolor_name(item.foreground().color()),
        _qcolor_name(item.background().color()),
        bool(font.bold()),
        bool(font.italic()),
        int(font.pointSize()),
        int(item.textAlignment()),
        item.toolTip(),
        int(item.flags()),
    )


def _runtime_ats_state(*keys: str) -> dict[str, object]:
    return {
        "manual_ats_selection": {
            "selected_sessions": list(keys),
            "trade_date": datetime.now().astimezone().date().isoformat(),
            "program_session_id": "test-program-session",
        }
    }


class AutoTradeOperationDisplaySyncTest(unittest.TestCase):
    def test_both_loaders_use_the_same_operation_display_helper(self) -> None:
        self.assertIs(
            gui_auto_trade_table_loader.auto_trade_operation_display,
            gui_main_table_loader.auto_trade_operation_display,
        )
        self.assertIs(
            gui_auto_trade_table_loader.create_auto_trade_operation_item,
            gui_main_table_loader.create_auto_trade_operation_item,
        )
        self.assertIs(
            gui_auto_trade_table_loader.create_auto_trade_situation_item,
            gui_main_table_loader.create_auto_trade_situation_item,
        )
        self.assertIs(
            gui_auto_trade_table_loader.create_auto_trade_setting_activity_status_item,
            gui_main_table_loader.create_auto_trade_setting_activity_status_item,
        )
        self.assertIs(
            gui_auto_trade_table_loader.create_auto_trade_stock_name_item,
            gui_main_table_loader.create_auto_trade_stock_name_item,
        )

    def test_review_required_situation_light_is_red_before_other_conditions(self) -> None:
        from gui_auto_trade_situation import create_auto_trade_situation_item

        cases = (
            (
                {"status": "REVIEW_REQUIRED"},
                False,
                "현황: 검토관리 - 운영자 확인 필요",
            ),
            (
                {
                    "status": "REVIEW_REQUIRED",
                    "server_holding_qty": 5,
                    "holding_qty": 0,
                },
                True,
                "현황: 검토관리 - 운영자 확인 필요",
            ),
            (
                {
                    "status": "STOPPED",
                    "review_required": True,
                    "operation_notice": "EARLY_CLOSE_NO_TARGET",
                },
                True,
                "현황: 검토관리 - 운영자 확인 필요",
            ),
            ({"status": "EMERGENCY_STOPPED"}, True, "현황: 긴급정지 - 운영자 확인 필요"),
        )

        for state, trade_started, tooltip in cases:
            with self.subTest(state=state, trade_started=trade_started):
                item = create_auto_trade_situation_item(
                    state,
                    trade_started,
                    "검토종목",
                )

                self.assertEqual("#dc2626", _qcolor_name(item.foreground().color()))
                self.assertEqual(tooltip, item.toolTip())

    def test_normal_situation_light_contract_remains_unchanged_without_orange(self) -> None:
        from gui_auto_trade_situation import create_auto_trade_situation_item

        cases = (
            ({"status": "STOPPED"}, False, "#9ca3af"),
            (
                {
                    "status": "MONITORING",
                    "operation_notice": "EARLY_CLOSE_NO_TARGET",
                    "holding_qty": 0,
                    "sell_pending_qty": 0,
                },
                True,
                "#16a34a",
            ),
            ({"status": "RUNNING"}, True, "#16a34a"),
        )

        for state, trade_started, color in cases:
            with self.subTest(state=state, trade_started=trade_started):
                item = create_auto_trade_situation_item(
                    state,
                    trade_started,
                    "",
                )

                self.assertEqual(color, _qcolor_name(item.foreground().color()))

    def test_data_mismatch_situation_light_is_red(self) -> None:
        from gui_auto_trade_situation import create_auto_trade_situation_item

        item = create_auto_trade_situation_item(
            {"status": "RUNNING", "holding_qty": 1},
            True,
            "매수/매도",
        )

        self.assertEqual("#dc2626", _qcolor_name(item.foreground().color()))

    def test_setting_loader_preserves_review_required_situation_light_color(self) -> None:
        app = QApplication.instance() or QApplication([])
        _ = app
        with tempfile.TemporaryDirectory() as temp_dir:
            stock_dir = Path(temp_dir) / "000660_SK하이닉스"
            stock_dir.mkdir()
            (stock_dir / "config.json").write_text(
                json.dumps(
                    {
                        "operation_mode": "CONTINUOUS",
                        "assigned_routine_instance_id": "instance-a",
                        "operation_excluded": True,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
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
            window = SimpleNamespace(
                stock_table=QTableWidget(),
                _all_stocks_scope_active=True,
                _stock_status_filter="all",
                _stock_visual_order=[],
                current_selected_routine_dir=lambda: None,
                current_selected_routine_name=lambda: "",
                capture_stock_table_view_state=lambda: (set(), 0),
                restore_stock_table_view_state=lambda *_args: None,
                update_selected_routine_status_bar=lambda: None,
                all_registered_instance_ids=lambda: ["instance-a"],
                update_action_buttons=lambda: None,
            )
            window.stock_table.setColumnCount(11)
            stock = {
                "code": "000660",
                "name": "SK하이닉스",
                "stock_path": str(stock_dir),
                "assigned_routine_instance_id": "instance-a",
            }

            with (
                patch.object(gui_auto_trade_table_loader, "read_base_stocks", return_value=[stock]),
                patch.object(
                    gui_auto_trade_table_loader,
                    "pending_order_side_quantities",
                    return_value=(0, 0),
                ),
            ):
                gui_auto_trade_table_loader.auto_trade_load_selected_routine_stocks(window)

            situation_item = window.stock_table.item(0, 3)
            name_item = window.stock_table.item(0, 1)
            status_item = window.stock_table.item(0, 4)
            self.assertIsNotNone(situation_item)
            self.assertIsNotNone(name_item)
            self.assertIsNotNone(status_item)
            self.assertEqual("#dc2626", _qcolor_name(situation_item.foreground().color()))
            self.assertEqual("#ff8c00", _qcolor_name(name_item.foreground().color()))
            self.assertEqual("#ff8c00", _qcolor_name(status_item.foreground().color()))

    def test_setting_status_sort_rank_separates_emergency_and_review(self) -> None:
        from gui_auto_trade_display import SORT_ROLE

        app = QApplication.instance() or QApplication([])
        _ = app
        with tempfile.TemporaryDirectory() as temp_dir:
            stock_specs = (
                ("000001_Normal", "000001", "Normal", {"status": "STOPPED"}),
                ("000002_Emergency", "000002", "Emergency", {"status": "EMERGENCY_STOPPED"}),
                (
                    "000003_Review",
                    "000003",
                    "Review",
                    {"status": "REVIEW_REQUIRED", "review_required": True},
                ),
            )
            stocks = []
            for folder_name, code, name, state in stock_specs:
                stock_dir = Path(temp_dir) / folder_name
                stock_dir.mkdir()
                (stock_dir / "config.json").write_text(
                    json.dumps(
                        {
                            "operation_mode": "CONTINUOUS",
                            "assigned_routine_instance_id": "instance-a",
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                (stock_dir / "state.json").write_text(
                    json.dumps(state, ensure_ascii=False),
                    encoding="utf-8",
                )
                stocks.append(
                    {
                        "code": code,
                        "name": name,
                        "stock_path": str(stock_dir),
                        "assigned_routine_instance_id": "instance-a",
                    }
                )
            window = SimpleNamespace(
                stock_table=QTableWidget(),
                _all_stocks_scope_active=True,
                _stock_status_filter="all",
                _stock_visual_order=[],
                current_selected_routine_dir=lambda: None,
                current_selected_routine_name=lambda: "",
                capture_stock_table_view_state=lambda: (set(), 0),
                restore_stock_table_view_state=lambda *_args: None,
                update_selected_routine_status_bar=lambda: None,
                all_registered_instance_ids=lambda: ["instance-a"],
                update_action_buttons=lambda: None,
            )
            window.stock_table.setColumnCount(11)

            with (
                patch.object(gui_auto_trade_table_loader, "read_base_stocks", return_value=stocks),
                patch.object(
                    gui_auto_trade_table_loader,
                    "pending_order_side_quantities",
                    return_value=(0, 0),
                ),
            ):
                gui_auto_trade_table_loader.auto_trade_load_selected_routine_stocks(window)

            rank_by_status = {}
            for row in range(window.stock_table.rowCount()):
                status_item = window.stock_table.item(row, 4)
                rank_by_status[status_item.text()] = status_item.data(SORT_ROLE)

            self.assertEqual(0, rank_by_status["감시/대기"])
            self.assertEqual(4, rank_by_status["긴급정지"])
            self.assertEqual(5, rank_by_status["검토종목"])
            self.assertNotEqual(rank_by_status["긴급정지"], rank_by_status["검토종목"])

    def test_setting_situation_and_status_columns_sort_independently(self) -> None:
        app = QApplication.instance() or QApplication([])
        _ = app

        def build_window() -> SimpleNamespace:
            window = SimpleNamespace(
                stock_table=QTableWidget(),
                _all_stocks_scope_active=True,
                _stock_status_filter="all",
                _stock_visual_order=[],
                current_selected_routine_dir=lambda: None,
                current_selected_routine_name=lambda: "",
                capture_stock_table_view_state=lambda: (set(), 0),
                restore_stock_table_view_state=lambda *_args: None,
                update_selected_routine_status_bar=lambda: None,
                all_registered_instance_ids=lambda: ["instance-a"],
                update_action_buttons=lambda: None,
            )
            window.stock_table.setColumnCount(11)
            return window

        def load_window(temp_dir: str) -> SimpleNamespace:
            stock_specs = (
                (
                    "000001_Mismatch",
                    "000001",
                    "Mismatch",
                    {"status": "STOPPED", "holding_qty": 0, "avg_price": 100},
                ),
                (
                    "000002_Running",
                    "000002",
                    "Running",
                    {"status": "RUNNING", "trade_started": True},
                ),
            )
            stocks = []
            for folder_name, code, name, state in stock_specs:
                stock_dir = Path(temp_dir) / folder_name
                stock_dir.mkdir(exist_ok=True)
                (stock_dir / "config.json").write_text(
                    json.dumps(
                        {
                            "operation_mode": "CONTINUOUS",
                            "assigned_routine_instance_id": "instance-a",
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                (stock_dir / "state.json").write_text(
                    json.dumps(state, ensure_ascii=False),
                    encoding="utf-8",
                )
                stocks.append(
                    {
                        "code": code,
                        "name": name,
                        "stock_path": str(stock_dir),
                        "assigned_routine_instance_id": "instance-a",
                    }
                )
            window = build_window()
            with (
                patch.object(gui_auto_trade_table_loader, "read_base_stocks", return_value=stocks),
                patch.object(
                    gui_auto_trade_table_loader,
                    "pending_order_side_quantities",
                    return_value=(0, 0),
                ),
            ):
                gui_auto_trade_table_loader.auto_trade_load_selected_routine_stocks(window)
            return window

        def codes(window: SimpleNamespace) -> list[str]:
            return [
                window.stock_table.item(row, 0).text()
                for row in range(window.stock_table.rowCount())
            ]

        with tempfile.TemporaryDirectory() as temp_dir:
            situation_asc = load_window(temp_dir)
            situation_asc.stock_table.sortItems(3, Qt.AscendingOrder)
            status_asc = load_window(temp_dir)
            status_asc.stock_table.sortItems(4, Qt.AscendingOrder)

            situation_desc = load_window(temp_dir)
            situation_desc.stock_table.sortItems(3, Qt.DescendingOrder)
            status_desc = load_window(temp_dir)
            status_desc.stock_table.sortItems(4, Qt.DescendingOrder)

            self.assertEqual(["000002", "000001"], codes(situation_asc))
            self.assertEqual(["000001", "000002"], codes(situation_desc))
            self.assertEqual(["000001", "000002"], codes(status_asc))
            self.assertEqual(["000002", "000001"], codes(status_desc))

    def test_manual_and_scheduled_display_contract(self) -> None:
        continuous = {"operation_mode": "CONTINUOUS"}
        scheduled = {
            "operation_mode": "SCHEDULED",
            "start_time": "09:30:00",
            "end_buy_time": "13:23:00",
        }

        self.assertEqual("수동", auto_trade_operation_display(continuous, {})[0])
        self.assertEqual(
            "수동",
            auto_trade_operation_display(
                continuous,
                {"manual_ats_selection": {"selected_sessions": []}},
            )[0],
        )
        self.assertEqual(
            "수동+ATS",
            auto_trade_operation_display(
                continuous,
                _runtime_ats_state("extra1"),
            )[0],
        )
        self.assertEqual(
            "09:30~13:23",
            auto_trade_operation_display(scheduled, {})[0],
        )
        self.assertEqual(
            "09:30~13:23",
            auto_trade_operation_display(
                scheduled,
                _runtime_ats_state("extra1"),
            )[0],
        )

    def test_liquidation_completed_today_suppresses_ats_display(self) -> None:
        state = _runtime_ats_state("extra1")
        state["liquidation_completed_at"] = (
            datetime.now().astimezone().isoformat(timespec="seconds")
        )

        self.assertEqual(
            "수동",
            auto_trade_operation_display(
                {"operation_mode": "CONTINUOUS"},
                state,
            )[0],
        )

    def test_actual_manual_ats_stock_metadata_matches_shared_display(self) -> None:
        for relative_path in ("stocks/003550_LG", "stocks/005380_현대차"):
            with self.subTest(stock=relative_path):
                stock_dir = PROJECT_ROOT / relative_path
                config = read_json_dict(stock_dir / "config.json")
                persisted_state = read_json_dict(stock_dir / "state.json")
                display_state = dict(persisted_state)
                display_state["manual_ats_selection"] = (
                    _runtime_ats_state("extra1")["manual_ats_selection"]
                )

                self.assertEqual("CONTINUOUS", config.get("operation_mode"))
                self.assertEqual(
                    "수동+ATS",
                    auto_trade_operation_display(config, display_state)[0],
                )

    def test_ats_save_and_clear_refresh_display_from_read_back(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stock_dir = Path(temp_dir) / "005930_test"
            stock_dir.mkdir()
            (stock_dir / "state.json").write_text("{}", encoding="utf-8")
            config = {"operation_mode": "CONTINUOUS"}

            self.assertTrue(
                write_manual_ats_runtime_selection(
                    stock_dir,
                    {"extra1": True},
                )
            )
            self.assertEqual(
                "수동+ATS",
                auto_trade_operation_display(
                    config,
                    read_json_dict(stock_dir / "state.json"),
                )[0],
            )

            self.assertTrue(clear_manual_ats_runtime_selection(stock_dir))
            self.assertEqual(
                "수동",
                auto_trade_operation_display(
                    config,
                    read_json_dict(stock_dir / "state.json"),
                )[0],
            )

    def test_main_running_table_uses_current_session_ats_display(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stock_dir = root / "stocks" / "003550_LG"
            stock_dir.mkdir(parents=True)
            (stock_dir / "config.json").write_text(
                json.dumps(
                    {
                        "routine": "지표추종매매",
                        "operation_mode": "CONTINUOUS",
                        "assigned_routine_instance_id": "instance-a",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (stock_dir / "state.json").write_text(
                json.dumps({"status": "MONITORING", "holding_qty": 0}),
                encoding="utf-8",
            )
            stock = {
                "code": "003550",
                "name": "LG",
                "stock_path": str(stock_dir),
                "routines": ["지표추종매매"],
                "assigned_routine_instance_id": "instance-a",
            }
            instance = SimpleNamespace(
                instance_id="instance-a",
                display_name="지표추종매매",
            )
            window = SimpleNamespace(
                running_stock_table=_Table(),
                _main_running_sort_column=-1,
                _main_running_sort_order=0,
                startup_recovery_session_ready=lambda **_kwargs: True,
            )

            with (
                patch.object(gui_main_table_loader, "read_base_stocks", return_value=[stock]),
                patch.object(
                    gui_main_table_loader,
                    "load_persisted_routine_instances",
                    return_value=[instance],
                ),
                patch.object(gui_main_table_loader, "SortableTableWidgetItem", _Item),
                patch.object(
                    gui_main_table_loader,
                    "create_auto_trade_situation_item",
                    side_effect=lambda *_args, **_kwargs: _Item("-"),
                ),
                patch.object(
                    gui_main_table_loader,
                    "create_auto_trade_setting_activity_status_item",
                    side_effect=_fake_activity_status_item,
                ),
                patch.object(
                    gui_main_table_loader,
                    "pending_order_side_quantities",
                    return_value=(0, 0),
                ),
            ):
                gui_main_table_loader.main_load_running_stock_table(window)
                self.assertEqual(
                    "수동",
                    _main_table_operation_item(window).text(),
                )
                _assert_operation_cell_matches_formatter(
                    self,
                    stock_dir,
                    _main_table_operation_item(window),
                )
                _assert_activity_style_applied(
                    self,
                    _main_table_status_item(window),
                )

                self.assertTrue(
                    write_manual_ats_runtime_selection(
                        stock_dir,
                        {"extra1": True},
                    )
                )
                gui_main_table_loader.main_load_running_stock_table(window)
                self.assertEqual(
                    "수동+ATS",
                    _main_table_operation_item(window).text(),
                )
                _assert_operation_cell_matches_formatter(
                    self,
                    stock_dir,
                    _main_table_operation_item(window),
                )
                _assert_activity_style_applied(
                    self,
                    _main_table_status_item(window),
                )

                self.assertTrue(clear_manual_ats_runtime_selection(stock_dir))
                gui_main_table_loader.main_load_running_stock_table(window)
                self.assertEqual(
                    "수동",
                    _main_table_operation_item(window).text(),
                )
                _assert_operation_cell_matches_formatter(
                    self,
                    stock_dir,
                    _main_table_operation_item(window),
                )

    def test_main_running_table_applies_auto_trade_setting_style_to_real_items(self) -> None:
        app = QApplication.instance() or QApplication([])
        self.assertIsNotNone(app)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stock_dir = root / "stocks" / "003550_LG"
            stock_dir.mkdir(parents=True)
            config = {
                "routine": "지표추종매매",
                "operation_mode": "CONTINUOUS",
                "assigned_routine_instance_id": "instance-a",
            }
            state = {
                "status": "MONITORING",
                "holding_qty": 0,
                "trade_enabled": False,
            }
            (stock_dir / "config.json").write_text(
                json.dumps(config, ensure_ascii=False),
                encoding="utf-8",
            )
            (stock_dir / "state.json").write_text(
                json.dumps(state, ensure_ascii=False),
                encoding="utf-8",
            )
            write_manual_ats_runtime_selection(stock_dir, {"extra1": True})
            stock = {
                "code": "003550",
                "name": "LG",
                "stock_path": str(stock_dir),
                "routines": ["지표추종매매"],
                "assigned_routine_instance_id": "instance-a",
            }
            instance = SimpleNamespace(
                instance_id="instance-a",
                display_name="지표추종매매",
            )
            window = SimpleNamespace(
                running_stock_table=QTableWidget(),
                _main_running_sort_column=-1,
                _main_running_sort_order=0,
                startup_recovery_session_ready=lambda **_kwargs: True,
            )
            window.running_stock_table.setColumnCount(10)

            with (
                patch.object(gui_main_table_loader, "read_base_stocks", return_value=[stock]),
                patch.object(
                    gui_main_table_loader,
                    "load_persisted_routine_instances",
                    return_value=[instance],
                ),
                patch.object(
                    gui_main_table_loader,
                    "pending_order_side_quantities",
                    return_value=(0, 0),
                ),
            ):
                gui_main_table_loader.main_load_running_stock_table(window)

            operation_item = window.running_stock_table.item(0, 3)
            situation_item = window.running_stock_table.item(0, 4)
            status_item = window.running_stock_table.item(0, 5)
            self.assertIsNotNone(operation_item)
            self.assertIsNotNone(situation_item)
            self.assertIsNotNone(status_item)

            expected_operation_text, expected_operation_color, expected_tooltip, _labels = (
                auto_trade_operation_display(
                    config,
                    read_json_dict(stock_dir / "state.json"),
                )
            )
            self.assertEqual(expected_operation_text, operation_item.text())
            self.assertEqual(
                expected_operation_color.lower(),
                _qcolor_name(operation_item.foreground().color()),
            )
            self.assertEqual(expected_tooltip, operation_item.toolTip())

            expected_situation = gui_main_table_loader.create_auto_trade_situation_item(
                read_json_dict(stock_dir / "state.json"),
                False,
                "감시/대기",
            )
            self.assertEqual(
                _item_style_tuple(expected_situation),
                _item_style_tuple(situation_item),
            )

            expected_status = create_auto_trade_setting_activity_status_item(
                "감시/대기",
                False,
            )
            self.assertEqual(
                _item_style_tuple(expected_status),
                _item_style_tuple(status_item),
            )

    def test_ats_save_success_refreshes_parent_monitoring_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            stock_dir = Path(temp_dir) / "003550_LG"
            stock_dir.mkdir()
            (stock_dir / "config.json").write_text(
                json.dumps({"operation_mode": "CONTINUOUS"}),
                encoding="utf-8",
            )
            (stock_dir / "state.json").write_text("{}", encoding="utf-8")
            parent = MagicMock()
            window = MagicMock()
            window.parent.return_value = parent
            window.capture_stock_table_view_state.return_value = (set(), 0)
            window.current_runtime_file_signature.return_value = ()

            result = gui_auto_trade_ats_ops.auto_trade_save_manual_ats_state_for_targets(
                window,
                [(stock_dir, "003550", "LG")],
                {"extra1": True, "extra2": False, "extra3": False},
            )

        self.assertEqual(1, result["succeeded"])
        window.load_selected_routine_stocks.assert_called_once()
        parent.refresh_all.assert_called_once()

    def test_after_regular_end_cleanup_does_not_create_legacy_permission_keys(self) -> None:
        app = QApplication.instance() or QApplication([])
        _ = app
        with tempfile.TemporaryDirectory() as temp_dir:
            stock_dir = Path(temp_dir) / "005930_삼성전자"
            stock_dir.mkdir()
            (stock_dir / "config.json").write_text(
                json.dumps(
                    {
                        "operation_mode": "SCHEDULED",
                        "assigned_routine_instance_id": "instance-a",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (stock_dir / "state.json").write_text(
                json.dumps(
                    {
                        "status": "EARLY_CLOSE",
                        "trade_enabled": True,
                        "early_close_requested_at": "2026-08-10 13:20:00",
                        "early_close_method": "루틴",
                        "operation_notice": "EARLY_CLOSE_WAITING",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            window = SimpleNamespace(
                stock_table=QTableWidget(),
                _all_stocks_scope_active=True,
                _stock_status_filter="all",
                _stock_visual_order=[],
                current_selected_routine_dir=lambda: None,
                current_selected_routine_name=lambda: "",
                capture_stock_table_view_state=lambda: (set(), 0),
                restore_stock_table_view_state=lambda *_args: None,
                update_selected_routine_status_bar=lambda: None,
                all_registered_instance_ids=lambda: ["instance-a"],
                update_action_buttons=lambda: None,
            )
            window.stock_table.setColumnCount(11)
            stock = {
                "code": "005930",
                "name": "삼성전자",
                "stock_path": str(stock_dir),
                "assigned_routine_instance_id": "instance-a",
            }

            with (
                patch.object(
                    gui_auto_trade_table_loader,
                    "read_base_stocks",
                    return_value=[stock],
                ),
                patch.object(
                    gui_auto_trade_table_loader,
                    "pending_order_side_quantities",
                    return_value=(0, 0),
                ),
                patch.object(
                    gui_auto_trade_table_loader,
                    "auto_trade_setting_is_after_regular_end",
                    return_value=True,
                ),
            ):
                gui_auto_trade_table_loader.auto_trade_load_selected_routine_stocks(
                    window
                )

            saved = read_json_dict(stock_dir / "state.json")
            self.assertEqual("MONITORING", saved["status"])
            self.assertEqual("WAIT_BUY", saved["trade_set_status"])
            self.assertEqual("", saved["early_close_requested_at"])
            self.assertNotIn("buy_enabled", saved)
            self.assertNotIn("sell_enabled", saved)


if __name__ == "__main__":
    unittest.main()
