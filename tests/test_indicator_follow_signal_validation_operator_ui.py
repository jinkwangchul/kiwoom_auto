# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest
import weakref
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QObject, QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QFontMetrics
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
import gui_indicator_follow_signal_validation_window as validation_window_module
from gui_indicator_follow_signal_validation_flow import IndicatorFollowSignalValidationFlow
from gui_indicator_follow_signal_validation_window import (
    IndicatorFollowSignalValidationWindow,
)
from indicator_follow_signal_validation_presentation import (
    _trace_status,
    filter_rows_for_entry,
    signal_evidence_lines_for_entry,
    signal_evidence_tooltip,
)
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
        cls.ui_state["buy_ui"]["signal_filter"]["buy_bollinger_sign_combo"] = "-"
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
            0,
            sum(
                button.text().endswith("기본설정")
                for button in window.findChildren(dialog_module.QPushButton)
            ),
        )
        self.assertEqual(
            1,
            sum(
                label.text().endswith("기본설정")
                for label in window.findChildren(QLabel)
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

    def test_collapsed_section_geometry_and_initial_available_ratio_are_normalized(self):
        window = self._window()
        window._available_signal_validation_geometry = lambda: QRect(0, 0, 2400, 1400)
        window.show()
        self.app.processEvents()
        window._apply_control_section_mode("summary", force=True)
        window._initial_natural_fit_pending = True
        window._initial_geometry_committed = False
        window._fit_signal_validation_window()
        self.app.processEvents()

        sections = (window.basic_box, window.buy_box, window.sell_box)
        headers = (
            window.basic_header_widget,
            window.buy_header_widget,
            window.sell_header_widget,
        )
        collapsed_height = window._v2_collapsed_section_height
        self.assertEqual([collapsed_height] * 3, [box.height() for box in sections])
        self.assertEqual(
            [window._V2_SECTION_HEADER_HEIGHT] * 3,
            [header.height() for header in headers],
        )
        lefts = [box.geometry().left() for box in sections]
        rights = [box.geometry().right() for box in sections]
        self.assertLessEqual(max(lefts) - min(lefts), 1)
        self.assertLessEqual(max(rights) - min(rights), 1)
        self.assertLessEqual(abs(window.frameGeometry().width() - 2040), 2)
        self.assertLessEqual(abs(window.frameGeometry().height() - 1190), 2)
        self.assertTrue(window._initial_geometry_committed)
        self.assertFalse(window._user_geometry_owned)

        window._toggle_recent_stock_row()
        self.app.processEvents()
        self.assertGreater(window.basic_box.height(), collapsed_height)
        self.assertEqual(
            [window._V2_SECTION_HEADER_HEIGHT] * 3,
            [header.height() for header in headers],
        )
        window._toggle_recent_stock_row()
        self.app.processEvents()
        self.assertEqual([collapsed_height] * 3, [box.height() for box in sections])

        for mode, expanded_box in (
            ("buy", window.buy_box),
            ("sell", window.sell_box),
        ):
            window._apply_control_section_mode(mode, force=True)
            self.app.processEvents()
            self.assertGreater(expanded_box.height(), collapsed_height)
            self.assertEqual(
                [window._V2_SECTION_HEADER_HEIGHT] * 3,
                [header.height() for header in headers],
            )
            window._apply_control_section_mode("summary", force=True)
            self.app.processEvents()
            self.assertEqual([collapsed_height] * 3, [box.height() for box in sections])

        user_width = window.width() + 180
        user_position = QPoint(137, 193)
        window.resize(user_width, window.height())
        window.move(user_position)
        window._user_geometry_owned = True
        window._toggle_control_section_mode("buy")
        window._fit_signal_validation_window()
        self.app.processEvents()
        self.assertEqual(user_width, window.width())
        self.assertEqual(user_position, window.pos())

    def test_initial_center_runs_once_and_section_toggles_preserve_window_position(self):
        window = self._window()
        window._available_signal_validation_geometry = lambda: QRect(0, 0, 2400, 1400)
        center_calls = []
        window._center_on_initial_screen = lambda: center_calls.append(window.pos())
        window._initial_natural_fit_pending = True
        window._fit_signal_validation_window()
        window._fit_signal_validation_window()
        self.assertEqual(1, len(center_calls))

        window.show()
        self.app.processEvents()
        window.move(QPoint(173, 211))
        self.app.processEvents()

        for toggle in (
            window._toggle_recent_stock_row,
            window._toggle_recent_stock_row,
            lambda: window._toggle_control_section_mode("buy"),
            lambda: window._toggle_control_section_mode("buy"),
            lambda: window._toggle_control_section_mode("sell"),
            lambda: window._toggle_control_section_mode("sell"),
        ):
            before_position = window.pos()
            toggle()
            self.app.processEvents()
            self.assertEqual(before_position, window.pos())

    def test_resize_ownership_ignores_programmatic_and_records_native_user_resize(self):
        window = self._window()

        class ResizeObservation:
            def __init__(self, spontaneous):
                self._spontaneous = spontaneous

            def spontaneous(self):
                return self._spontaneous

        window._initial_geometry_committed = True
        window._programmatic_resize_in_progress = True
        window._record_signal_validation_resize_ownership(ResizeObservation(True))
        self.assertFalse(window._user_geometry_owned)

        window._programmatic_resize_in_progress = False
        window._record_signal_validation_resize_ownership(ResizeObservation(False))
        self.assertFalse(window._user_geometry_owned)

        window._record_signal_validation_resize_ownership(ResizeObservation(True))
        self.assertTrue(window._user_geometry_owned)

    def test_header_internal_alignment_separators_and_stock_button_are_normalized(self):
        window = self._window()
        window.show()
        self.app.processEvents()

        titles = (
            window.basic_toggle_button,
            window.buy_title,
            window.sell_title,
        )
        headers = (
            window.basic_header_widget,
            window.buy_header_widget,
            window.sell_header_widget,
        )
        separators = (
            window.basic_header_separator,
            window.buy_header_separator,
            window.sell_header_separator,
        )
        self.assertIsInstance(window.basic_toggle_button, QLabel)
        self.assertNotIsInstance(window.basic_toggle_button, dialog_module.QPushButton)
        self.assertEqual([132] * 3, [item.minimumWidth() for item in titles])
        self.assertEqual([30] * 3, [item.height() for item in titles])
        self.assertEqual(1, len({item.width() for item in titles}))
        self.assertEqual(
            [
                "font-size: 13pt; font-weight: bold; color: #2E6B3A; padding: 0px 5px; border: 1px solid #000000; border-radius: 2px; background: transparent;",
                "font-size: 13pt; font-weight: bold; color: #1565C0; padding: 0px 5px; border: 1px solid #000000; border-radius: 2px; background: transparent;",
                "font-size: 13pt; font-weight: bold; color: #C62828; padding: 0px 5px; border: 1px solid #000000; border-radius: 2px; background: transparent;",
            ],
            [item.styleSheet() for item in titles],
        )
        self.assertEqual(
            ["▶ 기본설정", "▶ 매수설정", "▶ 매도설정"],
            [item.text() for item in titles],
        )
        for title in titles:
            self.assertLessEqual(
                QFontMetrics(title.font()).horizontalAdvance(title.text()),
                title.contentsRect().width(),
            )
        title_center_ys = [
            title.mapTo(header, title.rect().center()).y()
            for title, header in zip(titles, headers)
        ]
        self.assertLessEqual(max(title_center_ys) - min(title_center_ys), 1)

        self.assertEqual(
            [(12, 30)] * 3,
            [(item.width(), item.height()) for item in separators],
        )
        self.assertEqual(["|"] * 3, [item.text() for item in separators])
        self.assertEqual(1, len({item.styleSheet() for item in separators}))
        separator_center_xs = [
            separator.mapTo(box, separator.rect().center()).x()
            for separator, box in zip(
                separators,
                (window.basic_box, window.buy_box, window.sell_box),
            )
        ]
        separator_center_ys = [
            separator.mapTo(header, separator.rect().center()).y()
            for separator, header in zip(separators, headers)
        ]
        self.assertLessEqual(max(separator_center_xs) - min(separator_center_xs), 1)
        self.assertLessEqual(max(separator_center_ys) - min(separator_center_ys), 1)

        following_widgets = tuple(
            header.layout().itemAt(2).widget() for header in headers
        )
        following_start_xs = [
            widget.mapTo(box, widget.rect().topLeft()).x()
            for widget, box in zip(
                following_widgets,
                (window.basic_box, window.buy_box, window.sell_box),
            )
        ]
        self.assertLessEqual(max(following_start_xs) - min(following_start_xs), 1)
        header_center_ys = [header.rect().center().y() for header in headers]
        boxes = (window.basic_box, window.buy_box, window.sell_box)
        self.assertEqual(
            [(1, 1, 1, 1)] * 3,
            [
                (
                    box.contentsMargins().left(),
                    box.contentsMargins().top(),
                    box.contentsMargins().right(),
                    box.contentsMargins().bottom(),
                )
                for box in boxes
            ],
        )
        box_header_center_deltas = []
        for box, header in zip(boxes, headers):
            expected_center_y = header.rect().center().y()
            header_widgets = [
                header.layout().itemAt(index).widget()
                for index in range(header.layout().count())
                if header.layout().itemAt(index).widget() is not None
            ]
            for widget in header_widgets:
                actual_center_y = widget.mapTo(header, widget.rect().center()).y()
                self.assertLessEqual(abs(actual_center_y - expected_center_y), 1)
            mapped_header_center_y = header.mapTo(
                box,
                header.rect().center(),
            ).y()
            box_header_center_deltas.append(
                mapped_header_center_y - box.rect().center().y()
            )
        self.assertLessEqual(
            max(box_header_center_deltas) - min(box_header_center_deltas),
            1,
        )
        self.assertTrue(
            all(abs(delta) <= 1 for delta in box_header_center_deltas),
            box_header_center_deltas,
        )
        compact_stock_center_y = window.compact_stock_label.mapTo(
            window.basic_header_widget,
            window.compact_stock_label.rect().center(),
        ).y()
        self.assertLessEqual(
            abs(compact_stock_center_y - window.basic_header_widget.rect().center().y()),
            1,
        )
        self.assertLessEqual(max(header_center_ys) - min(header_center_ys), 1)

        button_style = window.stock_selection_button.styleSheet()
        self.assertIn("border: 1px solid", button_style)
        self.assertIn("background:", button_style)
        self.assertIn(":hover", button_style)
        self.assertIn(":pressed", button_style)
        self.assertEqual(26, window.stock_selection_button.height())

        window._apply_control_section_mode("all", force=True)
        window._toggle_recent_stock_row()
        self.app.processEvents()
        self.assertEqual(
            ["▼ 기본설정", "▼ 매수설정", "▼ 매도설정"],
            [item.text() for item in titles],
        )
        self.assertEqual(1, len({item.width() for item in titles}))
        for title in titles:
            self.assertLessEqual(
                QFontMetrics(title.font()).horizontalAdvance(title.text()),
                title.contentsRect().width(),
            )
        self.assertEqual("종목선택", window.stock_selection_button.text())
        self.assertGreaterEqual(
            window.stock_selection_button.width(),
            window.stock_selection_button.sizeHint().width(),
        )
        self.assertLessEqual(
            QFontMetrics(window.stock_selection_button.font()).horizontalAdvance(
                window.stock_selection_button.text()
            ),
            window.stock_selection_button.contentsRect().width(),
        )
        select_right_x = window.stock_selection_button.mapTo(
            window.basic_box,
            window.stock_selection_button.rect().topRight(),
        ).x()
        basic_center_x = window.basic_toggle_button.mapTo(
            window.basic_box,
            window.basic_toggle_button.rect().center(),
        ).x()
        self.assertEqual(basic_center_x, select_right_x)

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
        self.assertGreaterEqual(window.chart_stack.minimumHeight(), 280)
        self.assertFalse(hasattr(window, "selection_summary"))
        self.assertFalse(hasattr(window, "result_splitter"))
        self.assertIs(
            window.chart_stack,
            window._signal_validation_result_layout.itemAt(0).widget(),
        )

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

    def test_signal_evidence_keeps_all_surviving_or_paths_and_hides_not_obstacle(self):
        identifier = lambda name: {"type": "identifier", "name": name}
        binary = lambda operator, left, right: {
            "type": "binary",
            "operator": operator,
            "left": left,
            "right": right,
        }
        top_ast = binary(
            "NOT",
            binary("OR", binary("OR", identifier("A"), identifier("A")), identifier("B")),
            identifier("C"),
        )
        a_ast = binary("AND", identifier("OCR_0"), identifier("GAP_0"))
        b_ast = binary("OR", identifier("MACD_0"), identifier("RSI_0"))
        signal_expression = {
            "ast": top_ast,
            "identifier_map": {
                "A": "ui_condition_a",
                "B": "ui_condition_b",
                "C": "ui_condition_c",
            },
        }
        rules = {
            "buy": {
                "filters": {
                    "composite": {
                        "expression": {
                            "ast": binary(
                                "NOT",
                                binary("OR", identifier("A"), identifier("C")),
                                identifier("D"),
                            ),
                        },
                    },
                    "ocr": {
                        "conditions": [{"target": "OSC", "operator": "TURN_UP"}],
                    },
                    "moving_average": {
                        "conditions": [{"target": "CLOSE", "operator": "CROSS_UP", "compare_target": "MA60"}],
                    },
                    "rsi": {
                        "conditions": [{"target": "RSI", "operator": "<="}],
                    },
                },
            },
            "sell": {
                "signals": {
                    "ui_condition_a": {
                        "signal_expression": signal_expression,
                        "groups": [{
                            "condition_expression": a_ast,
                            "conditions": [
                                {"expression_id": "OCR_0", "target": "OSC", "operator": "TURN_DOWN"},
                                {"expression_id": "GAP_0", "target": "CLOSE", "operator": "PERCENT_GAP", "compare_target": "AVG_PRICE"},
                            ],
                        }],
                    },
                    "ui_condition_b": {
                        "signal_expression": signal_expression,
                        "groups": [{
                            "condition_expression": b_ast,
                            "conditions": [
                                {"expression_id": "MACD_0", "target": "MACD", "operator": "<="},
                                {"expression_id": "RSI_0", "target": "RSI", "operator": "<="},
                            ],
                        }],
                    },
                    "ui_condition_c": {
                        "signal_expression": signal_expression,
                        "groups": [{
                            "conditions": [
                                {"expression_id": "GAP_0", "target": "CLOSE", "operator": "PERCENT_GAP", "compare_target": "AVG_PRICE"},
                            ],
                        }],
                    },
                },
            },
        }
        paths = {
            "A": "sell.signals.ui_condition_a.groups[0]",
            "B": "sell.signals.ui_condition_b.groups[0]",
            "C": "sell.signals.ui_condition_c.groups[0]",
        }

        def condition(path, expression_id, condition_type, operator, value, right, result, snapshots=()):
            return {
                "path": f"{path}.conditions[0]",
                "expression_id": expression_id,
                "condition_type": condition_type,
                "operator": operator,
                "left_operand": {"key": condition_type, "index": 0, "value": value},
                "right_operand": right,
                "raw_result": result,
                "final_result": result,
                "indicator_snapshots": list(snapshots),
            }

        a_ocr = condition(
            paths["A"], "OCR_0", "OSC", "TURN_DOWN", -709.65,
            {"key": "none", "index": None, "value": None}, True,
            ({"indicator": "OSC", "index": 0, "current": -709.65, "previous": -700, "previous2": -720},),
        )
        a_gap = condition(
            paths["A"], "GAP_0", "CLOSE", "PERCENT_GAP", 252500,
            {"key": "AVG_PRICE", "index": 0, "value": 250000}, True,
        )
        a_gap["path"] = f"{paths['A']}.conditions[1]"
        b_macd = condition(
            paths["B"], "MACD_0", "MACD", "<=", -709.65,
            {"key": "value", "index": None, "value": 0}, True,
        )
        b_rsi = condition(
            paths["B"], "RSI_0", "RSI", "<=", 42,
            {"key": "value", "index": None, "value": 45}, True,
        )
        b_rsi["path"] = f"{paths['B']}.conditions[1]"
        c_obstacle = condition(
            paths["C"], "GAP_0", "CLOSE", "PERCENT_GAP", 240000,
            {"key": "AVG_PRICE", "index": 0, "value": 250000}, False,
        )
        trace = {
            "conditions": [a_ocr, a_gap, b_macd, b_rsi, c_obstacle],
            "groups": [
                {
                    "path": paths["A"],
                    "condition_paths": [a_ocr["path"], a_gap["path"]],
                    "condition_expression": a_ast,
                    "expression_values": {"OCR_0": True, "GAP_0": True},
                    "result": True,
                },
                {
                    "path": paths["B"],
                    "condition_paths": [b_macd["path"], b_rsi["path"]],
                    "condition_expression": b_ast,
                    "expression_values": {"MACD_0": True, "RSI_0": True},
                    "result": True,
                },
                {
                    "path": paths["C"],
                    "condition_paths": [c_obstacle["path"]],
                    "logic": "AND",
                    "result": False,
                },
            ],
            "aggregations": [{
                "side": "SELL",
                "payload": {
                    "matched_group_paths": [paths["A"], paths["B"]],
                    "ui_signal_expression": signal_expression,
                    "ui_expression_values": {"A": True, "B": True, "C": False},
                    "ui_expression_result": True,
                    "result": True,
                },
            }],
        }
        entry = ValidationReplayEntry(
            evaluation_side="SELL",
            evaluation_index=1,
            evaluation_time="20260913100100",
            signal="SELL",
            reason="fixture",
            signal_index=0,
            signal_time="20260913100000",
            delay_bar=1,
            matched_groups=["A", "B"],
            details=[],
            trace=trace,
        )
        original_trace = entry.trace
        lines = signal_evidence_lines_for_entry(entry, rules)
        tooltip = signal_evidence_tooltip(entry, rules)

        self.assertEqual(4, len(lines))
        self.assertEqual(1, sum(line.startswith("▪ OCR ") for line in lines))
        self.assertTrue(any(line.startswith("▪ 가격비교 ") for line in lines))
        self.assertTrue(any(line.startswith("▪ MACD ") for line in lines))
        self.assertTrue(any(line.startswith("▪ RSI ") for line in lines))
        self.assertIn("추정평단 250,000 / 종가 252,500 / +1.00%", tooltip)
        self.assertIn("현재 -709.65 / 이전 -700 / 이전2 -720", tooltip)
        self.assertIn("▪ MACD -709.65", tooltip)
        self.assertNotIn("240,000", tooltip)
        self.assertNotIn("통과", tooltip)
        self.assertNotIn("하락전환", tooltip)
        self.assertNotIn("이하", tooltip)
        self.assertNotIn("sell.signals", tooltip)
        self.assertNotIn("A·", tooltip)
        self.assertEqual("SELL · 09/13 10:01", tooltip.splitlines()[0])
        self.assertTrue(all(line.startswith("▪ ") for line in tooltip.splitlines()[1:]))
        self.assertEqual(original_trace, entry.trace)

        buy_entry = ValidationReplayEntry(
            evaluation_side="BUY",
            evaluation_index=1,
            evaluation_time="20260913100100",
            signal="BUY",
            reason="fixture",
            signal_index=0,
            signal_time="20260913100000",
            delay_bar=1,
            matched_groups=["A", "C"],
            details=[
                "filter_type=OCR enabled=True passed=True reason=matched evaluation_index=1 condition_details=PASS_OSC_TURN_UP",
                "filter_type=MOVING_AVERAGE enabled=True current_value=252500 ma_value=251000 passed=True reason=matched evaluation_index=1",
                "filter_type=RSI enabled=True evaluated_value=52 passed=False reason=not_matched evaluation_index=1",
            ],
            trace={"conditions": [], "groups": [], "aggregations": []},
        )
        buy_lines = signal_evidence_lines_for_entry(buy_entry, rules)
        buy_tooltip = signal_evidence_tooltip(buy_entry, rules)
        self.assertEqual(2, len(buy_lines))
        self.assertTrue(any(line.startswith("▪ OCR ") for line in buy_lines))
        self.assertTrue(any(line.startswith("▪ 이동평균 ") for line in buy_lines))
        self.assertNotIn("RSI", buy_tooltip)
        self.assertEqual("BUY · 09/13 10:01", buy_tooltip.splitlines()[0])

        settings_snapshot = ValidationSettingsSnapshot(rules)
        window = self._window()
        window._pending_result_settings_snapshot = settings_snapshot
        replay = ValidationReplaySnapshot(
            stock=self.stock,
            timeframe_minutes=5,
            settings_hash=settings_snapshot.rules_hash,
            historical_request_id="REQ-EVIDENCE",
            evaluated_start_index=0,
            evaluated_end_index=1,
            dropped_raw_rows_count=0,
            candles=[
                {"time": "20260913100000", "open": 249000, "high": 251000, "low": 248000, "close": 250000, "volume": 1000},
                {"time": "20260913100100", "open": 251000, "high": 253000, "low": 250000, "close": 252500, "volume": 1200},
            ],
            entries=[buy_entry, entry],
        )
        window.set_replay_snapshot(replay)
        marker_tooltips = {
            marker["side"]: marker["tooltip"]
            for marker in window.canvas.marker_records()
        }
        self.assertEqual({"BUY": buy_tooltip, "SELL": tooltip}, marker_tooltips)
        self.assertFalse(hasattr(window, "signal_list_table"))
        self.assertEqual(0, window.completed_cycle_table.rowCount())
        self.assertFalse(window.completed_cycle_empty_label.isHidden())
        with patch.object(
            validation_window_module,
            "signal_evidence_tooltip",
            side_effect=AssertionError("Hover must not rebuild evidence"),
        ):
            self.assertEqual(tooltip, window.canvas.marker_tooltip_at(
                window.canvas._x_for_index(1),
                window.canvas._TOP - 12,
            ))

    def test_completed_cycle_row_selects_matching_sell_candle(self):
        first = self._entry(
            "BUY",
            0,
            signal="BUY",
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
        self.assertFalse(hasattr(window, "filter_result_table"))
        self.assertFalse(hasattr(window, "signal_list_table"))
        self.assertEqual(1, window.completed_cycle_table.rowCount())
        self.assertEqual("09/13 10:01", window.completed_cycle_table.item(0, 4).text())
        self.assertEqual("second-reason", window._entries[1].reason)
        window.select_evaluation_index(0)
        self.assertEqual(0, window.selected_evaluation_index)
        window._completed_cycle_row_clicked(0, 0)
        self.assertEqual(1, window.selected_evaluation_index)
        window.select_evaluation_index(0)
        self.assertEqual(0, window.selected_evaluation_index)

    def test_completed_cycle_table_keeps_five_rows_and_markers_remain_independent(self):
        window = self._window()
        window.show()
        candle_count = 200
        candles = [
            {
                "time": f"20260913{9 + index // 60:02d}{index % 60:02d}00",
                "open": 100 + index,
                "high": 102 + index,
                "low": 99 + index,
                "close": 101 + index,
                "volume": 1000,
            }
            for index in range(candle_count)
        ]
        entries = [
            ValidationReplayEntry(
                evaluation_side="BUY" if index % 2 == 0 else "SELL",
                evaluation_index=index,
                evaluation_time=candles[index]["time"],
                signal="BUY" if index % 2 == 0 else "SELL",
                reason="fixture",
                signal_index=index,
                signal_time=candles[index]["time"],
                delay_bar=0,
                matched_groups=["A"],
                details=[],
                trace={"conditions": [], "groups": [], "aggregations": []},
            )
            for index in range(candle_count)
        ]
        window.set_replay_snapshot(ValidationReplaySnapshot(
            stock=self.stock,
            timeframe_minutes=5,
            settings_hash="hash",
            historical_request_id="REQ-LIST",
            evaluated_start_index=0,
            evaluated_end_index=candle_count - 1,
            dropped_raw_rows_count=0,
            candles=candles,
            entries=entries,
        ))
        self.app.processEvents()
        table = window.completed_cycle_table
        self.assertEqual(Qt.ScrollBarAlwaysOn, table.verticalScrollBarPolicy())
        self.assertEqual(candle_count // 2, table.rowCount())
        expected_height = (
            table.horizontalHeader().height()
            + table.verticalHeader().defaultSectionSize()
            * window._CYCLE_TABLE_VISIBLE_ROWS
            + table.frameWidth() * 2
        )
        self.assertEqual(expected_height, table.height())
        self.assertTrue(table.verticalScrollBar().isVisible())
        self.assertGreater(table.verticalScrollBar().maximum(), 0)
        self.assertEqual(
            candle_count // 2,
            sum(marker["side"] == "BUY" for marker in window.canvas.marker_records()),
        )
        self.assertEqual(
            candle_count // 2,
            sum(marker["side"] == "SELL" for marker in window.canvas.marker_records()),
        )
        window.select_evaluation_index(0)
        self.app.processEvents()
        scroll_value_before = window.time_navigation_scrollbar.value()
        window._completed_cycle_row_clicked(table.rowCount() - 1, 0)
        self.app.processEvents()
        self.assertEqual(candle_count - 1, window.selected_evaluation_index)
        self.assertGreater(
            window.time_navigation_scrollbar.value(),
            scroll_value_before,
        )
        self.assertGreaterEqual(window.chart_stack.height(), window.chart_stack.minimumHeight())

    def test_cycle_table_height_scroll_and_column_ratios_are_row_count_independent(self):
        window = self._window()
        window._available_signal_validation_geometry = lambda: QRect(0, 0, 1920, 1080)
        window.show()
        self.app.processEvents()
        table = window.completed_cycle_table
        fixed_height = table.height()
        ratios = window._CYCLE_TABLE_COLUMN_RATIOS

        def load_cycle_count(cycle_count):
            candles = []
            entries = []
            for index in range(cycle_count * 2):
                time = f"20260914{9 + index // 60:02d}{index % 60:02d}00"
                candles.append({
                    "time": time,
                    "open": 100 + index,
                    "high": 102 + index,
                    "low": 99 + index,
                    "close": 100 + index,
                    "volume": 1000,
                })
                side = "BUY" if index % 2 == 0 else "SELL"
                entries.append(ValidationReplayEntry(
                    evaluation_side=side,
                    evaluation_index=index,
                    evaluation_time=time,
                    signal=side,
                    reason="fixture",
                    signal_index=index,
                    signal_time=time,
                    delay_bar=0,
                    matched_groups=["A"],
                    details=[],
                    trace={"conditions": [], "groups": [], "aggregations": []},
                ))
            window._candles = candles
            window._entries = entries
            window._populate_completed_cycles()
            self.app.processEvents()

        for count in (0, 1, 5, 6, 20):
            with self.subTest(count=count):
                load_cycle_count(count)
                self.assertEqual(count, table.rowCount())
                self.assertEqual(fixed_height, table.height())
                self.assertEqual(
                    table.verticalHeader().defaultSectionSize()
                    * window._CYCLE_TABLE_VISIBLE_ROWS,
                    table.viewport().height(),
                )
                self.assertEqual(Qt.ScrollBarAlwaysOn, table.verticalScrollBarPolicy())
                self.assertEqual(0 if count <= 5 else count - 5, table.verticalScrollBar().maximum())
                self.assertEqual(not count, not window.completed_cycle_empty_label.isHidden())

        window._resize_completed_cycle_columns()
        viewport_width = table.viewport().width()
        widths = [table.columnWidth(column) for column in range(7)]
        self.assertEqual(viewport_width, sum(widths))
        for actual, expected_ratio in zip(widths, ratios):
            self.assertLessEqual(abs(actual / viewport_width - expected_ratio), 0.02)

        before_height = table.height()
        window.resize(window.width() + 180, window.height() + 90)
        self.app.processEvents()
        window._resize_completed_cycle_columns()
        resized_viewport_width = table.viewport().width()
        resized_widths = [table.columnWidth(column) for column in range(7)]
        self.assertEqual(before_height, table.height())
        self.assertEqual(resized_viewport_width, sum(resized_widths))
        for actual, expected_ratio in zip(resized_widths, ratios):
            self.assertLessEqual(
                abs(actual / resized_viewport_width - expected_ratio),
                0.02,
            )

        window.resize(round(1366 * 0.85), window.height())
        self.app.processEvents()
        window._resize_completed_cycle_columns()
        compact_widths = [table.columnWidth(column) for column in range(7)]
        self.assertTrue(all(
            actual >= minimum
            for actual, minimum in zip(
                compact_widths,
                window._completed_cycle_column_minimum_widths(),
            )
        ))
        self.assertEqual(0, table.horizontalScrollBar().maximum())

    def test_user_owned_geometry_survives_result_apply_and_all_section_toggles(self):
        window = self._window()
        window._available_signal_validation_geometry = lambda: QRect(0, 0, 2400, 1400)
        window.show()
        window._initial_natural_fit_pending = True
        window._initial_geometry_committed = False
        window._fit_signal_validation_window()
        self.app.processEvents()

        user_size = window.size() + QSize(120, 70)
        user_position = QPoint(151, 179)
        window.resize(user_size)
        window.move(user_position)
        window._user_geometry_owned = True
        expected_geometry = window.geometry()

        window._request_validation()
        window.set_replay_snapshot(self._snapshot([
            self._entry("BUY", 0, signal="BUY"),
            self._entry("SELL", 1, signal="SELL"),
        ]))
        window._request_settings_apply()
        window.show_settings_apply_result("설정 적용 완료", success=True)
        for toggle in (
            window._toggle_recent_stock_row,
            window._toggle_recent_stock_row,
            lambda: window._toggle_control_section_mode("buy"),
            lambda: window._toggle_control_section_mode("buy"),
            lambda: window._toggle_control_section_mode("sell"),
            lambda: window._toggle_control_section_mode("sell"),
        ):
            toggle()
            self.app.processEvents()
            self.assertEqual(expected_geometry, window.geometry())

    def test_initial_and_changed_candle_counts_drive_requests_but_not_apply(self):
        window = self._window()
        initial = window.request_initial_validation()
        self.assertEqual(100, initial.candle_count)
        window.historical_candle_count_spin.setValue(500)
        changed = window._request_validation()
        self.assertEqual(500, changed.candle_count)
        window.set_replay_snapshot(self._snapshot([]))
        payload = window._request_settings_apply()
        self.assertNotIn("candle_count", json.dumps(payload.to_ui_state()))
        self.assertNotIn("500", json.dumps(payload.to_ui_state()))

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
                window.buy_bollinger_direction_combo.setCurrentText("하향")
                window.buy_bollinger_sign_combo.setCurrentText("+")
                window.buy_bollinger_value_line.setText("0.1")
                window.buy_bollinger_compare_combo.setCurrentText("이하")
                window.sell_signal_condition_a_rsi_value_line.setText("57")
                window.sell_signal_condition_a_gap_left_combo.setCurrentText("현재가")
                window.sell_signal_condition_a_gap_right_combo.setCurrentText("평단가")
                window.sell_signal_condition_a_gap_direction_combo.setCurrentText("하향")
                window.sell_signal_condition_a_gap_value_line.setText("0.75")
                window.sell_signal_condition_a_gap_compare_combo.setCurrentText("이상")
                window.sell_signal_condition_b_price_box_direction_combo.setCurrentText("하향")
                window.sell_signal_condition_b_price_box_sign_combo.setCurrentText("-")
                window.sell_signal_condition_b_price_box_value_line.setText("0.1")
                window.sell_signal_condition_b_price_box_compare_combo.setCurrentText("이상")
                window.sell_signal_condition_b_bollinger_direction_combo.setCurrentText("상향")
                window.sell_signal_condition_b_bollinger_sign_combo.setCurrentText("-")
                window.sell_signal_condition_b_bollinger_value_line.setText("0.2")
                window.sell_signal_condition_b_bollinger_compare_combo.setCurrentText("이하")
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
                bollinger = after["buy_ui"]["signal_filter"]
                self.assertEqual("하향", bollinger["buy_bollinger_direction_combo"])
                self.assertEqual("+", bollinger["buy_bollinger_sign_combo"])
                self.assertEqual("0.1", bollinger["buy_bollinger_value_line"])
                self.assertEqual("이하", bollinger["buy_bollinger_compare_combo"])
                self.assertEqual("57", after["sell_ui"]["signal_conditions"]["condition_a"]["rsi_value_line"])
                sell_a = after["sell_ui"]["signal_conditions"]["condition_a"]
                self.assertEqual("현재가", sell_a["gap_left_combo"])
                self.assertEqual("평단가", sell_a["gap_right_combo"])
                self.assertEqual("하향", sell_a["gap_direction_combo"])
                self.assertEqual("0.75", sell_a["gap_value_line"])
                self.assertEqual("이상", sell_a["gap_compare_combo"])
                sell_b = after["sell_ui"]["signal_conditions"]["condition_b"]
                self.assertEqual("하향", sell_b["price_box_direction_combo"])
                self.assertEqual("-", sell_b["price_box_sign_combo"])
                self.assertEqual("0.1", sell_b["price_box_value_line"])
                self.assertEqual("이상", sell_b["price_box_compare_combo"])
                self.assertEqual("상향", sell_b["bollinger_direction_combo"])
                self.assertEqual("-", sell_b["bollinger_sign_combo"])
                self.assertEqual("0.2", sell_b["bollinger_value_line"])
                self.assertEqual("이하", sell_b["bollinger_compare_combo"])
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

    def test_registration_undo_is_unavailable_without_canonical_defaults(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            rules_path.write_text(json.dumps(self.rules, ensure_ascii=False), encoding="utf-8")
            with patch.object(dialog_module.QTimer, "singleShot"):
                source = dialog_module.IndicatorFollowRoutineSettingsDialog(
                    rules_path=rules_path,
                    routine_path=self.routine_dir,
                    routine_name="지표추종매매",
                    definition_id="indicator_follow",
                    settings_mode="registration",
                )
            self.widgets.append(source)
            file_before = rules_path.read_bytes()
            self.assertEqual("되돌리기", source.reload_button.text())

            source.basic_signal_interval_combo.setCurrentText("10")
            for interval in ("15", "30", "60"):
                candidate = self._seed().to_ui_state()
                candidate["basic"]["basic_signal_interval_combo"] = interval
                result = source.apply_signal_validation_candidate_ui_state(candidate)
                self.assertEqual([], result["skipped"])
                self.assertEqual(interval, source.basic_signal_interval_combo.currentText())
                self.assertEqual(file_before, rules_path.read_bytes())

            registration = source.build_registration_rules_from_current_ui_state()
            self.assertTrue(registration["success"], registration.get("error"))
            self.assertEqual(
                "60",
                registration["rules"]["indicator_follow_ui_state"]["state"]
                ["basic"]["basic_signal_interval_combo"],
            )
            self.assertFalse(hasattr(source, "_registration_undo_target_snapshot"))
            with patch.object(source, "load_rules") as persistent_reload:
                undo = source.restore_settings_undo_snapshot()
            persistent_reload.assert_not_called()
            self.assertFalse(undo["available"])
            self.assertEqual(dialog_module.STATE_AUTHORITY_CANONICAL_DEFAULT, undo["source"])
            self.assertEqual("60", source.basic_signal_interval_combo.currentText())
            self.assertEqual(file_before, rules_path.read_bytes())

    def test_group_remembered_state_overrides_legacy_template_on_registration_open(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            rules_path.write_text(json.dumps(self.rules, ensure_ascii=False), encoding="utf-8")
            remembered = deepcopy(
                self.rules["indicator_follow_ui_state"]["state"]
            )
            remembered["basic"]["basic_signal_interval_combo"] = "60"
            remembered["buy_ui"]["signal_filter"][
                "buy_bollinger_sign_combo"
            ] = "+"
            with patch.object(dialog_module.QTimer, "singleShot"), patch.object(
                dialog_module,
                "LogicalGroupRepository",
            ) as repository_type:
                repository_type.return_value.remembered_registration_state.return_value = {
                    "indicator_follow_ui_state": remembered
                }
                source = dialog_module.IndicatorFollowRoutineSettingsDialog(
                    rules_path=rules_path,
                    routine_path=self.routine_dir,
                    routine_name="지표추종매매",
                    definition_id="indicator_follow",
                    group_id="dbf3790f-c00f-427f-90dc-15d8283f4e1a",
                    settings_mode="registration",
                )
            self.widgets.append(source)
            self.assertEqual("60", source.basic_signal_interval_combo.currentText())
            self.assertEqual("+", source.buy_bollinger_sign_combo.currentText())
            self.assertEqual(
                dialog_module.STATE_AUTHORITY_GROUP_REMEMBERED,
                source._registration_initial_state_source,
            )

    def test_edit_undo_restores_immutable_registration_baseline_without_persistent_read(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_path = Path(temp_dir) / "rules.json"
            rules_path.write_text(json.dumps(self.rules, ensure_ascii=False), encoding="utf-8")
            registration_state = deepcopy(
                self.rules["indicator_follow_ui_state"]["state"]
            )
            with patch.object(dialog_module.QTimer, "singleShot"), patch.object(
                dialog_module,
                "RoutineInstanceRepository",
            ) as repository_type:
                repository_type.return_value.load_registration_baseline.return_value = {
                    "indicator_follow_ui_state": registration_state
                }
                source = dialog_module.IndicatorFollowRoutineSettingsDialog(
                    rules_path=rules_path,
                    routine_path=self.routine_dir,
                    routine_name="지표추종매매A",
                    definition_id="indicator_follow",
                    instance_id="INSTANCE-UNDO",
                    settings_mode="edit",
                )
            self.widgets.append(source)
            baseline = source.collect_indicator_follow_ui_state()
            file_before = rules_path.read_bytes()
            source.basic_signal_interval_combo.setCurrentText("10")
            candidate = self._seed().to_ui_state()
            candidate["basic"]["basic_signal_interval_combo"] = "15"
            result = source.apply_signal_validation_candidate_ui_state(candidate)
            self.assertEqual([], result["skipped"])
            self.assertEqual("15", source.basic_signal_interval_combo.currentText())
            edit_rules = source.build_rules_with_indicator_follow_ui_state()
            self.assertEqual(
                "15",
                edit_rules["indicator_follow_ui_state"]["state"]
                ["basic"]["basic_signal_interval_combo"],
            )

            with patch.object(source, "load_rules") as persistent_reload:
                source.restore_settings_undo_snapshot()
            persistent_reload.assert_not_called()
            self.assertEqual(
                baseline,
                source.collect_indicator_follow_ui_state(),
            )
            self.assertEqual(file_before, rules_path.read_bytes())

    def test_v2_repeated_settings_apply_emits_each_validated_candidate_once(self):
        window = self._window()
        runs = []
        emitted = []

        def accept(payload):
            emitted.append(payload)
            validation_summary = window.validation_status_label.text()
            window.show_settings_apply_result("설정 적용 완료", success=True)
            self.assertEqual(validation_summary, window.validation_status_label.text())

        window.validation_run_requested.connect(runs.append)
        window.settings_apply_requested.connect(accept)
        for expected_count, expression in enumerate(("B", "C", "D"), start=1):
            window.buy_signal_expr_line.setText(expression)
            self.assertEqual(expected_count - 1, len(runs))
            self.assertEqual(expected_count - 1, len(emitted))
            window.primary_validation_action_button.click()
            self.assertEqual(expected_count, len(runs))
            window.set_replay_snapshot(self._snapshot([]))
            self.assertEqual(expected_count, len(emitted))
            self.assertNotIn("설정 적용 완료", window.validation_status_label.text())
            self.assertEqual(
                expression,
                emitted[-1].to_ui_state()["basic"]["buy_signal_expr_line"],
            )
            self.assertEqual("설정적용", window.primary_validation_action_button.text())
            self.assertTrue(window.primary_validation_action_button.isEnabled())

        detached = emitted[-1].to_ui_state()
        detached["basic"]["buy_signal_expr_line"] = "A"
        self.assertEqual(
            "D",
            emitted[-1].to_ui_state()["basic"]["buy_signal_expr_line"],
        )

    def test_parent_v2_context_apply_and_fixed_undo_workflow(self):
        for mode in ("registration", "edit"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp_dir:
                rules_path = Path(temp_dir) / "rules.json"
                rules_path.write_text(
                    json.dumps(self.rules, ensure_ascii=False),
                    encoding="utf-8",
                )
                kwargs = dict(
                    rules_path=rules_path,
                    routine_path=self.routine_dir,
                    routine_name="지표추종매매",
                    definition_id="indicator_follow",
                    settings_mode=mode,
                )
                if mode == "edit":
                    kwargs["instance_id"] = "INSTANCE-CONTEXT-APPLY"
                registration_state = deepcopy(
                    self.rules["indicator_follow_ui_state"]["state"]
                )
                repository_patch = patch.object(
                    dialog_module,
                    "RoutineInstanceRepository",
                )
                with patch.object(dialog_module.QTimer, "singleShot"), repository_patch as repository_type:
                    if mode == "edit":
                        repository_type.return_value.load_registration_baseline.return_value = {
                            "indicator_follow_ui_state": registration_state
                        }
                    source = dialog_module.IndicatorFollowRoutineSettingsDialog(**kwargs)
                    seed = IndicatorFollowSignalValidationSeed(
                        ValidationSettingsSnapshot(self.rules),
                        source.collect_indicator_follow_ui_state(),
                    )
                    window = IndicatorFollowSignalValidationWindow(self.stock, seed)
                self.widgets.extend([source, window])
                source.show()
                window.show()
                self.app.processEvents()

                baseline = source.collect_indicator_follow_ui_state()
                file_before = rules_path.read_bytes()
                run_requests = []
                candidates = []

                def complete_validation(run_request):
                    run_requests.append(run_request)
                    candle_count = run_request.candle_count
                    timeframe = run_request.settings_snapshot.to_dict()["bar"]["bar_minutes"]
                    start = datetime(2026, 9, 14, 9, 0)
                    candles = [{
                        "time": (start + timedelta(minutes=timeframe * index)).strftime(
                            "%Y%m%d%H%M%S"
                        ),
                        "open": 100 + index,
                        "high": 101 + index,
                        "low": 99 + index,
                        "close": 100 + index,
                        "volume": 1,
                    } for index in range(candle_count)]
                    window.set_replay_snapshot(ValidationReplaySnapshot(
                        stock=self.stock,
                        timeframe_minutes=timeframe,
                        settings_hash=run_request.settings_snapshot.rules_hash,
                        historical_request_id=f"HARNESS-{len(run_requests)}",
                        evaluated_start_index=0,
                        evaluated_end_index=candle_count - 1,
                        dropped_raw_rows_count=0,
                        candles=candles,
                        entries=[],
                    ))

                def apply_candidate(payload):
                    candidates.append(payload)
                    result = source.apply_signal_validation_candidate_ui_state(
                        payload.to_ui_state()
                    )
                    self.assertEqual([], result["skipped"])
                    validation_summary = window.validation_status_label.text()
                    window.show_settings_apply_result("설정 적용 완료", success=True)
                    self.assertEqual(
                        validation_summary,
                        window.validation_status_label.text(),
                    )

                window.validation_run_requested.connect(complete_validation)
                window.settings_apply_requested.connect(apply_candidate)

                window.historical_candle_count_spin.lineEdit().setText("200")
                window.historical_candle_count_spin.editingFinished.emit()
                self.assertEqual(200, len(window._candles))
                self.assertEqual([], candidates)

                window.basic_signal_interval_combo.setCurrentText("3")
                self.assertEqual(3, window.replay_snapshot.timeframe_minutes)
                self.assertEqual(
                    baseline["basic"]["basic_signal_interval_combo"],
                    source.basic_signal_interval_combo.currentText(),
                )
                self.assertEqual([], candidates)

                prior_runs = len(run_requests)
                for expected_count, expression in enumerate(("B", "C", "D"), start=1):
                    window.buy_signal_expr_line.setText(expression)
                    self.assertEqual(prior_runs + expected_count - 1, len(run_requests))
                    window.primary_validation_action_button.click()
                    self.assertEqual(prior_runs + expected_count, len(run_requests))
                    self.assertEqual(expected_count, len(candidates))
                    self.assertEqual(
                        expression,
                        source.buy_signal_expr_line.text(),
                    )
                    self.assertEqual("3", source.basic_signal_interval_combo.currentText())
                    self.assertTrue(window.primary_validation_action_button.isEnabled())

                payload = source.build_registration_rules_from_current_ui_state()
                if mode == "registration":
                    self.assertTrue(payload["success"], payload.get("error"))
                    final_rules = payload["rules"]
                else:
                    final_rules = source.build_rules_with_indicator_follow_ui_state()
                final_state = final_rules["indicator_follow_ui_state"]["state"]
                self.assertEqual("3", final_state["basic"]["basic_signal_interval_combo"])
                self.assertEqual("D", final_state["basic"]["buy_signal_expr_line"])
                self.assertNotIn("candle_count", json.dumps(final_state))
                self.assertEqual(file_before, rules_path.read_bytes())

                before_undo = source.collect_indicator_follow_ui_state()
                undo = source.restore_settings_undo_snapshot()
                if mode == "registration":
                    self.assertFalse(undo["available"])
                    self.assertEqual(
                        dialog_module.STATE_AUTHORITY_CANONICAL_DEFAULT,
                        undo["source"],
                    )
                    self.assertEqual(
                        before_undo,
                        source.collect_indicator_follow_ui_state(),
                    )
                else:
                    self.assertTrue(undo["available"])
                    self.assertEqual(
                        baseline,
                        source.collect_indicator_follow_ui_state(),
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
            launch_expression = source.buy_signal_expr_line.text()
            with patch.object(flow_module.QTimer, "singleShot"):
                source.signal_validation_requested.emit(self._seed())
            window = created[0]
            state = self._seed().to_ui_state()
            state["basic"]["buy_signal_expr_line"] = "B or C"
            payload = IndicatorFollowSignalValidationApplyPayload(state)
            window.settings_apply_requested.emit(payload)
            self.assertEqual("B or C", source.buy_signal_expr_line.text())
            self.assertEqual(("", True), window.apply_results[-1])
            self.assertFalse(hasattr(source, "_registration_undo_target_snapshot"))
            undo = source.restore_settings_undo_snapshot()
            self.assertFalse(undo["available"])
            self.assertEqual(
                dialog_module.STATE_AUTHORITY_CANONICAL_DEFAULT,
                undo["source"],
            )
            self.assertNotEqual(launch_expression, source.buy_signal_expr_line.text())
            self.assertEqual("B or C", source.buy_signal_expr_line.text())
            self.assertIn(window, flow.open_windows)

            before = source.collect_indicator_follow_ui_state()
            flow._apply_to_source(window, lambda: None, payload)
            self.assertEqual(before, source.collect_indicator_follow_ui_state())
            self.assertEqual(
                ("원본 설정창이 닫혀 있어 검증값을 적용할 수 없습니다.", False),
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
