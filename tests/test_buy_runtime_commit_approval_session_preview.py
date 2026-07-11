from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest import mock

from buy_execution_runtime_projection_preview import build_buy_runtime_projection_preview
from buy_runtime_commit_approval_session_preview import (
    STATUS_INVALID,
    STATUS_READY,
    build_buy_runtime_commit_approval_session_preview,
)
from buy_runtime_commit_core_dry_run_adapter import build_buy_runtime_commit_core_dry_run
from buy_runtime_commit_execution_readiness_preview import build_buy_runtime_commit_execution_readiness_preview
from buy_runtime_commit_gate_adapter import build_buy_runtime_commit_gate_preview
from buy_runtime_commit_preview_bridge import build_buy_runtime_commit_preview


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stable_hash(value) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class BuyRuntimeCommitApprovalSessionPreviewTest(unittest.TestCase):
    def _runtime(self):
        return {
            "current_buy_round": 1,
            "executed_buy_rounds": 1,
            "cumulative_buy_budget": 100000.0,
            "last_buy_order_price": 69000.0,
            "last_buy_budget": 100000.0,
            "last_buy_signal_id": "SIG_PREV",
            "last_buy_candidate_id": "BUY_ORDER_CANDIDATE_PREV",
            "last_buy_created_at": "2026-07-11T09:00:00+09:00",
            "is_last_buy_round": False,
        }

    def _snapshot(self, runtime):
        return {
            "policy_hash": "policy-hash-1",
            "approved_rule_hash": "approved-rule-hash-1",
            "runtime_state_hash": _stable_hash(runtime),
            "calculation_hash": "calc-hash-1",
        }

    def _readiness(self):
        runtime = self._runtime()
        snapshot = self._snapshot(runtime)
        candidate = {
            "status": "READY",
            "order_candidate_draft": {
                "candidate_version": "BUY_ORDER_CANDIDATE_DRAFT_V1",
                "candidate_id": "BUY_ORDER_CANDIDATE_1",
                "symbol": "005930",
                "side": "BUY",
                "order_type": "LIMIT",
                "price": 70000.0,
                "budget": 150000.0,
                "quantity_policy": "BUDGET_BASED",
                "next_buy_round": 2,
                "is_last_round": False,
                "hoga_mode": "SINGLE",
                "hoga_up": 1,
                "hoga_down": 0,
                "source_signal_id": "SIG_BUY_2",
                "policy_version": "BUY_EXECUTION_POLICY_V1",
                "execution_snapshot": snapshot,
            },
            "execution_snapshot": deepcopy(snapshot),
            "evidence": {"candidate": "ready"},
            "diagnostics": [{"stage": "candidate_draft", "ok": True}],
        }
        projection = build_buy_runtime_projection_preview(
            buy_candidate_preview=candidate,
            runtime_state_snapshot=runtime,
            execution_policy_snapshot={"policy_hash": "policy-hash-1"},
            projection_context={"preview_timestamp": "2026-07-11T10:00:00+09:00"},
        )
        commit_result = build_buy_runtime_commit_preview(projection)
        gate_result = build_buy_runtime_commit_gate_preview(commit_result)
        dry_run = build_buy_runtime_commit_core_dry_run(
            gate_result,
            runtime_commit_preview=commit_result["runtime_commit_preview"],
            runtime_patch_preview=commit_result["runtime_patch_preview"],
        )
        return build_buy_runtime_commit_execution_readiness_preview(
            dry_run,
            {"gate_summary": gate_result["gate_summary"], "risk_level": "medium"},
        )

    def test_ready_readiness_creates_pending_session(self):
        result = build_buy_runtime_commit_approval_session_preview(self._readiness())

        self.assertEqual(STATUS_READY, result["status"])
        self.assertIsNone(result["approval_decision_preview"])
        session = result["approval_session_preview"]
        self.assertEqual("BUY_RUNTIME_COMMIT_APPROVAL_SESSION_V1", session["approval_session_version"])
        self.assertTrue(session["approval_session_id"].startswith("BUY_RUNTIME_COMMIT_APPROVAL_SESSION_"))
        self.assertEqual("PENDING", session["approval_status"])
        self.assertTrue(session["approval_required"])
        self.assertFalse(session["approval_granted"])
        self.assertFalse(session["execution_allowed"])
        self.assertFalse(session["token_issued"])
        self.assertFalse(session["token_consumed"])
        self.assertFalse(session["execution_started"])

    def test_approved_decision_preview(self):
        result = build_buy_runtime_commit_approval_session_preview(
            self._readiness(),
            {
                "decision": "APPROVED",
                "reviewer_id": "operator-1",
                "decision_reason": "looks good",
                "decision_at": "2026-07-11T10:30:00+09:00",
            },
        )

        decision = result["approval_decision_preview"]
        self.assertEqual(STATUS_READY, result["status"])
        self.assertEqual("APPROVED", decision["approval_status"])
        self.assertTrue(decision["approval_granted"])
        self.assertFalse(decision["execution_allowed"])
        self.assertFalse(decision["token_issued"])
        self.assertFalse(decision["token_consumed"])
        self.assertEqual("APPROVED", result["approval_summary"]["approval_status"])

    def test_rejected_decision_preview(self):
        result = build_buy_runtime_commit_approval_session_preview(
            self._readiness(),
            {
                "decision": "REJECTED",
                "reviewer_id": "operator-1",
                "decision_reason": "risk rejected",
                "decision_at": "2026-07-11T10:31:00+09:00",
            },
        )

        decision = result["approval_decision_preview"]
        self.assertEqual("REJECTED", decision["approval_status"])
        self.assertFalse(decision["approval_granted"])
        self.assertFalse(result["approval_summary"]["execution_allowed"])

    def test_deferred_decision_preview(self):
        result = build_buy_runtime_commit_approval_session_preview(
            self._readiness(),
            {
                "decision": "DEFERRED",
                "reviewer_id": "operator-1",
                "decision_reason": "wait",
                "decision_at": "2026-07-11T10:32:00+09:00",
            },
        )

        decision = result["approval_decision_preview"]
        self.assertEqual("DEFERRED", decision["approval_status"])
        self.assertFalse(decision["approval_granted"])
        self.assertFalse(decision["execution_allowed"])

    def test_unsupported_decision_blocks(self):
        result = build_buy_runtime_commit_approval_session_preview(
            self._readiness(),
            {"decision": "MAYBE", "reviewer_id": "operator-1"},
        )

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertIn("UNSUPPORTED_DECISION", result["issues"])

    def test_missing_reviewer_for_approved_blocks(self):
        result = build_buy_runtime_commit_approval_session_preview(
            self._readiness(),
            {"decision": "APPROVED", "decision_reason": "ok", "decision_at": "2026-07-11T10:30:00+09:00"},
        )

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertIn("REVIEWER_ID_MISSING", result["issues"])

    def test_missing_rejection_reason_blocks(self):
        result = build_buy_runtime_commit_approval_session_preview(
            self._readiness(),
            {"decision": "REJECTED", "reviewer_id": "operator-1", "decision_at": "2026-07-11T10:30:00+09:00"},
        )

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertIn("DECISION_REASON_MISSING", result["issues"])

    def test_session_id_and_decision_id_are_deterministic(self):
        decision_input = {
            "decision": "APPROVED",
            "reviewer_id": "operator-1",
            "decision_reason": "ok",
            "decision_at": "2026-07-11T10:30:00+09:00",
        }

        first = build_buy_runtime_commit_approval_session_preview(self._readiness(), decision_input)
        second = build_buy_runtime_commit_approval_session_preview(self._readiness(), decision_input)

        self.assertEqual(
            first["approval_session_preview"]["approval_session_id"],
            second["approval_session_preview"]["approval_session_id"],
        )
        self.assertEqual(
            first["approval_decision_preview"]["decision_id"],
            second["approval_decision_preview"]["decision_id"],
        )
        self.assertTrue(first["approval_decision_preview"]["decision_id"].startswith("BUY_RUNTIME_COMMIT_APPROVAL_DECISION_"))

    def test_hash_preserved_and_mismatch_blocks(self):
        readiness = self._readiness()
        ok = build_buy_runtime_commit_approval_session_preview(readiness)
        self.assertEqual(readiness["approval_request_preview"]["projection_hash"], ok["approval_session_preview"]["projection_hash"])

        broken = deepcopy(readiness)
        broken["approval_request_preview"]["projection_hash"] = "other"
        result = build_buy_runtime_commit_approval_session_preview(broken)

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertIn("PROJECTION_HASH_MISMATCH", result["issues"])

    def test_malformed_changed_fields_blocks(self):
        readiness = self._readiness()
        readiness["approval_request_preview"]["changed_fields"] = "bad"

        result = build_buy_runtime_commit_approval_session_preview(readiness)

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertIn("CHANGED_FIELDS_MALFORMED", result["issues"])

    def test_preset_execution_or_token_flags_block(self):
        readiness = self._readiness()
        readiness["approval_request_preview"]["execution_allowed"] = True
        execution = build_buy_runtime_commit_approval_session_preview(readiness)
        readiness = self._readiness()
        readiness["approval_request_preview"]["token_issued"] = True
        token = build_buy_runtime_commit_approval_session_preview(readiness)

        self.assertEqual(STATUS_INVALID, execution["status"])
        self.assertIn("EXECUTION_ALLOWED_PRESET", execution["issues"])
        self.assertEqual(STATUS_INVALID, token["status"])
        self.assertIn("TOKEN_FLAG_PRESET", token["issues"])

    def test_input_immutability(self):
        readiness = self._readiness()
        decision = {"decision": "DEFERRED", "reviewer_id": "operator-1", "decision_reason": "wait", "decision_at": "t"}
        original = deepcopy((readiness, decision))

        build_buy_runtime_commit_approval_session_preview(readiness, decision)

        self.assertEqual(original, (readiness, decision))

    def test_runtime_and_queue_files_are_not_changed(self):
        paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        before = {path: _sha256(path) for path in paths}

        build_buy_runtime_commit_approval_session_preview(self._readiness())

        self.assertEqual(before, {path: _sha256(path) for path in paths})

    def test_side_effect_components_are_not_called(self):
        with (
            mock.patch("runtime_commit_approval_token_store.issue_runtime_commit_approval_token") as issue_token,
            mock.patch("runtime_commit_approval_token_store.consume_runtime_commit_approval_token") as consume_token,
            mock.patch("runtime_commit_real_executor.execute_runtime_commit") as real_executor,
            mock.patch("runtime_commit_guard.acquire_runtime_commit_lock", create=True) as lock,
            mock.patch("runtime_backup_manager.create_runtime_backup_plan") as backup,
            mock.patch("runtime_commit_recovery_journal.record_runtime_commit_journal_event", create=True) as journal,
            mock.patch("runtime_commit_transaction_persistence.save_runtime_commit_transaction_manifest", create=True) as persistence,
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = build_buy_runtime_commit_approval_session_preview(self._readiness())

        self.assertEqual(STATUS_READY, result["status"])
        issue_token.assert_not_called()
        consume_token.assert_not_called()
        real_executor.assert_not_called()
        lock.assert_not_called()
        backup.assert_not_called()
        journal.assert_not_called()
        persistence.assert_not_called()
        write_text.assert_not_called()
        open_mock.assert_not_called()
        self.assertFalse(result["approval_token_issued"])
        self.assertFalse(result["approval_token_consumed"])
        self.assertFalse(result["runtime_commit_real_executor_called"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["broker_called"])
        self.assertFalse(result["gui_updated"])


if __name__ == "__main__":
    unittest.main()
