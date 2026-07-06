from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from execution_runtime_commit_service import commit_execution_runtime_plan
from execution_runtime_file_schema import default_order_executions_data, default_order_locks_data


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _commit_orchestrator() -> dict:
    commit_plan = {
        "plan_type": "EXECUTION_RUNTIME_COMMIT_PLAN_PREVIEW",
        "status": "READY",
        "commit_ready": True,
        "preview_only": True,
        "runtime_write": False,
        "planned_targets": {
            "order_executions": "runtime/order_executions.json",
            "order_locks": "runtime/order_locks.json",
        },
        "planned_records": {
            "execution": {
                "execution_id": "EXEC_CONTRACT_1",
                "order_id": "ORDER_CONTRACT_1",
                "request_hash": "HASH_CONTRACT_1",
                "lock_id": "LOCK_CONTRACT_1",
                "status": "RUNTIME_WRITE_PREVIEW",
                "preview_only": True,
                "runtime_write": False,
            },
            "lock": {
                "lock_id": "LOCK_CONTRACT_1",
                "order_id": "ORDER_CONTRACT_1",
                "request_hash": "HASH_CONTRACT_1",
                "execution_id": "EXEC_CONTRACT_1",
                "status": "RUNTIME_WRITE_PREVIEW",
                "preview_only": True,
                "runtime_write": False,
            },
        },
        "required_confirmations": {
            "manual_execution_runtime_commit_confirmed": True,
            "manual_runtime_file_write_confirmed": True,
        },
        "issues": [],
        "warnings": ["Preview mode"],
    }
    return {
        "orchestrator_type": "EXECUTION_RUNTIME_COMMIT_PLAN_ORCHESTRATOR",
        "status": "READY",
        "commit_ready": True,
        "preview_only": True,
        "runtime_write": False,
        "commit_plan": commit_plan,
        "validation": {
            "valid": True,
            "status": "READY",
            "preview_only": True,
            "runtime_write": False,
            "issues": [],
            "warnings": [],
        },
        "issues": [],
        "warnings": ["Preview mode"],
    }


def _confirmations() -> dict:
    return {
        "manual_execution_runtime_commit_confirmed": True,
        "manual_runtime_file_write_confirmed": True,
    }


