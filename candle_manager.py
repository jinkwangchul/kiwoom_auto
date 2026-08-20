# -*- coding: utf-8 -*-
"""Minimal candle file helpers for routine probe tests.

This module only reads and writes stocks/<stock>/candles.json. It does not
connect to Kiwoom, orders, rules, or the routine engine.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any


CANDLES_FILENAME = "candles.json"
DEFAULT_CANDLES_MAX_COUNT = 600

_CANDLE_LOCKS: dict[str, threading.RLock] = {}
_CANDLE_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True)
class CanonicalCandleFileCommitResult:
    ok: bool
    changed: bool
    readback_verified: bool
    path: str
    saved_count: int
    canonical_content_hash: str
    error_kind: str = ""
    error: str = ""
    previous_content_hash: str = ""


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def validate_candle(candle: Any) -> bool:
    """Return True when candle is a dict with a numeric close value."""
    if not isinstance(candle, dict):
        return False
    close_value = _safe_float(candle.get("close"))
    return close_value is not None


def _normalize_candles(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = value.get("candles")
    if not isinstance(value, list):
        return []
    return [item for item in value if validate_candle(item)]


def canonical_candle_content_hash(candles: list[dict[str, Any]]) -> str:
    payload = json.dumps(
        candles,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _candles_path(stock_dir: str | Path) -> Path:
    return Path(stock_dir) / CANDLES_FILENAME


def _lock_for_path(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _CANDLE_LOCKS_GUARD:
        lock = _CANDLE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _CANDLE_LOCKS[key] = lock
        return lock


@contextmanager
def candle_commit_lock(stock_dir: str | Path):
    """Serialize one stock's read/merge/commit sequence within this process."""
    lock = _lock_for_path(_candles_path(stock_dir))
    with lock:
        yield


def _read_candles_strict(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("candles")
    if not isinstance(data, list):
        raise ValueError("canonical candle file must contain a JSON array")
    normalized = _normalize_candles(data)
    if len(normalized) != len(data):
        raise ValueError("canonical candle file contains invalid candles")
    return normalized


def _write_temp_payload(directory: Path, payload: str) -> Path:
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{CANDLES_FILENAME}.",
        suffix=".tmp",
        dir=str(directory),
    )
    temp_path = Path(raw_path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return temp_path


def commit_candles(
    stock_dir: str | Path,
    candles: list[dict[str, Any]],
    max_count: int = DEFAULT_CANDLES_MAX_COUNT,
) -> CanonicalCandleFileCommitResult:
    """Atomically commit and verify the canonical candles.json state."""
    path = _candles_path(stock_dir)
    clean_candles = _normalize_candles(candles)
    try:
        limit = max(int(max_count), 0)
    except (TypeError, ValueError):
        limit = DEFAULT_CANDLES_MAX_COUNT
    if limit:
        clean_candles = clean_candles[-limit:]

    expected_hash = canonical_candle_content_hash(clean_candles)
    payload = json.dumps(clean_candles, ensure_ascii=False, indent=2) + "\n"
    previous_hash = ""
    temp_path: Path | None = None

    with candle_commit_lock(stock_dir):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            return CanonicalCandleFileCommitResult(
                ok=False,
                changed=False,
                readback_verified=False,
                path=str(path),
                saved_count=0,
                canonical_content_hash="",
                error_kind="CANDLE_TEMP_WRITE_FAILED",
                error=str(exc),
            )
        if path.exists():
            try:
                previous = _read_candles_strict(path)
                previous_hash = canonical_candle_content_hash(previous)
                if previous == clean_candles and previous_hash == expected_hash:
                    return CanonicalCandleFileCommitResult(
                        ok=True,
                        changed=False,
                        readback_verified=True,
                        path=str(path),
                        saved_count=len(clean_candles),
                        canonical_content_hash=expected_hash,
                        previous_content_hash=previous_hash,
                    )
            except Exception:
                previous_hash = ""

        try:
            temp_path = _write_temp_payload(path.parent, payload)
        except Exception as exc:
            return CanonicalCandleFileCommitResult(
                ok=False,
                changed=False,
                readback_verified=False,
                path=str(path),
                saved_count=0,
                canonical_content_hash="",
                error_kind="CANDLE_TEMP_WRITE_FAILED",
                error=str(exc),
                previous_content_hash=previous_hash,
            )

        try:
            temp_candles = _read_candles_strict(temp_path)
            if temp_candles != clean_candles or canonical_candle_content_hash(temp_candles) != expected_hash:
                raise ValueError("temporary candle payload verification mismatch")
        except Exception as exc:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            return CanonicalCandleFileCommitResult(
                ok=False,
                changed=False,
                readback_verified=False,
                path=str(path),
                saved_count=0,
                canonical_content_hash="",
                error_kind="CANDLE_TEMP_VERIFY_FAILED",
                error=str(exc),
                previous_content_hash=previous_hash,
            )

        try:
            os.replace(temp_path, path)
            temp_path = None
        except Exception as exc:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            return CanonicalCandleFileCommitResult(
                ok=False,
                changed=False,
                readback_verified=False,
                path=str(path),
                saved_count=0,
                canonical_content_hash="",
                error_kind="CANDLE_REPLACE_FAILED",
                error=str(exc),
                previous_content_hash=previous_hash,
            )

        try:
            readback = _read_candles_strict(path)
            readback_hash = canonical_candle_content_hash(readback)
            if readback != clean_candles or readback_hash != expected_hash:
                raise ValueError("final candle read-back verification mismatch")
        except Exception as exc:
            return CanonicalCandleFileCommitResult(
                ok=False,
                changed=True,
                readback_verified=False,
                path=str(path),
                saved_count=0,
                canonical_content_hash="",
                error_kind="CANDLE_FINAL_VERIFY_FAILED",
                error=str(exc),
                previous_content_hash=previous_hash,
            )

        return CanonicalCandleFileCommitResult(
            ok=True,
            changed=True,
            readback_verified=True,
            path=str(path),
            saved_count=len(readback),
            canonical_content_hash=readback_hash,
            previous_content_hash=previous_hash,
        )


def load_candles(stock_dir: str | Path) -> list[dict[str, Any]]:
    """Load candles from stock_dir/candles.json.

    Accepted file shapes:
    - [{...}, {...}]
    - {"candles": [{...}, {...}]}
    """
    path = _candles_path(stock_dir)
    try:
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return _normalize_candles(data)


def save_candles(
    stock_dir: str | Path,
    candles: list[dict[str, Any]],
    max_count: int = DEFAULT_CANDLES_MAX_COUNT,
) -> list[dict[str, Any]]:
    """Atomically save validated candles and preserve the list-return contract."""
    with candle_commit_lock(stock_dir):
        result = commit_candles(stock_dir, candles, max_count=max_count)
        if not result.ok or not result.readback_verified:
            raise OSError(result.error or result.error_kind or "candle commit failed")
        return load_candles(stock_dir)


def append_candle(
    stock_dir: str | Path,
    candle: dict[str, Any],
    max_count: int = DEFAULT_CANDLES_MAX_COUNT,
) -> list[dict[str, Any]]:
    """Append one valid candle, save, and return the saved candle list."""
    if not validate_candle(candle):
        return load_candles(stock_dir)
    with candle_commit_lock(stock_dir):
        candles = load_candles(stock_dir)
        candles.append(candle)
        return save_candles(stock_dir, candles, max_count=max_count)
