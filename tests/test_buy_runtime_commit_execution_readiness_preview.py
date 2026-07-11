from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest import mock

from buy_execution_runtime_projection_preview import build_buy_runtime_projection_preview
from buy_runtime_commit_core_dry_run_adapter import build_buy_runtime_commit_core_dry_run
from buy_runtime_commit_execution_readiness_preview import (
    STATUS_BLOCKED,
    STATUS_INVALID,
    STATUS_READY,
    build_buy_runtime_commit_execution_readiness_preview,
)
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


class BuyRuntimeCommitExecutionReadinessPreviewTest(unittest.TestCase):
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

    def _candidate(self, snapshot):
        draft = {
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
        }
        return {
            "status": "READY",
            "order_candidate_draft": draft,
            "execution_snapshot": deepcopy(snapshot),
            "evidence": {"candidate": "ready"},
            "diagnostics": [{"stage": "candidate_draft", "ok": True}],
        }

    def _chain(self):
        runtime = self._runtime()
        snapshot = self._snapshot(runtime)
        projection = build_buy_runtime_projection_preview(
            buy_candidate_preview=self._candidate(snapshot),
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
        return projection, commit_result, gate_result, dry_run

    def _context(self, gate_result=None):
        gate_result = gate_result or self._chain()[2]
        return {
            "gate_summary": deepcopy(gate_result["gate_summary"]),
            "risk_level": "medium",
        }

    def _readiness(self, dry_run=None, context=None):
        _projection, _commit, gate, chain_dry_run = self._chain()
        return build_buy_runtime_commit_execution_readiness_preview(
            dry_run or chain_dry_run,
            context if context is not None else self._context(gate),
        )

    def test_ready_dry_run_creates_readiness_preview(self):
        result = self._readiness()

        self.assertEqual(STATUS_READY, result["status"])
        self.assertTrue(result["approval_required"])
        self.assertFalse(result["approval_granted"])
        self.assertFalse(result["execution_allowed"])
        self.assertFalse(result["execution_started"])
        self.assertFalse(result["runtime_commit_execute"])
        self.assertFalse(result["runtime_write"])
        self.assertIsInstance(result["execution_readiness_preview"], dict)
        self.assertIsInstance(result["approval_request_preview"], dict)

    def test_blocked_and_invalid_dry_run_do_not_create_readiness(self):
        _projection, _commit, gate, dry_run = self._chain()
        blocked_input = deepcopy(dry_run)
        blocked_input["status"] = "BLOCKED"
        invalid_input = deepcopy(dry_run)
        invalid_input["status"] = "INVALID"

        blocked = build_buy_runtime_commit_execution_readiness_preview(blocked_input, self._context(gate))
        invalid = build_buy_runtime_commit_execution_readiness_preview(invalid_input, self._context(gate))

        self.assertEqual(STATUS_BLOCKED, blocked["status"])
        self.assertIsNone(blocked["execution_readiness_preview"])
        self.assertEqual(STATUS_INVALID, invalid["status"])
        self.assertIsNone(invalid["execution_readiness_preview"])

    def test_approval_request_id_is_deterministic(self):
        first = self._readiness()["approval_request_preview"]["approval_request_id"]
        second = self._readiness()["approval_request_preview"]["approval_request_id"]

        self.assertEqual(first, second)
        self.assertTrue(first.startswith("BUY_RUNTIME_COMMIT_APPROVAL_"))
        self.assertEqual(len("BUY_RUNTIME_COMMIT_APPROVAL_") + 24, len(first))

    def test_approval_request_contract(self):
        result = self._readiness()
        approval = result["approval_request_preview"]

        self.assertEqual("BUY_RUNTIME_COMMIT_APPROVAL_REQUEST_V1", approval["approval_version"])
        self.assertEqual(result["execution_readiness_preview"]["dry_run_id"], approval["dry_run_id"])
        self.assertEqual("BUY_ORDER_CANDIDATE_1", approval["candidate_id"])
        self.assertTrue(approval["transaction_id"])
        self.assertEqual("medium", approval["risk_level"])
        self.assertTrue(approval["approval_required"])
        self.assertFalse(approval["approval_granted"])
        self.assertFalse(approval["execution_allowed"])
        self.assertFalse(approval["execution_started"])

    def test_plans_are_preserved(self):
        _projection, _commit, gate, dry_run = self._chain()
        result = build_buy_runtime_commit_execution_readiness_preview(dry_run, self._context(gate))

        self.assertEqual(dry_run["transaction_contract_preview"]["transaction_id"], result["approval_request_preview"]["transaction_id"])
        self.assertEqual(dry_run["apply_plan_preview"]["target"], result["approval_request_preview"]["target"])
        self.assertEqual(dry_run["verification_plan_preview"]["plan_type"], "BUY_RUNTIME_COMMIT_VERIFICATION_PLAN_PREVIEW")
        self.assertEqual(dry_run["rollback_plan_preview"]["plan_type"], "BUY_RUNTIME_COMMIT_ROLLBACK_PLAN_PREVIEW")

    def test_hashes_are_preserved_and_mismatch_blocks(self):
        _projection, _commit, gate, dry_run = self._chain()
        result = build_buy_runtime_commit_execution_readiness_preview(dry_run, self._context(gate))
        readiness = result["execution_readiness_preview"]

        self.assertEqual(dry_run["runtime_commit_dry_run"]["projection_hash"], readiness["projection_hash"])
        self.assertEqual(dry_run["runtime_commit_dry_run"]["policy_hash"], readiness["policy_hash"])
        broken = deepcopy(dry_run)
        broken["transaction_contract_preview"]["metadata"]["projection_hash"] = "other"
        blocked = build_buy_runtime_commit_execution_readiness_preview(broken, self._context(gate))
        self.assertEqual(STATUS_INVALID, blocked["status"])
        self.assertIn("PROJECTION_HASH_MISMATCH", blocked["issues"])

    def test_blocking_guard_diagnostic_blocks(self):
        _projection, _commit, gate, dry_run = self._chain()
        dry_run["guard_diagnostics"].append({"name": "manual_review", "ok": False, "blocking": True})

        result = build_buy_runtime_commit_execution_readiness_preview(dry_run, self._context(gate))

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertTrue(any("BLOCKING_GUARD_DIAGNOSTIC" in issue for issue in result["issues"]))

    def test_changed_fields_empty_blocks(self):
        _projection, _commit, gate, dry_run = self._chain()
        dry_run["runtime_commit_dry_run"]["changed_fields"] = []

        result = build_buy_runtime_commit_execution_readiness_preview(dry_run, self._context(gate))

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertIn("CHANGED_FIELDS_EMPTY", result["issues"])

    def test_preset_approval_or_execution_blocks(self):
        _projection, _commit, gate, dry_run = self._chain()

        approval = build_buy_runtime_commit_execution_readiness_preview(
            dry_run,
            {"gate_summary": gate["gate_summary"], "approval_granted": True},
        )
        execution = build_buy_runtime_commit_execution_readiness_preview(
            dry_run,
            {"gate_summary": gate["gate_summary"], "execution_allowed": True},
        )

        self.assertEqual(STATUS_INVALID, approval["status"])
        self.assertIn("APPROVAL_GRANTED_PRESET", approval["issues"])
        self.assertEqual(STATUS_INVALID, execution["status"])
        self.assertIn("EXECUTION_ALLOWED_PRESET", execution["issues"])

    def test_readiness_summary(self):
        result = self._readiness()
        summary = result["readiness_summary"]

        self.assertTrue(summary["ready"])
        self.assertTrue(summary["approval_required"])
        self.assertFalse(summary["approval_granted"])
        self.assertFalse(summary["execution_allowed"])
        self.assertEqual(11, summary["changed_fields_count"])
        self.assertEqual("buy_execution_state", summary["target"])
        self.assertEqual(2, summary["current_buy_round"])
        self.assertEqual(2, summary["executed_buy_rounds"])
        self.assertEqual(250000.0, summary["cumulative_budget"])
        self.assertFalse(summary["is_last_round"])
        self.assertEqual("medium", summary["risk_level"])
        self.assertTrue(summary["projection_hash"])
        self.assertEqual("BUY_EXECUTION_POLICY_V1", summary["policy_version"])

    def test_readiness_report_sections(self):
        result = self._readiness()
        report = result["readiness_report"]

        self.assertEqual("Runtime Commit Execution Readiness", report["title"])
        self.assertEqual(
            [
                "Decision",
                "Approval State",
                "Candidate",
                "Transaction Contract",
                "Apply Plan",
                "Verification Plan",
                "Rollback Plan",
                "Changed Fields",
                "Hashes",
                "Warnings",
                "Diagnostics",
            ],
            [section["title"] for section in report["sections"]],
        )

    def test_input_immutability_and_deterministic(self):
        _projection, _commit, gate, dry_run = self._chain()
        context = self._context(gate)
        original = deepcopy((dry_run, context))

        first = build_buy_runtime_commit_execution_readiness_preview(dry_run, context)
        second = build_buy_runtime_commit_execution_readiness_preview(dry_run, context)

        self.assertEqual(original, (dry_run, context))
        self.assertEqual(first, second)

    def test_runtime_and_queue_files_are_not_changed(self):
        paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        before = {path: _sha256(path) for path in paths}

        self._readiness()

        self.assertEqual(before, {path: _sha256(path) for path in paths})

    def test_side_effect_components_are_not_called(self):
        with (
            mock.patch("runtime_commit_real_executor.execute_runtime_commit") as real_executor,
            mock.patch("runtime_commit_approval_token_store.issue_runtime_commit_approval_token") as issue_token,
            mock.patch("runtime_commit_approval_token_store.consume_runtime_commit_approval_token") as consume_token,
            mock.patch("runtime_commit_guard.acquire_runtime_commit_lock", create=True) as lock,
            mock.patch("runtime_backup_manager.create_runtime_backup_plan") as backup,
            mock.patch("runtime_commit_recovery_journal.record_runtime_commit_journal_event", create=True) as journal,
            mock.patch("runtime_commit_transaction_persistence.save_runtime_commit_transaction_manifest", create=True) as persistence,
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = self._readiness()

        self.assertEqual(STATUS_READY, result["status"])
        real_executor.assert_not_called()
        issue_token.assert_not_called()
        consume_token.assert_not_called()
        lock.assert_not_called()
        backup.assert_not_called()
        journal.assert_not_called()
        persistence.assert_not_called()
        write_text.assert_not_called()
        open_mock.assert_not_called()
        self.assertFalse(result["runtime_commit_real_executor_called"])
        self.assertFalse(result["approval_token_issued"])
        self.assertFalse(result["approval_token_consumed"])
        self.assertFalse(result["lock_acquired"])
        self.assertFalse(result["backup_created"])
        self.assertFalse(result["journal_written"])
        self.assertFalse(result["persistence_write"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["broker_called"])
        self.assertFalse(result["gui_updated"])


if __name__ == "__main__":
    unittest.main()
