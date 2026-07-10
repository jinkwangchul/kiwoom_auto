# -*- coding: utf-8 -*-
"""Canonical Runtime Commit contract helpers (M6-8).

This module normalizes the scattered M6 Runtime Commit result dictionaries into
a single preview-safe contract shape. It does not call existing M6 APIs, does
not read or write runtime files, and does not connect to GUI, broker, SendOrder,
Chejan, SQLite, backup, rollback, or atomic writer components.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
import hashlib
import json
import math
from typing import Any


CONTRACT_VERSION = "M6_RUNTIME_COMMIT_V1"

COMPONENT_ATOMIC_WRITER = "ATOMIC_WRITER"
COMPONENT_BACKUP_PLAN = "BACKUP_PLAN"
COMPONENT_ROLLBACK_PLAN = "ROLLBACK_PLAN"
COMPONENT_COMMIT_VERIFIER_PLAN = "COMMIT_VERIFIER_PLAN"
COMPONENT_COMMIT_VERIFIER_RESULT = "COMMIT_VERIFIER_RESULT"
COMPONENT_AUDIT_MANIFEST_PREVIEW = "AUDIT_MANIFEST_PREVIEW"
COMPONENT_EXECUTION_PLAN_PREVIEW = "EXECUTION_PLAN_PREVIEW"
COMPONENT_EXECUTION_GATE_PREVIEW = "EXECUTION_GATE_PREVIEW"

VALID_COMPONENTS = {
    COMPONENT_ATOMIC_WRITER,
    COMPONENT_BACKUP_PLAN,
    COMPONENT_ROLLBACK_PLAN,
    COMPONENT_COMMIT_VERIFIER_PLAN,
    COMPONENT_COMMIT_VERIFIER_RESULT,
    COMPONENT_AUDIT_MANIFEST_PREVIEW,
    COMPONENT_EXECUTION_PLAN_PREVIEW,
    COMPONENT_EXECUTION_GATE_PREVIEW,
}

STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_FAILED = "FAILED"
VALID_STATUSES = {
    STATUS_READY,
    STATUS_BLOCKED,
    STATUS_INVALID,
    STATUS_SUCCEEDED,
    STATUS_FAILED,
}

STATUS_ALIAS_FIELDS = (
    "status",
    "backup_status",
    "rollback_status",
    "verify_status",
    "verification_status",
    "audit_status",
    "executor_status",
    "gate_status",
)

FORBIDDEN_SAFETY_FLAGS = (
    "runtime_write",
    "position_write",
    "balance_write",
    "rules_write",
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
    "actual_execution",
)

REQUIRED_SAFETY_FLAGS_BY_COMPONENT = {
    COMPONENT_ATOMIC_WRITER: (),
    COMPONENT_BACKUP_PLAN: ("runtime_write", "file_write_called", "backup_created"),
    COMPONENT_ROLLBACK_PLAN: ("runtime_write", "file_write_called", "rollback_executed"),
    COMPONENT_COMMIT_VERIFIER_PLAN: ("runtime_write", "file_write_called"),
    COMPONENT_COMMIT_VERIFIER_RESULT: ("runtime_write", "file_write_called"),
    COMPONENT_AUDIT_MANIFEST_PREVIEW: ("runtime_write", "file_write_called", "audit_written"),
    COMPONENT_EXECUTION_PLAN_PREVIEW: (
        "runtime_write",
        "file_write_called",
        "backup_created",
        "rollback_executed",
        "verification_executed",
        "audit_written",
    ),
    COMPONENT_EXECUTION_GATE_PREVIEW: FORBIDDEN_SAFETY_FLAGS,
}

ALLOWED_TRUE_ACTIVITY_FLAGS_BY_COMPONENT = {
    COMPONENT_COMMIT_VERIFIER_RESULT: {"verification_executed"},
}

SAFETY_FLAG_ALIASES = {
    "audit_written": ("audit_write", "audit_file_written"),
    "actual_execution": ("actual_execution_performed",),
}

PREVIEW_ONLY_DEFAULT_BY_COMPONENT = {
    COMPONENT_ATOMIC_WRITER: False,
    COMPONENT_BACKUP_PLAN: True,
    COMPONENT_ROLLBACK_PLAN: True,
    COMPONENT_COMMIT_VERIFIER_PLAN: True,
    COMPONENT_COMMIT_VERIFIER_RESULT: True,
    COMPONENT_AUDIT_MANIFEST_PREVIEW: True,
    COMPONENT_EXECUTION_PLAN_PREVIEW: True,
    COMPONENT_EXECUTION_GATE_PREVIEW: True,
}

HASH_FIELDS = (
    "contract_version",
    "component",
    "commit_id",
    "status",
    "preview_only",
    "payload",
    "safety_flags",
)


def normalize_runtime_commit_component_result(
    component: str,
    source: dict[str, Any],
    *,
    expected_commit_id: str | None = None,
    preview_only: bool | None = None,
) -> dict[str, Any]:
    """Normalize a current M6 component result dict into the canonical contract."""
    source_copy = deepcopy(source)
    issues: list[str] = []
    warnings: list[str] = []

    component_text = _normalize_component(component)
    if component_text not in VALID_COMPONENTS:
        return _invalid_contract(
            component=component_text,
            commit_id="",
            issues=[f"invalid component: {component}"],
            warnings=[],
        )

    if not isinstance(source, dict):
        return _invalid_contract(
            component=component_text,
            commit_id="",
            issues=["source must be a dict"],
            warnings=[],
        )

    expected = ""
    expected_issues: list[str] = []
    if expected_commit_id is not None:
        expected, expected_issues = _validate_commit_id(expected_commit_id)
        issues.extend([f"expected_commit_id: {item}" for item in expected_issues])

    source_has_commit_id = "commit_id" in source
    commit_id, commit_issues = _validate_commit_id(source.get("commit_id")) if source_has_commit_id else ("", [])
    if source_has_commit_id:
        issues.extend(commit_issues)
    if not source_has_commit_id and expected and not expected_issues:
        commit_id = expected
    if source_has_commit_id and not commit_issues and expected and not expected_issues and commit_id != expected:
        issues.append("commit_id mismatch with expected_commit_id")
    if not commit_id:
        issues.append("commit_id is missing or empty")

    raw_status, status_issues = _extract_normalized_status(component_text, source)
    issues.extend(status_issues)
    status = raw_status if raw_status in VALID_STATUSES else STATUS_INVALID

    expected_preview = PREVIEW_ONLY_DEFAULT_BY_COMPONENT.get(component_text, True) if preview_only is None else preview_only
    source_preview_only = source.get("preview_only")
    if "preview_only" in source and source_preview_only is not expected_preview:
        issues.append("preview_only mismatch with expected preview_only")
    canonical_preview_only = expected_preview if "preview_only" not in source else source_preview_only

    source_issues = _normalize_messages(source.get("issues"))
    source_warnings = _normalize_messages(source.get("warnings"))
    issues.extend(source_issues)
    warnings.extend(source_warnings)

    safety_flags, safety_issues = _normalize_safety_flags(
        source.get("safety_flags"),
        component=component_text,
    )
    issues.extend(safety_issues)

    protected_issues = _find_protected_target_issues(source_copy)
    issues.extend(protected_issues)

    payload = _extract_payload(component_text, source_copy)
    payload_json_issue = _find_json_contract_issue(payload, "payload")
    if payload_json_issue:
        issues.append(payload_json_issue)

    if issues:
        status = STATUS_INVALID if any(_is_invalid_issue(item) for item in issues) else STATUS_BLOCKED

    return build_runtime_commit_contract(
        component=component_text,
        commit_id=commit_id,
        status=status,
        preview_only=canonical_preview_only,
        payload=payload,
        issues=issues,
        warnings=warnings,
        safety_flags=safety_flags,
        metadata={
            "source_component": component_text,
            "source_status": _extract_original_status(source),
            "source_preview_only": source_preview_only,
        },
    )


def validate_runtime_commit_contract(
    contract: dict[str, Any],
    *,
    expected_component: str | None = None,
    expected_commit_id: str | None = None,
) -> dict[str, Any]:
    """Validate a normalized Runtime Commit contract without mutating it."""
    snapshot = deepcopy(contract)
    issues: list[str] = []
    warnings: list[str] = []

    if not isinstance(contract, dict):
        return _validation_result(False, issues=["contract must be a dict"], warnings=[])

    required = (
        "contract_version",
        "component",
        "commit_id",
        "status",
        "preview_only",
        "issues",
        "warnings",
        "safety_flags",
    )
    for field in required:
        if field not in contract:
            issues.append(f"required field missing: {field}")

    if issues:
        return _validation_result(False, issues=issues, warnings=warnings)

    if contract.get("contract_version") != CONTRACT_VERSION:
        issues.append("contract_version is invalid")

    component = _normalize_component(contract.get("component"))
    if component not in VALID_COMPONENTS:
        issues.append("component is invalid")
    if expected_component is not None and component != _normalize_component(expected_component):
        issues.append("component mismatch with expected_component")

    commit_id, commit_issues = _validate_commit_id(contract.get("commit_id"))
    issues.extend(commit_issues)
    if expected_commit_id is not None:
        expected, expected_issues = _validate_commit_id(expected_commit_id)
        issues.extend([f"expected_commit_id: {item}" for item in expected_issues])
        if not expected_issues and commit_id and commit_id != expected:
            issues.append("commit_id mismatch with expected_commit_id")

    if contract.get("status") not in VALID_STATUSES:
        issues.append("status is invalid")

    expected_preview = PREVIEW_ONLY_DEFAULT_BY_COMPONENT.get(component, True)
    if contract.get("preview_only") is not expected_preview:
        issues.append("preview_only mismatch with component default")

    if not isinstance(contract.get("issues"), list) or any(
        not isinstance(item, str) for item in contract.get("issues", [])
    ):
        issues.append("issues must be a list of strings")
    if not isinstance(contract.get("warnings"), list) or any(
        not isinstance(item, str) for item in contract.get("warnings", [])
    ):
        issues.append("warnings must be a list of strings")

    safety_flags, safety_issues = _normalize_safety_flags(contract.get("safety_flags"), component=component)
    issues.extend(safety_issues)
    if isinstance(contract.get("safety_flags"), dict) and safety_flags != contract.get("safety_flags"):
        issues.append("safety_flags must contain only canonical false bool values")

    protected_issues = _find_protected_target_issues(contract)
    issues.extend(protected_issues)

    json_issue = _find_json_contract_issue(_hash_payload(contract), "contract hash payload")
    if json_issue:
        issues.append(json_issue)

    if snapshot != contract:
        issues.append("contract was mutated during validation")

    return _validation_result(not issues, issues=issues, warnings=warnings)


def build_runtime_commit_contract_hash(contract: dict[str, Any]) -> str:
    """Build a deterministic SHA-256 hash for the stable contract fields."""
    validation = validate_runtime_commit_contract(contract)
    if not validation["valid"]:
        raise ValueError("; ".join(validation["issues"]))
    payload = _hash_payload(contract)
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_runtime_commit_contract(
    *,
    component: str,
    commit_id: Any,
    status: str,
    preview_only: bool,
    payload: dict[str, Any] | None = None,
    issues: list[Any] | None = None,
    warnings: list[Any] | None = None,
    safety_flags: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a canonical Runtime Commit contract."""
    component_text = _normalize_component(component)
    commit_text, commit_issues = _validate_commit_id(commit_id)
    status_text = str(status).strip().upper() if isinstance(status, str) else ""
    normalized_safety = _default_safety_flags()
    if safety_flags is not None and isinstance(safety_flags, dict):
        for key, value in safety_flags.items():
            normalized_safety[str(key)] = value

    merged_issues = list(commit_issues)
    merged_issues.extend(_normalize_messages(issues))
    normalized_warnings = _normalize_messages(warnings)

    if component_text not in VALID_COMPONENTS:
        merged_issues.append(f"invalid component: {component}")
    if status_text not in VALID_STATUSES:
        merged_issues.append(f"invalid status: {status}")
        status_text = STATUS_INVALID
    expected_preview = PREVIEW_ONLY_DEFAULT_BY_COMPONENT.get(component_text, True)
    if preview_only is not expected_preview:
        merged_issues.append("preview_only mismatch with component default")

    return {
        "contract_version": CONTRACT_VERSION,
        "component": component_text,
        "commit_id": commit_text,
        "status": status_text,
        "preview_only": bool(preview_only),
        "payload": deepcopy(payload) if isinstance(payload, dict) else {},
        "issues": _dedupe(merged_issues),
        "warnings": _dedupe(normalized_warnings),
        "safety_flags": deepcopy(normalized_safety),
        "metadata": deepcopy(metadata) if isinstance(metadata, dict) else {},
    }


