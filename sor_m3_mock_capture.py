"""Best-effort direct M3 capture of newly appended Mock Validation events."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any

from sor_m3_operational_evidence import _marker, _qualified_time
from sor_m3_production_capture import _MARKER_KEYS, _QUEUE, _hex, enqueue_line

_PATH = Path(__file__).resolve().parent / "runtime" / "diagnostics" / "sor_m3_mock_events.jsonl"
_DIRECT_PATH = Path(__file__).resolve().parent / "runtime" / "diagnostics" / "sor_direct_mock_events.jsonl"
_LOCK = Lock()
_SEEN: dict[str, None] = {}


def enqueue_event(event: Any, *, cycle_identity: Any = None,
                  cycle_status: Any = None) -> bool:
    """Capture only a persisted, source-bound decision; never delay evaluation on I/O."""
    try:
        if not isinstance(event, dict) or event.get("event_type") != "ROUTINE_EVALUATED":
            return False
        payload = event.get("payload")
        marker = payload.get("sor_source_evidence") if isinstance(payload, dict) else None
        event_instant = _qualified_time(event.get("timestamp"))
        event_time = event_instant.strftime("%Y%m%d%H%M%S") if event_instant else None
        side = payload.get("signal") if isinstance(payload, dict) else None
        activation_time = payload.get("signal_bar_time") if side in {"BUY", "SELL"} else None
        activated = _qualified_time(activation_time)
        if (not isinstance(marker, dict)
                or not _marker(marker, event_time or "",
                               timeframe=marker.get("timeframe"))
                or any(key not in marker for key in _MARKER_KEYS)
                or not _hex(marker.get("source_session_sha256"))
                or type(marker.get("source_connection_epoch")) is not int
                or marker["source_connection_epoch"] <= 0
                or not isinstance(event.get("event_id"), str)
                or not event["event_id"].startswith("ME-")
                or not isinstance(event.get("validation_session_id"), str)
                or not event["validation_session_id"].startswith("MV-")
                or event.get("stock_code") != marker["stock"]
                or not isinstance(event.get("routine_instance_id"), str)
                or not event["routine_instance_id"]
                or payload.get("rules_hash") != marker["rules_hash"]
                or payload.get("signal") != marker["side"]
                or not isinstance(payload.get("evaluation_cycle_id"), str)
                or not payload["evaluation_cycle_id"].startswith("MC-")
                or not payload.get("operation_identity")
                or (side in {"BUY", "SELL"} and (not activated or activated > event_instant))
                or (side == "NONE" and payload.get("signal_bar_time") is not None)):
            return False
        record = {
            "origin": "MOCK", "event_id": event["event_id"],
            "validation_session_id": event["validation_session_id"],
            "stock_code": event["stock_code"],
            "routine_instance_id": event["routine_instance_id"],
            "event_type": event["event_type"], "timestamp": event["timestamp"],
            "activation_time": activation_time,
            "payload": {"signal": payload["signal"], "rules_hash": payload["rules_hash"],
                        **({"signal_bar_time": activation_time} if activated else {}),
                        "evaluation_cycle_id": payload["evaluation_cycle_id"],
                        "operation_identity": payload["operation_identity"],
                        "sor_source_evidence": {key: marker[key] for key in _MARKER_KEYS}},
        }
        if isinstance(cycle_identity, str) and cycle_identity:
            record["cycle_identity"] = cycle_identity
        if cycle_status in {"resolved", "unresolved"}:
            record["cycle_status"] = cycle_status
        line = json.dumps(record, ensure_ascii=True, sort_keys=True,
                          separators=(",", ":"), allow_nan=False) + "\n"
        with _LOCK:
            if record["event_id"] in _SEEN:
                return False
            path = _PATH if marker["timeframe"] == "M3" else _DIRECT_PATH
            if not enqueue_line(path, line):
                return False
            _SEEN[record["event_id"]] = None
            if len(_SEEN) > 256:
                del _SEEN[next(iter(_SEEN))]
        return True
    except Exception:
        return False
