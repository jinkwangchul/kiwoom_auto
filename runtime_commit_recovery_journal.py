# -*- coding: utf-8 -*-
"""Runtime Commit Recovery Journal (M6-17).

This module implements the Recovery Journal used by the Real Runtime Commit
pipeline to record recoverable stages and the final recovery outcome.

Design goals
------------
1. ``tempfile``-based only. All journal state lives under a caller-created
   temporary directory (``tempfile.mkdtemp``). It never writes to
   ``runtime/*.json`` or ``routines/*/rules.json``.
2. Append-only policy. Stage entries are appended as JSON Lines. Once a terminal
   recovery status (COMPLETED / ABORTED / ROLLED_BACK / MANUAL_RESTORE_REQUIRED)
   is recorded, the journal is CLOSED and no further stage appends are allowed.
3. Integration-friendly. The journal handle carries ``transaction_id`` and
   ``commit_id`` identical to those used by:
     - M6-13 Runtime Commit Transaction Persistence
     - M6-14 Runtime Commit Guard
     - M6-15 Runtime Commit Approval Token Store
     - M6-16 Real Runtime Commit Executor
   so the executor can append stages and record the final recovery outcome.

Scope boundaries (M6-17):
- No Runtime State Write (runtime/*.json) or routines/*/rules.json access.
- No Backup / Rollback execution, no Token consume/store, no Guard call.
- No Atomic Writer call, no Verifier call, no Commit Executor call.
- No SQLite, no third-party lock library, no GUI/Broker/SendOrder/Chejan.
- No new Contract / Preview / Wrapper creation.
- No Git operations.
- Never reaches outside the temp directory created for the journal.

Atomic appends use a local temp file + fsync + os.replace to avoid partial
writes, while preserving the append-only (never-rewrite) semantics of the
journal file content.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from uuid import uuid4


# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

JOURNAL_TYPE = "RUNTIME_COMMIT_RECOVERY_JOURNAL"

JOURNAL_CONTRACT_VERSION = "M6_RUNTIME_RECOVERY_JOURNAL_V1"
JOURNAL_EVENT_VERSION = "M6_RUNTIME_RECOVERY_JOURNAL_EVENT_V1"

# Terminal recovery statuses (recorded exactly once; closes the journal).
RECOVERY_STATUS_COMPLETED = "COMPLETED"
RECOVERY_STATUS_ABORTED = "ABORTED"
RECOVERY_STATUS_ROLLED_BACK = "ROLLED_BACK"
RECOVERY_STATUS_MANUAL_RESTORE_REQUIRED = "MANUAL_RESTORE_REQUIRED"

ALLOWED_RECOVERY_STATUSES = {
    RECOVERY_STATUS_COMPLETED,
    RECOVERY_STATUS_ABORTED,
    RECOVERY_STATUS_ROLLED_BACK,
    RECOVERY_STATUS_MANUAL_RESTORE_REQUIRED,
}

# Special stage marker used for the terminal recovery status entry.
RECOVERY_STATUS_STAGE = "__RECOVERY_STATUS__"

# Non-terminal stage statuses that may be appended before closure.
STAGE_STATUS_STARTED = "STARTED"
STAGE_STATUS_SUCCEEDED = "SUCCEEDED"
STAGE_STATUS_FAILED = "FAILED"
STAGE_STATUS_SKIPPED = "SKIPPED"
STAGE_STATUS_BLOCKED = "BLOCKED"

ALLOWED_STAGE_STATUSES = {
    STAGE_STATUS_STARTED,
    STAGE_STATUS_SUCCEEDED,
    STAGE_STATUS_FAILED,
    STAGE_STATUS_SKIPPED,
    STAGE_STATUS_BLOCKED,
}

# While no terminal recovery status has been recorded.
RECOVERY_STATUS_IN_PROGRESS = "IN_PROGRESS"
RECOVERY_STATUS_CLOSED = "CLOSED"

CREATE_OK = "OK"
CREATE_INVALID = "INVALID"
CREATE_ERROR = "ERROR"

APPEND_APPENDED = "APPENDED"
APPEND_UNCHANGED = "UNCHANGED"
APPEND_CLOSED = "CLOSED"
APPEND_INVALID = "INVALID"
APPEND_ERROR = "ERROR"

READ_OK = "OK"
READ_INVALID = "INVALID"
READ_ERROR = "ERROR"

RECORD_OK = "OK"
RECORD_UNCHANGED = "UNCHANGED"
RECORD_CLOSED = "CLOSED"
RECORD_INVALID = "INVALID"
RECORD_ERROR = "ERROR"

JOURNAL_REQUIRED_FIELDS = (
    "event_version",
    "transaction_id",
    "commit_id",
    "event_id",
    "sequence",
    "stage",
    "status",
    "created_at",
    "details",
    "is_terminal",
)

# Safety flags mirroring the neighbouring runtime-commit modules.
SAFETY_FLAG_NAMES = (
    "file_write_called",
    "journal_written",
    "runtime_write",
    "token_consumed",
    "lock_acquired",
    "backup_created",
    "rollback_executed",
    "actual_execution",
    "gui_update_called",
    "send_order_called",
    "broker_called",
    "sqlite_write",
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _as_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safety_flags() -> dict[str, bool]:
    return {flag: False for flag in SAFETY_FLAG_NAMES}


def _compute_event_id(
    transaction_id: str,
    commit_id: str,
    sequence: int,
    stage: str,
    status: str,
) -> str:
    raw = "|".join([transaction_id, commit_id, str(sequence), stage, status])
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


def _parse_journal_lines(text: str) -> tuple[list[dict[str, Any]], str | None]:
    """Parse JSON Lines text.

    Returns (events, corruption) where corruption is None for a valid sequence,
    otherwise "PARTIAL_LINE" or "INVALID_LINE". Blank lines are skipped.
    """
    if text == "":
        return [], None
    ends_with_newline = text.endswith("\n")
    lines = text.split("\n")
    if ends_with_newline:
        lines = lines[:-1]
    else:
        if lines and lines[-1].strip() != "":
            return [], "PARTIAL_LINE"
    events: list[dict[str, Any]] = []
    for line in lines:
        if line.strip() == "":
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            return [], "INVALID_LINE"
        if not isinstance(obj, dict):
            return [], "INVALID_LINE"
        events.append(obj)
    return events, None


def _read_journal_events(journal_path: Path) -> tuple[list[dict[str, Any]], str | None]:
    if not journal_path.exists():
        return [], None
    try:
        text = journal_path.read_text(encoding="utf-8")
    except OSError:
        return [], "READ_ERROR"
    return _parse_journal_lines(text)


def _atomic_append_line(journal_path: Path, line: str) -> None:
    """Append a single JSON line atomically (temp file + fsync + replace).

    The temp file is written in full (existing content + new line) to keep the
    append-only content intact and avoid interleaving on concurrent appends.
    """
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    if journal_path.exists():
        existing = journal_path.read_text(encoding="utf-8")
    new_text = existing + line
    tmp_path = journal_path.with_name(f".{journal_path.name}.{uuid4().hex}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            handle.write(new_text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, journal_path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


# --------------------------------------------------------------------------
# Journal creation
# --------------------------------------------------------------------------

def create_recovery_journal(
    *,
    transaction_id: Any,
    commit_id: Any,
    owner_id: Any = None,
    storage_root: Any = None,
) -> dict[str, Any]:
    """Create a new append-only recovery journal under a temp directory.

    The journal lives entirely inside a ``tempfile.mkdtemp`` directory. If a
    ``storage_root`` is provided it must be a directory that resolves under a
    temporary location; otherwise a fresh temp dir is created. The journal never
    writes under the project ``runtime`` directory or ``routines``.

    Returns a journal handle dict containing ``journal_path``,
    ``transaction_id``, ``commit_id``, ``owner_id`` and safety flags.
    """
    issues: list[str] = []

    tx_text = _validate_id(transaction_id, "transaction_id", issues)
    commit_text = _validate_id(commit_id, "commit_id", issues)
    owner_text = _as_text(owner_id)

    if issues:
        return _create_result(
            CREATE_INVALID, "", tx_text, commit_text, owner_text, issues
        )

    # Resolve storage directory (tempfile-based only).
    try:
        if storage_root is not None and _as_text(storage_root):
            root_path = Path(_as_text(storage_root)).resolve(strict=False)
            # Reject project runtime / routines paths outright.
            if _under_project_runtime(root_path):
                return _create_result(
                    CREATE_INVALID, "", tx_text, commit_text, owner_text,
                    ["project runtime path is not allowed as storage_root"],
                )
            if "routines" in root_path.parts:
                return _create_result(
                    CREATE_INVALID, "", tx_text, commit_text, owner_text,
                    ["routines path is not allowed as storage_root"],
                )
            root_path.mkdir(parents=True, exist_ok=True)
        else:
            root_path = Path(tempfile.mkdtemp(prefix="rj_"))
    except Exception as exc:  # noqa: BLE001
        return _create_result(
            CREATE_ERROR, "", tx_text, commit_text, owner_text,
            [f"storage_root creation failed: {exc}"],
        )

    journal_dir = root_path / "recovery_journal"
    journal_path = journal_dir / f"{commit_text}.{tx_text}.journal.jsonl"

    # Safety: journal path must stay within the resolved root.
    try:
        journal_path.resolve(strict=False).relative_to(root_path.resolve(strict=False))
    except ValueError:
        return _create_result(
            CREATE_INVALID, "", tx_text, commit_text, owner_text,
            ["journal_path escapes storage_root"],
        )

    return {
        "create_status": CREATE_OK,
        "journal_path": str(journal_path),
        "journal_dir": str(journal_dir),
        "storage_root": str(root_path),
        "transaction_id": tx_text,
        "commit_id": commit_text,
        "owner_id": owner_text,
        "contract_version": JOURNAL_CONTRACT_VERSION,
        "closed": False,
        "issues": [],
        "warnings": [],
        "safety_flags": _safety_flags(),
    }


def _under_project_runtime(path: Path) -> bool:
    target = path.resolve(strict=False)
    root = (Path(__file__).resolve().parent / "runtime").resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError:
        return False
    return True


def _create_result(
    status: str,
    journal_path: str,
    transaction_id: str,
    commit_id: str,
    owner_id: str,
    issues: list[str],
) -> dict[str, Any]:
    return {
        "create_status": status,
        "journal_path": journal_path,
        "journal_dir": "",
        "storage_root": "",
        "transaction_id": transaction_id,
        "commit_id": commit_id,
        "owner_id": owner_id,
        "contract_version": JOURNAL_CONTRACT_VERSION,
        "closed": False,
        "issues": issues,
        "warnings": [],
        "safety_flags": _safety_flags(),
    }


def _validate_journal_handle(journal: Any) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(journal, dict):
        return None, CREATE_INVALID
    if journal.get("create_status") != CREATE_OK:
        return journal, CREATE_INVALID
    if not _as_text(journal.get("journal_path")):
        return journal, CREATE_INVALID
    return journal, None


# --------------------------------------------------------------------------
# Stage append
# --------------------------------------------------------------------------

def append_recovery_stage(
    *,
    journal: Any,
    stage: Any,
    status: Any,
    details: Any = None,
) -> dict[str, Any]:
    """Append a non-terminal stage entry to the recovery journal.

    Append-only: the sequence number is always ``last_sequence + 1``. Once the
    journal is CLOSED (a terminal recovery status was recorded) further appends
    are rejected with ``APPEND_CLOSED``.
    """
    handle, issue = _validate_journal_handle(journal)
    if issue is not None:
        return _append_result(
            APPEND_INVALID, handle, 0, "", "", ["journal handle is invalid"],
        )

    issues: list[str] = []
    stage_text = _as_text(stage)
    if not stage_text:
        issues.append("stage must be a non-empty string")
    if stage_text == RECOVERY_STATUS_STAGE:
        issues.append("stage must not equal the reserved recovery-status marker")
    status_text = _as_text(status)
    if status_text not in ALLOWED_STAGE_STATUSES:
        issues.append(f"status must be one of {sorted(ALLOWED_STAGE_STATUSES)}")

    if issues:
        return _append_result(
            APPEND_INVALID, handle, 0, stage_text, status_text, issues,
        )

    # Closed journal cannot accept more stages.
    if bool(handle.get("closed")):
        return _append_result(
            APPEND_CLOSED, handle, 0, stage_text, status_text,
            ["journal is closed; terminal recovery status already recorded"],
        )

    journal_path = Path(handle["journal_path"])
    transaction_id = handle["transaction_id"]
    commit_id = handle["commit_id"]

    existing_events, corruption = _read_journal_events(journal_path)
    if corruption is not None:
        return _append_result(
            APPEND_ERROR, handle, 0, stage_text, status_text,
            [f"journal corrupted: {corruption}"],
        )

    sequences = [e.get("sequence") for e in existing_events if isinstance(e.get("sequence"), int)]
    last_sequence = max(sequences) if sequences else 0
    sequence = last_sequence + 1

    details_value = deepcopy(details) if isinstance(details, (dict, list)) else details
    if details_value is None:
        details_value = {}

    event_id = _compute_event_id(transaction_id, commit_id, sequence, stage_text, status_text)

    entry = {
        "event_version": JOURNAL_EVENT_VERSION,
        "transaction_id": transaction_id,
        "commit_id": commit_id,
        "event_id": event_id,
        "sequence": sequence,
        "stage": stage_text,
        "status": status_text,
        "created_at": _now(),
        "details": details_value,
        "is_terminal": False,
    }

    line = json.dumps(entry, ensure_ascii=False, allow_nan=False) + "\n"
    try:
        _atomic_append_line(journal_path, line)
    except Exception as exc:  # noqa: BLE001
        return _append_result(
            APPEND_ERROR, handle, sequence, stage_text, status_text,
            [f"journal append failed: {exc}"],
        )

    return {
        "append_status": APPEND_APPENDED,
        "journal_path": str(journal_path),
        "event_id": event_id,
        "sequence": sequence,
        "stage": stage_text,
        "status": status_text,
        "is_terminal": False,
        "journal_written": True,
        "file_write_called": True,
        "runtime_write": False,
        "token_consumed": False,
        "lock_acquired": False,
        "backup_created": False,
        "rollback_executed": False,
        "actual_execution": False,
        "gui_update_called": False,
        "send_order_called": False,
        "broker_called": False,
        "sqlite_write": False,
        "issues": [],
        "warnings": [],
    }


def _append_result(
    status: str,
    handle: dict[str, Any] | None,
    sequence: int,
    stage: str,
    status_text: str,
    issues: list[str],
) -> dict[str, Any]:
    return {
        "append_status": status,
        "journal_path": (handle or {}).get("journal_path", ""),
        "event_id": "",
        "sequence": sequence,
        "stage": stage,
        "status": status_text,
        "is_terminal": False,
        "journal_written": False,
        "file_write_called": False,
        "runtime_write": False,
        "token_consumed": False,
        "lock_acquired": False,
        "backup_created": False,
        "rollback_executed": False,
        "actual_execution": False,
        "gui_update_called": False,
        "send_order_called": False,
        "broker_called": False,
        "sqlite_write": False,
        "issues": issues,
        "warnings": [],
    }


# --------------------------------------------------------------------------
# Last stage query
# --------------------------------------------------------------------------

def get_last_recovery_stage(*, journal: Any) -> dict[str, Any]:
    """Return the most recently appended non-terminal stage entry.

    If no stage entries exist yet, returns status ``READ_OK`` with
    ``found=False``. Terminal recovery-status entries are excluded.
    """
    handle, issue = _validate_journal_handle(journal)
    if issue is not None:
        return _last_stage_result(READ_INVALID, handle, None, ["journal handle is invalid"])

    journal_path = Path(handle["journal_path"])
    events, corruption = _read_journal_events(journal_path)
    if corruption is not None:
        return _last_stage_result(READ_INVALID, handle, None, [f"journal corrupted: {corruption}"])

    stage_entries = [
        e for e in events
        if isinstance(e, dict)
        and not e.get("is_terminal")
        and _as_text(e.get("stage"))
        and _as_text(e.get("stage")) != RECOVERY_STATUS_STAGE
    ]
    if not stage_entries:
        return {
            "read_status": READ_OK,
            "found": False,
            "journal_path": str(journal_path),
            "last_stage": None,
            "sequence": 0,
            "status": "",
            "created_at": "",
            "issues": [],
            "warnings": [],
        }

    ordered = sorted(stage_entries, key=lambda e: e.get("sequence", 0))
    last = deepcopy(ordered[-1])
    return {
        "read_status": READ_OK,
        "found": True,
        "journal_path": str(journal_path),
        "last_stage": _as_text(last.get("stage")),
        "sequence": last.get("sequence", 0),
        "status": _as_text(last.get("status")),
        "created_at": _as_text(last.get("created_at")),
        "issues": [],
        "warnings": [],
    }


def _last_stage_result(
    status: str,
    handle: dict[str, Any] | None,
    last_stage: dict[str, Any] | None,
    issues: list[str],
) -> dict[str, Any]:
    return {
        "read_status": status,
        "found": last_stage is not None,
        "journal_path": (handle or {}).get("journal_path", ""),
        "last_stage": _as_text(last_stage.get("stage")) if last_stage else "",
        "sequence": last_stage.get("sequence", 0) if last_stage else 0,
        "status": _as_text(last_stage.get("status")) if last_stage else "",
        "created_at": _as_text(last_stage.get("created_at")) if last_stage else "",
        "issues": issues,
        "warnings": [],
    }


# --------------------------------------------------------------------------
# Recovery status record / query
# --------------------------------------------------------------------------

def record_recovery_status(
    *,
    journal: Any,
    recovery_status: Any,
    details: Any = None,
) -> dict[str, Any]:
    """Record a terminal recovery status and CLOSE the journal.

    Allowed exactly once. After a successful record the journal is CLOSED and
    further ``append_recovery_stage`` / ``record_recovery_status`` calls are
    rejected. Idempotent: recording the same status again returns UNCHANGED.
    """
    handle, issue = _validate_journal_handle(journal)
    if issue is not None:
        return _record_result(RECORD_INVALID, handle, "", ["journal handle is invalid"])

    status_text = _as_text(recovery_status)
    if status_text not in ALLOWED_RECOVERY_STATUSES:
        return _record_result(
            RECORD_INVALID, handle, status_text,
            [f"recovery_status must be one of {sorted(ALLOWED_RECOVERY_STATUSES)}"],
        )

    journal_path = Path(handle["journal_path"])
    transaction_id = handle["transaction_id"]
    commit_id = handle["commit_id"]

    existing_events, corruption = _read_journal_events(journal_path)
    if corruption is not None:
        return _record_result(
            RECORD_ERROR, handle, status_text, [f"journal corrupted: {corruption}"],
        )

    # Already closed?
    terminal_entries = [e for e in existing_events if isinstance(e, dict) and e.get("is_terminal")]
    if terminal_entries:
        existing_terminal = terminal_entries[0]
        if _as_text(existing_terminal.get("status")) == status_text:
            return {
                "record_status": RECORD_UNCHANGED,
                "journal_path": str(journal_path),
                "recovery_status": status_text,
                "event_id": _as_text(existing_terminal.get("event_id")),
                "closed": True,
                "file_write_called": False,
                "journal_written": False,
                "runtime_write": False,
                "actual_execution": False,
                "issues": [],
                "warnings": [],
            }
        return _record_result(
            RECORD_CLOSED, handle, status_text,
            ["journal already closed with a different recovery status"],
        )

    sequences = [e.get("sequence") for e in existing_events if isinstance(e.get("sequence"), int)]
    last_sequence = max(sequences) if sequences else 0
    sequence = last_sequence + 1

    details_value = deepcopy(details) if isinstance(details, (dict, list)) else details
    if details_value is None:
        details_value = {}

    event_id = _compute_event_id(
        transaction_id, commit_id, sequence, RECOVERY_STATUS_STAGE, status_text
    )

    entry = {
        "event_version": JOURNAL_EVENT_VERSION,
        "transaction_id": transaction_id,
        "commit_id": commit_id,
        "event_id": event_id,
        "sequence": sequence,
        "stage": RECOVERY_STATUS_STAGE,
        "status": status_text,
        "created_at": _now(),
        "details": details_value,
        "is_terminal": True,
    }

    line = json.dumps(entry, ensure_ascii=False, allow_nan=False) + "\n"
    try:
        _atomic_append_line(journal_path, line)
    except Exception as exc:  # noqa: BLE001
        return _record_result(
            RECORD_ERROR, handle, status_text, [f"recovery status record failed: {exc}"],
        )

    # Mark the in-memory handle as closed for callers reusing it.
    handle["closed"] = True

    return {
        "record_status": RECORD_OK,
        "journal_path": str(journal_path),
        "recovery_status": status_text,
        "event_id": event_id,
        "closed": True,
        "file_write_called": True,
        "journal_written": True,
        "runtime_write": False,
        "token_consumed": False,
        "lock_acquired": False,
        "backup_created": False,
        "rollback_executed": False,
        "actual_execution": False,
        "gui_update_called": False,
        "send_order_called": False,
        "broker_called": False,
        "sqlite_write": False,
        "issues": [],
        "warnings": [],
    }


def _record_result(
    status: str,
    handle: dict[str, Any] | None,
    recovery_status: str,
    issues: list[str],
) -> dict[str, Any]:
    return {
        "record_status": status,
        "journal_path": (handle or {}).get("journal_path", ""),
        "recovery_status": recovery_status,
        "event_id": "",
        "closed": bool((handle or {}).get("closed", False)),
        "file_write_called": False,
        "journal_written": False,
        "runtime_write": False,
        "token_consumed": False,
        "lock_acquired": False,
        "backup_created": False,
        "rollback_executed": False,
        "actual_execution": False,
        "gui_update_called": False,
        "send_order_called": False,
        "broker_called": False,
        "sqlite_write": False,
        "issues": issues,
        "warnings": [],
    }


def get_recovery_status(*, journal: Any) -> dict[str, Any]:
    """Return the recorded terminal recovery status.

    If no terminal status has been recorded, ``recovery_status`` is IN_PROGRESS
    and ``closed`` is False. Otherwise returns the recorded status and ``closed``
    is True.
    """
    handle, issue = _validate_journal_handle(journal)
    if issue is not None:
        return _recovery_status_result(READ_INVALID, handle, "", ["journal handle is invalid"])

    journal_path = Path(handle["journal_path"])
    events, corruption = _read_journal_events(journal_path)
    if corruption is not None:
        return _recovery_status_result(READ_INVALID, handle, "", [f"journal corrupted: {corruption}"])

    terminal_entries = [e for e in events if isinstance(e, dict) and e.get("is_terminal")]
    if not terminal_entries:
        # Reflect in-memory closed flag for consistency.
        closed = bool(handle.get("closed"))
        return {
            "read_status": READ_OK,
            "recovery_status": RECOVERY_STATUS_IN_PROGRESS,
            "closed": closed,
            "recorded_at": "",
            "event_id": "",
            "details": {},
            "journal_path": str(journal_path),
            "issues": [],
            "warnings": [],
        }

    terminal = terminal_entries[0]
    return {
        "read_status": READ_OK,
        "recovery_status": _as_text(terminal.get("status")),
        "closed": True,
        "recorded_at": _as_text(terminal.get("created_at")),
        "event_id": _as_text(terminal.get("event_id")),
        "details": deepcopy(terminal.get("details")) if isinstance(terminal.get("details"), dict) else {},
        "journal_path": str(journal_path),
        "issues": [],
        "warnings": [],
    }


def _recovery_status_result(
    status: str,
    handle: dict[str, Any] | None,
    recovery_status: str,
    issues: list[str],
) -> dict[str, Any]:
    return {
        "read_status": status,
        "recovery_status": recovery_status,
        "closed": bool((handle or {}).get("closed", False)),
        "recorded_at": "",
        "event_id": "",
        "details": {},
        "journal_path": (handle or {}).get("journal_path", ""),
        "issues": issues,
        "warnings": [],
    }