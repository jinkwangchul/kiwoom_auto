# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import multiprocessing
import msvcrt
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import execution_queue_writer as queue_writer
from execution_queue_writer import (
    claim_order_for_dispatch,
    commit_execution_queue_write,
    commit_execution_queue_write_batch,
    inspect_dispatch_claim,
    preview_execution_queue_write,
    release_dispatch_claim,
)


def _process_queue_pending_result() -> dict:
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
        "lock_preview": {"ok": True, "lock_id": "LOCK_1"},
        "execution_request_preview": {
            "ok": True,
            "execution_request": {
                "execution_id": "EXEC_1",
                "order_id": "ORDER_1",
                "source_signal_id": "SIG_1",
                "lock_id": "LOCK_1",
                "request_hash": "a" * 64,
            },
        },
    }


def _process_write_preview_for(index: int, *, same_request_hash: bool = False) -> dict:
    pending = _process_queue_pending_result()
    request_hash = "a" * 64 if same_request_hash else chr(96 + index) * 64
    pending["order_id"] = f"ORDER_{index}"
    pending["created_from_candidate_id"] = f"CANDIDATE_{index}"
    pending["queue_pending_id"] = f"PENDING_{index}"
    pending["request_hash_preview"] = request_hash
    pending["lock_preview"]["lock_id"] = f"LOCK_{index}"
    pending["execution_request_preview"]["execution_request"]["order_id"] = f"ORDER_{index}"
    pending["execution_request_preview"]["execution_request"]["execution_id"] = f"EXEC_{index}"
    pending["execution_request_preview"]["execution_request"]["request_hash"] = request_hash
    pending["execution_request_preview"]["execution_request"]["lock_id"] = f"LOCK_{index}"
    return preview_execution_queue_write(pending)


def _process_commit_worker(queue_path: str, index: int, same_request_hash: bool, start_event: Any, result_queue: Any) -> None:
    start_event.wait()
    result = commit_execution_queue_write(
        _process_write_preview_for(index, same_request_hash=same_request_hash),
        queue_path,
        context={"manual_queue_write_confirmed": True},
    )
    result_queue.put(result)


def _identity_from_record(record: dict) -> dict:
    return {
        "order_queued_id": record.get("id"),
        "order_id": record.get("order_id"),
        "candidate_id": record.get("candidate_id"),
        "queue_pending_id": record.get("queue_pending_id"),
        "execution_id": record.get("execution_id"),
        "request_hash": record.get("request_hash"),
        "lock_id": record.get("lock_id"),
        "source_signal_id": record.get("source_signal_id"),
    }


def _final_guard_for_record(record: dict, *, approval_token: str = "APPROVAL_TOKEN") -> dict:
    return {
        "guard_type": "SELL_DISPATCH_FINAL_EXECUTION_GUARD",
        "status": "READY",
        "final_guard_ready": True,
        "queue_path": "",
        "approval_token_hash": hashlib.sha256(approval_token.encode("utf-8")).hexdigest(),
        "guarded_candidates": [
            {
                "candidate": deepcopy(record),
                "queue_record": deepcopy(record),
                "final_guard": {"ok": True},
            }
        ],
    }


