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
    SESSION_REVIEW_STOPPED,
    SESSION_RUNNING,
    SESSION_WAITING,
    MockValidationError,
    payload_hash,
)
from mock_validation_operation_lifecycle import (
    CLOSE_CARRYOVER,
    CLOSE_CURRENT_PRICE,
    CLOSE_MARKET,
    MockOperationLifecycleCoordinator,
    OPERATION_ENDED,
    OPERATION_REVIEW_STOPPED,
    OUTCOME_CARRYOVER_DONE,
    OUTCOME_DONE,
    OUTCOME_NOT_READY,
    OUTCOME_REVIEW_REQUIRED,
    evaluate_mock_operation_completion,
    mock_validation_end_eligibility,
)
from mock_validation_repository import MockValidationRepository
from mock_validation_session_service import MockValidationSessionService
from mock_validation_virtual_execution import MockExecutionPolicy, MockVirtualExecutionEngine
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
        self.assertFalse(hasattr(self.coordinator, "start_instance_operation"))

    def test_review_state_blocks_operation_start(self):
        self.service.stop_for_instance_error(
            SESSION_ID, source_routine_instance_id="B",
            reason_code="FIXTURE", reason="fixture", command_id="MC-stop",
        )
        with self.assertRaisesRegex(MockValidationError, "START_STATE_INVALID|REVIEW_UNRESOLVED"):
            self.start()

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

    def test_carryover_requires_explicit_long_hold_and_no_active_order(self):
        self.start(long_hold=False)
        self.position("A", 1)
        with self.assertRaisesRegex(MockValidationError, "LONG_HOLD_NOT_ENABLED"):
            self.close(CLOSE_CARRYOVER)

    def test_market_depth_residual_becomes_stock_review_not_carryover(self):
        self.start(long_hold=True)
        self.position("B", 100)
        self.close(CLOSE_MARKET, long_hold=True)
        partial_market = _market(
            now=self.clock["now"] + timedelta(milliseconds=100),
            bids=((100, 40), (99, 30)), sequence=2,
        )
        started = self.cycle("R1", market=partial_market)
        reviewed = self.cycle("R2", market=partial_market, final=True)
        document = self.repository.read_session(SESSION_ID)
        position = next(item for item in document["positions"] if item["routine_instance_id"] == "B")
        self.assertEqual((70, 30), (started["orders"][0]["filled_qty"], position["holding_qty"]))
        self.assertEqual(OUTCOME_REVIEW_REQUIRED, reviewed["status"])
        self.assertEqual(SESSION_REVIEW_STOPPED, document["session"]["state"])
        self.assertEqual("B", document["review"]["source_routine_instance_id"])
        self.assertEqual(OPERATION_REVIEW_STOPPED, document["mock_operation_lifecycle"]["current"]["state"])
        self.assertNotEqual(OUTCOME_CARRYOVER_DONE, document["mock_operation_lifecycle"]["current"]["outcome"])
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

    def test_stock_level_resume_continues_existing_close_without_duplicate_order(self):
        self.start()
        self.position("B", 3)
        self.close()
        partial = _market(
            now=self.clock["now"] + timedelta(milliseconds=100),
            bids=((100, 1),), sequence=2,
        )
        started = self.cycle("RS1", market=partial)
        self.cycle("RS2", market=partial, final=True)
        self.clock["now"] += timedelta(milliseconds=100)
        resumed = self.coordinator.resume_stock_operation(
            SESSION_ID, as_of=self.clock["now"], command_id="MC-resume",
            resolution="fresh market evidence restored",
        )
        fresh = _market(
            now=self.clock["now"] + timedelta(milliseconds=100),
            bids=((100, 100),), sequence=3,
        )
        progressed = self.cycle("RS3", market=fresh)
        completed = self.cycle("RS4", market=_market(now=self.clock["now"] + timedelta(milliseconds=100), sequence=4))
        document = self.repository.read_session(SESSION_ID)
        self.assertEqual("CLOSING", resumed["status"])
        self.assertEqual("LIQUIDATION_PROGRESS", progressed["action"])
        self.assertEqual(OUTCOME_DONE, completed["status"])
        self.assertEqual(1, len(document["orders"]))
        self.assertEqual(started["orders"][0]["mock_order_id"], document["orders"][0]["mock_order_id"])

    def test_completion_evaluator_distinguishes_done_pending_carryover_and_review(self):
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

    def test_operation_integrity_failure_stops_the_whole_stock(self):
        self.start()
        self.close()
        before = self.repository.read_session(SESSION_ID)

        def corrupt(document):
            document["mock_operation_lifecycle"]["current"]["processed_cycles"] = []
            return document

        self.repository.mutate_session(SESSION_ID, corrupt, expected_revision=before["revision"])
        stopped = self.cycle("BAD-1")
        document = stopped["document"]
        self.assertEqual(OUTCOME_REVIEW_REQUIRED, stopped["status"])
        self.assertEqual(SESSION_REVIEW_STOPPED, document["session"]["state"])
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
