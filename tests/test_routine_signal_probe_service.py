from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest

import routine_signal_probe_service


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ACTUAL_RULES_PATH = PROJECT_ROOT / "routines" / "지표추종매매" / "rules.json"
RUNTIME_QUEUE_PATH = PROJECT_ROOT / "runtime" / "routine_signals.json"


def _file_sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _candles() -> list[dict[str, float]]:
    return [
        {"close": 10.0, "volume": 100.0},
        {"close": 11.0, "volume": 110.0},
        {"close": 12.0, "volume": 120.0},
    ]


def _indicators() -> dict:
    return {
        "rsi": None,
        "macd": None,
        "signal": None,
        "ma": None,
        "bollinger": None,
    }


def _snapshot(candle_key: str = "candles") -> dict:
    return {
        "symbol": "005930",
        "timeframe": "1m",
        candle_key: _candles(),
        "current_price": 12.0,
        "indicators": _indicators(),
    }


def _base_rules() -> dict:
    return {
        "enabled": True,
        "macd": {"fast": 12, "slow": 26, "signal": 9},
        "rsi": {"period": 14},
        "moving_averages": [],
        "buy": {
            "delay_bar": 0,
            "groups": [],
        },
        "sell": {
            "delay_bar": 0,
            "signals": {
                "macd_sell": {
                    "enabled": False,
                    "groups": [],
                }
            },
        },
    }


def _buy_rules() -> dict:
    rules = _base_rules()
    rules["buy"]["groups"] = [
        {
            "enabled": True,
            "name": "probe_buy_group",
            "conditions": [
                {"enabled": True, "target": "CLOSE", "operator": ">=", "value": 12}
            ],
        }
    ]
    return rules


def _sell_rules() -> dict:
    rules = _base_rules()
    rules["buy"]["groups"] = [
        {
            "enabled": True,
            "name": "probe_buy_group",
            "conditions": [
                {"enabled": True, "target": "CLOSE", "operator": ">", "value": 99}
            ],
        }
    ]
    rules["sell"]["signals"] = {
        "macd_sell": {
            "enabled": True,
            "groups": [
                {
                    "enabled": True,
                    "name": "probe_sell_group",
                    "conditions": [
                        {"enabled": True, "target": "CLOSE", "operator": ">=", "value": 12}
                    ],
                }
            ],
        }
    }
    return rules


def _none_rules() -> dict:
    rules = _base_rules()
    rules["buy"]["groups"] = [
        {
            "enabled": True,
            "name": "probe_buy_group",
            "conditions": [
                {"enabled": True, "target": "CLOSE", "operator": ">", "value": 99}
            ],
        }
    ]
    return rules


class RoutineSignalProbeServiceTest(unittest.TestCase):
    def test_buy_probe_returns_preview_dict_without_mutating_inputs(self):
        rules = _buy_rules()
        snapshot = _snapshot()
        snapshot["source"] = "unit-test"
        rules_before = deepcopy(rules)
        snapshot_before = deepcopy(snapshot)

        result = routine_signal_probe_service.run_routine_signal_probe(
            rules,
            snapshot,
            rule_source="unit_probe_rules",
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["stage"], "ROUTINE_SIGNAL_PROBE")
        self.assertEqual(result["preview_type"], "routine_signal_preview")
        self.assertEqual(result["signal"], "BUY")
        self.assertEqual(result["rule_source"], "unit_probe_rules")
        self.assertEqual(result["matched_rule_paths"], ["buy.groups"])
        self.assertEqual(result["matched_groups"], ["probe_buy_group"])
        self.assertEqual(result["condition_summary"], result["details"])
        self.assertFalse(result["queue_connected"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["execution_connected"])
        self.assertFalse(result["send_order_connected"])
        self.assertEqual(rules, rules_before)
        self.assertEqual(snapshot, snapshot_before)

    def test_sell_probe_returns_preview_dict(self):
        result = routine_signal_probe_service.run_routine_signal_probe(
            _sell_rules(),
            _snapshot(),
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["signal"], "SELL")
        self.assertEqual(result["matched_rule_paths"], ["sell.signals"])
        self.assertEqual(result["matched_groups"], ["probe_sell_group"])
        self.assertEqual(result["routine_signal"]["signal"], "SELL")
        self.assertTrue(result["reason"])

    def test_none_probe_returns_none_preview(self):
        result = routine_signal_probe_service.run_routine_signal_probe(
            _none_rules(),
            _snapshot(),
        )

        self.assertTrue(result["ok"], result)
        self.assertIsNone(result["signal"])
        self.assertEqual(result["matched_rule_paths"], [])
        self.assertEqual(result["matched_groups"], [])
        self.assertEqual(result["routine_signal"]["signal"], None)

    def test_probe_blocks_non_dict_inputs(self):
        rules_result = routine_signal_probe_service.run_routine_signal_probe(
            [],
            _snapshot(),
        )
        snapshot_result = routine_signal_probe_service.run_routine_signal_probe(
            _buy_rules(),
            [],
        )

        self.assertFalse(rules_result["ok"])
        self.assertEqual(rules_result["blocked_reasons"], ["rules must be dict"])
        self.assertFalse(snapshot_result["ok"])
        self.assertEqual(snapshot_result["blocked_reasons"], ["market_snapshot must be dict"])

    def test_probe_does_not_touch_actual_rules_or_runtime_queue(self):
        before_rules_hash = _file_sha256(ACTUAL_RULES_PATH)
        before_queue_hash = _file_sha256(RUNTIME_QUEUE_PATH)

        result = routine_signal_probe_service.run_routine_signal_probe(
            _buy_rules(),
            _snapshot(),
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(_file_sha256(ACTUAL_RULES_PATH), before_rules_hash)
        self.assertEqual(_file_sha256(RUNTIME_QUEUE_PATH), before_queue_hash)

    def test_probe_normalizes_candle_aliases_to_same_result(self):
        results = [
            routine_signal_probe_service.run_routine_signal_probe(
                _buy_rules(),
                _snapshot(candle_key),
            )
            for candle_key in ("candles", "bars", "ohlcv")
        ]

        self.assertTrue(all(result["ok"] for result in results), results)
        self.assertEqual([result["signal"] for result in results], ["BUY", "BUY", "BUY"])
        self.assertEqual(
            [result["routine_signal"] for result in results],
            [results[0]["routine_signal"], results[0]["routine_signal"], results[0]["routine_signal"]],
        )

    def test_probe_blocks_missing_required_snapshot_fields(self):
        snapshot = _snapshot()
        del snapshot["timeframe"]

        result = routine_signal_probe_service.run_routine_signal_probe(
            _buy_rules(),
            snapshot,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "ROUTINE_SIGNAL_PROBE_BLOCKED")
        self.assertEqual(
            result["blocked_reasons"],
            ["missing required market_snapshot fields: timeframe"],
        )
        self.assertFalse(result["queue_connected"])
        self.assertFalse(result["runtime_write"])

    def test_probe_service_has_no_execution_or_queue_imports(self):
        module_text = Path(routine_signal_probe_service.__file__).read_text(encoding="utf-8")

        self.assertNotIn("routine_signal_queue", module_text)
        self.assertNotIn("import send_order", module_text.lower())
        self.assertNotIn("from send_order", module_text.lower())
        self.assertNotIn("SendOrder(", module_text)
        self.assertNotIn("order_queue", module_text)
        self.assertNotIn("execution_service", module_text)


if __name__ == "__main__":
    unittest.main()
