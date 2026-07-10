# -*- coding: utf-8 -*-
"""Runtime Commit Guard (M6-14).

This module implements the pre-execution protection layer for the Real Runtime
Commit. It covers:

1. Commit Lock (acquire / read / release) using exclusive file creation.
2. Idempotency evaluation (delegated to M6-11).
3. Guard plan construction and evaluation (no lock acquisition in evaluate).

Scope boundaries (M6-14):
- No Runtime State Write (runtime/*.json) or routines/*/rules.json access.
- No Backup execution, Rollback execution, Token consume/store.
- No Real Commit Executor, no Recovery Reconciler.
- No SQLite, no third-party lock library, no OS global mutex.
- No GUI / Broker / SendOrder / Chejan connections.
- Lock files are stored only under caller-provided storage_root/locks.
- Project runtime/routines paths are rejected.

Lock acquisition uses ``open(path, "x", encoding="utf-8")`` (exclusive create)
to avoid TOCTOU. Existing files are never overwritten. A released lock record
is never reused for a new acquisition in this step; a second acquisition
attempt on the same lock_key is BLOCKED.
"""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from runtime_commit_transaction_contract import (
    evaluate_runtime_commit_idempotency,
)


# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

GUARD_TYPE = "RUNTIME_COMMIT_GUARD"
LOCK_CONTRACT_VERSION = "M6_RUNTIME_LOCK_RECORD_V1"

LOCK_STATUS_ACQUIRED = "LOCK_ACQUIRED"
LOCK_STATUS_RELEASED = "LOCK_RELEASED"

GUARD_STATUS_READY = "READY"
GUARD_STATUS_BLOCKED = "BLOCKED"
GUARD_STATUS_INVALID = "INVALID"

LOCK_READ_OK = "OK"
LOCK_READ_NOT_FOUND = "NOT_FOUND"
LOCK_READ_INVALID = "INVALID"
LOCK_READ_ERROR = "ERROR"

LOCK_ACQUIRE_ACQUIRED = "ACQUIRED"
LOCK_ACQUIRE_BLOCKED = "BLOCKED"
LOCK_ACQUIRE_INVALID = "INVALID"
LOCK_ACQUIRE_ERROR = "ERROR"

LOCK_RELEASE_RELEASED = "RELEASED"
LOCK_RELEASE_UNCHANGED = "UNCHANGED"
LOCK_RELEASE_BLOCKED = "BLOCKED"
LOCK_RELEASE_INVALID = "INVALID"
LOCK_RELEASE_ERROR = "ERROR"

