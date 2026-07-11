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
from buy_runtime_commit_core_dry_run_adapter import build_buy_runtime_commit_core_dry_run
from buy_runtime_commit_execution_context_builder import (
    STATUS_BLOCKED,
    STATUS_INVALID,
    STATUS_READY,
    build_buy_runtime_commit_execution_context,
)
from buy_runtime_commit_execution_readiness_preview import build_buy_runtime_commit_execution_readiness_preview
from buy_runtime_commit_gate_adapter import build_buy_runtime_commit_gate_preview
from buy_runtime_commit_preview_bridge import build_buy_runtime_commit_preview
from buy_runtime_commit_token_issue_preview import build_buy_runtime_commit_token_issue_preview


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stable_hash(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class BuyRuntimeCommitExecutionContextBuilderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="buy_context_builder_")
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

    def _chain(self, decision="APPROVED"):
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
                "decision": decision,
                "reviewer_id": "operator-1",
                "decision_reason": "ok",
                "decision_at": "2026-07-11T10:30:00+09:00",
            },
        )
        token_issue = build_buy_runtime_commit_token_issue_preview(approval)
        return {
            "dry_run": dry_run,
            "readiness": readiness,
            "approval": approval,
            "token_issue": token_issue,
        }

    def _context_input(self, **overrides):
        data = {
            "storage_root": self.storage_root,
            "consumer_id": "consumer-1",
            "scope": "RUNTIME_COMMIT_EXECUTION",
        }
        data.update(overrides)
        return data

    def _build(self, chain=None, context_input=None):
        chain = chain or self._chain()
        return build_buy_runtime_commit_execution_context(
            runtime_commit_core_dry_run_result=chain["dry_run"],
            execution_readiness_preview_result=chain["readiness"],
            approval_session_preview_result=chain["approval"],
            token_issue_preview_result=chain["token_issue"],
            context_input=context_input if context_input is not None else self._context_input(),
        )

    def test_approved_input_creates_ready_context(self):
        chain = self._chain()

        result = self._build(chain)

        self.assertEqual(STATUS_READY, result["status"])
        context = result["runtime_commit_execution_context"]
        transaction = chain["dry_run"]["transaction_contract_preview"]
        token_issue = chain["token_issue"]["token_issue_preview"]
        self.assertEqual("BUY_RUNTIME_COMMIT_EXECUTION_CONTEXT_V1", context["context_version"])
        self.assertTrue(context["context_id"].startswith("BUY_RUNTIME_COMMIT_CONTEXT_"))
        self.assertEqual(token_issue["token_issue_request_id"], context["token_id"])
        self.assertTrue(context["approval_granted"])
        self.assertEqual("operator-1", context["reviewer_id"])
        self.assertEqual(transaction["commit_id"], context["commit_id"])
        self.assertEqual(transaction["transaction_id"], context["transaction_id"])
        self.assertEqual(transaction["execution_plan_hash"], context["execution_plan_hash"])
        self.assertEqual("consumer-1", context["consumer_id"])
        self.assertEqual("RUNTIME_COMMIT_EXECUTION", context["scope"])
        self.assertEqual(token_issue["candidate_id"], context["candidate_id"])
        self.assertEqual(token_issue["projection_hash"], context["projection_hash"])
        self.assertEqual(token_issue["runtime_before_hash"], context["runtime_before_hash"])
        self.assertEqual(token_issue["runtime_after_hash"], context["runtime_after_hash"])
        self.assertEqual(token_issue["policy_hash"], context["policy_hash"])
        self.assertEqual(token_issue["approved_rule_hash"], context["approved_rule_hash"])
        self.assertEqual(token_issue["changed_fields"], context["changed_fields"])
        self.assertIsNotNone(context["transaction_manifest_preview"])
        self.assertIsNotNone(context["apply_plan_preview"])
        self.assertIsNotNone(context["verification_plan_preview"])
        self.assertIsNotNone(context["rollback_plan_preview"])
        self.assertFalse(context["token_issued"])
        self.assertFalse(context["token_consumed"])
        self.assertFalse(context["execution_allowed"])
        self.assertFalse(context["execution_started"])
        self.assertFalse(context["runtime_write"])
        self.assertEqual("operator-1", context["token_metadata"]["reviewer_id"])
        self.assertEqual(context["context_id"], result["context_summary"]["context_id"])
        self.assertEqual("BUY Runtime Commit Execution Context", result["context_report"]["title"])

    def test_missing_context_inputs_block(self):
        cases = (
            ("storage_root", {"storage_root": ""}, "STORAGE_ROOT_MISSING"),
            ("consumer_id", {"consumer_id": ""}, "CONSUMER_ID_MISSING"),
            ("scope", {"scope": ""}, "SCOPE_MISSING"),
        )
        for name, overrides, issue in cases:
            with self.subTest(name=name):
                result = self._build(context_input=self._context_input(**overrides))

                self.assertEqual(STATUS_INVALID, result["status"])
                self.assertIn(issue, result["issues"])
                self.assertIsNone(result["runtime_commit_execution_context"])

    def test_missing_core_identity_fields_block(self):
        cases = (
            ("commit", "commit_id", "COMMIT_ID_MISSING"),
            ("transaction", "transaction_id", "TRANSACTION_ID_MISSING"),
            ("execution_hash", "execution_plan_hash", "EXECUTION_PLAN_HASH_MISSING"),
        )
        for name, field, issue in cases:
            with self.subTest(name=name):
                chain = self._chain()
                chain["dry_run"]["transaction_contract_preview"][field] = ""

                result = self._build(chain)

                self.assertEqual(STATUS_INVALID, result["status"])
                self.assertIn(issue, result["issues"])

    def test_unapproved_decision_blocks(self):
        chain = self._chain("REJECTED")

        result = self._build(chain)

        self.assertEqual(STATUS_BLOCKED, result["status"])
        self.assertIsNone(result["runtime_commit_execution_context"])

    def test_preset_token_or_execution_flags_block(self):
        cases = (
            ("token_issued", "token_issued"),
            ("token_stored", "token_stored"),
            ("token_consumed", "token_consumed"),
            ("execution_allowed", "execution_allowed"),
            ("execution_started", "execution_started"),
        )
        for name, field in cases:
            with self.subTest(name=name):
                chain = self._chain()
                chain["token_issue"]["token_issue_preview"][field] = True

                result = self._build(chain)

                self.assertEqual(STATUS_INVALID, result["status"])

    def test_identity_mismatch_blocks(self):
        cases = (
            ("candidate", "candidate_id", "other-candidate", "CANDIDATE_ID_SESSION_MISMATCH"),
            ("transaction", "transaction_id", "other-transaction", "TRANSACTION_ID_SESSION_MISMATCH"),
        )
        for name, field, value, issue in cases:
            with self.subTest(name=name):
                chain = self._chain()
                chain["approval"]["approval_session_preview"][field] = value

                result = self._build(chain)

                self.assertEqual(STATUS_INVALID, result["status"])
                self.assertIn(issue, result["issues"])

    def test_hash_mismatch_blocks(self):
        cases = (
            ("projection", "projection_hash", "PROJECTION_HASH_SESSION_MISMATCH"),
            ("policy", "policy_hash", "POLICY_HASH_SESSION_MISMATCH"),
            ("rule", "approved_rule_hash", "APPROVED_RULE_HASH_SESSION_MISMATCH"),
            ("runtime_after", "runtime_after_hash", "RUNTIME_AFTER_HASH_SESSION_MISMATCH"),
        )
        for name, field, issue in cases:
            with self.subTest(name=name):
                chain = self._chain()
                chain["approval"]["approval_session_preview"][field] = f"other-{name}"

                result = self._build(chain)

                self.assertEqual(STATUS_INVALID, result["status"])
                self.assertIn(issue, result["issues"])

    def test_malformed_changed_fields_blocks(self):
        chain = self._chain()
        chain["token_issue"]["token_issue_preview"]["changed_fields"] = []

        result = self._build(chain)

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertIn("CHANGED_FIELDS_MALFORMED", result["issues"])

    def test_context_id_is_deterministic(self):
        chain = self._chain()

        first = self._build(chain)
        second = self._build(chain)

        self.assertEqual(
            first["runtime_commit_execution_context"]["context_id"],
            second["runtime_commit_execution_context"]["context_id"],
        )

    def test_storage_root_is_normalized_and_protected(self):
        normal = self._build(context_input=self._context_input(storage_root=str(Path(self.storage_root) / ".")))
        self.assertEqual(str(Path(self.storage_root).resolve(strict=False)), normal["runtime_commit_execution_context"]["storage_root"])

        traversal = self._build(context_input=self._context_input(storage_root=str(Path(self.storage_root) / ".." / "storage")))
        self.assertEqual(STATUS_INVALID, traversal["status"])
        self.assertIn("STORAGE_ROOT_PATH_TRAVERSAL", traversal["issues"])

        project_runtime = self._build(context_input=self._context_input(storage_root=str(ROOT / "runtime")))
        self.assertEqual(STATUS_INVALID, project_runtime["status"])
        self.assertIn("STORAGE_ROOT_PROJECT_RUNTIME_BLOCKED", project_runtime["issues"])

    def test_input_immutability(self):
        chain = self._chain()
        context_input = self._context_input()
        original = deepcopy((chain, context_input))

        self._build(chain, context_input)

        self.assertEqual(original, (chain, context_input))

    def test_runtime_and_queue_files_are_not_changed(self):
        paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        before = {path: _sha256(path) for path in paths}

        self._build()

        self.assertEqual(before, {path: _sha256(path) for path in paths})

    def test_side_effect_components_are_not_called(self):
        chain = self._chain()
        with (
            mock.patch("runtime_commit_approval_token_store.issue_runtime_commit_approval_token") as issue_token,
            mock.patch("runtime_commit_approval_token_store.consume_runtime_commit_approval_token") as consume_token,
            mock.patch("runtime_commit_real_executor.execute_runtime_commit") as real_executor,
            mock.patch("runtime_commit_guard.acquire_runtime_commit_lock", create=True) as lock,
            mock.patch("runtime_backup_manager.create_runtime_backup_plan") as backup,
            mock.patch("runtime_commit_recovery_journal.record_runtime_commit_journal_event", create=True) as journal,
            mock.patch("runtime_commit_transaction_persistence.write_runtime_transaction_manifest", create=True) as persistence,
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = self._build(chain)

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
        self.assertFalse(result["runtime_commit_real_executor_called"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["broker_called"])
        self.assertFalse(result["gui_updated"])


if __name__ == "__main__":
    unittest.main()
