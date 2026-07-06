from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from execution_runtime_real_commit_readiness_policy import (
    POLICY_TYPE,
    STATUS_READY,
    evaluate_execution_runtime_real_commit_readiness,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionRuntimeRealCommitReadinessPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.tmp.name)
        self.order_executions_path = self.temp_root / "order_executions.json"
        self.order_locks_path = self.temp_root / "order_locks.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _runtime_api_result(self, status: str = "READY") -> dict:
        return {
            "api_type": "EXECUTION_RUNTIME_API",
            "status": status,
            "dry_run": True,
            "preview_only": True,
            "runtime_write": False,
            "issues": [],
            "warnings": [],
        }

    def _commit_plan(self, status: str = "READY", commit_ready: bool = True) -> dict:
        return {
            "orchestrator_type": "EXECUTION_RUNTIME_COMMIT_PLAN_ORCHESTRATOR",
            "status": status,
            "commit_ready": commit_ready,
            "preview_only": True,
            "runtime_write": False,
            "issues": [],
            "warnings": [],
        }

    def _confirmations(self) -> dict:
        return {
            "manual_execution_runtime_commit_confirmed": True,
            "manual_runtime_file_write_confirmed": True,
        }

    def _environment(self, *, allow_project_runtime_commit: bool = True) -> dict:
        return {
            "real_runtime_commit_enabled": True,
            "allow_project_runtime_commit": allow_project_runtime_commit,
        }

    def _evaluate(self, **overrides) -> dict:
        kwargs = {
            "runtime_api_result": self._runtime_api_result(),
            "commit_plan_orchestrator_result": self._commit_plan(),
            "order_executions_path": ROOT / "runtime" / "order_executions.json",
            "order_locks_path": ROOT / "runtime" / "order_locks.json",
            "confirmations": self._confirmations(),
            "environment_flags": self._environment(),
        }
        kwargs.update(overrides)
        return evaluate_execution_runtime_real_commit_readiness(**kwargs)

    def test_ready_to_open_runtime_commit(self) -> None:
        result = self._evaluate()

        self.assertEqual(POLICY_TYPE, result["policy_type"])
        self.assertEqual(STATUS_READY, result["status"])
        self.assertTrue(result["runtime_commit_allowed"])
        self.assertFalse(result["runtime_write"])
        self.assertTrue(result["preview_only"])
        self.assertTrue(result["environment_checks"]["allow_project_runtime_commit"])

    def test_runtime_api_result_not_ready_blocks(self) -> None:
        result = self._evaluate(runtime_api_result=self._runtime_api_result("BLOCKED"))

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["runtime_commit_allowed"])
        self.assertIn("RUNTIME_API_RESULT_NOT_READY", result["issues"])

    def test_commit_plan_not_ready_blocks(self) -> None:
        result = self._evaluate(commit_plan_orchestrator_result=self._commit_plan("BLOCKED"))

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["runtime_commit_allowed"])
        self.assertIn("COMMIT_PLAN_ORCHESTRATOR_NOT_READY", result["issues"])

    def test_commit_ready_false_blocks(self) -> None:
        result = self._evaluate(commit_plan_orchestrator_result=self._commit_plan(commit_ready=False))

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["runtime_commit_allowed"])
        self.assertIn("COMMIT_READY_IS_NOT_TRUE", result["issues"])

    def test_project_runtime_path_without_allow_flag_blocks(self) -> None:
        result = self._evaluate(environment_flags=self._environment(allow_project_runtime_commit=False))

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["runtime_commit_allowed"])
        self.assertIn("PROJECT_RUNTIME_COMMIT_NOT_ALLOWED", result["issues"])

    def test_allow_flag_but_missing_manual_confirmation_blocks(self) -> None:
        confirmations = {
            "manual_execution_runtime_commit_confirmed": True,
            "manual_runtime_file_write_confirmed": False,
        }

        result = self._evaluate(confirmations=confirmations)

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["runtime_commit_allowed"])
        self.assertIn("MANUAL_RUNTIME_FILE_WRITE_CONFIRMATION_REQUIRED", result["issues"])

    def test_missing_execution_runtime_confirmation_blocks(self) -> None:
        confirmations = {
            "manual_execution_runtime_commit_confirmed": False,
            "manual_runtime_file_write_confirmed": True,
        }

        result = self._evaluate(confirmations=confirmations)

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["runtime_commit_allowed"])
        self.assertIn("MANUAL_EXECUTION_RUNTIME_COMMIT_CONFIRMATION_REQUIRED", result["issues"])

    def test_missing_environment_flag_blocks(self) -> None:
        result = self._evaluate(environment_flags={"allow_project_runtime_commit": True})

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["runtime_commit_allowed"])
        self.assertIn("REAL_RUNTIME_COMMIT_DISABLED", result["issues"])

    def test_malformed_input_invalid(self) -> None:
        result = self._evaluate(runtime_api_result=None)

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["runtime_commit_allowed"])
        self.assertIn("MALFORMED_RUNTIME_API_RESULT", result["issues"])

    def test_invalid_commit_plan_invalid(self) -> None:
        result = self._evaluate(commit_plan_orchestrator_result=self._commit_plan("INVALID"))

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["runtime_commit_allowed"])
        self.assertIn("COMMIT_PLAN_ORCHESTRATOR_INVALID", result["issues"])

    def test_missing_paths_invalid(self) -> None:
        result = self._evaluate(order_executions_path="")

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["runtime_commit_allowed"])
        self.assertIn("MISSING_ORDER_EXECUTIONS_PATH", result["issues"])

    def test_non_project_runtime_paths_can_be_evaluated_without_allow_flag(self) -> None:
        result = self._evaluate(
            order_executions_path=self.order_executions_path,
            order_locks_path=self.order_locks_path,
            environment_flags={
                "real_runtime_commit_enabled": True,
                "allow_project_runtime_commit": False,
            },
        )

        self.assertEqual(STATUS_READY, result["status"])
        self.assertTrue(result["runtime_commit_allowed"])
        self.assertFalse(result["environment_checks"]["order_executions_is_project_runtime"])
        self.assertFalse(result["environment_checks"]["order_locks_is_project_runtime"])

    def test_no_file_write_mkdir_queue_send_order_or_commit(self) -> None:
        with (
            mock.patch("pathlib.Path.mkdir") as mkdir,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
        ):
            result = self._evaluate()

        self.assertEqual(STATUS_READY, result["status"])
        mkdir.assert_not_called()
        runtime_commit.assert_not_called()
        queue_commit.assert_not_called()
        send_order.assert_not_called()
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())

    def test_runtime_and_rules_hash_unchanged(self) -> None:
        runtime_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        rules_paths = list((ROOT / "routines").glob("**/rules.json"))
        before_runtime = {str(path): _sha256(path) for path in runtime_paths}
        before_rules = {str(path): _sha256(path) for path in rules_paths}

        self._evaluate()
        self._evaluate(runtime_api_result=self._runtime_api_result("BLOCKED"))
        self._evaluate(commit_plan_orchestrator_result=self._commit_plan("BLOCKED"))

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
