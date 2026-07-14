from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from sell_runtime_commit_real_executor import execute_sell_runtime_commit


def _record(
    *,
    source_signal_id: str = "SIG_1",
    order_id: str = "ORDER_1",
    candidate_id: str | None = None,
    queue_pending_id: str | None = None,
    request_hash: str = "r" * 64,
    lock_id: str = "LOCK_1",
    execution_id: str = "EXEC_1",
) -> dict:
    candidate_id = candidate_id or f"CANDIDATE_{order_id}"
    queue_pending_id = queue_pending_id or f"QUEUE_PENDING_{order_id}"
    return {
        "id": f"ORDER_QUEUED_{order_id}",
        "status": "ORDER_QUEUED",
        "source": "execution_queue_pending",
        "source_signal_id": source_signal_id,
        "order_id": order_id,
        "candidate_id": candidate_id,
        "queue_pending_id": queue_pending_id,
        "request_hash": request_hash,
        "lock_id": lock_id,
        "execution_id": execution_id,
        "execution_request": {
            "execution_id": execution_id,
            "request_hash": request_hash,
            "lock_id": lock_id,
        },
        "queue_contract_version": "preview-1",
        "send_order_called": False,
        "execution_enabled": False,
        "blocked_reasons": [],
    }


def _approved_action(record: dict | None = None, *, queue_path: str = "runtime/order_queue.json", token: str = "TOKEN_1") -> dict:
    payload = record or _record()
    queue_write_preview = {
        "write_preview": True,
        "write_stage": "order_queued_record_preview_created",
        "next_stage": "QUEUE_WRITE_REQUIRED",
        "preview_only": True,
        "no_write": True,
        "blocked_reasons": [],
        "order_queued_record_preview": deepcopy(payload),
    }
    return {
        "status": "READY",
        "approval_action": "APPROVE_REAL_EXECUTOR_COMMIT_PREVIEW",
        "source_signal_id": payload["source_signal_id"],
        "order_id": payload["order_id"],
        "candidate_id": payload["candidate_id"],
        "queue_pending_id": payload["queue_pending_id"],
        "execution_id": payload["execution_id"],
        "request_hash": payload["request_hash"],
        "lock_id": payload["lock_id"],
        "commit_boundary_function": "execution_queue_writer.commit_execution_queue_write",
        "commit_payload": {
            "function": "execution_queue_writer.commit_execution_queue_write",
            "args": {
                "queue_write_preview_result": queue_write_preview,
                "queue_path": queue_path,
            },
            "kwargs": {
                "backup": True,
                "context": {
                    "manual_queue_write_confirmed": True,
                    "approval_token": token,
                },
            },
            "queue_path_required": True,
            "manual_queue_write_confirmation_required": True,
            "called": False,
        },
        "approval_token": token,
        "queue_path": queue_path,
        "source_real_executor_action": {},
        "runtime_write": False,
        "queue_write": False,
        "file_write": False,
        "queue_committed": False,
        "send_order": False,
        "broker_api_called": False,
        "order_request_created": False,
        "real_ready_state_changed": False,
        "runtime_commit_executed": False,
    }


def _approval(*actions: dict, status: str = "READY", approval_granted: bool | None = None, commit_allowed: bool | None = None) -> dict:
    action_list = list(actions or [_approved_action()])
    if approval_granted is None:
        approval_granted = status == "READY"
    if commit_allowed is None:
        commit_allowed = status == "READY"
    return {
        "approval_type": "SELL_RUNTIME_COMMIT_REAL_EXECUTOR_APPROVAL_GATE_PREVIEW",
        "ownership": "MASTER_ENGINE",
        "domain": "Execution / Runtime Commit Real Executor Approval Gate Preview",
        "routine_dependency": None,
        "preview_only": True,
        "execution_connected": False,
        "runtime_write": False,
        "queue_write": False,
        "file_write": False,
        "queue_committed": False,
        "send_order": False,
        "broker_api_called": False,
        "actual_order_sent": False,
        "order_request_created": False,
        "real_ready_state_changed": False,
        "runtime_commit_executed": False,
        "status": status,
        "approval_granted": approval_granted,
        "commit_allowed": commit_allowed,
        "real_executor_preview_snapshot": {},
        "approval_context_snapshot": {},
        "approved_real_executor_actions": action_list,
        "blocked_approval_actions": [],
        "source_summary": {},
        "warnings": [],
        "reasons": [],
        "summary": {
            "approval_ready_count": sum(1 for item in action_list if isinstance(item, dict) and item.get("status") == "READY"),
            "approval_blocked_count": 0,
            "approval_invalid_count": 0,
            "approved_action_count": len(action_list),
            "blocked_action_count": 0,
            "runtime_write": False,
            "queue_write": False,
            "file_write": False,
            "queue_committed": False,
            "send_order": False,
            "broker_api_called": False,
            "runtime_commit_executed": False,
            "priority_selected": False,
            "auto_selected": False,
        },
    }


