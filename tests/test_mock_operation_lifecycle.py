# -*- coding: utf-8 -*-

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

from mock_validation_contract import (
    ORDER_CANCEL_PENDING,
    ORDER_CANCELED,
    SESSION_CLOSING,
    SESSION_ENDED,
    SESSION_REVIEW_STOPPED,
    SESSION_RUNNING,
    SESSION_WAITING,
    MockValidationError,
    instance_individual_liquidation_reservation,
    payload_hash,
)
from mock_validation_operation_lifecycle import (
    CLOSE_CARRYOVER,
    CLOSE_CURRENT_PRICE,
    CLOSE_MARKET,
    CLOSE_PROFIT_LOSS,
    CLOSE_ROUTINE,
    MockOperationLifecycleCoordinator,
    OPERATION_CLOSING,
    OPERATION_ENDED,
    OPERATION_RUNNING,
    OPERATION_REVIEW_STOPPED,
    OUTCOME_CARRYOVER_DONE,
    OUTCOME_DONE,
    OUTCOME_NOT_READY,
    OUTCOME_REVIEW_REQUIRED,
    evaluate_mock_operation_completion,
    mock_validation_end_eligibility,
    normalize_close_method,
)
from mock_validation_repository import MockValidationRepository
from mock_validation_session_service import MockValidationSessionService
from mock_validation_ui_projection import mock_instance_projection
from mock_validation_virtual_execution import MockExecutionPolicy, MockVirtualExecutionEngine
from manual_ats_runtime import PROGRAM_SESSION_ID
from tests.test_mock_indicator_follow_adapter import NOW, SESSION_ID, _buy_rules, _market


def _reference(instance_ids=("A", "B", "C")):
    instances = []
    for instance_id in instance_ids:
        rules = _buy_rules(qty=3)
        instances.append({
            "routine_instance_id": instance_id,
            "routine_definition_id": "indicator-follow",
            "routine_type": "INDICATOR_FOLLOW",
            "rules_snapshot": deepcopy(rules),
            "rules_hash": payload_hash(rules),
        })
    snapshot = {
        "stock_code": "005930",
        "stock_name": "삼성전자",
        "stock_identity_reference": "STOCK-005930",
        "snapshot_created_at": NOW.isoformat(),
        "routine_instances": instances,
    }
    snapshot["snapshot_hash"] = payload_hash(snapshot)
    return snapshot


