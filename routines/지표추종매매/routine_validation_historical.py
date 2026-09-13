# -*- coding: utf-8 -*-
"""Read-only historical rows boundary for indicator-follow Validation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
from typing import Any, Protocol

from .routine_validation_contract import ValidationStockRef
from .routine_validation_session import ValidationSession


REASON_INVALID_COUNT = "INVALID_COUNT"
REASON_BROKER_REQUESTER_UNAVAILABLE = "BROKER_REQUESTER_UNAVAILABLE"
REASON_BROKER_REQUEST_ERROR = "BROKER_REQUEST_ERROR"
REASON_INVALID_HISTORICAL_RESPONSE = "INVALID_HISTORICAL_RESPONSE"
REASON_HISTORICAL_REQUEST_FAILED = "HISTORICAL_REQUEST_FAILED"


@dataclass(frozen=True, slots=True, init=False)
class ValidationHistoricalSnapshot:
    """Immutable copy of one Broker historical response."""

    stock: ValidationStockRef
    timeframe_minutes: int
    requested_count: int
    rows_count: int
    request_id: str
    raw_rows_json: str

    def __init__(
        self,
        *,
        stock: ValidationStockRef,
        timeframe_minutes: int,
        requested_count: int,
        request_id: str,
        rows: list[dict[str, Any]],
    ) -> None:
        if not isinstance(stock, ValidationStockRef):
            raise TypeError("stock must be ValidationStockRef")
        if (
            isinstance(timeframe_minutes, bool)
            or not isinstance(timeframe_minutes, int)
            or timeframe_minutes <= 0
        ):
            raise ValueError("timeframe_minutes must be a positive integer")
        if (
            isinstance(requested_count, bool)
            or not isinstance(requested_count, int)
            or requested_count <= 0
        ):
            raise ValueError("requested_count must be a positive integer")
        clean_request_id = str(request_id or "").strip()
        if not clean_request_id:
            raise ValueError("request_id is required")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise TypeError("rows must be a list of objects")

        canonical_rows = json.dumps(
            rows,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        copied_rows = json.loads(canonical_rows)
        if not isinstance(copied_rows, list) or any(
            not isinstance(row, dict) for row in copied_rows
        ):
            raise ValueError("canonical rows are invalid")

        object.__setattr__(self, "stock", stock)
        object.__setattr__(self, "timeframe_minutes", timeframe_minutes)
        object.__setattr__(self, "requested_count", requested_count)
        object.__setattr__(self, "rows_count", len(copied_rows))
        object.__setattr__(self, "request_id", clean_request_id)
        object.__setattr__(self, "raw_rows_json", canonical_rows)

    def to_rows(self) -> list[dict[str, Any]]:
        rows = json.loads(self.raw_rows_json)
        if not isinstance(rows, list):
            raise ValueError("canonical rows must decode to a list")
        return rows


@dataclass(frozen=True, slots=True)
class ValidationHistoricalResult:
    ok: bool
    snapshot: ValidationHistoricalSnapshot | None = None
    reason: str | None = None
    error: str | None = None


class ReadOnlyMinuteCandleRequester(Protocol):
    def request_minute_candles_read_only(
        self,
        code: str,
        name: str,
        *,
        interval: int,
        count: int,
        callback: Callable[[dict[str, Any]], None],
    ) -> object: ...


class ValidationHistoricalMemoryCache:
    """Provider-owned, process-local historical snapshot cache."""

    def __init__(self) -> None:
        self._snapshots: dict[
            tuple[str, int, int], ValidationHistoricalSnapshot
        ] = {}

    @staticmethod
    def _key(
        stock: ValidationStockRef,
        timeframe_minutes: int,
        requested_count: int,
    ) -> tuple[str, int, int]:
        return (stock.code, timeframe_minutes, requested_count)

    def get(
        self,
        stock: ValidationStockRef,
        timeframe_minutes: int,
        requested_count: int,
    ) -> ValidationHistoricalSnapshot | None:
        return self._snapshots.get(
            self._key(stock, timeframe_minutes, requested_count)
        )

    def put(self, snapshot: ValidationHistoricalSnapshot) -> None:
        if not isinstance(snapshot, ValidationHistoricalSnapshot):
            raise TypeError("snapshot must be ValidationHistoricalSnapshot")
        self._snapshots[
            self._key(
                snapshot.stock,
                snapshot.timeframe_minutes,
                snapshot.requested_count,
            )
        ] = snapshot

    def clear(self) -> None:
        self._snapshots.clear()

    @property
    def size(self) -> int:
        return len(self._snapshots)

    def __len__(self) -> int:
        return self.size


class ValidationHistoricalProvider:
    """Request fresh read-only rows and cache only a still-allowed response."""

    def __init__(
        self,
        session: ValidationSession,
        read_only_minute_candle_requester: ReadOnlyMinuteCandleRequester,
        *,
        cache: ValidationHistoricalMemoryCache | None = None,
    ) -> None:
        if not isinstance(session, ValidationSession):
            raise TypeError("session must be ValidationSession")
        self.session = session
        self.requester = read_only_minute_candle_requester
        self.cache = cache if cache is not None else ValidationHistoricalMemoryCache()

    def request_latest(
        self,
        count: int,
        callback: Callable[[ValidationHistoricalResult], None],
    ) -> ValidationHistoricalResult | None:
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            return self._finish(
                callback,
                ValidationHistoricalResult(False, reason=REASON_INVALID_COUNT),
            )

        availability = self.session.readiness()
        if not availability.allowed:
            return self._finish(
                callback,
                ValidationHistoricalResult(False, reason=availability.reason),
            )

        requester = getattr(self.requester, "request_minute_candles_read_only", None)
        if not callable(requester):
            return self._finish(
                callback,
                ValidationHistoricalResult(
                    False,
                    reason=REASON_BROKER_REQUESTER_UNAVAILABLE,
                ),
            )

        completed = False

        def receive_response(response: object) -> None:
            nonlocal completed
            if completed:
                return
            completed = True
            self._finish(callback, self._process_response(response, count))

        request = self.session.request
        try:
            requester(
                request.stock.code,
                request.stock.name,
                interval=request.timeframe_minutes,
                count=count,
                callback=receive_response,
            )
        except Exception as exc:
            if completed:
                return None
            completed = True
            return self._finish(
                callback,
                ValidationHistoricalResult(
                    False,
                    reason=REASON_BROKER_REQUEST_ERROR,
                    error=str(exc),
                ),
            )
        return None

    def _process_response(
        self,
        response: object,
        requested_count: int,
    ) -> ValidationHistoricalResult:
        if not isinstance(response, dict):
            return ValidationHistoricalResult(
                False,
                reason=REASON_INVALID_HISTORICAL_RESPONSE,
            )
        if response.get("ok") is not True:
            return ValidationHistoricalResult(
                False,
                reason=REASON_HISTORICAL_REQUEST_FAILED,
                error=str(response.get("error") or "") or None,
            )

        request = self.session.request
        rows = response.get("rows")
        rows_count = response.get("rows_count")
        interval = response.get("interval")
        request_id = response.get("request_id")
        valid_shape = (
            response.get("type") == "minute_candles"
            and response.get("code") == request.stock.code
            and isinstance(interval, int)
            and not isinstance(interval, bool)
            and interval == request.timeframe_minutes
            and isinstance(rows, list)
            and isinstance(rows_count, int)
            and not isinstance(rows_count, bool)
            and rows_count == len(rows)
            and isinstance(request_id, str)
            and bool(request_id.strip())
        )
        if not valid_shape:
            return ValidationHistoricalResult(
                False,
                reason=REASON_INVALID_HISTORICAL_RESPONSE,
            )

        availability = self.session.readiness()
        if not availability.allowed:
            return ValidationHistoricalResult(False, reason=availability.reason)

        try:
            snapshot = ValidationHistoricalSnapshot(
                stock=request.stock,
                timeframe_minutes=request.timeframe_minutes,
                requested_count=requested_count,
                request_id=request_id,
                rows=rows,
            )
        except (TypeError, ValueError, OverflowError):
            return ValidationHistoricalResult(
                False,
                reason=REASON_INVALID_HISTORICAL_RESPONSE,
            )
        if snapshot.rows_count != rows_count:
            return ValidationHistoricalResult(
                False,
                reason=REASON_INVALID_HISTORICAL_RESPONSE,
            )

        self.cache.put(snapshot)
        return ValidationHistoricalResult(True, snapshot=snapshot)

    @staticmethod
    def _finish(
        callback: Callable[[ValidationHistoricalResult], None],
        result: ValidationHistoricalResult,
    ) -> ValidationHistoricalResult:
        if callable(callback):
            callback(result)
        return result
