# -*- coding: utf-8 -*-
"""Runtime Commit Verifier for Real Runtime Commit (M6-4).

This module builds a *preview-only* commit verification plan based on a backup_plan
and rollback_plan. It does NOT execute any commit, does NOT modify any files,
and does NOT touch protected runtime files.

Scope boundaries (M6-4):
- ``preview_only`` is always True.
- No file copy / creation / write of any kind.
- No backup directory creation or modification.
- No modification of ``runtime/*.json`` or ``routines/*/rules.json``.
- Atomic Writer is never invoked (``atomic_writer_called`` stays False).
- No GUI / SendOrder / Chejan / Broker / SQLite / rules writes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Sequence

STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"


def _is_protected_rules_json(p: Path) -> bool:
    return p.name == "rules.json"


def _build_safety_flags() -> dict[str, bool]:
    return {
        "runtime_write": False,
        "position_write": False,
        "balance_write": False,
        "audit_write": False,
        "file_write_called": False,
        "backup_created": False,
        "rollback_executed": False,
        "gui_update_called": False,
        "send_order_called": False,
        "chejan_called": False,
        "broker_called": False,
        "sqlite_write": False,
        "rules_write": False,
        "atomic_writer_called": False,
        "backup_manager_called": False,
    }


def create_runtime_commit_verifier_plan(
    commit_id: Optional[str] = None,
    backup_plan: Optional[dict[str, Any]] = None,
    rollback_plan: Optional[dict[str, Any]] = None,
    runtime_snapshot: Optional[Any] = None,
    operator_context: Optional[Any] = None,
) -> dict[str, Any]:
    """Build a preview-only commit verification plan.

    Args:
        commit_id: Identifier for the runtime commit.
        backup_plan: Backup plan dict from create_runtime_backup_plan.
        rollback_plan: Rollback plan dict from create_runtime_rollback_plan.
        runtime_snapshot: Optional runtime snapshot reference (not used).
        operator_context: Optional operator context metadata (not used).

    Returns:
        Dict with verify_status (READY/BLOCKED/INVALID), preview_only,
        commit_id, verify_metadata, verify_strategy, safety_flags (all False),
        issues, warnings.
    """
    issues: list[str] = []
    warnings: list[str] = []

    # ---- INVALID: commit_id missing ----
    if not commit_id or not str(commit_id).strip():
        issues.append("commit_id is missing or empty")

    # ---- INVALID: backup_plan missing or not dict ----
    if backup_plan is None:
        issues.append("backup_plan is missing")
    elif not isinstance(backup_plan, dict):
        issues.append("backup_plan is not a dict")

    # ---- INVALID: rollback_plan missing or not dict ----
    if rollback_plan is None:
        issues.append("rollback_plan is missing")
    elif not isinstance(rollback_plan, dict):
        issues.append("rollback_plan is not a dict")

    if issues:
        return _build_plan(
            status=STATUS_INVALID,
            commit_id=commit_id,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
            issues=issues,
            warnings=warnings,
            runtime_snapshot=runtime_snapshot,
            operator_context=operator_context,
        )

    # ---- INVALID: backup_plan.preview_only != True ----
    if backup_plan.get("preview_only", False) is not True:
        issues.append("backup_plan preview_only is not True")

    # ---- INVALID: rollback_plan.preview_only != True ----
    if rollback_plan.get("preview_only", False) is not True:
        issues.append("rollback_plan preview_only is not True")

    # ---- INVALID: commit_id mismatch ----
    bp_commit_id = backup_plan.get("commit_id", "")
    rp_commit_id = rollback_plan.get("commit_id", "")
    if str(bp_commit_id).strip() != str(commit_id).strip():
        issues.append("commit_id mismatch with backup_plan")
    if str(rp_commit_id).strip() != str(commit_id).strip():
        issues.append("commit_id mismatch with rollback_plan")

    # ---- INVALID: backup_plan.backup_status == INVALID ----
    backup_status = backup_plan.get("backup_status", "")
    if backup_status == STATUS_INVALID:
        issues.append("backup_plan backup_status is INVALID")

    # ---- INVALID: rollback_plan.rollback_status == INVALID ----
    rollback_status = rollback_plan.get("rollback_status", "")
    if rollback_status == STATUS_INVALID:
        issues.append("rollback_plan rollback_status is INVALID")

    # ---- INVALID: rules.json in backup_targets ----
    if not issues and backup_plan.get("backup_targets"):
        for t in backup_plan.get("backup_targets", []):
            source = t.get("source", "")
            if source:
                p = Path(source)
                if _is_protected_rules_json(p):
                    issues.append(f"protected rules.json in backup_targets: {p}")

    # ---- INVALID: rules.json in rollback_targets ----
    if not issues and rollback_plan.get("rollback_targets"):
        for t in rollback_plan.get("rollback_targets", []):
            source = t.get("source", "")
            if source:
                p = Path(source)
                if _is_protected_rules_json(p):
                    issues.append(f"protected rules.json in rollback_targets: {p}")

    if issues:
        return _build_plan(
            status=STATUS_INVALID,
            commit_id=commit_id,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
            issues=issues,
            warnings=warnings,
            runtime_snapshot=runtime_snapshot,
            operator_context=operator_context,
        )

    # ---- BLOCKED: backup_plan.backup_status != READY ----
    if backup_status != STATUS_READY:
        issues.append("backup_plan backup_status is not READY")

    # ---- BLOCKED: rollback_plan.rollback_status != READY ----
    if rollback_status != STATUS_READY:
        issues.append("rollback_plan rollback_status is not READY")

    if issues:
        return _build_plan(
            status=STATUS_BLOCKED,
            commit_id=commit_id,
            backup_plan=backup_plan,
            rollback_plan=rollback_plan,
            issues=issues,
            warnings=warnings,
            runtime_snapshot=runtime_snapshot,
            operator_context=operator_context,
        )

    # ---- READY ----
    return _build_plan(
        status=STATUS_READY,
        commit_id=commit_id,
        backup_plan=backup_plan,
        rollback_plan=rollback_plan,
        issues=issues,
        warnings=warnings,
        runtime_snapshot=runtime_snapshot,
        operator_context=operator_context,
    )


def _build_plan(
    *,
    status: str,
    commit_id: Optional[str],
    backup_plan: Optional[dict[str, Any]],
    rollback_plan: Optional[dict[str, Any]],
    issues: list[str],
    warnings: list[str],
    runtime_snapshot: Optional[Any],
    operator_context: Optional[Any],
) -> dict[str, Any]:
    commit_str = str(commit_id).strip() if commit_id is not None else ""

    bp_targets = []
    rp_targets = []
    if isinstance(backup_plan, dict):
        bp_targets = backup_plan.get("backup_targets", [])
    if isinstance(rollback_plan, dict):
        rp_targets = rollback_plan.get("rollback_targets", [])

    verify_metadata = {
        "plan_type": "RUNTIME_COMMIT_VERIFY_PLAN",
        "commit_id": commit_str,
        "backup_target_count": len(bp_targets),
        "rollback_target_count": len(rp_targets),
        "preview_only": True,
        "has_runtime_snapshot": runtime_snapshot is not None,
        "has_operator_context": operator_context is not None,
    }

    verify_strategy = {
        "strategy": "verify_before_commit",
        "backup_verified": status == STATUS_READY,
        "rollback_ready": status == STATUS_READY,
    }

    return {
        "verify_status": status,
        "preview_only": True,
        "commit_id": commit_str,
        "verify_metadata": verify_metadata,
        "verify_strategy": verify_strategy,
        "safety_flags": _build_safety_flags(),
        "issues": issues,
        "warnings": warnings,
    }


def _deep_compare_dict(
    expected: dict[str, Any],
    actual: dict[str, Any],
    compare_fields: Optional[Sequence[str]] = None,
) -> tuple[bool, Optional[str]]:
    """Deep compare two dicts, ignoring key order. Returns (is_match, mismatch_detail)."""
    if compare_fields is not None:
        exp_subset = {k: expected.get(k) for k in compare_fields}
        act_subset = {k: actual.get(k) for k in compare_fields}
        if exp_subset != act_subset:
            return False, f"field mismatch: {compare_fields}"
        return True, None

    if set(expected.keys()) != set(actual.keys()):
        return False, f"key set mismatch: expected keys {set(expected.keys())}, actual keys {set(actual.keys())}"

    for key in expected:
        exp_val = expected[key]
        act_val = actual[key]

        if isinstance(exp_val, list) and isinstance(act_val, list):
            if exp_val != act_val:
                return False, f"list mismatch at key {key}"
        elif isinstance(exp_val, dict) and isinstance(act_val, dict):
            sub_match, sub_detail = _deep_compare_dict(exp_val, act_val, compare_fields)
            if not sub_match:
                return False, f"dict mismatch at key {key}: {sub_detail}"
        else:
            if exp_val != act_val:
                return False, f"value mismatch at key {key}"

    return True, None


def _deep_compare_data(
    expected: Any,
    actual: Any,
    compare_fields: Optional[Sequence[str]] = None,
) -> tuple[bool, Optional[str]]:
    """Compare data structures. Lists must match in order; dicts ignore key order."""
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return False, f"list length mismatch: {len(expected)} vs {len(actual)}"
        for i, (e, a) in enumerate(zip(expected, actual)):
            if isinstance(e, dict) and isinstance(a, dict):
                match, detail = _deep_compare_dict(e, a, compare_fields)
                if not match:
                    return False, f"list[{i}]: {detail}"
            elif e != a:
                return False, f"list[{i}] mismatch: {e} vs {a}"
        return True, None
    elif isinstance(expected, dict) and isinstance(actual, dict):
        return _deep_compare_dict(expected, actual, compare_fields)
    else:
        if expected != actual:
            return False, f"value mismatch: {expected} vs {actual}"
        return True, None


def verify_runtime_commit(
    commit_id: Optional[str] = None,
    expected_targets: Optional[dict[str, Any]] = None,
    actual_targets: Optional[dict[str, Any]] = None,
    verification_plan: Optional[dict[str, Any]] = None,
    runtime_snapshot: Optional[Any] = None,
    operator_context: Optional[Any] = None,
) -> dict[str, Any]:
    """Verify runtime commit results by comparing expected vs actual targets.

    Args:
        commit_id: Identifier for the runtime commit.
        expected_targets: Dict mapping target names to expected content.
        actual_targets: Dict mapping target names to actual content after commit.
        verification_plan: Optional plan dict with compare_fields and strict_compare.
        runtime_snapshot: Optional runtime snapshot reference (not used).
        operator_context: Optional operator context metadata (not used).

    Returns:
        Dict with verification_status, preview_only=True, commit_id,
        target_verification_results, matched_targets, mismatched_targets,
        missing_targets, unexpected_targets, rollback_required,
        verification_metadata, safety_flags (all False), issues, warnings.
    """
    issues: list[str] = []
    warnings: list[str] = []

    # ---- INVALID: commit_id missing ----
    if not commit_id or not str(commit_id).strip():
        issues.append("commit_id is missing or empty")

    # ---- INVALID: expected_targets not dict ----
    if expected_targets is None:
        issues.append("expected_targets is missing")
    elif not isinstance(expected_targets, dict):
        issues.append("expected_targets is not a dict")

    # ---- INVALID: actual_targets not dict ----
    if actual_targets is None:
        issues.append("actual_targets is missing")
    elif not isinstance(actual_targets, dict):
        issues.append("actual_targets is not a dict")

    # ---- INVALID: verification_plan not dict ----
    if verification_plan is not None and not isinstance(verification_plan, dict):
        issues.append("verification_plan is not a dict")

    if issues:
        return _build_verification_plan(
            status=STATUS_INVALID,
            commit_id=commit_id,
            expected_targets=expected_targets if isinstance(expected_targets, dict) else {},
            actual_targets=actual_targets if isinstance(actual_targets, dict) else {},
            verification_plan=verification_plan if isinstance(verification_plan, dict) else {},
            issues=issues,
            warnings=warnings,
            runtime_snapshot=runtime_snapshot,
            operator_context=operator_context,
        )

    # ---- Check for protected rules.json ----
    for key in list(expected_targets.keys()) + list(actual_targets.keys()):
        if isinstance(key, str) and key.endswith("rules.json"):
            issues.append(f"protected rules.json target: {key}")

    if issues:
        return _build_verification_plan(
            status=STATUS_INVALID,
            commit_id=commit_id,
            expected_targets=expected_targets,
            actual_targets=actual_targets,
            verification_plan=verification_plan if isinstance(verification_plan, dict) else {},
            issues=issues,
            warnings=warnings,
            runtime_snapshot=runtime_snapshot,
            operator_context=operator_context,
        )

    # Extract options from verification_plan
    compare_fields = None
    strict_compare = False
    if verification_plan:
        if "compare_fields" in verification_plan:
            compare_fields = verification_plan["compare_fields"]
        if verification_plan.get("strict_compare", False):
            strict_compare = True

    # ---- Perform verification ----
    expected_keys = set(expected_targets.keys())
    actual_keys = set(actual_targets.keys())

    missing_targets = expected_keys - actual_keys
    unexpected_targets = actual_keys - expected_keys
    common_keys = expected_keys & actual_keys

    matched_targets: list[str] = []
    mismatched_targets: list[str] = []

    for key in common_keys:
        exp_val = expected_targets[key]
        act_val = actual_targets[key]

        # Check hash if present
        if isinstance(exp_val, dict) and isinstance(act_val, dict):
            exp_hash = exp_val.get("expected_hash")
            act_hash = act_val.get("actual_hash")
            if exp_hash is not None or act_hash is not None:
                if exp_hash != act_hash:
                    mismatched_targets.append(key)
                else:
                    matched_targets.append(key)
                continue

        # Deep compare values
        match, _ = _deep_compare_data(exp_val, act_val, compare_fields)
        if match:
            matched_targets.append(key)
        else:
            mismatched_targets.append(key)

    # ---- Determine status ----
    status = STATUS_READY
    has_missing = bool(missing_targets)
    has_mismatch = bool(mismatched_targets)
    has_unexpected = bool(unexpected_targets)

    if has_missing:
        status = STATUS_BLOCKED

    if has_mismatch:
        status = STATUS_BLOCKED

    if has_unexpected and strict_compare:
        status = STATUS_BLOCKED

    if not expected_keys:
        status = STATUS_BLOCKED

    # Check for INVALID conditions (rules.json or type errors)
    for issue in issues:
        if "rules.json" in issue:
            status = STATUS_INVALID
            break
        if "commit_id is missing" in issue or "not a dict" in issue:
            status = STATUS_INVALID
            break

    # Add final issues summary
    if has_missing:
        issues.append(f"missing targets: {missing_targets}")
    if has_mismatch:
        issues.append(f"content mismatch for targets: {mismatched_targets}")
    if has_unexpected and strict_compare:
        warnings.append(f"unexpected targets: {unexpected_targets}")
        issues.append(f"strict_compare violation: unexpected targets {unexpected_targets}")
    elif has_unexpected:
        warnings.append(f"unexpected targets: {unexpected_targets}")
    if not expected_keys:
        issues.append("expected_targets is empty")

    # ---- Determine rollback_required ----
    rollback_required = bool(missing_targets or mismatched_targets)

    return _build_verification_plan(
        status=status,
        commit_id=commit_id,
        expected_targets=expected_targets,
        actual_targets=actual_targets,
        verification_plan=verification_plan if isinstance(verification_plan, dict) else {},
        issues=issues,
        warnings=warnings,
        runtime_snapshot=runtime_snapshot,
        operator_context=operator_context,
        matched_targets=matched_targets,
        mismatched_targets=mismatched_targets,
        missing_targets=list(missing_targets),
        unexpected_targets=list(unexpected_targets),
        rollback_required=rollback_required,
    )


def _build_verification_plan(
    *,
    status: str,
    commit_id: Optional[str],
    expected_targets: dict[str, Any],
    actual_targets: dict[str, Any],
    verification_plan: dict[str, Any],
    issues: list[str],
    warnings: list[str],
    runtime_snapshot: Optional[Any],
    operator_context: Optional[Any],
    matched_targets: Optional[list[str]] = None,
    mismatched_targets: Optional[list[str]] = None,
    missing_targets: Optional[list[str]] = None,
    unexpected_targets: Optional[list[str]] = None,
    rollback_required: bool = False,
) -> dict[str, Any]:
    commit_str = str(commit_id).strip() if commit_id is not None else ""

    target_verification_results = []
    for key in expected_targets:
        result = {
            "target": key,
            "matched": key in (matched_targets or []),
            "missing": key in (missing_targets or []),
            "mismatched": key in (mismatched_targets or []),
        }
        target_verification_results.append(result)

    verification_metadata = {
        "plan_type": "RUNTIME_COMMIT_VERIFICATION",
        "commit_id": commit_str,
        "expected_target_count": len(expected_targets),
        "actual_target_count": len(actual_targets),
        "matched_count": len(matched_targets or []),
        "mismatched_count": len(mismatched_targets or []),
        "missing_count": len(missing_targets or []),
        "unexpected_count": len(unexpected_targets or []),
        "preview_only": True,
        "has_runtime_snapshot": runtime_snapshot is not None,
        "has_operator_context": operator_context is not None,
    }

    return {
        "verification_status": status,
        "preview_only": True,
        "commit_id": commit_str,
        "target_verification_results": target_verification_results,
        "matched_targets": matched_targets or [],
        "mismatched_targets": mismatched_targets or [],
        "missing_targets": missing_targets or [],
        "unexpected_targets": unexpected_targets or [],
        "rollback_required": rollback_required,
        "verification_metadata": verification_metadata,
        "safety_flags": _build_safety_flags(),
        "issues": issues,
        "warnings": warnings,
    }