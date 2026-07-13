from __future__ import annotations

from copy import deepcopy
import inspect
import unittest
from unittest import mock

import sell_execution_full_preview_orchestrator as subject
from sell_execution_full_preview_orchestrator import build_sell_execution_full_preview


def _guard() -> dict:
    return {"operator_confirmed": True, "real_trade_enabled": True, "account_no": "12345678"}


def _candidate(
    *,
    order_id: str = "ORDER_1",
    source_signal_id: str = "SIG_1",
    action_source: str = "METHOD",
) -> dict:
    return {
        "candidate_type": "SELL_REAL_READY_ORDER_CANDIDATE_PREVIEW",
        "candidate_status": "READY",
        "action_source": action_source,
        "id": order_id,
        "order_id": order_id,
        "source_signal_id": source_signal_id,
        "code": "003550",
        "symbol": "003550",
        "status": "REAL_READY",
        "execution_enabled": True,
        "side": "SELL",
        "quantity": 10,
        "price": 85000,
        "hoga": "LIMIT",
        "order_type": "SELL",
        "order_intent": {"side": "SELL", "hoga": "LIMIT", "action_source": action_source},
        "preview_only": True,
        "execution_connected": False,
        "pipeline_called": False,
        "runtime_write": False,
        "queue_write": False,
        "order_request_created": False,
        "send_order": False,
        "real_ready_state_changed": False,
        "warnings": [],
        "reasons": [],
    }


def _adapter_preview(candidates: list[dict] | None = None) -> dict:
    return {
        "preview_type": "SELL_REAL_READY_ADAPTER_PREVIEW",
        "preview_only": True,
        "execution_connected": False,
        "pipeline_called": False,
        "runtime_write": False,
        "queue_write": False,
        "file_write": False,
        "order_request_created": False,
        "send_order": False,
        "broker_api_called": False,
        "real_ready_state_changed": False,
        "queue_committed": False,
        "status": "READY",
        "order_candidates": list(candidates or [_candidate()]),
        "blocked_candidates": [],
        "summary": {},
        "warnings": ["adapter_warning"],
        "reasons": [],
    }


def _common(status: str = "READY") -> dict:
    return {
        "preview_type": "SELL_COMMON_EXECUTION_PREVIEW_ADAPTER",
        "preview_only": True,
        "status": status,
        "common_execution_ready": status == "READY",
        "candidate_results": [{"status": "READY", "action_source": "METHOD"}] if status == "READY" else [],
        "blocked_candidates": [],
        "summary": {
            "ready_candidate_count": 1 if status == "READY" else 0,
            "blocked_candidate_count": 1 if status == "BLOCKED" else 0,
            "invalid_candidate_count": 1 if status == "INVALID" else 0,
            "priority_selected": False,
            "auto_selected": False,
        },
        "warnings": ["shared_warning", "common_warning"],
        "reasons": ["shared_reason"] if status != "READY" else [],
    }


def _readiness(status: str = "READY") -> dict:
    return {
        "preview_type": "SELL_EXECUTION_READINESS_PREVIEW",
        "preview_only": True,
        "status": status,
        "readiness_ready": status == "READY",
        "ready_candidates": [{"status": "READY", "readiness_ready": True}] if status == "READY" else [],
        "candidate_readiness": [],
        "blocked_candidate_readiness": [],
        "summary": {
            "ready_candidate_count": 1 if status == "READY" else 0,
            "blocked_candidate_count": 1 if status == "BLOCKED" else 0,
            "invalid_candidate_count": 1 if status == "INVALID" else 0,
            "priority_selected": False,
            "auto_selected": False,
        },
        "warnings": ["shared_warning", "readiness_warning"],
        "reasons": ["readiness_reason"] if status != "READY" else [],
    }


