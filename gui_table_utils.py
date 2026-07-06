# -*- coding: utf-8 -*-
"""
gui_table_utils.py

GUI 테이블 정렬/선택 관련 공통 유틸리티.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt


def next_sort_order(
    current_column: int,
    requested_column: int,
    current_order: Qt.SortOrder,
) -> Qt.SortOrder:
    """
    같은 컬럼을 다시 누르면 오름차순/내림차순을 전환한다.
    다른 컬럼을 누르면 오름차순부터 시작한다.
    """
    if current_column == requested_column:
        return Qt.DescendingOrder if current_order == Qt.AscendingOrder else Qt.AscendingOrder
    return Qt.AscendingOrder
