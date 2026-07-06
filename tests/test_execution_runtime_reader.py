from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from execution_runtime_file_schema import (
    default_order_executions_data,
    default_order_locks_data,
)
from execution_runtime_reader import read_order_executions, read_order_locks


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionRuntimeReaderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.executions_path = self.root / "order_executions.json"
        self.locks_path = self.root / "order_locks.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_json(self, path: Path, data: object) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_read_order_executions_missing_file(self) -> None:
        result = read_order_executions(self.executions_path)

        self.assertFalse(result["ok"])
        self.assertEqual("MISSING", result["status"])
        self.assertEqual(str(self.executions_path), result["path"])
        self.assertIsNone(result["data"])
        self.assertIn("order_executions.json file not found", result["issues"])

    def test_read_order_locks_missing_file(self) -> None:
        result = read_order_locks(self.locks_path)

        self.assertFalse(result["ok"])
        self.assertEqual("MISSING", result["status"])
        self.assertEqual(str(self.locks_path), result["path"])
        self.assertIsNone(result["data"])
        self.assertIn("order_locks.json file not found", result["issues"])

    def test_corrupt_json_is_error(self) -> None:
        self.executions_path.write_text("{not-json", encoding="utf-8")

        result = read_order_executions(self.executions_path)

        self.assertFalse(result["ok"])
        self.assertEqual("ERROR", result["status"])
        self.assertTrue(result["issues"][0].startswith("failed to read order_executions.json json:"))

    def test_root_non_dict_is_invalid(self) -> None:
        self._write_json(self.executions_path, [])

        result = read_order_executions(self.executions_path)

        self.assertFalse(result["ok"])
        self.assertEqual("INVALID", result["status"])
        self.assertIsNone(result["data"])
        self.assertIn("order_executions.json root must be an object", result["issues"])

    def test_executions_field_missing_is_invalid(self) -> None:
        self._write_json(self.executions_path, {"version": 1, "updated_at": None})

        result = read_order_executions(self.executions_path)

        self.assertFalse(result["ok"])
        self.assertEqual("INVALID", result["status"])
        self.assertEqual({"version": 1, "updated_at": None}, result["data"])
        self.assertIn("executions field is required", result["issues"])

    def test_locks_field_missing_is_invalid(self) -> None:
        self._write_json(self.locks_path, {"version": 1, "updated_at": None})

        result = read_order_locks(self.locks_path)

        self.assertFalse(result["ok"])
        self.assertEqual("INVALID", result["status"])
        self.assertEqual({"version": 1, "updated_at": None}, result["data"])
        self.assertIn("locks field is required", result["issues"])

    def test_executions_field_non_list_is_invalid(self) -> None:
        self._write_json(self.executions_path, {"version": 1, "updated_at": None, "executions": {}})

        result = read_order_executions(self.executions_path)

        self.assertFalse(result["ok"])
        self.assertEqual("INVALID", result["status"])
        self.assertIn("executions field must be a list", result["issues"])

    def test_locks_field_non_list_is_invalid(self) -> None:
        self._write_json(self.locks_path, {"version": 1, "updated_at": None, "locks": {}})

        result = read_order_locks(self.locks_path)

        self.assertFalse(result["ok"])
        self.assertEqual("INVALID", result["status"])
        self.assertIn("locks field must be a list", result["issues"])

    def test_valid_order_executions_file_read(self) -> None:
        data = default_order_executions_data()
        data["executions"].append({"execution_id": "EXEC_1"})
        self._write_json(self.executions_path, data)

        result = read_order_executions(self.executions_path)

        self.assertTrue(result["ok"])
        self.assertEqual("READY", result["status"])
        self.assertEqual(data, result["data"])
        self.assertEqual([], result["issues"])
        self.assertEqual([], result["warnings"])

    def test_valid_order_locks_file_read(self) -> None:
        data = default_order_locks_data()
        data["locks"].append({"lock_id": "LOCK_1"})
        self._write_json(self.locks_path, data)

        result = read_order_locks(self.locks_path)

        self.assertTrue(result["ok"])
        self.assertEqual("READY", result["status"])
        self.assertEqual(data, result["data"])
        self.assertEqual([], result["issues"])
        self.assertEqual([], result["warnings"])

    def test_returned_data_is_deepcopy(self) -> None:
        data = default_order_executions_data()
        data["executions"].append({"execution_id": "EXEC_1"})
        self._write_json(self.executions_path, data)

        result = read_order_executions(self.executions_path)
        result["data"]["executions"][0]["execution_id"] = "MUTATED_RESULT_ONLY"
        second_result = read_order_executions(self.executions_path)

        self.assertEqual(second_result["data"], data)

    def test_file_content_is_not_mutated(self) -> None:
        data = default_order_locks_data()
        data["locks"].append({"lock_id": "LOCK_1"})
        self._write_json(self.locks_path, data)
        before = self.locks_path.read_text(encoding="utf-8")

        read_order_locks(self.locks_path)

        self.assertEqual(before, self.locks_path.read_text(encoding="utf-8"))

    def test_reader_does_not_write_or_mkdir(self) -> None:
        data = default_order_executions_data()
        self._write_json(self.executions_path, data)

        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("pathlib.Path.mkdir") as mkdir,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = read_order_executions(self.executions_path)

        self.assertTrue(result["ok"])
        write_text.assert_not_called()
        mkdir.assert_not_called()
        open_mock.assert_not_called()

    def test_actual_runtime_and_rules_hash_unchanged(self) -> None:
        runtime_queue = ROOT / "runtime" / "order_queue.json"
        rules_path = ROOT / "routines" / "지표추종매매" / "rules.json"
        before_runtime = _sha256(runtime_queue)
        before_rules = _sha256(rules_path)

        data = default_order_executions_data()
        self._write_json(self.executions_path, data)
        read_order_executions(self.executions_path)

        self.assertEqual(before_runtime, _sha256(runtime_queue))
        self.assertEqual(before_rules, _sha256(rules_path))

    def test_input_path_object_is_not_mutated(self) -> None:
        data = default_order_executions_data()
        self._write_json(self.executions_path, data)
        before = deepcopy(self.executions_path)

        read_order_executions(self.executions_path)

        self.assertEqual(before, self.executions_path)


if __name__ == "__main__":
    unittest.main()
