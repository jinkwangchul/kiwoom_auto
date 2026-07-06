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
from execution_runtime_write_preview import build_execution_runtime_write_preview
from execution_runtime_write_preview_orchestrator import (
    run_execution_runtime_write_preview_orchestrator,
)
from execution_runtime_write_preview_validator import validate_execution_runtime_write_preview


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionRuntimeWritePreviewContractTest(unittest.TestCase):
    def _catalog(self, *, status: str = "READY") -> dict:
        return {
            "status": status,
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
            "issues": [] if status == "READY" else [f"CATALOG_{status}"],
        }

    def _executions(self) -> dict:
        return default_order_executions_data()

    def _locks(self) -> dict:
        return default_order_locks_data()

    def test_build_validate_orchestrator_ready_contract(self) -> None:
        catalog = self._catalog()
        executions = self._executions()
        locks = self._locks()

        write_preview = build_execution_runtime_write_preview(
            catalog,
            existing_order_executions_data=executions,
            existing_order_locks_data=locks,
        )
        validation = validate_execution_runtime_write_preview(write_preview)
        orchestrated = run_execution_runtime_write_preview_orchestrator(
            catalog,
            existing_order_executions_data=executions,
            existing_order_locks_data=locks,
        )

        self.assertEqual("READY", write_preview["status"])
        self.assertTrue(validation["valid"])
        self.assertEqual("READY", orchestrated["status"])
        self.assertEqual(write_preview, orchestrated["write_preview"])

    def test_blocked_duplicate_status_is_preserved(self) -> None:
        catalog = self._catalog()
        executions = self._executions()
        executions["executions"].append({"execution_id": "EXEC_1"})

        write_preview = build_execution_runtime_write_preview(
            catalog,
            existing_order_executions_data=executions,
            existing_order_locks_data=self._locks(),
        )
        validation = validate_execution_runtime_write_preview(write_preview)
        orchestrated = run_execution_runtime_write_preview_orchestrator(
            catalog,
            existing_order_executions_data=executions,
            existing_order_locks_data=self._locks(),
        )

        self.assertEqual("BLOCKED", write_preview["status"])
        self.assertTrue(validation["valid"])
        self.assertEqual("BLOCKED", orchestrated["status"])
        self.assertIn("DUPLICATE_EXECUTION_ID", orchestrated["issues"])

    def test_invalid_malformed_status_is_preserved(self) -> None:
        write_preview = build_execution_runtime_write_preview(
            "malformed",
            existing_order_executions_data=self._executions(),
            existing_order_locks_data=self._locks(),
        )
        validation = validate_execution_runtime_write_preview(write_preview)
        orchestrated = run_execution_runtime_write_preview_orchestrator(
            "malformed",
            existing_order_executions_data=self._executions(),
            existing_order_locks_data=self._locks(),
        )

        self.assertEqual("INVALID", write_preview["status"])
        self.assertTrue(validation["valid"])
        self.assertEqual("INVALID", orchestrated["status"])
        self.assertIn("MALFORMED_CATALOG_INPUT", orchestrated["issues"])

    def test_preview_only_and_runtime_write_boundaries_are_preserved(self) -> None:
        orchestrated = run_execution_runtime_write_preview_orchestrator(
            self._catalog(),
            existing_order_executions_data=self._executions(),
            existing_order_locks_data=self._locks(),
        )

        self.assertTrue(orchestrated["preview_only"])
        self.assertFalse(orchestrated["runtime_write"])
        self.assertTrue(orchestrated["write_preview"]["preview_only"])
        self.assertFalse(orchestrated["write_preview"]["runtime_write"])
        self.assertTrue(orchestrated["validation"]["preview_only"])
        self.assertFalse(orchestrated["validation"]["runtime_write"])

    def test_execution_record_preview_fields_are_preserved(self) -> None:
        orchestrated = run_execution_runtime_write_preview_orchestrator(
            self._catalog(),
            existing_order_executions_data=self._executions(),
            existing_order_locks_data=self._locks(),
        )
        record = orchestrated["write_preview"]["execution_record_preview"]

        self.assertEqual("EXEC_1", record["execution_id"])
        self.assertEqual("ORDER_1", record["order_id"])
        self.assertEqual("HASH_1", record["request_hash"])
        self.assertEqual("LOCK_1", record["lock_id"])

    def test_lock_record_preview_fields_are_preserved(self) -> None:
        orchestrated = run_execution_runtime_write_preview_orchestrator(
            self._catalog(),
            existing_order_executions_data=self._executions(),
            existing_order_locks_data=self._locks(),
        )
        record = orchestrated["write_preview"]["lock_record_preview"]

        self.assertEqual("LOCK_1", record["lock_id"])
        self.assertEqual("ORDER_1", record["order_id"])
        self.assertEqual("HASH_1", record["request_hash"])
        self.assertEqual("EXEC_1", record["execution_id"])

    def test_would_write_targets_are_preserved(self) -> None:
        catalog = self._catalog()
        orchestrated = run_execution_runtime_write_preview_orchestrator(
            catalog,
            existing_order_executions_data=self._executions(),
            existing_order_locks_data=self._locks(),
        )

        self.assertEqual(
            catalog["runtime_targets"],
            orchestrated["write_preview"]["would_write_targets"],
        )

    def test_issues_and_warnings_are_preserved(self) -> None:
        catalog = self._catalog(status="BLOCKED")
        orchestrated = run_execution_runtime_write_preview_orchestrator(
            catalog,
            existing_order_executions_data=self._executions(),
            existing_order_locks_data=self._locks(),
        )

        self.assertIn("CATALOG_BLOCKED", orchestrated["issues"])
        self.assertIn("Preview mode", orchestrated["warnings"])

    def test_inputs_remain_unchanged_across_contract_flow(self) -> None:
        catalog = self._catalog()
        executions = self._executions()
        locks = self._locks()
        before = (deepcopy(catalog), deepcopy(executions), deepcopy(locks))

        orchestrated = run_execution_runtime_write_preview_orchestrator(
            catalog,
            existing_order_executions_data=executions,
            existing_order_locks_data=locks,
        )
        orchestrated["write_preview"]["execution_record_preview"]["execution_id"] = "MUTATED_RESULT_ONLY"
        orchestrated["write_preview"]["would_write_targets"]["order_locks"] = "changed"

        self.assertEqual(before[0], catalog)
        self.assertEqual(before[1], executions)
        self.assertEqual(before[2], locks)

    def test_no_file_write_or_mkdir(self) -> None:
        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("pathlib.Path.mkdir") as mkdir,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            orchestrated = run_execution_runtime_write_preview_orchestrator(
                self._catalog(),
                existing_order_executions_data=self._executions(),
                existing_order_locks_data=self._locks(),
            )

        self.assertEqual("READY", orchestrated["status"])
        write_text.assert_not_called()
        mkdir.assert_not_called()
        open_mock.assert_not_called()

    def test_runtime_and_rules_hash_unchanged(self) -> None:
        runtime_queue = ROOT / "runtime" / "order_queue.json"
        rules_path = ROOT / "routines" / "지표추종매매" / "rules.json"
        before_runtime = _sha256(runtime_queue)
        before_rules = _sha256(rules_path)

        run_execution_runtime_write_preview_orchestrator(
            self._catalog(),
            existing_order_executions_data=self._executions(),
            existing_order_locks_data=self._locks(),
        )

        self.assertEqual(before_runtime, _sha256(runtime_queue))
        self.assertEqual(before_rules, _sha256(rules_path))


if __name__ == "__main__":
    unittest.main()
