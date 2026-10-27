# -*- coding: utf-8 -*-
"""Opt-in runtime trace, with unconditional V5 SOR error diagnostics."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import Lock, current_thread
import time
from typing import Any

_TRACE_ENV = "KIWOOM_SIGNAL_VALIDATION_TRACE"
_TRACE_PATH = Path(__file__).resolve().parent / "runtime" / "signal_validation_runtime_trace.jsonl"
_TRACE_LOCK = Lock()
_POOL_CACHE_FIELDS = (
    "production_detail_replay_cache",
    "full_parity_entries_by_key",
    "production_presentation_cache",
    "production_projected_view_cache",
)


def signal_validation_trace_enabled() -> bool:
    return str(os.getenv(_TRACE_ENV, "") or "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def trace_signal_validation_event(event: str, **payload: Any) -> None:
    if event not in {"v5_sor_block", "v5_sor_historical_error"} and not signal_validation_trace_enabled():
        return
    record = {
        "wall_time": datetime.now(timezone.utc).astimezone().isoformat(),
        "monotonic": time.monotonic(),
        "pid": os.getpid(),
        "thread": current_thread().name,
        "event": str(event),
        **payload,
    }
    try:
        serialized = json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        with _TRACE_LOCK:
            _TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with _TRACE_PATH.open("a", encoding="utf-8", newline="") as handle:
                handle.write(serialized)
                handle.write("\n")
    except Exception:
        # Diagnostics must never change validation behavior.
        return


def trace_candle_signature(candles: object) -> dict[str, Any]:
    if not isinstance(candles, list):
        return {"count": None, "first_time": "", "last_time": ""}
    first_time = ""
    last_time = ""
    if candles:
        first = candles[0]
        last = candles[-1]
        if isinstance(first, dict):
            first_time = str(first.get("time") or "")
        if isinstance(last, dict):
            last_time = str(last.get("time") or "")
    return {
        "count": len(candles),
        "first_time": first_time,
        "last_time": last_time,
    }


def trace_pool_summary(
    pool: object,
    *,
    source_identity: object = None,
) -> dict[str, Any]:
    """Return bounded pool metadata without copying or exposing pool content."""
    if not isinstance(pool, dict):
        return {"present": False}
    contract = pool.get("production_session_contract")
    return {
        "present": True,
        "request_id": str(pool.get("request_id") or ""),
        "timeframe": str(pool.get("timeframe_key") or ""),
        "requested_count": pool.get("requested_count"),
        "candles": trace_candle_signature(pool.get("candles")),
        "cache_fields": {field: field in pool for field in _POOL_CACHE_FIELDS},
        "production_contract": trace_production_contract_summary(contract),
        "source_identity": {
            "present": source_identity is not None,
            "is_tuple": isinstance(source_identity, tuple),
            "item_count": len(source_identity)
            if isinstance(source_identity, tuple)
            else 0,
        },
    }


def trace_production_contract_summary(contract: object) -> dict[str, Any]:
    """Return only routing-relevant production-session contract metadata."""
    selected_ats = contract.get("selected_ats") if isinstance(contract, dict) else None
    session_windows = (
        contract.get("session_windows") if isinstance(contract, dict) else None
    )
    return {
        "status": str(contract.get("status") or "")
        if isinstance(contract, dict)
        else "",
        "ready": contract.get("ready") if isinstance(contract, dict) else None,
        "selected_ats_count": len(selected_ats)
        if isinstance(selected_ats, (list, tuple, set))
        else 0,
        "session_windows_count": len(session_windows)
        if isinstance(session_windows, (list, tuple))
        else 0,
    }
