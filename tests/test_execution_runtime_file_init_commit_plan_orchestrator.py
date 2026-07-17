from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from execution_runtime_file_init_approval_gate import approve_execution_runtime_file_init
from execution_runtime_file_init_commit_plan_orchestrator import (
    ORCHESTRATOR_TYPE,
    run_execution_runtime_file_init_commit_plan_orchestrator,
)
from execution_runtime_file_init_preview import build_execution_runtime_file_init_preview


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionRuntimeFileInitCommitPlanOrchestratorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.tmp.name)
        self.order_executions_path = self.temp_root / "order_executions.json"
        self.order_locks_path = self.temp_root / "order_locks.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _preview(self, **overrides) -> dict:
        kwargs = {
            "order_executions_path": self.order_executions_path,
            "order_locks_path": self.order_locks_path,
        }
        kwargs.update(overrides)
        return build_execution_runtime_file_init_preview(**kwargs)

    def _approval(self, preview: dict, **overrides) -> dict:
        kwargs = {"manual_runtime_file_init_confirmed": True}
        kwargs.update(overrides)
        return approve_execution_runtime_file_init(preview, **kwargs)

    def _run(self, preview: dict, approval: dict) -> dict:
        return run_execution_runtime_file_init_commit_plan_orchestrator(preview, approval)

    def test_ready_flow(self) -> None:
        preview = self._preview()
        approval = self._approval(preview)

        result = self._run(preview, approval)

        self.assertEqual(ORCHESTRATOR_TYPE, result["orchestrator_type"])
        self.assertEqual("READY", result["status"])
        self.assertTrue(result["init_commit_ready"])
        self.assertTrue(result["validation"]["valid"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])

    def test_partial_flow_ready_with_warning(self) -> None:
        self.order_executions_path.write_text("{}", encoding="utf-8")
        preview = self._preview()
        approval = self._approval(preview)

        result = self._run(preview, approval)

        self.assertEqual("READY", result["status"])
        self.assertTrue(result["init_commit_ready"])
        self.assertTrue(result["validation"]["valid"])
        self.assertIn("PARTIAL_RUNTIME_FILES_EXIST", result["warnings"])

    def test_invalid_flow(self) -> None:
        preview = self._preview(order_executions_path="")
        approval = self._approval(preview)

        result = self._run(preview, approval)

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["init_commit_ready"])
        self.assertTrue(result["validation"]["valid"])
        self.assertIn("MISSING_ORDER_EXECUTIONS_PATH", result["issues"])

    def test_skipped_flow(self) -> None:
        self.order_executions_path.write_text("{}", encoding="utf-8")
        self.order_locks_path.write_text("{}", encoding="utf-8")
        preview = self._preview()
        approval = self._approval(preview)

        result = self._run(preview, approval)

        self.assertEqual("SKIPPED", result["status"])
        self.assertFalse(result["init_commit_ready"])
        self.assertTrue(result["validation"]["valid"])

    def test_validator_invalid_flow(self) -> None:
        preview = self._preview()
        approval = self._approval(preview)

        with mock.patch(
            "execution_runtime_file_init_commit_plan_orchestrator.validate_execution_runtime_file_init_commit_plan_preview",
            return_value={
                "valid": False,
                "status": "READY",
                "preview_only": True,
                "runtime_write": False,
                "issues": ["FORCED_VALIDATOR_INVALID"],
                "warnings": [],
            },
        ):
            result = self._run(preview, approval)

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["init_commit_ready"])
        self.assertIn("FORCED_VALIDATOR_INVALID", result["issues"])

    def test_init_commit_ready_preserved_only_for_ready(self) -> None:
        ready_preview = self._preview()
        ready = self._run(ready_preview, self._approval(ready_preview))

        self.order_executions_path.write_text("{}", encoding="utf-8")
        self.order_locks_path.write_text("{}", encoding="utf-8")
        skipped_preview = self._preview()
        skipped = self._run(skipped_preview, self._approval(skipped_preview))

        self.assertTrue(ready["init_commit_ready"])
        self.assertFalse(skipped["init_commit_ready"])

    def test_input_immutability(self) -> None:
        preview = self._preview()
        approval = self._approval(preview)
        before_preview = deepcopy(preview)
        before_approval = deepcopy(approval)

        result = self._run(preview, approval)
        result["commit_plan"]["planned_targets"]["order_executions"] = "mutated"

        self.assertEqual(before_preview, preview)
        self.assertEqual(before_approval, approval)

    def test_no_file_write_or_mkdir(self) -> None:
        preview = self._preview()
        approval = self._approval(preview)

        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("pathlib.Path.mkdir") as mkdir,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = self._run(preview, approval)

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
        self._run(preview, approval)

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})
        self.assertFalse((ROOT / "runtime" / "order_executions.json").exists())
        self.assertFalse((ROOT / "runtime" / "order_locks.json").exists())


if __name__ == "__main__":
    unittest.main()
