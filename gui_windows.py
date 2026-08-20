# -*- coding: utf-8 -*-

"""
gui_windows.py

MASTER_SPEC v1.1 Windows GUI Edition 기준
Windows GUI 창 클래스 정의 파일.

현재 단계:
- 메인 윈도우 안정 버전
- 자동매매 루틴 폴더 자동 탐색
- __pycache__ 제외
- budget.json 이 있는 폴더만 루틴으로 인정
- 키움 로그인, 주문, 실시간 수신 기능은 아직 연결하지 않음
- 수동등록/검색등록 검증 강화
- 신규 종목은 stock_library.json 검색 결과에서만 등록 허용
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from PyQt5 import sip
from PyQt5.QtCore import (
    QEvent,
    QItemSelectionModel,
    QObject,
    QRegularExpression,
    QRect,
    QSettings,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt5.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QIntValidator,
    QPainter,
    QPalette,
    QPen,
    QRegularExpressionValidator,
)
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGroupBox,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QComboBox,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

LOGGER = logging.getLogger(__name__)

ACCOUNT_MEMOS_SETTINGS_KEY = "ui/account_memos"
ACCOUNT_HISTORY_SETTINGS_KEY = "ui/known_accounts"
TOTAL_BUDGET_ROUNDING_SETTINGS_KEY = "ui/total_budget_digit_alignment"
BUDGET_WARNING_SETTINGS_KEY = "ui/budget_warning_enabled"
BUFFER_RESPONSE_EVALUATION_FACTORS = ("손익비율", "손익금액", "투입금액")
BUFFER_RESPONSE_SORT_DIRECTIONS = ("높은순", "낮은순")
BUFFER_RESPONSE_ACTION_MODES = ("조기마감", "즉시청산", "구간마감")
BUFFER_RESPONSE_RATIO_OPTIONS = tuple(f"{percent}%" for percent in range(10, 100, 10))
ACCOUNT_NO_ROLE = Qt.UserRole
ACCOUNT_POPUP_MEMO_ROLE = Qt.UserRole + 1
ACCOUNT_ACTIVE_ROLE = Qt.UserRole + 2


def _system_total_budget_amount() -> int | None:
    try:
        amount = int(read_system_budget_policy()["total_budget"])
    except (KeyError, TypeError, ValueError):
        return None
    return amount if amount >= 0 else None


def masked_account_no(account_no: object) -> str:
    clean = str(account_no or "").strip()
    if not clean:
        return ""
    return f"{clean[:4]}****"


def account_combo_display_text(account_no: object, _memo: object = "") -> str:
    return masked_account_no(account_no)


def account_popup_display_text(account_no: object, memo: object = "") -> str:
    account_text = masked_account_no(account_no)
    clean_memo = str(memo or "").strip()[:8]
    return f"{account_text}   {clean_memo}" if clean_memo else account_text


class _DoubleClickActionButton(QPushButton):
    """Expose one action signal without giving ordinary clicks side effects."""

    doubleClicked = pyqtSignal()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.isEnabled():
            self.doubleClicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class _TextOnlyPopupComboBox(QComboBox):
    """Open from the text hit area while the native arrow remains hidden."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._popup_minimum_width = 0

    def setPopupMinimumWidth(self, width: int) -> None:
        self._popup_minimum_width = max(0, int(width))
        self.view().setMinimumWidth(self._popup_minimum_width)

    def showPopup(self) -> None:
        if self._popup_minimum_width > 0:
            self.view().setMinimumWidth(self._popup_minimum_width)
        super().showPopup()
        if self._popup_minimum_width > 0:
            popup = self.view().window()
            popup.setMinimumWidth(self._popup_minimum_width)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.isEnabled():
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.isEnabled():
            QTimer.singleShot(0, self.showPopup)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _BudgetPercentEdit(QLineEdit):
    """Compact integer editor that also permits the projected '-' display."""

    commitRequested = pyqtSignal()
    cancelRequested = pyqtSignal()

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        selection_epoch = getattr(self, "_selection_epoch", 0) + 1
        self._selection_epoch = selection_epoch
        QTimer.singleShot(
            0,
            lambda epoch=selection_epoch: self._select_all_for_edit(epoch),
        )

    def _select_all_for_edit(self, selection_epoch: int) -> None:
        if (
            self.hasFocus()
            and selection_epoch == getattr(self, "_selection_epoch", 0)
        ):
            self.selectAll()

    def finish_display(self) -> None:
        """Return to a plain-text projection without re-emitting a commit."""
        self._selection_epoch = getattr(self, "_selection_epoch", 0) + 1
        signals_were_blocked = self.blockSignals(True)
        try:
            self.deselect()
            self.clearFocus()
            self.setSelection(0, 0)
        finally:
            self.blockSignals(signals_were_blocked)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.commitRequested.emit()
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            self.cancelRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.commitRequested.emit()
        self.deselect()


class _DoubleClickValueLabel(QLabel):
    """A display label whose only action gesture is a left-button double click."""

    doubleClicked = pyqtSignal()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.isEnabled():
            self.doubleClicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class _MainTotalBudgetPopup(QFrame):
    """Compact, non-modal editor anchored below the total-budget value label."""

    def __init__(self, owner: "MainWindow") -> None:
        super().__init__(owner, Qt.Popup | Qt.FramelessWindowHint)
        self._owner = owner
        self._application_filter_installed = False
        self.setObjectName("mainTotalBudgetPopup")
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "QFrame#mainTotalBudgetPopup { background: palette(window); "
            "border: 1px solid #CBD5E1; border-radius: 4px; }"
            "QPushButton { min-width: 48px; min-height: 24px; padding: 1px 5px; }"
            "QLineEdit { min-height: 24px; padding: 1px 5px; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        percent_layout = QGridLayout()
        percent_layout.setContentsMargins(0, 0, 0, 0)
        percent_layout.setHorizontalSpacing(4)
        percent_layout.setVerticalSpacing(4)
        self.percent_layout = percent_layout
        self.percent_buttons: dict[int, QPushButton] = {}
        for index, percent in enumerate(MAIN_TOTAL_BUDGET_PERCENT_OPTIONS):
            button = QPushButton(f"{percent}%")
            button.setObjectName(f"mainTotalBudgetPercent{percent}")
            button.setToolTip("주문 가능금액 확인 후 사용 가능")
            button.clicked.connect(
                lambda _checked=False, value=percent: self._apply_percent(value)
            )
            self.percent_buttons[percent] = button
            percent_layout.addWidget(button, index // 5, index % 5)
        layout.addLayout(percent_layout)

        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        layout.addWidget(separator)

        direct_layout = QHBoxLayout()
        direct_layout.setContentsMargins(0, 0, 0, 0)
        direct_layout.setSpacing(6)
        self.rounding_toggle = QPushButton()
        self.rounding_toggle.setObjectName("mainTotalBudgetRoundingToggle")
        self.rounding_toggle.setCheckable(True)
        self.rounding_toggle.setToolTip("비율 계산 금액의 상위 두 자릿수를 맞춥니다")
        self.rounding_toggle.toggled.connect(self._rounding_toggled)
        direct_layout.addWidget(self.rounding_toggle)
        direct_layout.addWidget(QLabel("직접입력"))
        self.direct_input = QLineEdit()
        self.direct_input.setObjectName("mainTotalBudgetDirectInput")
        self.direct_input.setMaxLength(len("9,999,999,999"))
        self.direct_input.setValidator(
            QRegularExpressionValidator(
                QRegularExpression(r"[0-9,]{0,13}"),
                self.direct_input,
            )
        )
        self.direct_input.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.direct_input.setFixedWidth(
            self.direct_input.fontMetrics().horizontalAdvance("9,999,999,999") + 14
        )
        self.direct_input.returnPressed.connect(self._apply_direct_input)
        direct_layout.addWidget(self.direct_input)
        layout.addLayout(direct_layout)
        self._stable_popup_size = self._calculate_stable_popup_size()
        self.setFixedSize(self._stable_popup_size)

    def _calculate_stable_popup_size(self):
        """Reserve the final content geometry before the popup is ever shown."""
        self.ensurePolished()
        current_text = self.rounding_toggle.text()
        toggle_width = 0
        for state in ("ON", "OFF"):
            self.rounding_toggle.setText(f"자릿수맞춤 {state}")
            self.rounding_toggle.ensurePolished()
            toggle_width = max(toggle_width, self.rounding_toggle.sizeHint().width())
        self.rounding_toggle.setMinimumWidth(toggle_width)
        self.rounding_toggle.setText(current_text or "자릿수맞춤 OFF")

        popup_layout = self.layout()
        popup_layout.invalidate()
        popup_layout.activate()
        return self.sizeHint()

    def refresh_projection(self) -> None:
        summary = collect_main_budget_summary()
        self.direct_input.setText(f"{int(summary.get('total_budget', 0)):,}")
        self.rounding_toggle.blockSignals(True)
        self.rounding_toggle.setChecked(
            self._owner.main_total_budget_rounding_enabled()
        )
        self.rounding_toggle.blockSignals(False)
        self._update_rounding_toggle_text()
        orderable = self._owner.current_orderable_cash_for_budget()
        enabled = orderable is not None
        for button in self.percent_buttons.values():
            button.setEnabled(enabled)

    def show_below(self, anchor: QWidget) -> None:
        self.refresh_projection()
        popup_layout = self.layout()
        popup_layout.invalidate()
        popup_layout.activate()
        self.resize(self._stable_popup_size)
        position = anchor.mapToGlobal(anchor.rect().bottomLeft())
        screen = QApplication.screenAt(position)
        if screen is not None:
            available = screen.availableGeometry()
            x = min(position.x(), available.right() - self.width() + 1)
            y = min(position.y() + 2, available.bottom() - self.height() + 1)
            position.setX(max(available.left(), x))
            position.setY(max(available.top(), y))
        self.move(position)
        self.show()
        self.raise_()

    def _apply_percent(self, percent: int) -> None:
        if self._owner.apply_main_total_budget_percentage(percent):
            self.hide()

    def _apply_direct_input(self) -> None:
        if self._owner.apply_main_total_budget_direct(self.direct_input.text()):
            self.hide()

    def _rounding_toggled(self, checked: bool) -> None:
        self._update_rounding_toggle_text()
        self._owner.set_main_total_budget_rounding_enabled(checked)

    def _update_rounding_toggle_text(self) -> None:
        state = "ON" if self.rounding_toggle.isChecked() else "OFF"
        self.rounding_toggle.setText(f"자릿수맞춤 {state}")

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)

    def showEvent(self, event) -> None:
        application = QApplication.instance()
        if application is not None and not self._application_filter_installed:
            application.installEventFilter(self)
            self._application_filter_installed = True
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        application = QApplication.instance()
        if application is not None and self._application_filter_installed:
            application.removeEventFilter(self)
            self._application_filter_installed = False
        super().hideEvent(event)

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.MouseButtonPress and self.isVisible():
            clicked_widget = watched if isinstance(watched, QWidget) else None
            if clicked_widget is None or (
                clicked_widget is not self
                and not self.isAncestorOf(clicked_widget)
            ):
                self.hide()
        return False


class _BufferResponseSettingsSurface(QDialog):
    """Wide non-modal editor for the persisted buffer-response policy."""

    MODE_NONE = "NONE"
    MODE_UNIFIED = "UNIFIED"
    MODE_SEGMENTED = "SEGMENTED"

    def __init__(
        self,
        owner: "MainWindow",
        *,
        policy_path: Path | None = None,
    ) -> None:
        super().__init__(owner)
        self._policy_path = policy_path
        self._persisted_baseline: dict[str, object] | None = None
        self._loading_policy = True
        self._last_save_error = ""
        self.setObjectName("mainBufferResponseSettingsSurface")
        self.setWindowTitle("완충대응 설정")
        self.setModal(False)
        self.setMinimumSize(560, 360)
        self.resize(560, 360)
        self.setStyleSheet(
            "QDialog#mainBufferResponseSettingsSurface,"
            "QDialog#mainBufferResponseSettingsSurface QWidget,"
            "QDialog#mainBufferResponseSettingsSurface QLabel,"
            "QDialog#mainBufferResponseSettingsSurface QCheckBox,"
            "QDialog#mainBufferResponseSettingsSurface QComboBox,"
            "QDialog#mainBufferResponseSettingsSurface QPushButton {"
            " font-family: 'Malgun Gothic'; font-size: 9pt;"
            " }"
            "QDialog#mainBufferResponseSettingsSurface QComboBox {"
            " min-height: 30px;"
            " }"
            "QDialog#mainBufferResponseSettingsSurface QPushButton {"
            " min-height: 28px; min-width: 82px;"
            " }"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(34, 14, 34, 12)
        root.setSpacing(10)

        self.unified_checkbox = QCheckBox("일괄적용")
        self.segmented_checkbox = QCheckBox("손익별 적용")
        root.addWidget(self.unified_checkbox)

        self.strategy_rows: dict[str, list[tuple[QComboBox, QComboBox]]] = {}
        self.strategy_action_badges: dict[str, QPushButton] = {}
        strategy_title_width = QFontMetrics(self.font()).horizontalAdvance(
            "▪ 손실구간"
        )
        self.unified_strategy_row, self.unified_strategy_title_label = (
            self._make_strategy_row(
                "",
                "unified",
                strategy_title_width,
                ("손익금액", "낮은순"),
                "조기마감",
            )
        )
        self.profit_strategy_row, self.profit_strategy_title_label = (
            self._make_strategy_row(
                "▪ 수익",
                "profit",
                strategy_title_width,
                ("손익금액", "높은순"),
                "조기마감",
            )
        )
        self.loss_strategy_row, self.loss_strategy_title_label = (
            self._make_strategy_row(
                "▪ 손실",
                "loss",
                strategy_title_width,
                ("손익금액", "낮은순"),
                "즉시청산",
            )
        )
        root.addWidget(self.unified_strategy_row)
        root.addWidget(self.segmented_checkbox)
        root.addWidget(self.profit_strategy_row)
        root.addWidget(self.loss_strategy_row)

        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        root.addWidget(separator)

        close_settings = QWidget()
        close_layout = QHBoxLayout(close_settings)
        close_layout.setContentsMargins(12, 0, 0, 0)
        close_layout.setSpacing(6)
        self.buffer_close_title_label = QLabel("※ 구간마감설정 :")
        close_layout.addWidget(self.buffer_close_title_label)

        badge_metrics = QFontMetrics(self.font())
        fixed_badge_width = max(
            badge_metrics.horizontalAdvance(text)
            for text in ("조기마감", "즉시청산")
        ) + 20
        self.buffer_close_early_badge = self._make_fixed_action_badge(
            "조기마감",
            fixed_badge_width,
        )
        self.buffer_close_immediate_badge = self._make_fixed_action_badge(
            "즉시청산",
            fixed_badge_width,
        )
        close_layout.addWidget(self.buffer_close_early_badge)
        close_layout.addWidget(QLabel("◁"))
        self.buffer_close_ratio_combo = _TextOnlyPopupComboBox()
        self.buffer_close_ratio_combo.setObjectName("bufferResponseRatioCombo")
        self.buffer_close_ratio_combo.addItems(BUFFER_RESPONSE_RATIO_OPTIONS)
        self.buffer_close_ratio_combo.setCurrentText("80%")
        ratio_width = badge_metrics.horizontalAdvance("90%") + 8
        self.buffer_close_ratio_combo.setFixedSize(ratio_width, 26)
        self.buffer_close_ratio_combo.setPopupMinimumWidth(
            max(64, badge_metrics.horizontalAdvance("90%") + 28)
        )
        self.buffer_close_ratio_combo.view().setTextElideMode(Qt.ElideNone)
        self.buffer_close_ratio_combo.setCursor(Qt.PointingHandCursor)
        self.buffer_close_ratio_combo.setStyleSheet(
            "QComboBox { border: none; outline: none; background: transparent;"
            " padding: 0px; }"
            "QComboBox:hover, QComboBox:focus { border: none;"
            " background: transparent; outline: none; }"
            "QComboBox::drop-down { border: none; width: 0px; }"
            "QComboBox::down-arrow { image: none; width: 0px; height: 0px; }"
        )
        close_layout.addWidget(self.buffer_close_ratio_combo)
        close_layout.addWidget(QLabel("▷"))
        close_layout.addWidget(self.buffer_close_immediate_badge)
        close_layout.addStretch(1)
        root.addWidget(close_settings)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        self.save_button = self.button_box.button(QDialogButtonBox.Save)
        self.cancel_button = self.button_box.button(QDialogButtonBox.Cancel)
        self.save_button.setText("저장")
        self.cancel_button.setText("취소")
        self.save_button.setMinimumWidth(110)
        self.cancel_button.setMinimumWidth(110)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        root.addWidget(self.button_box)

        self._mode_syncing = False
        self.unified_checkbox.toggled.connect(self._on_unified_toggled)
        self.segmented_checkbox.toggled.connect(self._on_segmented_toggled)
        for rows in self.strategy_rows.values():
            for factor_combo, direction_combo in rows:
                factor_combo.currentTextChanged.connect(
                    lambda _text: self._refresh_save_enabled()
                )
                direction_combo.currentTextChanged.connect(
                    lambda _text: self._refresh_save_enabled()
                )
        self.buffer_close_ratio_combo.currentTextChanged.connect(
            lambda _text: self._refresh_save_enabled()
        )
        self._loading_policy = False
        self.reload_from_persisted()

    def _make_strategy_row(
        self,
        title: str,
        key: str,
        title_width: int,
        default: tuple[str, str],
        action_default: str,
    ) -> tuple[QWidget, QLabel]:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(8)
        title_label = QLabel(title)
        title_label.setFixedWidth(title_width)
        title_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        if title:
            title_label.setContentsMargins(12, 0, 0, 0)
        layout.addWidget(title_label)
        factor, direction = default
        factor_combo = QComboBox()
        factor_combo.addItems(BUFFER_RESPONSE_EVALUATION_FACTORS)
        factor_combo.setCurrentText(factor)
        factor_combo.setFixedWidth(126)
        direction_combo = QComboBox()
        direction_combo.addItems(BUFFER_RESPONSE_SORT_DIRECTIONS)
        direction_combo.setCurrentText(direction)
        direction_combo.setFixedWidth(106)
        layout.addWidget(factor_combo)
        layout.addWidget(direction_combo)
        layout.addSpacing(8)
        action_badge = _DoubleClickActionButton(action_default)
        action_badge.setCursor(Qt.PointingHandCursor)
        action_badge_width = max(
            QFontMetrics(action_badge.font()).horizontalAdvance(text)
            for text in BUFFER_RESPONSE_ACTION_MODES
        ) + 20
        action_badge.setFixedSize(action_badge_width, 30)
        action_badge.doubleClicked.connect(
            lambda strategy_key=key: (
                self._cycle_strategy_action_badge(strategy_key)
            )
        )
        layout.addWidget(action_badge)
        layout.addStretch(1)
        self.strategy_rows[key] = [(factor_combo, direction_combo)]
        self.strategy_action_badges[key] = action_badge
        return widget, title_label

    @staticmethod
    def _make_fixed_action_badge(text: str, width: int) -> QLabel:
        badge = QLabel(text)
        badge.setAlignment(Qt.AlignCenter)
        badge.setFixedSize(width, 30)
        badge.setStyleSheet(
            "QLabel { border: 1px solid #b7bcc5; border-radius: 3px;"
            " background: palette(base); padding: 0px; }"
        )
        return badge

    def _cycle_strategy_action_badge(self, key: str) -> None:
        badge = self.strategy_action_badges.get(key)
        if badge is None or not badge.isEnabled():
            return
        current = badge.text()
        try:
            index = BUFFER_RESPONSE_ACTION_MODES.index(current)
        except ValueError:
            index = -1
        badge.setText(
            BUFFER_RESPONSE_ACTION_MODES[
                (index + 1) % len(BUFFER_RESPONSE_ACTION_MODES)
            ]
        )
        self._refresh_save_enabled()

    def _on_unified_toggled(self, checked: bool) -> None:
        if self._mode_syncing:
            return
        self.set_application_mode(
            self.MODE_UNIFIED if checked else self.MODE_NONE
        )

    def _on_segmented_toggled(self, checked: bool) -> None:
        if self._mode_syncing:
            return
        self.set_application_mode(
            self.MODE_SEGMENTED if checked else self.MODE_NONE
        )

    def set_application_mode(self, mode: str) -> None:
        self._mode_syncing = True
        try:
            self.unified_checkbox.setChecked(mode == self.MODE_UNIFIED)
            self.segmented_checkbox.setChecked(mode == self.MODE_SEGMENTED)
        finally:
            self._mode_syncing = False
        self.unified_checkbox.setEnabled(True)
        self.segmented_checkbox.setEnabled(True)
        self.unified_strategy_row.setEnabled(mode == self.MODE_UNIFIED)
        segmented = mode == self.MODE_SEGMENTED
        self.profit_strategy_row.setEnabled(segmented)
        self.loss_strategy_row.setEnabled(segmented)
        self._refresh_save_enabled()

    def application_mode(self) -> str:
        if self.unified_checkbox.isChecked():
            return self.MODE_UNIFIED
        if self.segmented_checkbox.isChecked():
            return self.MODE_SEGMENTED
        return self.MODE_NONE

    def _editor_policy(self) -> dict[str, object]:
        strategies: dict[str, dict[str, str]] = {}
        for key in ("unified", "profit", "loss"):
            factor_combo, direction_combo = self.strategy_rows[key][0]
            strategies[key] = {
                "evaluation_factor": factor_combo.currentText().strip(),
                "direction": direction_combo.currentText().strip(),
                "response_mode": self.strategy_action_badges[key].text().strip(),
            }
        ratio_text = self.buffer_close_ratio_combo.currentText().strip()
        threshold: object = ratio_text[:-1] if ratio_text.endswith("%") else ratio_text
        return {
            "application_mode": self.application_mode(),
            "threshold_percent": threshold,
            "strategies": strategies,
        }

    def _apply_editor_policy(self, policy: object) -> None:
        normalized = validate_buffer_response_policy(policy)
        self._loading_policy = True
        try:
            self.set_application_mode(str(normalized["application_mode"]))
            strategies = normalized["strategies"]
            assert isinstance(strategies, dict)
            for key in ("unified", "profit", "loss"):
                strategy = strategies[key]
                factor_combo, direction_combo = self.strategy_rows[key][0]
                factor_combo.setCurrentText(strategy["evaluation_factor"])
                direction_combo.setCurrentText(strategy["direction"])
                self.strategy_action_badges[key].setText(strategy["response_mode"])
            self.buffer_close_ratio_combo.setCurrentText(
                f"{normalized['threshold_percent']}%"
            )
        finally:
            self._loading_policy = False
        self._refresh_save_enabled()

    def reload_from_persisted(self) -> None:
        persisted = read_buffer_response_policy(path=self._policy_path)
        if persisted.get("available") is True:
            normalized = validate_buffer_response_policy(persisted)
            self._persisted_baseline = normalized
            self._apply_editor_policy(normalized)
            return
        self._persisted_baseline = None
        self._apply_editor_policy(default_buffer_response_policy())

    def _refresh_save_enabled(self) -> None:
        if self._loading_policy or not hasattr(self, "save_button"):
            return
        try:
            current = validate_buffer_response_policy(self._editor_policy())
        except ValueError:
            self.save_button.setEnabled(False)
            return
        self.save_button.setEnabled(
            self._persisted_baseline is None
            or current != self._persisted_baseline
        )

    def accept(self) -> None:
        try:
            current = validate_buffer_response_policy(self._editor_policy())
            saved = write_buffer_response_policy(
                current,
                path=self._policy_path,
            )
        except Exception as exc:
            self._last_save_error = str(exc).strip() or "BUFFER_RESPONSE_POLICY_SAVE_FAILED"
            self._refresh_save_enabled()
            return
        self._last_save_error = ""
        self._persisted_baseline = saved
        self._refresh_save_enabled()
        super().accept()

    def reject(self) -> None:
        self.reload_from_persisted()
        super().reject()

    def closeEvent(self, event) -> None:
        self.reload_from_persisted()
        super().closeEvent(event)


class _RoutineLimitResponseSettingsSurface(QDialog):
    """RoutineInstance-scoped editor for the persisted limit response policy."""

    MODE_NONE = "NONE"
    MODE_UNIFIED = "UNIFIED"
    MODE_SEGMENTED = "SEGMENTED"
    ROUTINE_CLOSE_COLOR = "#DC2626"
    NEUTRAL_ACTION_COLOR = "#374151"

    def __init__(
        self,
        owner: QWidget,
        instance_id: str,
        *,
        repository: RoutineInstanceRepository | None = None,
    ) -> None:
        super().__init__(owner)
        self.instance_id = str(instance_id or "").strip()
        self._repository = repository or RoutineInstanceRepository(PROJECT_ROOT)
        self._persisted_baseline: dict[str, object] | None = None
        self._loading_policy = True
        self._last_save_error = ""
        self.setObjectName("routineLimitResponseSettingsSurface")
        self.setWindowTitle("한도대응")
        self.setModal(False)
        self.setMinimumSize(560, 360)
        self.resize(560, 360)
        self.setStyleSheet(
            "QDialog#routineLimitResponseSettingsSurface,"
            "QDialog#routineLimitResponseSettingsSurface QWidget,"
            "QDialog#routineLimitResponseSettingsSurface QLabel,"
            "QDialog#routineLimitResponseSettingsSurface QCheckBox,"
            "QDialog#routineLimitResponseSettingsSurface QComboBox,"
            "QDialog#routineLimitResponseSettingsSurface QPushButton {"
            " font-family: 'Malgun Gothic'; font-size: 9pt;"
            " }"
            "QDialog#routineLimitResponseSettingsSurface QComboBox {"
            " min-height: 30px;"
            " }"
            "QDialog#routineLimitResponseSettingsSurface QPushButton {"
            " min-height: 28px; min-width: 82px;"
            " }"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(34, 14, 34, 12)
        root.setSpacing(10)

        self.unified_checkbox = QCheckBox("일괄적용")
        self.segmented_checkbox = QCheckBox("손익별 적용")
        root.addWidget(self.unified_checkbox)

        self.strategy_rows: dict[str, list[tuple[QComboBox, QComboBox]]] = {}
        self.strategy_action_badges: dict[str, QPushButton] = {}
        title_width = QFontMetrics(self.font()).horizontalAdvance("▪ 손실구간")
        self.unified_strategy_row = self._make_strategy_row(
            "", "unified", title_width, ("손익금액", "낮은순"), "조기마감"
        )
        self.profit_strategy_row = self._make_strategy_row(
            "▪ 수익", "profit", title_width, ("손익금액", "높은순"), "조기마감"
        )
        self.loss_strategy_row = self._make_strategy_row(
            "▪ 손실", "loss", title_width, ("손익금액", "낮은순"), "즉시청산"
        )
        root.addWidget(self.unified_strategy_row)
        root.addWidget(self.segmented_checkbox)
        root.addWidget(self.profit_strategy_row)
        root.addWidget(self.loss_strategy_row)

        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        root.addWidget(separator)

        segment_settings = QWidget()
        segment_layout = QHBoxLayout(segment_settings)
        segment_layout.setContentsMargins(0, 0, 0, 0)
        segment_layout.setSpacing(3)
        badge_metrics = QFontMetrics(self.font())
        self.segment_close_title_label = QLabel("※구간마감설정:")
        self.segment_close_title_label.setFixedWidth(
            badge_metrics.horizontalAdvance("※구간마감설정:") + 8
        )
        segment_layout.addWidget(self.segment_close_title_label)
        fixed_badge_width = max(
            badge_metrics.horizontalAdvance(text)
            for text in ("조기마감", "즉시청산")
        ) + 20
        self.early_close_percent_combo = self._make_percent_combo(
            ROUTINE_LIMIT_RESPONSE_EARLY_CLOSE_PERCENTS
        )
        self.early_close_percent_combo.setObjectName(
            "routineLimitEarlyClosePercentCombo"
        )
        segment_layout.addWidget(self.early_close_percent_combo)
        segment_layout.addWidget(QLabel("▷"))
        self.segment_early_close_label = self._make_fixed_action_badge(
            "조기마감",
            fixed_badge_width,
            color=self.ROUTINE_CLOSE_COLOR,
        )
        segment_layout.addWidget(self.segment_early_close_label)
        segment_layout.addStretch(1)
        self.segment_close_separator_label = QLabel("|")
        segment_layout.addWidget(self.segment_close_separator_label)
        segment_layout.addStretch(1)
        self.immediate_liquidation_percent_combo = self._make_percent_combo(
            ROUTINE_LIMIT_RESPONSE_IMMEDIATE_LIQUIDATION_PERCENTS
        )
        self.immediate_liquidation_percent_combo.setObjectName(
            "routineLimitImmediateLiquidationPercentCombo"
        )
        segment_layout.addWidget(self.immediate_liquidation_percent_combo)
        segment_layout.addWidget(QLabel("▷"))
        self.segment_immediate_liquidation_label = self._make_fixed_action_badge(
            "즉시청산",
            fixed_badge_width,
        )
        segment_layout.addWidget(self.segment_immediate_liquidation_label)
        root.addWidget(segment_settings)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        self.save_button = self.button_box.button(QDialogButtonBox.Save)
        self.cancel_button = self.button_box.button(QDialogButtonBox.Cancel)
        self.save_button.setText("저장")
        self.cancel_button.setText("취소")
        self.save_button.setMinimumWidth(110)
        self.cancel_button.setMinimumWidth(110)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        root.addWidget(self.button_box)

        self._mode_syncing = False
        self.unified_checkbox.toggled.connect(self._on_unified_toggled)
        self.segmented_checkbox.toggled.connect(self._on_segmented_toggled)
        for rows in self.strategy_rows.values():
            for factor_combo, direction_combo in rows:
                factor_combo.currentTextChanged.connect(
                    lambda _text: self._refresh_save_enabled()
                )
                direction_combo.currentTextChanged.connect(
                    lambda _text: self._refresh_save_enabled()
                )
        self.early_close_percent_combo.currentTextChanged.connect(
            self._on_early_close_percent_changed
        )
        self.immediate_liquidation_percent_combo.currentTextChanged.connect(
            lambda _text: self._refresh_save_enabled()
        )
        self._loading_policy = False
        self.reload_from_persisted()

    def _make_strategy_row(
        self,
        title: str,
        key: str,
        title_width: int,
        default: tuple[str, str],
        action_default: str,
    ) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(8)
        title_label = QLabel(title)
        title_label.setFixedWidth(title_width)
        title_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        if title:
            title_label.setContentsMargins(12, 0, 0, 0)
        layout.addWidget(title_label)
        factor_combo = QComboBox()
        factor_combo.addItems(ROUTINE_LIMIT_RESPONSE_EVALUATION_FACTORS)
        factor_combo.setCurrentText(default[0])
        factor_combo.setFixedWidth(126)
        direction_combo = QComboBox()
        direction_combo.addItems(ROUTINE_LIMIT_RESPONSE_SORT_DIRECTIONS)
        direction_combo.setCurrentText(default[1])
        direction_combo.setFixedWidth(106)
        layout.addWidget(factor_combo)
        layout.addWidget(direction_combo)
        layout.addSpacing(8)
        action_badge = _DoubleClickActionButton(action_default)
        action_badge.setCursor(Qt.PointingHandCursor)
        action_badge.setFixedSize(
            max(
                QFontMetrics(action_badge.font()).horizontalAdvance(text)
                for text in ROUTINE_LIMIT_RESPONSE_ACTION_MODES
            )
            + 20,
            30,
        )
        action_badge.doubleClicked.connect(
            lambda strategy_key=key: self._cycle_strategy_action_badge(strategy_key)
        )
        layout.addWidget(action_badge)
        layout.addStretch(1)
        self.strategy_rows[key] = [(factor_combo, direction_combo)]
        self.strategy_action_badges[key] = action_badge
        self._apply_action_badge_style(action_badge)
        return widget

    @staticmethod
    def _make_fixed_action_badge(
        text: str,
        width: int,
        *,
        color: str = "#374151",
    ) -> QLabel:
        badge = QLabel(text)
        badge.setAlignment(Qt.AlignCenter)
        badge.setFixedSize(width, 30)
        badge.setStyleSheet(
            "QLabel {"
            f" color: {color}; border: 1px solid {color};"
            " border-radius: 3px; background: palette(base);"
            " margin-top: 4px; padding: 0px; }"
        )
        return badge

    def _make_percent_combo(self, percents: tuple[int, ...]) -> _TextOnlyPopupComboBox:
        combo = _TextOnlyPopupComboBox()
        combo.addItems([f"{percent}%" for percent in percents])
        metrics = QFontMetrics(self.font())
        combo.setFixedSize(metrics.horizontalAdvance("90%") + 8, 26)
        combo.setPopupMinimumWidth(max(64, metrics.horizontalAdvance("90%") + 28))
        combo.view().setTextElideMode(Qt.ElideNone)
        combo.setCursor(Qt.PointingHandCursor)
        combo.setStyleSheet(
            "QComboBox { border: none; outline: none; background: transparent;"
            " padding: 0px; }"
            "QComboBox:hover, QComboBox:focus { border: none;"
            " background: transparent; outline: none; }"
            "QComboBox::drop-down { border: none; width: 0px; }"
            "QComboBox::down-arrow { image: none; width: 0px; height: 0px; }"
        )
        return combo

    def _apply_action_badge_style(self, badge: QPushButton) -> None:
        color = (
            self.ROUTINE_CLOSE_COLOR
            if badge.text() == "조기마감"
            else self.NEUTRAL_ACTION_COLOR
        )
        badge.setStyleSheet(
            "QPushButton {"
            f" color: {color}; border: 1px solid {color};"
            " border-radius: 3px; background: transparent; padding: 0px;"
            " font-weight: bold; }"
        )

    def _cycle_strategy_action_badge(self, key: str) -> None:
        badge = self.strategy_action_badges.get(key)
        if badge is None or not badge.isEnabled():
            return
        try:
            index = ROUTINE_LIMIT_RESPONSE_ACTION_MODES.index(badge.text())
        except ValueError:
            index = -1
        badge.setText(
            ROUTINE_LIMIT_RESPONSE_ACTION_MODES[
                (index + 1) % len(ROUTINE_LIMIT_RESPONSE_ACTION_MODES)
            ]
        )
        self._apply_action_badge_style(badge)
        self._refresh_save_enabled()

    def _on_unified_toggled(self, checked: bool) -> None:
        if not self._mode_syncing:
            self.set_application_mode(self.MODE_UNIFIED if checked else self.MODE_NONE)

    def _on_segmented_toggled(self, checked: bool) -> None:
        if not self._mode_syncing:
            self.set_application_mode(
                self.MODE_SEGMENTED if checked else self.MODE_NONE
            )

    def set_application_mode(self, mode: str) -> None:
        self._mode_syncing = True
        try:
            self.unified_checkbox.setChecked(mode == self.MODE_UNIFIED)
            self.segmented_checkbox.setChecked(mode == self.MODE_SEGMENTED)
        finally:
            self._mode_syncing = False
        self.unified_strategy_row.setEnabled(mode == self.MODE_UNIFIED)
        segmented = mode == self.MODE_SEGMENTED
        self.profit_strategy_row.setEnabled(segmented)
        self.loss_strategy_row.setEnabled(segmented)
        self._refresh_save_enabled()

    def application_mode(self) -> str:
        if self.unified_checkbox.isChecked():
            return self.MODE_UNIFIED
        if self.segmented_checkbox.isChecked():
            return self.MODE_SEGMENTED
        return self.MODE_NONE

    @staticmethod
    def _percent_value(combo: QComboBox) -> int:
        return int(combo.currentText().strip().rstrip("%"))

    def _on_early_close_percent_changed(self, _text: str) -> None:
        self._rebuild_immediate_percent_options()
        self._refresh_save_enabled()

    def _rebuild_immediate_percent_options(self, preferred: int | None = None) -> None:
        if preferred is None and self.immediate_liquidation_percent_combo.currentText():
            preferred = self._percent_value(self.immediate_liquidation_percent_combo)
        early = self._percent_value(self.early_close_percent_combo)
        valid = [
            percent
            for percent in ROUTINE_LIMIT_RESPONSE_IMMEDIATE_LIQUIDATION_PERCENTS
            if percent > early
        ]
        selected = preferred if preferred in valid else valid[0]
        combo = self.immediate_liquidation_percent_combo
        blocked = combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItems([f"{percent}%" for percent in valid])
            combo.setCurrentText(f"{selected}%")
        finally:
            combo.blockSignals(blocked)

    def _editor_policy(self) -> dict[str, object]:
        strategies: dict[str, dict[str, str]] = {}
        for key in ("unified", "profit", "loss"):
            factor_combo, direction_combo = self.strategy_rows[key][0]
            strategies[key] = {
                "evaluation_factor": factor_combo.currentText().strip(),
                "direction": direction_combo.currentText().strip(),
                "response_mode": self.strategy_action_badges[key].text().strip(),
            }
        return {
            "application_mode": self.application_mode(),
            "strategies": strategies,
            "segment_close": {
                "early_close_percent": self._percent_value(
                    self.early_close_percent_combo
                ),
                "immediate_liquidation_percent": self._percent_value(
                    self.immediate_liquidation_percent_combo
                ),
            },
        }

    def _apply_editor_policy(self, policy: object) -> None:
        normalized = validate_routine_limit_response_policy(policy)
        self._loading_policy = True
        try:
            self.set_application_mode(str(normalized["application_mode"]))
            strategies = normalized["strategies"]
            assert isinstance(strategies, dict)
            for key in ("unified", "profit", "loss"):
                strategy = strategies[key]
                factor_combo, direction_combo = self.strategy_rows[key][0]
                factor_combo.setCurrentText(strategy["evaluation_factor"])
                direction_combo.setCurrentText(strategy["direction"])
                badge = self.strategy_action_badges[key]
                badge.setText(strategy["response_mode"])
                self._apply_action_badge_style(badge)
            segment_close = normalized["segment_close"]
            assert isinstance(segment_close, dict)
            self.early_close_percent_combo.setCurrentText(
                f"{segment_close['early_close_percent']}%"
            )
            self._rebuild_immediate_percent_options(
                int(segment_close["immediate_liquidation_percent"])
            )
        finally:
            self._loading_policy = False
        self._refresh_save_enabled()

    def reload_from_persisted(self) -> None:
        instance = self._repository.get_instance(self.instance_id)
        persisted = instance.buy_limit_response_policy if instance is not None else None
        if persisted is None:
            self._persisted_baseline = None
            self._apply_editor_policy(default_routine_limit_response_policy())
            return
        normalized = validate_routine_limit_response_policy(persisted)
        self._persisted_baseline = normalized
        self._apply_editor_policy(normalized)

    def _refresh_save_enabled(self) -> None:
        if self._loading_policy or not hasattr(self, "save_button"):
            return
        try:
            current = validate_routine_limit_response_policy(self._editor_policy())
        except (ValueError, TypeError):
            self.save_button.setEnabled(False)
            return
        self.save_button.setEnabled(
            self._persisted_baseline is None or current != self._persisted_baseline
        )

    def accept(self) -> None:
        try:
            current = validate_routine_limit_response_policy(self._editor_policy())
            result = self._repository.update_buy_limit_response_policy(
                self.instance_id,
                current,
            )
            if not result.success or result.instance is None:
                raise RuntimeError(
                    result.error or "ROUTINE_LIMIT_RESPONSE_POLICY_SAVE_FAILED"
                )
            if result.instance.buy_limit_response_policy != current:
                raise RuntimeError("ROUTINE_LIMIT_RESPONSE_POLICY_READBACK_MISMATCH")
        except Exception as exc:
            self._last_save_error = str(exc).strip() or (
                "ROUTINE_LIMIT_RESPONSE_POLICY_SAVE_FAILED"
            )
            self._refresh_save_enabled()
            return
        self._last_save_error = ""
        self._persisted_baseline = current
        self._refresh_save_enabled()
        super().accept()

    def reject(self) -> None:
        self.reload_from_persisted()
        super().reject()

    def closeEvent(self, event) -> None:
        self.reload_from_persisted()
        super().closeEvent(event)


class _AccountPopupDisplayDelegate(QStyledItemDelegate):
    """Project memo text only in the popup without changing account identity."""

    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        option.text = account_popup_display_text(
            index.data(ACCOUNT_NO_ROLE),
            index.data(ACCOUNT_POPUP_MEMO_ROLE),
        )


class _AccountPopupView(QListView):
    """Provide the keyboard dismissal expected from a combo popup."""

    def __init__(self, combo: QComboBox) -> None:
        super().__init__(combo)
        self._combo = combo

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self._combo.hidePopup()
            event.accept()
            return
        super().keyPressEvent(event)


class _AccountInfoComboBox(QComboBox):
    """Use one explicit popup view so account selection/context menus are reliable."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._account_popup_view = _AccountPopupView(self)
        self._account_popup_view.setWindowFlags(Qt.Popup)
        self._account_popup_view.setModel(self.model())
        self._account_popup_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self._account_popup_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def view(self):
        return self._account_popup_view

    def showPopup(self) -> None:
        view = self._account_popup_view
        view.setModel(self.model())
        row_height = max(view.sizeHintForRow(0), self.sizeHint().height())
        visible_rows = max(1, min(self.count(), 10))
        metrics = view.fontMetrics()
        projected_widths = [
            metrics.horizontalAdvance(
                account_popup_display_text(
                    self.itemData(row, ACCOUNT_NO_ROLE),
                    self.itemData(row, ACCOUNT_POPUP_MEMO_ROLE),
                )
            )
            for row in range(self.count())
        ]
        canonical_width = metrics.horizontalAdvance(
            account_popup_display_text("8129000000", "가나다라마바사아")
        )
        width = max(
            self.width(),
            canonical_width + 24,
            (max(projected_widths) + 24) if projected_widths else 0,
        )
        height = row_height * visible_rows + 2 * view.frameWidth()
        top_left = self.mapToGlobal(self.rect().bottomLeft())
        view.setGeometry(top_left.x(), top_left.y(), width, height)
        if 0 <= self.currentIndex() < self.count():
            view.setCurrentIndex(self.model().index(self.currentIndex(), 0))
        view.show()
        view.raise_()
        view.setFocus(Qt.PopupFocusReason)
        controller = getattr(self, "_popup_interaction_controller", None)
        if controller is None:
            return
        view.removeEventFilter(controller)
        view.installEventFilter(controller)
        viewport = self.view().viewport()
        viewport.removeEventFilter(controller)
        viewport.installEventFilter(controller)

    def hidePopup(self) -> None:
        self._account_popup_view.hide()

    def hideEvent(self, event) -> None:
        self.hidePopup()
        super().hideEvent(event)


class _AccountComboPopupInteractionController(QObject):
    """Provide reliable selection and context actions on the popup view."""

    def __init__(self, owner, combo: QComboBox) -> None:
        super().__init__(combo.view().viewport())
        self._owner = owner
        self._combo = combo

    def eventFilter(self, watched, event) -> bool:
        view = self._combo.view()
        viewport = view.viewport()
        if watched not in (view, viewport):
            return False
        event_type = event.type()
        if event_type == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            global_position = event.globalPos()
            if not view.rect().contains(view.mapFromGlobal(global_position)):
                combo_clicked = self._combo.rect().contains(
                    self._combo.mapFromGlobal(global_position)
                )
                self._combo.hidePopup()
                return combo_clicked
            if watched is view:
                return False
            return True
        if watched is not viewport:
            return False
        if event_type == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            index = view.indexAt(event.pos())
            if index.isValid():
                self._combo.setCurrentIndex(index.row())
                self._combo.hidePopup()
            return True
        if event_type == QEvent.MouseButtonPress and event.button() == Qt.RightButton:
            index = view.indexAt(event.pos())
            if index.isValid():
                self._owner.open_kiwoom_account_context_menu_for_index(
                    index,
                    event.globalPos(),
                )
            return True
        if event_type == QEvent.MouseButtonRelease and event.button() == Qt.RightButton:
            return True
        if event_type == QEvent.ContextMenu:
            index = view.indexAt(event.pos())
            if index.isValid():
                self._owner.open_kiwoom_account_context_menu_for_index(
                    index,
                    event.globalPos(),
                )
            return True
        return False


from gui_stock_register_window import StockRegisterWindow
from gui_review_required_window import (
    GlobalReviewRequiredWindow,
    collect_global_review_required_rows,
)
from gui_main_emergency_ops import (
    has_emergency_stopped_stock as emergency_has_emergency_stopped_stock,
    update_emergency_button_state as emergency_update_emergency_button_state,
    emergency_review_reason_for_stock as emergency_review_reason_for_stock_impl,
    update_runtime_stock_status as emergency_update_runtime_stock_status,
    execute_emergency_stop as emergency_execute_emergency_stop,
    release_emergency_stop as emergency_release_emergency_stop,
    on_emergency_stop_clicked as emergency_on_emergency_stop_clicked,
)
from gui_main_table_loader import (
    ROUTINE_DEFINITION_ID_ROLE,
    ROUTINE_CHECKBOX_VISUAL_ENABLED_ROLE,
    ROUTINE_CHILD_COLLAPSED_ROLE,
    ROUTINE_CHILD_CHECKBOX_OFFSET,
    ROUTINE_CHILD_HAS_STOCKS_ROLE,
    ROUTINE_CHILD_PROFIT_LED_ROLE,
    ROUTINE_INSTANCE_ID_ROLE,
    ROUTINE_INSTANCE_NAME_WIDTH,
    ROUTINE_INSTANCE_MONEY_OUTER_PADDING,
    ROUTINE_MONITORING_HEADERS,
    ROUTINE_PARENT_AGGREGATE_ROLE,
    ROUTINE_PARENT_AGGREGATE_VALUES_ROLE,
    ROUTINE_PARENT_PROFIT_ROLE,
    ROUTINE_PARENT_COLLAPSED_ROLE,
    ROUTINE_PARENT_NAME_ROLE,
    ROUTINE_COMPLETION_STATUSES,
    ROUTINE_ROW_CHILD,
    ROUTINE_ROW_KIND_ROLE,
    ROUTINE_ROW_PARENT,
    ROUTINE_ROW_STOCK,
    ROUTINE_PARENT_CHECKBOX_OFFSET,
    ROUTINE_PARENT_EXPAND_OFFSET,
    ROUTINE_PARENT_EXPAND_WIDTH,
    ROUTINE_PROFIT_LED_BOX_SIZE,
    ROUTINE_PROFIT_LED_GAP,
    ROUTINE_PROFIT_LED_SIZE,
    MAIN_STOCK_METRIC_LAYOUT_PREVIEW,
    ROUTINE_STOCK_CODE_ROLE,
    ROUTINE_STOCK_INITIAL_BUY_ROLE,
    ROUTINE_STOCK_DISPLAY_ROLE,
    ROUTINE_STOCK_METRICS_ROLE,
    ROUTINE_STOCK_PATH_ROLE,
    ROUTINE_STOCK_PROFIT_LED_ROLE,
    ROUTINE_STOCK_TEXT_OFFSET,
    ROUTINE_STOCK_VALUES_ROLE,
    routine_instance_separator_width,
    routine_aggregate_separator_width,
    routine_instance_grid_columns,
    routine_aggregate_slot_lefts,
    routine_aggregate_label_width,
    routine_aggregate_number_slot_width,
    routine_instance_number_widths,
    main_refresh_pnl_only,
    main_stock_default_reference_price,
    routine_instance_suggested_buy_limits,
    routine_stock_column_widths,
    routine_stock_position_value_widths,
    ROUTINE_STATUS_DEFAULT,
    ROUTINE_STATUS_EARLY_CLOSE,
    ROUTINE_STATUS_IMMEDIATE_LIQUIDATION,
    main_sort_routine_table_by_column,
    main_sort_running_table_by_column,
    main_apply_routine_sort,
    main_apply_running_sort,
    main_load_routine_table,
    main_load_running_stock_table,
    main_monitoring_table_font,
    main_monitoring_cell_font,
)
from pnl_ui_refresh import PNL_REFRESH_INTERVAL_MS
from gui_main_budget_panel import (
    MAIN_TOTAL_BUDGET_PERCENT_OPTIONS,
    collect_main_budget_summary,
    persist_main_total_budget,
    persist_main_budget_percent,
    project_main_budget_warning_transition,
    set_metric_value_text,
    total_budget_from_orderable_cash,
    update_main_budget_panel,
)
from account_funds_foundation import (
    ACCOUNT_AUTHENTICATION_REQUIRED,
    DISCONNECTED as ACCOUNT_FUNDS_DISCONNECTED,
    FAILED as ACCOUNT_FUNDS_FAILED,
    LOADING as ACCOUNT_FUNDS_LOADING,
    READY as ACCOUNT_FUNDS_READY,
    AccountFundsProjection,
    format_money as format_account_funds_money,
)
from kiwoom_account_funds_adapter import KiwoomAccountFundsAdapter
from gui_main_stock_context_menu import (
    MainMonitoringStockOperationAdapter,
    MainMonitoringStockTarget,
    _stock_target_for_row,
    clear_main_monitoring_chart_open_selection,
    show_main_monitoring_stock_context_menu,
)
from gui_auto_trade_context_menu import (
    CONTEXT_MENU_DANGER_TEXT_COLOR,
    set_menu_action_text_color,
)
from gui_auto_trade_integrity import is_operation_excluded
from gui_auto_trade_display import (
    draw_limit_metric,
    draw_stock_position_metric,
    draw_stock_position_metric_display,
    profit_loss_value_color,
)
from gui_auto_trade_run_control import (
    auto_trade_running_registered_operation_targets,
    show_auto_trade_operation_failure_dialog,
)
from gui_auto_trade_policy import (
    auto_trade_current_session_operation_participant_codes,
    operation_policy_section,
)
from buffer_response_coordinator import (
    coordinate_main_window_buffer_response,
    main_window_buffer_response_integration_ready,
    reconcile_main_window_buffer_response_cycle,
    register_main_window_buffer_response_integration,
)
from buffer_response_early_close_dispatcher import (
    resume_main_window_buffer_early_close,
)
from buffer_response_immediate_liquidation_preparer import (
    resume_main_window_buffer_immediate_liquidation_preparation,
)
from buffer_response_immediate_liquidation_dispatcher import (
    dispatch_ready_main_window_buffer_immediate_preparations,
)
from stock_limit_response_service import (
    evaluate_main_window_stock_limit_after_chejan,
    resume_main_window_stock_limit_responses,
)
from routine_limit_response_service import (
    evaluate_main_window_routine_limit_after_chejan,
    resume_main_window_routine_limit_responses,
)
from gui_auto_trade_operation_host import AutoTradeOperationHost
from gui_toast import show_toast
from gui_event_record_window import open_event_record_prototype
from gui_stock_instance_chart_window import (
    CHART_OPEN_STOCK_CODE_COLOR,
    open_stock_instance_chart,
    stock_instance_chart_is_open,
)
from gui_window_policy import close_persistent_feature_windows
from event_journal_production import (
    append_owner_event_once,
    append_production_event,
    observe_owner_failure_transition,
)
from runtime_io import read_json_dict
from routine_order_permission import canonical_stock_trading_time_status
from gui_review_utils import current_price_from_state
from gui_operation_environment import (
    default_buffer_response_policy,
    effective_amount_starting_budget,
    read_buffer_response_policy,
    read_system_budget_policy,
    starting_budget_defaults,
    suggested_buy_limit,
    validate_buffer_response_policy,
    write_buffer_response_policy,
)
from gui_auto_trade_setting_window import (
    AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR,
    AUTO_TRADE_SETTING_BADGE_INACTIVE_COLOR,
    AUTO_TRADE_SETTING_EARLY_CLOSE_BUTTON_STYLE,
    AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT,
    AutoTradeSettingWindow,
    InstanceStockSearchRegisterDialog,
    auto_trade_setting_badge_stylesheet,
    get_routine_dirs,
    get_stock_dirs_in_routine,
    handle_stock_name_operation_exclusion_double_click,
    handle_kiwoom_raw_chejan_event,
    is_emergency_stopped_state,
    is_review_required_state,
    normalize_base_stock_single_routine_file,
    routine_display_name,
)
from gui_routine_registry import routine_record_by_name
from routine_instance_registry import (
    ROUTINE_LIMIT_RESPONSE_ACTION_MODES,
    ROUTINE_LIMIT_RESPONSE_EARLY_CLOSE_PERCENTS,
    ROUTINE_LIMIT_RESPONSE_EVALUATION_FACTORS,
    ROUTINE_LIMIT_RESPONSE_IMMEDIATE_LIQUIDATION_PERCENTS,
    ROUTINE_LIMIT_RESPONSE_SORT_DIRECTIONS,
    default_routine_limit_response_policy,
    routine_definition_by_id,
    routine_instance_by_id,
    validate_routine_limit_response_policy,
)
from routine_instance_repository import RoutineInstanceRepository
from stock_repository import now_text as stock_now_text
from gui_main_routine_selection import (
    routine_definition_enabled,
    routine_instance_checked,
)
from kiwoom_api import KiwoomApi
from operator_reconciliation_service import assess_startup_recovery
from broker_holding_recorder import (
    resolve_account_holding_snapshot_failures,
    write_production_recovery_review,
)
from production_recovery_contract import (
    ACCOUNT_FAILED,
    ACCOUNT_COMPLETED,
    ACCOUNT_REVIEW_REQUIRED,
    BrokerAccountSnapshot,
    BrokerSnapshotPart,
    RecoverySessionIdentity,
    combine_account_snapshot,
    create_recovery_session_identity,
    recovery_request_id,
)
from production_recovery_state_registry import (
    RECOVERY_ACCOUNT_FAILED,
    RECOVERY_ACCOUNT_REVIEW_REQUIRED,
    RECOVERY_CONTEXT_MISSING,
    RECOVERY_IDENTITY_MISMATCH,
    RECOVERY_IN_PROGRESS,
    RECOVERY_NOT_STARTED,
    RECOVERY_STALE_SESSION,
    RECOVERY_STOCK_FAILED,
    RECOVERY_STOCK_PENDING,
    RECOVERY_STOCK_REVIEW_REQUIRED,
    check_production_recovery_gate,
    production_recovery_registry,
    recovery_account_allows_isolated_stock_operation,
    recovery_stock_is_review_required,
    reconcile_production_recovery_snapshot,
)
from startup_runtime_initializer import initialize_pristine_startup_runtime
from operation_command_service import MODE_EARLY_CLOSE
from close_liquidation_transition_service import POLICY_MARKET


PROJECT_ROOT = Path(__file__).resolve().parent
BASE_STOCK_PATH = PROJECT_ROOT / "기초종목.txt"
RECOVERY_ORDER_QUEUE_PATH = PROJECT_ROOT / "runtime" / "order_queue.json"
RECOVERY_POSITIONS_PATH = PROJECT_ROOT / "runtime" / "positions.json"
RECOVERY_BROKER_HOLDINGS_PATH = PROJECT_ROOT / "runtime" / "broker_holdings.json"
EXPECTED_USER_ACTION_RECOVERY_BLOCK_REASONS = frozenset(
    {
        RECOVERY_CONTEXT_MISSING,
        RECOVERY_NOT_STARTED,
        RECOVERY_IN_PROGRESS,
    }
)
MAIN_ROUTINE_BADGE_IDLE_TEXT_COLOR = "#404040"
MAIN_PERFORMANCE_CUMULATIVE_TITLE_COLOR = "#22B14C"
MAIN_PERFORMANCE_CURRENT_TITLE_COLOR = "#FF7F27"
MAIN_PERFORMANCE_PROFIT_COLOR = "#ED1C24"
MAIN_PERFORMANCE_LOSS_COLOR = "#3F48CC"
MAIN_ROUTINE_METRIC_KEYS_BY_LEVEL = {
    "group": frozenset(),
    "routine": frozenset(("profit", "limit")),
    "stock": frozenset(("holding", "price", "profit", "trade", "limit")),
}
ROUTINE_INLINE_EDIT_STYLE = """
QLineEdit {
    border: none;
    background: transparent;
    padding: 0px;
    margin: 0px;
}
QLineEdit:focus {
    background: transparent;
}
"""


def _routine_parent_font(base_font: QFont) -> QFont:
    font = QFont(base_font)
    if font.pointSizeF() > 0:
        font.setPointSizeF(font.pointSizeF() + 1.0)
    elif font.pixelSize() > 0:
        font.setPixelSize(font.pixelSize() + 1)
    return font


def _routine_profit_led_color(led_state: object) -> str:
    return {
        "red": "#DC2626",
        "yellow": "#D97706",
        "green": "#16A34A",
        "gray": "#9CA3AF",
    }.get(str(led_state or "gray"), "#9CA3AF")


def _draw_routine_profit_led(
    painter,
    *,
    row_rect: QRect,
    led_box_left: int,
    led_state: object,
    visually_enabled: bool,
    square: bool = False,
) -> None:
    led_box_top = (
        row_rect.top()
        + (row_rect.height() - ROUTINE_PROFIT_LED_BOX_SIZE) // 2
    )
    led_rect = QRect(
        led_box_left + (ROUTINE_PROFIT_LED_BOX_SIZE - ROUTINE_PROFIT_LED_SIZE) // 2,
        led_box_top + (ROUTINE_PROFIT_LED_BOX_SIZE - ROUTINE_PROFIT_LED_SIZE) // 2,
        ROUTINE_PROFIT_LED_SIZE,
        ROUTINE_PROFIT_LED_SIZE,
    )
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    if not visually_enabled:
        painter.setOpacity(0.45)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(_routine_profit_led_color(led_state)))
    if square:
        square_size = max(8, ROUTINE_PROFIT_LED_SIZE - 2)
        square_rect = QRect(
            led_rect.center().x() - square_size // 2,
            led_rect.center().y() - square_size // 2,
            square_size,
            square_size,
        )
        painter.drawRect(square_rect)
    else:
        painter.drawEllipse(led_rect)
    painter.restore()


MAIN_STOCK_METRIC_LAYOUT = {
    "separator_spacing": 6,
    "metrics": (
        {
            "key": "holding",
            "max_text": "\ubcf4\uc720(99999\uc8fc / 999,999,999)",
        },
        {
            "key": "price",
            "max_text": "\uac00\uaca9(9,999,999 / 9,999,999)",
        },
        {
            "key": "profit",
            "max_text": "\uc218\uc775(-99,999,999 / -00.00%)",
        },
        {
            "key": "trade",
            "max_text": "\ub9e4\ub9e4(99 / 99)",
        },
        {
            "key": "limit",
            "max_text": "\ud55c\ub3c4(999,999,999)",
        },
        {
            "key": "consumed",
            "max_text": "\uc18c\ubaa8(999,999,999 / 00.0%)",
        },
    ),
}
ROUTINE_STOCK_METRIC_SEPARATOR_GAP = int(
    MAIN_STOCK_METRIC_LAYOUT["separator_spacing"]
)
MAIN_STOCK_METRIC_MAX_TEXTS = tuple(
    str(metric["max_text"]) for metric in MAIN_STOCK_METRIC_LAYOUT["metrics"]
)
ROUTINE_STOCK_METRIC_SEPARATOR_WIDTH = 4


def _main_stock_metric_slot_widths(metrics: QFontMetrics) -> tuple[int, ...]:
    return tuple(
        metrics.horizontalAdvance(str(metric["max_text"]))
        for metric in MAIN_STOCK_METRIC_LAYOUT["metrics"]
    )


MAIN_STOCK_METRIC_SLOT_WIDTHS = (231, 221, 226, 105, 144, 210)


def _routine_stock_metric_display_text(metric: object) -> str:
    label = str(getattr(metric, "label", "") or "").strip()
    value1 = str(getattr(metric, "value1", "") or "").strip()
    value2 = str(getattr(metric, "value2", "") or "").strip()
    if not label:
        return ""
    return f"{label}({value1} / {value2})"


INITIAL_BUY_BADGE_WIDTH = 64
INITIAL_BUY_BADGE_HEIGHT = AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT
INITIAL_BUY_BADGE_GAP = 4
MAIN_ROUTINE_FILTER_BADGE_WIDTH = 64
MAIN_ROUTINE_FILTER_BADGE_AREA_WIDTH = 68
MAIN_ROUTINE_SUMMARY_VALID_BADGE_WIDTH = 68
INITIAL_BUY_AMOUNT_COLOR = "#B98200"
INITIAL_BUY_QUANTITY_COLOR = "#6F52B5"
ROUTINE_STOCK_OPERATION_TOKEN_INDEX = 2
ROUTINE_STOCK_NAME_TOKEN_INDEX = 0


def _routine_stock_token_rect(table, index, token_index: int) -> QRect:
    if table is None or not index.isValid() or token_index < 0:
        return QRect()
    values = index.data(ROUTINE_STOCK_VALUES_ROLE)
    if not isinstance(values, (list, tuple)) or token_index >= len(values):
        return QRect()
    row_rect = table.visualRect(index)
    if row_rect.isNull():
        return QRect()
    x = row_rect.left() + ROUTINE_STOCK_TEXT_OFFSET
    separator_width = routine_instance_separator_width(table.font())
    for column, width in enumerate(routine_stock_column_widths(table.font())[: len(values)]):
        if column > 0 and column != 1:
            x += separator_width
        token_rect = QRect(x, row_rect.top(), width, row_rect.height())
        if column == token_index:
            return token_rect
        x += width
    return QRect()


def _routine_stock_name_rect(table, index) -> QRect:
    token_rect = _routine_stock_token_rect(
        table,
        index,
        ROUTINE_STOCK_NAME_TOKEN_INDEX,
    )
    if token_rect.isNull():
        return QRect()
    code = str(index.data(ROUTINE_STOCK_CODE_ROLE) or "").strip()
    text_left = (
        token_rect.left()
        + ROUTINE_PROFIT_LED_BOX_SIZE
        + ROUTINE_PROFIT_LED_GAP
    )
    if code:
        code_font, _code_color = _routine_stock_code_chart_style(table.font(), code)
        text_left += QFontMetrics(code_font).horizontalAdvance(f"{code} ")
    return QRect(
        text_left,
        token_rect.top(),
        max(0, token_rect.right() - text_left + 1),
        token_rect.height(),
    )


def _routine_stock_code_rect(table, index) -> QRect:
    token_rect = _routine_stock_token_rect(
        table,
        index,
        ROUTINE_STOCK_NAME_TOKEN_INDEX,
    )
    if token_rect.isNull():
        return QRect()
    code = str(index.data(ROUTINE_STOCK_CODE_ROLE) or "").strip()
    if not code:
        return QRect()
    text_left = (
        token_rect.left()
        + ROUTINE_PROFIT_LED_BOX_SIZE
        + ROUTINE_PROFIT_LED_GAP
    )
    code_font, _code_color = _routine_stock_code_chart_style(table.font(), code)
    return QRect(
        text_left,
        token_rect.top(),
        QFontMetrics(code_font).horizontalAdvance(code),
        token_rect.height(),
    )


def _routine_stock_code_chart_style(
    base_font: QFont,
    stock_code: str,
) -> tuple[QFont, QColor | None]:
    font = QFont(base_font)
    if not stock_instance_chart_is_open(stock_code):
        return font, None
    return font, QColor(CHART_OPEN_STOCK_CODE_COLOR)


def _initial_buy_component_rects(cell_rect: QRect) -> dict[str, QRect]:
    badge_rect = QRect(
        cell_rect.left(),
        cell_rect.top() + max(0, (cell_rect.height() - INITIAL_BUY_BADGE_HEIGHT) // 2),
        INITIAL_BUY_BADGE_WIDTH,
        min(INITIAL_BUY_BADGE_HEIGHT, cell_rect.height()),
    )
    value_left = badge_rect.right() + 1 + INITIAL_BUY_BADGE_GAP
    value_right = cell_rect.right() - 1
    value_rect = QRect(
        value_left,
        cell_rect.top(),
        max(0, value_right - value_left + 1),
        cell_rect.height(),
    )
    return {"badge": badge_rect, "value": value_rect}


def _initial_buy_badge_font() -> QFont:
    font = QApplication.font("QPushButton")
    font.setWeight(QFont.DemiBold)
    return font


def _draw_initial_buy_display(
    painter: QPainter,
    cell_rect: QRect,
    initial_buy: dict[str, object],
    *,
    hide_value: bool = False,
) -> None:
    components = _initial_buy_component_rects(cell_rect)
    mode = str(initial_buy.get("mode", "QUANTITY") or "QUANTITY").upper()
    badge_text = "금액" if mode == "AMOUNT" else "주수"
    badge_color = INITIAL_BUY_AMOUNT_COLOR if mode == "AMOUNT" else INITIAL_BUY_QUANTITY_COLOR

    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    outline_color = QColor(badge_color)
    painter.setPen(QPen(outline_color, 1))
    painter.setBrush(Qt.NoBrush)
    painter.drawRoundedRect(components["badge"], 4, 4)
    painter.setPen(outline_color)
    painter.setFont(_initial_buy_badge_font())
    painter.drawText(components["badge"], Qt.AlignCenter, badge_text)
    painter.restore()

    if not hide_value:
        value_text = str(initial_buy.get("value_text", "") or "")
        painter.drawText(
            components["value"],
            (
                Qt.AlignCenter
                if value_text == "대기"
                else Qt.AlignRight | Qt.AlignVCenter
            ),
            value_text,
        )


def _routine_stock_metric_texts_legacy_unused(values: list[object], metrics_data: tuple[object, ...]) -> list[str]:
    if MAIN_STOCK_METRIC_LAYOUT_PREVIEW:
        return list(MAIN_STOCK_METRIC_MAX_TEXTS)
    texts: list[str] = []
    for metric in metrics_data[:4]:
        metric_text = _routine_stock_metric_display_text(metric)
        if metric_text:
            texts.append(metric_text)
    initial_buy_offset = (
        1
        if len(values) > 1 and str(values[1] or "").startswith(("금액 ", "주수 "))
        else 0
    )
    limit_index = 10 + initial_buy_offset
    consumed_index = 11 + initial_buy_offset
    if len(values) > limit_index:
        texts.append(str(values[limit_index] or "").strip())
    if len(metrics_data) > 5:
        consumed_text = _routine_stock_metric_display_text(metrics_data[5])
        if consumed_text:
            texts.append(consumed_text)
    elif len(values) > consumed_index:
        texts.append(str(values[consumed_index] or "").strip())
    else:
        texts.append("소모(0 / 0.0%)")
    return [text for text in texts if text]


def _routine_stock_metric_texts(values: list[object], metrics_data: tuple[object, ...]) -> list[str]:
    if MAIN_STOCK_METRIC_LAYOUT_PREVIEW:
        return list(MAIN_STOCK_METRIC_MAX_TEXTS)

    texts: list[str] = []
    for metric in metrics_data[:4]:
        metric_text = _routine_stock_metric_display_text(metric)
        if metric_text:
            texts.append(metric_text)
    initial_buy_offset = (
        1
        if len(values) > 1 and str(values[1] or "").startswith(("금액 ", "주수 "))
        else 0
    )
    limit_index = 10 + initial_buy_offset
    consumed_index = 11 + initial_buy_offset
    if len(values) > limit_index:
        texts.append(str(values[limit_index] or "").strip())
    if len(metrics_data) > 5:
        consumed_text = _routine_stock_metric_display_text(metrics_data[5])
        if consumed_text:
            texts.append(consumed_text)
    elif len(values) > consumed_index:
        texts.append(str(values[consumed_index] or "").strip())
    return [text for text in texts if text]


def _routine_stock_metric_layout_rects(
    *,
    row_rect: QRect,
    start_x: int,
    count: int,
    metrics: QFontMetrics | None = None,
) -> tuple[list[QRect], list[QRect], int]:
    metric_rects: list[QRect] = []
    separator_rects: list[QRect] = []
    x = start_x
    gap = ROUTINE_STOCK_METRIC_SEPARATOR_GAP
    slot_widths = (
        _main_stock_metric_slot_widths(metrics)
        if metrics is not None
        else MAIN_STOCK_METRIC_SLOT_WIDTHS
    )
    separator_width = (
        max(1, metrics.horizontalAdvance("|"))
        if metrics is not None
        else ROUTINE_STOCK_METRIC_SEPARATOR_WIDTH
    )
    for index, slot_width in enumerate(slot_widths[:count]):
        metric_rect = QRect(x, row_rect.top(), slot_width, row_rect.height())
        metric_rects.append(metric_rect)
        x += slot_width
        if index < count - 1:
            separator_rect = QRect(x + gap, row_rect.top(), separator_width, row_rect.height())
            separator_rects.append(separator_rect)
            x = separator_rect.left() + separator_width + gap
    return metric_rects, separator_rects, x


def _split_main_stock_metric_text(text: str) -> tuple[str, str, str | None]:
    value = str(text or "").strip()
    if "(" not in value or not value.endswith(")"):
        return value, "", None
    label, inner = value.split("(", 1)
    inner = inner[:-1]
    if " / " in inner:
        left_value, right_value = inner.split(" / ", 1)
        return label, left_value, right_value
    return label, inner, None


def _main_stock_metric_component_rects(
    metrics,
    metric_rect: QRect,
    layout_metric: dict[str, object],
) -> dict[str, QRect]:
    max_label, max_left_value, max_right_value = _split_main_stock_metric_text(
        str(layout_metric["max_text"])
    )
    x = metric_rect.left()
    label_rect = QRect(x, metric_rect.top(), metrics.horizontalAdvance(max_label), metric_rect.height())
    x += label_rect.width()
    open_paren_rect = QRect(x, metric_rect.top(), metrics.horizontalAdvance("("), metric_rect.height())
    x += open_paren_rect.width()
    left_value_rect = QRect(
        x,
        metric_rect.top(),
        metrics.horizontalAdvance(max_left_value),
        metric_rect.height(),
    )
    x += left_value_rect.width()
    if max_right_value is None:
        close_paren_rect = QRect(x, metric_rect.top(), metrics.horizontalAdvance(")"), metric_rect.height())
        return {
            "label": label_rect,
            "open_paren": open_paren_rect,
            "left_value": left_value_rect,
            "close_paren": close_paren_rect,
        }

    slash_rect = QRect(x, metric_rect.top(), metrics.horizontalAdvance(" / "), metric_rect.height())
    x += slash_rect.width()
    right_value_rect = QRect(
        x,
        metric_rect.top(),
        metrics.horizontalAdvance(max_right_value),
        metric_rect.height(),
    )
    x += right_value_rect.width()
    close_paren_rect = QRect(x, metric_rect.top(), metrics.horizontalAdvance(")"), metric_rect.height())
    return {
        "label": label_rect,
        "open_paren": open_paren_rect,
        "left_value": left_value_rect,
        "slash": slash_rect,
        "right_value": right_value_rect,
        "close_paren": close_paren_rect,
    }


def _main_stock_metric_component_layouts(
    metrics,
    metric_rects: list[QRect],
) -> list[dict[str, QRect]]:
    return [
        _main_stock_metric_component_rects(metrics, metric_rect, layout_metric)
        for metric_rect, layout_metric in zip(metric_rects, MAIN_STOCK_METRIC_LAYOUT["metrics"])
    ]


def _main_stock_value_alignment(value: str):
    if str(value).strip() in {"-", "\ubbf8\uc124\uc815", "대기"}:
        return Qt.AlignCenter | Qt.AlignVCenter
    return Qt.AlignRight | Qt.AlignVCenter


def _draw_main_stock_metric_components(
    painter,
    text: str,
    rects: dict[str, QRect],
    *,
    hide_left_value: bool = False,
) -> None:
    label, left_value, right_value = _split_main_stock_metric_text(text)
    painter.drawText(rects["label"], Qt.AlignLeft | Qt.AlignVCenter, label)
    painter.drawText(rects["open_paren"], Qt.AlignCenter | Qt.AlignVCenter, "(")
    if not hide_left_value:
        painter.drawText(rects["left_value"], _main_stock_value_alignment(left_value), left_value)
    if right_value is not None and "slash" in rects and "right_value" in rects:
        painter.drawText(rects["slash"], Qt.AlignCenter | Qt.AlignVCenter, " / ")
        painter.drawText(rects["right_value"], _main_stock_value_alignment(right_value), right_value)
    painter.drawText(rects["close_paren"], Qt.AlignCenter | Qt.AlignVCenter, ")")


def _draw_routine_stock_metric_text_sequence(
    painter,
    *,
    row_rect: QRect,
    start_x: int,
    texts: list[str],
    foregrounds: list[QColor | None] | None = None,
    hidden_value_indexes: set[int] | None = None,
) -> tuple[list[tuple[str, int, int, int, int]], int]:
    metric_rects, separator_rects, end_x = _routine_stock_metric_layout_rects(
        row_rect=row_rect,
        start_x=start_x,
        count=len(texts),
        metrics=painter.fontMetrics(),
    )
    component_rects = _main_stock_metric_component_layouts(painter.fontMetrics(), metric_rects)
    layout_rows: list[tuple[str, int, int, int, int]] = []
    hidden_value_indexes = hidden_value_indexes or set()
    for index, (text, metric_rect, rects) in enumerate(zip(texts, metric_rects, component_rects)):
        original_pen = painter.pen()
        if foregrounds is not None and index < len(foregrounds):
            foreground = foregrounds[index]
            if isinstance(foreground, QColor) and foreground.isValid():
                painter.setPen(foreground)
        _draw_main_stock_metric_components(
            painter,
            text,
            rects,
            hide_left_value=index in hidden_value_indexes,
        )
        painter.setPen(original_pen)
        text_start = metric_rect.left()
        text_end = metric_rect.left() + metric_rect.width()
        if index < len(separator_rects):
            separator_rect = separator_rects[index]
            next_text_start = (
                separator_rect.left()
                + separator_rect.width()
                + ROUTINE_STOCK_METRIC_SEPARATOR_GAP
            )
            layout_rows.append((text, text_start, text_end, separator_rect.left(), next_text_start))
        else:
            layout_rows.append((text, text_start, text_end, -1, -1))
    for separator_rect in separator_rects:
        painter.drawText(
            separator_rect,
            Qt.AlignCenter | Qt.AlignVCenter,
            "|",
        )
    return layout_rows, end_x


def _apply_routine_inline_edit_style(editor: QLineEdit, table) -> None:
    editor.setFrame(False)
    editor.setStyleSheet(ROUTINE_INLINE_EDIT_STYLE)
    editor.setFont(table.font())
    editor.setContentsMargins(0, 0, 0, 0)


def _create_routine_operation_confirmation(
    parent: QWidget | None,
    display_status: str,
    icon: QMessageBox.Icon = QMessageBox.Question,
) -> QMessageBox:
    if display_status == MODE_EARLY_CLOSE:
        display_status = ROUTINE_STATUS_EARLY_CLOSE
    title, message = {
        ROUTINE_STATUS_EARLY_CLOSE: ("조기마감", "조기마감을 적용합니다."),
        ROUTINE_STATUS_IMMEDIATE_LIQUIDATION: ("즉시청산", "즉시청산을 적용합니다."),
    }[display_status]
    dialog = QMessageBox(
        icon,
        title,
        message,
        QMessageBox.Yes | QMessageBox.No,
        parent,
    )
    dialog.setDefaultButton(QMessageBox.No)
    dialog.button(QMessageBox.Yes).setText("진행")
    dialog.button(QMessageBox.No).setText("취소")
    return dialog


class _RoutineTreeInteractionController(QObject):
    """Handle routine tree hover, expansion, editing, and metric interactions."""

    def __init__(self, window) -> None:
        super().__init__(window.routine_table)
        self.window = window
        self.table = window.routine_table

    def _set_parent_name_hover(self, definition_id: str) -> None:
        current = str(
            getattr(self.table, "_hovered_routine_definition_id", "") or ""
        )
        if definition_id == current:
            return
        self.table._hovered_routine_definition_id = definition_id
        self.table.viewport().update()

    def _parent_name_rect(self, index) -> QRect:
        cell_rect = self.table.visualRect(index)
        name = str(index.data(ROUTINE_PARENT_NAME_ROLE) or "")
        prefix = "▶ " if index.data(ROUTINE_PARENT_COLLAPSED_ROLE) else "▼ "
        metrics = QFontMetrics(_routine_parent_font(self.table.font()))
        text_left = (
            cell_rect.left()
            + ROUTINE_PARENT_CHECKBOX_OFFSET
        )
        name_left = text_left + metrics.horizontalAdvance(prefix)
        return QRect(
            name_left,
            cell_rect.top(),
            metrics.horizontalAdvance(name),
            cell_rect.height(),
        )

    def _child_name_rect(self, index) -> QRect:
        cell_rect = self.table.visualRect(index)
        name = str(index.data(Qt.DisplayRole) or "")
        metrics = QFontMetrics(self.table.font())
        text_left = (
            cell_rect.left()
            + ROUTINE_CHILD_CHECKBOX_OFFSET
            + ROUTINE_PROFIT_LED_BOX_SIZE
            + ROUTINE_PROFIT_LED_GAP
        )
        name_left = text_left + metrics.horizontalAdvance("▶") + 4
        return QRect(
            name_left,
            cell_rect.top(),
            metrics.horizontalAdvance(name),
            cell_rect.height(),
        )

    def _child_expand_rect(self, index) -> QRect:
        if not bool(index.data(ROUTINE_CHILD_HAS_STOCKS_ROLE)):
            return QRect()
        cell_rect = self.table.visualRect(index)
        metrics = QFontMetrics(self.table.font())
        arrow_width = metrics.horizontalAdvance("▶") + 4
        expand_left = (
            cell_rect.left()
            + ROUTINE_CHILD_CHECKBOX_OFFSET
        )
        return QRect(
            expand_left,
            cell_rect.top(),
            arrow_width,
            cell_rect.height(),
        )

    def _stock_metric_rect(self, index, target_column: int) -> QRect:
        if target_column == 11:
            return self._stock_main_metric_rect(index, 4)
        return self._stock_legacy_metric_rect(index, target_column)

    def _stock_main_metric_rect(self, index, metric_index: int) -> QRect:
        if metric_index < 0 or metric_index >= len(MAIN_STOCK_METRIC_SLOT_WIDTHS):
            return QRect()
        base_metric_rect = self._stock_legacy_metric_rect(index, 7)
        if base_metric_rect.isNull():
            return QRect()
        metric_rects, _separator_rects, _end_x = _routine_stock_metric_layout_rects(
            row_rect=self.table.visualRect(index),
            start_x=base_metric_rect.left() + ROUTINE_STOCK_METRIC_SEPARATOR_GAP,
            count=metric_index + 1,
            metrics=QFontMetrics(self.table.font()),
        )
        return metric_rects[metric_index] if metric_index < len(metric_rects) else QRect()

    def _stock_legacy_metric_rect(self, index, target_column: int) -> QRect:
        cell_rect = self.table.visualRect(index)
        values = index.data(ROUTINE_STOCK_VALUES_ROLE)
        if not isinstance(values, (list, tuple)):
            return QRect()
        if target_column >= len(values):
            return QRect()
        x = cell_rect.left() + ROUTINE_STOCK_TEXT_OFFSET
        separator_width = routine_instance_separator_width(self.table.font())
        for column, width in enumerate(routine_stock_column_widths(self.table.font())[: len(values)]):
            if column > 0 and column != 1:
                x += separator_width
            rect = QRect(x, cell_rect.top(), width, cell_rect.height())
            if column == target_column:
                return rect
            x += width
        return QRect()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.MouseMove:
            index = self.table.indexAt(event.pos())
            definition_id = ""
            if (
                index.isValid()
                and index.column() == 0
                and str(index.data(ROUTINE_ROW_KIND_ROLE) or "") == ROUTINE_ROW_PARENT
                and self._parent_name_rect(index).contains(event.pos())
            ):
                definition_id = str(
                    index.data(ROUTINE_DEFINITION_ID_ROLE) or ""
                ).strip()
            self._set_parent_name_hover(definition_id)
        elif event.type() == QEvent.Leave:
            self._set_parent_name_hover("")

        if event.type() in {
            QEvent.MouseButtonPress,
            QEvent.MouseButtonRelease,
            QEvent.MouseButtonDblClick,
        }:
            index = self.table.indexAt(event.pos())
            if index.isValid() and index.column() == 0:
                cell_rect = self.table.visualRect(index)
                row_kind = str(index.data(ROUTINE_ROW_KIND_ROLE) or "")
                if row_kind == ROUTINE_ROW_STOCK:
                    limit_rect = self._stock_metric_rect(index, 11)
                    if (
                        event.type() == QEvent.MouseButtonRelease
                        and event.button() == Qt.LeftButton
                        and limit_rect.contains(event.pos())
                    ):
                        if not self.window.consume_routine_stock_buy_limit_release(index.row()):
                            self.window.schedule_routine_stock_buy_limit_single_click(index.row())
                        event.accept()
                        return True
                    if (
                        event.type() == QEvent.MouseButtonDblClick
                        and event.button() == Qt.LeftButton
                    ):
                        if _routine_stock_code_rect(
                            self.table,
                            index,
                        ).contains(event.pos()):
                            if self.window.handle_routine_stock_code_double_click(
                                index.row()
                            ):
                                event.accept()
                                return True
                            return super().eventFilter(watched, event)
                        operation_rect = _routine_stock_token_rect(
                            self.table,
                            index,
                            ROUTINE_STOCK_OPERATION_TOKEN_INDEX,
                        )
                        if operation_rect.contains(event.pos()):
                            if self.window.handle_routine_stock_operation_double_click(index.row()):
                                event.accept()
                                return True
                            return super().eventFilter(watched, event)
                        if _routine_stock_name_rect(
                            self.table,
                            index,
                        ).contains(event.pos()):
                            if self.window.handle_routine_stock_name_double_click(
                                index.row()
                            ):
                                event.accept()
                                return True
                            return super().eventFilter(watched, event)
                        if not self.window._main_routine_initial_buy_badge_enabled():
                            return super().eventFilter(watched, event)
                        initial_buy_rect = self._stock_legacy_metric_rect(index, 1)
                        initial_buy_parts = _initial_buy_component_rects(initial_buy_rect)
                        if initial_buy_parts["badge"].contains(event.pos()):
                            self.window.toggle_routine_stock_initial_buy_mode(index.row())
                            event.accept()
                            return True
                        if initial_buy_parts["value"].contains(event.pos()):
                            self.window.start_routine_stock_initial_buy_edit(index.row())
                            event.accept()
                            return True
                        if limit_rect.contains(event.pos()):
                            self.window.cancel_routine_stock_buy_limit_single_click(
                                suppress_release_row=index.row(),
                            )
                            self.window.handle_routine_stock_buy_limit_double_click(index.row())
                            event.accept()
                            return True
                    return super().eventFilter(watched, event)
                if row_kind not in {ROUTINE_ROW_PARENT, ROUTINE_ROW_CHILD}:
                    return super().eventFilter(watched, event)
                expand_left = cell_rect.left() + ROUTINE_PARENT_EXPAND_OFFSET
                expand_right = expand_left + ROUTINE_PARENT_EXPAND_WIDTH
                if (
                    row_kind == ROUTINE_ROW_PARENT
                    and expand_left <= event.pos().x() <= expand_right
                ):
                    if event.type() == QEvent.MouseButtonPress:
                        self.window.toggle_routine_expansion(index.row())
                    event.accept()
                    return True
                if (
                    row_kind == ROUTINE_ROW_CHILD
                    and self._child_expand_rect(index).contains(event.pos())
                ):
                    if event.type() == QEvent.MouseButtonPress:
                        self.window.toggle_routine_instance_expansion(index.row())
                    event.accept()
                    return True
                if (
                    event.type() == QEvent.MouseButtonDblClick
                    and event.button() == Qt.LeftButton
                ):
                    if (
                        row_kind == ROUTINE_ROW_CHILD
                        and self._child_name_rect(index).contains(event.pos())
                    ):
                        if self.window.handle_routine_instance_name_double_click(index.row()):
                            event.accept()
                            return True
                        return super().eventFilter(watched, event)
                    if (
                        row_kind == ROUTINE_ROW_PARENT
                        and self._parent_name_rect(index).contains(event.pos())
                    ):
                        if self.window.handle_routine_group_name_double_click(index.row()):
                            event.accept()
                            return True
                        return super().eventFilter(watched, event)
                    event.accept()
                    return True
            elif event.type() == QEvent.MouseButtonDblClick:
                event.accept()
                return True
        return super().eventFilter(watched, event)


class _RoutineInstanceNameEdit(QLineEdit):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window.routine_table.viewport())
        self.window = window

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.window.finish_routine_instance_name_edit(save=True)
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            self.window.finish_routine_instance_name_edit(save=False)
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        self.window.finish_routine_instance_name_edit(save=True)
        super().focusOutEvent(event)


class _RoutineBuyLimitValueEditFilter(QObject):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self.window = window

    def eventFilter(self, watched, event):
        object_name = watched.objectName()
        if object_name == "routineInstanceBuyLimitAmount":
            if (
                event.type() == QEvent.MouseButtonRelease
                and event.button() == Qt.LeftButton
            ):
                if not self.window.consume_routine_instance_buy_limit_release(watched):
                    self.window.schedule_routine_instance_buy_limit_single_click(watched)
                event.accept()
                return True
            if (
                event.type() == QEvent.MouseButtonDblClick
                and event.button() == Qt.LeftButton
            ):
                self.window.cancel_routine_instance_buy_limit_single_click(
                    suppress_release_widget=watched,
                )
                self.window.handle_routine_instance_buy_limit_double_click(watched)
                event.accept()
                return True
        if (
            object_name == "routineInstanceBuyLimitSettings"
            and event.type() == QEvent.MouseButtonRelease
            and event.button() == Qt.LeftButton
        ):
            self.window.handle_routine_instance_buy_limit_settings_click(watched)
            event.accept()
            return True
        if object_name == "routineInstanceBuyLimitEditor":
            if event.type() == QEvent.KeyPress:
                if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                    self.window.finish_routine_instance_buy_limit_edit(save=True)
                    event.accept()
                    return True
                if event.key() == Qt.Key_Escape:
                    self.window.finish_routine_instance_buy_limit_edit(save=False)
                    event.accept()
                    return True
            if event.type() == QEvent.FocusOut:
                self.window.finish_routine_instance_buy_limit_edit(save=True)
        if object_name == "routineStockBuyLimitEditor":
            if event.type() == QEvent.KeyPress:
                if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                    self.window.finish_routine_stock_buy_limit_edit(save=True)
                    event.accept()
                    return True
                if event.key() == Qt.Key_Escape:
                    self.window.finish_routine_stock_buy_limit_edit(save=False)
                    event.accept()
                    return True
            if event.type() == QEvent.FocusOut:
                self.window.finish_routine_stock_buy_limit_edit(save=True)
        if object_name == "routineStockInitialBuyEditor":
            if event.type() == QEvent.KeyPress:
                if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                    self.window.finish_routine_stock_initial_buy_edit(save=True)
                    event.accept()
                    return True
                if event.key() == Qt.Key_Escape:
                    self.window.finish_routine_stock_initial_buy_edit(save=False)
                    event.accept()
                    return True
            if event.type() == QEvent.FocusOut:
                self.window.finish_routine_stock_initial_buy_edit(save=True)
        return super().eventFilter(watched, event)


class _RoutineTreeItemDelegate(QStyledItemDelegate):
    """Paint the first-column hierarchy without text-based indentation."""

    @staticmethod
    def _child_arrow_state(has_stocks: bool, collapsed: bool) -> tuple[str, bool]:
        if not has_stocks:
            return "▶", False
        return ("▶" if collapsed else "▼"), True

    @staticmethod
    def _stock_token(tokens: object, column: int) -> dict[str, object]:
        if isinstance(tokens, (list, tuple)) and column < len(tokens):
            token = tokens[column]
            if isinstance(token, dict):
                return token
        return {}

    @staticmethod
    def _stock_token_font(base_font: QFont, token: dict[str, object]) -> QFont:
        font = QFont(base_font)
        if "bold" in token:
            font.setBold(bool(token.get("bold")))
        if "italic" in token:
            font.setItalic(bool(token.get("italic")))
        point_size = token.get("point_size")
        try:
            point_size_int = int(point_size)
        except (TypeError, ValueError):
            point_size_int = 0
        if point_size_int > 0:
            font.setPointSize(point_size_int)
        return font

    @staticmethod
    def _stock_token_alignment(
        token: dict[str, object],
        fallback: Qt.Alignment,
    ) -> Qt.Alignment:
        try:
            alignment = int(token.get("alignment", 0) or 0)
        except (TypeError, ValueError):
            alignment = 0
        return Qt.Alignment(alignment) if alignment else fallback

    @staticmethod
    def _stock_token_foreground(
        token: dict[str, object],
        option,
        *,
        visually_enabled: bool,
    ) -> QColor:
        if option.state & QStyle.State_Selected:
            return option.palette.highlightedText().color()
        color_text = str(token.get("foreground", "") or "").strip()
        color = QColor(color_text)
        if color.isValid():
            return color
        if not visually_enabled:
            return QColor("#9ca3af")
        return option.palette.text().color()

    @staticmethod
    def _fill_stock_token_background(
        painter,
        rect: QRect,
        token: dict[str, object],
        option,
    ) -> None:
        if option.state & QStyle.State_Selected:
            return
        color_text = str(token.get("background", "") or "").strip()
        color = QColor(color_text)
        if not color.isValid():
            return
        painter.fillRect(rect.adjusted(1, 2, -1, -2), color)

    def display_text(self, index, widget) -> str:
        display_text = str(index.data(Qt.DisplayRole) or "")
        if str(index.data(ROUTINE_ROW_KIND_ROLE) or "") != ROUTINE_ROW_PARENT:
            return display_text
        definition_id = str(index.data(ROUTINE_DEFINITION_ID_ROLE) or "")
        aggregate = str(index.data(ROUTINE_PARENT_AGGREGATE_ROLE) or "")
        collapsed = bool(index.data(ROUTINE_PARENT_COLLAPSED_ROLE))
        hovered = str(
            getattr(widget, "_hovered_routine_definition_id", "") or ""
        )
        if aggregate and (collapsed or definition_id == hovered):
            return f"{display_text}    {aggregate}"
        return display_text

    def paint(self, painter, option, index):
        base_option = QStyleOptionViewItem(option)
        self.initStyleOption(base_option, index)
        base_option.text = ""
        base_option.features &= ~QStyleOptionViewItem.HasCheckIndicator
        style = option.widget.style() if option.widget is not None else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, base_option, painter, option.widget)

        row_kind = str(index.data(ROUTINE_ROW_KIND_ROLE) or "")
        if row_kind == ROUTINE_ROW_STOCK:
            painter.save()
            painter.setFont(option.font)
            visually_enabled = index.data(ROUTINE_CHECKBOX_VISUAL_ENABLED_ROLE) is not False
            if not visually_enabled:
                painter.setPen(QColor("#9ca3af"))
            elif option.state & QStyle.State_Selected:
                painter.setPen(option.palette.highlightedText().color())
            else:
                painter.setPen(option.palette.text().color())
            values = index.data(ROUTINE_STOCK_VALUES_ROLE)
            if not isinstance(values, (list, tuple)):
                values = [self.display_text(index, option.widget)]
            display_tokens = index.data(ROUTINE_STOCK_DISPLAY_ROLE)
            if not isinstance(display_tokens, (list, tuple)):
                display_tokens = ()
            metrics_data = index.data(ROUTINE_STOCK_METRICS_ROLE)
            if not isinstance(metrics_data, (list, tuple)):
                metrics_data = ()
            separator_width = routine_instance_separator_width(painter.font())
            stock_column_widths = routine_stock_column_widths(painter.font())
            stock_position_value_widths = routine_stock_position_value_widths(painter.font())
            visible_stock_column_widths = stock_column_widths[: len(values)]
            for column, width in enumerate(visible_stock_column_widths):
                token = self._stock_token(display_tokens, column)
                text = str(
                    token.get("text")
                    if token.get("text") not in (None, "")
                    else values[column] if column < len(values) else ""
                )
                cell_rect = _routine_stock_token_rect(
                    option.widget,
                    index,
                    column,
                )
                if cell_rect.isNull():
                    continue
                if column > 0 and column != 1:
                    separator_rect = QRect(
                        cell_rect.left() - separator_width,
                        option.rect.top(),
                        separator_width,
                        option.rect.height(),
                    )
                    painter.drawText(
                        separator_rect,
                        Qt.AlignCenter,
                        "|",
                    )
                self._fill_stock_token_background(painter, cell_rect, token, option)
                token_font = self._stock_token_font(option.font, token)
                token_pen = self._stock_token_foreground(
                    token,
                    option,
                    visually_enabled=visually_enabled,
                )
                if column == 0:
                    stock_led_left = cell_rect.left()
                    _draw_routine_profit_led(
                        painter,
                        row_rect=option.rect,
                        led_box_left=stock_led_left,
                        led_state=index.data(ROUTINE_STOCK_PROFIT_LED_ROLE),
                        visually_enabled=visually_enabled,
                    )
                    text_rect = cell_rect.adjusted(
                        ROUTINE_PROFIT_LED_BOX_SIZE + ROUTINE_PROFIT_LED_GAP,
                        0,
                        -2,
                        0,
                    )
                    stock_code = str(
                        index.data(ROUTINE_STOCK_CODE_ROLE) or ""
                    ).strip()
                    code_font, chart_open_color = _routine_stock_code_chart_style(
                        token_font,
                        stock_code,
                    )
                    if chart_open_color is not None and text.startswith(stock_code):
                        painter.setFont(code_font)
                        painter.setPen(chart_open_color)
                        painter.drawText(
                            text_rect,
                            Qt.AlignLeft | Qt.AlignVCenter,
                            stock_code,
                        )
                        code_width = QFontMetrics(code_font).horizontalAdvance(stock_code)
                        remainder = text[len(stock_code) :]
                        remainder_rect = text_rect.adjusted(code_width, 0, 0, 0)
                        painter.setFont(token_font)
                        painter.setPen(token_pen)
                        painter.drawText(
                            remainder_rect,
                            Qt.AlignLeft | Qt.AlignVCenter,
                            painter.fontMetrics().elidedText(
                                remainder,
                                Qt.ElideRight,
                                max(0, remainder_rect.width()),
                            ),
                        )
                    else:
                        elided = painter.fontMetrics().elidedText(
                            text,
                            Qt.ElideRight,
                            max(0, text_rect.width()),
                        )
                        painter.setFont(token_font)
                        painter.setPen(token_pen)
                        painter.drawText(
                            text_rect,
                            Qt.AlignLeft | Qt.AlignVCenter,
                            elided,
                        )
                    continue
                if column == 1:
                    initial_buy = index.data(ROUTINE_STOCK_INITIAL_BUY_ROLE)
                    if not isinstance(initial_buy, dict):
                        initial_buy = {}
                    _draw_initial_buy_display(
                        painter,
                        cell_rect,
                        initial_buy,
                        hide_value=(
                            str(
                                getattr(
                                    option.widget,
                                    "_editing_stock_initial_buy_path",
                                    "",
                                )
                                or ""
                            )
                            == str(index.data(ROUTINE_STOCK_PATH_ROLE) or "").strip()
                        ),
                    )
                    continue
                if column == 3:
                    led_size = min(
                        ROUTINE_PROFIT_LED_SIZE,
                        ROUTINE_PROFIT_LED_BOX_SIZE,
                        cell_rect.height(),
                    )
                    led_rect = QRect(
                        cell_rect.left() + (cell_rect.width() - led_size) // 2,
                        cell_rect.top() + (cell_rect.height() - led_size) // 2,
                        led_size,
                        led_size,
                    )
                    painter.save()
                    painter.setRenderHint(QPainter.Antialiasing, True)
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(token_pen)
                    painter.drawEllipse(led_rect)
                    painter.restore()
                    continue
                alignment = (
                        Qt.AlignLeft | Qt.AlignVCenter
                        if column == 0
                        else Qt.AlignCenter
                )
                alignment = self._stock_token_alignment(token, alignment)
                if column >= 7:
                    painter.setFont(token_font)
                    painter.setPen(token_pen)
                    if column == 7:
                        metric_texts = _routine_stock_metric_texts(list(values), tuple(metrics_data))
                        metric_foregrounds: list[QColor | None] = []
                        for metric_index in range(len(metric_texts)):
                            token_index = 7 + metric_index
                            metric_token = (
                                display_tokens[token_index]
                                if token_index < len(display_tokens)
                                and isinstance(display_tokens[token_index], dict)
                                else {}
                            )
                            metric_foregrounds.append(
                                self._stock_token_foreground(
                                    metric_token,
                                    option,
                                    visually_enabled=visually_enabled,
                                )
                            )
                        hidden_value_indexes: set[int] = set()
                        if (
                            str(getattr(option.widget, "_editing_stock_buy_limit_path", "") or "")
                            == str(index.data(ROUTINE_STOCK_PATH_ROLE) or "").strip()
                        ):
                            hidden_value_indexes.add(4)
                        _draw_routine_stock_metric_text_sequence(
                            painter,
                            row_rect=option.rect,
                            start_x=cell_rect.left() + ROUTINE_STOCK_METRIC_SEPARATOR_GAP,
                            texts=metric_texts,
                            foregrounds=metric_foregrounds,
                            hidden_value_indexes=hidden_value_indexes,
                        )
                        painter.restore()
                        return
                    stock_metric_label_hint = {
                        7: "보유",
                        8: "가격",
                        9: "수익",
                        10: "매매",
                    }.get(column)
                    metric_index = column - 7
                    metric = (
                        metrics_data[metric_index]
                        if metric_index < len(metrics_data)
                        else None
                    )
                    if metric is not None and draw_stock_position_metric_display(
                        painter,
                        cell_rect.adjusted(2, 0, -2, 0),
                        metric,
                        outer_padding=ROUTINE_INSTANCE_MONEY_OUTER_PADDING,
                        show_label=True,
                    ):
                        continue
                    if column == 11 and draw_limit_metric(
                        painter,
                        cell_rect,
                        text,
                        value_width=routine_instance_number_widths(painter.font())[
                            "limit_amount"
                        ],
                        outer_padding=ROUTINE_INSTANCE_MONEY_OUTER_PADDING,
                        hide_value=(
                            str(getattr(option.widget, "_editing_stock_buy_limit_path", "") or "")
                            == str(index.data(ROUTINE_STOCK_PATH_ROLE) or "").strip()
                        ),
                    ):
                        continue
                    if draw_stock_position_metric(
                        painter,
                        cell_rect.adjusted(2, 0, -2, 0),
                        text,
                        value_widths=stock_position_value_widths,
                        outer_padding=ROUTINE_INSTANCE_MONEY_OUTER_PADDING,
                        label_hint=stock_metric_label_hint,
                    ):
                        continue
                elided = painter.fontMetrics().elidedText(
                    text,
                    Qt.ElideRight,
                    max(0, cell_rect.width() - 4),
                )
                painter.setFont(token_font)
                painter.setPen(token_pen)
                painter.drawText(
                    cell_rect.adjusted(2, 0, -2, 0),
                    alignment,
                    elided,
                )
            painter.restore()
            return
        content_offset = (
            ROUTINE_CHILD_CHECKBOX_OFFSET
            if row_kind == ROUTINE_ROW_CHILD
            else ROUTINE_PARENT_CHECKBOX_OFFSET
        )
        visually_enabled = index.data(ROUTINE_CHECKBOX_VISUAL_ENABLED_ROLE) is not False
        text_left_offset = content_offset
        if row_kind == ROUTINE_ROW_CHILD:
            has_stocks = bool(index.data(ROUTINE_CHILD_HAS_STOCKS_ROLE))
            arrow, arrow_enabled = self._child_arrow_state(
                has_stocks,
                bool(index.data(ROUTINE_CHILD_COLLAPSED_ROLE)),
            )
            arrow_rect = option.rect.adjusted(
                text_left_offset,
                0,
                -4,
                0,
            )
            painter.save()
            painter.setFont(option.font)
            if not arrow_enabled:
                painter.setPen(option.palette.color(QPalette.Disabled, QPalette.Text))
            elif not visually_enabled:
                painter.setPen(QColor("#9ca3af"))
            elif option.state & QStyle.State_Selected:
                painter.setPen(option.palette.highlightedText().color())
            else:
                painter.setPen(option.palette.text().color())
            painter.drawText(
                arrow_rect,
                Qt.AlignLeft | Qt.AlignVCenter,
                arrow,
            )
            painter.restore()
            text_left_offset += QFontMetrics(option.font).horizontalAdvance("▶") + 4
            led_state = str(index.data(ROUTINE_CHILD_PROFIT_LED_ROLE) or "gray")
            led_box_left = option.rect.left() + text_left_offset
            _draw_routine_profit_led(
                painter,
                row_rect=option.rect,
                led_box_left=led_box_left,
                led_state=led_state,
                visually_enabled=visually_enabled,
                square=True,
            )
            text_left_offset += (
                ROUTINE_PROFIT_LED_BOX_SIZE
                + ROUTINE_PROFIT_LED_GAP
            )

        text_rect = option.rect.adjusted(
            text_left_offset,
            0,
            -4,
            0,
        )
        painter.save()
        if not visually_enabled:
            painter.setPen(QColor("#9ca3af"))
        elif option.state & QStyle.State_Selected:
            painter.setPen(option.palette.highlightedText().color())
        else:
            foreground = index.data(Qt.ForegroundRole)
            if isinstance(foreground, QBrush) and foreground.style() != Qt.NoBrush:
                painter.setPen(foreground.color())
            else:
                painter.setPen(option.palette.text().color())
        if row_kind == ROUTINE_ROW_PARENT:
            parent_text = str(index.data(Qt.DisplayRole) or "")
            parent_font = _routine_parent_font(option.font)
            painter.setFont(parent_font)
            painter.drawText(
                text_rect,
                Qt.AlignLeft | Qt.AlignVCenter,
                parent_text,
            )
            display_text = self.display_text(index, option.widget)
            if display_text != parent_text:
                aggregate_font = main_monitoring_cell_font()
                painter.setFont(aggregate_font)
                aggregate_values = index.data(ROUTINE_PARENT_AGGREGATE_VALUES_ROLE)
                if isinstance(aggregate_values, (list, tuple)) and len(aggregate_values) == 4:
                    column_widths = routine_instance_grid_columns(aggregate_font)
                    separator_width = routine_aggregate_separator_width(aggregate_font)
                    aggregate_left = option.rect.left() + ROUTINE_INSTANCE_NAME_WIDTH
                    slot_lefts = routine_aggregate_slot_lefts(
                        aggregate_left,
                        aggregate_font,
                    )
                    metrics = QFontMetrics(aggregate_font)
                    open_paren_width = metrics.horizontalAdvance("(")
                    close_paren_width = metrics.horizontalAdvance(")")
                    number_slot_width = routine_aggregate_number_slot_width(
                        aggregate_font
                    )
                    for slot_index, (column_key, value, slot_left) in enumerate(
                        zip(
                            ("registered", "excluded", "operation_or_stopped", "review"),
                            aggregate_values,
                            slot_lefts,
                        )
                    ):
                        label_text, number_text = value
                        slot_width = column_widths[column_key]
                        label_width = routine_aggregate_label_width(
                            column_key,
                            aggregate_font,
                        )
                        label_rect = QRect(
                            slot_left,
                            text_rect.top(),
                            label_width,
                            text_rect.height(),
                        )
                        painter.drawText(
                            label_rect,
                            Qt.AlignLeft | Qt.AlignVCenter,
                            str(label_text),
                        )
                        open_paren_rect = QRect(
                            label_rect.right() + 1,
                            text_rect.top(),
                            open_paren_width,
                            text_rect.height(),
                        )
                        painter.drawText(
                            open_paren_rect,
                            Qt.AlignCenter,
                            "(",
                        )
                        number_rect = QRect(
                            open_paren_rect.right() + 1,
                            text_rect.top(),
                            number_slot_width,
                            text_rect.height(),
                        )
                        painter.drawText(
                            number_rect,
                            Qt.AlignCenter,
                            str(number_text),
                        )
                        close_paren_rect = QRect(
                            number_rect.right() + 1,
                            text_rect.top(),
                            close_paren_width,
                            text_rect.height(),
                        )
                        painter.drawText(
                            close_paren_rect,
                            Qt.AlignCenter,
                            ")",
                        )
                        if slot_index < 3:
                            separator_rect = QRect(
                                slot_left + slot_width,
                                text_rect.top(),
                                separator_width,
                                text_rect.height(),
                            )
                            painter.drawText(separator_rect, Qt.AlignCenter, "|")
                    profit_data = index.data(ROUTINE_PARENT_PROFIT_ROLE)
                    if (
                        isinstance(profit_data, (list, tuple))
                        and len(profit_data) == 2
                        and slot_lefts
                    ):
                        profit_text, profit_color = profit_data
                        profit_left = (
                            slot_lefts[-1]
                            + column_widths["review"]
                            + separator_width
                        )
                        separator_rect = QRect(
                            profit_left - separator_width,
                            text_rect.top(),
                            separator_width,
                            text_rect.height(),
                        )
                        painter.drawText(separator_rect, Qt.AlignCenter, "|")
                        color = QColor(str(profit_color or ""))
                        if color.isValid() and visually_enabled:
                            painter.setPen(color)
                        number_widths = routine_instance_number_widths(
                            aggregate_font
                        )
                        draw_stock_position_metric(
                            painter,
                            QRect(
                                profit_left,
                                text_rect.top(),
                                routine_instance_grid_columns(aggregate_font)["profit"],
                                text_rect.height(),
                            ),
                            str(profit_text or ""),
                            value_widths={
                                "수익": (
                                    number_widths["profit_amount"],
                                    number_widths["profit_rate"],
                                )
                            },
                            outer_padding=ROUTINE_INSTANCE_MONEY_OUTER_PADDING,
                            label_hint="수익",
                        )
                else:
                    aggregate = display_text[len(parent_text) :].lstrip()
                    aggregate_left = option.rect.left() + ROUTINE_INSTANCE_NAME_WIDTH
                    aggregate_rect = QRect(
                        aggregate_left,
                        text_rect.top(),
                        max(0, text_rect.right() - aggregate_left),
                        text_rect.height(),
                    )
                    painter.drawText(
                        aggregate_rect,
                        Qt.AlignLeft | Qt.AlignVCenter,
                        aggregate,
                    )
        else:
            painter.setFont(option.font)
            child_text = self.display_text(index, option.widget)
            child_text = painter.fontMetrics().elidedText(
                child_text,
                Qt.ElideRight,
                text_rect.width(),
            )
            painter.drawText(
                text_rect,
                Qt.AlignLeft | Qt.AlignVCenter,
                child_text,
            )
        painter.restore()


def append_base_stock(code: str, name: str) -> None:
    """
    기초종목.txt 에 종목 1개를 추가한다.
    """
    existing_text = BASE_STOCK_PATH.read_text(encoding="utf-8") if BASE_STOCK_PATH.exists() else ""
    prefix = "" if not existing_text or existing_text.endswith("\n") else "\n"

    with BASE_STOCK_PATH.open("a", encoding="utf-8") as file:
        file.write(f"{prefix}{code},{name}\n")


def routine_dir_by_display_name() -> dict[str, Path]:
    """
    GUI 표시 루틴명 기준으로 루틴 폴더를 찾는다.
    """
    return {routine_display_name(path): path for path in get_routine_dirs()}


class MainWindow(QMainWindow):
    """
    키움 자동매매 시스템 메인 윈도우
    """

    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("키움 OpenAPI 자동매매 시스템 - v1.1 Windows GUI")
        self.resize(2137, 720)
        self.setMinimumWidth(1680)
        try:
            self.kiwoom_api = KiwoomApi(parent=self)
        except Exception as exc:
            self.kiwoom_api = None
            self.kiwoom_api_unavailable_reason = str(exc)
        else:
            self.kiwoom_api_unavailable_reason = self.kiwoom_api.unavailable_reason()
            login_state_changed = getattr(self.kiwoom_api, "login_state_changed", None)
            if login_state_changed is not None:
                login_state_changed.connect(self.on_kiwoom_login_state_changed)
            raw_chejan_received = getattr(self.kiwoom_api, "raw_chejan_received", None)
            if raw_chejan_received is not None:
                raw_chejan_received.connect(self.on_kiwoom_raw_chejan_received)
            authentication_required = getattr(
                self.kiwoom_api,
                "account_authentication_required",
                None,
            )
            if authentication_required is not None:
                authentication_required.connect(
                    self.on_kiwoom_account_authentication_required
                )

        self.login_status_label = QLabel("로그인 상태: 미연결")
        self.btn_kiwoom_login = QPushButton("키움 로그인")
        self.account_label = QLabel("계좌정보 :")
        self.account_combo = _AccountInfoComboBox()
        self.account_combo.setObjectName("kiwoomAccountCombo")
        self.account_combo.setEnabled(False)
        self.account_type_label = QLabel("계좌 구분: -")
        self.account_type_label.hide()
        self._account_memo_settings = QSettings(
            QSettings.IniFormat,
            QSettings.UserScope,
            "jinkwangchul",
            "kiwoom_auto",
        )
        self._selected_kiwoom_account_no = ""
        self._account_memo_edit_account_no = ""
        self._account_memo_loading = False
        self.account_memo_edit = QLineEdit()
        self.account_memo_edit.setObjectName("kiwoomAccountMemoEdit")
        self.account_memo_edit.setMaxLength(8)
        self.account_memo_edit.setEnabled(False)
        self.account_memo_edit.setFrame(False)
        self.account_memo_edit.setStyleSheet(ROUTINE_INLINE_EDIT_STYLE)
        self._account_authentication_states: dict[str, str] = {}
        self._account_query_states: dict[str, str] = {}
        self.account_auth_separator = QLabel("|")
        self.account_auth_label = QLabel("계좌인증 :")
        self.account_auth_neutral_label = QLabel("-")
        self.account_auth_done_label = QLabel("완료")
        self.btn_account_authentication = QPushButton("미인증")
        self.btn_account_authentication.setObjectName("accountAuthenticationAction")
        self.btn_account_authentication.setFixedHeight(24)
        self.btn_account_authentication.setStyleSheet(
            "QPushButton {"
            " color: #DC2626; background: transparent;"
            " border: 1px solid #DC2626; border-radius: 3px;"
            " padding: 1px 6px; min-height: 18px;"
            "}"
            "QPushButton:hover { background: #FEF2F2; }"
            "QPushButton:pressed { background: #FEE2E2; }"
        )
        for widget in (
            self.account_auth_separator,
            self.account_auth_label,
            self.account_auth_neutral_label,
            self.account_auth_done_label,
            self.btn_account_authentication,
        ):
            widget.hide()
        self.account_query_status_label = QLabel("계좌상태 :")
        self.account_query_normal_label = QLabel("정상")
        self.account_query_neutral_label = QLabel("-")
        self.btn_account_requery = QPushButton("재조회")
        self.btn_account_requery.setObjectName("accountRequeryAction")
        self.btn_account_requery.setFixedHeight(24)
        self.btn_account_requery.setStyleSheet(
            "QPushButton {"
            " color: #B45309; background: transparent;"
            " border: 1px solid #D97706; border-radius: 3px;"
            " padding: 1px 6px; min-height: 18px;"
            "}"
            "QPushButton:hover { background: #FFFBEB; }"
            "QPushButton:pressed { background: #FEF3C7; }"
        )
        for widget in (
            self.account_query_status_label,
            self.account_query_normal_label,
            self.account_query_neutral_label,
            self.btn_account_requery,
        ):
            widget.hide()
        self.account_combo.view().setContextMenuPolicy(Qt.CustomContextMenu)
        self._account_popup_display_delegate = _AccountPopupDisplayDelegate(
            self.account_combo.view()
        )
        self.account_combo.view().setItemDelegate(
            self._account_popup_display_delegate
        )
        self._account_combo_popup_controller = (
            _AccountComboPopupInteractionController(self, self.account_combo)
        )
        self.account_combo._popup_interaction_controller = (
            self._account_combo_popup_controller
        )
        self.account_combo.view().viewport().installEventFilter(
            self._account_combo_popup_controller
        )
        self.account_combo.view().installEventFilter(
            self._account_combo_popup_controller
        )
        self.auto_status_label = QLabel("전체 자동매매 상태: 정지")
        self.buy_time_status_label = QLabel("매수 가능 상태: 확인 전")
        self.account_total_deposit_label = QLabel("-")
        self.account_order_available_label = QLabel("-")
        self._account_funds_projection = AccountFundsProjection()
        self.account_funds_adapter = (
            KiwoomAccountFundsAdapter(self.kiwoom_api)
            if self.kiwoom_api is not None
            else None
        )

        # 관제창 예산 현황 표시 전용 QLabel
        # 실제 예산 저장/주문수량 계산/매수 제한 로직은 아직 연결하지 않는다.
        self.budget_total_label = _DoubleClickValueLabel("0")
        self.budget_used_label = QLabel("0")
        self.budget_available_label = QLabel("0")
        self.budget_reserve_label = QLabel("0")
        self.budget_usage_rate_label = QLabel("-")
        self.budget_routine_count_label = QLabel("0")
        self.budget_stock_count_label = QLabel("0")
        self.budget_status_label = QLabel("확인 전")
        self._main_budget_warning_previous_available_ratio = None
        self._main_budget_warning_previous_buffer_entered = None

        self.routine_table = QTableWidget()
        self.running_stock_table = QTableWidget()
        self._main_routine_sort_column = -1
        self._main_routine_sort_order = Qt.AscendingOrder
        self._collapsed_routine_definition_ids: set[str] = set()
        self._collapsed_routine_instance_ids: set[str] = set()
        self._routine_definition_enabled: dict[str, bool] = {}
        self._routine_instance_selection: dict[str, bool] = {}
        self._routine_stock_selection: dict[str, bool] = {}
        self._routine_instance_ids_by_definition: dict[str, tuple[str, ...]] = {}
        self._routine_definition_by_instance: dict[str, str] = {}
        self._routine_assigned_stock_count_by_instance: dict[str, int] = {}
        self._routine_instance_name_editor = None
        self._routine_instance_name_editor_instance_id = ""
        self._routine_instance_name_editor_original = ""
        self._routine_instance_name_editor_item = None
        self._routine_instance_name_edit_finishing = False
        self._main_routine_valid_only = True
        self._main_routine_display_level = "stock"
        self._main_routine_display_level_applied = False
        self._main_routine_metric_sort_key = ""
        self._main_routine_metric_sort_active = False
        self._main_routine_initial_buy_sort_mode = ""
        self._main_routine_initial_buy_sort_next_mode = "AMOUNT"
        self._main_routine_column_sort_key = ""
        self._main_routine_excluded_only = False
        self._main_routine_stock_scope = "all"
        self._last_main_routine_table_height_signature = (-1, -1, -1)
        self._main_routine_valid_button = None
        self._main_routine_level_buttons: dict[str, QPushButton] = {}
        self._main_routine_metric_buttons: dict[str, QPushButton] = {}
        self._main_routine_initial_buy_sort_button = None
        self._main_routine_column_sort_buttons: dict[str, QPushButton] = {}
        self._routine_buy_limit_edit_filter = _RoutineBuyLimitValueEditFilter(self)
        self._routine_instance_buy_limit_editor = None
        self._routine_instance_buy_limit_editor_instance_id = ""
        self._routine_instance_buy_limit_editor_label = None
        self._routine_instance_buy_limit_edit_finishing = False
        self._routine_instance_buy_limit_pending_id = ""
        self._routine_instance_buy_limit_suppressed_release_widget = None
        self._routine_instance_buy_limit_click_timer = QTimer(self)
        self._routine_instance_buy_limit_click_timer.setSingleShot(True)
        self._routine_instance_buy_limit_click_timer.timeout.connect(
            self._execute_routine_instance_buy_limit_single_click
        )
        self._routine_stock_buy_limit_editor = None
        self._routine_stock_buy_limit_editor_config_path = ""
        self._routine_stock_buy_limit_edit_finishing = False
        self._routine_stock_buy_limit_pending_path = ""
        self._routine_stock_buy_limit_suppressed_release_row = -1
        self._routine_stock_buy_limit_click_timer = QTimer(self)
        self._routine_stock_buy_limit_click_timer.setSingleShot(True)
        self._routine_stock_buy_limit_click_timer.timeout.connect(
            self._execute_routine_stock_buy_limit_single_click
        )
        self.routine_table._editing_stock_buy_limit_path = ""
        self._routine_stock_initial_buy_editor = None
        self._routine_stock_initial_buy_editor_config_path = ""
        self._routine_stock_initial_buy_editor_mode = "QUANTITY"
        self._routine_stock_initial_buy_edit_finishing = False
        self.routine_table._editing_stock_initial_buy_path = ""
        self._main_running_sort_column = -1
        self._main_running_sort_order = Qt.AscendingOrder
        self._startup_recovery_result: dict[str, object] = {}
        self._startup_recovery_approved = False
        self._startup_recovery_approved_snapshot = ""
        self._production_recovery_identity = None
        self._production_recovery_parts: dict[str, BrokerSnapshotPart] = {}
        self._latest_completed_recovery_snapshot: BrokerAccountSnapshot | None = None
        self._latest_completed_recovery_identity: RecoverySessionIdentity | None = None

        self.btn_start = QPushButton("▶ 운영시작")
        self.btn_auto_trade_setting = QPushButton("자동매매설정")
        self.btn_close_all_windows = QPushButton("모든창닫기")
        self.btn_close_all_windows.setObjectName("mainCloseAllWindowsButton")
        self.btn_log_view = QPushButton("이벤트")
        self.btn_review_required = QPushButton("검토관리(0)")
        self.btn_main_visible_early_close = QPushButton("조기마감")
        self.btn_exit = QPushButton("종료")
        self.btn_emergency_stop = _DoubleClickActionButton("긴급정지")

        self._setup_ui()
        self._connect_events()
        operation_host = self.main_monitoring_auto_trade_operation_host()
        operation_host.operation_cycle_completed.connect(
            self._on_main_operation_cycle_completed
        )
        normalize_base_stock_single_routine_file()
        self.refresh_startup_recovery_status()
        self.refresh_all()
        self._pnl_refresh_timer = QTimer(self)
        self._pnl_refresh_timer.setInterval(PNL_REFRESH_INTERVAL_MS)
        self._pnl_refresh_timer.timeout.connect(lambda: main_refresh_pnl_only(self))
        self._pnl_refresh_timer.start()
        append_owner_event_once(
            self,
            "app_started",
            "APP_STARTED",
            result="SUCCESS",
            source="MainWindow.__init__",
            target_type="APPLICATION",
            target_id="kiwoom_auto",
        )
        register_main_window_buffer_response_integration(self)

    def _setup_ui(self) -> None:
        central = QWidget()
        central.setObjectName("mainDashboardRoot")
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        server_connection_box = self._create_top_status_box()
        server_basic_info_box = self._create_account_funds_box()
        budget_setting_box = self._create_budget_status_box()
        performance_box = self._create_performance_box()
        self._main_top_part_boxes = (
            server_connection_box,
            server_basic_info_box,
            budget_setting_box,
            performance_box,
        )
        top_layout = QHBoxLayout()
        top_layout.setSpacing(6)
        server_connection_box.setSizePolicy(
            QSizePolicy.Maximum,
            QSizePolicy.Preferred,
        )
        server_basic_info_box.setSizePolicy(
            QSizePolicy.Maximum,
            QSizePolicy.Preferred,
        )
        budget_setting_box.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Preferred,
        )
        performance_box.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Preferred,
        )
        for top_part_box in self._main_top_part_boxes:
            top_part_box.setFixedHeight(131)
        top_layout.addWidget(server_connection_box, 0)
        top_layout.addWidget(server_basic_info_box, 0)
        top_layout.addWidget(budget_setting_box, 1)
        top_layout.addWidget(performance_box, 1)

        table_layout = self._create_table_area()
        button_layout = self._create_button_area()

        main_layout.addLayout(top_layout)
        main_layout.addLayout(table_layout)
        main_layout.addLayout(button_layout)

        central.setLayout(main_layout)
        self.setCentralWidget(central)
        self._apply_main_dashboard_style(central)

        self.statusBar().showMessage("준비 완료")

    def _create_top_status_box(self) -> QGroupBox:
        box = QGroupBox("시스템")
        box.setObjectName("mainServerConnectionStatusPart")
        layout = QGridLayout()
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(6)

        self.login_status_label.setParent(box)
        self.login_status_label.hide()
        self.auto_status_label.hide()
        self.buy_time_status_label.hide()
        self.btn_kiwoom_login.setObjectName("kiwoomLoginStateButton")
        self.btn_kiwoom_login.setFixedSize(92, 92)
        self._apply_kiwoom_login_button_state("DISCONNECTED")
        layout.addWidget(
            self.btn_kiwoom_login,
            0,
            0,
            3,
            1,
            Qt.AlignTop | Qt.AlignHCenter,
        )
        self.account_info_widget = QWidget(box)
        account_info_layout = QHBoxLayout(self.account_info_widget)
        account_info_layout.setContentsMargins(0, 0, 0, 0)
        account_info_layout.setSpacing(0)
        account_info_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        account_title_width = max(
            self.account_label.fontMetrics().horizontalAdvance(text)
            for text in ("계좌정보 :", "계좌인증 :", "계좌상태 :")
        ) + 2
        for title_label in (
            self.account_label,
            self.account_auth_label,
            self.account_query_status_label,
        ):
            title_label.setFixedWidth(account_title_width)
            title_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        account_info_layout.addWidget(self.account_label)
        account_info_layout.addSpacing(8)
        account_info_layout.addWidget(self.account_combo)
        account_info_layout.addSpacing(4)
        account_info_layout.addWidget(self.account_memo_edit)
        account_display_width = self.account_combo.fontMetrics().horizontalAdvance(
            "8129****"
        )
        self.account_combo.setFixedWidth(account_display_width + 20)
        memo_display_width = self.account_memo_edit.fontMetrics().horizontalAdvance(
            "가나다라마바사아"
        )
        self.account_memo_edit.setFixedWidth(memo_display_width + 12)
        layout.addWidget(self.account_info_widget, 0, 1)

        self.account_auth_widget = QWidget(box)
        account_auth_layout = QHBoxLayout(self.account_auth_widget)
        account_auth_layout.setContentsMargins(0, 0, 0, 0)
        account_auth_layout.setSpacing(0)
        account_auth_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        account_auth_layout.addWidget(self.account_auth_label)
        # The borderless account combo keeps a 10px text inset.  Start the
        # other row values at that same visual text column, not merely at the
        # combo widget's outer geometry.
        account_auth_layout.addSpacing(18)
        account_auth_layout.addWidget(self.account_auth_neutral_label)
        account_auth_layout.addWidget(self.account_auth_done_label)
        account_auth_layout.addWidget(self.btn_account_authentication)
        layout.addWidget(self.account_auth_widget, 1, 1)

        self.account_query_status_widget = QWidget(box)
        account_query_status_layout = QHBoxLayout(self.account_query_status_widget)
        account_query_status_layout.setContentsMargins(0, 0, 0, 0)
        account_query_status_layout.setSpacing(0)
        account_query_status_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        account_query_status_layout.addWidget(self.account_query_status_label)
        account_query_status_layout.addSpacing(18)
        account_query_status_layout.addWidget(self.account_query_normal_label)
        account_query_status_layout.addWidget(self.account_query_neutral_label)
        account_query_status_layout.addWidget(self.btn_account_requery)
        layout.addWidget(self.account_query_status_widget, 2, 1)

        auth_value_width = max(
            self.account_auth_done_label.fontMetrics().horizontalAdvance(value)
            for value in ("-", "완료", "미인증")
        ) + 14
        for widget in (
            self.account_auth_neutral_label,
            self.account_auth_done_label,
            self.btn_account_authentication,
        ):
            widget.setFixedWidth(auth_value_width)
        query_value_width = max(
            self.account_query_normal_label.fontMetrics().horizontalAdvance(value)
            for value in ("-", "정상", "재조회")
        ) + 14
        for widget in (
            self.account_query_neutral_label,
            self.account_query_normal_label,
            self.btn_account_requery,
        ):
            widget.setFixedWidth(query_value_width)

        layout.setColumnStretch(1, 1)

        box.setLayout(layout)
        self.refresh_account_authentication_ui()
        return box

    def _apply_kiwoom_login_button_state(self, state: str) -> None:
        clean_state = str(state or "").strip().upper()
        contracts = {
            "LOGIN_IN_PROGRESS": (
                "로그인\n중...",
                "#E2E8F0",
                "#94A3B8",
                "#334155",
            ),
            "REAL_CONNECTED": (
                "실전\n연결됨",
                "#F97316",
                "#F97316",
                "#FFFFFF",
            ),
            "SIMULATION_CONNECTED": (
                "모의\n연결됨",
                "#FACC15",
                "#FACC15",
                "#1E3A8A",
            ),
        }
        text, background, border, color = contracts.get(
            clean_state,
            ("키움\n로그인", "#F8FAFC", "#CBD5E1", "#334155"),
        )
        self.btn_kiwoom_login.setProperty("kiwoomLoginState", clean_state or "DISCONNECTED")
        self.btn_kiwoom_login.setText(text)
        login_action_available = clean_state not in contracts
        self.btn_kiwoom_login.setEnabled(login_action_available)
        self.btn_kiwoom_login.setCursor(
            Qt.PointingHandCursor if login_action_available else Qt.ArrowCursor
        )
        self.btn_kiwoom_login.setStyleSheet(
            "QPushButton#kiwoomLoginStateButton {"
            f" background-color: {background};"
            f" border: 1px solid {border};"
            f" color: {color};"
            " border-radius: 6px;"
            " padding: 8px;"
            " min-width: 74px;"
            " max-width: 74px;"
            " min-height: 74px;"
            " max-height: 74px;"
            " font-weight: 600;"
            "}"
            "QPushButton#kiwoomLoginStateButton:hover {"
            f" border-color: {border};"
            f" background-color: {background};"
            "}"
            "QPushButton#kiwoomLoginStateButton:disabled {"
            f" background-color: {background};"
            f" border-color: {border};"
            f" color: {color};"
            "}"
        )

    def _apply_connected_kiwoom_login_button_state(self) -> None:
        api = getattr(self, "kiwoom_api", None)
        server_type_getter = getattr(api, "account_server_type", None)
        server_type = ""
        if callable(server_type_getter):
            try:
                server_type = str(server_type_getter() or "").strip().upper()
            except Exception:
                LOGGER.exception("Kiwoom server type projection failed")
        self._apply_kiwoom_login_button_state(
            "SIMULATION_CONNECTED" if server_type == "SIMULATION" else "REAL_CONNECTED"
        )

    def _create_account_funds_box(self) -> QGroupBox:
        box = QGroupBox("현황정보")
        box.setObjectName("mainServerBasicInfoPart")
        layout = QGridLayout()
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setHorizontalSpacing(0)
        layout.setVerticalSpacing(6)

        self.btn_emergency_stop.setObjectName("dangerButton")
        self.btn_emergency_stop.setFixedSize(self.btn_kiwoom_login.size())
        self.btn_emergency_stop.setToolTip("더블클릭하여 긴급정지")
        self.btn_emergency_stop.setStyleSheet(
            "QPushButton#dangerButton {"
            " background: #DC2626; border: 1px solid #B91C1C;"
            " color: #FFFFFF; border-radius: 6px; padding: 8px;"
            " min-width: 74px; max-width: 74px;"
            " min-height: 74px; max-height: 74px; font-weight: 700;"
            "}"
            "QPushButton#dangerButton:hover { background: #B91C1C; }"
            "QPushButton#dangerButton:pressed { background: #991B1B; }"
        )
        layout.addWidget(
            self.btn_emergency_stop,
            0,
            0,
            2,
            1,
            Qt.AlignTop | Qt.AlignLeft,
        )
        self.account_total_deposit_title_label = QLabel("총 예수금")
        self.account_order_available_title_label = QLabel("주문 가능금액")
        fund_title_width = max(
            self.account_total_deposit_title_label.fontMetrics().horizontalAdvance(
                title
            )
            for title in ("총 예수금", "주문 가능금액")
        ) + 2
        for title_label in (
            self.account_total_deposit_title_label,
            self.account_order_available_title_label,
        ):
            title_label.setFixedWidth(fund_title_width)
            title_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        # Match the System box's 8px gap between its state button and the
        # first account-information text.
        layout.setColumnMinimumWidth(1, 8)
        layout.addWidget(self.account_total_deposit_title_label, 0, 2)
        layout.addWidget(self.account_order_available_title_label, 1, 2)
        layout.addWidget(self.account_total_deposit_label, 0, 4)
        layout.addWidget(self.account_order_available_label, 1, 4)
        layout.setColumnMinimumWidth(5, 1)

        fund_value_font = QFont("Malgun Gothic", 12, QFont.Bold)
        fund_value_metrics = QFontMetrics(fund_value_font)
        fund_value_sample = "500,000,000"
        removed_currency_width = (
            fund_value_metrics.horizontalAdvance(f"{fund_value_sample}원")
            - fund_value_metrics.horizontalAdvance(fund_value_sample)
        )
        # Keep the 현황정보 box footprint unchanged: transfer the removed
        # currency-suffix width to the title/value gap so the numeric right
        # edge advances to the old full-text right edge.
        layout.setColumnMinimumWidth(3, 20 + removed_currency_width)
        fund_value_width = fund_value_metrics.horizontalAdvance(fund_value_sample) + 8
        for label in (
            self.account_total_deposit_label,
            self.account_order_available_label,
        ):
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            label.setFixedWidth(fund_value_width)
            label.setObjectName("fundValue")
            set_metric_value_text(label, label.text())

        box.setLayout(layout)
        return box

    def _create_performance_box(self) -> QGroupBox:
        box = QGroupBox("실적")
        box.setObjectName("mainPerformancePart")
        layout = QVBoxLayout()
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)

        cumulative_font = QFont(box.font())
        cumulative_font.setPointSize(13)
        cumulative_font.setBold(True)
        current_font = QFont(box.font())
        current_font.setPointSize(16)
        current_font.setBold(True)
        title_width = max(
            QFontMetrics(cumulative_font).horizontalAdvance("누적"),
            QFontMetrics(current_font).horizontalAdvance("현재"),
        ) + 16

        self.performance_cumulative_title_label = QLabel("누적")
        self.performance_cumulative_title_label.setObjectName(
            "mainPerformanceCumulativeTitle"
        )
        self.performance_cumulative_value_label = QLabel("0 (0.00%)")
        self.performance_cumulative_value_label.setObjectName(
            "mainPerformanceCumulativeValue"
        )
        self.performance_current_title_label = QLabel("현재")
        self.performance_current_title_label.setObjectName(
            "mainPerformanceCurrentTitle"
        )
        self.performance_current_value_label = QLabel("0 (0.00%)")
        self.performance_current_value_label.setObjectName(
            "mainPerformanceCurrentValue"
        )

        for title_label, font, color in (
            (
                self.performance_cumulative_title_label,
                cumulative_font,
                MAIN_PERFORMANCE_CUMULATIVE_TITLE_COLOR,
            ),
            (
                self.performance_current_title_label,
                current_font,
                MAIN_PERFORMANCE_CURRENT_TITLE_COLOR,
            ),
        ):
            title_label.setFixedWidth(title_width)
            title_label.setAlignment(Qt.AlignCenter)
            title_label.setFont(font)
            title_label.setStyleSheet(f"color: {color};")

        for value_label, font in (
            (self.performance_cumulative_value_label, cumulative_font),
            (self.performance_current_value_label, current_font),
        ):
            value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            value_label.setFont(font)
            MainWindow._set_main_performance_value(
                value_label,
                "0 (0.00%)",
                0,
            )

        for title_label, value_label in (
            (
                self.performance_cumulative_title_label,
                self.performance_cumulative_value_label,
            ),
            (
                self.performance_current_title_label,
                self.performance_current_value_label,
            ),
        ):
            row_layout = QHBoxLayout()
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(0)
            row_layout.addWidget(title_label)
            row_layout.addStretch(1)
            row_layout.addWidget(value_label)
            layout.addLayout(row_layout, 1)

        box.setLayout(layout)
        return box

    @staticmethod
    def _set_main_performance_value(
        label: QLabel,
        display_text: str,
        profit_amount: object | None,
    ) -> None:
        text = str(display_text or "").strip()
        if not text or text == "-":
            text = "0 (0.00%)"
        color = profit_loss_value_color(0)
        if profit_amount is not None:
            try:
                amount = float(str(profit_amount).replace(",", "").strip())
            except (TypeError, ValueError):
                amount = 0.0
            if amount > 0:
                color = MAIN_PERFORMANCE_PROFIT_COLOR
            elif amount < 0:
                color = MAIN_PERFORMANCE_LOSS_COLOR
        label.setText(text)
        label.setStyleSheet(f"color: {color};")

    def _create_budget_status_box(self) -> QGroupBox:
        """관제창 예산 현황 UI.

        시스템 전체예산과 가용비율만 전역 정책에서 읽고 쓴다.
        주문수량 산출, 매수 제한, 루틴/종목 배분에는 연결하지 않는다.
        """
        box = QGroupBox("예산설정")
        box.setObjectName("mainBudgetSettingPart")
        box_layout = QVBoxLayout()
        box_layout.setContentsMargins(8, 6, 8, 6)
        box_layout.setSpacing(4)
        total_row_layout = QHBoxLayout()
        total_row_layout.setContentsMargins(0, 0, 0, 0)
        total_row_layout.setSpacing(0)
        layout = QGridLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(0)
        layout.setVerticalSpacing(4)
        box_layout.addLayout(total_row_layout)
        box_layout.addLayout(layout)

        self.budget_total_title_label = QLabel("전체예산 :")
        title_metrics = self.budget_total_title_label.fontMetrics()
        percent_edit_width = title_metrics.horizontalAdvance("100") + 10
        percent_edit_height = max(20, title_metrics.height() + 4)
        percent_prefix_width = max(
            title_metrics.horizontalAdvance("▪ 가용 "),
            title_metrics.horizontalAdvance("▪ 완충 "),
        )
        percent_suffix_width = title_metrics.horizontalAdvance("% :")
        budget_title_width = max(
            title_metrics.horizontalAdvance("전체예산 :"),
            percent_prefix_width + percent_edit_width + percent_suffix_width,
        ) + 2

        self.budget_total_title_label.setFixedWidth(budget_title_width)
        self.budget_total_title_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        total_row_layout.addWidget(self.budget_total_title_label)

        def make_percent_title(prefix: str, object_name: str):
            widget = QWidget()
            row_layout = QHBoxLayout(widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(0)
            prefix_label = QLabel(prefix)
            prefix_label.setFixedWidth(percent_prefix_width)
            editor = _BudgetPercentEdit()
            editor.setObjectName(object_name)
            editor.setMaxLength(3)
            editor.setValidator(QIntValidator(0, 999, editor))
            editor.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            editor.setFixedSize(percent_edit_width, percent_edit_height)
            editor.setStyleSheet(
                "QLineEdit { border: none; outline: none; "
                "background: transparent; padding: 0; }"
                "QLineEdit:focus { border: none; outline: none; "
                "background: transparent; }"
            )
            suffix_label = QLabel("% :")
            suffix_label.setFixedWidth(percent_suffix_width)
            row_layout.addWidget(prefix_label)
            row_layout.addWidget(editor)
            row_layout.addWidget(suffix_label)
            widget.setFixedWidth(budget_title_width)
            return widget, editor, suffix_label

        (
            self.budget_available_title_label,
            self.budget_available_percent_edit,
            self.budget_available_percent_suffix_label,
        ) = make_percent_title("▪ 가용 ", "mainAvailableBudgetPercent")
        (
            self.budget_reserve_title_label,
            self.budget_buffer_percent_edit,
            self.budget_buffer_percent_suffix_label,
        ) = make_percent_title("▪ 완충 ", "mainBufferBudgetPercent")
        layout.addWidget(self.budget_available_title_label, 0, 0)
        layout.addWidget(self.budget_reserve_title_label, 1, 0)

        self.budget_available_percent_edit.commitRequested.connect(
            lambda: self._commit_main_budget_percent("available")
        )
        self.budget_buffer_percent_edit.commitRequested.connect(
            lambda: self._commit_main_budget_percent("buffer")
        )
        self.budget_available_percent_edit.cancelRequested.connect(
            self.update_budget_panel
        )
        self.budget_buffer_percent_edit.cancelRequested.connect(
            self.update_budget_panel
        )

        layout.setColumnMinimumWidth(1, 10)
        budget_value_width = QFontMetrics(self.budget_total_label.font()).horizontalAdvance(
            "9,999,999,999"
        ) + 8
        value_labels = (
            self.budget_total_label,
            self.budget_available_label,
            self.budget_reserve_label,
        )
        for label in value_labels:
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            label.setFixedWidth(budget_value_width)
            label.setObjectName("metricValue")
        total_row_layout.addSpacing(10)
        total_row_layout.addWidget(self.budget_total_label)
        for row, label in enumerate(
            (self.budget_available_label, self.budget_reserve_label)
        ):
            layout.addWidget(label, row, 2)
        layout.setColumnMinimumWidth(3, 8)
        separator_width = title_metrics.horizontalAdvance("|") + 4
        activity_title_width = max(
            title_metrics.horizontalAdvance("잔여"),
            title_metrics.horizontalAdvance("진입"),
        ) + 2
        activity_ratio_width = title_metrics.horizontalAdvance("100.0%") + 6
        parenthesis_width = max(
            title_metrics.horizontalAdvance("("),
            title_metrics.horizontalAdvance(")"),
        ) + 2

        self.budget_warning_separator_label = QLabel("|")
        self.budget_warning_toggle_button = _DoubleClickActionButton()
        self.budget_warning_toggle_button.setObjectName("mainBudgetWarningToggle")
        self.budget_warning_toggle_button.setCursor(Qt.PointingHandCursor)
        self.budget_warning_toggle_button.setToolTip("더블클릭하여 경고 ON/OFF 전환")
        self.budget_buffer_response_button = QPushButton("완충대응")
        self.budget_buffer_response_button.setObjectName(
            "mainBudgetBufferResponseEntry"
        )
        self.budget_buffer_response_button.setCursor(Qt.PointingHandCursor)
        self.budget_buffer_response_button.setToolTip("완충대응 설정")
        self.budget_warning_row_widget = QWidget()
        self.budget_warning_row_widget.setSizePolicy(
            QSizePolicy.Fixed,
            QSizePolicy.Fixed,
        )
        warning_row_layout = QHBoxLayout(self.budget_warning_row_widget)
        warning_row_layout.setContentsMargins(0, 0, 0, 0)
        warning_row_layout.setSpacing(8)
        self.budget_warning_separator_label.setFixedWidth(separator_width)
        self.budget_warning_separator_label.setAlignment(Qt.AlignCenter)
        warning_row_layout.addWidget(self.budget_warning_separator_label)
        warning_row_layout.addWidget(self.budget_warning_toggle_button)
        warning_row_layout.addWidget(self.budget_buffer_response_button)
        total_row_layout.addSpacing(8)
        total_row_layout.addWidget(self.budget_warning_row_widget)
        total_row_layout.addStretch(1)

        warning_enabled_getter = getattr(self, "main_budget_warning_enabled", None)
        warning_enabled = (
            bool(warning_enabled_getter())
            if callable(warning_enabled_getter)
            else True
        )
        MainWindow._apply_main_budget_warning_badge_style(
            self,
            warning_enabled,
        )
        self.budget_warning_toggle_button.ensurePolished()
        warning_metrics = QFontMetrics(self.budget_warning_toggle_button.font())
        warning_badge_width = max(
            warning_metrics.horizontalAdvance("경고 ON"),
            warning_metrics.horizontalAdvance("경고 OFF"),
        ) + 16
        warning_badge_height = max(
            AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT,
            warning_metrics.height() + 2,
        )
        self.budget_warning_toggle_button.setFixedSize(
            warning_badge_width,
            warning_badge_height,
        )
        response_metrics = QFontMetrics(self.budget_buffer_response_button.font())
        response_content_height = max(
            AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT - 2,
            response_metrics.height(),
        )
        MainWindow._apply_main_budget_buffer_response_badge_style(
            self,
            False,
            content_height=response_content_height,
        )
        self.budget_buffer_response_button.setFixedSize(
            response_metrics.horizontalAdvance("완충대응") + 16,
            warning_badge_height,
        )
        warning_toggle_handler = getattr(
            self,
            "set_main_budget_warning_enabled",
            None,
        )
        if callable(warning_toggle_handler) and callable(warning_enabled_getter):
            self.budget_warning_toggle_button.doubleClicked.connect(
                lambda: warning_toggle_handler(
                    not bool(warning_enabled_getter())
                )
            )
        response_entry_handler = getattr(
            self,
            "on_main_budget_buffer_response_entry_clicked",
            None,
        )
        if callable(response_entry_handler):
            self.budget_buffer_response_button.clicked.connect(
                response_entry_handler
            )

        self.budget_available_activity_separator_label = QLabel("|")
        self.budget_buffer_activity_separator_label = QLabel("|")
        self.budget_available_activity_title_label = QLabel("잔여")
        self.budget_buffer_activity_title_label = QLabel("진입")
        self.budget_available_remaining_label = QLabel("-")
        self.budget_buffer_entry_label = QLabel("-")
        self.budget_available_ratio_open_label = QLabel("(")
        self.budget_buffer_ratio_open_label = QLabel("(")
        self.budget_available_remaining_ratio_label = QLabel("-")
        self.budget_buffer_entry_ratio_label = QLabel("-")
        self.budget_available_ratio_close_label = QLabel(")")
        self.budget_buffer_ratio_close_label = QLabel(")")

        for row, separator_label in enumerate(
            (
                self.budget_available_activity_separator_label,
                self.budget_buffer_activity_separator_label,
            ),
            start=0,
        ):
            separator_label.setFixedWidth(separator_width)
            separator_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(separator_label, row, 4)

        layout.setColumnMinimumWidth(5, 8)
        for row, activity_label in enumerate(
            (
                self.budget_available_activity_title_label,
                self.budget_buffer_activity_title_label,
            ),
            start=0,
        ):
            activity_label.setFixedWidth(activity_title_width)
            activity_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            activity_label.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(
                activity_label,
                row,
                6,
                Qt.AlignLeft | Qt.AlignVCenter,
            )

        layout.setColumnMinimumWidth(7, 6)
        for row, amount_label in enumerate(
            (
                self.budget_available_remaining_label,
                self.budget_buffer_entry_label,
            ),
            start=0,
        ):
            amount_label.setFixedWidth(budget_value_width)
            amount_label.setObjectName("metricValue")
            set_metric_value_text(amount_label, "-")
            layout.addWidget(amount_label, row, 8)

        layout.setColumnMinimumWidth(9, 4)
        for row, open_label in enumerate(
            (
                self.budget_available_ratio_open_label,
                self.budget_buffer_ratio_open_label,
            ),
            start=0,
        ):
            open_label.setFixedWidth(parenthesis_width)
            open_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(open_label, row, 10)
        for row, ratio_label in enumerate(
            (
                self.budget_available_remaining_ratio_label,
                self.budget_buffer_entry_ratio_label,
            ),
            start=0,
        ):
            ratio_label.setFixedWidth(activity_ratio_width)
            ratio_label.setObjectName("metricRatio")
            set_metric_value_text(ratio_label, "-")
            layout.addWidget(ratio_label, row, 11)
        for row, close_label in enumerate(
            (
                self.budget_available_ratio_close_label,
                self.budget_buffer_ratio_close_label,
            ),
            start=0,
        ):
            close_label.setFixedWidth(parenthesis_width)
            close_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(close_label, row, 12)
        layout.setColumnStretch(13, 1)

        for legacy_label in (
            self.budget_used_label,
            self.budget_usage_rate_label,
            self.budget_routine_count_label,
            self.budget_stock_count_label,
            self.budget_status_label,
        ):
            legacy_label.hide()

        if isinstance(self, QWidget):
            self._main_total_budget_popup = _MainTotalBudgetPopup(self)
            self.budget_total_label.setToolTip("더블클릭하여 전체예산 설정")
            self.budget_total_label.doubleClicked.connect(
                self.show_main_total_budget_popup
            )

        box.setLayout(box_layout)
        return box

    def _create_table_area(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(8)

        routine_box = QGroupBox("등록된 자동매매 루틴")
        routine_box.setMinimumWidth(860)
        routine_layout = QVBoxLayout()
        routine_layout.setContentsMargins(8, 6, 8, 8)
        self._setup_routine_table()
        routine_content_layout = QHBoxLayout()
        routine_content_layout.setContentsMargins(0, 0, 0, 0)
        routine_content_layout.setSpacing(6)
        routine_filter_badge_area = self._create_routine_filter_badge_area()
        routine_header_layout = QHBoxLayout()
        routine_header_layout.setContentsMargins(0, 0, 0, 0)
        routine_header_layout.setSpacing(routine_content_layout.spacing())
        routine_summary = self._create_main_routine_summary()
        routine_header_layout.addWidget(routine_summary)
        routine_header_layout.addStretch(1)
        if hasattr(self, "_main_routine_valid_only"):
            MainWindow._update_main_routine_filter_badges(self)
        MainWindow._style_main_visible_early_close_button(self)
        routine_header_layout.addWidget(
            self.btn_main_visible_early_close,
            0,
            Qt.AlignRight | Qt.AlignVCenter,
        )
        routine_layout.addLayout(routine_header_layout)
        routine_content_layout.addWidget(routine_filter_badge_area)
        routine_content_layout.addWidget(self.routine_table, 1)
        routine_layout.addLayout(routine_content_layout)
        routine_box.setLayout(routine_layout)

        self._setup_running_stock_table()
        self.running_stock_table.setVisible(False)

        layout.addWidget(routine_box, 1)

        return layout

    def _style_main_visible_early_close_button(self) -> None:
        self.btn_main_visible_early_close.setObjectName("mainVisibleEarlyCloseButton")
        early_close_style = (
            "QPushButton#mainVisibleEarlyCloseButton {"
            f" {AUTO_TRADE_SETTING_EARLY_CLOSE_BUTTON_STYLE}"
        )
        reference_button = getattr(self, "_main_routine_valid_button", None)
        if reference_button is not None:
            reference_font = reference_button.font()
            self.btn_main_visible_early_close.setFont(reference_font)
            if reference_font.pixelSize() > 0:
                early_close_style += f" font-size: {reference_font.pixelSize()}px;"
            elif reference_font.pointSizeF() > 0:
                early_close_style += f" font-size: {reference_font.pointSizeF():g}pt;"
        early_close_style += " }"
        self.btn_main_visible_early_close.setMinimumHeight(28)
        self.btn_main_visible_early_close.setStyleSheet(early_close_style)

    def _create_main_routine_summary(self) -> QWidget:
        summary = QWidget()
        summary.setObjectName("mainRoutineSummary")
        layout = QHBoxLayout(summary)
        routine_table = getattr(self, "routine_table", None)
        text_left_margin = 0
        if isinstance(routine_table, QTableWidget):
            text_left_margin = (
                routine_table.viewport().geometry().x()
                +
                routine_table.style().pixelMetric(
                    QStyle.PM_FocusFrameHMargin,
                    None,
                    routine_table,
                )
                + 1
            )
        layout.setSpacing(4)

        summary_font = summary.font()
        point_size = summary_font.pointSizeF()
        if point_size > 0:
            summary_font.setPointSizeF(point_size * 1.3)
        elif summary_font.pixelSize() > 0:
            summary_font.setPixelSize(max(1, round(summary_font.pixelSize() * 1.3)))
        summary_font.setBold(True)

        metrics = QFontMetrics(summary_font)
        badge_border_width = 1
        badge_vertical_padding = 3
        badge_row_vertical_margin = 2
        badge_left_inset = (
            MAIN_ROUTINE_FILTER_BADGE_AREA_WIDTH - MAIN_ROUTINE_FILTER_BADGE_WIDTH
        ) // 2
        label_slot_width = max(
            metrics.horizontalAdvance(text)
            for text in ("그룹", "루틴", "종목", "운영", "대기", "제외", "검토")
        )
        number_slot_width = routine_aggregate_number_slot_width(summary_font)
        badge_height = max(
            AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT,
            metrics.height()
            + badge_vertical_padding * 2
            + badge_border_width * 2,
        )
        layout.setContentsMargins(
            badge_left_inset,
            badge_row_vertical_margin,
            0,
            badge_row_vertical_margin,
        )
        summary.setFixedHeight(badge_height + badge_row_vertical_margin * 2)
        badge_horizontal_padding = text_left_margin
        badge_body_spacing = 4
        count_badge_width = (
            badge_horizontal_padding * 2
            + label_slot_width
            + badge_body_spacing
            + number_slot_width
        )
        count_labels: dict[str, tuple[QLabel, QLabel]] = {}
        count_buttons: dict[str, QPushButton] = {}

        def create_summary_separator(object_name: str) -> QLabel:
            separator_font = QFont(summary_font)
            separator_font.setBold(False)
            separator = QLabel("|")
            separator.setObjectName(object_name)
            separator.setFont(separator_font)
            separator.setAlignment(Qt.AlignCenter)
            separator.setFixedSize(
                QFontMetrics(separator_font).horizontalAdvance("|") + 8,
                badge_height,
            )
            separator.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            return separator

        valid_button = QPushButton("유효")
        valid_button.setObjectName("mainRoutineValidBadge")
        valid_button.setFont(summary_font)
        valid_button.setFixedSize(MAIN_ROUTINE_SUMMARY_VALID_BADGE_WIDTH, badge_height)
        valid_button.setFocusPolicy(Qt.NoFocus)
        valid_button.setCursor(Qt.PointingHandCursor)
        valid_button.setCheckable(True)
        valid_button.clicked.connect(self._set_main_routine_valid_only)
        self._main_routine_valid_button = valid_button
        layout.addWidget(valid_button)
        valid_separator = create_summary_separator(
            "mainRoutineSummaryValidSeparator"
        )
        layout.addWidget(valid_separator)
        self._main_routine_summary_valid_separator = valid_separator
        for key, label_text in (
            ("group", "그룹"),
            ("routine", "루틴"),
            ("stock", "종목"),
            ("operation", "운영"),
            ("waiting", "대기"),
            ("excluded", "제외"),
            ("review", "검토"),
        ):
            badge = QPushButton()
            badge.setObjectName("mainRoutineSummaryCountBadge")
            badge.setFixedSize(count_badge_width, badge_height)
            badge.setFocusPolicy(Qt.NoFocus)
            badge.setCursor(Qt.PointingHandCursor)
            badge.setCheckable(True)
            badge_layout = QHBoxLayout(badge)
            badge_layout.setContentsMargins(
                badge_horizontal_padding,
                0,
                badge_horizontal_padding,
                0,
            )
            badge_layout.setSpacing(badge_body_spacing)
            label = QLabel(label_text)
            label.setObjectName(f"mainRoutineSummary{key.title()}Label")
            label.setFixedWidth(label_slot_width)
            label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            label.setFont(summary_font)
            label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            value = QLabel("0")
            value.setObjectName(f"mainRoutineSummary{key.title()}Value")
            value.setFixedWidth(number_slot_width)
            value.setAlignment(Qt.AlignCenter)
            value.setFont(summary_font)
            value.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            badge_layout.addWidget(label)
            badge_layout.addWidget(value)
            layout.addWidget(badge)
            count_labels[key] = (label, value)
            count_buttons[key] = badge
            badge.clicked.connect(
                lambda checked=False, target_key=key:
                self._activate_main_routine_summary_badge(target_key, bool(checked))
            )

            if key == "stock":
                stock_separator = create_summary_separator(
                    "mainRoutineSummaryStockSeparator"
                )
                layout.addWidget(stock_separator)

        self._main_routine_summary_count_labels = count_labels
        self._main_routine_summary_count_buttons = count_buttons
        self._main_routine_level_buttons = {
            level: count_buttons[level]
            for level in ("group", "routine", "stock")
        }
        self._main_routine_summary_count_badge_width = count_badge_width
        self._main_routine_summary_number_slot_width = number_slot_width
        self._main_routine_summary_widget = summary
        MainWindow._update_main_routine_summary_badge_styles(self)
        return summary

    def _update_main_routine_summary(self, projection: dict[str, object]) -> None:
        count_labels = getattr(self, "_main_routine_summary_count_labels", {})
        badges = projection.get("count_badges")
        if not isinstance(badges, tuple):
            return
        badge_snapshot = tuple(
            (str(key), str(label_text), max(0, int(value or 0)))
            for key, label_text, value in badges
        )
        if badge_snapshot == getattr(
            self,
            "_main_routine_summary_badge_snapshot",
            None,
        ):
            return
        if isinstance(count_labels, dict) and isinstance(badges, tuple):
            for key, label_text, value in badges:
                labels = count_labels.get(str(key))
                if not isinstance(labels, tuple) or len(labels) != 2:
                    continue
                label, value_label = labels
                clean_label_text = str(label_text)
                clean_value_text = str(max(0, int(value or 0)))
                if label.text() != clean_label_text:
                    label.setText(clean_label_text)
                if value_label.text() != clean_value_text:
                    value_label.setText(clean_value_text)
        self._main_routine_summary_badge_snapshot = badge_snapshot
        MainWindow._update_main_routine_summary_badge_styles(self)

    def _activate_main_routine_summary_badge(
        self,
        key: str,
        checked: bool,
    ) -> None:
        clean_key = str(key or "").strip().lower()
        if clean_key in {"group", "routine"}:
            MainWindow._assign_main_routine_stock_scope(self, "all", True)
            self._main_routine_excluded_only = False
            self._main_routine_stock_scope = "all"
            self._set_main_routine_display_level(clean_key)
            return
        if clean_key == "stock":
            self._assign_main_routine_stock_scope("all", True)
            self._set_main_routine_display_level("stock")
            return
        if clean_key in {"operation", "waiting", "excluded", "review"}:
            self._set_main_routine_display_level("stock")
            self._set_main_routine_stock_scope(clean_key, checked)

    def _update_main_routine_summary_badge_styles(self) -> None:
        buttons = getattr(self, "_main_routine_summary_count_buttons", {})
        if not isinstance(buttons, dict):
            return
        scope = MainWindow._current_main_routine_stock_scope(self)
        level = str(getattr(self, "_main_routine_display_level", "") or "")
        active_by_key = {
            "group": level == "group",
            "routine": level == "routine",
            "stock": level == "stock",
            "operation": level == "stock" and scope == "operation",
            "waiting": level == "stock" and scope == "waiting",
            "excluded": level == "stock" and scope == "excluded",
            "review": level == "stock" and scope == "review",
        }
        for key, button in buttons.items():
            active = bool(active_by_key.get(key, False))
            button.setChecked(active)
            color = (
                AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR
                if active
                else MAIN_ROUTINE_BADGE_IDLE_TEXT_COLOR
            )
            border_color = (
                AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR
                if active
                else AUTO_TRADE_SETTING_BADGE_INACTIVE_COLOR
            )
            button.setStyleSheet(
                "QPushButton#mainRoutineSummaryCountBadge {"
                " background-color: transparent;"
                f" border: 1px solid {border_color};"
                " border-radius: 4px; padding: 0;"
                "}"
                "QPushButton#mainRoutineSummaryCountBadge QLabel {"
                f" color: {color}; border: none; background: transparent; padding: 0;"
                "}"
            )

    def _create_routine_filter_badge_area(self) -> QWidget:
        badge_area = QWidget()
        badge_area.setObjectName("mainRoutineFilterBadgeArea")
        badge_area.setFixedWidth(MAIN_ROUTINE_FILTER_BADGE_AREA_WIDTH)
        layout = QVBoxLayout(badge_area)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        layout.addWidget(
            self._create_main_routine_filter_separator("mainRoutineValidSeparator"),
            0,
            Qt.AlignHCenter,
        )
        layout.addSpacing(8)

        self._main_routine_metric_buttons = {}
        for metric, title in (
            ("holding", "보유"),
            ("price", "가격"),
            ("profit", "수익"),
            ("trade", "매매"),
            ("limit", "한도"),
        ):
            button = self._create_main_routine_filter_badge(
                title,
                f"mainRoutine{metric.title()}MetricBadge",
            )
            button.clicked.connect(
                lambda _checked=False, target_metric=metric:
                self._set_main_routine_metric_sort(target_metric)
            )
            self._main_routine_metric_buttons[metric] = button
            layout.addWidget(button, 0, Qt.AlignHCenter)

        layout.addSpacing(8)
        layout.addWidget(
            self._create_main_routine_filter_separator(
                "mainRoutineInitialBuySeparator"
            ),
            0,
            Qt.AlignHCenter,
        )
        layout.addSpacing(8)

        initial_buy_sort_button = self._create_main_routine_filter_badge(
            "금액",
            "mainRoutineInitialBuySortBadge",
        )
        initial_buy_sort_button.clicked.connect(
            self._toggle_main_routine_initial_buy_sort_mode
        )
        self._main_routine_initial_buy_sort_button = initial_buy_sort_button
        layout.addWidget(initial_buy_sort_button, 0, Qt.AlignHCenter)

        layout.addSpacing(8)
        layout.addWidget(
            self._create_main_routine_filter_separator(
                "mainRoutineColumnSortSeparator"
            ),
            0,
            Qt.AlignHCenter,
        )
        layout.addSpacing(8)

        self._main_routine_column_sort_buttons = {}
        for sort_key, title in (
            ("operation", "운영"),
            ("situation", "현황"),
            ("status", "상태"),
            ("method", "방식"),
            ("liquidation", "청산"),
        ):
            button = self._create_main_routine_filter_badge(
                title,
                f"mainRoutine{sort_key.title()}ColumnSortBadge",
            )
            button.clicked.connect(
                lambda _checked=False, target_key=sort_key:
                self._set_main_routine_column_sort(target_key)
            )
            self._main_routine_column_sort_buttons[sort_key] = button
            layout.addWidget(button, 0, Qt.AlignHCenter)

        layout.addStretch(1)
        self._update_main_routine_filter_badges()
        return badge_area

    def _create_main_routine_filter_badge(
        self,
        text: str,
        object_name: str,
    ) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(object_name)
        button.setFocusPolicy(Qt.NoFocus)
        button.setCursor(Qt.PointingHandCursor)
        button.setFixedSize(
            MAIN_ROUTINE_FILTER_BADGE_WIDTH,
            AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT,
        )
        return button

    def _create_main_routine_filter_separator(self, object_name: str) -> QFrame:
        separator = QFrame()
        separator.setObjectName(object_name)
        separator.setFrameShape(QFrame.NoFrame)
        separator.setFixedSize(52, 2)
        separator.setFocusPolicy(Qt.NoFocus)
        separator.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        separator.setAttribute(Qt.WA_StyledBackground, True)
        separator.setStyleSheet(
            f"QFrame#{object_name} {{"
            " background-color: #64748B;"
            " border: none;"
            " min-height: 2px;"
            " max-height: 2px;"
            "}"
        )
        return separator

    @staticmethod
    def _main_routine_filter_badge_style(
        text_color: str,
        border_color: str | None = None,
    ) -> str:
        return (
            auto_trade_setting_badge_stylesheet(
                "QPushButton",
                text_color=text_color,
                border_color=border_color or text_color,
            )
            + (
                "QPushButton {"
                f" min-height: {AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT - 2}px;"
                f" max-height: {AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT - 2}px;"
                "}"
            )
        )

    def _update_main_routine_filter_badges(self) -> None:
        disabled_style = (
            "QPushButton:disabled, QPushButton:disabled:hover {"
            " background-color: transparent;"
            " border: 1px solid #D1D5DB;"
            " border-radius: 4px;"
            " color: #9CA3AF;"
            " font-weight: 600;"
            " padding: 0 6px;"
            "}"
        )
        valid_only = bool(self._main_routine_valid_only)
        if self._main_routine_valid_button is not None:
            self._main_routine_valid_button.setChecked(valid_only)
            valid_text_color = (
                AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR
                if valid_only
                else MAIN_ROUTINE_BADGE_IDLE_TEXT_COLOR
            )
            self._main_routine_valid_button.setStyleSheet(
                self._main_routine_filter_badge_style(
                    valid_text_color,
                    AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR
                    if valid_only
                    else AUTO_TRADE_SETTING_BADGE_INACTIVE_COLOR,
                )
                + (
                    "QPushButton {"
                    f" min-height: {self._main_routine_valid_button.height() - 2}px;"
                    f" max-height: {self._main_routine_valid_button.height() - 2}px;"
                    "}"
                )
            )
            valid_separator = getattr(
                self,
                "_main_routine_summary_valid_separator",
                None,
            )
            if valid_separator is not None:
                valid_separator.setFixedHeight(
                    self._main_routine_valid_button.height()
                )

        for level, button in self._main_routine_level_buttons.items():
            active = level == self._main_routine_display_level
            text_color = (
                AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR
                if active
                else MAIN_ROUTINE_BADGE_IDLE_TEXT_COLOR
            )
            button.setStyleSheet(
                self._main_routine_filter_badge_style(
                    text_color,
                    AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR
                    if active
                    else AUTO_TRADE_SETTING_BADGE_INACTIVE_COLOR,
                )
            )

        available_metrics = MAIN_ROUTINE_METRIC_KEYS_BY_LEVEL.get(
            self._main_routine_display_level,
            frozenset(),
        )
        for metric, button in self._main_routine_metric_buttons.items():
            enabled = metric in available_metrics
            button.setEnabled(enabled)
            button.setCursor(
                Qt.PointingHandCursor if enabled else Qt.ArrowCursor
            )
            active = (
                enabled
                and self._main_routine_metric_sort_active
                and metric == self._main_routine_metric_sort_key
            )
            text_color = (
                AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR
                if active
                else MAIN_ROUTINE_BADGE_IDLE_TEXT_COLOR
            )
            button.setStyleSheet(
                self._main_routine_filter_badge_style(
                    text_color,
                    AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR
                    if active
                    else AUTO_TRADE_SETTING_BADGE_INACTIVE_COLOR,
                )
                + disabled_style
            )

        initial_buy_button = self._main_routine_initial_buy_sort_button
        if initial_buy_button is not None:
            initial_buy_enabled = self._main_routine_initial_buy_badge_enabled()
            next_mode = self._main_routine_initial_buy_sort_next_mode
            badge_text = "금액" if next_mode == "AMOUNT" else "주수"
            badge_color = (
                INITIAL_BUY_AMOUNT_COLOR
                if next_mode == "AMOUNT"
                else INITIAL_BUY_QUANTITY_COLOR
            )
            initial_buy_button.setText(badge_text)
            initial_buy_button.setEnabled(initial_buy_enabled)
            initial_buy_button.setCursor(
                Qt.PointingHandCursor if initial_buy_enabled else Qt.ArrowCursor
            )
            initial_buy_button.setStyleSheet(
                self._main_routine_filter_badge_style(
                    badge_color,
                    badge_color,
                )
                + disabled_style
            )

        column_sort_enabled = self._main_routine_display_level == "stock"
        for sort_key, button in self._main_routine_column_sort_buttons.items():
            button.setEnabled(column_sort_enabled)
            button.setCursor(
                Qt.PointingHandCursor if column_sort_enabled else Qt.ArrowCursor
            )
            active = (
                column_sort_enabled
                and sort_key == self._main_routine_column_sort_key
            )
            text_color = (
                AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR
                if active
                else MAIN_ROUTINE_BADGE_IDLE_TEXT_COLOR
            )
            button.setStyleSheet(
                self._main_routine_filter_badge_style(
                    text_color,
                    AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR
                    if active
                    else AUTO_TRADE_SETTING_BADGE_INACTIVE_COLOR,
                )
                + disabled_style
            )
        MainWindow._update_main_routine_summary_badge_styles(self)

    def _main_routine_selected_row_keys(self) -> tuple[tuple[str, str, str, str], ...]:
        selected_keys: list[tuple[str, str, str, str]] = []
        for index in self.routine_table.selectionModel().selectedRows():
            item = self.routine_table.item(index.row(), 0)
            if item is None:
                continue
            selected_keys.append(
                (
                    str(item.data(ROUTINE_ROW_KIND_ROLE) or ""),
                    str(item.data(ROUTINE_DEFINITION_ID_ROLE) or ""),
                    str(item.data(ROUTINE_INSTANCE_ID_ROLE) or ""),
                    str(item.data(ROUTINE_STOCK_PATH_ROLE) or ""),
                )
            )
        return tuple(selected_keys)

    def _reload_main_routine_table_preserving_view(self) -> None:
        selected_keys = self._main_routine_selected_row_keys()
        scroll_value = self.routine_table.verticalScrollBar().value()
        self.load_routine_table()
        wanted = set(selected_keys)
        selection_model = self.routine_table.selectionModel()
        selection_model.clearSelection()
        for row in range(self.routine_table.rowCount()):
            item = self.routine_table.item(row, 0)
            if item is None:
                continue
            key = (
                str(item.data(ROUTINE_ROW_KIND_ROLE) or ""),
                str(item.data(ROUTINE_DEFINITION_ID_ROLE) or ""),
                str(item.data(ROUTINE_INSTANCE_ID_ROLE) or ""),
                str(item.data(ROUTINE_STOCK_PATH_ROLE) or ""),
            )
            if key in wanted:
                selection_model.select(
                    self.routine_table.model().index(row, 0),
                    QItemSelectionModel.Select | QItemSelectionModel.Rows,
                )
        self.routine_table.verticalScrollBar().setValue(scroll_value)

    def _set_main_routine_valid_only(self, enabled: bool) -> None:
        self._main_routine_valid_only = bool(enabled)
        self._update_main_routine_filter_badges()
        self._reload_main_routine_table_preserving_view()

    def _current_main_routine_stock_scope(self) -> str:
        if bool(getattr(self, "_main_routine_excluded_only", False)):
            return "excluded"
        scope = str(
            getattr(self, "_main_routine_stock_scope", "all") or "all"
        ).strip().lower()
        return scope if scope in {
            "all", "normal", "operation", "waiting", "excluded", "review"
        } else "all"

    def _assign_main_routine_stock_scope(
        self,
        scope: str,
        enabled: bool,
    ) -> None:
        clean_scope = str(scope or "").strip().lower()
        if clean_scope not in {
            "all", "normal", "operation", "waiting", "excluded", "review"
        }:
            clean_scope = "all"
        current_scope = MainWindow._current_main_routine_stock_scope(self)
        if bool(enabled):
            target_scope = clean_scope
        elif clean_scope in {"operation", "waiting", "excluded", "review"}:
            target_scope = "all"
        else:
            target_scope = clean_scope
        if bool(enabled) and current_scope == clean_scope:
            target_scope = clean_scope
        self._main_routine_stock_scope = target_scope
        self._main_routine_excluded_only = target_scope == "excluded"

    def _set_main_routine_stock_scope(
        self,
        scope: str,
        enabled: bool,
    ) -> None:
        MainWindow._assign_main_routine_stock_scope(self, scope, enabled)
        self._update_main_routine_filter_badges()
        self._reload_main_routine_table_preserving_view()

    def _set_main_routine_excluded_only(self, enabled: bool) -> None:
        MainWindow._set_main_routine_stock_scope(self, "excluded", enabled)

    def _set_main_routine_display_level(self, level: str) -> None:
        clean_level = str(level or "").strip()
        if clean_level not in {"group", "routine", "stock"}:
            return


        definition_ids = set(self._routine_instance_ids_by_definition)
        instance_ids = {
            instance_id
            for values in self._routine_instance_ids_by_definition.values()
            for instance_id in values
        }
        if clean_level == "group":
            self._collapsed_routine_definition_ids.update(definition_ids)
        elif clean_level == "routine":
            self._collapsed_routine_definition_ids.difference_update(definition_ids)
            self._collapsed_routine_instance_ids.update(instance_ids)
        else:
            self._collapsed_routine_definition_ids.difference_update(definition_ids)
            self._collapsed_routine_instance_ids.difference_update(instance_ids)
        available_metrics = MAIN_ROUTINE_METRIC_KEYS_BY_LEVEL[clean_level]
        if (
            self._main_routine_metric_sort_active
            and self._main_routine_metric_sort_key not in available_metrics
        ):
            self._main_routine_metric_sort_key = ""
            self._main_routine_metric_sort_active = False
        if clean_level != "stock":
            self._main_routine_column_sort_key = ""
        self._main_routine_display_level = clean_level
        self._main_routine_display_level_applied = True
        self._update_main_routine_filter_badges()
        self._reload_main_routine_table_preserving_view()

    def _set_main_routine_metric_sort(self, metric: str) -> None:
        clean_metric = str(metric or "").strip()
        if clean_metric not in {"holding", "price", "profit", "trade", "limit"}:
            return
        available_metrics = MAIN_ROUTINE_METRIC_KEYS_BY_LEVEL.get(
            self._main_routine_display_level,
            frozenset(),
        )
        if clean_metric not in available_metrics:
            return
        self._main_routine_initial_buy_sort_mode = ""
        self._main_routine_column_sort_key = ""
        self._main_routine_metric_sort_key = clean_metric
        self._main_routine_metric_sort_active = True
        self._update_main_routine_filter_badges()
        self._reload_main_routine_table_preserving_view()

    def _toggle_main_routine_initial_buy_sort_mode(self) -> None:
        if not self._main_routine_initial_buy_badge_enabled():
            return
        target_mode = self._main_routine_initial_buy_sort_next_mode
        self._main_routine_initial_buy_sort_mode = target_mode
        self._main_routine_initial_buy_sort_next_mode = (
            "QUANTITY" if target_mode == "AMOUNT" else "AMOUNT"
        )
        self._main_routine_metric_sort_key = ""
        self._main_routine_metric_sort_active = False
        self._main_routine_column_sort_key = ""
        self._update_main_routine_filter_badges()
        self._reload_main_routine_table_preserving_view()

    def _set_main_routine_column_sort(self, sort_key: str) -> None:
        clean_key = str(sort_key or "").strip()
        if self._main_routine_display_level != "stock":
            return
        if clean_key not in {
            "operation",
            "situation",
            "status",
            "method",
            "liquidation",
        }:
            return
        self._main_routine_metric_sort_key = ""
        self._main_routine_metric_sort_active = False
        self._main_routine_initial_buy_sort_mode = ""
        self._main_routine_column_sort_key = clean_key
        self._update_main_routine_filter_badges()
        self._reload_main_routine_table_preserving_view()

    def _create_button_area(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(8)

        buttons = [
            self.btn_start,
            self.btn_auto_trade_setting,
            self.btn_log_view,
            self.btn_review_required,
            self.btn_close_all_windows,
            self.btn_exit,
        ]

        for button in buttons:
            button.setMinimumHeight(32)
            layout.addWidget(button)

        self.btn_exit.setObjectName("secondaryButton")
        return layout

    def _setup_routine_table(self) -> None:
        headers = list(ROUTINE_MONITORING_HEADERS)

        self.routine_table.setFont(main_monitoring_table_font())
        self.routine_table.setColumnCount(len(headers))
        self.routine_table.setHorizontalHeaderLabels(headers)

        routine_header = self.routine_table.horizontalHeader()
        routine_header.setMinimumSectionSize(0)
        routine_header.setSectionResizeMode(QHeaderView.Fixed)
        routine_header.setStretchLastSection(False)

        self.routine_table.setColumnWidth(0, ROUTINE_INSTANCE_NAME_WIDTH)
        for column in range(1, len(headers) - 1):
            self.routine_table.setColumnWidth(column, 0)
        routine_header.setSectionResizeMode(len(headers) - 1, QHeaderView.Stretch)
        self.routine_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.routine_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)

        routine_header.setSectionsClickable(True)
        routine_header.setSortIndicatorShown(True)
        self.routine_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.routine_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.routine_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.routine_table.verticalHeader().setDefaultSectionSize(24)
        self.routine_table.verticalHeader().setVisible(False)
        self.routine_table.horizontalHeader().setVisible(False)
        self.routine_table.setAlternatingRowColors(True)
        self.routine_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.routine_table.setMouseTracking(True)
        self.routine_table.viewport().setMouseTracking(True)
        self.routine_table._hovered_routine_definition_id = ""
        self._routine_tree_item_delegate = _RoutineTreeItemDelegate(self.routine_table)
        self.routine_table.setItemDelegateForColumn(0, self._routine_tree_item_delegate)

    def _apply_main_routine_table_height(
        self,
        total_groups: int,
        total_instances: int,
        total_stocks: int,
    ) -> None:
        def _to_int(value: object) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return -1

        signature = (
            _to_int(total_groups),
            _to_int(total_instances),
            _to_int(total_stocks),
        )
        if getattr(self, "_last_main_routine_table_height_signature", None) == signature:
            return
        table = self.routine_table
        row_height = max(1, int(table.verticalHeader().defaultSectionSize() or 0))
        total_rows = max(1, signature[0] + signature[1] + signature[2] + 1)
        desired_height = (
            total_rows * row_height
            + table.frameWidth() * 2
            + table.contentsMargins().top()
            + table.contentsMargins().bottom()
        )
        # The automatic height is a one-shot resize target, not a minimum-size
        # contract.  Keeping the table minimum at zero preserves manual window
        # shrinking after the registered group/routine/stock totals are applied.
        table.setMinimumHeight(0)
        target_window_height = max(
            int(self.minimumHeight()),
            int(self.height()) + desired_height - int(table.height()),
        )
        screen = self.screen()
        if screen is not None:
            available_height = max(0, int(screen.availableGeometry().height()))
            if available_height > 0:
                frame_overhead = max(
                    0,
                    int(self.frameGeometry().height()) - int(self.height()),
                )
                target_window_height = min(
                    target_window_height,
                    max(1, available_height - frame_overhead),
                )
        self.resize(int(self.width()), target_window_height)
        self._last_main_routine_table_height_signature = signature

    def _setup_running_stock_table(self) -> None:
        headers = [
            "코드",
            "종목",
            "루틴",
            "운영",
            "현황",
            "상태",
            "보유",
            "평단",
            "미수",
            "미도",
        ]

        self.running_stock_table.setFont(main_monitoring_table_font())
        self.running_stock_table.setColumnCount(len(headers))
        self.running_stock_table.setHorizontalHeaderLabels(headers)
        self.running_stock_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.running_stock_table.horizontalHeader().setStretchLastSection(True)
        self.running_stock_table.setColumnWidth(0, 75)
        self.running_stock_table.setColumnWidth(1, 130)
        self.running_stock_table.setColumnWidth(2, 140)
        self.running_stock_table.setColumnWidth(3, 75)
        self.running_stock_table.setColumnWidth(4, 55)
        self.running_stock_table.setColumnWidth(5, 100)
        self.running_stock_table.setColumnWidth(6, 80)
        self.running_stock_table.setColumnWidth(7, 90)
        self.running_stock_table.setColumnWidth(8, 65)
        self.running_stock_table.setColumnWidth(9, 65)
        self.running_stock_table.horizontalHeader().setSectionsClickable(True)
        self.running_stock_table.horizontalHeader().setSortIndicatorShown(True)
        self.running_stock_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.running_stock_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.running_stock_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.running_stock_table.verticalHeader().setDefaultSectionSize(24)
        self.running_stock_table.verticalHeader().setVisible(False)
        self.running_stock_table.setAlternatingRowColors(True)

    def _apply_main_dashboard_style(self, root: QWidget) -> None:
        root.setStyleSheet(
            """
            QWidget#mainDashboardRoot {
                background: #f6f8fb;
                color: #1f2937;
                font-family: "Malgun Gothic", "Segoe UI";
                font-size: 9pt;
            }
            QWidget#mainDashboardRoot QGroupBox {
                background: #ffffff;
                border: 1px solid #d7dde6;
                border-radius: 5px;
                margin-top: 12px;
                padding: 7px 6px 6px 6px;
                font-weight: 600;
                color: #243044;
            }
            QWidget#mainDashboardRoot QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 10px;
                padding: 0 4px;
                color: #111827;
            }
            QWidget#mainDashboardRoot QLabel {
                background: transparent;
                color: #1f2937;
            }
            QWidget#mainDashboardRoot QLabel#metricValue {
                color: #111827;
                font-weight: 600;
            }
            QWidget#mainDashboardRoot QLabel#fundValue {
                color: #0f172a;
                font-size: 12pt;
                font-weight: 700;
            }
            QWidget#mainDashboardRoot QComboBox {
                min-height: 24px;
                padding: 2px 8px;
                background: #ffffff;
                border: 1px solid #cfd6df;
                border-radius: 4px;
            }
            QWidget#mainDashboardRoot QComboBox#kiwoomAccountCombo {
                min-height: 24px;
                padding-left: 10px;
                padding-right: 10px;
                background: transparent;
                border: none;
                border-radius: 0;
                outline: none;
            }
            QWidget#mainDashboardRoot QComboBox#kiwoomAccountCombo::drop-down {
                width: 0;
                border: none;
                background: transparent;
            }
            QWidget#mainDashboardRoot QComboBox#kiwoomAccountCombo::down-arrow {
                width: 0;
                height: 0;
                image: none;
            }
            QWidget#mainDashboardRoot QPushButton {
                min-height: 28px;
                padding: 4px 10px;
                background: #eef2f7;
                border: 1px solid #c8d0db;
                border-radius: 5px;
                color: #1f2937;
                font-weight: 500;
            }
            QWidget#mainDashboardRoot QPushButton:hover {
                background: #e2e8f0;
            }
            QWidget#mainDashboardRoot QPushButton#dangerButton {
                background: #dc2626;
                border-color: #b91c1c;
                color: #ffffff;
                font-weight: 700;
            }
            QWidget#mainDashboardRoot QPushButton#warningButton {
                background: #f97316;
                border-color: #ea580c;
                color: #ffffff;
                font-weight: 700;
            }
            QWidget#mainDashboardRoot QPushButton#successButton {
                background: #16a34a;
                border-color: #15803d;
                color: #ffffff;
                font-weight: 700;
            }
            QWidget#mainDashboardRoot QPushButton#secondaryButton {
                background: #f8fafc;
                color: #334155;
            }
            QWidget#mainDashboardRoot QWidget#mainRoutineFilterBadgeArea {
                background: transparent;
            }
            QWidget#mainDashboardRoot QTableWidget {
                background: #ffffff;
                alternate-background-color: #f8fafc;
                gridline-color: #e5e7eb;
                border: 1px solid #d7dde6;
                border-radius: 4px;
                selection-background-color: #dbeafe;
                selection-color: #111827;
            }
            QWidget#mainDashboardRoot QHeaderView::section {
                background: #243044;
                color: #ffffff;
                padding: 4px 6px;
                border: 0;
                border-right: 1px solid #39465a;
                font-weight: 600;
            }
            """
        )

    def _connect_events(self) -> None:
        self.btn_exit.clicked.connect(self.close)
        self.btn_kiwoom_login.clicked.connect(self.login_kiwoom_manually)
        account_changed = getattr(self.account_combo, "currentIndexChanged", None)
        if account_changed is not None:
            account_changed.connect(self.on_kiwoom_account_changed)
        self.account_memo_edit.returnPressed.connect(
            self.save_current_account_memo_input
        )
        self.account_memo_edit.editingFinished.connect(
            self.save_current_account_memo_input
        )
        self.btn_account_authentication.clicked.connect(
            self.open_current_account_authentication
        )
        self.btn_account_requery.clicked.connect(self.requery_current_account_funds)
        account_context_menu = getattr(
            self.account_combo.view(),
            "customContextMenuRequested",
            None,
        )
        if account_context_menu is not None:
            account_context_menu.connect(self.open_kiwoom_account_context_menu)
        self.btn_emergency_stop.doubleClicked.connect(self.on_emergency_stop_clicked)
        self.btn_start.clicked.connect(self.start_global_auto_trades)
        self.btn_auto_trade_setting.clicked.connect(self.open_auto_trade_setting_window)
        self.btn_main_visible_early_close.clicked.connect(
            self.request_visible_monitoring_early_close
        )
        self.btn_close_all_windows.clicked.connect(
            self.close_all_persistent_feature_windows
        )
        self.btn_log_view.clicked.connect(self.open_event_record_window)
        self.btn_review_required.clicked.connect(self.open_review_required_window)
        self.routine_table.horizontalHeader().sectionClicked.connect(self.sort_main_routine_table_by_column)
        self.routine_table.customContextMenuRequested.connect(self.open_routine_context_menu)
        self._routine_tree_interaction_controller = _RoutineTreeInteractionController(self)
        self.routine_table.viewport().installEventFilter(
            self._routine_tree_interaction_controller
        )
        self.running_stock_table.horizontalHeader().sectionClicked.connect(self.sort_main_running_table_by_column)
        self.running_stock_table.itemDoubleClicked.connect(
            self.on_running_stock_table_item_double_clicked
        )

    def on_running_stock_table_item_double_clicked(
        self,
        item: QTableWidgetItem,
    ) -> None:
        """Open the common instance chart only from the stock-code column."""
        if item.column() != 0:
            return
        row = item.row()
        if row < 0 or row >= self.running_stock_table.rowCount():
            return
        stock_code = item.text().strip()
        if not stock_code:
            return
        open_stock_instance_chart(
            stock_code,
            trade_date=None,
            parent=self,
        )

    def handle_routine_stock_code_double_click(self, row: int) -> bool:
        """Open the chart from the visible monitoring routine-tree code token."""
        item = self.routine_table.item(row, 0)
        if (
            item is None
            or str(item.data(ROUTINE_ROW_KIND_ROLE) or "") != ROUTINE_ROW_STOCK
        ):
            return False
        stock_code = str(item.data(ROUTINE_STOCK_CODE_ROLE) or "").strip()
        if not stock_code:
            return False
        opened = open_stock_instance_chart(
            stock_code,
            trade_date=None,
            parent=self,
        )
        if opened is None:
            return False
        clear_main_monitoring_chart_open_selection(self)
        return True

    def startup_recovery_stock_state_paths(self) -> list[Path]:
        return [stock_dir / "state.json" for stock_dir in self.all_runtime_stock_dirs()]

    def _production_recovery_required(self) -> bool:
        api = getattr(self, "kiwoom_api", None)
        checker = getattr(api, "is_connected", None)
        if not callable(checker):
            return False
        try:
            return checker() is True
        except Exception:
            return False

    def _stop_production_recovery_timers(self) -> None:
        host = getattr(self, "_main_monitoring_auto_trade_operation_host", None)
        stopper = getattr(host, "stop_operation_timers", None)
        if callable(stopper):
            stopper()

    def _clear_completed_recovery_handoff(self) -> None:
        """Invalidate the process-local completed Recovery evidence handoff."""
        self._latest_completed_recovery_snapshot = None
        self._latest_completed_recovery_identity = None

    def _publish_completed_recovery_handoff(
        self,
        identity: RecoverySessionIdentity,
        snapshot: BrokerAccountSnapshot,
    ) -> bool:
        """Publish only an exact, final COMPLETED Recovery snapshot reference."""
        context = production_recovery_registry.snapshot()
        if (
            not isinstance(identity, RecoverySessionIdentity)
            or not isinstance(snapshot, BrokerAccountSnapshot)
            or context is None
            or context.identity != identity
            or context.account_status != ACCOUNT_COMPLETED
            or not snapshot.is_complete
            or bool(snapshot.errors)
            or snapshot.account_no != identity.account_no
            or snapshot.trading_day != identity.trading_day
            or snapshot.recovery_session_id != identity.recovery_session_id
            or snapshot.requested_at != identity.requested_at
            or snapshot.request_id != recovery_request_id(identity, "ACCOUNT")
            or not snapshot.completed_at
        ):
            MainWindow._clear_completed_recovery_handoff(self)
            return False
        self._latest_completed_recovery_identity = identity
        self._latest_completed_recovery_snapshot = snapshot
        return True

    def latest_completed_recovery_handoff(self) -> dict[str, object] | None:
        """Return current process-local handoff only while all identities match."""
        identity = getattr(self, "_latest_completed_recovery_identity", None)
        snapshot = getattr(self, "_latest_completed_recovery_snapshot", None)
        context = production_recovery_registry.snapshot()
        api = getattr(self, "kiwoom_api", None)
        login_session_id = str(
            getattr(api, "login_session_id", lambda: "")() or ""
        ).strip()
        account_no = str(self.selected_account_no() or "").strip()
        trading_day = datetime.now().date().isoformat()
        if (
            not isinstance(identity, RecoverySessionIdentity)
            or not isinstance(snapshot, BrokerAccountSnapshot)
            or context is None
            or context.identity != identity
            or context.account_status != ACCOUNT_COMPLETED
            or identity.account_no != account_no
            or identity.login_session_id != login_session_id
            or identity.trading_day != trading_day
            or snapshot.account_no != identity.account_no
            or snapshot.trading_day != identity.trading_day
            or snapshot.recovery_session_id != identity.recovery_session_id
            or snapshot.requested_at != identity.requested_at
            or snapshot.request_id != recovery_request_id(identity, "ACCOUNT")
            or not snapshot.is_complete
            or bool(snapshot.errors)
        ):
            return None
        return {
            "identity": identity,
            "snapshot": snapshot,
            "recovery_status": context.account_status,
            "holdings_complete": True,
            "open_orders_complete": True,
            "account_display": masked_account_no(identity.account_no),
        }

    def _on_main_operation_cycle_completed(self, _result: dict[str, object]) -> None:
        """Refresh monitoring from canonical readers after an operation cycle."""
        if getattr(self, "_main_window_closing", False):
            return
        completed_identity = getattr(
            self, "_latest_completed_recovery_identity", None
        )
        if (
            isinstance(completed_identity, RecoverySessionIdentity)
            and completed_identity.trading_day
            != datetime.now().date().isoformat()
        ):
            MainWindow._clear_completed_recovery_handoff(self)
        self.last_buffer_response_cycle_reconciliation_result = (
            reconcile_main_window_buffer_response_cycle(self)
        )
        self.refresh_auto_trade_assignment_views()

    def _production_recovery_status_result(self) -> dict[str, object]:
        context = production_recovery_registry.snapshot()
        status = context.account_status if context is not None else "NOT_STARTED"
        reasons: list[str] = []
        if context is not None:
            for stock in context.stocks:
                reasons.extend(stock.reason_codes)
            api = getattr(self, "kiwoom_api", None)
            login_session_id = str(
                getattr(api, "login_session_id", lambda: "")() or ""
            ).strip()
            if (
                context.identity.login_session_id != login_session_id
                or context.identity.account_no != self.selected_account_no()
                or context.identity.trading_day != datetime.now().date().isoformat()
            ):
                MainWindow._clear_completed_recovery_handoff(self)
                status = "STALE"
                reasons.append("STALE_RECOVERY_SESSION")
        labels = {
            "NOT_STARTED": "복구 대기",
            "COLLECTING": "Broker 상태 수집",
            "RECONCILING": "Runtime 대조",
            "REVIEW_REQUIRED": "검토 필요",
            "COMPLETED": "복구 완료",
            "FAILED": "복구 실패",
            "STALE": "복구 세션 만료",
        }
        self.auto_status_label.setText(
            f"전체 자동매매 상태: {labels.get(status, status)}"
        )
        return {
            "status": status,
            "operator_approval_allowed": status in {
                ACCOUNT_COMPLETED,
                ACCOUNT_REVIEW_REQUIRED,
            },
            "review_reasons": list(dict.fromkeys(reasons)),
            "production_recovery": True,
        }

    @staticmethod
    def _read_recovery_runtime_list(path: Path, field: str) -> list[dict[str, object]]:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get(field), list):
            raise ValueError(f"{path.name}.{field} must be a list")
        return [dict(item) for item in data[field] if isinstance(item, dict)]

    def _registered_recovery_stock_runtime(
        self,
        *,
        account_no: str,
    ) -> list[tuple[str, dict[str, object] | None]]:
        positions = self._read_recovery_runtime_list(
            RECOVERY_POSITIONS_PATH,
            "positions",
        )
        result: list[tuple[str, dict[str, object] | None]] = []
        from gui_auto_trade_runtime import all_registered_stock_dirs

        for stock_dir in all_registered_stock_dirs():
            code = stock_dir.name.split("_", 1)[0].strip()
            if not code:
                continue
            matches = [
                item
                for item in positions
                if str(item.get("account_no") or "").strip() == account_no
                and str(item.get("code") or item.get("stock_code") or "").strip()
                == code
            ]
            if len(matches) > 1:
                raise ValueError(f"duplicate Runtime position: {code}")
            result.append((code, matches[0] if matches else None))
        return result

    def _record_production_recovery_review(
        self,
        identity,
        *,
        stock_code: str,
        reason_code: str,
        broker_evidence: object = None,
        runtime_evidence: object = None,
    ) -> None:
        canonical_reason = {
            "HOLDINGS_SNAPSHOT_FAILED": "HOLDING_SNAPSHOT_FAILED",
            "OPEN_ORDERS_SNAPSHOT_FAILED": "OPEN_ORDER_SNAPSHOT_FAILED",
            "RECOVERY_IDENTITY_MISMATCH": "STALE_RECOVERY_SESSION",
            "INVALID_STOCK_CODE": "RECOVERY_FAILED",
            "DUPLICATE_BROKER_HOLDING": "RECOVERY_PARTIAL",
            "HOLDINGS_SNAPSHOT_ADAPTER_MISSING": "RECOVERY_FAILED",
            "OPEN_ORDERS_SNAPSHOT_ADAPTER_MISSING": "RECOVERY_FAILED",
        }.get(reason_code, reason_code)
        write_production_recovery_review(
            {
                "account_no": identity.account_no,
                "trading_day": identity.trading_day,
                "login_session_id": identity.login_session_id,
                "recovery_session_id": identity.recovery_session_id,
                "stock_code": stock_code,
                "reason_code": canonical_reason,
                "detected_at": datetime.now().isoformat(timespec="seconds"),
                "broker_evidence": broker_evidence or {},
                "runtime_evidence": {
                    "original_reason_code": reason_code,
                    "evidence": runtime_evidence or {},
                },
                "status": "OPEN",
            },
            RECOVERY_BROKER_HOLDINGS_PATH,
        )

    def _fail_production_recovery(
        self,
        identity,
        reason_code: str,
        *,
        broker_evidence: object = None,
    ) -> None:
        MainWindow._clear_completed_recovery_handoff(self)
        self._production_recovery_failure_reason_code = str(reason_code or "")
        production_recovery_registry.fail_account(identity)
        self._stop_production_recovery_timers()
        self._record_production_recovery_review(
            identity,
            stock_code="",
            reason_code=reason_code,
            broker_evidence=broker_evidence,
        )
        self._production_recovery_status_result()
        append_owner_event_once(
            self,
            f"recovery:{identity.recovery_session_id}",
            "RECOVERY_FAILED",
            severity="ERROR",
            result="FAILED",
            source="MainWindow._fail_production_recovery",
            target_type="RECOVERY_SESSION",
            target_id=identity.recovery_session_id,
            correlation_id=identity.recovery_session_id,
            reason_code=str(reason_code or "RECOVERY_FAILED"),
        )

    def _finish_production_recovery(self, identity) -> None:
        holdings = self._production_recovery_parts.get("HOLDINGS")
        open_orders = self._production_recovery_parts.get("OPEN_ORDERS")
        if not isinstance(holdings, BrokerSnapshotPart) or not isinstance(
            open_orders,
            BrokerSnapshotPart,
        ):
            return
        if (
            holdings.recovery_session_id != identity.recovery_session_id
            or open_orders.recovery_session_id != identity.recovery_session_id
        ):
            self._fail_production_recovery(identity, "RECOVERY_IDENTITY_MISMATCH")
            return

        snapshot = combine_account_snapshot(identity, holdings, open_orders)
        if not snapshot.is_complete:
            self._fail_production_recovery(
                identity,
                "INCOMPLETE_BROKER_SNAPSHOT",
                broker_evidence={"errors": list(snapshot.errors)},
            )
            return
        try:
            runtime_orders = self._read_recovery_runtime_list(
                RECOVERY_ORDER_QUEUE_PATH,
                "orders",
            )
            stock_runtime = self._registered_recovery_stock_runtime(
                account_no=identity.account_no,
            )
        except Exception as exc:
            self._fail_production_recovery(
                identity,
                "DAMAGED_RUNTIME",
                broker_evidence={"error": str(exc)},
            )
            return

        result = reconcile_production_recovery_snapshot(
            identity=identity,
            snapshot=snapshot,
            stock_runtime=stock_runtime,
            runtime_orders=runtime_orders,
        )
        for stock_result in result.get("stock_results", ()):
            if not stock_result.review_required:
                continue
            broker_items = [
                asdict(item)
                for item in (*snapshot.holdings, *snapshot.open_orders)
                if item.stock_code == stock_result.stock_code
            ]
            runtime_position = next(
                (
                    position
                    for code, position in stock_runtime
                    if code == stock_result.stock_code
                ),
                None,
            )
            for reason_code in stock_result.reason_codes:
                self._record_production_recovery_review(
                    identity,
                    stock_code=stock_result.stock_code,
                    reason_code=reason_code,
                    broker_evidence={"items": broker_items},
                    runtime_evidence={"position": runtime_position or {}},
                )

        context = production_recovery_registry.snapshot()
        current_session_participants = (
            auto_trade_current_session_operation_participant_codes(self)
        )
        if (
            recovery_account_allows_isolated_stock_operation(context)
            and current_session_participants
        ):
            host = self.main_monitoring_auto_trade_operation_host()
            starter = getattr(host, "start_after_recovery", None)
            if callable(starter):
                timer_result = starter(identity)
                self._production_recovery_timer_start_result = timer_result
                if (
                    not isinstance(timer_result, dict)
                    or timer_result.get("started") is not True
                ):
                    reason_code = (
                        str(timer_result.get("reason_code") or "")
                        if isinstance(timer_result, dict)
                        else ""
                    )
                    LOGGER.error(
                        "Recovery timer start failed: %s",
                        reason_code or "RECOVERY_TIMER_START_FAILED",
                    )
                    self._fail_production_recovery(
                        identity,
                        reason_code or "RECOVERY_TIMER_START_FAILED",
                    )
                    return
        else:
            self._stop_production_recovery_timers()
            self._production_recovery_timer_start_result = {
                "started": False,
                "started_count": 0,
                "reason_code": (
                    "NO_CURRENT_SESSION_OPERATION_PARTICIPATION"
                    if recovery_account_allows_isolated_stock_operation(context)
                    else "RECOVERY_NOT_OPERATION_READY"
                ),
            }
        self._production_recovery_status_result()
        context = production_recovery_registry.snapshot()
        if context is not None and context.identity == identity:
            MainWindow._publish_completed_recovery_handoff(self, identity, snapshot)
            warning = context.account_status == ACCOUNT_REVIEW_REQUIRED
            append_owner_event_once(
                self,
                f"recovery:{identity.recovery_session_id}",
                "RECOVERY_WARNING" if warning else "RECOVERY_COMPLETED",
                severity="WARNING" if warning else "INFO",
                result="COMPLETED",
                source="MainWindow._finish_production_recovery",
                target_type="RECOVERY_SESSION",
                target_id=identity.recovery_session_id,
                correlation_id=identity.recovery_session_id,
                reason_code=str(context.account_status or ""),
            )
            self._request_account_funds_after_recovery(identity)
            self.update_budget_panel()
            self._resume_limit_responses_after_recovery(identity)
        window = getattr(self, "auto_trade_setting_window", None)
        updater = getattr(window, "update_startup_recovery_controls", None)
        if callable(updater):
            updater()
        self.update_review_required_button_text()

    def _resume_limit_responses_after_recovery(self, identity) -> None:
        """Settle Buffer and Routine ownership before evaluating stock limits."""
        self.last_buffer_response_recovery_coordination_result = (
            coordinate_main_window_buffer_response(
                self,
                chejan_result={
                    "recorded": True,
                    "stage": "RECOVERY_POSITION_REEVALUATION",
                },
            )
            if main_window_buffer_response_integration_ready(self)
            else {"reason": "BUFFER_RESPONSE_INTEGRATION_NOT_READY"}
        )
        self.last_buffer_response_early_close_resume_result = (
            resume_main_window_buffer_early_close(
                self,
                recovery_identity=identity,
            )
        )
        self.last_buffer_response_immediate_preparation_resume_result = (
            resume_main_window_buffer_immediate_liquidation_preparation(
                self,
                recovery_identity=identity,
            )
        )
        self.last_buffer_response_immediate_dispatch_resume_result = (
            dispatch_ready_main_window_buffer_immediate_preparations(
                self,
                preparation_resume_result=(
                    self.last_buffer_response_immediate_preparation_resume_result
                ),
            )
        )
        self.last_routine_limit_recovery_result = (
            resume_main_window_routine_limit_responses(
                self,
                buffer_result=(
                    self.last_buffer_response_recovery_coordination_result
                ),
            )
        )
        self.last_stock_limit_recovery_result = (
            resume_main_window_stock_limit_responses(
                self,
                higher_priority_result=(
                    self.last_buffer_response_recovery_coordination_result
                ),
                routine_priority_result=self.last_routine_limit_recovery_result,
            )
        )

    def _request_account_funds_after_recovery(self, identity) -> dict[str, object]:
        """Refresh the UI projection only for the account Recovery just completed."""
        recovered_account = str(getattr(identity, "account_no", "") or "").strip()
        if not recovered_account or recovered_account != self.selected_account_no():
            return {"ok": False, "status": "STALE_RECOVERY_ACCOUNT"}
        try:
            projection = object.__getattribute__(self, "_account_funds_projection")
        except (AttributeError, RuntimeError):
            projection = None
        snapshot = getattr(projection, "snapshot", None)
        if (
            snapshot is not None
            and snapshot.account_id == recovered_account
            and (
                snapshot.status in (ACCOUNT_FUNDS_LOADING, ACCOUNT_FUNDS_READY)
                or (
                    snapshot.status == ACCOUNT_FUNDS_FAILED
                    and snapshot.error_kind == ACCOUNT_AUTHENTICATION_REQUIRED
                )
            )
        ):
            return {
                "ok": snapshot.status == ACCOUNT_FUNDS_READY,
                "status": snapshot.status,
                "account_id": recovered_account,
            }
        return self.request_account_funds()

    def _on_production_recovery_snapshot(
        self,
        identity,
        kind: str,
        result: object,
    ) -> None:
        current = self._production_recovery_identity
        if (
            current is None
            or current.recovery_session_id != identity.recovery_session_id
        ):
            return
        payload = result if isinstance(result, dict) else {}
        snapshot = payload.get("snapshot")
        if (
            payload.get("ok") is not True
            or not isinstance(snapshot, BrokerSnapshotPart)
            or snapshot.is_complete is not True
        ):
            self._fail_production_recovery(
                identity,
                f"{kind}_SNAPSHOT_FAILED",
                broker_evidence={
                    "errors": list(payload.get("errors") or []),
                    "error": str(payload.get("error") or ""),
                    "result": payload.get("result"),
                },
            )
            return
        self._production_recovery_parts[kind] = snapshot
        if kind == "HOLDINGS":
            try:
                self._production_recovery_holding_failure_resolution_result = (
                    resolve_account_holding_snapshot_failures(
                        account_no=identity.account_no,
                        trading_day=identity.trading_day,
                        login_session_id=identity.login_session_id,
                        successful_recovery_session_id=identity.recovery_session_id,
                        broker_holdings_path=RECOVERY_BROKER_HOLDINGS_PATH,
                    )
                )
            except Exception as exc:
                self._production_recovery_holding_failure_resolution_result = {
                    "status": "RESOLUTION_FAILED",
                    "resolved_count": 0,
                    "reason": str(exc),
                }
        if kind == "HOLDINGS" and "OPEN_ORDERS" not in self._production_recovery_parts:
            self._request_production_recovery_snapshot(identity, "OPEN_ORDERS")
            return
        self._finish_production_recovery(identity)

    def _request_production_recovery_snapshot(self, identity, kind: str) -> bool:
        api = getattr(self, "kiwoom_api", None)
        requester_name = {
            "HOLDINGS": "request_account_holdings_snapshot",
            "OPEN_ORDERS": "request_open_orders_snapshot",
        }.get(kind, "")
        requester = getattr(api, requester_name, None)
        if not callable(requester):
            self._fail_production_recovery(
                identity,
                f"{kind}_SNAPSHOT_ADAPTER_MISSING",
            )
            return False
        response = requester(
            identity,
            callback=lambda result: self._on_production_recovery_snapshot(
                identity,
                kind,
                result,
            ),
        )
        return not (
            isinstance(response, dict)
            and response.get("ok") is False
        )

    def start_production_recovery(self) -> bool:
        MainWindow._clear_completed_recovery_handoff(self)
        self._stop_production_recovery_timers()
        production_recovery_registry.invalidate("new login/account recovery")
        self._production_recovery_parts = {}
        self._production_recovery_identity = None
        self._production_recovery_failure_reason_code = ""
        self._production_recovery_timer_start_result = None

        api = getattr(self, "kiwoom_api", None)
        account_no = self.selected_account_no()
        login_session_id = str(
            getattr(api, "login_session_id", lambda: "")() or ""
        ).strip()
        if not account_no or not login_session_id:
            self._production_recovery_status_result()
            return False
        identity = create_recovery_session_identity(
            login_session_id=login_session_id,
            account_no=account_no,
            trading_day=datetime.now().date().isoformat(),
            requested_at=datetime.now().isoformat(timespec="microseconds"),
        )
        self._production_recovery_identity = identity
        production_recovery_registry.begin_recovery(identity)
        production_recovery_registry.mark_collecting(identity)
        self._production_recovery_status_result()
        return self._request_production_recovery_snapshot(identity, "HOLDINGS")

    def _restart_failed_production_recovery_after_account_funds_success(
        self,
        account_no: object,
    ) -> bool:
        """Retry the existing Recovery entrypoint after verified account funds."""
        account = str(account_no or "").strip()
        if not account or not self._kiwoom_connected_for_budget():
            return False
        if account != str(self.selected_account_no() or "").strip():
            return False
        try:
            authentication_states = object.__getattribute__(
                self,
                "_account_authentication_states",
            )
            query_states = object.__getattribute__(self, "_account_query_states")
            projection = object.__getattribute__(self, "_account_funds_projection")
            window_identity = object.__getattribute__(
                self,
                "_production_recovery_identity",
            )
        except (AttributeError, RuntimeError):
            return False
        if authentication_states.get(account) != ACCOUNT_FUNDS_READY:
            return False
        if query_states.get(account) != ACCOUNT_FUNDS_READY:
            return False

        snapshot = projection.snapshot
        if snapshot.status != ACCOUNT_FUNDS_READY or snapshot.account_id != account:
            return False
        for amount in (snapshot.deposit, snapshot.orderable_cash):
            if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
                return False

        context = production_recovery_registry.snapshot()
        if (
            context is None
            or context.account_status != ACCOUNT_FAILED
            or window_identity is None
            or context.identity != window_identity
        ):
            return False

        identity = context.identity
        api = getattr(self, "kiwoom_api", None)
        login_session_reader = getattr(api, "login_session_id", None)
        try:
            login_session_id = (
                str(login_session_reader() or "").strip()
                if callable(login_session_reader)
                else ""
            )
        except Exception:
            return False
        if (
            identity.account_no != account
            or identity.login_session_id != login_session_id
            or identity.trading_day != datetime.now().date().isoformat()
        ):
            return False
        return self.start_production_recovery() is True

    def production_recovery_gate_for_stock(
        self,
        stock_code: str,
        *,
        caller_name: str,
    ):
        api = getattr(self, "kiwoom_api", None)
        context = production_recovery_registry.snapshot()
        return check_production_recovery_gate(
            login_session_id=str(
                getattr(api, "login_session_id", lambda: "")() or ""
            ).strip(),
            account_no=self.selected_account_no(),
            trading_day=datetime.now().date().isoformat(),
            stock_code=stock_code,
            recovery_session_id=(
                context.identity.recovery_session_id if context is not None else ""
            ),
            caller_name=caller_name,
        )

    def production_recovery_stock_is_review_required(self, stock_code: str) -> bool:
        return recovery_stock_is_review_required(stock_code)

    def routine_recovery_block_message(self, action: str) -> str:
        """Format a routine-operation block without exposing recovery internals."""
        title = f"{str(action or '루틴 작업').strip()} 불가"
        api = getattr(self, "kiwoom_api", None)
        checker = getattr(api, "is_connected", None)
        try:
            connected = callable(checker) and checker() is True
        except Exception:
            connected = False

        if not connected:
            return "키움 서버에 로그인되어 있지 않습니다."
        elif not self.selected_account_no():
            detail = (
                "사용할 계좌 정보가 아직 확인되지 않았습니다.\n"
                "로그인과 계좌 선택 상태를 확인해 주세요."
            )
        else:
            context = production_recovery_registry.snapshot()
            status = str(
                getattr(context, "account_status", "NOT_STARTED") or "NOT_STARTED"
            ).strip()
            if status in {"COLLECTING", "RECONCILING"}:
                detail = (
                    "프로그램 시작 후 기존 운영 상태를 확인하고 있습니다.\n"
                    "확인이 끝난 뒤 다시 시도해 주세요."
                )
            elif status in {"FAILED", "STALE"}:
                detail = (
                    "이전 운영 상태를 확인하지 못했습니다.\n"
                    "운영 상태와 로그를 확인해 주세요."
                )
            elif status == "REVIEW_REQUIRED":
                detail = (
                    "확인이 필요한 운영 항목이 남아 있습니다.\n"
                    "검토관리에서 상태를 확인해 주세요."
                )
            else:
                detail = (
                    "프로그램 시작 후 운영 상태 확인이 아직 완료되지 않았습니다.\n"
                    "잠시 후 다시 시도해 주세요."
                )
        return f"{title}\n\n{detail}"

    def show_routine_recovery_block_toast(self, action: str) -> None:
        show_toast(
            parent=self,
            message=self.routine_recovery_block_message(action),
            duration_ms=2500,
            position="center",
        )

    def production_recovery_block_user_message(self, decision) -> str:
        reason_code = str(getattr(decision, "reason_code", "") or "").strip()
        api = getattr(self, "kiwoom_api", None)
        connected = False
        checker = getattr(api, "is_connected", None)
        if callable(checker):
            try:
                connected = checker() is True
            except Exception:
                connected = False

        if reason_code == RECOVERY_CONTEXT_MISSING:
            if not connected:
                return "키움 서버에 로그인되어 있지 않습니다."
            login_session_id = str(
                getattr(api, "login_session_id", lambda: "")() or ""
            ).strip()
            if not login_session_id:
                return (
                    "로그인 세션 정보를 확인할 수 없습니다. "
                    "키움 서버에 다시 로그인하십시오."
                )
            if not self.selected_account_no():
                return "운영할 계좌를 선택하십시오."
            evidence = tuple(getattr(decision, "evidence", ()) or ())
            if any(str(item).startswith("registry_error=") for item in evidence):
                return (
                    "Recovery 데이터를 읽을 수 없습니다. "
                    "복구를 다시 실행한 후 운영을 시작하십시오."
                )
            return (
                "운영 시작에 필요한 Recovery 정보를 확인할 수 없습니다. "
                "로그인과 계좌 선택 상태를 확인한 후 Recovery를 다시 실행하십시오."
            )

        messages = {
            RECOVERY_NOT_STARTED: (
                "운영 시작 전에 Recovery가 완료되지 않았습니다. "
                "로그인과 계좌 선택 후 Recovery를 완료하십시오."
            ),
            RECOVERY_IN_PROGRESS: (
                "Recovery가 진행 중입니다. 복구가 완료된 후 다시 시도하십시오."
            ),
            RECOVERY_ACCOUNT_REVIEW_REQUIRED: (
                "복구가 필요한 종목이 남아 있습니다. "
                "검토관리에서 해당 종목을 처리하십시오."
            ),
            RECOVERY_IDENTITY_MISMATCH: (
                "현재 로그인 또는 계좌와 Recovery 정보가 일치하지 않습니다. "
                "Recovery를 다시 실행하십시오."
            ),
            RECOVERY_STALE_SESSION: (
                "이전 Recovery 정보는 현재 세션에서 사용할 수 없습니다. "
                "Recovery를 다시 실행하십시오."
            ),
            RECOVERY_STOCK_PENDING: (
                "선택한 종목의 Recovery가 아직 완료되지 않았습니다."
            ),
            RECOVERY_STOCK_REVIEW_REQUIRED: (
                "선택한 종목은 복구 검토 대상입니다. "
                "검토관리에서 해당 종목을 처리하십시오."
            ),
            RECOVERY_STOCK_FAILED: (
                "선택한 종목의 Recovery에 실패했습니다. "
                "검토관리에서 상태를 확인하십시오."
            ),
        }
        if reason_code == RECOVERY_ACCOUNT_FAILED:
            failure_reason = str(
                getattr(self, "_production_recovery_failure_reason_code", "") or ""
            ).strip()
            if failure_reason == "DAMAGED_RUNTIME":
                return (
                    "Runtime 데이터를 읽을 수 없어 Recovery에 실패했습니다. "
                    "검토관리에서 Runtime 상태를 확인하십시오."
                )
            if failure_reason in {
                "INCOMPLETE_BROKER_SNAPSHOT",
                "HOLDINGS_SNAPSHOT_FAILED",
                "OPEN_ORDERS_SNAPSHOT_FAILED",
                "HOLDINGS_SNAPSHOT_ADAPTER_MISSING",
                "OPEN_ORDERS_SNAPSHOT_ADAPTER_MISSING",
            }:
                return (
                    "계좌의 보유 또는 미체결 정보를 확인하지 못했습니다. "
                    "키움 연결 상태를 확인한 후 Recovery를 다시 실행하십시오."
                )
            if failure_reason == "RECOVERY_TIMER_START_FAILED":
                return (
                    "운영 주기 실행을 시작하지 못했습니다. "
                    "로그를 확인한 후 Recovery를 다시 실행하십시오."
                )
            if failure_reason == "RECOVERY_NO_RESTORED_STOCK":
                return (
                    "Recovery가 완료된 운영 대상 종목이 없습니다. "
                    "검토관리에서 종목 상태를 확인하십시오."
                )
            return (
                "계좌 Recovery에 실패했습니다. "
                "로그인과 계좌 상태를 확인한 후 Recovery를 다시 실행하십시오."
            )
        return messages.get(
            reason_code,
            "운영 시작에 필요한 복구 상태를 확인할 수 없습니다. "
            "로그인, 계좌 선택 및 Recovery 상태를 확인하십시오.",
        )

    def filter_start_targets_by_production_recovery(
        self,
        targets: list[tuple[Path, str, str]],
        *,
        caller_name: str,
    ) -> dict[str, object]:
        eligible: list[tuple[Path, str, str]] = []
        excluded_review: list[str] = []
        for stock_dir, code, name in targets:
            decision = self.production_recovery_gate_for_stock(
                code,
                caller_name=caller_name,
            )
            if decision.allowed:
                eligible.append((stock_dir, code, name))
                continue
            if decision.reason_code == RECOVERY_STOCK_REVIEW_REQUIRED:
                excluded_review.append(f"{code} {name}")
                continue
            return {
                "allowed": False,
                "reason": decision.reason_code,
                "user_message": self.production_recovery_block_user_message(
                    decision
                ),
                "eligible": tuple(eligible),
                "excluded_review": tuple(excluded_review),
            }
        return {
            "allowed": True,
            "reason": "RECOVERY_COMPLETED",
            "eligible": tuple(eligible),
            "excluded_review": tuple(excluded_review),
        }

    def refresh_startup_recovery_status(self) -> dict[str, object]:
        if self._production_recovery_required():
            result = self._production_recovery_status_result()
            self._startup_recovery_result = result
            return result
        stock_state_paths = self.startup_recovery_stock_state_paths()
        self._startup_runtime_initialization_result = (
            initialize_pristine_startup_runtime()
        )
        result = assess_startup_recovery(
            stock_state_paths=stock_state_paths,
        )
        self._startup_recovery_result = result
        status = str(result.get("status") or "INVALID_RUNTIME")
        if (
            self._startup_recovery_approved
            and self._startup_recovery_approved_snapshot != result.get("snapshot_hash")
        ):
            self._startup_recovery_approved = False
            self._startup_recovery_approved_snapshot = ""

        if self._startup_recovery_approved:
            self.auto_status_label.setText("전체 자동매매 상태: 운영 재개 승인")
        else:
            labels = {
                "RESUME_READY": "재개 가능",
                "REVIEW_REQUIRED": "검토 필요",
                "BLOCKED_RECOVERY": "복구 차단",
                "INVALID_RUNTIME": "Runtime 손상",
            }
            self.auto_status_label.setText(
                f"전체 자동매매 상태: {labels.get(status, status)}"
            )
        return result

    def startup_recovery_session_ready(self, *, refresh: bool = True) -> bool:
        if self._production_recovery_required():
            if refresh:
                self._production_recovery_status_result()
            context = production_recovery_registry.snapshot()
            identity = getattr(self, "_production_recovery_identity", None)
            api = getattr(self, "kiwoom_api", None)
            login_session_id = str(
                getattr(api, "login_session_id", lambda: "")() or ""
            ).strip()
            return bool(
                context is not None
                and identity is not None
                and context.identity == identity
                and recovery_account_allows_isolated_stock_operation(context)
                and identity.login_session_id == login_session_id
                and identity.account_no == self.selected_account_no()
                and identity.trading_day == datetime.now().date().isoformat()
            )
        if refresh:
            self.refresh_startup_recovery_status()
        return bool(
            self._startup_recovery_approved
            and self._startup_recovery_approved_snapshot
            and self._startup_recovery_approved_snapshot
            == self._startup_recovery_result.get("snapshot_hash")
        )

    def rebind_startup_recovery_after_trusted_runtime_update(self) -> bool:
        """Bind an approved session to a verified in-session Runtime mutation."""
        if self._startup_recovery_approved is not True:
            return False
        result = self.refresh_startup_recovery_status()
        if result.get("operator_approval_allowed") is not True:
            return False
        snapshot_hash = str(result.get("snapshot_hash") or "")
        if not snapshot_hash:
            return False
        self._startup_recovery_approved = True
        self._startup_recovery_approved_snapshot = snapshot_hash
        self.refresh_startup_recovery_status()
        return self.startup_recovery_session_ready(refresh=False)

    def startup_recovery_block_reason(self) -> str:
        result = self._startup_recovery_result
        status = str(result.get("status") or "INVALID_RUNTIME")
        for key in ("invalid_reasons", "blocked_reasons", "review_reasons"):
            reasons = result.get(key)
            if isinstance(reasons, list) and reasons:
                return f"{status}: {reasons[0]}"
        return f"{status}: 운영 재개 확인이 필요합니다."

    def _startup_recovery_detail_text(self, result: dict[str, object]) -> str:
        counts = result.get("runtime_counts")
        counts = counts if isinstance(counts, dict) else {}
        lines = [
            f"판정: {result.get('status', 'INVALID_RUNTIME')}",
            f"Queue 주문: {counts.get('orders', 0)}",
            f"Fill: {counts.get('fills', 0)}",
            f"Position: {counts.get('positions', 0)}",
            f"Broker Holdings: {counts.get('broker_holdings', 0)}",
            f"Runtime Lock: {counts.get('locks', 0)}",
            f"Reconciliation: "
            f"{result.get('operator_reconciliation', {}).get('summary', {}).get('total', 0)}",
        ]
        for title, key in (
            ("손상", "invalid_reasons"),
            ("차단", "blocked_reasons"),
            ("검토", "review_reasons"),
        ):
            reasons = result.get(key)
            if isinstance(reasons, list) and reasons:
                lines.append("")
                lines.append(f"{title}:")
                lines.extend(f"- {reason}" for reason in reasons[:12])
                if len(reasons) > 12:
                    lines.append(f"- 외 {len(reasons) - 12}개")
        return "\n".join(lines)

    def review_startup_recovery(self) -> None:
        result = self.refresh_startup_recovery_status()
        status = str(result.get("status") or "INVALID_RUNTIME")
        detail = self._startup_recovery_detail_text(result)

        if result.get("operator_approval_allowed") is not True:
            QMessageBox.warning(
                self,
                "운영 재개 차단",
                detail + "\n\nRuntime evidence를 먼저 검토·복구해야 합니다.",
            )
            if result.get("operator_reconciliation", {}).get("summary", {}).get("total", 0):
                self.open_review_required_window()
            return

        message = detail + "\n\n현재 evidence를 기준으로 자동매매 운영을 재개하시겠습니까?"
        answer = QMessageBox.question(
            self,
            "Startup Recovery",
            message,
            QMessageBox.Yes | QMessageBox.No,
        )
        append_production_event(
            "OPERATOR_SYSTEM_DECISION",
            result="ACCEPTED" if answer == QMessageBox.Yes else "REJECTED",
            source="gui_windows.MainWindow.review_startup_recovery",
            target_type="RECOVERY_SESSION",
            target_name="Startup Recovery",
            details={
                "interaction_type": "CONFIRM",
                "prompt_key": "STARTUP_RECOVERY_APPROVAL",
                "prompt_title": "Startup Recovery",
                "prompt_summary": "확인된 Runtime evidence 기준 운영 재개 승인",
                "offered_options": ["예", "아니오"],
                "selected_option": "예" if answer == QMessageBox.Yes else "아니오",
                "recovery_status": status,
            },
        )
        if answer != QMessageBox.Yes:
            self.statusBar().showMessage("운영 재개 승인이 취소되었습니다.")
            return

        self._startup_recovery_approved = True
        self._startup_recovery_approved_snapshot = str(result.get("snapshot_hash") or "")
        self.refresh_startup_recovery_status()
        window = getattr(self, "auto_trade_setting_window", None)
        refresh_actions = getattr(window, "update_action_buttons", None)
        if callable(refresh_actions):
            refresh_actions()
        else:
            refresh_controls = getattr(window, "update_startup_recovery_controls", None)
            if callable(refresh_controls):
                refresh_controls()
        self.statusBar().showMessage(f"운영 재개 승인 완료: {status}")

    def login_kiwoom_manually(self) -> None:
        api = getattr(self, "kiwoom_api", None)
        if api is None:
            reason = getattr(self, "kiwoom_api_unavailable_reason", "") or "KiwoomApi is not initialized"
            LOGGER.error("Kiwoom login unavailable: %s", reason)
            message = (
                "키움 OpenAPI를 사용할 수 없습니다. "
                "설치 상태와 32비트 실행 환경을 확인하십시오."
            )
            self.login_status_label.setText(message)
            self._apply_kiwoom_login_button_state("DISCONNECTED")
            self.statusBar().showMessage(message)
            return

        try:
            if not api.is_available():
                reason = api.unavailable_reason() or getattr(self, "kiwoom_api_unavailable_reason", "") or "kiwoom api unavailable"
                LOGGER.error("Kiwoom login unavailable: %s", reason)
                message = (
                    "키움 OpenAPI를 사용할 수 없습니다. "
                    "설치 상태와 32비트 실행 환경을 확인하십시오."
                )
                self.login_status_label.setText(message)
                self._apply_kiwoom_login_button_state("DISCONNECTED")
                self.statusBar().showMessage(message)
                return
            if api.is_connected():
                message = "로그인 상태: 연결됨"
                self.login_status_label.setText(message)
                self._apply_connected_kiwoom_login_button_state()
                self.refresh_kiwoom_accounts()
                self.sync_account_funds_selection(connected=True)
                self.request_account_funds()
                self.statusBar().showMessage(message)
                return

            self._apply_kiwoom_login_button_state("LOGIN_IN_PROGRESS")
            result = api.login()
        except Exception:
            LOGGER.exception("Kiwoom login request failed")
            message = (
                "키움 로그인 요청 중 오류가 발생했습니다. "
                "키움 OpenAPI 상태를 확인한 뒤 다시 시도하십시오."
            )
            self.login_status_label.setText(message)
            self._apply_kiwoom_login_button_state("DISCONNECTED")
            self.statusBar().showMessage(message)
            return

        status = str(result.get("status", ""))
        if status == "login_requested":
            message = "로그인 요청됨"
            self._apply_kiwoom_login_button_state("LOGIN_IN_PROGRESS")
        elif result.get("connected"):
            message = "로그인 상태: 연결됨"
            self._apply_connected_kiwoom_login_button_state()
        else:
            reason = result.get("error") or result.get("message") or status or "unknown error"
            LOGGER.error("Kiwoom login request failed: %s", reason)
            message = (
                "키움 로그인 요청을 완료하지 못했습니다. "
                "키움 OpenAPI 상태를 확인한 뒤 다시 시도하십시오."
            )
            self._apply_kiwoom_login_button_state("DISCONNECTED")

        self.login_status_label.setText(message)
        self.refresh_kiwoom_accounts()
        self.statusBar().showMessage(message)

    def on_kiwoom_login_state_changed(self, state) -> None:
        state = state if isinstance(state, dict) else {}
        connected = bool(state.get("connected", False))
        previously_connected = bool(
            getattr(self, "_event_journal_kiwoom_connected", False)
        )
        message = str(state.get("message", "") or "")
        if connected:
            label_text = "로그인 상태: 연결됨"
            status_message = message or label_text
            self._apply_connected_kiwoom_login_button_state()
        else:
            label_text = "로그인 상태: 실패"
            status_message = message or label_text
            self._apply_kiwoom_login_button_state("DISCONNECTED")

        self.login_status_label.setText(label_text)
        self.refresh_kiwoom_accounts()
        self.sync_account_funds_selection(connected=connected)
        if connected:
            self.request_account_funds()
            self.start_production_recovery()
        else:
            MainWindow._clear_completed_recovery_handoff(self)
            self._account_authentication_states.clear()
            self._account_query_states.clear()
            self._stop_production_recovery_timers()
            production_recovery_registry.invalidate("login disconnected")
            self._production_recovery_identity = None
            self._production_recovery_parts = {}
            self._production_recovery_status_result()
        if connected and not previously_connected:
            append_production_event(
                "LOGIN_SUCCEEDED",
                result="SUCCESS",
                source="gui_windows.MainWindow.on_kiwoom_login_state_changed",
                target_type="BROKER_CONNECTION",
                target_id="kiwoom_openapi",
                target_name="키움 OpenAPI",
                reason_code="ON_EVENT_CONNECT_SUCCESS",
            )
        elif not connected and previously_connected:
            append_production_event(
                "CONNECTION_LOST",
                severity="WARNING",
                result="FAILED",
                source="gui_windows.MainWindow.on_kiwoom_login_state_changed",
                target_type="BROKER_CONNECTION",
                target_id="kiwoom_openapi",
                target_name="키움 OpenAPI",
                reason_code="LOGIN_STATE_DISCONNECTED",
            )
        self._event_journal_kiwoom_connected = connected
        self.statusBar().showMessage(status_message)

    def on_kiwoom_account_changed(self, _account_no: str = "") -> None:
        MainWindow._clear_completed_recovery_handoff(self)
        previous_account = str(
            getattr(self, "_selected_kiwoom_account_no", "") or ""
        ).strip()
        self.save_current_account_memo_input()
        combo = getattr(self, "account_combo", None)
        current_data = getattr(combo, "currentData", None)
        self.load_current_account_memo_input()
        if callable(current_data):
            active = bool(current_data(ACCOUNT_ACTIVE_ROLE))
            account = str(current_data(ACCOUNT_NO_ROLE) or "").strip()
            if combo.currentIndex() >= 0 and not active:
                self.refresh_account_authentication_ui()
                self.refresh_account_query_status_ui()
                return
            if active and account:
                self._selected_kiwoom_account_no = account
                if account != previous_account:
                    account_display = masked_account_no(account)
                    append_production_event(
                        "ACCOUNT_CHANGED",
                        result="SUCCESS",
                        source="gui_windows.MainWindow.on_kiwoom_account_changed",
                        template_args={"account_display": account_display},
                        target_type="ACCOUNT",
                        target_id=account_display,
                        target_name=account_display,
                        reason_code="ACTIVE_ACCOUNT_SELECTED",
                    )
        self.sync_account_funds_selection()
        self.refresh_account_authentication_ui()
        self.refresh_account_query_status_ui()
        self.request_account_funds()
        if self._production_recovery_required():
            self.start_production_recovery()
            return
        self._stop_production_recovery_timers()
        production_recovery_registry.invalidate("account changed")
        self._production_recovery_identity = None
        self._production_recovery_parts = {}

    def _displayed_active_account_no(self) -> str:
        combo = getattr(self, "account_combo", None)
        if combo is None or not combo.isEnabled() or combo.currentIndex() < 0:
            return ""
        if not bool(combo.currentData(ACCOUNT_ACTIVE_ROLE)):
            return ""
        account = str(combo.currentData(ACCOUNT_NO_ROLE) or "").strip()
        return account if account in self.kiwoom_account_numbers() else ""

    def refresh_account_authentication_ui(self) -> None:
        account = self._displayed_active_account_no()
        state = str(self._account_authentication_states.get(account, "") or "")
        visible = bool(account)
        completed = visible and state == ACCOUNT_FUNDS_READY
        self.account_auth_separator.hide()
        self.account_auth_label.show()
        self.account_auth_neutral_label.setVisible(not visible)
        self.account_auth_done_label.setVisible(completed)
        self.btn_account_authentication.setVisible(visible and not completed)
        self.btn_account_authentication.setEnabled(visible and not completed)
        self.refresh_account_query_status_ui()

    def refresh_account_query_status_ui(self) -> None:
        account = self._displayed_active_account_no()
        visible = bool(account)
        authenticated = (
            visible
            and self._account_authentication_states.get(account)
            == ACCOUNT_FUNDS_READY
        )
        query_state = str(self._account_query_states.get(account, "") or "")
        normal = authenticated and query_state == ACCOUNT_FUNDS_READY
        retryable = authenticated and query_state == ACCOUNT_FUNDS_FAILED
        neutral = not normal and not retryable
        self.account_query_status_label.show()
        self.account_query_normal_label.setVisible(normal)
        self.account_query_neutral_label.setVisible(neutral)
        self.btn_account_requery.setVisible(retryable)
        self.btn_account_requery.setEnabled(retryable)

    def on_kiwoom_account_authentication_required(self, payload) -> None:
        evidence = payload if isinstance(payload, dict) else {}
        account = str(evidence.get("account_id") or "").strip()
        if account and account in self.kiwoom_account_numbers():
            self._account_authentication_states[account] = (
                ACCOUNT_AUTHENTICATION_REQUIRED
            )
            self._account_query_states[account] = ACCOUNT_FUNDS_FAILED
        self.refresh_account_authentication_ui()

    def open_current_account_authentication(self) -> None:
        account = self._displayed_active_account_no()
        if (
            not account
            or self._account_authentication_states.get(account)
            == ACCOUNT_FUNDS_READY
        ):
            return
        api = getattr(self, "kiwoom_api", None)
        opener = getattr(api, "show_account_password_window", None)
        if not callable(opener):
            self.statusBar().showMessage("계좌비밀번호 입력 기능을 사용할 수 없습니다.")
            return
        result = opener()
        if not isinstance(result, dict) or result.get("ok") is not True:
            self.statusBar().showMessage("계좌비밀번호 입력창을 열지 못했습니다.")
            self.refresh_account_authentication_ui()
            return
        if self._displayed_active_account_no() == account:
            self.request_account_funds()

    def requery_current_account_funds(self) -> None:
        account = self._displayed_active_account_no()
        if (
            not account
            or self._account_authentication_states.get(account)
            != ACCOUNT_FUNDS_READY
            or self._account_query_states.get(account) != ACCOUNT_FUNDS_FAILED
        ):
            self.refresh_account_query_status_ui()
            return
        self.request_account_funds(query_reason="MANUAL_REQUERY")

    def kiwoom_account_numbers(self) -> list[str]:
        api = getattr(self, "kiwoom_api", None)
        getter = getattr(api, "account_numbers", None)
        if not callable(getter):
            return []
        try:
            raw_accounts = getter()
        except Exception:
            return []

        accounts: list[str] = []
        seen: set[str] = set()
        for value in raw_accounts if isinstance(raw_accounts, list) else []:
            account = str(value or "").strip()
            if not account or account in seen:
                continue
            accounts.append(account)
            seen.add(account)
        return accounts

    def account_memos(self) -> dict[str, str]:
        try:
            settings = object.__getattribute__(self, "_account_memo_settings")
        except (AttributeError, RuntimeError):
            settings = None
        if settings is None:
            return {}
        try:
            raw_value = settings.value(ACCOUNT_MEMOS_SETTINGS_KEY, "")
            raw = json.loads(str(raw_value or "{}"))
        except Exception:
            return {}
        if not isinstance(raw, dict):
            return {}
        result: dict[str, str] = {}
        for raw_account, raw_memo in raw.items():
            account = str(raw_account or "").strip()
            memo = str(raw_memo or "").strip()[:8]
            if account and memo:
                result[account] = memo
        return result

    def set_account_memo(self, account_no: str, memo: str) -> None:
        account = str(account_no or "").strip()
        if not account:
            return
        memos = self.account_memos()
        clean_memo = str(memo or "").strip()[:8]
        if clean_memo:
            memos[account] = clean_memo
        else:
            memos.pop(account, None)
        try:
            settings = object.__getattribute__(self, "_account_memo_settings")
        except (AttributeError, RuntimeError):
            settings = None
        if settings is None:
            return
        settings.setValue(
            ACCOUNT_MEMOS_SETTINGS_KEY,
            json.dumps(memos, ensure_ascii=False, sort_keys=True),
        )
        settings.sync()
        combo = getattr(self, "account_combo", None)
        if combo is not None:
            for row in range(combo.count()):
                if str(combo.itemData(row, ACCOUNT_NO_ROLE) or "").strip() == account:
                    combo.setItemData(row, clean_memo, ACCOUNT_POPUP_MEMO_ROLE)
                    break
            combo.view().viewport().update()

    def save_current_account_memo_input(self) -> None:
        if bool(getattr(self, "_account_memo_loading", False)):
            return
        editor = getattr(self, "account_memo_edit", None)
        account = str(
            getattr(self, "_account_memo_edit_account_no", "") or ""
        ).strip()
        if editor is None or not account:
            return
        memo = str(editor.text() or "").strip()[:8]
        self.set_account_memo(account, memo)
        if editor.text() != memo:
            self._account_memo_loading = True
            try:
                editor.setText(memo)
            finally:
                self._account_memo_loading = False

    def load_current_account_memo_input(self) -> None:
        editor = getattr(self, "account_memo_edit", None)
        combo = getattr(self, "account_combo", None)
        if editor is None or combo is None:
            return
        current_data = getattr(combo, "currentData", None)
        account = (
            str(current_data(ACCOUNT_NO_ROLE) or "").strip()
            if callable(current_data) and combo.currentIndex() >= 0
            else ""
        )
        self._account_memo_loading = True
        try:
            self._account_memo_edit_account_no = account
            editor.setEnabled(bool(account))
            editor.setText(self.account_memos().get(account, "") if account else "")
        finally:
            self._account_memo_loading = False

    def remembered_account_numbers(self) -> list[str]:
        try:
            settings = object.__getattribute__(self, "_account_memo_settings")
        except (AttributeError, RuntimeError):
            settings = None
        if settings is None:
            return []
        try:
            raw = json.loads(
                str(settings.value(ACCOUNT_HISTORY_SETTINGS_KEY, "[]") or "[]")
            )
        except Exception:
            raw = []
        values = list(raw) if isinstance(raw, list) else []
        values.extend(self.account_memos().keys())
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            account = str(value or "").strip()
            if account and account not in seen:
                result.append(account)
                seen.add(account)
        return result

    def remember_account_numbers(self, accounts: list[str]) -> list[str]:
        remembered = self.remembered_account_numbers()
        merged = list(remembered)
        for value in accounts:
            account = str(value or "").strip()
            if account and account not in merged:
                merged.append(account)
        if merged == remembered:
            return merged
        try:
            settings = object.__getattribute__(self, "_account_memo_settings")
        except (AttributeError, RuntimeError):
            settings = None
        if settings is not None:
            settings.setValue(
                ACCOUNT_HISTORY_SETTINGS_KEY,
                json.dumps(merged, ensure_ascii=False),
            )
            settings.sync()
        return merged

    def _create_account_info_context_menu(self, account_no: str):
        account = str(account_no or "").strip()
        menu = QMenu(self)
        action = menu.addAction("정보삭제")
        action.setEnabled(
            bool(account) and account not in set(self.kiwoom_account_numbers())
        )
        return menu, action

    def _confirm_delete_saved_account_info(self) -> bool:
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Question)
        dialog.setWindowTitle("계좌정보 삭제")
        dialog.setText("저장된 계좌정보를 삭제하시겠습니까?")
        delete_button = dialog.addButton("삭제", QMessageBox.DestructiveRole)
        cancel_button = dialog.addButton("취소", QMessageBox.RejectRole)
        dialog.setDefaultButton(cancel_button)
        dialog.setEscapeButton(cancel_button)
        dialog.exec_()
        return dialog.clickedButton() is delete_button

    def delete_saved_account_info(self, account_no: str) -> bool:
        account = str(account_no or "").strip()
        if not account or account in set(self.kiwoom_account_numbers()):
            return False
        before_exists = account in set(self.remembered_account_numbers())
        before_memo_exists = account in self.account_memos()
        remembered = [
            value for value in self.remembered_account_numbers() if value != account
        ]
        memos = self.account_memos()
        memos.pop(account, None)
        try:
            settings = object.__getattribute__(self, "_account_memo_settings")
        except (AttributeError, RuntimeError):
            settings = None
        if settings is None:
            return False
        settings.setValue(
            ACCOUNT_HISTORY_SETTINGS_KEY,
            json.dumps(remembered, ensure_ascii=False),
        )
        settings.setValue(
            ACCOUNT_MEMOS_SETTINGS_KEY,
            json.dumps(memos, ensure_ascii=False, sort_keys=True),
        )
        settings.sync()
        if account in set(self.remembered_account_numbers()) or account in self.account_memos():
            return False
        if before_exists or before_memo_exists:
            account_display = masked_account_no(account)
            append_production_event(
                "SETTING_CHANGED",
                result="SUCCESS",
                source="SAVED_ACCOUNT_INFO_WRITER",
                template_args={"target": "저장 계좌정보"},
                target_type="ACCOUNT",
                target_id=account_display,
                target_name=account_display,
                changes=[
                    {
                        "field_key": "saved_account_info",
                        "before": True,
                        "after": False,
                    }
                ],
            )
        if str(getattr(self, "_account_memo_edit_account_no", "") or "") == account:
            self._account_memo_loading = True
            try:
                self._account_memo_edit_account_no = ""
                self.account_memo_edit.clear()
                self.account_memo_edit.setEnabled(False)
            finally:
                self._account_memo_loading = False
        self.refresh_kiwoom_accounts()
        return True

    def open_kiwoom_account_context_menu(self, position) -> None:
        combo = getattr(self, "account_combo", None)
        if combo is None:
            return
        view = combo.view()
        index = view.indexAt(position)
        if not index.isValid():
            return
        self.open_kiwoom_account_context_menu_for_index(
            index,
            view.viewport().mapToGlobal(position),
        )

    def open_kiwoom_account_context_menu_for_index(
        self,
        index,
        global_position,
    ) -> None:
        account = str(index.data(ACCOUNT_NO_ROLE) or "").strip()
        if not account:
            return
        menu, delete_action = self._create_account_info_context_menu(account)
        selected = menu.exec_(global_position)
        if selected is not delete_action or not delete_action.isEnabled():
            return
        if self._confirm_delete_saved_account_info():
            self.delete_saved_account_info(account)

    def refresh_kiwoom_accounts(self) -> list[str]:
        combo = getattr(self, "account_combo", None)
        if combo is None:
            return []

        self.save_current_account_memo_input()
        api = getattr(self, "kiwoom_api", None)
        is_connected = getattr(api, "is_connected", None)
        try:
            connected = bool(is_connected()) if callable(is_connected) else False
        except Exception:
            connected = False
        accounts = self.kiwoom_account_numbers() if connected else []
        if not accounts:
            combo.hidePopup()
            combo.blockSignals(True)
            try:
                combo.clear()
                combo.setCurrentIndex(-1)
                combo.setEnabled(False)
                self._selected_kiwoom_account_no = ""
            finally:
                combo.blockSignals(False)
            self.load_current_account_memo_input()
            self.refresh_account_authentication_ui()
            return []

        current_data = getattr(combo, "currentData", None)
        viewed_account = (
            str(current_data(ACCOUNT_NO_ROLE) or "").strip()
            if callable(current_data) and combo.currentIndex() >= 0
            else ""
        )
        current = self.selected_account_no()
        active_accounts = set(accounts)
        remembered = self.remember_account_numbers(accounts)
        memos = self.account_memos()
        inactive_accounts = [
            account for account in remembered if account not in active_accounts
        ]
        displayed_accounts = list(accounts) + inactive_accounts
        combo.blockSignals(True)
        try:
            combo.clear()
            add_item = getattr(combo, "addItem", None)
            if callable(add_item):
                for account in displayed_accounts:
                    active = account in active_accounts
                    add_item(account_combo_display_text(account), account)
                    row = combo.count() - 1
                    combo.setItemData(
                        row,
                        memos.get(account, ""),
                        ACCOUNT_POPUP_MEMO_ROLE,
                    )
                    combo.setItemData(row, active, ACCOUNT_ACTIVE_ROLE)
                    if not active:
                        combo.setItemData(
                            row,
                            QBrush(QColor("#9CA3AF")),
                            Qt.ForegroundRole,
                        )
                    item_reader = getattr(combo.model(), "item", None)
                    item = item_reader(row) if callable(item_reader) else None
                    if item is not None:
                        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            else:
                combo.addItems(accounts)
            combo.setEnabled(bool(displayed_accounts))
            selected_account = ""
            if len(accounts) == 1:
                selected_account = accounts[0]
            elif current and current in accounts:
                selected_account = current
            display_account = (
                viewed_account
                if viewed_account in displayed_accounts
                else selected_account
            )
            if not display_account and not accounts and displayed_accounts:
                display_account = displayed_accounts[0]
            combo.setCurrentIndex(
                displayed_accounts.index(display_account)
                if display_account in displayed_accounts
                else -1
            )
            try:
                self._selected_kiwoom_account_no = selected_account
            except RuntimeError:
                pass
        finally:
            combo.blockSignals(False)
        self.load_current_account_memo_input()
        self.refresh_account_authentication_ui()
        return accounts

    def selected_account_no(self) -> str:
        combo = getattr(self, "account_combo", None)
        if combo is None or not combo.isEnabled():
            return ""
        accounts = self.kiwoom_account_numbers()
        current_data = getattr(combo, "currentData", None)
        if callable(current_data):
            active = bool(current_data(ACCOUNT_ACTIVE_ROLE))
            account = str(current_data(ACCOUNT_NO_ROLE) or "").strip()
            if active and account in accounts:
                self._selected_kiwoom_account_no = account
                return account
            try:
                selected = str(
                    object.__getattribute__(self, "_selected_kiwoom_account_no")
                    or ""
                ).strip()
            except (AttributeError, RuntimeError):
                selected = ""
            return selected if selected in accounts else ""
        account = str(combo.currentText() or "").strip()
        return account if account in accounts else ""

    def sync_account_funds_selection(self, *, connected: bool | None = None):
        """Project the selected login account into the memory-only funds view."""
        if connected is None:
            api = getattr(self, "kiwoom_api", None)
            checker = getattr(api, "is_connected", None)
            try:
                connected = callable(checker) and checker() is True
            except Exception:
                connected = False
        account_id = self.selected_account_no() if connected else ""
        snapshot = self._account_funds_projection.select_account(
            account_id,
            connected=bool(connected),
        )
        adapter = getattr(self, "account_funds_adapter", None)
        selector = getattr(adapter, "set_active_account", None)
        if callable(selector):
            selector(account_id)
        self.render_account_funds_snapshot(snapshot)
        return snapshot

    def _append_account_query_journal_event(
        self,
        event_type: str,
        *,
        account_id: str,
        request_id: int,
        query_reason: str,
        result: str,
        payload: object = None,
    ) -> dict[str, object]:
        evidence = payload if isinstance(payload, dict) else {}
        account_display = masked_account_no(account_id)
        error_code = evidence.get("error_code", evidence.get("result"))
        reason = str(evidence.get("error") or "").strip()
        error_kind = str(evidence.get("error_kind") or "").strip()
        if error_code in (None, "") and reason:
            code_match = re.search(r"\((\d+)\)\s*$", reason)
            if code_match:
                error_code = code_match.group(1)
        details = {
            "query_scope": "ACCOUNT_FUNDS",
            "query_reason": str(query_reason or "INITIAL_QUERY"),
            "request_id": int(request_id),
            "retryable": error_kind != ACCOUNT_AUTHENTICATION_REQUIRED,
        }
        if error_code not in (None, ""):
            details["error_code"] = str(error_code)
        if error_kind:
            details["error_kind"] = error_kind
        if reason:
            details["reason"] = reason[:500]
        return append_production_event(
            event_type,
            severity="WARNING" if result == "FAILED" else "INFO",
            result=result,
            source="MainWindow.request_account_funds",
            template_args={"account_display": account_display},
            target_type="ACCOUNT",
            target_id=account_display,
            target_name=account_display,
            reason_code=error_kind or (str(error_code) if error_code not in (None, "") else ""),
            details=details,
        )

    def request_account_funds(
        self,
        *,
        query_reason: str = "INITIAL_QUERY",
    ) -> dict[str, object]:
        """Run an injected adapter; the Production Kiwoom adapter is intentionally absent."""
        adapter = getattr(self, "account_funds_adapter", None)
        requester = getattr(adapter, "request_account_funds", None)
        if not callable(requester):
            return {"ok": False, "status": "ADAPTER_UNAVAILABLE"}

        request = self._account_funds_projection.begin_request()
        if request is None:
            self.render_account_funds_snapshot(self._account_funds_projection.snapshot)
            return {"ok": False, "status": "ACCOUNT_UNAVAILABLE"}
        manual_requery = str(query_reason or "").strip() == "MANUAL_REQUERY"
        requested_event = (
            "ACCOUNT_REQUERY_REQUESTED"
            if manual_requery
            else "ACCOUNT_QUERY_REQUESTED"
        )
        try:
            query_states = object.__getattribute__(self, "_account_query_states")
        except (AttributeError, RuntimeError):
            query_states = {}
            try:
                object.__setattr__(self, "_account_query_states", query_states)
            except RuntimeError:
                pass
        query_states[request.account_id] = ACCOUNT_FUNDS_LOADING
        self._append_account_query_journal_event(
            requested_event,
            account_id=request.account_id,
            request_id=request.request_id,
            query_reason=query_reason,
            result="REQUESTED",
        )
        self.render_account_funds_snapshot(self._account_funds_projection.snapshot)

        result_recorded = False

        def on_result(payload) -> None:
            nonlocal result_recorded
            if self._account_funds_projection.apply_result(request, payload):
                self.render_account_funds_snapshot(self._account_funds_projection.snapshot)
                evidence = payload if isinstance(payload, dict) else {}
                succeeded = evidence.get("ok") is True
                error_kind = str(evidence.get("error_kind") or "").strip()
                if succeeded:
                    event_type = (
                        "ACCOUNT_REQUERY_SUCCEEDED"
                        if manual_requery
                        else "ACCOUNT_QUERY_SUCCEEDED"
                    )
                    journal_result = "SUCCESS"
                elif manual_requery:
                    event_type = "ACCOUNT_REQUERY_FAILED"
                    journal_result = "FAILED"
                elif error_kind == ACCOUNT_AUTHENTICATION_REQUIRED:
                    event_type = "ACCOUNT_AUTH_REQUIRED"
                    journal_result = "FAILED"
                else:
                    event_type = "ACCOUNT_QUERY_FAILED"
                    journal_result = "FAILED"
                self._append_account_query_journal_event(
                    event_type,
                    account_id=request.account_id,
                    request_id=request.request_id,
                    query_reason=query_reason,
                    result=journal_result,
                    payload=evidence,
                )
                result_recorded = True
                if succeeded:
                    self._restart_failed_production_recovery_after_account_funds_success(
                        request.account_id
                    )

        try:
            adapter_result = requester(
                request.account_id,
                request_id=request.request_id,
                callback=on_result,
            )
        except Exception as exc:
            if self._account_funds_projection.fail_request(request, exc):
                self.render_account_funds_snapshot(self._account_funds_projection.snapshot)
                self._append_account_query_journal_event(
                    "ACCOUNT_REQUERY_FAILED" if manual_requery else "ACCOUNT_QUERY_FAILED",
                    account_id=request.account_id,
                    request_id=request.request_id,
                    query_reason=query_reason,
                    result="FAILED",
                    payload={"error": str(exc)},
                )
            return {"ok": False, "status": ACCOUNT_FUNDS_FAILED}
        if isinstance(adapter_result, dict) and adapter_result.get("ok") is False:
            if not result_recorded:
                on_result(adapter_result)
            self.render_account_funds_snapshot(self._account_funds_projection.snapshot)
            return {"ok": False, "status": ACCOUNT_FUNDS_FAILED}
        return {
            "ok": True,
            "status": ACCOUNT_FUNDS_LOADING,
            "account_id": request.account_id,
            "request_id": request.request_id,
        }

    def render_account_funds_snapshot(self, snapshot=None) -> None:
        """Bind one memory snapshot to the existing account/funds labels."""
        snapshot = snapshot or self._account_funds_projection.snapshot
        self.account_label.setText("계좌정보 :")
        account_id = str(getattr(snapshot, "account_id", "") or "").strip()
        try:
            authentication_states = object.__getattribute__(
                self,
                "_account_authentication_states",
            )
        except (AttributeError, RuntimeError):
            authentication_states = None
        if isinstance(authentication_states, dict):
            if snapshot.status == ACCOUNT_FUNDS_READY and account_id:
                authentication_states[account_id] = ACCOUNT_FUNDS_READY
            elif (
                snapshot.status == ACCOUNT_FUNDS_FAILED
                and str(getattr(snapshot, "error_kind", "") or "")
                == ACCOUNT_AUTHENTICATION_REQUIRED
                and account_id
            ):
                authentication_states[account_id] = (
                    ACCOUNT_AUTHENTICATION_REQUIRED
                )
        try:
            query_states = object.__getattribute__(self, "_account_query_states")
        except (AttributeError, RuntimeError):
            query_states = None
        if (
            isinstance(query_states, dict)
            and account_id
            and snapshot.status
            in (ACCOUNT_FUNDS_LOADING, ACCOUNT_FUNDS_READY, ACCOUNT_FUNDS_FAILED)
        ):
            query_states[account_id] = snapshot.status

        if snapshot.status == ACCOUNT_FUNDS_DISCONNECTED:
            account_type = "-"
            deposit = "미연결"
            orderable = "미연결"
            buy_status = "미연결"
        elif snapshot.status == ACCOUNT_FUNDS_LOADING:
            account_type = "-"
            deposit = "조회 중"
            orderable = "조회 중"
            buy_status = "조회 중"
        elif snapshot.status == ACCOUNT_FUNDS_FAILED:
            account_type = "확인 필요"
            deposit = "조회 실패"
            orderable = "조회 실패"
            buy_status = "확인 필요"
        elif snapshot.status == ACCOUNT_FUNDS_READY:
            account_type = snapshot.account_type or "확인 필요"
            deposit = format_account_funds_money(snapshot.deposit).removesuffix("원")
            orderable = format_account_funds_money(snapshot.orderable_cash).removesuffix("원")
            buy_status = "확인 필요"
        else:
            account_type = "-"
            deposit = "-"
            orderable = "-"
            buy_status = "확인 전"

        self.account_type_label.setText(f"계좌 구분: {account_type}")
        set_metric_value_text(self.account_total_deposit_label, deposit)
        set_metric_value_text(self.account_order_available_label, orderable)
        self.buy_time_status_label.setText(f"매수 가능 상태: {buy_status}")
        if isinstance(authentication_states, dict):
            self.refresh_account_authentication_ui()
        try:
            budget_total_label = object.__getattribute__(self, "budget_total_label")
        except (AttributeError, RuntimeError):
            budget_total_label = None
        if budget_total_label is not None:
            self.refresh_main_budget_orderable_validation()
            update_main_budget_panel(self)
        if snapshot.status == ACCOUNT_FUNDS_READY:
            recovery = production_recovery_registry.snapshot()
            identity = getattr(recovery, "identity", None)
            if (
                recovery is not None
                and recovery.account_status == ACCOUNT_COMPLETED
                and identity is not None
                and identity.account_no == account_id
            ):
                self._resume_limit_responses_after_recovery(identity)

    def refresh_all(self) -> None:
        self.load_routine_table()
        self.load_running_stock_table()
        self.update_budget_panel()
        self.update_emergency_button_state()
        self.update_review_required_button_text()
        self.update_global_operation_button_state()

    def recalculate_routine_limits_for_new_operation_session(self) -> dict[str, object]:
        from routine_limit_recalculation import (
            recalculate_enabled_routine_limits_for_new_session,
        )

        return recalculate_enabled_routine_limits_for_new_session(self)

    def update_global_operation_button_state(self) -> None:
        adapter = MainMonitoringStockOperationAdapter(self, [])
        adapter.update_global_operation_button_state()
        window = getattr(self, "auto_trade_setting_window", None)
        if window is None or sip.isdeleted(window):
            return
        update = getattr(window, "update_global_operation_button_state", None)
        if callable(update):
            update()

    def start_global_auto_trades(self) -> None:
        adapter = MainMonitoringStockOperationAdapter(self, [])
        AutoTradeSettingWindow.start_selected_auto_trades(adapter)

    def refresh_auto_trade_assignment_views(self) -> None:
        """Refresh monitoring and an already-open auto-trade settings window once."""
        self.refresh_all()
        window = getattr(self, "auto_trade_setting_window", None)
        if window is None:
            return
        if sip.isdeleted(window):
            self.auto_trade_setting_window = None
            return
        window.refresh_all()

    def update_budget_panel(self) -> None:
        self.refresh_main_budget_orderable_validation()
        update_main_budget_panel(self)

    def _kiwoom_connected_for_budget(self) -> bool:
        api = getattr(self, "kiwoom_api", None)
        is_connected = getattr(api, "is_connected", None)
        try:
            return bool(is_connected()) if callable(is_connected) else False
        except Exception:
            return False

    def current_orderable_cash_for_budget(self) -> int | None:
        """Return one READY snapshot for the currently selected active account."""
        if not self._kiwoom_connected_for_budget():
            return None
        account_no = str(self.selected_account_no() or "").strip()
        if not account_no:
            return None
        snapshot = self._account_funds_projection.snapshot
        if (
            snapshot.status != ACCOUNT_FUNDS_READY
            or str(snapshot.account_id or "").strip() != account_no
            or snapshot.orderable_cash is None
        ):
            return None
        try:
            amount = int(snapshot.orderable_cash)
        except (TypeError, ValueError):
            return None
        return amount if amount >= 0 else None

    def refresh_main_budget_orderable_validation(self) -> bool | None:
        """Project account-fit without mutating the saved total-budget amount."""
        if not self._kiwoom_connected_for_budget():
            self._main_budget_orderable_valid = None
            self.budget_total_label.setToolTip("더블클릭하여 전체예산 설정")
            return None
        orderable = self.current_orderable_cash_for_budget()
        if orderable is None:
            self._main_budget_orderable_valid = False
            self.budget_total_label.setToolTip("주문 가능금액 확인 후 사용 가능")
            return False
        total_budget = int(collect_main_budget_summary().get("total_budget", 0))
        valid = total_budget <= orderable
        self._main_budget_orderable_valid = valid
        self.budget_total_label.setToolTip(
            "더블클릭하여 전체예산 설정"
            if valid
            else "전체예산이 주문 가능금액을 초과합니다."
        )
        return valid

    def show_main_total_budget_popup(self) -> None:
        popup = getattr(self, "_main_total_budget_popup", None)
        if popup is not None:
            popup.show_below(self.budget_total_label)

    def _show_main_budget_setting_notice(self, message: str) -> None:
        show_toast(
            parent=self,
            message=message,
            duration_ms=1800,
            position="center",
        )

    def _save_main_total_budget(
        self,
        value: object,
        *,
        orderable_cash: int | None,
    ) -> bool:
        try:
            persist_main_total_budget(value, orderable_cash=orderable_cash)
        except (TypeError, ValueError):
            self._show_main_budget_setting_notice("전체예산을 확인하세요.")
            return False
        self.update_budget_panel()
        return True

    def main_total_budget_rounding_enabled(self) -> bool:
        settings = self._account_memo_settings
        raw_value = settings.value(TOTAL_BUDGET_ROUNDING_SETTINGS_KEY, True)
        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, (int, float)):
            return bool(raw_value)
        return str(raw_value).strip().lower() not in {"0", "false", "no", "off"}

    def set_main_total_budget_rounding_enabled(self, enabled: bool) -> None:
        self._account_memo_settings.setValue(
            TOTAL_BUDGET_ROUNDING_SETTINGS_KEY,
            bool(enabled),
        )

    def main_budget_warning_enabled(self) -> bool:
        raw_value = self._account_memo_settings.value(
            BUDGET_WARNING_SETTINGS_KEY,
            True,
        )
        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, (int, float)):
            return bool(raw_value)
        return str(raw_value).strip().lower() not in {"0", "false", "no", "off"}

    def set_main_budget_warning_enabled(self, enabled: bool) -> None:
        clean_enabled = bool(enabled)
        self._account_memo_settings.setValue(
            BUDGET_WARNING_SETTINGS_KEY,
            clean_enabled,
        )
        MainWindow._apply_main_budget_warning_badge_style(self, clean_enabled)

    def _apply_main_budget_warning_badge_style(self, enabled: bool) -> None:
        button = getattr(self, "budget_warning_toggle_button", None)
        if button is not None:
            button.setText("경고 ON" if enabled else "경고 OFF")
            badge_content_height = max(
                AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT - 2,
                QFontMetrics(button.font()).height(),
            )
            text_color = (
                AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR
                if enabled
                else MAIN_ROUTINE_BADGE_IDLE_TEXT_COLOR
            )
            border_color = (
                AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR
                if enabled
                else AUTO_TRADE_SETTING_BADGE_INACTIVE_COLOR
            )
            button.setStyleSheet(
                auto_trade_setting_badge_stylesheet(
                    "QPushButton#mainBudgetWarningToggle",
                    text_color=text_color,
                    border_color=border_color,
                )
                + "QPushButton#mainBudgetWarningToggle {"
                f" min-height: {badge_content_height}px;"
                f" max-height: {badge_content_height}px;"
                " }"
                + "QPushButton#mainBudgetWarningToggle:focus { outline: none; }"
            )

    def on_main_budget_buffer_response_entry_clicked(self) -> None:
        """Open one editor whose baseline always comes from persisted policy."""
        surface = getattr(self, "_main_buffer_response_settings_surface", None)
        if surface is None or sip.isdeleted(surface):
            surface = _BufferResponseSettingsSurface(self)
            self._main_buffer_response_settings_surface = surface
        elif not surface.isVisible():
            surface.reload_from_persisted()
        surface.show()
        surface.raise_()
        surface.activateWindow()

    def _apply_main_budget_buffer_response_badge_style(
        self,
        active: bool,
        *,
        content_height: int | None = None,
    ) -> None:
        button = getattr(self, "budget_buffer_response_button", None)
        if button is None:
            return
        if content_height is None:
            content_height = max(
                AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT - 2,
                QFontMetrics(button.font()).height(),
            )
        color = "#ea580c" if active else MAIN_ROUTINE_BADGE_IDLE_TEXT_COLOR
        border_color = (
            "#ea580c" if active else AUTO_TRADE_SETTING_BADGE_INACTIVE_COLOR
        )
        button.setStyleSheet(
            auto_trade_setting_badge_stylesheet(
                "QPushButton#mainBudgetBufferResponseEntry",
                text_color=color,
                border_color=border_color,
            )
            + "QPushButton#mainBudgetBufferResponseEntry {"
            f" min-height: {content_height}px;"
            f" max-height: {content_height}px;"
            " }"
            + "QPushButton#mainBudgetBufferResponseEntry:focus { outline: none; }"
        )

    def handle_main_budget_warning_projection(
        self,
        *,
        activity: dict[str, object],
        buffer_enabled: bool,
    ) -> None:
        transition = project_main_budget_warning_transition(
            previous_available_remaining_ratio=getattr(
                self,
                "_main_budget_warning_previous_available_ratio",
                None,
            ),
            previous_buffer_entered=getattr(
                self,
                "_main_budget_warning_previous_buffer_entered",
                None,
            ),
            activity=activity,
            buffer_enabled=buffer_enabled,
        )
        self._main_budget_warning_previous_available_ratio = transition.get(
            "available_remaining_ratio"
        )
        self._main_budget_warning_previous_buffer_entered = transition.get(
            "buffer_entered"
        )

        if not self.main_budget_warning_enabled():
            return
        crossed_threshold = transition.get("available_threshold_crossed")
        if crossed_threshold is not None:
            show_toast(
                parent=self,
                message=f"가용금액이 {int(crossed_threshold)}% 남았습니다.",
                duration_ms=2200,
                position="bottom_right",
            )
        if transition.get("buffer_entry_started") is True:
            show_toast(
                parent=self,
                message=(
                    "경고 완충금액에 진입했습니다.\n"
                    "종목 강제마감 주의하세요"
                ),
                duration_ms=2600,
                position="bottom_right",
            )

    def apply_main_total_budget_percentage(self, percent: int) -> bool:
        orderable = self.current_orderable_cash_for_budget()
        if orderable is None:
            self._show_main_budget_setting_notice(
                "주문 가능금액 확인 후 사용 가능합니다."
            )
            return False
        try:
            total_budget = total_budget_from_orderable_cash(
                orderable,
                percent,
                align_digits=self.main_total_budget_rounding_enabled(),
            )
        except (TypeError, ValueError):
            self._show_main_budget_setting_notice("전체예산을 확인하세요.")
            return False
        return self._save_main_total_budget(
            total_budget,
            orderable_cash=orderable,
        )

    def apply_main_total_budget_direct(self, value: object) -> bool:
        orderable = None
        if self._kiwoom_connected_for_budget():
            orderable = self.current_orderable_cash_for_budget()
            if orderable is None:
                self._show_main_budget_setting_notice(
                    "주문 가능금액 확인 후 사용 가능합니다."
                )
                return False
        return self._save_main_total_budget(value, orderable_cash=orderable)

    def _finish_main_budget_percent_editing(self) -> None:
        for editor in (
            self.budget_available_percent_edit,
            self.budget_buffer_percent_edit,
        ):
            editor.finish_display()

    def _commit_main_budget_percent(self, source: str) -> None:
        summary = collect_main_budget_summary()
        current_available = int(summary.get("available_budget_percent", 100))
        current_buffer = int(summary.get("buffer_budget_percent", 0))
        editor = (
            self.budget_available_percent_edit
            if source == "available"
            else self.budget_buffer_percent_edit
        )
        raw_value = editor.text().strip()

        if source == "available" and (
            raw_value == str(current_available)
            or (current_buffer == 0 and raw_value == "-")
        ):
            self.update_budget_panel()
            self._finish_main_budget_percent_editing()
            return
        if source == "buffer" and (
            raw_value == str(current_buffer)
            or (current_buffer == 0 and raw_value == "-")
        ):
            self.update_budget_panel()
            self._finish_main_budget_percent_editing()
            return

        try:
            saved_summary = persist_main_budget_percent(source, raw_value)
        except Exception:
            self.update_budget_panel()
            self._finish_main_budget_percent_editing()
            show_toast(
                parent=self,
                message=(
                    "가용 비율을 확인하세요."
                    if source == "available"
                    else "완충 비율을 확인하세요."
                ),
                duration_ms=1800,
                position="center",
            )
            return
        self.update_budget_panel()
        self._finish_main_budget_percent_editing()
        if (
            source == "buffer"
            and raw_value.isdigit()
            and int(raw_value) == 0
            and int(saved_summary.get("buffer_budget_percent", -1)) == 0
        ):
            show_toast(
                parent=self,
                message=(
                    "※완충 0%설정은 심각한 손실을 초래할수 있습니다. "
                    "권장 완충은 20%입니다."
                ),
                duration_ms=3000,
                position="center",
            )

    def review_required_stock_count(self) -> int:
        """검토관리창과 동일 Collector 기준으로 대상 종목 수를 계산한다."""
        return len(collect_global_review_required_rows())

    def update_review_required_button_text(self) -> None:
        if not hasattr(self, "btn_review_required"):
            return
        count = self.review_required_stock_count()
        self.btn_review_required.setText(f"검토관리({count})")

    def sort_main_routine_table_by_column(self, column: int) -> None:
        main_sort_routine_table_by_column(self, column)

    def sort_main_running_table_by_column(self, column: int) -> None:
        main_sort_running_table_by_column(self, column)

    def _apply_main_routine_sort(self) -> None:
        main_apply_routine_sort(self)

    def _apply_main_running_sort(self) -> None:
        main_apply_running_sort(self)

    def load_routine_table(self) -> None:
        main_load_routine_table(self)
        self._install_routine_buy_limit_edit_filters()

    def load_running_stock_table(self) -> None:
        main_load_running_stock_table(self)

    def all_runtime_stock_dirs(self) -> list[Path]:
        """전체 루틴의 종목 runtime 폴더를 중복 없이 조회한다."""
        stock_dirs: list[Path] = []
        seen: set[str] = set()
        for routine_dir in get_routine_dirs():
            for stock_dir in get_stock_dirs_in_routine(routine_dir):
                key = str(stock_dir.resolve())
                if key in seen:
                    continue
                seen.add(key)
                stock_dirs.append(stock_dir)
        return stock_dirs

    def routine_name_for_stock_dir(self, stock_dir: Path) -> str:
        """종목 runtime 폴더 기준 루틴 표시명을 반환한다."""
        try:
            return routine_display_name(stock_dir.parent)
        except Exception:
            return str(stock_dir.parent.name).lstrip("_") or "루틴확인필요"

    def has_emergency_stopped_stock(self) -> bool:
        return emergency_has_emergency_stopped_stock(self)

    def update_emergency_button_state(self) -> None:
        emergency_update_emergency_button_state(self)

    def emergency_review_reason_for_stock(self, stock_dir: Path) -> tuple[bool, str]:
        return emergency_review_reason_for_stock_impl(stock_dir)


    def update_runtime_stock_status(
        self,
        stock_dir: Path,
        code: str,
        name: str,
        new_status: str,
        extra_state: dict[str, object] | None = None,
        log_suffix: str = "",
    ) -> bool:
        return emergency_update_runtime_stock_status(
            self,
            stock_dir,
            code,
            name,
            new_status,
            extra_state,
            log_suffix,
        )

    def execute_emergency_stop(self) -> None:
        emergency_execute_emergency_stop(self)

    def release_emergency_stop(self) -> None:
        emergency_release_emergency_stop(self)

    def on_emergency_stop_clicked(self) -> None:
        emergency_on_emergency_stop_clicked(self)

    def open_routine_settings_from_main_table(self, item=None) -> None:
        """Open a definition template or persisted instance settings dialog."""
        row = item.row() if item is not None else self.routine_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "루틴 설정", "설정을 열 루틴을 선택하세요.")
            return

        routine_item = self.routine_table.item(row, 0)
        if routine_item is None:
            QMessageBox.warning(self, "루틴 설정", "선택한 행에서 루틴명을 확인하지 못했습니다.")
            return

        row_kind = str(routine_item.data(ROUTINE_ROW_KIND_ROLE) or "")
        definition_id = str(routine_item.data(ROUTINE_DEFINITION_ID_ROLE) or "").strip()
        instance_id = str(routine_item.data(ROUTINE_INSTANCE_ID_ROLE) or "").strip()
        definition = routine_definition_by_id(definition_id) if definition_id else None
        instance = routine_instance_by_id(instance_id) if row_kind == ROUTINE_ROW_CHILD else None
        if definition is None:
            QMessageBox.warning(self, "루틴 설정", "선택한 루틴 유형을 확인할 수 없습니다.")
            return
        if row_kind == ROUTINE_ROW_CHILD and instance is None:
            QMessageBox.warning(self, "루틴 설정", "선택한 등록 루틴을 확인할 수 없습니다.")
            return

        routine_record = routine_record_by_name(definition.source_name)
        if routine_record is None:
            QMessageBox.warning(
                self,
                "루틴 설정",
                f"선택한 루틴을 Registry에서 찾지 못했습니다.\n루틴명: {definition.display_name}",
            )
            return

        settings_ui = str(routine_record.settings_ui or "").strip().lower()
        if settings_ui != "indicator_follow":
            QMessageBox.information(
                self,
                "\ub8e8\ud2f4 \uc124\uc815",
                f"\uc120\ud0dd\ud55c \ub8e8\ud2f4\uc758 \uc124\uc815\ucc3d\uc774 \uc544\uc9c1 \uc5f0\uacb0\ub418\uc9c0 \uc54a\uc558\uc2b5\ub2c8\ub2e4.\\n\ub8e8\ud2f4\uba85: {routine_record.name}",
            )
            return

        rules_path = instance.rules_path if instance is not None else routine_record.rules_path
        if not rules_path.exists():
            QMessageBox.warning(
                self,
                "rules.json \uc5c6\uc74c",
                f"\uc120\ud0dd\ud55c \ub8e8\ud2f4\uc758 rules.json\uc744 \ucc3e\uc744 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.\\n{rules_path}",
            )
            return

        try:
            from gui_indicator_follow_routine_settings_dialog import IndicatorFollowRoutineSettingsDialog
        except Exception as exc:
            QMessageBox.critical(
                self,
                "\uc124\uc815\ucc3d \ub85c\ub4dc \uc2e4\ud328",
                "gui_indicator_follow_routine_settings_dialog.py \ud30c\uc77c\uc744 \ubd88\ub7ec\uc624\uc9c0 \ubabb\ud588\uc2b5\ub2c8\ub2e4.\\n"
                f"{exc}",
            )
            return

        dialog = IndicatorFollowRoutineSettingsDialog(
            rules_path=rules_path,
            routine_path=routine_record.path,
            routine_name=instance.display_name if instance is not None else routine_record.name,
            parent=self,
            definition_id=definition.definition_id,
            definition_display_name=definition.display_name,
            instance_id=instance.instance_id if instance is not None else "",
            settings_mode="edit" if instance is not None else "registration",
        )
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)
        windows = getattr(self, "_routine_settings_windows", None)
        if not isinstance(windows, set):
            windows = set()
            self._routine_settings_windows = windows
        windows.add(dialog)
        dialog.destroyed.connect(
            lambda _obj=None, target=dialog: windows.discard(target)
        )
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def toggle_routine_expansion(self, row: int) -> None:
        item = self.routine_table.item(row, 0)
        if item is None or str(item.data(ROUTINE_ROW_KIND_ROLE) or "") != ROUTINE_ROW_PARENT:
            return
        definition_id = str(item.data(ROUTINE_DEFINITION_ID_ROLE) or "").strip()
        if not definition_id:
            return
        if definition_id in self._collapsed_routine_definition_ids:
            self._collapsed_routine_definition_ids.discard(definition_id)
        else:
            self._collapsed_routine_definition_ids.add(definition_id)
        self.load_routine_table()

    def toggle_routine_instance_expansion(self, row: int) -> None:
        item = self.routine_table.item(row, 0)
        if item is None or str(item.data(ROUTINE_ROW_KIND_ROLE) or "") != ROUTINE_ROW_CHILD:
            return
        instance_id = str(item.data(ROUTINE_INSTANCE_ID_ROLE) or "").strip()
        if not instance_id:
            return
        if instance_id in self._collapsed_routine_instance_ids:
            self._collapsed_routine_instance_ids.discard(instance_id)
        else:
            self._collapsed_routine_instance_ids.add(instance_id)
        self.load_routine_table()

    def start_routine_instance_name_edit(self, row: int) -> None:
        item = self.routine_table.item(row, 0)
        if item is None:
            return
        if str(item.data(ROUTINE_ROW_KIND_ROLE) or "") != ROUTINE_ROW_CHILD:
            return
        instance_id = str(item.data(ROUTINE_INSTANCE_ID_ROLE) or "").strip()
        if not instance_id:
            return

        self.finish_routine_instance_name_edit(save=True)
        index = self.routine_table.model().index(row, 0)
        name_rect = self._routine_tree_interaction_controller._child_name_rect(index)
        cell_rect = self.routine_table.visualRect(index)
        max_width = max(80, cell_rect.right() - name_rect.left() - 4)
        editor_width = min(max_width, max(name_rect.width() + 24, 96))
        editor_rect = QRect(
            name_rect.left(),
            cell_rect.top() + 2,
            editor_width,
            max(20, cell_rect.height() - 4),
        )

        editor = _RoutineInstanceNameEdit(self)
        editor.setObjectName("routineInstanceNameEditor")
        _apply_routine_inline_edit_style(editor, self.routine_table)
        editor.setText(item.text())
        editor.setGeometry(editor_rect)
        editor.selectAll()
        editor.show()
        editor.setFocus(Qt.MouseFocusReason)

        self._routine_instance_name_editor = editor
        self._routine_instance_name_editor_instance_id = instance_id
        self._routine_instance_name_editor_original = item.text()
        self._routine_instance_name_editor_item = item
        item.setText("")

    def finish_routine_instance_name_edit(self, *, save: bool) -> None:
        editor = self._routine_instance_name_editor
        if editor is None or self._routine_instance_name_edit_finishing:
            return
        self._routine_instance_name_edit_finishing = True
        instance_id = self._routine_instance_name_editor_instance_id
        original_name = self._routine_instance_name_editor_original
        original_item = self._routine_instance_name_editor_item
        new_name = editor.text().strip()

        self._routine_instance_name_editor = None
        self._routine_instance_name_editor_instance_id = ""
        self._routine_instance_name_editor_original = ""
        self._routine_instance_name_editor_item = None
        editor.hide()
        editor.deleteLater()
        self._routine_instance_name_edit_finishing = False

        if not save or not new_name or new_name == original_name:
            if original_item is not None:
                original_item.setText(original_name)
            return

        result = RoutineInstanceRepository(PROJECT_ROOT).rename_instance(
            instance_id,
            new_name,
        )
        if not result.success:
            if original_item is not None:
                original_item.setText(original_name)
            QMessageBox.warning(
                self,
                "루틴 이름 변경",
                result.error or "등록 루틴 이름을 변경하지 못했습니다.",
            )
            return

        self.refresh_all()

    def _install_routine_buy_limit_edit_filters(self) -> None:
        for row in range(self.routine_table.rowCount()):
            status_widget = self.routine_table.cellWidget(row, 1)
            if status_widget is None:
                continue
            for object_name in (
                "routineInstanceBuyLimitAmount",
                "routineInstanceBuyLimitEditor",
                "routineInstanceBuyLimitSettings",
            ):
                child = status_widget.findChild(QWidget, object_name)
                if child is not None:
                    child.installEventFilter(self._routine_buy_limit_edit_filter)

    def _routine_row_for_child_widget(self, widget: QWidget) -> int:
        position = widget.mapTo(
            self.routine_table.viewport(),
            widget.rect().center(),
        )
        index = self.routine_table.indexAt(position)
        return index.row() if index.isValid() else -1

    @staticmethod
    def _parse_buy_limit_amount(text: str) -> int | None:
        normalized = str(text or "").replace(",", "").strip()
        if not normalized or not normalized.isdigit():
            return None
        try:
            amount = int(normalized)
        except ValueError:
            return None
        return amount if amount > 0 else None

    @staticmethod
    def _write_stock_buy_limit_config(
        config_path: Path,
        *,
        enabled: bool,
        amount: int | None = None,
    ) -> None:
        config = read_json_dict(config_path)
        if not isinstance(config, dict):
            config = {}
        config["buy_limit_enabled"] = bool(enabled)
        config["buy_limit_amount"] = int(amount) if enabled and amount is not None else None
        config["updated_at"] = stock_now_text()
        config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _write_stock_initial_buy_config(
        config_path: Path,
        *,
        mode: str,
        value: int,
    ) -> None:
        config = read_json_dict(config_path)
        if not isinstance(config, dict):
            config = {}
        normalized_mode = "AMOUNT" if str(mode).upper() == "AMOUNT" else "QUANTITY"
        config["trade_amount_type"] = normalized_mode
        if normalized_mode == "AMOUNT":
            config["buy_amount"] = max(0, int(value))
        else:
            config["buy_qty"] = max(1, int(value))
        config["updated_at"] = stock_now_text()
        config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _stock_config_path_for_routine_row(self, row: int) -> Path | None:
        item = self.routine_table.item(row, 0)
        if item is None or str(item.data(ROUTINE_ROW_KIND_ROLE) or "") != ROUTINE_ROW_STOCK:
            return None
        stock_path = str(item.data(ROUTINE_STOCK_PATH_ROLE) or "").strip()
        if not stock_path:
            return None
        return PROJECT_ROOT / stock_path / "config.json"

    @staticmethod
    def _stock_current_price_for_config(config_path: Path) -> float | None:
        state = read_json_dict(config_path.parent / "state.json")
        return current_price_from_state(state if isinstance(state, dict) else {})

    @staticmethod
    def _stock_default_initial_buy_value(config_path: Path, mode: str) -> int:
        defaults = starting_budget_defaults()
        if mode == "QUANTITY":
            return int(defaults["quantity"])
        amount = effective_amount_starting_budget(
            MainWindow._stock_current_price_for_config(config_path),
            defaults["amount_multiplier"],
        )
        return int(amount or 0)

    @staticmethod
    def _stock_suggested_buy_limit(
        config_path: Path,
        *,
        minimum: bool = False,
        window=None,
    ) -> int | None:
        target_path = Path(config_path)
        defaults = starting_budget_defaults()
        multiplier_key = (
            "limit_minimum_multiplier" if minimum else "limit_recommended_multiplier"
        )
        reference_price = MainWindow._stock_current_price_for_config(target_path)
        if window is not None:
            try:
                stock_path = str(target_path.parent.relative_to(PROJECT_ROOT))
            except ValueError:
                stock_path = str(target_path.parent)
            try:
                reference_price = main_stock_default_reference_price(
                    window,
                    {"stock_path": stock_path},
                    reference_price,
                )
            except RuntimeError:
                pass
        return suggested_buy_limit(
            reference_price,
            defaults[multiplier_key],
        )

    def handle_routine_stock_operation_double_click(self, row: int) -> bool:
        target = _stock_target_for_row(self, row)
        if target is None:
            return False
        config = read_json_dict(target.stock_dir / "config.json")
        if not isinstance(config, dict) or not config:
            QMessageBox.warning(
                self,
                "운영방식 변경",
                f"{target.code} {target.name}의 운영방식 설정을 읽을 수 없습니다.",
            )
            return True

        adapter = MainMonitoringStockOperationAdapter(
            self,
            [target],
            request_scope="single",
        )
        self._main_monitoring_stock_operation_adapter = adapter
        adapter.handle_operation_mode_double_click()
        return True

    def handle_routine_stock_name_double_click(self, row: int) -> bool:
        target = _stock_target_for_row(self, row)
        if target is None:
            return False
        adapter = MainMonitoringStockOperationAdapter(
            self,
            [target],
            request_scope="single",
        )
        self._main_monitoring_stock_operation_adapter = adapter
        handle_stock_name_operation_exclusion_double_click(
            adapter,
            (target.stock_dir, target.code, target.name),
        )
        return True

    def handle_routine_instance_name_double_click(self, row: int) -> bool:
        item = self.routine_table.item(row, 0)
        if (
            item is None
            or str(item.data(ROUTINE_ROW_KIND_ROLE) or "") != ROUTINE_ROW_CHILD
        ):
            return False

        instance_id = str(item.data(ROUTINE_INSTANCE_ID_ROLE) or "").strip()
        if not instance_id:
            return False

        stock_dirs = list(self._routine_instance_stock_dirs(instance_id))
        if not stock_dirs:
            return False

        should_exclude = False
        if not all(
            is_operation_excluded(read_json_dict(stock_dir / "config.json"))
            for stock_dir in stock_dirs
        ):
            should_exclude = True

        adapter = MainMonitoringStockOperationAdapter(
            self,
            [],
            request_scope="multiple",
        )
        changed = False
        for stock_dir in stock_dirs:
            code, separator, name = stock_dir.name.partition("_")
            if not separator or not code or not name:
                continue
            if adapter.set_stock_operation_exclusion(
                (stock_dir, code, name),
                should_exclude,
                refresh=False,
            ):
                changed = True
        if changed:
            self.refresh_auto_trade_assignment_views()
        return changed

    def handle_routine_group_name_double_click(self, row: int) -> bool:
        item = self.routine_table.item(row, 0)
        if (
            item is None
            or str(item.data(ROUTINE_ROW_KIND_ROLE) or "") != ROUTINE_ROW_PARENT
        ):
            return False

        definition_id = str(item.data(ROUTINE_DEFINITION_ID_ROLE) or "").strip()
        if not definition_id:
            return False

        stock_dirs = []
        for instance_id in self._routine_instance_ids_by_definition.get(
            definition_id,
            (),
        ):
            stock_dirs.extend(self._routine_instance_stock_dirs(instance_id))
        if not stock_dirs:
            return False

        should_exclude = False
        if not all(
            is_operation_excluded(read_json_dict(stock_dir / "config.json"))
            for stock_dir in stock_dirs
        ):
            should_exclude = True

        adapter = MainMonitoringStockOperationAdapter(
            self,
            [],
            request_scope="multiple",
        )
        changed = False
        for stock_dir in stock_dirs:
            code, separator, name = stock_dir.name.partition("_")
            if not separator or not code or not name:
                continue
            if adapter.set_stock_operation_exclusion(
                (stock_dir, code, name),
                should_exclude,
                refresh=False,
            ):
                changed = True
        if changed:
            self.refresh_auto_trade_assignment_views()
        return changed

    def _routine_stock_initial_buy_value_rect(self, row: int) -> QRect:
        index = self.routine_table.model().index(row, 0)
        if not index.isValid():
            return QRect()
        cell_rect = self._routine_tree_interaction_controller._stock_legacy_metric_rect(
            index,
            1,
        )
        value_rect = _initial_buy_component_rects(cell_rect)["value"]
        return QRect(
            value_rect.left(),
            value_rect.top() + 2,
            value_rect.width(),
            max(20, value_rect.height() - 4),
        )

    def _main_routine_initial_buy_badge_enabled(self) -> bool:
        return self._main_routine_display_level == "stock"

    def toggle_routine_stock_initial_buy_mode(self, row: int) -> None:
        if not self._main_routine_initial_buy_badge_enabled():
            return
        config_path = self._stock_config_path_for_routine_row(row)
        if config_path is None:
            return
        self.finish_routine_stock_initial_buy_edit(save=True)
        config = read_json_dict(config_path)
        if not isinstance(config, dict):
            config = {}
        current_mode = str(config.get("trade_amount_type", "QUANTITY") or "").upper()
        next_mode = "QUANTITY" if current_mode == "AMOUNT" else "AMOUNT"
        next_value = self._stock_default_initial_buy_value(config_path, next_mode)
        self._write_stock_initial_buy_config(
            config_path,
            mode=next_mode,
            value=next_value,
        )
        self.load_routine_table()

    def start_routine_stock_initial_buy_edit(self, row: int) -> None:
        if not self._main_routine_initial_buy_badge_enabled():
            return
        item = self.routine_table.item(row, 0)
        stock_path = (
            str(item.data(ROUTINE_STOCK_PATH_ROLE) or "").strip()
            if item is not None
            else ""
        )
        config_path = self._stock_config_path_for_routine_row(row)
        if config_path is None:
            return
        config = read_json_dict(config_path)
        if not isinstance(config, dict):
            config = {}
        mode = str(config.get("trade_amount_type", "QUANTITY") or "").upper()
        if mode != "AMOUNT":
            mode = "QUANTITY"
        value = config.get("buy_amount", 0) if mode == "AMOUNT" else config.get("buy_qty", 0)
        try:
            configured_value = int(value or 0)
        except (TypeError, ValueError):
            configured_value = 0
        value_text = str(
            configured_value
            if configured_value > 0
            else self._stock_default_initial_buy_value(config_path, mode)
        )

        self.finish_routine_stock_initial_buy_edit(save=True)
        self.finish_routine_stock_buy_limit_edit(save=True)
        editor_rect = self._routine_stock_initial_buy_value_rect(row)
        if editor_rect.isNull():
            return
        editor = QLineEdit(self.routine_table.viewport())
        editor.setObjectName("routineStockInitialBuyEditor")
        _apply_routine_inline_edit_style(editor, self.routine_table)
        editor.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        editor.setText(value_text)
        editor.setGeometry(editor_rect)
        editor.installEventFilter(self._routine_buy_limit_edit_filter)
        self.routine_table._editing_stock_initial_buy_path = stock_path
        self.routine_table.viewport().update(
            self.routine_table.visualRect(self.routine_table.model().index(row, 0))
        )
        editor.selectAll()
        editor.show()
        editor.setFocus(Qt.MouseFocusReason)
        self._routine_stock_initial_buy_editor = editor
        self._routine_stock_initial_buy_editor_config_path = str(config_path)
        self._routine_stock_initial_buy_editor_mode = mode

    def finish_routine_stock_initial_buy_edit(self, *, save: bool) -> None:
        editor = self._routine_stock_initial_buy_editor
        if editor is None or self._routine_stock_initial_buy_edit_finishing:
            return
        self._routine_stock_initial_buy_edit_finishing = True
        config_path_text = self._routine_stock_initial_buy_editor_config_path
        mode = self._routine_stock_initial_buy_editor_mode
        normalized = str(editor.text() or "").replace(",", "").replace("원", "").replace("주", "").strip()
        value = int(normalized) if normalized.isdigit() else None

        self._routine_stock_initial_buy_editor = None
        self._routine_stock_initial_buy_editor_config_path = ""
        self._routine_stock_initial_buy_editor_mode = "QUANTITY"
        self.routine_table._editing_stock_initial_buy_path = ""
        editor.hide()
        editor.deleteLater()
        self._routine_stock_initial_buy_edit_finishing = False

        if not save or value is None:
            self.routine_table.viewport().update()
            return
        if mode == "QUANTITY" and value < 1:
            self.routine_table.viewport().update()
            return
        config_path = Path(config_path_text)
        self._write_stock_initial_buy_config(
            config_path,
            mode=mode,
            value=value,
        )
        self.load_routine_table()

    def _routine_stock_buy_limit_value_rect(self, row: int) -> QRect:
        index = self.routine_table.model().index(row, 0)
        if not index.isValid():
            return QRect()
        metric_rect = self._routine_tree_interaction_controller._stock_metric_rect(
            index,
            11,
        )
        if metric_rect.isNull():
            return QRect()
        component_rects = _main_stock_metric_component_rects(
            QFontMetrics(self.routine_table.font()),
            metric_rect,
            MAIN_STOCK_METRIC_LAYOUT["metrics"][4],
        )
        value_rect = component_rects.get("left_value", QRect())
        if value_rect.isNull():
            return QRect()
        return QRect(
            value_rect.left(),
            value_rect.top() + 2,
            value_rect.width(),
            max(20, metric_rect.height() - 4),
        )

    def schedule_routine_stock_buy_limit_single_click(self, row: int) -> None:
        config_path = self._stock_config_path_for_routine_row(row)
        if config_path is None:
            return
        config = read_json_dict(config_path)
        if not isinstance(config, dict) or not bool(
            config.get("buy_limit_enabled", False)
        ):
            return
        if self._stock_current_price_for_config(config_path) is None:
            return
        item = self.routine_table.item(row, 0)
        stock_path = (
            str(item.data(ROUTINE_STOCK_PATH_ROLE) or "").strip()
            if item is not None
            else ""
        )
        if not stock_path:
            return
        self._routine_stock_buy_limit_pending_path = stock_path
        self._routine_stock_buy_limit_click_timer.start(
            QApplication.doubleClickInterval() + 25
        )

    def cancel_routine_stock_buy_limit_single_click(
        self,
        *,
        suppress_release_row: int = -1,
    ) -> None:
        self._routine_stock_buy_limit_click_timer.stop()
        self._routine_stock_buy_limit_pending_path = ""
        self._routine_stock_buy_limit_suppressed_release_row = suppress_release_row

    def consume_routine_stock_buy_limit_release(self, row: int) -> bool:
        if self._routine_stock_buy_limit_suppressed_release_row != row:
            return False
        self._routine_stock_buy_limit_suppressed_release_row = -1
        return True

    def _execute_routine_stock_buy_limit_single_click(self) -> None:
        stock_path = self._routine_stock_buy_limit_pending_path
        self._routine_stock_buy_limit_pending_path = ""
        if not stock_path:
            return
        for row in range(self.routine_table.rowCount()):
            item = self.routine_table.item(row, 0)
            if item is None:
                continue
            candidate = str(item.data(ROUTINE_STOCK_PATH_ROLE) or "").strip()
            if candidate == stock_path:
                self.start_routine_stock_buy_limit_edit(row)
                return

    def start_routine_stock_buy_limit_edit(self, row: int) -> None:
        item = self.routine_table.item(row, 0)
        stock_path = (
            str(item.data(ROUTINE_STOCK_PATH_ROLE) or "").strip()
            if item is not None
            else ""
        )
        config_path = self._stock_config_path_for_routine_row(row)
        if config_path is None:
            return
        config = read_json_dict(config_path)
        if not isinstance(config, dict):
            config = {}
        if self._stock_current_price_for_config(config_path) is None:
            return

        self.finish_routine_instance_buy_limit_edit(save=True)
        self.finish_routine_stock_buy_limit_edit(save=True)

        editor_rect = self._routine_stock_buy_limit_value_rect(row)
        if editor_rect.isNull():
            return
        editor = QLineEdit(self.routine_table.viewport())
        editor.setObjectName("routineStockBuyLimitEditor")
        _apply_routine_inline_edit_style(editor, self.routine_table)
        editor.setStyleSheet(
            """
            QLineEdit {
                border: none;
                background: transparent;
                padding: 0px;
                margin: 0px;
            }
            QLineEdit:focus {
                background: transparent;
            }
            """
        )
        editor.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        configured_amount = self._parse_buy_limit_amount(
            str(config.get("buy_limit_amount") or "")
        )
        suggested_amount = self._stock_suggested_buy_limit(
            config_path,
            window=self,
        )
        editor.setText(str(configured_amount or suggested_amount or ""))
        editor.setGeometry(editor_rect)
        editor.installEventFilter(self._routine_buy_limit_edit_filter)
        self.routine_table._editing_stock_buy_limit_path = stock_path
        self.routine_table.viewport().update(
            self.routine_table.visualRect(self.routine_table.model().index(row, 0))
        )
        editor.selectAll()
        editor.show()
        editor.setFocus(Qt.MouseFocusReason)

        self._routine_stock_buy_limit_editor = editor
        self._routine_stock_buy_limit_editor_config_path = str(config_path)

    def handle_routine_stock_buy_limit_double_click(self, row: int) -> None:
        config_path = self._stock_config_path_for_routine_row(row)
        if config_path is None:
            return
        config = read_json_dict(config_path)
        if not isinstance(config, dict):
            config = {}
        enabled = bool(config.get("buy_limit_enabled", False))

        self.finish_routine_instance_buy_limit_edit(save=True)
        self.finish_routine_stock_buy_limit_edit(save=False)

        if enabled:
            self._write_stock_buy_limit_config(
                config_path,
                enabled=False,
                amount=None,
            )
        else:
            recommended = self._stock_suggested_buy_limit(
                config_path,
                window=self,
            )
            total_budget = _system_total_budget_amount()
            activate_limit = True
            if (
                recommended is not None
                and total_budget is not None
                and recommended > total_budget
            ):
                activate_limit = False
                recommended = None
            elif total_budget is None:
                recommended = None
            self._write_stock_buy_limit_config(
                config_path,
                enabled=activate_limit,
                amount=recommended,
            )
        self.load_routine_table()

    def finish_routine_stock_buy_limit_edit(self, *, save: bool) -> None:
        editor = self._routine_stock_buy_limit_editor
        if editor is None or self._routine_stock_buy_limit_edit_finishing:
            return
        self._routine_stock_buy_limit_edit_finishing = True
        config_path_text = self._routine_stock_buy_limit_editor_config_path
        amount = self._parse_buy_limit_amount(editor.text()) if save else None

        self._routine_stock_buy_limit_editor = None
        self._routine_stock_buy_limit_editor_config_path = ""
        self.routine_table._editing_stock_buy_limit_path = ""
        editor.hide()
        editor.deleteLater()
        self._routine_stock_buy_limit_edit_finishing = False
        self.routine_table.viewport().update()

        if not save:
            return
        config_path = Path(config_path_text)
        if amount is None:
            return
        minimum_amount = self._stock_suggested_buy_limit(
            config_path,
            minimum=True,
            window=self,
        )
        if minimum_amount is None:
            return
        total_budget = _system_total_budget_amount()
        if total_budget is None:
            return
        if amount > total_budget:
            self._write_stock_buy_limit_config(
                config_path,
                enabled=False,
                amount=None,
            )
            self.load_routine_table()
            return
        if amount < minimum_amount:
            QMessageBox.warning(
                self,
                "종목 한도 변경",
                f"종목 한도는 현재 최소 금액 {minimum_amount:,}원 이상이어야 합니다.",
            )
            return
        try:
            self._write_stock_buy_limit_config(
                config_path,
                enabled=True,
                amount=amount,
            )
        except Exception as exc:
            QMessageBox.warning(
                self,
                "종목 한도 변경",
                f"종목 한도를 변경하지 못했습니다.\n{exc}",
            )
            return
        self.load_routine_table()

    def handle_routine_instance_buy_limit_double_click(self, amount_label: QLabel) -> None:
        instance_id = self._routine_instance_id_for_buy_limit_widget(amount_label)
        if not instance_id:
            return
        instance = routine_instance_by_id(instance_id)
        if instance is None:
            return

        self.finish_routine_stock_buy_limit_edit(save=True)
        self.finish_routine_instance_buy_limit_edit(save=False)
        enabled = False
        amount = None
        if not instance.buy_limit_enabled:
            recommended, _minimum = routine_instance_suggested_buy_limits(
                self,
                instance_id,
            )
            total_budget = _system_total_budget_amount()
            if recommended is None or total_budget is None:
                enabled = True
            elif recommended <= total_budget:
                enabled = True
                amount = recommended
        result = RoutineInstanceRepository(PROJECT_ROOT).update_buy_limit(
            instance_id,
            enabled=enabled,
            amount=amount,
        )
        if not result.success:
            QMessageBox.warning(
                self,
                "매수한도 변경",
                result.error or "매수한도를 변경하지 못했습니다.",
            )
            return
        self.refresh_all()

    def _routine_instance_id_for_buy_limit_widget(self, widget: QWidget) -> str:
        instance_id = str(widget.property("routine_instance_id") or "").strip()
        if instance_id:
            return instance_id
        row = self._routine_row_for_child_widget(widget)
        if row < 0:
            return ""
        item = self.routine_table.item(row, 0)
        if item is None or str(item.data(ROUTINE_ROW_KIND_ROLE) or "") != ROUTINE_ROW_CHILD:
            return ""
        return str(item.data(ROUTINE_INSTANCE_ID_ROLE) or "").strip()

    def schedule_routine_instance_buy_limit_single_click(self, amount_label: QLabel) -> None:
        instance_id = self._routine_instance_id_for_buy_limit_widget(amount_label)
        instance = routine_instance_by_id(instance_id) if instance_id else None
        if instance is None or not instance.buy_limit_enabled:
            return
        self._routine_instance_buy_limit_pending_id = instance_id
        self._routine_instance_buy_limit_click_timer.start(
            QApplication.doubleClickInterval() + 25
        )

    def cancel_routine_instance_buy_limit_single_click(
        self,
        *,
        suppress_release_widget: QWidget | None = None,
    ) -> None:
        self._routine_instance_buy_limit_click_timer.stop()
        self._routine_instance_buy_limit_pending_id = ""
        self._routine_instance_buy_limit_suppressed_release_widget = (
            suppress_release_widget
        )

    def consume_routine_instance_buy_limit_release(self, widget: QWidget) -> bool:
        if self._routine_instance_buy_limit_suppressed_release_widget is not widget:
            return False
        self._routine_instance_buy_limit_suppressed_release_widget = None
        return True

    def _execute_routine_instance_buy_limit_single_click(self) -> None:
        instance_id = self._routine_instance_buy_limit_pending_id
        self._routine_instance_buy_limit_pending_id = ""
        if not instance_id:
            return
        for amount_label in self.routine_table.findChildren(
            QLabel,
            "routineInstanceBuyLimitAmount",
        ):
            if self._routine_instance_id_for_buy_limit_widget(amount_label) == instance_id:
                self.start_routine_instance_buy_limit_edit(amount_label)
                return

    def start_routine_instance_buy_limit_edit(self, amount_label: QLabel) -> None:
        instance_id = self._routine_instance_id_for_buy_limit_widget(amount_label)
        if not instance_id:
            return
        instance = routine_instance_by_id(instance_id)
        if instance is None or not instance.buy_limit_enabled:
            return

        self.finish_routine_stock_buy_limit_edit(save=True)
        self.finish_routine_instance_buy_limit_edit(save=True)

        value_slot = amount_label.parentWidget()
        editor = (
            value_slot.findChild(QLineEdit, "routineInstanceBuyLimitEditor")
            if value_slot is not None
            else None
        )
        value_stack = value_slot.layout() if value_slot is not None else None
        if editor is None or value_stack is None:
            return

        _apply_routine_inline_edit_style(editor, self.routine_table)
        editor.setText(str(instance.buy_limit_amount or ""))
        editor.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        editor.selectAll()
        if hasattr(value_stack, "setCurrentWidget"):
            value_stack.setCurrentWidget(editor)
        amount_label.hide()
        editor.show()
        editor.setFocus(Qt.MouseFocusReason)

        self._routine_instance_buy_limit_editor = editor
        self._routine_instance_buy_limit_editor_instance_id = instance_id
        self._routine_instance_buy_limit_editor_label = amount_label

    def handle_routine_instance_buy_limit_settings_click(
        self,
        settings_label: QLabel,
    ) -> bool:
        instance_id = self._routine_instance_id_for_buy_limit_widget(settings_label)
        if not instance_id:
            return False
        return self.open_routine_instance_buy_limit_settings(instance_id)

    def open_routine_instance_buy_limit_settings(self, instance_id: str) -> bool:
        """Open the persisted response-policy editor for one RoutineInstance."""
        clean_instance_id = str(instance_id or "").strip()
        repository = RoutineInstanceRepository(PROJECT_ROOT)
        if not clean_instance_id or repository.get_instance(clean_instance_id) is None:
            return False
        surfaces = getattr(self, "_routine_limit_response_settings_surfaces", None)
        if surfaces is None:
            surfaces = {}
            self._routine_limit_response_settings_surfaces = surfaces
        surface = surfaces.get(clean_instance_id)
        if surface is None or sip.isdeleted(surface):
            surface = _RoutineLimitResponseSettingsSurface(
                self,
                clean_instance_id,
                repository=repository,
            )
            surfaces[clean_instance_id] = surface
        elif not surface.isVisible():
            surface.reload_from_persisted()
        surface.show()
        surface.raise_()
        surface.activateWindow()
        return True

    def finish_routine_instance_buy_limit_edit(self, *, save: bool) -> None:
        editor = self._routine_instance_buy_limit_editor
        if editor is None or self._routine_instance_buy_limit_edit_finishing:
            return
        self._routine_instance_buy_limit_edit_finishing = True
        instance_id = self._routine_instance_buy_limit_editor_instance_id
        amount_label = self._routine_instance_buy_limit_editor_label
        amount = self._parse_buy_limit_amount(editor.text()) if save else None

        self._routine_instance_buy_limit_editor = None
        self._routine_instance_buy_limit_editor_instance_id = ""
        self._routine_instance_buy_limit_editor_label = None

        value_slot = editor.parentWidget()
        value_stack = value_slot.layout() if value_slot is not None else None
        editor.hide()
        if amount_label is not None:
            amount_label.show()
            if hasattr(value_stack, "setCurrentWidget"):
                value_stack.setCurrentWidget(amount_label)
        self._routine_instance_buy_limit_edit_finishing = False

        if not save or amount is None:
            return
        recommended, minimum = routine_instance_suggested_buy_limits(
            self,
            instance_id,
        )
        total_budget = _system_total_budget_amount()
        if recommended is None or minimum is None or total_budget is None:
            return
        if amount > total_budget:
            result = RoutineInstanceRepository(PROJECT_ROOT).update_buy_limit(
                instance_id,
                enabled=False,
                amount=None,
            )
            if not result.success:
                QMessageBox.warning(
                    self,
                    "매수한도 변경",
                    result.error or "매수한도를 변경하지 못했습니다.",
                )
                return
            self.refresh_all()
            return
        if amount < minimum:
            show_toast(
                self,
                f"최저금액은 {minimum:,}원입니다.",
                duration_ms=2500,
            )
            return
        adjustment_ratio = Decimal(amount) / Decimal(recommended)
        result = RoutineInstanceRepository(PROJECT_ROOT).update_buy_limit(
            instance_id,
            enabled=True,
            amount=amount,
            adjustment_ratio=adjustment_ratio,
        )
        if not result.success:
            QMessageBox.warning(
                self,
                "매수한도 변경",
                result.error or "매수한도를 변경하지 못했습니다.",
            )
            return
        self.refresh_all()

    def _routine_instance_has_assigned_stocks(self, instance_id: str) -> bool:
        return int(
            self._routine_assigned_stock_count_by_instance.get(instance_id, 0) or 0
        ) > 0

    def _routine_instance_stock_dirs(self, instance_id: str) -> list[Path]:
        result: list[Path] = []
        stocks_root = PROJECT_ROOT / "stocks"
        if not stocks_root.exists():
            return result
        for stock_dir in sorted(path for path in stocks_root.iterdir() if path.is_dir()):
            config = read_json_dict(stock_dir / "config.json")
            if (
                str(config.get("assigned_routine_instance_id") or "").strip()
                == str(instance_id or "").strip()
            ):
                result.append(stock_dir)
        return result

    def _running_routine_operation_targets(
        self,
        instance_ids,
    ) -> list[MainMonitoringStockTarget]:
        running_by_path = {
            str(Path(stock_dir).resolve()): (Path(stock_dir), code, name)
            for stock_dir, code, name in auto_trade_running_registered_operation_targets(
                self
            )
        }
        targets: list[MainMonitoringStockTarget] = []
        seen_paths: set[str] = set()
        for instance_id in instance_ids:
            clean_instance_id = str(instance_id or "").strip()
            if not clean_instance_id:
                continue
            for stock_dir in self._routine_instance_stock_dirs(clean_instance_id):
                stock_path = str(Path(stock_dir).resolve())
                running = running_by_path.get(stock_path)
                if running is None or stock_path in seen_paths:
                    continue
                seen_paths.add(stock_path)
                resolved_dir, code, name = running
                targets.append(
                    MainMonitoringStockTarget(
                        stock_dir=resolved_dir,
                        code=code,
                        name=name,
                        routine_instance_id=clean_instance_id,
                    )
                )
        return targets

    def _visible_monitoring_early_close_targets(self) -> list[MainMonitoringStockTarget]:
        running_by_path = {
            str(Path(stock_dir).resolve()): (Path(stock_dir), code, name)
            for stock_dir, code, name in auto_trade_running_registered_operation_targets(
                self
            )
        }
        targets: list[MainMonitoringStockTarget] = []
        seen_paths: set[str] = set()
        for row in range(self.routine_table.rowCount()):
            target = _stock_target_for_row(self, row)
            if target is None:
                continue
            stock_path = str(Path(target.stock_dir).resolve())
            running = running_by_path.get(stock_path)
            if running is None or stock_path in seen_paths:
                continue
            state = read_json_dict(Path(target.stock_dir) / "state.json")
            if is_review_required_state(state) or is_emergency_stopped_state(state):
                continue
            seen_paths.add(stock_path)
            resolved_dir, code, name = running
            targets.append(
                MainMonitoringStockTarget(
                    stock_dir=resolved_dir,
                    code=code,
                    name=name,
                    routine_instance_id=target.routine_instance_id,
                )
            )
        return targets

    def request_visible_monitoring_early_close(self) -> None:
        targets = self._visible_monitoring_early_close_targets()
        if not targets:
            message = "조기마감 대상이 없습니다."
            show_toast(self, message, duration_ms=2500)
            self.statusBar().showMessage("관제창 조기마감: 대상 0")
            return

        answer = _create_routine_operation_confirmation(
            self,
            ROUTINE_STATUS_EARLY_CLOSE,
        ).exec_()
        accepted = answer == QMessageBox.Yes
        method = (
            str(operation_policy_section("early_close").get("method", "루틴")).strip()
            or "루틴"
        )
        append_production_event(
            "OPERATOR_OPERATION_DECISION",
            result="ACCEPTED" if accepted else "CANCELLED",
            source="gui_windows.MainWindow.request_visible_monitoring_early_close",
            target_type="VISIBLE_MONITORING_STOCKS",
            target_id="visible",
            target_name="현재 표시 종목",
            details={
                "interaction_type": "CONFIRM",
                "prompt_key": "VISIBLE_MONITORING_EARLY_CLOSE_CONFIRM",
                "prompt_title": "조기마감",
                "prompt_summary": "관제창 현재 표시 종목 조기마감 적용",
                "offered_options": ["진행", "취소"],
                "selected_option": "진행" if accepted else "취소",
                "operation": "EARLY_CLOSE",
                "method": method,
                "target_count": len(targets),
            },
        )
        if not accepted:
            self.statusBar().showMessage("관제창 조기마감 취소")
            return

        adapter = MainMonitoringStockOperationAdapter(
            self,
            targets,
            request_scope="multiple",
        )
        self._main_monitoring_stock_operation_adapter = adapter
        result = adapter.apply_selected_early_close(
            method,
            source="main_visible_early_close_button",
            show_error_dialog=False,
            show_result_toast=False,
            show_confirmation=False,
        )
        applied_count = int(result.get("completed_count", 0) or 0)
        failed_count = int(result.get("failed_count", 0) or 0)
        result_message = str(result.get("message") or "").strip()
        blocked_without_counts = (
            result.get("ok") is False
            and not applied_count
            and not failed_count
            and bool(result_message)
            and "대상이 없습니다" not in result_message
        )
        self.update_review_required_button_text()
        if applied_count and failed_count:
            QMessageBox.warning(
                self,
                "관제창 조기마감 일부 적용",
                f"조기마감 {applied_count}건 접수 / {failed_count}건 차단",
            )
        elif failed_count or blocked_without_counts:
            QMessageBox.warning(
                self,
                "관제창 조기마감 실패",
                result_message or "현재 상태에서는 실행할 수 없습니다.",
            )
        elif applied_count:
            show_toast(
                self,
                f"조기마감 {applied_count}종목 적용 합니다.",
                duration_ms=2500,
            )
        else:
            show_toast(
                self,
                "조기마감 대상이 없습니다.",
                duration_ms=2500,
            )
        self.statusBar().showMessage(
            f"관제창 조기마감: 성공 {applied_count} / 차단 {failed_count}"
        )

    def toggle_routine_instance_operation(self, instance_id: str) -> None:
        instance = routine_instance_by_id(instance_id)
        if instance is None:
            result = {
                "ok": False,
                "reason": "INSTANCE_NOT_FOUND",
                "user_message": (
                    "선택한 루틴 정보를 읽을 수 없습니다.\n"
                    "화면을 새로고침한 뒤 다시 시도하십시오."
                ),
            }
            self.statusBar().showMessage(str(result["user_message"]))
            show_auto_trade_operation_failure_dialog(
                self,
                "운영시작",
                result,
            )
            return

        targets: list[MainMonitoringStockTarget] = []
        operational_targets: list[MainMonitoringStockTarget] = []
        current_running_stock_dirs = {
            str(Path(stock_dir).resolve())
            for stock_dir, _code, _name in (
                auto_trade_running_registered_operation_targets(self)
            )
        }
        current_running_targets: list[MainMonitoringStockTarget] = []
        for stock_dir in self._routine_instance_stock_dirs(instance_id):
            code, separator, name = stock_dir.name.partition("_")
            if not separator or not code or not name:
                continue
            state = read_json_dict(stock_dir / "state.json")
            target = MainMonitoringStockTarget(
                stock_dir=stock_dir,
                code=code,
                name=name,
                routine_instance_id=instance_id,
            )
            targets.append(target)
            registry_review_checker = getattr(
                self,
                "production_recovery_stock_is_review_required",
                None,
            )
            if (
                is_review_required_state(state)
                or (
                    callable(registry_review_checker)
                    and registry_review_checker(code)
                )
            ):
                continue
            operational_targets.append(target)
            if str(stock_dir.resolve()) in current_running_stock_dirs:
                current_running_targets.append(target)

        any_running = bool(current_running_targets)

        if not targets:
            result = {
                "ok": False,
                "reason": "NO_REGISTERED_STOCKS",
                "user_message": (
                    "선택한 루틴에 등록된 종목이 없습니다.\n"
                    "자동매매 설정에서 종목을 등록하십시오."
                ),
            }
            self.statusBar().showMessage(str(result["user_message"]))
            show_auto_trade_operation_failure_dialog(
                self,
                "운영시작",
                result,
            )
            return

        if not operational_targets:
            self._reload_main_routine_table_preserving_view()
            return

        if any_running:
            self.statusBar().showMessage("운영 중단은 긴급정지를 사용하십시오.")
            return

        adapter = MainMonitoringStockOperationAdapter(
            self,
            operational_targets,
            request_scope="multiple",
            recovery_action_label="루틴 재시작",
        )
        self._main_monitoring_stock_operation_adapter = adapter
        requested_action = "운영시작"
        try:
            operation_result = adapter.start_selected_auto_trades()
        except Exception:
            LOGGER.exception(
                "루틴 %s %s 처리 오류",
                instance_id,
                requested_action,
            )
            self._reload_main_routine_table_preserving_view()
            message = (
                "운영 상태를 변경하는 중 오류가 발생했습니다.\n"
                "로그를 확인한 뒤 다시 시도하십시오."
            )
            self.statusBar().showMessage(
                message
            )
            QMessageBox.critical(
                self,
                f"{requested_action} 오류",
                message,
            )
            return

        running_after_stock_dirs = {
            str(Path(stock_dir).resolve())
            for stock_dir, _code, _name in (
                auto_trade_running_registered_operation_targets(self)
            )
        }
        running_after = any(
            str(stock_dir.resolve()) in running_after_stock_dirs
            for stock_dir in self._routine_instance_stock_dirs(instance_id)
        )
        transition_succeeded = running_after
        self._reload_main_routine_table_preserving_view()
        if transition_succeeded:
            user_message = (
                str(operation_result.get("user_message") or "").strip()
                if isinstance(operation_result, dict)
                else ""
            )
            self.statusBar().showMessage(
                user_message
                or (
                    f"{instance.display_name} {requested_action} 완료 "
                    f"(대상 {len(operational_targets)}종목)"
                )
            )
            return

        user_message = ""
        if isinstance(operation_result, dict):
            user_message = str(
                operation_result.get("user_message") or ""
            ).strip()
        if not user_message:
            user_message = str(
                getattr(adapter, "_last_operation_user_message", "") or ""
            ).strip()
        self.statusBar().showMessage(
            f"{instance.display_name} {requested_action} 실패: "
            f"{user_message or '로그인, 계좌 및 운영 상태를 확인하십시오.'}"
        )
        adapter.show_operation_failure_dialog(requested_action, operation_result)

    def _production_recovery_allows_routine_operation(
        self,
        instance_id: str,
        *,
        command: str,
        caller_name: str,
    ) -> bool:
        for stock_dir in self._routine_instance_stock_dirs(instance_id):
            code, _, _name = stock_dir.name.partition("_")
            try:
                decision = self.production_recovery_gate_for_stock(
                    code,
                    caller_name=caller_name,
                )
            except Exception:
                LOGGER.exception(
                    "Routine operation Recovery Gate failed: "
                    "command=%s caller=%s instance=%s stock=%s",
                    command,
                    caller_name,
                    instance_id,
                    code,
                )
                return False
            if getattr(decision, "allowed", None) is True:
                continue
            api = getattr(self, "kiwoom_api", None)
            login_session_reader = getattr(api, "login_session_id", None)
            login_session_present = False
            if callable(login_session_reader):
                try:
                    login_session_present = bool(
                        str(login_session_reader() or "").strip()
                    )
                except Exception:
                    login_session_present = False
            account_selected = False
            try:
                account_selected = bool(str(self.selected_account_no() or "").strip())
            except Exception:
                account_selected = False
            reason_code = str(getattr(decision, "reason_code", "") or "").strip()
            evidence = tuple(getattr(decision, "evidence", ()) or ())
            has_internal_error_evidence = any(
                str(item).startswith(("registry_error=", "gate_exception="))
                for item in evidence
            )
            if (
                reason_code not in EXPECTED_USER_ACTION_RECOVERY_BLOCK_REASONS
                or has_internal_error_evidence
            ):
                LOGGER.warning(
                    "Routine operation blocked by Production Recovery: "
                    "command=%s caller=%s instance=%s stock=%s reason=%s "
                    "evidence=%s login_session_present=%s account_selected=%s "
                    "requested_at=%s",
                    command,
                    caller_name,
                    instance_id,
                    code,
                    reason_code,
                    evidence,
                    login_session_present,
                    account_selected,
                    datetime.now().isoformat(timespec="seconds"),
                )
            return False
        return True

    @staticmethod
    def _set_routine_operation_actions_enabled(actions, enabled: bool) -> None:
        unavailable_reason = "등록된 종목이 없어 실행할 수 없습니다."
        for action in actions:
            action.setEnabled(enabled)
            action.setStatusTip("" if enabled else unavailable_reason)
            action.setToolTip("" if enabled else unavailable_reason)

    def open_routine_context_menu(self, position) -> None:
        item = self.routine_table.itemAt(position)
        if item is None:
            return
        first_item = self.routine_table.item(item.row(), 0)
        if first_item is None:
            return
        row_kind = str(first_item.data(ROUTINE_ROW_KIND_ROLE) or "")
        if row_kind == ROUTINE_ROW_STOCK:
            show_main_monitoring_stock_context_menu(self, position)
            return
        if row_kind == ROUTINE_ROW_PARENT:
            index = self.routine_table.model().index(item.row(), 0)
            if not self._routine_tree_interaction_controller._parent_name_rect(index).contains(
                position
            ):
                return
            definition_id = str(
                first_item.data(ROUTINE_DEFINITION_ID_ROLE) or ""
            ).strip()
            definition = routine_definition_by_id(definition_id)
            if definition is None:
                QMessageBox.warning(
                    self,
                    "루틴 운영",
                    "선택한 루틴 카테고리를 확인할 수 없습니다.",
                )
                return
            menu = QMenu(self.routine_table)
            menu.setToolTipsVisible(True)
            new_routine_action = menu.addAction("신규루틴")
            menu.addSeparator()
            early_close_action = menu.addAction("조기마감")
            immediate_action = menu.addAction("즉시청산")
            set_menu_action_text_color(
                menu,
                early_close_action,
                CONTEXT_MENU_DANGER_TEXT_COLOR,
            )
            has_valid_target = any(
                routine_instance_checked(self, instance_id)
                and self._routine_instance_has_assigned_stocks(instance_id)
                for instance_id in self._routine_instance_ids_by_definition.get(
                    definition_id,
                    (),
                )
            )
            self._set_routine_operation_actions_enabled(
                (early_close_action, immediate_action),
                has_valid_target,
            )
            new_routine_action.triggered.connect(
                lambda _checked=False, item=first_item: self.open_routine_settings_from_main_table(
                    item
                )
            )
            early_close_action.triggered.connect(
                lambda _checked=False: self.request_routine_definition_operation(
                    definition_id,
                    definition.display_name,
                    "루틴",
                    ROUTINE_STATUS_EARLY_CLOSE,
                )
            )
            immediate_action.triggered.connect(
                lambda _checked=False: self.request_routine_definition_operation(
                    definition_id,
                    definition.display_name,
                    POLICY_MARKET,
                    ROUTINE_STATUS_IMMEDIATE_LIQUIDATION,
                )
            )
            chosen = menu.exec_(self.routine_table.viewport().mapToGlobal(position))
            if chosen in (early_close_action, immediate_action):
                market_selected = chosen == immediate_action
                append_production_event(
                    "OPERATOR_OPERATION_DECISION",
                    result="ACCEPTED",
                    source="gui_windows.MainWindow.open_routine_context_menu",
                    target_type="ROUTINE_DEFINITION",
                    target_id=definition_id,
                    target_name=definition.display_name,
                    routine=definition.display_name,
                    details={
                        "interaction_type": "SELECTION",
                        "prompt_key": "ROUTINE_DEFINITION_CONTEXT_MENU",
                        "prompt_title": "루틴 카테고리 메뉴",
                        "prompt_summary": "루틴 카테고리 운영 action 선택",
                        "offered_options": ["조기마감", "즉시청산"],
                        "selected_option": (
                            "EARLY_CLOSE_MARKET" if market_selected else "EARLY_CLOSE_ROUTINE"
                        ),
                        "method": "market" if market_selected else "routine",
                    },
                )
            return
        if row_kind != ROUTINE_ROW_CHILD:
            return
        instance_id = str(first_item.data(ROUTINE_INSTANCE_ID_ROLE) or "").strip()
        if not instance_id:
            return
        instance = routine_instance_by_id(instance_id)
        if instance is None:
            QMessageBox.warning(self, "루틴 운영", "선택한 등록 루틴을 확인할 수 없습니다.")
            return

        menu = QMenu(self.routine_table)
        menu.setToolTipsVisible(True)
        settings_action = menu.addAction("설정변경")
        rename_action = menu.addAction("이름변경")
        stock_register_action = menu.addAction("종목등록")
        menu.addSeparator()
        early_close_action = menu.addAction("조기마감")
        immediate_action = menu.addAction("즉시청산")
        set_menu_action_text_color(
            menu,
            early_close_action,
            CONTEXT_MENU_DANGER_TEXT_COLOR,
        )
        settings_action.triggered.connect(
            lambda _checked=False, item=first_item: self.open_routine_settings_from_main_table(
                item
            )
        )
        rename_action.triggered.connect(
            lambda _checked=False, row=item.row(): self.start_routine_instance_name_edit(
                row
            )
        )
        stock_register_action.triggered.connect(
            lambda _checked=False, target_id=instance_id: self.open_routine_instance_stock_register_from_main_table(
                target_id
            )
        )
        self._set_routine_operation_actions_enabled(
            (early_close_action, immediate_action),
            self._routine_instance_has_assigned_stocks(instance_id),
        )
        early_close_action.triggered.connect(
            lambda _checked=False: self.request_routine_operation(
                instance_id,
                instance.display_name,
                "루틴",
                ROUTINE_STATUS_EARLY_CLOSE,
            )
        )
        immediate_action.triggered.connect(
            lambda _checked=False: self.request_routine_operation(
                instance_id,
                instance.display_name,
                POLICY_MARKET,
                ROUTINE_STATUS_IMMEDIATE_LIQUIDATION,
            )
        )
        chosen = menu.exec_(self.routine_table.viewport().mapToGlobal(position))
        if chosen in (early_close_action, immediate_action):
            market_selected = chosen == immediate_action
            append_production_event(
                "OPERATOR_OPERATION_DECISION",
                result="ACCEPTED",
                source="gui_windows.MainWindow.open_routine_context_menu",
                target_type="ROUTINE_INSTANCE",
                target_id=instance_id,
                target_name=instance.display_name,
                routine=instance.display_name,
                details={
                    "interaction_type": "SELECTION",
                    "prompt_key": "ROUTINE_INSTANCE_CONTEXT_MENU",
                    "prompt_title": "등록 루틴 메뉴",
                    "prompt_summary": "등록 루틴 운영 action 선택",
                    "offered_options": ["조기마감", "즉시청산"],
                    "selected_option": (
                        "EARLY_CLOSE_MARKET" if market_selected else "EARLY_CLOSE_ROUTINE"
                    ),
                    "method": "market" if market_selected else "routine",
                },
            )

    def open_routine_instance_stock_register_from_main_table(self, instance_id: str) -> None:
        clean_instance_id = str(instance_id or "").strip()
        if not clean_instance_id:
            return
        instance = routine_instance_by_id(clean_instance_id)
        if instance is None:
            QMessageBox.warning(self, "종목등록", "선택한 루틴을 확인할 수 없습니다.")
            return
        definition = routine_definition_by_id(str(instance.definition_id or "").strip())
        if definition is None:
            QMessageBox.warning(self, "종목등록", "선택한 루틴 유형을 확인할 수 없습니다.")
            return
        instance_dir = Path(instance.rules_path).parent if instance.rules_path else Path()
        metadata = {
            "row_kind": "instance",
            "definition_id": str(definition.definition_id),
            "instance_id": str(instance.instance_id),
            "definition_name": str(definition.display_name),
            "instance_name": str(instance.display_name),
            "package_dir": str(definition.package_dir),
            "instance_dir": str(instance_dir) if instance_dir else "",
            "display_name": str(instance.display_name),
        }
        self.instance_stock_search_register_window = InstanceStockSearchRegisterDialog(
            self,
            instance_metadata=metadata,
        )
        self.instance_stock_search_register_window.show()

    def request_routine_definition_operation(
        self,
        definition_id: str,
        display_name: str,
        requested_policy: str,
        display_status: str,
    ) -> None:
        instance_ids = tuple(
            instance_id
            for instance_id in sorted(
                self._routine_instance_ids_by_definition.get(definition_id, ())
            )
            if routine_instance_checked(self, instance_id)
            and self._routine_instance_has_assigned_stocks(instance_id)
        )
        command_label = display_status
        market_requested = requested_policy == POLICY_MARKET
        if not instance_ids:
            QMessageBox.warning(
                self,
                f"카테고리 {command_label} 불가",
                "체크된 하위 루틴이 없습니다.",
            )
            return

        targets = MainWindow._running_routine_operation_targets(self, instance_ids)
        if not targets:
            message = f"{command_label} 대상이 없습니다."
            show_toast(self, message, duration_ms=2500)
            self.statusBar().showMessage(
                f"카테고리 {command_label}: {display_name} / 대상 0"
            )
            return

        if market_requested:
            answer = _create_routine_operation_confirmation(
                self,
                display_status,
                QMessageBox.Warning,
            ).exec_()
        else:
            answer = _create_routine_operation_confirmation(self, display_status).exec_()
        accepted = answer == QMessageBox.Yes
        append_production_event(
            "OPERATOR_OPERATION_DECISION",
            result="ACCEPTED" if accepted else "CANCELLED",
            source="gui_windows.MainWindow.request_routine_definition_operation",
            target_type="ROUTINE_DEFINITION",
            target_id=definition_id,
            target_name=display_name,
            routine=display_name,
            details={
                "interaction_type": "CONFIRM",
                "prompt_key": "ROUTINE_DEFINITION_EARLY_CLOSE_CONFIRM",
                "prompt_title": "즉시청산" if market_requested else "조기마감",
                "prompt_summary": "루틴 카테고리 조기마감 적용",
                "offered_options": ["진행", "취소"],
                "selected_option": "진행" if accepted else "취소",
                "operation": "EARLY_CLOSE",
                "method": "market" if market_requested else "routine",
            },
        )
        if answer != QMessageBox.Yes:
            self.statusBar().showMessage(
                f"카테고리 {command_label} 취소: {display_name}"
            )
            return

        adapter = MainMonitoringStockOperationAdapter(
            self,
            targets,
            request_scope="multiple",
        )
        self._main_monitoring_stock_operation_adapter = adapter
        result = adapter.apply_selected_early_close(
            requested_policy,
            source="main_routine_parent_context_menu",
            show_error_dialog=False,
            show_result_toast=False,
            show_confirmation=False,
        )
        applied_count = int(result.get("completed_count", 0) or 0)
        failed_count = int(result.get("failed_count", 0) or 0)
        result_message = str(result.get("message") or "").strip()
        blocked_without_counts = (
            result.get("ok") is False
            and not applied_count
            and not failed_count
            and bool(result_message)
            and "대상이 없습니다" not in result_message
        )
        self.update_review_required_button_text()
        if applied_count and failed_count:
            QMessageBox.warning(
                self,
                f"카테고리 {command_label} 일부 적용",
                f"{command_label} {applied_count}건 접수 / {failed_count}건 차단",
            )
        elif failed_count or blocked_without_counts:
            QMessageBox.warning(
                self,
                f"카테고리 {command_label} 실패",
                result_message or "현재 상태에서는 실행할 수 없습니다.",
            )
        elif applied_count:
            success_message = (
                f"조기마감 {applied_count}종목 적용 합니다."
                if command_label == ROUTINE_STATUS_EARLY_CLOSE
                else f"{display_name} {command_label} 요청이 접수되었습니다."
            )
            show_toast(
                self,
                success_message,
                duration_ms=2500,
            )
        else:
            show_toast(
                self,
                f"{command_label} 대상이 없습니다.",
                duration_ms=2500,
            )
        self.statusBar().showMessage(
            f"카테고리 {command_label}: {display_name} / 성공 {applied_count} / "
            f"차단 {failed_count}"
        )

    def request_routine_operation(
        self,
        instance_id: str,
        display_name: str,
        requested_policy: str,
        display_status: str,
    ) -> None:
        command_label = display_status
        market_requested = requested_policy == POLICY_MARKET
        targets = MainWindow._running_routine_operation_targets(self, (instance_id,))
        if not targets:
            message = f"{command_label} 대상이 없습니다."
            show_toast(self, message, duration_ms=2500)
            self.statusBar().showMessage(
                f"루틴 {command_label}: {display_name} / 대상 0"
            )
            return
        answer = _create_routine_operation_confirmation(self, display_status).exec_()
        accepted = answer == QMessageBox.Yes
        append_production_event(
            "OPERATOR_OPERATION_DECISION",
            result="ACCEPTED" if accepted else "CANCELLED",
            source="gui_windows.MainWindow.request_routine_operation",
            target_type="ROUTINE_INSTANCE",
            target_id=instance_id,
            target_name=display_name,
            routine=display_name,
            details={
                "interaction_type": "CONFIRM",
                "prompt_key": "ROUTINE_INSTANCE_EARLY_CLOSE_CONFIRM",
                "prompt_title": "즉시청산" if market_requested else "조기마감",
                "prompt_summary": "등록 루틴 조기마감 적용",
                "offered_options": ["진행", "취소"],
                "selected_option": "진행" if accepted else "취소",
                "operation": "EARLY_CLOSE",
                "method": "market" if market_requested else "routine",
            },
        )
        if answer != QMessageBox.Yes:
            self.statusBar().showMessage(f"루틴 {command_label} 취소: {display_name}")
            return

        adapter = MainMonitoringStockOperationAdapter(
            self,
            targets,
            request_scope="multiple",
        )
        self._main_monitoring_stock_operation_adapter = adapter
        result = adapter.apply_selected_early_close(
            requested_policy,
            source="main_routine_context_menu",
            show_error_dialog=False,
            show_result_toast=False,
            show_confirmation=False,
        )
        applied_count = int(result.get("completed_count", 0) or 0)
        failed_count = int(result.get("failed_count", 0) or 0)
        result_message = str(result.get("message") or "").strip()
        blocked_without_counts = (
            result.get("ok") is False
            and not applied_count
            and not failed_count
            and bool(result_message)
            and "대상이 없습니다" not in result_message
        )
        self.update_review_required_button_text()
        if applied_count and failed_count:
            QMessageBox.warning(
                self,
                f"루틴 {command_label} 일부 적용",
                f"{command_label} {applied_count}건 접수 / {failed_count}건 차단",
            )
        elif failed_count or blocked_without_counts:
            QMessageBox.warning(
                self,
                f"루틴 {command_label} 실패",
                result_message or "현재 상태에서는 실행할 수 없습니다.",
            )
        elif applied_count:
            success_message = (
                f"조기마감 {applied_count}종목 적용 합니다."
                if command_label == ROUTINE_STATUS_EARLY_CLOSE
                else f"{display_name} {command_label} 요청이 접수되었습니다."
            )
            show_toast(
                self,
                success_message,
                duration_ms=2500,
            )
        else:
            show_toast(
                self,
                f"{command_label} 대상이 없습니다.",
                duration_ms=2500,
            )
        self.statusBar().showMessage(
            f"루틴 {command_label}: {display_name} / 성공 {applied_count} / "
            f"차단 {failed_count}"
        )

    def reflect_routine_completion_result(
        self,
        instance_id: str,
        completion_status: str,
        *,
        data_mismatch: bool = False,
    ) -> bool:
        """Reflect an authoritative backend completion result without inferring it."""
        if data_mismatch:
            self.update_review_required_button_text()
            return False
        if completion_status not in ROUTINE_COMPLETION_STATUSES:
            return False
        if routine_instance_by_id(instance_id) is None:
            return False
        self.load_routine_table()
        return True

    def open_stock_register_window(self) -> None:
        window = getattr(self, "stock_register_window", None)
        if window is not None and not sip.isdeleted(window) and window.isVisible():
            window.show()
            window.raise_()
            window.activateWindow()
            return
        window = StockRegisterWindow(self)
        window.setAttribute(Qt.WA_DeleteOnClose, True)
        self.stock_register_window = window
        window.destroyed.connect(
            lambda _obj=None, target=window: (
                setattr(self, "stock_register_window", None)
                if getattr(self, "stock_register_window", None) is target
                else None
            )
        )
        window.show()
        window.raise_()
        window.activateWindow()

    def open_auto_trade_setting_window(self) -> None:
        window = getattr(self, "auto_trade_setting_window", None)
        if window is not None and sip.isdeleted(window):
            self.auto_trade_setting_window = None
            window = None
        if window is not None and window.isVisible():
            if window.isMinimized():
                window.showNormal()
            else:
                window.show()
            window.raise_()
            window.activateWindow()
            return
        if window is not None and not window.isVisible():
            self.auto_trade_setting_window = None
            window.deleteLater()
            window = None
        if window is None:
            window = AutoTradeSettingWindow(self)
            window.setAttribute(Qt.WA_DeleteOnClose, True)
            self.auto_trade_setting_window = window
            window.destroyed.connect(
                lambda _obj=None, target=window: (
                    setattr(self, "auto_trade_setting_window", None)
                    if getattr(self, "auto_trade_setting_window", None) is target
                    else None
                )
            )
        if window.isMinimized():
            window.showNormal()
        else:
            window.show()
        window.raise_()
        window.activateWindow()

    def main_monitoring_auto_trade_operation_host(self) -> AutoTradeOperationHost:
        """Return the widget-free production operation host for monitoring."""

        host = getattr(self, "_main_monitoring_auto_trade_operation_host", None)
        if host is None:
            host = AutoTradeOperationHost(self)
            self._main_monitoring_auto_trade_operation_host = host
        return host

    def on_kiwoom_raw_chejan_received(self, raw_event: dict[str, object]) -> None:
        self.last_chejan_record_result = handle_kiwoom_raw_chejan_event(
            raw_event,
            {
                "kiwoom_api_live_event": True,
                "live_event_source": "KiwoomApi.raw_chejan_received",
            },
        )
        chejan_result = (
            self.last_chejan_record_result
            if isinstance(self.last_chejan_record_result, dict)
            else {}
        )
        chejan_stage = str(
            chejan_result.get("stage")
            or chejan_result.get("record_stage")
            or ""
        ).strip().upper()
        chejan_failed = chejan_result.get("recorded") is not True
        chejan_execution_id = str(chejan_result.get("execution_id") or "").strip()
        chejan_order_id = str(chejan_result.get("order_id") or "").strip()
        observe_owner_failure_transition(
            self,
            "kiwoom_chejan_normalization",
            active=chejan_failed,
            signature=f"CHEJAN_EVIDENCE_FAILED:{chejan_stage}",
            event_type=(
                "PROCESSING_ERROR"
                if chejan_stage in {"QUEUE_READ", "QUEUE_STRUCTURE"}
                else "INTEGRITY_WARNING"
            ),
            severity="ERROR",
            result="FAILED",
            source="gui_windows.MainWindow.on_kiwoom_raw_chejan_received",
            template_args={"target": "키움 체결 증거"},
            target_type="BROKER_EVIDENCE",
            target_id="kiwoom_chejan",
            target_name="키움 체결 증거",
            reason_code=(
                f"CHEJAN_{chejan_stage}_FAILED"
                if chejan_stage
                else "CHEJAN_EVIDENCE_FAILED"
            ),
            component="kiwoom_chejan",
            operation="normalize_and_record",
            execution_id=chejan_execution_id or None,
            order_id=chejan_order_id or None,
            correlation_id=chejan_execution_id or chejan_order_id or None,
            details={"stage": chejan_stage},
        )
        if (
            self.last_chejan_record_result.get("recorded") is True
            and self.last_chejan_record_result.get("manual_reconciliation_required")
            is not True
            and main_window_buffer_response_integration_ready(self)
        ):
            self.last_buffer_response_coordination_result = (
                coordinate_main_window_buffer_response(
                    self,
                    chejan_result=self.last_chejan_record_result,
                )
            )
            self.last_routine_limit_response_result = (
                evaluate_main_window_routine_limit_after_chejan(
                    self,
                    chejan_result=self.last_chejan_record_result,
                    buffer_result=self.last_buffer_response_coordination_result,
                )
            )
            self.last_stock_limit_response_result = (
                evaluate_main_window_stock_limit_after_chejan(
                    self,
                    chejan_result=self.last_chejan_record_result,
                    higher_priority_result=(
                        self.last_buffer_response_coordination_result
                    ),
                    routine_priority_result=(
                        self.last_routine_limit_response_result
                    ),
                )
            )
        try:
            window = getattr(self, "auto_trade_setting_window", None)
        except RuntimeError:
            window = None
        if window is not None:
            setattr(window, "last_chejan_record_result", self.last_chejan_record_result)

    def _main_exit_warning_required(self, now_dt: datetime | None = None) -> bool:
        """Fail closed when a current-running stock can trade at the current time."""

        try:
            running_targets = list(
                auto_trade_running_registered_operation_targets(self)
            )
        except Exception:
            LOGGER.exception("Main exit current-running projection failed")
            return True
        if not running_targets:
            return False

        current = now_dt or datetime.now()
        for stock_dir, _code, _name in running_targets:
            stock_path = Path(stock_dir)
            config = read_json_dict(stock_path / "config.json")
            state = read_json_dict(stock_path / "state.json")
            if not config or not state:
                return True
            time_status = canonical_stock_trading_time_status(
                config=config,
                state=state,
                now_dt=current,
            )
            if time_status.get("evaluable") is not True:
                return True
            if time_status.get("active") is True:
                return True
        return False

    def _confirm_main_window_exit_if_required(self) -> bool:
        if not self._main_exit_warning_required():
            return True

        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Warning)
        dialog.setWindowTitle("프로그램 종료")
        dialog.setText(
            "운영 중입니다. 지금 종료하면 심각한 손실이 발생할 수 있습니다."
        )
        exit_button = dialog.addButton("종료", QMessageBox.AcceptRole)
        cancel_button = dialog.addButton("취소", QMessageBox.RejectRole)
        dialog.setDefaultButton(cancel_button)
        dialog.setEscapeButton(cancel_button)
        dialog.exec_()
        accepted = dialog.clickedButton() is exit_button
        append_production_event(
            "OPERATOR_SYSTEM_DECISION",
            result="ACCEPTED" if accepted else "CANCELLED",
            source="gui_windows.MainWindow._confirm_main_window_exit_if_required",
            target_type="APPLICATION",
            target_name="메인 관제창",
            details={
                "interaction_type": "CONFIRM",
                "prompt_key": "MAIN_WINDOW_EXIT_WHILE_RUNNING",
                "prompt_title": "프로그램 종료",
                "prompt_summary": "운영 중 프로그램 종료",
                "offered_options": ["종료", "취소"],
                "selected_option": "종료" if accepted else "취소",
            },
        )
        return accepted

    def closeEvent(self, event) -> None:
        """Stop the single operation host only when the main program closes."""
        if not self._confirm_main_window_exit_if_required():
            event.ignore()
            return
        self._main_window_closing = True
        MainWindow._clear_completed_recovery_handoff(self)
        close_persistent_feature_windows(self)
        timer = getattr(self, "_pnl_refresh_timer", None)
        if timer is not None:
            timer.stop()
        host = getattr(self, "_main_monitoring_auto_trade_operation_host", None)
        shutdown = getattr(host, "shutdown", None)
        if callable(shutdown):
            shutdown()
        super().closeEvent(event)
        if event.isAccepted():
            append_owner_event_once(
                self,
                "app_stopped",
                "APP_STOPPED",
                result="COMPLETED",
                source="MainWindow.closeEvent",
                target_type="APPLICATION",
                target_id="kiwoom_auto",
            )

    def open_review_required_window(self) -> None:
        window = getattr(self, "review_required_window", None)
        if window is not None and not sip.isdeleted(window) and window.isVisible():
            refresh = getattr(window, "refresh_review_items", None)
            if callable(refresh):
                refresh()
            window.show()
            window.raise_()
            window.activateWindow()
            return
        window = GlobalReviewRequiredWindow(self)
        window.setAttribute(Qt.WA_DeleteOnClose, True)
        self.review_required_window = window
        window.destroyed.connect(
            lambda _obj=None, target=window: (
                setattr(self, "review_required_window", None)
                if getattr(self, "review_required_window", None) is target
                else None
            )
        )
        window.show()
        window.raise_()
        window.activateWindow()

    def open_event_record_window(self) -> None:
        open_event_record_prototype(self)

    def close_all_persistent_feature_windows(self) -> None:
        """Close persistent feature windows without closing MainWindow."""

        close_persistent_feature_windows(self)
