# -*- coding: utf-8 -*-

"""
gui_windows.py

MASTER_SPEC v1.1 Windows GUI Edition 기준
Windows GUI 창 클래스 정의 파일.

현재 단계:
- 메인 윈도우 안정 버전
- Logical Group Registry 자동 탐색
- __pycache__ 제외
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
from time import monotonic

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
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QFileDialog,
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
from gui_stock_name_tooltip import install_persistent_stock_name_tooltips
from gui_main_footer_status import (
    OPERATOR_FOOTER_PRIORITY_HOLD_MS,
    project_operator_footer_message,
    should_defer_operator_footer_message,
)

LOGGER = logging.getLogger(__name__)

ACCOUNT_MEMOS_SETTINGS_KEY = "ui/account_memos"
ACCOUNT_HISTORY_SETTINGS_KEY = "ui/known_accounts"
TOTAL_BUDGET_ROUNDING_SETTINGS_KEY = "ui/total_budget_digit_alignment"
BUDGET_WARNING_SETTINGS_KEY = "ui/budget_warning_enabled"
START_BUDGET_APPLY_LIMIT_SETTINGS_KEY = "ui/start_budget_apply_limit_checked"
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

    def showEvent(self, event) -> None:
        super().showEvent(event)
        action_badge_size = self.strategy_action_badges["unified"].size()
        self.segment_early_close_label.setFixedSize(action_badge_size)
        self.segment_immediate_liquidation_label.setFixedSize(action_badge_size)

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
    ) -> QLabel:
        badge = QLabel(text)
        badge.setAlignment(Qt.AlignCenter)
        badge.setFixedSize(width, 30)
        color = (
            _RoutineLimitResponseSettingsSurface.ROUTINE_CLOSE_COLOR
            if text == "조기마감"
            else "palette(text)"
        )
        border_color = (
            _RoutineLimitResponseSettingsSurface.ROUTINE_CLOSE_COLOR
            if text == "조기마감"
            else "#b7bcc5"
        )
        badge.setStyleSheet(
            "QLabel {"
            f" color: {color}; border: 1px solid {border_color};"
            " border-radius: 3px; background: palette(base); padding: 0px; }"
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
        if badge.text() != "조기마감":
            badge.setStyleSheet("")
            return
        badge.setStyleSheet(
            "QPushButton {"
            f" color: {self.ROUTINE_CLOSE_COLOR};"
            f" border: 1px solid {self.ROUTINE_CLOSE_COLOR};"
            " border-radius: 3px; background: transparent; }"
            "QPushButton:disabled {"
            " color: #9CA3AF; border-color: #D1D5DB;"
            " background: transparent; }"
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
    update_emergency_button_state as emergency_update_emergency_button_state,
    on_emergency_stop_clicked as emergency_on_emergency_stop_clicked,
)
from gui_main_table_loader import (
    ROUTINE_GROUP_ID_ROLE,
    ROUTINE_GROUP_PATH_ROLE,
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
    ROUTINE_STOCK_NAME_ROLE,
    ROUTINE_STOCK_INITIAL_BUY_ROLE,
    ROUTINE_STOCK_DISPLAY_ROLE,
    ROUTINE_STOCK_METRICS_ROLE,
    ROUTINE_STOCK_PATH_ROLE,
    ROUTINE_STOCK_PROFIT_LED_ROLE,
    ROUTINE_STOCK_TEXT_OFFSET,
    ROUTINE_STOCK_TOOLTIP_DATA_ROLE,
    ROUTINE_STOCK_VALUES_ROLE,
    routine_instance_separator_width,
    routine_aggregate_separator_width,
    routine_instance_grid_columns,
    routine_instance_status_column_left,
    routine_aggregate_slot_lefts,
    routine_aggregate_label_width,
    routine_aggregate_number_slot_width,
    routine_instance_number_widths,
    main_refresh_market_information_only,
    main_refresh_pnl_only,
    build_main_refresh_read_context,
    main_group_instance_relation_id,
    main_stock_configuration_market_information_state,
    main_stock_fresh_market_information_state,
    main_stock_resolved_starting_budget,
    routine_instance_suggested_buy_limits,
    routine_stock_column_widths,
    routine_stock_position_value_widths,
    ROUTINE_STATUS_DEFAULT,
    ROUTINE_STATUS_EARLY_CLOSE,
    ROUTINE_STATUS_IMMEDIATE_LIQUIDATION,
    main_sort_routine_table_by_column,
    main_sort_running_table_by_column,
    main_load_routine_table,
    main_load_running_stock_table,
    main_monitoring_table_font,
    main_monitoring_cell_font,
    main_stock_row_tooltip_from_projection,
    routine_instance_consumed_text,
    stock_buy_limit_state,
)
from routine_tree_title_display import tree_title_text
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
    execute_main_monitoring_selective_start,
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
    OperationStartCommandRequest,
    OperationStartIntent,
    auto_trade_registered_operation_targets,
    auto_trade_running_registered_operation_targets,
    auto_trade_update_global_operation_button_state,
    execute_operation_start_command,
    show_auto_trade_operation_failure_dialog,
)
from gui_auto_trade_close import auto_trade_apply_selected_early_close
from gui_operation_ui_context import (
    refresh_auto_trade_views,
    sync_auto_trade_monitoring_universe,
)
from gui_auto_trade_status_ops import (
    auto_trade_set_stock_operation_exclusion,
    handle_auto_trade_operation_mode_double_click,
)
from gui_routine_policy import can_unassign_active_routine_from_stock
from group_complete_deletion_service import (
    collect_group_deletion_scope,
    delete_group_completely,
)
from group_pack_registration import register_group_pack
from group_pack_packing import pack_group
from gui_auto_trade_policy import (
    auto_trade_current_session_operation_participant_codes,
    auto_trade_start_budget_current_running,
    auto_trade_start_budget_mutation_decision,
    operation_policy_section,
)
from gui_review_utils import safe_float_value
from gui_common_utils import safe_int_value
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
    clear_pending_stock_instance_chart_refreshes,
    open_stock_instance_chart,
    queue_open_stock_instance_chart_refresh,
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
from gui_operation_environment import (
    default_buffer_response_policy,
    floor_money_to_won,
    read_buffer_response_policy,
    read_system_budget_policy,
    starting_budget_defaults,
    stock_limit_digit_alignment_enabled,
    suggested_buy_limit,
    validate_buffer_response_policy,
    write_buffer_response_policy,
)
from running_budget_adjustment import (
    commit_running_budget_adjustment,
    project_running_budget_adjustment_display_config,
)
from budget_command import (
    BudgetModeChangeRequest,
    BudgetValueChangeRequest,
    CURRENT_PRICE_UNAVAILABLE,
    execute_budget_mode_change,
    inspect_budget_value_entry,
)
from gui_auto_trade_setting_window import (
    AUTO_TRADE_SETTING_BADGE_ACTIVE_COLOR,
    AUTO_TRADE_SETTING_BADGE_INACTIVE_COLOR,
    AUTO_TRADE_SETTING_EARLY_CLOSE_BUTTON_STYLE,
    AUTO_TRADE_SETTING_TOP_CONTROL_ROW_HEIGHT,
    AutoTradeSettingWindow,
    auto_trade_setting_badge_stylesheet,
    clone_routine_instance_with_existing_policy,
    delete_routine_instance_with_existing_policy,
    get_group_dirs,
    get_stock_dirs_in_routine,
    handle_stock_name_operation_exclusion_double_click,
    handle_kiwoom_raw_chejan_event,
    is_emergency_stopped_state,
    is_review_required_state,
    normalize_base_stock_single_routine_file,
    open_instance_stock_search_register_dialog,
    open_routine_settings_dialog_for_owner,
)
from gui_routine_registry import (
    group_record_by_id,
    routine_record_by_name,
)
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
from routine_package_contract import (
    SETTINGS_ROLE,
    RoutineContractError,
    load_routine_callable,
)
from stock_repository import (
    STOCK_CONFIG_EXPECTED_MISSING,
    STOCK_CONFIG_WRITE_CONCURRENT_UPDATE_RETRY_EXHAUSTED,
    STOCK_CONFIG_WRITE_FIELD_CONFLICT,
    STOCK_CONFIG_WRITE_INVALID_STOCK_IDENTITY,
    StockConfigWriteResult,
    StockRepository,
    now_text as stock_now_text,
)
from stock_repository import StockRepository as CanonicalStockConfigRepository
from stock_buy_limit_provenance import (
    BUY_LIMIT_SOURCE_MANUAL,
    BUY_LIMIT_SOURCE_RECOMMENDED,
    canonical_stock_buy_limit_values,
)
from gui_main_routine_selection import (
    routine_definition_enabled,
    routine_instance_checked,
)
from kiwoom_api import KiwoomApi
from kiwoom_stock_library_service import KiwoomStockLibrarySyncService
from stock_library_diagnostics_retention import (
    StockLibraryDiagnosticsAutomaticRetention,
)
from operator_reconciliation_service import assess_startup_recovery
from assignment_startup_reconciliation_service import (
    apply_assignment_reconciliation_to_production_registry,
    reconcile_assignment_startup,
)
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
MAIN_MARKET_INFORMATION_REFRESH_INTERVAL_MS = 100
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


def _routine_stock_metric_texts(
    values: list[object],
    metrics_data: tuple[object, ...],
    *,
    include_consumed: bool = False,
) -> list[str]:
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
    elif include_consumed:
        consumed_amount = getattr(metrics_data[0], "value2", 0) if metrics_data else 0
        texts.append(
            routine_instance_consumed_text(
                consumed_amount=consumed_amount,
                buy_limit_enabled=False,
                buy_limit_amount=None,
            )
        )
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

    def _set_parent_name_hover(self, group_id: str) -> None:
        current = str(
            getattr(self.table, "_hovered_main_group_id", "") or ""
        )
        if group_id == current:
            return
        self.table._hovered_main_group_id = group_id
        self.table.viewport().update()

    def _parent_name_rect(self, index) -> QRect:
        cell_rect = self.table.visualRect(index)
        name = tree_title_text(index.data(ROUTINE_PARENT_NAME_ROLE))
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
            group_id = ""
            if (
                index.isValid()
                and index.column() == 0
                and str(index.data(ROUTINE_ROW_KIND_ROLE) or "") == ROUTINE_ROW_PARENT
                and self._parent_name_rect(index).contains(event.pos())
            ):
                group_id = str(
                    index.data(ROUTINE_GROUP_ID_ROLE) or ""
                ).strip()
            self._set_parent_name_hover(group_id)
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
                        if limit_rect.contains(event.pos()):
                            self.window.cancel_routine_stock_buy_limit_single_click(
                                suppress_release_row=index.row(),
                            )
                            self.window.handle_routine_stock_buy_limit_double_click(index.row())
                            event.accept()
                            return True
                        if not self.window._main_routine_initial_buy_badge_enabled():
                            return super().eventFilter(watched, event)
                        initial_buy_rect = self._stock_legacy_metric_rect(index, 1)
                        initial_buy_parts = _initial_buy_component_rects(initial_buy_rect)
                        if initial_buy_parts["badge"].contains(event.pos()):
                            self.window.toggle_routine_stock_initial_buy_mode(index.row())
                            event.accept()
                            return True
                        if initial_buy_parts["value"].contains(event.pos()):
                            self.window.open_routine_stock_initial_buy_dialog(index.row())
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
        return super().eventFilter(watched, event)


class _ClippedTextItemDelegate(QStyledItemDelegate):
    """Clip overflowing text at the cell edge without drawing an ellipsis."""

    def paint(self, painter, option, index) -> None:
        clipped_option = QStyleOptionViewItem(option)
        self.initStyleOption(clipped_option, index)
        style = option.widget.style() if option.widget is not None else QApplication.style()
        text = clipped_option.text
        text_rect = style.subElementRect(
            QStyle.SE_ItemViewItemText,
            clipped_option,
            option.widget,
        )
        clipped_option.text = ""
        style.drawControl(QStyle.CE_ItemViewItem, clipped_option, painter, option.widget)
        painter.save()
        painter.setClipRect(text_rect, Qt.IntersectClip)
        painter.setFont(option.font)
        painter.setPen(
            clipped_option.palette.highlightedText().color()
            if option.state & QStyle.State_Selected
            else clipped_option.palette.text().color()
        )
        painter.drawText(text_rect, int(clipped_option.displayAlignment), text)
        painter.restore()


class _RoutineTreeItemDelegate(QStyledItemDelegate):
    """Paint the first-column hierarchy without text-based indentation."""

    @staticmethod
    def _child_name_text_rect(row_rect: QRect, left_offset: int) -> QRect:
        left = row_rect.left() + left_offset
        return QRect(
            left,
            row_rect.top(),
            max(0, ROUTINE_INSTANCE_NAME_WIDTH - left_offset - 4),
            row_rect.height(),
        )

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
        group_id = str(index.data(ROUTINE_GROUP_ID_ROLE) or "")
        aggregate = str(index.data(ROUTINE_PARENT_AGGREGATE_ROLE) or "")
        collapsed = bool(index.data(ROUTINE_PARENT_COLLAPSED_ROLE))
        hovered = str(
            getattr(widget, "_hovered_main_group_id", "") or ""
        )
        if aggregate and (collapsed or group_id == hovered):
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
                        painter.save()
                        painter.setClipRect(remainder_rect, Qt.IntersectClip)
                        painter.drawText(
                            remainder_rect,
                            Qt.AlignLeft | Qt.AlignVCenter,
                            remainder,
                        )
                        painter.restore()
                    else:
                        painter.setFont(token_font)
                        painter.setPen(token_pen)
                        painter.save()
                        painter.setClipRect(text_rect, Qt.IntersectClip)
                        painter.drawText(
                            text_rect,
                            Qt.AlignLeft | Qt.AlignVCenter,
                            text,
                        )
                        painter.restore()
                    continue
                if column == 1:
                    initial_buy = index.data(ROUTINE_STOCK_INITIAL_BUY_ROLE)
                    if not isinstance(initial_buy, dict):
                        initial_buy = {}
                    _draw_initial_buy_display(
                        painter,
                        cell_rect,
                        initial_buy,
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
                        metric_texts = _routine_stock_metric_texts(
                            list(values),
                            tuple(metrics_data),
                            include_consumed=bool(
                                getattr(
                                    option.widget,
                                    "_main_stock_limit_expanded",
                                    False,
                                )
                            ),
                        )
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
        if row_kind == ROUTINE_ROW_CHILD:
            text_rect = self._child_name_text_rect(option.rect, text_left_offset)
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


class _MarketDataMonitoringWindow(QDialog):
    """Read-only process-local Realtime and TR Governor diagnostics."""

    REFRESH_INTERVAL_MS = 750
    INITIAL_WIDTH = 620
    INITIAL_HEIGHT = 680
    MINIMUM_WIDTH = 550
    MINIMUM_HEIGHT = 580
    REALTIME_ROWS = (
        ("broker_connected", "Broker 연결"),
        ("connection_epoch", "Connection Epoch"),
        ("login_session_id", "Login Session"),
        ("registration_active", "Realtime 등록"),
        ("target_stock_count", "대상 종목"),
        ("received_tick_count", "Tick 수신"),
        ("processed_tick_count", "Tick 처리"),
        ("tick_rate", "Tick/sec (관측)"),
        ("queue", "Queue (현재 / 최대)"),
        ("overflow_count", "Overflow"),
        ("sequence", "Sequence (수신 / 처리)"),
        ("latency", "처리 Latency (최근 / 최대)"),
        ("data_quality", "Data Quality"),
        ("last_received_at", "최근 Tick 수신"),
        ("last_processed_at", "최근 Tick 처리"),
    )
    TR_ROWS = (
        ("total_enqueued", "누적 Enqueue"),
        ("total_dispatched", "누적 Dispatch"),
        ("current_queue_depth", "현재 Queue"),
        ("last_rqname", "최근 RQName"),
        ("last_trcode", "최근 TRCode"),
        ("last_dispatch_monotonic", "최근 Dispatch (monotonic ms)"),
        ("dispatch_count_last_60s", "최근 60초 Dispatch"),
        ("queue_wait", "Queue Wait (최근 / 최대)"),
        ("timeout_count", "Timeout"),
        ("stale_count", "Stale"),
        ("error_count", "Error"),
        ("last_error_reason", "최근 Error"),
    )

    def __init__(self, parent: QWidget, operation_host: object) -> None:
        super().__init__(parent)
        self._operation_host = operation_host
        self._last_tick_rate_count: int | None = None
        self._last_tick_rate_monotonic: float | None = None
        self._value_labels: dict[str, QLabel] = {}
        self._full_value_texts: dict[str, str] = {}
        self._row_labels: dict[str, tuple[QLabel, QLabel]] = {}
        self._row_minimum_height = self.fontMetrics().height() + 3
        self.setObjectName("marketDataMonitoringWindow")
        self.setWindowTitle("Realtime / TR 모니터링")

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        root.addWidget(self._create_section("Realtime", self.REALTIME_ROWS))
        root.addWidget(self._create_section("TR Governor", self.TR_ROWS))
        compact_minimum_height = max(
            self.MINIMUM_HEIGHT,
            self.minimumSizeHint().height(),
        )
        self.setMinimumSize(self.MINIMUM_WIDTH, compact_minimum_height)
        self.resize(
            self.INITIAL_WIDTH,
            max(self.INITIAL_HEIGHT, compact_minimum_height),
        )

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(self.REFRESH_INTERVAL_MS)
        self._refresh_timer.timeout.connect(self.refresh_snapshot)
        self.refresh_snapshot()
        self._refresh_timer.start()

    def _create_section(
        self,
        title: str,
        rows: tuple[tuple[str, str], ...],
    ) -> QGroupBox:
        box = QGroupBox(title)
        layout = QGridLayout(box)
        metrics = box.fontMetrics()
        vertical_spacing = 0
        layout.setContentsMargins(8, metrics.height(), 8, 4)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(vertical_spacing)
        for row, (key, caption) in enumerate(rows):
            caption_label = QLabel(caption)
            value_label = QLabel("-")
            caption_label.setMinimumHeight(self._row_minimum_height)
            value_label.setMinimumHeight(self._row_minimum_height)
            caption_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
            value_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            layout.addWidget(caption_label, row, 0, Qt.AlignLeft | Qt.AlignVCenter)
            layout.addWidget(value_label, row, 1, Qt.AlignLeft | Qt.AlignVCenter)
            self._value_labels[key] = value_label
            self._row_labels[key] = (caption_label, value_label)
        layout.setColumnStretch(1, 1)
        return box

    @staticmethod
    def _display(value: object) -> str:
        if value is None or str(value).strip() == "":
            return "-"
        return str(value)

    def value_text(self, key: str) -> str:
        label = self._value_labels.get(str(key))
        return label.text() if label is not None else ""

    def _set_value(self, key: str, value: object) -> None:
        label = self._value_labels.get(key)
        if label is not None:
            self._full_value_texts[key] = self._display(value)
            self._update_compact_value_label(key)

    def _update_compact_value_label(self, key: str) -> None:
        label = self._value_labels.get(key)
        if label is None:
            return
        full_text = self._full_value_texts.get(key, "-")
        available_width = max(0, label.contentsRect().width())
        visible_text = label.fontMetrics().elidedText(
            full_text,
            Qt.ElideRight,
            available_width,
        )
        label.setText(visible_text)
        label.setToolTip(full_text if visible_text != full_text else "")

    def _refresh_compact_value_labels(self) -> None:
        for key in self._value_labels:
            self._update_compact_value_label(key)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh_compact_value_labels()

    def refresh_snapshot(self, now_monotonic: float | None = None) -> None:
        now = monotonic() if now_monotonic is None else float(now_monotonic)
        market_getter = getattr(
            self._operation_host,
            "high_resolution_market_data_snapshot",
            None,
        )
        tr_getter = getattr(
            self._operation_host,
            "tr_governor_metrics_snapshot",
            None,
        )
        try:
            market = market_getter() if callable(market_getter) else None
        except Exception:
            market = None
        try:
            tr_metrics = tr_getter() if callable(tr_getter) else None
        except Exception:
            tr_metrics = None
        received_count = int(getattr(market, "received_tick_count", 0) or 0)
        tick_rate = "-"
        if (
            self._last_tick_rate_count is not None
            and self._last_tick_rate_monotonic is not None
        ):
            elapsed = now - self._last_tick_rate_monotonic
            if elapsed > 0:
                delta = max(0, received_count - self._last_tick_rate_count)
                tick_rate = f"{delta / elapsed:.1f}"
        self._last_tick_rate_count = received_count
        self._last_tick_rate_monotonic = now

        self._set_value("broker_connected", "연결" if bool(getattr(market, "broker_connected", False)) else "미연결")
        self._set_value("connection_epoch", getattr(market, "connection_epoch", None))
        self._set_value("login_session_id", getattr(market, "login_session_id", None))
        self._set_value("registration_active", "활성" if bool(getattr(market, "realtime_registration_active", False)) else "비활성")
        self._set_value("target_stock_count", getattr(market, "realtime_target_stock_count", None))
        self._set_value("received_tick_count", received_count)
        self._set_value("processed_tick_count", getattr(market, "processed_tick_count", None))
        self._set_value("tick_rate", tick_rate)
        self._set_value(
            "queue",
            f"{int(getattr(market, 'current_queue_depth', 0) or 0)} / "
            f"{int(getattr(market, 'queue_high_watermark', 0) or 0)}",
        )
        self._set_value("overflow_count", getattr(market, "overflow_count", None))
        self._set_value(
            "sequence",
            f"{int(getattr(market, 'last_receive_sequence', 0) or 0)} / "
            f"{int(getattr(market, 'last_processed_sequence', 0) or 0)}",
        )
        self._set_value(
            "latency",
            f"{float(getattr(market, 'last_processing_latency_ms', 0.0) or 0.0):.3f} ms / "
            f"{float(getattr(market, 'max_processing_latency_ms', 0.0) or 0.0):.3f} ms",
        )
        self._set_value("data_quality", getattr(market, "data_quality", None))
        self._set_value("last_received_at", getattr(market, "last_tick_received_at", None))
        self._set_value("last_processed_at", getattr(market, "last_tick_processed_at", None))

        for key in (
            "total_enqueued",
            "total_dispatched",
            "current_queue_depth",
            "last_rqname",
            "last_trcode",
            "last_dispatch_monotonic",
            "dispatch_count_last_60s",
            "timeout_count",
            "stale_count",
            "error_count",
            "last_error_reason",
        ):
            self._set_value(key, getattr(tr_metrics, key, None))
        self._set_value(
            "queue_wait",
            f"{float(getattr(tr_metrics, 'last_queue_wait_ms', 0.0) or 0.0):.3f} ms / "
            f"{float(getattr(tr_metrics, 'max_queue_wait_ms', 0.0) or 0.0):.3f} ms",
        )

    def closeEvent(self, event) -> None:
        self._refresh_timer.stop()
        super().closeEvent(event)


class RunningBudgetAdjustmentDialog(QDialog):
    """Shared editor for a stock's base starting budget."""

    def __init__(
        self,
        owner: "MainWindow",
        *,
        stock_code: str,
        stock_name: str,
        current_price: object,
        config: dict[str, object],
        minimum_amount: object = None,
        pending_adjustment: dict[str, object] | None = None,
        timing_selection_enabled: bool = True,
        configuration_price_available: bool | None = None,
        apply_limit_checked: bool = False,
    ) -> None:
        super().__init__(owner)
        self.stock_code = str(stock_code or "").strip()
        self.stock_name = str(stock_name or "").strip()
        resolved_current_price = safe_float_value(current_price, 0.0)
        self.configuration_price_available = (
            resolved_current_price > 0
            if configuration_price_available is None
            else bool(configuration_price_available)
        )
        self.current_price = (
            resolved_current_price if self.configuration_price_available else 0.0
        )
        self.config = config if isinstance(config, dict) else {}
        resolved_minimum_amount = safe_int_value(minimum_amount, 0)
        self.minimum_amount = (
            resolved_minimum_amount if resolved_minimum_amount > 0 else None
        )
        self.pending_adjustment = (
            dict(pending_adjustment)
            if isinstance(pending_adjustment, dict)
            else None
        )
        self.timing_selection_enabled = bool(timing_selection_enabled)
        self.mode = (
            "AMOUNT"
            if str(self.config.get("trade_amount_type", "QUANTITY")).upper()
            == "AMOUNT"
            else "QUANTITY"
        )
        self.current_value = self._config_value(self.mode)
        self.requested_at = stock_now_text()
        self.result: dict[str, object] = {}

        display_name = self.stock_name.split("|", 1)[0].strip()
        if self.stock_code and display_name.startswith(self.stock_code):
            display_name = display_name[len(self.stock_code) :].lstrip(" -")
        self.display_stock_name = display_name
        stock_identity = f"{self.stock_code} {display_name}".strip()
        self.setWindowTitle(f"기본예산변경 | {stock_identity}".strip())
        self.setModal(True)
        self.setMinimumWidth(480)
        self.resize(520, 210)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 12, 20, 12)
        root.setSpacing(0)
        root.setAlignment(Qt.AlignTop)

        self.current_price_label = QLabel(self._current_price_text())
        self.current_price_label.setAlignment(Qt.AlignCenter)
        current_price_font = self.current_price_label.font()
        current_price_font.setPointSizeF(
            max(1.0, current_price_font.pointSizeF() * 2.0)
        )
        self.current_price_label.setFont(current_price_font)
        root.addWidget(self.current_price_label)
        root.addSpacing(9)

        current_text = self._value_text(self.current_value)
        mode_text = "금액" if self.mode == "AMOUNT" else "주수"
        current_badge = QLabel(mode_text)
        current_badge.setAlignment(Qt.AlignCenter)
        current_badge.setFixedSize(
            INITIAL_BUY_BADGE_WIDTH,
            INITIAL_BUY_BADGE_HEIGHT,
        )
        current_badge.setFont(_initial_buy_badge_font())
        badge_color = (
            INITIAL_BUY_AMOUNT_COLOR
            if self.mode == "AMOUNT"
            else INITIAL_BUY_QUANTITY_COLOR
        )
        current_badge.setStyleSheet(
            "QLabel {"
            f"color: {badge_color};"
            f"border: 1px solid {badge_color};"
            "border-radius: 4px;"
            "background: transparent;"
            "padding: 0px;"
            "}"
        )
        self.current_badge = current_badge

        current_label = QLabel(current_text)
        current_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.current_reference_label = QLabel()
        self.current_reference_label.setText(self._reference_text(self.current_value))
        current_box = QHBoxLayout()
        current_box.setContentsMargins(0, 0, 0, 0)
        current_box.setSpacing(4)
        current_box.addWidget(current_label)
        current_box.addWidget(QLabel("/"))
        current_box.addWidget(self.current_reference_label)

        self.value_edit = QLineEdit()
        self.value_edit.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.value_edit.setPlaceholderText("숫자 입력")
        self.value_edit.setValidator(
            QRegularExpressionValidator(
                QRegularExpression(
                    r"^(?=(?:[^0-9]*[0-9]){0,8}[^0-9]*$)[0-9,]{0,10}$"
                    if self.mode == "AMOUNT"
                    else r"[0-9]{0,8}"
                ),
                self.value_edit,
            )
        )
        self.value_edit.setStyleSheet(
            "QLineEdit {"
            "border: 1px solid #CBD5E1;"
            "border-radius: 3px;"
            "background: #FFFFFF;"
            "padding: 1px 5px;"
            "}"
            "QLineEdit:focus {"
            "border: 1px solid #CBD5E1;"
            "outline: none;"
            "}"
        )
        input_width_text = "00,000,000" if self.mode == "AMOUNT" else "0000"
        self.value_edit.setFixedWidth(
            QFontMetrics(self.value_edit.font()).horizontalAdvance(input_width_text)
            + 24
        )
        initial_value_text = (
            f"{self.current_value:,}"
            if self.mode == "AMOUNT" and self.current_value > 0
            else str(self.current_value) if self.current_value > 0 else ""
        )
        self.value_edit.setText(initial_value_text)
        self._last_valid_value_text = initial_value_text
        self.value_edit.textChanged.connect(self._on_value_text_changed)
        input_box = QWidget()
        input_layout = QHBoxLayout(input_box)
        input_layout.setContentsMargins(0, 0, 0, 0)
        input_layout.setSpacing(2)
        input_layout.addWidget(self.value_edit)
        input_layout.addWidget(QLabel(self._unit_text()))

        self.changed_reference_label = QLabel()
        budget_row = QHBoxLayout()
        budget_row.setContentsMargins(0, 0, 0, 0)
        budget_row.setSpacing(6)
        budget_row.addStretch(1)
        budget_row.addWidget(current_badge)
        budget_row.addLayout(current_box)
        budget_row.addWidget(QLabel("▷"))
        budget_row.addWidget(input_box)
        budget_row.addWidget(self.changed_reference_label)
        budget_row.addStretch(1)
        root.addLayout(budget_row)
        root.addSpacing(10)

        checkbox_indent = 24 + QFontMetrics(self.font()).horizontalAdvance("한")
        timing_row = QHBoxLayout()
        timing_row.setContentsMargins(checkbox_indent, 0, 0, 0)
        self.immediate_checkbox = QCheckBox("즉시적용")
        self.next_cycle_checkbox = QCheckBox("다음회차적용")
        timing_group = QButtonGroup(self)
        timing_group.setExclusive(True)
        timing_group.addButton(self.immediate_checkbox)
        timing_group.addButton(self.next_cycle_checkbox)
        pending_policy = (
            str(self.pending_adjustment.get("apply_policy") or "").strip().upper()
            if self.pending_adjustment is not None
            else ""
        )
        self.immediate_checkbox.setChecked(
            self.timing_selection_enabled and pending_policy != "NEXT_CYCLE"
        )
        self.next_cycle_checkbox.setChecked(
            self.timing_selection_enabled and pending_policy == "NEXT_CYCLE"
        )
        self.immediate_checkbox.setEnabled(self.timing_selection_enabled)
        self.next_cycle_checkbox.setEnabled(self.timing_selection_enabled)
        timing_row.setSpacing(36)
        timing_row.addWidget(self.immediate_checkbox)
        timing_row.addWidget(self.next_cycle_checkbox)
        timing_row.addStretch(1)
        root.addLayout(timing_row)
        root.addSpacing(9)

        self.apply_limit_checkbox = QCheckBox("한도금액에 새 설정값 적용")
        self.apply_limit_checkbox.setChecked(bool(apply_limit_checked))
        limit_row = QHBoxLayout()
        limit_row.setContentsMargins(checkbox_indent, 0, 0, 0)
        limit_row.addWidget(self.apply_limit_checkbox)
        limit_row.addStretch(1)
        root.addLayout(limit_row)
        root.addSpacing(9)

        self.validation_label = QLabel()
        self.validation_label.setStyleSheet("color: #B91C1C;")
        self.validation_label.setAlignment(Qt.AlignCenter)
        self.validation_label.hide()
        root.addWidget(self.validation_label)
        root.addSpacing(8)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("확인")
        buttons.button(QDialogButtonBox.Cancel).setText("취소")
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._refresh_preview(validate=False)

    def _config_value(self, mode: str) -> int:
        key = "buy_amount" if mode == "AMOUNT" else "buy_qty"
        try:
            return max(0, int(self.config.get(key) or 0))
        except (TypeError, ValueError):
            return 0

    def _value_text(self, value: int) -> str:
        unit = "원" if self.mode == "AMOUNT" else "주"
        return f"{value:,}{unit}"

    def _current_price_text(self) -> str:
        if self.current_price <= 0:
            return "현재가 -"
        return f"현재가 {self.current_price:,.0f}원"

    def _unit_text(self) -> str:
        return "원" if self.mode == "AMOUNT" else "주"

    def _reference_text(self, value: int) -> str:
        if value <= 0 or self.current_price <= 0:
            return "-"
        if self.mode == "AMOUNT":
            shares = value / self.current_price
            return f"{shares:.1f}주"
        return f"{value * self.current_price:,.0f}원"

    def _input_value(self) -> int:
        raw = str(self.value_edit.text() or "")
        digits = "".join(character for character in raw if character.isdigit())
        try:
            return int(digits or 0)
        except ValueError:
            return 0

    def _on_value_text_changed(self, text: str) -> None:
        raw_text = str(text)
        raw_digits = "".join(character for character in raw_text if character.isdigit())
        if len(raw_digits) > 8:
            formatted = self._last_valid_value_text
        elif self.mode == "AMOUNT":
            formatted = f"{int(raw_digits):,}" if raw_digits else ""
        else:
            formatted = raw_digits
        if formatted != text:
            cursor_digits = sum(
                character.isdigit()
                for character in str(text)[: self.value_edit.cursorPosition()]
            )
            signals_were_blocked = self.value_edit.blockSignals(True)
            try:
                self.value_edit.setText(formatted)
                cursor_position = 0
                digits_seen = 0
                while cursor_position < len(formatted) and digits_seen < cursor_digits:
                    if formatted[cursor_position].isdigit():
                        digits_seen += 1
                    cursor_position += 1
                self.value_edit.setCursorPosition(cursor_position)
            finally:
                self.value_edit.blockSignals(signals_were_blocked)
        self._last_valid_value_text = formatted
        self._refresh_preview()

    def _refresh_preview(self, *, validate: bool = True) -> None:
        value = self._input_value()
        reference = self._reference_text(value)
        self.changed_reference_label.setText(f"/ {reference}")
        if validate:
            self._refresh_validation_message(value)

    def _hide_validation_message(self) -> None:
        self.validation_label.clear()
        self.validation_label.hide()

    def _show_validation_message(self, message: str) -> None:
        self.validation_label.setText(str(message or ""))
        self.validation_label.show()

    def _show_minimum_amount_error(self) -> None:
        if self.minimum_amount is None:
            self._show_validation_message("시작예산 금액을 확인하세요.")
            return
        self._show_validation_message(
            f"※ 입력 불가. 최소값은 {self.minimum_amount:,}원 이상입니다."
        )

    def _refresh_validation_message(self, value: int) -> None:
        if (
            self.mode == "AMOUNT"
            and self.minimum_amount is not None
            and value > 0
            and value < self.minimum_amount
        ):
            self._show_minimum_amount_error()
            return
        self._hide_validation_message()

    def _validate_and_accept(self) -> None:
        value = self._input_value()
        if value <= 0:
            self._show_validation_message("변경값을 입력하세요.")
            return
        if not self.configuration_price_available or self.current_price <= 0:
            self._show_validation_message(
                MainWindow._start_budget_price_unavailable_message()
            )
            return
        if value > 99_999_999:
            self._show_validation_message("99,999,999까지 입력할 수 있습니다.")
            return
        if self.mode == "AMOUNT":
            if self.minimum_amount is None:
                self._show_minimum_amount_error()
                return
            if value < self.minimum_amount:
                self._show_minimum_amount_error()
                return
        if self.timing_selection_enabled and not (
            self.immediate_checkbox.isChecked()
            or self.next_cycle_checkbox.isChecked()
        ):
            self._show_validation_message("적용 시점을 선택하세요.")
            return
        self._hide_validation_message()
        self.result = {
            "mode": self.mode,
            "value": value,
            "apply_timing": (
                "PRE_OPERATION"
                if not self.timing_selection_enabled
                else "IMMEDIATE"
                if self.immediate_checkbox.isChecked()
                else "NEXT_CYCLE"
            ),
            "apply_limit": self.apply_limit_checkbox.isChecked(),
        }
        self.accept()


