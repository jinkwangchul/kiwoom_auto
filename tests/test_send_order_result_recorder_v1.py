# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from execution_runtime_file_schema import default_order_executions_data
from send_order_result_recorder_v1 import record_send_order_result


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


class SendOrderResultRecorderV1Test(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}

    def tearDown(self) -> None:
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def _record_path(self, temp_dir: str) -> Path:
        path = Path(temp_dir) / "runtime" / "order_executions.json"
        path.parent.mkdir()
        _write_json(path, default_order_executions_data())
        return path

    def _contract_result(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "status": "RECORD_READY",
            "record_contract": {
                "contract_type": "SEND_ORDER_RESULT_RECORDER_CONTRACT",
                "dispatch_id": "DISPATCH_RECORD_V1",
                "order_id": "ORDER_RECORD_V1",
                "source_order_id": "SOURCE_ORDER_RECORD_V1",
                "source_signal_id": "SIGNAL_RECORD_V1",
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
        result.update(overrides)
        return result

    def test_recorded_normal_temp_order_executions_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            record_path = self._record_path(temp_dir)

            result = record_send_order_result(self._contract_result(), record_path, manual_confirmation=True)

            self.assertEqual("RECORDED", result["status"])
            self.assertTrue(result["record_called"])
            self.assertTrue(result["runtime_write"])
            self.assertFalse(result["queue_write"])
            self.assertFalse(result["chejan_called"])
            report = result["record_report"]
            self.assertTrue(Path(report["backup_path"]).exists())
            self.assertNotEqual(report["before_hash"], report["after_hash"])

            data = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(1, len(data["executions"]))
            record = data["executions"][0]
            self.assertEqual("DISPATCH_RECORD_V1", record["dispatch_id"])
            self.assertEqual("ORDER_RECORD_V1", record["order_id"])
            self.assertEqual("SEND_ORDER_RESULT_RECORDED", record["status"])

        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())

    def test_manual_confirmation_false_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            record_path = self._record_path(temp_dir)

            result = record_send_order_result(self._contract_result(), record_path, manual_confirmation=False)

            self.assertEqual("BLOCKED", result["status"])
            self.assertFalse(result["record_called"])
            self.assertFalse(result["runtime_write"])
            data = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual([], data["executions"])

    def test_contract_blocked_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            record_path = self._record_path(temp_dir)

            result = record_send_order_result(
                self._contract_result(status="BLOCKED", record_ready=False),
                record_path,
                manual_confirmation=True,
            )

            self.assertEqual("BLOCKED", result["status"])
            self.assertFalse(result["record_called"])

    def test_contract_invalid_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            record_path = self._record_path(temp_dir)

            result = record_send_order_result(
                self._contract_result(status="INVALID", record_ready=False),
                record_path,
                manual_confirmation=True,
            )

            self.assertEqual("INVALID", result["status"])
            self.assertFalse(result["record_called"])

    def test_malformed_record_file_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "runtime" / "order_executions.json"
            path.parent.mkdir()
            _write_json(path, {"version": 1, "updated_at": None, "executions": {}})

            result = record_send_order_result(self._contract_result(), path, manual_confirmation=True)

            self.assertEqual("INVALID", result["status"])
            self.assertIn("order_executions executions must be a list", result["issues"])

    def test_duplicate_dispatch_or_order_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            record_path = self._record_path(temp_dir)
            data = default_order_executions_data()
            data["executions"].append({"dispatch_id": "DISPATCH_RECORD_V1", "order_id": "OTHER"})
            _write_json(record_path, data)

            result = record_send_order_result(self._contract_result(), record_path, manual_confirmation=True)

            self.assertEqual("BLOCKED", result["status"])
            self.assertIn("duplicate dispatch_id", result["issues"])

        with tempfile.TemporaryDirectory() as temp_dir:
            record_path = self._record_path(temp_dir)
            data = default_order_executions_data()
            data["executions"].append({"dispatch_id": "OTHER", "order_id": "ORDER_RECORD_V1"})
            _write_json(record_path, data)

            result = record_send_order_result(self._contract_result(), record_path, manual_confirmation=True)

            self.assertEqual("BLOCKED", result["status"])
            self.assertIn("duplicate order_id", result["issues"])

    def test_project_runtime_order_executions_path_is_invalid(self) -> None:
        result = record_send_order_result(
            self._contract_result(),
            ROOT / "runtime" / "order_executions.json",
            manual_confirmation=True,
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("project runtime/order_executions.json is not allowed", result["issues"])
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())

    def test_record_path_guard_violation_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "runtime" / "not_order_executions.json"
            path.parent.mkdir()
            _write_json(path, default_order_executions_data())

            result = record_send_order_result(self._contract_result(), path, manual_confirmation=True)

            self.assertEqual("INVALID", result["status"])
            self.assertIn("record_path must resolve to order_executions.json", result["issues"])

    def test_post_write_verification_failure_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            record_path = self._record_path(temp_dir)
            original_text = record_path.read_text(encoding="utf-8")

            with mock.patch("send_order_result_recorder_v1._verify_record", return_value=False):
                result = record_send_order_result(self._contract_result(), record_path, manual_confirmation=True)

            self.assertEqual("ERROR", result["status"])
            report = result["record_report"]
            self.assertTrue(report["rollback_attempted"])
            self.assertTrue(report["rollback_succeeded"])
            self.assertTrue(report["restored_from_backup"])
            self.assertFalse(report["manual_restore_required"])
            self.assertEqual(original_text, record_path.read_text(encoding="utf-8"))

    def test_write_exception_rolls_back_from_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            record_path = self._record_path(temp_dir)
            original_text = record_path.read_text(encoding="utf-8")

            with mock.patch("send_order_result_recorder_v1._write_json_atomic", side_effect=OSError("boom")):
                result = record_send_order_result(self._contract_result(), record_path, manual_confirmation=True)

            self.assertEqual("ERROR", result["status"])
            self.assertTrue(result["record_report"]["rollback_attempted"])
            self.assertTrue(result["record_report"]["rollback_succeeded"])
            self.assertEqual(original_text, record_path.read_text(encoding="utf-8"))

    def test_send_order_chejan_queue_and_rules_are_not_called_or_changed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            record_path = self._record_path(temp_dir)

            result = record_send_order_result(self._contract_result(), record_path, manual_confirmation=True)

            self.assertEqual("RECORDED", result["status"])
            self.assertFalse(result["queue_write"])
            self.assertFalse(result["chejan_called"])
            self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
            self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
