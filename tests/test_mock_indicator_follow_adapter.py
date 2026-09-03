# -*- coding: utf-8 -*-

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from mock_validation_contract import ORDER_OPEN, ORDER_PARTIAL_FILL, payload_hash
from mock_validation_indicator_follow_adapter import (
    MockIndicatorFollowRoutineAdapter,
    RESULT_NOOP,
    RESULT_NO_SIGNAL,
    RESULT_PROGRESSED,
    RESULT_WAIT,
    _pure_routine_functions,
)
from mock_validation_market_data import (
    MockMarketSnapshot,
    MockOrderbookLevel,
    MockOrderbookSnapshot,
    MockTradeSnapshot,
)
from mock_validation_repository import MockValidationRepository
from mock_validation_session_service import MockValidationSessionService
from mock_validation_virtual_execution import MockExecutionPolicy, MockVirtualExecutionEngine


SEOUL = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 3, 10, 0, 0, tzinfo=SEOUL)
SESSION_ID = "MV-00000000000000000000000000000401"


def _levels(values):
    return tuple(
        MockOrderbookLevel(index + 1, *(values[index] if index < len(values) else (None, None)))
        for index in range(10)
    )


def _market(*, price=102, asks=((100, 100),), bids=((100, 100),), now=NOW, sequence=1):
    ask_levels = _levels(asks)
    bid_levels = _levels(bids)
    content = {"asks": [(v.price, v.quantity) for v in ask_levels], "bids": [(v.price, v.quantity) for v in bid_levels]}
    book = MockOrderbookSnapshot(
        stock_code="005930", real_type="주식호가잔량", quote_time_raw="100000",
        received_at=now.isoformat(timespec="microseconds"), connection_epoch=1,
        login_session_id="LOGIN-1", receive_sequence=sequence,
        asks=ask_levels, bids=bid_levels, total_ask_qty=None, total_bid_qty=None,
        content_hash=payload_hash(content), snapshot_identity=f"MOB-{sequence}-{payload_hash(content)}",
    )
    trade = MockTradeSnapshot(
        stock_code="005930", current_price=price, execution_price=price,
        execution_qty=10, execution_qty_signed=10, trade_side="BUY",
        execution_time="100000", market_datetime=now.isoformat(),
        received_at=now.isoformat(timespec="microseconds"), connection_epoch=1,
        login_session_id="LOGIN-1", receive_sequence=sequence,
        snapshot_identity=f"MTR-{sequence}-{price}",
    )
    return MockMarketSnapshot("005930", book, trade, f"MMK-{sequence}-{price}")


def _buy_rules(*, mode="SINGLE", qty=3, budget=10_000, active_buy=False):
    rules = {
        "buy": {
            "execution": {
                "base": {
                    "buy_phase": "BASE", "buy_round": 1,
                    "hoga_mode": "SINGLE", "order_price_basis": "ORDER_PRICE",
                    "hoga_up": 0, "hoga_down": 0,
                },
                "repeat": {
                    "buy_phase": "REPEAT", "starts_from_round": 2,
                    "apply_all": True, "detail_mode": "ROUND",
                    "round_operator": "ADD", "round_budget_value": 1,
                    "budget_ratio": 2,
                },
            }
        },
        "mock_validation": {
            "stock_config": {"trade_amount_type": "QUANTITY", "buy_qty": qty},
            "execution_budget": budget,
        },
    }
    base = rules["buy"]["execution"]["base"]
    if mode == "MULTI_HOGA":
        base.update({"hoga_mode": "MULTI", "hoga_up": 1, "hoga_down": 1})
    elif mode == "MULTI_TIME":
        base.update({
            "point_mode": "MULTI_TIME", "point_count": 3, "point_value": 10,
            "point_unit": "SECOND", "point_range": "INTERVAL",
            "time_order_price_basis": "ORDER_PRICE",
        })
    elif mode == "MULTI_RATIO":
        base.update({
            "point_mode": "MULTI_RATIO", "ratio_count": 3,
            "ratio_left": "ORDER_PRICE", "ratio_right": "CURRENT_PRICE",
            "ratio_direction": "UP", "ratio_value": 0.5, "ratio_compare": ">=",
        })
    elif active_buy:
        base["point_mode"] = "ACTIVE_BUY"
    return rules


