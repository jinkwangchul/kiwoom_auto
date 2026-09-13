# -*- coding: utf-8 -*-
"""Independent candle/indicator signal-validation window."""

from __future__ import annotations

from copy import deepcopy
import json
import math
from typing import Any

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTabWidget,
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
    IndicatorFollowSignalValidationSeed,
    build_signal_validation_snapshot,
)
from routines.지표추종매매.routine_validation_contract import (
    ValidationSettingsSnapshot,
    ValidationStockRef,
)
from routines.지표추종매매.routine_validation_replay import (
    ValidationReplayEntry,
    ValidationReplaySnapshot,
)


class _SingleControlTabHost:
    def addTab(self, _widget: QWidget, _label: str) -> int:
        return 0


def _display_time(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) == 14 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]} {text[8:10]}:{text[10:12]}"
    return text or "-"


def _pretty(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


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


class IndicatorFollowSignalValidationWindow(
    IndicatorFollowRoutineSettingsDialog
):
    """Top-level working copy for repeated signal-only historical replay."""

    validation_run_requested = pyqtSignal(object)

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
        self._replay_snapshot: ValidationReplaySnapshot | None = None
        self._candles: list[dict[str, Any]] = []
        self._entries: list[ValidationReplayEntry] = []
        self._selected_index: int | None = None
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
        self.tabs = _SingleControlTabHost()
        self._build_control_tab()
        self.control_tab.setMinimumHeight(290)
        self.control_tab.setMaximumHeight(430)
        root.addWidget(self.control_tab)

        action_row = QHBoxLayout()
        self.validation_status_label = QLabel("")
        self.estimated_return_label = QLabel("추정 손익률: -")
        self.estimated_return_label.setStyleSheet("font-weight: bold;")
        self.run_validation_button = QPushButton("검증 실행")
        self.close_button = QPushButton("닫기")
        self.run_validation_button.clicked.connect(self._request_validation)
        self.close_button.clicked.connect(self.close)
        action_row.addWidget(self.validation_status_label, 1)
        action_row.addWidget(self.estimated_return_label)
        action_row.addWidget(self.run_validation_button)
        action_row.addWidget(self.close_button)
        root.addLayout(action_row)

        self.result_widget = QWidget()
        result_layout = QVBoxLayout(self.result_widget)
        result_layout.setContentsMargins(0, 0, 0, 0)
        self.result_summary_label = QLabel("")
        result_layout.addWidget(self.result_summary_label)

        self.chart_scroll_area = QScrollArea()
        self.chart_scroll_area.setWidgetResizable(True)
        self.chart_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.buy_details = self._detail_view()
        self.sell_details = self._detail_view()
        self.detail_tabs = QTabWidget()
        self.detail_tabs.addTab(self.buy_details, "BUY")
        self.detail_tabs.addTab(self.sell_details, "SELL")
        self.result_splitter = QSplitter(Qt.Horizontal)
        self.result_splitter.addWidget(self.chart_scroll_area)
        self.result_splitter.addWidget(self.detail_tabs)
        self.result_splitter.setStretchFactor(0, 3)
        self.result_splitter.setStretchFactor(1, 2)
        result_layout.addWidget(self.result_splitter, 1)
        self.result_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.result_widget.hide()
        root.addWidget(self.result_widget, 1)

    @staticmethod
    def _detail_view() -> QPlainTextEdit:
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setLineWrapMode(QPlainTextEdit.NoWrap)
        return view

    def load_rules(self) -> None:
        self.rules_data = self._signal_validation_seed.settings_snapshot.to_dict()
        self.rules = deepcopy(self.rules_data)
        self.apply_indicator_follow_ui_state(
            self._signal_validation_seed.to_ui_state()
        )
        self._set_registration_identity_stamp(
            f"{self.stock.code}  {self.stock.name}"
        )

    def _show_with_initial_control_section_state(self) -> None:
        self.showNormal()
        self._apply_control_section_mode("all", force=True)
        QTimer.singleShot(0, self._center_on_initial_screen)

    def _defer_fit_dialog_height_to_control_mode(self, mode=None) -> None:
        return None

    def changeEvent(self, event) -> None:
        QDialog.changeEvent(self, event)

    def _request_validation(self) -> ValidationSettingsSnapshot | None:
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
        except Exception:
            self.show_validation_error("현재 신호설정으로 검증 데이터를 만들 수 없습니다.")
            return None
        self.validation_status_label.setText("Historical Candle 요청 중")
        self.run_validation_button.setEnabled(False)
        self.validation_run_requested.emit(snapshot)
        return snapshot

    def show_validation_error(self, message: str) -> None:
        self.validation_status_label.setText(str(message or "검증 실패"))
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
        self.canvas = IndicatorFollowValidationChartCanvas(self._candles, markers)
        self.canvas.bar_selected.connect(self.select_evaluation_index)
        self.chart_scroll_area.setWidget(self.canvas)

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
        self.result_widget.show()
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
            text = (
                "Replay 평가 범위 밖\n"
                f"평가시각: {_display_time(self._candles[index].get('time'))}"
            )
            self.buy_details.setPlainText(text)
            self.sell_details.setPlainText(text)
            return True
        self.buy_details.setPlainText(self._entry_text(index, "BUY"))
        self.sell_details.setPlainText(self._entry_text(index, "SELL"))
        return True

    def _entry_text(self, index: int, side: str) -> str:
        entry = next(
            (
                candidate for candidate in self._entries
                if candidate.evaluation_index == index
                and candidate.evaluation_side == side
            ),
            None,
        )
        if entry is None:
            return (
                "Replay 결과 없음\n"
                f"평가시각: {_display_time(self._candles[index].get('time'))}"
            )
        trace = entry.trace
        return "\n".join(
            [
                f"공식 신호: {entry.signal or '없음'}",
                f"평가시각: {_display_time(entry.evaluation_time)}",
                f"조건 근거봉: {_display_time(entry.signal_time)}",
                f"지연봉: {entry.delay_bar}",
                f"reason: {entry.reason}",
                "",
                "[Matched Groups]",
                _pretty(entry.matched_groups),
                "",
                "[Details]",
                _pretty(entry.details),
                "",
                "[Conditions]",
                _pretty(trace.get("conditions", [])),
                "",
                "[Groups]",
                _pretty(trace.get("groups", [])),
                "",
                "[Aggregations]",
                _pretty(trace.get("aggregations", [])),
            ]
        )
