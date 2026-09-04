from copy import deepcopy
from datetime import datetime
from contextlib import ExitStack
import json
from types import SimpleNamespace
import unittest
from unittest import mock

from tests import test_buy_time_slice_production as time_tests
from tests import test_indicator_follow_buy_execution_connection as buy_tests
from execution_ratio_slice_eligibility import inspect_eligible_ratio_slices
from execution_price_comparison import evaluate_percent_comparison
import routine_signal_consumer
import order_queue
import gui_auto_trade_timer


class BuyRatioSliceProductionTest(unittest.TestCase):
    # Reuse durable Queue/Fill fixtures and checks, not a mocked price/ledger.
    write = time_tests.BuyTimeSliceProductionTest.write
    prepare = time_tests.BuyTimeSliceProductionTest.prepare
    completed_child = time_tests.BuyTimeSliceProductionTest.completed_child

    def setUp(self):
        time_tests.BuyTimeSliceProductionTest.setUp(self)
        self.rules["buy"]["execution"]["base"].update(
            point_mode="MULTI_RATIO", ratio_count=3, ratio_left="ORDER_PRICE",
            ratio_right="CURRENT_PRICE", ratio_direction="UP", ratio_value=0.15, ratio_compare=">=")

    build = time_tests.BuyTimeSliceProductionTest.build

    def inspect(self, at="2026-09-02T10:00:00", price=101, cash=10000, codes=None):
        return inspect_eligible_ratio_slices(
            selected_account_no="12345678", allowed_stock_codes=codes or ["005930"],
            now=datetime.fromisoformat(at), actionable_prices_by_code={code: price for code in (codes or ["005930"])}, current_orderable_cash=cash,
            signals_path=self.signal_path, orders_path=self.root / "order_queue.json",
            executions_path=self.root / "order_executions.json", fills_path=self.root / "fills.json",
            positions_path=self.root / "positions.json", holdings_path=self.root / "broker_holdings.json",
        )

    def test_ratio_count_same_round_quantities_and_identity(self):
        self.prepare()
        self.assertEqual([4, 3, 3], [i["quantity"] for i in self.intents])
        self.assertEqual({1}, {i["buy_round"] for i in self.intents})
        self.assertEqual({0}, {i["plan_generation"] for i in self.intents})
        self.assertEqual(1, len({i["execution_process_id"] for i in self.intents}))
        self.assertEqual({self.signal["id"]}, {i["source_signal_id"] for i in self.intents})
        self.assertEqual(3, len({i["execution_id"] for i in self.intents}))
        self.assertEqual({"RATIO_SLICE"}, {i["child_kind"] for i in self.intents})
        self.assertEqual(1000, sum(i["budget"] for i in self.intents))
        self.assertEqual("BUY_MULTI_RATIO_QUANTITY_BELOW_CHILD_COUNT", self.build(
            stock_config={"trade_amount_type": "QUANTITY", "buy_qty": 2})["reason"])

    def test_false_true_terminal_true_does_not_need_edge_or_new_threshold(self):
        self.prepare()
        self.assertFalse(self.inspect(price=100)["proposals"])
        self.assertEqual(1, self.inspect()["proposals"][0]["child_sequence_index"])
        self.completed_child()
        second = self.inspect()["proposals"]
        self.assertEqual(1, len(second))
        self.assertEqual(2, second[0]["child_sequence_index"])
        self.assertFalse(self.inspect(price=100)["proposals"])
        self.assertEqual(2, self.inspect()["proposals"][0]["child_sequence_index"])

    def test_exact_percent_threshold_and_direction_combinations(self):
        for direction, compare, price, eligible in (
            ("UP", ">=", 100150, True), ("UP", "<=", 100150, True),
            ("DOWN", ">=", 99850, True), ("DOWN", "<=", 99850, True),
            ("BOTH", "WITHIN", 100150, True), ("BOTH", "OUTSIDE", 100150, False),
            ("BOTH", "OUTSIDE", 100151, True), ("UP", ">=", 100149, False),
        ):
            with self.subTest(direction=direction, compare=compare, price=price):
                self.assertEqual(eligible, evaluate_percent_comparison(left=100000, right=price,
                    direction=direction, compare=compare, threshold=0.15)[0])
        self.rules["buy"]["execution"]["base"].update(ratio_value=0.15)
        self.prepare()
        # Integral market prices at the exact 0.15% boundary.
        for child in self.signal["execution_intents"]:
            child["multi_ratio_plan"]["order_price"] = 100000
        self.write("routine_signals.json", "signals", [self.signal])
        self.assertTrue(self.inspect(price=100150)["proposals"])
        self.assertFalse(self.inspect(price=100149)["proposals"])

    def test_order_price_not_replaced_by_ratio_input(self):
        self.prepare()
        child = self.inspect(price=150)["proposals"][0]["execution_intents"][0]
        self.assertEqual(100, child["price"])
        self.assertEqual(400, child["budget"])
        self.assertEqual(150, child["child_plan"]["ratio_trigger_evidence"]["right_price"])

    def test_current_price_reprices_and_stale_cash_round_ceiling_block(self):
        self.rules["buy"]["execution"]["base"]["order_price_basis"] = "CURRENT_PRICE"
        self.prepare()
        self.completed_child(price=150)
        self.assertFalse(self.inspect(price=None)["proposals"])
        self.assertFalse(self.inspect(price=120, cash=359)["proposals"])
        proposal = self.inspect(price=120)["proposals"][0]
        self.assertEqual(120, proposal["execution_intents"][0]["price"])
        self.assertEqual(360, proposal["execution_intents"][0]["budget"])
        self.assertEqual(400, proposal["budget_evidence"]["remaining_round_budget"])
        self.assertFalse(self.inspect(price=140)["proposals"])

    def test_average_price_comes_from_position_not_signal_or_gui(self):
        self.rules["buy"]["execution"]["base"].update(ratio_left="AVG_PRICE")
        self.prepare()
        self.assertFalse(self.inspect()["proposals"])
        self.write("positions.json", "positions", [{"account_no": "12345678", "code": "005930", "quantity": 2, "average_price": 100}])
        self.write("broker_holdings.json", "holdings", [{"account_no": "12345678", "code": "005930", "holding_quantity": 2}])
        child = self.inspect()["proposals"][0]["execution_intents"][0]
        self.assertEqual(100, child["child_plan"]["ratio_trigger_evidence"]["left_price"])

    def test_direct_planner_blocks_excess_budget_invalid_modes_and_zero_children(self):
        ready = self.build()
        intent = deepcopy(ready["execution_intent"])
        intent.update(quantity=10, budget=999)
        self.assertEqual("BUY_MULTI_RATIO_ROUND_BUDGET_EXCEEDED", buy_tests.bridge._multi_ratio_execution_intents(intent, {"rules": self.rules})["reason"])
        for key, value in (("ratio_count", 0), ("ratio_value", 0), ("ratio_value", float("inf")), ("ratio_left", "GUI"), ("ratio_compare", "UNSUPPORTED")):
            with self.subTest(key=key, value=value):
                rules = deepcopy(self.rules)
                rules["buy"]["execution"]["base"][key] = value
                self.assertEqual("BLOCKED", self.build(rules=rules)["status"])

    def test_candidate_approval_and_duplicate_queue(self):
        self.prepare()
        proposal = self.inspect()["proposals"][0]
        with mock.patch.object(order_queue, "ORDER_QUEUE_PATH", self.root / "order_queue.json"), mock.patch.object(
            routine_signal_consumer, "routine_execution_intent_admission", return_value={"allowed": True}
        ), mock.patch.object(routine_signal_consumer, "_apply_operation_policy_to_created_orders", return_value={"ok": True, "policy_checked": 1, "policy_executable": 0, "policy_blocked": 0, "policy_errors": 0, "policy_results": []}):
            first = routine_signal_consumer.enqueue_eligible_ratio_slice(proposal)
            again = routine_signal_consumer.enqueue_eligible_ratio_slice(proposal)
        self.assertEqual(1, first["orders_created"])
        self.assertEqual(1, first["approved"])
        self.assertEqual(0, again["orders_created"])
        self.assertGreaterEqual(again["duplicates"], 1)

    def test_last_child_admission_block_does_not_complete_plan(self):
        self.prepare()
        proposal = self.inspect()["proposals"][0]
        proposal["complete_after_enqueue"] = True
        with mock.patch.object(routine_signal_consumer, "_build_order_queue_candidates_for_signals", return_value={"ok": True, "orders_created": 0, "duplicates": 0}), mock.patch.object(routine_signal_consumer, "update_signal_status") as update:
            routine_signal_consumer.enqueue_eligible_ratio_slice(proposal)
        update.assert_not_called()

    def test_same_round_two_is_not_child_buy_round_increment(self):
        result = self.build(cycle=self.buy._cycle(1, base_filled_buy_amount=500, last_filled_buy_amount=500))
        self.assertEqual("READY", result["status"], result)
        self.assertEqual({2}, {i["buy_round"] for i in result["execution_intents"]})
        self.assertEqual([4, 3, 3], [i["quantity"] for i in result["execution_intents"]])

    def test_unresolved_cycle_never_builds_plan(self):
        result = self.build(cycle=self.buy._cycle(status="unresolved"))
        self.assertEqual("BLOCKED", result["status"])
        self.assertIsNone(result["execution_intent"])

    def test_runtime_orphan_blocks_blind_recovery(self):
        self.prepare()
        self.completed_child()
        self.write("order_queue.json", "orders", [])
        result = self.inspect()
        self.assertFalse(result["proposals"])
        self.assertIn("RATIO_SLICE_RUNTIME_QUEUE_MISMATCH", result["reviews"][0]["review_reasons"])

    def test_position_broker_mismatch_blocks(self):
        self.prepare()
        self.completed_child()
        self.write("positions.json", "positions", [{"account_no": "12345678", "code": "005930", "quantity": 99}])
        result = self.inspect()
        self.assertFalse(result["proposals"])
        self.assertTrue(result["reviews"])

    def test_duplicate_signal_row_never_proposes_two_children_in_one_cycle(self):
        self.prepare()
        self.write("routine_signals.json", "signals", [self.signal, deepcopy(self.signal)])
        self.assertEqual(1, len(self.inspect()["proposals"]))

    def test_runtime_uncertainty_even_if_queue_is_terminal(self):
        self.prepare()
        self.completed_child()
        path = self.root / "order_executions.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["executions"][0]["status"] = "SEND_UNCERTAIN"
        path.write_text(json.dumps(data), encoding="utf-8")
        result = self.inspect()
        self.assertFalse(result["proposals"])
        self.assertIn("RATIO_SLICE_RUNTIME_UNCERTAIN", result["reviews"][0]["review_reasons"])

    def test_frozen_ratio_child_validates_and_tampered_plan_fails(self):
        self.prepare()
        intent = self.inspect()["proposals"][0]["execution_intents"][0]
        self.assertEqual("", buy_tests.bridge.inspect_buy_execution_support(subject={"execution_intent": intent}, rules=self.rules))
        for field, value in (("execution_mode", "SINGLE"), ("quantity", 9), ("buy_round", 2), ("budget", 1)):
            with self.subTest(field=field):
                bad = deepcopy(intent)
                bad[field] = value
                self.assertEqual("BUY_MULTI_RATIO_PLAN_INVALID", buy_tests.bridge.inspect_buy_execution_support(subject={"execution_intent": bad}, rules=self.rules))

    def test_current_price_final_pre_hash_and_round_recheck(self):
        self.rules["buy"]["execution"]["base"]["order_price_basis"] = "CURRENT_PRICE"
        time_tests.BuyTimeSliceProductionTest.test_final_boundary_rechecks_cash_and_round_budget_after_due(self)

    def test_all_pending_and_open_statuses_wait(self):
        self.prepare()
        for status in ("PENDING", "APPROVED", "EXECUTABLE", "REAL_READY", "ORDER_QUEUED",
                       "DISPATCH_CLAIMED", "SEND_CALL_ACCEPTED", "BROKER_ACCEPTED", "PARTIALLY_FILLED"):
            with self.subTest(status=status):
                self.completed_child(status=status, filled=0, remaining=4)
                result = self.inspect()
                self.assertFalse(result["proposals"])

    def test_unsafe_process_does_not_stop_other_stock(self):
        from execution_provenance_contract import materialize_execution_intent_children
        self.prepare()
        self.completed_child(status="SEND_UNCERTAIN", filled=0)
        other = deepcopy(self.signal)
        other.update(id="OTHER-SIGNAL", code="000660")
        other["execution_intents"] = materialize_execution_intent_children(
            self.build()["execution_intents"], source_signal_id="OTHER-SIGNAL", execution_process_id="OTHER-PROCESS")
        other["execution_intent"] = other["execution_intents"][0]
        self.write("routine_signals.json", "signals", [self.signal, other])
        result = self.inspect(codes=["005930", "000660"])
        self.assertEqual(["000660"], [p["code"] for p in result["proposals"]])
        self.assertEqual(["005930"], [p["code"] for p in result["reviews"]])

    def test_three_children_complete_one_confirmed_round_not_three(self):
        self.prepare()
        first, first_fill = self.completed_child()
        orders, fills = [first], [first_fill]
        owner = json.loads((self.root / "order_executions.json").read_text(encoding="utf-8"))["processes"]
        for index, total_qty in ((2, 7), (3, 10)):
            self.assertEqual(index, self.inspect()["proposals"][0]["child_sequence_index"])
            intent = self.intents[index - 1]
            record = {**deepcopy(first), "id": f"ORDER-{index}", "execution_intent": deepcopy(intent),
                      "execution_id": intent["execution_id"], "child_sequence_index": index,
                      "quantity": 3, "amount": 300, "broker_order_no": f"BROKER-{index}", "cumulative_filled_quantity": 3}
            fill = {**deepcopy(first_fill), "fill_id": f"FILL-{index}", "execution_id": intent["execution_id"],
                    "order_id": record["id"], "broker_order_no": record["broker_order_no"], "filled_quantity": 3}
            orders.append(record)
            fills.append(fill)
            self.write("order_queue.json", "orders", orders)
            self.write("fills.json", "fills", fills)
            (self.root / "order_executions.json").write_text(json.dumps({"executions": orders, "processes": owner}), encoding="utf-8")
            self.write("positions.json", "positions", [{"account_no": "12345678", "code": "005930", "quantity": total_qty, "average_price": 100}])
            self.write("broker_holdings.json", "holdings", [{"account_no": "12345678", "code": "005930", "holding_quantity": total_qty, "received_at": "2026-09-02T10:00:06"}])
        self.assertFalse(self.inspect()["proposals"])
        cycle = buy_tests.bridge.project_indicator_follow_cycle(code="005930", routine_instance_id="INSTANCE_A",
            order_queue={"orders": orders}, fills={"fills": fills}, positions={"positions": [{"code": "005930", "quantity": 10, "average_price": 100}]})
        self.assertEqual("resolved", cycle["status"], cycle)
        self.assertEqual(1, cycle["confirmed_buy_round"])
        self.assertEqual(1000, cycle["cumulative_filled_buy_amount"])

    def test_new_ratio_with_multi_hoga_remains_fail_closed(self):
        self.rules["buy"]["execution"]["base"].update(hoga_mode="MULTI", hoga_up=1, hoga_down=1)
        self.assertEqual("BUY_MULTI_RATIO_HOGA_COMBINATION_NOT_IMPLEMENTED", self.build()["reason"])

    def test_missing_cash_and_stale_price_create_no_candidates(self):
        self.prepare()
        self.assertFalse(self.inspect(cash=None)["proposals"])
        self.assertFalse(self.inspect(price=None)["proposals"])

    def _routine_gate_fixture(self):
        project = self.root / "project"
        stock = project / "stocks" / "005930"
        stock.mkdir(parents=True)
        runtime = project / "runtime"
        runtime.mkdir()
        (stock / "config.json").write_text(json.dumps({"code": "005930", "assigned_routine_instance_id": "INSTANCE_A", "buy_limit_enabled": True, "buy_limit_amount": 1000}), encoding="utf-8")
        (stock / "state.json").write_text("{}", encoding="utf-8")
        for name in ("order_queue.json", "fills.json", "positions.json"):
            (runtime / name).write_text((self.root / name).read_text(encoding="utf-8"), encoding="utf-8")
        module = buy_tests._load_module("routine.py", "ratio_production_gate_test")
        module.__file__ = str(project / "routines" / "test" / "routine.py")
        self.rules.update(principle={"execution_enabled": True}, safety={"real_order_allowed": True})
        def gate(subject, final=False):
            callback = module.evaluate_final_real_order_safety if final else module.evaluate_execution_admission
            return callback(subject=subject, rules=self.rules, routine_identity={}, rules_identity="test")
        return gate

    def test_actual_routine_admission_approval_queue_and_final_safety(self):
        self.prepare()
        gate = self._routine_gate_fixture()
        proposal = self.inspect()["proposals"][0]
        self.assertTrue(gate(proposal["signal"])["allowed"])
        self.assertTrue(gate(proposal["signal"], final=True)["allowed"])
        with mock.patch.object(order_queue, "ORDER_QUEUE_PATH", self.root / "order_queue.json"), mock.patch.object(
            routine_signal_consumer, "routine_execution_intent_admission", side_effect=gate
        ), mock.patch.object(routine_signal_consumer, "_apply_operation_policy_to_created_orders", return_value={"ok": True, "policy_checked": 1, "policy_executable": 0, "policy_blocked": 0, "policy_errors": 0, "policy_results": []}):
            result = routine_signal_consumer.enqueue_eligible_ratio_slice(proposal)
        self.assertEqual(1, result["orders_created"], result)
        self.assertEqual(1, result["approved"], result)
        self.rules["safety"]["real_order_allowed"] = False
        self.assertFalse(gate(proposal["signal"], final=True)["allowed"])

    def test_timer_real_ratio_inspection_passes_fresh_price_cash_and_enqueues_once(self):
        self.prepare()
        gate = self._routine_gate_fixture()
        window = SimpleNamespace(
            _selected_account_no=lambda: "12345678",
            fresh_monitoring_market_information_state=lambda code: SimpleNamespace(last_price=101),
            current_orderable_cash_for_budget=lambda: 10000,
            mark_review_required=mock.Mock(return_value=True), statusBarMessage=mock.Mock(),
            auto_process_executable_orders_for_real_trade=mock.Mock(return_value={"processed": 1, "blocked": 0}),
        )
        snapshot = SimpleNamespace(entries=(SimpleNamespace(execution_ready=True, signal_probe_only=False, stock_code="005930", stock_name="test", stock_dir=self.root / "005930"),))
        def inspect(**kwargs):
            self.assertEqual(101, kwargs["actionable_prices_by_code"]["005930"])
            self.assertEqual(10000, kwargs["current_orderable_cash"])
            return self.inspect(price=kwargs["actionable_prices_by_code"]["005930"], cash=kwargs["current_orderable_cash"])
        empty = {"ok": True, "proposals": [], "reviews": [], "waiting": [], "errors": []}
        with ExitStack() as stack:
            for name in ("inspect_due_time_slices", "inspect_execution_process_supplements", "inspect_sell_price_resets", "inspect_unfilled_cancel_eligibility", "inspect_sell_repeats", "inspect_sell_final_residual_exits"):
                if hasattr(gui_auto_trade_timer, name):
                    stack.enter_context(mock.patch.object(gui_auto_trade_timer, name, return_value=empty))
            stack.enter_context(mock.patch.object(gui_auto_trade_timer, "inspect_eligible_ratio_slices", side_effect=inspect))
            stack.enter_context(mock.patch.object(gui_auto_trade_timer, "consume_pending_routine_signals_dry_run", return_value={"summary": {}}))
            stack.enter_context(mock.patch.object(gui_auto_trade_timer, "auto_trade_signal_probe_only_active", return_value=False))
            stack.enter_context(mock.patch.object(gui_auto_trade_timer, "auto_trade_real_execution_active", return_value=True))
            stack.enter_context(mock.patch.object(routine_signal_consumer, "routine_execution_intent_admission", side_effect=gate))
            stack.enter_context(mock.patch.object(order_queue, "ORDER_QUEUE_PATH", self.root / "order_queue.json"))
            stack.enter_context(mock.patch.object(routine_signal_consumer, "_apply_operation_policy_to_created_orders", return_value={"ok": True, "policy_checked": 1, "policy_executable": 1, "policy_blocked": 0, "policy_errors": 0, "policy_results": []}))
            result = gui_auto_trade_timer._process_pending_signal_pipeline(window, snapshot)
        self.assertEqual(1, result["ratio_slice"]["proposals"], result)
        self.assertEqual(1, result["ratio_slice"]["orders_created"], result)

    test_actual_cycle_stock_limit_and_gate_wiring = time_tests.BuyTimeSliceProductionTest.test_current_cycle_and_stock_limit_rechecked_in_routine_owned_gates

    test_open_and_partial_orders = time_tests.BuyTimeSliceProductionTest.test_open_and_partial_buy_wait_without_parallel_orders
    test_partial_cumulative_fill_deltas = time_tests.BuyTimeSliceProductionTest.test_partial_fill_cumulative_deltas_not_double_counted
    test_reject_uncertain_and_queue_fill_mismatch = time_tests.BuyTimeSliceProductionTest.test_confirmed_reject_continues_uncertainty_and_mismatch_isolate
    test_pending_plan_blocks_new_round = time_tests.BuyTimeSliceProductionTest.test_pending_plan_blocks_new_round_signal_and_defers_all_children
    test_immutable_process_owner = time_tests.BuyTimeSliceProductionTest.test_first_owner_round_budget_and_subsequent_child_owner_reference
    test_runtime_commit_unique_child_id_hash_lock = time_tests.BuyTimeSliceProductionTest.test_runtime_commit_reuses_immutable_process_and_unique_child_hash_lock
    test_source_and_dispatch_record_coalescing = time_tests.BuyTimeSliceProductionTest.test_real_queue_shape_with_source_and_nested_dispatch_continues
