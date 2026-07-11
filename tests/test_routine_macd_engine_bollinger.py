# -*- coding: utf-8 -*-
"""Unit tests for the BUY Bollinger filter in routine_macd_engine.

Covers the required validations for finalizing the Bollinger BUY filter:
- enabled=false passes
- upper/lower band conditions pass and block
- invalid period / operator / value are blocked
- insufficient_data is blocked
- a pending (not-yet-applied) candidate has no effect
- evaluation is deterministic
- inputs are not mutated
- SELL evaluation is unaffected by the BUY Bollinger filter
- BUY filter evaluation order (RSI -> MA -> price_compare -> Bollinger) is kept
"""

from __future__ import annotations

from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest import mock
import unittest


def _load_routine_engine_module():
    project_root = Path(__file__).resolve().parents[1]
    engine_path = next((project_root / "routines").glob("*/routine_macd_engine.py"))
    spec = spec_from_file_location("routine_macd_engine_for_bollinger_test", engine_path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class RoutineMacdBollingerFilterTest(unittest.TestCase):
    def setUp(self):
        self.engine = _load_routine_engine_module()
        self.default_config = deepcopy(self.engine.DEFAULT_INDICATOR_FOLLOW_CONFIG)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _bollinger_buy_cfg(self, filter_cfg):
        return {"filters": {"bollinger": filter_cfg}}

    def _series(self, close, bollinger):
        return {"CLOSE": close, "BOLLINGER": bollinger}

    def _reason(self, detail):
        if not detail:
            return None
        for token in detail.split():
            if token.startswith("reason="):
                return token.split("=", 1)[1]
        return None

    # ------------------------------------------------------------------
    # enabled=false passes
    # ------------------------------------------------------------------
    def test_enabled_false_passes(self):
        buy_cfg = self._bollinger_buy_cfg({
            "enabled": False,
            "conditions": [{"enabled": True, "operator": ">=", "value": 0}],
        })
        series = self._series([100.0, 100.0], [95.0, 95.0])
        passed, detail = self.engine._evaluate_buy_bollinger_filter(
            self.default_config, buy_cfg, series, 1
        )
        self.assertTrue(passed)
        self.assertEqual(self._reason(detail), "disabled")

    # ------------------------------------------------------------------
    # upper band (above lower band) pass / block
    # ------------------------------------------------------------------
    def test_upper_band_condition_passes(self):
        buy_cfg = self._bollinger_buy_cfg({
            "enabled": True,
            "conditions": [{
                "enabled": True,
                "target": "CLOSE",
                "operator": ">=",
                "compare_target": "BOLLINGER",
                "value": 0.0,
            }],
        })
        series = self._series([100.0, 100.0], [95.0, 95.0])
        passed, detail = self.engine._evaluate_buy_bollinger_filter(
            self.default_config, buy_cfg, series, 1
        )
        self.assertTrue(passed)
        self.assertEqual(self._reason(detail), "matched")

    def test_upper_band_condition_blocks(self):
        buy_cfg = self._bollinger_buy_cfg({
            "enabled": True,
            "conditions": [{
                "enabled": True,
                "target": "CLOSE",
                "operator": ">=",
                "compare_target": "BOLLINGER",
                "value": 0.0,
            }],
        })
        series = self._series([100.0, 90.0], [95.0, 95.0])
        passed, detail = self.engine._evaluate_buy_bollinger_filter(
            self.default_config, buy_cfg, series, 1
        )
        self.assertFalse(passed)
        self.assertEqual(self._reason(detail), "not_matched")

    def test_upper_band_with_positive_offset_passes_and_blocks(self):
        buy_cfg = self._bollinger_buy_cfg({
            "enabled": True,
            "conditions": [{
                "enabled": True,
                "target": "CLOSE",
                "operator": ">=",
                "compare_target": "BOLLINGER",
                "value": 5.0,
            }],
        })
        series_pass = self._series([100.0, 100.0], [95.0, 95.0])
        passed, _ = self.engine._evaluate_buy_bollinger_filter(
            self.default_config, buy_cfg, series_pass, 1
        )
        self.assertTrue(passed)

        series_block = self._series([100.0, 99.0], [95.0, 95.0])
        passed, _ = self.engine._evaluate_buy_bollinger_filter(
            self.default_config, buy_cfg, series_block, 1
        )
        self.assertFalse(passed)

    # ------------------------------------------------------------------
    # lower band (below lower band) pass / block
    # ------------------------------------------------------------------
    def test_lower_band_condition_passes(self):
        buy_cfg = self._bollinger_buy_cfg({
            "enabled": True,
            "conditions": [{
                "enabled": True,
                "target": "CLOSE",
                "operator": "<=",
                "compare_target": "BOLLINGER",
                "value": 0.0,
            }],
        })
        series = self._series([100.0, 90.0], [95.0, 95.0])
        passed, detail = self.engine._evaluate_buy_bollinger_filter(
            self.default_config, buy_cfg, series, 1
        )
        self.assertTrue(passed)
        self.assertEqual(self._reason(detail), "matched")

    def test_lower_band_condition_blocks(self):
        buy_cfg = self._bollinger_buy_cfg({
            "enabled": True,
            "conditions": [{
                "enabled": True,
                "target": "CLOSE",
                "operator": "<=",
                "compare_target": "BOLLINGER",
                "value": 0.0,
            }],
        })
        series = self._series([100.0, 100.0], [95.0, 95.0])
        passed, detail = self.engine._evaluate_buy_bollinger_filter(
            self.default_config, buy_cfg, series, 1
        )
        self.assertFalse(passed)
        self.assertEqual(self._reason(detail), "not_matched")

    # ------------------------------------------------------------------
    # invalid period / operator / value are blocked
    # ------------------------------------------------------------------
    def test_invalid_period_blocked(self):
        buy_cfg = self._bollinger_buy_cfg({
            "enabled": True,
            "conditions": [{
                "enabled": True,
                "target": "CLOSE",
                "operator": ">=",
                "compare_target": "BOLLINGER",
                "period": "not_a_period",
                "value": 0.0,
            }],
        })
        series = self._series([100.0, 100.0], [95.0, 95.0])
        passed, detail = self.engine._evaluate_buy_bollinger_filter(
            self.default_config, buy_cfg, series, 1
        )
        self.assertFalse(passed)
        self.assertEqual(self._reason(detail), "invalid_period")

    def test_invalid_period_zero_blocked(self):
        buy_cfg = self._bollinger_buy_cfg({
            "enabled": True,
            "conditions": [{
                "enabled": True,
                "target": "CLOSE",
                "operator": ">=",
                "compare_target": "BOLLINGER",
                "period": 0,
                "value": 0.0,
            }],
        })
        series = self._series([100.0, 100.0], [95.0, 95.0])
        passed, detail = self.engine._evaluate_buy_bollinger_filter(
            self.default_config, buy_cfg, series, 1
        )
        self.assertFalse(passed)
        self.assertEqual(self._reason(detail), "invalid_period")

    def test_invalid_operator_blocked(self):
        buy_cfg = self._bollinger_buy_cfg({
            "enabled": True,
            "conditions": [{
                "enabled": True,
                "target": "CLOSE",
                "operator": "INVALID_OP",
                "compare_target": "BOLLINGER",
                "value": 0.0,
            }],
        })
        series = self._series([100.0, 100.0], [95.0, 95.0])
        passed, detail = self.engine._evaluate_buy_bollinger_filter(
            self.default_config, buy_cfg, series, 1
        )
        self.assertFalse(passed)
        self.assertEqual(self._reason(detail), "unsupported_operator")

    def test_invalid_value_blocked(self):
        buy_cfg = self._bollinger_buy_cfg({
            "enabled": True,
            "conditions": [{
                "enabled": True,
                "target": "CLOSE",
                "operator": ">=",
                "compare_target": "BOLLINGER",
                "value": "not_a_number",
            }],
        })
        series = self._series([100.0, 100.0], [95.0, 95.0])
        passed, detail = self.engine._evaluate_buy_bollinger_filter(
            self.default_config, buy_cfg, series, 1
        )
        self.assertFalse(passed)
        self.assertEqual(self._reason(detail), "invalid_value")

    # ------------------------------------------------------------------
    # insufficient_data is blocked
    # ------------------------------------------------------------------
    def test_insufficient_data_blocked_when_bollinger_missing(self):
        buy_cfg = self._bollinger_buy_cfg({
            "enabled": True,
            "conditions": [{
                "enabled": True,
                "target": "CLOSE",
                "operator": ">=",
                "compare_target": "BOLLINGER",
                "value": 0.0,
            }],
        })
        series = self._series([100.0, 100.0], [95.0, None])
        passed, detail = self.engine._evaluate_buy_bollinger_filter(
            self.default_config, buy_cfg, series, 1
        )
        self.assertFalse(passed)
        self.assertEqual(self._reason(detail), "insufficient_data")

    def test_insufficient_data_blocked_when_close_missing(self):
        buy_cfg = self._bollinger_buy_cfg({
            "enabled": True,
            "conditions": [{
                "enabled": True,
                "target": "CLOSE",
                "operator": ">=",
                "compare_target": "BOLLINGER",
                "value": 0.0,
            }],
        })
        series = self._series([100.0, None], [95.0, 95.0])
        passed, detail = self.engine._evaluate_buy_bollinger_filter(
            self.default_config, buy_cfg, series, 1
        )
        self.assertFalse(passed)
        self.assertEqual(self._reason(detail), "insufficient_data")

    # ------------------------------------------------------------------
    # missing conditions / invalid condition shape
    # ------------------------------------------------------------------
    def test_missing_conditions_blocked(self):
        buy_cfg = self._bollinger_buy_cfg({"enabled": True, "conditions": []})
        series = self._series([100.0, 100.0], [95.0, 95.0])
        passed, detail = self.engine._evaluate_buy_bollinger_filter(
            self.default_config, buy_cfg, series, 1
        )
        self.assertFalse(passed)
        self.assertEqual(self._reason(detail), "missing_conditions")

    def test_invalid_condition_shape_blocked(self):
        buy_cfg = self._bollinger_buy_cfg({"enabled": True, "conditions": ["not_a_dict"]})
        series = self._series([100.0, 100.0], [95.0, 95.0])
        passed, detail = self.engine._evaluate_buy_bollinger_filter(
            self.default_config, buy_cfg, series, 1
        )
        self.assertFalse(passed)
        self.assertEqual(self._reason(detail), "invalid_condition")

    # ------------------------------------------------------------------
    # no filter / pending candidate has no effect
    # ------------------------------------------------------------------
    def test_no_filter_passes(self):
        buy_cfg = {"groups": []}
        series = self._series([100.0, 100.0], [95.0, 95.0])
        passed, detail = self.engine._evaluate_buy_bollinger_filter(
            self.default_config, buy_cfg, series, 1
        )
        self.assertTrue(passed)
        self.assertIsNone(detail)

    def test_pending_candidate_not_in_config_has_no_effect(self):
        # A Bollinger filter that is still PENDING (not applied to rules) is
        # simply absent from buy.filters, so the engine evaluates as if there
        # is no Bollinger filter: it passes and has no effect on the signal.
        buy_cfg = deepcopy(self.default_config.get("buy", {}))
        buy_cfg.pop("filters", None)
        series = self._series([100.0, 100.0], [95.0, 95.0])
        passed, detail = self.engine._evaluate_buy_bollinger_filter(
            self.default_config, buy_cfg, series, 1
        )
        self.assertTrue(passed)
        self.assertIsNone(detail)

    # ------------------------------------------------------------------
    # deterministic
    # ------------------------------------------------------------------
    def test_deterministic(self):
        buy_cfg = self._bollinger_buy_cfg({
            "enabled": True,
            "conditions": [{
                "enabled": True,
                "target": "CLOSE",
                "operator": ">=",
                "compare_target": "BOLLINGER",
                "value": 0.0,
            }],
        })
        series = self._series([100.0, 100.0], [95.0, 95.0])
        r1 = self.engine._evaluate_buy_bollinger_filter(
            self.default_config, buy_cfg, series, 1
        )
        r2 = self.engine._evaluate_buy_bollinger_filter(
            self.default_config, buy_cfg, series, 1
        )
        self.assertEqual(r1, r2)

    # ------------------------------------------------------------------
    # inputs are not mutated
    # ------------------------------------------------------------------
    def test_inputs_are_not_mutated(self):
        buy_cfg = self._bollinger_buy_cfg({
            "enabled": True,
            "conditions": [{
                "enabled": True,
                "target": "CLOSE",
                "operator": ">=",
                "compare_target": "BOLLINGER",
                "value": 0.0,
            }],
        })
        series = self._series([100.0, 100.0], [95.0, 95.0])
        config_before = deepcopy(self.default_config)
        buy_cfg_before = deepcopy(buy_cfg)
        series_before = deepcopy(series)

        self.engine._evaluate_buy_bollinger_filter(
            self.default_config, buy_cfg, series, 1
        )

        self.assertEqual(self.default_config, config_before)
        self.assertEqual(buy_cfg, buy_cfg_before)
        self.assertEqual(series, series_before)

    # ------------------------------------------------------------------
    # SELL evaluation is unaffected by the BUY Bollinger filter
    # ------------------------------------------------------------------
    def test_sell_unaffected_by_bollinger_filter(self):
        config = deepcopy(self.default_config)
        # SELL passes trivially (CLOSE >= 0 always true with positive closes)
        config["sell"]["groups"] = [{
            "enabled": True,
            "name": "sell_group",
            "conditions": [{
                "enabled": True,
                "not": False,
                "target": "CLOSE",
                "operator": ">=",
                "value": 0,
            }],
        }]
        # BUY group passes trivially too
        config["buy"]["groups"] = [{
            "enabled": True,
            "name": "buy_group",
            "conditions": [{
                "enabled": True,
                "not": False,
                "target": "CLOSE",
                "operator": ">=",
                "value": 0,
            }],
        }]
        # Bollinger filter that would block BUY
        config["buy"]["filters"] = {
            "bollinger": {
                "enabled": True,
                "conditions": [{
                    "enabled": True,
                    "target": "CLOSE",
                    "operator": ">=",
                    "compare_target": "BOLLINGER",
                    "value": 1000.0,
                }],
            }
        }
        candles = [{"close": float(100 + i)} for i in range(30)]
        signal = self.engine.evaluate_indicator_follow_routine(candles, config, None)
        # SELL is evaluated first and must not be influenced by the BUY filter
        self.assertEqual(signal.signal, "SELL")

    def test_bollinger_blocks_buy_but_not_sell(self):
        config = deepcopy(self.default_config)
        # SELL fails (CLOSE < 0 is never true with positive closes)
        config["sell"]["groups"] = [{
            "enabled": True,
            "name": "sell_group",
            "conditions": [{
                "enabled": True,
                "not": False,
                "target": "CLOSE",
                "operator": "<",
                "value": 0,
            }],
        }]
        # BUY group passes trivially
        config["buy"]["groups"] = [{
            "enabled": True,
            "name": "buy_group",
            "conditions": [{
                "enabled": True,
                "not": False,
                "target": "CLOSE",
                "operator": ">=",
                "value": 0,
            }],
        }]
        # Bollinger filter blocks BUY
        config["buy"]["filters"] = {
            "bollinger": {
                "enabled": True,
                "conditions": [{
                    "enabled": True,
                    "target": "CLOSE",
                    "operator": ">=",
                    "compare_target": "BOLLINGER",
                    "value": 1000.0,
                }],
            }
        }
        candles = [{"close": float(100 + i)} for i in range(30)]
        signal = self.engine.evaluate_indicator_follow_routine(candles, config, None)
        # SELL failed, BUY blocked by Bollinger -> no signal
        self.assertIsNone(signal.signal)
        self.assertEqual(signal.reason, "BUY bollinger filter blocked")

    # ------------------------------------------------------------------
    # BUY filter evaluation order is maintained
    # ------------------------------------------------------------------
    def test_buy_filter_evaluation_order_maintained(self):
        config = deepcopy(self.default_config)
        config["buy"]["groups"] = [{
            "enabled": True,
            "name": "buy_group",
            "conditions": [{
                "enabled": True,
                "not": False,
                "target": "CLOSE",
                "operator": ">=",
                "value": 0,
            }],
        }]
        config["buy"]["filters"] = {
            "rsi": {
                "enabled": True,
                "conditions": [{
                    "enabled": True,
                    "period": 14,
                    "operator": "<=",
                    "threshold": 100,
                }],
            },
            "moving_average": {
                "enabled": True,
                "conditions": [{
                    "enabled": True,
                    "target": "CLOSE",
                    "operator": ">",
                    "compare_target": "MA",
                    "period": 5,
                }],
            },
            "price_compare": {
                "enabled": True,
                "conditions": [{
                    "enabled": True,
                    "target": "CLOSE",
                    "operator": ">=",
                    "compare_target": "CLOSE",
                    "value": 0,
                }],
            },
            "bollinger": {
                "enabled": True,
                "conditions": [{
                    "enabled": True,
                    "target": "CLOSE",
                    "operator": ">=",
                    "compare_target": "BOLLINGER",
                    "value": 1000.0,
                }],
            },
        }
        candles = [{"close": float(100 + i)} for i in range(30)]

        call_order = []

        def _recorder(name, ret):
            def _wrapper(*args, **kwargs):
                call_order.append(name)
                return ret
            return _wrapper

        with mock.patch.object(
            self.engine, "_evaluate_buy_rsi_filter", _recorder("rsi", (True, "rsi"))
        ), mock.patch.object(
            self.engine, "_evaluate_buy_moving_average_filter", _recorder("ma", (True, "ma"))
        ), mock.patch.object(
            self.engine, "_evaluate_buy_price_compare_filter", _recorder("price_compare", (True, "pc"))
        ), mock.patch.object(
            self.engine, "_evaluate_buy_bollinger_filter", _recorder("bollinger", (False, "bol"))
        ):
            signal = self.engine.evaluate_indicator_follow_routine(candles, config, None)

        self.assertEqual(call_order, ["rsi", "ma", "price_compare", "bollinger"])
        self.assertIsNone(signal.signal)
        self.assertEqual(signal.reason, "BUY bollinger filter blocked")


if __name__ == "__main__":
    unittest.main()