def _process_claim_worker(queue_path: str, start_event: Any, result_queue: Any, owner: str) -> None:
    data = json.loads(Path(queue_path).read_text(encoding="utf-8"))
    record = data["orders"][0]
    start_event.wait()
    result = claim_order_for_dispatch(
        queue_path,
        _identity_from_record(record),
        _final_guard_for_record(record),
        claim_token="CLAIM_TOKEN",
        claim_owner=owner,
        context={"approval_token": "APPROVAL_TOKEN"},
        expected_revision=0,
    )
    result_queue.put(result)


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

    def _read_queue(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

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

    def _claimable_record(self, index: int = 1) -> dict:
        preview = self._write_preview_for(
            order_id=f"ORDER_{index}",
            candidate_id=f"CANDIDATE_{index}",
            queue_pending_id=f"PENDING_{index}",
            request_hash=chr(96 + index) * 64,
            lock_id=f"LOCK_{index}",
            execution_id=f"EXEC_{index}",
        )
        record = deepcopy(preview["order_queued_record_preview"])
        record["execution_enabled"] = True
        return record

    def _identity(self, record: dict) -> dict:
        return _identity_from_record(record)

    def _final_guard(self, record: dict, *, approval_token: str = "APPROVAL_TOKEN", status: str = "READY") -> dict:
        guard = _final_guard_for_record(record, approval_token=approval_token)
        guard["status"] = status
        if status != "READY":
            guard["final_guard_ready"] = False
        return guard

    def _claim_context(self, *, approval_token: str = "APPROVAL_TOKEN", owner: str = "GUI_MANUAL") -> dict:
        return {
            "approval_token": approval_token,
            "dispatch_claim_owner": owner,
            "dispatch_claim_source": "final_guard",
            "dispatch_claim_ttl_sec": 60,
        }

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
        data = self._read_queue(queue_path)

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
        self.assertTrue(result["lock_acquired"])
        self.assertEqual(0, result["revision_before"])
        self.assertEqual(1, result["revision_after"])
        self.assertEqual(1, data["revision"])

    def test_revisionless_queue_success_sets_revision_one(self) -> None:
        result, queue_path = self._commit_to_temp_queue()

        self.assertTrue(result["committed"])
        self.assertEqual(0, result["revision_before"])
        self.assertEqual(1, result["revision_after"])
        self.assertEqual(1, self._read_queue(queue_path)["revision"])

    def test_existing_revision_success_increments_once(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        queue_path.write_text(json.dumps({"version": 1, "revision": 5, "orders": []}), encoding="utf-8")

        result = commit_execution_queue_write(self._write_preview_result(), queue_path, context=self._context())

        self.assertTrue(result["committed"])
        self.assertEqual(5, result["revision_before"])
        self.assertEqual(6, result["revision_after"])
        self.assertEqual(6, self._read_queue(queue_path)["revision"])

    def test_stale_expected_revision_blocks_without_backup_or_write(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        original = {"version": 1, "revision": 3, "updated_at": "before", "orders": []}
        queue_path.write_text(json.dumps(original, indent=2), encoding="utf-8")

        result = commit_execution_queue_write(
            self._write_preview_result(),
            queue_path,
            context=self._context(),
            expected_revision=2,
        )

        self.assertFalse(result["committed"])
        self.assertEqual("revision_cas", result["write_stage"])
        self.assertTrue(result["cas_checked"])
        self.assertTrue(result["lock_acquired"])
        self.assertEqual(3, result["revision_before"])
        self.assertEqual(3, result["revision_after"])
        self.assertEqual(original, self._read_queue(queue_path))
        self.assertFalse(Path(str(queue_path) + ".bak").exists())

    def test_duplicate_keeps_revision_and_does_not_backup(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        self._write_queue(queue_path, orders=[{"request_hash": "a" * 64, "lock_id": "OTHER", "order_id": "OTHER"}])

        result = commit_execution_queue_write(self._write_preview_result(), queue_path, context=self._context())

        self.assertFalse(result["committed"])
        self.assertEqual("duplicate", result["write_stage"])
        self.assertEqual(0, result["revision_before"])
        self.assertEqual(0, result["revision_after"])
        self.assertNotIn("revision", self._read_queue(queue_path))
        self.assertFalse(Path(str(queue_path) + ".bak").exists())

    def test_backup_preserves_previous_revision(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        queue_path.write_text(json.dumps({"version": 1, "revision": 7, "orders": []}), encoding="utf-8")

        result = commit_execution_queue_write(self._write_preview_result(), queue_path, context=self._context())

        self.assertTrue(result["committed"])
        backup_data = self._read_queue(Path(result["backup_path"]))
        self.assertEqual(7, backup_data["revision"])
        self.assertEqual(8, self._read_queue(queue_path)["revision"])

    def test_single_commit_read_failure_after_replace_preserves_side_effect_flags(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        self._write_queue(queue_path)
        original_read = queue_writer._read_queue_file
        calls = {"count": 0}

        def flaky_read(path: Path):
            calls["count"] += 1
            if calls["count"] == 1:
                return original_read(path)
            return {}, {
                "committed": False,
                "write_stage": "read_queue",
                "next_stage": "BLOCKED",
                "changed": False,
                "blocked_reasons": ["post-write read failed"],
                "warnings": [],
            }

        with mock.patch("execution_queue_writer._read_queue_file", side_effect=flaky_read):
            result = commit_execution_queue_write(self._write_preview_result(), queue_path, context=self._context())

        self.assertTrue(result["committed"])
        self.assertTrue(result["changed"])
        self.assertTrue(result["file_write"])
        self.assertTrue(result["queue_write"])
        self.assertTrue(result["queue_committed"])
        self.assertFalse(result["post_write_verified"])
        self.assertEqual("read_queue", result["write_stage"])

    def test_single_commit_revision_mismatch_after_replace_preserves_side_effect_flags(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        self._write_queue(queue_path)
        original_read = queue_writer._read_queue_file
        calls = {"count": 0}

        def mismatched_revision(path: Path):
            calls["count"] += 1
            data, blocked = original_read(path)
            if calls["count"] > 1 and not blocked:
                data["revision"] = 999
            return data, blocked

        with mock.patch("execution_queue_writer._read_queue_file", side_effect=mismatched_revision):
            result = commit_execution_queue_write(self._write_preview_result(), queue_path, context=self._context())

        self.assertTrue(result["committed"])
        self.assertTrue(result["changed"])
        self.assertTrue(result["file_write"])
        self.assertTrue(result["queue_write"])
        self.assertTrue(result["queue_committed"])
        self.assertFalse(result["post_write_verified"])
        self.assertEqual("post_write_verify", result["write_stage"])

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
        self.assertEqual(1, data["revision"])
        self.assertEqual(0, result["revision_before"])
        self.assertEqual(1, result["revision_after"])

    def test_batch_three_records_increments_revision_once(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        self._write_queue(queue_path)
        previews = [
            self._write_preview_for(
                order_id=f"ORDER_{index}",
                candidate_id=f"CANDIDATE_{index}",
                queue_pending_id=f"PENDING_{index}",
                request_hash=chr(96 + index) * 64,
                lock_id=f"LOCK_{index}",
                execution_id=f"EXEC_{index}",
            )
            for index in (1, 2, 3)
        ]

        result = commit_execution_queue_write_batch(previews, queue_path, context=self._context())

        self.assertTrue(result["committed"])
        self.assertEqual(3, result["committed_count"])
        self.assertEqual(1, self._read_queue(queue_path)["revision"])
        self.assertEqual(0, result["revision_before"])
        self.assertEqual(1, result["revision_after"])

    def test_batch_commit_read_failure_after_replace_preserves_side_effect_flags(self) -> None:
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
        original_read = queue_writer._read_queue_file
        calls = {"count": 0}

        def flaky_read(path: Path):
            calls["count"] += 1
            if calls["count"] == 1:
                return original_read(path)
            return {}, {
                "committed": False,
                "write_stage": "read_queue",
                "next_stage": "BLOCKED",
                "changed": False,
                "blocked_reasons": ["post-write read failed"],
                "warnings": [],
            }

        with mock.patch("execution_queue_writer._read_queue_file", side_effect=flaky_read):
            result = commit_execution_queue_write_batch(previews, queue_path, context=self._context())

        self.assertTrue(result["committed"])
        self.assertEqual(2, result["committed_count"])
        self.assertTrue(result["changed"])
        self.assertTrue(result["file_write"])
        self.assertTrue(result["queue_write"])
        self.assertTrue(result["queue_committed"])
        self.assertFalse(result["post_write_verified"])
        self.assertEqual("read_queue", result["write_stage"])

    def test_batch_commit_revision_mismatch_after_replace_preserves_side_effect_flags(self) -> None:
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
        original_read = queue_writer._read_queue_file
        calls = {"count": 0}

        def mismatched_revision(path: Path):
            calls["count"] += 1
            data, blocked = original_read(path)
            if calls["count"] > 1 and not blocked:
                data["revision"] = 999
            return data, blocked

        with mock.patch("execution_queue_writer._read_queue_file", side_effect=mismatched_revision):
            result = commit_execution_queue_write_batch(previews, queue_path, context=self._context())

        self.assertTrue(result["committed"])
        self.assertEqual(2, result["committed_count"])
        self.assertTrue(result["changed"])
        self.assertTrue(result["file_write"])
        self.assertTrue(result["queue_write"])
        self.assertTrue(result["queue_committed"])
        self.assertFalse(result["post_write_verified"])
        self.assertEqual("post_write_verify", result["write_stage"])

    def test_same_request_hash_two_threads_only_one_commit(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        self._write_queue(queue_path)
        results: list[dict] = []

        def worker() -> None:
            results.append(commit_execution_queue_write(self._write_preview_result(), queue_path, context=self._context()))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(1, sum(1 for item in results if item.get("committed") is True))
        self.assertEqual(1, len(self._read_queue(queue_path)["orders"]))
        self.assertEqual(1, self._read_queue(queue_path)["revision"])

    def test_different_orders_two_threads_both_commit_without_loss(self) -> None:
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
        results: list[dict] = []

        def worker(preview: dict) -> None:
            results.append(commit_execution_queue_write(preview, queue_path, context=self._context()))

        threads = [threading.Thread(target=worker, args=(preview,)) for preview in previews]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        data = self._read_queue(queue_path)
        self.assertEqual(2, sum(1 for item in results if item.get("committed") is True))
        self.assertEqual(["ORDER_1", "ORDER_2"], sorted(record["order_id"] for record in data["orders"]))
        self.assertEqual(2, data["revision"])

    def test_batch_and_single_concurrent_commits_preserve_all_records(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        self._write_queue(queue_path)
        batch_previews = [
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
        single_preview = self._write_preview_for(
            order_id="ORDER_3",
            candidate_id="CANDIDATE_3",
            queue_pending_id="PENDING_3",
            request_hash="c" * 64,
            lock_id="LOCK_3",
            execution_id="EXEC_3",
        )
        results: list[dict] = []

        threads = [
            threading.Thread(
                target=lambda: results.append(
                    commit_execution_queue_write_batch(batch_previews, queue_path, context=self._context())
                )
            ),
            threading.Thread(
                target=lambda: results.append(commit_execution_queue_write(single_preview, queue_path, context=self._context()))
            ),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        data = self._read_queue(queue_path)
        self.assertEqual(2, sum(1 for item in results if item.get("committed") is True))
        self.assertEqual(["ORDER_1", "ORDER_2", "ORDER_3"], sorted(record["order_id"] for record in data["orders"]))
        self.assertEqual(2, data["revision"])

    def test_lock_timeout_blocks_without_file_changes(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        self._write_queue(queue_path)
        before = queue_path.read_text(encoding="utf-8")
        lock_path = queue_path.with_name(f"{queue_path.name}.lock")
        with lock_path.open("a+b") as handle:
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                result = commit_execution_queue_write(
                    self._write_preview_result(),
                    queue_path,
                    context={**self._context(), "queue_lock_timeout_sec": 0.05},
                )
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

        self.assertFalse(result["committed"])
        self.assertEqual("queue_lock", result["write_stage"])
        self.assertFalse(result["lock_acquired"])
        self.assertEqual(before, queue_path.read_text(encoding="utf-8"))
        self.assertFalse(Path(str(queue_path) + ".bak").exists())

    def test_same_request_hash_two_processes_only_one_commit(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        self._write_queue(queue_path)
        ctx = multiprocessing.get_context("spawn")
        start_event = ctx.Event()
        result_queue = ctx.Queue()
        processes = [
            ctx.Process(target=_process_commit_worker, args=(str(queue_path), 1, True, start_event, result_queue)),
            ctx.Process(target=_process_commit_worker, args=(str(queue_path), 2, True, start_event, result_queue)),
        ]
        for process in processes:
            process.start()
        start_event.set()
        for process in processes:
            process.join(10)
            self.assertEqual(0, process.exitcode)
        results = [result_queue.get(timeout=5) for _ in processes]

        data = self._read_queue(queue_path)
        self.assertEqual(1, sum(1 for item in results if item.get("committed") is True))
        self.assertEqual(1, len(data["orders"]))
        self.assertEqual(1, data["revision"])

    def test_different_orders_two_processes_both_commit_without_loss(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        self._write_queue(queue_path)
        ctx = multiprocessing.get_context("spawn")
        start_event = ctx.Event()
        result_queue = ctx.Queue()
        processes = [
            ctx.Process(target=_process_commit_worker, args=(str(queue_path), 1, False, start_event, result_queue)),
            ctx.Process(target=_process_commit_worker, args=(str(queue_path), 2, False, start_event, result_queue)),
        ]
        for process in processes:
            process.start()
        start_event.set()
        for process in processes:
            process.join(10)
            self.assertEqual(0, process.exitcode)
        results = [result_queue.get(timeout=5) for _ in processes]

        data = self._read_queue(queue_path)
        self.assertEqual(2, sum(1 for item in results if item.get("committed") is True))
        self.assertEqual(["ORDER_1", "ORDER_2"], sorted(record["order_id"] for record in data["orders"]))
        self.assertEqual(2, data["revision"])

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

    def test_dispatch_claim_success_transitions_order_queued_to_dispatch_claimed(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        record = self._claimable_record()
        self._write_queue(queue_path, orders=[record])

        result = claim_order_for_dispatch(
            queue_path,
            self._identity(record),
            self._final_guard(record),
            claim_token="CLAIM_TOKEN",
            claim_owner="GUI_MANUAL",
            context=self._claim_context(),
            expected_revision=0,
        )

        data = self._read_queue(queue_path)
        claimed_record = data["orders"][0]
        self.assertTrue(result["committed"])
        self.assertTrue(result["claimed"])
        self.assertEqual("dispatch_claim_committed", result["write_stage"])
        self.assertEqual("DISPATCH_CLAIMED", result["status"])
        self.assertEqual("DISPATCH_CLAIMED", claimed_record["status"])
        self.assertTrue(claimed_record["dispatch_claimed"])
        self.assertEqual(1, claimed_record["dispatch_generation"])
        self.assertEqual(1, result["dispatch_generation"])
        self.assertEqual(result["dispatch_claim_token_hash"], claimed_record["dispatch_claim_token_hash"])
        self.assertNotIn("CLAIM_TOKEN", json.dumps(data))
        self.assertNotIn("APPROVAL_TOKEN", json.dumps(result))
        self.assertEqual(1, data["revision"])
        self.assertEqual(0, result["revision_before"])
        self.assertEqual(1, result["revision_after"])
        self.assertTrue(result["queue_write"])
        self.assertTrue(result["queue_committed"])
        self.assertTrue(result["post_write_verified"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["actual_order_sent"])
        self.assertFalse(result["broker_api_called"])

    def test_dispatch_claim_requires_execution_enabled_true(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        record = self._claimable_record()
        record["execution_enabled"] = False
        self._write_queue(queue_path, orders=[record])

        result = claim_order_for_dispatch(
            queue_path,
            self._identity(record),
            self._final_guard(record),
            claim_token="CLAIM_TOKEN",
            claim_owner="GUI_MANUAL",
            context=self._claim_context(),
            expected_revision=0,
        )

        self.assertFalse(result["committed"])
        self.assertFalse(result["claimed"])
        self.assertIn("target record execution_enabled is not true", result["blocked_reasons"])
        self.assertEqual(0, self._read_queue(queue_path).get("revision", 0))
        self.assertFalse(Path(str(queue_path) + ".bak").exists())

    def test_dispatch_claim_blocks_send_order_called_and_broker_order_no(self) -> None:
        cases = [
            ("send_order_called", True, "target record send_order_called is not false"),
            ("broker_order_no", "BROKER_1", "target record already has broker_order_no"),
        ]
        for field, value, reason in cases:
            with self.subTest(field=field):
                tmp = tempfile.TemporaryDirectory()
                self.addCleanup(tmp.cleanup)
                queue_path = Path(tmp.name) / "order_queue.json"
                record = self._claimable_record()
                record[field] = value
                self._write_queue(queue_path, orders=[record])

                result = claim_order_for_dispatch(
                    queue_path,
                    self._identity(record),
                    self._final_guard(record),
                    claim_token="CLAIM_TOKEN",
                    claim_owner="GUI_MANUAL",
                    context=self._claim_context(),
                    expected_revision=0,
                )

                self.assertFalse(result["committed"])
                self.assertIn(reason, result["blocked_reasons"])

    def test_dispatch_claim_blocks_non_ready_final_guard_and_token_mismatch(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        record = self._claimable_record()
        self._write_queue(queue_path, orders=[record])

        blocked_guard = claim_order_for_dispatch(
            queue_path,
            self._identity(record),
            self._final_guard(record, status="BLOCKED"),
            claim_token="CLAIM_TOKEN",
            claim_owner="GUI_MANUAL",
            context=self._claim_context(),
            expected_revision=0,
        )
        token_mismatch = claim_order_for_dispatch(
            queue_path,
            self._identity(record),
            self._final_guard(record),
            claim_token="CLAIM_TOKEN",
            claim_owner="GUI_MANUAL",
            context=self._claim_context(approval_token="WRONG"),
            expected_revision=0,
        )

        self.assertFalse(blocked_guard["committed"])
        self.assertIn("final guard must be READY", blocked_guard["blocked_reasons"])
        self.assertFalse(token_mismatch["committed"])
        self.assertIn("approval token hash mismatch", token_mismatch["blocked_reasons"])
        self.assertEqual(0, self._read_queue(queue_path).get("revision", 0))

    def test_dispatch_claim_blocks_stale_final_guard_revision_and_snapshot_hash(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        record = self._claimable_record()
        queue_path.write_text(json.dumps({"version": 1, "revision": 3, "orders": [record]}), encoding="utf-8")
        stale_guard = self._final_guard(record)
        stale_guard["queue_revision"] = 2
        hash_guard = self._final_guard(record)
        hash_guard["queue_revision"] = 3
        hash_guard["queue_snapshot_hash"] = "GUARD_HASH"

        stale_revision = claim_order_for_dispatch(
            queue_path,
            self._identity(record),
            stale_guard,
            claim_token="CLAIM_TOKEN",
            claim_owner="GUI_MANUAL",
            context=self._claim_context(),
            expected_revision=3,
        )
        stale_hash = claim_order_for_dispatch(
            queue_path,
            self._identity(record),
            hash_guard,
            claim_token="CLAIM_TOKEN",
            claim_owner="GUI_MANUAL",
            context={**self._claim_context(), "queue_snapshot_hash": "OTHER_HASH"},
            expected_revision=3,
        )

        self.assertFalse(stale_revision["committed"])
        self.assertIn("final guard queue revision is stale", stale_revision["blocked_reasons"])
        self.assertFalse(stale_hash["committed"])
        self.assertIn("final guard queue snapshot hash mismatch", stale_hash["blocked_reasons"])
        self.assertEqual(3, self._read_queue(queue_path)["revision"])
        self.assertFalse(Path(str(queue_path) + ".bak").exists())

    def test_dispatch_claim_requires_identity_match_exactly_once(self) -> None:
        cases = ["missing", "duplicate"]
        for case in cases:
            with self.subTest(case=case):
                tmp = tempfile.TemporaryDirectory()
                self.addCleanup(tmp.cleanup)
                queue_path = Path(tmp.name) / "order_queue.json"
                record = self._claimable_record()
                orders = [] if case == "missing" else [record, deepcopy(record)]
                self._write_queue(queue_path, orders=orders)

                result = claim_order_for_dispatch(
                    queue_path,
                    self._identity(record),
                    self._final_guard(record),
                    claim_token="CLAIM_TOKEN",
                    claim_owner="GUI_MANUAL",
                    context=self._claim_context(),
                    expected_revision=0,
                )

                self.assertFalse(result["committed"])
                self.assertIn(f"dispatch target matching record count is {0 if case == 'missing' else 2}", result["blocked_reasons"])

    def test_dispatch_claim_blocks_existing_and_expired_claim_without_auto_reclaim(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        record = self._claimable_record()
        record.update(
            {
                "status": "DISPATCH_CLAIMED",
                "dispatch_claimed": True,
                "dispatch_claim_id": "OLD_CLAIM",
                "dispatch_claim_expires_at": "2000-01-01 00:00:00",
            }
        )
        self._write_queue(queue_path, orders=[record])

        result = claim_order_for_dispatch(
            queue_path,
            self._identity(record),
            self._final_guard(record),
            claim_token="CLAIM_TOKEN",
            claim_owner="TIMER",
            context=self._claim_context(owner="TIMER"),
            expected_revision=0,
        )

        self.assertFalse(result["committed"])
        self.assertEqual("stale_dispatch_claim", result["write_stage"])
        self.assertIn("target record status is DISPATCH_CLAIMED", result["blocked_reasons"])

    def test_dispatch_claim_requires_expected_revision_and_blocks_stale_revision(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        record = self._claimable_record()
        queue_path.write_text(json.dumps({"version": 1, "revision": 4, "orders": [record]}), encoding="utf-8")

        missing = claim_order_for_dispatch(
            queue_path,
            self._identity(record),
            self._final_guard(record),
            claim_token="CLAIM_TOKEN",
            claim_owner="GUI_MANUAL",
            context=self._claim_context(),
        )
        stale = claim_order_for_dispatch(
            queue_path,
            self._identity(record),
            self._final_guard(record),
            claim_token="CLAIM_TOKEN",
            claim_owner="GUI_MANUAL",
            context=self._claim_context(),
            expected_revision=3,
        )

        self.assertFalse(missing["committed"])
        self.assertIn("expected_revision is required for dispatch claim", missing["blocked_reasons"])
        self.assertFalse(stale["committed"])
        self.assertEqual("revision_cas", stale["write_stage"])
        self.assertEqual(4, self._read_queue(queue_path)["revision"])
        self.assertFalse(Path(str(queue_path) + ".bak").exists())

    def test_dispatch_claim_requires_token_owner_and_positive_ttl(self) -> None:
        record = self._claimable_record()
        cases = [
            {"claim_token": "", "claim_owner": "GUI_MANUAL", "context": self._claim_context(), "reason": "dispatch claim token is required"},
            {"claim_token": "CLAIM_TOKEN", "claim_owner": "", "context": {"approval_token": "APPROVAL_TOKEN"}, "reason": "dispatch claim owner is required"},
            {
                "claim_token": "CLAIM_TOKEN",
                "claim_owner": "GUI_MANUAL",
                "context": {**self._claim_context(), "dispatch_claim_ttl_sec": 0},
                "reason": "dispatch claim ttl must be greater than zero",
            },
        ]
        for case in cases:
            with self.subTest(reason=case["reason"]):
                result = claim_order_for_dispatch(
                    "unused.json",
                    self._identity(record),
                    self._final_guard(record),
                    claim_token=case["claim_token"],
                    claim_owner=case["claim_owner"],
                    context=case["context"],
                    expected_revision=0,
                )
                self.assertFalse(result["committed"])
                self.assertIn(case["reason"], result["blocked_reasons"])

    def test_inspect_dispatch_claim_reports_claim_without_mutation(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        record = self._claimable_record()
        self._write_queue(queue_path, orders=[record])
        claim = claim_order_for_dispatch(
            queue_path,
            self._identity(record),
            self._final_guard(record),
            claim_token="CLAIM_TOKEN",
            claim_owner="GUI_MANUAL",
            context=self._claim_context(),
            expected_revision=0,
        )

        inspected = inspect_dispatch_claim(queue_path, self._identity(record))

        self.assertTrue(claim["claimed"])
        self.assertFalse(inspected["committed"])
        self.assertFalse(inspected["changed"])
        self.assertTrue(inspected["claimed"])
        self.assertEqual("DISPATCH_CLAIMED", inspected["status"])
        self.assertEqual(claim["dispatch_claim_id"], inspected["dispatch_claim_id"])
        self.assertEqual(1, self._read_queue(queue_path)["revision"])

    def test_release_dispatch_claim_success_restores_order_queued(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        record = self._claimable_record()
        self._write_queue(queue_path, orders=[record])
        claim = claim_order_for_dispatch(
            queue_path,
            self._identity(record),
            self._final_guard(record),
            claim_token="CLAIM_TOKEN",
            claim_owner="GUI_MANUAL",
            context=self._claim_context(),
            expected_revision=0,
        )

        released = release_dispatch_claim(
            queue_path,
            self._identity(record),
            claim_id=claim["dispatch_claim_id"],
            claim_token="CLAIM_TOKEN",
            context={
                "manual_dispatch_claim_release_confirmed": True,
                "dispatch_release_reason": "operator_cancelled_before_send",
                "dispatch_released_by": "GUI_MANUAL",
            },
            expected_revision=1,
        )

        data = self._read_queue(queue_path)
        self.assertTrue(released["committed"])
        self.assertTrue(released["released"])
        self.assertEqual("dispatch_claim_released", released["write_stage"])
        self.assertEqual("ORDER_QUEUED", data["orders"][0]["status"])
        self.assertFalse(data["orders"][0]["dispatch_claimed"])
        self.assertEqual("operator_cancelled_before_send", data["orders"][0]["dispatch_release_reason"])
        self.assertEqual("GUI_MANUAL", data["orders"][0]["dispatch_released_by"])
        self.assertEqual(claim["dispatch_claim_id"], data["orders"][0]["previous_dispatch_claim_id"])
        self.assertEqual(2, data["orders"][0]["dispatch_generation"])
        self.assertEqual("operator_cancelled_before_send", released["dispatch_release_reason"])
        self.assertEqual("GUI_MANUAL", released["dispatch_released_by"])
        self.assertEqual(claim["dispatch_claim_id"], released["previous_dispatch_claim_id"])
        self.assertEqual(2, released["dispatch_generation"])
        self.assertEqual(2, data["revision"])
        self.assertFalse(released["send_order_called"])
        self.assertFalse(released["actual_order_sent"])
        self.assertFalse(released["broker_api_called"])

    def test_release_dispatch_claim_blocks_wrong_token_and_send_attempted_state(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        record = self._claimable_record()
        self._write_queue(queue_path, orders=[record])
        claim = claim_order_for_dispatch(
            queue_path,
            self._identity(record),
            self._final_guard(record),
            claim_token="CLAIM_TOKEN",
            claim_owner="GUI_MANUAL",
            context=self._claim_context(),
            expected_revision=0,
        )

        wrong_token = release_dispatch_claim(
            queue_path,
            self._identity(record),
            claim_id=claim["dispatch_claim_id"],
            claim_token="WRONG",
            context={"manual_dispatch_claim_release_confirmed": True},
            expected_revision=1,
        )
        data = self._read_queue(queue_path)
        data["orders"][0]["status"] = "SEND_ATTEMPTED"
        queue_path.write_text(json.dumps(data), encoding="utf-8")
        send_attempted = release_dispatch_claim(
            queue_path,
            self._identity(record),
            claim_id=claim["dispatch_claim_id"],
            claim_token="CLAIM_TOKEN",
            context={"manual_dispatch_claim_release_confirmed": True},
            expected_revision=1,
        )

        self.assertFalse(wrong_token["committed"])
        self.assertIn("dispatch claim token hash mismatch", wrong_token["blocked_reasons"])
        self.assertFalse(send_attempted["committed"])
        self.assertIn("target record is not DISPATCH_CLAIMED", send_attempted["blocked_reasons"])

    def test_dispatch_claim_post_write_verify_failure_preserves_side_effect_flags(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        record = self._claimable_record()
        self._write_queue(queue_path, orders=[record])
        original_read = queue_writer._read_queue_file
        calls = {"count": 0}

        def mismatched_after_read(path: Path):
            calls["count"] += 1
            data, blocked = original_read(path)
            if calls["count"] > 1 and not blocked:
                data["orders"][0]["dispatch_claim_id"] = "WRONG"
            return data, blocked

        with mock.patch("execution_queue_writer._read_queue_file", side_effect=mismatched_after_read):
            result = claim_order_for_dispatch(
                queue_path,
                self._identity(record),
                self._final_guard(record),
                claim_token="CLAIM_TOKEN",
                claim_owner="GUI_MANUAL",
                context=self._claim_context(),
                expected_revision=0,
            )

        self.assertTrue(result["committed"])
        self.assertTrue(result["changed"])
        self.assertTrue(result["file_write"])
        self.assertTrue(result["queue_write"])
        self.assertTrue(result["queue_committed"])
        self.assertFalse(result["post_write_verified"])

    def test_same_order_two_threads_only_one_dispatch_claim(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        record = self._claimable_record()
        self._write_queue(queue_path, orders=[record])
        results: list[dict] = []

        def worker(owner: str) -> None:
            results.append(
                claim_order_for_dispatch(
                    queue_path,
                    self._identity(record),
                    self._final_guard(record),
                    claim_token="CLAIM_TOKEN",
                    claim_owner=owner,
                    context=self._claim_context(owner=owner),
                    expected_revision=0,
                )
            )

        threads = [threading.Thread(target=worker, args=("GUI_MANUAL",)), threading.Thread(target=worker, args=("TIMER",))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        data = self._read_queue(queue_path)
        self.assertEqual(1, sum(1 for item in results if item.get("claimed") is True))
        self.assertEqual("DISPATCH_CLAIMED", data["orders"][0]["status"])
        self.assertEqual(1, data["revision"])

    def test_same_order_two_processes_only_one_dispatch_claim(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        record = self._claimable_record()
        self._write_queue(queue_path, orders=[record])
        ctx = multiprocessing.get_context("spawn")
        start_event = ctx.Event()
        result_queue = ctx.Queue()
        processes = [
            ctx.Process(target=_process_claim_worker, args=(str(queue_path), start_event, result_queue, "GUI_MANUAL")),
            ctx.Process(target=_process_claim_worker, args=(str(queue_path), start_event, result_queue, "TIMER")),
        ]
        for process in processes:
            process.start()
        start_event.set()
        for process in processes:
            process.join(10)
            self.assertEqual(0, process.exitcode)
        results = [result_queue.get(timeout=5) for _ in processes]

        data = self._read_queue(queue_path)
        self.assertEqual(1, sum(1 for item in results if item.get("claimed") is True))
        self.assertEqual(1, data["revision"])
        self.assertEqual("DISPATCH_CLAIMED", data["orders"][0]["status"])

    def test_different_order_claims_preserve_all_records_without_send_order(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        queue_path = Path(tmp.name) / "order_queue.json"
        records = [self._claimable_record(1), self._claimable_record(2)]
        self._write_queue(queue_path, orders=records)

        first = claim_order_for_dispatch(
            queue_path,
            self._identity(records[0]),
            self._final_guard(records[0]),
            claim_token="CLAIM_TOKEN_1",
            claim_owner="GUI_MANUAL",
            context=self._claim_context(),
            expected_revision=0,
        )
        second = claim_order_for_dispatch(
            queue_path,
            self._identity(records[1]),
            self._final_guard(records[1]),
            claim_token="CLAIM_TOKEN_2",
            claim_owner="WORKER:2",
            context=self._claim_context(owner="WORKER:2"),
            expected_revision=1,
        )

        data = self._read_queue(queue_path)
        self.assertTrue(first["claimed"])
        self.assertTrue(second["claimed"])
        self.assertEqual(["DISPATCH_CLAIMED", "DISPATCH_CLAIMED"], [item["status"] for item in data["orders"]])
        self.assertEqual(2, data["revision"])
        self.assertFalse(any(item.get("send_order_called") for item in data["orders"]))


if __name__ == "__main__":
    unittest.main()
