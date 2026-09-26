"""Indicator-Follow Validation hidden-history contract."""

from __future__ import annotations

from typing import Any, Mapping

from engines.indicator_engine import DEFAULT_INDICATOR_HISTORY_TARGET_BARS
from indicator_follow_signal_validation_visualization import (
    required_validation_warmup_bars,
)


def required_validation_history_context_bars(
    rules: Mapping[str, Any],
) -> int:
    """Return hidden history needed for Kiwoom-parity indicator state."""
    return max(
        DEFAULT_INDICATOR_HISTORY_TARGET_BARS,
        required_validation_warmup_bars(rules),
    )
