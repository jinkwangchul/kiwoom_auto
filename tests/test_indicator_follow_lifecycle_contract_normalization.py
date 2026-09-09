# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from importlib.util import module_from_spec, spec_from_file_location
import json
import inspect
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QComboBox, QLineEdit

from execution_buy_recovery import inspect_buy_recovery_generations
from execution_signal_ownership_guard import signal_dispatch_block_reasons
from execution_unfilled_cancel_eligibility import inspect_unfilled_cancel_eligibility
import gui_auto_trade_timer
import gui_indicator_follow_routine_settings_dialog as dialog_module
from routine_signal_probe import _requires_base_bar_entry_projection
from routine_signal_consumer import apply_duplicate_signal_priority, block_signal_pre_dispatch_orders


ROOT = Path(__file__).resolve().parents[1]
ROUTINE_DIR = next((ROOT / "routines").glob("*/routine_rule_mapper.py")).parent


def _load(name: str, filename: str):
    spec = spec_from_file_location(name, ROUTINE_DIR / filename)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write(path: Path, field: str, values: list[dict]) -> None:
    path.write_text(json.dumps({field: values}, ensure_ascii=False), encoding="utf-8")


class SituationAndExitUiContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        rules_path = Path(self.temp.name) / "rules.json"
        rules_path.write_bytes((ROUTINE_DIR / "rules.json").read_bytes())
        self.dialog = dialog_module.IndicatorFollowRoutineSettingsDialog(rules_path=rules_path)
        self.mapper = _load("lifecycle_mapper", "routine_rule_mapper.py")

    def tearDown(self) -> None:
        self.dialog.close()
        self.dialog.deleteLater()
        self.temp.cleanup()

    def _state(self) -> dict:
        return self.dialog.collect_indicator_follow_ui_state()

    def _preview(self, state: dict | None = None, current: dict | None = None) -> dict:
        return self.mapper.build_engine_rules_preview_from_ui_state(
            state or self._state(),
            current or {
                "bar": {"bar_minutes": 1},
                "buy": {"groups": [{"conditions": []}]},
                "sell": {"signals": {}},
                "indicators": {"rsi": {"period": 14}},
            },
        )

    def test_unfilled_and_price_are_mutually_exclusive_and_two_price_rows_round_trip(self) -> None:
        self.assertFalse(hasattr(self.dialog, "buy_situation_response_type_combo"))
        self.dialog.buy_situation_response_unfilled_enabled_check.setChecked(False)
        self.dialog.buy_situation_response_price_enabled_check.setChecked(False)
        self.dialog.buy_situation_response_setting2_left_combo.setCurrentText("무설정")
        self.assertFalse(self.dialog.buy_situation_response_unfilled_enabled_check.isChecked())
        self.assertFalse(self.dialog.buy_situation_response_price_enabled_check.isChecked())
        self.assertEqual(
            "무설정",
            self.dialog.buy_situation_response_setting2_left_combo.currentText(),
        )
        self.assertFalse(self.dialog.buy_situation_response_setting2_enabled_check.isChecked())
        self.assertEqual(0, self.dialog.buy_situation_response_setting2_detail_stack.currentIndex())

        with (
            mock.patch.object(dialog_module.QMessageBox, "warning") as warning,
            mock.patch.object(dialog_module.QMessageBox, "critical") as critical,
            mock.patch.object(dialog_module, "show_toast") as toast,
        ):
            self.dialog.buy_situation_response_unfilled_enabled_check.setChecked(True)
            self.assertTrue(self.dialog.buy_situation_response_unfilled_enabled_check.isChecked())
            self.assertFalse(self.dialog.buy_situation_response_price_enabled_check.isChecked())
            self.dialog.buy_situation_response_unfilled_enabled_check.setChecked(False)
            self.assertFalse(self.dialog.buy_situation_response_unfilled_enabled_check.isChecked())
            self.assertFalse(self.dialog.buy_situation_response_price_enabled_check.isChecked())
            self.dialog.buy_situation_response_unfilled_enabled_check.setChecked(True)

            self.dialog.buy_situation_response_price_enabled_check.setChecked(True)
            self.assertFalse(self.dialog.buy_situation_response_unfilled_enabled_check.isChecked())
            self.assertTrue(self.dialog.buy_situation_response_price_enabled_check.isChecked())
            self.dialog.buy_situation_response_price_enabled_check.setChecked(False)
            self.assertFalse(self.dialog.buy_situation_response_unfilled_enabled_check.isChecked())
            self.assertFalse(self.dialog.buy_situation_response_price_enabled_check.isChecked())
            self.dialog.buy_situation_response_price_enabled_check.setChecked(True)
            self.dialog.buy_situation_response_unfilled_enabled_check.setChecked(True)
            self.assertTrue(self.dialog.buy_situation_response_unfilled_enabled_check.isChecked())
            self.assertFalse(self.dialog.buy_situation_response_price_enabled_check.isChecked())
            self.dialog.buy_situation_response_price_enabled_check.setChecked(True)
            warning.assert_not_called()
            critical.assert_not_called()
            toast.assert_not_called()
        self.dialog.buy_situation_response_setting2_left_combo.setCurrentText("주문가")
        self.assertTrue(self.dialog.buy_situation_response_setting2_enabled_check.isChecked())
        self.assertEqual(1, self.dialog.buy_situation_response_setting2_detail_stack.currentIndex())
        self.dialog.buy_situation_response_setting1_direction_combo.setCurrentText("상하")
        self.dialog.buy_situation_response_setting1_compare_combo.setCurrentText("이탈")
        self.dialog.buy_situation_response_setting1_ratio_line.setText("0.73")
        expected = deepcopy(self._state()["buy_ui"]["situation"])

        self.dialog.buy_situation_response_price_enabled_check.setChecked(False)
        self.dialog.buy_situation_response_setting2_left_combo.setCurrentText("무설정")
        applied = self.dialog.apply_indicator_follow_ui_state({
            "buy_ui": {"situation": expected}
        })

        self.assertFalse(applied["sync_errors"])
        actual = self._state()["buy_ui"]["situation"]
        self.assertEqual(expected, actual)
        self.assertFalse(actual["unfilled_enabled_check"])
        self.assertTrue(actual["price_enabled_check"])
        self.assertTrue(actual["setting1_enabled_check"])
        self.assertTrue(actual["setting2_enabled_check"])
        self.assertEqual("주문가", actual["setting2_left_combo"])

        self.dialog.buy_situation_response_setting2_left_combo.setCurrentText("무설정")
        disabled = self._state()["buy_ui"]["situation"]
        self.assertFalse(disabled["setting2_enabled_check"])
        self.assertEqual(0, self.dialog.buy_situation_response_setting2_detail_stack.currentIndex())
        self.assertTrue(self.dialog.buy_situation_response_setting2_left_combo.isEnabled())
        self.assertIn(
            "color: #808080",
            self.dialog.buy_situation_response_setting2_left_combo.styleSheet(),
        )
        self.assertNotIn(
            "background-color",
            self.dialog.buy_situation_response_setting2_left_combo.styleSheet(),
        )
        self.assertFalse(self.dialog.buy_situation_response_setting2_right_combo.isEnabled())
        placeholder = self.dialog.buy_situation_response_setting2_detail_stack.currentWidget()
        placeholder_combos = placeholder.findChildren(QComboBox)
        placeholder_lines = placeholder.findChildren(QLineEdit)
        self.assertEqual(4, len(placeholder_combos))
        self.assertEqual(["-"] * 4, [combo.currentText() for combo in placeholder_combos])
        self.assertTrue(all(not combo.isEnabled() for combo in placeholder_combos))
        self.assertEqual(["-"], [line.text() for line in placeholder_lines])
        self.assertTrue(all(not line.isEnabled() for line in placeholder_lines))
        self.assertTrue(all("background-color" not in combo.styleSheet() for combo in placeholder_combos))
        self.assertNotIn("background-color", placeholder_lines[0].styleSheet())

    def test_explicit_both_situation_modes_apply_normalizes_to_one_mode(self) -> None:
        applied = self.dialog.apply_indicator_follow_ui_state({
            "buy_ui": {
                "situation": {
                    "unfilled_enabled_check": True,
                    "price_enabled_check": True,
                    "setting1_enabled_check": True,
                    "setting1_left_combo": "주문가",
                }
            }
        })

        self.assertFalse(applied["sync_errors"])
        actual = self._state()["buy_ui"]["situation"]
        self.assertEqual(
            1,
            sum(bool(actual[key]) for key in ("unfilled_enabled_check", "price_enabled_check")),
        )

    def test_raw_both_situation_modes_are_rejected_before_candidate(self) -> None:
        state = self._state()
        situation = state["buy_ui"]["situation"]
        situation.update({
            "unfilled_enabled_check": True,
            "price_enabled_check": True,
            "setting1_enabled_check": True,
            "setting1_left_combo": "주문가",
            "setting1_action_combo": "일괄취소",
        })

        preview = self._preview(state)
        execution = preview["preview_rules"]["indicator_follow_rule_preview"]["candidates"].get(
            "execution", {}
        )
        self.assertNotIn("base", execution)
        self.assertIn(
            "buy situation response modes are mutually exclusive",
            preview["validation_warnings"],
        )

    def test_price_response_slots_allow_independent_directions_and_actions(self) -> None:
        cases = (
            ("상향", "이상", "일괄취소", "하향", "이하", "매수리셋"),
            ("상향", "이상", "일괄취소", "상향", "이상", "매수리셋"),
            ("하향", "이하", "매수리셋", "하향", "이하", "일괄취소"),
        )
        for first_direction, first_compare, first_action, second_direction, second_compare, second_action in cases:
            with self.subTest(directions=(first_direction, second_direction)):
                state = self._state()
                situation = state["buy_ui"]["situation"]
                situation.update({
                    "unfilled_enabled_check": False,
                    "price_enabled_check": True,
                    "setting1_enabled_check": True,
                    "setting2_enabled_check": True,
                    "setting1_left_combo": "평단가",
                    "setting1_right_combo": "현재가",
                    "setting1_direction_combo": first_direction,
                    "setting1_ratio_line": "0.15",
                    "setting1_compare_combo": first_compare,
                    "setting1_action_combo": first_action,
                    "setting2_left_combo": "주문가",
                    "setting2_right_combo": "평단가",
                    "setting2_direction_combo": second_direction,
                    "setting2_ratio_line": "0.10",
                    "setting2_compare_combo": second_compare,
                    "setting2_action_combo": second_action,
                })
                preview = self._preview(state)
                execution = preview["preview_rules"]["indicator_follow_rule_preview"][
                    "candidates"
                ]["execution"]
                policies = execution["base"]["value"]["buy_price_response_policies"]
                self.assertEqual(["SETTING1", "SETTING2"], [item["slot"] for item in policies])
                self.assertEqual(
                    [
                        ("AVG_PRICE", "CURRENT_PRICE", first_action),
                        ("ORDER_PRICE", "AVG_PRICE", second_action),
                    ],
                    [
                        (
                            item["left_source"],
                            item["right_source"],
                            {"RESET": "매수리셋", "CANCEL_BATCH": "일괄취소"}[item["action"]],
                        )
                        for item in policies
                    ],
                )

    def test_price_response_slots_still_require_one_valid_enabled_slot(self) -> None:
        cases = (
            (
                {"setting1_enabled_check": False, "setting2_enabled_check": False},
                "buy situation price response requires at least one enabled slot",
            ),
            (
                {"setting1_ratio_line": "invalid", "setting2_enabled_check": False},
                "buy situation price slot SETTING1 is invalid",
            ),
            (
                {
                    "setting2_enabled_check": True,
                    "setting2_left_combo": "현재가",
                    "setting2_ratio_line": "invalid",
                },
                "buy situation price slot SETTING2 is invalid",
            ),
            (
                {
                    "setting1_action_combo": "지원하지않음",
                    "setting2_enabled_check": False,
                },
                "buy situation price slot SETTING1 is invalid",
            ),
        )
        for changes, expected_warning in cases:
            with self.subTest(expected_warning=expected_warning):
                state = self._state()
                situation = state["buy_ui"]["situation"]
                situation.update({
                    "unfilled_enabled_check": False,
                    "price_enabled_check": True,
                })
                situation.update(changes)
                preview = self._preview(state)
                execution = preview["preview_rules"]["indicator_follow_rule_preview"][
                    "candidates"
                ].get("execution", {})
                self.assertNotIn("base", execution)
                self.assertIn(expected_warning, preview["validation_warnings"])

    def test_exit_timeout_is_independent_and_three_conditions_use_or(self) -> None:
        self.dialog.buy_cycle_time_mode_combo.setCurrentText("다중시간")
        self.dialog.buy_exit_price_check.setChecked(True)
        self.dialog.buy_exit_count_check.setChecked(True)
        self.dialog.buy_exit_time_check.setChecked(True)
        self.dialog.buy_exit_time_line.setText("43")
        candidate = self._preview()["preview_rules"]["indicator_follow_rule_preview"]["candidates"]["execution"]["base"]
        policy = candidate["value"]["buy_exit_policy"]
        self.assertTrue(self.dialog.buy_exit_time_check.isEnabled())
        self.assertEqual("OR", policy["logic"])
        self.assertEqual({"PRICE", "COUNT", "TIME"}, {item["condition_type"] for item in policy["conditions"]})
        time_rule = next(item for item in policy["conditions"] if item["condition_type"] == "TIME")
        self.assertEqual("FIRST_RECOVERY_ENTERED_AT", time_rule["anchor"])

    def test_repeat_disabled_removes_existing_canonical_policy(self) -> None:
        state = self._state()
        state["buy_ui"]["repeat"]["apply_all_check"] = False
        current = {
            "bar": {"bar_minutes": 1},
            "buy": {"groups": [{"conditions": []}], "execution": {"repeat": {"apply_all": True}}},
            "sell": {"signals": {}},
            "indicators": {"rsi": {"period": 14}},
        }
        preview = self._preview(state, current)
        session = self.mapper.build_rule_approval_session(
            preview, {"buy.execution.repeat": "APPROVED"}
        )
        pipeline = self.mapper.build_rule_pipeline_preview(current, preview, session)
        operations = [item["operation"] for item in pipeline["patch_preview"]["patches"]]
        self.assertIn("remove_execution_policy", operations)
        execution = pipeline["apply_preview"]["applied_rules_preview"]["buy"].get("execution", {})
        self.assertNotIn("repeat", execution)


