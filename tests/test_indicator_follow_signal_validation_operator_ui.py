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

from PyQt5.QtCore import QObject, Qt, pyqtSignal
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import (
    QApplication,
    QAbstractSpinBox,
    QComboBox,
    QDialog,
    QLabel,
    QScrollArea,
)

import gui_indicator_follow_routine_settings_dialog as dialog_module
import gui_indicator_follow_signal_validation_flow as flow_module
from gui_indicator_follow_signal_validation_flow import IndicatorFollowSignalValidationFlow
from gui_indicator_follow_signal_validation_window import IndicatorFollowSignalValidationWindow
from indicator_follow_signal_validation_presentation import filter_rows_for_entry, _trace_status
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

    def preflight_block_reason(self, snapshot):
        return None

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
    stock_selection_requested = pyqtSignal()
    recent_stock_selected = pyqtSignal(object)

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

    def set_validation_stock(self, stock):
        if stock == self.stock:
            return False
        self.stock = stock
        return True

    def request_validation(self):
        return None

    def set_recent_stocks(self, _stocks):
        pass

    def set_stock_metadata(self, _metadata):
        pass


class _MemoryRecentStockStore:
    recent_stocks = ()

    def metadata_for(self, _stock):
        return None

    def activate(self, stock):
        updated = (stock,) + tuple(
            candidate for candidate in self.recent_stocks
            if candidate.code != stock.code
        )
        changed = updated != self.recent_stocks
        self.recent_stocks = updated
        return changed


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
        resolved_state = deepcopy(ui_state or self.ui_state)
        for group_name in ("condition_a", "condition_b", "condition_c"):
            group = resolved_state["sell_ui"]["signal_conditions"][group_name]
            group["gap_left_combo"] = "평단가"
            group["gap_right_combo"] = "현재가"
        return IndicatorFollowSignalValidationSeed(
            ValidationSettingsSnapshot(self.rules),
            resolved_state,
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
        self.assertIsInstance(window.compact_stock_label, QLabel)
        self.assertNotIsInstance(window.compact_stock_label, QComboBox)
        self.assertFalse(hasattr(window, "compact_stock_selector"))
        self.assertEqual("▶ 기본설정", window.compact_header_arrow.text())
        self.assertFalse(window.recent_stock_row.isVisible())
        self.assertEqual([], window.compact_stock_display.findChildren(QComboBox))
        self.assertEqual("5", window.basic_signal_interval_combo.currentText())
        self.assertEqual(100, window.historical_candle_count_spin.value())
        self.assertEqual(
            QAbstractSpinBox.NoButtons,
            window.historical_candle_count_spin.buttonSymbols(),
        )
        window.historical_candle_count_spin.lineEdit().setText("500")
        window.historical_candle_count_spin.interpretText()
        self.assertEqual(500, window.historical_candle_count_spin.value())
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
        for forbidden_text in ("중복신호처리", "오류발생"):
            self.assertNotIn(forbidden_text, all_labels)
        self.assertEqual(
            1,
            sum(
                button.text().endswith("기본설정")
                for button in window.findChildren(dialog_module.QPushButton)
            ),
        )
        self.assertEqual([], window.control_tab.findChildren(QScrollArea))

        window._show_with_initial_control_section_state()
        self.assertEqual("summary", window._control_section_mode)
        self.assertFalse(window.buy_detail_expanded)
        self.assertFalse(window.sell_detail_expanded)
        self.assertTrue(window.basic_box.isVisible())
        self.assertTrue(window.buy_box.isVisible())
        self.assertTrue(window.sell_box.isVisible())
        self.assertEqual("▶ 기본설정", window.basic_toggle_button.text())
        self.assertEqual("▶ 매수설정", window.buy_title.text())
        self.assertEqual("▶ 매도설정", window.sell_title.text())
        self.assertFalse(window.buy_detail_widget.isVisible())
        self.assertFalse(window.sell_detail_widget.isVisible())
        window._toggle_control_section_mode("sell")
        self.assertFalse(window.buy_detail_expanded)
        self.assertTrue(window.sell_detail_expanded)
        window._toggle_control_section_mode("buy")
        self.assertTrue(window.buy_detail_expanded)
        self.assertTrue(window.sell_detail_expanded)

    def test_recent_row_toggles_inline_as_exactly_one_line(self):
        window = self._window()
        stocks = tuple(
            ValidationStockRef(f"{index:06d}", f"종목{index}")
            for index in range(1, 8)
        )
        window.set_recent_stocks(stocks)
        window.show()
        self.app.processEvents()
        window._fit_signal_validation_window()
        self.app.processEvents()
        before_size = window.size()
        before_header_height = window.basic_box.height()
        before_buy_position = window.buy_box.pos()
        before_sell_position = window.sell_box.pos()
        selection_requests = []
        window.stock_selection_requested.connect(
            lambda: selection_requests.append(True)
        )

        QTest.mouseClick(window.compact_header_arrow, Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual("▼ 기본설정", window.compact_header_arrow.text())
        self.assertTrue(window.recent_stock_row.isVisible())
        self.assertTrue(window.stock_selection_button.isVisible())
        self.assertGreater(window.basic_box.height(), before_header_height)
        self.assertGreater(window.buy_box.pos().y(), before_buy_position.y())
        self.assertGreater(window.sell_box.pos().y(), before_sell_position.y())
        self.assertGreaterEqual(window.height(), before_size.height())
        self.assertEqual(
            window.recent_stock_row.minimumHeight(),
            window.recent_stock_row.maximumHeight(),
        )
        self.assertEqual([], window.recent_stock_row.findChildren(QScrollArea))
        self.assertEqual(
            1,
            len({button.pos().y() for button in window.recent_stock_row.stock_buttons}),
        )
        panel_margins = window.recent_stock_panel_layout.contentsMargins()
        self.assertGreater(panel_margins.left(), 0)
        self.assertGreater(panel_margins.top(), 0)
        self.assertGreater(panel_margins.right(), 0)
        self.assertGreater(panel_margins.bottom(), 0)
        select_center_y = window.stock_selection_button.mapTo(
            window.recent_stock_panel,
            window.stock_selection_button.rect().center(),
        ).y()
        recent_center_y = window.recent_stock_row.mapTo(
            window.recent_stock_panel,
            window.recent_stock_row.rect().center(),
        ).y()
        self.assertLessEqual(abs(select_center_y - recent_center_y), 1)
        select_right_x = window.stock_selection_button.mapTo(
            window.basic_box,
            window.stock_selection_button.rect().topRight(),
        ).x()
        basic_center_x = window.basic_toggle_button.mapTo(
            window.basic_box,
            window.basic_toggle_button.rect().center(),
        ).x()
        self.assertEqual(basic_center_x, select_right_x)
        self.assertGreater(
            window.stock_selection_button.mapTo(
                window.basic_box,
                window.stock_selection_button.rect().topLeft(),
            ).y(),
            window.basic_box.rect().top(),
        )
        QTest.mouseClick(window.stock_selection_button, Qt.LeftButton)
        self.assertEqual([True], selection_requests)

        QTest.mouseClick(window.compact_header_arrow, Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual("▶ 기본설정", window.compact_header_arrow.text())
        self.assertFalse(window.recent_stock_row.isVisible())
        self.assertEqual(before_header_height, window.basic_box.height())
        self.assertEqual(before_buy_position, window.buy_box.pos())
        self.assertEqual(before_sell_position, window.sell_box.pos())
        self.assertEqual(before_size.height(), window.height())

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
            recent_stock_store=_MemoryRecentStockStore(),
        )
        flow._last_selected_stock = self.stock
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

    def test_sell_operand_source_distinguishes_unary_and_missing_data(self):
        for operator, left, right, passed, expected in (
            ("TURN_UP", 88, None, True, "통과"),
            ("TURN_DOWN", 88, None, False, "실패"),
            ("TURN_UP", None, None, False, "데이터부족"),
            ("PERCENT_GAP", 10030, 10000, True, "통과"),
            ("PERCENT_GAP", 10020, 10000, False, "실패"),
            ("PERCENT_GAP", 10020, None, False, "데이터부족"),
        ):
            with self.subTest(operator=operator, left=left, right=right):
                payload = {
                    "operator": operator,
                    "left_operand": {"source": "indicator", "value": left},
                    "right_operand": {
                        "source": "none" if operator.startswith("TURN_") else "indicator",
                        "value": right,
                    },
                    "final_result": passed,
                }
                self.assertEqual(expected, _trace_status([payload]))

    def test_sell_ocr_transition_and_threshold_keep_observed_result(self):
        for passed in (True, False):
            with self.subTest(passed=passed):
                conditions = [
                    {
                        "path": f"sell.signals.ui_condition_a.groups[0].conditions[{index}]",
                        "expression_id": f"OCR_{index}",
                        "operator": operator,
                        "left_operand": {"source": "indicator", "value": 88},
                        "right_operand": {"source": source, "value": value},
                        "final_result": passed if index == 0 else True,
                    }
                    for index, (operator, source, value) in enumerate((
                        ("TURN_DOWN", "none", None), ("<=", "literal", 91),
                    ))
                ]
                entry = self._entry("SELL", 0, trace={"conditions": conditions})
                rows = filter_rows_for_entry(entry, self.ui_state)
                row = next(row for row in rows if row.condition == "A·OCR")
                self.assertEqual("통과" if passed else "실패", row.result)
                self.assertIn("전환", row.actual)
                self.assertIn("임계값", row.actual)

    def test_buy_detail_status_and_partial_values_preserve_evidence(self):
        for reason, enabled, passed, expected in (
            (None, "True", "False", "미평가"),
            ("disabled", "True", "True", "미사용"),
            ("matched", "False", "True", "미사용"),
            ("insufficient_data", "True", "False", "데이터부족"),
            ("matched", "True", "True", "통과"),
            ("not_matched", "True", "False", "실패"),
        ):
            with self.subTest(reason=reason, enabled=enabled):
                details = [] if reason is None else [
                    f"filter_type={kind} enabled={enabled} passed={passed} reason={reason} "
                    "current_value=70000 ma_value=None"
                    for kind in ("OCR", "BOLLINGER", "MOVING_AVERAGE", "RSI")
                ]
                entry = self._entry("BUY", 0, details=details)
                rows = filter_rows_for_entry(entry, self.ui_state)
                filters = [row for row in rows if row.row_kind == "condition"]
                self.assertEqual([expected] * 4, [row.result for row in filters])
                if reason == "insufficient_data":
                    self.assertEqual("현재가 70000 / 이평 -", filters[2].actual)
                self.assertEqual("미발생", rows[-1].result)

    def test_trace_adapter_uses_observed_values_without_inventing_missing_values(self):
        buy_entry = self._entry(
            "BUY",
            0,
            signal="BUY",
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
                "filter_type=OCR enabled=True logic=AND passed=True reason=matched evaluation_index=0 condition_details=PASS_OSC_TURN_UP",
                "filter_type=BOLLINGER enabled=True operator=<= value=0.1 close_price=88 bollinger_value=90 passed=False reason=not_matched evaluation_index=0",
                "filter_type=MOVING_AVERAGE enabled=True operator=CROSS_UP current_value=101 ma_value=100 passed=True reason=matched evaluation_index=0",
                "filter_type=RSI enabled=True period=14 operator=<= threshold=45 evaluated_value=None passed=False reason=insufficient_data evaluation_index=0",
            ],
        )
        buy_rows = filter_rows_for_entry(buy_entry, self.ui_state)
        buy_conditions = [row for row in buy_rows if row.row_kind == "condition"]
        self.assertEqual(list("ABCD"), [row.condition for row in buy_conditions])
        self.assertEqual(["통과", "실패", "통과", "데이터부족"], [row.result for row in buy_conditions])
        self.assertIn("상승전환", buy_conditions[0].actual)
        self.assertEqual("-", buy_conditions[3].actual)
        self.assertNotIn("실패 조건", [row.condition for row in buy_rows])

        sell_state = deepcopy(self.ui_state)
        sell_state["sell_ui"]["signal_conditions"]["condition_c"]["macd_check"] = False
        sell_state["sell_ui"]["signal_conditions"]["condition_c"]["array_check"] = False
        sell_entry = self._entry(
            "SELL",
            0,
            trace={
                "conditions": [
                    {
                        "path": "sell.signals.ui_condition_a.groups[0].conditions[0]",
                        "expression_id": "OCR_0",
                        "condition_type": "OSC",
                        "operator": "TURN_DOWN",
                        "left_operand": {"value": 3},
                        "right_operand": {"value": None},
                        "final_result": True,
                    },
                    {
                        "path": "sell.signals.ui_condition_b.groups[0].conditions[0]",
                        "expression_id": "PRICE_BOX_0",
                        "condition_type": "MA",
                        "operator": "CROSS_UP",
                        "left_operand": {"value": 101},
                        "right_operand": {"value": 100},
                        "final_result": False,
                    },
                    {
                        "path": "sell.signals.ui_condition_c.groups[0].conditions[0]",
                        "expression_id": "MACD_0",
                        "condition_type": "MACD",
                        "operator": "CROSS_DOWN",
                        "left_operand": {"value": -1},
                        "right_operand": {"value": 0},
                        "final_result": True,
                    },
                ],
                "groups": [
                    {"path": "sell.signals.ui_condition_a.groups[0]", "result": True},
                    {"path": "sell.signals.ui_condition_b.groups[0]", "result": False},
                    {"path": "sell.signals.ui_condition_c.groups[0]", "result": True},
                ],
                "aggregations": [{"side": "SELL", "payload": {"result": False}}],
            },
        )
        sell_rows = filter_rows_for_entry(sell_entry, sell_state)
        sell_conditions = [row for row in sell_rows if row.row_kind == "condition"]
        self.assertEqual(
            [
                "A·OCR", "A·가격비교", "A·RSI",
                "B·가격박스", "B·볼린저밴드", "B·가격비교",
                "C·가격비교", "C·MACD", "C·이평배열",
            ],
            [row.condition for row in sell_conditions],
        )
        self.assertIn("하락전환", sell_conditions[0].actual)
        self.assertIn("상향돌파", sell_conditions[3].actual)
        self.assertEqual("미사용", sell_conditions[7].result)
        visible_text = " ".join(cell for row in buy_rows + sell_rows for cell in row.to_cells())
        self.assertNotIn("buy.groups", visible_text)
        self.assertNotIn("sell.signals", visible_text)
        self.assertNotIn("TURN_", visible_text)
        self.assertNotIn("CROSS_", visible_text)

    def test_candle_selection_updates_summary_and_filter_table_for_same_index(self):
        first = self._entry(
            "BUY",
            0,
            details=[
                "filter_type=RSI enabled=True period=14 operator=<= threshold=45 evaluated_value=51.2 passed=False reason=not_matched evaluation_index=0"
            ],
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
                "path": "sell.signals.ui_condition_c.groups[0].conditions[0]",
                "condition_type": "SECOND",
                "operator": "TURN_DOWN",
                "left_operand": {"value": 3},
                "right_operand": {"value": None},
                "final_result": True,
            }], "groups": [], "aggregations": []},
        )
        window = self._window()
        window.set_replay_snapshot(self._snapshot([first, second]))
        self.assertIn("하락전환", self._table_text(window))
        self.assertIn("SELL reason: second-reason", window.selection_summary.toPlainText())
        window.select_evaluation_index(0)
        self.assertIn("51.2", self._table_text(window))
        self.assertNotIn("하락전환", self._table_text(window))
        self.assertIn("종가: 101", window.selection_summary.toPlainText())

    def test_operator_table_grows_to_all_rows_without_vertical_scroll(self):
        buy = self._entry(
            "BUY",
            1,
            details=[
                "filter_type=OCR enabled=True passed=True reason=matched evaluation_index=1 condition_details=PASS_OSC_TURN_UP",
                "filter_type=BOLLINGER enabled=True close_price=101 bollinger_value=102 passed=False reason=not_matched evaluation_index=1",
                "filter_type=MOVING_AVERAGE enabled=True current_value=101 ma_value=100 passed=True reason=matched evaluation_index=1",
                "filter_type=RSI enabled=True evaluated_value=52 passed=False reason=not_matched evaluation_index=1",
            ],
        )
        sell = self._entry(
            "SELL",
            1,
            trace={
                "conditions": [
                    {
                        "path": f"sell.signals.ui_condition_{letter.lower()}.groups[0].conditions[0]",
                        "operator": "TURN_DOWN",
                        "left_operand": {"value": index},
                        "right_operand": {"value": None},
                        "final_result": letter != "B",
                    }
                    for index, letter in enumerate("ABC", 1)
                ],
                "groups": [
                    {
                        "path": f"sell.signals.ui_condition_{letter.lower()}.groups[0]",
                        "result": letter != "B",
                    }
                    for letter in "ABC"
                ],
                "aggregations": [],
            },
        )
        window = self._window()
        window.show()
        window._populate_filter_result_table(buy, None)
        buy_only_height = window.filter_result_table.height()
        buy_only_window_hint = window._required_signal_validation_window_height()
        window._populate_filter_result_table(buy, sell)
        full_height = window.filter_result_table.height()
        full_window_hint = window._required_signal_validation_window_height()
        self.assertGreater(full_height, buy_only_height)
        self.assertGreater(full_window_hint, buy_only_window_hint)
        window.set_replay_snapshot(self._snapshot([buy, sell]))
        self.app.processEvents()
        table = window.filter_result_table
        self.assertEqual(Qt.ScrollBarAlwaysOff, table.verticalScrollBarPolicy())
        self.assertEqual(20, table.rowCount())
        expected_height = (
            table.horizontalHeader().height()
            + sum(table.rowHeight(row) for row in range(table.rowCount()))
            + table.frameWidth() * 2
            + table.contentsMargins().top()
            + table.contentsMargins().bottom()
            + 2
        )
        self.assertEqual(expected_height, table.height())
        self.assertGreater(table.height(), 190)
        last_rect = table.visualRect(table.model().index(table.rowCount() - 1, 0))
        self.assertLessEqual(last_rect.bottom(), table.viewport().height())
        self.assertGreaterEqual(window.result_splitter.height(), window.result_splitter.minimumHeight())

    def test_initial_and_changed_candle_counts_drive_requests_but_not_apply(self):
        window = self._window()
        initial = window.request_initial_validation()
        self.assertEqual(100, initial.candle_count)
        window.historical_candle_count_spin.setValue(500)
        changed = window._request_validation()
        self.assertEqual(500, changed.candle_count)
        payload = window._request_settings_apply()
        self.assertNotIn("candle_count", json.dumps(payload.to_ui_state()))
        self.assertNotIn("500", json.dumps(payload.to_ui_state()))

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
                window.sell_signal_condition_a_gap_left_combo.setCurrentText("현재가")
                window.sell_signal_condition_a_gap_right_combo.setCurrentText("평단가")
                window.sell_signal_condition_a_gap_direction_combo.setCurrentText("하향")
                window.sell_signal_condition_a_gap_value_line.setText("0.75")
                window.sell_signal_condition_a_gap_compare_combo.setCurrentText("이상")
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
                sell_a = after["sell_ui"]["signal_conditions"]["condition_a"]
                self.assertEqual("현재가", sell_a["gap_left_combo"])
                self.assertEqual("평단가", sell_a["gap_right_combo"])
                self.assertEqual("하향", sell_a["gap_direction_combo"])
                self.assertEqual("0.75", sell_a["gap_value_line"])
                self.assertEqual("이상", sell_a["gap_compare_combo"])
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
            recent_stock_store=_MemoryRecentStockStore(),
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
            state = self._seed().to_ui_state()
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
        projected = project_signal_validation_rules(
            rules,
            ui_state=self._seed().to_ui_state(),
        )
        self.assertNotIn("signal_runtime_policy", projected)
        basic = projected["indicator_follow_ui_state"]["state"]["basic"]
        self.assertNotIn("basic_duplicate_signal_combo", basic)
        self.assertNotIn("basic_error_policy_combo", basic)


if __name__ == "__main__":
    unittest.main()
