# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import hashlib
import unittest
from pathlib import Path
from unittest import mock

from execution_readiness_input_builder import build_execution_readiness_inputs


class ExecutionReadinessInputBuilderTest(unittest.TestCase):
    def _gate(self, *, gate_result: str = "OPEN") -> dict:
        return {
            "ok": gate_result == "OPEN",
            "stage": "SIGNAL_QUEUE_GATE",
            "gate_result": gate_result,
            "candidate_result": "READY",
            "signal": "BUY",
            "decision": "ACCEPT",
            "policy_result": "PASS",
            "queue_connected": False,
            "runtime_write": False,
            "execution_connected": False,
            "send_order_connected": False,
        }

    def _order(self, *, status: str = "REAL_READY") -> dict:
        return {
            "id": "ORDER_1",
            "status": status,
            "source_signal_id": "SIG_1",
            "code": "003550",
            "side": "BUY",
            "quantity": 10,
            "price": 85000,
            "execution_enabled": True,
            "order_intent": {"side": "BUY", "hoga": "\uc2dc\uc7a5\uac00"},
        }

    def _queue_preview(self) -> dict:
        return {
            "ok": True,
            "stage": "SIGNAL_GATE_EXECUTION_QUEUE_BRIDGE",
            "bridge_result": "QUEUE_WRITER_PREVIEW_READY",
            "gate_result": "OPEN",
            "order_status": "REAL_READY",
            "queue_writer_preview_connected": True,
            "queue_write_preview_result": {
                "write_preview": True,
                "preview_only": True,
                "no_write": True,
                "blocked_reasons": [],
            },
            "queue_connected": False,
            "runtime_write": False,
            "execution_connected": False,
            "send_order_connected": False,
        }

    def _context(self, **overrides) -> dict:
        context = {
            "source": "unit_test",
            "gate_result": self._gate(),
            "order_candidate": self._order(),
            "queue_preview_result": self._queue_preview(),
        }
        context.update(overrides)
        return context

    def _writer_preview(self, *, write_preview: bool = True) -> dict:
        return {
            "queue_write_preview_result": {
                "write_preview": write_preview,
                "preview_only": True,
                "no_write": True,
                "blocked_reasons": [] if write_preview else ["queue pending blocked"],
                "order_queued_record_preview": {"status": "ORDER_QUEUED"} if write_preview else None,
            }
        }

    def _legacy_preview(
        self,
        *,
        ok: bool = True,
        order: dict[str, object] | None = None,
        write_preview: bool = True,
        blocked_reasons: list[str] | None = None,
    ) -> dict:
        order_dict = order if order is not None else self._order()
        return {
            "ok": ok,
            "stage": "REAL_READY_ORDER_EXECUTION_PREVIEW",
            "read_result": {
                "ok": ok,
                "order": order_dict,
                "blocked_reasons": [] if ok else (blocked_reasons or ["legacy blocked"]),
            },
            "preview_result": {
                "summary": {
                    "ok": ok,
                    "order_id": order_dict.get("id"),
                    "blocked_reasons": [] if ok else (blocked_reasons or ["legacy blocked"]),
                },
                "queue_write_preview_result": {
                    "write_preview": write_preview,
                    "preview_only": True,
                    "no_write": True,
                    "blocked_reasons": [] if write_preview else ["queue preview unavailable"],
                    "order_queued_record_preview": {"status": "ORDER_QUEUED"} if write_preview else None,
                },
            },
        }

    def _legacy_context(self, **overrides) -> dict:
        context = {
            "source": "gui_execution_preview_button",
            "guard": {"operator_confirmed": True, "real_trade_enabled": True},
            "legacy_execution_preview_result": self._legacy_preview(),
        }
        context.update(overrides)
        return context

    def _sha256(self, path: Path) -> str | None:
        if not path.exists():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_ready_with_prebuilt_inputs(self) -> None:
        context = self._context()

        result = build_execution_readiness_inputs(order_id="ORDER_1", preview_context=context)

        self.assertEqual("READY", result["status"])
        self.assertEqual("INPUTS_READY", result["summary"])
        self.assertEqual(context["gate_result"], result["gate_result"])
        self.assertEqual(context["order_candidate"], result["order_candidate"])
        self.assertEqual(context["queue_preview_result"], result["queue_preview_result"])
        self.assertEqual([], result["issues"])

    def test_ready_builds_queue_preview_from_gate_and_order(self) -> None:
        builder = mock.Mock(return_value=self._writer_preview())
        context = {
            "source": "unit_test",
            "gate_result": self._gate(),
            "order_candidate": self._order(),
            "guard": {"operator_confirmed": True},
            "execution_preview_builder": builder,
        }

        result = build_execution_readiness_inputs(order_id="ORDER_1", preview_context=context)

        builder.assert_called_once()
        self.assertEqual("READY", result["status"])
        self.assertEqual("INPUTS_READY", result["summary"])
        self.assertEqual("SIGNAL_GATE_EXECUTION_QUEUE_BRIDGE", result["queue_preview_result"]["stage"])
        self.assertTrue(result["queue_preview_result"]["queue_writer_preview_connected"])

    def test_blocked_when_gate_is_missing_but_order_exists(self) -> None:
        result = build_execution_readiness_inputs(
            order_id="ORDER_1",
            preview_context={"source": "unit_test", "order_candidate": self._order()},
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("INPUTS_BLOCKED", result["summary"])
        self.assertEqual(["MISSING_GATE_RESULT"], result["issues"])
        self.assertIsNone(result["gate_result"])
        self.assertIsNotNone(result["order_candidate"])

    def test_blocked_when_queue_preview_cannot_be_created(self) -> None:
        context = {
            "source": "unit_test",
            "gate_result": self._gate(),
            "order_candidate": self._order(),
        }

        result = build_execution_readiness_inputs(order_id="ORDER_1", preview_context=context)

        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual(["MISSING_QUEUE_PREVIEW_RESULT"], result["issues"])

    def test_blocked_when_bridge_blocks_queue_preview(self) -> None:
        builder = mock.Mock(return_value=self._writer_preview(write_preview=False))
        context = {
            "source": "unit_test",
            "gate_result": self._gate(),
            "order_candidate": self._order(),
            "execution_preview_builder": builder,
        }

        result = build_execution_readiness_inputs(order_id="ORDER_1", preview_context=context)

        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("queue pending blocked", result["issues"][0])
        self.assertEqual("BLOCKED", result["queue_preview_result"]["bridge_result"])

    def test_invalid_without_order_id(self) -> None:
        result = build_execution_readiness_inputs(preview_context=self._context())

        self.assertEqual("INVALID", result["status"])
        self.assertEqual("INPUTS_INVALID", result["summary"])
        self.assertEqual(["MISSING_ORDER_ID"], result["issues"])

    def test_invalid_without_preview_context(self) -> None:
        result = build_execution_readiness_inputs(order_id="ORDER_1", preview_context=None)

        self.assertEqual("INVALID", result["status"])
        self.assertEqual(["MISSING_PREVIEW_CONTEXT"], result["issues"])

    def test_invalid_when_order_candidate_missing(self) -> None:
        result = build_execution_readiness_inputs(
            order_id="ORDER_1",
            preview_context={"source": "unit_test", "gate_result": self._gate()},
        )

        self.assertEqual("INVALID", result["status"])
        self.assertEqual(["MISSING_ORDER_CANDIDATE"], result["issues"])

    def test_invalid_when_order_id_mismatches_candidate(self) -> None:
        result = build_execution_readiness_inputs(order_id="OTHER", preview_context=self._context())

        self.assertEqual("INVALID", result["status"])
        self.assertEqual(["ORDER_ID_MISMATCH"], result["issues"])
        self.assertEqual("ORDER_1", result["order_candidate"]["id"])

    def test_metadata_is_created(self) -> None:
        result = build_execution_readiness_inputs(order_id="ORDER_1", preview_context=self._context())
        metadata = result["metadata"]

        self.assertEqual("unit_test", metadata["source"])
        self.assertTrue(metadata["preview_mode"])
        self.assertEqual(1, metadata["builder_version"])
        self.assertEqual("EXECUTION_READINESS_PREVIEW", metadata["project_phase"])
        self.assertEqual("preview_context", metadata["input_type"])
        self.assertEqual("ORDER_1", metadata["order_id"])

    def test_legacy_preview_ready_builds_readiness_inputs(self) -> None:
        result = build_execution_readiness_inputs(
            order_id="ORDER_1",
            preview_context=self._legacy_context(),
        )

        self.assertEqual("READY", result["status"])
        self.assertEqual("INPUTS_READY_FROM_LEGACY_PREVIEW", result["summary"])
        self.assertEqual("SIGNAL_QUEUE_GATE", result["gate_result"]["stage"])
        self.assertEqual("OPEN", result["gate_result"]["gate_result"])
        self.assertEqual("legacy_execution_preview_result", result["gate_result"]["source"])
        self.assertEqual("ORDER_1", result["order_candidate"]["id"])
        self.assertEqual("REAL_READY", result["order_candidate"]["status"])
        self.assertEqual("BUY", result["order_candidate"]["signal"])
        self.assertEqual(85000, result["order_candidate"]["price"])
        self.assertEqual(10, result["order_candidate"]["quantity"])
        self.assertEqual("SIGNAL_GATE_EXECUTION_QUEUE_BRIDGE", result["queue_preview_result"]["stage"])
        self.assertTrue(result["queue_preview_result"]["queue_writer_preview_connected"])
        self.assertFalse(result["queue_preview_result"]["runtime_write"])
        self.assertEqual([], result["issues"])
        self.assertIn("Legacy preview adapter", result["warnings"])

    def test_legacy_preview_metadata_is_created(self) -> None:
        result = build_execution_readiness_inputs(
            order_id="ORDER_1",
            preview_context=self._legacy_context(),
        )
        metadata = result["metadata"]

        self.assertEqual("legacy_execution_preview_result", metadata["source"])
        self.assertTrue(metadata["preview_mode"])
        self.assertEqual(1, metadata["builder_version"])
        self.assertEqual("EXECUTION_READINESS_PREVIEW", metadata["project_phase"])
        self.assertEqual("legacy_execution_preview_result", metadata["input_type"])
        self.assertEqual("ORDER_1", metadata["order_id"])
        self.assertEqual("READY", metadata["legacy_status"])
        self.assertEqual("REAL_READY_ORDER_EXECUTION_PREVIEW", metadata["legacy_summary"])

    def test_legacy_preview_blocked_when_not_real_ready(self) -> None:
        order = self._order(status="APPROVED")
        result = build_execution_readiness_inputs(
            order_id="ORDER_1",
            preview_context=self._legacy_context(
                legacy_execution_preview_result=self._legacy_preview(order=order)
            ),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("INPUTS_BLOCKED_FROM_LEGACY_PREVIEW", result["summary"])
        self.assertEqual("BLOCKED", result["gate_result"]["gate_result"])
        self.assertEqual(["LEGACY_RESULT_NOT_READY"], result["issues"])

    def test_legacy_preview_blocked_when_queue_preview_unavailable(self) -> None:
        result = build_execution_readiness_inputs(
            order_id="ORDER_1",
            preview_context=self._legacy_context(
                legacy_execution_preview_result=self._legacy_preview(write_preview=False)
            ),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("INPUTS_BLOCKED_FROM_LEGACY_PREVIEW", result["summary"])
        self.assertEqual(["LEGACY_QUEUE_PREVIEW_UNAVAILABLE"], result["issues"])
        self.assertFalse(result["queue_preview_result"]["queue_writer_preview_connected"])

    def test_legacy_preview_blocked_when_legacy_result_not_ready(self) -> None:
        result = build_execution_readiness_inputs(
            order_id="ORDER_1",
            preview_context=self._legacy_context(
                legacy_execution_preview_result=self._legacy_preview(
                    ok=False,
                    blocked_reasons=["legacy policy blocked"],
                )
            ),
        )

        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("INPUTS_BLOCKED_FROM_LEGACY_PREVIEW", result["summary"])
        self.assertEqual(["legacy policy blocked"], result["issues"])

    def test_legacy_preview_invalid_when_missing_legacy_result(self) -> None:
        result = build_execution_readiness_inputs(
            order_id="ORDER_1",
            preview_context={"source": "gui_execution_preview_button", "guard": {}},
        )

        self.assertEqual("INVALID", result["status"])
        self.assertEqual("INPUTS_INVALID_FROM_LEGACY_PREVIEW", result["summary"])
        self.assertEqual(["MISSING_LEGACY_PREVIEW_RESULT"], result["issues"])

    def test_legacy_preview_invalid_when_order_id_mismatch(self) -> None:
        result = build_execution_readiness_inputs(
            order_id="OTHER_ORDER",
            preview_context=self._legacy_context(),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertEqual("INPUTS_INVALID_FROM_LEGACY_PREVIEW", result["summary"])
        self.assertEqual(["ORDER_ID_MISMATCH"], result["issues"])

    def test_legacy_preview_invalid_when_required_order_fields_missing(self) -> None:
        order = self._order()
        order.pop("price")
        order.pop("quantity")
        order["side"] = ""
        order["order_intent"] = {}

        result = build_execution_readiness_inputs(
            order_id="ORDER_1",
            preview_context=self._legacy_context(
                legacy_execution_preview_result=self._legacy_preview(order=order)
            ),
        )

        self.assertEqual("INVALID", result["status"])
        self.assertEqual("INPUTS_INVALID_FROM_LEGACY_PREVIEW", result["summary"])
        self.assertIn("MISSING_ORDER_PRICE", result["issues"])
        self.assertIn("MISSING_ORDER_QTY", result["issues"])
        self.assertIn("INVALID_ORDER_TYPE", result["issues"])
        self.assertIn("INVALID_HOGA", result["issues"])

    def test_legacy_preview_invalid_when_legacy_shape_is_unknown(self) -> None:
        result = build_execution_readiness_inputs(
            order_id="ORDER_1",
            preview_context={
                "source": "gui_execution_preview_button",
                "legacy_execution_preview_result": {"ok": True, "unexpected": True},
            },
        )

        self.assertEqual("INVALID", result["status"])
        self.assertEqual(["INVALID_LEGACY_RESULT"], result["issues"])

    def test_legacy_preview_context_is_not_mutated(self) -> None:
        context = self._legacy_context()
        original = deepcopy(context)

        build_execution_readiness_inputs(order_id="ORDER_1", preview_context=context)

        self.assertEqual(original, context)

    def test_input_context_is_not_mutated(self) -> None:
        context = self._context()
        original = deepcopy(context)

        build_execution_readiness_inputs(order_id="ORDER_1", preview_context=context)

        self.assertEqual(original, context)

    def test_result_is_deepcopy_isolated_from_input(self) -> None:
        context = self._context()

        result = build_execution_readiness_inputs(order_id="ORDER_1", preview_context=context)
        result["gate_result"]["gate_result"] = "MUTATED"
        result["order_candidate"]["id"] = "MUTATED"
        result["queue_preview_result"]["stage"] = "MUTATED"

        self.assertEqual("OPEN", context["gate_result"]["gate_result"])
        self.assertEqual("ORDER_1", context["order_candidate"]["id"])
        self.assertEqual("SIGNAL_GATE_EXECUTION_QUEUE_BRIDGE", context["queue_preview_result"]["stage"])

    def test_builder_does_not_touch_gui_output_files_runtime_queue_execution_or_send_order(self) -> None:
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
            result = build_execution_readiness_inputs(
                order_id="ORDER_1",
                preview_context=self._context(),
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
