from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from buy_runtime_commit_real_executor_handoff_finalizer import (
    STATUS_BLOCKED,
    STATUS_INVALID,
    STATUS_READY,
    calculate_final_execution_plan_hash,
    finalize_runtime_commit_real_executor_handoff,
)
from runtime_commit_approval_token_store import (
    create_runtime_commit_token_storage_plan,
    issue_runtime_commit_approval_token,
    read_runtime_commit_approval_token,
)
from runtime_commit_guard import LOCK_STATUS_RELEASED, read_runtime_commit_lock
from runtime_commit_real_executor import STATUS_COMMITTED, execute_runtime_commit
from runtime_commit_recovery_journal import RECOVERY_STATUS_COMPLETED
from runtime_commit_transaction_contract import build_runtime_commit_transaction_manifest
from runtime_commit_transaction_persistence import read_runtime_transaction_journal


ROOT = Path(__file__).resolve().parents[1]


def _stable_hash(value) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _protected_hashes() -> dict[str, str | None]:
    paths = [
        ROOT / "runtime" / "order_queue.json",
        ROOT / "runtime" / "order_executions.json",
        ROOT / "runtime" / "order_locks.json",
        ROOT / "routines" / "지표추종매매" / "rules.json",
    ]
    return {str(path): _sha256(path) for path in paths}


