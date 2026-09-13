# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
import weakref
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QApplication, QDialog, QScrollArea

import gui_indicator_follow_routine_settings_dialog as dialog_module
import gui_indicator_follow_signal_validation_flow as flow_module
from gui_indicator_follow_signal_validation_flow import IndicatorFollowSignalValidationFlow
from gui_indicator_follow_signal_validation_window import IndicatorFollowSignalValidationWindow
from indicator_follow_signal_validation_presentation import filter_rows_for_entry
from indicator_follow_signal_validation_projection import (
    IndicatorFollowSignalValidationApplyPayload,
    IndicatorFollowSignalValidationSeed,
    project_signal_validation_rules,
)
from routines.지표추종매매.routine_validation_contract import (
    ValidationRequest,
    ValidationSettingsSnapshot,
    ValidationStockRef,
)
from routines.지표추종매매.routine_validation_replay import (
    ValidationReplayEntry,
    ValidationReplaySnapshot,
)
from routines.지표추종매매.routine_validation_session import ValidationSession


class _ConnectedBroker:
    def is_connected(self):
        return True


class _Host(QObject):
    validation_session_ready = pyqtSignal(object)
    validation_blocked = pyqtSignal(str)

    def __init__(self, stock):
        super().__init__()
        self.stock = stock

    def start(self, snapshot, *, ui_parent=None):
        session = ValidationSession(
            ValidationRequest(
                self.stock,
                snapshot,
                snapshot.to_dict()["bar"]["bar_minutes"],
            ),
            operation_active_reader=lambda: False,
        )
        self.validation_session_ready.emit(session)
        return session


class _AutoWindow(QDialog):
    validation_run_requested = pyqtSignal(object)
    settings_apply_requested = pyqtSignal(object)

    def __init__(self, stock, seed, parent=None):
        super().__init__(parent)
        self.stock = stock
        self.seed = seed
        self.initial_requests = 0
        self.run_was_connected = False
        self.apply_was_connected = False
        self.apply_results = []

    def request_initial_validation(self):
        self.initial_requests += 1
        self.run_was_connected = self.receivers(self.validation_run_requested) > 0
        self.apply_was_connected = self.receivers(self.settings_apply_requested) > 0

    def show_settings_apply_result(self, message, *, success):
        self.apply_results.append((message, success))

    def show_validation_error(self, _message):
        pass


class IndicatorFollowSignalValidationOperatorUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.project_root = Path(__file__).resolve().parents[1]
        cls.routine_dir = cls.project_root / "routines" / "지표추종매매"
        cls.rules = json.loads(
            (cls.routine_dir / "rules.json").read_text(encoding="utf-8")
        )
        cls.ui_state = cls.rules["indicator_follow_ui_state"]["state"]
        cls.stock = ValidationStockRef("005930", "삼성전자")

    def setUp(self):
        self.widgets = []

    def tearDown(self):
        for widget in self.widgets:
            try:
                widget.close()
                widget.deleteLater()
            except RuntimeError:
                pass
        self.app.processEvents()

    def _seed(self, ui_state=None):
        return IndicatorFollowSignalValidationSeed(
            ValidationSettingsSnapshot(self.rules),
            ui_state or self.ui_state,
        )

    def _window(self, ui_state=None):
        with patch.object(dialog_module.QTimer, "singleShot"):
            window = IndicatorFollowSignalValidationWindow(
                self.stock,
                self._seed(ui_state),
            )
        self.widgets.append(window)
        return window

    @staticmethod
    def _entry(side, index, *, signal=None, trace=None, details=None, reason="fixture"):
        return ValidationReplayEntry(
            evaluation_side=side,
            evaluation_index=index,
            evaluation_time=f"2026091310{index:02d}00",
            signal=signal,
            reason=reason,
            signal_index=index if signal else None,
            signal_time=f"2026091310{index:02d}00" if signal else None,
            delay_bar=0,
            matched_groups=["A"] if signal else [],
            details=details or [],
            trace=trace or {"conditions": [], "groups": [], "aggregations": []},
        )

    def _snapshot(self, entries):
        candles = [
            {
                "time": f"2026091310{index:02d}00",
                "open": 100 + index,
                "high": 102 + index,
                "low": 99 + index,
                "close": 101 + index,
                "volume": 1000,
            }
            for index in range(2)
        ]
        return ValidationReplaySnapshot(
            stock=self.stock,
            timeframe_minutes=5,
            settings_hash="hash",
            historical_request_id="REQ",
            evaluated_start_index=0,
            evaluated_end_index=1,
            dropped_raw_rows_count=0,
            candles=candles,
            entries=entries,
        )

    def test_compact_header_and_independent_sections_have_no_settings_scrollbar(self):
        window = self._window()
        self.assertEqual("005930 삼성전자", window.compact_stock_label.text())
        self.assertEqual("5", window.basic_signal_interval_combo.currentText())
        self.assertEqual(300, window.historical_candle_count_spin.value())
        all_labels = " ".join(
            label.text() for label in window.control_tab.findChildren(dialog_module.QLabel)
        )
        self.assertIn("기준봉", all_labels)
        self.assertIn("봉수", all_labels)
        for forbidden_attribute in (
            "basic_title",
            "registration_mode_label",
            "basic_duplicate_signal_combo",
            "basic_error_policy_combo",
            "control_scroll",
        ):
            self.assertFalse(hasattr(window, forbidden_attribute), forbidden_attribute)
        for forbidden_text in ("기본설정", "중복신호처리", "오류발생"):
            self.assertNotIn(forbidden_text, all_labels)
        self.assertEqual([], window.control_tab.findChildren(QScrollArea))

        window._show_with_initial_control_section_state()
        self.assertTrue(window.buy_detail_expanded)
        self.assertFalse(window.sell_detail_expanded)
        window._toggle_control_section_mode("sell")
        self.assertTrue(window.buy_detail_expanded)
        self.assertTrue(window.sell_detail_expanded)
        window._toggle_control_section_mode("buy")
        self.assertFalse(window.buy_detail_expanded)
        self.assertTrue(window.sell_detail_expanded)

    def test_existing_registration_basic_controls_remain_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            rules_path.write_text(json.dumps(self.rules, ensure_ascii=False), encoding="utf-8")
            with patch.object(dialog_module.QTimer, "singleShot"):
                dialog = dialog_module.IndicatorFollowRoutineSettingsDialog(
                    rules_path=rules_path,
                    routine_path=self.routine_dir,
                    routine_name="지표추종매매",
                    definition_id="indicator_follow",
                    settings_mode="registration",
                )
            self.widgets.append(dialog)
            self.assertEqual("▶ 기본설정", dialog.basic_title.text())
            self.assertTrue(hasattr(dialog, "basic_duplicate_signal_combo"))
            self.assertTrue(hasattr(dialog, "basic_error_policy_combo"))
            self.assertTrue(hasattr(dialog, "registration_mode_label"))

    def test_result_area_is_visible_with_loading_state_before_replay(self):
        window = self._window()
        window.show()
        self.app.processEvents()
        self.assertFalse(window.result_widget.isHidden())
        self.assertIs(window.loading_label, window.chart_stack.currentWidget())
        self.assertIn("과거 분봉 데이터 조회 중", window.loading_label.text())
        self.assertGreaterEqual(window.result_splitter.minimumHeight(), 280)

    def test_initial_validation_is_scheduled_once_after_both_signals_are_connected(self):
        callbacks = []
        created = []

        def factory(stock, seed, parent=None):
            window = _AutoWindow(stock, seed, parent)
            self.widgets.append(window)
            created.append(window)
            return window

        flow = IndicatorFollowSignalValidationFlow(
            _ConnectedBroker(),
            host=_Host(self.stock),
            window_factory=factory,
        )
        carrier = type("Carrier", (QDialog,), {"signal_validation_requested": pyqtSignal(object)})()
        self.widgets.append(carrier)
        flow.bind_dialog(carrier)
        with patch.object(flow_module.QTimer, "singleShot", side_effect=lambda _ms, callback: callbacks.append(callback)):
            carrier.signal_validation_requested.emit(self._seed())
        self.assertEqual(1, len(created))
        self.assertEqual(0, created[0].initial_requests)
        self.assertEqual(1, len(callbacks))
        callbacks[0]()
        self.assertEqual(1, created[0].initial_requests)
        self.assertTrue(created[0].run_was_connected)
        self.assertTrue(created[0].apply_was_connected)

    def test_trace_adapter_uses_observed_values_without_inventing_missing_values(self):
        entry = self._entry(
            "BUY",
            0,
            trace={
                "conditions": [
                    {
                        "path": "buy.groups[0].conditions[0]",
                        "condition_type": "RSI",
                        "operator": "<=",
                        "left_operand": {"value": 51.2},
                        "right_operand": {"value": 45},
                        "raw_result": False,
                        "final_result": False,
                    },
                    {
                        "path": "buy.groups[0].conditions[1]",
                        "condition_type": "DISABLED",
                        "operator": "DISABLED",
                        "left_operand": {"value": None, "reason": "condition disabled"},
                        "right_operand": {"value": None, "reason": "condition disabled"},
                        "raw_result": True,
                        "final_result": True,
                    },
                ],
                "groups": [{
                    "path": "buy.groups[0]",
                    "group_name": "A",
                    "enabled": True,
                    "logic": "AND",
                    "result": False,
                }],
                "aggregations": [{
                    "side": "BUY",
                    "payload": {"logic": "OR", "result": False, "matched_group_paths": []},
                }],
            },
            details=[
                "filter_type=RSI enabled=True period=14 operator=<= threshold=45 evaluated_value=None passed=False reason=insufficient_data evaluation_index=0"
            ],
        )
        rows = filter_rows_for_entry(entry)
        rsi_condition = rows[0]
        self.assertEqual(("51.2", "45", "<=", "FAIL"), (
            rsi_condition.actual,
            rsi_condition.comparison,
            rsi_condition.operation,
            rsi_condition.result,
        ))
        self.assertEqual("-", rsi_condition.reason)
        self.assertEqual("condition disabled", rows[1].reason)
        detail = next(row for row in rows if row.label == "RSI" and row.reason == "insufficient_data")
        self.assertEqual("-", detail.actual)
        self.assertEqual("45", detail.comparison)
        self.assertEqual("FAIL", detail.result)

    def test_candle_selection_updates_summary_and_filter_table_for_same_index(self):
        first = self._entry(
            "BUY",
            0,
            trace={"conditions": [{
                "path": "buy.groups[0].conditions[0]",
                "condition_type": "FIRST",
                "operator": ">",
                "left_operand": {"value": 1},
                "right_operand": {"value": 2},
                "final_result": False,
            }], "groups": [], "aggregations": []},
        )
        second = self._entry(
            "SELL",
            1,
            signal="SELL",
            reason="second-reason",
            trace={"conditions": [{
                "path": "sell.groups[0].conditions[0]",
                "condition_type": "SECOND",
                "operator": "TURN_DOWN",
                "left_operand": {"value": 3},
                "right_operand": {"value": None},
                "final_result": True,
            }], "groups": [], "aggregations": []},
        )
        window = self._window()
        window.set_replay_snapshot(self._snapshot([first, second]))
        self.assertIn("SECOND", self._table_text(window))
        self.assertIn("SELL reason: second-reason", window.selection_summary.toPlainText())
        window.select_evaluation_index(0)
        self.assertIn("FIRST", self._table_text(window))
        self.assertNotIn("SECOND", self._table_text(window))
        self.assertIn("종가: 101", window.selection_summary.toPlainText())

    @staticmethod
    def _table_text(window):
        return " ".join(
            window.filter_result_table.item(row, column).text()
            for row in range(window.filter_result_table.rowCount())
            for column in range(window.filter_result_table.columnCount())
            if window.filter_result_table.item(row, column) is not None
        )

    def test_apply_payload_changes_only_source_signal_whitelist_in_registration_and_edit(self):
        for mode in ("registration", "edit"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp_dir:
                rules_path = Path(temp_dir) / "rules.json"
                rules_path.write_text(json.dumps(self.rules, ensure_ascii=False), encoding="utf-8")
                kwargs = dict(
                    rules_path=rules_path,
                    routine_path=self.routine_dir,
                    routine_name="지표추종매매",
                    definition_id="indicator_follow",
                    settings_mode=mode,
                )
                if mode == "edit":
                    kwargs["instance_id"] = "INSTANCE-APPLY"
                with patch.object(dialog_module.QTimer, "singleShot"):
                    source = dialog_module.IndicatorFollowRoutineSettingsDialog(**kwargs)
                    window = IndicatorFollowSignalValidationWindow(self.stock, self._seed())
                self.widgets.extend([source, window])
                before = source.collect_indicator_follow_ui_state()
                file_before = rules_path.read_bytes()

                window.basic_signal_interval_combo.setCurrentText("15")
                window.historical_candle_count_spin.setValue(777)
                window.buy_signal_expr_line.setText("A or D")
                window.buy_rsi_value_line.setText("39")
                window.sell_signal_condition_a_rsi_value_line.setText("57")
                payload = IndicatorFollowSignalValidationApplyPayload(
                    window.collect_indicator_follow_ui_state()
                )
                self.assertNotIn("777", json.dumps(payload.to_ui_state()))
                self.assertEqual(before, source.collect_indicator_follow_ui_state())
                result = source.apply_signal_validation_ui_state(payload.to_ui_state())
                self.assertEqual([], result["skipped"])
                after = source.collect_indicator_follow_ui_state()

                self.assertEqual("15", after["basic"]["basic_signal_interval_combo"])
                self.assertEqual("A or D", after["basic"]["buy_signal_expr_line"])
                self.assertEqual("39", after["buy_ui"]["signal_filter"]["buy_rsi_value_line"])
                self.assertEqual("57", after["sell_ui"]["signal_conditions"]["condition_a"]["rsi_value_line"])
                self.assertEqual(before["buy_ui"] | {"signal_filter": after["buy_ui"]["signal_filter"]}, after["buy_ui"])
                self.assertEqual(before["sell_ui"] | {"signal_conditions": after["sell_ui"]["signal_conditions"]}, after["sell_ui"])
                self.assertEqual(
                    before["basic"]["basic_duplicate_signal_combo"],
                    after["basic"]["basic_duplicate_signal_combo"],
                )
                self.assertEqual(
                    before["basic"]["basic_error_policy_combo"],
                    after["basic"]["basic_error_policy_combo"],
                )
                self.assertEqual(file_before, rules_path.read_bytes())

    def test_flow_apply_uses_source_weakref_and_closed_source_fails_closed(self):
        created = []

        def factory(stock, seed, parent=None):
            window = _AutoWindow(stock, seed, parent)
            self.widgets.append(window)
            created.append(window)
            return window

        flow = IndicatorFollowSignalValidationFlow(
            _ConnectedBroker(),
            host=_Host(self.stock),
            window_factory=factory,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "rules.json"
            path.write_text(json.dumps(self.rules, ensure_ascii=False), encoding="utf-8")
            with patch.object(dialog_module.QTimer, "singleShot"):
                source = dialog_module.IndicatorFollowRoutineSettingsDialog(
                    rules_path=path,
                    routine_path=self.routine_dir,
                    routine_name="지표추종매매",
                    definition_id="indicator_follow",
                    settings_mode="registration",
                )
            self.widgets.append(source)
            flow.bind_dialog(source)
            with patch.object(flow_module.QTimer, "singleShot"):
                source.signal_validation_requested.emit(self._seed())
            window = created[0]
            state = deepcopy(self.ui_state)
            state["basic"]["buy_signal_expr_line"] = "B or C"
            payload = IndicatorFollowSignalValidationApplyPayload(state)
            window.settings_apply_requested.emit(payload)
            self.assertEqual("B or C", source.buy_signal_expr_line.text())
            self.assertEqual(("설정 반영 완료", True), window.apply_results[-1])
            self.assertIn(window, flow.open_windows)

            before = source.collect_indicator_follow_ui_state()
            flow._apply_to_source(window, lambda: None, payload)
            self.assertEqual(before, source.collect_indicator_follow_ui_state())
            self.assertEqual(
                ("원본 설정창이 닫혀 있어 설정을 반영할 수 없습니다.", False),
                window.apply_results[-1],
            )

    def test_projection_excludes_runtime_policy_and_non_signal_basic_values(self):
        rules = deepcopy(self.rules)
        rules["signal_runtime_policy"] = {
            "duplicate_priority": "TRAILING",
            "error_policy": "STOP_AND_REVIEW",
        }
        projected = project_signal_validation_rules(rules, ui_state=self.ui_state)
        self.assertNotIn("signal_runtime_policy", projected)
        basic = projected["indicator_follow_ui_state"]["state"]["basic"]
        self.assertNotIn("basic_duplicate_signal_combo", basic)
        self.assertNotIn("basic_error_policy_combo", basic)


if __name__ == "__main__":
    unittest.main()
