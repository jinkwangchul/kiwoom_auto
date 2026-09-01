# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import inspect
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QRectF
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QApplication, QPushButton

import gui_stock_instance_chart_window as chart_window
import stock_instance_day_projection
from execution_chart_read_model import (
    option_snapshot_summary,
    project_execution_chart_read_model,
)
from execution_provenance_contract import option_snapshot_hash
from gui_stock_instance_chart_window import StockInstanceChartWindow, StockInstanceCloseChart


TRADE_DATE = "2026-09-01"
CODE = "005070"


def _snapshot(*, mode: str = "SINGLE", source_kind: str = "ROUTINE_SIGNAL") -> dict:
    snapshot = {
        "provenance_contract_version": 1,
        "approved_at": f"{TRADE_DATE}T09:29:59+09:00",
        "routine_id": "INDICATOR_FOLLOW",
        "routine_name": "지표추종매매",
        "routine_instance_id": "INSTANCE_1",
        "source_kind": source_kind,
        "side": "BUY",
        "execution_mode": mode,
        "buy_phase": "BASE",
        "buy_round": 1,
    }
    if source_kind == "OPERATION_COMMAND":
        snapshot["source_command_id"] = "COMMAND_1"
    else:
        snapshot["source_signal_id"] = "SIGNAL_1"
    return snapshot


def _process(*, process_id: str = "PROCESS_1", snapshot: dict | None = None) -> dict:
    frozen = deepcopy(snapshot or _snapshot())
    return {
        "execution_process_id": process_id,
        "provenance_contract_version": 1,
        "approved_at": frozen["approved_at"],
        "source_kind": frozen["source_kind"],
        "source_signal_id": frozen.get("source_signal_id"),
        "source_command_id": frozen.get("source_command_id"),
        "option_snapshot": frozen,
        "option_snapshot_hash": option_snapshot_hash(frozen),
        "process_plan": {"planned_total_quantity": 10},
        "status": "APPROVED",
    }


def _execution(
    process: dict,
    *,
    execution_id: str = "EXEC_1",
    index: int = 1,
    total: int = 1,
    kind: str = "SINGLE_ORDER",
    plan: dict | None = None,
) -> dict:
    return {
        "execution_id": execution_id,
        "execution_process_id": process["execution_process_id"],
        "order_id": f"ORDER_{execution_id}",
        "child_sequence_index": index,
        "child_sequence_total": total,
        "child_kind": kind,
        "child_plan": deepcopy(plan or {"planned_quantity": 10, "planned_price": 40250}),
        "option_snapshot_hash": process["option_snapshot_hash"],
        "status": "RUNTIME_WRITE_PREVIEW",
    }


def _queue(execution: dict, *, status: str = "SEND_CALL_ACCEPTED") -> dict:
    return {
        "id": f"QUEUED_{execution['execution_id']}",
        "code": CODE,
        "execution_trade_date": TRADE_DATE,
        "execution_id": execution["execution_id"],
        "execution_process_id": execution["execution_process_id"],
        "order_id": execution["order_id"],
        "child_sequence_index": execution["child_sequence_index"],
        "child_sequence_total": execution["child_sequence_total"],
        "child_kind": execution["child_kind"],
        "child_plan": deepcopy(execution["child_plan"]),
        "option_snapshot_hash": execution["option_snapshot_hash"],
        "status": status,
        "send_order_called": True,
        "broker_order_no": f"BROKER_{execution['execution_id']}",
        "created_at": f"{TRADE_DATE}T09:30:00+09:00",
    }


def _fill(
    *,
    fill_id: str = "FILL_1",
    execution_id: str = "EXEC_1",
    process_id: str = "PROCESS_1",
    cumulative: int = 10,
    price: int = 40250,
    second: int = 1,
    side: str = "BUY",
    source: str = "BROKER_FID_908",
) -> dict:
    broker_time = f"{TRADE_DATE}T09:30:{second:02d}+09:00" if source == "BROKER_FID_908" else None
    return {
        "fill_id": fill_id,
        "execution_id": execution_id,
        "execution_process_id": process_id,
        "execution_identity_source": "BROKER_EXECUTION_NO",
        "execution_identity": f"IDENTITY_{fill_id}",
        "broker_order_no": f"BROKER_{execution_id}",
        "order_id": f"ORDER_{execution_id}",
        "order_queued_id": f"QUEUED_{execution_id}",
        "account_no": "12345678",
        "code": CODE,
        "side": side,
        "filled_quantity": cumulative,
        "filled_price": price,
        "broker_execution_time_raw": f"0930{second:02d}",
        "broker_execution_datetime": broker_time,
        "execution_time_source": source,
        "execution_time_quality": "EXACT" if source == "BROKER_FID_908" else "APPROXIMATE",
        "received_at": f"{TRADE_DATE}T09:31:{second:02d}+09:00",
        "recorded_at": f"{TRADE_DATE}T09:32:{second:02d}+09:00",
    }


