# -*- coding: utf-8 -*-
"""Lifecycle -> Builder -> Adapter -> Real Executor integration tests.

All real file IO in this module is constrained to tempfile roots.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from execution_runtime_commit_plan_orchestrator import ORCHESTRATOR_TYPE
from lifecycle_commit_service import commit_lifecycle
from lifecycle_commit_writer import LifecycleCommitWriter
from lifecycle_runtime_commit_builder import build_lifecycle_runtime_commit_adapter_request
from runtime_commit_approval_token_store import (
    issue_runtime_commit_approval_token,
    read_runtime_commit_approval_token,
)
from runtime_commit_guard import acquire_runtime_commit_lock, read_runtime_commit_lock
from runtime_commit_recovery_journal import RECOVERY_STATUS_COMPLETED, RECOVERY_STATUS_ROLLED_BACK
from runtime_commit_transaction_persistence import read_runtime_transaction_journal


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROJECT_RUNTIME = PROJECT_ROOT / "runtime"
PROJECT_ROUTINES = PROJECT_ROOT / "routines"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _protected_hashes() -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(PROJECT_RUNTIME.glob("*.json")):
        if path.is_file():
            hashes[str(path)] = _sha256(path)
    for path in sorted(PROJECT_ROUTINES.glob("**/rules.json")):
        if path.is_file():
            hashes[str(path)] = _sha256(path)
    return hashes


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _transaction_journal_stages(storage_root: Path) -> list[str]:
    stages: list[str] = []
    for path in sorted(storage_root.glob("transactions/*/journal.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                stages.append(json.loads(line).get("stage", ""))
    return stages


def _rows(db_path: Path, table: str) -> list[dict[str, object]]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()]
    finally:
        conn.close()


class LifecycleRuntimeCommitE2EHarness:
    def __init__(self, testcase: unittest.TestCase, name: str) -> None:
        self.testcase = testcase
        self.tmp_dir = Path(tempfile.mkdtemp(prefix=f"m7_03a_{name}_"))
        self.storage_root = self.tmp_dir / "storage"
        self.target_path = self.tmp_dir / "targets" / f"{name}.json"
        self.target_path.parent.mkdir(parents=True, exist_ok=True)
        self.before_payload = {"value": "before", "case": name}
        self.after_payload = {"value": "after", "case": name}
        self.target_path.write_text(
            json.dumps(self.before_payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        digest = hashlib.sha256(str(self.tmp_dir).encode("utf-8")).hexdigest()[:12]
        self.commit_id = f"m7-03a-{name}-{digest}"
        self.transaction_id = f"tx-{digest}"
        self.token_id = f"token-{name}-{digest}"
        self.owner_id = f"owner-{name}-{digest}"
        self.db_path = self.tmp_dir / "lifecycle.sqlite3"
        self.writer = LifecycleCommitWriter(self.db_path)

    def cleanup(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def lifecycle_request(self, **overrides: object) -> dict[str, object]:
        request: dict[str, object] = {
            "lifecycle_id": f"life-{self.commit_id}",
            "commit_id": self.commit_id,
            "transaction_id": self.transaction_id,
            "requested_action": "RUNTIME_COMMIT",
            "source_stage": "LIFECYCLE_COMMIT_SERVICE",
            "runtime_commit_boundary_status": "RUNTIME_COMMIT_BOUNDARY_READY",
            "preview_only": True,
            "metadata": {"case": self.commit_id},
        }
        request.update(overrides)
        return request

    def preview(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "status": "LIFECYCLE_COMMIT_READY",
            "commit_contract": {
                "contract_type": "ORDER_LIFECYCLE_COMMIT_CONTRACT_PREVIEW",
                "commit_id": self.commit_id,
                "lifecycle_commit_request": self.lifecycle_request(),
                "preview_only": True,
                "runtime_write": False,
                "queue_write": False,
                "candidate_lifecycle_event": "ORDER_RECEIVED",
                "order_id": f"ORDER-{self.commit_id}",
                "evidence_id": f"EVIDENCE-{self.commit_id}",
                "record_id": f"RECORD-{self.commit_id}",
                "dispatch_id": f"DISPATCH-{self.commit_id}",
                "source_signal_id": f"SIGNAL-{self.commit_id}",
                "order_queued_id": f"ORDER-QUEUED-{self.commit_id}",
                "target_name": "temp_lifecycle",
                "lifecycle_store": "temp_store",
                "required_next_service": "ORDER_LIFECYCLE_COMMIT_SERVICE",
            },
            "commit_plan": {
                "plan_type": "ORDER_LIFECYCLE_COMMIT_PLAN_PREVIEW",
                "preview_only": True,
                "runtime_write": False,
                "queue_write": False,
            },
            "issues": [],
            "warnings": [],
            "preview_only": True,
            "runtime_write": False,
            "queue_write": False,
        }
        result.update(overrides)
        return result

    def commit_plan(self, *, planned_targets: dict[str, object] | None = None) -> dict[str, object]:
        return {
            "planned_records": [{"record_id": f"planned-{self.commit_id}"}],
            "planned_targets": planned_targets
            if planned_targets is not None
            else {str(self.target_path): copy.deepcopy(self.after_payload)},
        }

    def orchestrator(self, *, commit_plan: dict[str, object] | None = None) -> dict[str, object]:
        return {
            "orchestrator_type": ORCHESTRATOR_TYPE,
            "status": "READY",
            "commit_ready": True,
            "commit_plan": commit_plan or self.commit_plan(),
            "issues": [],
            "warnings": [],
        }

    def context(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "storage_root": str(self.storage_root),
            "owner_id": self.owner_id,
            "token_id": self.token_id,
            "consumer_id": self.owner_id,
        }
        result.update(overrides)
        return result

    def build_payload(self, *, preview: dict | None = None, plan: dict | None = None, context: dict | None = None) -> dict:
        ctx = context or self.context()
        return build_lifecycle_runtime_commit_adapter_request(
            commit_contract_preview=preview or self.preview(),
            commit_plan=plan or self.commit_plan(),
            storage_root=ctx["storage_root"],
            owner_id=ctx["owner_id"],
            token_id=ctx.get("token_id"),
            execution_plan_hash=ctx.get("execution_plan_hash"),
            expected_payload_hash=ctx.get("expected_payload_hash"),
            consumer_override=ctx.get("consumer_id"),
        )

    def issue_token(self, payload: dict, *, commit_id: str | None = None, plan_hash: str | None = None) -> dict:
        return issue_runtime_commit_approval_token(
            storage_plan=payload["token_storage_plan"],
            token={
                "token_id": payload["token_storage_plan"]["token_id"],
                "commit_id": commit_id or payload["transaction_manifest"]["commit_id"],
                "plan_hash": plan_hash or payload["transaction_manifest"]["execution_plan_hash"],
                "issued_for": payload["consumer_id"],
                "issued_by": "m7-03a-test",
                "scope": "RUNTIME_COMMIT_EXECUTION",
                "single_use": True,
            },
        )

    def terminal_recovery_statuses(self) -> list[str]:
        statuses: list[str] = []
        for path in sorted(self.storage_root.glob("**/*.journal.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                event = json.loads(line)
                if event.get("is_terminal"):
                    statuses.append(event.get("status", ""))
        return statuses


class TestLifecycleRuntimeCommitEndToEnd(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_before = _protected_hashes()
        self.harnesses: list[LifecycleRuntimeCommitE2EHarness] = []

    def tearDown(self) -> None:
        for harness in self.harnesses:
            harness.cleanup()
        self.assertEqual(self.protected_before, _protected_hashes())

    def _harness(self, name: str) -> LifecycleRuntimeCommitE2EHarness:
        harness = LifecycleRuntimeCommitE2EHarness(self, name)
        self.harnesses.append(harness)
        return harness

    def _run_service_with_spies(self, h: LifecycleRuntimeCommitE2EHarness, *, context: dict | None = None, plan: dict | None = None):
        builder_results: list[dict] = []
        adapter_results: list[dict] = []
        executor_results: list[dict] = []

        import lifecycle_commit_service
        import lifecycle_runtime_commit_adapter
        import runtime_commit_real_executor

        real_builder = lifecycle_commit_service.build_lifecycle_runtime_commit_adapter_request
        real_adapter = lifecycle_runtime_commit_adapter.adapt_and_execute_lifecycle_runtime_commit
        real_executor = runtime_commit_real_executor.execute_runtime_commit

        def builder_spy(**kwargs):
            result = real_builder(**kwargs)
            builder_results.append(copy.deepcopy(result))
            return result

        def adapter_spy(**kwargs):
            result = real_adapter(**kwargs)
            adapter_results.append(copy.deepcopy(result))
            return result

        def executor_spy(**kwargs):
            result = real_executor(**kwargs)
            executor_results.append(copy.deepcopy(result))
            return result

        with mock.patch("lifecycle_commit_service.build_lifecycle_runtime_commit_adapter_request", side_effect=builder_spy) as builder, \
             mock.patch("lifecycle_runtime_commit_adapter.adapt_and_execute_lifecycle_runtime_commit", side_effect=adapter_spy) as adapter, \
             mock.patch("runtime_commit_real_executor.execute_runtime_commit", side_effect=executor_spy) as executor, \
             mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as legacy:
            result = commit_lifecycle(
                h.preview(),
                h.orchestrator(commit_plan=plan),
                h.writer,
                queue_commit_executor=lambda commit_plan: {"ok": True},
                context=context or h.context(),
            )

        return {
            "service_result": result,
            "builder": builder,
            "adapter": adapter,
            "executor": executor,
            "legacy": legacy,
            "builder_results": builder_results,
            "adapter_results": adapter_results,
            "executor_results": executor_results,
        }

    def test_01_full_path_commits_through_builder_adapter_and_real_executor(self) -> None:
        h = self._harness("success")
        payload = h.build_payload()
        h.issue_token(payload)

        preview_snapshot = copy.deepcopy(h.preview())
        plan_snapshot = copy.deepcopy(h.commit_plan())
        context_snapshot = copy.deepcopy(h.context())
        payload_snapshot = copy.deepcopy(payload)

        observed = self._run_service_with_spies(h)
        result = observed["service_result"]
        executor_result = observed["executor_results"][0]
        adapter_result = observed["adapter_results"][0]
        builder_result = observed["builder_results"][0]

        self.assertEqual("COMMITTED", result["status"])
        self.assertEqual("COMMITTED", adapter_result["adapter_status"])
        self.assertEqual("COMMITTED", executor_result["execute_status"])
        self.assertEqual(1, observed["builder"].call_count)
        self.assertEqual(1, observed["adapter"].call_count)
        self.assertEqual(1, observed["executor"].call_count)
        observed["legacy"].assert_not_called()

        self.assertEqual("APPROVED", builder_result["gate_result"]["gate_status"])
        self.assertEqual(h.commit_id, builder_result["gate_result"]["commit_id"])
        self.assertEqual(h.commit_id, builder_result["transaction_manifest"]["commit_id"])
        self.assertEqual(h.transaction_id, builder_result["transaction_manifest"]["transaction_id"])
        self.assertEqual(payload["transaction_manifest"]["execution_plan_hash"], builder_result["transaction_manifest"]["execution_plan_hash"])
        self.assertEqual(payload["transaction_manifest"]["target_set_hash"], builder_result["transaction_manifest"]["target_set_hash"])
        self.assertEqual(set(builder_result["expected_targets"].keys()), set(builder_result["new_targets"].keys()))
        self.assertTrue(all(str(Path(path)).lower().startswith(str(h.tmp_dir).lower()) for path in builder_result["new_targets"]))

        self.assertTrue(executor_result["backup_created"])
        self.assertTrue(executor_result["write_executed"])
        self.assertTrue(executor_result["verify_passed"])
        self.assertTrue(executor_result["token_consumed"])
        self.assertTrue(executor_result["lock_released"])
        self.assertFalse(executor_result["rollback_executed"])
        self.assertFalse(executor_result["manual_restore_required"])
        self.assertEqual(_read_json(h.target_path), h.after_payload)

        self.assertIn("COMPLETED", _transaction_journal_stages(h.storage_root))
        self.assertIn(RECOVERY_STATUS_COMPLETED, h.terminal_recovery_statuses())
        token = read_runtime_commit_approval_token(storage_plan=builder_result["token_storage_plan"])["token"]
        self.assertEqual("CONSUMED", token["token_status"])

        self.assertEqual(preview_snapshot, h.preview())
        self.assertEqual(plan_snapshot, h.commit_plan())
        self.assertEqual(context_snapshot, h.context())
        self.assertEqual(payload_snapshot, payload)

    def test_02_missing_storage_root_blocks_before_builder_adapter_and_executor(self) -> None:
        h = self._harness("missing-storage")
        import lifecycle_commit_service

        with mock.patch("lifecycle_commit_service.build_lifecycle_runtime_commit_adapter_request", wraps=lifecycle_commit_service.build_lifecycle_runtime_commit_adapter_request) as builder, \
             mock.patch("lifecycle_runtime_commit_adapter.adapt_and_execute_lifecycle_runtime_commit") as adapter, \
             mock.patch("runtime_commit_real_executor.execute_runtime_commit") as executor, \
             mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as legacy:
            result = commit_lifecycle(
                h.preview(),
                h.orchestrator(),
                h.writer,
                queue_commit_executor=lambda commit_plan: {"ok": True},
                context={"owner_id": h.owner_id, "token_id": h.token_id},
            )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("RUNTIME_COMMIT_ADAPTER_INPUT_REQUIRED", result["issues"])
        builder.assert_not_called()
        adapter.assert_not_called()
        executor.assert_not_called()
        legacy.assert_not_called()
        self.assertEqual(_read_json(h.target_path), h.before_payload)

    def test_03_missing_token_blocks_after_lock_release_without_write(self) -> None:
        h = self._harness("missing-token")

        observed = self._run_service_with_spies(h)
        result = observed["service_result"]
        executor_result = observed["executor_results"][0]
        builder_result = observed["builder_results"][0]

        self.assertEqual("ABORTED", result["status"])
        self.assertEqual("BLOCKED", executor_result["execute_status"])
        self.assertTrue(executor_result["lock_acquired"])
        self.assertTrue(executor_result["lock_released"])
        self.assertFalse(executor_result["write_executed"])
        self.assertFalse(executor_result["token_consumed"])
        self.assertEqual(_read_json(h.target_path), h.before_payload)
        lock = read_runtime_commit_lock(guard_plan=builder_result["guard_plan"])
        self.assertEqual("LOCK_RELEASED", lock["lock_record"]["lock_status"])

    def test_04_token_commit_id_mismatch_blocks_before_write(self) -> None:
        h = self._harness("commit-mismatch")
        payload = h.build_payload()
        h.issue_token(payload, commit_id="different-commit")

        observed = self._run_service_with_spies(h)
        executor_result = observed["executor_results"][0]

        self.assertEqual("ABORTED", observed["service_result"]["status"])
        self.assertEqual("BLOCKED", executor_result["execute_status"])
        self.assertFalse(executor_result["write_executed"])
        self.assertFalse(executor_result["token_consumed"])
        self.assertEqual(_read_json(h.target_path), h.before_payload)

    def test_05_plan_hash_mismatch_blocks_before_write(self) -> None:
        h = self._harness("plan-mismatch")
        payload = h.build_payload()
        h.issue_token(payload, plan_hash="different-plan-hash")

        observed = self._run_service_with_spies(h)
        executor_result = observed["executor_results"][0]

        self.assertEqual("ABORTED", observed["service_result"]["status"])
        self.assertEqual("BLOCKED", executor_result["execute_status"])
        self.assertFalse(executor_result["write_executed"])
        self.assertFalse(executor_result["token_consumed"])
        self.assertEqual(_read_json(h.target_path), h.before_payload)

    def test_06_transaction_id_mismatch_blocks_before_write(self) -> None:
        h = self._harness("tx-mismatch")
        payload = h.build_payload()
        h.issue_token(payload)
        context = h.context(consumer_id=h.owner_id)
        context["runtime_commit_adapter_request"] = copy.deepcopy(payload)
        context["runtime_commit_adapter_request"]["storage_plan"]["transaction_id"] = "different-tx"

        observed = self._run_service_with_spies(h, context=context)
        executor_result = observed["executor_results"][0]

        self.assertEqual("ABORTED", observed["service_result"]["status"])
        self.assertEqual("BLOCKED", executor_result["execute_status"])
        self.assertFalse(executor_result["write_executed"])
        self.assertEqual(_read_json(h.target_path), h.before_payload)

    def test_07_target_set_hash_mismatch_blocks_before_write(self) -> None:
        h = self._harness("target-hash-mismatch")
        payload = h.build_payload()
        h.issue_token(payload)
        context = h.context()
        context["runtime_commit_adapter_request"] = copy.deepcopy(payload)
        context["runtime_commit_adapter_request"]["transaction_manifest"]["target_set_hash"] = "different-target-set"

        observed = self._run_service_with_spies(h, context=context)
        executor_result = observed["executor_results"][0]

        self.assertEqual("ABORTED", observed["service_result"]["status"])
        self.assertEqual("BLOCKED", executor_result["execute_status"])
        self.assertFalse(executor_result["write_executed"])
        self.assertEqual(_read_json(h.target_path), h.before_payload)

    def test_08_guard_lock_conflict_blocks_write_and_token_consume(self) -> None:
        h = self._harness("lock-conflict")
        payload = h.build_payload()
        h.issue_token(payload)
        acquire = acquire_runtime_commit_lock(guard_plan=payload["guard_plan"])
        self.assertEqual("ACQUIRED", acquire["acquire_status"])

        observed = self._run_service_with_spies(h)
        executor_result = observed["executor_results"][0]

        self.assertEqual("ABORTED", observed["service_result"]["status"])
        self.assertEqual("BLOCKED", executor_result["execute_status"])
        self.assertFalse(executor_result["write_executed"])
        self.assertFalse(executor_result["token_consumed"])
        self.assertEqual(_read_json(h.target_path), h.before_payload)

    def test_09_verify_failure_rolls_back_and_records_recovery(self) -> None:
        h = self._harness("verify-rollback")
        bad_plan = h.commit_plan(planned_targets={str(h.target_path): {"expected": "different"}})
        payload = h.build_payload(plan=bad_plan)
        h.issue_token(payload)
        # Service will use a direct adapter request to keep the token hash aligned
        # while the executor receives mismatching expected/new target payloads.
        direct_payload = copy.deepcopy(payload)
        direct_payload["new_targets"] = {str(h.target_path).replace("\\", "/").lower(): copy.deepcopy(h.after_payload)}
        context = h.context(runtime_commit_adapter_request=direct_payload)

        observed = self._run_service_with_spies(h, context=context, plan=bad_plan)
        executor_result = observed["executor_results"][0]

        self.assertEqual("ABORTED", observed["service_result"]["status"])
        self.assertEqual("ROLLED_BACK", executor_result["execute_status"])
        self.assertTrue(executor_result["rollback_executed"])
        self.assertFalse(executor_result["verify_passed"])
        self.assertFalse(executor_result["token_consumed"])
        self.assertTrue(executor_result["lock_released"])
        self.assertFalse(executor_result["manual_restore_required"])
        self.assertEqual(_read_json(h.target_path), h.before_payload)
        self.assertIn(RECOVERY_STATUS_ROLLED_BACK, h.terminal_recovery_statuses())

    def test_10_adapter_exception_has_no_legacy_fallback_or_double_execution(self) -> None:
        h = self._harness("adapter-exception")
        payload = h.build_payload()
        h.issue_token(payload)

        with mock.patch("lifecycle_runtime_commit_adapter.adapt_and_execute_lifecycle_runtime_commit", side_effect=RuntimeError("adapter failed")) as adapter, \
             mock.patch("runtime_commit_real_executor.execute_runtime_commit") as executor, \
             mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as legacy:
            result = commit_lifecycle(
                h.preview(),
                h.orchestrator(),
                h.writer,
                queue_commit_executor=lambda commit_plan: {"ok": True},
                context=h.context(),
            )

        self.assertEqual("ABORTED", result["status"])
        self.assertIn("RUNTIME_COMMIT_ADAPTER_EXCEPTION: RuntimeError", result["issues"])
        self.assertEqual(1, adapter.call_count)
        executor.assert_not_called()
        legacy.assert_not_called()
        self.assertEqual(_read_json(h.target_path), h.before_payload)

    def test_11_input_dicts_are_not_mutated(self) -> None:
        h = self._harness("immutability")
        payload = h.build_payload()
        h.issue_token(payload)
        preview = h.preview()
        plan = h.commit_plan()
        orchestrator = h.orchestrator(commit_plan=plan)
        context = h.context()
        snapshots = copy.deepcopy((preview, plan, orchestrator, context, payload))

        result = commit_lifecycle(
            preview,
            orchestrator,
            h.writer,
            queue_commit_executor=lambda commit_plan: {"ok": True},
            context=context,
        )

        self.assertEqual("COMMITTED", result["status"])
        self.assertEqual(snapshots, (preview, plan, orchestrator, context, payload))


if __name__ == "__main__":
    unittest.main()
