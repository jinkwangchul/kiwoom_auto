# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from execution_runtime_file_schema import default_order_executions_data
from send_order_result_recorder_v1 import record_send_order_result
from send_order_result_recorder_review import review_send_order_result_record


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _protected_paths() -> list[Path]:
    paths = [
        ROOT / "runtime" / "order_queue.json",
        ROOT / "runtime" / "order_executions.json",
        ROOT / "runtime" / "order_locks.json",
    ]
    paths.extend(sorted((ROOT / "routines").glob("*/rules.json")))
    return paths


def _write_json(path: Path, data: dict[str, object]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class SendOrderResultRecorderReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def _record_path(self, temp_dir: str) -> Path:
        path = Path(temp_dir) / "runtime" / "order_executions.json"
        path.parent.mkdir()
        _write_json(path, default_order_executions_data())
        return path

    def _contract_result(self) -> dict[str, object]:
        return {
            "status": "RECORD_READY",
            "record_contract": {
                "contract_type": "SEND_ORDER_RESULT_RECORDER_CONTRACT",
                "dispatch_id": "DISPATCH_REVIEW_RECORD_1",
                "order_id": "ORDER_REVIEW_RECORD_1",
                "source_order_id": "SOURCE_ORDER_REVIEW_RECORD_1",
                "source_signal_id": "SIGNAL_REVIEW_RECORD_1",
                "code": "003550",
                "side": "BUY",
                "quantity": 10,
                "price": 85000,
                "hoga": "03",
                "send_order_return_code": 0,
                "send_order_status": "SEND_ORDER_SENT",
                "review_status": "SEND_ORDER_REVIEW_OK",
                "recorded_at": "2026-07-07 10:00:00",
            },
            "issues": [],
            "warnings": [],
            "record_ready": True,
            "record_called": False,
            "runtime_write": False,
            "queue_write": False,
            "chejan_called": False,
        }

    def _recorded_result(self, record_path: Path) -> dict[str, object]:
        return record_send_order_result(self._contract_result(), record_path, manual_confirmation=True)

    def test_record_review_ok_normal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            record_path = self._record_path(temp_dir)
            recorder_result = self._recorded_result(record_path)
            record_id = recorder_result["record_report"]["record_id"]

            result = review_send_order_result_record(recorder_result, record_id, record_path)

            self.assertEqual("RECORD_REVIEW_OK", result["status"])
            self.assertTrue(result["record_verified"])
            self.assertFalse(result["chejan_ready"])
            self.assertFalse(result["chejan_called"])
            self.assertFalse(result["runtime_write"])
            self.assertFalse(result["queue_write"])
            review = result["review"]
            self.assertEqual(record_id, review["record_id"])
            self.assertEqual("DISPATCH_REVIEW_RECORD_1", review["dispatch_id"])
            self.assertEqual("ORDER_REVIEW_RECORD_1", review["order_id"])
            self.assertEqual("SEND_ORDER_RESULT_RECORDED", review["record_status"])

    def test_recorder_blocked_is_review_blocked(self) -> None:
        result = review_send_order_result_record(
            {
                "status": "BLOCKED",
                "record_report": {},
                "issues": ["blocked"],
                "warnings": [],
                "record_called": False,
                "runtime_write": False,
                "queue_write": False,
                "chejan_called": False,
            },
            "RECORD_ID",
            "unused",
        )

        self.assertEqual("RECORD_REVIEW_BLOCKED", result["status"])
        self.assertFalse(result["record_verified"])

    def test_recorder_invalid_is_invalid(self) -> None:
        result = review_send_order_result_record(
            {"status": "INVALID", "issues": ["bad"], "warnings": []},
            "RECORD_ID",
            "unused",
        )

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["record_verified"])

    def test_recorder_error_is_error(self) -> None:
        result = review_send_order_result_record(
            {"status": "ERROR", "issues": ["boom"], "warnings": []},
            "RECORD_ID",
            "unused",
        )

        self.assertEqual("ERROR", result["status"])
        self.assertFalse(result["record_verified"])

    def test_expected_record_id_missing_is_invalid(self) -> None:
        result = review_send_order_result_record({"status": "RECORDED"}, "", "unused")

        self.assertEqual("INVALID", result["status"])
        self.assertIn("expected_record_id is required", result["issues"])

    def test_expected_record_id_mismatch_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            record_path = self._record_path(temp_dir)
            recorder_result = self._recorded_result(record_path)

            result = review_send_order_result_record(recorder_result, "OTHER_RECORD_ID", record_path)

            self.assertEqual("INVALID", result["status"])
            self.assertIn("expected_record_id does not match record_report.record_id", result["issues"])

    def test_record_file_missing_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            record_path = self._record_path(temp_dir)
            recorder_result = self._recorded_result(record_path)
            record_id = recorder_result["record_report"]["record_id"]
            missing_path = Path(temp_dir) / "runtime" / "missing_order_executions.json"

            result = review_send_order_result_record(recorder_result, record_id, missing_path)

            self.assertEqual("ERROR", result["status"])
            self.assertIn("record file not found", result["issues"])

    def test_malformed_record_file_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            record_path = self._record_path(temp_dir)
            recorder_result = self._recorded_result(record_path)
            record_id = recorder_result["record_report"]["record_id"]
            _write_json(record_path, {"version": 1, "updated_at": None, "executions": {}})

            result = review_send_order_result_record(recorder_result, record_id, record_path)

            self.assertEqual("ERROR", result["status"])
            self.assertIn("order_executions executions must be a list", result["issues"])

    def test_record_missing_in_file_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            record_path = self._record_path(temp_dir)
            recorder_result = self._recorded_result(record_path)
            record_id = recorder_result["record_report"]["record_id"]
            _write_json(record_path, default_order_executions_data())

            result = review_send_order_result_record(recorder_result, record_id, record_path)

            self.assertEqual("ERROR", result["status"])
            self.assertIn("expected record not found", result["issues"])

    def test_deepcopy_defends_external_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            record_path = self._record_path(temp_dir)
            recorder_result = self._recorded_result(record_path)
            before = deepcopy(recorder_result)
            record_id = recorder_result["record_report"]["record_id"]

            result = review_send_order_result_record(recorder_result, record_id, record_path)
            result["review"]["order_id"] = "MUTATED_ORDER"

            self.assertEqual(before, recorder_result)
            fresh = review_send_order_result_record(recorder_result, record_id, record_path)
            self.assertEqual("ORDER_REVIEW_RECORD_1", fresh["review"]["order_id"])

    def test_project_runtime_order_executions_is_not_created(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            record_path = self._record_path(temp_dir)
            recorder_result = self._recorded_result(record_path)
            record_id = recorder_result["record_report"]["record_id"]

            result = review_send_order_result_record(recorder_result, record_id, record_path)

            self.assertEqual("RECORD_REVIEW_OK", result["status"])
            self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
            self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
