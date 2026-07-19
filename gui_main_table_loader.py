# -*- coding: utf-8 -*-
"""
gui_main_table_loader.py

메인 관제창의 표 로딩/정렬 전용 헬퍼.

분리 범위:
- 좌측 루틴표 정렬/로딩
- 우측 실행종목표 정렬/로딩

주의:
- MainWindow UI 생성/버튼 연결/긴급정지/검토관리 로직은 포함하지 않는다.
"""

from __future__ import annotations

import json
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont, QFontMetrics
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QStackedLayout, QWidget

from gui_table_utils import next_sort_order
from gui_common_utils import safe_int_value
from gui_stock_data import stock_runtime_dir_for_routine
from gui_order_utils import (
    pending_order_side_quantities,
    format_number_value,
)
from gui_review_utils import current_price_from_state, safe_float_value
from runtime_io import read_json_dict
from state_policy import normalize_operation_mode
from gui_auto_trade_display import (
    create_routine_profit_signal_widget,
    create_auto_trade_setting_status_item,
    format_routine_buy_limit,
    format_routine_buy_limit_usage,
    format_routine_used_amount,
    routine_profit_signal,
    SORT_ROLE,
    SortableTableWidgetItem,
)
from gui_auto_trade_setting_window import (
    auto_trade_setting_trade_started,
    create_auto_trade_situation_item,
    get_routine_dirs,
    is_review_required_state,
    routine_display_name,
)
from gui_auto_trade_policy import (
    auto_trade_setting_current_session_trade_started,
    auto_trade_setting_display_status_for_current_session,
)
from gui_base_stock_service import read_base_stocks
from routine_instance_registry import (
    load_persisted_routine_instances,
    load_routine_definitions,
)
from gui_main_routine_selection import (
    routine_definition_enabled,
    routine_instance_checkbox_enabled,
    routine_instance_checked,
    sync_routine_selection_state,
)


ROUTINE_MONITORING_HEADERS = (
    "루틴명",
    "상태",
    "등록",
    "실행",
    "정지",
    "오류",
    "사용금액",
    "매수한도",
    "사용률",
    "수익률",
)

ROUTINE_STATUS_DEFAULT = "기본운영"
ROUTINE_STATUS_EARLY_CLOSE = "조기마감"
ROUTINE_STATUS_IMMEDIATE_LIQUIDATION = "즉시청산"
ROUTINE_STATUS_COMPLETED = "매매완료"
ROUTINE_STATUS_PARTIAL_COMPLETION = "일부완료"
ROUTINE_COMPLETION_STATUSES = frozenset(
    {ROUTINE_STATUS_COMPLETED, ROUTINE_STATUS_PARTIAL_COMPLETION}
)
ROUTINE_STATUS_STAMP_COLORS = {
    ROUTINE_STATUS_DEFAULT: "#2563EB",
    ROUTINE_STATUS_IMMEDIATE_LIQUIDATION: "#DC2626",
    ROUTINE_STATUS_EARLY_CLOSE: "#D97706",
    ROUTINE_STATUS_COMPLETED: "#16A34A",
    ROUTINE_STATUS_PARTIAL_COMPLETION: "#7C3AED",
}

ROUTINE_ROW_KIND_ROLE = Qt.UserRole + 201
ROUTINE_DEFINITION_ID_ROLE = Qt.UserRole + 202
ROUTINE_INSTANCE_ID_ROLE = Qt.UserRole + 203
ROUTINE_CHECKBOX_VISUAL_ENABLED_ROLE = Qt.UserRole + 204
ROUTINE_PARENT_NAME_ROLE = Qt.UserRole + 205
ROUTINE_PARENT_AGGREGATE_ROLE = Qt.UserRole + 206
ROUTINE_PARENT_COLLAPSED_ROLE = Qt.UserRole + 207
ROUTINE_CHILD_STATUS_ROLE = Qt.UserRole + 208
ROUTINE_CHILD_AGGREGATE_ROLE = Qt.UserRole + 209
ROUTINE_CHILD_PROFIT_LED_ROLE = Qt.UserRole + 210
ROUTINE_ROW_PARENT = "definition"
ROUTINE_ROW_CHILD = "instance"
ROUTINE_PARENT_CHECKBOX_OFFSET = 4
ROUTINE_CHILD_CHECKBOX_OFFSET = 24
ROUTINE_CHECKBOX_SIZE = 16
ROUTINE_PROFIT_LED_BOX_SIZE = 18
ROUTINE_PROFIT_LED_SIZE = 18
ROUTINE_PROFIT_LED_GAP = 4
ROUTINE_INSTANCE_NAME_WIDTH = 180
ROUTINE_INSTANCE_ROW_HEIGHT = 28
ROUTINE_STATUS_STAMP_WIDTH = 82
ROUTINE_STATUS_STAMP_HEIGHT = 22
ROUTINE_INSTANCE_GRID_COLUMN_SAMPLES = {
    "status": "[기본운영]",
    "registered": "등록(99)",
    "running": "실행(99)",
    "stopped": "정지(99)",
    "error": "오류(99)",
    "limit": "한도(99,999,999)",
    "consumed": "소모(99,999,999 / 100.0%)",
    "profit": "수익(-99,999,999 / -99.99%)",
}
ROUTINE_INSTANCE_GRID_PADDING = 12
ROUTINE_INSTANCE_COUNT_GRID_PADDING = 4
ROUTINE_INSTANCE_GRID_SPACING = 0
ROUTINE_INSTANCE_SEPARATOR_PADDING = 0
ROUTINE_INSTANCE_NUMBER_PADDING = 4
ROUTINE_INSTANCE_MONEY_OUTER_PADDING = 5
ROUTINE_INSTANCE_NUMBER_PADDING_BY_KEY = {
    "limit_amount": 2,
    "consumed_amount": 0,
    "consumed_rate": 0,
    "profit_amount": 0,
    "profit_rate": 0,
}
ROUTINE_INSTANCE_COMPACT_COLUMNS = frozenset(
    {"registered", "running", "stopped", "error"}
)
ROUTINE_INSTANCE_AMOUNT_SAMPLES = {
    "limit_amount": ("-99,999,999", "99,999,999", "미사용", "확인 필요"),
    "consumed_amount": ("99,999,999",),
    "consumed_rate": ("100.0%", "-"),
    "profit_amount": ("-99,999,999", "+99,999,999"),
    "profit_rate": ("-99.99%", "+99.99%"),
}


