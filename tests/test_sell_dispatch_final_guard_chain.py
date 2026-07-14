from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from sell_dispatch_final_guard_chain import (
    build_sell_dispatch_execution_audit_preview,
    build_sell_dispatch_final_execution_guard,
    build_sell_send_order_call_preview,
)
from sell_queue_dispatch_readiness_chain import (
    build_sell_broker_request_preview,
    build_sell_dispatch_approval_gate,
    build_sell_dispatch_eligibility,
    build_sell_queue_committed_review,
)


def _record(index: int = 1, *, side: str = "SELL", quantity: int = 10, price: int = 70000) -> dict:
    order_id = f"ORDER_{index}"
    candidate_id = f"CANDIDATE_{index}"
    queue_pending_id = f"QUEUE_PENDING_{index}"
    execution_id = f"EXEC_{index}"
    request_hash = chr(96 + index) * 64
    lock_id = f"LOCK_{index}"
    code = f"00593{index}"
    return {
        "id": f"ORDER_QUEUED_{order_id}",
        "status": "ORDER_QUEUED",
        "source": "execution_queue_pending",
        "source_signal_id": "SIG_1",
        "order_id": order_id,
        "candidate_id": candidate_id,
        "queue_pending_id": queue_pending_id,
        "request_hash": request_hash,
        "lock_id": lock_id,
        "execution_id": execution_id,
        "execution_request": {
            "source_signal_id": "SIG_1",
            "order_id": order_id,
            "candidate_id": candidate_id,
            "queue_pending_id": queue_pending_id,
            "execution_id": execution_id,
            "request_hash": request_hash,
            "lock_id": lock_id,
            "guard_snapshot": {"account_no": "12345678"},
            "request_preview": {
                "account_no": "12345678",
                "code": code,
                "side": side,
                "quantity": quantity,
                "price": price,
                "order_type": side,
                "hoga": "LIMIT",
                "original_order_no": "",
                "screen_no": "9001",
            },
        },
        "queue_contract_version": "preview-1",
        "send_order_called": False,
        "execution_enabled": False,
    }


