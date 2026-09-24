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
from threading import RLock
import uuid

from indicator_follow_validation_timeframe import normalize_validation_timeframe


SCHEMA_VERSION = 1
CACHE_STATE_SCHEMA_VERSION = 1
DEFAULT_CACHE_ROOT = (
    Path(__file__).resolve().parent
    / "runtime"
    / "signal_validation_v2_history_cache"
)
_CANDLE_FIELDS = ("time", "open", "high", "low", "close", "volume")
_CACHE_WRITE_LOCK = RLock()
_CACHE_STATE_ACTIVE = "ACTIVE"
_CACHE_STATE_INVALIDATED = "INVALIDATED"


@dataclass(frozen=True, slots=True)
class ValidationHistoricalCacheEntry:
    stock_code: str
    stock_name: str
    timeframe_minutes: int
    timeframe_key: str
    requested_count: int
    candles: tuple[dict[str, object], ...]
    updated_at: str
    market_data_identity: str = ""
    market_source: str = ""


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
        timeframe_key: object | None = None,
    ) -> tuple[str, int, str, int]:
        code = str(stock_code or "").strip()
        if not code:
            raise ValueError("stock_code is required")
        for name, value in (
            ("timeframe_minutes", timeframe_minutes),
            ("requested_count", requested_count),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        timeframe = normalize_validation_timeframe(
            timeframe_key or timeframe_minutes,
            fallback_minutes=int(timeframe_minutes),
        )
        return code, int(timeframe_minutes), str(timeframe["key"]), int(requested_count)

    def path_for(
        self,
        stock_code: object,
        timeframe_minutes: object,
        requested_count: object,
        timeframe_key: object | None = None,
    ) -> Path:
        code, timeframe, key, count = self._identity(
            stock_code,
            timeframe_minutes,
            requested_count,
            timeframe_key,
        )
        safe_code = re.sub(r"[^A-Za-z0-9_-]+", "_", code).strip("_") or "stock"
        token = str(timeframe) if key == f"M{timeframe}" else key
        return self.root / f"{safe_code}_{token}_{count}.json"

    def state_path_for(self, stock_code: object) -> Path:
        code = str(stock_code or "").strip()
        if not code:
            raise ValueError("stock_code is required")
        safe_code = re.sub(r"[^A-Za-z0-9_-]+", "_", code).strip("_") or "stock"
        return self.root / f".{safe_code}.cache-state.json"

    def _read_cache_state_unlocked(self, stock_code: str) -> dict[str, str] | None:
        path = self.state_path_for(stock_code)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return {"state": _CACHE_STATE_INVALIDATED, "generation": ""}
        if not isinstance(payload, dict):
            return {"state": _CACHE_STATE_INVALIDATED, "generation": ""}
        state = str(payload.get("state") or "").strip()
        generation = str(payload.get("generation") or "").strip()
        if (
            payload.get("schema_version") != CACHE_STATE_SCHEMA_VERSION
            or str(payload.get("stock_code") or "").strip() != stock_code
            or state not in {_CACHE_STATE_ACTIVE, _CACHE_STATE_INVALIDATED}
            or not generation
        ):
            return {"state": _CACHE_STATE_INVALIDATED, "generation": ""}
        return {"state": state, "generation": generation}

    def _write_cache_state_unlocked(
        self,
        stock_code: str,
        *,
        state: str,
        generation: str,
    ) -> bool:
        if state not in {_CACHE_STATE_ACTIVE, _CACHE_STATE_INVALIDATED}:
            return False
        clean_generation = str(generation or "").strip()
        if not clean_generation:
            return False
        path = self.state_path_for(stock_code)
        temp_path: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            serialized = json.dumps(
                {
                    "schema_version": CACHE_STATE_SCHEMA_VERSION,
                    "stock_code": stock_code,
                    "state": state,
                    "generation": clean_generation,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
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
            persisted = json.loads(path.read_text(encoding="utf-8"))
            return (
                isinstance(persisted, dict)
                and persisted.get("schema_version") == CACHE_STATE_SCHEMA_VERSION
                and persisted.get("stock_code") == stock_code
                and persisted.get("state") == state
                and persisted.get("generation") == clean_generation
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return False
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink()
                except OSError:
                    pass

    def _payload_is_current_unlocked(
        self,
        stock_code: str,
        payload: object,
    ) -> bool:
        state = self._read_cache_state_unlocked(stock_code)
        if state is None:
            return True
        return (
            state["state"] == _CACHE_STATE_ACTIVE
            and bool(state["generation"])
            and isinstance(payload, dict)
            and str(payload.get("cache_generation") or "").strip()
            == state["generation"]
        )

    def delete_stock(self, stock_code: object) -> dict[str, object]:
        """Delete every Validation cache artifact owned by one exact safe code."""
        code = str(stock_code or "").strip()
        if not code:
            return {
                "ok": False,
                "stock_code": "",
                "deleted_count": 0,
                "deleted_paths": (),
                "failed_paths": (),
                "invalidated": False,
                "reason_code": "STOCK_CODE_REQUIRED",
            }
        safe_code = re.sub(r"[^A-Za-z0-9_-]+", "_", code).strip("_") or "stock"
        deleted: list[str] = []
        failed: list[str] = []
        with _CACHE_WRITE_LOCK:
            generation = uuid.uuid4().hex
            invalidated = self._write_cache_state_unlocked(
                code,
                state=_CACHE_STATE_INVALIDATED,
                generation=generation,
            )
            try:
                root = self.root.resolve()
            except OSError:
                root = self.root.absolute()
            if not root.exists():
                return {
                    "ok": True,
                    "stock_code": code,
                    "deleted_count": 0,
                    "deleted_paths": (),
                    "failed_paths": (),
                    "invalidated": invalidated,
                    "reason_code": "NO_CACHE_ARTIFACTS",
                }
            artifact_pattern = re.compile(
                rf"{re.escape(safe_code)}_"
                r"(?:1|3|5|10|15|30|60|120|240|D1|W1|MO1|Y1)_"
                r"[1-9][0-9]*\.json(?:\.market-source\.json)?"
            )
            for candidate in sorted(root.glob(f"{safe_code}_*.json")):
                if artifact_pattern.fullmatch(candidate.name) is None:
                    continue
                try:
                    resolved = candidate.resolve()
                    resolved.relative_to(root)
                    if resolved.parent != root or not resolved.is_file():
                        continue
                    resolved.unlink()
                    deleted.append(str(resolved))
                except (OSError, ValueError):
                    failed.append(str(candidate))
        return {
            "ok": not failed,
            "stock_code": code,
            "deleted_count": len(deleted),
            "deleted_paths": tuple(deleted),
            "failed_paths": tuple(failed),
            "invalidated": invalidated,
            "reason_code": "CACHE_DELETE_FAILED" if failed else (
                "CACHE_DELETED" if deleted else "NO_CACHE_ARTIFACTS"
            ),
        }

    def load(
        self,
        stock_code: object,
        timeframe_minutes: object,
        requested_count: object,
        timeframe_key: object | None = None,
    ) -> ValidationHistoricalCacheEntry | None:
        with _CACHE_WRITE_LOCK:
            try:
                code, timeframe, key, count = self._identity(
                    stock_code,
                    timeframe_minutes,
                    requested_count,
                    timeframe_key,
                )
                path = self.path_for(code, timeframe, count, key)
                payload = json.loads(path.read_text(encoding="utf-8"))
                if not self._payload_is_current_unlocked(code, payload):
                    return None
                return self._entry_from_payload(
                    payload,
                    expected_identity=(code, timeframe, key, count),
                )
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                return None

    def load_covering(
        self,
        stock_code: object,
        timeframe_minutes: object,
        minimum_count: object,
        timeframe_key: object | None = None,
    ) -> ValidationHistoricalCacheEntry | None:
        """Return the smallest valid cache entry that covers minimum_count."""
        with _CACHE_WRITE_LOCK:
            try:
                code, timeframe, key, count = self._identity(
                    stock_code,
                    timeframe_minutes,
                    minimum_count,
                    timeframe_key,
                )
                exact = self.load(code, timeframe, count, key)
                if exact is not None:
                    return exact
                safe_code = re.sub(r"[^A-Za-z0-9_-]+", "_", code).strip("_") or "stock"
                token = str(timeframe) if key == f"M{timeframe}" else key
                pattern = re.compile(
                    rf"{re.escape(safe_code)}_{re.escape(token)}_([1-9][0-9]*)\.json"
                )
                candidates: list[tuple[int, Path]] = []
                if not self.root.exists():
                    return None
                for path in self.root.glob(f"{safe_code}_{token}_*.json"):
                    match = pattern.fullmatch(path.name)
                    if match is None:
                        continue
                    candidate_count = int(match.group(1))
                    if candidate_count >= count:
                        candidates.append((candidate_count, path))
                for candidate_count, path in sorted(candidates):
                    try:
                        payload = json.loads(path.read_text(encoding="utf-8"))
                        entry = (
                            self._entry_from_payload(
                                payload,
                                expected_identity=(
                                    code,
                                    timeframe,
                                    key,
                                    candidate_count,
                                ),
                            )
                            if self._payload_is_current_unlocked(code, payload)
                            else None
                        )
                    except (OSError, TypeError, ValueError, json.JSONDecodeError):
                        entry = None
                    if entry is not None:
                        return entry
            except (OSError, TypeError, ValueError):
                return None
        return None

    def store(
        self,
        *,
        stock_code: object,
        stock_name: object,
        timeframe_minutes: object,
        requested_count: object,
        candles: object,
        timeframe_key: object | None = None,
        market_data_identity: object = "",
        market_source: object = "",
    ) -> bool:
        with _CACHE_WRITE_LOCK:
            return self._store_unlocked(
                stock_code=stock_code,
                stock_name=stock_name,
                timeframe_minutes=timeframe_minutes,
                requested_count=requested_count,
                candles=candles,
                timeframe_key=timeframe_key,
                market_data_identity=market_data_identity,
                market_source=market_source,
            )

    def _store_unlocked(
        self,
        *,
        stock_code: object,
        stock_name: object,
        timeframe_minutes: object,
        requested_count: object,
        candles: object,
        timeframe_key: object | None = None,
        market_data_identity: object = "",
        market_source: object = "",
    ) -> bool:
        temp_path: Path | None = None
        try:
            code, timeframe, key, count = self._identity(
                stock_code,
                timeframe_minutes,
                requested_count,
                timeframe_key,
            )
            normalized = merge_validation_candles([], candles, count)
            if not normalized:
                return False
            state = self._read_cache_state_unlocked(code)
            generation = ""
            activate_after_store = False
            if state is not None:
                generation = state["generation"] or uuid.uuid4().hex
                activate_after_store = state["state"] != _CACHE_STATE_ACTIVE
                if activate_after_store and not self._write_cache_state_unlocked(
                    code,
                    state=_CACHE_STATE_INVALIDATED,
                    generation=generation,
                ):
                    return False
            payload = {
                "schema_version": SCHEMA_VERSION,
                "stock": {
                    "code": code,
                    "name": str(stock_name or "").strip(),
                },
                "timeframe_minutes": timeframe,
                "timeframe_key": key,
                "requested_count": count,
                "candles": normalized,
                "market_data_identity": str(market_data_identity or "").strip(),
                "market_source": str(market_source or "").strip(),
                "signal_entries_by_settings_hash": {},
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            if generation:
                payload["cache_generation"] = generation
            path = self.path_for(code, timeframe, count, key)
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
            persisted_payload = json.loads(path.read_text(encoding="utf-8"))
            persisted = self._entry_from_payload(
                persisted_payload,
                expected_identity=(code, timeframe, key, count),
            )
            if persisted is None or list(persisted.candles) != normalized:
                return False
            if generation and str(
                persisted_payload.get("cache_generation") or ""
            ).strip() != generation:
                return False
            if activate_after_store and not self._write_cache_state_unlocked(
                code,
                state=_CACHE_STATE_ACTIVE,
                generation=generation,
            ):
                return False
            return self.load(code, timeframe, count, key) is not None
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return False
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink()
                except OSError:
                    pass

    def load_signal_entries(
        self,
        stock_code: object,
        timeframe_minutes: object,
        requested_count: object,
        settings_hash: object,
        timeframe_key: object | None = None,
    ) -> list[dict[str, object]] | None:
        with _CACHE_WRITE_LOCK:
            try:
                code, timeframe, key, count = self._identity(
                    stock_code,
                    timeframe_minutes,
                    requested_count,
                    timeframe_key,
                )
                clean_hash = str(settings_hash or "").strip()
                if not clean_hash:
                    return None
                path = self.path_for(code, timeframe, count, key)
                payload = json.loads(path.read_text(encoding="utf-8"))
                if not self._payload_is_current_unlocked(code, payload):
                    return None
                if self._entry_from_payload(
                    payload,
                    expected_identity=(code, timeframe, key, count),
                ) is None:
                    return None
                by_hash = payload.get("signal_entries_by_settings_hash")
                if not isinstance(by_hash, dict):
                    return None
                entries = by_hash.get(clean_hash)
                if not isinstance(entries, list) or any(
                    not isinstance(entry, dict) for entry in entries
                ):
                    return None
                return json.loads(json.dumps(
                    entries,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ))
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                return None

    def store_signal_entries(
        self,
        *,
        stock_code: object,
        timeframe_minutes: object,
        requested_count: object,
        settings_hash: object,
        entries: object,
        timeframe_key: object | None = None,
    ) -> bool:
        with _CACHE_WRITE_LOCK:
            return self._store_signal_entries_unlocked(
                stock_code=stock_code,
                timeframe_minutes=timeframe_minutes,
                requested_count=requested_count,
                settings_hash=settings_hash,
                entries=entries,
                timeframe_key=timeframe_key,
            )

    def _store_signal_entries_unlocked(
        self,
        *,
        stock_code: object,
        timeframe_minutes: object,
        requested_count: object,
        settings_hash: object,
        entries: object,
        timeframe_key: object | None = None,
    ) -> bool:
        temp_path: Path | None = None
        try:
            code, timeframe, key, count = self._identity(
                stock_code,
                timeframe_minutes,
                requested_count,
                timeframe_key,
            )
            clean_hash = str(settings_hash or "").strip()
            if (
                not clean_hash
                or not isinstance(entries, (list, tuple))
                or any(not isinstance(entry, dict) for entry in entries)
            ):
                return False
            normalized_entries = json.loads(json.dumps(
                list(entries),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ))
            path = self.path_for(code, timeframe, count, key)
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not self._payload_is_current_unlocked(code, payload):
                return False
            if self._entry_from_payload(
                payload,
                expected_identity=(code, timeframe, key, count),
            ) is None:
                return False
            by_hash = payload.get("signal_entries_by_settings_hash")
            if not isinstance(by_hash, dict):
                by_hash = {}
            else:
                by_hash = dict(by_hash)
            by_hash[clean_hash] = normalized_entries
            payload["signal_entries_by_settings_hash"] = by_hash
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
            return self.load_signal_entries(
                code,
                timeframe,
                count,
                clean_hash,
                key,
            ) == normalized_entries
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
        expected_identity: tuple[str, int, str, int],
    ) -> ValidationHistoricalCacheEntry | None:
        if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
            return None
        stock = payload.get("stock")
        if not isinstance(stock, dict):
            return None
        stock_name = stock.get("name")
        if not isinstance(stock_name, str):
            return None
        payload_minutes = payload.get("timeframe_minutes")
        payload_key = str(
            payload.get("timeframe_key")
            or (
                f"M{payload_minutes}"
                if isinstance(payload_minutes, int) and not isinstance(payload_minutes, bool)
                else ""
            )
        )
        identity = (
            stock.get("code"),
            payload_key,
            payload.get("requested_count"),
        )
        expected_cache_identity = (
            expected_identity[0],
            expected_identity[2],
            expected_identity[3],
        )
        if identity != expected_cache_identity:
            return None
        if (
            not isinstance(payload_minutes, int)
            or isinstance(payload_minutes, bool)
            or payload_minutes <= 0
        ):
            return None
        if payload_key.startswith("M") and payload_minutes != expected_identity[1]:
            return None
        raw_candles = payload.get("candles")
        if not isinstance(raw_candles, list) or not raw_candles:
            return None
        candles = merge_validation_candles([], raw_candles, expected_identity[3])
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
            timeframe_key=expected_identity[2],
            requested_count=expected_identity[3],
            candles=tuple(dict(candle) for candle in candles),
            updated_at=updated_at,
            market_data_identity=str(
                payload.get("market_data_identity") or ""
            ).strip(),
            market_source=str(payload.get("market_source") or "").strip(),
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
