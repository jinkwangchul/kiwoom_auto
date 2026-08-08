# -*- coding: utf-8 -*-
"""Single fail-open append boundary for Decision Trace JSONL records."""

from __future__ import annotations

import json
from pathlib import Path
import threading
from typing import Any

from decision_trace_contract import parse_aware_timestamp, validate_trace_record


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_LIVE_TRACE_DIR = PROJECT_ROOT / "runtime" / "diagnostics" / "decision_trace" / "live"

_WRITE_LOCK = threading.RLock()
_ID_CACHE: dict[Path, tuple[tuple[int, int], set[str], set[str]]] = {}


def _file_signature(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return (-1, -1)
    return (stat.st_size, stat.st_mtime_ns)


def _load_identities(path: Path) -> tuple[set[str], set[str]]:
    record_ids: set[str] = set()
    decision_trace_ids: set[str] = set()
    if not path.exists():
        return record_ids, decision_trace_ids
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except Exception:
                continue
            if not isinstance(value, dict):
                continue
            record_id = str(value.get("trace_record_id") or "").strip()
            if record_id:
                record_ids.add(record_id)
            if value.get("stage") == "DECISION":
                trace_id = str(value.get("trace_id") or "").strip()
                if trace_id:
                    decision_trace_ids.add(trace_id)
    return record_ids, decision_trace_ids


def _cached_identities(path: Path) -> tuple[set[str], set[str]]:
    signature = _file_signature(path)
    cached = _ID_CACHE.get(path)
    if cached is not None and cached[0] == signature:
        return cached[1], cached[2]
    record_ids, decision_trace_ids = _load_identities(path)
    _ID_CACHE[path] = (signature, record_ids, decision_trace_ids)
    return record_ids, decision_trace_ids


def _result(
    *,
    appended: bool = False,
    duplicate: bool = False,
    invalid: bool = False,
    write_failed: bool = False,
    record: dict[str, Any] | None = None,
    path: Path | None = None,
    issues: list[str] | None = None,
    error: str = "",
) -> dict[str, Any]:
    return {
        "appended": appended,
        "duplicate": duplicate,
        "invalid": invalid,
        "write_failed": write_failed,
        "record": record,
        "path": str(path) if path is not None else "",
        "issues": list(issues or []),
        "error": str(error or ""),
    }


class DecisionTraceWriter:
    def __init__(
        self,
        live_trace_dir: Path = DEFAULT_LIVE_TRACE_DIR,
        *,
        backtest_trace_dir: Path | None = None,
    ) -> None:
        self.live_trace_dir = Path(live_trace_dir)
        self.backtest_trace_dir = Path(backtest_trace_dir) if backtest_trace_dir is not None else None

    def append_record(self, record: Any) -> dict[str, Any]:
        issues = validate_trace_record(record)
        if issues:
            return _result(invalid=True, record=record if isinstance(record, dict) else None, issues=issues)
        assert isinstance(record, dict)
        path_result = self._path_for(record)
        if isinstance(path_result, str):
            return _result(invalid=True, record=record, issues=[path_result])
        path = path_result
        record_id = str(record["trace_record_id"])
        trace_id = str(record["trace_id"])
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
        try:
            with _WRITE_LOCK:
                path.parent.mkdir(parents=True, exist_ok=True)
                record_ids, decision_trace_ids = _cached_identities(path)
                if record_id in record_ids:
                    return _result(duplicate=True, record=record, path=path)
                if record.get("stage") == "DECISION" and trace_id in decision_trace_ids:
                    return _result(duplicate=True, record=record, path=path)
                with path.open("a", encoding="utf-8", newline="") as handle:
                    handle.write(line)
                    handle.flush()
                record_ids.add(record_id)
                if record.get("stage") == "DECISION":
                    decision_trace_ids.add(trace_id)
                _ID_CACHE[path] = (_file_signature(path), record_ids, decision_trace_ids)
        except Exception as exc:
            return _result(write_failed=True, record=record, path=path, error=str(exc))
        return _result(appended=True, record=record, path=path)

    def _path_for(self, record: dict[str, Any]) -> Path | str:
        if record.get("environment") == "LIVE":
            occurred = parse_aware_timestamp(record.get("recorded_at"))
            if occurred is None:
                return "recorded_at is invalid"
            return self.live_trace_dir / f"{occurred.date().isoformat()}.jsonl"
        if self.backtest_trace_dir is None:
            return "backtest_trace_dir is required for BACKTEST writes"
        run_id = str(record.get("backtest_run_id") or "").strip()
        return self.backtest_trace_dir / run_id / "decision_trace.jsonl"
