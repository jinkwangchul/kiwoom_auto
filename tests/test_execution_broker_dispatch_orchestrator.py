# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_broker_dispatch_orchestrator import orchestrate_broker_dispatch


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class MockBrokerAdapter:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def send_order(self, request: dict[str, object]) -> dict[str, object]:
        self.requests.append(request)
        return {
            "broker_status": "SUBMITTED",
            "broker_order_no": "BRK_ORDER_1",
            "order_id": request.get("order_id"),
            "request_hash": request.get("request_hash"),
        }


class RaisingBrokerAdapter:
    def send_order(self, request: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("broker unavailable")


class ExecutionBrokerDispatchOrchestratorTest(unittest.TestCase):
    def _entrypoint_result(self, **overrides: object) -> dict[str, object]:
        result = {
            "send_order_executed": True,
            "entrypoint_stage": "send_order_called_mock",
            "next_stage": "SEND_ORDER_RESULT_REVIEW_REQUIRED",
            "broker": "SEND_ORDER_ENTRYPOINT_PREVIEW_BROKER",
            "order_id": "ORDER_DISPATCH_1",
            "order_queued_id": "ORDER_QUEUED_DISPATCH_1",
            "request_hash": "HASH_DISPATCH_1",
            "lock_id": "LOCK_DISPATCH_1",
            "execution_id": "EXEC_DISPATCH_1",
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

    def _entrypoint_orchestrator(self, **overrides: object) -> dict[str, object]:
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

    def _policy(self, **overrides: object) -> dict[str, object]:
        result = {
            "policy_type": "EXECUTION_BROKER_DISPATCH_OPEN_POLICY",
            "status": "READY_TO_OPEN_BROKER_DISPATCH",
            "broker_dispatch_allowed": True,
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
            "entrypoint_called": True,
            "send_order_called": False,
            "broker_called": False,
            "kiwoom_called": False,
            "required_confirmations": {"manual_broker_dispatch_confirmed": True},
            "environment_checks": {
                "broker_dispatch_enabled": True,
                "real_broker_dispatch_enabled": True,
                "kiwoom_connected": True,
                "account_selected": True,
                "real_trade_enabled": True,
            },
            "issues": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def test_ready_policy_and_entrypoint_pass_submits_to_mock_broker(self) -> None:
        broker = MockBrokerAdapter()

        result = orchestrate_broker_dispatch(self._policy(), self._entrypoint_orchestrator(), broker)

        self.assertEqual("BROKER_DISPATCH_SUBMITTED", result["status"])
        self.assertTrue(result["broker_dispatch_called"])
        self.assertFalse(result["kiwoom_called"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertTrue(result["send_order_called"])
        self.assertEqual("BROKER_RESULT_REVIEW_REQUIRED", result["next_stage"])
        self.assertEqual("SUBMITTED", result["broker_result"]["broker_status"])
        self.assertEqual(1, len(broker.requests))
        self.assertEqual("ORDER_DISPATCH_1", broker.requests[0]["order_id"])

    def test_broker_adapter_missing_blocks(self) -> None:
        result = orchestrate_broker_dispatch(self._policy(), self._entrypoint_orchestrator())

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["broker_dispatch_called"])
        self.assertIn("BROKER_ADAPTER_REQUIRED", result["issues"])

    def test_broker_adapter_send_order_missing_blocks(self) -> None:
        result = orchestrate_broker_dispatch(self._policy(), self._entrypoint_orchestrator(), object())

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["broker_dispatch_called"])
        self.assertIn("BROKER_ADAPTER_SEND_ORDER_REQUIRED", result["issues"])

    def test_policy_blocked_invalid_and_malformed_do_not_call_broker(self) -> None:
        broker = MockBrokerAdapter()
        cases = [
            (self._policy(status="BLOCKED", issues=["policy blocked"]), "BLOCKED"),
            (self._policy(status="INVALID", issues=["policy invalid"]), "INVALID"),
            ("malformed", "INVALID"),
            (self._policy(broker_dispatch_allowed=False), "BLOCKED"),
        ]
        for policy, expected in cases:
            with self.subTest(expected=expected):
                result = orchestrate_broker_dispatch(policy, self._entrypoint_orchestrator(), broker)

                self.assertEqual(expected, result["status"])
                self.assertFalse(result["broker_dispatch_called"])
        self.assertEqual([], broker.requests)

    def test_entrypoint_blocked_invalid_and_malformed_do_not_call_broker(self) -> None:
        broker = MockBrokerAdapter()
        cases = [
            (self._entrypoint_orchestrator(status="BLOCKED", issues=["entrypoint blocked"]), "BLOCKED"),
            (self._entrypoint_orchestrator(status="INVALID", issues=["entrypoint invalid"]), "INVALID"),
            ("malformed", "INVALID"),
        ]
        for entrypoint, expected in cases:
            with self.subTest(expected=expected):
                result = orchestrate_broker_dispatch(self._policy(), entrypoint, broker)

                self.assertEqual(expected, result["status"])
                self.assertFalse(result["broker_dispatch_called"])
        self.assertEqual([], broker.requests)

    def test_missing_entrypoint_result_and_next_stage_mismatch_block(self) -> None:
        broker = MockBrokerAdapter()
        cases = [
            (self._entrypoint_orchestrator(send_order_entrypoint_result=None), "SEND_ORDER_ENTRYPOINT_RESULT_REQUIRED"),
            (self._entrypoint_orchestrator(next_stage="BLOCKED"), "BROKER_SEND_NEXT_STAGE_REQUIRED"),
        ]
        for entrypoint, issue in cases:
            with self.subTest(issue=issue):
                result = orchestrate_broker_dispatch(self._policy(), entrypoint, broker)

                self.assertEqual("BLOCKED", result["status"])
                self.assertFalse(result["broker_dispatch_called"])
                self.assertIn(issue, result["issues"])
        self.assertEqual([], broker.requests)

    def test_broker_exception_blocks_without_runtime_or_queue_write(self) -> None:
        result = orchestrate_broker_dispatch(self._policy(), self._entrypoint_orchestrator(), RaisingBrokerAdapter())

        self.assertEqual("BLOCKED", result["status"])
        self.assertTrue(result["broker_dispatch_called"])
        self.assertFalse(result["kiwoom_called"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertTrue(any(issue.startswith("BROKER_DISPATCH_EXCEPTION") for issue in result["issues"]))

    def test_queue_runtime_commit_and_kiwoom_are_not_called(self) -> None:
        broker = MockBrokerAdapter()
        with (
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
            mock.patch("execution_controller.build_kiwoom_order_request") as kiwoom_request,
        ):
            result = orchestrate_broker_dispatch(self._policy(), self._entrypoint_orchestrator(), broker)

        self.assertEqual("BROKER_DISPATCH_SUBMITTED", result["status"])
        self.assertFalse(result["kiwoom_called"])
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

        orchestrate_broker_dispatch(self._policy(), self._entrypoint_orchestrator(), MockBrokerAdapter())
        orchestrate_broker_dispatch(self._policy(status="BLOCKED"), self._entrypoint_orchestrator(), MockBrokerAdapter())

        self.assertEqual(before, {str(path): _sha256(path) for path in protected_paths})


if __name__ == "__main__":
    unittest.main()
