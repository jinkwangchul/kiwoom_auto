from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from legacy_performance_migration import (
    BOOTSTRAP_CURRENT_ASSIGNED,
    ECONOMIC_FACT_ONLY,
    FULLY_RECOVERABLE,
    LEGACY_IDENTITY_RECOVERABLE,
    NOT_RECOVERABLE,
    OWNERSHIP_UNRESOLVED,
    PARTIALLY_RECOVERABLE,
    apply_legacy_performance_migration,
    build_legacy_performance_migration_plan,
    migration_readiness_summary,
    planned_phase_8b_apply_paths,
    run_legacy_performance_migration_dry_run,
    verify_legacy_performance_source_hashes,
)


BOOTSTRAP_AT = "2026-08-23T12:00:00+09:00"
CODE = "005930"


class LegacyPerformanceMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "project"
        self.root.mkdir()
        (self.root / "runtime").mkdir()
        self.write_runtime([])

    def write_json(self, path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def write_runtime(self, realizations: list[dict]) -> None:
        self.write_json(self.root / "runtime" / "realized_pnl.json", {"realizations": realizations})
        self.write_json(self.root / "runtime" / "fills.json", {"fills": []})
        self.write_json(self.root / "runtime" / "order_executions.json", {"executions": []})

    def add_group_and_instance(self, instance_id: str = "INSTANCE-A", group_id: str = "GROUP-A") -> None:
        self.write_json(
            self.root / "groups" / "registry.json",
            {"group_ids": [group_id]},
        )
        self.write_json(
            self.root / "groups" / group_id / "group.json",
            {"group_id": group_id, "display_name": "Group A", "definition_id": "definition-a"},
        )
        self.write_json(
            self.root / "routine_instances" / instance_id / "instance.json",
            {
                "instance_id": instance_id,
                "display_name": "Instance A",
                "definition_id": "definition-a",
                "group_id": group_id,
            },
        )

    def add_stock(self, history: list[object], *, assigned: str = "") -> Path:
        path = self.root / "stocks" / f"{CODE}_Sample" / "config.json"
        self.write_json(
            path,
            {
                "code": CODE,
                "assigned_routine_instance_id": assigned,
                "routine_instance_name": "Instance A" if assigned else "",
                "routine_definition_id": "definition-a" if assigned else "",
                "routine_assignment_history": history,
            },
        )
        self.write_json(path.parent / "orders.json", {"orders": []})
        return path

    @staticmethod
    def history(instance: str, start: str, end: str, **extra) -> dict:
        return {
            "instance_id": instance,
            "instance_name": instance,
            "definition_id": "definition-a",
            "registered_at": start,
            "unregistered_at": end,
            **extra,
        }

    @staticmethod
    def realization(*, identity: bool = True, gross: int = 20) -> dict:
        return {
            "realization_id": "REALIZATION-1" if identity else "",
            "trade_date": "2026-08-20",
            "stock_code": CODE,
            "realized_at": "2026-08-20T10:00:00+09:00",
            "sell_quantity": 2,
            "matched_cost_basis": 200,
            "gross_realized_profit": gross,
            "fee": None,
            "tax": None,
            "net_realized_profit": None,
        }

    def plan(self):
        return build_legacy_performance_migration_plan(self.root, bootstrap_at=BOOTSTRAP_AT)

    def dry_run(self, plan=None):
        output = Path(self.temporary.name) / "dry-run"
        return run_legacy_performance_migration_dry_run(plan or self.plan(), output)

    def test_a_to_b_to_a_preserves_three_historical_episodes(self) -> None:
        self.add_stock(
            [
                self.history("A", "2026-08-01T09:00:00+09:00", "2026-08-01T10:00:00+09:00", group_id="G"),
                self.history("B", "2026-08-01T10:00:00+09:00", "2026-08-01T11:00:00+09:00", group_id="G"),
                self.history("A", "2026-08-01T11:00:00+09:00", "2026-08-01T12:00:00+09:00", group_id="G"),
            ]
        )
        result = self.dry_run()
        episodes = json.loads((Path(result["dry_run_root"]) / "assignment_episodes" / CODE / "episodes.json").read_text(encoding="utf-8"))["episodes"]
        self.assertEqual(["A", "B", "A", None], [item["instance_id"] for item in episodes])
        self.assertEqual(3, sum(not item["ended_at"] is None for item in episodes[:3]))

    def test_missing_historical_group_is_partial_and_never_inferred_from_current_instance(self) -> None:
        self.add_group_and_instance()
        self.add_stock(
            [self.history("INSTANCE-A", "2026-08-01T09:00:00+09:00", "2026-08-01T10:00:00+09:00")],
            assigned="INSTANCE-A",
        )
        plan = self.plan()
        self.assertEqual(PARTIALLY_RECOVERABLE, plan["assignment_records"][0]["classification"])
        result = self.dry_run(plan)
        episodes = json.loads((Path(result["dry_run_root"]) / "assignment_episodes" / CODE / "episodes.json").read_text(encoding="utf-8"))["episodes"]
        self.assertIsNone(episodes[0]["group_id"])
        self.assertEqual("GROUP-A", episodes[-1]["group_id"])

    def test_current_assignment_without_history_creates_only_apply_time_bootstrap(self) -> None:
        self.add_group_and_instance()
        self.add_stock([], assigned="INSTANCE-A")
        plan = self.plan()
        self.assertEqual(BOOTSTRAP_CURRENT_ASSIGNED, plan["current_bootstraps"][0]["migration_action"])
        result = self.dry_run(plan)
        episodes = json.loads((Path(result["dry_run_root"]) / "assignment_episodes" / CODE / "episodes.json").read_text(encoding="utf-8"))["episodes"]
        self.assertEqual(1, len(episodes))
        self.assertEqual(BOOTSTRAP_AT, episodes[0]["started_at"])

    def test_legacy_realization_imports_unresolved_and_stock_lifetime_only(self) -> None:
        self.add_stock([])
        self.write_runtime([self.realization()])
        plan = self.plan()
        self.assertEqual(LEGACY_IDENTITY_RECOVERABLE, plan["performance_records"][0]["classification"])
        result = self.dry_run(plan)
        dry = result["manifest"]["dry_run_result"]
        self.assertEqual(1, dry["performance_event_count"])
        self.assertEqual(20, dry["dry_run_stock_lifetime_gross_pnl"])
        self.assertEqual(20, dry["performance_reconciliation"]["unresolved_gross_pnl"])
        self.assertEqual(0, dry["performance_reconciliation"]["instance_gross_pnl"])
        event = json.loads((Path(result["dry_run_root"]) / "performance_ledger" / CODE / "events.json").read_text(encoding="utf-8"))["events"][0]
        self.assertEqual(OWNERSHIP_UNRESOLVED, event["allocations"][0]["entry_episode_id"])

    def test_economic_fact_without_stable_identity_is_report_only(self) -> None:
        self.add_stock([])
        self.write_runtime([self.realization(identity=False)])
        plan = self.plan()
        self.assertEqual(ECONOMIC_FACT_ONLY, plan["performance_records"][0]["classification"])
        result = self.dry_run(plan)
        self.assertEqual(0, result["manifest"]["dry_run_result"]["performance_event_count"])

    def test_repeat_plan_and_dry_run_have_same_target_ids_and_no_duplicates(self) -> None:
        self.add_stock([self.history("A", "2026-08-01T09:00:00+09:00", "2026-08-01T10:00:00+09:00", group_id="G")])
        self.write_runtime([self.realization()])
        first = self.plan()
        second = self.plan()
        self.assertEqual(
            [item["target_episode_id"] for item in first["assignment_records"]],
            [item["target_episode_id"] for item in second["assignment_records"]],
        )
        self.assertEqual(first["performance_records"][0]["target_event_id"], second["performance_records"][0]["target_event_id"])
        first_result = run_legacy_performance_migration_dry_run(first, Path(self.temporary.name) / "dry-one")
        second_result = run_legacy_performance_migration_dry_run(second, Path(self.temporary.name) / "dry-two")
        self.assertEqual(1, first_result["manifest"]["dry_run_result"]["performance_event_count"])
        self.assertEqual(1, second_result["manifest"]["dry_run_result"]["performance_event_count"])
        self.assertEqual(first_result["manifest"], second_result["manifest"])

    def test_exact_duplicate_realization_is_idempotent_and_conflicting_identity_is_rejected(self) -> None:
        self.add_stock([])
        original = self.realization()
        conflict = {**original, "gross_realized_profit": 21}
        self.write_runtime([original, dict(original), conflict])
        plan = self.plan()
        self.assertEqual(
            ["IMPORT_LEGACY_NAMESPACED_EVENT", "NO_OP_DUPLICATE_SOURCE", "REJECT"],
            [item["migration_action"] for item in plan["performance_records"]],
        )
        result = self.dry_run(plan)
        self.assertEqual(1, result["manifest"]["dry_run_result"]["performance_event_count"])

    def test_dry_run_output_inside_source_project_is_rejected(self) -> None:
        self.add_stock([])
        with self.assertRaisesRegex(ValueError, "outside the source project"):
            run_legacy_performance_migration_dry_run(self.plan(), self.root / "dry-run")

    def test_source_hash_guard_detects_change(self) -> None:
        path = self.add_stock([])
        plan = self.plan()
        self.assertEqual((), verify_legacy_performance_source_hashes(plan, self.root))
        value = json.loads(path.read_text(encoding="utf-8"))
        value["memo"] = "changed"
        self.write_json(path, value)
        self.assertIn("CHANGED:stocks/005930_Sample/config.json", verify_legacy_performance_source_hashes(plan, self.root))

    def test_deleted_instance_snapshot_survives_with_unresolved_group(self) -> None:
        self.add_stock([self.history("DELETED", "2026-08-01T09:00:00+09:00", "2026-08-01T10:00:00+09:00")])
        result = self.dry_run()
        episode = json.loads((Path(result["dry_run_root"]) / "assignment_episodes" / CODE / "episodes.json").read_text(encoding="utf-8"))["episodes"][0]
        self.assertEqual("DELETED", episode["instance_id"])
        self.assertEqual("DELETED", episode["instance_name_snapshot"])
        self.assertIsNone(episode["group_id"])

    def test_corrupt_record_is_rejected_without_dropping_valid_record(self) -> None:
        self.add_stock(
            [
                self.history("A", "", "2026-08-01T10:00:00+09:00"),
                self.history("B", "2026-08-01T11:00:00+09:00", "2026-08-01T12:00:00+09:00", group_id="G"),
            ]
        )
        plan = self.plan()
        self.assertEqual([NOT_RECOVERABLE, FULLY_RECOVERABLE], [item["classification"] for item in plan["assignment_records"]])
        result = self.dry_run(plan)
        self.assertEqual(1, result["manifest"]["dry_run_result"]["historical_episode_count"])

    def test_reconciliation_counts_match_all_source_records(self) -> None:
        self.add_stock([self.history("A", "2026-08-01 09:00:00", "2026-08-01 10:00:00")])
        self.write_runtime([self.realization(), self.realization(identity=False, gross=-5)])
        plan = self.plan()
        summary = migration_readiness_summary(plan)
        result = self.dry_run(plan)["manifest"]["dry_run_result"]
        self.assertEqual(1, summary["assignment"][PARTIALLY_RECOVERABLE])
        self.assertTrue(result["assignment_reconciled"])
        self.assertTrue(result["performance_reconciled"])
        self.assertTrue(result["stock_lifetime_reconciled"])
        self.assertTrue(result["parent_difference_explained"])

    def test_apply_preview_lists_only_canonical_targets_and_manifest(self) -> None:
        self.add_stock([])
        self.write_runtime([self.realization()])
        self.assertEqual(
            (
                "assignment_episodes/005930/episodes.json",
                "performance_ledger/005930/events.json",
                "migration_manifests/legacy_performance_v1.json",
            ),
            planned_phase_8b_apply_paths(self.plan()),
        )

    def test_apply_promotes_verified_targets_and_second_apply_is_no_op(self) -> None:
        self.add_group_and_instance()
        self.add_stock(
            [self.history("INSTANCE-A", "2026-08-01T09:00:00+09:00", "2026-08-01T10:00:00+09:00")],
            assigned="INSTANCE-A",
        )
        self.write_runtime([self.realization()])
        plan = self.plan()

        first = apply_legacy_performance_migration(plan, self.root)
        self.assertTrue(first["changed"])
        self.assertEqual(2, first["read_back"]["episode_count"])
        self.assertEqual(1, first["read_back"]["historical_closed_count"])
        self.assertEqual(1, first["read_back"]["open_bootstrap_count"])
        self.assertEqual(1, first["read_back"]["performance_event_count"])
        paths = planned_phase_8b_apply_paths(plan)
        before = {path: (self.root / path).read_bytes() for path in paths}

        second = apply_legacy_performance_migration(plan, self.root)
        self.assertTrue(second["no_op"])
        self.assertFalse(second["changed"])
        self.assertEqual(before, {path: (self.root / path).read_bytes() for path in paths})

    def test_apply_blocks_changed_source_without_creating_targets(self) -> None:
        config_path = self.add_stock([])
        plan = self.plan()
        value = json.loads(config_path.read_text(encoding="utf-8"))
        value["memo"] = "changed"
        self.write_json(config_path, value)
        with self.assertRaisesRegex(RuntimeError, "source hash changed"):
            apply_legacy_performance_migration(plan, self.root)
        self.assertFalse((self.root / "assignment_episodes").exists())
        self.assertFalse((self.root / "migration_manifests").exists())

    def test_apply_blocks_unexpected_existing_target_without_overwrite(self) -> None:
        self.add_stock([])
        plan = self.plan()
        target = self.root / "assignment_episodes" / CODE / "episodes.json"
        self.write_json(target, {"unexpected": True})
        before = target.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "unexpected canonical migration target"):
            apply_legacy_performance_migration(plan, self.root)
        self.assertEqual(before, target.read_bytes())
        self.assertFalse((self.root / "migration_manifests").exists())

    def test_apply_promotion_failure_rolls_back_only_new_targets(self) -> None:
        self.add_stock([])
        self.write_runtime([self.realization()])
        plan = self.plan()
        calls = 0

        def fail_second(source, destination) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected promote failure")
            os.replace(source, destination)

        with self.assertRaisesRegex(OSError, "injected promote failure"):
            apply_legacy_performance_migration(plan, self.root, promote_replace=fail_second)
        for path in planned_phase_8b_apply_paths(plan):
            self.assertFalse((self.root / path).exists(), path)


if __name__ == "__main__":
    unittest.main()
