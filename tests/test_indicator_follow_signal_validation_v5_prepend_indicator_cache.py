"""Exact V5 history-prepend indicator cache and fail-closed gates."""
from __future__ import annotations

from copy import deepcopy
from threading import Event
from unittest.mock import patch

import pytest

import gui_indicator_follow_signal_validation_v5_flow as flow
from indicator_follow_signal_validation_visualization import (
    FAMILY_MACD_SIGNAL, FAMILY_MOVING_AVERAGE, FAMILY_OCR_OSC, FAMILY_RSI,
    build_validation_indicator_cache,
)
from tests.test_indicator_follow_signal_validation_v5_baseline import candles, descriptor


RULES = {"bar_minutes": 3}


def _descriptors(*, large_period=False):
    return (
        descriptor("ma", FAMILY_MOVING_AVERAGE, "MA", "PRICE", "BUY", "MA5"),
        descriptor("rsi", FAMILY_RSI, "RSI", "LOWER", "BUY", "RSI",
                   {"period": 950 if large_period else 14}),
        descriptor("ocr", FAMILY_OCR_OSC, "OCR", "LOWER", "BUY", "OSC",
                   {"fast": 12, "slow": 26, "signal": 9}),
        descriptor("macd", FAMILY_MACD_SIGNAL, "MACD", "LOWER", "BUY", "MACD",
                   {"fast": 12, "slow": 26, "signal": 9}),
    )


def _case(*, count=1900, old_count=1400, descriptors=None):
    rows = candles(count)
    old = rows[-old_count:]
    descriptors = descriptors or _descriptors()
    old_cache = build_validation_indicator_cache(old, RULES, descriptors)
    old_identity = ("M3", old_count, "old", "005930", "KRX", old_count,
                    old[0]["time"], old[-1]["time"], "old-hash")
    new_identity = ("M3", count, "new", "005930", "KRX", count,
                    rows[0]["time"], rows[-1]["time"], "new-hash")
    tail = ("rules-hash", descriptors, ())
    previous = ((old_identity, *tail), old_cache, tuple(deepcopy(old)))
    return rows, old, descriptors, previous, (new_identity, *tail)


def _extend(case, *, rows=None, source=None, previous=None, key=None, rules=None,
            descriptors=None, event=None):
    original_rows, old, original_descriptors, prior, original_key = case
    return flow._extend_prepend_indicator_cache(
        prior if previous is None else previous,
        original_rows if rows is None else rows,
        old if source is None else source,
        original_key if key is None else key,
        RULES if rules is None else rules,
        original_descriptors if descriptors is None else descriptors,
        Event() if event is None else event,
    )


def test_auto_prepend_reuses_rsi_ocr_macd_and_matches_full_series_exactly():
    case = _case()
    rows, _old, descriptors, _previous, _key = case
    full = build_validation_indicator_cache(rows, RULES, descriptors)
    with patch.object(flow, "build_validation_indicator_cache",
                      wraps=build_validation_indicator_cache) as builder:
        result = _extend(case)
    assert result == full
    assert [len(call.args[0]) for call in builder.call_args_list] == [1400, 1900]
    assert {channel for identity, channel, _values in result.series
            if identity in {"rsi", "ocr", "macd"}} == {"RSI", "OSC", "MACD"}


@pytest.mark.parametrize("change", [
    "cold", "source", "overlap", "market", "rules", "descriptors", "cancel",
])
def test_prepend_reuse_rejects_changed_authority(change):
    case = _case()
    rows, old, descriptors, previous, key = case
    kwargs = {}
    if change == "cold":
        kwargs["previous"] = None  # passed explicitly below
    elif change == "source":
        source = deepcopy(old)
        source[20]["volume"] += 1
        kwargs["source"] = source
    elif change == "overlap":
        changed = deepcopy(rows)
        changed[-20]["close"] += 1
        kwargs["rows"] = changed
    elif change == "market":
        identity = list(key[0])
        identity[4] = "NXT"
        kwargs["key"] = (tuple(identity), *key[1:])
    elif change == "rules":
        kwargs["key"] = (key[0], "different-rules", *key[2:])
    elif change == "descriptors":
        kwargs["descriptors"] = _descriptors(large_period=True)
        kwargs["key"] = (key[0], key[1], kwargs["descriptors"], key[3])
    elif change == "cancel":
        event = Event()
        event.set()
        kwargs["event"] = event
    with patch.object(flow, "build_validation_indicator_cache") as builder:
        if change == "cold":
            assert flow._extend_prepend_indicator_cache(
                None, rows, old, key, RULES, descriptors, Event()) is None
        else:
            assert _extend(case, **kwargs) is None
        builder.assert_not_called()


