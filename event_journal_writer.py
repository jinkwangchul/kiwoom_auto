# -*- coding: utf-8 -*-
"""Single append boundary for the operator Event Journal."""

from __future__ import annotations

import json
from pathlib import Path
import threading
from typing import Any, Callable

from event_journal_contract import (
    EVENT_TYPE_CATEGORIES,
    SCHEMA_VERSION,
    account_safety_issues,
    new_event_id,
    parse_aware_timestamp,
    render_summary,
    sanitize_event_record,
    validate_event_record,
)


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_EVENT_JOURNAL_DIR = PROJECT_ROOT / "runtime" / "event_journal"

_WRITE_LOCK = threading.RLock()
_ID_CACHE: dict[Path, tuple[tuple[int, int], set[str]]] = {}


def _file_signature(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return (-1, -1)
    return (stat.st_size, stat.st_mtime_ns)


def _load_event_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    event_ids: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except Exception:
                continue
            if isinstance(value, dict):
                event_id = str(value.get("event_id") or "").strip()
                if event_id:
                    event_ids.add(event_id)
    return event_ids


def _cached_event_ids(path: Path) -> set[str]:
    signature = _file_signature(path)
    cached = _ID_CACHE.get(path)
    if cached is not None and cached[0] == signature:
        return cached[1]
    event_ids = _load_event_ids(path)
    _ID_CACHE[path] = (signature, event_ids)
    return event_ids


def _result(
    *,
    appended: bool = False,
    duplicate: bool = False,
    invalid: bool = False,
    write_failed: bool = False,
    event: dict[str, Any] | None = None,
    path: Path | None = None,
    issues: list[str] | None = None,
    error: str = "",
) -> dict[str, Any]:
    return {
        "appended": appended,
        "duplicate": duplicate,
        "invalid": invalid,
        "write_failed": write_failed,
        "event": event,
        "path": str(path) if path is not None else "",
        "issues": list(issues or []),
        "error": str(error or ""),
    }


class EventJournalWriter:
    """Append validated, template-rendered events to monthly JSONL files."""

    def __init__(
        self,
        journal_dir: Path = DEFAULT_EVENT_JOURNAL_DIR,
        *,
        event_id_factory: Callable[[], str] = new_event_id,
    ) -> None:
        self.journal_dir = Path(journal_dir)
        self._event_id_factory = event_id_factory

    def append_event(
        self,
        *,
        event_type: str | None = None,
        occurred_at: str | None = None,
        category: str | None = None,
        severity: str | None = None,
        template_args: dict[str, Any] | None = None,
        event_id: str | None = None,
        **optional_fields: Any,
    ) -> dict[str, Any]:
        event_type_text = str(event_type or "").strip()
        category_text = str(category or "").strip()
        severity_text = str(severity or "").strip()
        occurred_text = str(occurred_at or "").strip()
        identity = str(event_id or "").strip() or str(self._event_id_factory()).strip()

        template_account_issues = account_safety_issues(template_args or {})
        if template_account_issues:
            return _result(invalid=True, issues=template_account_issues)
        optional_account_issues = account_safety_issues(optional_fields)
        if optional_account_issues:
            return _result(invalid=True, issues=optional_account_issues)

        rendered = render_summary(event_type_text, template_args)
        if not rendered.get("rendered"):
            return _result(invalid=True, issues=[str(rendered.get("error") or "template render failed")])

        record: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "event_id": identity,
            "occurred_at": occurred_text,
            "category": category_text,
            "severity": severity_text,
            "event_type": event_type_text,
            "summary": str(rendered.get("summary") or ""),
        }
        for key, value in optional_fields.items():
            if value is not None and value != "":
                record[key] = value

        try:
            record = sanitize_event_record(record)
        except Exception as exc:
            return _result(write_failed=True, error=f"event sanitization failed: {exc}")

        issues = validate_event_record(record)
        if issues:
            return _result(invalid=True, event=record, issues=issues)

        occurred = parse_aware_timestamp(occurred_text)
        if occurred is None:
            return _result(invalid=True, event=record, issues=["occurred_at is invalid"])
        path = self.journal_dir / f"{occurred.year:04d}-{occurred.month:02d}.jsonl"

        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            with _WRITE_LOCK:
                path.parent.mkdir(parents=True, exist_ok=True)
                event_ids = _cached_event_ids(path)
                if identity in event_ids:
                    return _result(duplicate=True, event=record, path=path)
                with path.open("a", encoding="utf-8", newline="") as handle:
                    handle.write(line)
                    handle.flush()
                event_ids.add(identity)
                _ID_CACHE[path] = (_file_signature(path), event_ids)
        except Exception as exc:
            return _result(write_failed=True, event=record, path=path, error=str(exc))
        return _result(appended=True, event=record, path=path)


def expected_category(event_type: str) -> str:
    """Expose the central mapping without duplicating it in callers."""

    return EVENT_TYPE_CATEGORIES.get(str(event_type or "").strip(), "")
