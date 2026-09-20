# -*- coding: utf-8 -*-
"""Pure normalization shared by read-only historical candle consumers."""

from __future__ import annotations

from typing import Any, Mapping


def normalize_date_based_candle_row(
    row: Mapping[str, Any] | None,
) -> dict[str, str] | None:
    """Project one date-based broker row into the canonical historical shape."""
    if not isinstance(row, Mapping):
        return None
    date_text = str(row.get("\uc77c\uc790") or "").strip()
    if len(date_text) != 8 or not date_text.isdigit():
        return None
    return {
        "\uccb4\uacb0\uc2dc\uac04": f"{date_text}000000",
        "\uc2dc\uac00": str(row.get("\uc2dc\uac00") or "").strip(),
        "\uace0\uac00": str(row.get("\uace0\uac00") or "").strip(),
        "\uc800\uac00": str(row.get("\uc800\uac00") or "").strip(),
        "\ud604\uc7ac\uac00": str(row.get("\ud604\uc7ac\uac00") or "").strip(),
        "\uac70\ub798\ub7c9": str(
            row.get("\uac70\ub7c9") or row.get("\uac70\ub798\ub7c9") or ""
        ).strip(),
    }