def routine_instance_grid_padding(column_key: str) -> int:
    if column_key in ROUTINE_INSTANCE_COMPACT_COLUMNS:
        return ROUTINE_INSTANCE_COUNT_GRID_PADDING
    return ROUTINE_INSTANCE_GRID_PADDING


def routine_instance_number_padding(column_key: str) -> int:
    return ROUTINE_INSTANCE_NUMBER_PADDING_BY_KEY.get(
        column_key,
        ROUTINE_INSTANCE_NUMBER_PADDING,
    )


def routine_instance_grid_columns(font: QFont | None = None) -> dict[str, int]:
    metrics = QFontMetrics(font or QFont())
    columns = {
        key: metrics.horizontalAdvance(sample) + routine_instance_grid_padding(key)
        for key, sample in ROUTINE_INSTANCE_GRID_COLUMN_SAMPLES.items()
    }
    number_widths = routine_instance_number_widths(font)
    columns["limit"] = (
        metrics.horizontalAdvance("한도(")
        + number_widths["limit_amount"]
        + metrics.horizontalAdvance(")")
        + (ROUTINE_INSTANCE_MONEY_OUTER_PADDING * 2)
    )
    columns["consumed"] = (
        metrics.horizontalAdvance("소모(")
        + number_widths["consumed_amount"]
        + metrics.horizontalAdvance(" / ")
        + number_widths["consumed_rate"]
        + metrics.horizontalAdvance(")")
        + (ROUTINE_INSTANCE_MONEY_OUTER_PADDING * 2)
    )
    columns["profit"] = (
        metrics.horizontalAdvance("수익(")
        + number_widths["profit_amount"]
        + metrics.horizontalAdvance(" / ")
        + number_widths["profit_rate"]
        + metrics.horizontalAdvance(")")
        + (ROUTINE_INSTANCE_MONEY_OUTER_PADDING * 2)
    )
    return columns


def routine_instance_number_widths(font: QFont | None = None) -> dict[str, int]:
    metrics = QFontMetrics(font or QFont())
    return {
        key: max(metrics.horizontalAdvance(sample) for sample in samples)
        + routine_instance_number_padding(key)
        for key, samples in ROUTINE_INSTANCE_AMOUNT_SAMPLES.items()
    }


ROUTINE_INSTANCE_GRID_COLUMNS = {
    "status": ROUTINE_STATUS_STAMP_WIDTH,
    "registered": 60,
    "running": 60,
    "stopped": 60,
    "error": 60,
    "limit": 148,
    "consumed": 226,
    "profit": 238,
}
ROUTINE_PROFIT_LED_STATES = frozenset(("gray", "red", "yellow", "green"))


def routine_instance_profit_led_state(_row_data: dict[str, object] | None = None) -> str:
    """Return the routine instance profit LED state.

    The cost policy is intentionally not wired yet. This is the future entry
    point for fee/tax-aware profit classification.
    """

    return "gray"


def routine_instance_separator_width(font: QFont | None = None) -> int:
    metrics = QFontMetrics(font or QFont())
    return metrics.horizontalAdvance("|")


def routine_status_stamp_spec(status: object) -> tuple[str, str]:
    display_status = str(status or "").strip()
    color = ROUTINE_STATUS_STAMP_COLORS.get(display_status, "")
    return (display_status, color) if color else ("", "")


def routine_instance_count_display(value: object) -> str:
    try:
        count = int(value)
    except (TypeError, ValueError):
        return "-"
    if count > 99:
        return "99"
    if count < 0:
        return "0"
    return str(count)


def _split_wrapped_metric_text(text: object, label: str) -> str:
    value = str(text or "").strip()
    prefix = f"{label}("
    if value.startswith(prefix) and value.endswith(")"):
        return value[len(prefix) : -1]
    return value


def _split_ratio_metric_text(text: object, label: str) -> tuple[str, str]:
    inner = _split_wrapped_metric_text(text, label)
    if " / " not in inner:
        return inner, ""
    amount, rate = inner.split(" / ", 1)
    return amount, rate


def _routine_metric_text_label(text: str, color_value: str) -> QLabel:
    label = QLabel(text)
    label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    label.setFocusPolicy(Qt.NoFocus)
    label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    label.setStyleSheet(
        f"QLabel {{ color: {color_value}; }}"
        "QLabel:disabled { color: #9CA3AF; }"
    )
    return label


def _set_fixed_metric_widget_policy(widget: QWidget) -> None:
    widget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)


def _routine_metric_number_label(
    text: str,
    *,
    width: int,
    color_value: str,
) -> QLabel:
    label = _routine_metric_text_label(text, color_value)
    label.setFixedWidth(width)
    label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return label


