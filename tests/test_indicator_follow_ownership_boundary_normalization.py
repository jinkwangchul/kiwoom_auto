from __future__ import annotations

import inspect
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

import account_auto_trade_budget_consumption as budget
import execution_price_reset
import execution_provenance_contract as provenance
import gui_auto_trade_timer
import operator_reconciliation_service
import routine_lifecycle_executor
import routine_signal_consumer
from routine_lifecycle_decision import (
    ROUTINE_LIFECYCLE_COMMANDS,
    build_routine_lifecycle_decision,
    validate_routine_lifecycle_decision,
)
from routine_main_facts import (
    build_routine_main_facts_from_projection,
    capture_routine_main_facts,
    validate_routine_main_facts,
)


class OwnershipBoundaryNormalizationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        runtime = self.root / "runtime"
        runtime.mkdir()
        for filename, fields in {
            "routine_signals.json": {"signals": []},
            "order_queue.json": {"orders": []},
            "order_executions.json": {"executions": [], "processes": []},
            "fills.json": {"fills": []},
            "positions.json": {"positions": []},
            "broker_holdings.json": {"holdings": []},
        }.items():
            (runtime / filename).write_text(json.dumps(fields), encoding="utf-8")
        self.stock = self.root / "stocks" / "005930_test"
        self.stock.mkdir(parents=True)
        (self.stock / "config.json").write_text(
            json.dumps({"assigned_routine_instance_id": "INSTANCE_A"}), encoding="utf-8"
        )
        (self.stock / "state.json").write_text(json.dumps({"status": "RUNNING"}), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def facts(self):
        return capture_routine_main_facts(
            project_root=self.root,
            stock_dirs={"005930": self.stock},
            selected_account_no="12345678",
            allowed_stock_codes=("005930",),
            actionable_prices_by_code={"005930": 77777},
            current_orderable_cash=1234567,
        ).to_payload()

    def lifecycle_module(self):
        path = Path(__file__).resolve().parents[1] / "routines" / "지표추종매매" / "routine_lifecycle.py"
        spec = importlib.util.spec_from_file_location("ownership_test_routine_lifecycle", path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def strategy_identity_module(self):
        path = Path(__file__).resolve().parents[1] / "routines" / "지표추종매매" / "routine_strategy_identity.py"
        spec = importlib.util.spec_from_file_location("ownership_test_routine_strategy_identity", path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def startup_recovery_module(self):
        path = Path(__file__).resolve().parents[1] / "routines" / "지표추종매매" / "routine_startup_recovery.py"
        spec = importlib.util.spec_from_file_location("ownership_test_routine_startup_recovery", path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def lifecycle_facts(self, *, cancel_confirmed: bool | None, position_qty: int = 3, holding_qty: int = 3):
        signals = [
            {"id": "SIGNAL_OLD", "code": "005930", "signal": "SELL", "status": "PREVIEWED",
             "routine_instance_id": "INSTANCE_A", "created_at": "2026-01-01T00:00:00"},
            {"id": "SIGNAL_NEW", "code": "005930", "signal": "BUY", "status": "PENDING",
             "routine_instance_id": "INSTANCE_A", "created_at": "2026-01-01T00:00:01",
             "signal_runtime_policy": {"duplicate_priority": "TRAILING"}},
        ]
        orders = [
            {"id": "ORDER_OLD", "code": "005930", "side": "SELL", "source_signal_id": "SIGNAL_OLD",
             "status": "BROKER_ACCEPTED", "broker_order_no": "B-1"},
        ]
        if cancel_confirmed is not None:
            orders.append({
                "id": "CANCEL_OLD", "code": "005930", "order_action": "CANCEL",
                "status": "BROKER_ACCEPTED", "original_order_effect_confirmed": cancel_confirmed,
                "execution_request": {"request_preview": {"order_action": "CANCEL", "original_order_no": "B-1"}},
            })
        runtime = self.root / "runtime"
        (runtime / "routine_signals.json").write_text(json.dumps({"signals": signals}), encoding="utf-8")
        (runtime / "order_queue.json").write_text(json.dumps({"orders": orders}), encoding="utf-8")
        (runtime / "positions.json").write_text(json.dumps({"positions": [{"code": "005930", "quantity": position_qty}]}), encoding="utf-8")
        (runtime / "broker_holdings.json").write_text(json.dumps({"holdings": [{"code": "005930", "holding_quantity": holding_qty, "reconciliation_status": "CONSISTENT"}]}), encoding="utf-8")
        return self.facts()

    def test_facts_are_one_hashed_revision_and_tamper_fails(self) -> None:
        facts = self.facts()
        self.assertEqual((True, ""), validate_routine_main_facts(facts))
        tampered = dict(facts)
        tampered["current_orderable_cash"] = 1
        self.assertEqual("MAIN_FACTS_HASH_MISMATCH", validate_routine_main_facts(tampered)[1])

    def test_all_commands_share_one_stale_safe_contract(self) -> None:
        facts = self.facts()
        for command in ROUTINE_LIFECYCLE_COMMANDS:
            decision = build_routine_lifecycle_decision(
                command,
                main_facts=facts,
                routine_identity={"definition_id": "indicator_follow", "routine_instance_id": "INSTANCE_A"},
                stock_code="005930",
                reason="CANCEL_EFFECT_PENDING" if command == "WAIT" else "TEST",
            )
            self.assertEqual((True, ""), validate_routine_lifecycle_decision(decision, main_facts=facts))
            stale = dict(facts)
            stale["revision"] = "OTHER"
            self.assertEqual("MAIN_FACTS_REVISION_MISMATCH", validate_routine_lifecycle_decision(decision, main_facts=stale)[1])

    def test_routine_package_has_no_main_sot_read(self) -> None:
        package = Path(__file__).resolve().parents[1] / "routines" / "지표추종매매"
        production = "\n".join(
            path.read_text(encoding="utf-8")
            for path in package.glob("*.py")
        )
        self.assertNotIn("StockRepository", production)
        self.assertNotIn(' / "runtime" / ', production)
        self.assertNotIn(' / "stocks" / ', production)

    def test_deferred_budget_uses_only_opaque_scope(self) -> None:
        intent = {
            "side": "BUY",
            "budget_scope_required": True,
            "approved_budget_ceiling": 1000,
            "budget_scope_member": {"member": "ROUND-A"},
            "source_signal_id": "SIGNAL-A",
            "execution_process_id": "PROCESS-A",
            "plan_generation": 0,
            "execution_mode": "MULTI_TIME",
            "child_kind": "TIME_SLICE",
            "child_sequence_index": 1,
            "child_sequence_total": 1,
        }
        materialized = provenance.materialize_execution_intent_children(
            [intent], source_signal_id="SIGNAL-A", execution_process_id="PROCESS-A"
        )[0]
        self.assertTrue(materialized["budget_scope_id"].startswith("BUDGET_SCOPE_"))
        order = {**materialized, "execution_intent": materialized, "code": "005930", "account_no": "12345678"}
        result = budget.project_deferred_buy_budget_scope(
            order=order, order_records=[], fill_records=[], candidate_amount=700
        )
        self.assertTrue(result["admitted"], result)
        source = inspect.getsource(budget.project_deferred_buy_budget_scope)
        self.assertNotIn("MULTI_TIME", source)
        self.assertNotIn("MULTI_RATIO", source)
        self.assertNotIn("buy_round", source)
        self.assertNotIn("multi_time_plan", source)

    def test_cycle_identity_is_decided_by_routine_not_main_queue(self) -> None:
        module = self.strategy_identity_module()
        context = {"routine_instance_id": "INSTANCE_A", "code": "005930", "tick_key": "2026-01-01T09:01:00"}
        first = module.indicator_follow_cycle_identity(
            cycle={}, signal={}, runtime_context=context, side="BUY"
        )
        repeated = module.indicator_follow_cycle_identity(
            cycle={}, signal={}, runtime_context=context, side="BUY"
        )
        later = module.indicator_follow_cycle_identity(
            cycle={}, signal={}, runtime_context={**context, "tick_key": "2026-01-01T09:02:00"}, side="BUY"
        )
        self.assertEqual(first, repeated)
        self.assertNotEqual(first, later)
        queue_source = inspect.getsource(__import__("routine_signal_queue").enqueue_routine_signal)
        self.assertNotIn("CYCLE_{record", queue_source)

    def test_main_timer_routes_assigned_routines_through_lifecycle_contract(self) -> None:
        source = inspect.getsource(gui_auto_trade_timer._process_pending_signal_pipeline)
        self.assertIn("capture_current_facts()", source)
        self.assertIn("execute_routine_lifecycle_decision(", source)
        for strategy_symbol in (
            "inspect_due_time_slices", "inspect_eligible_ratio_slices",
            "inspect_buy_price_resets", "inspect_sell_repeat_generations",
            "inspect_buy_recovery_generations",
        ):
            self.assertNotIn(strategy_symbol, source)

    def test_assigned_production_path_does_not_call_legacy_strategy_inspectors(self) -> None:
        facts = self.facts()
        wait = build_routine_lifecycle_decision(
            "WAIT", main_facts=facts,
            routine_identity={"definition_id": "indicator_follow", "routine_instance_id": "INSTANCE_A"},
            stock_code="005930", reason="CANCEL_EFFECT_PENDING",
        )
        snapshot = SimpleNamespace(entries=(SimpleNamespace(
            execution_ready=True, signal_probe_only=False, stock_code="005930",
            stock_name="test", stock_dir=self.stock,
        ),))
        window = SimpleNamespace(
            _selected_account_no=lambda: "12345678",
            current_orderable_cash_for_budget=lambda: 1000,
            mark_review_required=mock.Mock(return_value=True),
            statusBarMessage=mock.Mock(),
        )
        with mock.patch.object(gui_auto_trade_timer, "auto_trade_signal_probe_only_active", return_value=False), \
             mock.patch.object(gui_auto_trade_timer, "auto_trade_real_execution_active", return_value=True), \
             mock.patch.object(gui_auto_trade_timer, "capture_routine_main_facts", return_value=SimpleNamespace(to_payload=lambda: facts)), \
             mock.patch.object(gui_auto_trade_timer, "evaluate_routine_lifecycle", return_value={"ok": True, "decisions": [wait]}), \
             mock.patch.object(gui_auto_trade_timer, "consume_pending_routine_signals_dry_run", return_value={"summary": {}}) as consumer:
            result = gui_auto_trade_timer._process_pending_signal_pipeline(window, snapshot)
        self.assertEqual(1, result["lifecycle"]["waiting"])
        self.assertEqual((), consumer.call_args.kwargs["allowed_stock_codes"])
        self.assertIs(facts, consumer.call_args.kwargs["main_facts"])

    def test_main_consumer_does_not_reinterpret_duplicate_priority(self) -> None:
        source = inspect.getsource(routine_signal_consumer.consume_pending_routine_signals_dry_run)
        self.assertNotIn("apply_duplicate_signal_priority", source)
        self.assertNotIn("read_signal_queue", source)
        self.assertNotIn("read_order_queue", source)
        self.assertNotIn("LEADING", source)
        self.assertNotIn("TRAILING", source)

    def test_consumer_blocks_when_fresh_facts_contain_a_new_signal(self) -> None:
        original = self.facts()
        runtime = self.root / "runtime"
        (runtime / "routine_signals.json").write_text(
            json.dumps({"signals": [{
                "id": "SIGNAL_NEW", "code": "005930", "signal": "BUY",
                "status": "PENDING", "execution_enabled": False,
                "routine_instance_id": "INSTANCE_A",
            }]}),
            encoding="utf-8",
        )
        fresh = self.facts()
        with mock.patch.object(routine_signal_consumer, "update_signal_status") as status_writer, \
             mock.patch.object(routine_signal_consumer, "_build_order_queue_candidates_for_signals") as queue_writer:
            result = routine_signal_consumer.consume_pending_routine_signals_dry_run(
                mark_previewed=True,
                write_order_queue=True,
                allowed_stock_codes=("005930",),
                main_facts=original,
                fresh_main_facts_provider=lambda: fresh,
            )
        self.assertTrue(result["summary"]["facts_stale"])
        self.assertEqual("MAIN_FACTS_CHANGED_BEFORE_CONSUME", result["summary"]["reason"])
        status_writer.assert_not_called()
        queue_writer.assert_not_called()

    def test_timer_recaptures_and_rearbitrates_after_consumer_facts_stale(self) -> None:
        original = self.facts()
        runtime = self.root / "runtime"
        (runtime / "routine_signals.json").write_text(
            json.dumps({"signals": [{
                "id": "SIGNAL_NEW", "code": "005930", "signal": "BUY",
                "status": "PENDING", "execution_enabled": False,
                "routine_instance_id": "INSTANCE_A",
            }]}),
            encoding="utf-8",
        )
        fresh = self.facts()
        snapshots = iter((original, fresh, fresh))
        capture = mock.Mock(side_effect=lambda **_kwargs: SimpleNamespace(
            to_payload=lambda: next(snapshots)
        ))
        evaluate = mock.Mock(return_value={"ok": True, "decisions": []})
        consumer_calls: list[dict[str, object]] = []

        def consume(**kwargs):
            kwargs["fresh_main_facts_provider"]()
            consumer_calls.append(kwargs)
            if len(consumer_calls) == 1:
                return {"summary": {"facts_stale": True, "executable_order_ids": []}}
            return {"summary": {"facts_stale": False, "executable_order_ids": []}}

        snapshot = SimpleNamespace(entries=(SimpleNamespace(
            execution_ready=True, signal_probe_only=False, stock_code="005930",
            stock_name="test", stock_dir=self.stock,
        ),))
        window = SimpleNamespace(
            _selected_account_no=lambda: "12345678",
            current_orderable_cash_for_budget=lambda: 1000,
            mark_review_required=mock.Mock(return_value=True),
            statusBarMessage=mock.Mock(),
        )
        with mock.patch.object(gui_auto_trade_timer, "capture_routine_main_facts", capture), \
             mock.patch.object(gui_auto_trade_timer, "evaluate_routine_lifecycle", evaluate), \
             mock.patch.object(gui_auto_trade_timer, "consume_pending_routine_signals_dry_run", side_effect=consume), \
             mock.patch.object(gui_auto_trade_timer, "auto_trade_real_execution_active", return_value=True):
            result = gui_auto_trade_timer._process_pending_signal_pipeline(window, snapshot)
        self.assertEqual(2, evaluate.call_count)
        self.assertEqual(original["snapshot_hash"], evaluate.call_args_list[0].kwargs["main_facts"]["snapshot_hash"])
        self.assertEqual(fresh["snapshot_hash"], evaluate.call_args_list[1].kwargs["main_facts"]["snapshot_hash"])
        self.assertEqual(2, len(consumer_calls))
        self.assertFalse(result["consumer"]["facts_stale"])

    def test_unassigned_execution_ready_stock_is_skipped_without_strategy_or_consumer(self) -> None:
        facts = {**self.facts(), "stock_configs": {"005930": {}}}
        snapshot = SimpleNamespace(entries=(SimpleNamespace(
            execution_ready=True, signal_probe_only=False, stock_code="005930",
            stock_name="test", stock_dir=self.stock,
        ),))
        window = SimpleNamespace(statusBarMessage=mock.Mock())
        with (
            mock.patch.object(gui_auto_trade_timer, "capture_routine_main_facts",
                              return_value=SimpleNamespace(to_payload=lambda: facts)),
            mock.patch.object(gui_auto_trade_timer, "evaluate_routine_lifecycle") as evaluate,
            mock.patch.object(gui_auto_trade_timer, "consume_pending_routine_signals_dry_run") as consumer,
        ):
            result = gui_auto_trade_timer._process_pending_signal_pipeline(window, snapshot)
        self.assertEqual("ROUTINE_ASSIGNMENT_MISSING", result["lifecycle"]["reason"])
        self.assertEqual(["005930"], result["lifecycle"]["skipped_unassigned_stock_codes"])
        evaluate.assert_not_called()
        consumer.assert_not_called()

    def test_unassigned_sibling_is_excluded_from_assigned_consumer_scope(self) -> None:
        sibling = self.root / "stocks" / "000660_test"
        sibling.mkdir()
        (sibling / "config.json").write_text("{}", encoding="utf-8")
        (sibling / "state.json").write_text("{}", encoding="utf-8")
        facts = {
            **self.facts(),
            "stock_configs": {
                "005930": {"assigned_routine_instance_id": "INSTANCE_A"},
                "000660": {},
            },
            "stock_states": {"005930": {}, "000660": {}},
        }
        snapshot = SimpleNamespace(entries=(
            SimpleNamespace(execution_ready=True, signal_probe_only=False, stock_code="005930",
                            stock_name="assigned", stock_dir=self.stock),
            SimpleNamespace(execution_ready=True, signal_probe_only=False, stock_code="000660",
                            stock_name="unassigned", stock_dir=sibling),
        ))
        consumer = mock.Mock(return_value={"summary": {"executable_order_ids": []}})
        with (
            mock.patch.object(gui_auto_trade_timer, "capture_routine_main_facts",
                              return_value=SimpleNamespace(to_payload=lambda: facts)),
            mock.patch.object(gui_auto_trade_timer, "evaluate_routine_lifecycle",
                              return_value={"ok": True, "decisions": []}),
            mock.patch.object(gui_auto_trade_timer, "consume_pending_routine_signals_dry_run", consumer),
            mock.patch.object(gui_auto_trade_timer, "auto_trade_real_execution_active", return_value=True),
        ):
            gui_auto_trade_timer._process_pending_signal_pipeline(SimpleNamespace(
                statusBarMessage=mock.Mock(), mark_review_required=mock.Mock(return_value=True),
            ), snapshot)
        self.assertEqual(("005930",), consumer.call_args.kwargs["allowed_stock_codes"])

    def test_eight_mutation_limit_blocks_tick_and_next_tick_resumes_from_fresh_facts(self) -> None:
        facts = self.facts()
        snapshot = SimpleNamespace(entries=(SimpleNamespace(
            execution_ready=True, signal_probe_only=False, stock_code="005930",
            stock_name="test", stock_dir=self.stock,
        ),))
        window = SimpleNamespace(
            _selected_account_no=lambda: "12345678",
            current_orderable_cash_for_budget=lambda: 1000,
            mark_review_required=mock.Mock(return_value=True),
            statusBarMessage=mock.Mock(),
        )
        capture = mock.Mock(return_value=SimpleNamespace(to_payload=lambda: facts))
        evaluate = mock.Mock(return_value={
            "ok": True,
            "decisions": [{"command": "CREATE_GENERATION", "stock_code": "005930"}],
        })
        execute = mock.Mock(return_value={"ok": True, "mutated": True, "executable_order_ids": []})
        consumer = mock.Mock(return_value={"summary": {"executable_order_ids": []}})
        with (
            mock.patch.object(gui_auto_trade_timer, "capture_routine_main_facts", capture),
            mock.patch.object(gui_auto_trade_timer, "evaluate_routine_lifecycle", evaluate),
            mock.patch.object(gui_auto_trade_timer, "execute_routine_lifecycle_decision", execute),
            mock.patch.object(gui_auto_trade_timer, "consume_pending_routine_signals_dry_run", consumer),
            mock.patch.object(gui_auto_trade_timer, "auto_trade_real_execution_active", return_value=True),
        ):
            first = gui_auto_trade_timer._process_pending_signal_pipeline(window, snapshot)
            evaluate.return_value = {"ok": True, "decisions": []}
            second = gui_auto_trade_timer._process_pending_signal_pipeline(window, snapshot)
        self.assertEqual(8, first["lifecycle"]["mutations"])
        self.assertEqual(1, consumer.call_count)
        self.assertEqual(("005930",), consumer.call_args.kwargs["allowed_stock_codes"])
        self.assertEqual(0, second["lifecycle"]["mutations"])

    def test_startup_recovery_strategy_is_owned_by_routine(self) -> None:
        module = self.startup_recovery_module()
        identity = {"definition_id": "indicator_follow", "routine_instance_id": "INSTANCE_A"}
        time_signal = {
            "id": "SIGNAL-TIME", "code": "005930", "status": "PENDING",
            "routine_instance_id": "INSTANCE_A",
            "execution_intents": [
                {
                    "execution_id": f"EXEC-TIME-{index}",
                    "execution_process_id": "PROCESS-TIME", "source_signal_id": "SIGNAL-TIME",
                    "plan_generation": 0, "child_sequence_index": index,
                    "child_sequence_total": 2, "child_kind": "TIME_SLICE",
                    "child_plan": {"planned_quantity": 1, "scheduled_at": scheduled},
                    "execution_mode": "MULTI_TIME",
                }
                for index, scheduled in enumerate(
                    ("2026-09-06T09:00:00", "2026-09-06T09:00:43"), start=1
                )
            ],
        }
        time_facts = build_routine_main_facts_from_projection({"signals": [time_signal]}).to_payload()
        time_result = module.classify_startup_recovery(
            signal_id="SIGNAL-TIME", main_facts=time_facts, rules={},
            routine_identity=identity, rules_identity="RULES-A",
        )
        self.assertEqual("PENDING_VALID", time_result["classification"])

        ratio_plan = {"planned_child_count": 2, "sentinel": 0.77}
        ratio_signal = {
            "id": "SIGNAL-RATIO", "code": "005930", "status": "PENDING",
            "routine_instance_id": "INSTANCE_A",
            "execution_intents": [
                {
                    "execution_id": f"EXEC-RATIO-{index}",
                    "execution_process_id": "PROCESS-RATIO", "source_signal_id": "SIGNAL-RATIO",
                    "plan_generation": 0, "child_sequence_index": index,
                    "child_sequence_total": 2, "child_kind": "RATIO_SLICE",
                    "child_plan": {"planned_quantity": 1}, "execution_mode": "MULTI_RATIO",
                    "multi_ratio_plan": ratio_plan,
                }
                for index in (1, 2)
            ],
        }
        ratio_facts = build_routine_main_facts_from_projection({"signals": [ratio_signal]}).to_payload()
        ratio_result = module.classify_startup_recovery(
            signal_id="SIGNAL-RATIO", main_facts=ratio_facts, rules={},
            routine_identity=identity, rules_identity="RULES-A",
        )
        self.assertEqual("PENDING_VALID", ratio_result["classification"])

    def test_main_startup_recovery_has_no_mode_aware_strategy_branch(self) -> None:
        source = inspect.getsource(operator_reconciliation_service.assess_startup_recovery)
        for strategy_token in (
            "MULTI_TIME", "TIME_SLICE", "MULTI_RATIO", "RATIO_SLICE",
            "scheduled_at", "multi_ratio_plan",
        ):
            self.assertNotIn(strategy_token, source)

    def test_malformed_startup_classification_is_stock_scoped_review(self) -> None:
        signals = [{
            "id": "SIGNAL-A", "code": "005930", "status": "PENDING",
            "routine_instance_id": "INSTANCE_A",
        }]
        runtime = self.root / "runtime"
        (runtime / "routine_signals.json").write_text(json.dumps({"signals": signals}), encoding="utf-8")
        result = operator_reconciliation_service.assess_startup_recovery(
            queue_path=runtime / "order_queue.json",
            fills_path=runtime / "fills.json",
            positions_path=runtime / "positions.json",
            broker_holdings_path=runtime / "broker_holdings.json",
            order_executions_path=runtime / "order_executions.json",
            order_locks_path=self.root / "missing-locks.json",
            routine_signals_path=runtime / "routine_signals.json",
            routine_recovery_classifier=lambda **_kwargs: {
                "classification": "PENDING_VALID", "signal_id": "WRONG",
            },
        )
        self.assertEqual("005930", result["routine_recovery_reviews"][0]["stock_code"])
        self.assertEqual(
            "ROUTINE_STARTUP_RECOVERY_CLASSIFICATION_INVALID",
            result["routine_recovery_reviews"][0]["reason"],
        )

    def test_executor_rejects_stale_decision_without_strategy_writer(self) -> None:
        facts = self.facts()
        decision = build_routine_lifecycle_decision(
            "WAIT", main_facts=facts,
            routine_identity={"definition_id": "indicator_follow", "routine_instance_id": "INSTANCE_A"},
            stock_code="005930", reason="CANCEL_EFFECT_PENDING",
        )
        result = routine_lifecycle_executor.execute_routine_lifecycle_decision(
            decision, main_facts=facts
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["mutated"])
        with mock.patch.object(routine_lifecycle_executor, "update_signal_status") as writer:
            tampered = dict(decision)
            tampered["facts_revision"] = "STALE"
            blocked = routine_lifecycle_executor.execute_routine_lifecycle_decision(
                tampered, main_facts=facts
            )
        self.assertFalse(blocked["ok"])
        writer.assert_not_called()

    def test_malformed_decision_stops_only_its_stock_for_review(self) -> None:
        facts = self.facts()
        decision = build_routine_lifecycle_decision(
            "ADMIT_SIGNAL", main_facts=facts,
            routine_identity={"definition_id": "indicator_follow", "routine_instance_id": "INSTANCE_A"},
            stock_code="005930", reason="TEST",
        )
        decision["decision_hash"] = "DAMAGED"
        review = mock.Mock(return_value=True)
        entry = SimpleNamespace(stock_dir=self.stock, stock_code="005930", stock_name="test")
        with mock.patch.object(routine_lifecycle_executor, "update_signal_status") as writer:
            result = routine_lifecycle_executor.execute_routine_lifecycle_decision(
                decision, main_facts=facts, review_marker=review, stock_entry=entry,
            )
        self.assertTrue(result["review_created"])
        review.assert_called_once()
        self.assertEqual("005930", review.call_args.args[1])
        writer.assert_not_called()

    def test_signal_supersede_waits_existing_cancel_without_duplicate_request(self) -> None:
        module = self.lifecycle_module()
        facts = self.lifecycle_facts(cancel_confirmed=False)
        result = module.evaluate_lifecycle(
            main_facts=facts, rules={},
            routine_identity={"definition_id": "indicator_follow", "routine_instance_id": "INSTANCE_A"},
            rules_identity="RULES-A",
        )
        self.assertEqual("WAIT", result["decisions"][0]["command"])
        self.assertEqual("CANCEL_EFFECT_PENDING", result["decisions"][0]["reason"])

    def test_signal_supersede_requires_latest_position_holding_reconciliation(self) -> None:
        module = self.lifecycle_module()
        pending = module.evaluate_lifecycle(
            main_facts=self.lifecycle_facts(cancel_confirmed=True, position_qty=3, holding_qty=2),
            rules={}, routine_identity={"definition_id": "indicator_follow", "routine_instance_id": "INSTANCE_A"},
            rules_identity="RULES-A",
        )
        self.assertEqual("WAIT", pending["decisions"][0]["command"])
        self.assertEqual("HOLDING_RECONCILIATION_PENDING", pending["decisions"][0]["reason"])
        confirmed = module.evaluate_lifecycle(
            main_facts=self.lifecycle_facts(cancel_confirmed=True, position_qty=2, holding_qty=2),
            rules={}, routine_identity={"definition_id": "indicator_follow", "routine_instance_id": "INSTANCE_A"},
            rules_identity="RULES-A",
        )
        self.assertEqual("COMPLETE_PROCESS", confirmed["decisions"][0]["command"])

    def test_reviewed_stock_is_not_reprocessed_by_routine(self) -> None:
        module = self.lifecycle_module()
        (self.stock / "state.json").write_text(
            json.dumps({"status": "REVIEW_REQUIRED", "review_required": True}), encoding="utf-8"
        )
        result = module.evaluate_lifecycle(
            main_facts=self.facts(), rules={},
            routine_identity={"definition_id": "indicator_follow", "routine_instance_id": "INSTANCE_A"},
            rules_identity="RULES-A",
        )
        self.assertEqual([], result["decisions"])

    def test_reset_generation_restamps_generic_deferred_budget_scope(self) -> None:
        intents = [{
            "side": "BUY", "execution_mode": "MULTI_TIME", "routine_instance_id": "INSTANCE_A",
            "code": "005930", "cycle_identity": "CYCLE-A", "buy_round": 4,
            "multi_time_plan": {"approved_round_budget": 4321},
        }]
        execution_price_reset._apply_deferred_dispatch_contract(intents)
        self.assertTrue(intents[0]["budget_scope_required"])
        self.assertEqual(4321, intents[0]["approved_budget_ceiling"])
        self.assertEqual("CYCLE-A:BUY_ROUND:4", intents[0]["budget_scope_member"]["round_identity"])


if __name__ == "__main__":
    unittest.main()
