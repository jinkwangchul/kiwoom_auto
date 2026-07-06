from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from execution_runtime_file_init_approval_gate import approve_execution_runtime_file_init
from execution_runtime_file_init_commit_plan_orchestrator import (
    run_execution_runtime_file_init_commit_plan_orchestrator,
)
from execution_runtime_file_init_preview import build_execution_runtime_file_init_preview
from execution_runtime_file_schema import (
    default_order_executions_data,
    default_order_locks_data,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionRuntimeFileInitCommitPlanContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.tmp.name)
        self.order_executions_path = self.temp_root / "order_executions.json"
        self.order_locks_path = self.temp_root / "order_locks.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run_flow(
        self,
        *,
        order_executions_path=None,
        order_locks_path=None,
        allow_project_runtime_path: bool = False,
        manual_runtime_file_init_confirmed: bool = True,
        manual_project_runtime_path_confirmed: bool = False,
    ) -> tuple[dict, dict, dict]:
        preview = build_execution_runtime_file_init_preview(
            self.order_executions_path if order_executions_path is None else order_executions_path,
            self.order_locks_path if order_locks_path is None else order_locks_path,
            allow_project_runtime_path=allow_project_runtime_path,
        )
        approval = approve_execution_runtime_file_init(
            preview,
            manual_runtime_file_init_confirmed=manual_runtime_file_init_confirmed,
            manual_project_runtime_path_confirmed=manual_project_runtime_path_confirmed,
        )
        result = run_execution_runtime_file_init_commit_plan_orchestrator(preview, approval)
        return preview, approval, result

    def test_missing_files_with_approval_confirmed_ready(self) -> None:
        preview, approval, result = self._run_flow()

        self.assertEqual("READY", result["status"])
        self.assertTrue(result["init_commit_ready"])
        self.assertEqual("READY", result["commit_plan"]["status"])
        self.assertTrue(result["validation"]["valid"])
        self.assertEqual(preview["targets"], result["commit_plan"]["planned_targets"])
        self.assertEqual(approval["required_confirmations"], result["commit_plan"]["required_confirmations"])

    def test_both_existing_skipped(self) -> None:
        self.order_executions_path.write_text("{}", encoding="utf-8")
        self.order_locks_path.write_text("{}", encoding="utf-8")

        _preview, _approval, result = self._run_flow()

        self.assertEqual("SKIPPED", result["status"])
        self.assertFalse(result["init_commit_ready"])

    def test_one_existing_blocked(self) -> None:
        self.order_executions_path.write_text("{}", encoding="utf-8")

        _preview, _approval, result = self._run_flow()

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["init_commit_ready"])
        self.assertIn("PARTIAL_RUNTIME_FILES_EXIST", result["issues"])

    def test_parent_missing_blocked(self) -> None:
        missing_root = self.temp_root / "missing"

        _preview, _approval, result = self._run_flow(
            order_executions_path=missing_root / "order_executions.json",
            order_locks_path=missing_root / "order_locks.json",
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["init_commit_ready"])
        self.assertIn("PARENT_DIRECTORY_MISSING", result["issues"])

    def test_malformed_path_invalid(self) -> None:
        _preview, _approval, result = self._run_flow(order_executions_path="")

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["init_commit_ready"])
        self.assertIn("MISSING_ORDER_EXECUTIONS_PATH", result["issues"])

    def test_project_runtime_path_without_allow_or_confirm_blocked(self) -> None:
        _preview, _approval, result = self._run_flow(
            order_executions_path=ROOT / "runtime" / "order_executions.json",
            order_locks_path=ROOT / "runtime" / "order_locks.json",
            allow_project_runtime_path=False,
            manual_runtime_file_init_confirmed=False,
            manual_project_runtime_path_confirmed=False,
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["init_commit_ready"])
        self.assertIn("PROJECT_RUNTIME_PATH_NOT_ALLOWED", result["issues"])

    def test_project_runtime_path_with_allow_and_confirm_ready_or_skipped(self) -> None:
        _preview, _approval, result = self._run_flow(
            order_executions_path=ROOT / "runtime" / "order_executions.json",
            order_locks_path=ROOT / "runtime" / "order_locks.json",
            allow_project_runtime_path=True,
            manual_runtime_file_init_confirmed=True,
            manual_project_runtime_path_confirmed=True,
        )

        self.assertIn(result["status"], {"READY", "SKIPPED"})
        self.assertIs(result["status"] == "READY", result["init_commit_ready"])

    def test_preview_only_runtime_write_contract(self) -> None:
        _preview, _approval, result = self._run_flow()

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertTrue(result["commit_plan"]["preview_only"])
        self.assertFalse(result["commit_plan"]["runtime_write"])
        self.assertTrue(result["validation"]["preview_only"])
        self.assertFalse(result["validation"]["runtime_write"])

    def test_planned_targets_and_schemas_preserved(self) -> None:
        preview, _approval, result = self._run_flow()

        self.assertEqual(preview["targets"], result["commit_plan"]["planned_targets"])
        self.assertEqual(default_order_executions_data(), result["commit_plan"]["planned_schemas"]["order_executions"])
        self.assertEqual(default_order_locks_data(), result["commit_plan"]["planned_schemas"]["order_locks"])

    def test_issues_and_warnings_preserved(self) -> None:
        self.order_executions_path.write_text("{}", encoding="utf-8")

        _preview, _approval, result = self._run_flow()

        self.assertIn("PARTIAL_RUNTIME_FILES_EXIST", result["issues"])
        self.assertEqual(result["commit_plan"]["issues"], result["issues"])

    def test_input_immutability(self) -> None:
        preview = build_execution_runtime_file_init_preview(
            self.order_executions_path,
            self.order_locks_path,
        )
        approval = approve_execution_runtime_file_init(
            preview,
            manual_runtime_file_init_confirmed=True,
        )
        before_preview = deepcopy(preview)
        before_approval = deepcopy(approval)

        result = run_execution_runtime_file_init_commit_plan_orchestrator(preview, approval)
        result["commit_plan"]["planned_targets"]["order_executions"] = "mutated"

        self.assertEqual(before_preview, preview)
        self.assertEqual(before_approval, approval)

    def test_no_file_write_or_mkdir(self) -> None:
        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("pathlib.Path.mkdir") as mkdir,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            _preview, _approval, result = self._run_flow()

        self.assertEqual("READY", result["status"])
        write_text.assert_not_called()
        mkdir.assert_not_called()
        open_mock.assert_not_called()
        self.assertFalse(self.order_executions_path.exists())
        self.assertFalse(self.order_locks_path.exists())

    def test_runtime_and_rules_hash_unchanged(self) -> None:
        runtime_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        rules_paths = list((ROOT / "routines").glob("**/rules.json"))
        before_runtime = {str(path): _sha256(path) for path in runtime_paths}
        before_rules = {str(path): _sha256(path) for path in rules_paths}

        self._run_flow()
        self._run_flow(
            order_executions_path=ROOT / "runtime" / "order_executions.json",
            order_locks_path=ROOT / "runtime" / "order_locks.json",
            allow_project_runtime_path=True,
            manual_runtime_file_init_confirmed=True,
            manual_project_runtime_path_confirmed=True,
        )

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
