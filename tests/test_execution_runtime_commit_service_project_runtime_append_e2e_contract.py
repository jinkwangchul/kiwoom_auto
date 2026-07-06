from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest import mock

from execution_runtime_commit_service import commit_execution_runtime_plan
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


class ExecutionRuntimeCommitServiceProjectRuntimeAppendE2EContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.order_executions_path = ROOT / "runtime" / "order_executions.json"
        self.order_locks_path = ROOT / "runtime" / "order_locks.json"
        self.order_queue_path = ROOT / "runtime" / "order_queue.json"
        self.created_paths: list[Path] = []
        self.preexisting_executions = self.order_executions_path.exists()
        self.preexisting_locks = self.order_locks_path.exists()

    def tearDown(self) -> None:
        for path in self.created_paths:
            if path.exists():
                path.unlink()

    def _commit_orchestrator(self) -> dict:
        commit_plan = {
            "plan_type": "EXECUTION_RUNTIME_COMMIT_PLAN_PREVIEW",
            "status": "READY",
            "commit_ready": True,
            "preview_only": True,
            "runtime_write": False,
            "planned_targets": {
                "order_executions": str(self.order_executions_path),
                "order_locks": str(self.order_locks_path),
            },
            "planned_records": {
                "execution": {
                    "execution_id": "EXEC_PROJECT_E2E_1",
                    "order_id": "ORDER_PROJECT_E2E_1",
                    "request_hash": "HASH_PROJECT_E2E_1",
                    "lock_id": "LOCK_PROJECT_E2E_1",
                    "status": "RUNTIME_WRITE_PREVIEW",
                    "preview_only": True,
                    "runtime_write": False,
                },
                "lock": {
                    "lock_id": "LOCK_PROJECT_E2E_1",
                    "order_id": "ORDER_PROJECT_E2E_1",
                    "request_hash": "HASH_PROJECT_E2E_1",
                    "execution_id": "EXEC_PROJECT_E2E_1",
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

    def _real_commit_policy(self, orchestrator: dict) -> dict:
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
            order_executions_path=self.order_executions_path,
            order_locks_path=self.order_locks_path,
            confirmations=self._context(),
            environment_flags={
                "real_runtime_commit_enabled": True,
                "allow_project_runtime_commit": True,
            },
        )

    def _create_runtime_files(self, executions_data=None, locks_data=None) -> bool:
        if self.preexisting_executions or self.preexisting_locks:
            return False
        _write_json(self.order_executions_path, executions_data or default_order_executions_data())
        _write_json(self.order_locks_path, locks_data or default_order_locks_data())
        self.created_paths.extend(
            [
                self.order_executions_path,
                self.order_locks_path,
                Path(str(self.order_executions_path) + ".bak"),
                Path(str(self.order_locks_path) + ".bak"),
            ]
        )
        return True

    def _commit(self, *, policy=None, project_confirmed: bool = True) -> dict:
        return commit_execution_runtime_plan(
            self._commit_orchestrator(),
            self.order_executions_path,
            self.order_locks_path,
            context=self._context(),
            real_commit_readiness_policy_result=policy,
            manual_project_runtime_commit_confirmed=project_confirmed,
        )

    def test_default_project_runtime_append_blocked(self) -> None:
        result = commit_execution_runtime_plan(
            self._commit_orchestrator(),
            self.order_executions_path,
            self.order_locks_path,
            context=self._context(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("PROJECT_RUNTIME_PATH_BLOCKED", result["issues"])

    def test_policy_missing_blocked(self) -> None:
        result = self._commit(policy=None)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("PROJECT_RUNTIME_PATH_BLOCKED", result["issues"])

    def test_policy_ready_without_final_confirmation_blocked(self) -> None:
        orchestrator = self._commit_orchestrator()
        result = self._commit(policy=self._real_commit_policy(orchestrator), project_confirmed=False)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("MANUAL_PROJECT_RUNTIME_COMMIT_CONFIRMATION_REQUIRED", result["issues"])

    def test_policy_ready_all_confirmations_existing_runtime_files_committed(self) -> None:
        if not self._create_runtime_files():
            result = commit_execution_runtime_plan(
                self._commit_orchestrator(),
                self.order_executions_path,
                self.order_locks_path,
                context=self._context(),
            )
            self.assertEqual("BLOCKED", result["status"])
            return

        before_order_queue = _sha256(self.order_queue_path)
        before_rules = {str(path): _sha256(path) for path in (ROOT / "routines").glob("**/rules.json")}
        orchestrator = self._commit_orchestrator()
        result = self._commit(policy=self._real_commit_policy(orchestrator))

        self.assertEqual("COMMITTED", result["status"])
        self.assertTrue(result["committed"])
        self.assertTrue(result["runtime_write"])
        self.assertTrue(result["read_back_verified"])
        self.assertTrue(Path(result["backup_paths"]["order_executions"]).exists())
        self.assertTrue(Path(result["backup_paths"]["order_locks"]).exists())

        executions_data = json.loads(self.order_executions_path.read_text(encoding="utf-8"))
        locks_data = json.loads(self.order_locks_path.read_text(encoding="utf-8"))
        self.assertEqual("EXEC_PROJECT_E2E_1", executions_data["executions"][0]["execution_id"])
        self.assertEqual("LOCK_PROJECT_E2E_1", locks_data["locks"][0]["lock_id"])
        self.assertEqual(before_order_queue, _sha256(self.order_queue_path))
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in (ROOT / "routines").glob("**/rules.json")})

    def test_duplicate_execution_id_request_hash_order_id_lock_id_blocked(self) -> None:
        duplicate_cases = [
            ("executions", "execution_id", "EXEC_PROJECT_E2E_1", "DUPLICATE_EXECUTION_ID"),
            ("executions", "request_hash", "HASH_PROJECT_E2E_1", "DUPLICATE_REQUEST_HASH"),
            ("executions", "order_id", "ORDER_PROJECT_E2E_1", "DUPLICATE_ORDER_ID"),
            ("locks", "lock_id", "LOCK_PROJECT_E2E_1", "DUPLICATE_LOCK_ID"),
        ]

        for target, field, value, expected_issue in duplicate_cases:
            with self.subTest(field=field):
                if self.order_executions_path.exists() or self.order_locks_path.exists():
                    continue
                executions_data = default_order_executions_data()
                locks_data = default_order_locks_data()
                if target == "executions":
                    executions_data["executions"].append({field: value})
                else:
                    locks_data["locks"].append({field: value})
                self._create_runtime_files(executions_data, locks_data)

                result = self._commit(policy=self._real_commit_policy(self._commit_orchestrator()))

                self.assertEqual("BLOCKED", result["status"])
                self.assertIn(expected_issue, result["issues"])
                self.tearDown()
                self.created_paths = []

    def test_second_write_failure_rolls_back_project_runtime_files(self) -> None:
        import execution_runtime_commit_service

        if not self._create_runtime_files():
            result = commit_execution_runtime_plan(
                self._commit_orchestrator(),
                self.order_executions_path,
                self.order_locks_path,
                context=self._context(),
            )
            self.assertEqual("BLOCKED", result["status"])
            return

        original_executions = self.order_executions_path.read_text(encoding="utf-8")
        original_locks = self.order_locks_path.read_text(encoding="utf-8")
        original_write = execution_runtime_commit_service._write_json_atomic
        call_count = {"value": 0}

        def failing_second_write(path, data):
            call_count["value"] += 1
            if call_count["value"] == 2:
                raise OSError("forced project runtime second write failure")
            return original_write(path, data)

        with mock.patch("execution_runtime_commit_service._write_json_atomic", side_effect=failing_second_write):
            result = self._commit(policy=self._real_commit_policy(self._commit_orchestrator()))

        self.assertEqual("ERROR", result["status"])
        self.assertTrue(result["rollback_attempted"])
        self.assertTrue(result["rollback_succeeded"])
        self.assertEqual(original_executions, self.order_executions_path.read_text(encoding="utf-8"))
        self.assertEqual(original_locks, self.order_locks_path.read_text(encoding="utf-8"))

    def test_no_queue_sendorder_execution_controller_or_gui_calls(self) -> None:
        if not self._create_runtime_files():
            return
        orchestrator = self._commit_orchestrator()
        with (
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
        ):
            result = self._commit(policy=self._real_commit_policy(orchestrator))

        self.assertEqual("COMMITTED", result["status"])
        queue_commit.assert_not_called()
        send_order.assert_not_called()
        self.assertNotIn("execution_controller_called", result)
        self.assertNotIn("gui_connected", result)


if __name__ == "__main__":
    unittest.main()
