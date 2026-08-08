# -*- coding: utf-8 -*-

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from transition_evidence_reader import (
    ABSENT,
    COMMAND_REQUEST_SCOPE,
    PRESENT,
    TIME_POLICY_SCOPE,
    UNKNOWN,
    TransitionEvidenceScope,
    build_transition_evidence,
    read_transition_fill_evidence,
    read_transition_queue_evidence,
)


class TransitionEvidenceReaderTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.queue_path = root / "order_queue.json"
        self.fills_path = root / "fills.json"
        self.scope = TransitionEvidenceScope(
            scope_type=COMMAND_REQUEST_SCOPE,
            stock_code="005930",
            trade_date="2026-07-27",
            routine_instance_id="routine-instance-1",
            transition_requested_at="2026-07-27 13:30:00",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def _record(**overrides):
        record = {
            "id": "ORDER_1",
            "status": "ORDER_QUEUED",
            "code": "005930",
            "created_at": "2026-07-27 13:31:00",
            "routine_instance_id": "routine-instance-1",
            "operation_command_id": "command-1",
            "order_action": "NEW",
            "side": "SELL",
        }
        record.update(overrides)
        return record

    @staticmethod
    def _fill(**overrides):
        fill = {
            "fill_id": "FILL_1",
            "code": "005930",
            "side": "BUY",
            "filled_quantity": 1,
            "recorded_at": "2026-07-27 13:32:00",
            "routine_instance_id": "routine-instance-1",
            "operation_command_id": "command-1",
        }
        fill.update(overrides)
        return fill

    def _write_queue(self, records):
        self.queue_path.write_text(
            json.dumps({"version": 1, "orders": records}),
            encoding="utf-8",
        )

    def _write_fills(self, records):
        self.fills_path.write_text(
            json.dumps({"version": 1, "fills": records}),
            encoding="utf-8",
        )

    def test_queue_reader_separates_stock_routine_date_and_start_time(self):
        cases = {
            "stock": self._record(code="000660"),
            "routine": self._record(routine_instance_id="routine-instance-2"),
            "date": self._record(created_at="2026-07-26 13:31:00"),
            "before_start": self._record(created_at="2026-07-27 13:29:59"),
        }
        for label, record in cases.items():
            with self.subTest(label=label):
                self._write_queue([record])
                result = read_transition_queue_evidence(
                    self.queue_path,
                    self.scope,
                )
                self.assertEqual(result.order_created, ABSENT)
                self.assertEqual(result.cancellation_started, ABSENT)

    def test_queue_reader_distinguishes_order_and_cancellation(self):
        self._write_queue(
            [
                self._record(),
                self._record(
                    id="ORDER_CANCEL_1",
                    order_action="CANCEL",
                    created_at="2026-07-27 13:33:00",
                ),
            ]
        )
        result = read_transition_queue_evidence(self.queue_path, self.scope)
        self.assertEqual(result.order_created, PRESENT)
        self.assertEqual(result.cancellation_started, PRESENT)

    def test_fill_reader_detects_buy_and_sell(self):
        self._write_fills(
            [
                self._fill(),
                self._fill(
                    fill_id="FILL_2",
                    side="SELL",
                    recorded_at="2026-07-27 13:34:00",
                ),
            ]
        )
        result = read_transition_fill_evidence(self.fills_path, self.scope)
        self.assertEqual(result.buy_fill_detected, PRESENT)
        self.assertEqual(result.sell_fill_detected, PRESENT)

    def test_fill_reader_separates_stock_routine_date_and_command(self):
        cases = {
            "stock": self._fill(code="000660"),
            "routine": self._fill(routine_instance_id="routine-instance-2"),
            "date": self._fill(recorded_at="2026-07-26 13:32:00"),
            "before_start": self._fill(recorded_at="2026-07-27 13:29:59"),
        }
        for label, record in cases.items():
            with self.subTest(label=label):
                self._write_fills([record])
                result = read_transition_fill_evidence(
                    self.fills_path,
                    self.scope,
                )
                self.assertEqual(result.buy_fill_detected, ABSENT)
                self.assertEqual(result.sell_fill_detected, ABSENT)

    def test_missing_scope_identity_is_unknown_not_false(self):
        record = self._record()
        record.pop("routine_instance_id")
        self._write_queue([record])
        result = read_transition_queue_evidence(self.queue_path, self.scope)
        self.assertEqual(result.order_created, UNKNOWN)
        self.assertEqual(result.cancellation_started, UNKNOWN)
        self.assertTrue(result.errors)

    def test_missing_order_side_or_action_is_unknown(self):
        for missing_key in ("side", "order_action"):
            with self.subTest(missing_key=missing_key):
                record = self._record()
                record.pop(missing_key)
                self._write_queue([record])
                result = read_transition_queue_evidence(
                    self.queue_path,
                    self.scope,
                )
                self.assertEqual(result.order_created, UNKNOWN)
                self.assertEqual(result.cancellation_started, UNKNOWN)

    def test_missing_fill_side_is_unknown(self):
        record = self._fill()
        record.pop("side")
        self._write_fills([record])
        result = read_transition_fill_evidence(self.fills_path, self.scope)
        self.assertEqual(result.buy_fill_detected, UNKNOWN)
        self.assertEqual(result.sell_fill_detected, UNKNOWN)

    def test_corrupt_files_are_unknown(self):
        self.queue_path.write_text("{", encoding="utf-8")
        self.fills_path.write_text("[]", encoding="utf-8")
        queue = read_transition_queue_evidence(self.queue_path, self.scope)
        fills = read_transition_fill_evidence(self.fills_path, self.scope)
        self.assertEqual(queue.order_created, UNKNOWN)
        self.assertEqual(queue.cancellation_started, UNKNOWN)
        self.assertEqual(fills.buy_fill_detected, UNKNOWN)
        self.assertEqual(fills.sell_fill_detected, UNKNOWN)

    def test_missing_files_are_unknown(self):
        queue = read_transition_queue_evidence(self.queue_path, self.scope)
        fills = read_transition_fill_evidence(self.fills_path, self.scope)
        self.assertEqual(queue.order_created, UNKNOWN)
        self.assertEqual(queue.cancellation_started, UNKNOWN)
        self.assertEqual(fills.buy_fill_detected, UNKNOWN)
        self.assertEqual(fills.sell_fill_detected, UNKNOWN)

    def test_builder_returns_transition_evidence_only_when_complete(self):
        self._write_queue([self._record()])
        self._write_fills([self._fill()])
        runtime_state = {
            "assigned_routine_instance_id": "routine-instance-1",
            "operation_command_id": "command-1",
            "close_routine_final_sell_ordered": True,
            "close_routine_final_sell_ordered_at": "2026-07-27 13:31:30",
        }
        result = build_transition_evidence(
            queue_path=self.queue_path,
            fills_path=self.fills_path,
            runtime_state=runtime_state,
            runtime_routine_instance_id="routine-instance-1",
            scope=self.scope,
        )
        self.assertTrue(result.complete)
        evidence = result.to_transition_evidence()
        self.assertIsNotNone(evidence)
        self.assertTrue(evidence.routine_close_action_started)
        self.assertTrue(evidence.actual_order_created)
        self.assertTrue(evidence.buy_occurred)
        self.assertFalse(evidence.sell_occurred)
        self.assertFalse(evidence.pending_order_cancellation_started)

    def test_unscoped_routine_close_timestamp_is_unknown(self):
        self._write_queue([])
        self._write_fills([])
        result = build_transition_evidence(
            queue_path=self.queue_path,
            fills_path=self.fills_path,
            runtime_state={"close_routine_final_sell_ordered": True},
            runtime_routine_instance_id="routine-instance-1",
            scope=self.scope,
        )
        self.assertEqual(result.routine_close_started, UNKNOWN)
        self.assertFalse(result.complete)

    def test_invalid_trade_date_is_unknown(self):
        scope = TransitionEvidenceScope(
            scope_type=COMMAND_REQUEST_SCOPE,
            stock_code="005930",
            trade_date="not-a-date",
            routine_instance_id="routine-instance-1",
            transition_requested_at="2026-07-27 13:30:00",
        )
        self._write_queue([])
        result = read_transition_queue_evidence(self.queue_path, scope)
        self.assertEqual(result.order_created, UNKNOWN)
        self.assertTrue(result.errors)

    def test_time_policy_scope_does_not_require_command_id(self):
        scope = TransitionEvidenceScope(
            scope_type=TIME_POLICY_SCOPE,
            stock_code="005930",
            trade_date="2026-07-27",
            routine_instance_id="routine-instance-1",
            auto_close_requested_at="2026-07-27 13:30:00",
            source="TIME_POLICY",
        )
        self._write_queue(
            [
                self._record(
                    operation_command_id=None,
                )
            ]
        )
        result = read_transition_queue_evidence(self.queue_path, scope)
        self.assertEqual(result.order_created, PRESENT)

    def test_optional_command_id_is_checked_when_supplied(self):
        scope = TransitionEvidenceScope(
            scope_type=COMMAND_REQUEST_SCOPE,
            stock_code="005930",
            trade_date="2026-07-27",
            routine_instance_id="routine-instance-1",
            transition_requested_at="2026-07-27 13:30:00",
            operation_command_id="command-1",
        )
        self._write_queue([self._record(operation_command_id="command-2")])
        result = read_transition_queue_evidence(self.queue_path, scope)
        self.assertEqual(result.order_created, ABSENT)

    def test_reader_does_not_modify_source_files(self):
        self._write_queue([self._record()])
        self._write_fills([self._fill()])
        before = {
            path: (
                path.stat().st_mtime_ns,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            for path in (self.queue_path, self.fills_path)
        }
        read_transition_queue_evidence(self.queue_path, self.scope)
        read_transition_fill_evidence(self.fills_path, self.scope)
        after = {
            path: (
                path.stat().st_mtime_ns,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            for path in (self.queue_path, self.fills_path)
        }
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
