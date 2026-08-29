# -*- coding: utf-8 -*-
"""Canonical stock-code identity and broker-action boundaries."""

from __future__ import annotations

from typing import Any


STOCK_CODE_LENGTH = 6


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


def normalize_broker_stock_code(value: Any) -> str:
    """Remove Kiwoom's leading A prefix only from seven-character wire values."""
    text = normalize_stock_code(value)
    if len(text) == STOCK_CODE_LENGTH + 1 and text.startswith("A"):
        candidate = text[1:]
        if is_valid_stock_code(candidate):
            return candidate
    return text
