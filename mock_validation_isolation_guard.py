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
    "gui_market_data_host",
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
FORBIDDEN_IMPORT_ROOTS.update(
    {
        "gui_auto_trade_close",
        "gui_auto_trade_context_menu",
        "manual_ats_runtime",
    }
)

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


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _literal_text(node: ast.AST | None) -> str:
    return str(node.value) if isinstance(node, ast.Constant) and isinstance(node.value, str) else ""


def _dynamic_import_name(node: ast.Call) -> str:
    if _call_name(node) not in {"import_module", "__import__"} or not node.args:
        return ""
    return _literal_text(node.args[0]).strip()


def _dynamic_routine_sources(source_path: Path, tree: ast.AST) -> tuple[Path, ...]:
    """Resolve literal routine modules loaded by the Mock adapter without executing them."""

    routine_root = source_path.parent / "routines" / "지표추종매매"
    discovered: set[Path] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) != "_load_file_module":
            continue
        if not node.args:
            continue
        filename = _literal_text(node.args[0]).strip()
        if not filename:
            continue
        candidate = (routine_root / filename).resolve()
        if candidate.is_file():
            discovered.add(candidate)
    return tuple(sorted(discovered))


def audit_mock_dependency_graph(paths: Iterable[str | Path]) -> dict[str, object]:
    violations: list[dict[str, object]] = []
    checked: list[str] = []
    pending = [Path(value) for value in paths]
    visited: set[Path] = set()
    while pending:
        path = pending.pop(0)
        resolved = path.resolve()
        if resolved in visited:
            continue
        visited.add(resolved)
        checked.append(str(path))
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        pending.extend(_dynamic_routine_sources(path, tree))
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
                name = _call_name(node)
                if name in FORBIDDEN_CALL_NAMES:
                    violations.append({"path": str(path), "line": node.lineno, "kind": "CALL", "name": name})
                dynamic_name = _dynamic_import_name(node)
                root = dynamic_name.split(".", 1)[0] if dynamic_name else ""
                if root in FORBIDDEN_IMPORT_ROOTS:
                    violations.append({
                        "path": str(path), "line": node.lineno,
                        "kind": "DYNAMIC_IMPORT", "name": dynamic_name,
                    })
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
