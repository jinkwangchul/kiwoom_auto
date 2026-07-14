from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import unittest
from unittest import mock

import sell_runtime_commit_execution_plan as subject
from sell_runtime_commit_execution_plan import build_sell_runtime_commit_execution_plan


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


def _approved_action(record: dict | None = None, *, status: str = "READY") -> dict:
    payload = record or _record()
    return {
        "status": status,
        "dryrun_action_ready": status == "READY",
        "candidate_index": 0,
        "action": "RUNTIME_COMMIT_DRY_RUN",
        "source_signal_id": payload["source_signal_id"],
        "order_id": payload["order_id"],
        "candidate_id": payload["candidate_id"],
        "queue_pending_id": payload["queue_pending_id"],
        "execution_id": payload["execution_id"],
        "request_hash": payload["request_hash"],
        "lock_id": payload["lock_id"],
        "execution_request": deepcopy(payload["execution_request"]),
        "order_queued_record_preview": deepcopy(payload),
        "runtime_write": False,
        "queue_write": False,
        "queue_committed": False,
        "send_order": False,
        "broker_api_called": False,
        "order_request_created": False,
        "real_ready_state_changed": False,
        "runtime_commit_executed": False,
        "reasons": [],
        "warnings": [],
    }


def _approval(
    *actions: dict,
    status: str = "READY",
    approval_granted: bool | None = None,
    commit_allowed: bool | None = None,
    warnings: list[str] | None = None,
    reasons: list[str] | None = None,
) -> dict:
    action_list = list(actions or [_approved_action()])
    if approval_granted is None:
        approval_granted = status == "READY"
    if commit_allowed is None:
        commit_allowed = status == "READY"
    return {
        "approval_type": "SELL_RUNTIME_COMMIT_APPROVAL_GATE",
        "ownership": "MASTER_ENGINE",
        "domain": "Execution / Runtime Commit Approval Gate",
        "routine_dependency": None,
        "preview_only": True,
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
        "approval_granted": approval_granted,
        "commit_allowed": commit_allowed,
        "runtime_commit_dryrun_snapshot": {},
        "approved_commit_actions": action_list,
        "blocked_approval_actions": [],
        "source_summary": {},
        "warnings": list(warnings or []),
        "reasons": list(reasons or []),
        "summary": {
            "approval_ready_count": sum(1 for item in action_list if item.get("status") == "READY"),
            "approval_blocked_count": 0,
            "approval_invalid_count": 0,
            "approved_action_count": len(action_list),
            "blocked_action_count": 0,
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


class SellRuntimeCommitExecutionPlanTests(unittest.TestCase):
    def test_all_approved_candidates_ready_creates_ready_plan(self):
        result = build_sell_runtime_commit_execution_plan(_approval())

        self.assertEqual(result["plan_type"], "SELL_RUNTIME_COMMIT_EXECUTION_PLAN")
        self.assertEqual(result["ownership"], "MASTER_ENGINE")
        self.assertEqual(result["domain"], "Execution / Runtime Commit Execution Plan")
        self.assertIsNone(result["routine_dependency"])
        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["execution_plan_ready"])
        self.assertTrue(result["commit_allowed"])
        self.assertTrue(result["plan_only"])

    def test_approval_blocked_blocks_plan(self):
        result = build_sell_runtime_commit_execution_plan(_approval(status="BLOCKED", approval_granted=False, commit_allowed=False))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["execution_plan_ready"])
        self.assertFalse(result["commit_allowed"])

    def test_approval_invalid_invalidates_plan(self):
        result = build_sell_runtime_commit_execution_plan(_approval(status="INVALID", approval_granted=False, commit_allowed=False))

        self.assertEqual(result["status"], "INVALID")
        self.assertFalse(result["execution_plan_ready"])

    def test_ready_approval_granted_false_is_invalid(self):
        result = build_sell_runtime_commit_execution_plan(_approval(approval_granted=False))

        self.assertEqual(result["status"], "INVALID")
        self.assertFalse(result["commit_allowed"])

    def test_ready_commit_allowed_false_is_invalid(self):
        result = build_sell_runtime_commit_execution_plan(_approval(commit_allowed=False))

        self.assertEqual(result["status"], "INVALID")

    def test_blocked_approval_with_grant_is_invalid(self):
        result = build_sell_runtime_commit_execution_plan(_approval(status="BLOCKED", approval_granted=True, commit_allowed=False))

        self.assertEqual(result["status"], "INVALID")

    def test_blocked_approval_actions_existing_is_invalid(self):
        approval = _approval()
        approval["blocked_approval_actions"] = [_approved_action(_record(order_id="ORDER_2"), status="BLOCKED")]

        result = build_sell_runtime_commit_execution_plan(approval)

        self.assertEqual(result["status"], "INVALID")

    def test_summary_count_mismatch_is_invalid(self):
        approval = _approval()
        approval["summary"]["approval_ready_count"] = 2

        result = build_sell_runtime_commit_execution_plan(approval)

        self.assertEqual(result["status"], "INVALID")

    def test_approval_blocked_count_mismatch_is_invalid(self):
        approval = _approval()
        approval["summary"]["approval_blocked_count"] = 1

        result = build_sell_runtime_commit_execution_plan(approval)

        self.assertEqual(result["status"], "INVALID")

    def test_approved_action_count_mismatch_is_invalid(self):
        approval = _approval()
        approval["summary"]["approved_action_count"] = 2

        result = build_sell_runtime_commit_execution_plan(approval)

        self.assertEqual(result["status"], "INVALID")

    def test_identity_missing_is_invalid(self):
        action = _approved_action()
        action["lock_id"] = ""

        result = build_sell_runtime_commit_execution_plan(_approval(action))

        self.assertEqual(result["status"], "INVALID")

    def test_identity_mismatch_is_invalid(self):
        action = _approved_action()
        action["execution_request"]["request_hash"] = "other"

        result = build_sell_runtime_commit_execution_plan(_approval(action))

        self.assertEqual(result["status"], "INVALID")

    def test_record_identity_mismatch_is_invalid(self):
        action = _approved_action()
        action["order_queued_record_preview"]["order_id"] = "OTHER"

        result = build_sell_runtime_commit_execution_plan(_approval(action))

        self.assertEqual(result["status"], "INVALID")

    def test_execution_request_required(self):
        action = _approved_action()
        action["execution_request"] = {}

        result = build_sell_runtime_commit_execution_plan(_approval(action))

        self.assertEqual(result["status"], "INVALID")

    def test_queued_record_required(self):
        action = _approved_action()
        action["order_queued_record_preview"] = {}

        result = build_sell_runtime_commit_execution_plan(_approval(action))

        self.assertEqual(result["status"], "INVALID")

    def test_safety_flag_violation_is_invalid(self):
        approval = _approval()
        approval["runtime_write"] = True

        result = build_sell_runtime_commit_execution_plan(approval)

        self.assertEqual(result["status"], "INVALID")

    def test_action_safety_flag_violation_is_invalid(self):
        action = _approved_action()
        action["send_order"] = True

        result = build_sell_runtime_commit_execution_plan(_approval(action))

        self.assertEqual(result["status"], "INVALID")

    def test_input_type_required(self):
        result = build_sell_runtime_commit_execution_plan("bad")  # type: ignore[arg-type]

        self.assertEqual(result["status"], "INVALID")

    def test_approval_type_required(self):
        approval = _approval()
        approval["approval_type"] = "OTHER"

        result = build_sell_runtime_commit_execution_plan(approval)

        self.assertEqual(result["status"], "INVALID")

    def test_preview_only_required(self):
        approval = _approval()
        approval["preview_only"] = False

        result = build_sell_runtime_commit_execution_plan(approval)

        self.assertEqual(result["status"], "INVALID")

    def test_approved_commit_actions_must_be_list(self):
        approval = _approval()
        approval["approved_commit_actions"] = {}

        result = build_sell_runtime_commit_execution_plan(approval)

        self.assertEqual(result["status"], "INVALID")

    def test_empty_approved_actions_blocked(self):
        approval = _approval()
        approval["approved_commit_actions"] = []
        approval["summary"]["approval_ready_count"] = 0
        approval["summary"]["approved_action_count"] = 0

        result = build_sell_runtime_commit_execution_plan(approval)

        self.assertEqual(result["status"], "BLOCKED")

    def test_blocked_approval_actions_must_be_list(self):
        approval = _approval()
        approval["blocked_approval_actions"] = {}

        result = build_sell_runtime_commit_execution_plan(approval)

        self.assertEqual(result["status"], "INVALID")

    def test_multiple_candidates_all_preserved(self):
        second = _approved_action(_record(source_signal_id="SIG_2", order_id="ORDER_2"))

        result = build_sell_runtime_commit_execution_plan(_approval(_approved_action(), second))

        self.assertEqual(result["status"], "READY")
        self.assertEqual(len(result["execution_actions"]), 2)
        self.assertEqual(result["execution_actions"][0]["order_id"], "ORDER_1")
        self.assertEqual(result["execution_actions"][1]["order_id"], "ORDER_2")
        self.assertFalse(result["summary"]["priority_selected"])
        self.assertFalse(result["summary"]["auto_selected"])

    def test_plan_action_structure_preserves_identity(self):
        action = _approved_action(
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

        result = build_sell_runtime_commit_execution_plan(_approval(action))

        plan = result["execution_actions"][0]
        self.assertEqual(plan["plan_action"], "RUNTIME_COMMIT")
        self.assertEqual(plan["source_signal_id"], "SIG_X")
        self.assertEqual(plan["order_id"], "ORDER_X")
        self.assertEqual(plan["candidate_id"], "CAND_X")
        self.assertEqual(plan["queue_pending_id"], "QUEUE_X")
        self.assertEqual(plan["execution_id"], "EXEC_X")
        self.assertEqual(plan["request_hash"], "h" * 64)
        self.assertEqual(plan["lock_id"], "LOCK_X")
        self.assertEqual(plan["source_approval_action"]["order_id"], "ORDER_X")

    def test_partial_plan_not_allowed(self):
        blocked = _approved_action(_record(order_id="ORDER_2"), status="BLOCKED")

        result = build_sell_runtime_commit_execution_plan(_approval(_approved_action(), blocked))

        self.assertEqual(result["status"], "INVALID")
        self.assertFalse(result["execution_plan_ready"])
        self.assertFalse(result["commit_allowed"])

    def test_warnings_reasons_and_summary_forwarded(self):
        result = build_sell_runtime_commit_execution_plan(_approval(warnings=["w1"], reasons=["r1"]))

        self.assertIn("w1", result["warnings"])
        self.assertIn("r1", result["reasons"])
        self.assertEqual(result["source_summary"]["approval_ready_count"], 1)
        self.assertEqual(result["summary"]["execution_ready_count"], 1)

    def test_input_mutation_does_not_occur(self):
        approval = _approval()
        original = deepcopy(approval)

        result = build_sell_runtime_commit_execution_plan(approval)
        result["execution_actions"][0]["order_id"] = "MUTATED"

        self.assertEqual(approval, original)

    def test_runtime_commit_not_performed(self):
        result = build_sell_runtime_commit_execution_plan(_approval())

        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["runtime_commit_executed"])
        self.assertFalse(result["summary"]["runtime_write"])
        self.assertFalse(result["summary"]["runtime_commit_executed"])

    def test_queue_commit_not_performed(self):
        result = build_sell_runtime_commit_execution_plan(_approval())

        self.assertFalse(result["queue_write"])
        self.assertFalse(result["queue_committed"])
        self.assertFalse(result["summary"]["queue_write"])
        self.assertFalse(result["summary"]["queue_committed"])

    def test_send_order_and_broker_not_performed(self):
        result = build_sell_runtime_commit_execution_plan(_approval())

        self.assertFalse(result["send_order"])
        self.assertFalse(result["broker_api_called"])
        self.assertFalse(result["summary"]["send_order"])
        self.assertFalse(result["summary"]["broker_api_called"])

    def test_order_request_and_real_ready_not_changed(self):
        result = build_sell_runtime_commit_execution_plan(_approval())

        self.assertFalse(result["order_request_created"])
        self.assertFalse(result["real_ready_state_changed"])
        self.assertFalse(result["actual_order_sent"])

    def test_no_file_or_runtime_access(self):
        with (
            mock.patch("pathlib.Path.read_text") as read_text,
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = build_sell_runtime_commit_execution_plan(_approval())

        self.assertEqual(result["status"], "READY")
        read_text.assert_not_called()
        write_text.assert_not_called()
        open_mock.assert_not_called()

    def test_module_does_not_import_commit_paths(self):
        source = Path(subject.__file__).read_text(encoding="utf-8")

        self.assertNotIn("commit_execution_queue_write", source)
        self.assertNotIn("execute_runtime_commit", source)
        self.assertNotIn("signal_gate_execution_queue_bridge", source)


if __name__ == "__main__":
    unittest.main()
