from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import unittest
from unittest import mock

import sell_runtime_commit_real_executor_approval_gate_preview as subject
from sell_runtime_commit_real_executor_approval_gate_preview import (
    build_sell_runtime_commit_real_executor_approval_gate_preview,
)


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
    }


def _real_action(record: dict | None = None, *, status: str = "READY") -> dict:
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
        "status": status,
        "executor_action": "PREVIEW_COMMIT_EXECUTION_QUEUE_WRITE",
        "commit_boundary_function": "execution_queue_writer.commit_execution_queue_write",
        "commit_boundary_called": False,
        "function_called": False,
        "source_signal_id": payload["source_signal_id"],
        "order_id": payload["order_id"],
        "candidate_id": payload["candidate_id"],
        "queue_pending_id": payload["queue_pending_id"],
        "execution_id": payload["execution_id"],
        "request_hash": payload["request_hash"],
        "lock_id": payload["lock_id"],
        "execution_request": deepcopy(payload["execution_request"]),
        "order_queued_record_preview": deepcopy(payload),
        "commit_payload": {
            "function": "execution_queue_writer.commit_execution_queue_write",
            "args": {
                "queue_write_preview_result": queue_write_preview,
                "queue_path": None,
            },
            "kwargs": {
                "backup": True,
                "context": None,
            },
            "queue_path_required": True,
            "manual_queue_write_confirmation_required": True,
            "called": False,
        },
        "source_execution_action": {},
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


