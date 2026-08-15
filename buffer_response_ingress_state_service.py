# -*- coding: utf-8 -*-
"""Durable checkpoint foundation for confirmed buffer-response ingress.

This module is intentionally disconnected from MainWindow, candidate selection,
close commands, and broker execution.  It owns only stable-observation
checkpoint state.  All tests must supply explicit snapshots and temp paths.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import msvcrt
from pathlib import Path
import threading
import time
from typing import Any, Callable, Iterable, Mapping

from buffer_response_ownership_service import (
    STATUS_COMPLETED,
    STATUS_OWNED,
    buffer_response_batch_event_id,
    validate_batch_ownership_event,
)
from production_recovery_contract import normalize_stock_code
from runtime_atomic_writer import STATUS_OK, write_json_atomic


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_INGRESS_PATH = PROJECT_ROOT / "runtime" / "buffer_response_ingress_state.json"
SCHEMA_VERSION = 1

_ROOT_KEYS = frozenset({"schema_version", "revision", "updated_at", "checkpoints"})
_CHECKPOINT_KEYS = frozenset(
    {
        "account_no",
        "trading_day",
        "last_confirmed_entry_amount",
        "last_confirmed_observation_id",
        "last_confirmed_observed_at",
        "last_confirmed_evidence",
        "seen_contributing_buy_ids",
        "last_event_sequence",
    }
)
_EVIDENCE_KEYS = frozenset(
    {
        "recovery_session_id",
        "queue_revision",
        "order_queue_sha256",
        "positions_sha256",
        "fills_sha256",
    }
)
_OBSERVATION_KEYS = frozenset(
    {
        "account_no",
        "trading_day",
        "confirmed_entry_amount",
        "observation_id",
        "observed_at",
        "evidence",
        "contributing_buy_ids",
    }
)
_OPEN_BROKER_STATUSES = frozenset({"BROKER_ACCEPTED", "PARTIALLY_FILLED"})
_AMBIGUOUS_SEND_STATUSES = frozenset(
    {"SEND_CALL_IN_PROGRESS", "SEND_CALL_ACCEPTED", "SEND_UNCERTAIN"}
)
_THREAD_LOCKS_GUARD = threading.RLock()
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_DEFAULT_LOCK_TIMEOUT_SECONDS = 5.0


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _canonical_account_no(value: object) -> str:
    account = _text(value)
    if not account:
        raise ValueError("account_no is required")
    return account


def _canonical_trading_day(value: object) -> str:
    try:
        return date.fromisoformat(_text(value)).isoformat()
    except ValueError as exc:
        raise ValueError("trading_day must be YYYY-MM-DD") from exc


def _canonical_timestamp(value: object, *, field: str) -> str:
    text = _text(value)
    if not text:
        raise ValueError(f"{field} is required")
    try:
        datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be ISO-8601") from exc
    return text


def _canonical_nonnegative_integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    return value


def _integer(value: object, *, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    text = _text(value).replace(",", "")
    if not text:
        raise ValueError(f"{field} is required")
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} is invalid") from exc
    if not number.is_finite() or number != number.to_integral_value():
        raise ValueError(f"{field} must be an integer")
    result = int(number)
    if result < minimum:
        raise ValueError(f"{field} must be at least {minimum}")
    return result


def _canonical_sha256(value: object, *, field: str) -> str:
    digest = _text(value).upper()
    if len(digest) != 64 or any(ch not in "0123456789ABCDEF" for ch in digest):
        raise ValueError(f"{field} must be an uppercase SHA-256")
    return digest


def _canonical_identity_list(value: object, *, field: str) -> list[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise ValueError(f"{field} must be a collection")
    identities = sorted({_text(item) for item in value})
    if any(not item for item in identities):
        raise ValueError(f"{field} contains an empty identity")
    return identities


def _stable_hash(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest().upper()


def _checkpoint_key(account_no: str, trading_day: str) -> str:
    return f"ACCOUNT_DAY_{_stable_hash({'account_no': account_no, 'trading_day': trading_day})}"


def _empty_document() -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": 0,
        "updated_at": None,
        "checkpoints": {},
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
        raise ValueError("ingress root must be an object")
    return value


def _validate_evidence(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _EVIDENCE_KEYS:
        raise ValueError("confirmed evidence schema is invalid")
    recovery_session_id = _text(value.get("recovery_session_id"))
    if not recovery_session_id:
        raise ValueError("recovery_session_id is required")
    return {
        "recovery_session_id": recovery_session_id,
        "queue_revision": _canonical_nonnegative_integer(
            value.get("queue_revision"), field="queue_revision"
        ),
        "order_queue_sha256": _canonical_sha256(
            value.get("order_queue_sha256"), field="order_queue_sha256"
        ),
        "positions_sha256": _canonical_sha256(
            value.get("positions_sha256"), field="positions_sha256"
        ),
        "fills_sha256": _canonical_sha256(value.get("fills_sha256"), field="fills_sha256"),
    }


def _validate_observation(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _OBSERVATION_KEYS:
        raise ValueError("stable observation schema is invalid")
    observation = {
        "account_no": _canonical_account_no(value.get("account_no")),
        "trading_day": _canonical_trading_day(value.get("trading_day")),
        "confirmed_entry_amount": _canonical_nonnegative_integer(
            value.get("confirmed_entry_amount"), field="confirmed_entry_amount"
        ),
        "observation_id": _canonical_sha256(
            value.get("observation_id"), field="observation_id"
        ),
        "observed_at": _canonical_timestamp(value.get("observed_at"), field="observed_at"),
        "evidence": _validate_evidence(value.get("evidence")),
        "contributing_buy_ids": _canonical_identity_list(
            value.get("contributing_buy_ids"), field="contributing_buy_ids"
        ),
    }
    expected_id = stable_buffer_observation_id(
        account_no=observation["account_no"],
        trading_day=observation["trading_day"],
        confirmed_entry_amount=observation["confirmed_entry_amount"],
        contributing_buy_ids=observation["contributing_buy_ids"],
        evidence=observation["evidence"],
    )
    if observation["observation_id"] != expected_id:
        raise ValueError("observation_id does not match stable evidence")
    return observation


def _validate_checkpoint(key: object, value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _CHECKPOINT_KEYS:
        raise ValueError("ingress checkpoint schema is invalid")
    account = _canonical_account_no(value.get("account_no"))
    day = _canonical_trading_day(value.get("trading_day"))
    if _text(key) != _checkpoint_key(account, day):
        raise ValueError("ingress checkpoint key does not match account/day")
    return {
        "account_no": account,
        "trading_day": day,
        "last_confirmed_entry_amount": _canonical_nonnegative_integer(
            value.get("last_confirmed_entry_amount"),
            field="last_confirmed_entry_amount",
        ),
        "last_confirmed_observation_id": _canonical_sha256(
            value.get("last_confirmed_observation_id"),
            field="last_confirmed_observation_id",
        ),
        "last_confirmed_observed_at": _canonical_timestamp(
            value.get("last_confirmed_observed_at"),
            field="last_confirmed_observed_at",
        ),
        "last_confirmed_evidence": _validate_evidence(value.get("last_confirmed_evidence")),
        "seen_contributing_buy_ids": _canonical_identity_list(
            value.get("seen_contributing_buy_ids"), field="seen_contributing_buy_ids"
        ),
        "last_event_sequence": _canonical_nonnegative_integer(
            value.get("last_event_sequence"), field="last_event_sequence"
        ),
    }


def _validate_document(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _ROOT_KEYS:
        raise ValueError("ingress root schema is invalid")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("ingress schema_version is unsupported")
    revision = _canonical_nonnegative_integer(value.get("revision"), field="revision")
    updated_at = value.get("updated_at")
    if revision == 0:
        if updated_at is not None:
            raise ValueError("empty ingress snapshot updated_at must be null")
    else:
        updated_at = _canonical_timestamp(updated_at, field="updated_at")
    raw = value.get("checkpoints")
    if not isinstance(raw, dict):
        raise ValueError("ingress checkpoints must be an object")
    checkpoints = {_text(key): _validate_checkpoint(key, item) for key, item in raw.items()}
    if revision == 0 and checkpoints:
        raise ValueError("revision zero ingress snapshot must be empty")
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": revision,
        "updated_at": updated_at,
        "checkpoints": checkpoints,
    }


def stable_buffer_observation_id(
    *,
    account_no: object,
    trading_day: object,
    confirmed_entry_amount: object,
    contributing_buy_ids: Iterable[object],
    evidence: object,
) -> str:
    identity = {
        "account_no": _canonical_account_no(account_no),
        "trading_day": _canonical_trading_day(trading_day),
        "confirmed_entry_amount": _canonical_nonnegative_integer(
            confirmed_entry_amount, field="confirmed_entry_amount"
        ),
        "contributing_buy_ids": _canonical_identity_list(
            tuple(contributing_buy_ids), field="contributing_buy_ids"
        ),
        "evidence": _validate_evidence(evidence),
    }
    return _stable_hash(identity)


def build_stable_buffer_observation(
    *,
    account_no: object,
    trading_day: object,
    confirmed_entry_amount: object,
    contributing_buy_ids: Iterable[object],
    evidence_before: object,
    evidence_after: object,
    observed_at: object,
) -> dict[str, object]:
    """Build an observation only when the double-snapshot evidence is stable."""

    try:
        before = _validate_evidence(evidence_before)
        after = _validate_evidence(evidence_after)
        if before != after:
            raise ValueError("stable observation evidence changed during projection")
        account = _canonical_account_no(account_no)
        day = _canonical_trading_day(trading_day)
        amount = _canonical_nonnegative_integer(
            confirmed_entry_amount, field="confirmed_entry_amount"
        )
        contributors = _canonical_identity_list(
            tuple(contributing_buy_ids), field="contributing_buy_ids"
        )
        timestamp = _canonical_timestamp(observed_at, field="observed_at")
        observation_id = stable_buffer_observation_id(
            account_no=account,
            trading_day=day,
            confirmed_entry_amount=amount,
            contributing_buy_ids=contributors,
            evidence=before,
        )
        return {
            "available": True,
            "blocked": False,
            "reason": "",
            "observation": {
                "account_no": account,
                "trading_day": day,
                "confirmed_entry_amount": amount,
                "observation_id": observation_id,
                "observed_at": timestamp,
                "evidence": before,
                "contributing_buy_ids": contributors,
            },
        }
    except ValueError as exc:
        return {"available": False, "blocked": True, "reason": str(exc), "observation": None}


def _record_account(record: Mapping[str, object]) -> str:
    execution_request = _as_dict(record.get("execution_request"))
    request_preview = _as_dict(execution_request.get("request_preview"))
    guard_snapshot = _as_dict(execution_request.get("guard_snapshot"))
    candidates = {
        _text(value)
        for value in (
            record.get("account_no"),
            request_preview.get("account_no"),
            guard_snapshot.get("account_no"),
        )
        if _text(value)
    }
    if len(candidates) > 1:
        raise ValueError("order account identity is inconsistent")
    return next(iter(candidates), "")


def _request_preview(record: Mapping[str, object]) -> dict[str, Any]:
    return _as_dict(_as_dict(record.get("execution_request")).get("request_preview"))


def _order_value(record: Mapping[str, object], field: str) -> object:
    return record.get(field) if record.get(field) not in (None, "") else _request_preview(record).get(field)


def collect_confirmed_contributing_buy_ids(
    *,
    account_no: object,
    positions_snapshot: object,
    order_queue_snapshot: object,
    fills_snapshot: object,
    reconciled_stock_codes: Iterable[object],
) -> dict[str, object]:
    """Collect only BUY identities proven to contribute to confirmed consumption."""

    try:
        account = _canonical_account_no(account_no)
        positions_root = _as_dict(positions_snapshot)
        queue_root = _as_dict(order_queue_snapshot)
        fills_root = _as_dict(fills_snapshot)
        positions = positions_root.get("positions")
        orders = queue_root.get("orders")
        fills = fills_root.get("fills")
        if not isinstance(positions, list) or any(not isinstance(item, dict) for item in positions):
            raise ValueError("positions snapshot is invalid")
        if not isinstance(orders, list) or any(not isinstance(item, dict) for item in orders):
            raise ValueError("order queue snapshot is invalid")
        if not isinstance(fills, list) or any(not isinstance(item, dict) for item in fills):
            raise ValueError("fills snapshot is invalid")
        reconciled = {
            normalize_stock_code(item)
            for item in reconciled_stock_codes
            if normalize_stock_code(item)
        }

        reservation_ids: set[str] = set()
        for order in orders:
            status = _text(order.get("status")).upper()
            side = _text(order.get("side") or _request_preview(order).get("side")).upper()
            if side != "BUY":
                continue
            order_account = _record_account(order)
            if status in _AMBIGUOUS_SEND_STATUSES | _OPEN_BROKER_STATUSES:
                if not order_account:
                    raise ValueError("active BUY contributor account_no is required")
                if order_account != account:
                    continue
            if status in _AMBIGUOUS_SEND_STATUSES:
                raise ValueError(f"BUY order lifecycle is unresolved: {status}")
            if status not in _OPEN_BROKER_STATUSES:
                continue
            if _text(order.get("order_action") or _request_preview(order).get("order_action") or "NEW").upper() != "NEW":
                raise ValueError("active MODIFY/CANCEL BUY cannot provide contributor evidence")
            code = normalize_stock_code(
                order.get("code") or order.get("stock_code") or _request_preview(order).get("code")
            )
            if not code or code not in reconciled:
                raise ValueError("active BUY contributor is outside reconciled stock scope")
            if not _text(order.get("broker_order_no")):
                raise ValueError("active BUY contributor broker_order_no is required")
            if not _text(order.get("source_signal_id")):
                raise ValueError("active BUY contributor source_signal_id is required")
            identity = _text(order.get("id") or order.get("order_queued_id"))
            if not identity:
                raise ValueError("active BUY contributor order_queued_id is required")
            remaining_value = order.get("remaining_quantity")
            if remaining_value in (None, ""):
                remaining_value = _order_value(order, "quantity")
            price_value = order.get("order_price")
            if price_value in (None, ""):
                price_value = _order_value(order, "price")
            remaining = _integer(remaining_value, field="remaining_quantity", minimum=1)
            price = _integer(price_value, field="order_price", minimum=1)
            if remaining * price <= 0:
                raise ValueError("active BUY reservation is not positive")
            reservation_ids.add(identity)

        applied_order_ids: set[str] = set()
        open_position_count = 0
        for position in positions:
            if _text(position.get("account_no")) != account:
                continue
            quantity = _integer(position.get("quantity", 0), field="position.quantity")
            code = normalize_stock_code(position.get("code") or position.get("stock_code"))
            if quantity > 0:
                open_position_count += 1
                if not code or code not in reconciled:
                    raise ValueError("open position contributor is outside reconciled stock scope")
                _integer(position.get("cost_basis"), field="position.cost_basis", minimum=1)
            audit = position.get("last_applied_cumulative_by_order")
            if audit is None:
                audit = {}
            if not isinstance(audit, dict):
                raise ValueError("position cumulative order evidence is invalid")
            for key, cumulative in audit.items():
                key_text = _text(key)
                if not key_text.startswith("order_queued_id:"):
                    continue
                _integer(cumulative, field="position cumulative quantity", minimum=1)
                applied_order_ids.add(key_text.split(":", 1)[1])

        applied_buy_ids: set[str] = set()
        for fill in fills:
            if _text(fill.get("account_no")) != account or _text(fill.get("side")).upper() != "BUY":
                continue
            code = normalize_stock_code(fill.get("code") or fill.get("stock_code"))
            if not code or code not in reconciled:
                raise ValueError("BUY fill contributor is outside reconciled stock scope")
            identity = _text(fill.get("order_queued_id"))
            if not identity:
                raise ValueError("BUY fill order_queued_id is required")
            if identity in applied_order_ids:
                applied_buy_ids.add(identity)

        if open_position_count > 0 and not applied_buy_ids:
            raise ValueError("open position cost has no confirmed BUY application identity")
        contributors = sorted(reservation_ids | applied_buy_ids)
        return {
            "available": True,
            "blocked": False,
            "reason": "",
            "contributing_buy_ids": contributors,
            "reservation_contributing_buy_ids": sorted(reservation_ids),
            "position_contributing_buy_ids": sorted(applied_buy_ids),
        }
    except (ValueError, TypeError) as exc:
        return {
            "available": False,
            "blocked": True,
            "reason": str(exc),
            "contributing_buy_ids": None,
            "reservation_contributing_buy_ids": None,
            "position_contributing_buy_ids": None,
        }


def _path_thread_lock(path: Path) -> threading.RLock:
    key = str(path.resolve()).casefold()
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _THREAD_LOCKS[key] = lock
        return lock


class _IngressFileLock:
    def __init__(self, path: Path, timeout_seconds: float) -> None:
        self.lock_path = path.with_name(f"{path.name}.lock")
        self.timeout_seconds = max(0.0, float(timeout_seconds))
        self.handle: Any = None
        self.wait_ms = 0

    def __enter__(self) -> "_IngressFileLock":
        if not self.lock_path.parent.is_dir():
            raise FileNotFoundError("ingress parent directory does not exist")
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
                    raise TimeoutError("ingress file lock timeout")
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


class BufferResponseIngressStateService:
    """The sole writer for durable confirmed buffer-observation checkpoints."""

    def __init__(
        self,
        ingress_path: str | Path = DEFAULT_INGRESS_PATH,
        *,
        now_factory: Callable[[], object] | None = None,
        lock_timeout_seconds: float = _DEFAULT_LOCK_TIMEOUT_SECONDS,
    ) -> None:
        self.ingress_path = Path(ingress_path)
        self._now_factory = now_factory or (lambda: datetime.now().isoformat(timespec="seconds"))
        self._lock_timeout_seconds = lock_timeout_seconds

    def _now_text(self) -> str:
        value = self._now_factory()
        if isinstance(value, datetime):
            value = value.isoformat(timespec="seconds")
        return _canonical_timestamp(value, field="current timestamp")

    def _load(self) -> tuple[dict[str, object] | None, str]:
        if not self.ingress_path.exists():
            return _empty_document(), ""
        try:
            return _validate_document(_read_json_strict(self.ingress_path)), ""
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return None, str(exc)

    def read_snapshot(self) -> dict[str, object]:
        document, error = self._load()
        if document is None:
            return self._blocked(error or "ingress snapshot is invalid")
        return {
            "ok": True,
            "blocked": False,
            "reason": "",
            "path": str(self.ingress_path),
            "exists": self.ingress_path.exists(),
            "snapshot": deepcopy(document),
        }

    @staticmethod
    def _checkpoint_from_observation(
        observation: Mapping[str, object], *, seen_ids: Iterable[object], event_sequence: int
    ) -> dict[str, object]:
        return {
            "account_no": observation["account_no"],
            "trading_day": observation["trading_day"],
            "last_confirmed_entry_amount": observation["confirmed_entry_amount"],
            "last_confirmed_observation_id": observation["observation_id"],
            "last_confirmed_observed_at": observation["observed_at"],
            "last_confirmed_evidence": deepcopy(observation["evidence"]),
            "seen_contributing_buy_ids": _canonical_identity_list(
                tuple(seen_ids), field="seen_contributing_buy_ids"
            ),
            "last_event_sequence": event_sequence,
        }

    @staticmethod
    def _prepare(document: Mapping[str, object], observation: Mapping[str, object]) -> dict[str, object]:
        checkpoints = document.get("checkpoints")
        assert isinstance(checkpoints, dict)
        key = _checkpoint_key(str(observation["account_no"]), str(observation["trading_day"]))
        existing = checkpoints.get(key)
        current_ids = set(observation["contributing_buy_ids"])
        if not isinstance(existing, dict):
            checkpoint = BufferResponseIngressStateService._checkpoint_from_observation(
                observation, seen_ids=current_ids, event_sequence=0
            )
            return {
                "baseline": True,
                "event_required": False,
                "event_id": None,
                "event_sequence": 0,
                "new_contributing_buy_ids": sorted(current_ids),
                "checkpoint_key": key,
                "checkpoint": checkpoint,
            }
        previous_amount = int(existing["last_confirmed_entry_amount"])
        previous_seen = set(existing["seen_contributing_buy_ids"])
        new_ids = sorted(current_ids - previous_seen)
        current_amount = int(observation["confirmed_entry_amount"])
        event_required = current_amount > previous_amount and current_amount > 0 and bool(new_ids)
        event_sequence = int(existing["last_event_sequence"]) + (1 if event_required else 0)
        checkpoint = BufferResponseIngressStateService._checkpoint_from_observation(
            observation,
            seen_ids=previous_seen | current_ids,
            event_sequence=event_sequence,
        )
        event_id = (
            buffer_response_batch_event_id(
                account_no=observation["account_no"],
                trading_day=observation["trading_day"],
                event_sequence=event_sequence,
            )
            if event_required
            else None
        )
        return {
            "baseline": False,
            "event_required": event_required,
            "event_id": event_id,
            "event_sequence": event_sequence,
            "new_contributing_buy_ids": new_ids,
            "previous_entry_amount": previous_amount,
            "checkpoint_key": key,
            "checkpoint": checkpoint,
        }

    def preview_observation(self, observation: object) -> dict[str, object]:
        try:
            normalized = _validate_observation(observation)
        except ValueError as exc:
            return self._blocked(str(exc))
        snapshot = self.read_snapshot()
        if snapshot.get("ok") is not True:
            return snapshot
        document = snapshot["snapshot"]
        assert isinstance(document, dict)
        prepared = self._prepare(document, normalized)
        result = {
            "ok": True,
            "blocked": False,
            "changed": False,
            "reason": "",
            "expected_revision": document["revision"],
            "observation": normalized,
        }
        result.update(prepared)
        return result

    def commit_stable_observation(
        self, *, observation: object, expected_revision: object
    ) -> dict[str, object]:
        return self._commit(
            observation=observation,
            expected_revision=expected_revision,
            claimed_event=None,
            require_event=False,
            require_ownership=False,
            operation="STABLE_OBSERVATION_COMMITTED",
        )

    def commit_event_observation(
        self,
        *,
        observation: object,
        claimed_event: object,
        expected_revision: object,
    ) -> dict[str, object]:
        return self._commit(
            observation=observation,
            expected_revision=expected_revision,
            claimed_event=claimed_event,
            require_event=True,
            require_ownership=True,
            operation="EVENT_OBSERVATION_COMMITTED",
        )

    def commit_unclaimed_event_observation(
        self, *, observation: object, expected_revision: object
    ) -> dict[str, object]:
        """Consume one confirmed increase when no response ownership can be claimed."""

        return self._commit(
            observation=observation,
            expected_revision=expected_revision,
            claimed_event=None,
            require_event=True,
            require_ownership=False,
            operation="EVENT_OBSERVATION_CONSUMED_WITHOUT_OWNERSHIP",
        )

    def _commit(
        self,
        *,
        observation: object,
        expected_revision: object,
        claimed_event: object | None,
        require_event: bool,
        require_ownership: bool,
        operation: str,
    ) -> dict[str, object]:
        try:
            normalized = _validate_observation(observation)
            expected = _canonical_nonnegative_integer(
                expected_revision, field="expected_revision"
            )
        except ValueError as exc:
            return self._blocked(str(exc))
        thread_lock = _path_thread_lock(self.ingress_path)
        try:
            with thread_lock:
                with _IngressFileLock(self.ingress_path, self._lock_timeout_seconds) as file_lock:
                    document, error = self._load()
                    if document is None:
                        return self._blocked(error, lock_wait_ms=file_lock.wait_ms)
                    revision_before = int(document["revision"])
                    if revision_before != expected:
                        return self._blocked(
                            "ingress revision changed after preview",
                            revision_before=revision_before,
                            revision_after=revision_before,
                            expected_revision=expected,
                            lock_wait_ms=file_lock.wait_ms,
                        )
                    prepared = self._prepare(document, normalized)
                    checkpoints = document.get("checkpoints")
                    assert isinstance(checkpoints, dict)
                    existing_checkpoint = checkpoints.get(prepared["checkpoint_key"])
                    prepared_checkpoint = prepared["checkpoint"]
                    if (
                        isinstance(existing_checkpoint, dict)
                        and existing_checkpoint.get("last_confirmed_observation_id")
                        == prepared_checkpoint.get("last_confirmed_observation_id")
                        and existing_checkpoint.get("last_confirmed_entry_amount")
                        == prepared_checkpoint.get("last_confirmed_entry_amount")
                        and existing_checkpoint.get("last_confirmed_evidence")
                        == prepared_checkpoint.get("last_confirmed_evidence")
                        and existing_checkpoint.get("seen_contributing_buy_ids")
                        == prepared_checkpoint.get("seen_contributing_buy_ids")
                        and existing_checkpoint.get("last_event_sequence")
                        == prepared_checkpoint.get("last_event_sequence")
                    ):
                        return {
                            "ok": True,
                            "blocked": False,
                            "changed": False,
                            "operation": "OBSERVATION_ALREADY_COMMITTED",
                            "event_id": prepared["event_id"],
                            "revision_before": revision_before,
                            "revision_after": revision_before,
                            "expected_revision": expected,
                            "lock_wait_ms": file_lock.wait_ms,
                            "post_write_verified": True,
                            "checkpoint": deepcopy(existing_checkpoint),
                            "reason": "",
                        }
                    if require_event is not bool(prepared["event_required"]):
                        reason = (
                            "event-producing observation requires a durable ownership claim"
                            if prepared["event_required"]
                            else "observation does not produce a new ingress event"
                        )
                        return self._blocked(reason, lock_wait_ms=file_lock.wait_ms)
                    if require_event and require_ownership:
                        try:
                            event = validate_batch_ownership_event(claimed_event)
                        except ValueError as exc:
                            return self._blocked(str(exc), lock_wait_ms=file_lock.wait_ms)
                        evidence = event["source_evidence"]
                        expected_source = {
                            "observation_id": normalized["observation_id"],
                            "observed_at": normalized["observed_at"],
                            "previous_entry_amount": prepared["previous_entry_amount"],
                            "current_entry_amount": normalized["confirmed_entry_amount"],
                            "new_contributing_buy_ids": prepared["new_contributing_buy_ids"],
                            "contributing_buy_ids": normalized["contributing_buy_ids"],
                            "confirmed_evidence": normalized["evidence"],
                        }
                        if (
                            event["event_id"] != prepared["event_id"]
                            or event["event_sequence"] != prepared["event_sequence"]
                            or event["account_no"] != normalized["account_no"]
                            or event["trading_day"] != normalized["trading_day"]
                            or evidence != expected_source
                            or event["status"] not in {STATUS_OWNED, STATUS_COMPLETED}
                        ):
                            return self._blocked(
                                "claimed ownership event does not match ingress observation",
                                lock_wait_ms=file_lock.wait_ms,
                            )
                    return self._write_checkpoint(
                        document,
                        checkpoint_key=str(prepared["checkpoint_key"]),
                        checkpoint=prepared["checkpoint"],
                        operation=operation,
                        expected_revision=expected,
                        lock_wait_ms=file_lock.wait_ms,
                        event_id=prepared["event_id"],
                    )
        except (OSError, TimeoutError, ValueError) as exc:
            return self._blocked(str(exc))

    def recover_claimed_event_checkpoint(
        self, *, claimed_event: object, expected_revision: object
    ) -> dict[str, object]:
        try:
            event = validate_batch_ownership_event(claimed_event)
            expected = _canonical_nonnegative_integer(
                expected_revision, field="expected_revision"
            )
        except ValueError as exc:
            return self._blocked(str(exc))
        thread_lock = _path_thread_lock(self.ingress_path)
        try:
            with thread_lock:
                with _IngressFileLock(self.ingress_path, self._lock_timeout_seconds) as file_lock:
                    document, error = self._load()
                    if document is None:
                        return self._blocked(error, lock_wait_ms=file_lock.wait_ms)
                    revision_before = int(document["revision"])
                    if revision_before != expected:
                        return self._blocked(
                            "ingress revision changed before crash bridge recovery",
                            revision_before=revision_before,
                            revision_after=revision_before,
                            expected_revision=expected,
                            lock_wait_ms=file_lock.wait_ms,
                        )
                    key = _checkpoint_key(str(event["account_no"]), str(event["trading_day"]))
                    checkpoints = document["checkpoints"]
                    assert isinstance(checkpoints, dict)
                    checkpoint = checkpoints.get(key)
                    if not isinstance(checkpoint, dict):
                        return self._blocked(
                            "crash bridge requires an existing baseline checkpoint",
                            lock_wait_ms=file_lock.wait_ms,
                        )
                    checkpoint_sequence = int(checkpoint["last_event_sequence"])
                    event_sequence = int(event["event_sequence"])
                    source = event["source_evidence"]
                    assert isinstance(source, dict)
                    if event_sequence <= checkpoint_sequence:
                        if (
                            event_sequence == checkpoint_sequence
                            and checkpoint["last_confirmed_observation_id"] == source["observation_id"]
                            and checkpoint["last_confirmed_entry_amount"] == source["current_entry_amount"]
                        ):
                            return {
                                "ok": True,
                                "blocked": False,
                                "changed": False,
                                "operation": "CRASH_BRIDGE_ALREADY_RECOVERED",
                                "event_id": event["event_id"],
                                "revision_before": revision_before,
                                "revision_after": revision_before,
                                "reason": "",
                            }
                        return self._blocked(
                            "claimed event sequence conflicts with checkpoint",
                            lock_wait_ms=file_lock.wait_ms,
                        )
                    if event_sequence != checkpoint_sequence + 1:
                        return self._blocked(
                            "claimed ownership sequence is more than one ahead of checkpoint",
                            lock_wait_ms=file_lock.wait_ms,
                        )
                    if source["previous_entry_amount"] != checkpoint["last_confirmed_entry_amount"]:
                        return self._blocked(
                            "claimed event previous_entry_amount conflicts with checkpoint",
                            lock_wait_ms=file_lock.wait_ms,
                        )
                    previous_seen = set(checkpoint["seen_contributing_buy_ids"])
                    current_ids = set(source["contributing_buy_ids"])
                    new_ids = set(source["new_contributing_buy_ids"])
                    if not previous_seen.issubset(current_ids) or current_ids - previous_seen != new_ids:
                        return self._blocked(
                            "claimed event contributor evidence conflicts with checkpoint",
                            lock_wait_ms=file_lock.wait_ms,
                        )
                    captured_observation = {
                        "account_no": event["account_no"],
                        "trading_day": event["trading_day"],
                        "confirmed_entry_amount": source["current_entry_amount"],
                        "observation_id": source["observation_id"],
                        "observed_at": source["observed_at"],
                        "evidence": source["confirmed_evidence"],
                        "contributing_buy_ids": source["contributing_buy_ids"],
                    }
                    normalized = _validate_observation(captured_observation)
                    recovered_checkpoint = self._checkpoint_from_observation(
                        normalized,
                        seen_ids=current_ids,
                        event_sequence=event_sequence,
                    )
                    return self._write_checkpoint(
                        document,
                        checkpoint_key=key,
                        checkpoint=recovered_checkpoint,
                        operation="CRASH_BRIDGE_RECOVERED",
                        expected_revision=expected,
                        lock_wait_ms=file_lock.wait_ms,
                        event_id=str(event["event_id"]),
                    )
        except (OSError, TimeoutError, ValueError) as exc:
            return self._blocked(str(exc))

    def _write_checkpoint(
        self,
        document: Mapping[str, object],
        *,
        checkpoint_key: str,
        checkpoint: object,
        operation: str,
        expected_revision: int,
        lock_wait_ms: int,
        event_id: object,
    ) -> dict[str, object]:
        revision_before = int(document["revision"])
        updated = deepcopy(dict(document))
        checkpoints = updated["checkpoints"]
        assert isinstance(checkpoints, dict)
        checkpoints[checkpoint_key] = deepcopy(checkpoint)
        updated_at = self._now_text()
        updated["revision"] = revision_before + 1
        updated["updated_at"] = updated_at
        try:
            validated = _validate_document(updated)
        except ValueError as exc:
            return self._blocked(str(exc), lock_wait_ms=lock_wait_ms)
        write_result = write_json_atomic(self.ingress_path, validated)
        if write_result.get("status") != STATUS_OK or write_result.get("written") is not True:
            return self._blocked(
                _text(write_result.get("error")) or "ingress atomic write failed",
                lock_wait_ms=lock_wait_ms,
            )
        read_back, error = self._load()
        revision_after = revision_before + 1
        if (
            read_back is None
            or read_back.get("revision") != revision_after
            or not isinstance(read_back.get("checkpoints"), dict)
            or read_back["checkpoints"].get(checkpoint_key) != checkpoint
        ):
            return self._blocked(
                error or "ingress post-write verification failed",
                changed=True,
                revision_before=revision_before,
                revision_after=revision_after,
                expected_revision=expected_revision,
                lock_wait_ms=lock_wait_ms,
            )
        return {
            "ok": True,
            "blocked": False,
            "changed": True,
            "operation": operation,
            "event_id": event_id,
            "revision_before": revision_before,
            "revision_after": revision_after,
            "expected_revision": expected_revision,
            "lock_wait_ms": lock_wait_ms,
            "post_write_verified": True,
            "checkpoint": deepcopy(checkpoint),
            "reason": "",
        }

    def _blocked(self, reason: str, **context: object) -> dict[str, object]:
        result: dict[str, object] = {
            "ok": False,
            "blocked": True,
            "changed": bool(context.pop("changed", False)),
            "reason": _text(reason) or "ingress operation blocked",
            "path": str(self.ingress_path),
            "post_write_verified": False,
        }
        result.update(context)
        return result


__all__ = [
    "BufferResponseIngressStateService",
    "DEFAULT_INGRESS_PATH",
    "SCHEMA_VERSION",
    "build_stable_buffer_observation",
    "collect_confirmed_contributing_buy_ids",
    "stable_buffer_observation_id",
]
