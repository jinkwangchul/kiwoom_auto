# -*- coding: utf-8 -*-
"""In-memory execution journal.

ExecutionJournal records session/state events in memory only. It never writes
log files, reads or writes runtime files, touches storage, commits queues,
calls SendOrder, or connects to GUI components.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


JOURNAL_TYPE = "EXECUTION_JOURNAL"


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


class ExecutionJournal:
    """In-memory event journal for execution sessions."""

    def __init__(self, events: list[dict[str, Any]] | None = None) -> None:
        self._events: list[dict[str, Any]] = []
        self._event_ids: set[str] = set()
        self._next_sequence = 1
        for event in events or []:
            self._append_existing_event(event)

    def _new_event_id(self) -> str:
        while True:
            event_id = f"EXEC_EVENT_{self._next_sequence:06d}"
            self._next_sequence += 1
            if event_id not in self._event_ids:
                return event_id

    def _append_existing_event(self, event: Any) -> None:
        normalized = self._validate_event(event)
        if normalized["event_id"] in self._event_ids:
            raise ValueError(f"duplicate event_id: {normalized['event_id']}")
        self._event_ids.add(normalized["event_id"])
        self._events.append(deepcopy(normalized))
        sequence = _sequence_from_event_id(normalized["event_id"])
        if sequence is not None and sequence >= self._next_sequence:
            self._next_sequence = sequence + 1

    def _validate_event(self, event: Any) -> dict[str, Any]:
        if not isinstance(event, dict):
            raise ValueError("event must be a dict")
        payload = event.get("payload")
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise ValueError("event payload must be a dict")
        return {
            "event_id": _require_text("event_id", event.get("event_id")),
            "event_type": _require_text("event_type", event.get("event_type")),
            "session_id": _require_text("session_id", event.get("session_id")),
            "created_at": _require_text("created_at", event.get("created_at")),
            "payload": deepcopy(payload),
        }

    def append_event(
        self,
        event_type: str,
        session_id: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if payload is not None and not isinstance(payload, dict):
            raise ValueError("payload must be a dict")
        event = {
            "event_id": self._new_event_id(),
            "event_type": _require_text("event_type", event_type),
            "session_id": _require_text("session_id", session_id),
            "created_at": _now_iso(),
            "payload": deepcopy(payload or {}),
        }
        self._event_ids.add(event["event_id"])
        self._events.append(deepcopy(event))
        return deepcopy(event)

    def list_events(self) -> list[dict[str, Any]]:
        return deepcopy(self._events)

    def list_by_session(self, session_id: str) -> list[dict[str, Any]]:
        target = _require_text("session_id", session_id)
        return deepcopy([event for event in self._events if event["session_id"] == target])

    def list_by_type(self, event_type: str) -> list[dict[str, Any]]:
        target = _require_text("event_type", event_type)
        return deepcopy([event for event in self._events if event["event_type"] == target])

    def latest_event(self, session_id: str | None = None) -> dict[str, Any] | None:
        events = self._events
        if session_id is not None:
            target = _require_text("session_id", session_id)
            events = [event for event in self._events if event["session_id"] == target]
        if not events:
            return None
        return deepcopy(events[-1])

    def to_dict(self) -> dict[str, Any]:
        return {
            "journal_type": JOURNAL_TYPE,
            "in_memory": True,
            "runtime_write": False,
            "event_count": len(self._events),
            "events": deepcopy(self._events),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ExecutionJournal":
        if not isinstance(data, dict):
            raise ValueError("journal data must be a dict")
        events = data.get("events")
        if not isinstance(events, list):
            raise ValueError("events must be a list")
        return cls(deepcopy(events))

    def copy(self) -> "ExecutionJournal":
        return ExecutionJournal.from_dict(self.to_dict())

    def summary(self) -> dict[str, Any]:
        by_type: dict[str, int] = {}
        by_session: dict[str, int] = {}
        for event in self._events:
            by_type[event["event_type"]] = by_type.get(event["event_type"], 0) + 1
            by_session[event["session_id"]] = by_session.get(event["session_id"], 0) + 1
        return {
            "journal_type": "EXECUTION_JOURNAL_SUMMARY",
            "in_memory": True,
            "runtime_write": False,
            "event_count": len(self._events),
            "by_type": deepcopy(by_type),
            "by_session": deepcopy(by_session),
            "latest_event": self.latest_event(),
        }


def _sequence_from_event_id(event_id: str) -> int | None:
    prefix = "EXEC_EVENT_"
    if not event_id.startswith(prefix):
        return None
    try:
        return int(event_id[len(prefix) :])
    except ValueError:
        return None
