# -*- coding: utf-8 -*-

import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import group_complete_deletion_service as deletion
from gui_routine_registry import scan_group_records


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_group(root: Path, name: str) -> Path:
    group = root / f"_{name}"
    _write_json(group / "budget.json", {"group": name})
    return group


def _write_instance(root: Path, instance_id: str, group_id: str) -> SimpleNamespace:
    instance_dir = root / "routine_instances" / instance_id
    _write_json(
        instance_dir / "instance.json",
        {"instance_id": instance_id, "group_id": group_id},
    )
    _write_json(instance_dir / "rules.json", {"instance": instance_id})
    return SimpleNamespace(instance_id=instance_id, group_id=group_id)


def _write_stock(
    root: Path,
    code: str,
    name: str,
    routine: str,
    instance_id: str,
) -> Path:
    stock_dir = root / "stocks" / f"{code}_{name}"
    _write_json(
        stock_dir / "config.json",
        {
            "routine": routine,
            "routine_name": routine,
            "assigned_routine": routine,
            "active_routine": routine,
            "routines": [routine],
            "assigned_routine_instance_id": instance_id,
            "routine_instance_name": routine,
            "routine_definition_id": "definition-a",
            "routine_type": routine,
            "sentinel": f"keep-{code}",
        },
    )
    _write_json(stock_dir / "state.json", {"holding_qty": 0, "status": "STOPPED"})
    _write_json(stock_dir / "orders.json", [])
    return stock_dir


