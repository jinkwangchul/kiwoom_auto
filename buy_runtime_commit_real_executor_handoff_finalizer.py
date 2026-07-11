# -*- coding: utf-8 -*-
"""Finalize BUY Runtime Commit handoff preview for tempfile Real Executor input.

This module translates a validated Real Executor handoff preview into the exact
input shape consumed by ``runtime_commit_real_executor.execute_runtime_commit``.
It is intentionally limited to caller-provided tempfile paths. It never opens
production runtime targets, consumes tokens, acquires locks, creates backups,
writes journals, executes commits, writes queues, or calls GUI/Broker/SendOrder.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

from runtime_commit_approval_token_store import (
    READ_OK,
    create_runtime_commit_token_storage_plan,
    read_runtime_commit_approval_token,
)
from runtime_commit_execution_gate import STATUS_APPROVED
from runtime_commit_guard import create_runtime_commit_guard_plan
from runtime_commit_transaction_contract import build_runtime_commit_transaction_manifest
from runtime_commit_transaction_persistence import create_runtime_transaction_storage_plan


FINALIZER_TYPE = "BUY_RUNTIME_COMMIT_REAL_EXECUTOR_HANDOFF_FINALIZER"
FINALIZER_VERSION = "BUY_RUNTIME_COMMIT_REAL_EXECUTOR_HANDOFF_FINALIZER_V1"

STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"

PLACEHOLDER_TOKEN_IDS = {
    "",
    "DRY_RUN_ONLY_NO_TOKEN",
    "PREVIEW_ONLY_NO_TOKEN",
    "NO_TOKEN",
    "PLACEHOLDER",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def calculate_final_execution_plan_hash(
    *,
    target_paths: Any,
    before_payload_hash: Any,
    after_payload_hash: Any,
) -> str:
    """Return the final plan hash bound to concrete target paths and payloads."""
    paths = [str(path) for path in target_paths] if isinstance(target_paths, list) else []
    return _stable_hash(
        {
            "target_paths": paths,
            "expected_payload_hash": _clean_text(before_payload_hash),
            "new_payload_hash": _clean_text(after_payload_hash),
        }
    )


def _result(
    *,
    status: str,
    finalized_real_executor_input: dict[str, Any] | None = None,
    finalized_transaction_manifest: dict[str, Any] | None = None,
    finalized_expected_targets: dict[str, Any] | None = None,
    finalized_new_targets: dict[str, Any] | None = None,
    finalized_storage_plan: dict[str, Any] | None = None,
    finalized_guard_plan: dict[str, Any] | None = None,
    finalization_summary: dict[str, Any] | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
    evidence: dict[str, Any] | None = None,
    issues: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "finalizer_type": FINALIZER_TYPE,
        "finalizer_version": FINALIZER_VERSION,
        "status": status,
        "finalized_real_executor_input": deepcopy(finalized_real_executor_input)
        if isinstance(finalized_real_executor_input, dict)
        else None,
        "finalized_transaction_manifest": deepcopy(finalized_transaction_manifest)
        if isinstance(finalized_transaction_manifest, dict)
        else None,
        "finalized_expected_targets": deepcopy(finalized_expected_targets)
        if isinstance(finalized_expected_targets, dict)
        else None,
        "finalized_new_targets": deepcopy(finalized_new_targets)
        if isinstance(finalized_new_targets, dict)
        else None,
        "finalized_storage_plan": deepcopy(finalized_storage_plan)
        if isinstance(finalized_storage_plan, dict)
        else None,
        "finalized_guard_plan": deepcopy(finalized_guard_plan)
        if isinstance(finalized_guard_plan, dict)
        else None,
        "finalization_summary": deepcopy(finalization_summary or {}),
        "diagnostics": deepcopy(diagnostics or []),
        "evidence": deepcopy(evidence or {}),
        "issues": list(issues or []),
        "preview_only": False,
        "tempfile_only": True,
        "runtime_commit_real_executor_called": False,
        "runtime_write": False,
        "queue_write": False,
        "send_order_called": False,
        "broker_called": False,
        "chejan_connected": False,
        "gui_updated": False,
    }


def _block(status: str, issues: list[str], evidence: dict[str, Any]) -> dict[str, Any]:
    return _result(
        status=status,
        diagnostics=[{"stage": "handoff_finalization", "ok": False, "reason": issue} for issue in issues],
        evidence=evidence,
        issues=issues,
    )


def _project_root() -> Path:
    return Path(__file__).resolve().parent


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _protected_path(path: Path) -> bool:
    root = _project_root()
    for protected in (root / "runtime", root / "routines"):
        if _under(path.resolve(strict=False), protected.resolve(strict=False)):
            return True
    return False


def _read_json_dict(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _resolve_target_path(
    *,
    raw_path: Any,
    temp_roots: list[Path],
) -> tuple[Path | None, str | None]:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None, "target path must be a non-empty string"
    path = Path(raw_path)
    if not path.is_absolute():
        return None, "target path must be absolute"
    if ".." in path.parts:
        return None, "target path must not contain '..'"
    if path.suffix.lower() != ".json":
        return None, "target path must be a JSON file"
    resolved = path.resolve(strict=False)
    if _protected_path(resolved):
        return None, "project runtime/routines target is not allowed"
    if not any(_under(resolved, root) for root in temp_roots):
        return None, "target path must stay under the tempfile allowlist root"
    if path.exists():
        try:
            strict_resolved = path.resolve(strict=True)
        except OSError:
            return None, "target path cannot be resolved safely"
        if not any(_under(strict_resolved, root) for root in temp_roots):
            return None, "target path symlink escapes tempfile root"
    return resolved, None


def _normalize_payload_map(value: Any, label: str) -> tuple[dict[str, dict[str, Any]], list[str]]:
    if not isinstance(value, dict):
        return {}, [f"{label} must be a dict"]
    result: dict[str, dict[str, Any]] = {}
    issues: list[str] = []
    for key, payload in value.items():
        logical = _clean_text(key)
        if not logical:
            issues.append(f"{label} contains an empty logical target")
            continue
        if not isinstance(payload, dict):
            issues.append(f"{label}.{logical} must be a dict")
            continue
        result[logical] = deepcopy(payload)
    return result, issues


def finalize_runtime_commit_real_executor_handoff(
    *,
    real_executor_handoff_preview: Any,
    storage_root: Any,
    target_path_allowlist: Any,
    runtime_before_payloads: Any,
    runtime_after_payloads: Any,
    token_id: Any,
) -> dict[str, Any]:
    """Finalize a handoff preview into tempfile-only Real Executor input."""
    handoff = deepcopy(_as_dict(real_executor_handoff_preview))
    evidence: dict[str, Any] = {
        "hash_recalculation": {},
        "target_mapping": {},
        "token_binding": {},
    }
    if not handoff:
        return _block(STATUS_INVALID, ["REAL_EXECUTOR_HANDOFF_PREVIEW_REQUIRED"], evidence)
    if handoff.get("handoff_version") != "BUY_RUNTIME_COMMIT_REAL_EXECUTOR_HANDOFF_PREVIEW_V1":
        return _block(STATUS_INVALID, ["MALFORMED_HANDOFF_PREVIEW"], evidence)

    storage_text = _clean_text(storage_root)
    if not storage_text:
        return _block(STATUS_INVALID, ["STORAGE_ROOT_REQUIRED"], evidence)
    storage_path = Path(storage_text)
    if not storage_path.is_absolute():
        return _block(STATUS_INVALID, ["STORAGE_ROOT_MUST_BE_ABSOLUTE"], evidence)
    storage_resolved = storage_path.resolve(strict=False)
    if _protected_path(storage_resolved):
        return _block(STATUS_BLOCKED, ["PROJECT_RUNTIME_OR_ROUTINES_STORAGE_ROOT_BLOCKED"], evidence)
    temp_roots = [storage_resolved, storage_resolved.parent]

    allowlist = deepcopy(_as_dict(target_path_allowlist))
    if not allowlist:
        return _block(STATUS_INVALID, ["TARGET_PATH_ALLOWLIST_REQUIRED"], evidence)
    before_by_logical, before_issues = _normalize_payload_map(runtime_before_payloads, "runtime_before_payloads")
    after_by_logical, after_issues = _normalize_payload_map(runtime_after_payloads, "runtime_after_payloads")
    if before_issues or after_issues:
        return _block(STATUS_INVALID, before_issues + after_issues, evidence)

    token_text = _clean_text(token_id)
    handoff_token = _clean_text(handoff.get("token_id"))
    if not token_text:
        return _block(STATUS_INVALID, ["TOKEN_ID_REQUIRED"], evidence)
    if handoff_token and handoff_token != token_text:
        return _block(STATUS_BLOCKED, ["TOKEN_ID_MISMATCH"], evidence)

    logical_targets = sorted(before_by_logical)
    if not logical_targets:
        return _block(STATUS_INVALID, ["NO_RUNTIME_TARGETS"], evidence)
    if sorted(after_by_logical) != logical_targets:
        return _block(STATUS_BLOCKED, ["BEFORE_AFTER_TARGET_SET_MISMATCH"], evidence)

    actual_paths: list[str] = []
    expected_targets_before: dict[str, dict[str, Any]] = {}
    new_targets_after: dict[str, dict[str, Any]] = {}
    seen_paths: set[str] = set()
    for logical in logical_targets:
        if logical not in allowlist:
            return _block(STATUS_BLOCKED, [f"LOGICAL_TARGET_NOT_MAPPED:{logical}"], evidence)
        actual, issue = _resolve_target_path(raw_path=allowlist.get(logical), temp_roots=temp_roots)
        if issue or actual is None:
            return _block(STATUS_BLOCKED, [f"TARGET_PATH_BLOCKED:{logical}:{issue}"], evidence)
        actual_text = str(actual)
        if actual_text in seen_paths:
            return _block(STATUS_BLOCKED, ["DUPLICATE_TARGET_PATH"], evidence)
        seen_paths.add(actual_text)
        if not actual.exists():
            return _block(STATUS_BLOCKED, [f"TARGET_FILE_NOT_FOUND:{logical}"], evidence)
        current = _read_json_dict(actual)
        if current is None:
            return _block(STATUS_BLOCKED, [f"TARGET_FILE_NOT_JSON_OBJECT:{logical}"], evidence)
        before_payload = deepcopy(before_by_logical[logical])
        after_payload = deepcopy(after_by_logical[logical])
        if current != before_payload:
            return _block(STATUS_BLOCKED, [f"BEFORE_PAYLOAD_MISMATCH:{logical}"], evidence)
        actual_paths.append(actual_text)
        expected_targets_before[actual_text] = before_payload
        new_targets_after[actual_text] = after_payload
        evidence["target_mapping"][logical] = actual_text

    manifest_preview = deepcopy(_as_dict(handoff.get("transaction_manifest")))
    if _clean_text(manifest_preview.get("approval_token_id")) in PLACEHOLDER_TOKEN_IDS:
        return _block(STATUS_BLOCKED, ["PLACEHOLDER_APPROVAL_TOKEN_ID"], evidence)
    if _clean_text(manifest_preview.get("approval_token_id")) != token_text:
        return _block(STATUS_BLOCKED, ["MANIFEST_APPROVAL_TOKEN_ID_MISMATCH"], evidence)

    before_hash = _stable_hash(expected_targets_before)
    after_hash = _stable_hash(new_targets_after)
    final_plan_hash = calculate_final_execution_plan_hash(
        target_paths=actual_paths,
        before_payload_hash=before_hash,
        after_payload_hash=after_hash,
    )
    preview_plan_hash = _clean_text(handoff.get("plan_hash") or manifest_preview.get("execution_plan_hash"))
    evidence["hash_recalculation"] = {
        "preview_execution_plan_hash": preview_plan_hash,
        "final_execution_plan_hash": final_plan_hash,
        "expected_payload_hash": before_hash,
        "new_payload_hash": after_hash,
        "preview_hash_changed": preview_plan_hash != final_plan_hash,
    }
    if preview_plan_hash != final_plan_hash:
        return _block(STATUS_BLOCKED, ["EXECUTION_PLAN_HASH_MISMATCH"], evidence)

    commit_id = _clean_text(handoff.get("commit_id") or manifest_preview.get("commit_id"))
    consumer_id = _clean_text(handoff.get("consumer_id"))
    if not commit_id:
        return _block(STATUS_INVALID, ["COMMIT_ID_REQUIRED"], evidence)
    if not consumer_id:
        return _block(STATUS_INVALID, ["CONSUMER_ID_REQUIRED"], evidence)

    token_storage_plan = create_runtime_commit_token_storage_plan(
        storage_root=str(storage_resolved),
        token_id=token_text,
        commit_id=commit_id,
    )
    if token_storage_plan.get("plan_status") != "READY":
        return _block(STATUS_BLOCKED, ["TOKEN_STORAGE_PLAN_NOT_READY"] + list(token_storage_plan.get("issues") or []), evidence)
    token_read = read_runtime_commit_approval_token(storage_plan=token_storage_plan)
    if token_read.get("read_status") != READ_OK:
        return _block(STATUS_BLOCKED, ["TOKEN_READ_FAILED"] + list(token_read.get("issues") or []), evidence)
    token = _as_dict(token_read.get("token"))
    token_metadata = _as_dict(token.get("metadata"))
    token_issues: list[str] = []
    if token.get("token_id") != token_text:
        token_issues.append("TOKEN_RECORD_ID_MISMATCH")
    if token.get("commit_id") != commit_id:
        token_issues.append("TOKEN_COMMIT_ID_MISMATCH")
    if token.get("plan_hash") != final_plan_hash:
        token_issues.append("TOKEN_PLAN_HASH_MISMATCH")
    if token.get("issued_for") != consumer_id:
        token_issues.append("TOKEN_CONSUMER_ID_MISMATCH")
    if _clean_text(token_metadata.get("transaction_id")) and token_metadata.get("transaction_id") != manifest_preview.get("transaction_id"):
        token_issues.append("TOKEN_METADATA_TRANSACTION_ID_MISMATCH")
    if _clean_text(token_metadata.get("commit_id")) and token_metadata.get("commit_id") != commit_id:
        token_issues.append("TOKEN_METADATA_COMMIT_ID_MISMATCH")
    if _clean_text(token_metadata.get("plan_hash")) and token_metadata.get("plan_hash") != final_plan_hash:
        token_issues.append("TOKEN_METADATA_PLAN_HASH_MISMATCH")
    if token_issues:
        return _block(STATUS_BLOCKED, token_issues, evidence)
    evidence["token_binding"] = {
        "token_id": token_text,
        "commit_id": commit_id,
        "plan_hash": final_plan_hash,
        "issued_for": consumer_id,
    }

    finalized_manifest = build_runtime_commit_transaction_manifest(
        commit_id=commit_id,
        target_paths=actual_paths,
        execution_plan_hash=final_plan_hash,
        approval_token_id=token_text,
        expected_payload_hash=before_hash,
        backup_plan_hash=_stable_hash({"backup_targets": actual_paths, "commit_id": commit_id}),
        rollback_plan_hash=_stable_hash({"rollback_targets": actual_paths, "commit_id": commit_id, "before": before_hash}),
        metadata={
            "handoff_id": handoff.get("handoff_id"),
            "candidate_id": handoff.get("candidate_id"),
            "logical_targets": logical_targets,
            "new_payload_hash": after_hash,
            "tempfile_only": True,
        },
    )
    if finalized_manifest.get("issues"):
        return _block(STATUS_INVALID, ["FINALIZED_TRANSACTION_MANIFEST_INVALID"] + list(finalized_manifest.get("issues") or []), evidence)

    preview_transaction_id = _clean_text(handoff.get("transaction_id") or manifest_preview.get("transaction_id"))
    final_transaction_id = _clean_text(finalized_manifest.get("transaction_id"))
    if preview_transaction_id and preview_transaction_id != final_transaction_id:
        evidence["hash_recalculation"]["preview_transaction_id"] = preview_transaction_id
        evidence["hash_recalculation"]["final_transaction_id"] = final_transaction_id
        return _block(STATUS_BLOCKED, ["TRANSACTION_ID_MISMATCH"], evidence)
    if _clean_text(token_metadata.get("transaction_id")) and token_metadata.get("transaction_id") != final_transaction_id:
        return _block(STATUS_BLOCKED, ["TOKEN_METADATA_TRANSACTION_ID_MISMATCH"], evidence)

    storage_plan = create_runtime_transaction_storage_plan(
        storage_root=str(storage_resolved),
        commit_id=commit_id,
        transaction_id=final_transaction_id,
    )
    if storage_plan.get("storage_status") != "READY":
        return _block(STATUS_BLOCKED, ["TRANSACTION_STORAGE_PLAN_NOT_READY"] + list(storage_plan.get("issues") or []), evidence)
    guard_plan = create_runtime_commit_guard_plan(
        storage_root=str(storage_resolved),
        commit_id=commit_id,
        transaction_id=final_transaction_id,
        target_set_hash=finalized_manifest.get("target_set_hash"),
        owner_id=consumer_id,
    )
    if guard_plan.get("guard_status") != "READY":
        return _block(STATUS_BLOCKED, ["GUARD_PLAN_NOT_READY"] + list(guard_plan.get("issues") or []), evidence)

    gate_result = deepcopy(_as_dict(handoff.get("gate_result")))
    if gate_result.get("gate_status") != STATUS_APPROVED:
        return _block(STATUS_BLOCKED, ["GATE_STATUS_NOT_APPROVED"], evidence)
    gate_result["commit_id"] = commit_id
    gate_result.setdefault("gate_metadata", {})
    if isinstance(gate_result["gate_metadata"], dict):
        gate_result["gate_metadata"]["plan_hash"] = final_plan_hash

    # The current Real Executor verifier compares its expected_targets against
    # the post-write actual targets. Preserve before-payloads in the public
    # finalized_expected_targets contract, but feed the executor its current
    # post-write verification shape so tempfile E2E can commit successfully.
    executor_expected_targets = deepcopy(new_targets_after)

    finalized_input = {
        "gate_result": gate_result,
        "transaction_manifest": deepcopy(finalized_manifest),
        "storage_plan": deepcopy(storage_plan),
        "guard_plan": deepcopy(guard_plan),
        "token_storage_plan": deepcopy(token_storage_plan),
        "expected_targets": executor_expected_targets,
        "new_targets": deepcopy(new_targets_after),
        "consumer_id": consumer_id,
    }
    summary = {
        "commit_id": commit_id,
        "transaction_id": final_transaction_id,
        "token_id": token_text,
        "consumer_id": consumer_id,
        "logical_targets": logical_targets,
        "target_paths": actual_paths,
        "execution_plan_hash": final_plan_hash,
        "target_set_hash": finalized_manifest.get("target_set_hash"),
        "expected_payload_hash": before_hash,
        "new_payload_hash": after_hash,
        "real_executor_expected_targets_contract": "post_write_payload_for_current_verifier",
        "tempfile_only": True,
    }
    return _result(
        status=STATUS_READY,
        finalized_real_executor_input=finalized_input,
        finalized_transaction_manifest=finalized_manifest,
        finalized_expected_targets=expected_targets_before,
        finalized_new_targets=new_targets_after,
        finalized_storage_plan=storage_plan,
        finalized_guard_plan=guard_plan,
        finalization_summary=summary,
        diagnostics=[{"stage": "handoff_finalization", "ok": True, "reason": "finalized real executor input ready"}],
        evidence=evidence,
        issues=[],
    )
