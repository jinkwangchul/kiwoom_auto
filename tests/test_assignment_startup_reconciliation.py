from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from assignment_episode_linkage import (
    ASSIGNMENT_TRANSACTION_ABORTED,
    ASSIGNMENT_TRANSACTION_COMMITTED,
    ASSIGNMENT_TRANSACTION_CONFIG_APPLIED,
    ASSIGNMENT_TRANSACTION_EPISODE_APPLIED,
    ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED,
    AssignmentTransactionJournalRepository,
    assignment_target_from_config,
    execute_assignment_transaction_foundation,
)
from assignment_episode_repository import (
    AssignmentEpisodeTarget,
    CanonicalAssignmentEpisodeRepository,
)
from assignment_startup_reconciliation_service import (
    ASSIGNMENT_RECONCILIATION_REASON_CODE,
    apply_assignment_reconciliation_to_production_registry,
    reconcile_assignment_startup,
)
from execution_universe import REVIEW_REQUIRED, project_execution_universe
from production_recovery_contract import (
    RecoverySessionIdentity,
    STOCK_RESTORED,
)
from production_recovery_state_registry import (
    RECOVERY_COMPLETED,
    RECOVERY_STOCK_REVIEW_REQUIRED,
    ProductionRecoveryStateRegistry,
    check_production_recovery_gate,
)
from runtime_stock_state_mutation import RuntimeStockStateMutationResult
from stock_repository import StockRepository


CODE_A = "005930"
CODE_B = "000660"
CHANGED_AT = "2026-08-29T09:00:00+09:00"


class AssignmentStartupReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.group_a = str(uuid4())
        self.group_b = str(uuid4())
        self.instance_a = str(uuid4())
        self.instance_b = str(uuid4())
        self.events: list[dict[str, object]] = []
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
        for group_id, display_name, slot in (
            (self.group_a, "Group", 0),
            (self.group_b, "Group_1", 1),
        ):
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
                "group_ids": [self.group_a, self.group_b],
                "cutover_at": CHANGED_AT,
            },
        )
        for instance_id, group_id, display_name in (
            (self.instance_a, self.group_a, "Alpha"),
            (self.instance_b, self.group_b, "Beta"),
        ):
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
            self._json(
                self.root / "routine_instances" / instance_id / "rules.json",
                {},
            )
        for code, name in ((CODE_A, "Samsung"), (CODE_B, "SKHynix")):
            stock = self.root / "stocks" / f"{code}_{name}"
            self._json(
                stock / "config.json",
                {
                    "routines": [],
                    "assigned_routine_instance_id": "",
                    "operation_mode": "CONTINUOUS",
                },
            )
            self._json(
                stock / "state.json",
                {"status": "STOPPED", "trade_enabled": False},
            )
            self._json(stock / "orders.json", {"orders": []})

    def _event_writer(self, event_type: str, **fields):
        self.events.append({"event_type": event_type, **fields})
        return {"appended": True}

    def _target(self, instance_id: str):
        return assignment_target_from_config(
            self.root,
            {"assigned_routine_instance_id": instance_id},
        )

    @staticmethod
    def _identity(target) -> dict[str, str]:
        return {
            "ownership_kind": target.ownership_kind,
            "instance_id": target.instance_id or "",
            "group_id": target.group_id or "",
            "definition_id": target.definition_id or "",
        }

    def _assign_a(self) -> None:
        result = execute_assignment_transaction_foundation(
            self.root,
            CODE_A,
            stock_name="Samsung",
            target_instance_id=self.instance_a,
            target_routine_type="Sample",
            changed_at=CHANGED_AT,
            reason="TEST_ASSIGNMENT",
            source="TEST",
            stock_repository=self.stock_repository,
            episode_repository=self.episode_repository,
            journal_repository=self.journal_repository,
        )
        self.assertTrue(result.success, result)

    def _prepared(self, target_identity: dict[str, str] | None = None) -> str:
        before = self._target(self.instance_a)
        target = self._target(self.instance_b)
        transaction_id = str(uuid4())
        self.journal_repository.create_prepared(
            transaction_id=transaction_id,
            stock_code=CODE_A,
            operation="REASSIGN",
            before_assignment_identity=self._identity(before),
            target_assignment_identity=(target_identity or self._identity(target)),
            before_config_fingerprint="CONFIG-A",
            before_episode_identity=self._identity(before),
            before_episode_fingerprint="EPISODE-A",
            reason="TEST_CRASH",
            source="TEST",
        )
        return transaction_id

    def _episode_to_b(self, transaction_id: str) -> None:
        target_b = self._target(self.instance_b)
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

    def _config_to_b(self, transaction_id: str) -> None:
        result = self.stock_repository.patch_stock_config(
            CODE_A,
            {
                "assigned_routine_instance_id": self.instance_b,
                "routine_definition_id": "sample",
                "routine_instance_name": "Beta",
                "routine_type": "Sample",
            },
        )
        self.assertTrue(result.ok, result)
        self.journal_repository.transition(
            CODE_A,
            transaction_id,
            ASSIGNMENT_TRANSACTION_CONFIG_APPLIED,
            after_config_fingerprint=result.after_fingerprint,
        )

    def _run(self) -> dict[str, object]:
        return reconcile_assignment_startup(
            self.root,
            event_writer=self._event_writer,
        )

    def _state(self) -> dict[str, object]:
        path = self.stock_repository.resolve_stock_dir(CODE_A) / "state.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_no_journal_leaves_review_and_assignment_unchanged(self) -> None:
        before = self._state()
        summary = self._run()
        self.assertEqual(0, summary["scanned"])
        self.assertEqual(before, self._state())
        self.assertEqual(0, summary["review_required"])

    def test_terminal_journal_is_not_scanned_or_mutated(self) -> None:
        self._assign_a()
        transaction_dir = self.journal_repository.transactions_root / CODE_A
        journal_path = next(transaction_dir.glob("*.json"))
        before = journal_path.read_bytes()

        summary = self._run()

        self.assertEqual(0, summary["scanned"])
        self.assertEqual(before, journal_path.read_bytes())

    def test_target_complete_terminalizes_committed_without_review(self) -> None:
        self._assign_a()
        transaction_id = self._prepared()
        self._episode_to_b(transaction_id)
        self._config_to_b(transaction_id)

        summary = self._run()
        self.assertEqual(1, summary["committed_terminalized"])
        self.assertEqual(
            ASSIGNMENT_TRANSACTION_COMMITTED,
            self.journal_repository.get(CODE_A, transaction_id)["state"],
        )
        self.assertFalse(self._state().get("review_required", False))
        self.assertTrue(
            any(
                event["event_type"] == "RECOVERY_COMPLETED"
                and event.get("reason_code") == "ASSIGNMENT_JOURNAL_COMMITTED"
                for event in self.events
            )
        )

    def test_prepared_without_mutation_terminalizes_aborted(self) -> None:
        self._assign_a()
        transaction_id = self._prepared()
        summary = self._run()
        self.assertEqual(1, summary["aborted_terminalized"])
        self.assertEqual(
            ASSIGNMENT_TRANSACTION_ABORTED,
            self.journal_repository.get(CODE_A, transaction_id)["state"],
        )

    def test_episode_config_mismatch_enters_review_and_execution_universe_excludes(self) -> None:
        self._assign_a()
        transaction_id = self._prepared()
        self._episode_to_b(transaction_id)
        summary = self._run()
        self.assertEqual((CODE_A,), summary["review_stock_codes"])
        state = self._state()
        self.assertEqual("REVIEW_REQUIRED", state["status"])
        self.assertIn(ASSIGNMENT_RECONCILIATION_REASON_CODE, state["review_detail"])

        class Window:
            @staticmethod
            def startup_recovery_session_ready(*, refresh=False):
                return True

        with patch(
            "execution_universe.auto_trade_current_session_operation_participant_codes",
            return_value=(CODE_A,),
        ):
            snapshot = project_execution_universe(
                Window(),
                stock_dirs=(self.stock_repository.resolve_stock_dir(CODE_A),),
            )
        self.assertFalse(snapshot.entries[0].execution_member)
        self.assertIn(REVIEW_REQUIRED, snapshot.entries[0].blockers)

    def test_missing_dependency_enters_review_without_auto_repair(self) -> None:
        self._assign_a()
        missing = {
            "ownership_kind": "ASSIGNED",
            "instance_id": str(uuid4()),
            "group_id": str(uuid4()),
            "definition_id": "sample",
        }
        transaction_id = self._prepared(missing)
        summary = self._run()
        result = next(
            item for item in summary["results"] if item["transaction_id"] == transaction_id
        )
        self.assertEqual("MISSING_DEPENDENCY", result["classification"])
        self.assertEqual(
            ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED,
            self.journal_repository.get(CODE_A, transaction_id)["state"],
        )
        config = json.loads(
            (self.stock_repository.resolve_stock_dir(CODE_A) / "config.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(self.instance_a, config["assigned_routine_instance_id"])

    def test_missing_group_enters_review_without_recreating_group(self) -> None:
        self._assign_a()
        transaction_id = self._prepared()
        group_path = self.root / "groups" / self.group_b
        for path in group_path.iterdir():
            path.unlink()
        group_path.rmdir()

        summary = self._run()
        result = next(
            item for item in summary["results"] if item["transaction_id"] == transaction_id
        )

        self.assertEqual("MISSING_DEPENDENCY", result["classification"])
        self.assertEqual("REVIEW_REQUIRED", self._state()["status"])
        self.assertFalse(group_path.exists())

    def test_consistent_unknown_identity_is_not_auto_selected_as_winner(self) -> None:
        self._assign_a()
        transaction_id = self._prepared()
        unassigned = AssignmentEpisodeTarget.unassigned()
        transition = self.episode_repository.transition_episode(
            CODE_A,
            unassigned,
            changed_at="2026-08-29T09:03:00+09:00",
            start_reason="TEST_UNKNOWN_STATE",
            end_reason="TEST_UNKNOWN_STATE",
            source="TEST",
        )
        self.assertTrue(transition.success, transition)
        write_result = self.stock_repository.patch_stock_config(
            CODE_A,
            {"assigned_routine_instance_id": ""},
        )
        self.assertTrue(write_result.ok, write_result)

        summary = self._run()
        result = next(
            item for item in summary["results"] if item["transaction_id"] == transaction_id
        )

        self.assertEqual("CONFIG_EPISODE_MISMATCH", result["classification"])
        self.assertEqual("REVIEW_REQUIRED", self._state()["status"])
        self.assertEqual("", self.stock_repository.find_by_code(CODE_A).assigned_routine_instance_id)

    def test_same_instance_rename_does_not_create_mismatch(self) -> None:
        self._assign_a()
        transaction_id = self._prepared()
        self._episode_to_b(transaction_id)
        self._config_to_b(transaction_id)
        instance_path = (
            self.root / "routine_instances" / self.instance_b / "instance.json"
        )
        instance = json.loads(instance_path.read_text(encoding="utf-8"))
        instance["display_name"] = "Beta Renamed"
        self._json(instance_path, instance)

        summary = self._run()

        self.assertEqual(1, summary["committed_terminalized"])
        self.assertFalse(self._state().get("review_required", False))

    def test_duplicate_non_terminal_journals_are_reviewed_idempotently(self) -> None:
        self._assign_a()
        first = self._prepared()
        second = self._prepared()
        summary = self._run()
        self.assertEqual(2, summary["review_required_results"])
        self.assertEqual(1, summary["review_required"])
        for transaction_id in (first, second):
            self.assertEqual(
                ASSIGNMENT_TRANSACTION_RECONCILIATION_REQUIRED,
                self.journal_repository.get(CODE_A, transaction_id)["state"],
            )
        state_before = self._state()
        repeated = self._run()
        state_after = self._state()
        self.assertEqual(1, repeated["review_required"])
        self.assertEqual(state_before, state_after)
        self.assertEqual(2, state_after["review_detail"].count("transaction_id="))

    def test_existing_review_cause_is_preserved_and_not_duplicated(self) -> None:
        self._assign_a()
        transaction_id = self._prepared()
        self._episode_to_b(transaction_id)
        state_path = self.stock_repository.resolve_stock_dir(CODE_A) / "state.json"
        self._json(
            state_path,
            {
                "status": "REVIEW_REQUIRED",
                "review_required": True,
                "review_status": "PENDING",
                "review_reason": "사용자 검토정지",
                "review_detail": "existing evidence",
                "review_entered_at": "2026-08-29 08:00:00",
            },
        )
        self._run()
        first = self._state()
        self._run()
        second = self._state()
        self.assertIn("사용자 검토정지", second["review_reason"])
        self.assertIn("루틴 할당 정보 불일치", second["review_reason"])
        self.assertEqual("2026-08-29 08:00:00", second["review_entered_at"])
        self.assertEqual(first, second)

    def test_invalid_journal_is_not_silently_ignored(self) -> None:
        bad = (
            self.journal_repository.transactions_root / CODE_A / f"{uuid4()}.json"
        )
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("not-json", encoding="utf-8")
        summary = self._run()
        self.assertEqual(1, summary["scanned"])
        self.assertEqual("INVALID_JOURNAL", summary["results"][0]["classification"])
        self.assertEqual("REVIEW_REQUIRED", self._state()["status"])
        self.assertTrue(bad.exists())

    def test_unidentified_root_journal_fails_closed_without_deletion(self) -> None:
        bad = self.journal_repository.transactions_root / f"{uuid4()}.json"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("not-json", encoding="utf-8")

        summary = self._run()

        self.assertEqual(1, summary["scanned"])
        self.assertTrue(summary["global_fail_closed"])
        self.assertFalse(summary["other_stocks_continue"])
        self.assertEqual("INVALID_JOURNAL", summary["results"][0]["classification"])
        self.assertTrue(bad.exists())

    def test_review_write_failure_blocks_only_that_stock_in_production_registry(self) -> None:
        self._assign_a()
        transaction_id = self._prepared()
        self._episode_to_b(transaction_id)
        failed_mutation = RuntimeStockStateMutationResult(
            ok=False,
            before_status="STOPPED",
            after_status="REVIEW_REQUIRED",
            read_back_verified=False,
            reason="WRITE_FAILED",
        )
        with patch(
            "assignment_startup_reconciliation_service.mutate_runtime_stock_state",
            return_value=failed_mutation,
        ):
            summary = self._run()
        self.assertEqual(1, summary["review_transition_failures"])

        identity = RecoverySessionIdentity(
            recovery_session_id=str(uuid4()),
            login_session_id="login-session",
            account_no="12345678",
            trading_day="2026-08-29",
            requested_at=CHANGED_AT,
        )
        registry = ProductionRecoveryStateRegistry()
        registry.begin_recovery(identity)
        registry.mark_collecting(identity)
        registry.mark_reconciling(identity)
        for code in (CODE_A, CODE_B):
            registry.set_stock_result(
                identity,
                stock_code=code,
                stock_status=STOCK_RESTORED,
            )
        registry.complete_account(identity)
        applied = apply_assignment_reconciliation_to_production_registry(
            summary,
            identity=identity,
            registry=registry,
        )
        self.assertTrue(applied["ok"], applied)

        blocked = check_production_recovery_gate(
            login_session_id=identity.login_session_id,
            account_no=identity.account_no,
            trading_day=identity.trading_day,
            stock_code=CODE_A,
            recovery_session_id=identity.recovery_session_id,
            registry=registry,
        )
        allowed = check_production_recovery_gate(
            login_session_id=identity.login_session_id,
            account_no=identity.account_no,
            trading_day=identity.trading_day,
            stock_code=CODE_B,
            recovery_session_id=identity.recovery_session_id,
            registry=registry,
        )
        self.assertFalse(blocked.allowed)
        self.assertEqual(RECOVERY_STOCK_REVIEW_REQUIRED, blocked.reason_code)
        self.assertTrue(allowed.allowed)
        self.assertEqual(RECOVERY_COMPLETED, allowed.reason_code)

    def test_main_startup_hook_precedes_recovery_assessment_and_refresh(self) -> None:
        source = (self.root.parent / "unused").as_posix()
        del source
        gui_source = (
            Path(__file__).resolve().parents[1] / "gui_windows.py"
        ).read_text(encoding="utf-8")
        reconciliation = gui_source.index("reconcile_assignment_startup(PROJECT_ROOT)")
        assessment = gui_source.index("self.refresh_startup_recovery_status()", reconciliation)
        refresh = gui_source.index("self.refresh_all()", assessment)
        self.assertLess(reconciliation, assessment)
        self.assertLess(assessment, refresh)


if __name__ == "__main__":
    unittest.main()