class MainWindow(QMainWindow):
    """
    키움 자동매매 시스템 메인 윈도우
    """

    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("키움 OpenAPI 자동매매 시스템 - v1.1 Windows GUI")
        self.setMinimumWidth(1680)
        self.resize(self.minimumWidth(), 720)
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
        self.stock_library_sync_service = (
            KiwoomStockLibrarySyncService(self.kiwoom_api, parent=self)
            if self.kiwoom_api is not None
            else None
        )
        self.stock_library_diagnostics_retention = (
            StockLibraryDiagnosticsAutomaticRetention(
                PROJECT_ROOT / "runtime" / "diagnostics",
                event_writer=append_production_event,
            )
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
        self._collapsed_main_group_ids: set[str] = set()
        self._collapsed_main_group_instance_ids: set[str] = set()
        self._routine_definition_enabled: dict[str, bool] = {}
        self._routine_instance_selection: dict[str, bool] = {}
        self._routine_stock_selection: dict[str, bool] = {}
        self._routine_instance_ids_by_definition: dict[str, tuple[str, ...]] = {}
        self._routine_instance_ids_by_group: dict[str, tuple[str, ...]] = {}
        self._routine_group_records_by_id: dict[str, object] = {}
        self._routine_stock_paths_by_group: dict[str, tuple[str, ...]] = {}
        self._routine_stock_paths_by_group_instance: dict[str, tuple[str, ...]] = {}
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
        self._routine_stock_buy_limit_editor_expected_fields = None
        self._routine_stock_buy_limit_edit_finishing = False
        self._routine_stock_buy_limit_pending_path = ""
        self._routine_stock_buy_limit_suppressed_release_row = -1
        self._routine_stock_buy_limit_click_timer = QTimer(self)
        self._routine_stock_buy_limit_click_timer.setSingleShot(True)
        self._routine_stock_buy_limit_click_timer.timeout.connect(
            self._execute_routine_stock_buy_limit_single_click
        )
        self.routine_table._editing_stock_buy_limit_path = ""
        self._main_running_sort_column = -1
        self._main_running_sort_order = Qt.AscendingOrder
        self._startup_recovery_result: dict[str, object] = {}
        self._assignment_startup_reconciliation_result: dict[str, object] = {}
        self._assignment_production_registry_result: dict[str, object] = {}
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
        self.btn_review_required = QPushButton()
        self.btn_market_data_monitoring = QPushButton("모니터링")
        self.btn_group_pack_register = QPushButton("그룹등록")
        self.btn_main_visible_early_close = QPushButton("조기마감")
        self.btn_exit = QPushButton("종료")
        self.btn_emergency_stop = _DoubleClickActionButton("긴급정지")

        self._setup_ui()
        self._apply_main_control_window_width()
        self._connect_events()
        operation_host = self.main_monitoring_auto_trade_operation_host()
        self._pending_main_market_information_codes: set[str] = set()
        self._main_market_information_refresh_timer = QTimer(self)
        self._main_market_information_refresh_timer.setSingleShot(True)
        self._main_market_information_refresh_timer.setInterval(
            MAIN_MARKET_INFORMATION_REFRESH_INTERVAL_MS
        )
        self._main_market_information_refresh_timer.timeout.connect(
            self._refresh_main_market_information_rows
        )
        market_host_getter = getattr(operation_host, "market_data_host", None)
        market_host = market_host_getter() if callable(market_host_getter) else None
        market_data_observed = getattr(market_host, "market_data_observed", None)
        if market_data_observed is not None and callable(
            getattr(market_data_observed, "connect", None)
        ):
            market_data_observed.connect(self._on_main_market_data_observed)
        operation_host.operation_cycle_completed.connect(
            self._on_main_operation_cycle_completed
        )
        normalize_base_stock_single_routine_file()
        self._assignment_startup_reconciliation_result = (
            reconcile_assignment_startup(PROJECT_ROOT)
        )
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
        self._main_dashboard_layout = main_layout
        self._apply_main_dashboard_style(central)

        self._bind_main_status_message_to_button_row()
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
        MainWindow._style_main_top_action_buttons(self)
        routine_header_layout.addWidget(
            self.btn_market_data_monitoring,
            0,
            Qt.AlignRight | Qt.AlignVCenter,
        )
        routine_header_layout.addWidget(
            self.btn_group_pack_register,
            0,
            Qt.AlignRight | Qt.AlignVCenter,
        )
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
        self._main_table_area_layout = layout
        self._main_routine_layout = routine_layout
        self._main_routine_content_layout = routine_content_layout
        self._main_routine_filter_badge_area = routine_filter_badge_area

        self._setup_running_stock_table()
        self.running_stock_table.setVisible(False)

        layout.addWidget(routine_box, 1)

        return layout

    @staticmethod
    def _layout_horizontal_margins(layout) -> int:
        margins = layout.contentsMargins()
        return int(margins.left()) + int(margins.right())

    def _main_control_window_required_width(self) -> int:
        table = self.routine_table
        font = table.font()
        metrics = QFontMetrics(font)
        column_widths = routine_stock_column_widths(font)
        base_column_count = 7
        base_metric_left = (
            ROUTINE_STOCK_TEXT_OFFSET
            + sum(column_widths[:base_column_count])
            + routine_instance_separator_width(font) * (base_column_count - 1)
        )
        metric_slot_widths = _main_stock_metric_slot_widths(metrics)
        metric_separator_width = max(1, metrics.horizontalAdvance("|"))
        row_content_width = (
            base_metric_left
            + ROUTINE_STOCK_METRIC_SEPARATOR_GAP
            + sum(metric_slot_widths)
            + max(0, len(metric_slot_widths) - 1)
            * (
                ROUTINE_STOCK_METRIC_SEPARATOR_GAP * 2
                + metric_separator_width
            )
        )
        scrollbar_extent = max(
            table.verticalScrollBar().sizeHint().width(),
            table.style().pixelMetric(QStyle.PM_ScrollBarExtent, None, table),
        )
        row_right_margin = (
            metrics.horizontalAdvance("0")
            + metrics.horizontalAdvance("한")
        )
        table_width = (
            row_content_width
            + row_right_margin
            + (table.frameWidth() * 2)
            + scrollbar_extent
        )
        required_width = (
            table_width
            + self._main_routine_filter_badge_area.width()
            + self._main_routine_content_layout.spacing()
            + self._layout_horizontal_margins(self._main_routine_content_layout)
            + self._layout_horizontal_margins(self._main_routine_layout)
            + self._layout_horizontal_margins(self._main_table_area_layout)
            + self._layout_horizontal_margins(self._main_dashboard_layout)
        )
        return max(int(self.minimumWidth()), int(required_width))

    def _apply_main_control_window_width(self) -> None:
        self.resize(self._main_control_window_required_width(), self.height())

    def _style_main_top_action_buttons(self) -> None:
        reference_button = getattr(self, "_main_routine_valid_button", None)
        for button, object_name in (
            (self.btn_market_data_monitoring, "mainMarketDataMonitoringButton"),
            (self.btn_group_pack_register, "mainGroupPackRegisterButton"),
            (self.btn_main_visible_early_close, "mainVisibleEarlyCloseButton"),
        ):
            button.setObjectName(object_name)
            style = (
                f"QPushButton#{object_name} {{"
                f" {AUTO_TRADE_SETTING_EARLY_CLOSE_BUTTON_STYLE}"
            )
            if reference_button is not None:
                reference_font = reference_button.font()
                button.setFont(reference_font)
                if reference_font.pixelSize() > 0:
                    style += f" font-size: {reference_font.pixelSize()}px;"
                elif reference_font.pointSizeF() > 0:
                    style += f" font-size: {reference_font.pointSizeF():g}pt;"
            style += " }"
            button.setMinimumHeight(28)
            button.setStyleSheet(style)

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
            badge = (
                getattr(self, "btn_review_required", None)
                if key == "review"
                else None
            )
            if not isinstance(badge, QPushButton):
                badge = QPushButton()
                if key == "review":
                    self.btn_review_required = badge
            badge.setText("")
            badge.setObjectName("mainRoutineSummaryCountBadge")
            badge.setFixedSize(count_badge_width, badge_height)
            badge.setFocusPolicy(Qt.NoFocus)
            badge.setCursor(Qt.PointingHandCursor)
            badge.setCheckable(key != "review")
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
            if key != "review":
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
            if str(key) != "review"
        )
        if badge_snapshot == getattr(
            self,
            "_main_routine_summary_badge_snapshot",
            None,
        ):
            return
        if isinstance(count_labels, dict) and isinstance(badges, tuple):
            for key, label_text, value in badges:
                if str(key) == "review":
                    continue
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
        if clean_key == "review":
            opener = getattr(self, "open_review_required_window", None)
            if callable(opener):
                opener()
            return
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
        if clean_key in {"operation", "waiting", "excluded"}:
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
            "review": MainWindow._review_required_window_is_open(self),
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

    def _review_required_window_is_open(self) -> bool:
        window = getattr(self, "review_required_window", None)
        if window is None or sip.isdeleted(window):
            return False
        is_visible = getattr(window, "isVisible", None)
        return bool(is_visible()) if callable(is_visible) else False

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

    def _main_routine_selected_row_keys(self) -> tuple[tuple[str, str, str, str, str], ...]:
        selected_keys: list[tuple[str, str, str, str, str]] = []
        for index in self.routine_table.selectionModel().selectedRows():
            item = self.routine_table.item(index.row(), 0)
            if item is None:
                continue
            selected_keys.append(
                (
                    str(item.data(ROUTINE_ROW_KIND_ROLE) or ""),
                    str(item.data(ROUTINE_GROUP_ID_ROLE) or ""),
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
                str(item.data(ROUTINE_GROUP_ID_ROLE) or ""),
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
            "all", "normal", "operation", "waiting", "excluded"
        } else "all"

    def _assign_main_routine_stock_scope(
        self,
        scope: str,
        enabled: bool,
    ) -> None:
        clean_scope = str(scope or "").strip().lower()
        if clean_scope not in {
            "all", "normal", "operation", "waiting", "excluded"
        }:
            clean_scope = "all"
        current_scope = MainWindow._current_main_routine_stock_scope(self)
        if bool(enabled):
            target_scope = clean_scope
        elif clean_scope in {"operation", "waiting", "excluded"}:
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


        group_ids = set(self._routine_instance_ids_by_group)
        relation_ids = set(self._routine_stock_paths_by_group_instance)
        if clean_level == "group":
            self._collapsed_main_group_ids.update(group_ids)
        elif clean_level == "routine":
            self._collapsed_main_group_ids.difference_update(group_ids)
            self._collapsed_main_group_instance_ids.update(relation_ids)
        else:
            self._collapsed_main_group_ids.difference_update(group_ids)
            self._collapsed_main_group_instance_ids.difference_update(relation_ids)
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
        layout.setContentsMargins(24, 0, 0, 6)
        layout.setSpacing(0)

        self.main_status_message_label = QLabel()
        self.main_status_message_label.setObjectName("mainFooterStatusMessage")
        self.main_status_message_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.main_status_message_label.setWordWrap(False)
        self.main_status_message_label.setMinimumWidth(0)
        self.main_status_message_label.setMinimumHeight(32)
        self.main_status_message_label.setSizePolicy(
            QSizePolicy.Ignored,
            QSizePolicy.Fixed,
        )
        layout.addWidget(self.main_status_message_label, 6)

        buttons = [
            self.btn_start,
            self.btn_auto_trade_setting,
            self.btn_log_view,
            self.btn_close_all_windows,
            self.btn_exit,
        ]

        layout.addStretch(1)
        for button in buttons:
            button.setProperty("mainBottomActionButton", True)
            button.setMinimumHeight(32)
            button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            layout.addWidget(button, 7)
            layout.addStretch(1)

        self.btn_exit.setObjectName("secondaryButton")
        return layout

    def _bind_main_status_message_to_button_row(self) -> None:
        status_bar = self.statusBar()
        self._main_footer_current_priority = 0
        self._main_footer_priority_hold_until = 0.0
        self._main_footer_deferred_raw_message = ""
        self._main_footer_deferred_generation = 0
        self._main_footer_suppressed_status_active = False
        status_bar.messageChanged.connect(
            lambda message: MainWindow._project_main_status_message(self, message)
        )
        MainWindow._project_main_status_message(self, status_bar.currentMessage())
        status_bar.hide()

    def _project_main_status_message(self, raw_message: object) -> None:
        clean_message = str(raw_message or "").strip()
        projection = project_operator_footer_message(clean_message)
        if projection is None:
            self._main_footer_suppressed_status_active = bool(clean_message)
            return
        if not clean_message and bool(
            getattr(self, "_main_footer_suppressed_status_active", False)
        ):
            self._main_footer_suppressed_status_active = False
            return
        self._main_footer_suppressed_status_active = False

        now = monotonic()
        current_priority = int(getattr(self, "_main_footer_current_priority", 0) or 0)
        hold_until = float(
            getattr(self, "_main_footer_priority_hold_until", 0.0) or 0.0
        )
        if should_defer_operator_footer_message(
            current_priority=current_priority,
            incoming_priority=projection.priority,
            hold_until=hold_until,
            now=now,
        ):
            self._main_footer_deferred_raw_message = clean_message
            self._main_footer_deferred_generation = int(
                getattr(self, "_main_footer_deferred_generation", 0) or 0
            ) + 1
            generation = self._main_footer_deferred_generation
            delay_ms = max(1, int((hold_until - now) * 1000) + 1)
            QTimer.singleShot(
                delay_ms,
                lambda: MainWindow._flush_deferred_main_status_message(
                    self,
                    generation,
                ),
            )
            return

        MainWindow._apply_main_status_projection(self, projection)

    def _apply_main_status_projection(self, projection) -> None:
        self._main_footer_deferred_generation = int(
            getattr(self, "_main_footer_deferred_generation", 0) or 0
        ) + 1
        self._main_footer_deferred_raw_message = ""
        self._main_footer_current_priority = projection.priority
        if projection.category in {"failure", "warning"}:
            self._main_footer_priority_hold_until = (
                monotonic() + (OPERATOR_FOOTER_PRIORITY_HOLD_MS / 1000.0)
            )
        else:
            self._main_footer_priority_hold_until = 0.0
        self.main_status_message_label.setText(projection.text)
        self.main_status_message_label.setStyleSheet(
            f"color: {projection.color}; background: transparent;"
        )

    def _flush_deferred_main_status_message(self, generation: int) -> None:
        if sip.isdeleted(self):
            return
        if generation != int(
            getattr(self, "_main_footer_deferred_generation", 0) or 0
        ):
            return
        raw_message = str(
            getattr(self, "_main_footer_deferred_raw_message", "") or ""
        )
        self._main_footer_priority_hold_until = 0.0
        MainWindow._project_main_status_message(self, raw_message)

    def _main_stock_live_tooltip(self, index, fallback_text: str) -> str:
        projection = index.data(ROUTINE_STOCK_TOOLTIP_DATA_ROLE)
        if not isinstance(projection, dict):
            return fallback_text
        code = str(projection.get("stock_code", "") or "").strip()
        host = self.main_monitoring_auto_trade_operation_host()
        market_information = getattr(
            host,
            "monitoring_market_information_state",
            None,
        )
        if not callable(market_information):
            market_information = getattr(host, "high_resolution_market_state", None)
        live_state = market_information(code) if code and callable(market_information) else None
        return main_stock_row_tooltip_from_projection(projection, live_state)

    def _setup_routine_table(self) -> None:
        headers = list(ROUTINE_MONITORING_HEADERS)

        self.routine_table.setFont(main_monitoring_table_font())
        self.routine_table.setColumnCount(len(headers))
        self.routine_table.setHorizontalHeaderLabels(headers)

        routine_header = self.routine_table.horizontalHeader()
        routine_header.setMinimumSectionSize(0)
        routine_header.setSectionResizeMode(QHeaderView.Fixed)
        routine_header.setStretchLastSection(False)

        self.routine_table.setColumnWidth(
            0,
            routine_instance_status_column_left(self.routine_table.font()),
        )
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
        self.routine_table._hovered_main_group_id = ""
        self._routine_tree_item_delegate = _RoutineTreeItemDelegate(self.routine_table)
        self.routine_table.setItemDelegateForColumn(0, self._routine_tree_item_delegate)
        self._routine_stock_name_tooltip_filter = install_persistent_stock_name_tooltips(
            self.routine_table,
            {0},
            accept_index=lambda index: str(
                index.data(ROUTINE_ROW_KIND_ROLE) or ""
            ) == ROUTINE_ROW_STOCK,
            accept_position=lambda index, pos: (
                _routine_stock_code_rect(self.routine_table, index).contains(pos)
                or _routine_stock_name_rect(self.routine_table, index).contains(pos)
            ),
            tooltip_point_size=(self.routine_table.font().pointSizeF() * 1.1),
            tooltip_resolver=self._main_stock_live_tooltip,
        )

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
        self._running_stock_name_clip_delegate = _ClippedTextItemDelegate(
            self.running_stock_table
        )
        self.running_stock_table.setItemDelegateForColumn(
            1,
            self._running_stock_name_clip_delegate,
        )
        self._running_stock_name_tooltip_filter = install_persistent_stock_name_tooltips(
            self.running_stock_table,
            {0, 1},
            source_column=1,
            tooltip_point_size=(self.running_stock_table.font().pointSizeF() * 1.1),
            tooltip_resolver=self._main_stock_live_tooltip,
        )
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
            QWidget#mainDashboardRoot QPushButton[mainBottomActionButton="true"],
            QWidget#mainDashboardRoot QPushButton#secondaryButton[mainBottomActionButton="true"] {
                background: #ffffff;
            }
            QWidget#mainDashboardRoot QPushButton[mainBottomActionButton="true"]:hover {
                background: #f8fafc;
            }
            QWidget#mainDashboardRoot QPushButton[mainBottomActionButton="true"]:disabled {
                background: #f8fafc;
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
        self.btn_market_data_monitoring.clicked.connect(
            self.open_market_data_monitoring_window
        )
        self.btn_group_pack_register.clicked.connect(self.register_group_pack_from_file)
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
        # Preserve the existing operation-cycle reconciliation fallback without
        # making an ordinary view refresh mutate the Broker subscription set.
        sync_auto_trade_monitoring_universe(self)
        self.refresh_auto_trade_assignment_views()

    def _on_main_market_data_observed(self, payload: object) -> None:
        if getattr(self, "_main_window_closing", False) or not isinstance(payload, dict):
            return
        code = str(payload.get("stock_code", "") or "").strip().upper().lstrip("A")
        if not code:
            return
        self._pending_main_market_information_codes.add(code)
        if not self._main_market_information_refresh_timer.isActive():
            self._main_market_information_refresh_timer.start()

    def _refresh_main_market_information_rows(self) -> int:
        stock_codes = tuple(self._pending_main_market_information_codes)
        self._pending_main_market_information_codes.clear()
        return main_refresh_market_information_only(self, stock_codes)

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
            "NOT_STARTED": "로그인 상태 확인 대기",
            "COLLECTING": "계좌 상태 확인",
            "RECONCILING": "저장 상태 대조",
            "REVIEW_REQUIRED": "검토 필요",
            "COMPLETED": "운영 상태 확인 완료",
            "FAILED": "운영 상태 확인 실패",
            "STALE": "로그인 상태 확인 만료",
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

        self._assignment_production_registry_result = (
            apply_assignment_reconciliation_to_production_registry(
                self._assignment_startup_reconciliation_result,
                identity=identity,
                registry=production_recovery_registry,
            )
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

    def global_operation_start_prerequisite(self, action_name: str) -> dict[str, object]:
        if self.startup_recovery_session_ready(refresh=True):
            return {"allowed": True, "reason": "GLOBAL_PREREQUISITE_READY"}
        decision = self.production_recovery_gate_for_stock(
            "__GLOBAL_OPERATION_START__",
            caller_name=f"{str(action_name or '운영시작').strip()}_GLOBAL_PREREQUISITE",
        )
        return {
            "allowed": False,
            "reason": str(getattr(decision, "reason_code", "") or "RECOVERY_NOT_READY"),
            "user_message": self.production_recovery_block_user_message(decision),
        }

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
                    "운영 상태 확인 정보를 읽을 수 없습니다. "
                    "다시 로그인한 후 운영을 시작하십시오."
                )
            return (
                "운영 시작에 필요한 현재 로그인 상태를 확인할 수 없습니다. "
                "로그인과 계좌 선택 상태를 다시 확인하십시오."
            )

        messages = {
            RECOVERY_NOT_STARTED: (
                "운영 시작 전에 현재 로그인 상태 확인이 완료되지 않았습니다. "
                "로그인과 계좌 선택 상태를 확인하십시오."
            ),
            RECOVERY_IN_PROGRESS: (
                "기존 운영 상태를 확인하고 있습니다. 확인이 끝난 후 다시 시도하십시오."
            ),
            RECOVERY_ACCOUNT_REVIEW_REQUIRED: (
                "복구가 필요한 종목이 남아 있습니다. "
                "검토관리에서 해당 종목을 처리하십시오."
            ),
            RECOVERY_IDENTITY_MISMATCH: (
                "현재 로그인 또는 계좌 정보가 확인된 운영 상태와 일치하지 않습니다. "
                "다시 로그인하십시오."
            ),
            RECOVERY_STALE_SESSION: (
                "이전 로그인에서 확인한 운영 상태는 현재 사용할 수 없습니다. "
                "다시 로그인하십시오."
            ),
            RECOVERY_STOCK_PENDING: (
                "선택한 종목의 현재 로그인 상태 확인이 완료되지 않아 "
                "운영을 시작할 수 없습니다. 다시 로그인한 후 운영하십시오."
            ),
            RECOVERY_STOCK_REVIEW_REQUIRED: (
                "선택한 종목은 복구 검토 대상입니다. "
                "검토관리에서 해당 종목을 처리하십시오."
            ),
            RECOVERY_STOCK_FAILED: (
                "선택한 종목의 운영 상태를 확인하지 못했습니다. "
                "검토관리에서 상태를 확인하십시오."
            ),
        }
        if reason_code == RECOVERY_ACCOUNT_FAILED:
            failure_reason = str(
                getattr(self, "_production_recovery_failure_reason_code", "") or ""
            ).strip()
            if failure_reason == "DAMAGED_RUNTIME":
                return (
                    "저장된 운영 상태를 읽을 수 없습니다. "
                    "검토관리에서 상태를 확인하십시오."
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
                    "키움 연결 상태를 확인한 후 다시 로그인하십시오."
                )
            if failure_reason == "RECOVERY_TIMER_START_FAILED":
                return (
                    "운영 주기 실행을 시작하지 못했습니다. "
                    "로그를 확인한 후 다시 로그인하십시오."
                )
            if failure_reason == "RECOVERY_NO_RESTORED_STOCK":
                return (
                    "운영 상태 확인이 완료된 대상 종목이 없습니다. "
                    "검토관리에서 종목 상태를 확인하십시오."
                )
            return (
                "계좌의 운영 상태를 확인하지 못했습니다. "
                "로그인과 계좌 상태를 확인한 후 다시 로그인하십시오."
            )
        return messages.get(
            reason_code,
            "운영 시작에 필요한 현재 로그인 상태를 확인할 수 없습니다. "
            "로그인과 계좌 선택 상태를 확인하십시오.",
        )

    def filter_start_targets_by_production_recovery(
        self,
        targets: list[tuple[Path, str, str]],
        *,
        caller_name: str,
    ) -> dict[str, object]:
        eligible: list[tuple[Path, str, str]] = []
        excluded_review: list[str] = []
        blocked_target_details: list[dict[str, object]] = []
        first_blocked_decision = None
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
                blocked_target_details.append(
                    {
                        "stock_code": str(code),
                        "stock_name": str(name),
                        "reason": str(decision.reason_code),
                        "display_label": f"{code} {name}".strip(),
                    }
                )
                continue
            if first_blocked_decision is None:
                first_blocked_decision = decision
            blocked_target_details.append(
                {
                    "stock_code": str(code),
                    "stock_name": str(name),
                    "reason": str(decision.reason_code),
                    "display_label": f"{code} {name}".strip(),
                }
            )
        if first_blocked_decision is not None:
            return {
                "allowed": False,
                "reason": first_blocked_decision.reason_code,
                "user_message": self.production_recovery_block_user_message(
                    first_blocked_decision
                ),
                "eligible": tuple(eligible),
                "excluded_review": tuple(excluded_review),
                "blocked_target_details": tuple(blocked_target_details),
            }
        return {
            "allowed": True,
            "reason": "RECOVERY_COMPLETED",
            "eligible": tuple(eligible),
            "excluded_review": tuple(excluded_review),
            "blocked_target_details": tuple(blocked_target_details),
        }

    def refresh_startup_recovery_status(self) -> dict[str, object]:
        if self._production_recovery_required():
            result = self._production_recovery_status_result()
            self._startup_recovery_result = result
            return result
        stock_state_paths = [
            stock_dir / "state.json"
            for stock_dir in self.all_runtime_stock_dirs()
        ]
        self._startup_runtime_initialization_result = (
            initialize_pristine_startup_runtime()
        )
        result = assess_startup_recovery(
            stock_state_paths=stock_state_paths,
            assignment_reconciliation_summary=(
                self._assignment_startup_reconciliation_result
            ),
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
                "BLOCKED_RECOVERY": "운영 재개 차단",
                "INVALID_RUNTIME": "저장 상태 손상",
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
                detail + "\n\n저장된 운영 상태를 먼저 검토하고 정상화해야 합니다.",
            )
            if result.get("operator_reconciliation", {}).get("summary", {}).get("total", 0):
                self.open_review_required_window()
            return

        message = detail + "\n\n현재 확인된 정보를 기준으로 자동매매 운영을 재개하시겠습니까?"
        answer = QMessageBox.question(
            self,
            "자동매매 운영 재개",
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
        if status in {"login_requested", "login_in_progress"}:
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
        login_identity = (
            int(state.get("connection_epoch", 0) or 0),
            str(state.get("login_session_id", "") or "").strip(),
        )
        new_authenticated_session = bool(
            connected
            and login_identity[1]
            and getattr(self, "_handled_kiwoom_login_identity", None) != login_identity
        )
        if new_authenticated_session:
            authentication_states = getattr(
                self,
                "_account_authentication_states",
                None,
            )
            if isinstance(authentication_states, dict):
                authentication_states.clear()
            query_states = getattr(self, "_account_query_states", None)
            if isinstance(query_states, dict):
                query_states.clear()
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
            if new_authenticated_session:
                self.main_monitoring_auto_trade_operation_host().sync_monitoring_universe_for_current_session()
        else:
            MainWindow._clear_completed_recovery_handoff(self)
            self._account_authentication_states.clear()
            self._account_query_states.clear()
            self._stop_production_recovery_timers()
            production_recovery_registry.invalidate("login disconnected")
            self._production_recovery_identity = None
            self._production_recovery_parts = {}
            self._production_recovery_status_result()
            self._handled_kiwoom_login_identity = None
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
        if new_authenticated_session:
            self._handled_kiwoom_login_identity = login_identity
            retention_runner = getattr(
                self,
                "stock_library_diagnostics_retention",
                None,
            )
            run_retention = getattr(retention_runner, "run_for_session", None)
            if callable(run_retention):
                try:
                    run_retention(
                        current_connection_epoch=login_identity[0],
                        current_session_id=login_identity[1],
                    )
                except Exception:
                    LOGGER.exception(
                        "Automatic Stock Library diagnostics retention failed"
                    )
        if connected != previously_connected or new_authenticated_session:
            MainWindow._refresh_start_budget_displays_for_auth_state(self)
        self.statusBar().showMessage(status_message)
        if new_authenticated_session:
            QTimer.singleShot(
                500,
                lambda identity=login_identity: MainWindow._continue_login_after_pending_budget_projection(
                    self,
                    identity,
                ),
            )

    def _continue_login_after_pending_budget_projection(
        self,
        login_identity: tuple[int, str],
    ) -> None:
        if (
            getattr(self, "_handled_kiwoom_login_identity", None) != login_identity
            or not bool(getattr(self, "_event_journal_kiwoom_connected", False))
        ):
            return
        self.request_account_funds()
        self.start_production_recovery()
        QTimer.singleShot(0, self.start_stock_library_sync_for_current_session)

    def start_stock_library_sync_for_current_session(self) -> bool:
        service = getattr(self, "stock_library_sync_service", None)
        start = getattr(service, "start_for_current_session", None)
        if not callable(start):
            return False
        try:
            return bool(start())
        except Exception:
            LOGGER.exception("Failed to schedule Kiwoom Stock Library sync")
            return False

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
        if not account or account == str(self.selected_account_no() or "").strip():
            MainWindow._refresh_start_budget_displays_for_auth_state(self)

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
        previous_authentication_state = (
            authentication_states.get(account_id)
            if isinstance(authentication_states, dict) and account_id
            else None
        )
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
        authentication_state_changed = bool(
            isinstance(authentication_states, dict)
            and account_id
            and authentication_states.get(account_id)
            != previous_authentication_state
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
        if authentication_state_changed:
            selected_account = str(self.selected_account_no() or "").strip()
            if not selected_account or selected_account == account_id:
                MainWindow._refresh_start_budget_displays_for_auth_state(self)

    def _refresh_start_budget_displays_for_auth_state(self) -> None:
        cache = getattr(self, "_main_stock_resolved_starting_budget_cache", None)
        if isinstance(cache, dict):
            cache.clear()
        load_routine_table = getattr(self, "load_routine_table", None)
        if callable(load_routine_table):
            load_routine_table()

    def refresh_all(self) -> None:
        previous_context = getattr(self, "_main_refresh_read_context", None)
        self._main_refresh_read_context = build_main_refresh_read_context(self)
        try:
            self.load_routine_table()
            main_load_running_stock_table(self)
            self.update_budget_panel()
            self.update_emergency_button_state()
            self.update_review_required_button_text()
            self.update_global_operation_button_state()
        finally:
            self._main_refresh_read_context = previous_context

    def recalculate_routine_limits_for_new_operation_session(self) -> dict[str, object]:
        from routine_limit_recalculation import (
            recalculate_enabled_routine_limits_for_new_session,
        )

        return recalculate_enabled_routine_limits_for_new_session(self)

    def update_global_operation_button_state(self) -> None:
        adapter = MainMonitoringStockOperationAdapter(self, [])
        auto_trade_update_global_operation_button_state(adapter)
        window = getattr(self, "auto_trade_setting_window", None)
        if window is None or sip.isdeleted(window):
            return
        update = getattr(window, "update_global_operation_button_state", None)
        if callable(update):
            update()

    def start_global_auto_trades(self) -> None:
        adapter = MainMonitoringStockOperationAdapter(self, [])
        execute_operation_start_command(
            adapter,
            OperationStartCommandRequest(
                intent=OperationStartIntent.FULL_START,
                source="auto_trade_global_start_button",
            ),
        )

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

    def start_budget_apply_limit_checked(self) -> bool:
        try:
            settings = object.__getattribute__(self, "_account_memo_settings")
        except (AttributeError, RuntimeError):
            settings = None
        if settings is None:
            return False
        raw_value = settings.value(START_BUDGET_APPLY_LIMIT_SETTINGS_KEY, False)
        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, (int, float)):
            return bool(raw_value)
        return str(raw_value).strip().lower() not in {"", "0", "false", "no", "off"}

    def set_start_budget_apply_limit_checked(self, checked: bool) -> None:
        try:
            settings = object.__getattribute__(self, "_account_memo_settings")
        except (AttributeError, RuntimeError):
            settings = None
        if settings is None:
            return
        settings.setValue(
            START_BUDGET_APPLY_LIMIT_SETTINGS_KEY,
            bool(checked),
        )
        settings.sync()

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
        refresh_context = getattr(self, "_main_refresh_read_context", None)
        if isinstance(refresh_context, dict):
            return len(
                collect_global_review_required_rows(
                    preloaded_stocks=refresh_context.get("stocks", ()),
                    preloaded_stock_data_by_dir=refresh_context.get(
                        "stock_data_by_dir",
                        {},
                    ),
                )
            )
        return len(collect_global_review_required_rows())

    def update_review_required_button_text(self) -> None:
        if not hasattr(self, "btn_review_required"):
            return
        count = self.review_required_stock_count()
        count_labels = getattr(self, "_main_routine_summary_count_labels", {})
        labels = count_labels.get("review") if isinstance(count_labels, dict) else None
        if isinstance(labels, tuple) and len(labels) == 2:
            label, value_label = labels
            label.setText("검토")
            value_label.setText(str(max(0, int(count))))
            return
        self.btn_review_required.setText(f"검토 {max(0, int(count))}")

    def sort_main_routine_table_by_column(self, column: int) -> None:
        main_sort_routine_table_by_column(self, column)

    def sort_main_running_table_by_column(self, column: int) -> None:
        main_sort_running_table_by_column(self, column)

    def load_routine_table(self) -> None:
        main_load_routine_table(self)
        self._install_routine_buy_limit_edit_filters()

    def registered_operation_targets(self) -> list[tuple[Path, str, str]]:
        return auto_trade_registered_operation_targets(self)

    def all_runtime_stock_dirs(self) -> list[Path]:
        """Return canonical Group-assigned central stock runtime folders."""
        from group_scope import load_group_scope

        return list(load_group_scope().all_group_stock_dirs())

    def update_emergency_button_state(self) -> None:
        emergency_update_emergency_button_state(self)

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
                f"선택한 루틴을 등록 정보에서 찾지 못했습니다.\n루틴명: {definition.display_name}",
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
            settings_dialog = load_routine_callable(definition, SETTINGS_ROLE)
        except RoutineContractError as exc:
            QMessageBox.critical(
                self,
                "\uc124\uc815\ucc3d \ub85c\ub4dc \uc2e4\ud328",
                f"\uc120\ud0dd\ud55c \ub8e8\ud2f4\uc758 \uc124\uc815\ucc3d\uc744 \ubd88\ub7ec\uc624\uc9c0 \ubabb\ud588\uc2b5\ub2c8\ub2e4.\\n{exc}",
            )
            return

        dialog = settings_dialog(
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
        group_id = str(item.data(ROUTINE_GROUP_ID_ROLE) or "").strip()
        if not group_id:
            return
        if group_id in self._collapsed_main_group_ids:
            self._collapsed_main_group_ids.discard(group_id)
        else:
            self._collapsed_main_group_ids.add(group_id)
        self.load_routine_table()

    def toggle_routine_instance_expansion(self, row: int) -> None:
        item = self.routine_table.item(row, 0)
        if item is None or str(item.data(ROUTINE_ROW_KIND_ROLE) or "") != ROUTINE_ROW_CHILD:
            return
        instance_id = str(item.data(ROUTINE_INSTANCE_ID_ROLE) or "").strip()
        group_id = str(item.data(ROUTINE_GROUP_ID_ROLE) or "").strip()
        if not instance_id or not group_id:
            return
        relation_id = main_group_instance_relation_id(group_id, instance_id)
        if relation_id in self._collapsed_main_group_instance_ids:
            self._collapsed_main_group_instance_ids.discard(relation_id)
        else:
            self._collapsed_main_group_instance_ids.add(relation_id)
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

        refresh_auto_trade_views(self)

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
    def _stock_config_expected_fields(
        config: dict[str, object],
        field_keys: tuple[str, ...],
    ) -> dict[str, object]:
        return {
            key: config[key] if key in config else STOCK_CONFIG_EXPECTED_MISSING
            for key in field_keys
        }

    @staticmethod
    def _stock_config_repository_target(
        config_path: Path,
    ) -> tuple[StockRepository | None, str]:
        target = Path(config_path)
        stock_dir = target.parent
        stocks_dir = stock_dir.parent
        stock_code = stock_dir.name.partition("_")[0].strip()
        if (
            target.name != "config.json"
            or stocks_dir.name != "stocks"
            or not stock_code
        ):
            return None, stock_code
        return CanonicalStockConfigRepository(stocks_dir.parent), stock_code

    @staticmethod
    def _invalid_stock_config_write_result(
        field_keys: tuple[str, ...],
    ) -> StockConfigWriteResult:
        return StockConfigWriteResult(
            ok=False,
            changed=False,
            field_keys=field_keys,
            conflict_detected=False,
            read_back_verified=False,
            reason_code=STOCK_CONFIG_WRITE_INVALID_STOCK_IDENTITY,
        )

    @staticmethod
    def _stock_config_write_succeeded(result: object) -> bool:
        return bool(getattr(result, "ok", False))

    @staticmethod
    def _show_stock_buy_limit_write_failure(window, result: object) -> None:
        reason_code = str(getattr(result, "reason_code", "") or "").strip()
        if reason_code in {
            STOCK_CONFIG_WRITE_FIELD_CONFLICT,
            STOCK_CONFIG_WRITE_CONCURRENT_UPDATE_RETRY_EXHAUSTED,
        }:
            message = "종목 한도 설정이 변경되어 적용하지 않았습니다."
        else:
            message = "한도금액을 저장하지 못했습니다."
        show_toast(window, message, duration_ms=2500)

    @staticmethod
    def _write_stock_buy_limit_config(
        config_path: Path,
        *,
        enabled: bool,
        amount: int | None = None,
        source: str | None = None,
        expected_fields: dict[str, object] | None = None,
    ) -> StockConfigWriteResult:
        config = read_json_dict(config_path)
        if not isinstance(config, dict):
            config = {}
        enabled_value, amount_value, source_value = canonical_stock_buy_limit_values(
            enabled=enabled,
            amount=amount,
            source=source,
        )
        field_keys = (
            "buy_limit_enabled",
            "buy_limit_amount",
            "buy_limit_source",
        )
        if expected_fields is None:
            expected_fields = MainWindow._stock_config_expected_fields(
                config,
                field_keys,
            )
        repository, stock_code = MainWindow._stock_config_repository_target(config_path)
        if repository is None:
            return MainWindow._invalid_stock_config_write_result(field_keys)
        patch = {
            "buy_limit_enabled": enabled_value,
            "buy_limit_amount": amount_value,
            "buy_limit_source": source_value,
        }
        if any(config.get(key) != value for key, value in patch.items()):
            patch["updated_at"] = stock_now_text()
        return repository.patch_stock_config(
            stock_code,
            patch,
            expected_fields=expected_fields,
        )

    def _write_stock_initial_buy_config(
        self,
        config_path: Path,
        *,
        mode: str,
        value: int,
        apply_limit: bool = False,
        adjusted_limit_amount: int | None = None,
        running_adjustment_authorized: bool = False,
        expected_fields: dict[str, object] | None = None,
    ) -> dict[str, object]:
        config = read_json_dict(config_path)
        if not isinstance(config, dict):
            config = {}
        next_config = dict(config)
        normalized_mode = "AMOUNT" if str(mode).upper() == "AMOUNT" else "QUANTITY"
        next_config["trade_amount_type"] = normalized_mode
        if normalized_mode == "AMOUNT":
            next_config["buy_amount"] = max(0, int(value))
        else:
            next_config["buy_qty"] = max(1, int(value))
        stock_code = config_path.parent.name.partition("_")[0].strip()
        decision = auto_trade_start_budget_mutation_decision(
            self,
            stock_code,
            config,
            next_config,
            current_state=read_json_dict(config_path.parent / "state.json"),
        )
        authorized_running_write = bool(
            running_adjustment_authorized
            and decision.get("current_running") is True
        )
        if decision.get("allowed") is not True and not authorized_running_write:
            return decision
        if authorized_running_write:
            decision = {
                **decision,
                "allowed": True,
                "reason": "",
                "running_adjustment_authorized": True,
            }
        limit_requested = bool(apply_limit)
        limit_enabled = bool(config.get("buy_limit_enabled", False))
        limit_amount = safe_int_value(adjusted_limit_amount, 0)
        limit_write_requested = bool(
            limit_requested and limit_enabled and limit_amount > 0
        )
        limit_failure_reason = (
            "INVALID_ADJUSTED_LIMIT"
            if limit_requested and limit_enabled and limit_amount <= 0
            else ""
        )
        budget_patch = {
            "trade_amount_type": normalized_mode,
            (
                "buy_amount" if normalized_mode == "AMOUNT" else "buy_qty"
            ): next_config[
                "buy_amount" if normalized_mode == "AMOUNT" else "buy_qty"
            ],
            "updated_at": stock_now_text(),
        }
        budget_field_keys = tuple(
            key for key in budget_patch if key != "updated_at"
        )
        if expected_fields is None:
            expected_fields = MainWindow._stock_config_expected_fields(
                config,
                (
                    *budget_field_keys,
                    *(
                        (
                            "buy_limit_enabled",
                            "buy_limit_amount",
                            "buy_limit_source",
                        )
                        if limit_requested
                        else ()
                    ),
                ),
            )
        budget_expected_fields = {
            key: expected_fields.get(key)
            for key in budget_field_keys
            if key in expected_fields
        }
        repository, canonical_stock_code = MainWindow._stock_config_repository_target(
            config_path
        )
        if repository is None:
            persistence_result = MainWindow._invalid_stock_config_write_result(
                budget_field_keys
            )
        elif decision.get("changed") is True:
            persistence_result = repository.patch_stock_config(
                canonical_stock_code,
                budget_patch,
                expected_fields=budget_expected_fields,
            )
        else:
            persistence_result = StockConfigWriteResult(
                ok=True,
                changed=False,
                field_keys=budget_field_keys,
                conflict_detected=False,
                read_back_verified=True,
                reason_code="",
            )
        if not persistence_result.ok:
            return {
                **decision,
                "allowed": False,
                "reason": persistence_result.reason_code,
                "changed": False,
                "limit_changed": False,
                "written": False,
                "config_write_result": persistence_result,
            }
        limit_changed = False
        limit_write_result = None
        if limit_write_requested:
            limit_expected_fields = {
                key: expected_fields.get(key)
                for key in (
                    "buy_limit_enabled",
                    "buy_limit_amount",
                    "buy_limit_source",
                )
                if key in expected_fields
            }
            limit_write_result = MainWindow._write_stock_buy_limit_config(
                config_path,
                enabled=limit_enabled,
                amount=limit_amount,
                source=BUY_LIMIT_SOURCE_RECOMMENDED,
                expected_fields=limit_expected_fields,
            )
            if limit_write_result.ok:
                limit_changed = bool(limit_write_result.changed)
            else:
                limit_failure_reason = str(limit_write_result.reason_code or "").strip()
        return {
            **decision,
            "changed": bool(decision.get("changed")) or limit_changed,
            "limit_changed": limit_changed,
            "limit_requested": limit_requested,
            "limit_applied": bool(
                limit_write_requested
                and limit_write_result is not None
                and limit_write_result.ok
            ),
            "limit_reason": limit_failure_reason,
            "written": bool(persistence_result.changed or limit_changed),
            "config_write_result": persistence_result,
            "limit_write_result": limit_write_result,
        }

    @staticmethod
    def _show_start_budget_mutation_blocked(window) -> None:
        show_toast(window, "운영중에는 시작예산을 변경할 수 없습니다.", duration_ms=2500)

    @staticmethod
    def _start_budget_config_expected_fields(
        opened_config: dict[str, object],
        *,
        mode: str,
        apply_limit: bool,
    ) -> dict[str, object]:
        normalized_mode = "AMOUNT" if str(mode).upper() == "AMOUNT" else "QUANTITY"
        field_keys = [
            "trade_amount_type",
            "buy_amount" if normalized_mode == "AMOUNT" else "buy_qty",
        ]
        if apply_limit:
            field_keys.extend(
                (
                    "buy_limit_enabled",
                    "buy_limit_amount",
                    "buy_limit_source",
                )
            )
        return MainWindow._stock_config_expected_fields(
            opened_config,
            tuple(field_keys),
        )

    @staticmethod
    def _record_start_budget_config_write_failure(
        window,
        *,
        stock_code: str,
        stock_name: str,
        reason_code: str,
        runtime_committed: bool,
    ) -> None:
        target_name = " ".join(
            part for part in (str(stock_code).strip(), str(stock_name).strip()) if part
        )
        event_type = "RUNTIME_WARNING" if runtime_committed else "PROCESSING_ERROR"
        observe_owner_failure_transition(
            window,
            f"start_budget_config_write:{stock_code}",
            active=True,
            signature=f"{event_type}:{reason_code}:{runtime_committed}",
            event_type=event_type,
            severity="WARNING" if runtime_committed else "ERROR",
            result="PARTIAL" if runtime_committed else "FAILED",
            source="gui_windows.MainWindow._open_running_budget_adjustment_dialog",
            template_args={"target": target_name or "기본예산 설정"},
            target_type="STOCK",
            target_id=stock_code,
            target_name=target_name,
            stock_code=stock_code,
            stock_name=stock_name,
            reason_code=reason_code,
            details={
                "stage": "base_config_write",
                "runtime_committed": runtime_committed,
            },
        )

    @staticmethod
    def _show_start_budget_config_write_failure(
        window,
        *,
        reason_code: str,
        runtime_committed: bool,
    ) -> None:
        if reason_code in {
            STOCK_CONFIG_WRITE_FIELD_CONFLICT,
            STOCK_CONFIG_WRITE_CONCURRENT_UPDATE_RETRY_EXHAUSTED,
        }:
            message = "기본예산 설정이 변경되어 적용하지 않았습니다."
        elif runtime_committed:
            message = "기본예산 변경 요청은 저장됐지만 영구 설정을 저장하지 못했습니다."
        else:
            message = "기본예산을 저장하지 못했습니다."
        show_toast(window, message)

    @staticmethod
    def _starting_budget_change_current_price(
        window,
        config_path: Path,
    ) -> float | None:
        availability = inspect_budget_value_entry(
            window,
            BudgetValueChangeRequest(Path(config_path)),
        )
        current_price = availability.get("current_price")
        resolved_price = safe_float_value(current_price, 0.0)
        return resolved_price if resolved_price > 0 else None

    @staticmethod
    def _start_budget_price_unavailable_message() -> str:
        return "현재 주가를 확인한 후 변경할 수 있습니다."

    def _open_running_budget_adjustment_dialog(
        self,
        row: int,
        config_path: Path,
    ) -> None:
        item = self.routine_table.item(row, 0)
        stock_code = str(
            item.data(ROUTINE_STOCK_CODE_ROLE) if item is not None else ""
        ).strip()
        stock_name = str(
            item.data(ROUTINE_STOCK_NAME_ROLE) if item is not None else ""
        ).strip()
        current_price = MainWindow._starting_budget_change_current_price(
            self,
            config_path,
        )
        if current_price is None:
            show_toast(
                self,
                MainWindow._start_budget_price_unavailable_message(),
            )
            return
        state = read_json_dict(config_path.parent / "state.json")
        config = read_json_dict(config_path)
        if not isinstance(config, dict):
            config = {}
        opened_current_running = auto_trade_start_budget_current_running(
            self,
            stock_code,
            config,
            state,
        )
        if opened_current_running:
            display_config, display_projection = (
                project_running_budget_adjustment_display_config(config, state)
            )
            pending_adjustment = display_projection.get("adjustment")
        else:
            display_config = dict(config)
            pending_adjustment = None
        minimum_amount = None
        if (
            str(display_config.get("trade_amount_type") or "").strip().upper()
            == "AMOUNT"
        ):
            budget_defaults = starting_budget_defaults()
            resolved_minimum_amount = MainWindow._stock_default_initial_buy_value(
                config_path,
                "AMOUNT",
                window=self,
                defaults=budget_defaults,
            )
            minimum_amount = (
                resolved_minimum_amount if resolved_minimum_amount > 0 else None
            )
        dialog = RunningBudgetAdjustmentDialog(
            self,
            stock_code=stock_code,
            stock_name=stock_name,
            current_price=current_price,
            config=display_config,
            minimum_amount=minimum_amount,
            pending_adjustment=(
                pending_adjustment if isinstance(pending_adjustment, dict) else None
            ),
            timing_selection_enabled=opened_current_running,
            configuration_price_available=(
                current_price is not None
                and safe_float_value(current_price, 0.0) > 0
            ),
            apply_limit_checked=MainWindow.start_budget_apply_limit_checked(self),
        )
        self._running_budget_adjustment_dialog = dialog
        accepted = False
        request: dict[str, object] = {}
        try:
            accepted = dialog.exec_() == QDialog.Accepted
            request = dict(dialog.result)
        finally:
            self._running_budget_adjustment_dialog = None
            dialog.deleteLater()
        if not accepted or not request:
            return

        MainWindow.set_start_budget_apply_limit_checked(
            self,
            bool(request.get("apply_limit", False)),
        )

        if MainWindow._starting_budget_change_current_price(
            self,
            config_path,
        ) is None:
            show_toast(
                self,
                MainWindow._start_budget_price_unavailable_message(),
            )
            return

        current_config = read_json_dict(config_path)
        current_state = read_json_dict(config_path.parent / "state.json")
        current_running = auto_trade_start_budget_current_running(
            self,
            stock_code,
            current_config,
            current_state,
        )
        if current_running != opened_current_running:
            show_toast(self, "운영 상태가 변경되어 기본예산 변경을 적용하지 않았습니다.")
            return

        requested_mode = str(request.get("mode") or "").strip().upper()
        current_mode = (
            "AMOUNT"
            if str(current_config.get("trade_amount_type") or "").strip().upper()
            == "AMOUNT"
            else "QUANTITY"
        )
        if requested_mode != current_mode:
            show_toast(self, "예산 방식이 변경되어 기본예산 변경을 적용하지 않았습니다.")
            return

        requested_value = safe_int_value(request.get("value"), 0)
        adjusted_limit_amount = None
        if bool(request.get("apply_limit", False)):
            adjusted_limit_amount = self._adjusted_buy_limit_for_start_budget(
                config_path,
                mode=current_mode,
                value=requested_value,
            )

        expected_fields = MainWindow._start_budget_config_expected_fields(
            display_config,
            mode=current_mode,
            apply_limit=bool(request.get("apply_limit", False)),
        )

        if opened_current_running:
            commit_result = commit_running_budget_adjustment(
                config_path.parent,
                stock_code=stock_code,
                expected_mode=current_mode,
                requested_value=requested_value,
                apply_policy=request.get("apply_timing"),
                apply_limit=bool(request.get("apply_limit", False)),
                adjusted_limit_amount=adjusted_limit_amount,
                requested_at=dialog.requested_at,
            )
            if commit_result.get("ok") is not True:
                show_toast(self, "기본예산 변경 요청을 저장하지 못했습니다.")
                return
            write_result = self._write_stock_initial_buy_config(
                config_path,
                mode=current_mode,
                value=requested_value,
                apply_limit=bool(request.get("apply_limit", False)),
                adjusted_limit_amount=adjusted_limit_amount,
                running_adjustment_authorized=True,
                expected_fields=expected_fields,
            )
            if write_result.get("allowed") is not True:
                reason_code = str(write_result.get("reason") or "").strip()
                MainWindow._record_start_budget_config_write_failure(
                    self,
                    stock_code=stock_code,
                    stock_name=stock_name,
                    reason_code=reason_code,
                    runtime_committed=True,
                )
                refresh_auto_trade_views(self)
                MainWindow._show_start_budget_config_write_failure(
                    self,
                    reason_code=reason_code,
                    runtime_committed=True,
                )
                return
        else:
            write_result = self._write_stock_initial_buy_config(
                config_path,
                mode=current_mode,
                value=requested_value,
                apply_limit=bool(request.get("apply_limit", False)),
                adjusted_limit_amount=adjusted_limit_amount,
                expected_fields=expected_fields,
            )
            if write_result.get("allowed") is not True:
                if write_result.get("reason") == "START_BUDGET_MUTATION_BLOCKED":
                    show_toast(
                        self,
                        "운영 상태가 변경되어 기본예산 변경을 적용하지 않았습니다.",
                    )
                else:
                    reason_code = str(write_result.get("reason") or "").strip()
                    MainWindow._record_start_budget_config_write_failure(
                        self,
                        stock_code=stock_code,
                        stock_name=stock_name,
                        reason_code=reason_code,
                        runtime_committed=False,
                    )
                    self.load_routine_table()
                    MainWindow._show_start_budget_config_write_failure(
                        self,
                        reason_code=reason_code,
                        runtime_committed=False,
                    )
                return
        if bool(request.get("apply_limit", False)):
            success_message = (
                "기본예산과 한도금액을 변경했습니다."
                if bool(write_result.get("limit_applied"))
                else "기본예산을 변경했습니다. 한도금액은 기존 설정을 유지합니다."
            )
        else:
            success_message = "기본예산을 변경했습니다."
        if opened_current_running or bool(write_result.get("changed")):
            refresh_auto_trade_views(self)
        show_toast(self, success_message)

    def _adjusted_buy_limit_for_start_budget(
        self,
        config_path: Path,
        *,
        mode: str,
        value: int,
    ) -> int | None:
        configuration_state = main_stock_configuration_market_information_state(
            self,
            self._stock_projection_for_config_path(config_path),
        )
        if configuration_state is None:
            return None
        starting_budget = max(0, safe_int_value(value, 0))
        if str(mode or "").strip().upper() == "QUANTITY":
            configuration_price = (
                getattr(configuration_state, "last_price", None)
            )
            starting_budget = floor_money_to_won(
                safe_float_value(configuration_price, 0.0) * starting_budget
            )
        defaults = starting_budget_defaults()
        if starting_budget <= 0:
            return None
        adjusted_limit_amount = suggested_buy_limit(
            starting_budget,
            defaults["limit_recommended_multiplier"],
            align_digits=stock_limit_digit_alignment_enabled(),
        )
        total_budget = _system_total_budget_amount()
        if (
            adjusted_limit_amount is None
            or total_budget is None
            or adjusted_limit_amount > total_budget
        ):
            return None
        return adjusted_limit_amount

    @staticmethod
    def _adjusted_buy_limit_failure_reason(
        window,
        config_path: Path,
        *,
        mode: str,
        value: int,
    ) -> str:
        configuration_state = main_stock_configuration_market_information_state(
            window,
            MainWindow._stock_projection_for_config_path(config_path),
        )
        if configuration_state is None:
            return CURRENT_PRICE_UNAVAILABLE
        starting_budget = max(0, safe_int_value(value, 0))
        if str(mode or "").strip().upper() == "QUANTITY":
            starting_budget = floor_money_to_won(
                safe_float_value(
                    getattr(configuration_state, "last_price", None),
                    0.0,
                )
                * starting_budget
            )
        if starting_budget <= 0:
            return "INVALID_STARTING_BUDGET"
        defaults = starting_budget_defaults()
        adjusted_limit_amount = suggested_buy_limit(
            starting_budget,
            defaults["limit_recommended_multiplier"],
            align_digits=stock_limit_digit_alignment_enabled(),
        )
        if adjusted_limit_amount is None:
            return "LIMIT_DEFAULTS_INVALID"
        total_budget = _system_total_budget_amount()
        if total_budget is None:
            return "TOTAL_BUDGET_UNAVAILABLE"
        if adjusted_limit_amount > total_budget:
            return "RECOMMENDED_BUY_LIMIT_EXCEEDS_TOTAL_BUDGET"
        return "INVALID_STARTING_BUDGET"

    def _stock_start_budget_locked(self, config_path: Path) -> bool:
        target_path = Path(config_path)
        stock_code = target_path.parent.name.partition("_")[0].strip()
        config = read_json_dict(target_path)
        if not isinstance(config, dict):
            config = {}
        return auto_trade_start_budget_current_running(
            self,
            stock_code,
            config,
            read_json_dict(target_path.parent / "state.json"),
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
    def _stock_projection_for_config_path(config_path: Path) -> dict[str, object]:
        target_path = Path(config_path)
        try:
            stock_path = str(target_path.parent.relative_to(PROJECT_ROOT))
        except ValueError:
            stock_path = str(target_path.parent)
        return {
            "stock_path": stock_path,
            "code": target_path.parent.name.partition("_")[0].strip(),
            "name": target_path.parent.name.partition("_")[2].strip(),
        }

    @staticmethod
    def _stock_default_initial_buy_value(
        config_path: Path,
        mode: str,
        *,
        window=None,
        defaults: dict[str, float | int] | None = None,
    ) -> int:
        defaults = (
            dict(defaults)
            if isinstance(defaults, dict)
            else starting_budget_defaults()
        )
        if mode == "QUANTITY":
            return int(defaults["quantity"])
        if window is None:
            return 0
        config = read_json_dict(config_path)
        if not isinstance(config, dict):
            config = {}
        config = {
            **config,
            "trade_amount_type": "AMOUNT",
            "buy_amount": 0,
        }
        amount = main_stock_resolved_starting_budget(
            window,
            MainWindow._stock_projection_for_config_path(config_path),
            config,
            policy={"starting_budget_defaults": defaults},
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
        config = read_json_dict(target_path)
        if not isinstance(config, dict):
            return None
        if window is None:
            return None
        if main_stock_configuration_market_information_state(
            window,
            MainWindow._stock_projection_for_config_path(target_path),
        ) is None:
            return None
        starting_budget = main_stock_resolved_starting_budget(
            window,
            MainWindow._stock_projection_for_config_path(target_path),
            config,
            policy={"starting_budget_defaults": defaults},
        )
        return suggested_buy_limit(
            starting_budget,
            defaults[multiplier_key],
            align_digits=stock_limit_digit_alignment_enabled(),
        )

    @staticmethod
    def _stock_suggested_buy_limit_failure_reason(
        config_path: Path,
        *,
        window=None,
    ) -> str:
        config = read_json_dict(Path(config_path))
        if not isinstance(config, dict) or not config:
            return "INVALID_STARTING_BUDGET"
        if window is None:
            return CURRENT_PRICE_UNAVAILABLE
        configuration_state = main_stock_configuration_market_information_state(
            window,
            MainWindow._stock_projection_for_config_path(Path(config_path)),
        )
        if configuration_state is None:
            return CURRENT_PRICE_UNAVAILABLE
        return "INVALID_STARTING_BUDGET"

    @staticmethod
    def _stock_buy_limit_failure_message(reason_code: object) -> str:
        return {
            CURRENT_PRICE_UNAVAILABLE: (
                "현재 주가를 확인할 수 없어 권장한도를 계산하지 못했습니다."
            ),
            "TOTAL_BUDGET_UNAVAILABLE": (
                "전체예산을 확인할 수 없어 한도를 계산하지 못했습니다."
            ),
            "RECOMMENDED_BUY_LIMIT_EXCEEDS_TOTAL_BUDGET": (
                "권장한도가 전체예산을 초과합니다."
            ),
            "MINIMUM_BUY_LIMIT_EXCEEDS_TOTAL_BUDGET": (
                "최소한도가 전체예산을 초과합니다."
            ),
            "INVALID_STARTING_BUDGET": (
                "기본예산을 확인할 수 없어 권장한도를 계산하지 못했습니다."
            ),
            "LIMIT_DEFAULTS_INVALID": (
                "최소한도를 계산하지 못해 한도를 적용하지 않았습니다."
            ),
            "WRITE_FAILED": "한도금액을 저장하지 못했습니다.",
        }.get(
            str(reason_code or "").strip(),
            "한도금액을 저장하지 못했습니다.",
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
        handle_auto_trade_operation_mode_double_click(
            adapter,
            (target.stock_dir, target.code, target.name),
        )
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
        group_id = str(item.data(ROUTINE_GROUP_ID_ROLE) or "").strip()
        if not instance_id or not group_id:
            return False

        stock_dirs = self._projected_group_instance_stock_dirs(group_id, instance_id)
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
            if auto_trade_set_stock_operation_exclusion(
                adapter,
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

        group_id = str(item.data(ROUTINE_GROUP_ID_ROLE) or "").strip()
        if not group_id:
            return False

        stock_dirs = self._projected_group_stock_dirs(group_id)
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
            if auto_trade_set_stock_operation_exclusion(
                adapter,
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
        config = read_json_dict(config_path)
        if not isinstance(config, dict):
            config = {}
        current_mode = str(config.get("trade_amount_type", "QUANTITY") or "").upper()
        next_mode = "QUANTITY" if current_mode == "AMOUNT" else "AMOUNT"
        result = execute_budget_mode_change(
            self,
            BudgetModeChangeRequest(
                config_path=config_path,
                target_mode=next_mode,
            ),
            writer=self._write_stock_initial_buy_config,
            current_running_reader=(
                lambda host, _code, _config, _state: (
                    getattr(
                        host,
                        "_stock_start_budget_locked",
                        MainWindow._stock_start_budget_locked,
                    )(config_path)
                )
            ),
        )
        if result.get("reason") == CURRENT_PRICE_UNAVAILABLE:
            show_toast(
                self,
                MainWindow._start_budget_price_unavailable_message(),
            )
            return
        if result.get("reason") == "START_BUDGET_MUTATION_BLOCKED":
            self._show_start_budget_mutation_blocked(self)
        elif result.get("allowed") is not True:
            MainWindow._show_start_budget_config_write_failure(
                self,
                reason_code=str(result.get("reason_code") or result.get("reason") or ""),
                runtime_committed=False,
            )
        if result.get("allowed") is True and result.get("changed") is True:
            refresh_auto_trade_views(self)

    def open_routine_stock_initial_buy_dialog(self, row: int) -> None:
        if not self._main_routine_initial_buy_badge_enabled():
            return
        config_path = self._stock_config_path_for_routine_row(row)
        if config_path is None:
            return
        self._open_running_budget_adjustment_dialog(row, config_path)

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
        if not isinstance(config, dict):
            config = {}
        if stock_buy_limit_state(
            enabled=bool(config.get("buy_limit_enabled", False)),
            amount=config.get("buy_limit_amount"),
        ) != "CONFIGURED":
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

    def start_routine_stock_buy_limit_edit(
        self,
        row: int,
        *,
        use_suggested_amount: bool = True,
    ) -> None:
        item = self.routine_table.item(row, 0)
        stock_path = (
            str(item.data(ROUTINE_STOCK_PATH_ROLE) or "").strip()
            if item is not None
            else ""
        )
        config_path = self._stock_config_path_for_routine_row(row)
        if config_path is None:
            return
        if MainWindow._starting_budget_change_current_price(
            self,
            config_path,
        ) is None:
            show_toast(
                self,
                MainWindow._start_budget_price_unavailable_message(),
            )
            return
        config = read_json_dict(config_path)
        if not isinstance(config, dict):
            config = {}
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
        suggested_amount = None
        if use_suggested_amount:
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
        self._routine_stock_buy_limit_editor_expected_fields = (
            MainWindow._stock_config_expected_fields(
                config,
                (
                    "buy_limit_enabled",
                    "buy_limit_amount",
                    "buy_limit_source",
                ),
            )
        )

    def handle_routine_stock_buy_limit_double_click(self, row: int) -> None:
        config_path = self._stock_config_path_for_routine_row(row)
        if config_path is None:
            return
        config = read_json_dict(config_path)
        if not isinstance(config, dict):
            config = {}
        limit_state = stock_buy_limit_state(
            enabled=bool(config.get("buy_limit_enabled", False)),
            amount=config.get("buy_limit_amount"),
        )

        self.finish_routine_instance_buy_limit_edit(save=True)
        self.finish_routine_stock_buy_limit_edit(save=False)

        if limit_state in {"WAITING", "CONFIGURED"}:
            write_result = self._write_stock_buy_limit_config(
                config_path,
                enabled=False,
                amount=None,
                source=None,
            )
            if not MainWindow._stock_config_write_succeeded(write_result):
                MainWindow._show_stock_buy_limit_write_failure(self, write_result)
            elif bool(getattr(write_result, "changed", False)):
                refresh_auto_trade_views(self)
            return

        recommended = self._stock_suggested_buy_limit(
            config_path,
            window=self,
        )
        if recommended is None:
            reason_code = MainWindow._stock_suggested_buy_limit_failure_reason(
                config_path,
                window=self,
            )
            show_toast(
                self,
                MainWindow._stock_buy_limit_failure_message(reason_code),
            )
            return
        total_budget = _system_total_budget_amount()
        if recommended <= 0:
            show_toast(
                self,
                MainWindow._stock_buy_limit_failure_message(
                    "INVALID_STARTING_BUDGET"
                ),
            )
            return
        if total_budget is None:
            show_toast(
                self,
                MainWindow._stock_buy_limit_failure_message(
                    "TOTAL_BUDGET_UNAVAILABLE"
                ),
            )
            return
        minimum_amount = self._stock_suggested_buy_limit(
            config_path,
            minimum=True,
            window=self,
        )
        if minimum_amount is None:
            show_toast(
                self,
                MainWindow._stock_buy_limit_failure_message(
                    "LIMIT_DEFAULTS_INVALID"
                ),
            )
            return
        if minimum_amount is not None and minimum_amount > total_budget:
            show_toast(
                self,
                MainWindow._stock_buy_limit_failure_message(
                    "MINIMUM_BUY_LIMIT_EXCEEDS_TOTAL_BUDGET"
                ),
                duration_ms=2500,
            )
            return
        if recommended > total_budget:
            show_toast(
                self,
                MainWindow._stock_buy_limit_failure_message(
                    "RECOMMENDED_BUY_LIMIT_EXCEEDS_TOTAL_BUDGET"
                ),
                duration_ms=2500,
            )
            self.start_routine_stock_buy_limit_edit(
                row,
                use_suggested_amount=False,
            )
            return
        write_result = self._write_stock_buy_limit_config(
            config_path,
            enabled=True,
            amount=recommended,
            source=BUY_LIMIT_SOURCE_RECOMMENDED,
        )
        if not MainWindow._stock_config_write_succeeded(write_result):
            MainWindow._show_stock_buy_limit_write_failure(self, write_result)
        elif bool(getattr(write_result, "changed", False)):
            refresh_auto_trade_views(self)

    def finish_routine_stock_buy_limit_edit(self, *, save: bool) -> None:
        editor = self._routine_stock_buy_limit_editor
        if editor is None or self._routine_stock_buy_limit_edit_finishing:
            return
        config_path_text = self._routine_stock_buy_limit_editor_config_path
        expected_fields = getattr(
            self,
            "_routine_stock_buy_limit_editor_expected_fields",
            None,
        )
        amount = self._parse_buy_limit_amount(editor.text()) if save else None

        def close_editor() -> None:
            self._routine_stock_buy_limit_edit_finishing = True
            self._routine_stock_buy_limit_editor = None
            self._routine_stock_buy_limit_editor_config_path = ""
            self._routine_stock_buy_limit_editor_expected_fields = None
            self.routine_table._editing_stock_buy_limit_path = ""
            editor.hide()
            editor.deleteLater()
            self._routine_stock_buy_limit_edit_finishing = False
            self.routine_table.viewport().update()

        if not save:
            close_editor()
            return
        config_path = Path(config_path_text)
        if amount is None:
            close_editor()
            return
        current_config = read_json_dict(config_path)
        if not isinstance(current_config, dict):
            current_config = {}
        current_amount = self._parse_buy_limit_amount(
            str(current_config.get("buy_limit_amount") or "")
        )
        had_configured_limit = (
            stock_buy_limit_state(
                enabled=bool(current_config.get("buy_limit_enabled", False)),
                amount=current_config.get("buy_limit_amount"),
            )
            == "CONFIGURED"
            and current_amount is not None
        )

        def restore_after_invalid_input() -> None:
            close_editor()
            if not had_configured_limit:
                write_result = self._write_stock_buy_limit_config(
                    config_path,
                    enabled=False,
                    amount=None,
                    source=None,
                )
                if not MainWindow._stock_config_write_succeeded(write_result):
                    MainWindow._show_stock_buy_limit_write_failure(
                        self,
                        write_result,
                    )
                elif bool(getattr(write_result, "changed", False)):
                    refresh_auto_trade_views(self)

        if (
            bool(current_config.get("buy_limit_enabled", False))
            and current_amount == amount
        ):
            close_editor()
            return
        minimum_amount = self._stock_suggested_buy_limit(
            config_path,
            minimum=True,
            window=self,
        )
        if minimum_amount is None:
            reason_code = MainWindow._stock_suggested_buy_limit_failure_reason(
                config_path,
                window=self,
            )
            show_toast(
                self,
                MainWindow._stock_buy_limit_failure_message(reason_code),
            )
            close_editor()
            return
        total_budget = _system_total_budget_amount()
        if total_budget is None:
            show_toast(
                self,
                MainWindow._stock_buy_limit_failure_message(
                    "TOTAL_BUDGET_UNAVAILABLE"
                ),
            )
            close_editor()
            return
        if amount > total_budget:
            show_toast(
                self,
                "입력한 한도금액이 전체예산을 초과합니다.",
                duration_ms=2500,
            )
            restore_after_invalid_input()
            return
        if minimum_amount is not None and amount < minimum_amount:
            show_toast(
                self,
                f"종목 한도는 현재 최소 금액 {minimum_amount:,}원 이상이어야 합니다.",
                duration_ms=2500,
            )
            restore_after_invalid_input()
            return
        try:
            close_editor()
            writer_kwargs = {
                "enabled": True,
                "amount": amount,
                "source": BUY_LIMIT_SOURCE_MANUAL,
            }
            if isinstance(expected_fields, dict):
                writer_kwargs["expected_fields"] = expected_fields
            write_result = self._write_stock_buy_limit_config(
                config_path,
                **writer_kwargs,
            )
        except Exception:
            show_toast(
                self,
                "한도금액을 저장하지 못했습니다.",
                duration_ms=2500,
            )
            return
        if not MainWindow._stock_config_write_succeeded(write_result):
            MainWindow._show_stock_buy_limit_write_failure(self, write_result)
            self.load_routine_table()
            return
        if bool(getattr(write_result, "changed", False)):
            refresh_auto_trade_views(self)

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
        refresh_auto_trade_views(self)

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
            refresh_auto_trade_views(self)
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
        refresh_auto_trade_views(self)

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

    @staticmethod
    def _projected_stock_dirs(stock_paths) -> list[Path]:
        result: list[Path] = []
        seen: set[str] = set()
        for stock_path in stock_paths:
            text = str(stock_path or "").strip()
            if not text:
                continue
            path = Path(text)
            if not path.is_absolute():
                path = PROJECT_ROOT / path
            key = str(path.resolve())
            if key in seen or not path.is_dir():
                continue
            seen.add(key)
            result.append(path)
        return result

    def _projected_group_instance_stock_dirs(
        self,
        group_id: str,
        instance_id: str,
    ) -> list[Path]:
        relation_id = main_group_instance_relation_id(group_id, instance_id)
        return MainWindow._projected_stock_dirs(
            self._routine_stock_paths_by_group_instance.get(relation_id, ())
        )

    def _projected_group_stock_dirs(self, group_id: str) -> list[Path]:
        return MainWindow._projected_stock_dirs(
            self._routine_stock_paths_by_group.get(str(group_id or "").strip(), ())
        )

    def _running_routine_operation_targets(
        self,
        instance_ids,
        *,
        stock_paths=None,
    ) -> list[MainMonitoringStockTarget]:
        running_by_path = {
            str(Path(stock_dir).resolve()): (Path(stock_dir), code, name)
            for stock_dir, code, name in auto_trade_running_registered_operation_targets(
                self
            )
        }
        targets: list[MainMonitoringStockTarget] = []
        seen_paths: set[str] = set()
        allowed_paths = (
            {
                str(path.resolve())
                for path in MainWindow._projected_stock_dirs(stock_paths)
            }
            if stock_paths is not None
            else None
        )
        for instance_id in instance_ids:
            clean_instance_id = str(instance_id or "").strip()
            if not clean_instance_id:
                continue
            for stock_dir in self._routine_instance_stock_dirs(clean_instance_id):
                stock_path = str(Path(stock_dir).resolve())
                if allowed_paths is not None and stock_path not in allowed_paths:
                    continue
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

    def register_group_pack_from_file(self) -> None:
        pack_path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "그룹등록",
            "",
            "Group Pack (*.group.zip)",
        )
        if not pack_path:
            return
        result = register_group_pack(pack_path, project_root=PROJECT_ROOT)
        if not result.success or result.group is None:
            QMessageBox.warning(
                self,
                "그룹등록 실패",
                result.error or "Group Pack을 등록하지 못했습니다.",
            )
            return
        try:
            self.refresh_auto_trade_assignment_views()
        except Exception:
            LOGGER.exception(
                "Group Pack registration persisted but view refresh failed: %s",
                result.group.group_id,
            )
        show_toast(
            self,
            f"{result.group.display_name} 그룹을 등록했습니다.",
            duration_ms=2500,
        )

    def pack_routine_group(self, group_id: str) -> bool:
        group = getattr(self, "_routine_group_records_by_id", {}).get(group_id)
        if group is None:
            group = group_record_by_id(group_id)
        if group is None:
            QMessageBox.warning(self, "그룹패킹 실패", "선택한 Group을 찾을 수 없습니다.")
            return False
        base_name = str(getattr(group, "base_name", "") or "").strip()
        output_path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "그룹패킹",
            f"{base_name}.group.zip",
            "Group Pack (*.group.zip)",
        )
        if not output_path:
            return False
        result = pack_group(
            str(getattr(group, "group_id", "") or "").strip(),
            output_path,
            project_root=PROJECT_ROOT,
        )
        if not result.success:
            QMessageBox.warning(
                self,
                "그룹패킹 실패",
                result.error or "그룹팩을 생성하지 못했습니다.",
            )
            return False
        show_toast(
            self,
            f"{base_name} 그룹팩을 생성했습니다.",
            duration_ms=2500,
        )
        return True

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
        result = auto_trade_apply_selected_early_close(
            adapter,
            method,
            source="main_visible_early_close_button",
            selected=adapter.selected_stock_infos(),
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

    def toggle_projected_routine_instance_operation(
        self,
        group_id: str,
        instance_id: str,
    ) -> None:
        self.toggle_routine_instance_operation(
            instance_id,
            stock_dirs=self._projected_group_instance_stock_dirs(
                group_id,
                instance_id,
            ),
        )

    def toggle_routine_instance_operation(
        self,
        instance_id: str,
        *,
        stock_dirs=None,
    ) -> None:
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

        projected_stock_dirs = (
            list(stock_dirs)
            if stock_dirs is not None
            else self._routine_instance_stock_dirs(instance_id)
        )
        targets: list[MainMonitoringStockTarget] = []
        operational_targets: list[MainMonitoringStockTarget] = []
        current_running_stock_dirs = {
            str(Path(stock_dir).resolve())
            for stock_dir, _code, _name in (
                auto_trade_running_registered_operation_targets(self)
            )
        }
        for stock_dir in projected_stock_dirs:
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
            if (
                is_review_required_state(state)
                or recovery_stock_is_review_required(code)
            ):
                continue
            if str(stock_dir.resolve()) not in current_running_stock_dirs:
                operational_targets.append(target)

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

        adapter = MainMonitoringStockOperationAdapter(
            self,
            operational_targets,
            request_scope="multiple",
            recovery_action_label="루틴 재시작",
        )
        self._main_monitoring_stock_operation_adapter = adapter
        requested_action = "운영시작"
        try:
            operation_result = execute_main_monitoring_selective_start(
                adapter,
                source="main_routine_start",
            )
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
            str(target.stock_dir.resolve()) in running_after_stock_dirs
            for target in operational_targets
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
        show_auto_trade_operation_failure_dialog(
            adapter,
            requested_action,
            operation_result,
            adapter._targets,
        )

    @staticmethod
    def _set_routine_operation_actions_enabled(actions, enabled: bool) -> None:
        unavailable_reason = "등록된 종목이 없어 실행할 수 없습니다."
        for action in actions:
            action.setEnabled(enabled)
            action.setStatusTip("" if enabled else unavailable_reason)
            action.setToolTip("" if enabled else unavailable_reason)

    def open_routine_registration_from_main_group(self, group_id: str) -> bool:
        group = getattr(self, "_routine_group_records_by_id", {}).get(group_id)
        if group is None:
            group = group_record_by_id(group_id)
        group_display_name = str(
            getattr(group, "display_name", "") or ""
        ).strip()
        if not group_display_name:
            show_toast(self, "선택한 그룹을 확인할 수 없습니다.")
            return False

        definition_id = str(getattr(group, "definition_id", "") or "").strip()
        definition = routine_definition_by_id(definition_id) if definition_id else None
        if definition is None:
            show_toast(self, "선택한 그룹의 등록 설정을 확인할 수 없습니다.")
            return False

        open_routine_settings_dialog_for_owner(
            self,
            {
                "row_kind": "definition",
                "definition_id": definition_id,
                "definition_name": str(
                    getattr(definition, "display_name", "") or ""
                ).strip(),
                "group_display_name": group_display_name,
                "group_id": group_id,
            },
            registration=True,
        )
        return True

    def clone_routine_instance_from_main_group(
        self,
        group_id: str,
        instance_id: str,
    ) -> bool:
        owning_group_ids = {
            candidate_group_id
            for candidate_group_id, instance_ids in self._routine_instance_ids_by_group.items()
            if instance_id in instance_ids
        }
        if owning_group_ids != {group_id}:
            QMessageBox.warning(
                self,
                "루틴 복제",
                "원본 루틴의 Group 귀속을 하나로 확인할 수 없어 복제할 수 없습니다.",
            )
            return False

        group = getattr(self, "_routine_group_records_by_id", {}).get(group_id)
        if group is None:
            group = group_record_by_id(group_id)
        return clone_routine_instance_with_existing_policy(
            self,
            {
                "row_kind": "instance",
                "group_id": group_id,
                "instance_id": instance_id,
            },
            owning_group_ids=owning_group_ids,
            group_record=group,
        )

    def delete_routine_group_completely(
        self,
        group_id: str,
        group_name: str,
    ) -> bool:
        clean_group_name = str(group_name or "").strip()
        answer = QMessageBox.question(
            self,
            "그룹삭제",
            f"{clean_group_name} 그룹을 완전삭제 하겠습니까?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return False

        try:
            scope = collect_group_deletion_scope(PROJECT_ROOT, group_id)
        except Exception as exc:
            QMessageBox.warning(self, "그룹삭제", str(exc))
            return False

        try:
            running_stock_dirs = [
                stock_dir
                for stock_dir, _code, _name in auto_trade_running_registered_operation_targets(
                    self
                )
            ]
        except Exception as exc:
            QMessageBox.warning(
                self,
                "그룹삭제",
                f"현재 운영 종목을 확인하지 못해 그룹을 삭제할 수 없습니다.\n{exc}",
            )
            return False

        result = delete_group_completely(
            PROJECT_ROOT,
            scope,
            can_unassign=can_unassign_active_routine_from_stock,
            running_stock_dirs=running_stock_dirs,
        )
        if not result.success:
            reason = result.error
            if result.blocked_reasons:
                reason = "\n".join(result.blocked_reasons)
            QMessageBox.warning(
                self,
                "그룹삭제",
                reason or "현재 상태에서는 그룹을 삭제할 수 없습니다.",
            )
            return False

        append_production_event(
            "ROUTINE_GROUP_COMPLETELY_DELETED",
            result="COMPLETED",
            source="gui_windows.MainWindow.delete_routine_group_completely",
            target_type="ROUTINE_GROUP",
            target_id=str(group_id or "").strip(),
            target_name=clean_group_name,
            routine=clean_group_name,
            details={
                "deleted_instance_ids": list(result.deleted_instance_ids),
                "cleared_stock_codes": list(result.cleared_stock_codes),
                "assignment_transitions": [
                    {
                        "stock_code": stock.code,
                        "before_instance_id": stock.assigned_routine_instance_id,
                        "after": "UNASSIGNED",
                    }
                    for stock in scope.stocks
                    if stock.code in set(result.cleared_stock_codes)
                ],
            },
        )
        if result.cleared_stock_codes:
            sync_auto_trade_monitoring_universe(self)
        self.refresh_auto_trade_assignment_views()
        show_toast(
            self,
            f"{clean_group_name} 그룹을 완전삭제 하였습니다.",
        )
        return True

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
            group_id = str(first_item.data(ROUTINE_GROUP_ID_ROLE) or "").strip()
            group_path = str(first_item.data(ROUTINE_GROUP_PATH_ROLE) or "").strip()
            group_name = str(first_item.data(ROUTINE_PARENT_NAME_ROLE) or "").strip()
            if not group_id or not group_path:
                QMessageBox.warning(
                    self,
                    "루틴 운영",
                    "선택한 그룹을 확인할 수 없습니다.",
                )
                return
            menu = QMenu(self.routine_table)
            menu.setToolTipsVisible(True)
            register_action = menu.addAction("루틴등록")
            menu.addSeparator()
            delete_group_action = menu.addAction("그룹삭제")
            delete_group_action.setEnabled(True)
            packing_action = menu.addAction("그룹패킹")
            packing_action.setEnabled(True)
            register_action.triggered.connect(
                lambda _checked=False: self.open_routine_registration_from_main_group(
                    group_id
                )
            )
            delete_group_action.triggered.connect(
                lambda _checked=False: self.delete_routine_group_completely(
                    group_id,
                    group_name,
                )
            )
            packing_action.triggered.connect(
                lambda _checked=False: self.pack_routine_group(group_id)
            )
            menu.exec_(self.routine_table.viewport().mapToGlobal(position))
            return
        if row_kind != ROUTINE_ROW_CHILD:
            return
        instance_id = str(first_item.data(ROUTINE_INSTANCE_ID_ROLE) or "").strip()
        group_id = str(first_item.data(ROUTINE_GROUP_ID_ROLE) or "").strip()
        if not instance_id or not group_id:
            return
        instance = routine_instance_by_id(instance_id)
        if instance is None:
            QMessageBox.warning(self, "루틴 운영", "선택한 등록 루틴을 확인할 수 없습니다.")
            return

        menu = QMenu(self.routine_table)
        menu.setToolTipsVisible(True)
        settings_action = menu.addAction("설정변경")
        clone_action = menu.addAction("루틴복제")
        delete_action = menu.addAction("루틴삭제")
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
        clone_action.triggered.connect(
            lambda _checked=False, target_group_id=group_id, target_id=instance_id: (
                self.clone_routine_instance_from_main_group(
                    target_group_id,
                    target_id,
                )
            )
        )
        delete_action.triggered.connect(
            lambda _checked=False, target_id=instance_id, target_name=instance.display_name: self.delete_routine_instance_from_main_table(
                target_id,
                target_name,
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
            bool(
                self._routine_stock_paths_by_group_instance.get(
                    main_group_instance_relation_id(group_id, instance_id),
                    (),
                )
            ),
        )
        early_close_action.triggered.connect(
            lambda _checked=False: self.request_routine_operation(
                instance_id,
                instance.display_name,
                "루틴",
                ROUTINE_STATUS_EARLY_CLOSE,
                stock_paths=self._routine_stock_paths_by_group_instance.get(
                    main_group_instance_relation_id(group_id, instance_id),
                    (),
                ),
            )
        )
        immediate_action.triggered.connect(
            lambda _checked=False: self.request_routine_operation(
                instance_id,
                instance.display_name,
                POLICY_MARKET,
                ROUTINE_STATUS_IMMEDIATE_LIQUIDATION,
                stock_paths=self._routine_stock_paths_by_group_instance.get(
                    main_group_instance_relation_id(group_id, instance_id),
                    (),
                ),
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

    def delete_routine_instance_from_main_table(
        self,
        instance_id: str,
        instance_name: str,
    ) -> None:
        delete_routine_instance_with_existing_policy(
            self,
            {
                "row_kind": "instance",
                "instance_id": str(instance_id or "").strip(),
                "instance_name": str(instance_name or "").strip(),
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
        self.instance_stock_search_register_window = open_instance_stock_search_register_dialog(
            self,
            metadata,
        )

    def request_routine_definition_operation(
        self,
        definition_id: str,
        display_name: str,
        requested_policy: str,
        display_status: str,
        *,
        instance_ids_override=None,
        stock_paths=None,
        target_type: str = "ROUTINE_DEFINITION",
        scope_label: str = "카테고리",
        event_source: str = "gui_windows.MainWindow.request_routine_definition_operation",
    ) -> None:
        candidate_ids = (
            tuple(instance_ids_override)
            if instance_ids_override is not None
            else self._routine_instance_ids_by_definition.get(definition_id, ())
        )
        instance_ids = tuple(
            instance_id
            for instance_id in sorted(candidate_ids)
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

        targets = MainWindow._running_routine_operation_targets(
            self,
            instance_ids,
            stock_paths=stock_paths,
        )
        if not targets:
            message = f"{command_label} 대상이 없습니다."
            show_toast(self, message, duration_ms=2500)
            self.statusBar().showMessage(
                f"{scope_label} {command_label}: {display_name} / 대상 0"
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
            source=event_source,
            target_type=target_type,
            target_id=definition_id,
            target_name=display_name,
            routine=display_name,
            details={
                "interaction_type": "CONFIRM",
                "prompt_key": f"{target_type}_EARLY_CLOSE_CONFIRM",
                "prompt_title": "즉시청산" if market_requested else "조기마감",
                "prompt_summary": f"{scope_label} 조기마감 적용",
                "offered_options": ["진행", "취소"],
                "selected_option": "진행" if accepted else "취소",
                "operation": "EARLY_CLOSE",
                "method": "market" if market_requested else "routine",
            },
        )
        if answer != QMessageBox.Yes:
            self.statusBar().showMessage(
                f"{scope_label} {command_label} 취소: {display_name}"
            )
            return

        adapter = MainMonitoringStockOperationAdapter(
            self,
            targets,
            request_scope="multiple",
        )
        self._main_monitoring_stock_operation_adapter = adapter
        result = auto_trade_apply_selected_early_close(
            adapter,
            requested_policy,
            source="main_routine_parent_context_menu",
            selected=adapter.selected_stock_infos(),
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
            f"{scope_label} {command_label}: {display_name} / 성공 {applied_count} / "
            f"차단 {failed_count}"
        )

    def request_routine_group_operation(
        self,
        group_id: str,
        display_name: str,
        requested_policy: str,
        display_status: str,
    ) -> None:
        self.request_routine_definition_operation(
            group_id,
            display_name,
            requested_policy,
            display_status,
            instance_ids_override=self._routine_instance_ids_by_group.get(
                group_id,
                (),
            ),
            stock_paths=self._routine_stock_paths_by_group.get(group_id, ()),
            target_type="ROUTINE_GROUP",
            scope_label="그룹",
            event_source="gui_windows.MainWindow.request_routine_group_operation",
        )

    def request_routine_operation(
        self,
        instance_id: str,
        display_name: str,
        requested_policy: str,
        display_status: str,
        *,
        stock_paths=None,
    ) -> None:
        command_label = display_status
        market_requested = requested_policy == POLICY_MARKET
        targets = MainWindow._running_routine_operation_targets(
            self,
            (instance_id,),
            stock_paths=stock_paths,
        )
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
        result = auto_trade_apply_selected_early_close(
            adapter,
            requested_policy,
            source="main_routine_context_menu",
            selected=adapter.selected_stock_infos(),
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

    def _build_stock_instance_chart_operation_adapter(
        self,
        stock_dir: Path,
        code: str,
        name: str,
        routine_instance_id: str,
    ) -> MainMonitoringStockOperationAdapter:
        return MainMonitoringStockOperationAdapter(
            self,
            [
                MainMonitoringStockTarget(
                    stock_dir=stock_dir,
                    code=code,
                    name=name,
                    routine_instance_id=routine_instance_id,
                )
            ],
            request_scope="single",
        )

    def _execute_stock_instance_chart_operation(
        self,
        adapter: MainMonitoringStockOperationAdapter,
        requested_policy: str,
        **kwargs,
    ) -> dict[str, object]:
        return auto_trade_apply_selected_early_close(
            adapter,
            requested_policy,
            **kwargs,
        )

    def open_stock_register_window(self) -> None:
        window = getattr(self, "stock_register_window", None)
        if window is not None and not sip.isdeleted(window) and window.isVisible():
            window.show()
            window.raise_()
            window.activateWindow()
            return
        window = StockRegisterWindow(
            self,
            stock_search_register_opener=open_instance_stock_search_register_dialog,
        )
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

    def open_market_data_monitoring_window(self) -> QDialog:
        window = getattr(self, "market_data_monitoring_window", None)
        if window is not None and sip.isdeleted(window):
            self.market_data_monitoring_window = None
            window = None
        if window is None:
            window = _MarketDataMonitoringWindow(
                self,
                self.main_monitoring_auto_trade_operation_host(),
            )
            window.setAttribute(Qt.WA_DeleteOnClose, True)
            self.market_data_monitoring_window = window
            window.destroyed.connect(
                lambda _obj=None, target=window: (
                    setattr(self, "market_data_monitoring_window", None)
                    if getattr(self, "market_data_monitoring_window", None) is target
                    else None
                )
            )
        else:
            window.refresh_snapshot()
        if window.isMinimized():
            window.showNormal()
        window.show()
        window.raise_()
        window.activateWindow()
        return window

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
        marker_result = chejan_result.get("close_routine_final_sell_marker")
        if (
            isinstance(marker_result, dict)
            and marker_result.get("changed") is True
        ):
            refresh_auto_trade_views(self)
        try:
            window = getattr(self, "auto_trade_setting_window", None)
        except RuntimeError:
            window = None
        if window is not None:
            setattr(window, "last_chejan_record_result", self.last_chejan_record_result)
        if not getattr(self, "_main_window_closing", False):
            fill_result = chejan_result.get("fill_result")
            position_result = chejan_result.get("position_result")
            durable_fill_available = (
                isinstance(fill_result, dict)
                and fill_result.get("fill_recorded") is True
            ) or isinstance(position_result, dict)
            durable_holding_available = chejan_result.get("holding_recorded") is True
            if durable_fill_available or durable_holding_available:
                normalized_event = chejan_result.get("normalized_event")
                normalized_code = (
                    normalized_event.get("code")
                    if isinstance(normalized_event, dict)
                    else None
                )
                queue_open_stock_instance_chart_refresh(
                    chejan_result.get("code") or normalized_code
                )

    def _main_exit_warning_required(self, now_dt: datetime | None = None) -> bool:
        """Warn when this GUI session has an active operation lifecycle."""

        try:
            running_targets = list(
                auto_trade_running_registered_operation_targets(self)
            )
        except Exception:
            LOGGER.exception("Main exit current-running projection failed")
            return True
        return bool(running_targets)

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
        clear_pending_stock_instance_chart_refreshes()
        MainWindow._clear_completed_recovery_handoff(self)
        monitoring_window = getattr(self, "market_data_monitoring_window", None)
        if monitoring_window is not None and not sip.isdeleted(monitoring_window):
            monitoring_window.close()
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
            MainWindow._update_main_routine_summary_badge_styles(self)
            return
        window = GlobalReviewRequiredWindow(self)
        window.setAttribute(Qt.WA_DeleteOnClose, True)
        self.review_required_window = window
        window.finished.connect(
            lambda _result=0: MainWindow._update_main_routine_summary_badge_styles(
                self
            )
        )
        window.destroyed.connect(
            lambda _obj=None, target=window: MainWindow._on_review_required_window_destroyed(
                self,
                target,
            )
        )
        window.show()
        window.raise_()
        window.activateWindow()
        MainWindow._update_main_routine_summary_badge_styles(self)

    def _on_review_required_window_destroyed(self, target) -> None:
        if getattr(self, "review_required_window", None) is target:
            self.review_required_window = None
        MainWindow._update_main_routine_summary_badge_styles(self)

    def open_event_record_window(self) -> None:
        open_event_record_prototype(self)

    def close_all_persistent_feature_windows(self) -> None:
        """Close persistent feature windows without closing MainWindow."""

        close_persistent_feature_windows(self)
