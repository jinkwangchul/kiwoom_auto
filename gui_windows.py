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
from pathlib import Path

from PyQt5.QtCore import QEvent, QObject, QRect, Qt
from PyQt5.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QGroupBox,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QComboBox,
    QTableWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionButton,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from gui_stock_register_window import StockRegisterWindow
from gui_review_required_window import GlobalReviewRequiredWindow
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
    ROUTINE_CHECKBOX_HIT_PADDING,
    ROUTINE_CHECKBOX_VISUAL_ENABLED_ROLE,
    ROUTINE_CHECKBOX_SIZE,
    ROUTINE_CHILD_COLLAPSED_ROLE,
    ROUTINE_CHILD_CHECKBOX_OFFSET,
    ROUTINE_CHILD_HAS_STOCKS_ROLE,
    ROUTINE_CHILD_PROFIT_LED_ROLE,
    ROUTINE_INSTANCE_ID_ROLE,
    ROUTINE_INSTANCE_NAME_WIDTH,
    ROUTINE_INSTANCE_MONEY_OUTER_PADDING,
    ROUTINE_MONITORING_HEADERS,
    ROUTINE_PARENT_AGGREGATE_ROLE,
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
    ROUTINE_STOCK_CHECKBOX_OFFSET,
    MAIN_STOCK_METRIC_LAYOUT_PREVIEW,
    ROUTINE_STOCK_METRICS_ROLE,
    ROUTINE_STOCK_PATH_ROLE,
    ROUTINE_STOCK_PROFIT_LED_ROLE,
    ROUTINE_STOCK_TEXT_OFFSET,
    ROUTINE_STOCK_VALUES_ROLE,
    routine_instance_separator_width,
    routine_instance_number_widths,
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
    routine_instance_buy_limit_configured,
)
from gui_main_budget_panel import update_main_budget_panel
from gui_auto_trade_display import (
    draw_limit_metric,
    draw_stock_position_metric,
    draw_stock_position_metric_display,
)
from runtime_io import read_json_dict
from gui_auto_trade_setting_window import (
    AutoTradeSettingWindow,
    get_routine_dirs,
    get_stock_dirs_in_routine,
    handle_kiwoom_raw_chejan_event,
    is_review_required_state,
    normalize_base_stock_single_routine_file,
    routine_display_name,
)
from gui_routine_registry import routine_record_by_name
from routine_instance_registry import routine_definition_by_id, routine_instance_by_id
from routine_instance_repository import RoutineInstanceRepository
from stock_repository import now_text as stock_now_text
from gui_main_routine_selection import (
    routine_definition_enabled,
    routine_instance_checkbox_enabled,
    routine_instance_checked,
    selected_routine_instance_ids,
    toggle_routine_definition,
    toggle_routine_instance,
)
from kiwoom_api import KiwoomApi
from operator_reconciliation_service import assess_startup_recovery
from operation_command_service import (
    COMMAND_IMMEDIATE_LIQUIDATION,
    MODE_EARLY_CLOSE,
    OperationCommandRequest,
    OperationCommandService,
    RESULT_FAILED,
    RESULT_PARTIAL_SUCCESS,
    SCOPE_ROUTINE_INSTANCE,
)


PROJECT_ROOT = Path(__file__).resolve().parent
BASE_STOCK_PATH = PROJECT_ROOT / "기초종목.txt"
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
    painter.drawEllipse(led_rect)
    painter.restore()


MAIN_STOCK_METRIC_LAYOUT = {
    "separator_gap": 5,
    "separator_width": 9,
    "metrics": (
        {
            "key": "holding",
            "max_text": "\ubcf4\uc720(99999\uc8fc / 999,999,999)",
            "slot_width": 232,
        },
        {
            "key": "price",
            "max_text": "\uac00\uaca9(9,999,999 / 9,999,999)",
            "slot_width": 226,
        },
        {
            "key": "profit",
            "max_text": "\uc190\uc775(-99,999,999 / -00.00%)",
            "slot_width": 232,
        },
        {
            "key": "unfilled",
            "max_text": "\ubbf8\uccb4\uacb0(99 / 99)",
            "slot_width": 120,
        },
        {
            "key": "limit",
            "max_text": "\ud55c\ub3c4(999,999,999)",
            "slot_width": 146,
        },
        {
            "key": "consumed",
            "max_text": "\uc18c\ubaa8(999,999,999 / 00.0%)",
            "slot_width": 214,
        },
    ),
}
ROUTINE_STOCK_METRIC_SEPARATOR_GAP = int(MAIN_STOCK_METRIC_LAYOUT["separator_gap"])
ROUTINE_STOCK_METRIC_SEPARATOR_WIDTH = int(MAIN_STOCK_METRIC_LAYOUT["separator_width"])
MAIN_STOCK_METRIC_MAX_TEXTS = tuple(
    str(metric["max_text"]) for metric in MAIN_STOCK_METRIC_LAYOUT["metrics"]
)
MAIN_STOCK_METRIC_SLOT_WIDTHS = tuple(
    int(metric["slot_width"]) for metric in MAIN_STOCK_METRIC_LAYOUT["metrics"]
)


def _routine_stock_metric_display_text(metric: object) -> str:
    label = str(getattr(metric, "label", "") or "").strip()
    value1 = str(getattr(metric, "value1", "") or "").strip()
    value2 = str(getattr(metric, "value2", "") or "").strip()
    if not label:
        return ""
    return f"{label}({value1} / {value2})"


def _routine_stock_metric_texts_legacy_unused(values: list[object], metrics_data: tuple[object, ...]) -> list[str]:
    if MAIN_STOCK_METRIC_LAYOUT_PREVIEW:
        return list(MAIN_STOCK_METRIC_MAX_TEXTS)
    texts: list[str] = []
    for metric in metrics_data[:4]:
        metric_text = _routine_stock_metric_display_text(metric)
        if metric_text:
            texts.append(metric_text)
    if len(values) > 10:
        texts.append(str(values[10] or "").strip())
    if len(metrics_data) > 5:
        consumed_text = _routine_stock_metric_display_text(metrics_data[5])
        if consumed_text:
            texts.append(consumed_text)
    elif len(values) > 11:
        texts.append(str(values[11] or "").strip())
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
    if len(values) > 10:
        texts.append(str(values[10] or "").strip())
    if len(metrics_data) > 5:
        consumed_text = _routine_stock_metric_display_text(metrics_data[5])
        if consumed_text:
            texts.append(consumed_text)
    elif len(values) > 11:
        texts.append(str(values[11] or "").strip())
    return [text for text in texts if text]


