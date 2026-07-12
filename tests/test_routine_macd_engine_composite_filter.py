from __future__ import annotations

from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest import mock
import unittest


def _load_routine_engine_module():
    project_root = Path(__file__).resolve().parents[1]
    engine_path = next((project_root / "routines").glob("*/routine_macd_engine.py"))
    spec = spec_from_file_location("routine_macd_engine_for_composite_filter_test", engine_path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class RoutineMacdCompositeFilterTest(unittest.TestCase):
    def setUp(self):
        self.engine = _load_routine_engine_module()

    def _candles(self):
        return [{"close": close, "volume": 100} for close in [10, 11, 12, 13, 14]]

    def _config(self, *, filters=None, pending_composite=None):
        config = deepcopy(self.engine.DEFAULT_INDICATOR_FOLLOW_CONFIG)
        config["buy"]["delay_bar"] = 0
        config["buy"]["groups"] = [
            {
                "enabled": True,
                "name": "buy_main",
                "conditions": [{"enabled": True, "target": "CLOSE", "operator": ">=", "value": 0}],
            }
        ]
        if filters is None:
            config["buy"].pop("filters", None)
        else:
            config["buy"]["filters"] = deepcopy(filters)
        if pending_composite is not None:
            config["indicator_follow_rule_pending"] = {
                "candidates": {
                    "filters": {
                        "composite": {
                            "path": "buy.filters.composite",
                            "value": deepcopy(pending_composite),
                        }
                    }
                }
            }
        config["sell"] = {"delay_bar": 0, "signals": {"macd_sell": {"enabled": False, "groups": []}}}
        return config

    def _base_filter_configs(self, *, enabled=True):
        return {
            "rsi": {"enabled": enabled},
            "moving_average": {"enabled": enabled},
            "price_compare": {"enabled": enabled},
            "bollinger": {"enabled": enabled},
            "ocr": {"enabled": enabled},
        }

    def _composite(self, *, logic="OR", groups=None, enabled=True, policy=None):
        value = {
            "enabled": enabled,
            "logic": logic,
            "groups": groups if groups is not None else [
                {"enabled": True, "logic": "AND", "filters": ["rsi"]}
            ],
        }
        if policy is not None:
            value["include_unreferenced_active_filters"] = policy
        return value

    def _filter_results(self, *, configured=True, enabled=True, passed=True):
        return {
            name: {
                "passed": passed,
                "detail": f"{name}_detail",
                "configured": configured,
                "enabled": enabled,
            }
            for name in self.engine.BUY_FILTER_ORDER
        }

    def _detail_reason(self, detail):
        if not detail:
            return None
        for token in detail.split():
            if token.startswith("reason="):
                return token.split("=", 1)[1]
        return None

    def _composite_detail(self, signal):
        return next((detail for detail in signal.details if "filter_type=COMPOSITE" in detail), "")

    def test_composite_absent_keeps_existing_sequential_buy_result(self):
        config = self._config(filters={"rsi": {"enabled": True}})

        with (
            mock.patch.object(self.engine, "_evaluate_buy_rsi_filter", return_value=(False, "rsi_detail")) as rsi,
            mock.patch.object(self.engine, "_evaluate_buy_moving_average_filter", return_value=(True, "ma_detail")) as ma,
        ):
            signal = self.engine.evaluate_indicator_follow_routine(self._candles(), config, {})

        self.assertIsNone(signal.signal)
        self.assertEqual("BUY RSI filter blocked", signal.reason)
        rsi.assert_called_once()
        ma.assert_not_called()

    def test_composite_disabled_keeps_existing_sequential_result(self):
        config = self._config(filters={"rsi": {"enabled": True}, "composite": self._composite(enabled=False)})

        with (
            mock.patch.object(self.engine, "_evaluate_buy_rsi_filter", return_value=(False, "rsi_detail")) as rsi,
            mock.patch.object(self.engine, "_evaluate_buy_moving_average_filter", return_value=(True, "ma_detail")) as ma,
        ):
            signal = self.engine.evaluate_indicator_follow_routine(self._candles(), config, {})

        self.assertIsNone(signal.signal)
        self.assertEqual("BUY RSI filter blocked", signal.reason)
        rsi.assert_called_once()
        ma.assert_not_called()

    def test_or_group_one_pass_allows_buy_and_evaluates_each_filter_once(self):
        filters = self._base_filter_configs()
        filters["composite"] = self._composite(groups=[
            {"enabled": True, "logic": "AND", "filters": ["rsi"]},
            {"enabled": True, "logic": "AND", "filters": ["bollinger"]},
        ])
        returns = {
            "rsi": (False, "rsi_detail"),
            "moving_average": (True, "ma_detail"),
            "price_compare": (True, "pc_detail"),
            "bollinger": (True, "bollinger_detail"),
            "ocr": (True, "ocr_detail"),
        }

        with (
            mock.patch.object(self.engine, "_evaluate_buy_rsi_filter", return_value=returns["rsi"]) as rsi,
            mock.patch.object(self.engine, "_evaluate_buy_moving_average_filter", return_value=returns["moving_average"]) as ma,
            mock.patch.object(self.engine, "_evaluate_buy_price_compare_filter", return_value=returns["price_compare"]) as pc,
            mock.patch.object(self.engine, "_evaluate_buy_bollinger_filter", return_value=returns["bollinger"]) as bol,
            mock.patch.object(self.engine, "_evaluate_buy_ocr_filter", return_value=returns["ocr"]) as ocr,
        ):
            signal = self.engine.evaluate_indicator_follow_routine(self._candles(), self._config(filters=filters), {})

        self.assertEqual("BUY", signal.signal)
        self.assertIn("reason=matched", self._composite_detail(signal))
        for patched in (rsi, ma, pc, bol, ocr):
            patched.assert_called_once()

    def test_and_group_one_failure_blocks_group(self):
        results = self._filter_results()
        results["moving_average"]["passed"] = False
        passed, detail = self.engine._evaluate_buy_composite_filter(
            self._composite(logic="OR", groups=[{"enabled": True, "logic": "AND", "filters": ["rsi", "moving_average"]}]),
            results,
        )

        self.assertFalse(passed)
        self.assertEqual("not_matched", self._detail_reason(detail))

    def test_all_groups_fail_blocks_buy(self):
        results = self._filter_results()
        results["rsi"]["passed"] = False
        results["ocr"]["passed"] = False
        passed, detail = self.engine._evaluate_buy_composite_filter(
            self._composite(logic="OR", groups=[
                {"enabled": True, "logic": "AND", "filters": ["rsi"]},
                {"enabled": True, "logic": "AND", "filters": ["ocr"]},
            ]),
            results,
        )

        self.assertFalse(passed)
        self.assertEqual("not_matched", self._detail_reason(detail))

    def test_top_level_and_requires_all_groups(self):
        results = self._filter_results()
        passed, detail = self.engine._evaluate_buy_composite_filter(
            self._composite(logic="AND", groups=[
                {"enabled": True, "logic": "AND", "filters": ["rsi"]},
                {"enabled": True, "logic": "AND", "filters": ["ocr"]},
            ]),
            results,
        )

        self.assertTrue(passed, detail)
        self.assertIn("logic=AND", detail)

    def test_unreferenced_active_filter_failure_blocks(self):
        results = self._filter_results()
        results["ocr"]["passed"] = False
        passed, detail = self.engine._evaluate_buy_composite_filter(
            self._composite(logic="OR", groups=[{"enabled": True, "logic": "AND", "filters": ["rsi"]}]),
            results,
        )

        self.assertFalse(passed)
        self.assertEqual("unreferenced_required_failed", self._detail_reason(detail))
        self.assertIn("unreferenced_required_filters=moving_average,price_compare,bollinger,ocr", detail)

    def test_unconfigured_unreferenced_filter_has_no_effect(self):
        results = self._filter_results(configured=False, enabled=False, passed=True)
        results["rsi"] = {"passed": True, "detail": "rsi", "configured": True, "enabled": True}
        passed, detail = self.engine._evaluate_buy_composite_filter(
            self._composite(logic="OR", groups=[{"enabled": True, "logic": "AND", "filters": ["rsi"]}]),
            results,
        )

        self.assertTrue(passed, detail)
        self.assertIn("unreferenced_required_filters=", detail)

    def test_unknown_filter_blocks(self):
        passed, detail = self.engine._evaluate_buy_composite_filter(
            self._composite(groups=[{"enabled": True, "logic": "AND", "filters": ["unknown"]}]),
            self._filter_results(),
        )

        self.assertFalse(passed)
        self.assertEqual("unknown_filter", self._detail_reason(detail))

    def test_composite_self_reference_blocks(self):
        passed, detail = self.engine._evaluate_buy_composite_filter(
            self._composite(groups=[{"enabled": True, "logic": "AND", "filters": ["composite"]}]),
            self._filter_results(),
        )

        self.assertFalse(passed)
        self.assertEqual("self_reference", self._detail_reason(detail))

    def test_duplicate_inside_same_group_blocks(self):
        passed, detail = self.engine._evaluate_buy_composite_filter(
            self._composite(groups=[{"enabled": True, "logic": "AND", "filters": ["rsi", "rsi"]}]),
            self._filter_results(),
        )

        self.assertFalse(passed)
        self.assertEqual("duplicate_filter_in_group", self._detail_reason(detail))

    def test_same_filter_reused_across_groups_is_allowed(self):
        results = self._filter_results()
        passed, detail = self.engine._evaluate_buy_composite_filter(
            self._composite(logic="AND", groups=[
                {"enabled": True, "logic": "AND", "filters": ["rsi"]},
                {"enabled": True, "logic": "OR", "filters": ["rsi"]},
            ]),
            results,
        )

        self.assertTrue(passed, detail)

    def test_disabled_filter_is_ignored_when_active_filter_exists(self):
        results = self._filter_results()
        results["rsi"]["enabled"] = False
        passed, detail = self.engine._evaluate_buy_composite_filter(
            self._composite(groups=[{"enabled": True, "logic": "AND", "filters": ["rsi", "ocr"]}]),
            results,
        )

        self.assertTrue(passed, detail)
        self.assertIn("rsi:disabled_ignored", detail)

    def test_disabled_only_group_blocks(self):
        results = self._filter_results()
        results["rsi"]["enabled"] = False
        passed, detail = self.engine._evaluate_buy_composite_filter(
            self._composite(groups=[{"enabled": True, "logic": "AND", "filters": ["rsi"]}]),
            results,
        )

        self.assertFalse(passed)
        self.assertEqual("group_has_no_active_filters", self._detail_reason(detail))

    def test_missing_result_map_entry_blocks(self):
        results = self._filter_results()
        results.pop("rsi")
        passed, detail = self.engine._evaluate_buy_composite_filter(
            self._composite(groups=[{"enabled": True, "logic": "AND", "filters": ["rsi"]}]),
            results,
        )

        self.assertFalse(passed)
        self.assertEqual("missing_filter_result", self._detail_reason(detail))

    def test_empty_groups_blocks(self):
        passed, detail = self.engine._evaluate_buy_composite_filter(
            self._composite(groups=[]),
            self._filter_results(),
        )

        self.assertFalse(passed)
        self.assertEqual("missing_groups", self._detail_reason(detail))

    def test_all_groups_disabled_blocks(self):
        passed, detail = self.engine._evaluate_buy_composite_filter(
            self._composite(groups=[{"enabled": False, "logic": "AND", "filters": ["rsi"]}]),
            self._filter_results(),
        )

        self.assertFalse(passed)
        self.assertEqual("all_groups_disabled", self._detail_reason(detail))

    def test_invalid_logic_blocks(self):
        passed, detail = self.engine._evaluate_buy_composite_filter(
            self._composite(logic="XOR"),
            self._filter_results(),
        )

        self.assertFalse(passed)
        self.assertEqual("invalid_logic", self._detail_reason(detail))

    def test_malformed_composite_config_blocks(self):
        config = self._config(filters={"composite": "not_a_dict"})

        signal = self.engine.evaluate_indicator_follow_routine(self._candles(), config, {})

        self.assertIsNone(signal.signal)
        self.assertEqual("BUY composite filter blocked", signal.reason)
        self.assertIn("reason=invalid_config", self._composite_detail(signal))

    def test_pending_composite_candidate_is_ignored(self):
        pending = self._composite(groups=[{"enabled": True, "logic": "AND", "filters": ["rsi"]}])
        config = self._config(filters=None, pending_composite=pending)

        signal = self.engine.evaluate_indicator_follow_routine(self._candles(), config, {})

        self.assertEqual("BUY", signal.signal)
        self.assertEqual("", self._composite_detail(signal))

    def test_existing_filter_regression_with_composite_disabled(self):
        config = self._config(filters={
            "composite": self._composite(enabled=False),
            "rsi": {"enabled": True},
            "moving_average": {"enabled": True},
            "price_compare": {"enabled": True},
            "bollinger": {"enabled": True},
            "ocr": {"enabled": True},
        })

        with (
            mock.patch.object(self.engine, "_evaluate_buy_rsi_filter", return_value=(True, "rsi_detail")) as rsi,
            mock.patch.object(self.engine, "_evaluate_buy_moving_average_filter", return_value=(True, "ma_detail")) as ma,
            mock.patch.object(self.engine, "_evaluate_buy_price_compare_filter", return_value=(True, "pc_detail")) as pc,
            mock.patch.object(self.engine, "_evaluate_buy_bollinger_filter", return_value=(True, "bol_detail")) as bol,
            mock.patch.object(self.engine, "_evaluate_buy_ocr_filter", return_value=(False, "ocr_detail")) as ocr,
        ):
            signal = self.engine.evaluate_indicator_follow_routine(self._candles(), config, {})

        self.assertIsNone(signal.signal)
        self.assertEqual("BUY OCR filter blocked", signal.reason)
        for patched in (rsi, ma, pc, bol, ocr):
            patched.assert_called_once()


if __name__ == "__main__":
    unittest.main()
