# -*- coding: utf-8 -*-
"""Execution runtime manager for dry-run lifecycle recording.

ExecutionRuntimeManager coordinates the dry-run controller with the in-memory
ExecutionLifecycle. It never commits storage, writes runtime files, commits
queues, calls SendOrder, or connects to GUI components.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from execution_context_adapter import adapt_runtime_dry_run_to_context
from execution_lifecycle import ExecutionLifecycle
from execution_runtime_controller import run_execution_runtime_dry_run
from execution_runtime_storage import ExecutionRuntimeStorage


MANAGER_TYPE = "EXECUTION_RUNTIME_MANAGER"


class ExecutionRuntimeManager:
    """Manage execution runtime dry-run results in an in-memory lifecycle."""

    def __init__(
        self,
        *,
        lifecycle: ExecutionLifecycle | None = None,
        storage: ExecutionRuntimeStorage,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if lifecycle is not None and not isinstance(lifecycle, ExecutionLifecycle):
            raise ValueError("lifecycle must be an ExecutionLifecycle")
        if not isinstance(storage, ExecutionRuntimeStorage):
            raise ValueError("storage must be an ExecutionRuntimeStorage")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata must be a dict")

        self.lifecycle = lifecycle if lifecycle is not None else ExecutionLifecycle()
        self.storage = storage
        self.metadata = deepcopy(metadata or {})

    def run_dry_run(self, order: Any, guard: Any, confirmations: Any = None) -> dict[str, Any]:
        dry_run_result = run_execution_runtime_dry_run(order, guard, self.storage, confirmations)
        adapter_result = adapt_runtime_dry_run_to_context(self.lifecycle.context, dry_run_result)
        return {
            "manager_type": MANAGER_TYPE,
            "status": adapter_result.get("status") if adapter_result.get("status") else dry_run_result.get("status"),
            "dry_run": True,
            "preview_only": True,
            "runtime_write": False,
            "storage_commit_called": False,
            "send_order_called": False,
            "queue_commit_called": False,
            "dry_run_result": deepcopy(dry_run_result),
            "context_adapter_result": deepcopy(adapter_result),
            "issues": list(dry_run_result.get("issues") or []) + list(adapter_result.get("issues") or []),
            "warnings": list(dry_run_result.get("warnings") or []) + list(adapter_result.get("warnings") or []),
        }

    def snapshot(self) -> dict[str, Any]:
        return deepcopy(self.lifecycle.snapshot())

    def restore(self, snapshot: Any) -> dict[str, Any]:
        return deepcopy(self.lifecycle.restore(snapshot))

    def list_sessions(self) -> list[Any]:
        return [session.copy() for session in self.lifecycle.context.list_sessions()]

    def list_events(self, session_id: str | None = None) -> list[dict[str, Any]]:
        return deepcopy(self.lifecycle.context.list_events(session_id))

    def summary(self) -> dict[str, Any]:
        return {
            "manager_type": MANAGER_TYPE,
            "in_memory": True,
            "runtime_write": False,
            "lifecycle": self.lifecycle.summary(),
            "metadata": deepcopy(self.metadata),
        }