def _routine_limit_metric_widget(
    text: str,
    *,
    width: int,
    number_widths: dict[str, int],
    color_value: str,
) -> QWidget:
    widget = QWidget()
    widget.setObjectName("routineInstanceBuyLimit")
    widget.setFixedWidth(width)
    _set_fixed_metric_widget_policy(widget)
    widget.setFocusPolicy(Qt.NoFocus)
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(
        ROUTINE_INSTANCE_MONEY_OUTER_PADDING,
        0,
        ROUTINE_INSTANCE_MONEY_OUTER_PADDING,
        0,
    )
    layout.setSpacing(0)
    layout.addWidget(_routine_metric_text_label("한도(", color_value))
    value_slot = QWidget()
    value_slot.setObjectName("routineInstanceBuyLimitValueSlot")
    value_slot.setFixedWidth(number_widths["limit_amount"])
    _set_fixed_metric_widget_policy(value_slot)
    value_stack = QStackedLayout(value_slot)
    value_stack.setContentsMargins(0, 0, 0, 0)
    value_stack.setStackingMode(QStackedLayout.StackOne)

    amount_label = _routine_metric_number_label(
        _split_wrapped_metric_text(text, "한도"),
        width=number_widths["limit_amount"],
        color_value=color_value,
    )
    amount_label.setObjectName("routineInstanceBuyLimitAmount")
    amount_label.setAttribute(Qt.WA_TransparentForMouseEvents, False)

    amount_editor = QLineEdit()
    amount_editor.setObjectName("routineInstanceBuyLimitEditor")
    amount_editor.setFixedWidth(number_widths["limit_amount"])
    amount_editor.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    amount_editor.setFrame(False)
    amount_editor.setStyleSheet(
        "QLineEdit { border: none; background: transparent; padding: 0px; margin: 0px; }"
        "QLineEdit:focus { background: transparent; }"
    )
    amount_editor.hide()

    value_stack.addWidget(amount_label)
    value_stack.addWidget(amount_editor)
    value_stack.setCurrentWidget(amount_label)
    layout.addWidget(value_slot)
    layout.addWidget(_routine_metric_text_label(")", color_value))
    return widget


def _routine_ratio_metric_widget(
    *,
    object_name: str,
    amount_object_name: str,
    rate_object_name: str,
    label_text: str,
    text: str,
    width: int,
    amount_width: int,
    rate_width: int,
    color_value: str,
) -> QWidget:
    widget = QWidget()
    widget.setObjectName(object_name)
    widget.setFixedWidth(width)
    _set_fixed_metric_widget_policy(widget)
    widget.setFocusPolicy(Qt.NoFocus)
    widget.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(
        ROUTINE_INSTANCE_MONEY_OUTER_PADDING,
        0,
        ROUTINE_INSTANCE_MONEY_OUTER_PADDING,
        0,
    )
    layout.setSpacing(0)
    amount_text, rate_text = _split_ratio_metric_text(text, label_text)
    layout.addWidget(_routine_metric_text_label(f"{label_text}(", color_value))
    amount_label = _routine_metric_number_label(
        amount_text,
        width=amount_width,
        color_value=color_value,
    )
    amount_label.setObjectName(amount_object_name)
    layout.addWidget(amount_label)
    layout.addWidget(_routine_metric_text_label(" / ", color_value))
    rate_label = _routine_metric_number_label(
        rate_text,
        width=rate_width,
        color_value=color_value,
    )
    rate_label.setObjectName(rate_object_name)
    layout.addWidget(rate_label)
    layout.addWidget(_routine_metric_text_label(")", color_value))
    return widget


def _format_plain_amount(value: object, *, signed: bool = False) -> str:
    try:
        amount = int(round(float(str(value).replace(",", "").strip())))
    except (TypeError, ValueError):
        return "확인 필요"
    if signed and amount > 0:
        return f"+{amount:,}"
    if signed and amount < 0:
        return f"-{abs(amount):,}"
    return f"{amount:,}"


def _format_percent(value: object, *, digits: int, signed: bool = False) -> str:
    try:
        rate = float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return "확인 필요"
    if signed:
        return f"{rate:+.{digits}f}%"
    return f"{rate:.{digits}f}%"


def routine_instance_buy_limit_text(
    *,
    enabled: bool,
    amount: object = None,
) -> str:
    if not enabled:
        return "한도(미사용)"
    try:
        limit_value = int(float(str(amount).replace(",", "").strip()))
    except (TypeError, ValueError):
        return "한도(확인 필요)"
    if limit_value <= 0:
        return "한도(확인 필요)"
    return f"한도({_format_plain_amount(limit_value)})"


def routine_instance_consumed_text(
    *,
    consumed_amount: object,
    buy_limit_enabled: bool,
    buy_limit_amount: object = None,
    amount_unknown: bool = False,
) -> str:
    amount_text = "확인 필요" if amount_unknown else _format_plain_amount(consumed_amount)
    if not buy_limit_enabled:
        return f"소모({amount_text} / -)"
    try:
        limit_value = float(str(buy_limit_amount).replace(",", "").strip())
        consumed_value = float(str(consumed_amount).replace(",", "").strip())
    except (TypeError, ValueError):
        return f"소모({amount_text} / 확인 필요)"
    if amount_unknown or limit_value <= 0:
        return f"소모({amount_text} / 확인 필요)"
    return f"소모({amount_text} / {_format_percent((consumed_value / limit_value) * 100.0, digits=1)})"


