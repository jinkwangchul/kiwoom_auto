# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import chejan_event_recorder
from chejan_event_recorder import record_chejan_event


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
            "status": "ORDER_QUEUED",
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

    def test_status_send_order_and_result_status_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir)

            self._record_event(path)
            record = self._read_queue(path)["orders"][0]

            self.assertEqual("ORDER_QUEUED", record["status"])
            self.assertTrue(record["send_order_called"])
            self.assertEqual("SEND_ORDER_CALLED", record["send_order_result_status"])

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

    def test_target_status_must_remain_order_queued(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_queue(tmpdir, record=self._record(status="OTHER"))

            result = self._record_event(path)

            self.assertFalse(result["recorded"])
            self.assertIn("target record.status is not ORDER_QUEUED", result["blocked_reasons"])


if __name__ == "__main__":
    unittest.main()
