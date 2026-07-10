# -*- coding: utf-8 -*-
"""Runtime Commit transaction/lock/idempotency contracts (M6-11).

This module defines deterministic contracts needed before Real Runtime Commit
execution. It never acquires locks, writes manifests, consumes tokens, calls
M6 components, touches runtime files, or connects to GUI/Broker/SendOrder/Chejan.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
import hashlib
import json
import math
from typing import Any


TRANSACTION_CONTRACT_VERSION = "M6_RUNTIME_TRANSACTION_V1"
LOCK_CONTRACT_VERSION = "M6_RUNTIME_LOCK_V1"

TRANSACTION_STAGES = {
    "MANIFEST_CREATED",
    "LOCK_PENDING",
    "LOCK_ACQUIRED",
    "IDEMPOTENCY_VALIDATED",
    "TOKEN_VALIDATED",
    "BACKUP_STARTED",
    "BACKUP_DONE",
    "WRITE_STARTED",
    "WRITE_DONE",
    "VERIFY_STARTED",
    "VERIFY_DONE",
    "ROLLBACK_STARTED",
    "ROLLBACK_DONE",
    "POST_ROLLBACK_VERIFY_DONE",
    "JOURNAL_RECORDED",
    "TOKEN_CONSUMED",
    "COMPLETED",
    "FAILED",
    "MANUAL_RESTORE_REQUIRED",
}

TRANSACTION_STATUSES = {
    "CREATED",
    "IN_PROGRESS",
    "COMMITTED",
    "ROLLED_BACK",
    "ABORTED",
    "FAILED",
    "MANUAL_RESTORE_REQUIRED",
}

LOCK_STATUSES = {
    "LOCK_REQUESTED",
    "LOCK_ACQUIRED",
    "LOCK_DENIED",
    "LOCK_EXPIRED",
    "LOCK_RELEASED",
    "LOCK_FAILED",
}

IDEMPOTENCY_STATUSES = {
    "NEW",
    "IN_PROGRESS_BLOCKED",
    "ALREADY_COMMITTED",
    "RETRY_ALLOWED",
    "RECOVERY_REQUIRED",
    "MANUAL_REVIEW_REQUIRED",
    "INVALID",
}

SAFETY_FLAG_NAMES = (
    "runtime_write",
    "file_write_called",
    "backup_created",
    "rollback_executed",
    "token_consumed",
    "lock_acquired",
    "lock_released",
    "journal_written",
    "manifest_persisted",
    "gui_update_called",
    "send_order_called",
    "chejan_called",
    "broker_called",
    "sqlite_write",
    "rules_write",
    "actual_execution",
)

HASH_FIELDS = (
    "contract_version",
    "transaction_id",
    "commit_id",
    "target_paths",
    "target_set_hash",
    "execution_plan_hash",
    "approval_token_id",
    "expected_payload_hash",
    "backup_plan_hash",
    "rollback_plan_hash",
    "transaction_status",
    "current_stage",
    "stage_history",
    "recovery_required",
    "manual_restore_required",
)


def build_runtime_commit_transaction_manifest(
    *,
    commit_id: Any,
    target_paths: Any,
    execution_plan_hash: Any,
    approval_token_id: Any,
    expected_payload_hash: Any,
    backup_plan_hash: Any = None,
    rollback_plan_hash: Any = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic Runtime Commit transaction manifest contract."""
    issues: list[str] = []
    warnings: list[str] = []

    commit_text = _validate_required_text(commit_id, "commit_id", issues)
    execution_hash = _validate_required_text(execution_plan_hash, "execution_plan_hash", issues)
    token_id = _validate_required_text(approval_token_id, "approval_token_id", issues)
    payload_hash = _validate_required_text(expected_payload_hash, "expected_payload_hash", issues)
    backup_hash = _validate_optional_text(backup_plan_hash, "backup_plan_hash", issues)
    rollback_hash = _validate_optional_text(rollback_plan_hash, "rollback_plan_hash", issues)
    canonical_targets, target_issues = _canonical_target_paths(target_paths)
    issues.extend(target_issues)

    target_set_hash = _hash_json({"target_paths": canonical_targets}) if not target_issues else ""
    transaction_id = (
        _hash_json(
            {
                "commit_id": commit_text,
                "target_set_hash": target_set_hash,
                "execution_plan_hash": execution_hash,
            }
        )
        if commit_text and target_set_hash and execution_hash
        else ""
    )

    meta = deepcopy(metadata) if isinstance(metadata, dict) else {}
    if metadata is not None and not isinstance(metadata, dict):
        issues.append("metadata must be a dict when provided")
    issues.extend(_json_issues(meta, "metadata"))

    return {
        "contract_version": TRANSACTION_CONTRACT_VERSION,
        "transaction_id": transaction_id,
        "commit_id": commit_text,
        "transaction_status": "CREATED" if not issues else "FAILED",
        "target_paths": canonical_targets,
        "target_set_hash": target_set_hash,
        "execution_plan_hash": execution_hash,
        "approval_token_id": token_id,
        "expected_payload_hash": payload_hash,
        "backup_plan_hash": backup_hash,
        "rollback_plan_hash": rollback_hash,
        "current_stage": "MANIFEST_CREATED",
        "stage_history": ["MANIFEST_CREATED"],
        "recovery_required": False,
        "manual_restore_required": False,
        "issues": _dedupe(issues),
        "warnings": warnings,
        "safety_flags": _safety_flags(),
        "metadata": meta,
    }


