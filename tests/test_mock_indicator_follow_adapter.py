# -*- coding: utf-8 -*-

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from mock_validation_contract import (
    MockValidationError,
    ORDER_OPEN,
    ORDER_PARTIAL_FILL,
    payload_hash,
)
from mock_validation_indicator_follow_adapter import (
    MockIndicatorFollowRoutineAdapter,
    RESULT_NOOP,
    RESULT_NO_SIGNAL,
    RESULT_PROGRESSED,
    RESULT_WAIT,
    _operation_effective_settings,
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


def _buy_recovery_policy(*, timeout_enabled=False, timeout_scope="EACH", price_responses=None):
    return {
        "scope": "SIGNAL_SCOPED_BUY_RECOVERY",
        "requires_source_signal": True,
        "autonomous_scheduler": False,
        "residual_only": True,
        "preserve_source_signal_id": True,
        "preserve_execution_process_id": True,
        "preserve_buy_round": True,
        "increment_plan_generation_only": True,
        "new_budget_allowed": False,
        "new_round_allowed": False,
        "active_buy_increment_allowed": False,
        "after_cycle_completion": "COMPLETE_CURRENT_BUY_ROUND",
        "order_policy": {
            "hoga_mode": "SINGLE", "order_price_basis": "CURRENT_PRICE",
            "hoga_up": 0, "hoga_down": 0,
        },
        "point_policy": {"mode": "NONE"},
        "unfilled_timeout_policy": {
            "policy": "CANCEL_PENDING_ORDER", "enabled": timeout_enabled,
            "action": "CANCEL", "scope": timeout_scope,
            "configured_value": 0, "configured_unit": "SECOND",
        },
        "buy_price_response_policies": deepcopy(price_responses or []),
        "execution_connected": True,
        "execution_lock_reason": "",
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

    def evaluate(
        self,
        adapter,
        *,
        instance="A",
        cycle="C1",
        at=NOW,
        market=None,
    ):
        return adapter.evaluate_cycle(
            SESSION_ID, routine_instance_id=instance,
            candles=[{"close": 100, "volume": 10} for _ in range(5)],
            market=market or _market(now=at), policy=MockExecutionPolicy(1, "LOGIN-1", 2, 2),
            evaluation_cycle_id=cycle, evaluated_at=at,
        )

    def snapshot_instance_settings(self, repository, instance, settings):
        before = repository.read_session(SESSION_ID)

        def mutation(document):
            lifecycle = document.setdefault(
                "mock_operation_lifecycle",
                {"instance_operations": {}},
            )
            lifecycle.setdefault("instance_operations", {})[instance] = {
                "routine_instance_id": instance,
                "operation_session_id": f"MS-OP-{instance}",
                "state": "RUNNING",
                "operation_policy_snapshot": {
                    "mock_instance_effective_settings": deepcopy(settings),
                },
            }
            document["effective_settings_by_instance"][instance] = deepcopy(settings)
            return document

        repository.mutate_session(
            SESSION_ID,
            mutation,
            expected_revision=before["revision"],
        )

    def commit_initial_buy(
        self,
        repository,
        service,
        *,
        instance="A",
        mode="QUANTITY",
        value=3,
        policy="IMMEDIATE",
    ):
        before = repository.read_session(SESSION_ID)
        return service.set_instance_initial_buy(
            SESSION_ID,
            routine_instance_id=instance,
            mode=mode,
            value=value,
            apply_policy=policy,
            expected_revision=before["revision"],
            expected_operation_session_id=f"MS-OP-{instance}",
            requested_at=NOW.isoformat(timespec="microseconds"),
            command_id=f"MC-budget-{instance}-{policy}-{value}",
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

    def test_operation_snapshot_quantity_overrides_frozen_initial_buy_in_execution(self):
        repository, _, _, adapter, _ = self.build({"A": _buy_rules(qty=1)})
        self.snapshot_instance_settings(
            repository,
            "A",
            {
                "initial_buy": {"mode": "QUANTITY", "value": 3},
                "operation_schedule": {
                    "start_time": "09:00:00",
                    "end_buy_time": "13:30:00",
                },
                "operation_mode": "MANUAL",
            },
        )
        result = self.evaluate(
            adapter,
            market=_market(asks=((100, 10),), bids=((99, 10),)),
        )
        self.assertEqual(RESULT_PROGRESSED, result["status"])
        self.assertEqual((3, 3), (
            result["orders"][0]["requested_qty"],
            result["orders"][0]["filled_qty"],
        ))
        self.assertEqual("LIMIT", result["orders"][0]["order_type"])

    def test_operation_snapshot_amount_is_the_mock_execution_budget(self):
        repository, _, _, adapter, _ = self.build(
            {"A": _buy_rules(qty=1, budget=10_000)}
        )
        self.snapshot_instance_settings(
            repository,
            "A",
            {
                "initial_buy": {"mode": "AMOUNT", "value": 500_000},
                "operation_schedule": {
                    "start_time": "09:00:00",
                    "end_buy_time": "13:30:00",
                },
                "operation_mode": "MANUAL_ATS",
            },
        )
        result = self.evaluate(
            adapter,
            market=_market(asks=((100, 10_000),), bids=((99, 10_000),)),
        )
        self.assertEqual(RESULT_PROGRESSED, result["status"])
        self.assertEqual((5_000, 500_000), (
            result["orders"][0]["requested_qty"],
            result["orders"][0]["filled_qty"] * 100,
        ))
        self.assertEqual("LIMIT", result["orders"][0]["order_type"])

    def test_running_immediate_quantity_change_applies_to_next_mock_buy(self):
        repository, service, _, adapter, _ = self.build(
            {"A": _buy_rules(qty=1)}
        )
        settings = {
            "initial_buy": {"mode": "QUANTITY", "value": 1},
            "operation_schedule": {
                "start_time": "09:00:00",
                "end_buy_time": "13:30:00",
            },
            "operation_mode": "MANUAL",
        }
        self.snapshot_instance_settings(repository, "A", settings)
        self.commit_initial_buy(
            repository,
            service,
            mode="QUANTITY",
            value=3,
            policy="IMMEDIATE",
        )

        result = self.evaluate(
            adapter,
            market=_market(asks=((100, 10),), bids=((99, 10),)),
        )
        document = repository.read_session(SESSION_ID)
        self.assertEqual(3, result["orders"][0]["requested_qty"])
        self.assertEqual(
            "APPLIED",
            document["initial_buy_adjustments_by_instance"]["A"]["state"],
        )
        reloaded = MockValidationRepository(
            repository.root,
            project_root=repository.project_root,
        ).read_session(SESSION_ID)
        self.assertEqual(
            document["initial_buy_adjustments_by_instance"]["A"],
            reloaded["initial_buy_adjustments_by_instance"]["A"],
        )

    def test_running_immediate_amount_change_is_actual_mock_execution_budget(self):
        repository, service, _, adapter, _ = self.build(
            {"A": _buy_rules(qty=1, budget=10_000)}
        )
        settings = {
            "initial_buy": {"mode": "AMOUNT", "value": 100},
            "operation_schedule": {
                "start_time": "09:00:00",
                "end_buy_time": "13:30:00",
            },
            "operation_mode": "MANUAL",
        }
        self.snapshot_instance_settings(repository, "A", settings)
        self.commit_initial_buy(
            repository,
            service,
            mode="AMOUNT",
            value=500,
            policy="IMMEDIATE",
        )

        result = self.evaluate(
            adapter,
            market=_market(asks=((100, 100),), bids=((99, 100),)),
        )
        self.assertEqual(5, result["orders"][0]["requested_qty"])
        self.assertEqual(500, result["orders"][0]["filled_qty"] * 100)

    def test_next_cycle_keeps_previous_value_until_sell_then_first_buy(self):
        rules = _buy_rules(qty=1)
        rules["sell"] = _sell_rules(qty=1)["sell"]
        rules["sell"]["method"]["setting_a"]["perform1_single_combo"] = "시장가"
        repository, service, _, adapter, selected = self.build(
            {"A": rules}
        )
        settings = {
            "initial_buy": {"mode": "QUANTITY", "value": 1},
            "operation_schedule": {
                "start_time": "09:00:00",
                "end_buy_time": "13:30:00",
            },
            "operation_mode": "MANUAL",
        }
        self.snapshot_instance_settings(repository, "A", settings)
        self.commit_initial_buy(
            repository,
            service,
            mode="QUANTITY",
            value=4,
            policy="NEXT_CYCLE",
        )

        first_buy = self.evaluate(
            adapter,
            cycle="NC1",
            market=_market(asks=((100, 20),), bids=((99, 20),), sequence=1),
        )
        self.assertEqual(1, first_buy["orders"][0]["requested_qty"])
        self.assertEqual(
            "WAIT_SELL",
            repository.read_session(SESSION_ID)[
                "initial_buy_adjustments_by_instance"
            ]["A"]["state"],
        )
        reloaded_waiting = MockValidationRepository(
            repository.root,
            project_root=repository.project_root,
        ).read_session(SESSION_ID)
        self.assertEqual(
            "WAIT_SELL",
            reloaded_waiting["initial_buy_adjustments_by_instance"]["A"][
                "state"
            ],
        )
        self.assertEqual(
            {"mode": "QUANTITY", "value": 1},
            _operation_effective_settings(reloaded_waiting, "A")["initial_buy"],
        )

        service.transition_instance_initial_buy_for_signal(
            SESSION_ID,
            routine_instance_id="A",
            signal="SELL",
            signal_id="MS-next-cycle-sell",
            observed_at=(NOW + timedelta(seconds=1)).isoformat(),
        )
        self.assertEqual(
            "WAIT_FIRST_BUY",
            repository.read_session(SESSION_ID)[
                "initial_buy_adjustments_by_instance"
            ]["A"]["state"],
        )
        self.assertEqual(
            {"mode": "QUANTITY", "value": 4},
            _operation_effective_settings(
                repository.read_session(SESSION_ID), "A"
            )["initial_buy"],
        )
        service.transition_instance_initial_buy_for_signal(
            SESSION_ID,
            routine_instance_id="A",
            signal="BUY",
            signal_id="MS-next-cycle-buy",
            observed_at=(NOW + timedelta(seconds=2)).isoformat(),
        )
        self.assertEqual(
            "APPLIED",
            repository.read_session(SESSION_ID)[
                "initial_buy_adjustments_by_instance"
            ]["A"]["state"],
        )

    def test_scheduled_buy_gate_blocks_only_new_buy_and_preserves_sell(self):
        rules = _buy_rules(qty=2)
        rules["sell"] = _sell_rules(qty=2)["sell"]
        repository, service, _, adapter, selected = self.build(
            {"A": rules},
            signal="BUY",
        )
        blocked = adapter.evaluate_cycle(
            SESSION_ID,
            routine_instance_id="A",
            candles=[{"close": 100, "volume": 10} for _ in range(5)],
            market=_market(asks=((100, 10),), bids=((99, 10),)),
            policy=MockExecutionPolicy(1, "LOGIN-1", 2, 2),
            evaluation_cycle_id="C-END-BUY",
            evaluated_at=NOW,
            new_buy_allowed=False,
        )
        self.assertEqual(
            (RESULT_WAIT, "MOCK_SCHEDULED_END_BUY_REACHED"),
            (blocked["status"], blocked["reason"]),
        )
        self.assertEqual([], repository.read_session(SESSION_ID)["orders"])

        self.seed_holding(repository, service, qty=2)
        selected["value"] = "SELL"
        sold = adapter.evaluate_cycle(
            SESSION_ID,
            routine_instance_id="A",
            candles=[{"close": 100, "volume": 10} for _ in range(5)],
            market=_market(asks=((100, 10),), bids=((100, 10),), sequence=2),
            policy=MockExecutionPolicy(1, "LOGIN-1", 2, 2),
            evaluation_cycle_id="C-END-SELL",
            evaluated_at=NOW,
            new_buy_allowed=False,
        )
        self.assertEqual(RESULT_PROGRESSED, sold["status"])
        self.assertEqual("SELL", sold["orders"][0]["side"])

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
        replay = self.evaluate(adapter, at=NOW.replace(microsecond=999999))
        self.assertEqual(RESULT_NOOP, replay["status"])
        self.assertEqual(
            1,
            len([
                event
                for event in repository.read_events(SESSION_ID)
                if event["event_type"] == "ROUTINE_EVALUATED"
            ]),
        )
        self.assertEqual("RUNNING", repository.read_session(SESSION_ID)["instance_execution"]["A"]["state"])
        self.seed_holding(repository, service, qty=3)
        sold = self.evaluate(adapter, cycle="C2")
        self.assertEqual(RESULT_PROGRESSED, sold["status"])
        self.assertEqual(("SELL", 3), (sold["orders"][0]["side"], sold["orders"][0]["filled_qty"]))
        self.assertEqual(0, next(item for item in repository.read_session(SESSION_ID)["positions"] if item["routine_instance_id"] == "A")["holding_qty"])
        self.assertEqual("SELL", selected["value"])

    def test_routine_evaluated_event_binds_operation_and_authoritative_signal_bar(self):
        repository, _, _, adapter, _ = self.build({"A": _sell_rules()}, signal="SELL")
        adapter._evaluator = lambda *_args: {
            "signal": "SELL",
            "reason": "fixture",
            "signal_index": 0,
        }
        result = adapter.evaluate_cycle(
            SESSION_ID,
            routine_instance_id="A",
            candles=[
                {
                    "bar_time": "2026-09-03T10:00:00+09:00",
                    "close": 100,
                    "volume": 10,
                    "timeframe_minutes": 5,
                }
            ],
            market=_market(),
            policy=MockExecutionPolicy(1, "LOGIN-1", 2, 2),
            evaluation_cycle_id="C-SIGNAL-MARKER",
            evaluated_at=NOW,
        )
        self.assertEqual("SELL_HOLDING_QUANTITY_INVALID", result["reason"])
        event = next(
            item
            for item in repository.read_events(SESSION_ID)
            if item["event_type"] == "ROUTINE_EVALUATED"
        )
        self.assertEqual("SELL", event["payload"]["signal"])
        self.assertEqual("2026-09-03T10:00:00+09:00", event["payload"]["signal_bar_time"])
        self.assertEqual(100, event["payload"]["signal_bar_close"])
        self.assertEqual(5, event["payload"]["signal_timeframe_minutes"])
        self.assertTrue(event["payload"]["operation_identity"])

    def test_evaluation_uses_current_operation_rules_snapshot(self):
        repository, _, engine, _, _ = self.build({"A": _buy_rules(qty=1)})
        current_rules = _buy_rules(qty=7)
        before = repository.read_session(SESSION_ID)

        def snapshot(document):
            document.setdefault("mock_operation_lifecycle", {"instance_operations": {}})[
                "instance_operations"
            ]["A"] = {
                "routine_instance_id": "A",
                "operation_session_id": "MS-current-rules",
                "state": "RUNNING",
                "operation_policy_snapshot": {
                    "mock_instance_rules_snapshot": deepcopy(current_rules),
                },
            }
            return document

        repository.mutate_session(SESSION_ID, snapshot, expected_revision=before["revision"])
        observed = {}

        def evaluator(_candles, rules, _context):
            observed["rules"] = deepcopy(rules)
            return {"signal": "", "reason": "fixture"}

        adapter = MockIndicatorFollowRoutineAdapter(
            repository,
            engine,
            now_factory=lambda: NOW,
            evaluator=evaluator,
        )
        result = self.evaluate(adapter)

        self.assertEqual(RESULT_NO_SIGNAL, result["status"])
        self.assertEqual(7, observed["rules"]["mock_validation"]["stock_config"]["buy_qty"])
        event = next(
            item
            for item in repository.read_events(SESSION_ID)
            if item["event_type"] == "ROUTINE_EVALUATED"
        )
        self.assertEqual(payload_hash(current_rules), event["payload"]["rules_hash"])

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

    def test_legacy_ats_execution_method_does_not_override_routine_buy_or_sell(self):
        repository, _, _, adapter, _ = self.build({"A": _buy_rules(qty=2)})
        settings = deepcopy(
            repository.read_session(SESSION_ID)["effective_settings_by_instance"]["A"]
        )
        settings["manual_ats"] = {
            "selected_sessions": ["extra1"],
            "execution_method": "MARKET",
        }
        self.snapshot_instance_settings(
            repository,
            "A",
            settings,
        )
        routine_order = self.evaluate(adapter, market=_market(asks=((100, 2),)))
        self.assertEqual(
            ("LIMIT", 100, 2),
            (
                routine_order["orders"][0]["order_type"],
                routine_order["orders"][0]["requested_price"],
                routine_order["orders"][0]["filled_qty"],
            ),
        )
        self.assertEqual(1, len(repository.read_session(SESSION_ID)["orders"]))

        repository2, service2, _, adapter2, _ = self.build(
            {"A": _sell_rules()}, signal="SELL"
        )
        settings2 = deepcopy(
            repository2.read_session(SESSION_ID)["effective_settings_by_instance"]["A"]
        )
        settings2["manual_ats"] = {
            "selected_sessions": ["extra1"],
            "execution_method": "MARKET",
        }
        self.snapshot_instance_settings(repository2, "A", settings2)
        self.seed_holding(repository2, service2, qty=2)
        routine_sell = self.evaluate(adapter2, market=_market(bids=((100, 2),)))
        self.assertEqual(
            ("LIMIT", 100, 2),
            (
                routine_sell["orders"][0]["order_type"],
                routine_sell["orders"][0]["requested_price"],
                routine_sell["orders"][0]["filled_qty"],
            ),
        )

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

    def test_running_budget_change_does_not_reprice_existing_multi_time_plan(self):
        repository, service, _, adapter, _ = self.build(
            {"A": _buy_rules(mode="MULTI_TIME", qty=3, budget=600)}
        )
        self.snapshot_instance_settings(
            repository,
            "A",
            {
                "initial_buy": {"mode": "AMOUNT", "value": 600},
                "operation_schedule": {
                    "start_time": "09:00:00",
                    "end_buy_time": "13:30:00",
                },
                "operation_mode": "MANUAL",
            },
        )
        first_market = _market(price=100, asks=((110, 100),), bids=((99, 100),))
        first = self.evaluate(adapter, cycle="TB1", at=NOW, market=first_market)
        self.assertEqual(RESULT_PROGRESSED, first["status"])
        self.assertEqual(600, first["orders"][0]["execution_budget"])

        self.commit_initial_buy(
            repository,
            service,
            mode="AMOUNT",
            value=150,
            policy="IMMEDIATE",
        )
        second = self.evaluate(
            adapter,
            cycle="TB2",
            at=NOW + timedelta(seconds=10),
            market=_market(
                price=100,
                asks=((110, 100),),
                bids=((99, 100),),
                now=NOW + timedelta(seconds=10),
                sequence=2,
            ),
        )
        document = repository.read_session(SESSION_ID)
        plan = document["progression_by_instance"]["A"][
            "indicator_follow_mock_adapter"
        ]["plans"][0]
        self.assertEqual(RESULT_PROGRESSED, second["status"])
        self.assertEqual(600, plan["execution_budget"])
        self.assertEqual([600, 600], [order["execution_budget"] for order in document["orders"]])
        self.assertEqual(
            {"mode": "AMOUNT", "value": 150},
            document["effective_settings_by_instance"]["A"]["initial_buy"],
        )

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

    def test_due_time_child_progresses_while_prior_child_remains_open(self):
        repository, _, _, adapter, _ = self.build({"A": _buy_rules(mode="MULTI_TIME", qty=3)})
        resting_market = _market(asks=((110, 100),), bids=((99, 100),))
        first = self.evaluate(adapter, cycle="O1", market=resting_market)
        second = self.evaluate(adapter, cycle="O2", at=NOW + timedelta(seconds=10), market=_market(now=NOW + timedelta(seconds=10), asks=((110, 100),), sequence=2))
        self.assertEqual(ORDER_OPEN, first["orders"][0]["state"])
        self.assertEqual(RESULT_PROGRESSED, second["status"])
        self.assertEqual(2, len(repository.read_session(SESSION_ID)["orders"]))

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

    def test_corrupt_plan_isolates_source_instance(self):
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
        self.assertEqual("INSTANCE_ERROR", result["status"])
        self.assertFalse(document["review"]["review_required"])
        self.assertEqual("ERROR", document["instance_execution"]["B"]["state"])
        self.assertFalse(document["instance_execution"]["B"]["progression_allowed"])
        self.assertTrue(document["instance_execution"]["A"]["progression_allowed"])
        self.assertTrue(document["instance_execution"]["C"]["progression_allowed"])

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

    def test_market_unavailable_254_cycles_is_one_persistent_episode(self):
        repository, _, engine, adapter, _ = self.build({"A": _buy_rules()})
        policy = MockExecutionPolicy(1, "LOGIN-1", 2, 2)
        for microsecond in range(254):
            result = adapter.evaluate_cycle(
                SESSION_ID,
                routine_instance_id="A",
                candles=[],
                market=None,
                policy=policy,
                evaluation_cycle_id="SAME-SECOND",
                evaluated_at=NOW.replace(microsecond=microsecond),
            )
            self.assertEqual(RESULT_WAIT, result["status"])
            self.assertEqual("MOCK_MARKET_UNAVAILABLE", result["reason"])

        blocked = [
            event
            for event in repository.read_events(SESSION_ID)
            if event["event_type"] == "EXECUTION_PLAN_BLOCKED"
        ]
        self.assertEqual(1, len(blocked))
        self.assertEqual("MOCK_MARKET_UNAVAILABLE", blocked[0]["reason_code"])
        self.assertEqual(
            "RUNNING",
            repository.read_session(SESSION_ID)["instance_execution"]["A"]["state"],
        )

        # A process restart must reuse the persisted episode and exact payload.
        restarted = MockIndicatorFollowRoutineAdapter(
            repository,
            engine,
            now_factory=lambda: NOW,
            evaluator=lambda *_args: {"signal": "", "reason": "fixture"},
        )
        before = repository.read_session(SESSION_ID)

        def expose_retry(document):
            document["progression_by_instance"]["A"]["indicator_follow_mock_adapter"][
                "normal_block_episode"
            ]["event_recorded"] = False
            return document

        repository.mutate_session(
            SESSION_ID,
            expose_retry,
            expected_revision=before["revision"],
        )
        retried = restarted.evaluate_cycle(
            SESSION_ID,
            routine_instance_id="A",
            candles=[],
            market=None,
            policy=policy,
            evaluation_cycle_id="SAME-SECOND",
            evaluated_at=NOW.replace(microsecond=999999),
        )
        self.assertEqual(RESULT_WAIT, retried["status"])
        self.assertEqual(1, len([
            event for event in repository.read_events(SESSION_ID)
            if event["event_type"] == "EXECUTION_PLAN_BLOCKED"
        ]))

    def test_normal_block_recovery_reentry_and_reason_transition_open_new_episodes(self):
        repository, _, _, adapter, selected = self.build({"A": _buy_rules()})
        policy = MockExecutionPolicy(1, "LOGIN-1", 2, 2)

        adapter.evaluate_cycle(
            SESSION_ID, routine_instance_id="A", candles=[], market=None,
            policy=policy, evaluation_cycle_id="BLOCK-1", evaluated_at=NOW,
        )
        selected["value"] = ""
        adapter.evaluate_cycle(
            SESSION_ID,
            routine_instance_id="A",
            candles=[{"close": 100, "volume": 10} for _ in range(5)],
            market=_market(now=NOW, sequence=10),
            policy=policy,
            evaluation_cycle_id="RECOVERED",
            evaluated_at=NOW + timedelta(microseconds=10),
        )
        adapter.evaluate_cycle(
            SESSION_ID, routine_instance_id="A", candles=[], market=None,
            policy=policy, evaluation_cycle_id="BLOCK-2",
            evaluated_at=NOW + timedelta(seconds=1),
        )
        invalid_market = replace(
            _market(now=NOW + timedelta(seconds=2), sequence=11),
            orderbook=replace(
                _market(now=NOW + timedelta(seconds=2), sequence=11).orderbook,
                connection_epoch=2,
            ),
        )
        for offset in range(3):
            adapter.evaluate_cycle(
                SESSION_ID,
                routine_instance_id="A",
                candles=[],
                market=invalid_market,
                policy=policy,
                evaluation_cycle_id="DIFFERENT-REASON",
                evaluated_at=NOW + timedelta(seconds=2, microseconds=offset),
            )

        blocked = [
            event for event in repository.read_events(SESSION_ID)
            if event["event_type"] == "EXECUTION_PLAN_BLOCKED"
        ]
        self.assertEqual(
            [
                "MOCK_MARKET_UNAVAILABLE",
                "MOCK_MARKET_UNAVAILABLE",
                "MOCK_MARKET_SESSION_INVALID",
            ],
            [event["reason_code"] for event in blocked],
        )
        self.assertEqual(3, len({event["event_id"] for event in blocked}))

    def test_normal_block_episode_isolated_by_instance_and_operation(self):
        repository, _, _, adapter, _ = self.build({"A": _buy_rules(), "B": _buy_rules()})
        policy = MockExecutionPolicy(1, "LOGIN-1", 2, 2)
        for instance_id in ("A", "B"):
            for microsecond in range(3):
                adapter.evaluate_cycle(
                    SESSION_ID,
                    routine_instance_id=instance_id,
                    candles=[],
                    market=None,
                    policy=policy,
                    evaluation_cycle_id="SAME-SECOND",
                    evaluated_at=NOW.replace(microsecond=microsecond),
                )
        before = repository.read_session(SESSION_ID)

        def next_operation(document):
            document["session"]["start_identity"] = "MS-next-operation"
            return document

        repository.mutate_session(
            SESSION_ID,
            next_operation,
            expected_revision=before["revision"],
        )
        adapter.evaluate_cycle(
            SESSION_ID,
            routine_instance_id="A",
            candles=[],
            market=None,
            policy=policy,
            evaluation_cycle_id="NEXT-OPERATION",
            evaluated_at=NOW + timedelta(seconds=1),
        )
        blocked = [
            event for event in repository.read_events(SESSION_ID)
            if event["event_type"] == "EXECUTION_PLAN_BLOCKED"
        ]
        self.assertEqual(3, len(blocked))
        self.assertEqual(2, len({event["routine_instance_id"] for event in blocked}))
        self.assertEqual(2, len({
            event["payload"]["operation_identity"]
            for event in blocked
            if event["routine_instance_id"] == "A"
        }))

    def test_true_event_identity_conflict_and_adapter_integrity_error_remain_fail_closed(self):
        repository, _, engine, _, _ = self.build({"A": _buy_rules()})
        first = {
            "event_id": "ME-00000000000000000000000000000999",
            "validation_session_id": SESSION_ID,
            "stock_code": "005930",
            "routine_instance_id": "A",
            "event_type": "EXECUTION_PLAN_BLOCKED",
            "timestamp": NOW.isoformat(timespec="microseconds"),
            "reason_code": "FIRST",
            "payload": {},
        }
        repository.append_event(first)
        with self.assertRaisesRegex(MockValidationError, "MOCK_EVENT_ID_CONFLICT"):
            repository.append_event({**first, "reason_code": "INCOMPATIBLE"})

        def broken_evaluator(*_args):
            raise RuntimeError("genuine adapter failure")

        adapter = MockIndicatorFollowRoutineAdapter(
            repository,
            engine,
            now_factory=lambda: NOW,
            evaluator=broken_evaluator,
        )
        result = self.evaluate(adapter, cycle="INTEGRITY")
        self.assertEqual("INSTANCE_ERROR", result["status"])
        error_event = next(
            event for event in reversed(repository.read_events(SESSION_ID))
            if event["event_type"] == "INSTANCE_ERROR"
        )
        self.assertEqual("RUNNING", error_event["payload"]["before_state"])
        self.assertEqual("ERROR", error_event["payload"]["after_state"])
        self.assertEqual("INDICATOR_FOLLOW_ADAPTER", error_event["payload"]["source_category"])
        self.assertTrue(error_event["payload"]["operation_identity"].startswith("MS-"))


if __name__ == "__main__":
    unittest.main()
