# -*- coding: utf-8 -*-

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import inspect
import json
from pathlib import Path
from types import MethodType, SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, call, patch

import gui_auto_trade_context_menu as common_menu
import gui_main_stock_context_menu as main_context_menu
import gui_main_table_loader as main_table_loader
import gui_windows
import mock_validation_context_menu as mock_context_menu
from gui_auto_trade_display import RatioMetricDisplay
from gui_auto_trade_context_menu import StockContextMenuCallbacks
from gui_event_record_window import EventRecordPrototypeWindow
from gui_windows import MainWindow
from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
)
from mock_validation_contract import MockValidationError, instance_effective_settings
from mock_validation_host import MockValidationHost
from mock_validation_reference_snapshot import build_mock_reference_snapshot
from mock_validation_ui_actions import MockValidationUIActions
from mock_validation_ui_projection import (
    MockEventReaderAdapter,
    current_mock_monitoring_trees,
    current_mock_projections,
    mock_badge_count,
)
from tests.test_mock_validation_market_data import _book, _trade_payload


SEOUL = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 3, 10, 0, 0, tzinfo=SEOUL)


class _Signal:
    def __init__(self) -> None:
        self.callbacks = []

    def connect(self, callback) -> None:
        self.callbacks.append(callback)

    def disconnect(self, callback) -> None:
        self.callbacks.remove(callback)

    def emit(self, payload=None) -> None:
        for callback in tuple(self.callbacks):
            callback(payload)


class _Api:
    def __init__(self) -> None:
        self.mock_orderbook_received = _Signal()
        self.realtime_shadow_tick_received = _Signal()
        self.login_state_changed = _Signal()
        self.targets = ()
        self.sync_calls = []
        self.clear_calls = []
        self.registration = SimpleNamespace(
            active=True,
            connection_epoch=1,
            login_session_id="SESSION-1",
            target_stock_codes=(),
        )

    def is_connected(self):
        return True

    def sync_mock_orderbook_registration(self, stock_codes):
        self.targets = tuple(sorted(set(stock_codes)))
        self.sync_calls.append(self.targets)
        self.registration = SimpleNamespace(
            active=bool(self.targets),
            connection_epoch=1,
            login_session_id="SESSION-1",
            target_stock_codes=self.targets,
        )
        return {"ok": True, "active": bool(self.targets), "snapshot": self.registration}

    def mock_orderbook_registration_snapshot(self):
        return self.registration

    def clear_mock_orderbook_registration(self, **kwargs):
        self.clear_calls.append(kwargs)
        self.targets = ()
        self.registration = SimpleNamespace(
            active=False,
            connection_epoch=1,
            login_session_id="SESSION-1",
            target_stock_codes=(),
        )
        return {"ok": True}


class _MenuAction:
    def __init__(self, text="") -> None:
        self._text = text
        self._enabled = True
        self._checkable = False
        self._checked = False
        self._properties = {}

    def text(self):
        return self._text

    def setText(self, text):
        self._text = str(text)

    def setEnabled(self, enabled):
        self._enabled = bool(enabled)

    def isEnabled(self):
        return self._enabled

    def setCheckable(self, value):
        self._checkable = bool(value)

    def setChecked(self, value):
        self._checked = bool(value)

    def isChecked(self):
        return self._checked

    def setIcon(self, _icon):
        return None

    def setProperty(self, name, value):
        self._properties[str(name)] = value

    def property(self, name):
        return self._properties.get(str(name))

    def setToolTip(self, _value):
        return None

    def setStatusTip(self, _value):
        return None


class _Menu:
    chosen_text = ""
    chosen_menu_title = None
    root = None

    def __init__(self, _parent=None, title="") -> None:
        self.title = title
        self.actions = []
        self.submenus = []
        self._enabled = True
        if not title:
            _Menu.root = self

    def setToolTipsVisible(self, _visible):
        return None

    def addMenu(self, title):
        value = _Menu(title=title)
        self.submenus.append(value)
        return value

    def addAction(self, text):
        value = _MenuAction(text)
        self.actions.append(value)
        return value

    def addSeparator(self):
        return self.addAction("<separator>")

    def setEnabled(self, enabled):
        self._enabled = bool(enabled)

    def isEnabled(self):
        return self._enabled

    def exec_(self, _position):
        pending = [self]
        menus = []
        while pending:
            menu = pending.pop(0)
            menus.append(menu)
            pending.extend(menu.submenus)
        for menu in menus:
            if self.chosen_menu_title is not None and menu.title != self.chosen_menu_title:
                continue
            for action in menu.actions:
                if action.text() == self.chosen_text:
                    return action
        return None


def _reference(
    stock_code="005930",
    instance_ids=("A", "B", "C"),
    *,
    include_display=True,
    stock_name="삼성전자",
):
    display_contract = {
        "initial_buy": {
            "mode": "QUANTITY",
            "badge": "주수",
            "value": 1,
            "value_text": "1주",
        },
        "operation_schedule": {"display_text": "09:00~13:30"},
        "liquidation": {"display_text": "5분/시장가"},
    }
    return build_mock_reference_snapshot(
        stock={
            "code": stock_code,
            "name": stock_name,
            "stock_path": f"stocks/{stock_code}_{stock_name}",
        },
        routine_instances=[
            {
                "instance_id": instance_id,
                "definition_id": "indicator_follow",
                "routine_type": "INDICATOR_FOLLOW",
                "display_name": f"루틴 {instance_id}",
            }
            for instance_id in instance_ids
        ],
        rules_by_instance_id={
            instance_id: {"version": 1, "instance": instance_id}
            for instance_id in instance_ids
        },
        display_contract=display_contract if include_display else None,
        created_at=NOW.isoformat(),
    )


class MockValidationHostUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project_root = Path(self.temporary.name) / "project"
        self.project_root.mkdir()
        self.api = _Api()
        self.clock = {"now": NOW}
        self.changed = Mock()
        self.host = MockValidationHost(
            self.api,
            project_root=self.project_root,
            now_factory=lambda: self.clock["now"],
            projection_changed=self.changed,
            operation_policy_provider=lambda: {
                "regular_market": {"end_time": "15:30:00"},
                "liquidation": {
                    "minutes_before_regular_close": "5",
                    "method": "시장가",
                },
                "review_policy": {"long_term_holding_enabled": False},
            },
            candles_provider=lambda _document: [],
        )
        self.addCleanup(self.host.dispose)
        self.actions = MockValidationUIActions(self.host)

    def create(self, stock_code="005930"):
        return self.actions.create_waiting_session(_reference(stock_code))

    def set_mock_trade_price(self, price=100):
        accepted = self.host.market_store.accept_trade(
            {**_trade_payload(), "current_price": price}
        )
        self.assertTrue(accepted)

    @staticmethod
    def set_main_market_price(
        window,
        price,
        *,
        source="REALTIME",
        login_session_id="SESSION-1",
    ):
        market_state = SimpleNamespace(
            last_price=price,
            login_session_id=login_session_id,
            field_sources=(("last_price", source),),
        )
        state_getter = Mock(return_value=market_state)
        operation_host = SimpleNamespace(
            configuration_market_information_state=state_getter,
        )
        window.main_monitoring_auto_trade_operation_host = Mock(
            return_value=operation_host
        )
        return state_getter

    def _phase1_mock_table_window(self):
        table = QTableWidget(0, len(main_table_loader.ROUTINE_MONITORING_HEADERS))
        table.setColumnWidth(0, 2200)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        window = SimpleNamespace(
            routine_table=table,
            mock_validation_host=self.host,
            mock_validation_ui_actions=self.actions,
            _collapsed_mock_validation_stock_keys=set(),
            kiwoom_api=self.api,
            selected_account_no=lambda: "12345678",
            _account_authentication_states={"12345678": "READY"},
            _running_budget_adjustment_dialog=None,
        )
        window.load_routine_table = lambda: main_table_loader._load_mock_routine_table(window)
        window._main_routine_selected_row_keys = MethodType(
            MainWindow._main_routine_selected_row_keys,
            window,
        )
        window._reload_main_routine_table_preserving_view = MethodType(
            MainWindow._reload_main_routine_table_preserving_view,
            window,
        )
        window.toggle_mock_routine_stock_expansion = MethodType(
            MainWindow.toggle_mock_routine_stock_expansion,
            window,
        )
        for name in (
            "_mock_routine_instance_edit_target",
            "_mock_start_budget_edit_authorized",
            "_mock_operation_settings_edit_authorized",
            "_mock_start_budget_current_price",
            "_write_mock_routine_instance_settings",
            "toggle_mock_routine_instance_initial_buy_mode",
            "open_mock_routine_instance_initial_buy_dialog",
            "toggle_mock_routine_instance_operation_mode",
            "open_mock_routine_instance_schedule_dialog",
            "reset_mock_routine_instance_schedule",
            "mock_routine_instance_ats_state",
            "set_mock_routine_instance_ats_flag",
        ):
            setattr(window, name, MethodType(getattr(MainWindow, name), window))
        controller = gui_windows._RoutineTreeInteractionController(window)
        table.viewport().installEventFilter(controller)
        window._routine_tree_interaction_controller = controller
        table.resize(2300, 320)
        table.show()
        self.app.processEvents()
        self.addCleanup(table.close)
        return window

    def _actual_mock_table_window(self):
        window = gui_windows.QMainWindow()
        self.addCleanup(window.close)
        table = QTableWidget(
            0,
            len(main_table_loader.ROUTINE_MONITORING_HEADERS),
            window,
        )
        table.setColumnWidth(0, 2200)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        window.setCentralWidget(table)
        window.routine_table = table
        window.mock_validation_host = self.host
        window.mock_validation_ui_actions = self.actions
        window._collapsed_mock_validation_stock_keys = set()
        window.kiwoom_api = self.api
        window.selected_account_no = lambda: "12345678"
        window._account_authentication_states = {"12345678": "READY"}
        window._mock_operation_settings_dialog = None
        window.load_routine_table = lambda: main_table_loader._load_mock_routine_table(
            window
        )
        for name in (
            "_main_routine_selected_row_keys",
            "_reload_main_routine_table_preserving_view",
            "_mock_routine_instance_edit_target",
            "_mock_operation_settings_edit_authorized",
            "_write_mock_routine_instance_settings",
            "toggle_mock_routine_instance_operation_mode",
            "open_mock_routine_instance_schedule_dialog",
            "reset_mock_routine_instance_schedule",
            "mock_routine_instance_ats_state",
            "set_mock_routine_instance_ats_flag",
        ):
            setattr(window, name, MethodType(getattr(MainWindow, name), window))
        controller = gui_windows._RoutineTreeInteractionController(window)
        table.viewport().installEventFilter(controller)
        window._routine_tree_interaction_controller = controller
        window.resize(2300, 320)
        window.show()
        self.app.processEvents()
        return window

    def test_host_connects_registers_unique_stock_and_disposes_only_mock_stream(self):
        self.assertEqual(1, len(self.api.mock_orderbook_received.callbacks))
        self.assertEqual(1, len(self.api.realtime_shadow_tick_received.callbacks))
        self.create()
        self.assertEqual(("005930",), self.api.targets)
        self.assertEqual(1, mock_badge_count(self.host.repository))
        self.host.dispose()
        self.assertEqual([], self.api.mock_orderbook_received.callbacks)
        self.assertEqual([], self.api.realtime_shadow_tick_received.callbacks)
        self.assertEqual(1, len(self.api.clear_calls))

    def test_waiting_buffers_every_tick_then_drains_without_routine_evaluation(self):
        self.create()
        for instance_id in ("A", "B", "C"):
            self.actions.set_instance_effective_settings(
                "005930",
                instance_id,
                operation_mode="MANUAL",
            )
        self.host.routine_adapter.evaluate_cycle = Mock()
        for sequence in (1, 2, 3):
            payload = {
                **_trade_payload(sequence=sequence),
                "received_at": (NOW + timedelta(milliseconds=sequence)).isoformat(),
                "market_datetime": (NOW + timedelta(milliseconds=sequence)).isoformat(),
                "trade_volume_raw": -sequence,
                "trade_volume_abs": sequence,
            }
            self.assertTrue(self.host.accept_trade(payload))
        buffered = self.host.buffered_evidence("005930")
        self.assertEqual((1, 2, 3), tuple(item.market_sequence for item in buffered))
        self.host.process_due_cycles(as_of=NOW + timedelta(seconds=1))
        self.assertEqual((), self.host.buffered_evidence("005930"))
        self.host.routine_adapter.evaluate_cycle.assert_not_called()

    def test_scheduled_instance_auto_starts_and_end_time_blocks_only_new_buy(self):
        self.create()
        self.actions.set_instance_effective_settings(
            "005930",
            "A",
            operation_mode="SCHEDULED",
            operation_schedule={
                "start_time": "10:30:00",
                "end_buy_time": "13:30:00",
            },
        )
        for instance_id in ("B", "C"):
            self.actions.set_instance_effective_settings(
                "005930",
                instance_id,
                operation_mode="MANUAL",
            )
        evaluate = Mock(return_value={"status": "NOOP"})
        self.host.routine_adapter.evaluate_cycle = evaluate

        self.host.process_due_cycles(as_of=NOW.replace(hour=10, minute=29))
        self.assertEqual(
            "WAITING",
            self.host.current_session("005930")["instance_execution"]["A"]["state"],
        )
        evaluate.assert_not_called()

        self.host.process_due_cycles(as_of=NOW.replace(hour=10, minute=30))
        started = self.host.current_session("005930")
        self.assertEqual("RUNNING", started["instance_execution"]["A"]["state"])
        self.assertTrue(evaluate.call_args.kwargs["new_buy_allowed"])
        operation = started["mock_operation_lifecycle"]["instance_operations"]["A"]
        self.assertEqual(
            started["effective_settings_by_instance"]["A"],
            operation["operation_policy_snapshot"]["mock_instance_effective_settings"],
        )

        evaluate.reset_mock()
        self.host.process_due_cycles(as_of=NOW.replace(hour=13, minute=30))
        evaluate.assert_called_once()
        self.assertFalse(evaluate.call_args.kwargs["new_buy_allowed"])
        self.assertEqual(
            "RUNNING",
            self.host.current_session("005930")["instance_execution"]["A"]["state"],
        )

    def test_explicit_scheduled_start_before_window_blocks_new_buy_progression(self):
        self.create()
        self.actions.set_instance_effective_settings(
            "005930",
            "A",
            operation_mode="SCHEDULED",
            operation_schedule={
                "start_time": "10:30:00",
                "end_buy_time": "13:30:00",
            },
        )
        for instance_id in ("B", "C"):
            self.actions.set_instance_effective_settings(
                "005930", instance_id, operation_mode="MANUAL"
            )
        before_start = NOW.replace(hour=10, minute=0)
        self.host.start_instance_operation("005930", "A", as_of=before_start)
        evaluate = Mock(return_value={"status": "NOOP"})
        self.host.routine_adapter.evaluate_cycle = evaluate

        self.host.process_due_cycles(as_of=before_start)
        evaluate.assert_called_once()
        self.assertFalse(evaluate.call_args.kwargs["new_buy_allowed"])

    def test_continuous_with_selected_ats_requires_explicit_start_and_uses_only_selected_session(self):
        self.create()
        self.actions.set_instance_effective_settings(
            "005930", "A", operation_mode="CONTINUOUS"
        )
        self.actions.set_instance_effective_settings(
            "005930",
            "B",
            operation_mode="CONTINUOUS",
            manual_ats={
                "selected_sessions": ["extra1"],
            },
        )
        self.actions.set_instance_effective_settings(
            "005930", "C", operation_mode="CONTINUOUS"
        )
        evaluate = Mock(return_value={"status": "NOOP"})
        self.host.routine_adapter.evaluate_cycle = evaluate
        ats_time = NOW.replace(hour=16, minute=0)
        self.host._operation_policy_provider = lambda: {
            "regular_market": {
                "start_time": "09:00:00",
                "end_time": "15:20:00",
            },
            "extra_sessions": [
                {
                    "enabled": True,
                    "start_time": "15:40:00",
                    "end_time": "19:50:00",
                }
            ],
            "liquidation": {
                "minutes_before_regular_close": "5",
                "method": "시장가",
            },
        }

        self.host.process_due_cycles(as_of=ats_time)
        self.assertEqual(
            {"WAITING"},
            {
                item["state"]
                for item in self.host.current_session("005930")["instance_execution"].values()
            },
        )
        evaluate.assert_not_called()

        self.host.start_instance_operation("005930", "A", as_of=ats_time)
        self.host.start_instance_operation("005930", "B", as_of=ats_time)
        self.host.process_due_cycles(as_of=ats_time)
        self.assertEqual(
            ["B"],
            [call.kwargs["routine_instance_id"] for call in evaluate.call_args_list],
        )
        self.assertTrue(
            all("execution_method" not in call.kwargs for call in evaluate.call_args_list)
        )
        document = self.host.current_session("005930")
        self.assertEqual("RUNNING", document["instance_execution"]["A"]["state"])
        self.assertEqual("RUNNING", document["instance_execution"]["B"]["state"])
        self.assertEqual("WAITING", document["instance_execution"]["C"]["state"])

        evaluate.reset_mock()
        self.host.process_due_cycles(as_of=NOW.replace(hour=11, minute=0))
        self.assertEqual(
            ["A", "B"],
            [call.kwargs["routine_instance_id"] for call in evaluate.call_args_list],
        )
        self.assertTrue(
            all("execution_method" not in call.kwargs for call in evaluate.call_args_list)
        )

        evaluate.reset_mock()
        self.host.process_due_cycles(as_of=NOW.replace(hour=20, minute=0))
        evaluate.assert_not_called()

    def test_legacy_manual_ats_projects_all_but_executes_only_policy_enabled_sessions(self):
        created = self.create()["document"]
        session_id = created["session"]["validation_session_id"]
        session_path = (
            self.host.repository.root
            / "runtime"
            / "sessions"
            / f"{session_id}.json"
        )
        raw = json.loads(session_path.read_text(encoding="utf-8"))
        raw_settings = raw["effective_settings_by_instance"]["A"]
        raw_settings["operation_mode"] = "MANUAL_ATS"
        raw_settings.pop("manual_ats", None)
        session_path.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        before = session_path.read_bytes()
        self.host._operation_policy_provider = lambda: {
            "regular_market": {"start_time": "09:00:00", "end_time": "15:20:00"},
            "extra_sessions": [
                {"enabled": True, "start_time": "16:00:00", "end_time": "17:00:00"},
                {"enabled": False, "start_time": "17:00:00", "end_time": "18:00:00"},
                {"enabled": True, "start_time": "18:00:00", "end_time": "19:00:00"},
            ],
        }

        settings = instance_effective_settings(self.host.current_session("005930"), "A")

        self.assertEqual("CONTINUOUS", settings["operation_mode"])
        self.assertEqual(
            ["extra1", "extra2", "extra3"],
            settings["manual_ats"]["selected_sessions"],
        )
        host_settings = self.host._instance_effective_settings(
            self.host.current_session("005930"),
            "A",
        )
        self.assertEqual(
            ["extra1", "extra3"],
            host_settings["manual_ats"]["selected_sessions"],
        )
        self.assertEqual(
            (True, True),
            self.host._mock_market_session_phase(
                host_settings, NOW.replace(hour=16, minute=30)
            ),
        )
        self.assertEqual(
            (False, False),
            self.host._mock_market_session_phase(
                host_settings, NOW.replace(hour=17, minute=30)
            ),
        )
        self.assertEqual(
            (True, True),
            self.host._mock_market_session_phase(
                host_settings, NOW.replace(hour=18, minute=30)
            ),
        )
        self.assertEqual(before, session_path.read_bytes())

    def test_running_and_closing_instance_settings_are_blocked(self):
        self.create()
        self.actions.set_instance_effective_settings(
            "005930", "A", operation_mode="MANUAL"
        )
        self.host.start_instance_operation("005930", "A", as_of=NOW)
        with self.assertRaisesRegex(
            MockValidationError,
            "MOCK_INSTANCE_SETTINGS_REQUIRE_WAITING",
        ):
            self.actions.set_instance_effective_settings(
                "005930",
                "A",
                initial_buy={"mode": "QUANTITY", "value": 7},
            )

        self.host.request_instance_early_close(
            "005930", "A", method="시장가", as_of=NOW
        )
        self.assertEqual(
            "CLOSING",
            self.host.current_session("005930")["instance_execution"]["A"]["state"],
        )
        with self.assertRaisesRegex(
            MockValidationError,
            "MOCK_INSTANCE_SETTINGS_REQUIRE_WAITING",
        ):
            self.actions.set_instance_effective_settings(
                "005930",
                "A",
                operation_mode="MANUAL_ATS",
            )

    def test_mock_instance_identity_indent_does_not_move_setting_slots(self):
        self.create()
        window = self._phase1_mock_table_window()
        window.load_routine_table()
        table = window.routine_table
        child_index = table.model().index(1, 0)
        controller = window._routine_tree_interaction_controller
        identity_rect = controller._stock_legacy_metric_rect(child_index, 0)
        initial_buy_rect = controller._stock_legacy_metric_rect(child_index, 1)
        schedule_rect = controller._stock_legacy_metric_rect(child_index, 2)

        self.assertEqual(
            gui_windows.ROUTINE_CHILD_CHECKBOX_OFFSET,
            gui_windows._routine_stock_identity_indent(
                main_table_loader.ROUTINE_ROW_MOCK_INSTANCE
            ),
        )
        self.assertEqual(
            0,
            gui_windows._routine_stock_identity_indent(
                main_table_loader.ROUTINE_ROW_MOCK_STOCK
            ),
        )
        widths = gui_windows.routine_stock_column_widths(table.font())
        self.assertEqual(identity_rect.left() + widths[0], initial_buy_rect.left())
        self.assertEqual(
            initial_buy_rect.left()
            + widths[1]
            + gui_windows.routine_instance_separator_width(table.font()),
            schedule_rect.left(),
        )

    def test_mock_start_budget_price_prefers_mock_trade_then_main_current_session(self):
        self.create()
        window = self._phase1_mock_table_window()

        for source in ("SNAPSHOT", "REALTIME"):
            with self.subTest(source=source):
                state_getter = self.set_main_market_price(
                    window,
                    393_000,
                    source=source,
                )
                self.assertEqual(
                    393_000,
                    window._mock_start_budget_current_price("005930"),
                )
                state_getter.assert_called_once_with("005930")

        self.set_main_market_price(window, 393_000, source="PERSISTED")
        self.assertIsNone(window._mock_start_budget_current_price("005930"))
        self.set_main_market_price(
            window,
            393_000,
            source="REALTIME",
            login_session_id="",
        )
        self.assertIsNone(window._mock_start_budget_current_price("005930"))
        self.set_main_market_price(window, 0, source="REALTIME")
        self.assertIsNone(window._mock_start_budget_current_price("005930"))
        window.main_monitoring_auto_trade_operation_host = Mock(
            return_value=SimpleNamespace(
                configuration_market_information_state=Mock(return_value=None)
            )
        )
        self.assertIsNone(window._mock_start_budget_current_price("005930"))

        self.set_mock_trade_price(100)
        state_getter = self.set_main_market_price(window, 393_000)
        self.assertEqual(100, window._mock_start_budget_current_price("005930"))
        state_getter.assert_not_called()

    def test_mock_start_budget_actual_viewport_fails_closed_without_any_price(self):
        created = self.create()
        window = self._phase1_mock_table_window()
        window.load_routine_table()
        table = window.routine_table
        controller = window._routine_tree_interaction_controller
        index = table.model().index(1, 0)
        parts = gui_windows._initial_buy_component_rects(
            controller._stock_legacy_metric_rect(index, 1)
        )

        with (
            patch.object(gui_windows, "show_toast") as toast,
            patch.object(gui_windows, "RunningBudgetAdjustmentDialog") as dialog,
        ):
            QTest.mouseDClick(
                table.viewport(),
                Qt.LeftButton,
                pos=parts["badge"].center(),
            )
            QTest.mouseDClick(
                table.viewport(),
                Qt.LeftButton,
                pos=parts["value"].center(),
            )
            self.app.processEvents()

        self.assertEqual(
            created["document"]["revision"],
            self.host.current_session("005930")["revision"],
        )
        self.assertEqual(
            [MainWindow._start_budget_price_unavailable_message()] * 2,
            [call_args.args[1] for call_args in toast.call_args_list],
        )
        dialog.assert_not_called()

    def test_mock_start_budget_actual_viewport_uses_main_market_fallback(self):
        created = self.create()
        frozen_hash = created["document"]["reference_snapshot"]["snapshot_hash"]
        sibling_before = deepcopy(
            created["document"]["effective_settings_by_instance"]["B"]
        )
        production_dir = self.project_root / "stocks" / "005930_삼성전자"
        production_dir.mkdir(parents=True)
        production_config = production_dir / "config.json"
        production_config.write_text('{"sentinel":"production"}', encoding="utf-8")
        production_before = production_config.read_bytes()

        window = gui_windows.QMainWindow()
        self.addCleanup(window.close)
        table = QTableWidget(
            0,
            len(main_table_loader.ROUTINE_MONITORING_HEADERS),
            window,
        )
        table.setColumnWidth(0, 2200)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        window.setCentralWidget(table)
        window.routine_table = table
        window.mock_validation_host = self.host
        window.mock_validation_ui_actions = self.actions
        window._collapsed_mock_validation_stock_keys = set()
        window.kiwoom_api = self.api
        window.selected_account_no = lambda: "12345678"
        window._account_authentication_states = {"12345678": "READY"}
        window._running_budget_adjustment_dialog = None
        window.load_routine_table = lambda: main_table_loader._load_mock_routine_table(
            window
        )
        for name in (
            "_main_routine_selected_row_keys",
            "_reload_main_routine_table_preserving_view",
            "_mock_routine_instance_edit_target",
            "_mock_start_budget_edit_authorized",
            "_mock_start_budget_current_price",
            "_write_mock_routine_instance_settings",
            "toggle_mock_routine_instance_initial_buy_mode",
            "open_mock_routine_instance_initial_buy_dialog",
        ):
            setattr(window, name, MethodType(getattr(MainWindow, name), window))
        controller = gui_windows._RoutineTreeInteractionController(window)
        table.viewport().installEventFilter(controller)
        window._routine_tree_interaction_controller = controller
        window.resize(2300, 320)
        window.show()
        self.app.processEvents()
        state_getter = self.set_main_market_price(window, 393_000)
        window.load_routine_table()
        self.assertIsNone(self.host.market_store.latest_trade("005930"))

        def initial_buy_parts():
            index = table.model().index(1, 0)
            visible = gui_windows._routine_stock_token_rect(table, index, 1)
            hit = controller._stock_legacy_metric_rect(index, 1)
            self.assertEqual(visible, hit)
            return gui_windows._initial_buy_component_rects(visible)

        QTest.mouseDClick(
            table.viewport(),
            Qt.LeftButton,
            pos=initial_buy_parts()["badge"].center(),
        )
        self.app.processEvents()
        self.assertEqual(
            "AMOUNT",
            self.host.current_session("005930")["effective_settings_by_instance"][
                "A"
            ]["initial_buy"]["mode"],
        )

        QTest.mouseDClick(
            table.viewport(),
            Qt.LeftButton,
            pos=initial_buy_parts()["badge"].center(),
        )
        self.app.processEvents()
        self.assertEqual(
            "QUANTITY",
            self.host.current_session("005930")["effective_settings_by_instance"][
                "A"
            ]["initial_buy"]["mode"],
        )

        observed = {}

        def accept_dialog():
            dialog = window._running_budget_adjustment_dialog
            observed["class"] = type(dialog)
            observed["exec_reached"] = dialog is not None
            observed["show_limit_option"] = dialog.show_limit_option
            observed["limit"] = dialog.apply_limit_checkbox
            observed["price"] = dialog.current_price_label.text()
            dialog.value_edit.setText("7")
            dialog._validate_and_accept()

        gui_windows.QTimer.singleShot(0, accept_dialog)
        with (
            patch.object(gui_windows, "show_toast") as toast,
            patch.object(
                gui_windows.QInputDialog,
                "getItem",
                side_effect=AssertionError("Mock budget must not use QInputDialog"),
            ) as old_dialog,
        ):
            QTest.mouseDClick(
                table.viewport(),
                Qt.LeftButton,
                pos=initial_buy_parts()["value"].center(),
            )
            self.app.processEvents()

        document = self.host.current_session("005930")
        self.assertIs(
            observed["class"],
            gui_windows.RunningBudgetAdjustmentDialog,
        )
        self.assertTrue(observed["exec_reached"])
        self.assertFalse(observed["show_limit_option"])
        self.assertIsNone(observed["limit"])
        self.assertEqual("현재가 393,000원", observed["price"])
        self.assertEqual(
            {"mode": "QUANTITY", "value": 7},
            document["effective_settings_by_instance"]["A"]["initial_buy"],
        )
        self.assertEqual(
            sibling_before,
            document["effective_settings_by_instance"]["B"],
        )
        self.assertEqual(frozen_hash, document["reference_snapshot"]["snapshot_hash"])
        self.assertEqual(production_before, production_config.read_bytes())
        self.assertGreaterEqual(state_getter.call_count, 3)
        self.assertNotIn(
            MainWindow._start_budget_price_unavailable_message(),
            [call_args.args[1] for call_args in toast.call_args_list],
        )
        old_dialog.assert_not_called()

    def test_mock_instance_ui_edits_are_mock_only_and_stale_identity_fails_closed(self):
        created = self.create()
        session_id = created["document"]["session"]["validation_session_id"]
        frozen_hash = created["document"]["reference_snapshot"]["snapshot_hash"]
        sibling_before = deepcopy(
            created["document"]["effective_settings_by_instance"]["B"]
        )
        self.set_mock_trade_price(100_000)
        window = self._phase1_mock_table_window()
        window.load_routine_table()

        def set_row_price():
            item = window.routine_table.item(1, 0)
            projection = dict(
                item.data(main_table_loader.ROUTINE_STOCK_TOOLTIP_DATA_ROLE)
            )
            projection["current_price"] = 100_000
            item.setData(
                main_table_loader.ROUTINE_STOCK_TOOLTIP_DATA_ROLE,
                projection,
            )

        set_row_price()

        window.toggle_mock_routine_instance_initial_buy_mode(1)
        set_row_price()
        class AcceptedBudgetDialog:
            result = {
                "mode": "AMOUNT",
                "value": 500_000,
                "apply_timing": "PRE_OPERATION",
                "apply_limit": False,
            }
            requested_at = NOW.isoformat()

            def __init__(self, *_args, **kwargs):
                self.kwargs = kwargs
                self.assert_mock_contract = (
                    kwargs.get("show_limit_option") is False
                    and kwargs.get("timing_selection_enabled") is False
                    and kwargs.get("config", {}).get("buy_amount") == 150_000
                )

            def exec_(self):
                if not self.assert_mock_contract:
                    raise AssertionError("Mock budget dialog contract mismatch")
                return gui_windows.QDialog.Accepted

            def deleteLater(self):
                return None

        with (
            patch.object(
                gui_windows,
                "RunningBudgetAdjustmentDialog",
                AcceptedBudgetDialog,
            ),
            patch.object(gui_windows, "show_toast"),
        ):
            window.open_mock_routine_instance_initial_buy_dialog(1)
        window.toggle_mock_routine_instance_operation_mode(1)
        window.set_mock_routine_instance_ats_flag(1, "extra1", True, "장전프리")

        changed = self.host.current_session("005930")
        self.assertEqual(
            {"mode": "AMOUNT", "value": 500_000},
            changed["effective_settings_by_instance"]["A"]["initial_buy"],
        )
        self.assertEqual(
            "CONTINUOUS",
            changed["effective_settings_by_instance"]["A"]["operation_mode"],
        )
        self.assertEqual(
            {
                "selected_sessions": ["extra1"],
            },
            changed["effective_settings_by_instance"]["A"]["manual_ats"],
        )
        self.assertEqual(
            sibling_before,
            changed["effective_settings_by_instance"]["B"],
        )
        self.assertEqual(frozen_hash, changed["reference_snapshot"]["snapshot_hash"])

        stale_item = window.routine_table.item(1, 0)
        stale_projection = dict(
            stale_item.data(main_table_loader.ROUTINE_STOCK_TOOLTIP_DATA_ROLE)
        )
        stale_projection["validation_session_id"] = "MV-stale"
        stale_item.setData(
            main_table_loader.ROUTINE_STOCK_TOOLTIP_DATA_ROLE,
            stale_projection,
        )
        revision = changed["revision"]
        window.toggle_mock_routine_instance_initial_buy_mode(1)
        self.assertEqual(revision, self.host.current_session("005930")["revision"])

    def test_mock_instance_ui_scheduled_times_are_saved_to_the_exact_child(self):
        self.create()
        window = self._phase1_mock_table_window()
        window.load_routine_table()
        dialog = SimpleNamespace(
            setWindowTitle=Mock(),
            exec_=Mock(return_value=gui_windows.QDialog.Accepted),
            selected_operation_mode=lambda: "SCHEDULED",
            start_time=lambda: "10:45:00",
            end_buy_time=lambda: "14:10:00",
            deleteLater=Mock(),
        )
        with patch.object(
            gui_windows,
            "ScheduleOperationDialog",
            return_value=dialog,
        ):
            window.open_mock_routine_instance_schedule_dialog(1)

        document = self.host.current_session("005930")
        self.assertEqual(
            {
                "start_time": "10:45:00",
                "end_buy_time": "14:10:00",
            },
            document["effective_settings_by_instance"]["A"]["operation_schedule"],
        )
        self.assertEqual(
            "SCHEDULED",
            document["effective_settings_by_instance"]["A"]["operation_mode"],
        )
        self.assertEqual(
            {"start_time": "09:00:00", "end_buy_time": "13:30:00"},
            document["effective_settings_by_instance"]["B"]["operation_schedule"],
        )

    def test_mock_operation_mode_actual_viewport_toggles_without_dialog(self):
        created = self.create()
        frozen_hash = created["document"]["reference_snapshot"]["snapshot_hash"]
        sibling_before = deepcopy(
            created["document"]["effective_settings_by_instance"]["B"]
        )
        production_dir = self.project_root / "stocks" / "005930_삼성전자"
        production_dir.mkdir(parents=True)
        production_config = production_dir / "config.json"
        production_config.write_text('{"sentinel":"production"}', encoding="utf-8")
        production_before = production_config.read_bytes()
        window = self._actual_mock_table_window()
        window.load_routine_table()
        table = window.routine_table
        controller = window._routine_tree_interaction_controller

        def expected_operation_style():
            settings = self.host.current_session("005930")[
                "effective_settings_by_instance"
            ]["A"]
            schedule = settings["operation_schedule"]
            manual_ats = settings["manual_ats"]
            descriptor = main_table_loader.auto_trade_operation_display(
                {
                    "operation_mode": settings["operation_mode"],
                    "start_time": schedule["start_time"],
                    "end_buy_time": schedule["end_buy_time"],
                },
                {
                    "manual_ats_selection": {
                        "selected_sessions": list(
                            manual_ats["selected_sessions"]
                        ),
                    }
                },
            )
            return main_table_loader._item_style_snapshot(
                main_table_loader.create_auto_trade_operation_item(descriptor)
            )

        initial_tokens = table.item(1, 0).data(
            main_table_loader.ROUTINE_STOCK_DISPLAY_ROLE
        )
        self.assertEqual(expected_operation_style(), initial_tokens[2])

        def operation_rect():
            index = table.model().index(1, 0)
            visible = gui_windows._routine_stock_token_rect(table, index, 2)
            hit = controller._stock_legacy_metric_rect(index, 2)
            self.assertEqual(visible, hit)
            return hit

        def toggle():
            QTest.mouseDClick(
                table.viewport(),
                Qt.LeftButton,
                pos=operation_rect().center(),
            )
            self.app.processEvents()

        with (
            patch.object(gui_windows, "ScheduleOperationDialog") as dialog,
            patch.object(
                gui_windows.QInputDialog,
                "getItem",
                side_effect=AssertionError(
                    "Mock operation settings must not use QInputDialog"
                ),
            ) as old_dialog,
        ):
            toggle()
            self.assertEqual(
                "CONTINUOUS",
                self.host.current_session("005930")["effective_settings_by_instance"]["A"]["operation_mode"],
            )
            window.set_mock_routine_instance_ats_flag(
                1, "extra1", True, "장전프리"
            )
            continuous_item = table.item(1, 0)
            self.assertEqual(
                "수동+ATS",
                continuous_item.data(
                    main_table_loader.ROUTINE_STOCK_VALUES_ROLE
                )[2],
            )
            toggle()

        document = self.host.current_session("005930")
        self.assertEqual(
            "SCHEDULED",
            document["effective_settings_by_instance"]["A"]["operation_mode"],
        )
        self.assertEqual(
            {"start_time": "09:00:00", "end_buy_time": "13:30:00"},
            document["effective_settings_by_instance"]["A"]["operation_schedule"],
        )
        self.assertEqual(
            [],
            document["effective_settings_by_instance"]["A"]["manual_ats"][
                "selected_sessions"
            ],
        )
        refreshed_item = table.item(1, 0)
        self.assertEqual(
            "09:00~13:30",
            refreshed_item.data(main_table_loader.ROUTINE_STOCK_VALUES_ROLE)[2],
        )
        self.assertEqual(
            document["effective_settings_by_instance"]["A"],
            refreshed_item.data(
                main_table_loader.ROUTINE_MOCK_EFFECTIVE_SETTINGS_ROLE
            ),
        )
        self.assertEqual(
            expected_operation_style(),
            refreshed_item.data(main_table_loader.ROUTINE_STOCK_DISPLAY_ROLE)[2],
        )
        self.assertEqual(
            sibling_before,
            document["effective_settings_by_instance"]["B"],
        )
        self.assertEqual(frozen_hash, document["reference_snapshot"]["snapshot_hash"])
        self.assertEqual(production_before, production_config.read_bytes())
        dialog.assert_not_called()
        old_dialog.assert_not_called()

        restarted = MockValidationHost(
            _Api(),
            project_root=self.project_root,
            now_factory=lambda: self.clock["now"],
            operation_policy_provider=self.host._operation_policy_provider,
            candles_provider=lambda _document: [],
        )
        self.addCleanup(restarted.dispose)
        read_back = restarted.current_session("005930")
        self.assertEqual(
            document["effective_settings_by_instance"]["A"],
            read_back["effective_settings_by_instance"]["A"],
        )

    def test_mock_operation_settings_require_auth_and_waiting_instance(self):
        created = self.create()
        window = self._phase1_mock_table_window()
        window.load_routine_table()
        initial_revision = created["document"]["revision"]

        window.kiwoom_api = SimpleNamespace(is_connected=lambda: False)
        with (
            patch.object(gui_windows, "show_toast") as toast,
            patch.object(gui_windows, "ScheduleOperationDialog") as dialog,
        ):
            window.toggle_mock_routine_instance_operation_mode(1)
        dialog.assert_not_called()
        self.assertEqual(initial_revision, self.host.current_session("005930")["revision"])
        self.assertIn("서버 인증 완료", toast.call_args.args[1])

        window.kiwoom_api = self.api
        self.host.start_instance_operation("005930", "A", as_of=NOW)
        self.host.start_instance_operation("005930", "B", as_of=NOW)
        self.host.request_instance_early_close(
            "005930", "B", method="시장가", as_of=NOW
        )
        self.host.session_service.stop_for_instance_error(
            created["document"]["session"]["validation_session_id"],
            source_routine_instance_id="C",
            reason_code="FIXTURE",
            reason="fixture",
            command_id="MC-operation-settings-error-C",
        )
        window.load_routine_table()
        blocked_revision = self.host.current_session("005930")["revision"]
        with (
            patch.object(gui_windows, "show_toast") as toast,
            patch.object(gui_windows, "ScheduleOperationDialog") as dialog,
        ):
            for row in (1, 2, 3):
                window.toggle_mock_routine_instance_operation_mode(row)
        dialog.assert_not_called()
        self.assertEqual(3, toast.call_count)
        self.assertTrue(
            all("운영대기 상태" in item.args[1] for item in toast.call_args_list)
        )
        self.assertEqual(blocked_revision, self.host.current_session("005930")["revision"])

    def test_mock_instance_reset_retains_operation_settings(self):
        created = self.create()
        frozen_hash = created["document"]["reference_snapshot"]["snapshot_hash"]
        self.actions.set_instance_effective_settings(
            "005930",
            "A",
            operation_mode="CONTINUOUS",
            manual_ats={
                "selected_sessions": ["extra2"],
            },
            operation_schedule={
                "start_time": "10:45:00",
                "end_buy_time": "14:10:00",
            },
        )
        self.host.start_instance_operation("005930", "A", as_of=NOW)
        reset = self.actions.reset_instance("005930", "A")["document"]
        self.assertEqual("WAITING", reset["instance_execution"]["A"]["state"])
        self.assertEqual(
            "CONTINUOUS",
            reset["effective_settings_by_instance"]["A"]["operation_mode"],
        )
        self.assertEqual(
            {
                "selected_sessions": ["extra2"],
            },
            reset["effective_settings_by_instance"]["A"]["manual_ats"],
        )
        self.assertEqual(
            {"start_time": "10:45:00", "end_buy_time": "14:10:00"},
            reset["effective_settings_by_instance"]["A"]["operation_schedule"],
        )
        self.assertEqual(frozen_hash, reset["reference_snapshot"]["snapshot_hash"])

    def test_schedule_dialog_default_production_presentation_is_unchanged(self):
        dialog = gui_windows.ScheduleOperationDialog(
            parent=None,
            start_time="09:00:00",
            end_buy_time="13:30:00",
            selected_count=2,
        )
        self.addCleanup(dialog.close)
        self.assertFalse(hasattr(dialog, "operation_mode_combo"))
        self.assertEqual("09:00:00", dialog.start_time())
        self.assertEqual("13:30:00", dialog.end_buy_time())
        self.assertTrue(dialog.start_hour_combo.isEnabled())
        self.assertTrue(dialog.end_hour_combo.isEnabled())

    def test_mock_start_budget_requires_official_server_auth(self):
        self.create()
        window = self._phase1_mock_table_window()
        window.load_routine_table()
        item = window.routine_table.item(1, 0)
        projection = dict(
            item.data(main_table_loader.ROUTINE_STOCK_TOOLTIP_DATA_ROLE)
        )
        projection["current_price"] = 100
        item.setData(
            main_table_loader.ROUTINE_STOCK_TOOLTIP_DATA_ROLE,
            projection,
        )
        window.kiwoom_api = SimpleNamespace(is_connected=lambda: False)
        before = self.host.current_session("005930")

        with (
            patch.object(gui_windows, "show_toast") as toast,
            patch.object(gui_windows, "RunningBudgetAdjustmentDialog") as dialog,
        ):
            window.toggle_mock_routine_instance_initial_buy_mode(1)
            window.open_mock_routine_instance_initial_buy_dialog(1)

        self.assertEqual(
            before["revision"], self.host.current_session("005930")["revision"]
        )
        dialog.assert_not_called()
        self.assertEqual(2, toast.call_count)

    def test_actual_mock_child_budget_dialog_round_trip_is_mock_only(self):
        created = self.create()
        frozen_hash = created["document"]["reference_snapshot"]["snapshot_hash"]
        sibling = deepcopy(
            created["document"]["effective_settings_by_instance"]["B"]
        )
        production_dir = self.project_root / "stocks" / "005930_삼성전자"
        production_dir.mkdir(parents=True)
        production_config = production_dir / "config.json"
        production_config.write_text('{"sentinel":"production"}', encoding="utf-8")
        production_before = production_config.read_bytes()

        window = gui_windows.QMainWindow()
        self.addCleanup(window.deleteLater)
        table = QTableWidget(
            0,
            len(main_table_loader.ROUTINE_MONITORING_HEADERS),
            window,
        )
        table.setColumnWidth(0, 2200)
        window.routine_table = table
        window.mock_validation_host = self.host
        window.mock_validation_ui_actions = self.actions
        window._collapsed_mock_validation_stock_keys = set()
        window.kiwoom_api = self.api
        window.selected_account_no = lambda: "12345678"
        window._account_authentication_states = {"12345678": "READY"}
        window._running_budget_adjustment_dialog = None
        self.set_mock_trade_price(100)
        window.load_routine_table = lambda: main_table_loader._load_mock_routine_table(
            window
        )
        for name in (
            "_main_routine_selected_row_keys",
            "_reload_main_routine_table_preserving_view",
            "_mock_routine_instance_edit_target",
            "_mock_start_budget_edit_authorized",
            "_mock_start_budget_current_price",
            "open_mock_routine_instance_initial_buy_dialog",
        ):
            setattr(window, name, MethodType(getattr(MainWindow, name), window))
        window.load_routine_table()
        item = table.item(1, 0)
        projection = dict(
            item.data(main_table_loader.ROUTINE_STOCK_TOOLTIP_DATA_ROLE)
        )
        projection["current_price"] = 100
        item.setData(
            main_table_loader.ROUTINE_STOCK_TOOLTIP_DATA_ROLE,
            projection,
        )
        observed = {}

        def accept_dialog():
            dialog = window._running_budget_adjustment_dialog
            observed["class"] = type(dialog)
            observed["limit"] = dialog.apply_limit_checkbox
            observed["price"] = dialog.current_price_label.text()
            dialog.value_edit.setText("7")
            dialog._validate_and_accept()

        gui_windows.QTimer.singleShot(0, accept_dialog)
        with (
            patch.object(gui_windows, "show_toast"),
            patch.object(
                gui_windows.QInputDialog,
                "getInt",
                side_effect=AssertionError("Mock budget must not use QInputDialog"),
            ) as old_dialog,
        ):
            window.open_mock_routine_instance_initial_buy_dialog(1)

        document = self.host.current_session("005930")
        self.assertIs(observed["class"], gui_windows.RunningBudgetAdjustmentDialog)
        self.assertIsNone(observed["limit"])
        self.assertEqual("현재가 100원", observed["price"])
        self.assertEqual(
            {"mode": "QUANTITY", "value": 7},
            document["effective_settings_by_instance"]["A"]["initial_buy"],
        )
        self.assertEqual(
            sibling, document["effective_settings_by_instance"]["B"]
        )
        self.assertEqual(
            frozen_hash, document["reference_snapshot"]["snapshot_hash"]
        )
        self.assertEqual(production_before, production_config.read_bytes())
        old_dialog.assert_not_called()

    def test_running_mock_budget_dialog_persists_operation_bound_immediate_request(self):
        created = self.create()
        frozen_hash = created["document"]["reference_snapshot"]["snapshot_hash"]
        for instance_id in ("A", "B", "C"):
            self.actions.set_instance_effective_settings(
                "005930", instance_id, operation_mode="MANUAL"
            )
        sibling = deepcopy(
            self.host.current_session("005930")["effective_settings_by_instance"][
                "B"
            ]
        )
        self.host.start_instance_operation("005930", "A", as_of=NOW)
        self.set_mock_trade_price(100)
        window = self._phase1_mock_table_window()
        window.load_routine_table()
        item = window.routine_table.item(1, 0)
        projection = dict(
            item.data(main_table_loader.ROUTINE_STOCK_TOOLTIP_DATA_ROLE)
        )
        projection["current_price"] = 100
        item.setData(
            main_table_loader.ROUTINE_STOCK_TOOLTIP_DATA_ROLE,
            projection,
        )

        class AcceptedRunningDialog:
            result = {
                "mode": "QUANTITY",
                "value": 7,
                "apply_timing": "IMMEDIATE",
                "apply_limit": False,
            }
            requested_at = NOW.isoformat()

            def __init__(self, *_args, **kwargs):
                if kwargs.get("timing_selection_enabled") is not True:
                    raise AssertionError("running Mock timing selector is required")
                if kwargs.get("show_limit_option") is not False:
                    raise AssertionError("Mock limit option must be absent")

            def exec_(self):
                return gui_windows.QDialog.Accepted

            def deleteLater(self):
                return None

        with (
            patch.object(
                gui_windows,
                "RunningBudgetAdjustmentDialog",
                AcceptedRunningDialog,
            ),
            patch.object(gui_windows, "show_toast"),
        ):
            window.open_mock_routine_instance_initial_buy_dialog(1)

        document = self.host.current_session("005930")
        operation = document["mock_operation_lifecycle"]["instance_operations"]["A"]
        adjustment = document["initial_buy_adjustments_by_instance"]["A"]
        self.assertEqual(
            operation["operation_session_id"], adjustment["operation_session_id"]
        )
        self.assertEqual("WAIT_FIRST_BUY", adjustment["state"])
        self.assertEqual(7, adjustment["requested_value"])
        self.assertEqual(
            {"mode": "QUANTITY", "value": 7},
            document["effective_settings_by_instance"]["A"]["initial_buy"],
        )
        self.assertEqual(sibling, document["effective_settings_by_instance"]["B"])
        self.assertEqual(frozen_hash, document["reference_snapshot"]["snapshot_hash"])

        stale_revision = document["revision"]
        self.actions.set_instance_effective_settings(
            "005930",
            "B",
            manual_ats={
                "selected_sessions": ["extra1"],
            },
        )
        with self.assertRaisesRegex(
            MockValidationError,
            "MOCK_INSTANCE_SETTINGS_REVISION_CONFLICT",
        ):
            self.actions.set_instance_initial_buy(
                "005930",
                "A",
                mode="QUANTITY",
                value=9,
                apply_policy="IMMEDIATE",
                expected_validation_session_id=document["session"][
                    "validation_session_id"
                ],
                expected_revision=stale_revision,
                expected_operation_session_id=operation["operation_session_id"],
                requested_at=NOW.isoformat(),
            )
        self.assertEqual(
            7,
            self.host.current_session("005930")["effective_settings_by_instance"][
                "A"
            ]["initial_buy"]["value"],
        )

    def test_running_resting_order_receives_all_trade_ticks_in_order_once(self):
        self.create()
        self.host.start_stock_operation("005930", as_of=NOW)
        book = _book()
        self.assertTrue(self.host.accept_orderbook(book))
        policy = self.host._policy()
        submitted = self.host.engine.submit_order(
            self.host.current_session("005930")["session"]["validation_session_id"],
            routine_instance_id="A",
            side="BUY",
            order_type="LIMIT",
            requested_qty=10,
            limit_price=99,
            market=self.host.market_store.market_snapshot("005930"),
            policy=policy,
            execution_budget=100000,
            command_id="MC-resting-order",
        )
        self.assertEqual("OPEN", submitted["order"]["state"])
        calls = []
        original = self.host.engine.process_trade

        def recording(*args, **kwargs):
            calls.append(kwargs["trade"].receive_sequence)
            return original(*args, **kwargs)

        self.host.engine.process_trade = recording
        self.host.routine_adapter.evaluate_cycle = Mock()
        for sequence in (1, 2, 3):
            payload = {
                **_trade_payload(sequence=sequence),
                "current_price": 99,
                "received_at": (NOW + timedelta(milliseconds=sequence)).isoformat(),
                "market_datetime": (NOW + timedelta(milliseconds=sequence)).isoformat(),
                "trade_volume_raw": -1,
                "trade_volume_abs": 1,
            }
            self.host.accept_trade(payload)
        self.host.process_due_cycles(as_of=NOW + timedelta(seconds=1))
        self.assertEqual([1, 2, 3], calls)
        self.host.process_due_cycles(as_of=NOW + timedelta(seconds=1))
        self.assertEqual([1, 2, 3], calls)

    def test_sequence_conflict_isolates_affected_stock_instances_without_review(self):
        self.create()
        self.actions.create_waiting_session(_reference("000660", ("A",)))
        for stock_code, instance_ids in (
            ("005930", ("A", "B", "C")),
            ("000660", ("A",)),
        ):
            for instance_id in instance_ids:
                self.actions.set_instance_effective_settings(
                    stock_code,
                    instance_id,
                    operation_mode="MANUAL",
                )
        first = _trade_payload(sequence=1)
        self.assertTrue(self.host.accept_trade(first))
        self.assertFalse(self.host.accept_trade({**first, "current_price": 101}))
        result = self.host.process_due_cycles(as_of=NOW + timedelta(seconds=1))
        states = {
            row["stock_code"]: row["state"]
            for row in current_mock_projections(self.host.repository)
        }
        self.assertEqual("WAITING", states["005930"])
        self.assertEqual("WAITING", states["000660"])
        affected = self.host.current_session("005930")
        unaffected = self.host.current_session("000660")
        self.assertFalse(affected["review"]["review_required"])
        self.assertEqual({"ERROR"}, {item["state"] for item in affected["instance_execution"].values()})
        self.assertEqual({"WAITING"}, {item["state"] for item in unaffected["instance_execution"].values()})
        self.assertEqual(2, result["processed"])

    def test_corrupt_session_read_does_not_stop_another_stock_cycle(self):
        first = self.create()
        self.actions.create_waiting_session(_reference("000660", ("A",)))
        corrupt_id = first["document"]["session"]["validation_session_id"]
        path = self.host.repository.root / "runtime" / "sessions" / f"{corrupt_id}.json"
        path.write_text("{broken", encoding="utf-8")
        original = self.host._process_stock
        processed = []

        def recording(document, now):
            processed.append(document["session"]["stock_code"])
            return original(document, now)

        self.host._process_stock = recording
        result = self.host.process_due_cycles(as_of=NOW)
        self.assertEqual(["000660"], processed)
        self.assertEqual("005930", result["errors"][0][0])

    def test_reentry_is_blocked_and_projection_refreshes_only_on_change(self):
        self.create()
        for instance_id in ("A", "B", "C"):
            self.actions.set_instance_effective_settings(
                "005930",
                instance_id,
                operation_mode="MANUAL",
            )
        self.host._processing = True
        self.assertEqual(
            "MOCK_HOST_REENTRY_BLOCKED",
            self.host.process_due_cycles()["reason"],
        )
        self.host._processing = False
        self.changed.reset_mock()
        self.host.process_due_cycles(as_of=NOW)
        self.host.process_due_cycles(as_of=NOW)
        self.changed.assert_not_called()

    def test_review_state_discards_transport_backlog_without_execution_progression(self):
        created = self.create()
        session_id = created["document"]["session"]["validation_session_id"]
        self.host.session_service.stop_for_instance_error(
            session_id,
            source_routine_instance_id="B",
            reason_code="FIXTURE",
            reason="fixture",
            command_id="MC-review",
        )
        self.host.engine.process_trade = Mock()
        self.host.accept_trade({
            **_trade_payload(sequence=1),
            "trade_volume_raw": -1,
            "trade_volume_abs": 1,
        })
        self.host.process_due_cycles(as_of=NOW + timedelta(seconds=1))
        self.assertEqual((), self.host.buffered_evidence("005930"))
        self.host.engine.process_trade.assert_not_called()

    def test_instance_error_freezes_its_live_order_without_blocking_sibling_order(self):
        created = self.create()
        self.actions.start("005930")
        session_id = created["document"]["session"]["validation_session_id"]
        orders = {}
        for instance_id in ("A", "B"):
            orders[instance_id] = self.host.session_service.create_order(
                session_id,
                routine_instance_id=instance_id,
                side="BUY",
                order_type="LIMIT",
                requested_qty=1,
                requested_price=99,
                command_id=f"MC-live-{instance_id}",
            )["order"]
            self.host.session_service.transition_order(
                session_id,
                orders[instance_id]["mock_order_id"],
                "OPEN",
                command_id=f"MC-open-{instance_id}",
            )
        self.host.session_service.stop_for_instance_error(
            session_id,
            source_routine_instance_id="B",
            reason_code="FIXTURE",
            reason="fixture",
            command_id="MC-freeze-B",
        )
        self.host.engine.process_trade = Mock()
        self.host.routine_adapter.evaluate_cycle = Mock()
        self.host.accept_trade({
            **_trade_payload(sequence=1),
            "trade_volume_raw": -1,
            "trade_volume_abs": 1,
        })

        self.host.process_due_cycles(as_of=NOW + timedelta(seconds=1))

        self.host.engine.process_trade.assert_called_once()
        self.assertEqual(
            orders["A"]["mock_order_id"],
            self.host.engine.process_trade.call_args.args[1],
        )
        document = self.host.current_session("005930")
        self.assertEqual("ERROR", document["instance_execution"]["B"]["state"])
        self.assertTrue(document["instance_execution"]["A"]["progression_allowed"])

    def test_common_tax_updates_waiting_but_preserves_running_operation_snapshot(self):
        self.create("005930")
        self.create("000660")
        self.actions.start("005930")

        changed = self.actions.set_common_tax_settings(enabled=False, rate=0.0043)

        self.assertEqual(("000660",), changed["waiting_synchronized"])
        self.assertEqual(("005930",), changed["active_preserved"])
        waiting = self.host.current_session("000660")
        running = self.host.current_session("005930")
        self.assertFalse(waiting["session"]["mock_tax_enabled"])
        self.assertEqual(0.0043, waiting["session"]["mock_tax_rate"])
        self.assertTrue(running["session"]["mock_tax_enabled"])
        self.assertEqual(0.002, running["session"]["mock_tax_rate"])
        operation = running["mock_operation_lifecycle"]["current"]
        self.assertTrue(operation["operation_policy_snapshot"]["mock_tax_enabled"])
        self.assertEqual(0.002, operation["operation_policy_snapshot"]["mock_tax_rate"])

    def test_common_tax_preserves_closing_and_running_instance_error_operation(self):
        first = self.create("005930")
        self.actions.start("005930")
        self.host.request_early_close("005930", method="시장가", as_of=NOW)

        second = self.create("000660")
        self.actions.start("000660")
        self.host.session_service.stop_for_instance_error(
            second["document"]["session"]["validation_session_id"],
            source_routine_instance_id="A",
            reason_code="FIXTURE",
            reason="fixture",
            command_id="MC-review-tax",
        )
        self.actions.set_common_tax_settings(enabled=False, rate=0.0062)

        for stock_code, expected_state in (
            ("005930", "CLOSING"),
            ("000660", "RUNNING"),
        ):
            document = self.host.current_session(stock_code)
            self.assertEqual(expected_state, document["session"]["state"])
            self.assertTrue(document["session"]["mock_tax_enabled"])
            self.assertEqual(0.002, document["session"]["mock_tax_rate"])
            operation = document["mock_operation_lifecycle"]["current"]
            self.assertTrue(operation["operation_policy_snapshot"]["mock_tax_enabled"])
            self.assertEqual(0.002, operation["operation_policy_snapshot"]["mock_tax_rate"])

    def test_virtual_sell_uses_operation_tax_snapshot_not_mutable_session_fields(self):
        created = self.create()
        self.actions.start("005930")
        session_id = created["document"]["session"]["validation_session_id"]
        self.host.session_service.set_instance_position(
            session_id,
            "A",
            holding_qty=1,
            available_qty=1,
            average_price=90,
            realized_cost_basis=90,
            command_id="MC-tax-position",
        )
        before = self.host.repository.read_session(session_id)

        def mutate_session_tax_only(document):
            document["session"]["mock_tax_enabled"] = True
            document["session"]["mock_tax_rate"] = 0.5
            return document

        self.host.repository.mutate_session(
            session_id,
            mutate_session_tax_only,
            expected_revision=before["revision"],
        )

        result = self.host.engine.submit_order(
            session_id,
            routine_instance_id="A",
            side="SELL",
            order_type="MARKET",
            requested_qty=1,
            limit_price=None,
            market=_book(),
            policy=self.host._policy(),
            execution_budget=100_000,
            command_id="MC-tax-snapshot-sell",
        )

        pnl = next(
            item
            for item in result["document"]["pnl"]
            if item["routine_instance_id"] == "A"
        )
        self.assertEqual(0.2, pnl["mock_tax"])

    def test_stock_reset_to_waiting_applies_latest_common_tax_settings(self):
        created = self.create()
        self.actions.start("005930")
        session_id = created["document"]["session"]["validation_session_id"]
        self.host.session_service.stop_for_instance_error(
            session_id,
            source_routine_instance_id="A",
            reason_code="FIXTURE",
            reason="fixture",
            command_id="MC-review-reset-tax",
        )
        self.actions.set_common_tax_settings(enabled=False, rate=0.0077)

        self.host.session_service.reset_stock_session(
            session_id,
            command_id="MC-stock-reset-tax",
        )

        document = self.host.current_session("005930")
        self.assertEqual("WAITING", document["session"]["state"])
        self.assertFalse(document["session"]["mock_tax_enabled"])
        self.assertEqual(0.0077, document["session"]["mock_tax_rate"])
        self.assertIsNone(document["mock_operation_lifecycle"]["current"])

    def test_mock_monitoring_tree_has_one_stock_parent_and_independent_instance_children(self):
        created = self.create()
        session_id = created["document"]["session"]["validation_session_id"]
        self.actions.start("005930")
        self.host.session_service.set_instance_position(
            session_id,
            "B",
            holding_qty=7,
            available_qty=7,
            average_price=123,
            realized_cost_basis=861,
            command_id="MC-tree-position-B",
        )
        self.host.session_service.stop_for_instance_error(
            session_id,
            source_routine_instance_id="C",
            reason_code="MOCK_TREE_ERROR",
            reason="fixture",
            command_id="MC-tree-error-C",
        )

        trees = current_mock_monitoring_trees(self.host.repository)

        self.assertEqual(1, mock_badge_count(self.host.repository))
        self.assertEqual(1, len(trees))
        self.assertEqual("mock_stock", trees[0]["row_kind"])
        self.assertEqual(3, trees[0]["routine_instance_count"])
        self.assertNotIn("holding_qty", trees[0])
        children = {item["routine_instance_id"]: item for item in trees[0]["children"]}
        self.assertEqual({"A", "B", "C"}, set(children))
        self.assertEqual((0, 7, 0), tuple(children[key]["holding_qty"] for key in ("A", "B", "C")))
        self.assertEqual("red", children["C"]["status_led"])
        self.assertEqual("normal", children["A"]["status_led"])
        self.assertEqual("normal", children["B"]["status_led"])

    def test_single_instance_parent_has_identity_and_profit_only_and_mock_fills_drive_trade_counts(self):
        created = self.actions.create_waiting_session(_reference("005930", ("A",)))
        session_id = created["document"]["session"]["validation_session_id"]
        self.actions.start("005930")
        book = _book()
        self.assertTrue(self.host.accept_orderbook(book))
        market = self.host.market_store.market_snapshot("005930")
        for side, command_id in (("BUY", "MC-display-buy"), ("SELL", "MC-display-sell")):
            self.host.engine.submit_order(
                session_id,
                routine_instance_id="A",
                side=side,
                order_type="MARKET",
                requested_qty=1,
                limit_price=None,
                market=market,
                policy=self.host._policy(),
                execution_budget=100_000,
                command_id=command_id,
            )

        trees = current_mock_monitoring_trees(self.host.repository)
        child = trees[0]["children"][0]

        self.assertEqual(1, trees[0]["routine_instance_count"])
        self.assertNotIn("holding_qty", trees[0])
        self.assertEqual((-1.2, 0, 0.0), (
            trees[0]["profit_amount"],
            trees[0]["profit_cost_basis"],
            trees[0]["profit_rate"],
        ))
        self.assertEqual((1, 1), (child["buy_trade_count"], child["sell_trade_count"]))
        self.assertEqual((0, 0), (child["buy_pending_qty"], child["sell_pending_qty"]))

        table = QTableWidget(0, len(main_table_loader.ROUTINE_MONITORING_HEADERS))
        window = SimpleNamespace(routine_table=table, mock_validation_host=self.host)
        main_table_loader._load_mock_routine_table(window)
        self.assertEqual(["▼ 005930 삼성전자 (1)"], table.item(0, 0).data(
            main_table_loader.ROUTINE_STOCK_VALUES_ROLE
        ))
        self.assertEqual(
            ("수익(-1.20 / 0.00%)", "#2563EB"),
            table.item(0, 0).data(main_table_loader.ROUTINE_PARENT_PROFIT_ROLE),
        )
        trade_metric = table.item(1, 0).data(main_table_loader.ROUTINE_STOCK_METRICS_ROLE)[3]
        self.assertEqual(("1", "1"), (trade_metric.value1, trade_metric.value2))

    def test_main_mock_table_renders_stock_parent_and_instance_children_only(self):
        created = self.create()
        session_id = created["document"]["session"]["validation_session_id"]
        self.actions.start("005930")
        self.host.session_service.set_instance_position(
            session_id,
            "B",
            holding_qty=7,
            available_qty=7,
            average_price=123,
            realized_cost_basis=861,
            command_id="MC-table-position-B",
        )
        self.host.session_service.stop_for_instance_error(
            session_id,
            source_routine_instance_id="C",
            reason_code="MOCK_TABLE_ERROR",
            reason="fixture",
            command_id="MC-table-error-C",
        )
        table = QTableWidget(0, len(main_table_loader.ROUTINE_MONITORING_HEADERS))
        window = SimpleNamespace(
            routine_table=table,
            mock_validation_host=self.host,
        )

        main_table_loader._load_mock_routine_table(window)

        self.assertEqual(4, table.rowCount())
        self.assertEqual(
            main_table_loader.ROUTINE_ROW_MOCK_STOCK,
            table.item(0, 0).data(main_table_loader.ROUTINE_ROW_KIND_ROLE),
        )
        self.assertEqual("", table.item(0, 0).data(main_table_loader.ROUTINE_INSTANCE_ID_ROLE))
        self.assertEqual("005930", table.item(0, 0).data(main_table_loader.ROUTINE_STOCK_CODE_ROLE))
        parent_values = table.item(0, 0).data(main_table_loader.ROUTINE_STOCK_VALUES_ROLE)
        self.assertEqual(["▼ 005930 삼성전자 (3)"], parent_values)
        parent_metrics = table.item(0, 0).data(
            main_table_loader.ROUTINE_STOCK_METRICS_ROLE
        )
        self.assertEqual(1, len(parent_metrics))
        self.assertEqual(("수익", "0", "0.00%"), (
            parent_metrics[0].label,
            parent_metrics[0].value1,
            parent_metrics[0].value2,
        ))
        self.assertIsNone(
            table.item(0, 0).data(main_table_loader.ROUTINE_STOCK_PROFIT_LED_ROLE)
        )
        instance_rows = [table.item(row, 0) for row in range(1, 4)]
        self.assertEqual(
            {main_table_loader.ROUTINE_ROW_MOCK_INSTANCE},
            {
                item.data(main_table_loader.ROUTINE_ROW_KIND_ROLE)
                for item in instance_rows
            },
        )
        self.assertEqual(
            {"A", "B", "C"},
            {
                item.data(main_table_loader.ROUTINE_INSTANCE_ID_ROLE)
                for item in instance_rows
            },
        )
        row_by_instance = {
            item.data(main_table_loader.ROUTINE_INSTANCE_ID_ROLE): item
            for item in instance_rows
        }
        b_metrics = row_by_instance["B"].data(main_table_loader.ROUTINE_STOCK_METRICS_ROLE)
        self.assertEqual(4, len(b_metrics))
        self.assertTrue(all(isinstance(metric, RatioMetricDisplay) for metric in b_metrics))
        self.assertEqual(("보유", "가격", "수익", "매매"), tuple(metric.label for metric in b_metrics))
        self.assertEqual(("7주", "861"), (b_metrics[0].value1, b_metrics[0].value2))
        self.assertEqual(("123", "0"), (b_metrics[1].value1, b_metrics[1].value2))
        b_values = row_by_instance["B"].data(main_table_loader.ROUTINE_STOCK_VALUES_ROLE)
        self.assertEqual(
            ("주수 1주", "09:00~13:30", "●", "운영중", "루틴", "5분/시장가"),
            tuple(b_values[1:7]),
        )
        self.assertEqual(
            {
                "mode": "QUANTITY",
                "badge": "주수",
                "value": 1,
                "value_text": "1주",
            },
            row_by_instance["B"].data(main_table_loader.ROUTINE_STOCK_INITIAL_BUY_ROLE),
        )
        self.assertEqual(
            "ERROR",
            row_by_instance["C"].data(main_table_loader.ROUTINE_STOCK_TOOLTIP_DATA_ROLE)["state"],
        )
        self.assertEqual(
            "gray",
            row_by_instance["C"].data(main_table_loader.ROUTINE_STOCK_PROFIT_LED_ROLE),
        )
        c_tokens = row_by_instance["C"].data(main_table_loader.ROUTINE_STOCK_DISPLAY_ROLE)
        self.assertEqual("#DC2626", c_tokens[3]["foreground"])
        parent_height = table.rowHeight(0)
        self.assertTrue(
            all(
                table.rowHeight(row) == parent_height
                for row in range(1, 4)
            )
        )
        self.assertNotIn(
            main_table_loader.ROUTINE_ROW_STOCK,
            {
                table.item(row, 0).data(main_table_loader.ROUTINE_ROW_KIND_ROLE)
                for row in range(table.rowCount())
            },
        )

    def test_mock_parent_arrow_collapses_all_children_and_preserves_current_identity(self):
        created = self.create()
        session_id = created["document"]["session"]["validation_session_id"]
        window = self._phase1_mock_table_window()
        window.load_routine_table()
        table = window.routine_table
        table.selectRow(2)

        window._reload_main_routine_table_preserving_view()
        selected = table.selectionModel().selectedRows()
        self.assertEqual(1, len(selected))
        self.assertEqual(
            "B",
            table.item(selected[0].row(), 0).data(
                main_table_loader.ROUTINE_INSTANCE_ID_ROLE
            ),
        )
        parent = table.item(0, 0)
        profit_before = parent.data(main_table_loader.ROUTINE_PARENT_PROFIT_ROLE)
        tooltip_before = parent.toolTip()
        index = table.model().index(0, 0)
        arrow = window._routine_tree_interaction_controller._mock_stock_expand_rect(index)

        QTest.mouseClick(table.viewport(), Qt.LeftButton, pos=arrow.center())
        self.app.processEvents()

        self.assertEqual(1, table.rowCount())
        self.assertEqual(
            ["▶ 005930 삼성전자 (3)"],
            table.item(0, 0).data(main_table_loader.ROUTINE_STOCK_VALUES_ROLE),
        )
        self.assertTrue(
            table.item(0, 0).data(main_table_loader.ROUTINE_PARENT_COLLAPSED_ROLE)
        )
        self.assertEqual(
            {(session_id, "005930")},
            window._collapsed_mock_validation_stock_keys,
        )
        self.assertEqual([], table.selectionModel().selectedRows())
        self.assertEqual(
            profit_before,
            table.item(0, 0).data(main_table_loader.ROUTINE_PARENT_PROFIT_ROLE),
        )
        self.assertEqual(tooltip_before, table.item(0, 0).toolTip())

        index = table.model().index(0, 0)
        arrow = window._routine_tree_interaction_controller._mock_stock_expand_rect(index)
        QTest.mouseClick(table.viewport(), Qt.LeftButton, pos=arrow.center())
        self.app.processEvents()

        self.assertEqual(4, table.rowCount())
        self.assertEqual(
            ["▼ 005930 삼성전자 (3)"],
            table.item(0, 0).data(main_table_loader.ROUTINE_STOCK_VALUES_ROLE),
        )
        self.assertEqual(set(), window._collapsed_mock_validation_stock_keys)

    def test_mock_parent_collapse_is_independent_reload_stable_and_session_scoped(self):
        first = self.create("000660")
        self.create("005930")
        old_session_id = first["document"]["session"]["validation_session_id"]
        window = self._phase1_mock_table_window()
        window.load_routine_table()
        table = window.routine_table

        first_parent_row = next(
            row
            for row in range(table.rowCount())
            if table.item(row, 0).data(main_table_loader.ROUTINE_ROW_KIND_ROLE)
            == main_table_loader.ROUTINE_ROW_MOCK_STOCK
            and table.item(row, 0).data(main_table_loader.ROUTINE_STOCK_CODE_ROLE)
            == "000660"
        )
        index = table.model().index(first_parent_row, 0)
        arrow = window._routine_tree_interaction_controller._mock_stock_expand_rect(index)
        QTest.mouseClick(table.viewport(), Qt.LeftButton, pos=arrow.center())
        self.app.processEvents()

        self.assertEqual(5, table.rowCount())
        self.assertEqual(
            3,
            sum(
                table.item(row, 0).data(main_table_loader.ROUTINE_ROW_KIND_ROLE)
                == main_table_loader.ROUTINE_ROW_MOCK_INSTANCE
                and table.item(row, 0).data(main_table_loader.ROUTINE_STOCK_CODE_ROLE)
                == "005930"
                for row in range(table.rowCount())
            ),
        )
        window.load_routine_table()
        self.assertEqual(5, table.rowCount())
        self.assertIn(
            (old_session_id, "000660"),
            window._collapsed_mock_validation_stock_keys,
        )

        self.actions.unregister("000660")
        recreated = self.create("000660")
        new_session_id = recreated["document"]["session"]["validation_session_id"]
        self.assertNotEqual(old_session_id, new_session_id)
        window.load_routine_table()

        self.assertEqual(8, table.rowCount())
        new_parent = next(
            table.item(row, 0)
            for row in range(table.rowCount())
            if table.item(row, 0).data(main_table_loader.ROUTINE_ROW_KIND_ROLE)
            == main_table_loader.ROUTINE_ROW_MOCK_STOCK
            and table.item(row, 0).data(main_table_loader.ROUTINE_STOCK_CODE_ROLE)
            == "000660"
        )
        self.assertEqual(
            ["▼ 000660 삼성전자 (3)"],
            new_parent.data(main_table_loader.ROUTINE_STOCK_VALUES_ROLE),
        )

    def test_mock_scope_disables_all_twelve_nonfunctional_sort_filter_controls(self):
        valid = QPushButton("유효")
        metrics = {
            key: QPushButton(label)
            for key, label in (
                ("holding", "보유"),
                ("price", "가격"),
                ("profit", "수익"),
                ("trade", "매매"),
                ("limit", "한도"),
            )
        }
        initial = QPushButton("금액")
        columns = {
            key: QPushButton(label)
            for key, label in (
                ("operation", "운영"),
                ("situation", "현황"),
                ("status", "상태"),
                ("method", "방식"),
                ("liquidation", "청산"),
            )
        }
        buttons = [valid, *metrics.values(), initial, *columns.values()]
        clicked = Mock()
        for button in buttons:
            button.clicked.connect(clicked)
        owner = SimpleNamespace(
            _main_routine_stock_scope="mock",
            _main_routine_excluded_only=False,
            _main_routine_valid_only=True,
            _main_routine_valid_button=valid,
            _main_routine_level_buttons={},
            _main_routine_display_level="stock",
            _main_routine_metric_buttons=metrics,
            _main_routine_metric_sort_active=False,
            _main_routine_metric_sort_key="",
            _main_routine_initial_buy_sort_button=initial,
            _main_routine_initial_buy_sort_next_mode="AMOUNT",
            _main_routine_column_sort_buttons=columns,
            _main_routine_column_sort_key="",
            _main_routine_summary_count_buttons={},
            _main_routine_filter_badge_style=MainWindow._main_routine_filter_badge_style,
        )
        owner._main_routine_initial_buy_badge_enabled = lambda: True

        MainWindow._update_main_routine_filter_badges(owner)
        self.assertEqual(12, len(buttons))
        self.assertTrue(all(not button.isEnabled() for button in buttons))
        for button in buttons:
            button.click()
        clicked.assert_not_called()

        owner._main_routine_stock_scope = "all"
        MainWindow._update_main_routine_filter_badges(owner)
        self.assertTrue(all(button.isEnabled() for button in buttons))

    def test_mock_child_non_setting_slots_do_not_dispatch_any_mutation_handler(self):
        created = self.create()
        session_id = created["document"]["session"]["validation_session_id"]
        window = self._phase1_mock_table_window()
        production_handlers = {
            name: Mock()
            for name in (
                "handle_routine_stock_code_double_click",
                "handle_routine_stock_operation_double_click",
                "handle_routine_stock_name_double_click",
                "handle_routine_stock_buy_limit_double_click",
                "toggle_routine_stock_initial_buy_mode",
                "open_routine_stock_initial_buy_dialog",
                "handle_routine_instance_name_double_click",
                "handle_routine_group_name_double_click",
                "toggle_routine_instance_operation",
            )
        }
        for name, handler in production_handlers.items():
            setattr(window, name, handler)
        mock_commands = SimpleNamespace(
            start_instance=Mock(),
            early_close_instance=Mock(),
            immediate_liquidation_instance=Mock(),
            reset_instance=Mock(),
        )
        window.mock_validation_ui_actions = mock_commands
        window.load_routine_table()
        table = window.routine_table
        child_row = 1
        index = table.model().index(child_row, 0)
        controller = window._routine_tree_interaction_controller
        hit_points = [
            controller._stock_legacy_metric_rect(index, column).center()
            for column in (0, 3, 4, 5, 6, 7, 8, 9, 10)
        ]
        for point in hit_points:
            QTest.mouseDClick(table.viewport(), Qt.LeftButton, pos=point)
        self.app.processEvents()

        for handler in production_handlers.values():
            handler.assert_not_called()
        for command in vars(mock_commands).values():
            command.assert_not_called()
        self.assertEqual(
            created["document"]["revision"],
            self.host.current_session("005930")["revision"],
        )
        self.assertEqual(
            session_id,
            table.item(child_row, 0).data(
                main_table_loader.ROUTINE_STOCK_TOOLTIP_DATA_ROLE
            )["validation_session_id"],
        )

    def test_mock_instance_profit_led_uses_mock_pnl_and_is_independent_per_child(self):
        pnl_values = {
            "A": (-10, 0, -10, 2, 0, -12),
            "B": (10, 0, 10, 11, 0, -1),
            "C": (10, 0, 10, 2, 0, 8),
            "D": (0, 0, 0, 0, 0, 0),
        }
        created = self.actions.create_waiting_session(
            _reference(instance_ids=tuple(pnl_values))
        )
        session_id = created["document"]["session"]["validation_session_id"]
        self.actions.start("005930")
        for index, (instance_id, values) in enumerate(pnl_values.items(), start=1):
            self.host.session_service.set_instance_position(
                session_id,
                instance_id,
                holding_qty=1,
                available_qty=1,
                average_price=100,
                realized_cost_basis=100,
                command_id=f"MC-display-position-{index}",
            )
            self.host.session_service.set_instance_pnl(
                session_id,
                instance_id,
                realized_pnl=values[0],
                unrealized_pnl=values[1],
                gross_pnl=values[2],
                commission=values[3],
                mock_tax=values[4],
                net_pnl=values[5],
                command_id=f"MC-display-pnl-{index}",
            )

        table = QTableWidget(0, len(main_table_loader.ROUTINE_MONITORING_HEADERS))
        window = SimpleNamespace(routine_table=table, mock_validation_host=self.host)
        main_table_loader._load_mock_routine_table(window)
        led_by_instance = {
            table.item(row, 0).data(main_table_loader.ROUTINE_INSTANCE_ID_ROLE):
            table.item(row, 0).data(main_table_loader.ROUTINE_STOCK_PROFIT_LED_ROLE)
            for row in range(1, table.rowCount())
        }
        self.assertEqual(
            {"A": "red", "B": "yellow", "C": "green", "D": "gray"},
            led_by_instance,
        )
        profit_values = {
            table.item(row, 0).data(main_table_loader.ROUTINE_INSTANCE_ID_ROLE):
            table.item(row, 0).data(main_table_loader.ROUTINE_STOCK_METRICS_ROLE)[2].value1
            for row in range(1, table.rowCount())
        }
        self.assertEqual({"A": "-12", "B": "-1", "C": "+8", "D": "0"}, profit_values)

    def test_mock_stock_parent_profit_aggregates_amount_and_cost_basis_without_rate_average(self):
        created = self.actions.create_waiting_session(
            _reference(instance_ids=("A", "B"))
        )
        session_id = created["document"]["session"]["validation_session_id"]
        self.actions.start("005930")
        for instance_id, cost_basis, profit in (
            ("A", 1_000_000, 10_000),
            ("B", 500_000, -4_000),
        ):
            self.host.session_service.set_instance_position(
                session_id,
                instance_id,
                holding_qty=1,
                available_qty=1,
                average_price=cost_basis,
                realized_cost_basis=cost_basis,
                command_id=f"MC-parent-position-{instance_id}",
            )
            self.host.session_service.set_instance_pnl(
                session_id,
                instance_id,
                realized_pnl=profit,
                unrealized_pnl=0,
                gross_pnl=profit,
                commission=0,
                mock_tax=0,
                net_pnl=profit,
                command_id=f"MC-parent-pnl-{instance_id}",
            )

        tree = current_mock_monitoring_trees(self.host.repository)[0]
        self.assertEqual((6_000, 1_500_000, 0.4), (
            tree["profit_amount"],
            tree["profit_cost_basis"],
            tree["profit_rate"],
        ))

        table = QTableWidget(0, len(main_table_loader.ROUTINE_MONITORING_HEADERS))
        window = SimpleNamespace(routine_table=table, mock_validation_host=self.host)
        main_table_loader._load_mock_routine_table(window)
        parent = table.item(0, 0)
        self.assertEqual(
            ("수익(+6,000 / +0.40%)", "#DC2626"),
            parent.data(main_table_loader.ROUTINE_PARENT_PROFIT_ROLE),
        )
        parent_metric = parent.data(main_table_loader.ROUTINE_STOCK_METRICS_ROLE)[0]
        self.assertEqual(("+6,000", "+0.40%"), (
            parent_metric.value1,
            parent_metric.value2,
        ))

        self.actions.reset_instance("005930", "A")
        reset_tree = current_mock_monitoring_trees(self.host.repository)[0]
        self.assertEqual((-4_000, 500_000, -0.8), (
            reset_tree["profit_amount"],
            reset_tree["profit_cost_basis"],
            reset_tree["profit_rate"],
        ))
        self.assertEqual(-4_000, reset_tree["children"][1]["net_pnl"])
        main_table_loader._load_mock_routine_table(window)
        self.assertEqual(
            ("수익(-4,000 / -0.80%)", "#2563EB"),
            table.item(0, 0).data(main_table_loader.ROUTINE_PARENT_PROFIT_ROLE),
        )

    def test_mock_rows_render_through_parent_identity_and_production_stock_delegate(self):
        self.create()
        table = QTableWidget(0, len(main_table_loader.ROUTINE_MONITORING_HEADERS))
        table.verticalHeader().setMinimumSectionSize(0)
        table.setColumnWidth(0, 1900)
        table.resize(2000, 300)
        table._main_stock_limit_expanded = True
        table.setItemDelegateForColumn(0, gui_windows._RoutineTreeItemDelegate(table))
        self.addCleanup(table.close)
        window = SimpleNamespace(routine_table=table, mock_validation_host=self.host)
        with (
            patch.object(
                main_table_loader,
                "_main_refresh_stock_data",
                side_effect=AssertionError("Production state reader must not be used"),
            ) as production_state,
            patch.object(
                main_table_loader,
                "project_confirmable_cumulative_pnl",
                side_effect=AssertionError("Production PnL reader must not be used"),
            ) as production_pnl,
        ):
            main_table_loader._load_mock_routine_table(window)
        production_state.assert_not_called()
        production_pnl.assert_not_called()
        with (
            patch.object(
                gui_windows,
                "_draw_initial_buy_display",
                wraps=gui_windows._draw_initial_buy_display,
            ) as initial_buy_renderer,
            patch.object(
                gui_windows,
                "_routine_stock_metric_texts",
                wraps=gui_windows._routine_stock_metric_texts,
            ) as metric_text_renderer,
            patch.object(
                gui_windows,
                "_draw_routine_stock_metric_text_sequence",
                wraps=gui_windows._draw_routine_stock_metric_text_sequence,
            ) as metric_sequence_renderer,
            patch.object(
                gui_windows,
                "draw_stock_position_metric_display",
                wraps=gui_windows.draw_stock_position_metric_display,
            ) as ratio_metric_renderer,
        ):
            table.show()
            self.app.processEvents()
            rendered = table.viewport().grab()

        self.assertFalse(rendered.isNull())
        self.assertTrue(
            any(
                call_args.args[2].get("value_text") == "1주"
                for call_args in initial_buy_renderer.call_args_list
            )
        )
        self.assertEqual(1, len(table.item(0, 0).data(main_table_loader.ROUTINE_STOCK_VALUES_ROLE)))
        self.assertEqual(11, len(table.item(1, 0).data(main_table_loader.ROUTINE_STOCK_VALUES_ROLE)))
        self.assertEqual(
            ("보유", "가격", "수익", "매매"),
            tuple(
                metric.label
                for metric in table.item(1, 0).data(main_table_loader.ROUTINE_STOCK_METRICS_ROLE)
            ),
        )
        child_values = table.item(1, 0).data(main_table_loader.ROUTINE_STOCK_VALUES_ROLE)
        child_metrics = table.item(1, 0).data(main_table_loader.ROUTINE_STOCK_METRICS_ROLE)
        metric_texts = gui_windows._routine_stock_metric_texts(child_values, child_metrics)
        self.assertEqual(4, len(metric_texts))
        self.assertTrue(all(not text.startswith(("한도(", "소모(")) for text in metric_texts))
        self.assertTrue(metric_text_renderer.call_args_list)
        self.assertTrue(
            all(
                call_args.kwargs.get("include_consumed") is False
                for call_args in metric_text_renderer.call_args_list
            )
        )
        self.assertTrue(metric_sequence_renderer.call_args_list)
        self.assertTrue(any(
            call_args.kwargs.get("show_label") is True
            and getattr(call_args.args[2], "label", "") == "수익"
            for call_args in ratio_metric_renderer.call_args_list
        ))
        self.assertTrue(
            all(
                not text.startswith(("한도(", "소모("))
                for call_args in metric_sequence_renderer.call_args_list
                for text in call_args.kwargs.get("texts", ())
            )
        )
        child_index = table.model().index(1, 0)
        mock_geometry = tuple(
            (
                gui_windows._routine_stock_token_rect(table, child_index, slot).left(),
                gui_windows._routine_stock_token_rect(table, child_index, slot).width(),
            )
            for slot in range(11)
        )
        for column in range(table.columnCount()):
            table.item(1, column).setData(
                main_table_loader.ROUTINE_ROW_KIND_ROLE,
                main_table_loader.ROUTINE_ROW_STOCK,
            )
        production_geometry = tuple(
            (
                gui_windows._routine_stock_token_rect(table, child_index, slot).left(),
                gui_windows._routine_stock_token_rect(table, child_index, slot).width(),
            )
            for slot in range(11)
        )
        self.assertEqual(production_geometry, mock_geometry)
        self.assertEqual(main_table_loader.ROUTINE_STOCK_ROW_HEIGHT, table.rowHeight(1))

    def test_mock_parent_reuses_production_tooltip_and_all_children_have_none(self):
        self.actions.create_waiting_session(_reference(instance_ids=("A", "B", "C")))
        table = QTableWidget(0, len(main_table_loader.ROUTINE_MONITORING_HEADERS))
        self.addCleanup(table.close)
        window = SimpleNamespace(routine_table=table, mock_validation_host=self.host)
        metadata = {
            "005930": {
                "market": "KOSPI",
                "nxt_available": True,
                "status": "정상 | 증거금40% | 신용가능",
            }
        }

        with patch.object(
            main_table_loader,
            "_stock_library_tooltip_metadata_by_code",
            return_value=metadata,
        ):
            main_table_loader._load_mock_routine_table(window)

        parent = table.item(0, 0)
        parent_projection = parent.data(main_table_loader.ROUTINE_STOCK_TOOLTIP_DATA_ROLE)
        production_tooltip = main_table_loader.main_stock_row_tooltip_from_projection(
            {
                "market": "KOSPI",
                "stock_code": "005930",
                "stock_name": "삼성전자",
                "current_price": None,
                "nxt_available": True,
                "stock_state": {},
                "stock_status": "정상 | 증거금40% | 신용가능",
            }
        )

        self.assertEqual(production_tooltip, parent.toolTip())
        self.assertEqual(
            production_tooltip,
            main_table_loader.main_stock_row_tooltip_from_projection(parent_projection),
        )
        self.assertNotIn("모의검증 종목", parent.toolTip())
        for row in range(1, 4):
            child = table.item(row, 0)
            self.assertEqual("", child.toolTip())
            child_context = child.data(
                main_table_loader.ROUTINE_STOCK_TOOLTIP_DATA_ROLE
            )
            self.assertTrue(child_context["mock_validation"])
            self.assertNotIn("stock_code", child_context)
            self.assertNotIn("market", child_context)
            self.assertEqual(
                main_table_loader.ROUTINE_ROW_MOCK_INSTANCE,
                child.data(main_table_loader.ROUTINE_ROW_KIND_ROLE),
            )
            self.assertTrue(child.data(main_table_loader.ROUTINE_INSTANCE_ID_ROLE))

    def test_multiple_mock_stocks_keep_their_own_production_tooltip_projection(self):
        self.actions.create_waiting_session(
            _reference("005930", ("A",), stock_name="삼성전자")
        )
        self.actions.create_waiting_session(
            _reference("000660", ("B",), stock_name="SK하이닉스")
        )
        table = QTableWidget(0, len(main_table_loader.ROUTINE_MONITORING_HEADERS))
        self.addCleanup(table.close)
        window = SimpleNamespace(routine_table=table, mock_validation_host=self.host)
        metadata = {
            "005930": {
                "market": "KOSPI",
                "nxt_available": True,
                "status": "정상 | 증거금40% | 신용가능",
            },
            "000660": {
                "market": "KOSPI",
                "nxt_available": False,
                "status": "정상 | 증거금20%",
            },
        }

        with patch.object(
            main_table_loader,
            "_stock_library_tooltip_metadata_by_code",
            return_value=metadata,
        ):
            main_table_loader._load_mock_routine_table(window)

        rows_by_code = {}
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            code = item.data(main_table_loader.ROUTINE_STOCK_CODE_ROLE)
            rows_by_code.setdefault(code, []).append(item)

        self.assertEqual({"005930", "000660"}, set(rows_by_code))
        for code, expected_name in (("005930", "삼성전자"), ("000660", "SK하이닉스")):
            parent, child = rows_by_code[code]
            self.assertIn(f"{code} {expected_name}", parent.toolTip())
            self.assertEqual("", child.toolTip())
            self.assertEqual(
                code,
                parent.data(main_table_loader.ROUTINE_STOCK_TOOLTIP_DATA_ROLE)[
                    "stock_code"
                ],
            )
            child_context = child.data(
                main_table_loader.ROUTINE_STOCK_TOOLTIP_DATA_ROLE
            )
            self.assertTrue(child_context["mock_validation"])
            self.assertNotIn("stock_code", child_context)
            self.assertNotIn("market", child_context)
        self.assertIn("NXT", rows_by_code["005930"][0].toolTip())
        self.assertNotIn("NXT", rows_by_code["000660"][0].toolTip())
        self.assertNotEqual(
            rows_by_code["005930"][0].toolTip(),
            rows_by_code["000660"][0].toolTip(),
        )

    def test_authenticated_live_stock_tooltip_matches_parent_and_excludes_child(self):
        self.actions.create_waiting_session(
            _reference("0009K0", ("A",), stock_name="에임드바이오")
        )
        table = QTableWidget(0, len(main_table_loader.ROUTINE_MONITORING_HEADERS))
        self.addCleanup(table.close)
        live_state = SimpleNamespace(
            last_price=70000,
            open_price=69000,
            high_price=71000,
            low_price=68000,
            change_rate=1.25,
            previous_day_volume_rate=-12.43,
            execution_strength=117.2,
        )
        market_state = Mock(return_value=live_state)
        main_host = SimpleNamespace(monitoring_market_information_state=market_state)
        owner = SimpleNamespace(
            routine_table=table,
            mock_validation_host=self.host,
            main_monitoring_auto_trade_operation_host=Mock(return_value=main_host),
        )
        owner._main_stock_live_tooltip = lambda index, fallback: (
            MainWindow._main_stock_live_tooltip(owner, index, fallback)
        )
        MainWindow._setup_routine_table(owner)
        metadata = {
            "0009K0": {
                "market": "KOSDAQ",
                "nxt_available": True,
                "status": "정상 | 증거금40% | 신용가능",
            }
        }
        with patch.object(
            main_table_loader,
            "_stock_library_tooltip_metadata_by_code",
            return_value=metadata,
        ):
            main_table_loader._load_mock_routine_table(owner)

        table.show()
        self.app.processEvents()
        parent_index = table.model().index(0, 0)
        child_index = table.model().index(1, 0)
        parent_tooltip = owner._routine_stock_name_tooltip_filter._tooltip_text(
            parent_index,
            table.visualRect(parent_index).center(),
        )
        child_tooltip = owner._routine_stock_name_tooltip_filter._tooltip_text(
            child_index,
            table.visualRect(child_index).center(),
        )
        production_tooltip = main_table_loader.main_stock_row_tooltip_from_projection(
            table.item(0, 0).data(main_table_loader.ROUTINE_STOCK_TOOLTIP_DATA_ROLE),
            live_state,
        )

        self.assertEqual(production_tooltip, parent_tooltip)
        self.assertEqual("", child_tooltip)
        for expected in (
            "0009K0 에임드바이오",
            "KOSDAQ",
            "상태 정상",
            "NXT",
            "현재가 70,000",
            "시가 69,000",
            "고가 71,000",
            "저가 68,000",
            "등락률 +1.25%",
            "전일대비 -12.43%",
            "체결강도 117.2",
        ):
            self.assertIn(expected, parent_tooltip)
        self.assertEqual([call("0009K0")], market_state.call_args_list)

    def test_legacy_session_uses_read_only_production_visual_projection_without_backfill(self):
        stock_dir = self.project_root / "stocks" / "005930_삼성전자"
        stock_dir.mkdir(parents=True)
        production_config = stock_dir / "config.json"
        production_config.write_text(
            '{"trade_amount_type":"QUANTITY","buy_qty":7}',
            encoding="utf-8",
        )
        production_before = production_config.read_bytes()
        created = self.actions.create_waiting_session(_reference(include_display=False))
        session_id = created["document"]["session"]["validation_session_id"]
        before_legacy = self.host.repository.read_session(session_id)
        self.host.repository.mutate_session(
            session_id,
            lambda document: (
                document.pop("effective_settings_by_instance", None),
                document,
            )[1],
            expected_revision=before_legacy["revision"],
        )
        session_path = self.host.repository.root / self.host.repository._session_relative(session_id)
        session_before = session_path.read_bytes()
        table = QTableWidget(0, len(main_table_loader.ROUTINE_MONITORING_HEADERS))
        table.setColumnWidth(0, 1900)
        table.resize(2000, 300)
        table._main_stock_limit_expanded = True
        table.setItemDelegateForColumn(0, gui_windows._RoutineTreeItemDelegate(table))
        self.addCleanup(table.close)
        window = SimpleNamespace(routine_table=table, mock_validation_host=self.host)

        initial_buy = {
            "mode": "QUANTITY",
            "badge": "주수",
            "value": 7,
            "value_text": "7주",
        }
        with (
            patch.object(
                main_table_loader,
                "_main_refresh_stock_data",
                side_effect=AssertionError("Production state must not be read for Mock display"),
            ) as production_state_reader,
            patch.object(
                main_table_loader,
                "main_stock_resolved_initial_buy_display",
                return_value=initial_buy,
            ) as initial_projection,
            patch.object(
                main_table_loader,
                "auto_trade_operation_display",
                return_value=("09:10~14:20", "#000000", "", []),
            ) as schedule_projection,
            patch.object(
                main_table_loader,
                "auto_trade_setting_liquidation_text",
                return_value="7분/현재가",
            ) as liquidation_projection,
        ):
            main_table_loader._load_mock_routine_table(window)

        production_state_reader.assert_not_called()
        initial_projection.assert_called_once()
        schedule_projection.assert_called_once_with(
            {"trade_amount_type": "QUANTITY", "buy_qty": 7}
        )
        liquidation_projection.assert_called_once_with(
            {"trade_amount_type": "QUANTITY", "buy_qty": 7}
        )
        values = table.item(1, 0).data(main_table_loader.ROUTINE_STOCK_VALUES_ROLE)
        self.assertEqual(
            ("주수 7주", "09:10~14:20", "7분/현재가"),
            (values[1], values[2], values[6]),
        )
        self.assertEqual(
            initial_buy,
            table.item(1, 0).data(main_table_loader.ROUTINE_STOCK_INITIAL_BUY_ROLE),
        )
        document = self.host.current_session("005930")
        self.assertNotIn("display_contract", document["reference_snapshot"])
        self.assertEqual(session_before, session_path.read_bytes())
        with (
            patch.object(
                gui_windows,
                "_routine_stock_metric_texts",
                wraps=gui_windows._routine_stock_metric_texts,
            ) as metric_text_renderer,
            patch.object(
                gui_windows,
                "_draw_routine_stock_metric_text_sequence",
                wraps=gui_windows._draw_routine_stock_metric_text_sequence,
            ) as metric_sequence_renderer,
        ):
            table.show()
            self.app.processEvents()
            rendered = table.viewport().grab()

        self.assertFalse(rendered.isNull())
        self.assertTrue(metric_text_renderer.call_args_list)
        self.assertTrue(
            all(
                call_args.kwargs.get("include_consumed") is False
                for call_args in metric_text_renderer.call_args_list
            )
        )
        self.assertTrue(
            all(
                not text.startswith(("한도(", "소모("))
                for call_args in metric_sequence_renderer.call_args_list
                for text in call_args.kwargs.get("texts", ())
            )
        )

    def test_frozen_display_contract_precedes_legacy_compatibility_projection(self):
        stock_dir = self.project_root / "stocks" / "005930_삼성전자"
        stock_dir.mkdir(parents=True)
        (stock_dir / "config.json").write_text(
            '{"trade_amount_type":"QUANTITY","buy_qty":99}',
            encoding="utf-8",
        )
        self.create()
        table = QTableWidget(0, len(main_table_loader.ROUTINE_MONITORING_HEADERS))
        window = SimpleNamespace(routine_table=table, mock_validation_host=self.host)

        with patch.object(
            main_table_loader,
            "_legacy_mock_display_contract",
            side_effect=AssertionError("frozen display contract must take precedence"),
        ) as compatibility_projection:
            main_table_loader._load_mock_routine_table(window)

        compatibility_projection.assert_not_called()
        values = table.item(1, 0).data(main_table_loader.ROUTINE_STOCK_VALUES_ROLE)
        self.assertEqual(
            ("주수 1주", "09:00~13:30", "5분/시장가"),
            (values[1], values[2], values[6]),
        )

    def test_shared_renderer_consumed_expansion_isolated_across_scope_round_trip(self):
        self.actions.create_waiting_session(_reference(instance_ids=("A",)))
        table = QTableWidget(0, len(main_table_loader.ROUTINE_MONITORING_HEADERS))
        table.setColumnWidth(0, 1900)
        table.resize(2000, 300)
        table.setItemDelegateForColumn(0, gui_windows._RoutineTreeItemDelegate(table))
        window = SimpleNamespace(routine_table=table, mock_validation_host=self.host)
        main_table_loader._load_mock_routine_table(window)
        anchor = table.item(1, 0)

        def render_metrics(*, row_kind, expanded):
            for column in range(table.columnCount()):
                item = table.item(1, column)
                if item is not None:
                    item.setData(main_table_loader.ROUTINE_ROW_KIND_ROLE, row_kind)
            table._main_stock_limit_expanded = expanded
            with (
                patch.object(
                    gui_windows,
                    "_routine_stock_metric_texts",
                    wraps=gui_windows._routine_stock_metric_texts,
                ) as metric_text_renderer,
                patch.object(
                    gui_windows,
                    "_draw_routine_stock_metric_text_sequence",
                    wraps=gui_windows._draw_routine_stock_metric_text_sequence,
                ) as metric_sequence_renderer,
            ):
                table.show()
                table.viewport().update()
                self.app.processEvents()
                rendered = table.viewport().grab()
            self.assertFalse(rendered.isNull())
            flags = tuple(
                call_args.kwargs.get("include_consumed")
                for call_args in metric_text_renderer.call_args_list
            )
            texts = tuple(
                text
                for call_args in metric_sequence_renderer.call_args_list
                for text in call_args.kwargs.get("texts", ())
            )
            return flags, texts

        production_expanded_flags, production_expanded_texts = render_metrics(
            row_kind=main_table_loader.ROUTINE_ROW_STOCK,
            expanded=True,
        )
        self.assertTrue(production_expanded_flags)
        self.assertTrue(all(flag is True for flag in production_expanded_flags))
        self.assertTrue(any(text.startswith("소모(") for text in production_expanded_texts))

        production_collapsed_flags, production_collapsed_texts = render_metrics(
            row_kind=main_table_loader.ROUTINE_ROW_STOCK,
            expanded=False,
        )
        self.assertTrue(production_collapsed_flags)
        self.assertTrue(all(flag is False for flag in production_collapsed_flags))
        self.assertTrue(all(not text.startswith("소모(") for text in production_collapsed_texts))

        mock_expanded_flags, mock_expanded_texts = render_metrics(
            row_kind=main_table_loader.ROUTINE_ROW_MOCK_INSTANCE,
            expanded=True,
        )
        self.assertTrue(mock_expanded_flags)
        self.assertTrue(all(flag is False for flag in mock_expanded_flags))
        self.assertTrue(all(not text.startswith("소모(") for text in mock_expanded_texts))

        production_return_flags, production_return_texts = render_metrics(
            row_kind=main_table_loader.ROUTINE_ROW_STOCK,
            expanded=True,
        )
        self.assertTrue(production_return_flags)
        self.assertTrue(all(flag is True for flag in production_return_flags))
        self.assertTrue(any(text.startswith("소모(") for text in production_return_texts))
        self.assertEqual("A", anchor.data(main_table_loader.ROUTINE_INSTANCE_ID_ROLE))

    def test_main_registration_freezes_official_display_projections_once(self):
        stock_dir = self.project_root / "stocks" / "005930_삼성전자"
        stock_dir.mkdir(parents=True)
        production_config = stock_dir / "config.json"
        production_config.write_text(
            '{"trade_amount_type":"AMOUNT","buy_amount":999999,"operation_mode":"SCHEDULED","start_time":"08:11"}',
            encoding="utf-8",
        )
        production_before = production_config.read_bytes()
        rules_path = self.project_root / "rules.json"
        rules_path.write_text('{"schema_version":"fixture"}', encoding="utf-8")
        instance = SimpleNamespace(
            instance_id="A",
            definition_id="indicator_follow",
            routine_type="INDICATOR_FOLLOW",
            display_name="지표추종매매A",
            group_id="group-1",
            rules_path=rules_path,
        )
        target = SimpleNamespace(
            code="005930",
            name="삼성전자",
            stock_dir=stock_dir,
            routine_instance_id="A",
        )
        actions = SimpleNamespace(create_waiting_session=Mock())
        owner = SimpleNamespace(
            mock_validation_host=SimpleNamespace(current_session=Mock(return_value=None)),
            mock_validation_ui_actions=actions,
            _select_mock_validation_instances=Mock(return_value=[instance]),
        )
        initial_buy = {
            "mode": "QUANTITY",
            "badge": "주수",
            "value": 7,
            "value_text": "7주",
        }
        with (
            patch.object(
                gui_windows,
                "default_config",
                return_value={
                    "trade_amount_type": "QUANTITY",
                    "operation_mode": "CONTINUOUS",
                },
            ) as production_defaults,
            patch.object(
                gui_windows,
                "starting_budget_defaults",
                return_value={
                    "quantity": 7,
                    "amount_multiplier": 2.75,
                    "limit_recommended_multiplier": 100.0,
                    "limit_minimum_multiplier": 25.0,
                },
            ) as budget_defaults,
            patch.object(
                gui_windows,
                "read_global_schedule",
                return_value={
                    "start_time": "10:15:00",
                    "end_buy_time": "14:05:00",
                },
            ) as schedule_defaults,
            patch.object(
                gui_windows,
                "main_stock_resolved_initial_buy_display",
                return_value=initial_buy,
            ) as initial_projection,
            patch.object(
                gui_windows,
                "auto_trade_setting_liquidation_text",
                return_value="7분/현재가",
            ) as liquidation_projection,
            patch.object(gui_windows, "show_toast"),
        ):
            self.assertTrue(MainWindow.begin_mock_validation(owner, target))

        production_defaults.assert_called_once_with()
        budget_defaults.assert_called_once_with()
        schedule_defaults.assert_called_once_with()
        initial_projection.assert_called_once()
        liquidation_projection.assert_called_once_with(
            {"trade_amount_type": "QUANTITY", "operation_mode": "CONTINUOUS"}
        )
        snapshot = actions.create_waiting_session.call_args.args[0]
        self.assertEqual(production_before, production_config.read_bytes())
        self.assertEqual(
            {
                "initial_buy": initial_buy,
                "operation_schedule": {"display_text": "수동"},
                "liquidation": {"display_text": "7분/현재가"},
            },
            snapshot["display_contract"],
        )
        self.assertEqual(
            {
                "A": {
                    "initial_buy": {"mode": "QUANTITY", "value": 7},
                    "operation_schedule": {
                        "start_time": "10:15:00",
                        "end_buy_time": "14:05:00",
                    },
                    "operation_mode": "CONTINUOUS",
                    "manual_ats": {
                        "selected_sessions": [],
                    },
                }
            },
            actions.create_waiting_session.call_args.kwargs[
                "effective_settings_by_instance"
            ],
        )
        production_config.write_text(
            '{"trade_amount_type":"AMOUNT","buy_amount":999999}',
            encoding="utf-8",
        )
        self.assertEqual("7주", snapshot["display_contract"]["initial_buy"]["value_text"])
        self.assertEqual("수동", snapshot["display_contract"]["operation_schedule"]["display_text"])
        self.assertEqual("7분/현재가", snapshot["display_contract"]["liquidation"]["display_text"])

    def _mock_context_window(self):
        table = QTableWidget(0, len(main_table_loader.ROUTINE_MONITORING_HEADERS))
        table.setColumnWidth(0, 1800)
        table.resize(2000, 500)
        table.setContextMenuPolicy(Qt.CustomContextMenu)
        results = []
        window = SimpleNamespace(
            routine_table=table,
            mock_validation_host=self.host,
            mock_validation_ui_actions=self.actions,
            _mock_action_result=lambda _title, operation: results.append(operation()),
            open_mock_validation_event_window=Mock(),
            kiwoom_api=self.api,
            selected_account_no=lambda: "12345678",
            _account_authentication_states={"12345678": "READY"},
        )
        window._main_routine_selected_row_keys = MethodType(
            MainWindow._main_routine_selected_row_keys,
            window,
        )
        window._reload_main_routine_table_preserving_view = MethodType(
            MainWindow._reload_main_routine_table_preserving_view,
            window,
        )
        window.load_routine_table = lambda: main_table_loader._load_mock_routine_table(window)
        for name in (
            "_mock_routine_instance_edit_target",
            "_mock_operation_settings_edit_authorized",
            "_write_mock_routine_instance_settings",
            "open_mock_routine_instance_schedule_dialog",
            "reset_mock_routine_instance_schedule",
            "mock_routine_instance_ats_state",
            "set_mock_routine_instance_ats_flag",
        ):
            setattr(window, name, MethodType(getattr(MainWindow, name), window))
        main_table_loader._load_mock_routine_table(window)
        table.customContextMenuRequested.connect(
            lambda position: MainWindow.open_routine_context_menu(window, position)
        )
        table.show()
        self.app.processEvents()
        return window, results

    @staticmethod
    def _row_for_instance(table, instance_id):
        return next(
            row
            for row in range(table.rowCount())
            if table.item(row, 0).data(main_table_loader.ROUTINE_INSTANCE_ID_ROLE)
            == instance_id
        )

    def test_actual_context_signal_dispatches_parent_and_exact_instance_without_production_adapter(self):
        self.create()
        window, results = self._mock_context_window()
        table = window.routine_table
        parent_position = table.visualItemRect(table.item(0, 0)).center()
        child_row = self._row_for_instance(table, "B")
        child_position = table.visualItemRect(table.item(child_row, 0)).center()

        _Menu.chosen_text = ""
        _Menu.chosen_menu_title = None
        with (
            patch.object(mock_context_menu, "QMenu", _Menu),
            patch.object(
                gui_windows,
                "show_main_monitoring_stock_context_menu",
                side_effect=AssertionError("Production adapter must not receive a Mock row"),
            ) as production_menu,
        ):
            table.customContextMenuRequested.emit(parent_position)
            self.assertEqual(
                ["운영일지", "등록해제"],
                [action.text() for action in _Menu.root.actions],
            )

            _Menu.chosen_text = "운영일지"
            table.customContextMenuRequested.emit(parent_position)
            window.open_mock_validation_event_window.assert_called_once_with(
                "005930",
                expected_validation_session_id=self.host.current_session("005930")[
                    "session"
                ]["validation_session_id"],
            )

            _Menu.chosen_text = "운영시작"
            with patch.object(
                self.actions,
                "start_instance",
                wraps=self.actions.start_instance,
            ) as start_instance:
                table.customContextMenuRequested.emit(child_position)
            start_instance.assert_called_once_with("005930", "B")
            production_menu.assert_not_called()

        document = self.host.current_session("005930")
        self.assertEqual("RUNNING", document["instance_execution"]["B"]["state"])
        self.assertEqual("WAITING", document["instance_execution"]["A"]["state"])
        self.assertEqual("WAITING", document["instance_execution"]["C"]["state"])
        self.assertTrue(results)
        child_root_text = [action.text() for action in _Menu.root.actions]
        self.assertIn("리셋", child_root_text)
        self.assertNotIn("종목리셋", child_root_text)
        self.assertIn("검증정지", child_root_text)
        self.assertIn("간이차트", child_root_text)
        self.assertNotIn("운영일지", child_root_text)
        self.assertIn("시간변경", child_root_text)
        self.assertIn("변경리셋", child_root_text)
        self.assertEqual(
            ["조기마감", "개별청산"],
            [submenu.title for submenu in _Menu.root.submenus],
        )
        for hidden in (
            "검토정지",
            "운영제외",
            "제외해제",
            "ATS설정",
            "종목등록",
        ):
            self.assertNotIn(hidden, child_root_text)

    def test_mock_select_all_and_clear_target_children_only(self):
        self.create()
        window, _results = self._mock_context_window()
        table = window.routine_table
        child_row = self._row_for_instance(table, "B")
        position = table.visualItemRect(table.item(child_row, 0)).center()
        _Menu.chosen_text = "전체선택"
        _Menu.chosen_menu_title = None
        with patch.object(mock_context_menu, "QMenu", _Menu):
            table.customContextMenuRequested.emit(position)
        self.assertFalse(table.item(0, 0).isSelected())
        self.assertTrue(all(table.item(row, 0).isSelected() for row in range(1, 4)))

        _Menu.chosen_text = "선택해제"
        with patch.object(mock_context_menu, "QMenu", _Menu):
            table.customContextMenuRequested.emit(position)

    def test_mock_continuous_context_reuses_production_ats_menu_and_reads_back(self):
        self.create()
        self.actions.set_instance_effective_settings(
            "005930", "A", operation_mode="CONTINUOUS"
        )
        window, _results = self._mock_context_window()
        table = window.routine_table

        def operation_display():
            row = self._row_for_instance(table, "A")
            item = table.item(row, 0)
            values = item.data(main_table_loader.ROUTINE_STOCK_VALUES_ROLE)
            tokens = item.data(main_table_loader.ROUTINE_STOCK_DISPLAY_ROLE)
            return values[2], tokens[2]

        def expected_operation_style():
            settings = self.host.current_session("005930")[
                "effective_settings_by_instance"
            ]["A"]
            schedule = settings["operation_schedule"]
            manual_ats = settings["manual_ats"]
            descriptor = main_table_loader.auto_trade_operation_display(
                {
                    "operation_mode": settings["operation_mode"],
                    "start_time": schedule["start_time"],
                    "end_buy_time": schedule["end_buy_time"],
                },
                {
                    "manual_ats_selection": {
                        "selected_sessions": list(
                            manual_ats["selected_sessions"]
                        ),
                    }
                },
            )
            return main_table_loader._item_style_snapshot(
                main_table_loader.create_auto_trade_operation_item(descriptor)
            )

        manual_text, manual_style = operation_display()
        self.assertEqual("수동", manual_text)
        self.assertEqual(expected_operation_style(), manual_style)

        def choose(text, menu_title):
            row = self._row_for_instance(table, "A")
            position = table.visualItemRect(table.item(row, 0)).center()
            _Menu.chosen_text = text
            _Menu.chosen_menu_title = menu_title
            with (
                patch.object(mock_context_menu, "QMenu", _Menu),
                patch.object(
                    common_menu,
                    "manual_ats_visible_session_keys",
                    return_value=("extra1", "extra2"),
                ),
                patch.object(
                    common_menu,
                    "manual_ats_session_labels",
                    return_value={"extra1": "장전프리", "extra2": "장마감NTX"},
                ),
            ):
                table.customContextMenuRequested.emit(position)

        choose("장전프리", "ATS설정")
        ats_text, ats_style = operation_display()
        self.assertEqual("수동+ATS", ats_text)
        self.assertEqual(expected_operation_style(), ats_style)
        self.assertFalse(table.viewport().grab().isNull())
        settings = self.host.current_session("005930")[
            "effective_settings_by_instance"
        ]["A"]
        self.assertEqual("CONTINUOUS", settings["operation_mode"])
        self.assertEqual(
            {
                "selected_sessions": ["extra1"],
            },
            settings["manual_ats"],
        )

        choose("", None)
        ats_menu = next(menu for menu in _Menu.root.submenus if menu.title == "ATS설정")
        self.assertTrue(ats_menu.isEnabled())
        self.assertTrue(
            next(action for action in ats_menu.actions if action.text() == "장전프리").property(
                "atsSessionCurrent"
            )
        )
        self.assertFalse(any(menu.title == "주문방식" for menu in ats_menu.submenus))
        self.assertNotIn("시간변경", [action.text() for action in _Menu.root.actions])
        self.assertFalse(any(table.item(row, 0).isSelected() for row in range(1, 4)))

        choose("장전프리", "ATS설정")
        cleared_text, cleared_style = operation_display()
        self.assertEqual("수동", cleared_text)
        self.assertEqual(expected_operation_style(), cleared_style)
        choose("장전프리", "ATS설정")
        restored_text, restored_style = operation_display()
        self.assertEqual("수동+ATS", restored_text)
        self.assertEqual(expected_operation_style(), restored_style)

    def test_mock_scheduled_context_routes_time_change_to_production_dialog_presenter(self):
        self.create()
        window, _results = self._mock_context_window()
        table = window.routine_table
        row = self._row_for_instance(table, "A")
        position = table.visualItemRect(table.item(row, 0)).center()
        presenter = Mock()
        window.open_mock_routine_instance_schedule_dialog = presenter
        _Menu.chosen_text = "시간변경"
        _Menu.chosen_menu_title = None

        with patch.object(mock_context_menu, "QMenu", _Menu):
            table.customContextMenuRequested.emit(position)

        presenter.assert_called_once_with(row)
        self.assertIn("시간변경", [action.text() for action in _Menu.root.actions])
        self.assertNotIn("ATS설정", [menu.title for menu in _Menu.root.submenus])

    def test_production_and_mock_same_stock_actual_dispatch_keeps_production_menu(self):
        self.create()
        table = QTableWidget(1, 1)
        item = QTableWidgetItem("삼성전자")
        item.setData(main_table_loader.ROUTINE_ROW_KIND_ROLE, main_table_loader.ROUTINE_ROW_STOCK)
        item.setData(main_table_loader.ROUTINE_STOCK_CODE_ROLE, "005930")
        item.setData(main_table_loader.ROUTINE_STOCK_NAME_ROLE, "삼성전자")
        item.setData(main_table_loader.ROUTINE_INSTANCE_ID_ROLE, "PRODUCTION-A")
        table.setItem(0, 0, item)
        table.resize(500, 100)
        table.show()
        window = SimpleNamespace(routine_table=table, mock_validation_host=self.host)
        table.setContextMenuPolicy(Qt.CustomContextMenu)
        table.customContextMenuRequested.connect(
            lambda position: MainWindow.open_routine_context_menu(window, position)
        )
        self.app.processEvents()
        position = table.visualItemRect(item).center()
        with (
            patch.object(gui_windows, "show_main_monitoring_stock_context_menu") as production_menu,
            patch.object(gui_windows, "show_mock_monitoring_context_menu") as mock_menu,
        ):
            table.customContextMenuRequested.emit(position)
        production_menu.assert_called_once_with(window, position)
        mock_menu.assert_not_called()

    def test_actual_child_menu_close_liquidation_and_reset_remain_instance_local(self):
        created = self.create()
        session_id = created["document"]["session"]["validation_session_id"]
        window, _results = self._mock_context_window()
        table = window.routine_table
        child_row = self._row_for_instance(table, "B")
        position = table.visualItemRect(table.item(child_row, 0)).center()

        def choose(text, menu_title=None):
            _Menu.chosen_text = text
            _Menu.chosen_menu_title = menu_title
            with patch.object(mock_context_menu, "QMenu", _Menu):
                table.customContextMenuRequested.emit(position)

        choose("운영시작")
        choose("이월", "조기마감")
        closing = self.host.current_session("005930")
        self.assertEqual(
            "CLOSING",
            closing["mock_operation_lifecycle"]["instance_operations"]["B"]["state"],
        )
        self.assertEqual("WAITING", closing["instance_execution"]["A"]["state"])
        self.assertEqual("WAITING", closing["instance_execution"]["C"]["state"])

        choose("리셋")
        reset = self.host.current_session("005930")
        self.assertEqual("WAITING", reset["instance_execution"]["B"]["state"])
        self.assertNotIn(
            "B", reset["mock_operation_lifecycle"].get("instance_operations", {})
        )

        choose("운영시작")
        self.host.session_service.set_instance_position(
            session_id,
            "B",
            holding_qty=3,
            available_qty=3,
            average_price=100,
            realized_cost_basis=300,
            command_id="MC-ui-position-B",
        )
        _Menu.chosen_text = "시장가"
        _Menu.chosen_menu_title = "개별청산"
        with (
            patch.object(mock_context_menu, "QMenu", _Menu),
            patch.object(mock_context_menu.QMessageBox, "question", return_value=QMessageBox.Yes),
        ):
            table.customContextMenuRequested.emit(position)
        liquidating = self.host.current_session("005930")
        operation = liquidating["mock_operation_lifecycle"]["instance_operations"]["B"]
        self.assertEqual("IMMEDIATE", operation["close_source"])
        self.assertEqual("CLOSING", operation["state"])
        self.assertEqual(0, next(
            item["holding_qty"]
            for item in liquidating["positions"]
            if item["routine_instance_id"] == "A"
        ))
        self.assertEqual("WAITING", liquidating["instance_execution"]["A"]["state"])
        self.assertEqual("WAITING", liquidating["instance_execution"]["C"]["state"])

    def test_host_processes_instance_liquidation_without_sibling_progression(self):
        created = self.create()
        session_id = created["document"]["session"]["validation_session_id"]
        for instance_id in ("A", "C"):
            self.actions.set_instance_effective_settings(
                "005930",
                instance_id,
                operation_mode="MANUAL",
            )
        self.host.start_instance_operation("005930", "B", as_of=NOW)
        self.host.session_service.set_instance_position(
            session_id,
            "B",
            holding_qty=3,
            available_qty=3,
            average_price=100,
            realized_cost_basis=300,
            command_id="MC-host-position-B",
        )
        self.assertTrue(self.host.accept_orderbook(_book()))
        self.host.request_instance_immediate_liquidation(
            "005930", "B", method="시장가", as_of=NOW
        )
        self.host.routine_adapter.evaluate_cycle = Mock()
        self.host.process_due_cycles(as_of=NOW + timedelta(milliseconds=100))
        self.host.process_due_cycles(as_of=NOW + timedelta(seconds=1))
        document = self.host.current_session("005930")
        positions = {
            item["routine_instance_id"]: item["holding_qty"]
            for item in document["positions"]
        }
        self.assertEqual({"A": 0, "B": 0, "C": 0}, positions)
        self.assertEqual("ENDED", document["instance_execution"]["B"]["state"])
        self.assertEqual("WAITING", document["instance_execution"]["A"]["state"])
        self.assertEqual("WAITING", document["instance_execution"]["C"]["state"])
        self.host.routine_adapter.evaluate_cycle.assert_not_called()

    def test_stale_mock_row_identity_fails_closed_without_production_fallback(self):
        self.create()
        window, results = self._mock_context_window()
        table = window.routine_table
        child_row = self._row_for_instance(table, "B")
        first = table.item(child_row, 0)
        projection = dict(first.data(main_table_loader.ROUTINE_STOCK_TOOLTIP_DATA_ROLE))
        projection["validation_session_id"] = "MV-stale"
        first.setData(main_table_loader.ROUTINE_STOCK_TOOLTIP_DATA_ROLE, projection)
        position = table.visualItemRect(first).center()
        _Menu.chosen_text = "운영시작"
        _Menu.chosen_menu_title = None
        _Menu.root = None
        with (
            patch.object(mock_context_menu, "QMenu", _Menu),
            patch.object(gui_windows, "show_main_monitoring_stock_context_menu") as production_menu,
        ):
            table.customContextMenuRequested.emit(position)
        self.assertIsNone(_Menu.root)
        production_menu.assert_not_called()
        self.assertEqual([], results)

    def test_auto_close_due_is_idempotent_and_stops_routine_progression(self):
        self.create()
        self.actions.start("005930")
        self.host.routine_adapter.evaluate_cycle = Mock()
        due = NOW.replace(hour=15, minute=25, second=0)
        self.host.process_due_cycles(as_of=due)
        document = self.host.current_session("005930")
        self.assertEqual("CLOSING", document["session"]["state"])
        self.assertEqual("AUTO", document["mock_operation_lifecycle"]["current"]["close_source"])
        called = self.host.routine_adapter.evaluate_cycle.call_count
        self.host.process_due_cycles(as_of=due + timedelta(seconds=1))
        self.assertEqual(called, self.host.routine_adapter.evaluate_cycle.call_count)

    def test_unregister_is_mock_only_and_purges_completed_lifecycle(self):
        created = self.create()
        session_id = created["document"]["session"]["validation_session_id"]
        open_reader = MockEventReaderAdapter(self.host.repository, session_id)
        completed = self.actions.unregister("005930")
        self.assertTrue(completed["ok"])
        self.assertIsNone(self.host.current_session("005930"))
        self.assertEqual([], self.host.repository.read_events(session_id))
        self.assertEqual(0, open_reader.read_events()["count"])
        self.assertEqual(0, completed["purge"]["events_remaining"])
        with self.assertRaisesRegex(MockValidationError, "MOCK_SESSION_NOT_FOUND"):
            self.host.repository.read_session(session_id)
        with self.assertRaisesRegex(
            MockValidationError,
            "MOCK_HISTORY_NOT_FOUND_OR_INVALID",
        ):
            self.host.repository.read_history(session_id)

    def test_unregister_purges_only_target_stock_lifecycle_and_all_instance_events(self):
        first = self.actions.create_waiting_session(
            _reference("005380", ("A", "B", "C"), stock_name="현대차")
        )
        second = self.actions.create_waiting_session(
            _reference("032680", ("A",), stock_name="소프트센")
        )
        first_id = first["document"]["session"]["validation_session_id"]
        second_id = second["document"]["session"]["validation_session_id"]
        for index, instance_id in enumerate(("", "A", "B", "C"), start=1):
            self.host.repository.append_event(
                {
                    "event_id": f"ME-first-{index}",
                    "validation_session_id": first_id,
                    "stock_code": "005380",
                    "routine_instance_id": instance_id,
                    "event_type": "EXECUTION_PLAN_BLOCKED",
                    "timestamp": NOW.isoformat(),
                    "reason_code": f"BLOCK_{index}",
                    "payload": {},
                }
            )
        second_fingerprint = json.dumps(
            self.host.repository.read_events(second_id),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        completed = self.actions.unregister("005380")

        self.assertTrue(completed["ok"])
        self.assertEqual([], self.host.repository.read_events(first_id))
        self.assertEqual(
            second_fingerprint,
            json.dumps(
                self.host.repository.read_events(second_id),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        self.assertIsNotNone(self.host.current_session("032680"))

    def test_unregister_failure_never_deletes_events_from_current_registration(self):
        created = self.create()
        session_id = created["document"]["session"]["validation_session_id"]
        before_ids = {
            event["event_id"] for event in self.host.repository.read_events(session_id)
        }
        with patch.object(
            self.actions.sessions,
            "end_stock_session",
            side_effect=MockValidationError("MOCK_FORCED_END_FAILURE"),
        ):
            with self.assertRaisesRegex(MockValidationError, "MOCK_FORCED_END_FAILURE"):
                self.actions.unregister("005930")
        self.assertIsNotNone(self.host.current_session("005930"))
        after_ids = {
            event["event_id"] for event in self.host.repository.read_events(session_id)
        }
        self.assertTrue(before_ids.issubset(after_ids))

    def test_unregister_purge_failure_retains_journal_after_registration_removal(self):
        created = self.create()
        session_id = created["document"]["session"]["validation_session_id"]
        with patch.object(
            self.host.repository,
            "purge_ended_session_lifecycle",
            side_effect=MockValidationError("MOCK_FORCED_PURGE_FAILURE"),
        ):
            with self.assertRaisesRegex(MockValidationError, "MOCK_FORCED_PURGE_FAILURE"):
                self.actions.unregister("005930")
        self.assertIsNone(self.host.current_session("005930"))
        self.assertGreater(len(self.host.repository.read_events(session_id)), 0)

    def test_reregister_starts_new_lifecycle_without_restoring_old_events(self):
        first = self.create()
        first_id = first["document"]["session"]["validation_session_id"]
        self.actions.unregister("005930")
        second = self.create()
        second_id = second["document"]["session"]["validation_session_id"]

        self.assertNotEqual(first_id, second_id)
        self.assertEqual([], self.host.repository.read_events(first_id))
        self.assertEqual(
            ["SESSION_CREATED"],
            [event["event_type"] for event in self.host.repository.read_events(second_id)],
        )

    def test_per_stock_operation_journal_includes_all_instances_and_no_other_stock(self):
        first = self.actions.create_waiting_session(
            _reference("005380", ("A", "B"), stock_name="현대차")
        )
        second = self.actions.create_waiting_session(
            _reference("032680", ("A",), stock_name="소프트센")
        )
        first_id = first["document"]["session"]["validation_session_id"]
        second_id = second["document"]["session"]["validation_session_id"]
        for instance_id in ("A", "B"):
            self.host.repository.append_event(
                {
                    "event_id": f"ME-005380-{instance_id}",
                    "validation_session_id": first_id,
                    "stock_code": "005380",
                    "routine_instance_id": instance_id,
                    "event_type": "EXECUTION_PLAN_BLOCKED",
                    "timestamp": NOW.isoformat(),
                    "reason_code": "MOCK_MARKET_UNAVAILABLE",
                    "payload": {},
                }
            )
        first_rows = MockEventReaderAdapter(
            self.host.repository, first_id
        ).read_events()["events"]
        second_rows = MockEventReaderAdapter(
            self.host.repository, second_id
        ).read_events()["events"]

        self.assertEqual({"005380"}, {row["stock_code"] for row in first_rows})
        self.assertEqual({"032680"}, {row["stock_code"] for row in second_rows})
        self.assertEqual(
            {"A", "B"},
            {
                row["target_id"]
                for row in first_rows
                if row["target_type"] == "MOCK_ROUTINE_INSTANCE"
            },
        )
        self.assertTrue(
            all(
                "루틴 " in row["target_name"]
                for row in first_rows
                if row["target_type"] == "MOCK_ROUTINE_INSTANCE"
            )
        )

    def test_operation_journal_window_is_read_only_and_uses_exact_current_identity(self):
        created = self.actions.create_waiting_session(
            _reference("005380", ("A",), stock_name="현대차")
        )
        session_id = created["document"]["session"]["validation_session_id"]
        before_session = json.dumps(
            self.host.repository.read_session(session_id),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        before_events = json.dumps(
            self.host.repository.read_events(session_id),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        owner = QMainWindow()
        self.addCleanup(owner.close)
        owner.mock_validation_host = self.host
        owner.mock_validation_event_window = None

        MainWindow.open_mock_validation_event_window(
            owner,
            "005380",
            expected_validation_session_id=session_id,
        )
        self.app.processEvents()
        journal = owner.mock_validation_event_window
        self.assertIsNotNone(journal)
        self.assertEqual("운영일지 - 005380 현대차", journal.windowTitle())
        self.assertEqual(
            {"005380"},
            {
                row["stock_code"]
                for row in journal.reader.read_events()["events"]
            },
        )
        self.assertEqual(
            before_session,
            json.dumps(
                self.host.repository.read_session(session_id),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        self.assertEqual(
            before_events,
            json.dumps(
                self.host.repository.read_events(session_id),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        journal.close()

    def test_actual_parent_viewport_opens_exact_stock_operation_journal_window(self):
        created = self.actions.create_waiting_session(
            _reference("005380", ("A", "B"), stock_name="현대차")
        )
        session_id = created["document"]["session"]["validation_session_id"]
        owner = QMainWindow()
        self.addCleanup(owner.close)
        table = QTableWidget(0, len(main_table_loader.ROUTINE_MONITORING_HEADERS))
        table.setColumnWidth(0, 1800)
        table.setContextMenuPolicy(Qt.CustomContextMenu)
        owner.setCentralWidget(table)
        owner.routine_table = table
        owner.mock_validation_host = self.host
        owner.mock_validation_ui_actions = self.actions
        owner.mock_validation_event_window = None
        owner._mock_action_result = lambda _title, operation: operation()
        owner.open_mock_validation_event_window = MethodType(
            MainWindow.open_mock_validation_event_window,
            owner,
        )
        main_table_loader._load_mock_routine_table(owner)
        table.customContextMenuRequested.connect(
            lambda position: MainWindow.open_routine_context_menu(owner, position)
        )
        owner.show()
        self.app.processEvents()
        position = table.visualItemRect(table.item(0, 0)).center()

        _Menu.chosen_text = "운영일지"
        _Menu.chosen_menu_title = None
        with patch.object(mock_context_menu, "QMenu", _Menu):
            table.customContextMenuRequested.emit(position)
        self.app.processEvents()

        journal = owner.mock_validation_event_window
        self.assertIsNotNone(journal)
        self.assertEqual("운영일지 - 005380 현대차", journal.windowTitle())
        self.assertEqual(session_id, journal.reader.session_id)
        self.assertEqual(
            {"005380"},
            {
                row["stock_code"]
                for row in journal.reader.read_events()["events"]
            },
        )
        journal.close()

    def test_actual_parent_viewport_unregister_purges_target_and_preserves_other_stock(self):
        first = self.actions.create_waiting_session(
            _reference("005380", ("A", "B"), stock_name="현대차")
        )
        second = self.actions.create_waiting_session(
            _reference("032680", ("A",), stock_name="소프트센")
        )
        first_id = first["document"]["session"]["validation_session_id"]
        second_id = second["document"]["session"]["validation_session_id"]
        second_before = deepcopy(self.host.repository.read_events(second_id))
        window, results = self._mock_context_window()
        table = window.routine_table
        parent_row = next(
            row
            for row in range(table.rowCount())
            if table.item(row, 0).data(main_table_loader.ROUTINE_ROW_KIND_ROLE)
            == main_table_loader.ROUTINE_ROW_MOCK_STOCK
            and table.item(row, 0).data(main_table_loader.ROUTINE_STOCK_CODE_ROLE)
            == "005380"
        )
        position = table.visualItemRect(table.item(parent_row, 0)).center()

        _Menu.chosen_text = "등록해제"
        _Menu.chosen_menu_title = None
        with patch.object(mock_context_menu, "QMenu", _Menu):
            table.customContextMenuRequested.emit(position)

        self.assertTrue(results[-1]["ok"])
        self.assertIsNone(self.host.current_session("005380"))
        self.assertEqual([], self.host.repository.read_events(first_id))
        self.assertEqual(second_before, self.host.repository.read_events(second_id))

    def test_event_reader_uses_mock_journal_only(self):
        created = self.create()
        session_id = created["document"]["session"]["validation_session_id"]
        result = MockEventReaderAdapter(self.host.repository, session_id).read_events()
        self.assertEqual(1, result["count"])
        self.assertEqual("MOCK", result["events"][0]["category"])
        self.assertEqual("SESSION_CREATED", result["events"][0]["event_type"])

    def test_host_market_unavailable_254_same_second_cycles_coalesce_without_false_error(self):
        created = self.actions.create_waiting_session(
            _reference("005380", ("A",), stock_name="현대차")
        )
        session_id = created["document"]["session"]["validation_session_id"]
        self.actions.set_instance_effective_settings(
            "005380", "A", operation_mode="CONTINUOUS"
        )
        self.host.start_instance_operation("005380", "A", as_of=NOW)

        for microsecond in range(254):
            self.host.process_due_cycles(as_of=NOW.replace(microsecond=microsecond))

        document = self.host.current_session("005380")
        self.assertEqual("RUNNING", document["instance_execution"]["A"]["state"])
        self.assertEqual("", document["instance_execution"]["A"]["error_code"])
        blocked = [
            event
            for event in self.host.repository.read_events(session_id)
            if event["event_type"] == "EXECUTION_PLAN_BLOCKED"
        ]
        self.assertEqual(1, len(blocked))
        self.assertEqual("MOCK_MARKET_UNAVAILABLE", blocked[0]["reason_code"])
        rows = MockEventReaderAdapter(self.host.repository, session_id).read_events()["events"]
        row = next(item for item in rows if item["event_type"] == "EXECUTION_PLAN_BLOCKED")
        self.assertEqual("005380 현대차 / 루틴 A", row["target_name"])
        self.assertEqual("MOCK_ROUTINE_INSTANCE", row["target_type"])
        self.assertEqual("A", row["target_id"])
        self.assertEqual("INFO", row["severity"])
        self.assertEqual("EXECUTION_PLAN_BLOCKED", row["summary"])
        self.assertNotEqual(row["reason_code"], row["summary"])
        self.assertEqual(
            1,
            EventRecordPrototypeWindow._summary_text(row).count(
                "MOCK_MARKET_UNAVAILABLE"
            ),
        )
        tree = current_mock_monitoring_trees(self.host.repository)[0]["children"][0]
        self.assertFalse(tree["error"])
        self.assertNotEqual("red", tree["status_led"])

    def test_instance_error_event_projects_actual_transition_and_operation_identity(self):
        created = self.actions.create_waiting_session(
            _reference("005380", ("A",), stock_name="현대차")
        )
        session_id = created["document"]["session"]["validation_session_id"]
        self.host.start_instance_operation("005380", "A", as_of=NOW)
        operation_id = self.host.current_session("005380")["mock_operation_lifecycle"][
            "instance_operations"
        ]["A"]["operation_session_id"]

        self.host.session_service.stop_for_instance_error(
            session_id,
            source_routine_instance_id="A",
            reason_code="MOCK_GENUINE_INTEGRITY_FAILURE",
            reason="fixture integrity failure",
            command_id="MC-genuine-error-evidence",
            source_category="MOCK_HOST_TEST",
        )

        rows = MockEventReaderAdapter(self.host.repository, session_id).read_events()["events"]
        row = next(item for item in rows if item["event_type"] == "INSTANCE_ERROR")
        self.assertEqual("ERROR", row["severity"])
        self.assertEqual("005380 현대차 / 루틴 A", row["target_name"])
        self.assertEqual("INSTANCE_ERROR", row["summary"])
        self.assertEqual("RUNNING", row["details"]["before_state"])
        self.assertEqual("ERROR", row["details"]["after_state"])
        self.assertEqual(operation_id, row["details"]["operation_identity"])
        self.assertEqual("MOCK_HOST_TEST", row["details"]["source_category"])
        self.assertLess(
            row["details"]["before_revision"],
            row["details"]["after_revision"],
        )

    def test_same_instance_new_operation_starts_a_distinct_normal_block_episode(self):
        created = self.actions.create_waiting_session(
            _reference("005380", ("A",), stock_name="현대차")
        )
        session_id = created["document"]["session"]["validation_session_id"]
        self.actions.set_instance_effective_settings(
            "005380", "A", operation_mode="CONTINUOUS"
        )
        self.host.start_instance_operation("005380", "A", as_of=NOW)
        first_operation = self.host.current_session("005380")["mock_operation_lifecycle"][
            "instance_operations"
        ]["A"]["operation_session_id"]
        self.host.process_due_cycles(as_of=NOW)

        self.actions.validation_stop_instance("005380", "A")
        self.assertEqual(
            "VALIDATION_STOPPED",
            self.host.current_session("005380")["instance_execution"]["A"]["state"],
        )
        self.actions.reset_instance("005380", "A")
        self.host.start_instance_operation(
            "005380", "A", as_of=NOW + timedelta(seconds=3)
        )
        second_operation = self.host.current_session("005380")["mock_operation_lifecycle"][
            "instance_operations"
        ]["A"]["operation_session_id"]
        self.assertNotEqual(first_operation, second_operation)
        self.host.process_due_cycles(as_of=NOW + timedelta(seconds=3))

        blocked = [
            event
            for event in self.host.repository.read_events(session_id)
            if event["event_type"] == "EXECUTION_PLAN_BLOCKED"
        ]
        self.assertEqual(2, len(blocked))
        self.assertEqual(
            {first_operation, second_operation},
            {event["payload"]["operation_identity"] for event in blocked},
        )

    def test_current_mock_context_reuses_general_shell_without_mock_submenu(self):
        called = Mock()
        production_early = Mock()
        production_liquidation = Mock()
        callbacks = StockContextMenuCallbacks(
            select_all=Mock(),
            clear_selection=Mock(),
            early_close=production_early,
            early_close_profit_loss=Mock(),
            early_close_cancel=Mock(),
            individual_liquidation=production_liquidation,
            mock_create=Mock(),
            open_charts=Mock(),
            mock_actions=lambda: {
                "current": True,
                "can_start": True,
                "can_early_close": False,
                "can_immediate": False,
                "can_reset": False,
                "can_unregister": True,
                "start": called,
                "unregister": Mock(),
            },
        )
        _Menu.chosen_text = "운영시작"
        with patch.object(common_menu, "QMenu", _Menu):
            common_menu.show_monitor_stock_context_menu(
                object(),
                object(),
                has_selection=True,
                callbacks=callbacks,
            )
        self.assertEqual(
            ["조기마감", "개별청산"],
            [menu.title for menu in _Menu.root.submenus],
        )
        root_texts = [action.text() for action in _Menu.root.actions]
        for expected in (
            "운영시작",
            "전체선택",
            "선택해제",
            "종목등록",
            "등록해제",
            "간이차트",
            "종목리셋",
        ):
            self.assertIn(expected, root_texts)
        self.assertNotIn("모의검증", [menu.title for menu in _Menu.root.submenus])
        self.assertFalse(any("모의세금" in text for text in root_texts))
        self.assertFalse(any("모의 이벤트" in text for text in root_texts))
        self.assertFalse(any("모의검토관리" in text for text in root_texts))
        called.assert_called_once_with()
        production_early.assert_not_called()
        production_liquidation.assert_not_called()

    def test_mock_membership_overlay_preserves_normal_production_projection(self):
        stock = {
            "stock_dir": str(self.project_root / "stocks" / "005930_삼성전자"),
            "stock_path": "stocks/005930_삼성전자",
            "instance_id": "A",
            "operation_excluded": False,
            "code": "005930",
            "name": "삼성전자",
        }
        window = SimpleNamespace(
            mock_validation_host=SimpleNamespace(
                current_stock_codes=lambda: frozenset({"005930"})
            )
        )
        inspection = SimpleNamespace(state={"status": "STOPPED"}, review_required=False)
        with patch.object(main_table_loader, "inspect_review_state_data", return_value=inspection):
            counts = main_table_loader._instance_stock_counts(
                window=window,
                static_data={"stocks": (stock,)},
                state_by_stock_dir={stock["stock_dir"]: inspection.state},
            )
        self.assertEqual(1, counts["A"]["registered"])
        self.assertEqual(1, counts["A"]["waiting"])
        self.assertEqual("A", stock["instance_id"])
        self.assertFalse(stock["operation_excluded"])

    def test_mock_entry_is_independent_from_production_active_or_review_state(self):
        target = SimpleNamespace(
            stock_dir=self.project_root / "stocks" / "005930_삼성전자",
            code="005930",
        )
        target.stock_dir.mkdir(parents=True)
        target.stock_dir.joinpath("state.json").write_text("{}", encoding="utf-8")
        with patch.object(
            main_context_menu,
            "read_json_dict",
            side_effect=AssertionError("Production state must not be read"),
        ) as production_read:
            self.assertTrue(main_context_menu._mock_entry_allowed(object(), target)[0])
        production_read.assert_not_called()

    def test_production_row_is_not_hijacked_by_mock_scope_or_membership(self):
        target = SimpleNamespace(
            stock_dir=self.project_root / "stocks" / "005930_삼성전자",
            code="005930",
            name="삼성전자",
            routine_instance_id="A",
        )
        item = SimpleNamespace(row=lambda: 0)
        table = SimpleNamespace(
            itemAt=lambda _position: item,
            viewport=lambda: SimpleNamespace(mapToGlobal=lambda position: position),
        )
        window = SimpleNamespace(
            routine_table=table,
            _main_routine_stock_scope="mock",
        )
        adapter = Mock()
        adapter.selected_operation_mode_set.return_value = {"SCHEDULED"}
        adapter.selected_stocks_are_operation_excluded.return_value = False
        with (
            patch.object(
                main_context_menu,
                "_stock_target_for_row",
                return_value=target,
            ),
            patch.object(
                main_context_menu,
                "ensure_main_monitoring_context_stock_selected",
            ),
            patch.object(
                main_context_menu,
                "selected_main_monitoring_stock_targets",
                return_value=[target],
            ),
            patch.object(
                main_context_menu,
                "MainMonitoringStockOperationAdapter",
                return_value=adapter,
            ),
            patch.object(
                main_context_menu,
                "selected_emergency_context_state",
                return_value=(False, True),
            ) as production_review,
            patch.object(
                main_context_menu,
                "show_monitor_stock_context_menu",
            ) as show_menu,
        ):
            opened = main_context_menu.show_main_monitoring_stock_context_menu(
                window,
                object(),
            )

        self.assertTrue(opened)
        adapter.selected_operation_mode_set.assert_called_once_with()
        adapter.selected_stocks_are_operation_excluded.assert_called_once_with()
        production_review.assert_called_once()
        self.assertEqual({"SCHEDULED"}, show_menu.call_args.kwargs["selected_modes"])
        self.assertFalse(show_menu.call_args.kwargs["operation_excluded"])
        callbacks = show_menu.call_args.kwargs["callbacks"]
        self.assertIsNone(callbacks.mock_actions)
        self.assertTrue(callable(callbacks.mock_create))

    def test_mock_scope_disables_production_bottom_buttons_and_restores_via_owners(self):
        host = SimpleNamespace(
            _main_routine_stock_scope="mock",
            btn_start=QPushButton(),
            btn_main_visible_early_close=QPushButton(),
            btn_emergency_stop=QPushButton(),
            btn_log_view=QPushButton("이벤트"),
            btn_review_manage=QPushButton("검토관리"),
            update_global_operation_button_state=Mock(),
            update_emergency_button_state=Mock(),
            _visible_monitoring_early_close_targets=lambda: [object()],
        )
        for button in (
            host.btn_start,
            host.btn_main_visible_early_close,
            host.btn_emergency_stop,
        ):
            button.setEnabled(True)
            button.setToolTip("original")
        host.btn_review_manage.setVisible(True)
        MainWindow._apply_mock_scope_button_isolation(host)
        self.assertEqual("이벤트", host.btn_log_view.text())
        self.assertEqual("검토관리", host.btn_review_manage.text())
        self.assertFalse(host.btn_review_manage.isVisible())
        self.assertFalse(host.btn_review_manage.isEnabled())
        self.assertTrue(all(not button.isEnabled() for button in (
            host.btn_start,
            host.btn_main_visible_early_close,
            host.btn_emergency_stop,
        )))
        self.assertEqual(
            {"모의 종목은 우클릭 메뉴에서 조작합니다."},
            {button.toolTip() for button in (
                host.btn_start,
                host.btn_main_visible_early_close,
                host.btn_emergency_stop,
            )},
        )
        host._main_routine_stock_scope = "all"
        MainWindow._apply_mock_scope_button_isolation(host)
        self.assertEqual("이벤트", host.btn_log_view.text())
        self.assertEqual("검토관리", host.btn_review_manage.text())
        self.assertTrue(host.btn_review_manage.isVisible())
        self.assertTrue(host.btn_review_manage.isEnabled())
        host.update_global_operation_button_state.assert_called_once_with()
        host.update_emergency_button_state.assert_called_once_with()
        self.assertTrue(host.btn_main_visible_early_close.isEnabled())
        self.assertEqual("original", host.btn_start.toolTip())

    def test_bottom_event_button_remains_production_only_in_every_scope(self):
        host = SimpleNamespace(
            _main_routine_stock_scope="all",
            open_event_record_window=Mock(),
            open_review_required_window=Mock(),
            open_mock_validation_event_window=Mock(),
            open_mock_validation_review_window=Mock(),
        )

        host.open_event_record_window()
        MainWindow.open_current_domain_review_window(host)
        host.open_event_record_window.assert_called_once_with()
        host.open_review_required_window.assert_called_once_with()
        host.open_mock_validation_event_window.assert_not_called()
        host.open_mock_validation_review_window.assert_not_called()

        host._main_routine_stock_scope = "mock"
        host.open_event_record_window()
        MainWindow.open_current_domain_review_window(host)
        self.assertEqual(2, host.open_event_record_window.call_count)
        host.open_mock_validation_event_window.assert_not_called()
        host.open_mock_validation_review_window.assert_not_called()
        host.open_review_required_window.assert_called_once_with()
        connection_source = inspect.getsource(MainWindow._connect_events)
        self.assertIn(
            "self.btn_log_view.clicked.connect(self.open_event_record_window)",
            connection_source,
        )
        self.assertNotIn("open_current_domain_event_window", connection_source)

    def test_context_and_review_reset_entry_points_share_one_mock_backend(self):
        reset = Mock(return_value={"reset": True})
        host = SimpleNamespace(
            mock_validation_ui_actions=SimpleNamespace(reset=reset),
            _mock_action_result=lambda _title, action: action(),
        )
        target = SimpleNamespace(code="005930")
        context = main_context_menu._main_mock_context(
            SimpleNamespace(
                mock_validation_host=object(),
                mock_validation_context_state=lambda _code: {
                    "current": True,
                    "can_start": False,
                    "can_early_close": False,
                    "can_immediate": False,
                    "can_reset": True,
                    "can_unregister": False,
                },
                start_mock_validation_stock=Mock(),
                early_close_mock_validation_stock=Mock(),
                immediate_liquidate_mock_validation_stock=Mock(),
                _reset_mock_validation_from_review=lambda code: (
                    MainWindow._reset_mock_validation_from_review(host, code)
                ),
            ),
            target,
        )
        with patch.object(main_context_menu, "_main_mock_unregister"):
            with patch("gui_windows.QMessageBox.question", return_value=gui_windows.QMessageBox.Yes):
                context["reset"]()
                MainWindow._reset_mock_validation_from_review(host, "005930")
        self.assertEqual([call("005930"), call("005930")], reset.call_args_list)


if __name__ == "__main__":
    unittest.main()