def validate_runtime_commit_transaction_manifest(
    manifest: Any,
    *,
    expected_commit_id: Any = None,
    expected_target_set_hash: Any = None,
) -> dict[str, Any]:
    """Validate a Runtime Commit transaction manifest contract."""
    issues: list[str] = []
    warnings: list[str] = []
    if not isinstance(manifest, dict):
        return _validation(False, ["manifest must be a dict"], warnings)

    required = (
        "contract_version",
        "transaction_id",
        "commit_id",
        "transaction_status",
        "target_paths",
        "target_set_hash",
        "execution_plan_hash",
        "approval_token_id",
        "expected_payload_hash",
        "current_stage",
        "stage_history",
        "recovery_required",
        "manual_restore_required",
        "issues",
        "warnings",
        "safety_flags",
        "metadata",
    )
    for field in required:
        if field not in manifest:
            issues.append(f"required field missing: {field}")
    if issues:
        return _validation(False, issues, warnings)

    if manifest["contract_version"] != TRANSACTION_CONTRACT_VERSION:
        issues.append("contract_version is invalid")
    commit_text = _validate_required_text(manifest.get("commit_id"), "commit_id", issues)
    if expected_commit_id is not None and commit_text != _validate_required_text(
        expected_commit_id, "expected_commit_id", issues
    ):
        issues.append("commit_id mismatch")

    canonical_targets, target_issues = _canonical_target_paths(manifest.get("target_paths"))
    issues.extend(target_issues)
    target_hash = _hash_json({"target_paths": canonical_targets}) if not target_issues else ""
    if target_hash and manifest.get("target_set_hash") != target_hash:
        issues.append("target_set_hash mismatch")
    if expected_target_set_hash is not None and manifest.get("target_set_hash") != expected_target_set_hash:
        issues.append("target_set_hash mismatch with expected")

    for field in ("execution_plan_hash", "approval_token_id", "expected_payload_hash"):
        _validate_required_text(manifest.get(field), field, issues)
    for field in ("backup_plan_hash", "rollback_plan_hash"):
        _validate_optional_text(manifest.get(field), field, issues)

    if manifest.get("transaction_status") not in TRANSACTION_STATUSES:
        issues.append("transaction_status is invalid")
    if manifest.get("current_stage") not in TRANSACTION_STAGES:
        issues.append("current_stage is invalid")
    if not isinstance(manifest.get("stage_history"), list) or not manifest["stage_history"]:
        issues.append("stage_history must be a non-empty list")
    else:
        for stage in manifest["stage_history"]:
            if stage not in TRANSACTION_STAGES:
                issues.append(f"stage_history contains invalid stage: {stage}")
    for field in ("recovery_required", "manual_restore_required"):
        if manifest.get(field) is not False:
            issues.append(f"{field} must be False in initial contract")
    issues.extend(_validate_safety_flags(manifest.get("safety_flags")))
    issues.extend(_json_issues(manifest.get("metadata"), "metadata"))
    issues.extend(_json_issues(_hash_payload(manifest), "manifest hash payload"))

    expected_transaction_id = (
        _hash_json(
            {
                "commit_id": commit_text,
                "target_set_hash": manifest.get("target_set_hash"),
                "execution_plan_hash": manifest.get("execution_plan_hash"),
            }
        )
        if commit_text and manifest.get("target_set_hash") and manifest.get("execution_plan_hash")
        else ""
    )
    if expected_transaction_id and manifest.get("transaction_id") != expected_transaction_id:
        issues.append("transaction_id mismatch")

    return _validation(not issues, issues, warnings)


