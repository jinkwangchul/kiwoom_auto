# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

from execution_final_send_gate_input_adapter import adapt_final_send_gate_readiness_to_input
from execution_final_send_gate_readiness_policy import evaluate_execution_final_send_gate_readiness


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExecutionFinalSendGateInputAdapterContractTest(unittest.TestCase):
    def _request_preview(self, **overrides: object) -> dict[str, object]:
        preview = {
            "order_id": "ORDER_INPUT_CONTRACT",
            "source_signal_id": "SIGNAL_INPUT_CONTRACT",
            "execution_id": "EXEC_INPUT_CONTRACT",
            "request_hash": "HASH_INPUT_CONTRACT",
            "lock_id": "LOCK_INPUT_CONTRACT",
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
            "id": "ORDER_QUEUED_INPUT_CONTRACT",
            "status": "ORDER_QUEUED",
            "order_id": "ORDER_INPUT_CONTRACT",
            "source_signal_id": "SIGNAL_INPUT_CONTRACT",
            "execution_id": "EXEC_INPUT_CONTRACT",
            "request_hash": "HASH_INPUT_CONTRACT",
            "lock_id": "LOCK_INPUT_CONTRACT",
            "send_order_called": False,
            "execution_enabled": False,
        }
        record.update(overrides)
        return record

    def _identity(self, **overrides: object) -> dict[str, object]:
        identity = {
            "order_id": "ORDER_INPUT_CONTRACT",
            "source_signal_id": "SIGNAL_INPUT_CONTRACT",
            "execution_id": "EXEC_INPUT_CONTRACT",
            "request_hash": "HASH_INPUT_CONTRACT",
            "lock_id": "LOCK_INPUT_CONTRACT",
        }
        identity.update(overrides)
        return identity

    def _adapter_result(self, **overrides: object) -> dict[str, object]:
        result = {
            "adapter_type": "EXECUTION_QUEUE_REVIEW_TO_SEND_ORDER_PREVIEW_ADAPTER",
            "status": "READY_FOR_FINAL_SEND_GATE",
            "preview_only": True,
            "queue_write": False,
            "runtime_write": False,
            "send_order_called": False,
            "final_send_gate_called": False,
            "adapter_preview_result": self._adapter_preview(),
            "order_queued_record": self._record(),
            "identity": self._identity(),
            "issues": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

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

    def _readiness(self, adapter_result: object | None = None) -> dict[str, object]:
        return evaluate_execution_final_send_gate_readiness(
            self._adapter_result() if adapter_result is None else adapter_result,
            self._guard(),
            self._context(),
        )

    def _adapt(
        self,
        readiness: object | None = None,
        guard: object | None = None,
        context: object | None = None,
    ) -> dict[str, object]:
        return adapt_final_send_gate_readiness_to_input(
            self._readiness() if readiness is None else readiness,
            self._guard() if guard is None else guard,
            self._context() if context is None else context,
        )

    def test_ready_readiness_builds_ready_payload(self) -> None:
        result = self._adapt()

        self.assertEqual("READY_FOR_FINAL_SEND_GATE", result["status"])
        self.assertIsInstance(result["final_send_gate_input"], dict)
        self.assertTrue(result["preview_only"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["final_send_gate_called"])

    def test_blocked_invalid_and_malformed_inputs(self) -> None:
        blocked = self._adapt(readiness={"status": "BLOCKED", "issues": ["blocked"]})
        invalid = self._adapt(readiness={"status": "INVALID", "issues": ["invalid"]})
        malformed = self._adapt(readiness="malformed")

        self.assertEqual("BLOCKED", blocked["status"])
        self.assertEqual("INVALID", invalid["status"])
        self.assertEqual("INVALID", malformed["status"])
        self.assertIsNone(blocked["final_send_gate_input"])
        self.assertIsNone(invalid["final_send_gate_input"])
        self.assertIsNone(malformed["final_send_gate_input"])

    def test_final_send_gate_input_preserves_all_required_payloads(self) -> None:
        readiness = self._readiness()
        guard = self._guard()
        context = self._context()
        result = adapt_final_send_gate_readiness_to_input(readiness, guard, context)
        final_input = result["final_send_gate_input"]

        self.assertEqual(readiness["adapter_preview_result"], final_input["adapter_preview_result"])
        self.assertEqual(
            readiness["adapter_preview_result"]["send_order_request_preview"],
            final_input["send_order_request_preview"],
        )
        self.assertEqual(readiness["order_queued_record"], final_input["order_queued_record"])
        self.assertEqual(readiness["identity"], final_input["identity"])
        self.assertEqual(guard, final_input["current_guard"])
        self.assertEqual(context, final_input["context"])
        self.assertEqual(readiness["identity"], result["identity"])

    def test_readiness_policy_preserves_original_payload_fields(self) -> None:
        readiness = self._readiness()

        self.assertEqual(self._adapter_preview(), readiness["adapter_preview_result"])
        self.assertEqual(self._record(), readiness["order_queued_record"])
        self.assertEqual(self._identity(), readiness["identity"])

    def test_deepcopy_independence(self) -> None:
        readiness = self._readiness()
        guard = self._guard()
        context = self._context()
        originals = (deepcopy(readiness), deepcopy(guard), deepcopy(context))
        result = adapt_final_send_gate_readiness_to_input(readiness, guard, context)

        final_input = result["final_send_gate_input"]
        final_input["adapter_preview_result"]["send_order_request_preview"]["order_id"] = "MUTATED"
        final_input["send_order_request_preview"]["order_id"] = "MUTATED"
        final_input["order_queued_record"]["order_id"] = "MUTATED"
        final_input["identity"]["order_id"] = "MUTATED"
        final_input["current_guard"]["account_no"] = "MUTATED"
        final_input["context"]["manual_final_send_confirmed"] = False
        result["identity"]["order_id"] = "MUTATED"

        self.assertEqual(originals[0], readiness)
        self.assertEqual(originals[1], guard)
        self.assertEqual(originals[2], context)

    def test_missing_payloads_block(self) -> None:
        readiness = self._readiness()
        cases = [
            ("adapter_preview_result", None, "ADAPTER_PREVIEW_RESULT_REQUIRED"),
            ("order_queued_record", None, "ORDER_QUEUED_RECORD_REQUIRED"),
            ("identity", {}, "MISSING_ORDER_ID"),
        ]
        for field, value, issue in cases:
            with self.subTest(field=field):
                broken = deepcopy(readiness)
                broken[field] = value
                result = self._adapt(readiness=broken)

                self.assertEqual("BLOCKED", result["status"])
                self.assertIn(issue, result["issues"])

    def test_no_final_send_gate_send_order_queue_or_runtime_commit_calls(self) -> None:
        with (
            mock.patch("final_send_gate_service.evaluate_final_send_gate") as final_gate,
            mock.patch("send_order_entrypoint.execute_send_order") as send_order,
            mock.patch("execution_queue_commit_service.commit_execution_queue_manually") as queue_commit,
            mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as runtime_commit,
        ):
            ready = self._adapt()
            blocked = self._adapt(readiness={"status": "BLOCKED", "issues": ["blocked"]})

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

        self._adapt()
        self._adapt(readiness={"status": "BLOCKED", "issues": ["blocked"]})

        self.assertEqual(before_runtime, {str(path): _sha256(path) for path in runtime_paths})
        self.assertEqual(before_rules, {str(path): _sha256(path) for path in rules_paths})


if __name__ == "__main__":
    unittest.main()
