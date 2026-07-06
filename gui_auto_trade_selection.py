# -*- coding: utf-8 -*-
"""
gui_auto_trade_selection.py

자동매매설정창의 종목 선택/조회 헬퍼.
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt


def selected_stock_rows(window) -> list[int]:
    return [index.row() for index in window.stock_table.selectionModel().selectedRows()]


def has_selected_stock(window) -> bool:
    return len(selected_stock_rows(window)) >= 1


def has_single_selected_stock(window) -> bool:
    return len(selected_stock_rows(window)) == 1


def ensure_context_row_selected(window, row: int) -> None:
    """우클릭한 행이 기존 선택에 없으면 해당 행만 선택한다."""
    if row < 0 or row >= window.stock_table.rowCount():
        return

    if row not in selected_stock_rows(window):
        window.stock_table.clearSelection()
        window.stock_table.selectRow(row)


def select_all_current_routine_stocks(window) -> None:
    window.stock_table.selectAll()
    window.update_action_buttons()
    window.statusBarMessage(f"현재 루틴 전체 종목 선택: {window.stock_table.rowCount()}개")


def clear_current_routine_stock_selection(window) -> None:
    window.stock_table.clearSelection()
    window.update_action_buttons()
    window.statusBarMessage("현재 루틴 종목 선택 해제")


def selected_stock_dir(window) -> Path | None:
    rows = selected_stock_rows(window)
    if len(rows) != 1:
        return None

    item = window.stock_table.item(rows[0], 0)
    if item is None:
        return None

    path_text = item.data(Qt.UserRole)
    if not path_text:
        return None

    stock_dir = Path(str(path_text))
    if not stock_dir.exists():
        return None

    return stock_dir


def selected_stock_info(window) -> tuple[Path, str, str] | None:
    rows = selected_stock_rows(window)
    if len(rows) != 1:
        return None

    row = rows[0]
    code_item = window.stock_table.item(row, 0)
    name_item = window.stock_table.item(row, 1)
    stock_dir = selected_stock_dir(window)

    if code_item is None or name_item is None or stock_dir is None:
        return None

    return stock_dir, code_item.text().strip(), name_item.text().strip()


def selected_stock_infos(window) -> list[tuple[Path, str, str]]:
    infos: list[tuple[Path, str, str]] = []
    seen_rows: set[int] = set()

    for row in selected_stock_rows(window):
        if row in seen_rows:
            continue
        seen_rows.add(row)

        code_item = window.stock_table.item(row, 0)
        name_item = window.stock_table.item(row, 1)
        path_item = window.stock_table.item(row, 0)

        if code_item is None or name_item is None or path_item is None:
            continue

        path_text = path_item.data(Qt.UserRole)
        if not path_text:
            continue

        stock_dir = Path(str(path_text))
        if not stock_dir.exists():
            continue

        infos.append((stock_dir, code_item.text().strip(), name_item.text().strip()))

    return infos