def build_runtime_commit_lock_contract(
    *,
    commit_id: Any,
    target_paths: Any,
    transaction_id: Any,
    owner_id: Any,
    acquired_at: Any = None,
    expires_at: Any = None,
) -> dict[str, Any]:
    """Build a deterministic lock request contract without acquiring a lock."""
    issues: list[str] = []
    commit_text = _validate_required_text(commit_id, "commit_id", issues)
    transaction_text = _validate_required_text(transaction_id, "transaction_id", issues)
    owner_text = _validate_required_text(owner_id, "owner_id", issues)
    canonical_targets, target_issues = _canonical_target_paths(target_paths)
    issues.extend(target_issues)
    target_set_hash = _hash_json({"target_paths": canonical_targets}) if not target_issues else ""
    lock_key = (
        _hash_json({"commit_id": commit_text, "target_set_hash": target_set_hash})
        if commit_text and target_set_hash
        else ""
    )
    for field_name, value in (("acquired_at", acquired_at), ("expires_at", expires_at)):
        if value is not None and not isinstance(value, str):
            issues.append(f"{field_name} must be a string when provided")

    return {
        "contract_version": LOCK_CONTRACT_VERSION,
        "lock_key": lock_key,
        "commit_id": commit_text,
        "transaction_id": transaction_text,
        "target_set_hash": target_set_hash,
        "owner_id": owner_text,
        "lock_status": "LOCK_REQUESTED" if not issues else "LOCK_FAILED",
        "acquired_at": acquired_at or "",
        "expires_at": expires_at or "",
        "reentrant": False,
        "issues": _dedupe(issues),
        "warnings": [],
        "safety_flags": _safety_flags(),
        "metadata": {"lock_execution_performed": False},
    }


def validate_runtime_commit_lock_contract(
    lock_contract: Any,
    *,
    expected_commit_id: Any = None,
    expected_transaction_id: Any = None,
) -> dict[str, Any]:
    """Validate a Runtime Commit lock contract without acquiring a lock."""
    issues: list[str] = []
    warnings: list[str] = []
    if not isinstance(lock_contract, dict):
        return _validation(False, ["lock_contract must be a dict"], warnings)

    required = (
        "contract_version",
        "lock_key",
        "commit_id",
        "transaction_id",
        "target_set_hash",
        "owner_id",
        "lock_status",
        "acquired_at",
        "expires_at",
        "reentrant",
        "issues",
        "warnings",
        "safety_flags",
        "metadata",
    )
    for field in required:
        if field not in lock_contract:
            issues.append(f"required field missing: {field}")
    if issues:
        return _validation(False, issues, warnings)

    if lock_contract.get("contract_version") != LOCK_CONTRACT_VERSION:
        issues.append("contract_version is invalid")
    commit_text = _validate_required_text(lock_contract.get("commit_id"), "commit_id", issues)
    transaction_text = _validate_required_text(lock_contract.get("transaction_id"), "transaction_id", issues)
    _validate_required_text(lock_contract.get("owner_id"), "owner_id", issues)
    _validate_required_text(lock_contract.get("target_set_hash"), "target_set_hash", issues)

    if expected_commit_id is not None and commit_text != _validate_required_text(
        expected_commit_id, "expected_commit_id", issues
    ):
        issues.append("commit_id mismatch")
    if expected_transaction_id is not None and transaction_text != _validate_required_text(
        expected_transaction_id, "expected_transaction_id", issues
    ):
        issues.append("transaction_id mismatch")
    if lock_contract.get("lock_status") not in LOCK_STATUSES:
        issues.append("lock_status is invalid")
    if lock_contract.get("reentrant") is not False:
        issues.append("reentrant must be False")
    expected_lock_key = (
        _hash_json({"commit_id": commit_text, "target_set_hash": lock_contract.get("target_set_hash")})
        if commit_text and lock_contract.get("target_set_hash")
        else ""
    )
    if expected_lock_key and lock_contract.get("lock_key") != expected_lock_key:
        issues.append("lock_key mismatch")
    issues.extend(_validate_safety_flags(lock_contract.get("safety_flags")))
    return _validation(not issues, issues, warnings)


