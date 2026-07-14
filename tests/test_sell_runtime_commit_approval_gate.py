from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import unittest
from unittest import mock

import sell_runtime_commit_approval_gate as subject
from sell_runtime_commit_approval_gate import build_sell_runtime_commit_approval_gate


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


def _action(record: dict | None = None, *, status: str = "READY") -> dict:
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


def _dryrun(
    *actions: dict,
    status: str = "READY",
    commit_allowed: bool | None = None,
    dry_run: bool = True,
    preview_only: bool = True,
    warnings: list[str] | None = None,
    reasons: list[str] | None = None,
) -> dict:
    action_list = list(actions or [_action()])
    if commit_allowed is None:
        commit_allowed = status == "READY"
    return {
        "dryrun_type": "SELL_RUNTIME_COMMIT_DRYRUN_EXECUTOR",
        "ownership": "MASTER_ENGINE",
        "domain": "Execution / Runtime Commit Dry Run Executor",
        "routine_dependency": None,
        "preview_only": preview_only,
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
        "dry_run": dry_run,
        "commit_allowed": commit_allowed,
        "status": status,
        "runtime_commit_validation_snapshot": {},
        "runtime_commit_preview": {},
        "commit_plan": {
            "plan_type": "SELL_RUNTIME_COMMIT_DRYRUN_PLAN",
            "dry_run": dry_run,
            "commit_allowed": commit_allowed,
            "actions": deepcopy(action_list),
        },
        "commit_actions": action_list,
        "blocked_commit_actions": [],
        "validation_summary": {},
        "execution_summary": {},
        "warnings": list(warnings or []),
        "reasons": list(reasons or []),
        "summary": {
            "dryrun_ready_count": sum(1 for item in action_list if item.get("status") == "READY"),
            "dryrun_blocked_count": sum(1 for item in action_list if item.get("status") == "BLOCKED"),
            "dryrun_invalid_count": sum(1 for item in action_list if item.get("status") == "INVALID"),
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


class SellRuntimeCommitApprovalGateTests(unittest.TestCase):
    def test_all_candidates_ready_approves(self):
        result = build_sell_runtime_commit_approval_gate(_dryrun())

        self.assertEqual(result["approval_type"], "SELL_RUNTIME_COMMIT_APPROVAL_GATE")
        self.assertEqual(result["ownership"], "MASTER_ENGINE")
        self.assertEqual(result["domain"], "Execution / Runtime Commit Approval Gate")
        self.assertIsNone(result["routine_dependency"])
        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["approval_granted"])
        self.assertTrue(result["commit_allowed"])

    def test_one_blocked_candidate_blocks_all_approval(self):
        result = build_sell_runtime_commit_approval_gate(_dryrun(_action(), _action(_record(order_id="ORDER_2"), status="BLOCKED")))

        self.assertEqual(result["status"], "INVALID")
        self.assertFalse(result["approval_granted"])
        self.assertFalse(result["commit_allowed"])

    def test_one_invalid_candidate_invalidates_approval(self):
        result = build_sell_runtime_commit_approval_gate(_dryrun(_action(), _action(_record(order_id="ORDER_2"), status="INVALID")))

        self.assertEqual(result["status"], "INVALID")
        self.assertFalse(result["approval_granted"])
        self.assertFalse(result["commit_allowed"])

    def test_ready_with_blocked_commit_actions_is_invalid(self):
        dryrun = _dryrun(_action())
        dryrun["blocked_commit_actions"] = [_action(_record(order_id="ORDER_2"), status="BLOCKED")]

        result = build_sell_runtime_commit_approval_gate(dryrun)

        self.assertEqual(result["status"], "INVALID")
        self.assertFalse(result["approval_granted"])
        self.assertIn("READY dry-run must not contain blocked_commit_actions", result["reasons"])

    def test_summary_count_mismatch_is_invalid(self):
        dryrun = _dryrun(_action())
        dryrun["summary"]["dryrun_ready_count"] = 2

        result = build_sell_runtime_commit_approval_gate(dryrun)

        self.assertEqual(result["status"], "INVALID")
        self.assertFalse(result["commit_allowed"])

    def test_ready_with_dryrun_blocked_count_is_invalid(self):
        dryrun = _dryrun(_action())
        dryrun["summary"]["dryrun_blocked_count"] = 1

        result = build_sell_runtime_commit_approval_gate(dryrun)

        self.assertEqual(result["status"], "INVALID")
        self.assertFalse(result["approval_granted"])

    def test_ready_with_dryrun_invalid_count_is_invalid(self):
        dryrun = _dryrun(_action())
        dryrun["summary"]["dryrun_invalid_count"] = 1

        result = build_sell_runtime_commit_approval_gate(dryrun)

        self.assertEqual(result["status"], "INVALID")
        self.assertFalse(result["approval_granted"])

    def test_ready_count_must_equal_commit_action_count(self):
        dryrun = _dryrun(_action(), _action(_record(order_id="ORDER_2")))
        dryrun["summary"]["dryrun_ready_count"] = 1

        result = build_sell_runtime_commit_approval_gate(dryrun)

        self.assertEqual(result["status"], "INVALID")
        self.assertFalse(result["commit_allowed"])

    def test_normal_all_ready_still_approves(self):
        dryrun = _dryrun(_action(), _action(_record(order_id="ORDER_2")))

        result = build_sell_runtime_commit_approval_gate(dryrun)

        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["approval_granted"])
        self.assertTrue(result["commit_allowed"])

    def test_commit_allowed_false_blocks(self):
        result = build_sell_runtime_commit_approval_gate(_dryrun(commit_allowed=False))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["approval_granted"])

    def test_dry_run_false_is_invalid(self):
        result = build_sell_runtime_commit_approval_gate(_dryrun(dry_run=False))

        self.assertEqual(result["status"], "INVALID")

    def test_preview_only_false_is_invalid(self):
        result = build_sell_runtime_commit_approval_gate(_dryrun(preview_only=False))

        self.assertEqual(result["status"], "INVALID")

    def test_upstream_blocked_blocks(self):
        result = build_sell_runtime_commit_approval_gate(_dryrun(status="BLOCKED", commit_allowed=False, reasons=["blocked upstream"]))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["commit_allowed"])

    def test_upstream_invalid_invalidates(self):
        result = build_sell_runtime_commit_approval_gate(_dryrun(status="INVALID", commit_allowed=False, reasons=["invalid upstream"]))

        self.assertEqual(result["status"], "INVALID")
        self.assertFalse(result["approval_granted"])

    def test_input_type_required(self):
        result = build_sell_runtime_commit_approval_gate("bad")  # type: ignore[arg-type]

        self.assertEqual(result["status"], "INVALID")

    def test_source_type_required(self):
        dryrun = _dryrun()
        dryrun["dryrun_type"] = "OTHER"

        result = build_sell_runtime_commit_approval_gate(dryrun)

        self.assertEqual(result["status"], "INVALID")

    def test_commit_actions_must_be_list(self):
        dryrun = _dryrun()
        dryrun["commit_actions"] = {}

        result = build_sell_runtime_commit_approval_gate(dryrun)

        self.assertEqual(result["status"], "INVALID")

    def test_empty_commit_actions_blocked(self):
        dryrun = _dryrun()
        dryrun["commit_actions"] = []
        dryrun["summary"]["dryrun_ready_count"] = 0

        result = build_sell_runtime_commit_approval_gate(dryrun)

        self.assertEqual(result["status"], "BLOCKED")

    def test_blocked_commit_actions_must_be_list(self):
        dryrun = _dryrun()
        dryrun["blocked_commit_actions"] = {}

        result = build_sell_runtime_commit_approval_gate(dryrun)

        self.assertEqual(result["status"], "INVALID")

    def test_safety_flag_violation_invalid(self):
        dryrun = _dryrun()
        dryrun["runtime_write"] = True

        result = build_sell_runtime_commit_approval_gate(dryrun)

        self.assertEqual(result["status"], "INVALID")

    def test_action_safety_flag_violation_invalid(self):
        action = _action()
        action["send_order"] = True

        result = build_sell_runtime_commit_approval_gate(_dryrun(action))

        self.assertEqual(result["status"], "INVALID")

    def test_identity_preserved(self):
        action = _action(
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

        result = build_sell_runtime_commit_approval_gate(_dryrun(action))

        approved = result["approved_commit_actions"][0]
        self.assertEqual(approved["source_signal_id"], "SIG_X")
        self.assertEqual(approved["order_id"], "ORDER_X")
        self.assertEqual(approved["candidate_id"], "CAND_X")
        self.assertEqual(approved["queue_pending_id"], "QUEUE_X")
        self.assertEqual(approved["execution_id"], "EXEC_X")
        self.assertEqual(approved["request_hash"], "h" * 64)
        self.assertEqual(approved["lock_id"], "LOCK_X")

    def test_identity_missing_is_invalid(self):
        action = _action()
        action["lock_id"] = ""

        result = build_sell_runtime_commit_approval_gate(_dryrun(action))

        self.assertEqual(result["status"], "INVALID")

    def test_identity_mismatch_is_invalid(self):
        action = _action()
        action["execution_request"]["request_hash"] = "other"

        result = build_sell_runtime_commit_approval_gate(_dryrun(action))

        self.assertEqual(result["status"], "INVALID")

    def test_record_identity_mismatch_is_invalid(self):
        action = _action()
        action["order_queued_record_preview"]["order_id"] = "OTHER"

        result = build_sell_runtime_commit_approval_gate(_dryrun(action))

        self.assertEqual(result["status"], "INVALID")

    def test_execution_request_required(self):
        action = _action()
        action["execution_request"] = {}

        result = build_sell_runtime_commit_approval_gate(_dryrun(action))

        self.assertEqual(result["status"], "INVALID")

    def test_queued_record_required(self):
        action = _action()
        action["order_queued_record_preview"] = {}

        result = build_sell_runtime_commit_approval_gate(_dryrun(action))

        self.assertEqual(result["status"], "INVALID")

    def test_partial_approval_not_allowed(self):
        blocked = _action(_record(order_id="ORDER_2"), status="BLOCKED")

        result = build_sell_runtime_commit_approval_gate(_dryrun(_action(), blocked))

        self.assertEqual(result["status"], "INVALID")
        self.assertFalse(result["approval_granted"])
        self.assertFalse(result["commit_allowed"])
        self.assertEqual(result["summary"]["approved_action_count"], 1)
        self.assertGreaterEqual(result["summary"]["blocked_action_count"], 1)

    def test_warnings_reasons_and_summary_forwarded(self):
        result = build_sell_runtime_commit_approval_gate(_dryrun(warnings=["w1"], reasons=["r1"]))

        self.assertIn("w1", result["warnings"])
        self.assertIn("r1", result["reasons"])
        self.assertEqual(result["source_summary"]["dryrun_ready_count"], 1)
        self.assertEqual(result["summary"]["approval_ready_count"], 1)

    def test_input_mutation_does_not_occur(self):
        dryrun = _dryrun()
        original = deepcopy(dryrun)

        result = build_sell_runtime_commit_approval_gate(dryrun)
        result["approved_commit_actions"][0]["order_id"] = "MUTATED"

        self.assertEqual(dryrun, original)

    def test_runtime_commit_not_performed(self):
        result = build_sell_runtime_commit_approval_gate(_dryrun())

        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["runtime_commit_executed"])
        self.assertFalse(result["summary"]["runtime_write"])
        self.assertFalse(result["summary"]["runtime_commit_executed"])

    def test_queue_commit_not_performed(self):
        result = build_sell_runtime_commit_approval_gate(_dryrun())

        self.assertFalse(result["queue_write"])
        self.assertFalse(result["queue_committed"])
        self.assertFalse(result["summary"]["queue_write"])
        self.assertFalse(result["summary"]["queue_committed"])

    def test_send_order_and_broker_not_performed(self):
        result = build_sell_runtime_commit_approval_gate(_dryrun())

        self.assertFalse(result["send_order"])
        self.assertFalse(result["broker_api_called"])
        self.assertFalse(result["summary"]["send_order"])
        self.assertFalse(result["summary"]["broker_api_called"])

    def test_order_request_and_real_ready_not_changed(self):
        result = build_sell_runtime_commit_approval_gate(_dryrun())

        self.assertFalse(result["order_request_created"])
        self.assertFalse(result["real_ready_state_changed"])
        self.assertFalse(result["actual_order_sent"])

    def test_no_file_or_runtime_access(self):
        with (
            mock.patch("pathlib.Path.read_text") as read_text,
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = build_sell_runtime_commit_approval_gate(_dryrun())

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
