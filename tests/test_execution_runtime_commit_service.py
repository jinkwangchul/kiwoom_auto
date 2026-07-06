from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from execution_runtime_commit_service import (
    SERVICE_TYPE,
    commit_execution_runtime_plan,
)
from execution_runtime_file_schema import default_order_executions_data, default_order_locks_data
from execution_runtime_real_commit_readiness_policy import evaluate_execution_runtime_real_commit_readiness


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


class ExecutionRuntimeCommitServiceTest(unittest.TestCase):
    def _commit_orchestrator(self) -> dict:
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
                    "execution_id": "EXEC_1",
                    "order_id": "ORDER_1",
                    "request_hash": "HASH_1",
                    "lock_id": "LOCK_1",
                    "status": "RUNTIME_WRITE_PREVIEW",
                    "preview_only": True,
                    "runtime_write": False,
                },
                "lock": {
                    "lock_id": "LOCK_1",
                    "order_id": "ORDER_1",
                    "request_hash": "HASH_1",
                    "execution_id": "EXEC_1",
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

    def _context(self) -> dict:
        return {
            "manual_execution_runtime_commit_confirmed": True,
            "manual_runtime_file_write_confirmed": True,
        }

    def _real_commit_policy(self, orchestrator: dict, executions: Path, locks: Path) -> dict:
        return evaluate_execution_runtime_real_commit_readiness(
            runtime_api_result={
                "api_type": "EXECUTION_RUNTIME_API",
                "status": "READY",
                "dry_run": True,
                "preview_only": True,
                "runtime_write": False,
                "issues": [],
                "warnings": [],
            },
            commit_plan_orchestrator_result=orchestrator,
            order_executions_path=executions,
            order_locks_path=locks,
            confirmations=self._context(),
            environment_flags={
                "real_runtime_commit_enabled": True,
                "allow_project_runtime_commit": True,
            },
        )

    def _paths(self, temp_dir: str) -> tuple[Path, Path]:
        base = Path(temp_dir)
        executions = base / "order_executions.json"
        locks = base / "order_locks.json"
        _write_json(executions, default_order_executions_data())
        _write_json(locks, default_order_locks_data())
        return executions, locks

    def test_temp_path_valid_files_commit_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executions, locks = self._paths(temp_dir)

            result = commit_execution_runtime_plan(
                self._commit_orchestrator(),
                executions,
                locks,
                context=self._context(),
            )

            self.assertEqual(SERVICE_TYPE, result["service_type"])
            self.assertEqual("COMMITTED", result["status"])
            self.assertTrue(result["runtime_write"])
            self.assertTrue(result["committed"])
            self.assertTrue(result["read_back_verified"])
            self.assertEqual([], result["issues"])

            execution_data = json.loads(executions.read_text(encoding="utf-8"))
            lock_data = json.loads(locks.read_text(encoding="utf-8"))
            self.assertEqual("EXEC_1", execution_data["executions"][0]["execution_id"])
            self.assertEqual("LOCK_1", lock_data["locks"][0]["lock_id"])

    def test_missing_order_executions_file_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executions = Path(temp_dir) / "order_executions.json"
            locks = Path(temp_dir) / "order_locks.json"
            _write_json(locks, default_order_locks_data())

            result = commit_execution_runtime_plan(
                self._commit_orchestrator(),
                executions,
                locks,
                context=self._context(),
            )

            self.assertEqual("BLOCKED", result["status"])
            self.assertFalse(result["committed"])
            self.assertIn("MISSING_ORDER_EXECUTIONS_FILE", result["issues"])
            self.assertFalse(executions.exists())

    def test_missing_order_locks_file_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executions = Path(temp_dir) / "order_executions.json"
            locks = Path(temp_dir) / "order_locks.json"
            _write_json(executions, default_order_executions_data())

            result = commit_execution_runtime_plan(
                self._commit_orchestrator(),
                executions,
                locks,
                context=self._context(),
            )

            self.assertEqual("BLOCKED", result["status"])
            self.assertIn("MISSING_ORDER_LOCKS_FILE", result["issues"])
            self.assertFalse(locks.exists())

    def test_actual_project_runtime_path_blocked(self) -> None:
        result = commit_execution_runtime_plan(
            self._commit_orchestrator(),
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
            context=self._context(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["committed"])
        self.assertIn("PROJECT_RUNTIME_PATH_BLOCKED", result["issues"])

    def test_project_runtime_policy_missing_blocked(self) -> None:
        executions = ROOT / "runtime" / "order_executions.json"
        locks = ROOT / "runtime" / "order_locks.json"
        result = commit_execution_runtime_plan(
            self._commit_orchestrator(),
            executions,
            locks,
            context=self._context(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("PROJECT_RUNTIME_PATH_BLOCKED", result["issues"])

    def test_project_runtime_policy_ready_missing_final_confirmation_blocked(self) -> None:
        executions = ROOT / "runtime" / "order_executions.json"
        locks = ROOT / "runtime" / "order_locks.json"
        orchestrator = self._commit_orchestrator()
        result = commit_execution_runtime_plan(
            orchestrator,
            executions,
            locks,
            context=self._context(),
            real_commit_readiness_policy_result=self._real_commit_policy(orchestrator, executions, locks),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("MANUAL_PROJECT_RUNTIME_COMMIT_CONFIRMATION_REQUIRED", result["issues"])

    def test_project_runtime_policy_ready_existing_files_committed(self) -> None:
        executions = ROOT / "runtime" / "order_executions.json"
        locks = ROOT / "runtime" / "order_locks.json"
        if executions.exists() or locks.exists():
            result = commit_execution_runtime_plan(
                self._commit_orchestrator(),
                executions,
                locks,
                context=self._context(),
            )
            self.assertEqual("BLOCKED", result["status"])
            return

        _write_json(executions, default_order_executions_data())
        _write_json(locks, default_order_locks_data())
        orchestrator = self._commit_orchestrator()
        try:
            result = commit_execution_runtime_plan(
                orchestrator,
                executions,
                locks,
                context=self._context(),
                real_commit_readiness_policy_result=self._real_commit_policy(orchestrator, executions, locks),
                manual_project_runtime_commit_confirmed=True,
            )

            self.assertEqual("COMMITTED", result["status"])
            self.assertTrue(result["committed"])
            self.assertTrue(result["runtime_write"])
            self.assertTrue(result["read_back_verified"])
            execution_data = json.loads(executions.read_text(encoding="utf-8"))
            lock_data = json.loads(locks.read_text(encoding="utf-8"))
            self.assertEqual("EXEC_1", execution_data["executions"][0]["execution_id"])
            self.assertEqual("LOCK_1", lock_data["locks"][0]["lock_id"])
        finally:
            for path in (executions, locks, Path(str(executions) + ".bak"), Path(str(locks) + ".bak")):
                if path.exists():
                    path.unlink()

    def test_confirmations_missing_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executions, locks = self._paths(temp_dir)

            result = commit_execution_runtime_plan(
                self._commit_orchestrator(),
                executions,
                locks,
                context={"manual_execution_runtime_commit_confirmed": True},
            )

            self.assertEqual("BLOCKED", result["status"])
            self.assertIn("MANUAL_CONFIRMATIONS_REQUIRED", result["issues"])
            self.assertEqual([], json.loads(executions.read_text(encoding="utf-8"))["executions"])

    def test_invalid_commit_plan_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executions, locks = self._paths(temp_dir)

            result = commit_execution_runtime_plan("bad", executions, locks, context=self._context())

            self.assertEqual("INVALID", result["status"])
            self.assertIn("MALFORMED_COMMIT_PLAN_ORCHESTRATOR_RESULT", result["issues"])

    def test_duplicate_execution_id_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executions, locks = self._paths(temp_dir)
            data = default_order_executions_data()
            data["executions"].append({"execution_id": "EXEC_1"})
            _write_json(executions, data)

            result = commit_execution_runtime_plan(
                self._commit_orchestrator(),
                executions,
                locks,
                context=self._context(),
            )

            self.assertEqual("BLOCKED", result["status"])
            self.assertIn("DUPLICATE_EXECUTION_ID", result["issues"])

    def test_duplicate_request_hash_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executions, locks = self._paths(temp_dir)
            data = default_order_executions_data()
            data["executions"].append({"request_hash": "HASH_1"})
            _write_json(executions, data)

            result = commit_execution_runtime_plan(
                self._commit_orchestrator(),
                executions,
                locks,
                context=self._context(),
            )

            self.assertEqual("BLOCKED", result["status"])
            self.assertIn("DUPLICATE_REQUEST_HASH", result["issues"])

    def test_duplicate_order_id_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executions, locks = self._paths(temp_dir)
            data = default_order_executions_data()
            data["executions"].append({"order_id": "ORDER_1"})
            _write_json(executions, data)

            result = commit_execution_runtime_plan(
                self._commit_orchestrator(),
                executions,
                locks,
                context=self._context(),
            )

            self.assertEqual("BLOCKED", result["status"])
            self.assertIn("DUPLICATE_ORDER_ID", result["issues"])

    def test_duplicate_lock_id_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executions, locks = self._paths(temp_dir)
            data = default_order_locks_data()
            data["locks"].append({"lock_id": "LOCK_1"})
            _write_json(locks, data)

            result = commit_execution_runtime_plan(
                self._commit_orchestrator(),
                executions,
                locks,
                context=self._context(),
            )

            self.assertEqual("BLOCKED", result["status"])
            self.assertIn("DUPLICATE_LOCK_ID", result["issues"])

    def test_backup_created(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executions, locks = self._paths(temp_dir)

            result = commit_execution_runtime_plan(
                self._commit_orchestrator(),
                executions,
                locks,
                context=self._context(),
            )

            self.assertEqual("COMMITTED", result["status"])
            self.assertTrue(Path(result["backup_paths"]["order_executions"]).exists())
            self.assertTrue(Path(result["backup_paths"]["order_locks"]).exists())

    def test_temp_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executions, locks = self._paths(temp_dir)

            result = commit_execution_runtime_plan(
                self._commit_orchestrator(),
                executions,
                locks,
                context=self._context(),
            )

            self.assertEqual("COMMITTED", result["status"])
            leftovers = [item.name for item in Path(temp_dir).iterdir() if item.name.startswith(".")]
            self.assertEqual([], leftovers)

    def test_read_back_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executions, locks = self._paths(temp_dir)

            result = commit_execution_runtime_plan(
                self._commit_orchestrator(),
                executions,
                locks,
                context=self._context(),
            )

            self.assertTrue(result["read_back_verified"])

    def test_second_write_failure_rolls_back_from_backup(self) -> None:
        import execution_runtime_commit_service

        with tempfile.TemporaryDirectory() as temp_dir:
            executions, locks = self._paths(temp_dir)
            original_executions = executions.read_text(encoding="utf-8")
            original_locks = locks.read_text(encoding="utf-8")
            original_write = execution_runtime_commit_service._write_json_atomic
            call_count = {"value": 0}

            def failing_second_write(path, data):
                call_count["value"] += 1
                if call_count["value"] == 2:
                    raise OSError("forced second write failure")
                return original_write(path, data)

            with mock.patch("execution_runtime_commit_service._write_json_atomic", side_effect=failing_second_write):
                result = commit_execution_runtime_plan(
                    self._commit_orchestrator(),
                    executions,
                    locks,
                    context=self._context(),
                )

            self.assertEqual("ERROR", result["status"])
            self.assertTrue(result["rollback_attempted"])
            self.assertTrue(result["rollback_succeeded"])
            self.assertEqual(original_executions, executions.read_text(encoding="utf-8"))
            self.assertEqual(original_locks, locks.read_text(encoding="utf-8"))

    def test_no_mkdir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executions, locks = self._paths(temp_dir)
            with mock.patch("pathlib.Path.mkdir") as mkdir:
                result = commit_execution_runtime_plan(
                    self._commit_orchestrator(),
                    executions,
                    locks,
                    context=self._context(),
                )

            self.assertEqual("COMMITTED", result["status"])
            mkdir.assert_not_called()

    def test_input_immutability(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            executions, locks = self._paths(temp_dir)
            plan = self._commit_orchestrator()
            before = deepcopy(plan)

            result = commit_execution_runtime_plan(plan, executions, locks, context=self._context())
            result["backup_paths"]["order_executions"] = "MUTATED_RESULT_ONLY"

            self.assertEqual(before, plan)

    def test_actual_runtime_and_rules_hash_unchanged(self) -> None:
        runtime_queue = ROOT / "runtime" / "order_queue.json"
        rules_paths = list((ROOT / "routines").glob("**/rules.json"))
        before_runtime = _sha256(runtime_queue)
        before_rules = {str(path): _sha256(path) for path in rules_paths}

        with tempfile.TemporaryDirectory() as temp_dir:
            executions, locks = self._paths(temp_dir)
            commit_execution_runtime_plan(
                self._commit_orchestrator(),
                executions,
                locks,
                context=self._context(),
            )

        self.assertEqual(before_runtime, _sha256(runtime_queue))
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})


if __name__ == "__main__":
    unittest.main()
