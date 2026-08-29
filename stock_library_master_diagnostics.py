# -*- coding: utf-8 -*-
"""Read-only diagnostics for Kiwoom Master stock-code list responses."""

from __future__ import annotations

from collections import Counter
import hashlib
import unicodedata
from typing import Any

from stock_code_contract import (
    is_valid_stock_code,
    normalize_stock_code,
)


DIAGNOSTIC_SCHEMA_VERSION = "stock_library_invalid_codes_v1"


def valid_stock_library_code(value: object) -> bool:
    return is_valid_stock_code(value)


def _has_control_character(value: str) -> bool:
    return any(unicodedata.category(char) in {"Cc", "Cf"} for char in value)


def invalid_stock_library_code_reason(value: object) -> str:
    raw = str(value or "")
    stripped = raw.strip()
    canonical = normalize_stock_code(stripped)
    if valid_stock_library_code(stripped):
        return ""
    if not stripped:
        return "EMPTY"
    if _has_control_character(stripped):
        return "CONTROL_CHARACTER"
    if canonical == "000000":
        return "ZERO_CODE"
    if len(canonical) != 6:
        return "LENGTH_NOT_6"
    if not is_valid_stock_code(canonical):
        if any(not (char.isdigit() or "A" <= char <= "Z") for char in canonical):
            return "SPECIAL_CHARACTER"
        return "NO_DIGIT"
    if raw != stripped:
        return "WHITESPACE_VARIATION"
    return "OTHER"


def master_code_token_diagnostic(raw_token: object) -> dict[str, Any]:
    raw = str(raw_token or "")
    stripped = raw.strip()
    valid = valid_stock_library_code(stripped)
    return {
        "raw_token": raw,
        "stripped_token": stripped,
        "normalized_token": normalize_stock_code(stripped),
        "raw_repr": ascii(raw),
        "raw_length": len(raw),
        "stripped_length": len(stripped),
        "isdigit": stripped.isdigit(),
        "isascii": stripped.isascii(),
        "leading_whitespace": bool(raw) and raw[0].isspace(),
        "trailing_whitespace": bool(raw) and raw[-1].isspace(),
        "has_control_character": _has_control_character(raw),
        "current_validator_valid": valid,
        "invalid_reason": "" if valid else invalid_stock_library_code_reason(raw),
    }


def build_master_code_diagnostic_projection(raw_value: object) -> dict[str, Any]:
    original = str(raw_value or "")
    raw_tokens = original.split(";")
    token_diagnostics = [master_code_token_diagnostic(token) for token in raw_tokens]
    normalized: list[str] = []
    seen: set[str] = set()
    for token in token_diagnostics:
        normalized_token = str(token["normalized_token"])
        if not normalized_token or normalized_token in seen:
            continue
        normalized.append(normalized_token)
        seen.add(normalized_token)

    raw_invalid_reasons = Counter(
        str(token["invalid_reason"])
        for token in token_diagnostics
        if not bool(token["current_validator_valid"])
    )
    return {
        "original_return_length": len(original),
        "original_return_sha256": hashlib.sha256(original.encode("utf-8")).hexdigest(),
        "split_token_count": len(raw_tokens),
        "normalized_unique_count": len(normalized),
        "raw_invalid_token_count": sum(raw_invalid_reasons.values()),
        "raw_invalid_by_reason": dict(sorted(raw_invalid_reasons.items())),
        "tokens": token_diagnostics,
    }
