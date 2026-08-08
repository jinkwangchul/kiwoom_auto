# -*- coding: utf-8 -*-
"""Fail-open Production adapter for the existing Event Journal writer."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from event_journal_contract import new_app_session_id
from event_journal_writer import EventJournalWriter, expected_category


LOGGER = logging.getLogger(__name__)

_APP_SESSION_ID = new_app_session_id()
_WRITER = EventJournalWriter()


def app_session_id() -> str:
    """Return the process-lifetime journal correlation id."""

    return _APP_SESSION_ID


def append_production_event(
    event_type: str,
    *,
    severity: str = "INFO",
    result: str | None = None,
    source: str,
    template_args: dict[str, Any] | None = None,
    occurred_at: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Append after a Production mutation without affecting that mutation."""

    try:
        return _WRITER.append_event(
            event_type=event_type,
            occurred_at=occurred_at or datetime.now().astimezone().isoformat(timespec="microseconds"),
            category=expected_category(event_type),
            severity=severity,
            template_args=template_args or {},
            app_session_id=_APP_SESSION_ID,
            result=result,
            source=source,
            **fields,
        )
    except Exception as exc:
        LOGGER.warning("Event Journal append failed for %s: %s", event_type, exc)
        return {
            "appended": False,
            "write_failed": True,
            "error": str(exc),
        }


def append_owner_event_once(
    owner: object,
    token: str,
    event_type: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Suppress duplicate lifecycle outcomes in process memory only."""

    marker_name = "_event_journal_emitted_tokens"
    tokens = getattr(owner, marker_name, None)
    if not isinstance(tokens, set):
        tokens = set()
        setattr(owner, marker_name, tokens)
    clean_token = str(token or "").strip()
    if clean_token in tokens:
        return {"appended": False, "duplicate": True, "event_type": event_type}
    tokens.add(clean_token)
    return append_production_event(event_type, **kwargs)
