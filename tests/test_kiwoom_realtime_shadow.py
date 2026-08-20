from __future__ import annotations

from datetime import datetime
import unittest

from candle_timeframe_aggregation import SEOUL_TIMEZONE
from kiwoom_realtime_shadow import (
    MATCH,
    MISMATCH,
    NO_CANONICAL_BAR,
    PARTIAL_VOLUME_UNVERIFIED,
    RealtimeShadowBar,
    RealtimeShadowBarBuilder,
    compare_shadow_bar_to_canonical,
    normalize_realtime_shadow_tick,
)


def _tick(time_text: str, price: object, volume: object):
    return normalize_realtime_shadow_tick(
        stock_code="005930",
        real_type="주식체결",
        execution_time_raw=time_text,
        current_price_raw=price,
        cumulative_volume_raw=volume,
        connection_epoch=7,
        login_session_id="SESSION-7",
        received_at=datetime(2026, 8, 20, 10, 0, tzinfo=SEOUL_TIMEZONE),
    )


def _bar(*, close: int = 103, volume=40, volume_complete: bool = True):
    return RealtimeShadowBar(
        stock_code="005930",
        timeframe_minutes=1,
        trade_date="2026-08-20",
        bar_time="2026-08-20T10:15:00+09:00",
        open=100,
        high=105,
        low=98,
        close=close,
        volume=volume,
        volume_complete=volume_complete,
        first_tick_time="2026-08-20T10:15:01+09:00",
        last_tick_time="2026-08-20T10:15:59+09:00",
        tick_count=4,
        connection_epoch=7,
        login_session_id="SESSION-7",
    )


class RealtimeShadowBarTests(unittest.TestCase):
    def test_tick_normalizes_official_fields_and_rejects_bad_time_or_price(self) -> None:
        tick = _tick("101501", "-1,234", "-9,876")
        self.assertIsNotNone(tick)
        self.assertEqual(1234, tick.current_price)
        self.assertEqual(9876, tick.cumulative_volume)
        self.assertEqual("2026-08-20 10:15", tick.minute_key)
        self.assertIsNone(_tick("1015", "100", "1"))
        self.assertIsNone(_tick("101501", "bad", "1"))

    def test_same_minute_ohlc_and_rollover_finalize_exactly_once(self) -> None:
        builder = RealtimeShadowBarBuilder()
        completed = []
        for time_text, price, volume in (
            ("101501", 100, 1000),
            ("101510", 105, 1010),
            ("101520", 98, 1020),
            ("101559", 103, 1030),
            ("101600", 104, 1040),
        ):
            status, bar = builder.accept_tick(_tick(time_text, price, volume))
            if bar is not None:
                completed.append((status, bar))

        self.assertEqual(1, len(completed))
        bar = completed[0][1]
        self.assertEqual((100, 105, 98, 103), (bar.open, bar.high, bar.low, bar.close))
        self.assertEqual(4, bar.tick_count)
        self.assertFalse(bar.volume_complete)
        self.assertIsNone(bar.volume)

    def test_consecutive_minute_volume_uses_previous_final_cumulative(self) -> None:
        builder = RealtimeShadowBarBuilder()
        for values in (("101500", 100, 100), ("101559", 101, 110), ("101600", 102, 120)):
            builder.accept_tick(_tick(*values))
        builder.accept_tick(_tick("101659", 103, 150))
        _status, completed = builder.accept_tick(_tick("101700", 104, 160))

        self.assertIsNotNone(completed)
        self.assertTrue(completed.volume_complete)
        self.assertEqual(40, completed.volume)

    def test_reset_gap_and_malformed_volume_are_conservative(self) -> None:
        for next_tick in (
            _tick("101600", 102, 90),
            _tick("101800", 102, 150),
            _tick("101600", 102, "bad"),
        ):
            builder = RealtimeShadowBarBuilder()
            builder.accept_tick(_tick("101500", 100, 100))
            builder.accept_tick(_tick("101559", 101, 110))
            builder.accept_tick(next_tick)
            next_minute = datetime.fromisoformat(next_tick.market_datetime).minute + 1
            rollover = f"10{next_minute:02d}00"
            _status, completed = builder.accept_tick(_tick(rollover, 103, 170))
            self.assertIsNotNone(completed)
            self.assertFalse(completed.volume_complete)
            self.assertIsNone(completed.volume)

    def test_out_of_order_tick_does_not_mutate_newer_bar(self) -> None:
        builder = RealtimeShadowBarBuilder()
        builder.accept_tick(_tick("101500", 100, 100))
        builder.accept_tick(_tick("101600", 110, 120))
        status, completed = builder.accept_tick(_tick("101559", 1, 1))
        self.assertEqual("OUT_OF_ORDER", status)
        self.assertIsNone(completed)
        _status, current = builder.accept_tick(_tick("101700", 111, 130))
        self.assertEqual(110, current.open)


class RealtimeShadowComparisonTests(unittest.TestCase):
    def canonical(self, *, close=103, volume=40):
        return [{
            "time": "20260820101500",
            "open": 100,
            "high": 105,
            "low": 98,
            "close": close,
            "volume": volume,
        }]

    def test_match_mismatch_partial_and_absent(self) -> None:
        matched = compare_shadow_bar_to_canonical(
            _bar(), self.canonical(), canonical_content_hash="hash-A"
        )
        mismatch = compare_shadow_bar_to_canonical(
            _bar(), self.canonical(close=999), canonical_content_hash="hash-B"
        )
        partial = compare_shadow_bar_to_canonical(
            _bar(volume=None, volume_complete=False),
            self.canonical(),
            canonical_content_hash="hash-C",
        )
        absent = compare_shadow_bar_to_canonical(
            _bar(), [], canonical_content_hash=""
        )

        self.assertEqual(MATCH, matched.status)
        self.assertEqual(MISMATCH, mismatch.status)
        self.assertEqual(PARTIAL_VOLUME_UNVERIFIED, partial.status)
        self.assertFalse(partial.volume_compared)
        self.assertEqual(NO_CANONICAL_BAR, absent.status)

    def test_timezone_formatting_difference_matches_same_market_minute(self) -> None:
        result = compare_shadow_bar_to_canonical(
            _bar(),
            [{
                "bar_time": "2026-08-20T01:15:00+00:00",
                "open": 100,
                "high": 105,
                "low": 98,
                "close": 103,
                "volume": 40,
            }],
            canonical_content_hash="hash",
        )
        self.assertEqual(MATCH, result.status)

if __name__ == "__main__":
    unittest.main()
