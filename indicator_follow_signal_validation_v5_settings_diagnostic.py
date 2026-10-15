"""Opt-in, detached V5 settings evidence; never evaluates or applies settings.

Files are observations, not an alternative settings store. Projection submission
and application records correlate by PID/window/projection_key_sha256, not time.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from uuid import uuid4

from routines.지표추종매매.routine_validation_contract import ValidationSettingsSnapshot

OUTPUT_DIRECTORY = Path(__file__).resolve().parent / "runtime" / "diagnostics" / "v5_settings"
_SENSITIVE = re.compile(
    r"account|accno|password|passwd|pwd|token|secret|credential|auth|login|"
    r"certificate|private.?key|계좌|비밀번호|인증", re.IGNORECASE,
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _detached(value):
    # Only JSON values are allowed; never serialize arbitrary broker/QObject reprs.
    if isinstance(value, Mapping):
        return {str(key): "[REDACTED]" if _SENSITIVE.search(str(key)) else _detached(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_detached(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_detached(item) for item in value), key=_json)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("Non-JSON diagnostic value")


def _settings(snapshot, source, confirmed):
    available = isinstance(snapshot, ValidationSettingsSnapshot)
    rules = snapshot.to_dict() if available else None
    digest = snapshot.rules_hash if available else None
    valid = available and _hash(rules) == digest
    return {"source": source, "available": available,
            "confirmed": bool(confirmed and valid), "rules_hash": digest,
            "hash_integrity": bool(valid), "rules": _detached(rules)}


def _descriptors(values):
    return [{"identity": item.identity, "family": item.family, "axis": item.axis,
             "sides": list(item.sides), "series_keys": list(item.series_keys),
             "supported": item.supported, "supported_sides": list(item.supported_sides),
             "unsupported_sides": list(item.unsupported_sides),
             "parameters": _detached(item.parameters)} for item in values]


def _compare(left, right):
    if left is None or right is None:
        return "UNAVAILABLE"
    return "MATCH" if left == right else "DIFFERENT"


def export_settings_snapshot(window, event, *, request_settings=None,
                             projection_request=None, projection_ui_state=None):
    """Read existing objects only; every failure remains outside chart behavior."""
    if os.environ.get("KIWOOM_V5_DIAG_SETTINGS_EXPORT") != "1":
        return {"status": "DISABLED"}
    try:
        result = getattr(window, "_result_settings_snapshot", None)
        replay = getattr(window, "_replay_snapshot", None)
        result_ready = (
            not getattr(window, "_chart_results_suspended", False)
            and bool(getattr(window, "_calculation_candles", None))
            and (getattr(window, "_normal_base_ready", False)
                 or getattr(window, "_range_replay_from_normal_base", False)
                 or (replay is not None and getattr(replay, "settings_hash", None)
                     == getattr(result, "rules_hash", None)))
        )
        result_record = _settings(result, "window._result_settings_snapshot", result_ready)
        request_record = _settings(request_settings, "flow.session.request.settings_snapshot",
                                   request_settings is not None)
        seed = getattr(window, "_signal_validation_seed", None)
        seed_record = _settings(getattr(seed, "settings_snapshot", None),
                                "window._signal_validation_seed.settings_snapshot", True)
        range_record = _settings(
            getattr(window, "_range_replay_settings_snapshot", None),
            "window._range_replay_settings_snapshot",
            bool(getattr(window, "_range_replay_entries_available", False)
                 and getattr(window, "_validation_range", None) is not None
                 and not getattr(window, "_chart_results_suspended", False)),
        )
        stored_ui = getattr(window, "_result_ui_state", None)
        descriptors = _descriptors(getattr(window, "_visualization_descriptors", ()))
        key = getattr(window, "_visualization_current_key", None)
        submitted = None
        if projection_request is not None:
            rules = projection_request["rules"]
            submitted = {
                "source": "window._start_visualization_projection.request",
                "rules": _detached(rules), "rules_sha256": _hash(rules),
                "descriptors": _descriptors(projection_request["descriptors"]),
                "ui_state": _detached(projection_request["ui_state"]),
                "projection_key_sha256": _hash(projection_request["key"]),
                "generation": projection_request["generation"],
                "data_epoch": projection_request["data_epoch"],
                "application_confirmed": False,
            }
        projection_pending = any(getattr(window, name, None) is not None for name in (
            "_visualization_projection_active", "_visualization_projection_pending",
            "_visualization_projection_deferred",
        ))
        missing = []
        if not result_record["confirmed"]:
            missing.append("APPLIED_RESULT_SETTINGS_UNAVAILABLE")
        if not isinstance(stored_ui, Mapping):
            missing.append("STORED_RESULT_UI_UNAVAILABLE")
        if getattr(window, "canvas", None) is None or getattr(window, "_visualization_data_error", ""):
            missing.append("APPLIED_VISUALIZATION_UNAVAILABLE")
        payload = {
            "schema": "kiwoom.v5.settings_diagnostic.v1",
            "status": "SNAPSHOT_UNAVAILABLE" if missing else "SNAPSHOT_CAPTURED",
            "unavailable_reasons": missing, "event": event,
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(), "window_id": id(window),
            "module_paths": {name: getattr(sys.modules.get(name), "__file__", None) for name in (
                "gui_indicator_follow_signal_validation_v5_window",
                "gui_indicator_follow_signal_validation_v5_flow",
            )},
            "stock_code": getattr(getattr(window, "stock", None), "code", None),
            "timeframe_key": getattr(window, "_normal_base_timeframe_key", None)
                             or getattr(replay, "timeframe_key", None),
            "ui_state": {
                "source": "window._result_ui_state", "value": _detached(stored_ui),
                "available": isinstance(stored_ui, Mapping),
                "sha256": _hash(stored_ui) if isinstance(stored_ui, Mapping) else None,
                "current_input_confirmed": False,
                "result_settings_pairing_confirmed": False,
                "note": "Retained UI may predate draft/result; no UI getter was called.",
            },
            "calculation_settings": {"request": request_record, "result": result_record,
                                     "range_result": range_record},
            "initial_settings": seed_record,
            "visualization_settings": {
                "source": "window._visualization_descriptors",
                "descriptors": descriptors, "descriptors_sha256": _hash(descriptors),
                "canvas_present": getattr(window, "canvas", None) is not None,
                "projection_pending": projection_pending,
                "application_confirmed": event == "visualization_applied",
                "projection_key_sha256": _hash(key) if key is not None else None,
                "generation": getattr(window, "_visualization_projection_generation", None),
                "data_epoch": getattr(window, "_visualization_data_epoch", None),
                "not_identities": _detached(getattr(window, "_not_filter_identities", ())),
                "inner_not_identities": _detached(getattr(window, "_inner_not_filter_identities", ())),
                "ocr_applied_ui_state": _detached(projection_ui_state),
                "submitted_request": submitted,
                "note": "Descriptors are not the full calculation rules. Submission does not prove application.",
            },
            "comparisons": {
                "request_vs_result": _compare(request_record["rules_hash"], result_record["rules_hash"]),
                "request_vs_range_result": _compare(request_record["rules_hash"], range_record["rules_hash"]),
                "seed_vs_result": _compare(seed_record["rules_hash"], result_record["rules_hash"]),
                "result_vs_submitted_projection": _compare(result_record["rules_hash"],
                    submitted["rules_sha256"] if submitted else None),
                "stored_ui_vs_applied_projection_ui": _compare(
                    _hash(stored_ui) if isinstance(stored_ui, Mapping) else None,
                    _hash(projection_ui_state) if isinstance(projection_ui_state, Mapping) else None),
                "ui_vs_rules_semantics": "UNAVAILABLE_NO_RECALCULATION",
            },
        }
        serialized = _json(payload)
        OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
        path = OUTPUT_DIRECTORY / f"settings-{os.getpid()}-{uuid4().hex}.json"
        with path.open("x", encoding="utf-8") as handle:
            handle.write(serialized)
        return {"status": "EXPORTED", "path": path}
    except Exception:
        # Do not leak exception text or change the chart/error/log lifecycle.
        return {"status": "EXPORT_FAILED"}
