from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import unittest
from unittest import mock

import sell_runtime_commit_validator as subject
from sell_runtime_commit_validator import validate_sell_runtime_commit


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


def _candidate(record: dict | None = None, *, status: str = "READY") -> dict:
    payload = record or _record()
    return {
        "status": status,
        "runtime_commit_ready": status == "READY",
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


def _preview(*candidates: dict, status: str = "READY", warnings: list[str] | None = None, reasons: list[str] | None = None) -> dict:
    candidate_list = list(candidates or [_candidate()])
    return {
        "preview_type": "SELL_RUNTIME_COMMIT_PREVIEW",
        "ownership": "MASTER_ENGINE",
        "domain": "Execution / Runtime Commit Preview",
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
        "status": status,
        "runtime_commit_ready": status == "READY",
        "runtime_commit_candidates": candidate_list,
        "blocked_runtime_commit_candidates": [],
        "full_preview_snapshot": {},
        "warnings": list(warnings or []),
        "reasons": list(reasons or []),
        "summary": {
            "runtime_commit_ready_count": sum(1 for item in candidate_list if item.get("status") == "READY"),
            "runtime_commit_blocked_count": 0,
            "runtime_commit_invalid_count": 0,
            "runtime_write": False,
            "queue_write": False,
            "queue_committed": False,
            "send_order": False,
            "broker_api_called": False,
            "priority_selected": False,
            "auto_selected": False,
        },
    }


class SellRuntimeCommitValidatorTests(unittest.TestCase):
    def test_ready_commit_allowed(self):
        result = validate_sell_runtime_commit(_preview())

        self.assertEqual(result["validation_type"], "SELL_RUNTIME_COMMIT_VALIDATOR")
        self.assertEqual(result["ownership"], "MASTER_ENGINE")
        self.assertEqual(result["domain"], "Execution / Runtime Commit Validator")
        self.assertIsNone(result["routine_dependency"])
        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["commit_allowed"])
        self.assertEqual(result["summary"]["validator_ready_count"], 1)

    def test_blocked_preview(self):
        result = validate_sell_runtime_commit(_preview(status="BLOCKED", reasons=["blocked upstream"]))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["commit_allowed"])
        self.assertIn("runtime_commit_preview status is BLOCKED", result["reasons"])

    def test_invalid_preview(self):
        result = validate_sell_runtime_commit(_preview(status="INVALID", reasons=["invalid upstream"]))

        self.assertEqual(result["status"], "INVALID")
        self.assertFalse(result["commit_allowed"])
        self.assertIn("runtime_commit_preview status is INVALID", result["reasons"])

    def test_preview_type_required(self):
        preview = _preview()
        preview["preview_type"] = "OTHER"

        result = validate_sell_runtime_commit(preview)

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("runtime_commit_preview preview_type is invalid", result["reasons"])

    def test_preview_only_required(self):
        preview = _preview()
        preview["preview_only"] = False

        result = validate_sell_runtime_commit(preview)

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("runtime_commit_preview preview_only must be True", result["reasons"])

    def test_safety_flag_violation_invalid(self):
        preview = _preview()
        preview["queue_committed"] = True

        result = validate_sell_runtime_commit(preview)

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("runtime_commit_preview safety flag violation", result["reasons"])

    def test_ready_requires_runtime_commit_ready_true(self):
        preview = _preview()
        preview["runtime_commit_ready"] = False

        result = validate_sell_runtime_commit(preview)

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("runtime_commit_ready must be True when status is READY", result["reasons"])

    def test_candidates_must_be_list(self):
        preview = _preview()
        preview["runtime_commit_candidates"] = {}

        result = validate_sell_runtime_commit(preview)

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("runtime_commit_candidates must be a list", result["reasons"])

    def test_empty_candidates_blocked(self):
        preview = _preview()
        preview["runtime_commit_candidates"] = []
        result = validate_sell_runtime_commit(preview)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("no runtime commit candidates to validate", result["reasons"])

    def test_candidate_must_be_dict(self):
        preview = _preview()
        preview["runtime_commit_candidates"] = ["bad"]

        result = validate_sell_runtime_commit(preview)

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("runtime commit candidate must be a dict", result["validated_runtime_commit_candidates"][0]["reasons"])

    def test_candidate_blocked_propagates(self):
        result = validate_sell_runtime_commit(_preview(_candidate(status="BLOCKED")))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("candidate status is BLOCKED", result["validated_runtime_commit_candidates"][0]["reasons"])

    def test_candidate_safety_flag_blocks(self):
        candidate = _candidate()
        candidate["send_order"] = True

        result = validate_sell_runtime_commit(_preview(candidate))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("candidate safety flag violation", result["validated_runtime_commit_candidates"][0]["reasons"])

    def test_required_candidate_identity(self):
        candidate = _candidate()
        candidate["source_signal_id"] = ""

        result = validate_sell_runtime_commit(_preview(candidate))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("source_signal_id is required", result["validated_runtime_commit_candidates"][0]["reasons"])

    def test_execution_request_required(self):
        candidate = _candidate()
        candidate.pop("execution_request")

        result = validate_sell_runtime_commit(_preview(candidate))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("execution_request must be a non-empty dict", result["validated_runtime_commit_candidates"][0]["reasons"])

    def test_record_required(self):
        candidate = _candidate()
        candidate.pop("order_queued_record_preview")

        result = validate_sell_runtime_commit(_preview(candidate))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("order_queued_record_preview must be a non-empty dict", result["validated_runtime_commit_candidates"][0]["reasons"])

    def test_record_execution_request_required(self):
        candidate = _candidate()
        candidate["order_queued_record_preview"].pop("execution_request")

        result = validate_sell_runtime_commit(_preview(candidate))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("record execution_request must be a non-empty dict", result["validated_runtime_commit_candidates"][0]["reasons"])

    def test_candidate_record_identity_must_match(self):
        candidate = _candidate()
        candidate["order_queued_record_preview"]["order_id"] = "OTHER"

        result = validate_sell_runtime_commit(_preview(candidate))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("order_id must match record", result["validated_runtime_commit_candidates"][0]["reasons"])

    def test_candidate_execution_request_identity_must_match(self):
        candidate = _candidate()
        candidate["execution_request"]["request_hash"] = "other"

        result = validate_sell_runtime_commit(_preview(candidate))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("request_hash must match execution_request", result["validated_runtime_commit_candidates"][0]["reasons"])

    def test_candidate_record_execution_request_identity_must_match(self):
        candidate = _candidate()
        candidate["order_queued_record_preview"]["execution_request"]["lock_id"] = "OTHER"

        result = validate_sell_runtime_commit(_preview(candidate))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("lock_id must match record execution_request", result["validated_runtime_commit_candidates"][0]["reasons"])

    def test_identity_preserved(self):
        candidate = _candidate(
            _record(
                source_signal_id="SIG_X",
                order_id="ORDER_X",
                request_hash="h" * 64,
                lock_id="LOCK_X",
                execution_id="EXEC_X",
            )
        )

        result = validate_sell_runtime_commit(_preview(candidate))

        validated = result["validated_runtime_commit_candidates"][0]
        self.assertEqual(validated["source_signal_id"], "SIG_X")
        self.assertEqual(validated["order_id"], "ORDER_X")
        self.assertEqual(validated["request_hash"], "h" * 64)
        self.assertEqual(validated["lock_id"], "LOCK_X")
        self.assertEqual(validated["execution_id"], "EXEC_X")

    def test_summary_warnings_reasons_preserved(self):
        result = validate_sell_runtime_commit(_preview(warnings=["w1"], reasons=["r1"]))

        self.assertIn("w1", result["warnings"])
        self.assertIn("r1", result["reasons"])
        self.assertEqual(result["summary"]["runtime_commit_ready_count"], 1)

    def test_multiple_candidates_preserved_without_selection(self):
        second = _candidate(_record(source_signal_id="SIG_2", order_id="ORDER_2"))

        result = validate_sell_runtime_commit(_preview(_candidate(), second))

        self.assertEqual(result["status"], "READY")
        self.assertEqual(len(result["validated_runtime_commit_candidates"]), 2)
        self.assertEqual(result["summary"]["validator_ready_count"], 2)
        self.assertFalse(result["summary"]["priority_selected"])
        self.assertFalse(result["summary"]["auto_selected"])

    def test_input_mutation_does_not_occur(self):
        preview = _preview()
        original = deepcopy(preview)

        result = validate_sell_runtime_commit(preview)
        result["validated_runtime_commit_candidates"][0]["source_signal_id"] = "MUTATED"

        self.assertEqual(preview, original)
        self.assertEqual(result["runtime_commit_preview_snapshot"], original)

    def test_runtime_commit_not_executed(self):
        result = validate_sell_runtime_commit(_preview())

        self.assertFalse(result["runtime_commit_executed"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["queue_committed"])

    def test_sendorder_and_broker_not_called(self):
        result = validate_sell_runtime_commit(_preview())

        self.assertFalse(result["send_order"])
        self.assertFalse(result["broker_api_called"])
        self.assertFalse(result["order_request_created"])
        self.assertFalse(result["real_ready_state_changed"])

    def test_no_file_or_runtime_access(self):
        with (
            mock.patch("pathlib.Path.read_text") as read_text,
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = validate_sell_runtime_commit(_preview())

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