def routine_instance_profit_text(
    *,
    profit_amount: object,
    cost_basis: object,
    unknown: bool = False,
) -> tuple[str, str]:
    if unknown:
        return "수익(확인 필요 / 확인 필요)", "#374151"
    try:
        profit_value = float(str(profit_amount).replace(",", "").strip())
        cost_value = float(str(cost_basis).replace(",", "").strip())
    except (TypeError, ValueError):
        return "수익(확인 필요 / 확인 필요)", "#374151"
    if cost_value > 0:
        rate_text = _format_percent((profit_value / cost_value) * 100.0, digits=2, signed=True)
    else:
        rate_text = "0.00%"
    amount_text = _format_plain_amount(profit_value, signed=True)
    if profit_value > 0:
        color = "#DC2626"
    elif profit_value < 0:
        color = "#2563EB"
    else:
        color = "#374151"
    return f"수익({amount_text} / {rate_text})", color


def create_routine_instance_status_widget(
    status: object,
    *,
    instance_id: str = "",
    registered: int,
    running: int,
    stopped: int,
    error: int,
    buy_limit_text: str = "",
    consumed_text: str = "",
    profit_text: str = "",
    profit_color: str = "",
    enabled: bool,
) -> QWidget:
    display_status, color = routine_status_stamp_spec(status)
    container = QWidget()
    container.setObjectName("routineInstanceStatusContainer")
    container.setFocusPolicy(Qt.NoFocus)

    layout = QHBoxLayout(container)
    layout.setContentsMargins(8, 0, 4, 0)
    layout.setSpacing(0)

    stamp = QWidget()
    stamp.setObjectName("routineInstanceStatusStamp")
    stamp.setFixedSize(ROUTINE_STATUS_STAMP_WIDTH, ROUTINE_STATUS_STAMP_HEIGHT)
    stamp.setFocusPolicy(Qt.NoFocus)
    stamp.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    stamp.setStyleSheet(
        "QWidget#routineInstanceStatusStamp {"
        " background-color: #FFFFFF;"
        f" border: 1px solid {color or '#9CA3AF'};"
        " border-radius: 4px;"
        "}"
        "QWidget#routineInstanceStatusStamp:disabled {"
        " border-color: #D1D5DB;"
        " background-color: #FFFFFF;"
        "}"
    )

    stamp_layout = QHBoxLayout(stamp)
    stamp_layout.setContentsMargins(4, 0, 4, 0)
    stamp_layout.setSpacing(0)
    stamp_layout.setAlignment(Qt.AlignCenter)
    stamp_color = color or "#9CA3AF"
    status_text = QLabel(display_status or "-")
    status_text.setObjectName("routineInstanceStatusText")
    status_text.setAlignment(Qt.AlignCenter)
    for label in (status_text,):
        label.setFocusPolicy(Qt.NoFocus)
        label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        label.setStyleSheet(
            "QLabel {"
            f" color: {stamp_color}; font-weight: 600; border: none;"
            " background-color: transparent;"
            "}"
            "QLabel:disabled { color: #9CA3AF; }"
        )
    stamp_layout.addWidget(status_text, 0, Qt.AlignCenter)

    layout.addWidget(stamp, 0, Qt.AlignVCenter)
    column_widths = routine_instance_grid_columns(container.font())
    number_widths = routine_instance_number_widths(container.font())
    separator_width = routine_instance_separator_width(container.font())
    metric_specs = (
        (
            "routineInstanceRegistered",
            "registered",
            f"등록({routine_instance_count_display(registered)})",
            "#374151",
        ),
        (
            "routineInstanceRunning",
            "running",
            f"실행({routine_instance_count_display(running)})",
            "#374151",
        ),
        (
            "routineInstanceStopped",
            "stopped",
            f"정지({routine_instance_count_display(stopped)})",
            "#374151",
        ),
        (
            "routineInstanceError",
            "error",
            f"오류({routine_instance_count_display(error)})",
            "#374151",
        ),
        ("routineInstanceBuyLimit", "limit", f"{buy_limit_text}", "#374151"),
        ("routineInstanceConsumed", "consumed", f"{consumed_text}", "#374151"),
        (
            "routineInstanceProfit",
            "profit",
            f"{profit_text}",
            profit_color if profit_color else "#374151",
        ),
    )
    for object_name, column_key, text, color_value in metric_specs:
        separator = QLabel("|")
        separator.setObjectName("routineInstanceSeparator")
        separator.setAlignment(Qt.AlignCenter)
        separator.setFixedWidth(separator_width)
        _set_fixed_metric_widget_policy(separator)
        separator.setFocusPolicy(Qt.NoFocus)
        separator.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        separator.setStyleSheet(
            "QLabel#routineInstanceSeparator { color: #9CA3AF; }"
            "QLabel#routineInstanceSeparator:disabled { color: #D1D5DB; }"
        )
        if column_key == "limit":
            metric_widget = _routine_limit_metric_widget(
                str(text or ""),
                width=column_widths[column_key],
                number_widths=number_widths,
                color_value=color_value,
            )
            metric_widget.setProperty("routine_instance_id", str(instance_id or ""))
            amount_label = metric_widget.findChild(QLabel, "routineInstanceBuyLimitAmount")
            amount_editor = metric_widget.findChild(QLineEdit, "routineInstanceBuyLimitEditor")
            if amount_label is not None:
                amount_label.setProperty("routine_instance_id", str(instance_id or ""))
            if amount_editor is not None:
                amount_editor.setProperty("routine_instance_id", str(instance_id or ""))
        elif column_key == "consumed":
            metric_widget = _routine_ratio_metric_widget(
                object_name=object_name,
                amount_object_name="routineInstanceConsumedAmount",
                rate_object_name="routineInstanceConsumedRate",
                label_text="소모",
                text=str(text or ""),
                width=column_widths[column_key],
                amount_width=number_widths["consumed_amount"],
                rate_width=number_widths["consumed_rate"],
                color_value=color_value,
            )
        elif column_key == "profit":
            metric_widget = _routine_ratio_metric_widget(
                object_name=object_name,
                amount_object_name="routineInstanceProfitAmount",
                rate_object_name="routineInstanceProfitRate",
                label_text="수익",
                text=str(text or ""),
                width=column_widths[column_key],
                amount_width=number_widths["profit_amount"],
                rate_width=number_widths["profit_rate"],
                color_value=color_value,
            )
        else:
            metric_widget = QLabel(str(text or ""))
            metric_widget.setObjectName(object_name)
            metric_widget.setAlignment(Qt.AlignCenter)
            metric_widget.setFixedWidth(column_widths[column_key])
            _set_fixed_metric_widget_policy(metric_widget)
            metric_widget.setFocusPolicy(Qt.NoFocus)
            metric_widget.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            metric_widget.setStyleSheet(
                f"QLabel#{object_name} {{ color: {color_value}; }}"
                f"QLabel#{object_name}:disabled {{ color: #9CA3AF; }}"
            )
        layout.addSpacing(ROUTINE_INSTANCE_GRID_SPACING)
        layout.addWidget(separator, 0, Qt.AlignVCenter)
        layout.addSpacing(ROUTINE_INSTANCE_GRID_SPACING)
        layout.addWidget(metric_widget, 0, Qt.AlignVCenter)
    layout.addStretch(1)
    container.setEnabled(bool(enabled))
    return container
