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
from buy_runtime_commit_approval_token_issue_adapter import issue_runtime_commit_approval_token_from_context
from buy_runtime_commit_core_dry_run_adapter import build_buy_runtime_commit_core_dry_run
from buy_runtime_commit_execution_context_builder import build_buy_runtime_commit_execution_context
from buy_runtime_commit_execution_readiness_preview import build_buy_runtime_commit_execution_readiness_preview
from buy_runtime_commit_gate_adapter import build_buy_runtime_commit_gate_preview
from buy_runtime_commit_preview_bridge import build_buy_runtime_commit_preview
from buy_runtime_commit_token_issue_preview import build_buy_runtime_commit_token_issue_preview
from buy_runtime_commit_token_validation_handoff_preview import (
    STATUS_BLOCKED,
    STATUS_INVALID,
    STATUS_READY,
    build_runtime_commit_token_validation_handoff_preview,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stable_hash(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class BuyRuntimeCommitTokenValidationHandoffPreviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="buy_token_handoff_")
        self._storage_seq = 0

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
        self._storage_seq += 1
        storage_root = str(Path(self.tmp.name) / f"storage_{self._storage_seq}")
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
                "storage_root": storage_root,
                "consumer_id": "consumer-1",
                "scope": "RUNTIME_COMMIT_EXECUTION",
            },
        )
        self.assertEqual("READY", context_result["status"])
        return context_result["runtime_commit_execution_context"]

    def _issued(self):
        context = self._context()
        issue_result = issue_runtime_commit_approval_token_from_context(context)
        self.assertEqual("READY", issue_result["status"])
        return context, issue_result

    def _handoff(self, context=None, issue_result=None):
        if context is None or issue_result is None:
            context, issue_result = self._issued()
        return build_runtime_commit_token_validation_handoff_preview(
            runtime_commit_execution_context=context,
            approval_token_issue_result=issue_result,
            token_storage_plan=issue_result["token_storage_plan"],
            issued_token=issue_result["issued_token_preview"],
        )

    def _rewrite_token(self, issue_result, mutate):
        token_path = Path(issue_result["token_storage_plan"]["token_path"])
        token = json.loads(token_path.read_text(encoding="utf-8"))
        mutate(token)
        token_path.write_text(json.dumps(token, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    def test_issued_token_validates_and_creates_handoff_preview(self):
        context, issue_result = self._issued()

        result = self._handoff(context, issue_result)

        self.assertEqual(STATUS_READY, result["status"])
        validation = result["token_validation_preview"]
        handoff = result["real_executor_handoff_preview"]
        self.assertEqual("BUY_RUNTIME_COMMIT_TOKEN_VALIDATION_PREVIEW_V1", validation["validation_version"])
        self.assertTrue(validation["token_valid"])
        self.assertEqual(context["token_id"], validation["token_id"])
        self.assertEqual(context["commit_id"], validation["commit_id"])
        self.assertEqual(context["execution_plan_hash"], validation["plan_hash"])
        self.assertEqual(context["scope"], validation["scope"])
        self.assertFalse(validation["token_consumed"])
        self.assertTrue(handoff["handoff_id"].startswith("BUY_RUNTIME_COMMIT_HANDOFF_"))
        self.assertEqual(context["context_id"], handoff["context_id"])
        self.assertEqual(context["token_id"], handoff["token_id"])
        self.assertEqual(context["commit_id"], handoff["commit_id"])
        self.assertEqual(context["transaction_id"], handoff["transaction_id"])
        self.assertEqual(context["consumer_id"], handoff["consumer_id"])
        self.assertEqual(context["execution_plan_hash"], handoff["plan_hash"])
        self.assertEqual("APPROVED", handoff["gate_result"]["gate_status"])
        self.assertEqual("READY", handoff["storage_plan"]["storage_status"])
        self.assertEqual("READY", handoff["guard_plan"]["guard_status"])
        self.assertIn(context["target"], handoff["expected_targets"])
        self.assertIn(context["target"], handoff["new_targets"])
        self.assertFalse(handoff["token_consumed"])
        self.assertFalse(handoff["execution_allowed"])
        self.assertFalse(handoff["execution_started"])
        self.assertFalse(handoff["runtime_write"])
        self.assertEqual(handoff["handoff_id"], result["handoff_summary"]["handoff_id"])
        self.assertEqual("BUY Runtime Commit Real Executor Handoff Preview", result["handoff_report"]["title"])

    def test_missing_token_blocks(self):
        context = self._context()
        missing_root = str(Path(self.tmp.name) / "missing_storage")
        issue_result = {
            "status": "READY",
            "token_storage_plan": {
                "plan_status": "READY",
                "storage_root": missing_root,
                "token_path": str(Path(missing_root) / "approval_tokens" / "missing.json"),
                "claim_path": str(Path(missing_root) / "approval_tokens" / "missing.consume.lock"),
                "token_id": "missing",
                "commit_id": context["commit_id"],
            },
            "issued_token_preview": {},
        }

        result = self._handoff(context, issue_result)

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertIsNone(result["real_executor_handoff_preview"])

    def test_consumed_token_blocks(self):
        context, issue_result = self._issued()

        def mutate(token):
            token["token_status"] = "CONSUMED"
            token["consumed_at"] = "2026-07-11T11:00:00+09:00"
            token["consumed_by"] = context["consumer_id"]
            token["consumption_id"] = "already-consumed"

        self._rewrite_token(issue_result, mutate)

        result = self._handoff(context, issue_result)

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertFalse(result["token_validation_preview"]["token_valid"])

    def test_commit_plan_scope_consumer_and_metadata_mismatches_block(self):
        cases = (
            ("commit", lambda token: token.update({"commit_id": "bad"}), "commit_id mismatch"),
            ("plan", lambda token: token.update({"plan_hash": "bad"}), "plan_hash mismatch"),
            ("scope", lambda token: token.update({"scope": "OTHER"}), "scope is invalid"),
            ("consumer", lambda token: token.update({"issued_for": "other"}), "CONSUMER_ID_MISMATCH"),
            ("metadata", lambda token: token["metadata"].update({"policy_hash": "bad"}), "POLICY_HASH_MISMATCH"),
        )
        for name, mutate, expected in cases:
            with self.subTest(name=name):
                context, issue_result = self._issued()
                self._rewrite_token(issue_result, mutate)

                result = self._handoff(context, issue_result)

                self.assertEqual(STATUS_BLOCKED, result["status"])
                self.assertIn(expected, result["issues"])

    def test_single_use_false_blocks(self):
        context, issue_result = self._issued()
        self._rewrite_token(issue_result, lambda token: token.update({"single_use": False}))

        result = self._handoff(context, issue_result)

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertIsNone(result["real_executor_handoff_preview"])

    def test_malformed_context_and_storage_plan_block(self):
        context, issue_result = self._issued()
        broken_context = deepcopy(context)
        broken_context["preview_only"] = False
        context_result = self._handoff(broken_context, issue_result)

        broken_plan = deepcopy(issue_result)
        broken_plan["token_storage_plan"] = {"plan_status": "INVALID"}
        plan_result = self._handoff(context, broken_plan)

        self.assertEqual(STATUS_INVALID, context_result["status"])
        self.assertIn("PREVIEW_ONLY_FALSE", context_result["issues"])
        self.assertEqual(STATUS_INVALID, plan_result["status"])
        self.assertIn("MALFORMED_STORAGE_PLAN", plan_result["issues"])

    def test_handoff_id_is_deterministic(self):
        context, issue_result = self._issued()

        first = self._handoff(context, issue_result)
        second = self._handoff(context, issue_result)

        self.assertEqual(
            first["real_executor_handoff_preview"]["handoff_id"],
            second["real_executor_handoff_preview"]["handoff_id"],
        )

    def test_input_immutability(self):
        context, issue_result = self._issued()
        original = deepcopy((context, issue_result))

        self._handoff(context, issue_result)

        self.assertEqual(original, (context, issue_result))

    def test_forbidden_components_are_not_called(self):
        context, issue_result = self._issued()
        with (
            mock.patch("runtime_commit_approval_token_store.consume_runtime_commit_approval_token") as consume_token,
            mock.patch("runtime_commit_real_executor.execute_runtime_commit") as real_executor,
            mock.patch("runtime_commit_guard.acquire_runtime_commit_lock", create=True) as lock,
            mock.patch("runtime_backup_manager.create_runtime_backup_plan") as backup,
            mock.patch("runtime_commit_recovery_journal.record_runtime_commit_journal_event", create=True) as journal,
            mock.patch("runtime_commit_transaction_persistence.write_runtime_transaction_manifest", create=True) as persistence,
            mock.patch("kiwoom_send_order_executor.execute_send_order", create=True) as send_order,
        ):
            result = self._handoff(context, issue_result)

        self.assertEqual(STATUS_READY, result["status"])
        consume_token.assert_not_called()
        real_executor.assert_not_called()
        lock.assert_not_called()
        backup.assert_not_called()
        journal.assert_not_called()
        persistence.assert_not_called()
        send_order.assert_not_called()

    def test_runtime_and_queue_files_are_not_changed(self):
        paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        before = {path: _sha256(path) for path in paths}

        self._handoff()

        self.assertEqual(before, {path: _sha256(path) for path in paths})


if __name__ == "__main__":
    unittest.main()
