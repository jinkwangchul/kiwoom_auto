# -*- coding: utf-8 -*-
"""In-memory execution lifecycle manager.

ExecutionLifecycle coordinates ExecutionContext and ExecutionRecovery. It never
reads or writes runtime files, touches storage, commits queues, calls SendOrder,
or connects to GUI components.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from execution_context import ExecutionContext
from execution_recovery import ExecutionRecovery


LIFECYCLE_TYPE = "EXECUTION_LIFECYCLE"


class ExecutionLifecycle:
    """Manage logical execution session lifecycle in memory."""

    def __init__(
        self,
        *,
        context: ExecutionContext | None = None,
        recovery: ExecutionRecovery | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if context is not None and not isinstance(context, ExecutionContext):
            raise ValueError("context must be an ExecutionContext")
        if recovery is not None and not isinstance(recovery, ExecutionRecovery):
            raise ValueError("recovery must be an ExecutionRecovery")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata must be a dict")

        self.context = context.copy() if context is not None else ExecutionContext()
        self.recovery = recovery if recovery is not None else ExecutionRecovery()
        self.metadata = deepcopy(metadata or {})

    def start_session(
        self,
        *,
        session_id: str,
        execution_id: str,
        order_id: str,
        request_hash: str,
        lock_id: str,
        created_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session = self.context.create_session(
            session_id=session_id,
            execution_id=execution_id,
            order_id=order_id,
            request_hash=request_hash,
            lock_id=lock_id,
            created_at=created_at,
            metadata=metadata,
        )
        self.context.journal.append_event(
            "LIFECYCLE_SESSION_STARTED",
            session.session_id,
            {"status": session.status, "execution_id": session.execution_id, "order_id": session.order_id},
        )
        return session.to_dict()

    def ready_session(self, session_id: str) -> dict[str, Any]:
        return self.context.mark_session_ready(session_id).to_dict()

    def block_session(self, session_id: str, reason: str) -> dict[str, Any]:
        return self.context.mark_session_blocked(session_id, reason).to_dict()

    def invalidate_session(self, session_id: str, reason: str) -> dict[str, Any]:
        return self.context.mark_session_invalid(session_id, reason).to_dict()

    def complete_session(self, session_id: str) -> dict[str, Any]:
        return self.context.mark_session_completed(session_id).to_dict()

    def snapshot(self) -> dict[str, Any]:
        return deepcopy(self.recovery.create_snapshot(self.context))

    def restore(self, snapshot: Any) -> dict[str, Any]:
        restored = self.recovery.restore_snapshot(snapshot)
        self.context = restored
        return self.context.to_dict()

    def summary(self) -> dict[str, Any]:
        return {
            "lifecycle_type": LIFECYCLE_TYPE,
            "in_memory": True,
            "runtime_write": False,
            "context": self.context.summary(),
            "metadata": deepcopy(self.metadata),
        }
