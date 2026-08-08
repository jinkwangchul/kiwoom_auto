# -*- coding: utf-8 -*-
"""Non-persistent identity resolver for Decision Trace stage correlation."""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Any

from decision_trace_reader import DecisionTraceReader


@dataclass(frozen=True)
class TraceCorrelation:
    trace_id: str
    trace_level: str


class DecisionTraceCorrelationResolver:
    """Resolve existing Production identities without creating a new Source of Truth."""

    def __init__(self, reader: DecisionTraceReader | None = None) -> None:
        self.reader = reader or DecisionTraceReader()
        self._lock = threading.RLock()
        self._by_signal: dict[str, TraceCorrelation] = {}
        self._by_order: dict[str, TraceCorrelation] = {}
        self._by_execution: dict[str, TraceCorrelation] = {}

    def register(
        self,
        *,
        trace_id: str,
        trace_level: str,
        signal_id: str = "",
        order_id: str = "",
        execution_id: str = "",
    ) -> TraceCorrelation | None:
        clean_trace = str(trace_id or "").strip()
        clean_level = str(trace_level or "").strip().upper()
        if not clean_trace or clean_level not in {"NORMAL", "DIAGNOSTIC", "BACKTEST"}:
            return None
        value = TraceCorrelation(clean_trace, clean_level)
        with self._lock:
            for identity, target in (
                (signal_id, self._by_signal),
                (order_id, self._by_order),
                (execution_id, self._by_execution),
            ):
                clean = str(identity or "").strip()
                if clean:
                    target[clean] = value
        return value

    def resolve(
        self,
        *,
        signal_id: str = "",
        order_id: str = "",
        execution_id: str = "",
    ) -> TraceCorrelation | None:
        identities = (
            (str(execution_id or "").strip(), self._by_execution),
            (str(order_id or "").strip(), self._by_order),
            (str(signal_id or "").strip(), self._by_signal),
        )
        with self._lock:
            cached = {target[value] for value, target in identities if value and value in target}
        if len(cached) == 1:
            correlation = next(iter(cached))
            self.register(
                trace_id=correlation.trace_id,
                trace_level=correlation.trace_level,
                signal_id=signal_id,
                order_id=order_id,
                execution_id=execution_id,
            )
            return correlation
        if len(cached) > 1:
            return None

        for field, value in (
            ("execution_id", execution_id),
            ("order_id", order_id),
            ("signal_id", signal_id),
        ):
            clean = str(value or "").strip()
            if not clean:
                continue
            try:
                result = self.reader.read_records(**{field: clean}, descending=True)
            except Exception:
                continue
            records = result.get("records", []) if isinstance(result, dict) else []
            correlations = {
                TraceCorrelation(
                    str(record.get("trace_id") or "").strip(),
                    str(record.get("trace_level") or "").strip().upper(),
                )
                for record in records
                if isinstance(record, dict)
                and str(record.get("trace_id") or "").strip()
                and str(record.get("trace_level") or "").strip().upper()
                in {"NORMAL", "DIAGNOSTIC", "BACKTEST"}
            }
            if len(correlations) != 1:
                continue
            correlation = next(iter(correlations))
            self.register(
                trace_id=correlation.trace_id,
                trace_level=correlation.trace_level,
                signal_id=signal_id,
                order_id=order_id,
                execution_id=execution_id,
            )
            return correlation
        return None


_DEFAULT_RESOLVER = DecisionTraceCorrelationResolver()


def default_trace_correlation_resolver() -> DecisionTraceCorrelationResolver:
    return _DEFAULT_RESOLVER
