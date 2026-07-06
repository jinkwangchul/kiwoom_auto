# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import unittest
from unittest import mock

import order_queued_review_service
from order_queued_review_service import review_order_queued_record


class OrderQueuedReviewServiceTest(unittest.TestCase):
    def _execution_request(self, **overrides: object) -> dict[str, object]:
        request = {
            "execution_id": "EXEC_1",
            "order_id": "ORDER_1",
            "source_signal_id": "SIG_1",
            "lock_id": "LOCK_1",
            "request_hash": "HASH_1",
            "guard_snapshot": {"operator_confirmed": True},
            "request_preview": {"hoga": "MARKET", "order_type": "BUY"},
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

    def test_non_dict_record_is_blocked(self) -> None:
        result = review_order_queued_record(None)

        self.assertFalse(result["review_ok"])
        self.assertEqual("record_validation", result["review_stage"])
        self.assertIn("record must be a dict", result["blocked_reasons"])

    def test_status_mismatch_is_blocked(self) -> None:
        result = review_order_queued_record(self._record(status="REAL_READY"))

        self.assertFalse(result["review_ok"])
        self.assertIn("record.status is not ORDER_QUEUED", result["blocked_reasons"])

    def test_send_order_called_true_is_blocked(self) -> None:
        result = review_order_queued_record(self._record(send_order_called=True))

        self.assertFalse(result["review_ok"])
        self.assertIn("record.send_order_called is not false", result["blocked_reasons"])

    def test_execution_enabled_true_is_blocked(self) -> None:
        result = review_order_queued_record(self._record(execution_enabled=True))

        self.assertFalse(result["review_ok"])
        self.assertIn("record.execution_enabled is not false", result["blocked_reasons"])

    def test_source_mismatch_is_blocked(self) -> None:
        result = review_order_queued_record(self._record(source="manual"))

        self.assertFalse(result["review_ok"])
        self.assertIn("record.source is not execution_queue_pending", result["blocked_reasons"])

    def test_blocked_reasons_present_is_blocked(self) -> None:
        result = review_order_queued_record(self._record(blocked_reasons=["blocked"]))

        self.assertFalse(result["review_ok"])
        self.assertIn("record.blocked_reasons is not empty", result["blocked_reasons"])

    def test_required_record_fields_are_required(self) -> None:
        fields = [
            "id",
            "source_signal_id",
            "order_id",
            "candidate_id",
            "queue_pending_id",
            "request_hash",
            "lock_id",
            "execution_id",
            "queue_contract_version",
            "send_order_called",
            "execution_enabled",
        ]
        for field in fields:
            with self.subTest(field=field):
                record = self._record()
                record[field] = None
                result = review_order_queued_record(record)

                self.assertFalse(result["review_ok"])
                self.assertIn(f"record.{field} is required", result["blocked_reasons"])

    def test_execution_request_must_be_dict(self) -> None:
        result = review_order_queued_record(self._record(execution_request=None))

        self.assertFalse(result["review_ok"])
        self.assertIn("record.execution_request must be a dict", result["blocked_reasons"])

    def test_execution_request_required_fields_are_required(self) -> None:
        fields = [
            "execution_id",
            "order_id",
            "source_signal_id",
            "lock_id",
            "request_hash",
        ]
        for field in fields:
            with self.subTest(field=field):
                request = self._execution_request()
                request[field] = ""
                result = review_order_queued_record(self._record(execution_request=request))

                self.assertFalse(result["review_ok"])
                self.assertIn(f"execution_request.{field} is required", result["blocked_reasons"])

    def test_execution_request_structured_fields_must_be_dicts(self) -> None:
        for field in ("guard_snapshot", "request_preview"):
            with self.subTest(field=field):
                request = self._execution_request(**{field: None})
                result = review_order_queued_record(self._record(execution_request=request))

                self.assertFalse(result["review_ok"])
                self.assertIn(f"execution_request.{field} must be a dict", result["blocked_reasons"])

    def test_request_hash_mismatch_is_blocked(self) -> None:
        result = review_order_queued_record(
            self._record(execution_request=self._execution_request(request_hash="OTHER"))
        )

        self.assertFalse(result["review_ok"])
        self.assertIn("record.request_hash does not match execution_request.request_hash", result["blocked_reasons"])

    def test_lock_id_mismatch_is_blocked(self) -> None:
        result = review_order_queued_record(
            self._record(execution_request=self._execution_request(lock_id="OTHER"))
        )

        self.assertFalse(result["review_ok"])
        self.assertIn("record.lock_id does not match execution_request.lock_id", result["blocked_reasons"])

    def test_execution_id_mismatch_is_blocked(self) -> None:
        result = review_order_queued_record(
            self._record(execution_request=self._execution_request(execution_id="OTHER"))
        )

        self.assertFalse(result["review_ok"])
        self.assertIn("record.execution_id does not match execution_request.execution_id", result["blocked_reasons"])

    def test_order_id_mismatch_is_blocked(self) -> None:
        result = review_order_queued_record(
            self._record(execution_request=self._execution_request(order_id="OTHER"))
        )

        self.assertFalse(result["review_ok"])
        self.assertIn("record.order_id does not match execution_request.order_id", result["blocked_reasons"])

    def test_source_signal_id_mismatch_is_blocked(self) -> None:
        result = review_order_queued_record(
            self._record(execution_request=self._execution_request(source_signal_id="OTHER"))
        )

        self.assertFalse(result["review_ok"])
        self.assertIn("record.source_signal_id does not match execution_request.source_signal_id", result["blocked_reasons"])

    def test_valid_record_reviews_ok(self) -> None:
        result = review_order_queued_record(self._record())

        self.assertTrue(result["review_ok"])
        self.assertEqual("order_queued_record_reviewed", result["review_stage"])
        self.assertEqual("SEND_ORDER_REQUEST_PREVIEW_REQUIRED", result["next_stage"])
        self.assertTrue(result["preview_only"])
        self.assertTrue(result["no_send"])
        self.assertFalse(result["send_order_called"])
        self.assertEqual("ORDER_QUEUED_ORDER_1", result["order_queued_id"])
        self.assertEqual("ORDER_1", result["order_id"])
        self.assertEqual("HASH_1", result["request_hash"])
        self.assertEqual("LOCK_1", result["lock_id"])
        self.assertEqual("EXEC_1", result["execution_id"])
        self.assertEqual([], result["blocked_reasons"])

    def test_send_order_adapter_runtime_write_and_gui_are_not_called(self) -> None:
        with (
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
            mock.patch("kiwoom_order_adapter.build_kiwoom_order_request_preview_for_order") as adapter_preview,
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = review_order_queued_record(self._record())

        self.assertTrue(result["review_ok"])
        send_order_stub.assert_not_called()
        adapter_preview.assert_not_called()
        write_text.assert_not_called()
        open_mock.assert_not_called()

    def test_module_does_not_reference_gui_timer_or_send_order(self) -> None:
        module_text = order_queued_review_service.__loader__.get_source(
            order_queued_review_service.__name__
        )

        self.assertNotIn("QTimer", module_text)
        self.assertNotIn("QPushButton", module_text)
        self.assertNotIn("SendOrder", module_text)

    def test_input_record_is_not_mutated(self) -> None:
        record = self._record()
        original = deepcopy(record)

        review_order_queued_record(record)

        self.assertEqual(original, record)


if __name__ == "__main__":
    unittest.main()
