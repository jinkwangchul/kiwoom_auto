from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from buy_execution_runtime_projection_preview import build_buy_runtime_projection_preview
from buy_runtime_commit_approval_session_preview import build_buy_runtime_commit_approval_session_preview
from buy_runtime_commit_approval_token_issue_adapter import (
    STATUS_BLOCKED,
    STATUS_INVALID,
    STATUS_READY,
    issue_runtime_commit_approval_token_from_context,
)
from buy_runtime_commit_core_dry_run_adapter import build_buy_runtime_commit_core_dry_run
from buy_runtime_commit_execution_context_builder import build_buy_runtime_commit_execution_context
from buy_runtime_commit_execution_readiness_preview import build_buy_runtime_commit_execution_readiness_preview
from buy_runtime_commit_gate_adapter import build_buy_runtime_commit_gate_preview
from buy_runtime_commit_preview_bridge import build_buy_runtime_commit_preview
from buy_runtime_commit_token_issue_preview import build_buy_runtime_commit_token_issue_preview
from runtime_commit_approval_token_store import read_runtime_commit_approval_token


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stable_hash(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class BuyRuntimeCommitApprovalTokenIssueAdapterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="buy_token_issue_adapter_")
        self.storage_root = str(Path(self.tmp.name) / "storage")

    def tearDown(self):
        self.tmp.cleanup()

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

    def _context(self):
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
        readiness = build_buy_runtime_commit_execution_readiness_preview(
            dry_run,
            {"gate_summary": gate_result["gate_summary"], "risk_level": "medium"},
        )
        approval = build_buy_runtime_commit_approval_session_preview(
            readiness,
            {
                "decision": "APPROVED",
                "reviewer_id": "operator-1",
                "decision_reason": "ok",
                "decision_at": "2026-07-11T10:30:00+09:00",
            },
        )
        token_issue = build_buy_runtime_commit_token_issue_preview(approval)
        context_result = build_buy_runtime_commit_execution_context(
            runtime_commit_core_dry_run_result=dry_run,
            execution_readiness_preview_result=readiness,
            approval_session_preview_result=approval,
            token_issue_preview_result=token_issue,
            context_input={
                "storage_root": self.storage_root,
                "consumer_id": "consumer-1",
                "scope": "RUNTIME_COMMIT_EXECUTION",
            },
        )
        self.assertEqual("READY", context_result["status"])
        return context_result["runtime_commit_execution_context"]

    def test_normal_issue(self):
        context = self._context()

        result = issue_runtime_commit_approval_token_from_context(context)

        self.assertEqual(STATUS_READY, result["status"])
        issue_result = result["approval_token_issue_result"]
        token = result["issued_token_preview"]
        self.assertEqual("ISSUED", issue_result["issue_status"])
        self.assertTrue(issue_result["token_issued"])
        self.assertEqual(context["token_issue_request_id"], token["token_id"])
        self.assertEqual(context["transaction_manifest_preview"]["commit_id"], token["commit_id"])
        self.assertEqual(context["transaction_manifest_preview"]["execution_plan_hash"], token["plan_hash"])
        self.assertEqual(context["consumer_id"], token["issued_for"])
        self.assertEqual(context["reviewer_id"], token["issued_by"])
        self.assertEqual(context["scope"], token["scope"])
        self.assertEqual("ISSUED", token["token_status"])
        self.assertTrue(token["single_use"])
        self.assertFalse(result["token_consumed"])
        stored = read_runtime_commit_approval_token(storage_plan=result["token_storage_plan"])
        self.assertEqual("OK", stored["read_status"])
        self.assertEqual(token["token_id"], stored["token"]["token_id"])

    def test_existing_token_blocks(self):
        context = self._context()
        first = issue_runtime_commit_approval_token_from_context(context)

        second = issue_runtime_commit_approval_token_from_context(context)

        self.assertEqual(STATUS_READY, first["status"])
        self.assertEqual(STATUS_BLOCKED, second["status"])
        self.assertIn("token already exists; reissue is not allowed", second["issues"])

    def test_missing_required_context_fields_block(self):
        cases = (
            ("storage_root", "STORAGE_ROOT_MISSING"),
            ("consumer_id", "CONSUMER_ID_MISSING"),
            ("scope", "SCOPE_MISSING"),
            ("commit_id", "COMMIT_ID_MISSING"),
            ("execution_plan_hash", "EXECUTION_PLAN_HASH_MISSING"),
            ("token_issue_request_id", "TOKEN_ISSUE_REQUEST_ID_MISSING"),
        )
        for field, issue in cases:
            with self.subTest(field=field):
                context = self._context()
                context[field] = ""
                if field in {"commit_id", "execution_plan_hash"}:
                    context["transaction_manifest_preview"][field] = ""

                result = issue_runtime_commit_approval_token_from_context(context)

                self.assertEqual(STATUS_INVALID, result["status"])
                self.assertIn(issue, result["issues"])

    def test_preview_and_runtime_flags_block(self):
        cases = (
            ("preview_only", False, "PREVIEW_ONLY_FALSE"),
            ("runtime_write", True, "RUNTIME_WRITE_TRUE"),
            ("token_issued", True, "TOKEN_FLAG_PRESET"),
            ("token_consumed", True, "TOKEN_FLAG_PRESET"),
            ("execution_allowed", True, "EXECUTION_FLAG_PRESET"),
        )
        for field, value, issue in cases:
            with self.subTest(field=field):
                context = self._context()
                context[field] = value

                result = issue_runtime_commit_approval_token_from_context(context)

                self.assertEqual(STATUS_INVALID, result["status"])
                self.assertIn(issue, result["issues"])

    def test_identity_and_hash_mismatch_block(self):
        cases = (
            ("commit", "commit_id", "bad", "COMMIT_ID_MISMATCH"),
            ("transaction", "transaction_id", "bad", "TRANSACTION_ID_MISMATCH"),
            ("execution_hash", "execution_plan_hash", "bad", "EXECUTION_PLAN_HASH_MISMATCH"),
            ("projection", "projection_hash", "bad", "PROJECTION_HASH_MISMATCH"),
            ("policy", "policy_hash", "bad", "POLICY_HASH_MISMATCH"),
            ("rule", "approved_rule_hash", "bad", "APPROVED_RULE_HASH_MISMATCH"),
        )
        for name, field, value, issue in cases:
            with self.subTest(name=name):
                context = self._context()
                if field in {"commit_id", "transaction_id", "execution_plan_hash"}:
                    context["transaction_manifest_preview"][field] = value
                else:
                    context["token_metadata"][field] = value

                result = issue_runtime_commit_approval_token_from_context(context)

                self.assertEqual(STATUS_INVALID, result["status"])
                self.assertIn(issue, result["issues"])

    def test_token_metadata_saved(self):
        context = self._context()

        result = issue_runtime_commit_approval_token_from_context(context)

        metadata = result["issued_token_preview"]["metadata"]
        for field in (
            "approval_session_id",
            "approval_decision_id",
            "approval_request_id",
            "candidate_id",
            "transaction_id",
            "projection_hash",
            "policy_hash",
            "approved_rule_hash",
            "runtime_before_hash",
            "runtime_after_hash",
            "target",
            "changed_fields",
        ):
            self.assertIn(field, metadata)
            self.assertEqual(context["token_metadata"][field], metadata[field])

    def test_input_immutability(self):
        context = self._context()
        original = deepcopy(context)

        issue_runtime_commit_approval_token_from_context(context)

        self.assertEqual(original, context)

    def test_only_token_store_issue_path_is_called(self):
        context = self._context()
        with (
            mock.patch("runtime_commit_approval_token_store.validate_runtime_commit_approval_token") as validate_token,
            mock.patch("runtime_commit_approval_token_store.consume_runtime_commit_approval_token") as consume_token,
            mock.patch("runtime_commit_real_executor.execute_runtime_commit") as real_executor,
            mock.patch("runtime_commit_guard.acquire_runtime_commit_lock", create=True) as lock,
            mock.patch("runtime_backup_manager.create_runtime_backup_plan") as backup,
            mock.patch("runtime_commit_recovery_journal.record_runtime_commit_journal_event", create=True) as journal,
            mock.patch("runtime_commit_transaction_persistence.write_runtime_transaction_manifest", create=True) as persistence,
        ):
            result = issue_runtime_commit_approval_token_from_context(context)

        self.assertEqual(STATUS_READY, result["status"])
        validate_token.assert_not_called()
        consume_token.assert_not_called()
        real_executor.assert_not_called()
        lock.assert_not_called()
        backup.assert_not_called()
        journal.assert_not_called()
        persistence.assert_not_called()

    def test_runtime_and_queue_files_are_not_changed(self):
        paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        before = {path: _sha256(path) for path in paths}

        issue_runtime_commit_approval_token_from_context(self._context())

        self.assertEqual(before, {path: _sha256(path) for path in paths})


if __name__ == "__main__":
    unittest.main()
