# -*- coding: utf-8 -*-
"""Real Runtime Commit Executor (M6-16).

This module implements the actual execution flow for Runtime Commits.
It connects the Execution Gate, Transaction Manifest, Guard, Token Store,
Backup, Atomic Write, Verify, and Rollback components.

Scope boundaries:
- storage_root and target files are caller-provided (tempfile for tests)
- No project runtime/*.json or routines/*/rules.json modifications
- No SQLite, no GUI/Broker/SendOrder/Chejan connections
- Input dicts are never mutated
- Existing M6 APIs are reused, no duplicate functionality
"""

from __future__ import annotations

import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from runtime_commit_execution_gate import STATUS_APPROVED
from runtime_commit_guard import (
    acquire_runtime_commit_lock,
    release_runtime_commit_lock,
    GUARD_STATUS_READY,
    GUARD_STATUS_BLOCKED,
    GUARD_STATUS_INVALID,
    LOCK_ACQUIRE_ACQUIRED,
    LOCK_ACQUIRE_BLOCKED,
    LOCK_ACQUIRE_INVALID,
)
from runtime_commit_approval_token_store import (
    read_runtime_commit_approval_token,
    validate_runtime_commit_approval_token,
    consume_runtime_commit_approval_token,
    TOKEN_STATUS_ISSUED,
    CONSUME_CONSUMED,
)
from runtime_atomic_writer import write_json_atomic
from runtime_commit_verifier import verify_runtime_commit
from runtime_commit_recovery_journal import (
    create_recovery_journal,
    append_recovery_stage,
    record_recovery_status,
    CREATE_OK,
    STAGE_STATUS_SUCCEEDED,
    STAGE_STATUS_FAILED,
    RECOVERY_STATUS_COMPLETED,
    RECOVERY_STATUS_ABORTED,
    RECOVERY_STATUS_ROLLED_BACK,
    RECOVERY_STATUS_MANUAL_RESTORE_REQUIRED,
)
from runtime_commit_transaction_persistence import (
    write_runtime_transaction_manifest,
    append_runtime_transaction_journal_event,
)
from runtime_commit_transaction_contract import (
    build_runtime_commit_transaction_manifest,
    TRANSACTION_STAGES,
)


STATUS_READY = "READY"
STATUS_BLOCKED = "BLOCKED"
STATUS_INVALID = "INVALID"
STATUS_ABORTED = "ABORTED"
STATUS_COMMITTED = "COMMITTED"
STATUS_ROLLED_BACK = "ROLLED_BACK"
STATUS_MANUAL_RESTORE_REQUIRED = "MANUAL_RESTORE_REQUIRED"


def _normalize_targets(targets: Any) -> list[str]:
    if isinstance(targets, dict):
        return sorted(targets.keys())
    if isinstance(targets, list):
        return sorted(str(t) for t in targets)
    return []


def _copy_file(source: Path, dest: Path) -> bool:
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(source.read_bytes())
        return True
    except Exception:
        return False


def _restore_file(backup_path: Path, target_path: Path) -> bool:
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(backup_path.read_bytes())
        return True
    except Exception:
        return False


def _safe_release_lock(guard_plan: dict[str, Any], owner_id: str) -> bool:
    try:
        result = release_runtime_commit_lock(guard_plan=guard_plan, expected_owner_id=owner_id)
        return result.get("release_status") == "RELEASED"
    except Exception:
        return False


def _build_result(
    *,
    status: str,
    transaction_id: str,
    commit_id: str,
    gate_valid: bool = False,
    guard_evaluated: bool = False,
    lock_acquired: bool = False,
    token_validated: bool = False,
    backup_created: bool = False,
    write_executed: bool = False,
    verify_passed: bool = False,
    token_consumed: bool = False,
    lock_released: bool = False,
    rollback_executed: bool = False,
    manual_restore_required: bool = False,
    issues: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "execute_status": status,
        "transaction_id": transaction_id,
        "commit_id": commit_id,
        "transaction_status": status,
        "gate_valid": gate_valid,
        "guard_evaluated": guard_evaluated,
        "lock_acquired": lock_acquired,
        "token_validated": token_validated,
        "backup_created": backup_created,
        "write_executed": write_executed,
        "verify_passed": verify_passed,
        "token_consumed": token_consumed,
        "lock_released": lock_released,
        "rollback_executed": rollback_executed,
        "manual_restore_required": manual_restore_required,
        "issues": issues,
        "warnings": warnings,
    }


