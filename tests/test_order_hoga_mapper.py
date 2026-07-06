# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import unittest

from order_hoga_mapper import map_order_hoga_preview


class OrderHogaMapperPreviewTest(unittest.TestCase):
    def test_maps_market_hoga(self) -> None:
        result = map_order_hoga_preview({"order_intent": {"hoga": "\uc2dc\uc7a5\uac00"}})

        self.assertTrue(result["ok"])
        self.assertEqual("MARKET", result["hoga"])
        self.assertEqual("order_intent", result["source"])
        self.assertFalse(result["unresolved"])
        self.assertEqual([], result["warnings"])

    def test_maps_limit_hoga(self) -> None:
        result = map_order_hoga_preview({"order_intent": {"hoga": "\uc9c0\uc815\uac00"}})

        self.assertTrue(result["ok"])
        self.assertEqual("LIMIT", result["hoga"])
        self.assertFalse(result["unresolved"])
        self.assertEqual([], result["warnings"])

    def test_maps_current_price_hoga_to_limit(self) -> None:
        result = map_order_hoga_preview({"order_intent": {"hoga": "\ud604\uc7ac\uac00"}})

        self.assertTrue(result["ok"])
        self.assertEqual("LIMIT", result["hoga"])
        self.assertFalse(result["unresolved"])
        self.assertEqual([], result["warnings"])

    def test_unresolved_hoga_stays_unresolved(self) -> None:
        result = map_order_hoga_preview({"order_intent": {"hoga": "\ubbf8\ud655\uc815"}})

        self.assertTrue(result["ok"])
        self.assertIsNone(result["hoga"])
        self.assertEqual("order_intent", result["source"])
        self.assertTrue(result["unresolved"])
        self.assertTrue(result["warnings"])

    def test_input_order_is_not_mutated(self) -> None:
        order = {
            "id": "ORDER-1",
            "order_intent": {
                "hoga": "\uc2dc\uc7a5\uac00",
                "metadata": {"nested": ["kept"]},
            },
        }
        original = deepcopy(order)

        map_order_hoga_preview(order)

        self.assertEqual(original, order)

    def test_kiwoom_like_numeric_code_is_not_mapped(self) -> None:
        result = map_order_hoga_preview({"order_intent": {"hoga": "03"}})

        self.assertTrue(result["ok"])
        self.assertIsNone(result["hoga"])
        self.assertTrue(result["unresolved"])
        self.assertTrue(result["warnings"])


if __name__ == "__main__":
    unittest.main()
