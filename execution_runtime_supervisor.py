# -*- coding: utf-8 -*-
"""In-memory supervisor for ExecutionRuntimeManager.

ExecutionRuntimeSupervisor keeps only the latest dry-run result and delegates
snapshot/session/event operations to ExecutionRuntimeManager. It never writes
runtime files, commits storage, commits queues, calls SendOrder, or connects to
GUI components.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from execution_runtime_manager import ExecutionRuntimeManager


SUPERVISOR_TYPE = "EXECUTION_RUNTIME_SUPERVISOR"


class ExecutionRuntimeSupervisor:
    """Supervise runtime-manager dry-runs in memory."""

    def __init__(
        self,
        *,
        runtime_manager: ExecutionRuntimeManager,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not isinstance(runtime_manager, ExecutionRuntimeManager):
            raise ValueError("runtime_manager must be an ExecutionRuntimeManager")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata must be a dict")

        self.runtime_manager = runtime_manager
        self.metadata = deepcopy(metadata or {})
        self._last_result: dict[str, Any] | None = None

    def run(self, order: Any, guard: Any, confirmations: Any = None) -> dict[str, Any]:
        manager_result = self.runtime_manager.run_dry_run(order, guard, confirmations)
        result = {
            "supervisor_type": SUPERVISOR_TYPE,
            "status": manager_result.get("status"),
            "dry_run": True,
            "preview_only": True,
            "runtime_write": False,
            "dry_run_result": deepcopy(manager_result),
            "issues": list(manager_result.get("issues") or []),
            "warnings": list(manager_result.get("warnings") or []),
        }
        self._last_result = deepcopy(result)
        return deepcopy(result)

    def last_result(self) -> dict[str, Any] | None:
        return deepcopy(self._last_result)

    def clear_last_result(self) -> None:
        self._last_result = None

    def snapshot(self) -> dict[str, Any]:
        return deepcopy(self.runtime_manager.snapshot())

    def restore(self, snapshot: Any) -> dict[str, Any]:
        return deepcopy(self.runtime_manager.restore(snapshot))

    def list_sessions(self) -> list[Any]:
        return [session.copy() for session in self.runtime_manager.list_sessions()]

    def list_events(self, session_id: str | None = None) -> list[dict[str, Any]]:
        return deepcopy(self.runtime_manager.list_events(session_id))

    def summary(self) -> dict[str, Any]:
        return {
            "supervisor_type": SUPERVISOR_TYPE,
            "in_memory": True,
            "runtime_write": False,
            "has_last_result": self._last_result is not None,
            "last_status": self._last_result.get("status") if self._last_result else None,
            "runtime_manager": self.runtime_manager.summary(),
            "metadata": deepcopy(self.metadata),
        }
