# -*- coding: utf-8 -*-
"""In-memory execution session state container.

ExecutionState manages ExecutionSession objects only in memory. It never reads
or writes runtime files, touches storage, commits queues, calls SendOrder, or
connects to GUI components.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from execution_session import ALLOWED_STATUSES, ExecutionSession


class ExecutionState:
    """In-memory collection of execution sessions."""

    def __init__(self, sessions: list[ExecutionSession] | None = None) -> None:
        self._sessions: dict[str, ExecutionSession] = {}
        for session in sessions or []:
            self.add_session(session)

    def _require_session(self, session: Any) -> ExecutionSession:
        if not isinstance(session, ExecutionSession):
            raise ValueError("session must be an ExecutionSession")
        return session

    def add_session(self, session: ExecutionSession) -> "ExecutionState":
        item = self._require_session(session)
        if item.session_id in self._sessions:
            raise ValueError(f"duplicate session_id: {item.session_id}")
        self._sessions[item.session_id] = item.copy()
        return self

    def get_session(self, session_id: str) -> ExecutionSession | None:
        session = self._sessions.get(str(session_id).strip())
        return session.copy() if session is not None else None

    def update_session(self, session: ExecutionSession) -> "ExecutionState":
        item = self._require_session(session)
        if item.session_id not in self._sessions:
            raise ValueError(f"unknown session_id: {item.session_id}")
        self._sessions[item.session_id] = item.copy()
        return self

    def remove_session(self, session_id: str) -> ExecutionSession | None:
        session = self._sessions.pop(str(session_id).strip(), None)
        return session.copy() if session is not None else None

    def list_sessions(self) -> list[ExecutionSession]:
        return [session.copy() for session in self._sessions.values()]

    def list_by_status(self, status: str) -> list[ExecutionSession]:
        if status not in ALLOWED_STATUSES:
            raise ValueError(f"invalid session status: {status}")
        return [session.copy() for session in self._sessions.values() if session.status == status]

    def has_session(self, session_id: str) -> bool:
        return str(session_id).strip() in self._sessions

    def to_dict(self) -> dict[str, Any]:
        sessions = [session.to_dict() for session in self._sessions.values()]
        return {
            "state_type": "EXECUTION_STATE",
            "in_memory": True,
            "runtime_write": False,
            "sessions": deepcopy(sessions),
            "session_count": len(sessions),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ExecutionState":
        if not isinstance(data, dict):
            raise ValueError("state data must be a dict")
        sessions = data.get("sessions")
        if not isinstance(sessions, list):
            raise ValueError("sessions must be a list")
        return cls([ExecutionSession.from_dict(item) for item in sessions])

    def copy(self) -> "ExecutionState":
        return ExecutionState.from_dict(self.to_dict())

    def summary(self) -> dict[str, Any]:
        by_status = {status: 0 for status in sorted(ALLOWED_STATUSES)}
        for session in self._sessions.values():
            by_status[session.status] = by_status.get(session.status, 0) + 1
        return {
            "state_type": "EXECUTION_STATE_SUMMARY",
            "in_memory": True,
            "runtime_write": False,
            "session_count": len(self._sessions),
            "by_status": deepcopy(by_status),
            "session_ids": list(self._sessions.keys()),
        }