class SellDispatchFinalGuardChainTests(unittest.TestCase):
    def _queue_path(self, records: list[dict]) -> Path:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "order_queue.json"
        path.write_text(json.dumps({"version": 1, "orders": records}, indent=2), encoding="utf-8")
        return path

    def _approval(self, records: list[dict] | None = None) -> dict:
        records = [_record()] if records is None else records
        queue_path = self._queue_path(deepcopy(records))
        verifier = {
            "verifier_type": "SELL_RUNTIME_COMMIT_POST_COMMIT_VERIFIER",
            "status": "READY",
            "post_commit_verified": True,
            "post_commit_file_verified": True,
            "preview_only": True,
            "execution_connected": False,
            "runtime_write": False,
            "queue_write": False,
            "file_write": False,
            "send_order_called": False,
            "send_order": False,
            "broker_api_called": False,
            "actual_order_sent": False,
            "order_request_created": False,
            "real_ready_state_changed": False,
            "verified_records": [{"order_queue_path": str(queue_path), "record": deepcopy(record)} for record in records],
            "warnings": [],
            "reasons": [],
        }
        review = build_sell_queue_committed_review(verifier)
        eligibility = build_sell_dispatch_eligibility(
            review,
            {"market_open": True, "lock_available": True, "holdings": {"005931": 100, "005932": 100, "005933": 100}},
        )
        broker_preview = build_sell_broker_request_preview(eligibility, {"account_no": "12345678", "screen_no": "9001"})
        approval = build_sell_dispatch_approval_gate(
            broker_preview,
            {
                "user_approved": True,
                "approval_token": "TOKEN_1",
                "approved_candidate_ids": broker_preview["candidate_ids"],
                "account_no": "12345678",
                "queue_path": broker_preview["queue_path"],
            },
        )
        approval["approval_token"] = "TOKEN_1"
        return approval

    def _guard(self, records: list[dict] | None = None, *, context: dict | None = None) -> dict:
        guard_context = {
            "approval_token": "TOKEN_1",
            "account_no": "12345678",
            "market_open": True,
            "emergency_stop": False,
            "trading_halted": False,
            "order_lock_valid": True,
            "account_snapshot": {"valid": True, "account_no": "12345678"},
            "holdings": {"005931": 100, "005932": 100, "005933": 100},
        }
        guard_context.update(context or {})
        return build_sell_dispatch_final_execution_guard(self._approval(records), guard_context)

    def _call_preview(self, records: list[dict] | None = None) -> dict:
        return build_sell_send_order_call_preview(self._guard(records), {"final_call_token": "FINAL_TOKEN_1"})

    def _audit(self, records: list[dict] | None = None) -> dict:
        return build_sell_dispatch_execution_audit_preview(
            self._call_preview(records),
            {"approval_token": "TOKEN_1", "account_no": "12345678"},
        )

    def assert_no_side_effects(self, result: dict):
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["broker_api_called"])
        self.assertFalse(result["actual_order_sent"])
        self.assertFalse(result["order_request_created"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_status_changed"])
        self.assertFalse(result["real_ready_state_changed"])

    def test_single_candidate_guard_ready(self):
        result = self._guard()

        self.assertEqual(result["guard_type"], "SELL_DISPATCH_FINAL_EXECUTION_GUARD")
        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["final_guard_ready"])
        self.assertEqual(["CANDIDATE_1"], result["candidate_ids"])
        self.assert_no_side_effects(result)

    def test_multi_two_preserves_order(self):
        result = self._guard([_record(1), _record(2)])

        self.assertEqual(result["status"], "READY")
        self.assertEqual(["CANDIDATE_1", "CANDIDATE_2"], result["candidate_ids"])

    def test_multi_three_preserves_order(self):
        result = self._guard([_record(1), _record(2), _record(3)])

        self.assertEqual(result["status"], "READY")
        self.assertEqual(["CANDIDATE_1", "CANDIDATE_2", "CANDIDATE_3"], result["candidate_ids"])

    def test_missing_approval_token_blocks(self):
        approval = self._approval()
        approval["approval_token"] = ""
        result = build_sell_dispatch_final_execution_guard(
            approval,
            {"account_no": "12345678", "market_open": True},
        )

        self.assertEqual(result["status"], "BLOCKED")

    def test_gate_token_and_matching_context_token_ready(self):
        result = self._guard(context={"approval_token": "TOKEN_1"})

        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["approval_token_present"])
        self.assertNotIn("TOKEN_1", json.dumps(result, ensure_ascii=False))

    def test_gate_token_and_mutated_context_token_invalid(self):
        result = self._guard(context={"approval_token": "TOKEN_MUTATED"})

        self.assertEqual(result["status"], "INVALID")

    def test_context_token_omitted_uses_gate_token(self):
        result = self._guard(context={"approval_token": None})

        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["approval_token_present"])

    def test_queue_record_missing_blocks(self):
        approval = self._approval()
        Path(approval["queue_path"]).write_text(json.dumps({"orders": []}), encoding="utf-8")

        result = build_sell_dispatch_final_execution_guard(
            approval,
            {"approval_token": "TOKEN_1", "account_no": "12345678", "market_open": True},
        )

        self.assertEqual(result["status"], "BLOCKED")

    def test_queue_record_mutation_blocks(self):
        approval = self._approval()
        queue_path = Path(approval["queue_path"])
        data = json.loads(queue_path.read_text(encoding="utf-8"))
        data["orders"][0]["status"] = "DISPATCHED"
        queue_path.write_text(json.dumps(data), encoding="utf-8")

        result = build_sell_dispatch_final_execution_guard(
            approval,
            {"approval_token": "TOKEN_1", "account_no": "12345678", "market_open": True},
        )

        self.assertEqual(result["status"], "BLOCKED")

    def test_emergency_stop_blocks(self):
        result = self._guard(context={"emergency_stop": True})

        self.assertEqual(result["status"], "BLOCKED")

    def test_trading_halted_blocks(self):
        result = self._guard(context={"trading_halted": True})

        self.assertEqual(result["status"], "BLOCKED")

    def test_market_closed_blocks(self):
        result = self._guard(context={"market_open": False})

        self.assertEqual(result["status"], "BLOCKED")

    def test_order_lock_mismatch_blocks(self):
        result = self._guard(context={"order_lock_valid": False})

        self.assertEqual(result["status"], "BLOCKED")

    def test_holding_quantity_shortage_blocks(self):
        result = self._guard([_record(quantity=101)])

        self.assertEqual(result["status"], "BLOCKED")

    def test_account_mismatch_blocks(self):
        result = self._guard(context={"account_snapshot": {"valid": True, "account_no": "99999999"}})

        self.assertEqual(result["status"], "BLOCKED")

    def test_one_failed_candidate_blocks_all(self):
        result = self._guard([_record(1), _record(2, quantity=101)])

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["final_guard_ready"])

    def test_send_order_call_preview_fields(self):
        result = self._call_preview()
        call = result["call_previews"][0]

        self.assertEqual(result["preview_type"], "SELL_SEND_ORDER_CALL_PREVIEW")
        self.assertEqual(result["status"], "READY")
        self.assertEqual(call["function_boundary"], "kiwoom.SendOrder")
        self.assertEqual(call["rq_name"], "SELL")
        self.assertEqual(call["screen_no"], "9001")
        self.assertEqual(call["account_no"], "12345678")
        self.assertEqual(call["order_type"], 2)
        self.assertEqual(call["code"], "005931")
        self.assertEqual(call["quantity"], 10)
        self.assertEqual(call["price"], 70000)
        self.assertEqual(call["hoga"], "00")
        self.assertEqual(call["original_order_no"], "")
        self.assert_no_side_effects(result)

    def test_send_order_callable_not_executed_and_existing_preview_used(self):
        with mock.patch("sell_dispatch_final_guard_chain.preview_kiwoom_send_order_call") as preview_mock:
            preview_mock.return_value = {
                "status": "SEND_ORDER_CALL_READY",
                "send_order_call_preview": {
                    "send_order_params": {
                        "screen_no": "9001",
                        "order_name": "SELL",
                        "account_no": "12345678",
                        "order_type": 2,
                        "code": "005931",
                        "quantity": 10,
                        "price": 70000,
                        "hoga": "00",
                        "original_order_no": "",
                    }
                },
                "send_order_args": ["9001", "SELL", "12345678", 2, "005931", 10, 70000, "00", ""],
                "issues": [],
                "warnings": [],
                "send_order_called": False,
                "broker_called": False,
                "runtime_write": False,
                "queue_write": False,
            }

            result = build_sell_send_order_call_preview(self._guard(), {"final_call_token": "FINAL_TOKEN_1"})

        self.assertEqual(result["status"], "READY")
        self.assertEqual(preview_mock.call_count, 1)
        self.assert_no_side_effects(result)

    def test_audit_preview_hash_stable(self):
        call_preview = self._call_preview()
        first = build_sell_dispatch_execution_audit_preview(
            call_preview,
            {"approval_token": "TOKEN_1", "account_no": "12345678"},
        )
        second = build_sell_dispatch_execution_audit_preview(
            call_preview,
            {"approval_token": "TOKEN_1", "account_no": "12345678"},
        )

        self.assertEqual(first["audit_type"], "SELL_DISPATCH_EXECUTION_AUDIT_PREVIEW")
        self.assertEqual(first["status"], "READY")
        self.assertEqual(first["send_order_call_preview_hash"], second["send_order_call_preview_hash"])
        self.assertEqual(first["expected_dispatch_count"], 1)
        self.assertTrue(first["execution_not_started"])

    def test_account_no_masked_in_audit(self):
        result = self._audit()

        self.assertEqual(result["masked_account_no"], "12****78")
        self.assertNotEqual(result["masked_account_no"], "12345678")

    def test_audit_actual_order_and_broker_false(self):
        result = self._audit()

        self.assertFalse(result["actual_order_sent"])
        self.assertFalse(result["broker_api_called"])
        self.assert_no_side_effects(result)

    def test_project_runtime_order_queue_not_accessed(self):
        result = self._guard()

        self.assertNotIn("runtime/order_queue.json", result["queue_path"].replace("\\", "/"))

    def test_input_mutation_does_not_occur(self):
        approval = self._approval()
        original = deepcopy(approval)

        build_sell_dispatch_final_execution_guard(
            approval,
            {"approval_token": "TOKEN_1", "account_no": "12345678", "market_open": True},
        )

        self.assertEqual(original, approval)


if __name__ == "__main__":
    unittest.main()
