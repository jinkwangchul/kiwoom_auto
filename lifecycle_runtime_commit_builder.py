# -*- coding: utf-8 -*-
"""Builder for Runtime Commit Adapter Request from Lifecycle Commit inputs.

This module constructs the complete adapter request needed by
lifecycle_runtime_commit_adapter from lifecycle_commit inputs.

Builder responsibilities:
1. Extract and validate lifecycle_commit_request from context
2. Build gate_result from commit_contract_preview
3. Build transaction_manifest from transaction data
4. Build storage_plan from storage_root (caller-provided)
5. Build guard_plan from owner_id and storage_root
6. Build token_storage_plan from token_id and storage_root
7. Build expected_targets and new_targets from commit_plan
8. Build consumer_id from context

Builder does NOT:
- Write files
- Verify writes
- Create backups
- Execute rollbacks
- Acquire locks or consume tokens
"""

from __future__ import annotations

import json
from copy import deepcopy
from hashlib import sha256
from typing import Any


BUILDER_TYPE = "LIFECYCLE_RUNTIME_COMMIT_BUILDER"

REQUIRED_LIFECYCLE_FIELDS = (
    "lifecycle_id",
    "commit_id",
    "transaction_id",
    "requested_action",
    "source_stage",
    "runtime_commit_boundary_status",
    "preview_only",
    "metadata",
)


def _as_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _hash_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def _validate_required_text(value: Any, field: str, issues: list[str]) -> str:
    if not isinstance(value, str):
        issues.append(f"{field} must be a string")
        return ""
    if value != value.strip() or not value:
        issues.append(f"{field} must be non-empty and trimmed")
        return ""
    return value


def build_gate_result(
    *,
    commit_contract_preview: dict[str, Any],
) -> dict[str, Any]:
    """Build gate_result from commit_contract_preview."""
    issues: list[str] = []
    warnings: list[str] = []

    commit_id = _as_text(commit_contract_preview.get("commit_id"))
    if not commit_id:
        issues.append("commit_contract_preview missing commit_id")

    return {
        "gate_status": "APPROVED",
        "commit_id": commit_id,
        "issues": issues,
        "warnings": warnings,
        "preview_only": True,
    }


