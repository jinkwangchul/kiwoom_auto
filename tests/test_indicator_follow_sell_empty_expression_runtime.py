# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

import gui_indicator_follow_routine_settings_dialog as dialog_module
from engines.condition_engine import parse_condition_expression
from gui_indicator_follow_signal_validation_window import (
    IndicatorFollowSignalValidationWindow,
)
from indicator_follow_signal_validation_projection import (
    IndicatorFollowSignalValidationSeed,
)
from routines.지표추종매매.routine_macd_engine import (
    evaluate_indicator_follow_routine,
)
from routines.지표추종매매.routine_validation_contract import (
    ValidationRequest,
    ValidationSettingsSnapshot,
    ValidationStockRef,
)
from routines.지표추종매매.routine_validation_historical import (
    ValidationHistoricalSnapshot,
)
from routines.지표추종매매.routine_validation_replay import (
    ValidationHistoricalReplay,
)
from routines.지표추종매매.routine_validation_session import ValidationSession
from routines.지표추종매매.routine_validation_trace import ValidationTraceObserver


_MISSING = object()
_PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SellEmptyExpressionRuntimeTest(unittest.TestCase):
    @staticmethod
    def _candles() -> list[dict[str, float]]:
        return [
            {
                "open": float(value),
                "high": float(value + 1),
                "low": float(value - 1),
                "close": float(value),
                "volume": 1000.0,
            }
            for value in range(10, 15)
        ]

    @staticmethod
    def _expression(source: str) -> dict:
        parsed = parse_condition_expression(
            source,
            allowed_identifiers={"A", "B", "C"},
            allow_duplicate_identifiers=False,
        )
        if parsed.get("ok") is not True:
            raise AssertionError(parsed)
        return {
            "source": source,
            "normalized": parsed["normalized"],
            "ast": parsed["ast"],
            "identifiers": parsed["identifiers"],
            "identifier_map": {
                "A": "ui_condition_a",
                "B": "ui_condition_b",
                "C": "ui_condition_c",
            },
        }

    @staticmethod
    def _condition_signal(name: str, passed: bool, expression=_MISSING) -> dict:
        signal = {
            "enabled": True,
            "order_delay_bars": 0,
            "groups": [{
                "enabled": True,
                "name": f"ui_{name.lower()}",
                "conditions": [{
                    "enabled": True,
                    "target": "CLOSE",
                    "operator": ">" if passed else "<",
                    "value": 0,
                }],
            }],
        }
        if expression is not _MISSING:
            signal["signal_expression"] = deepcopy(expression)
        return signal

    def _config(
        self,
        *,
        ui_passed: set[str] | None = None,
        expression=_MISSING,
        macd_passed: bool = False,
        profit_enabled: bool = False,
    ) -> dict:
        ui_passed = set(ui_passed or ())
        signals = {
            f"ui_condition_{name.lower()}": self._condition_signal(
                name,
                name in ui_passed,
                expression,
            )
            for name in ("A", "B", "C")
        }
        signals["macd_sell"] = {
            "enabled": macd_passed,
            "delay_bar": 0,
            "order_delay_bars": 1,
            "groups": [{
                "enabled": True,
                "name": "independent_macd",
                "conditions": [{
                    "enabled": True,
                    "target": "CLOSE",
                    "operator": "<",
                    "value": 100,
                }],
            }],
        }
        signals["profit_rate_sell"] = {
            "enabled": profit_enabled,
            "profit_rate_percent": 5.0,
        }
        return {
            "enabled": True,
            "bar": {"bar_minutes": 5},
            "macd": {"fast": 2, "slow": 3, "signal": 2},
            "rsi": {"period": 2},
            "moving_averages": [2, 3],
            "buy": {"enabled": False, "delay_bar": 0, "groups": []},
            "sell": {
                "enabled": True,
                "delay_bar": 0,
                "signal_logic": "OR",
                "signals": signals,
            },
        }

    def _evaluate(self, config: dict, *, profit_context: bool = False):
        observer = ValidationTraceObserver()
        context = {
            "_indicator_follow_evaluate_side": "SELL",
            "decision_trace_observer": observer,
        }
        if profit_context:
            context.update({
                "average_price": 100.0,
                "current_price": 110.0,
                "holding_qty": 1,
            })
        result = evaluate_indicator_follow_routine(
            self._candles(),
            config,
            context,
        )
        return result, observer.snapshot()

    def _assert_empty_ui_expression_is_inactive(self, expression) -> None:
        config = self._config(
            ui_passed={"A", "B", "C"},
            expression=expression,
        )
        original = deepcopy(config)
        result, trace = self._evaluate(config)

        self.assertIsNone(result.signal)
        self.assertEqual(original, config)
        aggregation = trace["aggregations"][-1]["payload"]
        self.assertIsNone(aggregation["ui_signal_expression"])
        self.assertEqual({}, aggregation["ui_expression_values"])
        self.assertFalse(aggregation["ui_expression_result"])
        self.assertEqual([], aggregation["matched_group_paths"])
        self.assertTrue(any(
            str(group.get("path") or "").startswith("sell.signals.ui_condition_")
            for group in trace["groups"]
        ))

    def test_missing_expression_blocks_true_ui_condition_a(self) -> None:
        config = self._config(ui_passed={"A"})
        result, _trace = self._evaluate(config)
        self.assertIsNone(result.signal)

    def test_missing_expression_blocks_true_ui_conditions_a_b_c(self) -> None:
        self._assert_empty_ui_expression_is_inactive(_MISSING)

    def test_none_expression_blocks_true_ui_conditions(self) -> None:
        self._assert_empty_ui_expression_is_inactive(None)

    def test_whitespace_expression_blocks_true_ui_conditions(self) -> None:
        self._assert_empty_ui_expression_is_inactive("   ")

    def test_independent_macd_sell_survives_without_ui_metadata_contamination(self) -> None:
        config = self._config(
            ui_passed={"A", "B", "C"},
            macd_passed=True,
        )
        result, trace = self._evaluate(config)

        self.assertEqual("SELL", result.signal)
        self.assertEqual(["independent_macd"], result.matched_groups)
        self.assertEqual(3, result.signal_index)
        self.assertEqual(1, result.delay_bar)
        self.assertFalse(any("CLOSE > 0" in detail for detail in result.details))
        aggregation = trace["aggregations"][-1]["payload"]
        self.assertEqual(
            ["sell.signals.macd_sell.groups[0]"],
            aggregation["matched_group_paths"],
        )
        self.assertFalse(any(
            "ui_condition_" in path
            for path in aggregation["active_group_paths"]
            + aggregation["matched_group_paths"]
        ))

    def test_profit_rate_sell_survives_without_ui_metadata_contamination(self) -> None:
        config = self._config(
            ui_passed={"A", "B", "C"},
            profit_enabled=True,
        )
        result, trace = self._evaluate(config, profit_context=True)

        self.assertEqual("SELL", result.signal)
        self.assertEqual(["profit_rate_sell"], result.matched_groups)
        self.assertFalse(any("CLOSE > 0" in detail for detail in result.details))
        self.assertTrue(any("profit_rate_sell" in detail for detail in result.details))
        aggregation = trace["aggregations"][-1]["payload"]
        self.assertEqual([], aggregation["active_group_paths"])
        self.assertEqual([], aggregation["matched_group_paths"])

    def test_valid_expression_matrix_preserves_existing_runtime_semantics(self) -> None:
        cases = (
            ("A", {"A"}),
            ("A and B", {"A", "B"}),
            ("A or C", {"A", "C"}),
            ("A and B and C", {"A", "B", "C"}),
            ("(A and B) or C", {"C"}),
        )
        for expression_text, passed_groups in cases:
            with self.subTest(expression=expression_text):
                expression = self._expression(expression_text)
                config = self._config(
                    ui_passed=passed_groups,
                    expression=expression,
                )
                result, trace = self._evaluate(config)
                self.assertEqual("SELL", result.signal)
                aggregation = trace["aggregations"][-1]["payload"]
                self.assertEqual(expression, aggregation["ui_signal_expression"])
                self.assertTrue(aggregation["ui_expression_result"])


