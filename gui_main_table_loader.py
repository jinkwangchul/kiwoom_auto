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

from dataclasses import replace
from datetime import datetime
import json
import math
from pathlib import Path
from typing import Callable, Iterable

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont, QFontMetrics
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QStackedLayout, QStyle, QWidget

from gui_table_utils import next_sort_order
from gui_common_utils import safe_int_value
from routine_tree_title_display import (
    tree_title_slot_width,
    tree_title_text,
    tree_title_tooltip,
)
from gui_config_utils import default_config
from execution_fill_recorder import read_execution_fill_records
from gui_stock_data import load_stock_library_snapshot, stock_runtime_dir_for_routine
from gui_order_utils import (
    pending_order_side_quantities,
    format_number_value,
    format_signed_money,
    format_signed_percent,
)
from gui_review_utils import average_price_from_state, current_price_from_state, safe_float_value
from confirmable_pnl_cycle_service import project_confirmable_cumulative_pnl
from pnl_ui_refresh import project_current_stock_pnl_snapshot
from gui_operation_environment import (
    effective_amount_starting_budget,
    read_system_total_budget_for_recalculation,
    stock_limit_digit_alignment_enabled,
    starting_budget_defaults,
    suggested_buy_limit,
)
from runtime_io import read_json_dict
from running_budget_adjustment import (
    STATE_WAIT_SELL,
    project_running_budget_adjustment_config,
    project_running_budget_adjustment_display_config,
)
from gui_auto_trade_display import (
    AUTO_TRADE_SETTING_BADGE_HEIGHT,
    RatioMetricDisplay,
    apply_auto_trade_plain_metric_item_style,
    apply_auto_trade_setting_activity_style,
    apply_auto_trade_setting_liquidation_style,
    apply_auto_trade_setting_protection_row_style,
    auto_trade_setting_badge_stylesheet,
    auto_trade_setting_status_sort_rank,
    confirmable_stock_profit_metric,
    create_auto_trade_operation_item,
    create_routine_profit_signal_widget,
    create_auto_trade_setting_activity_status_item,
    create_auto_trade_stock_name_item,
    format_routine_buy_limit,
    format_routine_buy_limit_usage,
    format_routine_used_amount,
    profit_loss_value_color,
    ratio_metric_text,
    ratio_metric_width,
    routine_profit_signal,
    split_ratio_metric_text,
    split_wrapped_metric_text,
    stock_position_metric_width,
    stock_position_metric_values,
    stock_position_display_values,
    SORT_ROLE,
    SortableTableWidgetItem,
)
from gui_auto_trade_situation import create_auto_trade_situation_item
from gui_auto_trade_integrity import (
    inspect_review_state_data,
    inspect_stock_review_state,
    is_operation_excluded,
)
from gui_auto_trade_policy import (
    auto_trade_start_budget_current_running,
    auto_trade_stock_operation_category,
    auto_trade_setting_trade_started,
    auto_trade_setting_current_session_trade_started,
    auto_trade_setting_display_status_for_current_session,
    auto_trade_setting_row_projection,
)
from gui_auto_trade_policy import (
    auto_trade_operation_display,
)
from gui_base_stock_service import normalize_stock_code, read_base_stocks
from gui_routine_registry import get_group_records
from main_group_projection import build_main_group_projection
from kiwoom_stock_library_service import master_status_tokens
from routine_instance_registry import (
    load_persisted_routine_instances,
    load_routine_definitions,
)
from gui_main_routine_selection import (
    routine_definition_enabled,
    routine_instance_checked,
    sync_routine_selection_state,
)


ROUTINE_MONITORING_HEADERS = (
    "루틴명",
    "상태",
    "등록",
    "제외",
    "운영/정지",
    "검토관리",
    "사용금액",
    "매수한도",
    "사용률",
    "수익률",
)

LOGIN_NOT_STARTED = "LOGIN_NOT_STARTED"
SERVER_AUTH_PENDING = "SERVER_AUTH_PENDING"
SERVER_AUTH_COMPLETE = "SERVER_AUTH_COMPLETE"


def main_budget_display_auth_state(window) -> str:
    api = getattr(window, "kiwoom_api", None)
    is_connected = getattr(api, "is_connected", None)
    try:
        connected = callable(is_connected) and is_connected() is True
    except Exception:
        connected = False
    if not connected:
        return LOGIN_NOT_STARTED

    account_getter = getattr(window, "selected_account_no", None)
    try:
        account = str(account_getter() if callable(account_getter) else "").strip()
    except Exception:
        account = ""
    authentication_states = getattr(window, "_account_authentication_states", None)
    if (
        account
        and isinstance(authentication_states, dict)
        and str(authentication_states.get(account) or "").strip().upper() == "READY"
    ):
        return SERVER_AUTH_COMPLETE
    return SERVER_AUTH_PENDING

MAIN_MONITORING_TABLE_FONT_FAMILY = "Malgun Gothic"
MAIN_MONITORING_CELL_FONT_FAMILY = "Gulim"
MAIN_MONITORING_FONT_POINT_SIZE = 9


def _main_monitoring_font(family: str) -> QFont:
    font = QFont(family, MAIN_MONITORING_FONT_POINT_SIZE)
    font.setWeight(QFont.Normal)
    font.setBold(False)
    font.setItalic(False)
    return font


def main_monitoring_table_font() -> QFont:
    """Return the font currently rendered by the monitoring table and painter."""
    return _main_monitoring_font(MAIN_MONITORING_TABLE_FONT_FAMILY)


def main_monitoring_cell_font() -> QFont:
    """Return the current embedded-cell font without changing its geometry."""
    return _main_monitoring_font(MAIN_MONITORING_CELL_FONT_FAMILY)


