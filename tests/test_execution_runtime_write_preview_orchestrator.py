from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_runtime_file_schema import (
    default_order_executions_data,
    default_order_locks_data,
)
from execution_runtime_write_preview_orchestrator import (
    ORCHESTRATOR_TYPE,
    run_execution_runtime_write_preview_orchestrator,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionRuntimeWritePreviewOrchestratorTest(unittest.TestCase):
    def _catalog(self, *, status: str = "READY", execution_id: str = "EXEC_1") -> dict:
        return {
            "status": status,
            "preview_only": True,
            "runtime_write": False,
            "catalog_type": "EXECUTION_RUNTIME_CATALOG_PREVIEW",
            "execution_id": execution_id,
            "order_id": "ORDER_1",
            "request_hash": "HASH_1",
            "lock_id": "LOCK_1",
            "runtime_targets": {
                "order_executions": "runtime/order_executions.json",
                "order_locks": "runtime/order_locks.json",
            },
            "checks": {},
            "warnings": ["Preview mode"],
            "issues": [] if status == "READY" else [f"CATALOG_{status}"],
        }

    def _catalog_orchestrator_result(self) -> dict:
        return {
            "status": "READY",
            "preview_only": True,
            "runtime_write": False,
            "orchestrator_type": "EXECUTION_RUNTIME_CATALOG_ORCHESTRATOR_PREVIEW",
            "catalog_preview": self._catalog(),
            "validation": {"valid": True},
            "issues": [],
            "warnings": ["Preview mode"],
        }

    def _executions(self) -> dict:
        return default_order_executions_data()

    def _locks(self) -> dict:
        return default_order_locks_data()

    def _run(self, catalog: object | None = None, *, executions: dict | None = None, locks: dict | None = None) -> dict:
        return run_execution_runtime_write_preview_orchestrator(
            self._catalog() if catalog is None else catalog,
            existing_order_executions_data=self._executions() if executions is None else executions,
            existing_order_locks_data=self._locks() if locks is None else locks,
        )

    def test_ready_flow(self) -> None:
        result = self._run()

        self.assertEqual("READY", result["status"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertEqual(ORCHESTRATOR_TYPE, result["orchestrator_type"])
        self.assertEqual("READY", result["write_preview"]["status"])
        self.assertTrue(result["validation"]["valid"])
        self.assertEqual([], result["issues"])
        self.assertIn("Preview mode", result["warnings"])

    def test_blocked_flow(self) -> None:
        executions = self._executions()
        executions["executions"].append({"execution_id": "EXEC_1"})

        result = self._run(executions=executions)

        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("BLOCKED", result["write_preview"]["status"])
        self.assertTrue(result["validation"]["valid"])
        self.assertIn("DUPLICATE_EXECUTION_ID", result["issues"])

    def test_invalid_flow(self) -> None:
        result = self._run(self._catalog(execution_id=""))

        self.assertEqual("INVALID", result["status"])
        self.assertEqual("INVALID", result["write_preview"]["status"])
        self.assertTrue(result["validation"]["valid"])
        self.assertIn("MISSING_EXECUTION_ID", result["issues"])

    def test_validator_invalid_flow(self) -> None:
        invalid_validation = {
            "valid": False,
            "status": "INVALID",
            "preview_only": True,
            "runtime_write": False,
            "issues": ["VALIDATOR_FORCED_INVALID"],
            "warnings": [],
        }

        with mock.patch(
            "execution_runtime_write_preview_orchestrator.validate_execution_runtime_write_preview",
            return_value=invalid_validation,
        ):
            result = self._run()

        self.assertEqual("INVALID", result["status"])
        self.assertEqual("READY", result["write_preview"]["status"])
        self.assertEqual(invalid_validation, result["validation"])
        self.assertIn("VALIDATOR_FORCED_INVALID", result["issues"])

    def test_catalog_orchestrator_result_input_flow(self) -> None:
        result = run_execution_runtime_write_preview_orchestrator(
            catalog_orchestrator_result=self._catalog_orchestrator_result(),
            existing_order_executions_data=self._executions(),
            existing_order_locks_data=self._locks(),
        )

        self.assertEqual("READY", result["status"])
        self.assertEqual("EXEC_1", result["write_preview"]["execution_record_preview"]["execution_id"])

    def test_inputs_are_not_mutated(self) -> None:
        catalog = self._catalog()
        executions = self._executions()
        locks = self._locks()
        before = (deepcopy(catalog), deepcopy(executions), deepcopy(locks))

        result = run_execution_runtime_write_preview_orchestrator(
            catalog,
            existing_order_executions_data=executions,
            existing_order_locks_data=locks,
        )
        result["write_preview"]["execution_record_preview"]["execution_id"] = "MUTATED_RESULT_ONLY"
        result["validation"]["issues"].append("MUTATED_RESULT_ONLY")

        self.assertEqual(before[0], catalog)
        self.assertEqual(before[1], executions)
        self.assertEqual(before[2], locks)

    def test_no_file_write_or_mkdir(self) -> None:
        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("pathlib.Path.mkdir") as mkdir,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = self._run()

        self.assertEqual("READY", result["status"])
        write_text.assert_not_called()
        mkdir.assert_not_called()
        open_mock.assert_not_called()

    def test_runtime_and_rules_hash_unchanged(self) -> None:
        runtime_queue = ROOT / "runtime" / "order_queue.json"
        rules_path = ROOT / "routines" / "지표추종매매" / "rules.json"
        before_runtime = _sha256(runtime_queue)
        before_rules = _sha256(rules_path)

        self._run()

        self.assertEqual(before_runtime, _sha256(runtime_queue))
        self.assertEqual(before_rules, _sha256(rules_path))

    def test_module_has_no_write_commit_execution_send_order_gui_connections(self) -> None:
        import execution_runtime_write_preview_orchestrator

        module_text = execution_runtime_write_preview_orchestrator.__loader__.get_source(
            execution_runtime_write_preview_orchestrator.__name__
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
