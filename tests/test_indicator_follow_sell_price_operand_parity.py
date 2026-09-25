# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

import gui_indicator_follow_routine_settings_dialog as dialog_module
from gui_indicator_follow_routine_settings_dialog import (
    IndicatorFollowRoutineSettingsDialog,
    STATE_AUTHORITY_CANONICAL_DEFAULT,
    STATE_AUTHORITY_INSTANCE_CURRENT,
)
from gui_indicator_follow_sell_controls import (
    SELL_PRICE_COMBO_VALUES,
    SELL_PRICE_RESELECTION_TEXT,
    SELL_PRICE_UNRESOLVED_PROPERTY,
)
from gui_indicator_follow_signal_validation_window import (
    IndicatorFollowSignalValidationWindow,
)
from indicator_follow_signal_validation_projection import (
    IndicatorFollowSignalValidationSeed,
    expression_aware_sell_price_selection_issues,
)
from routines.지표추종매매.routine_validation_contract import (
    ValidationSettingsSnapshot,
    ValidationStockRef,
)


class SellPriceOperandParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.root = Path(__file__).resolve().parents[1]
        cls.routine_dir = cls.root / "routines" / "지표추종매매"
        cls.template_rules = cls.routine_dir / "rules.json"
        cls.stock = ValidationStockRef("005930", "삼성전자")

    def setUp(self):
        self.widgets = []

    def tearDown(self):
        for widget in self.widgets:
            widget.close()
            widget.deleteLater()
        self.app.processEvents()

    def _dialog(self, *, mode="registration", template=True):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        rules_path = Path(temp_dir.name) / "rules.json"
        if template:
            rules_path.write_bytes(self.template_rules.read_bytes())

        class Dialog(IndicatorFollowRoutineSettingsDialog):
            def load_rules(inner_self):
                if template:
                    return super(Dialog, inner_self).load_rules()
                inner_self.rules_data = {}
                inner_self.rules = {}

        kwargs = {
            "rules_path": rules_path,
            "routine_path": self.routine_dir,
            "routine_name": "지표추종매매",
            "definition_id": "indicator_follow",
            "settings_mode": mode,
        }
        if mode == "edit":
            kwargs["instance_id"] = "PRICE-OPERAND-PARITY"
        with patch.object(dialog_module.QTimer, "singleShot"):
            dialog = Dialog(**kwargs)
        self.widgets.append(dialog)
        return dialog

    @staticmethod
    def _operand_widgets(owner):
        for group_name in "abc":
            for side in ("left", "right"):
                yield group_name, side, getattr(
                    owner,
                    f"sell_signal_condition_{group_name}_gap_{side}_combo",
                )

    @staticmethod
    def _set_all_operands(state, value):
        copied = deepcopy(state)
        for group_name in "abc":
            condition = copied["sell_ui"]["signal_conditions"][
                f"condition_{group_name}"
            ]
            condition["gap_left_combo"] = value
            condition["gap_right_combo"] = value
        return copied

    def _v2(self, source):
        seed = IndicatorFollowSignalValidationSeed(
            ValidationSettingsSnapshot(source.rules_data),
            source.collect_indicator_follow_ui_state(),
        )
        with patch.object(dialog_module.QTimer, "singleShot"):
            window = IndicatorFollowSignalValidationWindow(self.stock, seed)
        self.widgets.append(window)
        return window

    def test_fresh_registration_uses_canonical_modern_price_operands(self):
        registration = self._dialog(template=True)
        actual = registration.collect_indicator_follow_ui_state()

        self.assertEqual(
            STATE_AUTHORITY_CANONICAL_DEFAULT,
            registration._registration_initial_state_source,
        )
        for group_name, _side, combo in self._operand_widgets(registration):
            condition = actual["sell_ui"]["signal_conditions"][f"condition_{group_name}"]
            self.assertEqual("평단가", condition["gap_left_combo"])
            self.assertEqual("현재가", condition["gap_right_combo"])
            self.assertEqual(list(SELL_PRICE_COMBO_VALUES), [
                combo.itemText(index) for index in range(combo.count())
            ])
            self.assertIn(combo.currentText(), SELL_PRICE_COMBO_VALUES)

    def test_modern_parent_and_v2_preserve_each_operand(self):
        parent = self._dialog(template=False)
        modern = parent.collect_indicator_follow_ui_state()
        for group_name in "abc":
            condition = modern["sell_ui"]["signal_conditions"][f"condition_{group_name}"]
            condition["gap_left_combo"] = "현재가"
            condition["gap_right_combo"] = "평단가"
        parent.apply_indicator_follow_ui_state(
            modern,
            source=STATE_AUTHORITY_INSTANCE_CURRENT,
        )
        v2 = self._v2(parent)

        for owner in (parent, v2):
            for _group_name, side, combo in self._operand_widgets(owner):
                self.assertEqual("현재가" if side == "left" else "평단가", combo.currentText())
                self.assertIsNone(combo.property(SELL_PRICE_UNRESOLVED_PROPERTY))

    def test_legacy_operand_is_explicit_and_never_silently_defaulted_in_v2(self):
        parent = self._dialog(template=False)
        legacy = self._set_all_operands(parent.collect_indicator_follow_ui_state(), "ORDER_PRICE")
        legacy["basic"]["sell_signal_expr_line"] = "A"
        legacy["sell_ui"]["signal_conditions"]["condition_a"]["gap_check"] = True
        parent.apply_indicator_follow_ui_state(
            legacy,
            source=STATE_AUTHORITY_INSTANCE_CURRENT,
        )
        v2 = self._v2(parent)

        for owner in (parent, v2):
            for _group_name, _side, combo in self._operand_widgets(owner):
                self.assertEqual(-1, combo.currentIndex())
                self.assertEqual("", combo.currentText())
                self.assertFalse(combo.isEditable())
                self.assertIn("현재가 또는 평단가", combo.toolTip())
                self.assertEqual("ORDER_PRICE", combo.property(SELL_PRICE_UNRESOLVED_PROPERTY))
                self.assertEqual(list(SELL_PRICE_COMBO_VALUES), [
                    combo.itemText(index) for index in range(combo.count())
                ])

        self.assertEqual(
            2,
            len(expression_aware_sell_price_selection_issues(
                parent.collect_indicator_follow_ui_state()
            )),
        )

        for _group_name, _side, combo in self._operand_widgets(parent):
            combo.setCurrentIndex(combo.findText("평단가"))
            self.assertIsNone(combo.property(SELL_PRICE_UNRESOLVED_PROPERTY))
        self.assertEqual(
            (),
            expression_aware_sell_price_selection_issues(
                parent.collect_indicator_follow_ui_state()
            ),
        )

    def test_delayed_sell_gap_uses_evaluation_time_current_price_and_average(self):
        from routines.지표추종매매.routine_macd_engine import (
            evaluate_indicator_follow_routine,
        )

        for target in ("CURRENT_PRICE", "CLOSE"):
            with self.subTest(target=target):
                rules = {
                    "enabled": True,
                    "buy": {"enabled": False},
                    "sell": {
                        "enabled": True,
                        "signals": {
                            "ui_condition_a": {
                                "enabled": True,
                                "order_delay_bars": 1,
                                "signal_expression": {
                                    "source": "A",
                                    "ast": {"type": "identifier", "name": "A"},
                                    "identifiers": ["A"],
                                    "identifier_map": {"A": "ui_condition_a"},
                                },
                                "groups": [{
                                    "enabled": True,
                                    "conditions": [{
                                        "enabled": True,
                                        "expression_id": "GAP_0",
                                        "target": target,
                                        "operator": "PERCENT_GAP",
                                        "compare_target": "AVG_PRICE",
                                        "direction": "UP",
                                        "compare_mode": "GTE",
                                        "value": 10.0,
                                    }],
                                }],
                            },
                        },
                    },
                }
                result = evaluate_indicator_follow_routine(
                    [{"close": 90.0}, {"close": 100.0}, {"close": 120.0}],
                    rules,
                    {
                        "_indicator_follow_evaluate_side": "SELL",
                        "actionable_current_price": 120.0,
                        "cycle": {"avg_price": 100.0},
                    },
                )

                self.assertEqual("SELL", result.signal)
                self.assertEqual(1, result.signal_index)
                self.assertEqual(1, result.delay_bar)

        legacy_rules = {
            "enabled": True,
            "buy": {"enabled": False},
            "sell": {
                "enabled": True,
                "signals": {
                    "price_sell": {
                        "enabled": True,
                        "order_delay_bars": 1,
                        "groups": [{
                            "enabled": True,
                            "conditions": [{
                                "enabled": True,
                                "target": "CLOSE",
                                "operator": "PERCENT_GAP",
                                "compare_target": "AVG_PRICE",
                                "direction": "UP",
                                "compare_mode": "GTE",
                                "value": 10.0,
                            }],
                        }],
                    },
                },
            },
        }
        blocked = evaluate_indicator_follow_routine(
            [{"close": 90.0}, {"close": 100.0}, {"close": 120.0}],
            legacy_rules,
            {
                "_indicator_follow_evaluate_side": "SELL",
                "cycle": {"avg_price": 100.0},
            },
        )
        self.assertIsNone(blocked.signal)

    def test_validation_delayed_sell_gap_uses_evaluation_candle_close(self):
        from routines.지표추종매매.routine_validation_contract import (
            ValidationRequest,
        )
        from routines.지표추종매매.routine_validation_historical import (
            ValidationHistoricalSnapshot,
        )
        from routines.지표추종매매.routine_validation_replay import (
            ValidationHistoricalReplay,
        )
        from routines.지표추종매매.routine_validation_session import (
            ValidationSession,
        )

        rules = {
            "enabled": True,
            "bar": {"bar_minutes": 5},
            "buy": {"enabled": False},
            "sell": {
                "enabled": True,
                "signals": {
                    "price_sell": {
                        "enabled": True,
                        "order_delay_bars": 1,
                        "groups": [{
                            "enabled": True,
                            "conditions": [{
                                "enabled": True,
                                "target": "CURRENT_PRICE",
                                "operator": "PERCENT_GAP",
                                "compare_target": "AVG_PRICE",
                                "direction": "UP",
                                "compare_mode": "GTE",
                                "value": 10.0,
                            }],
                        }],
                    },
                },
            },
        }
        settings = ValidationSettingsSnapshot(rules)
        session = ValidationSession(
            ValidationRequest(self.stock, settings, 5),
            operation_active_reader=lambda: False,
        )
        closes = (90.0, 100.0, 120.0)
        historical = ValidationHistoricalSnapshot(
            stock=self.stock,
            timeframe_minutes=5,
            requested_count=len(closes),
            request_id="CURRENT-PRICE-DELAY-PARITY",
            rows=[
                {
                    "체결시간": f"2026092509{index:02d}00",
                    "시가": str(close),
                    "고가": str(close),
                    "저가": str(close),
                    "현재가": str(close),
                    "거래량": "1",
                }
                for index, close in reversed(list(enumerate(closes)))
            ],
        )

        def context_provider(_index, _side, _prefix, _entries):
            return {"average_price": 100.0}

        result = ValidationHistoricalReplay(session).evaluate(
            historical,
            start_index=2,
            end_index=2,
            context_provider=context_provider,
        )
        self.assertTrue(result.ok, result)
        sell = next(
            entry
            for entry in result.snapshot.to_entries()
            if entry.evaluation_side == "SELL" and entry.signal == "SELL"
        )
        self.assertEqual(2, sell.evaluation_index)
        self.assertEqual(1, sell.signal_index)
        gap = next(
            item
            for item in sell.trace["conditions"]
            if item["operator"] == "PERCENT_GAP"
        )
        self.assertEqual("CURRENT_PRICE", gap["left_operand"]["key"])
        self.assertEqual(120.0, gap["left_operand"]["value"])
        self.assertEqual("AVG_PRICE", gap["right_operand"]["key"])
        self.assertEqual(100.0, gap["right_operand"]["value"])

    def test_validation_batch_delayed_sell_uses_evaluation_current_price(self):
        from routines.지표추종매매.routine_validation_batch import (
            _IncrementalAverageContext,
            scan_indicator_follow_validation_batch,
        )

        rules = {
            "enabled": True,
            "buy": {
                "enabled": True,
                "delay_bar": 0,
                "groups": [{
                    "enabled": True,
                    "conditions": [{
                        "enabled": True,
                        "target": "CLOSE",
                        "operator": ">",
                        "value": 0,
                    }],
                }],
            },
            "sell": {
                "enabled": True,
                "signals": {
                    "price_sell": {
                        "enabled": True,
                        "order_delay_bars": 1,
                        "groups": [{
                            "enabled": True,
                            "conditions": [{
                                "enabled": True,
                                "target": "CURRENT_PRICE",
                                "operator": "PERCENT_GAP",
                                "compare_target": "AVG_PRICE",
                                "direction": "UP",
                                "compare_mode": "GTE",
                                "value": 10.0,
                            }],
                        }],
                    },
                },
            },
        }
        candles = [
            {"time": f"2026092509{index:02d}00", "close": close}
            for index, close in enumerate((100.0, 100.0, 100.0, 120.0))
        ]
        result = scan_indicator_follow_validation_batch(
            candles,
            rules,
            start_index=2,
            end_index=3,
            context_provider=_IncrementalAverageContext(),
        )

        self.assertTrue(result.supported, result)
        self.assertTrue(any(
            record.evaluation_side == "BUY"
            and record.evaluation_index == 2
            and record.signal == "BUY"
            for record in result.records
        ))
        sell = next(
            record
            for record in result.records
            if record.evaluation_side == "SELL"
            and record.evaluation_index == 3
            and record.signal == "SELL"
        )
        self.assertEqual(2, sell.routine_signal.signal_index)
        gap = next(
            item
            for item in sell.trace["conditions"]
            if item["operator"] == "PERCENT_GAP"
        )
        self.assertEqual("CURRENT_PRICE", gap["left_operand"]["key"])
        self.assertEqual(120.0, gap["left_operand"]["value"])
        self.assertEqual(100.0, gap["right_operand"]["value"])


if __name__ == "__main__":
    unittest.main()