def _read_model(
    fills: list[dict],
    *,
    process: dict | None = None,
    executions: list[dict] | None = None,
    queues: list[dict] | None = None,
    legacy: bool = False,
) -> dict:
    root = {"version": 1, "executions": executions or []}
    if not legacy:
        root["processes"] = [process] if process else []
    return project_execution_chart_read_model(
        fills=fills,
        order_executions=root,
        queue_records=queues or [],
        stock_code=CODE,
        trade_date=TRADE_DATE,
    )


def _chart_projection(**updates) -> dict:
    base = {
        "stock_code": CODE,
        "stock_name": "코스모신소재",
        "trade_date": TRADE_DATE,
        "instance_id": "INSTANCE_1",
        "instance_name": "지표추종-A",
        "bar_minutes": 1,
        "operation_title_display": "시간운영",
        "projection_status": "VALID",
        "candles": [
            {"bar_time": f"{TRADE_DATE}T09:00:00+09:00", "close": 40000},
            {"bar_time": f"{TRADE_DATE}T09:30:00+09:00", "close": 40300},
        ],
        "buy_signal_markers": [],
        "sell_signal_markers": [],
        "buy_signal_count": 0,
        "sell_signal_count": 0,
        "actual_fill_markers": [],
        "execution_process_rails": [],
        "average_price": None,
        "average_price_visible": False,
        "pnl_available": False,
        "diagnostics": {"raw_candle_count": 2, "issues": []},
    }
    base.update(updates)
    return base


