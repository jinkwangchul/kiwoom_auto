from __future__ import annotations

from copy import deepcopy
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from execution_runtime_file_init_approval_gate import approve_execution_runtime_file_init
from execution_runtime_file_init_commit_plan_orchestrator import (
    run_execution_runtime_file_init_commit_plan_orchestrator,
)
from execution_runtime_file_init_commit_service import (
    commit_execution_runtime_file_init_plan,
)
from execution_runtime_file_init_open_policy import evaluate_execution_runtime_file_init_open_policy
from execution_runtime_file_init_preview import build_execution_runtime_file_init_preview


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionRuntimeFileInitCommitServiceContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.tmp.name)
        self.order_executions_path = self.temp_root / "order_executions.json"
        self.order_locks_path = self.temp_root / "order_locks.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _orchestrator(self, order_executions_path=None, order_locks_path=None) -> dict:
        preview = build_execution_runtime_file_init_preview(
            self.order_executions_path if order_executions_path is None else order_executions_path,
            self.order_locks_path if order_locks_path is None else order_locks_path,
        )
        approval = approve_execution_runtime_file_init(
            preview,
            manual_runtime_file_init_confirmed=True,
        )
        return run_execution_runtime_file_init_commit_plan_orchestrator(preview, approval)

    def _commit(self, orchestrator: dict, *, confirmed: bool = True) -> dict:
        return commit_execution_runtime_file_init_plan(
            orchestrator,
            manual_runtime_file_init_commit_confirmed=confirmed,
            manual_temp_file_init_confirmed=confirmed,
        )

    def _project_orchestrator(self) -> dict:
        preview = build_execution_runtime_file_init_preview(
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
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

    def _valid_orchestrator_with_target(self, *, order_executions_path: Path | None = None, order_locks_path: Path | None = None) -> dict:
        orchestrator = deepcopy(self._orchestrator())
        targets = orchestrator["commit_plan"]["planned_targets"]
        if order_executions_path is not None:
            targets["order_executions"] = str(order_executions_path)
        if order_locks_path is not None:
            targets["order_locks"] = str(order_locks_path)
        return orchestrator

    def test_temp_path_valid_plan_confirmations_committed(self) -> None:
        result = self._commit(self._orchestrator())

        self.assertEqual("COMMITTED", result["status"])
        self.assertTrue(result["committed"])
        self.assertTrue(result["runtime_write"])
        self.assertEqual(
            [str(self.order_executions_path), str(self.order_locks_path)],
            result["created_files"],
        )
        self.assertTrue(result["read_back_verified"])
        self.assertTrue(self.order_executions_path.exists())
        self.assertTrue(self.order_locks_path.exists())

    def test_actual_project_runtime_order_executions_path_blocked(self) -> None:
        orchestrator = self._valid_orchestrator_with_target(
            order_executions_path=ROOT / "runtime" / "order_executions.json",
        )

        result = self._commit(orchestrator)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("PROJECT_RUNTIME_PATH_BLOCKED", result["issues"])
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())

    def test_actual_project_runtime_order_locks_path_blocked(self) -> None:
        orchestrator = self._valid_orchestrator_with_target(
            order_locks_path=ROOT / "runtime" / "order_locks.json",
        )

        result = self._commit(orchestrator)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("PROJECT_RUNTIME_PATH_BLOCKED", result["issues"])
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())

    def test_project_runtime_open_policy_ready_with_confirmation_committed(self) -> None:
        order_executions_path = ROOT / "runtime" / "order_executions.json"
        order_locks_path = ROOT / "runtime" / "order_locks.json"
        self.assertFalse(order_executions_path.exists())
        self.assertFalse(order_locks_path.exists())
        orchestrator = self._project_orchestrator()

        try:
            result = commit_execution_runtime_file_init_plan(
                orchestrator,
                manual_runtime_file_init_commit_confirmed=True,
                file_init_open_policy_result=self._open_policy(orchestrator),
                manual_project_runtime_file_init_commit_confirmed=True,
            )

            self.assertEqual("COMMITTED", result["status"])
            self.assertTrue(result["committed"])
            self.assertTrue(result["runtime_write"])
            self.assertEqual([str(order_executions_path), str(order_locks_path)], result["created_files"])
            self.assertTrue(result["read_back_verified"])
        finally:
            for path in (order_executions_path, order_locks_path):
                if path.exists():
                    path.unlink()

    def test_project_runtime_open_policy_ready_without_project_confirmation_blocked(self) -> None:
        orchestrator = self._project_orchestrator()

        result = commit_execution_runtime_file_init_plan(
            orchestrator,
            manual_runtime_file_init_commit_confirmed=True,
            file_init_open_policy_result=self._open_policy(orchestrator),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("MANUAL_PROJECT_RUNTIME_FILE_INIT_COMMIT_CONFIRMATION_REQUIRED", result["issues"])
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())

    def test_confirmations_missing_blocked(self) -> None:
        result = self._commit(self._orchestrator(), confirmed=False)

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["committed"])
        self.assertFalse(result["runtime_write"])
        self.assertEqual([], result["created_files"])

    def test_parent_missing_blocked(self) -> None:
        missing_root = self.temp_root / "missing"
        orchestrator = self._valid_orchestrator_with_target(
            order_executions_path=missing_root / "order_executions.json",
            order_locks_path=missing_root / "order_locks.json",
        )

        result = self._commit(orchestrator)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("PARENT_DIRECTORY_MISSING", result["issues"])
        self.assertFalse(missing_root.exists())

    def test_target_already_exists_blocked(self) -> None:
        orchestrator = self._orchestrator()
        self.order_executions_path.write_text("{}", encoding="utf-8")

        result = self._commit(orchestrator)

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("ORDER_EXECUTIONS_FILE_ALREADY_EXISTS", result["issues"])
        self.assertFalse(result["committed"])

    def test_malformed_plan_invalid(self) -> None:
        result = self._commit({"bad": True})

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["committed"])
        self.assertFalse(result["runtime_write"])
        self.assertEqual([], result["created_files"])

    def test_read_back_verification_and_temp_cleanup(self) -> None:
        result = self._commit(self._orchestrator())
        leftovers = [item.name for item in self.temp_root.iterdir() if item.name.startswith(".")]

        self.assertEqual("COMMITTED", result["status"])
        self.assertTrue(result["read_back_verified"])
        self.assertEqual([], leftovers)

    def test_mkdir_not_called(self) -> None:
        with mock.patch("pathlib.Path.mkdir") as mkdir:
            result = self._commit(self._orchestrator())

        self.assertEqual("COMMITTED", result["status"])
        mkdir.assert_not_called()

    def test_created_files_runtime_write_committed_only_when_committed(self) -> None:
        committed = self._commit(self._orchestrator())
        blocked = self._commit(self._orchestrator(), confirmed=False)
        invalid = self._commit({"bad": True})

        self.assertEqual("COMMITTED", committed["status"])
        self.assertTrue(committed["created_files"])
        self.assertTrue(committed["runtime_write"])
        self.assertTrue(committed["committed"])

        for result in (blocked, invalid):
            self.assertNotEqual("COMMITTED", result["status"])
            self.assertEqual([], result["created_files"])
            self.assertFalse(result["runtime_write"])
            self.assertFalse(result["committed"])

    def test_no_queue_sendorder_execution_controller_gui_calls(self) -> None:
        with mock.patch("os.replace", wraps=os.replace) as replace:
            result = self._commit(self._orchestrator())

        self.assertEqual("COMMITTED", result["status"])
        self.assertEqual(2, replace.call_count)
        self.assertNotIn("queue_commit_called", result)
        self.assertNotIn("send_order_called", result)
        self.assertNotIn("execution_controller_called", result)
        self.assertNotIn("gui_connected", result)

    def test_actual_runtime_files_and_rules_unchanged(self) -> None:
        runtime_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        rules_paths = list((ROOT / "routines").glob("**/rules.json"))
        before_runtime = {str(path): _sha256(path) for path in runtime_paths}
        before_rules = {str(path): _sha256(path) for path in rules_paths}

        self._commit(self._orchestrator())
        self._commit(
            self._valid_orchestrator_with_target(
                order_executions_path=ROOT / "runtime" / "order_executions.json",
            )
        )
        self._commit(
            self._valid_orchestrator_with_target(
                order_locks_path=ROOT / "runtime" / "order_locks.json",
            )
        )

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
