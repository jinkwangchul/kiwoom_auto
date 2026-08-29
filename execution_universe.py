# -*- coding: utf-8 -*-
"""Central current-session execution universe projection.

This module is deliberately widget-free and read-only. It projects durable stock
state together with the process-local operation participant set into the only
stock universe allowed to create new routine signals.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable

from gui_auto_trade_integrity import (
    is_emergency_stopped_state,
    is_operation_excluded,
    is_review_required_state,
)
from gui_auto_trade_policy import (
    auto_trade_current_session_operation_participant_codes,
    auto_trade_setting_trade_started,
)
from gui_auto_trade_runtime import all_registered_stock_dirs, parse_stock_folder_name


RECOVERY_READINESS_UNAVAILABLE = "RECOVERY_READINESS_UNAVAILABLE"
RECOVERY_NOT_READY = "RECOVERY_NOT_READY"
INVALID_STOCK_CODE = "INVALID_STOCK_CODE"
NOT_CURRENT_SESSION_PARTICIPANT = "NOT_CURRENT_SESSION_PARTICIPANT"
TRADE_NOT_STARTED = "TRADE_NOT_STARTED"
OPERATION_EXCLUDED = "OPERATION_EXCLUDED"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
EMERGENCY_BLOCKED = "EMERGENCY_BLOCKED"
STATUS_BLOCKED = "STATUS_BLOCKED"

_LOCAL_BLOCKED_STATUSES = {
    "REVIEW_REQUIRED",
    "REVIEW",
    "EMERGENCY_STOPPED",
    "EMERGENCY_STOP",
    "EMERGENCY",
    "STOPPED",
    "STOP",
    "MANUAL_STOPPED",
    "UNREGISTERED",
}


@dataclass(frozen=True)
class ExecutionUniverseEntry:
    stock_code: str
    stock_name: str
    stock_dir: Path
    participant: bool
    persisted_trade_started: bool
    operation_excluded: bool
    review_required: bool
    emergency_blocked: bool
    real_trade_enabled: bool
    signal_probe_only: bool
    execution_member: bool
    execution_ready: bool
    blockers: tuple[str, ...]


@dataclass(frozen=True)
class ExecutionUniverseSnapshot:
    participant_stock_codes: tuple[str, ...]
    execution_stock_codes: tuple[str, ...]
    global_ready: bool
    global_blockers: tuple[str, ...]
    entries: tuple[ExecutionUniverseEntry, ...]


def _read_json_dict(path: Path) -> dict[str, object]:
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _status_blocked(state: dict[str, object]) -> bool:
    raw_status = str(state.get("status", "") or "").strip().upper()
    return raw_status in _LOCAL_BLOCKED_STATUSES


def _global_readiness(window) -> tuple[bool, tuple[str, ...]]:
    checker = getattr(window, "startup_recovery_session_ready", None)
    if not callable(checker):
        return False, (RECOVERY_READINESS_UNAVAILABLE,)
    try:
        ready = bool(checker(refresh=False))
    except Exception:
        return False, (RECOVERY_READINESS_UNAVAILABLE,)
    if not ready:
        return False, (RECOVERY_NOT_READY,)
    return True, ()


def project_execution_universe(
    window,
    *,
    stock_dirs: Iterable[Path] | None = None,
) -> ExecutionUniverseSnapshot:
    """Return the current read-only execution universe snapshot.

    Membership is stock-local and never depends on global Recovery readiness.
    Readiness is the final execution gate: members remain visible in the
    snapshot even when Recovery is unavailable or not ready.
    """

    participant_stock_codes = auto_trade_current_session_operation_participant_codes(
        window
    )
    participant_set = set(participant_stock_codes)
    global_ready, global_blockers = _global_readiness(window)

    if stock_dirs is not None:
        candidate_dirs = list(stock_dirs)
    else:
        registered_getter = getattr(window, "registered_operation_targets", None)
        if callable(registered_getter):
            try:
                registered_targets = registered_getter()
                candidate_dirs = [
                    Path(stock_dir) for stock_dir, _code, _name in registered_targets
                ]
            except TypeError:
                candidate_dirs = all_registered_stock_dirs()
        else:
            candidate_dirs = all_registered_stock_dirs()
    entries: list[ExecutionUniverseEntry] = []
    execution_stock_codes: list[str] = []

    for raw_stock_dir in candidate_dirs:
        stock_dir = Path(raw_stock_dir)
        stock_code, stock_name = parse_stock_folder_name(stock_dir.name)
        state = _read_json_dict(stock_dir / "state.json")
        config = _read_json_dict(stock_dir / "config.json")

        participant = bool(stock_code and stock_code in participant_set)
        persisted_trade_started = auto_trade_setting_trade_started(state)
        operation_excluded = is_operation_excluded(config)
        review_required = is_review_required_state(state)
        emergency_blocked = is_emergency_stopped_state(state)
        real_trade_enabled = state.get("real_trade_enabled") is True
        signal_probe_only = state.get("signal_probe_only") is True

        blockers: list[str] = []
        if not stock_code:
            blockers.append(INVALID_STOCK_CODE)
        if not participant:
            blockers.append(NOT_CURRENT_SESSION_PARTICIPANT)
        if not persisted_trade_started:
            blockers.append(TRADE_NOT_STARTED)
        if operation_excluded:
            blockers.append(OPERATION_EXCLUDED)
        if review_required:
            blockers.append(REVIEW_REQUIRED)
        if emergency_blocked:
            blockers.append(EMERGENCY_BLOCKED)
        if _status_blocked(state):
            blockers.append(STATUS_BLOCKED)

        execution_member = not blockers
        execution_ready = execution_member and global_ready
        if execution_ready:
            execution_stock_codes.append(stock_code)

        entries.append(
            ExecutionUniverseEntry(
                stock_code=stock_code,
                stock_name=stock_name,
                stock_dir=stock_dir,
                participant=participant,
                persisted_trade_started=persisted_trade_started,
                operation_excluded=operation_excluded,
                review_required=review_required,
                emergency_blocked=emergency_blocked,
                real_trade_enabled=real_trade_enabled,
                signal_probe_only=signal_probe_only,
                execution_member=execution_member,
                execution_ready=execution_ready,
                blockers=tuple(blockers),
            )
        )

    return ExecutionUniverseSnapshot(
        participant_stock_codes=tuple(participant_stock_codes),
        execution_stock_codes=tuple(execution_stock_codes),
        global_ready=global_ready,
        global_blockers=tuple(global_blockers),
        entries=tuple(entries),
    )


def execution_ready_entries(
    snapshot: ExecutionUniverseSnapshot,
) -> tuple[ExecutionUniverseEntry, ...]:
    return tuple(entry for entry in snapshot.entries if entry.execution_ready)
