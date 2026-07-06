from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from execution_runtime_file_init_approval_gate import approve_execution_runtime_file_init
from execution_runtime_file_init_commit_plan_orchestrator import (
    run_execution_runtime_file_init_commit_plan_orchestrator,
)
from execution_runtime_file_init_open_policy import (
    POLICY_TYPE,
    STATUS_READY,
    evaluate_execution_runtime_file_init_open_policy,
)
from execution_runtime_file_init_preview import build_execution_runtime_file_init_preview


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionRuntimeFileInitOpenPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.tmp.name)
        self.order_executions_path = ROOT / "runtime" / "order_executions.json"
        self.order_locks_path = ROOT / "runtime" / "order_locks.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _orchestrator(
        self,
        *,
        status_case: str = "READY",
        init_commit_ready: bool | None = None,
    ) -> dict:
        if status_case == "READY":
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
        elif status_case == "BLOCKED":
            partial = self.temp_root / "order_executions.json"
            partial.write_text("{}", encoding="utf-8")
            preview = build_execution_runtime_file_init_preview(
                partial,
                self.temp_root / "order_locks.json",
            )
            approval = approve_execution_runtime_file_init(
                preview,
                manual_runtime_file_init_confirmed=True,
            )
        elif status_case == "INVALID":
            preview = build_execution_runtime_file_init_preview("", self.order_locks_path)
            approval = approve_execution_runtime_file_init(
                preview,
                manual_runtime_file_init_confirmed=True,
            )
        elif status_case == "SKIPPED":
            existing_executions = self.temp_root / "order_executions.json"
            existing_locks = self.temp_root / "order_locks.json"
            existing_executions.write_text("{}", encoding="utf-8")
            existing_locks.write_text("{}", encoding="utf-8")
            preview = build_execution_runtime_file_init_preview(existing_executions, existing_locks)
            approval = approve_execution_runtime_file_init(
                preview,
                manual_runtime_file_init_confirmed=True,
            )
        else:
            raise ValueError(status_case)

        orchestrator = run_execution_runtime_file_init_commit_plan_orchestrator(preview, approval)
        if init_commit_ready is not None:
            orchestrator["init_commit_ready"] = init_commit_ready
        return orchestrator

    def _confirmations(self, *, runtime_confirmed: bool = True, project_confirmed: bool = True) -> dict:
        return {
            "manual_runtime_file_init_commit_confirmed": runtime_confirmed,
            "manual_project_runtime_path_confirmed": project_confirmed,
        }

    def _environment(self, *, enabled: bool = True, allow: bool = True) -> dict:
        return {
            "real_runtime_file_init_enabled": enabled,
            "allow_project_runtime_file_init": allow,
        }

    def _evaluate(self, **overrides) -> dict:
        kwargs = {
            "file_init_commit_plan_orchestrator_result": self._orchestrator(),
            "confirmations": self._confirmations(),
            "environment_flags": self._environment(),
        }
        kwargs.update(overrides)
        return evaluate_execution_runtime_file_init_open_policy(**kwargs)

    def test_all_conditions_ready_to_open_file_init(self) -> None:
        result = self._evaluate()

        self.assertEqual(POLICY_TYPE, result["policy_type"])
        self.assertEqual(STATUS_READY, result["status"])
        self.assertTrue(result["file_init_allowed"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertTrue(result["required_confirmations"]["manual_runtime_file_init_commit_confirmed"])
        self.assertTrue(result["required_confirmations"]["manual_project_runtime_path_confirmed"])
        self.assertTrue(result["environment_checks"]["real_runtime_file_init_enabled"])
        self.assertTrue(result["environment_checks"]["allow_project_runtime_file_init"])

    def test_orchestrator_blocked_stays_blocked(self) -> None:
        result = self._evaluate(file_init_commit_plan_orchestrator_result=self._orchestrator(status_case="BLOCKED"))

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["file_init_allowed"])

    def test_orchestrator_invalid_stays_invalid(self) -> None:
        result = self._evaluate(file_init_commit_plan_orchestrator_result=self._orchestrator(status_case="INVALID"))

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["file_init_allowed"])

    def test_orchestrator_skipped_stays_skipped(self) -> None:
        result = self._evaluate(file_init_commit_plan_orchestrator_result=self._orchestrator(status_case="SKIPPED"))

        self.assertEqual("SKIPPED", result["status"])
        self.assertFalse(result["file_init_allowed"])

    def test_init_commit_ready_false_blocks(self) -> None:
        result = self._evaluate(
            file_init_commit_plan_orchestrator_result=self._orchestrator(init_commit_ready=False)
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["file_init_allowed"])
        self.assertIn("INIT_COMMIT_READY_IS_NOT_TRUE", result["issues"])

    def test_manual_confirmation_missing_blocks(self) -> None:
        result = self._evaluate(confirmations=self._confirmations(runtime_confirmed=False))

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["file_init_allowed"])
        self.assertIn("MANUAL_RUNTIME_FILE_INIT_COMMIT_CONFIRMATION_REQUIRED", result["issues"])

    def test_project_runtime_confirmation_missing_blocks(self) -> None:
        result = self._evaluate(confirmations=self._confirmations(project_confirmed=False))

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["file_init_allowed"])
        self.assertIn("MANUAL_PROJECT_RUNTIME_PATH_CONFIRMATION_REQUIRED", result["issues"])

    def test_environment_flag_missing_blocks(self) -> None:
        result = self._evaluate(environment_flags=self._environment(enabled=False))

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["file_init_allowed"])
        self.assertIn("REAL_RUNTIME_FILE_INIT_DISABLED", result["issues"])

    def test_project_runtime_allow_flag_missing_blocks(self) -> None:
        result = self._evaluate(environment_flags=self._environment(allow=False))

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["file_init_allowed"])
        self.assertIn("PROJECT_RUNTIME_FILE_INIT_NOT_ALLOWED", result["issues"])

    def test_malformed_input_invalid(self) -> None:
        result = self._evaluate(file_init_commit_plan_orchestrator_result=None)

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["file_init_allowed"])
        self.assertIn("MALFORMED_FILE_INIT_COMMIT_PLAN_ORCHESTRATOR_RESULT", result["issues"])

    def test_file_init_allowed_only_when_ready(self) -> None:
        ready = self._evaluate()
        blocked = self._evaluate(confirmations=self._confirmations(runtime_confirmed=False))
        invalid = self._evaluate(file_init_commit_plan_orchestrator_result=None)
        skipped = self._evaluate(file_init_commit_plan_orchestrator_result=self._orchestrator(status_case="SKIPPED"))

        self.assertTrue(ready["file_init_allowed"])
        for result in (blocked, invalid, skipped):
            self.assertFalse(result["file_init_allowed"])
            self.assertFalse(result["runtime_write"])
            self.assertTrue(result["preview_only"])

    def test_no_file_write_mkdir_commit_queue_send_order(self) -> None:
        with (
            mock.patch("pathlib.Path.mkdir") as mkdir,
            mock.patch("execution_runtime_file_init_commit_service.commit_execution_runtime_file_init_plan") as init_commit,
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
        ):
            result = self._evaluate()

        self.assertEqual(STATUS_READY, result["status"])
        mkdir.assert_not_called()
        init_commit.assert_not_called()
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
        self._evaluate(confirmations=self._confirmations(runtime_confirmed=False))
        self._evaluate(file_init_commit_plan_orchestrator_result=None)

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
