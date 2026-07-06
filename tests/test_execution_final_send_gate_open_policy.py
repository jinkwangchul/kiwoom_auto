# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_final_send_gate_open_policy import evaluate_execution_final_send_gate_open_policy


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionFinalSendGateOpenPolicyTest(unittest.TestCase):
    def _final_input(self, **overrides: object) -> dict[str, object]:
        final_input = {
            "adapter_preview_result": {"adapter_preview_ok": True},
            "send_order_request_preview": {"order_id": "ORDER_OPEN_1"},
            "order_queued_record": {"order_id": "ORDER_OPEN_1"},
            "identity": {"order_id": "ORDER_OPEN_1"},
            "current_guard": {"account_no": "12345678"},
            "context": {"manual_final_send_confirmed": True},
        }
        final_input.update(overrides)
        return final_input

    def _orchestrator(self, **overrides: object) -> dict[str, object]:
        result = {
            "orchestrator_type": "EXECUTION_FINAL_SEND_GATE_ORCHESTRATOR",
            "status": "READY_FOR_FINAL_SEND_GATE",
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
            "send_order_called": False,
            "final_send_gate_called": False,
            "final_send_gate_ready": True,
            "next_stage": "FINAL_SEND_GATE_SERVICE_REQUIRED",
            "final_send_gate_input": self._final_input(),
            "identity": {"order_id": "ORDER_OPEN_1"},
            "issues": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def _confirmations(self, **overrides: object) -> dict[str, object]:
        confirmations = {"manual_final_send_gate_call_confirmed": True}
        confirmations.update(overrides)
        return confirmations

    def _environment_flags(self, **overrides: object) -> dict[str, object]:
        flags = {"final_send_gate_call_enabled": True}
        flags.update(overrides)
        return flags

    def _evaluate(
        self,
        orchestrator_result: object | None = None,
        confirmations: object | None = None,
        environment_flags: object | None = None,
    ) -> dict[str, object]:
        return evaluate_execution_final_send_gate_open_policy(
            self._orchestrator() if orchestrator_result is None else orchestrator_result,
            self._confirmations() if confirmations is None else confirmations,
            self._environment_flags() if environment_flags is None else environment_flags,
        )

    def assert_boundary(self, result: dict[str, object]) -> None:
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["final_send_gate_called"])

    def test_all_valid_ready_to_open_final_send_gate(self) -> None:
        result = self._evaluate()

        self.assertEqual("READY_TO_OPEN_FINAL_SEND_GATE", result["status"])
        self.assertTrue(result["final_send_gate_call_allowed"])
        self.assertTrue(result["required_confirmations"]["manual_final_send_gate_call_confirmed"])
        self.assertTrue(result["environment_checks"]["final_send_gate_call_enabled"])
        self.assert_boundary(result)

    def test_final_send_gate_call_allowed_only_when_ready(self) -> None:
        cases = [
            self._orchestrator(status="BLOCKED", issues=["blocked"]),
            self._orchestrator(status="INVALID", issues=["invalid"]),
            self._orchestrator(final_send_gate_ready=False),
            self._orchestrator(next_stage="OTHER"),
            self._orchestrator(final_send_gate_input=None),
        ]
        for orchestrator_result in cases:
            with self.subTest(orchestrator_result=orchestrator_result):
                result = self._evaluate(orchestrator_result=orchestrator_result)

                self.assertNotEqual("READY_TO_OPEN_FINAL_SEND_GATE", result["status"])
                self.assertFalse(result["final_send_gate_call_allowed"])
                self.assert_boundary(result)

    def test_orchestrator_blocked_invalid_and_malformed(self) -> None:
        blocked = self._evaluate(orchestrator_result=self._orchestrator(status="BLOCKED", issues=["blocked"]))
        invalid = self._evaluate(orchestrator_result=self._orchestrator(status="INVALID", issues=["invalid"]))
        malformed = self._evaluate(orchestrator_result="malformed")

        self.assertEqual("BLOCKED", blocked["status"])
        self.assertEqual("INVALID", invalid["status"])
        self.assertEqual("INVALID", malformed["status"])
        self.assertIn("MALFORMED_FINAL_SEND_GATE_ORCHESTRATOR_RESULT", malformed["issues"])

    def test_final_send_gate_ready_false_blocks(self) -> None:
        result = self._evaluate(orchestrator_result=self._orchestrator(final_send_gate_ready=False))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("FINAL_SEND_GATE_READY_NOT_TRUE", result["issues"])

    def test_next_stage_mismatch_blocks(self) -> None:
        result = self._evaluate(orchestrator_result=self._orchestrator(next_stage="OTHER"))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("FINAL_SEND_GATE_NEXT_STAGE_NOT_SERVICE_REQUIRED", result["issues"])

    def test_missing_final_send_gate_input_blocks(self) -> None:
        result = self._evaluate(orchestrator_result=self._orchestrator(final_send_gate_input=None))

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("FINAL_SEND_GATE_INPUT_REQUIRED", result["issues"])

    def test_manual_confirmation_missing_blocks(self) -> None:
        result = self._evaluate(confirmations={})

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["required_confirmations"]["manual_final_send_gate_call_confirmed"])
        self.assertIn("MANUAL_FINAL_SEND_GATE_CALL_CONFIRMATION_REQUIRED", result["issues"])

    def test_environment_flag_missing_blocks(self) -> None:
        result = self._evaluate(environment_flags={})

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["environment_checks"]["final_send_gate_call_enabled"])
        self.assertIn("FINAL_SEND_GATE_CALL_ENVIRONMENT_DISABLED", result["issues"])

    def test_final_send_gate_send_order_queue_and_runtime_commit_are_not_called(self) -> None:
        with (
            mock.patch("final_send_gate_service.evaluate_final_send_gate") as final_gate,
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
        ):
            ready = self._evaluate()
            blocked = self._evaluate(confirmations={})

        self.assertEqual("READY_TO_OPEN_FINAL_SEND_GATE", ready["status"])
        self.assertEqual("BLOCKED", blocked["status"])
        final_gate.assert_not_called()
        send_order.assert_not_called()
        queue_commit.assert_not_called()
        runtime_commit.assert_not_called()

    def test_order_queue_runtime_and_rules_hash_unchanged(self) -> None:
        runtime_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        rules_paths = list((ROOT / "routines").glob("**/rules.json"))
        before_runtime = {str(path): _sha256(path) for path in runtime_paths}
        before_rules = {str(path): _sha256(path) for path in rules_paths}

        self._evaluate()
        self._evaluate(confirmations={})

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})


if __name__ == "__main__":
    unittest.main()
