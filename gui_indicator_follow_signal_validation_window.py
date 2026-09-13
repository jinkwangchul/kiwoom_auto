# -*- coding: utf-8 -*-
"""Independent candle/indicator signal-validation window."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import math
from typing import Any

from PyQt5.QtCore import QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QPainter
from PyQt5.QtWidgets import (
    QAbstractSpinBox,
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui_indicator_follow_routine_settings_dialog import (
    IndicatorFollowRoutineSettingsDialog,
)
from gui_indicator_follow_validation_chart_window import (
    IndicatorFollowValidationChartCanvas,
)
from indicator_follow_signal_validation_projection import (
    IndicatorFollowSignalValidationApplyPayload,
    IndicatorFollowSignalValidationRunRequest,
    IndicatorFollowSignalValidationSeed,
    build_signal_validation_snapshot,
)
from indicator_follow_signal_validation_presentation import (
    build_signal_validation_filter_rows,
)
from routines.지표추종매매.routine_validation_contract import (
    ValidationSettingsSnapshot,
    ValidationStockRef,
)
from routines.지표추종매매.routine_validation_replay import (
    ValidationReplayEntry,
    ValidationReplaySnapshot,
)


def _display_time(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) == 14 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]} {text[8:10]}:{text[10:12]}"
    return text or "-"


def estimated_signal_return_percent(
    replay_snapshot: ValidationReplaySnapshot,
) -> float | None:
    """Estimate latest SELL return from earlier BUY evaluation-bar closes."""
    if not isinstance(replay_snapshot, ValidationReplaySnapshot):
        raise TypeError("replay_snapshot must be ValidationReplaySnapshot")
    candles = replay_snapshot.to_candles()
    entries = replay_snapshot.to_entries()
    sell_entries = [
        entry for entry in entries
        if entry.signal == "SELL"
        and isinstance(entry.evaluation_index, int)
        and not isinstance(entry.evaluation_index, bool)
        and 0 <= entry.evaluation_index < len(candles)
    ]
    if not sell_entries:
        return None
    latest_sell = max(sell_entries, key=lambda entry: entry.evaluation_index)
    sell_close = _valid_close(candles[latest_sell.evaluation_index].get("close"))
    if sell_close is None:
        return None
    buy_closes = []
    for entry in entries:
        if (
            entry.signal != "BUY"
            or isinstance(entry.evaluation_index, bool)
            or not isinstance(entry.evaluation_index, int)
            or not 0 <= entry.evaluation_index < latest_sell.evaluation_index
        ):
            continue
        close = _valid_close(candles[entry.evaluation_index].get("close"))
        if close is not None:
            buy_closes.append(close)
    if not buy_closes:
        return None
    average_buy = sum(buy_closes) / len(buy_closes)
    if average_buy == 0:
        return None
    value = (sell_close - average_buy) / average_buy * 100.0
    return value if math.isfinite(value) else None


def _valid_close(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _marker_records(
    candles: list[dict[str, Any]],
    entries: list[ValidationReplayEntry],
) -> list[dict[str, Any]]:
    markers = []
    for entry in entries:
        index = entry.evaluation_index
        if (
            entry.signal not in {"BUY", "SELL"}
            or isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < len(candles)
            or str(candles[index].get("time") or "") != entry.evaluation_time
        ):
            continue
        markers.append({
            "side": entry.signal,
            "evaluation_index": index,
            "evaluation_time": entry.evaluation_time,
        })
    return markers


def _parse_candle_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if len(text) != 14 or not text.isdigit():
        return None
    try:
        return datetime.strptime(text, "%Y%m%d%H%M%S")
    except ValueError:
        return None


def _time_axis_label_records(
    candles: list[dict[str, Any]],
    *,
    target_count: int = 10,
) -> list[dict[str, Any]]:
    """Select readable labels from real Candle timestamps without inferring dates."""
    parsed = [
        (index, parsed_time)
        for index, candle in enumerate(candles)
        if (parsed_time := _parse_candle_time(candle.get("time"))) is not None
    ]
    if not parsed:
        return []
    first_time = parsed[0][1]
    last_time = parsed[-1][1]
    duration = last_time - first_time
    year_changed = first_time.year != last_time.year

    boundary_indices = [
        parsed[position][0]
        for position in range(1, len(parsed))
        if parsed[position - 1][1].date() != parsed[position][1].date()
    ]
    priority = [parsed[0][0], parsed[-1][0], *boundary_indices]
    uniform_slots = max(2, min(12, int(target_count)))
    if len(parsed) > 1:
        priority.extend(
            parsed[round(slot * (len(parsed) - 1) / (uniform_slots - 1))][0]
            for slot in range(uniform_slots)
        )

    maximum_labels = 12
    minimum_gap = max(1, len(candles) // maximum_labels)
    selected: list[int] = []
    for index in priority:
        if index in selected:
            continue
        if index not in (parsed[0][0], parsed[-1][0]) and any(
            abs(index - existing) < minimum_gap for existing in selected
        ):
            continue
        selected.append(index)
        if len(selected) >= maximum_labels:
            break
    selected = sorted(set(selected))

    times_by_index = dict(parsed)
    records = []
    for index in selected:
        candle_time = times_by_index[index]
        if year_changed:
            label = candle_time.strftime("%Y/%m/%d")
        elif duration <= timedelta(days=1):
            label = candle_time.strftime("%H:%M")
        elif duration <= timedelta(days=3):
            previous = times_by_index.get(index - 1)
            if previous is None or previous.date() != candle_time.date():
                label = candle_time.strftime("%m/%d %H:%M")
            else:
                label = candle_time.strftime("%H:%M")
        elif duration < timedelta(days=7):
            label = candle_time.strftime("%m/%d %H:%M")
        elif duration < timedelta(days=90):
            label = candle_time.strftime("%m/%d")
        else:
            label = candle_time.strftime("%y/%m/%d")
        records.append({
            "index": index,
            "time": candles[index].get("time"),
            "label": label,
        })
    return records


class IndicatorFollowSignalValidationChartCanvas(
    IndicatorFollowValidationChartCanvas
):
    """V2 canvas overlaying sparse labels derived only from Candle timestamps."""

    def __init__(self, candles, markers, parent=None) -> None:
        super().__init__(candles, markers, parent)
        self._time_axis_records = _time_axis_label_records(self._candles)
        self.setMinimumHeight(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def sizeHint(self) -> QSize:
        return QSize(self._content_width(), 160)

    def time_axis_records(self) -> list[dict[str, Any]]:
        return deepcopy(self._time_axis_records)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self._time_axis_records:
            return
        painter = QPainter(self)
        painter.setPen(QColor("#d1d5db"))
        font = painter.font()
        font.setPointSize(max(7, font.pointSize() - 1))
        painter.setFont(font)
        label_top = max(0, self.height() - 32)
        for record in self._time_axis_records:
            center_x = self._x_for_index(record["index"])
            painter.drawText(
                QRectF(center_x - 42, label_top, 84, 26),
                Qt.AlignHCenter | Qt.AlignTop,
                record["label"],
            )


class IndicatorFollowSignalValidationWindow(
    IndicatorFollowRoutineSettingsDialog
):
    """Top-level working copy for repeated signal-only historical replay."""

    validation_run_requested = pyqtSignal(object)
    settings_apply_requested = pyqtSignal(object)

    def __init__(
        self,
        stock: ValidationStockRef,
        seed: IndicatorFollowSignalValidationSeed,
        parent: QWidget | None = None,
    ) -> None:
        if not isinstance(stock, ValidationStockRef) or not stock.code or not stock.name:
            raise TypeError("stock must be a populated ValidationStockRef")
        if not isinstance(seed, IndicatorFollowSignalValidationSeed):
            raise TypeError("seed must be IndicatorFollowSignalValidationSeed")
        self._signal_validation_mode = True
        self._signal_validation_stock = ValidationStockRef(stock.code, stock.name)
        self._signal_validation_seed = IndicatorFollowSignalValidationSeed(
            seed.settings_snapshot,
            seed.to_ui_state(),
        )
        self._result_ui_state = seed.to_ui_state()
        self._replay_snapshot: ValidationReplaySnapshot | None = None
        self._candles: list[dict[str, Any]] = []
        self._entries: list[ValidationReplayEntry] = []
        self._selected_index: int | None = None
        self._initial_validation_requested = False
        super().__init__(
            rules_path=__file__,
            routine_name="지표추종매매 신호검증 V2",
            definition_id="indicator_follow_signal_validation_v2",
            settings_mode="registration",
            parent=parent,
        )
        self.setModal(False)
        self.resize(1600, 900)
        self.setMinimumSize(1200, 700)

    @property
    def stock(self) -> ValidationStockRef:
        return self._signal_validation_stock

    @property
    def replay_snapshot(self) -> ValidationReplaySnapshot | None:
        return self._replay_snapshot

    @property
    def selected_evaluation_index(self) -> int | None:
        return self._selected_index

    def _update_window_title(self) -> None:
        self.setWindowTitle("지표추종매매 - 독립 신호검증 V2")

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)
        self.title_label = QLabel("")
        self.title_label.setVisible(False)
        self._build_signal_validation_control_tab()
        root.addWidget(self.control_tab, 0)

        action_row = QHBoxLayout()
        self.validation_status_label = QLabel("과거 분봉 데이터 조회 중...")
        self.result_summary_label = QLabel("Candle -  |  BUY -  |  SELL -")
        self.estimated_return_label = QLabel("추정 손익률: -")
        self.estimated_return_label.setStyleSheet("font-weight: bold;")
        self.run_validation_button = QPushButton("검증 실행")
        self.apply_settings_button = QPushButton("설정 반영")
        self.close_button = QPushButton("닫기")
        self.run_validation_button.clicked.connect(self._request_validation)
        self.apply_settings_button.clicked.connect(self._request_settings_apply)
        self.close_button.clicked.connect(self.close)
        action_row.addWidget(self.validation_status_label)
        action_row.addWidget(self.result_summary_label, 1)
        action_row.addWidget(self.estimated_return_label)
        action_row.addWidget(self.run_validation_button)
        action_row.addWidget(self.apply_settings_button)
        action_row.addWidget(self.close_button)
        self._signal_validation_action_layout = action_row
        root.addLayout(action_row)

        self.result_widget = QWidget()
        result_layout = QVBoxLayout(self.result_widget)
        result_layout.setContentsMargins(0, 0, 0, 0)
        self._signal_validation_result_layout = result_layout

        self.chart_stack = QStackedWidget()
        self.loading_label = QLabel("과거 분봉 데이터 조회 중...")
        self.loading_label.setAlignment(Qt.AlignCenter)
        self.loading_label.setStyleSheet("font-size: 12pt; color: #555555;")
        self.chart_scroll_area = QScrollArea()
        self.chart_scroll_area.setWidgetResizable(True)
        self.chart_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.chart_scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chart_stack.addWidget(self.loading_label)
        self.chart_stack.addWidget(self.chart_scroll_area)
        self.chart_stack.setCurrentWidget(self.loading_label)

        self.selection_summary = self._detail_view()
        self.selection_summary.setObjectName("signalValidationCandleSummary")
        self.selection_summary.setPlainText("선택 Candle 요약\n-")
        self.result_splitter = QSplitter(Qt.Horizontal)
        self.result_splitter.addWidget(self.chart_stack)
        self.result_splitter.addWidget(self.selection_summary)
        self.result_splitter.setStretchFactor(0, 3)
        self.result_splitter.setStretchFactor(1, 1)
        self.result_splitter.setMinimumHeight(280)
        result_layout.addWidget(self.result_splitter, 1)

        self.filter_result_table = QTableWidget(0, 5)
        self.filter_result_table.setHorizontalHeaderLabels(
            ["구분", "조건", "설정내용", "실제값", "판정"]
        )
        self.filter_result_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.filter_result_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.filter_result_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.filter_result_table.verticalHeader().setVisible(False)
        self.filter_result_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.filter_result_table.setWordWrap(True)
        header = self.filter_result_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        result_layout.addWidget(self.filter_result_table)
        self.result_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(self.result_widget, 1)

    def _build_signal_validation_control_tab(self) -> None:
        """Build the compact V2 shell while reusing only BUY/SELL signal builders."""
        self.control_tab = QWidget()
        outer = QVBoxLayout(self.control_tab)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        self.control_page = QWidget()
        page_layout = QVBoxLayout(self.control_page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(2)
        page_layout.setAlignment(Qt.AlignTop)

        self.basic_box = QGroupBox("")
        self.basic_box.setObjectName("signalValidationCompactHeader")
        self.basic_box.setStyleSheet(
            "QGroupBox#signalValidationCompactHeader {"
            "border: 1px solid #8A98A8; border-radius: 2px; background: transparent;"
            "}"
        )
        header_row = QHBoxLayout(self.basic_box)
        header_row.setContentsMargins(10, 3, 10, 3)
        header_row.setSpacing(8)
        self.compact_header_arrow = QLabel("▶")
        self.compact_header_arrow.setStyleSheet("font-size: 13pt; font-weight: bold;")
        self.compact_stock_label = QLabel(
            f"{self.stock.code} {self.stock.name}"
        )
        self.compact_stock_label.setStyleSheet("font-size: 13pt; font-weight: bold;")
        self.basic_signal_interval_combo = QComboBox()
        self.basic_signal_interval_combo.addItems(
            ["1", "3", "5", "10", "15", "30", "60", "120", "240"]
        )
        self.basic_signal_interval_combo.setCurrentText("5")
        self.basic_signal_interval_combo.setFixedWidth(60)
        self.basic_signal_interval_combo.setFixedHeight(30)
        self.basic_signal_interval_combo.setLayoutDirection(Qt.RightToLeft)
        self.historical_candle_count_spin = QSpinBox()
        self.historical_candle_count_spin.setRange(1, 2_147_483_647)
        self.historical_candle_count_spin.setValue(100)
        self.historical_candle_count_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.historical_candle_count_spin.setFixedWidth(80)
        self.historical_candle_count_spin.setFixedHeight(30)
        header_row.addWidget(self.compact_header_arrow)
        header_row.addWidget(self.compact_stock_label)
        header_row.addWidget(QLabel("|"))
        header_row.addWidget(QLabel("기준봉"))
        header_row.addWidget(self.basic_signal_interval_combo)
        header_row.addWidget(QLabel("분봉"))
        header_row.addWidget(QLabel("|"))
        header_row.addWidget(QLabel("봉수"))
        header_row.addWidget(self.historical_candle_count_spin)
        header_row.addWidget(QLabel("봉"))
        header_row.addStretch(1)
        self.basic_box.setMaximumHeight(52)
        page_layout.addWidget(self.basic_box)

        buy_title = self._build_control_buy_section(page_layout)
        sell_title = self._build_control_sell_section(page_layout)
        self._control_section_mode = "summary"
        self._control_header_click_modes = {
            buy_title: "buy",
            sell_title: "sell",
        }
        for title in self._control_header_click_modes:
            title.installEventFilter(self)
            title.setCursor(Qt.PointingHandCursor)
        self._apply_control_section_mode("summary", force=True)
        outer.addWidget(self.control_page)
        self.control_tab.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

    @staticmethod
    def _detail_view() -> QPlainTextEdit:
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setLineWrapMode(QPlainTextEdit.NoWrap)
        return view

    def load_rules(self) -> None:
        self.rules_data = self._signal_validation_seed.settings_snapshot.to_dict()
        self.rules = deepcopy(self.rules_data)
        self.apply_signal_validation_ui_state(
            self._signal_validation_seed.to_ui_state()
        )
        self.compact_stock_label.setText(f"{self.stock.code} {self.stock.name}")

    def _show_with_initial_control_section_state(self) -> None:
        self.showNormal()
        self._apply_control_section_mode("buy", force=True)
        QTimer.singleShot(0, self._center_on_initial_screen)

    def _defer_fit_dialog_height_to_control_mode(self, mode=None) -> None:
        QTimer.singleShot(0, self._fit_signal_validation_window)

    def _fit_signal_validation_window(self) -> None:
        layout = self.layout()
        if layout is not None:
            layout.activate()
            contents_hint = layout.sizeHint()
        else:
            contents_hint = self.sizeHint()
        desired_height = max(
            700,
            contents_hint.height(),
            self._required_signal_validation_window_height(),
        )
        desired_width = max(1200, self.width(), contents_hint.width())
        screen = self.screen()
        if screen is None:
            application = QApplication.instance()
            screen = application.primaryScreen() if application is not None else None
        if screen is not None:
            available = screen.availableGeometry()
            frame_extra_width = max(0, self.frameGeometry().width() - self.width())
            frame_extra_height = max(0, self.frameGeometry().height() - self.height())
            desired_width = min(desired_width, max(1, available.width() - frame_extra_width))
            desired_height = min(desired_height, max(1, available.height() - frame_extra_height))
        self.resize(int(desired_width), int(desired_height))
        self._center_on_initial_screen()

    def _required_signal_validation_window_height(self) -> int:
        root_layout = self.layout()
        if root_layout is None:
            return self.sizeHint().height()
        root_layout.activate()
        self._signal_validation_result_layout.activate()
        root_margins = root_layout.contentsMargins()
        result_margins = self._signal_validation_result_layout.contentsMargins()
        control_height = max(
            self.control_tab.sizeHint().height(),
            self.control_page.sizeHint().height(),
        )
        action_height = self._signal_validation_action_layout.sizeHint().height()
        result_height = (
            self.result_splitter.minimumHeight()
            + self.filter_result_table.height()
            + result_margins.top()
            + result_margins.bottom()
            + self._signal_validation_result_layout.spacing()
        )
        return (
            root_margins.top()
            + root_margins.bottom()
            + control_height
            + action_height
            + result_height
            + root_layout.spacing() * max(0, root_layout.count() - 1)
        )

    def changeEvent(self, event) -> None:
        QDialog.changeEvent(self, event)

    def set_historical_candle_count(self, candle_count: int) -> None:
        if (
            isinstance(candle_count, bool)
            or not isinstance(candle_count, int)
            or candle_count <= 0
        ):
            raise ValueError("candle_count must be a positive integer")
        self.historical_candle_count_spin.setValue(candle_count)

    def _request_validation(self) -> IndicatorFollowSignalValidationRunRequest | None:
        try:
            ui_state = self.collect_indicator_follow_ui_state()
            mapper = self._load_indicator_follow_rule_mapper()
            preview = mapper.build_engine_rules_preview_from_ui_state(
                ui_state,
                self._signal_validation_seed.settings_snapshot.to_dict(),
            )
            preview_rules = preview.get("preview_rules")
            if not isinstance(preview_rules, dict):
                raise ValueError("signal validation preview rules are unavailable")
            snapshot = build_signal_validation_snapshot(
                preview_rules,
                ui_state=ui_state,
            )
            run_request = IndicatorFollowSignalValidationRunRequest(
                snapshot,
                self.historical_candle_count_spin.value(),
            )
        except Exception:
            self.show_validation_error("현재 신호설정으로 검증 데이터를 만들 수 없습니다.")
            return None
        self._result_ui_state = deepcopy(ui_state)
        self.validation_status_label.setText("Historical Candle 요청 중")
        self.loading_label.setText("과거 분봉 데이터 조회 중...")
        if self._replay_snapshot is None:
            self.chart_stack.setCurrentWidget(self.loading_label)
        self.run_validation_button.setEnabled(False)
        self.validation_run_requested.emit(run_request)
        return run_request

    def request_initial_validation(self) -> IndicatorFollowSignalValidationRunRequest | None:
        if self._initial_validation_requested:
            return None
        self._initial_validation_requested = True
        return self._request_validation()

    def _request_settings_apply(self) -> IndicatorFollowSignalValidationApplyPayload | None:
        try:
            payload = IndicatorFollowSignalValidationApplyPayload(
                self.collect_indicator_follow_ui_state()
            )
        except Exception:
            self.show_settings_apply_result(
                "현재 신호설정을 반영용 데이터로 만들 수 없습니다.",
                success=False,
            )
            return None
        self.settings_apply_requested.emit(payload)
        return payload

    def show_settings_apply_result(self, message: str, *, success: bool) -> None:
        self.validation_status_label.setText(str(message or "설정 반영 실패"))

    def show_validation_error(self, message: str) -> None:
        self.validation_status_label.setText(str(message or "검증 실패"))
        if self._replay_snapshot is None:
            self.loading_label.setText(str(message or "검증 실패"))
            self.chart_stack.setCurrentWidget(self.loading_label)
        self.run_validation_button.setEnabled(True)

    def set_replay_snapshot(self, replay_snapshot: ValidationReplaySnapshot) -> None:
        if not isinstance(replay_snapshot, ValidationReplaySnapshot):
            raise TypeError("replay_snapshot must be ValidationReplaySnapshot")
        if replay_snapshot.stock != self.stock:
            raise ValueError("replay stock identity mismatch")
        self._replay_snapshot = replay_snapshot
        self._candles = replay_snapshot.to_candles()
        self._entries = replay_snapshot.to_entries()
        markers = _marker_records(self._candles, self._entries)
        old_canvas = self.chart_scroll_area.takeWidget()
        if old_canvas is not None:
            old_canvas.deleteLater()
        self.canvas = IndicatorFollowSignalValidationChartCanvas(self._candles, markers)
        self.canvas.bar_selected.connect(self.select_evaluation_index)
        self.chart_scroll_area.setWidget(self.canvas)
        self.chart_stack.setCurrentWidget(self.chart_scroll_area)

        buy_count = sum(marker["side"] == "BUY" for marker in markers)
        sell_count = sum(marker["side"] == "SELL" for marker in markers)
        self.result_summary_label.setText(
            f"{self.stock.code} {self.stock.name}  |  "
            f"{replay_snapshot.timeframe_minutes}분봉  |  "
            f"Candle {len(self._candles)}  |  BUY {buy_count}  |  SELL {sell_count}"
        )
        estimated = estimated_signal_return_percent(replay_snapshot)
        self.estimated_return_label.setText(
            "추정 손익률: -"
            if estimated is None
            else f"추정 손익률: {estimated:+.2f}%"
        )
        self.validation_status_label.setText("검증 완료")
        self.run_validation_button.setEnabled(True)
        self.select_evaluation_index(replay_snapshot.evaluated_end_index)

    def select_evaluation_index(self, index: int) -> bool:
        snapshot = self._replay_snapshot
        if (
            snapshot is None
            or isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < len(self._candles)
        ):
            return False
        self._selected_index = index
        self.canvas.set_selected_index(index)
        if not snapshot.evaluated_start_index <= index <= snapshot.evaluated_end_index:
            self.selection_summary.setPlainText(
                "선택 Candle 요약\nReplay 평가 범위 밖\n"
                f"평가시각: {_display_time(self._candles[index].get('time'))}"
            )
            self.filter_result_table.setRowCount(0)
            self._resize_filter_result_table_to_contents()
            return True
        buy_entry = self._entry_at(index, "BUY")
        sell_entry = self._entry_at(index, "SELL")
        self.selection_summary.setPlainText(
            self._selection_summary_text(index, buy_entry, sell_entry)
        )
        self._populate_filter_result_table(buy_entry, sell_entry)
        return True

    def _entry_at(self, index: int, side: str) -> ValidationReplayEntry | None:
        return next(
            (
                candidate for candidate in self._entries
                if candidate.evaluation_index == index
                and candidate.evaluation_side == side
            ),
            None,
        )

    @staticmethod
    def _entry_summary_lines(side: str, entry: ValidationReplayEntry | None) -> list[str]:
        if entry is None:
            return [
                f"{side} 신호: 미발생",
                f"{side} reason: -",
                f"{side} matched group: -",
                f"{side} signal_time: -",
                f"{side} delay_bar: -",
            ]
        return [
            f"{side} 신호: {'발생' if entry.signal == side else '미발생'}",
            f"{side} reason: {entry.reason or '-'}",
            f"{side} matched group: {', '.join(entry.matched_groups) or '-'}",
            f"{side} signal_time: {_display_time(entry.signal_time)}",
            f"{side} delay_bar: {entry.delay_bar}",
        ]

    def _selection_summary_text(
        self,
        index: int,
        buy_entry: ValidationReplayEntry | None,
        sell_entry: ValidationReplayEntry | None,
    ) -> str:
        candle = self._candles[index]
        lines = [
            "선택 Candle 요약",
            f"평가 시각: {_display_time(candle.get('time'))}",
            f"시가: {candle.get('open') if candle.get('open') is not None else '-'}",
            f"고가: {candle.get('high') if candle.get('high') is not None else '-'}",
            f"저가: {candle.get('low') if candle.get('low') is not None else '-'}",
            f"종가: {candle.get('close') if candle.get('close') is not None else '-'}",
            "",
        ]
        lines.extend(self._entry_summary_lines("BUY", buy_entry))
        lines.append("")
        lines.extend(self._entry_summary_lines("SELL", sell_entry))
        return "\n".join(lines)

    def _populate_filter_result_table(
        self,
        buy_entry: ValidationReplayEntry | None,
        sell_entry: ValidationReplayEntry | None,
    ) -> None:
        rows = build_signal_validation_filter_rows(
            buy_entry,
            sell_entry,
            self._result_ui_state,
        )
        self.filter_result_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row.to_cells()):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if row.row_kind in {"section", "summary"}:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.filter_result_table.setItem(row_index, column_index, item)
        self._resize_filter_result_table_to_contents()

    def _resize_filter_result_table_to_contents(self) -> int:
        table = self.filter_result_table
        table.resizeRowsToContents()
        margins = table.contentsMargins()
        required_height = (
            table.horizontalHeader().height()
            + sum(table.rowHeight(row) for row in range(table.rowCount()))
            + table.frameWidth() * 2
            + margins.top()
            + margins.bottom()
            + 2
        )
        table.setFixedHeight(required_height)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.updateGeometry()
        QTimer.singleShot(0, self._fit_signal_validation_window)
        return required_height
