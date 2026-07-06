# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import unittest
from unittest import mock

import chejan_event_review_service
from chejan_event_review_service import review_chejan_event


_DEFAULT = object()


class ChejanEventReviewServiceTest(unittest.TestCase):
    def _event(self, **overrides: object) -> dict[str, object]:
        event = {
            "normalized": True,
            "event_stage": "chejan_event_normalized",
            "event_type": "PARTIAL_FILL",
            "broker": "KIWOOM",
            "source": "kiwoom_chejan",
            "gubun": "0",
            "broker_order_no": "BRK_1",
            "account_no": "12345678",
            "code": "003550",
            "name": "LG",
            "side": "BUY",
            "order_status": "체결",
            "order_quantity": 10,
            "filled_quantity": 3,
            "remaining_quantity": 7,
            "order_price": 1000,
            "filled_price": 1000,
            "request_hash": None,
            "lock_id": None,
            "execution_id": None,
            "unresolved": False,
            "blocked_reasons": [],
            "warnings": [],
            "raw_event": {},
        }
        event.update(overrides)
        return event

    def _record(self, **overrides: object) -> dict[str, object]:
        record = {
            "id": "ORDER_QUEUED_ORDER_1",
            "status": "ORDER_QUEUED",
            "order_id": "ORDER_1",
            "request_hash": "HASH_1",
            "lock_id": "LOCK_1",
            "execution_id": "EXEC_1",
            "broker_order_no": "BRK_1",
            "account_no": "12345678",
            "code": "003550",
            "side": "BUY",
            "send_order_called": True,
            "send_order_result_status": "SEND_ORDER_CALLED",
        }
        record.update(overrides)
        return record

    def _result_review(self, **overrides: object) -> dict[str, object]:
        result = {
            "result_review_ok": True,
            "review_stage": "send_order_result_reviewed",
            "next_stage": "CHEJAN_OR_EXECUTION_EVENT_REQUIRED",
            "send_order_called": True,
            "send_order_result_status": "SEND_ORDER_CALLED",
            "order_id": "ORDER_1",
            "order_queued_id": "ORDER_QUEUED_ORDER_1",
            "request_hash": "HASH_1",
            "lock_id": "LOCK_1",
            "execution_id": "EXEC_1",
            "broker": "MOCK_BROKER",
            "broker_order_no": "BRK_1",
            "blocked_reasons": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def _review(
        self,
        normalized_event: object = _DEFAULT,
        order_record: object = _DEFAULT,
        send_order_result_review_result: object | None = None,
    ) -> dict[str, object]:
        return review_chejan_event(
            self._event() if normalized_event is _DEFAULT else normalized_event,
            order_record=self._record() if order_record is _DEFAULT else order_record,
            send_order_result_review_result=send_order_result_review_result,
        )

    def test_normalized_event_non_dict_is_blocked(self) -> None:
        result = self._review(normalized_event="invalid")

        self.assertFalse(result["chejan_review_ok"])
        self.assertEqual("normalized_event", result["review_stage"])
        self.assertIn("normalized_event must be a dict", result["blocked_reasons"])

    def test_normalized_false_is_blocked(self) -> None:
        result = self._review(normalized_event=self._event(normalized=False))

        self.assertFalse(result["chejan_review_ok"])
        self.assertIn("normalized_event.normalized is not true", result["blocked_reasons"])

    def test_unresolved_true_is_blocked(self) -> None:
        result = self._review(normalized_event=self._event(unresolved=True))

        self.assertFalse(result["chejan_review_ok"])
        self.assertIn("normalized_event.unresolved is not false", result["blocked_reasons"])

    def test_non_kiwoom_broker_is_blocked(self) -> None:
        result = self._review(normalized_event=self._event(broker="OTHER"))

        self.assertFalse(result["chejan_review_ok"])
        self.assertIn("normalized_event.broker is not KIWOOM", result["blocked_reasons"])

    def test_order_unknown_is_manual_review(self) -> None:
        result = self._review(normalized_event=self._event(event_type="ORDER_UNKNOWN"))

        self.assertFalse(result["chejan_review_ok"])
        self.assertEqual("MANUAL_CHEJAN_REVIEW_REQUIRED", result["next_stage"])
        self.assertIn("ORDER_UNKNOWN requires manual Chejan review", result["blocked_reasons"])

    def test_order_record_missing_is_blocked(self) -> None:
        result = self._review(order_record=None)

        self.assertFalse(result["chejan_review_ok"])
        self.assertEqual("order_record", result["review_stage"])
        self.assertIn("order_record must be a dict", result["blocked_reasons"])

    def test_order_record_send_order_called_false_is_blocked(self) -> None:
        result = self._review(order_record=self._record(send_order_called=False))

        self.assertFalse(result["chejan_review_ok"])
        self.assertIn("order_record.send_order_called is not true", result["blocked_reasons"])

    def test_send_order_result_status_mismatch_is_blocked(self) -> None:
        result = self._review(order_record=self._record(send_order_result_status="OTHER"))

        self.assertFalse(result["chejan_review_ok"])
        self.assertIn("order_record.send_order_result_status is not SEND_ORDER_CALLED", result["blocked_reasons"])

    def test_account_code_side_mismatch_is_blocked(self) -> None:
        cases = [
            ("account_no", "99999999"),
            ("code", "000000"),
            ("side", "SELL"),
        ]
        for field, value in cases:
            with self.subTest(field=field):
                result = self._review(order_record=self._record(**{field: value}))

                self.assertFalse(result["chejan_review_ok"])
                self.assertIn(
                    f"normalized_event.{field} does not match order_record.{field}",
                    result["blocked_reasons"],
                )

    def test_matching_broker_order_no_succeeds(self) -> None:
        result = self._review()

        self.assertTrue(result["chejan_review_ok"])
        self.assertEqual("broker_order_no", result["matched_by"])
        self.assertEqual("BRK_1", result["broker_order_no"])

    def test_event_broker_order_no_with_record_missing_warns_and_succeeds(self) -> None:
        result = self._review(order_record=self._record(broker_order_no=None))

        self.assertTrue(result["chejan_review_ok"])
        self.assertEqual("event_broker_order_no", result["matched_by"])
        self.assertIn(
            "order record broker_order_no is missing; Chejan recorder may enrich it",
            result["warnings"],
        )

    def test_record_broker_order_no_with_event_missing_is_blocked(self) -> None:
        result = self._review(normalized_event=self._event(broker_order_no=None))

        self.assertFalse(result["chejan_review_ok"])
        self.assertIn("normalized_event.broker_order_no is required", result["blocked_reasons"])

    def test_both_broker_order_no_missing_is_blocked(self) -> None:
        result = self._review(
            normalized_event=self._event(broker_order_no=None),
            order_record=self._record(broker_order_no=None),
        )

        self.assertFalse(result["chejan_review_ok"])
        self.assertIn("broker_order_no is required to link Chejan event in phase 1", result["blocked_reasons"])

    def test_event_record_types_go_to_chejan_event_record_required(self) -> None:
        for event_type in ("ORDER_ACCEPTED", "ORDER_OPEN", "ORDER_REJECTED", "ORDER_CANCELED"):
            with self.subTest(event_type=event_type):
                result = self._review(normalized_event=self._event(event_type=event_type, filled_quantity=0))

                self.assertTrue(result["chejan_review_ok"])
                self.assertEqual("CHEJAN_EVENT_RECORD_REQUIRED", result["next_stage"])

    def test_fill_types_go_to_fill_record_required(self) -> None:
        partial = self._review(normalized_event=self._event(event_type="PARTIAL_FILL", filled_quantity=3, remaining_quantity=7))
        full = self._review(normalized_event=self._event(event_type="FULL_FILL", filled_quantity=10, remaining_quantity=0))

        self.assertTrue(partial["chejan_review_ok"])
        self.assertEqual("FILL_RECORD_REQUIRED", partial["next_stage"])
        self.assertTrue(full["chejan_review_ok"])
        self.assertEqual("FILL_RECORD_REQUIRED", full["next_stage"])

    def test_partial_fill_quantity_must_be_positive(self) -> None:
        result = self._review(normalized_event=self._event(event_type="PARTIAL_FILL", filled_quantity=0, remaining_quantity=7))

        self.assertFalse(result["chejan_review_ok"])
        self.assertIn("filled_quantity must be greater than 0", result["blocked_reasons"])

    def test_full_fill_remaining_quantity_must_be_zero(self) -> None:
        result = self._review(normalized_event=self._event(event_type="FULL_FILL", filled_quantity=10, remaining_quantity=1))

        self.assertFalse(result["chejan_review_ok"])
        self.assertIn("FULL_FILL remaining_quantity must be 0", result["blocked_reasons"])

    def test_filled_price_missing_warns(self) -> None:
        result = self._review(normalized_event=self._event(filled_price=None))

        self.assertTrue(result["chejan_review_ok"])
        self.assertIn("filled_price is missing; fill recorder should verify price before recording", result["warnings"])

    def test_send_order_result_review_mismatch_is_blocked(self) -> None:
        result = self._review(send_order_result_review_result=self._result_review(request_hash="OTHER"))

        self.assertFalse(result["chejan_review_ok"])
        self.assertIn(
            "send_order_result_review_result.request_hash does not match order_record.request_hash",
            result["blocked_reasons"],
        )

    def test_send_order_result_review_success_is_accepted(self) -> None:
        result = self._review(send_order_result_review_result=self._result_review())

        self.assertTrue(result["chejan_review_ok"])

    def test_normal_review_result_fields(self) -> None:
        result = self._review()

        self.assertTrue(result["chejan_review_ok"])
        self.assertEqual("chejan_event_reviewed", result["review_stage"])
        self.assertEqual("PARTIAL_FILL", result["event_type"])
        self.assertEqual("ORDER_1", result["order_id"])
        self.assertEqual("ORDER_QUEUED_ORDER_1", result["order_queued_id"])
        self.assertEqual("HASH_1", result["request_hash"])
        self.assertEqual("LOCK_1", result["lock_id"])
        self.assertEqual("EXEC_1", result["execution_id"])
        self.assertEqual([], result["blocked_reasons"])

    def test_runtime_fills_positions_send_order_and_chejan_are_not_called(self) -> None:
        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
        ):
            result = self._review()

        self.assertTrue(result["chejan_review_ok"])
        write_text.assert_not_called()
        open_mock.assert_not_called()
        send_order_stub.assert_not_called()

    def test_module_does_not_reference_runtime_gui_timer_or_send_order(self) -> None:
        module_text = chejan_event_review_service.__loader__.get_source(chejan_event_review_service.__name__)

        self.assertNotIn("ORDER_QUEUE_PATH", module_text)
        self.assertNotIn("fills.json", module_text)
        self.assertNotIn("positions.json", module_text)
        self.assertNotIn("kiwoom_order_adapter", module_text)
        self.assertNotIn("dynamicCall", module_text)
        self.assertNotIn("QTimer", module_text)
        self.assertNotIn("QPushButton", module_text)

    def test_input_dicts_are_not_mutated(self) -> None:
        event = self._event()
        record = self._record()
        result_review = self._result_review()
        originals = (deepcopy(event), deepcopy(record), deepcopy(result_review))

        review_chejan_event(event, order_record=record, send_order_result_review_result=result_review)

        self.assertEqual(originals[0], event)
        self.assertEqual(originals[1], record)
        self.assertEqual(originals[2], result_review)


if __name__ == "__main__":
    unittest.main()
