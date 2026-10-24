"""Bounded, best-effort persistence of direct M3 calculation-only outputs."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from queue import Queue
from threading import Lock, Thread
from typing import Any

from candle_timeframe_aggregation import SUPPORTED_BAR_MINUTES
from sor_routine_signal_evidence import _SOURCE_PROOF, _valid_cutoff, _valid_stock

_PATH = Path(__file__).resolve().parent / "runtime" / "diagnostics" / "sor_m3_production_outputs.jsonl"
_DIRECT_PATH = Path(__file__).resolve().parent / "runtime" / "diagnostics" / "sor_direct_production_outputs.jsonl"
_MAX_BYTES = 4 * 1024 * 1024
_QUEUE: Queue[tuple[Path, str]] = Queue(maxsize=256)
_LOCK = Lock()
_STARTED = False
_SEEN: dict[tuple[object, ...], None] = {}
_MARKER_KEYS = (
    "stock", "timeframe", "rules_hash", "evaluation_time",
    "market_data_identity", "source_proof", "input_hash", "side",
    "source_identity", "receipt_hash", "source_session_sha256",
    "source_connection_epoch",
)


def _hex(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value)


def _safe_record(output: Any) -> dict[str, Any] | None:
    if not isinstance(output, dict):
        return None
    marker = output.get("sor_preview_evidence")
    if (not _valid_stock(output.get("code")) or output.get("signal") != "SKIP"
            or output.get("reason") != "SOR_CALCULATION_ONLY_NO_ORDER"
            or any(output.get(key) is not False for key in
                   ("execution_enabled", "queue_connected", "send_order_connected"))
            or not isinstance(marker, dict)
            or marker.get("stock") != output["code"]
            or marker.get("timeframe") not in {
                f"M{minutes}" for minutes in SUPPORTED_BAR_MINUTES if minutes > 1}
            or marker.get("market_data_identity") != f"{output['code']}_AL"
            or marker.get("source_proof") != _SOURCE_PROOF
            or marker.get("side") not in {"BUY", "SELL", "NONE"}
            or not _valid_cutoff(marker.get("evaluation_time"))
            or any(not _hex(marker.get(key)) for key in
                   ("rules_hash", "input_hash", "source_identity", "receipt_hash",
                    "source_session_sha256"))
            or type(marker.get("source_connection_epoch")) is not int
            or marker["source_connection_epoch"] <= 0
            or not _hex(output.get("sor_preview_cycle_hash"))
            or not isinstance(output.get("sor_preview_routine_instance_id"), str)
            or not output["sor_preview_routine_instance_id"]
            or not isinstance(output.get("sor_preview_observed_at"), str)
            or str(output.get("sor_preview_signal") or "NONE").upper() != marker["side"]):
        return None
    return {
        "origin": "PRODUCTION", "code": output["code"], "signal": "SKIP",
        "reason": "SOR_CALCULATION_ONLY_NO_ORDER",
        "execution_enabled": False, "queue_connected": False,
        "send_order_connected": False,
        "sor_preview_signal": marker["side"],
        "sor_preview_evidence": {key: marker[key] for key in _MARKER_KEYS},
        "sor_preview_cycle_hash": output["sor_preview_cycle_hash"],
        "sor_preview_routine_instance_sha256": hashlib.sha256(
            output["sor_preview_routine_instance_id"].encode("utf-8")).hexdigest(),
        "sor_preview_cycle_status": (
            output.get("sor_preview_cycle_status")
            if output.get("sor_preview_cycle_status") in {"resolved", "unresolved"}
            else "UNKNOWN"),
        "sor_preview_observed_at": output["sor_preview_observed_at"],
    }


def _write_loop() -> None:
    while True:
        path, line = _QUEUE.get()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                size = 0
            if size + len(line.encode("utf-8")) <= _MAX_BYTES:
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(line)
        except Exception:
            pass  # Evidence failure cannot affect the no-order probe.
        finally:
            _QUEUE.task_done()


def enqueue_line(path: Path, line: str) -> bool:
    """Share the bounded background writer with other M3 evidence routes."""
    global _STARTED
    try:
        with _LOCK:
            if not _STARTED:
                Thread(target=_write_loop, name="sor-m3-production-evidence", daemon=True).start()
                _STARTED = True
            _QUEUE.put_nowait((path, line))
        return True
    except Exception:
        return False


def enqueue_preview(output: Any) -> bool:
    """Queue only a sanitized marker; failures silently drop optional evidence."""
    global _STARTED
    try:
        record = _safe_record(output)
        if record is None:
            return False
        marker = record["sor_preview_evidence"]
        key = (record["code"], record["sor_preview_routine_instance_sha256"],
               marker["timeframe"], marker["evaluation_time"],
               marker["source_identity"], marker["rules_hash"],
               record["sor_preview_cycle_hash"])
        line = json.dumps(record, ensure_ascii=True, sort_keys=True,
                          separators=(",", ":"), allow_nan=False) + "\n"
        with _LOCK:
            if key in _SEEN:
                return False
            if not _STARTED:
                Thread(target=_write_loop, name="sor-m3-production-evidence", daemon=True).start()
                _STARTED = True
            _QUEUE.put_nowait(((_PATH if marker["timeframe"] == "M3"
                              else _DIRECT_PATH), line))
            _SEEN[key] = None
            if len(_SEEN) > 256:
                del _SEEN[next(iter(_SEEN))]
        return True
    except Exception:
        return False
