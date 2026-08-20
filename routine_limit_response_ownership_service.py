# -*- coding: utf-8 -*-
"""Durable exactly-once ownership for Routine Limit response events."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
import msvcrt
from pathlib import Path
import threading
import time
from typing import Any, Mapping

from production_recovery_contract import normalize_stock_code
from runtime_atomic_writer import STATUS_OK, write_json_atomic


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_OWNERSHIP_PATH = PROJECT_ROOT / "runtime" / "routine_limit_response_ownership.json"
SCHEMA_VERSION = 1
STATUS_OWNED = "OWNED"
STATUS_COMPLETED = "COMPLETED"
INTENT_EARLY_CLOSE = "EARLY_CLOSE"
INTENT_IMMEDIATE = "IMMEDIATE_LIQUIDATION_REQUIRED"
RESPONSE_MODES = frozenset({"조기마감", "즉시청산", "구간마감"})
_ROOT_KEYS = frozenset({"schema_version", "revision", "updated_at", "events"})
_EVENT_KEYS = frozenset(
    {
        "event_id", "account_no", "trading_day", "routine_instance_id",
        "trigger_identity_source", "trigger_identity", "trigger_stock_code",
        "detected_at", "selected_stock_code", "selected_at",
        "configured_response_mode", "response_intent", "status", "completion",
    }
)
_COMPLETION_KEYS = frozenset({"evaluator_status", "observed_at"})
_LOCKS_GUARD = threading.RLock()
_LOCKS: dict[str, threading.RLock] = {}


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _account(value: object) -> str:
    result = _text(value)
    if not result:
        raise ValueError("account_no is required")
    return result


def _day(value: object) -> str:
    try:
        return date.fromisoformat(_text(value)).isoformat()
    except ValueError as exc:
        raise ValueError("trading_day must be YYYY-MM-DD") from exc


def _required(value: object, field: str) -> str:
    result = _text(value)
    if not result:
        raise ValueError(f"{field} is required")
    return result


def _stock(value: object, field: str) -> str:
    code = normalize_stock_code(value)
    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"{field} must be a six-digit stock code")
    return code


def _timestamp(value: object, field: str) -> str:
    result = _required(value, field)
    try:
        datetime.fromisoformat(result)
    except ValueError as exc:
        raise ValueError(f"{field} must be ISO-8601") from exc
    return result


def routine_limit_response_event_id(
    *, account_no: object, trading_day: object, routine_instance_id: object,
    trigger_identity_source: object, trigger_identity: object,
) -> str:
    identity = {
        "account_no": _account(account_no),
        "trading_day": _day(trading_day),
        "routine_instance_id": _required(routine_instance_id, "routine_instance_id"),
        "trigger_identity_source": _required(trigger_identity_source, "trigger_identity_source"),
        "trigger_identity": _required(trigger_identity, "trigger_identity"),
    }
    encoded = json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return f"ROUTINE_LIMIT_RESPONSE_EVENT_{hashlib.sha256(encoded).hexdigest().upper()}"


def _validate_completion(value: object) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != _COMPLETION_KEYS:
        raise ValueError("completion schema is invalid")
    status = _text(value.get("evaluator_status")).upper()
    if status not in {"DONE", "CARRYOVER_DONE"}:
        raise ValueError("completion evaluator_status is invalid")
    return {"evaluator_status": status, "observed_at": _timestamp(value.get("observed_at"), "completion.observed_at")}


def _validate_event(key: object, value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _EVENT_KEYS:
        raise ValueError("routine ownership event schema is invalid")
    account = _account(value.get("account_no"))
    trading_day = _day(value.get("trading_day"))
    routine_id = _required(value.get("routine_instance_id"), "routine_instance_id")
    identity_source = _required(value.get("trigger_identity_source"), "trigger_identity_source")
    identity = _required(value.get("trigger_identity"), "trigger_identity")
    event_id = routine_limit_response_event_id(
        account_no=account, trading_day=trading_day, routine_instance_id=routine_id,
        trigger_identity_source=identity_source, trigger_identity=identity,
    )
    if _text(key) != event_id or _text(value.get("event_id")) != event_id:
        raise ValueError("event_id does not match canonical Routine trigger identity")
    mode = _text(value.get("configured_response_mode"))
    if mode not in RESPONSE_MODES:
        raise ValueError("configured_response_mode is invalid")
    intent = _text(value.get("response_intent")).upper()
    if intent not in {INTENT_EARLY_CLOSE, INTENT_IMMEDIATE}:
        raise ValueError("response_intent is invalid")
    status = _text(value.get("status")).upper()
    if status not in {STATUS_OWNED, STATUS_COMPLETED}:
        raise ValueError("ownership status is invalid")
    completion = _validate_completion(value.get("completion"))
    if (status == STATUS_OWNED) == (completion is not None):
        raise ValueError("ownership status and completion evidence conflict")
    return {
        "event_id": event_id, "account_no": account, "trading_day": trading_day,
        "routine_instance_id": routine_id,
        "trigger_identity_source": identity_source, "trigger_identity": identity,
        "trigger_stock_code": _stock(value.get("trigger_stock_code"), "trigger_stock_code"),
        "detected_at": _timestamp(value.get("detected_at"), "detected_at"),
        "selected_stock_code": _stock(value.get("selected_stock_code"), "selected_stock_code"),
        "selected_at": _timestamp(value.get("selected_at"), "selected_at"),
        "configured_response_mode": mode, "response_intent": intent,
        "status": status, "completion": completion,
    }


def _empty() -> dict[str, object]:
    return {"schema_version": SCHEMA_VERSION, "revision": 0, "updated_at": None, "events": {}}


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _validate_document(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _ROOT_KEYS:
        raise ValueError("routine ownership root schema is invalid")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("routine ownership schema_version is invalid")
    revision = value.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("revision must be a nonnegative integer")
    events = value.get("events")
    if not isinstance(events, dict):
        raise ValueError("events must be an object")
    normalized = {_text(key): _validate_event(key, event) for key, event in events.items()}
    updated_at = value.get("updated_at")
    if normalized:
        _timestamp(updated_at, "updated_at")
    elif updated_at is not None:
        raise ValueError("empty ownership updated_at must be null")
    return {"schema_version": SCHEMA_VERSION, "revision": revision, "updated_at": updated_at, "events": normalized}


def _path_lock(path: Path) -> threading.RLock:
    key = str(path.resolve()).casefold()
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


class _FileLock:
    def __init__(self, path: Path, timeout: float) -> None:
        self.path = path.with_name(f"{path.name}.lock")
        self.timeout = timeout
        self.handle: Any = None
        self.wait_ms = 0

    def __enter__(self):
        if not self.path.parent.is_dir():
            raise FileNotFoundError("ownership parent directory does not exist")
        self.handle = self.path.open("a+b")
        started = time.monotonic()
        while True:
            try:
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                self.wait_ms = int((time.monotonic() - started) * 1000)
                return self
            except OSError:
                if time.monotonic() - started >= self.timeout:
                    self.handle.close()
                    raise TimeoutError("routine ownership file lock timeout")
                time.sleep(0.02)

    def __exit__(self, *_args):
        if self.handle is not None:
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            self.handle.close()


class RoutineLimitResponseOwnershipService:
    def __init__(self, path: str | Path = DEFAULT_OWNERSHIP_PATH, *, now_factory=None, lock_timeout_seconds: float = 5.0) -> None:
        self.path = Path(path)
        self._now = now_factory or (lambda: datetime.now().isoformat(timespec="seconds"))
        self._timeout = lock_timeout_seconds

    def _load(self) -> tuple[dict[str, object] | None, str]:
        if not self.path.exists():
            return _empty(), ""
        try:
            value = json.loads(
                self.path.read_text(encoding="utf-8"),
                object_pairs_hook=_reject_duplicate_keys,
            )
            return _validate_document(value), ""
        except Exception as exc:
            return None, str(exc)

    def read_snapshot(self) -> dict[str, object]:
        document, error = self._load()
        return {"ok": document is not None, "snapshot": deepcopy(document), "reason": error, "path": str(self.path)}

    def active_event(self, *, account_no: object, trading_day: object, routine_instance_id: object) -> dict[str, object]:
        try:
            account, day, routine_id = _account(account_no), _day(trading_day), _required(routine_instance_id, "routine_instance_id")
        except ValueError as exc:
            return self._blocked(str(exc))
        read = self.read_snapshot()
        snapshot = read.get("snapshot")
        if read.get("ok") is not True or not isinstance(snapshot, Mapping):
            return self._blocked(_text(read.get("reason")) or "OWNERSHIP_UNAVAILABLE")
        matches = [deepcopy(event) for event in snapshot["events"].values() if event["account_no"] == account and event["trading_day"] == day and event["routine_instance_id"] == routine_id and event["status"] == STATUS_OWNED]
        if len(matches) > 1:
            return self._blocked("MULTIPLE_ACTIVE_ROUTINE_EVENTS")
        return {"ok": True, "event": matches[0] if matches else None, "revision": snapshot["revision"], "reason": ""}

    def claim(self, **values: object) -> dict[str, object]:
        try:
            event_id = routine_limit_response_event_id(
                account_no=values.get("account_no"), trading_day=values.get("trading_day"),
                routine_instance_id=values.get("routine_instance_id"),
                trigger_identity_source=values.get("trigger_identity_source"), trigger_identity=values.get("trigger_identity"),
            )
            event = _validate_event(event_id, {
                "event_id": event_id, "account_no": values.get("account_no"), "trading_day": values.get("trading_day"),
                "routine_instance_id": values.get("routine_instance_id"), "trigger_identity_source": values.get("trigger_identity_source"),
                "trigger_identity": values.get("trigger_identity"), "trigger_stock_code": values.get("trigger_stock_code"),
                "detected_at": values.get("detected_at"), "selected_stock_code": values.get("selected_stock_code"),
                "selected_at": values.get("selected_at") or self._now(), "configured_response_mode": values.get("configured_response_mode"),
                "response_intent": values.get("response_intent"), "status": STATUS_OWNED, "completion": None,
            })
            expected = values.get("expected_revision")
            if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
                raise ValueError("expected_revision must be a nonnegative integer")
        except ValueError as exc:
            return self._blocked(str(exc))
        return self._mutate(event_id, expected, lambda document: self._claim_mutation(document, event))

    def _claim_mutation(self, document: dict[str, object], event: dict[str, object]):
        events = document["events"]
        existing = events.get(event["event_id"])
        if existing is not None:
            return document, existing, "ALREADY_OWNED" if existing["status"] == STATUS_OWNED else "ALREADY_COMPLETED", False
        active = [item for item in events.values() if item["account_no"] == event["account_no"] and item["trading_day"] == event["trading_day"] and item["routine_instance_id"] == event["routine_instance_id"] and item["status"] == STATUS_OWNED]
        if active:
            return document, active[0], "ACTIVE_ROUTINE_EVENT_EXISTS", False
        updated = deepcopy(document); updated["events"][event["event_id"]] = event
        return updated, event, "CLAIMED", True

    def promote_to_immediate(self, *, event_id: object, expected_revision: object) -> dict[str, object]:
        return self._event_transition(event_id, expected_revision, "PROMOTED_TO_IMMEDIATE", lambda event: {**event, "response_intent": INTENT_IMMEDIATE} if event["status"] == STATUS_OWNED and event["response_intent"] == INTENT_EARLY_CLOSE else None)

    def mark_completed(self, *, event_id: object, evaluator_status: object, observed_at: object, expected_revision: object) -> dict[str, object]:
        try:
            completion = _validate_completion({"evaluator_status": evaluator_status, "observed_at": observed_at})
        except ValueError as exc:
            return self._blocked(str(exc))
        return self._event_transition(event_id, expected_revision, "COMPLETED", lambda event: {**event, "status": STATUS_COMPLETED, "completion": completion} if event["status"] == STATUS_OWNED else None)

    def _event_transition(self, event_id: object, expected_revision: object, operation: str, transition):
        identity = _text(event_id)
        if not identity:
            return self._blocked("event_id is required")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            return self._blocked("expected_revision must be a nonnegative integer")
        def mutation(document):
            event = document["events"].get(identity)
            if not isinstance(event, dict):
                return document, None, "EVENT_NOT_FOUND", False
            changed = transition(deepcopy(event))
            if changed is None:
                return document, event, "TRANSITION_NOT_ALLOWED", False
            updated = deepcopy(document); updated["events"][identity] = changed
            return updated, changed, operation, True
        return self._mutate(identity, expected_revision, mutation)

    def _mutate(self, event_id: str, expected_revision: int, mutation):
        try:
            with _path_lock(self.path):
                with _FileLock(self.path, self._timeout) as lock:
                    document, error = self._load()
                    if document is None:
                        return self._blocked(error)
                    if document["revision"] != expected_revision:
                        return self._blocked("OWNERSHIP_REVISION_CONFLICT", revision=document["revision"])
                    updated, event, operation, changed = mutation(document)
                    if not changed:
                        return {"ok": True, "changed": False, "operation": operation, "event_id": event_id, "event": deepcopy(event), "revision": document["revision"], "reason": ""}
                    updated["revision"] = document["revision"] + 1
                    updated["updated_at"] = _timestamp(self._now(), "updated_at")
                    validated = _validate_document(updated)
                    write = write_json_atomic(self.path, validated)
                    if write.get("status") != STATUS_OK or write.get("written") is not True:
                        return self._blocked(_text(write.get("error")) or "ATOMIC_WRITE_FAILED")
                    verified, verify_error = self._load()
                    if verified != validated:
                        return self._blocked(verify_error or "OWNERSHIP_READ_BACK_MISMATCH")
                    return {"ok": True, "changed": True, "operation": operation, "event_id": event_id, "event": deepcopy(event), "revision": validated["revision"], "lock_wait_ms": lock.wait_ms, "reason": ""}
        except Exception as exc:
            return self._blocked(str(exc))

    @staticmethod
    def _blocked(reason: str, **updates: object) -> dict[str, object]:
        return {"ok": False, "changed": False, "reason": _text(reason), **updates}


__all__ = ["DEFAULT_OWNERSHIP_PATH", "INTENT_EARLY_CLOSE", "INTENT_IMMEDIATE", "RoutineLimitResponseOwnershipService", "STATUS_COMPLETED", "STATUS_OWNED", "routine_limit_response_event_id"]
