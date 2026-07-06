# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
import unittest
from pathlib import Path
from unittest import mock

from execution_readiness_preview_controller import (
    build_execution_readiness_preview,
    build_execution_readiness_preview_from_context,
)


class ExecutionReadinessPreviewControllerTest(unittest.TestCase):
    def _gate(self, *, gate_result: str = "OPEN") -> dict:
        return {
            "ok": gate_result == "OPEN",
            "stage": "SIGNAL_QUEUE_GATE",
            "gate_result": gate_result,
            "signal": "BUY",
            "blocked_reasons": [] if gate_result == "OPEN" else [gate_result],
        }

    def _order(self, *, status: str = "REAL_READY") -> dict:
        return {
            "id": "ORDER_1",
            "status": status,
            "source_signal_id": "SIG_1",
            "price": 85000,
            "quantity": 10,
            "order_intent": {"side": "BUY", "hoga": "\uc2dc\uc7a5\uac00"},
        }

    def _queue_preview(self, *, connected: bool = True) -> dict:
        return {
            "ok": connected,
            "stage": "SIGNAL_GATE_EXECUTION_QUEUE_BRIDGE",
            "gate_result": "OPEN" if connected else "BLOCKED",
            "order_status": "REAL_READY",
            "queue_writer_preview_connected": connected,
            "queue_write_preview_result": {
                "write_preview": connected,
                "preview_only": True,
                "no_write": True,
                "blocked_reasons": [] if connected else ["POLICY_BLOCKED"],
            },
            "runtime_write": False,
            "execution_connected": False,
            "send_order_connected": False,
        }

    def _input_context(self, *, connected: bool = True) -> dict:
        return {
            "source": "controller_test",
            "gate_result": self._gate(gate_result="OPEN" if connected else "BLOCKED"),
            "order_candidate": self._order(),
            "queue_preview_result": self._queue_preview(connected=connected),
        }

    def _preview(
        self,
        *,
        status: str = "READY",
        completed: bool = True,
        summary: str = "EXECUTION_READINESS_PREVIEW_READY",
        warnings: list[str] | None = None,
        issues: list[str] | None = None,
    ) -> dict:
        return {
            "status": status,
            "completed": completed,
            "summary": summary,
            "preview_steps": {
                "ExecutionPreviewReport": "PASS",
                "CandidateInspector": "PASS" if status == "READY" else "FAIL",
                "ReadinessSummary": "PASS" if status == "READY" else "FAIL",
                "AuditRecord": "PASS" if status == "READY" else "SKIP",
                "SnapshotPipeline": "PASS" if status == "READY" else "FAIL",
            },
            "readiness_summary": {
                "decision": "READY_FOR_EXECUTION_PREVIEW" if status == "READY" else summary,
                "checks": {
                    "Gate": "PASS" if status != "BLOCKED" else "FAIL",
                    "PreviewQueue": "PASS",
                    "PreviewReport": "PASS",
                    "CandidateInspector": "PASS" if status == "READY" else "FAIL",
                },
            },
            "snapshot_pipeline": {"status": status},
            "warnings": warnings or ["Preview mode", "Runtime write disabled"],
            "issues": issues or [],
        }

    def _formatted(self, status: str = "READY") -> dict:
        return {
            "status": status,
            "summary": f"EXECUTION_READINESS_PREVIEW_{status}",
            "text": "Execution Readiness Preview",
            "sections": {
                "Header": f"Overall Status\n{status}",
                "Pipeline": "Pipeline\nExecution Preview Report\nPASS",
                "Warnings": "Warnings\nPreview mode",
                "Issues": "Issues\nNone",
                "Checks": "Checks\nGate\nPASS",
                "Footer": "Result\nREADY_FOR_EXECUTION_PREVIEW",
            },
            "line_count": 1,
        }

    def _view_model(self, status: str = "READY") -> dict:
        return {
            "status": status,
            "title": "Execution Readiness Preview",
            "subtitle": status,
            "summary": f"EXECUTION_READINESS_PREVIEW_{status}",
            "ready": status == "READY",
            "sections": {},
            "badges": ["Ready" if status == "READY" else status.title(), "Preview"],
            "warnings": ["Preview mode"],
            "issues": [],
            "table_rows": [("Overall Status", status)],
            "footer": "Result",
        }

    def _sha256(self, path: Path) -> str | None:
        if not path.exists():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_ready_facade_builds_preview_formatter_and_view_model(self) -> None:
        result = build_execution_readiness_preview(
            self._gate(),
            self._order(),
            self._queue_preview(),
        )

        self.assertEqual("READY", result["status"])
        self.assertTrue(result["completed"])
        self.assertEqual("EXECUTION_READINESS_PREVIEW_READY", result["summary"])
        self.assertEqual("READY", result["preview_result"]["status"])
        self.assertEqual("READY", result["formatted_result"]["status"])
        self.assertEqual("READY", result["view_model"]["status"])
        self.assertTrue(result["view_model"]["ready"])
        self.assertIn("Execution Readiness Preview", result["formatted_result"]["text"])

    def test_blocked_facade_returns_blocked(self) -> None:
        result = build_execution_readiness_preview(
            self._gate(gate_result="BLOCKED"),
            self._order(),
            self._queue_preview(connected=False),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["completed"])
        self.assertEqual("EXECUTION_READINESS_PREVIEW_BLOCKED", result["summary"])
        self.assertEqual("BLOCKED", result["view_model"]["status"])

    def test_invalid_facade_returns_invalid(self) -> None:
        result = build_execution_readiness_preview(
            self._gate(),
            self._order(status="REAL_READY") | {"price": None},
            self._queue_preview(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["completed"])
        self.assertEqual("EXECUTION_READINESS_PREVIEW_INVALID", result["summary"])
        self.assertEqual("INVALID", result["formatted_result"]["status"])
        self.assertEqual("INVALID", result["view_model"]["status"])

    def test_facade_calls_internal_layers_in_order(self) -> None:
        calls: list[str] = []
        preview = self._preview()

        with (
            mock.patch(
                "execution_readiness_preview_controller.run_execution_readiness_preview",
                side_effect=lambda *args: calls.append("orchestrator") or preview,
            ),
            mock.patch(
                "execution_readiness_preview_controller.format_execution_readiness_preview",
                side_effect=lambda arg: calls.append("formatter") or self._formatted(arg["status"]),
            ),
            mock.patch(
                "execution_readiness_preview_controller.build_execution_readiness_view_model",
                side_effect=lambda arg: calls.append("adapter") or self._view_model(arg["status"]),
            ),
        ):
            result = build_execution_readiness_preview(
                self._gate(),
                self._order(),
                self._queue_preview(),
            )

        self.assertEqual(["orchestrator", "formatter", "adapter"], calls)
        self.assertEqual("READY", result["status"])
        self.assertEqual("READY", result["formatted_result"]["status"])
        self.assertEqual("READY", result["view_model"]["status"])

    def test_warnings_and_issues_are_merged_without_duplicates(self) -> None:
        preview = self._preview(issues=["POLICY_BLOCKED"], warnings=["Preview mode", "Runtime write disabled"])
        formatted = self._formatted()
        view_model = self._view_model()
        view_model["warnings"] = ["Preview mode", "Execution disabled"]
        view_model["issues"] = ["POLICY_BLOCKED", "MISSING_ORDER_PRICE"]

        with (
            mock.patch("execution_readiness_preview_controller.run_execution_readiness_preview", return_value=preview),
            mock.patch("execution_readiness_preview_controller.format_execution_readiness_preview", return_value=formatted),
            mock.patch("execution_readiness_preview_controller.build_execution_readiness_view_model", return_value=view_model),
        ):
            result = build_execution_readiness_preview(self._gate(), self._order(), self._queue_preview())

        self.assertEqual(["Preview mode", "Runtime write disabled", "Execution disabled"], result["warnings"])
        self.assertEqual(["POLICY_BLOCKED", "MISSING_ORDER_PRICE"], result["issues"])

    def test_inputs_and_nested_results_are_deepcopy_isolated(self) -> None:
        gate = self._gate()
        order = self._order()
        queue_preview = self._queue_preview()
        originals = (deepcopy(gate), deepcopy(order), deepcopy(queue_preview))

        result = build_execution_readiness_preview(gate, order, queue_preview)
        result["preview_result"]["status"] = "MUTATED"
        result["formatted_result"]["status"] = "MUTATED"
        result["view_model"]["status"] = "MUTATED"

        self.assertEqual(originals[0], gate)
        self.assertEqual(originals[1], order)
        self.assertEqual(originals[2], queue_preview)

    def test_facade_does_not_touch_gui_output_files_runtime_queue_execution_or_send_order(self) -> None:
        runtime_path = Path("runtime") / "order_queue.json"
        rules_path = Path("routines") / "\uc9c0\ud45c\ucd94\uc885\ub9e4\ub9e4" / "rules.json"
        before_runtime = self._sha256(runtime_path)
        before_rules = self._sha256(rules_path)

        with (
            mock.patch("builtins.print") as print_mock,
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("pathlib.Path.mkdir") as mkdir,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
            mock.patch("logging.Logger.info") as logger_info,
            mock.patch("execution_controller.build_execution_preview") as execution_controller,
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
        ):
            result = build_execution_readiness_preview(
                self._gate(),
                self._order(),
                self._queue_preview(),
            )

        self.assertEqual("READY", result["status"])
        print_mock.assert_not_called()
        write_text.assert_not_called()
        mkdir.assert_not_called()
        open_mock.assert_not_called()
        logger_info.assert_not_called()
        execution_controller.assert_not_called()
        send_order_stub.assert_not_called()
        self.assertEqual(before_runtime, self._sha256(runtime_path))
        self.assertEqual(before_rules, self._sha256(rules_path))

    def test_existing_facade_signature_still_accepts_three_inputs(self) -> None:
        result = build_execution_readiness_preview(
            self._gate(),
            self._order(),
            self._queue_preview(),
        )

        self.assertEqual("READY", result["status"])
        self.assertNotIn("input_builder_result", result)

    def test_context_facade_ready_calls_input_builder_then_existing_facade(self) -> None:
        calls: list[str] = []
        input_result = {
            "status": "READY",
            "summary": "INPUTS_READY",
            "gate_result": self._gate(),
            "order_candidate": self._order(),
            "queue_preview_result": self._queue_preview(),
            "metadata": {"preview_mode": True},
            "warnings": ["Input preview mode"],
            "issues": [],
        }
        facade_result = {
            "status": "READY",
            "completed": True,
            "summary": "EXECUTION_READINESS_PREVIEW_READY",
            "preview_result": {"status": "READY"},
            "formatted_result": {"status": "READY"},
            "view_model": {"status": "READY"},
            "warnings": ["Preview mode"],
            "issues": [],
        }

        with (
            mock.patch(
                "execution_readiness_preview_controller.build_execution_readiness_inputs",
                side_effect=lambda **kwargs: calls.append("input_builder") or input_result,
            ),
            mock.patch(
                "execution_readiness_preview_controller.build_execution_readiness_preview",
                side_effect=lambda *args: calls.append("facade") or facade_result,
            ) as facade,
        ):
            result = build_execution_readiness_preview_from_context(
                order_id="ORDER_1",
                preview_context=self._input_context(),
            )

        self.assertEqual(["input_builder", "facade"], calls)
        facade.assert_called_once_with(
            input_result["gate_result"],
            input_result["order_candidate"],
            input_result["queue_preview_result"],
        )
        self.assertEqual("READY", result["status"])
        self.assertTrue(result["completed"])
        self.assertEqual("INPUTS_READY", result["input_summary"])
        self.assertEqual("READY", result["input_status"])
        self.assertEqual(input_result, result["input_builder_result"])
        self.assertEqual(["Input preview mode", "Preview mode"], result["warnings"])

    def test_context_facade_blocked_by_input_builder_skips_full_preview_formatter_adapter(self) -> None:
        input_result = {
            "status": "BLOCKED",
            "summary": "INPUTS_BLOCKED",
            "gate_result": None,
            "order_candidate": self._order(),
            "queue_preview_result": None,
            "metadata": {"preview_mode": True},
            "warnings": ["Input blocked"],
            "issues": ["MISSING_GATE_RESULT"],
        }

        with (
            mock.patch("execution_readiness_preview_controller.build_execution_readiness_inputs", return_value=input_result),
            mock.patch("execution_readiness_preview_controller.run_execution_readiness_preview") as orchestrator,
            mock.patch("execution_readiness_preview_controller.format_execution_readiness_preview") as formatter,
            mock.patch("execution_readiness_preview_controller.build_execution_readiness_view_model") as adapter,
        ):
            result = build_execution_readiness_preview_from_context(
                order_id="ORDER_1",
                preview_context=self._input_context(),
            )

        orchestrator.assert_not_called()
        formatter.assert_not_called()
        adapter.assert_not_called()
        self.assertEqual("BLOCKED", result["status"])
        self.assertFalse(result["completed"])
        self.assertEqual("INPUTS_BLOCKED", result["summary"])
        self.assertIsNone(result["preview_result"])
        self.assertIsNone(result["formatted_result"])
        self.assertIsNone(result["view_model"])
        self.assertEqual(input_result, result["input_builder_result"])
        self.assertEqual(["MISSING_GATE_RESULT"], result["issues"])

    def test_context_facade_invalid_by_input_builder_skips_full_preview_formatter_adapter(self) -> None:
        input_result = {
            "status": "INVALID",
            "summary": "INPUTS_INVALID",
            "gate_result": None,
            "order_candidate": None,
            "queue_preview_result": None,
            "metadata": {"preview_mode": True},
            "warnings": [],
            "issues": ["MISSING_ORDER_ID"],
        }

        with (
            mock.patch("execution_readiness_preview_controller.build_execution_readiness_inputs", return_value=input_result),
            mock.patch("execution_readiness_preview_controller.run_execution_readiness_preview") as orchestrator,
            mock.patch("execution_readiness_preview_controller.format_execution_readiness_preview") as formatter,
            mock.patch("execution_readiness_preview_controller.build_execution_readiness_view_model") as adapter,
        ):
            result = build_execution_readiness_preview_from_context(
                order_id=None,
                preview_context=self._input_context(),
            )

        orchestrator.assert_not_called()
        formatter.assert_not_called()
        adapter.assert_not_called()
        self.assertEqual("INVALID", result["status"])
        self.assertFalse(result["completed"])
        self.assertEqual("INPUTS_INVALID", result["summary"])
        self.assertEqual(["MISSING_ORDER_ID"], result["issues"])

    def test_context_facade_propagates_existing_facade_blocked_and_invalid(self) -> None:
        for status in ("BLOCKED", "INVALID"):
            with self.subTest(status=status):
                input_result = {
                    "status": "READY",
                    "summary": "INPUTS_READY",
                    "gate_result": self._gate(),
                    "order_candidate": self._order(),
                    "queue_preview_result": self._queue_preview(),
                    "metadata": {"preview_mode": True},
                    "warnings": [],
                    "issues": [],
                }
                facade_result = {
                    "status": status,
                    "completed": False,
                    "summary": f"EXECUTION_READINESS_PREVIEW_{status}",
                    "preview_result": {"status": status},
                    "formatted_result": {"status": status},
                    "view_model": {"status": status},
                    "warnings": [],
                    "issues": [f"{status}_ISSUE"],
                }

                with (
                    mock.patch("execution_readiness_preview_controller.build_execution_readiness_inputs", return_value=input_result),
                    mock.patch("execution_readiness_preview_controller.build_execution_readiness_preview", return_value=facade_result),
                ):
                    result = build_execution_readiness_preview_from_context(
                        order_id="ORDER_1",
                        preview_context=self._input_context(),
                    )

                self.assertEqual(status, result["status"])
                self.assertFalse(result["completed"])
                self.assertEqual([f"{status}_ISSUE"], result["issues"])

    def test_context_facade_inputs_are_not_mutated(self) -> None:
        context = self._input_context()
        original_context = deepcopy(context)

        result = build_execution_readiness_preview_from_context(
            order_id="ORDER_1",
            preview_context=context,
        )

        self.assertEqual(original_context, context)
        result["input_builder_result"]["status"] = "MUTATED"
        self.assertEqual(original_context, context)

    def test_context_facade_does_not_touch_gui_output_files_runtime_queue_execution_or_send_order(self) -> None:
        runtime_path = Path("runtime") / "order_queue.json"
        rules_path = Path("routines") / "\uc9c0\ud45c\ucd94\uc885\ub9e4\ub9e4" / "rules.json"
        before_runtime = self._sha256(runtime_path)
        before_rules = self._sha256(rules_path)

        with (
            mock.patch("builtins.print") as print_mock,
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("pathlib.Path.mkdir") as mkdir,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
            mock.patch("logging.Logger.info") as logger_info,
            mock.patch("execution_controller.build_execution_preview") as execution_controller,
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
        ):
            result = build_execution_readiness_preview_from_context(
                order_id="ORDER_1",
                preview_context=self._input_context(),
            )

        self.assertEqual("READY", result["status"])
        print_mock.assert_not_called()
        write_text.assert_not_called()
        mkdir.assert_not_called()
        open_mock.assert_not_called()
        logger_info.assert_not_called()
        execution_controller.assert_not_called()
        send_order_stub.assert_not_called()
        self.assertEqual(before_runtime, self._sha256(runtime_path))
        self.assertEqual(before_rules, self._sha256(rules_path))


if __name__ == "__main__":
    unittest.main()
