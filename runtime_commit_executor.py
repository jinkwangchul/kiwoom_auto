# -*- coding: utf-8 -*-
"""Runtime Commit Executor Preview for Real Runtime Commit (M6-6).

This module builds an execution plan only. It never calls Atomic Writer,
Backup Manager, Rollback Manager, Runtime Commit Verifier, Audit Record
Builder, or any real executor. It never reads or writes runtime files.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any


STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"

STEP_SEQUENCE = (
    ("VALIDATE_BOUNDARY", "runtime_commit_boundary", True),
    ("PREPARE_BACKUP", "runtime_backup_manager", True),
    ("PREPARE_ATOMIC_WRITE", "runtime_atomic_writer", True),
    ("VERIFY_COMMIT", "runtime_commit_verifier", True),
    ("EVALUATE_ROLLBACK", "runtime_rollback_manager", False),
    ("BUILD_AUDIT_RECORD", "runtime_commit_audit_record", True),
    ("COMPLETE", "runtime_commit_executor", True),
)

STATE_SEQUENCE_READY = (
    "CREATED",
    "VALIDATING",
    "BACKUP_PLANNED",
    "WRITE_PLANNED",
    "VERIFICATION_PLANNED",
    "ROLLBACK_EVALUATED",
    "AUDIT_PLANNED",
    "READY_TO_EXECUTE",
)

SAFETY_FLAG_NAMES = (
    "runtime_write",
    "position_write",
    "balance_write",
    "file_write_called",
    "atomic_write_executed",
    "backup_created",
    "rollback_executed",
    "verification_executed",
    "audit_write",
    "audit_file_written",
    "gui_update_called",
    "send_order_called",
    "chejan_called",
    "broker_called",
    "sqlite_write",
    "rules_write",
    "atomic_writer_called",
    "backup_manager_called",
    "rollback_manager_called",
    "commit_verifier_called",
    "audit_record_builder_called",
    "real_executor_called",
    "actual_execution_performed",
)

INPUT_FORBIDDEN_FLAGS = (
    "runtime_write",
    "position_write",
    "balance_write",
    "file_write_called",
    "atomic_write_executed",
    "backup_created",
    "rollback_executed",
    "verification_executed",
    "audit_write",
    "audit_file_written",
    "gui_update_called",
    "send_order_called",
    "chejan_called",
    "broker_called",
    "sqlite_write",
    "rules_write",
    "real_executor_called",
    "actual_execution_performed",
)


def _now_text() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _upper(value: Any) -> str:
    return _text(value).upper()


def _build_safety_flags() -> dict[str, bool]:
    return {name: False for name in SAFETY_FLAG_NAMES}


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _normalize_path_text(value: Any) -> str:
    return _text(value).replace("\\", "/").lower()


def _looks_like_rules_target(value: Any) -> bool:
    text = _normalize_path_text(value)
    if not text:
        return False
    parts = [part for part in text.split("/") if part]
    return len(parts) >= 2 and parts[-1] == "rules.json" and "routines" in parts


def _contains_protected_rules_target(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {
                "path",
                "target",
                "target_path",
                "source",
                "destination",
                "file",
                "filename",
            } and _looks_like_rules_target(child):
                return True
            if key in {"files", "paths", "targets"} and _contains_protected_rules_target(child):
                return True
            if _looks_like_rules_target(key) or _contains_protected_rules_target(child):
                return True
        return False
    if isinstance(value, (list, tuple, set)):
        return any(_contains_protected_rules_target(child) for child in value)
    return _looks_like_rules_target(value)


def _status_readyish(value: Any) -> str:
    status = _upper(value)
    if not status:
        return ""
    if "INVALID" in status:
        return STATUS_INVALID
    if "BLOCKED" in status or "ERROR" in status or "FAILED" in status:
        return STATUS_BLOCKED
    if status in {
        STATUS_READY,
        "OK",
        "RUNTIME_COMMIT_BOUNDARY_READY",
        "COMMITTED",
        "AUDIT_READY",
    }:
        return STATUS_READY
    return STATUS_BLOCKED


def _status_from_boundary(source: dict[str, Any]) -> str:
    value = source.get("runtime_commit_boundary_status")
    final_decision = source.get("final_runtime_commit_boundary_decision")
    if not value and isinstance(final_decision, dict):
        value = final_decision.get("status")
    if not value:
        value = source.get("boundary_status") or source.get("status")
    return _status_readyish(value)


def _extract_status(source_name: str, source: dict[str, Any] | None) -> str:
    if source is None:
        return ""
    if source_name == "runtime_commit_boundary":
        return _status_from_boundary(source)
    if source_name == "runtime_atomic_writer":
        return _status_readyish(
            source.get("atomic_writer_status")
            or source.get("writer_status")
            or source.get("status")
        )
    if source_name == "runtime_backup_manager":
        return _status_readyish(source.get("backup_status") or source.get("status"))
    if source_name == "runtime_rollback_manager":
        return _status_readyish(source.get("rollback_status") or source.get("status"))
    if source_name == "runtime_commit_verifier":
        return _status_readyish(
            source.get("verification_status")
            or source.get("verify_status")
            or source.get("status")
        )
    if source_name == "runtime_commit_audit_record":
        return _status_readyish(source.get("audit_status") or source.get("status"))
    return _status_readyish(source.get("status"))


def _source_commit_id(source: dict[str, Any] | None) -> str:
    if not isinstance(source, dict):
        return ""
    return _text(source.get("commit_id"))


def _messages(source_name: str, source: dict[str, Any] | None, field: str) -> list[str]:
    if not isinstance(source, dict):
        return []
    raw = source.get(field) or []
    if not isinstance(raw, list):
        raw = [raw]
    return [f"{source_name}: {_text(item)}" for item in raw if _text(item)]


def _find_safety_violations(value: Any, source_name: str) -> list[str]:
    issues: list[str] = []
    if isinstance(value, dict):
        for flag in INPUT_FORBIDDEN_FLAGS:
            if value.get(flag) is True:
                issues.append(f"{source_name}: safety flag {flag} must be false")
        safety_flags = value.get("safety_flags")
        if isinstance(safety_flags, dict):
            for flag in INPUT_FORBIDDEN_FLAGS:
                if safety_flags.get(flag) is True:
                    issues.append(f"{source_name}: safety flag {flag} must be false")
        for child in value.values():
            issues.extend(_find_safety_violations(child, source_name))
    elif isinstance(value, (list, tuple, set)):
        for child in value:
            issues.extend(_find_safety_violations(child, source_name))
    return issues


def _build_source_statuses(
    boundary_result: dict[str, Any],
    atomic_writer_plan: dict[str, Any] | None,
    backup_plan: dict[str, Any] | None,
    rollback_plan: dict[str, Any] | None,
    verifier_result: dict[str, Any] | None,
    audit_record: dict[str, Any] | None,
) -> dict[str, str]:
    return {
        "runtime_commit_boundary": _extract_status("runtime_commit_boundary", boundary_result),
        "runtime_backup_manager": _extract_status("runtime_backup_manager", backup_plan),
        "runtime_atomic_writer": _extract_status("runtime_atomic_writer", atomic_writer_plan),
        "runtime_commit_verifier": _extract_status("runtime_commit_verifier", verifier_result),
        "runtime_rollback_manager": _extract_status("runtime_rollback_manager", rollback_plan),
        "runtime_commit_audit_record": _extract_status("runtime_commit_audit_record", audit_record),
        "runtime_commit_executor": STATUS_READY,
    }


def _build_execution_steps(
    source_statuses: dict[str, str],
    issues_by_component: dict[str, list[str]],
    warnings_by_component: dict[str, list[str]],
) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for index, (step_name, component, required) in enumerate(STEP_SEQUENCE, start=1):
        source_status = source_statuses.get(component, "")
        blocked = source_status == STATUS_BLOCKED
        invalid = source_status == STATUS_INVALID
        if component == "runtime_commit_executor":
            source_status = STATUS_READY
        steps.append(
            {
                "step_index": index,
                "step_name": step_name,
                "component": component,
                "required": required,
                "source_status": source_status,
                "planned_action": f"plan_{step_name.lower()}",
                "callable_invoked": False,
                "execution_performed": False,
                "result_consumed": bool(source_status) or component == "runtime_commit_executor",
                "blocked": blocked,
                "issues": list(issues_by_component.get(component, [])),
                "warnings": list(warnings_by_component.get(component, [])),
            }
        )
    return steps


def _build_state_machine(status: str) -> dict[str, Any]:
    if status == STATUS_READY:
        states = list(STATE_SEQUENCE_READY)
        terminal = "READY_TO_EXECUTE"
        handoff_state = "GATE_ENTRY_READY"
        state_semantics = "Gate entry available"
    elif status == STATUS_BLOCKED:
        states = ["CREATED", "VALIDATING", "BLOCKED"]
        terminal = "BLOCKED"
        handoff_state = "GATE_ENTRY_BLOCKED"
        state_semantics = "Gate entry blocked"
    else:
        states = ["CREATED", "VALIDATING", "INVALID"]
        terminal = "INVALID"
        handoff_state = "GATE_ENTRY_INVALID"
        state_semantics = "Gate entry invalid"
    return {
        "initial_state": "CREATED",
        "current_state": terminal,
        "terminal_state": terminal,
        "handoff_state": handoff_state,
        "state_semantics": state_semantics,
        "states": states,
        "transitions": [
            {"from": states[index], "to": states[index + 1], "preview_only": True}
            for index in range(len(states) - 1)
        ],
        "preview_only": True,
    }


def _build_result(
    *,
    status: str,
    commit_id: Any,
    execution_steps: list[dict[str, Any]],
    state_machine: dict[str, Any],
    source_statuses: dict[str, str],
    issues: list[str],
    warnings: list[str],
    rollback_required: bool,
    protected_target_violation: bool,
    created_at_preview: str,
) -> dict[str, Any]:
    blocked_step = ""
    for step in execution_steps:
        if step["blocked"]:
            blocked_step = step["step_name"]
            break
    required_steps = [step["step_name"] for step in execution_steps if step["required"]]
    optional_steps = [step["step_name"] for step in execution_steps if not step["required"]]
    executable = status == STATUS_READY
    execution_plan = {
        "plan_type": "RUNTIME_COMMIT_EXECUTION_PLAN_PREVIEW",
        "execution_phase": "PRE_EXECUTION_PLAN",
        "commit_id": _text(commit_id),
        "execution_mode": "PREVIEW",
        "executable": executable,
        "execution_performed": False,
        "executable_without_real_gate": False,
        "current_state": state_machine["current_state"],
        "initial_state": state_machine["initial_state"],
        "terminal_state": state_machine["terminal_state"],
        "handoff_state": state_machine["handoff_state"],
        "state_semantics": state_machine["state_semantics"],
        "rollback_required": rollback_required,
        "step_count": len(execution_steps),
        "required_steps": required_steps,
        "optional_steps": optional_steps,
        "blocked_step": blocked_step,
        "protected_target_violation": protected_target_violation,
        "actual_execution_performed": False,
    }
    execution_metadata = {
        "plan_type": "RUNTIME_COMMIT_EXECUTION_PLAN_PREVIEW",
        "execution_phase": "PRE_EXECUTION_PLAN",
        "execution_performed": False,
        "executable_without_real_gate": False,
        "created_at_preview": created_at_preview,
        "issue_count": len(issues),
        "warning_count": len(warnings),
        "preview_only": True,
    }
    return {
        "executor_status": status,
        "preview_only": True,
        "commit_id": _text(commit_id),
        "plan_type": "RUNTIME_COMMIT_EXECUTION_PLAN_PREVIEW",
        "execution_phase": "PRE_EXECUTION_PLAN",
        "execution_performed": False,
        "executable_without_real_gate": False,
        "actual_execution": False,
        "execution_plan": execution_plan,
        "execution_steps": deepcopy(execution_steps),
        "state_machine": deepcopy(state_machine),
        "source_statuses": dict(source_statuses),
        "issues": list(issues),
        "warnings": list(warnings),
        "safety_flags": _build_safety_flags(),
        "execution_metadata": execution_metadata,
    }


def create_runtime_commit_execution_plan(
    commit_id: Any,
    boundary_result: Any,
    atomic_writer_plan: Any = None,
    backup_plan: Any = None,
    rollback_plan: Any = None,
    verifier_result: Any = None,
    audit_record: Any = None,
    execution_context: Any = None,
) -> dict[str, Any]:
    """Create a preview-only runtime commit execution plan."""
    return create_runtime_commit_execution_plan_preview(
        commit_id=commit_id,
        boundary_result=boundary_result,
        atomic_writer_plan=atomic_writer_plan,
        backup_plan=backup_plan,
        rollback_plan=rollback_plan,
        verifier_result=verifier_result,
        audit_record=audit_record,
        execution_context=execution_context,
    )


def create_runtime_commit_execution_plan_preview(
    commit_id: Any,
    boundary_result: Any,
    atomic_writer_plan: Any = None,
    backup_plan: Any = None,
    rollback_plan: Any = None,
    verifier_result: Any = None,
    audit_record: Any = None,
    execution_context: Any = None,
) -> dict[str, Any]:
    """Create a runtime commit execution plan preview.

    READY_TO_EXECUTE means the plan may enter the Execution Gate Preview. It
    does not mean runtime commit execution is allowed or performed.
    """
    created_at_preview = _now_text()
    issues: list[str] = []
    warnings: list[str] = []

    commit_str = _text(commit_id)
    if not commit_str:
        issues.append("commit_id is missing or empty")
    if not isinstance(boundary_result, dict):
        issues.append("boundary_result must be a dict")

    optional_sources = {
        "runtime_atomic_writer": atomic_writer_plan,
        "runtime_backup_manager": backup_plan,
        "runtime_rollback_manager": rollback_plan,
        "runtime_commit_verifier": verifier_result,
        "runtime_commit_audit_record": audit_record,
    }
    for component, source in optional_sources.items():
        if source is not None and not isinstance(source, dict):
            issues.append(f"{component} source must be a dict when provided")

    if issues:
        source_statuses = {
            "runtime_commit_boundary": "",
            "runtime_backup_manager": "",
            "runtime_atomic_writer": "",
            "runtime_commit_verifier": "",
            "runtime_rollback_manager": "",
            "runtime_commit_audit_record": "",
            "runtime_commit_executor": STATUS_READY,
        }
        state_machine = _build_state_machine(STATUS_INVALID)
        steps = _build_execution_steps(source_statuses, {}, {})
        return _build_result(
            status=STATUS_INVALID,
            commit_id=commit_id,
            execution_steps=steps,
            state_machine=state_machine,
            source_statuses=source_statuses,
            issues=_dedupe(issues),
            warnings=warnings,
            rollback_required=False,
            protected_target_violation=False,
            created_at_preview=created_at_preview,
        )

    boundary = deepcopy(boundary_result)
    atomic_writer = deepcopy(atomic_writer_plan) if isinstance(atomic_writer_plan, dict) else None
    backup = deepcopy(backup_plan) if isinstance(backup_plan, dict) else None
    rollback = deepcopy(rollback_plan) if isinstance(rollback_plan, dict) else None
    verifier = deepcopy(verifier_result) if isinstance(verifier_result, dict) else None
    audit = deepcopy(audit_record) if isinstance(audit_record, dict) else None
    context = deepcopy(execution_context) if isinstance(execution_context, dict) else execution_context

    sources = {
        "runtime_commit_boundary": boundary,
        "runtime_atomic_writer": atomic_writer,
        "runtime_backup_manager": backup,
        "runtime_rollback_manager": rollback,
        "runtime_commit_verifier": verifier,
        "runtime_commit_audit_record": audit,
    }
    source_statuses = _build_source_statuses(boundary, atomic_writer, backup, rollback, verifier, audit)
    issues_by_component: dict[str, list[str]] = {}
    warnings_by_component: dict[str, list[str]] = {}
    protected_target_violation = False

    for component, source in sources.items():
        if source is None:
            continue
        source_commit_id = _source_commit_id(source)
        if source_commit_id and source_commit_id != commit_str:
            issues_by_component.setdefault(component, []).append(f"{component}: commit_id mismatch")
        issues_by_component.setdefault(component, []).extend(_messages(component, source, "issues"))
        warnings_by_component.setdefault(component, []).extend(_messages(component, source, "warnings"))
        issues_by_component.setdefault(component, []).extend(_find_safety_violations(source, component))
        if _contains_protected_rules_target(source):
            protected_target_violation = True
            issues_by_component.setdefault(component, []).append(
                f"{component}: protected routines rules.json target included"
            )

    if isinstance(context, dict):
        context_issues = _find_safety_violations(context, "execution_context")
        if context_issues:
            issues_by_component.setdefault("runtime_commit_executor", []).extend(context_issues)
        if _contains_protected_rules_target(context):
            protected_target_violation = True
            issues_by_component.setdefault("runtime_commit_executor", []).append(
                "execution_context: protected routines rules.json target included"
            )

    required_missing = []
    for component, value in (
        ("runtime_backup_manager", backup),
        ("runtime_atomic_writer", atomic_writer),
        ("runtime_commit_verifier", verifier),
        ("runtime_commit_audit_record", audit),
    ):
        if value is None:
            required_missing.append(component)
            issues_by_component.setdefault(component, []).append(f"{component}: required source is missing")

    rollback_required = bool(verifier and verifier.get("rollback_required") is True)
    if rollback_required:
        issues_by_component.setdefault("runtime_commit_verifier", []).append(
            "runtime_commit_verifier: rollback_required is true"
        )
        if rollback is None:
            warnings_by_component.setdefault("runtime_rollback_manager", []).append(
                "runtime_rollback_manager: rollback plan missing while rollback_required is true"
            )

    for component, source_status in source_statuses.items():
        if component == "runtime_commit_executor":
            continue
        if source_status == STATUS_INVALID:
            issues_by_component.setdefault(component, []).append(f"{component}: status is INVALID")
        elif source_status == STATUS_BLOCKED:
            issues_by_component.setdefault(component, []).append(f"{component}: status is not READY")

    all_issues: list[str] = []
    all_warnings: list[str] = []
    for _step, component, _required in STEP_SEQUENCE:
        all_issues.extend(issues_by_component.get(component, []))
        all_warnings.extend(warnings_by_component.get(component, []))
    all_issues = _dedupe(all_issues)
    all_warnings = _dedupe(all_warnings)

    status = STATUS_READY
    if (
        not commit_str
        or protected_target_violation
        or any("must be false" in issue for issue in all_issues)
        or any("commit_id mismatch" in issue for issue in all_issues)
        or any(source_status == STATUS_INVALID for source_status in source_statuses.values())
    ):
        status = STATUS_INVALID
    elif (
        required_missing
        or rollback_required
        or all_issues
        or any(
            source_status == STATUS_BLOCKED
            for component, source_status in source_statuses.items()
            if component != "runtime_rollback_manager"
        )
        or (rollback is not None and source_statuses["runtime_rollback_manager"] != STATUS_READY)
    ):
        status = STATUS_BLOCKED

    state_machine = _build_state_machine(status)
    execution_steps = _build_execution_steps(source_statuses, issues_by_component, warnings_by_component)
    return _build_result(
        status=status,
        commit_id=commit_id,
        execution_steps=execution_steps,
        state_machine=state_machine,
        source_statuses=source_statuses,
        issues=all_issues,
        warnings=all_warnings,
        rollback_required=rollback_required,
        protected_target_violation=protected_target_violation,
        created_at_preview=created_at_preview,
    )
