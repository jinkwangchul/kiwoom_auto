from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

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
from sell_real_dispatch_readiness import build_sell_real_dispatch_readiness


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


class SellRealDispatchReadinessTests(unittest.TestCase):
    def _queue_path(self, records: list[dict]) -> Path:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "order_queue.json"
        path.write_text(json.dumps({"version": 1, "orders": records}, indent=2), encoding="utf-8")
        return path

    def _chain(self, records: list[dict] | None = None) -> dict:
        records = [_record()] if records is None else records
        queue_path = self._queue_path(deepcopy(records))
        post_commit = {
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
        queue_review = build_sell_queue_committed_review(post_commit)
        eligibility = build_sell_dispatch_eligibility(
            queue_review,
            {"market_open": True, "lock_available": True, "holdings": {"005931": 100, "005932": 100, "005933": 100}},
        )
        broker = build_sell_broker_request_preview(eligibility, {"account_no": "12345678", "screen_no": "9001"})
        approval = build_sell_dispatch_approval_gate(
            broker,
            {
                "user_approved": True,
                "approval_token": "TOKEN_1",
                "approved_candidate_ids": broker["candidate_ids"],
                "account_no": "12345678",
                "queue_path": broker["queue_path"],
            },
        )
        approval["approval_token"] = "TOKEN_1"
        guard = build_sell_dispatch_final_execution_guard(
            approval,
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
        audit = build_sell_dispatch_execution_audit_preview(call_preview, {"approval_token": "TOKEN_1", "account_no": "12345678"})
        plan = build_sell_dispatch_executor_plan(audit)
        boundary = build_sell_dispatch_executor_approval_boundary(
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
        dry_run = build_sell_dispatch_dry_run_executor(boundary)
        post_execution = build_sell_dispatch_post_execution_verification_preview(dry_run)
        return {
            "post_commit": post_commit,
            "queue_review": queue_review,
            "eligibility": eligibility,
            "broker": broker,
            "approval": approval,
            "guard": guard,
            "call_preview": call_preview,
            "audit": audit,
            "plan": plan,
            "boundary": boundary,
            "dry_run": dry_run,
            "post_execution": post_execution,
        }

    def _readiness(self, records: list[dict] | None = None, **overrides):
        chain = self._chain(records)
        chain.update(overrides)
        return build_sell_real_dispatch_readiness(
            chain["post_execution"],
            post_commit_verifier=chain["post_commit"],
            queue_committed_review=chain["queue_review"],
            dispatch_eligibility=chain["eligibility"],
            broker_request_preview=chain["broker"],
            dispatch_approval_gate=chain["approval"],
            final_guard=chain["guard"],
            send_order_call_preview=chain["call_preview"],
            dispatch_audit_preview=chain["audit"],
            executor_plan=chain["plan"],
            executor_approval_boundary=chain["boundary"],
            dry_run_executor=chain["dry_run"],
            **{k: v for k, v in overrides.items() if k == "recovery_post_check"},
        )

    def assert_no_side_effects(self, result):
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["broker_api_called"])
        self.assertFalse(result["actual_order_sent"])
        self.assertFalse(result["execution_started"])
        self.assertFalse(result["queue_write"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_status_changed"])
        self.assertFalse(result["order_request_created"])

    def test_single_ready(self):
        result = self._readiness()

        self.assertEqual(result["readiness_type"], "SELL_REAL_DISPATCH_READINESS")
        self.assertEqual(result["status"], "READY")
        self.assertTrue(result["real_dispatch_ready"])
        self.assertEqual(result["candidate_count"], 1)
        self.assertTrue(result["readiness_hash"])
        self.assertTrue(result["chain_hash"])
        self.assert_no_side_effects(result)

    def test_multi_ready(self):
        result = self._readiness([_record(1), _record(2), _record(3)])

        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["candidate_count"], 3)
        self.assertEqual(["CANDIDATE_1", "CANDIDATE_2", "CANDIDATE_3"], [item["candidate_id"] for item in result["candidate_identity_order"]])

    def test_upstream_invalid_blocks_final_status(self):
        chain = self._chain()
        chain["guard"]["status"] = "INVALID"
        result = self._readiness(**chain)

        self.assertEqual(result["status"], "INVALID")

    def test_recovery_required_blocks(self):
        recovery = {
            "post_check_type": "SELL_RUNTIME_COMMIT_RECOVERY_POST_CHECK",
            "status": "READY",
            "recovery_required": True,
            "preview_only": True,
        }
        result = self._readiness(recovery_post_check=recovery)

        self.assertEqual(result["status"], "BLOCKED")

    def test_queue_mutation_blocks(self):
        chain = self._chain()
        chain["queue_review"]["status"] = "BLOCKED"

        result = self._readiness(**chain)

        self.assertEqual(result["status"], "BLOCKED")

    def test_guard_failure_blocks(self):
        chain = self._chain()
        chain["guard"]["status"] = "BLOCKED"
        chain["guard"]["final_guard_ready"] = False

        result = self._readiness(**chain)

        self.assertEqual(result["status"], "BLOCKED")

    def test_executor_boundary_failure_blocks(self):
        chain = self._chain()
        chain["boundary"]["status"] = "BLOCKED"
        chain["boundary"]["execution_allowed"] = False

        result = self._readiness(**chain)

        self.assertEqual(result["status"], "BLOCKED")

    def test_dry_run_failure_invalid(self):
        chain = self._chain()
        chain["dry_run"]["status"] = "INVALID"

        result = self._readiness(**chain)

        self.assertEqual(result["status"], "INVALID")

    def test_post_preview_failure_invalid(self):
        chain = self._chain()
        chain["post_execution"]["status"] = "INVALID"

        result = self._readiness(**chain)

        self.assertEqual(result["status"], "INVALID")

    def test_hash_mismatch_invalid(self):
        chain = self._chain()
        chain["audit"]["send_order_call_preview_hash"] = "bad"

        result = self._readiness(**chain)

        self.assertEqual(result["status"], "INVALID")

    def test_order_change_invalid(self):
        chain = self._chain([_record(1), _record(2)])
        chain["dry_run"]["simulated_candidate_ids"] = ["CANDIDATE_2", "CANDIDATE_1"]

        result = self._readiness(**chain)

        self.assertEqual(result["status"], "INVALID")


if __name__ == "__main__":
    unittest.main()
