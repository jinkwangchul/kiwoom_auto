# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_broker_dispatch_open_policy import evaluate_execution_broker_dispatch_open_policy


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionBrokerDispatchOpenPolicyTest(unittest.TestCase):
    def _entrypoint_result(self, **overrides: object) -> dict[str, object]:
        result = {
            "send_order_executed": True,
            "entrypoint_stage": "send_order_called_mock",
            "next_stage": "SEND_ORDER_RESULT_REVIEW_REQUIRED",
            "broker": "SEND_ORDER_ENTRYPOINT_PREVIEW_BROKER",
            "order_id": "ORDER_BROKER_1",
            "order_queued_id": "ORDER_QUEUED_BROKER_1",
            "request_hash": "HASH_BROKER_1",
            "lock_id": "LOCK_BROKER_1",
            "execution_id": "EXEC_BROKER_1",
            "broker_result": {
                "broker_status": "PREVIEW_ACCEPTED",
                "preview_only": True,
                "kiwoom_api_called": False,
            },
            "runtime_write_required": True,
            "send_order_called": True,
            "blocked_reasons": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def _orchestrator(self, **overrides: object) -> dict[str, object]:
        result = {
            "orchestrator_type": "EXECUTION_SEND_ORDER_ENTRYPOINT_ORCHESTRATOR",
            "status": "SEND_ORDER_ENTRYPOINT_PASSED",
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
            "entrypoint_called": True,
            "send_order_called": True,
            "next_stage": "BROKER_SEND_REQUIRED",
            "send_order_entrypoint_result": self._entrypoint_result(),
            "issues": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def _confirmations(self, **overrides: object) -> dict[str, object]:
        result = {"manual_broker_dispatch_confirmed": True}
        result.update(overrides)
        return result

    def _environment(self, **overrides: object) -> dict[str, object]:
        result = {
            "broker_dispatch_enabled": True,
            "real_broker_dispatch_enabled": True,
            "kiwoom_connected": True,
            "account_selected": True,
            "real_trade_enabled": True,
        }
        result.update(overrides)
        return result

    def test_all_valid_ready_to_open_broker_dispatch(self) -> None:
        result = evaluate_execution_broker_dispatch_open_policy(
            self._orchestrator(),
            confirmations=self._confirmations(),
            environment_flags=self._environment(),
        )

        self.assertEqual("READY_TO_OPEN_BROKER_DISPATCH", result["status"])
        self.assertTrue(result["broker_dispatch_allowed"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertTrue(result["entrypoint_called"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["broker_called"])
        self.assertFalse(result["kiwoom_called"])

    def test_broker_dispatch_allowed_only_when_ready(self) -> None:
        ready = evaluate_execution_broker_dispatch_open_policy(
            self._orchestrator(),
            confirmations=self._confirmations(),
            environment_flags=self._environment(),
        )
        blocked = evaluate_execution_broker_dispatch_open_policy(
            self._orchestrator(status="BLOCKED", issues=["ENTRYPOINT_BLOCKED"]),
            confirmations=self._confirmations(),
            environment_flags=self._environment(),
        )
        invalid = evaluate_execution_broker_dispatch_open_policy(
            self._orchestrator(status="INVALID", issues=["ENTRYPOINT_INVALID"]),
            confirmations=self._confirmations(),
            environment_flags=self._environment(),
        )

        self.assertTrue(ready["broker_dispatch_allowed"])
        self.assertFalse(blocked["broker_dispatch_allowed"])
        self.assertFalse(invalid["broker_dispatch_allowed"])

    def test_entrypoint_blocked_invalid_and_malformed(self) -> None:
        cases = [
            (self._orchestrator(status="BLOCKED", issues=["ENTRYPOINT_BLOCKED"]), "BLOCKED"),
            (self._orchestrator(status="INVALID", issues=["ENTRYPOINT_INVALID"]), "INVALID"),
            ("malformed", "INVALID"),
        ]
        for orchestrator, expected in cases:
            with self.subTest(expected=expected):
                result = evaluate_execution_broker_dispatch_open_policy(
                    orchestrator,
                    confirmations=self._confirmations(),
                    environment_flags=self._environment(),
                )

                self.assertEqual(expected, result["status"])
                self.assertFalse(result["broker_dispatch_allowed"])

    def test_next_stage_mismatch_blocks(self) -> None:
        result = evaluate_execution_broker_dispatch_open_policy(
            self._orchestrator(next_stage="BLOCKED"),
            confirmations=self._confirmations(),
            environment_flags=self._environment(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("BROKER_DISPATCH_NEXT_STAGE_REQUIRED", result["issues"])

    def test_entrypoint_called_false_blocks(self) -> None:
        result = evaluate_execution_broker_dispatch_open_policy(
            self._orchestrator(entrypoint_called=False),
            confirmations=self._confirmations(),
            environment_flags=self._environment(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("ENTRYPOINT_CALLED_NOT_TRUE", result["issues"])

    def test_missing_send_order_entrypoint_result_blocks(self) -> None:
        result = evaluate_execution_broker_dispatch_open_policy(
            self._orchestrator(send_order_entrypoint_result=None),
            confirmations=self._confirmations(),
            environment_flags=self._environment(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("SEND_ORDER_ENTRYPOINT_RESULT_REQUIRED", result["issues"])

    def test_entrypoint_result_not_normal_blocks(self) -> None:
        cases = [
            (self._entrypoint_result(send_order_executed=False), "SEND_ORDER_ENTRYPOINT_RESULT_NOT_EXECUTED"),
            (self._entrypoint_result(send_order_called=False), "SEND_ORDER_ENTRYPOINT_RESULT_SEND_ORDER_CALLED_NOT_TRUE"),
            (self._entrypoint_result(next_stage="BLOCKED"), "SEND_ORDER_ENTRYPOINT_RESULT_NEXT_STAGE_REQUIRED"),
            (
                self._entrypoint_result(blocked_reasons=["blocked"]),
                "SEND_ORDER_ENTRYPOINT_RESULT_HAS_BLOCKED_REASONS",
            ),
        ]
        for entrypoint_result, expected_issue in cases:
            with self.subTest(expected_issue=expected_issue):
                result = evaluate_execution_broker_dispatch_open_policy(
                    self._orchestrator(send_order_entrypoint_result=entrypoint_result),
                    confirmations=self._confirmations(),
                    environment_flags=self._environment(),
                )

                self.assertEqual("BLOCKED", result["status"])
                self.assertIn(expected_issue, result["issues"])

    def test_manual_confirmation_missing_blocks(self) -> None:
        result = evaluate_execution_broker_dispatch_open_policy(
            self._orchestrator(),
            confirmations={},
            environment_flags=self._environment(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("MANUAL_BROKER_DISPATCH_CONFIRMATION_REQUIRED", result["issues"])
        self.assertFalse(result["required_confirmations"]["manual_broker_dispatch_confirmed"])

    def test_environment_flags_false_block(self) -> None:
        cases = [
            ("broker_dispatch_enabled", "BROKER_DISPATCH_ENVIRONMENT_DISABLED"),
            ("real_broker_dispatch_enabled", "REAL_BROKER_DISPATCH_ENVIRONMENT_DISABLED"),
            ("kiwoom_connected", "KIWOOM_CONNECTED_NOT_TRUE"),
            ("account_selected", "ACCOUNT_SELECTED_NOT_TRUE"),
            ("real_trade_enabled", "REAL_TRADE_ENABLED_NOT_TRUE"),
        ]
        for flag, expected_issue in cases:
            with self.subTest(flag=flag):
                environment = self._environment(**{flag: False})
                result = evaluate_execution_broker_dispatch_open_policy(
                    self._orchestrator(),
                    confirmations=self._confirmations(),
                    environment_flags=environment,
                )

                self.assertEqual("BLOCKED", result["status"])
                self.assertIn(expected_issue, result["issues"])
                self.assertFalse(result["broker_dispatch_allowed"])

    def test_broker_kiwoom_queue_and_runtime_are_not_called(self) -> None:
        with (
            mock.patch("send_order_entrypoint.execute_send_order") as entrypoint,
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
            mock.patch("execution_controller.build_kiwoom_order_request") as kiwoom_request,
        ):
            result = evaluate_execution_broker_dispatch_open_policy(
                self._orchestrator(),
                confirmations=self._confirmations(),
                environment_flags=self._environment(),
            )

        self.assertEqual("READY_TO_OPEN_BROKER_DISPATCH", result["status"])
        self.assertFalse(result["broker_called"])
        self.assertFalse(result["kiwoom_called"])
        self.assertFalse(result["send_order_called"])
        entrypoint.assert_not_called()
        queue_commit.assert_not_called()
        runtime_commit.assert_not_called()
        kiwoom_request.assert_not_called()

    def test_runtime_order_queue_and_rules_hash_unchanged(self) -> None:
        protected_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        protected_paths.extend((ROOT / "routines").glob("**/rules.json"))
        before = {str(path): _sha256(path) for path in protected_paths}

        evaluate_execution_broker_dispatch_open_policy(
            self._orchestrator(),
            confirmations=self._confirmations(),
            environment_flags=self._environment(),
        )
        evaluate_execution_broker_dispatch_open_policy(
            self._orchestrator(status="BLOCKED"),
            confirmations=self._confirmations(),
            environment_flags=self._environment(),
        )

        self.assertEqual(before, {str(path): _sha256(path) for path in protected_paths})


if __name__ == "__main__":
    unittest.main()
