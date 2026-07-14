# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import send_order_result_recorder
from send_order_result_recorder import record_send_order_result


_DEFAULT_QUEUE_PATH = object()


class SendOrderResultRecorderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.queue_path = Path(self.tmp.name) / "order_queue.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_json(self, path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_json(self, path: Path | None = None) -> dict:
        target = self.queue_path if path is None else path
        return json.loads(target.read_text(encoding="utf-8"))

    def _sha256(self, path: Path | None = None) -> str:
        target = self.queue_path if path is None else path
        return hashlib.sha256(target.read_bytes()).hexdigest().upper()

    def _entrypoint_result(self, **overrides: object) -> dict[str, object]:
        result = {
            "send_order_executed": True,
            "entrypoint_stage": "send_order_called_mock",
            "next_stage": "SEND_ORDER_RESULT_REVIEW_REQUIRED",
            "broker": "MOCK_BROKER",
            "order_id": "ORDER_1",
            "order_queued_id": "ORDER_QUEUED_ORDER_1",
            "request_hash": "HASH_1",
            "lock_id": "LOCK_1",
            "execution_id": "EXEC_1",
            "broker_result": {
                "broker_status": "MOCK_ACCEPTED",
                "broker_order_no": "BRK_1",
                "request_hash": "HASH_1",
            },
            "runtime_write_required": True,
            "send_order_called": True,
            "blocked_reasons": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def _record(self, **overrides: object) -> dict[str, object]:
        record = {
            "id": "ORDER_QUEUED_ORDER_1",
            "status": "ORDER_QUEUED",
            "source": "execution_queue_pending",
            "source_signal_id": "SIG_1",
            "order_id": "ORDER_1",
            "candidate_id": "EXEC_CANDIDATE_ORDER_1",
            "queue_pending_id": "QUEUE_PENDING_EXEC_CANDIDATE_ORDER_1",
            "request_hash": "HASH_1",
            "lock_id": "LOCK_1",
            "execution_id": "EXEC_1",
            "send_order_called": False,
            "execution_enabled": False,
        }
        record.update(overrides)
        return record

    def _write_queue(self, *, record: dict[str, object] | None = None, root: object | None = None) -> None:
        if root is not None:
            self._write_json(self.queue_path, root)
            return
        self._write_json(
            self.queue_path,
            {
                "version": 1,
                "updated_at": "",
                "orders": [self._record() if record is None else record],
            },
        )

    def _context(self) -> dict[str, object]:
        return {"manual_send_order_result_record_confirmed": True}

    def _record_result(
        self,
        entrypoint_result: object | None = None,
        queue_path: object = _DEFAULT_QUEUE_PATH,
        queue_snapshot: object | None = None,
        context: object | None = None,
        backup: bool = True,
    ) -> dict[str, object]:
        return record_send_order_result(
            self._entrypoint_result() if entrypoint_result is None else entrypoint_result,
            self.queue_path if queue_path is _DEFAULT_QUEUE_PATH else queue_path,
            queue_snapshot=queue_snapshot,
            context=self._context() if context is None else context,
            backup=backup,
        )

    def test_entrypoint_result_non_dict_is_blocked(self) -> None:
        result = self._record_result(entrypoint_result="invalid")

        self.assertFalse(result["recorded"])
        self.assertEqual("entrypoint_result", result["record_stage"])
        self.assertIn("entrypoint_result must be a dict", result["blocked_reasons"])

    def test_send_order_executed_false_is_blocked(self) -> None:
        result = self._record_result(entrypoint_result=self._entrypoint_result(send_order_executed=False))

        self.assertFalse(result["recorded"])
        self.assertIn("entrypoint_result.send_order_executed is not true", result["blocked_reasons"])

    def test_send_order_called_false_is_blocked(self) -> None:
        result = self._record_result(entrypoint_result=self._entrypoint_result(send_order_called=False))

        self.assertFalse(result["recorded"])
        self.assertIn("entrypoint_result.send_order_called is not true", result["blocked_reasons"])

    def test_runtime_write_required_false_is_blocked(self) -> None:
        result = self._record_result(entrypoint_result=self._entrypoint_result(runtime_write_required=False))

        self.assertFalse(result["recorded"])
        self.assertIn("entrypoint_result.runtime_write_required is not true", result["blocked_reasons"])

    def test_next_stage_mismatch_is_blocked(self) -> None:
        result = self._record_result(entrypoint_result=self._entrypoint_result(next_stage="OTHER"))

        self.assertFalse(result["recorded"])
        self.assertIn(
            "entrypoint_result.next_stage is not SEND_ORDER_RESULT_REVIEW_REQUIRED",
            result["blocked_reasons"],
        )

    def test_manual_record_confirmation_missing_is_blocked(self) -> None:
        self._write_queue()
        result = self._record_result(context={})

        self.assertFalse(result["recorded"])
        self.assertEqual("operator_confirmation", result["record_stage"])
        self.assertIn("manual send order result record confirmation is required", result["blocked_reasons"])

    def test_queue_path_missing_is_blocked(self) -> None:
        result = self._record_result(queue_path=None)

        self.assertFalse(result["recorded"])
        self.assertEqual("queue_path", result["record_stage"])
        self.assertIn("queue_path is required", result["blocked_reasons"])

    def test_queue_file_missing_is_blocked(self) -> None:
        result = self._record_result()

        self.assertFalse(result["recorded"])
        self.assertEqual("read_queue", result["record_stage"])
        self.assertIn("queue file does not exist", result["blocked_reasons"])

    def test_corrupt_json_is_blocked(self) -> None:
        self.queue_path.write_text("{", encoding="utf-8")

        result = self._record_result()

        self.assertFalse(result["recorded"])
        self.assertEqual("read_queue", result["record_stage"])
        self.assertTrue(result["blocked_reasons"][0].startswith("failed to read order_queue json:"))

    def test_root_non_dict_is_blocked(self) -> None:
        self._write_json(self.queue_path, [])

        result = self._record_result()

        self.assertFalse(result["recorded"])
        self.assertIn("order_queue root must be an object", result["blocked_reasons"])

    def test_orders_non_list_is_blocked(self) -> None:
        self._write_queue(root={"version": 1, "orders": {}})

        result = self._record_result()

        self.assertFalse(result["recorded"])
        self.assertIn("order_queue orders must be a list", result["blocked_reasons"])

    def test_target_record_missing_is_blocked(self) -> None:
        self._write_queue(record=self._record(id="OTHER", order_id="OTHER"))

        result = self._record_result()

        self.assertFalse(result["recorded"])
        self.assertIn("target record not found", result["blocked_reasons"])

    def test_target_status_mismatch_is_blocked(self) -> None:
        self._write_queue(record=self._record(status="REAL_READY"))

        result = self._record_result()

        self.assertFalse(result["recorded"])
        self.assertIn("target record.status is not ORDER_QUEUED", result["blocked_reasons"])

    def test_target_send_order_called_true_is_blocked(self) -> None:
        self._write_queue(record=self._record(send_order_called=True))

        result = self._record_result()

        self.assertFalse(result["recorded"])
        self.assertIn("target record.send_order_called is not false", result["blocked_reasons"])

    def test_target_execution_enabled_true_is_blocked(self) -> None:
        self._write_queue(record=self._record(execution_enabled=True))

        result = self._record_result()

        self.assertFalse(result["recorded"])
        self.assertIn("target record.execution_enabled is not false", result["blocked_reasons"])

    def test_target_identity_mismatch_is_blocked(self) -> None:
        mismatches = {
            "order_id": "OTHER_ORDER",
            "request_hash": "OTHER_HASH",
            "lock_id": "OTHER_LOCK",
            "execution_id": "OTHER_EXEC",
        }
        for field, value in mismatches.items():
            with self.subTest(field=field):
                self._write_queue(record=self._record(**{field: value}))

                result = self._record_result()

                self.assertFalse(result["recorded"])
                self.assertIn(
                    f"target record.{field} does not match entrypoint_result.{field}",
                    result["blocked_reasons"],
                )

    def test_stale_snapshot_is_blocked(self) -> None:
        self._write_queue()

        result = self._record_result(queue_snapshot={"sha256": "STALE"})

        self.assertFalse(result["recorded"])
        self.assertEqual("stale_queue", result["record_stage"])
        self.assertIn(
            "queue file changed after send order entrypoint; manual review required",
            result["blocked_reasons"],
        )

    def test_uncertain_result_is_blocked(self) -> None:
        result = self._record_result(
            entrypoint_result=self._entrypoint_result(
                send_order_executed=False,
                send_order_called=False,
                runtime_write_required=False,
                next_stage="BROKER_CALL_UNCERTAIN_REVIEW_REQUIRED",
            )
        )

        self.assertFalse(result["recorded"])
        self.assertIn("uncertain broker call results are not recorded by this recorder", result["blocked_reasons"])

    def test_temp_queue_successfully_records_send_order_result(self) -> None:
        self._write_queue()
        before_sha = self._sha256()

        result = self._record_result(queue_snapshot={"sha256": before_sha})

        self.assertTrue(result["recorded"])
        self.assertEqual("send_order_result_recorded", result["record_stage"])
        self.assertEqual("SEND_ORDER_RESULT_REVIEW_REQUIRED", result["next_stage"])
        self.assertTrue(result["changed"])
        self.assertEqual(str(self.queue_path), result["order_queue_path"])
        self.assertEqual(str(self.queue_path) + ".bak", result["backup_path"])
        self.assertEqual("ORDER_1", result["order_id"])
        self.assertEqual("ORDER_QUEUED_ORDER_1", result["order_queued_id"])
        self.assertEqual("HASH_1", result["request_hash"])
        self.assertEqual("LOCK_1", result["lock_id"])
        self.assertEqual("EXEC_1", result["execution_id"])
        self.assertTrue(result["send_order_called"])
        self.assertEqual("SEND_ORDER_CALLED", result["send_order_result_status"])
        self.assertEqual(before_sha, result["before_sha256"])
        self.assertNotEqual(before_sha, result["after_sha256"])
        self.assertEqual([], result["blocked_reasons"])
        self.assertEqual(1, self._read_json()["revision"])
        self.assertEqual(0, result["revision_before"])
        self.assertEqual(1, result["revision_after"])

    def test_stale_expected_revision_is_blocked_without_backup(self) -> None:
        self._write_queue()

        result = self._record_result(context={"manual_send_order_result_record_confirmed": True, "expected_revision": 9})

        self.assertFalse(result["recorded"])
        self.assertEqual("revision_cas", result["record_stage"])
        self.assertEqual(0, self._read_json().get("revision", 0))
        self.assertFalse(Path(str(self.queue_path) + ".bak").exists())

    def test_success_records_expected_fields(self) -> None:
        self._write_queue()

        self._record_result()

        record = self._read_json()["orders"][0]
        self.assertTrue(record["send_order_called"])
        self.assertTrue(record["send_order_called_at"])
        self.assertEqual("send_order_called_mock", record["send_order_entrypoint_stage"])
        self.assertEqual("SEND_ORDER_CALLED", record["send_order_result_status"])
        self.assertTrue(record["send_order_result_recorded_at"])
        self.assertEqual("MOCK_BROKER", record["broker"])
        self.assertEqual(
            {"broker_status": "MOCK_ACCEPTED", "broker_order_no": "BRK_1", "request_hash": "HASH_1"},
            record["broker_result"],
        )
        self.assertEqual("BRK_1", record["broker_order_no"])
        self.assertEqual("send_order_entrypoint", record["send_order_record_source"])
        self.assertTrue(record["updated_at"])

    def test_backup_file_is_created(self) -> None:
        self._write_queue()

        result = self._record_result()

        self.assertTrue(Path(result["backup_path"]).exists())

    def test_backup_false_does_not_create_backup_path(self) -> None:
        self._write_queue()

        result = self._record_result(backup=False)

        self.assertTrue(result["recorded"])
        self.assertIsNone(result["backup_path"])
        self.assertFalse(Path(str(self.queue_path) + ".bak").exists())

    def test_inputs_are_not_mutated(self) -> None:
        self._write_queue()
        entrypoint_result = self._entrypoint_result()
        queue_snapshot = {"sha256": self._sha256()}
        context = self._context()
        originals = (deepcopy(entrypoint_result), deepcopy(queue_snapshot), deepcopy(context))

        record_send_order_result(
            entrypoint_result,
            self.queue_path,
            queue_snapshot=queue_snapshot,
            context=context,
        )

        self.assertEqual(originals[0], entrypoint_result)
        self.assertEqual(originals[1], queue_snapshot)
        self.assertEqual(originals[2], context)

    def test_send_order_and_entrypoint_are_not_called(self) -> None:
        self._write_queue()
        with (
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
            mock.patch("send_order_entrypoint.execute_send_order") as execute_send_order,
        ):
            result = self._record_result()

        self.assertTrue(result["recorded"])
        send_order_stub.assert_not_called()
        execute_send_order.assert_not_called()

    def test_module_does_not_use_runtime_default_gui_timer_or_send_order(self) -> None:
        module_text = send_order_result_recorder.__loader__.get_source(send_order_result_recorder.__name__)

        self.assertNotIn("ORDER_QUEUE_PATH", module_text)
        self.assertNotIn("runtime/order_queue.json", module_text)
        self.assertNotIn("import send_order_entrypoint", module_text)
        self.assertNotIn("from send_order_entrypoint", module_text)
        self.assertNotIn("kiwoom_order_adapter", module_text)
        self.assertNotIn("dynamicCall", module_text)
        self.assertNotIn("QTimer", module_text)
        self.assertNotIn("QPushButton", module_text)


if __name__ == "__main__":
    unittest.main()
