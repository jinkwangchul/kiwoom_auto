# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import unittest
from unittest import mock

import send_order_entrypoint
from send_order_entrypoint import execute_send_order


class MockBrokerAdapter:
    broker_name = "MOCK_BROKER"

    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def send_order(self, request: dict[str, object]) -> dict[str, object]:
        self.requests.append(request)
        return {
            "broker_status": "MOCK_ACCEPTED",
            "request_hash": request.get("request_hash"),
        }


class RaisingBrokerAdapter:
    broker_name = "MOCK_BROKER"

    def send_order(self, request: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("mock broker failure")


class SendOrderEntrypointTest(unittest.TestCase):
    def _final_gate(self, **overrides: object) -> dict[str, object]:
        result = {
            "final_send_gate_ok": True,
            "send_gate_stage": "final_send_gate_approved",
            "next_stage": "SEND_ORDER_ENTRYPOINT_REQUIRED",
            "preview_only": True,
            "no_send": True,
            "send_order_called": False,
            "order_id": "ORDER_1",
            "order_queued_id": "ORDER_QUEUED_ORDER_1",
            "request_hash": "HASH_1",
            "lock_id": "LOCK_1",
            "execution_id": "EXEC_1",
            "source_signal_id": "SIG_1",
            "blocked_reasons": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def _adapter_request(self, **overrides: object) -> dict[str, object]:
        request = {
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
        }
        request.update(overrides)
        return request

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
            "send_order_called": False,
            "execution_enabled": False,
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
        context = {"manual_send_order_entrypoint_confirmed": True}
        context.update(overrides)
        return context

    def _execute(
        self,
        final_send_gate_result: object | None = None,
        adapter_request: object | None = None,
        order_queued_record: object | None = None,
        broker_adapter: object | None = None,
        current_guard: object | None = None,
        context: object | None = None,
        queue_snapshot: object | None = None,
        current_queue_snapshot: object | None = None,
    ) -> dict[str, object]:
        return execute_send_order(
            self._final_gate() if final_send_gate_result is None else final_send_gate_result,
            self._adapter_request() if adapter_request is None else adapter_request,
            self._record() if order_queued_record is None else order_queued_record,
            MockBrokerAdapter() if broker_adapter is None else broker_adapter,
            queue_path=None,
            queue_snapshot=queue_snapshot,
            current_queue_snapshot=current_queue_snapshot,
            current_guard=self._guard() if current_guard is None else current_guard,
            context=self._context() if context is None else context,
        )

    def test_final_gate_non_dict_is_blocked(self) -> None:
        result = self._execute(final_send_gate_result="invalid")

        self.assertFalse(result["send_order_executed"])
        self.assertEqual("final_gate", result["entrypoint_stage"])
        self.assertIn("final_send_gate_result must be a dict", result["blocked_reasons"])

    def test_final_send_gate_ok_false_is_blocked(self) -> None:
        result = self._execute(final_send_gate_result=self._final_gate(final_send_gate_ok=False))

        self.assertFalse(result["send_order_executed"])
        self.assertIn("final_send_gate_result.final_send_gate_ok is not true", result["blocked_reasons"])

    def test_next_stage_mismatch_is_blocked(self) -> None:
        result = self._execute(final_send_gate_result=self._final_gate(next_stage="OTHER"))

        self.assertFalse(result["send_order_executed"])
        self.assertIn(
            "final_send_gate_result.next_stage is not SEND_ORDER_ENTRYPOINT_REQUIRED",
            result["blocked_reasons"],
        )

    def test_final_gate_send_order_called_true_is_blocked(self) -> None:
        result = self._execute(final_send_gate_result=self._final_gate(send_order_called=True))

        self.assertFalse(result["send_order_executed"])
        self.assertIn("final_send_gate_result.send_order_called is not false", result["blocked_reasons"])

    def test_adapter_request_non_dict_is_blocked(self) -> None:
        result = self._execute(adapter_request="invalid")

        self.assertFalse(result["send_order_executed"])
        self.assertEqual("adapter_request", result["entrypoint_stage"])
        self.assertIn("adapter_request must be a dict", result["blocked_reasons"])

    def test_adapter_request_required_fields_are_required(self) -> None:
        fields = [
            "order_id",
            "source_signal_id",
            "execution_id",
            "request_hash",
            "lock_id",
            "account_no",
            "side",
            "code",
            "quantity",
            "price",
            "hoga",
        ]
        for field in fields:
            with self.subTest(field=field):
                request = self._adapter_request()
                request[field] = None
                result = self._execute(adapter_request=request)

                self.assertFalse(result["send_order_executed"])
                self.assertIn(f"adapter_request.{field} is required", result["blocked_reasons"])

    def test_order_queued_record_non_dict_is_blocked(self) -> None:
        result = self._execute(order_queued_record="invalid")

        self.assertFalse(result["send_order_executed"])
        self.assertEqual("record", result["entrypoint_stage"])
        self.assertIn("order_queued_record must be a dict", result["blocked_reasons"])

    def test_record_status_mismatch_is_blocked(self) -> None:
        result = self._execute(order_queued_record=self._record(status="REAL_READY"))

        self.assertFalse(result["send_order_executed"])
        self.assertIn("order_queued_record.status is not ORDER_QUEUED", result["blocked_reasons"])

    def test_record_send_order_called_true_is_blocked(self) -> None:
        result = self._execute(order_queued_record=self._record(send_order_called=True))

        self.assertFalse(result["send_order_executed"])
        self.assertIn("order_queued_record.send_order_called is not false", result["blocked_reasons"])

    def test_record_execution_enabled_true_is_blocked(self) -> None:
        result = self._execute(order_queued_record=self._record(execution_enabled=True))

        self.assertFalse(result["send_order_executed"])
        self.assertIn("order_queued_record.execution_enabled is not false", result["blocked_reasons"])

    def test_identity_mismatch_is_blocked(self) -> None:
        mismatches = {
            "order_id": "OTHER_ORDER",
            "request_hash": "OTHER_HASH",
            "lock_id": "OTHER_LOCK",
            "execution_id": "OTHER_EXEC",
            "source_signal_id": "OTHER_SIG",
        }
        for field, value in mismatches.items():
            with self.subTest(field=field):
                request = self._adapter_request(**{field: value})
                result = self._execute(adapter_request=request)

                self.assertFalse(result["send_order_executed"])
                self.assertTrue(
                    any(field in reason for reason in result["blocked_reasons"]),
                    result["blocked_reasons"],
                )

    def test_guard_missing_is_blocked(self) -> None:
        result = self._execute(current_guard={})

        self.assertFalse(result["send_order_executed"])
        self.assertIn("current_guard is required", result["blocked_reasons"])

    def test_guard_conditions_failure_is_blocked(self) -> None:
        cases = [
            ("real_trade_enabled", False, "current_guard.real_trade_enabled is not true"),
            ("kiwoom_logged_in", False, "current_guard.kiwoom_logged_in is not true"),
            ("account_selected", False, "current_guard.account_selected is not true"),
            ("account_no", "", "current_guard.account_no is required"),
            ("operator_confirmed", False, "current_guard.operator_confirmed is not true"),
        ]
        for field, value, reason in cases:
            with self.subTest(field=field):
                result = self._execute(current_guard=self._guard(**{field: value}))

                self.assertFalse(result["send_order_executed"])
                self.assertIn(reason, result["blocked_reasons"])

    def test_account_no_mismatch_is_blocked(self) -> None:
        result = self._execute(current_guard=self._guard(account_no="87654321"))

        self.assertFalse(result["send_order_executed"])
        self.assertIn("current_guard.account_no does not match adapter_request.account_no", result["blocked_reasons"])

    def test_manual_entrypoint_confirmation_missing_is_blocked(self) -> None:
        result = self._execute(context={})

        self.assertFalse(result["send_order_executed"])
        self.assertEqual("operator_confirmation", result["entrypoint_stage"])
        self.assertIn("manual send order entrypoint confirmation is required", result["blocked_reasons"])

    def test_existing_operator_confirmed_alone_is_blocked(self) -> None:
        result = self._execute(context={"operator_confirmed": True})

        self.assertFalse(result["send_order_executed"])
        self.assertIn("manual send order entrypoint confirmation is required", result["blocked_reasons"])

    def test_operator_confirmed_for_send_order_entrypoint_allows_call(self) -> None:
        result = self._execute(context={"operator_confirmed_for_send_order_entrypoint": True})

        self.assertTrue(result["send_order_executed"])

    def test_stale_queue_snapshot_is_blocked(self) -> None:
        result = self._execute(
            queue_snapshot={"sha256": "AAA"},
            current_queue_snapshot={"sha256": "BBB"},
        )

        self.assertFalse(result["send_order_executed"])
        self.assertEqual("stale_queue", result["entrypoint_stage"])
        self.assertIn("queue file changed after final send gate; rerun final send gate", result["blocked_reasons"])

    def test_broker_adapter_missing_is_blocked(self) -> None:
        result = self._execute(broker_adapter=False)

        self.assertFalse(result["send_order_executed"])
        self.assertEqual("broker_adapter", result["entrypoint_stage"])
        self.assertIn("broker_adapter.send_order must be callable", result["blocked_reasons"])

    def test_broker_adapter_send_order_not_callable_is_blocked(self) -> None:
        class BadAdapter:
            send_order = None

        result = self._execute(broker_adapter=BadAdapter())

        self.assertFalse(result["send_order_executed"])
        self.assertIn("broker_adapter.send_order must be callable", result["blocked_reasons"])

    def test_normal_mock_adapter_call_succeeds(self) -> None:
        adapter = MockBrokerAdapter()
        result = self._execute(
            broker_adapter=adapter,
            queue_snapshot={"sha256": "AAA"},
            current_queue_snapshot={"sha256": "AAA"},
        )

        self.assertTrue(result["send_order_executed"])
        self.assertEqual("send_order_called_mock", result["entrypoint_stage"])
        self.assertEqual("SEND_ORDER_RESULT_REVIEW_REQUIRED", result["next_stage"])
        self.assertEqual("MOCK_BROKER", result["broker"])
        self.assertEqual("ORDER_1", result["order_id"])
        self.assertEqual("ORDER_QUEUED_ORDER_1", result["order_queued_id"])
        self.assertEqual("HASH_1", result["request_hash"])
        self.assertEqual("LOCK_1", result["lock_id"])
        self.assertEqual("EXEC_1", result["execution_id"])
        self.assertTrue(result["runtime_write_required"])
        self.assertTrue(result["send_order_called"])
        self.assertEqual([], result["blocked_reasons"])
        self.assertEqual(1, len(adapter.requests))

    def test_request_passed_to_adapter_matches_adapter_request(self) -> None:
        adapter = MockBrokerAdapter()
        adapter_request = self._adapter_request()

        self._execute(adapter_request=adapter_request, broker_adapter=adapter)

        self.assertEqual(adapter_request, adapter.requests[0])
        self.assertIsNot(adapter_request, adapter.requests[0])

    def test_broker_result_is_returned(self) -> None:
        result = self._execute()

        self.assertEqual({"broker_status": "MOCK_ACCEPTED", "request_hash": "HASH_1"}, result["broker_result"])

    def test_adapter_exception_returns_uncertain_review_result(self) -> None:
        result = self._execute(broker_adapter=RaisingBrokerAdapter())

        self.assertFalse(result["send_order_executed"])
        self.assertEqual("broker_adapter", result["entrypoint_stage"])
        self.assertEqual("BROKER_CALL_UNCERTAIN_REVIEW_REQUIRED", result["next_stage"])
        self.assertFalse(result["send_order_called"])
        self.assertIn("broker adapter raised exception: mock broker failure", result["blocked_reasons"])
        self.assertIn("broker call may be uncertain; manual review required", result["warnings"])

    def test_runtime_write_and_real_adapter_are_not_called(self) -> None:
        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
            mock.patch("kiwoom_order_adapter.build_kiwoom_order_request") as adapter_builder,
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
        ):
            result = self._execute()

        self.assertTrue(result["send_order_executed"])
        write_text.assert_not_called()
        open_mock.assert_not_called()
        adapter_builder.assert_not_called()
        send_order_stub.assert_not_called()

    def test_module_does_not_import_real_adapter_or_dynamic_call(self) -> None:
        module_text = send_order_entrypoint.__loader__.get_source(send_order_entrypoint.__name__)

        self.assertNotIn("kiwoom_order_adapter", module_text)
        self.assertNotIn("dynamicCall", module_text)
        self.assertNotIn("QTimer", module_text)
        self.assertNotIn("QPushButton", module_text)

    def test_input_dicts_are_not_mutated(self) -> None:
        final_gate = self._final_gate()
        adapter_request = self._adapter_request()
        record = self._record()
        guard = self._guard()
        queue_snapshot = {"sha256": "AAA"}
        current_queue_snapshot = {"sha256": "AAA"}
        context = self._context()
        originals = (
            deepcopy(final_gate),
            deepcopy(adapter_request),
            deepcopy(record),
            deepcopy(guard),
            deepcopy(queue_snapshot),
            deepcopy(current_queue_snapshot),
            deepcopy(context),
        )

        execute_send_order(
            final_gate,
            adapter_request,
            record,
            MockBrokerAdapter(),
            queue_path=None,
            queue_snapshot=queue_snapshot,
            current_queue_snapshot=current_queue_snapshot,
            current_guard=guard,
            context=context,
        )

        self.assertEqual(originals[0], final_gate)
        self.assertEqual(originals[1], adapter_request)
        self.assertEqual(originals[2], record)
        self.assertEqual(originals[3], guard)
        self.assertEqual(originals[4], queue_snapshot)
        self.assertEqual(originals[5], current_queue_snapshot)
        self.assertEqual(originals[6], context)


if __name__ == "__main__":
    unittest.main()
