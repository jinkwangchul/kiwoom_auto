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


def _final_gate_result(**overrides: object) -> dict[str, object]:
    result = {
        "final_send_gate_ok": True,
        "next_stage": "SEND_ORDER_ENTRYPOINT_REQUIRED",
        "send_order_called": False,
        "blocked_reasons": [],
        "warnings": [],
    }
    result.update(overrides)
    return result


def _gate_call_result(**overrides: object) -> dict[str, object]:
    result = {
        "orchestrator_type": "EXECUTION_FINAL_SEND_GATE_CALL_ORCHESTRATOR",
        "status": "FINAL_SEND_GATE_PASSED",
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "send_order_called": False,
        "final_send_gate_called": True,
        "next_stage": "SEND_ORDER_ENTRYPOINT_REQUIRED",
        "final_send_gate_result": _final_gate_result(),
        "issues": [],
        "warnings": [],
    }
    result.update(overrides)
    return result


def _confirmations(**overrides: object) -> dict[str, object]:
    result = {"manual_send_order_entrypoint_confirmed": True}
    result.update(overrides)
    return result


def _environment(**overrides: object) -> dict[str, object]:
    result = {
        "send_order_entrypoint_enabled": True,
        "real_send_order_enabled": True,
    }
    result.update(overrides)
    return result


