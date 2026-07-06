# -*- coding: utf-8 -*-
"""In-memory execution session model.

ExecutionSession represents a logical execution runtime unit. It never reads or
writes runtime files, touches storage, commits queues, calls SendOrder, or
connects to GUI components.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


STATUS_CREATED = "CREATED"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
STATUS_COMPLETED = "COMPLETED"

ALLOWED_STATUSES = {
    STATUS_CREATED,
    STATUS_READY,
    STATUS_BLOCKED,
    STATUS_INVALID,
    STATUS_COMPLETED,
}

TERMINAL_STATUSES = {STATUS_INVALID, STATUS_COMPLETED}
ID_FIELDS = {"session_id", "execution_id", "order_id", "request_hash", "lock_id"}

ALLOWED_TRANSITIONS = {
    STATUS_CREATED: {STATUS_READY, STATUS_BLOCKED, STATUS_INVALID},
    STATUS_READY: {STATUS_BLOCKED, STATUS_INVALID, STATUS_COMPLETED},
    STATUS_BLOCKED: {STATUS_READY},
    STATUS_INVALID: set(),
    STATUS_COMPLETED: set(),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _require_text(name: str, value: Any) -> str:
    text = _text(value)
    if not text:
        raise ValueError(f"{name} is required")
    return text


class ExecutionSession:
    """Mutable-status, immutable-identity execution session."""

    def __init__(
        self,
        *,
        session_id: str,
        execution_id: str,
        order_id: str,
        request_hash: str,
        lock_id: str,
        created_at: str | None = None,
        status: str = STATUS_CREATED,
        metadata: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> None:
        object.__setattr__(self, "_locked_ids", False)
        self.session_id = _require_text("session_id", session_id)
        self.created_at = _require_text("created_at", created_at or _now_iso())
        self.execution_id = _require_text("execution_id", execution_id)
        self.order_id = _require_text("order_id", order_id)
        self.request_hash = _require_text("request_hash", request_hash)
        self.lock_id = _require_text("lock_id", lock_id)
        if status not in ALLOWED_STATUSES:
            raise ValueError(f"invalid session status: {status}")
        self.status = status
        self.metadata = deepcopy(metadata or {})
        self.reason = _text(reason) or None
        object.__setattr__(self, "_locked_ids", True)

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_locked_ids", False) and name in ID_FIELDS:
            raise AttributeError(f"{name} is immutable")
        object.__setattr__(self, name, value)

    def _transition(self, next_status: str, reason: str | None = None) -> "ExecutionSession":
        if next_status not in ALLOWED_STATUSES:
            raise ValueError(f"invalid session status: {next_status}")
        allowed = ALLOWED_TRANSITIONS.get(self.status, set())
        if next_status not in allowed:
            raise ValueError(f"invalid session transition: {self.status} -> {next_status}")
        self.status = next_status
        self.reason = _text(reason) or None
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "execution_id": self.execution_id,
            "order_id": self.order_id,
            "request_hash": self.request_hash,
            "lock_id": self.lock_id,
            "status": self.status,
            "metadata": deepcopy(self.metadata),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ExecutionSession":
        if not isinstance(data, dict):
            raise ValueError("session data must be a dict")
        return cls(
            session_id=data.get("session_id"),
            created_at=data.get("created_at"),
            execution_id=data.get("execution_id"),
            order_id=data.get("order_id"),
            request_hash=data.get("request_hash"),
            lock_id=data.get("lock_id"),
            status=data.get("status", STATUS_CREATED),
            metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
            reason=data.get("reason"),
        )

    def mark_ready(self) -> "ExecutionSession":
        return self._transition(STATUS_READY)

    def mark_blocked(self, reason: str) -> "ExecutionSession":
        return self._transition(STATUS_BLOCKED, _require_text("reason", reason))

    def mark_invalid(self, reason: str) -> "ExecutionSession":
        return self._transition(STATUS_INVALID, _require_text("reason", reason))

    def mark_completed(self) -> "ExecutionSession":
        return self._transition(STATUS_COMPLETED)

    def copy(self) -> "ExecutionSession":
        return ExecutionSession.from_dict(self.to_dict())

    def summary(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "status": self.status,
            "execution_id": self.execution_id,
            "order_id": self.order_id,
            "request_hash": self.request_hash,
            "lock_id": self.lock_id,
            "reason": self.reason,
            "terminal": self.status in TERMINAL_STATUSES,
        }


def create_session(
    *,
    session_id: str,
    execution_id: str,
    order_id: str,
    request_hash: str,
    lock_id: str,
    created_at: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ExecutionSession:
    """Create a new in-memory execution session."""
    return ExecutionSession(
        session_id=session_id,
        created_at=created_at,
        execution_id=execution_id,
        order_id=order_id,
        request_hash=request_hash,
        lock_id=lock_id,
        metadata=metadata,
    )
