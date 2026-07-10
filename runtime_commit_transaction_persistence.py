# -*- coding: utf-8 -*-
"""Runtime Commit Transaction Persistence (M6-13).

This module provides the transaction persistence foundation used before the
Real Runtime Commit executes. It implements only:

1. Transaction Manifest storage (write/read)
2. Recovery Journal append (JSON Lines) + read
3. Existing transaction / idempotency record lookup

It deliberately does NOT:
- call the Atomic Writer module (runtime_atomic_writer)
- perform backup / rollback / verify
- acquire locks or consume tokens
- execute the real commit
- write Runtime State (runtime/*.json) or routines/*/rules.json
- use SQLite
- connect to GUI / Broker / SendOrder / Chejan

Atomic replacement is implemented locally (temp file + fsync + os.replace)
because the Atomic Writer module must not be called from this layer.

Storage layout (caller-provided storage_root only):

    storage_root/
    └─ transactions/
       └─ <transaction_id>/
          ├─ manifest.json
          └─ journal.jsonl

Manifest validation, transaction hash, and idempotency evaluation are delegated
to M6-11 (runtime_commit_transaction_contract) to avoid duplicating those rules.
"""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from runtime_commit_transaction_contract import (
    TRANSACTION_STAGES,
    build_runtime_commit_transaction_hash,
    evaluate_runtime_commit_idempotency,
    validate_runtime_commit_transaction_manifest,
)


# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

PERSISTENCE_TYPE = "RUNTIME_COMMIT_TRANSACTION_PERSISTENCE"

JOURNAL_EVENT_VERSION = "M6_RUNTIME_JOURNAL_EVENT_V1"

JOURNAL_EVENT_STATUSES = {
    "RECORDED",
    "STARTED",
    "SUCCEEDED",
    "FAILED",
    "BLOCKED",
}

JOURNAL_REQUIRED_FIELDS = (
    "event_version",
    "transaction_id",
    "commit_id",
    "event_id",
    "stage",
    "event_status",
    "sequence",
    "created_at",
    "details",
    "safety_flags",
)

# Safety flags used by this persistence layer (distinct from M6-11's set).
PERSISTENCE_SAFETY_FLAG_NAMES = (
    "manifest_written",
    "journal_written",
    "file_write_called",
    "runtime_write",
    "token_consumed",
    "lock_acquired",
    "backup_created",
    "rollback_executed",
    "actual_execution",
    "rules_write",
    "gui_update_called",
    "send_order_called",
    "broker_called",
    "sqlite_write",
)

STORAGE_STATUS_READY = "READY"
STORAGE_STATUS_BLOCKED = "BLOCKED"
STORAGE_STATUS_INVALID = "INVALID"

MANIFEST_WRITE_WRITTEN = "WRITTEN"
MANIFEST_WRITE_UNCHANGED = "UNCHANGED"
MANIFEST_WRITE_BLOCKED = "BLOCKED"
MANIFEST_WRITE_INVALID = "INVALID"
MANIFEST_WRITE_ERROR = "ERROR"

JOURNAL_APPEND_APPENDED = "APPENDED"
JOURNAL_APPEND_UNCHANGED = "UNCHANGED"
JOURNAL_APPEND_CONFLICT = "CONFLICT"
JOURNAL_APPEND_INVALID = "INVALID"
JOURNAL_APPEND_ERROR = "ERROR"

JOURNAL_READ_OK = "OK"
JOURNAL_READ_INVALID = "INVALID"
JOURNAL_READ_ERROR = "ERROR"

MANIFEST_READ_OK = "OK"
MANIFEST_READ_NOT_FOUND = "NOT_FOUND"
MANIFEST_READ_INVALID = "INVALID"
MANIFEST_READ_ERROR = "ERROR"

SEARCH_OK = "OK"
SEARCH_PARTIAL = "PARTIAL"
SEARCH_INVALID = "INVALID"
SEARCH_ERROR = "ERROR"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _as_text(value: Any) -> str:
    if value is None:
        return ""
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


def _persistence_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in PERSISTENCE_SAFETY_FLAG_NAMES}