class RuntimeLifecycleContractTest(unittest.TestCase):
    def test_signal_ownership_arbitration_precedes_buy_completion_mutation(self) -> None:
        timer_source = inspect.getsource(gui_auto_trade_timer._process_pending_signal_pipeline)
        self.assertIn("evaluate_routine_lifecycle(", timer_source)
        self.assertNotIn("arbitrate_pending_signal_ownership(", timer_source)
        self.assertNotIn("reconcile_completed_buy_signal_processes(", timer_source)
        source = (ROUTINE_DIR / "routine_lifecycle.py").read_text(encoding="utf-8")
        arbitration_call = source.index("decisions = _signal_ownership_decisions(")
        completion_call = source.index("decisions.extend(_buy_completion_decisions(")
        first_lifecycle_inspector = source.index('(\"BUY_EXIT\", inspect_buy_repeat_exits(')

        self.assertLess(arbitration_call, completion_call)
        self.assertLess(completion_call, first_lifecycle_inspector)

    def test_ocr_zero_and_n_use_current_base_bar_entry_index(self) -> None:
        engine = _load("lifecycle_engine", "routine_macd_engine.py")
        candles = [{"close": value} for value in range(5)]
        self.assertTrue(_requires_base_bar_entry_projection({
            "buy": {"filters": {"ocr": {"order_delay_bars": 0}}}
        }))
        self.assertEqual(4, engine._delay_index(candles, 0))
        self.assertEqual(3, engine._delay_index(candles, 1))
        self.assertEqual(2, engine._delay_index(candles, 2))

    def test_same_tick_buy_sell_conflict_uses_latest_holding_fallback(self) -> None:
        sys.path.insert(0, str(ROUTINE_DIR))
        try:
            routine = _load("lifecycle_routine", "routine.py")
        finally:
            sys.path.remove(str(ROUTINE_DIR))

        signal_type = routine.evaluate_indicator_follow_routine.__globals__["RoutineSignal"]

        def both_true(_candles, _config, context):
            side = context.get("_indicator_follow_evaluate_side")
            return signal_type(side, f"{side} matched", [], [], 2, 0)

        base = {
            "candles": [
                {"close": 1, "bar_time": "2026-09-05T10:00:00"},
                {"close": 2, "bar_time": "2026-09-05T10:01:00"},
                {"close": 3, "bar_time": "2026-09-05T10:02:00"},
            ],
            "rules": {"enabled": True},
        }
        with mock.patch.object(routine, "evaluate_indicator_follow_routine", side_effect=both_true):
            no_holding = routine.evaluate({**base, "cycle": {"holding_qty": 0}})
            holding = routine.evaluate({**base, "cycle": {"holding_qty": 3}})
        self.assertEqual("BUY_AND_SELL_TRUE", no_holding["signal_conflict_evidence"]["conflict"])
        self.assertEqual("BUY_AND_SELL_TRUE", holding["signal_conflict_evidence"]["conflict"])
        self.assertEqual("BUY", no_holding["signal_conflict_evidence"]["selected_side"])
        self.assertEqual("SELL", holding["signal_conflict_evidence"]["selected_side"])

    def test_supersession_durably_blocks_only_pre_dispatch_children(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            queue = Path(temp) / "order_queue.json"
            _write(queue, "orders", [
                {"id": "Q1", "source_signal_id": "S1", "status": "EXECUTABLE", "order_action": "NEW"},
                {"id": "Q2", "source_signal_id": "S1", "status": "BROKER_ACCEPTED", "order_action": "NEW"},
                {"id": "Q3", "source_signal_id": "S2", "status": "EXECUTABLE", "order_action": "NEW"},
            ])
            result = block_signal_pre_dispatch_orders("S1", order_queue_path=queue)
            self.assertTrue(result["committed"], result)
            rows = json.loads(queue.read_text(encoding="utf-8"))["orders"]
            by_id = {row["id"]: row for row in rows}
            self.assertEqual("BLOCKED", by_id["Q1"]["status"])
            self.assertFalse(by_id["Q1"]["execution_enabled"])
            self.assertEqual("BROKER_ACCEPTED", by_id["Q2"]["status"])
            self.assertEqual("EXECUTABLE", by_id["Q3"]["status"])

    def test_batch_timeout_anchors_to_last_actual_submission(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            queue = Path(temp) / "order_queue.json"
            policy = {
                "policy": "CANCEL_PENDING_ORDER", "enabled": True, "action": "CANCEL",
                "scope": "BATCH", "timeout_ms": 10_000,
            }
            orders = []
            for index, accepted in ((1, "2026-09-05T10:00:00.000"), (2, "2026-09-05T10:05:00.000")):
                orders.append({
                    "id": f"O{index}", "execution_id": f"E{index}", "status": "BROKER_ACCEPTED",
                    "broker_order_no": f"B{index}", "broker_accepted_at": accepted,
                    "remaining_quantity": 1, "account_no": "ACC", "code": "005930", "side": "BUY",
                    "source_signal_id": "S1", "execution_process_id": "P1", "routine": "R1",
                    "execution_intent": {
                        "source_signal_id": "S1", "execution_process_id": "P1", "side": "BUY",
                        "child_sequence_index": index, "child_sequence_total": 2,
                        "unfilled_timeout_policy": policy,
                    },
                })
            _write(queue, "orders", orders)
            early = inspect_unfilled_cancel_eligibility(
                selected_account_no="ACC", now=datetime.fromisoformat("2026-09-05T10:05:05"),
                order_queue_path=queue,
            )
            due = inspect_unfilled_cancel_eligibility(
                selected_account_no="ACC", now=datetime.fromisoformat("2026-09-05T10:05:11"),
                order_queue_path=queue,
            )
            self.assertEqual([], early["proposals"])
            self.assertEqual(2, len(due["proposals"]))
            self.assertEqual({"LAST_CHILD_BROKER_ACCEPTED_AT"}, {item["timeout_anchor"] for item in due["proposals"]})

    def test_confirmed_buy_residual_recovery_preserves_identity_and_exact_quantity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            queue, positions, holdings, signals = (
                root / "order_queue.json", root / "positions.json",
                root / "broker_holdings.json", root / "routine_signals.json",
            )
            recovery_policy = {
                "scope": "SIGNAL_SCOPED_BUY_RECOVERY", "residual_only": True,
                "order_policy": {"hoga_mode": "SINGLE", "order_price_basis": "ORDER_PRICE", "hoga_up": 0, "hoga_down": 0},
                "point_policy": {"mode": "NONE"},
                "unfilled_timeout_policy": {
                    "policy": "CANCEL_PENDING_ORDER", "enabled": True,
                    "scope": "EACH", "configured_value": 5,
                    "configured_unit": "SECOND",
                },
                "buy_price_response_policies": [{
                    "slot": "SETTING1", "enabled": True,
                    "left_source": "ORDER_PRICE", "right_source": "CURRENT_PRICE",
                    "direction": "UP", "threshold_percent": 1.0,
                    "compare": ">=", "action": "CANCEL_BATCH",
                }],
            }
            intent = {
                "side": "BUY", "source_signal_id": "S1", "execution_process_id": "P1",
                "option_snapshot_hash": "OPT", "plan_generation": 1, "buy_round": 2,
                "execution_mode": "SINGLE_ORDER", "price_basis": "ORDER_PRICE", "price": 200,
                "buy_recovery_cycle_policy": recovery_policy,
            }
            original = {
                "id": "O1", "execution_id": "E1", "status": "CANCELED", "order_action": "NEW",
                "broker_order_no": "B1", "remaining_quantity": 3, "account_no": "ACC",
                "code": "005930", "side": "BUY", "source_signal_id": "S1",
                "execution_process_id": "P1", "execution_intent": intent,
            }
            cancel = {
                "id": "C1", "status": "CANCELED", "order_action": "CANCEL", "side": "BUY",
                "original_order_no": "B1", "original_order_effect_confirmed": True,
                "execution_process_id": "P1", "cancel_evidence": {
                    "trigger": "UNFILLED_TIMEOUT", "source_plan_generation": 1,
                    "execution_process_id": "P1",
                },
            }
            _write(queue, "orders", [original, cancel])
            _write(positions, "positions", [{"account_no": "ACC", "code": "005930", "quantity": 2}])
            _write(holdings, "holdings", [{"account_no": "ACC", "code": "005930", "holding_quantity": 2}])
            _write(signals, "signals", [{
                "id": "S1", "status": "PREVIEWED", "signal": "BUY", "code": "005930",
                "signal_timeframe_minutes": 1,
            }])
            result = inspect_buy_recovery_generations(
                selected_account_no="ACC", actionable_prices_by_code={"005930": 100},
                now=datetime.fromisoformat("2026-09-05T10:06:00"), order_queue_path=queue,
                positions_path=positions, holdings_path=holdings, signals_path=signals,
            )
            self.assertTrue(result["ok"], result)
            self.assertEqual(1, len(result["proposals"]))
            proposal = result["proposals"][0]
            generated = proposal["execution_intents"]
            self.assertEqual(3, sum(item["quantity"] for item in generated))
            self.assertEqual({"S1"}, {item["source_signal_id"] for item in generated})
            self.assertEqual({"P1"}, {item["execution_process_id"] for item in generated})
            self.assertEqual({2}, {item["buy_round"] for item in generated})
            self.assertEqual({2}, {item["plan_generation"] for item in generated})
            self.assertTrue(all(item["recovery_cycle"] is True for item in generated))
            self.assertTrue(all(item["unfilled_timeout_policy"]["enabled"] is True for item in generated))
            self.assertTrue(all(item["unfilled_timeout_policy"]["timeout_ms"] == 5_000 for item in generated))
            self.assertEqual(
                {"CANCEL_BATCH"},
                {item["buy_price_response_policies"][0]["action"] for item in generated},
            )

    def test_each_child_recovery_never_reuses_an_already_transferred_residual(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            queue, positions, holdings, signals = (
                root / "order_queue.json", root / "positions.json",
                root / "broker_holdings.json", root / "routine_signals.json",
            )
            policy = {
                "scope": "SIGNAL_SCOPED_BUY_RECOVERY", "residual_only": True,
                "order_policy": {"hoga_mode": "SINGLE", "order_price_basis": "ORDER_PRICE", "hoga_up": 0, "hoga_down": 0},
                "point_policy": {"mode": "NONE"},
                "unfilled_timeout_policy": {
                    "policy": "CANCEL_PENDING_ORDER", "enabled": True,
                    "scope": "EACH", "configured_value": 5, "configured_unit": "SECOND",
                },
                "buy_price_response_policies": [],
            }
            template = {
                "side": "BUY", "source_signal_id": "S1", "execution_process_id": "P1",
                "option_snapshot_hash": "OPT", "plan_generation": 1, "buy_round": 2,
                "execution_mode": "SINGLE_ORDER", "price_basis": "ORDER_PRICE", "price": 100,
                "buy_recovery_cycle_policy": policy,
            }
            first = {
                "id": "O1", "execution_id": "E1", "status": "CANCELED", "order_action": "NEW",
                "broker_order_no": "B1", "remaining_quantity": 3, "account_no": "ACC",
                "code": "005930", "side": "BUY", "source_signal_id": "S1",
                "execution_process_id": "P1", "execution_intent": deepcopy(template),
            }
            second = {
                **deepcopy(first), "id": "O2", "execution_id": "E2", "status": "BROKER_ACCEPTED",
                "broker_order_no": "B2", "remaining_quantity": 2,
            }
            cancel1 = {
                "id": "C1", "status": "CANCELED", "order_action": "CANCEL", "side": "BUY",
                "original_order_no": "B1", "original_order_effect_confirmed": True,
                "execution_process_id": "P1", "cancel_evidence": {
                    "trigger": "UNFILLED_TIMEOUT", "scope": "EACH",
                    "source_plan_generation": 1, "execution_process_id": "P1",
                },
            }
            _write(queue, "orders", [first, second, cancel1])
            _write(positions, "positions", [{"account_no": "ACC", "code": "005930", "quantity": 0}])
            _write(holdings, "holdings", [{"account_no": "ACC", "code": "005930", "holding_quantity": 0}])
            _write(signals, "signals", [{"id": "S1", "status": "PREVIEWED", "signal": "BUY", "code": "005930"}])

            first_result = inspect_buy_recovery_generations(
                selected_account_no="ACC", actionable_prices_by_code={"005930": 100},
                order_queue_path=queue, positions_path=positions,
                holdings_path=holdings, signals_path=signals,
            )
            self.assertEqual([3], [item["confirmed_residual_quantity"] for item in first_result["proposals"]])
            recovery_order = {
                "id": "R1", "status": "ORDER_QUEUED", "order_action": "NEW",
                "execution_process_id": "P1",
                "execution_intent": deepcopy(first_result["proposals"][0]["execution_intents"][0]),
            }
            second["status"] = "CANCELED"
            cancel2 = {
                **deepcopy(cancel1), "id": "C2", "original_order_no": "B2",
            }
            _write(queue, "orders", [first, second, cancel1, cancel2, recovery_order])
            second_result = inspect_buy_recovery_generations(
                selected_account_no="ACC", actionable_prices_by_code={"005930": 100},
                order_queue_path=queue, positions_path=positions,
                holdings_path=holdings, signals_path=signals,
            )
            self.assertEqual([2], [item["confirmed_residual_quantity"] for item in second_result["proposals"]])

    def test_trailing_priority_waits_for_cancel_effect_and_dispatch_guard_is_fail_closed(self) -> None:
        first = {"id": "S1", "created_at": "1", "routine_instance_id": "R1", "code": "005930", "signal": "BUY", "status": "PREVIEWED"}
        second = {"id": "S2", "created_at": "2", "routine_instance_id": "R1", "code": "005930", "signal": "SELL", "status": "PENDING", "signal_runtime_policy": {"duplicate_priority": "TRAILING"}}
        original = {"id": "O1", "source_signal_id": "S1", "execution_process_id": "P1", "status": "BROKER_ACCEPTED", "broker_order_no": "B1", "side": "BUY"}
        selected, summary = apply_duplicate_signal_priority(
            [second], all_signals=[first, second], orders=[original],
            cancel_requester=lambda *args, **kwargs: {"cancel_requested": 1},
            holding_consistency_reader=lambda _code: True,
            status_updater=lambda *args, **kwargs: {"ok": True},
            predispatch_blocker=lambda _signal: {"committed": True, "superseded_order_ids": []},
        )
        self.assertEqual([], selected)
        self.assertEqual(1, summary["cancel_requested"])

        cancel = {"id": "C1", "status": "CANCELED", "order_action": "CANCEL", "original_order_no": "B1", "original_order_effect_confirmed": True}
        selected, _ = apply_duplicate_signal_priority(
            [second], all_signals=[first, second], orders=[original, cancel],
            holding_consistency_reader=lambda _code: True,
            status_updater=lambda *args, **kwargs: {"ok": True},
            predispatch_blocker=lambda _signal: {"committed": True, "superseded_order_ids": []},
        )
        self.assertEqual(["S2"], [item["id"] for item in selected])

        with tempfile.TemporaryDirectory() as temp:
            signal_path = Path(temp) / "routine_signals.json"
            _write(signal_path, "signals", [{"id": "S1", "status": "PREVIEWED", "supersede_pending": True}])
            self.assertEqual(
                ["SIGNAL_PROCESS_SUPERSEDE_PENDING"],
                signal_dispatch_block_reasons({"source_signal_id": "S1"}, signals_path=signal_path),
            )
            self.assertEqual(
                ["SIGNAL_OWNERSHIP_IDENTITY_INVALID"],
                signal_dispatch_block_reasons({"source_signal_id": "S2"}, signals_path=signal_path),
            )
            _write(signal_path, "signals", [
                {"id": "S1", "status": "PREVIEWED", "supersede_pending": True},
                {"id": "S3", "status": "PREVIEWED"},
            ])
            self.assertEqual(
                [],
                signal_dispatch_block_reasons({"source_signal_id": "S3"}, signals_path=signal_path),
            )


if __name__ == "__main__":
    unittest.main()
