# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import unittest
from unittest import mock

from execution_queue_pending_service import build_execution_queue_pending


class ExecutionQueuePendingServiceTest(unittest.TestCase):
    def _candidate_result(self) -> dict:
        return {
            "candidate": True,
            "candidate_stage": "candidate_created",
            "candidate_id": "EXEC_CANDIDATE_ORDER_1",
            "next_stage": "QUEUE_PENDING",
            "blocked_reasons": [],
            "order_id": "ORDER_1",
            "source_signal_id": "SIG_1",
            "request_hash_preview": "a" * 64,
            "lock_preview": {
                "ok": True,
                "lock_id": "LOCK_PREVIEW_ORDER_1_003550_BUY_SIG_1",
            },
            "execution_request_preview": {
                "ok": True,
                "execution_request": {
                    "execution_id": "EXEC_PREVIEW_ORDER_1",
                    "order_id": "ORDER_1",
                    "source_signal_id": "SIG_1",
                    "lock_id": "LOCK_PREVIEW_ORDER_1_003550_BUY_SIG_1",
                    "request_hash": "a" * 64,
                },
            },
        }

    def test_candidate_false_is_blocked(self) -> None:
        candidate_result = self._candidate_result()
        candidate_result["candidate"] = False

        result = build_execution_queue_pending(candidate_result)

        self.assertFalse(result["queue_pending"])
        self.assertEqual("candidate", result["queue_pending_stage"])
        self.assertEqual("BLOCKED", result["next_stage"])
        self.assertTrue(result["preview_only"])
        self.assertTrue(result["no_write"])
        self.assertIn("candidate_result.candidate is not true", result["blocked_reasons"])

    def test_candidate_stage_mismatch_is_blocked(self) -> None:
        candidate_result = self._candidate_result()
        candidate_result["candidate_stage"] = "candidate_pending"

        result = build_execution_queue_pending(candidate_result)

        self.assertFalse(result["queue_pending"])
        self.assertIn(
            "candidate_result.candidate_stage is not candidate_created",
            result["blocked_reasons"],
        )

    def test_next_stage_mismatch_is_blocked(self) -> None:
        candidate_result = self._candidate_result()
        candidate_result["next_stage"] = "BLOCKED"

        result = build_execution_queue_pending(candidate_result)

        self.assertFalse(result["queue_pending"])
        self.assertIn("candidate_result.next_stage is not QUEUE_PENDING", result["blocked_reasons"])

    def test_candidate_id_missing_is_blocked(self) -> None:
        candidate_result = self._candidate_result()
        candidate_result["candidate_id"] = ""

        result = build_execution_queue_pending(candidate_result)

        self.assertFalse(result["queue_pending"])
        self.assertIn("candidate_id is required", result["blocked_reasons"])

    def test_order_id_missing_is_blocked(self) -> None:
        candidate_result = self._candidate_result()
        candidate_result["order_id"] = ""

        result = build_execution_queue_pending(candidate_result)

        self.assertFalse(result["queue_pending"])
        self.assertIn("order_id is required", result["blocked_reasons"])

    def test_source_signal_id_missing_is_blocked(self) -> None:
        candidate_result = self._candidate_result()
        candidate_result["source_signal_id"] = ""

        result = build_execution_queue_pending(candidate_result)

        self.assertFalse(result["queue_pending"])
        self.assertIn("source_signal_id is required", result["blocked_reasons"])

    def test_request_hash_preview_missing_is_blocked(self) -> None:
        candidate_result = self._candidate_result()
        candidate_result["request_hash_preview"] = ""

        result = build_execution_queue_pending(candidate_result)

        self.assertFalse(result["queue_pending"])
        self.assertIn("request_hash_preview is required", result["blocked_reasons"])

    def test_lock_preview_lock_id_missing_is_blocked(self) -> None:
        candidate_result = self._candidate_result()
        candidate_result["lock_preview"]["lock_id"] = ""

        result = build_execution_queue_pending(candidate_result)

        self.assertFalse(result["queue_pending"])
        self.assertIn("lock_preview.lock_id is required", result["blocked_reasons"])

    def test_execution_request_preview_missing_is_blocked(self) -> None:
        candidate_result = self._candidate_result()
        candidate_result.pop("execution_request_preview")

        result = build_execution_queue_pending(candidate_result)

        self.assertFalse(result["queue_pending"])
        self.assertIn("execution_request_preview is required", result["blocked_reasons"])

    def test_execution_request_missing_is_blocked(self) -> None:
        candidate_result = self._candidate_result()
        candidate_result["execution_request_preview"].pop("execution_request")

        result = build_execution_queue_pending(candidate_result)

        self.assertFalse(result["queue_pending"])
        self.assertIn(
            "execution_request_preview.execution_request is required",
            result["blocked_reasons"],
        )

    def test_execution_request_execution_id_missing_is_blocked(self) -> None:
        candidate_result = self._candidate_result()
        candidate_result["execution_request_preview"]["execution_request"]["execution_id"] = ""

        result = build_execution_queue_pending(candidate_result)

        self.assertFalse(result["queue_pending"])
        self.assertIn("execution_request.execution_id is required", result["blocked_reasons"])

    def test_execution_request_request_hash_missing_is_blocked(self) -> None:
        candidate_result = self._candidate_result()
        candidate_result["execution_request_preview"]["execution_request"]["request_hash"] = ""

        result = build_execution_queue_pending(candidate_result)

        self.assertFalse(result["queue_pending"])
        self.assertIn("execution_request.request_hash is required", result["blocked_reasons"])

    def test_execution_request_lock_id_missing_is_blocked(self) -> None:
        candidate_result = self._candidate_result()
        candidate_result["execution_request_preview"]["execution_request"]["lock_id"] = ""

        result = build_execution_queue_pending(candidate_result)

        self.assertFalse(result["queue_pending"])
        self.assertIn("execution_request.lock_id is required", result["blocked_reasons"])

    def test_all_conditions_met_creates_queue_pending(self) -> None:
        result = build_execution_queue_pending(self._candidate_result())

        self.assertTrue(result["queue_pending"])
        self.assertEqual("queue_pending_created", result["queue_pending_stage"])
        self.assertEqual("QUEUE_PENDING_EXEC_CANDIDATE_ORDER_1", result["queue_pending_id"])
        self.assertEqual("EXEC_CANDIDATE_ORDER_1", result["created_from_candidate_id"])
        self.assertEqual("preview-1", result["queue_contract_version"])
        self.assertEqual("QUEUE_WRITER_REQUIRED", result["next_stage"])
        self.assertEqual([], result["blocked_reasons"])
        self.assertEqual([], result["warnings"])
        self.assertEqual("ORDER_1", result["order_id"])
        self.assertEqual("SIG_1", result["source_signal_id"])
        self.assertEqual("a" * 64, result["request_hash_preview"])

    def test_queue_pending_id_is_stable(self) -> None:
        first = build_execution_queue_pending(self._candidate_result())
        second = build_execution_queue_pending(self._candidate_result())

        self.assertEqual("QUEUE_PENDING_EXEC_CANDIDATE_ORDER_1", first["queue_pending_id"])
        self.assertEqual(first["queue_pending_id"], second["queue_pending_id"])

    def test_preview_only_and_no_write_flags_are_preserved_on_success(self) -> None:
        result = build_execution_queue_pending(self._candidate_result())

        self.assertTrue(result["preview_only"])
        self.assertTrue(result["no_write"])

    def test_success_does_not_call_send_order_or_write_runtime(self) -> None:
        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
        ):
            result = build_execution_queue_pending(self._candidate_result())

        self.assertTrue(result["queue_pending"])
        write_text.assert_not_called()
        open_mock.assert_not_called()
        send_order_stub.assert_not_called()

    def test_input_dict_is_not_mutated(self) -> None:
        candidate_result = self._candidate_result()
        context = {"note": "queue pending preview only"}
        original_candidate = deepcopy(candidate_result)
        original_context = deepcopy(context)

        build_execution_queue_pending(candidate_result, context)

        self.assertEqual(original_candidate, candidate_result)
        self.assertEqual(original_context, context)


if __name__ == "__main__":
    unittest.main()