def _gate(status: str = "READY") -> dict:
    return {
        "preview_type": "SELL_SIGNAL_GATE_PREVIEW",
        "preview_only": True,
        "status": status,
        "signal_gate_ready": status == "READY",
        "opened_gates": [{"status": "READY", "gate_result": "OPEN"}] if status == "READY" else [],
        "blocked_gates": [],
        "ignored_gates": [],
        "candidate_gates": [],
        "summary": {
            "opened_gate_count": 1 if status == "READY" else 0,
            "blocked_gate_count": 1 if status == "BLOCKED" else 0,
            "invalid_candidate_count": 1 if status == "INVALID" else 0,
            "priority_selected": False,
            "auto_selected": False,
        },
        "warnings": ["gate_warning"],
        "reasons": ["gate_reason"] if status != "READY" else [],
    }


def _queue(status: str = "READY") -> dict:
    return {
        "preview_type": "SELL_EXECUTION_QUEUE_PREVIEW",
        "preview_only": True,
        "status": status,
        "execution_queue_ready": status == "READY",
        "queue_ready_candidates": [{"status": "READY"}] if status == "READY" else [],
        "blocked_queue_candidates": [],
        "ignored_queue_candidates": [],
        "candidate_queue_results": [],
        "summary": {
            "queue_ready_count": 1 if status == "READY" else 0,
            "blocked_count": 1 if status == "BLOCKED" else 0,
            "invalid_count": 1 if status == "INVALID" else 0,
            "order_queued_preview_count": 1 if status == "READY" else 0,
            "priority_selected": False,
            "auto_selected": False,
            "queue_committed": False,
        },
        "warnings": ["queue_warning"],
        "reasons": ["queue_reason"] if status != "READY" else [],
    }


