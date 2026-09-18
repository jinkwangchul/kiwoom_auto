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


if __name__ == "__main__":
    unittest.main()