def _preview(
    *actions: dict,
    status: str = "READY",
    ready: bool | None = None,
    commit_allowed: bool | None = None,
    warnings: list[str] | None = None,
    reasons: list[str] | None = None,
) -> dict:
    action_list = list(actions or [_real_action()])
    if ready is None:
        ready = status == "READY"
    if commit_allowed is None:
        commit_allowed = status == "READY"
    return {
        "preview_type": "SELL_RUNTIME_COMMIT_REAL_EXECUTOR_PREVIEW",
        "ownership": "MASTER_ENGINE",
        "domain": "Execution / Runtime Commit Real Executor Preview",
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
        "real_executor_preview_ready": ready,
        "commit_allowed": commit_allowed,
        "commit_boundary": {
            "function": "execution_queue_writer.commit_execution_queue_write",
            "called": False,
            "runtime_write": False,
            "queue_write": False,
            "queue_committed": False,
        },
        "execution_plan_snapshot": {},
        "real_executor_actions": action_list,
        "blocked_real_executor_actions": [],
        "source_summary": {},
        "warnings": list(warnings or []),
        "reasons": list(reasons or []),
        "summary": {
            "real_executor_ready_count": sum(1 for item in action_list if isinstance(item, dict) and item.get("status") == "READY"),
            "real_executor_blocked_count": 0,
            "real_executor_invalid_count": 0,
            "real_executor_action_count": len(action_list),
            "blocked_real_executor_action_count": 0,
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


def _context(*candidate_ids: str, user_approved: bool = True, queue_path: str | Path = "runtime/order_queue.json", token: str = "TOKEN_1") -> dict:
    return {
        "user_approved": user_approved,
        "queue_path": queue_path,
        "approval_token": token,
        "approved_candidate_ids": list(candidate_ids or ["CANDIDATE_ORDER_1"]),
    }


class SellRuntimeCommitRealExecutorApprovalGatePreviewTests(unittest.TestCase):
    def test_explicit_approval_valid_queue_path_and_token_is_ready(self):
        result = build_sell_runtime_commit_real_executor_approval_gate_preview(_preview(), _context())

        self.assertEqual(result["approval_type"], "SELL_RUNTIME_COMMIT_REAL_EXECUTOR_APPROVAL_GATE_PREVIEW")
        self.assertEqual(result["ownership"], "MASTER_ENGINE")
        self.assertEqual(result["domain"], "Execution / Runtime Commit Real Executor Approval Gate Preview")
        self.assertIsNone(result["routine_dependency"])
        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["approval_granted"])
        self.assertTrue(result["commit_allowed"])

    def test_missing_approval_context_is_blocked(self):
        result = build_sell_runtime_commit_real_executor_approval_gate_preview(_preview())

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["approval_granted"])

    def test_user_approved_false_is_blocked(self):
        result = build_sell_runtime_commit_real_executor_approval_gate_preview(_preview(), _context(user_approved=False))

        self.assertEqual(result["status"], "BLOCKED")

    def test_missing_queue_path_is_blocked(self):
        result = build_sell_runtime_commit_real_executor_approval_gate_preview(_preview(), _context(queue_path=""))

        self.assertEqual(result["status"], "BLOCKED")

    def test_missing_approval_token_is_blocked(self):
        result = build_sell_runtime_commit_real_executor_approval_gate_preview(_preview(), _context(token=""))

        self.assertEqual(result["status"], "BLOCKED")

    def test_partial_candidate_approval_is_invalid(self):
        first = _real_action(_record(order_id="ORDER_1", candidate_id="CANDIDATE_1"))
        second = _real_action(_record(order_id="ORDER_2", candidate_id="CANDIDATE_2"))

        result = build_sell_runtime_commit_real_executor_approval_gate_preview(_preview(first, second), _context("CANDIDATE_1"))

        self.assertEqual(result["status"], "INVALID")

    def test_candidate_order_mismatch_is_invalid(self):
        first = _real_action(_record(order_id="ORDER_1", candidate_id="CANDIDATE_1"))
        second = _real_action(_record(order_id="ORDER_2", candidate_id="CANDIDATE_2"))

        result = build_sell_runtime_commit_real_executor_approval_gate_preview(
            _preview(first, second),
            _context("CANDIDATE_2", "CANDIDATE_1"),
        )

        self.assertEqual(result["status"], "INVALID")

    def test_upstream_blocked_is_blocked(self):
        result = build_sell_runtime_commit_real_executor_approval_gate_preview(
            _preview(status="BLOCKED", ready=False, commit_allowed=False),
            _context(),
        )

        self.assertEqual(result["status"], "BLOCKED")

    def test_upstream_invalid_is_invalid(self):
        result = build_sell_runtime_commit_real_executor_approval_gate_preview(
            _preview(status="INVALID", ready=False, commit_allowed=False),
            _context(),
        )

        self.assertEqual(result["status"], "INVALID")

    def test_upstream_summary_mismatch_is_invalid(self):
        preview = _preview()
        preview["summary"]["real_executor_ready_count"] = 2

        result = build_sell_runtime_commit_real_executor_approval_gate_preview(preview, _context())

        self.assertEqual(result["status"], "INVALID")

    def test_identity_mismatch_is_invalid(self):
        action = _real_action()
        action["execution_request"]["request_hash"] = "OTHER"

        result = build_sell_runtime_commit_real_executor_approval_gate_preview(_preview(action), _context())

        self.assertEqual(result["status"], "INVALID")

    def test_commit_boundary_function_mismatch_is_invalid(self):
        action = _real_action()
        action["commit_boundary_function"] = "other.commit"

        result = build_sell_runtime_commit_real_executor_approval_gate_preview(_preview(action), _context())

        self.assertEqual(result["status"], "INVALID")

    def test_commit_payload_function_mismatch_is_invalid(self):
        action = _real_action()
        action["commit_payload"]["function"] = "other.commit"

        result = build_sell_runtime_commit_real_executor_approval_gate_preview(_preview(action), _context())

        self.assertEqual(result["status"], "INVALID")

    def test_commit_payload_called_true_is_invalid(self):
        action = _real_action()
        action["commit_payload"]["called"] = True

        result = build_sell_runtime_commit_real_executor_approval_gate_preview(_preview(action), _context())

        self.assertEqual(result["status"], "INVALID")

    def test_commit_boundary_called_true_is_invalid(self):
        action = _real_action()
        action["commit_boundary_called"] = True

        result = build_sell_runtime_commit_real_executor_approval_gate_preview(_preview(action), _context())

        self.assertEqual(result["status"], "INVALID")

    def test_function_called_true_is_invalid(self):
        action = _real_action()
        action["function_called"] = True

        result = build_sell_runtime_commit_real_executor_approval_gate_preview(_preview(action), _context())

        self.assertEqual(result["status"], "INVALID")

    def test_queue_path_already_present_in_preview_payload_is_invalid(self):
        action = _real_action()
        action["commit_payload"]["args"]["queue_path"] = "runtime/order_queue.json"

        result = build_sell_runtime_commit_real_executor_approval_gate_preview(_preview(action), _context())

        self.assertEqual(result["status"], "INVALID")

    def test_safety_flag_violation_is_invalid(self):
        preview = _preview()
        preview["queue_write"] = True

        result = build_sell_runtime_commit_real_executor_approval_gate_preview(preview, _context())

        self.assertEqual(result["status"], "INVALID")

    def test_action_safety_flag_violation_is_invalid(self):
        action = _real_action()
        action["runtime_write"] = True

        result = build_sell_runtime_commit_real_executor_approval_gate_preview(_preview(action), _context())

        self.assertEqual(result["status"], "INVALID")

    def test_multiple_candidates_order_preserved(self):
        first = _real_action(_record(order_id="ORDER_1", candidate_id="CANDIDATE_1"))
        second = _real_action(_record(order_id="ORDER_2", candidate_id="CANDIDATE_2"))

        result = build_sell_runtime_commit_real_executor_approval_gate_preview(
            _preview(first, second),
            _context("CANDIDATE_1", "CANDIDATE_2"),
        )

        self.assertEqual(result["status"], "READY")
        self.assertEqual([item["candidate_id"] for item in result["approved_real_executor_actions"]], ["CANDIDATE_1", "CANDIDATE_2"])
        self.assertFalse(result["summary"]["priority_selected"])
        self.assertFalse(result["summary"]["auto_selected"])

    def test_approved_action_preserves_required_fields(self):
        result = build_sell_runtime_commit_real_executor_approval_gate_preview(_preview(), _context())

        action = result["approved_real_executor_actions"][0]
        self.assertEqual(action["status"], "READY")
        self.assertEqual(action["approval_action"], "APPROVE_REAL_EXECUTOR_COMMIT_PREVIEW")
        self.assertEqual(action["source_signal_id"], "SIG_1")
        self.assertEqual(action["order_id"], "ORDER_1")
        self.assertEqual(action["candidate_id"], "CANDIDATE_ORDER_1")
        self.assertEqual(action["queue_pending_id"], "QUEUE_PENDING_ORDER_1")
        self.assertEqual(action["execution_id"], "EXEC_1")
        self.assertEqual(action["request_hash"], "r" * 64)
        self.assertEqual(action["lock_id"], "LOCK_1")
        self.assertEqual(action["commit_boundary_function"], "execution_queue_writer.commit_execution_queue_write")
        self.assertEqual(action["approval_token"], "TOKEN_1")
        self.assertEqual(action["queue_path"], "runtime/order_queue.json")
        self.assertFalse(action["runtime_write"])

    def test_approved_payload_injects_queue_path_and_manual_context(self):
        result = build_sell_runtime_commit_real_executor_approval_gate_preview(_preview(), _context())

        payload = result["approved_real_executor_actions"][0]["commit_payload"]
        self.assertEqual(payload["args"]["queue_path"], "runtime/order_queue.json")
        self.assertTrue(payload["kwargs"]["context"]["manual_queue_write_confirmed"])
        self.assertEqual(payload["kwargs"]["context"]["approval_token"], "TOKEN_1")
        self.assertFalse(payload["called"])

    def test_path_queue_path_supported(self):
        result = build_sell_runtime_commit_real_executor_approval_gate_preview(
            _preview(),
            _context(queue_path=Path("runtime/order_queue.json")),
        )

        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["approved_real_executor_actions"][0]["queue_path"], "runtime\\order_queue.json")

    def test_input_type_invalid(self):
        result = build_sell_runtime_commit_real_executor_approval_gate_preview("bad", _context())  # type: ignore[arg-type]

        self.assertEqual(result["status"], "INVALID")

    def test_context_type_invalid(self):
        result = build_sell_runtime_commit_real_executor_approval_gate_preview(_preview(), "bad")  # type: ignore[arg-type]

        self.assertEqual(result["status"], "INVALID")

    def test_preview_type_required(self):
        preview = _preview()
        preview["preview_type"] = "OTHER"

        result = build_sell_runtime_commit_real_executor_approval_gate_preview(preview, _context())

        self.assertEqual(result["status"], "INVALID")

    def test_preview_only_required(self):
        preview = _preview()
        preview["preview_only"] = False

        result = build_sell_runtime_commit_real_executor_approval_gate_preview(preview, _context())

        self.assertEqual(result["status"], "INVALID")

    def test_ready_requires_ready_flag(self):
        result = build_sell_runtime_commit_real_executor_approval_gate_preview(_preview(ready=False), _context())

        self.assertEqual(result["status"], "INVALID")

    def test_ready_requires_commit_allowed(self):
        result = build_sell_runtime_commit_real_executor_approval_gate_preview(_preview(commit_allowed=False), _context())

        self.assertEqual(result["status"], "INVALID")

    def test_real_executor_actions_must_be_list(self):
        preview = _preview()
        preview["real_executor_actions"] = {}

        result = build_sell_runtime_commit_real_executor_approval_gate_preview(preview, _context())

        self.assertEqual(result["status"], "INVALID")

    def test_real_executor_actions_must_not_be_empty(self):
        preview = _preview()
        preview["real_executor_actions"] = []
        preview["summary"]["real_executor_ready_count"] = 0
        preview["summary"]["real_executor_action_count"] = 0

        result = build_sell_runtime_commit_real_executor_approval_gate_preview(preview, _context())

        self.assertEqual(result["status"], "INVALID")

    def test_blocked_real_executor_actions_must_be_list(self):
        preview = _preview()
        preview["blocked_real_executor_actions"] = {}

        result = build_sell_runtime_commit_real_executor_approval_gate_preview(preview, _context())

        self.assertEqual(result["status"], "INVALID")

    def test_blocked_real_executor_actions_existing_is_invalid(self):
        preview = _preview()
        preview["blocked_real_executor_actions"] = [{"status": "BLOCKED"}]

        result = build_sell_runtime_commit_real_executor_approval_gate_preview(preview, _context())

        self.assertEqual(result["status"], "INVALID")

    def test_approved_candidate_ids_type_required(self):
        context = _context()
        context["approved_candidate_ids"] = "CANDIDATE_ORDER_1"

        result = build_sell_runtime_commit_real_executor_approval_gate_preview(_preview(), context)

        self.assertEqual(result["status"], "INVALID")

    def test_approved_candidate_ids_non_empty_required(self):
        result = build_sell_runtime_commit_real_executor_approval_gate_preview(_preview(), _context())
        self.assertEqual(result["status"], "READY")

        empty_context = _context()
        empty_context["approved_candidate_ids"] = []
        blocked = build_sell_runtime_commit_real_executor_approval_gate_preview(_preview(), empty_context)
        self.assertEqual(blocked["status"], "BLOCKED")

    def test_approved_candidate_ids_non_empty_strings_required(self):
        context = _context()
        context["approved_candidate_ids"] = [""]

        result = build_sell_runtime_commit_real_executor_approval_gate_preview(_preview(), context)

        self.assertEqual(result["status"], "INVALID")

    def test_payload_queue_write_preview_record_mismatch_invalid(self):
        action = _real_action()
        action["commit_payload"]["args"]["queue_write_preview_result"]["order_queued_record_preview"]["candidate_id"] = "OTHER"

        result = build_sell_runtime_commit_real_executor_approval_gate_preview(_preview(action), _context())

        self.assertEqual(result["status"], "INVALID")

    def test_warnings_reasons_and_summary_forwarded(self):
        result = build_sell_runtime_commit_real_executor_approval_gate_preview(_preview(warnings=["w"], reasons=["r"]), _context())

        self.assertIn("w", result["warnings"])
        self.assertIn("r", result["reasons"])
        self.assertEqual(result["source_summary"]["real_executor_ready_count"], 1)
        self.assertEqual(result["summary"]["approval_ready_count"], 1)

    def test_input_mutation_does_not_occur(self):
        preview = _preview()
        context = _context()
        original_preview = deepcopy(preview)
        original_context = deepcopy(context)

        result = build_sell_runtime_commit_real_executor_approval_gate_preview(preview, context)
        result["approved_real_executor_actions"][0]["queue_path"] = "MUTATED"

        self.assertEqual(preview, original_preview)
        self.assertEqual(context, original_context)

    def test_no_commit_function_runtime_file_or_broker_call(self):
        with (
            mock.patch("execution_queue_writer.commit_execution_queue_write") as commit_write,
            mock.patch("pathlib.Path.read_text") as read_text,
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
        ):
            result = build_sell_runtime_commit_real_executor_approval_gate_preview(_preview(), _context())

        self.assertEqual(result["status"], "READY")
        commit_write.assert_not_called()
        read_text.assert_not_called()
        write_text.assert_not_called()
        open_mock.assert_not_called()
        send_order_stub.assert_not_called()

    def test_module_does_not_import_or_call_commit_writer(self):
        source = Path(subject.__file__).read_text(encoding="utf-8")

        self.assertIn("execution_queue_writer.commit_execution_queue_write", source)
        self.assertNotIn("from execution_queue_writer import", source)
        self.assertNotIn("import execution_queue_writer", source)


if __name__ == "__main__":
    unittest.main()