def _invalid_contract(*, component: str, commit_id: str, issues: list[str], warnings: list[str]) -> dict[str, Any]:
    return build_runtime_commit_contract(
        component=component if component in VALID_COMPONENTS else COMPONENT_EXECUTION_PLAN_PREVIEW,
        commit_id=commit_id or "INVALID_COMMIT_ID",
        status=STATUS_INVALID,
        preview_only=True,
        payload={},
        issues=issues,
        warnings=warnings,
        safety_flags=_default_safety_flags(),
        metadata={},
    )


def _validation_result(valid: bool, *, issues: list[str], warnings: list[str]) -> dict[str, Any]:
    return {
        "valid": valid,
        "status": STATUS_READY if valid else STATUS_INVALID,
        "issues": _dedupe(issues),
        "warnings": _dedupe(warnings),
        "preview_only": True,
    }


def _normalize_component(component: Any) -> str:
    if not isinstance(component, str):
        return ""
    return component.strip().upper()


def _validate_commit_id(value: Any) -> tuple[str, list[str]]:
    if not isinstance(value, str):
        return "", ["commit_id must be a string"]
    if value != value.strip():
        return "", ["commit_id must not contain leading or trailing whitespace"]
    if not value:
        return "", ["commit_id is missing or empty"]
    return value, []