def evaluate_runtime_commit_idempotency(
    *,
    commit_id: Any,
    target_set_hash: Any,
    transaction_state: Any,
    existing_records: Any = None,
) -> dict[str, Any]:
    """Evaluate idempotency using provided records only."""
    issues: list[str] = []
    warnings: list[str] = []
    commit_text = _validate_required_text(commit_id, "commit_id", issues)
    target_hash = _validate_required_text(target_set_hash, "target_set_hash", issues)
    state = _validate_required_text(transaction_state, "transaction_state", issues)
    records = [] if existing_records is None else deepcopy(existing_records)
    if not isinstance(records, list):
        issues.append("existing_records must be a list when provided")
        records = []
    if state and state not in TRANSACTION_STATUSES:
        issues.append("transaction_state is invalid")
    if issues:
        return _idempotency_result("INVALID", commit_text, target_hash, [], False, False, False, issues, warnings)

    matching = [
        record
        for record in records
        if isinstance(record, dict)
        and record.get("commit_id") == commit_text
        and record.get("target_set_hash") == target_hash
    ]
    malformed = [record for record in matching if record.get("transaction_status") not in TRANSACTION_STATUSES]
    if malformed:
        return _idempotency_result(
            "INVALID", commit_text, target_hash, matching, False, False, False, ["existing record status is invalid"], warnings
        )
    if _has_conflict(matching):
        return _idempotency_result(
            "MANUAL_REVIEW_REQUIRED",
            commit_text,
            target_hash,
            matching,
            False,
            False,
            True,
            ["conflicting idempotency records"],
            warnings,
        )
    if not matching:
        return _idempotency_result("NEW", commit_text, target_hash, [], True, False, False, [], warnings)

    statuses = {record.get("transaction_status") for record in matching}
    stages = {record.get("current_stage") for record in matching}
    if any(record.get("lock_active") is True for record in matching):
        return _idempotency_result(
            "IN_PROGRESS_BLOCKED",
            commit_text,
            target_hash,
            matching,
            False,
            False,
            False,
            ["active lock exists"],
            warnings,
        )
    if "COMMITTED" in statuses:
        return _idempotency_result("ALREADY_COMMITTED", commit_text, target_hash, matching, False, False, False, [], warnings)
    if "MANUAL_RESTORE_REQUIRED" in statuses or "FAILED" in statuses:
        return _idempotency_result("MANUAL_REVIEW_REQUIRED", commit_text, target_hash, matching, False, False, True, [], warnings)
    if "IN_PROGRESS" in statuses:
        if stages & {"WRITE_STARTED", "WRITE_DONE", "VERIFY_STARTED"}:
            return _idempotency_result("RECOVERY_REQUIRED", commit_text, target_hash, matching, False, True, False, [], warnings)
        return _idempotency_result(
            "IN_PROGRESS_BLOCKED", commit_text, target_hash, matching, False, False, False, [], warnings
        )
    if statuses <= {"ABORTED"}:
        if stages & {"WRITE_STARTED", "WRITE_DONE", "VERIFY_STARTED"}:
            return _idempotency_result("RECOVERY_REQUIRED", commit_text, target_hash, matching, False, True, False, [], warnings)
        return _idempotency_result("RETRY_ALLOWED", commit_text, target_hash, matching, True, False, False, [], warnings)
    if statuses <= {"ROLLED_BACK"}:
        return _idempotency_result("RETRY_ALLOWED", commit_text, target_hash, matching, True, False, False, [], warnings)
    return _idempotency_result(
        "MANUAL_REVIEW_REQUIRED", commit_text, target_hash, matching, False, False, True, ["unhandled state"], warnings
    )


def build_runtime_commit_transaction_hash(manifest: dict[str, Any]) -> str:
    """Build the deterministic hash for a valid transaction manifest."""
    validation = validate_runtime_commit_transaction_manifest(manifest)
    if not validation["valid"]:
        raise ValueError("; ".join(validation["issues"]))
    return _hash_json(_hash_payload(manifest))