class ExecutionRuntimeCommitServiceContractTest(unittest.TestCase):
    def _paths(self, temp_dir: str) -> tuple[Path, Path]:
        executions = Path(temp_dir) / "order_executions.json"
        locks = Path(temp_dir) / "order_locks.json"
        _write_json(executions, default_order_executions_data())
        _write_json(locks, default_order_locks_data())
        return executions, locks

    def test_valid_temp_files_valid_plan_and_confirmations_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executions, locks = self._paths(temp_dir)

            result = commit_execution_runtime_plan(
                _commit_orchestrator(),
                executions,
                locks,
                context=_confirmations(),
            )

            self.assertEqual("COMMITTED", result["status"])
            self.assertTrue(result["runtime_write"])
            self.assertTrue(result["committed"])
            self.assertTrue(result["read_back_verified"])

    def test_project_runtime_order_executions_path_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _, locks = self._paths(temp_dir)

            result = commit_execution_runtime_plan(
                _commit_orchestrator(),
                ROOT / "runtime" / "order_executions.json",
                locks,
                context=_confirmations(),
            )

            self.assertEqual("BLOCKED", result["status"])
            self.assertFalse(result["runtime_write"])
            self.assertFalse(result["committed"])
            self.assertIn("PROJECT_RUNTIME_PATH_BLOCKED", result["issues"])

    def test_project_runtime_order_locks_path_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executions, _ = self._paths(temp_dir)

            result = commit_execution_runtime_plan(
                _commit_orchestrator(),
                executions,
                ROOT / "runtime" / "order_locks.json",
                context=_confirmations(),
            )

            self.assertEqual("BLOCKED", result["status"])
            self.assertFalse(result["runtime_write"])
            self.assertFalse(result["committed"])
            self.assertIn("PROJECT_RUNTIME_PATH_BLOCKED", result["issues"])

    def test_missing_target_files_are_blocked_and_not_created(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executions = Path(temp_dir) / "order_executions.json"
            locks = Path(temp_dir) / "order_locks.json"

            result = commit_execution_runtime_plan(
                _commit_orchestrator(),
                executions,
                locks,
                context=_confirmations(),
            )

            self.assertEqual("BLOCKED", result["status"])
            self.assertFalse(executions.exists())
            self.assertFalse(locks.exists())
            self.assertIn("MISSING_ORDER_EXECUTIONS_FILE", result["issues"])

    def test_mkdir_is_not_called(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executions, locks = self._paths(temp_dir)
            with mock.patch("pathlib.Path.mkdir") as mkdir:
                result = commit_execution_runtime_plan(
                    _commit_orchestrator(),
                    executions,
                    locks,
                    context=_confirmations(),
                )

            self.assertEqual("COMMITTED", result["status"])
            mkdir.assert_not_called()

    def test_duplicate_execution_id_request_hash_order_id_lock_id_are_blocked(self) -> None:
        duplicate_cases = [
            ("executions", "execution_id", "EXEC_CONTRACT_1", "DUPLICATE_EXECUTION_ID"),
            ("executions", "request_hash", "HASH_CONTRACT_1", "DUPLICATE_REQUEST_HASH"),
            ("executions", "order_id", "ORDER_CONTRACT_1", "DUPLICATE_ORDER_ID"),
            ("locks", "lock_id", "LOCK_CONTRACT_1", "DUPLICATE_LOCK_ID"),
        ]

        for target, field, value, expected_issue in duplicate_cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp_dir:
                executions, locks = self._paths(temp_dir)
                if target == "executions":
                    data = default_order_executions_data()
                    data["executions"].append({field: value})
                    _write_json(executions, data)
                else:
                    data = default_order_locks_data()
                    data["locks"].append({field: value})
                    _write_json(locks, data)

                result = commit_execution_runtime_plan(
                    _commit_orchestrator(),
                    executions,
                    locks,
                    context=_confirmations(),
                )

                self.assertEqual("BLOCKED", result["status"])
                self.assertFalse(result["runtime_write"])
                self.assertFalse(result["committed"])
                self.assertIn(expected_issue, result["issues"])

    def test_backup_temp_cleanup_and_read_back_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executions, locks = self._paths(temp_dir)

            result = commit_execution_runtime_plan(
                _commit_orchestrator(),
                executions,
                locks,
                context=_confirmations(),
            )

            self.assertEqual("COMMITTED", result["status"])
            self.assertTrue(Path(result["backup_paths"]["order_executions"]).exists())
            self.assertTrue(Path(result["backup_paths"]["order_locks"]).exists())
            self.assertTrue(result["read_back_verified"])
            self.assertEqual([], [item.name for item in Path(temp_dir).iterdir() if item.name.startswith(".")])

    def test_runtime_write_and_committed_true_only_when_committed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executions, locks = self._paths(temp_dir)
            committed = commit_execution_runtime_plan(
                _commit_orchestrator(),
                executions,
                locks,
                context=_confirmations(),
            )
            blocked = commit_execution_runtime_plan(
                _commit_orchestrator(),
                executions,
                locks,
                context=_confirmations(),
            )
            invalid = commit_execution_runtime_plan(
                "bad",
                executions,
                locks,
                context=_confirmations(),
            )

            self.assertEqual("COMMITTED", committed["status"])
            self.assertTrue(committed["runtime_write"])
            self.assertTrue(committed["committed"])
            for result in (blocked, invalid):
                self.assertNotEqual("COMMITTED", result["status"])
                self.assertFalse(result["runtime_write"])
                self.assertFalse(result["committed"])

    def test_no_send_order_execution_controller_queue_commit_or_gui_calls(self) -> None:
        import execution_runtime_commit_service

        module_text = execution_runtime_commit_service.__loader__.get_source(
            execution_runtime_commit_service.__name__
        )

        self.assertNotIn("send_order", module_text.lower())
        self.assertNotIn("ExecutionController", module_text)
        self.assertNotIn("commit_execution_queue", module_text)
        self.assertNotIn("QWidget", module_text)
        self.assertNotIn("QDialog", module_text)

    def test_actual_runtime_and_rules_hash_unchanged(self) -> None:
        runtime_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        rules_paths = list((ROOT / "routines").glob("**/rules.json"))
        before_runtime = {str(path): _sha256(path) for path in runtime_paths}
        before_rules = {str(path): _sha256(path) for path in rules_paths}

        with tempfile.TemporaryDirectory() as temp_dir:
            executions, locks = self._paths(temp_dir)
            commit_execution_runtime_plan(
                _commit_orchestrator(),
                executions,
                locks,
                context=_confirmations(),
            )

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})


if __name__ == "__main__":
    unittest.main()