ROUTINE_CHECKBOX_HIT_PADDING = 4
ROUTINE_PARENT_EXPAND_OFFSET = 25
ROUTINE_PARENT_EXPAND_WIDTH = 20
def main_sort_routine_table_by_column(window, column: int) -> None:
    """메인 관제창 좌측 루틴표 헤더 정렬."""
    if column < 0 or column >= window.routine_table.columnCount():
        return
    window._main_routine_sort_order = next_sort_order(
        window._main_routine_sort_column,
        column,
        window._main_routine_sort_order,
    )
    window._main_routine_sort_column = column
    main_load_routine_table(window)


def main_sort_running_table_by_column(window, column: int) -> None:
    """메인 관제창 우측 종목표 헤더 정렬."""
    if column < 0 or column >= window.running_stock_table.columnCount():
        return
    window._main_running_sort_order = next_sort_order(
        window._main_running_sort_column,
        column,
        window._main_running_sort_order,
    )
    window._main_running_sort_column = column
    window.running_stock_table.sortItems(column, window._main_running_sort_order)
    window.running_stock_table.horizontalHeader().setSortIndicator(column, window._main_running_sort_order)


def main_apply_routine_sort(window) -> None:
    if 0 <= window._main_routine_sort_column < window.routine_table.columnCount():
        window.routine_table.horizontalHeader().setSortIndicator(
            window._main_routine_sort_column,
            window._main_routine_sort_order,
        )


def main_apply_running_sort(window) -> None:
    if 0 <= window._main_running_sort_column < window.running_stock_table.columnCount():
        window.running_stock_table.sortItems(window._main_running_sort_column, window._main_running_sort_order)
        window.running_stock_table.horizontalHeader().setSortIndicator(
            window._main_running_sort_column,
            window._main_running_sort_order,
        )


def _clear_routine_table_cell_widgets(table) -> None:
    row_count_getter = getattr(table, "rowCount", None)
    row_count = row_count_getter() if callable(row_count_getter) else 0
    column_count = table.columnCount()
    remove_cell_widget = getattr(table, "removeCellWidget", None)
    for row in range(row_count):
        for column in range(column_count):
            widget = table.cellWidget(row, column)
            if widget is None:
                continue
            if callable(remove_cell_widget):
                remove_cell_widget(row, column)
            delete_later = getattr(widget, "deleteLater", None)
            if callable(delete_later):
                delete_later()



def _routine_names_for_stock_record(stock: dict[str, object]) -> list[str]:
    """
    read_base_stocks() 표준 반환값에서 종목의 루틴명 목록을 추출한다.

    중앙 stocks/ 구조에서는 일반적으로 1종목 1루틴이지만,
    기존 호환 반환을 위해 list 형태를 유지한다.
    """
    routines = stock.get("routines", [])
    if isinstance(routines, list):
        return [str(item).strip() for item in routines if str(item).strip()]

    routine_text = str(routines or "").strip()
    return [routine_text] if routine_text else []


def _routine_stock_counts_from_base_stocks() -> dict[str, int]:
    """
    메인 좌측 루틴표의 종목수를 중앙 종목관리 기준으로 계산한다.

    자동매매설정창 하단 목록과 같은 기준을 사용한다.
    - 루틴 미지정 종목 제외
    - 검토관리/검토종목 상태 제외
    """
    counts: dict[str, int] = {}

    for stock in read_base_stocks():
        code = str(stock.get("code", "")).strip()
        name = str(stock.get("name", "")).strip()
        if not code or not name:
            continue

        for routine_name in _routine_names_for_stock_record(stock):
            if not routine_name:
                continue

            stock_dir = stock_runtime_dir_for_routine(routine_name, code, name)
            state = read_json_dict(stock_dir / "state.json") if stock_dir is not None else {}
            if not isinstance(state, dict):
                state = {}

            if is_review_required_state(state):
                continue

            counts[routine_name] = counts.get(routine_name, 0) + 1

    return counts


