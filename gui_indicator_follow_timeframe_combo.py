# -*- coding: utf-8 -*-
"""Operator-facing timeframe combo with legacy minute-value compatibility."""

from __future__ import annotations

from PyQt5.QtWidgets import QComboBox

from indicator_follow_validation_timeframe import (
    TIMEFRAME_LABELS,
    normalize_validation_timeframe,
)


class IndicatorFollowTimeframeComboBox(QComboBox):
    """Show full timeframe labels while preserving legacy minute UI values."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.addItems(list(TIMEFRAME_LABELS))

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