class SellEmptyExpressionV2IntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.stock = ValidationStockRef("005930", "삼성전자")
        rules_path = _PROJECT_ROOT / "routines" / "지표추종매매" / "rules.json"
        cls.source_rules = json.loads(rules_path.read_text(encoding="utf-8"))

    @staticmethod
    def _runtime_rules() -> dict:
        config = SellEmptyExpressionRuntimeTest()._config(
            ui_passed={"A", "B", "C"},
        )
        config["buy"] = {
            "enabled": True,
            "delay_bar": 0,
            "groups": [{
                "enabled": True,
                "name": "buy_d",
                "conditions": [{
                    "enabled": True,
                    "target": "CLOSE",
                    "operator": ">",
                    "value": 0,
                }],
            }],
        }
        return config

    def _ui_state(self) -> dict:
        state = deepcopy(self.source_rules["indicator_follow_ui_state"]["state"])
        state["basic"]["buy_signal_expr_line"] = "D"
        state["basic"]["sell_signal_expr_line"] = ""
        for group in state["sell_ui"]["signal_conditions"].values():
            group["gap_left_combo"] = "평단가"
            group["gap_right_combo"] = "현재가"
        return state

    def _replay(self, settings: ValidationSettingsSnapshot):
        request = ValidationRequest(self.stock, settings, 5)
        session = ValidationSession(
            request,
            operation_active_reader=Mock(return_value=False),
        )
        rows = [
            {
                "체결시간": f"2026091413{index:02d}00",
                "시가": str(close),
                "고가": str(close + 1),
                "저가": str(close - 1),
                "현재가": str(close),
                "거래량": "1000",
            }
            for index, close in reversed(list(enumerate((10, 11, 12, 13, 14))))
        ]
        historical = ValidationHistoricalSnapshot(
            stock=self.stock,
            timeframe_minutes=5,
            requested_count=len(rows),
            request_id="EMPTY-SELL-V2",
            rows=rows,
        )
        result = ValidationHistoricalReplay(session).evaluate(historical)
        self.assertTrue(result.ok, result)
        return result.snapshot

    def test_v2_replay_keeps_buy_and_projects_no_ui_sell_surface(self) -> None:
        ui_state = self._ui_state()
        original_ui_state = deepcopy(ui_state)
        seed = IndicatorFollowSignalValidationSeed(
            ValidationSettingsSnapshot(self._runtime_rules()),
            ui_state,
        )
        snapshot = self._replay(seed.settings_snapshot)
        entries = snapshot.to_entries()
        self.assertGreater(sum(entry.signal == "BUY" for entry in entries), 0)
        self.assertEqual(0, sum(entry.signal == "SELL" for entry in entries))

        with patch.object(dialog_module.QTimer, "singleShot"):
            window = IndicatorFollowSignalValidationWindow(
                self.stock,
                seed,
            )
        try:
            window._pending_result_settings_snapshot = seed.settings_snapshot
            window.set_replay_snapshot(snapshot)
            markers = window.canvas.marker_records()
            self.assertGreater(sum(marker["side"] == "BUY" for marker in markers), 0)
            self.assertEqual(0, sum(marker["side"] == "SELL" for marker in markers))
            self.assertIn("SELL 0", window.result_summary_label.text())
            self.assertFalse(hasattr(window, "signal_list_table"))
            self.assertEqual(0, window.completed_cycle_table.rowCount())
            self.assertTrue(window.completed_cycle_table.isHidden())
            self.assertFalse(window.completed_cycle_empty_label.isHidden())
            self.assertTrue(any(
                tooltip.startswith("BUY ·")
                for (_index, side), tooltip in window._signal_tooltips.items()
                if side == "BUY"
            ))
            self.assertFalse(any(
                side == "SELL" for _index, side in window._signal_tooltips
            ))
            self.assertEqual("D", window.buy_signal_expr_line.text())
            self.assertEqual("", window.sell_signal_expr_line.text())
            self.assertEqual(original_ui_state, ui_state)
            projected_signals = seed.settings_snapshot.to_dict()["sell"]["signals"]
            self.assertEqual(
                {"ui_condition_a", "ui_condition_b", "ui_condition_c"},
                set(projected_signals),
            )
            self.assertTrue(all(
                "signal_expression" not in signal
                for signal in projected_signals.values()
            ))
            self.assertTrue(all(signal["groups"] for signal in projected_signals.values()))
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