# Safety flags for guard plan / evaluation results.
GUARD_SAFETY_FLAG_NAMES = (
    "file_write_called",
    "lock_acquired",
    "lock_released",
    "runtime_write",
    "token_consumed",
    "backup_created",
    "rollback_executed",
    "actual_execution",
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _as_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _project_runtime_root() -> Path:
    return (Path(__file__).resolve().parent / "runtime").resolve(strict=False)


def _under_project_runtime(path: Path) -> bool:
    target = path.resolve(strict=False)
    root = _project_runtime_root()
    try:
        target.relative_to(root)
    except ValueError:
        return False
    return True


def _guard_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in GUARD_SAFETY_FLAG_NAMES}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _compute_lock_key(commit_id: str, target_set_hash: str) -> str:
    raw = json.dumps(
        {"commit_id": commit_id, "target_set_hash": target_set_hash},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _validate_id(value: Any, label: str, issues: list[str]) -> str:
    text = _as_text(value)
    if not text:
        issues.append(f"{label} must be a non-empty string")
        return ""
    if " " in text:
        issues.append(f"{label} must not contain whitespace")
        return ""
    if "/" in text or "\\" in text or ".." in text:
        issues.append(f"{label} must not contain path separators or '..'")
        return ""
    return text


# --------------------------------------------------------------------------
# Guard Plan
# --------------------------------------------------------------------------

def create_runtime_commit_guard_plan(
    *,
    storage_root: Any,
    commit_id: Any,
    transaction_id: Any,
    target_set_hash: Any,
    owner_id: Any,
) -> dict[str, Any]:
    """Build a validated guard plan (no side effects)."""
    issues: list[str] = []
    warnings: list[str] = []

    if not isinstance(storage_root, str) or not storage_root.strip():
        return _guard_plan_result(
            GUARD_STATUS_INVALID,
            storage_root="",
            lock_path="",
            lock_key="",
            commit_id=_as_text(commit_id),
            transaction_id=_as_text(transaction_id),
            target_set_hash=_as_text(target_set_hash),
            owner_id=_as_text(owner_id),
            issues=["storage_root must be a non-empty string"],
            warnings=warnings,
        )

    commit_text = _validate_id(commit_id, "commit_id", issues)
    tx_text = _validate_id(transaction_id, "transaction_id", issues)
    hash_text = _validate_id(target_set_hash, "target_set_hash", issues)
    owner_text = _validate_id(owner_id, "owner_id", issues)

    root_path = Path(storage_root).resolve(strict=False)

    # Reject project runtime / routines paths.
    if _under_project_runtime(root_path):
        return _guard_plan_result(
            GUARD_STATUS_BLOCKED,
            storage_root=str(root_path),
            lock_path="",
            lock_key="",
            commit_id=commit_text,
            transaction_id=tx_text,
            target_set_hash=hash_text,
            owner_id=owner_text,
            issues=["project runtime path is not allowed as storage_root"],
            warnings=warnings,
        )
    if "routines" in root_path.parts:
        return _guard_plan_result(
            GUARD_STATUS_BLOCKED,
            storage_root=str(root_path),
            lock_path="",
            lock_key="",
            commit_id=commit_text,
            transaction_id=tx_text,
            target_set_hash=hash_text,
            owner_id=owner_text,
            issues=["routines path is not allowed as storage_root"],
            warnings=warnings,
        )

    # Path traversal / escape check.
    locks_dir = root_path / "locks"
    if issues:
        return _guard_plan_result(
            GUARD_STATUS_INVALID,
            storage_root=str(root_path),
            lock_path="",
            lock_key="",
            commit_id=commit_text,
            transaction_id=tx_text,
            target_set_hash=hash_text,
            owner_id=owner_text,
            issues=issues,
            warnings=warnings,
        )

    lock_key = _compute_lock_key(commit_text, hash_text)
    lock_path = locks_dir / f"{lock_key}.json"

    # Ensure lock_path stays under storage_root/locks.
    try:
        lock_path.resolve(strict=False).relative_to(locks_dir.resolve(strict=False))
    except ValueError:
        return _guard_plan_result(
            GUARD_STATUS_INVALID,
            storage_root=str(root_path),
            lock_path="",
            lock_key="",
            commit_id=commit_text,
            transaction_id=tx_text,
            target_set_hash=hash_text,
            owner_id=owner_text,
            issues=["lock_path escapes storage_root/locks"],
            warnings=warnings,
        )

    return _guard_plan_result(
        GUARD_STATUS_READY,
        storage_root=str(root_path),
        lock_path=str(lock_path),
        lock_key=lock_key,
        commit_id=commit_text,
        transaction_id=tx_text,
        target_set_hash=hash_text,
        owner_id=owner_text,
        issues=[],
        warnings=warnings,
    )


def _guard_plan_result(
    status: str,
    *,
    storage_root: str,
    lock_path: str,
    lock_key: str,
    commit_id: str,
    transaction_id: str,
    target_set_hash: str,
    owner_id: str,
    issues: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "guard_status": status,
        "storage_root": storage_root,
        "lock_path": lock_path,
        "lock_key": lock_key,
        "commit_id": commit_id,
        "transaction_id": transaction_id,
        "target_set_hash": target_set_hash,
        "owner_id": owner_id,
        "preview_only": True,
        "issues": issues,
        "warnings": warnings,
        "safety_flags": _guard_safety_flags(),
    }


# --------------------------------------------------------------------------
# Lock acquire / read / release
# --------------------------------------------------------------------------

def acquire_runtime_commit_lock(
    *,
    guard_plan: Any,
) -> dict[str, Any]:
    """Acquire a commit lock via exclusive file creation (no TOCTOU)."""
    if not isinstance(guard_plan, dict):
        return _lock_acquire_result(
            LOCK_ACQUIRE_INVALID, "", "", ["guard_plan must be a dict"]
        )
    if guard_plan.get("guard_status") != GUARD_STATUS_READY:
        return _lock_acquire_result(
            LOCK_ACQUIRE_INVALID, "", "", ["guard_plan is not READY"]
        )

    lock_path = Path(guard_plan["lock_path"])
    lock_key = guard_plan["lock_key"]
    commit_id = guard_plan["commit_id"]
    transaction_id = guard_plan["transaction_id"]
    target_set_hash = guard_plan["target_set_hash"]
    owner_id = guard_plan["owner_id"]

    # Already exists -> never overwrite, never reentrant.
    if lock_path.exists():
        existing = _read_lock_record(lock_path)
        if existing.get("lock_status") == LOCK_STATUS_ACQUIRED:
            return _lock_acquire_result(
                LOCK_ACQUIRE_BLOCKED, lock_key, str(lock_path),
                ["lock already acquired; reentrant acquisition is not allowed"],
            )
        # Released or other state: do not reuse; block new acquisition.
        return _lock_acquire_result(
            LOCK_ACQUIRE_BLOCKED, lock_key, str(lock_path),
            ["existing lock record present; new acquisition blocked"],
        )

    record = {
        "contract_version": LOCK_CONTRACT_VERSION,
        "lock_key": lock_key,
        "commit_id": commit_id,
        "transaction_id": transaction_id,
        "target_set_hash": target_set_hash,
        "owner_id": owner_id,
        "lock_status": LOCK_STATUS_ACQUIRED,
        "created_at": _now(),
        "released_at": "",
        "reentrant": False,
    }

    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive create: fails if file already exists.
        with lock_path.open("x", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        return _lock_acquire_result(
            LOCK_ACQUIRE_BLOCKED, lock_key, str(lock_path),
            ["lock file already exists (race); acquisition blocked"],
        )
    except OSError as exc:
        return _lock_acquire_result(
            LOCK_ACQUIRE_ERROR, lock_key, str(lock_path),
            [f"lock acquire failed: {exc}"],
        )

    return {
        "acquire_status": LOCK_ACQUIRE_ACQUIRED,
        "lock_key": lock_key,
        "lock_path": str(lock_path),
        "lock_record": deepcopy(record),
        "file_write_called": True,
        "lock_acquired": True,
        "runtime_write": False,
        "actual_execution": False,
        "issues": [],
        "warnings": [],
    }


def _lock_acquire_result(
    status: str, lock_key: str, lock_path: str, issues: list[str]
) -> dict[str, Any]:
    return {
        "acquire_status": status,
        "lock_key": lock_key,
        "lock_path": lock_path,
        "lock_record": None,
        "file_write_called": False,
        "lock_acquired": False,
        "runtime_write": False,
        "actual_execution": False,
        "issues": issues,
        "warnings": [],
    }


def read_runtime_commit_lock(
    *,
    guard_plan: Any,
) -> dict[str, Any]:
    """Read and validate a commit lock record (no implicit recovery)."""
    if not isinstance(guard_plan, dict):
        return _lock_read_result(LOCK_READ_INVALID, None, ["guard_plan must be a dict"])
    lock_path = Path(guard_plan.get("lock_path", ""))
    if not lock_path.exists():
        return _lock_read_result(LOCK_READ_NOT_FOUND, None, [])

    record = _read_lock_record(lock_path)
    if record is None:
        return _lock_read_result(LOCK_READ_INVALID, None, ["lock file is not valid JSON"])

    validation_issues = _validate_lock_record(record)
    if validation_issues:
        return _lock_read_result(LOCK_READ_INVALID, record, validation_issues)

    return _lock_read_result(LOCK_READ_OK, record, [])


def _read_lock_record(lock_path: Path) -> dict[str, Any] | None:
    try:
        text = lock_path.read_text(encoding="utf-8")
        obj = json.loads(text)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def _validate_lock_record(record: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    required = (
        "contract_version",
        "lock_key",
        "commit_id",
        "transaction_id",
        "target_set_hash",
        "owner_id",
        "lock_status",
        "created_at",
        "released_at",
        "reentrant",
    )
    for field in required:
        if field not in record:
            issues.append(f"lock record missing field: {field}")
    if issues:
        return issues
    if record["contract_version"] != LOCK_CONTRACT_VERSION:
        issues.append("lock contract_version is invalid")
    for field in ("lock_key", "commit_id", "transaction_id", "target_set_hash", "owner_id"):
        if not isinstance(record.get(field), str) or not record[field]:
            issues.append(f"lock field {field} must be a non-empty string")
    if record.get("lock_status") not in (LOCK_STATUS_ACQUIRED, LOCK_STATUS_RELEASED):
        issues.append("lock_status is invalid")
    if record.get("reentrant") is not False:
        issues.append("reentrant must be False")
    return issues


def _lock_read_result(
    status: str, record: dict[str, Any] | None, issues: list[str]
) -> dict[str, Any]:
    return {
        "read_status": status,
        "lock_record": deepcopy(record) if record is not None else None,
        "issues": issues,
        "warnings": [],
    }


def release_runtime_commit_lock(
    *,
    guard_plan: Any,
    expected_owner_id: Any,
) -> dict[str, Any]:
    """Release an acquired lock by rewriting the record (atomic replace)."""
    if not isinstance(guard_plan, dict):
        return _lock_release_result(
            LOCK_RELEASE_INVALID, "", ["guard_plan must be a dict"]
        )
    owner_text = _as_text(expected_owner_id)
    if not owner_text:
        return _lock_release_result(
            LOCK_RELEASE_INVALID, "", ["expected_owner_id must be a non-empty string"]
        )

    lock_path = Path(guard_plan.get("lock_path", ""))
    if not lock_path.exists():
        return _lock_release_result(LOCK_RELEASE_INVALID, str(lock_path), ["lock file not found"])

    record = _read_lock_record(lock_path)
    if record is None:
        return _lock_release_result(LOCK_RELEASE_INVALID, str(lock_path), ["lock file is not valid JSON"])

    validation_issues = _validate_lock_record(record)
    if validation_issues:
        return _lock_release_result(LOCK_RELEASE_INVALID, str(lock_path), validation_issues)

    if record["owner_id"] != owner_text:
        return _lock_release_result(
            LOCK_RELEASE_BLOCKED, str(lock_path), ["owner_id mismatch; release denied"]
        )

    if record["lock_status"] == LOCK_STATUS_RELEASED:
        return {
            "release_status": LOCK_RELEASE_UNCHANGED,
            "lock_key": record["lock_key"],
            "lock_path": str(lock_path),
            "lock_record": deepcopy(record),
            "file_write_called": False,
            "lock_released": False,
            "runtime_write": False,
            "actual_execution": False,
            "issues": [],
            "warnings": [],
        }

    if record["lock_status"] != LOCK_STATUS_ACQUIRED:
        return _lock_release_result(
            LOCK_RELEASE_BLOCKED, str(lock_path), ["lock is not in acquired state"]
        )

    # Preserve core IDs; mark released.
    released = deepcopy(record)
    released["lock_status"] = LOCK_STATUS_RELEASED
    released["released_at"] = _now()

    try:
        tmp_path = lock_path.with_name(f".{lock_path.name}.{os.urandom(8).hex()}.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(released, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, lock_path)
    except OSError as exc:
        return _lock_release_result(
            LOCK_RELEASE_ERROR, str(lock_path), [f"lock release failed: {exc}"]
        )
    finally:
        tmp = lock_path.with_name(f".{lock_path.name}.{os.urandom(8).hex()}.tmp")
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass

    return {
        "release_status": LOCK_RELEASE_RELEASED,
        "lock_key": released["lock_key"],
        "lock_path": str(lock_path),
        "lock_record": deepcopy(released),
        "file_write_called": True,
        "lock_released": True,
        "runtime_write": False,
        "actual_execution": False,
        "issues": [],
        "warnings": [],
    }


def _lock_release_result(
    status: str, lock_path: str, issues: list[str]
) -> dict[str, Any]:
    return {
        "release_status": status,
        "lock_key": "",
        "lock_path": lock_path,
        "lock_record": None,
        "file_write_called": False,
        "lock_released": False,
        "runtime_write": False,
        "actual_execution": False,
        "issues": issues,
        "warnings": [],
    }


# --------------------------------------------------------------------------
# Guard evaluation (no lock acquisition)
# --------------------------------------------------------------------------

def evaluate_runtime_commit_guard(
    *,
    guard_plan: Any,
    transaction_records: Any = None,
) -> dict[str, Any]:
    """Evaluate guard without acquiring a lock (delegates idempotency)."""
    if not isinstance(guard_plan, dict):
        return _guard_eval_result(
            GUARD_STATUS_INVALID, "", "", "", False, False, False,
            ["guard_plan must be a dict"], [],
        )

    plan_status = guard_plan.get("guard_status")
    if plan_status != GUARD_STATUS_READY:
        return _guard_eval_result(
            GUARD_STATUS_INVALID if plan_status == GUARD_STATUS_INVALID else GUARD_STATUS_BLOCKED,
            "", "", "", False, False, False,
            ["guard_plan is not READY"], [],
        )

    commit_id = guard_plan["commit_id"]
    target_set_hash = guard_plan["target_set_hash"]

    # Active lock check (read only, no acquisition).
    lock_read = read_runtime_commit_lock(guard_plan=guard_plan)
    active_lock = (
        lock_read.get("read_status") == LOCK_READ_OK
        and lock_read.get("lock_record", {}).get("lock_status") == LOCK_STATUS_ACQUIRED
    )

    idem = evaluate_runtime_commit_idempotency(
        commit_id=commit_id,
        target_set_hash=target_set_hash,
        transaction_state="CREATED",
        existing_records=transaction_records,
    )
    idem_status = idem.get("idempotency_status", "INVALID")

    execution_allowed = (
        plan_status == GUARD_STATUS_READY
        and not active_lock
        and idem_status in ("NEW", "RETRY_ALLOWED")
    )

    recovery_required = bool(idem.get("recovery_required"))
    manual_review_required = bool(idem.get("manual_review_required"))

    issues: list[str] = []
    if active_lock:
        issues.append("active lock already exists")
    if idem_status in (
        "IN_PROGRESS_BLOCKED",
        "ALREADY_COMMITTED",
        "RECOVERY_REQUIRED",
        "MANUAL_REVIEW_REQUIRED",
        "INVALID",
    ):
        issues.append(f"idempotency blocks execution: {idem_status}")

    return _guard_eval_result(
        GUARD_STATUS_READY if execution_allowed else GUARD_STATUS_BLOCKED,
        LOCK_STATUS_ACQUIRED if active_lock else "",
        idem_status,
        execution_allowed,
        recovery_required,
        manual_review_required,
        issues,
        [],
    )


def _guard_eval_result(
    guard_status: str,
    lock_status: str,
    idempotency_status: str,
    execution_allowed: bool,
    recovery_required: bool,
    manual_review_required: bool,
    issues: list[str],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "guard_status": guard_status,
        "lock_status": lock_status,
        "idempotency_status": idempotency_status,
        "execution_allowed": execution_allowed,
        "recovery_required": recovery_required,
        "manual_review_required": manual_review_required,
        "issues": issues,
        "warnings": warnings,
        "safety_flags": _guard_safety_flags(),
    }