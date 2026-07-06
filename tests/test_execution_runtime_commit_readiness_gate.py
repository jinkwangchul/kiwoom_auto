from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_runtime_commit_readiness_gate import (
    GATE_TYPE,
    evaluate_execution_runtime_commit_readiness,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionRuntimeCommitReadinessGateTest(unittest.TestCase):
    def _orchestrator_result(self, *, status: str = "READY") -> dict:
        return {
            "status": status,
            "preview_only": True,
            "runtime_write": False,
            "orchestrator_type": "EXECUTION_RUNTIME_WRITE_PREVIEW_ORCHESTRATOR",
            "write_preview": {"status": status},
            "validation": {"valid": status != "INVALID"},
            "issues": [] if status == "READY" else [f"ORCHESTRATOR_{status}"],
            "warnings": ["Preview mode"],
        }

    def test_ready_with_confirmations_true(self) -> None:
        result = evaluate_execution_runtime_commit_readiness(
            self._orchestrator_result(),
            manual_execution_runtime_commit_confirmed=True,
            manual_runtime_file_write_confirmed=True,
        )

        self.assertEqual(GATE_TYPE, result["gate_type"])
        self.assertEqual("READY", result["status"])
        self.assertTrue(result["commit_ready"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertTrue(
            result["required_confirmations"]["manual_execution_runtime_commit_confirmed"]
        )
        self.assertTrue(
            result["required_confirmations"]["manual_runtime_file_write_confirmed"]
        )
        self.assertEqual([], result["issues"])

    def test_ready_with_first_confirmation_false_is_blocked(self) -> None:
        result = evaluate_execution_runtime_commit_readiness(
            self._orchestrator_result(),
            manual_execution_runtime_commit_confirmed=False,
            manual_runtime_file_write_confirmed=True,
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["commit_ready"])
        self.assertIn(
            "MANUAL_EXECUTION_RUNTIME_COMMIT_CONFIRMATION_REQUIRED",
            result["issues"],
        )

    def test_ready_with_second_confirmation_false_is_blocked(self) -> None:
        result = evaluate_execution_runtime_commit_readiness(
            self._orchestrator_result(),
            manual_execution_runtime_commit_confirmed=True,
            manual_runtime_file_write_confirmed=False,
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["commit_ready"])
        self.assertIn(
            "MANUAL_RUNTIME_FILE_WRITE_CONFIRMATION_REQUIRED",
            result["issues"],
        )

    def test_blocked_orchestrator_is_blocked(self) -> None:
        result = evaluate_execution_runtime_commit_readiness(
            self._orchestrator_result(status="BLOCKED"),
            manual_execution_runtime_commit_confirmed=True,
            manual_runtime_file_write_confirmed=True,
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["commit_ready"])
        self.assertIn("ORCHESTRATOR_BLOCKED", result["issues"])

    def test_invalid_orchestrator_is_invalid(self) -> None:
        result = evaluate_execution_runtime_commit_readiness(
            self._orchestrator_result(status="INVALID"),
            manual_execution_runtime_commit_confirmed=True,
            manual_runtime_file_write_confirmed=True,
        )

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["commit_ready"])
        self.assertIn("ORCHESTRATOR_INVALID", result["issues"])

    def test_malformed_input_is_invalid(self) -> None:
        result = evaluate_execution_runtime_commit_readiness(
            "bad",
            manual_execution_runtime_commit_confirmed=True,
            manual_runtime_file_write_confirmed=True,
        )

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["commit_ready"])
        self.assertIn("MALFORMED_WRITE_PREVIEW_ORCHESTRATOR_RESULT", result["issues"])

    def test_unknown_orchestrator_status_is_invalid(self) -> None:
        result = evaluate_execution_runtime_commit_readiness(
            self._orchestrator_result(status="WAITING"),
            manual_execution_runtime_commit_confirmed=True,
            manual_runtime_file_write_confirmed=True,
        )

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["commit_ready"])
        self.assertIn("INVALID_WRITE_PREVIEW_ORCHESTRATOR_STATUS", result["issues"])

    def test_preview_only_runtime_write_boundaries(self) -> None:
        result = evaluate_execution_runtime_commit_readiness(
            self._orchestrator_result(),
            manual_execution_runtime_commit_confirmed=True,
            manual_runtime_file_write_confirmed=True,
        )

        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])

    def test_input_immutability(self) -> None:
        orchestrator_result = self._orchestrator_result()
        before = deepcopy(orchestrator_result)

        result = evaluate_execution_runtime_commit_readiness(
            orchestrator_result,
            manual_execution_runtime_commit_confirmed=True,
            manual_runtime_file_write_confirmed=True,
        )
        result["issues"].append("MUTATED_RESULT_ONLY")

        self.assertEqual(before, orchestrator_result)

    def test_no_file_write_or_mkdir(self) -> None:
        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("pathlib.Path.mkdir") as mkdir,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = evaluate_execution_runtime_commit_readiness(
                self._orchestrator_result(),
                manual_execution_runtime_commit_confirmed=True,
                manual_runtime_file_write_confirmed=True,
            )

        self.assertEqual("READY", result["status"])
        write_text.assert_not_called()
        mkdir.assert_not_called()
        open_mock.assert_not_called()

    def test_runtime_and_rules_hash_unchanged(self) -> None:
        runtime_queue = ROOT / "runtime" / "order_queue.json"
        rules_path = ROOT / "routines" / "지표추종매매" / "rules.json"
        before_runtime = _sha256(runtime_queue)
        before_rules = _sha256(rules_path)

        evaluate_execution_runtime_commit_readiness(
            self._orchestrator_result(),
            manual_execution_runtime_commit_confirmed=True,
            manual_runtime_file_write_confirmed=True,
        )

        self.assertEqual(before_runtime, _sha256(runtime_queue))
        self.assertEqual(before_rules, _sha256(rules_path))

    def test_module_has_no_write_commit_execution_send_order_gui_connections(self) -> None:
        import execution_runtime_commit_readiness_gate

        module_text = execution_runtime_commit_readiness_gate.__loader__.get_source(
            execution_runtime_commit_readiness_gate.__name__
        )

        self.assertNotIn("write_text", module_text)
        self.assertNotIn("mkdir", module_text)
        self.assertNotIn("os.replace", module_text)
        self.assertNotIn("commit_execution_queue", module_text)
        self.assertNotIn("send_order", module_text)
        self.assertNotIn("ExecutionController", module_text)
        self.assertNotIn("QWidget", module_text)


if __name__ == "__main__":
    unittest.main()
