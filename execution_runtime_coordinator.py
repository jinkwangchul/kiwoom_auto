# -*- coding: utf-8 -*-
"""In-memory coordinator for execution runtime dry-run submissions.

ExecutionRuntimeCoordinator delegates execution to ExecutionRuntimeSupervisor
only. It never writes runtime files, commits storage, commits queues, calls
SendOrder, or connects to GUI components.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from execution_runtime_supervisor import ExecutionRuntimeSupervisor


COORDINATOR_TYPE = "EXECUTION_RUNTIME_COORDINATOR"


class ExecutionRuntimeCoordinator:
    """Coordinate dry-run submissions through an ExecutionRuntimeSupervisor."""

    def __init__(
        self,
        *,
        supervisor: ExecutionRuntimeSupervisor,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not isinstance(supervisor, ExecutionRuntimeSupervisor):
            raise ValueError("supervisor must be an ExecutionRuntimeSupervisor")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata must be a dict")
        self.supervisor = supervisor
        self.metadata = deepcopy(metadata or {})
        self._last_result: dict[str, Any] | None = None

    def submit(self, order: Any, guard: Any, confirmations: Any = None) -> dict[str, Any]:
        supervisor_result = self.supervisor.run(order, guard, confirmations)
        result = {
            "coordinator_type": COORDINATOR_TYPE,
            "status": supervisor_result.get("status"),
            "dry_run": True,
            "preview_only": True,
            "runtime_write": False,
            "supervisor_result": deepcopy(supervisor_result),
            "issues": list(supervisor_result.get("issues") or []),
            "warnings": list(supervisor_result.get("warnings") or []),
        }
        self._last_result = deepcopy(result)
        return deepcopy(result)

    def last_result(self) -> dict[str, Any] | None:
        return deepcopy(self._last_result)

    def clear(self) -> None:
        self._last_result = None
        self.supervisor.clear_last_result()

    def snapshot(self) -> dict[str, Any]:
        return deepcopy(self.supervisor.snapshot())

    def restore(self, snapshot: Any) -> dict[str, Any]:
        return deepcopy(self.supervisor.restore(snapshot))

    def list_sessions(self) -> list[Any]:
        return [session.copy() for session in self.supervisor.list_sessions()]

    def list_events(self, session_id: str | None = None) -> list[dict[str, Any]]:
        return deepcopy(self.supervisor.list_events(session_id))

    def summary(self) -> dict[str, Any]:
        return {
            "coordinator_type": COORDINATOR_TYPE,
            "in_memory": True,
            "runtime_write": False,
            "has_last_result": self._last_result is not None,
            "last_status": self._last_result.get("status") if self._last_result else None,
            "supervisor": self.supervisor.summary(),
            "metadata": deepcopy(self.metadata),
        }
