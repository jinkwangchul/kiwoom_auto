# -*- coding: utf-8 -*-
"""Operator-facing timeframe combo with legacy minute-value compatibility."""

from __future__ import annotations

from PyQt5.QtCore import QSize, Qt
from PyQt5.QtWidgets import QComboBox, QStyledItemDelegate

from indicator_follow_validation_timeframe import (
    MINUTE_VALUES,
    TIMEFRAME_LABELS,
    normalize_validation_timeframe,
)


class _TimeframePopupDelegate(QStyledItemDelegate):
    """Give timeframe items breathing room and center the period separator."""

    def sizeHint(self, option, index):  # noqa: N802 - Qt API compatibility
        base = super().sizeHint(option, index)
        font_height = max(1, option.fontMetrics.height())
        if index.data(Qt.AccessibleDescriptionRole) == "separator":
            return QSize(base.width(), font_height + 2)
        return QSize(base.width(), font_height + 4)

    def paint(self, painter, option, index):
        if index.data(Qt.AccessibleDescriptionRole) != "separator":
            super().paint(painter, option, index)
            return
        painter.save()
        pen = painter.pen()
        pen.setColor(option.palette.mid().color())
        painter.setPen(pen)
        y = option.rect.center().y()
        painter.drawLine(
            option.rect.left() + 4,
            y,
            option.rect.right() - 4,
            y,
        )
        painter.restore()


class IndicatorFollowTimeframeComboBox(QComboBox):
    """Show full timeframe labels while preserving legacy minute UI values."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        minute_count = len(MINUTE_VALUES)
        self.addItems(list(TIMEFRAME_LABELS[:minute_count]))
        self.insertSeparator(self.count())
        self.addItems(list(TIMEFRAME_LABELS[minute_count:]))
        self._popup_delegate = _TimeframePopupDelegate(self.view())
        self.view().setItemDelegate(self._popup_delegate)
        self._apply_popup_spacing()

    def _apply_popup_spacing(self) -> None:
        font_metrics = self.fontMetrics()
        font_height = max(1, font_metrics.height())
        separator_index = len(MINUTE_VALUES)
        popup_width = max(
            font_metrics.horizontalAdvance(self.itemText(index))
            for index in range(self.count())
            if self.itemText(index)
        ) + 20
        model = self.model()
        for index in range(self.count()):
            height = font_height + (2 if index == separator_index else 4)
            model.setData(
                model.index(index, 0),
                QSize(popup_width, height),
                Qt.SizeHintRole,
            )

    def showPopup(self) -> None:  # noqa: N802 - Qt API compatibility
        self._apply_popup_spacing()
        super().showPopup()

    def setCurrentText(self, text: str) -> None:  # noqa: N802 - Qt API compatibility
        identity = normalize_validation_timeframe(text)
        super().setCurrentText(str(identity["label"]))

    def currentText(self) -> str:  # noqa: N802 - Qt API compatibility
        return super().currentText()

    def displayText(self) -> str:
        return super().currentText()

    def validationValue(self) -> str:  # noqa: N802 - Qt API compatibility
        identity = normalize_validation_timeframe(super().currentText())
        if identity["kind"] == "MINUTE":
            return str(identity["minutes"])
        return str(identity["label"])
