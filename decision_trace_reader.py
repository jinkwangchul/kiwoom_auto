# -*- coding: utf-8 -*-
"""Fault-tolerant reader for Decision Trace JSONL files."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

from decision_trace_contract import ENVIRONMENTS, STAGES, TRACE_LEVELS, parse_aware_timestamp, validate_trace_record
from decision_trace_writer import DEFAULT_LIVE_TRACE_DIR


def _result(**overrides: Any) -> dict[str, Any]:
    result = {
        "records": [],
        "malformed_count": 0,
        "invalid_count": 0,
        "errors": [],
        "files_read": [],
        "invalid_request": False,
    }
    result.update(overrides)
    return result


class DecisionTraceReader:
    def __init__(self, trace_root: Path = DEFAULT_LIVE_TRACE_DIR) -> None:
        self.trace_root = Path(trace_root)

    def read_records(
        self,
        *,
        trace_id: str = "",
        signal_id: str = "",
        order_id: str = "",
        execution_id: str = "",
        stock_code: str = "",
        routine_instance_id: str = "",
        stage: str = "",
        environment: str = "",
        trace_level: str = "",
        start_at: str | datetime | None = None,
        end_at: str | datetime | None = None,
        descending: bool = False,
    ) -> dict[str, Any]:
        start = self._parse_bound(start_at)
        end = self._parse_bound(end_at)
        if start_at is not None and start is None:
            return _result(invalid_request=True, errors=["start_at must be timezone-aware"])
        if end_at is not None and end is None:
            return _result(invalid_request=True, errors=["end_at must be timezone-aware"])
        if start is not None and end is not None and start > end:
            return _result(invalid_request=True, errors=["start_at is after end_at"])
        if stage and stage not in STAGES:
            return _result(invalid_request=True, errors=["stage is invalid"])
        if environment and environment not in ENVIRONMENTS:
            return _result(invalid_request=True, errors=["environment is invalid"])
        if trace_level and trace_level not in TRACE_LEVELS:
            return _result(invalid_request=True, errors=["trace_level is invalid"])

        try:
            if self.trace_root.is_file():
                paths = [self.trace_root]
            else:
                paths = sorted(self.trace_root.rglob("*.jsonl"))
        except Exception as exc:
            return _result(errors=[str(exc)])

        records: list[dict[str, Any]] = []
        malformed_count = 0
        invalid_count = 0
        errors: list[str] = []
        files_read: list[str] = []
        for path in paths:
            try:
                with path.open("r", encoding="utf-8") as handle:
                    lines = list(handle)
            except Exception as exc:
                errors.append(f"{path}: {exc}")
                continue
            files_read.append(str(path))
            for line in lines:
                try:
                    record = json.loads(line)
                except Exception:
                    malformed_count += 1
                    continue
                if validate_trace_record(record):
                    invalid_count += 1
                    continue
                occurred = parse_aware_timestamp(record.get("recorded_at"))
                if occurred is None:
                    invalid_count += 1
                    continue
                if start is not None and occurred < start:
                    continue
                if end is not None and occurred > end:
                    continue
                if trace_id and record.get("trace_id") != trace_id:
                    continue
                if signal_id and record.get("signal_id") != signal_id:
                    continue
                if order_id and record.get("order_id") != order_id:
                    continue
                if execution_id and record.get("execution_id") != execution_id:
                    continue
                if stock_code and record.get("stock_code") != stock_code:
                    continue
                if routine_instance_id and record.get("routine_instance_id") != routine_instance_id:
                    continue
                if stage and record.get("stage") != stage:
                    continue
                if environment and record.get("environment") != environment:
                    continue
                if trace_level and record.get("trace_level") != trace_level:
                    continue
                records.append(record)
        records.sort(
            key=lambda item: parse_aware_timestamp(item.get("recorded_at")) or datetime.min,
            reverse=bool(descending),
        )
        return _result(
            records=records,
            malformed_count=malformed_count,
            invalid_count=invalid_count,
            errors=errors,
            files_read=files_read,
        )

    def read_trace(self, trace_id: str) -> dict[str, Any]:
        return self.read_records(trace_id=str(trace_id or ""), descending=False)

    @staticmethod
    def _parse_bound(value: str | datetime | None) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                return None
            return value
        return parse_aware_timestamp(value)
