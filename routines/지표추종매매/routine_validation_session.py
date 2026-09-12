# -*- coding: utf-8 -*-
"""Minimal, fail-closed session boundary for indicator-follow validation."""

from __future__ import annotations

from collections.abc import Callable

from .routine_validation_contract import ValidationAvailability, ValidationRequest
from .routine_validation_trace import ValidationTraceObserver


REASON_OPERATION_ACTIVE_READER_UNAVAILABLE = (
    "OPERATION_ACTIVE_READER_UNAVAILABLE"
)
REASON_OPERATION_ACTIVE_READER_ERROR = "OPERATION_ACTIVE_READER_ERROR"
REASON_OPERATION_ACTIVE_READER_INVALID = "OPERATION_ACTIVE_READER_INVALID"
REASON_OPERATION_ACTIVE = "OPERATION_ACTIVE"


class ValidationSession:
    """Hold one request and expose only the Phase-1 readiness decision."""

    def __init__(
        self,
        request: ValidationRequest,
        *,
        trace_observer: ValidationTraceObserver | None = None,
        operation_active_reader: Callable[[], bool] | None = None,
    ) -> None:
        if not isinstance(request, ValidationRequest):
            raise TypeError("request must be ValidationRequest")
        self.request = request
        self.trace_observer = (
            trace_observer
            if trace_observer is not None
            else ValidationTraceObserver()
        )
        self.operation_active_reader = operation_active_reader

    def readiness(self) -> ValidationAvailability:
        reader = self.operation_active_reader
        if not callable(reader):
            return ValidationAvailability(
                allowed=False,
                reason=REASON_OPERATION_ACTIVE_READER_UNAVAILABLE,
            )
        try:
            active = reader()
        except Exception:
            return ValidationAvailability(
                allowed=False,
                reason=REASON_OPERATION_ACTIVE_READER_ERROR,
            )
        if not isinstance(active, bool):
            return ValidationAvailability(
                allowed=False,
                reason=REASON_OPERATION_ACTIVE_READER_INVALID,
            )
        if active:
            return ValidationAvailability(
                allowed=False,
                reason=REASON_OPERATION_ACTIVE,
            )
        return ValidationAvailability(allowed=True, reason=None)