def _idempotency_result(
    status: str,
    commit_id: str,
    target_set_hash: str,
    matching_records: list[dict[str, Any]],
    execution_allowed: bool,
    recovery_required: bool,
    manual_review_required: bool,
    issues: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "idempotency_status": status,
        "commit_id": commit_id,
        "target_set_hash": target_set_hash,
        "matching_records": deepcopy(matching_records),
        "execution_allowed": execution_allowed,
        "recovery_required": recovery_required,
        "manual_review_required": manual_review_required,
        "issues": _dedupe(issues),
        "warnings": _dedupe(warnings),
        "safety_flags": _safety_flags(),
    }


def _has_conflict(records: list[dict[str, Any]]) -> bool:
    if len([record for record in records if record.get("lock_active") is True]) > 1:
        return True
    statuses = {record.get("transaction_status") for record in records}
    transaction_ids = {record.get("transaction_id") for record in records if record.get("transaction_id")}
    final_statuses = statuses & {"COMMITTED", "ABORTED", "FAILED", "MANUAL_RESTORE_REQUIRED", "ROLLED_BACK"}
    if "COMMITTED" in statuses and (statuses & {"IN_PROGRESS", "ABORTED"}):
        return True
    if len(transaction_ids) > 1 and final_statuses:
        return True
    if len(final_statuses) > 1:
        return True
    return False


def _canonical_target_paths(value: Any) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    if not isinstance(value, (list, tuple)):
        return [], ["target_paths must be a list or tuple"]
    if not value:
        return [], ["target_paths must not be empty"]
    canonical: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            issues.append("target path must be a string")
            continue
        if item != item.strip() or not item:
            issues.append("target path must be non-empty and trimmed")
            continue
        normalized = item.replace("\\", "/").lower()
        parts = [part for part in normalized.split("/") if part]
        if ".." in parts:
            issues.append(f"path traversal is not allowed: {item}")
        if len(parts) >= 3 and "routines" in parts and parts[-1] == "rules.json":
            issues.append(f"protected routines rules.json target: {item}")
        if normalized in seen:
            issues.append(f"duplicate target path: {item}")
        seen.add(normalized)
        canonical.append(normalized)
    return sorted(canonical), _dedupe(issues)


def _validate_required_text(value: Any, field: str, issues: list[str]) -> str:
    if not isinstance(value, str):
        issues.append(f"{field} must be a string")
        return ""
    if value != value.strip() or not value:
        issues.append(f"{field} must be non-empty and trimmed")
        return ""
    return value


def _validate_optional_text(value: Any, field: str, issues: list[str]) -> str:
    if value is None:
        return ""
    return _validate_required_text(value, field, issues)


def _safety_flags() -> dict[str, bool]:
    return {flag: False for flag in SAFETY_FLAG_NAMES}


def _validate_safety_flags(value: Any) -> list[str]:
    issues: list[str] = []
    if not isinstance(value, dict):
        return ["safety_flags must be a dict"]
    for flag in SAFETY_FLAG_NAMES:
        if value.get(flag) is not False:
            issues.append(f"safety flag {flag} must be bool False")
    return issues


def _validation(valid: bool, issues: list[str], warnings: list[str]) -> dict[str, Any]:
    return {"valid": valid, "issues": _dedupe(issues), "warnings": _dedupe(warnings), "preview_only": True}


def _hash_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    return {field: deepcopy(manifest.get(field)) for field in HASH_FIELDS}


def _hash_json(value: Any) -> str:
    _assert_json_value(value)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _json_issues(value: Any, label: str) -> list[str]:
    try:
        _assert_json_value(value)
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        return [f"{label} is not canonical JSON serializable: {exc}"]
    return []


def _assert_json_value(value: Any) -> None:
    if isinstance(value, bool) or value is None or isinstance(value, str) or isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite float is not allowed")
        return
    if isinstance(value, (Path, datetime, date, bytes, set)):
        raise TypeError(f"{type(value).__name__} is not allowed")
    if isinstance(value, list):
        for item in value:
            _assert_json_value(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("dict keys must be strings")
            _assert_json_value(item)
        return
    raise TypeError(f"{type(value).__name__} is not allowed")


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
