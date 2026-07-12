from __future__ import annotations

from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unittest


def _load_routine_engine_module():
    project_root = Path(__file__).resolve().parents[1]
    engine_path = next((project_root / "routines").glob("*/routine_macd_engine.py"))
    spec = spec_from_file_location("routine_macd_engine_for_ocr_filter_test", engine_path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class RoutineMacdOcrFilterTest(unittest.TestCase):
    def setUp(self):
        self.engine = _load_routine_engine_module()
        self.default_config = deepcopy(self.engine.DEFAULT_INDICATOR_FOLLOW_CONFIG)

    def _buy_cfg(self, filter_cfg=None):
        buy_cfg = {"groups": []}
        if filter_cfg is not None:
            buy_cfg["filters"] = {"ocr": deepcopy(filter_cfg)}
        return buy_cfg

    def _reason(self, detail):
        if not detail:
            return None
        for token in detail.split():
            if token.startswith("reason="):
                return token.split("=", 1)[1]
        return None

    def _config(self, ocr_filter=None, pending_filter=None):
        config = deepcopy(self.default_config)
        config["buy"]["delay_bar"] = 0
        config["buy"]["groups"] = [
            {
                "enabled": True,
                "name": "buy_main",
                "conditions": [{"enabled": True, "target": "CLOSE", "operator": ">=", "value": 0}],
            }
        ]
        if ocr_filter is not None:
            config["buy"]["filters"] = {"ocr": deepcopy(ocr_filter)}
        else:
            config["buy"].pop("filters", None)
        if pending_filter is not None:
            config["indicator_follow_rule_pending"] = {
                "candidates": {
                    "filters": {
                        "ocr": {
                            "path": "buy.filters.ocr",
                            "value": deepcopy(pending_filter),
                        }
                    }
                }
            }
        config["sell"] = {"delay_bar": 0, "signals": {"macd_sell": {"enabled": False, "groups": []}}}
        return config

    def _candles(self):
        return [{"close": close, "volume": 100} for close in [10, 11, 12, 13, 14]]

    def _signal(self, ocr_filter=None, pending_filter=None):
        return self.engine.evaluate_indicator_follow_routine(
            self._candles(),
            self._config(ocr_filter=ocr_filter, pending_filter=pending_filter),
            {},
        )

    def _ocr_detail(self, signal):
        return next((detail for detail in signal.details if "filter_type=OCR" in detail), "")

    def test_no_ocr_filter_keeps_buy_result(self):
        signal = self._signal()

        self.assertEqual("BUY", signal.signal)
        self.assertEqual("", self._ocr_detail(signal))

    def test_disabled_ocr_filter_passes(self):
        signal = self._signal({"enabled": False, "conditions": [{"target": "OSC", "operator": ">", "value": 999}]})

        self.assertEqual("BUY", signal.signal)
        self.assertIn("enabled=False", self._ocr_detail(signal))
        self.assertIn("reason=disabled", self._ocr_detail(signal))

    def test_turn_up_and_turn_down_conditions(self):
        up_filter = {"enabled": True, "conditions": [{"target": "OSC", "operator": "TURN_UP"}]}
        down_filter = {"enabled": True, "conditions": [{"target": "OSC", "operator": "TURN_DOWN"}]}

        up_passed, up_detail = self.engine._evaluate_buy_ocr_filter(
            self.default_config, self._buy_cfg(up_filter), {"OSC": [3.0, 2.0, 3.0]}, 2
        )
        down_passed, down_detail = self.engine._evaluate_buy_ocr_filter(
            self.default_config, self._buy_cfg(down_filter), {"OSC": [1.0, 2.0, 1.0]}, 2
        )

        self.assertTrue(up_passed, up_detail)
        self.assertEqual("matched", self._reason(up_detail))
        self.assertTrue(down_passed, down_detail)
        self.assertEqual("matched", self._reason(down_detail))

    def test_threshold_above_and_below_conditions(self):
        gte_filter = {"enabled": True, "conditions": [{"target": "OSC", "operator": ">=", "value": 2.0}]}
        lte_filter = {"enabled": True, "conditions": [{"target": "OSC", "operator": "<=", "value": 2.0}]}

        gte_passed, gte_detail = self.engine._evaluate_buy_ocr_filter(
            self.default_config, self._buy_cfg(gte_filter), {"OSC": [1.0, 2.0]}, 1
        )
        lte_passed, lte_detail = self.engine._evaluate_buy_ocr_filter(
            self.default_config, self._buy_cfg(lte_filter), {"OSC": [3.0, 2.0]}, 1
        )

        self.assertTrue(gte_passed, gte_detail)
        self.assertTrue(lte_passed, lte_detail)

    def test_and_or_multiple_conditions(self):
        and_filter = {
            "enabled": True,
            "conditions_logic": "AND",
            "conditions": [
                {"target": "OSC", "operator": ">=", "value": 2.0},
                {"target": "OSC", "operator": "<=", "value": 2.0},
            ],
        }
        or_filter = {
            "enabled": True,
            "conditions_logic": "OR",
            "conditions": [
                {"target": "OSC", "operator": ">", "value": 99.0},
                {"target": "OSC", "operator": "<=", "value": 2.0},
            ],
        }

        and_passed, and_detail = self.engine._evaluate_buy_ocr_filter(
            self.default_config, self._buy_cfg(and_filter), {"OSC": [2.0]}, 0
        )
        or_passed, or_detail = self.engine._evaluate_buy_ocr_filter(
            self.default_config, self._buy_cfg(or_filter), {"OSC": [2.0]}, 0
        )

        self.assertTrue(and_passed, and_detail)
        self.assertIn("logic=AND", and_detail)
        self.assertTrue(or_passed, or_detail)
        self.assertIn("logic=OR", or_detail)

    def test_condition_not_matched_blocks_buy_signal(self):
        signal = self._signal({"enabled": True, "conditions": [{"target": "OSC", "operator": ">", "value": 999}]})

        self.assertIsNone(signal.signal)
        self.assertEqual("BUY OCR filter blocked", signal.reason)
        self.assertIn("reason=not_matched", self._ocr_detail(signal))

    def test_bad_config_and_insufficient_data_block_with_reason(self):
        cases = [
            ({"enabled": True, "conditions": []}, {}, 0, "missing_conditions"),
            ({"enabled": True, "conditions": ["bad"]}, {"OSC": [1.0]}, 0, "invalid_condition"),
            ({"enabled": True, "conditions": [{"target": "OSC", "operator": "!="}]}, {"OSC": [1.0]}, 0, "unsupported_operator"),
            ({"enabled": True, "conditions": [{"target": "UNKNOWN", "operator": ">=", "value": 1}]}, {"OSC": [1.0]}, 0, "unsupported_target"),
            ({"enabled": True, "conditions": [{"target": "OSC", "operator": ">=", "value": "bad"}]}, {"OSC": [1.0]}, 0, "invalid_value"),
            ({"enabled": True, "conditions": [{"target": "OSC", "operator": "TURN_UP"}]}, {"OSC": [1.0, None, 2.0]}, 2, "insufficient_data"),
        ]

        for filter_cfg, series_map, index, reason in cases:
            with self.subTest(reason=reason):
                passed, detail = self.engine._evaluate_buy_ocr_filter(
                    self.default_config, self._buy_cfg(filter_cfg), series_map, index
                )
                self.assertFalse(passed)
                self.assertEqual(reason, self._reason(detail))

    def test_pending_ocr_candidate_is_not_used_for_execution(self):
        pending = {"enabled": True, "conditions": [{"target": "OSC", "operator": ">", "value": 999}]}

        signal = self._signal(ocr_filter=None, pending_filter=pending)

        self.assertEqual("BUY", signal.signal)
        self.assertEqual("", self._ocr_detail(signal))

    def test_official_ocr_filter_is_used_when_pending_differs(self):
        official = {"enabled": True, "conditions": [{"target": "OSC", "operator": "<=", "value": 999}]}
        pending = {"enabled": True, "conditions": [{"target": "OSC", "operator": ">", "value": 999}]}

        signal = self._signal(ocr_filter=official, pending_filter=pending)

        self.assertEqual("BUY", signal.signal)
        self.assertIn("filter_type=OCR", self._ocr_detail(signal))


if __name__ == "__main__":
    unittest.main()