def build_transaction_manifest(
    *,
    commit_id: str,
    transaction_id: str,
    execution_plan_hash: str,
    approval_token_id: str,
    expected_payload_hash: str,
    target_paths: list[str] | None = None,
    backup_plan_hash: str | None = None,
    rollback_plan_hash: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build transaction_manifest for runtime commit.

    Uses minimal required fields to construct a valid M6-11 transaction manifest.
    """
    issues: list[str] = []

    if not commit_id:
        issues.append("commit_id is required")
    if not transaction_id:
        issues.append("transaction_id is required")
    if not execution_plan_hash:
        issues.append("execution_plan_hash is required")
    if not approval_token_id:
        issues.append("approval_token_id is required")
    if not expected_payload_hash:
        issues.append("expected_payload_hash is required")

    targets = sorted(target_paths) if target_paths else []
    target_set_hash = _hash_json({"target_paths": targets}) if targets else ""

    meta = deepcopy(metadata) if isinstance(metadata, dict) else {}

    return {
        "contract_version": "M6_RUNTIME_TRANSACTION_V1",
        "transaction_id": transaction_id,
        "commit_id": commit_id,
        "target_paths": targets,
        "target_set_hash": target_set_hash,
        "execution_plan_hash": execution_plan_hash,
        "approval_token_id": approval_token_id,
        "expected_payload_hash": expected_payload_hash,
        "backup_plan_hash": backup_plan_hash or "",
        "rollback_plan_hash": rollback_plan_hash or "",
        "transaction_status": "CREATED",
        "current_stage": "MANIFEST_CREATED",
        "stage_history": ["MANIFEST_CREATED"],
        "recovery_required": False,
        "manual_restore_required": False,
        "issues": issues,
        "warnings": [],
        "safety_flags": {
            "runtime_write": False,
            "file_write_called": False,
            "backup_created": False,
            "rollback_executed": False,
            "token_consumed": False,
            "lock_acquired": False,
            "lock_released": False,
            "journal_written": False,
            "manifest_persisted": False,
            "gui_update_called": False,
            "send_order_called": False,
            "chejan_called": False,
            "broker_called": False,
            "sqlite_write": False,
            "rules_write": False,
            "actual_execution": False,
        },
        "metadata": meta,
    }


def build_storage_plan(
    *,
    storage_root: str,
    commit_id: str,
    transaction_id: str,
) -> dict[str, Any]:
    """Build storage_plan for transaction persistence."""
    issues: list[str] = []
    warnings: list[str] = []

    if not storage_root:
        issues.append("storage_root is required")

    storage_root = _as_text(storage_root) or ""

    return {
        "storage_status": "READY" if storage_root else "INVALID",
        "storage_root": storage_root,
        "transaction_dir": f"{storage_root}/transactions/{transaction_id}" if storage_root else "",
        "manifest_path": f"{storage_root}/transactions/{transaction_id}/manifest.json" if storage_root else "",
        "journal_path": f"{storage_root}/transactions/{transaction_id}/journal.jsonl" if storage_root else "",
        "commit_id": commit_id,
        "transaction_id": transaction_id,
        "issues": issues,
        "warnings": warnings,
        "preview_only": True,
        "safety_flags": {
            "manifest_written": False,
            "journal_written": False,
            "file_write_called": False,
            "runtime_write": False,
            "token_consumed": False,
            "lock_acquired": False,
            "backup_created": False,
            "rollback_executed": False,
            "actual_execution": False,
        },
    }


def build_guard_plan(
    *,
    storage_root: str,
    commit_id: str,
    transaction_id: str,
    target_set_hash: str,
    owner_id: str,
) -> dict[str, Any]:
    """Build guard_plan for lock acquisition."""
    issues: list[str] = []
    warnings: list[str] = []

    commit_id = _validate_required_text(commit_id, "commit_id", issues) or ""
    transaction_id = _validate_required_text(transaction_id, "transaction_id", issues) or ""
    target_set_hash = _validate_required_text(target_set_hash, "target_set_hash", issues) or ""
    owner_id = _validate_required_text(owner_id, "owner_id", issues) or ""
    storage_root = _as_text(storage_root) or ""

    lock_key = sha256(f'{{"commit_id":"{commit_id}","target_set_hash":"{target_set_hash}"}}'.encode("utf-8")).hexdigest()

    return {
        "guard_status": "READY" if not issues else "INVALID",
        "storage_root": storage_root,
        "lock_path": f"{storage_root}/locks/{lock_key}.json" if storage_root else "",
        "lock_key": lock_key,
        "commit_id": commit_id,
        "transaction_id": transaction_id,
        "target_set_hash": target_set_hash,
        "owner_id": owner_id,
        "issues": issues,
        "warnings": warnings,
        "preview_only": True,
        "safety_flags": {
            "file_write_called": False,
            "lock_acquired": False,
            "lock_released": False,
            "runtime_write": False,
            "token_consumed": False,
            "backup_created": False,
            "rollback_executed": False,
            "actual_execution": False,
        },
    }


def build_token_storage_plan(
    *,
    storage_root: str,
    token_id: str,
    commit_id: str,
) -> dict[str, Any]:
    """Build token_storage_plan for approval token."""
    issues: list[str] = []
    warnings: list[str] = []

    token_id = _validate_required_text(token_id, "token_id", issues) or ""
    commit_id = _validate_required_text(commit_id, "commit_id", issues) or ""
    storage_root = _as_text(storage_root) or ""

    return {
        "plan_status": "READY" if storage_root and token_id and commit_id else "INVALID",
        "storage_root": storage_root,
        "token_path": f"{storage_root}/approval_tokens/{token_id}.json" if storage_root and token_id else "",
        "claim_path": f"{storage_root}/approval_tokens/{token_id}.consume.lock" if storage_root and token_id else "",
        "token_id": token_id,
        "commit_id": commit_id,
        "issues": issues,
        "warnings": warnings,
        "preview_only": True,
        "safety_flags": {
            "file_write_called": False,
            "token_issued": False,
            "token_consumed": False,
            "claim_acquired": False,
            "runtime_write": False,
            "lock_acquired": False,
            "backup_created": False,
            "rollback_executed": False,
            "actual_execution": False,
        },
    }


def build_expected_targets(
    *,
    planned_targets: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build expected_targets from planned_targets."""
    if not isinstance(planned_targets, dict):
        return {}
    result = {}
    for path, value in planned_targets.items():
        normalized = path.replace("\\", "/").lower()
        result[normalized] = deepcopy(value)
    return result


def build_new_targets(
    *,
    planned_records: list[dict[str, Any]] | None = None,
    planned_targets: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build new_targets from planned_records and planned_targets.

    For lifecycle commits, new_targets represents the expected state after
    the runtime commit. This is derived from planned_targets.
    """
    if not isinstance(planned_targets, dict):
        return {}
    result = {}
    for path, value in planned_targets.items():
        normalized = path.replace("\\", "/").lower()
        if isinstance(value, dict):
            result[normalized] = deepcopy(value)
        else:
            result[normalized] = value
    return result


def build_consumer_id(
    *,
    owner_id: str | None = None,
    consumer_override: str | None = None,
) -> str:
    """Build consumer_id from owner_id or override."""
    if consumer_override:
        return _as_text(consumer_override)
    if owner_id:
        return _as_text(owner_id)
    return "lifecycle_consumer"


def validate_lifecycle_request(lifecycle_commit_request: Any) -> tuple[dict[str, Any], list[str]]:
    """Validate lifecycle_commit_request has all required fields."""
    if not isinstance(lifecycle_commit_request, dict):
        return {}, ["lifecycle_commit_request must be a dict"]
    missing = [field for field in REQUIRED_LIFECYCLE_FIELDS if field not in lifecycle_commit_request]
    if missing:
        return lifecycle_commit_request, [f"missing lifecycle field: {field}" for field in missing]
    return deepcopy(lifecycle_commit_request), []


def build_adapter_request(
    *,
    commit_contract_preview: dict[str, Any],
    commit_plan: dict[str, Any],
    storage_root: str,
    owner_id: str,
    token_id: str | None = None,
    execution_plan_hash: str | None = None,
    expected_payload_hash: str | None = None,
    consumer_override: str | None = None,
) -> dict[str, Any]:
    """Build complete adapter request from lifecycle commit inputs.

    This is the main entry point that constructs all adapter inputs.
    """
    issues: list[str] = []
    nested_contract = commit_contract_preview.get("commit_contract")
    contract = (
        nested_contract
        if (
            isinstance(nested_contract, dict)
            and (
                nested_contract.get("lifecycle_commit_request")
                or nested_contract.get("commit_id")
            )
        )
        else commit_contract_preview
    )

    if not storage_root or not isinstance(storage_root, str) or not storage_root.strip():
        issues.append("storage_root must be a non-empty string")

    lifecycle_request = contract.get("lifecycle_commit_request", {})
    if isinstance(lifecycle_request, dict):
        request, validation_issues = validate_lifecycle_request(lifecycle_request)
        if validation_issues:
            issues.extend(validation_issues)

    commit_id = _as_text(contract.get("commit_id"))
    if not commit_id:
        commit_id = _as_text(lifecycle_request.get("commit_id"))
    if not commit_id:
        issues.append("commit_id is required")

    transaction_id = _as_text(lifecycle_request.get("transaction_id"))
    if not transaction_id:
        issues.append("transaction_id is required")

    planned_targets = commit_plan.get("planned_targets") if isinstance(commit_plan, dict) else None
    planned_records = commit_plan.get("planned_records") if isinstance(commit_plan, dict) else None

    expected_targets = build_expected_targets(planned_targets=planned_targets)
    new_targets = build_new_targets(planned_records=planned_records, planned_targets=planned_targets)

    plan_hash = expected_payload_hash or sha256(str(expected_targets).encode("utf-8")).hexdigest()
    exec_hash = execution_plan_hash or sha256(str(new_targets).encode("utf-8")).hexdigest()
    token_id = token_id or commit_id

    result = {
        "lifecycle_commit_request": deepcopy(lifecycle_request),
        "gate_result": build_gate_result(commit_contract_preview=contract),
        "transaction_manifest": build_transaction_manifest(
            commit_id=commit_id,
            transaction_id=transaction_id,
            execution_plan_hash=exec_hash,
            approval_token_id=token_id,
            expected_payload_hash=plan_hash,
            target_paths=list(expected_targets.keys()) if expected_targets else None,
        ),
        "storage_plan": build_storage_plan(
            storage_root=storage_root,
            commit_id=commit_id,
            transaction_id=transaction_id,
        ),
        "guard_plan": build_guard_plan(
            storage_root=storage_root,
            commit_id=commit_id,
            transaction_id=transaction_id,
            target_set_hash=_hash_json({"target_paths": sorted(expected_targets.keys())}) if expected_targets else "",
            owner_id=owner_id,
        ),
        "token_storage_plan": build_token_storage_plan(
            storage_root=storage_root,
            token_id=token_id,
            commit_id=commit_id,
        ),
        "expected_targets": expected_targets,
        "new_targets": new_targets,
        "consumer_id": build_consumer_id(owner_id=owner_id, consumer_override=consumer_override),
    }

    if issues:
        result["build_issues"] = issues

    return result


def build_lifecycle_runtime_commit_adapter_request(
    *,
    commit_contract_preview: dict[str, Any],
    commit_plan: dict[str, Any],
    storage_root: str,
    owner_id: str,
    token_id: str | None = None,
    execution_plan_hash: str | None = None,
    expected_payload_hash: str | None = None,
    consumer_override: str | None = None,
) -> dict[str, Any]:
    """Main API: Build adapter request from lifecycle commit inputs.

    Args:
        commit_contract_preview: The commit contract preview dict
        commit_plan: The orchestrated commit plan with planned_targets/records
        storage_root: Caller-provided storage root path (tempfile in tests)
        owner_id: The commit owner/consumer ID
        token_id: Optional approval token ID (defaults to commit_id)
        execution_plan_hash: Optional execution plan hash
        expected_payload_hash: Optional expected payload hash
        consumer_override: Optional consumer ID override

    Returns:
        Dict with all adapter inputs ready for adapt_and_execute_lifecycle_runtime_commit
    """
    return build_adapter_request(
        commit_contract_preview=commit_contract_preview,
        commit_plan=commit_plan,
        storage_root=storage_root,
        owner_id=owner_id,
        token_id=token_id,
        execution_plan_hash=execution_plan_hash,
        expected_payload_hash=expected_payload_hash,
        consumer_override=consumer_override,
    )
