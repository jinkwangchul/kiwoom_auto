from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest import mock

from buy_execution_runtime_projection_preview import build_buy_runtime_projection_preview
from buy_runtime_commit_approval_session_preview import build_buy_runtime_commit_approval_session_preview
from buy_runtime_commit_core_dry_run_adapter import build_buy_runtime_commit_core_dry_run
from buy_runtime_commit_execution_readiness_preview import build_buy_runtime_commit_execution_readiness_preview
from buy_runtime_commit_gate_adapter import build_buy_runtime_commit_gate_preview
from buy_runtime_commit_preview_bridge import build_buy_runtime_commit_preview
from buy_runtime_commit_token_issue_preview import (
    STATUS_BLOCKED,
    STATUS_INVALID,
    STATUS_READY,
    build_buy_runtime_commit_token_issue_preview,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stable_hash(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class BuyRuntimeCommitTokenIssuePreviewTest(unittest.TestCase):
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

    def _approval(self, decision="APPROVED"):
        return build_buy_runtime_commit_approval_session_preview(
            self._readiness(),
            {
                "decision": decision,
                "reviewer_id": "operator-1",
                "decision_reason": "ok",
                "decision_at": "2026-07-11T10:30:00+09:00",
            },
        )

    def test_approved_decision_creates_token_issue_preview(self):
        approval = self._approval("APPROVED")

        result = build_buy_runtime_commit_token_issue_preview(approval)

        self.assertEqual(STATUS_READY, result["status"])
        preview = result["token_issue_preview"]
        self.assertEqual("BUY_RUNTIME_COMMIT_APPROVAL_TOKEN_ISSUE_V1", preview["token_issue_version"])
        self.assertTrue(preview["token_issue_request_id"].startswith("BUY_RUNTIME_COMMIT_TOKEN_ISSUE_"))
        self.assertEqual(approval["approval_session_preview"]["approval_session_id"], preview["approval_session_id"])
        self.assertEqual(approval["approval_decision_preview"]["decision_id"], preview["decision_id"])
        self.assertEqual(approval["approval_session_preview"]["candidate_id"], preview["candidate_id"])
        self.assertEqual(approval["approval_session_preview"]["transaction_id"], preview["transaction_id"])
        self.assertEqual(approval["approval_session_preview"]["projection_hash"], preview["projection_hash"])
        self.assertEqual(approval["approval_session_preview"]["runtime_before_hash"], preview["runtime_before_hash"])
        self.assertEqual(approval["approval_session_preview"]["runtime_after_hash"], preview["runtime_after_hash"])
        self.assertEqual(approval["approval_session_preview"]["changed_fields"], preview["changed_fields"])
        self.assertTrue(preview["token_required"])
        self.assertFalse(preview["token_issued"])
        self.assertFalse(preview["token_stored"])
        self.assertFalse(preview["token_consumed"])
        self.assertFalse(preview["execution_allowed"])
        self.assertFalse(preview["execution_started"])
        self.assertEqual(approval["execution_snapshot"], result["execution_snapshot"])
        self.assertEqual(preview["token_issue_request_id"], result["token_issue_summary"]["token_issue_request_id"])
        self.assertEqual("BUY Runtime Commit Approval Token Issue Preview", result["token_issue_report"]["title"])

    def test_non_approved_decisions_are_blocked(self):
        for decision in ("REJECTED", "DEFERRED"):
            with self.subTest(decision=decision):
                result = build_buy_runtime_commit_token_issue_preview(self._approval(decision))

                self.assertEqual(STATUS_BLOCKED, result["status"])
                self.assertIsNone(result["token_issue_preview"])

    def test_pending_without_decision_is_blocked(self):
        approval = build_buy_runtime_commit_approval_session_preview(self._readiness())

        result = build_buy_runtime_commit_token_issue_preview(approval)

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertIsNone(result["token_issue_preview"])

    def test_approval_granted_false_blocks(self):
        approval = self._approval("APPROVED")
        approval["approval_decision_preview"]["approval_granted"] = False

        result = build_buy_runtime_commit_token_issue_preview(approval)

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertIn("APPROVAL_GRANTED_FALSE", result["issues"])

    def test_missing_reviewer_or_decision_id_blocks(self):
        missing_reviewer = self._approval("APPROVED")
        missing_reviewer["approval_decision_preview"]["reviewer_id"] = ""
        missing_decision = self._approval("APPROVED")
        missing_decision["approval_decision_preview"]["decision_id"] = ""

        reviewer_result = build_buy_runtime_commit_token_issue_preview(missing_reviewer)
        decision_result = build_buy_runtime_commit_token_issue_preview(missing_decision)

        self.assertEqual(STATUS_INVALID, reviewer_result["status"])
        self.assertIn("REVIEWER_ID_MISSING", reviewer_result["issues"])
        self.assertEqual(STATUS_INVALID, decision_result["status"])
        self.assertIn("DECISION_ID_MISSING", decision_result["issues"])

    def test_preset_token_or_execution_flags_block(self):
        token_issued = self._approval("APPROVED")
        token_issued["approval_decision_preview"]["token_issued"] = True
        token_stored = self._approval("APPROVED")
        token_stored["approval_summary"]["token_stored"] = True
        token_consumed = self._approval("APPROVED")
        token_consumed["approval_session_preview"]["token_consumed"] = True
        execution_allowed = self._approval("APPROVED")
        execution_allowed["approval_decision_preview"]["execution_allowed"] = True

        for payload in (token_issued, token_stored, token_consumed, execution_allowed):
            with self.subTest(flag_payload=payload):
                result = build_buy_runtime_commit_token_issue_preview(payload)

                self.assertEqual(STATUS_INVALID, result["status"])
                self.assertIsNone(result["token_issue_preview"])

    def test_token_issue_request_id_is_deterministic(self):
        approval = self._approval("APPROVED")

        first = build_buy_runtime_commit_token_issue_preview(approval)
        second = build_buy_runtime_commit_token_issue_preview(approval)

        self.assertEqual(
            first["token_issue_preview"]["token_issue_request_id"],
            second["token_issue_preview"]["token_issue_request_id"],
        )

    def test_hash_preserved_and_mismatch_blocks(self):
        approval = self._approval("APPROVED")
        ok = build_buy_runtime_commit_token_issue_preview(approval)
        self.assertEqual(
            approval["approval_session_preview"]["policy_hash"],
            ok["token_issue_preview"]["policy_hash"],
        )

        broken = deepcopy(approval)
        broken["approval_summary"]["projection_hash"] = "other"
        result = build_buy_runtime_commit_token_issue_preview(broken)

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertIn("SUMMARY_PROJECTION_HASH_MISMATCH", result["issues"])

    def test_malformed_changed_fields_blocks(self):
        approval = self._approval("APPROVED")
        approval["approval_session_preview"]["changed_fields"] = "bad"

        result = build_buy_runtime_commit_token_issue_preview(approval)

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertIn("CHANGED_FIELDS_MALFORMED", result["issues"])

    def test_input_immutability(self):
        approval = self._approval("APPROVED")
        original = deepcopy(approval)

        build_buy_runtime_commit_token_issue_preview(approval)

        self.assertEqual(original, approval)

    def test_runtime_and_queue_files_are_not_changed(self):
        paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        before = {path: _sha256(path) for path in paths}

        build_buy_runtime_commit_token_issue_preview(self._approval("APPROVED"))

        self.assertEqual(before, {path: _sha256(path) for path in paths})

    def test_side_effect_components_are_not_called(self):
        approval = self._approval("APPROVED")

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
            result = build_buy_runtime_commit_token_issue_preview(approval)

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
        self.assertFalse(result["approval_token_stored"])
        self.assertFalse(result["approval_token_consumed"])
        self.assertFalse(result["runtime_commit_core_called"])
        self.assertFalse(result["runtime_commit_real_executor_called"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["broker_called"])
        self.assertFalse(result["gui_updated"])


if __name__ == "__main__":
    unittest.main()