class ExecutionChartReadModelTests(unittest.TestCase):
    def test_single_exact_fill_joins_process_child_queue_and_rail(self) -> None:
        process = _process()
        execution = _execution(process)
        result = _read_model([_fill()], process=process, executions=[execution], queues=[_queue(execution)])

        marker = result["actual_fill_markers"][0]
        self.assertEqual("COMPLETE", marker["provenance_status"])
        self.assertEqual(f"{TRADE_DATE}T09:30:01+09:00", marker["occurred_at"])
        self.assertEqual("PROCESS_1", marker["execution_process_id"])
        self.assertEqual("단일 주문", marker["option_summary"])
        self.assertEqual(1, marker["child_sequence_index"])
        rail = result["execution_process_rails"][0]
        self.assertEqual("PROCESS_1", rail["execution_process_id"])
        self.assertEqual(["FILL_1"], rail["children"][0]["fill_ids"])
        self.assertEqual("COMPLETED", rail["status"])

    def test_partial_cumulative_fills_keep_three_markers_and_3_2_5_deltas(self) -> None:
        process = _process()
        execution = _execution(process)
        fills = [
            _fill(fill_id="F1", cumulative=3, second=1, price=40250),
            _fill(fill_id="F2", cumulative=5, second=2, price=40250),
            _fill(fill_id="F3", cumulative=10, second=2, price=40250),
        ]
        result = _read_model(fills, process=process, executions=[execution], queues=[_queue(execution)])
        self.assertEqual([3, 2, 5], [item["filled_quantity_delta"] for item in result["actual_fill_markers"]])
        self.assertEqual(3, len({item["fill_id"] for item in result["actual_fill_markers"]}))
        self.assertTrue(all(item["execution_id"] == "EXEC_1" for item in result["actual_fill_markers"]))

    def test_local_received_time_is_approximate_and_unavailable_time_has_no_marker(self) -> None:
        process = _process()
        execution = _execution(process)
        approximate = _fill(source="LOCAL_RECEIVED_AT")
        result = _read_model([approximate], process=process, executions=[execution], queues=[_queue(execution)])
        self.assertEqual("LOCAL_RECEIVED_AT", result["actual_fill_markers"][0]["execution_time_source"])
        self.assertEqual("APPROXIMATE", result["actual_fill_markers"][0]["execution_time_quality"])
        self.assertEqual(f"{TRADE_DATE}T09:31:01+09:00", result["actual_fill_markers"][0]["occurred_at"])

        unavailable = _fill()
        unavailable.update({"execution_time_source": "NONE", "execution_time_quality": "UNAVAILABLE"})
        result = _read_model([unavailable], process=process, executions=[execution], queues=[_queue(execution)])
        self.assertEqual([], result["actual_fill_markers"])
        self.assertIn("FILL_TIME_UNAVAILABLE", {item["reason"] for item in result["diagnostics"]})

    def test_legacy_fill_can_render_without_option_or_rail(self) -> None:
        legacy_fill = _fill(process_id="")
        legacy_fill.pop("execution_process_id")
        result = _read_model([legacy_fill], legacy=True)
        self.assertEqual(1, len(result["actual_fill_markers"]))
        self.assertEqual("LEGACY_MISSING", result["actual_fill_markers"][0]["provenance_status"])
        self.assertEqual("", result["actual_fill_markers"][0]["option_summary"])
        self.assertEqual([], result["execution_process_rails"])

    def test_invalid_process_reference_keeps_fill_marker_but_suppresses_rail(self) -> None:
        fill = _fill(process_id="MISSING")
        execution = {**_execution(_process(process_id="MISSING")), "option_snapshot_hash": "missing"}
        result = _read_model([fill], executions=[execution])
        self.assertEqual("INVALID", result["actual_fill_markers"][0]["provenance_status"])
        self.assertEqual([], result["execution_process_rails"])

    def test_cancel_and_operation_command_are_rail_only_evidence(self) -> None:
        process = _process(snapshot=_snapshot(source_kind="OPERATION_COMMAND"))
        original = _execution(process)
        cancel = _execution(process, execution_id="EXEC_CANCEL", kind="CANCEL", plan={"planned_quantity": 10})
        result = _read_model(
            [_fill()],
            process=process,
            executions=[original, cancel],
            queues=[_queue(original), _queue(cancel, status="CANCEL_CONFIRMED")],
        )
        self.assertEqual(1, len(result["actual_fill_markers"]))
        rail = result["execution_process_rails"][0]
        self.assertEqual("OPERATION_COMMAND", rail["source_kind"])
        self.assertEqual("COMMAND_1", rail["source_command_id"])
        self.assertEqual(["SINGLE_ORDER", "CANCEL"], [child["child_kind"] for child in rail["children"]])
        self.assertEqual([], rail["children"][1]["fill_ids"])

    def test_multi_time_and_hoga_fixtures_create_rails_without_fill_markers(self) -> None:
        for mode, kind, plans, expected_summary in (
            (
                "MULTI_TIME",
                "TIME_SLICE",
                [{"scheduled_offset_ms": value} for value in (0, 30000, 60000)],
                "다중시간 1분 이내 3회",
            ),
            (
                "MULTI_HOGA",
                "HOGA_LEVEL",
                [{"hoga_offset_ticks": value, "planned_price": 40250 - value} for value in (0, -1, -2)],
                "다중호가 ↑0 / ↓2",
            ),
        ):
            with self.subTest(mode=mode):
                snapshot = _snapshot(mode=mode)
                if mode == "MULTI_TIME":
                    snapshot["point"] = {"point_value": 1, "point_unit": "MINUTE", "point_count": 3}
                else:
                    snapshot["hoga"] = {"hoga_mode": "MULTI", "hoga_up": 0, "hoga_down": 2}
                process = _process(process_id=f"PROCESS_{mode}", snapshot=snapshot)
                executions = [
                    _execution(process, execution_id=f"EXEC_{mode}_{index}", index=index, total=3, kind=kind, plan=plan)
                    for index, plan in enumerate(plans, start=1)
                ]
                result = _read_model([], process=process, executions=executions, queues=[_queue(item) for item in executions])
                self.assertEqual([], result["actual_fill_markers"])
                self.assertEqual(expected_summary, result["execution_process_rails"][0]["option_summary"])
                self.assertEqual([1, 2, 3], [child["child_sequence_index"] for child in result["execution_process_rails"][0]["children"]])

    def test_option_summary_never_reads_current_rules(self) -> None:
        snapshot = _snapshot(mode="MULTI_TIME")
        snapshot["point"] = {"point_value": 1, "point_unit": "MINUTE", "point_count": 3}
        self.assertEqual("다중시간 1분 이내 3회", option_snapshot_summary(snapshot))
        snapshot["execution_mode"] = "SINGLE"
        self.assertEqual("단일 주문", option_snapshot_summary(snapshot))

    def test_day_projection_wires_runtime_read_model_without_writing_any_runtime_file(self) -> None:
        process = _process()
        execution = _execution(process)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dir = root / "stocks" / f"{CODE}_코스모신소재"
            stock_dir.mkdir(parents=True)
            (stock_dir / "config.json").write_text(json.dumps({"name": "코스모신소재"}), encoding="utf-8")
            (stock_dir / "state.json").write_text("{}", encoding="utf-8")
            runtime = root / "runtime"
            runtime.mkdir()
            (runtime / "fills.json").write_text(json.dumps({"version": 1, "fills": [_fill()]}), encoding="utf-8")
            (runtime / "order_executions.json").write_text(
                json.dumps({"version": 1, "processes": [process], "executions": [execution]}),
                encoding="utf-8",
            )
            before = {path.name: path.read_bytes() for path in runtime.iterdir() if path.is_file()}
            projected = stock_instance_day_projection.project_stock_instance_day(
                CODE,
                TRADE_DATE,
                project_root=root,
            )
            after = {path.name: path.read_bytes() for path in runtime.iterdir() if path.is_file()}

        self.assertEqual(before, after)
        self.assertEqual(1, len(projected["actual_fill_markers"]))
        self.assertEqual(1, len(projected["execution_process_rails"]))
        self.assertEqual("COMPLETE", projected["actual_fill_markers"][0]["provenance_status"])

    def test_day_projection_recomputes_average_price_and_hides_after_full_sell(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stock_dir = root / "stocks" / f"{CODE}_코스모신소재"
            stock_dir.mkdir(parents=True)
            (stock_dir / "config.json").write_text(json.dumps({"name": "코스모신소재"}), encoding="utf-8")
            (stock_dir / "state.json").write_text("{}", encoding="utf-8")
            runtime = root / "runtime"
            runtime.mkdir()
            positions_path = runtime / "positions.json"

            def write_position(quantity: int, average_price: int) -> None:
                positions_path.write_text(
                    json.dumps({"positions": [{"account_no": "12345678", "code": CODE, "quantity": quantity, "average_price": average_price}]}),
                    encoding="utf-8",
                )

            def project() -> dict:
                return stock_instance_day_projection.project_stock_instance_day(
                    CODE,
                    TRADE_DATE,
                    project_root=root,
                    now=datetime.fromisoformat(f"{TRADE_DATE}T12:00:00+09:00"),
                )

            write_position(3, 40100)
            first = project()
            write_position(5, 40300)
            second = project()
            write_position(0, 0)
            sold = project()

        self.assertTrue(first["average_price_visible"])
        self.assertEqual(40100.0, first["average_price"])
        self.assertEqual(40300.0, second["average_price"])
        self.assertFalse(sold["average_price_visible"])
        self.assertIsNone(sold["average_price"])


class ExecutionChartUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_signal_and_actual_fill_series_remain_distinct_at_same_coordinate(self) -> None:
        chart = StockInstanceCloseChart()
        time = f"{TRADE_DATE}T09:30:00+09:00"
        chart.set_projection(
            [{"bar_time": time, "close": 40250}],
            [{"signal_bar_time": time, "signal_bar_close": 40250}],
            [],
            actual_fill_markers=[{"marker_id": "M1", "fill_id": "F1", "side": "BUY", "occurred_at": time, "filled_price": 40250}],
            x_range_start=f"{TRADE_DATE}T09:00:00+09:00",
            x_range_end=f"{TRADE_DATE}T15:30:00+09:00",
        )
        self.assertEqual(1, len(chart.buy_series))
        self.assertEqual(1, len(chart.actual_buy_fill_series))
        self.assertIsNot(chart.buy_series, chart.actual_buy_fill_series)
        chart.close()

    def test_average_price_is_scaled_drawn_and_replaced_on_same_bar_refresh(self) -> None:
        first = _chart_projection(average_price=40100, average_price_visible=True)
        second = _chart_projection(average_price=40500, average_price_visible=True)
        third = _chart_projection(average_price=None, average_price_visible=False, holding_quantity=0)
        provider = Mock(side_effect=[first, second, third])
        window = StockInstanceChartWindow(CODE, TRADE_DATE, projection_provider=provider)
        self.assertEqual(40100.0, window.chart.average_price)
        _minimum_time, _maximum_time, low, high = window.chart._scale_values()
        self.assertLessEqual(low, 40100.0)
        self.assertGreaterEqual(high, 40100.0)
        window.refresh_projection(preserve_pnl_if_same_bar=True)
        self.assertEqual(40500.0, window.chart.average_price)
        window.refresh_projection(preserve_pnl_if_same_bar=True)
        self.assertIsNone(window.chart.average_price)
        window.close()

    def test_marker_selection_links_process_rail_and_shows_evidence_detail(self) -> None:
        marker = {
            "marker_id": "ACTUAL_FILL:F1",
            "fill_id": "F1",
            "side": "BUY",
            "filled_quantity_delta": 3,
            "filled_price": 40250,
            "occurred_at": f"{TRADE_DATE}T09:30:01+09:00",
            "execution_time_source": "LOCAL_RECEIVED_AT",
            "execution_time_quality": "APPROXIMATE",
            "execution_process_id": "PROCESS_1",
            "execution_id": "EXEC_1",
            "execution_identity": "FILL_NO_1",
            "option_summary": "단일 주문",
            "child_sequence_index": 1,
            "child_sequence_total": 1,
            "broker_order_no": "BROKER_1",
        }
        rail = {
            "execution_process_id": "PROCESS_1",
            "side": "BUY",
            "option_summary": "단일 주문",
            "status": "COMPLETED",
            "child_total": 1,
            "child_completed": 1,
            "children": [],
        }
        window = StockInstanceChartWindow(
            CODE,
            TRADE_DATE,
            projection_provider=lambda *_: _chart_projection(actual_fill_markers=[marker], execution_process_rails=[rail]),
        )
        window._on_actual_fill_marker_selected(marker)
        self.assertEqual("PROCESS_1", window.process_rail.selected_execution_process_id)
        self.assertIn("Chejan 수신시각(근사)", window.fill_detail_label.text())
        window._on_execution_process_selected("PROCESS_1")
        self.assertEqual("PROCESS_1", window.chart.selected_execution_process_id)
        window.close()

    def test_current_price_label_has_no_realtime_word_and_never_joins_history(self) -> None:
        source = inspect.getsource(StockInstanceCloseChart._draw_live_price_projection)
        self.assertNotIn('f"실시간 ', source)
        chart = StockInstanceCloseChart()
        chart.set_projection(
            [
                {"bar_time": f"{TRADE_DATE}T09:00:00+09:00", "close": 40000},
                {"bar_time": f"{TRADE_DATE}T09:01:00+09:00", "close": 40100},
            ],
            [],
            [],
            x_range_start=f"{TRADE_DATE}T09:00:00+09:00",
            x_range_end=f"{TRADE_DATE}T15:30:00+09:00",
        )
        chart.set_live_price_projection(f"{TRADE_DATE}T13:52:00+09:00", 40400)
        self.assertEqual(2, sum(len(segment) for segment in chart._line_segments()))
        self.assertNotIn(chart.live_price_point, [point for segment in chart._line_segments() for point in segment])
        chart.close()

    def test_price_signal_button_absent_and_twenty_refreshes_create_no_marker_timers(self) -> None:
        marker = {"marker_id": "M1", "fill_id": "F1", "side": "SELL", "occurred_at": f"{TRADE_DATE}T09:30:00+09:00", "filled_price": 40250}
        window = StockInstanceChartWindow(
            CODE,
            TRADE_DATE,
            projection_provider=lambda *_: _chart_projection(actual_fill_markers=[marker]),
        )
        self.assertFalse(any("가격신호" in button.text() for button in window.findChildren(QPushButton)))
        initial_timers = len(window.chart.findChildren(chart_window.QTimer))
        for _ in range(20):
            window.chart.set_projection(
                _chart_projection()["candles"], [], [], actual_fill_markers=[marker]
            )
        self.assertEqual(initial_timers, len(window.chart.findChildren(chart_window.QTimer)))
        pixmap = QPixmap(820, 428)
        window.resize(pixmap.size())
        window.render(pixmap)
        self.assertFalse(pixmap.isNull())
        window.close()

    def test_chart_refresh_calls_no_tr_real_registration_or_send_order(self) -> None:
        host = SimpleNamespace(
            CommRqData=Mock(),
            SetRealReg=Mock(),
            SendOrder=Mock(),
            high_resolution_market_state=Mock(return_value=None),
            high_resolution_market_data_snapshot=Mock(return_value=None),
        )

        class Owner(chart_window.QDialog):
            def main_monitoring_auto_trade_operation_host(self):
                return host

        owner = Owner()
        with patch.object(chart_window, "_today_trade_date", return_value=TRADE_DATE):
            window = StockInstanceChartWindow(CODE, TRADE_DATE, owner, projection_provider=lambda *_: _chart_projection())
            if window._live_price_refresh_timer is not None:
                window._live_price_refresh_timer.stop()
            window.refresh_projection()
            window.refresh_live_price_projection()
        host.CommRqData.assert_not_called()
        host.SetRealReg.assert_not_called()
        host.SendOrder.assert_not_called()
        window.close()
        owner.close()


if __name__ == "__main__":
    unittest.main()