def _instance_stock_counts() -> dict[str, dict[str, object]]:
    counts: dict[str, dict[str, object]] = {}
    valid_instance_ids = {
        instance.instance_id for instance in load_persisted_routine_instances()
    }
    for stock in read_base_stocks():
        stock_path = str(stock.get("stock_path", "") or "").strip()
        if not stock_path:
            continue
        stock_dir = Path(__file__).resolve().parent / stock_path
        config = read_json_dict(stock_dir / "config.json")
        state = read_json_dict(stock_dir / "state.json")
        instance_id = str(config.get("assigned_routine_instance_id", "") or "").strip()
        if not instance_id or instance_id not in valid_instance_ids:
            continue
        item = counts.setdefault(
            instance_id,
            {
                "registered": 0,
                "running": 0,
                "stopped": 0,
                "error": 0,
                "consumed_amount": 0,
                "consumed_unknown": False,
                "profit_amount": 0,
                "profit_cost_basis": 0,
                "profit_unknown": False,
            },
        )
        item["registered"] += 1
        status = str(state.get("status", "") or "").strip().upper()
        running = auto_trade_setting_trade_started(state)
        if running:
            item["running"] += 1
        else:
            item["stopped"] += 1
        if status == "ERROR":
            item["error"] += 1
        holding_qty = safe_int_value(state.get("holding_qty"), 0)
        avg_price = safe_float_value(state.get("avg_price"), 0.0)
        if holding_qty > 0 and avg_price > 0:
            cost_basis = holding_qty * avg_price
            item["consumed_amount"] = float(item["consumed_amount"]) + cost_basis
            item["profit_cost_basis"] = float(item["profit_cost_basis"]) + cost_basis
            current_price = current_price_from_state(state)
            if current_price is None:
                item["profit_unknown"] = True
            else:
                item["profit_amount"] = float(item["profit_amount"]) + (
                    (current_price - avg_price) * holding_qty
                )
        elif holding_qty > 0:
            item["consumed_unknown"] = True
            item["profit_unknown"] = True
    return counts


def _routine_monitor_sort_value(row: dict[str, object], column: int):
    if column == 0:
        return str(row.get("name", "")).casefold()
    if column == 1:
        return str(row.get("operation_status", "")).casefold()
    if column in {2, 3, 4, 5}:
        return int(row.get(("registered", "running", "stopped", "error")[column - 2], 0) or 0)
    if column == 7:
        return int(row.get("buy_limit_amount", 0) or 0)
    return str(row.get("values", [""] * len(ROUTINE_MONITORING_HEADERS))[column]).casefold()


