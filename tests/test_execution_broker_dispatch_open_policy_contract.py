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


def _entrypoint_result(**overrides: object) -> dict[str, object]:
    result = {
        "send_order_executed": True,
        "entrypoint_stage": "send_order_called_mock",
        "next_stage": "SEND_ORDER_RESULT_REVIEW_REQUIRED",
        "broker": "SEND_ORDER_ENTRYPOINT_PREVIEW_BROKER",
        "order_id": "ORDER_BROKER_CONTRACT_1",
        "order_queued_id": "ORDER_QUEUED_BROKER_CONTRACT_1",
        "request_hash": "HASH_BROKER_CONTRACT_1",
        "lock_id": "LOCK_BROKER_CONTRACT_1",
        "execution_id": "EXEC_BROKER_CONTRACT_1",
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


def _orchestrator(**overrides: object) -> dict[str, object]:
    result = {
        "orchestrator_type": "EXECUTION_SEND_ORDER_ENTRYPOINT_ORCHESTRATOR",
        "status": "SEND_ORDER_ENTRYPOINT_PASSED",
        "preview_only": True,
        "runtime_write": False,
        "queue_write": False,
        "entrypoint_called": True,
        "send_order_called": True,
        "next_stage": "BROKER_SEND_REQUIRED",
        "send_order_entrypoint_result": _entrypoint_result(),
        "issues": [],
        "warnings": [],
    }
    result.update(overrides)
    return result


def _confirmations(**overrides: object) -> dict[str, object]:
    result = {"manual_broker_dispatch_confirmed": True}
    result.update(overrides)
    return result


def _environment(**overrides: object) -> dict[str, object]:
    result = {
        "broker_dispatch_enabled": True,
        "real_broker_dispatch_enabled": True,
        "kiwoom_connected": True,
        "account_selected": True,
        "real_trade_enabled": True,
    }
    result.update(overrides)
    return result


class ExecutionBrokerDispatchOpenPolicyContractTest(unittest.TestCase):
    def test_all_valid_ready_to_open_broker_dispatch_without_calling_broker(self) -> None:
        with mock.patch("send_order_entrypoint.execute_send_order") as entrypoint:
            result = evaluate_execution_broker_dispatch_open_policy(
                _orchestrator(),
                confirmations=_confirmations(),
                environment_flags=_environment(),
            )

        self.assertEqual("READY_TO_OPEN_BROKER_DISPATCH", result["status"])
        self.assertTrue(result["broker_dispatch_allowed"])
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["broker_called"])
        self.assertFalse(result["kiwoom_called"])
        entrypoint.assert_not_called()

    def test_broker_dispatch_allowed_only_when_ready(self) -> None:
        ready = evaluate_execution_broker_dispatch_open_policy(
            _orchestrator(),
            confirmations=_confirmations(),
            environment_flags=_environment(),
        )
        blocked = evaluate_execution_broker_dispatch_open_policy(
            _orchestrator(status="BLOCKED", issues=["ENTRYPOINT_BLOCKED"]),
            confirmations=_confirmations(),
            environment_flags=_environment(),
        )
        invalid = evaluate_execution_broker_dispatch_open_policy(
            _orchestrator(status="INVALID", issues=["ENTRYPOINT_INVALID"]),
            confirmations=_confirmations(),
            environment_flags=_environment(),
        )

        self.assertTrue(ready["broker_dispatch_allowed"])
        self.assertFalse(blocked["broker_dispatch_allowed"])
        self.assertFalse(invalid["broker_dispatch_allowed"])

    def test_entrypoint_blocked_invalid_and_malformed_are_safe(self) -> None:
        cases = [
            (_orchestrator(status="BLOCKED", issues=["ENTRYPOINT_BLOCKED"]), "BLOCKED"),
            (_orchestrator(status="INVALID", issues=["ENTRYPOINT_INVALID"]), "INVALID"),
            ("malformed", "INVALID"),
        ]

        for orchestrator, expected in cases:
            with self.subTest(expected=expected):
                result = evaluate_execution_broker_dispatch_open_policy(
                    orchestrator,
                    confirmations=_confirmations(),
                    environment_flags=_environment(),
                )

                self.assertEqual(expected, result["status"])
                self.assertFalse(result["broker_dispatch_allowed"])
                self.assertFalse(result["broker_called"])
                self.assertFalse(result["kiwoom_called"])

    def test_entrypoint_payload_blockers(self) -> None:
        cases = [
            (_orchestrator(next_stage="BLOCKED"), "BROKER_DISPATCH_NEXT_STAGE_REQUIRED"),
            (_orchestrator(entrypoint_called=False), "ENTRYPOINT_CALLED_NOT_TRUE"),
            (_orchestrator(send_order_entrypoint_result=None), "SEND_ORDER_ENTRYPOINT_RESULT_REQUIRED"),
        ]

        for orchestrator, expected_issue in cases:
            with self.subTest(expected_issue=expected_issue):
                result = evaluate_execution_broker_dispatch_open_policy(
                    orchestrator,
                    confirmations=_confirmations(),
                    environment_flags=_environment(),
                )

                self.assertEqual("BLOCKED", result["status"])
                self.assertFalse(result["broker_dispatch_allowed"])
                self.assertIn(expected_issue, result["issues"])

    def test_manual_confirmation_missing_blocks(self) -> None:
        result = evaluate_execution_broker_dispatch_open_policy(
            _orchestrator(),
            confirmations={},
            environment_flags=_environment(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["broker_dispatch_allowed"])
        self.assertFalse(result["required_confirmations"]["manual_broker_dispatch_confirmed"])
        self.assertIn("MANUAL_BROKER_DISPATCH_CONFIRMATION_REQUIRED", result["issues"])

    def test_environment_false_flags_block(self) -> None:
        cases = [
            ("broker_dispatch_enabled", "BROKER_DISPATCH_ENVIRONMENT_DISABLED"),
            ("real_broker_dispatch_enabled", "REAL_BROKER_DISPATCH_ENVIRONMENT_DISABLED"),
            ("kiwoom_connected", "KIWOOM_CONNECTED_NOT_TRUE"),
            ("account_selected", "ACCOUNT_SELECTED_NOT_TRUE"),
            ("real_trade_enabled", "REAL_TRADE_ENABLED_NOT_TRUE"),
        ]

        for flag, expected_issue in cases:
            with self.subTest(flag=flag):
                result = evaluate_execution_broker_dispatch_open_policy(
                    _orchestrator(),
                    confirmations=_confirmations(),
                    environment_flags=_environment(**{flag: False}),
                )

                self.assertEqual("BLOCKED", result["status"])
                self.assertFalse(result["broker_dispatch_allowed"])
                self.assertIn(expected_issue, result["issues"])

    def test_broker_kiwoom_queue_and_runtime_commit_are_not_called(self) -> None:
        external_broker = mock.Mock()
        with (
            mock.patch("send_order_entrypoint.execute_send_order") as entrypoint,
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
            mock.patch("execution_controller.build_kiwoom_order_request") as kiwoom_request,
        ):
            result = evaluate_execution_broker_dispatch_open_policy(
                _orchestrator(),
                confirmations=_confirmations(),
                environment_flags=_environment(),
            )

        self.assertEqual("READY_TO_OPEN_BROKER_DISPATCH", result["status"])
        self.assertFalse(result["broker_called"])
        self.assertFalse(result["kiwoom_called"])
        external_broker.send_order.assert_not_called()
        entrypoint.assert_not_called()
        queue_commit.assert_not_called()
        runtime_commit.assert_not_called()
        kiwoom_request.assert_not_called()

    def test_runtime_order_queue_and_rules_are_unchanged(self) -> None:
        protected_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        protected_paths.extend((ROOT / "routines").glob("**/rules.json"))
        before = {str(path): _sha256(path) for path in protected_paths}

        evaluate_execution_broker_dispatch_open_policy(
            _orchestrator(),
            confirmations=_confirmations(),
            environment_flags=_environment(),
        )
        evaluate_execution_broker_dispatch_open_policy(
            _orchestrator(status="BLOCKED"),
            confirmations=_confirmations(),
            environment_flags=_environment(),
        )

        self.assertEqual(before, {str(path): _sha256(path) for path in protected_paths})


if __name__ == "__main__":
    unittest.main()
