# -*- coding: utf-8 -*-
"""Disposable persistent historical-candle cache owned by Signal Validation V2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import tempfile


SCHEMA_VERSION = 1
DEFAULT_CACHE_ROOT = (
    Path(__file__).resolve().parent
    / "runtime"
    / "signal_validation_v2_history_cache"
)
_CANDLE_FIELDS = ("time", "open", "high", "low", "close", "volume")


@dataclass(frozen=True, slots=True)
class ValidationHistoricalCacheEntry:
    stock_code: str
    stock_name: str
    timeframe_minutes: int
    requested_count: int
    candles: tuple[dict[str, object], ...]
    updated_at: str


def merge_validation_candles(
    existing: object,
    incoming: object,
    requested_count: int,
) -> list[dict[str, object]]:
    """Return newest normalized candles, deduplicated by candle time."""
    if (
        isinstance(requested_count, bool)
        or not isinstance(requested_count, int)
        or requested_count <= 0
    ):
        raise ValueError("requested_count must be a positive integer")
    candles_by_time: dict[str, dict[str, object]] = {}
    for collection in (existing, incoming):
        if not isinstance(collection, (list, tuple)):
            continue
        for candle in collection:
            normalized = _validated_candle(candle)
            if normalized is not None:
                candles_by_time[str(normalized["time"])] = normalized
    return [
        candles_by_time[key]
        for key in sorted(candles_by_time)[-requested_count:]
    ]


class IndicatorFollowSignalValidationHistoricalCache:
    """Read and atomically replace Validation-owned derived candle files."""

    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        self.root = Path(root) if root is not None else DEFAULT_CACHE_ROOT

    @staticmethod
    def _identity(
        stock_code: object,
        timeframe_minutes: object,
        requested_count: object,
    ) -> tuple[str, int, int]:
        code = str(stock_code or "").strip()
        if not code:
            raise ValueError("stock_code is required")
        for name, value in (
            ("timeframe_minutes", timeframe_minutes),
            ("requested_count", requested_count),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        return code, int(timeframe_minutes), int(requested_count)

    def path_for(
        self,
        stock_code: object,
        timeframe_minutes: object,
        requested_count: object,
    ) -> Path:
        code, timeframe, count = self._identity(
            stock_code,
            timeframe_minutes,
            requested_count,
        )
        safe_code = re.sub(r"[^A-Za-z0-9_-]+", "_", code).strip("_") or "stock"
        return self.root / f"{safe_code}_{timeframe}_{count}.json"

    def load(
        self,
        stock_code: object,
        timeframe_minutes: object,
        requested_count: object,
    ) -> ValidationHistoricalCacheEntry | None:
        try:
            code, timeframe, count = self._identity(
                stock_code,
                timeframe_minutes,
                requested_count,
            )
            path = self.path_for(code, timeframe, count)
            payload = json.loads(path.read_text(encoding="utf-8"))
            return self._entry_from_payload(
                payload,
                expected_identity=(code, timeframe, count),
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def store(
        self,
        *,
        stock_code: object,
        stock_name: object,
        timeframe_minutes: object,
        requested_count: object,
        candles: object,
    ) -> bool:
        temp_path: Path | None = None
        try:
            code, timeframe, count = self._identity(
                stock_code,
                timeframe_minutes,
                requested_count,
            )
            normalized = merge_validation_candles([], candles, count)
            if not normalized:
                return False
            payload = {
                "schema_version": SCHEMA_VERSION,
                "stock": {
                    "code": code,
                    "name": str(stock_name or "").strip(),
                },
                "timeframe_minutes": timeframe,
                "requested_count": count,
                "candles": normalized,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            path = self.path_for(code, timeframe, count)
            path.parent.mkdir(parents=True, exist_ok=True)
            serialized = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            descriptor, temp_name = tempfile.mkstemp(
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=str(path.parent),
            )
            temp_path = Path(temp_name)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, path)
            temp_path = None
            persisted = self.load(code, timeframe, count)
            return persisted is not None and list(persisted.candles) == normalized
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return False
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink()
                except OSError:
                    pass

    @staticmethod
    def _entry_from_payload(
        payload: object,
        *,
        expected_identity: tuple[str, int, int],
    ) -> ValidationHistoricalCacheEntry | None:
        if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
            return None
        stock = payload.get("stock")
        if not isinstance(stock, dict):
            return None
        stock_name = stock.get("name")
        if not isinstance(stock_name, str):
            return None
        identity = (
            stock.get("code"),
            payload.get("timeframe_minutes"),
            payload.get("requested_count"),
        )
        if identity != expected_identity:
            return None
        raw_candles = payload.get("candles")
        if not isinstance(raw_candles, list) or not raw_candles:
            return None
        candles = merge_validation_candles([], raw_candles, expected_identity[2])
        if len(candles) != len(raw_candles):
            return None
        updated_at = str(payload.get("updated_at") or "").strip()
        if not updated_at:
            return None
        try:
            parsed_updated_at = datetime.fromisoformat(updated_at)
        except ValueError:
            return None
        if parsed_updated_at.tzinfo is None:
            return None
        return ValidationHistoricalCacheEntry(
            stock_code=expected_identity[0],
            stock_name=stock_name,
            timeframe_minutes=expected_identity[1],
            requested_count=expected_identity[2],
            candles=tuple(dict(candle) for candle in candles),
            updated_at=updated_at,
        )


def _validated_candle(candle: object) -> dict[str, object] | None:
    if not isinstance(candle, dict) or set(candle) != set(_CANDLE_FIELDS):
        return None
    candle_time = str(candle.get("time") or "").strip()
    if len(candle_time) != 14 or not candle_time.isdigit():
        return None
    try:
        datetime.strptime(candle_time, "%Y%m%d%H%M%S")
    except ValueError:
        return None
    normalized: dict[str, object] = {"time": candle_time}
    for field in _CANDLE_FIELDS[1:]:
        value = candle.get(field)
        if value is None:
            normalized[field] = None
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if not math.isfinite(float(value)):
            return None
        normalized[field] = value
    if normalized["close"] is None:
        return None
    return normalized
