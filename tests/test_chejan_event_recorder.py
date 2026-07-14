# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import multiprocessing
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import chejan_event_recorder
from chejan_event_recorder import (
    build_order_reconciliation_preview,
    inspect_broker_chejan_lifecycle,
    inspect_incomplete_order_reconciliation,
    record_chejan_event,
)


def _chejan_process_worker(queue_path: str, output: multiprocessing.Queue) -> None:
    review = {
        "chejan_review_ok": True,
        "review_stage": "chejan_event_reviewed",
        "next_stage": "FILL_RECORD_REQUIRED",
        "event_type": "PARTIAL_FILL",
        "order_id": "ORDER_1",
        "order_queued_id": "ORDER_QUEUED_ORDER_1",
        "broker_order_no": "BRK_1",
        "request_hash": "HASH_1",
        "lock_id": "LOCK_1",
        "execution_id": "EXEC_1",
        "matched_by": "broker_order_no",
        "blocked_reasons": [],
        "warnings": [],
    }
    event = {
        "normalized": True,
        "event_stage": "chejan_event_normalized",
        "event_type": "PARTIAL_FILL",
        "broker": "KIWOOM",
        "source": "kiwoom_chejan",
        "gubun": "0",
        "broker_order_no": "BRK_1",
        "account_no": "12345678",
        "code": "003550",
        "name": "LG",
        "side": "BUY",
        "order_status": "FILLED",
        "order_quantity": 10,
        "filled_quantity": 3,
        "remaining_quantity": 7,
        "order_price": 1000,
        "filled_price": 1000,
        "request_hash": None,
        "lock_id": None,
        "execution_id": None,
        "unresolved": False,
        "blocked_reasons": [],
        "warnings": [],
        "raw_event": {"received_at": "2026-07-04 09:30:00", "fid_values": {"909": "EXECUTION_909_1"}},
    }
    try:
        output.put(record_chejan_event(review, event, queue_path, context={"manual_chejan_event_record_confirmed": True}))
    except Exception as exc:  # pragma: no cover - returned to parent process
        output.put({"recorded": False, "error": repr(exc)})


