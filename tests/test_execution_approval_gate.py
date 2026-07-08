# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_approval_gate import evaluate_execution_approval


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _protected_paths() -> list[Path]:
    paths = [
        ROOT / "runtime" / "order_queue.json",
        ROOT / "runtime" / "order_executions.json",
        ROOT / "runtime" / "order_locks.json",
    ]
    paths.extend(sorted((ROOT / "routines").glob("*/rules.json")))
    return paths


class ExecutionApprovalGateTest(unittest.TestCase):
    def _readiness(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "status": "READY",
            "readiness": {
                "queue_commit_ready": True,
                "guard_ready": True,
                "runtime_ready": True,
                "operation_ready": True,
            },
            "issues": [],
            "warnings": [],
            "validation_summary": {"ready": True},
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
            "send_order_called": False,
            "queue_commit_called": False,
        }
        result.update(overrides)
        return result

    def _operator_context(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "operator_confirmed": True,
            "real_trade_enabled": True,
            "real_trade_guard_ok": True,
            "emergency_stop": False,
        }
        result.update(overrides)
        return result

    def _approval_policy(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "approved": True,
            "order_id": "ORDER_APPROVAL_GATE_1",
            "source_signal_id": "SIGNAL_APPROVAL_GATE_1",
        }
        result.update(overrides)
        return result

    def _runtime_snapshot(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "locked": False,
            "duplicate": False,
            "locks": [],
            "existing_orders": [],
            "emergency_stop": False,
        }
        result.update(overrides)
        return result

    def test_approved_normal(self) -> None:
        result = evaluate_execution_approval(
            self._readiness(),
            self._operator_context(),
            self._approval_policy(),
            self._runtime_snapshot(),
        )

        self.assertEqual("APPROVED", result["status"])
        self.assertTrue(result["approval"]["approved"])
        self.assertTrue(result["approval_summary"]["approved"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["queue_commit_called"])
        self.assertFalse(result["send_order_called"])

    def test_readiness_blocked_is_denied(self) -> None:
        result = evaluate_execution_approval(
            self._readiness(status="BLOCKED"),
            self._operator_context(),
            self._approval_policy(),
            self._runtime_snapshot(),
        )

        self.assertEqual("DENIED", result["status"])
        self.assertIn("readiness_result.status is not READY", result["issues"])

    def test_readiness_invalid_is_invalid(self) -> None:
        result = evaluate_execution_approval(
            self._readiness(status="INVALID"),
            self._operator_context(),
            self._approval_policy(),
            self._runtime_snapshot(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("readiness_result.status is INVALID", result["issues"])

    def test_operator_confirmed_false_is_denied(self) -> None:
        result = evaluate_execution_approval(
            self._readiness(),
            self._operator_context(operator_confirmed=False),
            self._approval_policy(),
            self._runtime_snapshot(),
        )

        self.assertEqual("DENIED", result["status"])
        self.assertIn("operator_context.operator_confirmed is not true", result["issues"])

    def test_real_trade_enabled_false_is_denied(self) -> None:
        result = evaluate_execution_approval(
            self._readiness(),
            self._operator_context(real_trade_enabled=False),
            self._approval_policy(),
            self._runtime_snapshot(),
        )

        self.assertEqual("DENIED", result["status"])
        self.assertIn("operator_context.real_trade_enabled is not true", result["issues"])

    def test_approval_policy_reject_is_denied(self) -> None:
        result = evaluate_execution_approval(
            self._readiness(),
            self._operator_context(),
            self._approval_policy(approved=False),
            self._runtime_snapshot(),
        )

        self.assertEqual("DENIED", result["status"])
        self.assertIn("approval policy rejected", result["issues"])

    def test_malformed_input_is_invalid(self) -> None:
        malformed_readiness = evaluate_execution_approval(
            "bad",
            self._operator_context(),
            self._approval_policy(),
            self._runtime_snapshot(),
        )
        malformed_policy = evaluate_execution_approval(
            self._readiness(),
            self._operator_context(),
            "bad",
            self._runtime_snapshot(),
        )

        self.assertEqual("INVALID", malformed_readiness["status"])
        self.assertEqual("INVALID", malformed_policy["status"])

    def test_runtime_lock_and_duplicate_are_denied(self) -> None:
        locked = evaluate_execution_approval(
            self._readiness(),
            self._operator_context(),
            self._approval_policy(),
            self._runtime_snapshot(locks=[{"order_id": "ORDER_APPROVAL_GATE_1"}]),
        )
        duplicate = evaluate_execution_approval(
            self._readiness(),
            self._operator_context(),
            self._approval_policy(),
            self._runtime_snapshot(existing_orders=[{"order_id": "ORDER_APPROVAL_GATE_1"}]),
        )

        self.assertEqual("DENIED", locked["status"])
        self.assertIn("runtime lock exists", locked["issues"])
        self.assertEqual("DENIED", duplicate["status"])
        self.assertIn("duplicate order exists", duplicate["issues"])

    def test_emergency_stop_is_denied(self) -> None:
        result = evaluate_execution_approval(
            self._readiness(),
            self._operator_context(emergency_stop=True),
            self._approval_policy(),
            self._runtime_snapshot(),
        )

        self.assertEqual("DENIED", result["status"])
        self.assertIn("emergency stop is active", result["issues"])

    def test_inputs_are_not_mutated(self) -> None:
        readiness = self._readiness()
        operator_context = self._operator_context()
        approval_policy = self._approval_policy()
        runtime_snapshot = self._runtime_snapshot()
        originals = (
            deepcopy(readiness),
            deepcopy(operator_context),
            deepcopy(approval_policy),
            deepcopy(runtime_snapshot),
        )

        result = evaluate_execution_approval(readiness, operator_context, approval_policy, runtime_snapshot)
        result["approval"]["policy"]["approved"] = False

        self.assertEqual(originals[0], readiness)
        self.assertEqual(originals[1], operator_context)
        self.assertEqual(originals[2], approval_policy)
        self.assertEqual(originals[3], runtime_snapshot)

    def test_runtime_order_queue_rules_hash_unchanged(self) -> None:
        before = {path: _sha256(path) for path in _protected_paths()}

        with mock.patch("execution_queue_commit_service.commit_execution_queue_manually", create=True) as queue_commit, \
            mock.patch("send_order_entrypoint.execute_send_order") as send_order:
            result = evaluate_execution_approval(
                self._readiness(),
                self._operator_context(),
                self._approval_policy(),
                self._runtime_snapshot(),
            )

        self.assertEqual("APPROVED", result["status"])
        queue_commit.assert_not_called()
        send_order.assert_not_called()
        self.assertEqual(before, {path: _sha256(path) for path in _protected_paths()})


if __name__ == "__main__":
    unittest.main()
