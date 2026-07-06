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

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor

from gui_table_utils import next_sort_order
from gui_common_utils import safe_int_value
from gui_stock_data import stock_runtime_dir_for_routine
from gui_order_utils import (
    pending_order_side_quantities,
    format_number_value,
)
from gui_review_utils import safe_float_value
from runtime_io import read_json_dict
from state_policy import normalize_operation_mode
from gui_auto_trade_display import (
    auto_trade_setting_display_status,
    create_auto_trade_setting_status_item,
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
from gui_base_stock_service import read_base_stocks
from gui_routine_registry import read_routine_budget


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
    window.routine_table.sortItems(column, window._main_routine_sort_order)
    window.routine_table.horizontalHeader().setSortIndicator(column, window._main_routine_sort_order)


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
        window.routine_table.sortItems(window._main_routine_sort_column, window._main_routine_sort_order)
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


def main_load_routine_table(window) -> None:
    """budget.json이 있는 루틴 폴더를 메인 좌측 루틴표에 표시한다.

    종목수는 더 이상 루틴폴더 안의 물리 종목폴더 개수로 계산하지 않는다.
    중앙 종목관리(read_base_stocks -> stocks/config.json) 기준으로 계산한다.
    """
    routine_dirs = get_routine_dirs()
    routine_counts = _routine_stock_counts_from_base_stocks()

    window.routine_table.setRowCount(len(routine_dirs))

    for row, routine_dir in enumerate(routine_dirs):
        routine_name = routine_display_name(routine_dir)

        total_budget = 0
        used_budget = 0
        available_budget = 0

        try:
            budget = read_routine_budget(routine_dir)
            total_budget = int(budget.get("total_budget", 0))
            used_budget = int(budget.get("used_budget", 0))
            available_budget = int(budget.get("available_budget", 0))
        except Exception:
            total_budget = 0
            used_budget = 0
            available_budget = 0

        stock_count = int(routine_counts.get(routine_name, 0))

        values = [
            routine_name,
            str(stock_count),
            "0",
            str(stock_count),
            "0",
            f"{total_budget:,}",
            f"{used_budget:,}",
            f"{available_budget:,}",
        ]

        for col, value in enumerate(values):
            item = SortableTableWidgetItem(value)
            if col in {1, 2, 3, 4, 5, 6, 7}:
                try:
                    item.setData(SORT_ROLE, int(str(value).replace(",", "")))
                except Exception:
                    pass
            item.setTextAlignment(Qt.AlignCenter)
            window.routine_table.setItem(row, col, item)

    main_apply_routine_sort(window)



def main_load_running_stock_table(window) -> None:
    """메인 관제창 실행 종목표를 중앙 종목관리 + state 기준으로 표시한다."""
    rows: list[dict[str, object]] = []

    for stock in read_base_stocks():
        code = str(stock.get("code", "")).strip()
        name = str(stock.get("name", "")).strip()
        routine_list = _routine_names_for_stock_record(stock)
        routine_name = routine_list[0] if routine_list else ""

        if not code or not name:
            continue

        # 메인 우측 표는 "실행 중 자동매매 종목" 영역이므로
        # 루틴 미지정 종목은 표시하지 않는다.
        if not routine_name:
            continue

        stock_dir = stock_runtime_dir_for_routine(routine_name, code, name)
        state = read_json_dict(stock_dir / "state.json") if stock_dir is not None else {}
        config = read_json_dict(stock_dir / "config.json") if stock_dir is not None else {}

        if not isinstance(state, dict):
            state = {}
        if not isinstance(config, dict):
            config = {}

        raw_mode = normalize_operation_mode(config.get("operation_mode", "SCHEDULED"))
        operation = "수동" if raw_mode == "CONTINUOUS" else "시간"

        raw_status = str(state.get("status", "STOPPED")).strip() or "STOPPED"
        display_status = auto_trade_setting_display_status(raw_status)

        if is_review_required_state(state):
            continue

        trade_started = auto_trade_setting_trade_started(state)

        holding_qty = safe_int_value(state.get("holding_qty"), 0)
        avg_price = safe_float_value(state.get("avg_price"), 0.0)
        buy_pending_qty, sell_pending_qty = pending_order_side_quantities(stock_dir, state) if stock_dir is not None else (0, 0)

        rows.append(
            {
                "code": code,
                "name": name,
                "routine": routine_name or "미지정",
                "operation": operation,
                "state": state,
                "trade_started": trade_started,
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