class ChejanEventRecorderTest(unittest.TestCase):
    def _review(self, **overrides: object) -> dict[str, object]:
        result = {
            "chejan_review_ok": True,
            "review_stage": "chejan_event_reviewed",
            "next_stage": "FILL_RECORD_REQUIRED",
            "event_type": "PARTIAL_FILL",
            "order_id": "ORDER_1",
            "order_queued_id": "ORDER_QUEUED_ORDER_1",
            "broker_order_no": "BRK_1",
            "request_hash": "HASH_1",
            "lock_id": "LOCK_1",
            "execution_id": "EXEC_1",
            "matched_by": "broker_order_no",
            "blocked_reasons": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def _event(self, **overrides: object) -> dict[str, object]:
        event = {
            "normalized": True,
            "event_stage": "chejan_event_normalized",
            "event_type": "PARTIAL_FILL",
            "broker": "KIWOOM",
            "source": "kiwoom_chejan",
            "gubun": "0",
            "broker_order_no": "BRK_1",
            "account_no": "12345678",
            "code": "003550",
            "name": "LG",
            "side": "BUY",
            "order_status": "FILLED",
            "order_quantity": 10,
            "filled_quantity": 3,
            "remaining_quantity": 7,
            "order_price": 1000,
            "filled_price": 1000,
            "request_hash": None,
            "lock_id": None,
            "execution_id": None,
            "unresolved": False,
            "blocked_reasons": [],
            "warnings": [],
            "raw_event": {"received_at": "2026-07-04 09:30:00"},
        }
        event.update(overrides)
        return event

    def _record(self, **overrides: object) -> dict[str, object]:
        record = {
            "id": "ORDER_QUEUED_ORDER_1",
            "status": "SEND_CALL_ACCEPTED",
            "order_id": "ORDER_1",
            "request_hash": "HASH_1",
            "lock_id": "LOCK_1",
            "execution_id": "EXEC_1",
            "broker_order_no": "BRK_1",
            "send_order_called": True,
            "send_order_result_status": "SEND_ORDER_CALLED",
            "execution_enabled": False,
        }
        record.update(overrides)
        return record

    def _write_queue(self, directory: str, record: dict[str, object] | None = None, root: object | None = None) -> Path:
        path = Path(directory) / "order_queue.json"
        data = {"version": 1, "updated_at": "2026-07-04 09:00:00", "orders": [record or self._record()]}
        if root is not None:
            data = root
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _read_queue(self, path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _sha256(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest().upper()

    def _write_fills(self, directory: str, fills: list[dict[str, object]] | None = None, root: object | None = None) -> Path:
        path = Path(directory) / "fills.json"
        data = {"version": 1, "updated_at": "2026-07-04 09:10:00", "fills": fills or []}
        if root is not None:
            data = root
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _fill(self, **overrides: object) -> dict[str, object]:
        execution_no = overrides.pop("execution_no", "EXECUTION_909_1")
        fill = {
            "fill_id": "FILL_1",
            "event_type": "PARTIAL_FILL",
            "broker_order_no": "BRK_1",
            "order_id": "ORDER_1",
            "order_queued_id": "ORDER_QUEUED_ORDER_1",
            "execution_id": "EXEC_1",
            "request_hash": "HASH_1",
            "lock_id": "LOCK_1",
            "filled_quantity": 3,
            "filled_price": 1000,
            "remaining_quantity": 7,
            "order_quantity": 10,
            "received_at": "2026-07-04 09:30:00",
            "normalized_event": {
                "event_type": "PARTIAL_FILL",
                "broker_order_no": "BRK_1",
                "raw_event": {"fid_values": {"909": execution_no}},
            },
        }
        fill.update(overrides)
        return fill

    def _record_event(
        self,
        path: Path,
        review: object | None = None,
        event: object | None = None,
        **kwargs: object,
    ) -> dict[str, object]:
        context = kwargs.pop("context", {"manual_chejan_event_record_confirmed": True})
        return record_chejan_event(
            self._review() if review is None else review,
            self._event() if event is None else event,
            path,
            context=context,
            **kwargs,
        )

    def test_review_failure_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)

            result = self._record_event(path, review=self._review(chejan_review_ok=False))

            self.assertFalse(result["recorded"])
            self.assertEqual("chejan_review", result["record_stage"])
            self.assertIn("chejan_review_result.chejan_review_ok is not true", result["blocked_reasons"])

    def test_context_confirmation_missing_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)

            result = self._record_event(path, context={})

            self.assertFalse(result["recorded"])
            self.assertEqual("operator_confirmation", result["record_stage"])

    def test_queue_path_missing_is_blocked(self) -> None:
        result = record_chejan_event(
            self._review(),
            self._event(),
            None,
            context={"manual_chejan_event_record_confirmed": True},
        )

        self.assertFalse(result["recorded"])
        self.assertEqual("queue_path", result["record_stage"])

    def test_queue_structure_errors_are_blocked(self) -> None:
        cases = [
            ("root", []),
            ("orders", {"version": 1, "orders": {}}),
            ("order_item", {"version": 1, "orders": ["invalid"]}),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            for name, root in cases:
                with self.subTest(name=name):
                    path = self._write_queue(tmpdir, root=root)

                    result = self._record_event(path)

                    self.assertFalse(result["recorded"])
                    self.assertEqual("read_queue", result["record_stage"])

    def test_target_record_missing_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir, record=self._record(id="OTHER", order_id="OTHER"))

            result = self._record_event(path)

            self.assertFalse(result["recorded"])
            self.assertIn("target ORDER_QUEUED record not found", result["blocked_reasons"])

    def test_identity_mismatch_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir, record=self._record(request_hash="OTHER"))

            result = self._record_event(path)

            self.assertFalse(result["recorded"])
            self.assertEqual("record_consistency", result["record_stage"])

    def test_broker_order_no_enrichment_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir, record=self._record(broker_order_no=None))

            result = self._record_event(path)
            record = self._read_queue(path)["orders"][0]

            self.assertTrue(result["recorded"])
            self.assertTrue(result["broker_order_no_enriched"])
            self.assertEqual("BRK_1", record["broker_order_no"])

    def test_broker_order_no_mismatch_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir, record=self._record(broker_order_no="OTHER"))

            result = self._record_event(path)

            self.assertFalse(result["recorded"])
            self.assertEqual("broker_order_no", result["record_stage"])

    def test_broker_order_no_both_missing_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir, record=self._record(broker_order_no=None))

            result = self._record_event(path, event=self._event(broker_order_no=None))

            self.assertFalse(result["recorded"])
            self.assertIn("broker_order_no is required to record Chejan event", result["blocked_reasons"])

    def test_chejan_events_append_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)

            result = self._record_event(path)
            record = self._read_queue(path)["orders"][0]
            data = self._read_queue(path)
            event = record["chejan_events"][0]

            self.assertTrue(result["recorded"])
            self.assertEqual(1, len(record["chejan_events"]))
            self.assertEqual("PARTIAL_FILL", event["event_type"])
            self.assertEqual("BRK_1", event["broker_order_no"])
            self.assertEqual("2026-07-04 09:30:00", event["received_at"])
            self.assertEqual(self._event(), event["normalized_event"])
            self.assertEqual(1, data["revision"])
            self.assertEqual(0, result["revision_before"])
            self.assertEqual(1, result["revision_after"])

    def test_broker_execution_number_is_preferred_for_event_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)
            event = self._event(raw_event={"fid_values": {"909": "EXECUTION_909_1"}})

            result = self._record_event(path, event=event)
            stored = self._read_queue(path)["orders"][0]["chejan_events"][0]

            self.assertTrue(result["recorded"])
            self.assertEqual("broker_event_id", result["event_identity_source"])
            self.assertEqual(result["event_identity"], stored["event_identity"])

    def test_duplicate_event_is_idempotent_without_revision_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)

            first = self._record_event(path)
            second = self._record_event(path)
            data = self._read_queue(path)

            self.assertTrue(first["recorded"])
            self.assertFalse(second["recorded"])
            self.assertTrue(second["duplicate"])
            self.assertTrue(second["idempotent"])
            self.assertFalse(second["committed"])
            self.assertFalse(second["file_write"])
            self.assertEqual(1, data["revision"])
            self.assertEqual(1, len(data["orders"][0]["chejan_events"]))

    def test_distinct_event_identity_appends_normally(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)

            first = self._record_event(path)
            second = self._record_event(
                path,
                event=self._event(filled_quantity=5, remaining_quantity=5),
            )
            data = self._read_queue(path)

            self.assertTrue(first["recorded"])
            self.assertTrue(second["recorded"])
            self.assertNotEqual(first["event_identity"], second["event_identity"])
            self.assertEqual(2, data["revision"])
            self.assertEqual(2, len(data["orders"][0]["chejan_events"]))

    def test_two_threads_record_same_event_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)
            start = threading.Event()
            results: list[dict[str, object]] = []

            def worker() -> None:
                start.wait()
                results.append(self._record_event(path))

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            start.set()
            for thread in threads:
                thread.join(10)

            data = self._read_queue(path)
            self.assertEqual(2, len(results))
            self.assertEqual(1, sum(result.get("recorded") is True for result in results))
            self.assertEqual(1, sum(result.get("duplicate") is True for result in results))
            self.assertEqual(1, data["revision"])
            self.assertEqual(1, len(data["orders"][0]["chejan_events"]))

    def test_two_processes_record_same_event_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)
            ctx = multiprocessing.get_context("spawn")
            output = ctx.Queue()
            processes = [ctx.Process(target=_chejan_process_worker, args=(str(path), output)) for _ in range(2)]

            for process in processes:
                process.start()
            for process in processes:
                process.join(20)

            results = [output.get(timeout=5) for _ in processes]
            data = self._read_queue(path)

            self.assertTrue(all(process.exitcode == 0 for process in processes), [process.exitcode for process in processes])
            self.assertEqual(1, sum(result.get("recorded") is True for result in results))
            self.assertEqual(1, sum(result.get("duplicate") is True for result in results))
            self.assertEqual(1, data["revision"])
            self.assertEqual(1, len(data["orders"][0]["chejan_events"]))

    def test_post_write_failure_preserves_canonical_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)
            writer_result = {
                "committed": True,
                "changed": True,
                "file_write": True,
                "queue_write": True,
                "queue_committed": True,
                "post_write_verified": False,
                "revision_before": 0,
                "revision_after": 1,
                "lock_acquired": True,
                "cas_checked": True,
                "write_stage": "post_write_verify",
                "blocked_reasons": ["forced post-write failure"],
                "warnings": [],
            }

            with mock.patch("chejan_event_recorder.mutate_order_queue", return_value=writer_result):
                result = self._record_event(path)

            self.assertFalse(result["recorded"])
            for field in ("committed", "changed", "file_write", "queue_write", "queue_committed", "lock_acquired", "cas_checked"):
                self.assertTrue(result[field], field)
            self.assertFalse(result["post_write_verified"])
            self.assertEqual(0, result["revision_before"])
            self.assertEqual(1, result["revision_after"])

    def test_stale_expected_revision_is_blocked_without_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)

            result = self._record_event(path, context={"manual_chejan_event_record_confirmed": True, "expected_revision": 9})

            self.assertFalse(result["recorded"])
            self.assertEqual("revision_cas", result["record_stage"])
            self.assertEqual(0, self._read_queue(path).get("revision", 0))
            self.assertFalse(Path(str(path) + ".bak").exists())

    def test_metadata_is_updated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)

            self._record_event(path)
            record = self._read_queue(path)["orders"][0]

            self.assertTrue(record["chejan_event_recorded"])
            self.assertEqual("chejan_event_review", record["chejan_event_record_source"])
            self.assertEqual("PARTIAL_FILL", record["last_chejan_event_type"])
            self.assertEqual("2026-07-04 09:30:00", record["last_chejan_event_at"])
            self.assertEqual("chejan_event_reviewed", record["last_chejan_review_stage"])
            self.assertTrue(record["chejan_event_recorded_at"])
            self.assertTrue(record["updated_at"])

    def test_partial_fill_transitions_to_partially_filled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)

            result = self._record_event(path)
            record = self._read_queue(path)["orders"][0]

            self.assertTrue(result["lifecycle_updated"])
            self.assertEqual("PARTIALLY_FILLED", result["lifecycle_status"])
            self.assertEqual("PARTIALLY_FILLED", record["status"])
            self.assertTrue(record["send_order_called"])
            self.assertEqual("SEND_ORDER_CALLED", record["send_order_result_status"])
            self.assertTrue(record["broker_accepted"])
            self.assertEqual(3, record["cumulative_filled_quantity"])
            self.assertEqual(7, record["remaining_quantity"])

    def test_partial_fill_next_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)

            result = self._record_event(path)

            self.assertEqual("FILL_RECORD_REQUIRED", result["next_stage"])

    def test_full_fill_next_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)

            result = self._record_event(
                path,
                review=self._review(event_type="FULL_FILL"),
                event=self._event(event_type="FULL_FILL", filled_quantity=10, remaining_quantity=0),
            )

            self.assertEqual("FILL_RECORD_REQUIRED", result["next_stage"])

    def test_order_accepted_next_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)

            result = self._record_event(
                path,
                review=self._review(next_stage="CHEJAN_EVENT_RECORD_REQUIRED", event_type="ORDER_ACCEPTED"),
                event=self._event(event_type="ORDER_ACCEPTED", filled_quantity=0, remaining_quantity=10),
            )

            self.assertEqual("CHEJAN_EVENT_RECORDED", result["next_stage"])

    def test_backup_file_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)

            result = self._record_event(path)

            self.assertTrue(Path(result["backup_path"]).exists())

    def test_backup_false_returns_no_backup_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)

            result = self._record_event(path, backup=False)

            self.assertTrue(result["recorded"])
            self.assertIsNone(result["backup_path"])
            self.assertFalse(Path(str(path) + ".bak").exists())

    def test_stale_snapshot_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)
            snapshot = {"sha256": self._sha256(path)}
            data = self._read_queue(path)
            data["updated_at"] = "changed"
            path.write_text(json.dumps(data), encoding="utf-8")

            result = self._record_event(path, queue_snapshot=snapshot)

            self.assertFalse(result["recorded"])
            self.assertIn("queue file changed after Chejan event review; manual review required", result["blocked_reasons"])

    def test_before_after_sha256_are_returned_and_changed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)
            before = self._sha256(path)

            result = self._record_event(path)

            self.assertEqual(before, result["before_sha256"])
            self.assertEqual(self._sha256(path), result["after_sha256"])
            self.assertNotEqual(result["before_sha256"], result["after_sha256"])

    def test_inputs_are_not_mutated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)
            review = self._review()
            event = self._event()
            originals = (deepcopy(review), deepcopy(event))

            record_chejan_event(
                review,
                event,
                path,
                context={"manual_chejan_event_record_confirmed": True},
            )

            self.assertEqual(originals[0], review)
            self.assertEqual(originals[1], event)

    def test_runtime_default_path_is_not_referenced(self) -> None:
        module_text = chejan_event_recorder.__loader__.get_source(chejan_event_recorder.__name__)

        self.assertNotIn("ORDER_QUEUE_PATH", module_text)
        self.assertNotIn("runtime/order_queue.json", module_text)
        self.assertNotIn("runtime\\order_queue.json", module_text)

    def test_fills_and_positions_files_are_not_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)

            self._record_event(path)

            self.assertFalse((Path(tmpdir) / "fills.json").exists())
            self.assertFalse((Path(tmpdir) / "positions.json").exists())

    def test_no_send_order_gui_or_timer_references(self) -> None:
        module_text = chejan_event_recorder.__loader__.get_source(chejan_event_recorder.__name__)

        self.assertNotIn("kiwoom_order_adapter", module_text)
        self.assertNotIn("send_order_entrypoint", module_text)
        self.assertNotIn("dynamicCall", module_text)
        self.assertNotIn("QTimer", module_text)
        self.assertNotIn("QPushButton", module_text)

    def test_normalized_event_must_be_dict(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)

            result = self._record_event(path, event="invalid")

            self.assertFalse(result["recorded"])
            self.assertEqual("normalized_event", result["record_stage"])

    def test_review_next_stage_must_be_recordable(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)

            result = self._record_event(path, review=self._review(next_stage="BLOCKED"))

            self.assertFalse(result["recorded"])
            self.assertIn("chejan_review_result.next_stage is not recordable", result["blocked_reasons"])

    def test_event_type_mismatch_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)

            result = self._record_event(path, review=self._review(event_type="FULL_FILL"))

            self.assertFalse(result["recorded"])
            self.assertIn("normalized_event.event_type does not match chejan_review_result.event_type", result["blocked_reasons"])

    def test_target_status_must_be_eligible_for_event_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir, record=self._record(status="OTHER"))

            result = self._record_event(path)

            self.assertFalse(result["recorded"])
            self.assertIn("target record.status cannot accept FILL event", result["blocked_reasons"][0])

    def test_order_queued_order_accepted_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir, record=self._record(status="ORDER_QUEUED"))

            result = self._record_event(
                path,
                review=self._review(next_stage="CHEJAN_EVENT_RECORD_REQUIRED", event_type="ORDER_ACCEPTED"),
                event=self._event(event_type="ORDER_ACCEPTED", filled_quantity=0, remaining_quantity=10),
            )

            self.assertFalse(result["recorded"])
            self.assertIn("target record.status cannot accept BROKER_ACCEPT event: ORDER_QUEUED", result["blocked_reasons"])

    def test_order_queued_partial_fill_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir, record=self._record(status="ORDER_QUEUED"))

            result = self._record_event(path)

            self.assertFalse(result["recorded"])
            self.assertIn("target record.status cannot accept FILL event: ORDER_QUEUED", result["blocked_reasons"])

    def test_dispatch_claimed_full_fill_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir, record=self._record(status="DISPATCH_CLAIMED"))

            result = self._record_event(
                path,
                review=self._review(event_type="FULL_FILL"),
                event=self._event(event_type="FULL_FILL", filled_quantity=10, remaining_quantity=0),
            )

            self.assertFalse(result["recorded"])
            self.assertIn("target record.status cannot accept FILL event: DISPATCH_CLAIMED", result["blocked_reasons"])

    def test_send_attempted_cancel_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir, record=self._record(status="SEND_ATTEMPTED"))

            result = self._record_event(
                path,
                review=self._review(next_stage="CHEJAN_EVENT_RECORD_REQUIRED", event_type="ORDER_CANCELED"),
                event=self._event(event_type="ORDER_CANCELED", filled_quantity=0, remaining_quantity=10),
            )

            self.assertFalse(result["recorded"])
            self.assertIn("target record.status cannot accept CANCEL event: SEND_ATTEMPTED", result["blocked_reasons"])

    def test_send_call_in_progress_order_accepted_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir, record=self._record(status="SEND_CALL_IN_PROGRESS"))

            result = self._record_event(
                path,
                review=self._review(next_stage="CHEJAN_EVENT_RECORD_REQUIRED", event_type="ORDER_ACCEPTED"),
                event=self._event(event_type="ORDER_ACCEPTED", filled_quantity=0, remaining_quantity=10),
            )

            self.assertFalse(result["recorded"])
            self.assertIn("target record.status cannot accept BROKER_ACCEPT event: SEND_CALL_IN_PROGRESS", result["blocked_reasons"])

    def test_send_call_accepted_chejan_acceptance_transitions_broker_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(
                tmpdir,
                record=self._record(
                    status="SEND_CALL_ACCEPTED",
                    send_order_called=True,
                    broker_call_executed=True,
                    broker_api_called=True,
                    actual_order_sent=False,
                    broker_accepted=False,
                    broker_rejected=False,
                ),
            )

            result = self._record_event(
                path,
                review=self._review(next_stage="CHEJAN_EVENT_RECORD_REQUIRED", event_type="ORDER_ACCEPTED"),
                event=self._event(event_type="ORDER_ACCEPTED", order_status="ACCEPTED", filled_quantity=0, remaining_quantity=10),
            )
            record = self._read_queue(path)["orders"][0]

            self.assertTrue(result["recorded"])
            self.assertEqual("BROKER_ACCEPTED", result["lifecycle_status"])
            self.assertEqual("BROKER_ACCEPTED", record["status"])
            self.assertTrue(record["broker_accepted"])
            self.assertFalse(record["broker_rejected"])
            self.assertTrue(record["actual_order_sent"])
            self.assertFalse(record["manual_reconciliation_required"])

    def test_send_uncertain_chejan_acceptance_clears_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(
                tmpdir,
                record=self._record(
                    status="SEND_UNCERTAIN",
                    send_order_called=True,
                    send_uncertain=True,
                    manual_reconciliation_required=True,
                    actual_order_sent=False,
                ),
            )

            result = self._record_event(
                path,
                review=self._review(next_stage="CHEJAN_EVENT_RECORD_REQUIRED", event_type="ORDER_OPEN"),
                event=self._event(event_type="ORDER_OPEN", filled_quantity=0, remaining_quantity=10),
            )
            record = self._read_queue(path)["orders"][0]

            self.assertTrue(result["recorded"])
            self.assertEqual("BROKER_ACCEPTED", record["status"])
            self.assertFalse(record["send_uncertain"])
            self.assertFalse(record["manual_reconciliation_required"])
            self.assertTrue(record["broker_accepted"])

    def test_order_rejected_transitions_to_broker_rejected_without_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir, record=self._record(status="SEND_CALL_ACCEPTED"))

            result = self._record_event(
                path,
                review=self._review(next_stage="CHEJAN_EVENT_RECORD_REQUIRED", event_type="ORDER_REJECTED"),
                event=self._event(event_type="ORDER_REJECTED", order_status="REJECTED", filled_quantity=0, remaining_quantity=10),
            )
            record = self._read_queue(path)["orders"][0]

            self.assertTrue(result["recorded"])
            self.assertEqual("BROKER_REJECTED", record["status"])
            self.assertTrue(record["broker_rejected"])
            self.assertFalse(record["broker_accepted"])
            self.assertFalse(record["automatic_retry_allowed"])
            self.assertFalse(record["actual_order_sent"])

    def test_full_fill_transitions_to_filled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir, record=self._record(status="BROKER_ACCEPTED"))

            result = self._record_event(
                path,
                review=self._review(event_type="FULL_FILL"),
                event=self._event(event_type="FULL_FILL", filled_quantity=10, remaining_quantity=0),
            )
            record = self._read_queue(path)["orders"][0]

            self.assertTrue(result["recorded"])
            self.assertEqual("FILLED", record["status"])
            self.assertEqual(10, record["cumulative_filled_quantity"])
            self.assertEqual(0, record["remaining_quantity"])
            self.assertEqual("CHEJAN_EVENT_" + result["event_identity"], record["final_fill_event_id"])

    def test_send_uncertain_full_fill_reconciliation_transitions_to_filled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(
                tmpdir,
                record=self._record(status="SEND_UNCERTAIN", send_uncertain=True, manual_reconciliation_required=True),
            )

            result = self._record_event(
                path,
                review=self._review(event_type="FULL_FILL"),
                event=self._event(event_type="FULL_FILL", filled_quantity=10, remaining_quantity=0),
            )
            record = self._read_queue(path)["orders"][0]

            self.assertTrue(result["recorded"])
            self.assertEqual("FILLED", record["status"])
            self.assertTrue(record["broker_accepted"])
            self.assertFalse(record["manual_reconciliation_required"])

    def test_two_partial_fills_compute_weighted_average(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(
                tmpdir,
                record=self._record(status="PARTIALLY_FILLED", cumulative_filled_quantity=1, total_filled_quantity=1, remaining_quantity=1, average_fill_price=10000),
            )

            result = self._record_event(
                path,
                event=self._event(order_quantity=2, filled_quantity=2, remaining_quantity=0, filled_price=11000, raw_event={"fid_values": {"909": "EXECUTION_909_2"}}),
            )
            record = self._read_queue(path)["orders"][0]

            self.assertTrue(result["recorded"])
            self.assertEqual("FILLED", record["status"])
            self.assertEqual(10500, record["average_fill_price"])

    def test_three_partial_fills_compute_weighted_average(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(
                tmpdir,
                record=self._record(status="PARTIALLY_FILLED", cumulative_filled_quantity=2, total_filled_quantity=2, remaining_quantity=3, average_fill_price=10000),
            )

            self._record_event(
                path,
                event=self._event(order_quantity=5, filled_quantity=4, remaining_quantity=1, filled_price=11500, raw_event={"fid_values": {"909": "EXECUTION_909_2"}}),
            )
            self._record_event(
                path,
                event=self._event(order_quantity=5, filled_quantity=5, remaining_quantity=0, filled_price=13000, raw_event={"fid_values": {"909": "EXECUTION_909_3"}}),
            )
            record = self._read_queue(path)["orders"][0]

            self.assertEqual("FILLED", record["status"])
            self.assertEqual(11200, record["average_fill_price"])

    def test_late_partial_fill_does_not_overwrite_average_price(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(
                tmpdir,
                record=self._record(status="FILLED", cumulative_filled_quantity=10, total_filled_quantity=10, remaining_quantity=0, average_fill_price=12345),
            )

            result = self._record_event(
                path,
                event=self._event(filled_quantity=3, remaining_quantity=7, filled_price=99999, raw_event={"fid_values": {"909": "LATE_PARTIAL_AVG"}}),
            )
            record = self._read_queue(path)["orders"][0]

            self.assertTrue(result["recorded"])
            self.assertEqual(12345, record["average_fill_price"])

    def test_duplicate_fill_does_not_change_average_quantity_or_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)

            first = self._record_event(path)
            second = self._record_event(path)
            record = self._read_queue(path)["orders"][0]

            self.assertTrue(first["recorded"])
            self.assertFalse(second["recorded"])
            self.assertTrue(second["duplicate"])
            self.assertEqual(3, record["cumulative_filled_quantity"])
            self.assertEqual(1000, record.get("average_fill_price"))
            self.assertEqual(1, self._read_queue(path)["revision"])

    def test_cancel_without_fill_transitions_to_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir, record=self._record(status="BROKER_ACCEPTED"))

            result = self._record_event(
                path,
                review=self._review(next_stage="CHEJAN_EVENT_RECORD_REQUIRED", event_type="ORDER_CANCELED"),
                event=self._event(event_type="ORDER_CANCELED", order_status="CANCELED", filled_quantity=0, remaining_quantity=10),
            )
            record = self._read_queue(path)["orders"][0]

            self.assertTrue(result["recorded"])
            self.assertEqual("CANCELLED", record["status"])
            self.assertEqual(0, record["remaining_quantity"])

    def test_cancel_after_partial_fill_transitions_to_partial_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(
                tmpdir,
                record=self._record(status="PARTIALLY_FILLED", cumulative_filled_quantity=3, remaining_quantity=7),
            )

            result = self._record_event(
                path,
                review=self._review(next_stage="CHEJAN_EVENT_RECORD_REQUIRED", event_type="ORDER_CANCELED"),
                event=self._event(event_type="ORDER_CANCELED", order_status="CANCELED", filled_quantity=3, remaining_quantity=7),
            )
            record = self._read_queue(path)["orders"][0]

            self.assertTrue(result["recorded"])
            self.assertEqual("PARTIAL_CANCELLED", record["status"])
            self.assertEqual(3, record["final_filled_quantity"])

    def test_out_of_order_partial_after_full_does_not_regress_filled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(
                tmpdir,
                record=self._record(status="FILLED", cumulative_filled_quantity=10, total_filled_quantity=10, remaining_quantity=0),
            )

            result = self._record_event(
                path,
                event=self._event(filled_quantity=3, remaining_quantity=7, raw_event={"fid_values": {"909": "LATE_PARTIAL"}}),
            )
            record = self._read_queue(path)["orders"][0]

            self.assertTrue(result["recorded"])
            self.assertEqual("FILLED", record["status"])
            self.assertTrue(record["out_of_order_detected"])

    def test_broker_order_number_cannot_attach_to_different_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            other = self._record(id="OTHER", order_id="OTHER", request_hash="OTHER_HASH", lock_id="OTHER_LOCK", execution_id="OTHER_EXEC", broker_order_no="BRK_1")
            target = self._record(broker_order_no=None)
            path = self._write_queue(tmpdir, root={"version": 1, "orders": [other, target]})

            result = self._record_event(path)

            self.assertFalse(result["recorded"])
            self.assertIn("broker_order_no already belongs to another queue record", result["blocked_reasons"])

    def test_inspect_broker_chejan_lifecycle_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)
            self._record_event(path)
            before = self._sha256(path)

            result = inspect_broker_chejan_lifecycle(path, self._review())

            self.assertTrue(result["inspection_ok"])
            self.assertEqual("PARTIALLY_FILLED", result["status"])
            self.assertEqual(1, result["chejan_event_count"])
            self.assertFalse(result["queue_write"])
            self.assertFalse(result["file_write"])
            self.assertEqual(before, self._sha256(path))

    def test_restart_reconciliation_consistent_queue_and_fills(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(
                tmpdir,
                record=self._record(
                    status="FILLED",
                    original_order_quantity=3,
                    cumulative_filled_quantity=3,
                    total_filled_quantity=3,
                    remaining_quantity=0,
                    fill_count=1,
                    average_fill_price=1000,
                ),
            )
            fills_path = self._write_fills(tmpdir, [self._fill()])
            before_queue = self._sha256(path)
            before_fills = self._sha256(fills_path)

            result = inspect_incomplete_order_reconciliation(path, self._review(), fills_path=fills_path)

            self.assertTrue(result["inspection_ok"])
            self.assertEqual("CONSISTENT", result["reconciliation_candidate_status"])
            self.assertFalse(result["queue_fills_mismatch"])
            self.assertFalse(result["write_performed"])
            self.assertEqual(before_queue, self._sha256(path))
            self.assertEqual(before_fills, self._sha256(fills_path))

    def test_restart_reconciliation_partially_filled_clean_state_requires_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(
                tmpdir,
                record=self._record(
                    status="PARTIALLY_FILLED",
                    original_order_quantity=10,
                    cumulative_filled_quantity=3,
                    total_filled_quantity=3,
                    remaining_quantity=7,
                    fill_count=1,
                    average_fill_price=1000,
                ),
            )
            fills_path = self._write_fills(tmpdir, [self._fill()])

            result = inspect_incomplete_order_reconciliation(path, self._review(), fills_path=fills_path)

            self.assertEqual("REVIEW_REQUIRED", result["reconciliation_candidate_status"])
            self.assertFalse(result["queue_fills_mismatch"])

    def test_restart_reconciliation_detects_quantity_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(
                tmpdir,
                record=self._record(
                    status="PARTIALLY_FILLED",
                    original_order_quantity=10,
                    cumulative_filled_quantity=5,
                    remaining_quantity=5,
                    fill_count=1,
                    average_fill_price=1000,
                ),
            )
            fills_path = self._write_fills(tmpdir, [self._fill(filled_quantity=3, remaining_quantity=7)])

            result = inspect_incomplete_order_reconciliation(path, self._review(), fills_path=fills_path)

            self.assertEqual("REVIEW_REQUIRED", result["reconciliation_candidate_status"])
            self.assertTrue(result["queue_fills_mismatch"])
            self.assertIn("queue cumulative filled quantity does not match fills ledger sum", result["blocked_reasons"])

    def test_restart_reconciliation_uses_cumulative_fill_delta_quantity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(
                tmpdir,
                record=self._record(
                    status="PARTIALLY_FILLED",
                    original_order_quantity=10,
                    cumulative_filled_quantity=5,
                    remaining_quantity=5,
                    fill_count=2,
                    average_fill_price=1040,
                ),
            )
            fills_path = self._write_fills(
                tmpdir,
                [
                    self._fill(fill_id="FILL_1", execution_no="1", filled_quantity=3, filled_price=1000, remaining_quantity=7),
                    self._fill(fill_id="FILL_2", execution_no="2", filled_quantity=5, filled_price=1100, remaining_quantity=5),
                ],
            )

            result = inspect_incomplete_order_reconciliation(path, self._review(), fills_path=fills_path)

            self.assertEqual(5, result["fills_summed_quantity"])
            self.assertEqual(5, result["fills_delta_quantity"])
            self.assertEqual(1040, result["fills_weighted_average_price"])
            self.assertFalse(result["queue_fills_mismatch"])

    def test_restart_reconciliation_three_cumulative_events_use_deltas(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(
                tmpdir,
                record=self._record(
                    status="PARTIALLY_FILLED",
                    original_order_quantity=10,
                    cumulative_filled_quantity=7,
                    remaining_quantity=3,
                    fill_count=3,
                    average_fill_price=1142.857142857143,
                ),
            )
            fills_path = self._write_fills(
                tmpdir,
                [
                    self._fill(fill_id="FILL_1", execution_no="1", filled_quantity=2, filled_price=1000, remaining_quantity=8),
                    self._fill(fill_id="FILL_2", execution_no="2", filled_quantity=4, filled_price=1200, remaining_quantity=6),
                    self._fill(fill_id="FILL_3", execution_no="3", filled_quantity=7, filled_price=1200, remaining_quantity=3),
                ],
            )

            result = inspect_incomplete_order_reconciliation(path, self._review(), fills_path=fills_path)

            self.assertEqual(7, result["fills_summed_quantity"])
            self.assertEqual(7, result["fills_delta_quantity"])
            self.assertEqual(1142.857142857143, result["fills_weighted_average_price"])

    def test_restart_reconciliation_out_of_order_cumulative_fill_does_not_decrease_quantity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(
                tmpdir,
                record=self._record(
                    status="PARTIALLY_FILLED",
                    original_order_quantity=10,
                    cumulative_filled_quantity=5,
                    remaining_quantity=5,
                    fill_count=2,
                    average_fill_price=1000,
                ),
            )
            fills_path = self._write_fills(
                tmpdir,
                [
                    self._fill(fill_id="FILL_1", execution_no="1", filled_quantity=5, filled_price=1000, remaining_quantity=5),
                    self._fill(fill_id="FILL_2", execution_no="2", filled_quantity=3, filled_price=900, remaining_quantity=7),
                ],
            )

            result = inspect_incomplete_order_reconciliation(path, self._review(), fills_path=fills_path)

            self.assertEqual(5, result["fills_summed_quantity"])
            self.assertIn("execution_no:2", result["out_of_order_fill_identities"])
            self.assertIn("out-of-order cumulative fill quantity in fills ledger", result["blocked_reasons"])

    def test_restart_reconciliation_repeated_cumulative_event_does_not_duplicate_quantity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(
                tmpdir,
                record=self._record(
                    status="PARTIALLY_FILLED",
                    original_order_quantity=10,
                    cumulative_filled_quantity=3,
                    remaining_quantity=7,
                    fill_count=1,
                    average_fill_price=1000,
                ),
            )
            fills_path = self._write_fills(
                tmpdir,
                [
                    self._fill(fill_id="FILL_1", execution_no="1", filled_quantity=3, filled_price=1000, remaining_quantity=7),
                    self._fill(fill_id="FILL_2", execution_no="2", filled_quantity=3, filled_price=1200, remaining_quantity=7),
                ],
            )

            result = inspect_incomplete_order_reconciliation(path, self._review(), fills_path=fills_path)

            self.assertEqual(3, result["fills_summed_quantity"])
            self.assertEqual(3, result["fills_delta_quantity"])
            self.assertEqual(1, result["fills_effective_count"])
            self.assertIn("execution_no:2", result["repeated_fill_identities"])
            self.assertFalse(result["queue_fills_mismatch"])

    def test_restart_reconciliation_detects_weighted_average_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(
                tmpdir,
                record=self._record(
                    status="PARTIALLY_FILLED",
                    original_order_quantity=10,
                    cumulative_filled_quantity=3,
                    remaining_quantity=7,
                    fill_count=1,
                    average_fill_price=1200,
                ),
            )
            fills_path = self._write_fills(tmpdir, [self._fill(filled_quantity=3, filled_price=1000)])

            result = inspect_incomplete_order_reconciliation(path, self._review(), fills_path=fills_path)

            self.assertEqual("REVIEW_REQUIRED", result["reconciliation_candidate_status"])
            self.assertIn("queue average_fill_price does not match fills weighted average", result["blocked_reasons"])

    def test_restart_reconciliation_detects_duplicate_fill_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(
                tmpdir,
                record=self._record(status="PARTIALLY_FILLED", original_order_quantity=10, cumulative_filled_quantity=6, remaining_quantity=4, fill_count=2, average_fill_price=1000),
            )
            fills_path = self._write_fills(tmpdir, [self._fill(fill_id="FILL_1"), self._fill(fill_id="FILL_2")])

            result = inspect_incomplete_order_reconciliation(path, self._review(), fills_path=fills_path)

            self.assertEqual("BLOCKED", result["reconciliation_candidate_status"])
            self.assertIn("execution_no:EXECUTION_909_1", result["duplicate_execution_identities"])

    def test_restart_reconciliation_missing_fills_file_requires_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir, record=self._record(status="BROKER_ACCEPTED"))

            result = inspect_incomplete_order_reconciliation(path, self._review(), fills_path=Path(tmpdir) / "missing_fills.json")

            self.assertEqual("REVIEW_REQUIRED", result["reconciliation_candidate_status"])
            self.assertIn("fills file does not exist", result["warnings"])

    def test_restart_reconciliation_invalid_fills_file_requires_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir, record=self._record(status="BROKER_ACCEPTED"))
            fills_path = Path(tmpdir) / "fills.json"
            fills_path.write_text("{bad json", encoding="utf-8")

            result = inspect_incomplete_order_reconciliation(path, self._review(), fills_path=fills_path)

            self.assertEqual("REVIEW_REQUIRED", result["reconciliation_candidate_status"])
            self.assertTrue(any("failed to read fills json" in warning for warning in result["warnings"]))

    def test_restart_reconciliation_does_not_match_fill_by_broker_order_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(
                tmpdir,
                record=self._record(
                    status="BROKER_ACCEPTED",
                    broker_order_no=None,
                    order_id="ORDER_1",
                    execution_id="EXEC_1",
                    request_hash="HASH_1",
                    lock_id="LOCK_1",
                ),
            )
            unrelated_fill = self._fill(
                order_id="",
                order_queued_id="",
                execution_id="",
                request_hash="",
                lock_id="",
                broker_order_no="BRK_1",
            )
            fills_path = self._write_fills(tmpdir, [unrelated_fill])

            result = inspect_incomplete_order_reconciliation(path, self._review(broker_order_no=""), fills_path=fills_path)

            self.assertEqual(0, result["fills_ledger_count"])
            self.assertEqual(0, result["fills_summed_quantity"])

    def test_restart_reconciliation_send_call_in_progress_with_accept_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            record = self._record(status="SEND_CALL_IN_PROGRESS", chejan_events=[{"event_type": "ORDER_ACCEPTED", "broker_order_no": "BRK_1"}])
            path = self._write_queue(tmpdir, record=record)
            fills_path = self._write_fills(tmpdir)

            result = inspect_incomplete_order_reconciliation(path, self._review(), fills_path=fills_path)

            self.assertEqual("RECONCILIATION_CANDIDATE", result["reconciliation_candidate_status"])
            self.assertTrue(result["manual_reconciliation_required"])
            self.assertFalse(result["automatic_retry_allowed"])

    def test_restart_reconciliation_send_uncertain_with_rejection_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            record = self._record(status="SEND_UNCERTAIN", chejan_events=[{"event_type": "ORDER_REJECTED", "broker_order_no": "BRK_1"}])
            path = self._write_queue(tmpdir, record=record)
            fills_path = self._write_fills(tmpdir)

            result = inspect_incomplete_order_reconciliation(path, self._review(), fills_path=fills_path)
            preview = build_order_reconciliation_preview(path, self._review(), fills_path=fills_path)

            self.assertEqual("RECONCILIATION_CANDIDATE", result["reconciliation_candidate_status"])
            self.assertEqual("BROKER_REJECTED", preview["proposed_status"])
            self.assertFalse(preview["write_performed"])

    def test_restart_reconciliation_filled_remaining_contradiction(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(
                tmpdir,
                record=self._record(status="FILLED", original_order_quantity=10, cumulative_filled_quantity=10, remaining_quantity=1, fill_count=1, average_fill_price=1000),
            )
            fills_path = self._write_fills(tmpdir, [self._fill(filled_quantity=10, remaining_quantity=0)])

            result = inspect_incomplete_order_reconciliation(path, self._review(), fills_path=fills_path)

            self.assertEqual("REVIEW_REQUIRED", result["reconciliation_candidate_status"])
            self.assertIn("queue FILLED still has remaining quantity", result["blocked_reasons"])

    def test_restart_reconciliation_partially_filled_zero_remaining_contradiction(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(
                tmpdir,
                record=self._record(status="PARTIALLY_FILLED", original_order_quantity=10, cumulative_filled_quantity=10, remaining_quantity=0, fill_count=1, average_fill_price=1000),
            )
            fills_path = self._write_fills(tmpdir, [self._fill(filled_quantity=10, remaining_quantity=0)])

            result = inspect_incomplete_order_reconciliation(path, self._review(), fills_path=fills_path)

            self.assertEqual("REVIEW_REQUIRED", result["reconciliation_candidate_status"])
            self.assertIn("queue PARTIALLY_FILLED has zero remaining quantity", result["blocked_reasons"])

    def test_restart_reconciliation_cancel_with_late_fill_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(
                tmpdir,
                record=self._record(status="CANCELLED", original_order_quantity=10, cumulative_filled_quantity=0, remaining_quantity=0, fill_count=1, average_fill_price=1000),
            )
            fills_path = self._write_fills(tmpdir, [self._fill()])

            result = inspect_incomplete_order_reconciliation(path, self._review(), fills_path=fills_path)

            self.assertEqual("BLOCKED", result["reconciliation_candidate_status"])
            self.assertIn("late fill evidence exists after cancelled state", result["blocked_reasons"])

    def test_reconciliation_preview_uses_expected_revision_and_snapshot_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = {"version": 1, "revision": 7, "updated_at": "2026-07-04", "orders": [self._record(status="SEND_UNCERTAIN", chejan_events=[{"event_type": "ORDER_ACCEPTED"}])]}
            path = self._write_queue(tmpdir, root=root)
            fills_path = self._write_fills(tmpdir)

            preview = build_order_reconciliation_preview(path, self._review(), fills_path=fills_path)

            self.assertTrue(preview["preview_ready"])
            self.assertEqual(7, preview["expected_revision"])
            self.assertTrue(preview["snapshot_hash"])
            self.assertTrue(preview["approval_required"])
            self.assertFalse(preview["queue_write"])


if __name__ == "__main__":
    unittest.main()