def _sell_rules(*, mode="SINGLE", qty=3):
    setting = {
        "perform1_title_combo": "단일호가",
        "perform1_single_combo": "주문가",
        "perform2_title_combo": "선택없음",
    }
    if mode == "MULTI_HOGA":
        setting.update({
            "perform1_title_combo": "다중호가",
            "perform1_multi_up_line": "1", "perform1_multi_down_line": "1",
        })
    elif mode == "MULTI_TIME":
        setting.update({
            "perform2_title_combo": "다중시간", "perform2_time_count": "3",
            "perform2_time_value": "10", "perform2_time_unit": "초",
            "perform2_time_range": "간격", "perform2_time_order": "주문가",
        })
    elif mode == "MULTI_RATIO":
        setting.update({
            "perform2_title_combo": "다중비율", "perform2_ratio_count": "3",
            "perform2_ratio_left": "주문가", "perform2_ratio_right": "현재가",
            "perform2_ratio_direction": "상향", "perform2_ratio_value": "0.5",
            "perform2_ratio_compare": "이상",
        })
    return {
        "sell": {"method": {"selected_sets": ["setting_a"], "setting_a": setting}},
        "mock_validation": {
            "stock_config": {"trade_amount_type": "QUANTITY", "buy_qty": qty},
            "execution_budget": 10_000,
        },
    }


def _reference(rules_by_instance):
    instances = []
    for instance_id, rules in rules_by_instance.items():
        instances.append({
            "routine_instance_id": instance_id,
            "routine_definition_id": "indicator-follow",
            "routine_type": "INDICATOR_FOLLOW",
            "rules_snapshot": deepcopy(rules),
            "rules_hash": payload_hash(rules),
        })
    snapshot = {
        "stock_code": "005930", "stock_name": "삼성전자",
        "stock_identity_reference": "STOCK-005930",
        "snapshot_created_at": NOW.isoformat(), "routine_instances": instances,
    }
    snapshot["snapshot_hash"] = payload_hash(snapshot)
    return snapshot


