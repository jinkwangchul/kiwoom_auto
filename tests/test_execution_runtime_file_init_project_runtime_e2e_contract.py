from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest
from unittest import mock

from execution_runtime_file_init_approval_gate import approve_execution_runtime_file_init
from execution_runtime_file_init_commit_plan_orchestrator import (
    run_execution_runtime_file_init_commit_plan_orchestrator,
)
from execution_runtime_file_init_commit_service import commit_execution_runtime_file_init_plan
from execution_runtime_file_init_open_policy import evaluate_execution_runtime_file_init_open_policy
from execution_runtime_file_init_preview import build_execution_runtime_file_init_preview
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


class ExecutionRuntimeFileInitProjectRuntimeE2EContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.order_executions_path = ROOT / "runtime" / "order_executions.json"
        self.order_locks_path = ROOT / "runtime" / "order_locks.json"
        self.order_queue_path = ROOT / "runtime" / "order_queue.json"
        self.created_paths: list[Path] = []

    def tearDown(self) -> None:
        for path in self.created_paths:
            if path.exists():
                path.unlink()

    def _project_orchestrator(self) -> dict:
        preview = build_execution_runtime_file_init_preview(
            self.order_executions_path,
            self.order_locks_path,
            allow_project_runtime_path=True,
        )
        approval = approve_execution_runtime_file_init(
            preview,
            manual_runtime_file_init_confirmed=True,
            manual_project_runtime_path_confirmed=True,
        )
        return run_execution_runtime_file_init_commit_plan_orchestrator(preview, approval)

    def _open_policy(self, orchestrator: dict) -> dict:
        return evaluate_execution_runtime_file_init_open_policy(
            file_init_commit_plan_orchestrator_result=orchestrator,
            confirmations={
                "manual_runtime_file_init_commit_confirmed": True,
                "manual_project_runtime_path_confirmed": True,
            },
            environment_flags={
                "real_runtime_file_init_enabled": True,
                "allow_project_runtime_file_init": True,
            },
        )

    def _commit_project_runtime(self, orchestrator: dict, *, policy: dict | None = None, project_confirmed: bool = True) -> dict:
        return commit_execution_runtime_file_init_plan(
            orchestrator,
            manual_runtime_file_init_commit_confirmed=True,
            file_init_open_policy_result=policy,
            manual_project_runtime_file_init_commit_confirmed=project_confirmed,
        )

    def test_default_project_runtime_path_blocked(self) -> None:
        result = commit_execution_runtime_file_init_plan(
            self._project_orchestrator(),
            manual_runtime_file_init_commit_confirmed=True,
            manual_temp_file_init_confirmed=True,
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("PROJECT_RUNTIME_PATH_BLOCKED", result["issues"])
        self.assertFalse(self.order_executions_path.exists())
        self.assertFalse(self.order_locks_path.exists())

    def test_open_policy_missing_blocked(self) -> None:
        result = self._commit_project_runtime(self._project_orchestrator(), policy=None)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("PROJECT_RUNTIME_PATH_BLOCKED", result["issues"])
        self.assertFalse(self.order_executions_path.exists())
        self.assertFalse(self.order_locks_path.exists())

    def test_open_policy_ready_without_project_final_confirmation_blocked(self) -> None:
        orchestrator = self._project_orchestrator()
        result = self._commit_project_runtime(
            orchestrator,
            policy=self._open_policy(orchestrator),
            project_confirmed=False,
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("MANUAL_PROJECT_RUNTIME_FILE_INIT_COMMIT_CONFIRMATION_REQUIRED", result["issues"])
        self.assertFalse(self.order_executions_path.exists())
        self.assertFalse(self.order_locks_path.exists())

    def test_open_policy_ready_all_confirmations_committed_schema_and_cleanup(self) -> None:
        if self.order_executions_path.exists() or self.order_locks_path.exists():
            result = self._commit_project_runtime(
                self._project_orchestrator(),
                policy=self._open_policy(self._project_orchestrator()),
            )
            self.assertEqual("BLOCKED", result["status"])
            return

        before_order_queue = _sha256(self.order_queue_path)
        before_rules = {str(path): _sha256(path) for path in (ROOT / "routines").glob("**/rules.json")}
        orchestrator = self._project_orchestrator()

        try:
            result = self._commit_project_runtime(orchestrator, policy=self._open_policy(orchestrator))
            self.created_paths = [self.order_executions_path, self.order_locks_path]

            self.assertEqual("COMMITTED", result["status"])
            self.assertTrue(result["committed"])
            self.assertTrue(result["runtime_write"])
            self.assertTrue(result["read_back_verified"])
            self.assertEqual(
                [str(self.order_executions_path), str(self.order_locks_path)],
                result["created_files"],
            )
            self.assertEqual(default_order_executions_data(), read_order_executions(self.order_executions_path)["data"])
            self.assertEqual(default_order_locks_data(), read_order_locks(self.order_locks_path)["data"])
            self.assertEqual(before_order_queue, _sha256(self.order_queue_path))
            self.assertEqual(before_rules, {str(path): _sha256(path) for path in (ROOT / "routines").glob("**/rules.json")})
        finally:
            for path in self.created_paths:
                if path.exists():
                    path.unlink()

        self.assertFalse(self.order_executions_path.exists())
        self.assertFalse(self.order_locks_path.exists())

    def test_valid_existing_file_creates_only_missing_project_runtime_file(self) -> None:
        if self.order_executions_path.exists() or self.order_locks_path.exists():
            result = self._commit_project_runtime(
                self._project_orchestrator(),
                policy=self._open_policy(self._project_orchestrator()),
            )
            self.assertEqual("BLOCKED", result["status"])
            return

        orchestrator = self._project_orchestrator()
        policy = self._open_policy(orchestrator)
        existing = default_order_executions_data()
        existing["updated_at"] = "existing"
        self.order_executions_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
        self.created_paths = [self.order_executions_path, self.order_locks_path]

        try:
            result = self._commit_project_runtime(orchestrator, policy=policy)

            self.assertEqual("COMMITTED", result["status"])
            self.assertTrue(result["committed"])
            self.assertEqual([str(self.order_locks_path)], result["created_files"])
            self.assertEqual(existing, read_order_executions(self.order_executions_path)["data"])
            self.assertTrue(read_order_locks(self.order_locks_path)["ok"])
        finally:
            for path in self.created_paths:
                if path.exists():
                    path.unlink()

    def test_no_queue_sendorder_execution_controller_or_gui_calls(self) -> None:
        if self.order_executions_path.exists() or self.order_locks_path.exists():
            return

        orchestrator = self._project_orchestrator()
        with (
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
        ):
            try:
                result = self._commit_project_runtime(orchestrator, policy=self._open_policy(orchestrator))
                self.created_paths = [self.order_executions_path, self.order_locks_path]
            finally:
                for path in self.created_paths:
                    if path.exists():
                        path.unlink()

        self.assertEqual("COMMITTED", result["status"])
        queue_commit.assert_not_called()
        send_order.assert_not_called()
        self.assertNotIn("execution_controller_called", result)
        self.assertNotIn("gui_connected", result)


if __name__ == "__main__":
    unittest.main()
