# -*- coding: utf-8 -*-
"""Immutable content-addressed candle-window evidence store."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import threading
from typing import Any, Callable

from decision_trace_snapshot_service import canonical_json_bytes


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_MARKET_EVIDENCE_DIR = PROJECT_ROOT / "runtime" / "diagnostics" / "decision_trace" / "evidence" / "market"
MARKET_EVIDENCE_SCHEMA_VERSION = "market_evidence_v1"
CANDLE_FIELDS = ("timestamp", "open", "high", "low", "close", "volume")
TIMESTAMP_ALIASES = ("timestamp", "datetime", "time", "date", "bar_time")
SENSITIVE_KEY_PARTS = ("account", "token", "secret", "password", "auth", "credential", "broker_order")

_EVIDENCE_LOCK = threading.RLock()


def _default_now() -> str:
    return datetime.now().astimezone().isoformat()


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).casefold()
            if any(part in lowered for part in SENSITIVE_KEY_PARTS):
                return True
            if _contains_sensitive_key(child):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_sensitive_key(child) for child in value)
    return False


def _number(value: Any, field: str) -> int | float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        decimal = Decimal(str(value)).normalize()
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    number = float(decimal)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    if decimal == decimal.to_integral_value():
        return int(decimal)
    return number


def normalize_candle_window(candles: Any) -> list[dict[str, Any]]:
    if not isinstance(candles, list) or not candles:
        raise ValueError("candle window must be a non-empty array")
    if _contains_sensitive_key(candles):
        raise ValueError("market evidence contains a sensitive field")
    normalized: list[dict[str, Any]] = []
    for index, candle in enumerate(candles):
        if not isinstance(candle, dict):
            raise ValueError(f"candle[{index}] must be an object")
        timestamp = next((candle.get(key) for key in TIMESTAMP_ALIASES if candle.get(key) not in (None, "")), None)
        if timestamp is None:
            raise ValueError(f"candle[{index}].timestamp is required")
        item: dict[str, Any] = {"timestamp": str(timestamp)}
        for field in CANDLE_FIELDS[1:]:
            if field not in candle:
                raise ValueError(f"candle[{index}].{field} is required")
            item[field] = _number(candle[field], f"candle[{index}].{field}")
        normalized.append(item)
    return normalized


def market_window_hash(candles: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(normalize_candle_window(candles))).hexdigest()


class MarketEvidenceStore:
    def __init__(
        self,
        evidence_dir: Path = DEFAULT_MARKET_EVIDENCE_DIR,
        *,
        now_factory: Callable[[], str] = _default_now,
    ) -> None:
        self.evidence_dir = Path(evidence_dir)
        self._now_factory = now_factory

    def save_window(self, candles: Any) -> dict[str, Any]:
        try:
            payload = normalize_candle_window(candles)
        except Exception as exc:
            return self._result("", None, invalid=True, error=str(exc))
        identity = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        path = self.evidence_dir / f"{identity}.json"
        document = {
            "schema_version": MARKET_EVIDENCE_SCHEMA_VERSION,
            "input_window_hash": identity,
            "created_at": self._now_factory(),
            "input_window_length": len(payload),
            "first_bar_time": payload[0]["timestamp"],
            "last_bar_time": payload[-1]["timestamp"],
            "payload": payload,
        }
        try:
            with _EVIDENCE_LOCK:
                if path.exists():
                    existing = json.loads(path.read_text(encoding="utf-8"))
                    if (
                        not isinstance(existing, dict)
                        or existing.get("input_window_hash") != identity
                        or existing.get("payload") != payload
                    ):
                        return self._result(identity, path, integrity_error=True, error="existing evidence content mismatch")
                    return self._result(identity, path, duplicate=True, payload=payload)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("x", encoding="utf-8", newline="") as handle:
                    json.dump(document, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    handle.write("\n")
                    handle.flush()
        except Exception as exc:
            return self._result(identity, path, write_failed=True, error=str(exc), payload=payload)
        return self._result(identity, path, saved=True, payload=payload)

    @staticmethod
    def _result(
        identity: str,
        path: Path | None,
        *,
        saved: bool = False,
        duplicate: bool = False,
        invalid: bool = False,
        integrity_error: bool = False,
        write_failed: bool = False,
        error: str = "",
        payload: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "saved": saved,
            "duplicate": duplicate,
            "invalid": invalid,
            "integrity_error": integrity_error,
            "write_failed": write_failed,
            "input_window_hash": identity,
            "path": str(path) if path is not None else "",
            "payload": payload,
            "error": error,
        }
