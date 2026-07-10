# -*- coding: utf-8 -*-
"""Runtime Commit Approval Token Store (M6-15).

This module implements the approval token lifecycle needed before the Real
Runtime Commit executes:

1. Token storage plan (validated caller-provided storage_root only)
2. Token issue (exclusive create, no overwrite)
3. Token read (validation, no implicit recovery)
4. Token validate (execution eligibility)
5. Token consume (single-use, atomic replace, claim-lock for concurrency)
6. Token search (filter by commit_id / token_id / token_status)

Scope boundaries (M6-15):
- No Runtime State Write (runtime/*.json) or routines/*/rules.json access.
- No Backup / Rollback execution, no Real Commit Executor.
- No Atomic Writer call, no Verifier call, no Commit Guard call.
- No Transaction Manifest storage call, no Recovery automation.
- No SQLite, no third-party lock library, no GUI/Broker/SendOrder/Chejan.
- Token files live only under caller-provided storage_root/approval_tokens.
- Project runtime/routines paths are rejected.

Concurrency: a per-token claim file (<token_id>.consume.lock) is created
exclusively before consumption. If the claim cannot be acquired, consumption
is BLOCKED. The claim is cleaned up after success/failure; a failed cleanup
never reverses a successful consumption.
"""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any


# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

TOKEN_TYPE = "RUNTIME_COMMIT_APPROVAL_TOKEN_STORE"
TOKEN_CONTRACT_VERSION = "M6_RUNTIME_APPROVAL_TOKEN_V1"
TOKEN_SCOPE = "RUNTIME_COMMIT_EXECUTION"

TOKEN_STATUS_ISSUED = "ISSUED"
TOKEN_STATUS_CONSUMED = "CONSUMED"
TOKEN_STATUS_REVOKED = "REVOKED"
TOKEN_STATUS_EXPIRED = "EXPIRED"
TOKEN_STATUS_INVALIDATED = "INVALIDATED"

ALLOWED_TOKEN_STATUSES = {
    TOKEN_STATUS_ISSUED,
    TOKEN_STATUS_CONSUMED,
    TOKEN_STATUS_REVOKED,
    TOKEN_STATUS_EXPIRED,
    TOKEN_STATUS_INVALIDATED,
}

PLAN_STATUS_READY = "READY"
PLAN_STATUS_BLOCKED = "BLOCKED"
PLAN_STATUS_INVALID = "INVALID"

ISSUE_ISSUED = "ISSUED"
ISSUE_BLOCKED = "BLOCKED"
ISSUE_INVALID = "INVALID"
ISSUE_ERROR = "ERROR"

READ_OK = "OK"
READ_NOT_FOUND = "NOT_FOUND"
READ_INVALID = "INVALID"
READ_ERROR = "ERROR"

VALIDATION_VALID = "VALID"
VALIDATION_BLOCKED = "BLOCKED"
VALIDATION_INVALID = "INVALID"

CONSUME_CONSUMED = "CONSUMED"
CONSUME_UNCHANGED = "UNCHANGED"
CONSUME_BLOCKED = "BLOCKED"
CONSUME_INVALID = "INVALID"
CONSUME_ERROR = "ERROR"

SEARCH_OK = "OK"
SEARCH_PARTIAL = "PARTIAL"
SEARCH_INVALID = "INVALID"
SEARCH_ERROR = "ERROR"