class SellRuntimeCommitRealExecutorTests(unittest.TestCase):
    def _queue_path(self) -> Path:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        queue_path = Path(temp.name) / "order_queue.json"
        queue_path.write_text(json.dumps({"version": 1, "updated_at": "before", "orders": []}, indent=2), encoding="utf-8")
        return queue_path

    def test_single_approved_candidate_commits_to_temp_queue(self):
        queue_path = self._queue_path()

        result = execute_sell_runtime_commit(_approval(_approved_action(queue_path=str(queue_path))))

        self.assertEqual(result["executor_type"], "SELL_RUNTIME_COMMIT_REAL_EXECUTOR")
        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["queue_committed"])
        self.assertTrue(result["runtime_commit_executed"])
        data = json.loads(queue_path.read_text(encoding="utf-8"))
        self.assertEqual(len(data["orders"]), 1)
        self.assertEqual(data["orders"][0]["order_id"], "ORDER_1")

    def test_backup_file_created(self):
        queue_path = self._queue_path()

        result = execute_sell_runtime_commit(_approval(_approved_action(queue_path=str(queue_path))))

        backup_path = result["execution_results"][0]["commit_result"]["backup_path"]
        self.assertTrue(Path(backup_path).exists())

    def test_atomic_json_result_shape(self):
        queue_path = self._queue_path()

        execute_sell_runtime_commit(_approval(_approved_action(queue_path=str(queue_path))))

        data = json.loads(queue_path.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], 1)
        self.assertIsInstance(data["updated_at"], str)
        self.assertEqual(data["orders"][0]["status"], "ORDER_QUEUED")
        self.assertFalse(data["orders"][0]["send_order_called"])
        self.assertFalse(data["orders"][0]["execution_enabled"])

    def test_unapproved_input_blocked(self):
        queue_path = self._queue_path()

        result = execute_sell_runtime_commit(
            _approval(_approved_action(queue_path=str(queue_path)), approval_granted=False)
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["queue_committed"])

    def test_blocked_input_blocked(self):
        result = execute_sell_runtime_commit(_approval(status="BLOCKED", approval_granted=False, commit_allowed=False))

        self.assertEqual(result["status"], "BLOCKED")

    def test_invalid_input_invalid(self):
        result = execute_sell_runtime_commit(_approval(status="INVALID", approval_granted=False, commit_allowed=False))

        self.assertEqual(result["status"], "INVALID")

    def test_wrong_type_invalid(self):
        approval = _approval()
        approval["approval_type"] = "OTHER"

        result = execute_sell_runtime_commit(approval)

        self.assertEqual(result["status"], "INVALID")

    def test_commit_allowed_false_blocked(self):
        queue_path = self._queue_path()

        result = execute_sell_runtime_commit(_approval(_approved_action(queue_path=str(queue_path)), commit_allowed=False))

        self.assertEqual(result["status"], "BLOCKED")

    def test_bad_token_invalid(self):
        queue_path = self._queue_path()
        action = _approved_action(queue_path=str(queue_path), token="TOKEN_1")
        action["commit_payload"]["kwargs"]["context"]["approval_token"] = "OTHER"

        result = execute_sell_runtime_commit(_approval(action))

        self.assertEqual(result["status"], "INVALID")

    def test_bad_path_invalid(self):
        queue_path = self._queue_path()
        action = _approved_action(queue_path=str(queue_path))
        action["commit_payload"]["args"]["queue_path"] = str(queue_path) + ".other"

        result = execute_sell_runtime_commit(_approval(action))

        self.assertEqual(result["status"], "INVALID")

    def test_identity_mismatch_invalid(self):
        queue_path = self._queue_path()
        action = _approved_action(queue_path=str(queue_path))
        action["commit_payload"]["args"]["queue_write_preview_result"]["order_queued_record_preview"]["request_hash"] = "OTHER"

        result = execute_sell_runtime_commit(_approval(action))

        self.assertEqual(result["status"], "INVALID")

    def test_commit_boundary_function_mismatch_invalid(self):
        queue_path = self._queue_path()
        action = _approved_action(queue_path=str(queue_path))
        action["commit_boundary_function"] = "other.commit"

        result = execute_sell_runtime_commit(_approval(action))

        self.assertEqual(result["status"], "INVALID")

    def test_commit_payload_function_mismatch_invalid(self):
        queue_path = self._queue_path()
        action = _approved_action(queue_path=str(queue_path))
        action["commit_payload"]["function"] = "other.commit"

        result = execute_sell_runtime_commit(_approval(action))

        self.assertEqual(result["status"], "INVALID")

    def test_duplicate_request_hash_blocked(self):
        queue_path = self._queue_path()
        existing = _record(request_hash="r" * 64, order_id="OTHER", lock_id="OTHER")
        queue_path.write_text(json.dumps({"version": 1, "updated_at": "before", "orders": [existing]}), encoding="utf-8")

        result = execute_sell_runtime_commit(_approval(_approved_action(queue_path=str(queue_path))))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("duplicate request_hash", result["execution_results"][0]["reasons"])

    def test_duplicate_lock_id_blocked(self):
        queue_path = self._queue_path()
        existing = _record(request_hash="x" * 64, order_id="OTHER", lock_id="LOCK_1")
        queue_path.write_text(json.dumps({"version": 1, "updated_at": "before", "orders": [existing]}), encoding="utf-8")

        result = execute_sell_runtime_commit(_approval(_approved_action(queue_path=str(queue_path))))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("duplicate lock_id", result["execution_results"][0]["reasons"])

    def test_duplicate_order_id_blocked(self):
        queue_path = self._queue_path()
        existing = _record(request_hash="x" * 64, order_id="ORDER_1", lock_id="OTHER")
        queue_path.write_text(json.dumps({"version": 1, "updated_at": "before", "orders": [existing]}), encoding="utf-8")

        result = execute_sell_runtime_commit(_approval(_approved_action(queue_path=str(queue_path))))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("duplicate order_id", result["execution_results"][0]["reasons"])

    def test_multi_candidate_execution_blocked(self):
        queue_path = self._queue_path()
        first = _approved_action(_record(order_id="ORDER_1", candidate_id="CANDIDATE_1"), queue_path=str(queue_path))
        second = _approved_action(_record(order_id="ORDER_2", candidate_id="CANDIDATE_2", request_hash="s" * 64, lock_id="LOCK_2", execution_id="EXEC_2"), queue_path=str(queue_path))

        result = execute_sell_runtime_commit(_approval(first, second))

        self.assertEqual(result["status"], "BLOCKED")
        data = json.loads(queue_path.read_text(encoding="utf-8"))
        self.assertEqual(data["orders"], [])

    def test_input_mutation_does_not_occur(self):
        queue_path = self._queue_path()
        approval = _approval(_approved_action(queue_path=str(queue_path)))
        original = deepcopy(approval)

        result = execute_sell_runtime_commit(approval)
        result["approval_snapshot"]["status"] = "MUTATED"

        self.assertEqual(approval, original)

    def test_send_order_and_broker_not_called(self):
        queue_path = self._queue_path()
        with mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub:
            result = execute_sell_runtime_commit(_approval(_approved_action(queue_path=str(queue_path))))

        self.assertEqual(result["status"], "READY")
        send_order_stub.assert_not_called()
        self.assertFalse(result["send_order"])
        self.assertFalse(result["broker_api_called"])
        self.assertFalse(result["order_request_created"])
        self.assertFalse(result["actual_order_sent"])
        self.assertFalse(result["real_ready_state_changed"])

    def test_project_runtime_order_queue_not_touched(self):
        runtime_queue = Path(__file__).resolve().parents[1] / "runtime" / "order_queue.json"
        before = hashlib.sha256(runtime_queue.read_bytes()).hexdigest()
        queue_path = self._queue_path()

        execute_sell_runtime_commit(_approval(_approved_action(queue_path=str(queue_path))))

        after = hashlib.sha256(runtime_queue.read_bytes()).hexdigest()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
