from __future__ import annotations

from copy import deepcopy
import json
import tempfile
from pathlib import Path
import unittest
from unittest import mock

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
                "symbol": code,
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


class SellQueueDispatchReadinessChainTests(unittest.TestCase):
    def _queue_path(self, records: list[dict]) -> Path:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "order_queue.json"
        path.write_text(json.dumps({"version": 1, "orders": records}, indent=2), encoding="utf-8")
        return path

    def _verifier(self, records: list[dict] | None = None, *, status: str = "READY") -> dict:
        records = [_record()] if records is None else records
        queue_path = self._queue_path(deepcopy(records))
        return {
            "verifier_type": "SELL_RUNTIME_COMMIT_POST_COMMIT_VERIFIER",
            "status": status,
            "post_commit_verified": status == "READY",
            "post_commit_file_verified": status == "READY",
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
            "verified_records": [
                {
                    "order_queue_path": str(queue_path),
                    "record": deepcopy(record),
                }
                for record in records
            ],
            "warnings": ["source warning"],
            "reasons": ["source reason"],
            "summary": {"expected_record_count": len(records), "verified_record_count": len(records)},
        }

    def _review(self, records: list[dict] | None = None) -> dict:
        return build_sell_queue_committed_review(self._verifier(records))

    def _eligibility(self, records: list[dict] | None = None, *, dispatch_context: dict | None = None) -> dict:
        return build_sell_dispatch_eligibility(
            self._review(records),
            {"market_open": True, "lock_available": True, "holdings": {"005931": 100, "005932": 100, "005933": 100}}
            | (dispatch_context or {}),
        )

    def _broker_preview(self, records: list[dict] | None = None) -> dict:
        return build_sell_broker_request_preview(
            self._eligibility(records),
            {"account_no": "12345678", "screen_no": "9001"},
        )

    def _approval(self, records: list[dict] | None = None, *, context: dict | None = None) -> dict:
        preview = self._broker_preview(records)
        approval_context = {
            "user_approved": True,
            "approval_token": "TOKEN_1",
            "approved_candidate_ids": preview["candidate_ids"],
            "account_no": "12345678",
            "queue_path": preview["queue_path"],
        }
        approval_context.update(context or {})
        return build_sell_dispatch_approval_gate(preview, approval_context)

    def assert_no_dispatch_side_effects(self, result: dict):
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["broker_api_called"])
        self.assertFalse(result["actual_order_sent"])
        self.assertFalse(result["order_request_created"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["real_ready_state_changed"])

    def test_single_sell_queue_record_full_chain_ready(self):
        approval = self._approval()

        self.assertEqual(approval["approval_type"], "SELL_DISPATCH_APPROVAL_GATE")
        self.assertEqual(approval["status"], "READY")
        self.assertTrue(approval["approval_granted"])
        self.assertTrue(approval["dispatch_execution_allowed"])
        self.assertEqual(["CANDIDATE_1"], approval["approved_candidate_ids"])
        self.assert_no_dispatch_side_effects(approval)

    def test_multi_two_records_preserve_order(self):
        records = [_record(1), _record(2)]
        approval = self._approval(records)

        self.assertEqual(approval["status"], "READY")
        self.assertEqual(["CANDIDATE_1", "CANDIDATE_2"], approval["approved_candidate_ids"])

    def test_multi_three_records_preserve_order(self):
        records = [_record(1), _record(2), _record(3)]
        approval = self._approval(records)

        self.assertEqual(approval["status"], "READY")
        self.assertEqual(["CANDIDATE_1", "CANDIDATE_2", "CANDIDATE_3"], approval["approved_candidate_ids"])

    def test_queue_record_missing_blocks_review_as_invalid(self):
        records = [_record()]
        verifier = self._verifier(records)
        Path(verifier["verified_records"][0]["order_queue_path"]).write_text(json.dumps({"orders": []}), encoding="utf-8")

        result = build_sell_queue_committed_review(verifier)

        self.assertEqual(result["status"], "INVALID")

    def test_queue_record_duplicate_blocks_review_as_invalid(self):
        records = [_record()]
        verifier = self._verifier(records)
        Path(verifier["verified_records"][0]["order_queue_path"]).write_text(
            json.dumps({"orders": [records[0], records[0]]}),
            encoding="utf-8",
        )

        result = build_sell_queue_committed_review(verifier)

        self.assertEqual(result["status"], "INVALID")

    def test_queue_record_mutation_blocks_review_as_invalid(self):
        records = [_record()]
        verifier = self._verifier(records)
        mutated = deepcopy(records[0])
        mutated["execution_request"]["price"] = 71000
        Path(verifier["verified_records"][0]["order_queue_path"]).write_text(json.dumps({"orders": [mutated]}), encoding="utf-8")

        result = build_sell_queue_committed_review(verifier)

        self.assertEqual(result["status"], "INVALID")

    def test_buy_direction_blocks_dispatch_eligibility(self):
        result = self._eligibility([_record(side="BUY")])

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["dispatch_eligible"])

    def test_zero_quantity_blocks_dispatch_eligibility(self):
        result = self._eligibility([_record(quantity=0)])

        self.assertEqual(result["status"], "BLOCKED")

    def test_negative_quantity_blocks_dispatch_eligibility(self):
        result = self._eligibility([_record(quantity=-1)])

        self.assertEqual(result["status"], "BLOCKED")

    def test_holding_quantity_exceeded_blocks_dispatch_eligibility(self):
        result = self._eligibility([_record(quantity=101)])

        self.assertEqual(result["status"], "BLOCKED")

    def test_emergency_stop_blocks_dispatch_eligibility(self):
        result = self._eligibility(dispatch_context={"emergency_stop": True})

        self.assertEqual(result["status"], "BLOCKED")

    def test_trading_halted_blocks_dispatch_eligibility(self):
        result = self._eligibility(dispatch_context={"trading_halted": True})

        self.assertEqual(result["status"], "BLOCKED")

    def test_market_closed_blocks_dispatch_eligibility(self):
        result = self._eligibility(dispatch_context={"market_open": False})

        self.assertEqual(result["status"], "BLOCKED")

    def test_lock_unavailable_blocks_dispatch_eligibility(self):
        result = self._eligibility(dispatch_context={"lock_available": False})

        self.assertEqual(result["status"], "BLOCKED")

    def test_broker_request_preview_fields(self):
        preview = self._broker_preview()
        payload = preview["broker_request_previews"][0]

        self.assertEqual(preview["preview_type"], "SELL_BROKER_REQUEST_PREVIEW")
        self.assertEqual(preview["status"], "READY")
        self.assertEqual(payload["screen_no"], "9001")
        self.assertEqual(payload["account_no"], "12345678")
        self.assertEqual(payload["order_type"], "SELL")
        self.assertEqual(payload["code"], "005931")
        self.assertEqual(payload["quantity"], 10)
        self.assertEqual(payload["price"], 70000)
        self.assertEqual(payload["hoga"], "LIMIT")
        self.assertEqual(payload["original_order_no"], "")
        self.assert_no_dispatch_side_effects(preview)

    def test_missing_account_blocks_broker_preview_as_invalid(self):
        result = build_sell_broker_request_preview(self._eligibility(), {"screen_no": "9001"})

        self.assertEqual(result["status"], "INVALID")

    def test_approval_missing_blocks(self):
        preview = self._broker_preview()
        result = build_sell_dispatch_approval_gate(
            preview,
            {
                "user_approved": False,
                "approval_token": "TOKEN_1",
                "approved_candidate_ids": preview["candidate_ids"],
                "account_no": "12345678",
                "queue_path": preview["queue_path"],
            },
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["approval_granted"])

    def test_partial_approval_invalid(self):
        preview = self._broker_preview([_record(1), _record(2)])
        result = build_sell_dispatch_approval_gate(
            preview,
            {
                "user_approved": True,
                "approval_token": "TOKEN_1",
                "approved_candidate_ids": ["CANDIDATE_1"],
                "account_no": "12345678",
                "queue_path": preview["queue_path"],
            },
        )

        self.assertEqual(result["status"], "INVALID")

    def test_approval_order_change_invalid(self):
        preview = self._broker_preview([_record(1), _record(2)])
        result = build_sell_dispatch_approval_gate(
            preview,
            {
                "user_approved": True,
                "approval_token": "TOKEN_1",
                "approved_candidate_ids": ["CANDIDATE_2", "CANDIDATE_1"],
                "account_no": "12345678",
                "queue_path": preview["queue_path"],
            },
        )

        self.assertEqual(result["status"], "INVALID")

    def test_account_mismatch_invalid(self):
        result = self._approval(context={"account_no": "87654321"})

        self.assertEqual(result["status"], "INVALID")

    def test_queue_path_mismatch_invalid(self):
        result = self._approval(context={"queue_path": "other.json"})

        self.assertEqual(result["status"], "INVALID")

    def test_approval_rechecks_order_queued_state(self):
        preview = self._broker_preview()
        queue_path = Path(preview["queue_path"])
        data = json.loads(queue_path.read_text(encoding="utf-8"))
        data["orders"][0]["status"] = "DISPATCHED"
        queue_path.write_text(json.dumps(data), encoding="utf-8")

        result = build_sell_dispatch_approval_gate(
            preview,
            {
                "user_approved": True,
                "approval_token": "TOKEN_1",
                "approved_candidate_ids": preview["candidate_ids"],
                "account_no": "12345678",
                "queue_path": preview["queue_path"],
            },
        )

        self.assertEqual(result["status"], "BLOCKED")

    def test_duplicate_identity_blocks_review(self):
        first = _record(1)
        second = _record(2)
        second["request_hash"] = first["request_hash"]

        result = self._review([first, second])

        self.assertEqual(result["status"], "INVALID")

    def test_safety_flag_violation_invalid(self):
        verifier = self._verifier()
        verifier["send_order_called"] = True

        result = build_sell_queue_committed_review(verifier)

        self.assertEqual(result["status"], "INVALID")

    def test_input_mutation_does_not_occur(self):
        verifier = self._verifier([_record(1), _record(2)])
        before = deepcopy(verifier)

        build_sell_queue_committed_review(verifier)

        self.assertEqual(before, verifier)

    def test_existing_modules_are_used_by_orchestration(self):
        record = _record()
        verifier = self._verifier([record])
        existing_queue_review = {
            "review_type": "EXECUTION_QUEUE_COMMITTED_REVIEW",
            "status": "READY_FOR_FINAL_SEND_GATE",
            "preview_only": True,
            "queue_write": False,
            "runtime_write": False,
            "send_order_called": False,
            "next_stage": "FINAL_SEND_GATE_REQUIRED",
            "order_queued_record": deepcopy(record),
            "identity": {
                "order_id": "ORDER_1",
                "source_signal_id": "SIG_1",
                "execution_id": "EXEC_1",
                "request_hash": "a" * 64,
                "lock_id": "LOCK_1",
            },
            "issues": [],
            "warnings": [],
        }
        existing_adapter = {
            "adapter_type": "EXECUTION_QUEUE_REVIEW_TO_SEND_ORDER_PREVIEW_ADAPTER",
            "status": "READY_FOR_FINAL_SEND_GATE",
            "preview_only": True,
            "queue_write": False,
            "runtime_write": False,
            "send_order_called": False,
            "final_send_gate_called": False,
            "adapter_preview_result": {
                "send_order_request_preview": {
                    "order_id": "ORDER_1",
                    "source_signal_id": "SIG_1",
                    "execution_id": "EXEC_1",
                    "request_hash": "a" * 64,
                    "lock_id": "LOCK_1",
                    "account_no": "12345678",
                    "side": "SELL",
                    "code": "005931",
                    "quantity": 10,
                    "price": 70000,
                    "hoga": "LIMIT",
                    "original_order_no": "",
                    "screen_no": "9001",
                }
            },
            "order_queued_record": deepcopy(record),
            "identity": {},
            "issues": [],
            "warnings": [],
        }
        existing_builder = {
            "builder_type": "EXECUTION_ORDER_DISPATCH_BUILDER",
            "status": "DISPATCH_READY",
            "dispatch_contract": {
                "account_no": "12345678",
                "broker_type": "KIWOOM",
                "order_id": "ORDER_1",
                "source_order_id": "ORDER_1",
                "source_signal_id": "SIG_1",
                "code": "005931",
                "side": "SELL",
                "quantity": 10,
                "price": 70000,
                "hoga": "LIMIT",
                "request_hash": "a" * 64,
                "dispatch_id": "DISPATCH_1",
            },
            "issues": [],
            "warnings": [],
            "send_order_ready": True,
            "send_order_called": False,
            "broker_called": False,
        }
        existing_broker = {
            "status": "BROKER_DISPATCH_READY",
            "broker_dispatch_preview": {},
            "send_order_params_preview": {
                "account_no": "12345678",
                "broker_type": "KIWOOM",
                "order_id": "ORDER_1",
                "source_order_id": "ORDER_1",
                "source_signal_id": "SIG_1",
                "code": "005931",
                "side": "SELL",
                "quantity": 10,
                "price": 70000,
                "hoga": "LIMIT",
                "request_hash": "a" * 64,
                "dispatch_id": "DISPATCH_1",
            },
            "issues": [],
            "warnings": [],
            "preview_only": True,
            "broker_called": False,
            "send_order_called": False,
            "runtime_write": False,
            "queue_write": False,
        }

        with mock.patch("sell_queue_dispatch_readiness_chain.review_execution_queue_committed", return_value=existing_queue_review) as review_mock:
            review = build_sell_queue_committed_review(verifier)
        with mock.patch("sell_queue_dispatch_readiness_chain.adapt_queue_review_to_send_order_preview", return_value=existing_adapter) as adapter_mock:
            eligibility = build_sell_dispatch_eligibility(review, {"market_open": True, "lock_available": True, "holding_qty": 100})
        with mock.patch("sell_queue_dispatch_readiness_chain.build_order_dispatch_contract", return_value=existing_builder) as builder_mock:
            with mock.patch("sell_queue_dispatch_readiness_chain.preview_broker_dispatch", return_value=existing_broker) as broker_mock:
                preview = build_sell_broker_request_preview(eligibility, {"account_no": "12345678", "screen_no": "9001"})

        self.assertEqual(preview["status"], "READY")
        self.assertTrue(review_mock.called)
        self.assertTrue(adapter_mock.called)
        self.assertTrue(builder_mock.called)
        self.assertTrue(broker_mock.called)


if __name__ == "__main__":
    unittest.main()
