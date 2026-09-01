from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from assignment_episode_linkage import (
    ASSIGNMENT_TRANSACTION_ABORTED,
    ASSIGNMENT_TRANSACTION_COMMITTED,
    ASSIGNMENT_TRANSACTION_CONFIG_APPLIED,
    ASSIGNMENT_TRANSACTION_EPISODE_APPLIED,
    ASSIGNMENT_TRANSACTION_FIELD_CONFLICT,
    ASSIGNMENT_TRANSACTION_PREPARED,
    ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED,
    ASSIGNMENT_TRANSACTION_ROLLED_BACK,
    AssignmentTransactionJournalRepository,
    assignment_target_from_config,
    assignment_transaction_lock,
    conditional_compensate_assignment_config,
    execute_assignment_transaction_foundation,
    reconcile_incomplete_assignment_transactions,
)
from assignment_episode_repository import (
    AssignmentEpisodeTarget,
    CanonicalAssignmentEpisodeRepository,
)
from stock_repository import (
    STOCK_CONFIG_WRITE_ATOMIC_WRITE_FAILED,
    STOCK_CONFIG_WRITE_FIELD_CONFLICT,
    StockConfigWriteResult,
    StockRepository,
)


CODE_A = "005930"
CODE_B = "000660"
CHANGED_AT = "2026-08-29T09:00:00+09:00"


class AssignmentTransactionFoundationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.group_a = str(uuid4())
        self.group_b = str(uuid4())
        self.group_c = str(uuid4())
        self.instance_a = str(uuid4())
        self.instance_b = str(uuid4())
        self.instance_c = str(uuid4())
        self._write_foundation()
        self.stock_repository = StockRepository(self.root)
        self.episode_repository = CanonicalAssignmentEpisodeRepository(self.root)
        self.journal_repository = AssignmentTransactionJournalRepository(
            self.root,
            episodes_repository=self.episode_repository,
        )

    def _json(self, path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _write_foundation(self) -> None:
        routine = self.root / "routines" / "Sample"
        self._json(
            routine / "routine.json",
            {
                "schema_version": "1.0",
                "definition_id": "sample",
                "name": "Sample",
                "entry_file": "routine.py",
                "rules_file": "rules.json",
                "enabled": True,
            },
        )
        (routine / "routine.py").write_text("", encoding="utf-8")
        groups = (
            (self.group_a, "Group", 0),
            (self.group_b, "Group_1", 1),
            (self.group_c, "Group_2", 2),
        )
        for group_id, display_name, slot in groups:
            self._json(
                self.root / "groups" / group_id / "group.json",
                {
                    "schema_version": "1.0",
                    "group_id": group_id,
                    "definition_id": "sample",
                    "base_name": "Group",
                    "display_name": display_name,
                    "slot": slot,
                    "created_at": CHANGED_AT,
                },
            )
        self._json(
            self.root / "groups" / "registry.json",
            {
                "schema_version": "1.0",
                "mode": "logical",
                "group_ids": [self.group_a, self.group_b, self.group_c],
                "cutover_at": CHANGED_AT,
            },
        )
        instances = (
            (self.instance_a, self.group_a, "Alpha"),
            (self.instance_b, self.group_b, "Beta"),
            (self.instance_c, self.group_c, "Gamma"),
        )
        for instance_id, group_id, display_name in instances:
            self._json(
                self.root / "routine_instances" / instance_id / "instance.json",
                {
                    "schema_version": "1.0",
                    "instance_id": instance_id,
                    "definition_id": "sample",
                    "display_name": display_name,
                    "enabled": False,
                    "buy_limit_enabled": False,
                    "buy_limit_amount": None,
                    "rules_file": "rules.json",
                    "created_at": CHANGED_AT,
                    "updated_at": CHANGED_AT,
                    "group_id": group_id,
                },
            )
            self._json(self.root / "routine_instances" / instance_id / "rules.json", {})
        for code, name in ((CODE_A, "Sample"), (CODE_B, "Other")):
            stock = self.root / "stocks" / f"{code}_{name}"
            self._json(
                stock / "config.json",
                {
                    "routines": [],
                    "assigned_routine_instance_id": "",
                    "buy_amount": 100_000,
                    "buy_limit_amount": 200_000,
                    "operation_mode": "CONTINUOUS",
                    "policy_overrides": {"entry": True},
                },
            )
            self._json(stock / "state.json", {"status": "STOPPED"})
            self._json(stock / "orders.json", {"orders": []})

    def _config_path(self, code: str = CODE_A) -> Path:
        return self.stock_repository.resolve_stock_dir(code) / "config.json"

    def _config(self, code: str = CODE_A) -> dict[str, object]:
        return json.loads(self._config_path(code).read_text(encoding="utf-8"))

    def _target(self, instance_id: str) -> AssignmentEpisodeTarget:
        return assignment_target_from_config(
            self.root,
            {"assigned_routine_instance_id": instance_id},
        )

    @staticmethod
    def _identity(target: AssignmentEpisodeTarget) -> dict[str, str]:
        return {
            "ownership_kind": target.ownership_kind,
            "instance_id": target.instance_id or "",
            "group_id": target.group_id or "",
            "definition_id": target.definition_id or "",
        }

    def _execute(self, instance_id: str, **overrides):
        arguments = {
            "stock_name": "Sample",
            "target_instance_id": instance_id,
            "target_routine_type": "Sample" if instance_id else "",
            "changed_at": CHANGED_AT,
            "reason": "TEST_ASSIGNMENT",
            "source": "TEST",
            "stock_repository": self.stock_repository,
            "episode_repository": self.episode_repository,
            "journal_repository": self.journal_repository,
        }
        arguments.update(overrides)
        return execute_assignment_transaction_foundation(self.root, CODE_A, **arguments)

    def _assign_a_and_open_episode(self) -> None:
        result = self._execute(self.instance_a)
        self.assertTrue(result.success, result)

    def _create_prepared_journal(
        self,
        *,
        before: AssignmentEpisodeTarget,
        target: AssignmentEpisodeTarget,
        before_episode: AssignmentEpisodeTarget | None,
    ) -> str:
        transaction_id = str(uuid4())
        self.journal_repository.create_prepared(
            transaction_id=transaction_id,
            stock_code=CODE_A,
            operation="REASSIGN",
            before_assignment_identity=self._identity(before),
            target_assignment_identity=self._identity(target),
            before_config_fingerprint="CONFIG-BEFORE",
            before_episode_identity=(
                self._identity(before_episode) if before_episode is not None else None
            ),
            before_episode_fingerprint="EPISODE-BEFORE",
            reason="TEST_CRASH",
            source="TEST",
        )
        return transaction_id

    def test_same_stock_transactions_are_serialized(self) -> None:
        first_acquired = threading.Event()
        release_first = threading.Event()
        second_acquired = threading.Event()

        def first() -> None:
            with assignment_transaction_lock(CODE_A):
                first_acquired.set()
                release_first.wait(2)

        def second() -> None:
            first_acquired.wait(2)
            with assignment_transaction_lock(CODE_A):
                second_acquired.set()

        thread_a = threading.Thread(target=first)
        thread_b = threading.Thread(target=second)
        thread_a.start()
        thread_b.start()
        self.assertTrue(first_acquired.wait(1))
        time.sleep(0.05)
        self.assertFalse(second_acquired.is_set())
        release_first.set()
        thread_a.join(2)
        thread_b.join(2)
        self.assertTrue(second_acquired.is_set())

    def test_different_stock_transactions_are_independent(self) -> None:
        first_acquired = threading.Event()
        release_first = threading.Event()
        second_acquired = threading.Event()

        def first() -> None:
            with assignment_transaction_lock(CODE_A):
                first_acquired.set()
                release_first.wait(2)

        def second() -> None:
            first_acquired.wait(2)
            with assignment_transaction_lock(CODE_B):
                second_acquired.set()

        thread_a = threading.Thread(target=first)
        thread_b = threading.Thread(target=second)
        thread_a.start()
        thread_b.start()
        self.assertTrue(second_acquired.wait(1))
        release_first.set()
        thread_a.join(2)
        thread_b.join(2)

    def test_exception_releases_stock_transaction_lock(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "injected"):
            with assignment_transaction_lock(CODE_A):
                raise RuntimeError("injected")
        with assignment_transaction_lock(CODE_A):
            acquired_again = True
        self.assertTrue(acquired_again)

    def test_journal_state_machine_and_atomic_terminal_evidence(self) -> None:
        before = AssignmentEpisodeTarget.unassigned()
        target = self._target(self.instance_a)
        transaction_id = self._create_prepared_journal(
            before=before,
            target=target,
            before_episode=None,
        )
        self.assertEqual(
            ASSIGNMENT_TRANSACTION_PREPARED,
            self.journal_repository.get(CODE_A, transaction_id)["state"],
        )
        prepared = self.journal_repository.get(CODE_A, transaction_id)
        self.assertNotIn("config", prepared)
        self.assertNotIn("before_config", prepared)
        self.assertNotIn("after_config", prepared)
        self.assertEqual(
            self.episode_repository.episodes_root / "_transactions",
            self.journal_repository.transactions_root,
        )
        self.journal_repository.transition(
            CODE_A,
            transaction_id,
            ASSIGNMENT_TRANSACTION_EPISODE_APPLIED,
            after_episode_identity=self._identity(target),
            after_episode_fingerprint="EPISODE-AFTER",
        )
        self.journal_repository.transition(
            CODE_A,
            transaction_id,
            ASSIGNMENT_TRANSACTION_CONFIG_APPLIED,
            after_config_fingerprint="CONFIG-AFTER",
        )
        committed = self.journal_repository.transition(
            CODE_A,
            transaction_id,
            ASSIGNMENT_TRANSACTION_COMMITTED,
        )
        self.assertEqual(ASSIGNMENT_TRANSACTION_COMMITTED, committed["state"])
        self.assertTrue(self.journal_repository.document_path(CODE_A, transaction_id).exists())
        self.assertEqual([], list(self.journal_repository.transactions_root.rglob("*.tmp")))

        aborted_id = self._create_prepared_journal(
            before=before,
            target=target,
            before_episode=None,
        )
        self.assertEqual(
            ASSIGNMENT_TRANSACTION_ABORTED,
            self.journal_repository.transition(
                CODE_A,
                aborted_id,
                ASSIGNMENT_TRANSACTION_ABORTED,
            )["state"],
        )
        rolled_back_id = self._create_prepared_journal(
            before=before,
            target=target,
            before_episode=None,
        )
        self.journal_repository.transition(
            CODE_A,
            rolled_back_id,
            ASSIGNMENT_TRANSACTION_EPISODE_APPLIED,
        )
        self.assertEqual(
            ASSIGNMENT_TRANSACTION_ROLLED_BACK,
            self.journal_repository.transition(
                CODE_A,
                rolled_back_id,
                ASSIGNMENT_TRANSACTION_ROLLED_BACK,
            )["state"],
        )
        reconcile_id = self._create_prepared_journal(
            before=before,
            target=target,
            before_episode=None,
        )
        self.assertEqual(
            ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED,
            self.journal_repository.transition(
                CODE_A,
                reconcile_id,
                ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED,
            )["state"],
        )

    def test_prepared_journal_failure_causes_zero_config_or_episode_mutation(self) -> None:
        config_before = self._config_path().read_bytes()
        episode_path = self.episode_repository.document_path(CODE_A)
        with patch.object(
            self.journal_repository,
            "create_prepared",
            side_effect=OSError("injected journal failure"),
        ):
            result = self._execute(self.instance_a)

        self.assertFalse(result.success)
        self.assertEqual("PREPARE_FAILED", result.error_code)
        self.assertEqual(config_before, self._config_path().read_bytes())
        self.assertFalse(episode_path.exists())

    def test_foundation_commits_with_field_patch_and_preserves_unrelated_updates(self) -> None:
        original_transition = CanonicalAssignmentEpisodeRepository.transition_episode

        def interleaved_update(repository, *args, **kwargs):
            update = self.stock_repository.patch_stock_config(
                CODE_A,
                {
                    "buy_amount": 333_000,
                    "buy_limit_amount": 444_000,
                    "operation_mode": "SCHEDULED",
                    "policy_overrides": {"entry": False, "exit": True},
                },
            )
            self.assertTrue(update.ok, update)
            return original_transition(repository, *args, **kwargs)

        with patch.object(
            CanonicalAssignmentEpisodeRepository,
            "transition_episode",
            interleaved_update,
        ):
            result = self._execute(self.instance_a)

        self.assertTrue(result.success, result)
        self.assertEqual(ASSIGNMENT_TRANSACTION_COMMITTED, result.journal_state)
        config = self._config()
        self.assertEqual(self.instance_a, config["assigned_routine_instance_id"])
        self.assertEqual(333_000, config["buy_amount"])
        self.assertEqual(444_000, config["buy_limit_amount"])
        self.assertEqual("SCHEDULED", config["operation_mode"])
        self.assertEqual({"entry": False, "exit": True}, config["policy_overrides"])

    def test_same_assignment_field_conflict_never_overwrites_external_identity(self) -> None:
        original_transition = CanonicalAssignmentEpisodeRepository.transition_episode

        def interleaved_conflict(repository, *args, **kwargs):
            external = self.stock_repository.patch_stock_config(
                CODE_A,
                {"assigned_routine_instance_id": self.instance_c},
            )
            self.assertTrue(external.ok, external)
            return original_transition(repository, *args, **kwargs)

        with patch.object(
            CanonicalAssignmentEpisodeRepository,
            "transition_episode",
            interleaved_conflict,
        ):
            result = self._execute(self.instance_b)

        self.assertFalse(result.success)
        self.assertEqual(ASSIGNMENT_TRANSACTION_FIELD_CONFLICT, result.error_code)
        self.assertEqual(
            ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED,
            result.journal_state,
        )
        self.assertEqual(self.instance_c, self._config()["assigned_routine_instance_id"])

    def test_history_field_conflict_blocks_assignment_and_preserves_external_history(self) -> None:
        original_transition = CanonicalAssignmentEpisodeRepository.transition_episode
        external_history = [
            {
                "instance_id": self.instance_c,
                "registered_at": CHANGED_AT,
                "unregistered_at": "2026-08-29T10:00:00+09:00",
                "display_hidden": True,
            }
        ]

        def interleaved_history(repository, *args, **kwargs):
            external = self.stock_repository.patch_stock_config(
                CODE_A,
                {"routine_assignment_history": external_history},
            )
            self.assertTrue(external.ok, external)
            return original_transition(repository, *args, **kwargs)

        with patch.object(
            CanonicalAssignmentEpisodeRepository,
            "transition_episode",
            interleaved_history,
        ):
            result = self._execute(self.instance_b)

        self.assertFalse(result.success)
        self.assertEqual(ASSIGNMENT_TRANSACTION_FIELD_CONFLICT, result.error_code)
        self.assertEqual(ASSIGNMENT_TRANSACTION_ROLLED_BACK, result.journal_state)
        config = self._config()
        self.assertEqual("", config["assigned_routine_instance_id"])
        self.assertEqual(external_history, config["routine_assignment_history"])

    def test_episode_is_conditionally_rolled_back_when_config_write_fails(self) -> None:
        failed_write = StockConfigWriteResult(
            ok=False,
            changed=False,
            field_keys=("assigned_routine_instance_id",),
            conflict_detected=False,
            read_back_verified=False,
            reason_code=STOCK_CONFIG_WRITE_ATOMIC_WRITE_FAILED,
        )
        with patch.object(
            self.stock_repository,
            "patch_stock_config",
            return_value=failed_write,
        ):
            result = self._execute(self.instance_a)

        self.assertFalse(result.success)
        self.assertEqual(ASSIGNMENT_TRANSACTION_ROLLED_BACK, result.journal_state)
        self.assertIsNone(self.episode_repository.get_open_episode(CODE_A))
        self.assertEqual("", self._config()["assigned_routine_instance_id"])

    def test_episode_changed_after_transaction_is_never_rolled_back(self) -> None:
        failed_write = StockConfigWriteResult(
            ok=False,
            changed=False,
            field_keys=("assigned_routine_instance_id",),
            conflict_detected=False,
            read_back_verified=False,
            reason_code=STOCK_CONFIG_WRITE_ATOMIC_WRITE_FAILED,
        )

        def change_episode_then_fail(*_args, **_kwargs):
            changed = self.episode_repository.transition_episode(
                CODE_A,
                self._target(self.instance_c),
                changed_at="2026-08-29T09:01:00+09:00",
                start_reason="EXTERNAL",
                end_reason="EXTERNAL",
                source="TEST_EXTERNAL",
            )
            self.assertTrue(changed.success, changed)
            return failed_write

        with patch.object(
            self.stock_repository,
            "patch_stock_config",
            side_effect=change_episode_then_fail,
        ):
            result = self._execute(self.instance_b)

        self.assertFalse(result.success)
        self.assertEqual(
            ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED,
            result.journal_state,
        )
        self.assertEqual(
            self.instance_c,
            self.episode_repository.get_open_episode(CODE_A).instance_id,
        )

    def test_config_compensation_is_field_specific_and_conditional(self) -> None:
        self._assign_a_and_open_episode()
        before_a = self._config()
        assigned_b = self._execute(self.instance_b)
        self.assertTrue(assigned_b.success, assigned_b)
        transaction_b = self._config()
        unrelated = self.stock_repository.patch_stock_config(
            CODE_A,
            {"buy_amount": 777_000, "buy_limit_amount": 888_000},
        )
        self.assertTrue(unrelated.ok, unrelated)

        compensated = conditional_compensate_assignment_config(
            self.stock_repository,
            CODE_A,
            "Sample",
            before_config=before_a,
            transaction_config=transaction_b,
        )
        self.assertTrue(compensated.ok, compensated)
        restored = self._config()
        self.assertEqual(self.instance_a, restored["assigned_routine_instance_id"])
        self.assertEqual(777_000, restored["buy_amount"])
        self.assertEqual(888_000, restored["buy_limit_amount"])

        external = self.stock_repository.patch_stock_config(
            CODE_A,
            {"assigned_routine_instance_id": self.instance_c},
        )
        self.assertTrue(external.ok, external)
        conflict = conditional_compensate_assignment_config(
            self.stock_repository,
            CODE_A,
            "Sample",
            before_config=before_a,
            transaction_config=transaction_b,
        )
        self.assertFalse(conflict.ok)
        self.assertEqual(STOCK_CONFIG_WRITE_FIELD_CONFLICT, conflict.reason_code)
        self.assertEqual(self.instance_c, self._config()["assigned_routine_instance_id"])

    def test_reconciliation_classifies_prepared_as_aborted(self) -> None:
        self._assign_a_and_open_episode()
        target_a = self._target(self.instance_a)
        transaction_id = self._create_prepared_journal(
            before=target_a,
            target=self._target(self.instance_b),
            before_episode=target_a,
        )
        results = reconcile_incomplete_assignment_transactions(self.root)
        result = next(item for item in results if item.transaction_id == transaction_id)
        self.assertEqual(ASSIGNMENT_TRANSACTION_ABORTED, result.terminal_state)
        self.assertEqual("NO_MUTATION", result.classification)
        self.assertFalse(result.review_required)

    def test_reconciliation_classifies_episode_applied_gap_as_review_required(self) -> None:
        self._assign_a_and_open_episode()
        target_a = self._target(self.instance_a)
        target_b = self._target(self.instance_b)
        transaction_id = self._create_prepared_journal(
            before=target_a,
            target=target_b,
            before_episode=target_a,
        )
        transition = self.episode_repository.transition_episode(
            CODE_A,
            target_b,
            changed_at="2026-08-29T09:02:00+09:00",
            start_reason="TEST_CRASH",
            end_reason="TEST_CRASH",
            source="TEST",
        )
        self.assertTrue(transition.success, transition)
        self.journal_repository.transition(
            CODE_A,
            transaction_id,
            ASSIGNMENT_TRANSACTION_EPISODE_APPLIED,
            after_episode_identity=self._identity(target_b),
            after_episode_fingerprint="EPISODE-B",
        )

        results = reconcile_incomplete_assignment_transactions(self.root)
        result = next(item for item in results if item.transaction_id == transaction_id)
        self.assertEqual(
            ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED,
            result.terminal_state,
        )
        self.assertEqual("CONFIG_EPISODE_MISMATCH", result.classification)
        self.assertTrue(result.review_required)

    def test_reconciliation_terminalizes_config_applied_consistent_target(self) -> None:
        self._assign_a_and_open_episode()
        target_a = self._target(self.instance_a)
        target_b = self._target(self.instance_b)
        transaction_id = self._create_prepared_journal(
            before=target_a,
            target=target_b,
            before_episode=target_a,
        )
        transition = self.episode_repository.transition_episode(
            CODE_A,
            target_b,
            changed_at="2026-08-29T09:02:00+09:00",
            start_reason="TEST_CRASH",
            end_reason="TEST_CRASH",
            source="TEST",
        )
        self.assertTrue(transition.success, transition)
        self.journal_repository.transition(
            CODE_A,
            transaction_id,
            ASSIGNMENT_TRANSACTION_EPISODE_APPLIED,
            after_episode_identity=self._identity(target_b),
            after_episode_fingerprint="EPISODE-B",
        )
        config_update = self.stock_repository.patch_stock_config(
            CODE_A,
            {
                "assigned_routine_instance_id": self.instance_b,
                "routine_definition_id": "sample",
                "routine_instance_name": "Beta",
                "routine_type": "Sample",
            },
        )
        self.assertTrue(config_update.ok, config_update)
        self.journal_repository.transition(
            CODE_A,
            transaction_id,
            ASSIGNMENT_TRANSACTION_CONFIG_APPLIED,
            after_config_fingerprint=config_update.after_fingerprint,
        )

        results = reconcile_incomplete_assignment_transactions(self.root)
        result = next(item for item in results if item.transaction_id == transaction_id)
        self.assertEqual(ASSIGNMENT_TRANSACTION_COMMITTED, result.terminal_state)
        self.assertEqual("CONSISTENT_TARGET", result.classification)
        self.assertFalse(result.review_required)

    def test_reconciliation_missing_dependency_is_fail_closed(self) -> None:
        self._assign_a_and_open_episode()
        target_a = self._target(self.instance_a)
        missing_target = {
            "ownership_kind": "ASSIGNED",
            "instance_id": str(uuid4()),
            "group_id": str(uuid4()),
            "definition_id": "sample",
        }
        transaction_id = str(uuid4())
        self.journal_repository.create_prepared(
            transaction_id=transaction_id,
            stock_code=CODE_A,
            operation="REASSIGN",
            before_assignment_identity=self._identity(target_a),
            target_assignment_identity=missing_target,
            before_config_fingerprint="CONFIG-A",
            before_episode_identity=self._identity(target_a),
            before_episode_fingerprint="EPISODE-A",
            reason="TEST_MISSING",
            source="TEST",
        )
        result = reconcile_incomplete_assignment_transactions(self.root)[0]
        self.assertEqual("MISSING_DEPENDENCY", result.classification)
        self.assertEqual(
            ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED,
            result.terminal_state,
        )
        self.assertTrue(result.review_required)

    def test_reconciliation_missing_group_is_fail_closed(self) -> None:
        self._assign_a_and_open_episode()
        target_a = self._target(self.instance_a)
        target_b = self._target(self.instance_b)
        transaction_id = self._create_prepared_journal(
            before=target_a,
            target=target_b,
            before_episode=target_a,
        )
        group_path = self.root / "groups" / self.group_b
        (group_path / "group.json").unlink()
        group_path.rmdir()

        results = reconcile_incomplete_assignment_transactions(self.root)
        result = next(item for item in results if item.transaction_id == transaction_id)
        self.assertEqual("MISSING_DEPENDENCY", result.classification)
        self.assertEqual(
            ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED,
            result.terminal_state,
        )
        self.assertTrue(result.review_required)

    def test_rename_display_snapshot_is_not_identity_mismatch(self) -> None:
        self._assign_a_and_open_episode()
        target_a = self._target(self.instance_a)
        transaction_id = self._create_prepared_journal(
            before=target_a,
            target=target_a,
            before_episode=target_a,
        )
        instance_path = (
            self.root / "routine_instances" / self.instance_a / "instance.json"
        )
        metadata = json.loads(instance_path.read_text(encoding="utf-8"))
        metadata["display_name"] = "Alpha Renamed"
        self._json(instance_path, metadata)

        results = reconcile_incomplete_assignment_transactions(self.root)
        result = next(item for item in results if item.transaction_id == transaction_id)
        self.assertEqual(ASSIGNMENT_TRANSACTION_COMMITTED, result.terminal_state)
        self.assertFalse(result.review_required)

    def test_invalid_config_and_episode_are_fail_closed(self) -> None:
        self._assign_a_and_open_episode()
        target_a = self._target(self.instance_a)
        transaction_id = self._create_prepared_journal(
            before=target_a,
            target=self._target(self.instance_b),
            before_episode=target_a,
        )
        self._config_path().write_text("not-json", encoding="utf-8")
        result = reconcile_incomplete_assignment_transactions(self.root)[0]
        self.assertEqual(transaction_id, result.transaction_id)
        self.assertEqual("INVALID_PERSISTENCE_EVIDENCE", result.classification)
        self.assertTrue(result.review_required)

        # A separate fixture is unnecessary here: the episode reader is independently
        # exercised by replacing the invalid config with a valid current snapshot.
        self._json(
            self._config_path(),
            {
                "assigned_routine_instance_id": self.instance_a,
                "routine_instance_name": "Alpha",
                "routine_definition_id": "sample",
                "routine_type": "Sample",
            },
        )
        resolved = next(
            item
            for item in reconcile_incomplete_assignment_transactions(self.root)
            if item.transaction_id == transaction_id
        )
        self.assertEqual(ASSIGNMENT_TRANSACTION_ROLLED_BACK, resolved.terminal_state)
        second_id = self._create_prepared_journal(
            before=target_a,
            target=self._target(self.instance_b),
            before_episode=target_a,
        )
        self.episode_repository.document_path(CODE_A).write_text(
            "not-json",
            encoding="utf-8",
        )
        second = next(
            item
            for item in reconcile_incomplete_assignment_transactions(self.root)
            if item.transaction_id == second_id
        )
        self.assertEqual(second_id, second.transaction_id)
        self.assertEqual("INVALID_PERSISTENCE_EVIDENCE", second.classification)
        self.assertTrue(second.review_required)

    def test_history_hide_uses_field_patch_and_preserves_other_config(self) -> None:
        config = self._config()
        config["routine_assignment_history"] = [
            {
                "instance_id": self.instance_a,
                "registered_at": CHANGED_AT,
                "unregistered_at": "2026-08-29T10:00:00+09:00",
                "display_hidden": False,
            }
        ]
        self._json(self._config_path(), config)
        with patch.object(
            self.stock_repository,
            "patch_stock_config",
            wraps=self.stock_repository.patch_stock_config,
        ) as canonical_patch:
            hidden = self.stock_repository.hide_routine_assignment_history(
                code=CODE_A,
                instance_id=self.instance_a,
            )
        self.assertTrue(hidden)
        canonical_patch.assert_called_once()
        saved = self._config()
        self.assertTrue(saved["routine_assignment_history"][0]["display_hidden"])
        self.assertEqual(100_000, saved["buy_amount"])
        self.assertEqual(200_000, saved["buy_limit_amount"])


if __name__ == "__main__":
    unittest.main()
