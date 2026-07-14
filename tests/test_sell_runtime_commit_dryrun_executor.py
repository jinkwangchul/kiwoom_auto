from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import unittest
from unittest import mock

import sell_runtime_commit_dryrun_executor as subject
from sell_runtime_commit_dryrun_executor import build_sell_runtime_commit_dryrun


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


def _validated_candidate(record: dict | None = None, *, status: str = "READY") -> dict:
    payload = record or _record()
    return {
        "status": status,
        "commit_allowed": status == "READY",
        "candidate_index": 0,
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


def _validation(*candidates: dict, status: str = "READY", commit_allowed: bool | None = None, warnings: list[str] | None = None, reasons: list[str] | None = None) -> dict:
    candidate_list = list(candidates or [_validated_candidate()])
    if commit_allowed is None:
        commit_allowed = status == "READY"
    return {
        "validation_type": "SELL_RUNTIME_COMMIT_VALIDATOR",
        "ownership": "MASTER_ENGINE",
        "domain": "Execution / Runtime Commit Validator",
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
        "commit_allowed": commit_allowed,
        "status": status,
        "runtime_commit_preview_snapshot": {},
        "validated_runtime_commit_candidates": candidate_list,
        "blocked_runtime_commit_candidates": [],
        "warnings": list(warnings or []),
        "reasons": list(reasons or []),
        "summary": {
            "validator_ready_count": sum(1 for item in candidate_list if item.get("status") == "READY"),
            "validator_blocked_count": 0,
            "validator_invalid_count": 0,
            "runtime_write": False,
            "queue_write": False,
            "queue_committed": False,
            "send_order": False,
            "broker_api_called": False,
            "priority_selected": False,
            "auto_selected": False,
        },
    }


class SellRuntimeCommitDryRunExecutorTests(unittest.TestCase):
    def test_ready(self):
        result = build_sell_runtime_commit_dryrun(_validation())

        self.assertEqual(result["dryrun_type"], "SELL_RUNTIME_COMMIT_DRYRUN_EXECUTOR")
        self.assertEqual(result["ownership"], "MASTER_ENGINE")
        self.assertEqual(result["domain"], "Execution / Runtime Commit Dry Run Executor")
        self.assertIsNone(result["routine_dependency"])
        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["commit_allowed"])
        self.assertEqual(result["summary"]["dryrun_ready_count"], 1)

    def test_blocked(self):
        result = build_sell_runtime_commit_dryrun(_validation(status="BLOCKED", commit_allowed=False, reasons=["blocked upstream"]))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["commit_allowed"])
        self.assertIn("runtime_commit_validation status is BLOCKED", result["reasons"])

    def test_invalid(self):
        result = build_sell_runtime_commit_dryrun(_validation(status="INVALID", commit_allowed=False, reasons=["invalid upstream"]))

        self.assertEqual(result["status"], "INVALID")
        self.assertFalse(result["commit_allowed"])
        self.assertIn("runtime_commit_validation status is INVALID", result["reasons"])

    def test_commit_allowed_false_blocks(self):
        result = build_sell_runtime_commit_dryrun(_validation(commit_allowed=False))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("commit_allowed must be True for dry-run execution", result["reasons"])

    def test_validation_type_required(self):
        validation = _validation()
        validation["validation_type"] = "OTHER"

        result = build_sell_runtime_commit_dryrun(validation)

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("runtime_commit_validation validation_type is invalid", result["reasons"])

    def test_preview_only_required(self):
        validation = _validation()
        validation["preview_only"] = False

        result = build_sell_runtime_commit_dryrun(validation)

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("runtime_commit_validation preview_only must be True", result["reasons"])

    def test_safety_flag_violation_invalid(self):
        validation = _validation()
        validation["runtime_write"] = True

        result = build_sell_runtime_commit_dryrun(validation)

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("runtime_commit_validation safety flag violation", result["reasons"])

    def test_candidates_must_be_list(self):
        validation = _validation()
        validation["validated_runtime_commit_candidates"] = {}

        result = build_sell_runtime_commit_dryrun(validation)

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("validated_runtime_commit_candidates must be a list", result["reasons"])

    def test_empty_candidates_blocked(self):
        validation = _validation()
        validation["validated_runtime_commit_candidates"] = []

        result = build_sell_runtime_commit_dryrun(validation)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("no validated runtime commit candidates", result["reasons"])

    def test_candidate_must_be_dict(self):
        validation = _validation()
        validation["validated_runtime_commit_candidates"] = ["bad"]

        result = build_sell_runtime_commit_dryrun(validation)

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("validated candidate must be a dict", result["commit_actions"][0]["reasons"])

    def test_candidate_commit_allowed_required(self):
        candidate = _validated_candidate()
        candidate["commit_allowed"] = False

        result = build_sell_runtime_commit_dryrun(_validation(candidate))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("validated candidate commit_allowed must be True", result["commit_actions"][0]["reasons"])

    def test_candidate_safety_flag_blocks(self):
        candidate = _validated_candidate()
        candidate["send_order"] = True

        result = build_sell_runtime_commit_dryrun(_validation(candidate))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("validated candidate safety flag violation", result["commit_actions"][0]["reasons"])

    def test_identity_preserved(self):
        candidate = _validated_candidate(
            _record(
                source_signal_id="SIG_X",
                order_id="ORDER_X",
                candidate_id="CAND_X",
                queue_pending_id="QUEUE_X",
                request_hash="h" * 64,
                lock_id="LOCK_X",
                execution_id="EXEC_X",
            )
        )

        result = build_sell_runtime_commit_dryrun(_validation(candidate))

        action = result["commit_actions"][0]
        self.assertEqual(action["source_signal_id"], "SIG_X")
        self.assertEqual(action["order_id"], "ORDER_X")
        self.assertEqual(action["candidate_id"], "CAND_X")
        self.assertEqual(action["queue_pending_id"], "QUEUE_X")
        self.assertEqual(action["execution_id"], "EXEC_X")

    def test_request_hash_and_lock_id_preserved(self):
        candidate = _validated_candidate(_record(request_hash="h" * 64, lock_id="LOCK_X"))

        result = build_sell_runtime_commit_dryrun(_validation(candidate))

        action = result["commit_actions"][0]
        self.assertEqual(action["request_hash"], "h" * 64)
        self.assertEqual(action["lock_id"], "LOCK_X")
        self.assertEqual(action["execution_request"]["request_hash"], "h" * 64)
        self.assertEqual(action["execution_request"]["lock_id"], "LOCK_X")

    def test_mismatched_execution_request_blocks(self):
        candidate = _validated_candidate()
        candidate["execution_request"]["request_hash"] = "other"

        result = build_sell_runtime_commit_dryrun(_validation(candidate))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("request_hash must match execution_request", result["commit_actions"][0]["reasons"])

    def test_mismatched_record_blocks(self):
        candidate = _validated_candidate()
        candidate["order_queued_record_preview"]["order_id"] = "OTHER"

        result = build_sell_runtime_commit_dryrun(_validation(candidate))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("order_id must match order_queued_record_preview", result["commit_actions"][0]["reasons"])

    def test_runtime_commit_preview_created(self):
        result = build_sell_runtime_commit_dryrun(_validation())

        preview = result["runtime_commit_preview"]
        self.assertEqual(preview["preview_type"], "SELL_RUNTIME_COMMIT_DRYRUN_PREVIEW")
        self.assertTrue(preview["dry_run"])
        self.assertFalse(preview["runtime_commit_executed"])

    def test_commit_plan_created(self):
        result = build_sell_runtime_commit_dryrun(_validation())

        plan = result["commit_plan"]
        self.assertEqual(plan["plan_type"], "SELL_RUNTIME_COMMIT_DRYRUN_PLAN")
        self.assertEqual(plan["action_count"], 1)
        self.assertEqual(plan["ready_action_count"], 1)

    def test_validation_and_execution_summary_created(self):
        result = build_sell_runtime_commit_dryrun(_validation(warnings=["w1"], reasons=["r1"]))

        self.assertEqual(result["validation_summary"]["source_validation_type"], "SELL_RUNTIME_COMMIT_VALIDATOR")
        self.assertEqual(result["execution_summary"]["action_count"], 1)
        self.assertIn("w1", result["warnings"])
        self.assertIn("r1", result["reasons"])

    def test_multiple_candidates_preserved_without_selection(self):
        second = _validated_candidate(_record(source_signal_id="SIG_2", order_id="ORDER_2"))

        result = build_sell_runtime_commit_dryrun(_validation(_validated_candidate(), second))

        self.assertEqual(result["status"], "READY")
        self.assertEqual(len(result["commit_actions"]), 2)
        self.assertEqual(result["summary"]["dryrun_ready_count"], 2)
        self.assertFalse(result["summary"]["priority_selected"])
        self.assertFalse(result["summary"]["auto_selected"])

    def test_input_mutation_does_not_occur(self):
        validation = _validation()
        original = deepcopy(validation)

        result = build_sell_runtime_commit_dryrun(validation)
        result["commit_actions"][0]["source_signal_id"] = "MUTATED"

        self.assertEqual(validation, original)
        self.assertEqual(result["runtime_commit_validation_snapshot"], original)

    def test_runtime_write_not_performed(self):
        result = build_sell_runtime_commit_dryrun(_validation())

        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["summary"]["runtime_write"])
        self.assertFalse(result["runtime_commit_executed"])

    def test_queue_commit_not_performed(self):
        result = build_sell_runtime_commit_dryrun(_validation())

        self.assertFalse(result["queue_write"])
        self.assertFalse(result["queue_committed"])
        self.assertFalse(result["summary"]["queue_committed"])

    def test_sendorder_not_performed(self):
        result = build_sell_runtime_commit_dryrun(_validation())

        self.assertFalse(result["send_order"])
        self.assertFalse(result["summary"]["send_order"])

    def test_broker_not_performed(self):
        result = build_sell_runtime_commit_dryrun(_validation())

        self.assertFalse(result["broker_api_called"])
        self.assertFalse(result["summary"]["broker_api_called"])

    def test_order_request_and_real_ready_not_changed(self):
        result = build_sell_runtime_commit_dryrun(_validation())

        self.assertFalse(result["order_request_created"])
        self.assertFalse(result["real_ready_state_changed"])

    def test_no_file_or_runtime_access(self):
        with (
            mock.patch("pathlib.Path.read_text") as read_text,
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = build_sell_runtime_commit_dryrun(_validation())

        self.assertEqual(result["status"], "READY")
        read_text.assert_not_called()
        write_text.assert_not_called()
        open_mock.assert_not_called()

    def test_module_does_not_import_commit_or_order_senders(self):
        source = Path(subject.__file__).read_text(encoding="utf-8")

        self.assertNotIn("commit_execution_queue_write", source)
        self.assertNotIn("execute_runtime_commit", source)
        self.assertNotIn("SendOrder", source)
        self.assertNotIn("Broker", source)


if __name__ == "__main__":
    unittest.main()
