"""Mock evidence -> shared chart contract; no user repository mutations."""

from copy import deepcopy
from datetime import datetime
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from mock_validation_contract import payload_hash
from mock_validation_execution_chart_read_model import project_mock_execution_chart
import mock_validation_quick_chart as quick_chart
from gui_stock_instance_chart_window import ExecutionProcessRail, StockInstanceChartWindow
from tests import test_mock_indicator_follow_adapter as adapter_fixture
from tests import test_stock_instance_chart_execution_results as chart_fixture


DAY = "2026-09-11"


def document_fixture():
    return {
        "session": {"stock_code": "005930", "stock_name": "삼성전자", "validation_session_id": "S"},
        "mock_operation_lifecycle": {"instance_operations": {"A": {
            "operation_session_id": "OP", "trading_date": DAY,
            "started_at": DAY + "T09:00:00+09:00", "ended_at": "",
        }}},
        "progression_by_instance": {}, "orders": [], "fills": [],
    }


def add_plan(doc, *, key="P1", mode="SINGLE", side="BUY", round=1, count=1, completed=1, instance="A", day=DAY):
    identity = {**doc["session"], "routine_instance_id": instance}
    children = []
    for index in range(1, count + 1):
        order_id = key + f"-O{index}"
        at = day + f"T10:00:{index:02d}+09:00"
        status = "FILLED" if index <= completed else "OPEN"
        child = {
            "child_sequence": index, "mock_order_id": order_id, "quantity": 3,
            "filled_qty": 3 if index <= completed else 0,
            "remaining_qty": 0 if index <= completed else 3,
            "status": status, "due_at": at,
            "intent": {"price": 100 + index, "child_kind": mode, "child_plan": {"planned_price": 100 + index}},
        }
        children.append(child)
        doc["orders"].append({**identity, "mock_order_id": order_id, "side": side, "state": status, "created_at": at})
        if index <= completed:
            doc["fills"].append({**identity, "mock_fill_id": key + f"-F{index}", "mock_order_id": order_id,
                                 "side": side, "qty": 3, "price": 100 + index, "filled_at": at, "fill_sequence": index})
    plan = {"plan_id": key, "execution_process_id": key, "mode": mode, "side": side, "round": round,
            "state": "COMPLETED" if completed == count else "ACTIVE", "plan_started_at": day + "T10:00:00+09:00",
            "children": children, "hoga": {"hoga_offsets": [0, -1, -2]},
            "schedule": {"interval": 30, "interval_unit": "SECOND"}, "ratio": {"ratio_value": 0.5}}
    doc["progression_by_instance"].setdefault(instance, {"indicator_follow_mock_adapter": {"plans": []}})["indicator_follow_mock_adapter"]["plans"].append(plan)
    return plan


