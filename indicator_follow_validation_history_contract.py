"""Indicator-Follow Validation hidden-history contract."""

from __future__ import annotations

from typing import Any, Mapping

from engines.indicator_engine import indicator_history_target_bars
from indicator_follow_signal_validation_visualization import (
    required_validation_warmup_bars,
)
from indicator_follow_validation_timeframe import validation_timeframe_from_rules


def required_validation_history_context_bars(
    rules: Mapping[str, Any],
) -> int:
    """Return hidden history needed for Kiwoom-parity indicator state."""
    timeframe = validation_timeframe_from_rules(rules)
    return max(
        indicator_history_target_bars(timeframe),
        required_validation_warmup_bars(rules),
    )
