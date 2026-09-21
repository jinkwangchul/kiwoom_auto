from __future__ import annotations

import unittest

from candle_timeframe_aggregation import (
    project_candle_supply,
    validate_market_bar_projection_request,
)


class CandleProjectionContractTests(unittest.TestCase):
    def test_valid_request_preserves_supported_projection_and_positive_warmup(self) -> None:
        self.assertEqual(
            {
                "projection": "FORMING_BASE_BAR",
                "warmup_bars": 35,
            },
            validate_market_bar_projection_request(
                {"projection": "forming_base_bar", "warmup_bars": 35},
                require_warmup=True,
            ),
        )

    def test_declared_request_rejects_missing_or_unsupported_projection(self) -> None:
        for request in (
            None,
            {},
            {"projection": "UNKNOWN", "warmup_bars": 3},
        ):
            with self.subTest(request=request), self.assertRaises(ValueError):
                validate_market_bar_projection_request(
                    request,
                    require_warmup=True,
                )

    def test_declared_request_rejects_missing_or_non_positive_integer_warmup(self) -> None:
        for warmup in (None, True, 0, -1, 3.5, "3"):
            request = {"projection": "FORMING_BASE_BAR"}
            if warmup is not None:
                request["warmup_bars"] = warmup
            with self.subTest(warmup=warmup), self.assertRaises(ValueError):
                validate_market_bar_projection_request(
                    request,
                    require_warmup=True,
                )

    def test_read_projection_accepts_missing_warmup_without_cache_requirement(self) -> None:
        request = validate_market_bar_projection_request(
            {"projection": "COMPLETED_TIMEFRAME"},
            require_warmup=False,
        )
        self.assertEqual(
            {"projection": "COMPLETED_TIMEFRAME"},
            request,
        )

        result = project_candle_supply(
            [],
            {"bar": {"bar_minutes": 5}},
            request,
        )
        self.assertTrue(result["available"])
        self.assertEqual(0, result["required_minute_candles"])

    def test_supply_fails_closed_for_invalid_optional_warmup(self) -> None:
        for warmup in (True, 0, -1, 3.5, "3"):
            with self.subTest(warmup=warmup):
                result = project_candle_supply(
                    [],
                    {"bar": {"bar_minutes": 1}},
                    {
                        "projection": "COMPLETED_TIMEFRAME",
                        "warmup_bars": warmup,
                    },
                )
                self.assertFalse(result["available"])
                self.assertEqual("PROJECTION_INVALID", result["availability_state"])


if __name__ == "__main__":
    unittest.main()
