"""Only today's current forming higher bar may be composed from M1."""

from datetime import datetime, timedelta

from candle_timeframe_aggregation import (
    SEOUL_TIMEZONE, aggregate_minute_candles,
    completed_timeframe_candles, read_canonical_bar_minutes,
)


def _minutes(count, *, start=datetime(2026, 8, 10, 9, 0, tzinfo=SEOUL_TIMEZONE)):
    return [
        {"time": (start + timedelta(minutes=index)).isoformat(),
         "open": 100 + index, "high": 103 + index,
         "low": 98 + index, "close": 101 + index, "volume": 10}
        for index in range(count)
    ]


def test_applied_bar_ignores_ui_and_pending_candidates():
    rules = {"bar": {"bar_minutes": 1},
             "indicator_follow_rule_pending": {"bar_minutes": 5}}
    assert read_canonical_bar_minutes(rules) == 1


def test_completed_m1_preserves_source_values():
    raw = _minutes(3)
    projected = completed_timeframe_candles(
        raw, {"bar": {"bar_minutes": 1}},
        now=datetime(2026, 8, 10, 9, 4, tzinfo=SEOUL_TIMEZONE),
    )
    assert len(projected) == 3
    assert [row["close"] for row in projected] == [row["close"] for row in raw]


def test_completed_higher_bars_are_never_derived_from_past_m1():
    raw = _minutes(240)
    now = datetime(2026, 8, 10, 13, 0, tzinfo=SEOUL_TIMEZONE)
    for interval in (3, 5, 10, 15, 30, 60, 120, 240):
        assert aggregate_minute_candles(raw, interval, now=now) == []
        assert completed_timeframe_candles(
            raw, {"bar": {"bar_minutes": interval}}, now=now,
        ) == []


def test_only_current_forming_bucket_is_composed():
    now = datetime(2026, 8, 10, 9, 7, tzinfo=SEOUL_TIMEZONE)
    projected = aggregate_minute_candles(_minutes(8), 5, now=now)
    assert len(projected) == 1
    assert projected[0]["bar_time"] == "2026-08-10T09:05:00+09:00"
    assert projected[0]["is_complete"] is False
    assert projected[0]["close"] == 108


def test_old_incomplete_bucket_is_not_current_forming():
    raw = _minutes(2)
    now = datetime(2026, 8, 10, 9, 10, tzinfo=SEOUL_TIMEZONE)
    assert aggregate_minute_candles(raw, 5, now=now) == []