class GroupCompleteDeletionServiceTests(unittest.TestCase):
    def test_complete_deletion_accepts_missing_group_recovery_control(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = _write_group(root, "지표추종매매")
            scan_group_records(project_root=root)
            instance = _write_instance(root, "instance-a", str(group.resolve()))
            stock = _write_stock(
                root, "000001", "대상종목", "지표추종매매", "instance-a"
            )
            shutil.rmtree(group)

            with (
                patch.object(
                    deletion,
                    "load_persisted_routine_instances",
                    return_value=[instance],
                ),
                patch("stock_repository._append_routine_changed"),
            ):
                scope = deletion.collect_group_deletion_scope(
                    root,
                    str(group.resolve()),
                )
                result = deletion.delete_group_completely(
                    root,
                    scope,
                    can_unassign=lambda _code, _name: (True, "지표추종매매", []),
                )

            self.assertTrue(result.success, result.error)
            self.assertFalse((root / "routine_instances" / "instance-a").exists())
            self.assertTrue(stock.exists())
            config = json.loads((stock / "config.json").read_text(encoding="utf-8"))
            self.assertEqual([], config["routines"])
            self.assertEqual("", config["assigned_routine_instance_id"])
            self.assertFalse((root / "group_recovery" / group.name).exists())

    def test_complete_deletion_removes_only_target_group_relationships(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target_group = _write_group(root, "지표추종매매")
            other_group = _write_group(root, "다른그룹")
            scan_group_records(project_root=root)
            target_instance = _write_instance(root, "instance-a", str(target_group.resolve()))
            other_instance = _write_instance(root, "instance-b", str(other_group.resolve()))
            legacy_instance = _write_instance(root, "instance-legacy", "")
            target_stock = _write_stock(
                root, "000001", "대상종목", "지표추종매매", "instance-a"
            )
            other_stock = _write_stock(
                root, "000002", "다른종목", "다른그룹", "instance-b"
            )
            instances = [target_instance, other_instance, legacy_instance]

            with (
                patch.object(deletion, "load_persisted_routine_instances", return_value=instances),
                patch("stock_repository._append_routine_changed"),
            ):
                scope = deletion.collect_group_deletion_scope(
                    root,
                    str(target_group.resolve()),
                )
                result = deletion.delete_group_completely(
                    root,
                    scope,
                    can_unassign=lambda _code, _name: (True, "지표추종매매", []),
                )

            self.assertTrue(result.success, result.error)
            self.assertFalse(target_group.exists())
            self.assertTrue(other_group.exists())
            self.assertFalse((root / "routine_instances" / "instance-a").exists())
            self.assertTrue((root / "routine_instances" / "instance-b").exists())
            self.assertTrue((root / "routine_instances" / "instance-legacy").exists())
            self.assertTrue(target_stock.exists())
            target_config = json.loads(
                (target_stock / "config.json").read_text(encoding="utf-8")
            )
            self.assertEqual([], target_config["routines"])
            self.assertEqual("", target_config["assigned_routine_instance_id"])
            self.assertEqual("keep-000001", target_config["sentinel"])
            self.assertEqual("keep-000002", json.loads(
                (other_stock / "config.json").read_text(encoding="utf-8")
            )["sentinel"])
            self.assertFalse(
                (root / "group_recovery" / target_group.name).exists()
            )
            self.assertTrue((root / "group_recovery" / other_group.name).exists())
            self.assertEqual(
                ["다른그룹"],
                [group.name for group in scan_group_records(project_root=root)],
            )

    def test_safety_block_changes_nothing_and_does_not_write_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = _write_group(root, "지표추종매매")
            scan_group_records(project_root=root)
            instance = _write_instance(root, "instance-a", str(group.resolve()))
            stock = _write_stock(root, "000001", "대상종목", "지표추종매매", "instance-a")
            group_before = (group / "budget.json").read_bytes()
            config_before = (stock / "config.json").read_bytes()

            with patch.object(
                deletion,
                "load_persisted_routine_instances",
                return_value=[instance],
            ):
                scope = deletion.collect_group_deletion_scope(root, str(group.resolve()))
                result = deletion.delete_group_completely(
                    root,
                    scope,
                    can_unassign=lambda _code, _name: (False, "지표추종매매", ["보유 1"]),
                )

            self.assertFalse(result.success)
            self.assertEqual(("대상종목: 보유 1",), result.blocked_reasons)
            self.assertEqual(group_before, (group / "budget.json").read_bytes())
            self.assertEqual(config_before, (stock / "config.json").read_bytes())
            manifest = json.loads(
                (root / "group_recovery" / group.name / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertNotIn("deletion_pending", manifest)

    def test_running_stock_blocks_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = _write_group(root, "지표추종매매")
            scan_group_records(project_root=root)
            instance = _write_instance(root, "instance-a", str(group.resolve()))
            stock = _write_stock(root, "000001", "대상종목", "지표추종매매", "instance-a")
            with patch.object(
                deletion,
                "load_persisted_routine_instances",
                return_value=[instance],
            ):
                scope = deletion.collect_group_deletion_scope(root, str(group.resolve()))
                result = deletion.delete_group_completely(
                    root,
                    scope,
                    can_unassign=lambda _code, _name: (True, "지표추종매매", []),
                    running_stock_dirs=[stock],
                )
            self.assertFalse(result.success)
            self.assertEqual(("대상종목: 운영 중",), result.blocked_reasons)
            self.assertTrue(group.exists())

    def test_failure_with_complete_rollback_clears_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = _write_group(root, "지표추종매매")
            scan_group_records(project_root=root)
            instance = _write_instance(root, "instance-a", str(group.resolve()))
            stock = _write_stock(root, "000001", "대상종목", "지표추종매매", "instance-a")
            config_before = (stock / "config.json").read_bytes()

            with (
                patch.object(deletion, "load_persisted_routine_instances", return_value=[instance]),
                patch.object(
                    deletion.StockRepository,
                    "update_stock_routine",
                    return_value=False,
                ),
            ):
                scope = deletion.collect_group_deletion_scope(root, str(group.resolve()))
                result = deletion.delete_group_completely(
                    root,
                    scope,
                    can_unassign=lambda _code, _name: (True, "지표추종매매", []),
                )

            self.assertFalse(result.success)
            self.assertTrue(group.exists())
            self.assertTrue((root / "routine_instances" / "instance-a").exists())
            self.assertEqual(config_before, (stock / "config.json").read_bytes())
            manifest_path = root / "group_recovery" / group.name / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertNotIn("deletion_pending", manifest)

            shutil.rmtree(group)
            self.assertEqual(1, len(scan_group_records(project_root=root)))
            self.assertTrue(group.exists())

    def test_failure_with_incomplete_rollback_keeps_marker_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = _write_group(root, "지표추종매매")
            scan_group_records(project_root=root)
            instance = _write_instance(root, "instance-a", str(group.resolve()))
            stock = _write_stock(root, "000001", "대상종목", "지표추종매매", "instance-a")
            config_path = stock / "config.json"

            def corrupt_then_fail(_repository, _code, _name, _routines):
                config_path.write_text("{}\n", encoding="utf-8")
                return False

            with (
                patch.object(deletion, "load_persisted_routine_instances", return_value=[instance]),
                patch.object(
                    deletion.StockRepository,
                    "update_stock_routine",
                    new=corrupt_then_fail,
                ),
                patch.object(deletion, "_restore_file", side_effect=OSError("rollback failed")),
            ):
                scope = deletion.collect_group_deletion_scope(root, str(group.resolve()))
                result = deletion.delete_group_completely(
                    root,
                    scope,
                    can_unassign=lambda _code, _name: (True, "지표추종매매", []),
                )

            self.assertFalse(result.success)
            self.assertIn("rollback", result.error)
            manifest_path = root / "group_recovery" / group.name / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertIs(True, manifest["deletion_pending"])

            shutil.rmtree(group)
            self.assertEqual([], scan_group_records(project_root=root))
            self.assertFalse(group.exists())


if __name__ == "__main__":
    unittest.main()
