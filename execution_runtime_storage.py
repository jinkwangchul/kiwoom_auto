# -*- coding: utf-8 -*-
"""Execution Runtime Storage preview facade.

This storage layer is a path-injected interface over read-only runtime readers
and preview-only runtime write/commit-plan layers. It never creates runtime
files, writes files, creates directories, performs atomic writes, commits
queues, or calls execution/order components.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from execution_runtime_commit_plan_orchestrator import run_execution_runtime_commit_plan_orchestrator
from execution_runtime_commit_readiness_gate import evaluate_execution_runtime_commit_readiness
from execution_runtime_reader import read_order_executions, read_order_locks
from execution_runtime_write_preview_orchestrator import run_execution_runtime_write_preview_orchestrator


class ExecutionRuntimeStorage:
    """Preview-only storage facade for execution runtime files."""

    def __init__(self, order_executions_path: str | Path, order_locks_path: str | Path) -> None:
        if order_executions_path is None or not str(order_executions_path).strip():
            raise ValueError("order_executions_path is required")
        if order_locks_path is None or not str(order_locks_path).strip():
            raise ValueError("order_locks_path is required")

        self.order_executions_path = Path(order_executions_path)
        self.order_locks_path = Path(order_locks_path)

    def read(self) -> dict[str, Any]:
        """Read injected runtime paths without mutating files."""
        executions = read_order_executions(self.order_executions_path)
        locks = read_order_locks(self.order_locks_path)
        return {
            "ok": executions.get("ok") is True and locks.get("ok") is True,
            "preview_only": True,
            "runtime_write": False,
            "order_executions": deepcopy(executions),
            "order_locks": deepcopy(locks),
            "issues": list(executions.get("issues") or []) + list(locks.get("issues") or []),
            "warnings": list(executions.get("warnings") or []) + list(locks.get("warnings") or []),
        }

    def preview_write(self, catalog_orchestrator_result: Any) -> dict[str, Any]:
        """Build a runtime write preview from current injected-path data."""
        read_result = self.read()
        return run_execution_runtime_write_preview_orchestrator(
            catalog_orchestrator_result=catalog_orchestrator_result,
            existing_order_executions_data=read_result["order_executions"].get("data"),
            existing_order_locks_data=read_result["order_locks"].get("data"),
        )

    def preview_commit_plan(
        self,
        catalog_orchestrator_result: Any,
        confirmations: Any = None,
    ) -> dict[str, Any]:
        """Build a commit plan preview without performing commit or write."""
        context = confirmations if isinstance(confirmations, dict) else {}
        write_preview = self.preview_write(catalog_orchestrator_result)
        gate = evaluate_execution_runtime_commit_readiness(
            write_preview,
            manual_execution_runtime_commit_confirmed=(
                context.get("manual_execution_runtime_commit_confirmed") is True
            ),
            manual_runtime_file_write_confirmed=(
                context.get("manual_runtime_file_write_confirmed") is True
            ),
        )
        return run_execution_runtime_commit_plan_orchestrator(write_preview, gate)

    def commit(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Runtime commit is intentionally not implemented in this preview layer."""
        del args, kwargs
        raise NotImplementedError("Execution runtime commit is not implemented")