def test_prepend_reuse_rejects_short_warmup_large_period_and_average_price():
    short = _case(count=1300, old_count=800)
    large = _case(descriptors=_descriptors(large_period=True))
    huge_macd = _case(descriptors=(
        descriptor("macd", FAMILY_MACD_SIGNAL, "MACD", "LOWER", "BUY", "MACD",
                   {"fast": 950, "slow": 26, "signal": 9}),
    ))
    average = _case(descriptors=(
        descriptor("avg", "PRICE_COMPARISON", "average", "PRICE", "BUY",
                   "AVG_PRICE", {"condition": {"target": "AVG_PRICE"}}),
        *_descriptors(),
    ))
    for case in (short, large, huge_macd, average):
        with patch.object(flow, "build_validation_indicator_cache") as builder:
            assert _extend(case) is None
            builder.assert_not_called()


def test_second_500_bar_prepend_uses_only_new_prefix_again():
    rows = candles(2400)
    first = rows[-1400:]
    middle = rows[-1900:]
    descriptors = _descriptors()
    first_cache = build_validation_indicator_cache(first, RULES, descriptors)
    identity = lambda value, tag: (
        "M3", len(value), tag, "005930", "KRX", len(value),
        value[0]["time"], value[-1]["time"], tag,
    )
    tail = ("rules-hash", descriptors, ())
    previous = ((identity(first, "first"), *tail), first_cache, tuple(deepcopy(first)))
    middle_key = (identity(middle, "middle"), *tail)
    middle_cache = flow._extend_prepend_indicator_cache(
        previous, middle, first, middle_key, RULES, descriptors, Event(),
    )
    assert middle_cache == build_validation_indicator_cache(middle, RULES, descriptors)
    previous = (middle_key, middle_cache, tuple(deepcopy(middle)))
    with patch.object(flow, "build_validation_indicator_cache",
                      wraps=build_validation_indicator_cache) as builder:
        final = flow._extend_prepend_indicator_cache(
            previous, rows, middle, (identity(rows, "final"), *tail),
            RULES, descriptors, Event(),
        )
    assert final == build_validation_indicator_cache(rows, RULES, descriptors)
    assert [len(call.args[0]) for call in builder.call_args_list] == [1400, 2400]


def test_cancel_during_prefix_and_invalid_old_cache_publish_no_reuse():
    case = _case()
    rows, old, descriptors, previous, key = case
    event = Event()

    def cancel_after_prefix(*args, **kwargs):
        result = build_validation_indicator_cache(*args, **kwargs)
        event.set()
        return result

    with patch.object(flow, "build_validation_indicator_cache", side_effect=cancel_after_prefix):
        assert _extend(case, event=event) is None
    bad_cache = flow.ValidationIndicatorSeriesCache(
        previous[1].candle_count, previous[1].series[:-1],
    )
    with patch.object(flow, "build_validation_indicator_cache",
                      wraps=build_validation_indicator_cache):
        assert flow._extend_prepend_indicator_cache(
            (previous[0], bad_cache, previous[2]), rows, old, key,
            RULES, descriptors, Event(),
        ) is None


def test_daily_history_target_uses_600_bar_boundary_exactly():
    rules = {"validation_timeframe": "D1"}
    rows = candles(1700)
    old = rows[500:]
    descriptors = _descriptors()
    old_cache = build_validation_indicator_cache(old, rules, descriptors)
    identity = lambda value, tag: (
        "D1", len(value), tag, "005930", "KRX", len(value),
        value[0]["time"], value[-1]["time"], tag,
    )
    tail = ("daily-rules", descriptors, ())
    previous = ((identity(old, "old"), *tail), old_cache, tuple(deepcopy(old)))
    with patch.object(flow, "build_validation_indicator_cache",
                      wraps=build_validation_indicator_cache) as builder:
        result = flow._extend_prepend_indicator_cache(
            previous, rows, old, (identity(rows, "new"), *tail),
            rules, descriptors, Event(),
        )
    assert result == build_validation_indicator_cache(rows, rules, descriptors)
    assert [len(call.args[0]) for call in builder.call_args_list] == [1100, 1700]


def test_calculation_error_cannot_return_a_cache():
    case = _case()
    with patch.object(flow, "build_validation_indicator_cache",
                      side_effect=RuntimeError("calculation failed")):
        with pytest.raises(RuntimeError, match="calculation failed"):
            _extend(case)
