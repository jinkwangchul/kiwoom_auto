# -*- coding: utf-8 -*-
"""Minimal read-only session boundary for indicator-follow validation."""

from __future__ import annotations

from collections.abc import Callable

from .routine_validation_contract import ValidationAvailability, ValidationRequest
from .routine_validation_trace import ValidationTraceObserver


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
        """Validation is available regardless of Production Operation state.

        operation_active_reader remains constructor-compatible for existing
        callers, but Operation activity is intentionally not consulted. The
        validation path is read-only with respect to Production trading state.
        """
        return ValidationAvailability(allowed=True, reason=None)