def _routine_stock_metric_layout_rects(
    *,
    row_rect: QRect,
    start_x: int,
    count: int,
) -> tuple[list[QRect], list[QRect], int]:
    metric_rects: list[QRect] = []
    separator_rects: list[QRect] = []
    x = start_x
    gap = ROUTINE_STOCK_METRIC_SEPARATOR_GAP
    separator_width = ROUTINE_STOCK_METRIC_SEPARATOR_WIDTH
    for index, slot_width in enumerate(MAIN_STOCK_METRIC_SLOT_WIDTHS[:count]):
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
    if str(value).strip() in {"-", "\ubbf8\uc124\uc815"}:
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
    hidden_value_indexes: set[int] | None = None,
) -> tuple[list[tuple[str, int, int, int, int]], int]:
    metric_rects, separator_rects, end_x = _routine_stock_metric_layout_rects(
        row_rect=row_rect,
        start_x=start_x,
        count=len(texts),
    )
    component_rects = _main_stock_metric_component_layouts(painter.fontMetrics(), metric_rects)
    layout_rows: list[tuple[str, int, int, int, int]] = []
    hidden_value_indexes = hidden_value_indexes or set()
    for index, (text, metric_rect, rects) in enumerate(zip(texts, metric_rects, component_rects)):
        _draw_main_stock_metric_components(
            painter,
            text,
            rects,
            hide_left_value=index in hidden_value_indexes,
        )
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
    command: str,
    icon: QMessageBox.Icon = QMessageBox.Question,
) -> QMessageBox:
    title, message = {
        MODE_EARLY_CLOSE: ("조기마감", "조기마감을 적용합니다."),
        COMMAND_IMMEDIATE_LIQUIDATION: ("즉시청산", "즉시청산을 적용합니다."),
    }[command]
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


