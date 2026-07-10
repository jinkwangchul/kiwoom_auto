# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import hashlib
import sqlite3
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from execution_runtime_commit_plan_orchestrator import ORCHESTRATOR_TYPE
from lifecycle_commit_service import commit_lifecycle
from lifecycle_commit_writer import LifecycleCommitWriter
from lifecycle_runtime_commit_builder import (
    build_lifecycle_runtime_commit_adapter_request,
    build_gate_result,
    build_transaction_manifest,
    build_storage_plan,
    build_guard_plan,
    build_token_storage_plan,
    build_expected_targets,
    build_new_targets,
    build_consumer_id,
    validate_lifecycle_request,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _protected_paths() -> list[Path]:
    paths = [
        ROOT / "runtime" / "order_queue.json",
        ROOT / "runtime" / "order_executions.json",
        ROOT / "runtime" / "order_locks.json",
    ]
    paths.extend(sorted((ROOT / "routines").glob("*/rules.json")))
    return paths


def _rows(db_path: Path, table: str) -> list[dict[str, object]]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()]
    finally:
        conn.close()


class LifecycleCommitWriterServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protected_hashes = {path: _sha256(path) for path in _protected_paths()}
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "lifecycle.sqlite3"
        self.writer = LifecycleCommitWriter(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
        self.assertEqual(self.protected_hashes, {path: _sha256(path) for path in _protected_paths()})

    def _contract(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "contract_type": "ORDER_LIFECYCLE_COMMIT_CONTRACT_PREVIEW",
            "contract_version": "preview-1",
            "preview_only": True,
            "lifecycle_write": False,
            "runtime_write": False,
            "queue_write": False,
            "candidate_lifecycle_event": "ORDER_RECEIVED",
            "evidence_id": "EVIDENCE_WRITER_1",
            "record_id": "RECORD_WRITER_1",
            "order_id": "ORDER_WRITER_1",
            "dispatch_id": "DISPATCH_WRITER_1",
            "source_signal_id": "SIGNAL_WRITER_1",
            "order_queued_id": "ORDER_QUEUED_WRITER_1",
            "target_name": "temp_lifecycle",
            "lifecycle_store": "temp_store",
            "required_next_service": "ORDER_LIFECYCLE_COMMIT_SERVICE",
        }
        result.update(overrides)
        return result

    def _plan(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "plan_type": "ORDER_LIFECYCLE_COMMIT_PLAN_PREVIEW",
            "preview_only": True,
            "lifecycle_write": False,
            "runtime_write": False,
            "queue_write": False,
            "would_append_event": "ORDER_RECEIVED",
        }
        result.update(overrides)
        return result

    def _preview(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "status": "LIFECYCLE_COMMIT_READY",
            "commit_contract": self._contract(),
            "commit_plan": self._plan(),
            "issues": [],
            "warnings": [],
            "preview_only": True,
            "lifecycle_write": False,
            "runtime_write": False,
            "queue_write": False,
        }
        result.update(overrides)
        return result

    def _orchestrator_result(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "orchestrator_type": ORCHESTRATOR_TYPE,
            "status": "READY",
            "commit_ready": True,
            "commit_plan": {
                "planned_records": [{"record_id": "planned-1"}],
                "planned_targets": {},
            },
            "issues": [],
            "warnings": [],
        }
        result.update(overrides)
        return result

    def _runtime_adapter_payload(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "lifecycle_commit_request": {
                "lifecycle_id": "life-writer-1",
                "commit_id": "commit-writer-1",
                "transaction_id": "tx-writer-1",
                "requested_action": "RUNTIME_COMMIT",
                "source_stage": "LIFECYCLE_COMMIT_SERVICE",
                "runtime_commit_boundary_status": "RUNTIME_COMMIT_BOUNDARY_READY",
                "preview_only": True,
                "metadata": {"source": "test"},
            },
            "gate_result": {"gate_status": "APPROVED"},
            "transaction_manifest": {
                "commit_id": "commit-writer-1",
                "transaction_id": "tx-writer-1",
                "execution_plan_hash": "plan-hash-writer-1",
            },
            "storage_plan": {"storage_status": "READY", "storage_root": str(Path(self.temp_dir.name) / "storage")},
            "guard_plan": {"guard_status": "READY", "owner_id": "consumer-writer-1"},
            "token_storage_plan": {"storage_status": "READY", "token_id": "token-writer-1"},
            "expected_targets": {"target.json": {"old": "value"}},
            "new_targets": {"target.json": {"new": "value"}},
            "consumer_id": "consumer-writer-1",
        }
        result.update(overrides)
        return result

    def _orchestrator_with_adapter(self, payload: dict[str, object] | None = None) -> dict[str, object]:
        return self._orchestrator_result(
            commit_plan={
                "planned_records": [{"record_id": "planned-1"}],
                "planned_targets": {},
                "runtime_commit_adapter": self._runtime_adapter_payload() if payload is None else payload,
            }
        )

    def _adapter_result(self, status: str, issues: list[str] | None = None) -> dict[str, object]:
        return {
            "adapter_status": status,
            "executor_called": True,
            "legacy_executor_called": False,
            "issues": list(issues or []),
            "runtime_commit_result": {"execute_status": status},
        }

    def test_writer_prepare_commit_creates_prepared_transition_and_journal(self) -> None:
        result = self.writer.prepare_commit(self._contract(), self._plan())

        self.assertTrue(result["ok"])
        self.assertTrue(result["prepared"])
        self.assertTrue(result["commit_token"])
        transitions = _rows(self.db_path, "transitions")
        journal = _rows(self.db_path, "journal")
        self.assertEqual(1, len(transitions))
        self.assertEqual("prepared", transitions[0]["status"])
        self.assertEqual("ORDER_WRITER_1", transitions[0]["order_id"])
        self.assertEqual("prepared", journal[0]["action"])

    def test_writer_finalize_commit_marks_committed(self) -> None:
        prepared = self.writer.prepare_commit(self._contract(), self._plan())

        result = self.writer.finalize_commit(prepared["commit_token"], success=True, metadata={"source": "test"})

        self.assertTrue(result["ok"])
        self.assertEqual("committed", result["status"])
        transitions = _rows(self.db_path, "transitions")
        journal_actions = [row["action"] for row in _rows(self.db_path, "journal")]
        self.assertEqual("committed", transitions[0]["status"])
        self.assertIn("committed", journal_actions)

    def test_writer_finalize_commit_marks_aborted(self) -> None:
        prepared = self.writer.prepare_commit(self._contract(), self._plan())

        result = self.writer.finalize_commit(prepared["commit_token"], success=False, metadata={"reason": "external_failed"})

        self.assertTrue(result["ok"])
        self.assertEqual("aborted", result["status"])
        transitions = _rows(self.db_path, "transitions")
        journal_actions = [row["action"] for row in _rows(self.db_path, "journal")]
        self.assertEqual("aborted", transitions[0]["status"])
        self.assertIn("aborted", journal_actions)

    def test_evidence_id_duplicate_is_blocked_after_commit(self) -> None:
        prepared = self.writer.prepare_commit(self._contract(), self._plan())
        self.writer.finalize_commit(prepared["commit_token"], success=True)

        duplicate = self.writer.prepare_commit(
            self._contract(order_id="ORDER_WRITER_2", candidate_lifecycle_event="PARTIAL_FILL"),
            self._plan(),
        )

        self.assertFalse(duplicate["ok"])
        self.assertIn("duplicate lifecycle transition exists", duplicate["issues"])

    def test_order_id_and_event_duplicate_is_blocked_for_prepared_transition(self) -> None:
        first = self.writer.prepare_commit(self._contract(evidence_id="EVIDENCE_A"), self._plan())

        second = self.writer.prepare_commit(self._contract(evidence_id="EVIDENCE_B"), self._plan())

        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertIn("duplicate lifecycle transition exists", second["issues"])

    def test_writer_prepare_commit_creates_journal_record(self) -> None:
        prepared = self.writer.prepare_commit(self._contract(), self._plan())

        self.assertTrue(prepared["ok"])
        journal = _rows(self.db_path, "journal")
        self.assertEqual(1, len(journal))
        self.assertEqual(prepared["commit_token"], journal[0]["commit_token"])
        self.assertEqual("prepared", journal[0]["action"])

    def test_service_commits_when_external_executors_succeed(self) -> None:
        runtime_calls: list[dict[str, object]] = []
        queue_calls: list[dict[str, object]] = []

        def runtime_executor(plan: dict[str, object]) -> dict[str, object]:
            runtime_calls.append(plan)
            return {"ok": True}

        def queue_executor(plan: dict[str, object]) -> dict[str, object]:
            queue_calls.append(plan)
            return {"ok": True}

        result = commit_lifecycle(
            self._preview(),
            self._orchestrator_result(),
            self.writer,
            runtime_commit_executor=runtime_executor,
            queue_commit_executor=queue_executor,
        )

        self.assertEqual("COMMITTED", result["status"])
        self.assertTrue(result["commit_token"])
        self.assertEqual(1, len(runtime_calls))
        self.assertEqual(1, len(queue_calls))
        self.assertEqual("committed", _rows(self.db_path, "transitions")[0]["status"])

    def test_service_default_runtime_executor_calls_adapter_once_and_not_legacy(self) -> None:
        with mock.patch(
            "lifecycle_runtime_commit_adapter.adapt_and_execute_lifecycle_runtime_commit",
            return_value=self._adapter_result("COMMITTED"),
        ) as adapter, mock.patch(
            "execution_runtime_commit_service.commit_execution_runtime_plan"
        ) as legacy:
            result = commit_lifecycle(
                self._preview(),
                self._orchestrator_with_adapter(),
                self.writer,
                queue_commit_executor=lambda plan: {"ok": True},
            )

        self.assertEqual("COMMITTED", result["status"])
        self.assertEqual(1, adapter.call_count)
        legacy.assert_not_called()
        self.assertEqual("committed", _rows(self.db_path, "transitions")[0]["status"])

    def test_service_explicit_runtime_executor_does_not_call_default_adapter(self) -> None:
        runtime_calls: list[dict[str, object]] = []

        def explicit_runtime(plan: dict[str, object]) -> dict[str, object]:
            runtime_calls.append(plan)
            return {"ok": True}

        with mock.patch("lifecycle_runtime_commit_adapter.adapt_and_execute_lifecycle_runtime_commit") as adapter:
            result = commit_lifecycle(
                self._preview(),
                self._orchestrator_with_adapter(),
                self.writer,
                runtime_commit_executor=explicit_runtime,
                queue_commit_executor=lambda plan: {"ok": True},
            )

        self.assertEqual("COMMITTED", result["status"])
        self.assertEqual(1, len(runtime_calls))
        adapter.assert_not_called()

    def test_service_propagates_non_committed_adapter_statuses_as_abort(self) -> None:
        for adapter_status in ("BLOCKED", "INVALID", "ABORTED", "ROLLED_BACK", "REVIEW_REQUIRED"):
            with self.subTest(adapter_status=adapter_status):
                self.tearDown()
                self.setUp()
                with mock.patch(
                    "lifecycle_runtime_commit_adapter.adapt_and_execute_lifecycle_runtime_commit",
                    return_value=self._adapter_result(adapter_status),
                ) as adapter, mock.patch(
                    "execution_runtime_commit_service.commit_execution_runtime_plan"
                ) as legacy:
                    result = commit_lifecycle(
                        self._preview(),
                        self._orchestrator_with_adapter(),
                        self.writer,
                        queue_commit_executor=lambda plan: {"ok": True},
                    )

                self.assertEqual("ABORTED", result["status"])
                self.assertIn(adapter_status, result["issues"])
                self.assertEqual(1, adapter.call_count)
                legacy.assert_not_called()

    def test_service_adapter_exception_does_not_fallback_to_legacy(self) -> None:
        with mock.patch(
            "lifecycle_runtime_commit_adapter.adapt_and_execute_lifecycle_runtime_commit",
            side_effect=RuntimeError("adapter failed"),
        ) as adapter, mock.patch(
            "execution_runtime_commit_service.commit_execution_runtime_plan"
        ) as legacy:
            result = commit_lifecycle(
                self._preview(),
                self._orchestrator_with_adapter(),
                self.writer,
                queue_commit_executor=lambda plan: {"ok": True},
            )

        self.assertEqual("ABORTED", result["status"])
        self.assertIn("RUNTIME_COMMIT_ADAPTER_EXCEPTION: RuntimeError", result["issues"])
        self.assertEqual(1, adapter.call_count)
        legacy.assert_not_called()

    def test_service_blocks_missing_adapter_payload_before_prepare(self) -> None:
        with mock.patch("lifecycle_runtime_commit_adapter.adapt_and_execute_lifecycle_runtime_commit") as adapter:
            result = commit_lifecycle(
                self._preview(),
                self._orchestrator_result(),
                self.writer,
                queue_commit_executor=lambda plan: {"ok": True},
            )

        self.assertEqual("BLOCKED", result["status"])
        self.assertIn("RUNTIME_COMMIT_ADAPTER_INPUT_REQUIRED", result["issues"])
        adapter.assert_not_called()
        self.assertEqual([], _rows(self.db_path, "transitions"))

    def test_service_does_not_mutate_adapter_payload_or_context(self) -> None:
        payload = self._runtime_adapter_payload()
        context = {"runtime_commit_adapter_request": self._runtime_adapter_payload(commit_id="context-commit")}
        orchestrator = self._orchestrator_with_adapter(payload)
        originals = copy.deepcopy((payload, context, orchestrator))

        def mutating_adapter(**kwargs: object) -> dict[str, object]:
            kwargs["gate_result"]["gate_status"] = "MUTATED"  # type: ignore[index]
            kwargs["transaction_manifest"]["commit_id"] = "mutated"  # type: ignore[index]
            kwargs["storage_plan"]["storage_root"] = "mutated"  # type: ignore[index]
            kwargs["guard_plan"]["guard_status"] = "MUTATED"  # type: ignore[index]
            kwargs["token_storage_plan"]["token_id"] = "mutated"  # type: ignore[index]
            kwargs["expected_targets"]["target.json"]["old"] = "mutated"  # type: ignore[index]
            kwargs["new_targets"]["target.json"]["new"] = "mutated"  # type: ignore[index]
            return self._adapter_result("COMMITTED")

        with mock.patch(
            "lifecycle_runtime_commit_adapter.adapt_and_execute_lifecycle_runtime_commit",
            side_effect=mutating_adapter,
        ):
            result = commit_lifecycle(
                self._preview(),
                orchestrator,
                self.writer,
                queue_commit_executor=lambda plan: {"ok": True},
                context=context,
            )

        self.assertEqual("COMMITTED", result["status"])
        self.assertEqual(originals, (payload, context, orchestrator))

    def test_service_aborts_when_external_executor_fails_and_keeps_prepare_and_journal(self) -> None:
        def runtime_executor(plan: dict[str, object]) -> dict[str, object]:
            return {"ok": False, "issues": ["runtime failed"]}

        result = commit_lifecycle(
            self._preview(),
            self._orchestrator_result(),
            self.writer,
            runtime_commit_executor=runtime_executor,
            queue_commit_executor=lambda plan: {"ok": True},
        )

        self.assertEqual("ABORTED", result["status"])
        self.assertIn("runtime failed", result["issues"])
        transitions = _rows(self.db_path, "transitions")
        journal_actions = [row["action"] for row in _rows(self.db_path, "journal")]
        self.assertEqual(1, len(transitions))
        self.assertEqual("aborted", transitions[0]["status"])
        self.assertIn("prepared", journal_actions)
        self.assertIn("aborted", journal_actions)

    def test_service_executor_failure_leaves_auditable_transition_record(self) -> None:
        result = commit_lifecycle(
            self._preview(),
            self._orchestrator_result(),
            self.writer,
            runtime_commit_executor=lambda plan: {"ok": True},
            queue_commit_executor=lambda plan: {"ok": False, "issues": ["queue failed"]},
        )

        self.assertEqual("ABORTED", result["status"])
        transitions = _rows(self.db_path, "transitions")
        journal = _rows(self.db_path, "journal")
        self.assertEqual(1, len(transitions))
        self.assertEqual("aborted", transitions[0]["status"])
        self.assertEqual("ORDER_WRITER_1", transitions[0]["order_id"])
        self.assertEqual(["prepared", "aborted"], [row["action"] for row in journal])

    def test_read_store_snapshot_returns_transitions(self) -> None:
        prepared = self.writer.prepare_commit(self._contract(), self._plan())
        self.writer.finalize_commit(prepared["commit_token"], success=True)

        snapshot = self.writer.read_store_snapshot("temp_store")

        self.assertTrue(snapshot["snapshot_valid"])
        self.assertEqual("temp_store", snapshot["lifecycle_store"])
        self.assertEqual(1, len(snapshot["existing_transitions"]))
        self.assertEqual("ORDER_WRITER_1", snapshot["existing_transitions"][0]["order_id"])
        self.assertEqual("committed", snapshot["existing_transitions"][0]["status"])

    def test_prepare_commit_rolls_back_transaction_on_failure(self) -> None:
        def boom(conn: sqlite3.Connection, order_id: str, event: str, evidence_id: str) -> None:
            raise RuntimeError("forced duplicate check failure")

        self.writer._existing_duplicate = boom  # type: ignore[method-assign]

        result = self.writer.prepare_commit(self._contract(), self._plan())

        self.assertFalse(result["ok"])
        self.assertIn("prepare failed: forced duplicate check failure", result["issues"])
        self.assertEqual([], _rows(self.db_path, "transitions"))
        self.assertEqual([], _rows(self.db_path, "journal"))

    def test_service_rejects_invalid_commit_plan_orchestrator(self) -> None:
        result = commit_lifecycle(
            self._preview(),
            self._orchestrator_result(orchestrator_type="WRONG_TYPE"),
            self.writer,
            runtime_commit_executor=lambda plan: {"ok": True},
            queue_commit_executor=lambda plan: {"ok": True},
        )

        self.assertEqual("INVALID", result["status"])
        self.assertIn("INVALID_COMMIT_PLAN_ORCHESTRATOR_TYPE", result["issues"])
        self.assertEqual([], _rows(self.db_path, "transitions"))
        self.assertEqual([], _rows(self.db_path, "journal"))


class LifecycleRuntimeCommitBuilderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_root = self.temp_dir.name
        self.db_path = Path(self.temp_dir.name) / "test.sqlite3"
        from lifecycle_commit_writer import LifecycleCommitWriter
        self.writer = LifecycleCommitWriter(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _lifecycle_request(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "lifecycle_id": "life-builder-1",
            "commit_id": "commit-builder-1",
            "transaction_id": "tx-builder-1",
            "requested_action": "RUNTIME_COMMIT",
            "source_stage": "LIFECYCLE_COMMIT_SERVICE",
            "runtime_commit_boundary_status": "RUNTIME_COMMIT_BOUNDARY_READY",
            "preview_only": True,
            "metadata": {"source": "test"},
        }
        result.update(overrides)
        return result

    def _commit_contract_preview(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "contract_type": "ORDER_LIFECYCLE_COMMIT_CONTRACT_PREVIEW",
            "commit_id": "commit-builder-1",
            "lifecycle_commit_request": {
                "lifecycle_id": "life-builder-1",
                "commit_id": "commit-builder-1",
                "transaction_id": "tx-builder-1",
                "requested_action": "RUNTIME_COMMIT",
                "source_stage": "LIFECYCLE_COMMIT_SERVICE",
                "runtime_commit_boundary_status": "RUNTIME_COMMIT_BOUNDARY_READY",
                "preview_only": True,
                "metadata": {"source": "test"},
            },
            "commit_contract": {
                "contract_type": "ORDER_LIFECYCLE_COMMIT_CONTRACT_PREVIEW",
                "contract_version": "preview-1",
                "preview_only": True,
                "lifecycle_write": False,
                "runtime_write": False,
                "queue_write": False,
                "candidate_lifecycle_event": "ORDER_RECEIVED",
                "evidence_id": "EVIDENCE_BUILDER_1",
                "record_id": "RECORD_BUILDER_1",
                "order_id": "ORDER_BUILDER_1",
                "dispatch_id": "DISPATCH_BUILDER_1",
                "source_signal_id": "SIGNAL_BUILDER_1",
                "order_queued_id": "ORDER_QUEUED_BUILDER_1",
                "target_name": "temp_lifecycle",
                "lifecycle_store": "temp_store",
                "required_next_service": "ORDER_LIFECYCLE_COMMIT_SERVICE",
            },
            "commit_plan": {
                "planned_records": [{"record_id": "planned-1"}],
                "planned_targets": {"target.json": {"old": "value"}},
            },
            "issues": [],
            "warnings": [],
            "preview_only": True,
        }
        result.update(overrides)
        return result

    def test_build_gate_result(self) -> None:
        result = build_gate_result(commit_contract_preview=self._commit_contract_preview())

        self.assertEqual("APPROVED", result["gate_status"])
        self.assertEqual("commit-builder-1", result["commit_id"])
        self.assertEqual([], result["issues"])

    def test_build_transaction_manifest(self) -> None:
        result = build_transaction_manifest(
            commit_id="commit-builder-1",
            transaction_id="tx-builder-1",
            execution_plan_hash="plan-hash-1",
            approval_token_id="token-1",
            expected_payload_hash="payload-hash-1",
            target_paths=["target.json"],
        )

        self.assertEqual("M6_RUNTIME_TRANSACTION_V1", result["contract_version"])
        self.assertEqual("commit-builder-1", result["commit_id"])
        self.assertEqual("tx-builder-1", result["transaction_id"])
        self.assertEqual("CREATED", result["transaction_status"])
        self.assertEqual(["MANIFEST_CREATED"], result["stage_history"])

    def test_build_storage_plan(self) -> None:
        result = build_storage_plan(
            storage_root=self.storage_root,
            commit_id="commit-builder-1",
            transaction_id="tx-builder-1",
        )

        self.assertEqual("READY", result["storage_status"])
        self.assertEqual(self.storage_root, result["storage_root"])
        self.assertIn("transactions", result["transaction_dir"])
        self.assertIn("manifest.json", result["manifest_path"])
        self.assertIn("journal.jsonl", result["journal_path"])

    def test_build_guard_plan(self) -> None:
        result = build_guard_plan(
            storage_root=self.storage_root,
            commit_id="commit-builder-1",
            transaction_id="tx-builder-1",
            target_set_hash="hash-123",
            owner_id="owner-1",
        )

        self.assertEqual("READY", result["guard_status"])
        self.assertEqual("commit-builder-1", result["commit_id"])
        self.assertEqual("tx-builder-1", result["transaction_id"])
        self.assertEqual("hash-123", result["target_set_hash"])
        self.assertEqual("owner-1", result["owner_id"])
        self.assertTrue(result["lock_path"].endswith(".json"))

    def test_build_token_storage_plan(self) -> None:
        result = build_token_storage_plan(
            storage_root=self.storage_root,
            token_id="token-1",
            commit_id="commit-builder-1",
        )

        self.assertEqual("READY", result["plan_status"])
        self.assertEqual(self.storage_root, result["storage_root"])
        self.assertEqual("token-1", result["token_id"])
        self.assertEqual("commit-builder-1", result["commit_id"])
        self.assertTrue(result["token_path"].endswith("token-1.json"))
        self.assertTrue(result["claim_path"].endswith("token-1.consume.lock"))

    def test_build_expected_targets(self) -> None:
        result = build_expected_targets(planned_targets={"target.json": {"old": "value"}})

        self.assertEqual({"target.json": {"old": "value"}}, result)

    def test_build_expected_targets_normalizes_paths(self) -> None:
        result = build_expected_targets(planned_targets={"Target.JSON": {"old": "value"}})

        self.assertIn("target.json", result)

    def test_build_new_targets(self) -> None:
        result = build_new_targets(
            planned_targets={"target.json": {"new": "value"}},
        )

        self.assertEqual({"target.json": {"new": "value"}}, result)

    def test_build_consumer_id(self) -> None:
        self.assertEqual("owner-1", build_consumer_id(owner_id="owner-1"))
        self.assertEqual("override-1", build_consumer_id(owner_id="owner-1", consumer_override="override-1"))
        self.assertEqual("lifecycle_consumer", build_consumer_id())

    def test_validate_lifecycle_request_valid(self) -> None:
        request = self._lifecycle_request()
        result, issues = validate_lifecycle_request(request)

        self.assertEqual([], issues)

    def test_validate_lifecycle_request_missing_field(self) -> None:
        request = self._lifecycle_request()
        del request["transaction_id"]
        result, issues = validate_lifecycle_request(request)

        self.assertTrue(any("transaction_id" in issue for issue in issues))

    def test_build_adapter_request_complete(self) -> None:
        preview = self._commit_contract_preview()
        plan = preview["commit_plan"]

        result = build_lifecycle_runtime_commit_adapter_request(
            commit_contract_preview=preview,
            commit_plan=plan,
            storage_root=self.storage_root,
            owner_id="owner-1",
            token_id="token-1",
        )

        self.assertIn("lifecycle_commit_request", result)
        self.assertIn("gate_result", result)
        self.assertIn("transaction_manifest", result)
        self.assertIn("storage_plan", result)
        self.assertIn("guard_plan", result)
        self.assertIn("token_storage_plan", result)
        self.assertIn("expected_targets", result)
        self.assertIn("new_targets", result)
        self.assertIn("consumer_id", result)
        self.assertEqual("owner-1", result["consumer_id"])

    def test_build_adapter_request_generates_from_lifecycle_request(self) -> None:
        preview = {
            "commit_id": "commit-builder-1",
            "lifecycle_commit_request": self._lifecycle_request(),
        }
        plan = {
            "planned_records": [{"record_id": "planned-1"}],
            "planned_targets": {"target.json": {"new": "value"}},
        }

        result = build_lifecycle_runtime_commit_adapter_request(
            commit_contract_preview=preview,
            commit_plan=plan,
            storage_root=self.storage_root,
            owner_id="owner-1",
        )

        self.assertEqual("commit-builder-1", result["transaction_manifest"]["commit_id"])
        self.assertEqual("tx-builder-1", result["transaction_manifest"]["transaction_id"])

    def test_build_adapter_request_missing_storage_root(self) -> None:
        preview = {"commit_id": "commit-1", "lifecycle_commit_request": self._lifecycle_request()}
        plan = {"planned_records": [], "planned_targets": {}}

        result = build_lifecycle_runtime_commit_adapter_request(
            commit_contract_preview=preview,
            commit_plan=plan,
            storage_root="",
            owner_id="owner-1",
        )

        self.assertTrue(len(result.get("build_issues", [])) > 0)

    def test_service_uses_builder_when_no_payload_provided(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            storage_root = temp_dir
            preview = self._commit_contract_preview()
            plan_orch = {
                "orchestrator_type": ORCHESTRATOR_TYPE,
                "status": "READY",
                "commit_ready": True,
                "commit_plan": {
                    "planned_records": [{"record_id": "planned-1"}],
                    "planned_targets": {},
                },
                "issues": [],
                "warnings": [],
            }

            with mock.patch("lifecycle_runtime_commit_adapter.adapt_and_execute_lifecycle_runtime_commit") as adapter:
                adapter.return_value = {
                    "adapter_status": "COMMITTED",
                    "executor_called": True,
                    "issues": [],
                }

                result = commit_lifecycle(
                    preview,
                    plan_orch,
                    self.writer,
                    queue_commit_executor=lambda plan: {"ok": True},
                    context={"storage_root": storage_root, "owner_id": "owner-1"},
                )

                self.assertEqual("COMMITTED", result["status"])
                self.assertEqual(1, adapter.call_count)

    def test_builder_does_not_mutate_inputs(self) -> None:
        preview = self._commit_contract_preview()
        plan = {
            "planned_records": [{"record_id": "planned-1"}],
            "planned_targets": {"target.json": {"key": "value"}},
        }
        original_plan = copy.deepcopy(plan)
        original_preview = copy.deepcopy(preview)

        result = build_lifecycle_runtime_commit_adapter_request(
            commit_contract_preview=preview,
            commit_plan=plan,
            storage_root=self.storage_root,
            owner_id="owner-1",
        )

        self.assertEqual(original_plan, plan)
        self.assertEqual(original_preview, preview)

    def test_adapter_receives_correct_inputs_from_builder(self) -> None:
        storage_root = self.storage_root
        owner_id = "owner-1"
        token_id = "token-1"

        preview = self._commit_contract_preview()
        plan = {
            "planned_records": [{"record_id": "planned-1"}],
            "planned_targets": {"target.json": {"key": "value"}},
        }

        adapter_calls: list[dict[str, object]] = []

        def capturing_executor(**kwargs: object) -> dict[str, object]:
            adapter_calls.append(copy.deepcopy(kwargs))
            return {
                "adapter_status": "COMMITTED",
                "executor_called": True,
                "issues": [],
            }

        with mock.patch(
            "lifecycle_runtime_commit_adapter.adapt_and_execute_lifecycle_runtime_commit",
            side_effect=capturing_executor,
        ):
            result = commit_lifecycle(
                preview,
                {
                    "orchestrator_type": ORCHESTRATOR_TYPE,
                    "status": "READY",
                    "commit_ready": True,
                    "commit_plan": plan,
                    "issues": [],
                    "warnings": [],
                },
                self.writer,
                queue_commit_executor=lambda plan: {"ok": True},
                context={"storage_root": storage_root, "owner_id": owner_id, "token_id": token_id},
            )

        self.assertEqual("COMMITTED", result["status"])
        self.assertEqual(1, len(adapter_calls))
        call = adapter_calls[0]
        self.assertIn("lifecycle_commit_request", call)
        self.assertIn("gate_result", call)
        self.assertIn("transaction_manifest", call)
        self.assertIn("storage_plan", call)
        self.assertIn("guard_plan", call)
        self.assertIn("token_storage_plan", call)
        self.assertIn("expected_targets", call)
        self.assertIn("new_targets", call)
        self.assertIn("consumer_id", call)

    def test_legacy_executor_not_called_when_builder_used(self) -> None:
        storage_root = self.storage_root

        with mock.patch("lifecycle_runtime_commit_adapter.adapt_and_execute_lifecycle_runtime_commit") as adapter, \
             mock.patch("execution_runtime_commit_service.commit_execution_runtime_plan") as legacy:
            adapter.return_value = {
                "adapter_status": "COMMITTED",
                "executor_called": True,
                "issues": [],
            }

            result = commit_lifecycle(
                self._commit_contract_preview(),
                {
                    "orchestrator_type": ORCHESTRATOR_TYPE,
                    "status": "READY",
                    "commit_ready": True,
                    "commit_plan": {
                        "planned_records": [{"record_id": "planned-1"}],
                        "planned_targets": {},
                    },
                    "issues": [],
                    "warnings": [],
                },
                self.writer,
                queue_commit_executor=lambda plan: {"ok": True},
                context={"storage_root": storage_root, "owner_id": "owner-1"},
            )

        self.assertEqual("COMMITTED", result["status"])
        adapter.assert_called_once()
        legacy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
