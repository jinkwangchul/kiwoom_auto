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
from execution_runtime_storage import ExecutionRuntimeStorage


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionRuntimeStorageContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.executions_path = self.root / "order_executions.json"
        self.locks_path = self.root / "order_locks.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_json(self, path: Path, data: object) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_runtime_files(self, *, duplicate: bool = False) -> tuple[dict, dict]:
        executions = default_order_executions_data()
        locks = default_order_locks_data()
        if duplicate:
            executions["executions"].append({"execution_id": "EXEC_1"})
        self._write_json(self.executions_path, executions)
        self._write_json(self.locks_path, locks)
        return executions, locks

    def _storage(self) -> ExecutionRuntimeStorage:
        return ExecutionRuntimeStorage(self.executions_path, self.locks_path)

    def _catalog_orchestrator_result(self) -> dict:
        return {
            "status": "READY",
            "preview_only": True,
            "runtime_write": False,
            "orchestrator_type": "EXECUTION_RUNTIME_CATALOG_ORCHESTRATOR_PREVIEW",
            "catalog_preview": {
                "status": "READY",
                "preview_only": True,
                "runtime_write": False,
                "catalog_type": "EXECUTION_RUNTIME_CATALOG_PREVIEW",
                "execution_id": "EXEC_1",
                "order_id": "ORDER_1",
                "request_hash": "HASH_1",
                "lock_id": "LOCK_1",
                "runtime_targets": {
                    "order_executions": "runtime/order_executions.json",
                    "order_locks": "runtime/order_locks.json",
                },
                "checks": {},
                "warnings": ["Preview mode"],
                "issues": [],
            },
            "validation": {"valid": True},
            "issues": [],
            "warnings": ["Preview mode"],
        }

    def test_missing_execution_and_lock_files_read_safely(self) -> None:
        result = self._storage().read()

        self.assertFalse(result["ok"])
        self.assertEqual("MISSING", result["order_executions"]["status"])
        self.assertEqual("MISSING", result["order_locks"]["status"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])

    def test_valid_execution_and_lock_files_read_results_are_preserved(self) -> None:
        executions, locks = self._write_runtime_files()

        result = self._storage().read()

        self.assertTrue(result["ok"])
        self.assertEqual(executions, result["order_executions"]["data"])
        self.assertEqual(locks, result["order_locks"]["data"])

    def test_preview_write_uses_reader_results_ready(self) -> None:
        self._write_runtime_files()

        result = self._storage().preview_write(self._catalog_orchestrator_result())

        self.assertEqual("READY", result["status"])
        self.assertEqual("EXEC_1", result["write_preview"]["execution_record_preview"]["execution_id"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])

    def test_preview_write_uses_reader_results_blocked_duplicate(self) -> None:
        self._write_runtime_files(duplicate=True)

        result = self._storage().preview_write(self._catalog_orchestrator_result())

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("DUPLICATE_EXECUTION_ID", result["issues"])

    def test_preview_write_returns_invalid_when_reader_has_missing_data(self) -> None:
        result = self._storage().preview_write(self._catalog_orchestrator_result())

        self.assertEqual("INVALID", result["status"])
        self.assertIn("MALFORMED_EXISTING_DATA", result["issues"])

    def test_preview_commit_plan_without_confirmations_is_blocked(self) -> None:
        self._write_runtime_files()

        result = self._storage().preview_commit_plan(self._catalog_orchestrator_result(), {})

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["commit_ready"])
        self.assertIn("MANUAL_EXECUTION_RUNTIME_COMMIT_CONFIRMATION_REQUIRED", result["issues"])
        self.assertIn("MANUAL_RUNTIME_FILE_WRITE_CONFIRMATION_REQUIRED", result["issues"])

    def test_preview_commit_plan_with_confirmations_and_ready_write_is_ready(self) -> None:
        self._write_runtime_files()

        result = self._storage().preview_commit_plan(
            self._catalog_orchestrator_result(),
            {
                "manual_execution_runtime_commit_confirmed": True,
                "manual_runtime_file_write_confirmed": True,
            },
        )

        self.assertEqual("READY", result["status"])
        self.assertTrue(result["commit_ready"])
        self.assertEqual("EXEC_1", result["commit_plan"]["planned_records"]["execution"]["execution_id"])

    def test_commit_is_blocked_by_not_implemented(self) -> None:
        with self.assertRaises(NotImplementedError):
            self._storage().commit()

    def test_path_auto_runtime_default_is_not_used(self) -> None:
        storage = self._storage()

        self.assertEqual(self.executions_path, storage.order_executions_path)
        self.assertEqual(self.locks_path, storage.order_locks_path)
        self.assertNotEqual(Path("runtime") / "order_executions.json", storage.order_executions_path)
        self.assertNotEqual(Path("runtime") / "order_locks.json", storage.order_locks_path)

    def test_preview_only_and_runtime_write_boundaries_are_preserved(self) -> None:
        self._write_runtime_files()

        read_result = self._storage().read()
        write_result = self._storage().preview_write(self._catalog_orchestrator_result())
        plan_result = self._storage().preview_commit_plan(
            self._catalog_orchestrator_result(),
            {
                "manual_execution_runtime_commit_confirmed": True,
                "manual_runtime_file_write_confirmed": True,
            },
        )

        for result in (read_result, write_result, plan_result):
            self.assertTrue(result["preview_only"])
            self.assertFalse(result["runtime_write"])

    def test_inputs_remain_unchanged(self) -> None:
        self._write_runtime_files()
        catalog = self._catalog_orchestrator_result()
        confirmations = {
            "manual_execution_runtime_commit_confirmed": True,
            "manual_runtime_file_write_confirmed": True,
        }
        before = (deepcopy(catalog), deepcopy(confirmations))

        result = self._storage().preview_commit_plan(catalog, confirmations)
        result["commit_plan"]["planned_records"]["execution"]["execution_id"] = "MUTATED"

        self.assertEqual(before[0], catalog)
        self.assertEqual(before[1], confirmations)

    def test_no_mkdir_or_write(self) -> None:
        self._write_runtime_files()

        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("pathlib.Path.mkdir") as mkdir,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = self._storage().preview_commit_plan(
                self._catalog_orchestrator_result(),
                {
                    "manual_execution_runtime_commit_confirmed": True,
                    "manual_runtime_file_write_confirmed": True,
                },
            )

        self.assertEqual("READY", result["status"])
        write_text.assert_not_called()
        mkdir.assert_not_called()
        open_mock.assert_not_called()

    def test_actual_runtime_and_rules_hash_unchanged(self) -> None:
        self._write_runtime_files()
        runtime_queue = ROOT / "runtime" / "order_queue.json"
        rules_path = ROOT / "routines" / "지표추종매매" / "rules.json"
        before_runtime = _sha256(runtime_queue)
        before_rules = _sha256(rules_path)

        self._storage().preview_commit_plan(
            self._catalog_orchestrator_result(),
            {
                "manual_execution_runtime_commit_confirmed": True,
                "manual_runtime_file_write_confirmed": True,
            },
        )

        self.assertEqual(before_runtime, _sha256(runtime_queue))
        self.assertEqual(before_rules, _sha256(rules_path))


if __name__ == "__main__":
    unittest.main()
