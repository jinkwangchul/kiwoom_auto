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


class ExecutionRuntimeStorageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.executions_path = self.root / "order_executions.json"
        self.locks_path = self.root / "order_locks.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_json(self, path: Path, data: object) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_runtime_files(self, *, duplicate: bool = False) -> None:
        executions = default_order_executions_data()
        locks = default_order_locks_data()
        if duplicate:
            executions["executions"].append({"execution_id": "EXEC_1"})
            locks["locks"].append({"lock_id": "OTHER_LOCK"})
        self._write_json(self.executions_path, executions)
        self._write_json(self.locks_path, locks)

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

    def test_init_preserves_injected_paths(self) -> None:
        storage = self._storage()

        self.assertEqual(self.executions_path, storage.order_executions_path)
        self.assertEqual(self.locks_path, storage.order_locks_path)

    def test_init_requires_explicit_paths(self) -> None:
        with self.assertRaises(ValueError):
            ExecutionRuntimeStorage("", self.locks_path)
        with self.assertRaises(ValueError):
            ExecutionRuntimeStorage(self.executions_path, "")

    def test_read_missing_files_returns_safe_result(self) -> None:
        result = self._storage().read()

        self.assertFalse(result["ok"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertEqual("MISSING", result["order_executions"]["status"])
        self.assertEqual("MISSING", result["order_locks"]["status"])

    def test_read_valid_files_returns_data(self) -> None:
        self._write_runtime_files()

        result = self._storage().read()

        self.assertTrue(result["ok"])
        self.assertEqual(default_order_executions_data(), result["order_executions"]["data"])
        self.assertEqual(default_order_locks_data(), result["order_locks"]["data"])

    def test_preview_write_ready_flow(self) -> None:
        self._write_runtime_files()

        result = self._storage().preview_write(self._catalog_orchestrator_result())

        self.assertEqual("READY", result["status"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertEqual("EXEC_1", result["write_preview"]["execution_record_preview"]["execution_id"])

    def test_preview_write_duplicate_blocked_flow(self) -> None:
        self._write_runtime_files(duplicate=True)

        result = self._storage().preview_write(self._catalog_orchestrator_result())

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("DUPLICATE_EXECUTION_ID", result["issues"])

    def test_preview_commit_plan_missing_confirmation_blocked(self) -> None:
        self._write_runtime_files()

        result = self._storage().preview_commit_plan(
            self._catalog_orchestrator_result(),
            {
                "manual_execution_runtime_commit_confirmed": True,
                "manual_runtime_file_write_confirmed": False,
            },
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["commit_ready"])
        self.assertIn("MANUAL_RUNTIME_FILE_WRITE_CONFIRMATION_REQUIRED", result["issues"])

    def test_preview_commit_plan_confirmed_ready(self) -> None:
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

    def test_commit_is_not_implemented(self) -> None:
        with self.assertRaises(NotImplementedError):
            self._storage().commit()

    def test_input_immutability(self) -> None:
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

    def test_storage_methods_do_not_write_or_mkdir(self) -> None:
        self._write_runtime_files()
        storage = self._storage()

        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("pathlib.Path.mkdir") as mkdir,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = storage.preview_commit_plan(
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

    def test_runtime_and_rules_hash_unchanged(self) -> None:
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

    def test_module_has_no_commit_write_execution_send_order_gui_connections(self) -> None:
        import execution_runtime_storage

        module_text = execution_runtime_storage.__loader__.get_source(
            execution_runtime_storage.__name__
        )

        self.assertNotIn("write_text", module_text)
        self.assertNotIn("mkdir", module_text)
        self.assertNotIn("os.replace", module_text)
        self.assertNotIn("commit_execution_queue", module_text)
        self.assertNotIn("send_order", module_text)
        self.assertNotIn("ExecutionController", module_text)
        self.assertNotIn("QWidget", module_text)


if __name__ == "__main__":
    unittest.main()