class MockExecutionChartTests(unittest.TestCase):
    def test_modes_counts_and_read_only(self):
        for mode, count, completed, label in (
            ("SINGLE", 1, 1, "단일 주문"), ("MULTI_HOGA", 3, 3, "다중호가 ↑0 / ↓2"),
            ("MULTI_TIME", 3, 2, "다중시간 30초 3회"), ("MULTI_RATIO", 3, 2, "다중비율 3회"),
        ):
            with self.subTest(mode=mode):
                doc = document_fixture()
                add_plan(doc, mode=mode, count=count, completed=completed)
                before = deepcopy(doc)
                result = project_mock_execution_chart(doc, "A", DAY)
                self.assertEqual(before, doc)
                self.assertEqual([], result["diagnostics"])
                self.assertEqual(completed, len(result["actual_fill_markers"]))
                rail, = result["execution_process_rails"]
                self.assertEqual((count, completed, 1, label), (rail["child_total"], rail["child_completed"], rail["buy_round"], rail["option_summary"]))
                self.assertIn("1회차", ExecutionProcessRail._row_text(rail))
                self.assertIn(f"{'완료' if completed == count else '진행'} {completed}/{count}", ExecutionProcessRail._row_text(rail))
                for index, marker in enumerate(result["actual_fill_markers"], 1):
                    self.assertEqual((index, count, 1), (marker["child_sequence_index"], marker["child_sequence_total"], marker["buy_round"]))
                    self.assertEqual("MOCK_FILL:" + marker["fill_id"], marker["marker_id"])
                    self.assertNotIn("broker_order_no", marker)

    def test_rounds_sell_partial_cancel_and_no_fabricated_completion(self):
        doc = document_fixture()
        add_plan(doc)
        plan = add_plan(doc, key="P2", round=2)
        plan["state"] = "ACTIVE"
        plan["children"][0].update(status="PARTIAL_FILL", filled_qty=2, remaining_qty=1)
        doc["fills"][-1]["qty"] = 1
        fill = deepcopy(doc["fills"][-1]); fill["mock_fill_id"] += "-partial"; fill["filled_at"] = DAY + "T10:00:02+09:00"
        doc["fills"].append(fill)
        sell = add_plan(doc, key="P3", side="SELL", round=0, completed=0)
        sell["children"][0]["status"] = "CANCELED"
        result = project_mock_execution_chart(doc, "A", DAY)
        self.assertEqual(3, len(result["actual_fill_markers"]))
        one, two, sell = result["execution_process_rails"]
        self.assertIn("1회차", ExecutionProcessRail._row_text(one))
        self.assertIn("2회차", ExecutionProcessRail._row_text(two))
        self.assertEqual((0, "PARTIAL", 2), (two["child_completed"], two["children"][0]["status"], len(two["children"][0]["fill_ids"])))
        self.assertNotIn("회차", ExecutionProcessRail._row_text(sell))
        self.assertEqual("CANCELLED", sell["children"][0]["status"])

    def test_instance_date_and_previous_operation_isolation(self):
        doc = document_fixture()
        add_plan(doc)
        add_plan(doc, key="B", instance="B")
        add_plan(doc, key="OLD", day="2026-09-10")
        old = add_plan(doc, key="PREVIOUS")
        old["plan_started_at"] = DAY + "T08:59:59+09:00"
        result = project_mock_execution_chart(doc, "A", DAY)
        self.assertEqual(["P1"], [p["execution_process_id"] for p in result["execution_process_rails"]])
        self.assertEqual(["P1-F1"], [f["fill_id"] for f in result["actual_fill_markers"]])
        # Second precision matters for same-minute restart boundaries.
        doc["mock_operation_lifecycle"]["instance_operations"]["A"]["started_at"] = DAY + "T10:00:01+09:00"
        self.assertEqual([], project_mock_execution_chart(doc, "A", DAY)["actual_fill_markers"])

    def test_orphan_keeps_only_fill_facts_duplicate_ownership_fails_closed(self):
        doc = document_fixture(); plan = add_plan(doc)
        doc["progression_by_instance"] = {}
        result = project_mock_execution_chart(doc, "A", DAY)
        self.assertEqual(1, len(result["actual_fill_markers"]))
        marker = result["actual_fill_markers"][0]
        self.assertEqual(("", None), (marker["execution_process_id"], marker["buy_round"]))
        self.assertTrue(result["diagnostics"])
        doc["progression_by_instance"] = {"A": {"indicator_follow_mock_adapter": {"plans": [plan, deepcopy(plan)]}}}
        result = project_mock_execution_chart(doc, "A", DAY)
        self.assertEqual([], result["actual_fill_markers"])
        self.assertEqual([], result["execution_process_rails"])
        self.assertTrue(result["diagnostics"])

    def test_bad_fill_or_order_identity_is_not_joined(self):
        for field, value in (("stock_code", "012210"), ("validation_session_id", "OTHER"), ("side", "SELL")):
            doc = document_fixture(); add_plan(doc)
            doc["orders"][0][field] = value
            result = project_mock_execution_chart(doc, "A", DAY)
            self.assertEqual([], result["actual_fill_markers"])
            self.assertEqual([], result["execution_process_rails"])
            self.assertTrue(result["diagnostics"])
        doc = document_fixture(); add_plan(doc); doc["fills"].append(deepcopy(doc["fills"][0]))
        self.assertEqual([], project_mock_execution_chart(doc, "A", DAY)["actual_fill_markers"])

    def test_real_adapter_virtual_fill_schema_projects_without_mutation(self):
        for mode in ("SINGLE", "MULTI_HOGA", "MULTI_TIME", "MULTI_RATIO"):
            with self.subTest(mode=mode):
                fixture = adapter_fixture.MockIndicatorFollowAdapterTest()
                self.addCleanup(fixture.doCleanups)
                repo, _, _, adapter, _ = fixture.build({"A": adapter_fixture._buy_rules(mode=mode)})
                fixture.evaluate(adapter)
                doc = repo.read_session(adapter_fixture.SESSION_ID)
                before = deepcopy(doc)
                result = project_mock_execution_chart(doc, "A", adapter_fixture.NOW.date().isoformat())
                self.assertEqual(len(doc["fills"]), len(result["actual_fill_markers"]))
                rail, = result["execution_process_rails"]
                plan = doc["progression_by_instance"]["A"]["indicator_follow_mock_adapter"]["plans"][0]
                self.assertEqual(sum(c["status"] == "FILLED" for c in plan["children"]), rail["child_completed"])
                self.assertEqual([], result["diagnostics"])
                self.assertEqual(before, repo.read_session(adapter_fixture.SESSION_ID))

    def test_sell_fill_has_no_buy_round_and_option_missing_is_not_invented(self):
        doc = document_fixture(); plan = add_plan(doc, side="SELL", round=7, mode="MULTI_TIME")
        plan.pop("schedule")
        result = project_mock_execution_chart(doc, "A", DAY)
        marker, = result["actual_fill_markers"]
        self.assertIsNone(marker["buy_round"])
        self.assertEqual("다중시간", marker["option_summary"])
        self.assertNotIn("회차", StockInstanceChartWindow._actual_fill_detail_text(marker))

    def test_quick_chart_wires_signal_fill_and_rail_independently(self):
        doc = document_fixture(); add_plan(doc, mode="MULTI_HOGA", count=3, completed=3)
        rules = {"bar": {"bar_minutes": 1}}
        doc.update(reference_snapshot={"routine_instances": [{"routine_instance_id": "A", "rules_snapshot": rules}]},
                   instance_execution={"A": {"state": "RUNNING"}}, positions=[{"routine_instance_id": "A", "holding_qty": 9, "average_price": 102}],
                   pnl=[{"routine_instance_id": "A", "mark_price": 104}])
        events = [{"event_type": "ROUTINE_EVALUATED", "stock_code": "005930", "routine_instance_id": "A", "payload": {
            "signal": "BUY", "operation_identity": "OP", "rules_hash": payload_hash(rules), "signal_trade_date": DAY,
            "signal_bar_time": DAY + "T10:00:00+09:00", "signal_bar_close": 100}}]
        host = SimpleNamespace(current_session=lambda *_: doc, _operation_rules=lambda *_: rules,
                               _operation_effective_settings=lambda *_: {}, _now=lambda: datetime.fromisoformat(DAY + "T10:01:00+09:00"),
                               routine_adapter=SimpleNamespace(market_bar_projection_request=lambda *_: {}),
                               _instance_candle_projection=lambda *_, **kw: {"available": True, "candles": [{"bar_time": DAY + "T10:00:00+09:00", "close": 100}]},
                               repository=SimpleNamespace(read_events=lambda *_: events), project_root="unused")
        target = SimpleNamespace(stock_code="005930", routine_instance_id="A", validation_session_id="S")
        before = deepcopy(doc)
        with patch.object(quick_chart, "mock_instance_projection", return_value={}), patch.object(quick_chart, "chart_market_session_projection", return_value={}):
            projected = quick_chart._projection(SimpleNamespace(mock_validation_host=host), target, "005930", DAY)
        self.assertEqual((1, 0, 3, 1), tuple(len(projected[key]) for key in ("buy_signal_markers", "sell_signal_markers", "actual_fill_markers", "execution_process_rails")))
        self.assertEqual(102, projected["average_price"])
        self.assertEqual(before, doc)


class MockExecutionChartGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_diamond_click_rail_click_round_and_simulated_time(self):
        doc = document_fixture(); add_plan(doc, mode="MULTI_HOGA", count=3, completed=3)
        execution = project_mock_execution_chart(doc, "A", DAY)
        projection = chart_fixture._chart_projection(
            trade_date=DAY, **{key: execution[key] for key in ("actual_fill_markers", "execution_process_rails")},
            candles=[{"bar_time": DAY + "T10:00:00+09:00", "close": 100}, {"bar_time": DAY + "T10:01:00+09:00", "close": 105}],
            buy_signal_markers=[{"signal_bar_time": DAY + "T10:00:00+09:00", "signal_bar_close": 100}], buy_signal_count=1,
        )
        window = StockInstanceChartWindow("005930", DAY, projection_provider=lambda *_: projection)
        self.addCleanup(window.close)
        window.show(); self.app.processEvents()
        with patch.object(window.chart, "_draw_actual_fill_marker", wraps=window.chart._draw_actual_fill_marker) as draw:
            window.chart.grab()
        self.assertGreater(draw.call_count, 0)
        self.assertEqual(3, len(window.chart.actual_fill_marker_records))
        self.assertEqual(1, len(window.chart.buy_series))
        marker = window.chart.actual_fill_marker_records[1]
        point = window.chart.position_for(marker["_occurred_at"], marker["_filled_price"])
        QTest.mouseClick(window.chart, Qt.LeftButton, pos=point.toPoint())
        self.app.processEvents()
        self.assertEqual("P1", window.process_rail.selected_execution_process_id)
        for text in ("1회차", "모의 체결시각", "10:00:02", "수량 3", "2/3"):
            self.assertIn(text, window.fill_detail_label.text())
        window.process_rail.grab()
        rect, _ = window.process_rail._row_rects[0]
        QTest.mouseClick(window.process_rail, Qt.LeftButton, pos=rect.center().toPoint())
        self.assertEqual("P1", window.chart.selected_execution_process_id)
        self.assertIn("매수 1", window.windowTitle())
        self.assertIn("매도 0", window.windowTitle())
        sell = {**execution["actual_fill_markers"][0], "side": "SELL", "buy_round": 0}
        self.assertNotIn("회차", window._actual_fill_detail_text(sell))


if __name__ == "__main__":
    unittest.main()
