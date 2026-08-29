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
VALID_EXECUTION_METHODS = ("ROUTINE", "MARKET", "CURRENT_PRICE")
DEFAULT_EXECUTION_METHOD = "ROUTINE"
INVALID_ATS_EXECUTION_METHOD = "INVALID_ATS_EXECUTION_METHOD"
PROGRAM_SESSION_ID = uuid4().hex
_PRESERVE_EXECUTION_METHOD = object()


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


def normalize_manual_ats_execution_method(value: object) -> str | None:
    normalized = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    return normalized if normalized in VALID_EXECUTION_METHODS else None


def manual_ats_runtime_execution_method_result(
    state: dict[str, object] | None,
) -> dict[str, object]:
    selection = state.get(MANUAL_ATS_SELECTION_KEY) if isinstance(state, dict) else None
    if not isinstance(selection, dict) or "execution_method" not in selection:
        return {
            "ok": True,
            "execution_method": DEFAULT_EXECUTION_METHOD,
            "explicit": False,
            "reason_code": "LEGACY_DEFAULT_ROUTINE",
        }

    normalized = normalize_manual_ats_execution_method(selection.get("execution_method"))
    if normalized is None:
        return {
            "ok": False,
            "execution_method": None,
            "explicit": True,
            "reason_code": INVALID_ATS_EXECUTION_METHOD,
            "raw_value": selection.get("execution_method"),
        }
    return {
        "ok": True,
        "execution_method": normalized,
        "explicit": True,
        "reason_code": "ATS_EXECUTION_METHOD_VALID",
    }


def manual_ats_runtime_execution_method(
    state: dict[str, object] | None,
) -> str | None:
    result = manual_ats_runtime_execution_method_result(state)
    value = result.get("execution_method")
    return str(value) if result.get("ok") is True and value else None


def write_manual_ats_runtime_selection(
    stock_dir: str | Path,
    selected_sessions: object,
    *,
    execution_method: object = _PRESERVE_EXECUTION_METHOD,
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
    existing_selection = state.get(MANUAL_ATS_SELECTION_KEY)
    selection = dict(existing_selection) if isinstance(existing_selection, dict) else {}
    previous_method_present = "execution_method" in selection
    previous_method_value = selection.get("execution_method")
    if execution_method is not _PRESERVE_EXECUTION_METHOD:
        normalized_method = normalize_manual_ats_execution_method(execution_method)
        if normalized_method is None:
            return False
        selection["execution_method"] = normalized_method
    selection.update({
        "selected_sessions": list(keys),
        "trade_date": current.date().isoformat(),
        "program_session_id": str(program_session_id or PROGRAM_SESSION_ID),
        "updated_at": current.isoformat(timespec="seconds"),
        "source": "ATS_SETTINGS",
    })
    state[MANUAL_ATS_SELECTION_KEY] = selection
    result = write_json_atomic(state_path, state)
    if result.get("status") != STATUS_OK:
        return False
    read_back = read_json_dict(state_path)
    sessions_match = manual_ats_runtime_selected_keys(
        read_back,
        now_dt=current,
        program_session_id=program_session_id,
    ) == keys
    if not sessions_match:
        return False
    read_back_selection = read_back.get(MANUAL_ATS_SELECTION_KEY)
    if not isinstance(read_back_selection, dict):
        return False
    if execution_method is _PRESERVE_EXECUTION_METHOD:
        return (
            ("execution_method" in read_back_selection) == previous_method_present
            and read_back_selection.get("execution_method") == previous_method_value
        )
    return (
        manual_ats_runtime_execution_method(read_back)
        == normalize_manual_ats_execution_method(execution_method)
    )


def clear_manual_ats_runtime_selection(stock_dir: str | Path) -> bool:
    state_path = Path(stock_dir) / "state.json"
    state = read_json_dict(state_path)
    if not isinstance(state, dict):
        return False
    if MANUAL_ATS_SELECTION_KEY not in state:
        return True
    state.pop(MANUAL_ATS_SELECTION_KEY, None)
    return write_json_atomic(state_path, state).get("status") == STATUS_OK
