from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import execution_queue_commit_service
from execution_queue_commit_service import commit_execution_queue_manually


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


class ExecutionQueueCommitServicePolicyContractTest(unittest.TestCase):
    def _queue_preview(self, *, order_id: str = "ORDER_POLICY_1") -> dict:
        return {
            "write_preview": True,
            "write_stage": "order_queued_record_preview_created",
            "next_stage": "QUEUE_WRITE_REQUIRED",
            "preview_only": True,
            "no_write": True,
            "blocked_reasons": [],
            "order_queued_record_preview": {
                "id": f"ORDER_QUEUED_{order_id}",
                "status": "ORDER_QUEUED",
                "source": "execution_queue_pending",
                "source_signal_id": "SIGNAL_POLICY_1",
                "order_id": order_id,
                "candidate_id": "CANDIDATE_POLICY_1",
                "queue_pending_id": "QUEUE_PENDING_POLICY_1",
                "request_hash": "HASH_POLICY_1",
                "lock_id": "LOCK_POLICY_1",
                "execution_id": "EXEC_POLICY_1",
                "execution_request": {
                    "execution_id": "EXEC_POLICY_1",
                    "order_id": order_id,
                    "request_hash": "HASH_POLICY_1",
                    "lock_id": "LOCK_POLICY_1",
                },
                "queue_contract_version": "preview-1",
                "send_order_called": False,
                "execution_enabled": False,
                "blocked_reasons": [],
            },
        }

    def _ready_policy(self) -> dict:
        return {
            "policy_type": "EXECUTION_QUEUE_COMMIT_READINESS_POLICY",
            "status": "READY_TO_COMMIT_QUEUE",
            "queue_commit_allowed": True,
            "preview_only": True,
            "queue_write": False,
            "runtime_write": False,
            "identity_checks": {},
            "required_confirmations": {},
            "issues": [],
            "warnings": [],
        }

    def _blocked_policy(self) -> dict:
        policy = self._ready_policy()
        policy.update(
            {
                "status": "BLOCKED",
                "queue_commit_allowed": False,
                "issues": ["RUNTIME_COMMIT_NOT_COMMITTED"],
            }
        )
        return policy

    def _invalid_policy(self) -> dict:
        policy = self._ready_policy()
        policy.update(
            {
                "status": "INVALID",
                "queue_commit_allowed": False,
                "issues": ["MALFORMED_RUNTIME_COMMIT_RESULT"],
            }
        )
        return policy

    def _context(self) -> dict:
        return {
            "manual_queue_write_confirmed": True,
            "manual_runtime_queue_write_confirmed": True,
        }

    def _success_commit_result(self) -> dict:
        return {
            "committed": True,
            "write_stage": "order_queued_record_committed",
            "next_stage": "QUEUE_COMMITTED_REVIEW_REQUIRED",
            "changed": True,
            "order_queue_path": str(ROOT / "runtime" / "order_queue.json"),
            "backup_path": str(ROOT / "runtime" / "order_queue.json.bak"),
            "order_id": "ORDER_POLICY_1",
            "order_queued_id": "ORDER_QUEUED_ORDER_POLICY_1",
            "request_hash": "HASH_POLICY_1",
            "lock_id": "LOCK_POLICY_1",
            "status": "ORDER_QUEUED",
            "send_order_called": False,
            "execution_enabled": False,
            "blocked_reasons": [],
            "warnings": [],
        }

    def _failed_commit_result(self) -> dict:
        return {
            "committed": False,
            "write_stage": "duplicate",
            "next_stage": "BLOCKED",
            "changed": False,
            "blocked_reasons": ["duplicate request_hash"],
            "warnings": [],
        }

    def test_runtime_queue_path_policy_missing_blocked(self) -> None:
        with mock.patch.object(execution_queue_commit_service, "commit_execution_queue_write") as writer:
            result = commit_execution_queue_manually(
                self._queue_preview(),
                ROOT / "runtime" / "order_queue.json",
                context=self._context(),
            )

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["manual_commit"])
        self.assertIn("queue commit readiness policy is required", result["blocked_reasons"])
        writer.assert_not_called()

    def test_runtime_queue_path_ready_policy_missing_final_confirmation_blocked(self) -> None:
        with mock.patch.object(execution_queue_commit_service, "commit_execution_queue_write") as writer:
            result = commit_execution_queue_manually(
                self._queue_preview(),
                ROOT / "runtime" / "order_queue.json",
                context=self._context(),
                queue_commit_readiness_policy_result=self._ready_policy(),
            )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("manual queue commit after runtime confirmation is required", result["blocked_reasons"])
        writer.assert_not_called()

    def test_runtime_queue_path_ready_policy_all_confirmations_committed(self) -> None:
        with mock.patch.object(
            execution_queue_commit_service,
            "commit_execution_queue_write",
            return_value=self._success_commit_result(),
        ) as writer:
            result = commit_execution_queue_manually(
                self._queue_preview(),
                ROOT / "runtime" / "order_queue.json",
                context=self._context(),
                queue_commit_readiness_policy_result=self._ready_policy(),
                manual_queue_commit_after_runtime_confirmed=True,
            )

        self.assertEqual("COMMITTED", result["status"])
        self.assertTrue(result["manual_commit"])
        self.assertEqual("QUEUE_COMMITTED_REVIEW_REQUIRED", result["next_stage"])
        self.assertFalse(result["commit_result"]["send_order_called"])
        self.assertFalse(result["commit_result"]["execution_enabled"])
        writer.assert_called_once()

    def test_policy_blocked_invalid_or_malformed(self) -> None:
        cases = [
            (self._blocked_policy(), "BLOCKED", "RUNTIME_COMMIT_NOT_COMMITTED"),
            (self._invalid_policy(), "INVALID", "MALFORMED_RUNTIME_COMMIT_RESULT"),
            ({"bad": True}, "INVALID", "queue commit readiness policy type is invalid"),
        ]
        for policy, status, reason in cases:
            with self.subTest(status=status), mock.patch.object(
                execution_queue_commit_service, "commit_execution_queue_write"
            ) as writer:
                result = commit_execution_queue_manually(
                    self._queue_preview(),
                    ROOT / "runtime" / "order_queue.json",
                    context=self._context(),
                    queue_commit_readiness_policy_result=policy,
                    manual_queue_commit_after_runtime_confirmed=True,
                )

                self.assertEqual(status, result["status"])
                self.assertFalse(result["manual_commit"])
                self.assertIn(reason, result["blocked_reasons"][0])
                writer.assert_not_called()

    def test_temp_queue_path_existing_commit_behavior_is_preserved(self) -> None:
        with mock.patch.object(
            execution_queue_commit_service,
            "commit_execution_queue_write",
            return_value=self._success_commit_result(),
        ) as writer:
            result = commit_execution_queue_manually(
                self._queue_preview(),
                "temp_order_queue.json",
                context={"manual_queue_write_confirmed": True},
            )

        self.assertEqual("COMMITTED", result["status"])
        self.assertTrue(result["manual_commit"])
        writer.assert_called_once()

    def test_writer_duplicate_read_schema_revalidation_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            queue_path = Path(temp_dir) / "order_queue.json"
            _write_json(
                queue_path,
                {
                    "version": 1,
                    "updated_at": "",
                    "orders": [{"request_hash": "HASH_POLICY_1"}],
                },
            )

            result = commit_execution_queue_manually(
                self._queue_preview(),
                queue_path,
                context={"manual_queue_write_confirmed": True},
            )

        self.assertFalse(result["manual_commit"])
        self.assertEqual("duplicate", result["commit_stage"])
        self.assertIn("duplicate request_hash", result["blocked_reasons"])

    def test_queue_commit_failure_does_not_call_runtime_rollback_and_marks_issue(self) -> None:
        with (
            mock.patch.object(
                execution_queue_commit_service,
                "commit_execution_queue_write",
                return_value=self._failed_commit_result(),
            ) as writer,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
        ):
            result = commit_execution_queue_manually(
                self._queue_preview(),
                ROOT / "runtime" / "order_queue.json",
                context=self._context(),
                queue_commit_readiness_policy_result=self._ready_policy(),
                manual_queue_commit_after_runtime_confirmed=True,
            )

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["manual_commit"])
        self.assertIn("QUEUE_COMMIT_FAILED_AFTER_RUNTIME_COMMIT", result["blocked_reasons"])
        writer.assert_called_once()
        runtime_commit.assert_not_called()

    def test_no_sendorder_execution_controller_or_gui_calls(self) -> None:
        with (
            mock.patch.object(
                execution_queue_commit_service,
                "commit_execution_queue_write",
                return_value=self._success_commit_result(),
            ),
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
        ):
            result = commit_execution_queue_manually(
                self._queue_preview(),
                ROOT / "runtime" / "order_queue.json",
                context=self._context(),
                queue_commit_readiness_policy_result=self._ready_policy(),
                manual_queue_commit_after_runtime_confirmed=True,
            )

        self.assertTrue(result["manual_commit"])
        send_order.assert_not_called()
        self.assertNotIn("execution_controller_called", result)
        self.assertNotIn("gui_connected", result)

    def test_order_queue_runtime_and_rules_hash_unchanged(self) -> None:
        runtime_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        rules_paths = list((ROOT / "routines").glob("**/rules.json"))
        before_runtime = {str(path): _sha256(path) for path in runtime_paths}
        before_rules = {str(path): _sha256(path) for path in rules_paths}

        with mock.patch.object(
            execution_queue_commit_service,
            "commit_execution_queue_write",
            return_value=self._success_commit_result(),
        ):
            commit_execution_queue_manually(
                self._queue_preview(),
                ROOT / "runtime" / "order_queue.json",
                context=self._context(),
                queue_commit_readiness_policy_result=self._ready_policy(),
                manual_queue_commit_after_runtime_confirmed=True,
            )

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})


if __name__ == "__main__":
    unittest.main()
