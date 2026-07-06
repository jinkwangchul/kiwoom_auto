# -*- coding: utf-8 -*-
"""In-memory execution context.

ExecutionContext combines ExecutionState and ExecutionJournal for logical
execution sessions. It never reads or writes runtime files, touches storage,
commits queues, calls SendOrder, or connects to GUI components.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from execution_journal import ExecutionJournal
from execution_session import ExecutionSession, create_session as build_execution_session
from execution_state import ExecutionState


CONTEXT_TYPE = "EXECUTION_CONTEXT"


class ExecutionContext:
    """In-memory context for sessions, state, and journal events."""

    def __init__(
        self,
        *,
        state: ExecutionState | None = None,
        journal: ExecutionJournal | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if state is not None and not isinstance(state, ExecutionState):
            raise ValueError("state must be an ExecutionState")
        if journal is not None and not isinstance(journal, ExecutionJournal):
            raise ValueError("journal must be an ExecutionJournal")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata must be a dict")

        self.state = state.copy() if state is not None else ExecutionState()
        self.journal = journal.copy() if journal is not None else ExecutionJournal()
        self.metadata = deepcopy(metadata or {})

    def create_session(
        self,
        *,
        session_id: str,
        execution_id: str,
        order_id: str,
        request_hash: str,
        lock_id: str,
        created_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ExecutionSession:
        session = build_execution_session(
            session_id=session_id,
            execution_id=execution_id,
            order_id=order_id,
            request_hash=request_hash,
            lock_id=lock_id,
            created_at=created_at,
            metadata=metadata,
        )
        self.state.add_session(session)
        self.journal.append_event(
            "SESSION_CREATED",
            session.session_id,
            {"status": session.status, "execution_id": session.execution_id, "order_id": session.order_id},
        )
        return session.copy()

    def _get_existing_session(self, session_id: str) -> ExecutionSession:
        session = self.state.get_session(session_id)
        if session is None:
            raise ValueError(f"unknown session_id: {session_id}")
        return session

    def mark_session_ready(self, session_id: str) -> ExecutionSession:
        session = self._get_existing_session(session_id)
        session.mark_ready()
        self.state.update_session(session)
        self.journal.append_event("SESSION_READY", session.session_id, {"status": session.status})
        return session.copy()

    def mark_session_blocked(self, session_id: str, reason: str) -> ExecutionSession:
        session = self._get_existing_session(session_id)
        session.mark_blocked(reason)
        self.state.update_session(session)
        self.journal.append_event(
            "SESSION_BLOCKED",
            session.session_id,
            {"status": session.status, "reason": session.reason},
        )
        return session.copy()

    def mark_session_invalid(self, session_id: str, reason: str) -> ExecutionSession:
        session = self._get_existing_session(session_id)
        session.mark_invalid(reason)
        self.state.update_session(session)
        self.journal.append_event(
            "SESSION_INVALID",
            session.session_id,
            {"status": session.status, "reason": session.reason},
        )
        return session.copy()

    def mark_session_completed(self, session_id: str) -> ExecutionSession:
        session = self._get_existing_session(session_id)
        session.mark_completed()
        self.state.update_session(session)
        self.journal.append_event("SESSION_COMPLETED", session.session_id, {"status": session.status})
        return session.copy()

    def get_session(self, session_id: str) -> ExecutionSession | None:
        return self.state.get_session(session_id)

    def list_sessions(self) -> list[ExecutionSession]:
        return self.state.list_sessions()

    def list_events(self, session_id: str | None = None) -> list[dict[str, Any]]:
        if session_id is None:
            return self.journal.list_events()
        return self.journal.list_by_session(session_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_type": CONTEXT_TYPE,
            "in_memory": True,
            "runtime_write": False,
            "state": self.state.to_dict(),
            "journal": self.journal.to_dict(),
            "metadata": deepcopy(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ExecutionContext":
        if not isinstance(data, dict):
            raise ValueError("context data must be a dict")
        state_data = data.get("state")
        journal_data = data.get("journal")
        metadata = data.get("metadata")
        return cls(
            state=ExecutionState.from_dict(state_data),
            journal=ExecutionJournal.from_dict(journal_data),
            metadata=metadata if isinstance(metadata, dict) else {},
        )

    def copy(self) -> "ExecutionContext":
        return ExecutionContext.from_dict(self.to_dict())

    def summary(self) -> dict[str, Any]:
        return {
            "context_type": "EXECUTION_CONTEXT_SUMMARY",
            "in_memory": True,
            "runtime_write": False,
            "state": self.state.summary(),
            "journal": self.journal.summary(),
            "metadata": deepcopy(self.metadata),
        }
