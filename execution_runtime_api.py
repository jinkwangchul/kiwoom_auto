# -*- coding: utf-8 -*-
"""Public in-memory API facade for execution runtime dry-runs.

ExecutionRuntimeAPI is a thin facade over ExecutionRuntimeCoordinator. It is
not connected to the GUI preview controller and never writes runtime files,
commits storage, commits queues, calls SendOrder, or performs real execution.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from execution_runtime_coordinator import ExecutionRuntimeCoordinator


API_TYPE = "EXECUTION_RUNTIME_API"


class ExecutionRuntimeAPI:
    """Expose safe dry-run runtime operations through a coordinator."""

    def __init__(
        self,
        *,
        coordinator: ExecutionRuntimeCoordinator,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not isinstance(coordinator, ExecutionRuntimeCoordinator):
            raise ValueError("coordinator must be an ExecutionRuntimeCoordinator")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata must be a dict")
        self.coordinator = coordinator
        self.metadata = deepcopy(metadata or {})

    def submit_dry_run(self, order: Any, guard: Any, confirmations: Any = None) -> dict[str, Any]:
        coordinator_result = self.coordinator.submit(order, guard, confirmations)
        return {
            "api_type": API_TYPE,
            "status": coordinator_result.get("status"),
            "dry_run": True,
            "preview_only": True,
            "runtime_write": False,
            "coordinator_result": deepcopy(coordinator_result),
            "issues": list(coordinator_result.get("issues") or []),
            "warnings": list(coordinator_result.get("warnings") or []),
        }

    def get_last_result(self) -> dict[str, Any] | None:
        coordinator_result = self.coordinator.last_result()
        if coordinator_result is None:
            return None
        return {
            "api_type": API_TYPE,
            "status": coordinator_result.get("status"),
            "dry_run": True,
            "preview_only": True,
            "runtime_write": False,
            "coordinator_result": deepcopy(coordinator_result),
            "issues": list(coordinator_result.get("issues") or []),
            "warnings": list(coordinator_result.get("warnings") or []),
        }

    def clear(self) -> None:
        self.coordinator.clear()

    def snapshot(self) -> dict[str, Any]:
        return deepcopy(self.coordinator.snapshot())

    def restore(self, snapshot: Any) -> dict[str, Any]:
        return deepcopy(self.coordinator.restore(snapshot))

    def list_sessions(self) -> list[Any]:
        return [session.copy() for session in self.coordinator.list_sessions()]

    def list_events(self, session_id: str | None = None) -> list[dict[str, Any]]:
        return deepcopy(self.coordinator.list_events(session_id))

    def summary(self) -> dict[str, Any]:
        return {
            "api_type": API_TYPE,
            "in_memory": True,
            "dry_run": True,
            "preview_only": True,
            "runtime_write": False,
            "has_last_result": self.coordinator.last_result() is not None,
            "coordinator": self.coordinator.summary(),
            "metadata": deepcopy(self.metadata),
        }
