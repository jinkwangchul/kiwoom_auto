# -*- coding: utf-8 -*-
"""Official filtered reader for monthly Event Journal JSONL files."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

from event_journal_contract import (
    CATEGORIES,
    EVENT_TYPE_LABELS,
    SEVERITIES,
    event_target_display,
    normalize_legacy_event_record,
    parse_aware_timestamp,
    validate_event_record,
)
from event_journal_writer import DEFAULT_EVENT_JOURNAL_DIR


def _month_paths(root: Path, start_at: datetime, end_at: datetime) -> list[Path]:
    year = start_at.year
    month = start_at.month
    result: list[Path] = []
    while (year, month) <= (end_at.year, end_at.month):
        result.append(root / f"{year:04d}-{month:02d}.jsonl")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return result


def _read_result(**overrides: Any) -> dict[str, Any]:
    result = {
        "events": [],
        "malformed_count": 0,
        "invalid_count": 0,
        "errors": [],
        "files_read": [],
        "invalid_request": False,
    }
    result.update(overrides)
    return result


class EventJournalReader:
    def __init__(self, journal_dir: Path = DEFAULT_EVENT_JOURNAL_DIR) -> None:
        self.journal_dir = Path(journal_dir)

    def read_events(
        self,
        *,
        start_at: str | datetime | None = None,
        end_at: str | datetime | None = None,
        category: str | None = None,
        severity: str | None = None,
        query: str = "",
        descending: bool = True,
    ) -> dict[str, Any]:
        start = self._parse_bound(start_at)
        end = self._parse_bound(end_at)
        if start_at is not None and start is None:
            return _read_result(invalid_request=True, errors=["start_at must be timezone-aware"])
        if end_at is not None and end is None:
            return _read_result(invalid_request=True, errors=["end_at must be timezone-aware"])
        if start is not None and end is not None and start > end:
            return _read_result(invalid_request=True, errors=["start_at is after end_at"])
        if category not in (None, "") and category not in CATEGORIES:
            return _read_result(invalid_request=True, errors=["category is invalid"])
        if severity not in (None, "") and severity not in SEVERITIES:
            return _read_result(invalid_request=True, errors=["severity is invalid"])

        if start is not None and end is not None:
            paths = _month_paths(self.journal_dir, start, end)
        else:
            try:
                paths = sorted(self.journal_dir.glob("????-??.jsonl"))
            except Exception as exc:
                return _read_result(errors=[str(exc)])

        events: list[dict[str, Any]] = []
        malformed_count = 0
        invalid_count = 0
        errors: list[str] = []
        files_read: list[str] = []
        normalized_query = str(query or "").strip().casefold()

        for path in paths:
            if not path.exists():
                continue
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
                issues = validate_event_record(record, allow_legacy_categories=True)
                if issues:
                    invalid_count += 1
                    continue
                record = normalize_legacy_event_record(record)
                occurred = parse_aware_timestamp(record.get("occurred_at"))
                if occurred is None:
                    invalid_count += 1
                    continue
                if start is not None and occurred < start:
                    continue
                if end is not None and occurred > end:
                    continue
                if category and record.get("category") != category:
                    continue
                if severity and record.get("severity") != severity:
                    continue
                if normalized_query and normalized_query not in self._search_text(record):
                    continue
                events.append(record)

        events.sort(
            key=lambda item: parse_aware_timestamp(item.get("occurred_at")) or datetime.min,
            reverse=bool(descending),
        )
        return _read_result(
            events=events,
            malformed_count=malformed_count,
            invalid_count=invalid_count,
            errors=errors,
            files_read=files_read,
        )

    @staticmethod
    def _parse_bound(value: str | datetime | None) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                return None
            return value
        return parse_aware_timestamp(value)

    @staticmethod
    def _search_text(record: dict[str, Any]) -> str:
        details = record.get("details")
        detail_parts: list[str] = []
        if isinstance(details, dict):
            for key in (
                "prompt_title",
                "prompt_summary",
                "selected_option",
                "interaction_type",
                "reason",
                "review_reason",
                "stage",
            ):
                value = details.get(key)
                if value not in (None, ""):
                    detail_parts.append(str(value))
        changes = record.get("changes")
        change_parts: list[str] = []
        if isinstance(changes, list):
            for change in changes:
                if not isinstance(change, dict):
                    continue
                for key in ("field_key", "before", "after"):
                    value = change.get(key)
                    if value not in (None, ""):
                        change_parts.append(str(value))
        parts = (
            event_target_display(record),
            str(record.get("event_type") or ""),
            EVENT_TYPE_LABELS.get(str(record.get("event_type") or ""), ""),
            str(record.get("summary") or ""),
            str(record.get("stock_code") or ""),
            str(record.get("stock_name") or ""),
            str(record.get("routine") or ""),
            str(record.get("reason_code") or ""),
            str(record.get("result") or ""),
            *detail_parts,
            *change_parts,
        )
        return " ".join(parts).casefold()