class MockOperationLifecycleTest(unittest.TestCase):
    def test_instance_regular_end_pending_cleanup_is_two_step_and_idempotent(self):
        self.start_instance("B")
        resting = _market(now=self.clock["now"], asks=((110, 100),))
        order = self.engine.submit_order(
            SESSION_ID,
            routine_instance_id="B",
            side="BUY",
            order_type="LIMIT",
            requested_qty=1,
            limit_price=100,
            market=resting,
            policy=self.policy,
            execution_budget=1000,
            command_id="MC-regular-end-pending",
        )["order"]
        requested = self.coordinator.process_instance_regular_end_pending_cleanup(
            SESSION_ID,
            routine_instance_id="B",
            lifecycle_cycle_id="REGULAR-END-1",
            as_of=self.clock["now"],
        )
        duplicate = self.coordinator.process_instance_regular_end_pending_cleanup(
            SESSION_ID,
            routine_instance_id="B",
            lifecycle_cycle_id="REGULAR-END-1",
            as_of=self.clock["now"],
        )
        confirmed = self.coordinator.process_instance_regular_end_pending_cleanup(
            SESSION_ID,
            routine_instance_id="B",
            lifecycle_cycle_id="REGULAR-END-2",
            as_of=self.clock["now"] + timedelta(seconds=1),
        )
        document = self.repository.read_session(SESSION_ID)
        saved = next(
            item for item in document["orders"]
            if item["mock_order_id"] == order["mock_order_id"]
        )
        self.assertEqual("REGULAR_END_CANCEL_REQUEST", requested["action"])
        self.assertTrue(duplicate.get("duplicate") or duplicate["status"] == "NOOP")
        self.assertEqual("REGULAR_END_CANCEL_EFFECT", confirmed["action"])
        self.assertEqual(ORDER_CANCELED, saved["state"])
        self.assertEqual(1, len(document["orders"]))

    def test_direct_liquidation_stops_reissuing_after_cleanup_boundary(self):
        self.start_instance("B")
        self.position("B", 3)
        self.coordinator.request_instance_liquidation_boundary(
            SESSION_ID,
            routine_instance_id="B",
            method=CLOSE_CURRENT_PRICE,
            reason="fixture liquidation",
            as_of=self.clock["now"],
            command_id="MC-liquidation-boundary-B",
        )
        resting = _market(
            now=self.clock["now"],
            bids=((90, 100),),
            sequence=2,
        )
        created = self.coordinator.process_instance_operation_cycle(
            SESSION_ID,
            routine_instance_id="B",
            lifecycle_cycle_id="LIQUIDATION-CREATE",
            as_of=self.clock["now"],
            market=resting,
            policy=self.policy,
        )
        self.assertEqual("LIQUIDATION_STARTED", created["action"])
        cancel_requested = self.coordinator.process_instance_operation_cycle(
            SESSION_ID,
            routine_instance_id="B",
            lifecycle_cycle_id="LIQUIDATION-CLEANUP-1",
            as_of=self.clock["now"] + timedelta(seconds=1),
            market=resting,
            policy=self.policy,
            pending_order_cleanup_boundary=True,
        )
        cancel_confirmed = self.coordinator.process_instance_operation_cycle(
            SESSION_ID,
            routine_instance_id="B",
            lifecycle_cycle_id="LIQUIDATION-CLEANUP-2",
            as_of=self.clock["now"] + timedelta(seconds=2),
            market=resting,
            policy=self.policy,
            pending_order_cleanup_boundary=True,
        )
        waiting = self.coordinator.process_instance_operation_cycle(
            SESSION_ID,
            routine_instance_id="B",
            lifecycle_cycle_id="LIQUIDATION-CLEANUP-3",
            as_of=self.clock["now"] + timedelta(seconds=3),
            market=resting,
            policy=self.policy,
            pending_order_cleanup_boundary=True,
        )
        document = self.repository.read_session(SESSION_ID)
        self.assertEqual("REGULAR_END_CANCEL_REQUEST", cancel_requested["action"])
        self.assertEqual("CANCEL_EFFECT", cancel_confirmed["action"])
        self.assertEqual("REGULAR_END_PENDING_CLEANUP_COMPLETE", waiting["action"])
        self.assertEqual(1, len(document["orders"]))
        self.assertEqual(3, document["positions"][1]["holding_qty"])

    def test_early_close_carryover_defers_cancel_until_cleanup_boundary(self):
        self.start_instance("B")
        self.position("B", 2)
        resting = _market(now=self.clock["now"], asks=((110, 100),))
        order = self.engine.submit_order(
            SESSION_ID,
            routine_instance_id="B",
            side="BUY",
            order_type="LIMIT",
            requested_qty=1,
            limit_price=100,
            market=resting,
            policy=self.policy,
            execution_budget=1000,
            command_id="MC-early-carry-pending",
        )["order"]
        self.coordinator.request_instance_early_close(
            SESSION_ID,
            routine_instance_id="B",
            method=CLOSE_CARRYOVER,
            reason="operator carry",
            as_of=self.clock["now"],
            command_id="MC-early-carry",
        )
        held = self.coordinator.process_instance_operation_cycle(
            SESSION_ID,
            routine_instance_id="B",
            lifecycle_cycle_id="EARLY-CARRY-HOLD",
            as_of=self.clock["now"],
            market=resting,
            policy=self.policy,
        )
        before_cleanup = self.repository.read_session(SESSION_ID)
        saved_before_cleanup = next(
            item for item in before_cleanup["orders"]
            if item["mock_order_id"] == order["mock_order_id"]
        )
        cleanup = self.coordinator.process_instance_regular_end_pending_cleanup(
            SESSION_ID,
            routine_instance_id="B",
            lifecycle_cycle_id="EARLY-CARRY-CLEANUP",
            as_of=self.clock["now"] + timedelta(seconds=1),
        )
        document = self.repository.read_session(SESSION_ID)
        saved = next(
            item for item in document["orders"]
            if item["mock_order_id"] == order["mock_order_id"]
        )
        self.assertEqual("WAIT", held["status"])
        self.assertEqual("MOCK_CARRYOVER_HOLD_UNTIL_CLEANUP", held["reason"])
        self.assertNotEqual(ORDER_CANCEL_PENDING, saved_before_cleanup["state"])
        self.assertEqual("REGULAR_END_CANCEL_REQUEST", cleanup["action"])
        self.assertEqual(ORDER_CANCEL_PENDING, saved["state"])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.repository = MockValidationRepository(
            root / "mock_validation", project_root=root / "project"
        )
        self.clock = {"now": NOW}
        self.service = MockValidationSessionService(
            self.repository,
            now_factory=lambda: self.clock["now"].isoformat(timespec="microseconds"),
        )
        self.service.create_stock_session(
            reference_snapshot=_reference(),
            validation_session_id=SESSION_ID,
            command_id="MC-create",
        )
        self.engine = MockVirtualExecutionEngine(
            self.repository, now_factory=lambda: self.clock["now"]
        )
        self.coordinator = MockOperationLifecycleCoordinator(
            self.repository, self.engine, now_factory=lambda: self.clock["now"]
        )
        self.policy = MockExecutionPolicy(1, "LOGIN-1", 2, 2)

    def start(self, *, day="2026-09-03", command="MC-op-start", long_hold=False):
        return self.coordinator.start_stock_operation(
            SESSION_ID,
            trading_date=day,
            as_of=self.clock["now"],
            operation_policy_snapshot={"long_hold_enabled": long_hold},
            command_id=command,
        )

    def position(self, instance_id, qty, average=90):
        self.service.set_instance_position(
            SESSION_ID,
            instance_id,
            holding_qty=qty,
            available_qty=qty,
            average_price=average if qty else 0,
            realized_cost_basis=qty * average,
            command_id=f"MC-position-{instance_id}-{qty}-{self.clock['now'].timestamp()}",
        )
        before = self.repository.read_session(SESSION_ID)

        def mutation(document):
            document["cycle_state_by_instance"][instance_id] = {
                "status": "resolved",
                "active": qty > 0,
                "cycle_identity": f"CYCLE-{instance_id}" if qty > 0 else "",
                "confirmed_buy_round": 1 if qty > 0 else 0,
            }
            return document

        self.repository.mutate_session(
            SESSION_ID, mutation, expected_revision=before["revision"]
        )

    def close(self, method=CLOSE_MARKET, *, source="NORMAL", long_hold=None):
        kwargs = {
            "method": method,
            "reason": f"{source.lower()} close",
            "as_of": self.clock["now"],
            "command_id": f"MC-{source.lower()}-close",
            "long_hold_enabled": long_hold,
        }
        return getattr(self.coordinator, f"request_{source.lower()}_close")(
            SESSION_ID, **kwargs
        )

    def cycle(self, identity, *, market=None, final=False, advance_ms=100):
        self.clock["now"] += timedelta(milliseconds=advance_ms)
        return self.coordinator.process_mock_operation_cycle(
            SESSION_ID,
            lifecycle_cycle_id=identity,
            as_of=self.clock["now"],
            market=market if market is not None else _market(now=self.clock["now"]),
            policy=self.policy,
            final_close_boundary=final,
        )

    @staticmethod
    def instance_snapshot(document, instance_id):
        return payload_hash(
            {
                "execution": document["instance_execution"][instance_id],
                "operation": (
                    document.get("mock_operation_lifecycle", {})
                    .get("instance_operations", {})
                    .get(instance_id)
                ),
                "cycle": document["cycle_state_by_instance"][instance_id],
                "progression": document["progression_by_instance"][instance_id],
                "position": next(
                    item
                    for item in document["positions"]
                    if item["routine_instance_id"] == instance_id
                ),
                "pnl": next(
                    item
                    for item in document["pnl"]
                    if item["routine_instance_id"] == instance_id
                ),
                "orders": [
                    item
                    for item in document["orders"]
                    if item["routine_instance_id"] == instance_id
                ],
                "fills": [
                    item
                    for item in document["fills"]
                    if item["routine_instance_id"] == instance_id
                ],
            }
        )

    def start_instance(
        self,
        instance_id="B",
        command="MC-instance-start",
        *,
        long_hold=True,
    ):
        return self.coordinator.start_instance_operation(
            SESSION_ID,
            routine_instance_id=instance_id,
            trading_date=self.clock["now"].date(),
            as_of=self.clock["now"],
            operation_policy_snapshot={
                "fixture": True,
                "long_hold_enabled": long_hold,
                "operation_mode": "SCHEDULED",
                "regular_market": {"end_time": "15:20:00"},
                "liquidation": {
                    "minutes_before_regular_close": "5",
                    "method": "MARKET",
                },
                "mock_instance_effective_settings": {
                    "operation_mode": "SCHEDULED",
                    "end_buy_time": "13:30:00",
                },
            },
            command_id=command,
        )

    def prepare_hard_early_auto_transition(self, instance_id="A"):
        self.start_instance(instance_id)
        self.position(instance_id, 3)
        requested = self.coordinator.request_instance_early_close(
            SESSION_ID,
            routine_instance_id=instance_id,
            method=CLOSE_MARKET,
            reason="operator",
            as_of=self.clock["now"],
            command_id=f"MC-hard-early-{instance_id}",
        )
        operation = requested["document"]["mock_operation_lifecycle"][
            "instance_operations"
        ][instance_id]
        order = self.engine.submit_order(
            SESSION_ID,
            routine_instance_id=instance_id,
            side="SELL",
            order_type="LIMIT",
            requested_qty=3,
            limit_price=200,
            market=_market(now=self.clock["now"], bids=((100, 100),)),
            policy=self.policy,
            child_identity=(
                f"MOCK_INSTANCE_CLOSE:{operation['operation_session_id']}:{instance_id}"
            ),
            command_id=f"MC-hard-close-order-{instance_id}",
            allow_closing=True,
        )["order"]
        returned = self.coordinator.return_instance_early_close_to_auto(
            SESSION_ID,
            routine_instance_id=instance_id,
            as_of=self.clock["now"] + timedelta(minutes=1),
            command_id=f"MC-hard-return-auto-{instance_id}",
        )
        cancel_requested = self.coordinator.process_instance_operation_cycle(
            SESSION_ID,
            routine_instance_id=instance_id,
            lifecycle_cycle_id=f"HARD-AUTO-CANCEL-REQUEST-{instance_id}",
            as_of=self.clock["now"],
            market=_market(now=self.clock["now"]),
            policy=self.policy,
        )
        self.assertEqual("TRANSITION_PENDING", returned["status"])
        self.assertEqual("CLOSE_TRANSITION_CANCEL_REQUEST", cancel_requested["action"])
        return order["mock_order_id"]

    def test_production_early_close_market_label_normalizes_to_mock_market(self):
        self.assertEqual(CLOSE_MARKET, normalize_close_method("시장가즉시"))

    def test_instance_start_changes_only_target_execution(self):
        before = self.repository.read_session(SESSION_ID)
        siblings = {
            instance_id: self.instance_snapshot(before, instance_id)
            for instance_id in ("A", "C")
        }
        result = self.start_instance("B")
        after = result["document"]
        self.assertEqual(SESSION_RUNNING, after["session"]["state"])
        self.assertEqual(SESSION_RUNNING, after["instance_execution"]["B"]["state"])
        self.assertTrue(after["instance_execution"]["B"]["progression_allowed"])
        self.assertEqual(SESSION_WAITING, after["instance_execution"]["A"]["state"])
        self.assertEqual(SESSION_WAITING, after["instance_execution"]["C"]["state"])
        self.assertEqual(
            siblings,
            {
                instance_id: self.instance_snapshot(after, instance_id)
                for instance_id in ("A", "C")
            },
        )

    def test_instance_routine_early_close_can_return_to_frozen_auto_timeline(self):
        self.start_instance("A")
        self.position("A", 3)
        self.coordinator.request_instance_early_close(
            SESSION_ID,
            routine_instance_id="A",
            method=CLOSE_ROUTINE,
            reason="operator",
            as_of=self.clock["now"],
            command_id="MC-early-routine",
        )
        result = self.coordinator.return_instance_early_close_to_auto(
            SESSION_ID,
            routine_instance_id="A",
            as_of=self.clock["now"] + timedelta(minutes=1),
            command_id="MC-return-auto",
        )
        operation = result["document"]["mock_operation_lifecycle"]["instance_operations"]["A"]
        self.assertEqual("CANCELLED", result["status"])
        self.assertEqual(OPERATION_RUNNING, operation["state"])
        self.assertEqual("", operation["close_source"])
        self.assertEqual("", operation["close_method"])

    def test_instance_early_close_cannot_return_to_auto_after_frozen_trigger(self):
        self.start_instance("A")
        self.position("A", 3)
        self.coordinator.request_instance_early_close(
            SESSION_ID,
            routine_instance_id="A",
            method=CLOSE_ROUTINE,
            reason="operator",
            as_of=self.clock["now"],
            command_id="MC-early-routine",
        )
        late = self.clock["now"].replace(hour=13, minute=30, second=0)
        with self.assertRaisesRegex(MockValidationError, "MOCK_EARLY_AUTO_RETURN_BLOCKED"):
            self.coordinator.return_instance_early_close_to_auto(
                SESSION_ID,
                routine_instance_id="A",
                as_of=late,
                command_id="MC-return-auto-late",
            )
        blocked = next(
            item
            for item in reversed(self.repository.read_events(SESSION_ID))
            if item.get("reason_code") == "MOCK_EARLY_AUTO_RETURN_BLOCKED"
        )
        self.assertEqual("EARLY_CLOSE_BLOCKED", blocked["event_type"])
        self.assertEqual("BLOCKED", blocked["payload"]["result"])
        self.assertEqual(
            "RETURN_TO_AUTO_TIMELINE", blocked["payload"]["requested_action"]
        )

    def test_instance_early_close_cancel_invalid_state_records_block_event(self):
        self.start_instance("A")
        with self.assertRaisesRegex(
            MockValidationError, "MOCK_INSTANCE_EARLY_CLOSE_CANCEL_BLOCKED"
        ):
            self.coordinator.cancel_instance_early_close(
                SESSION_ID,
                routine_instance_id="A",
                as_of=self.clock["now"],
                command_id="MC-cancel-close-invalid-A",
            )
        blocked = next(
            item
            for item in reversed(self.repository.read_events(SESSION_ID))
            if item.get("reason_code")
            == "MOCK_INSTANCE_EARLY_CLOSE_CANCEL_BLOCKED"
        )
        self.assertEqual("EARLY_CLOSE_BLOCKED", blocked["event_type"])
        self.assertEqual("CANCEL_EARLY_CLOSE", blocked["payload"]["requested_action"])
        self.assertEqual("BLOCKED", blocked["payload"]["result"])

    def test_instance_hard_early_auto_return_after_cancel_with_zero_fill(self):
        self.prepare_hard_early_auto_transition("A")
        self.coordinator.process_instance_operation_cycle(
            SESSION_ID,
            routine_instance_id="A",
            lifecycle_cycle_id="HARD-AUTO-CANCEL-EFFECT-A",
            as_of=self.clock["now"],
            market=_market(now=self.clock["now"]),
            policy=self.policy,
        )
        restored = self.coordinator.process_instance_operation_cycle(
            SESSION_ID,
            routine_instance_id="A",
            lifecycle_cycle_id="HARD-AUTO-RESTORE-A",
            as_of=self.clock["now"],
            market=_market(now=self.clock["now"]),
            policy=self.policy,
        )
        operation = restored["document"]["mock_operation_lifecycle"][
            "instance_operations"
        ]["A"]
        self.assertEqual("PROGRESSED", restored["status"])
        self.assertEqual(OPERATION_RUNNING, operation["state"])
        self.assertEqual("", operation["close_source"])
        self.assertEqual("", operation["close_method"])

    def test_instance_hard_early_auto_return_blocks_late_partial_sell_fill(self):
        order_id = self.prepare_hard_early_auto_transition("A")
        fill = self.engine.process_orderbook(
            SESSION_ID,
            order_id,
            market=_market(
                now=self.clock["now"],
                bids=((200, 1),),
                sequence=2,
            ),
            policy=self.policy,
            command_id="MC-late-partial-fill-A",
            allow_closing=True,
        )["order"]
        self.assertEqual(1, fill["filled_qty"])
        self.coordinator.process_instance_operation_cycle(
            SESSION_ID,
            routine_instance_id="A",
            lifecycle_cycle_id="HARD-AUTO-RECANCEL-A",
            as_of=self.clock["now"],
            market=_market(now=self.clock["now"]),
            policy=self.policy,
        )
        self.coordinator.process_instance_operation_cycle(
            SESSION_ID,
            routine_instance_id="A",
            lifecycle_cycle_id="HARD-AUTO-CANCEL-EFFECT-A",
            as_of=self.clock["now"],
            market=_market(now=self.clock["now"]),
            policy=self.policy,
        )
        blocked = self.coordinator.process_instance_operation_cycle(
            SESSION_ID,
            routine_instance_id="A",
            lifecycle_cycle_id="HARD-AUTO-FINAL-RECHECK-A",
            as_of=self.clock["now"],
            market=_market(now=self.clock["now"]),
            policy=self.policy,
        )
        operation = blocked["document"]["mock_operation_lifecycle"][
            "instance_operations"
        ]["A"]
        self.assertEqual("BLOCKED", blocked["status"])
        self.assertEqual("MOCK_EARLY_AUTO_RETURN_FILLED_BLOCKED", blocked["reason"])
        self.assertEqual(OPERATION_CLOSING, operation["state"])
        self.assertIsNone(operation["close_transition_pending"])
        event = next(
            item
            for item in reversed(self.repository.read_events(SESSION_ID))
            if item.get("reason_code")
            == "MOCK_EARLY_AUTO_RETURN_FILLED_BLOCKED"
        )
        self.assertEqual("EARLY_CLOSE_BLOCKED", event["event_type"])
        self.assertEqual(1, event["payload"]["filled_qty"])
        self.assertEqual("BLOCKED", event["payload"]["result"])

    def test_instance_hard_early_auto_return_blocks_late_full_sell_fill(self):
        order_id = self.prepare_hard_early_auto_transition("A")
        fill = self.engine.process_orderbook(
            SESSION_ID,
            order_id,
            market=_market(
                now=self.clock["now"],
                bids=((200, 3),),
                sequence=2,
            ),
            policy=self.policy,
            command_id="MC-late-full-fill-A",
            allow_closing=True,
        )["order"]
        self.assertEqual(3, fill["filled_qty"])
        blocked = self.coordinator.process_instance_operation_cycle(
            SESSION_ID,
            routine_instance_id="A",
            lifecycle_cycle_id="HARD-AUTO-FINAL-RECHECK-A",
            as_of=self.clock["now"],
            market=_market(now=self.clock["now"]),
            policy=self.policy,
        )
        operation = blocked["document"]["mock_operation_lifecycle"][
            "instance_operations"
        ]["A"]
        self.assertEqual("BLOCKED", blocked["status"])
        self.assertEqual("MOCK_EARLY_AUTO_RETURN_FILLED_BLOCKED", blocked["reason"])
        self.assertEqual(OPERATION_CLOSING, operation["state"])
        self.assertIsNone(operation["close_transition_pending"])

    def test_instance_auto_method_change_preserves_auto_cause(self):
        self.start_instance("A")
        self.position("A", 2)
        self.coordinator.request_instance_auto_close(
            SESSION_ID,
            routine_instance_id="A",
            method=CLOSE_ROUTINE,
            reason="time policy",
            as_of=self.clock["now"],
            command_id="MC-auto-routine",
        )
        changed = self.coordinator.request_instance_early_close(
            SESSION_ID,
            routine_instance_id="A",
            method=CLOSE_MARKET,
            reason="operator method change",
            as_of=self.clock["now"] + timedelta(seconds=1),
            command_id="MC-auto-to-market",
        )
        operation = changed["document"]["mock_operation_lifecycle"]["instance_operations"]["A"]
        self.assertEqual("AUTO", operation["close_source"])
        self.assertEqual(CLOSE_MARKET, operation["close_method"])

    def test_instance_hard_close_return_to_routine_records_block_event(self):
        self.start_instance("A")
        self.position("A", 2)
        self.coordinator.request_instance_early_close(
            SESSION_ID,
            routine_instance_id="A",
            method=CLOSE_MARKET,
            reason="operator",
            as_of=self.clock["now"],
            command_id="MC-early-market-A",
        )
        with self.assertRaisesRegex(
            MockValidationError, "RETURN_TO_ROUTINE_CLOSE_NOT_ALLOWED"
        ):
            self.coordinator.request_instance_early_close(
                SESSION_ID,
                routine_instance_id="A",
                method=CLOSE_ROUTINE,
                reason="operator method change",
                as_of=self.clock["now"] + timedelta(seconds=1),
                command_id="MC-hard-to-routine-A",
            )
        blocked = next(
            item
            for item in reversed(self.repository.read_events(SESSION_ID))
            if item.get("reason_code") == "RETURN_TO_ROUTINE_CLOSE_NOT_ALLOWED"
        )
        self.assertEqual("EARLY_CLOSE_BLOCKED", blocked["event_type"])
        self.assertEqual("MARKET", blocked["payload"]["current_method"])
        self.assertEqual("ROUTINE", blocked["payload"]["requested_method"])
        self.assertEqual("BLOCKED", blocked["payload"]["result"])

    def test_instance_early_close_and_liquidation_preserve_siblings(self):
        self.start_instance("A", command="MC-instance-start-A")
        self.position("A", 2)
        self.start_instance("B", command="MC-instance-start-B")
        self.position("B", 3)
        before = self.repository.read_session(SESSION_ID)
        sibling_hashes = {
            instance_id: self.instance_snapshot(before, instance_id)
            for instance_id in ("A", "C")
        }
        requested = self.coordinator.request_instance_early_close(
            SESSION_ID,
            routine_instance_id="B",
            method=CLOSE_MARKET,
            reason="fixture",
            as_of=self.clock["now"],
            command_id="MC-instance-close-B",
        )
        self.assertEqual(SESSION_CLOSING, requested["document"]["instance_execution"]["B"]["state"])
        self.assertEqual(
            sibling_hashes,
            {
                instance_id: self.instance_snapshot(requested["document"], instance_id)
                for instance_id in ("A", "C")
            },
        )

        for ordinal in range(1, 5):
            self.clock["now"] += timedelta(milliseconds=100)
            result = self.coordinator.process_instance_operation_cycle(
                SESSION_ID,
                routine_instance_id="B",
                lifecycle_cycle_id=f"INSTANCE-B-{ordinal}",
                as_of=self.clock["now"],
                market=_market(now=self.clock["now"], bids=((100, 20),), sequence=ordinal + 1),
                policy=self.policy,
            )
            if result.get("status") == OUTCOME_DONE:
                break
        after = self.repository.read_session(SESSION_ID)
        positions = {
            item["routine_instance_id"]: item["holding_qty"]
            for item in after["positions"]
        }
        self.assertEqual(2, positions["A"])
        self.assertEqual(0, positions["B"])
        self.assertEqual(0, positions["C"])
        self.assertEqual(SESSION_ENDED, after["instance_execution"]["B"]["state"])
        self.assertEqual(
            sibling_hashes,
            {
                instance_id: self.instance_snapshot(after, instance_id)
                for instance_id in ("A", "C")
            },
        )

    def test_instance_close_carryover_requires_mock_snapshot_long_hold(self):
        self.start_instance("A", long_hold=False)
        self.position("A", 2)
        self.coordinator.request_instance_early_close(
            SESSION_ID,
            routine_instance_id="A",
            method=CLOSE_CARRYOVER,
            reason="operator carry",
            as_of=self.clock["now"],
            command_id="MC-instance-carry-off",
        )
        result = self.coordinator.process_instance_operation_cycle(
            SESSION_ID,
            routine_instance_id="A",
            lifecycle_cycle_id="MC-instance-carry-off-cycle",
            as_of=self.clock["now"] + timedelta(seconds=1),
            market=_market(now=self.clock["now"] + timedelta(seconds=1)),
            policy=self.policy,
        )
        operation = result["document"]["mock_operation_lifecycle"]["instance_operations"]["A"]
        self.assertEqual(OUTCOME_REVIEW_REQUIRED, result["status"])
        self.assertEqual("CLOSE_CARRYOVER", operation["termination_provenance"])

    def test_instance_routine_close_keeps_progression_until_final_sell_evidence(self):
        started = self.start_instance("B")
        operation_id = started["operation"]["operation_session_id"]
        self.position("B", 3)
        requested = self.coordinator.request_instance_early_close(
            SESSION_ID,
            routine_instance_id="B",
            method=CLOSE_ROUTINE,
            reason="fixture",
            as_of=self.clock["now"],
            command_id="MC-routine-close-B",
        )
        operation = requested["document"]["mock_operation_lifecycle"]["instance_operations"]["B"]
        self.assertEqual("RUNNING", operation["state"])
        self.assertTrue(operation["close_pending"])
        self.assertTrue(requested["document"]["instance_execution"]["B"]["progression_allowed"])

        recorded = self.coordinator.record_instance_routine_final_sell(
            SESSION_ID,
            routine_instance_id="B",
            as_of=self.clock["now"],
            evaluation_cycle_id="EVAL-SELL-B",
            result={
                "signal": {"signal": "SELL"},
                "plan_id": "PLAN-SELL-B",
                "decision_id": "DECISION-SELL-B",
            },
        )
        evidence = recorded["document"]["mock_operation_lifecycle"]["instance_operations"]["B"]["final_sell_evidence"]
        self.assertEqual("PLAN-SELL-B", evidence["plan_id"])
        self.assertEqual(operation_id, recorded["document"]["instance_execution"]["B"]["operation_session_id"])

        self.position("B", 0)
        completed = self.coordinator.complete_instance_routine_close_if_ready(
            SESSION_ID,
            routine_instance_id="B",
            as_of=self.clock["now"],
            lifecycle_cycle_id="ROUTINE-CLOSE-DONE-B",
        )
        self.assertEqual(OUTCOME_DONE, completed["status"])

    def test_instance_routine_close_with_zero_position_is_blocked_without_fake_sell(self):
        self.start_instance("B")
        before = self.repository.read_session(SESSION_ID)
        with self.assertRaisesRegex(MockValidationError, "NO_HOLDING"):
            self.coordinator.request_instance_early_close(
                SESSION_ID,
                routine_instance_id="B",
                method=CLOSE_ROUTINE,
                reason="fixture",
                as_of=self.clock["now"],
                command_id="MC-zero-routine-close-B",
            )
        after = self.repository.read_session(SESSION_ID)
        events = self.repository.read_events(SESSION_ID)
        self.assertEqual(before, after)
        self.assertEqual("NO_HOLDING", events[-1]["reason_code"])
        self.assertEqual([], [item for item in events if item["event_type"] == "ROUTINE_EVALUATED"])
        self.assertEqual([], after["orders"])
        self.assertEqual([], after["fills"])

    def test_instance_early_close_uses_mock_operation_time_axis_and_event_evidence(self):
        self.start_instance("B", command="MC-start-scheduled-time-B")
        self.position("B", 3)
        scheduled_time = self.clock["now"].replace(hour=14, minute=0)
        with self.assertRaisesRegex(
            MockValidationError, "SCHEDULED_OPERATION_WINDOW_ENDED"
        ):
            self.coordinator.request_instance_early_close(
                SESSION_ID,
                routine_instance_id="B",
                method=CLOSE_ROUTINE,
                reason="operator",
                as_of=scheduled_time,
                command_id="MC-scheduled-time-block-B",
            )
        event = self.repository.read_events(SESSION_ID)[-1]
        self.assertEqual("EARLY_CLOSE_BLOCKED", event["event_type"])
        self.assertEqual("SCHEDULED_OPERATION_WINDOW_ENDED", event["reason_code"])
        self.assertEqual(4, event["payload"]["admission_level"])
        self.assertEqual("SCHEDULED", event["payload"]["operation_mode"])
        self.assertEqual(13 * 3600 + 30 * 60, event["payload"]["operation_end_seconds"])

        for instance_id, command in (("A", "MC-start-continuous-A"), ("C", "MC-start-continuous-C")):
            self.coordinator.start_instance_operation(
                SESSION_ID,
                routine_instance_id=instance_id,
                trading_date=self.clock["now"].date(),
                as_of=self.clock["now"],
                operation_policy_snapshot={
                    "operation_mode": "CONTINUOUS",
                    "regular_market": {
                        "start_time": "09:00:00",
                        "end_time": "15:20:00",
                    },
                    "liquidation": {
                        "minutes_before_regular_close": "5",
                        "method": "MARKET",
                    },
                    "mock_instance_effective_settings": {
                        "operation_mode": "CONTINUOUS",
                        "selected_ats_sessions": ["AFTER_MARKET"],
                    },
                },
                command_id=command,
            )
            self.position(instance_id, 3)

        allowed = self.coordinator.request_instance_early_close(
            SESSION_ID,
            routine_instance_id="A",
            method=CLOSE_ROUTINE,
            reason="operator",
            as_of=self.clock["now"].replace(hour=14, minute=0),
            command_id="MC-continuous-allowed-A",
        )
        self.assertEqual("CLOSING", allowed["status"])

        with self.assertRaisesRegex(MockValidationError, "OUTSIDE_REGULAR_MARKET"):
            self.coordinator.request_instance_early_close(
                SESSION_ID,
                routine_instance_id="C",
                method=CLOSE_ROUTINE,
                reason="operator",
                as_of=self.clock["now"].replace(hour=15, minute=16),
                command_id="MC-continuous-time-block-C",
            )
        event = self.repository.read_events(SESSION_ID)[-1]
        self.assertEqual("EARLY_CLOSE_BLOCKED", event["event_type"])
        self.assertEqual("OUTSIDE_REGULAR_MARKET", event["reason_code"])
        self.assertEqual("CONTINUOUS", event["payload"]["operation_mode"])
        self.assertEqual(15 * 3600 + 15 * 60, event["payload"]["liquidation_start_seconds"])

    def test_instance_routine_close_with_pending_buy_plan_waits_at_zero_position(self):
        self.start_instance("B")
        before = self.repository.read_session(SESSION_ID)

        def add_pending_buy(document):
            document["progression_by_instance"]["B"]["indicator_follow_mock_adapter"] = {
                "version": 1,
                "evaluation_cycles": {},
                "plans": [{"plan_id": "PENDING-BUY-B", "side": "BUY", "state": "ACTIVE"}],
            }
            return document

        self.repository.mutate_session(
            SESSION_ID, add_pending_buy, expected_revision=before["revision"]
        )
        with self.assertRaisesRegex(MockValidationError, "NO_HOLDING"):
            self.coordinator.request_instance_early_close(
                SESSION_ID,
                routine_instance_id="B",
                method=CLOSE_ROUTINE,
                reason="fixture",
                as_of=self.clock["now"],
                command_id="MC-zero-pending-routine-close-B",
            )

    def test_instance_routine_close_with_open_buy_order_waits_at_zero_position(self):
        self.start_instance("B")
        resting = _market(now=self.clock["now"], asks=((110, 100),))
        order = self.engine.submit_order(
            SESSION_ID,
            routine_instance_id="B",
            side="BUY",
            order_type="LIMIT",
            requested_qty=1,
            limit_price=100,
            market=resting,
            policy=self.policy,
            execution_budget=1000,
            command_id="MC-open-buy-B",
        )["order"]
        with self.assertRaisesRegex(MockValidationError, "NO_HOLDING"):
            self.coordinator.request_instance_early_close(
                SESSION_ID,
                routine_instance_id="B",
                method=CLOSE_ROUTINE,
                reason="fixture",
                as_of=self.clock["now"],
                command_id="MC-zero-open-order-routine-close-B",
            )
        self.assertEqual("OPEN", order["state"])

    def test_instance_routine_close_partial_position_waits_and_terminal_is_idempotent(self):
        self.start_instance("B")
        self.position("B", 10)
        self.coordinator.request_instance_early_close(
            SESSION_ID,
            routine_instance_id="B",
            method=CLOSE_ROUTINE,
            reason="fixture",
            as_of=self.clock["now"],
            command_id="MC-partial-routine-close-B",
        )
        self.coordinator.record_instance_routine_final_sell(
            SESSION_ID,
            routine_instance_id="B",
            as_of=self.clock["now"],
            evaluation_cycle_id="EVAL-PARTIAL-SELL-B",
            result={
                "signal": {"signal": "SELL"},
                "plan_id": "PLAN-PARTIAL-SELL-B",
                "decision_id": "DECISION-PARTIAL-SELL-B",
            },
        )
        self.position("B", 4)
        waiting = self.coordinator.complete_instance_routine_close_if_ready(
            SESSION_ID,
            routine_instance_id="B",
            as_of=self.clock["now"],
            lifecycle_cycle_id="ROUTINE-CLOSE-PARTIAL-B",
        )
        self.assertEqual("WAIT", waiting["status"])
        self.assertEqual("RUNNING", waiting["document"]["mock_operation_lifecycle"]["instance_operations"]["B"]["state"])
        self.position("B", 0)
        completed = self.coordinator.complete_instance_routine_close_if_ready(
            SESSION_ID,
            routine_instance_id="B",
            as_of=self.clock["now"],
            lifecycle_cycle_id="ROUTINE-CLOSE-FINAL-B",
        )
        event_count = len(self.repository.read_events(SESSION_ID))
        duplicate = self.coordinator.complete_instance_routine_close_if_ready(
            SESSION_ID,
            routine_instance_id="B",
            as_of=self.clock["now"],
            lifecycle_cycle_id="ROUTINE-CLOSE-FINAL-B",
        )
        self.assertEqual(OUTCOME_DONE, completed["status"])
        self.assertEqual("NOOP", duplicate["status"])
        self.assertEqual(event_count, len(self.repository.read_events(SESSION_ID)))

    def test_instance_auto_and_early_close_request_events_keep_distinct_causes(self):
        self.start_instance("A", command="MC-start-auto-A")
        self.position("A", 1)
        self.coordinator.request_instance_auto_close(
            SESSION_ID,
            routine_instance_id="A",
            method=CLOSE_ROUTINE,
            reason="scheduled final end",
            as_of=self.clock["now"],
            command_id="MC-auto-close-A",
        )
        self.start_instance("B", command="MC-start-early-B")
        self.position("B", 1)
        self.coordinator.request_instance_early_close(
            SESSION_ID,
            routine_instance_id="B",
            method=CLOSE_ROUTINE,
            reason="operator early close",
            as_of=self.clock["now"],
            command_id="MC-early-close-B",
        )
        events = self.repository.read_events(SESSION_ID)
        auto_events = [item for item in events if item["event_type"] == "AUTO_CLOSE_REQUESTED"]
        early_events = [item for item in events if item["event_type"] == "EARLY_CLOSE_REQUESTED"]
        self.assertEqual(["A"], [item["routine_instance_id"] for item in auto_events])
        self.assertEqual(["B"], [item["routine_instance_id"] for item in early_events])
        operations = self.repository.read_session(SESSION_ID)["mock_operation_lifecycle"]["instance_operations"]
        self.assertEqual(("AUTO", CLOSE_ROUTINE), (operations["A"]["close_source"], operations["A"]["close_method"]))
        self.assertEqual(("EARLY", CLOSE_ROUTINE), (operations["B"]["close_source"], operations["B"]["close_method"]))

    def test_instance_early_close_cancel_restores_normal_progression_before_boundary(self):
        self.start_instance("B")
        self.position("B", 3)
        self.coordinator.request_instance_early_close(
            SESSION_ID,
            routine_instance_id="B",
            method=CLOSE_MARKET,
            reason="fixture",
            as_of=self.clock["now"],
            command_id="MC-market-close-B",
        )
        cancelled = self.coordinator.cancel_instance_early_close(
            SESSION_ID,
            routine_instance_id="B",
            as_of=self.clock["now"],
            command_id="MC-cancel-close-B",
        )
        operation = cancelled["document"]["mock_operation_lifecycle"]["instance_operations"]["B"]
        self.assertEqual("RUNNING", operation["state"])
        self.assertEqual("", operation["close_method"])
        self.assertIsNotNone(operation["early_close_cancel_evidence"])
        self.assertTrue(cancelled["document"]["instance_execution"]["B"]["progression_allowed"])

    def test_profit_loss_close_snapshots_thresholds_without_inventing_execution(self):
        self.start_instance("B")
        self.position("B", 3)
        requested = self.coordinator.request_instance_early_close(
            SESSION_ID,
            routine_instance_id="B",
            method=CLOSE_PROFIT_LOSS,
            profit_percent="3.5",
            loss_percent="2",
            reason="fixture",
            as_of=self.clock["now"],
            command_id="MC-profit-loss-B",
        )
        operation = requested["document"]["mock_operation_lifecycle"]["instance_operations"]["B"]
        self.assertEqual("RUNNING", operation["state"])
        self.assertEqual(
            {"profit_percent": 3.5, "loss_percent": 2.0},
            {
                key: operation["close_policy_snapshot"][key]
                for key in ("profit_percent", "loss_percent")
            },
        )

    def test_profit_loss_close_uses_position_return_and_current_price(self):
        self.start_instance("B")
        self.position("B", 3, average=100)
        self.coordinator.request_instance_early_close(
            SESSION_ID,
            routine_instance_id="B",
            method=CLOSE_PROFIT_LOSS,
            profit_percent="3",
            loss_percent="2",
            reason="fixture",
            as_of=self.clock["now"],
            command_id="MC-profit-loss-trigger-B",
        )
        waiting = self.coordinator.activate_instance_profit_loss_if_triggered(
            SESSION_ID,
            routine_instance_id="B",
            as_of=self.clock["now"],
            current_price=102,
        )
        self.assertEqual("WAIT", waiting["status"])
        triggered = self.coordinator.activate_instance_profit_loss_if_triggered(
            SESSION_ID,
            routine_instance_id="B",
            as_of=self.clock["now"],
            current_price=104,
        )
        operation = triggered["document"]["mock_operation_lifecycle"][
            "instance_operations"
        ]["B"]
        self.assertEqual("CLOSING", triggered["status"])
        self.assertEqual(CLOSE_PROFIT_LOSS, operation["close_method"])
        self.assertEqual(CLOSE_CURRENT_PRICE, operation["liquidation_execution_method"])

    def test_individual_carryover_snapshots_time_and_preserves_position(self):
        self.start_instance("B")
        requested = self.coordinator.request_instance_individual_liquidation(
            SESSION_ID,
            routine_instance_id="B",
            method=CLOSE_CARRYOVER,
            minutes_before_regular_close="15",
            reason="fixture",
            as_of=self.clock["now"],
            command_id="MC-individual-carry-B",
        )
        operation = requested["document"]["mock_operation_lifecycle"]["instance_operations"]["B"]
        self.assertEqual(
            "",
            operation["individual_liquidation_time_snapshot"]["minutes_before_regular_close"],
        )
        self.assertTrue(requested["setting_only"])
        self.assertEqual("RUNNING", operation["state"])
        self.assertEqual("", operation["close_method"])
        self.position("B", 3)
        self.coordinator.request_instance_liquidation_boundary(
            SESSION_ID,
            routine_instance_id="B",
            method=CLOSE_CARRYOVER,
            reason="fixture boundary",
            as_of=self.clock["now"],
            command_id="MC-individual-carry-boundary-B",
        )
        held = self.coordinator.process_instance_operation_cycle(
            SESSION_ID,
            routine_instance_id="B",
            lifecycle_cycle_id="INDIVIDUAL-CARRY-B",
            as_of=self.clock["now"],
            market=_market(now=self.clock["now"]),
            policy=self.policy,
        )
        completed = self.coordinator.process_instance_operation_cycle(
            SESSION_ID,
            routine_instance_id="B",
            lifecycle_cycle_id="INDIVIDUAL-CARRY-CLEANUP-B",
            as_of=self.clock["now"] + timedelta(seconds=1),
            market=_market(now=self.clock["now"] + timedelta(seconds=1)),
            policy=self.policy,
            pending_order_cleanup_boundary=True,
        )
        self.assertEqual("WAIT", held["status"])
        self.assertEqual("MOCK_CARRYOVER_HOLD_UNTIL_CLEANUP", held["reason"])
        self.assertEqual(OUTCOME_CARRYOVER_DONE, completed["status"])
        position = next(
            item for item in completed["document"]["positions"]
            if item["routine_instance_id"] == "B"
        )
        self.assertEqual(3, position["holding_qty"])
        operation = completed["document"]["mock_operation_lifecycle"][
            "instance_operations"
        ]["B"]
        self.assertIsNone(operation["individual_liquidation_time_snapshot"])

    def test_manual_individual_setting_projects_only_after_early_close(self):
        settings = self.repository.read_session(SESSION_ID)[
            "effective_settings_by_instance"
        ]["B"]
        settings["operation_mode"] = "CONTINUOUS"
        settings["manual_ats"]["selected_sessions"] = []
        self.coordinator.start_instance_operation(
            SESSION_ID,
            routine_instance_id="B",
            trading_date=self.clock["now"].date(),
            as_of=self.clock["now"],
            operation_policy_snapshot={
                "operation_mode": "CONTINUOUS",
                "regular_market": {"start_time": "09:00:00", "end_time": "15:20:00"},
                "liquidation": {
                    "minutes_before_regular_close": "5",
                    "method": "MARKET",
                },
                "mock_instance_effective_settings": settings,
            },
            command_id="MC-manual-start-B",
        )
        self.position("B", 2)
        requested = self.coordinator.request_instance_individual_liquidation(
            SESSION_ID,
            routine_instance_id="B",
            method=CLOSE_CURRENT_PRICE,
            minutes_before_regular_close="10",
            reason="manual fixture",
            as_of=self.clock["now"],
            command_id="MC-manual-individual-B",
        )
        before = mock_instance_projection(
            requested["document"], "B", as_of=self.clock["now"]
        )
        self.assertEqual(
            "-", before["display_contract"]["liquidation"]["display_text"]
        )
        self.assertFalse(before["liquidation_has_policy"])
        self.assertEqual([], requested["document"]["orders"])

        early = self.coordinator.request_instance_early_close(
            SESSION_ID,
            routine_instance_id="B",
            method=CLOSE_MARKET,
            reason="operator",
            as_of=self.clock["now"],
            command_id="MC-manual-early-B",
        )
        after = mock_instance_projection(
            early["document"], "B", as_of=self.clock["now"]
        )
        early_operation = early["document"]["mock_operation_lifecycle"][
            "instance_operations"
        ]["B"]
        self.assertEqual(CLOSE_CURRENT_PRICE, early_operation["close_method"])
        self.assertEqual(
            "10분/현재가",
            after["display_contract"]["liquidation"]["display_text"],
        )

    def test_manual_early_close_without_override_uses_global_carryover(self):
        settings = self.repository.read_session(SESSION_ID)[
            "effective_settings_by_instance"
        ]["B"]
        settings["operation_mode"] = "CONTINUOUS"
        settings["manual_ats"]["selected_sessions"] = []
        self.coordinator.start_instance_operation(
            SESSION_ID,
            routine_instance_id="B",
            trading_date=self.clock["now"].date(),
            as_of=self.clock["now"],
            operation_policy_snapshot={
                "operation_mode": "CONTINUOUS",
                "regular_market": {"start_time": "09:00:00", "end_time": "15:20:00"},
                "liquidation": {
                    "minutes_before_regular_close": "5",
                    "method": "CARRYOVER",
                },
                "mock_instance_effective_settings": settings,
            },
            command_id="MC-manual-global-start-B",
        )
        self.position("B", 2)
        early = self.coordinator.request_instance_early_close(
            SESSION_ID,
            routine_instance_id="B",
            method=CLOSE_MARKET,
            reason="operator",
            as_of=self.clock["now"],
            command_id="MC-manual-global-early-B",
        )
        operation = early["document"]["mock_operation_lifecycle"][
            "instance_operations"
        ]["B"]
        projected = mock_instance_projection(
            early["document"], "B", as_of=self.clock["now"]
        )
        held = self.coordinator.process_instance_operation_cycle(
            SESSION_ID,
            routine_instance_id="B",
            lifecycle_cycle_id="MANUAL-GLOBAL-CARRYOVER-HOLD",
            as_of=self.clock["now"],
            market=_market(now=self.clock["now"]),
            policy=self.policy,
        )

        self.assertEqual(CLOSE_CARRYOVER, operation["close_method"])
        self.assertEqual(
            "이월", projected["display_contract"]["liquidation"]["display_text"]
        )
        self.assertEqual("WAIT", held["status"])
        self.assertEqual([], held["document"]["orders"])

    def test_pre_operation_individual_liquidation_reservation_binds_on_start(self):
        reserved = self.coordinator.request_instance_individual_liquidation(
            SESSION_ID,
            routine_instance_id="B",
            method=CLOSE_CURRENT_PRICE,
            minutes_before_regular_close="10",
            reason="pre-operation fixture",
            as_of=self.clock["now"].replace(hour=21),
            command_id="MC-pre-operation-individual-B",
        )
        reservation = reserved["document"][
            "individual_liquidation_reservations_by_instance"
        ]["B"]
        self.assertEqual("NEXT_OPERATION", reservation["reservation_scope"])
        self.assertEqual(PROGRAM_SESSION_ID, reservation["program_session_id"])
        self.assertEqual("10", reservation["minutes_before_regular_close"])
        self.assertEqual([], reserved["document"]["orders"])
        self.assertEqual([], reserved["document"]["fills"])

        started = self.start_instance("B", command="MC-start-after-reservation-B")
        operation = started["document"]["mock_operation_lifecycle"][
            "instance_operations"
        ]["B"]
        snapshot = operation["individual_liquidation_time_snapshot"]
        self.assertEqual(operation["operation_session_id"], snapshot["operation_session_id"])
        self.assertEqual(CLOSE_CURRENT_PRICE, snapshot["method"])
        self.assertEqual("10", snapshot["minutes_before_regular_close"])
        self.assertNotIn(
            "B",
            started["document"].get(
                "individual_liquidation_reservations_by_instance", {}
            ),
        )

    def test_pre_operation_carryover_has_no_time_attribute(self):
        reserved = self.coordinator.request_instance_individual_liquidation(
            SESSION_ID,
            routine_instance_id="B",
            method=CLOSE_CARRYOVER,
            minutes_before_regular_close="30",
            reason="pre-operation carry",
            as_of=self.clock["now"].replace(hour=21),
            command_id="MC-pre-operation-carry-B",
        )
        reservation = reserved["document"][
            "individual_liquidation_reservations_by_instance"
        ]["B"]
        self.assertEqual(CLOSE_CARRYOVER, reservation["method"])
        self.assertEqual("", reservation["minutes_before_regular_close"])

    def test_pre_operation_market_reservation_has_time_and_no_execution(self):
        reserved = self.coordinator.request_instance_individual_liquidation(
            SESSION_ID,
            routine_instance_id="B",
            method=CLOSE_MARKET,
            minutes_before_regular_close="5",
            reason="pre-operation market",
            as_of=self.clock["now"].replace(hour=21),
            command_id="MC-pre-operation-market-B",
        )
        reservation = reserved["document"][
            "individual_liquidation_reservations_by_instance"
        ]["B"]
        self.assertEqual(CLOSE_MARKET, reservation["method"])
        self.assertEqual("5", reservation["minutes_before_regular_close"])
        self.assertEqual([], reserved["document"]["orders"])
        self.assertEqual([], reserved["document"]["fills"])

    def test_pre_operation_reservation_is_process_local_and_expires_on_restart(self):
        coordinator_a = MockOperationLifecycleCoordinator(
            self.repository,
            self.engine,
            now_factory=lambda: self.clock["now"],
            program_session_id="process-A",
        )
        reserved = coordinator_a.request_instance_individual_liquidation(
            SESSION_ID,
            routine_instance_id="B",
            method=CLOSE_CURRENT_PRICE,
            minutes_before_regular_close="10",
            reason="pre-operation fixture",
            as_of=self.clock["now"].replace(hour=21),
            command_id="MC-process-local-reservation-B",
        )
        document = reserved["document"]
        self.assertEqual(
            "process-A",
            document["individual_liquidation_reservations_by_instance"]["B"][
                "program_session_id"
            ],
        )
        self.assertIsNotNone(
            instance_individual_liquidation_reservation(
                document,
                "B",
                program_session_id="process-A",
            )
        )
        self.assertIsNone(
            instance_individual_liquidation_reservation(
                document,
                "B",
                program_session_id="process-B",
            )
        )

        service_b = MockValidationSessionService(
            self.repository,
            now_factory=lambda: self.clock["now"].isoformat(timespec="microseconds"),
            program_session_id="process-B",
        )
        expiration = service_b.expire_individual_liquidation_reservations_for_application_boundary(
            source="APPLICATION_RESTART_RECOVERY"
        )
        saved = self.repository.read_session(SESSION_ID)

        self.assertEqual(1, len(expiration["expired"]))
        self.assertEqual((), expiration["errors"])
        self.assertNotIn(
            "B",
            saved.get("individual_liquidation_reservations_by_instance", {}),
        )

    def test_application_shutdown_expires_current_process_pending_reservation(self):
        coordinator = MockOperationLifecycleCoordinator(
            self.repository,
            self.engine,
            now_factory=lambda: self.clock["now"],
            program_session_id="process-A",
        )
        coordinator.request_instance_individual_liquidation(
            SESSION_ID,
            routine_instance_id="B",
            method=CLOSE_CARRYOVER,
            minutes_before_regular_close="",
            reason="pre-operation fixture",
            as_of=self.clock["now"].replace(hour=21),
            command_id="MC-shutdown-reservation-B",
        )
        service = MockValidationSessionService(
            self.repository,
            now_factory=lambda: self.clock["now"].isoformat(timespec="microseconds"),
            program_session_id="process-A",
        )

        expiration = service.expire_individual_liquidation_reservations_for_application_boundary(
            source="APPLICATION_SHUTDOWN"
        )
        saved = self.repository.read_session(SESSION_ID)

        self.assertEqual(1, len(expiration["expired"]))
        self.assertNotIn(
            "B",
            saved.get("individual_liquidation_reservations_by_instance", {}),
        )

    def test_stale_reservation_cannot_bind_without_startup_cleanup(self):
        coordinator_a = MockOperationLifecycleCoordinator(
            self.repository,
            self.engine,
            now_factory=lambda: self.clock["now"],
            program_session_id="process-A",
        )
        coordinator_a.request_instance_individual_liquidation(
            SESSION_ID,
            routine_instance_id="B",
            method=CLOSE_CURRENT_PRICE,
            minutes_before_regular_close="10",
            reason="pre-operation fixture",
            as_of=self.clock["now"].replace(hour=21),
            command_id="MC-stale-binding-reservation-B",
        )
        coordinator_b = MockOperationLifecycleCoordinator(
            self.repository,
            self.engine,
            now_factory=lambda: self.clock["now"],
            program_session_id="process-B",
        )

        started = coordinator_b.start_instance_operation(
            SESSION_ID,
            routine_instance_id="B",
            trading_date=self.clock["now"].date(),
            as_of=self.clock["now"],
            operation_policy_snapshot={
                "operation_mode": "SCHEDULED",
                "regular_market": {"end_time": "15:20:00"},
                "liquidation": {
                    "minutes_before_regular_close": "5",
                    "method": "MARKET",
                },
            },
            command_id="MC-stale-binding-start-B",
        )
        operation = started["document"]["mock_operation_lifecycle"][
            "instance_operations"
        ]["B"]

        self.assertIsNone(operation["individual_liquidation_time_snapshot"])
        self.assertNotIn(
            "B",
            started["document"].get(
                "individual_liquidation_reservations_by_instance", {}
            ),
        )

    def test_restart_expiration_preserves_bound_active_operation_snapshot(self):
        coordinator_a = MockOperationLifecycleCoordinator(
            self.repository,
            self.engine,
            now_factory=lambda: self.clock["now"],
            program_session_id="process-A",
        )
        coordinator_a.request_instance_individual_liquidation(
            SESSION_ID,
            routine_instance_id="B",
            method=CLOSE_CURRENT_PRICE,
            minutes_before_regular_close="10",
            reason="pre-operation fixture",
            as_of=self.clock["now"].replace(hour=21),
            command_id="MC-active-snapshot-reservation-B",
        )
        started = coordinator_a.start_instance_operation(
            SESSION_ID,
            routine_instance_id="B",
            trading_date=self.clock["now"].date(),
            as_of=self.clock["now"],
            operation_policy_snapshot={
                "operation_mode": "SCHEDULED",
                "regular_market": {"end_time": "15:20:00"},
                "liquidation": {
                    "minutes_before_regular_close": "5",
                    "method": "MARKET",
                },
            },
            command_id="MC-active-snapshot-start-B",
        )
        operation_before = started["document"]["mock_operation_lifecycle"][
            "instance_operations"
        ]["B"]
        service_b = MockValidationSessionService(
            self.repository,
            now_factory=lambda: self.clock["now"].isoformat(timespec="microseconds"),
            program_session_id="process-B",
        )

        expiration = service_b.expire_individual_liquidation_reservations_for_application_boundary(
            source="APPLICATION_RESTART_RECOVERY"
        )
        operation_after = self.repository.read_session(SESSION_ID)[
            "mock_operation_lifecycle"
        ]["instance_operations"]["B"]

        self.assertEqual((), expiration["expired"])
        self.assertEqual(
            operation_before["individual_liquidation_time_snapshot"],
            operation_after["individual_liquidation_time_snapshot"],
        )
        self.assertEqual(
            operation_after["operation_session_id"],
            operation_after["individual_liquidation_time_snapshot"][
                "operation_session_id"
            ],
        )

    def test_individual_liquidation_time_lock_records_mock_owned_block_event(self):
        self.start_instance("B")
        self.position("B", 3)
        before = self.repository.read_session(SESSION_ID)
        blocked_at = self.clock["now"].replace(hour=15, minute=16, second=0)

        with self.assertRaisesRegex(
            MockValidationError,
            "LIQUIDATION_TIME_WINDOW_ENTERED",
        ):
            self.coordinator.request_instance_individual_liquidation(
                SESSION_ID,
                routine_instance_id="B",
                method=CLOSE_MARKET,
                minutes_before_regular_close="5",
                reason="context menu setting",
                as_of=blocked_at,
                command_id="MC-individual-time-lock-B",
            )

        after = self.repository.read_session(SESSION_ID)
        events = self.repository.read_events(SESSION_ID)
        blocked = [
            event
            for event in events
            if event["event_type"] == "INDIVIDUAL_LIQUIDATION_BLOCKED"
        ]
        self.assertEqual(before, after)
        self.assertEqual(1, len(blocked))
        self.assertEqual("LIQUIDATION_TIME_WINDOW_ENTERED", blocked[0]["reason_code"])
        self.assertEqual(
            {"minutes_before_regular_close": 5, "method": CLOSE_MARKET},
            blocked[0]["payload"]["requested_policy"],
        )

    def test_instance_reset_is_zero_base_and_sibling_isolated(self):
        self.start_instance("B")
        self.position("B", 4)
        before = self.repository.read_session(SESSION_ID)
        sibling_hashes = {
            instance_id: self.instance_snapshot(before, instance_id)
            for instance_id in ("A", "C")
        }
        with self.assertRaisesRegex(
            MockValidationError, "MOCK_INSTANCE_OPERATION_ACTIVE"
        ):
            self.service.reset_routine_instance(
                SESSION_ID,
                routine_instance_id="B",
                command_id="MC-reset-running-B",
            )
        self.service.stop_instance_validation(
            SESSION_ID,
            routine_instance_id="B",
            command_id="MC-stop-before-reset-B",
        )
        result = self.service.reset_routine_instance(
            SESSION_ID,
            routine_instance_id="B",
            command_id="MC-reset-B",
        )
        after = result["document"]
        position = next(
            item for item in after["positions"] if item["routine_instance_id"] == "B"
        )
        self.assertEqual(0, position["holding_qty"])
        self.assertEqual(0, position["average_price"])
        self.assertEqual(SESSION_WAITING, after["instance_execution"]["B"]["state"])
        self.assertNotIn(
            "B", after["mock_operation_lifecycle"].get("instance_operations", {})
        )
        self.assertEqual(
            sibling_hashes,
            {
                instance_id: self.instance_snapshot(after, instance_id)
                for instance_id in ("A", "C")
            },
        )

    def test_instance_immediate_liquidation_targets_only_selected_position(self):
        self.start_instance("A", command="MC-immediate-start-A")
        self.position("A", 2)
        self.start_instance("B", command="MC-immediate-start-B")
        self.position("B", 3)
        before = self.repository.read_session(SESSION_ID)
        sibling_hashes = {
            instance_id: self.instance_snapshot(before, instance_id)
            for instance_id in ("A", "C")
        }
        self.coordinator.request_instance_immediate_liquidation(
            SESSION_ID,
            routine_instance_id="B",
            method=CLOSE_MARKET,
            reason="fixture",
            as_of=self.clock["now"],
            command_id="MC-immediate-B",
        )
        for ordinal in range(1, 5):
            self.clock["now"] += timedelta(milliseconds=100)
            result = self.coordinator.process_instance_operation_cycle(
                SESSION_ID,
                routine_instance_id="B",
                lifecycle_cycle_id=f"IMMEDIATE-B-{ordinal}",
                as_of=self.clock["now"],
                market=_market(
                    now=self.clock["now"],
                    bids=((100, 20),),
                    sequence=ordinal + 20,
                ),
                policy=self.policy,
            )
            if result.get("status") == OUTCOME_DONE:
                break
        after = self.repository.read_session(SESSION_ID)
        positions = {
            item["routine_instance_id"]: item["holding_qty"]
            for item in after["positions"]
        }
        self.assertEqual({"A": 2, "B": 0, "C": 0}, positions)
        self.assertEqual(
            sibling_hashes,
            {
                instance_id: self.instance_snapshot(after, instance_id)
                for instance_id in ("A", "C")
            },
        )

    def test_parent_unregister_is_blocked_while_instance_operation_active(self):
        self.start_instance("B")
        result = mock_validation_end_eligibility(
            self.repository.read_session(SESSION_ID)
        )
        self.assertFalse(result["eligible"])
        self.assertEqual("MOCK_INSTANCE_OPERATION_ACTIVE", result["reason"])

    def test_stock_start_is_shared_idempotent_and_does_not_start_trading_cycles(self):
        started = self.start()
        duplicate = self.start(command="MC-op-start-duplicate")
        document = self.repository.read_session(SESSION_ID)
        operation_id = document["mock_operation_lifecycle"]["current"]["operation_session_id"]
        self.assertEqual(SESSION_RUNNING, document["session"]["state"])
        self.assertEqual("STARTED", started["status"])
        self.assertTrue(duplicate["duplicate"])
        self.assertTrue(all(item["started_at"] == NOW.isoformat(timespec="microseconds") for item in document["instance_execution"].values()))
        self.assertTrue(all(item["operation_session_id"] == operation_id for item in document["instance_execution"].values()))
        self.assertTrue(operation_id.startswith("MS-"))
        self.assertTrue(all(not value for value in document["cycle_state_by_instance"].values()))
        self.assertTrue(callable(self.coordinator.start_instance_operation))

    def test_instance_error_does_not_block_sibling_operation_start(self):
        self.service.stop_for_instance_error(
            SESSION_ID, source_routine_instance_id="B",
            reason_code="FIXTURE", reason="fixture", command_id="MC-stop",
        )
        started = self.start()
        document = started["document"]
        self.assertEqual("STARTED", started["status"])
        self.assertEqual("ERROR", document["instance_execution"]["B"]["state"])
        self.assertFalse(document["instance_execution"]["B"]["progression_allowed"])
        self.assertTrue(document["instance_execution"]["A"]["progression_allowed"])
        self.assertTrue(document["instance_execution"]["C"]["progression_allowed"])

    def test_normal_auto_and_early_close_share_closing_core_with_distinct_provenance(self):
        for source in ("NORMAL", "AUTO", "EARLY"):
            with self.subTest(source=source):
                self.tearDown()
                self.setUp()
                self.start()
                result = self.close(source=source)
                current = result["document"]["mock_operation_lifecycle"]["current"]
                self.assertEqual((SESSION_CLOSING, "CLOSING"), (result["document"]["session"]["state"], current["state"]))
                self.assertEqual(source, current["close_source"])
                self.assertTrue(all(item["progression_allowed"] is False for item in result["document"]["instance_execution"].values()))
                event_types = {item["event_type"] for item in self.repository.read_events(SESSION_ID)}
                self.assertIn(f"{source}_CLOSE_REQUESTED", event_types)

    def test_market_close_liquidates_all_instances_and_completes_only_after_zero(self):
        self.start()
        self.position("A", 3)
        self.position("B", 2)
        self.close()
        market = _market(now=self.clock["now"] + timedelta(milliseconds=100), bids=((100, 100),), sequence=2)
        started = self.cycle("L1", market=market)
        completed = self.cycle("L2", market=_market(now=self.clock["now"] + timedelta(milliseconds=100), sequence=3))
        document = self.repository.read_session(SESSION_ID)
        self.assertEqual("LIQUIDATION_STARTED", started["action"])
        self.assertEqual({"A", "B"}, {item["routine_instance_id"] for item in started["orders"]})
        self.assertEqual(OUTCOME_DONE, completed["status"])
        self.assertTrue(all(item["holding_qty"] == 0 for item in document["positions"]))
        self.assertEqual((OPERATION_ENDED, "FINAL"), (document["mock_operation_lifecycle"]["current"]["state"], document["mock_operation_lifecycle"]["current"]["pnl_finalization"]))

    def test_close_cancels_active_buy_before_completion(self):
        self.start()
        resting = _market(now=self.clock["now"], asks=((110, 100),))
        order = self.engine.submit_order(
            SESSION_ID, routine_instance_id="A", side="BUY", order_type="LIMIT",
            requested_qty=1, limit_price=100, market=resting, policy=self.policy,
            execution_budget=1000, command_id="MC-active-buy",
        )["order"]
        self.close()
        requested = self.cycle("C1", market=resting)
        confirmed = self.cycle("C2", market=resting)
        completed = self.cycle("C3", market=resting)
        final_order = next(item for item in self.repository.read_session(SESSION_ID)["orders"] if item["mock_order_id"] == order["mock_order_id"])
        self.assertEqual(("CANCEL_REQUEST", "CANCEL_EFFECT"), (requested["action"], confirmed["action"]))
        self.assertEqual(ORDER_CANCELED, final_order["state"])
        self.assertEqual(OUTCOME_DONE, completed["status"])

    def test_current_price_close_waits_for_fresh_trade_then_liquidates(self):
        self.start()
        self.position("A", 2)
        self.close(CLOSE_CURRENT_PRICE)
        stale = _market(now=NOW - timedelta(seconds=10), bids=((100, 100),), sequence=2)
        waited = self.cycle("CP1", market=stale)
        fresh = _market(now=self.clock["now"] + timedelta(milliseconds=100), bids=((100, 100),), sequence=3)
        started = self.cycle("CP2", market=fresh)
        self.assertEqual(("WAIT", "WAIT"), (waited["action"], waited["status"]))
        self.assertEqual("LIQUIDATION_STARTED", started["action"])
        self.assertEqual(
            ("LIMIT", fresh.trade.current_price),
            (started["orders"][0]["order_type"], started["orders"][0]["requested_price"]),
        )

    def test_immediate_liquidation_is_one_time_idempotent_not_persistent_mode(self):
        self.start()
        before_policy = deepcopy(self.repository.read_session(SESSION_ID)["mock_operation_lifecycle"]["current"]["operation_policy_snapshot"])
        requested = self.coordinator.request_immediate_liquidation(
            SESSION_ID, as_of=self.clock["now"], command_id="MC-immediate", source="USER"
        )
        duplicate = self.coordinator.request_immediate_liquidation(
            SESSION_ID, as_of=self.clock["now"], command_id="MC-immediate", source="USER"
        )
        current = duplicate["document"]["mock_operation_lifecycle"]["current"]
        self.assertEqual("REQUESTED", requested["command"]["status"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(before_policy, current["operation_policy_snapshot"])
        self.assertNotIn("operation_mode", current["immediate_commands"]["MC-immediate"])

    def test_immediate_liquidation_completes_only_after_position_zero(self):
        self.start()
        self.position("A", 2)
        self.coordinator.request_immediate_liquidation(
            SESSION_ID, as_of=self.clock["now"], command_id="MC-immediate-complete"
        )
        market = _market(
            now=self.clock["now"] + timedelta(milliseconds=100),
            bids=((100, 100),), sequence=2,
        )
        started = self.cycle("IM1", market=market)
        completed = self.cycle("IM2", market=_market(now=self.clock["now"] + timedelta(milliseconds=100), sequence=3))
        command = completed["document"]["mock_operation_lifecycle"]["current"]["immediate_commands"]["MC-immediate-complete"]
        self.assertEqual("LIQUIDATION_STARTED", started["action"])
        self.assertEqual(OUTCOME_DONE, completed["status"])
        self.assertEqual("COMPLETED", command["status"])

    def test_existing_sell_is_canceled_before_close_order_and_not_duplicated(self):
        self.start()
        self.position("A", 3)
        resting = _market(now=self.clock["now"], bids=((90, 100),))
        existing = self.engine.submit_order(
            SESSION_ID, routine_instance_id="A", side="SELL", order_type="LIMIT",
            requested_qty=3, limit_price=100, market=resting, policy=self.policy,
            command_id="MC-existing-sell",
        )["order"]
        self.close()
        requested = self.cycle("ES1", market=resting)
        document = self.repository.read_session(SESSION_ID)
        self.assertEqual("CANCEL_REQUEST", requested["action"])
        self.assertEqual(ORDER_CANCEL_PENDING, next(item for item in document["orders"] if item["mock_order_id"] == existing["mock_order_id"])["state"])
        self.assertEqual(1, len(document["orders"]))

    def test_carryover_ends_day_but_preserves_position_cycle_and_next_day_identity(self):
        self.start(long_hold=True)
        self.position("B", 5, average=87)
        first_operation = self.repository.read_session(SESSION_ID)["mock_operation_lifecycle"]["current"]["operation_session_id"]
        self.close(CLOSE_CARRYOVER, long_hold=True)
        carried = self.cycle("CO1")
        after = self.repository.read_session(SESSION_ID)
        self.assertEqual(OUTCOME_CARRYOVER_DONE, carried["status"])
        self.assertEqual((5, 87), (after["positions"][1]["holding_qty"], after["positions"][1]["average_price"]))
        self.assertTrue(after["cycle_state_by_instance"]["B"]["active"])
        self.assertEqual(SESSION_WAITING, after["session"]["state"])
        self.clock["now"] += timedelta(days=1)
        next_day = self.start(day="2026-09-04", command="MC-next-day", long_hold=True)
        current = next_day["document"]["mock_operation_lifecycle"]["current"]
        self.assertNotEqual(first_operation, current["operation_session_id"])
        self.assertEqual(5, next_day["document"]["positions"][1]["holding_qty"])
        self.assertTrue(next_day["document"]["cycle_state_by_instance"]["B"]["active"])
        self.assertEqual({}, current["immediate_commands"])
        self.assertEqual(1, len(next_day["document"]["mock_operation_lifecycle"]["history"]))

    def test_carryover_with_mock_snapshot_long_hold_off_requires_review(self):
        self.start(long_hold=False)
        self.position("A", 1)
        self.close(CLOSE_CARRYOVER)
        result = self.cycle("CARRY-NO-PRODUCTION-POLICY")
        self.assertEqual(OUTCOME_REVIEW_REQUIRED, result["status"])
        self.assertEqual(1, result["document"]["positions"][0]["holding_qty"])
        self.assertTrue(result["document"]["review"]["review_required"])

    def test_market_depth_residual_is_canceled_then_reviewed(self):
        self.start(long_hold=True)
        self.position("B", 100)
        self.close(CLOSE_MARKET, long_hold=True)
        partial_market = _market(
            now=self.clock["now"] + timedelta(milliseconds=100),
            bids=((100, 40), (99, 30)), sequence=2,
        )
        started = self.cycle("R1", market=partial_market)
        cancel_requested = self.cycle("R2", market=partial_market, final=True)
        cancel_effect = self.cycle("R3", market=partial_market, final=True)
        carried = self.cycle("R4", market=partial_market, final=True)
        document = self.repository.read_session(SESSION_ID)
        position = next(item for item in document["positions"] if item["routine_instance_id"] == "B")
        self.assertEqual((70, 30), (started["orders"][0]["filled_qty"], position["holding_qty"]))
        self.assertEqual(("FINAL_CARRY_CANCEL", "CANCEL_EFFECT"), (cancel_requested["action"], cancel_effect["action"]))
        self.assertEqual(OUTCOME_REVIEW_REQUIRED, carried["status"])
        self.assertEqual(SESSION_REVIEW_STOPPED, document["session"]["state"])
        self.assertTrue(document["review"]["review_required"])
        self.assertEqual(OPERATION_REVIEW_STOPPED, document["mock_operation_lifecycle"]["current"]["state"])
        self.assertEqual(OUTCOME_REVIEW_REQUIRED, document["mock_operation_lifecycle"]["current"]["outcome"])
        self.assertTrue(all(item["progression_allowed"] is False for item in document["instance_execution"].values()))

    def test_review_reset_zeroes_current_state_preserves_operation_history_and_waits(self):
        self.start()
        self.position("A", 2)
        self.close()
        market = _market(now=self.clock["now"] + timedelta(milliseconds=100), bids=((100, 1),), sequence=2)
        self.cycle("RR1", market=market)
        self.cycle("RR2", market=market, final=True)
        reset = self.service.reset_stock_session(SESSION_ID, command_id="MC-review-reset")
        document = reset["document"]
        self.assertEqual(SESSION_WAITING, document["session"]["state"])
        self.assertTrue(all(item["holding_qty"] == 0 for item in document["positions"]))
        self.assertIsNone(document["mock_operation_lifecycle"]["current"])
        self.assertEqual("RESET", document["mock_operation_lifecycle"]["history"][-1]["outcome"])
        self.assertTrue(all(item["progression_allowed"] is False for item in document["instance_execution"].values()))
        self.assertIn("OPERATION_RESET", {item["event_type"] for item in self.repository.read_events(SESSION_ID)})

    def test_instance_reset_after_error_preserves_sibling_close_state(self):
        self.start()
        self.position("B", 3)
        self.close()
        partial = _market(
            now=self.clock["now"] + timedelta(milliseconds=100),
            bids=((100, 1),), sequence=2,
        )
        started = self.cycle("RS1", market=partial)
        self.service.stop_for_instance_error(
            SESSION_ID,
            source_routine_instance_id="B",
            reason_code="FIXTURE",
            reason="fixture",
            command_id="MC-instance-error-B",
        )
        reset = self.service.reset_routine_instance(
            SESSION_ID,
            routine_instance_id="B",
            command_id="MC-instance-reset-B",
        )
        document = reset["document"]
        self.assertEqual("CLOSING", document["session"]["state"])
        self.assertEqual("WAITING", document["instance_execution"]["B"]["state"])
        self.assertFalse(document["review"]["review_required"])
        self.assertFalse(any(item["routine_instance_id"] == "B" for item in document["orders"]))
        self.assertEqual(0, next(item for item in document["positions"] if item["routine_instance_id"] == "B")["holding_qty"])
        self.assertNotIn("B", document["mock_operation_lifecycle"]["current"]["liquidation_by_instance"])
        self.assertEqual("LIQUIDATION_STARTED", started["action"])

    def test_completion_evaluator_distinguishes_done_pending_and_carryover(self):
        self.start(long_hold=True)
        self.close(CLOSE_CARRYOVER, long_hold=True)
        document = self.repository.read_session(SESSION_ID)
        self.assertEqual(OUTCOME_CARRYOVER_DONE, evaluate_mock_operation_completion(document)["outcome"])
        before = self.repository.read_session(SESSION_ID)

        def mutation(value):
            value["mock_operation_lifecycle"]["current"]["close_method"] = CLOSE_MARKET
            value["positions"][0].update({"holding_qty": 1, "available_qty": 1, "average_price": 1, "realized_cost_basis": 1})
            return value

        changed = self.repository.mutate_session(SESSION_ID, mutation, expected_revision=before["revision"])["document"]
        self.assertEqual(OUTCOME_NOT_READY, evaluate_mock_operation_completion(changed)["outcome"])
        self.assertEqual(OUTCOME_REVIEW_REQUIRED, evaluate_mock_operation_completion(changed, final_close_boundary=True)["outcome"])

    def test_closing_recovery_and_cycle_identity_are_durable(self):
        self.start()
        self.position("A", 1)
        self.close()
        restarted = MockOperationLifecycleCoordinator(
            self.repository, self.engine, now_factory=lambda: self.clock["now"]
        )
        market = _market(now=self.clock["now"] + timedelta(milliseconds=100), bids=((100, 100),), sequence=2)
        self.clock["now"] += timedelta(milliseconds=100)
        first = restarted.process_mock_operation_cycle(
            SESSION_ID, lifecycle_cycle_id="REC-1", as_of=self.clock["now"],
            market=market, policy=self.policy,
        )
        replay = restarted.process_mock_operation_cycle(
            SESSION_ID, lifecycle_cycle_id="REC-1", as_of=self.clock["now"],
            market=market, policy=self.policy,
        )
        self.assertEqual("LIQUIDATION_STARTED", first["action"])
        self.assertEqual("MOCK_OPERATION_CYCLE_ALREADY_PROCESSED", replay["reason"])
        self.assertEqual(1, len(self.repository.read_session(SESSION_ID)["orders"]))

    def test_validation_end_eligibility_rejects_position_and_allows_clean_ended_day(self):
        self.start()
        self.position("A", 1)
        self.assertEqual("MOCK_POSITION_REMAINS", mock_validation_end_eligibility(self.repository.read_session(SESSION_ID))["reason"])
        self.close(CLOSE_CARRYOVER, long_hold=True)
        self.cycle("VE1")
        self.assertFalse(mock_validation_end_eligibility(self.repository.read_session(SESSION_ID))["eligible"])
        reset = self.service.reset_stock_session(SESSION_ID, command_id="MC-end-eligibility-reset")
        self.assertTrue(mock_validation_end_eligibility(reset["document"])["eligible"])

    def test_shared_operation_integrity_failure_isolates_all_instances_without_review(self):
        self.start()
        self.close()
        before = self.repository.read_session(SESSION_ID)

        def corrupt(document):
            document["mock_operation_lifecycle"]["current"]["processed_cycles"] = []
            return document

        self.repository.mutate_session(SESSION_ID, corrupt, expected_revision=before["revision"])
        stopped = self.cycle("BAD-1")
        document = stopped["document"]
        self.assertEqual("INSTANCE_ERROR", stopped["status"])
        self.assertEqual(SESSION_CLOSING, document["session"]["state"])
        self.assertFalse(document["review"]["review_required"])
        self.assertEqual({"ERROR"}, {item["state"] for item in document["instance_execution"].values()})
        self.assertTrue(all(item["progression_allowed"] is False for item in document["instance_execution"].values()))

    def test_operation_events_have_day_and_operation_identity(self):
        self.start()
        self.close(source="EARLY")
        events = [item for item in self.repository.read_events(SESSION_ID) if item["event_type"].startswith("OPERATION_") or item["event_type"].endswith("CLOSE_REQUESTED")]
        self.assertTrue(events)
        for event in events:
            self.assertEqual("2026-09-03", event["payload"]["trading_date"])
            self.assertTrue(event["payload"]["operation_session_id"].startswith("MS-"))

    def test_sell_multi_hoga_low_quantity_divergence_does_not_affect_close_completion(self):
        """Close uses one explicit residual order, not the Routine HOGA downgrade."""
        self.start()
        self.position("A", 2)
        self.close(CLOSE_MARKET)
        market = _market(
            now=self.clock["now"] + timedelta(milliseconds=100),
            bids=((100, 100),), sequence=2,
        )
        started = self.cycle("KH1", market=market)
        completed = self.cycle("KH2", market=_market(now=self.clock["now"] + timedelta(milliseconds=100), sequence=3))
        self.assertEqual(1, len(started["orders"]))
        self.assertEqual((2, 2, 0), (
            started["orders"][0]["requested_qty"],
            started["orders"][0]["filled_qty"],
            started["orders"][0]["remaining_qty"],
        ))
        self.assertEqual(OUTCOME_DONE, completed["status"])


if __name__ == "__main__":
    unittest.main()
