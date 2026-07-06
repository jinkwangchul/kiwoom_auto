# -*- coding: utf-8 -*-
"""
gui_styles.py

GUI 공통 스타일 헬퍼.
- 표 헤더의 과한 굵은 강조 제거
- 선택 루틴 라벨 등 반복 UI 스타일 처리
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QPainter
from PyQt5.QtWidgets import QLabel, QHeaderView, QTableWidget


class PlainHorizontalHeader(QHeaderView):
    """
    QTableWidget 가로 헤더를 직접 일반 굵기 텍스트로 그리는 헤더.

    Windows 기본 QHeaderView 스타일은 setFont(), header item font, stylesheet 를
    모두 적용해도 테마에 따라 헤더 텍스트가 굵게 렌더링될 수 있다.
    따라서 헤더 배경과 테두리만 직접 그리고, 텍스트는 QPainter 로
    Normal weight 를 지정해 별도로 그린다.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(Qt.Horizontal, parent)
        self.setDefaultAlignment(Qt.AlignCenter)
        self.setSectionsClickable(True)
        self.setHighlightSections(False)
        font = QFont(self.font())
        font.setBold(False)
        font.setWeight(QFont.Normal)
        self.setFont(font)

    def paintSection(self, painter: QPainter, rect, logicalIndex: int) -> None:  # type: ignore[override]
        if not rect.isValid():
            return

        painter.save()
        painter.fillRect(rect, self.palette().button())
        painter.setPen(self.palette().mid().color())
        painter.drawRect(rect.adjusted(0, 0, -1, -1))

        font = QFont(self.font())
        font.setBold(False)
        font.setWeight(QFont.Normal)
        painter.setFont(font)
        painter.setPen(self.palette().buttonText().color())

        value = self.model().headerData(logicalIndex, self.orientation(), Qt.DisplayRole)
        text = "" if value is None else str(value)
        painter.drawText(
            rect.adjusted(4, 0, -4, 0),
            Qt.AlignCenter | Qt.AlignVCenter | Qt.TextSingleLine,
            text,
        )
        painter.restore()


def apply_plain_table_header(table: QTableWidget) -> None:
    """
    표 헤더를 일반 굵기로 고정한다.
    """
    if not isinstance(table.horizontalHeader(), PlainHorizontalHeader):
        table.setHorizontalHeader(PlainHorizontalHeader(table))

    header = table.horizontalHeader()
    header_font = QFont(header.font())
    header_font.setBold(False)
    header_font.setWeight(QFont.Normal)
    header.setFont(header_font)

    for col in range(table.columnCount()):
        header_item = table.horizontalHeaderItem(col)
        if header_item is not None:
            header_item.setFont(header_font)


def apply_selected_routine_label_style(label: QLabel) -> None:
    """
    자동매매설정 창 하단의 선택 루틴명을 식별 기준으로 강조한다.
    """
    font = QFont(label.font())
    font.setBold(True)
    font.setPointSize(max(font.pointSize(), 10))
    label.setFont(font)
