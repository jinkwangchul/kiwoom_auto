# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
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


class ContractBrokerAdapter:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def send_order(self, request: dict[str, object]) -> dict[str, object]:
        self.requests.append(request)
        return {
            "broker_status": "CONTRACT_SUBMITTED",
            "broker_order_no": "CONTRACT_BRK_1",
            "order_id": request.get("order_id"),
            "request_hash": request.get("request_hash"),
        }


class ContractRaisingBrokerAdapter:
    def send_order(self, request: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("contract broker failure")


def _entrypoint_result(**overrides: object) -> dict[str, object]:
    result = {
        "send_order_executed": True,
        "entrypoint_stage": "send_order_called_mock",
        "next_stage": "SEND_ORDER_RESULT_REVIEW_REQUIRED",
        "broker": "SEND_ORDER_ENTRYPOINT_PREVIEW_BROKER",
        "order_id": "ORDER_DISPATCH_CONTRACT_1",
        "order_queued_id": "ORDER_QUEUED_DISPATCH_CONTRACT_1",
        "request_hash": "HASH_DISPATCH_CONTRACT_1",
        "lock_id": "LOCK_DISPATCH_CONTRACT_1",
        "execution_id": "EXEC_DISPATCH_CONTRACT_1",
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


def _entrypoint_orchestrator(**overrides: object) -> dict[str, object]:
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


def _policy(**overrides: object) -> dict[str, object]:
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


class ExecutionBrokerDispatchOrchestratorContractTest(unittest.TestCase):
    def test_ready_policy_and_entrypoint_pass_submit_to_injected_broker(self) -> None:
        broker = ContractBrokerAdapter()

        result = orchestrate_broker_dispatch(_policy(), _entrypoint_orchestrator(), broker)

        self.assertEqual("BROKER_DISPATCH_SUBMITTED", result["status"])
        self.assertEqual("BROKER_RESULT_REVIEW_REQUIRED", result["next_stage"])
        self.assertTrue(result["broker_dispatch_called"])
        self.assertTrue(result["send_order_called"])
        self.assertFalse(result["kiwoom_called"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertEqual("CONTRACT_SUBMITTED", result["broker_result"]["broker_status"])
        self.assertEqual(1, len(broker.requests))

    def test_broker_result_is_preserved(self) -> None:
        broker = ContractBrokerAdapter()

        result = orchestrate_broker_dispatch(_policy(), _entrypoint_orchestrator(), broker)

        expected = {
            "broker_status": "CONTRACT_SUBMITTED",
            "broker_order_no": "CONTRACT_BRK_1",
            "order_id": "ORDER_DISPATCH_CONTRACT_1",
            "request_hash": "HASH_DISPATCH_CONTRACT_1",
        }
        self.assertEqual(expected, result["broker_result"])

    def test_broker_adapter_missing_or_without_send_order_blocks(self) -> None:
        missing = orchestrate_broker_dispatch(_policy(), _entrypoint_orchestrator())
        no_send_order = orchestrate_broker_dispatch(_policy(), _entrypoint_orchestrator(), object())

        self.assertEqual("BLOCKED", missing["status"])
        self.assertIn("BROKER_ADAPTER_REQUIRED", missing["issues"])
        self.assertFalse(missing["broker_dispatch_called"])
        self.assertEqual("BLOCKED", no_send_order["status"])
        self.assertIn("BROKER_ADAPTER_SEND_ORDER_REQUIRED", no_send_order["issues"])
        self.assertFalse(no_send_order["broker_dispatch_called"])

    def test_policy_blocked_invalid_malformed_and_disallowed_do_not_call_broker(self) -> None:
        broker = ContractBrokerAdapter()
        cases = [
            (_policy(status="BLOCKED", issues=["POLICY_BLOCKED"]), "BLOCKED"),
            (_policy(status="INVALID", issues=["POLICY_INVALID"]), "INVALID"),
            ("malformed", "INVALID"),
            (_policy(broker_dispatch_allowed=False), "BLOCKED"),
        ]

        for policy, expected in cases:
            with self.subTest(expected=expected):
                result = orchestrate_broker_dispatch(policy, _entrypoint_orchestrator(), broker)

                self.assertEqual(expected, result["status"])
                self.assertFalse(result["broker_dispatch_called"])
        self.assertEqual([], broker.requests)

    def test_entrypoint_blocked_invalid_and_malformed_do_not_call_broker(self) -> None:
        broker = ContractBrokerAdapter()
        cases = [
            (_entrypoint_orchestrator(status="BLOCKED", issues=["ENTRYPOINT_BLOCKED"]), "BLOCKED"),
            (_entrypoint_orchestrator(status="INVALID", issues=["ENTRYPOINT_INVALID"]), "INVALID"),
            ("malformed", "INVALID"),
        ]

        for entrypoint, expected in cases:
            with self.subTest(expected=expected):
                result = orchestrate_broker_dispatch(_policy(), entrypoint, broker)

                self.assertEqual(expected, result["status"])
                self.assertFalse(result["broker_dispatch_called"])
        self.assertEqual([], broker.requests)

    def test_missing_entrypoint_result_and_next_stage_mismatch_block(self) -> None:
        broker = ContractBrokerAdapter()
        cases = [
            (_entrypoint_orchestrator(send_order_entrypoint_result=None), "SEND_ORDER_ENTRYPOINT_RESULT_REQUIRED"),
            (_entrypoint_orchestrator(next_stage="BLOCKED"), "BROKER_SEND_NEXT_STAGE_REQUIRED"),
        ]

        for entrypoint, expected_issue in cases:
            with self.subTest(expected_issue=expected_issue):
                result = orchestrate_broker_dispatch(_policy(), entrypoint, broker)

                self.assertEqual("BLOCKED", result["status"])
                self.assertIn(expected_issue, result["issues"])
                self.assertFalse(result["broker_dispatch_called"])
        self.assertEqual([], broker.requests)

    def test_broker_exception_blocks_without_runtime_or_queue_write(self) -> None:
        result = orchestrate_broker_dispatch(
            _policy(),
            _entrypoint_orchestrator(),
            ContractRaisingBrokerAdapter(),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertTrue(result["broker_dispatch_called"])
        self.assertFalse(result["kiwoom_called"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertTrue(any(issue.startswith("BROKER_DISPATCH_EXCEPTION") for issue in result["issues"]))

    def test_queue_runtime_and_kiwoom_are_not_called(self) -> None:
        broker = ContractBrokerAdapter()
        with (
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
            mock.patch("execution_controller.build_kiwoom_order_request") as kiwoom_request,
        ):
            result = orchestrate_broker_dispatch(_policy(), _entrypoint_orchestrator(), broker)

        self.assertEqual("BROKER_DISPATCH_SUBMITTED", result["status"])
        self.assertFalse(result["kiwoom_called"])
        queue_commit.assert_not_called()
        runtime_commit.assert_not_called()
        kiwoom_request.assert_not_called()

    def test_inputs_are_deepcopied_before_broker_mutation(self) -> None:
        class MutatingBroker:
            def send_order(self, request: dict[str, object]) -> dict[str, object]:
                request["order_id"] = "MUTATED"
                return {"broker_status": "MUTATED", "order_id": request["order_id"]}

        entrypoint = _entrypoint_orchestrator()
        original = copy.deepcopy(entrypoint)

        result = orchestrate_broker_dispatch(_policy(), entrypoint, MutatingBroker())

        self.assertEqual("BROKER_DISPATCH_SUBMITTED", result["status"])
        self.assertEqual(original, entrypoint)
        self.assertEqual("MUTATED", result["broker_result"]["order_id"])

    def test_runtime_order_queue_and_rules_are_unchanged(self) -> None:
        protected_paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        protected_paths.extend((ROOT / "routines").glob("**/rules.json"))
        before = {str(path): _sha256(path) for path in protected_paths}

        orchestrate_broker_dispatch(_policy(), _entrypoint_orchestrator(), ContractBrokerAdapter())
        orchestrate_broker_dispatch(_policy(status="BLOCKED"), _entrypoint_orchestrator(), ContractBrokerAdapter())

        self.assertEqual(before, {str(path): _sha256(path) for path in protected_paths})


if __name__ == "__main__":
    unittest.main()