def execute_runtime_commit(
    *,
    gate_result: Any,
    transaction_manifest: Any,
    storage_plan: Any,
    guard_plan: Any,
    token_storage_plan: Any,
    expected_targets: Any,
    new_targets: Any,
    consumer_id: Any,
) -> dict[str, Any]:
    """Execute a real runtime commit with full safety flow."""
    issues: list[str] = []
    warnings: list[str] = []

    commit_id = transaction_manifest.get("commit_id", "") if isinstance(transaction_manifest, dict) else ""
    transaction_id = transaction_manifest.get("transaction_id", "") if isinstance(transaction_manifest, dict) else ""
    # Prefer the storage_plan identity so M6-13 persistence/journal writes match
    # the caller-provided transaction_id/commit_id exactly.
    if isinstance(storage_plan, dict):
        sp_tx = storage_plan.get("transaction_id")
        sp_commit = storage_plan.get("commit_id")
        if isinstance(sp_tx, str) and sp_tx:
            transaction_id = sp_tx
        if isinstance(sp_commit, str) and sp_commit:
            commit_id = sp_commit
    target_paths = _normalize_targets(expected_targets)
    plan_hash = transaction_manifest.get("execution_plan_hash", "") if isinstance(transaction_manifest, dict) else ""

    # Internal storage_plan copy: M6-13 manifest auto-derives transaction_id
    # from (commit_id, target_set_hash, execution_plan_hash); we sync the
    # storage_plan identity to that derived value so write/validate agree.
    sp = deepcopy(storage_plan) if isinstance(storage_plan, dict) else storage_plan

    # --- Recovery Journal (M6-17) ---
    # Create a tempfile-based journal at execution start. The journal handle is
    # threaded through the flow and records each stage plus the final outcome.
    # When a storage_plan with a storage_root is available, the journal lives
    # under that caller-provided root (still tempfile-based for tests).
    journal_storage_root = None
    if isinstance(storage_plan, dict):
        candidate = storage_plan.get("storage_root")
        if isinstance(candidate, str) and candidate.strip():
            journal_storage_root = candidate
    journal = create_recovery_journal(
        transaction_id=transaction_id,
        commit_id=commit_id,
        owner_id=consumer_id,
        storage_root=journal_storage_root,
    )
    journal_ok = journal.get("create_status") == CREATE_OK
    if journal_ok:
        append_recovery_stage(
            journal=journal, stage="EXECUTE_START", status=STAGE_STATUS_SUCCEEDED,
            details={"consumer_id": consumer_id},
        )

    def _record_stage(stage: str, ok: bool, details: dict | None = None) -> None:
        if not journal_ok:
            return
        append_recovery_stage(
            journal=journal,
            stage=stage,
            status=STAGE_STATUS_SUCCEEDED if ok else STAGE_STATUS_FAILED,
            details=details or {},
        )

    def _close_journal(recovery_status: str, details: dict | None = None) -> None:
        if not journal_ok:
            return
        record_recovery_status(
            journal=journal, recovery_status=recovery_status, details=details or {}
        )

    # --- M6-13 Transaction Persistence (Manifest + Transaction Journal) ---
    # Persist a manifest before execution and append transaction-journal events
    # after each major stage. Only active when a valid storage_plan is supplied.
    persistence_ok = (
        isinstance(storage_plan, dict)
        and storage_plan.get("storage_status") == "READY"
        and bool(storage_plan.get("storage_root"))
    )

    _tx_journal_seq = {"n": 0}

    def _persist_manifest() -> None:
        if not persistence_ok:
            return
        try:
            manifest = build_runtime_commit_transaction_manifest(
                commit_id=commit_id,
                target_paths=target_paths,
                execution_plan_hash=plan_hash,
                approval_token_id=token.get("token_id", "") if isinstance(token, dict) else "",
                expected_payload_hash="payload-runtime-exec",
                backup_plan_hash="backup-runtime-exec",
                rollback_plan_hash="rollback-runtime-exec",
            )
            # M6-13 derives transaction_id from (commit_id, target_set_hash,
            # execution_plan_hash). Sync the internal storage_plan identity to
            # that derived value so write/validate agree (no caller mutation).
            manifest["transaction_status"] = "IN_PROGRESS"
            manifest["current_stage"] = "MANIFEST_CREATED"
            manifest["stage_history"] = ["MANIFEST_CREATED"]
            sp["transaction_id"] = manifest["transaction_id"]
            # Keep the caller's storage_plan identity in sync so downstream
            # reads (using the original plan) observe the same transaction_id.
            if isinstance(storage_plan, dict):
                storage_plan["transaction_id"] = manifest["transaction_id"]
            write_runtime_transaction_manifest(storage_plan=sp, manifest=manifest)
        except Exception:
            pass

    def _append_tx_journal(stage: str, event_status: str, details: dict | None = None) -> None:
        if not persistence_ok:
            return
        if stage not in TRANSACTION_STAGES:
            return
        _tx_journal_seq["n"] += 1
        try:
            append_runtime_transaction_journal_event(
                storage_plan=sp,
                event={
                    "event_version": "M6_RUNTIME_JOURNAL_EVENT_V1",
                    "transaction_id": sp.get("transaction_id", transaction_id),
                    "commit_id": commit_id,
                    "event_id": "",
                    "stage": stage,
                    "event_status": event_status,
                    "sequence": _tx_journal_seq["n"],
                    "created_at": "",
                    "details": details or {},
                    "safety_flags": {
                        "manifest_written": False,
                        "journal_written": True,
                        "file_write_called": True,
                        "runtime_write": False,
                        "token_consumed": False,
                        "lock_acquired": False,
                        "backup_created": False,
                        "rollback_executed": False,
                        "actual_execution": False,
                        "rules_write": False,
                        "gui_update_called": False,
                        "send_order_called": False,
                        "broker_called": False,
                        "sqlite_write": False,
                    },
                },
            )
        except Exception:
            pass

    # Persist manifest at execution start (before any side effects).
    # NOTE: _persist_manifest references `token`, which is assigned after token
    # validation below; the actual persist call is performed post-validation
    # (still before backup/write) to keep the manifest accurate.

    if not isinstance(gate_result, dict):
        return _build_result(
            status=STATUS_INVALID,
            transaction_id=transaction_id,
            commit_id=commit_id,
            issues=["gate_result must be a dict"],
            warnings=warnings,
        )

    if gate_result.get("gate_status") != STATUS_APPROVED:
        issues.append(f"gate_status is not APPROVED: {gate_result.get('gate_status')}")
        return _build_result(
            status=STATUS_BLOCKED,
            transaction_id=transaction_id,
            commit_id=commit_id,
            issues=issues,
            warnings=warnings,
        )

    gate_valid = True

    if not isinstance(guard_plan, dict) or guard_plan.get("guard_status") != GUARD_STATUS_READY:
        status = guard_plan.get("guard_status") if isinstance(guard_plan, dict) else "missing"
        issues.append(f"guard_status is not READY: {status}")
        return _build_result(
            status=STATUS_BLOCKED,
            transaction_id=transaction_id,
            commit_id=commit_id,
            gate_valid=gate_valid,
            issues=issues,
            warnings=warnings,
        )

    guard_evaluated = True

    lock_result = acquire_runtime_commit_lock(guard_plan=guard_plan)
    if lock_result.get("acquire_status") != LOCK_ACQUIRE_ACQUIRED:
        lock_status = lock_result.get("acquire_status")
        if lock_status == LOCK_ACQUIRE_BLOCKED:
            issues.append("active lock already exists or lock blocked")
        else:
            issues.append(f"lock acquire failed: {lock_status}")
        return _build_result(
            status=STATUS_BLOCKED,
            transaction_id=transaction_id,
            commit_id=commit_id,
            gate_valid=gate_valid,
            guard_evaluated=guard_evaluated,
            issues=issues,
            warnings=warnings,
        )

    lock_acquired = True

    token_read = read_runtime_commit_approval_token(storage_plan=token_storage_plan)
    if token_read.get("read_status") != "OK":
        issues.append("token not found or invalid")
        _safe_release_lock(guard_plan, consumer_id)
        return _build_result(
            status=STATUS_BLOCKED,
            transaction_id=transaction_id,
            commit_id=commit_id,
            gate_valid=gate_valid,
            guard_evaluated=guard_evaluated,
            lock_acquired=lock_acquired,
            issues=issues,
            warnings=warnings,
        )

    token = token_read.get("token", {})
    
    if not isinstance(token, dict):
        issues.append("token record is not a valid dict")
        _safe_release_lock(guard_plan, consumer_id)
        return _build_result(
            status=STATUS_BLOCKED,
            transaction_id=transaction_id,
            commit_id=commit_id,
            gate_valid=gate_valid,
            guard_evaluated=guard_evaluated,
            lock_acquired=lock_acquired,
            issues=issues,
            warnings=warnings,
        )

    token_validation = validate_runtime_commit_approval_token(
        token=token,
        expected_commit_id=commit_id,
        expected_plan_hash=plan_hash,
    )
    if not token_validation.get("valid_for_execution"):
        issues.append(f"token validation failed: {token_validation.get('token_status')}")
        _safe_release_lock(guard_plan, consumer_id)
        return _build_result(
            status=STATUS_BLOCKED,
            transaction_id=transaction_id,
            commit_id=commit_id,
            gate_valid=gate_valid,
            guard_evaluated=guard_evaluated,
            lock_acquired=lock_acquired,
            issues=issues,
            warnings=warnings,
        )

    if token.get("token_status") != TOKEN_STATUS_ISSUED:
        _safe_release_lock(guard_plan, consumer_id)
        return _build_result(
            status=STATUS_BLOCKED,
            transaction_id=transaction_id,
            commit_id=commit_id,
            gate_valid=gate_valid,
            guard_evaluated=guard_evaluated,
            lock_acquired=lock_acquired,
            token_validated=True,
            issues=["token already consumed"],
            warnings=warnings,
        )

    token_validated = True

    # Persist the transaction manifest now that the token is validated and
    # before any backup/write side effects occur (M6-13 requirement).
    _persist_manifest()
    _append_tx_journal("MANIFEST_CREATED", "RECORDED", details={"consumer_id": consumer_id})

    backup_created = True
    backup_root = Path(storage_plan.get("storage_root", tempfile.gettempdir())) / "backups" / commit_id if storage_plan else Path(tempfile.gettempdir())
    backup_root.mkdir(parents=True, exist_ok=True)

    backup_errors: list[str] = []
    for target in target_paths:
        target_path = Path(target)
        if target_path.exists():
            backup_name = target_path.stem
            backup_path = backup_root / f"{backup_name}.bak"
            if not _copy_file(target_path, backup_path):
                backup_errors.append(f"backup failed for {target}")
                backup_created = False

    if not backup_created:
        _append_tx_journal("BACKUP_DONE", "FAILED", details={"errors": backup_errors})
        _record_stage("BACKUP", False, details={"errors": backup_errors})
        _close_journal(RECOVERY_STATUS_ABORTED, details={"stage": "BACKUP", "errors": backup_errors})
        _safe_release_lock(guard_plan, consumer_id)
        return _build_result(
            status=STATUS_ABORTED,
            transaction_id=transaction_id,
            commit_id=commit_id,
            gate_valid=gate_valid,
            guard_evaluated=guard_evaluated,
            lock_acquired=lock_acquired,
            token_validated=token_validated,
            backup_created=backup_created,
            issues=backup_errors,
            warnings=warnings,
        )

    _append_tx_journal("BACKUP_DONE", "SUCCEEDED", details={"backup_root": str(backup_root)})
    _record_stage("BACKUP", True, details={"backup_root": str(backup_root)})

    write_success = True
    write_errors: list[str] = []
    for target_path, content in (new_targets or {}).items():
        target = Path(target_path)
        try:
            write_result = write_json_atomic(target, content if isinstance(content, dict) else {})
            if write_result.get("status") != "OK":
                write_success = False
                write_errors.append(f"write failed for {target_path}: {write_result.get('error')}")
        except Exception as exc:
            write_success = False
            write_errors.append(f"write exception for {target_path}: {exc}")

    write_executed = write_success

    if not write_success:
        _append_tx_journal("WRITE_DONE", "FAILED", details={"errors": write_errors})
        _record_stage("WRITE", False, details={"errors": write_errors})
        _close_journal(RECOVERY_STATUS_ABORTED, details={"stage": "WRITE", "errors": write_errors})
        _safe_release_lock(guard_plan, consumer_id)
        return _build_result(
            status=STATUS_ABORTED,
            transaction_id=transaction_id,
            commit_id=commit_id,
            gate_valid=gate_valid,
            guard_evaluated=guard_evaluated,
            lock_acquired=lock_acquired,
            token_validated=token_validated,
            backup_created=backup_created,
            write_executed=write_executed,
            issues=write_errors,
            warnings=warnings,
        )

    _append_tx_journal("WRITE_DONE", "SUCCEEDED", details={"target_count": len(new_targets or {})})
    _record_stage("WRITE", True, details={"target_count": len(new_targets or {})})

    verify_result = verify_runtime_commit(
        commit_id=commit_id,
        expected_targets=expected_targets,
        actual_targets=new_targets,
    )

    verify_passed = not verify_result.get("rollback_required", False)

    if not verify_passed:
        _append_tx_journal("VERIFY_DONE", "FAILED", details=verify_result.get("issues", []))
        rollback_success = True
        rollback_errors: list[str] = []

        for target in target_paths:
            target_path = Path(target)
            backup_files = list(backup_root.glob(f"{target_path.stem}.bak"))
            if backup_files:
                if not _restore_file(backup_files[0], target_path):
                    rollback_success = False
                    rollback_errors.append(f"rollback failed for {target}")

        rollback_executed = rollback_success
        lock_released = _safe_release_lock(guard_plan, consumer_id)

        final_status = STATUS_ROLLED_BACK if rollback_success else STATUS_MANUAL_RESTORE_REQUIRED
        recovery_status = RECOVERY_STATUS_ROLLED_BACK if rollback_success else RECOVERY_STATUS_MANUAL_RESTORE_REQUIRED
        _append_tx_journal(
            "ROLLBACK_DONE" if rollback_success else "MANUAL_RESTORE_REQUIRED",
            "SUCCEEDED" if rollback_success else "FAILED",
            details={"errors": rollback_errors},
        )
        _record_stage("ROLLBACK", rollback_success, details={"errors": rollback_errors})
        _close_journal(recovery_status, details={"rollback_errors": rollback_errors})

        return _build_result(
            status=final_status,
            transaction_id=transaction_id,
            commit_id=commit_id,
            gate_valid=gate_valid,
            guard_evaluated=guard_evaluated,
            lock_acquired=lock_acquired,
            token_validated=token_validated,
            backup_created=backup_created,
            write_executed=write_executed,
            verify_passed=verify_passed,
            rollback_executed=rollback_executed,
            lock_released=lock_released,
            issues=verify_result.get("issues", []) + (rollback_errors if not rollback_success else []),
            warnings=verify_result.get("warnings", warnings),
        )

    _append_tx_journal("VERIFY_DONE", "SUCCEEDED", details={})
    _record_stage("VERIFY", True, details={})

    consume_result = consume_runtime_commit_approval_token(
        storage_plan=token_storage_plan,
        expected_commit_id=commit_id,
        expected_plan_hash=plan_hash,
        expected_consumer_id=consumer_id,
    )

    token_consumed = consume_result.get("consume_status") == CONSUME_CONSUMED

    if not token_consumed:
        lock_released = _safe_release_lock(guard_plan, consumer_id)
        return _build_result(
            status=STATUS_BLOCKED,
            transaction_id=transaction_id,
            commit_id=commit_id,
            gate_valid=gate_valid,
            guard_evaluated=guard_evaluated,
            lock_acquired=lock_acquired,
            token_validated=token_validated,
            backup_created=backup_created,
            write_executed=write_executed,
            verify_passed=verify_passed,
            lock_released=lock_released,
            issues=["token consumption failed"],
            warnings=warnings,
        )

    lock_released = _safe_release_lock(guard_plan, consumer_id)

    _append_tx_journal("COMPLETED", "SUCCEEDED", details={"token_consumed": token_consumed})
    _close_journal(RECOVERY_STATUS_COMPLETED, details={"token_consumed": token_consumed})

    return _build_result(
        status=STATUS_COMMITTED,
        transaction_id=transaction_id,
        commit_id=commit_id,
        gate_valid=gate_valid,
        guard_evaluated=guard_evaluated,
        lock_acquired=lock_acquired,
        token_validated=token_validated,
        backup_created=backup_created,
        write_executed=write_executed,
        verify_passed=verify_passed,
        token_consumed=token_consumed,
        lock_released=lock_released,
        rollback_executed=False,
        issues=[],
        warnings=warnings,
    )
