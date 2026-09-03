# -*- coding: utf-8 -*-
"""Static dependency guard for the isolated Mock Validation domain."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable

from mock_validation_contract import MockValidationError


FORBIDDEN_IMPORT_ROOTS = {
    "routine_signal_queue",
    "routine_signal_consumer",
    "order_queue",
    "operation_policy_gate",
    "execution_enable_service",
    "execution_queue_writer",
    "execution_queue_commit_executor",
    "execution_queue_commit_service",
    "execution_runtime_commit_service",
    "execution_runtime_file_init_commit_service",
    "runtime_atomic_writer",
    "send_order_entrypoint",
    "send_order_result_recorder_v1",
    "kiwoom_send_order_executor",
    "chejan_event_recorder",
    "execution_fill_recorder",
    "position_update_service",
    "realized_pnl_ledger",
    "production_performance_linkage",
    "event_journal_production",
    "event_journal_writer",
    "budget_command",
    "account_auto_trade_budget_consumption",
    "running_budget_adjustment",
    "close_intent_service",
    "close_liquidation_command",
    "close_liquidation_execution_pipeline",
    "operation_close_completion_check_service",
    "lifecycle_commit_writer",
    "lifecycle_runtime_recovery",
    "execution_recovery",
    "production_recovery_timer_lifecycle",
    "stock_long_hold_policy",
}

FORBIDDEN_CALL_NAMES = {
    "SendOrder",
    "append_order_candidates",
    "enqueue_routine_signal",
    "commit_execution_enable",
    "claim_order_for_dispatch",
    "execute_claimed_send_order",
    "record_chejan_event",
    "record_execution_fill",
    "update_position_from_fill",
    "record_realized_pnl",
    "append_production_event",
    "commit_close_intent",
    "commit_close_liquidation",
    "commit_operation_completion",
}


def mock_foundation_module_paths(project_root: str | Path) -> tuple[Path, ...]:
    root = Path(project_root)
    return tuple(sorted(root.glob("mock_validation_*.py")))


def audit_mock_dependency_graph(paths: Iterable[str | Path]) -> dict[str, object]:
    violations: list[dict[str, object]] = []
    checked: list[str] = []
    for value in paths:
        path = Path(value)
        checked.append(str(path))
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    if root in FORBIDDEN_IMPORT_ROOTS:
                        violations.append({"path": str(path), "line": node.lineno, "kind": "IMPORT", "name": alias.name})
            elif isinstance(node, ast.ImportFrom):
                root = str(node.module or "").split(".", 1)[0]
                if root in FORBIDDEN_IMPORT_ROOTS:
                    violations.append({"path": str(path), "line": node.lineno, "kind": "IMPORT_FROM", "name": node.module})
            elif isinstance(node, ast.Call):
                name = ""
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                if name in FORBIDDEN_CALL_NAMES:
                    violations.append({"path": str(path), "line": node.lineno, "kind": "CALL", "name": name})
    return {"ok": not violations, "files_checked": tuple(checked), "violations": violations}


def assert_mock_dependency_isolation(paths: Iterable[str | Path]) -> dict[str, object]:
    result = audit_mock_dependency_graph(paths)
    if result["ok"] is not True:
        raise MockValidationError(f"MOCK_PRODUCTION_DEPENDENCY_FORBIDDEN:{result['violations']}")
    return result


__all__ = [
    "FORBIDDEN_CALL_NAMES",
    "FORBIDDEN_IMPORT_ROOTS",
    "assert_mock_dependency_isolation",
    "audit_mock_dependency_graph",
    "mock_foundation_module_paths",
]
