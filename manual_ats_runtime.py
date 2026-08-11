# -*- coding: utf-8 -*-
"""Persistent per-stock manual ATS selection storage and normalization."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

from runtime_atomic_writer import STATUS_OK, write_json_atomic
from runtime_io import read_json_dict


MANUAL_ATS_SELECTION_KEY = "manual_ats_selection"
VALID_SESSION_KEYS = ("extra1", "extra2", "extra3")
PROGRAM_SESSION_ID = uuid4().hex


def _current(now_dt: datetime | None = None) -> datetime:
    return now_dt or datetime.now().astimezone()


def normalized_manual_ats_session_keys(values: object) -> tuple[str, ...]:
    if isinstance(values, dict):
        selected = {key for key in VALID_SESSION_KEYS if bool(values.get(key, False))}
    elif isinstance(values, (list, tuple, set)):
        selected = {str(value or "").strip() for value in values}
    else:
        selected = set()
    return tuple(key for key in VALID_SESSION_KEYS if key in selected)


def manual_ats_runtime_selected_keys(
    state: dict[str, object] | None,
    *,
    now_dt: datetime | None = None,
    program_session_id: str | None = None,
) -> tuple[str, ...]:
    """Return the persisted ATS selection regardless of its write date/session."""
    if not isinstance(state, dict):
        return ()
    selection = state.get(MANUAL_ATS_SELECTION_KEY)
    if not isinstance(selection, dict):
        return ()
    return normalized_manual_ats_session_keys(selection.get("selected_sessions"))


def write_manual_ats_runtime_selection(
    stock_dir: str | Path,
    selected_sessions: object,
    *,
    now_dt: datetime | None = None,
    program_session_id: str | None = None,
) -> bool:
    path = Path(stock_dir)
    state_path = path / "state.json"
    if not state_path.exists():
        return False
    state = read_json_dict(state_path)
    if not isinstance(state, dict):
        return False

    current = _current(now_dt)
    keys = normalized_manual_ats_session_keys(selected_sessions)
    state[MANUAL_ATS_SELECTION_KEY] = {
        "selected_sessions": list(keys),
        "trade_date": current.date().isoformat(),
        "program_session_id": str(program_session_id or PROGRAM_SESSION_ID),
        "updated_at": current.isoformat(timespec="seconds"),
        "source": "ATS_SETTINGS",
    }
    result = write_json_atomic(state_path, state)
    if result.get("status") != STATUS_OK:
        return False
    read_back = read_json_dict(state_path)
    return manual_ats_runtime_selected_keys(
        read_back,
        now_dt=current,
        program_session_id=program_session_id,
    ) == keys


def clear_manual_ats_runtime_selection(stock_dir: str | Path) -> bool:
    state_path = Path(stock_dir) / "state.json"
    state = read_json_dict(state_path)
    if not isinstance(state, dict):
        return False
    if MANUAL_ATS_SELECTION_KEY not in state:
        return True
    state.pop(MANUAL_ATS_SELECTION_KEY, None)
    return write_json_atomic(state_path, state).get("status") == STATUS_OK
