from __future__ import annotations

import hashlib
from pathlib import Path
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


class ExecutionRuntimeRealCommitReadinessPolicyContractTest(unittest.TestCase):
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

    def _environment(self) -> dict:
        return {
            "allow_project_runtime_commit": True,
            "real_runtime_commit_enabled": True,
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

    def _assert_preview_contract(self, result: dict, *, allowed: bool) -> None:
        self.assertEqual(POLICY_TYPE, result["policy_type"])
        self.assertFalse(result["runtime_write"])
        self.assertTrue(result["preview_only"])
        self.assertIs(allowed, result["runtime_commit_allowed"])
        self.assertIs(result["status"] == STATUS_READY, result["runtime_commit_allowed"])

    def test_all_conditions_ready_to_open_runtime_commit(self) -> None:
        result = self._evaluate()

        self.assertEqual(STATUS_READY, result["status"])
        self._assert_preview_contract(result, allowed=True)

    def test_runtime_api_result_not_ready_blocks(self) -> None:
        result = self._evaluate(runtime_api_result=self._runtime_api_result("BLOCKED"))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("RUNTIME_API_RESULT_NOT_READY", result["issues"])
        self._assert_preview_contract(result, allowed=False)

    def test_commit_plan_not_ready_blocks(self) -> None:
        result = self._evaluate(commit_plan_orchestrator_result=self._commit_plan("BLOCKED"))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("COMMIT_PLAN_ORCHESTRATOR_NOT_READY", result["issues"])
        self._assert_preview_contract(result, allowed=False)

    def test_commit_ready_false_blocks(self) -> None:
        result = self._evaluate(commit_plan_orchestrator_result=self._commit_plan(commit_ready=False))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("COMMIT_READY_IS_NOT_TRUE", result["issues"])
        self._assert_preview_contract(result, allowed=False)

    def test_allow_project_runtime_commit_missing_blocks(self) -> None:
        result = self._evaluate(environment_flags={"real_runtime_commit_enabled": True})

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("PROJECT_RUNTIME_COMMIT_NOT_ALLOWED", result["issues"])
        self._assert_preview_contract(result, allowed=False)

    def test_manual_execution_runtime_commit_confirmation_missing_blocks(self) -> None:
        result = self._evaluate(
            confirmations={
                "manual_runtime_file_write_confirmed": True,
            }
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("MANUAL_EXECUTION_RUNTIME_COMMIT_CONFIRMATION_REQUIRED", result["issues"])
        self._assert_preview_contract(result, allowed=False)

    def test_manual_runtime_file_write_confirmation_missing_blocks(self) -> None:
        result = self._evaluate(
            confirmations={
                "manual_execution_runtime_commit_confirmed": True,
            }
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("MANUAL_RUNTIME_FILE_WRITE_CONFIRMATION_REQUIRED", result["issues"])
        self._assert_preview_contract(result, allowed=False)

    def test_real_runtime_commit_enabled_missing_blocks(self) -> None:
        result = self._evaluate(environment_flags={"allow_project_runtime_commit": True})

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("REAL_RUNTIME_COMMIT_DISABLED", result["issues"])
        self._assert_preview_contract(result, allowed=False)

    def test_malformed_runtime_api_result_invalid(self) -> None:
        result = self._evaluate(runtime_api_result=None)

        self.assertEqual("INVALID", result["status"])
        self.assertIn("MALFORMED_RUNTIME_API_RESULT", result["issues"])
        self._assert_preview_contract(result, allowed=False)

    def test_malformed_commit_plan_invalid(self) -> None:
        result = self._evaluate(commit_plan_orchestrator_result=None)

        self.assertEqual("INVALID", result["status"])
        self.assertIn("MALFORMED_COMMIT_PLAN_ORCHESTRATOR_RESULT", result["issues"])
        self._assert_preview_contract(result, allowed=False)

    def test_no_file_write_mkdir_commit_queue_or_send_order(self) -> None:
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

    def test_runtime_files_and_rules_unchanged(self) -> None:
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
        self._evaluate(runtime_api_result=None)

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