def _extract_original_status(source: dict[str, Any]) -> str:
    for field in STATUS_ALIAS_FIELDS:
        if field in source:
            return str(source.get(field))
    return ""


def _extract_normalized_status(component: str, source: dict[str, Any]) -> tuple[str, list[str]]:
    found: dict[str, str] = {}
    for field in STATUS_ALIAS_FIELDS:
        if field in source:
            value = source.get(field)
            if isinstance(value, str) and value.strip():
                found[field] = _map_status(component, value)
            else:
                found[field] = STATUS_INVALID
    if not found:
        return STATUS_INVALID, ["status alias is missing"]
    unique = set(found.values())
    if len(unique) > 1:
        return STATUS_INVALID, [f"status alias conflict: {found}"]
    return next(iter(unique)), []


def _map_status(component: str, value: Any) -> str:
    status = str(value).strip().upper()
    if component == COMPONENT_ATOMIC_WRITER:
        if status == "OK":
            return STATUS_SUCCEEDED
        if status == "ERROR":
            return STATUS_FAILED
    if component == COMPONENT_COMMIT_VERIFIER_RESULT:
        if status == STATUS_READY:
            return STATUS_SUCCEEDED
        if status == STATUS_BLOCKED:
            return STATUS_FAILED
    if component == COMPONENT_EXECUTION_GATE_PREVIEW and status == "APPROVED":
        return STATUS_READY
    if status in VALID_STATUSES:
        return status
    if status in {"OK", "SUCCESS", "SUCCESSFUL"}:
        return STATUS_SUCCEEDED
    if status in {"ERROR", "FAIL", "FAILED"}:
        return STATUS_FAILED
    return STATUS_INVALID


