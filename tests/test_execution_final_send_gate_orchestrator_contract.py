# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_final_send_gate_orchestrator import orchestrate_final_send_gate_preview


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionFinalSendGateOrchestratorContractTest(unittest.TestCase):
    def _request_preview(self, **overrides: object) -> dict[str, object]:
        preview = {
            "order_id": "ORDER_ORCH_CONTRACT",
            "source_signal_id": "SIGNAL_ORCH_CONTRACT",
            "execution_id": "EXEC_ORCH_CONTRACT",
            "request_hash": "HASH_ORCH_CONTRACT",
            "lock_id": "LOCK_ORCH_CONTRACT",
            "account_no": "12345678",
            "side": "BUY",
            "code": "003550",
            "quantity": 10,
            "price": 100,
            "hoga": "LIMIT",
        }
        preview.update(overrides)
        return preview

    def _adapter_preview(self, **overrides: object) -> dict[str, object]:
        preview = {
            "adapter_preview_ok": True,
            "adapter_stage": "kiwoom_send_order_request_preview_created",
            "next_stage": "FINAL_SEND_GATE_REQUIRED",
            "preview_only": True,
            "no_send": True,
            "send_order_called": False,
            "send_order_request_preview": self._request_preview(),
            "blocked_reasons": [],
            "warnings": [],
        }
        preview.update(overrides)
        return preview

    def _record(self, **overrides: object) -> dict[str, object]:
        record = {
            "id": "ORDER_QUEUED_ORCH_CONTRACT",
            "status": "ORDER_QUEUED",
            "order_id": "ORDER_ORCH_CONTRACT",
            "source_signal_id": "SIGNAL_ORCH_CONTRACT",
            "execution_id": "EXEC_ORCH_CONTRACT",
            "request_hash": "HASH_ORCH_CONTRACT",
            "lock_id": "LOCK_ORCH_CONTRACT",
            "send_order_called": False,
            "execution_enabled": False,
        }
        record.update(overrides)
        return record

    def _identity(self, **overrides: object) -> dict[str, object]:
        identity = {
            "order_id": "ORDER_ORCH_CONTRACT",
            "source_signal_id": "SIGNAL_ORCH_CONTRACT",
            "execution_id": "EXEC_ORCH_CONTRACT",
            "request_hash": "HASH_ORCH_CONTRACT",
            "lock_id": "LOCK_ORCH_CONTRACT",
        }
        identity.update(overrides)
        return identity

    def _guard(self, **overrides: object) -> dict[str, object]:
        guard = {
            "real_trade_enabled": True,
            "kiwoom_logged_in": True,
            "account_selected": True,
            "account_no": "12345678",
            "operator_confirmed": True,
        }
        guard.update(overrides)
        return guard

    def _context(self, **overrides: object) -> dict[str, object]:
        context = {"manual_final_send_confirmed": True}
        context.update(overrides)
        return context

    def _final_input(self, **overrides: object) -> dict[str, object]:
        final_input = {
            "adapter_preview_result": self._adapter_preview(),
            "send_order_request_preview": self._request_preview(),
            "order_queued_record": self._record(),
            "identity": self._identity(),
            "current_guard": self._guard(),
            "context": self._context(),
        }
        final_input.update(overrides)
        return final_input

    def _adapter_result(self, **overrides: object) -> dict[str, object]:
        result = {
            "adapter_type": "EXECUTION_FINAL_SEND_GATE_INPUT_ADAPTER",
            "status": "READY_FOR_FINAL_SEND_GATE",
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
            "send_order_called": False,
            "final_send_gate_called": False,
            "final_send_gate_input": self._final_input(),
            "identity": self._identity(),
            "issues": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def assert_preview_boundary(self, result: dict[str, object]) -> None:
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["final_send_gate_called"])

    def test_adapter_ready_yields_ready_and_next_stage(self) -> None:
        result = orchestrate_final_send_gate_preview(self._adapter_result())

        self.assertEqual("READY_FOR_FINAL_SEND_GATE", result["status"])
        self.assertTrue(result["final_send_gate_ready"])
        self.assertEqual("FINAL_SEND_GATE_SERVICE_REQUIRED", result["next_stage"])
        self.assert_preview_boundary(result)

    def test_final_send_gate_ready_only_when_ready(self) -> None:
        cases = [
            self._adapter_result(status="BLOCKED", issues=["blocked"]),
            self._adapter_result(status="INVALID", issues=["invalid"]),
            self._adapter_result(final_send_gate_input=None),
            self._adapter_result(issues=["SHOULD_BLOCK"]),
        ]
        for adapter_result in cases:
            with self.subTest(adapter_result=adapter_result):
                result = orchestrate_final_send_gate_preview(adapter_result)

                self.assertNotEqual("READY_FOR_FINAL_SEND_GATE", result["status"])
                self.assertFalse(result["final_send_gate_ready"])
                self.assert_preview_boundary(result)

    def test_adapter_blocked_invalid_and_malformed(self) -> None:
        blocked = orchestrate_final_send_gate_preview(self._adapter_result(status="BLOCKED", issues=["blocked"]))
        invalid = orchestrate_final_send_gate_preview(self._adapter_result(status="INVALID", issues=["invalid"]))
        malformed = orchestrate_final_send_gate_preview("malformed")

        self.assertEqual("BLOCKED", blocked["status"])
        self.assertEqual("INVALID", invalid["status"])
        self.assertEqual("INVALID", malformed["status"])
        self.assertIn("MALFORMED_FINAL_SEND_GATE_INPUT_ADAPTER_RESULT", malformed["issues"])

    def test_payloads_are_preserved(self) -> None:
        adapter_result = self._adapter_result()
        result = orchestrate_final_send_gate_preview(adapter_result)
        final_input = result["final_send_gate_input"]

        self.assertEqual(adapter_result["final_send_gate_input"], final_input)
        self.assertEqual(adapter_result["final_send_gate_input"]["adapter_preview_result"], final_input["adapter_preview_result"])
        self.assertEqual(adapter_result["final_send_gate_input"]["send_order_request_preview"], final_input["send_order_request_preview"])
        self.assertEqual(adapter_result["final_send_gate_input"]["order_queued_record"], final_input["order_queued_record"])
        self.assertEqual(adapter_result["final_send_gate_input"]["identity"], final_input["identity"])
        self.assertEqual(adapter_result["final_send_gate_input"]["current_guard"], final_input["current_guard"])
        self.assertEqual(adapter_result["final_send_gate_input"]["context"], final_input["context"])
        self.assertEqual(adapter_result["identity"], result["identity"])

    def test_missing_required_payload_blocks(self) -> None:
        cases = [
            ("adapter_preview_result", "ADAPTER_PREVIEW_RESULT_REQUIRED"),
            ("send_order_request_preview", "SEND_ORDER_REQUEST_PREVIEW_REQUIRED"),
            ("order_queued_record", "ORDER_QUEUED_RECORD_REQUIRED"),
            ("identity", "IDENTITY_REQUIRED"),
            ("current_guard", "CURRENT_GUARD_REQUIRED"),
            ("context", "CONTEXT_REQUIRED"),
        ]
        for field, issue in cases:
            with self.subTest(field=field):
                final_input = self._final_input()
                final_input[field] = None
                result = orchestrate_final_send_gate_preview(self._adapter_result(final_send_gate_input=final_input))

                self.assertEqual("BLOCKED", result["status"])
                self.assertFalse(result["final_send_gate_ready"])
                self.assertIn(issue, result["issues"])

    def test_ready_with_issues_blocks(self) -> None:
        result = orchestrate_final_send_gate_preview(self._adapter_result(issues=["SHOULD_BLOCK"]))

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["final_send_gate_ready"])
        self.assertIn("SHOULD_BLOCK", result["issues"])

    def test_deepcopy_independence(self) -> None:
        adapter_result = self._adapter_result()
        original = deepcopy(adapter_result)
        result = orchestrate_final_send_gate_preview(adapter_result)

        result["final_send_gate_input"]["adapter_preview_result"]["send_order_request_preview"]["order_id"] = "MUTATED"
        result["final_send_gate_input"]["send_order_request_preview"]["order_id"] = "MUTATED"
        result["final_send_gate_input"]["order_queued_record"]["order_id"] = "MUTATED"
        result["final_send_gate_input"]["identity"]["order_id"] = "MUTATED"
        result["final_send_gate_input"]["current_guard"]["account_no"] = "MUTATED"
        result["final_send_gate_input"]["context"]["manual_final_send_confirmed"] = False
        result["identity"]["order_id"] = "MUTATED"

        self.assertEqual(original, adapter_result)

    def test_no_final_send_gate_send_order_queue_or_runtime_commit_calls(self) -> None:
        with (
            mock.patch("final_send_gate_service.evaluate_final_send_gate") as final_gate,
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
        ):
            ready = orchestrate_final_send_gate_preview(self._adapter_result())
            blocked = orchestrate_final_send_gate_preview(self._adapter_result(status="BLOCKED"))

        self.assertEqual("READY_FOR_FINAL_SEND_GATE", ready["status"])
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

        orchestrate_final_send_gate_preview(self._adapter_result())
        orchestrate_final_send_gate_preview(self._adapter_result(status="BLOCKED"))

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})


if __name__ == "__main__":
    unittest.main()
