# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from execution_queue_writer import (
    commit_execution_queue_write,
    commit_execution_queue_write_batch,
    preview_execution_queue_write,
)


class ExecutionQueueWriterPreviewTest(unittest.TestCase):
    def _queue_pending_result(self) -> dict:
        return {
            "queue_pending": True,
            "queue_pending_stage": "queue_pending_created",
            "queue_pending_id": "QUEUE_PENDING_EXEC_CANDIDATE_ORDER_1",
            "created_from_candidate_id": "EXEC_CANDIDATE_ORDER_1",
            "queue_contract_version": "preview-1",
            "next_stage": "QUEUE_WRITER_REQUIRED",
            "preview_only": True,
            "no_write": True,
            "blocked_reasons": [],
            "warnings": [],
            "order_id": "ORDER_1",
            "source_signal_id": "SIG_1",
            "request_hash_preview": "a" * 64,
            "lock_preview": {
                "ok": True,
                "lock_id": "LOCK_PREVIEW_ORDER_1_003550_BUY_SIG_1",
            },
            "execution_request_preview": {
                "ok": True,
                "execution_request": {
                    "execution_id": "EXEC_PREVIEW_ORDER_1",
                    "order_id": "ORDER_1",
                    "source_signal_id": "SIG_1",
                    "lock_id": "LOCK_PREVIEW_ORDER_1_003550_BUY_SIG_1",
                    "request_hash": "a" * 64,
                },
            },
        }

    def _write_preview_result(self) -> dict:
        return preview_execution_queue_write(self._queue_pending_result())

    def _write_preview_for(
        self,
        *,
        order_id: str,
        candidate_id: str,
        queue_pending_id: str,
        request_hash: str,
        lock_id: str,
        execution_id: str,
    ) -> dict:
        pending = self._queue_pending_result()
        pending["order_id"] = order_id
        pending["created_from_candidate_id"] = candidate_id
        pending["queue_pending_id"] = queue_pending_id
        pending["request_hash_preview"] = request_hash
        pending["lock_preview"]["lock_id"] = lock_id
        pending["execution_request_preview"]["execution_request"]["order_id"] = order_id
        pending["execution_request_preview"]["execution_request"]["execution_id"] = execution_id
        pending["execution_request_preview"]["execution_request"]["request_hash"] = request_hash
        pending["execution_request_preview"]["execution_request"]["lock_id"] = lock_id
        return preview_execution_queue_write(pending)

    def _context(self) -> dict:
        return {"manual_queue_write_confirmed": True}

    def _write_queue(self, path: Path, orders: list[dict] | None = None) -> None:
        path.write_text(
            json.dumps({"version": 1, "updated_at": "before", "orders": orders or []}, indent=2),
            encoding="utf-8",
        )

    def _commit_to_temp_queue(
        self,
        write_preview: dict | None = None,
        *,
        backup: bool = True,
        context: dict | None = None,
        orders: list[dict] | None = None,
    ) -> tuple[dict, Path]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        self._write_queue(queue_path, orders)
        result = commit_execution_queue_write(
            write_preview or self._write_preview_result(),
            queue_path,
            backup=backup,
            context=self._context() if context is None else context,
        )
        return result, queue_path

    def test_queue_pending_false_is_blocked(self) -> None:
        queue_pending = self._queue_pending_result()
        queue_pending["queue_pending"] = False

        result = preview_execution_queue_write(queue_pending)

        self.assertFalse(result["write_preview"])
        self.assertEqual("queue_pending", result["write_stage"])
        self.assertEqual("BLOCKED", result["next_stage"])
        self.assertTrue(result["preview_only"])
        self.assertTrue(result["no_write"])
        self.assertIsNone(result["order_queued_record_preview"])
        self.assertIn("queue_pending_result.queue_pending is not true", result["blocked_reasons"])

    def test_queue_pending_stage_mismatch_is_blocked(self) -> None:
        queue_pending = self._queue_pending_result()
        queue_pending["queue_pending_stage"] = "candidate"

        result = preview_execution_queue_write(queue_pending)

        self.assertFalse(result["write_preview"])
        self.assertIn("queue_pending_stage is not queue_pending_created", result["blocked_reasons"])

    def test_next_stage_mismatch_is_blocked(self) -> None:
        queue_pending = self._queue_pending_result()
        queue_pending["next_stage"] = "BLOCKED"

        result = preview_execution_queue_write(queue_pending)

        self.assertFalse(result["write_preview"])
        self.assertIn(
            "queue_pending_result.next_stage is not QUEUE_WRITER_REQUIRED",
            result["blocked_reasons"],
        )

    def test_preview_only_false_is_blocked(self) -> None:
        queue_pending = self._queue_pending_result()
        queue_pending["preview_only"] = False

        result = preview_execution_queue_write(queue_pending)

        self.assertFalse(result["write_preview"])
        self.assertIn("queue_pending_result.preview_only is not true", result["blocked_reasons"])

    def test_no_write_false_is_blocked(self) -> None:
        queue_pending = self._queue_pending_result()
        queue_pending["no_write"] = False

        result = preview_execution_queue_write(queue_pending)

        self.assertFalse(result["write_preview"])
        self.assertIn("queue_pending_result.no_write is not true", result["blocked_reasons"])

    def test_required_top_level_fields_missing_are_blocked(self) -> None:
        cases = [
            ("queue_pending_id", "queue_pending_id is required"),
            ("created_from_candidate_id", "created_from_candidate_id is required"),
            ("order_id", "order_id is required"),
            ("source_signal_id", "source_signal_id is required"),
            ("request_hash_preview", "request_hash_preview is required"),
        ]

        for field, reason in cases:
            with self.subTest(field=field):
                queue_pending = self._queue_pending_result()
                queue_pending[field] = ""

                result = preview_execution_queue_write(queue_pending)

                self.assertFalse(result["write_preview"])
                self.assertIn(reason, result["blocked_reasons"])

    def test_lock_preview_lock_id_missing_is_blocked(self) -> None:
        queue_pending = self._queue_pending_result()
        queue_pending["lock_preview"]["lock_id"] = ""

        result = preview_execution_queue_write(queue_pending)

        self.assertFalse(result["write_preview"])
        self.assertIn("lock_preview.lock_id is required", result["blocked_reasons"])

    def test_execution_request_required_fields_missing_are_blocked(self) -> None:
        cases = [
            ("execution_id", "execution_request.execution_id is required"),
            ("request_hash", "execution_request.request_hash is required"),
            ("lock_id", "execution_request.lock_id is required"),
        ]

        for field, reason in cases:
            with self.subTest(field=field):
                queue_pending = self._queue_pending_result()
                queue_pending["execution_request_preview"]["execution_request"][field] = ""

                result = preview_execution_queue_write(queue_pending)

                self.assertFalse(result["write_preview"])
                self.assertIn(reason, result["blocked_reasons"])

    def test_execution_request_missing_is_blocked(self) -> None:
        queue_pending = self._queue_pending_result()
        queue_pending["execution_request_preview"].pop("execution_request")

        result = preview_execution_queue_write(queue_pending)

        self.assertFalse(result["write_preview"])
        self.assertIn(
            "execution_request_preview.execution_request is required",
            result["blocked_reasons"],
        )

    def test_all_conditions_met_creates_order_queued_record_preview(self) -> None:
        result = preview_execution_queue_write(self._queue_pending_result())
        record = result["order_queued_record_preview"]

        self.assertTrue(result["write_preview"])
        self.assertEqual("order_queued_record_preview_created", result["write_stage"])
        self.assertEqual("QUEUE_WRITE_REQUIRED", result["next_stage"])
        self.assertTrue(result["preview_only"])
        self.assertTrue(result["no_write"])
        self.assertEqual([], result["blocked_reasons"])
        self.assertEqual("ORDER_QUEUED_ORDER_1", record["id"])
        self.assertEqual("ORDER_QUEUED", record["status"])
        self.assertEqual("execution_queue_pending", record["source"])
        self.assertEqual("SIG_1", record["source_signal_id"])
        self.assertEqual("ORDER_1", record["order_id"])
        self.assertEqual("EXEC_CANDIDATE_ORDER_1", record["candidate_id"])
        self.assertEqual("QUEUE_PENDING_EXEC_CANDIDATE_ORDER_1", record["queue_pending_id"])
        self.assertEqual("a" * 64, record["request_hash"])
        self.assertEqual("LOCK_PREVIEW_ORDER_1_003550_BUY_SIG_1", record["lock_id"])
        self.assertEqual("EXEC_PREVIEW_ORDER_1", record["execution_id"])
        self.assertEqual("preview-1", record["queue_contract_version"])

    def test_record_preview_keeps_no_write_execution_disabled_and_send_order_false(self) -> None:
        result = preview_execution_queue_write(self._queue_pending_result())
        record = result["order_queued_record_preview"]

        self.assertTrue(result["no_write"])
        self.assertFalse(record["send_order_called"])
        self.assertFalse(record["execution_enabled"])
        self.assertEqual([], record["blocked_reasons"])

    def test_duplicate_request_hash_is_blocked(self) -> None:
        existing_orders = [
            {
                "request_hash": "a" * 64,
                "lock_id": "OTHER_LOCK",
                "order_id": "OTHER_ORDER",
            }
        ]

        result = preview_execution_queue_write(self._queue_pending_result(), existing_orders)

        self.assertFalse(result["write_preview"])
        self.assertEqual("duplicate", result["write_stage"])
        self.assertIn("duplicate request_hash", result["blocked_reasons"])

    def test_duplicate_lock_id_is_blocked(self) -> None:
        existing_orders = [
            {
                "request_hash": "b" * 64,
                "lock_id": "LOCK_PREVIEW_ORDER_1_003550_BUY_SIG_1",
                "order_id": "OTHER_ORDER",
            }
        ]

        result = preview_execution_queue_write(self._queue_pending_result(), existing_orders)

        self.assertFalse(result["write_preview"])
        self.assertIn("duplicate lock_id", result["blocked_reasons"])

    def test_duplicate_order_id_is_blocked(self) -> None:
        existing_orders = [
            {
                "request_hash": "b" * 64,
                "lock_id": "OTHER_LOCK",
                "order_id": "ORDER_1",
            }
        ]

        result = preview_execution_queue_write(self._queue_pending_result(), existing_orders)

        self.assertFalse(result["write_preview"])
        self.assertIn("duplicate order_id", result["blocked_reasons"])

    def test_duplicate_priority_is_request_hash_then_lock_id_then_order_id(self) -> None:
        queue_pending = self._queue_pending_result()

        request_hash_duplicate = preview_execution_queue_write(
            queue_pending,
            [
                {
                    "request_hash": "a" * 64,
                    "lock_id": "LOCK_PREVIEW_ORDER_1_003550_BUY_SIG_1",
                    "order_id": "ORDER_1",
                }
            ],
        )
        lock_id_duplicate = preview_execution_queue_write(
            queue_pending,
            [
                {
                    "request_hash": "b" * 64,
                    "lock_id": "LOCK_PREVIEW_ORDER_1_003550_BUY_SIG_1",
                    "order_id": "ORDER_1",
                }
            ],
        )
        order_id_duplicate = preview_execution_queue_write(
            queue_pending,
            [
                {
                    "request_hash": "b" * 64,
                    "lock_id": "OTHER_LOCK",
                    "order_id": "ORDER_1",
                }
            ],
        )

        self.assertIn("duplicate request_hash", request_hash_duplicate["blocked_reasons"])
        self.assertIn("duplicate lock_id", lock_id_duplicate["blocked_reasons"])
        self.assertIn("duplicate order_id", order_id_duplicate["blocked_reasons"])

    def test_success_does_not_call_send_order_or_write_runtime(self) -> None:
        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
        ):
            result = preview_execution_queue_write(self._queue_pending_result())

        self.assertTrue(result["write_preview"])
        write_text.assert_not_called()
        open_mock.assert_not_called()
        send_order_stub.assert_not_called()

    def test_input_dict_and_existing_orders_are_not_mutated(self) -> None:
        queue_pending = self._queue_pending_result()
        existing_orders = [{"request_hash": "b" * 64, "lock_id": "LOCK_2", "order_id": "ORDER_2"}]
        context = {"note": "dry-run only"}
        original_queue_pending = deepcopy(queue_pending)
        original_existing_orders = deepcopy(existing_orders)
        original_context = deepcopy(context)

        preview_execution_queue_write(queue_pending, existing_orders, context)

        self.assertEqual(original_queue_pending, queue_pending)
        self.assertEqual(original_existing_orders, existing_orders)
        self.assertEqual(original_context, context)

    def test_commit_without_manual_confirmation_is_blocked(self) -> None:
        result, _ = self._commit_to_temp_queue(context={})

        self.assertFalse(result["committed"])
        self.assertEqual("manual_confirm", result["write_stage"])
        self.assertIn("manual queue write confirmation is required", result["blocked_reasons"])

    def test_commit_invalid_preview_is_blocked(self) -> None:
        write_preview = self._write_preview_result()
        write_preview["write_preview"] = False

        result, _ = self._commit_to_temp_queue(write_preview)

        self.assertFalse(result["committed"])
        self.assertEqual("write_preview", result["write_stage"])
        self.assertIn(
            "queue_write_preview_result.write_preview is not true",
            result["blocked_reasons"],
        )

    def test_commit_record_required_field_missing_is_blocked(self) -> None:
        required_fields = [
            "id",
            "source",
            "source_signal_id",
            "order_id",
            "candidate_id",
            "queue_pending_id",
            "request_hash",
            "lock_id",
            "execution_id",
            "queue_contract_version",
        ]

        for field in required_fields:
            with self.subTest(field=field):
                write_preview = self._write_preview_result()
                write_preview["order_queued_record_preview"][field] = ""

                result, _ = self._commit_to_temp_queue(write_preview)

                self.assertFalse(result["committed"])
                self.assertIn(f"record.{field} is required", result["blocked_reasons"])

    def test_commit_record_execution_request_missing_is_blocked(self) -> None:
        write_preview = self._write_preview_result()
        write_preview["order_queued_record_preview"].pop("execution_request")

        result, _ = self._commit_to_temp_queue(write_preview)

        self.assertFalse(result["committed"])
        self.assertIn("record.execution_request is required", result["blocked_reasons"])

    def test_commit_queue_file_missing_is_blocked(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "missing_order_queue.json"

        result = commit_execution_queue_write(
            self._write_preview_result(),
            queue_path,
            context=self._context(),
        )

        self.assertFalse(result["committed"])
        self.assertEqual("read_queue", result["write_stage"])
        self.assertIn("queue file does not exist", result["blocked_reasons"])

    def test_commit_corrupt_json_is_blocked(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        queue_path.write_text("{not json", encoding="utf-8")

        result = commit_execution_queue_write(
            self._write_preview_result(),
            queue_path,
            context=self._context(),
        )

        self.assertFalse(result["committed"])
        self.assertEqual("read_queue", result["write_stage"])
        self.assertTrue(result["blocked_reasons"][0].startswith("failed to read order_queue json"))

    def test_commit_root_non_dict_is_blocked(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        queue_path.write_text("[]", encoding="utf-8")

        result = commit_execution_queue_write(
            self._write_preview_result(),
            queue_path,
            context=self._context(),
        )

        self.assertFalse(result["committed"])
        self.assertIn("order_queue root must be an object", result["blocked_reasons"])

    def test_commit_orders_non_list_is_blocked(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        queue_path.write_text(json.dumps({"version": 1, "orders": {}}), encoding="utf-8")

        result = commit_execution_queue_write(
            self._write_preview_result(),
            queue_path,
            context=self._context(),
        )

        self.assertFalse(result["committed"])
        self.assertIn("order_queue orders must be a list", result["blocked_reasons"])

    def test_commit_orders_item_non_dict_is_blocked(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        queue_path.write_text(json.dumps({"version": 1, "orders": ["bad"]}), encoding="utf-8")

        result = commit_execution_queue_write(
            self._write_preview_result(),
            queue_path,
            context=self._context(),
        )

        self.assertFalse(result["committed"])
        self.assertIn("order_queue orders must contain only objects", result["blocked_reasons"])

    def test_commit_duplicate_request_hash_is_blocked(self) -> None:
        result, _ = self._commit_to_temp_queue(
            orders=[{"request_hash": "a" * 64, "lock_id": "OTHER", "order_id": "OTHER"}]
        )

        self.assertFalse(result["committed"])
        self.assertEqual("duplicate", result["write_stage"])
        self.assertIn("duplicate request_hash", result["blocked_reasons"])

    def test_commit_duplicate_lock_id_is_blocked(self) -> None:
        result, _ = self._commit_to_temp_queue(
            orders=[
                {
                    "request_hash": "b" * 64,
                    "lock_id": "LOCK_PREVIEW_ORDER_1_003550_BUY_SIG_1",
                    "order_id": "OTHER",
                }
            ]
        )

        self.assertFalse(result["committed"])
        self.assertIn("duplicate lock_id", result["blocked_reasons"])

    def test_commit_duplicate_order_id_is_blocked(self) -> None:
        result, _ = self._commit_to_temp_queue(
            orders=[{"request_hash": "b" * 64, "lock_id": "OTHER", "order_id": "ORDER_1"}]
        )

        self.assertFalse(result["committed"])
        self.assertIn("duplicate order_id", result["blocked_reasons"])

    def test_commit_success_appends_record_to_temp_queue(self) -> None:
        result, queue_path = self._commit_to_temp_queue()
        data = json.loads(queue_path.read_text(encoding="utf-8"))

        self.assertTrue(result["committed"])
        self.assertEqual("order_queued_record_committed", result["write_stage"])
        self.assertEqual("QUEUE_COMMITTED_REVIEW_REQUIRED", result["next_stage"])
        self.assertTrue(result["changed"])
        self.assertEqual(str(queue_path), result["order_queue_path"])
        self.assertEqual("ORDER_1", result["order_id"])
        self.assertEqual("ORDER_QUEUED_ORDER_1", result["order_queued_id"])
        self.assertEqual("ORDER_QUEUED", result["status"])
        self.assertEqual(1, len(data["orders"]))
        self.assertEqual("ORDER_QUEUED_ORDER_1", data["orders"][0]["id"])

    def test_commit_creates_backup_file(self) -> None:
        result, _ = self._commit_to_temp_queue()

        self.assertTrue(result["committed"])
        self.assertTrue(result["backup_path"])
        self.assertTrue(Path(result["backup_path"]).exists())

    def test_commit_backup_false_does_not_report_backup_path(self) -> None:
        result, _ = self._commit_to_temp_queue(backup=False)

        self.assertTrue(result["committed"])
        self.assertIsNone(result["backup_path"])

    def test_commit_preserves_send_order_called_false_and_execution_enabled_false(self) -> None:
        result, queue_path = self._commit_to_temp_queue()
        record = json.loads(queue_path.read_text(encoding="utf-8"))["orders"][0]

        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["execution_enabled"])
        self.assertFalse(record["send_order_called"])
        self.assertFalse(record["execution_enabled"])

    def test_commit_backup_failure_is_blocked(self) -> None:
        with mock.patch("execution_queue_writer.shutil.copy2", side_effect=OSError("backup denied")):
            result, _ = self._commit_to_temp_queue()

        self.assertFalse(result["committed"])
        self.assertEqual("backup", result["write_stage"])
        self.assertTrue(result["blocked_reasons"][0].startswith("failed to create backup"))

    def test_commit_input_dict_is_not_mutated(self) -> None:
        write_preview = self._write_preview_result()
        context = self._context()
        original_preview = deepcopy(write_preview)
        original_context = deepcopy(context)

        self._commit_to_temp_queue(write_preview, context=context)

        self.assertEqual(original_preview, write_preview)
        self.assertEqual(original_context, context)

    def test_commit_does_not_modify_project_runtime_order_queue(self) -> None:
        runtime_queue = Path(__file__).resolve().parents[1] / "runtime" / "order_queue.json"
        before = hashlib.sha256(runtime_queue.read_bytes()).hexdigest()

        self._commit_to_temp_queue()

        after = hashlib.sha256(runtime_queue.read_bytes()).hexdigest()
        self.assertEqual(before, after)

    def test_commit_success_does_not_call_send_order(self) -> None:
        with mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub:
            result, _ = self._commit_to_temp_queue()

        self.assertTrue(result["committed"])
        send_order_stub.assert_not_called()

    def test_batch_commit_appends_records_atomically_and_preserves_order(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        self._write_queue(queue_path)
        previews = [
            self._write_preview_for(
                order_id="ORDER_1",
                candidate_id="CANDIDATE_1",
                queue_pending_id="PENDING_1",
                request_hash="a" * 64,
                lock_id="LOCK_1",
                execution_id="EXEC_1",
            ),
            self._write_preview_for(
                order_id="ORDER_2",
                candidate_id="CANDIDATE_2",
                queue_pending_id="PENDING_2",
                request_hash="b" * 64,
                lock_id="LOCK_2",
                execution_id="EXEC_2",
            ),
        ]

        result = commit_execution_queue_write_batch(previews, queue_path, context=self._context())

        self.assertTrue(result["committed"])
        self.assertEqual(2, result["committed_count"])
        self.assertEqual(["ORDER_1", "ORDER_2"], result["order_ids"])
        self.assertTrue(result["backup_path"])
        self.assertTrue(Path(result["backup_path"]).exists())
        data = json.loads(queue_path.read_text(encoding="utf-8"))
        self.assertEqual(["ORDER_1", "ORDER_2"], [record["order_id"] for record in data["orders"]])

    def test_batch_commit_duplicate_identity_is_blocked_before_write(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        self._write_queue(queue_path)
        previews = [
            self._write_preview_for(
                order_id="ORDER_1",
                candidate_id="CANDIDATE_1",
                queue_pending_id="PENDING_1",
                request_hash="a" * 64,
                lock_id="LOCK_1",
                execution_id="EXEC_1",
            ),
            self._write_preview_for(
                order_id="ORDER_2",
                candidate_id="CANDIDATE_2",
                queue_pending_id="PENDING_2",
                request_hash="a" * 64,
                lock_id="LOCK_2",
                execution_id="EXEC_2",
            ),
        ]

        result = commit_execution_queue_write_batch(previews, queue_path, context=self._context())

        self.assertFalse(result["committed"])
        self.assertIn("duplicate request_hash", result["blocked_reasons"])
        data = json.loads(queue_path.read_text(encoding="utf-8"))
        self.assertEqual([], data["orders"])

    def test_batch_commit_existing_queue_duplicate_is_blocked_before_write(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        self._write_queue(queue_path, orders=[{"order_id": "ORDER_1", "request_hash": "z" * 64, "lock_id": "OTHER"}])
        preview = self._write_preview_for(
            order_id="ORDER_1",
            candidate_id="CANDIDATE_1",
            queue_pending_id="PENDING_1",
            request_hash="a" * 64,
            lock_id="LOCK_1",
            execution_id="EXEC_1",
        )

        result = commit_execution_queue_write_batch([preview], queue_path, context=self._context())

        self.assertFalse(result["committed"])
        self.assertIn("duplicate order_id", result["blocked_reasons"])
        data = json.loads(queue_path.read_text(encoding="utf-8"))
        self.assertEqual(1, len(data["orders"]))

    def test_batch_commit_existing_queue_candidate_id_duplicate_is_blocked_before_write(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        self._write_queue(
            queue_path,
            orders=[{"order_id": "OTHER", "candidate_id": "CANDIDATE_1", "request_hash": "z" * 64, "lock_id": "OTHER"}],
        )
        preview = self._write_preview_for(
            order_id="ORDER_1",
            candidate_id="CANDIDATE_1",
            queue_pending_id="PENDING_1",
            request_hash="a" * 64,
            lock_id="LOCK_1",
            execution_id="EXEC_1",
        )

        result = commit_execution_queue_write_batch([preview], queue_path, context=self._context())

        self.assertFalse(result["committed"])
        self.assertEqual(0, result.get("committed_count", 0))
        self.assertIn("duplicate candidate_id", result["blocked_reasons"])
        self.assertFalse(result.get("queue_write", False))
        self.assertFalse(result.get("queue_committed", False))
        data = json.loads(queue_path.read_text(encoding="utf-8"))
        self.assertEqual(1, len(data["orders"]))

    def test_batch_commit_existing_queue_pending_id_duplicate_is_blocked_before_write(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        self._write_queue(
            queue_path,
            orders=[{"order_id": "OTHER", "queue_pending_id": "PENDING_1", "request_hash": "z" * 64, "lock_id": "OTHER"}],
        )
        preview = self._write_preview_for(
            order_id="ORDER_1",
            candidate_id="CANDIDATE_1",
            queue_pending_id="PENDING_1",
            request_hash="a" * 64,
            lock_id="LOCK_1",
            execution_id="EXEC_1",
        )

        result = commit_execution_queue_write_batch([preview], queue_path, context=self._context())

        self.assertFalse(result["committed"])
        self.assertEqual(0, result.get("committed_count", 0))
        self.assertIn("duplicate queue_pending_id", result["blocked_reasons"])
        self.assertFalse(result.get("queue_write", False))
        self.assertFalse(result.get("queue_committed", False))
        data = json.loads(queue_path.read_text(encoding="utf-8"))
        self.assertEqual(1, len(data["orders"]))

    def test_batch_commit_existing_queue_execution_id_duplicate_is_blocked_before_write(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        self._write_queue(
            queue_path,
            orders=[{"order_id": "OTHER", "execution_id": "EXEC_1", "request_hash": "z" * 64, "lock_id": "OTHER"}],
        )
        preview = self._write_preview_for(
            order_id="ORDER_1",
            candidate_id="CANDIDATE_1",
            queue_pending_id="PENDING_1",
            request_hash="a" * 64,
            lock_id="LOCK_1",
            execution_id="EXEC_1",
        )

        result = commit_execution_queue_write_batch([preview], queue_path, context=self._context())

        self.assertFalse(result["committed"])
        self.assertEqual(0, result.get("committed_count", 0))
        self.assertIn("duplicate execution_id", result["blocked_reasons"])
        self.assertFalse(result.get("queue_write", False))
        self.assertFalse(result.get("queue_committed", False))
        data = json.loads(queue_path.read_text(encoding="utf-8"))
        self.assertEqual(1, len(data["orders"]))


if __name__ == "__main__":
    unittest.main()