def _default_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in FORBIDDEN_SAFETY_FLAGS}


def _normalize_safety_flags(value: Any, *, component: str) -> tuple[dict[str, bool], list[str]]:
    issues: list[str] = []
    flags = _default_safety_flags()
    required = set(REQUIRED_SAFETY_FLAGS_BY_COMPONENT.get(component, FORBIDDEN_SAFETY_FLAGS))
    allowed_true = ALLOWED_TRUE_ACTIVITY_FLAGS_BY_COMPONENT.get(component, set())
    if value is None:
        if not required:
            return flags, issues
        return flags, ["safety_flags is missing"]
    if not isinstance(value, dict):
        return flags, ["safety_flags must be a dict"]

    for flag in required:
        if _get_source_flag(value, flag)[0] is False:
            issues.append(f"required safety flag missing: {flag}")

    for flag in FORBIDDEN_SAFETY_FLAGS:
        exists, flag_value = _get_source_flag(value, flag)
        if not exists:
            continue
        if flag_value is True and flag in allowed_true:
            flags[flag] = True
            continue
        if flag_value is not False:
            issues.append(f"safety flag {flag} must be bool False")
        flags[flag] = flag_value

    for flag, flag_value in value.items():
        if flag not in FORBIDDEN_SAFETY_FLAGS and not _is_known_safety_alias(flag) and flag_value is True:
            issues.append(f"unknown safety flag {flag} must not be True")

    return flags, issues


def _get_source_flag(value: dict[str, Any], flag: str) -> tuple[bool, Any]:
    if flag in value:
        return True, value.get(flag)
    for alias in SAFETY_FLAG_ALIASES.get(flag, ()):
        if alias in value:
            return True, value.get(alias)
    return False, None


