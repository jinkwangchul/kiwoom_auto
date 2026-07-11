from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest import mock

from buy_execution_runtime_projection_preview import build_buy_runtime_projection_preview
from buy_runtime_commit_core_dry_run_adapter import (
    STATUS_BLOCKED,
    STATUS_INVALID,
    STATUS_READY,
    build_buy_runtime_commit_core_dry_run,
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


class BuyRuntimeCommitCoreDryRunAdapterTest(unittest.TestCase):
    def _runtime(self, **overrides):
        state = {
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
        state.update(overrides)
        return state

    def _snapshot(self, runtime=None):
        return {
            "policy_hash": "policy-hash-1",
            "approved_rule_hash": "approved-rule-hash-1",
            "runtime_state_hash": _stable_hash(self._runtime() if runtime is None else runtime),
            "calculation_hash": "calc-hash-1",
        }

    def _candidate(self, *, snapshot=None):
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
            "execution_snapshot": snapshot or self._snapshot(),
        }
        return {
            "status": "READY",
            "order_candidate_draft": draft,
            "execution_snapshot": deepcopy(draft["execution_snapshot"]),
            "evidence": {"candidate": "ready"},
            "diagnostics": [{"stage": "candidate_draft", "ok": True}],
        }

    def _chain(self):
        runtime = self._runtime()
        projection = build_buy_runtime_projection_preview(
            buy_candidate_preview=self._candidate(snapshot=self._snapshot(runtime)),
            runtime_state_snapshot=runtime,
            execution_policy_snapshot={"policy_hash": "policy-hash-1"},
            projection_context={"preview_timestamp": "2026-07-11T10:00:00+09:00"},
        )
        commit_result = build_buy_runtime_commit_preview(projection)
        gate_result = build_buy_runtime_commit_gate_preview(commit_result)
        return projection, commit_result, gate_result

    def _dry_run(self, gate_result=None, commit_result=None, projection=None):
        projection = projection or self._chain()[0]
        commit_result = commit_result or build_buy_runtime_commit_preview(projection)
        gate_result = gate_result or build_buy_runtime_commit_gate_preview(commit_result)
        return build_buy_runtime_commit_core_dry_run(
            gate_result,
            runtime_commit_preview=commit_result["runtime_commit_preview"],
            runtime_patch_preview=commit_result["runtime_patch_preview"],
        )

    def test_ready_gate_creates_dry_run(self):
        result = self._dry_run()

        self.assertEqual(STATUS_READY, result["status"])
        self.assertTrue(result["dry_run_only"])
        self.assertTrue(result["runtime_commit_core_called"])
        self.assertFalse(result["runtime_commit_real_executor_called"])
        self.assertFalse(result["runtime_write"])
        self.assertFalse(result["queue_write"])
        dry_run = result["runtime_commit_dry_run"]
        self.assertTrue(dry_run["dry_run_id"].startswith("BUY_RUNTIME_COMMIT_DRY_RUN_"))
        self.assertEqual("buy_execution_state", dry_run["target"])

    def test_blocked_and_invalid_gate_do_not_create_dry_run(self):
        _, commit_result, gate_result = self._chain()
        gate_result["status"] = "BLOCKED"
        blocked = self._dry_run(gate_result=gate_result, commit_result=commit_result)
        gate_result["status"] = "INVALID"
        invalid = self._dry_run(gate_result=gate_result, commit_result=commit_result)

        self.assertEqual(STATUS_BLOCKED, blocked["status"])
        self.assertIsNone(blocked["runtime_commit_dry_run"])
        self.assertEqual(STATUS_INVALID, invalid["status"])
        self.assertIsNone(invalid["runtime_commit_dry_run"])

    def test_commit_allowed_false_blocks(self):
        _, commit_result, gate_result = self._chain()
        gate_result["runtime_commit_gate_preview"]["commit_allowed"] = False

        result = self._dry_run(gate_result=gate_result, commit_result=commit_result)

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertIn("COMMIT_ALLOWED_FALSE", result["issues"])

    def test_commit_execute_true_blocks(self):
        _, commit_result, gate_result = self._chain()
        gate_result["runtime_commit_gate_preview"]["commit_execute"] = True

        result = self._dry_run(gate_result=gate_result, commit_result=commit_result)

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertIn("COMMIT_EXECUTE_TRUE", result["issues"])

    def test_transaction_apply_verify_and_rollback_plans_created(self):
        result = self._dry_run()

        self.assertIsInstance(result["transaction_contract_preview"], dict)
        self.assertEqual("CREATED", result["transaction_contract_preview"]["transaction_status"])
        self.assertEqual("preview_runtime_state_patch", result["apply_plan_preview"]["operation"])
        self.assertTrue(result["verification_plan_preview"]["verification_required"])
        self.assertTrue(result["rollback_plan_preview"]["rollback_required_on_failure"])
        self.assertTrue(result["guard_diagnostics"])

    def test_dry_run_id_is_deterministic(self):
        first = self._dry_run()["runtime_commit_dry_run"]["dry_run_id"]
        second = self._dry_run()["runtime_commit_dry_run"]["dry_run_id"]

        self.assertEqual(first, second)
        self.assertEqual(len("BUY_RUNTIME_COMMIT_DRY_RUN_") + 24, len(first))

    def test_hashes_are_preserved(self):
        _, commit_result, gate_result = self._chain()
        result = self._dry_run(gate_result=gate_result, commit_result=commit_result)
        gate = gate_result["runtime_commit_gate_preview"]
        dry_run = result["runtime_commit_dry_run"]

        self.assertEqual(gate["projection_hash"], dry_run["projection_hash"])
        self.assertEqual(gate["policy_hash"], dry_run["policy_hash"])
        self.assertEqual(gate["approved_rule_hash"], dry_run["approved_rule_hash"])
        self.assertEqual(gate["runtime_after_hash"], dry_run["runtime_after_hash"])

    def test_hash_mismatch_blocks(self):
        _, commit_result, gate_result = self._chain()
        commit_result["runtime_commit_preview"]["projection_hash"] = "other"

        result = self._dry_run(gate_result=gate_result, commit_result=commit_result)

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertIn("PROJECTION_HASH_MISMATCH", result["issues"])

    def test_malformed_changed_fields_blocks(self):
        _, commit_result, gate_result = self._chain()
        gate_result["runtime_commit_gate_preview"]["changed_fields"] = "bad"

        result = self._dry_run(gate_result=gate_result, commit_result=commit_result)

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertIn("CHANGED_FIELDS_MALFORMED", result["issues"])

    def test_target_unknown_blocks(self):
        _, commit_result, gate_result = self._chain()
        commit_result["runtime_patch_preview"].pop("target")
        commit_result["runtime_commit_preview"].pop("runtime_target")

        result = self._dry_run(gate_result=gate_result, commit_result=commit_result)

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertIn("TARGET_UNKNOWN", result["issues"])

    def test_preview_only_false_and_write_flags_block(self):
        _, commit_result, gate_result = self._chain()
        gate_result["preview_only"] = False
        gate_result["runtime_write"] = True

        result = self._dry_run(gate_result=gate_result, commit_result=commit_result)

        self.assertEqual(STATUS_INVALID, result["status"])
        self.assertIn("PREVIEW_ONLY_FALSE", result["issues"])
        self.assertTrue(any("runtime_write must be false" in issue for issue in result["issues"]))

    def test_input_immutability(self):
        projection, commit_result, gate_result = self._chain()
        original = deepcopy((projection, commit_result, gate_result))

        self._dry_run(gate_result=gate_result, commit_result=commit_result, projection=projection)

        self.assertEqual(original, (projection, commit_result, gate_result))

    def test_deterministic(self):
        self.assertEqual(self._dry_run(), self._dry_run())

    def test_runtime_and_queue_files_are_not_changed(self):
        paths = [
            ROOT / "runtime" / "order_queue.json",
            ROOT / "runtime" / "order_executions.json",
            ROOT / "runtime" / "order_locks.json",
        ]
        before = {path: _sha256(path) for path in paths}

        self._dry_run()

        self.assertEqual(before, {path: _sha256(path) for path in paths})

    def test_side_effect_components_are_not_called(self):
        with (
            mock.patch("runtime_commit_real_executor.execute_runtime_commit") as real_executor,
            mock.patch("runtime_commit_approval_token_store.consume_runtime_commit_approval_token") as token,
            mock.patch("runtime_commit_guard.acquire_runtime_commit_lock", create=True) as lock,
            mock.patch("runtime_backup_manager.create_runtime_backup_plan") as backup,
            mock.patch("runtime_commit_recovery_journal.record_runtime_commit_journal_event", create=True) as journal,
            mock.patch("runtime_commit_transaction_persistence.save_runtime_commit_transaction_manifest", create=True) as persistence,
            mock.patch("pathlib.Path.write_text") as write_text,
            mock.patch("builtins.open", mock.mock_open()) as open_mock,
        ):
            result = self._dry_run()

        self.assertEqual(STATUS_READY, result["status"])
        real_executor.assert_not_called()
        token.assert_not_called()
        lock.assert_not_called()
        backup.assert_not_called()
        journal.assert_not_called()
        persistence.assert_not_called()
        write_text.assert_not_called()
        open_mock.assert_not_called()
        self.assertFalse(result["approval_token_consumed"])
        self.assertFalse(result["lock_acquired"])
        self.assertFalse(result["backup_created"])
        self.assertFalse(result["journal_written"])
        self.assertFalse(result["send_order_called"])
        self.assertFalse(result["broker_called"])
        self.assertFalse(result["gui_updated"])


if __name__ == "__main__":
    unittest.main()
