from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import unittest
from unittest import mock

import sell_runtime_commit_preview as subject
from sell_runtime_commit_preview import build_sell_runtime_commit_preview


def _record(
    *,
    source_signal_id: str = "SIG_1",
    order_id: str = "ORDER_1",
    request_hash: str = "r" * 64,
    lock_id: str = "LOCK_1",
    execution_id: str = "EXEC_1",
) -> dict:
    return {
        "id": f"ORDER_QUEUED_{order_id}",
        "status": "ORDER_QUEUED",
        "source": "execution_queue_pending",
        "source_signal_id": source_signal_id,
        "order_id": order_id,
        "candidate_id": f"CANDIDATE_{order_id}",
        "queue_pending_id": f"QUEUE_PENDING_{order_id}",
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


def _queue_candidate(record: dict | None = None, *, warnings: list[str] | None = None) -> dict:
    payload = record or _record()
    return {
        "source_candidate_index": 0,
        "source_signal_id": payload["source_signal_id"],
        "source_order_id": payload["order_id"],
        "action_source": "METHOD",
        "signal": "SELL",
        "status": "READY",
        "order_queued_record_preview": deepcopy(payload),
        "queue_write_preview_result": {
            "write_preview": True,
            "write_stage": "order_queued_record_preview_created",
            "next_stage": "QUEUE_WRITE_REQUIRED",
            "preview_only": True,
            "no_write": True,
            "blocked_reasons": [],
            "order_queued_record_preview": deepcopy(payload),
        },
        "bridge_preview": {"stage": "SIGNAL_GATE_EXECUTION_QUEUE_BRIDGE"},
        "real_ready_order": {"id": payload["order_id"], "source_signal_id": payload["source_signal_id"]},
        "reasons": [],
        "warnings": list(warnings or []),
        "priority_selected": False,
        "auto_selected": False,
    }


def _full_preview(*candidates: dict, status: str = "READY", warnings: list[str] | None = None, reasons: list[str] | None = None) -> dict:
    return {
        "preview_type": "SELL_EXECUTION_FULL_PREVIEW",
        "ownership": "MASTER_ENGINE",
        "domain": "Execution / Full Preview Orchestration",
        "routine_dependency": None,
        "preview_only": True,
        "execution_connected": False,
        "runtime_write": False,
        "queue_write": False,
        "file_write": False,
        "send_order": False,
        "broker_api_called": False,
        "real_ready_state_changed": False,
        "order_request_created": False,
        "queue_committed": False,
        "actual_order_sent": False,
        "status": status,
        "completed": status == "READY",
        "common_execution_preview": {},
        "execution_readiness_preview": {},
        "signal_gate_preview": {},
        "execution_queue_preview": {
            "preview_type": "SELL_EXECUTION_QUEUE_PREVIEW",
            "status": status,
            "execution_queue_ready": status == "READY",
            "queue_ready_candidates": list(candidates or [_queue_candidate()]),
            "blocked_queue_candidates": [],
            "candidate_queue_results": list(candidates or [_queue_candidate()]),
            "summary": {
                "queue_ready_count": len(candidates or [_queue_candidate()]),
                "queue_committed": False,
            },
        },
        "preview_steps": {},
        "summary": {
            "queue_ready_count": len(candidates or [_queue_candidate()]),
            "order_queued_preview_count": len(candidates or [_queue_candidate()]),
            "priority_selected": False,
            "auto_selected": False,
            "queue_committed": False,
        },
        "warnings": list(warnings or []),
        "reasons": list(reasons or []),
    }


class SellRuntimeCommitPreviewTests(unittest.TestCase):
    def test_ready(self):
        result = build_sell_runtime_commit_preview(_full_preview())

        self.assertEqual(result["preview_type"], "SELL_RUNTIME_COMMIT_PREVIEW")
        self.assertEqual(result["ownership"], "MASTER_ENGINE")
        self.assertEqual(result["domain"], "Execution / Runtime Commit Preview")
        self.assertIsNone(result["routine_dependency"])
        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["runtime_commit_ready"])

    def test_blocked(self):
        result = build_sell_runtime_commit_preview(_full_preview(status="BLOCKED", reasons=["blocked upstream"]))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["runtime_commit_ready"])
        self.assertIn("full_preview status is BLOCKED", result["reasons"])

    def test_invalid(self):
        result = build_sell_runtime_commit_preview(_full_preview(status="INVALID", reasons=["invalid upstream"]))

        self.assertEqual(result["status"], "INVALID")
        self.assertFalse(result["runtime_commit_ready"])
        self.assertIn("full_preview status is INVALID", result["reasons"])

    def test_preview_only_false(self):
        preview = _full_preview()
        preview["preview_only"] = False

        result = build_sell_runtime_commit_preview(preview)

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("full_preview preview_only must be True", result["reasons"])

    def test_safety_flag_violation(self):
        preview = _full_preview()
        preview["runtime_write"] = True

        result = build_sell_runtime_commit_preview(preview)

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("full_preview safety flag violation", result["reasons"])

    def test_identity_preserved(self):
        record = _record(source_signal_id="SIG_ID", order_id="ORDER_ID")

        result = build_sell_runtime_commit_preview(_full_preview(_queue_candidate(record)))

        candidate = result["runtime_commit_candidates"][0]
        self.assertEqual(candidate["source_signal_id"], "SIG_ID")
        self.assertEqual(candidate["order_id"], "ORDER_ID")

    def test_execution_request_identity_preserved(self):
        record = _record(request_hash="h" * 64, lock_id="LOCK_ID", execution_id="EXEC_ID")

        result = build_sell_runtime_commit_preview(_full_preview(_queue_candidate(record)))

        candidate = result["runtime_commit_candidates"][0]
        self.assertEqual(candidate["request_hash"], "h" * 64)
        self.assertEqual(candidate["lock_id"], "LOCK_ID")
        self.assertEqual(candidate["execution_id"], "EXEC_ID")
        self.assertEqual(candidate["execution_request"]["request_hash"], "h" * 64)
        self.assertEqual(candidate["execution_request"]["lock_id"], "LOCK_ID")
        self.assertEqual(candidate["execution_request"]["execution_id"], "EXEC_ID")

    def test_summary_delivered(self):
        result = build_sell_runtime_commit_preview(_full_preview(_queue_candidate(), _queue_candidate(_record(order_id="ORDER_2", source_signal_id="SIG_2"))))

        self.assertEqual(result["summary"]["queue_ready_count"], 2)
        self.assertEqual(result["summary"]["runtime_commit_ready_count"], 2)

    def test_warnings_and_reasons_delivered(self):
        result = build_sell_runtime_commit_preview(_full_preview(warnings=["w1"], reasons=["r1"]))

        self.assertIn("w1", result["warnings"])
        self.assertIn("r1", result["reasons"])

    def test_runtime_commit_not_performed(self):
        result = build_sell_runtime_commit_preview(_full_preview())

        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["summary"]["runtime_write"])

    def test_queue_commit_not_performed(self):
        result = build_sell_runtime_commit_preview(_full_preview())

        self.assertFalse(result["queue_write"])
        self.assertFalse(result["queue_committed"])
        self.assertFalse(result["summary"]["queue_committed"])

    def test_sendorder_not_performed(self):
        result = build_sell_runtime_commit_preview(_full_preview())

        self.assertFalse(result["send_order"])
        self.assertFalse(result["summary"]["send_order"])

    def test_broker_not_performed(self):
        result = build_sell_runtime_commit_preview(_full_preview())

        self.assertFalse(result["broker_api_called"])
        self.assertFalse(result["summary"]["broker_api_called"])

    def test_order_request_and_real_ready_not_mutated(self):
        result = build_sell_runtime_commit_preview(_full_preview())

        self.assertFalse(result["order_request_created"])
        self.assertFalse(result["real_ready_state_changed"])

    def test_input_mutation_does_not_occur(self):
        preview = _full_preview()
        original = deepcopy(preview)

        result = build_sell_runtime_commit_preview(preview)
        result["runtime_commit_candidates"][0]["source_signal_id"] = "MUTATED"

        self.assertEqual(preview, original)
        self.assertEqual(result["full_preview_snapshot"], original)

    def test_missing_execution_request_invalid(self):
        record = _record()
        record.pop("execution_request")

        result = build_sell_runtime_commit_preview(_full_preview(_queue_candidate(record)))

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("execution_request must be a non-empty dict", result["runtime_commit_candidates"][0]["reasons"])

    def test_mismatched_request_hash_blocked(self):
        record = _record()
        record["execution_request"]["request_hash"] = "other"

        result = build_sell_runtime_commit_preview(_full_preview(_queue_candidate(record)))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("request_hash must match execution_request", result["runtime_commit_candidates"][0]["reasons"])

    def test_no_file_or_runtime_access(self):
        with (
            mock.patch("pathlib.Path.read_text") as read_text,
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = build_sell_runtime_commit_preview(_full_preview())

        self.assertEqual(result["status"], "READY")
        read_text.assert_not_called()
        write_text.assert_not_called()
        open_mock.assert_not_called()

    def test_module_does_not_import_commit_or_order_senders(self):
        source = Path(subject.__file__).read_text(encoding="utf-8")

        self.assertNotIn("commit_execution_queue_write", source)
        self.assertNotIn("SendOrder", source)
        self.assertNotIn("Broker", source)


if __name__ == "__main__":
    unittest.main()
