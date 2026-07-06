# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_send_order_entrypoint_open_policy import (
    evaluate_execution_send_order_entrypoint_open_policy,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionSendOrderEntrypointOpenPolicyTest(unittest.TestCase):
    def _final_gate_result(self, **overrides: object) -> dict[str, object]:
        result = {
            "final_send_gate_ok": True,
            "next_stage": "SEND_ORDER_ENTRYPOINT_REQUIRED",
            "send_order_called": False,
            "blocked_reasons": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def _call_result(self, **overrides: object) -> dict[str, object]:
        result = {
            "orchestrator_type": "EXECUTION_FINAL_SEND_GATE_CALL_ORCHESTRATOR",
            "status": "FINAL_SEND_GATE_PASSED",
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
            "send_order_called": False,
            "final_send_gate_called": True,
            "next_stage": "SEND_ORDER_ENTRYPOINT_REQUIRED",
            "final_send_gate_result": self._final_gate_result(),
            "issues": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def _confirmations(self, **overrides: object) -> dict[str, object]:
        result = {"manual_send_order_entrypoint_confirmed": True}
        result.update(overrides)
        return result

    def _environment(self, **overrides: object) -> dict[str, object]:
        result = {
            "send_order_entrypoint_enabled": True,
            "real_send_order_enabled": True,
        }
        result.update(overrides)
        return result

    def test_all_valid_ready_to_open_send_order_entrypoint(self) -> None:
        result = evaluate_execution_send_order_entrypoint_open_policy(
            self._call_result(),
            confirmations=self._confirmations(),
            environment_flags=self._environment(),
        )

        self.assertEqual("READY_TO_OPEN_SEND_ORDER_ENTRYPOINT", result["status"])
        self.assertTrue(result["send_order_entrypoint_allowed"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["entrypoint_called"])

    def test_send_order_entrypoint_allowed_only_when_ready(self) -> None:
        cases = [
            self._call_result(status="BLOCKED", issues=["blocked"]),
            self._call_result(final_send_gate_called=False),
            self._call_result(send_order_called=True),
            self._call_result(final_send_gate_result=None),
        ]

        for call_result in cases:
            with self.subTest(call_result=call_result):
                result = evaluate_execution_send_order_entrypoint_open_policy(
                    call_result,
                    confirmations=self._confirmations(),
                    environment_flags=self._environment(),
                )

                self.assertNotEqual("READY_TO_OPEN_SEND_ORDER_ENTRYPOINT", result["status"])
                self.assertFalse(result["send_order_entrypoint_allowed"])

    def test_gate_call_result_blocked_invalid_and_malformed(self) -> None:
        cases = [
            (self._call_result(status="BLOCKED", issues=["FINAL_GATE_BLOCKED"]), "BLOCKED"),
            (self._call_result(status="INVALID", issues=["FINAL_GATE_INVALID"]), "INVALID"),
            ("malformed", "INVALID"),
        ]

        for call_result, expected_status in cases:
            with self.subTest(expected_status=expected_status):
                result = evaluate_execution_send_order_entrypoint_open_policy(
                    call_result,
                    confirmations=self._confirmations(),
                    environment_flags=self._environment(),
                )

                self.assertEqual(expected_status, result["status"])
                self.assertFalse(result["send_order_entrypoint_allowed"])

    def test_final_gate_called_false_blocks(self) -> None:
        result = evaluate_execution_send_order_entrypoint_open_policy(
            self._call_result(final_send_gate_called=False),
            confirmations=self._confirmations(),
            environment_flags=self._environment(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("FINAL_SEND_GATE_CALLED_NOT_TRUE", result["issues"])

    def test_send_order_called_true_blocks(self) -> None:
        result = evaluate_execution_send_order_entrypoint_open_policy(
            self._call_result(send_order_called=True),
            confirmations=self._confirmations(),
            environment_flags=self._environment(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("SEND_ORDER_ALREADY_CALLED", result["issues"])

    def test_missing_final_send_gate_result_blocks(self) -> None:
        result = evaluate_execution_send_order_entrypoint_open_policy(
            self._call_result(final_send_gate_result=None),
            confirmations=self._confirmations(),
            environment_flags=self._environment(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("FINAL_SEND_GATE_RESULT_REQUIRED", result["issues"])

    def test_final_send_gate_ok_false_blocks(self) -> None:
        result = evaluate_execution_send_order_entrypoint_open_policy(
            self._call_result(final_send_gate_result=self._final_gate_result(final_send_gate_ok=False)),
            confirmations=self._confirmations(),
            environment_flags=self._environment(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("FINAL_SEND_GATE_RESULT_NOT_OK", result["issues"])

    def test_next_stage_mismatch_blocks(self) -> None:
        result = evaluate_execution_send_order_entrypoint_open_policy(
            self._call_result(next_stage="BLOCKED"),
            confirmations=self._confirmations(),
            environment_flags=self._environment(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("SEND_ORDER_ENTRYPOINT_NEXT_STAGE_REQUIRED", result["issues"])

    def test_final_gate_result_next_stage_mismatch_blocks(self) -> None:
        result = evaluate_execution_send_order_entrypoint_open_policy(
            self._call_result(final_send_gate_result=self._final_gate_result(next_stage="BLOCKED")),
            confirmations=self._confirmations(),
            environment_flags=self._environment(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("FINAL_SEND_GATE_RESULT_NEXT_STAGE_REQUIRED", result["issues"])

    def test_manual_confirmation_missing_blocks(self) -> None:
        result = evaluate_execution_send_order_entrypoint_open_policy(
            self._call_result(),
            confirmations={},
            environment_flags=self._environment(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("MANUAL_SEND_ORDER_ENTRYPOINT_CONFIRMATION_REQUIRED", result["issues"])
        self.assertFalse(result["required_confirmations"]["manual_send_order_entrypoint_confirmed"])

    def test_environment_flag_missing_blocks(self) -> None:
        cases = [
            ({}, {"SEND_ORDER_ENTRYPOINT_ENVIRONMENT_DISABLED", "REAL_SEND_ORDER_ENVIRONMENT_DISABLED"}),
            ({"send_order_entrypoint_enabled": True}, {"REAL_SEND_ORDER_ENVIRONMENT_DISABLED"}),
            ({"real_send_order_enabled": True}, {"SEND_ORDER_ENTRYPOINT_ENVIRONMENT_DISABLED"}),
        ]

        for environment, expected_issues in cases:
            with self.subTest(environment=environment):
                result = evaluate_execution_send_order_entrypoint_open_policy(
                    self._call_result(),
                    confirmations=self._confirmations(),
                    environment_flags=environment,
                )

                self.assertEqual("BLOCKED", result["status"])
                self.assertTrue(expected_issues.issubset(set(result["issues"])))
                self.assertFalse(result["send_order_entrypoint_allowed"])

    def test_send_order_entrypoint_and_commit_services_are_not_called(self) -> None:
        with (
            mock.patch("send_order_entrypoint.execute_send_order") as entrypoint,
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
        ):
            result = evaluate_execution_send_order_entrypoint_open_policy(
                self._call_result(),
                confirmations=self._confirmations(),
                environment_flags=self._environment(),
            )

        self.assertEqual("READY_TO_OPEN_SEND_ORDER_ENTRYPOINT", result["status"])
        self.assertFalse(result["entrypoint_called"])
        self.assertFalse(result["send_order_called"])
        entrypoint.assert_not_called()
        queue_commit.assert_not_called()
        runtime_commit.assert_not_called()

    def test_runtime_order_queue_and_rules_hash_unchanged(self) -> None:
        protected_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
            ROOT / "routines" / "지표추종매매" / "rules.json",
        ]
        before = {str(path): _sha256(path) for path in protected_paths}

        evaluate_execution_send_order_entrypoint_open_policy(
            self._call_result(),
            confirmations=self._confirmations(),
            environment_flags=self._environment(),
        )
        evaluate_execution_send_order_entrypoint_open_policy(
            self._call_result(status="BLOCKED"),
            confirmations=self._confirmations(),
            environment_flags=self._environment(),
        )

        self.assertEqual(before, {str(path): _sha256(path) for path in protected_paths})


if __name__ == "__main__":
    unittest.main()