ROUTINE_STATUS_RUNNING = "운  영"
ROUTINE_STATUS_STOPPED = "정  지"
ROUTINE_STATUS_DEFAULT = ROUTINE_STATUS_STOPPED
ROUTINE_STATUS_EARLY_CLOSE = "조기마감"
ROUTINE_STATUS_IMMEDIATE_LIQUIDATION = "즉시청산"
ROUTINE_STATUS_COMPLETED = "매매완료"
ROUTINE_STATUS_PARTIAL_COMPLETION = "일부완료"
ROUTINE_COMPLETION_STATUSES = frozenset(
    {ROUTINE_STATUS_COMPLETED, ROUTINE_STATUS_PARTIAL_COMPLETION}
)
ROUTINE_STATUS_STAMP_COLORS = {
    ROUTINE_STATUS_RUNNING: "#16A34A",
    ROUTINE_STATUS_STOPPED: "#DC2626",
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
ROUTINE_CHILD_COLLAPSED_ROLE = Qt.UserRole + 211
ROUTINE_CHILD_HAS_STOCKS_ROLE = Qt.UserRole + 212
ROUTINE_STOCK_CODE_ROLE = Qt.UserRole + 213
ROUTINE_STOCK_NAME_ROLE = Qt.UserRole + 214
ROUTINE_STOCK_VALUES_ROLE = Qt.UserRole + 215
ROUTINE_STOCK_PATH_ROLE = Qt.UserRole + 216
ROUTINE_STOCK_METRICS_ROLE = Qt.UserRole + 217
ROUTINE_STOCK_PROFIT_LED_ROLE = Qt.UserRole + 218
ROUTINE_STOCK_INITIAL_BUY_ROLE = Qt.UserRole + 219
ROUTINE_STOCK_DISPLAY_ROLE = Qt.UserRole + 220
ROUTINE_PARENT_AGGREGATE_VALUES_ROLE = Qt.UserRole + 221
ROUTINE_PARENT_PROFIT_ROLE = Qt.UserRole + 222
ROUTINE_GROUP_ID_ROLE = Qt.UserRole + 223
ROUTINE_GROUP_PATH_ROLE = Qt.UserRole + 224
ROUTINE_STOCK_TOOLTIP_DATA_ROLE = Qt.UserRole + 225
_MAIN_PNL_STATIC_CACHE_ATTR = "_main_pnl_refresh_static_cache"
_MAIN_REFRESH_READ_CONTEXT_ATTR = "_main_refresh_read_context"
ROUTINE_ROW_PARENT = "group"
ROUTINE_ROW_CHILD = "instance"
ROUTINE_ROW_STOCK = "stock"
MAIN_STOCK_OPERATION_CATEGORY_LABELS = {
    "operation": "운영",
    "waiting": "대기",
    "excluded": "제외",
    "review": "검토",
}


def build_main_refresh_read_context(window=None) -> dict[str, object]:
    """Read one refresh-local canonical projection input set for Main."""

    definitions = tuple(load_routine_definitions())
    instances = tuple(load_persisted_routine_instances())
    groups = tuple(get_group_records())
    stocks = tuple(read_base_stocks())
    stock_data_by_dir: dict[str, dict[str, object]] = {}
    for stock in stocks:
        stock_path = str(stock.get("stock_path", "") or "").strip()
        if not stock_path:
            continue
        stock_dir = Path(__file__).resolve().parent / stock_path
        loaded_state = read_json_dict(stock_dir / "state.json")
        review_inspection = inspect_stock_review_state(
            stock_dir,
            loaded_state=loaded_state,
        )
        config = read_json_dict(stock_dir / "config.json")
        stock_data_by_dir[str(stock_dir)] = {
            "config": dict(config) if isinstance(config, dict) else {},
            "state": dict(review_inspection.state),
            "review_inspection": review_inspection,
            "state_issue_reason": str(
                getattr(review_inspection, "issue_reason", "") or ""
            ),
        }
    return {
        "definitions": definitions,
        "instances": instances,
        "groups": groups,
        "stocks": stocks,
        "stock_data_by_dir": stock_data_by_dir,
    }


def _main_refresh_read_context(window) -> dict[str, object] | None:
    context = getattr(window, _MAIN_REFRESH_READ_CONTEXT_ATTR, None)
    return context if isinstance(context, dict) else None


def _main_refresh_stock_data(window, stock_dir: Path | None) -> dict[str, object]:
    if stock_dir is None:
        return {
            "config": {},
            "state": {},
            "review_inspection": inspect_review_state_data(
                {},
                source="MAIN_LEGACY_STOCK_RECORD",
            ),
        }
    context = _main_refresh_read_context(window)
    if context is not None:
        stock_data = context.get("stock_data_by_dir", {})
        if isinstance(stock_data, dict):
            cached = stock_data.get(str(stock_dir))
            if isinstance(cached, dict):
                return cached
    loaded_state = read_json_dict(stock_dir / "state.json")
    review_inspection = inspect_stock_review_state(
        stock_dir,
        loaded_state=loaded_state,
    )
    config = read_json_dict(stock_dir / "config.json")
    return {
        "config": dict(config) if isinstance(config, dict) else {},
        "state": dict(review_inspection.state),
        "review_inspection": review_inspection,
    }
ROUTINE_PARENT_CHECKBOX_OFFSET = 4
ROUTINE_CHILD_CHECKBOX_OFFSET = 24
ROUTINE_STOCK_CHECKBOX_OFFSET = 45
ROUTINE_STOCK_TEXT_OFFSET = ROUTINE_STOCK_CHECKBOX_OFFSET
ROUTINE_STOCK_BASE_COLUMN_WIDTHS = (214, 176, 116, 34, 104, 58, 120)
MAIN_STOCK_METRIC_LAYOUT_PREVIEW = False
ROUTINE_CHECKBOX_SIZE = 16
ROUTINE_PROFIT_LED_BOX_SIZE = 18
ROUTINE_PROFIT_LED_SIZE = 18
ROUTINE_PROFIT_LED_GAP = 4
ROUTINE_INSTANCE_NAME_WIDTH = 270
ROUTINE_INSTANCE_ROW_HEIGHT = 28
ROUTINE_STOCK_ROW_HEIGHT = 24
ROUTINE_STATUS_STAMP_WIDTH = 82
# The painted stock badge's 1px outline occupies the full stock-row footprint.
ROUTINE_STATUS_STAMP_HEIGHT = ROUTINE_STOCK_ROW_HEIGHT
ROUTINE_STATUS_STAMP_GRID_INSET = 8
ROUTINE_LIMIT_SETTINGS_BADGE_WIDTH = 64
ROUTINE_LIMIT_SETTINGS_BADGE_GAP = 4
ROUTINE_INSTANCE_GRID_COLUMN_SAMPLES = {
    "status": "[기본운영]",
    "registered": "등록(99)",
    "excluded": "제외(99)",
    "operation_or_stopped": "운영(99)",
    "review": "검토(99)",
    "limit": "[설정] 한도(99,999,999)",
    "consumed": "소모(99,999,999 / 100.0%)",
    "profit": "수익(-99,999,999 / -99.99%)",
}
ROUTINE_INSTANCE_GRID_PADDING = 12
ROUTINE_INSTANCE_COUNT_GRID_PADDING = 4
ROUTINE_INSTANCE_GRID_SPACING = 0
ROUTINE_INSTANCE_SEPARATOR_PADDING = 8
ROUTINE_AGGREGATE_LEADING_GAP = 14
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
    {"registered", "excluded", "operation_or_stopped", "review"}
)
ROUTINE_AGGREGATE_COLUMN_KEYS = (
    "registered",
    "excluded",
    "operation_or_stopped",
    "review",
)
ROUTINE_AGGREGATE_LABELS = {
    "registered": ("등록",),
    "excluded": ("제외",),
    "operation_or_stopped": ("운영", "정지"),
    "review": ("검토",),
}
ROUTINE_AGGREGATE_NUMBER_SAMPLES = ("199", "999")
ROUTINE_INSTANCE_AMOUNT_SAMPLES = {
    "limit_amount": ("-99,999,999", "99,999,999", "미설정", "대기"),
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
    for column_key in ROUTINE_AGGREGATE_COLUMN_KEYS:
        columns[column_key] = routine_aggregate_metric_width(column_key, font)
    number_widths = routine_instance_number_widths(font)
    columns["limit"] = (
        ROUTINE_LIMIT_SETTINGS_BADGE_WIDTH
        + ROUTINE_LIMIT_SETTINGS_BADGE_GAP
        + metrics.horizontalAdvance("한도(")
        + number_widths["limit_amount"]
        + metrics.horizontalAdvance(")")
        + (ROUTINE_INSTANCE_MONEY_OUTER_PADDING * 2)
    )
    columns["consumed"] = (
        ratio_metric_width(
            label="소모",
            left_width=number_widths["consumed_amount"],
            right_width=number_widths["consumed_rate"],
            font=font,
            outer_padding=ROUTINE_INSTANCE_MONEY_OUTER_PADDING,
        )
    )
    columns["profit"] = (
        ratio_metric_width(
            label="수익",
            left_width=number_widths["profit_amount"],
            right_width=number_widths["profit_rate"],
            font=font,
            outer_padding=ROUTINE_INSTANCE_MONEY_OUTER_PADDING,
        )
    )
    return columns


def routine_aggregate_number_slot_width(font: QFont | None = None) -> int:
    metrics = QFontMetrics(font or QFont())
    return max(
        metrics.horizontalAdvance(sample)
        for sample in ROUTINE_AGGREGATE_NUMBER_SAMPLES
    )


def routine_aggregate_label_width(
    column_key: str,
    font: QFont | None = None,
) -> int:
    metrics = QFontMetrics(font or QFont())
    return max(
        metrics.horizontalAdvance(label)
        for label in ROUTINE_AGGREGATE_LABELS[column_key]
    )


def routine_aggregate_metric_width(
    column_key: str,
    font: QFont | None = None,
) -> int:
    metrics = QFontMetrics(font or QFont())
    return (
        routine_aggregate_label_width(column_key, font)
        + metrics.horizontalAdvance("(")
        + routine_aggregate_number_slot_width(font)
        + metrics.horizontalAdvance(")")
    )


def routine_aggregate_slot_lefts(
    start_x: int,
    font: QFont | None = None,
) -> tuple[int, ...]:
    column_widths = routine_instance_grid_columns(font)
    separator_width = routine_aggregate_separator_width(font)
    left = int(start_x) + ROUTINE_AGGREGATE_LEADING_GAP
    result: list[int] = []
    for column_key in ROUTINE_AGGREGATE_COLUMN_KEYS:
        result.append(left)
        left += column_widths[column_key] + separator_width
    return tuple(result)


def routine_instance_number_widths(font: QFont | None = None) -> dict[str, int]:
    metrics = QFontMetrics(font or QFont())
    return {
        key: max(metrics.horizontalAdvance(sample) for sample in samples)
        + routine_instance_number_padding(key)
        for key, samples in ROUTINE_INSTANCE_AMOUNT_SAMPLES.items()
    }


def routine_stock_position_value_widths(font: QFont | None = None) -> dict[str, tuple[int, int]]:
    metrics = QFontMetrics(font or QFont())
    instance_widths = routine_instance_number_widths(font)
    money_width = max(
        instance_widths["profit_amount"],
        metrics.horizontalAdvance("-999,999,999"),
    )
    rate_width = instance_widths["profit_rate"]
    return {
        "보유": (
            max(metrics.horizontalAdvance("9999주"), metrics.horizontalAdvance("0주")),
            money_width,
        ),
        "가격": (
            max(metrics.horizontalAdvance("9,999,999"), metrics.horizontalAdvance("-")),
            max(metrics.horizontalAdvance("9,999,999"), metrics.horizontalAdvance("-")),
        ),
        "수익": (
            money_width,
            rate_width,
        ),
        "매매": (
            metrics.horizontalAdvance("9999"),
            metrics.horizontalAdvance("9999"),
        ),
        "소모": (
            instance_widths["consumed_amount"],
            instance_widths["consumed_rate"],
        ),
    }


def routine_stock_column_widths(font: QFont | None = None) -> tuple[int, ...]:
    metrics = QFontMetrics(font or QFont())
    value_widths = routine_stock_position_value_widths(font)
    metric_widths = tuple(
        stock_position_metric_width(
            label=label,
            value_widths=value_widths,
            font=font,
            outer_padding=ROUTINE_INSTANCE_MONEY_OUTER_PADDING,
        )
        for label in ("보유", "가격", "수익", "매매")
    )
    instance_widths = routine_instance_grid_columns(font)
    group_title_column_width = max(
        ROUTINE_STOCK_BASE_COLUMN_WIDTHS[0],
        ROUTINE_PARENT_CHECKBOX_OFFSET
        + metrics.horizontalAdvance("▼ ")
        + tree_title_slot_width(metrics)
        + 8,
    )
    return (
        group_title_column_width,
        *ROUTINE_STOCK_BASE_COLUMN_WIDTHS[1:],
        *metric_widths,
        instance_widths["limit"],
        instance_widths["consumed"],
    )


def _table_cell_elided_tooltip(table, column: int, text: object) -> str:
    value = str(text or "")
    style = table.style()
    item_margin = style.pixelMetric(QStyle.PM_FocusFrameHMargin, None, table) + 1
    section_border_width = (
        style.pixelMetric(QStyle.PM_DefaultFrameWidth, None, table)
        if table.showGrid()
        else 0
    )
    available_width = max(
        0,
        table.columnWidth(column) - (item_margin * 2) - section_border_width,
    )
    return value if table.fontMetrics().horizontalAdvance(value) > available_width else ""


def _stock_library_tooltip_metadata_by_code() -> dict[str, dict[str, object]]:
    try:
        records = load_stock_library_snapshot().records
    except Exception:
        return {}
    return {
        str(record.get("code", "") or "").strip().upper().lstrip("A"): {
            "market": str(record.get("market", "") or "").strip().upper(),
            "nxt_available": record.get("nxt_available") is True,
            "status": str(record.get("status", "") or "").strip(),
        }
        for record in records
        if isinstance(record, dict) and str(record.get("code", "") or "").strip()
    }


def main_monitoring_stock_status_text(value: object) -> str:
    """Project master status evidence for the Main monitoring tooltip only."""

    visible_tokens = [
        token
        for token in master_status_tokens(value)
        if not token.startswith("증거금")
        and token not in {"담보대출", "신용가능"}
    ]
    return " | ".join(visible_tokens) or "-"


def _main_stock_row_tooltip(
    market: object,
    stock_code: object,
    stock_name: object,
    current_price: object,
    *,
    nxt_available: bool = False,
    stock_state: dict[str, object] | None = None,
    stock_status: object = None,
) -> str:
    state = stock_state if isinstance(stock_state, dict) else {}

    def finite_number(key: str) -> float | None:
        value = state.get(key)
        if isinstance(value, bool) or value in (None, "", "-"):
            return None
        try:
            number = float(str(value).replace(",", "").replace("%", "").strip())
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    def price_text(value: object) -> str:
        if isinstance(value, bool) or value in (None, "", "-"):
            return "-"
        try:
            number = float(str(value).replace(",", "").strip())
        except (TypeError, ValueError):
            return "-"
        if not math.isfinite(number) or number <= 0:
            return "-"
        return format_number_value(number)

    def signed_percent_text(key: str) -> str:
        number = finite_number(key)
        return f"{number:+.2f}%" if number is not None else "-"

    def strength_text() -> str:
        number = finite_number("execution_strength")
        return f"{number:.1f}" if number is not None and number >= 0 else "-"

    market_text = str(market or "").strip().upper() or "-"
    code = str(stock_code or "").strip()
    name = str(stock_name or "").strip()
    status_text = main_monitoring_stock_status_text(stock_status)
    first_line = [f"▪  {code} {name}", market_text, f"상태 {status_text}"]
    if nxt_available:
        first_line.append("NXT")
    second_line = [
        f"▪  현재가 {price_text(current_price)}",
        f"시가 {price_text(state.get('open_price'))}",
        f"고가 {price_text(state.get('high_price'))}",
        f"저가 {price_text(state.get('low_price'))}",
    ]
    third_line = [
        f"▪  등락률 {signed_percent_text('change_rate')}",
        f"전일대비 {signed_percent_text('previous_day_volume_rate')}",
        f"체결강도 {strength_text()}",
    ]
    separator = "  |  "
    return "\n".join(
        separator.join(parts)
        for parts in (first_line, second_line, third_line)
    )


def main_stock_row_tooltip_from_projection(
    projection: object,
    live_state: object = None,
) -> str:
    data = dict(projection) if isinstance(projection, dict) else {}
    state = (
        dict(data.get("stock_state"))
        if isinstance(data.get("stock_state"), dict)
        else {}
    )
    current_price = data.get("current_price")
    if live_state is not None:
        current_price = getattr(live_state, "last_price", current_price)
        for field in (
            "open_price",
            "high_price",
            "low_price",
            "change_rate",
            "previous_day_volume_rate",
            "execution_strength",
        ):
            value = getattr(live_state, field, None)
            if value is not None:
                state[field] = value
    return _main_stock_row_tooltip(
        data.get("market", ""),
        data.get("stock_code", ""),
        data.get("stock_name", ""),
        current_price,
        nxt_available=bool(data.get("nxt_available") is True),
        stock_state=state,
        stock_status=data.get("stock_status"),
    )


def main_stock_operator_status(
    window,
    *,
    stock_code: object,
    stock_state: dict[str, object] | None,
    operation_excluded: bool,
    review_required: bool,
) -> str:
    """Project one Stock into the same operator category used by Main badges."""

    state = stock_state if isinstance(stock_state, dict) else {}
    category = auto_trade_stock_operation_category(
        window,
        stock_code=stock_code,
        persisted_trade_started=auto_trade_setting_trade_started(state),
        operation_excluded=bool(operation_excluded),
        review_required=bool(review_required),
    )
    return MAIN_STOCK_OPERATION_CATEGORY_LABELS.get(category, "-")


def routine_instance_status_column_left(font: QFont | None = None) -> int:
    return ROUTINE_STOCK_TEXT_OFFSET + routine_stock_column_widths(
        font or main_monitoring_table_font()
    )[0]


ROUTINE_STOCK_COLUMN_WIDTHS = (*ROUTINE_STOCK_BASE_COLUMN_WIDTHS, 174, 154, 174, 110, 148, 226)
RUNTIME_FILLS_PATH = Path(__file__).resolve().parent / "runtime" / "fills.json"


def stock_trade_counts_by_code(
    fill_records: object,
    *,
    trading_day: str,
) -> dict[str, tuple[int, int]]:
    """Count distinct filled BUY/SELL orders per stock for one trading day."""

    identities_by_code: dict[str, dict[str, set[tuple[str, str]]]] = {}
    records = fill_records if isinstance(fill_records, (list, tuple)) else ()
    for record in records:
        if not isinstance(record, dict):
            continue
        received_at = str(
            record.get("received_at") or record.get("recorded_at") or ""
        ).strip()
        if received_at[:10] != trading_day:
            continue
        if str(record.get("event_type") or "").strip().upper() not in {
            "PARTIAL_FILL",
            "FULL_FILL",
        }:
            continue
        code = str(record.get("code") or "").strip().lstrip("A")
        side = str(record.get("side") or "").strip().upper()
        if not code or side not in {"BUY", "SELL"}:
            continue
        identity = next(
            (
                str(record.get(field) or "").strip()
                for field in (
                    "broker_order_no",
                    "order_id",
                    "order_queued_id",
                    "execution_id",
                    "fill_id",
                )
                if str(record.get(field) or "").strip()
            ),
            "",
        )
        if not identity:
            continue
        account_no = str(record.get("account_no") or "").strip()
        side_identities = identities_by_code.setdefault(
            code,
            {"BUY": set(), "SELL": set()},
        )
        side_identities[side].add((account_no, identity))

    return {
        code: (len(sides["BUY"]), len(sides["SELL"]))
        for code, sides in identities_by_code.items()
    }


def current_stock_trade_counts_by_code(
    fills_path: str | Path = RUNTIME_FILLS_PATH,
    *,
    now: datetime | None = None,
) -> dict[str, tuple[int, int]]:
    snapshot = read_execution_fill_records(fills_path)
    if snapshot.get("ok") is not True:
        return {}
    trading_day = (now or datetime.now().astimezone()).date().isoformat()
    return stock_trade_counts_by_code(
        snapshot.get("records"),
        trading_day=trading_day,
    )


def normalize_initial_buy_mode(value: object) -> str:
    normalized = str(value or "").strip().upper()
    if not normalized:
        return "QUANTITY"
    if normalized in {"QUANTITY", "QTY", "SHARES", "SHARE", "주수"}:
        return "QUANTITY"
    return "AMOUNT"


def stock_initial_buy_display(
    config: dict[str, object],
    *,
    current_price: object = None,
    policy: dict[str, object] | None = None,
) -> dict[str, object]:
    mode = normalize_initial_buy_mode(config.get("trade_amount_type"))
    defaults = starting_budget_defaults(policy)
    if mode == "QUANTITY":
        configured = safe_int_value(config.get("buy_qty"), 0)
        value = configured if configured > 0 else int(defaults["quantity"])
        return {
            "mode": mode,
            "badge": "주수",
            "value": value,
            "value_text": f"{value:,}주",
        }
    configured = safe_int_value(config.get("buy_amount"), 0)
    suggested = (
        None
        if configured > 0
        else effective_amount_starting_budget(
            current_price,
            defaults["amount_multiplier"],
        )
    )
    value = configured if configured > 0 else int(suggested or 0)
    return {
        "mode": mode,
        "badge": "금액",
        "value": value,
        "value_text": f"{value:,}원" if value > 0 else "-",
    }


def stock_starting_budget_amount(
    config: dict[str, object],
    *,
    current_price: object = None,
    policy: dict[str, object] | None = None,
) -> int | None:
    initial_buy = stock_initial_buy_display(
        config,
        current_price=current_price,
        policy=policy,
    )
    value = safe_int_value(initial_buy.get("value"), 0)
    if value <= 0:
        return None
    if initial_buy.get("mode") == "AMOUNT":
        return value
    return effective_amount_starting_budget(current_price, value)


def main_stock_current_price(
    window,
    stock: dict[str, object],
    state: dict[str, object] | None,
) -> float | None:
    """Resolve Main-row display price from Last Known data before persisted fallback."""

    persisted = current_price_from_state(state if isinstance(state, dict) else {})
    code = str(stock.get("code", "") or "").strip().lstrip("A")
    host_getter = getattr(window, "main_monitoring_auto_trade_operation_host", None)
    if not code or not callable(host_getter):
        return persisted
    try:
        host = host_getter()
        state_getter = getattr(host, "monitoring_market_information_state", None)
        market_state = state_getter(code) if callable(state_getter) else None
        current = safe_float_value(getattr(market_state, "last_price", None), 0.0)
    except Exception:
        current = 0.0
    return current if current > 0 else persisted


def main_stock_fresh_market_information_state(
    window,
    stock: dict[str, object],
):
    code = str(stock.get("code", "") or "").strip().upper().lstrip("A")
    host_getter = getattr(window, "main_monitoring_auto_trade_operation_host", None)
    if not code or not callable(host_getter):
        return None
    try:
        host = host_getter()
        state_getter = getattr(
            host,
            "fresh_monitoring_market_information_state",
            None,
        )
        market_state = state_getter(code) if callable(state_getter) else None
    except Exception:
        return None
    if not str(getattr(market_state, "login_session_id", "") or "").strip():
        return None
    if safe_float_value(getattr(market_state, "last_price", None), 0.0) <= 0:
        return None
    return market_state


def main_stock_resolved_starting_budget(
    window,
    stock: dict[str, object],
    config: dict[str, object],
    *,
    policy: dict[str, object] | None = None,
) -> int | None:
    fresh_state = main_stock_fresh_market_information_state(window, stock)
    if fresh_state is None:
        return None
    last_price = safe_float_value(getattr(fresh_state, "last_price", None), 0.0)
    defaults = starting_budget_defaults(policy)
    mode = normalize_initial_buy_mode(config.get("trade_amount_type"))
    configured_value = safe_int_value(
        config.get("buy_qty") if mode == "QUANTITY" else config.get("buy_amount"),
        0,
    )
    stock_key = str(stock.get("stock_path", "") or "").strip() or "|".join(
        (
            str(stock.get("code", "") or "").strip(),
            str(stock.get("name", "") or "").strip(),
        )
    )
    signature = (
        int(getattr(fresh_state, "connection_epoch", 0) or 0),
        str(getattr(fresh_state, "login_session_id", "") or "").strip(),
        mode,
        configured_value,
        int(defaults["quantity"]),
        float(defaults["amount_multiplier"]),
        last_price,
    )
    cache = getattr(window, "_main_stock_resolved_starting_budget_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        setattr(window, "_main_stock_resolved_starting_budget_cache", cache)
    cached = cache.get(stock_key)
    if isinstance(cached, dict) and cached.get("signature") == signature:
        return cached.get("amount")
    amount = stock_starting_budget_amount(
        config,
        current_price=last_price,
        policy={"starting_budget_defaults": defaults},
    )
    cache[stock_key] = {"signature": signature, "amount": amount}
    return amount


def main_stock_resolved_initial_buy_display(
    window,
    stock: dict[str, object],
    config: dict[str, object],
    *,
    policy: dict[str, object] | None = None,
) -> dict[str, object]:
    base = stock_initial_buy_display(config, current_price=None, policy=policy)
    auth_state = main_budget_display_auth_state(window)
    if auth_state == SERVER_AUTH_PENDING:
        return {**base, "value_text": "대기"}
    if auth_state == LOGIN_NOT_STARTED:
        return base
    amount = main_stock_resolved_starting_budget(
        window,
        stock,
        config,
        policy=policy,
    )
    if amount is None:
        return {**base, "value_text": "대기"}
    if base.get("mode") != "AMOUNT":
        return base
    return {
        **base,
        "value": int(amount),
        "value_text": f"{amount:,}원",
    }


def routine_stock_initial_buy_mode_sort_value(
    stock_row: dict[str, object],
    preferred_mode: str,
) -> bool:
    initial_buy = stock_row.get("initial_buy")
    if not isinstance(initial_buy, dict):
        return False
    return str(initial_buy.get("mode", "") or "").upper() == str(
        preferred_mode or ""
    ).upper()


def sort_routine_stock_rows_by_initial_buy_mode(
    stock_rows: list[dict[str, object]],
    preferred_mode: str,
) -> None:
    stock_rows.sort(
        key=lambda stock: routine_stock_initial_buy_mode_sort_value(
            stock,
            preferred_mode,
        ),
        reverse=True,
    )


ROUTINE_INSTANCE_GRID_COLUMNS = {
    "status": ROUTINE_STATUS_STAMP_WIDTH,
    "registered": 60,
    "excluded": 60,
    "operation_or_stopped": 60,
    "review": 78,
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


def routine_profit_led_state_from_signal(signal: object) -> str:
    return {
        "LOSS": "red",
        "COST_NOT_RECOVERED": "yellow",
        "NET_PROFIT": "green",
        "NEUTRAL": "gray",
    }.get(str(signal or "").strip().upper(), "gray")


def routine_instance_separator_width(font: QFont | None = None) -> int:
    metrics = QFontMetrics(font or QFont())
    return metrics.horizontalAdvance("|")


def routine_aggregate_separator_width(font: QFont | None = None) -> int:
    return (
        routine_instance_separator_width(font)
        + (ROUTINE_INSTANCE_SEPARATOR_PADDING * 2)
    )


def routine_status_stamp_spec(status: object) -> tuple[str, str]:
    display_status = str(status or "").strip()
    color = ROUTINE_STATUS_STAMP_COLORS.get(display_status, "")
    return (display_status, color) if color else ("", "")


def routine_instance_count_display(value: object) -> str:
    try:
        count = int(value)
    except (TypeError, ValueError):
        return "-"
    if count > 999:
        return "999"
    if count < 0:
        return "0"
    return str(count)


def routine_instance_name_display(display_name: object) -> str:
    return tree_title_text(display_name)


def _routine_aggregate_metric_widget(
    *,
    object_name: str,
    column_key: str,
    label_text: str,
    count: object,
    font: QFont,
    color_value: str,
) -> QWidget:
    widget = QWidget()
    widget.setObjectName(object_name)
    widget.setFont(font)
    widget.setFixedWidth(routine_aggregate_metric_width(column_key, font))
    _set_fixed_metric_widget_policy(widget)
    widget.setFocusPolicy(Qt.NoFocus)
    widget.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    parts = (
        ("Label", label_text, routine_aggregate_label_width(column_key, font), Qt.AlignLeft),
        ("OpenParen", "(", QFontMetrics(font).horizontalAdvance("("), Qt.AlignCenter),
        (
            "Number",
            routine_instance_count_display(count),
            routine_aggregate_number_slot_width(font),
            Qt.AlignCenter,
        ),
        ("CloseParen", ")", QFontMetrics(font).horizontalAdvance(")"), Qt.AlignCenter),
    )
    for suffix, text, width, alignment in parts:
        label = QLabel(text)
        label.setObjectName(f"{object_name}{suffix}")
        label.setFont(font)
        label.setFixedWidth(width)
        label.setAlignment(alignment | Qt.AlignVCenter)
        label.setFocusPolicy(Qt.NoFocus)
        label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        label.setStyleSheet(
            f"QLabel {{ color: {color_value}; }}"
            "QLabel:disabled { color: #9CA3AF; }"
        )
        layout.addWidget(label)
    return widget


def _split_wrapped_metric_text(text: object, label: str) -> str:
    return split_wrapped_metric_text(text, label)


def _split_ratio_metric_text(text: object, label: str) -> tuple[str, str]:
    return split_ratio_metric_text(text, label)


def _routine_metric_text_label(text: str, color_value: str) -> QLabel:
    label = QLabel(text)
    label.setFont(main_monitoring_cell_font())
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
    settings_label = _routine_metric_text_label("설정", "#2563EB")
    settings_label.setObjectName("routineInstanceBuyLimitSettings")
    settings_label.setFixedSize(
        ROUTINE_LIMIT_SETTINGS_BADGE_WIDTH,
        AUTO_TRADE_SETTING_BADGE_HEIGHT,
    )
    settings_label.setAlignment(Qt.AlignCenter)
    settings_label.setAttribute(Qt.WA_TransparentForMouseEvents, False)
    settings_label.setCursor(Qt.PointingHandCursor)
    settings_label.setStyleSheet(
        auto_trade_setting_badge_stylesheet(
            "QLabel#routineInstanceBuyLimitSettings",
            text_color="#2563EB",
            border_color="#2563EB",
        )
        + (
            "QLabel#routineInstanceBuyLimitSettings:disabled {"
            " color: #9CA3AF; border-color: #D1D5DB;"
            " background-color: transparent;"
            "}"
        )
    )
    layout.addWidget(settings_label, 0, Qt.AlignVCenter)
    layout.addSpacing(ROUTINE_LIMIT_SETTINGS_BADGE_GAP)
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
    if amount_label.text() in {"미설정", "대기"}:
        amount_label.setAlignment(Qt.AlignCenter | Qt.AlignVCenter)
    amount_label.setObjectName("routineInstanceBuyLimitAmount")
    amount_label.setAttribute(Qt.WA_TransparentForMouseEvents, False)

    amount_editor = QLineEdit()
    amount_editor.setObjectName("routineInstanceBuyLimitEditor")
    amount_editor.setFont(main_monitoring_cell_font())
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
        return "한도(미설정)"
    if amount is None:
        return "한도(미설정)"
    try:
        limit_value = int(float(str(amount).replace(",", "").strip()))
    except (TypeError, ValueError):
        return "한도(미설정)"
    if limit_value <= 0:
        return "한도(미설정)"
    return f"한도({_format_plain_amount(limit_value)})"


def routine_instance_buy_limit_configured(*, enabled: bool, amount: object = None) -> bool:
    if not enabled:
        return False
    try:
        return float(str(amount).replace(",", "").strip()) > 0
    except (TypeError, ValueError):
        return False


def stock_buy_limit_state(*, enabled: bool, amount: object = None) -> str:
    if not enabled:
        return "UNSET"
    if routine_instance_buy_limit_configured(enabled=True, amount=amount):
        return "CONFIGURED"
    return "WAITING"


def routine_instance_consumed_text(
    *,
    consumed_amount: object,
    buy_limit_enabled: bool,
    buy_limit_amount: object = None,
    total_budget: object = None,
    amount_unknown: bool = False,
) -> str:
    limit_state = stock_buy_limit_state(
        enabled=buy_limit_enabled,
        amount=buy_limit_amount,
    )
    if limit_state == "WAITING":
        return "소모(0 / 0.0%)"

    amount_text = "확인 필요" if amount_unknown else _format_plain_amount(consumed_amount)
    try:
        consumed_value = float(str(consumed_amount).replace(",", "").strip())
        denominator = float(
            str(
                total_budget if limit_state == "UNSET" else buy_limit_amount
            ).replace(",", "").strip()
        )
    except (TypeError, ValueError):
        return f"소모({amount_text} / 확인 필요)"
    if amount_unknown or denominator <= 0:
        return f"소모({amount_text} / 확인 필요)"
    return f"소모({amount_text} / {_format_percent((consumed_value / denominator) * 100.0, digits=1)})"


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
        rate_text = format_signed_percent(
            (profit_value / cost_value) * 100.0,
            digits=2,
        )
    else:
        rate_text = format_signed_percent(0.0, digits=2)
    amount_text = _format_plain_amount(profit_value, signed=True)
    color = profit_loss_value_color(profit_value)
    return f"수익({amount_text} / {rate_text})", color


def routine_group_profit_projection(
    instance_ids: Iterable[object],
    instance_counts: dict[str, dict[str, object]],
) -> tuple[str, str, float]:
    """Aggregate definition/group profit with the same contract as a routine row."""

    counts = [
        instance_counts.get(str(instance_id or "").strip(), {})
        for instance_id in instance_ids
    ]
    amount = sum(float(count.get("profit_amount") or 0) for count in counts)
    cost_basis = sum(float(count.get("profit_cost_basis") or 0) for count in counts)
    text, color = routine_instance_profit_text(
        profit_amount=amount,
        cost_basis=cost_basis,
        unknown=any(bool(count.get("profit_unknown")) for count in counts),
    )
    return text, color, amount


class RoutineInstanceStatusStamp(QWidget):
    def __init__(
        self,
        *,
        on_click: Callable[[], None] | None = None,
        on_double_click: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._on_click = on_click
        self._on_double_click = on_double_click

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._on_click is not None:
            self._on_click()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._on_double_click is not None:
            self._on_double_click()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


def create_routine_instance_status_widget(
    status: object,
    *,
    instance_id: str = "",
    registered: int,
    excluded: int,
    operation_or_stopped: int,
    review: int,
    buy_limit_text: str = "",
    consumed_text: str = "",
    profit_text: str = "",
    profit_color: str = "",
    buy_limit_configured: bool = False,
    enabled: bool = True,
    on_status_click: Callable[[], None] | None = None,
    on_status_double_click: Callable[[], None] | None = None,
) -> QWidget:
    display_status, color = routine_status_stamp_spec(status)
    operation_label = "운영" if display_status == ROUTINE_STATUS_RUNNING else "정지"
    container = QWidget()
    container.setObjectName("routineInstanceStatusContainer")
    container.setFont(main_monitoring_cell_font())
    container.setFocusPolicy(Qt.NoFocus)

    layout = QHBoxLayout(container)
    status_column_left = routine_instance_status_column_left()
    layout.setContentsMargins(0, 0, 4, 0)
    layout.setSpacing(0)

    stamp = RoutineInstanceStatusStamp(
        on_click=on_status_click,
        on_double_click=on_status_double_click,
    )
    stamp.setObjectName("routineInstanceStatusStamp")
    stamp.setFixedSize(ROUTINE_STATUS_STAMP_WIDTH, ROUTINE_STATUS_STAMP_HEIGHT)
    stamp.setFocusPolicy(Qt.NoFocus)
    stamp.setAttribute(Qt.WA_StyledBackground, True)
    stamp.setCursor(Qt.PointingHandCursor if enabled else Qt.ArrowCursor)
    if display_status == ROUTINE_STATUS_STOPPED:
        stamp.setToolTip("더블클릭 | 운영시작")
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
    stamp_layout.setContentsMargins(4, 2, 4, 2)
    stamp_layout.setSpacing(0)
    stamp_layout.setAlignment(Qt.AlignCenter)
    stamp_color = color or "#9CA3AF"
    status_text = QLabel(display_status or "-")
    status_text.setObjectName("routineInstanceStatusText")
    status_text.setFont(main_monitoring_cell_font())
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
    layout.addSpacing(
        ROUTINE_INSTANCE_NAME_WIDTH
        + ROUTINE_STATUS_STAMP_GRID_INSET
        - status_column_left
    )
    column_widths = routine_instance_grid_columns(container.font())
    number_widths = routine_instance_number_widths(container.font())
    separator_width = routine_aggregate_separator_width(container.font())
    metric_specs = [
        (
            "routineInstanceRegistered",
            "registered",
            f"등록({routine_instance_count_display(registered)})",
            "#374151",
        ),
        (
            "routineInstanceExcluded",
            "excluded",
            f"제외({routine_instance_count_display(excluded)})",
            "#374151",
        ),
        (
            "routineInstanceOperationOrStopped",
            "operation_or_stopped",
            f"{operation_label}({routine_instance_count_display(operation_or_stopped)})",
            "#374151",
        ),
        (
            "routineInstanceReview",
            "review",
            f"검토({routine_instance_count_display(review)})",
            "#374151",
        ),
        (
            "routineInstanceProfit",
            "profit",
            f"{profit_text}",
            profit_color if profit_color else "#374151",
        ),
        ("routineInstanceBuyLimit", "limit", f"{buy_limit_text}", "#374151"),
    ]
    metric_specs.append(
        ("routineInstanceConsumed", "consumed", f"{consumed_text}", "#374151")
    )
    aggregate_labels = {
        "registered": "등록",
        "excluded": "제외",
        "operation_or_stopped": operation_label,
        "review": "검토",
    }
    aggregate_counts = {
        "registered": registered,
        "excluded": excluded,
        "operation_or_stopped": operation_or_stopped,
        "review": review,
    }
    for metric_index, (object_name, column_key, text, color_value) in enumerate(
        metric_specs
    ):
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
        if column_key in ROUTINE_AGGREGATE_COLUMN_KEYS:
            metric_widget = _routine_aggregate_metric_widget(
                object_name=object_name,
                column_key=column_key,
                label_text=aggregate_labels[column_key],
                count=aggregate_counts[column_key],
                font=container.font(),
                color_value=color_value,
            )
        elif column_key == "limit":
            metric_widget = _routine_limit_metric_widget(
                str(text or ""),
                width=column_widths[column_key],
                number_widths=number_widths,
                color_value=color_value,
            )
            metric_widget.setProperty("routine_instance_id", str(instance_id or ""))
            amount_label = metric_widget.findChild(QLabel, "routineInstanceBuyLimitAmount")
            amount_editor = metric_widget.findChild(QLineEdit, "routineInstanceBuyLimitEditor")
            settings_label = metric_widget.findChild(
                QLabel,
                "routineInstanceBuyLimitSettings",
            )
            if amount_label is not None:
                amount_label.setProperty("routine_instance_id", str(instance_id or ""))
            if amount_editor is not None:
                amount_editor.setProperty("routine_instance_id", str(instance_id or ""))
            if settings_label is not None:
                settings_label.setProperty("routine_instance_id", str(instance_id or ""))
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
            metric_widget.setFont(main_monitoring_cell_font())
            metric_widget.setAlignment(Qt.AlignCenter)
            metric_widget.setFixedWidth(column_widths[column_key])
            _set_fixed_metric_widget_policy(metric_widget)
            metric_widget.setFocusPolicy(Qt.NoFocus)
            metric_widget.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            metric_widget.setStyleSheet(
                f"QLabel#{object_name} {{ color: {color_value}; }}"
                f"QLabel#{object_name}:disabled {{ color: #9CA3AF; }}"
            )
        if metric_index == 0:
            layout.addSpacing(ROUTINE_AGGREGATE_LEADING_GAP)
        else:
            layout.addSpacing(ROUTINE_INSTANCE_GRID_SPACING)
            layout.addWidget(separator, 0, Qt.AlignVCenter)
            layout.addSpacing(ROUTINE_INSTANCE_GRID_SPACING)
        layout.addWidget(metric_widget, 0, Qt.AlignVCenter)
    layout.addStretch(1)
    container.setEnabled(bool(enabled))
    return container
ROUTINE_CHECKBOX_HIT_PADDING = 4
ROUTINE_PARENT_EXPAND_OFFSET = ROUTINE_PARENT_CHECKBOX_OFFSET
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
            hide_widget = getattr(widget, "hide", None)
            if callable(hide_widget):
                hide_widget()
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


def _instance_stock_counts(
    operation_excluded_only: bool | None = None,
    *,
    window=None,
    stock_scope: str | None = None,
    stock_paths: set[str] | None = None,
    static_data: dict[str, object] | None = None,
    state_by_stock_dir: dict[str, dict[str, object]] | None = None,
    state_issue_by_stock_dir: dict[str, str] | None = None,
) -> dict[str, dict[str, object]]:
    counts: dict[str, dict[str, object]] = {}
    clean_scope = str(stock_scope or "").strip().lower()
    if clean_scope not in {
        "normal",
        "all",
        "operation",
        "waiting",
        "excluded",
        "all_non_review",
    }:
        if operation_excluded_only is True:
            clean_scope = "excluded"
        elif operation_excluded_only is False:
            clean_scope = "normal"
        else:
            clean_scope = "all_non_review"
    static_cache = (
        static_data
        if isinstance(static_data, dict)
        else _main_pnl_refresh_static_cache(window)
    )
    dynamic_stocks: list[tuple[dict[str, object], dict[str, object], bool]] = []
    seen_stock_identities: set[str] = set()
    for stock in static_cache["stocks"]:
        stock_dir = Path(stock["stock_dir"])
        if state_by_stock_dir is not None:
            inspection = inspect_review_state_data(
                state_by_stock_dir.get(str(stock_dir), {}),
                state_issue_reason=(state_issue_by_stock_dir or {}).get(
                    str(stock_dir),
                    "",
                ),
                source="MAIN_REFRESH_SNAPSHOT",
            )
        else:
            refresh_context = _main_refresh_read_context(window)
            if refresh_context is not None:
                snapshot_data = _main_refresh_stock_data(window, stock_dir)
                inspection = snapshot_data.get("review_inspection")
                if inspection is None:
                    inspection = inspect_review_state_data(
                        snapshot_data.get("state", {}),
                        source="MAIN_REFRESH_SNAPSHOT",
                    )
            else:
                loaded_state = read_json_dict(stock_dir / "state.json")
                inspection = inspect_stock_review_state(
                    stock_dir,
                    loaded_state=loaded_state,
                )
        dynamic_stocks.append(
            (stock, inspection.state, inspection.review_required)
        )
    for stock, state, review_required in dynamic_stocks:
        stock_path = str(stock.get("stock_path", "") or "").strip()
        if stock_paths is not None and stock_path not in stock_paths:
            continue
        instance_id = str(stock.get("instance_id", "") or "").strip()
        operation_excluded = bool(stock.get("operation_excluded", False))
        code = str(stock.get("code", "") or "").strip()
        name = str(stock.get("name", "") or "").strip()
        stock_identity = normalize_stock_code(code) or stock_path
        if stock_identity in seen_stock_identities:
            continue
        seen_stock_identities.add(stock_identity)
        item = counts.setdefault(
            instance_id,
            {
                "registered": 0,
                "operation_or_stopped": 0,
                "operation_running": 0,
                "waiting": 0,
                "normal": 0,
                "excluded": 0,
                "review": 0,
                "consumed_amount": 0,
                "consumed_unknown": False,
                "profit_amount": 0,
                "profit_cost_basis": 0,
                "profit_unknown": False,
                "pnl_stock_codes": [],
                "pnl_flat_stock_codes": [],
                "stocks": [],
            },
        )
        item["registered"] += 1
        category = auto_trade_stock_operation_category(
            window,
            stock_code=code,
            persisted_trade_started=auto_trade_setting_trade_started(state),
            operation_excluded=operation_excluded,
            review_required=review_required,
        )
        if category == "review":
            item["review"] += 1
        elif category == "excluded":
            item["excluded"] += 1
        else:
            item["normal"] += 1
            item["operation_or_stopped"] += 1
            if category == "operation":
                item["operation_running"] += 1
            else:
                item["waiting"] += 1
        if clean_scope == "all":
            include_stock_row = not review_required
        elif clean_scope == "excluded":
            include_stock_row = operation_excluded and not review_required
        elif clean_scope == "operation":
            include_stock_row = category == "operation"
        elif clean_scope == "waiting":
            include_stock_row = category == "waiting"
        elif clean_scope == "normal":
            include_stock_row = not review_required and not operation_excluded
        else:
            include_stock_row = not review_required
        if include_stock_row and (code or name):
            item["stocks"].append(
                {
                    "code": code,
                    "name": name,
                    "stock_path": stock_path,
                    "instance_id": instance_id,
                    "enabled": bool(stock.get("enabled", True)),
                }
            )
        if review_required:
            continue
        holding_qty = safe_int_value(state.get("holding_qty"), 0)
        avg_price = average_price_from_state(state)
        if code:
            item["pnl_stock_codes"].append(code)
            if holding_qty == 0:
                item["pnl_flat_stock_codes"].append(code)
        if holding_qty > 0 and avg_price > 0:
            cost_basis = holding_qty * avg_price
            item["consumed_amount"] = float(item["consumed_amount"]) + cost_basis
        elif holding_qty > 0:
            item["consumed_unknown"] = True
            item["profit_unknown"] = True
    for item in counts.values():
        item["running"] = int(item.get("operation_running", 0) or 0)
        item["stopped"] = max(
            0,
            int(item.get("normal", 0) or 0) - int(item["running"]),
        )
        stocks = item.get("stocks")
        if isinstance(stocks, list):
            stocks.sort(
                key=lambda stock: (
                    str(stock.get("code", "") or ""),
                    str(stock.get("name", "") or "").casefold(),
                )
            )
    return counts


def _invalidate_main_pnl_refresh_cache(window) -> None:
    """Invalidate process-local metadata before the canonical table reload."""
    if window is not None:
        setattr(window, _MAIN_PNL_STATIC_CACHE_ATTR, None)


def _main_pnl_refresh_static_cache(window) -> dict[str, object]:
    """Return slow-changing routine/assignment metadata for the 1-second tick."""
    cached = (
        getattr(window, _MAIN_PNL_STATIC_CACHE_ATTR, None)
        if window is not None
        else None
    )
    if isinstance(cached, dict):
        return cached

    refresh_context = _main_refresh_read_context(window)
    definitions = (
        tuple(refresh_context.get("definitions", ()))
        if refresh_context is not None
        else tuple(load_routine_definitions())
    )
    instances = (
        tuple(refresh_context.get("instances", ()))
        if refresh_context is not None
        else tuple(load_persisted_routine_instances())
    )
    groups = (
        tuple(refresh_context.get("groups", ()))
        if refresh_context is not None
        else tuple(get_group_records())
    )
    valid_instance_ids = {
        str(instance_id)
        for instance in instances
        if (instance_id := getattr(instance, "instance_id", ""))
    }
    stocks: list[dict[str, object]] = []
    base_stocks = (
        tuple(refresh_context.get("stocks", ()))
        if refresh_context is not None
        else tuple(read_base_stocks())
    )
    for stock in base_stocks:
        stock_path = str(stock.get("stock_path", "") or "").strip()
        if not stock_path:
            continue
        stock_dir = Path(__file__).resolve().parent / stock_path
        config = (
            _main_refresh_stock_data(window, stock_dir).get("config", {})
            if refresh_context is not None
            else read_json_dict(stock_dir / "config.json")
        )
        instance_id = str(
            stock.get("assigned_routine_instance_id", "")
            or config.get("assigned_routine_instance_id", "")
            or ""
        ).strip()
        if not instance_id or instance_id not in valid_instance_ids:
            continue
        stocks.append(
            {
                "stock_path": stock_path,
                "stock_dir": stock_dir,
                "stock_dir_key": str(stock_dir),
                "instance_id": instance_id,
                "operation_excluded": is_operation_excluded(config),
                "code": str(stock.get("code", "") or "").strip(),
                "name": str(stock.get("name", "") or "").strip(),
                "enabled": bool(stock.get("enabled", True)),
                "routines": tuple(_routine_names_for_stock_record(stock)),
                "assigned_routine_instance_id": instance_id,
            }
        )
    result: dict[str, object] = {
        "definitions": definitions,
        "instances": instances,
        "groups": groups,
        "stocks": tuple(stocks),
    }
    if window is not None:
        setattr(window, _MAIN_PNL_STATIC_CACHE_ATTR, result)
    return result


def _main_pnl_tick_state_snapshot(
    static_data: dict[str, object],
) -> tuple[
    dict[str, dict[str, object]],
    dict[str, str],
    dict[str, dict[str, object]],
]:
    """Read each stock state once for all projections in one PnL tick."""
    state_by_stock_dir: dict[str, dict[str, object]] = {}
    state_issue_by_stock_dir: dict[str, str] = {}
    state_by_code: dict[str, dict[str, object]] = {}
    for stock in static_data.get("stocks", ()):
        if not isinstance(stock, dict):
            continue
        stock_dir = Path(str(stock.get("stock_dir", "") or ""))
        if not stock_dir.name:
            continue
        inspection = inspect_stock_review_state(stock_dir)
        stock_dir_key = str(stock_dir)
        state = dict(inspection.state)
        state_by_stock_dir[stock_dir_key] = state
        state_issue_by_stock_dir[stock_dir_key] = str(
            inspection.issue_reason or ""
        )
        code = normalize_stock_code(stock.get("code"))
        if code:
            state_by_code.setdefault(code, state)
    return state_by_stock_dir, state_issue_by_stock_dir, state_by_code


def routine_instance_suggested_buy_limits(
    window,
    instance_id: str,
    *,
    policy: dict[str, object] | None = None,
) -> tuple[int | None, int | None]:
    """Return complete registered-stock recommendation/minimum totals."""
    target_id = str(instance_id or "").strip()
    if not target_id:
        return None, None
    stocks = [
        stock
        for stock in _main_pnl_refresh_static_cache(window).get("stocks", ())
        if isinstance(stock, dict)
        and str(stock.get("instance_id", "") or "").strip() == target_id
    ]
    if not stocks:
        return None, None

    defaults = starting_budget_defaults(policy)
    recommended_total = 0
    minimum_total = 0
    for stock in stocks:
        stock_path = str(stock.get("stock_path", "") or "").strip()
        code = str(stock.get("code", "") or "").strip()
        name = str(stock.get("name", "") or "").strip()
        stock_dir = Path(str(stock.get("stock_dir", "") or ""))
        if not stock_path or not code or not name or not stock_dir.name:
            return None, None
        state = read_json_dict(stock_dir / "state.json")
        if not isinstance(state, dict):
            return None, None
        config = read_json_dict(stock_dir / "config.json")
        if not isinstance(config, dict):
            return None, None
        starting_budget = main_stock_resolved_starting_budget(
            window,
            stock,
            config,
            policy=policy,
        )
        recommended = suggested_buy_limit(
            starting_budget,
            defaults["limit_recommended_multiplier"],
            align_digits=stock_limit_digit_alignment_enabled(),
        )
        minimum = suggested_buy_limit(
            starting_budget,
            defaults["limit_minimum_multiplier"],
            align_digits=stock_limit_digit_alignment_enabled(),
        )
        if recommended is None or minimum is None:
            return None, None
        recommended_total += recommended
        minimum_total += minimum
    return recommended_total, minimum_total


def _main_pnl_refresh_routine_metadata(window) -> tuple[list[object], list[object]]:
    cache = _main_pnl_refresh_static_cache(window)
    return list(cache["definitions"]), list(cache["instances"])


def _refresh_instance_pnl_from_batch(
    instance_counts: dict[str, dict[str, object]],
    *,
    state_by_code: dict[str, dict[str, object]] | None = None,
    pnl_by_code: dict[str, dict[str, object]] | None = None,
) -> dict[str, dict[str, object]]:
    """Apply one confirmable-PnL Runtime snapshot to all registered stocks."""
    stock_codes = [
        str(code or "").strip().lstrip("A")
        for count in instance_counts.values()
        for code in count.get("pnl_stock_codes", [])
        if str(code or "").strip().lstrip("A")
    ]
    if pnl_by_code is None:
        projection_kwargs: dict[str, object] = {
            "project_root": Path(__file__).resolve().parent,
        }
        if state_by_code is not None:
            projection_kwargs["state_by_code"] = state_by_code
        pnl_by_code = project_current_stock_pnl_snapshot(
            stock_codes,
            **projection_kwargs,
        )
    for count in instance_counts.values():
        if "pnl_stock_codes" not in count:
            continue
        count["profit_amount"] = 0
        count["profit_cost_basis"] = 0
        count["profit_unknown"] = False
        flat_codes = {
            str(code or "").strip().lstrip("A")
            for code in count.get("pnl_flat_stock_codes", [])
        }
        for raw_code in count.get("pnl_stock_codes", []):
            code = str(raw_code or "").strip().lstrip("A")
            result = pnl_by_code.get(code, {})
            if result.get("available") is not True:
                if (
                    code in flat_codes
                    and str(result.get("reason") or "").strip()
                    == "PNL_CYCLE_BOOTSTRAP_REQUIRED"
                ):
                    continue
                count["profit_unknown"] = True
                continue
            count["profit_amount"] = float(count["profit_amount"]) + float(
                result.get("cumulative_profit") or 0
            )
            count["profit_cost_basis"] = float(
                count["profit_cost_basis"]
            ) + float(result.get("completed_buy_cost") or 0) + float(
                result.get("open_cost") or 0
            )
    return pnl_by_code


def _main_routine_summary_projection(
    definitions: list[object],
    instances: list[object],
    instance_counts: dict[str, dict[str, object]],
    *,
    group_badge_count: int | None = None,
    routine_badge_count: int | None = None,
    stock_badge_count: int | None = None,
) -> dict[str, object]:
    """Project the unfiltered registered-routine summary from existing counts."""
    registered = sum(
        int(count.get("registered", 0) or 0)
        for count in instance_counts.values()
    )
    excluded = sum(
        int(count.get("excluded", 0) or 0)
        for count in instance_counts.values()
    )
    review = sum(
        int(count.get("review", 0) or 0)
        for count in instance_counts.values()
    )
    running = sum(
        int(count.get("operation_running", count.get("running", 0)) or 0)
        for count in instance_counts.values()
    )
    waiting = sum(
        int(count.get("waiting", 0) or 0)
        for count in instance_counts.values()
    )
    displayed_stock_count = (
        max(0, registered - review)
        if stock_badge_count is None
        else max(0, int(stock_badge_count))
    )
    displayed_group_count = (
        len(definitions)
        if group_badge_count is None
        else max(0, int(group_badge_count))
    )
    displayed_routine_count = (
        len(instances)
        if routine_badge_count is None
        else max(0, int(routine_badge_count))
    )
    profit_amount = sum(
        float(count.get("profit_amount", 0) or 0)
        for count in instance_counts.values()
    )
    profit_cost_basis = sum(
        float(count.get("profit_cost_basis", 0) or 0)
        for count in instance_counts.values()
    )
    profit_rate = (
        profit_amount / profit_cost_basis * 100
        if profit_cost_basis > 0
        else 0
    )
    return {
        "count_badges": (
            ("group", "그룹", displayed_group_count),
            ("routine", "루틴", displayed_routine_count),
            ("stock", "종목", displayed_stock_count),
            ("operation", MAIN_STOCK_OPERATION_CATEGORY_LABELS["operation"], running),
            ("waiting", MAIN_STOCK_OPERATION_CATEGORY_LABELS["waiting"], waiting),
            ("excluded", MAIN_STOCK_OPERATION_CATEGORY_LABELS["excluded"], excluded),
            ("review", MAIN_STOCK_OPERATION_CATEGORY_LABELS["review"], review),
        ),
        "counts_text": (
            f"그룹({displayed_group_count})  루틴({displayed_routine_count})  종목({displayed_stock_count})  "
            f"운영({running})  대기({waiting})  제외({excluded})  검토({review})"
        ),
        "profit_value_text": (
            f"{format_signed_money(profit_amount)} / "
            f"{format_signed_percent(profit_rate, digits=2)}"
        ),
        "profit_text": (
            f"수익({format_signed_money(profit_amount)} / "
            f"{format_signed_percent(profit_rate, digits=2)})"
        ),
        "profit_amount": profit_amount,
        "profit_rate": profit_rate,
        "profit_color": profit_loss_value_color(profit_amount),
    }


def _update_main_routine_summary(
    window,
    definitions: list[object],
    instances: list[object],
    instance_counts: dict[str, dict[str, object]],
    *,
    group_projection=None,
    relation_counts: dict[str, dict[str, object]] | None = None,
) -> None:
    update = getattr(window, "_update_main_routine_summary", None)
    if callable(update):
        valid_projection = bool(getattr(window, "_main_routine_valid_only", False))
        valid_instance_ids = {
            str(getattr(instance, "instance_id", "") or "").strip()
            for instance in instances
            if (
                int(
                    instance_counts.get(
                        str(getattr(instance, "instance_id", "") or "").strip(),
                        {},
                    ).get("operation_running", 0)
                    or 0
                )
                + int(
                    instance_counts.get(
                        str(getattr(instance, "instance_id", "") or "").strip(),
                        {},
                    ).get("waiting", 0)
                    or 0
                )
                > 0
            )
        }
        valid_definition_ids = {
            str(getattr(instance, "definition_id", "") or "").strip()
            for instance in instances
            if str(getattr(instance, "instance_id", "") or "").strip()
            in valid_instance_ids
            and str(getattr(instance, "definition_id", "") or "").strip()
        }
        registered_definition_ids = {
            str(getattr(definition, "definition_id", "") or "").strip()
            for definition in definitions
            if str(getattr(definition, "definition_id", "") or "").strip()
        }
        projection_supplied = group_projection is not None
        projected_groups = tuple(group_projection or ())
        projected_relation_count = sum(
            len(projected_group.instances) for projected_group in projected_groups
        )
        valid_relation_ids = {
            main_group_instance_relation_id(
                projected_group.group_id,
                projected_instance.instance_id,
            )
            for projected_group in projected_groups
            for projected_instance in projected_group.instances
            if bool(
                (relation_counts or {}).get(
                    main_group_instance_relation_id(
                        projected_group.group_id,
                        projected_instance.instance_id,
                    ),
                    {},
                ).get("stocks", ())
            )
        }
        valid_projected_groups = sum(
            1
            for projected_group in projected_groups
            if any(
                main_group_instance_relation_id(
                    projected_group.group_id,
                    projected_instance.instance_id,
                )
                in valid_relation_ids
                for projected_instance in projected_group.instances
            )
        )
        valid_projected_relations = len(valid_relation_ids)
        valid_stock_count = (
            sum(
                len((relation_counts or {}).get(relation_id, {}).get("stocks", ()))
                for relation_id in valid_relation_ids
            )
            if valid_projection and projection_supplied
            else sum(
                int(count.get("operation_running", 0) or 0)
                + int(count.get("waiting", 0) or 0)
                for count in instance_counts.values()
            )
            if valid_projection
            else None
        )
        update(
            _main_routine_summary_projection(
                definitions,
                instances,
                instance_counts,
                group_badge_count=(
                    valid_projected_groups
                    if valid_projection and projection_supplied
                    else len(projected_groups)
                    if projection_supplied
                    else len(valid_definition_ids & registered_definition_ids)
                    if valid_projection
                    else None
                ),
                routine_badge_count=(
                    valid_projected_relations
                    if valid_projection and projection_supplied
                    else projected_relation_count
                    if projection_supplied
                    else len(valid_instance_ids)
                    if valid_projection
                    else None
                ),
                stock_badge_count=valid_stock_count,
            )
        )


def _main_routine_effective_stock_scope(window) -> str:
    stock_scope = str(
        getattr(window, "_main_routine_stock_scope", "") or ""
    ).strip().lower()
    if bool(getattr(window, "_main_routine_excluded_only", False)):
        stock_scope = "excluded"
    if stock_scope == "review":
        stock_scope = "all"
    if stock_scope not in {
        "normal", "all", "operation", "waiting", "excluded"
    }:
        stock_scope = "normal"
    if bool(getattr(window, "_main_routine_valid_only", False)) and stock_scope == "all":
        stock_scope = "normal"
    return stock_scope


def main_refresh_pnl_only(window) -> None:
    """Refresh monitoring stock/instance PnL without rebuilding either table."""
    static_cache = _main_pnl_refresh_static_cache(window)
    (
        state_by_stock_dir,
        state_issue_by_stock_dir,
        state_by_code,
    ) = _main_pnl_tick_state_snapshot(static_cache)
    instance_counts = _instance_stock_counts(
        window=window,
        static_data=static_cache,
        state_by_stock_dir=state_by_stock_dir,
        state_issue_by_stock_dir=state_issue_by_stock_dir,
    )
    pnl_by_code = _refresh_instance_pnl_from_batch(
        instance_counts,
        state_by_code=state_by_code,
    )
    definitions, instances = _main_pnl_refresh_routine_metadata(window)
    groups = static_cache.get("groups")
    if not isinstance(groups, (list, tuple)):
        groups = get_group_records()
    group_projection = build_main_group_projection(
        groups,
        instances,
        tuple(static_cache.get("stocks", ())),
    )
    relation_counts = (
        _projected_group_relation_counts(
            window,
            group_projection,
            _main_routine_effective_stock_scope(window),
            static_data=static_cache,
            state_by_stock_dir=state_by_stock_dir,
            state_issue_by_stock_dir=state_issue_by_stock_dir,
            pnl_by_code=pnl_by_code,
        )
        if any(projected_group.instances for projected_group in group_projection)
        else {}
    )
    _update_main_routine_summary(
        window,
        definitions,
        instances,
        instance_counts,
        group_projection=group_projection,
        relation_counts=relation_counts,
    )
    changed = False
    for row in range(window.routine_table.rowCount()):
        item = window.routine_table.item(row, 0)
        if item is None:
            continue
        kind = item.data(ROUTINE_ROW_KIND_ROLE)
        if kind == ROUTINE_ROW_STOCK:
            code = str(item.data(ROUTINE_STOCK_CODE_ROLE) or "").strip()
            metrics = item.data(ROUTINE_STOCK_METRICS_ROLE)
            if not code or not isinstance(metrics, tuple) or len(metrics) < 3:
                continue
            result = pnl_by_code.get(code.lstrip("A"), {})
            if result.get("available") is not True:
                continue
            updated = list(metrics)
            updated[2], _amount, _rate = confirmable_stock_profit_metric(result)
            if tuple(updated) != metrics:
                item.setData(ROUTINE_STOCK_METRICS_ROLE, tuple(updated))
                changed = True
        elif kind == ROUTINE_ROW_CHILD:
            instance_id = str(item.data(ROUTINE_INSTANCE_ID_ROLE) or "").strip()
            group_id = str(item.data(ROUTINE_GROUP_ID_ROLE) or "").strip()
            count = relation_counts.get(
                main_group_instance_relation_id(group_id, instance_id)
            )
            widget = window.routine_table.cellWidget(row, 1)
            label = widget.findChild(QLabel, "routineInstanceProfit") if widget is not None else None
            if not count or label is None:
                continue
            text, color = routine_instance_profit_text(profit_amount=count.get("profit_amount", 0), cost_basis=count.get("profit_cost_basis", 0), unknown=bool(count.get("profit_unknown")))
            if label.text() != text:
                label.setText(text)
                label.setStyleSheet(f"color: {color}; border: none; background: transparent;")
                changed = True
        elif kind == ROUTINE_ROW_PARENT:
            group_id = str(item.data(ROUTINE_GROUP_ID_ROLE) or "").strip()
            group_instance_ids = getattr(
                window,
                "_routine_instance_ids_by_group",
                {},
            ).get(group_id, ())
            text, color, _amount = routine_group_profit_projection(
                (
                    main_group_instance_relation_id(group_id, instance_id)
                    for instance_id in group_instance_ids
                ),
                relation_counts,
            )
            profit_data = (text, color)
            if item.data(ROUTINE_PARENT_PROFIT_ROLE) != profit_data:
                item.setData(ROUTINE_PARENT_PROFIT_ROLE, profit_data)
                changed = True
    if changed:
        window.routine_table.viewport().update()


def _replace_stock_market_projection(
    current: object,
    refreshed: object,
    indexes: tuple[int, ...],
) -> object:
    if not isinstance(current, (list, tuple)) or not isinstance(refreshed, (list, tuple)):
        return current
    result = list(current)
    for index in indexes:
        if index >= len(refreshed):
            continue
        while len(result) <= index:
            result.append(None)
        result[index] = refreshed[index]
    return tuple(result) if isinstance(current, tuple) else result


def main_refresh_market_information_only(
    window,
    stock_codes: Iterable[str] | None = None,
) -> int:
    """Refresh live-price-dependent Stock row roles without rebuilding the Tree."""

    table = getattr(window, "routine_table", None)
    if table is None:
        return 0
    targets = {
        str(code or "").strip().upper().lstrip("A")
        for code in (stock_codes or ())
        if str(code or "").strip()
    }
    total_budget = read_system_total_budget_for_recalculation()
    changed_rows = 0
    for row in range(table.rowCount()):
        anchor = table.item(row, 0)
        if anchor is None or anchor.data(ROUTINE_ROW_KIND_ROLE) != ROUTINE_ROW_STOCK:
            continue
        code = str(anchor.data(ROUTINE_STOCK_CODE_ROLE) or "").strip().lstrip("A")
        if not code or (targets and code.upper() not in targets):
            continue
        tooltip_projection = anchor.data(ROUTINE_STOCK_TOOLTIP_DATA_ROLE)
        tooltip_projection = (
            dict(tooltip_projection) if isinstance(tooltip_projection, dict) else {}
        )
        stock = {
            "code": code,
            "name": str(tooltip_projection.get("stock_name", "") or "").strip(),
            "stock_path": str(anchor.data(ROUTINE_STOCK_PATH_ROLE) or "").strip(),
            "enabled": True,
        }
        refreshed = _routine_tree_stock_row(
            window,
            definition_id=str(anchor.data(ROUTINE_DEFINITION_ID_ROLE) or ""),
            instance_id=str(anchor.data(ROUTINE_INSTANCE_ID_ROLE) or ""),
            stock=stock,
            total_budget=total_budget,
        )
        current_values = anchor.data(ROUTINE_STOCK_VALUES_ROLE)
        current_metrics = anchor.data(ROUTINE_STOCK_METRICS_ROLE)
        current_display = anchor.data(ROUTINE_STOCK_DISPLAY_ROLE)
        values = _replace_stock_market_projection(
            current_values,
            refreshed.get("stock_values"),
            (1, 8, 11, 12),
        )
        metrics = _replace_stock_market_projection(
            current_metrics,
            refreshed.get("stock_metrics"),
            (1, 5),
        )
        display = _replace_stock_market_projection(
            current_display,
            refreshed.get("stock_display_tokens"),
            (1, 8, 11, 12),
        )
        tooltip_projection["current_price"] = refreshed.get("current_price")
        tooltip_projection["stock_state"] = dict(refreshed.get("stock_state") or {})
        for column in range(table.columnCount()):
            item = table.item(row, column)
            if item is None:
                continue
            item.setData(ROUTINE_STOCK_VALUES_ROLE, values)
            item.setData(ROUTINE_STOCK_METRICS_ROLE, metrics)
            item.setData(ROUTINE_STOCK_DISPLAY_ROLE, display)
            item.setData(
                ROUTINE_STOCK_INITIAL_BUY_ROLE,
                refreshed.get("initial_buy", {}),
            )
            item.setData(ROUTINE_STOCK_TOOLTIP_DATA_ROLE, tooltip_projection)
            item.setToolTip(main_stock_row_tooltip_from_projection(tooltip_projection))
        changed_rows += 1
    if changed_rows:
        table.viewport().update()
    return changed_rows


def _routine_tree_stock_display_values(
    window,
    stock: dict[str, object],
    *,
    trade_counts: tuple[int, int] = (0, 0),
) -> list[str]:
    code = str(stock.get("code", "") or "").strip()
    name = str(stock.get("name", "") or "").strip()
    stock_path = str(stock.get("stock_path", "") or "").strip()
    stock_dir = Path(__file__).resolve().parent / stock_path if stock_path else None
    snapshot_data = _main_refresh_stock_data(window, stock_dir)
    review_inspection = snapshot_data.get("review_inspection")
    if stock_dir is None and isinstance(stock.get("state"), dict):
        review_inspection = inspect_review_state_data(
            stock.get("state"),
            source="MAIN_STOCK_RECORD",
        )
    state = review_inspection.state
    config = (
        snapshot_data.get("config", {})
        if stock_dir is not None
        else stock.get("config")
        if isinstance(stock.get("config"), dict)
        else {}
    )
    if not isinstance(state, dict):
        state = {}
    if not isinstance(config, dict) or not config:
        config = default_config()

    buy_pending_qty, sell_pending_qty = (
        pending_order_side_quantities(stock_dir, state)
        if stock_dir is not None
        else (0, 0)
    )
    holding_qty = safe_int_value(state.get("holding_qty"), 0)
    avg_price = average_price_from_state(state)
    trade_started = auto_trade_setting_trade_started(state)
    current_session_trade_started = auto_trade_setting_current_session_trade_started(
        window,
        trade_started,
        code,
    )
    operation_category = auto_trade_stock_operation_category(
        window,
        stock_code=code,
        persisted_trade_started=trade_started,
        operation_excluded=is_operation_excluded(config),
        review_required=review_inspection.review_required,
    )
    row_projection = auto_trade_setting_row_projection(
        state,
        config,
        operation_category=operation_category,
        holding_qty=holding_qty,
        buy_pending_qty=buy_pending_qty,
        sell_pending_qty=sell_pending_qty,
        current_session_trade_started=current_session_trade_started,
        persisted_trade_started=trade_started,
    )
    display_status = str(row_projection["display_status"])
    operation_display = auto_trade_operation_display(config, state)
    (
        operation_display_text,
        _operation_color,
        _operation_tooltip,
        _ats_labels,
    ) = operation_display
    method_text = str(row_projection["method_text"])
    liquidation_text = str(row_projection["liquidation_text"])
    current_price = main_stock_current_price(window, stock, state)
    holding_text, price_text, profit_text, _pending_text, _profit_amount, _profit_rate = (
        stock_position_display_values(
            holding_qty=holding_qty,
            avg_price=avg_price,
            current_price=current_price,
            buy_pending_qty=buy_pending_qty,
            sell_pending_qty=sell_pending_qty,
        )
    )
    buy_trade_count, sell_trade_count = trade_counts
    trade_text = f"매매({buy_trade_count:,} / {sell_trade_count:,})"
    initial_buy = main_stock_resolved_initial_buy_display(
        window,
        stock,
        config,
    )
    values = [
        f"{code} {name}".strip(),
        f"{initial_buy['badge']} {initial_buy['value_text']}",
        operation_display_text,
        "●",
        display_status,
        method_text,
        liquidation_text,
        holding_text,
        price_text,
        profit_text,
        trade_text,
    ]
    return [str(value or "-") for value in values]


def _item_style_snapshot(item, *, enabled: bool = True) -> dict[str, object]:
    font = item.font()
    foreground = item.foreground()
    background = item.background()
    foreground_color = (
        foreground.color().name()
        if foreground.style() != Qt.NoBrush
        else ""
    )
    background_color = (
        background.color().name()
        if background.style() != Qt.NoBrush
        else ""
    )
    return {
        "text": str(item.text()),
        "foreground": str(foreground_color).lower(),
        "background": str(background_color).lower(),
        "bold": bool(font.bold()),
        "italic": bool(font.italic()),
        "point_size": int(font.pointSize()) if font.pointSize() > 0 else None,
        "alignment": int(item.textAlignment()),
        "tooltip": str(item.toolTip()),
        "enabled": bool(enabled),
        "sort_value": item.data(SORT_ROLE),
    }


def _plain_stock_display_snapshot(
    text: object,
    *,
    alignment: int = int(Qt.AlignCenter),
    enabled: bool = True,
) -> dict[str, object]:
    item = SortableTableWidgetItem(str(text or "-"))
    item.setTextAlignment(alignment)
    return _item_style_snapshot(item, enabled=enabled)


def _auto_trade_profit_display_snapshot(
    text: object,
    profit_amount: object,
) -> dict[str, object]:
    value = str(text or "-")
    item = SortableTableWidgetItem(value)
    item.setToolTip(value)
    item.setForeground(QColor(profit_loss_value_color(profit_amount)))
    item.setTextAlignment(Qt.AlignCenter)
    return _item_style_snapshot(item)


def _routine_tree_stock_display_snapshots(
    window,
    stock: dict[str, object],
    values: list[str],
    *,
    trade_counts: tuple[int, int] = (0, 0),
) -> list[dict[str, object]]:
    code = str(stock.get("code", "") or "").strip()
    name = str(stock.get("name", "") or "").strip()
    stock_path = str(stock.get("stock_path", "") or "").strip()
    stock_dir = Path(__file__).resolve().parent / stock_path if stock_path else None
    snapshot_data = _main_refresh_stock_data(window, stock_dir)
    review_inspection = snapshot_data.get("review_inspection")
    if stock_dir is None and isinstance(stock.get("state"), dict):
        review_inspection = inspect_review_state_data(
            stock.get("state"),
            source="MAIN_STOCK_RECORD",
        )
    state = review_inspection.state
    config = (
        snapshot_data.get("config", {})
        if stock_dir is not None
        else stock.get("config")
        if isinstance(stock.get("config"), dict)
        else {}
    )
    if not isinstance(state, dict):
        state = {}
    if not isinstance(config, dict) or not config:
        config = default_config()

    buy_pending_qty, sell_pending_qty = (
        pending_order_side_quantities(stock_dir, state)
        if stock_dir is not None
        else (0, 0)
    )
    holding_qty = safe_int_value(state.get("holding_qty"), 0)
    avg_price = average_price_from_state(state)
    current_price = main_stock_current_price(window, stock, state)
    _holding_text, _price_text, _profit_text, _pending_text, profit_amount, _profit_rate = (
        stock_position_display_values(
            holding_qty=holding_qty,
            avg_price=avg_price,
            current_price=current_price,
            buy_pending_qty=buy_pending_qty,
            sell_pending_qty=sell_pending_qty,
        )
    )
    trade_started = auto_trade_setting_trade_started(state)
    current_session_trade_started = auto_trade_setting_current_session_trade_started(
        window,
        trade_started,
        code,
    )
    operation_category = auto_trade_stock_operation_category(
        window,
        stock_code=code,
        persisted_trade_started=trade_started,
        operation_excluded=is_operation_excluded(config),
        review_required=review_inspection.review_required,
    )
    row_projection = auto_trade_setting_row_projection(
        state,
        config,
        operation_category=operation_category,
        holding_qty=holding_qty,
        buy_pending_qty=buy_pending_qty,
        sell_pending_qty=sell_pending_qty,
        current_session_trade_started=current_session_trade_started,
        persisted_trade_started=trade_started,
    )
    display_status = str(row_projection["display_status"])
    operation_display = auto_trade_operation_display(config, state)
    method_text = str(row_projection["method_text"])
    liquidation_text = str(row_projection["liquidation_text"])
    status_cell_active = bool(row_projection["status_cell_active"])
    method_cell_active = bool(row_projection["method_cell_active"])
    liquidation_has_policy = bool(row_projection["liquidation_has_policy"])
    liquidation_is_individual = bool(row_projection["liquidation_is_individual"])
    liquidation_cell_active = bool(row_projection["liquidation_cell_active"])

    tokens: list[dict[str, object]] = []
    name_item = create_auto_trade_stock_name_item(
        f"{code} {name}".strip(),
        review_required=review_inspection.review_required,
        review_status=display_status in {"긴급정지", "검토종목"},
        trade_started=trade_started,
    )
    tokens.append(_item_style_snapshot(name_item))
    tokens.append(
        _plain_stock_display_snapshot(
            values[1] if len(values) > 1 else "-",
            alignment=int(Qt.AlignCenter),
        )
    )
    tokens.append(_item_style_snapshot(create_auto_trade_operation_item(operation_display)))
    tokens.append(
        _item_style_snapshot(
            create_auto_trade_situation_item(
                state,
                bool(row_projection["situation_active"]),
                display_status,
            )
        )
    )
    tokens.append(
        _item_style_snapshot(
            create_auto_trade_setting_activity_status_item(
                display_status,
                status_cell_active,
            )
        )
    )

    method_item = SortableTableWidgetItem(method_text)
    method_item.setToolTip(f"현재 상태 적용 방식: {method_text}")
    apply_auto_trade_setting_activity_style(method_item, method_cell_active)
    method_item.setTextAlignment(Qt.AlignCenter)
    tokens.append(_item_style_snapshot(method_item))

    liquidation_item = SortableTableWidgetItem(liquidation_text)
    tooltip_prefix = "개별 청산" if liquidation_is_individual else "청산정책"
    liquidation_item.setToolTip(f"{tooltip_prefix}: {liquidation_text}")
    apply_auto_trade_setting_liquidation_style(
        liquidation_item,
        liquidation_cell_active,
        liquidation_has_policy,
        liquidation_is_individual,
    )
    liquidation_item.setTextAlignment(Qt.AlignCenter)
    tokens.append(_item_style_snapshot(liquidation_item))

    for value_index, value in enumerate(values[len(tokens):], start=len(tokens)):
        if value_index == 9:
            tokens.append(_auto_trade_profit_display_snapshot(value, profit_amount))
        else:
            tokens.append(
                _plain_stock_display_snapshot(
                    value,
                    alignment=int(Qt.AlignCenter),
                )
            )
    review_required = review_inspection.review_required
    operation_excluded = is_operation_excluded(config)
    if review_required or operation_excluded:
        for token_index, token in enumerate(tokens):
            if not isinstance(token, dict):
                continue
            style_item = SortableTableWidgetItem(str(token.get("text", "") or ""))
            style_item.setToolTip(str(token.get("tooltip", "") or ""))
            apply_auto_trade_setting_protection_row_style(
                style_item,
                review_required=review_required,
                operation_excluded=operation_excluded,
            )
            if review_required and token_index in {4, 5}:
                apply_auto_trade_setting_activity_style(style_item, False)
            elif review_required and token_index == 6:
                apply_auto_trade_setting_liquidation_style(
                    style_item,
                    False,
                    liquidation_has_policy,
                    liquidation_is_individual,
                )
            protected = _item_style_snapshot(style_item)
            token["foreground"] = protected.get("foreground", token.get("foreground"))
            token["background"] = protected.get("background", token.get("background"))
            token["tooltip"] = protected.get("tooltip", token.get("tooltip", ""))
    return tokens


def _routine_tree_stock_metric_values(
    window,
    stock: dict[str, object],
    *,
    trade_counts: tuple[int, int] = (0, 0),
    total_budget: object = None,
    config_override: dict[str, object] | None = None,
) -> tuple[tuple[object, ...], str, str, str | None, dict[str, object]]:
    stock_path = str(stock.get("stock_path", "") or "").strip()
    stock_dir = Path(__file__).resolve().parent / stock_path if stock_path else None
    snapshot_data = _main_refresh_stock_data(window, stock_dir)
    state = (
        snapshot_data.get("state", {})
        if stock_dir is not None
        else stock.get("state")
        if isinstance(stock.get("state"), dict)
        else {}
    )
    if not isinstance(state, dict):
        state = {}

    holding_qty = safe_int_value(state.get("holding_qty", 0))
    avg_price = average_price_from_state(state)
    buy_pending_qty, sell_pending_qty = (
        pending_order_side_quantities(stock_dir, state)
        if stock_dir is not None
        else (
            safe_int_value(state.get("pending_buy_qty", 0)),
            safe_int_value(state.get("pending_sell_qty", 0)),
        )
    )
    current_price = main_stock_current_price(window, stock, state)
    holding_metric, price_metric, _base_profit_metric, _pending_metric, _profit_amount, _profit_rate = (
        stock_position_metric_values(
            holding_qty=holding_qty,
            avg_price=avg_price,
            current_price=current_price,
            buy_pending_qty=buy_pending_qty,
            sell_pending_qty=sell_pending_qty,
        )
    )
    cycle_pnl = project_confirmable_cumulative_pnl(
        str(stock.get("code") or ""),
        current_price,
        project_root=Path(__file__).resolve().parent,
    )
    profit_metric, profit_amount, profit_rate = confirmable_stock_profit_metric(
        cycle_pnl
    )
    buy_trade_count, sell_trade_count = trade_counts
    trade_metric = RatioMetricDisplay(
        label="매매",
        value1=f"{buy_trade_count:,}",
        value2=f"{sell_trade_count:,}",
        value1_sample="99",
        value2_sample="99",
    )
    config: dict[str, object] = (
        dict(config_override) if isinstance(config_override, dict) else {}
    )
    if not config and stock_dir is not None:
        loaded_config = snapshot_data.get("config", {})
        if isinstance(loaded_config, dict):
            config = loaded_config
    elif not config and isinstance(stock.get("config"), dict):
        config = stock["config"]
    buy_limit_enabled = bool(config.get("buy_limit_enabled", False))
    buy_limit_amount = config.get("buy_limit_amount")
    limit_state = stock_buy_limit_state(
        enabled=buy_limit_enabled,
        amount=buy_limit_amount,
    )
    explicit_limit = limit_state == "CONFIGURED"
    suggested_limit = None
    defaults = starting_budget_defaults()
    starting_budget = main_stock_resolved_starting_budget(
        window,
        stock,
        config,
        policy={"starting_budget_defaults": defaults},
    )
    if starting_budget is not None:
        suggested_limit = suggested_buy_limit(
            starting_budget,
            defaults["limit_recommended_multiplier"],
            align_digits=stock_limit_digit_alignment_enabled(),
        )
    effective_limit_amount = (
        buy_limit_amount
        if explicit_limit
        else suggested_limit
        if buy_limit_enabled
        else None
    )
    auth_state = main_budget_display_auth_state(window)
    if auth_state == SERVER_AUTH_PENDING or (
        auth_state == SERVER_AUTH_COMPLETE and starting_budget is None
    ):
        limit_text = "한도(대기)"
    elif limit_state == "UNSET":
        limit_text = "한도(미설정)"
    elif limit_state == "CONFIGURED":
        limit_text = routine_instance_buy_limit_text(
            enabled=True,
            amount=buy_limit_amount,
        )
    elif suggested_limit is not None:
        limit_text = f"권장({_format_plain_amount(suggested_limit)})"
    else:
        limit_text = "한도(미설정)"
    consumed_text = routine_instance_consumed_text(
        consumed_amount=holding_metric.value2,
        buy_limit_enabled=buy_limit_enabled,
        buy_limit_amount=buy_limit_amount,
        total_budget=total_budget,
    )
    consumed_amount, consumed_rate = split_ratio_metric_text(consumed_text, "소모")
    consumed_metric = RatioMetricDisplay(
        label="소모",
        value1=consumed_amount,
        value2=consumed_rate,
        value1_sample=ROUTINE_INSTANCE_AMOUNT_SAMPLES["consumed_amount"][0],
        value2_sample=ROUTINE_INSTANCE_AMOUNT_SAMPLES["consumed_rate"][0],
    )
    signal, _display_text, _color = routine_profit_signal(profit_rate, None)
    metrics: list[object] = [
        holding_metric,
        price_metric,
        profit_metric,
        trade_metric,
        None,
    ]
    metrics.append(consumed_metric)
    sort_values = {
        "holding": holding_qty,
        "price": safe_float_value(current_price, 0.0),
        "profit": int(round(profit_amount)),
        "trade": buy_trade_count + sell_trade_count,
        "limit": (
            safe_int_value(effective_limit_amount, 0)
        ),
        "configured_limit": safe_int_value(buy_limit_amount, 0) if explicit_limit else 0,
        "recommended_limit": safe_int_value(suggested_limit, 0),
        "limit_enabled": bool(buy_limit_enabled),
        "limit_source": limit_state,
    }
    return (
        tuple(metrics),
        routine_profit_led_state_from_signal(signal),
        limit_text,
        consumed_text,
        sort_values,
    )


def _routine_tree_stock_row(
    window,
    *,
    definition_id: str,
    instance_id: str,
    stock: dict[str, object],
    trade_counts: tuple[int, int] = (0, 0),
    total_budget: object = None,
) -> dict[str, object]:
    stock_values = _routine_tree_stock_display_values(
        window,
        stock,
        trade_counts=trade_counts,
    )
    stock_path = str(stock.get("stock_path", "") or "").strip()
    stock_dir = Path(__file__).resolve().parent / stock_path if stock_path else None
    snapshot_data = _main_refresh_stock_data(window, stock_dir)
    stock_config = (
        snapshot_data.get("config", {})
        if stock_dir is not None
        else dict(stock.get("config"))
        if isinstance(stock.get("config"), dict)
        else {}
    )
    if not isinstance(stock_config, dict) or not stock_config:
        stock_config = default_config()
    review_inspection = snapshot_data.get("review_inspection")
    if stock_dir is None and isinstance(stock.get("state"), dict):
        review_inspection = inspect_review_state_data(
            stock.get("state"),
            source="MAIN_STOCK_RECORD",
        )
    stock_state = review_inspection.state
    if auto_trade_start_budget_current_running(
        window,
        stock.get("code"),
        stock_config,
        stock_state,
    ):
        display_config, current_adjustment = project_running_budget_adjustment_config(
            stock_config,
            stock_state,
        )
        _pending_config, pending_adjustment = (
            project_running_budget_adjustment_display_config(stock_config, stock_state)
        )
        pending_visible = bool(
            str(current_adjustment.get("state") or "").strip().upper()
            == STATE_WAIT_SELL
            and pending_adjustment.get("hydrated", False)
        )
        adjustment_display = {
            **current_adjustment,
            "current": current_adjustment,
            "pending": pending_visible,
            "pending_request": pending_adjustment if pending_visible else None,
        }
    else:
        display_config = dict(stock_config)
        adjustment_display = {
            "active": False,
            "hydrated": False,
            "reason": "NOT_CURRENT_RUNNING",
        }
    (
        stock_metrics,
        stock_profit_led,
        limit_text,
        consumed_text,
        sort_values,
    ) = _routine_tree_stock_metric_values(
        window,
        stock,
        trade_counts=trade_counts,
        total_budget=total_budget,
        config_override=display_config,
    )
    profit_metric = stock_metrics[2] if len(stock_metrics) > 2 else None
    if isinstance(profit_metric, RatioMetricDisplay) and len(stock_values) > 9:
        stock_values[9] = ratio_metric_text(profit_metric)
    stock_values = [
        *stock_values,
        limit_text,
        *([consumed_text] if consumed_text is not None else []),
    ]
    stock_display_tokens = _routine_tree_stock_display_snapshots(
        window,
        stock,
        stock_values,
        trade_counts=trade_counts,
    )
    if len(stock_display_tokens) > 9:
        profit_token = dict(stock_display_tokens[9])
        profit_token["text"] = stock_values[9]
        profit_token["tooltip"] = stock_values[9]
        profit_token["foreground"] = profit_loss_value_color(
            sort_values.get("profit", 0)
        ).lower()
        stock_display_tokens[9] = profit_token
    situation_sort_value = (
        stock_display_tokens[3].get("sort_value", 0)
        if len(stock_display_tokens) > 3
        and isinstance(stock_display_tokens[3], dict)
        else 0
    )
    column_sort_values = {
        "operation": stock_values[2] if len(stock_values) > 2 else "-",
        "situation": safe_int_value(situation_sort_value, 0),
        "status": auto_trade_setting_status_sort_rank(
            stock_values[4] if len(stock_values) > 4 else "-"
        ),
        "method": stock_values[5] if len(stock_values) > 5 else "-",
        "liquidation": stock_values[6] if len(stock_values) > 6 else "-",
    }
    operator_status = main_stock_operator_status(
        window,
        stock_code=stock.get("code"),
        stock_state=stock_state,
        operation_excluded=is_operation_excluded(stock_config),
        review_required=review_inspection.review_required,
    )
    current_price = main_stock_current_price(window, stock, stock_state)
    return {
        "kind": ROUTINE_ROW_STOCK,
        "definition_id": definition_id,
        "instance_id": instance_id,
        "code": str(stock.get("code", "") or ""),
        "stock_display_name": str(stock.get("name", "") or ""),
        "name": " | ".join(stock_values),
        "stock_values": stock_values,
        "stock_display_tokens": stock_display_tokens,
        "stock_metrics": stock_metrics,
        "initial_buy": main_stock_resolved_initial_buy_display(
            window,
            stock,
            display_config,
        ),
        "running_budget_adjustment_display": adjustment_display,
        "stock_profit_led": stock_profit_led,
        "sort_metrics": sort_values,
        "column_sort_values": column_sort_values,
        "stock_path": str(stock.get("stock_path", "") or ""),
        "current_price": current_price,
        "stock_state": stock_state,
        "operator_status": operator_status,
        "buy_limit_configured": bool(sort_values.get("configured_limit")),
        "buy_limit_enabled": bool(sort_values.get("limit_enabled")),
        "buy_limit_suggested": sort_values.get("recommended_limit", 0),
        "buy_limit_source": sort_values.get("limit_source", "UNSET"),
        "enabled": bool(stock.get("enabled", True)),
        "description": "",
        "operation_status": "",
        "registered": 0,
        "excluded": 0,
        "operation_or_stopped": 0,
        "review": 0,
        "buy_limit_display": "",
        "consumed_display": "",
        "profit_display": "",
        "profit_color": "",
    }


def main_stock_limit_expanded(groups: Iterable[dict[str, object]]) -> bool:
    """Return whether the visible Stock scope needs the always-on consumed slot."""

    return any(
        True
        for group in groups
        for child in group.get("children", [])
        if isinstance(child, dict)
        for stock in child.get("stocks", [])
        if isinstance(stock, dict)
    )


def routine_stock_column_sort_value(
    stock_row: dict[str, object],
    sort_key: str,
) -> tuple[object, str, str]:
    values = stock_row.get("column_sort_values")
    values = values if isinstance(values, dict) else {}
    value = values.get(sort_key, "")
    normalized: object = (
        safe_int_value(value, 0)
        if sort_key in {"situation", "status"}
        else str(value or "-").strip().casefold()
    )
    return (
        normalized,
        str(stock_row.get("code", "") or ""),
        str(stock_row.get("name", "") or "").casefold(),
    )


def sort_routine_stock_rows_by_column(
    stock_rows: list[dict[str, object]],
    sort_key: str,
) -> None:
    stock_rows.sort(key=lambda stock: routine_stock_column_sort_value(stock, sort_key))


def routine_instance_operation_status(running_count: object) -> str:
    """Project Runtime-backed instance operation state."""
    return (
        ROUTINE_STATUS_RUNNING
        if safe_int_value(running_count, 0) > 0
        else ROUTINE_STATUS_STOPPED
    )


def routine_instance_operation_badge_enabled(
    *,
    definition_enabled: object,
    registered_count: object,
) -> bool:
    """Keep operation control independent from the removed checkbox selection."""
    return bool(definition_enabled) and safe_int_value(registered_count, 0) > 0


def _routine_monitor_sort_value(row: dict[str, object], column: int):
    if column == 0:
        return str(row.get("name", "")).casefold()
    if column == 1:
        return str(row.get("operation_status", "")).casefold()
    if column in {2, 3, 4, 5}:
        return int(
            row.get(
                ("registered", "excluded", "operation_or_stopped", "review")[column - 2],
                0,
            )
            or 0
        )
    if column == 7:
        return int(row.get("buy_limit_amount", 0) or 0)
    return str(row.get("values", [""] * len(ROUTINE_MONITORING_HEADERS))[column]).casefold()


def main_group_instance_relation_id(group_id: object, instance_id: object) -> str:
    """Return a window-local identity for one projected Group/Instance relation."""
    return f"{str(group_id or '').strip()}\x1f{str(instance_id or '').strip()}"


def _empty_instance_count() -> dict[str, object]:
    return {
        "registered": 0,
        "running": 0,
        "stopped": 0,
        "operation_running": 0,
        "waiting": 0,
        "normal": 0,
        "excluded": 0,
        "review": 0,
        "consumed_amount": 0,
        "consumed_unknown": False,
        "profit_amount": 0,
        "profit_cost_basis": 0,
        "profit_unknown": False,
        "pnl_stock_codes": [],
        "pnl_flat_stock_codes": [],
        "stocks": [],
    }


def _projected_group_relation_counts(
    window,
    projection,
    stock_scope: str,
    *,
    static_data: dict[str, object] | None = None,
    state_by_stock_dir: dict[str, dict[str, object]] | None = None,
    state_issue_by_stock_dir: dict[str, str] | None = None,
    pnl_by_code: dict[str, dict[str, object]] | None = None,
):
    relation_counts: dict[str, dict[str, object]] = {}
    for projected_group in projection:
        if not projected_group.instances:
            continue
        group_stock_paths = {
            str(stock.get("stock_path", "") or "").strip()
            for projected_instance in projected_group.instances
            for stock in projected_instance.stocks
            if str(stock.get("stock_path", "") or "").strip()
        }
        group_counts = _instance_stock_counts(
            window=window,
            stock_scope=stock_scope,
            stock_paths=group_stock_paths,
            static_data=static_data,
            state_by_stock_dir=state_by_stock_dir,
            state_issue_by_stock_dir=state_issue_by_stock_dir,
        )
        for projected_instance in projected_group.instances:
            relation_id = main_group_instance_relation_id(
                projected_group.group_id,
                projected_instance.instance_id,
            )
            relation_counts[relation_id] = group_counts.get(
                projected_instance.instance_id,
                _empty_instance_count(),
            )
    if pnl_by_code is None:
        _refresh_instance_pnl_from_batch(relation_counts)
    else:
        _refresh_instance_pnl_from_batch(
            relation_counts,
            pnl_by_code=pnl_by_code,
        )
    return relation_counts


def main_load_routine_table(window) -> None:
    """등록 루틴의 운영 수와 1차 관제 상태를 메인 좌측 표에 표시한다.

    종목수는 더 이상 루틴폴더 안의 물리 종목폴더 개수로 계산하지 않는다.
    중앙 종목관리(read_base_stocks -> stocks/config.json) 기준으로 계산한다.

    인스턴스 한도는 routine_instances 메타데이터, 소모/손익은 배정 종목
    state.json의 보유수량/평단/현재가 후보 필드만 사용한다.
    """
    _invalidate_main_pnl_refresh_cache(window)
    stock_scope = _main_routine_effective_stock_scope(window)
    instance_counts = _instance_stock_counts(
        window=window,
        stock_scope=stock_scope,
    )
    _refresh_instance_pnl_from_batch(instance_counts)
    definitions, instances = _main_pnl_refresh_routine_metadata(window)
    static_stocks = tuple(_main_pnl_refresh_static_cache(window).get("stocks", ()))
    refresh_context = _main_refresh_read_context(window)
    group_records = (
        tuple(refresh_context.get("groups", ()))
        if refresh_context is not None
        else get_group_records()
    )
    group_projection = build_main_group_projection(
        group_records,
        instances,
        static_stocks,
    )
    relation_counts = _projected_group_relation_counts(
        window,
        group_projection,
        stock_scope,
    )
    trade_counts_by_code = current_stock_trade_counts_by_code()
    stock_tooltip_metadata_by_code = _stock_library_tooltip_metadata_by_code()
    total_budget = read_system_total_budget_for_recalculation()
    window._routine_assigned_stock_count_by_instance = {
        instance.instance_id: int(
            instance_counts.get(instance.instance_id, {}).get("registered", 0) or 0
        )
        for instance in instances
    }
    window._routine_instance_ids_by_group = {
        projected_group.group_id: tuple(
            projected_instance.instance_id
            for projected_instance in projected_group.instances
        )
        for projected_group in group_projection
    }
    window._routine_group_records_by_id = {
        projected_group.group_id: projected_group.group
        for projected_group in group_projection
    }
    window._routine_stock_paths_by_group_instance = {
        main_group_instance_relation_id(
            projected_group.group_id,
            projected_instance.instance_id,
        ): tuple(
            str(stock.get("stock_path", "") or "").strip()
            for stock in projected_instance.stocks
            if str(stock.get("stock_path", "") or "").strip()
        )
        for projected_group in group_projection
        for projected_instance in projected_group.instances
    }
    window._routine_stock_paths_by_group = {
        projected_group.group_id: tuple(
            dict.fromkeys(
                str(stock.get("stock_path", "") or "").strip()
                for projected_instance in projected_group.instances
                for stock in projected_instance.stocks
                if str(stock.get("stock_path", "") or "").strip()
            )
        )
        for projected_group in group_projection
    }
    total_groups = len(group_projection)
    total_instances = sum(
        len(projected_group.instances) for projected_group in group_projection
    )
    total_stocks = sum(
        len(projected_instance.stocks)
        for projected_group in group_projection
        for projected_instance in projected_group.instances
    )
    apply_routine_height = getattr(window, "_apply_main_routine_table_height", None)
    if callable(apply_routine_height):
        apply_routine_height(total_groups, total_instances, total_stocks)
    sync_routine_selection_state(window, definitions, instances)
    if not isinstance(getattr(window, "_collapsed_main_group_ids", None), set):
        window._collapsed_main_group_ids = set()
    if not isinstance(
        getattr(window, "_collapsed_main_group_instance_ids", None),
        set,
    ):
        window._collapsed_main_group_instance_ids = set()
    display_level = str(
        getattr(window, "_main_routine_display_level", "") or ""
    ).strip()
    if (
        display_level in {"group", "routine", "stock"}
        and not bool(
            getattr(window, "_main_routine_display_level_applied", False)
        )
    ):
        group_ids = {projected_group.group_id for projected_group in group_projection}
        relation_ids = {
            main_group_instance_relation_id(
                projected_group.group_id,
                projected_instance.instance_id,
            )
            for projected_group in group_projection
            for projected_instance in projected_group.instances
        }
        if display_level == "group":
            window._collapsed_main_group_ids.update(group_ids)
        elif display_level == "routine":
            window._collapsed_main_group_ids.difference_update(group_ids)
            window._collapsed_main_group_instance_ids.update(relation_ids)
        else:
            window._collapsed_main_group_ids.difference_update(group_ids)
            window._collapsed_main_group_instance_ids.difference_update(relation_ids)
        window._main_routine_display_level_applied = True

    groups: list[dict[str, object]] = []
    collapsed_groups = getattr(window, "_collapsed_main_group_ids", set())
    collapsed_relations = getattr(
        window,
        "_collapsed_main_group_instance_ids",
        set(),
    )
    for projected_group in group_projection:
        children: list[dict[str, object]] = []
        child_relation_ids: list[str] = []
        for projected_instance in projected_group.instances:
            instance = projected_instance.instance
            relation_id = main_group_instance_relation_id(
                projected_group.group_id,
                projected_instance.instance_id,
            )
            child_relation_ids.append(relation_id)
            count = relation_counts.get(relation_id, _empty_instance_count())
            if main_budget_display_auth_state(window) == SERVER_AUTH_PENDING:
                buy_limit_text = "한도(대기)"
            else:
                buy_limit_text = routine_instance_buy_limit_text(
                    enabled=instance.buy_limit_enabled,
                    amount=instance.buy_limit_amount,
                )
            buy_limit_configured = routine_instance_buy_limit_configured(
                enabled=instance.buy_limit_enabled,
                amount=instance.buy_limit_amount,
            )
            consumed_text = routine_instance_consumed_text(
                consumed_amount=count.get("consumed_amount", 0),
                buy_limit_enabled=instance.buy_limit_enabled,
                buy_limit_amount=instance.buy_limit_amount,
                total_budget=total_budget,
                amount_unknown=bool(count.get("consumed_unknown")),
            )
            profit_text, profit_color = routine_instance_profit_text(
                profit_amount=count.get("profit_amount", 0),
                cost_basis=count.get("profit_cost_basis", 0),
                unknown=bool(count.get("profit_unknown")),
            )
            stock_rows = [
                _routine_tree_stock_row(
                    window,
                    definition_id=instance.definition_id,
                    instance_id=instance.instance_id,
                    stock=stock,
                    trade_counts=trade_counts_by_code.get(
                        str(stock.get("code", "") or "").strip().lstrip("A"),
                        (0, 0),
                    ),
                    total_budget=total_budget,
                )
                for stock in count.get("stocks", [])
                if isinstance(stock, dict)
            ]
            for stock_row in stock_rows:
                stock_row["group_id"] = projected_group.group_id
                stock_row["group_path"] = str(projected_group.group_path)
                stock_row["relation_id"] = relation_id
                stock_metadata = stock_tooltip_metadata_by_code.get(
                    str(stock_row.get("code", "") or "").strip().upper().lstrip("A"),
                    {},
                )
                stock_row["market"] = stock_metadata.get("market", "")
                stock_row["nxt_available"] = bool(
                    stock_metadata.get("nxt_available") is True
                )
                stock_row["stock_status"] = stock_metadata.get("status", "")
            operation_running_count = int(
                count.get("operation_running", count.get("running", 0)) or 0
            )
            operation_or_stopped_count = (
                operation_running_count
                if operation_running_count > 0
                else int(
                    count.get(
                        "operation_or_stopped",
                        max(
                            0,
                            int(count.get("registered", 0) or 0)
                            - int(count.get("excluded", 0) or 0)
                            - int(
                                count.get(
                                    "review",
                                    count.get("error", 0),
                                )
                                or 0
                            ),
                        ),
                    )
                    or 0
                )
            )
            children.append(
                {
                    "kind": ROUTINE_ROW_CHILD,
                    "group_id": projected_group.group_id,
                    "group_path": str(projected_group.group_path),
                    "relation_id": relation_id,
                    "definition_id": instance.definition_id,
                    "instance_id": instance.instance_id,
                    "explicit_group_assignment": (
                        str(getattr(instance, "group_id", "") or "").strip()
                        == projected_group.group_id
                    ),
                    "name": routine_instance_name_display(instance.display_name),
                    "full_name": instance.display_name,
                    "description": instance.description,
                    "operation_status": routine_instance_operation_status(
                        operation_running_count,
                    ),
                    "registered": int(count["registered"]),
                    "operation_running": operation_running_count,
                    "waiting": int(count.get("waiting", 0) or 0),
                    "review": int(count.get("review", count.get("error", 0)) or 0),
                    "operation_or_stopped": operation_or_stopped_count,
                    "normal": int(count.get("normal", 0) or 0),
                    "excluded": int(count.get("excluded", 0) or 0),
                    "buy_limit_enabled": instance.buy_limit_enabled,
                    "buy_limit_amount": instance.buy_limit_amount,
                    "buy_limit_configured": buy_limit_configured,
                    "buy_limit_display": buy_limit_text,
                    "consumed_display": consumed_text,
                    "profit_display": profit_text,
                    "profit_amount": safe_int_value(
                        count.get("profit_amount"),
                        0,
                    ),
                    "profit_color": profit_color,
                    "rules_path": instance.rules_path,
                    "collapsed": relation_id in collapsed_relations,
                    "stocks": stock_rows,
                }
            )

        all_children = tuple(children)
        if bool(getattr(window, "_main_routine_valid_only", False)):
            children = [child for child in children if child.get("stocks")]
            if not children:
                continue

        parent_registered = sum(int(item["registered"]) for item in all_children)
        parent_operation_running = sum(
            int(item["operation_running"]) for item in all_children
        )
        parent_waiting = sum(int(item.get("waiting", 0) or 0) for item in all_children)
        parent_operation_or_stopped = (
            parent_operation_running
            if parent_operation_running > 0
            else sum(int(item["operation_or_stopped"]) for item in all_children)
        )
        parent_normal = sum(
            int(item.get("normal", 0) or 0) for item in all_children
        )
        parent_excluded = sum(
            int(item.get("excluded", 0) or 0) for item in all_children
        )
        parent_review = sum(int(item["review"]) for item in all_children)
        parent_profit_text, parent_profit_color, parent_profit_amount = (
            routine_group_profit_projection(
                child_relation_ids,
                relation_counts,
            )
        )
        groups.append(
            {
                "kind": ROUTINE_ROW_PARENT,
                "group_id": projected_group.group_id,
                "group_path": str(projected_group.group_path),
                "definition_id": "",
                "name": projected_group.display_name,
                "operation_status": routine_instance_operation_status(
                    parent_operation_running
                ),
                "registered": parent_registered,
                "operation_running": parent_operation_running,
                "waiting": parent_waiting,
                "operation_or_stopped": parent_operation_or_stopped,
                "normal": parent_normal,
                "excluded": parent_excluded,
                "review": parent_review,
                "buy_limit_enabled": False,
                "buy_limit_amount": None,
                "buy_limit_configured": False,
                "buy_limit_display": "",
                "consumed_display": "",
                "profit_display": parent_profit_text,
                "profit_amount": parent_profit_amount,
                "profit_color": parent_profit_color,
                "collapsed": projected_group.group_id in collapsed_groups,
                "children": children,
            }
        )

    window.routine_table._main_stock_limit_expanded = main_stock_limit_expanded(groups)

    _update_main_routine_summary(
        window,
        definitions,
        instances,
        instance_counts,
        group_projection=group_projection,
        relation_counts=relation_counts,
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

    metric_sort_key = str(
        getattr(window, "_main_routine_metric_sort_key", "") or ""
    ).strip()
    initial_buy_sort_mode = str(
        getattr(window, "_main_routine_initial_buy_sort_mode", "") or ""
    ).strip().upper()
    column_sort_key = str(
        getattr(window, "_main_routine_column_sort_key", "") or ""
    ).strip()
    flat_valid_stock_list = (
        display_level == "stock"
        and bool(getattr(window, "_main_routine_valid_only", False))
    )
    if (
        display_level == "stock"
        and column_sort_key
        in {"operation", "situation", "status", "method", "liquidation"}
        and not flat_valid_stock_list
    ):
        for group in groups:
            for child in group["children"]:
                sort_routine_stock_rows_by_column(child["stocks"], column_sort_key)
    elif (
        display_level == "stock"
        and initial_buy_sort_mode in {"AMOUNT", "QUANTITY"}
        and not flat_valid_stock_list
    ):
        for group in groups:
            for child in group["children"]:
                sort_routine_stock_rows_by_initial_buy_mode(
                    child["stocks"],
                    initial_buy_sort_mode,
                )
    elif (
        display_level == "stock"
        and bool(getattr(window, "_main_routine_metric_sort_active", False))
        and metric_sort_key
        in {"holding", "price", "profit", "trade", "limit"}
    ):
        for group in groups:
            for child in group["children"]:
                child["stocks"].sort(
                    key=lambda stock: int(
                        stock.get("sort_metrics", {}).get(
                            metric_sort_key,
                            0,
                        )
                        or 0
                    ),
                    reverse=True,
                )
    elif (
        display_level == "routine"
        and bool(getattr(window, "_main_routine_metric_sort_active", False))
        and metric_sort_key in {"profit", "limit"}
    ):
        routine_sort_field = (
            "profit_amount"
            if metric_sort_key == "profit"
            else "buy_limit_amount"
        )
        for group in groups:
            group["children"].sort(
                key=lambda child: safe_int_value(
                    child.get(routine_sort_field),
                    0,
                ),
                reverse=True,
            )

    rows: list[dict[str, object]] = []
    for group in groups:
        if flat_valid_stock_list:
            for child in group["children"]:
                rows.extend(child.get("stocks", []))
            continue
        rows.append(group)
        if not group["collapsed"]:
            for child in group["children"]:
                rows.append(child)
                if not child.get("collapsed"):
                    rows.extend(child.get("stocks", []))

    if (
        flat_valid_stock_list
        and column_sort_key
        in {"operation", "situation", "status", "method", "liquidation"}
    ):
        sort_routine_stock_rows_by_column(rows, column_sort_key)
    elif (
        flat_valid_stock_list
        and initial_buy_sort_mode in {"AMOUNT", "QUANTITY"}
    ):
        sort_routine_stock_rows_by_initial_buy_mode(
            rows,
            initial_buy_sort_mode,
        )

    _clear_routine_table_cell_widgets(window.routine_table)
    clear_spans = getattr(window.routine_table, "clearSpans", None)
    if callable(clear_spans):
        clear_spans()
    window.routine_table.setRowCount(0)
    window.routine_table.setRowCount(len(rows))

    for row, row_data in enumerate(rows):
        is_parent = row_data["kind"] == ROUTINE_ROW_PARENT
        is_child = row_data["kind"] == ROUTINE_ROW_CHILD
        is_stock = row_data["kind"] == ROUTINE_ROW_STOCK
        set_row_height = getattr(window.routine_table, "setRowHeight", None)
        if callable(set_row_height):
            set_row_height(
                row,
                ROUTINE_STOCK_ROW_HEIGHT if is_stock else ROUTINE_INSTANCE_ROW_HEIGHT,
            )
        group_enabled = (
            True
            if is_parent
            else routine_definition_enabled(
                window,
                str(row_data["definition_id"]),
            )
        )
        if is_parent:
            checked = group_enabled
        elif is_child:
            checked = routine_instance_checked(
                window,
                str(row_data.get("instance_id", "")),
            )
        else:
            stock_path = str(row_data.get("stock_path", "") or "").strip()
            stock_selection = getattr(window, "_routine_stock_selection", {})
            if isinstance(stock_selection, dict) and stock_path in stock_selection:
                checked = bool(stock_selection.get(stock_path))
            else:
                checked = bool(row_data.get("enabled", True))
        instance_enabled = routine_instance_checked(
            window,
            str(row_data.get("instance_id", "")),
        )
        row_visually_enabled = group_enabled and (is_parent or checked or (is_stock and instance_enabled))
        prefix = ("▶ " if row_data.get("collapsed") else "▼ ") if is_parent else ""
        used_amount_text = str(row_data.get("consumed_display", ""))
        buy_limit_text = str(row_data.get("buy_limit_display", ""))
        usage_rate_text = ""
        profit_signal, profit_text, _profit_color = routine_profit_signal()

        operation_label = (
            "운영"
            if row_data.get("operation_status") == ROUTINE_STATUS_RUNNING
            else "정지"
        )
        aggregate_values = (
            ("등록", routine_instance_count_display(row_data.get("registered", 0))),
            ("제외", routine_instance_count_display(row_data.get("excluded", 0))),
            (
                operation_label,
                routine_instance_count_display(row_data.get("operation_or_stopped", 0)),
            ),
            ("검토", routine_instance_count_display(row_data.get("review", 0))),
        )
        aggregate_slots = tuple(
            f"{label}({number})" for label, number in aggregate_values
        )
        aggregate_text = " | ".join(aggregate_slots)
        parent_aggregate = aggregate_text
        child_aggregate = aggregate_text
        raw_group_name = str(row_data.get("name", "") or "")
        values = (
            [f"{prefix}{tree_title_text(raw_group_name)}"] + ([""] * 9)
            if is_parent
            else [
                str(row_data["name"]),
                str(row_data.get("operation_status", "")),
                str(row_data["registered"]),
                str(row_data["excluded"]),
                str(row_data["operation_or_stopped"]),
                str(row_data["review"]),
                used_amount_text,
                buy_limit_text,
                usage_rate_text,
                f"● {profit_text}" if profit_text != "-" else "-",
            ]
        )
        row_data["values"] = values

        for col, value in enumerate(values):
            display_value = "" if is_child and col > 0 else value
            item = SortableTableWidgetItem(display_value)
            item.setData(ROUTINE_ROW_KIND_ROLE, row_data["kind"])
            item.setData(ROUTINE_GROUP_ID_ROLE, row_data.get("group_id", ""))
            item.setData(ROUTINE_GROUP_PATH_ROLE, row_data.get("group_path", ""))
            item.setData(ROUTINE_DEFINITION_ID_ROLE, row_data["definition_id"])
            item.setData(ROUTINE_INSTANCE_ID_ROLE, row_data.get("instance_id", ""))
            item.setData(ROUTINE_STOCK_CODE_ROLE, row_data.get("code", ""))
            item.setData(ROUTINE_STOCK_NAME_ROLE, row_data.get("name", ""))
            item.setData(ROUTINE_STOCK_VALUES_ROLE, row_data.get("stock_values", []))
            item.setData(ROUTINE_STOCK_DISPLAY_ROLE, row_data.get("stock_display_tokens", []))
            item.setData(ROUTINE_STOCK_METRICS_ROLE, row_data.get("stock_metrics", ()))
            item.setData(ROUTINE_STOCK_PROFIT_LED_ROLE, row_data.get("stock_profit_led", "gray"))
            item.setData(ROUTINE_STOCK_PATH_ROLE, row_data.get("stock_path", ""))
            if is_stock:
                item.setData(
                    ROUTINE_STOCK_TOOLTIP_DATA_ROLE,
                    {
                        "market": row_data.get("market", ""),
                        "stock_code": row_data.get("code", ""),
                        "stock_name": row_data.get("stock_display_name", ""),
                        "current_price": row_data.get("current_price"),
                        "nxt_available": bool(row_data.get("nxt_available") is True),
                        "stock_state": dict(row_data.get("stock_state") or {}),
                        "stock_status": row_data.get("stock_status", ""),
                    },
                )
            item.setData(ROUTINE_STOCK_INITIAL_BUY_ROLE, row_data.get("initial_buy", {}))
            if col == 0:
                item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
                item.setData(ROUTINE_CHECKBOX_VISUAL_ENABLED_ROLE, row_visually_enabled)
                if is_parent:
                    item.setData(ROUTINE_PARENT_NAME_ROLE, str(row_data["name"]))
                    item.setToolTip(tree_title_tooltip(row_data["name"]))
                    item.setData(ROUTINE_PARENT_AGGREGATE_ROLE, parent_aggregate)
                    item.setData(
                        ROUTINE_PARENT_AGGREGATE_VALUES_ROLE,
                        aggregate_values,
                    )
                    item.setData(
                        ROUTINE_PARENT_PROFIT_ROLE,
                        (
                            str(row_data.get("profit_display", "")),
                            str(row_data.get("profit_color", "")),
                        ),
                    )
                    item.setData(
                        ROUTINE_PARENT_COLLAPSED_ROLE,
                        bool(row_data.get("collapsed")),
                    )
                elif is_child:
                    item.setData(
                        ROUTINE_CHILD_STATUS_ROLE,
                        str(row_data.get("operation_status", "")),
                    )
                    item.setData(ROUTINE_CHILD_AGGREGATE_ROLE, child_aggregate)
                    item.setData(
                        ROUTINE_CHILD_PROFIT_LED_ROLE,
                        routine_instance_profit_led_state(row_data),
                    )
                    item.setData(
                        ROUTINE_CHILD_COLLAPSED_ROLE,
                        bool(row_data.get("collapsed")),
                    )
                    item.setData(
                        ROUTINE_CHILD_HAS_STOCKS_ROLE,
                        bool(row_data.get("stocks")),
                    )
                elif is_stock:
                    item.setToolTip(
                        _main_stock_row_tooltip(
                            row_data.get("market", ""),
                            row_data.get("code", ""),
                            row_data.get("stock_display_name", ""),
                            row_data.get("current_price"),
                            nxt_available=bool(row_data.get("nxt_available") is True),
                            stock_state=row_data.get("stock_state"),
                            stock_status=row_data.get("stock_status"),
                        )
                    )
            if row_data["kind"] == ROUTINE_ROW_CHILD:
                full_name = str(
                    row_data.get("full_name") or row_data.get("name") or ""
                )
                tooltip_parts = [tree_title_tooltip(full_name)]
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

        if is_parent or is_stock:
            set_span = getattr(window.routine_table, "setSpan", None)
            if callable(set_span):
                set_span(row, 0, 1, window.routine_table.columnCount())
        elif is_child:
            set_span = getattr(window.routine_table, "setSpan", None)
            if callable(set_span):
                set_span(row, 1, 1, window.routine_table.columnCount() - 1)
            operation_badge_enabled = routine_instance_operation_badge_enabled(
                definition_enabled=group_enabled,
                registered_count=row_data.get("registered", 0),
            )
            window.routine_table.setCellWidget(
                row,
                1,
                create_routine_instance_status_widget(
                    row_data.get("operation_status", ""),
                    instance_id=str(row_data.get("instance_id", "")),
                    registered=int(row_data["registered"]),
                    excluded=int(row_data["excluded"]),
                    operation_or_stopped=int(row_data["operation_or_stopped"]),
                    review=int(row_data["review"]),
                    buy_limit_text=str(row_data.get("buy_limit_display", "")),
                    consumed_text=str(row_data.get("consumed_display", "")),
                    profit_text=str(row_data.get("profit_display", "")),
                    profit_color=str(row_data.get("profit_color", "")),
                    buy_limit_configured=bool(row_data.get("buy_limit_configured")),
                    enabled=operation_badge_enabled,
                    on_status_click=lambda row=row: window.routine_table.selectRow(row),
                    on_status_double_click=(
                        lambda group_id=str(row_data.get("group_id", "")),
                        instance_id=str(row_data.get("instance_id", "")): (
                            window.toggle_projected_routine_instance_operation(
                                group_id,
                                instance_id,
                            )
                        )
                    ),
                ),
            )

    main_apply_routine_sort(window)



def main_load_running_stock_table(window) -> None:
    """메인 관제창 실행 종목표를 중앙 종목관리 + state 기준으로 표시한다."""
    rows: list[dict[str, object]] = []
    stock_tooltip_metadata_by_code = _stock_library_tooltip_metadata_by_code()
    refresh_context = _main_refresh_read_context(window)
    instances = (
        tuple(refresh_context.get("instances", ()))
        if refresh_context is not None
        else tuple(load_persisted_routine_instances())
    )
    instance_by_id = {
        instance.instance_id: instance
        for instance in instances
    }

    base_stocks = (
        tuple(refresh_context.get("stocks", ()))
        if refresh_context is not None
        else tuple(read_base_stocks())
    )
    for stock in base_stocks:
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
        snapshot_data = _main_refresh_stock_data(window, stock_dir)
        review_inspection = snapshot_data.get("review_inspection")
        state = review_inspection.state
        config = snapshot_data.get("config", {})

        if not isinstance(state, dict):
            state = {}
        if not isinstance(config, dict):
            config = {}

        operation_display = auto_trade_operation_display(config, state)
        operation, _operation_color, _operation_tooltip, _ats_labels = operation_display

        if review_inspection.review_required:
            continue

        trade_started = auto_trade_setting_trade_started(state)
        current_session_trade_started = auto_trade_setting_current_session_trade_started(
            window,
            trade_started,
            code,
        )

        holding_qty = safe_int_value(state.get("holding_qty"), 0)
        avg_price = average_price_from_state(state)
        current_price = current_price_from_state(state)
        buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state) if stock_dir is not None else (0, 0)
        operation_category = auto_trade_stock_operation_category(
            window,
            stock_code=code,
            persisted_trade_started=trade_started,
            operation_excluded=is_operation_excluded(config),
            review_required=False,
        )
        row_projection = auto_trade_setting_row_projection(
            state,
            config,
            operation_category=operation_category,
            holding_qty=holding_qty,
            buy_pending_qty=buy_pending_qty,
            sell_pending_qty=sell_pending_qty,
            current_session_trade_started=current_session_trade_started,
            persisted_trade_started=trade_started,
        )
        display_status = str(row_projection["display_status"])
        status_cell_active = bool(row_projection["status_cell_active"])
        operator_status = main_stock_operator_status(
            window,
            stock_code=code,
            stock_state=state,
            operation_excluded=is_operation_excluded(config),
            review_required=review_inspection.review_required,
        )

        stock_metadata = stock_tooltip_metadata_by_code.get(
            code.upper().lstrip("A"),
            {},
        )
        rows.append(
            {
                "code": code,
                "name": name,
                "routine": routine_name or "미지정",
                "operation": operation,
                "operation_display": operation_display,
                "config": config,
                "state": state,
                "trade_started": bool(row_projection["situation_active"]),
                "status": display_status,
                "operator_status": operator_status,
                "status_cell_active": status_cell_active,
                "holding": f"{holding_qty:,}",
                "avg_price": format_number_value(avg_price),
                "current_price": current_price,
                "market": stock_metadata.get("market", ""),
                "nxt_available": bool(stock_metadata.get("nxt_available") is True),
                "stock_status": stock_metadata.get("status", ""),
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
        row_tooltip = _main_stock_row_tooltip(
            row.get("market", ""),
            row.get("code", ""),
            row.get("name", ""),
            row.get("current_price"),
            nxt_available=bool(row.get("nxt_available") is True),
            stock_state=row.get("state"),
            stock_status=row.get("stock_status"),
        )

        for col, value in enumerate(values):
            if col == 1:
                item = create_auto_trade_stock_name_item(
                    str(value),
                    review_required=False,
                    review_status=False,
                    trade_started=bool(row.get("trade_started")),
                )
            elif col == 3:
                item = create_auto_trade_operation_item(row["operation_display"])
            elif col == 4:
                item = create_auto_trade_situation_item(
                    row.get("state") if isinstance(row.get("state"), dict) else {},
                    bool(row.get("trade_started")),
                    str(row.get("status", "")),
                )
            elif col == 5:
                item = create_auto_trade_setting_activity_status_item(
                    str(value),
                    bool(row.get("status_cell_active")),
                )
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
            if col in {6, 7, 8, 9}:
                apply_auto_trade_plain_metric_item_style(item, value)
            elif col == 1:
                item.setToolTip(row_tooltip)
                item.setData(
                    ROUTINE_STOCK_TOOLTIP_DATA_ROLE,
                    {
                        "market": row.get("market", ""),
                        "stock_code": row.get("code", ""),
                        "stock_name": row.get("name", ""),
                        "current_price": row.get("current_price"),
                        "nxt_available": bool(row.get("nxt_available") is True),
                        "stock_state": dict(row.get("state") or {}),
                        "stock_status": row.get("stock_status", ""),
                    },
                )
            window.running_stock_table.setItem(row_index, col, item)

    main_apply_running_sort(window)
