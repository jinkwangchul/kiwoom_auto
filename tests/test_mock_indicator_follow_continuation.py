# -*- coding: utf-8 -*-

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import unittest

from mock_validation_contract import ORDER_CANCELED, ORDER_CANCEL_PENDING, ORDER_FILLED, ORDER_OPEN
from mock_validation_indicator_follow_continuation import (
    ACTION_CANCEL_EFFECT,
    ACTION_CANCEL_REQUEST,
    ACTION_EXIT,
    ACTION_FINAL_RESIDUAL,
    ACTION_RECOVERY,
    ACTION_REPLAN,
    ACTION_REPEAT,
    batch_timeout_due,
    evaluate_price_reset_policy,
    timeout_due,
)
from tests.test_mock_indicator_follow_adapter import (
    NOW,
    SESSION_ID,
    MockIndicatorFollowAdapterTest,
    _buy_recovery_policy,
    _buy_rules,
    _market,
    _sell_rules,
)


class MockIndicatorFollowContinuationTest(MockIndicatorFollowAdapterTest):
    def test_batch_timeout_anchors_last_actual_child_submission(self):
        plan = {"children": [{"mock_order_id": "O1"}, {"mock_order_id": "O2"}]}
        orders = [
            {"mock_order_id": "O1", "accepted_at": NOW.isoformat()},
            {"mock_order_id": "O2", "accepted_at": (NOW + timedelta(seconds=5)).isoformat()},
        ]
        policy = {"scope": "BATCH", "timeout_ms": 10_000}
        before = batch_timeout_due(
            plan, orders, policy,
            as_of=NOW + timedelta(seconds=14, milliseconds=999),
        )
        boundary = batch_timeout_due(
            plan, orders, policy, as_of=NOW + timedelta(seconds=15),
        )
        self.assertFalse(before["eligible"])
        self.assertTrue(boundary["eligible"])
        self.assertEqual("LAST_CHILD_MOCK_OPENED_AT", boundary["anchor"])
        self.assertEqual(
            (NOW + timedelta(seconds=5)).isoformat(timespec="microseconds"),
            boundary["anchor_at"],
        )

    def test_timeout_exact_boundary_requests_then_confirms_virtual_cancel(self):
        rules = _buy_rules(qty=1)
        rules["buy"]["execution"]["base"]["unfilled_timeout_policy"] = {
            "policy": "CANCEL_PENDING_ORDER", "enabled": True, "action": "CANCEL",
            "scope": "EACH", "configured_value": 10, "configured_unit": "SECOND",
        }
        repository, _, _, adapter, _ = self.build({"A": rules})
        resting = _market(asks=((110, 100),), bids=((99, 100),))
        first = self.evaluate(adapter, cycle="T0", market=resting)
        order = first["orders"][0]
        before = timeout_due(order, {"timeout_ms": 10_000, "scope": "EACH"}, as_of=NOW + timedelta(seconds=9, milliseconds=999))
        boundary = self.evaluate(
            adapter, cycle="T1", at=NOW + timedelta(seconds=10),
            market=_market(now=NOW + timedelta(seconds=10), asks=((110, 100),), sequence=2),
        )
        effect = self.evaluate(
            adapter, cycle="T2", at=NOW + timedelta(seconds=11),
            market=_market(now=NOW + timedelta(seconds=11), asks=((110, 100),), sequence=3),
        )
        current = repository.read_session(SESSION_ID)["orders"][0]
        self.assertFalse(before["eligible"])
        self.assertEqual((ACTION_CANCEL_REQUEST, ORDER_CANCEL_PENDING), (boundary["action"], boundary["orders"][0]["state"]))
        self.assertEqual((ACTION_CANCEL_EFFECT, ORDER_CANCELED), (effect["action"], current["state"]))
        self.assertEqual(0, current["filled_qty"])

    def test_buy_reset_precedes_timeout_and_replans_same_round_next_generation(self):
        rules = _buy_rules(qty=3, budget=10_000)
        base = rules["buy"]["execution"]["base"]
        base["unfilled_timeout_policy"] = {
            "policy": "CANCEL_PENDING_ORDER", "enabled": True, "action": "CANCEL",
            "scope": "EACH", "configured_value": 0, "configured_unit": "SECOND",
        }
        base["buy_price_reset_policy"] = {
            "policy": "BUY_PRICE_CHANGE_RESET", "enabled": True, "action": "RESET",
            "left_source": "ORDER_PRICE", "right_source": "CURRENT_PRICE",
            "direction": "UP", "compare": ">=", "threshold_percent": 1,
        }
        repository, _, _, adapter, _ = self.build({"A": rules})
        initial = self.evaluate(adapter, cycle="R0", market=_market(price=100, asks=((110, 100),)))
        request = self.evaluate(
            adapter, cycle="R1", at=NOW + timedelta(seconds=1),
            market=_market(price=102, now=NOW + timedelta(seconds=1), asks=((110, 100),), sequence=2),
        )
        effect = self.evaluate(
            adapter, cycle="R2", at=NOW + timedelta(seconds=2),
            market=_market(price=102, now=NOW + timedelta(seconds=2), asks=((110, 100),), sequence=3),
        )
        replanned = self.evaluate(
            adapter, cycle="R3", at=NOW + timedelta(seconds=3),
            market=_market(price=102, now=NOW + timedelta(seconds=3), asks=((110, 100),), sequence=4),
        )
        plans = repository.read_session(SESSION_ID)["progression_by_instance"]["A"]["indicator_follow_mock_adapter"]["plans"]
        self.assertEqual(ORDER_OPEN, initial["orders"][0]["state"])
        self.assertEqual((ACTION_CANCEL_REQUEST, "BUY_PRICE_RESET_CANCEL_REQUIRED"), (request["action"], request["reason"]))
        self.assertEqual(ACTION_CANCEL_EFFECT, effect["action"])
        self.assertEqual(ACTION_REPLAN, replanned["continuation_action"])
        self.assertEqual([(1, 0), (1, 1)], [(item["round"], item["generation"]) for item in plans])
        self.assertEqual(plans[0]["execution_process_id"], plans[1]["execution_process_id"])
        self.assertEqual(plans[0]["source_signal_id"], plans[1]["source_signal_id"])

    def test_cancel_confirmed_partial_residual_is_recovered_once_without_quantity_duplication(self):
        rules = _buy_rules(qty=3, budget=10_000)
        rules["buy"]["execution"]["base"]["unfilled_timeout_policy"] = {
            "policy": "CANCEL_PENDING_ORDER", "enabled": True, "action": "CANCEL",
            "scope": "EACH", "configured_value": 0, "configured_unit": "SECOND",
        }
        rules["buy"]["execution"]["cycle"] = _buy_recovery_policy()
        repository, _, _, adapter, _ = self.build({"A": rules})
        partial = self.evaluate(
            adapter, cycle="CR0", market=_market(price=100, asks=((100, 1),), sequence=1),
        )
        self.evaluate(
            adapter, cycle="CR1", at=NOW + timedelta(milliseconds=100),
            market=_market(now=NOW, asks=((110, 100),), sequence=2),
        )
        self.evaluate(
            adapter, cycle="CR2", at=NOW + timedelta(milliseconds=200),
            market=_market(now=NOW, asks=((110, 100),), sequence=3),
        )
        recovered = self.evaluate(
            adapter, cycle="CR3", at=NOW + timedelta(milliseconds=300),
            market=_market(price=102, now=NOW, asks=((102, 100),), sequence=4),
        )
        replay = self.evaluate(
            adapter, cycle="CR4", at=NOW + timedelta(milliseconds=400),
            market=_market(price=102, now=NOW, asks=((102, 100),), sequence=5),
        )
        document = repository.read_session(SESSION_ID)
        plans = document["progression_by_instance"]["A"]["indicator_follow_mock_adapter"]["plans"]
        position = next(item for item in document["positions"] if item["routine_instance_id"] == "A")
        self.assertEqual((1, 2), (partial["orders"][0]["filled_qty"], partial["orders"][0]["remaining_qty"]))
        self.assertEqual(ACTION_RECOVERY, recovered["continuation_action"])
        self.assertEqual((3, 1, 2), (position["holding_qty"], plans[0]["filled_qty"], plans[1]["total_qty"]))
        self.assertEqual((plans[0]["source_signal_id"], plans[0]["execution_process_id"], plans[0]["round"]),
                         (plans[1]["source_signal_id"], plans[1]["execution_process_id"], plans[1]["round"]))
        self.assertEqual(1, plans[1]["generation"])
        self.assertNotEqual(ACTION_RECOVERY, replay.get("continuation_action"))

    def test_each_timeout_recovers_one_child_before_later_base_time_children(self):
        rules = _buy_rules(mode="MULTI_TIME", qty=3, budget=10_000)
        rules["buy"]["execution"]["base"]["unfilled_timeout_policy"] = {
            "policy": "CANCEL_PENDING_ORDER", "enabled": True, "action": "CANCEL",
            "scope": "EACH", "configured_value": 0, "configured_unit": "SECOND",
        }
        rules["buy"]["execution"]["cycle"] = _buy_recovery_policy()
        repository, _, _, adapter, _ = self.build({"A": rules})
        self.evaluate(adapter, cycle="ER0", market=_market(asks=((110, 100),), sequence=1))
        self.evaluate(adapter, cycle="ER1", at=NOW + timedelta(milliseconds=100), market=_market(now=NOW, asks=((110, 100),), sequence=2))
        self.evaluate(adapter, cycle="ER2", at=NOW + timedelta(milliseconds=200), market=_market(now=NOW, asks=((110, 100),), sequence=3))
        recovered = self.evaluate(adapter, cycle="ER3", at=NOW + timedelta(milliseconds=300), market=_market(now=NOW, asks=((100, 100),), sequence=4))
        resumed = self.evaluate(adapter, cycle="ER4", at=NOW + timedelta(milliseconds=400), market=_market(now=NOW, asks=((110, 100),), sequence=5))
        plans = repository.read_session(SESSION_ID)["progression_by_instance"]["A"]["indicator_follow_mock_adapter"]["plans"]
        base, recovery = plans
        self.assertEqual(ACTION_RECOVERY, recovered["continuation_action"])
        self.assertEqual((1, 1), (recovery["total_qty"], recovery["filled_qty"]))
        self.assertEqual(("ACTIVE", 1), (base["state"], sum(bool(item["mock_order_id"]) for item in base["children"])))
        self.assertEqual(("WAIT", "TIME_CHILD_NOT_DUE"), (resumed["status"], resumed["reason"]))

    def test_recovery_origin_reset_restarts_base_contract_without_recovery_fields(self):
        response = {
            "policy": "BUY_PRICE_CHANGE_RESPONSE", "enabled": True, "action": "RESET",
            "left_source": "ORDER_PRICE", "right_source": "CURRENT_PRICE",
            "direction": "UP", "compare": ">=", "threshold_percent": 1,
        }
        rules = _buy_rules(qty=3, budget=10_000)
        rules["buy"]["execution"]["base"]["unfilled_timeout_policy"] = {
            "policy": "CANCEL_PENDING_ORDER", "enabled": True, "action": "CANCEL",
            "scope": "EACH", "configured_value": 0, "configured_unit": "SECOND",
        }
        rules["buy"]["execution"]["cycle"] = _buy_recovery_policy(price_responses=[response])
        repository, _, _, adapter, _ = self.build({"A": rules})
        self.evaluate(adapter, cycle="RR0", market=_market(asks=((110, 100),), sequence=1))
        self.evaluate(adapter, cycle="RR1", at=NOW + timedelta(milliseconds=100), market=_market(now=NOW, asks=((110, 100),), sequence=2))
        self.evaluate(adapter, cycle="RR2", at=NOW + timedelta(milliseconds=200), market=_market(now=NOW, asks=((110, 100),), sequence=3))
        self.evaluate(adapter, cycle="RR3", at=NOW + timedelta(milliseconds=300), market=_market(price=102, now=NOW, asks=((110, 100),), sequence=4))
        request = self.evaluate(adapter, cycle="RR4", at=NOW + timedelta(milliseconds=400), market=_market(price=104, now=NOW, asks=((110, 100),), sequence=5))
        self.evaluate(adapter, cycle="RR5", at=NOW + timedelta(milliseconds=500), market=_market(price=104, now=NOW, asks=((110, 100),), sequence=6))
        replanned = self.evaluate(adapter, cycle="RR6", at=NOW + timedelta(milliseconds=600), market=_market(price=104, now=NOW, asks=((110, 100),), sequence=7))
        plans = repository.read_session(SESSION_ID)["progression_by_instance"]["A"]["indicator_follow_mock_adapter"]["plans"]
        reset_intent = plans[-1]["children"][0]["intent"]
        self.assertEqual(ACTION_CANCEL_REQUEST, request["action"])
        self.assertEqual(ACTION_REPLAN, replanned["continuation_action"])
        self.assertEqual((0, 1, 2), tuple(item["generation"] for item in plans))
        self.assertEqual((plans[0]["source_signal_id"], plans[0]["execution_process_id"], plans[0]["round"]),
                         (plans[-1]["source_signal_id"], plans[-1]["execution_process_id"], plans[-1]["round"]))
        for key in (
            "recovery_cycle", "recovery_generation", "confirmed_residual_quantity",
            "recovery_source_snapshot_hash", "recovery_source_original_order_nos",
        ):
            self.assertNotIn(key, reset_intent)

    def test_price_cancel_batch_enters_confirmed_residual_recovery(self):
        response = {
            "policy": "BUY_PRICE_CHANGE_RESPONSE", "enabled": True, "action": "CANCEL_BATCH",
            "left_source": "ORDER_PRICE", "right_source": "CURRENT_PRICE",
            "direction": "UP", "compare": ">=", "threshold_percent": 1,
        }
        rules = _buy_rules(qty=2, budget=10_000)
        base = rules["buy"]["execution"]["base"]
        base["buy_price_response_policies"] = [response]
        rules["buy"]["execution"]["cycle"] = _buy_recovery_policy()
        repository, _, _, adapter, _ = self.build({"A": rules})
        self.evaluate(adapter, cycle="PB0", market=_market(price=100, asks=((110, 100),), sequence=1))
        request = self.evaluate(adapter, cycle="PB1", at=NOW + timedelta(milliseconds=100), market=_market(price=102, now=NOW, asks=((110, 100),), sequence=2))
        self.evaluate(adapter, cycle="PB2", at=NOW + timedelta(milliseconds=200), market=_market(price=102, now=NOW, asks=((110, 100),), sequence=3))
        recovered = self.evaluate(adapter, cycle="PB3", at=NOW + timedelta(milliseconds=300), market=_market(price=102, now=NOW, asks=((102, 100),), sequence=4))
        plans = repository.read_session(SESSION_ID)["progression_by_instance"]["A"]["indicator_follow_mock_adapter"]["plans"]
        self.assertEqual((ACTION_CANCEL_REQUEST, "BUY_PRICE_CHANGE_CANCEL_BATCH"), (request["action"], request["reason"]))
        self.assertEqual(ACTION_RECOVERY, recovered["continuation_action"])
        self.assertEqual(2, plans[-1]["total_qty"])

    def test_signal_priority_uses_production_routine_decision_and_waits_for_cancel_effect(self):
        rules = _buy_rules(qty=1, budget=10_000)
        rules["signal_runtime_policy"] = {"duplicate_priority": "TRAILING"}
        signals = iter((
            {"signal": "BUY", "signal_id": "SIG-OLD", "reason": "old"},
            {"signal": "BUY", "signal_id": "SIG-NEW", "reason": "new"},
        ))
        repository, _, engine, _, _ = self.build({"A": rules})
        from mock_validation_indicator_follow_adapter import MockIndicatorFollowRoutineAdapter
        adapter = MockIndicatorFollowRoutineAdapter(
            repository, engine, now_factory=lambda: NOW,
            evaluator=lambda *_args: next(signals),
        )
        first = self.evaluate(adapter, cycle="SP0", market=_market(asks=((110, 100),), sequence=1))
        requested = self.evaluate(adapter, cycle="SP1", at=NOW + timedelta(milliseconds=100), market=_market(now=NOW, asks=((110, 100),), sequence=2))
        effect = self.evaluate(adapter, cycle="SP2", at=NOW + timedelta(milliseconds=200), market=_market(now=NOW, asks=((110, 100),), sequence=3))
        successor = self.evaluate(adapter, cycle="SP3", at=NOW + timedelta(milliseconds=300), market=_market(now=NOW, asks=((110, 100),), sequence=4))
        plans = repository.read_session(SESSION_ID)["progression_by_instance"]["A"]["indicator_follow_mock_adapter"]["plans"]
        self.assertEqual(ORDER_OPEN, first["orders"][0]["state"])
        self.assertEqual((ACTION_CANCEL_REQUEST, ACTION_CANCEL_EFFECT), (requested["action"], effect["action"]))
        self.assertEqual("SIG-NEW", successor["signal"]["signal_id"])
        self.assertEqual(["SIG-OLD", "SIG-NEW"], [item["source_signal_id"] for item in plans])
        self.assertEqual("COMPLETED", plans[0]["state"])
        self.assertEqual("DUPLICATE_SIGNAL_REPLACED_AFTER_CANCEL_EFFECT", plans[0]["superseded_reason"])

    def test_leading_signal_priority_ignores_successor_without_touching_open_order(self):
        rules = _buy_rules(qty=1, budget=10_000)
        rules["signal_runtime_policy"] = {"duplicate_priority": "LEADING"}
        signals = iter((
            {"signal": "BUY", "signal_id": "SIG-OLD", "reason": "old"},
            {"signal": "SELL", "signal_id": "SIG-IGNORED", "reason": "new"},
        ))
        repository, _, engine, _, _ = self.build({"A": rules})
        from mock_validation_indicator_follow_adapter import MockIndicatorFollowRoutineAdapter
        adapter = MockIndicatorFollowRoutineAdapter(repository, engine, now_factory=lambda: NOW, evaluator=lambda *_args: next(signals))
        self.evaluate(adapter, cycle="SL0", market=_market(asks=((110, 100),), sequence=1))
        ignored = self.evaluate(adapter, cycle="SL1", at=NOW + timedelta(milliseconds=100), market=_market(now=NOW, asks=((110, 100),), sequence=2))
        document = repository.read_session(SESSION_ID)
        self.assertEqual(("NOOP", "DUPLICATE_SIGNAL_LEADING_PRIORITY"), (ignored["status"], ignored["reason"]))
        self.assertEqual((1, ORDER_OPEN), (len(document["orders"]), document["orders"][0]["state"]))
        self.assertEqual(1, len(document["progression_by_instance"]["A"]["indicator_follow_mock_adapter"]["plans"]))

    def test_signal_arbitration_stale_mock_facts_blocks_writer_then_re_evaluates(self):
        rules = _buy_rules(qty=1, budget=10_000)
        rules["signal_runtime_policy"] = {"duplicate_priority": "TRAILING"}
        signals = iter((
            {"signal": "BUY", "signal_id": "SIG-OLD", "reason": "old"},
            {"signal": "BUY", "signal_id": "SIG-NEW", "reason": "new"},
        ))
        repository, _, engine, _, _ = self.build({"A": rules})
        from mock_validation_indicator_follow_adapter import MockIndicatorFollowRoutineAdapter
        adapter = MockIndicatorFollowRoutineAdapter(repository, engine, now_factory=lambda: NOW, evaluator=lambda *_args: next(signals))
        self.evaluate(adapter, cycle="SF0", market=_market(asks=((110, 100),), sequence=1))
        production_evaluator = adapter._lifecycle_evaluator

        def racing_evaluator(**kwargs):
            result = production_evaluator(**kwargs)
            before = repository.read_session(SESSION_ID)

            def mutation(document):
                document["progression_by_instance"]["A"]["race_marker"] = "AFTER_FACTS_CAPTURE"
                return document

            repository.mutate_session(SESSION_ID, mutation, expected_revision=before["revision"])
            return result

        adapter._lifecycle_evaluator = racing_evaluator
        stale = self.evaluate(adapter, cycle="SF1", at=NOW + timedelta(milliseconds=100), market=_market(now=NOW, asks=((110, 100),), sequence=2))
        self.assertEqual(("WAIT", "MOCK_SIGNAL_FACTS_STALE"), (stale["status"], stale["reason"]))
        self.assertEqual(ORDER_OPEN, repository.read_session(SESSION_ID)["orders"][0]["state"])
        adapter._lifecycle_evaluator = production_evaluator
        fresh = self.evaluate(adapter, cycle="SF2", at=NOW + timedelta(milliseconds=200), market=_market(now=NOW, asks=((110, 100),), sequence=3))
        self.assertEqual(ACTION_CANCEL_REQUEST, fresh["action"])

    def test_partial_fill_is_preserved_by_timeout_cancel(self):
        rules = _sell_rules()
        setting = rules["sell"]["method"]["setting_a"]
        setting.update({"perform3_title_combo": "미체결", "perform3_pending_scope": "매회",
                        "perform3_pending_value": "1", "perform3_pending_unit": "초"})
        repository, service, _, adapter, _ = self.build({"A": rules}, signal="SELL")
        self.seed_holding(repository, service, qty=3)
        first = self.evaluate(adapter, cycle="P0", market=_market(bids=((100, 1),)))
        requested = self.evaluate(
            adapter, cycle="P1", at=NOW + timedelta(seconds=1),
            market=_market(now=NOW + timedelta(seconds=1), bids=((90, 100),), sequence=2),
        )
        self.evaluate(
            adapter, cycle="P2", at=NOW + timedelta(seconds=2),
            market=_market(now=NOW + timedelta(seconds=2), bids=((90, 100),), sequence=3),
        )
        document = repository.read_session(SESSION_ID)
        order = document["orders"][0]
        position = next(item for item in document["positions"] if item["routine_instance_id"] == "A")
        self.assertEqual((1, 2), (first["orders"][0]["filled_qty"], first["orders"][0]["remaining_qty"]))
        self.assertEqual(ACTION_CANCEL_REQUEST, requested["action"])
        self.assertEqual((ORDER_CANCELED, 1, 2), (order["state"], order["filled_qty"], order["remaining_qty"]))
        self.assertEqual(2, position["holding_qty"])

    def test_sell_price_reset_is_cancel_first_and_uses_current_mock_holding(self):
        rules = _sell_rules()
        setting = rules["sell"]["method"]["setting_a"]
        setting.update({
            "perform3_title_combo": "가격비교", "perform3_price_action": "매도리셋",
            "perform3_price_left": "주문가", "perform3_price_right": "현재가",
            "perform3_price_direction": "상향", "perform3_price_value": "1",
            "perform3_price_compare": "이상",
        })
        repository, service, _, adapter, _ = self.build({"A": rules}, signal="SELL")
        self.seed_holding(repository, service, qty=3)
        initial = self.evaluate(adapter, cycle="SR0", market=_market(price=100, bids=((90, 100),)))
        request = self.evaluate(adapter, cycle="SR1", at=NOW + timedelta(milliseconds=100),
                                market=_market(price=102, now=NOW, bids=((102, 100),), sequence=2))
        effect = self.evaluate(adapter, cycle="SR2", at=NOW + timedelta(milliseconds=200),
                               market=_market(price=102, now=NOW, bids=((102, 100),), sequence=3))
        replanned = self.evaluate(adapter, cycle="SR3", at=NOW + timedelta(milliseconds=300),
                                  market=_market(price=102, now=NOW, bids=((102, 100),), sequence=4))
        plans = repository.read_session(SESSION_ID)["progression_by_instance"]["A"]["indicator_follow_mock_adapter"]["plans"]
        self.assertEqual(ORDER_OPEN, initial["orders"][0]["state"])
        self.assertEqual(ACTION_CANCEL_REQUEST, request["action"])
        self.assertEqual(ACTION_CANCEL_EFFECT, effect["action"])
        self.assertEqual(ACTION_REPLAN, replanned["continuation_action"])
        self.assertEqual((1, 3), (plans[-1]["generation"], plans[-1]["total_qty"]))
        self.assertEqual(plans[0]["execution_process_id"], plans[-1]["execution_process_id"])

    def test_buy_exit_or_count_uses_completed_repeat_rounds_and_is_durable(self):
        rules = _buy_rules(qty=1, budget=10_000)
        timeout = {
            "policy": "CANCEL_PENDING_ORDER", "enabled": True, "action": "CANCEL",
            "scope": "EACH", "configured_value": 0, "configured_unit": "SECOND",
        }
        rules["buy"]["execution"]["base"]["unfilled_timeout_policy"] = timeout
        rules["buy"]["execution"]["cycle"] = _buy_recovery_policy()
        rules["buy"]["execution"]["base"]["buy_exit_policy"] = {
            "policy": "BUY_RECOVERY_EXIT", "enabled": True, "logic": "OR",
            "conditions": [{"condition_type": "COUNT", "target_recovery_generations": 1}],
        }
        repository, _, _, adapter, _ = self.build({"A": rules})
        resting = lambda at, seq: _market(price=102, now=at, asks=((110, 100),), sequence=seq)
        self.evaluate(adapter, cycle="E0", market=resting(NOW, 1))
        self.evaluate(adapter, cycle="E1", at=NOW + timedelta(milliseconds=100), market=resting(NOW, 2))
        self.evaluate(adapter, cycle="E2", at=NOW + timedelta(milliseconds=200), market=resting(NOW, 3))
        recovered = self.evaluate(
            adapter, cycle="E3", at=NOW + timedelta(milliseconds=300),
            market=_market(price=102, now=NOW, asks=((102, 100),), sequence=4),
        )
        exited = self.evaluate(
            adapter, cycle="E4", at=NOW + timedelta(milliseconds=400),
            market=_market(price=102, now=NOW, sequence=5),
        )
        blocked = self.evaluate(
            adapter, cycle="E5", at=NOW + timedelta(milliseconds=500),
            market=_market(price=102, now=NOW, sequence=6),
        )
        document = repository.read_session(SESSION_ID)
        state = document["progression_by_instance"]["A"]["indicator_follow_mock_continuation"]
        self.assertEqual(ACTION_RECOVERY, recovered["continuation_action"])
        self.assertEqual(ACTION_EXIT, exited["action"])
        self.assertEqual("BUY_EXIT_ACTIVE", blocked["reason"])
        self.assertEqual(1, state["buy_exit"]["completed_recovery_generations"])
        self.assertEqual(1, document["cycle_state_by_instance"]["A"]["confirmed_buy_round"])

    def test_sell_repeat_exit_then_final_residual_market_is_last(self):
        rules = _sell_rules()
        setting = rules["sell"]["method"]["setting_a"]
        setting.update({
            "repeat_perform1_title_combo": "단일호가", "repeat_perform1_single_combo": "주문가",
            "repeat_perform2_title_combo": "선택없음",
            "exit_count_check": True, "exit_count_line": "1",
        })
        repository, service, engine, adapter, _ = self.build({"A": rules}, signal="SELL")
        self.seed_holding(repository, service, qty=3)
        # Seed a completed SELL generation that intentionally sold one share.
        first = self.evaluate(adapter, cycle="S0", market=_market(bids=((100, 1),)))
        engine.cancel_order(SESSION_ID, first["orders"][0]["mock_order_id"], command_id="MC-seed-cancel")
        repeat = self.evaluate(adapter, cycle="S1", at=NOW + timedelta(milliseconds=100),
                               market=_market(now=NOW, bids=((100, 1),), sequence=2))
        repeat_order = repeat["orders"][0]
        if repeat_order["state"] != ORDER_FILLED:
            engine.cancel_order(SESSION_ID, repeat_order["mock_order_id"], command_id="MC-repeat-cancel")
        exited = self.evaluate(adapter, cycle="S2", at=NOW + timedelta(milliseconds=200),
                               market=_market(now=NOW, bids=((100, 100),), sequence=3))
        residual = self.evaluate(adapter, cycle="S3", at=NOW + timedelta(milliseconds=300),
                                 market=_market(now=NOW, bids=((100, 100),), sequence=4))
        self.assertEqual(ACTION_REPEAT, repeat["continuation_action"])
        self.assertEqual(ACTION_EXIT, exited["action"])
        self.assertEqual(ACTION_FINAL_RESIDUAL, residual["continuation_action"])
        self.assertEqual("MARKET", residual["orders"][0]["order_type"])
        self.assertEqual(0, next(item for item in repository.read_session(SESSION_ID)["positions"] if item["routine_instance_id"] == "A")["holding_qty"])
        event_types = {item["event_type"] for item in repository.read_events(SESSION_ID)}
        self.assertIn("SELL_REPEAT_TRIGGERED", event_types)
        self.assertIn("SELL_REPEAT_GENERATION_STARTED", event_types)
        self.assertIn("SELL_REPEAT_EXIT_TRIGGERED", event_types)
        self.assertIn("FINAL_RESIDUAL_MARKET_STARTED", event_types)

    def test_price_reset_waits_on_stale_current_price(self):
        policy = {"enabled": True, "left_source": "ORDER_PRICE", "right_source": "CURRENT_PRICE",
                  "direction": "UP", "compare": ">=", "threshold_percent": 1}
        result = evaluate_price_reset_policy(policy, order_price=100, current_price=None, average_price=90)
        self.assertEqual((True, False, "CURRENT_PRICE_UNAVAILABLE"),
                         (result["active"], result["triggered"], result["reason"]))

    def test_cancel_pending_recovery_finishes_before_any_replacement(self):
        rules = _buy_rules(qty=1)
        rules["buy"]["execution"]["base"]["unfilled_timeout_policy"] = {
            "policy": "CANCEL_PENDING_ORDER", "enabled": True, "action": "CANCEL",
            "scope": "EACH", "configured_value": 0, "configured_unit": "SECOND",
        }
        repository, _, engine, adapter, _ = self.build({"A": rules})
        self.evaluate(adapter, cycle="RC0", market=_market(asks=((110, 100),)))
        requested = self.evaluate(adapter, cycle="RC1", at=NOW + timedelta(milliseconds=100),
                                  market=_market(now=NOW, asks=((110, 100),), sequence=2))
        from mock_validation_indicator_follow_adapter import MockIndicatorFollowRoutineAdapter
        restarted = MockIndicatorFollowRoutineAdapter(repository, engine, now_factory=lambda: NOW,
                                                       evaluator=lambda *_args: {"signal": "BUY", "reason": "new"})
        confirmed = self.evaluate(restarted, cycle="RC2", at=NOW + timedelta(milliseconds=200),
                                  market=_market(now=NOW, asks=((110, 100),), sequence=3))
        self.assertEqual(ACTION_CANCEL_REQUEST, requested["action"])
        self.assertEqual(ACTION_CANCEL_EFFECT, confirmed["action"])
        self.assertEqual(1, len(repository.read_session(SESSION_ID)["orders"]))

    def test_completed_cycle_exit_evidence_does_not_block_next_cycle(self):
        rules = _buy_rules(qty=1, budget=10_000)
        rules["sell"] = deepcopy(_sell_rules()["sell"])
        timeout = {
            "policy": "CANCEL_PENDING_ORDER", "enabled": True, "action": "CANCEL",
            "scope": "EACH", "configured_value": 0, "configured_unit": "SECOND",
        }
        rules["buy"]["execution"]["base"]["unfilled_timeout_policy"] = timeout
        rules["buy"]["execution"]["cycle"] = _buy_recovery_policy()
        rules["buy"]["execution"]["base"]["buy_exit_policy"] = {
            "policy": "BUY_RECOVERY_EXIT", "enabled": True, "logic": "OR",
            "conditions": [{"condition_type": "COUNT", "target_recovery_generations": 1}],
        }
        repository, _, _, adapter, selected = self.build({"A": rules})
        first = self.evaluate(adapter, cycle="HC0", at=NOW, market=_market(now=NOW, asks=((110, 100),), sequence=1))
        self.evaluate(adapter, cycle="HC1", at=NOW + timedelta(milliseconds=100), market=_market(now=NOW, asks=((110, 100),), sequence=2))
        self.evaluate(adapter, cycle="HC2", at=NOW + timedelta(milliseconds=200), market=_market(now=NOW, asks=((110, 100),), sequence=3))
        self.evaluate(adapter, cycle="HC3", at=NOW + timedelta(milliseconds=300), market=_market(price=102, now=NOW, asks=((102, 100),), sequence=4))
        exited = self.evaluate(adapter, cycle="HC4", at=NOW + timedelta(milliseconds=400), market=_market(now=NOW, sequence=5))
        selected["value"] = "SELL"
        sold = self.evaluate(
            adapter, cycle="HC5", at=NOW + timedelta(milliseconds=500),
            market=_market(price=102, now=NOW, bids=((102, 100),), sequence=6),
        )
        selected["value"] = "BUY"
        restarted = self.evaluate(adapter, cycle="HC6", at=NOW + timedelta(milliseconds=600), market=_market(now=NOW, sequence=7))
        plans = repository.read_session(SESSION_ID)["progression_by_instance"]["A"]["indicator_follow_mock_adapter"]["plans"]
        self.assertEqual(ACTION_EXIT, exited["action"])
        self.assertEqual("SELL", sold["orders"][0]["side"])
        self.assertEqual("BUY", restarted["orders"][0]["side"])
        self.assertEqual(1, plans[-1]["round"])
        self.assertNotEqual(plans[0]["cycle_scope_identity"], plans[-1]["cycle_scope_identity"])


if __name__ == "__main__":
    unittest.main()
