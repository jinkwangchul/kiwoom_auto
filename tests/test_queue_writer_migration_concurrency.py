# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import json
import tempfile
import threading
import unittest
from pathlib import Path

from chejan_event_recorder import record_chejan_event
from execution_queue_writer import commit_execution_queue_write, preview_execution_queue_write
from sell_runtime_commit_recovery_executor import execute_sell_runtime_commit_recovery
from send_order_result_recorder import record_send_order_result


def _queued_record(index: int, *, send_order_called: bool = False) -> dict:
    return {
        "id": f"ORDER_QUEUED_ORDER_{index}",
        "status": "ORDER_QUEUED",
        "source": "execution_queue_pending",
        "source_signal_id": f"SIG_{index}",
        "order_id": f"ORDER_{index}",
        "candidate_id": f"CANDIDATE_{index}",
        "queue_pending_id": f"PENDING_{index}",
        "request_hash": chr(96 + index) * 64,
        "lock_id": f"LOCK_{index}",
        "execution_id": f"EXEC_{index}",
        "broker_order_no": f"BRK_{index}",
        "send_order_called": send_order_called,
        "execution_enabled": False,
    }


def _write_preview(index: int) -> dict:
    pending = {
        "queue_pending": True,
        "queue_pending_stage": "queue_pending_created",
        "queue_pending_id": f"PENDING_{index}",
        "created_from_candidate_id": f"CANDIDATE_{index}",
        "queue_contract_version": "preview-1",
        "next_stage": "QUEUE_WRITER_REQUIRED",
        "preview_only": True,
        "no_write": True,
        "blocked_reasons": [],
        "warnings": [],
        "order_id": f"ORDER_{index}",
        "source_signal_id": f"SIG_{index}",
        "request_hash_preview": chr(96 + index) * 64,
        "lock_preview": {"ok": True, "lock_id": f"LOCK_{index}"},
        "execution_request_preview": {
            "ok": True,
            "execution_request": {
                "execution_id": f"EXEC_{index}",
                "order_id": f"ORDER_{index}",
                "source_signal_id": f"SIG_{index}",
                "lock_id": f"LOCK_{index}",
                "request_hash": chr(96 + index) * 64,
            },
        },
    }
    return preview_execution_queue_write(pending)


class QueueWriterMigrationConcurrencyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.queue_path = Path(self.tmp.name) / "order_queue.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_queue(self, orders: list[dict], *, revision: int = 0) -> None:
        self.queue_path.write_text(
            json.dumps({"version": 1, "revision": revision, "updated_at": "", "orders": orders}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _read_queue(self) -> dict:
        return json.loads(self.queue_path.read_text(encoding="utf-8"))

    def _chejan_review(self, index: int = 1) -> dict:
        return {
            "chejan_review_ok": True,
            "review_stage": "chejan_event_reviewed",
            "next_stage": "FILL_RECORD_REQUIRED",
            "event_type": "PARTIAL_FILL",
            "order_id": f"ORDER_{index}",
            "order_queued_id": f"ORDER_QUEUED_ORDER_{index}",
            "broker_order_no": f"BRK_{index}",
            "request_hash": chr(96 + index) * 64,
            "lock_id": f"LOCK_{index}",
            "execution_id": f"EXEC_{index}",
            "matched_by": "broker_order_no",
        }

    def _chejan_event(self, index: int = 1) -> dict:
        return {
            "event_type": "PARTIAL_FILL",
            "broker": "KIWOOM",
            "broker_order_no": f"BRK_{index}",
            "account_no": "12345678",
            "code": "003550",
            "side": "SELL",
            "order_status": "FILLED",
            "order_quantity": 10,
            "filled_quantity": 3,
            "remaining_quantity": 7,
            "raw_event": {"received_at": "2026-07-14 09:30:00"},
        }

    def _entrypoint_result(self, index: int = 1) -> dict:
        return {
            "send_order_executed": True,
            "entrypoint_stage": "send_order_called_mock",
            "next_stage": "SEND_ORDER_RESULT_REVIEW_REQUIRED",
            "broker": "MOCK_BROKER",
            "order_id": f"ORDER_{index}",
            "order_queued_id": f"ORDER_QUEUED_ORDER_{index}",
            "request_hash": chr(96 + index) * 64,
            "lock_id": f"LOCK_{index}",
            "execution_id": f"EXEC_{index}",
            "broker_result": {"broker_status": "MOCK_ACCEPTED", "broker_order_no": f"BRK_{index}"},
            "runtime_write_required": True,
            "send_order_called": True,
        }

    def _run_two(self, left, right) -> list[dict]:
        start = threading.Event()
        results: list[dict] = []

        def runner(fn) -> None:
            start.wait()
            results.append(fn())

        threads = [threading.Thread(target=runner, args=(left,)), threading.Thread(target=runner, args=(right,))]
        for thread in threads:
            thread.start()
        start.set()
        for thread in threads:
            thread.join(10)
        return results

    def test_chejan_update_and_new_commit_do_not_lose_records(self) -> None:
        self._write_queue([_queued_record(1, send_order_called=True)])

        results = self._run_two(
            lambda: record_chejan_event(
                self._chejan_review(1),
                self._chejan_event(1),
                self.queue_path,
                context={"manual_chejan_event_record_confirmed": True},
            ),
            lambda: commit_execution_queue_write(
                _write_preview(2),
                self.queue_path,
                context={"manual_queue_write_confirmed": True},
            ),
        )

        data = self._read_queue()
        self.assertEqual(2, data["revision"])
        self.assertEqual(["ORDER_1", "ORDER_2"], [item["order_id"] for item in data["orders"]])
        self.assertTrue(any(item.get("recorded") for item in results))
        self.assertTrue(any(item.get("committed") for item in results))

    def test_send_result_update_and_new_commit_do_not_lose_records(self) -> None:
        self._write_queue([_queued_record(1)])

        results = self._run_two(
            lambda: record_send_order_result(
                self._entrypoint_result(1),
                self.queue_path,
                context={"manual_send_order_result_record_confirmed": True},
            ),
            lambda: commit_execution_queue_write(
                _write_preview(2),
                self.queue_path,
                context={"manual_queue_write_confirmed": True},
            ),
        )

        data = self._read_queue()
        self.assertEqual(2, data["revision"])
        self.assertEqual(["ORDER_1", "ORDER_2"], [item["order_id"] for item in data["orders"]])
        self.assertTrue(data["orders"][0]["send_order_called"])
        self.assertTrue(any(item.get("recorded") for item in results))
        self.assertTrue(any(item.get("committed") for item in results))

    def test_recovery_and_new_commit_with_same_expected_revision_allow_only_one_mutation(self) -> None:
        record = _queued_record(1)
        backup_path = Path(str(self.queue_path) + ".bak")
        self._write_queue([record], revision=0)
        backup_path.write_text(json.dumps({"version": 1, "revision": 0, "orders": []}, ensure_ascii=False, indent=2), encoding="utf-8")
        approval = {
            "approval_type": "SELL_RUNTIME_COMMIT_RECOVERY_APPROVAL_GATE",
            "approval_granted": True,
            "recovery_execution_allowed": True,
            "status": "READY",
            "approved_recovery_actions": [
                {
                    "status": "READY",
                    "approval_token": "TOKEN",
                    "queue_path": str(self.queue_path),
                    "backup_path": str(backup_path),
                    "target_identity": {field: record[field] for field in ("order_id", "request_hash", "lock_id", "execution_id")},
                    "target_identities": [{field: record[field] for field in ("order_id", "candidate_id", "queue_pending_id", "request_hash", "lock_id", "execution_id")}],
                    "target_count": 1,
                    "expected_revision": 0,
                    "queue_backup_diff": {
                        "queue_order_count": 1,
                        "backup_order_count": 0,
                        "queue_matching_record_count": 1,
                        "backup_matching_record_count": 0,
                        "queue_matching_counts": [1],
                        "backup_matching_counts": [0],
                        "queue_backup_changed": True,
                        "target_record_changed": True,
                    },
                }
            ],
        }

        results = self._run_two(
            lambda: execute_sell_runtime_commit_recovery(deepcopy(approval)),
            lambda: commit_execution_queue_write(
                _write_preview(2),
                self.queue_path,
                context={"manual_queue_write_confirmed": True},
                expected_revision=0,
            ),
        )

        data = self._read_queue()
        successful_mutations = sum(
            1
            for item in results
            if item.get("status") == "READY" or item.get("committed") is True
        )
        self.assertEqual(1, successful_mutations)
        self.assertEqual(1, data["revision"])


if __name__ == "__main__":
    unittest.main()
