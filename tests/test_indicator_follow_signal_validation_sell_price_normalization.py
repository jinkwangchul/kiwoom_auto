# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import json
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
)
from indicator_follow_signal_validation_projection import (
    IndicatorFollowSignalValidationApplyPayload,
    IndicatorFollowSignalValidationSeed,
    build_signal_validation_snapshot,
    build_validation_average_price_context,
    project_signal_validation_rules,
    sell_price_selection_issues,
)
from routines.지표추종매매 import routine_rule_mapper as mapper
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
    ValidationReplayEntry,
    ValidationReplaySnapshot,
)
from routines.지표추종매매.routine_validation_session import ValidationSession
from gui_indicator_follow_signal_validation_window import (
    IndicatorFollowSignalValidationWindow,
    estimated_signal_return_percent,
)
from indicator_follow_signal_validation_presentation import filter_rows_for_entry
from routines.지표추종매매.routine_validation_trace import ValidationTraceObserver


class SellPriceValidationNormalizationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.project_root = Path(__file__).resolve().parents[1]
        cls.routine_dir = cls.project_root / "routines" / "지표추종매매"
        cls.rules_path = cls.routine_dir / "rules.json"
        cls.rules = json.loads(cls.rules_path.read_text(encoding="utf-8"))
        cls.stock = ValidationStockRef("005930", "삼성전자")

    def setUp(self):
        self.widgets = []

    def tearDown(self):
        for widget in self.widgets:
            try:
                widget.close()
                widget.deleteLater()
            except RuntimeError:
                pass
        self.app.processEvents()

    def _dialog(self, mode="registration"):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "rules.json"
        path.write_bytes(self.rules_path.read_bytes())
        kwargs = {
            "rules_path": path,
            "routine_path": self.routine_dir,
            "routine_name": "지표추종매매",
            "definition_id": "indicator_follow",
            "settings_mode": mode,
        }
        if mode == "edit":
            kwargs["instance_id"] = "INSTANCE-PRICE-NORMALIZATION"
        with patch.object(dialog_module.QTimer, "singleShot"):
            dialog = IndicatorFollowRoutineSettingsDialog(**kwargs)
        self.widgets.append(dialog)
        return dialog, path

    @staticmethod
    def _resolved_state(source):
        state = deepcopy(source)
        conditions = state["sell_ui"]["signal_conditions"]
        for group_name in ("condition_a", "condition_b", "condition_c"):
            conditions[group_name]["gap_left_combo"] = "평단가"
            conditions[group_name]["gap_right_combo"] = "현재가"
        return state

    @staticmethod
    def _entry(side, index, signal, close_time=None, trace=None):
        time = close_time or f"2026091409{index:02d}00"
        return ValidationReplayEntry(
            evaluation_side=side,
            evaluation_index=index,
            evaluation_time=time,
            signal=signal,
            reason="fixture",
            signal_index=index if signal else None,
            signal_time=time if signal else None,
            delay_bar=0,
            matched_groups=[],
            details=[],
            trace=trace or {"conditions": [], "groups": [], "aggregations": []},
        )

    def test_six_sell_price_combos_exclude_order_price_and_preserve_reselection(self):
        for mode in ("registration", "edit"):
            with self.subTest(mode=mode):
                dialog, path = self._dialog(mode)
                file_before = path.read_bytes()
                for group_name in "abc":
                    for side in ("left", "right"):
                        combo = getattr(
                            dialog,
                            f"sell_signal_condition_{group_name}_gap_{side}_combo",
                        )
                        self.assertEqual(
                            ["현재가", "평단가"],
                            [combo.itemText(index) for index in range(combo.count())],
                        )
                        self.assertNotIn("주문가", [
                            combo.itemText(index) for index in range(combo.count())
                        ])
                collected = dialog.collect_indicator_follow_ui_state()
                self.assertEqual(3, len(sell_price_selection_issues(collected)))
                self.assertEqual(
                    "가격 기준 재선택 필요",
                    dialog.sell_signal_condition_a_gap_left_combo.placeholderText(),
                )
                self.assertEqual(
                    "주문가",
                    collected["sell_ui"]["signal_conditions"]["condition_a"]["gap_left_combo"],
                )
                signal_payloads = []
                dialog.signal_validation_requested.connect(signal_payloads.append)
                dialog.basic_signal_interval_combo.setCurrentText("3")
                with patch.object(dialog_module.QMessageBox, "warning") as warning:
                    seed = dialog._handle_signal_validation_clicked()
                warning.assert_not_called()
                self.assertIsInstance(seed, IndicatorFollowSignalValidationSeed)
                self.assertEqual([seed], signal_payloads)
                self.assertEqual(3, seed.settings_snapshot.to_dict()["bar"]["bar_minutes"])
                self.assertEqual(
                    "주문가",
                    seed.to_ui_state()["sell_ui"]["signal_conditions"][
                        "condition_a"
                    ]["gap_left_combo"],
                )
                registration_result = dialog.build_registration_rules_from_current_ui_state()
                self.assertFalse(registration_result["success"])
                self.assertIn("재선택", " ".join(
                    registration_result.get("internal_blocked_reasons", [])
                    + [registration_result.get("internal_error", "")]
                ))
                self.assertEqual(file_before, path.read_bytes())

                self.assertIn("주문가", [
                    dialog.sell_a_perform1_single_combo.itemText(index)
                    for index in range(dialog.sell_a_perform1_single_combo.count())
                ])
                self.assertIn("주문가", [
                    dialog.buy_cycle_order_combo.itemText(index)
                    for index in range(dialog.buy_cycle_order_combo.count())
                ])

                valid_state = deepcopy(collected)
                for group_name in "abc":
                    condition = valid_state["sell_ui"]["signal_conditions"][
                        f"condition_{group_name}"
                    ]
                    condition["gap_left_combo"] = "평단가"
                    condition["gap_right_combo"] = "현재가"
                load_result = dialog.apply_indicator_follow_ui_state(valid_state)
                self.assertFalse([
                    item for item in load_result["skipped"]
                    if "gap_" in str(item.get("name") or "")
                ])
                self.assertEqual((), sell_price_selection_issues(
                    dialog.collect_indicator_follow_ui_state()
                ))

    def test_unresolved_price_is_entry_only_but_blocks_run_and_apply_payloads(self):
        unresolved = deepcopy(self.rules["indicator_follow_ui_state"]["state"])
        seed = IndicatorFollowSignalValidationSeed(
            ValidationSettingsSnapshot(self.rules), unresolved
        )
        unresolved["sell_ui"]["signal_conditions"]["condition_a"][
            "gap_left_combo"
        ] = "현재가"
        self.assertEqual(
            "주문가",
            seed.to_ui_state()["sell_ui"]["signal_conditions"]["condition_a"][
                "gap_left_combo"
            ],
        )
        with self.assertRaisesRegex(ValueError, "재선택"):
            IndicatorFollowSignalValidationApplyPayload(unresolved)
        with self.assertRaisesRegex(ValueError, "재선택"):
            build_signal_validation_snapshot(self.rules, ui_state=unresolved)

        dialog, _path = self._dialog()
        before = dialog.collect_indicator_follow_ui_state()
        changed = deepcopy(before)
        changed["basic"]["buy_signal_expr_line"] = "D"
        result = dialog.apply_signal_validation_ui_state(changed)
        self.assertTrue(result["skipped"])
        self.assertEqual(before, dialog.collect_indicator_follow_ui_state())

    def test_unresolved_window_defers_initial_run_and_apply_until_reselected(self):
        unresolved = deepcopy(self.rules["indicator_follow_ui_state"]["state"])
        seed = IndicatorFollowSignalValidationSeed(
            ValidationSettingsSnapshot(self.rules), unresolved
        )
        with patch.object(dialog_module.QTimer, "singleShot"):
            window = IndicatorFollowSignalValidationWindow(self.stock, seed)
        self.widgets.append(window)
        run_requests = []
        apply_payloads = []
        window.validation_run_requested.connect(run_requests.append)
        window.settings_apply_requested.connect(apply_payloads.append)

        self.assertIsNone(window.request_initial_validation())
        self.assertEqual([], run_requests)
        self.assertTrue(window.run_validation_button.isEnabled())
        self.assertIn("현재가 또는 평단가", window.validation_status_label.text())
        self.assertIsNone(window._request_settings_apply())
        self.assertEqual([], apply_payloads)

        window.sell_signal_condition_a_gap_left_combo.setCurrentText("평단가")
        self.assertIsNone(window._request_validation())
        self.assertEqual([], run_requests)
        window.sell_signal_condition_b_gap_left_combo.setCurrentText("현재가")
        self.assertIsNone(window._request_validation())
        self.assertEqual([], run_requests)
        window.sell_signal_condition_c_gap_left_combo.setCurrentText("평단가")

        request = window._request_validation()
        self.assertIsNotNone(request)
        self.assertEqual([request], run_requests)
        payload = window._request_settings_apply()
        self.assertIsNotNone(payload)
        self.assertEqual([payload], apply_payloads)

    def test_new_sell_price_defaults_are_average_to_current_and_v2_can_run(self):
        class FreshDialog(IndicatorFollowRoutineSettingsDialog):
            def load_rules(self):
                self.rules_data = {}
                self.rules = {}

        with patch.object(dialog_module.QTimer, "singleShot"):
            dialog = FreshDialog(
                rules_path=Path("unused-rules.json"),
                routine_path=self.routine_dir,
                routine_name="지표추종매매",
                definition_id="indicator_follow",
                settings_mode="registration",
            )
        self.widgets.append(dialog)
        for group_name in "abc":
            left = getattr(dialog, f"sell_signal_condition_{group_name}_gap_left_combo")
            right = getattr(dialog, f"sell_signal_condition_{group_name}_gap_right_combo")
            self.assertEqual("평단가", left.currentText())
            self.assertEqual("현재가", right.currentText())
            self.assertFalse(left.property("indicatorFollowUnresolvedSellPriceBasis"))
            for combo in (left, right):
                self.assertNotIn(
                    "주문가", [combo.itemText(i) for i in range(combo.count())]
                )

        fresh_state = dialog.collect_indicator_follow_ui_state()
        self.assertEqual((), sell_price_selection_issues(fresh_state))
        seed = IndicatorFollowSignalValidationSeed(
            ValidationSettingsSnapshot(self.rules), fresh_state
        )
        with patch.object(dialog_module.QTimer, "singleShot"):
            window = IndicatorFollowSignalValidationWindow(self.stock, seed)
        self.widgets.append(window)
        for group_name in "abc":
            self.assertEqual(
                "평단가",
                getattr(
                    window,
                    f"sell_signal_condition_{group_name}_gap_left_combo",
                ).currentText(),
            )
            self.assertEqual(
                "현재가",
                getattr(
                    window,
                    f"sell_signal_condition_{group_name}_gap_right_combo",
                ).currentText(),
            )
        self.assertIsNotNone(window.request_initial_validation())

    def test_sell_price_combos_keep_operands_distinct_in_registration_edit_and_v2(self):
        for mode in ("registration", "edit"):
            with self.subTest(mode=mode):
                dialog, _path = self._dialog(mode)
                for group_name in "abc":
                    left = getattr(
                        dialog,
                        f"sell_signal_condition_{group_name}_gap_left_combo",
                    )
                    right = getattr(
                        dialog,
                        f"sell_signal_condition_{group_name}_gap_right_combo",
                    )
                    left.setCurrentText("평단가")
                    right.setCurrentText("평단가")
                    self.assertEqual("현재가", left.currentText())
                    self.assertEqual("평단가", right.currentText())

                    left.setCurrentText("평단가")
                    self.assertEqual("평단가", left.currentText())
                    self.assertEqual("현재가", right.currentText())

        resolved = self._resolved_state(
            self.rules["indicator_follow_ui_state"]["state"]
        )
        seed = IndicatorFollowSignalValidationSeed(
            ValidationSettingsSnapshot(self.rules),
            resolved,
        )
        with patch.object(dialog_module.QTimer, "singleShot"):
            window = IndicatorFollowSignalValidationWindow(self.stock, seed)
        self.widgets.append(window)
        window.sell_signal_condition_a_gap_right_combo.setCurrentText("평단가")
        self.assertEqual(
            "현재가",
            window.sell_signal_condition_a_gap_left_combo.currentText(),
        )
        self.assertEqual(
            "평단가",
            window.sell_signal_condition_a_gap_right_combo.currentText(),
        )

    def test_mapper_rejects_same_sell_price_operand_pair(self):
        state = self._resolved_state(
            self.rules["indicator_follow_ui_state"]["state"]
        )
        conditions = state["sell_ui"]["signal_conditions"]
        for group in conditions.values():
            for key in tuple(group):
                if key.endswith("_check"):
                    group[key] = False
        conditions["condition_a"].update({
            "gap_check": True,
            "gap_left_combo": "평단가",
            "gap_right_combo": "평단가",
            "gap_direction_combo": "상향",
            "gap_value_line": "0.25",
            "gap_compare_combo": "이상",
        })
        state["basic"]["sell_signal_expr_line"] = "A"

        preview = mapper.build_engine_rules_preview_from_ui_state(
            state,
            self.rules,
        )
        self.assertTrue(any(
            "동일 가격 기준" in str(item)
            for item in preview["validation_warnings"]
        ))

    def test_v2_entry_only_ignores_reselection_warning_not_other_sell_errors(self):
        dialog, _path = self._dialog()

        class InvalidSellMapper:
            @staticmethod
            def build_engine_rules_preview_from_ui_state(_state, rules):
                return {
                    "validation_warnings": ["sell signal malformed"],
                    "preview_rules": rules,
                }

        with patch.object(
            dialog,
            "_load_indicator_follow_rule_mapper",
            return_value=InvalidSellMapper(),
        ):
            with self.assertRaisesRegex(ValueError, "sell signal malformed"):
                dialog.build_signal_validation_entry_snapshot_from_current_ui_state()

    def test_mapper_materializes_only_current_v2_sell_candidates(self):
        state = self._resolved_state(
            self.rules["indicator_follow_ui_state"]["state"]
        )
        preview = mapper.build_engine_rules_preview_from_ui_state(state, self.rules)
        self.assertFalse([
            item for item in preview["validation_warnings"] if "가격 기준" in item
        ])
        projected = project_signal_validation_rules(
            preview["preview_rules"], ui_state=state
        )
        signals = projected["sell"]["signals"]
        self.assertEqual(
            {"ui_condition_a", "ui_condition_b", "ui_condition_c"},
            set(signals),
        )
        self.assertNotIn("macd_sell", signals)
        self.assertNotIn("profit_rate_sell", signals)
        self.assertNotIn("indicator_follow_rule_preview", projected)
        self.assertIn(
            "AVG_PRICE",
            json.dumps(signals, ensure_ascii=False),
        )

    def test_price_comparison_uses_left_as_basis_at_point_two_five_boundary(self):
        state = self._resolved_state(
            self.rules["indicator_follow_ui_state"]["state"]
        )
        conditions = state["sell_ui"]["signal_conditions"]
        for group in conditions.values():
            for key in tuple(group):
                if key.endswith("_check"):
                    group[key] = False
        a = conditions["condition_a"]
        a.update({
            "gap_check": True,
            "gap_left_combo": "평단가",
            "gap_right_combo": "현재가",
            "gap_direction_combo": "상향",
            "gap_value_line": "0.25",
            "gap_compare_combo": "이상",
        })
        state["basic"]["sell_signal_expr_line"] = "A"
        preview = mapper.build_engine_rules_preview_from_ui_state(state, self.rules)
        projected = project_signal_validation_rules(preview["preview_rules"], ui_state=state)
        signal = projected["sell"]["signals"]["ui_condition_a"]
        gap = signal["groups"][0]["conditions"][0]
        self.assertEqual("CLOSE", gap["target"])
        self.assertEqual("AVG_PRICE", gap["compare_target"])

        from routines.지표추종매매.routine_macd_engine import (
            evaluate_indicator_follow_routine,
        )

        def evaluate(close):
            observer = ValidationTraceObserver()
            result = evaluate_indicator_follow_routine(
                [{"close": 10000.0}, {"close": 10000.0}, {"close": close}],
                projected,
                {
                    "_indicator_follow_evaluate_side": "SELL",
                    "average_price_series": [10000.0, 10000.0, 10000.0],
                    "average_price": 10000.0,
                    "decision_trace_observer": observer,
                },
            )
            return result, observer.snapshot()

        passed, trace = evaluate(10030.0)
        failed, _ = evaluate(10020.0)
        self.assertEqual("SELL", passed.signal)
        self.assertIsNone(failed.signal)
        aggregation = next(
            item["payload"] for item in trace["aggregations"]
            if item.get("side") == "SELL"
        )
        self.assertEqual({"A": True}, aggregation["ui_expression_values"])
        self.assertTrue(aggregation["ui_expression_result"])

        entry = self._entry("SELL", 2, "SELL", trace=trace)
        rows = filter_rows_for_entry(entry, state)
        price_row = next(row for row in rows if row.condition == "A·가격비교")
        self.assertIn("평단가", price_row.actual)
        self.assertIn("현재가", price_row.actual)
        self.assertIn("0.3000%", price_row.actual)
        expression_row = next(row for row in rows if row.condition == "적용 조합식")
        self.assertEqual("통과", expression_row.result)

    def test_actual_replay_uses_segment_average_and_excludes_same_bar_buy(self):
        rules = {
            "bar": {"bar_minutes": 5, "buy_delay_bar": 0, "sell_delay_bar": 0},
            "buy": {
                "groups": [{
                    "enabled": True,
                    "name": "always_buy",
                    "conditions": [{
                        "enabled": True,
                        "target": "CLOSE",
                        "operator": ">",
                        "value": 0,
                    }],
                }],
            },
            "sell": {
                "signal_logic": "OR",
                "signals": {
                    "ui_condition_a": {
                        "enabled": True,
                        "order_delay_bars": 0,
                        "groups": [{
                            "enabled": True,
                            "name": "condition_a",
                            "conditions": [{
                                "enabled": True,
                                "expression_id": "GAP_0",
                                "target": "CLOSE",
                                "operator": "PERCENT_GAP",
                                "compare_target": "AVG_PRICE",
                                "direction": "UP",
                                "compare_mode": "GTE",
                                "value": 0.25,
                            }],
                        }],
                    },
                },
            },
        }
        snapshot = ValidationSettingsSnapshot(rules)
        session = ValidationSession(
            ValidationRequest(self.stock, snapshot, 5),
            operation_active_reader=lambda: False,
        )
        closes = [100.0, 101.0, 104.0, 110.0, 120.0, 124.0]
        historical = ValidationHistoricalSnapshot(
            stock=self.stock,
            timeframe_minutes=5,
            requested_count=len(closes),
            request_id="SEGMENT-AVERAGE",
            rows=[
                {
                    "체결시간": f"2026091409{index:02d}00",
                    "시가": str(close),
                    "고가": str(close),
                    "저가": str(close),
                    "현재가": str(close),
                    "거래량": "1",
                }
                for index, close in reversed(list(enumerate(closes)))
            ],
        )
        result = ValidationHistoricalReplay(session).evaluate_with_context(
            historical,
            context_provider=build_validation_average_price_context,
        )
        self.assertTrue(result.ok, result)
        entries = result.snapshot.to_entries()
        by_index = {
            index: [entry for entry in entries if entry.evaluation_index == index]
            for index in range(len(closes))
        }
        self.assertEqual(
            {"BUY", "SELL"},
            {entry.signal for entry in by_index[3] if entry.signal},
        )
        sell_one = next(entry for entry in by_index[3] if entry.evaluation_side == "SELL")
        sell_two = next(entry for entry in by_index[5] if entry.evaluation_side == "SELL")
        self.assertEqual(104.0, sell_one.trace["evaluation_context"]["estimated_average_price"])
        self.assertEqual(120.0, sell_two.trace["evaluation_context"]["estimated_average_price"])
        self.assertEqual([4], sell_two.trace["evaluation_context"]["contributing_buy_indexes"])

    def test_estimated_return_aggregates_completed_sell_segments(self):
        candles = [
            {
                "time": f"2026091409{index:02d}00",
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1,
            }
            for index, close in enumerate((100.0, 110.0, 120.0, 130.0, 140.0))
        ]
        entries = [
            self._entry("BUY", 0, "BUY"),
            self._entry("BUY", 1, "BUY"),
            self._entry("SELL", 2, "SELL"),
            self._entry("BUY", 2, "BUY"),
            self._entry("BUY", 3, "BUY"),
            self._entry("SELL", 4, "SELL"),
        ]
        replay = ValidationReplaySnapshot(
            stock=self.stock,
            timeframe_minutes=5,
            settings_hash="hash",
            historical_request_id="RETURN-SEGMENT",
            evaluated_start_index=0,
            evaluated_end_index=4,
            dropped_raw_rows_count=0,
            candles=candles,
            entries=entries,
        )
        self.assertAlmostEqual(
            (30.0 + 10.0) / (210.0 + 130.0) * 100.0,
            estimated_signal_return_percent(replay),
        )

    def test_same_bar_buy_after_sell_does_not_create_a_reusable_average(self):
        context = build_validation_average_price_context(
            3,
            "SELL",
            [
                {"close": 100.0},
                {"close": 110.0},
                {"close": 120.0},
                {"close": 130.0},
            ],
            [
                self._entry("BUY", 0, "BUY"),
                self._entry("SELL", 2, "SELL"),
                self._entry("BUY", 2, "BUY"),
            ],
        )
        self.assertIsNone(context["average_price"])
        self.assertEqual(
            [],
            context["validation_trace_context"]["contributing_buy_indexes"],
        )


if __name__ == "__main__":
    unittest.main()
