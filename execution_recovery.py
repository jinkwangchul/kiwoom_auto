# -*- coding: utf-8 -*-
"""In-memory recovery helpers for ExecutionContext snapshots.

ExecutionRecovery creates and restores in-memory snapshots only. It never reads
or writes files, touches runtime storage, commits queues, calls SendOrder, or
connects to GUI components.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from execution_context import CONTEXT_TYPE, ExecutionContext


SNAPSHOT_TYPE = "EXECUTION_CONTEXT_SNAPSHOT"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _result(*, valid: bool, issues: list[str] | None = None, warnings: list[str] | None = None) -> dict[str, Any]:
    return {
        "valid": valid,
        "issues": list(issues or []),
        "warnings": list(warnings or []),
    }


class ExecutionRecovery:
    """Create, validate, and restore in-memory ExecutionContext snapshots."""

    def create_snapshot(self, context: ExecutionContext) -> dict[str, Any]:
        if not isinstance(context, ExecutionContext):
            raise ValueError("context must be an ExecutionContext")
        return {
            "snapshot_type": SNAPSHOT_TYPE,
            "created_at": _now_iso(),
            "context": deepcopy(context.to_dict()),
        }

    def restore_snapshot(self, snapshot: Any) -> ExecutionContext:
        validation = self.validate_snapshot(snapshot)
        if validation.get("valid") is not True:
            raise ValueError(f"invalid execution context snapshot: {validation.get('issues')}")
        return ExecutionContext.from_dict(deepcopy(snapshot)["context"])

    def validate_snapshot(self, snapshot: Any) -> dict[str, Any]:
        if not isinstance(snapshot, dict):
            return _result(valid=False, issues=["SNAPSHOT_MUST_BE_DICT"])

        issues: list[str] = []
        if snapshot.get("snapshot_type") != SNAPSHOT_TYPE:
            issues.append("INVALID_SNAPSHOT_TYPE")
        if not snapshot.get("created_at"):
            issues.append("MISSING_CREATED_AT")

        context = snapshot.get("context")
        if not isinstance(context, dict):
            return _result(valid=False, issues=issues + ["MISSING_CONTEXT"])

        if context.get("context_type") != CONTEXT_TYPE:
            issues.append("INVALID_CONTEXT_TYPE")
        if "state" not in context:
            issues.append("MISSING_STATE")
        if "journal" not in context:
            issues.append("MISSING_JOURNAL")
        if "metadata" not in context:
            issues.append("MISSING_METADATA")

        state = _as_dict(context.get("state"))
        journal = _as_dict(context.get("journal"))
        metadata = context.get("metadata")
        if not isinstance(metadata, dict):
            issues.append("METADATA_MUST_BE_DICT")

        issues.extend(_state_issues(state))
        issues.extend(_journal_issues(journal))

        if not issues:
            try:
                ExecutionContext.from_dict(deepcopy(context))
            except Exception as exc:
                issues.append(f"CONTEXT_RESTORE_VALIDATION_FAILED: {exc}")

        return _result(valid=not issues, issues=issues, warnings=[])

    def copy_snapshot(self, snapshot: Any) -> dict[str, Any]:
        return deepcopy(snapshot)

    def summary(self, snapshot: Any) -> dict[str, Any]:
        validation = self.validate_snapshot(snapshot)
        context = _as_dict(_as_dict(snapshot).get("context"))
        state = _as_dict(context.get("state"))
        journal = _as_dict(context.get("journal"))
        return {
            "snapshot_type": SNAPSHOT_TYPE,
            "valid": validation.get("valid") is True,
            "issues": list(validation.get("issues") or []),
            "warnings": list(validation.get("warnings") or []),
            "session_count": state.get("session_count") if isinstance(state, dict) else None,
            "event_count": journal.get("event_count") if isinstance(journal, dict) else None,
            "created_at": _as_dict(snapshot).get("created_at"),
        }


def _state_issues(state: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if not state:
        return ["STATE_MUST_BE_DICT"]
    sessions = state.get("sessions")
    if not isinstance(sessions, list):
        return ["STATE_SESSIONS_MUST_BE_LIST"]
    for index, session in enumerate(sessions):
        if not isinstance(session, dict):
            issues.append(f"SESSION_{index}_MUST_BE_DICT")
            continue
        for field in ("session_id", "created_at", "execution_id", "order_id", "request_hash", "lock_id", "status"):
            if not session.get(field):
                issues.append(f"SESSION_{index}_MISSING_{field.upper()}")
        if not isinstance(session.get("metadata"), dict):
            issues.append(f"SESSION_{index}_METADATA_MUST_BE_DICT")
    return issues


def _journal_issues(journal: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if not journal:
        return ["JOURNAL_MUST_BE_DICT"]
    events = journal.get("events")
    if not isinstance(events, list):
        return ["JOURNAL_EVENTS_MUST_BE_LIST"]
    seen: set[str] = set()
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            issues.append(f"EVENT_{index}_MUST_BE_DICT")
            continue
        event_id = event.get("event_id")
        if not event_id:
            issues.append(f"EVENT_{index}_MISSING_EVENT_ID")
        elif event_id in seen:
            issues.append(f"EVENT_{index}_DUPLICATE_EVENT_ID")
        else:
            seen.add(str(event_id))
        for field in ("event_type", "session_id", "created_at"):
            if not event.get(field):
                issues.append(f"EVENT_{index}_MISSING_{field.upper()}")
        if not isinstance(event.get("payload"), dict):
            issues.append(f"EVENT_{index}_PAYLOAD_MUST_BE_DICT")
    return issues
