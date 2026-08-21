# -*- coding: utf-8 -*-

import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import group_recovery_repository as recovery
import gui_routine_registry as registry
from gui_routine_registry import consume_group_recovery_messages, scan_group_records


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_group(root: Path, name: str = "지표추종매매") -> Path:
    group = root / f"_{name}"
    _write_json(group / "budget.json", {"total_budget": 1_000_000})
    _write_json(group / "nested" / "state.json", {"value": 1})
    return group


class GroupRecoveryRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        consume_group_recovery_messages()

    def tearDown(self) -> None:
        consume_group_recovery_messages()

    def test_discovery_creates_complete_group_recovery_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = _write_group(root)

            records = scan_group_records(project_root=root)

            saved = root / "group_recovery" / group.name
            manifest = json.loads((saved / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual("1.0", manifest["schema_version"])
            self.assertEqual(str(group.resolve()), manifest["group_id"])
            self.assertEqual(group.name, manifest["group_folder_name"])
            self.assertEqual("지표추종매매", manifest["display_name"])
            self.assertEqual(str(group.resolve()), manifest["source_path"])
            self.assertTrue(manifest["snapshot_at"])
            self.assertEqual({"total_budget": 1_000_000}, json.loads(
                (saved / "snapshot" / "budget.json").read_text(encoding="utf-8")
            ))
            self.assertTrue((saved / "snapshot" / "nested" / "state.json").is_file())
            self.assertEqual([group], [record.path for record in records])

    def test_unchanged_rescan_does_not_rewrite_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = _write_group(root)
            scan_group_records(project_root=root)
            manifest_path = root / "group_recovery" / group.name / "manifest.json"
            first_manifest = manifest_path.read_bytes()
            first_mtime = manifest_path.stat().st_mtime_ns

            with patch.object(recovery, "_write_manifest") as write_manifest:
                scan_group_records(project_root=root)

            write_manifest.assert_not_called()
            self.assertEqual(first_manifest, manifest_path.read_bytes())
            self.assertEqual(first_mtime, manifest_path.stat().st_mtime_ns)

    def test_changed_group_replaces_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = _write_group(root)
            scan_group_records(project_root=root)
            saved = root / "group_recovery" / group.name
            first_manifest = json.loads((saved / "manifest.json").read_text(encoding="utf-8"))
            _write_json(group / "nested" / "state.json", {"value": 2})

            scan_group_records(project_root=root)

            second_manifest = json.loads((saved / "manifest.json").read_text(encoding="utf-8"))
            snapshot_state = json.loads(
                (saved / "snapshot" / "nested" / "state.json").read_text(encoding="utf-8")
            )
            self.assertNotEqual(first_manifest["content_digest"], second_manifest["content_digest"])
            self.assertEqual({"value": 2}, snapshot_state)

    def test_failed_replacement_preserves_previous_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = _write_group(root)
            record = scan_group_records(project_root=root)[0]
            saved = root / "group_recovery" / group.name
            previous_manifest = (saved / "manifest.json").read_bytes()
            previous_snapshot = (saved / "snapshot" / "nested" / "state.json").read_bytes()
            _write_json(group / "nested" / "state.json", {"value": 2})
            real_replace = os.replace

            def fail_staged_promotion(source, destination):
                source_path = Path(source)
                destination_path = Path(destination)
                if source_path.name.endswith(".tmp") and destination_path == saved:
                    raise OSError("simulated promotion failure")
                return real_replace(source, destination)

            with patch.object(recovery.os, "replace", side_effect=fail_staged_promotion):
                result = recovery.sync_group_recovery_snapshot(root, record)

            self.assertFalse(result.success)
            self.assertEqual(previous_manifest, (saved / "manifest.json").read_bytes())
            self.assertEqual(
                previous_snapshot,
                (saved / "snapshot" / "nested" / "state.json").read_bytes(),
            )

    def test_recovery_folder_is_not_discovered_as_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = _write_group(root)

            first = scan_group_records(project_root=root)
            second = scan_group_records(project_root=root)

            self.assertEqual([group], [record.path for record in first])
            self.assertEqual([group], [record.path for record in second])

    def test_high_frequency_scan_can_skip_recovery_sync(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = _write_group(root)

            records = scan_group_records(project_root=root, sync_recovery=False)

            self.assertEqual([group], [record.path for record in records])
            self.assertFalse((root / "group_recovery").exists())

    def test_discovery_does_not_modify_routine_instance_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = _write_group(root)
            instance_path = root / "routine_instances" / "instance-a" / "instance.json"
            _write_json(
                instance_path,
                {"instance_id": "instance-a", "group_id": str(group.resolve())},
            )
            before = instance_path.read_bytes()

            scan_group_records(project_root=root)

            self.assertEqual(before, instance_path.read_bytes())

    def test_missing_group_is_restored_and_discovered_with_one_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = _write_group(root)
            scan_group_records(project_root=root)
            expected_budget = (group / "budget.json").read_bytes()
            expected_nested = (group / "nested" / "state.json").read_bytes()
            shutil.rmtree(group)

            records = scan_group_records(project_root=root)

            self.assertEqual([group], [record.path for record in records])
            self.assertEqual(expected_budget, (group / "budget.json").read_bytes())
            self.assertEqual(expected_nested, (group / "nested" / "state.json").read_bytes())
            self.assertEqual(
                ("지표추종매매 그룹을 복구하였습니다.",),
                consume_group_recovery_messages(),
            )
            self.assertTrue((root / "group_recovery" / group.name).is_dir())

            self.assertEqual([group], [record.path for record in scan_group_records(project_root=root)])
            self.assertEqual((), consume_group_recovery_messages())

    def test_restore_does_not_modify_instance_or_stock_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = _write_group(root)
            instance_path = root / "routine_instances" / "instance-a" / "instance.json"
            stock_path = root / "stocks" / "000001_Test" / "config.json"
            _write_json(instance_path, {"instance_id": "instance-a", "group_id": str(group.resolve())})
            _write_json(
                stock_path,
                {
                    "routines": ["지표추종매매"],
                    "assigned_routine_instance_id": "instance-a",
                },
            )
            scan_group_records(project_root=root)
            instance_before = instance_path.read_bytes()
            stock_before = stock_path.read_bytes()
            shutil.rmtree(group)

            scan_group_records(project_root=root)

            self.assertEqual(instance_before, instance_path.read_bytes())
            self.assertEqual(stock_before, stock_path.read_bytes())

    def test_corrupt_snapshot_is_not_restored(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = _write_group(root)
            scan_group_records(project_root=root)
            snapshot_budget = root / "group_recovery" / group.name / "snapshot" / "budget.json"
            snapshot_budget.write_text("{}", encoding="utf-8")
            shutil.rmtree(group)

            with self.assertLogs(registry.LOGGER, level="WARNING"):
                records = scan_group_records(project_root=root)

            self.assertEqual([], records)
            self.assertFalse(group.exists())
            self.assertEqual((), consume_group_recovery_messages())
            self.assertEqual(b"{}", snapshot_budget.read_bytes())

    def test_manifest_target_mismatch_is_not_restored(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = _write_group(root)
            scan_group_records(project_root=root)
            manifest_path = root / "group_recovery" / group.name / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["source_path"] = str((root.parent / group.name).resolve())
            _write_json(manifest_path, manifest)
            shutil.rmtree(group)

            with self.assertLogs(registry.LOGGER, level="WARNING"):
                records = scan_group_records(project_root=root)

            self.assertEqual([], records)
            self.assertFalse(group.exists())
            self.assertEqual((), consume_group_recovery_messages())

    def test_restore_promotion_failure_leaves_no_partial_group_and_can_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = _write_group(root)
            scan_group_records(project_root=root)
            recovery_path = root / "group_recovery" / group.name
            shutil.rmtree(group)
            real_replace = os.replace

            def fail_group_promotion(source, destination):
                if Path(destination) == group:
                    raise OSError("simulated restore promotion failure")
                return real_replace(source, destination)

            with (
                patch.object(recovery.os, "replace", side_effect=fail_group_promotion),
                self.assertLogs(registry.LOGGER, level="WARNING"),
            ):
                records = scan_group_records(project_root=root)

            self.assertEqual([], records)
            self.assertFalse(group.exists())
            self.assertTrue(recovery_path.is_dir())
            self.assertEqual([], list(root.glob(".*.restore.tmp")))
            self.assertEqual((), consume_group_recovery_messages())
            controls = recovery.list_group_recovery_controls(root)
            self.assertEqual(1, len(controls))
            self.assertEqual("지표추종매매", controls[0].display_name)
            self.assertEqual(str(group.resolve()), controls[0].group_id)
            self.assertFalse(controls[0].deletion_pending)

            retry_records = scan_group_records(project_root=root)
            self.assertEqual([group], [record.path for record in retry_records])
            self.assertEqual(
                ("지표추종매매 그룹을 복구하였습니다.",),
                consume_group_recovery_messages(),
            )

    def test_manual_restore_uses_validated_single_group_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = _write_group(root)
            scan_group_records(project_root=root)
            expected_budget = (group / "budget.json").read_bytes()
            shutil.rmtree(group)

            result = recovery.restore_group_snapshot(root, str(group.resolve()))

            self.assertTrue(result.success)
            self.assertTrue(result.restored)
            self.assertEqual(group, result.target_path)
            self.assertEqual(expected_budget, (group / "budget.json").read_bytes())
            self.assertEqual((), recovery.list_group_recovery_controls(root))

    def test_manual_restore_failure_leaves_no_partial_group_and_keeps_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = _write_group(root)
            scan_group_records(project_root=root)
            recovery_path = root / "group_recovery" / group.name
            shutil.rmtree(group)
            real_replace = os.replace

            def fail_group_promotion(source, destination):
                if Path(destination) == group:
                    raise OSError("manual restore failed")
                return real_replace(source, destination)

            with patch.object(recovery.os, "replace", side_effect=fail_group_promotion):
                result = recovery.restore_group_snapshot(root, str(group.resolve()))

            self.assertFalse(result.success)
            self.assertFalse(group.exists())
            self.assertTrue(recovery_path.is_dir())
            self.assertEqual([], list(root.glob(".*.restore.tmp")))
            self.assertEqual(1, len(recovery.list_group_recovery_controls(root)))

    def test_deletion_pending_control_cannot_be_manually_restored(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = _write_group(root)
            scan_group_records(project_root=root)
            self.assertTrue(
                recovery.mark_group_deletion_pending(root, str(group.resolve())).success
            )
            shutil.rmtree(group)

            controls = recovery.list_group_recovery_controls(root)
            result = recovery.restore_group_snapshot(root, str(group.resolve()))

            self.assertEqual(1, len(controls))
            self.assertTrue(controls[0].deletion_pending)
            self.assertFalse(result.success)
            self.assertIn("deletion", result.error.lower())
            self.assertFalse(group.exists())

    def test_failed_post_promotion_verification_removes_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = _write_group(root)
            record = scan_group_records(project_root=root)[0]
            recovery_path = root / "group_recovery" / group.name
            shutil.rmtree(group)
            real_digest = recovery._tree_digest

            def fail_target_verification(path: Path) -> str:
                if Path(path) == group and group.exists():
                    return "invalid-after-promotion"
                return real_digest(Path(path))

            with patch.object(recovery, "_tree_digest", side_effect=fail_target_verification):
                results = recovery.restore_missing_group_snapshots(root)

            self.assertEqual(1, len(results))
            self.assertFalse(results[0].success)
            self.assertFalse(group.exists())
            self.assertTrue(recovery_path.is_dir())
            self.assertEqual([], list(root.glob(".*.restore.tmp")))

    def test_main_refresh_shows_pending_restore_toast_once(self) -> None:
        import gui_windows

        owner = SimpleNamespace(
            _install_routine_buy_limit_edit_filters=MagicMock(),
        )
        registry._PENDING_GROUP_RECOVERY_MESSAGES.append(
            "지표추종매매 그룹을 복구하였습니다."
        )
        with (
            patch.object(gui_windows, "main_load_routine_table"),
            patch.object(gui_windows, "show_toast") as show_toast,
        ):
            gui_windows.MainWindow.load_routine_table(owner)
            gui_windows.MainWindow.load_routine_table(owner)

        show_toast.assert_called_once_with(
            owner,
            "지표추종매매 그룹을 복구하였습니다.",
        )

    def test_deletion_marker_suppresses_backup_and_automatic_restore(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = _write_group(root)
            scan_group_records(project_root=root)
            marked = recovery.mark_group_deletion_pending(root, str(group.resolve()))
            self.assertTrue(marked.success)
            manifest_path = root / "group_recovery" / group.name / "manifest.json"
            marked_manifest = manifest_path.read_bytes()

            _write_json(group / "budget.json", {"total_budget": 2_000_000})
            scan_group_records(project_root=root)
            self.assertEqual(marked_manifest, manifest_path.read_bytes())
            shutil.rmtree(group)

            records = scan_group_records(project_root=root)

            self.assertEqual([], records)
            self.assertFalse(group.exists())
            self.assertEqual((), consume_group_recovery_messages())
            persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertIs(True, persisted["deletion_pending"])

    def test_pending_recovery_can_only_be_removed_with_matching_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = _write_group(root)
            scan_group_records(project_root=root)
            recovery_path = root / "group_recovery" / group.name

            unmarked = recovery.remove_pending_group_recovery(root, str(group.resolve()))
            self.assertFalse(unmarked.success)
            self.assertTrue(recovery_path.exists())

            self.assertTrue(
                recovery.mark_group_deletion_pending(root, str(group.resolve())).success
            )
            removed = recovery.remove_pending_group_recovery(root, str(group.resolve()))

            self.assertTrue(removed.success)
            self.assertFalse(recovery_path.exists())

    def test_deletion_marker_can_be_cleared_after_verified_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = _write_group(root)
            scan_group_records(project_root=root)
            self.assertTrue(
                recovery.mark_group_deletion_pending(root, str(group.resolve())).success
            )

            cleared = recovery.clear_group_deletion_pending(root, str(group.resolve()))

            self.assertTrue(cleared.success)
            manifest = json.loads(
                (
                    root / "group_recovery" / group.name / "manifest.json"
                ).read_text(encoding="utf-8")
            )
            self.assertNotIn("deletion_pending", manifest)
            self.assertNotIn("deletion_requested_at", manifest)


if __name__ == "__main__":
    unittest.main()