def _result(*, status: str, **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"status": status}
    for key, value in fields.items():
        base[key] = value
    return base


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Local atomic JSON replacement (Atomic Writer module is not called)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(
                data,
                handle,
                sort_keys=True,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _compute_event_id(
    transaction_id: str,
    sequence: int,
    stage: str,
    event_status: str,
) -> str:
    raw = "|".join([transaction_id, str(sequence), stage, event_status])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parse_journal_lines(text: str) -> tuple[list[dict[str, Any]], str | None]:
    """Parse JSON Lines text.

    Returns (events, corruption) where corruption is None when the text is a
    valid sequence of complete JSON lines, otherwise a short reason string
    ("PARTIAL_LINE" or "INVALID_LINE"). Blank/whitespace-only lines are skipped.
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


# --------------------------------------------------------------------------
# Storage Plan
# --------------------------------------------------------------------------

def create_runtime_transaction_storage_plan(
    *,
    storage_root: Any,
    commit_id: Any,
    transaction_id: Any,
) -> dict[str, Any]:
    """Build a validated storage plan for a transaction's persistence files.

    Returns a dict with storage_status in {READY, BLOCKED, INVALID}.
    """
    issues: list[str] = []
    warnings: list[str] = []

    if not isinstance(storage_root, str) or not storage_root.strip():
        return _result(
            status=STORAGE_STATUS_INVALID,
            storage_status=STORAGE_STATUS_INVALID,
            storage_root="",
            transaction_dir="",
            manifest_path="",
            journal_path="",
            commit_id=_as_text(commit_id),
            transaction_id=_as_text(transaction_id),
            preview_only=True,
            issues=["storage_root must be a non-empty string"],
            warnings=warnings,
            safety_flags=_persistence_safety_flags(),
        )

    commit_text = _as_text(commit_id)
    if not commit_text:
        issues.append("commit_id must be a non-empty string")

    tx_text = _as_text(transaction_id)
    if not tx_text:
        issues.append("transaction_id must be a non-empty string")
    if tx_text and ("/" in tx_text or "\\" in tx_text or ".." in tx_text):
        issues.append("transaction_id must not contain path separators or '..'")

    if issues:
        return _result(
            status=STORAGE_STATUS_INVALID,
            storage_status=STORAGE_STATUS_INVALID,
            storage_root=storage_root,
            transaction_dir="",
            manifest_path="",
            journal_path="",
            commit_id=commit_text,
            transaction_id=tx_text,
            preview_only=True,
            issues=issues,
            warnings=warnings,
            safety_flags=_persistence_safety_flags(),
        )

    root_path = Path(storage_root).resolve(strict=False)
    transaction_dir = root_path / "transactions" / tx_text

    # Policy: never operate under the project runtime directory.
    if _under_project_runtime(root_path) or _under_project_runtime(transaction_dir):
        return _result(
            status=STORAGE_STATUS_BLOCKED,
            storage_status=STORAGE_STATUS_BLOCKED,
            storage_root=str(root_path),
            transaction_dir=str(transaction_dir),
            manifest_path=str(transaction_dir / "manifest.json"),
            journal_path=str(transaction_dir / "journal.jsonl"),
            commit_id=commit_text,
            transaction_id=tx_text,
            preview_only=True,
            issues=["project runtime path is not allowed as storage_root"],
            warnings=warnings,
            safety_flags=_persistence_safety_flags(),
        )

    # Policy: never operate under routines/* (rules.json protection).
    root_parts = root_path.parts
    if "routines" in root_parts:
        return _result(
            status=STORAGE_STATUS_BLOCKED,
            storage_status=STORAGE_STATUS_BLOCKED,
            storage_root=str(root_path),
            transaction_dir=str(transaction_dir),
            manifest_path=str(transaction_dir / "manifest.json"),
            journal_path=str(transaction_dir / "journal.jsonl"),
            commit_id=commit_text,
            transaction_id=tx_text,
            preview_only=True,
            issues=["routines path is not allowed as storage_root"],
            warnings=warnings,
            safety_flags=_persistence_safety_flags(),
        )

    # transaction_dir must remain under storage_root.
    try:
        transaction_dir.resolve(strict=False).relative_to(root_path)
    except ValueError:
        return _result(
            status=STORAGE_STATUS_INVALID,
            storage_status=STORAGE_STATUS_INVALID,
            storage_root=str(root_path),
            transaction_dir=str(transaction_dir),
            manifest_path=str(transaction_dir / "manifest.json"),
            journal_path=str(transaction_dir / "journal.jsonl"),
            commit_id=commit_text,
            transaction_id=tx_text,
            preview_only=True,
            issues=["transaction_dir escapes storage_root"],
            warnings=warnings,
            safety_flags=_persistence_safety_flags(),
        )

    return _result(
        status=STORAGE_STATUS_READY,
        storage_status=STORAGE_STATUS_READY,
        storage_root=str(root_path),
        transaction_dir=str(transaction_dir),
        manifest_path=str(transaction_dir / "manifest.json"),
        journal_path=str(transaction_dir / "journal.jsonl"),
        commit_id=commit_text,
        transaction_id=tx_text,
        preview_only=True,
        issues=[],
        warnings=warnings,
        safety_flags=_persistence_safety_flags(),
    )


def _validate_storage_plan(storage_plan: Any) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(storage_plan, dict):
        return None, STORAGE_STATUS_INVALID
    status = storage_plan.get("storage_status")
    if status != STORAGE_STATUS_READY:
        return storage_plan, status if status in (STORAGE_STATUS_BLOCKED, STORAGE_STATUS_INVALID) else STORAGE_STATUS_INVALID
    return storage_plan, None


# --------------------------------------------------------------------------
# Manifest write / read
# --------------------------------------------------------------------------

def write_runtime_transaction_manifest(
    *,
    storage_plan: Any,
    manifest: Any,
) -> dict[str, Any]:
    """Write a validated transaction manifest atomically (idempotent)."""
    plan, plan_issue = _validate_storage_plan(storage_plan)
    if plan_issue is not None:
        return {
            "write_status": plan_issue,
            "manifest_path": (plan or {}).get("manifest_path", ""),
            "manifest_hash": "",
            "manifest_written": False,
            "file_write_called": False,
            "runtime_write": False,
            "token_consumed": False,
            "lock_acquired": False,
            "backup_created": False,
            "rollback_executed": False,
            "actual_execution": False,
            "issues": ["storage plan is not READY"],
            "warnings": [],
        }

    if not isinstance(manifest, dict):
        return _manifest_write_result(MANIFEST_WRITE_INVALID, plan, "", ["manifest must be a dict"])

    # M6-11 validation.
    validation = validate_runtime_commit_transaction_manifest(
        manifest, expected_commit_id=plan.get("commit_id")
    )
    if not validation.get("valid"):
        return _manifest_write_result(
            MANIFEST_WRITE_INVALID, plan, "", list(validation.get("issues") or ["manifest validation failed"])
        )

    # Identity match with storage plan.
    if manifest.get("commit_id") != plan.get("commit_id"):
        return _manifest_write_result(MANIFEST_WRITE_INVALID, plan, "", ["commit_id mismatch with storage plan"])
    if manifest.get("transaction_id") != plan.get("transaction_id"):
        return _manifest_write_result(MANIFEST_WRITE_INVALID, plan, "", ["transaction_id mismatch with storage plan"])

    manifest_path = Path(plan["manifest_path"])

    # Existing manifest: never overwrite.
    if manifest_path.exists():
        try:
            existing_text = manifest_path.read_text(encoding="utf-8")
            existing = json.loads(existing_text)
        except (OSError, ValueError, TypeError):
            return _manifest_write_result(
                MANIFEST_WRITE_BLOCKED, plan, "", ["existing manifest unreadable; refusing to overwrite"]
            )
        try:
            existing_hash = build_runtime_commit_transaction_hash(existing)
            new_hash = build_runtime_commit_transaction_hash(deepcopy(manifest))
        except Exception:
            return _manifest_write_result(
                MANIFEST_WRITE_BLOCKED, plan, "", ["existing manifest hash mismatch; refusing to overwrite"]
            )
        if existing_hash == new_hash:
            return {
                "write_status": MANIFEST_WRITE_UNCHANGED,
                "manifest_path": str(manifest_path),
                "manifest_hash": existing_hash,
                "manifest_written": False,
                "file_write_called": False,
                "runtime_write": False,
                "token_consumed": False,
                "lock_acquired": False,
                "backup_created": False,
                "rollback_executed": False,
                "actual_execution": False,
                "issues": [],
                "warnings": [],
            }
        return _manifest_write_result(
            MANIFEST_WRITE_BLOCKED, plan, "", ["different manifest already exists; refusing to overwrite"]
        )

    # Write (deepcopy to avoid mutating caller input).
    try:
        new_hash = build_runtime_commit_transaction_hash(deepcopy(manifest))
        _atomic_write_json(manifest_path, deepcopy(manifest))
    except Exception as exc:  # noqa: BLE001
        return _manifest_write_result(MANIFEST_WRITE_ERROR, plan, "", [f"manifest write failed: {exc}"])

    return {
        "write_status": MANIFEST_WRITE_WRITTEN,
        "manifest_path": str(manifest_path),
        "manifest_hash": new_hash,
        "manifest_written": True,
        "file_write_called": True,
        "runtime_write": False,
        "token_consumed": False,
        "lock_acquired": False,
        "backup_created": False,
        "rollback_executed": False,
        "actual_execution": False,
        "issues": [],
        "warnings": [],
    }


def _manifest_write_result(status: str, plan: dict[str, Any], manifest_hash: str, issues: list[str]) -> dict[str, Any]:
    return {
        "write_status": status,
        "manifest_path": plan.get("manifest_path", ""),
        "manifest_hash": manifest_hash,
        "manifest_written": False,
        "file_write_called": False,
        "runtime_write": False,
        "token_consumed": False,
        "lock_acquired": False,
        "backup_created": False,
        "rollback_executed": False,
        "actual_execution": False,
        "issues": issues,
        "warnings": [],
    }


def read_runtime_commit_manifest(
    *,
    storage_plan: Any,
) -> dict[str, Any]:
    """Read and validate a stored transaction manifest."""
    plan, plan_issue = _validate_storage_plan(storage_plan)
    if plan_issue is not None:
        return {
            "read_status": plan_issue,
            "manifest": None,
            "manifest_hash": "",
            "issues": ["storage plan is not READY"],
            "warnings": [],
        }

    manifest_path = Path(plan["manifest_path"])
    if not manifest_path.exists():
        return {
            "read_status": MANIFEST_READ_NOT_FOUND,
            "manifest": None,
            "manifest_hash": "",
            "issues": [],
            "warnings": [],
        }

    try:
        text = manifest_path.read_text(encoding="utf-8")
        manifest = json.loads(text)
    except (OSError, ValueError, TypeError) as exc:
        return {
            "read_status": MANIFEST_READ_INVALID,
            "manifest": None,
            "manifest_hash": "",
            "issues": [f"manifest unreadable: {exc}"],
            "warnings": [],
        }

    if not isinstance(manifest, dict):
        return {
            "read_status": MANIFEST_READ_INVALID,
            "manifest": None,
            "manifest_hash": "",
            "issues": ["manifest root must be an object"],
            "warnings": [],
        }

    validation = validate_runtime_commit_transaction_manifest(
        manifest, expected_commit_id=plan.get("commit_id")
    )
    if not validation.get("valid"):
        return {
            "read_status": MANIFEST_READ_INVALID,
            "manifest": None,
            "manifest_hash": "",
            "issues": list(validation.get("issues") or ["manifest validation failed"]),
            "warnings": [],
        }

    try:
        manifest_hash = build_runtime_commit_transaction_hash(deepcopy(manifest))
    except Exception as exc:  # noqa: BLE001
        return {
            "read_status": MANIFEST_READ_ERROR,
            "manifest": None,
            "manifest_hash": "",
            "issues": [f"manifest hash failed: {exc}"],
            "warnings": [],
        }

    return {
        "read_status": MANIFEST_READ_OK,
        "manifest": deepcopy(manifest),
        "manifest_hash": manifest_hash,
        "issues": [],
        "warnings": [],
    }


def read_runtime_transaction_manifest(
    *,
    storage_plan: Any,
) -> dict[str, Any]:
    """Compatibility alias for read_runtime_commit_manifest.

    Both APIs return identical results for the same storage_plan.
    """
    return read_runtime_commit_manifest(storage_plan=storage_plan)


# --------------------------------------------------------------------------
# Journal append / read
# --------------------------------------------------------------------------

def append_runtime_transaction_journal_event(
    *,
    storage_plan: Any,
    event: Any,
) -> dict[str, Any]:
    """Append one journal event as a JSON line (idempotent on event_id)."""
    plan, plan_issue = _validate_storage_plan(storage_plan)
    if plan_issue is not None:
        return _journal_append_result(JOURNAL_APPEND_INVALID, plan, "", 0, "", "", ["storage plan is not READY"])

    if not isinstance(event, dict):
        return _journal_append_result(JOURNAL_APPEND_INVALID, plan, "", 0, "", "", ["event must be a dict"])

    event_copy = deepcopy(event)
    missing = [f for f in JOURNAL_REQUIRED_FIELDS if f not in event_copy]
    if missing:
        return _journal_append_result(
            JOURNAL_APPEND_INVALID, plan, "", 0, "", "", [f"event missing fields: {', '.join(missing)}"]
        )

    if event_copy.get("event_version") != JOURNAL_EVENT_VERSION:
        return _journal_append_result(JOURNAL_APPEND_INVALID, plan, "", 0, "", "", ["event_version is invalid"])

    if event_copy.get("transaction_id") != plan.get("transaction_id"):
        return _journal_append_result(JOURNAL_APPEND_INVALID, plan, "", 0, "", "", ["transaction_id mismatch with storage plan"])
    if event_copy.get("commit_id") != plan.get("commit_id"):
        return _journal_append_result(JOURNAL_APPEND_INVALID, plan, "", 0, "", "", ["commit_id mismatch with storage plan"])

    stage = _as_text(event_copy.get("stage"))
    if stage not in TRANSACTION_STAGES:
        return _journal_append_result(JOURNAL_APPEND_INVALID, plan, "", 0, stage, "", ["stage is not allowed"])

    event_status = _as_text(event_copy.get("event_status"))
    if event_status not in JOURNAL_EVENT_STATUSES:
        return _journal_append_result(JOURNAL_APPEND_INVALID, plan, "", 0, stage, event_status, ["event_status is not allowed"])

    sequence = event_copy.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        return _journal_append_result(JOURNAL_APPEND_INVALID, plan, "", 0, stage, event_status, ["sequence must be an int >= 1"])

    journal_path = Path(plan["journal_path"])
    existing_events, corruption = _read_journal_events(journal_path)
    if corruption is not None:
        return _journal_append_result(
            JOURNAL_APPEND_ERROR, plan, "", sequence, stage, event_status, [f"journal corrupted: {corruption}"]
        )

    existing_sequences = [e.get("sequence") for e in existing_events if isinstance(e.get("sequence"), int)]
    existing_event_ids = {_as_text(e.get("event_id")) for e in existing_events}
    last_sequence = max(existing_sequences) if existing_sequences else 0

    computed_event_id = _compute_event_id(plan["transaction_id"], sequence, stage, event_status)

    if sequence in existing_sequences:
        # Same sequence already present: idempotent only if event_id matches.
        for e in existing_events:
            if e.get("sequence") == sequence:
                if _as_text(e.get("event_id")) == computed_event_id:
                    return {
                        "append_status": JOURNAL_APPEND_UNCHANGED,
                        "journal_path": str(journal_path),
                        "event_id": computed_event_id,
                        "sequence": sequence,
                        "stage": stage,
                        "event_status": event_status,
                        "journal_written": False,
                        "file_write_called": False,
                        "runtime_write": False,
                        "token_consumed": False,
                        "lock_acquired": False,
                        "backup_created": False,
                        "rollback_executed": False,
                        "actual_execution": False,
                        "issues": [],
                        "warnings": [],
                    }
                return _journal_append_result(
                    JOURNAL_APPEND_CONFLICT, plan, computed_event_id, sequence, stage, event_status,
                    ["different event already recorded at this sequence"],
                )

    if sequence != last_sequence + 1:
        return _journal_append_result(
            JOURNAL_APPEND_INVALID, plan, computed_event_id, sequence, stage, event_status,
            ["sequence must be exactly last_sequence + 1 (no gaps, no skips)"],
        )

    if computed_event_id in existing_event_ids:
        return _journal_append_result(
            JOURNAL_APPEND_UNCHANGED, plan, computed_event_id, sequence, stage, event_status, []
        )

    # Persist with deterministic event_id.
    event_copy["event_id"] = computed_event_id
    line = json.dumps(event_copy, ensure_ascii=False, allow_nan=False) + "\n"
    try:
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with journal_path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception as exc:  # noqa: BLE001
        return _journal_append_result(
            JOURNAL_APPEND_ERROR, plan, computed_event_id, sequence, stage, event_status, [f"journal append failed: {exc}"]
        )

    return {
        "append_status": JOURNAL_APPEND_APPENDED,
        "journal_path": str(journal_path),
        "event_id": computed_event_id,
        "sequence": sequence,
        "stage": stage,
        "event_status": event_status,
        "journal_written": True,
        "file_write_called": True,
        "runtime_write": False,
        "token_consumed": False,
        "lock_acquired": False,
        "backup_created": False,
        "rollback_executed": False,
        "actual_execution": False,
        "issues": [],
        "warnings": [],
    }


def _journal_append_result(
    status: str,
    plan: dict[str, Any],
    event_id: str,
    sequence: int,
    stage: str,
    event_status: str,
    issues: list[str],
) -> dict[str, Any]:
    return {
        "append_status": status,
        "journal_path": plan.get("journal_path", ""),
        "event_id": event_id,
        "sequence": sequence,
        "stage": stage,
        "event_status": event_status,
        "journal_written": False,
        "file_write_called": False,
        "runtime_write": False,
        "token_consumed": False,
        "lock_acquired": False,
        "backup_created": False,
        "rollback_executed": False,
        "actual_execution": False,
        "issues": issues,
        "warnings": [],
    }


def read_runtime_transaction_journal(
    *,
    storage_plan: Any,
) -> dict[str, Any]:
    """Read and validate a transaction journal (JSON Lines)."""
    plan, plan_issue = _validate_storage_plan(storage_plan)
    if plan_issue is not None:
        return {
            "read_status": plan_issue,
            "events": [],
            "event_count": 0,
            "last_sequence": 0,
            "last_stage": "",
            "issues": ["storage plan is not READY"],
            "warnings": [],
        }

    journal_path = Path(plan["journal_path"])
    if not journal_path.exists():
        return {
            "read_status": JOURNAL_READ_OK,
            "events": [],
            "event_count": 0,
            "last_sequence": 0,
            "last_stage": "",
            "issues": [],
            "warnings": ["journal not found; treated as empty"],
        }

    events, corruption = _read_journal_events(journal_path)
    if corruption is not None:
        return {
            "read_status": JOURNAL_READ_INVALID,
            "events": [],
            "event_count": 0,
            "last_sequence": 0,
            "last_stage": "",
            "issues": [f"journal corrupted: {corruption}"],
            "warnings": [],
        }

    issues: list[str] = []
    seen_sequences: set[int] = set()
    seen_event_ids: set[str] = set()
    valid_events: list[dict[str, Any]] = []

    for idx, ev in enumerate(events):
        ev_issues: list[str] = []
        if not isinstance(ev, dict):
            issues.append(f"event[{idx}] is not an object")
            continue
        for field in JOURNAL_REQUIRED_FIELDS:
            if field not in ev:
                ev_issues.append(f"event[{idx}] missing {field}")
        if ev.get("event_version") != JOURNAL_EVENT_VERSION:
            ev_issues.append(f"event[{idx}] event_version invalid")
        stage = _as_text(ev.get("stage"))
        if stage not in TRANSACTION_STAGES:
            ev_issues.append(f"event[{idx}] stage invalid: {stage}")
        estatus = _as_text(ev.get("event_status"))
        if estatus not in JOURNAL_EVENT_STATUSES:
            ev_issues.append(f"event[{idx}] event_status invalid: {estatus}")
        seq = ev.get("sequence")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
            ev_issues.append(f"event[{idx}] sequence invalid")
        if ev.get("transaction_id") != plan.get("transaction_id"):
            ev_issues.append(f"event[{idx}] transaction_id mismatch")
        if ev.get("commit_id") != plan.get("commit_id"):
            ev_issues.append(f"event[{idx}] commit_id mismatch")
        if isinstance(seq, int) and seq in seen_sequences:
            ev_issues.append(f"event[{idx}] duplicate sequence {seq}")
        eid = _as_text(ev.get("event_id"))
        if eid and eid in seen_event_ids:
            ev_issues.append(f"event[{idx}] duplicate event_id {eid}")

        if not ev_issues:
            valid_events.append(deepcopy(ev))
            if isinstance(seq, int):
                seen_sequences.add(seq)
            if eid:
                seen_event_ids.add(eid)
        else:
            issues.extend(ev_issues)

    if issues:
        return {
            "read_status": JOURNAL_READ_INVALID,
            "events": deepcopy(events),
            "event_count": len(events),
            "last_sequence": max(seen_sequences) if seen_sequences else 0,
            "last_stage": "",
            "issues": issues,
            "warnings": [],
        }

    last_sequence = max(seen_sequences) if seen_sequences else 0
    last_stage = ""
    if valid_events:
        ordered = sorted(valid_events, key=lambda e: e.get("sequence", 0))
        last_stage = _as_text(ordered[-1].get("stage"))

    return {
        "read_status": JOURNAL_READ_OK,
        "events": valid_events,
        "event_count": len(valid_events),
        "last_sequence": last_sequence,
        "last_stage": last_stage,
        "issues": [],
        "warnings": [],
    }


# --------------------------------------------------------------------------
# Record search / idempotency compatibility
# --------------------------------------------------------------------------

def find_runtime_transaction_records(
    *,
    storage_root: Any,
    commit_id: Any = None,
    target_set_hash: Any = None,
    transaction_id: Any = None,
) -> dict[str, Any]:
    """Scan stored transaction manifests/journals and return records.

    Corrupt records are reported (record_valid=False) rather than silently
    dropped. search_status is one of OK / PARTIAL / INVALID / ERROR.
    """
    issues: list[str] = []
    warnings: list[str] = []

    if not isinstance(storage_root, str) or not storage_root.strip():
        return {
            "search_status": SEARCH_ERROR,
            "records": [],
            "record_count": 0,
            "issues": ["storage_root must be a non-empty string"],
            "warnings": warnings,
        }

    root_path = Path(storage_root).resolve(strict=False)
    transactions_dir = root_path / "transactions"
    if not transactions_dir.is_dir():
        return {
            "search_status": SEARCH_OK,
            "records": [],
            "record_count": 0,
            "issues": [],
            "warnings": warnings,
        }

    commit_filter = _as_text(commit_id)
    hash_filter = _as_text(target_set_hash)
    tx_filter = _as_text(transaction_id)

    records: list[dict[str, Any]] = []
    valid_count = 0
    invalid_count = 0

    for tx_dir in sorted(p for p in transactions_dir.iterdir() if p.is_dir()):
        manifest_path = tx_dir / "manifest.json"
        journal_path = tx_dir / "journal.jsonl"

        record: dict[str, Any] = {
            "transaction_id": "",
            "commit_id": "",
            "target_set_hash": "",
            "transaction_status": "",
            "current_stage": "",
            "manifest_path": str(manifest_path),
            "journal_path": str(journal_path),
            "last_journal_stage": "",
            "last_sequence": 0,
            "recovery_required": False,
            "manual_restore_required": False,
            "lock_active": False,
            "record_valid": False,
            "record_issues": [],
        }

        # Manifest
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError) as exc:
                manifest = None
                record["record_issues"].append(f"manifest unreadable: {exc}")
            if isinstance(manifest, dict):
                validation = validate_runtime_commit_transaction_manifest(manifest)
                if validation.get("valid"):
                    record["transaction_id"] = _as_text(manifest.get("transaction_id"))
                    record["commit_id"] = _as_text(manifest.get("commit_id"))
                    record["target_set_hash"] = _as_text(manifest.get("target_set_hash"))
                    record["transaction_status"] = _as_text(manifest.get("transaction_status"))
                    record["current_stage"] = _as_text(manifest.get("current_stage"))
                    record["recovery_required"] = bool(manifest.get("recovery_required"))
                    record["manual_restore_required"] = bool(manifest.get("manual_restore_required"))
                    record["record_valid"] = True
                else:
                    record["record_issues"].extend(list(validation.get("issues") or ["manifest invalid"]))
                    record["transaction_id"] = _as_text(manifest.get("transaction_id"))
                    record["commit_id"] = _as_text(manifest.get("commit_id"))
                    record["target_set_hash"] = _as_text(manifest.get("target_set_hash"))

        # Journal
        if journal_path.exists():
            events, corruption = _read_journal_events(journal_path)
            if corruption is None:
                sequences = [e.get("sequence") for e in events if isinstance(e.get("sequence"), int)]
                if sequences:
                    record["last_sequence"] = max(sequences)
                    ordered = sorted(events, key=lambda e: e.get("sequence", 0))
                    record["last_journal_stage"] = _as_text(ordered[-1].get("stage"))
            else:
                record["record_issues"].append(f"journal corrupted: {corruption}")
                record["record_valid"] = False

        # Apply filters.
        if commit_filter and record.get("commit_id") != commit_filter:
            continue
        if hash_filter and record.get("target_set_hash") != hash_filter:
            continue
        if tx_filter and record.get("transaction_id") != tx_filter:
            continue

        if record["record_valid"]:
            valid_count += 1
        else:
            invalid_count += 1
        records.append(record)

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
        "records": records,
        "record_count": len(records),
        "issues": issues,
        "warnings": warnings,
    }
