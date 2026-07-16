# -*- coding: utf-8 -*-
"""Record PARTIAL_FILL/FULL_FILL events to an explicit fills file.

This module only appends fill ledger records to the provided fill_path. It does
not update positions, order_queue status, GUI flows, timers, Chejan handlers, or
broker order APIs.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import shutil
import threading
import time
from typing import Any
from uuid import uuid4


NEXT_STAGE_BLOCKED = "BLOCKED"
NEXT_STAGE_POSITION_UPDATE_REQUIRED = "POSITION_UPDATE_REQUIRED"
CHEJAN_EVENT_NEXT_STAGE_REQUIRED = "FILL_RECORD_REQUIRED"
_FILL_EVENT_TYPES = {"PARTIAL_FILL", "FULL_FILL"}
_FILL_THREAD_LOCK = threading.RLock()
_LOCK_POLL_SECONDS = 0.02
_DEFAULT_LOCK_TIMEOUT_SECONDS = 5.0


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _blocked(stage: str, reason: str) -> dict[str, Any]:
    return {
        "fill_recorded": False,
        "fill_stage": stage,
        "next_stage": NEXT_STAGE_BLOCKED,
        "changed": False,
        "file_write": False,
        "fill_write": False,
        "fill_committed": False,
        "post_write_verified": False,
        "lock_acquired": False,
        "lock_wait_ms": 0,
        "blocked_reasons": [reason],
        "warnings": [],
    }


def _confirmed(context: Any) -> bool:
    ctx = _as_dict(context)
    if ctx.get("manual_fill_record_confirmed") is True:
        return True
    return (
        ctx.get("kiwoom_api_live_event") is True
        and _clean_text(ctx.get("live_event_source")) == "KiwoomApi.raw_chejan_received"
    )


def _snapshot_sha256(snapshot: Any) -> str:
    return _clean_text(_as_dict(snapshot).get("sha256")).upper()


def _lock_timeout_sec(context: Any) -> float:
    value = _as_dict(context).get("fill_lock_timeout_sec")
    if isinstance(value, bool):
        return _DEFAULT_LOCK_TIMEOUT_SECONDS
    if isinstance(value, (int, float)) and value >= 0:
        return float(value)
    return _DEFAULT_LOCK_TIMEOUT_SECONDS


class _FillFileLock:
    def __init__(self, fill_path: Path, timeout_sec: float) -> None:
        self.lock_path = fill_path.with_name(f"{fill_path.name}.lock")
        self.timeout_sec = timeout_sec
        self.handle: Any = None
        self.wait_ms = 0

    def __enter__(self) -> "_FillFileLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.lock_path.open("a+b")
        started = time.monotonic()
        while True:
            try:
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                self.wait_ms = int((time.monotonic() - started) * 1000)
                return self
            except OSError:
                if time.monotonic() - started >= self.timeout_sec:
                    self.wait_ms = int((time.monotonic() - started) * 1000)
                    self.handle.close()
                    self.handle = None
                    raise TimeoutError("fills lock timeout")
                time.sleep(_LOCK_POLL_SECONDS)

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self.handle.close()
            self.handle = None


def _with_lock_metadata(result: dict[str, Any], *, lock_acquired: bool, lock_wait_ms: int = 0) -> dict[str, Any]:
    updated = deepcopy(result)
    updated["lock_acquired"] = lock_acquired
    updated["lock_wait_ms"] = lock_wait_ms
    updated.setdefault("file_write", False)
    updated.setdefault("fill_write", False)
    updated.setdefault("fill_committed", False)
    updated.setdefault("post_write_verified", False)
    return updated


def _post_write_failed(stage: str, reason: str) -> dict[str, Any]:
    result = _blocked(stage, reason)
    result.update(
        {
            "changed": True,
            "file_write": True,
            "fill_write": True,
            "fill_committed": True,
            "post_write_verified": False,
        }
    )
    return result


def _write_json_temp(path: Path, data: dict[str, Any]) -> Path:
    tmp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return tmp_path


def _cleanup_temp(path: Path | None) -> None:
    if path is not None and path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def _read_fills(path: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not path.exists():
        return {"version": 1, "updated_at": None, "fills": []}, None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, _blocked("read_fills", f"failed to read fills json: {exc}")

    if not isinstance(data, dict):
        return {}, _blocked("read_fills", "fills root must be an object")

    fills = data.get("fills")
    if not isinstance(fills, list):
        return {}, _blocked("read_fills", "fills must be a list")

    for item in fills:
        if not isinstance(item, dict):
            return {}, _blocked("read_fills", "fills must contain only objects")

    return data, None


def _read_fills_for_lookup(path: Path) -> list[dict[str, Any]]:
    data, blocked = _read_fills(path)
    if blocked is not None:
        return []
    fills = data.get("fills")
    return [dict(item) for item in fills if isinstance(item, dict)] if isinstance(fills, list) else []


def _validate_event_record_result(result_value: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    result = _as_dict(result_value)
    if not isinstance(result_value, dict):
        return result, _blocked("chejan_event_record_result", "chejan_event_record_result must be a dict")

    if result.get("recorded") is not True:
        return result, _blocked("chejan_event_record_result", "chejan_event_record_result.recorded is not true")

    if result.get("next_stage") != CHEJAN_EVENT_NEXT_STAGE_REQUIRED:
        return result, _blocked(
            "chejan_event_record_result",
            "chejan_event_record_result.next_stage is not FILL_RECORD_REQUIRED",
        )

    return result, None


def _received_at(event: dict[str, Any]) -> str:
    raw_event = _as_dict(event.get("raw_event"))
    return _clean_text(event.get("received_at")) or _clean_text(raw_event.get("received_at"))


def _required_text(event: dict[str, Any], field: str) -> str | None:
    value = _clean_text(event.get(field))
    if not value:
        return f"normalized_event.{field} is required"
    return None


def _required_int(event: dict[str, Any], field: str) -> str | None:
    value = event.get(field)
    if not isinstance(value, int):
        return f"normalized_event.{field} is required"
    return None


def _validate_normalized_event(event_value: Any) -> tuple[dict[str, Any], str, dict[str, Any] | None]:
    event = _as_dict(event_value)
    if not isinstance(event_value, dict):
        return event, "", _blocked("normalized_event", "normalized_event must be a dict")

    event_type = _clean_text(event.get("event_type"))
    if event_type not in _FILL_EVENT_TYPES:
        return event, event_type, _blocked("event_type", "normalized_event.event_type is not fill recordable")

    for field in ("broker_order_no", "account_no", "code", "side"):
        reason = _required_text(event, field)
        if reason:
            return event, event_type, _blocked("normalized_event", reason)

    for field in ("filled_quantity", "filled_price", "remaining_quantity", "order_quantity"):
        reason = _required_int(event, field)
        if reason:
            return event, event_type, _blocked("normalized_event", reason)

    if not _received_at(event):
        return event, event_type, _blocked("normalized_event", "normalized_event.received_at is required")

    if event["filled_quantity"] <= 0:
        return event, event_type, _blocked("quantity", "filled_quantity must be greater than 0")

    if event["filled_price"] <= 0:
        return event, event_type, _blocked("price", "filled_price must be greater than 0")

    if event_type == "PARTIAL_FILL" and event["remaining_quantity"] <= 0:
        return event, event_type, _blocked("remaining_quantity", "PARTIAL_FILL remaining_quantity must be greater than 0")

    if event_type == "FULL_FILL" and event["remaining_quantity"] != 0:
        return event, event_type, _blocked("remaining_quantity", "FULL_FILL remaining_quantity must be 0")

    return event, event_type, None


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest().upper()


def _raw_event_identity(event: dict[str, Any]) -> tuple[str, str]:
    raw_event = _as_dict(event.get("raw_event"))
    fid_values = _as_dict(raw_event.get("fid_values"))
    for source in (event, raw_event):
        for field in ("execution_no", "broker_event_id", "event_id", "chejan_event_id", "fill_no", "trade_no"):
            value = _clean_text(source.get(field))
            if value:
                source_name = "execution_no" if field == "execution_no" else "broker_event_id"
                return source_name, value
    fid_909 = _clean_text(fid_values.get("909"))
    if fid_909:
        return "fid_909", fid_909
    return "", ""


def _stored_execution_identity(record: dict[str, Any]) -> tuple[str, str]:
    source = _clean_text(record.get("execution_identity_source"))
    value = _clean_text(record.get("execution_identity"))
    if source and value:
        return source, value
    return "", ""


def _legacy_execution_identity(record: dict[str, Any]) -> tuple[str, str]:
    source, value = _stored_execution_identity(record)
    if source and value:
        return source, value
    normalized = _as_dict(record.get("normalized_event"))
    return _raw_event_identity(normalized)


def _fill_id(event: dict[str, Any], event_type: str, received_at: str) -> str:
    received_hash = hashlib.sha256(received_at.encode("utf-8")).hexdigest().upper()[:12]
    parts = [
        "FILL",
        _clean_text(event.get("broker_order_no")),
        event_type,
        str(event.get("filled_quantity")),
        str(event.get("remaining_quantity")),
        str(event.get("filled_price")),
        received_hash,
    ]
    return "_".join(parts)


def _composite_key(record: dict[str, Any]) -> tuple[str, str, str, str, str, str]:
    return (
        _clean_text(record.get("broker_order_no")),
        _clean_text(record.get("event_type")),
        str(record.get("filled_quantity")),
        str(record.get("remaining_quantity")),
        str(record.get("filled_price")),
        _clean_text(record.get("received_at")),
    )


def _duplicate_reason(fills: list[Any], candidate: dict[str, Any]) -> str | None:
    candidate_identity_source, candidate_identity = _stored_execution_identity(candidate)
    if candidate_identity_source and candidate_identity:
        for fill in fills:
            item = _as_dict(fill)
            item_source, item_identity = _stored_execution_identity(item)
            if item_source and item_identity:
                if item_source == candidate_identity_source and item_identity == candidate_identity:
                    return f"duplicate {candidate_identity_source}"
                continue

            legacy_source, legacy_identity = _legacy_execution_identity(item)
            if legacy_source == candidate_identity_source and legacy_identity == candidate_identity:
                return f"duplicate {candidate_identity_source}"
        return None

    candidate_fill_id = _clean_text(candidate.get("fill_id"))
    candidate_key = _composite_key(candidate)
    candidate_event_hash = _stable_hash(candidate.get("normalized_event"))

    for fill in fills:
        item = _as_dict(fill)
        if _clean_text(item.get("fill_id")) == candidate_fill_id:
            return "duplicate fill_id"

    for fill in fills:
        item = _as_dict(fill)
        if _composite_key(item) == candidate_key:
            return "duplicate fill composite key"

    for fill in fills:
        item = _as_dict(fill)
        if _stable_hash(item.get("normalized_event")) == candidate_event_hash:
            return "duplicate normalized_event"

    return None


def _matching_existing_fill_record(
    fill: dict[str, Any],
    chejan_result: dict[str, Any],
    normalized_event: dict[str, Any],
) -> bool:
    for field in ("order_id", "order_queued_id", "request_hash", "lock_id", "execution_id"):
        expected = _clean_text(chejan_result.get(field))
        actual = _clean_text(fill.get(field))
        if expected and actual and expected != actual:
            return False
    for field in ("broker_order_no", "event_type"):
        expected = _clean_text(chejan_result.get(field) or normalized_event.get(field))
        actual = _clean_text(fill.get(field))
        if expected and actual != expected:
            return False
    source, identity = _raw_event_identity(normalized_event)
    if source and identity:
        return (
            _clean_text(fill.get("execution_identity_source")) == source
            and _clean_text(fill.get("execution_identity")) == identity
        )
    return (
        fill.get("filled_quantity") == normalized_event.get("filled_quantity")
        and fill.get("remaining_quantity") == normalized_event.get("remaining_quantity")
        and fill.get("filled_price") == normalized_event.get("filled_price")
    )


def find_existing_execution_fill_record(
    fills_path: str | Path,
    chejan_event_record_result: Any,
    normalized_event: Any,
) -> dict[str, Any] | None:
    target_path = Path(fills_path)
    chejan_result = _as_dict(chejan_event_record_result)
    event = _as_dict(normalized_event)
    for fill in _read_fills_for_lookup(target_path):
        if _matching_existing_fill_record(fill, chejan_result, event):
            return dict(fill)
    return None


def _fill_record(
    *,
    result: dict[str, Any],
    event: dict[str, Any],
    event_type: str,
    received_at: str,
    recorded_at: str,
) -> dict[str, Any]:
    fill_id = _fill_id(event, event_type, received_at)
    execution_identity_source, execution_identity = _raw_event_identity(event)
    return {
        "fill_id": fill_id,
        "execution_identity_source": execution_identity_source,
        "execution_identity": execution_identity,
        "fill_source": "chejan_event",
        "event_type": event_type,
        "broker": _clean_text(event.get("broker")),
        "broker_order_no": _clean_text(event.get("broker_order_no")),
        "order_id": _clean_text(result.get("order_id")),
        "order_queued_id": _clean_text(result.get("order_queued_id")),
        "execution_id": _clean_text(result.get("execution_id")),
        "request_hash": _clean_text(result.get("request_hash")),
        "lock_id": _clean_text(result.get("lock_id")),
        "account_no": _clean_text(event.get("account_no")),
        "code": _clean_text(event.get("code")),
        "side": _clean_text(event.get("side")),
        "filled_quantity": event.get("filled_quantity"),
        "filled_price": event.get("filled_price"),
        "remaining_quantity": event.get("remaining_quantity"),
        "order_quantity": event.get("order_quantity"),
        "order_price": event.get("order_price"),
        "received_at": received_at,
        "recorded_at": recorded_at,
        "normalized_event": deepcopy(event),
    }


def record_execution_fill(
    chejan_event_record_result: Any,
    normalized_event: Any,
    fill_path: str | Path | None,
    fill_snapshot: Any = None,
    context: Any = None,
    backup: bool = True,
) -> dict[str, Any]:
    """Append a fill ledger record to the explicit fill_path only."""
    result, result_blocked = _validate_event_record_result(chejan_event_record_result)
    if result_blocked is not None:
        return result_blocked

    event, event_type, event_blocked = _validate_normalized_event(normalized_event)
    if event_blocked is not None:
        return event_blocked

    if not _confirmed(context):
        return _blocked("operator_confirmation", "manual fill record confirmation is required")

    if fill_path is None or not str(fill_path).strip():
        return _blocked("fill_path", "fill_path is required")

    target_path = Path(fill_path)
    before_sha256 = None
    if target_path.exists():
        before_sha256 = _sha256_file(target_path)

    snapshot_sha256 = _snapshot_sha256(fill_snapshot)
    if snapshot_sha256 and before_sha256 != snapshot_sha256:
        return _blocked(
            "stale_fills",
            "fills file changed after Chejan event record; manual review required",
        )

    now = _now_text()
    received_at = _received_at(event)
    fill_record = _fill_record(
        result=result,
        event=event,
        event_type=event_type,
        received_at=received_at,
        recorded_at=now,
    )

    try:
        with _FILL_THREAD_LOCK:
            with _FillFileLock(target_path, _lock_timeout_sec(context)) as lock:
                current_sha256 = _sha256_file(target_path) if target_path.exists() else None
                before_sha256 = current_sha256
                if snapshot_sha256 and current_sha256 != snapshot_sha256:
                    return _with_lock_metadata(
                        _blocked("stale_fills", "fills file changed after Chejan event record; manual review required"),
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )

                data, read_blocked = _read_fills(target_path)
                if read_blocked is not None:
                    return _with_lock_metadata(read_blocked, lock_acquired=True, lock_wait_ms=lock.wait_ms)

                duplicate_reason = _duplicate_reason(data["fills"], fill_record)
                if duplicate_reason:
                    return _with_lock_metadata(
                        _blocked("duplicate", duplicate_reason),
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )

                backup_path = None
                if backup and target_path.exists():
                    backup_path = str(target_path) + ".bak"
                    try:
                        shutil.copy2(target_path, backup_path)
                    except Exception as exc:
                        return _with_lock_metadata(
                            _blocked("backup", f"failed to create backup: {exc}"),
                            lock_acquired=True,
                            lock_wait_ms=lock.wait_ms,
                        )

                updated_data = deepcopy(data)
                updated_data["version"] = updated_data.get("version", 1)
                updated_data["updated_at"] = now
                updated_data["fills"].append(fill_record)

                tmp_path = None
                try:
                    tmp_path = _write_json_temp(target_path, updated_data)
                    os.replace(tmp_path, target_path)
                    tmp_path = None
                except Exception as exc:
                    _cleanup_temp(tmp_path)
                    return _with_lock_metadata(
                        _blocked("write_fills", f"failed to write fills json: {exc}"),
                        lock_acquired=True,
                        lock_wait_ms=lock.wait_ms,
                    )

                after_sha256 = _sha256_file(target_path)
                post_data, post_blocked = _read_fills(target_path)
                if post_blocked is not None:
                    failed = _post_write_failed(
                        "post_write_verify",
                        post_blocked.get("blocked_reasons", ["fills json invalid after write"])[0],
                    )
                    failed.update(
                        {
                            "fill_path": str(target_path),
                            "backup_path": backup_path,
                            "fill_id": fill_record["fill_id"],
                            "before_sha256": before_sha256,
                            "after_sha256": after_sha256,
                        }
                    )
                    return _with_lock_metadata(failed, lock_acquired=True, lock_wait_ms=lock.wait_ms)

                if post_data != updated_data:
                    failed = _post_write_failed("post_write_verify", "fills json did not match expected data after write")
                    failed.update(
                        {
                            "fill_path": str(target_path),
                            "backup_path": backup_path,
                            "fill_id": fill_record["fill_id"],
                            "before_sha256": before_sha256,
                            "after_sha256": after_sha256,
                        }
                    )
                    return _with_lock_metadata(failed, lock_acquired=True, lock_wait_ms=lock.wait_ms)

                result_payload = {
                    "fill_recorded": True,
                    "fill_stage": "execution_fill_recorded",
                    "next_stage": NEXT_STAGE_POSITION_UPDATE_REQUIRED,
                    "changed": True,
                    "file_write": True,
                    "fill_write": True,
                    "fill_committed": True,
                    "post_write_verified": True,
                    "fill_path": str(target_path),
                    "backup_path": backup_path,
                    "fill_id": fill_record["fill_id"],
                    "execution_identity_source": fill_record["execution_identity_source"],
                    "execution_identity": fill_record["execution_identity"],
                    "event_type": event_type,
                    "order_id": fill_record["order_id"],
                    "order_queued_id": fill_record["order_queued_id"],
                    "broker_order_no": fill_record["broker_order_no"],
                    "request_hash": fill_record["request_hash"],
                    "lock_id": fill_record["lock_id"],
                    "execution_id": fill_record["execution_id"],
                    "filled_quantity": fill_record["filled_quantity"],
                    "filled_price": fill_record["filled_price"],
                    "fill_record": deepcopy(fill_record),
                    "before_sha256": before_sha256,
                    "after_sha256": after_sha256,
                    "blocked_reasons": [],
                    "warnings": [],
                }
                return _with_lock_metadata(result_payload, lock_acquired=True, lock_wait_ms=lock.wait_ms)
    except TimeoutError:
        return _with_lock_metadata(_blocked("fill_lock", "fills lock timeout"), lock_acquired=False)

    return _with_lock_metadata(_blocked("fill_lock", "fills lock failed"), lock_acquired=False)
