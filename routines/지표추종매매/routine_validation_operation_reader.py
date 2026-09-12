# -*- coding: utf-8 -*-
"""Read-only adapter for the canonical current Operation fact."""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path


_ACTIVE_STATUSES = frozenset({"RUNNING", "CLOSING"})
_KNOWN_STATUSES = _ACTIVE_STATUSES | {"NORMAL_ENDED"}
_DEFAULT_OPERATION_STATE_PATH = (
    Path(__file__).resolve().parents[2] / "runtime" / "operation_state.json"
)


class ValidationOperationStateReadError(RuntimeError):
    """The canonical Operation fact could not be read unambiguously."""


def is_operation_active(
    operation_state_path: str | Path | None = None,
    *,
    today: date | None = None,
) -> bool:
    """Return whether today's canonical Operation is RUNNING or CLOSING.

    Missing, malformed, or unknown facts raise so ``ValidationSession`` can
    apply its existing fail-closed reader-error decision.
    """

    path = (
        _DEFAULT_OPERATION_STATE_PATH
        if operation_state_path is None
        else Path(operation_state_path)
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValidationOperationStateReadError(
            "canonical Operation state is unavailable"
        ) from exc

    if not isinstance(payload, dict):
        raise ValidationOperationStateReadError(
            "canonical Operation state must be an object"
        )

    raw_operation_date = payload.get("operation_date")
    raw_status = payload.get("operation_status")
    if not isinstance(raw_operation_date, str) or not isinstance(raw_status, str):
        raise ValidationOperationStateReadError(
            "canonical Operation state schema is invalid"
        )

    operation_date_text = raw_operation_date.strip()
    status = raw_status.strip().upper()
    try:
        operation_date = date.fromisoformat(operation_date_text)
    except ValueError as exc:
        raise ValidationOperationStateReadError(
            "canonical Operation date is invalid"
        ) from exc
    if status not in _KNOWN_STATUSES:
        raise ValidationOperationStateReadError(
            "canonical Operation status is unknown"
        )

    current_date = today if today is not None else date.today()
    if not isinstance(current_date, date):
        raise TypeError("today must be a date")
    return operation_date == current_date and status in _ACTIVE_STATUSES
