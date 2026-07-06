# -*- coding: utf-8 -*-
"""
gui_auto_trade_order_log.py

자동매매설정창의 주문상태/로그보기 창 호출 헬퍼.
"""

from __future__ import annotations

from PyQt5.QtWidgets import QMessageBox

from gui_order_status_window import OrderStatusWindow
from gui_log_view_window import LogViewWindow


def open_auto_trade_order_status_window(window) -> None:
    """선택 종목의 주문상태 창을 연다."""
    selected = window.selected_stock_info()
    routine_name = window.current_selected_routine_name()

    if selected is None or not routine_name:
        QMessageBox.warning(
            window,
            "선택 오류",
            "주문상태를 확인할 종목을 1개 선택하세요.",
        )
        return

    try:
        stock_dir, code, name = selected
        dialog = OrderStatusWindow(
            stock_dir=stock_dir,
            routine_name=routine_name,
            stock_code=code,
            stock_name=name,
            parent=window,
        )
        dialog.exec_()
    except Exception as exc:
        QMessageBox.critical(
            window,
            "주문상태 보기 오류",
            f"주문상태 창을 여는 중 오류가 발생했습니다.\n\n{exc}",
        )


def open_auto_trade_log_view_window(window) -> None:
    """선택 종목의 로그 보기 창을 연다."""
    selected = window.selected_stock_info()
    routine_name = window.current_selected_routine_name()

    if selected is None or not routine_name:
        QMessageBox.warning(
            window,
            "선택 오류",
            "로그를 확인할 종목을 1개 선택하세요.",
        )
        return

    try:
        stock_dir, code, name = selected
        dialog = LogViewWindow(
            stock_dir=stock_dir,
            routine_name=routine_name,
            stock_code=code,
            stock_name=name,
            parent=window,
        )
        dialog.exec_()
    except Exception as exc:
        QMessageBox.critical(
            window,
            "로그 보기 오류",
            f"로그 보기 창을 여는 중 오류가 발생했습니다.\n\n{exc}",
        )