def main_load_routine_table(window) -> None:
    """등록 루틴의 운영 수와 1차 관제 상태를 메인 좌측 표에 표시한다.

    종목수는 더 이상 루틴폴더 안의 물리 종목폴더 개수로 계산하지 않는다.
    중앙 종목관리(read_base_stocks -> stocks/config.json) 기준으로 계산한다.

    인스턴스 한도는 routine_instances 메타데이터, 소모/손익은 배정 종목
    state.json의 보유수량/평단/현재가 후보 필드만 사용한다.
    """
    instance_counts = _instance_stock_counts()
    definitions = load_routine_definitions()
    instances = load_persisted_routine_instances()
    window._routine_assigned_stock_count_by_instance = {
        instance.instance_id: int(
            instance_counts.get(instance.instance_id, {}).get("registered", 0) or 0
        )
        for instance in instances
    }
    sync_routine_selection_state(window, definitions, instances)
    by_definition: dict[str, list[object]] = {}
    for instance in instances:
        by_definition.setdefault(instance.definition_id, []).append(instance)

    groups: list[dict[str, object]] = []
    collapsed = getattr(window, "_collapsed_routine_definition_ids", set())
    for definition in definitions:
        children: list[dict[str, object]] = []
        for instance in by_definition.get(definition.definition_id, []):
            count = instance_counts.get(
                instance.instance_id,
                {
                    "registered": 0,
                    "running": 0,
                    "stopped": 0,
                    "error": 0,
                    "consumed_amount": 0,
                    "consumed_unknown": False,
                    "profit_amount": 0,
                    "profit_cost_basis": 0,
                    "profit_unknown": False,
                },
            )
            buy_limit_text = routine_instance_buy_limit_text(
                enabled=instance.buy_limit_enabled,
                amount=instance.buy_limit_amount,
            )
            consumed_text = routine_instance_consumed_text(
                consumed_amount=count.get("consumed_amount", 0),
                buy_limit_enabled=instance.buy_limit_enabled,
                buy_limit_amount=instance.buy_limit_amount,
                amount_unknown=bool(count.get("consumed_unknown")),
            )
            profit_text, profit_color = routine_instance_profit_text(
                profit_amount=count.get("profit_amount", 0),
                cost_basis=count.get("profit_cost_basis", 0),
                unknown=bool(count.get("profit_unknown")),
            )
            children.append(
                {
                    "kind": ROUTINE_ROW_CHILD,
                    "definition_id": definition.definition_id,
                    "instance_id": instance.instance_id,
                    "name": instance.display_name,
                    "description": instance.description,
                    "operation_status": str(
                        getattr(window, "_routine_operation_status_by_instance", {}).get(
                            instance.instance_id,
                            ROUTINE_STATUS_DEFAULT,
                        )
                    ),
                    "registered": int(count["registered"]),
                    "running": int(count["running"]),
                    "stopped": int(count["stopped"]),
                    "error": int(count["error"]),
                    "buy_limit_enabled": instance.buy_limit_enabled,
                    "buy_limit_amount": instance.buy_limit_amount,
                    "buy_limit_display": buy_limit_text,
                    "consumed_display": consumed_text,
                    "profit_display": profit_text,
                    "profit_color": profit_color,
                    "rules_path": instance.rules_path,
                }
            )

        parent_registered = sum(int(item["registered"]) for item in children)
        parent_running = sum(int(item["running"]) for item in children)
        parent_error = sum(int(item["error"]) for item in children)
        parent_stopped = max(0, parent_registered - parent_running)
        groups.append(
            {
                "kind": ROUTINE_ROW_PARENT,
                "definition_id": definition.definition_id,
                "name": definition.display_name,
                "operation_status": "",
                "registered": parent_registered,
                "running": parent_running,
                "stopped": parent_stopped,
                "error": parent_error,
                "buy_limit_enabled": False,
                "buy_limit_amount": None,
                "buy_limit_display": "",
                "consumed_display": "",
                "profit_display": "",
                "profit_color": "",
                "collapsed": definition.definition_id in collapsed,
                "children": children,
            }
        )

    sort_column = getattr(window, "_main_routine_sort_column", -1)
    reverse = getattr(window, "_main_routine_sort_order", Qt.AscendingOrder) == Qt.DescendingOrder
    if 0 <= sort_column < len(ROUTINE_MONITORING_HEADERS):
        groups.sort(key=lambda item: _routine_monitor_sort_value(item, sort_column), reverse=reverse)
        for group in groups:
            group["children"].sort(
                key=lambda item: _routine_monitor_sort_value(item, sort_column),
                reverse=reverse,
            )

    rows: list[dict[str, object]] = []
    for group in groups:
        rows.append(group)
        if not group["collapsed"]:
            rows.extend(group["children"])

    _clear_routine_table_cell_widgets(window.routine_table)
    clear_spans = getattr(window.routine_table, "clearSpans", None)
    if callable(clear_spans):
        clear_spans()
    window.routine_table.setRowCount(0)
    window.routine_table.setRowCount(len(rows))

    for row, row_data in enumerate(rows):
        is_parent = row_data["kind"] == ROUTINE_ROW_PARENT
        set_row_height = getattr(window.routine_table, "setRowHeight", None)
        if callable(set_row_height):
            set_row_height(
                row,
                ROUTINE_INSTANCE_ROW_HEIGHT,
            )
        group_enabled = routine_definition_enabled(
            window,
            str(row_data["definition_id"]),
        )
        checked = (
            group_enabled
            if is_parent
            else routine_instance_checked(
                window,
                str(row_data.get("instance_id", "")),
            )
        )
        row_visually_enabled = group_enabled and (is_parent or checked)
        prefix = ("▶ " if row_data.get("collapsed") else "▼ ") if is_parent else ""
        used_amount_text = str(row_data.get("consumed_display", ""))
        buy_limit_text = str(row_data.get("buy_limit_display", ""))
        usage_rate_text = ""
        profit_signal, profit_text, _profit_color = routine_profit_signal()

        parent_aggregate = (
            f"등록({row_data['registered']}) | 실행({row_data['running']}) | "
            f"정지({row_data['stopped']}) | 오류({row_data['error']})"
        )
        child_aggregate = (
            f"| 등록({row_data['registered']}) | 실행({row_data['running']}) | "
            f"정지({row_data['stopped']}) | "
            f"오류({row_data['error']})"
        )
        values = (
            [f"{prefix}{row_data['name']}"] + ([""] * 9)
            if is_parent
            else [
                str(row_data["name"]),
                str(row_data.get("operation_status", "")),
                str(row_data["registered"]),
                str(row_data["running"]),
                str(row_data["stopped"]),
                str(row_data["error"]),
                used_amount_text,
                buy_limit_text,
                usage_rate_text,
                f"● {profit_text}" if profit_text != "-" else "-",
            ]
        )
        row_data["values"] = values

        for col, value in enumerate(values):
            display_value = "" if not is_parent and col > 0 else value
            item = SortableTableWidgetItem(display_value)
            item.setData(ROUTINE_ROW_KIND_ROLE, row_data["kind"])
            item.setData(ROUTINE_DEFINITION_ID_ROLE, row_data["definition_id"])
            item.setData(ROUTINE_INSTANCE_ID_ROLE, row_data.get("instance_id", ""))
            if col == 0:
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
                item.setData(ROUTINE_CHECKBOX_VISUAL_ENABLED_ROLE, row_visually_enabled)
                if is_parent:
                    item.setData(ROUTINE_PARENT_NAME_ROLE, str(row_data["name"]))
                    item.setData(ROUTINE_PARENT_AGGREGATE_ROLE, parent_aggregate)
                    item.setData(
                        ROUTINE_PARENT_COLLAPSED_ROLE,
                        bool(row_data.get("collapsed")),
                    )
                else:
                    item.setData(
                        ROUTINE_CHILD_STATUS_ROLE,
                        str(row_data.get("operation_status", "")),
                    )
                    item.setData(ROUTINE_CHILD_AGGREGATE_ROLE, child_aggregate)
                    item.setData(
                        ROUTINE_CHILD_PROFIT_LED_ROLE,
                        routine_instance_profit_led_state(row_data),
                    )
            if row_data["kind"] == ROUTINE_ROW_CHILD:
                tooltip_parts = [str(row_data.get("name") or "")]
                if row_data.get("description"):
                    tooltip_parts.append(str(row_data["description"]))
                item.setToolTip("\n\n".join(part for part in tooltip_parts if part))
            if not row_visually_enabled:
                item.setForeground(QColor("#9ca3af"))
            if col in {1, 2, 3, 4}:
                try:
                    item.setData(SORT_ROLE, int(str(value).replace(",", "")))
                except Exception:
                    pass
            elif col == 9:
                item.setData(SORT_ROLE, profit_signal)
            if col in {6, 7}:
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            else:
                item.setTextAlignment(Qt.AlignCenter)
            window.routine_table.setItem(row, col, item)

        if is_parent:
            set_span = getattr(window.routine_table, "setSpan", None)
            if callable(set_span):
                set_span(row, 0, 1, window.routine_table.columnCount())
        else:
            set_span = getattr(window.routine_table, "setSpan", None)
            if callable(set_span):
                set_span(row, 1, 1, window.routine_table.columnCount() - 1)
            window.routine_table.setCellWidget(
                row,
                1,
                create_routine_instance_status_widget(
                    row_data.get("operation_status", ""),
                    instance_id=str(row_data.get("instance_id", "")),
                    registered=int(row_data["registered"]),
                    running=int(row_data["running"]),
                    stopped=int(row_data["stopped"]),
                    error=int(row_data["error"]),
                    buy_limit_text=str(row_data.get("buy_limit_display", "")),
                    consumed_text=str(row_data.get("consumed_display", "")),
                    profit_text=str(row_data.get("profit_display", "")),
                    profit_color=str(row_data.get("profit_color", "")),
                    enabled=row_visually_enabled,
                ),
            )

    main_apply_routine_sort(window)



