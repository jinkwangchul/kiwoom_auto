# -*- coding: utf-8 -*-
"""Pure BUY/SELL conflict selection shared by production and validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SignalSideSelection:
    selected_side: str | None
    conflict: bool


def select_signal_side(
    sell_true: bool,
    buy_true: bool,
    holding_quantity: Any,
) -> SignalSideSelection:
    """Select SELL while holding on conflict; otherwise prefer the true side."""
    try:
        has_holding = int(holding_quantity or 0) > 0
    except (TypeError, ValueError):
        has_holding = False

    sell_selected = bool(sell_true)
    buy_selected = bool(buy_true)
    conflict = sell_selected and buy_selected
    if conflict:
        selected_side = "SELL" if has_holding else "BUY"
    elif sell_selected:
        selected_side = "SELL"
    elif buy_selected:
        selected_side = "BUY"
    else:
        selected_side = None
    return SignalSideSelection(selected_side, conflict)
