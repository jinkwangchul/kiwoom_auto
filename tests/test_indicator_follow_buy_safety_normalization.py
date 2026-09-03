from copy import deepcopy
import unittest
from unittest import mock

import routine_signal_consumer as consumer
import routine_signal_queue
from tests import test_indicator_follow_buy_execution_connection as buy_tests


class BuySafetyNormalizationTest(unittest.TestCase):
    def setUp(self):
        self.buy = buy_tests.IndicatorFollowBuyExecutionConnectionTest()
        self.routine = buy_tests._load_module("routine.py", "buy_safety_routine_test")
        self.mapper = buy_tests._load_module("routine_rule_mapper.py", "buy_safety_mapper_test")
        self.rules = self.buy._rules(price_basis="ORDER_PRICE")
        self.rules.update(principle={"execution_enabled": True}, safety={"real_order_allowed": True})

    def gate(self, subject, *, final=False):
        callback = (self.routine.evaluate_final_real_order_safety if final
                    else self.routine.evaluate_execution_admission)
        return callback(subject=subject, rules=self.rules, routine_identity={}, rules_identity="test")

    def test_invalid_ratio_plan_never_downgrades_for_met_or_unmet_condition_or_hoga(self):
        for current_price in (90, 100, 110):
            for hoga_mode in ("SINGLE", "MULTI"):
                with self.subTest(price=current_price, hoga=hoga_mode):
                    self.rules["buy"]["execution"]["base"].update(
                        point_mode="MULTI_RATIO", hoga_mode=hoga_mode, hoga_up=1, hoga_down=1,
                        ratio_left="ORDER_PRICE", ratio_right="CURRENT_PRICE",
                        ratio_direction="UP", ratio_value=1, ratio_compare=">=", ratio_count=0,
                    )
                    result = self.buy._build(rules=self.rules, actionable_price=current_price)
                    self.assertEqual("BUY_MULTI_RATIO_PLAN_INVALID" if hoga_mode == "SINGLE"
                                     else "BUY_MULTI_RATIO_HOGA_COMBINATION_NOT_IMPLEMENTED", result["reason"])
                    self.assertEqual("BLOCKED", result["status"])
                    self.assertIsNone(result["execution_intent"])
                    self.assertEqual([], result["execution_intents"])

    def test_unknown_modes_fail_closed_without_normalizing_into_single(self):
        for field, value in (("point_mode", "UNKNOWN"), ("point_mode", "multi_time"),
                             ("point_mode", "ACTIVE_BUY"), ("point_mode", []),
                             ("hoga_mode", "UNKNOWN"), ("execution_mode", "UNKNOWN")):
            with self.subTest(field=field, value=value):
                rules = deepcopy(self.rules)
                rules["buy"]["execution"]["base"][field] = value
                result = self.buy._build(rules=rules)
                self.assertEqual("BLOCKED", result["status"])
                self.assertIsNone(result["execution_intent"])

    def test_routine_result_has_no_signal_and_never_writes_signal_queue(self):
        self.rules["buy"]["execution"]["base"]["point_mode"] = "MULTI_RATIO"
        with mock.patch.object(self.routine, "evaluate_indicator_follow_routine"), mock.patch.object(
            self.routine, "signal_to_dict", return_value={"signal": "BUY"}
        ), mock.patch.object(routine_signal_queue, "_mutate_queue") as writer:
            result = self.routine.evaluate({
                "rules": self.rules, "cycle": self.buy._cycle(), "candles": [],
                "stock_config": {"trade_amount_type": "QUANTITY", "buy_qty": 10},
                "reference_price": 100, "routine_instance_id": "INSTANCE_A",
            })
            queued = routine_signal_queue.enqueue_routine_signal(
                result, routine="지표추종매매", code="005930", name="test",
            )
        self.assertIsNone(result["signal"])
        self.assertEqual("BUY_MULTI_RATIO_PLAN_INVALID", result["buy_execution_blocked_reason"])
        self.assertNotEqual("queued", queued["status"])
        writer.assert_not_called()

    def test_old_downgraded_signal_blocked_before_candidate_approval_queue_and_policy(self):
        # The old builder retained MULTI_RATIO only in frozen opaque options.
        intent = self.buy._build(rules=self.rules)["execution_intent"]
        intent["approved_execution_options"]["point_mode"] = "MULTI_RATIO"
        signal = {"id": "OLD_SIGNAL", "signal": "BUY", "routine_instance_id": "INSTANCE_A",
                  "execution_intent": intent}
        with mock.patch.object(consumer, "evaluate_routine_gate", side_effect=lambda **kw: self.gate(kw["subject"])), \
             mock.patch.object(consumer, "read_order_queue", return_value={"orders": []}), \
             mock.patch.object(consumer, "signal_to_order_candidates") as candidate, \
             mock.patch.object(consumer, "evaluate_order_approval") as approval, \
             mock.patch.object(consumer, "append_order_candidates") as writer, \
             mock.patch.object(consumer, "_apply_operation_policy_to_created_orders") as policy:
            result = consumer._build_order_queue_candidates_for_signals([signal], apply_approval=True)
        self.assertEqual(0, result["orders_created"])
        for callback in (candidate, approval, writer, policy):
            callback.assert_not_called()
        self.assertFalse(self.gate(signal, final=True)["allowed"])

    def test_current_ratio_rules_block_old_single_intent_at_both_gates(self):
        intent = self.buy._build(rules=self.rules)["execution_intent"]
        self.rules["buy"]["execution"]["base"]["point_mode"] = "MULTI_RATIO"
        for final in (False, True):
            self.assertEqual("BUY_MULTI_RATIO_PLAN_INVALID",
                             self.gate({"execution_intent": intent}, final=final)["reason"])

    def test_all_children_are_checked_and_ratio_child_cannot_hide_behind_single(self):
        intent = self.buy._build(rules=self.rules)["execution_intent"]
        ratio = {**deepcopy(intent), "execution_mode": "MULTI_RATIO", "child_kind": "RATIO_SLICE"}
        result = self.gate({"execution_intent": intent, "execution_intents": [intent, ratio]})
        self.assertFalse(result["allowed"])

    def test_active_buy_repeat_blocked_but_does_not_disable_supported_initial_buy(self):
        self.rules["buy"]["execution"]["repeat"]["detail_mode"] = "ACTIVE_BUY"
        base = self.buy._build(rules=self.rules)
        repeat = self.buy._build(rules=self.rules, cycle=self.buy._cycle(1))
        self.assertEqual("READY", base["status"])
        self.assertEqual("ACTIVE_BUY_NOT_IMPLEMENTED", repeat["reason"])
        for final in (False, True):
            self.assertFalse(self.gate({"side": "BUY", "buy_round": 2}, final=final)["allowed"])

    def test_ui_only_and_reserved_policies_are_not_promoted_to_execution(self):
        baseline = self.buy._build(rules=self.rules)["execution_intent"]
        for section in ("situation", "cycle", "exit", "additional", "price_compare"):
            with self.subTest(section=section):
                rules = deepcopy(self.rules)
                rules["indicator_follow_ui_state"] = {"state": {"buy_ui": {
                    section: {"check": True, "type_combo": "미체결", "action_combo": "매수리셋",
                              "active_buy_check": True, "buy_exit_count_check": True,
                              "buy_exit_count_line": "1"}}}}
                rules["buy_management"] = {"enabled": False, "status": "reserved",
                    "average_price_control": {"enabled": False, "threshold_percent": 1}}
                result = self.buy._build(rules=rules)
                # This is the independent supported BUY signal, not an action
                # generated from any saved UI-only timeout/reset/exit policy.
                self.assertEqual(baseline, result["execution_intent"])
                self.assertNotIn("unfilled_timeout_policy", result["execution_intent"])
                with mock.patch.object(self.routine, "evaluate_indicator_follow_routine"), mock.patch.object(
                    self.routine, "signal_to_dict", return_value={"signal": None}
                ):
                    result = self.routine.evaluate({"rules": rules, "cycle": self.buy._cycle()})
                self.assertNotIn("execution_intent", result)

    def test_apply_all_remains_bulk_application_not_a_new_repeat_off_contract(self):
        ui_source = (buy_tests.PROJECT_ROOT / "gui_indicator_follow_buy_method_controls.py").read_text(encoding="utf-8")
        self.assertIn('DetailToggleCheckBox("기본매수설정을 전체매수에 적용")', ui_source)
        before = deepcopy(self.rules)
        preview = self.mapper.build_engine_rules_preview_from_ui_state(
            {"buy_ui": {"repeat": {"apply_all_check": False}}}, self.rules,
        )["preview_rules"]
        self.assertEqual(before, self.rules)
        self.assertEqual(before["buy"]["execution"]["repeat"], preview["buy"]["execution"]["repeat"])
        self.assertNotIn("repeat", preview["indicator_follow_rule_preview"]["candidates"].get("execution", {}))

    def test_sell_and_cancel_are_not_blocked_by_buy_unsupported_policy(self):
        self.rules["buy"]["execution"]["base"]["point_mode"] = "MULTI_RATIO"
        for final in (False, True):
            for subject in ({"side": "SELL"}, {"side": "BUY", "order_action": "CANCEL"}):
                self.assertTrue(self.gate(subject, final=final)["allowed"])

    def test_supported_single_and_existing_round_formulas_are_unchanged(self):
        self.rules["buy"]["execution"]["base"]["point_mode"] = "NONE"
        for confirmed, quantity, budget in ((0, 1, 100), (1, 2, 200)):
            result = self.buy._build(rules=self.rules, cycle=self.buy._cycle(confirmed))
            self.assertEqual("READY", result["status"])
            intent = result["execution_intent"]
            self.assertEqual((quantity, budget), (intent["quantity"], intent["budget"]))
            self.assertTrue(self.gate({"execution_intent": intent})["allowed"])
            self.assertTrue(self.gate({"execution_intent": intent}, final=True)["allowed"])
