from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

import tests.test_mock_indicator_follow_adapter as fixtures
from mock_validation_indicator_follow_adapter import (
    _load_file_module, _pure_routine_functions, _signal_payload,
)
from mock_validation_virtual_execution import MockExecutionPolicy


class MockSignalSemanticsParityTest(unittest.TestCase):
    def compare(self, rules, evaluator, holding, expected, *, forming=False):
        fixture = fixtures.MockIndicatorFollowAdapterTest()
        self.addCleanup(fixture.doCleanups)
        repository, service, _, adapter, _ = fixture.build({"A": rules})
        if holding:
            fixture.seed_holding(repository, service, qty=holding)
        contexts = []

        def capture(candles, rules_value, context):
            contexts.append(deepcopy(context))
            return evaluator(candles, rules_value, context)

        adapter._evaluator = capture
        adapter._buy_builder = Mock(wraps=adapter._buy_builder)
        adapter._sell_builder = Mock(wraps=adapter._sell_builder)
        candles = [
            {"close": 100 + i, "volume": 100,
             "bar_time": f"2026-09-03T09:5{i}:00+09:00", "timeframe_minutes": 1,
             "is_complete": not (forming and i == 4)}
            for i in range(5)
        ]
        result = adapter.evaluate_cycle(
            fixtures.SESSION_ID, routine_instance_id="A", candles=candles,
            market=fixtures._market(), policy=MockExecutionPolicy(1, "LOGIN-1", 2, 2),
            evaluation_cycle_id="PARITY", evaluated_at=fixtures.NOW,
        )
        self.assertEqual(["SELL", "BUY"],
                         [c["_indicator_follow_evaluate_side"] for c in contexts])
        production = _load_file_module("routine.py", "parity_production_routine")
        with patch.object(production, "evaluate_indicator_follow_routine", evaluator), \
             patch.object(production, "signal_to_dict", _signal_payload):
            prod = production.evaluate(contexts[0])
        mock_signal = result["signal"]
        self.assertEqual(expected, prod["signal"])
        self.assertEqual(prod["signal"], mock_signal["signal"])
        for field in ("signal_index", "signal_source_index", "signal_activation_index",
                      "delay_bar", "signal_activation_bar_time"):
            self.assertEqual(prod.get(field), mock_signal.get(field), field)
        events = [e for e in repository.read_events(fixtures.SESSION_ID)
                  if e["event_type"] == "ROUTINE_EVALUATED"]
        if expected is None:
            self.assertEqual([], events)
        else:
            self.assertEqual(1, len(events))
            self.assertEqual(prod["signal"], events[0]["payload"]["signal"])
        if expected is not None and forming:
            self.assertEqual(candles[-1]["bar_time"], events[0]["payload"]["signal_bar_time"])
        self.assertLessEqual(adapter._buy_builder.call_count + adapter._sell_builder.call_count, 1)

    @staticmethod
    def rules():
        rules = fixtures._buy_rules()
        rules["sell"] = fixtures._sell_rules()["sell"]
        return rules

    def test_dual_side_final_signal_matrix(self):
        for buy, sell, holding, expected in (
            (False, False, 0, None), (True, False, 0, "BUY"),
            (False, True, 3, "SELL"), (False, True, 0, None),
            (True, True, 0, "BUY"), (True, True, 3, "SELL"),
        ):
            with self.subTest(buy=buy, sell=sell, holding=holding):
                def evaluator(_candles, _rules, context):
                    side = context["_indicator_follow_evaluate_side"]
                    return {"signal": side if {"BUY": buy, "SELL": sell}[side] else None,
                            "reason": "fixture", "signal_index": 1, "delay_bar": 1}
                self.compare(self.rules(), evaluator, holding, expected)

    def test_completed_activation_preserves_transition_source_and_delay(self):
        for delay in (0, 1, 2):
            with self.subTest(delay=delay):
                rules = self.rules()
                rules["buy"]["filters"] = {"ocr": {"order_delay_bars": delay}}
                source_index = 4 - delay
                def evaluator(_candles, _rules, context):
                    return {"signal": "BUY" if context["_indicator_follow_evaluate_side"] == "BUY" else None,
                            "signal_index": source_index, "delay_bar": delay}
                self.compare(rules, evaluator, 0, "BUY", forming=False)

    def test_real_engine_simultaneous_conditions(self):
        evaluator = _pure_routine_functions()[0]
        rules = deepcopy(evaluator.__globals__["DEFAULT_INDICATOR_FOLLOW_CONFIG"])
        groups = [{"enabled": True, "name": "both", "conditions": [
            {"enabled": True, "target": "CLOSE", "operator": ">=", "value": 0}]}]
        rules["buy"].update(delay_bar=0, groups=groups,
                             execution=self.rules()["buy"]["execution"])
        rules["sell"] = {"delay_bar": 0, "signal_logic": "OR",
                         "signals": {"macd_sell": {"enabled": True, "groups": groups}},
                         "method": self.rules()["sell"]["method"]}
        rules["mock_validation"] = self.rules()["mock_validation"]
        for holding, expected in ((0, "BUY"), (3, "SELL")):
            with self.subTest(holding=holding):
                self.compare(rules, evaluator, holding, expected)


if __name__ == "__main__":
    unittest.main()