class SellExecutionFullPreviewOrchestratorTests(unittest.TestCase):
    def _run_with_mocks(
        self,
        *,
        common: dict | Exception | None = None,
        readiness: dict | Exception | None = None,
        gate: dict | Exception | None = None,
        queue: dict | Exception | None = None,
        guard: dict | None = None,
        existing_orders: list | None = None,
    ) -> tuple[dict, list[str]]:
        calls: list[str] = []

        def common_side_effect(*args, **kwargs):
            calls.append("common")
            if isinstance(common, Exception):
                raise common
            return _common() if common is None else common

        def readiness_side_effect(*args, **kwargs):
            calls.append("readiness")
            if isinstance(readiness, Exception):
                raise readiness
            return _readiness() if readiness is None else readiness

        def gate_side_effect(*args, **kwargs):
            calls.append("gate")
            if isinstance(gate, Exception):
                raise gate
            return _gate() if gate is None else gate

        def queue_side_effect(*args, **kwargs):
            calls.append("queue")
            if isinstance(queue, Exception):
                raise queue
            return _queue() if queue is None else queue

        with (
            mock.patch("sell_execution_full_preview_orchestrator.build_sell_common_execution_preview", side_effect=common_side_effect),
            mock.patch("sell_execution_full_preview_orchestrator.build_sell_execution_readiness_preview", side_effect=readiness_side_effect),
            mock.patch("sell_execution_full_preview_orchestrator.build_sell_signal_gate_preview", side_effect=gate_side_effect),
            mock.patch("sell_execution_full_preview_orchestrator.build_sell_execution_queue_preview", side_effect=queue_side_effect),
        ):
            result = build_sell_execution_full_preview(
                _adapter_preview(),
                guard_context=guard if guard is not None else _guard(),
                existing_orders=existing_orders,
            )
        return result, calls

    def test_all_four_steps_ready_completed(self):
        result, _ = self._run_with_mocks()

        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["completed"])
        self.assertEqual(result["preview_steps"]["ExecutionQueuePreview"], "PASS")

    def test_call_order(self):
        _, calls = self._run_with_mocks()

        self.assertEqual(calls, ["common", "readiness", "gate", "queue"])

    def test_guard_context_passed_to_common_and_queue(self):
        guard = _guard()
        with (
            mock.patch("sell_execution_full_preview_orchestrator.build_sell_common_execution_preview", return_value=_common()) as common_builder,
            mock.patch("sell_execution_full_preview_orchestrator.build_sell_execution_readiness_preview", return_value=_readiness()),
            mock.patch("sell_execution_full_preview_orchestrator.build_sell_signal_gate_preview", return_value=_gate()),
            mock.patch("sell_execution_full_preview_orchestrator.build_sell_execution_queue_preview", return_value=_queue()) as queue_builder,
        ):
            build_sell_execution_full_preview(_adapter_preview(), guard_context=guard)

        self.assertEqual(common_builder.call_args.kwargs["guard_context"], guard)
        self.assertEqual(queue_builder.call_args.kwargs["guard_context"], guard)

    def test_existing_orders_passed_to_queue(self):
        existing = [{"order_id": "ORDER_1"}]
        with (
            mock.patch("sell_execution_full_preview_orchestrator.build_sell_common_execution_preview", return_value=_common()),
            mock.patch("sell_execution_full_preview_orchestrator.build_sell_execution_readiness_preview", return_value=_readiness()),
            mock.patch("sell_execution_full_preview_orchestrator.build_sell_signal_gate_preview", return_value=_gate()),
            mock.patch("sell_execution_full_preview_orchestrator.build_sell_execution_queue_preview", return_value=_queue()) as queue_builder,
        ):
            build_sell_execution_full_preview(_adapter_preview(), guard_context=_guard(), existing_orders=existing)

        self.assertEqual(queue_builder.call_args.kwargs["existing_orders"], existing)

    def test_input_not_mutated(self):
        preview = _adapter_preview()
        original = deepcopy(preview)

        result, _ = self._run_with_mocks()
        result["adapter_preview_snapshot"]["order_candidates"][0]["id"] = "MUTATED"

        self.assertEqual(preview, original)

    def test_common_invalid_skips_later_steps(self):
        result, calls = self._run_with_mocks(common=_common("INVALID"))

        self.assertEqual(result["status"], "INVALID")
        self.assertEqual(calls, ["common"])
        self.assertEqual(result["preview_steps"]["ExecutionReadinessPreview"], "SKIP")

    def test_common_blocked_skips_later_steps(self):
        result, calls = self._run_with_mocks(common=_common("BLOCKED"))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(calls, ["common"])

    def test_common_exception_invalid(self):
        result, calls = self._run_with_mocks(common=RuntimeError("boom"))

        self.assertEqual(result["status"], "INVALID")
        self.assertEqual(calls, ["common"])
        self.assertIn("COMMON_PREVIEW_FAILED: boom", result["reasons"])

    def test_ready_stage_with_ready_flag_false_invalid(self):
        common = _common("READY")
        common["common_execution_ready"] = False

        result, _ = self._run_with_mocks(common=common)

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("CommonExecutionPreview ready flag is not True", result["reasons"])

    def test_ready_stage_with_ready_flag_missing_invalid(self):
        common = _common("READY")
        common.pop("common_execution_ready")

        result, _ = self._run_with_mocks(common=common)

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("CommonExecutionPreview ready flag is not True", result["reasons"])

    def test_readiness_invalid_skips_gate_queue(self):
        result, calls = self._run_with_mocks(readiness=_readiness("INVALID"))

        self.assertEqual(result["status"], "INVALID")
        self.assertEqual(calls, ["common", "readiness"])
        self.assertEqual(result["preview_steps"]["SignalGatePreview"], "SKIP")

    def test_readiness_blocked_skips_gate_queue(self):
        result, calls = self._run_with_mocks(readiness=_readiness("BLOCKED"))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(calls, ["common", "readiness"])

    def test_readiness_exception_invalid(self):
        result, _ = self._run_with_mocks(readiness=RuntimeError("nope"))

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("READINESS_PREVIEW_FAILED: nope", result["reasons"])

    def test_gate_invalid_skips_queue(self):
        result, calls = self._run_with_mocks(gate=_gate("INVALID"))

        self.assertEqual(result["status"], "INVALID")
        self.assertEqual(calls, ["common", "readiness", "gate"])

    def test_gate_blocked_skips_queue(self):
        result, calls = self._run_with_mocks(gate=_gate("BLOCKED"))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(calls, ["common", "readiness", "gate"])

    def test_gate_exception_invalid(self):
        result, _ = self._run_with_mocks(gate=RuntimeError("gate error"))

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("SIGNAL_GATE_PREVIEW_FAILED: gate error", result["reasons"])

    def test_queue_invalid_overall_invalid(self):
        result, _ = self._run_with_mocks(queue=_queue("INVALID"))

        self.assertEqual(result["status"], "INVALID")
        self.assertFalse(result["completed"])

    def test_queue_blocked_overall_blocked(self):
        result, _ = self._run_with_mocks(queue=_queue("BLOCKED"))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["completed"])

    def test_queue_ready_with_execution_queue_ready_false_invalid(self):
        queue = _queue("READY")
        queue["execution_queue_ready"] = False

        result, _ = self._run_with_mocks(queue=queue)

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("ExecutionQueuePreview ready flag is not True", result["reasons"])

    def test_queue_exception_invalid(self):
        result, _ = self._run_with_mocks(queue=RuntimeError("queue error"))

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("EXECUTION_QUEUE_PREVIEW_FAILED: queue error", result["reasons"])

    def test_invalid_input_type_invalid(self):
        result = build_sell_execution_full_preview(None)

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("adapter_preview must be a dict", result["reasons"])

    def test_invalid_preview_type_invalid(self):
        preview = _adapter_preview()
        preview["preview_type"] = "OTHER"

        result = build_sell_execution_full_preview(preview)

        self.assertEqual(result["status"], "INVALID")

    def test_preview_only_false_invalid(self):
        preview = _adapter_preview()
        preview["preview_only"] = False

        result = build_sell_execution_full_preview(preview)

        self.assertEqual(result["status"], "INVALID")

    def test_safety_flag_violation_invalid(self):
        preview = _adapter_preview()
        preview["queue_committed"] = True

        result = build_sell_execution_full_preview(preview)

        self.assertEqual(result["status"], "INVALID")

    def test_stage_safety_flag_true_invalid(self):
        cases = (
            ("common", _common("READY"), "runtime_write", "CommonExecutionPreview safety flag violation"),
            ("readiness", _readiness("READY"), "queue_write", "ExecutionReadinessPreview safety flag violation"),
            ("gate", _gate("READY"), "send_order", "SignalGatePreview safety flag violation"),
            ("queue", _queue("READY"), "broker_api_called", "ExecutionQueuePreview safety flag violation"),
        )

        for stage, payload, flag, reason in cases:
            with self.subTest(stage=stage):
                payload[flag] = True
                kwargs = {stage: payload}
                result, _ = self._run_with_mocks(**kwargs)

                self.assertEqual(result["status"], "INVALID")
                self.assertIn(reason, result["reasons"])

    def test_guard_context_type_error_invalid(self):
        result = build_sell_execution_full_preview(_adapter_preview(), guard_context="bad")

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("guard_context must be a dict or None", result["reasons"])

    def test_existing_orders_type_error_invalid(self):
        result = build_sell_execution_full_preview(_adapter_preview(), existing_orders={})

        self.assertEqual(result["status"], "INVALID")
        self.assertIn("existing_orders must be a list or None", result["reasons"])

    def test_preview_steps_pass_fail_skip(self):
        result, _ = self._run_with_mocks(gate=_gate("BLOCKED"))

        self.assertEqual(result["preview_steps"]["CommonExecutionPreview"], "PASS")
        self.assertEqual(result["preview_steps"]["ExecutionReadinessPreview"], "PASS")
        self.assertEqual(result["preview_steps"]["SignalGatePreview"], "FAIL")
        self.assertEqual(result["preview_steps"]["ExecutionQueuePreview"], "SKIP")

    def test_stage_results_preserved(self):
        result, _ = self._run_with_mocks()

        self.assertEqual(result["common_execution_preview"]["preview_type"], "SELL_COMMON_EXECUTION_PREVIEW_ADAPTER")
        self.assertEqual(result["execution_readiness_preview"]["preview_type"], "SELL_EXECUTION_READINESS_PREVIEW")
        self.assertEqual(result["signal_gate_preview"]["preview_type"], "SELL_SIGNAL_GATE_PREVIEW")
        self.assertEqual(result["execution_queue_preview"]["preview_type"], "SELL_EXECUTION_QUEUE_PREVIEW")

    def test_warnings_deduplicated_in_order(self):
        result, _ = self._run_with_mocks()

        self.assertEqual(result["warnings"], ["shared_warning", "common_warning", "readiness_warning", "gate_warning", "queue_warning"])

    def test_reasons_deduplicated_in_order(self):
        common = _common("READY")
        common["reasons"] = ["r1", "r2"]
        readiness = _readiness("READY")
        readiness["reasons"] = ["r2", "r3"]

        result, _ = self._run_with_mocks(common=common, readiness=readiness)

        self.assertEqual(result["reasons"], ["r1", "r2", "r3"])

    def test_summary_counts_delivered(self):
        result, _ = self._run_with_mocks()

        self.assertEqual(result["summary"]["ready_candidate_count"], 1)
        self.assertEqual(result["summary"]["opened_gate_count"], 1)
        self.assertEqual(result["summary"]["queue_ready_count"], 1)
        self.assertEqual(result["summary"]["order_queued_preview_count"], 1)

    def test_multiple_candidates_preserved_by_underlying_stages(self):
        queue = _queue()
        queue["queue_ready_candidates"] = [{"status": "READY", "id": "A"}, {"status": "READY", "id": "B"}]
        queue["summary"]["queue_ready_count"] = 2
        result, _ = self._run_with_mocks(queue=queue)

        self.assertEqual(len(result["execution_queue_preview"]["queue_ready_candidates"]), 2)
        self.assertEqual(result["summary"]["queue_ready_count"], 2)

    def test_priority_selected_false(self):
        result, _ = self._run_with_mocks()

        self.assertFalse(result["summary"]["priority_selected"])

    def test_auto_selected_false(self):
        result, _ = self._run_with_mocks()

        self.assertFalse(result["summary"]["auto_selected"])

    def test_queue_committed_false(self):
        result, _ = self._run_with_mocks()

        self.assertFalse(result["queue_committed"])
        self.assertFalse(result["summary"]["queue_committed"])

    def test_safety_flags_false(self):
        result, _ = self._run_with_mocks()

        for flag in (
            "execution_connected",
            "runtime_write",
            "queue_write",
            "file_write",
            "send_order",
            "broker_api_called",
            "real_ready_state_changed",
            "order_request_created",
            "queue_committed",
            "actual_order_sent",
        ):
            self.assertFalse(result[flag])

    def test_does_not_import_existing_full_preview_orchestrator(self):
        source = inspect.getsource(subject)

        self.assertNotIn("execution_readiness_full_preview_orchestrator", source)
        self.assertNotIn("run_execution_readiness_preview", source)

    def test_does_not_import_queue_commit(self):
        source = inspect.getsource(subject)

        self.assertNotIn("commit_execution_queue_write", source)

    def test_no_file_access(self):
        with (
            mock.patch("pathlib.Path.read_text") as read_text,
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result, _ = self._run_with_mocks()

        self.assertEqual(result["status"], "READY")
        read_text.assert_not_called()
        write_text.assert_not_called()
        open_mock.assert_not_called()

    def test_real_four_stage_minimal_integration(self):
        result = build_sell_execution_full_preview(_adapter_preview(), guard_context=_guard())

        self.assertEqual(result["preview_type"], "SELL_EXECUTION_FULL_PREVIEW")
        self.assertIn(result["status"], {"READY", "BLOCKED", "INVALID"})
        self.assertEqual(result["common_execution_preview"]["preview_type"], "SELL_COMMON_EXECUTION_PREVIEW_ADAPTER")


if __name__ == "__main__":
    unittest.main()
