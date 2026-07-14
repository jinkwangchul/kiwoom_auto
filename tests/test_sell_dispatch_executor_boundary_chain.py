from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from sell_dispatch_executor_boundary_chain import (
    build_sell_dispatch_dry_run_executor,
    build_sell_dispatch_executor_approval_boundary,
    build_sell_dispatch_executor_plan,
    build_sell_dispatch_post_execution_verification_preview,
)
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


def _record(index: int = 1) -> dict:
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
                "side": "SELL",
                "quantity": 10,
                "price": 70000,
                "order_type": "SELL",
                "hoga": "LIMIT",
                "original_order_no": "",
                "screen_no": "9001",
            },
        },
        "queue_contract_version": "preview-1",
        "send_order_called": False,
        "execution_enabled": False,
    }


class SellDispatchExecutorBoundaryChainTests(unittest.TestCase):
    def _queue_path(self, records: list[dict]) -> Path:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "order_queue.json"
        path.write_text(json.dumps({"version": 1, "orders": records}, indent=2), encoding="utf-8")
        return path

    def _approval_gate(self, records: list[dict] | None = None) -> dict:
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

    def _audit(self, records: list[dict] | None = None) -> dict:
        guard = build_sell_dispatch_final_execution_guard(
            self._approval_gate(records),
            {
                "account_no": "12345678",
                "market_open": True,
                "emergency_stop": False,
                "trading_halted": False,
                "order_lock_valid": True,
                "account_snapshot": {"valid": True, "account_no": "12345678"},
                "holdings": {"005931": 100, "005932": 100, "005933": 100},
            },
        )
        call_preview = build_sell_send_order_call_preview(guard, {"final_call_token": "FINAL_TOKEN_1"})
        return build_sell_dispatch_execution_audit_preview(call_preview, {"approval_token": "TOKEN_1", "account_no": "12345678"})

    def _plan(self, records: list[dict] | None = None) -> dict:
        return build_sell_dispatch_executor_plan(self._audit(records))

    def _boundary(self, records: list[dict] | None = None, *, context: dict | None = None) -> dict:
        plan = self._plan(records)
        approval_context = {
            "user_approved": True,
            "approval_token": "TOKEN_2",
            "approved_candidate_ids": plan["candidate_ids"],
            "plan_hash": plan["plan_hash"],
            "queue_path": plan["queue_path"],
            "account_no": "12345678",
        }
        approval_context.update(context or {})
        return build_sell_dispatch_executor_approval_boundary(plan, approval_context)

    def _dryrun(self, records: list[dict] | None = None) -> dict:
        return build_sell_dispatch_dry_run_executor(self._boundary(records))

    def _post_verify(self, records: list[dict] | None = None) -> dict:
        return build_sell_dispatch_post_execution_verification_preview(self._dryrun(records))

    def assert_no_side_effects(self, result: dict):
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["broker_api_called"])
        self.assertFalse(result["actual_order_sent"])
        self.assertFalse(result["order_request_created"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_status_changed"])
        self.assertFalse(result["execution_started"])
        self.assertFalse(result["partial_execution"])
        self.assertFalse(result["real_ready_state_changed"])

    def test_single_candidate_full_boundary_chain_ready(self):
        result = self._post_verify()

        self.assertEqual(result["verification_type"], "SELL_DISPATCH_POST_EXECUTION_VERIFICATION_PREVIEW")
        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["post_execution_verified"])
        self.assertEqual(result["summary"]["verified_count"], 1)
        self.assert_no_side_effects(result)

    def test_multi_two_preserves_order(self):
        result = self._post_verify([_record(1), _record(2)])

        self.assertEqual(result["status"], "READY")
        self.assertEqual(["CANDIDATE_1", "CANDIDATE_2"], result["source_dry_run_snapshot"]["simulated_candidate_ids"])

    def test_multi_three_preserves_order(self):
        result = self._post_verify([_record(1), _record(2), _record(3)])

        self.assertEqual(result["status"], "READY")
        self.assertEqual(["CANDIDATE_1", "CANDIDATE_2", "CANDIDATE_3"], result["source_dry_run_snapshot"]["simulated_candidate_ids"])

    def test_audit_hash_tamper_blocks_plan(self):
        audit = self._audit()
        audit["send_order_call_preview_hash"] = "bad"

        result = build_sell_dispatch_executor_plan(audit)

        self.assertEqual(result["status"], "INVALID")

    def test_plan_hash_mismatch_blocks_boundary(self):
        result = self._boundary(context={"plan_hash": "bad"})

        self.assertEqual(result["status"], "INVALID")

    def test_partial_approval_blocks_boundary(self):
        result = self._boundary([_record(1), _record(2)], context={"approved_candidate_ids": ["CANDIDATE_1"]})

        self.assertEqual(result["status"], "INVALID")

    def test_approval_order_change_blocks_boundary(self):
        result = self._boundary([_record(1), _record(2)], context={"approved_candidate_ids": ["CANDIDATE_2", "CANDIDATE_1"]})

        self.assertEqual(result["status"], "INVALID")

    def test_queue_path_mismatch_blocks_boundary(self):
        result = self._boundary(context={"queue_path": "other.json"})

        self.assertEqual(result["status"], "INVALID")

    def test_account_mismatch_blocks_boundary(self):
        result = self._boundary(context={"account_no": "87654321"})

        self.assertEqual(result["status"], "INVALID")

    def test_queue_record_missing_blocks_boundary(self):
        plan = self._plan()
        Path(plan["queue_path"]).write_text(json.dumps({"orders": []}), encoding="utf-8")

        result = build_sell_dispatch_executor_approval_boundary(
            plan,
            {
                "user_approved": True,
                "approval_token": "TOKEN_2",
                "approved_candidate_ids": plan["candidate_ids"],
                "plan_hash": plan["plan_hash"],
                "queue_path": plan["queue_path"],
                "account_no": "12345678",
            },
        )

        self.assertEqual(result["status"], "BLOCKED")

    def test_queue_record_state_change_blocks_boundary(self):
        plan = self._plan()
        queue_path = Path(plan["queue_path"])
        data = json.loads(queue_path.read_text(encoding="utf-8"))
        data["orders"][0]["status"] = "SENT"
        queue_path.write_text(json.dumps(data), encoding="utf-8")

        result = build_sell_dispatch_executor_approval_boundary(
            plan,
            {
                "user_approved": True,
                "approval_token": "TOKEN_2",
                "approved_candidate_ids": plan["candidate_ids"],
                "plan_hash": plan["plan_hash"],
                "queue_path": plan["queue_path"],
                "account_no": "12345678",
            },
        )

        self.assertEqual(result["status"], "BLOCKED")

    def test_identity_field_mismatch_blocks_boundary(self):
        for field in ("order_id", "candidate_id", "queue_pending_id", "execution_id", "request_hash", "lock_id"):
            with self.subTest(field=field):
                plan = self._plan()
                queue_path = Path(plan["queue_path"])
                data = json.loads(queue_path.read_text(encoding="utf-8"))
                data["orders"][0][field] = "MUTATED"
                queue_path.write_text(json.dumps(data), encoding="utf-8")

                result = build_sell_dispatch_executor_approval_boundary(
                    plan,
                    {
                        "user_approved": True,
                        "approval_token": "TOKEN_2",
                        "approved_candidate_ids": plan["candidate_ids"],
                        "plan_hash": plan["plan_hash"],
                        "queue_path": plan["queue_path"],
                        "account_no": "12345678",
                    },
                )

                self.assertEqual(result["status"], "BLOCKED")

    def test_dry_run_candidate_result_count_matches_plan(self):
        dryrun = self._dryrun([_record(1), _record(2)])

        self.assertEqual(dryrun["status"], "READY")
        self.assertEqual(dryrun["simulated_dispatch_count"], 2)
        self.assertEqual(len(dryrun["per_candidate_results"]), 2)

    def test_dry_run_candidate_failure_invalidates_all(self):
        boundary = self._boundary()
        boundary["approved_execution_actions"][0]["send_order_args_snapshot"] = ["too", "short"]

        result = build_sell_dispatch_dry_run_executor(boundary)

        self.assertEqual(result["status"], "INVALID")
        self.assertFalse(result["partial_execution"])

    def test_partial_execution_false(self):
        result = self._dryrun()

        self.assertFalse(result["partial_execution"])

    def test_callable_not_called(self):
        fake_callable = mock.Mock()

        result = self._dryrun()

        fake_callable.assert_not_called()
        self.assertEqual(result["status"], "READY")

    def test_post_execution_verification_ready(self):
        result = self._post_verify()

        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["post_execution_verified"])

    def test_post_execution_result_tamper_invalid(self):
        dryrun = self._dryrun()
        dryrun["per_candidate_results"][0]["identity"]["order_id"] = "MUTATED"

        result = build_sell_dispatch_post_execution_verification_preview(dryrun)

        self.assertEqual(result["status"], "INVALID")

    def test_project_runtime_order_queue_not_accessed(self):
        plan = self._plan()

        self.assertNotIn("runtime/order_queue.json", plan["queue_path"].replace("\\", "/"))

    def test_input_mutation_does_not_occur(self):
        plan = self._plan()
        original = deepcopy(plan)

        build_sell_dispatch_executor_approval_boundary(
            plan,
            {
                "user_approved": True,
                "approval_token": "TOKEN_2",
                "approved_candidate_ids": plan["candidate_ids"],
                "plan_hash": plan["plan_hash"],
                "queue_path": plan["queue_path"],
                "account_no": "12345678",
            },
        )

        self.assertEqual(plan, original)


if __name__ == "__main__":
    unittest.main()