class HandoffFinalizerHarness:
    def __init__(self, name: str) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix=f"handoff_finalizer_{name}_")
        self.tmp_root = Path(self.tmp.name).resolve()
        self.storage_root = self.tmp_root / "storage"
        self.target_path = (self.tmp_root / "runtime" / "buy_execution_state.json").resolve()
        self.target_path.parent.mkdir(parents=True, exist_ok=True)
        self.before_payload = {
            "current_buy_round": 1,
            "executed_buy_rounds": 1,
            "cumulative_budget": 100000.0,
        }
        self.after_payload = {
            "current_buy_round": 2,
            "executed_buy_rounds": 2,
            "cumulative_budget": 250000.0,
        }
        self.target_path.write_text(
            json.dumps(self.before_payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        suffix = hashlib.sha256(str(self.tmp_root).encode("utf-8")).hexdigest()[:12]
        self.commit_id = f"commit-{name}-{suffix}"
        self.token_id = f"token-{name}-{suffix}"
        self.consumer_id = f"consumer-{name}-{suffix}"
        self.logical_target = "buy_execution_state"
        self.actual_path = str(self.target_path)
        self.before_targets = {self.actual_path: deepcopy(self.before_payload)}
        self.after_targets = {self.actual_path: deepcopy(self.after_payload)}
        self.before_hash = _stable_hash(self.before_targets)
        self.after_hash = _stable_hash(self.after_targets)
        self.plan_hash = calculate_final_execution_plan_hash(
            target_paths=[self.actual_path],
            before_payload_hash=self.before_hash,
            after_payload_hash=self.after_hash,
        )
        self.manifest = build_runtime_commit_transaction_manifest(
            commit_id=self.commit_id,
            target_paths=[self.actual_path],
            execution_plan_hash=self.plan_hash,
            approval_token_id=self.token_id,
            expected_payload_hash=self.before_hash,
            backup_plan_hash="backup-hash",
            rollback_plan_hash="rollback-hash",
            metadata={"logical_targets": [self.logical_target]},
        )
        self.transaction_id = self.manifest["transaction_id"]
        self.token_storage_plan = create_runtime_commit_token_storage_plan(
            storage_root=str(self.storage_root),
            token_id=self.token_id,
            commit_id=self.commit_id,
        )
        self.issue_token()

    def cleanup(self) -> None:
        self.tmp.cleanup()

    def issue_token(self) -> None:
        result = issue_runtime_commit_approval_token(
            storage_plan=self.token_storage_plan,
            token={
                "token_id": self.token_id,
                "commit_id": self.commit_id,
                "plan_hash": self.plan_hash,
                "issued_for": self.consumer_id,
                "issued_by": "operator-1",
                "scope": "RUNTIME_COMMIT_EXECUTION",
                "single_use": True,
                "metadata": {
                    "commit_id": self.commit_id,
                    "transaction_id": self.transaction_id,
                    "plan_hash": self.plan_hash,
                    "candidate_id": "candidate-1",
                },
            },
        )
        if result["issue_status"] != "ISSUED":
            raise AssertionError(result)

    def handoff(self) -> dict:
        return {
            "handoff_version": "BUY_RUNTIME_COMMIT_REAL_EXECUTOR_HANDOFF_PREVIEW_V1",
            "handoff_id": "HANDOFF_1",
            "token_id": self.token_id,
            "commit_id": self.commit_id,
            "transaction_id": self.transaction_id,
            "candidate_id": "candidate-1",
            "consumer_id": self.consumer_id,
            "scope": "RUNTIME_COMMIT_EXECUTION",
            "plan_hash": self.plan_hash,
            "transaction_manifest": deepcopy(self.manifest),
            "token_storage_plan": deepcopy(self.token_storage_plan),
            "gate_result": {
                "gate_status": "APPROVED",
                "commit_id": self.commit_id,
                "preview_only": True,
                "execution_allowed": False,
                "actual_execution": False,
                "token_consumed": False,
                "gate_metadata": {"plan_hash": self.plan_hash},
                "issues": [],
                "warnings": [],
            },
            "expected_targets": {self.logical_target: {}},
            "new_targets": {self.logical_target: {}},
            "preview_only": True,
            "runtime_write": False,
        }

    def finalize(self, **overrides) -> dict:
        kwargs = {
            "real_executor_handoff_preview": self.handoff(),
            "storage_root": str(self.storage_root),
            "target_path_allowlist": {self.logical_target: self.actual_path},
            "runtime_before_payloads": {self.logical_target: deepcopy(self.before_payload)},
            "runtime_after_payloads": {self.logical_target: deepcopy(self.after_payload)},
            "token_id": self.token_id,
        }
        kwargs.update(overrides)
        return finalize_runtime_commit_real_executor_handoff(**kwargs)


class BuyRuntimeCommitRealExecutorHandoffFinalizerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_before = _protected_hashes()
        self.harnesses: list[HandoffFinalizerHarness] = []

    def tearDown(self) -> None:
        for harness in self.harnesses:
            harness.cleanup()
        self.assertEqual(self.protected_before, _protected_hashes())

    def _harness(self, name: str) -> HandoffFinalizerHarness:
        harness = HandoffFinalizerHarness(name)
        self.harnesses.append(harness)
        return harness

    def test_finalizer_ready_maps_logical_target_to_tempfile_contract(self) -> None:
        h = self._harness("ready")
        original = deepcopy(
            (
                h.handoff(),
                {h.logical_target: h.actual_path},
                {h.logical_target: h.before_payload},
                {h.logical_target: h.after_payload},
            )
        )

        result = h.finalize()

        self.assertEqual(STATUS_READY, result["status"])
        self.assertEqual(h.before_targets, result["finalized_expected_targets"])
        self.assertEqual(h.after_targets, result["finalized_new_targets"])
        manifest = result["finalized_transaction_manifest"]
        self.assertEqual(h.token_id, manifest["approval_token_id"])
        self.assertEqual(1, len(manifest["target_paths"]))
        self.assertTrue(manifest["target_paths"][0].endswith("/runtime/buy_execution_state.json"))
        self.assertEqual(h.plan_hash, manifest["execution_plan_hash"])
        self.assertEqual(h.before_hash, manifest["expected_payload_hash"])
        self.assertEqual(h.transaction_id, manifest["transaction_id"])
        finalized_input = result["finalized_real_executor_input"]
        self.assertEqual(h.after_targets, finalized_input["expected_targets"])
        self.assertEqual(h.after_targets, finalized_input["new_targets"])
        self.assertEqual(h.consumer_id, finalized_input["consumer_id"])
        self.assertEqual("READY", result["finalized_storage_plan"]["storage_status"])
        self.assertEqual("READY", result["finalized_guard_plan"]["guard_status"])
        self.assertEqual(
            original,
            (
                h.handoff(),
                {h.logical_target: h.actual_path},
                {h.logical_target: h.before_payload},
                {h.logical_target: h.after_payload},
            ),
        )

    def test_tempfile_real_executor_e2e_commits_consumes_token_and_releases_lock(self) -> None:
        h = self._harness("e2e")
        finalized = h.finalize()
        self.assertEqual(STATUS_READY, finalized["status"], finalized["issues"])

        result = execute_runtime_commit(**finalized["finalized_real_executor_input"])

        self.assertEqual(STATUS_COMMITTED, result["execute_status"])
        self.assertTrue(result["write_executed"])
        self.assertTrue(result["verify_passed"])
        self.assertTrue(result["token_consumed"])
        self.assertTrue(result["lock_released"])
        self.assertEqual(
            h.after_payload,
            json.loads(h.target_path.read_text(encoding="utf-8")),
        )
        token = read_runtime_commit_approval_token(storage_plan=h.token_storage_plan)["token"]
        self.assertEqual("CONSUMED", token["token_status"])
        lock = read_runtime_commit_lock(guard_plan=finalized["finalized_guard_plan"])
        self.assertEqual(LOCK_STATUS_RELEASED, lock["lock_record"]["lock_status"])
        self.assertTrue((h.storage_root / "backups" / h.commit_id).exists())
        journal = read_runtime_transaction_journal(storage_plan=finalized["finalized_storage_plan"])
        self.assertEqual("OK", journal["read_status"])
        self.assertIn("COMPLETED", [event["stage"] for event in journal["events"]])
        terminal_statuses = []
        for path in h.storage_root.glob("**/*.journal.jsonl"):
            for line in path.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                if event.get("is_terminal"):
                    terminal_statuses.append(event.get("status"))
        self.assertIn(RECOVERY_STATUS_COMPLETED, terminal_statuses)

    def test_unmapped_and_duplicate_targets_block(self) -> None:
        h = self._harness("target-block")
        unmapped = h.finalize(target_path_allowlist={})
        duplicate = h.finalize(
            target_path_allowlist={
                "buy_execution_state": h.actual_path,
                "buy_execution_state_copy": h.actual_path,
            },
            runtime_before_payloads={
                "buy_execution_state": deepcopy(h.before_payload),
                "buy_execution_state_copy": deepcopy(h.before_payload),
            },
            runtime_after_payloads={
                "buy_execution_state": deepcopy(h.after_payload),
                "buy_execution_state_copy": deepcopy(h.after_payload),
            },
        )

        self.assertEqual(STATUS_INVALID, unmapped["status"])
        self.assertIn("TARGET_PATH_ALLOWLIST_REQUIRED", unmapped["issues"])
        self.assertEqual(STATUS_BLOCKED, duplicate["status"])
        self.assertIn("DUPLICATE_TARGET_PATH", duplicate["issues"])

    def test_project_runtime_relative_traversal_and_symlink_escape_block(self) -> None:
        h = self._harness("path-block")
        project_runtime = h.finalize(
            target_path_allowlist={h.logical_target: str(ROOT / "runtime" / "order_queue.json")}
        )
        relative = h.finalize(target_path_allowlist={h.logical_target: "runtime/buy_execution_state.json"})
        traversal = h.finalize(target_path_allowlist={h.logical_target: str(h.tmp_root / ".." / "escape.json")})

        self.assertEqual(STATUS_BLOCKED, project_runtime["status"])
        self.assertTrue(any("TARGET_PATH_BLOCKED" in issue for issue in project_runtime["issues"]))
        self.assertEqual(STATUS_BLOCKED, relative["status"])
        self.assertTrue(any("TARGET_PATH_BLOCKED" in issue for issue in relative["issues"]))
        self.assertEqual(STATUS_BLOCKED, traversal["status"])
        self.assertTrue(any("TARGET_PATH_BLOCKED" in issue for issue in traversal["issues"]))

        outside = Path(tempfile.mkdtemp(prefix="handoff_escape_")).resolve()
        self.addCleanup(lambda: shutil.rmtree(outside, ignore_errors=True))
        outside_file = outside / "outside.json"
        outside_file.write_text(json.dumps(h.before_payload), encoding="utf-8")
        link_path = h.tmp_root / "runtime" / "link.json"
        try:
            link_path.symlink_to(outside_file)
        except OSError:
            self.skipTest("symlink creation is not available in this environment")
        symlink = h.finalize(target_path_allowlist={h.logical_target: str(link_path)})
        self.assertEqual(STATUS_BLOCKED, symlink["status"])
        self.assertTrue(any("symlink escapes" in issue for issue in symlink["issues"]))

    def test_malformed_payload_and_before_mismatch_block(self) -> None:
        h = self._harness("payload-block")
        malformed = h.finalize(runtime_before_payloads={h.logical_target: ["bad"]})
        mismatch = h.finalize(runtime_before_payloads={h.logical_target: {"unexpected": True}})

        self.assertEqual(STATUS_INVALID, malformed["status"])
        self.assertIn("runtime_before_payloads.buy_execution_state must be a dict", malformed["issues"])
        self.assertEqual(STATUS_BLOCKED, mismatch["status"])
        self.assertIn("BEFORE_PAYLOAD_MISMATCH:buy_execution_state", mismatch["issues"])

    def test_token_commit_transaction_plan_and_placeholder_mismatch_block(self) -> None:
        h = self._harness("identity-block")
        token_id_mismatch = h.finalize(token_id="other-token")

        commit_handoff = h.handoff()
        commit_handoff["commit_id"] = "other-commit"
        commit_mismatch = h.finalize(real_executor_handoff_preview=commit_handoff)

        tx_handoff = h.handoff()
        tx_handoff["transaction_id"] = "other-transaction"
        tx_handoff["transaction_manifest"]["transaction_id"] = "other-transaction"
        transaction_mismatch = h.finalize(real_executor_handoff_preview=tx_handoff)

        plan_handoff = h.handoff()
        plan_handoff["plan_hash"] = "other-plan"
        plan_handoff["transaction_manifest"]["execution_plan_hash"] = "other-plan"
        plan_mismatch = h.finalize(real_executor_handoff_preview=plan_handoff)

        placeholder = h.handoff()
        placeholder["transaction_manifest"]["approval_token_id"] = "DRY_RUN_ONLY_NO_TOKEN"
        placeholder_result = h.finalize(real_executor_handoff_preview=placeholder)

        self.assertEqual(STATUS_BLOCKED, token_id_mismatch["status"])
        self.assertIn("TOKEN_ID_MISMATCH", token_id_mismatch["issues"])
        self.assertEqual(STATUS_BLOCKED, commit_mismatch["status"])
        self.assertIn("TOKEN_COMMIT_ID_MISMATCH", commit_mismatch["issues"])
        self.assertEqual(STATUS_BLOCKED, transaction_mismatch["status"])
        self.assertIn("TOKEN_METADATA_TRANSACTION_ID_MISMATCH", transaction_mismatch["issues"])
        self.assertEqual(STATUS_BLOCKED, plan_mismatch["status"])
        self.assertIn("EXECUTION_PLAN_HASH_MISMATCH", plan_mismatch["issues"])
        self.assertEqual(STATUS_BLOCKED, placeholder_result["status"])
        self.assertIn("PLACEHOLDER_APPROVAL_TOKEN_ID", placeholder_result["issues"])

    def test_deterministic_output_for_same_input(self) -> None:
        h = self._harness("deterministic")

        first = h.finalize()
        second = h.finalize()

        self.assertEqual(STATUS_READY, first["status"])
        self.assertEqual(first["finalized_transaction_manifest"], second["finalized_transaction_manifest"])
        self.assertEqual(first["finalized_expected_targets"], second["finalized_expected_targets"])
        self.assertEqual(first["finalized_new_targets"], second["finalized_new_targets"])
        self.assertEqual(first["finalization_summary"], second["finalization_summary"])


if __name__ == "__main__":
    unittest.main()
