# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import unittest

from order_type_mapper import map_order_type_preview


class OrderTypeMapperPreviewTest(unittest.TestCase):
    def test_maps_buy_order_type(self) -> None:
        result = map_order_type_preview({"order_intent": {"side": "BUY"}})

        self.assertTrue(result["ok"])
        self.assertEqual("BUY", result["order_type"])
        self.assertEqual("order_intent", result["source"])
        self.assertFalse(result["unresolved"])
        self.assertEqual([], result["warnings"])

    def test_maps_sell_order_type(self) -> None:
        result = map_order_type_preview({"order_intent": {"side": "SELL"}})

        self.assertTrue(result["ok"])
        self.assertEqual("SELL", result["order_type"])
        self.assertEqual("order_intent", result["source"])
        self.assertFalse(result["unresolved"])
        self.assertEqual([], result["warnings"])

    def test_maps_korean_buy_order_type(self) -> None:
        result = map_order_type_preview({"order_intent": {"side": "\ub9e4\uc218"}})

        self.assertTrue(result["ok"])
        self.assertEqual("BUY", result["order_type"])
        self.assertFalse(result["unresolved"])
        self.assertEqual([], result["warnings"])

    def test_maps_korean_sell_order_type(self) -> None:
        result = map_order_type_preview({"order_intent": {"side": "\ub9e4\ub3c4"}})

        self.assertTrue(result["ok"])
        self.assertEqual("SELL", result["order_type"])
        self.assertFalse(result["unresolved"])
        self.assertEqual([], result["warnings"])

    def test_unresolved_order_type_stays_unresolved(self) -> None:
        result = map_order_type_preview({"order_intent": {"side": "\ubbf8\ud655\uc815"}})

        self.assertTrue(result["ok"])
        self.assertIsNone(result["order_type"])
        self.assertEqual("order_intent", result["source"])
        self.assertTrue(result["unresolved"])
        self.assertTrue(result["warnings"])

    def test_input_order_is_not_mutated(self) -> None:
        order = {
            "id": "ORDER-1",
            "order_intent": {
                "side": "BUY",
                "metadata": {"nested": ["kept"]},
            },
        }
        original = deepcopy(order)

        map_order_type_preview(order)

        self.assertEqual(original, order)

    def test_kiwoom_like_numeric_code_is_not_mapped(self) -> None:
        result = map_order_type_preview({"order_intent": {"side": "1"}})

        self.assertTrue(result["ok"])
        self.assertIsNone(result["order_type"])
        self.assertTrue(result["unresolved"])
        self.assertTrue(result["warnings"])


if __name__ == "__main__":
    unittest.main()
