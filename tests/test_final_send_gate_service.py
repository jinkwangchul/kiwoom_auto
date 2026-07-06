# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import unittest
from unittest import mock

import final_send_gate_service
from final_send_gate_service import evaluate_final_send_gate


class FinalSendGateServiceTest(unittest.TestCase):
    def _send_order_request_preview(self, **overrides: object) -> dict[str, object]:
        preview = {
            "order_id": "ORDER_1",
            "source_signal_id": "SIG_1",
            "execution_id": "EXEC_1",
            "request_hash": "HASH_1",
            "lock_id": "LOCK_1",
            "account_no": "12345678",
            "side": "BUY",
            "code": "003550",
            "quantity": 10,
            "price": 100,
            "hoga": "LIMIT",
            "original_order_no": "",
            "screen_no": "9000",
            "rqname": "SEND_ORDER_PREVIEW_ORDER_1",
        }
        preview.update(overrides)
        return preview

    def _adapter_preview(self, **overrides: object) -> dict[str, object]:
        result = {
            "adapter_preview_ok": True,
            "adapter_stage": "kiwoom_send_order_request_preview_created",
            "next_stage": "FINAL_SEND_GATE_REQUIRED",
            "preview_only": True,
            "no_send": True,
            "send_order_called": False,
            "send_order_request_preview": self._send_order_request_preview(),
            "blocked_reasons": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def _record(self, **overrides: object) -> dict[str, object]:
        record = {
            "id": "ORDER_QUEUED_ORDER_1",
            "status": "ORDER_QUEUED",
            "source": "execution_queue_pending",
            "source_signal_id": "SIG_1",
            "order_id": "ORDER_1",
            "candidate_id": "EXEC_CANDIDATE_ORDER_1",
            "queue_pending_id": "QUEUE_PENDING_EXEC_CANDIDATE_ORDER_1",
            "request_hash": "HASH_1",
            "lock_id": "LOCK_1",
            "execution_id": "EXEC_1",
            "execution_request": {},
            "queue_contract_version": "preview-1",
            "send_order_called": False,
            "execution_enabled": False,
            "blocked_reasons": [],
        }
        record.update(overrides)
        return record

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

    def _evaluate(
        self,
        adapter_preview_result: object | None = None,
        order_queued_record: object | None = None,
        current_guard: object | None = None,
        context: object | None = None,
        queue_snapshot: object | None = None,
        current_queue_snapshot: object | None = None,
    ) -> dict[str, object]:
        return evaluate_final_send_gate(
            self._adapter_preview() if adapter_preview_result is None else adapter_preview_result,
            self._record() if order_queued_record is None else order_queued_record,
            self._guard() if current_guard is None else current_guard,
            queue_snapshot=queue_snapshot,
            current_queue_snapshot=current_queue_snapshot,
            context=self._context() if context is None else context,
        )

    def test_adapter_preview_result_non_dict_is_blocked(self) -> None:
        result = self._evaluate(adapter_preview_result="invalid")

        self.assertFalse(result["final_send_gate_ok"])
        self.assertEqual("adapter_preview", result["send_gate_stage"])
        self.assertIn("adapter_preview_result must be a dict", result["blocked_reasons"])

    def test_adapter_preview_ok_false_is_blocked(self) -> None:
        result = self._evaluate(adapter_preview_result=self._adapter_preview(adapter_preview_ok=False))

        self.assertFalse(result["final_send_gate_ok"])
        self.assertIn("adapter_preview_result.adapter_preview_ok is not true", result["blocked_reasons"])

    def test_adapter_next_stage_mismatch_is_blocked(self) -> None:
        result = self._evaluate(adapter_preview_result=self._adapter_preview(next_stage="OTHER"))

        self.assertFalse(result["final_send_gate_ok"])
        self.assertIn("adapter_preview_result.next_stage is not FINAL_SEND_GATE_REQUIRED", result["blocked_reasons"])

    def test_adapter_no_send_false_is_blocked(self) -> None:
        result = self._evaluate(adapter_preview_result=self._adapter_preview(no_send=False))

        self.assertFalse(result["final_send_gate_ok"])
        self.assertIn("adapter_preview_result.no_send is not true", result["blocked_reasons"])

    def test_adapter_send_order_called_true_is_blocked(self) -> None:
        result = self._evaluate(adapter_preview_result=self._adapter_preview(send_order_called=True))

        self.assertFalse(result["final_send_gate_ok"])
        self.assertIn("adapter_preview_result.send_order_called is not false", result["blocked_reasons"])

    def test_send_order_request_preview_missing_is_blocked(self) -> None:
        result = self._evaluate(adapter_preview_result=self._adapter_preview(send_order_request_preview=None))

        self.assertFalse(result["final_send_gate_ok"])
        self.assertIn("send_order_request_preview is required", result["blocked_reasons"])

    def test_order_queued_record_non_dict_is_blocked(self) -> None:
        result = self._evaluate(order_queued_record="invalid")

        self.assertFalse(result["final_send_gate_ok"])
        self.assertEqual("record", result["send_gate_stage"])
        self.assertIn("order_queued_record must be a dict", result["blocked_reasons"])

    def test_record_status_mismatch_is_blocked(self) -> None:
        result = self._evaluate(order_queued_record=self._record(status="REAL_READY"))

        self.assertFalse(result["final_send_gate_ok"])
        self.assertIn("order_queued_record.status is not ORDER_QUEUED", result["blocked_reasons"])

    def test_record_send_order_called_true_is_blocked(self) -> None:
        result = self._evaluate(order_queued_record=self._record(send_order_called=True))

        self.assertFalse(result["final_send_gate_ok"])
        self.assertIn("order_queued_record.send_order_called is not false", result["blocked_reasons"])

    def test_record_execution_enabled_true_is_blocked(self) -> None:
        result = self._evaluate(order_queued_record=self._record(execution_enabled=True))

        self.assertFalse(result["final_send_gate_ok"])
        self.assertIn("order_queued_record.execution_enabled is not false", result["blocked_reasons"])

    def test_record_and_preview_identity_mismatch_is_blocked(self) -> None:
        mismatches = {
            "order_id": "OTHER_ORDER",
            "request_hash": "OTHER_HASH",
            "lock_id": "OTHER_LOCK",
            "execution_id": "OTHER_EXEC",
            "source_signal_id": "OTHER_SIG",
        }
        for field, value in mismatches.items():
            with self.subTest(field=field):
                record = self._record(**{field: value})
                result = self._evaluate(order_queued_record=record)

                self.assertFalse(result["final_send_gate_ok"])
                self.assertIn(
                    f"record.{field} does not match send_order_request_preview.{field}",
                    result["blocked_reasons"],
                )

    def test_guard_missing_is_blocked(self) -> None:
        result = self._evaluate(current_guard={})

        self.assertFalse(result["final_send_gate_ok"])
        self.assertIn("current_guard is required", result["blocked_reasons"])

    def test_guard_real_trade_enabled_false_is_blocked(self) -> None:
        result = self._evaluate(current_guard=self._guard(real_trade_enabled=False))

        self.assertFalse(result["final_send_gate_ok"])
        self.assertIn("current_guard.real_trade_enabled is not true", result["blocked_reasons"])

    def test_guard_kiwoom_logged_in_false_is_blocked(self) -> None:
        result = self._evaluate(current_guard=self._guard(kiwoom_logged_in=False))

        self.assertFalse(result["final_send_gate_ok"])
        self.assertIn("current_guard.kiwoom_logged_in is not true", result["blocked_reasons"])

    def test_guard_account_selected_false_is_blocked(self) -> None:
        result = self._evaluate(current_guard=self._guard(account_selected=False))

        self.assertFalse(result["final_send_gate_ok"])
        self.assertIn("current_guard.account_selected is not true", result["blocked_reasons"])

    def test_guard_account_no_missing_is_blocked(self) -> None:
        result = self._evaluate(current_guard=self._guard(account_no=""))

        self.assertFalse(result["final_send_gate_ok"])
        self.assertIn("current_guard.account_no is required", result["blocked_reasons"])

    def test_guard_operator_confirmed_false_is_blocked(self) -> None:
        result = self._evaluate(current_guard=self._guard(operator_confirmed=False))

        self.assertFalse(result["final_send_gate_ok"])
        self.assertIn("current_guard.operator_confirmed is not true", result["blocked_reasons"])

    def test_account_no_mismatch_is_blocked(self) -> None:
        result = self._evaluate(current_guard=self._guard(account_no="87654321"))

        self.assertFalse(result["final_send_gate_ok"])
        self.assertIn(
            "current_guard.account_no does not match send_order_request_preview.account_no",
            result["blocked_reasons"],
        )

    def test_manual_final_send_confirmation_missing_is_blocked(self) -> None:
        result = self._evaluate(context={})

        self.assertFalse(result["final_send_gate_ok"])
        self.assertEqual("operator_confirmation", result["send_gate_stage"])
        self.assertIn("manual final send confirmation is required", result["blocked_reasons"])

    def test_existing_operator_confirmed_alone_is_blocked(self) -> None:
        result = self._evaluate(context={"operator_confirmed": True})

        self.assertFalse(result["final_send_gate_ok"])
        self.assertIn("manual final send confirmation is required", result["blocked_reasons"])

    def test_operator_confirmed_for_final_send_allows_gate(self) -> None:
        result = self._evaluate(context={"operator_confirmed_for_final_send": True})

        self.assertTrue(result["final_send_gate_ok"])

    def test_stale_queue_snapshot_is_blocked(self) -> None:
        result = self._evaluate(
            queue_snapshot={"sha256": "AAA"},
            current_queue_snapshot={"sha256": "BBB"},
        )

        self.assertFalse(result["final_send_gate_ok"])
        self.assertEqual("stale_queue", result["send_gate_stage"])
        self.assertIn(
            "queue file changed after send order preview; rerun review and adapter preview",
            result["blocked_reasons"],
        )

    def test_adapter_blocked_reasons_are_blocked(self) -> None:
        result = self._evaluate(adapter_preview_result=self._adapter_preview(blocked_reasons=["blocked"]))

        self.assertFalse(result["final_send_gate_ok"])
        self.assertIn("adapter_preview_result.blocked_reasons is not empty", result["blocked_reasons"])

    def test_all_conditions_satisfied_approves_final_gate(self) -> None:
        result = self._evaluate(
            queue_snapshot={"sha256": "AAA"},
            current_queue_snapshot={"sha256": "AAA"},
        )

        self.assertTrue(result["final_send_gate_ok"])
        self.assertEqual("final_send_gate_approved", result["send_gate_stage"])
        self.assertEqual("SEND_ORDER_ENTRYPOINT_REQUIRED", result["next_stage"])
        self.assertTrue(result["preview_only"])
        self.assertTrue(result["no_send"])
        self.assertFalse(result["send_order_called"])
        self.assertEqual("ORDER_1", result["order_id"])
        self.assertEqual("ORDER_QUEUED_ORDER_1", result["order_queued_id"])
        self.assertEqual("HASH_1", result["request_hash"])
        self.assertEqual("LOCK_1", result["lock_id"])
        self.assertEqual("EXEC_1", result["execution_id"])
        self.assertEqual([], result["blocked_reasons"])

    def test_send_order_adapter_runtime_write_and_gui_are_not_called(self) -> None:
        with (
            mock.patch("kiwoom_order_adapter.build_kiwoom_order_request") as adapter_builder,
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = self._evaluate()

        self.assertTrue(result["final_send_gate_ok"])
        adapter_builder.assert_not_called()
        send_order_stub.assert_not_called()
        write_text.assert_not_called()
        open_mock.assert_not_called()

    def test_module_does_not_import_adapter_gui_or_timer(self) -> None:
        module_text = final_send_gate_service.__loader__.get_source(final_send_gate_service.__name__)

        self.assertNotIn("kiwoom_order_adapter", module_text)
        self.assertNotIn("QTimer", module_text)
        self.assertNotIn("QPushButton", module_text)
        self.assertNotIn("dynamicCall", module_text)

    def test_input_dicts_are_not_mutated(self) -> None:
        adapter_preview = self._adapter_preview()
        record = self._record()
        guard = self._guard()
        queue_snapshot = {"sha256": "AAA"}
        current_queue_snapshot = {"sha256": "AAA"}
        context = self._context()
        originals = (
            deepcopy(adapter_preview),
            deepcopy(record),
            deepcopy(guard),
            deepcopy(queue_snapshot),
            deepcopy(current_queue_snapshot),
            deepcopy(context),
        )

        evaluate_final_send_gate(
            adapter_preview,
            record,
            guard,
            queue_snapshot=queue_snapshot,
            current_queue_snapshot=current_queue_snapshot,
            context=context,
        )

        self.assertEqual(originals[0], adapter_preview)
        self.assertEqual(originals[1], record)
        self.assertEqual(originals[2], guard)
        self.assertEqual(originals[3], queue_snapshot)
        self.assertEqual(originals[4], current_queue_snapshot)
        self.assertEqual(originals[5], context)


if __name__ == "__main__":
    unittest.main()
