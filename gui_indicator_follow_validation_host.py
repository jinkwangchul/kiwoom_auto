# -*- coding: utf-8 -*-
"""Coordinator for starting an indicator-follow Validation session."""

from __future__ import annotations

from collections.abc import Callable

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QDialog, QWidget

from gui_indicator_follow_validation_stock_picker import (
    IndicatorFollowValidationStockPicker,
)
from routines.지표추종매매.routine_validation_contract import (
    ValidationRequest,
    ValidationSettingsSnapshot,
    ValidationStockRef,
)
from routines.지표추종매매.routine_validation_operation_reader import (
    is_operation_active,
)
from routines.지표추종매매.routine_validation_session import (
    REASON_OPERATION_ACTIVE,
    REASON_OPERATION_ACTIVE_READER_ERROR,
    REASON_OPERATION_ACTIVE_READER_INVALID,
    REASON_OPERATION_ACTIVE_READER_UNAVAILABLE,
    ValidationSession,
)


REASON_INVALID_SETTINGS_SNAPSHOT = "INVALID_SETTINGS_SNAPSHOT"
REASON_INVALID_TIMEFRAME = "INVALID_TIMEFRAME"
REASON_INVALID_SELECTED_STOCK = "INVALID_SELECTED_STOCK"


class IndicatorFollowValidationHost(QObject):
    """Validate inputs and coordinate one ready-session handoff."""

    validation_session_ready = pyqtSignal(object)
    validation_blocked = pyqtSignal(str)

    def __init__(
        self,
        parent=None,
        *,
        operation_active_reader: Callable[[], bool] | None = is_operation_active,
        stock_picker_factory: Callable[[object], object] = (
            IndicatorFollowValidationStockPicker
        ),
    ) -> None:
        super().__init__(parent)
        self._operation_active_reader = operation_active_reader
        self._stock_picker_factory = stock_picker_factory

    def start(
        self,
        settings_snapshot: ValidationSettingsSnapshot,
        *,
        ui_parent: QWidget | None = None,
    ) -> ValidationSession | None:
        if not isinstance(settings_snapshot, ValidationSettingsSnapshot):
            self.validation_blocked.emit(REASON_INVALID_SETTINGS_SNAPSHOT)
            return None

        try:
            rules = settings_snapshot.to_dict()
            timeframe_minutes = rules["bar"]["bar_minutes"]
        except (KeyError, TypeError, ValueError):
            self.validation_blocked.emit(REASON_INVALID_TIMEFRAME)
            return None
        if (
            isinstance(timeframe_minutes, bool)
            or not isinstance(timeframe_minutes, int)
            or timeframe_minutes <= 0
        ):
            self.validation_blocked.emit(REASON_INVALID_TIMEFRAME)
            return None

        operation_block_reason = self._preflight_operation_block_reason()
        if operation_block_reason is not None:
            self.validation_blocked.emit(operation_block_reason)
            return None

        picker = self._stock_picker_factory(self._picker_parent(ui_parent))
        if picker.exec_() != QDialog.Accepted:
            return None
        selected_stock = picker.selected_stock
        if (
            not isinstance(selected_stock, ValidationStockRef)
            or not selected_stock.code
            or not selected_stock.name
        ):
            self.validation_blocked.emit(REASON_INVALID_SELECTED_STOCK)
            return None

        request = ValidationRequest(
            stock=selected_stock,
            settings_snapshot=settings_snapshot,
            timeframe_minutes=timeframe_minutes,
        )
        session = ValidationSession(
            request,
            operation_active_reader=self._operation_active_reader,
        )
        availability = session.readiness()
        if not availability.allowed:
            self.validation_blocked.emit(str(availability.reason or ""))
            return None

        self.validation_session_ready.emit(session)
        return session

    def _picker_parent(self, ui_parent: object) -> QWidget | None:
        if isinstance(ui_parent, QWidget):
            try:
                ui_parent.window()
            except RuntimeError:
                pass
            else:
                return ui_parent
        lifetime_parent = self.parent()
        if isinstance(lifetime_parent, QWidget):
            try:
                lifetime_parent.window()
            except RuntimeError:
                return None
            return lifetime_parent
        return None

    def _preflight_operation_block_reason(self) -> str | None:
        reader = self._operation_active_reader
        if not callable(reader):
            return REASON_OPERATION_ACTIVE_READER_UNAVAILABLE
        try:
            active = reader()
        except Exception:
            return REASON_OPERATION_ACTIVE_READER_ERROR
        if not isinstance(active, bool):
            return REASON_OPERATION_ACTIVE_READER_INVALID
        if active:
            return REASON_OPERATION_ACTIVE
        return None
