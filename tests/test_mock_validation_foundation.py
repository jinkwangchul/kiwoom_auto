# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest

from mock_validation_contract import MockValidationError
from mock_validation_reference_snapshot import build_mock_reference_snapshot
from mock_validation_repository import MockValidationRepository
from mock_validation_session_service import MockValidationSessionService


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _Clock:
    def __init__(self) -> None:
        self.index = 0

    def __call__(self) -> str:
        self.index += 1
        return f"2026-09-03T09:00:{self.index:02d}+09:00"


def _reference() -> dict:
    instances = [
        {
            "instance_id": value,
            "definition_id": "indicator-follow",
            "routine_type": "INDICATOR_FOLLOW",
            "display_name": f"루틴 {value}",
            "group_id": "group-1",
        }
        for value in ("A", "B", "C")
    ]
    return build_mock_reference_snapshot(
        stock={"code": "005930", "name": "삼성전자", "stock_path": "stocks/005930_삼성전자"},
        routine_instances=instances,
        rules_by_instance_id={value: {"version": 1, "instance": value} for value in ("A", "B", "C")},
        created_at="2026-09-03T08:59:00+09:00",
    )


def _file_hash(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _tree_hashes(*roots: Path) -> dict[str, str]:
    return {
        str(path.relative_to(PROJECT_ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for root in roots
        if root.exists()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class MockValidationFoundationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "mock_validation"
        self.repository = MockValidationRepository(self.root)
        self.clock = _Clock()
        self.service = MockValidationSessionService(self.repository, now_factory=self.clock)
        self.session_id = "MV-00000000000000000000000000000001"

    def create(self, session_id: str | None = None):
        return self.service.create_stock_session(
            reference_snapshot=_reference(),
            validation_session_id=session_id or self.session_id,
            command_id=f"MC-create-{session_id or self.session_id}",
        )

    def start(self):
        self.create()
        return self.service.start_stock_mock_session(self.session_id, command_id="MC-start")

    def test_session_creation_is_waiting_and_contains_all_instances(self) -> None:
        result = self.create()
        document = result["document"]
        self.assertTrue(result["created"])
        self.assertEqual("WAITING", document["session"]["state"])
        self.assertEqual({"A", "B", "C"}, set(document["instance_execution"]))
        self.assertTrue(document["session"]["mock_tax_enabled"])
        self.assertEqual(0.002, document["session"]["mock_tax_rate"])

    def test_stock_start_uses_one_timestamp_and_has_no_instance_start_api(self) -> None:
        result = self.start()
        document = result["document"]
        started = document["session"]["started_at"]
        self.assertTrue(started)
        self.assertEqual({started}, {item["started_at"] for item in document["instance_execution"].values()})
        self.assertEqual({True}, {item["progression_allowed"] for item in document["instance_execution"].values()})
        self.assertFalse(hasattr(self.service, "start_instance"))

    def test_instance_order_position_and_pnl_are_isolated(self) -> None:
        self.start()
        order = self.service.create_order(
            self.session_id,
            routine_instance_id="A",
            side="BUY",
            order_type="LIMIT",
            requested_qty=3,
            requested_price=70000,
            command_id="MC-order-A",
        )["order"]
        self.service.set_instance_position(
            self.session_id, "A", holding_qty=3, available_qty=3,
            average_price=70000, realized_cost_basis=210000, command_id="MC-pos-A",
        )
        self.service.set_instance_pnl(
            self.session_id, "A", realized_pnl=100, unrealized_pnl=50,
            gross_pnl=150, commission=10, mock_tax=20, net_pnl=120,
            command_id="MC-pnl-A",
        )
        document = self.repository.read_session(self.session_id)
        self.assertEqual("A", order["routine_instance_id"])
        self.assertFalse(any(item["routine_instance_id"] in {"B", "C"} for item in document["orders"]))
        positions = {item["routine_instance_id"]: item for item in document["positions"]}
        pnl = {item["routine_instance_id"]: item for item in document["pnl"]}
        self.assertEqual(3, positions["A"]["holding_qty"])
        self.assertEqual(0, positions["B"]["holding_qty"])
        self.assertEqual(0, positions["C"]["holding_qty"])
        self.assertEqual(120, pnl["A"]["net_pnl"])
        self.assertEqual(0, pnl["B"]["net_pnl"])
        self.assertEqual(0, pnl["C"]["net_pnl"])

    def test_instance_error_stops_whole_session_and_blocks_progression(self) -> None:
        self.start()
        result = self.service.stop_for_instance_error(
            self.session_id,
            source_routine_instance_id="B",
            reason_code="MOCK_TEST_ERROR",
            reason="B failure",
            command_id="MC-error-B",
        )
        document = result["document"]
        self.assertEqual("REVIEW_STOPPED", document["session"]["state"])
        self.assertEqual("B", document["review"]["source_routine_instance_id"])
        self.assertEqual({False}, {item["progression_allowed"] for item in document["instance_execution"].values()})
        with self.assertRaisesRegex(MockValidationError, "MOCK_SESSION_NOT_RUNNING"):
            self.service.create_order(
                self.session_id, routine_instance_id="A", side="BUY", order_type="LIMIT",
                requested_qty=1, requested_price=1, command_id="MC-after-review",
            )
        event_types = [item["event_type"] for item in self.repository.read_events(self.session_id)]
        self.assertIn("INSTANCE_ERROR", event_types)
        self.assertIn("SESSION_REVIEW_STOPPED", event_types)

    def test_reset_zeroes_all_current_state_preserves_history_and_does_not_start(self) -> None:
        self.start()
        order = self.service.create_order(
            self.session_id, routine_instance_id="A", side="BUY", order_type="LIMIT",
            requested_qty=2, requested_price=10, command_id="MC-order",
        )["order"]
        self.service.transition_order(self.session_id, order["mock_order_id"], "OPEN", command_id="MC-open")
        self.service.append_fill(
            self.session_id, mock_order_id=order["mock_order_id"], qty=1, price=10,
            market_snapshot_identity="MKT-1", command_id="MC-fill",
        )
        self.service.set_instance_position(
            self.session_id, "A", holding_qty=1, available_qty=1,
            average_price=10, realized_cost_basis=10, command_id="MC-pos",
        )
        self.service.stop_for_instance_error(
            self.session_id, source_routine_instance_id="B", reason_code="ERR",
            reason="failure", command_id="MC-error",
        )
        events_before = self.repository.read_events(self.session_id)
        result = self.service.reset_stock_session(self.session_id, command_id="MC-reset")
        document = result["document"]
        self.assertEqual("WAITING", document["session"]["state"])
        self.assertEqual(2, document["session"]["session_generation"])
        self.assertEqual([], document["orders"])
        self.assertEqual([], document["fills"])
        self.assertTrue(all(item["holding_qty"] == 0 for item in document["positions"]))
        self.assertTrue(all(item["net_pnl"] == 0 for item in document["pnl"]))
        self.assertFalse(document["review"]["review_required"])
        self.assertEqual({False}, {item["progression_allowed"] for item in document["instance_execution"].values()})
        events_after = self.repository.read_events(self.session_id)
        self.assertEqual(len(events_before) + 1, len(events_after))
        self.assertEqual("SESSION_RESET", events_after[-1]["event_type"])

    def test_end_archives_immutable_history_and_new_session_starts_zero(self) -> None:
        self.start()
        self.service.set_instance_position(
            self.session_id, "A", holding_qty=5, available_qty=5,
            average_price=100, realized_cost_basis=500, command_id="MC-pos-v1",
        )
        ended = self.service.end_stock_session(self.session_id, command_id="MC-end-v1")
        self.assertEqual("ENDED", ended["document"]["session"]["state"])
        history = self.repository.read_history(self.session_id)
        self.assertEqual(5, history["session_document"]["positions"][0]["holding_qty"])
        self.assertEqual("", self.repository.current_session_id("005930"))

        session_v2 = "MV-00000000000000000000000000000002"
        created_v2 = self.create(session_v2)["document"]
        self.assertEqual(session_v2, created_v2["session"]["validation_session_id"])
        self.assertTrue(all(item["holding_qty"] == 0 for item in created_v2["positions"]))
        self.assertEqual(5, self.repository.read_history(self.session_id)["session_document"]["positions"][0]["holding_qty"])

    def test_ended_session_duplicate_never_becomes_current_again(self) -> None:
        self.start()
        self.service.end_stock_session(self.session_id, command_id="MC-end-immutable")
        duplicate = self.create()
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual("ENDED", duplicate["document"]["session"]["state"])
        self.assertEqual("", self.repository.current_session_id("A005930"))

    def test_order_fill_contract_updates_only_original_order(self) -> None:
        self.start()
        order_a = self.service.create_order(
            self.session_id, routine_instance_id="A", side="BUY", order_type="LIMIT",
            requested_qty=3, requested_price=100, command_id="MC-order-a",
        )["order"]
        order_b = self.service.create_order(
            self.session_id, routine_instance_id="B", side="BUY", order_type="LIMIT",
            requested_qty=2, requested_price=100, command_id="MC-order-b",
        )["order"]
        self.service.transition_order(self.session_id, order_a["mock_order_id"], "OPEN", command_id="MC-open-a")
        self.service.append_fill(
            self.session_id, mock_order_id=order_a["mock_order_id"], qty=1, price=100,
            market_snapshot_identity="BOOK-1", command_id="MC-fill-a1",
        )
        document = self.repository.read_session(self.session_id)
        orders = {item["mock_order_id"]: item for item in document["orders"]}
        self.assertEqual("PARTIAL_FILL", orders[order_a["mock_order_id"]]["state"])
        self.assertEqual(2, orders[order_a["mock_order_id"]]["remaining_qty"])
        self.assertEqual("CREATED", orders[order_b["mock_order_id"]]["state"])
        self.assertEqual("A", document["fills"][0]["routine_instance_id"])

    def test_same_command_is_idempotent(self) -> None:
        self.start()
        first = self.service.create_order(
            self.session_id, routine_instance_id="A", side="BUY", order_type="LIMIT",
            requested_qty=1, requested_price=1, command_id="MC-same",
        )
        second = self.service.create_order(
            self.session_id, routine_instance_id="A", side="BUY", order_type="LIMIT",
            requested_qty=1, requested_price=1, command_id="MC-same",
        )
        self.assertTrue(first["created"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(1, len(self.repository.read_session(self.session_id)["orders"]))

    def test_writer_rejects_production_and_outside_paths(self) -> None:
        for forbidden in ("runtime", "stocks", "routine_instances", "performance_ledger"):
            with self.subTest(forbidden=forbidden):
                with self.assertRaisesRegex(MockValidationError, "MOCK_ROOT_OVERLAPS_PRODUCTION"):
                    MockValidationRepository(PROJECT_ROOT / forbidden)
        with self.assertRaisesRegex(MockValidationError, "MOCK_WRITE_PATH_OUTSIDE_ROOT"):
            self.repository.read_object(PROJECT_ROOT / "runtime" / "order_queue.json")

    def test_malformed_session_fails_closed(self) -> None:
        self.create()
        with self.assertRaisesRegex(MockValidationError, "MOCK_SESSION_SCHEMA_INVALID"):
            self.repository.mutate_session(
                self.session_id,
                lambda document: {**document, "schema_version": "broken"},
            )

    def test_invalid_position_and_market_order_price_fail_closed(self) -> None:
        self.start()
        with self.assertRaisesRegex(MockValidationError, "MOCK_POSITION_AVAILABLE_EXCEEDS_HOLDING"):
            self.service.set_instance_position(
                self.session_id, "A", holding_qty=1, available_qty=2,
                average_price=10, realized_cost_basis=10, command_id="MC-invalid-position",
            )
        with self.assertRaisesRegex(MockValidationError, "MOCK_MARKET_ORDER_PRICE_MUST_BE_EMPTY"):
            self.service.create_order(
                self.session_id, routine_instance_id="A", side="BUY", order_type="MARKET",
                requested_qty=1, requested_price=10, command_id="MC-invalid-market",
            )

    def test_created_or_terminal_order_cannot_receive_fill(self) -> None:
        self.start()
        order = self.service.create_order(
            self.session_id, routine_instance_id="A", side="BUY", order_type="LIMIT",
            requested_qty=1, requested_price=10, command_id="MC-created-order",
        )["order"]
        with self.assertRaisesRegex(MockValidationError, "MOCK_ORDER_NOT_FILLABLE"):
            self.service.append_fill(
                self.session_id, mock_order_id=order["mock_order_id"], qty=1, price=10,
                market_snapshot_identity="BOOK-X", command_id="MC-created-fill",
            )

    def test_pnl_totals_fail_closed_when_inconsistent(self) -> None:
        self.start()
        with self.assertRaisesRegex(MockValidationError, "MOCK_PNL_GROSS_MISMATCH"):
            self.service.set_instance_pnl(
                self.session_id, "A", realized_pnl=10, unrealized_pnl=5,
                gross_pnl=99, commission=1, mock_tax=2, net_pnl=96,
                command_id="MC-invalid-pnl",
            )

    def test_foundation_event_command_is_idempotent(self) -> None:
        self.create()
        first = self.service.start_stock_mock_session(self.session_id, command_id="MC-repeat-start")
        second = self.service.start_stock_mock_session(self.session_id, command_id="MC-repeat-start")
        self.assertTrue(first["started"])
        self.assertTrue(second["duplicate"])
        events = [item for item in self.repository.read_events(self.session_id) if item["event_type"] == "SESSION_STARTED"]
        self.assertEqual(1, len(events))

    def test_mock_operations_do_not_change_production_mutables(self) -> None:
        production_roots = tuple(
            PROJECT_ROOT / name
            for name in ("runtime", "stocks", "routine_instances", "performance_ledger")
        )
        protected_files = (PROJECT_ROOT / "operation_policy.json",)
        before_trees = _tree_hashes(*production_roots)
        before_files = {str(path): _file_hash(path) for path in protected_files}
        self.start()
        self.service.stop_for_instance_error(
            self.session_id, source_routine_instance_id="C",
            reason_code="ISOLATION", reason="test", command_id="MC-isolation",
        )
        self.service.reset_stock_session(self.session_id, command_id="MC-reset-isolation")
        after_trees = _tree_hashes(*production_roots)
        after_files = {str(path): _file_hash(path) for path in protected_files}
        self.assertEqual(before_trees, after_trees)
        self.assertEqual(before_files, after_files)


if __name__ == "__main__":
    unittest.main()
