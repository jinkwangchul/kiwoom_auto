from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import unittest
from unittest import mock

import sell_runtime_commit_real_executor_preview as subject
from sell_runtime_commit_real_executor_preview import build_sell_runtime_commit_real_executor_preview


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


def _execution_action(record: dict | None = None, *, status: str = "READY") -> dict:
    payload = record or _record()
    return {
        "status": status,
        "plan_action": "RUNTIME_COMMIT",
        "source_signal_id": payload["source_signal_id"],
        "order_id": payload["order_id"],
        "candidate_id": payload["candidate_id"],
        "queue_pending_id": payload["queue_pending_id"],
        "execution_id": payload["execution_id"],
        "request_hash": payload["request_hash"],
        "lock_id": payload["lock_id"],
        "execution_request": deepcopy(payload["execution_request"]),
        "order_queued_record_preview": deepcopy(payload),
        "source_approval_action": {},
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


def _plan(
    *actions: dict,
    status: str = "READY",
    commit_allowed: bool | None = None,
    execution_plan_ready: bool | None = None,
    warnings: list[str] | None = None,
    reasons: list[str] | None = None,
) -> dict:
    action_list = list(actions or [_execution_action()])
    if commit_allowed is None:
        commit_allowed = status == "READY"
    if execution_plan_ready is None:
        execution_plan_ready = status == "READY"
    return {
        "plan_type": "SELL_RUNTIME_COMMIT_EXECUTION_PLAN",
        "ownership": "MASTER_ENGINE",
        "domain": "Execution / Runtime Commit Execution Plan",
        "routine_dependency": None,
        "preview_only": True,
        "plan_only": True,
        "execution_connected": False,
        "runtime_write": False,
        "queue_write": False,
        "file_write": False,
        "send_order": False,
        "broker_api_called": False,
        "queue_committed": False,
        "actual_order_sent": False,
        "order_request_created": False,
        "real_ready_state_changed": False,
        "runtime_commit_executed": False,
        "status": status,
        "execution_plan_ready": execution_plan_ready,
        "commit_allowed": commit_allowed,
        "approval_snapshot": {},
        "execution_actions": action_list,
        "blocked_execution_actions": [],
        "source_summary": {},
        "warnings": list(warnings or []),
        "reasons": list(reasons or []),
        "summary": {
            "execution_ready_count": sum(1 for item in action_list if isinstance(item, dict) and item.get("status") == "READY"),
            "execution_blocked_count": 0,
            "execution_invalid_count": 0,
            "execution_action_count": len(action_list),
            "blocked_execution_action_count": 0,
            "runtime_write": False,
            "queue_write": False,
            "queue_committed": False,
            "send_order": False,
            "broker_api_called": False,
            "runtime_commit_executed": False,
            "priority_selected": False,
            "auto_selected": False,
        },
    }


class SellRuntimeCommitRealExecutorPreviewTests(unittest.TestCase):
    def test_ready_plan_creates_real_executor_preview(self):
        result = build_sell_runtime_commit_real_executor_preview(_plan())

        self.assertEqual(result["preview_type"], "SELL_RUNTIME_COMMIT_REAL_EXECUTOR_PREVIEW")
        self.assertEqual(result["ownership"], "MASTER_ENGINE")
        self.assertEqual(result["domain"], "Execution / Runtime Commit Real Executor Preview")
        self.assertIsNone(result["routine_dependency"])
        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["real_executor_preview_ready"])
        self.assertTrue(result["commit_allowed"])

    def test_blocked_plan_blocks_preview(self):
        result = build_sell_runtime_commit_real_executor_preview(
            _plan(status="BLOCKED", commit_allowed=False, execution_plan_ready=False)
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["commit_allowed"])

    def test_invalid_plan_invalidates_preview(self):
        result = build_sell_runtime_commit_real_executor_preview(
            _plan(status="INVALID", commit_allowed=False, execution_plan_ready=False)
        )

        self.assertEqual(result["status"], "INVALID")
        self.assertFalse(result["real_executor_preview_ready"])

    def test_ready_requires_commit_allowed(self):
        result = build_sell_runtime_commit_real_executor_preview(_plan(commit_allowed=False))

        self.assertEqual(result["status"], "INVALID")

    def test_ready_requires_execution_plan_ready(self):
        result = build_sell_runtime_commit_real_executor_preview(_plan(execution_plan_ready=False))

        self.assertEqual(result["status"], "INVALID")

    def test_input_type_required(self):
        result = build_sell_runtime_commit_real_executor_preview("bad")  # type: ignore[arg-type]

        self.assertEqual(result["status"], "INVALID")

    def test_plan_type_required(self):
        plan = _plan()
        plan["plan_type"] = "OTHER"

        result = build_sell_runtime_commit_real_executor_preview(plan)

        self.assertEqual(result["status"], "INVALID")

    def test_preview_only_required(self):
        plan = _plan()
        plan["preview_only"] = False

        result = build_sell_runtime_commit_real_executor_preview(plan)

        self.assertEqual(result["status"], "INVALID")

    def test_safety_flag_violation_is_invalid(self):
        plan = _plan()
        plan["runtime_write"] = True

        result = build_sell_runtime_commit_real_executor_preview(plan)

        self.assertEqual(result["status"], "INVALID")

    def test_execution_actions_must_be_list(self):
        plan = _plan()
        plan["execution_actions"] = {}

        result = build_sell_runtime_commit_real_executor_preview(plan)

        self.assertEqual(result["status"], "INVALID")

    def test_execution_actions_must_not_be_empty(self):
        plan = _plan()
        plan["execution_actions"] = []
        plan["summary"]["execution_ready_count"] = 0
        plan["summary"]["execution_action_count"] = 0

        result = build_sell_runtime_commit_real_executor_preview(plan)

        self.assertEqual(result["status"], "INVALID")

    def test_blocked_execution_actions_must_be_list(self):
        plan = _plan()
        plan["blocked_execution_actions"] = {}

        result = build_sell_runtime_commit_real_executor_preview(plan)

        self.assertEqual(result["status"], "INVALID")

    def test_blocked_execution_actions_existing_is_invalid(self):
        plan = _plan()
        plan["blocked_execution_actions"] = [{"status": "BLOCKED"}]

        result = build_sell_runtime_commit_real_executor_preview(plan)

        self.assertEqual(result["status"], "INVALID")

    def test_summary_count_mismatch_is_invalid(self):
        plan = _plan()
        plan["summary"]["execution_ready_count"] = 2

        result = build_sell_runtime_commit_real_executor_preview(plan)

        self.assertEqual(result["status"], "INVALID")

    def test_action_must_be_dict(self):
        result = build_sell_runtime_commit_real_executor_preview(_plan("bad"))  # type: ignore[arg-type]

        self.assertEqual(result["status"], "INVALID")

    def test_action_status_invalid_is_invalid(self):
        result = build_sell_runtime_commit_real_executor_preview(_plan(_execution_action(status="INVALID")))

        self.assertEqual(result["status"], "INVALID")

    def test_action_status_blocked_is_invalid_overall(self):
        result = build_sell_runtime_commit_real_executor_preview(_plan(_execution_action(status="BLOCKED")))

        self.assertEqual(result["status"], "INVALID")

    def test_action_plan_action_required(self):
        action = _execution_action()
        action["plan_action"] = "OTHER"

        result = build_sell_runtime_commit_real_executor_preview(_plan(action))

        self.assertEqual(result["status"], "INVALID")

    def test_action_safety_flag_violation_is_invalid(self):
        action = _execution_action()
        action["queue_write"] = True

        result = build_sell_runtime_commit_real_executor_preview(_plan(action))

        self.assertEqual(result["status"], "INVALID")

    def test_identity_missing_is_invalid(self):
        action = _execution_action()
        action["request_hash"] = ""

        result = build_sell_runtime_commit_real_executor_preview(_plan(action))

        self.assertEqual(result["status"], "INVALID")

    def test_execution_request_required(self):
        action = _execution_action()
        action["execution_request"] = {}

        result = build_sell_runtime_commit_real_executor_preview(_plan(action))

        self.assertEqual(result["status"], "INVALID")

    def test_order_queued_record_preview_required(self):
        action = _execution_action()
        action["order_queued_record_preview"] = {}

        result = build_sell_runtime_commit_real_executor_preview(_plan(action))

        self.assertEqual(result["status"], "INVALID")

    def test_identity_mismatch_is_invalid(self):
        action = _execution_action()
        action["execution_request"]["lock_id"] = "OTHER"

        result = build_sell_runtime_commit_real_executor_preview(_plan(action))

        self.assertEqual(result["status"], "INVALID")

    def test_record_identity_mismatch_is_invalid(self):
        action = _execution_action()
        action["order_queued_record_preview"]["order_id"] = "OTHER"

        result = build_sell_runtime_commit_real_executor_preview(_plan(action))

        self.assertEqual(result["status"], "INVALID")

    def test_multiple_actions_preserve_order(self):
        first = _execution_action(_record(source_signal_id="SIG_1", order_id="ORDER_1"))
        second = _execution_action(_record(source_signal_id="SIG_2", order_id="ORDER_2"))

        result = build_sell_runtime_commit_real_executor_preview(_plan(first, second))

        self.assertEqual(result["status"], "READY")
        self.assertEqual([item["order_id"] for item in result["real_executor_actions"]], ["ORDER_1", "ORDER_2"])
        self.assertFalse(result["summary"]["priority_selected"])
        self.assertFalse(result["summary"]["auto_selected"])

    def test_commit_boundary_function_and_payload_created_as_preview_only(self):
        result = build_sell_runtime_commit_real_executor_preview(_plan())

        action = result["real_executor_actions"][0]
        payload = action["commit_payload"]
        self.assertEqual(result["commit_boundary"]["function"], "execution_queue_writer.commit_execution_queue_write")
        self.assertFalse(result["commit_boundary"]["called"])
        self.assertEqual(action["commit_boundary_function"], "execution_queue_writer.commit_execution_queue_write")
        self.assertFalse(action["commit_boundary_called"])
        self.assertFalse(action["function_called"])
        self.assertEqual(payload["function"], "execution_queue_writer.commit_execution_queue_write")
        self.assertFalse(payload["called"])
        self.assertTrue(payload["queue_path_required"])
        self.assertTrue(payload["manual_queue_write_confirmation_required"])

    def test_payload_contains_queue_write_preview_result(self):
        result = build_sell_runtime_commit_real_executor_preview(_plan())

        queue_write_preview = result["real_executor_actions"][0]["commit_payload"]["args"]["queue_write_preview_result"]
        self.assertTrue(queue_write_preview["write_preview"])
        self.assertEqual(queue_write_preview["write_stage"], "order_queued_record_preview_created")
        self.assertEqual(queue_write_preview["next_stage"], "QUEUE_WRITE_REQUIRED")
        self.assertTrue(queue_write_preview["preview_only"])
        self.assertTrue(queue_write_preview["no_write"])
        self.assertEqual(queue_write_preview["order_queued_record_preview"]["order_id"], "ORDER_1")

    def test_identity_preserved_in_preview_action(self):
        action = _execution_action(
            _record(
                source_signal_id="SIG_X",
                order_id="ORDER_X",
                candidate_id="CAND_X",
                queue_pending_id="QUEUE_X",
                execution_id="EXEC_X",
                request_hash="h" * 64,
                lock_id="LOCK_X",
            )
        )

        result = build_sell_runtime_commit_real_executor_preview(_plan(action))

        preview = result["real_executor_actions"][0]
        self.assertEqual(preview["source_signal_id"], "SIG_X")
        self.assertEqual(preview["order_id"], "ORDER_X")
        self.assertEqual(preview["candidate_id"], "CAND_X")
        self.assertEqual(preview["queue_pending_id"], "QUEUE_X")
        self.assertEqual(preview["execution_id"], "EXEC_X")
        self.assertEqual(preview["request_hash"], "h" * 64)
        self.assertEqual(preview["lock_id"], "LOCK_X")

    def test_warnings_reasons_and_summary_forwarded(self):
        result = build_sell_runtime_commit_real_executor_preview(_plan(warnings=["w"], reasons=["r"]))

        self.assertIn("w", result["warnings"])
        self.assertIn("r", result["reasons"])
        self.assertEqual(result["source_summary"]["execution_ready_count"], 1)
        self.assertEqual(result["summary"]["real_executor_ready_count"], 1)

    def test_input_mutation_does_not_occur(self):
        plan = _plan()
        original = deepcopy(plan)

        result = build_sell_runtime_commit_real_executor_preview(plan)
        result["real_executor_actions"][0]["order_id"] = "MUTATED"

        self.assertEqual(plan, original)

    def test_deepcopy_prevents_payload_mutation(self):
        plan = _plan()

        result = build_sell_runtime_commit_real_executor_preview(plan)
        result["real_executor_actions"][0]["commit_payload"]["args"]["queue_write_preview_result"]["order_queued_record_preview"][
            "order_id"
        ] = "MUTATED"

        self.assertEqual(plan["execution_actions"][0]["order_id"], "ORDER_1")

    def test_runtime_queue_and_send_flags_remain_false(self):
        result = build_sell_runtime_commit_real_executor_preview(_plan())

        for flag in (
            "execution_connected",
            "runtime_write",
            "queue_write",
            "file_write",
            "queue_committed",
            "send_order",
            "broker_api_called",
            "order_request_created",
            "real_ready_state_changed",
            "runtime_commit_executed",
        ):
            self.assertFalse(result[flag], flag)
            self.assertFalse(result["summary"].get(flag, False), flag)
        self.assertFalse(result["real_executor_actions"][0]["runtime_write"])
        self.assertFalse(result["real_executor_actions"][0]["queue_committed"])

    def test_no_commit_function_file_or_broker_call(self):
        with (
            mock.patch("execution_queue_writer.commit_execution_queue_write") as commit_write,
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
        ):
            result = build_sell_runtime_commit_real_executor_preview(_plan())

        self.assertEqual(result["status"], "READY")
        commit_write.assert_not_called()
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