def _is_known_safety_alias(flag: str) -> bool:
    return any(flag in aliases for aliases in SAFETY_FLAG_ALIASES.values())


def _normalize_messages(value: Any) -> list[str]:
    if value is None:
        return []
    raw = value if isinstance(value, list) else [value]
    return _dedupe([item for item in raw if isinstance(item, str) and item])


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _extract_payload(component: str, source: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"original_status": _extract_original_status(source)}
    if component == COMPONENT_ATOMIC_WRITER:
        for field in ("writer_type", "target_path", "written", "bytes_written", "error"):
            if field in source:
                payload[field] = deepcopy(source[field])
    elif component == COMPONENT_BACKUP_PLAN:
        for field in ("plan_type", "backup_root_preview", "backup_targets", "backup_metadata"):
            if field in source:
                payload[field] = deepcopy(source[field])
    elif component == COMPONENT_ROLLBACK_PLAN:
        for field in ("rollback_targets", "rollback_metadata", "rollback_strategy"):
            if field in source:
                payload[field] = deepcopy(source[field])
    elif component == COMPONENT_COMMIT_VERIFIER_PLAN:
        for field in ("verify_metadata", "verify_strategy"):
            if field in source:
                payload[field] = deepcopy(source[field])
    elif component == COMPONENT_COMMIT_VERIFIER_RESULT:
        for field in (
            "target_verification_results",
            "matched_targets",
            "mismatched_targets",
            "missing_targets",
            "unexpected_targets",
            "rollback_required",
            "verification_metadata",
        ):
            if field in source:
                payload[field] = deepcopy(source[field])
    elif component == COMPONENT_AUDIT_MANIFEST_PREVIEW:
        for field in (
            "record_type",
            "audit_phase",
            "persisted",
            "post_commit_record",
            "audit_written",
            "actual_execution",
            "audit_records_preview",
            "audit_metadata",
            "audit_summary",
            "source_statuses",
        ):
            if field in source:
                payload[field] = deepcopy(source[field])
    elif component == COMPONENT_EXECUTION_PLAN_PREVIEW:
        for field in (
            "plan_type",
            "execution_phase",
            "execution_performed",
            "executable_without_real_gate",
            "actual_execution",
            "execution_plan",
            "execution_steps",
            "state_machine",
            "source_statuses",
            "rollback_required",
        ):
            if field in source:
                payload[field] = deepcopy(source[field])
    elif component == COMPONENT_EXECUTION_GATE_PREVIEW:
        for field in (
            "gate_type",
            "gate_phase",
            "approval_validation_only",
            "real_gate_active",
            "gate_status",
            "execution_allowed",
            "actual_execution",
            "token_consumed",
            "token_persisted",
            "commit_lock_acquired",
            "replay_protection_active",
            "ready_for_real_executor",
            "approval_summary",
            "execution_plan_summary",
            "validation_results",
            "gate_metadata",
        ):
            if field in source:
                payload[field] = deepcopy(source[field])
    return payload


def _find_protected_target_issues(value: Any) -> list[str]:
    issues: list[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                if isinstance(key, str):
                    check_path(key)
                visit(child)
        elif isinstance(node, (list, tuple)):
            for child in node:
                visit(child)
        elif isinstance(node, str):
            check_path(node)

    def check_path(text: str) -> None:
        normalized = text.replace("\\", "/").lower()
        parts = [part for part in normalized.split("/") if part]
        if ".." in parts:
            issues.append(f"protected target path traversal: {text}")
        if len(parts) >= 3 and "routines" in parts and parts[-1] == "rules.json":
            issues.append(f"protected routines rules.json target: {text}")

    visit(value)
    return _dedupe(issues)


def _find_json_contract_issue(value: Any, label: str) -> str | None:
    try:
        _assert_json_value(value)
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        return f"{label} is not canonical JSON serializable: {exc}"
    return None


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


def _hash_payload(contract: dict[str, Any]) -> dict[str, Any]:
    return {field: deepcopy(contract.get(field)) for field in HASH_FIELDS}


def _is_invalid_issue(issue: str) -> bool:
    markers = (
        "invalid",
        "missing",
        "mismatch",
        "conflict",
        "must be",
        "must not",
        "protected",
        "not canonical",
        "path traversal",
    )
    text = issue.lower()
    return any(marker in text for marker in markers)