class ExecutionSendOrderEntrypointOpenPolicyContractTest(unittest.TestCase):
    def test_all_valid_returns_ready_without_calling_entrypoint(self) -> None:
        with mock.patch("send_order_entrypoint.execute_send_order") as entrypoint:
            result = evaluate_execution_send_order_entrypoint_open_policy(
                _gate_call_result(),
                confirmations=_confirmations(),
                environment_flags=_environment(),
            )

        self.assertEqual("READY_TO_OPEN_SEND_ORDER_ENTRYPOINT", result["status"])
        self.assertTrue(result["send_order_entrypoint_allowed"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["entrypoint_called"])
        entrypoint.assert_not_called()

    def test_send_order_entrypoint_allowed_only_when_ready(self) -> None:
        ready = evaluate_execution_send_order_entrypoint_open_policy(
            _gate_call_result(),
            confirmations=_confirmations(),
            environment_flags=_environment(),
        )
        blocked = evaluate_execution_send_order_entrypoint_open_policy(
            _gate_call_result(status="BLOCKED", issues=["FINAL_GATE_BLOCKED"]),
            confirmations=_confirmations(),
            environment_flags=_environment(),
        )
        invalid = evaluate_execution_send_order_entrypoint_open_policy(
            _gate_call_result(status="INVALID", issues=["FINAL_GATE_INVALID"]),
            confirmations=_confirmations(),
            environment_flags=_environment(),
        )

        self.assertTrue(ready["send_order_entrypoint_allowed"])
        self.assertFalse(blocked["send_order_entrypoint_allowed"])
        self.assertFalse(invalid["send_order_entrypoint_allowed"])

    def test_gate_call_result_blocked_invalid_and_malformed_are_safe(self) -> None:
        cases = [
            (_gate_call_result(status="BLOCKED", issues=["FINAL_GATE_BLOCKED"]), "BLOCKED"),
            (_gate_call_result(status="INVALID", issues=["FINAL_GATE_INVALID"]), "INVALID"),
            ("malformed", "INVALID"),
        ]

        for call_result, expected in cases:
            with self.subTest(expected=expected):
                result = evaluate_execution_send_order_entrypoint_open_policy(
                    call_result,
                    confirmations=_confirmations(),
                    environment_flags=_environment(),
                )

                self.assertEqual(expected, result["status"])
                self.assertFalse(result["send_order_entrypoint_allowed"])
                self.assertFalse(result["send_order_called"])
                self.assertFalse(result["entrypoint_called"])

    def test_gate_call_payload_blockers(self) -> None:
        cases = [
            (_gate_call_result(final_send_gate_called=False), "FINAL_SEND_GATE_CALLED_NOT_TRUE"),
            (_gate_call_result(send_order_called=True), "SEND_ORDER_ALREADY_CALLED"),
            (_gate_call_result(final_send_gate_result=None), "FINAL_SEND_GATE_RESULT_REQUIRED"),
            (
                _gate_call_result(final_send_gate_result=_final_gate_result(final_send_gate_ok=False)),
                "FINAL_SEND_GATE_RESULT_NOT_OK",
            ),
            (_gate_call_result(next_stage="BLOCKED"), "SEND_ORDER_ENTRYPOINT_NEXT_STAGE_REQUIRED"),
            (
                _gate_call_result(final_send_gate_result=_final_gate_result(next_stage="BLOCKED")),
                "FINAL_SEND_GATE_RESULT_NEXT_STAGE_REQUIRED",
            ),
        ]

        for call_result, expected_issue in cases:
            with self.subTest(expected_issue=expected_issue):
                result = evaluate_execution_send_order_entrypoint_open_policy(
                    call_result,
                    confirmations=_confirmations(),
                    environment_flags=_environment(),
                )

                self.assertEqual("BLOCKED", result["status"])
                self.assertFalse(result["send_order_entrypoint_allowed"])
                self.assertIn(expected_issue, result["issues"])

    def test_manual_confirmation_missing_blocks(self) -> None:
        result = evaluate_execution_send_order_entrypoint_open_policy(
            _gate_call_result(),
            confirmations={},
            environment_flags=_environment(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["send_order_entrypoint_allowed"])
        self.assertFalse(result["required_confirmations"]["manual_send_order_entrypoint_confirmed"])
        self.assertIn("MANUAL_SEND_ORDER_ENTRYPOINT_CONFIRMATION_REQUIRED", result["issues"])

    def test_environment_flags_missing_or_false_block(self) -> None:
        cases = [
            ({}, {"SEND_ORDER_ENTRYPOINT_ENVIRONMENT_DISABLED", "REAL_SEND_ORDER_ENVIRONMENT_DISABLED"}),
            ({"send_order_entrypoint_enabled": False, "real_send_order_enabled": True}, {"SEND_ORDER_ENTRYPOINT_ENVIRONMENT_DISABLED"}),
            ({"send_order_entrypoint_enabled": True, "real_send_order_enabled": False}, {"REAL_SEND_ORDER_ENVIRONMENT_DISABLED"}),
        ]

        for environment, expected_issues in cases:
            with self.subTest(environment=environment):
                result = evaluate_execution_send_order_entrypoint_open_policy(
                    _gate_call_result(),
                    confirmations=_confirmations(),
                    environment_flags=environment,
                )

                self.assertEqual("BLOCKED", result["status"])
                self.assertFalse(result["send_order_entrypoint_allowed"])
                self.assertTrue(expected_issues.issubset(set(result["issues"])))

    def test_send_order_queue_commit_and_runtime_commit_are_not_called(self) -> None:
        with (
            mock.patch("send_order_entrypoint.execute_send_order") as entrypoint,
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
        ):
            result = evaluate_execution_send_order_entrypoint_open_policy(
                _gate_call_result(),
                confirmations=_confirmations(),
                environment_flags=_environment(),
            )

        self.assertEqual("READY_TO_OPEN_SEND_ORDER_ENTRYPOINT", result["status"])
        self.assertFalse(result["entrypoint_called"])
        self.assertFalse(result["send_order_called"])
        entrypoint.assert_not_called()
        queue_commit.assert_not_called()
        runtime_commit.assert_not_called()

    def test_runtime_order_queue_and_rules_are_unchanged(self) -> None:
        protected_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        protected_paths.extend((ROOT / "routines").glob("**/rules.json"))
        before = {str(path): _sha256(path) for path in protected_paths}

        evaluate_execution_send_order_entrypoint_open_policy(
            _gate_call_result(),
            confirmations=_confirmations(),
            environment_flags=_environment(),
        )
        evaluate_execution_send_order_entrypoint_open_policy(
            _gate_call_result(status="BLOCKED"),
            confirmations=_confirmations(),
            environment_flags=_environment(),
        )

        self.assertEqual(before, {str(path): _sha256(path) for path in protected_paths})


if __name__ == "__main__":
    unittest.main()