def main_load_running_stock_table(window) -> None:
    """메인 관제창 실행 종목표를 중앙 종목관리 + state 기준으로 표시한다."""
    rows: list[dict[str, object]] = []
    instance_by_id = {
        instance.instance_id: instance
        for instance in load_persisted_routine_instances()
    }

    for stock in read_base_stocks():
        code = str(stock.get("code", "")).strip()
        name = str(stock.get("name", "")).strip()
        routine_list = _routine_names_for_stock_record(stock)
        legacy_routine_name = routine_list[0] if routine_list else ""
        instance_id = str(
            stock.get("assigned_routine_instance_id", "") or ""
        ).strip()
        assigned_instance = instance_by_id.get(instance_id)
        routine_name = (
            assigned_instance.display_name
            if assigned_instance is not None
            else "배정 확인 필요"
        )

        if not code or not name:
            continue

        # 메인 우측 표는 "실행 중 자동매매 종목" 영역이므로
        # 루틴 미지정 종목은 표시하지 않는다.
        if assigned_instance is None and not (instance_id or legacy_routine_name):
            continue

        stock_path = str(stock.get("stock_path", "") or "").strip()
        if assigned_instance is None and not stock_path:
            # Compatibility-only records cannot be resolved to a central config.
            # Preserve their legacy label; central stocks remain explicit review targets.
            routine_name = legacy_routine_name or routine_name
        stock_dir = Path(__file__).resolve().parent / stock_path if stock_path else None
        if stock_dir is None and legacy_routine_name:
            stock_dir = stock_runtime_dir_for_routine(
                legacy_routine_name,
                code,
                name,
            )
        state = read_json_dict(stock_dir / "state.json") if stock_dir is not None else {}
        config = read_json_dict(stock_dir / "config.json") if stock_dir is not None else {}

        if not isinstance(state, dict):
            state = {}
        if not isinstance(config, dict):
            config = {}

        raw_mode = normalize_operation_mode(config.get("operation_mode", "SCHEDULED"))
        operation = "수동" if raw_mode == "CONTINUOUS" else "시간"

        if is_review_required_state(state):
            continue

        trade_started = auto_trade_setting_trade_started(state)
        current_session_trade_started = auto_trade_setting_current_session_trade_started(
            window,
            trade_started,
        )

        holding_qty = safe_int_value(state.get("holding_qty"), 0)
        avg_price = safe_float_value(state.get("avg_price"), 0.0)
        buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state) if stock_dir is not None else (0, 0)
        display_status = auto_trade_setting_display_status_for_current_session(
            state,
            config,
            holding_qty=holding_qty,
            buy_pending_qty=buy_pending_qty,
            sell_pending_qty=sell_pending_qty,
            current_session_trade_started=current_session_trade_started,
            persisted_trade_started=trade_started,
        )

        rows.append(
            {
                "code": code,
                "name": name,
                "routine": routine_name or "미지정",
                "operation": operation,
                "state": state,
                "trade_started": current_session_trade_started,
                "status": display_status,
                "holding": f"{holding_qty:,}",
                "avg_price": format_number_value(avg_price),
                "buy_pending": f"{buy_pending_qty:,}" if isinstance(buy_pending_qty, int) else str(buy_pending_qty),
                "sell_pending": f"{sell_pending_qty:,}" if isinstance(sell_pending_qty, int) else str(sell_pending_qty),
            }
        )

    window.running_stock_table.setRowCount(len(rows))

    for row_index, row in enumerate(rows):
        values = [
            row["code"],
            row["name"],
            row["routine"],
            row["operation"],
            "",
            row["status"],
            row["holding"],
            row["avg_price"],
            row["buy_pending"],
            row["sell_pending"],
        ]

        for col, value in enumerate(values):
            if col == 4:
                item = create_auto_trade_situation_item(
                    row.get("state") if isinstance(row.get("state"), dict) else {},
                    bool(row.get("trade_started")),
                    str(row.get("status", "")),
                )
            elif col == 5:
                item = create_auto_trade_setting_status_item(str(value))
            else:
                item = SortableTableWidgetItem(str(value))
                if col in {6, 7, 8, 9}:
                    try:
                        item.setData(SORT_ROLE, int(str(value).replace(",", "").replace("-", "0")))
                    except Exception:
                        pass
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                else:
                    item.setTextAlignment(Qt.AlignCenter)

            if col == 3 and str(value) == "수동":
                item.setForeground(QColor("#8A2BE2"))
            window.running_stock_table.setItem(row_index, col, item)

    main_apply_running_sort(window)
