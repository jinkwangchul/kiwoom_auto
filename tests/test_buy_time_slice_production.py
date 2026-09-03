from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from tests import test_indicator_follow_buy_execution_connection as buy_tests
from account_auto_trade_budget_consumption import project_time_slice_buy_budget
from execution_time_slice_due import inspect_due_time_slices
from execution_provenance_contract import option_snapshot_hash
from execution_preview_service import preview_execution_for_order
from auto_trade_order_execution_boundary import AutoTradeOrderExecutionBoundary
import order_queue
import routine_signal_queue
import routine_signal_consumer


class BuyTimeSliceProductionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.buy = buy_tests.IndicatorFollowBuyExecutionConnectionTest()
        self.rules = self.buy._rules(price_basis="ORDER_PRICE")
        self.rules["buy"]["execution"]["base"].update({
            "point_mode": "MULTI_TIME", "point_count": 3, "point_value": 30,
            "point_unit": "SECOND", "point_range": "WITHIN",
            "time_order_price_basis": "ORDER_PRICE",
        })

    def build(self, **overrides):
        context = {
            "rules": self.rules, "cycle": self.buy._cycle(),
            "stock_config": {"trade_amount_type": "QUANTITY", "buy_qty": 10},
            "routine_instance_id": "INSTANCE_A", "reference_price": 100,
            "actionable_current_price": 100,
            "account_budget": {"account_no": "12345678", "system_total_budget": 10000, "account_consumed_amount": 0},
        }
        context.update(overrides)
        return buy_tests.bridge.build_indicator_follow_buy_intent(buy_signal_result={"signal": "BUY"}, context=context)

    def write(self, name, field, records):
        (self.root / name).write_text(json.dumps({field: records}), encoding="utf-8")

    def prepare(self):
        result = self.build()
        self.assertEqual("READY", result["status"], result)
        self.signal_path = self.root / "routine_signals.json"
        with mock.patch.object(routine_signal_queue, "QUEUE_PATH", self.signal_path), mock.patch.object(
            routine_signal_queue, "now_text", return_value="2026-09-02T10:00:00"
        ):
            queued = routine_signal_queue.enqueue_routine_signal({"signal": "BUY", **result}, routine="지표추종매매", code="005930", name="삼성전자")
        self.assertEqual("queued", queued["status"])
        self.signal = json.loads(self.signal_path.read_text(encoding="utf-8"))["signals"][0]
        self.intents = self.signal["execution_intents"]
        for name, field in (("order_queue.json", "orders"), ("order_executions.json", "executions"), ("fills.json", "fills"), ("positions.json", "positions"), ("broker_holdings.json", "holdings")):
            self.write(name, field, [])

    def inspect(self, at="2026-09-02T10:00:00", price=100, cash=10000):
        return inspect_due_time_slices(
            selected_account_no="12345678", allowed_stock_codes=["005930"],
            now=datetime.fromisoformat(at), actionable_prices_by_code={"005930": price}, current_orderable_cash=cash,
            signals_path=self.signal_path, orders_path=self.root / "order_queue.json",
            executions_path=self.root / "order_executions.json", fills_path=self.root / "fills.json",
            positions_path=self.root / "positions.json", holdings_path=self.root / "broker_holdings.json",
        )

    def completed_child(self, *, status="FILLED", filled=4, price=100, remaining=0):
        intent = self.intents[0]
        record = {
            "id": "ORDER-1", "execution_intent": deepcopy(intent),
            **{k: intent[k] for k in ("execution_id", "execution_process_id", "source_signal_id", "plan_generation", "child_sequence_index", "child_sequence_total", "child_kind")},
            "account_no": "12345678", "side": "BUY", "code": "005930", "quantity": 4,
            "price": 100, "amount": 400, "status": status, "broker_order_no": "BROKER-1",
            "cumulative_filled_quantity": filled, "remaining_quantity": remaining,
            "updated_at": "2026-09-02T10:00:05",
        }
        self.write("order_queue.json", "orders", [record])
        runtime = {**deepcopy(record), "status": status}
        snapshot = {"side": "BUY", "buy_round": 1, "source_signal_id": self.signal["id"]}
        owner = {"execution_process_id": intent["execution_process_id"], "source_signal_id": self.signal["id"],
                 "provenance_contract_version": 1, "option_snapshot": snapshot, "option_snapshot_hash": option_snapshot_hash(snapshot)}
        (self.root / "order_executions.json").write_text(json.dumps({"executions": [runtime], "processes": [owner]}), encoding="utf-8")
        fill = {"fill_id": "FILL-1", "execution_id": intent["execution_id"], "execution_process_id": intent["execution_process_id"],
                "order_id": "ORDER-1", "broker_order_no": "BROKER-1", "account_no": "12345678", "code": "005930", "side": "BUY",
                "filled_quantity": filled, "filled_price": price, "received_at": "2026-09-02T10:00:04"}
        self.write("fills.json", "fills", [fill] if filled else [])
        self.write("positions.json", "positions", [{"account_no": "12345678", "code": "005930", "quantity": filled, "updated_at": "2026-09-02T10:00:05"}])
        self.write("broker_holdings.json", "holdings", [{"account_no": "12345678", "code": "005930", "holding_quantity": filled, "available_quantity": filled, "received_at": "2026-09-02T10:00:06", "reconciliation_status": "CONSISTENT"}])
        return record, fill

    def test_plan_quantity_schedule_identity_and_round(self):
        self.prepare()
        self.assertEqual([4, 3, 3], [i["quantity"] for i in self.intents])
        self.assertEqual([0, 15000, 30000], [i["child_plan"]["scheduled_offset_ms"] for i in self.intents])
        self.assertEqual(["2026-09-02T10:00:00.000", "2026-09-02T10:00:15.000", "2026-09-02T10:00:30.000"], [i["child_plan"]["scheduled_at"] for i in self.intents])
        self.assertEqual({1}, {i["buy_round"] for i in self.intents})
        self.assertEqual(1, len({i["execution_process_id"] for i in self.intents}))
        self.assertEqual({self.signal["id"]}, {i["source_signal_id"] for i in self.intents})
        self.assertEqual(3, len({i["execution_id"] for i in self.intents}))
        self.assertEqual(1000, sum(i["budget"] for i in self.intents))

    def test_interval_and_bar_schedule_and_missing_timeframe(self):
        base = self.rules["buy"]["execution"]["base"]
        base.update(point_range="INTERVAL", point_value=2, point_unit="MINUTE")
        self.assertEqual([0, 120000, 240000], [i["child_plan"]["scheduled_offset_ms"] for i in self.build()["execution_intents"]])
        base.update(point_unit="BAR")
        self.assertEqual("BUY_MULTI_TIME_UNIT_UNRESOLVED", self.build()["reason"])
        result = self.build(candles=[{"timeframe_minutes": 5}])
        self.assertEqual([0, 600000, 1200000], [i["child_plan"]["scheduled_offset_ms"] for i in result["execution_intents"]])

    def test_missing_price_policy_insufficient_qty_and_combination_blocked(self):
        base = self.rules["buy"]["execution"]["base"]
        base.pop("time_order_price_basis")
        self.assertEqual("BUY_MULTI_TIME_PRICE_POLICY_MISSING", self.build()["reason"])
        base["time_order_price_basis"] = "ORDER_PRICE"
        self.assertEqual("BUY_MULTI_TIME_QUANTITY_BELOW_CHILD_COUNT", self.build(stock_config={"trade_amount_type": "QUANTITY", "buy_qty": 2})["reason"])
        base.update(hoga_mode="MULTI", hoga_up=1, hoga_down=1)
        self.assertEqual("BUY_MULTI_TIME_HOGA_COMBINATION_NOT_IMPLEMENTED", self.build()["reason"])

    def test_due_boundary_one_progression_and_restart_no_duplicate(self):
        self.prepare()
        self.assertEqual([], self.inspect("2026-09-02T09:59:59")["proposals"])
        first = self.inspect()["proposals"]
        self.assertEqual(1, len(first))
        self.assertEqual(1, first[0]["child_sequence_index"])
        self.completed_child()
        self.assertEqual([], self.inspect("2026-09-02T10:00:14")["proposals"])
        second = self.inspect("2026-09-02T10:00:15")["proposals"]
        self.assertEqual(2, second[0]["child_sequence_index"])
        self.assertEqual(600, second[0]["budget_evidence"]["remaining_round_budget"])
        self.assertEqual(2, self.inspect("2026-09-02T10:00:15")["proposals"][0]["child_sequence_index"])
        self.assertEqual(1, len(self.inspect("2026-09-02T11:00:00")["proposals"]))

    def test_open_and_partial_buy_wait_without_parallel_orders(self):
        self.prepare()
        self.completed_child(status="BROKER_ACCEPTED", filled=0, remaining=4)
        self.assertEqual([], self.inspect("2026-09-02T10:00:15")["proposals"])
        self.completed_child(status="PARTIALLY_FILLED", filled=2, remaining=2)
        partial = self.inspect("2026-09-02T10:00:15")
        self.assertEqual([], partial["proposals"])
        self.assertEqual(200, partial["waiting"][0]["budget_evidence"]["open_buy_reservation"])
        self.completed_child(status="PARTIAL_CANCELLED", filled=2)
        result = self.inspect("2026-09-02T10:00:15")
        self.assertEqual(800, result["proposals"][0]["budget_evidence"]["remaining_round_budget"])

    def test_current_price_due_reprice_cash_stale_and_remaining_budget(self):
        self.rules["buy"]["execution"]["base"]["time_order_price_basis"] = "CURRENT_PRICE"
        self.prepare()
        self.completed_child(price=150)
        self.assertEqual([], self.inspect("2026-09-02T10:00:15", price=None)["proposals"])
        self.assertEqual([], self.inspect("2026-09-02T10:00:15", price=120, cash=359)["proposals"])
        result = self.inspect("2026-09-02T10:00:15", price=120)
        child = result["proposals"][0]["execution_intents"][0]
        self.assertEqual(360, child["budget"])
        self.assertEqual(120, child["price"])
        self.assertEqual(400, result["proposals"][0]["budget_evidence"]["remaining_round_budget"])
        blocked = self.inspect("2026-09-02T10:00:15", price=140)
        self.assertEqual([], blocked["proposals"])
        self.assertEqual("TIME_SLICE_BUY_ROUND_BUDGET_EXCEEDED", blocked["waiting"][0]["reason"])

    def test_confirmed_reject_continues_uncertainty_and_mismatch_isolate(self):
        self.prepare()
        self.completed_child(status="SEND_CALL_REJECTED", filled=0)
        self.assertEqual(2, self.inspect("2026-09-02T10:00:15")["proposals"][0]["child_sequence_index"])
        self.completed_child(status="SEND_UNCERTAIN", filled=0)
        uncertain = self.inspect("2026-09-02T10:00:15")
        self.assertEqual([], uncertain["proposals"])
        self.assertTrue(uncertain["reviews"])
        self.completed_child()
        self.write("fills.json", "fills", [])
        mismatch = self.inspect("2026-09-02T10:00:15")
        self.assertEqual([], mismatch["proposals"])
        self.assertIn("TIME_SLICE_BUY_QUEUE_FILL_MISMATCH", mismatch["reviews"][0]["review_reasons"])

    def test_pending_plan_blocks_new_round_signal_and_defers_all_children(self):
        self.prepare()
        next_result = self.build()
        with mock.patch.object(routine_signal_queue, "QUEUE_PATH", self.signal_path):
            blocked = routine_signal_queue.enqueue_routine_signal({"signal": "BUY", **next_result}, routine="지표추종매매", code="005930", name="삼성전자", tick_key="NEW_TICK")
        self.assertEqual("BUY_DEFERRED_PLAN_PENDING", blocked["reason"])
        self.assertTrue(routine_signal_consumer._is_deferred_child_signal(self.signal))

    def test_due_candidate_approval_pipeline_and_replay(self):
        self.prepare()
        proposal = self.inspect()["proposals"][0]
        with mock.patch.object(order_queue, "ORDER_QUEUE_PATH", self.root / "order_queue.json"), mock.patch.object(
            routine_signal_consumer, "routine_execution_intent_admission", return_value={"allowed": True}
        ), mock.patch.object(routine_signal_consumer, "_apply_operation_policy_to_created_orders", return_value={"ok": True, "policy_checked": 1, "policy_executable": 0, "policy_blocked": 0, "policy_errors": 0, "policy_results": []}):
            first = routine_signal_consumer.enqueue_scheduled_time_slice(proposal)
            again = routine_signal_consumer.enqueue_scheduled_time_slice(proposal)
        self.assertEqual(1, first["orders_created"])
        self.assertEqual(1, first["approved"])
        self.assertEqual(0, again["orders_created"])
        self.assertGreaterEqual(again["duplicates"], 1)

    def test_partial_fill_cumulative_deltas_not_double_counted(self):
        self.prepare()
        record, fill = self.completed_child(status="PARTIAL_CANCELLED", filled=3, price=110)
        earlier = {**fill, "fill_id": "FILL-EARLIER", "filled_quantity": 1, "filled_price": 100, "received_at": "2026-09-02T10:00:03"}
        self.write("fills.json", "fills", [earlier, fill])
        proposal = self.inspect("2026-09-02T10:00:15")["proposals"][0]
        self.assertEqual(320, proposal["budget_evidence"]["consumed_amount"])

    def test_first_owner_round_budget_and_subsequent_child_owner_reference(self):
        self.prepare()
        first = self.inspect()["proposals"][0]
        order = order_queue.signal_to_order_candidates(first["signal"], 1)[0]
        order.update(status="REAL_READY", execution_enabled=True)
        preview = preview_execution_for_order(order, {"operator_confirmed": True, "real_trade_enabled": True, "account_no": "12345678"})
        self.assertTrue(preview["ok"], preview)
        process = preview["candidate_result"]["process_record"]
        self.assertEqual(1000, process["process_plan"]["approved_budget"])
        self.completed_child()
        second = self.inspect("2026-09-02T10:00:15")["proposals"][0]
        child = second["execution_intents"][0]
        self.assertFalse(child["execution_process_owner_required"])
        order = order_queue.signal_to_order_candidates(second["signal"], 1)[0]
        order.update(status="REAL_READY", execution_enabled=True)
        preview = preview_execution_for_order(order, {"operator_confirmed": True, "real_trade_enabled": True, "account_no": "12345678"})
        self.assertTrue(preview["ok"], preview)
        self.assertIsNone(preview["candidate_result"]["process_record"])

    def test_final_boundary_rechecks_cash_and_round_budget_after_due(self):
        self.rules["buy"]["execution"]["base"]["time_order_price_basis"] = "CURRENT_PRICE"
        self.prepare()
        self.completed_child()
        proposal = self.inspect("2026-09-02T10:00:15")["proposals"][0]
        order = order_queue.signal_to_order_candidates(proposal["signal"], 1)[0]
        boundary = AutoTradeOrderExecutionBoundary.__new__(AutoTradeOrderExecutionBoundary)
        boundary._context = SimpleNamespace(selected_account_no=lambda: "12345678", fresh_current_price=lambda code: 100)
        with mock.patch.object(boundary, "_current_orderable_cash", return_value=299):
            blocked = boundary._fresh_buy_dispatch_preflight(order, {"selected_account_no": "12345678"}, self.root / "order_queue.json")
        self.assertEqual("fresh_buy_orderable_cash_exceeded", blocked["stage"])
        # An intervening fill-cost update removes round capacity. Final Send
        # must not trust the earlier due snapshot.
        self.completed_child(price=200)
        with mock.patch.object(boundary, "_current_orderable_cash", return_value=10000):
            blocked = boundary._fresh_buy_dispatch_preflight(order, {"selected_account_no": "12345678"}, self.root / "order_queue.json")
        self.assertEqual("fresh_buy_time_slice_round_budget", blocked["stage"])
        boundary._context.fresh_current_price = lambda code: None
        stale = boundary.finalize_current_price_before_hash(order, queue_path=self.root / "order_queue.json")
        self.assertEqual("current_price_pre_hash_unavailable", stale["stage"])

    def test_last_child_not_marked_complete_when_admission_refuses_enqueue(self):
        self.prepare()
        proposal = self.inspect()["proposals"][0]
        proposal["complete_after_enqueue"] = True
        with mock.patch.object(routine_signal_consumer, "_build_order_queue_candidates_for_signals", return_value={"ok": True, "orders_created": 0, "duplicates": 0}), mock.patch.object(routine_signal_consumer, "update_signal_status") as update:
            routine_signal_consumer.enqueue_scheduled_time_slice(proposal)
        update.assert_not_called()

    def test_real_queue_shape_with_source_and_nested_dispatch_continues(self):
        self.prepare()
        dispatch, fill = self.completed_child()
        source = deepcopy(dispatch)
        source.update(id="SOURCE-1", status="EXECUTABLE", cumulative_filled_quantity=0,
                      remaining_quantity=4, updated_at="2026-09-02T10:00:00")
        source.pop("broker_order_no")
        dispatch["order_id"] = source["id"]
        fill.update(order_id=source["id"], order_queued_id=dispatch["id"])
        self.write("fills.json", "fills", [fill])
        dispatch["execution_request"] = {"execution_intent": deepcopy(dispatch["execution_intent"]),
            "request_preview": {k: dispatch[k] for k in ("account_no", "side", "code", "quantity", "price")}}
        dispatch["original_order_quantity"] = dispatch.pop("quantity")
        for field in ("account_no", "side", "code", "price"):
            dispatch.pop(field)
        self.write("order_queue.json", "orders", [source, dispatch])
        result = self.inspect("2026-09-02T10:00:15")
        self.assertTrue(result["proposals"], result)
        self.assertEqual(2, result["proposals"][0]["child_sequence_index"], result)
        self.assertEqual(400, result["proposals"][0]["budget_evidence"]["consumed_amount"])

    def test_current_cycle_and_stock_limit_rechecked_in_routine_owned_gates(self):
        self.prepare()
        self.completed_child()
        proposal = self.inspect("2026-09-02T10:00:15")["proposals"][0]
        root = self.root / "project"
        runtime = root / "runtime"
        runtime.mkdir(parents=True)
        stock_dir = root / "stocks" / "005930"
        stock_dir.mkdir(parents=True)
        config = {"code": "005930", "assigned_routine_instance_id": "INSTANCE_A",
                  "buy_limit_enabled": True, "buy_limit_amount": 700}
        config_path = stock_dir / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        (stock_dir / "state.json").write_text("{}", encoding="utf-8")
        for name in ("order_queue.json", "fills.json", "positions.json"):
            data = json.loads((self.root / name).read_text(encoding="utf-8"))
            if name == "positions.json":
                data["positions"][0]["average_price"] = 100
            (runtime / name).write_text(json.dumps(data), encoding="utf-8")

        def check():
            return buy_tests.bridge.inspect_buy_time_slice_continuation(
                subject=proposal["signal"], rules=self.rules, project_root=root)

        self.assertEqual("", check())
        config["buy_limit_amount"] = 699
        config_path.write_text(json.dumps(config), encoding="utf-8")
        self.assertEqual("BUY_TIME_SLICE_STOCK_LIMIT_EXCEEDED", check())
        config.update(buy_limit_amount=1000, assigned_routine_instance_id="OTHER")
        config_path.write_text(json.dumps(config), encoding="utf-8")
        self.assertEqual("BUY_TIME_SLICE_ASSIGNMENT_CHANGED", check())
        module = buy_tests._load_module("routine.py", "time_slice_gate_test")
        rules = {"principle": {"execution_enabled": True}, "safety": {"real_order_allowed": True}}
        with mock.patch.object(module, "inspect_buy_time_slice_continuation", return_value="BUY_TIME_SLICE_STOCK_LIMIT_EXCEEDED") as gate:
            for callback in (module.evaluate_execution_admission, module.evaluate_final_real_order_safety):
                result = callback(subject=proposal["signal"], rules=rules, routine_identity={}, rules_identity="hash")
                self.assertFalse(result["allowed"])
            self.assertEqual(2, gate.call_count)

    def test_runtime_commit_reuses_immutable_process_and_unique_child_hash_lock(self):
        from execution_runtime_catalog_preview import build_execution_runtime_catalog_preview
        from execution_runtime_write_preview_orchestrator import run_execution_runtime_write_preview_orchestrator
        from execution_runtime_commit_readiness_gate import evaluate_execution_runtime_commit_readiness
        from execution_runtime_commit_plan_orchestrator import run_execution_runtime_commit_plan_orchestrator
        from execution_runtime_commit_service import commit_execution_runtime_plan
        from execution_runtime_file_schema import default_order_executions_data, default_order_locks_data
        self.prepare()
        executions = self.root / "committed_executions.json"
        locks = self.root / "committed_locks.json"
        executions.write_text(json.dumps(default_order_executions_data()), encoding="utf-8")
        locks.write_text(json.dumps(default_order_locks_data()), encoding="utf-8")
        context = {"manual_execution_runtime_commit_confirmed": True, "manual_runtime_file_write_confirmed": True}

        def commit(proposal):
            # Production assigns the next queue index, even if both requests
            # happen within the same clock second.
            index = len(json.loads(executions.read_text(encoding="utf-8"))["executions"]) + 1
            order = order_queue.signal_to_order_candidates(proposal["signal"], index)[0]
            order.update(status="REAL_READY", execution_enabled=True)
            preview = preview_execution_for_order(order, {"operator_confirmed": True, "real_trade_enabled": True, "account_no": "12345678"})
            candidate = preview["candidate_result"]
            pipeline = preview["pipeline_result"]["pipeline"]
            catalog = build_execution_runtime_catalog_preview(
                execution_request_preview=candidate["execution_request_preview"],
                lock_preview=pipeline["lock_preview"], request_hash_preview=pipeline["request_hash_preview"],
                queue_write_preview_result=preview["queue_write_preview_result"], order_candidate=order)
            write = run_execution_runtime_write_preview_orchestrator(catalog_preview=catalog,
                existing_order_executions_data=json.loads(executions.read_text(encoding="utf-8")),
                existing_order_locks_data=json.loads(locks.read_text(encoding="utf-8")))
            gate = evaluate_execution_runtime_commit_readiness(write, **context)
            plan = run_execution_runtime_commit_plan_orchestrator(write, gate)
            result = commit_execution_runtime_plan(plan, executions, locks, context=context)
            self.assertTrue(result["committed"], result)
            return preview["queue_write_preview_result"]["order_queued_record_preview"]

        first = commit(self.inspect()["proposals"][0])
        owner = json.loads(executions.read_text(encoding="utf-8"))["processes"][0]
        self.completed_child()
        evidence = json.loads((self.root / "order_executions.json").read_text(encoding="utf-8"))
        evidence["processes"] = [owner]
        (self.root / "order_executions.json").write_text(json.dumps(evidence), encoding="utf-8")
        second = commit(self.inspect("2026-09-02T10:00:15")["proposals"][0])
        stored = json.loads(executions.read_text(encoding="utf-8"))
        self.assertEqual([owner], stored["processes"])
        self.assertEqual(2, len(stored["executions"]))
        for field in ("execution_id", "order_id", "request_hash", "lock_id"):
            self.assertNotEqual(first[field], second[field], field)


if __name__ == "__main__":
    unittest.main()
