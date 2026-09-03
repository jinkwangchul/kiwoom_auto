# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
import unittest
from unittest import mock

from execution_candidate_service import build_execution_candidate


class ExecutionCandidateServiceTest(unittest.TestCase):
    def _preview_result(self) -> dict:
        return {
            "ok": True,
            "stage": "EXECUTION_PREVIEW_SERVICE",
            "summary": {
                "ok": True,
                "order_id": "ORDER_1",
                "request_hash": "a" * 64,
            },
            "pipeline_result": {
                "pipeline": {
                    "lock_preview": {
                        "ok": True,
                        "lock_id": "LOCK_PREVIEW_ORDER_1_003550_BUY_SIG_1",
                    },
                    "request_hash_preview": {
                        "ok": True,
                        "request_hash": "a" * 64,
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
            },
        }

    def _approval_result(self) -> dict:
        return {
            "approved": True,
            "approval_stage": "approved",
            "blocked_reasons": [],
            "next_stage": "EXECUTION_CANDIDATE",
        }

    def test_approval_not_approved_is_blocked(self) -> None:
        approval_result = self._approval_result()
        approval_result["approved"] = False

        result = build_execution_candidate(self._preview_result(), approval_result)

        self.assertFalse(result["candidate"])
        self.assertEqual("approval", result["candidate_stage"])
        self.assertEqual("BLOCKED", result["next_stage"])
        self.assertIn("approval_result.approved is not true", result["blocked_reasons"])

    def test_approval_next_stage_mismatch_is_blocked(self) -> None:
        approval_result = self._approval_result()
        approval_result["next_stage"] = "BLOCKED"

        result = build_execution_candidate(self._preview_result(), approval_result)

        self.assertFalse(result["candidate"])
        self.assertEqual("approval", result["candidate_stage"])
        self.assertIn(
            "approval_result.next_stage is not EXECUTION_CANDIDATE",
            result["blocked_reasons"],
        )

    def test_preview_not_ok_is_blocked(self) -> None:
        preview_result = self._preview_result()
        preview_result["ok"] = False

        result = build_execution_candidate(preview_result, self._approval_result())

        self.assertFalse(result["candidate"])
        self.assertEqual("preview_result", result["candidate_stage"])
        self.assertIn("preview_result.ok is not true", result["blocked_reasons"])

    def test_request_hash_preview_missing_is_blocked(self) -> None:
        preview_result = self._preview_result()
        preview_result["pipeline_result"]["pipeline"].pop("request_hash_preview")

        result = build_execution_candidate(preview_result, self._approval_result())

        self.assertFalse(result["candidate"])
        self.assertEqual("preview_result", result["candidate_stage"])
        self.assertIn("request_hash_preview is required", result["blocked_reasons"])

    def test_lock_preview_missing_is_blocked(self) -> None:
        preview_result = self._preview_result()
        preview_result["pipeline_result"]["pipeline"].pop("lock_preview")

        result = build_execution_candidate(preview_result, self._approval_result())

        self.assertFalse(result["candidate"])
        self.assertEqual("preview_result", result["candidate_stage"])
        self.assertIn("lock_preview is required", result["blocked_reasons"])

    def test_execution_request_preview_missing_is_blocked(self) -> None:
        preview_result = self._preview_result()
        preview_result["pipeline_result"]["pipeline"].pop("execution_request_preview")

        result = build_execution_candidate(preview_result, self._approval_result())

        self.assertFalse(result["candidate"])
        self.assertEqual("preview_result", result["candidate_stage"])
        self.assertIn("execution_request_preview is required", result["blocked_reasons"])

    def test_all_conditions_met_creates_candidate(self) -> None:
        result = build_execution_candidate(self._preview_result(), self._approval_result())

        self.assertTrue(result["candidate"])
        self.assertEqual("candidate_created", result["candidate_stage"])
        self.assertEqual("QUEUE_PENDING", result["next_stage"])
        self.assertEqual([], result["blocked_reasons"])
        self.assertEqual("ORDER_1", result["order_id"])
        self.assertEqual("SIG_1", result["source_signal_id"])
        self.assertEqual("a" * 64, result["request_hash_preview"])
        self.assertIn("lock_preview", result)
        self.assertIn("execution_request_preview", result)

    def test_candidate_id_is_stable(self) -> None:
        first = build_execution_candidate(self._preview_result(), self._approval_result())
        second = build_execution_candidate(self._preview_result(), self._approval_result())

        self.assertEqual("EXEC_CANDIDATE_ORDER_1", first["candidate_id"])
        self.assertEqual(first["candidate_id"], second["candidate_id"])

    def test_candidate_true_does_not_call_send_order_or_write_runtime(self) -> None:
        with (
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
            mock.patch("kiwoom_order_adapter.send_order_stub") as send_order_stub,
        ):
            result = build_execution_candidate(self._preview_result(), self._approval_result())

        self.assertTrue(result["candidate"])
        write_text.assert_not_called()
        open_mock.assert_not_called()
        send_order_stub.assert_not_called()

    def test_input_dicts_are_not_mutated(self) -> None:
        preview_result = self._preview_result()
        approval_result = self._approval_result()
        context = {"note": "candidate preview only"}
        original_preview = deepcopy(preview_result)
        original_approval = deepcopy(approval_result)
        original_context = deepcopy(context)

        build_execution_candidate(preview_result, approval_result, context)

        self.assertEqual(original_preview, preview_result)
        self.assertEqual(original_approval, approval_result)
        self.assertEqual(original_context, context)

    def test_preplanned_process_child_contract_is_preserved(self) -> None:
        preview = self._preview_result()
        request = preview["pipeline_result"]["pipeline"]["execution_request_preview"]["execution_request"]
        request.update(
            {
                "execution_id": "EXEC_CHILD_PROCESS_001",
                "execution_process_id": "EXEC_PROCESS_MULTI_1",
                "child_sequence_index": 1,
                "child_sequence_total": 5,
                "child_kind": "HOGA_LEVEL",
                "child_plan": {
                    "planned_quantity": 3,
                    "planned_price": 81_000,
                    "hoga_offset_ticks": 0,
                },
                "execution_intent": {
                    "side": "SELL",
                    "routine_type": "INDICATOR_FOLLOW",
                    "routine_instance_id": "INSTANCE-1",
                    "source_signal_id": "SIG_1",
                    "execution_mode": "MULTI_HOGA",
                    "execution_process_owner_required": True,
                    "provenance_approved_at": "2026-09-02T10:00:00+09:00",
                    "planned_total_quantity": 13,
                    "hoga_mode": "MULTI",
                    "hoga_up": 2,
                    "hoga_down": 2,
                    "quantity": 3,
                    "price_basis": "ORDER_PRICE",
                    "price": 81_000,
                    "hoga": "LIMIT",
                },
            }
        )

        result = build_execution_candidate(preview, self._approval_result())

        self.assertTrue(result["candidate"], result)
        self.assertEqual("EXEC_PROCESS_MULTI_1", result["execution_process_id"])
        self.assertEqual("EXEC_CHILD_PROCESS_001", result["child_contract"]["execution_id"])
        self.assertEqual("HOGA_LEVEL", result["child_contract"]["child_kind"])
        self.assertEqual(1, result["child_contract"]["child_sequence_index"])
        self.assertEqual(5, result["child_contract"]["child_sequence_total"])
        self.assertEqual(0, result["child_contract"]["child_plan"]["hoga_offset_ticks"])
        self.assertEqual(13, result["process_record"]["process_plan"]["planned_total_quantity"])
        self.assertEqual(
            "MULTI_HOGA",
            result["process_record"]["option_snapshot"]["execution_mode"],
        )


if __name__ == "__main__":
    unittest.main()