class MockIndicatorFollowAdapterTest(unittest.TestCase):
    def build(self, rules_by_instance, *, signal="BUY"):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        repository = MockValidationRepository(
            Path(temporary.name) / "mock_validation", project_root=Path(temporary.name) / "project"
        )
        service = MockValidationSessionService(repository, now_factory=lambda: NOW.isoformat(timespec="microseconds"))
        service.create_stock_session(
            reference_snapshot=_reference(rules_by_instance), validation_session_id=SESSION_ID,
            command_id="MC-create",
        )
        service.start_stock_mock_session(SESSION_ID, command_id="MC-start")
        engine = MockVirtualExecutionEngine(repository, now_factory=lambda: NOW)
        selected = {"value": signal}
        adapter = MockIndicatorFollowRoutineAdapter(
            repository, engine, now_factory=lambda: NOW,
            evaluator=lambda *_args: {"signal": selected["value"], "reason": "fixture"},
        )
        return repository, service, engine, adapter, selected

    def evaluate(self, adapter, *, instance="A", cycle="C1", at=NOW, market=None):
        return adapter.evaluate_cycle(
            SESSION_ID, routine_instance_id=instance,
            candles=[{"close": 100, "volume": 10} for _ in range(5)],
            market=market or _market(now=at), policy=MockExecutionPolicy(1, "LOGIN-1", 2, 2),
            evaluation_cycle_id=cycle, evaluated_at=at,
        )

    def seed_holding(self, repository, service, *, instance="A", qty=3):
        service.set_instance_position(
            SESSION_ID, instance, holding_qty=qty, available_qty=qty,
            average_price=90, realized_cost_basis=qty * 90,
            command_id=f"MC-position-{instance}-{qty}",
        )
        before = repository.read_session(SESSION_ID)

        def mutation(document):
            document["cycle_state_by_instance"][instance] = {
                "status": "resolved", "active": True, "cycle_identity": f"CYCLE-{instance}",
                "confirmed_buy_round": 1, "cumulative_filled_buy_amount": qty * 90,
            }
            return document

        repository.mutate_session(SESSION_ID, mutation, expected_revision=before["revision"])

    def test_buy_single_fill_starts_cycle_and_same_decision_is_idempotent(self):
        repository, _, _, adapter, _ = self.build({"A": _buy_rules(qty=3)})
        first = self.evaluate(adapter)
        replay = self.evaluate(adapter)
        document = repository.read_session(SESSION_ID)
        self.assertEqual(RESULT_PROGRESSED, first["status"])
        self.assertEqual((1, 3, "FILLED"), (len(first["orders"]), first["orders"][0]["filled_qty"], first["orders"][0]["state"]))
        self.assertEqual(RESULT_NOOP, replay["status"])
        self.assertEqual(1, len(document["orders"]))
        self.assertTrue(document["cycle_state_by_instance"]["A"]["active"])
        self.assertEqual(1, document["cycle_state_by_instance"]["A"]["confirmed_buy_round"])

    def test_no_signal_and_non_running_session_create_no_orders(self):
        repository, service, _, adapter, selected = self.build({"A": _buy_rules()})
        selected["value"] = None
        no_signal = self.evaluate(adapter)
        service.end_stock_session(SESSION_ID, command_id="MC-end")
        stopped = self.evaluate(adapter, cycle="C2")
        self.assertEqual(RESULT_NO_SIGNAL, no_signal["status"])
        self.assertEqual(RESULT_WAIT, stopped["status"])
        self.assertEqual([], repository.read_history(SESSION_ID)["session_document"]["orders"])

    def test_stale_market_and_budget_shortage_are_normal_blocks(self):
        repository, _, _, adapter, _ = self.build({"A": _buy_rules(qty=3, budget=200)})
        stale = self.evaluate(adapter, market=_market(now=NOW - timedelta(seconds=10)))
        budget = self.evaluate(adapter, cycle="C2")
        self.assertEqual((RESULT_WAIT, "MOCK_ORDERBOOK_STALE"), (stale["status"], stale["reason"]))
        self.assertEqual("MOCK_EXECUTION_BUDGET_EXCEEDED", budget["reason"])
        self.assertEqual("RUNNING", repository.read_session(SESSION_ID)["session"]["state"])

    def test_wrong_stock_market_is_blocked_before_virtual_order(self):
        repository, _, _, adapter, _ = self.build({"A": _buy_rules(qty=1)})
        wrong = replace(_market(), stock_code="000660")
        result = self.evaluate(adapter, market=wrong)
        self.assertEqual("MOCK_MARKET_STOCK_MISMATCH", result["reason"])
        self.assertEqual([], repository.read_session(SESSION_ID)["orders"])

    def test_sell_priority_holding_and_active_cycle_contract(self):
        repository, service, _, adapter, selected = self.build({"A": _sell_rules()}, signal="SELL")
        no_holding = self.evaluate(adapter)
        self.assertEqual("SELL_HOLDING_QUANTITY_INVALID", no_holding["reason"])
        self.seed_holding(repository, service, qty=3)
        sold = self.evaluate(adapter, cycle="C2")
        self.assertEqual(RESULT_PROGRESSED, sold["status"])
        self.assertEqual(("SELL", 3), (sold["orders"][0]["side"], sold["orders"][0]["filled_qty"]))
        self.assertEqual(0, next(item for item in repository.read_session(SESSION_ID)["positions"] if item["routine_instance_id"] == "A")["holding_qty"])
        self.assertEqual("SELL", selected["value"])

    def test_market_single_modes_use_virtual_orderbook_without_production_price_fallback(self):
        buy_rules = _buy_rules(qty=2)
        buy_rules["buy"]["execution"]["base"]["order_price_basis"] = "MARKET"
        repository, _, _, adapter, _ = self.build({"A": buy_rules})
        bought = self.evaluate(adapter, market=_market(asks=((100, 2),)))
        self.assertEqual(("MARKET", None, 2), (bought["orders"][0]["order_type"], bought["orders"][0]["requested_price"], bought["orders"][0]["filled_qty"]))

        sell_rules = _sell_rules()
        sell_rules["sell"]["method"]["setting_a"]["perform1_single_combo"] = "시장가"
        repository2, service2, _, adapter2, _ = self.build({"A": sell_rules}, signal="SELL")
        self.seed_holding(repository2, service2, qty=2)
        sold = self.evaluate(adapter2, market=_market(bids=((100, 2),)))
        self.assertEqual(("MARKET", None, 2), (sold["orders"][0]["order_type"], sold["orders"][0]["requested_price"], sold["orders"][0]["filled_qty"]))

    def test_sell_partial_fill_uses_only_mock_holding_and_preserves_active_cycle(self):
        repository, service, _, adapter, _ = self.build({"A": _sell_rules()}, signal="SELL")
        self.seed_holding(repository, service, qty=3)
        result = self.evaluate(adapter, market=_market(bids=((100, 1),)))
        document = repository.read_session(SESSION_ID)
        position = next(item for item in document["positions"] if item["routine_instance_id"] == "A")
        self.assertEqual(("PARTIAL_FILL", 1, 2), (result["orders"][0]["state"], result["orders"][0]["filled_qty"], result["orders"][0]["remaining_qty"]))
        self.assertEqual((2, 0), (position["holding_qty"], position["available_qty"]))
        self.assertTrue(document["cycle_state_by_instance"]["A"]["active"])

    def test_buy_multi_hoga_balanced_remainder_tick_prices_and_identity(self):
        repository, _, _, adapter, _ = self.build({"A": _buy_rules(mode="MULTI_HOGA", qty=10, budget=1_000)})
        result = self.evaluate(adapter, market=_market(asks=((99, 100),), bids=((98, 100),)))
        self.assertEqual(RESULT_PROGRESSED, result["status"])
        self.assertEqual([4, 3, 3], [item["requested_qty"] for item in result["orders"]])
        self.assertEqual([100, 101, 99], [item["requested_price"] for item in result["orders"]])
        self.assertEqual(10, sum(item["requested_qty"] for item in result["orders"]))
        self.assertEqual(3, len({item["child_identity"] for item in result["orders"]}))
        plan = repository.read_session(SESSION_ID)["progression_by_instance"]["A"]["indicator_follow_mock_adapter"]["plans"][0]
        self.assertEqual({0}, {child["generation"] for child in plan["children"]})
        self.assertEqual({1}, {child["round"] for child in plan["children"]})

    def test_buy_and_sell_multi_hoga_qty_below_children_fail_closed(self):
        repository, _, _, adapter, _ = self.build({"A": _buy_rules(mode="MULTI_HOGA", qty=2)})
        buy = self.evaluate(adapter)
        self.assertEqual("BUY_MULTI_HOGA_QUANTITY_BELOW_CHILD_COUNT", buy["reason"])
        self.assertEqual([], repository.read_session(SESSION_ID)["orders"])

        repository2, service2, _, adapter2, _ = self.build({"A": _sell_rules(mode="MULTI_HOGA")}, signal="SELL")
        self.seed_holding(repository2, service2, qty=2)
        sell = self.evaluate(adapter2)
        self.assertEqual("SELL_MULTI_HOGA_QUANTITY_BELOW_CHILD_COUNT", sell["reason"])
        self.assertEqual([], repository2.read_session(SESSION_ID)["orders"])

    def test_multi_time_emits_one_due_child_per_distinct_cycle_and_survives_adapter_restart(self):
        repository, _, engine, adapter, _ = self.build({"A": _buy_rules(mode="MULTI_TIME", qty=3)})
        first = self.evaluate(adapter, cycle="T1", at=NOW)
        early = self.evaluate(adapter, cycle="T2", at=NOW + timedelta(seconds=5), market=_market(now=NOW + timedelta(seconds=5), sequence=2))
        restarted = MockIndicatorFollowRoutineAdapter(
            repository, engine, now_factory=lambda: NOW + timedelta(seconds=10),
            evaluator=lambda *_args: {"signal": "BUY", "reason": "fixture"},
        )
        second = self.evaluate(restarted, cycle="T3", at=NOW + timedelta(seconds=10), market=_market(now=NOW + timedelta(seconds=10), sequence=3))
        replay = self.evaluate(restarted, cycle="T3", at=NOW + timedelta(seconds=10), market=_market(now=NOW + timedelta(seconds=10), sequence=3))
        self.assertEqual((1, RESULT_WAIT, 1, RESULT_NOOP), (len(first["orders"]), early["status"], len(second["orders"]), replay["status"]))
        self.assertEqual(2, len(repository.read_session(SESSION_ID)["orders"]))

    def test_multi_ratio_rechecks_condition_and_does_not_require_false_edge(self):
        repository, _, _, adapter, _ = self.build({"A": _buy_rules(mode="MULTI_RATIO", qty=3)})
        below = self.evaluate(adapter, cycle="R1", market=_market(price=100))
        first = self.evaluate(adapter, cycle="R2", at=NOW + timedelta(seconds=1), market=_market(price=101, now=NOW, sequence=2))
        second = self.evaluate(adapter, cycle="R3", at=NOW + timedelta(seconds=2), market=_market(price=101, now=NOW, sequence=3))
        self.assertEqual((RESULT_WAIT, "RATIO_THRESHOLD_NOT_MET"), (below["status"], below["reason"]))
        self.assertEqual((1, 1), (len(first["orders"]), len(second["orders"])))
        self.assertEqual(2, len(repository.read_session(SESSION_ID)["orders"]))

    def test_sell_multi_time_and_ratio_use_one_child_per_cycle(self):
        repository, service, _, adapter, _ = self.build({"A": _sell_rules(mode="MULTI_TIME")}, signal="SELL")
        self.seed_holding(repository, service, qty=3)
        first = self.evaluate(adapter, cycle="ST1")
        second = self.evaluate(
            adapter, cycle="ST2", at=NOW + timedelta(seconds=10),
            market=_market(now=NOW + timedelta(seconds=10), sequence=2),
        )
        self.assertEqual((1, 1), (len(first["orders"]), len(second["orders"])))
        self.assertEqual(2, len(repository.read_session(SESSION_ID)["orders"]))

        repository2, service2, _, adapter2, _ = self.build({"A": _sell_rules(mode="MULTI_RATIO")}, signal="SELL")
        self.seed_holding(repository2, service2, qty=3)
        waiting = self.evaluate(adapter2, cycle="SR1", market=_market(price=100))
        eligible = self.evaluate(
            adapter2, cycle="SR2", at=NOW + timedelta(seconds=1),
            market=_market(price=101, now=NOW, sequence=2),
        )
        self.assertEqual("RATIO_THRESHOLD_NOT_MET", waiting["reason"])
        self.assertEqual(1, len(eligible["orders"]))

    def test_current_price_basis_requires_fresh_trade_but_order_price_does_not_fallback(self):
        rules = _buy_rules(qty=1)
        rules["buy"]["execution"]["base"]["order_price_basis"] = "CURRENT_PRICE"
        repository, _, _, adapter, _ = self.build({"A": rules})
        market = _market()
        stale_trade = replace(market.trade, received_at=(NOW - timedelta(seconds=10)).isoformat())
        stale = replace(market, trade=stale_trade, snapshot_identity="MMK-STALE-TRADE")
        result = self.evaluate(adapter, market=stale)
        self.assertEqual("CURRENT_PRICE_VALUE_MISSING", result["reason"])
        self.assertEqual([], repository.read_session(SESSION_ID)["orders"])

    def test_unsupported_compound_modes_are_not_downgraded(self):
        buy_rules = _buy_rules(mode="MULTI_TIME", qty=3)
        buy_rules["buy"]["execution"]["base"].update({"hoga_mode": "MULTI", "hoga_up": 1, "hoga_down": 1})
        repository, _, _, adapter, _ = self.build({"A": buy_rules})
        buy = self.evaluate(adapter)
        self.assertEqual("BUY_MULTI_TIME_HOGA_COMBINATION_NOT_IMPLEMENTED", buy["reason"])
        self.assertEqual([], repository.read_session(SESSION_ID)["orders"])

        sell_rules = _sell_rules(mode="MULTI_TIME")
        sell_rules["sell"]["method"]["setting_a"].update({
            "perform1_title_combo": "다중호가", "perform1_multi_up_line": "1", "perform1_multi_down_line": "1",
        })
        repository2, service2, _, adapter2, _ = self.build({"A": sell_rules}, signal="SELL")
        self.seed_holding(repository2, service2, qty=3)
        sell = self.evaluate(adapter2)
        self.assertEqual("SELL_MULTI_TIME_HOGA_COMBINATION_NOT_IMPLEMENTED", sell["reason"])
        self.assertEqual([], repository2.read_session(SESSION_ID)["orders"])

    def test_active_order_blocks_future_time_progression(self):
        repository, _, _, adapter, _ = self.build({"A": _buy_rules(mode="MULTI_TIME", qty=3)})
        resting_market = _market(asks=((110, 100),), bids=((99, 100),))
        first = self.evaluate(adapter, cycle="O1", market=resting_market)
        second = self.evaluate(adapter, cycle="O2", at=NOW + timedelta(seconds=10), market=_market(now=NOW + timedelta(seconds=10), asks=((110, 100),), sequence=2))
        self.assertEqual(ORDER_OPEN, first["orders"][0]["state"])
        self.assertEqual((RESULT_WAIT, "BLOCKED_ACTIVE_ORDER"), (second["status"], second["reason"]))
        self.assertEqual(1, len(repository.read_session(SESSION_ID)["orders"]))

    def test_sell_multi_hoga_supplement_uses_mock_fills_and_is_idempotent_while_open(self):
        repository, service, engine, adapter, _ = self.build({"A": _sell_rules(mode="MULTI_HOGA")}, signal="SELL")
        self.seed_holding(repository, service, qty=6)
        initial = self.evaluate(adapter, cycle="S1", market=_market(bids=((100, 1),)))
        for order in initial["orders"]:
            if order["state"] in {ORDER_OPEN, ORDER_PARTIAL_FILL}:
                engine.cancel_order(SESSION_ID, order["mock_order_id"], command_id=f"MC-cancel-{order['mock_order_id']}")
        supplement = self.evaluate(adapter, cycle="S2", at=NOW + timedelta(seconds=1), market=_market(now=NOW, bids=((100, 100),), sequence=2))
        self.assertTrue(supplement.get("order"))
        self.assertEqual(4, supplement["order"]["requested_qty"])
        self.assertEqual(0, next(item for item in repository.read_session(SESSION_ID)["positions"] if item["routine_instance_id"] == "A")["holding_qty"])

    def test_multi_instance_market_shared_but_ledgers_and_rules_are_isolated(self):
        repository, _, _, adapter, selected = self.build({"A": _buy_rules(qty=1), "B": _buy_rules(qty=2), "C": _buy_rules(qty=3)})
        market = _market()
        a = self.evaluate(adapter, instance="A", cycle="A1", market=market)
        b = self.evaluate(adapter, instance="B", cycle="B1", market=market)
        selected["value"] = None
        c = self.evaluate(adapter, instance="C", cycle="C1", market=market)
        document = repository.read_session(SESSION_ID)
        positions = {item["routine_instance_id"]: item["holding_qty"] for item in document["positions"]}
        self.assertEqual((1, 2, RESULT_NO_SIGNAL), (a["orders"][0]["requested_qty"], b["orders"][0]["requested_qty"], c["status"]))
        self.assertEqual({"A": 1, "B": 2, "C": 0}, positions)
        plans = [
            document["progression_by_instance"][key].get("indicator_follow_mock_adapter", {}).get("plans", [])
            for key in ("A", "B", "C")
        ]
        self.assertEqual([1, 1, 0], [len(value) for value in plans])
        self.assertEqual({market.snapshot_identity}, {a["plan"]["market_evidence_identity"], b["plan"]["market_evidence_identity"]})

    def test_corrupt_plan_stops_whole_stock_session_with_source_instance(self):
        def broken_builder(**_kwargs):
            return {"status": "READY", "execution_intents": [
                {"side": "BUY", "quantity": 1, "price": 100, "hoga": "LIMIT", "child_sequence_index": 2},
            ]}

        repository, _, engine, _, _ = self.build({"A": _buy_rules(), "B": _buy_rules(), "C": _buy_rules()})
        adapter = MockIndicatorFollowRoutineAdapter(
            repository, engine, now_factory=lambda: NOW,
            evaluator=lambda *_args: {"signal": "BUY", "reason": "fixture"},
            buy_intent_builder=broken_builder,
        )
        result = self.evaluate(adapter, instance="B")
        document = repository.read_session(SESSION_ID)
        self.assertEqual("REVIEW_STOPPED", result["status"])
        self.assertEqual("B", document["review"]["source_routine_instance_id"])
        self.assertEqual({False}, {item["progression_allowed"] for item in document["instance_execution"].values()})

    def test_active_buy_is_explicitly_fail_closed(self):
        repository, _, _, adapter, _ = self.build({"A": _buy_rules(active_buy=True)})
        result = self.evaluate(adapter)
        self.assertEqual("ACTIVE_BUY_NOT_IMPLEMENTED", result["reason"])
        self.assertEqual([], repository.read_session(SESSION_ID)["orders"])

    def test_frozen_rules_are_used_after_original_input_changes(self):
        rules = _buy_rules(qty=1)
        repository, _, _, adapter, _ = self.build({"A": rules})
        rules["mock_validation"]["stock_config"]["buy_qty"] = 99
        result = self.evaluate(adapter)
        self.assertEqual(1, result["orders"][0]["requested_qty"])
        stored = repository.read_session(SESSION_ID)["reference_snapshot"]["routine_instances"][0]
        self.assertEqual(payload_hash(stored["rules_snapshot"]), stored["rules_hash"])

    def test_actual_pure_evaluator_parity_and_sell_priority(self):
        evaluator, _, _ = _pure_routine_functions()
        rules = deepcopy(evaluator.__globals__["DEFAULT_INDICATOR_FOLLOW_CONFIG"])
        rules["buy"]["delay_bar"] = 0
        rules["buy"]["groups"] = [{
            "enabled": True, "name": "buy", "conditions": [
                {"enabled": True, "target": "CLOSE", "operator": ">=", "value": 0}
            ],
        }]
        rules["sell"] = {"delay_bar": 0, "signals": {"macd_sell": {"enabled": False, "groups": []}}}
        execution_rules = _buy_rules(qty=1)
        rules["buy"]["execution"] = execution_rules["buy"]["execution"]
        rules["mock_validation"] = execution_rules["mock_validation"]
        candles = [{"close": value, "volume": 100} for value in (10, 11, 12, 13, 14)]
        expected = evaluator(candles, rules, {"candles": candles})
        repository, _, engine, _, _ = self.build({"A": rules})
        adapter = MockIndicatorFollowRoutineAdapter(repository, engine, now_factory=lambda: NOW)
        result = adapter.evaluate_cycle(
            SESSION_ID, routine_instance_id="A", candles=candles, market=_market(),
            policy=MockExecutionPolicy(1, "LOGIN-1", 2, 2), evaluation_cycle_id="PARITY", evaluated_at=NOW,
        )
        self.assertEqual("BUY", expected.signal)
        self.assertEqual(expected.signal, result["signal"]["signal"])
        event_types = {event["event_type"] for event in repository.read_events(SESSION_ID)}
        self.assertTrue({"ROUTINE_EVALUATED", "ROUTINE_BUY_DECISION", "EXECUTION_PLAN_CREATED", "EXECUTION_CHILD_CREATED"} <= event_types)

        sell_priority_rules = deepcopy(evaluator.__globals__["DEFAULT_INDICATOR_FOLLOW_CONFIG"])
        sell_priority_rules["buy"]["delay_bar"] = 0
        sell_priority_rules["buy"]["groups"] = deepcopy(rules["buy"]["groups"])
        sell_priority_rules["buy"]["execution"] = deepcopy(execution_rules["buy"]["execution"])
        sell_priority_rules["sell"] = {
            "delay_bar": 0,
            "signal_logic": "OR",
            "signals": {
                "macd_sell": {
                    "enabled": True,
                    "groups": [{
                        "enabled": True, "name": "sell", "conditions": [
                            {"enabled": True, "target": "CLOSE", "operator": ">=", "value": 0}
                        ],
                    }],
                }
            },
            "method": deepcopy(_sell_rules()["sell"]["method"]),
        }
        sell_priority_rules["mock_validation"] = deepcopy(execution_rules["mock_validation"])
        expected_sell = evaluator(
            candles,
            sell_priority_rules,
            {"candles": candles, "holding_qty": 1, "average_price": 90, "current_price": 102},
        )
        repository2, service2, engine2, _, _ = self.build({"A": sell_priority_rules})
        self.seed_holding(repository2, service2, qty=1)
        adapter2 = MockIndicatorFollowRoutineAdapter(repository2, engine2, now_factory=lambda: NOW)
        sell_result = adapter2.evaluate_cycle(
            SESSION_ID, routine_instance_id="A", candles=candles, market=_market(),
            policy=MockExecutionPolicy(1, "LOGIN-1", 2, 2), evaluation_cycle_id="SELL-PARITY", evaluated_at=NOW,
        )
        self.assertEqual("SELL", expected_sell.signal)
        self.assertEqual(expected_sell.signal, sell_result["signal"]["signal"])
        self.assertEqual({"SELL"}, {order["side"] for order in sell_result["orders"]})


if __name__ == "__main__":
    unittest.main()