class _RoutineCheckBoxController(QObject):
    """Apply window-local checkbox selection without touching persisted state."""

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
            + ROUTINE_CHECKBOX_SIZE
            + 6
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
            + ROUTINE_CHECKBOX_SIZE
            + ROUTINE_PROFIT_LED_GAP
            + ROUTINE_PROFIT_LED_BOX_SIZE
            + ROUTINE_PROFIT_LED_GAP
        )
        name_left = text_left
        if bool(index.data(ROUTINE_CHILD_HAS_STOCKS_ROLE)):
            name_left += metrics.horizontalAdvance("▶") + 4
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
            + ROUTINE_CHECKBOX_SIZE
            + ROUTINE_PROFIT_LED_GAP
            + ROUTINE_PROFIT_LED_BOX_SIZE
            + ROUTINE_PROFIT_LED_GAP
        )
        return QRect(
            expand_left,
            cell_rect.top(),
            arrow_width,
            cell_rect.height(),
        )

    def _stock_metric_rect(self, index, target_column: int) -> QRect:
        if target_column == 10:
            return self._stock_main_metric_rect(index, 4)
        return self._stock_legacy_metric_rect(index, target_column)

    def _stock_main_metric_rect(self, index, metric_index: int) -> QRect:
        if metric_index < 0 or metric_index >= len(MAIN_STOCK_METRIC_SLOT_WIDTHS):
            return QRect()
        base_metric_rect = self._stock_legacy_metric_rect(index, 6)
        if base_metric_rect.isNull():
            return QRect()
        metric_rects, _separator_rects, _end_x = _routine_stock_metric_layout_rects(
            row_rect=self.table.visualRect(index),
            start_x=base_metric_rect.left() + ROUTINE_STOCK_METRIC_SEPARATOR_GAP,
            count=metric_index + 1,
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
            if column > 0:
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
                    checkbox_left = cell_rect.left() + ROUTINE_STOCK_CHECKBOX_OFFSET
                    checkbox_right = (
                        checkbox_left
                        + ROUTINE_CHECKBOX_SIZE
                        + ROUTINE_CHECKBOX_HIT_PADDING
                    )
                    if checkbox_left <= event.pos().x() <= checkbox_right:
                        if (
                            event.type() == QEvent.MouseButtonPress
                            and event.button() == Qt.LeftButton
                        ):
                            self.window.toggle_routine_stock_check_state(index.row())
                        event.accept()
                        return True
                    if (
                        event.type() == QEvent.MouseButtonDblClick
                        and event.button() == Qt.LeftButton
                        and self._stock_metric_rect(index, 10).contains(event.pos())
                    ):
                        self.window.handle_routine_stock_buy_limit_double_click(index.row())
                        event.accept()
                        return True
                    return super().eventFilter(watched, event)
                if row_kind not in {ROUTINE_ROW_PARENT, ROUTINE_ROW_CHILD}:
                    return super().eventFilter(watched, event)
                checkbox_offset = (
                    ROUTINE_CHILD_CHECKBOX_OFFSET
                    if row_kind == ROUTINE_ROW_CHILD
                    else ROUTINE_PARENT_CHECKBOX_OFFSET
                )
                checkbox_left = cell_rect.left() + checkbox_offset
                checkbox_right = (
                    checkbox_left
                    + ROUTINE_CHECKBOX_SIZE
                    + ROUTINE_CHECKBOX_HIT_PADDING
                )
                if checkbox_left <= event.pos().x() <= checkbox_right:
                    if (
                        event.type() == QEvent.MouseButtonPress
                        and event.button() == Qt.LeftButton
                    ):
                        instance_id = str(
                            index.data(ROUTINE_INSTANCE_ID_ROLE) or ""
                        ).strip()
                        if row_kind == ROUTINE_ROW_PARENT or (
                            row_kind == ROUTINE_ROW_CHILD
                            and routine_instance_checkbox_enabled(
                                self.window,
                                instance_id,
                            )
                        ):
                            self.window.toggle_routine_check_state(index.row())
                    event.accept()
                    return True
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
                    if row_kind == ROUTINE_ROW_PARENT:
                        self.window.open_routine_settings_from_main_table(
                            self.table.item(index.row(), 0)
                        )
                    elif (
                        row_kind == ROUTINE_ROW_CHILD
                        and self._child_name_rect(index).contains(event.pos())
                    ):
                        self.window.start_routine_instance_name_edit(index.row())
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
        if (
            object_name == "routineInstanceBuyLimitAmount"
            and event.type() == QEvent.MouseButtonDblClick
            and event.button() == Qt.LeftButton
        ):
            self.window.handle_routine_instance_buy_limit_double_click(watched)
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


class _RoutineTreeItemDelegate(QStyledItemDelegate):
    """Paint the first-column hierarchy without text-based indentation."""

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
            checkbox_rect = QRect(
                option.rect.left() + ROUTINE_STOCK_CHECKBOX_OFFSET,
                option.rect.top() + (option.rect.height() - ROUTINE_CHECKBOX_SIZE) // 2,
                ROUTINE_CHECKBOX_SIZE,
                ROUTINE_CHECKBOX_SIZE,
            )
            checkbox_option = QStyleOptionButton()
            checkbox_option.rect = checkbox_rect
            checked = index.data(Qt.CheckStateRole) == Qt.Checked
            checkbox_option.state = (
                QStyle.State_Enabled if visually_enabled else QStyle.State_None
            ) | (QStyle.State_On if checked else QStyle.State_Off)
            if not visually_enabled:
                painter.setOpacity(0.45)
            style.drawPrimitive(
                QStyle.PE_IndicatorCheckBox,
                checkbox_option,
                painter,
                option.widget,
            )
            painter.setOpacity(1.0)
            if not visually_enabled:
                painter.setPen(QColor("#9ca3af"))
            elif option.state & QStyle.State_Selected:
                painter.setPen(option.palette.highlightedText().color())
            else:
                painter.setPen(option.palette.text().color())
            values = index.data(ROUTINE_STOCK_VALUES_ROLE)
            if not isinstance(values, (list, tuple)):
                values = [self.display_text(index, option.widget)]
            metrics_data = index.data(ROUTINE_STOCK_METRICS_ROLE)
            if not isinstance(metrics_data, (list, tuple)):
                metrics_data = ()
            x = option.rect.left() + ROUTINE_STOCK_TEXT_OFFSET
            separator_width = routine_instance_separator_width(painter.font())
            stock_column_widths = routine_stock_column_widths(painter.font())
            stock_position_value_widths = routine_stock_position_value_widths(painter.font())
            visible_stock_column_widths = stock_column_widths[: len(values)]
            for column, width in enumerate(visible_stock_column_widths):
                text = str(values[column] if column < len(values) else "")
                if column > 0:
                    separator_rect = QRect(
                        x,
                        option.rect.top(),
                        separator_width,
                        option.rect.height(),
                    )
                    painter.drawText(
                        separator_rect,
                        Qt.AlignCenter,
                        "|",
                    )
                    x += separator_width
                cell_rect = QRect(
                    x,
                    option.rect.top(),
                    width,
                    option.rect.height(),
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
                    elided = painter.fontMetrics().elidedText(
                        text,
                        Qt.ElideRight,
                        max(0, text_rect.width()),
                    )
                    painter.drawText(
                        text_rect,
                        Qt.AlignLeft | Qt.AlignVCenter,
                        elided,
                    )
                    x += width
                    continue
                if column == 2:
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
                    painter.setBrush(
                        QColor("#111827" if visually_enabled else "#9CA3AF")
                    )
                    painter.drawEllipse(led_rect)
                    painter.restore()
                    x += width
                    continue
                alignment = (
                        Qt.AlignLeft | Qt.AlignVCenter
                        if column == 0
                        else Qt.AlignCenter
                )
                if column >= 6:
                    if column == 6:
                        metric_texts = _routine_stock_metric_texts(list(values), tuple(metrics_data))
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
                            hidden_value_indexes=hidden_value_indexes,
                        )
                        painter.restore()
                        return
                    stock_metric_label_hint = {
                        6: "보유",
                        7: "가격",
                        8: "손익",
                        9: "미체결",
                    }.get(column)
                    metric_index = column - 6
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
                        x += width
                        continue
                    if column == 10 and draw_limit_metric(
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
                        x += width
                        continue
                    if draw_stock_position_metric(
                        painter,
                        cell_rect.adjusted(2, 0, -2, 0),
                        text,
                        value_widths=stock_position_value_widths,
                        outer_padding=ROUTINE_INSTANCE_MONEY_OUTER_PADDING,
                        label_hint=stock_metric_label_hint,
                    ):
                        x += width
                        continue
                elided = painter.fontMetrics().elidedText(
                    text,
                    Qt.ElideRight,
                    max(0, cell_rect.width() - 4),
                )
                painter.drawText(
                    cell_rect.adjusted(2, 0, -2, 0),
                    alignment,
                    elided,
                )
                x += width
            painter.restore()
            return
        checkbox_offset = (
            ROUTINE_CHILD_CHECKBOX_OFFSET
            if row_kind == ROUTINE_ROW_CHILD
            else ROUTINE_PARENT_CHECKBOX_OFFSET
        )
        checkbox_rect = QRect(
            option.rect.left() + checkbox_offset,
            option.rect.top() + (option.rect.height() - ROUTINE_CHECKBOX_SIZE) // 2,
            ROUTINE_CHECKBOX_SIZE,
            ROUTINE_CHECKBOX_SIZE,
        )
        checkbox_option = QStyleOptionButton()
        checkbox_option.rect = checkbox_rect
        checked = index.data(Qt.CheckStateRole) == Qt.Checked
        visually_enabled = index.data(ROUTINE_CHECKBOX_VISUAL_ENABLED_ROLE) is not False
        checkbox_option.state = (
            QStyle.State_Enabled if visually_enabled else QStyle.State_None
        ) | (QStyle.State_On if checked else QStyle.State_Off)
        painter.save()
        if not visually_enabled:
            painter.setOpacity(0.45)
        style.drawPrimitive(
            QStyle.PE_IndicatorCheckBox,
            checkbox_option,
            painter,
            option.widget,
        )
        painter.restore()

        text_left_offset = checkbox_offset + ROUTINE_CHECKBOX_SIZE + 6
        if row_kind == ROUTINE_ROW_CHILD:
            led_state = str(index.data(ROUTINE_CHILD_PROFIT_LED_ROLE) or "gray")
            led_box_left = (
                option.rect.left()
                + checkbox_offset
                + ROUTINE_CHECKBOX_SIZE
                + ROUTINE_PROFIT_LED_GAP
            )
            _draw_routine_profit_led(
                painter,
                row_rect=option.rect,
                led_box_left=led_box_left,
                led_state=led_state,
                visually_enabled=visually_enabled,
            )
            text_left_offset = (
                checkbox_offset
                + ROUTINE_CHECKBOX_SIZE
                + ROUTINE_PROFIT_LED_GAP
                + ROUTINE_PROFIT_LED_BOX_SIZE
                + ROUTINE_PROFIT_LED_GAP
            )
            if bool(index.data(ROUTINE_CHILD_HAS_STOCKS_ROLE)):
                collapsed = bool(index.data(ROUTINE_CHILD_COLLAPSED_ROLE))
                arrow = "▶" if collapsed else "▼"
                arrow_rect = option.rect.adjusted(
                    text_left_offset,
                    0,
                    -4,
                    0,
                )
                painter.save()
                painter.setFont(option.font)
                if not visually_enabled:
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
                aggregate = display_text[len(parent_text) :].lstrip()
                aggregate_left = (
                    text_rect.left()
                    + QFontMetrics(parent_font).horizontalAdvance(parent_text)
                    + 16
                )
                aggregate_rect = QRect(
                    aggregate_left,
                    text_rect.top(),
                    max(0, text_rect.right() - aggregate_left),
                    text_rect.height(),
                )
                painter.setFont(option.font)
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

        self.login_status_label = QLabel("로그인 상태: 미연결")
        self.btn_kiwoom_login = QPushButton("키움 로그인")
        self.account_label = QLabel("계좌번호: -")
        self.account_combo = QComboBox()
        self.account_combo.setEnabled(False)
        self.account_type_label = QLabel("계좌 구분: -")
        self.auto_status_label = QLabel("전체 자동매매 상태: 정지")
        self.buy_time_status_label = QLabel("매수 가능 상태: 확인 전")
        self.account_total_deposit_label = QLabel("-")
        self.account_order_available_label = QLabel("-")

        # 관제창 예산 현황 표시 전용 QLabel
        # 실제 예산 저장/주문수량 계산/매수 제한 로직은 아직 연결하지 않는다.
        self.budget_total_label = QLabel("0")
        self.budget_used_label = QLabel("0")
        self.budget_available_label = QLabel("0")
        self.budget_usage_rate_label = QLabel("-")
        self.budget_routine_count_label = QLabel("0")
        self.budget_stock_count_label = QLabel("0")
        self.budget_status_label = QLabel("확인 전")

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
        self._routine_operation_status_by_instance: dict[str, str] = {}
        self._routine_instance_name_editor = None
        self._routine_instance_name_editor_instance_id = ""
        self._routine_instance_name_editor_original = ""
        self._routine_instance_name_editor_item = None
        self._routine_instance_name_edit_finishing = False
        self._routine_dummy_tab_buttons: list[QPushButton] = []
        self._routine_buy_limit_edit_filter = _RoutineBuyLimitValueEditFilter(self)
        self._routine_instance_buy_limit_editor = None
        self._routine_instance_buy_limit_editor_instance_id = ""
        self._routine_instance_buy_limit_editor_label = None
        self._routine_instance_buy_limit_edit_finishing = False
        self._routine_stock_buy_limit_editor = None
        self._routine_stock_buy_limit_editor_config_path = ""
        self._routine_stock_buy_limit_edit_finishing = False
        self.routine_table._editing_stock_buy_limit_path = ""
        self._main_running_sort_column = -1
        self._main_running_sort_order = Qt.AscendingOrder
        self._startup_recovery_result: dict[str, object] = {}
        self._startup_recovery_approved = False
        self._startup_recovery_approved_snapshot = ""

        self.btn_stock_register = QPushButton("종목등록설정")
        self.btn_auto_trade_setting = QPushButton("자동매매설정")
        self.btn_stop_all = QPushButton("전체 자동매매 정지")
        self.btn_restart = QPushButton("운영 재개")
        self.btn_initialize = QPushButton("초기화")
        self.btn_log_view = QPushButton("로그 보기")
        self.btn_review_required = QPushButton("검토관리종목")
        self.btn_exit = QPushButton("종료")
        self.btn_emergency_stop = QPushButton("긴급정지")

        self._setup_ui()
        self._connect_events()
        normalize_base_stock_single_routine_file()
        self.refresh_startup_recovery_status()
        self.refresh_all()

    def _setup_ui(self) -> None:
        central = QWidget()
        central.setObjectName("mainDashboardRoot")
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        top_box = self._create_top_status_box()
        account_funds_box = self._create_account_funds_box()
        budget_box = self._create_budget_status_box()
        top_layout = QHBoxLayout()
        top_layout.setSpacing(6)
        top_layout.addWidget(top_box, 4)
        top_layout.addWidget(account_funds_box, 2)
        top_layout.addWidget(budget_box, 4)

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
        box = QGroupBox("시스템 상태")
        layout = QGridLayout()
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(6)

        layout.addWidget(self.login_status_label, 0, 0)
        layout.addWidget(self.btn_kiwoom_login, 0, 1)
        layout.addWidget(self.account_label, 0, 2)
        layout.addWidget(self.account_combo, 0, 3)
        layout.addWidget(self.account_type_label, 0, 4)

        layout.addWidget(self.auto_status_label, 1, 0, 1, 2)
        layout.addWidget(self.buy_time_status_label, 1, 2, 1, 2)
        layout.addWidget(self.btn_emergency_stop, 1, 4)

        self.btn_emergency_stop.setMinimumHeight(32)
        self.btn_emergency_stop.setObjectName("dangerButton")

        box.setMaximumHeight(108)
        box.setLayout(layout)
        return box

    def _create_account_funds_box(self) -> QGroupBox:
        box = QGroupBox("계좌 자금")
        layout = QGridLayout()
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(6)

        layout.addWidget(QLabel("총 예수금"), 0, 0)
        layout.addWidget(self.account_total_deposit_label, 0, 1)
        layout.addWidget(QLabel("주문 가능금액"), 1, 0)
        layout.addWidget(self.account_order_available_label, 1, 1)

        for label in (
            self.account_total_deposit_label,
            self.account_order_available_label,
        ):
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            label.setMinimumWidth(110)
            label.setObjectName("fundValue")

        box.setMaximumHeight(108)
        box.setLayout(layout)
        return box

    def _create_budget_status_box(self) -> QGroupBox:
        """관제창 예산 현황 UI.

        현재는 표시 전용이다.
        예산 저장, 주문수량 산출, 매수 제한, 루틴/종목 배분은 이후 단계에서 검토한다.
        """
        box = QGroupBox("예산 현황")
        layout = QGridLayout()
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(6)

        layout.addWidget(QLabel("전체예산"), 0, 0)
        layout.addWidget(self.budget_total_label, 0, 1)
        layout.addWidget(QLabel("사용예산"), 0, 2)
        layout.addWidget(self.budget_used_label, 0, 3)
        layout.addWidget(QLabel("가용예산"), 0, 4)
        layout.addWidget(self.budget_available_label, 0, 5)

        layout.addWidget(QLabel("사용률"), 1, 0)
        layout.addWidget(self.budget_usage_rate_label, 1, 1)
        layout.addWidget(QLabel("루틴수"), 1, 2)
        layout.addWidget(self.budget_routine_count_label, 1, 3)
        layout.addWidget(QLabel("연결종목"), 1, 4)
        layout.addWidget(self.budget_stock_count_label, 1, 5)
        layout.addWidget(QLabel("예산상태"), 1, 6)
        layout.addWidget(self.budget_status_label, 1, 7)

        value_labels = [
            self.budget_total_label,
            self.budget_used_label,
            self.budget_available_label,
            self.budget_usage_rate_label,
            self.budget_routine_count_label,
            self.budget_stock_count_label,
            self.budget_status_label,
        ]
        for label in value_labels:
            label.setAlignment(Qt.AlignCenter)
            label.setMinimumWidth(90)
            label.setObjectName("metricValue")

        box.setMaximumHeight(108)
        box.setLayout(layout)
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
        routine_content_layout.addWidget(self._create_routine_dummy_tab_area())
        routine_content_layout.addWidget(self.routine_table, 1)
        routine_layout.addLayout(routine_content_layout)
        routine_box.setLayout(routine_layout)

        self._setup_running_stock_table()
        self.running_stock_table.setVisible(False)

        layout.addWidget(routine_box, 1)

        return layout

    def _create_routine_dummy_tab_area(self) -> QWidget:
        tab_area = QWidget()
        tab_area.setObjectName("routineDummyTabArea")
        tab_area.setFixedWidth(46)
        layout = QVBoxLayout(tab_area)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._routine_dummy_tab_buttons = []
        for index, title in enumerate(("전체", "루틴", "운용", "종목")):
            button = QPushButton(title)
            button.setObjectName("routineDummyTabButton")
            button.setCheckable(True)
            button.setFixedSize(38, 30)
            button.clicked.connect(
                lambda _checked=False, selected=button: self._select_routine_dummy_tab(selected)
            )
            if index == 0:
                button.setChecked(True)
            self._routine_dummy_tab_buttons.append(button)
            layout.addWidget(button)

        layout.addStretch(1)
        return tab_area

    def _select_routine_dummy_tab(self, selected_button: QPushButton) -> None:
        for button in self._routine_dummy_tab_buttons:
            button.setChecked(button is selected_button)

    def _create_button_area(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(8)

        buttons = [
            self.btn_stock_register,
            self.btn_auto_trade_setting,
            self.btn_stop_all,
            self.btn_restart,
            self.btn_initialize,
            self.btn_log_view,
            self.btn_review_required,
            self.btn_exit,
        ]

        for button in buttons:
            button.setMinimumHeight(32)
            layout.addWidget(button)

        self.btn_stop_all.setObjectName("warningButton")
        self.btn_restart.setObjectName("successButton")
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
            QWidget#mainDashboardRoot QWidget#routineDummyTabArea {
                background: transparent;
            }
            QWidget#mainDashboardRoot QPushButton#routineDummyTabButton {
                min-width: 38px;
                max-width: 38px;
                min-height: 30px;
                max-height: 30px;
                padding: 0px;
                background: #f8fafc;
                border: 1px solid #cbd5e1;
                border-radius: 4px;
                color: #334155;
                font-size: 9pt;
                font-weight: 500;
            }
            QWidget#mainDashboardRoot QPushButton#routineDummyTabButton:checked {
                background: #2563eb;
                border-color: #1d4ed8;
                color: #ffffff;
                font-weight: 700;
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
        self.btn_emergency_stop.clicked.connect(self.on_emergency_stop_clicked)
        self.btn_stop_all.clicked.connect(self.on_stop_all_clicked)
        self.btn_stock_register.clicked.connect(self.open_stock_register_window)
        self.btn_auto_trade_setting.clicked.connect(self.open_auto_trade_setting_window)
        self.btn_restart.clicked.connect(self.review_startup_recovery)
        self.btn_initialize.clicked.connect(self.not_implemented)
        self.btn_log_view.clicked.connect(self.not_implemented)
        self.btn_review_required.clicked.connect(self.open_review_required_window)
        self.routine_table.horizontalHeader().sectionClicked.connect(self.sort_main_routine_table_by_column)
        self.routine_table.itemChanged.connect(self.on_routine_check_item_changed)
        self.routine_table.customContextMenuRequested.connect(self.open_routine_context_menu)
        self._routine_checkbox_controller = _RoutineCheckBoxController(self)
        self.routine_table.viewport().installEventFilter(self._routine_checkbox_controller)
        self.running_stock_table.horizontalHeader().sectionClicked.connect(self.sort_main_running_table_by_column)

    def startup_recovery_stock_state_paths(self) -> list[Path]:
        return [stock_dir / "state.json" for stock_dir in self.all_runtime_stock_dirs()]

    def refresh_startup_recovery_status(self) -> dict[str, object]:
        result = assess_startup_recovery(
            stock_state_paths=self.startup_recovery_stock_state_paths(),
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
            self.btn_restart.setText("운영 재개 확인 완료")
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
            self.btn_restart.setText("운영 재개")
        return result

    def startup_recovery_session_ready(self, *, refresh: bool = True) -> bool:
        if refresh:
            self.refresh_startup_recovery_status()
        return bool(
            self._startup_recovery_approved
            and self._startup_recovery_approved_snapshot
            and self._startup_recovery_approved_snapshot
            == self._startup_recovery_result.get("snapshot_hash")
        )

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
        if QMessageBox.question(
            self,
            "Startup Recovery",
            message,
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
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
            message = f"키움 로그인 사용불가: {reason}"
            self.login_status_label.setText(message)
            self.statusBar().showMessage(message)
            return

        try:
            if not api.is_available():
                reason = api.unavailable_reason() or getattr(self, "kiwoom_api_unavailable_reason", "") or "kiwoom api unavailable"
                message = f"키움 로그인 사용불가: {reason}"
                self.login_status_label.setText(message)
                self.statusBar().showMessage(message)
                return
            if api.is_connected():
                message = "로그인 상태: 연결됨"
                self.login_status_label.setText(message)
                self.statusBar().showMessage(message)
                return

            result = api.login()
        except Exception as exc:
            message = f"키움 로그인 요청 실패: {exc}"
            self.login_status_label.setText(message)
            self.statusBar().showMessage(message)
            return

        status = str(result.get("status", ""))
        if status == "login_requested":
            message = "로그인 요청됨"
        elif result.get("connected"):
            message = "로그인 상태: 연결됨"
        else:
            reason = result.get("error") or result.get("message") or status or "unknown error"
            message = f"키움 로그인 요청 실패: {reason}"

        self.login_status_label.setText(message)
        self.refresh_kiwoom_accounts()
        self.statusBar().showMessage(message)

    def on_kiwoom_login_state_changed(self, state) -> None:
        state = state if isinstance(state, dict) else {}
        connected = bool(state.get("connected", False))
        message = str(state.get("message", "") or "")
        if connected:
            label_text = "로그인 상태: 연결됨"
            status_message = message or label_text
        else:
            label_text = "로그인 상태: 실패"
            status_message = message or label_text

        self.login_status_label.setText(label_text)
        self.refresh_kiwoom_accounts()
        self.statusBar().showMessage(status_message)

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

    def refresh_kiwoom_accounts(self) -> list[str]:
        combo = getattr(self, "account_combo", None)
        if combo is None:
            return []

        current = self.selected_account_no()
        accounts = self.kiwoom_account_numbers()
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItems(accounts)
            combo.setEnabled(bool(accounts))
            if len(accounts) == 1:
                combo.setCurrentIndex(0)
            elif current and current in accounts:
                combo.setCurrentIndex(accounts.index(current))
            else:
                combo.setCurrentIndex(-1)
        finally:
            combo.blockSignals(False)
        return accounts

    def selected_account_no(self) -> str:
        combo = getattr(self, "account_combo", None)
        if combo is None or not combo.isEnabled():
            return ""
        account = str(combo.currentText() or "").strip()
        return account if account in self.kiwoom_account_numbers() else ""

    def refresh_all(self) -> None:
        self.load_routine_table()
        self.load_running_stock_table()
        self.update_budget_panel()
        self.update_emergency_button_state()
        self.update_review_required_button_text()

    def update_budget_panel(self) -> None:
        update_main_budget_panel(self)

    def review_required_stock_count(self) -> int:
        """관제창에서 제외된 검토관리 대상 종목 수를 계산한다."""
        count = 0
        seen: set[str] = set()
        for stock_dir in self.all_runtime_stock_dirs():
            key = str(stock_dir.resolve())
            if key in seen:
                continue
            seen.add(key)
            try:
                state = read_json_dict(stock_dir / "state.json")
            except Exception:
                state = {}
            if is_review_required_state(state):
                count += 1
        return count

    def update_review_required_button_text(self) -> None:
        if not hasattr(self, "btn_review_required"):
            return
        count = self.review_required_stock_count()
        self.btn_review_required.setText(f"검토관리종목({count})" if count else "검토관리종목")

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

    def on_stop_all_clicked(self) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("전체 자동매매 정지")
        box.setText(
            "전체 자동매매를 정지하시겠습니까?\n\n"
            "보유 종목은 자동 매도하지 않습니다."
        )
        proceed_button = box.addButton("진행", QMessageBox.AcceptRole)
        box.addButton("취소", QMessageBox.RejectRole)
        box.setDefaultButton(proceed_button)
        box.exec_()

        if box.clickedButton() == proceed_button:
            self.statusBar().showMessage("전체 자동매매 정지 요청됨")
            QMessageBox.information(
                self,
                "전체 자동매매 정지",
                "현재 단계에서는 실제 자동매매가 연결되어 있지 않습니다.\n"
                "GUI 버튼 동작만 확인했습니다.",
            )

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
        dialog.exec_()

    def toggle_routine_check_state(self, row: int) -> None:
        item = self.routine_table.item(row, 0)
        if item is None:
            return
        row_kind = str(item.data(ROUTINE_ROW_KIND_ROLE) or "")
        definition_id = str(item.data(ROUTINE_DEFINITION_ID_ROLE) or "").strip()
        instance_id = str(item.data(ROUTINE_INSTANCE_ID_ROLE) or "").strip()
        if row_kind == ROUTINE_ROW_PARENT and definition_id:
            enabled = toggle_routine_definition(self, definition_id)
            if enabled:
                self._collapsed_routine_definition_ids.discard(definition_id)
            else:
                self._collapsed_routine_definition_ids.add(definition_id)
        elif row_kind == ROUTINE_ROW_CHILD and instance_id:
            toggle_routine_instance(self, instance_id)
        else:
            return
        self.load_routine_table()

    def on_routine_check_item_changed(self, item) -> None:
        if item is None or item.column() != 0:
            return
        row_kind = str(item.data(ROUTINE_ROW_KIND_ROLE) or "")
        definition_id = str(item.data(ROUTINE_DEFINITION_ID_ROLE) or "").strip()
        instance_id = str(item.data(ROUTINE_INSTANCE_ID_ROLE) or "").strip()
        requested = item.checkState() == Qt.Checked
        if row_kind == ROUTINE_ROW_PARENT and definition_id:
            current = routine_definition_enabled(self, definition_id)
        elif row_kind == ROUTINE_ROW_CHILD and instance_id:
            current = routine_instance_checked(self, instance_id)
            if not routine_instance_checkbox_enabled(self, instance_id):
                if requested != current:
                    self.refresh_routine_check_states()
                return
        elif row_kind == ROUTINE_ROW_STOCK:
            return
        else:
            return
        if requested != current:
            self.toggle_routine_check_state(item.row())

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

    def toggle_routine_stock_check_state(self, row: int) -> None:
        item = self.routine_table.item(row, 0)
        if item is None or str(item.data(ROUTINE_ROW_KIND_ROLE) or "") != ROUTINE_ROW_STOCK:
            return
        stock_path = str(item.data(ROUTINE_STOCK_PATH_ROLE) or "").strip()
        if not stock_path:
            return
        checked = item.checkState() != Qt.Checked
        self._routine_stock_selection[stock_path] = checked
        item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self.routine_table.viewport().update()

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

    def refresh_routine_check_states(self) -> None:
        for row in range(self.routine_table.rowCount()):
            first_item = self.routine_table.item(row, 0)
            if first_item is None:
                continue
            row_kind = str(first_item.data(ROUTINE_ROW_KIND_ROLE) or "")
            definition_id = str(first_item.data(ROUTINE_DEFINITION_ID_ROLE) or "").strip()
            instance_id = str(first_item.data(ROUTINE_INSTANCE_ID_ROLE) or "").strip()
            group_enabled = routine_definition_enabled(self, definition_id)
            if row_kind == ROUTINE_ROW_PARENT:
                checked = group_enabled
                row_visually_enabled = group_enabled
                first_item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            elif row_kind == ROUTINE_ROW_CHILD:
                checked = routine_instance_checked(self, instance_id)
                row_visually_enabled = group_enabled and checked
                first_item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            elif row_kind == ROUTINE_ROW_STOCK:
                stock_path = str(first_item.data(ROUTINE_STOCK_PATH_ROLE) or "").strip()
                checked = first_item.checkState() == Qt.Checked
                if stock_path and stock_path in self._routine_stock_selection:
                    checked = bool(self._routine_stock_selection.get(stock_path))
                row_visually_enabled = group_enabled and routine_instance_checked(
                    self,
                    instance_id,
                )
                first_item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            else:
                continue
            first_item.setData(
                ROUTINE_CHECKBOX_VISUAL_ENABLED_ROLE,
                row_visually_enabled,
            )
            for column in range(self.routine_table.columnCount()):
                item = self.routine_table.item(row, column)
                if item is not None:
                    item.setForeground(
                        QBrush() if row_visually_enabled else QColor("#9ca3af")
                    )
                widget = self.routine_table.cellWidget(row, column)
                if widget is not None:
                    widget.setEnabled(row_visually_enabled)
        self.routine_table.viewport().update()

    def selected_routine_instance_ids(self) -> tuple[str, ...]:
        return selected_routine_instance_ids(self)

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
        name_rect = self._routine_checkbox_controller._child_name_rect(index)
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

    def _stock_config_path_for_routine_row(self, row: int) -> Path | None:
        item = self.routine_table.item(row, 0)
        if item is None or str(item.data(ROUTINE_ROW_KIND_ROLE) or "") != ROUTINE_ROW_STOCK:
            return None
        stock_path = str(item.data(ROUTINE_STOCK_PATH_ROLE) or "").strip()
        if not stock_path:
            return None
        return PROJECT_ROOT / stock_path / "config.json"

    def _routine_stock_buy_limit_value_rect(self, row: int) -> QRect:
        index = self.routine_table.model().index(row, 0)
        if not index.isValid():
            return QRect()
        metric_rect = self._routine_checkbox_controller._stock_metric_rect(index, 10)
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

    def handle_routine_stock_buy_limit_double_click(self, row: int) -> None:
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
        enabled = bool(config.get("buy_limit_enabled", False))
        amount = config.get("buy_limit_amount")

        self.finish_routine_instance_buy_limit_edit(save=True)
        self.finish_routine_stock_buy_limit_edit(save=True)

        if routine_instance_buy_limit_configured(enabled=enabled, amount=amount):
            self._write_stock_buy_limit_config(
                config_path,
                enabled=False,
                amount=None,
            )
            self.load_routine_table()
            return

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
        editor.setText("")
        editor.setGeometry(editor_rect)
        editor.installEventFilter(self._routine_buy_limit_edit_filter)
        self.routine_table._editing_stock_buy_limit_path = stock_path
        self.routine_table.viewport().update(self.routine_table.visualRect(self.routine_table.model().index(row, 0)))
        editor.show()
        editor.setFocus(Qt.MouseFocusReason)

        self._routine_stock_buy_limit_editor = editor
        self._routine_stock_buy_limit_editor_config_path = str(config_path)

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
        try:
            self._write_stock_buy_limit_config(
                config_path,
                enabled=amount is not None,
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
        instance_id = str(amount_label.property("routine_instance_id") or "").strip()
        if not instance_id:
            row = self._routine_row_for_child_widget(amount_label)
            if row < 0:
                return
            item = self.routine_table.item(row, 0)
            if item is None:
                return
            if str(item.data(ROUTINE_ROW_KIND_ROLE) or "") != ROUTINE_ROW_CHILD:
                return
            instance_id = str(item.data(ROUTINE_INSTANCE_ID_ROLE) or "").strip()
            if not instance_id:
                return
        instance = routine_instance_by_id(instance_id)
        if instance is None:
            return

        self.finish_routine_stock_buy_limit_edit(save=True)
        self.finish_routine_instance_buy_limit_edit(save=True)
        if instance.buy_limit_enabled:
            result = RoutineInstanceRepository(PROJECT_ROOT).update_buy_limit(
                instance_id,
                enabled=False,
            )
            if not result.success:
                QMessageBox.warning(
                    self,
                    "매수한도 변경",
                    result.error or "매수한도를 변경하지 못했습니다.",
                )
                return
            amount_label.setText("미사용")
            self.refresh_all()
            return

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
        editor.setText("")
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

        result = RoutineInstanceRepository(PROJECT_ROOT).update_buy_limit(
            instance_id,
            enabled=amount is not None,
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

    def _routine_instance_has_assigned_stocks(self, instance_id: str) -> bool:
        return int(
            self._routine_assigned_stock_count_by_instance.get(instance_id, 0) or 0
        ) > 0

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
        if row_kind == ROUTINE_ROW_PARENT:
            index = self.routine_table.model().index(item.row(), 0)
            if not self._routine_checkbox_controller._parent_name_rect(index).contains(
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
            early_close_action = menu.addAction("조기마감")
            immediate_action = menu.addAction("즉시청산")
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
            early_close_action.triggered.connect(
                lambda _checked=False: self.request_routine_definition_operation(
                    definition_id,
                    definition.display_name,
                    MODE_EARLY_CLOSE,
                    ROUTINE_STATUS_EARLY_CLOSE,
                )
            )
            immediate_action.triggered.connect(
                lambda _checked=False: self.request_routine_definition_operation(
                    definition_id,
                    definition.display_name,
                    COMMAND_IMMEDIATE_LIQUIDATION,
                    ROUTINE_STATUS_IMMEDIATE_LIQUIDATION,
                )
            )
            menu.exec_(self.routine_table.viewport().mapToGlobal(position))
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
        menu.addSeparator()
        early_close_action = menu.addAction("조기마감")
        immediate_action = menu.addAction("즉시청산")
        settings_action.triggered.connect(
            lambda _checked=False, item=first_item: self.open_routine_settings_from_main_table(
                item
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
                MODE_EARLY_CLOSE,
                ROUTINE_STATUS_EARLY_CLOSE,
            )
        )
        immediate_action.triggered.connect(
            lambda _checked=False: self.request_routine_operation(
                instance_id,
                instance.display_name,
                COMMAND_IMMEDIATE_LIQUIDATION,
                ROUTINE_STATUS_IMMEDIATE_LIQUIDATION,
            )
        )
        menu.exec_(self.routine_table.viewport().mapToGlobal(position))

    def request_routine_definition_operation(
        self,
        definition_id: str,
        display_name: str,
        command: str,
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
        command_label = (
            ROUTINE_STATUS_EARLY_CLOSE
            if command == MODE_EARLY_CLOSE
            else ROUTINE_STATUS_IMMEDIATE_LIQUIDATION
        )
        if not instance_ids:
            QMessageBox.warning(
                self,
                f"카테고리 {command_label} 불가",
                "체크된 하위 루틴 인스턴스가 없습니다.",
            )
            return

        if command == COMMAND_IMMEDIATE_LIQUIDATION:
            answer = _create_routine_operation_confirmation(
                self,
                command,
                QMessageBox.Warning,
            ).exec_()
        else:
            answer = _create_routine_operation_confirmation(self, command).exec_()
        if answer != QMessageBox.Yes:
            self.statusBar().showMessage(
                f"카테고리 {command_label} 취소: {display_name}"
            )
            return

        service = OperationCommandService(PROJECT_ROOT)
        applied_count = 0
        partial_count = 0
        failed_count = 0
        for instance_id in instance_ids:
            result = service.apply(
                OperationCommandRequest(
                    target_scope=SCOPE_ROUTINE_INSTANCE,
                    target_id=instance_id,
                    command=command,
                    source="main_routine_parent_context_menu",
                )
            )
            if result.status == RESULT_FAILED or not result.stock_results:
                failed_count += 1
                continue
            self._routine_operation_status_by_instance[instance_id] = display_status
            if result.status == RESULT_PARTIAL_SUCCESS:
                partial_count += 1
            else:
                applied_count += 1

        self.load_routine_table()
        self.update_review_required_button_text()
        if partial_count or failed_count:
            QMessageBox.warning(
                self,
                f"카테고리 {command_label} 일부 적용"
                if applied_count or partial_count
                else f"카테고리 {command_label} 실패",
                f"성공 {applied_count}개 / 일부 적용 {partial_count}개 / "
                f"실패 {failed_count}개입니다. 검토관리 상태를 확인하세요.",
            )
        self.statusBar().showMessage(
            f"카테고리 {command_label}: {display_name} / 성공 {applied_count} / "
            f"일부 적용 {partial_count} / 실패 {failed_count}"
        )

    def request_routine_operation(
        self,
        instance_id: str,
        display_name: str,
        command: str,
        display_status: str,
    ) -> None:
        command_label = (
            ROUTINE_STATUS_EARLY_CLOSE
            if command == MODE_EARLY_CLOSE
            else ROUTINE_STATUS_IMMEDIATE_LIQUIDATION
        )
        if command == COMMAND_IMMEDIATE_LIQUIDATION:
            answer = _create_routine_operation_confirmation(self, command).exec_()
        else:
            answer = _create_routine_operation_confirmation(self, command).exec_()
        if answer != QMessageBox.Yes:
            self.statusBar().showMessage(f"루틴 {command_label} 취소: {display_name}")
            return

        result = OperationCommandService(PROJECT_ROOT).apply(
            OperationCommandRequest(
                target_scope=SCOPE_ROUTINE_INSTANCE,
                target_id=instance_id,
                command=command,
                source="main_routine_context_menu",
            )
        )
        if result.status == RESULT_FAILED or not result.stock_results:
            self.update_review_required_button_text()
            QMessageBox.warning(
                self,
                f"루틴 {command_label} 실패",
                result.error or "명령을 적용할 대상 또는 결과를 확인하지 못했습니다.",
            )
            return

        self._routine_operation_status_by_instance[instance_id] = display_status
        self.load_routine_table()
        self.update_review_required_button_text()
        if result.status == RESULT_PARTIAL_SUCCESS:
            QMessageBox.warning(
                self,
                f"루틴 {command_label} 일부 적용",
                "일부 종목에 명령을 적용하지 못했습니다. 검토관리 상태를 확인하세요.",
            )
        self.statusBar().showMessage(f"루틴 {command_label}: {display_name}")

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
        self._routine_operation_status_by_instance[instance_id] = completion_status
        self.load_routine_table()
        return True

    def open_stock_register_window(self) -> None:
        self.stock_register_window = StockRegisterWindow(self)
        self.stock_register_window.show()

    def open_auto_trade_setting_window(self) -> None:
        window = getattr(self, "auto_trade_setting_window", None)
        if window is None:
            window = AutoTradeSettingWindow(self)
            self.auto_trade_setting_window = window
        if window.isMinimized():
            window.showNormal()
        else:
            window.show()
        window.raise_()
        window.activateWindow()

    def on_kiwoom_raw_chejan_received(self, raw_event: dict[str, object]) -> None:
        self.last_chejan_record_result = handle_kiwoom_raw_chejan_event(
            raw_event,
            {
                "kiwoom_api_live_event": True,
                "live_event_source": "KiwoomApi.raw_chejan_received",
            },
        )
        window = getattr(self, "auto_trade_setting_window", None)
        if window is not None:
            setattr(window, "last_chejan_record_result", self.last_chejan_record_result)

    def open_review_required_window(self) -> None:
        self.review_required_window = GlobalReviewRequiredWindow(self)
        self.review_required_window.show()

    def not_implemented(self) -> None:
        QMessageBox.information(
            self,
            "안내",
            "이 기능은 다음 단계에서 구현합니다.",
        )
