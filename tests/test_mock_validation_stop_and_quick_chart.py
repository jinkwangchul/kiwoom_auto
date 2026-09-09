# -*- coding: utf-8 -*-

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import gc
import tempfile
import unittest
from unittest.mock import Mock, patch

from PyQt5.QtCore import QCoreApplication, QEvent, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog, QMainWindow, QTableWidget

import gui_main_table_loader as main_table_loader
import gui_windows
import mock_validation_context_menu as context_menu
import mock_validation_quick_chart as quick_chart
from mock_validation_contract import MockValidationError, payload_hash
from mock_validation_host import MockValidationHost
from mock_validation_repository import MockValidationRepository
from mock_validation_ui_actions import MockValidationUIActions
from mock_validation_ui_projection import current_mock_monitoring_trees
from tests.test_mock_validation_host_ui import _Api, _Menu, _reference
from tests.test_mock_validation_market_data import _book


SEOUL = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 7, 12, 0, 0, tzinfo=SEOUL)


class MockValidationStopAndQuickChartTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        quick_chart._OPEN_MOCK_INSTANCE_CHARTS.clear()
        self.addCleanup(quick_chart._OPEN_MOCK_INSTANCE_CHARTS.clear)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project_root = Path(self.temporary.name) / "project"
        self.project_root.mkdir()
        self.clock = {"now": NOW}
        self.host = MockValidationHost(
            _Api(),
            project_root=self.project_root,
            now_factory=lambda: self.clock["now"],
            operation_policy_provider=lambda: {
                "regular_market": {"end_time": "15:30:00"},
                "liquidation": {"minutes_before_regular_close": 5, "method": "시장가"},
            },
            candles_provider=lambda **_kwargs: {
                "available": False,
                "candles": [],
                "availability_state": "SOURCE_UNAVAILABLE",
                "available_minute_candles": 0,
            },
        )
        self.addCleanup(self.host.dispose)
        self.actions = MockValidationUIActions(self.host)
        self.created = self.actions.create_waiting_session(_reference())
        self.session_id = self.created["document"]["session"]["validation_session_id"]

    def _start(self, instance_id: str) -> None:
        self.actions.set_instance_effective_settings(
            "005930", instance_id, operation_mode="CONTINUOUS"
        )
        self.host.start_instance_operation("005930", instance_id, as_of=self.clock["now"])

    def _open_order(self, instance_id: str, order_id: str, side: str, qty: int) -> None:
        self.host.session_service.create_order(
            self.session_id,
            routine_instance_id=instance_id,
            side=side,
            order_type="LIMIT",
            requested_qty=qty,
            requested_price=100,
            mock_order_id=order_id,
            command_id=f"MC-create-{order_id}",
        )
        self.host.session_service.transition_order(
            self.session_id,
            order_id,
            "OPEN",
            command_id=f"MC-open-{order_id}",
        )

    def _table_window(self):
        window = QMainWindow()
        self.addCleanup(window.close)
        table = QTableWidget(
            0,
            len(main_table_loader.ROUTINE_MONITORING_HEADERS),
            window,
        )
        table.setColumnWidth(0, 1900)
        table.resize(2100, 320)
        window.setCentralWidget(table)
        window.routine_table = table
        window.mock_validation_host = self.host
        window.mock_validation_ui_actions = self.actions
        window._mock_action_result = lambda _title, operation: operation()
        window.mock_routine_instance_ats_state = lambda _row: {
            "extra1": False,
            "extra2": False,
            "extra3": False,
        }
        window.set_mock_routine_instance_ats_flag = Mock()
        main_table_loader._load_mock_routine_table(window)
        controller = gui_windows._RoutineTreeInteractionController(window)
        table.viewport().installEventFilter(controller)
        window._routine_tree_interaction_controller = controller
        table.show()
        window.show()
        self.app.processEvents()
        return window, table, controller

    @staticmethod
    def _row(table, instance_id: str) -> int:
        return next(
            row
            for row in range(table.rowCount())
            if table.item(row, 0).data(main_table_loader.ROUTINE_INSTANCE_ID_ROLE)
            == instance_id
        )

    def test_validation_stop_cancels_all_target_orders_and_preserves_ledgers_and_siblings(self):
        self._start("A")
        self._start("B")
        self._start("C")
        self.host.session_service.set_instance_position(
            self.session_id,
            "B",
            holding_qty=10,
            available_qty=9,
            average_price=123,
            realized_cost_basis=1230,
            command_id="MC-position-B",
        )
        self.host.session_service.set_instance_pnl(
            self.session_id,
            "B",
            realized_pnl=7,
            unrealized_pnl=11,
            gross_pnl=18,
            commission=1,
            mock_tax=2,
            net_pnl=15,
            command_id="MC-pnl-B",
        )
        self._open_order("B", "MO-BUY", "BUY", 2)
        self._open_order("B", "MO-SELL", "SELL", 1)
        self._open_order("A", "MO-A", "BUY", 3)
        before = self.host.current_session("005930")
        sibling_before = payload_hash(
            {
                "execution": before["instance_execution"]["A"],
                "orders": [item for item in before["orders"] if item["routine_instance_id"] == "A"],
                "position": next(item for item in before["positions"] if item["routine_instance_id"] == "A"),
                "pnl": next(item for item in before["pnl"] if item["routine_instance_id"] == "A"),
            }
        )
        position_before = deepcopy(next(item for item in before["positions"] if item["routine_instance_id"] == "B"))
        pnl_before = deepcopy(next(item for item in before["pnl"] if item["routine_instance_id"] == "B"))

        result = self.host.stop_instance_validation(
            "005930", "B", command_id="MC-validation-stop-B"
        )

        self.assertEqual(2, result["cancelled_order_count"])
        after = result["document"]
        self.assertEqual("VALIDATION_STOPPED", after["instance_execution"]["B"]["state"])
        self.assertFalse(after["instance_execution"]["B"]["progression_allowed"])
        target_orders = [item for item in after["orders"] if item["routine_instance_id"] == "B"]
        self.assertEqual({"CANCELED"}, {item["state"] for item in target_orders})
        self.assertEqual(0, sum(item["remaining_qty"] for item in target_orders if item["state"] != "CANCELED"))
        position_after = next(item for item in after["positions"] if item["routine_instance_id"] == "B")
        pnl_after = next(item for item in after["pnl"] if item["routine_instance_id"] == "B")
        self.assertEqual(position_before["holding_qty"], position_after["holding_qty"])
        self.assertEqual(position_before["average_price"], position_after["average_price"])
        self.assertEqual(position_before["realized_cost_basis"], position_after["realized_cost_basis"])
        self.assertEqual(position_before["available_qty"], position_after["available_qty"])
        self.assertEqual(pnl_before, pnl_after)
        sibling_after = payload_hash(
            {
                "execution": after["instance_execution"]["A"],
                "orders": [item for item in after["orders"] if item["routine_instance_id"] == "A"],
                "position": next(item for item in after["positions"] if item["routine_instance_id"] == "A"),
                "pnl": next(item for item in after["pnl"] if item["routine_instance_id"] == "A"),
            }
        )
        self.assertEqual(sibling_before, sibling_after)
        events = self.host.repository.read_events(self.session_id)
        types = [item["event_type"] for item in events]
        self.assertEqual(1, types.count("VALIDATION_STOP_REQUESTED"))
        self.assertEqual(1, types.count("VALIDATION_STOP_COMPLETED"))
        completed = next(item for item in events if item["event_type"] == "VALIDATION_STOP_COMPLETED")
        self.assertEqual(2, completed["payload"]["cancelled_order_count"])
        self.assertEqual(10, completed["payload"]["remaining_holding_qty"])
        self.assertEqual("B", completed["routine_instance_id"])
        child = next(
            item
            for item in current_mock_monitoring_trees(self.host.repository)[0]["children"]
            if item["routine_instance_id"] == "B"
        )
        self.assertNotIn("state_label", child)
        self.assertEqual("감시/대기", child["display_status"])
        self.assertFalse(child["error"])
        self.assertNotEqual("red", child["status_led"])

        duplicate = self.host.stop_instance_validation(
            "005930", "B", command_id="MC-validation-stop-B"
        )
        self.assertTrue(duplicate["duplicate"])
        repeated = self.host.repository.read_events(self.session_id)
        self.assertEqual(len(events), len(repeated))

    def test_partial_fill_is_preserved_and_only_remaining_quantity_is_cancelled(self):
        self._start("B")
        self._open_order("B", "MO-PARTIAL", "BUY", 10)
        self.host.session_service.append_fill(
            self.session_id,
            mock_order_id="MO-PARTIAL",
            qty=4,
            price=100,
            market_snapshot_identity="MMK-fixture",
            command_id="MC-partial-fill",
        )
        self.host.session_service.set_instance_position(
            self.session_id,
            "B",
            holding_qty=4,
            available_qty=4,
            average_price=100,
            realized_cost_basis=400,
            command_id="MC-partial-position",
        )

        result = self.host.stop_instance_validation(
            "005930", "B", command_id="MC-stop-partial"
        )

        order = next(item for item in result["document"]["orders"] if item["mock_order_id"] == "MO-PARTIAL")
        self.assertEqual((4, 6, "CANCELED"), (order["filled_qty"], order["remaining_qty"], order["state"]))
        self.assertEqual(1, len(result["document"]["fills"]))
        position = next(item for item in result["document"]["positions"] if item["routine_instance_id"] == "B")
        self.assertEqual((4, 4, 100), (position["holding_qty"], position["available_qty"], position["average_price"]))

    def test_validation_stopped_instance_restarts_without_reset_and_preserves_data(self):
        self._start("B")
        self.host.session_service.set_instance_position(
            self.session_id,
            "B",
            holding_qty=3,
            available_qty=3,
            average_price=100,
            realized_cost_basis=300,
            command_id="MC-restart-position-B",
        )
        self._open_order("B", "MO-restart-B", "BUY", 2)
        stopped = self.host.stop_instance_validation(
            "005930", "B", command_id="MC-stop-before-restart-B"
        )["document"]
        first_operation_id = stopped["mock_operation_lifecycle"]["instance_operations"][
            "B"
        ]["operation_session_id"]
        preserved = payload_hash(
            {
                "orders": stopped["orders"],
                "fills": stopped["fills"],
                "positions": stopped["positions"],
                "pnl": stopped["pnl"],
                "cycle": stopped["cycle_state_by_instance"]["B"],
                "progression": stopped["progression_by_instance"]["B"],
            }
        )
        stopped_context = self.host.instance_context_state("005930", "B")
        self.assertTrue(stopped_context["can_start"])
        self.assertFalse(stopped_context["can_validation_stop"])
        self.assertTrue(stopped_context["can_reset"])

        self.clock["now"] += timedelta(seconds=1)
        restarted = self.host.start_instance_operation(
            "005930", "B", as_of=self.clock["now"]
        )["document"]

        self.assertEqual("RUNNING", restarted["instance_execution"]["B"]["state"])
        self.assertTrue(restarted["instance_execution"]["B"]["progression_allowed"])
        self.assertNotEqual(
            first_operation_id,
            restarted["mock_operation_lifecycle"]["instance_operations"]["B"][
                "operation_session_id"
            ],
        )
        self.assertEqual(
            preserved,
            payload_hash(
                {
                    "orders": restarted["orders"],
                    "fills": restarted["fills"],
                    "positions": restarted["positions"],
                    "pnl": restarted["pnl"],
                    "cycle": restarted["cycle_state_by_instance"]["B"],
                    "progression": restarted["progression_by_instance"]["B"],
                }
            ),
        )
        running_context = self.host.instance_context_state("005930", "B")
        self.assertFalse(running_context["can_start"])
        self.assertTrue(running_context["can_validation_stop"])
        self.assertFalse(running_context["can_reset"])
        with self.assertRaisesRegex(
            MockValidationError, "MOCK_INSTANCE_OPERATION_ACTIVE"
        ):
            self.actions.reset_instance("005930", "B")

    def test_validation_stop_accepts_error_only_to_cancel_live_mock_orders(self):
        self._start("B")
        self._open_order("B", "MO-ERROR-LIVE", "BUY", 5)
        self.host.session_service.stop_for_instance_error(
            self.session_id,
            source_routine_instance_id="B",
            reason_code="FIXTURE_ERROR",
            reason="fixture",
            command_id="MC-error-before-validation-stop",
        )

        result = self.host.stop_instance_validation(
            "005930", "B", command_id="MC-stop-error-instance"
        )

        execution = result["document"]["instance_execution"]["B"]
        order = next(item for item in result["document"]["orders"] if item["mock_order_id"] == "MO-ERROR-LIVE")
        self.assertEqual("VALIDATION_STOPPED", execution["state"])
        self.assertFalse(execution["progression_allowed"])
        self.assertEqual("FIXTURE_ERROR", execution["error_code"])
        self.assertEqual("CANCELED", order["state"])

    def test_error_recovery_reset_cleans_running_operation_and_preserves_sibling(self):
        self._start("A")
        self._start("B")
        self._open_order("A", "MO-ERROR-A", "BUY", 5)
        self.host.session_service.append_fill(
            self.session_id,
            mock_order_id="MO-ERROR-A",
            qty=2,
            price=100,
            market_snapshot_identity="MMK-error-reset",
            command_id="MC-error-reset-fill-A",
        )
        self.host.session_service.set_instance_position(
            self.session_id,
            "A",
            holding_qty=2,
            available_qty=2,
            average_price=100,
            realized_cost_basis=200,
            command_id="MC-error-reset-position-A",
        )
        self.host.session_service.set_instance_pnl(
            self.session_id,
            "A",
            realized_pnl=7,
            unrealized_pnl=11,
            gross_pnl=18,
            commission=1,
            mock_tax=2,
            net_pnl=15,
            command_id="MC-error-reset-pnl-A",
        )
        self._open_order("B", "MO-SIBLING-B", "BUY", 3)
        before_error = self.host.current_session("005930")
        first_operation_id = before_error["mock_operation_lifecycle"][
            "instance_operations"
        ]["A"]["operation_session_id"]
        session_id = before_error["session"]["validation_session_id"]
        reference_hash = payload_hash(before_error["reference_snapshot"])
        effective_hash = payload_hash(before_error["effective_settings_by_instance"]["A"])
        sibling_hash = payload_hash(
            {
                "execution": before_error["instance_execution"]["B"],
                "orders": [
                    item
                    for item in before_error["orders"]
                    if item["routine_instance_id"] == "B"
                ],
                "fills": [
                    item
                    for item in before_error["fills"]
                    if item["routine_instance_id"] == "B"
                ],
                "position": next(
                    item
                    for item in before_error["positions"]
                    if item["routine_instance_id"] == "B"
                ),
                "pnl": next(
                    item
                    for item in before_error["pnl"]
                    if item["routine_instance_id"] == "B"
                ),
                "operation": before_error["mock_operation_lifecycle"][
                    "instance_operations"
                ]["B"],
            }
        )

        self.host.session_service.stop_for_instance_error(
            self.session_id,
            source_routine_instance_id="A",
            reason_code="MOCK_ROUTINE_ADAPTER_INTEGRITY_FAILURE",
            reason="MOCK_EVENT_ID_CONFLICT",
            command_id="MC-error-reset-isolate-A",
        )
        error_document = self.host.current_session("005930")
        self.assertEqual("ERROR", error_document["instance_execution"]["A"]["state"])
        self.assertFalse(
            error_document["instance_execution"]["A"]["progression_allowed"]
        )
        self.assertEqual(
            "RUNNING",
            error_document["mock_operation_lifecycle"]["instance_operations"]["A"][
                "state"
            ],
        )
        restarted_error_repository = MockValidationRepository(
            self.project_root / "mock_validation", project_root=self.project_root
        )
        durable_error = restarted_error_repository.read_session(self.session_id)
        self.assertEqual("ERROR", durable_error["instance_execution"]["A"]["state"])
        self.assertFalse(
            durable_error["instance_execution"]["A"]["progression_allowed"]
        )
        self.assertEqual(
            "RUNNING",
            durable_error["mock_operation_lifecycle"]["instance_operations"]["A"][
                "state"
            ],
        )
        error_context = self.host.instance_context_state("005930", "A")
        self.assertTrue(error_context["can_reset"])
        self.assertTrue(error_context["can_validation_stop"])
        error_child = next(
            item
            for item in current_mock_monitoring_trees(self.host.repository)[0]["children"]
            if item["routine_instance_id"] == "A"
        )
        self.assertEqual("검토종목", error_child["display_status"])

        window, table, _controller = self._table_window()
        row = self._row(table, "A")
        position = table.visualItemRect(table.item(row, 0)).center()
        _Menu.chosen_text = ""
        with patch.object(context_menu, "QMenu", _Menu):
            context_menu.show_mock_monitoring_context_menu(
                window,
                position,
                expected_row_kind=main_table_loader.ROUTINE_ROW_MOCK_INSTANCE,
            )
        self.assertTrue(
            next(
                action
                for action in _Menu.root.actions
                if action.text() == "검증리셋"
            ).isEnabled()
        )

        events_before = self.host.repository.read_events(self.session_id)
        event_ids_before = {item["event_id"] for item in events_before}
        reset = self.actions.reset_instance("005930", "A")["document"]

        execution = reset["instance_execution"]["A"]
        self.assertEqual("WAITING", execution["state"])
        self.assertFalse(execution["progression_allowed"])
        self.assertEqual("", execution["operation_session_id"])
        self.assertEqual("", execution["error_code"])
        self.assertEqual("", execution["error_reason"])
        self.assertEqual("", execution["error_occurred_at"])
        self.assertEqual({}, reset["cycle_state_by_instance"]["A"])
        self.assertEqual({}, reset["progression_by_instance"]["A"])
        self.assertFalse(
            any(item["routine_instance_id"] == "A" for item in reset["orders"])
        )
        self.assertFalse(
            any(item["routine_instance_id"] == "A" for item in reset["fills"])
        )
        position_a = next(
            item for item in reset["positions"] if item["routine_instance_id"] == "A"
        )
        pnl_a = next(
            item for item in reset["pnl"] if item["routine_instance_id"] == "A"
        )
        self.assertEqual((0, 0, 0, 0), (
            position_a["holding_qty"],
            position_a["available_qty"],
            position_a["average_price"],
            position_a["realized_cost_basis"],
        ))
        self.assertEqual(
            {0},
            {
                pnl_a[key]
                for key in (
                    "realized_pnl",
                    "unrealized_pnl",
                    "gross_pnl",
                    "commission",
                    "mock_tax",
                    "net_pnl",
                )
            },
        )
        self.assertNotIn(
            "A", reset["mock_operation_lifecycle"]["instance_operations"]
        )
        self.assertEqual(session_id, reset["session"]["validation_session_id"])
        self.assertEqual(reference_hash, payload_hash(reset["reference_snapshot"]))
        self.assertEqual(
            effective_hash, payload_hash(reset["effective_settings_by_instance"]["A"])
        )
        self.assertEqual(
            sibling_hash,
            payload_hash(
                {
                    "execution": reset["instance_execution"]["B"],
                    "orders": [
                        item
                        for item in reset["orders"]
                        if item["routine_instance_id"] == "B"
                    ],
                    "fills": [
                        item
                        for item in reset["fills"]
                        if item["routine_instance_id"] == "B"
                    ],
                    "position": next(
                        item
                        for item in reset["positions"]
                        if item["routine_instance_id"] == "B"
                    ),
                    "pnl": next(
                        item
                        for item in reset["pnl"]
                        if item["routine_instance_id"] == "B"
                    ),
                    "operation": reset["mock_operation_lifecycle"][
                        "instance_operations"
                    ]["B"],
                }
            ),
        )
        events_after = self.host.repository.read_events(self.session_id)
        self.assertTrue(event_ids_before.issubset({item["event_id"] for item in events_after}))
        self.assertEqual(
            1,
            sum(item["event_type"] == "INSTANCE_RESET" for item in events_after)
            - sum(item["event_type"] == "INSTANCE_RESET" for item in events_before),
        )
        self.assertIn(
            ("VIRTUAL_ORDER_CANCELED", "ERROR_RESET"),
            {
                (item["event_type"], item.get("payload", {}).get("source"))
                for item in events_after
            },
        )
        reset_context = self.host.instance_context_state("005930", "A")
        self.assertEqual("WAITING", reset_context["state"])
        self.assertTrue(reset_context["can_start"])
        reset_child = next(
            item
            for item in current_mock_monitoring_trees(self.host.repository)[0]["children"]
            if item["routine_instance_id"] == "A"
        )
        self.assertNotEqual("검토종목", reset_child["display_status"])

        restarted_repository = MockValidationRepository(
            self.project_root / "mock_validation", project_root=self.project_root
        )
        restored = restarted_repository.read_session(self.session_id)
        self.assertEqual("WAITING", restored["instance_execution"]["A"]["state"])
        self.assertNotIn(
            "A", restored["mock_operation_lifecycle"]["instance_operations"]
        )

        self.clock["now"] += timedelta(seconds=1)
        restarted = self.host.start_instance_operation(
            "005930", "A", as_of=self.clock["now"]
        )["document"]
        self.assertEqual("RUNNING", restarted["instance_execution"]["A"]["state"])
        self.assertNotEqual(
            first_operation_id,
            restarted["mock_operation_lifecycle"]["instance_operations"]["A"][
                "operation_session_id"
            ],
        )

    def test_application_shutdown_stops_running_and_restart_requires_manual_start(self):
        self._start("A")
        self._open_order("A", "MO-SHUTDOWN-A", "BUY", 4)
        self.host.session_service.set_instance_position(
            self.session_id,
            "A",
            holding_qty=3,
            available_qty=3,
            average_price=101,
            realized_cost_basis=303,
            command_id="MC-shutdown-position-A",
        )
        self.host.session_service.set_instance_pnl(
            self.session_id,
            "A",
            realized_pnl=5,
            unrealized_pnl=7,
            gross_pnl=12,
            commission=1,
            mock_tax=1,
            net_pnl=10,
            command_id="MC-shutdown-pnl-A",
        )
        before = self.host.current_session("005930")
        old_operation = deepcopy(
            before["mock_operation_lifecycle"]["instance_operations"]["A"]
        )
        position_before = deepcopy(
            next(item for item in before["positions"] if item["routine_instance_id"] == "A")
        )
        pnl_before = deepcopy(
            next(item for item in before["pnl"] if item["routine_instance_id"] == "A")
        )
        reference_before = deepcopy(before["reference_snapshot"])

        shutdown = self.host.shutdown()

        self.assertEqual(1, len(shutdown["stopped"]))
        self.assertEqual((), shutdown["errors"])
        stopped = self.host.repository.read_session(self.session_id)
        self.assertEqual("VALIDATION_STOPPED", stopped["instance_execution"]["A"]["state"])
        self.assertFalse(stopped["instance_execution"]["A"]["progression_allowed"])
        self.assertEqual(
            "VALIDATION_STOPPED",
            stopped["mock_operation_lifecycle"]["instance_operations"]["A"]["state"],
        )
        self.assertEqual(
            {"CANCELED"},
            {
                item["state"]
                for item in stopped["orders"]
                if item["routine_instance_id"] == "A"
            },
        )
        self.assertEqual(
            position_before,
            next(item for item in stopped["positions"] if item["routine_instance_id"] == "A"),
        )
        self.assertEqual(
            pnl_before,
            next(item for item in stopped["pnl"] if item["routine_instance_id"] == "A"),
        )
        self.assertEqual(reference_before, stopped["reference_snapshot"])
        events_after_shutdown = self.host.repository.read_events(self.session_id)
        completed = next(
            item
            for item in reversed(events_after_shutdown)
            if item["event_type"] == "VALIDATION_STOP_COMPLETED"
            and item["routine_instance_id"] == "A"
        )
        self.assertEqual("APPLICATION_SHUTDOWN", completed["payload"]["source"])
        self.assertEqual(old_operation, completed["payload"]["operation_snapshot"])

        restarted = MockValidationHost(
            _Api(),
            project_root=self.project_root,
            now_factory=lambda: self.clock["now"],
            operation_policy_provider=lambda: {
                "regular_market": {"end_time": "15:30:00"},
                "liquidation": {"minutes_before_regular_close": 5, "method": "시장가"},
            },
            candles_provider=lambda **_kwargs: {
                "available": False,
                "candles": [],
                "availability_state": "SOURCE_UNAVAILABLE",
            },
        )
        self.addCleanup(restarted.dispose)
        self.assertEqual((), restarted._restart_recovery_result["stopped"])
        self.assertEqual(
            len(events_after_shutdown),
            len(restarted.repository.read_events(self.session_id)),
        )
        restarted.routine_adapter.evaluate_cycle = Mock()
        restarted.process_due_cycles(as_of=self.clock["now"])
        restarted.routine_adapter.evaluate_cycle.assert_not_called()
        self.assertTrue(restarted.instance_context_state("005930", "A")["can_start"])

        self.clock["now"] += timedelta(seconds=1)
        started = restarted.start_instance_operation("005930", "A", as_of=self.clock["now"])
        self.assertNotEqual(
            old_operation["operation_session_id"],
            started["operation"]["operation_session_id"],
        )
        self.assertEqual("RUNNING", started["document"]["instance_execution"]["A"]["state"])
        self.assertEqual(
            position_before,
            next(
                item
                for item in started["document"]["positions"]
                if item["routine_instance_id"] == "A"
            ),
        )
        self.assertEqual(
            pnl_before,
            next(
                item
                for item in started["document"]["pnl"]
                if item["routine_instance_id"] == "A"
            ),
        )

    def test_restart_defensively_stops_only_stale_active_instance(self):
        self._start("A")
        self._start("B")
        self._start("C")
        self._open_order("A", "MO-CRASH-A", "BUY", 2)
        self.host.request_instance_early_close(
            "005930", "A", method="MARKET", as_of=self.clock["now"]
        )
        self.host.stop_instance_validation(
            "005930", "B", command_id="MC-pre-restart-stop-B"
        )
        self.host.session_service.stop_for_instance_error(
            self.session_id,
            source_routine_instance_id="C",
            reason_code="MOCK_ROUTINE_ADAPTER_INTEGRITY_FAILURE",
            reason="MOCK_EVENT_ID_CONFLICT",
            command_id="MC-pre-restart-error-C",
        )
        waiting_created = self.actions.create_waiting_session(_reference("006400"))
        waiting_session_id = waiting_created["document"]["session"][
            "validation_session_id"
        ]
        waiting_before = payload_hash(waiting_created["document"])
        before = self.host.current_session("005930")
        self.assertEqual("CLOSING", before["instance_execution"]["A"]["state"])
        old_operation_id = before["mock_operation_lifecycle"]["instance_operations"]["A"][
            "operation_session_id"
        ]
        b_before = payload_hash(
            {
                "execution": before["instance_execution"]["B"],
                "operation": before["mock_operation_lifecycle"]["instance_operations"]["B"],
            }
        )
        c_before = payload_hash(
            {
                "execution": before["instance_execution"]["C"],
                "operation": before["mock_operation_lifecycle"]["instance_operations"]["C"],
                "position": next(
                    item for item in before["positions"] if item["routine_instance_id"] == "C"
                ),
                "pnl": next(item for item in before["pnl"] if item["routine_instance_id"] == "C"),
            }
        )

        restarted = MockValidationHost(
            _Api(),
            project_root=self.project_root,
            now_factory=lambda: self.clock["now"],
            operation_policy_provider=lambda: {
                "regular_market": {"end_time": "15:30:00"},
                "liquidation": {"minutes_before_regular_close": 5, "method": "시장가"},
            },
            candles_provider=lambda **_kwargs: {
                "available": False,
                "candles": [],
                "availability_state": "SOURCE_UNAVAILABLE",
            },
        )
        self.addCleanup(restarted.dispose)

        self.assertEqual(1, len(restarted._restart_recovery_result["stopped"]))
        self.assertEqual((), restarted._restart_recovery_result["errors"])
        after = restarted.current_session("005930")
        self.assertEqual(
            waiting_before,
            payload_hash(restarted.repository.read_session(waiting_session_id)),
        )
        self.assertEqual("VALIDATION_STOPPED", after["instance_execution"]["A"]["state"])
        self.assertEqual("VALIDATION_STOPPED", after["instance_execution"]["B"]["state"])
        self.assertEqual("ERROR", after["instance_execution"]["C"]["state"])
        self.assertFalse(after["instance_execution"]["C"]["progression_allowed"])
        self.assertEqual(
            b_before,
            payload_hash(
                {
                    "execution": after["instance_execution"]["B"],
                    "operation": after["mock_operation_lifecycle"]["instance_operations"]["B"],
                }
            ),
        )
        self.assertEqual(
            c_before,
            payload_hash(
                {
                    "execution": after["instance_execution"]["C"],
                    "operation": after["mock_operation_lifecycle"]["instance_operations"]["C"],
                    "position": next(
                        item
                        for item in after["positions"]
                        if item["routine_instance_id"] == "C"
                    ),
                    "pnl": next(
                        item for item in after["pnl"] if item["routine_instance_id"] == "C"
                    ),
                }
            ),
        )
        self.assertEqual(
            {"CANCELED"},
            {
                item["state"]
                for item in after["orders"]
                if item["routine_instance_id"] == "A"
            },
        )
        completed = next(
            item
            for item in reversed(restarted.repository.read_events(self.session_id))
            if item["event_type"] == "VALIDATION_STOP_COMPLETED"
            and item["routine_instance_id"] == "A"
        )
        self.assertEqual("APPLICATION_RESTART_RECOVERY", completed["payload"]["source"])
        self.assertEqual(
            old_operation_id,
            completed["payload"]["operation_snapshot"]["operation_session_id"],
        )
        restarted.routine_adapter.evaluate_cycle = Mock()
        restarted.process_due_cycles(as_of=self.clock["now"])
        restarted.routine_adapter.evaluate_cycle.assert_not_called()
        self.assertTrue(restarted.instance_context_state("005930", "A")["can_start"])
        self.assertTrue(restarted.instance_context_state("005930", "C")["can_reset"])

    def test_stopped_instance_never_progresses_or_auto_starts_on_host_cycles(self):
        self._start("A")
        self._start("B")
        self.host.stop_instance_validation(
            "005930", "B", command_id="MC-stop-host-cycle"
        )
        stopped = self.host.current_session("005930")
        frozen = payload_hash(
            {
                "execution": stopped["instance_execution"]["B"],
                "orders": [item for item in stopped["orders"] if item["routine_instance_id"] == "B"],
                "fills": [item for item in stopped["fills"] if item["routine_instance_id"] == "B"],
                "position": next(item for item in stopped["positions"] if item["routine_instance_id"] == "B"),
                "pnl": next(item for item in stopped["pnl"] if item["routine_instance_id"] == "B"),
                "cycle": stopped["cycle_state_by_instance"]["B"],
                "progression": stopped["progression_by_instance"]["B"],
            }
        )
        evaluated = []
        self.host.routine_adapter.evaluate_cycle = lambda _session, **kwargs: evaluated.append(kwargs["routine_instance_id"])
        for seconds in (1, 2, 3):
            self.host.process_due_cycles(as_of=NOW + timedelta(seconds=seconds))
        current = self.host.current_session("005930")
        self.assertNotIn("B", evaluated)
        self.assertEqual("VALIDATION_STOPPED", current["instance_execution"]["B"]["state"])
        self.assertEqual(
            frozen,
            payload_hash(
                {
                    "execution": current["instance_execution"]["B"],
                    "orders": [item for item in current["orders"] if item["routine_instance_id"] == "B"],
                    "fills": [item for item in current["fills"] if item["routine_instance_id"] == "B"],
                    "position": next(item for item in current["positions"] if item["routine_instance_id"] == "B"),
                    "pnl": next(item for item in current["pnl"] if item["routine_instance_id"] == "B"),
                    "cycle": current["cycle_state_by_instance"]["B"],
                    "progression": current["progression_by_instance"]["B"],
                }
            ),
        )

    def test_restart_manual_start_hold_covers_time_manual_and_ats_for_ten_seconds(self):
        settings = {
            "A": {
                "operation_mode": "SCHEDULED",
                "operation_schedule": {
                    "start_time": "09:00:00",
                    "end_buy_time": "13:30:00",
                },
                "manual_ats": {"selected_sessions": []},
            },
            "B": {
                "operation_mode": "CONTINUOUS",
                "manual_ats": {"selected_sessions": []},
            },
            "C": {
                "operation_mode": "CONTINUOUS",
                "manual_ats": {"selected_sessions": ["extra1"]},
            },
        }
        old_operation_ids = {}
        for instance_id, changes in settings.items():
            self.actions.set_instance_effective_settings(
                "005930", instance_id, **changes
            )
            started = self.host.start_instance_operation(
                "005930", instance_id, as_of=self.clock["now"]
            )
            old_operation_ids[instance_id] = started["operation"][
                "operation_session_id"
            ]

        self.host.dispose()  # Crash-like boundary: no graceful shutdown writer.
        restarted = MockValidationHost(
            _Api(),
            project_root=self.project_root,
            now_factory=lambda: self.clock["now"],
            operation_policy_provider=lambda: {
                "regular_market": {"end_time": "15:30:00"},
                "extra_sessions": [
                    {
                        "enabled": True,
                        "start_time": "08:00:00",
                        "end_time": "08:50:00",
                    }
                ],
                "liquidation": {
                    "minutes_before_regular_close": 5,
                    "method": "시장가",
                },
            },
            candles_provider=lambda **_kwargs: {
                "available": False,
                "candles": [],
                "availability_state": "SOURCE_UNAVAILABLE",
            },
        )
        self.addCleanup(restarted.dispose)
        after_recovery = restarted.current_session("005930")
        operation_events_before = len(
            [
                item
                for item in restarted.repository.read_events(self.session_id)
                if item["event_type"]
                in {"OPERATION_SESSION_CREATED", "OPERATION_STARTED"}
            ]
        )
        orders_before = payload_hash(after_recovery.get("orders", ()))
        restarted.routine_adapter.evaluate_cycle = Mock()

        for seconds in range(1, 11):
            restarted.process_due_cycles(
                as_of=self.clock["now"] + timedelta(seconds=seconds)
            )

        current = restarted.current_session("005930")
        restarted.routine_adapter.evaluate_cycle.assert_not_called()
        self.assertEqual(orders_before, payload_hash(current.get("orders", ())))
        self.assertFalse(hasattr(restarted, "_auto_start_scheduled_instances"))
        self.assertEqual(
            operation_events_before,
            len(
                [
                    item
                    for item in restarted.repository.read_events(self.session_id)
                    if item["event_type"]
                    in {"OPERATION_SESSION_CREATED", "OPERATION_STARTED"}
                ]
            ),
        )
        tree = current_mock_monitoring_trees(
            restarted.repository,
            as_of=self.clock["now"] + timedelta(seconds=10),
        )[0]
        for child in tree["children"]:
            instance_id = child["routine_instance_id"]
            operation = current["mock_operation_lifecycle"]["instance_operations"][
                instance_id
            ]
            self.assertEqual("VALIDATION_STOPPED", child["state"])
            self.assertFalse(child["status_cell_active"])
            self.assertEqual("VALIDATION_STOPPED", operation["state"])
            self.assertEqual(old_operation_ids[instance_id], operation["operation_session_id"])
            self.assertTrue(restarted.instance_context_state("005930", instance_id)["can_start"])

        original_host, original_actions = self.host, self.actions
        self.host = restarted
        self.actions = MockValidationUIActions(restarted)
        try:
            owner, table, _controller = self._table_window()
            target = context_menu.mock_context_target_for_row(
                owner, self._row(table, "A")
            )
            with patch.object(quick_chart, "_bar_minutes", return_value=5):
                chart = quick_chart.MockInstanceQuickChartWindow(owner, target)
            self.addCleanup(chart.close)
            self.assertEqual(
                "대기중", chart.header_badges["operation_status"].text()
            )
        finally:
            self.host, self.actions = original_host, original_actions

        self.clock["now"] += timedelta(seconds=11)
        manually_started = restarted.start_instance_operation(
            "005930", "A", as_of=self.clock["now"]
        )
        self.assertEqual("RUNNING", manually_started["document"]["instance_execution"]["A"]["state"])
        self.assertNotEqual(
            old_operation_ids["A"],
            manually_started["operation"]["operation_session_id"],
        )

    def test_restart_normalizes_active_operation_ledger_even_if_execution_is_stopped(self):
        self._start("A")
        old_operation_id = self.host.current_session("005930")[
            "mock_operation_lifecycle"
        ]["instance_operations"]["A"]["operation_session_id"]

        def make_execution_stale(document):
            document["instance_execution"]["A"].update(
                {"state": "VALIDATION_STOPPED", "progression_allowed": False}
            )
            return document

        before = self.host.current_session("005930")
        self.host.repository.mutate_session(
            self.session_id,
            make_execution_stale,
            expected_revision=before["revision"],
        )
        self.host.dispose()

        restarted = MockValidationHost(
            _Api(),
            project_root=self.project_root,
            now_factory=lambda: self.clock["now"],
            operation_policy_provider=lambda: {},
            candles_provider=lambda **_kwargs: [],
        )
        self.addCleanup(restarted.dispose)
        current = restarted.current_session("005930")
        operation = current["mock_operation_lifecycle"]["instance_operations"]["A"]
        self.assertEqual("VALIDATION_STOPPED", current["instance_execution"]["A"]["state"])
        self.assertFalse(current["instance_execution"]["A"]["progression_allowed"])
        self.assertEqual("VALIDATION_STOPPED", operation["state"])
        self.assertEqual(old_operation_id, operation["operation_session_id"])
        self.assertTrue(restarted.instance_context_state("005930", "A")["can_start"])

    def test_mock_child_context_has_validation_stop_and_chart_with_exact_availability(self):
        self._start("B")
        window, table, _controller = self._table_window()
        row = self._row(table, "B")
        position = table.visualItemRect(table.item(row, 0)).center()
        _Menu.chosen_text = ""
        _Menu.chosen_menu_title = None
        with patch.object(context_menu, "QMenu", _Menu):
            context_menu.show_mock_monitoring_context_menu(
                window,
                position,
                expected_row_kind=main_table_loader.ROUTINE_ROW_MOCK_INSTANCE,
            )
        texts = [action.text() for action in _Menu.root.actions]
        self.assertEqual(
            [
                "운영시작",
                "검증종료",
                "<separator>",
                "전체선택",
                "선택해제",
                "<separator>",
                "간이차트",
                "검증리셋",
            ],
            texts,
        )
        self.assertFalse(next(action for action in _Menu.root.actions if action.text() == "운영시작").isEnabled())
        self.assertTrue(next(action for action in _Menu.root.actions if action.text() == "검증종료").isEnabled())
        self.assertFalse(next(action for action in _Menu.root.actions if action.text() == "검증리셋").isEnabled())

        _Menu.chosen_text = "검증종료"
        with patch.object(context_menu, "QMenu", _Menu):
            context_menu.show_mock_monitoring_context_menu(
                window,
                position,
                expected_row_kind=main_table_loader.ROUTINE_ROW_MOCK_INSTANCE,
            )
        self.assertEqual(
            "VALIDATION_STOPPED",
            self.host.current_session("005930")["instance_execution"]["B"]["state"],
        )
        _Menu.chosen_text = ""
        with patch.object(context_menu, "QMenu", _Menu):
            context_menu.show_mock_monitoring_context_menu(
                window,
                position,
                expected_row_kind=main_table_loader.ROUTINE_ROW_MOCK_INSTANCE,
            )
        self.assertTrue(next(action for action in _Menu.root.actions if action.text() == "운영시작").isEnabled())
        self.assertFalse(next(action for action in _Menu.root.actions if action.text() == "검증종료").isEnabled())
        self.assertTrue(next(action for action in _Menu.root.actions if action.text() == "검증리셋").isEnabled())

        _Menu.chosen_text = "간이차트"
        with (
            patch.object(context_menu, "QMenu", _Menu),
            patch.object(
                context_menu,
                "open_mock_instance_quick_chart",
                wraps=quick_chart.open_mock_instance_quick_chart,
            ) as opened,
        ):
            context_menu.show_mock_monitoring_context_menu(
                window,
                position,
                expected_row_kind=main_table_loader.ROUTINE_ROW_MOCK_INSTANCE,
            )
        opened.assert_called_once()
        self.assertEqual("B", opened.call_args.args[1].routine_instance_id)
        target = context_menu.mock_context_target_for_row(window, row)
        key = (target.validation_session_id, target.routine_instance_id)
        self.app.processEvents()
        gc.collect()
        self.app.processEvents()
        chart = quick_chart._OPEN_MOCK_INSTANCE_CHARTS.get(key)
        self.assertIsNotNone(chart)
        self.assertTrue(chart.isVisible())
        self.assertIsNone(chart.parent())
        chart.close()
        del chart
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        self.assertNotIn(key, quick_chart._OPEN_MOCK_INSTANCE_CHARTS)

    def test_actual_viewport_double_click_opens_only_from_mock_routine_name(self):
        window, table, controller = self._table_window()
        row = self._row(table, "B")
        index = table.model().index(row, 0)
        name_rect = controller._mock_instance_name_rect(index)
        self.assertFalse(name_rect.isNull())
        window.toggle_mock_routine_instance_initial_buy_mode = Mock()
        window.open_mock_routine_instance_initial_buy_dialog = Mock()
        window.toggle_mock_routine_instance_operation_mode = Mock()
        with patch.object(
                gui_windows,
                "open_mock_instance_quick_chart",
                wraps=quick_chart.open_mock_instance_quick_chart,
            ) as opened:
            QTest.mouseClick(table.viewport(), Qt.LeftButton, pos=name_rect.center())
            self.app.processEvents()
            opened.assert_not_called()
            self.assertEqual(row, table.currentRow())

            QTest.mouseDClick(table.viewport(), Qt.LeftButton, pos=name_rect.center())
            self.app.processEvents()
            opened.assert_called_once()
            self.assertEqual("B", opened.call_args.args[1].routine_instance_id)
            opened.reset_mock()

            values = index.data(main_table_loader.ROUTINE_STOCK_VALUES_ROLE)
            for metric_index in range(1, len(values)):
                metric_rect = controller._stock_legacy_metric_rect(index, metric_index)
                self.assertFalse(metric_rect.isNull())
                QTest.mouseDClick(
                    table.viewport(), Qt.LeftButton, pos=metric_rect.center()
                )
                self.app.processEvents()
            opened.assert_not_called()

        target = context_menu.mock_context_target_for_row(window, row)
        key = (target.validation_session_id, target.routine_instance_id)
        gc.collect()
        self.app.processEvents()
        chart = quick_chart._OPEN_MOCK_INSTANCE_CHARTS[key]
        self.assertTrue(chart.isVisible())
        self.assertIsNone(chart.parent())
        chart.close()
        del chart
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        self.assertNotIn(key, quick_chart._OPEN_MOCK_INSTANCE_CHARTS)

    def test_mock_quick_chart_reuses_chart_ui_without_production_operation_adapter(self):
        window, table, _controller = self._table_window()
        target_a = context_menu.mock_context_target_for_row(window, self._row(table, "A"))
        target_b = context_menu.mock_context_target_for_row(window, self._row(table, "B"))
        self.assertIsNotNone(target_a)
        self.assertIsNotNone(target_b)
        created = []

        class FakeChart:
            def __init__(self, owner, target):
                self.owner = owner
                self.target = target
                self.stock_code = target.stock_code
                self.visible = True
                self.refresh_count = 0
                created.append(self)

            def setAttribute(self, *_args):
                return None

            def show(self):
                self.visible = True

            def raise_(self):
                return None

            def activateWindow(self):
                return None

            def refresh_projection(self):
                self.refresh_count += 1

            def isVisible(self):
                return self.visible

            destroyed = SimpleNamespace(connect=lambda _callback: None)

        with patch.object(quick_chart, "MockInstanceQuickChartWindow", FakeChart):
            chart_a = quick_chart.open_mock_instance_quick_chart(window, target_a)
            chart_b = quick_chart.open_mock_instance_quick_chart(window, target_b)
        self.assertIsNot(chart_a, chart_b)
        self.assertEqual("005930", chart_a.stock_code)
        self.assertEqual("A", chart_a.target.routine_instance_id)
        self.assertEqual("B", chart_b.target.routine_instance_id)
        self.assertEqual(2, len(created))

        stale = SimpleNamespace(**target_a.__dict__)
        stale.validation_session_id = "MV-stale"
        self.assertIsNone(quick_chart.open_mock_instance_quick_chart(window, stale))

    def test_mock_quick_chart_uses_keyword_only_production_candle_provider(self):
        window, table, _controller = self._table_window()
        target = context_menu.mock_context_target_for_row(window, self._row(table, "A"))
        calls = []

        def provider(**kwargs):
            calls.append(kwargs)
            return {
                "available": True,
                "availability_state": "AVAILABLE",
                "candles": [
                    {
                        "bar_time": "2026-09-07T09:00:00+09:00",
                        "open": 100,
                        "high": 101,
                        "low": 99,
                        "close": 100,
                        "volume": 10,
                        "timeframe_minutes": 5,
                        "is_complete": True,
                    }
                ],
                "timeframe_minutes": 5,
                "available_minute_candles": 72,
                "completeness": {"completed_only": True},
                "freshness": {"as_of": NOW.isoformat()},
                "source_identity": "PRODUCTION-CANDLE-SOURCE",
            }

        self.host._candles_provider = provider
        projected = quick_chart._projection(window, target, "005930", "2026-09-07")

        self.assertEqual(1, len(projected["candles"]))
        self.assertEqual("VALID", projected["projection_status"])
        self.assertEqual("PRODUCTION-CANDLE-SOURCE", projected["source_identity"])
        self.assertEqual("005930", calls[0]["stock_code"])
        self.assertEqual("MOCK", calls[0]["consumer_scope"])
        self.assertIsInstance(calls[0]["projection_request"], dict)
        self.assertNotIn("warmup_bars", calls[0]["projection_request"])
        self.assertEqual(
            projected["bar_minutes"],
            calls[0]["rules"].get("bar_minutes"),
        )

        self.host._candles_provider = lambda **_kwargs: {
            "available": False,
            "candles": [],
            "availability_state": "SOURCE_UNAVAILABLE",
            "available_minute_candles": 0,
            "completeness": {},
            "freshness": {},
            "source_identity": "EMPTY-SOURCE",
        }
        unavailable = quick_chart._projection(
            window,
            target,
            "005930",
            "2026-09-07",
        )
        self.assertEqual("NOT_READY", unavailable["projection_status"])
        self.assertEqual(
            "SOURCE_UNAVAILABLE",
            unavailable["candle_availability"]["availability_state"],
        )

        self.host._candles_provider = lambda **_kwargs: (_ for _ in ()).throw(
            TypeError("provider query failed")
        )
        with self.assertRaisesRegex(TypeError, "provider query failed"):
            quick_chart._projection(window, target, "005930", "2026-09-07")

    def test_mock_quick_chart_uses_the_canonical_window_shell_and_mock_header_projection(self):
        self._start("A")
        self.host._candles_provider = lambda **_kwargs: {
            "available": True,
            "availability_state": "AVAILABLE",
            "candles": [
                {
                    "bar_time": "2026-09-07T12:00:00+09:00",
                    "open": 100,
                    "high": 101,
                    "low": 99,
                    "close": 100,
                    "volume": 10,
                    "timeframe_minutes": 5,
                    "is_complete": True,
                }
            ],
            "timeframe_minutes": 5,
            "source_identity": "SHARED-CANDLE-SOURCE",
        }
        owner, table, _controller = self._table_window()
        target = context_menu.mock_context_target_for_row(owner, self._row(table, "A"))
        production = quick_chart.StockInstanceChartWindow(
            "005930",
            NOW.date().isoformat(),
            owner,
            projection_provider=lambda _code, _day: {
                "stock_code": "005930",
                "stock_name": "삼성전자",
                "trade_date": NOW.date().isoformat(),
                "instance_id": "A",
                "instance_name": "지표추종매매A",
                "bar_minutes": 5,
                "operation_title_display": "Production",
                "projection_status": "VALID",
                "candles": [
                    {
                        "bar_time": "2026-09-07T12:00:00+09:00",
                        "close": 100,
                    }
                ],
                "buy_signal_markers": [],
                "sell_signal_markers": [],
                "actual_fill_markers": [],
                "execution_process_rails": [],
                "pnl_available": True,
            },
        )
        with patch.object(quick_chart, "_bar_minutes", return_value=5):
            mock = quick_chart.MockInstanceQuickChartWindow(owner, target)
        self.addCleanup(production.close)
        self.addCleanup(mock.close)

        self.assertEqual("stockInstanceChartWindow", production.objectName())
        self.assertEqual(production.objectName(), mock.objectName())
        self.assertEqual(production.windowFlags(), mock.windowFlags())
        self.assertEqual(production.windowModality(), mock.windowModality())
        self.assertEqual(production.minimumSize(), mock.minimumSize())
        self.assertEqual(production.styleSheet(), mock.styleSheet())
        self.assertEqual(type(production.chart), type(mock.chart))
        self.assertIs(
            quick_chart.MockInstanceQuickChartWindow._find_live_price_operation_host,
            quick_chart.StockInstanceChartWindow._find_live_price_operation_host,
        )
        self.assertIs(
            quick_chart.MockInstanceQuickChartWindow._find_bar_committed_signal,
            quick_chart.StockInstanceChartWindow._find_bar_committed_signal,
        )
        self.assertEqual(NOW.date().isoformat(), mock.trade_date)
        document = self.host.current_session("005930")
        self.assertEqual(
            document["mock_operation_lifecycle"]["instance_operations"]["A"][
                "operation_session_id"
            ],
            mock.last_projection["operation_session_id"],
        )
        self.assertEqual("매수/매도", mock.operation_info_labels["status"].text())
        self.assertEqual("루틴", mock.operation_info_labels["method"].text())
        self.assertNotEqual("-", mock.operation_info_labels["liquidation"].text())
        self.assertEqual(
            ["모의", "수동", "운영중", "5분봉", "NXT"],
            [
                mock.header_badges[key].text()
                for key in (
                    "domain",
                    "operation_method",
                    "operation_status",
                    "bar",
                    "nxt",
                )
            ],
        )
        self.assertFalse(mock.header_badges["nxt"].isEnabled())
        self.assertFalse(mock.early_close_button.isEnabled())
        self.assertFalse(mock.immediate_liquidation_button.isEnabled())

    def test_production_and_mock_share_nxt_eligibility_and_exchange_axis(self):
        self._start("A")
        self.host._candles_provider = lambda **_kwargs: {
            "available": True,
            "availability_state": "AVAILABLE",
            "candles": [
                {
                    "bar_time": "2026-09-07T09:00:00+09:00",
                    "close": 100,
                    "timeframe_minutes": 5,
                    "is_complete": True,
                }
            ],
            "timeframe_minutes": 5,
        }
        market_projection = {
            "nxt_available": True,
            "market_eligibility": {"krx": True, "nxt": True},
            "market_sessions": [
                {"name": "NXT_PRE", "start_time": "08:00:00", "end_time": "08:50:00"},
                {"name": "KRX", "start_time": "09:00:00", "end_time": "15:30:00"},
                {"name": "NXT_AFTER", "start_time": "15:40:00", "end_time": "20:00:00"},
            ],
        }
        owner, table, _controller = self._table_window()
        target = context_menu.mock_context_target_for_row(owner, self._row(table, "A"))
        with patch.object(
            quick_chart,
            "chart_market_session_projection",
            return_value=market_projection,
        ), patch.object(quick_chart, "_bar_minutes", return_value=5):
            mock = quick_chart.MockInstanceQuickChartWindow(owner, target)
        production_projection = deepcopy(mock.last_projection)
        production_projection["chart_domain_label"] = "KRX"
        production = quick_chart.StockInstanceChartWindow(
            "005930",
            NOW.date().isoformat(),
            owner,
            projection_provider=lambda _code, _day: production_projection,
        )
        self.addCleanup(production.close)
        self.addCleanup(mock.close)

        self.assertTrue(production.header_badges["nxt"].isEnabled())
        self.assertTrue(mock.header_badges["nxt"].isEnabled())
        self.assertEqual("KRX", production.header_badges["domain"].text())
        self.assertEqual("모의", mock.header_badges["domain"].text())
        self.assertEqual(
            production.chart.visible_time_ranges,
            mock.chart.visible_time_ranges,
        )
        self.assertEqual("08:00", mock.chart.fixed_time_range[0].strftime("%H:%M"))
        self.assertEqual("20:00", mock.chart.fixed_time_range[1].strftime("%H:%M"))

    def test_quick_chart_projects_unique_current_operation_signal_markers(self):
        self._start("A")
        window, table, _controller = self._table_window()
        target = context_menu.mock_context_target_for_row(window, self._row(table, "A"))
        document = self.host.current_session("005930")
        operation_id = document["mock_operation_lifecycle"]["instance_operations"]["A"][
            "operation_session_id"
        ]
        rules = self.host._operation_rules(document, "A")
        rules_hash = payload_hash(rules)
        for index, (side, bar_time, scoped_operation, instance_id) in enumerate(
            (
                ("BUY", "2026-09-07T09:05:00+09:00", operation_id, "A"),
                ("BUY", "2026-09-07T09:05:00+09:00", operation_id, "A"),
                ("SELL", "2026-09-07T09:10:00+09:00", operation_id, "A"),
                ("SELL", "2026-09-07T09:15:00+09:00", "MS-old", "A"),
                ("SELL", "2026-09-07T09:20:00+09:00", operation_id, "B"),
            ),
            1,
        ):
            self.host.repository.append_event(
                {
                    "event_id": f"ME-signal-marker-{index}",
                    "validation_session_id": self.session_id,
                    "stock_code": "005930",
                    "routine_instance_id": instance_id,
                    "event_type": "ROUTINE_EVALUATED",
                    "timestamp": (NOW + timedelta(seconds=index)).isoformat(timespec="microseconds"),
                    "reason_code": "",
                    "payload": {
                        "signal": side,
                        "rules_hash": rules_hash,
                        "operation_identity": scoped_operation,
                        "signal_bar_time": bar_time,
                        "signal_bar_close": 100 + index,
                        "signal_trade_date": "2026-09-07",
                    },
                }
            )
        self.host._candles_provider = lambda **_kwargs: {
            "available": True,
            "candles": [
                {"bar_time": "2026-09-07T09:05:00+09:00", "close": 101},
                {"bar_time": "2026-09-07T09:10:00+09:00", "close": 103},
            ],
            "availability_state": "AVAILABLE",
        }

        projected = quick_chart._projection(window, target, "005930", "2026-09-07")

        self.assertEqual(1, len(projected["buy_signal_markers"]))
        self.assertEqual(1, len(projected["sell_signal_markers"]))
        self.assertEqual(
            "2026-09-07T09:10:00+09:00",
            projected["sell_signal_markers"][0]["signal_bar_time"],
        )

    def test_mock_flow_reaches_quick_chart_signal_marker_without_error(self):
        self._start("A")
        self.host._candles_provider = lambda **_kwargs: {
            "available": True,
            "candles": [
                {
                    "bar_time": "2026-09-07T12:00:00+09:00",
                    "close": 100,
                    "volume": 10,
                    "timeframe_minutes": 5,
                }
            ],
            "availability_state": "AVAILABLE",
        }
        self.host.routine_adapter._evaluator = lambda *_args: {
            "signal": "SELL",
            "reason": "fixture",
            "signal_index": 0,
        }
        self.assertTrue(
            self.host.accept_orderbook(
                replace(_book(), received_at=NOW.isoformat(timespec="microseconds"))
            )
        )

        self.host.process_due_cycles(as_of=NOW)
        window, table, _controller = self._table_window()
        target = context_menu.mock_context_target_for_row(window, self._row(table, "A"))
        projected = quick_chart._projection(window, target, "005930", "2026-09-07")

        self.assertEqual(1, len(projected["candles"]))
        self.assertEqual(0, len(projected["buy_signal_markers"]))
        self.assertEqual(1, len(projected["sell_signal_markers"]))
        document = self.host.current_session("005930")
        self.assertEqual("RUNNING", document["instance_execution"]["A"]["state"])
        self.assertFalse(
            any(
                event["event_type"] == "INSTANCE_ERROR"
                for event in self.host.repository.read_events(self.session_id)
            )
        )

    def test_quick_chart_strong_registry_lifetime_cleanup_and_failures(self):
        window, table, _controller = self._table_window()
        target_a = context_menu.mock_context_target_for_row(window, self._row(table, "A"))
        target_b = context_menu.mock_context_target_for_row(window, self._row(table, "B"))
        target_c = context_menu.mock_context_target_for_row(window, self._row(table, "C"))
        key_a = (target_a.validation_session_id, target_a.routine_instance_id)
        key_b = (target_b.validation_session_id, target_b.routine_instance_id)
        key_c = (target_c.validation_session_id, target_c.routine_instance_id)

        def flush_destroyed() -> None:
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.app.processEvents()
            gc.collect()
            self.app.processEvents()

        for _iteration in range(5):
            quick_chart.open_mock_instance_quick_chart(window, target_a)
            self.app.processEvents()
            gc.collect()
            self.app.processEvents()
            current = quick_chart._OPEN_MOCK_INSTANCE_CHARTS[key_a]
            self.assertTrue(current.isVisible())
            self.assertIsNone(current.parent())
            identity = id(current)
            quick_chart.open_mock_instance_quick_chart(window, target_a)
            self.assertEqual(identity, id(quick_chart._OPEN_MOCK_INSTANCE_CHARTS[key_a]))
            current.close()
            del current
            flush_destroyed()
            self.assertNotIn(key_a, quick_chart._OPEN_MOCK_INSTANCE_CHARTS)

        for target in (target_a, target_b, target_c):
            quick_chart.open_mock_instance_quick_chart(window, target)
        self.app.processEvents()
        gc.collect()
        self.app.processEvents()
        self.assertEqual(
            {key_a, key_b, key_c},
            set(quick_chart._OPEN_MOCK_INSTANCE_CHARTS),
        )
        chart_a = quick_chart._OPEN_MOCK_INSTANCE_CHARTS[key_a]
        chart_a.close()
        del chart_a
        flush_destroyed()
        self.assertNotIn(key_a, quick_chart._OPEN_MOCK_INSTANCE_CHARTS)
        self.assertIn(key_b, quick_chart._OPEN_MOCK_INSTANCE_CHARTS)
        self.assertIn(key_c, quick_chart._OPEN_MOCK_INSTANCE_CHARTS)
        for key in (key_b, key_c):
            chart = quick_chart._OPEN_MOCK_INSTANCE_CHARTS[key]
            chart.close()
            del chart
        flush_destroyed()
        self.assertNotIn(key_b, quick_chart._OPEN_MOCK_INSTANCE_CHARTS)
        self.assertNotIn(key_c, quick_chart._OPEN_MOCK_INSTANCE_CHARTS)

        class ProbeChart(QDialog):
            def __init__(self, _owner, target):
                super().__init__(None)
                self.target = target
                self.stock_code = target.stock_code
                self.refresh_count = 0

            def refresh_projection(self):
                self.refresh_count += 1

        with patch.object(quick_chart, "MockInstanceQuickChartWindow", ProbeChart):
            old = quick_chart.open_mock_instance_quick_chart(window, target_a)
            quick_chart._OPEN_MOCK_INSTANCE_CHARTS.pop(key_a)
            new = quick_chart.open_mock_instance_quick_chart(window, target_a)
            old.close()
            del old
            flush_destroyed()
            self.assertIs(new, quick_chart._OPEN_MOCK_INSTANCE_CHARTS[key_a])
            new.close()
            del new
            flush_destroyed()
            self.assertNotIn(key_a, quick_chart._OPEN_MOCK_INSTANCE_CHARTS)

            deleted = ProbeChart(window, target_a)
            quick_chart._OPEN_MOCK_INSTANCE_CHARTS[key_a] = deleted
            deleted.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.app.processEvents()
            replacement = quick_chart.open_mock_instance_quick_chart(
                window, target_a
            )
            self.assertIsNot(deleted, replacement)
            self.assertIs(
                replacement, quick_chart._OPEN_MOCK_INSTANCE_CHARTS[key_a]
            )
            replacement.close()
            del replacement, deleted
            flush_destroyed()
            self.assertNotIn(key_a, quick_chart._OPEN_MOCK_INSTANCE_CHARTS)

        class ConstructorFailure:
            def __init__(self, _owner, _target):
                raise RuntimeError("ORIGINAL_CONSTRUCTOR_FAILURE")

        with patch.object(
            quick_chart, "MockInstanceQuickChartWindow", ConstructorFailure
        ):
            with self.assertRaisesRegex(RuntimeError, "ORIGINAL_CONSTRUCTOR_FAILURE"):
                quick_chart.open_mock_instance_quick_chart(window, target_a)
        self.assertNotIn(key_a, quick_chart._OPEN_MOCK_INSTANCE_CHARTS)

        class ShowFailure(ProbeChart):
            def show(self):
                raise RuntimeError("ORIGINAL_SHOW_FAILURE")

        with patch.object(quick_chart, "MockInstanceQuickChartWindow", ShowFailure):
            with self.assertRaisesRegex(RuntimeError, "ORIGINAL_SHOW_FAILURE"):
                quick_chart.open_mock_instance_quick_chart(window, target_a)
        self.assertNotIn(key_a, quick_chart._OPEN_MOCK_INSTANCE_CHARTS)


if __name__ == "__main__":
    unittest.main()
