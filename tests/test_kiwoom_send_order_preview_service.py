# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import unittest
from unittest import mock

import kiwoom_send_order_preview_service
from kiwoom_send_order_preview_service import preview_kiwoom_send_order_request


class KiwoomSendOrderPreviewServiceTest(unittest.TestCase):
    def _review(self, **overrides: object) -> dict[str, object]:
        review = {
            "review_ok": True,
            "review_stage": "order_queued_record_reviewed",
            "next_stage": "SEND_ORDER_REQUEST_PREVIEW_REQUIRED",
            "preview_only": True,
            "no_send": True,
            "send_order_called": False,
            "order_queued_id": "ORDER_QUEUED_ORDER_1",
            "order_id": "ORDER_1",
            "request_hash": "HASH_1",
            "lock_id": "LOCK_1",
            "execution_id": "EXEC_1",
            "blocked_reasons": [],
            "warnings": [],
        }
        review.update(overrides)
        return review

    def _request_preview(self, **overrides: object) -> dict[str, object]:
        preview = {
            "account_no": "12345678",
            "side": "BUY",
            "code": "003550",
            "quantity": 10,
            "price": 100,
            "hoga": "LIMIT",
        }
        preview.update(overrides)
        return preview

    def _execution_request(self, **overrides: object) -> dict[str, object]:
        request = {
            "execution_id": "EXEC_1",
            "order_id": "ORDER_1",
            "source_signal_id": "SIG_1",
            "lock_id": "LOCK_1",
            "request_hash": "HASH_1",
            "guard_snapshot": {"account_no": "12345678", "operator_confirmed": True},
            "request_preview": self._request_preview(),
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
            "execution_request": self._execution_request(),
            "queue_contract_version": "preview-1",
            "send_order_called": False,
            "execution_enabled": False,
            "blocked_reasons": [],
        }
        record.update(overrides)
        return record

    def test_record_review_result_non_dict_is_blocked(self) -> None:
        result = preview_kiwoom_send_order_request(None, self._record())

        self.assertFalse(result["adapter_preview_ok"])
        self.assertEqual("record_review", result["adapter_stage"])
        self.assertIn("record_review_result must be a dict", result["blocked_reasons"])

    def test_review_ok_false_is_blocked(self) -> None:
        result = preview_kiwoom_send_order_request(self._review(review_ok=False), self._record())

        self.assertFalse(result["adapter_preview_ok"])
        self.assertIn("record_review_result.review_ok is not true", result["blocked_reasons"])

    def test_review_next_stage_mismatch_is_blocked(self) -> None:
        result = preview_kiwoom_send_order_request(self._review(next_stage="OTHER"), self._record())

        self.assertFalse(result["adapter_preview_ok"])
        self.assertIn(
            "record_review_result.next_stage is not SEND_ORDER_REQUEST_PREVIEW_REQUIRED",
            result["blocked_reasons"],
        )

    def test_order_queued_record_non_dict_is_blocked(self) -> None:
        result = preview_kiwoom_send_order_request(self._review(), None)

        self.assertFalse(result["adapter_preview_ok"])
        self.assertEqual("record", result["adapter_stage"])
        self.assertIn("order_queued_record must be a dict", result["blocked_reasons"])

    def test_status_mismatch_is_blocked(self) -> None:
        result = preview_kiwoom_send_order_request(self._review(), self._record(status="REAL_READY"))

        self.assertFalse(result["adapter_preview_ok"])
        self.assertIn("order_queued_record.status is not ORDER_QUEUED", result["blocked_reasons"])

    def test_send_order_called_true_is_blocked(self) -> None:
        result = preview_kiwoom_send_order_request(self._review(), self._record(send_order_called=True))

        self.assertFalse(result["adapter_preview_ok"])
        self.assertIn("order_queued_record.send_order_called is not false", result["blocked_reasons"])

    def test_execution_enabled_true_is_blocked(self) -> None:
        result = preview_kiwoom_send_order_request(self._review(), self._record(execution_enabled=True))

        self.assertFalse(result["adapter_preview_ok"])
        self.assertIn("order_queued_record.execution_enabled is not false", result["blocked_reasons"])

    def test_execution_request_missing_is_blocked(self) -> None:
        result = preview_kiwoom_send_order_request(self._review(), self._record(execution_request=None))

        self.assertFalse(result["adapter_preview_ok"])
        self.assertIn("order_queued_record.execution_request is required", result["blocked_reasons"])

    def test_request_preview_missing_is_blocked(self) -> None:
        request = self._execution_request(request_preview=None)
        result = preview_kiwoom_send_order_request(self._review(), self._record(execution_request=request))

        self.assertFalse(result["adapter_preview_ok"])
        self.assertIn("execution_request.request_preview is required", result["blocked_reasons"])

    def test_account_no_missing_is_blocked(self) -> None:
        request = self._execution_request(
            guard_snapshot={},
            request_preview=self._request_preview(account_no=""),
        )
        result = preview_kiwoom_send_order_request(self._review(), self._record(execution_request=request))

        self.assertFalse(result["adapter_preview_ok"])
        self.assertIn("account_no is required", result["blocked_reasons"])

    def test_account_no_can_come_from_guard_snapshot(self) -> None:
        request = self._execution_request(request_preview=self._request_preview(account_no=""))
        result = preview_kiwoom_send_order_request(self._review(), self._record(execution_request=request))

        self.assertTrue(result["adapter_preview_ok"])
        self.assertEqual("12345678", result["send_order_request_preview"]["account_no"])

    def test_side_or_order_type_missing_is_blocked(self) -> None:
        request = self._execution_request(request_preview=self._request_preview(side="", order_type=""))
        result = preview_kiwoom_send_order_request(self._review(), self._record(execution_request=request))

        self.assertFalse(result["adapter_preview_ok"])
        self.assertIn("side/order_type is required", result["blocked_reasons"])

    def test_order_type_can_supply_side(self) -> None:
        request = self._execution_request(request_preview=self._request_preview(side="", order_type="SELL"))
        result = preview_kiwoom_send_order_request(self._review(), self._record(execution_request=request))

        self.assertTrue(result["adapter_preview_ok"])
        self.assertEqual("SELL", result["send_order_request_preview"]["side"])

    def test_side_or_order_type_outside_buy_sell_is_blocked(self) -> None:
        request = self._execution_request(request_preview=self._request_preview(side="HOLD"))
        result = preview_kiwoom_send_order_request(self._review(), self._record(execution_request=request))

        self.assertFalse(result["adapter_preview_ok"])
        self.assertIn("side/order_type must be BUY or SELL", result["blocked_reasons"])

    def test_code_missing_is_blocked(self) -> None:
        request = self._execution_request(request_preview=self._request_preview(code=""))
        result = preview_kiwoom_send_order_request(self._review(), self._record(execution_request=request))

        self.assertFalse(result["adapter_preview_ok"])
        self.assertIn("code is required", result["blocked_reasons"])

    def test_quantity_zero_or_less_is_blocked(self) -> None:
        for quantity in (0, -1):
            with self.subTest(quantity=quantity):
                request = self._execution_request(request_preview=self._request_preview(quantity=quantity))
                result = preview_kiwoom_send_order_request(self._review(), self._record(execution_request=request))

                self.assertFalse(result["adapter_preview_ok"])
                self.assertIn("quantity must be greater than 0", result["blocked_reasons"])

    def test_hoga_missing_is_blocked(self) -> None:
        request = self._execution_request(request_preview=self._request_preview(hoga=""))
        result = preview_kiwoom_send_order_request(self._review(), self._record(execution_request=request))

        self.assertFalse(result["adapter_preview_ok"])
        self.assertIn("hoga is required", result["blocked_reasons"])

    def test_hoga_outside_market_limit_is_blocked(self) -> None:
        request = self._execution_request(request_preview=self._request_preview(hoga="UNDECIDED"))
        result = preview_kiwoom_send_order_request(self._review(), self._record(execution_request=request))

        self.assertFalse(result["adapter_preview_ok"])
        self.assertIn("hoga must be MARKET or LIMIT", result["blocked_reasons"])

    def test_limit_price_zero_or_less_is_blocked(self) -> None:
        for price in (0, -1):
            with self.subTest(price=price):
                request = self._execution_request(request_preview=self._request_preview(hoga="LIMIT", price=price))
                result = preview_kiwoom_send_order_request(self._review(), self._record(execution_request=request))

                self.assertFalse(result["adapter_preview_ok"])
                self.assertIn("LIMIT price must be greater than 0", result["blocked_reasons"])

    def test_market_price_zero_is_allowed(self) -> None:
        request = self._execution_request(request_preview=self._request_preview(side="SELL", hoga="MARKET", price=0))
        result = preview_kiwoom_send_order_request(self._review(), self._record(execution_request=request))

        self.assertTrue(result["adapter_preview_ok"])
        self.assertEqual(0, result["send_order_request_preview"]["price"])
        self.assertEqual("MARKET", result["send_order_request_preview"]["hoga"])

    def test_cancel_modify_candidate_is_blocked(self) -> None:
        candidates = [
            self._request_preview(action="CANCEL"),
            self._request_preview(order_action="MODIFY"),
            self._request_preview(original_order_no="12345"),
        ]
        for request_preview in candidates:
            with self.subTest(request_preview=request_preview):
                request = self._execution_request(request_preview=request_preview)
                result = preview_kiwoom_send_order_request(self._review(), self._record(execution_request=request))

                self.assertFalse(result["adapter_preview_ok"])
                self.assertIn(
                    "cancel/modify orders are not supported in adapter preview phase 1",
                    result["blocked_reasons"],
                )

    def test_record_and_execution_request_identity_mismatch_is_blocked(self) -> None:
        mismatches = {
            "order_id": "OTHER_ORDER",
            "source_signal_id": "OTHER_SIG",
            "execution_id": "OTHER_EXEC",
            "request_hash": "OTHER_HASH",
            "lock_id": "OTHER_LOCK",
        }
        for field, value in mismatches.items():
            with self.subTest(field=field):
                request = self._execution_request(**{field: value})
                result = preview_kiwoom_send_order_request(self._review(), self._record(execution_request=request))

                self.assertFalse(result["adapter_preview_ok"])
                self.assertIn(
                    f"record.{field} does not match execution_request.{field}",
                    result["blocked_reasons"],
                )

    def test_normal_limit_buy_preview_succeeds(self) -> None:
        result = preview_kiwoom_send_order_request(self._review(), self._record())

        self.assertTrue(result["adapter_preview_ok"])
        self.assertEqual("kiwoom_send_order_request_preview_created", result["adapter_stage"])
        self.assertEqual("FINAL_SEND_GATE_REQUIRED", result["next_stage"])
        self.assertTrue(result["preview_only"])
        self.assertTrue(result["no_send"])
        self.assertFalse(result["send_order_called"])
        preview = result["send_order_request_preview"]
        self.assertEqual("ORDER_1", preview["order_id"])
        self.assertEqual("SIG_1", preview["source_signal_id"])
        self.assertEqual("EXEC_1", preview["execution_id"])
        self.assertEqual("HASH_1", preview["request_hash"])
        self.assertEqual("LOCK_1", preview["lock_id"])
        self.assertEqual("12345678", preview["account_no"])
        self.assertEqual("BUY", preview["side"])
        self.assertEqual("003550", preview["code"])
        self.assertEqual(10, preview["quantity"])
        self.assertEqual(100, preview["price"])
        self.assertEqual("LIMIT", preview["hoga"])
        self.assertEqual("", preview["original_order_no"])
        self.assertEqual("9000", preview["screen_no"])
        self.assertEqual("SEND_ORDER_PREVIEW_ORDER_1", preview["rqname"])
        self.assertEqual([], result["blocked_reasons"])

    def test_normal_market_sell_preview_succeeds(self) -> None:
        request = self._execution_request(
            request_preview=self._request_preview(side="SELL", hoga="MARKET", price=0)
        )
        result = preview_kiwoom_send_order_request(self._review(), self._record(execution_request=request))

        self.assertTrue(result["adapter_preview_ok"])
        self.assertEqual("SELL", result["send_order_request_preview"]["side"])
        self.assertEqual("MARKET", result["send_order_request_preview"]["hoga"])
        self.assertEqual("FINAL_SEND_GATE_REQUIRED", result["next_stage"])

    def test_no_kiwoom_numeric_code_mapping_fields_are_created(self) -> None:
        result = preview_kiwoom_send_order_request(self._review(), self._record())
        preview = result["send_order_request_preview"]

        self.assertNotIn("kiwoom_order_type_code", preview)
        self.assertNotIn("kiwoom_hoga_code", preview)
        self.assertNotIn("order_type_code", preview)
        self.assertNotIn("hoga_code", preview)

    def test_send_order_adapter_runtime_write_and_gui_are_not_called(self) -> None:
        with (
            mock.patch("kiwoom_order_adapter.build_kiwoom_order_request") as adapter_builder,
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = preview_kiwoom_send_order_request(self._review(), self._record())

        self.assertTrue(result["adapter_preview_ok"])
        adapter_builder.assert_not_called()
        send_order_stub.assert_not_called()
        write_text.assert_not_called()
        open_mock.assert_not_called()

    def test_module_does_not_import_adapter_gui_or_timer(self) -> None:
        module_text = kiwoom_send_order_preview_service.__loader__.get_source(
            kiwoom_send_order_preview_service.__name__
        )

        self.assertNotIn("kiwoom_order_adapter", module_text)
        self.assertNotIn("QTimer", module_text)
        self.assertNotIn("QPushButton", module_text)
        self.assertNotIn("dynamicCall", module_text)

    def test_input_dicts_are_not_mutated(self) -> None:
        review = self._review()
        record = self._record()
        original_review = deepcopy(review)
        original_record = deepcopy(record)

        preview_kiwoom_send_order_request(review, record)

        self.assertEqual(original_review, review)
        self.assertEqual(original_record, record)


if __name__ == "__main__":
    unittest.main()
