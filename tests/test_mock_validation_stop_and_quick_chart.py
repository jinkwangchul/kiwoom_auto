# -*- coding: utf-8 -*-

from __future__ import annotations

from copy import deepcopy
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
from mock_validation_contract import payload_hash
from mock_validation_host import MockValidationHost
from mock_validation_ui_actions import MockValidationUIActions
from mock_validation_ui_projection import current_mock_monitoring_trees
from tests.test_mock_validation_host_ui import _Api, _Menu, _reference


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
            candles_provider=lambda _document: [],
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
        self.assertEqual("검증정지", child["state_label"])
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
                "검증정지",
                "<separator>",
                "전체선택",
                "선택해제",
                "<separator>",
                "간이차트",
                "리셋",
            ],
            texts,
        )
        self.assertTrue(next(action for action in _Menu.root.actions if action.text() == "검증정지").isEnabled())

        _Menu.chosen_text = "검증정지"
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
