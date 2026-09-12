# -*- coding: utf-8 -*-
"""Validation-local, in-memory observer for routine evaluation traces."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


class ValidationTraceObserver:
    """Collect the evaluator's existing observer callbacks without side effects."""

    def __init__(self) -> None:
        self._conditions: list[Any] = []
        self._groups: list[Any] = []
        self._aggregations: list[dict[str, Any]] = []

    @staticmethod
    def _copy(value: Any) -> Any:
        return deepcopy(value)

    def observe_condition(self, payload: Any) -> None:
        try:
            copied = self._copy(payload)
            self._conditions.append(copied)
        except Exception:
            return

    def observe_group(self, payload: Any) -> None:
        try:
            copied = self._copy(payload)
            self._groups.append(copied)
        except Exception:
            return

    def observe_aggregation(self, side: Any, payload: Any) -> None:
        try:
            copied = {
                "side": self._copy(side),
                "payload": self._copy(payload),
            }
            self._aggregations.append(copied)
        except Exception:
            return

    def snapshot(self) -> dict[str, list[Any]]:
        try:
            return {
                "conditions": self._copy(self._conditions),
                "groups": self._copy(self._groups),
                "aggregations": self._copy(self._aggregations),
            }
        except Exception:
            return {"conditions": [], "groups": [], "aggregations": []}

    def clear(self) -> None:
        self._conditions.clear()
        self._groups.clear()
        self._aggregations.clear()
