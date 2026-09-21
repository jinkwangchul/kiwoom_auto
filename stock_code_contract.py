# -*- coding: utf-8 -*-
"""Canonical stock-code identity and broker-action boundaries."""

from __future__ import annotations

from typing import Any


STOCK_CODE_LENGTH = 6
INTEGRATED_MARKET_DATA_SUFFIX = "_AL"
NXT_MARKET_DATA_SUFFIX = "_NX"
MARKET_DATA_SUFFIXES = (
    INTEGRATED_MARKET_DATA_SUFFIX,
    NXT_MARKET_DATA_SUFFIX,
)


def normalize_stock_code(value: Any) -> str:
    """Normalize identity without deleting, padding, or truncating characters."""
    return str(value or "").strip().upper()


def is_valid_stock_code(value: Any) -> bool:
    """Return whether value is a canonical six-character stock identity."""
    code = normalize_stock_code(value)
    return (
        len(code) == STOCK_CODE_LENGTH
        and code != "000000"
        and code.isascii()
        and any(character.isdigit() for character in code)
        and all(character.isdigit() or "A" <= character <= "Z" for character in code)
    )


def is_numeric_stock_code(value: Any) -> bool:
    code = normalize_stock_code(value)
    return is_valid_stock_code(code) and code.isdigit()


def is_broker_action_stock_code(value: Any) -> bool:
    """Fail closed until alphanumeric codes are proven for broker action APIs."""
    return is_numeric_stock_code(value)


def canonical_stock_code_from_market_data_identity(value: Any) -> str:
    """Return the six-character owner identity for a supported quote identity."""
    identity = normalize_stock_code(value)
    for suffix in MARKET_DATA_SUFFIXES:
        if identity.endswith(suffix):
            candidate = identity[: -len(suffix)]
            return candidate if is_broker_action_stock_code(candidate) else ""
    return identity if is_broker_action_stock_code(identity) else ""


def is_market_data_stock_code(value: Any) -> bool:
    """Accept bare, integrated, or NXT quote identities without changing orders."""
    identity = normalize_stock_code(value)
    canonical = canonical_stock_code_from_market_data_identity(identity)
    if not canonical:
        return False
    return identity == canonical or any(
        identity == f"{canonical}{suffix}" for suffix in MARKET_DATA_SUFFIXES
    )


def market_data_identity_for_nxt_availability(
    canonical_code: Any,
    nxt_available: bool | None,
) -> str:
    """Choose Kiwoom integrated quotes only from verified positive evidence."""
    code = normalize_stock_code(canonical_code)
    if not is_broker_action_stock_code(code):
        return ""
    if nxt_available is True:
        return f"{code}{INTEGRATED_MARKET_DATA_SUFFIX}"
    return code


def market_source_for_identity(value: Any) -> str:
    identity = normalize_stock_code(value)
    if identity.endswith(INTEGRATED_MARKET_DATA_SUFFIX) and is_market_data_stock_code(identity):
        return "INTEGRATED"
    if identity.endswith(NXT_MARKET_DATA_SUFFIX) and is_market_data_stock_code(identity):
        return "NXT"
    if is_broker_action_stock_code(identity):
        return "KRX"
    return "UNKNOWN"


def normalize_broker_stock_code(value: Any) -> str:
    """Remove Kiwoom's leading A prefix only from seven-character wire values."""
    text = normalize_stock_code(value)
    if len(text) == STOCK_CODE_LENGTH + 1 and text.startswith("A"):
        candidate = text[1:]
        if is_valid_stock_code(candidate):
            return candidate
    return text
