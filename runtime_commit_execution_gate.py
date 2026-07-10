# -*- coding: utf-8 -*-
"""Runtime Commit Execution Gate (M6-7).

This module validates whether a Runtime Commit Executor Preview plan may be
handed to a future real executor. It never consumes tokens, persists approvals,
calls M6 components, executes runtime commits, or reads/writes runtime files.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any


STATUS_APPROVED = "APPROVED"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
EXPECTED_STEP_SEQUENCE = (
    "VALIDATE_BOUNDARY",
    "PREPARE_BACKUP",
    "PREPARE_ATOMIC_WRITE",
    "VERIFY_COMMIT",
    "EVALUATE_ROLLBACK",
    "BUILD_AUDIT_RECORD",
    "COMPLETE",
)

SAFETY_FLAG_NAMES = (
    "runtime_write",
    "position_write",
    "balance_write",
    "file_write_called",
    "backup_created",
    "rollback_executed",
    "verification_executed",
    "audit_written",
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
    "audit_record_called",
    "executor_called",
    "actual_execution",
    "execution_token_consumed",
    "approval_persisted",
)

INPUT_FORBIDDEN_FLAGS = (
    "runtime_write",
    "position_write",
    "balance_write",
    "file_write_called",
    "backup_created",
    "rollback_executed",
    "verification_executed",
    "audit_write",
    "audit_written",
    "audit_file_written",
    "gui_update_called",
    "send_order_called",
    "chejan_called",
    "broker_called",
    "sqlite_write",
    "rules_write",
    "actual_execution",
    "actual_execution_performed",
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _upper(value: Any) -> str:
    return _text(value).upper()


def _build_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in SAFETY_FLAG_NAMES}


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


def _stable_plan_payload(execution_plan: dict[str, Any]) -> dict[str, Any]:
    plan = execution_plan.get("execution_plan") if isinstance(execution_plan.get("execution_plan"), dict) else {}
    state_machine = execution_plan.get("state_machine") if isinstance(execution_plan.get("state_machine"), dict) else {}
    return {
        "commit_id": execution_plan.get("commit_id"),
        "executor_status": execution_plan.get("executor_status"),
        "execution_steps": execution_plan.get("execution_steps", []),
        "source_statuses": execution_plan.get("source_statuses", {}),
        "rollback_required": plan.get("rollback_required"),
        "protected_target_violation": plan.get("protected_target_violation"),
        "state_machine_final_state": state_machine.get("terminal_state") or state_machine.get("current_state"),
    }


def build_execution_plan_hash(execution_plan: dict[str, Any]) -> str:
    """Return the deterministic SHA-256 hash for approval/token binding."""
    payload = _stable_plan_payload(execution_plan)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validate_steps(execution_plan: dict[str, Any]) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    warnings: list[str] = []
    steps = execution_plan.get("execution_steps")
    if not isinstance(steps, list):
        return ["execution_steps must be a list"], warnings
    names = [step.get("step_name") if isinstance(step, dict) else "" for step in steps]
    if len(names) != len(set(names)):
        issues.append("execution_steps contains duplicate step_name")
        return issues, warnings
    missing = [name for name in EXPECTED_STEP_SEQUENCE if name not in names]
    if missing:
        issues.append(f"required execution steps missing: {missing}")
        return issues, warnings
    if tuple(names) != EXPECTED_STEP_SEQUENCE:
        issues.append("execution_steps order is invalid")
    return issues, warnings


def _status_from_plan(execution_plan: dict[str, Any]) -> str:
    status = _upper(execution_plan.get("executor_status"))
    if "INVALID" in status:
        return STATUS_INVALID
    if "BLOCKED" in status:
        return STATUS_BLOCKED
    if status == "READY":
        return STATUS_APPROVED
    return STATUS_BLOCKED


def _messages(source: dict[str, Any], field: str, prefix: str) -> list[str]:
    raw = source.get(field) or []
    if not isinstance(raw, list):
        raw = [raw]
    return [f"{prefix}: {_text(item)}" for item in raw if _text(item)]


def _build_result(
    *,
    status: str,
    commit_id: Any,
    plan_hash: str,
    ready_for_real_executor: bool,
    approval_summary: dict[str, Any],
    execution_plan_summary: dict[str, Any],
    validation_results: dict[str, Any],
    issues: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "gate_status": status,
        "commit_id": _text(commit_id),
        "preview_only": True,
        "gate_type": "RUNTIME_COMMIT_EXECUTION_GATE_PREVIEW",
        "gate_phase": "PRE_REAL_EXECUTION_VALIDATION",
        "approval_validation_only": True,
        "real_gate_active": False,
        "execution_allowed": False,
        "actual_execution": False,
        "token_consumed": False,
        "token_persisted": False,
        "commit_lock_acquired": False,
        "replay_protection_active": False,
        "ready_for_real_executor": ready_for_real_executor,
        "approval_summary": deepcopy(approval_summary),
        "execution_plan_summary": deepcopy(execution_plan_summary),
        "validation_results": deepcopy(validation_results),
        "issues": list(issues),
        "warnings": list(warnings),
        "safety_flags": _build_safety_flags(),
        "gate_metadata": {
            "gate_type": "RUNTIME_COMMIT_EXECUTION_GATE_PREVIEW",
            "gate_phase": "PRE_REAL_EXECUTION_VALIDATION",
            "approval_validation_only": True,
            "real_gate_active": False,
            "plan_hash": plan_hash,
            "actual_execution": False,
            "execution_token_consumed": False,
            "token_consumed": False,
            "token_persisted": False,
            "approval_persisted": False,
            "commit_lock_acquired": False,
            "replay_protection_active": False,
            "preview_only": True,
        },
    }


def evaluate_runtime_commit_execution_gate(
    commit_id: Any,
    execution_plan: Any,
    approval_context: Any = None,
    execution_token: Any = None,
    expected_plan_hash: Any = None,
    operator_context: Any = None,
) -> dict[str, Any]:
    """Validate approval and token binding for a runtime commit execution plan."""
    return evaluate_runtime_commit_execution_gate_preview(
        commit_id=commit_id,
        execution_plan=execution_plan,
        approval_context=approval_context,
        execution_token=execution_token,
        expected_plan_hash=expected_plan_hash,
        operator_context=operator_context,
    )


def evaluate_runtime_commit_execution_gate_preview(
    commit_id: Any,
    execution_plan: Any,
    approval_context: Any = None,
    execution_token: Any = None,
    expected_plan_hash: Any = None,
    operator_context: Any = None,
) -> dict[str, Any]:
    """Validate approval and token binding without activating the real gate."""
    issues: list[str] = []
    warnings: list[str] = []
    commit_str = _text(commit_id)

    if not commit_str:
        issues.append("commit_id is missing or empty")
    if not isinstance(execution_plan, dict):
        issues.append("execution_plan must be a dict")
    if approval_context is not None and not isinstance(approval_context, dict):
        issues.append("approval_context must be a dict when provided")
    if execution_token is not None and not isinstance(execution_token, dict):
        issues.append("execution_token must be a dict when provided")

    if issues:
        return _build_result(
            status=STATUS_INVALID,
            commit_id=commit_id,
            plan_hash="",
            ready_for_real_executor=False,
            approval_summary={},
            execution_plan_summary={},
            validation_results={"valid": False, "issues": list(issues), "warnings": []},
            issues=_dedupe(issues),
            warnings=[],
        )

    plan = deepcopy(execution_plan)
    approval = deepcopy(approval_context) if isinstance(approval_context, dict) else None
    token = deepcopy(execution_token) if isinstance(execution_token, dict) else None
    operator = deepcopy(operator_context) if isinstance(operator_context, dict) else operator_context

    plan_hash = build_execution_plan_hash(plan)
    plan_commit_id = _text(plan.get("commit_id"))
    if plan_commit_id != commit_str:
        issues.append("execution_plan commit_id mismatch")

    plan_status = _status_from_plan(plan)
    if plan_status == STATUS_INVALID:
        issues.append("execution_plan status is INVALID")
    elif plan_status == STATUS_BLOCKED:
        issues.append("execution_plan status is not READY")

    nested_plan = plan.get("execution_plan") if isinstance(plan.get("execution_plan"), dict) else {}
    state_machine = plan.get("state_machine") if isinstance(plan.get("state_machine"), dict) else {}
    final_state = state_machine.get("terminal_state") or state_machine.get("current_state")
    rollback_required = nested_plan.get("rollback_required") is True
    if final_state != "READY_TO_EXECUTE":
        issues.append("state_machine final state is not READY_TO_EXECUTE")
    if rollback_required:
        issues.append("execution_plan rollback_required is true")

    step_issues, step_warnings = _validate_steps(plan)
    issues.extend(step_issues)
    warnings.extend(step_warnings)
    issues.extend(_messages(plan, "issues", "execution_plan"))
    warnings.extend(_messages(plan, "warnings", "execution_plan"))

    if expected_plan_hash is None or not _text(expected_plan_hash):
        issues.append("expected_plan_hash is missing")
    elif _text(expected_plan_hash) != plan_hash:
        issues.append("expected_plan_hash mismatch")

    if approval is None:
        issues.append("approval_context is missing")
    else:
        if approval.get("approved") is not True:
            issues.append("approval_context approved must be true")
        if _text(approval.get("approved_commit_id")) != commit_str:
            issues.append("approval_context approved_commit_id mismatch")
        if _text(approval.get("approval_scope")) != "RUNTIME_COMMIT":
            issues.append("approval_context approval_scope is invalid")
        if _text(approval.get("approved_plan_hash")) != plan_hash:
            issues.append("approval_context approved_plan_hash mismatch")
        if not _text(approval.get("approved_by")):
            issues.append("approval_context approved_by is missing")
        if not _text(approval.get("approval_reason")):
            issues.append("approval_context approval_reason is missing")

    if token is None:
        issues.append("execution_token is missing")
    else:
        if _text(token.get("commit_id")) != commit_str:
            issues.append("execution_token commit_id mismatch")
        if _text(token.get("plan_hash")) != plan_hash:
            issues.append("execution_token plan_hash mismatch")
        if _text(token.get("scope")) != "RUNTIME_COMMIT_EXECUTION":
            issues.append("execution_token scope is invalid")
        if token.get("single_use") is not True:
            issues.append("execution_token single_use must be true")
        if token.get("consumed") is True:
            issues.append("execution_token consumed must be false")

    for source_name, value in (
        ("execution_plan", plan),
        ("approval_context", approval),
        ("execution_token", token),
        ("operator_context", operator),
    ):
        if value is None:
            continue
        issues.extend(_find_safety_violations(value, source_name))
        if _contains_protected_rules_target(value):
            issues.append(f"{source_name}: protected routines rules.json target included")

    issues = _dedupe(issues)
    warnings = _dedupe(warnings)

    invalid_markers = (
        "mismatch",
        "scope is invalid",
        "status is INVALID",
        "order is invalid",
        "duplicate",
        "protected routines rules.json",
        "must be a dict",
        "safety flag",
    )
    status = STATUS_APPROVED
    if any(marker in issue for issue in issues for marker in invalid_markers):
        status = STATUS_INVALID
    elif issues:
        status = STATUS_BLOCKED

    ready_for_real_executor = status == STATUS_APPROVED
    approval_summary = {
        "approved": bool(approval and approval.get("approved") is True),
        "approved_by": _text(approval.get("approved_by")) if approval else "",
        "approval_scope": _text(approval.get("approval_scope")) if approval else "",
        "approval_reason_present": bool(approval and _text(approval.get("approval_reason"))),
        "token_present": token is not None,
        "token_consumed": False,
        "preview_only": True,
    }
    execution_plan_summary = {
        "executor_status": plan.get("executor_status"),
        "plan_hash": plan_hash,
        "final_state": final_state,
        "rollback_required": rollback_required,
        "step_count": len(plan.get("execution_steps") or []),
        "actual_execution": False,
        "preview_only": True,
    }
    validation_results = {
        "valid": status == STATUS_APPROVED,
        "ready_for_real_executor": ready_for_real_executor,
        "plan_hash": plan_hash,
        "issues": list(issues),
        "warnings": list(warnings),
        "preview_only": True,
    }

    return _build_result(
        status=status,
        commit_id=commit_id,
        plan_hash=plan_hash,
        ready_for_real_executor=ready_for_real_executor,
        approval_summary=approval_summary,
        execution_plan_summary=execution_plan_summary,
        validation_results=validation_results,
        issues=issues,
        warnings=warnings,
    )
