# -*- coding: utf-8 -*-
"""
gui_centered_checkbox_delegate.py

테이블 체크박스를 셀 중앙에 표시하는 공용 델리게이트.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, QRect
from PyQt5.QtWidgets import (
    QApplication,
    QStyle,
    QStyleOptionButton,
    QStyledItemDelegate,
)


class CenteredCheckBoxDelegate(QStyledItemDelegate):
    """체크박스를 셀 중앙에 그리는 전용 델리게이트."""

    def paint(self, painter, option, index) -> None:
        check_state = index.data(Qt.CheckStateRole)
        if check_state is None:
            super().paint(painter, option, index)
            return

        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())

        check_option = QStyleOptionButton()
        check_option.state = QStyle.State_Enabled
        if check_state == Qt.Checked:
            check_option.state |= QStyle.State_On
        else:
            check_option.state |= QStyle.State_Off

        style = QApplication.style()
        indicator_rect = style.subElementRect(QStyle.SE_CheckBoxIndicator, check_option, None)
        check_option.rect = QRect(
            option.rect.x() + (option.rect.width() - indicator_rect.width()) // 2,
            option.rect.y() + (option.rect.height() - indicator_rect.height()) // 2,
            indicator_rect.width(),
            indicator_rect.height(),
        )
        style.drawControl(QStyle.CE_CheckBox, check_option, painter)

