# -*- coding: utf-8 -*-
"""Durable exactly-once ownership for buffer-response events.

This service owns only the mapping from one canonical BUY lifecycle event to
one selected stock and its ownership completion status.  It deliberately does
not calculate budgets, rank candidates, execute close commands, or mutate any
other Runtime file.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
import msvcrt
from pathlib import Path
import threading
import time
from typing import Any, Callable, Mapping

from production_recovery_contract import normalize_stock_code
from runtime_atomic_writer import STATUS_OK, write_json_atomic


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_OWNERSHIP_PATH = PROJECT_ROOT / "runtime" / "buffer_response_ownership.json"

SCHEMA_VERSION = 1
LEGACY_BATCH_SCHEMA_VERSION = 2
BATCH_SCHEMA_VERSION = 3
STATUS_OWNED = "OWNED"
STATUS_COMPLETED = "COMPLETED"
OWNERSHIP_STATUSES = frozenset({STATUS_OWNED, STATUS_COMPLETED})
COMPLETION_STATUSES = frozenset({"DONE", "CARRYOVER_DONE"})
RESPONSE_INTENT_EARLY_CLOSE = "EARLY_CLOSE"
RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED = (
    "IMMEDIATE_LIQUIDATION_REQUIRED"
)
RESPONSE_INTENTS = frozenset(
    {
        RESPONSE_INTENT_EARLY_CLOSE,
        RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED,
    }
)

_ROOT_KEYS = frozenset({"schema_version", "revision", "updated_at", "events"})
_EVENT_KEYS = frozenset(
    {
        "event_id",
        "account_no",
        "trading_day",
        "source_order_id",
        "source_stock_code",
        "detected_at",
        "status",
        "selected_stock_code",
        "selected_at",
        "completion",
    }
)
_BATCH_EVENT_KEYS = frozenset(
    {
        "event_id",
        "account_no",
        "trading_day",
        "event_sequence",
        "source_evidence",
        "detected_at",
        "status",
        "selected_stock_code",
        "selected_at",
        "completion",
    }
)
_INTENT_BATCH_EVENT_KEYS = frozenset((*_BATCH_EVENT_KEYS, "response_intent"))
_SOURCE_EVIDENCE_KEYS = frozenset(
    {
        "observation_id",
        "observed_at",
        "previous_entry_amount",
        "current_entry_amount",
        "new_contributing_buy_ids",
        "contributing_buy_ids",
        "confirmed_evidence",
    }
)
_CONFIRMED_EVIDENCE_KEYS = frozenset(
    {
        "recovery_session_id",
        "queue_revision",
        "order_queue_sha256",
        "positions_sha256",
        "fills_sha256",
    }
)
_COMPLETION_KEYS = frozenset({"evaluator_status", "observed_at"})
_THREAD_LOCKS_GUARD = threading.RLock()
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_DEFAULT_LOCK_TIMEOUT_SECONDS = 5.0


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _canonical_account_no(value: object) -> str:
    account = _text(value)
    if not account:
        raise ValueError("account_no is required")
    return account


def _canonical_trading_day(value: object) -> str:
    text = _text(value)
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError("trading_day must be YYYY-MM-DD") from exc


def _canonical_order_id(value: object) -> str:
    order_id = _text(value)
    if not order_id:
        raise ValueError("source_order_id is required")
    return order_id


def _canonical_stock_code(value: object, *, field: str) -> str:
    code = normalize_stock_code(value)
    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"{field} must be a six-digit stock code")
    return code


def _canonical_timestamp(value: object, *, field: str) -> str:
    text = _text(value)
    if not text:
        raise ValueError(f"{field} is required")
    try:
        datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be ISO-8601") from exc
    return text


def buffer_response_event_id(
    *,
    account_no: object,
    trading_day: object,
    source_order_id: object,
) -> str:
    """Return the deterministic event ID for one canonical BUY lifecycle."""

    identity = {
        "account_no": _canonical_account_no(account_no),
        "trading_day": _canonical_trading_day(trading_day),
        "source_order_id": _canonical_order_id(source_order_id),
    }
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"BUFFER_RESPONSE_EVENT_{hashlib.sha256(encoded).hexdigest().upper()}"


def buffer_response_batch_event_id(
    *,
    account_no: object,
    trading_day: object,
    event_sequence: object,
) -> str:
    """Return one deterministic identity for a confirmed ingress batch."""

    if isinstance(event_sequence, bool) or not isinstance(event_sequence, int):
        raise ValueError("event_sequence must be a positive integer")
    if event_sequence <= 0:
        raise ValueError("event_sequence must be a positive integer")
    identity = {
        "account_no": _canonical_account_no(account_no),
        "trading_day": _canonical_trading_day(trading_day),
        "event_sequence": event_sequence,
    }
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"BUFFER_RESPONSE_BATCH_{hashlib.sha256(encoded).hexdigest().upper()}"


def _empty_document(schema_version: int = SCHEMA_VERSION) -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "revision": 0,
        "updated_at": None,
        "events": {},
    }


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_json_strict(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
    if not isinstance(value, dict):
        raise ValueError("ownership root must be an object")
    return value


def _validate_completion(value: object) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != _COMPLETION_KEYS:
        raise ValueError("completion schema is invalid")
    evaluator_status = _text(value.get("evaluator_status")).upper()
    if evaluator_status not in COMPLETION_STATUSES:
        raise ValueError("completion evaluator_status is not canonical complete")
    observed_at = _canonical_timestamp(value.get("observed_at"), field="completion.observed_at")
    return {"evaluator_status": evaluator_status, "observed_at": observed_at}


def _validate_event(event_key: object, value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _EVENT_KEYS:
        raise ValueError("ownership event schema is invalid")
    account_no = _canonical_account_no(value.get("account_no"))
    trading_day = _canonical_trading_day(value.get("trading_day"))
    source_order_id = _canonical_order_id(value.get("source_order_id"))
    event_id = buffer_response_event_id(
        account_no=account_no,
        trading_day=trading_day,
        source_order_id=source_order_id,
    )
    if _text(event_key) != event_id or _text(value.get("event_id")) != event_id:
        raise ValueError("event_id does not match canonical BUY identity")
    status = _text(value.get("status")).upper()
    if status not in OWNERSHIP_STATUSES:
        raise ValueError("ownership status is invalid")
    completion = _validate_completion(value.get("completion"))
    if status == STATUS_OWNED and completion is not None:
        raise ValueError("OWNED event must not contain completion evidence")
    if status == STATUS_COMPLETED and completion is None:
        raise ValueError("COMPLETED event requires completion evidence")
    return {
        "event_id": event_id,
        "account_no": account_no,
        "trading_day": trading_day,
        "source_order_id": source_order_id,
        "source_stock_code": _canonical_stock_code(
            value.get("source_stock_code"), field="source_stock_code"
        ),
        "detected_at": _canonical_timestamp(value.get("detected_at"), field="detected_at"),
        "status": status,
        "selected_stock_code": _canonical_stock_code(
            value.get("selected_stock_code"), field="selected_stock_code"
        ),
        "selected_at": _canonical_timestamp(value.get("selected_at"), field="selected_at"),
        "completion": completion,
    }


def _canonical_nonnegative_integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    return value


def _canonical_response_intent(value: object) -> str:
    intent = _text(value).upper()
    if intent not in RESPONSE_INTENTS:
        raise ValueError("response_intent is invalid")
    return intent


def _canonical_identity_list(value: object, *, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    identities = [_text(item) for item in value]
    if any(not item for item in identities):
        raise ValueError(f"{field} contains an empty identity")
    if identities != sorted(set(identities)):
        raise ValueError(f"{field} must be sorted and unique")
    return identities


def _canonical_sha256(value: object, *, field: str) -> str:
    digest = _text(value).upper()
    if len(digest) != 64 or any(ch not in "0123456789ABCDEF" for ch in digest):
        raise ValueError(f"{field} must be an uppercase SHA-256")
    return digest


def _validate_confirmed_evidence(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _CONFIRMED_EVIDENCE_KEYS:
        raise ValueError("source_evidence.confirmed_evidence schema is invalid")
    recovery_session_id = _text(value.get("recovery_session_id"))
    if not recovery_session_id:
        raise ValueError("source_evidence.confirmed_evidence.recovery_session_id is required")
    return {
        "recovery_session_id": recovery_session_id,
        "queue_revision": _canonical_nonnegative_integer(
            value.get("queue_revision"), field="confirmed_evidence.queue_revision"
        ),
        "order_queue_sha256": _canonical_sha256(
            value.get("order_queue_sha256"), field="confirmed_evidence.order_queue_sha256"
        ),
        "positions_sha256": _canonical_sha256(
            value.get("positions_sha256"), field="confirmed_evidence.positions_sha256"
        ),
        "fills_sha256": _canonical_sha256(
            value.get("fills_sha256"), field="confirmed_evidence.fills_sha256"
        ),
    }


def _validate_source_evidence(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _SOURCE_EVIDENCE_KEYS:
        raise ValueError("source_evidence schema is invalid")
    observation_id = _canonical_sha256(
        value.get("observation_id"), field="source_evidence.observation_id"
    )
    observed_at = _canonical_timestamp(
        value.get("observed_at"), field="source_evidence.observed_at"
    )
    previous_amount = _canonical_nonnegative_integer(
        value.get("previous_entry_amount"), field="source_evidence.previous_entry_amount"
    )
    current_amount = _canonical_nonnegative_integer(
        value.get("current_entry_amount"), field="source_evidence.current_entry_amount"
    )
    if current_amount <= previous_amount or current_amount <= 0:
        raise ValueError("source_evidence does not describe a buffer-entry increase")
    new_ids = _canonical_identity_list(
        value.get("new_contributing_buy_ids"),
        field="source_evidence.new_contributing_buy_ids",
    )
    if not new_ids:
        raise ValueError("source_evidence.new_contributing_buy_ids must not be empty")
    contributing_ids = _canonical_identity_list(
        value.get("contributing_buy_ids"),
        field="source_evidence.contributing_buy_ids",
    )
    if not set(new_ids).issubset(contributing_ids):
        raise ValueError("new contributing BUY identities are not in the captured contributor set")
    return {
        "observation_id": observation_id,
        "observed_at": observed_at,
        "previous_entry_amount": previous_amount,
        "current_entry_amount": current_amount,
        "new_contributing_buy_ids": new_ids,
        "contributing_buy_ids": contributing_ids,
        "confirmed_evidence": _validate_confirmed_evidence(value.get("confirmed_evidence")),
    }


def _validate_batch_event(event_key: object, value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _BATCH_EVENT_KEYS:
        raise ValueError("batch ownership event schema is invalid")
    account_no = _canonical_account_no(value.get("account_no"))
    trading_day = _canonical_trading_day(value.get("trading_day"))
    event_sequence = value.get("event_sequence")
    event_id = buffer_response_batch_event_id(
        account_no=account_no,
        trading_day=trading_day,
        event_sequence=event_sequence,
    )
    if _text(event_key) != event_id or _text(value.get("event_id")) != event_id:
        raise ValueError("event_id does not match canonical ingress batch identity")
    status = _text(value.get("status")).upper()
    if status not in OWNERSHIP_STATUSES:
        raise ValueError("ownership status is invalid")
    completion = _validate_completion(value.get("completion"))
    if status == STATUS_OWNED and completion is not None:
        raise ValueError("OWNED event must not contain completion evidence")
    if status == STATUS_COMPLETED and completion is None:
        raise ValueError("COMPLETED event requires completion evidence")
    return {
        "event_id": event_id,
        "account_no": account_no,
        "trading_day": trading_day,
        "event_sequence": int(event_sequence),
        "source_evidence": _validate_source_evidence(value.get("source_evidence")),
        "detected_at": _canonical_timestamp(value.get("detected_at"), field="detected_at"),
        "status": status,
        "selected_stock_code": _canonical_stock_code(
            value.get("selected_stock_code"), field="selected_stock_code"
        ),
        "selected_at": _canonical_timestamp(value.get("selected_at"), field="selected_at"),
        "completion": completion,
    }


def _validate_intent_batch_event(
    event_key: object, value: object
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _INTENT_BATCH_EVENT_KEYS:
        raise ValueError("intent batch ownership event schema is invalid")
    legacy_shape = {key: item for key, item in value.items() if key != "response_intent"}
    normalized = _validate_batch_event(event_key, legacy_shape)
    normalized["response_intent"] = _canonical_response_intent(
        value.get("response_intent")
    )
    return normalized


def validate_batch_ownership_event(value: object) -> dict[str, object]:
    """Validate and normalize one batch event without mutating ownership."""

    if not isinstance(value, Mapping):
        raise ValueError("batch ownership event must be an object")
    event = dict(value)
    if set(event) == _BATCH_EVENT_KEYS:
        return _validate_batch_event(event.get("event_id"), event)
    return _validate_intent_batch_event(event.get("event_id"), event)


def _validate_document(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _ROOT_KEYS:
        raise ValueError("ownership root schema is invalid")
    schema_version = value.get("schema_version")
    if schema_version not in {
        SCHEMA_VERSION,
        LEGACY_BATCH_SCHEMA_VERSION,
        BATCH_SCHEMA_VERSION,
    }:
        raise ValueError("ownership schema_version is unsupported")
    revision = value.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("ownership revision must be a nonnegative integer")
    updated_at = value.get("updated_at")
    if revision == 0:
        if updated_at is not None:
            raise ValueError("empty ownership snapshot updated_at must be null")
    else:
        _canonical_timestamp(updated_at, field="updated_at")
    raw_events = value.get("events")
    if not isinstance(raw_events, dict):
        raise ValueError("ownership events must be an object")
    validator = {
        SCHEMA_VERSION: _validate_event,
        LEGACY_BATCH_SCHEMA_VERSION: _validate_batch_event,
        BATCH_SCHEMA_VERSION: _validate_intent_batch_event,
    }[schema_version]
    events = {_text(key): validator(key, event) for key, event in raw_events.items()}
    if revision == 0 and events:
        raise ValueError("revision zero ownership snapshot must be empty")
    return {
        "schema_version": schema_version,
        "revision": revision,
        "updated_at": updated_at,
        "events": events,
    }


def _path_thread_lock(path: Path) -> threading.RLock:
    key = str(path.resolve()).casefold()
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _THREAD_LOCKS[key] = lock
        return lock


class _OwnershipFileLock:
    def __init__(self, path: Path, timeout_seconds: float) -> None:
        self.lock_path = path.with_name(f"{path.name}.lock")
        self.timeout_seconds = max(0.0, float(timeout_seconds))
        self.handle: Any = None
        self.wait_ms = 0

    def __enter__(self) -> "_OwnershipFileLock":
        if not self.lock_path.parent.is_dir():
            raise FileNotFoundError("ownership parent directory does not exist")
        self.handle = self.lock_path.open("a+b")
        start = time.monotonic()
        while True:
            try:
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                self.wait_ms = int((time.monotonic() - start) * 1000)
                return self
            except OSError:
                if time.monotonic() - start >= self.timeout_seconds:
                    self.handle.close()
                    self.handle = None
                    raise TimeoutError("ownership file lock timeout")
                time.sleep(0.02)

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self.handle.close()
            self.handle = None


class BufferResponseOwnershipService:
    """The sole mutation boundary for durable buffer-response ownership."""

    def __init__(
        self,
        ownership_path: str | Path = DEFAULT_OWNERSHIP_PATH,
        *,
        now_factory: Callable[[], object] | None = None,
        lock_timeout_seconds: float = _DEFAULT_LOCK_TIMEOUT_SECONDS,
    ) -> None:
        self.ownership_path = Path(ownership_path)
        self._now_factory = now_factory or (lambda: datetime.now().isoformat(timespec="seconds"))
        self._lock_timeout_seconds = lock_timeout_seconds

    def _now_text(self) -> str:
        value = self._now_factory()
        if isinstance(value, datetime):
            value = value.isoformat(timespec="seconds")
        return _canonical_timestamp(value, field="current timestamp")

    def _load(
        self, *, missing_schema_version: int = SCHEMA_VERSION
    ) -> tuple[dict[str, object] | None, str]:
        if not self.ownership_path.exists():
            return _empty_document(missing_schema_version), ""
        try:
            return _validate_document(_read_json_strict(self.ownership_path)), ""
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return None, str(exc)

    def read_snapshot(self) -> dict[str, object]:
        document, error = self._load()
        if document is None:
            return {
                "ok": False,
                "blocked": True,
                "reason": error or "ownership snapshot is invalid",
                "path": str(self.ownership_path),
                "exists": self.ownership_path.exists(),
                "snapshot": None,
            }
        return {
            "ok": True,
            "blocked": False,
            "reason": "",
            "path": str(self.ownership_path),
            "exists": self.ownership_path.exists(),
            "snapshot": deepcopy(document),
        }

    def claim_event_candidate(
        self,
        *,
        account_no: object,
        trading_day: object,
        source_order_id: object,
        source_stock_code: object,
        selected_stock_code: object,
        detected_at: object,
        expected_revision: object,
    ) -> dict[str, object]:
        try:
            account = _canonical_account_no(account_no)
            day = _canonical_trading_day(trading_day)
            order_id = _canonical_order_id(source_order_id)
            source_code = _canonical_stock_code(source_stock_code, field="source_stock_code")
            selected_code = _canonical_stock_code(
                selected_stock_code, field="selected_stock_code"
            )
            detected = _canonical_timestamp(detected_at, field="detected_at")
            if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
                raise ValueError("expected_revision must be a nonnegative integer")
            if expected_revision < 0:
                raise ValueError("expected_revision must be a nonnegative integer")
            event_id = buffer_response_event_id(
                account_no=account,
                trading_day=day,
                source_order_id=order_id,
            )
        except ValueError as exc:
            return self._blocked(str(exc))

        thread_lock = _path_thread_lock(self.ownership_path)
        try:
            with thread_lock:
                with _OwnershipFileLock(
                    self.ownership_path, self._lock_timeout_seconds
                ) as file_lock:
                    document, error = self._load()
                    if document is None:
                        return self._blocked(error, lock_wait_ms=file_lock.wait_ms)
                    if document.get("schema_version") != SCHEMA_VERSION:
                        return self._blocked(
                            "legacy BUY ownership claim cannot mutate a batch ownership document",
                            lock_wait_ms=file_lock.wait_ms,
                        )
                    events = document["events"]
                    assert isinstance(events, dict)
                    existing = events.get(event_id)
                    if isinstance(existing, dict):
                        return self._existing_claim_result(
                            existing,
                            proposed_stock_code=selected_code,
                            revision=int(document["revision"]),
                            lock_wait_ms=file_lock.wait_ms,
                        )
                    revision_before = int(document["revision"])
                    if revision_before != expected_revision:
                        return self._blocked(
                            "ownership revision changed after preview",
                            revision_before=revision_before,
                            revision_after=revision_before,
                            expected_revision=expected_revision,
                            lock_wait_ms=file_lock.wait_ms,
                        )
                    selected_at = self._now_text()
                    event = {
                        "event_id": event_id,
                        "account_no": account,
                        "trading_day": day,
                        "source_order_id": order_id,
                        "source_stock_code": source_code,
                        "detected_at": detected,
                        "status": STATUS_OWNED,
                        "selected_stock_code": selected_code,
                        "selected_at": selected_at,
                        "completion": None,
                    }
                    updated = deepcopy(document)
                    updated_events = updated["events"]
                    assert isinstance(updated_events, dict)
                    updated_events[event_id] = event
                    updated["revision"] = revision_before + 1
                    updated["updated_at"] = selected_at
                    return self._write_and_verify(
                        updated,
                        event_id=event_id,
                        expected_event=event,
                        operation="CLAIMED",
                        revision_before=revision_before,
                        expected_revision=expected_revision,
                        lock_wait_ms=file_lock.wait_ms,
                    )
        except (OSError, TimeoutError, ValueError) as exc:
            return self._blocked(str(exc))

    def claim_batch_event_candidate(
        self,
        *,
        account_no: object,
        trading_day: object,
        event_sequence: object,
        source_evidence: object,
        selected_stock_code: object,
        response_intent: object,
        detected_at: object,
        expected_revision: object,
    ) -> dict[str, object]:
        """Atomically bind one confirmed ingress batch to one selected stock."""

        try:
            account = _canonical_account_no(account_no)
            day = _canonical_trading_day(trading_day)
            if isinstance(event_sequence, bool) or not isinstance(event_sequence, int):
                raise ValueError("event_sequence must be a positive integer")
            if event_sequence <= 0:
                raise ValueError("event_sequence must be a positive integer")
            evidence = _validate_source_evidence(source_evidence)
            selected_code = _canonical_stock_code(
                selected_stock_code, field="selected_stock_code"
            )
            intent = _canonical_response_intent(response_intent)
            detected = _canonical_timestamp(detected_at, field="detected_at")
            if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
                raise ValueError("expected_revision must be a nonnegative integer")
            if expected_revision < 0:
                raise ValueError("expected_revision must be a nonnegative integer")
            event_id = buffer_response_batch_event_id(
                account_no=account,
                trading_day=day,
                event_sequence=event_sequence,
            )
        except ValueError as exc:
            return self._blocked(str(exc))

        thread_lock = _path_thread_lock(self.ownership_path)
        try:
            with thread_lock:
                with _OwnershipFileLock(
                    self.ownership_path, self._lock_timeout_seconds
                ) as file_lock:
                    document, error = self._load(
                        missing_schema_version=BATCH_SCHEMA_VERSION
                    )
                    if document is None:
                        return self._blocked(error, lock_wait_ms=file_lock.wait_ms)
                    if document.get("schema_version") != BATCH_SCHEMA_VERSION:
                        return self._blocked(
                            "batch claim requires a batch ownership document; automatic v1 migration is forbidden",
                            lock_wait_ms=file_lock.wait_ms,
                        )
                    events = document["events"]
                    assert isinstance(events, dict)
                    existing = events.get(event_id)
                    if isinstance(existing, dict):
                        if existing.get("source_evidence") != evidence:
                            return self._blocked(
                                "same batch sequence has different source evidence",
                                revision_before=int(document["revision"]),
                                revision_after=int(document["revision"]),
                                expected_revision=expected_revision,
                                lock_wait_ms=file_lock.wait_ms,
                            )
                        return self._existing_claim_result(
                            existing,
                            proposed_stock_code=selected_code,
                            proposed_response_intent=intent,
                            revision=int(document["revision"]),
                            lock_wait_ms=file_lock.wait_ms,
                        )
                    revision_before = int(document["revision"])
                    if revision_before != expected_revision:
                        return self._blocked(
                            "ownership revision changed after preview",
                            revision_before=revision_before,
                            revision_after=revision_before,
                            expected_revision=expected_revision,
                            lock_wait_ms=file_lock.wait_ms,
                        )
                    selected_at = self._now_text()
                    event = {
                        "event_id": event_id,
                        "account_no": account,
                        "trading_day": day,
                        "event_sequence": event_sequence,
                        "source_evidence": evidence,
                        "detected_at": detected,
                        "status": STATUS_OWNED,
                        "selected_stock_code": selected_code,
                        "response_intent": intent,
                        "selected_at": selected_at,
                        "completion": None,
                    }
                    updated = deepcopy(document)
                    updated_events = updated["events"]
                    assert isinstance(updated_events, dict)
                    updated_events[event_id] = event
                    updated["revision"] = revision_before + 1
                    updated["updated_at"] = selected_at
                    return self._write_and_verify(
                        updated,
                        event_id=event_id,
                        expected_event=event,
                        operation="CLAIMED",
                        revision_before=revision_before,
                        expected_revision=expected_revision,
                        lock_wait_ms=file_lock.wait_ms,
                    )
        except (OSError, TimeoutError, ValueError) as exc:
            return self._blocked(str(exc))

    def mark_completed(
        self,
        *,
        event_id: object,
        completion_projection: object,
        expected_revision: object,
        observed_at: object | None = None,
    ) -> dict[str, object]:
        identity = _text(event_id)
        if not identity:
            return self._blocked("event_id is required")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            return self._blocked("expected_revision must be a nonnegative integer")

        thread_lock = _path_thread_lock(self.ownership_path)
        try:
            with thread_lock:
                with _OwnershipFileLock(
                    self.ownership_path, self._lock_timeout_seconds
                ) as file_lock:
                    document, error = self._load()
                    if document is None:
                        return self._blocked(error, lock_wait_ms=file_lock.wait_ms)
                    events = document["events"]
                    assert isinstance(events, dict)
                    existing = events.get(identity)
                    if not isinstance(existing, dict):
                        return self._blocked(
                            "ownership event was not found", lock_wait_ms=file_lock.wait_ms
                        )
                    if existing.get("status") == STATUS_COMPLETED:
                        return {
                            "ok": True,
                            "blocked": False,
                            "changed": False,
                            "operation": "ALREADY_COMPLETED",
                            "event_id": identity,
                            "selected_stock_code": existing["selected_stock_code"],
                            "status": STATUS_COMPLETED,
                            "revision_before": document["revision"],
                            "revision_after": document["revision"],
                            "event": deepcopy(existing),
                            "reason": "",
                        }
                    completion_status, completion_error = self._completion_status(
                        existing, completion_projection
                    )
                    if completion_error:
                        return self._blocked(
                            completion_error,
                            revision_before=int(document["revision"]),
                            revision_after=int(document["revision"]),
                            expected_revision=expected_revision,
                            lock_wait_ms=file_lock.wait_ms,
                        )
                    revision_before = int(document["revision"])
                    if revision_before != expected_revision:
                        return self._blocked(
                            "ownership revision changed after preview",
                            revision_before=revision_before,
                            revision_after=revision_before,
                            expected_revision=expected_revision,
                            lock_wait_ms=file_lock.wait_ms,
                        )
                    completed_at = (
                        _canonical_timestamp(observed_at, field="observed_at")
                        if observed_at is not None
                        else self._now_text()
                    )
                    updated = deepcopy(document)
                    updated_events = updated["events"]
                    assert isinstance(updated_events, dict)
                    completed_event = deepcopy(existing)
                    completed_event["status"] = STATUS_COMPLETED
                    completed_event["completion"] = {
                        "evaluator_status": completion_status,
                        "observed_at": completed_at,
                    }
                    updated_events[identity] = completed_event
                    updated["revision"] = revision_before + 1
                    updated["updated_at"] = completed_at
                    return self._write_and_verify(
                        updated,
                        event_id=identity,
                        expected_event=completed_event,
                        operation="COMPLETED",
                        revision_before=revision_before,
                        expected_revision=expected_revision,
                        lock_wait_ms=file_lock.wait_ms,
                    )
        except (OSError, TimeoutError, ValueError) as exc:
            return self._blocked(str(exc))

    def promote_owned_early_close_to_immediate(
        self,
        *,
        event_id: object,
        expected_revision: object,
        observed_at: object | None = None,
    ) -> dict[str, object]:
        """Promote one existing OWNED event without changing its target identity.

        This is the only allowed interval-close escalation transition.  It
        deliberately does not create an event, select a new stock, complete an
        ownership, or execute a close command.
        """

        identity = _text(event_id)
        if not identity:
            return self._blocked("event_id is required")
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            return self._blocked("expected_revision must be a nonnegative integer")

        thread_lock = _path_thread_lock(self.ownership_path)
        try:
            with thread_lock:
                with _OwnershipFileLock(
                    self.ownership_path, self._lock_timeout_seconds
                ) as file_lock:
                    document, error = self._load()
                    if document is None:
                        return self._blocked(error, lock_wait_ms=file_lock.wait_ms)
                    if document.get("schema_version") != BATCH_SCHEMA_VERSION:
                        return self._blocked(
                            "response intent promotion requires ownership schema v3",
                            lock_wait_ms=file_lock.wait_ms,
                        )
                    events = document["events"]
                    assert isinstance(events, dict)
                    existing = events.get(identity)
                    if not isinstance(existing, dict):
                        return self._blocked(
                            "ownership event was not found",
                            lock_wait_ms=file_lock.wait_ms,
                        )
                    if existing.get("status") == STATUS_COMPLETED:
                        return {
                            "ok": True,
                            "blocked": False,
                            "changed": False,
                            "operation": "ALREADY_COMPLETED",
                            "event_id": identity,
                            "selected_stock_code": existing["selected_stock_code"],
                            "status": STATUS_COMPLETED,
                            "response_intent": existing.get("response_intent"),
                            "revision_before": document["revision"],
                            "revision_after": document["revision"],
                            "event": deepcopy(existing),
                            "reason": "",
                        }
                    if existing.get("status") != STATUS_OWNED:
                        return self._blocked(
                            "ownership event is not active",
                            lock_wait_ms=file_lock.wait_ms,
                        )
                    current_intent = _text(existing.get("response_intent")).upper()
                    if current_intent == RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED:
                        return {
                            "ok": True,
                            "blocked": False,
                            "changed": False,
                            "operation": "ALREADY_PROMOTED",
                            "event_id": identity,
                            "selected_stock_code": existing["selected_stock_code"],
                            "status": STATUS_OWNED,
                            "response_intent": current_intent,
                            "revision_before": document["revision"],
                            "revision_after": document["revision"],
                            "event": deepcopy(existing),
                            "reason": "",
                        }
                    if current_intent != RESPONSE_INTENT_EARLY_CLOSE:
                        return self._blocked(
                            "only an OWNED EARLY_CLOSE event can be promoted",
                            lock_wait_ms=file_lock.wait_ms,
                        )
                    revision_before = int(document["revision"])
                    if revision_before != expected_revision:
                        return self._blocked(
                            "ownership revision changed after preview",
                            revision_before=revision_before,
                            revision_after=revision_before,
                            expected_revision=expected_revision,
                            lock_wait_ms=file_lock.wait_ms,
                        )
                    promoted_at = (
                        _canonical_timestamp(observed_at, field="observed_at")
                        if observed_at is not None
                        else self._now_text()
                    )
                    updated = deepcopy(document)
                    updated_events = updated["events"]
                    assert isinstance(updated_events, dict)
                    promoted_event = deepcopy(existing)
                    promoted_event["response_intent"] = (
                        RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED
                    )
                    updated_events[identity] = promoted_event
                    updated["revision"] = revision_before + 1
                    updated["updated_at"] = promoted_at
                    return self._write_and_verify(
                        updated,
                        event_id=identity,
                        expected_event=promoted_event,
                        operation="PROMOTED_TO_IMMEDIATE",
                        revision_before=revision_before,
                        expected_revision=expected_revision,
                        lock_wait_ms=file_lock.wait_ms,
                    )
        except (OSError, TimeoutError, ValueError) as exc:
            return self._blocked(str(exc))

    def active_owned_stock_codes(
        self,
        *,
        account_no: object,
        trading_day: object,
    ) -> dict[str, object]:
        try:
            account = _canonical_account_no(account_no)
            day = _canonical_trading_day(trading_day)
        except ValueError as exc:
            return self._blocked(str(exc))
        snapshot = self.read_snapshot()
        if snapshot.get("ok") is not True:
            return snapshot
        document = snapshot["snapshot"]
        assert isinstance(document, dict)
        events = document["events"]
        assert isinstance(events, dict)
        codes = sorted(
            {
                str(event["selected_stock_code"])
                for event in events.values()
                if isinstance(event, dict)
                and event.get("account_no") == account
                and event.get("trading_day") == day
                and event.get("status") == STATUS_OWNED
            }
        )
        return {
            "ok": True,
            "blocked": False,
            "reason": "",
            "account_no": account,
            "trading_day": day,
            "stock_codes": tuple(codes),
            "count": len(codes),
            "revision": document["revision"],
        }

    @staticmethod
    def _completion_status(
        event: Mapping[str, object], projection: object
    ) -> tuple[str, str]:
        if not isinstance(projection, Mapping):
            return "", "completion projection is required"
        if projection.get("evaluated") is not True or projection.get("blocked") is True:
            return "", "completion projection is not canonical-ready"
        operation_date = _text(projection.get("operation_date"))
        if operation_date != event.get("trading_day"):
            return "", "completion projection trading day does not match ownership"
        evidence_errors = projection.get("evidence_errors")
        if not isinstance(evidence_errors, (list, tuple)) or evidence_errors:
            return "", "completion projection contains unavailable evidence"
        stock_results = projection.get("stock_results")
        if not isinstance(stock_results, (list, tuple)):
            return "", "completion projection stock_results is unavailable"
        selected_code = event.get("selected_stock_code")
        matches = [
            item
            for item in stock_results
            if isinstance(item, Mapping)
            and normalize_stock_code(item.get("stock_code")) == selected_code
        ]
        if len(matches) != 1:
            return "", "completion projection does not contain exactly one selected stock"
        status = _text(matches[0].get("status")).upper()
        if status not in COMPLETION_STATUSES:
            return "", f"completion status is not complete: {status or 'missing'}"
        reasons = matches[0].get("reasons")
        if not isinstance(reasons, (list, tuple)) or reasons:
            return "", "completion stock result contains unresolved reasons"
        return status, ""

    def _write_and_verify(
        self,
        document: dict[str, object],
        *,
        event_id: str,
        expected_event: Mapping[str, object],
        operation: str,
        revision_before: int,
        expected_revision: int,
        lock_wait_ms: int,
    ) -> dict[str, object]:
        try:
            validated = _validate_document(document)
        except ValueError as exc:
            return self._blocked(str(exc), lock_wait_ms=lock_wait_ms)
        write_result = write_json_atomic(self.ownership_path, validated)
        if write_result.get("status") != STATUS_OK or write_result.get("written") is not True:
            return self._blocked(
                _text(write_result.get("error")) or "ownership atomic write failed",
                revision_before=revision_before,
                revision_after=revision_before,
                expected_revision=expected_revision,
                lock_wait_ms=lock_wait_ms,
            )
        read_back, error = self._load()
        revision_after = revision_before + 1
        if (
            read_back is None
            or int(read_back.get("revision", -1)) != revision_after
            or not isinstance(read_back.get("events"), dict)
            or read_back["events"].get(event_id) != dict(expected_event)
        ):
            return self._blocked(
                error or "ownership post-write verification failed",
                revision_before=revision_before,
                revision_after=revision_after,
                expected_revision=expected_revision,
                lock_wait_ms=lock_wait_ms,
                changed=True,
            )
        return {
            "ok": True,
            "blocked": False,
            "changed": True,
            "operation": operation,
            "event_id": event_id,
            "selected_stock_code": expected_event["selected_stock_code"],
            "response_intent": expected_event.get("response_intent"),
            "status": expected_event["status"],
            "revision_before": revision_before,
            "revision_after": revision_after,
            "expected_revision": expected_revision,
            "lock_wait_ms": lock_wait_ms,
            "post_write_verified": True,
            "event": deepcopy(dict(expected_event)),
            "reason": "",
        }

    @staticmethod
    def _existing_claim_result(
        event: Mapping[str, object],
        *,
        proposed_stock_code: str,
        proposed_response_intent: str | None = None,
        revision: int,
        lock_wait_ms: int,
    ) -> dict[str, object]:
        status = _text(event.get("status")).upper()
        selected_code = _text(event.get("selected_stock_code"))
        response_intent = _text(event.get("response_intent"))
        return {
            "ok": True,
            "blocked": False,
            "changed": False,
            "operation": (
                "ALREADY_COMPLETED" if status == STATUS_COMPLETED else "ALREADY_OWNED"
            ),
            "event_id": event["event_id"],
            "selected_stock_code": selected_code,
            "response_intent": response_intent,
            "proposed_stock_code": proposed_stock_code,
            "proposal_ignored": proposed_stock_code != selected_code,
            "proposed_response_intent": proposed_response_intent or "",
            "intent_proposal_ignored": bool(
                proposed_response_intent
                and proposed_response_intent != response_intent
            ),
            "status": status,
            "revision_before": revision,
            "revision_after": revision,
            "lock_wait_ms": lock_wait_ms,
            "post_write_verified": True,
            "event": deepcopy(dict(event)),
            "reason": "",
        }

    def _blocked(self, reason: str, **context: object) -> dict[str, object]:
        result: dict[str, object] = {
            "ok": False,
            "blocked": True,
            "changed": bool(context.pop("changed", False)),
            "reason": _text(reason) or "ownership operation blocked",
            "path": str(self.ownership_path),
            "post_write_verified": False,
        }
        result.update(context)
        return result


__all__ = [
    "BATCH_SCHEMA_VERSION",
    "BufferResponseOwnershipService",
    "DEFAULT_OWNERSHIP_PATH",
    "LEGACY_BATCH_SCHEMA_VERSION",
    "RESPONSE_INTENT_EARLY_CLOSE",
    "RESPONSE_INTENT_IMMEDIATE_LIQUIDATION_REQUIRED",
    "RESPONSE_INTENTS",
    "SCHEMA_VERSION",
    "STATUS_COMPLETED",
    "STATUS_OWNED",
    "buffer_response_batch_event_id",
    "buffer_response_event_id",
    "validate_batch_ownership_event",
]