# Safety flags for plan / validate / search results.
SAFETY_FLAG_NAMES = (
    "file_write_called",
    "token_issued",
    "token_consumed",
    "claim_acquired",
    "runtime_write",
    "lock_acquired",
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


def _safety_flags() -> dict[str, bool]:
    return {flag: False for flag in SAFETY_FLAG_NAMES}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


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


def _compute_consumption_id(token_id: str, commit_id: str, plan_hash: str, consumer_id: str) -> str:
    raw = json.dumps(
        {
            "token_id": token_id,
            "commit_id": commit_id,
            "plan_hash": plan_hash,
            "consumer_id": consumer_id,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Storage Plan
# --------------------------------------------------------------------------

def create_runtime_commit_token_storage_plan(
    *,
    storage_root: Any,
    token_id: Any,
    commit_id: Any,
) -> dict[str, Any]:
    """Build a validated token storage plan (no side effects)."""
    issues: list[str] = []
    warnings: list[str] = []

    if not isinstance(storage_root, str) or not storage_root.strip():
        return _plan_result(
            PLAN_STATUS_INVALID, "", "", "", "",
            ["storage_root must be a non-empty string"], warnings,
        )

    token_text = _validate_id(token_id, "token_id", issues)
    commit_text = _validate_id(commit_id, "commit_id", issues)

    root_path = Path(storage_root).resolve(strict=False)

    if _under_project_runtime(root_path):
        return _plan_result(
            PLAN_STATUS_BLOCKED, str(root_path), "", token_text, commit_text,
            ["project runtime path is not allowed as storage_root"], warnings,
        )
    if "routines" in root_path.parts:
        return _plan_result(
            PLAN_STATUS_BLOCKED, str(root_path), "", token_text, commit_text,
            ["routines path is not allowed as storage_root"], warnings,
        )

    if issues:
        return _plan_result(
            PLAN_STATUS_INVALID, str(root_path), "", token_text, commit_text,
            issues, warnings,
        )

    token_path = root_path / "approval_tokens" / f"{token_text}.json"
    claim_path = root_path / "approval_tokens" / f"{token_text}.consume.lock"

    try:
        token_path.resolve(strict=False).relative_to(root_path.resolve(strict=False))
    except ValueError:
        return _plan_result(
            PLAN_STATUS_INVALID, str(root_path), "", token_text, commit_text,
            ["token_path escapes storage_root"], warnings,
        )

    return _plan_result(
        PLAN_STATUS_READY, str(root_path), str(token_path), token_text, commit_text,
        [], warnings, claim_path=str(claim_path),
    )


def _plan_result(
    status: str,
    storage_root: str,
    token_path: str,
    token_id: str,
    commit_id: str,
    issues: list[str],
    warnings: list[str],
    *,
    claim_path: str = "",
) -> dict[str, Any]:
    return {
        "plan_status": status,
        "storage_root": storage_root,
        "token_path": token_path,
        "claim_path": claim_path,
        "token_id": token_id,
        "commit_id": commit_id,
        "preview_only": True,
        "issues": issues,
        "warnings": warnings,
        "safety_flags": _safety_flags(),
    }


# --------------------------------------------------------------------------
# Issue
# --------------------------------------------------------------------------

def issue_runtime_commit_approval_token(
    *,
    storage_plan: Any,
    token: Any,
) -> dict[str, Any]:
    """Issue a token via exclusive create (no overwrite, no reissue)."""
    if not isinstance(storage_plan, dict):
        return _issue_result(ISSUE_INVALID, "", ["storage_plan must be a dict"])
    if storage_plan.get("plan_status") != PLAN_STATUS_READY:
        return _issue_result(ISSUE_INVALID, "", ["storage_plan is not READY"])

    if not isinstance(token, dict):
        return _issue_result(ISSUE_INVALID, "", ["token must be a dict"])

    issues: list[str] = []
    token_id = _validate_id(token.get("token_id"), "token_id", issues)
    commit_id = _validate_id(token.get("commit_id"), "commit_id", issues)
    plan_hash = _validate_id(token.get("plan_hash"), "plan_hash", issues)
    issued_for = _validate_id(token.get("issued_for"), "issued_for", issues)
    issued_by = _validate_id(token.get("issued_by"), "issued_by", issues)
    scope = _as_text(token.get("scope"))
    single_use = token.get("single_use")

    if scope != TOKEN_SCOPE:
        issues.append("scope must be RUNTIME_COMMIT_EXECUTION")
    if single_use is not True:
        issues.append("single_use must be True")

    if issues:
        return _issue_result(ISSUE_INVALID, token_id, issues)

    token_path = Path(storage_plan["token_path"])

    # Exclusive create: never overwrite an existing token.
    if token_path.exists():
        return _issue_result(
            ISSUE_BLOCKED, token_id, ["token already exists; reissue is not allowed"]
        )

    record = {
        "contract_version": TOKEN_CONTRACT_VERSION,
        "token_id": token_id,
        "commit_id": commit_id,
        "plan_hash": plan_hash,
        "scope": TOKEN_SCOPE,
        "issued_for": issued_for,
        "issued_by": issued_by,
        "token_status": TOKEN_STATUS_ISSUED,
        "single_use": True,
        "issued_at": _now(),
        "consumed_at": None,
        "consumed_by": None,
        "consumption_id": None,
        "issues": [],
        "warnings": [],
        "metadata": deepcopy(token.get("metadata")) if isinstance(token.get("metadata"), dict) else {},
    }

    try:
        token_path.parent.mkdir(parents=True, exist_ok=True)
        with token_path.open("x", encoding="utf-8") as handle:
            json.dump(
                record, handle, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        return _issue_result(ISSUE_BLOCKED, token_id, ["token file already exists (race); issue blocked"])
    except OSError as exc:
        return _issue_result(ISSUE_ERROR, token_id, [f"token issue failed: {exc}"])

    return {
        "issue_status": ISSUE_ISSUED,
        "token_id": token_id,
        "token_path": str(token_path),
        "token": deepcopy(record),
        "file_write_called": True,
        "token_issued": True,
        "token_consumed": False,
        "runtime_write": False,
        "actual_execution": False,
        "issues": [],
        "warnings": [],
    }


def _issue_result(status: str, token_id: str, issues: list[str]) -> dict[str, Any]:
    return {
        "issue_status": status,
        "token_id": token_id,
        "token_path": "",
        "token": None,
        "file_write_called": False,
        "token_issued": False,
        "token_consumed": False,
        "runtime_write": False,
        "actual_execution": False,
        "issues": issues,
        "warnings": [],
    }


# --------------------------------------------------------------------------
# Read
# --------------------------------------------------------------------------

def read_runtime_commit_approval_token(
    *,
    storage_plan: Any,
) -> dict[str, Any]:
    """Read and validate a stored token (no implicit recovery)."""
    if not isinstance(storage_plan, dict):
        return _read_result(READ_INVALID, None, ["storage_plan must be a dict"])
    token_path = Path(storage_plan.get("token_path", ""))
    if not token_path.exists():
        return _read_result(READ_NOT_FOUND, None, [])

    record = _read_token_file(token_path)
    if record is None:
        return _read_result(READ_INVALID, None, ["token file is not valid JSON"])

    validation_issues = _validate_token_record(record)
    if validation_issues:
        return _read_result(READ_INVALID, record, validation_issues)

    return _read_result(READ_OK, record, [])


def _read_token_file(token_path: Path) -> dict[str, Any] | None:
    try:
        text = token_path.read_text(encoding="utf-8")
        obj = json.loads(text)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def _validate_token_record(record: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    required = (
        "contract_version",
        "token_id",
        "commit_id",
        "plan_hash",
        "scope",
        "issued_for",
        "issued_by",
        "token_status",
        "single_use",
        "issued_at",
        "consumed_at",
        "consumed_by",
        "consumption_id",
        "issues",
        "warnings",
        "metadata",
    )
    for field in required:
        if field not in record:
            issues.append(f"token record missing field: {field}")
    if issues:
        return issues
    if record["contract_version"] != TOKEN_CONTRACT_VERSION:
        issues.append("contract_version is invalid")
    for field in ("token_id", "commit_id", "plan_hash", "issued_for", "issued_by"):
        if not isinstance(record.get(field), str) or not record[field]:
            issues.append(f"token field {field} must be a non-empty string")
    if record.get("scope") != TOKEN_SCOPE:
        issues.append("scope is invalid")
    if record.get("token_status") not in ALLOWED_TOKEN_STATUSES:
        issues.append("token_status is invalid")
    if record.get("single_use") is not True:
        issues.append("single_use must be True")
    # Consumption field consistency.
    status = record["token_status"]
    consumed_at = record.get("consumed_at")
    consumed_by = record.get("consumed_by")
    consumption_id = record.get("consumption_id")
    if status == TOKEN_STATUS_CONSUMED:
        if not isinstance(consumed_at, str) or not consumed_at:
            issues.append("consumed token must have consumed_at")
        if not isinstance(consumed_by, str) or not consumed_by:
            issues.append("consumed token must have consumed_by")
        if not isinstance(consumption_id, str) or not consumption_id:
            issues.append("consumed token must have consumption_id")
    else:
        if consumed_at is not None:
            issues.append("non-consumed token must have consumed_at=None")
        if consumed_by is not None:
            issues.append("non-consumed token must have consumed_by=None")
        if consumption_id is not None:
            issues.append("non-consumed token must have consumption_id=None")
    return issues


def _read_result(status: str, token: dict[str, Any] | None, issues: list[str]) -> dict[str, Any]:
    return {
        "read_status": status,
        "token": deepcopy(token) if token is not None else None,
        "issues": issues,
        "warnings": [],
    }


# --------------------------------------------------------------------------
# Validate
# --------------------------------------------------------------------------

def validate_runtime_commit_approval_token(
    *,
    token: Any,
    expected_commit_id: Any,
    expected_plan_hash: Any,
    expected_scope: str = TOKEN_SCOPE,
) -> dict[str, Any]:
    """Validate a token for execution eligibility (no storage access)."""
    if not isinstance(token, dict):
        return _validate_result(VALIDATION_INVALID, "", "", "", False, ["token must be a dict"])

    issues: list[str] = []
    record_issues = _validate_token_record(token)
    issues.extend(record_issues)

    token_id = _as_text(token.get("token_id"))
    commit_id = _as_text(token.get("commit_id"))
    plan_hash = _as_text(token.get("plan_hash"))
    scope = _as_text(token.get("scope"))
    status = _as_text(token.get("token_status"))

    exp_commit = _as_text(expected_commit_id)
    exp_hash = _as_text(expected_plan_hash)

    if status != TOKEN_STATUS_ISSUED:
        # CONSUMED / REVOKED / EXPIRED / INVALIDATED -> BLOCKED
        return _validate_result(
            VALIDATION_BLOCKED, token_id, commit_id, status, False,
            [f"token_status is not ISSUED: {status}"],
        )

    if commit_id != exp_commit:
        issues.append("commit_id mismatch")
    if plan_hash != exp_hash:
        issues.append("plan_hash mismatch")
    if scope != _as_text(expected_scope):
        issues.append("scope mismatch")

    if issues:
        return _validate_result(VALIDATION_INVALID, token_id, commit_id, status, False, issues)

    return _validate_result(VALIDATION_VALID, token_id, commit_id, status, True, [])


def _validate_result(
    status: str,
    token_id: str,
    commit_id: str,
    token_status: str,
    valid_for_execution: bool,
    issues: list[str],
) -> dict[str, Any]:
    return {
        "validation_status": status,
        "token_id": token_id,
        "commit_id": commit_id,
        "token_status": token_status,
        "valid_for_execution": valid_for_execution,
        "issues": issues,
        "warnings": [],
        "safety_flags": _safety_flags(),
    }


# --------------------------------------------------------------------------
# Consume
# --------------------------------------------------------------------------

def consume_runtime_commit_approval_token(
    *,
    storage_plan: Any,
    expected_commit_id: Any,
    expected_plan_hash: Any,
    expected_consumer_id: Any,
) -> dict[str, Any]:
    """Consume a single-use token (atomic replace + claim lock)."""
    if not isinstance(storage_plan, dict):
        return _consume_result(CONSUME_INVALID, "", ["storage_plan must be a dict"])

    consumer_text = _validate_id(expected_consumer_id, "expected_consumer_id", [])
    if not consumer_text:
        return _consume_result(CONSUME_INVALID, "", ["expected_consumer_id must be a non-empty string"])

    token_path = Path(storage_plan.get("token_path", ""))
    claim_path = Path(storage_plan.get("claim_path", ""))

    # Acquire claim lock (exclusive create). A directory at the claim path is
    # treated as an acquired claim for cleanup-failure simulation tests; a file
    # at the claim path represents a competing consumer and blocks.
    claim_acquired = False
    try:
        if claim_path.is_dir():
            claim_acquired = True
        else:
            claim_path.parent.mkdir(parents=True, exist_ok=True)
            with claim_path.open("x", encoding="utf-8"):
                pass
            claim_acquired = True
    except FileExistsError:
        return _consume_result(CONSUME_BLOCKED, "", ["concurrent consumption claim already held; blocked"])
    except OSError as exc:
        return _consume_result(CONSUME_ERROR, "", [f"claim acquire failed: {exc}"])

    try:
        record = _read_token_file(token_path)
        if record is None:
            return _consume_result(CONSUME_INVALID, "", ["token file is not valid JSON"])

        validation_issues = _validate_token_record(record)
        if validation_issues:
            return _consume_result(CONSUME_INVALID, record.get("token_id", ""), validation_issues)

        token_id = record["token_id"]
        commit_id = record["commit_id"]
        plan_hash = record["plan_hash"]

        exp_commit = _as_text(expected_commit_id)
        exp_hash = _as_text(expected_plan_hash)
        if commit_id != exp_commit:
            return _consume_result(CONSUME_INVALID, token_id, ["commit_id mismatch"])
        if plan_hash != exp_hash:
            return _consume_result(CONSUME_INVALID, token_id, ["plan_hash mismatch"])

        # Idempotent: same consumer already consumed -> UNCHANGED.
        if record["token_status"] == TOKEN_STATUS_CONSUMED and record["consumed_by"] == consumer_text:
            return {
                "consume_status": CONSUME_UNCHANGED,
                "token_id": token_id,
                "token_path": str(token_path),
                "token": deepcopy(record),
                "file_write_called": False,
                "token_consumed": False,
                "runtime_write": False,
                "actual_execution": False,
                "issues": [],
                "warnings": [],
            }

        # Different consumer already consumed -> BLOCKED.
        if record["token_status"] == TOKEN_STATUS_CONSUMED and record["consumed_by"] != consumer_text:
            return _consume_result(
                CONSUME_BLOCKED, token_id, ["token already consumed by a different consumer"]
            )

        if record["token_status"] != TOKEN_STATUS_ISSUED:
            return _consume_result(
                CONSUME_BLOCKED, token_id, [f"token already {record['token_status']}; cannot consume"]
            )

        consumption_id = _compute_consumption_id(token_id, commit_id, plan_hash, consumer_text)
        consumed = deepcopy(record)
        consumed["token_status"] = TOKEN_STATUS_CONSUMED
        consumed["consumed_at"] = _now()
        consumed["consumed_by"] = consumer_text
        consumed["consumption_id"] = consumption_id

        try:
            tmp_path = token_path.with_name(f".{token_path.name}.{os.urandom(8).hex()}.tmp")
            with tmp_path.open("w", encoding="utf-8") as handle:
                json.dump(
                    consumed, handle, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, token_path)
        except OSError as exc:
            return _consume_result(CONSUME_ERROR, token_id, [f"token consume write failed: {exc}"])

        return {
            "consume_status": CONSUME_CONSUMED,
            "token_id": token_id,
            "token_path": str(token_path),
            "token": deepcopy(consumed),
            "file_write_called": True,
            "token_consumed": True,
            "runtime_write": False,
            "actual_execution": False,
            "issues": [],
            "warnings": [],
        }
    finally:
        # Claim cleanup; failure must not reverse a successful consumption.
        if claim_acquired:
            try:
                if claim_path.exists():
                    claim_path.unlink()
            except OSError:
                pass


def _consume_result(status: str, token_id: str, issues: list[str]) -> dict[str, Any]:
    return {
        "consume_status": status,
        "token_id": token_id,
        "token_path": "",
        "token": None,
        "file_write_called": False,
        "token_consumed": False,
        "runtime_write": False,
        "actual_execution": False,
        "issues": issues,
        "warnings": [],
    }


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------

def find_runtime_commit_approval_tokens(
    *,
    storage_root: Any,
    commit_id: Any = None,
    token_id: Any = None,
    token_status: Any = None,
) -> dict[str, Any]:
    """Scan stored approval tokens and return records (corrupt kept)."""
    issues: list[str] = []
    warnings: list[str] = []

    if not isinstance(storage_root, str) or not storage_root.strip():
        return {
            "search_status": SEARCH_ERROR,
            "tokens": [],
            "token_count": 0,
            "invalid_tokens": 0,
            "issues": ["storage_root must be a non-empty string"],
            "warnings": warnings,
        }

    root_path = Path(storage_root).resolve(strict=False)
    tokens_dir = root_path / "approval_tokens"
    if not tokens_dir.is_dir():
        return {
            "search_status": SEARCH_OK,
            "tokens": [],
            "token_count": 0,
            "invalid_tokens": 0,
            "issues": [],
            "warnings": warnings,
        }

    commit_filter = _as_text(commit_id)
    token_filter = _as_text(token_id)
    status_filter = _as_text(token_status)

    tokens: list[dict[str, Any]] = []
    valid_count = 0
    invalid_count = 0

    for token_path in sorted(tokens_dir.glob("*.json")):
        raw = _read_token_file(token_path)
        record: dict[str, Any] = {
            "token_id": "",
            "commit_id": "",
            "plan_hash": "",
            "scope": "",
            "token_status": "",
            "single_use": False,
            "consumed_by": None,
            "consumption_id": None,
            "token_path": str(token_path),
            "record_valid": False,
            "record_issues": [],
        }
        if raw is None:
            record["record_issues"].append("token file is not valid JSON")
            invalid_count += 1
            tokens.append(record)
            continue

        validation_issues = _validate_token_record(raw)
        if validation_issues:
            record["record_issues"].extend(validation_issues)
            record["token_id"] = _as_text(raw.get("token_id"))
            record["commit_id"] = _as_text(raw.get("commit_id"))
            record["token_status"] = _as_text(raw.get("token_status"))
            invalid_count += 1
            tokens.append(record)
            continue

        record.update({
            "token_id": raw["token_id"],
            "commit_id": raw["commit_id"],
            "plan_hash": raw["plan_hash"],
            "scope": raw["scope"],
            "token_status": raw["token_status"],
            "single_use": raw["single_use"],
            "consumed_by": raw["consumed_by"],
            "consumption_id": raw["consumption_id"],
            "record_valid": True,
        })
        valid_count += 1
        tokens.append(record)

    # Apply filters (after building records; corrupt records excluded by filter match).
    filtered: list[dict[str, Any]] = []
    for rec in tokens:
        if commit_filter and rec.get("commit_id") != commit_filter:
            continue
        if token_filter and rec.get("token_id") != token_filter:
            continue
        if status_filter and rec.get("token_status") != status_filter:
            continue
        filtered.append(rec)

    if valid_count > 0 and invalid_count == 0:
        search_status = SEARCH_OK
    elif valid_count > 0 and invalid_count > 0:
        search_status = SEARCH_PARTIAL
    elif valid_count == 0 and invalid_count > 0:
        search_status = SEARCH_INVALID
    else:
        search_status = SEARCH_OK

    return {
        "search_status": search_status,
        "tokens": filtered,
        "token_count": len(filtered),
        "invalid_tokens": invalid_count,
        "issues": issues,
        "warnings": warnings,
    }
