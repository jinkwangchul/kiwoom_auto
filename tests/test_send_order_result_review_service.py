# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import unittest
from unittest import mock

import send_order_result_review_service
from send_order_result_review_service import review_send_order_result


class SendOrderResultReviewServiceTest(unittest.TestCase):
    def _broker_result(self, **overrides: object) -> dict[str, object]:
        result = {
            "broker_status": "MOCK_ACCEPTED",
            "broker_order_no": "BRK_1",
            "request_hash": "HASH_1",
        }
        result.update(overrides)
        return result

    def _recorder_result(self, **overrides: object) -> dict[str, object]:
        result = {
            "recorded": True,
            "record_stage": "send_order_result_recorded",
            "next_stage": "SEND_ORDER_RESULT_REVIEW_REQUIRED",
            "changed": True,
            "order_queue_path": "TEMP_QUEUE",
            "backup_path": "TEMP_QUEUE.bak",
            "order_id": "ORDER_1",
            "order_queued_id": "ORDER_QUEUED_ORDER_1",
            "request_hash": "HASH_1",
            "lock_id": "LOCK_1",
            "execution_id": "EXEC_1",
            "send_order_called": True,
            "send_order_result_status": "SEND_ORDER_CALLED",
            "before_sha256": "BEFORE",
            "after_sha256": "AFTER",
            "blocked_reasons": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def _updated_record(self, **overrides: object) -> dict[str, object]:
        record = {
            "id": "ORDER_QUEUED_ORDER_1",
            "status": "ORDER_QUEUED",
            "source": "execution_queue_pending",
            "source_signal_id": "SIG_1",
            "order_id": "ORDER_1",
            "request_hash": "HASH_1",
            "lock_id": "LOCK_1",
            "execution_id": "EXEC_1",
            "send_order_called": True,
            "send_order_called_at": "2026-07-04 10:00:00",
            "send_order_entrypoint_stage": "send_order_called_mock",
            "send_order_result_status": "SEND_ORDER_CALLED",
            "send_order_result_recorded_at": "2026-07-04 10:00:01",
            "broker": "MOCK_BROKER",
            "broker_result": self._broker_result(),
            "broker_order_no": "BRK_1",
            "send_order_record_source": "send_order_entrypoint",
        }
        record.update(overrides)
        return record

    def _review(
        self,
        recorder_result: object | None = None,
        updated_order_record: object | None = None,
        broker_result: object | None = None,
    ) -> dict[str, object]:
        return review_send_order_result(
            self._recorder_result() if recorder_result is None else recorder_result,
            self._updated_record() if updated_order_record is None else updated_order_record,
            broker_result=broker_result,
        )

    def test_recorder_result_non_dict_is_blocked(self) -> None:
        result = self._review(recorder_result="invalid")

        self.assertFalse(result["result_review_ok"])
        self.assertEqual("recorder_result", result["review_stage"])
        self.assertIn("recorder_result must be a dict", result["blocked_reasons"])

    def test_recorded_false_is_blocked(self) -> None:
        result = self._review(recorder_result=self._recorder_result(recorded=False))

        self.assertFalse(result["result_review_ok"])
        self.assertIn("recorder_result.recorded is not true", result["blocked_reasons"])

    def test_next_stage_mismatch_is_blocked(self) -> None:
        result = self._review(recorder_result=self._recorder_result(next_stage="OTHER"))

        self.assertFalse(result["result_review_ok"])
        self.assertIn(
            "recorder_result.next_stage is not SEND_ORDER_RESULT_REVIEW_REQUIRED",
            result["blocked_reasons"],
        )

    def test_recorder_send_order_called_false_is_blocked(self) -> None:
        result = self._review(recorder_result=self._recorder_result(send_order_called=False))

        self.assertFalse(result["result_review_ok"])
        self.assertIn("recorder_result.send_order_called is not true", result["blocked_reasons"])

    def test_recorder_result_status_mismatch_is_blocked(self) -> None:
        result = self._review(recorder_result=self._recorder_result(send_order_result_status="OTHER"))

        self.assertFalse(result["result_review_ok"])
        self.assertIn(
            "recorder_result.send_order_result_status is not SEND_ORDER_CALLED",
            result["blocked_reasons"],
        )

    def test_updated_order_record_non_dict_is_blocked(self) -> None:
        result = self._review(updated_order_record="invalid")

        self.assertFalse(result["result_review_ok"])
        self.assertEqual("updated_order_record", result["review_stage"])
        self.assertIn("updated_order_record must be a dict", result["blocked_reasons"])

    def test_record_status_mismatch_is_blocked(self) -> None:
        result = self._review(updated_order_record=self._updated_record(status="REAL_READY"))

        self.assertFalse(result["result_review_ok"])
        self.assertIn("updated_order_record.status is not ORDER_QUEUED", result["blocked_reasons"])

    def test_record_send_order_called_false_is_blocked(self) -> None:
        result = self._review(updated_order_record=self._updated_record(send_order_called=False))

        self.assertFalse(result["result_review_ok"])
        self.assertIn("updated_order_record.send_order_called is not true", result["blocked_reasons"])

    def test_record_result_status_mismatch_is_blocked(self) -> None:
        result = self._review(updated_order_record=self._updated_record(send_order_result_status="OTHER"))

        self.assertFalse(result["result_review_ok"])
        self.assertIn(
            "updated_order_record.send_order_result_status is not SEND_ORDER_CALLED",
            result["blocked_reasons"],
        )

    def test_required_record_fields_are_required(self) -> None:
        fields = [
            "send_order_entrypoint_stage",
            "send_order_called_at",
            "send_order_result_recorded_at",
            "broker",
        ]
        for field in fields:
            with self.subTest(field=field):
                result = self._review(updated_order_record=self._updated_record(**{field: ""}))

                self.assertFalse(result["result_review_ok"])
                self.assertIn(f"updated_order_record.{field} is required", result["blocked_reasons"])

    def test_broker_result_missing_is_blocked(self) -> None:
        result = self._review(updated_order_record=self._updated_record(broker_result=None), broker_result=None)

        self.assertFalse(result["result_review_ok"])
        self.assertEqual("broker_result", result["review_stage"])
        self.assertIn("broker_result must be a dict", result["blocked_reasons"])

    def test_identity_mismatches_are_blocked(self) -> None:
        fields = ["order_id", "request_hash", "lock_id", "execution_id"]
        for field in fields:
            with self.subTest(field=field):
                result = self._review(updated_order_record=self._updated_record(**{field: "OTHER"}))

                self.assertFalse(result["result_review_ok"])
                self.assertIn(
                    f"recorder_result.{field} does not match updated_order_record.{field}",
                    result["blocked_reasons"],
                )

    def test_order_queued_id_mismatch_is_blocked(self) -> None:
        result = self._review(updated_order_record=self._updated_record(id="OTHER"))

        self.assertFalse(result["result_review_ok"])
        self.assertIn(
            "recorder_result.order_queued_id does not match updated_order_record.id",
            result["blocked_reasons"],
        )

    def test_broker_result_mismatch_is_blocked(self) -> None:
        result = self._review(broker_result=self._broker_result(request_hash="OTHER"))

        self.assertFalse(result["result_review_ok"])
        self.assertEqual("broker_result", result["review_stage"])
        self.assertIn("updated_order_record.broker_result does not match broker_result", result["blocked_reasons"])

    def test_broker_order_no_missing_warns_but_passes(self) -> None:
        broker_result = self._broker_result()
        broker_result.pop("broker_order_no")
        record = self._updated_record(broker_result=broker_result, broker_order_no=None)

        result = self._review(updated_order_record=record)

        self.assertTrue(result["result_review_ok"])
        self.assertEqual("", result["broker_order_no"])
        self.assertIn(
            "broker_order_no is missing; wait for broker/Chejan confirmation",
            result["warnings"],
        )

    def test_broker_order_no_present_has_no_warning(self) -> None:
        result = self._review()

        self.assertTrue(result["result_review_ok"])
        self.assertEqual("BRK_1", result["broker_order_no"])
        self.assertEqual([], result["warnings"])

    def test_normal_result_review_passes(self) -> None:
        result = self._review()

        self.assertTrue(result["result_review_ok"])
        self.assertEqual("send_order_result_reviewed", result["review_stage"])
        self.assertEqual("CHEJAN_OR_EXECUTION_EVENT_REQUIRED", result["next_stage"])
        self.assertTrue(result["send_order_called"])
        self.assertEqual("SEND_ORDER_CALLED", result["send_order_result_status"])
        self.assertEqual("ORDER_1", result["order_id"])
        self.assertEqual("ORDER_QUEUED_ORDER_1", result["order_queued_id"])
        self.assertEqual("HASH_1", result["request_hash"])
        self.assertEqual("LOCK_1", result["lock_id"])
        self.assertEqual("EXEC_1", result["execution_id"])
        self.assertEqual("MOCK_BROKER", result["broker"])
        self.assertEqual("BRK_1", result["broker_order_no"])
        self.assertEqual([], result["blocked_reasons"])

    def test_broker_result_argument_can_match_core_fields(self) -> None:
        result = self._review(broker_result={"request_hash": "HASH_1", "broker_status": "MOCK_ACCEPTED"})

        self.assertTrue(result["result_review_ok"])

    def test_send_order_runtime_write_and_chejan_are_not_called(self) -> None:
        with (
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = self._review()

        self.assertTrue(result["result_review_ok"])
        send_order_stub.assert_not_called()
        write_text.assert_not_called()
        open_mock.assert_not_called()

    def test_module_does_not_import_runtime_broker_or_gui_timer(self) -> None:
        module_text = send_order_result_review_service.__loader__.get_source(
            send_order_result_review_service.__name__
        )

        self.assertNotIn("kiwoom_order_adapter", module_text)
        self.assertNotIn("dynamicCall", module_text)
        self.assertNotIn("QTimer", module_text)
        self.assertNotIn("QPushButton", module_text)
        self.assertNotIn("ORDER_QUEUE_PATH", module_text)

    def test_input_dicts_are_not_mutated(self) -> None:
        recorder_result = self._recorder_result()
        updated_record = self._updated_record()
        broker_result = self._broker_result()
        originals = (deepcopy(recorder_result), deepcopy(updated_record), deepcopy(broker_result))

        review_send_order_result(recorder_result, updated_record, broker_result=broker_result)

        self.assertEqual(originals[0], recorder_result)
        self.assertEqual(originals[1], updated_record)
        self.assertEqual(originals[2], broker_result)


if __name__ == "__main__":
    unittest.main()
