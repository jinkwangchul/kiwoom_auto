# -*- coding: utf-8 -*-
"""BUY Runtime Commit Core dry-run adapter.

This adapter translates a BUY Runtime Commit Gate Preview into dry-run-only
Runtime Commit Core inputs. It calls only preview/contract builders and never
calls the real executor, consumes tokens, acquires locks, writes persistence,
creates backups, writes journals, mutates runtime files, enqueues orders, calls
SendOrder/Broker/Chejan, or updates GUI state.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from runtime_commit_executor import create_runtime_commit_execution_plan_preview
from runtime_commit_transaction_contract import build_runtime_commit_transaction_manifest


ADAPTER_TYPE = "BUY_RUNTIME_COMMIT_CORE_DRY_RUN_ADAPTER"
DRY_RUN_VERSION = "BUY_RUNTIME_COMMIT_CORE_DRY_RUN_V1"
STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"

FORBIDDEN_TRUE_FLAGS = (
    "runtime_write",
    "queue_write",
    "file_write_called",
    "backup_created",
    "rollback_executed",
    "token_consumed",
    "lock_acquired",
    "journal_written",
    "manifest_persisted",
    "persistence_write",
    "sqlite_write",
    "send_order_called",
    "broker_called",
    "chejan_connected",
    "gui_updated",
    "runtime_commit_real_executor_called",
    "real_executor_called",
    "actual_execution",
)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return deepcopy(value) if isinstance(value, list) else []


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _dry_run_id(
    *,
    gate_id: str,
    preview_id: str,
    projection_hash: str,
    policy_hash: str,
    approved_rule_hash: str,
    runtime_after_hash: str,
) -> str:
    digest = _stable_hash({
        "gate_id": gate_id,
        "preview_id": preview_id,
        "projection_hash": projection_hash,
        "policy_hash": policy_hash,
        "approved_rule_hash": approved_rule_hash,
        "runtime_after_hash": runtime_after_hash,
    })[:24].upper()
    return "BUY_RUNTIME_COMMIT_DRY_RUN_{}".format(digest)


def _result(
    *,
    status: str,
    runtime_commit_dry_run: dict[str, Any] | None = None,
    transaction_contract_preview: dict[str, Any] | None = None,
    apply_plan_preview: dict[str, Any] | None = None,
    verification_plan_preview: dict[str, Any] | None = None,
    rollback_plan_preview: dict[str, Any] | None = None,
    guard_diagnostics: list[dict[str, Any]] | None = None,
    execution_snapshot: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
    issues: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "adapter_type": ADAPTER_TYPE,
        "status": status,
        "dry_run_only": True,
        "runtime_commit_core_called": True,
        "runtime_commit_real_executor_called": False,
        "approval_token_consumed": False,
        "approval_token_used": False,
        "lock_acquired": False,
        "backup_created": False,
        "journal_written": False,
        "persistence_write": False,
        "runtime_write": False,
        "queue_write": False,
        "send_order_called": False,
        "broker_called": False,
        "chejan_connected": False,
        "gui_updated": False,
        "runtime_commit_dry_run": deepcopy(runtime_commit_dry_run) if isinstance(runtime_commit_dry_run, dict) else None,
        "transaction_contract_preview": deepcopy(transaction_contract_preview)
        if isinstance(transaction_contract_preview, dict)
        else None,
        "apply_plan_preview": deepcopy(apply_plan_preview) if isinstance(apply_plan_preview, dict) else None,
        "verification_plan_preview": deepcopy(verification_plan_preview)
        if isinstance(verification_plan_preview, dict)
        else None,
        "rollback_plan_preview": deepcopy(rollback_plan_preview) if isinstance(rollback_plan_preview, dict) else None,
        "guard_diagnostics": deepcopy(guard_diagnostics or []),
        "execution_snapshot": deepcopy(execution_snapshot or {}),
        "evidence": deepcopy(evidence or {}),
        "diagnostics": deepcopy(diagnostics or []),
        "issues": list(issues or []),
    }


def _find_forbidden_true_flags(value: Any, prefix: str = "input") -> list[str]:
    issues: list[str] = []
    if isinstance(value, dict):
        for flag in FORBIDDEN_TRUE_FLAGS:
            if value.get(flag) is True:
                issues.append(f"{prefix}.{flag} must be false")
        safety_flags = value.get("safety_flags")
        if isinstance(safety_flags, dict):
            for flag in FORBIDDEN_TRUE_FLAGS:
                if safety_flags.get(flag) is True:
                    issues.append(f"{prefix}.safety_flags.{flag} must be false")
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                issues.extend(_find_forbidden_true_flags(child, f"{prefix}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            if isinstance(child, (dict, list)):
                issues.extend(_find_forbidden_true_flags(child, f"{prefix}[{index}]"))
    return issues


def _extract_gate_payload(gate_result: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    gate = _as_dict(gate_result.get("runtime_commit_gate_preview"))
    commit_preview = _as_dict(gate_result.get("runtime_commit_preview"))
    patch_preview = _as_dict(gate_result.get("runtime_patch_preview"))
    execution_snapshot = _as_dict(gate_result.get("execution_snapshot")) or _as_dict(gate.get("execution_snapshot"))
    diagnostics = _as_list(gate_result.get("diagnostics"))
    return gate, commit_preview, patch_preview, execution_snapshot, diagnostics


def _changed_fields(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _build_apply_plan(*, dry_run_id: str, gate: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    changes = _as_dict(patch.get("changes"))
    return {
        "plan_type": "BUY_RUNTIME_COMMIT_APPLY_PLAN_PREVIEW",
        "dry_run_id": dry_run_id,
        "target": patch.get("target") or gate.get("runtime_target"),
        "operation": patch.get("operation", "preview_runtime_state_patch"),
        "changed_fields": sorted(changes),
        "patch_count": len(changes),
        "source_candidate_id": patch.get("source_candidate_id") or gate.get("candidate_id"),
        "source_signal_id": patch.get("source_signal_id") or gate.get("signal_id"),
        "runtime_state_hash_before": patch.get("runtime_state_hash_before") or gate.get("runtime_before_hash"),
        "runtime_state_hash_after_candidate": patch.get("runtime_state_hash_after_candidate") or gate.get("runtime_after_hash"),
        "preview_only": True,
        "apply_executed": False,
    }


def _build_verification_plan(*, dry_run_id: str, gate: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    return {
        "plan_type": "BUY_RUNTIME_COMMIT_VERIFICATION_PLAN_PREVIEW",
        "dry_run_id": dry_run_id,
        "verification_required": True,
        "verification_executed": False,
        "items": [
            {
                "verification_name": "runtime_before_hash_matches",
                "expected_hash": gate.get("runtime_before_hash"),
                "source_hash": patch.get("runtime_state_hash_before"),
                "preview_only": True,
            },
            {
                "verification_name": "runtime_after_hash_matches",
                "expected_hash": gate.get("runtime_after_hash"),
                "source_hash": patch.get("runtime_state_hash_after_candidate"),
                "preview_only": True,
            },
            {
                "verification_name": "projection_hash_present",
                "expected_hash": gate.get("projection_hash"),
                "preview_only": True,
            },
        ],
        "preview_only": True,
    }


def _build_rollback_plan(*, dry_run_id: str, gate: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    return {
        "plan_type": "BUY_RUNTIME_COMMIT_ROLLBACK_PLAN_PREVIEW",
        "rollback_status": STATUS_READY,
        "commit_id": dry_run_id,
        "dry_run_id": dry_run_id,
        "rollback_required_on_failure": True,
        "rollback_executed": False,
        "rollback_target": patch.get("target"),
        "restore_hash": gate.get("runtime_before_hash"),
        "source_candidate_id": gate.get("candidate_id"),
        "preview_only": True,
    }


def _build_core_execution_plan(
    *,
    commit_id: str,
    apply_plan: dict[str, Any],
    verification_plan: dict[str, Any],
    rollback_plan: dict[str, Any],
    gate: dict[str, Any],
    dry_run_id: str,
) -> dict[str, Any]:
    boundary = {
        "runtime_commit_boundary_status": "RUNTIME_COMMIT_BOUNDARY_READY",
        "commit_id": commit_id,
        "preview_only": True,
        "issues": [],
        "warnings": [],
    }
    atomic = {
        "writer_status": "OK",
        "commit_id": commit_id,
        "preview_only": True,
        "target": apply_plan.get("target"),
        "issues": [],
        "warnings": [],
    }
    backup = {
        "backup_status": STATUS_READY,
        "commit_id": commit_id,
        "preview_only": True,
        "backup_targets": [{"target": apply_plan.get("target"), "preview_only": True}],
        "issues": [],
        "warnings": [],
    }
    verifier = {
        "verification_status": STATUS_READY,
        "commit_id": commit_id,
        "preview_only": True,
        "verification_plan": deepcopy(verification_plan),
        "rollback_required": False,
        "issues": [],
        "warnings": [],
    }
    audit = {
        "audit_status": STATUS_READY,
        "commit_id": commit_id,
        "preview_only": True,
        "dry_run_id": dry_run_id,
        "issues": [],
        "warnings": [],
    }
    context = {
        "dry_run_only": True,
        "gate_id": gate.get("gate_id"),
        "preview_id": gate.get("preview_id"),
        "runtime_write": False,
        "queue_write": False,
        "actual_execution": False,
    }
    return create_runtime_commit_execution_plan_preview(
        commit_id=commit_id,
        boundary_result=boundary,
        atomic_writer_plan=atomic,
        backup_plan=backup,
        rollback_plan=rollback_plan,
        verifier_result=verifier,
        audit_record=audit,
        execution_context=context,
    )


def build_buy_runtime_commit_core_dry_run(
    runtime_commit_gate_preview_result: Any,
    *,
    runtime_commit_preview: Any = None,
    runtime_patch_preview: Any = None,
    dry_run_context: Any = None,
) -> dict[str, Any]:
    """Build Runtime Commit Core dry-run from BUY Runtime Commit Gate Preview."""
    gate_result = deepcopy(_as_dict(runtime_commit_gate_preview_result))
    context = deepcopy(_as_dict(dry_run_context))
    if not gate_result:
        return _result(
            status=STATUS_INVALID,
            diagnostics=[{"stage": "input", "ok": False, "reason": "runtime_commit_gate_preview_result is required"}],
            issues=["runtime_commit_gate_preview_result is required"],
        )

    gate, embedded_commit_preview, embedded_patch, execution_snapshot, diagnostics = _extract_gate_payload(gate_result)
    commit_preview = deepcopy(_as_dict(runtime_commit_preview)) or embedded_commit_preview
    patch_preview = deepcopy(_as_dict(runtime_patch_preview)) or embedded_patch
    evidence = _as_dict(gate_result.get("evidence"))
    upstream_status = _clean_text(gate_result.get("status")).upper()
    issues = _as_list(gate_result.get("issues"))
    validation_issues: list[str] = []

    if upstream_status != STATUS_READY:
        status = STATUS_BLOCKED if upstream_status == STATUS_BLOCKED else STATUS_INVALID
        return _result(
            status=status,
            execution_snapshot=execution_snapshot,
            evidence=evidence,
            diagnostics=diagnostics + [{"stage": "dry_run_gate", "ok": False, "reason": "Gate status is not READY"}],
            issues=issues or ["Gate status is not READY"],
        )

    if not gate:
        validation_issues.append("GATE_PREVIEW_MISSING")
    if gate.get("commit_allowed") is not True:
        validation_issues.append("COMMIT_ALLOWED_FALSE")
    if gate.get("commit_execute") is True:
        validation_issues.append("COMMIT_EXECUTE_TRUE")
    if not _clean_text(gate.get("gate_id")):
        validation_issues.append("GATE_ID_MISSING")
    if not commit_preview:
        validation_issues.append("RUNTIME_COMMIT_PREVIEW_MISSING")
    if not patch_preview:
        validation_issues.append("RUNTIME_PATCH_PREVIEW_MISSING")
    if not execution_snapshot:
        validation_issues.append("EXECUTION_SNAPSHOT_MISSING")
    if gate_result.get("preview_only") is False or gate.get("preview_only") is False:
        validation_issues.append("PREVIEW_ONLY_FALSE")
    if gate_result.get("dry_run_only") is False or gate.get("dry_run_only") is False:
        validation_issues.append("DRY_RUN_ONLY_FALSE")
    validation_issues.extend(_find_forbidden_true_flags(gate_result, "gate_result"))
    validation_issues.extend(_find_forbidden_true_flags(commit_preview, "runtime_commit_preview"))
    validation_issues.extend(_find_forbidden_true_flags(patch_preview, "runtime_patch_preview"))

    changed_fields = gate.get("changed_fields")
    if not isinstance(changed_fields, list):
        validation_issues.append("CHANGED_FIELDS_MALFORMED")
    target = _clean_text(patch_preview.get("target") or commit_preview.get("runtime_target"))
    if not target:
        validation_issues.append("TARGET_UNKNOWN")

    comparisons = (
        ("preview_id", gate.get("preview_id"), commit_preview.get("preview_id")),
        ("candidate_id", gate.get("candidate_id"), commit_preview.get("candidate_id")),
        ("projection_hash", gate.get("projection_hash"), commit_preview.get("projection_hash")),
        ("runtime_before_hash", gate.get("runtime_before_hash"), commit_preview.get("runtime_before_hash")),
        ("runtime_after_hash", gate.get("runtime_after_hash"), commit_preview.get("runtime_after_hash")),
        ("policy_hash", gate.get("policy_hash"), commit_preview.get("policy_hash") or patch_preview.get("policy_hash")),
        ("approved_rule_hash", gate.get("approved_rule_hash"), commit_preview.get("approved_rule_hash")),
    )
    for name, left, right in comparisons:
        if _clean_text(left) and _clean_text(right) and _clean_text(left) != _clean_text(right):
            validation_issues.append(f"{name.upper()}_MISMATCH")
    if _clean_text(patch_preview.get("runtime_state_hash_before")) and _clean_text(gate.get("runtime_before_hash")):
        if _clean_text(patch_preview.get("runtime_state_hash_before")) != _clean_text(gate.get("runtime_before_hash")):
            validation_issues.append("RUNTIME_BEFORE_HASH_MISMATCH")
    if _clean_text(patch_preview.get("runtime_state_hash_after_candidate")) and _clean_text(gate.get("runtime_after_hash")):
        if _clean_text(patch_preview.get("runtime_state_hash_after_candidate")) != _clean_text(gate.get("runtime_after_hash")):
            validation_issues.append("RUNTIME_AFTER_HASH_MISMATCH")

    if validation_issues:
        return _result(
            status=STATUS_INVALID,
            execution_snapshot=execution_snapshot,
            evidence=evidence,
            diagnostics=diagnostics + [
                {"stage": "dry_run_validation", "ok": False, "reason": issue}
                for issue in validation_issues
            ],
            issues=issues + validation_issues,
        )

    gate_id = _clean_text(gate.get("gate_id"))
    preview_id = _clean_text(gate.get("preview_id"))
    projection_hash = _clean_text(gate.get("projection_hash"))
    policy_hash = _clean_text(gate.get("policy_hash"))
    approved_rule_hash = _clean_text(gate.get("approved_rule_hash"))
    runtime_after_hash = _clean_text(gate.get("runtime_after_hash"))
    dry_run_id = _dry_run_id(
        gate_id=gate_id,
        preview_id=preview_id,
        projection_hash=projection_hash,
        policy_hash=policy_hash,
        approved_rule_hash=approved_rule_hash,
        runtime_after_hash=runtime_after_hash,
    )
    commit_id = dry_run_id
    apply_plan = _build_apply_plan(dry_run_id=dry_run_id, gate=gate, patch=patch_preview)
    verification_plan = _build_verification_plan(dry_run_id=dry_run_id, gate=gate, patch=patch_preview)
    rollback_plan = _build_rollback_plan(dry_run_id=dry_run_id, gate=gate, patch=patch_preview)
    core_plan = _build_core_execution_plan(
        commit_id=commit_id,
        apply_plan=apply_plan,
        verification_plan=verification_plan,
        rollback_plan=rollback_plan,
        gate=gate,
        dry_run_id=dry_run_id,
    )
    execution_plan_hash = _stable_hash(core_plan.get("execution_plan", {}))
    target_paths = [target]
    transaction_contract = build_runtime_commit_transaction_manifest(
        commit_id=commit_id,
        target_paths=target_paths,
        execution_plan_hash=execution_plan_hash,
        approval_token_id="DRY_RUN_ONLY_NO_TOKEN",
        expected_payload_hash=runtime_after_hash,
        backup_plan_hash=_stable_hash({"dry_run_id": dry_run_id, "plan": "backup"}),
        rollback_plan_hash=_stable_hash(rollback_plan),
        metadata={
            "dry_run_only": True,
            "gate_id": gate_id,
            "preview_id": preview_id,
            "projection_hash": projection_hash,
            "policy_hash": policy_hash,
            "approved_rule_hash": approved_rule_hash,
        },
    )
    guard_diagnostics = [
        {"name": "gate_ready", "ok": True, "detail": "READY gate accepted for dry-run"},
        {"name": "commit_allowed", "ok": True, "detail": "commit_allowed true"},
        {"name": "no_real_executor", "ok": True, "detail": "real executor not called"},
        {"name": "no_token_lock_backup_journal_persistence", "ok": True, "detail": "side-effect components not called"},
    ]
    dry_run = {
        "dry_run_version": DRY_RUN_VERSION,
        "dry_run_id": dry_run_id,
        "dry_run_only": True,
        "gate_id": gate_id,
        "preview_id": preview_id,
        "projection_hash": projection_hash,
        "policy_hash": policy_hash,
        "approved_rule_hash": approved_rule_hash,
        "runtime_before_hash": gate.get("runtime_before_hash"),
        "runtime_after_hash": runtime_after_hash,
        "target": target,
        "changed_fields": _changed_fields(changed_fields),
        "core_execution_plan": deepcopy(core_plan),
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "commit_execute": False,
    }
    final_status = STATUS_READY if core_plan.get("executor_status") == STATUS_READY else STATUS_BLOCKED
    final_issues = issues + _as_list(core_plan.get("issues"))
    return _result(
        status=final_status,
        runtime_commit_dry_run=dry_run,
        transaction_contract_preview=transaction_contract,
        apply_plan_preview=apply_plan,
        verification_plan_preview=verification_plan,
        rollback_plan_preview=rollback_plan,
        guard_diagnostics=guard_diagnostics,
        execution_snapshot=execution_snapshot,
        evidence=evidence,
        diagnostics=diagnostics + [{"stage": "runtime_commit_core_dry_run", "ok": final_status == STATUS_READY, "reason": "dry-run ready"}],
        issues=final_issues,
    )
