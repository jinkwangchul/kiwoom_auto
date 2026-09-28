from __future__ import annotations

import unittest

from stock_code_contract import (
    canonical_stock_code_from_market_data_identity,
    is_market_data_stock_code,
    market_data_identity_for_nxt_availability,
    market_source_for_identity,
)


class StockCodeMarketDataContractTest(unittest.TestCase):
    def test_bare_and_nxt_identities_are_supported(self) -> None:
        self.assertEqual(
            "005930",
            canonical_stock_code_from_market_data_identity("005930"),
        )
        self.assertEqual(
            "005930",
            canonical_stock_code_from_market_data_identity("005930_NX"),
        )
        self.assertTrue(is_market_data_stock_code("005930"))
        self.assertTrue(is_market_data_stock_code("005930_NX"))
        self.assertEqual("KRX", market_source_for_identity("005930"))
        self.assertEqual("NXT", market_source_for_identity("005930_NX"))

    def test_integrated_identity_is_not_supported(self) -> None:
        self.assertEqual(
            "",
            canonical_stock_code_from_market_data_identity("005930_AL"),
        )
        self.assertFalse(is_market_data_stock_code("005930_AL"))
        self.assertEqual("UNKNOWN", market_source_for_identity("005930_AL"))

    def test_nxt_eligibility_never_creates_integrated_identity(self) -> None:
        for availability in (True, False, None):
            with self.subTest(nxt_available=availability):
                self.assertEqual(
                    "005930",
                    market_data_identity_for_nxt_availability(
                        "005930",
                        availability,
                    ),
                )
        self.assertEqual(
            "",
            market_data_identity_for_nxt_availability("00088K", True),
        )


if __name__ == "__main__":
    unittest.main()
