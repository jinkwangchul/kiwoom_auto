# -*- coding: utf-8 -*-
"""Side-effect-free shared contracts for manual ATS session selection."""

from __future__ import annotations

from uuid import uuid4


VALID_SESSION_KEYS = ("extra1", "extra2", "extra3")
VALID_EXECUTION_METHODS = ("ROUTINE", "MARKET", "CURRENT_PRICE")
DEFAULT_EXECUTION_METHOD = "ROUTINE"
INVALID_ATS_EXECUTION_METHOD = "INVALID_ATS_EXECUTION_METHOD"
PROGRAM_SESSION_ID = uuid4().hex


def normalized_manual_ats_session_keys(values: object) -> tuple[str, ...]:
    if isinstance(values, dict):
        selected = {key for key in VALID_SESSION_KEYS if bool(values.get(key, False))}
    elif isinstance(values, (list, tuple, set)):
        selected = {str(value or "").strip() for value in values}
    else:
        selected = set()
    return tuple(key for key in VALID_SESSION_KEYS if key in selected)


def normalize_manual_ats_execution_method(value: object) -> str | None:
    normalized = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    return normalized if normalized in VALID_EXECUTION_METHODS else None


__all__ = [
    "DEFAULT_EXECUTION_METHOD",
    "INVALID_ATS_EXECUTION_METHOD",
    "PROGRAM_SESSION_ID",
    "VALID_EXECUTION_METHODS",
    "VALID_SESSION_KEYS",
    "normalize_manual_ats_execution_method",
    "normalized_manual_ats_session_keys",
]
